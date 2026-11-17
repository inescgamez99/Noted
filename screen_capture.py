"""Capturas de la ventana de la reunion ("Capturar pantalla de Teams").

Noted captura, guarda y enlaza cada captura a su reunion por el nombre; quien
las cura es el consumidor de minutes/ (contrato en tests/test_consumer_contract.py).
No van al transcript: el transcript sigue siendo la fuente tal cual, y la imagen
original queda localizable.

- Durante la llamada: recordings/<grabacion>_screenshot_MM-SS.png y una linea
  por captura en recordings/<grabacion>.screenshots.jsonl.
- Al cerrar la reunion (file_for_minutes): minutes/<stem>_screenshot_MM-SS.png,
  y minutes/<stem>_screenshots.json con lo que dice cada una. Una reunion en
  varias partes lleva la parte en el nombre a partir de la segunda (_p2_MM-SS).

La captura: PrintWindow con PW_RENDERFULLCONTENT, que captura la ventana aunque
este tapada o en otra pantalla; si sale negra, lo que se ve en pantalla en ese
rectangulo (PIL.ImageGrab), pero solo si lo que se ve es la ventana de la
reunion: si otra la tapa (el chat de Teams, por ejemplo), no se guarda nada y se
avisa. Si tambien sale negra, Teams no deja capturar esa reunion ("Prevent
screen capture"): se respeta y no se guarda nada. Sin dependencias nuevas:
pywin32 y Pillow ya estaban.
"""
import ctypes
import hashlib
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

SUFFIX = '_screenshot_'
LOG_SUFFIX = '.screenshots.jsonl'
MANIFEST_SUFFIX = '_screenshots.json'
_PW_RENDERFULLCONTENT = 0x2
_DPI_PER_MONITOR_V2 = -4
_GA_ROOT = 2


class Shot(NamedTuple):
    image: object
    method: str     # 'window': PrintWindow, la ventana sola; 'screen': lo que se veia, que era ella
    title: str      # el titulo de la ventana capturada, tal cual


