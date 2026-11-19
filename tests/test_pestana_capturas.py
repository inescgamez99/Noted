"""Pestaña «Capturas» del panel: listar, miniatura y copiar al portapapeles."""
import sys
import types

import pytest
from PIL import Image

import app_window as aw

pytestmark = pytest.mark.unit

STEM = '20261117_1236_Daily'


@pytest.fixture
def api():
    return object.__new__(aw.AppAPI)


@pytest.fixture
def reunion(tmp_path):
    carpeta = tmp_path / 'minutes'
    carpeta.mkdir()
    md = carpeta / f'{STEM}.md'
    md.write_text('x', encoding='utf-8')
    for nombre in ['_screenshot_12-30', '_screenshot_00-30', '_screenshot_p2_00-10', '_screenshot_00-30_2']:
        Image.new('RGB', (1200, 800), (10, 120, 200)).save(carpeta / f'{STEM}{nombre}.png')
    # lo que NO es de esta reunion o no es una captura
    Image.new('RGB', (10, 10)).save(carpeta / '20261117_0900_Otra_screenshot_00-05.png')
    (carpeta / f'{STEM}_screenshot_00-31.txt').write_text('x')
    return md


def test_lista_las_capturas_de_la_reunion_en_orden(api, reunion):
    ficheros = [s['file'] for s in api.list_screenshots(str(reunion))]
    assert ficheros == [f'{STEM}_screenshot_00-30.png', f'{STEM}_screenshot_00-30_2.png',
                        f'{STEM}_screenshot_12-30.png', f'{STEM}_screenshot_p2_00-10.png']
    assert [s['time'] for s in api.list_screenshots(str(reunion))][:3] == ['00:30', '00:30', '12:30']


def test_sin_capturas_la_lista_esta_vacia(api, tmp_path):
    md = tmp_path / 'sola.md'
    md.write_text('x')
    assert api.list_screenshots(str(md)) == []


def test_la_miniatura_es_pequena_y_en_data_url(api, reunion):
    url = api.get_screenshot_thumb(str(reunion), f'{STEM}_screenshot_00-30.png')
    assert url.startswith('data:image/jpeg;base64,') and len(url) < 60_000


@pytest.mark.parametrize('nombre', [
    '20261117_0900_Otra_screenshot_00-05.png',      # otra reunion de la misma carpeta
    '..\\x_screenshot_00-30.png', f'{STEM}_screenshot_00-31.txt', f'{STEM}_screenshot_09-09.png', '', None])
def test_no_lee_ni_copia_lo_que_no_es_una_captura_de_la_reunion(api, reunion, monkeypatch, nombre):
    fake = types.SimpleNamespace(CF_DIB=8, OpenClipboard=lambda: pytest.fail('toco el portapapeles'))
    monkeypatch.setitem(sys.modules, 'win32clipboard', fake)
    assert api.get_screenshot_thumb(str(reunion), nombre) == ''
    assert api.copy_screenshot(str(reunion), nombre) is False


def test_copiar_deja_un_dib_completo_en_el_portapapeles(api, reunion, monkeypatch):
    puesto = {}
    fake = types.SimpleNamespace(
        CF_DIB=8, OpenClipboard=lambda: puesto.setdefault('abierto', True),
        EmptyClipboard=lambda: None, CloseClipboard=lambda: puesto.setdefault('cerrado', True),
        SetClipboardData=lambda fmt, data: puesto.update(fmt=fmt, data=data))
    monkeypatch.setitem(sys.modules, 'win32clipboard', fake)
    assert api.copy_screenshot(str(reunion), f'{STEM}_screenshot_00-30.png') is True
    assert puesto['fmt'] == 8 and puesto['cerrado']
    # cabecera BITMAPINFOHEADER: tamaño 40, ancho 1200, alto 800 (sin la cabecera de fichero BMP)
    assert int.from_bytes(puesto['data'][0:4], 'little') == 40
    assert int.from_bytes(puesto['data'][4:8], 'little') == 1200
    assert int.from_bytes(puesto['data'][8:12], 'little') == 800


def test_si_el_portapapeles_falla_cierra_y_dice_false(api, reunion, monkeypatch):
    estado = {}

    def _falla(fmt, data):
        raise OSError('ocupado')
    fake = types.SimpleNamespace(
        CF_DIB=8, OpenClipboard=lambda: None, EmptyClipboard=lambda: None,
        CloseClipboard=lambda: estado.setdefault('cerrado', True), SetClipboardData=_falla)
    monkeypatch.setitem(sys.modules, 'win32clipboard', fake)
    assert api.copy_screenshot(str(reunion), f'{STEM}_screenshot_00-30.png') is False
    assert estado.get('cerrado')
