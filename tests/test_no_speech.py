"""Una grabacion sin voz no es un fallo del transcriptor.

INCIDENTE QUE ORIGINO ESTOS TESTS (22/09/2026)
Una ventana residual de Teams disparo una grabacion de 8,5 s. El VAD de
Whisper se llevo el audio entero porque no habia voz, `transcribe()` devolvio
('', 'en') sin excepcion — funciono — y la app escribio:

    ERROR tray_app: Transcripción fallida para 2026-09-22_12-06_....wav

y saco la notificacion de error. Es la misma linea y la misma notificacion que
cuando el transcriptor se queda sin memoria o revienta, asi que era imposible
distinguir "no habia nada que transcribir" de "esto se ha roto". Lo que el
usuario ve es que la herramienta falla.

La diferencia importa: `isError` es sobre si el sistema funciono, no sobre si
hubo resultado. Cero resultados con el sistema sano no es un error.
"""
import logging

import pytest

import tray_app as ta

pytestmark = pytest.mark.unit

STEM = '2026-09-22_12-06_Ventana_Residual'


def cadenas():
    """El mismo idioma que resolveria la app. Fijarlo a 'es' hacia que el
    test dependiera de la configuracion de la maquina."""
    return ta._STR.get(ta.get_ui_language(), ta._STR['en'])


@pytest.fixture
def pipeline(tray, tr_dirs, wav_factory, monkeypatch):
    """Un trabajo listo para correr, con `transcribe` bajo control."""
    wav = wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.3)
    avisos = []
    monkeypatch.setattr(tray, '_notify', lambda t, m: avisos.append((t, m)))
    monkeypatch.setattr(tray, 'set_processing', lambda *_a: None)
    monkeypatch.setattr(tray, '_register_part', lambda *_a: avisos.append(('registrado', '')))

    def con(resultado):
        import transcriber
        monkeypatch.setattr(transcriber, 'transcribe', lambda *_a, **_k: resultado)
        return wav, avisos

    return con


def test_sin_voz_NO_se_registra_como_error(pipeline, tray, caplog):
    """EL TEST CLAVE: el nivel de log distingue un no-evento de una averia."""
    wav, _ = pipeline(('', 'en'))
    with caplog.at_level(logging.DEBUG, logger='tray_app'):
        tray._run_pipeline_sync(wav)

    errores = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errores, f"se registro como error: {[r.message for r in errores]}"


def test_sin_voz_avisa_con_un_mensaje_distinto_al_de_fallo(pipeline, tray):
    wav, avisos = pipeline(('', 'en'))
    tray._run_pipeline_sync(wav)

    s = cadenas()
    textos = [m for _t, m in avisos]
    assert s['no_speech'] in textos
    assert s['transcription_failed'] not in textos


def test_sin_voz_no_pone_el_icono_de_aviso(pipeline, tray):
    """La campana con ⚠ es para cosas que hay que mirar. Esta no lo es."""
    wav, avisos = pipeline(('', 'en'))
    tray._run_pipeline_sync(wav)
    titulos = [t for t, _m in avisos if t != 'registrado']
    assert all('⚠' not in t for t in titulos), titulos


def test_sin_voz_no_deja_un_transcript_vacio_en_disco(pipeline, tray):
    wav, _ = pipeline(('', 'en'))
    tray._run_pipeline_sync(wav)
    assert not (ta.RECORDINGS_DIR / f'{STEM}_transcript.txt').exists()


def test_sin_voz_si_guarda_el_idioma_detectado(pipeline, tray):
    """Se detecto idioma, luego el modelo corrio. Es la prueba de que no fallo."""
    wav, _ = pipeline(('', 'en'))
    tray._run_pipeline_sync(wav)
    # _move_to_processed lo lleva a processed/ junto al WAV (lote A: un reintento
    # de minutas lo necesita ahi); lo que importa es que se guardo.
    lang = ta.RECORDINGS_DIR / 'processed' / f'{STEM}_transcript.lang'
    assert lang.exists() and lang.read_text().strip() == 'en'