class CaptureError(Exception):
    """reason: 'minimized' | 'covered' | 'blocked' | 'failed' (cada uno con su aviso)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def stamp(seconds: float) -> str:
    m, s = divmod(int(max(seconds, 0)), 60)
    return f"{m:02d}-{s:02d}"


def is_black(img) -> bool:
    """Una captura que Teams bloquea, o que fallo, sale negra."""
    return img.convert('L').getextrema()[1] < 16


def _thread_dpi_aware():
    """Rectangulo y bitmap en pixeles reales, solo en este hilo: sin esto, con la
    pantalla al 150 % la captura sale recortada."""
    try:
        return ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(_DPI_PER_MONITOR_V2))
    except Exception:
        return None


def _restore_dpi(previous) -> None:
    if previous:
        try:
            ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))
        except Exception:
            pass


def _print_window(hwnd: int, width: int, height: int):
    import win32gui
    import win32ui
    from PIL import Image
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hwnd_dc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(src, width, height)
        mem.SelectObject(bmp)
        if not ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), _PW_RENDERFULLCONTENT):
            return None
        info = bmp.GetInfo()
        return Image.frombuffer('RGB', (info['bmWidth'], info['bmHeight']),
                                bmp.GetBitmapBits(True), 'raw', 'BGRX', 0, 1)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def _screen_grab(box):
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=box, all_screens=True)


def _shows_window(hwnd: int, box) -> bool:
    """Lo que se ve en ese rectangulo es esta ventana: la que esta delante en el
    centro y cerca de cada esquina tiene que ser ella. Sin esto, con PrintWindow en
    negro, la pantalla copio el chat de Teams, que estaba delante de la reunion y
    del mismo tamano (17/11/2026)."""
    import win32gui
    left, top, right, bottom = box
    dx, dy = (right - left) // 5, (bottom - top) // 5
    points = [((left + right) // 2, (top + bottom) // 2), (left + dx, top + dy), (right - dx, top + dy),
              (left + dx, bottom - dy), (right - dx, bottom - dy)]
    try:
        return all(win32gui.GetAncestor(win32gui.WindowFromPoint(p), _GA_ROOT) == hwnd for p in points)
    except Exception:       # ninguna ventana en ese punto, o se acaba de cerrar
        return False


def capture(hwnd: int) -> Shot:
    """La ventana de la reunion, o CaptureError con el motivo. Nunca otra ventana."""
    import win32gui
    if win32gui.IsIconic(hwnd):
        raise CaptureError('minimized')
    previous = _thread_dpi_aware()
    try:
        title = win32gui.GetWindowText(hwnd) or ''
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if right - left <= 0 or bottom - top <= 0:
            raise CaptureError('failed')
        img = None
        try:
            img = _print_window(hwnd, right - left, bottom - top)
        except Exception as e:
            log.info(f"PrintWindow no pudo capturar la ventana ({e}); se prueba con la pantalla")
        if img is not None and not is_black(img):
            return Shot(img, 'window', title)
        if not _shows_window(hwnd, (left, top, right, bottom)):
            log.info("PrintWindow no dio la ventana y otra la tapa en pantalla: no se copia la pantalla")
            raise CaptureError('covered')
        img = _screen_grab((left, top, right, bottom))
        if is_black(img):
            raise CaptureError('blocked')
        return Shot(img, 'screen', title)
    finally:
        _restore_dpi(previous)


def _unique(path: Path) -> Path:
    n, candidate = 2, path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        n += 1
    return candidate


def save(img, rec_path: Path, seconds: float, window_title: str | None, method: str | None = None) -> Path:
    """Guarda la captura junto a la grabacion y la apunta en su registro."""
    png = _unique(rec_path.with_name(f"{rec_path.stem}{SUFFIX}{stamp(seconds)}.png"))
    img.save(png, 'PNG')
    data = png.read_bytes()
    entry = {'file': png.name, 'offset_s': round(max(seconds, 0), 1),
             'taken_at': datetime.now().isoformat(timespec='seconds'),
             'window_title': window_title or '', 'method': method,
             'width': img.width, 'height': img.height,
             'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    with open(rec_path.with_suffix(LOG_SUFFIX), 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return png


def read_log(rec_path: Path) -> list[dict]:
    try:
        raw = rec_path.with_suffix(LOG_SUFFIX).read_text(encoding='utf-8')
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get('file'):
            out.append(entry)
    return out


def file_for_minutes(parts: list[Path], minutes_path: Path) -> list[dict]:
    """Mueve las capturas de las partes de una reunion junto a sus minutas, con el
    nombre de las minutas, y escribe su manifiesto. Lo ya movido no se repite."""
    stem, entries = minutes_path.stem, []
    for k, part in enumerate(parts):
        for entry in read_log(part):
            src = part.with_name(entry['file'])
            if not src.exists():
                continue
            prefix = f"p{k + 1}_" if k else ''
            dst = _unique(minutes_path.with_name(f"{stem}{SUFFIX}{prefix}{stamp(entry['offset_s'])}.png"))
            shutil.move(str(src), str(dst))
            entries.append({**entry, 'file': dst.name, 'part': k + 1, 'recording': part.stem})
        part.with_suffix(LOG_SUFFIX).unlink(missing_ok=True)
    if entries:
        manifest = minutes_path.with_name(f"{stem}{MANIFEST_SUFFIX}")
        previous = json.loads(manifest.read_text(encoding='utf-8')).get('screenshots', []) \
            if manifest.exists() else []
        manifest.write_text(json.dumps({'meeting': stem, 'screenshots': previous + entries},
                                       ensure_ascii=False, indent=2), encoding='utf-8')
    return entries


def discard(rec_path: Path) -> int:
    """Una grabacion descartada mientras graba se lleva sus capturas: la reunion no existe."""
    n = 0
    for entry in read_log(rec_path):
        try:
            rec_path.with_name(entry['file']).unlink(missing_ok=True)
            n += 1
        except OSError as e:
            log.warning(f"No se pudo borrar la captura {entry['file']}: {e}")
    rec_path.with_suffix(LOG_SUFFIX).unlink(missing_ok=True)
    return n
