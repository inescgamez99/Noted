"""
Módulo de hilo Tkinter persistente.
Un único tk.Tk() root corre en un hilo de fondo dedicado.
Todos los popups y diálogos usan Toplevel sobre ese root vía after().
"""
import threading
import time
import tkinter as tk

_root: tk.Tk | None = None
_ready = threading.Event()
_alive = threading.Event()
_lock  = threading.Lock()


def _heartbeat():
    global _root
    _alive.set()
    if _root is not None:
        _root.after(1000, _heartbeat)


def _tk_main():
    global _root
    _root = tk.Tk()
    _root.withdraw()
    _ready.set()
    _root.after(1000, _heartbeat)
    try:
        _root.mainloop()
    finally:
        _alive.clear()
        _ready.clear()
        _root = None


def _ensure_running():
    with _lock:
        if _root is None or not _ready.is_set() or not _alive.is_set():
            t = threading.Thread(target=_tk_main, daemon=True, name='TkThread')
            t.start()
            _ready.wait(timeout=5)


def get_root() -> tk.Tk:
    _ensure_running()
    return _root


def run_in_tk(fn):
    """Ejecuta fn en el hilo Tk. Seguro desde cualquier hilo."""
    _ensure_running()
    _root.after(0, fn)
