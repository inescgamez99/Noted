"""Que Windows no frene a los procesos de Noted que transcriben.

Medido el 20/10/2026 en una reunion real de 69 min: el daemon (12 hilos de
Whisper, prioridad normal) usaba 2,4 de 14 nucleos al transcribir tras colgar;
el proceso del transcript en vivo, 0,7 nucleos para 4 hilos. Windows trata a un
proceso sin ventana como de segundo plano y lo pone en modo de eficiencia
(EcoQoS): nucleos lentos y frecuencia baja. Al sacarlos de ese modo: el daemon
paso a 7,7 nucleos y el en vivo de 247 s de retraso a ir al dia en 3 minutos.
Probablemente la causa de que el 19/10 una reunion de 48 min tardara 84 en
transcribirse.

API documentada: SetProcessInformation(ProcessPowerThrottling) con
ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED y StateMask = 0. No toca
la prioridad: un proceso con prioridad minima sigue cediendo la CPU a Teams.

La prioridad la sube aparte el daemon mientras transcribe una reunion ya
terminada (transcribing / set_recording, abajo).
"""
import logging
import os
import threading
from contextlib import contextmanager

log = logging.getLogger(__name__)

_done = False


def opt_out_of_efficiency_mode() -> bool:
    """Saca el proceso actual del modo de eficiencia. Una vez por proceso;
    nunca lanza. False fuera de Windows o si la llamada falla."""
    global _done
    if _done:
        return True
    if os.name != 'nt':
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
            _fields_ = [('Version', wintypes.ULONG), ('ControlMask', wintypes.ULONG),
                        ('StateMask', wintypes.ULONG)]

        state = PROCESS_POWER_THROTTLING_STATE(1, 0x1, 0)   # version 1, EXECUTION_SPEED, apagado
        # El pseudo-handle del proceso actual, (HANDLE)-1, como HANDLE de 64 bits.
        # Antes se pasaba k32.GetCurrentProcess(): sin restype, ctypes lo recorta
        # a un int de 32 bits y Windows devuelve ERROR_INVALID_HANDLE (23/10/2026:
        # el arreglo del 20/10 nunca llego a funcionar en produccion).
        current_process = wintypes.HANDLE(-1)
        process_power_throttling = 4                        # PROCESS_INFORMATION_CLASS
        _done = bool(ctypes.windll.kernel32.SetProcessInformation(
            current_process, process_power_throttling, ctypes.byref(state), ctypes.sizeof(state)))
        return _done
    except Exception:
        return False


# ── Prioridad al transcribir una reunion ya terminada ────────────────────────
#
# Medido con el equipo cargado (agentes de seguridad ~20 %, otras sesiones,
# tests): con prioridad Normal la transcripcion tras colgar no conseguia CPU. El
# 23/11/2026, 0,6 nucleos y 8 hilos esperando, la reunion atascada en el 94 %;
# con AboveNormal, 4,5 nucleos y ninguno esperando en 8 s. El 25/11, 0,9 -> 5,3.
#
# La prioridad es del proceso entero, y el daemon es tambien el que graba. Si
# empieza otra reunion mientras se transcribe la anterior (sin el modo en vivo,
# la transcripcion sigue), 8 hilos de Whisper por encima de Teams estropearian la
# llamada. Por eso una sola regla, reevaluada en cada cambio: AboveNormal solo si
# hay una transcripcion tras colgar, no se graba Y no hay una llamada de Teams.
# La llamada cuenta aparte de la grabacion: la grabacion empieza despues del aviso
# (hasta 30 s) y puede no empezar si se dice que no, y en ese rato Teams prepara
# el audio y el video. Nunca High: con AboveNormal basta, y High puede frenar el
# raton y la propia ventana de Teams.

_WINDOWS = os.name == 'nt'
_lock = threading.Lock()
_transcribing = 0          # transcripciones tras colgar en curso
_recording = False
_in_call = False           # una llamada de Teams detectada (teams_detector)
_previous = None           # la prioridad de antes de subirla; None = no esta subida


@contextmanager
def transcribing():
    """Mientras dura el bloque, el daemon transcribe una reunion ya terminada.
    Al salir (tambien por un fallo, una cancelacion o un aplazamiento) se
    reevalua y, si no queda ninguna, la prioridad vuelve a la de antes."""
    global _transcribing
    with _lock:
        _transcribing += 1
        _apply()
    try:
        yield
    finally:
        with _lock:
            _transcribing -= 1
            _apply()


def set_recording(active: bool) -> None:
    """La grabacion empieza (True) o termina (False). Mientras se graba no se
    sube, y si estaba subida vuelve a la prioridad de antes."""
    global _recording
    with _lock:
        _recording = bool(active)
        _apply()


def set_in_call(active: bool) -> None:
    """Empieza (True) o termina (False) una llamada de Teams, se grabe o no. Igual
    que grabando: no se sube, y si estaba subida vuelve a la de antes."""
    global _in_call
    with _lock:
        _in_call = bool(active)
        _apply()


def _apply() -> None:
    """Con _lock tomado. Solo toca la prioridad al subirla o al devolverla: un
    proceso que nunca la subio no la cambia (los tests corren en BelowNormal y no
    deben acabar en Normal por grabar). Nunca lanza."""
    global _previous
    if not _WINDOWS:
        return
    want_up = _transcribing > 0 and not _recording and not _in_call
    try:
        import psutil
        proc = psutil.Process()
        if want_up and _previous is None:
            before = proc.nice()
            proc.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
            _previous = before
            log.info("Prioridad AboveNormal: transcribiendo una reunion ya terminada")
        elif not want_up and _previous is not None:
            before, _previous = _previous, None
            proc.nice(before)
            motivo = ('se graba otra reunion' if _recording else
                      'llamada de Teams' if _in_call else 'transcripcion terminada')
            log.info("Prioridad de vuelta a la de antes (%s)", motivo)
    except Exception as e:
        log.warning(f"No se pudo cambiar la prioridad: {e}")
