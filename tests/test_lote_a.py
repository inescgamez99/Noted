"""Perdidas de texto y estados colgados del cierre del pipeline (lote A).

Cada test lleva el incidente que cubre. Ninguno toca el disco real: todo va a
tr_dirs (conftest) y TrayApp se construye sin hilos.
"""
import time

import pytest

import tray_app as ta

pytestmark = pytest.mark.unit

STEM = '2026-11-10_10-00_Comite_Semanal'


@pytest.fixture
def pipeline(tray, monkeypatch):
    """_run_pipeline_sync sin Whisper, sin modelo y sin ventanas."""
    monkeypatch.setattr(ta.TrayApp, '_defer_reason', lambda self: None)
    monkeypatch.setattr(ta.TrayApp, '_claim_whisper', lambda self: None)
    monkeypatch.setattr(ta.TrayApp, '_use_live_transcript', lambda self, *a, **k: None)
    monkeypatch.setattr(ta.TrayApp, '_notify', lambda self, t, m: None)
    monkeypatch.setattr(ta.TrayApp, 'set_processing', lambda self, m='': None)
    monkeypatch.setattr(ta.TrayApp, '_write_status', lambda self: None)
    monkeypatch.setattr(ta.TrayApp, '_discard_live_files', lambda self, w: None)
    return tray


def _con_transcribe(monkeypatch, resultado):
    import transcriber
    monkeypatch.setattr(transcriber, 'transcribe', lambda *a, **k: resultado)


# ── A1: el .partial solo se borra si hubo resultado ──────────────────────

def test_si_transcribe_falla_el_partial_se_conserva(pipeline, monkeypatch, wav_factory):
    """Un fallo de Whisper y del respaldo OpenAI (transcribe -> None) borraba el
    .partial ANTES de mirar el resultado: se perdia todo lo ya transcrito."""
    wav = wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)
    partial = ta.RECORDINGS_DIR / f'{STEM}.partial'
    partial.write_text('[00:00] hola', encoding='utf-8')
    _con_transcribe(monkeypatch, None)

    pipeline._run_pipeline_sync(wav)

    assert partial.exists() and partial.read_text(encoding='utf-8') == '[00:00] hola'


def test_con_resultado_el_partial_se_borra(pipeline, monkeypatch, wav_factory):
    wav = wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)
    partial = ta.RECORDINGS_DIR / f'{STEM}.partial'
    partial.write_text('[00:00] hola', encoding='utf-8')
    _con_transcribe(monkeypatch, ('hola', 'es'))

    pipeline._run_pipeline_sync(wav)

    assert not partial.exists()


# ── A6: _current_job no se queda colgado ─────────────────────────────────

def test_tras_fallar_la_transcripcion_el_pipeline_vuelve_a_estar_ocioso(pipeline, monkeypatch,
                                                                        wav_factory):
    """_current_job se quedaba con el stem: _pipeline_idle() era False para
    siempre (no se reintentaban minutas pendientes) y _active_job_stem()
    devolvia un trabajo fantasma."""
    wav = wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)
    pipeline._recorder = type('G', (), {'is_recording': False, 'is_saving': False})()
    _con_transcribe(monkeypatch, None)

    pipeline._run_pipeline_sync(wav)

    assert pipeline._current_job == {}
    assert pipeline._active_job_stem() == ''
    assert pipeline._pipeline_idle() is True


def test_tras_una_grabacion_sin_voz_el_pipeline_vuelve_a_estar_ocioso(pipeline, monkeypatch,
                                                                      wav_factory):
    wav = wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)
    pipeline._recorder = type('G', (), {'is_recording': False, 'is_saving': False})()
    _con_transcribe(monkeypatch, ('', 'es'))

    pipeline._run_pipeline_sync(wav)

    assert pipeline._current_job == {}
    assert pipeline._pipeline_idle() is True


def test_tras_una_excepcion_del_pipeline_el_trabajo_se_limpia(pipeline, monkeypatch, wav_factory):
    wav = wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)
    pipeline._current_job = {'stem': STEM}

    def revienta(self, w):
        raise RuntimeError('boom')

    monkeypatch.setattr(ta.TrayApp, '_run_pipeline_until_done', revienta)
    monkeypatch.setattr(ta.TrayApp, '_release_whisper', lambda self, force=False: None)
    pipeline._pipeline_queue.put(wav)
    pipeline._pipeline_queue.put(None)

    pipeline._pipeline_loop()

    assert pipeline._current_job == {}


