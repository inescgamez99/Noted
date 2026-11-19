"""Vigilancia del microfono: el stream puede morir a mitad de reunion.

INCIDENTE: con auriculares Bluetooth, Windows cambia de perfil y de dispositivo
por defecto al entrar en la llamada. El InputStream abierto al empezar se
quedaba mudo sin lanzar error y la grabacion salia sin la voz de quien graba.

Usan un sounddevice falso: no tocan el hardware, corren en CI.
"""
import queue
import time

import numpy as np
import pytest

import audio_recorder as ar
from audio_recorder import AudioRecorder


class FakeStream:
    instances: list = []
    fail_open = 0  # cuantas aperturas fallan antes de funcionar
    stop_delay = 0.0  # lo que tarda en cerrarse: el cierre real puede bloquearse

    def __init__(self, callback=None, device=None, **_kw):
        if FakeStream.fail_open > 0:
            FakeStream.fail_open -= 1
            raise OSError('Invalid device')
        self.callback = callback
        self.device = device
        self.closed = False
        FakeStream.instances.append(self)

    def start(self):
        pass

    def stop(self):
        if FakeStream.stop_delay:
            time.sleep(FakeStream.stop_delay)

    def close(self):
        self.closed = True

    def feed(self, value=0.1, n=1600):
        self.callback(np.full((n, 1), value, dtype=np.float32), n, None, None)


@pytest.fixture
def fake_sd(monkeypatch):
    FakeStream.instances = []
    FakeStream.fail_open = 0
    FakeStream.stop_delay = 0.0
    monkeypatch.setattr(ar.sd, 'InputStream', FakeStream)
    monkeypatch.setattr(ar.sd, 'query_devices', lambda *a, **k: {'name': 'fake'})
    monkeypatch.setattr(AudioRecorder, '_mic_candidates', staticmethod(lambda: [None]))
    monkeypatch.setattr(AudioRecorder, '_refresh_portaudio', lambda self: None)
    return FakeStream


@pytest.fixture
def rec(fake_sd, monkeypatch):
    monkeypatch.setattr(ar, '_MIC_RETRY_WAIT', 0.0)
    r = AudioRecorder()
    r._recording = True
    r._mic_q = queue.Queue()
    r._mic_t0 = time.monotonic()
    yield r
    r._recording = False
    r._mic_watch_stop.set()


def _drain(q):
    total = 0
    while not q.empty():
        b = q.get()
        total += 0 if b is None else len(b)
    return total


def test_abre_el_microfono_y_cuenta_las_muestras(rec, fake_sd):
    assert rec._open_mic_stream()
    fake_sd.instances[0].feed(n=1600)
    assert rec._mic_samples == 1600


def test_si_el_dispositivo_falla_no_revienta_y_guarda_el_motivo(rec, fake_sd):
    fake_sd.fail_open = 5
    assert not rec._open_mic_stream()
    assert 'Invalid device' in rec._mic_error
    assert rec._stream is None


def test_reabrir_cierra_el_stream_muerto_y_abre_uno_nuevo(rec, fake_sd):
    rec._open_mic_stream()
    viejo = rec._stream
    assert rec._reopen_mic()
    assert viejo.closed
    assert rec._stream is not viejo and not rec._stream.closed


def test_el_hueco_de_la_caida_se_rellena_con_silencio(rec, fake_sd):
    rec._open_mic_stream()
    rec._mic_t0 = time.monotonic() - 5.0   # 5 s de reunion y ninguna muestra
    rec._reopen_mic()
    assert _drain(rec._mic_q) == pytest.approx(5 * ar.SAMPLE_RATE, rel=0.05)


def test_watchdog_reabre_si_dejan_de_llegar_datos(rec, fake_sd, monkeypatch):
    monkeypatch.setattr(ar, '_MIC_STALL_SECONDS', 0.2)
    FakeStream.stop_delay = 0.4
    rec._open_mic_stream()
    viejo = rec._stream
    rec._mic_last_cb = time.monotonic() - 10     # el callback lleva 10 s mudo
    import threading
    stop = threading.Event()
    t = threading.Thread(target=rec._mic_watchdog, args=(stop,), daemon=True)
    t.start()
    t0 = time.time()
    # Esperar a que el reabrir TERMINE, no solo a que _stream deje de ser el viejo:
    # _reopen_mic pone _stream = None, cierra el viejo en otro hilo y luego abre el
    # nuevo. Salir en el None intermedio comprobaba viejo.closed antes de que ese hilo
    # acabase (fallo intermitente en la integracion continua del 19/11/2026).
    while time.time() - t0 < 10:
        if rec._stream is not None and rec._stream is not viejo and viejo.closed:
            break
        time.sleep(0.05)
    stop.set()
    assert rec._stream is not None and rec._stream is not viejo and viejo.closed


def test_watchdog_reabre_si_solo_hay_silencio_digital(rec, fake_sd, monkeypatch):
    monkeypatch.setattr(ar, '_MIC_SILENT_SECONDS', 0.2)
    rec._open_mic_stream()
    viejo = rec._stream
    rec._mic_last_loud = time.monotonic() - 10
    import threading
    stop = threading.Event()
    t = threading.Thread(target=rec._mic_watchdog, args=(stop,), daemon=True)
    t.start()
    t0 = time.time()
    while (rec._stream is None or rec._stream is viejo) and time.time() - t0 < 10:
        viejo.feed(value=0.0)                     # sigue llegando, pero todo ceros
        time.sleep(0.05)
    stop.set()
    assert rec._stream is not None and rec._stream is not viejo


def test_un_micro_muerto_con_ruido_de_1_lsb_cuenta_como_silencio(rec, fake_sd):
    """Caso real del 28/10: el dispositivo entregaba ±1 LSB, no ceros."""
    rec._open_mic_stream()
    antes = rec._mic_last_loud
    time.sleep(0.01)
    fake_sd.instances[0].feed(value=3.05e-5)
    assert rec._mic_last_loud == antes
    fake_sd.instances[0].feed(value=0.01)           # voz real
    assert rec._mic_last_loud > antes


def test_watchdog_avisa_si_no_hay_microfono_y_se_recupera(rec, fake_sd, monkeypatch):
    monkeypatch.setattr(ar, '_MIC_WARN_AFTER', 0.0)
    avisos = []
    rec.on_mic_unavailable = avisos.append
    fake_sd.fail_open = 3                          # fallan 3 aperturas seguidas
    import threading
    stop = threading.Event()
    t = threading.Thread(target=rec._mic_watchdog, args=(stop,), daemon=True)
    t.start()
    t0 = time.time()
    while rec._stream is None and time.time() - t0 < 10:
        time.sleep(0.1)
    stop.set()
    assert rec._stream is not None                 # acabo recuperandose
    assert len(avisos) == 1                        # y avisó una sola vez


def test_si_el_micro_nunca_abrio_stop_deja_silencio_en_vez_de_vacio(rec, fake_sd):
    """Con 0 muestras, _process_and_save tiraba la grabacion entera."""
    rec._mic_t0 = time.monotonic() - 3.0
    rec._mic_pad_to_now()
    assert _drain(rec._mic_q) == pytest.approx(3 * ar.SAMPLE_RATE, rel=0.05)


def test_reabrir_tras_parar_no_deja_un_stream_huerfano(rec, fake_sd):
    rec._open_mic_stream()
    rec._recording = False
    assert not rec._reopen_mic()
    assert len(fake_sd.instances) == 1
