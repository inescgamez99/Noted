"""
Módulo de hilo Tkinter persistente.
Un único tk.Tk() root corre en un hilo de fondo dedicado.
Todos los popups y diálogos usan Toplevel sobre ese root vía after().

El primer popup del día fallaba a veces ("'NoneType' object has no attribute
'after'"): el hilo Tk se creaba con el primer popup y solo se le daban 5 s para
arrancar; con el PC recién encendido tk.Tk() tardaba más, y como el detector
solo avisa al empezar la llamada, esa reunión ya no se ofrecía. Ahora:
- start() se llama al iniciar Noted, así el primer popup no paga el arranque;
- se espera hasta START_TIMEOUT s y, si aun así no está, se lanza TkNotReady
  (un error claro que main.py reintenta) en vez de un AttributeError;
- nunca hay dos hilos Tk: antes, si el popup llegaba en el primer segundo (antes
  del primer latido), se creaba un segundo tk.Tk() en otro hilo;
- los errores dentro de Tk (arranque o callbacks) se escriben en noted.log; con
  pythonw se perdían.
"""
import logging
import threading
import tkinter as tk

log = logging.getLogger(__name__)

START_TIMEOUT = 60   # s que se espera a que el hilo Tk esté listo

_root: tk.Tk | None = None
_thread: threading.Thread | None = None
_ready = threading.Event()
_lock  = threading.Lock()


class TkNotReady(RuntimeError):
    """El hilo Tk no está listo (no arrancó a tiempo o se cerró)."""


def _log_callback_error(exc, val, tb):
    log.error("Error en un callback de Tk", exc_info=(exc, val, tb))


def _tk_main():
    global _root
    try:
        root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = _log_callback_error
    except Exception as e:
        log.error(f"Tk no pudo arrancar: {e}", exc_info=True)
        return
    _root = root
    _ready.set()
    try:
        root.mainloop()
    except Exception as e:
        log.error(f"El bucle de Tk terminó con error: {e}", exc_info=True)
    finally:
        _ready.clear()
        _root = None
        log.warning("Hilo Tk terminado; se relanzará con el próximo popup")


def start():
    """Arranca el hilo Tk si no está corriendo, sin esperar a que esté listo."""
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _ready.clear()
            _thread = threading.Thread(target=_tk_main, daemon=True, name='TkThread')
            _thread.start()


def _ensure_running(timeout: float | None = None) -> tk.Tk:
    if timeout is None:
        timeout = START_TIMEOUT
    start()
    if not _ready.wait(timeout=timeout):
        raise TkNotReady(f"el hilo Tk no arrancó en {timeout:.0f} s")
    root = _root
    if root is None:
        raise TkNotReady("el hilo Tk se cerró")
    return root


def get_root() -> tk.Tk:
    return _ensure_running()


def run_in_tk(fn):
    """Ejecuta fn en el hilo Tk. Seguro desde cualquier hilo."""
    _ensure_running().after(0, fn)