# ── A2 y A3: el cierre no pierde el transcript ni el idioma ──────────────

@pytest.fixture
def cierre(pipeline, monkeypatch, tr_dirs):
    import html_exporter
    import minutes_generator
    import project_context
    monkeypatch.setattr(minutes_generator, 'generate_minutes',
                        lambda *a, **k: 'TITULO: Comite\n\n## Resumen\n\ngenerado')
    monkeypatch.setattr(html_exporter, 'export_to_html', lambda *a, **k: None)
    monkeypatch.setattr(project_context, 'prepare_context', lambda *a, **k: (None, None))
    import actions_enricher
    monkeypatch.setattr(actions_enricher, 'enrich_and_save', lambda *a, **k: None)
    monkeypatch.setattr(ta.TrayApp, '_wait_while_deferred', lambda self, w: None)
    return pipeline


def _sesion(wav, texto='el texto de la reunion'):
    return {'base_wav': wav, 'parts': [wav], 'transcripts': {wav.stem: texto},
            'lang': 'es', 'key': 'comite', 'awaiting': False, 'finalized': False,
            'timer': None, 'last_at': time.time()}


def test_si_falla_la_copia_del_transcript_el_original_se_conserva(cierre, monkeypatch,
                                                                  wav_factory):
    """Con el disco lleno o el antivirus bloqueando, la copia a minutes/ fallaba
    solo con un warning y _move_to_processed borraba el original: el texto de la
    reunion dejaba de existir."""
    monkeypatch.setattr(ta, '_write_text_atomic', lambda path, text: False)
    wav = wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)

    cierre._finalize_session(_sesion(wav))

    original = ta.RECORDINGS_DIR / f'{STEM}_transcript.txt'
    assert original.read_text(encoding='utf-8') == 'el texto de la reunion'


def test_si_la_copia_funciona_el_original_se_borra_como_siempre(cierre, wav_factory):
    wav = wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)

    cierre._finalize_session(_sesion(wav))

    assert not (ta.RECORDINGS_DIR / f'{STEM}_transcript.txt').exists()
    [copia] = ta.MINUTES_DIR.glob('*_transcript.txt')
    assert copia.read_text(encoding='utf-8') == 'el texto de la reunion'


def test_el_reintento_encuentra_el_transcript_que_se_quedo_en_recordings(cierre, monkeypatch,
                                                                         wav_factory):
    """Minutas 'pending' + copia fallida: el unico transcript esta en
    recordings/ y _retry_pending_minutes tiene que seguir encontrandolo."""
    import config
    import minutes_generator as mg
    wav = wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)
    (ta.RECORDINGS_DIR / f'{STEM}_transcript.txt').write_text('texto a salvo', encoding='utf-8')
    md = ta.MINUTES_DIR / '20261110_1000_Comite.md'
    _t, content = mg.extract_title_from_minutes(mg.brief_minutes('Comite', 'pending', stem=STEM))
    md.write_text(content, encoding='utf-8')
    cierre._recorder = type('G', (), {'is_recording': False, 'is_saving': False})()
    monkeypatch.setattr(config, 'minutes_mode', lambda: 'full')
    vistos = []
    monkeypatch.setattr(ta.TrayApp, '_finalize_session', lambda self, s: vistos.append(s))

    cierre._retry_pending_minutes()

    [sess] = vistos
    assert sess['transcripts'] == {STEM: 'texto a salvo'}
    del wav


