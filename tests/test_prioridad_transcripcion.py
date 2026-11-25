"""Prioridad del daemon al transcribir una reunion ya terminada (25/11/2026).

Con el equipo cargado (agentes de seguridad, otras sesiones, tests), en Normal la
transcripcion tras colgar no conseguia CPU: 0,6 nucleos y la reunion atascada en
el 94 % el 23/11; con AboveNormal, 4,5 nucleos en 8 s. Subirla a mano se perdia
en cada reinicio.

LO QUE ESTOS TESTS PROTEGEN
1. Transcribiendo tras colgar, AboveNormal; al acabar (tambien por un fallo), la
   de antes.
2. Las reuniones seguidas: si empieza a grabarse otra mientras se transcribe la
   anterior, vuelve a Normal en el acto (Teams tiene que ir fluido) y sube otra
   vez al colgar si la transcripcion sigue.
3. Un proceso que nunca la subio no la toca (grabar no la cambia), un fallo de
   psutil no rompe nada, y fuera de Windows no se hace nada.
4. Lo llaman el pipeline del daemon (el paso de transcribir) y el grabador (al
   empezar y al terminar).
"""
import pytest

import win_power as wp

pytestmark = pytest.mark.unit

psutil = pytest.importorskip('psutil')
ABOVE = getattr(psutil, 'ABOVE_NORMAL_PRIORITY_CLASS', 'above')
NORMAL = getattr(psutil, 'NORMAL_PRIORITY_CLASS', 'normal')
BELOW = getattr(psutil, 'BELOW_NORMAL_PRIORITY_CLASS', 'below')


class FakeProc:
    def __init__(self, start=NORMAL, fails=False):
        self.value, self.calls, self.fails = start, [], fails

    def nice(self, value=None):
        if self.fails:
            raise PermissionError('acceso denegado')
        if value is None:
            return self.value
        self.calls.append(value)
        self.value = value


@pytest.fixture
def proc(monkeypatch):
    p = FakeProc()
    monkeypatch.setattr(psutil, 'Process', lambda *_a: p)
    monkeypatch.setattr(psutil, 'ABOVE_NORMAL_PRIORITY_CLASS', ABOVE, raising=False)
    monkeypatch.setattr(wp, '_WINDOWS', True)
    monkeypatch.setattr(wp, '_transcribing', 0)
    monkeypatch.setattr(wp, '_recording', False)
    monkeypatch.setattr(wp, '_in_call', False)
    monkeypatch.setattr(wp, '_previous', None)
    return p


# ── 1. Transcribiendo tras colgar ────────────────────────────────────────────

def test_transcribiendo_sube_y_al_acabar_vuelve(proc):
    with wp.transcribing():
        assert proc.value == ABOVE
    assert proc.value == NORMAL and proc.calls == [ABOVE, NORMAL]


def test_un_fallo_tambien_la_devuelve(proc):
    with pytest.raises(RuntimeError):
        with wp.transcribing():
            raise RuntimeError('whisper revento')
    assert proc.value == NORMAL


def test_vuelve_a_la_de_antes_no_a_normal(proc):
    proc.value = BELOW
    with wp.transcribing():
        assert proc.value == ABOVE
    assert proc.value == BELOW


def test_dos_a_la_vez_no_baja_hasta_que_acaban_las_dos(proc):
    with wp.transcribing():
        with wp.transcribing():
            pass
        assert proc.value == ABOVE
    assert proc.value == NORMAL and proc.calls == [ABOVE, NORMAL]


# ── 2. Reuniones seguidas ────────────────────────────────────────────────────

def test_si_empieza_otra_reunion_vuelve_a_normal_y_sube_al_colgar(proc):
    with wp.transcribing():
        assert proc.value == ABOVE
        wp.set_recording(True)
        assert proc.value == NORMAL, "8 hilos de Whisper por encima de Teams estropean la llamada"
        wp.set_recording(False)
        assert proc.value == ABOVE
    assert proc.value == NORMAL


def test_en_una_llamada_tampoco_aunque_no_se_grabe(proc):
    """La grabacion empieza tras el aviso (hasta 30 s), o nunca si se dice que no:
    en ese rato Teams prepara el audio y el video."""
    with wp.transcribing():
        wp.set_in_call(True)
        assert proc.value == NORMAL
        wp.set_in_call(False)
        assert proc.value == ABOVE
    assert proc.value == NORMAL


def test_grabando_no_sube_aunque_empiece_a_transcribir(proc):
    wp.set_recording(True)
    with wp.transcribing():
        assert proc.value == NORMAL
    wp.set_recording(False)
    assert proc.calls == []


# ── 3. Sin efectos donde no toca ─────────────────────────────────────────────

