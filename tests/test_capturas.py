"""Capturas de la ventana de la reunion ("Capturar pantalla de Teams").

LO QUE PROTEGEN
1. La captura se guarda con su minuto y queda enlazada a su reunion por el
   nombre: al cerrar la reunion pasa a minutes/<stem>_screenshot_MM-SS.png, con
   su manifiesto, que es lo que lee el consumidor de minutes/.
2. Hay aviso siempre: guardada, o por que no (sin ventana, minimizada,
   bloqueada por Teams, fallo).
3. Teams bloqueando las capturas se respeta: una imagen negra no se guarda.
4. Descartar la reunion se lleva sus capturas, y la retencion las borra.
5. Nunca se guarda otra ventana: si PrintWindow no da la reunion, la pantalla
   solo se copia si lo que se ve es la reunion. El 17/11/2026 se guardo el chat
   de Teams, que estaba delante de la reunion y del mismo tamano.
"""
import json
import sys
import types

import pytest
from PIL import Image

import screen_capture as sc
import teams_detector as td
import tray_app as ta

pytestmark = pytest.mark.unit

REC = '2026-11-16_10-32_Comite_Semanal'
STEM = '20261116_1032_Comite_Semanal'


def imagen(color=(200, 120, 40)):
    return Image.new('RGB', (40, 30), color)


# ── La ventana de la reunion ────────────────────────────────────────────────

@pytest.mark.parametrize('titulo, esperado', [
    ('Comite Semanal | Microsoft Teams', ('teams2', 'Comite Semanal')),
    ('Comite Semanal | Contoso | ana@contoso.com | Microsoft Teams', ('teams2', 'Comite Semanal')),
    ('Chat | Microsoft Teams', (None, None)),
    ('Comite | Microsoft Teams meeting', ('classic', 'Comite')),
    ('Microsoft Teams meeting', ('classic', None)),
    ('Chat | Teams meeting | Microsoft Teams', (None, None)),
    ('Documento - Word', (None, None)),
])
def test_las_reglas_de_titulo_de_teams(titulo, esperado):
    assert td._title_kind(titulo) == esperado


@pytest.fixture
def ventanas(monkeypatch):
    """Ventanas falsas de Teams: (hwnd, pid, visible, titulo), en orden Z."""
    lista = []
    gui = types.SimpleNamespace(
        IsWindowVisible=lambda h: next(v for hh, _p, v, _t in lista if hh == h),
        GetWindowText=lambda h: next(t for hh, _p, _v, t in lista if hh == h),
        EnumWindows=lambda cb, extra: [cb(h, extra) for h, *_ in lista])
    proc = types.SimpleNamespace(
        GetWindowThreadProcessId=lambda h: (0, next(p for hh, p, _v, _t in lista if hh == h)))
    monkeypatch.setitem(sys.modules, 'win32gui', gui)
    monkeypatch.setitem(sys.modules, 'win32process', proc)
    monkeypatch.setattr(td, '_teams_pids', lambda: [10])
    return lista


def test_se_elige_la_ventana_con_el_nombre_de_la_reunion(ventanas):
    ventanas += [(1, 10, True, 'Otra | Microsoft Teams'),
                 (2, 99, True, 'Comite Semanal | Microsoft Teams'),      # otro proceso
                 (3, 10, False, 'Comite Semanal | Microsoft Teams'),     # oculta
                 (4, 10, True, 'Comite Semanal | Microsoft Teams')]
    assert td.find_meeting_window('comite semanal') == 4
    assert td.find_meeting_window('No esta') == 1, "sin coincidencia, la de mas arriba"
    assert td.find_meeting_window(None) == 1


def test_sin_teams_no_hay_ventana(ventanas, monkeypatch):
    assert td.find_meeting_window('x') is None, "sin ninguna ventana de reunion"
    monkeypatch.setattr(td, '_teams_pids', lambda: [])
    assert td.find_meeting_window('x') is None