def test_el_reintento_conserva_el_idioma_tras_mover_a_processed(cierre, monkeypatch,
                                                                wav_factory):
    """_move_to_processed se llevaba el idioma y el reintento caia a 'auto': las
    minutas reintentadas salian con cabeceras en ingles. (El fichero real es
    <stem>_transcript.lang, no <stem>.lang.)"""
    import config
    import minutes_generator as mg
    wav = wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)
    transcript = ta.RECORDINGS_DIR / f'{STEM}_transcript.txt'
    transcript.write_text('texto', encoding='utf-8')
    lang = ta.RECORDINGS_DIR / f'{STEM}_transcript.lang'
    lang.write_text('es')

    cierre._move_to_processed(wav, transcript)

    assert not lang.exists()
    assert (ta.RECORDINGS_DIR / 'processed' / lang.name).read_text() == 'es'

    md = ta.MINUTES_DIR / '20261110_1000_Comite.md'
    _t, content = mg.extract_title_from_minutes(mg.brief_minutes('Comite', 'pending', stem=STEM))
    md.write_text(content, encoding='utf-8')
    md.with_name(md.stem + '_transcript.txt').write_text('texto', encoding='utf-8')
    cierre._recorder = type('G', (), {'is_recording': False, 'is_saving': False})()
    monkeypatch.setattr(config, 'minutes_mode', lambda: 'full')
    vistos = []
    monkeypatch.setattr(ta.TrayApp, '_finalize_session', lambda self, s: vistos.append(s))

    cierre._retry_pending_minutes()

    assert vistos[0]['lang'] == 'es'


# ── A4: TrayApp.cancel_continuation ──────────────────────────────────────

@pytest.fixture
def sin_hilos(monkeypatch):
    creados = []

    class HiloFalso:
        def __init__(self, target=None, args=(), **kwargs):
            self.target, self.args, self.daemon = target, args, False
            self.cancelado = False

        def start(self):
            creados.append(self)

        def cancel(self):
            self.cancelado = True

    monkeypatch.setattr(ta.threading, 'Thread', HiloFalso)
    monkeypatch.setattr(ta.threading, 'Timer', lambda _s, f, args=(): HiloFalso(f, args))
    return creados


def _sesion_en_espera(tray, wav, transcript='texto'):
    sess = {'base_wav': wav, 'parts': [wav], 'transcripts': {wav.stem: transcript},
            'lang': 'es', 'key': 'comite', 'awaiting': False, 'finalized': False,
            'timer': None, 'last_at': time.time()}
    tray._session = sess
    tray.request_continuation()
    assert sess['awaiting'] is True
    return sess


def test_cancelar_la_continuacion_reprograma_el_cierre(tray, sin_hilos, tr_dirs):
    """Sin esto, la sesion se quedaba esperando una grabacion que no iba a llegar."""
    sess = _sesion_en_espera(tray, ta.RECORDINGS_DIR / f'{STEM}.wav')
    assert sin_hilos == []

    tray.cancel_continuation()

    assert sess['awaiting'] is False
    [timer] = sin_hilos
    assert timer.target == tray._finalize_session and timer.args == (sess,)
    assert sess['timer'] is timer


def test_cancelar_la_continuacion_con_una_parte_sin_transcribir_deja_el_cierre_a_register_part(
        tray, sin_hilos, tr_dirs):
    sess = _sesion_en_espera(tray, ta.RECORDINGS_DIR / f'{STEM}.wav', transcript=None)

    tray.cancel_continuation()

    assert sess['awaiting'] is False and sin_hilos == []


def test_cancelar_sin_continuacion_pendiente_no_hace_nada(tray, sin_hilos, tr_dirs):
    tray.cancel_continuation()                                  # sin sesion
    sess = {'base_wav': None, 'parts': [], 'transcripts': {}, 'awaiting': False,
            'finalized': False, 'timer': None}
    tray._session = sess
    tray.cancel_continuation()
    assert sin_hilos == [] and sess['timer'] is None


def test_descartar_la_grabacion_de_una_reconexion_deshace_la_espera(tray, sin_hilos, tr_dirs,
                                                                    monkeypatch):
    """Descartar la grabacion de la reconexion (_cancel_recording) la dejaba
    esperando para siempre."""
    sess = _sesion_en_espera(tray, ta.RECORDINGS_DIR / f'{STEM}.wav')

    class Grabadora:
        is_recording = True
        is_saving = False

        def cancel(self):
            self.is_recording = False

    tray._recorder = Grabadora()
    tray._recording_path = ta.RECORDINGS_DIR / f'{STEM}_b.wav'
    monkeypatch.setattr(ta.TrayApp, '_stop_live', lambda self, wait=False: None)
    monkeypatch.setattr(ta.TrayApp, 'set_recording', lambda self, *a, **k: None)

    tray._cancel_recording()

    assert sess['awaiting'] is False
    assert any(t.target == tray._finalize_session for t in sin_hilos)