def test_grabar_sin_transcribir_no_toca_la_prioridad(proc):
    """Los tests corren en BelowNormal (ci_guarded): grabar no debe dejarlos en Normal."""
    wp.set_recording(True)
    wp.set_recording(False)
    assert proc.calls == []


def test_un_fallo_de_psutil_no_rompe_nada(proc, monkeypatch, caplog):
    fallo = FakeProc(fails=True)
    monkeypatch.setattr(psutil, 'Process', lambda *_a: fallo)
    with wp.transcribing():
        wp.set_recording(True)
    assert 'No se pudo cambiar la prioridad' in caplog.text


def test_fuera_de_windows_no_hace_nada(proc, monkeypatch):
    monkeypatch.setattr(wp, '_WINDOWS', False)
    with wp.transcribing():
        pass
    assert proc.calls == []


# ── 4. Quien lo llama ────────────────────────────────────────────────────────

def test_el_grabador_avisa_al_empezar_y_al_terminar(tr_dirs, monkeypatch):
    import audio_recorder as ar
    avisos = []
    monkeypatch.setattr(wp, 'set_recording', avisos.append)
    ar._write_sentinel()
    ar._remove_sentinel()
    assert avisos == [True, False]


def test_el_detector_avisa_al_empezar_y_al_terminar_la_llamada(monkeypatch):
    import time

    import teams_detector as td
    avisos = []
    estado = {'pids': [1234], 'audio': True}
    monkeypatch.setattr(wp, 'set_in_call', avisos.append)
    monkeypatch.setattr(td, '_teams_pids', lambda: estado['pids'])
    monkeypatch.setattr(td, '_check_audio_session', lambda _p: estado['audio'])
    monkeypatch.setattr(td, '_check_mic_session', lambda _p: estado['audio'])
    monkeypatch.setattr(td, '_check_window_titles',
                        lambda _p: (False, True, 'Mi Reunion', ['Mi Reunion']) if estado['audio']
                        else (False, False, None, []))
    det = td.TeamsCallDetector(poll_interval=0.02, required_confirmations=2)
    det.on_call_started = det.on_call_ended = None

    def espera(cond, segundos=15):
        fin = time.monotonic() + segundos
        while not cond() and time.monotonic() < fin:
            time.sleep(0.02)

    det.start()
    try:
        espera(lambda: avisos == [True])
        assert avisos == [True]
        estado['audio'] = False                         # cuelga
        espera(lambda: avisos == [True, False])
        assert avisos == [True, False]
    finally:
        det.stop()


def test_el_final_del_transcript_de_fondo_tambien_va_dentro(tray, wav_factory, monkeypatch):
    """Con el transcript de fondo (lo normal), al colgar finish_live transcribe en el
    daemon lo que falta: es el caso que se midio atascado."""
    import contextlib

    import tray_app as ta
    estado, dentro = {'activo': False}, []

    @contextlib.contextmanager
    def espia():
        estado['activo'] = True
        try:
            yield
        finally:
            estado['activo'] = False

    def fake_live(_wav, transcript_path, lang_path):
        dentro.append(estado['activo'])
        transcript_path.write_text('[00:00] hola', encoding='utf-8')
        lang_path.write_text('es')

    wav = ta.RECORDINGS_DIR / '2026-11-25_11-48_Reunion.wav'
    wav_factory(wav, seconds=0.5)
    monkeypatch.setattr(wp, 'transcribing', espia)
    monkeypatch.setattr(tray, '_use_live_transcript', fake_live)
    monkeypatch.setattr(tray, '_claim_whisper', lambda: None)
    monkeypatch.setattr(tray, 'set_processing', lambda *_a: None)
    monkeypatch.setattr(tray, '_register_part', lambda *_a: None)
    tray._run_pipeline_sync(wav)
    assert dentro == [True] and not estado['activo']


def test_el_pipeline_transcribe_dentro_de_transcribing(tray, wav_factory, monkeypatch):
    import contextlib

    import transcriber
    import tray_app as ta
    dentro = []
    estado = {'activo': False}

    @contextlib.contextmanager
    def espia():
        estado['activo'] = True
        try:
            yield
        finally:
            estado['activo'] = False

    def fake_transcribe(*_a, **_k):
        dentro.append(estado['activo'])
        return '[00:00] hola', 'es'

    wav = ta.RECORDINGS_DIR / '2026-11-25_11-48_Reunion.wav'
    wav_factory(wav, seconds=0.5)
    monkeypatch.setattr(wp, 'transcribing', espia)
    monkeypatch.setattr(transcriber, 'transcribe', fake_transcribe)
    monkeypatch.setattr(tray, 'set_processing', lambda *_a: None)
    monkeypatch.setattr(tray, '_register_part', lambda *_a: None)
    tray._run_pipeline_sync(wav)
    assert dentro == [True] and not estado['activo']