def test_la_deteccion_de_la_llamada_usa_las_mismas_reglas(ventanas):
    ventanas += [(1, 10, True, 'Chat | Microsoft Teams'),
                 (2, 10, True, 'Comite | Microsoft Teams meeting'),
                 (3, 10, True, 'Otra | Microsoft Teams'),
                 (4, 10, True, '')]
    assert td._check_window_titles([10]) == (True, True, 'Comite', ['Comite', 'Otra'])


def test_sin_pywin32_o_con_una_ventana_rara_no_revienta(ventanas, monkeypatch):
    def rara(h):
        raise OSError('ventana cerrada')
    ventanas.append((1, 10, True, 'Comite | Microsoft Teams'))
    monkeypatch.setattr(sys.modules['win32gui'], 'GetWindowText', rara)
    assert td.find_meeting_window('Comite') is None
    monkeypatch.setitem(sys.modules, 'win32gui', None)
    assert td.find_meeting_window('Comite') is None


# ── Capturar ─────────────────────────────────────────────────────────────────

class _Bitmap:
    def CreateCompatibleBitmap(self, dc, w, h):
        self.size = (w, h)

    def GetInfo(self):
        return {'bmWidth': self.size[0], 'bmHeight': self.size[1]}

    def GetBitmapBits(self, _as_bytes):
        return bytes([30, 60, 90, 0]) * (self.size[0] * self.size[1])

    def GetHandle(self):
        return 1


class _DC:
    def CreateCompatibleDC(self):
        return _DC()

    def SelectObject(self, _obj):
        pass

    def GetSafeHdc(self):
        return 5

    def DeleteDC(self):
        pass


@pytest.fixture
def gdi(monkeypatch):
    """pywin32 y user32 falsos: lo que PrintWindow pinta, sin ventanas de verdad."""
    estado = {'pinta': 1}
    monkeypatch.setitem(sys.modules, 'win32gui', types.SimpleNamespace(
        GetWindowDC=lambda h: 9, DeleteObject=lambda h: None, ReleaseDC=lambda h, dc: None))
    monkeypatch.setitem(sys.modules, 'win32ui', types.SimpleNamespace(
        CreateDCFromHandle=lambda dc: _DC(), CreateBitmap=_Bitmap))
    user32 = types.SimpleNamespace(PrintWindow=lambda h, dc, flags: estado['pinta'],
                                   SetThreadDpiAwarenessContext=lambda ctx: 17)
    monkeypatch.setattr(sc, 'ctypes', types.SimpleNamespace(windll=types.SimpleNamespace(user32=user32),
                                                            c_void_p=lambda v: v))
    return estado


def test_printwindow_devuelve_la_ventana_pintada(gdi):
    img = sc._print_window(1, 4, 3)
    assert img.size == (4, 3) and img.getpixel((0, 0)) == (90, 60, 30)
    gdi['pinta'] = 0
    assert sc._print_window(1, 4, 3) is None


def test_el_ppp_del_hilo_se_pide_y_se_devuelve_sin_romper_nada(gdi, monkeypatch):
    assert sc._thread_dpi_aware() == 17
    sc._restore_dpi(17)

    def falla(_ctx):
        raise OSError('Windows 7')

    monkeypatch.setattr(sc.ctypes.windll.user32, 'SetThreadDpiAwarenessContext', falla)
    assert sc._thread_dpi_aware() is None
    sc._restore_dpi(17)


def test_la_pantalla_se_lee_con_imagegrab(monkeypatch):
    from PIL import ImageGrab
    pedido = []
    monkeypatch.setattr(ImageGrab, 'grab', lambda bbox, all_screens: pedido.append((bbox, all_screens)) or imagen())
    assert sc._screen_grab((1, 2, 3, 4)).size == (40, 30)
    assert pedido == [((1, 2, 3, 4), True)]

REUNION, CHAT = 1, 2
TITULO = 'Comite Semanal | Microsoft Teams'


