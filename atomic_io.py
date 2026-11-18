"""Escritura atomica de ficheros de texto y bloqueo entre procesos.

Se escribe en `<nombre>.tmp` al lado y se hace replace: un crash a mitad deja
el original intacto en vez de un JSON truncado."""
import contextlib
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    import msvcrt
except ImportError:  # no Windows
    msvcrt = None

log = logging.getLogger(__name__)


def _replace(tmp: Path, path: Path, attempts: int = 10, wait: float = 0.05) -> None:
    """os.replace con reintentos: en Windows falla con PermissionError si otro
    proceso (antivirus, indexador) tiene el destino abierto en ese instante."""
    for i in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(wait)


def write_text_atomic(path, text: str, encoding: str = 'utf-8') -> None:
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    try:
        tmp.write_text(text, encoding=encoding)
        _replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def quarantine_corrupt(path, logger=None) -> Path | None:
    """Copia un fichero que existe pero no se puede parsear a
    `<nombre>.corrupt-<YYYYmmdd-HHMMSS>` antes de que alguien lo sobrescriba
    con el valor por defecto. No duplica: si ya hay una copia con los mismos
    bytes no crea otra (`_load` se llama en cada lectura)."""
    logger = logger or log
    path = Path(path)
    try:
        raw = path.read_bytes()
        for old in path.parent.glob(path.name + '.corrupt-*'):
            try:
                if old.read_bytes() == raw:
                    return old
            except OSError:
                continue
        dst = path.with_name(f"{path.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, dst)
        logger.warning(f"{path.name} no se puede leer: copia conservada en {dst.name}")
        return dst
    except OSError as e:
        logger.warning(f"{path.name} corrupto y no se pudo copiar: {e}")
        return None


_thread_lock = threading.RLock()
_depth = threading.local()


@contextlib.contextmanager
def file_lock(path, timeout: float = 3.0):
    """Bloqueo entre hilos (RLock) y entre procesos (msvcrt.locking sobre
    `path`). Si en `timeout` s no se obtiene el de fichero se sigue sin el:
    mejor una carrera improbable que colgar la ventana. Reentrante."""
    with _thread_lock:
        if getattr(_depth, 'n', 0) > 0:
            _depth.n += 1
            try:
                yield
            finally:
                _depth.n -= 1
            return
        fh = None
        locked = False
        try:
            fh = open(path, 'a+b')
            if msvcrt is not None:
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(0.02)
        except OSError:
            pass
        _depth.n = 1
        try:
            yield
        finally:
            _depth.n = 0
            if fh is not None:
                if locked:
                    try:
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                fh.close()
