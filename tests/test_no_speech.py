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
    lang = ta.RECORDINGS_DIR / f'{STEM}_transcript.lang'
    assert lang.exists() and lang.read_text().strip() == 'en'


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