@pytest.fixture
def ventana(monkeypatch):
    """La ventana 1 es la reunion, en (100, 50, 140, 80). 'delante' dice que ventana
    se ve en cada punto de la pantalla; WindowFromPoint devuelve una hija (x10),
    como el WebView de Teams, y GetAncestor sube a su ventana principal."""
    estado = {'minimizada': False, 'delante': lambda x, y: REUNION}

    def ancestro(h, flag):
        assert flag == sc._GA_ROOT
        if not h:
            raise OSError('Invalid window handle.')
        return h // 10

    gui = types.SimpleNamespace(IsIconic=lambda h: estado['minimizada'],
                                GetWindowRect=lambda h: (100, 50, 140, 80),
                                GetWindowText=lambda h: TITULO,
                                WindowFromPoint=lambda p: estado['delante'](*p) * 10,
                                GetAncestor=ancestro)
    monkeypatch.setitem(sys.modules, 'win32gui', gui)
    return estado


def test_se_captura_la_ventana_aunque_este_tapada(ventana, monkeypatch):
    ventana['delante'] = lambda x, y: CHAT
    monkeypatch.setattr(sc, '_print_window', lambda h, w, hh: imagen())
    monkeypatch.setattr(sc, '_screen_grab', lambda box: pytest.fail('no hacia falta la pantalla'))
    shot = sc.capture(REUNION)
    assert shot.image.size == (40, 30) and (shot.method, shot.title) == ('window', TITULO)


def test_si_sale_negra_se_prueba_con_la_pantalla(ventana, monkeypatch):
    cajas = []
    monkeypatch.setattr(sc, '_print_window', lambda h, w, hh: imagen((0, 0, 0)))
    monkeypatch.setattr(sc, '_screen_grab', lambda box: cajas.append(box) or imagen())
    shot = sc.capture(REUNION)
    assert not sc.is_black(shot.image) and shot.method == 'screen'
    assert cajas == [(100, 50, 140, 80)]


@pytest.mark.parametrize('delante', [
    lambda x, y: CHAT,                                  # el chat de Teams, del mismo tamano, delante
    lambda x, y: CHAT if (x, y) == (132, 74) else REUNION,  # solo tapa una esquina
    lambda x, y: 0,                                     # ninguna ventana en esos puntos
], ids=['tapada', 'una-esquina', 'sin-ventana'])
def test_si_sale_negra_y_otra_la_tapa_no_se_copia_la_otra(ventana, monkeypatch, delante):
    ventana['delante'] = delante
    monkeypatch.setattr(sc, '_print_window', lambda h, w, hh: None)
    monkeypatch.setattr(sc, '_screen_grab', lambda box: pytest.fail('copiaria la ventana de delante'))
    with pytest.raises(sc.CaptureError) as e:
        sc.capture(REUNION)
    assert e.value.reason == 'covered'


def test_se_mira_el_centro_y_cerca_de_cada_esquina(ventana):
    mirados = []
    ventana['delante'] = lambda x, y: mirados.append((x, y)) or REUNION
    assert sc._shows_window(REUNION, (100, 50, 140, 80))
    assert mirados == [(120, 65), (108, 56), (132, 56), (108, 74), (132, 74)]


def test_si_printwindow_falla_tambien_se_prueba_con_la_pantalla(ventana, monkeypatch):
    def falla(*_a):
        raise OSError('sin DC')
    monkeypatch.setattr(sc, '_print_window', falla)
    monkeypatch.setattr(sc, '_screen_grab', lambda box: imagen())
    assert sc.capture(REUNION).method == 'screen'


def test_si_teams_la_bloquea_no_se_guarda_nada(ventana, monkeypatch):
    monkeypatch.setattr(sc, '_print_window', lambda h, w, hh: None)
    monkeypatch.setattr(sc, '_screen_grab', lambda box: imagen((0, 0, 0)))
    with pytest.raises(sc.CaptureError) as e:
        sc.capture(1)
    assert e.value.reason == 'blocked'


