"""El loopback no puede dejar temporales huerfanos cuando falla.

INCIDENTE QUE ORIGINO ESTOS TESTS (22/09/2026)
Quedo un `.loop.part` de 824 KB con 12,9 s de ceros exactos en recordings/.
Lo dejo un reintento que corrio DESPUES de que la grabacion terminara:

    12:06:36,791  WAV guardado
    12:06:36,933  Grabacion detenida
    12:06:39,088  WASAPI loopback no arranco      <- 3 s despues de guardar

`_start_stereo_mix_loopback` abria el fichero ANTES de construir el
InputStream, y este equipo no tiene Stereo Mix: el constructor lanza
"Invalid device" y el `.part` se quedaba escrito. La limpieza (`_discard_temp`)
corre en el finally de la parada, que para entonces ya habia pasado.

Es la misma familia que el huerfano de 99 MB del 16/09, en otra rama del
codigo: alli el problema era el writer vivo, aqui el fichero que nadie borra.
"""
import time

import pytest
import sounddevice as sd

import audio_recorder as ar
from audio_recorder import AudioRecorder

pytestmark = pytest.mark.unit


@pytest.fixture
def grabador(tmp_path, monkeypatch):
    """Un AudioRecorder con temporales en tmp_path y sin hilos de captura."""
    rec = AudioRecorder()
    rec._tmp_loop = tmp_path / 'reunion.loop.part'
    rec._tmp_mic = tmp_path / 'reunion.mic.part'
    rec._recording = True
    # _mic_t0 SE FIJA a proposito. Con None no hay padding que escribir, el
    # hilo del writer se queda esperando y el fichero puede no existir todavia
    # cuando el test mira: el test pasaba con y sin el arreglo, por una
    # carrera y no por el codigo. En produccion siempre esta fijado, y son
    # justo esos ceros de compensacion los que formaron el huerfano de 824 KB.
    rec._mic_t0 = time.monotonic() - 12.9
    return rec


def esperar_al_fichero(ruta, segundos=2.0):
    """El writer escribe en su propio hilo: hay que darle tiempo antes de
    afirmar que el fichero NO esta."""
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        if ruta.exists():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def stereo_mix_presente(monkeypatch):
    """Windows dice que hay un Stereo Mix. Si lo abre o no, lo decide el test."""
    monkeypatch.setattr(sd, 'query_devices',
                        lambda: [{'name': 'Stereo Mix (Realtek)', 'max_input_channels': 2}])


def test_si_el_dispositivo_no_abre_no_queda_ningun_part(grabador, stereo_mix_presente,
                                                        monkeypatch):
    """EL TEST CLAVE. Es literalmente lo que pasa en esta maquina: el
    dispositivo aparece en la lista y el constructor lo rechaza."""
    def input_stream_roto(**_kw):
        raise sd.PortAudioError('Error opening InputStream: Invalid device [PaErrorCode -9996]')

    monkeypatch.setattr(sd, 'InputStream', input_stream_roto)

    assert grabador._start_stereo_mix_loopback() is False
    assert not esperar_al_fichero(grabador._tmp_loop), \
        f"quedo un huerfano de {grabador._tmp_loop.stat().st_size} bytes"


def test_si_el_dispositivo_no_abre_no_queda_el_writer_colgado(grabador, stereo_mix_presente,
                                                              monkeypatch):
    """Un writer vivo sobre un fichero abierto impide borrarlo en Windows."""
    monkeypatch.setattr(sd, 'InputStream',
                        lambda **_kw: (_ for _ in ()).throw(sd.PortAudioError('boom')))

    grabador._start_stereo_mix_loopback()

    assert grabador._loop_q is None
    assert grabador._loop_writer is None


def test_si_la_grabacion_ya_termino_no_se_abre_nada(grabador, stereo_mix_presente,
                                                    monkeypatch):
    """El caso del 22/09: el reintento llega despues de la parada. Aunque el
    dispositivo funcionase, abrir un fichero ahora es basura garantizada."""
    class StreamFalso:
        def __init__(self, **_kw): pass
        def start(self): raise AssertionError('no deberia arrancar tras la parada')
        def close(self): pass

    monkeypatch.setattr(sd, 'InputStream', StreamFalso)
    grabador._recording = False

    assert grabador._start_stereo_mix_loopback() is False
    assert not esperar_al_fichero(grabador._tmp_loop)


def test_si_todo_va_bien_SI_se_abre_el_writer(grabador, stereo_mix_presente, monkeypatch):
    """CONTROL: el camino bueno no se rompio. Sin esto, 'no dejar huerfanos'
    se cumple trivialmente no grabando nunca el audio del sistema."""
    arrancados = []

    class StreamOk:
        def __init__(self, **_kw): pass
        def start(self): arrancados.append(1)
        def close(self): pass

    monkeypatch.setattr(sd, 'InputStream', StreamOk)

    assert grabador._start_stereo_mix_loopback() is True
    assert arrancados == [1]
    assert grabador._loop_q is not None, "no se abrio el writer del loopback"
    grabador._close_stale_loop_writer()


def test_abortar_borra_el_temporal_aunque_tenga_datos(grabador):
    """`_abortar_loop_writer` cierra el hilo Y borra. Cerrar solo el hilo
    (lo que hacia `_close_stale_loop_writer`) dejaba el fichero en disco."""
    grabador._open_loop_writer()
    grabador._loop_q.put(ar.np.zeros(1000, dtype='float32'))

    grabador._abortar_loop_writer()

    assert not grabador._tmp_loop.exists()
    assert grabador._loop_writer is None


def test_abortar_no_revienta_si_no_hay_nada_que_borrar(grabador):
    """Se llama en el except de un fallo: no puede lanzar y tapar la causa."""
    grabador._abortar_loop_writer()
    grabador._abortar_loop_writer()
