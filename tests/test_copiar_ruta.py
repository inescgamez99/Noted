"""«Copiar ruta» de una captura o del transcript (25/11/2026).

Para que Claude mire una captura o un transcript, el usuario los pegaba enteros
en la conversacion. Con la ruta basta: Claude lo lee del disco.

LO QUE ESTOS TESTS PROTEGEN
1. La ruta del transcript es la del texto que se ve: la misma busqueda que
   get_transcript_text (junto a las minutas o, en las reuniones antiguas, en
   recordings/processed o recordings). Deducirla en el panel daria una ruta que
   no existe en las reuniones antiguas.
2. La ruta de una captura solo sale para una captura DE ESA reunion: el nombre
   llega del DOM y no puede servir para dar la ruta de otra cosa.
"""
import pytest

import app_window as aw
import config

pytestmark = pytest.mark.unit

STEM = '20261125_1148_Reunion'


@pytest.fixture
def api():
    return object.__new__(aw.AppAPI)


@pytest.fixture
def minutas(tr_dirs):
    md = tr_dirs / 'minutes' / f'{STEM}.md'
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text('Nada.\n', encoding='utf-8')
    return md


def test_el_transcript_junto_a_las_minutas(api, minutas):
    tx = minutas.parent / f'{STEM}_transcript.txt'
    tx.write_text('hola', encoding='utf-8')
    assert api.get_transcript_path(str(minutas)) == str(tx)
    assert api.get_transcript_text(str(minutas)) == 'hola'


@pytest.mark.parametrize('carpeta, nombre', [
    ('processed', f'{STEM}_transcript.txt'),
    ('', f'{STEM}.txt'),
    ('processed', f'{STEM}_es.txt'),
])
def test_una_reunion_antigua_lo_tiene_en_recordings(api, minutas, carpeta, nombre):
    tx = config.RECORDINGS_DIR / carpeta / nombre if carpeta else config.RECORDINGS_DIR / nombre
    tx.parent.mkdir(parents=True, exist_ok=True)
    tx.write_text('viejo', encoding='utf-8')
    assert api.get_transcript_path(str(minutas)) == str(tx)
    assert api.get_transcript_text(str(minutas)) == 'viejo', "la ruta es la del texto que se ve"


def test_sin_transcript_no_hay_ruta(api, minutas):
    assert api.get_transcript_path(str(minutas)) == ''
    assert api.get_transcript_text(str(minutas)) == ''


def test_un_error_no_rompe_el_panel(api, minutas, monkeypatch):
    def revienta(_md):
        raise OSError('disco')

    monkeypatch.setattr(aw.AppAPI, '_transcript_file', staticmethod(revienta))
    assert api.get_transcript_path(str(minutas)) == ''
    assert api.get_transcript_text(str(minutas)) == ''


def test_la_ruta_de_una_captura_de_esta_reunion(api, minutas):
    shot = minutas.parent / f'{STEM}_screenshot_04-09.png'
    shot.write_bytes(b'png')
    assert api.get_screenshot_path(str(minutas), shot.name) == str(shot)


@pytest.mark.parametrize('nombre', [
    '..\\settings.json',
    '20261124_0900_Otra_screenshot_01-00.png',     # existe, pero es de otra reunion
    f'{STEM}_transcript.txt',
    f'{STEM}_screenshot_09-99.png',                # su nombre, pero no esta en disco
    '',
])
def test_no_da_la_ruta_de_lo_que_no_es_una_captura_suya(api, minutas, nombre):
    (minutas.parent / '20261124_0900_Otra_screenshot_01-00.png').write_bytes(b'png')
    (minutas.parent / f'{STEM}_transcript.txt').write_text('x', encoding='utf-8')
    assert api.get_screenshot_path(str(minutas), nombre) == ''