def test_minimizada_o_sin_tamano_no_se_captura(ventana, monkeypatch):
    ventana['minimizada'] = True
    with pytest.raises(sc.CaptureError) as e:
        sc.capture(1)
    assert e.value.reason == 'minimized'
    ventana['minimizada'] = False
    monkeypatch.setitem(sys.modules, 'win32gui', types.SimpleNamespace(
        IsIconic=lambda h: False, GetWindowRect=lambda h: (0, 0, 0, 0), GetWindowText=lambda h: TITULO))
    with pytest.raises(sc.CaptureError) as e:
        sc.capture(1)
    assert e.value.reason == 'failed'


# ── Guardar, mover a minutes/, descartar ────────────────────────────────────

def test_se_guarda_con_su_minuto_y_queda_apuntada(tr_dirs):
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    a = sc.save(imagen(), wav, 754.4, TITULO, 'screen')
    b = sc.save(imagen(), wav, 754.9, TITULO)
    assert (a.name, b.name) == (f'{REC}_screenshot_12-34.png', f'{REC}_screenshot_12-34_2.png')
    [e1, e2] = sc.read_log(wav)
    assert e1['file'] == a.name and e1['offset_s'] == 754.4 and e1['window_title'] == TITULO
    assert (e1['method'], e2['method']) == ('screen', None)
    assert len(e1['sha256']) == 64 and e1['bytes'] == a.stat().st_size and e2['file'] == b.name


def test_un_registro_roto_no_tumba_la_lectura(tr_dirs):
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    wav.with_suffix(sc.LOG_SUFFIX).write_text('{roto\n[]\n{"file": "x.png"}\n', encoding='utf-8')
    assert sc.read_log(wav) == [{'file': 'x.png'}]
    assert sc.read_log(ta.RECORDINGS_DIR / 'no_existe.wav') == []


def test_al_cerrar_pasan_a_minutes_con_el_nombre_de_las_minutas(tr_dirs):
    uno = ta.RECORDINGS_DIR / f'{REC}.wav'
    dos = ta.RECORDINGS_DIR / '2026-11-16_11-05_Comite_Semanal.wav'
    sc.save(imagen(), uno, 65, 'Comite')
    sc.save(imagen(), dos, 65, 'Comite')
    md = ta.MINUTES_DIR / f'{STEM}.md'

    filed = sc.file_for_minutes([uno, dos], md)

    assert [e['file'] for e in filed] == [f'{STEM}_screenshot_01-05.png', f'{STEM}_screenshot_p2_01-05.png']
    assert all((ta.MINUTES_DIR / e['file']).exists() for e in filed)
    manifiesto = json.loads((ta.MINUTES_DIR / f'{STEM}_screenshots.json').read_text(encoding='utf-8'))
    assert manifiesto['meeting'] == STEM
    assert [(e['part'], e['recording']) for e in manifiesto['screenshots']] == [(1, REC), (2, dos.stem)]
    assert not list(ta.RECORDINGS_DIR.glob('*_screenshot_*')) and not uno.with_suffix(sc.LOG_SUFFIX).exists()
    assert sc.file_for_minutes([uno, dos], md) == [], "lo ya movido no se repite"


def test_un_segundo_cierre_conserva_lo_que_ya_habia(tr_dirs):
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    md = ta.MINUTES_DIR / f'{STEM}.md'
    sc.save(imagen(), wav, 5, 't')
    sc.file_for_minutes([wav], md)
    sc.save(imagen(), wav, 9, 't')
    sc.file_for_minutes([wav], md)
    manifiesto = json.loads((ta.MINUTES_DIR / f'{STEM}_screenshots.json').read_text(encoding='utf-8'))
    assert [e['file'] for e in manifiesto['screenshots']] == [f'{STEM}_screenshot_00-05.png',
                                                              f'{STEM}_screenshot_00-09.png']


