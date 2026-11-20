"""Prioridad de los hilos que no pueden esperar.

Mientras se graba una reunion, el mismo proceso puede estar transcribiendo otra
con varios hilos de Whisper, y Teams con video compite por la CPU. Lo que no
puede retrasarse (escribir la grabacion a disco, leer el audio de los demas,
detectar llamadas) sube la prioridad de su propio hilo dos
niveles dentro de la clase del proceso. No cambia la prioridad del proceso ni
la de los demas hilos.
"""
import os

THREAD_PRIORITY_HIGHEST = 2


def _k32():
    """kernel32 propio y con tipos declarados. Sin restype/argtypes, ctypes pasa
    el pseudo-handle del hilo (-2) como int de 32 bits, Windows recibe otro valor
    y la llamada falla sin avisar. Instancia aparte para no tocar las firmas de
    ctypes.windll.kernel32 que usa el resto de la app."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.GetCurrentThread.restype = wintypes.HANDLE
    k.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
    k.SetThreadPriority.restype = wintypes.BOOL
    k.GetThreadPriority.argtypes = [wintypes.HANDLE]
    k.GetThreadPriority.restype = ctypes.c_int
    return k


def boost_current_thread() -> bool:
    """Sube el hilo actual a THREAD_PRIORITY_HIGHEST. Nunca lanza; False si no
    se pudo (o fuera de Windows)."""
    if os.name != 'nt':
        return False
    try:
        k = _k32()
        return bool(k.SetThreadPriority(k.GetCurrentThread(), THREAD_PRIORITY_HIGHEST))
    except Exception:
        return False


def current_thread_priority() -> int | None:
    """Prioridad del hilo actual (para el log y los tests), o None."""
    if os.name != 'nt':
        return None
    try:
        k = _k32()
        return k.GetThreadPriority(k.GetCurrentThread())
    except Exception:
        return None
