"""La retencion de audio no borra una reunion que todavia no se proceso, pero
tampoco la guarda para siempre.

cleanup_old_recordings corre al arrancar (main.py), justo antes de que
_recover_pending reencole los WAV de recordings/ que no tienen minutas. Con la
retencion en 10 dias (8b8822e), una reunion que fallo o que se grabo con el
portatil a punto de apagarse y no se abrio en 10 dias se borraba sin haberse
transcrito. Y al reves: sin tope, una grabacion sin voz o con la transcripcion
rota se reencolaria en cada arranque para siempre. Lo pendiente se guarda hasta
PENDING_RETENTION_FACTOR veces la retencion (30 dias con la de 10).
"""
import os
import time

import pytest

import storage

pytestmark = pytest.mark.unit

STEM = '2026-10-01_10-00_Comite'
HACE_20 = time.time() - 20 * 86400       # pasada la retencion (10), dentro del tope (30)
HACE_40 = time.time() - 40 * 86400       # pasado el tope


def envejecer(path, cuando=HACE_20):
    path.write_bytes(b'RIFF')
    os.utime(path, (cuando, cuando))
    return path


def con_minutas():
    storage.MINUTES_DIR.mkdir(parents=True, exist_ok=True)
    (storage.MINUTES_DIR / '20261001_1000_Comite.md').write_text('# Comite', encoding='utf-8')


def test_una_reunion_pendiente_no_se_borra(tr_dirs):
    """El audio, sus pistas, el contexto que escribio el usuario y el .partial
    de una transcripcion a medias: sin ellos se perderia o se rehacia peor."""
    restos = [envejecer(storage.RECORDINGS_DIR / f'{STEM}{s}')
              for s in ('.wav', '.mic.wav', '.loop.wav', '.context', '.partial')]
    storage.cleanup_old_recordings()
    assert all(p.exists() for p in restos)


def test_pasado_el_tope_una_reunion_pendiente_se_borra(tr_dirs):
    """Una grabacion sin voz o rota tambien se queda sin minutas: sin tope se
    reencolaria en cada arranque."""
    restos = [envejecer(storage.RECORDINGS_DIR / f'{STEM}{s}', HACE_40) for s in ('.wav', '.context')]
    storage.cleanup_old_recordings()
    assert not any(p.exists() for p in restos)


def test_con_minutas_se_borra_a_los_10_dias(tr_dirs):
    restos = [envejecer(storage.RECORDINGS_DIR / f'{STEM}{s}') for s in ('.wav', '.lang')]
    con_minutas()
    storage.cleanup_old_recordings()
    assert not any(p.exists() for p in restos)


def test_lo_procesado_se_borra_por_antiguo(tr_dirs):
    processed = storage.RECORDINGS_DIR / 'processed'
    processed.mkdir(parents=True, exist_ok=True)
    wav = envejecer(processed / f'{STEM}.wav')
    storage.cleanup_old_recordings()
    assert not wav.exists()


def test_lo_reciente_no_se_toca(tr_dirs):
    wav = storage.RECORDINGS_DIR / f'{STEM}.wav'
    wav.write_bytes(b'RIFF')
    con_minutas()
    storage.cleanup_old_recordings()
    assert wav.exists()


# ── originals/: el audio original de lo importado o soltado en inbox/ ───────

def en_originals(nombre, cuando=HACE_20):
    d = storage.RECORDINGS_DIR / 'originals'
    d.mkdir(parents=True, exist_ok=True)
    return envejecer(d / nombre, cuando)


def test_un_original_con_minutas_se_borra_a_los_10_dias(tr_dirs):
    """Sin esto, los MP3, M4A y MP4 importados se acumulan en disco para siempre."""
    original = en_originals(f'{STEM}.m4a')
    con_minutas()
    storage.cleanup_old_recordings()
    assert not original.exists()


def test_un_original_sin_minutas_se_guarda_hasta_el_tope(tr_dirs):
    """Como la grabacion que sale de el: si no se proceso, no se borra a los 10 dias."""
    original = en_originals(f'{STEM}.mp4')
    storage.cleanup_old_recordings()
    assert original.exists()


def test_pasado_el_tope_un_original_pendiente_se_borra(tr_dirs):
    original = en_originals(f'{STEM}.mp4', HACE_40)
    storage.cleanup_old_recordings()
    assert not original.exists()


def test_un_original_reciente_no_se_toca(tr_dirs):
    original = en_originals(f'{STEM}.wav', time.time())
    con_minutas()
    storage.cleanup_old_recordings()
    assert original.exists()


def test_la_marca_de_audio_importado_se_borra_con_su_grabacion(tr_dirs):
    """Si no, quedarian marcas sueltas para siempre en recordings/ y processed/."""
    processed = storage.RECORDINGS_DIR / 'processed'
    processed.mkdir(parents=True, exist_ok=True)
    marcas = [envejecer(storage.RECORDINGS_DIR / f'{STEM}.imported'),
              envejecer(processed / f'{STEM}.imported')]
    con_minutas()
    storage.cleanup_old_recordings()
    assert not any(m.exists() for m in marcas)


def test_la_marca_reciente_no_se_toca(tr_dirs):
    marca = storage.RECORDINGS_DIR / f'{STEM}.imported'
    marca.write_text('inbox', encoding='utf-8')
    storage.cleanup_old_recordings()
    assert marca.exists()