def test_una_captura_que_ya_no_esta_se_salta(tr_dirs):
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    png = sc.save(imagen(), wav, 5, 't')
    png.unlink()
    assert sc.file_for_minutes([wav], ta.MINUTES_DIR / f'{STEM}.md') == []
    assert not (ta.MINUTES_DIR / f'{STEM}_screenshots.json').exists()


# ── En el transcript, como las notas ────────────────────────────────────────

def test_al_transcript_van_las_capturas_que_siguen_ahi_con_su_segundo(tr_dirs):
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    a = sc.save(imagen(), wav, 30.6, 't')
    b = sc.save(imagen(), wav, 95.2, 't')
    sc.save(imagen(), wav, 120, 't').unlink()
    assert sc.transcript_items(wav) == [(30, a.name), (95, b.name)]
    assert sc.transcript_items(ta.RECORDINGS_DIR / 'otra.wav') == []


def test_el_manifiesto_guarda_el_nombre_con_que_se_grabo(tr_dirs):
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    png = sc.save(imagen(), wav, 5, 't')
    [e] = sc.file_for_minutes([wav], ta.MINUTES_DIR / f'{STEM}.md')
    assert (e['recorded_as'], e['file']) == (png.name, f'{STEM}_screenshot_00-05.png')


def test_relink_pone_el_nombre_de_minutes_tambien_en_un_segundo_cierre(tr_dirs):
    """Una reconexion cierra otra vez la reunion: el transcript se rehace con los
    nombres de la grabacion, tambien los de capturas que ya movio el primer cierre."""
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    md = ta.MINUTES_DIR / f'{STEM}.md'
    a = sc.save(imagen(), wav, 5, 't')
    sc.file_for_minutes([wav], md)
    b = sc.save(imagen(), wav, 9, 't')
    sc.file_for_minutes([wav], md)
    texto = f'[00:05] [Captura]: {a.name}\n[00:09] [Captura]: {b.name}'
    assert sc.relink(texto, md) == (f'[00:05] [Captura]: {STEM}_screenshot_00-05.png\n'
                                    f'[00:09] [Captura]: {STEM}_screenshot_00-09.png')


def test_relink_sin_manifiesto_o_roto_no_toca_el_texto(tr_dirs):
    md = ta.MINUTES_DIR / f'{STEM}.md'
    assert sc.relink('[00:05] hola', md) == '[00:05] hola'
    (ta.MINUTES_DIR / f'{STEM}_screenshots.json').write_text('[roto', encoding='utf-8')
    assert sc.relink('[00:05] hola', md) == '[00:05] hola'
    (ta.MINUTES_DIR / f'{STEM}_screenshots.json').write_text('[]', encoding='utf-8')
    assert sc.relink('[00:05] hola', md) == '[00:05] hola'


def test_la_parte_lleva_sus_capturas_en_su_minuto_una_sola_vez(tray, tr_dirs, monkeypatch):
    monkeypatch.setattr(ta, 'get_ui_language', lambda: 'es')
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    txt = ta.RECORDINGS_DIR / f'{REC}_transcript.txt'
    png = sc.save(imagen(), wav, 30.6, 't')
    texto = '[00:10] [Otros]: hola\n[00:40] [Otros]: mirad esta slide'

    una = tray._merge_screenshots(wav, txt, texto)

    assert una.splitlines() == ['[00:10] [Otros]: hola', f'[00:30] [Captura]: {png.name}',
                                '[00:40] [Otros]: mirad esta slide']
    assert txt.read_text(encoding='utf-8') == una
    assert tray._merge_screenshots(wav, txt, una) == una, "el pipeline puede pasar dos veces"
    assert tray._merge_screenshots(ta.RECORDINGS_DIR / 'sin.wav', txt, texto) == texto


def test_si_no_se_puede_escribir_el_transcript_sigue_sin_las_capturas(tray, tr_dirs, monkeypatch):
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    sc.save(imagen(), wav, 5, 't')
    txt = ta.RECORDINGS_DIR / f'{REC}_transcript.txt'
    original = type(txt).write_text

    def lleno(self, *a, **k):
        if self == txt:
            raise OSError('disco lleno')
        return original(self, *a, **k)

    monkeypatch.setattr(type(txt), 'write_text', lleno)
    assert tray._merge_screenshots(wav, txt, '[00:01] [Otros]: hola') == '[00:01] [Otros]: hola'