# ── El WAV tiene que salir de recordings/ ────────────────────────────────
#
# INCIDENTE (29/09/2026). Una grabacion de 8,5s sin voz del 22/09 seguia en
# recordings/ una semana despues, y se reprocesaba en CADA arranque del
# daemon. _recover_pending busca WAVs que no tengan minutas, y una grabacion
# sin voz nunca las va a tener: bucle infinito.
#
# El coste no es teorico. Cada pasada carga un modelo de Whisper —unos 1 GB y
# 15 segundos— en un portatil donde quedarse sin memoria es exactamente lo que
# impide arrancar al worker del loopback, y eso ya costo las voces de los demas
# en una reunion de 36 minutos.

def test_sin_voz_saca_el_wav_de_recordings(pipeline, tray):
    """EL TEST CLAVE. Sin esto la grabacion se reprocesa para siempre."""
    wav, _ = pipeline(('', 'en'))
    tray._run_pipeline_sync(wav)

    assert not wav.exists(), "el WAV sigue en recordings/: se reencolara"
    assert (ta.RECORDINGS_DIR / 'processed' / wav.name).exists(), \
        "no aparecio en processed/"


def test_sin_voz_ya_no_lo_reencola_el_arranque(pipeline, tray, monkeypatch):
    """La consecuencia que importa: tras procesarla, _recover_pending ya no la
    ve. Es lo que rompe el bucle, y se comprueba con la funcion de verdad."""
    wav, _ = pipeline(('', 'en'))
    tray._run_pipeline_sync(wav)

    encolados = []
    monkeypatch.setattr(tray, '_enqueue_pipeline', encolados.append, raising=False)
    monkeypatch.setattr(ta.time, 'sleep', lambda _s: None)
    tray._pipeline_queue = __import__('queue').Queue()
    tray._pipeline_queued = []
    tray._recover_pending()

    pendientes = [p for p in list(tray._pipeline_queue.queue) + encolados if p]
    assert not any(str(wav.name) in str(p) for p in pendientes), \
        f"la grabacion sin voz se volvio a encolar: {pendientes}"


def test_un_fallo_real_NO_mueve_el_wav(pipeline, tray):
    """CONTROL, y es el limite del arreglo: si transcribe() fallo de verdad, la
    grabacion tiene que quedarse donde esta para reintentarla al arrancar.
    Moverla ahi seria perderla."""
    wav, _ = pipeline(None)
    tray._run_pipeline_sync(wav)

    assert wav.exists(), "un fallo real no puede sacar el WAV de recordings/"
    assert not (ta.RECORDINGS_DIR / 'processed' / wav.name).exists()


# ── Y un fallo de verdad SIGUE siendo un fallo ───────────────────────────

def test_un_fallo_real_sigue_siendo_error(pipeline, tray, caplog):
    """CONTROL: `transcribe` devolviendo None es que no pudo. Eso no se
    ablanda — si esto deja de registrarse como error, el arreglo se paso."""
    wav, avisos = pipeline(None)
    with caplog.at_level(logging.DEBUG, logger='tray_app'):
        tray._run_pipeline_sync(wav)

    assert [r for r in caplog.records if r.levelno >= logging.ERROR], \
        "un fallo real tiene que seguir siendo ERROR"
    assert cadenas()['transcription_failed'] in [m for _t, m in avisos]


def test_un_fallo_real_no_escribe_el_idioma(pipeline, tray):
    wav, _ = pipeline(None)
    tray._run_pipeline_sync(wav)
    assert not (ta.RECORDINGS_DIR / f'{STEM}_transcript.lang').exists()


def test_con_texto_el_pipeline_sigue_adelante(pipeline, tray):
    """CONTROL: el camino bueno no se toco."""
    wav, avisos = pipeline(('00:00 hola', 'es'))
    tray._run_pipeline_sync(wav)

    assert (ta.RECORDINGS_DIR / f'{STEM}_transcript.txt').read_text(encoding='utf-8') == '00:00 hola'
    assert ('registrado', '') in avisos


# ── Paridad de las cadenas nuevas ────────────────────────────────────────

@pytest.mark.parametrize('idioma', sorted(ta._STR))
def test_no_speech_existe_en_todos_los_idiomas(idioma):
    assert ta._STR[idioma].get('no_speech'), f"falta no_speech en '{idioma}'"