def test_la_retencion_las_borra_con_su_grabacion_y_con_sus_minutas(tr_dirs, monkeypatch):
    import os
    import time

    import storage
    viejo = time.time() - 400 * 86400
    en_processed = ta.RECORDINGS_DIR / 'processed' / f'{REC}_screenshot_00-05.png'
    en_minutes = ta.MINUTES_DIR / f'{STEM}_screenshot_00-05.png'
    manifiesto = ta.MINUTES_DIR / f'{STEM}_screenshots.json'
    for p in (en_processed, en_minutes, manifiesto):
        p.write_bytes(b'x')
        os.utime(p, (viejo, viejo))
    monkeypatch.setattr(storage, 'MINUTES_RETENTION_DAYS', 30)
    storage.cleanup_old_recordings(days=10)
    storage.cleanup_old_minutes()
    assert not any(p.exists() for p in (en_processed, en_minutes, manifiesto))


def test_descartar_la_grabacion_se_lleva_sus_capturas(tr_dirs):
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    sc.save(imagen(), wav, 5, 't')
    sc.save(imagen(), wav, 9, 't')
    assert sc.discard(wav) == 2
    assert not list(ta.RECORDINGS_DIR.glob(f'{REC}*'))


def test_una_captura_que_no_se_deja_borrar_no_para_el_descarte(tr_dirs, monkeypatch):
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    sc.save(imagen(), wav, 5, 't')
    original = type(wav).unlink

    def terca(self, missing_ok=False):
        if self.suffix == '.png':
            raise PermissionError('abierta en el visor')
        return original(self, missing_ok=missing_ok)

    monkeypatch.setattr(type(wav), 'unlink', terca)
    assert sc.discard(wav) == 0
    assert not wav.with_suffix(sc.LOG_SUFFIX).exists()


# ── La bandeja ───────────────────────────────────────────────────────────────

@pytest.fixture
def avisos(tray, monkeypatch):
    vistos = []
    monkeypatch.setattr(tray, '_notify', lambda titulo, msg: vistos.append((titulo, msg)))
    monkeypatch.setattr(ta, 'get_ui_language', lambda: 'es')
    return vistos


def test_capturar_guarda_y_avisa_con_el_minuto(tray, avisos, tr_dirs, monkeypatch):
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    pedida = []
    monkeypatch.setattr(td, 'find_meeting_window', lambda name: pedida.append(name) or 7)
    monkeypatch.setattr(sc, 'capture', lambda hwnd: sc.Shot(imagen(), 'window', TITULO))
    monkeypatch.setattr(ta.time, 'time', lambda: 1000.0 + 754)

    png = tray._take_screenshot(wav, 1000.0)

    assert png.name == f'{REC}_screenshot_12-34.png' and png.exists()
    assert pedida == ['Comite Semanal']
    assert avisos == [('Noted', 'Captura guardada en el minuto 12:34.')]
    [entrada] = sc.read_log(wav)
    assert (entrada['window_title'], entrada['method']) == (TITULO, 'window'), \
        "se apunta la ventana que se capturo de verdad, no el nombre de la reunion"


@pytest.mark.parametrize('motivo, aviso', [
    ('minimized', 'La ventana de Teams esta minimizada: abrela y vuelve a capturar.'),
    ('covered', 'Otra ventana tapa la de la reunion: pon la reunion delante y vuelve a capturar.'),
    ('blocked', 'Teams no deja capturar esta reunion: no se ha guardado nada.'),
])
def test_si_no_se_puede_capturar_se_dice_por_que(tray, avisos, tr_dirs, monkeypatch, motivo, aviso):
    monkeypatch.setattr(td, 'find_meeting_window', lambda name: 7)

    def no(hwnd):
        raise sc.CaptureError(motivo)

    monkeypatch.setattr(sc, 'capture', no)
    assert tray._take_screenshot(ta.RECORDINGS_DIR / f'{REC}.wav', None) is None
    assert avisos == [('Noted ⚠', aviso)]
    assert not list(ta.RECORDINGS_DIR.glob('*.png'))


def test_sin_ventana_o_con_un_fallo_tambien_avisa(tray, avisos, tr_dirs, monkeypatch):
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    monkeypatch.setattr(td, 'find_meeting_window', lambda name: None)
    assert tray._take_screenshot(wav, None) is None
    monkeypatch.setattr(td, 'find_meeting_window', lambda name: 7)

    def roto(hwnd):
        raise RuntimeError('GDI')

    monkeypatch.setattr(sc, 'capture', roto)
    assert tray._take_screenshot(wav, None) is None
    assert [a for _t, a in avisos] == [
        'No encuentro la ventana de la reunion de Teams: no se ha guardado nada.',
        'No se pudo guardar la captura (el detalle esta en noted.log).']


def test_el_boton_solo_hace_algo_mientras_se_graba(tray, tr_dirs, monkeypatch):
    hechas = []
    monkeypatch.setattr(tray, '_take_screenshot', lambda path, started: hechas.append((path, started)))
    monkeypatch.setattr(ta.threading, 'Thread',
                        lambda target, **_k: type('H', (), {'start': lambda self: target()})())
    tray._recording_path, tray._recording_start = None, None
    tray._capture_screen()
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    tray._recording_path, tray._recording_start = wav, 1000.0
    tray._capture_screen()
    assert hechas == [(wav, 1000.0)]


def test_cancelar_la_grabacion_borra_sus_capturas(tray, tr_dirs, monkeypatch):
    import live_transcriber as lt
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    sc.save(imagen(), wav, 5, 't')
    tray._recorder = type('R', (), {'is_recording': True, 'cancel': lambda self: None})()
    tray._recording_path = wav
    monkeypatch.setattr(lt, 'kill', lambda *_a: None)
    tray._cancel_recording()
    assert not list(ta.RECORDINGS_DIR.glob('*_screenshot_*'))


def test_si_borrar_las_capturas_falla_el_descarte_sigue(tray, tr_dirs, monkeypatch):
    import live_transcriber as lt
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    tray._recorder = type('R', (), {'is_recording': True, 'cancel': lambda self: None})()
    tray._recording_path, tray._recording_start = wav, 1000.0
    monkeypatch.setattr(lt, 'kill', lambda *_a: None)

    def rota(_path):
        raise OSError('disco')

    monkeypatch.setattr(sc, 'discard', rota)
    tray._cancel_recording()
    assert tray._recording_start is None, "el descarte no llego a parar la grabacion"


def test_descartar_la_reunion_manda_sus_capturas_a_la_papelera(tray, tr_dirs):
    wav = ta.RECORDINGS_DIR / f'{REC}.wav'
    sc.save(imagen(), wav, 5, 't')
    md = ta.MINUTES_DIR / f'{STEM}.md'
    md.write_text('x', encoding='utf-8')
    sc.save(imagen(), ta.RECORDINGS_DIR / '2026-11-16_10-31_Otra.wav', 1, 't')
    sc.file_for_minutes([ta.RECORDINGS_DIR / '2026-11-16_10-31_Otra.wav'], md)
    sc.save(imagen(), wav, 9, 't')

    tray._discard_job(wav, 'prueba', md)

    papelera = tr_dirs / 'trash' / REC
    nombres = sorted(p.name for p in papelera.iterdir())
    assert f'{REC}_screenshot_00-09.png' in nombres and f'{STEM}_screenshot_00-01.png' in nombres
    assert f'{STEM}_screenshots.json' in nombres
    assert not list(ta.MINUTES_DIR.glob('*_screenshot*'))
