import datetime
import logging
import os
import sys
import time
import threading
from pathlib import Path

# ── Bootstrap logger ──────────────────────────────────────────────────────────
# Writes to %LOCALAPPDATA%\Noted\startup.log using only stdlib and
# absolute paths BEFORE any import that could fail.  With pythonw.exe every
# unhandled pre-logging exception is completely silent; this file captures it.
_BS_LOG = (
    Path(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')))
    / 'Noted' / 'startup.log'
)

def _bs(msg: str):
    try:
        _BS_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(_BS_LOG, 'a', encoding='utf-8') as f:
            f.write(f"{ts}  {msg}\n")
    except Exception:
        pass

_bs(f"=== START PID={os.getpid()} exe={sys.executable!r}")
_bs(f"  CWD={os.getcwd()!r}  __file__={__file__!r}")
_bs(f"  sys.path={sys.path!r}")
# ─────────────────────────────────────────────────────────────────────────────

_bs("importing config…")
# Import tardio a proposito: el log de arranque de arriba tiene que existir ANTES,
# para registrar justo los fallos de importacion de config.
from config import PROJECT_DIR, LOG_FILE, CLI_CONTROL_FILE  # noqa: E402
_bs(f"config OK  PROJECT_DIR={PROJECT_DIR!r}  LOG_FILE={LOG_FILE!r}")

# Logging
try:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(),
        ]
    )
    _bs("logging.basicConfig OK")
except Exception as _e:
    _bs(f"logging.basicConfig FAILED: {_e}")
    raise

log = logging.getLogger(__name__)

LOCK_FILE = PROJECT_DIR / '.lock'
# Named mutex for atomic single-instance guarantee (file lock alone has a TOCTOU race)
_MUTEX_NAME = 'Noted_SingleInstance'


def _check_single_instance() -> bool:
    # Step 1: Windows named mutex — atomic, survives process death automatically.
    try:
        import ctypes
        _SA = ctypes.c_void_p  # simplification; no SECURITY_ATTRIBUTES
        kernel32 = ctypes.windll.kernel32
        _mutex = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        _err   = kernel32.GetLastError()
        if _err == 183:  # ERROR_ALREADY_EXISTS
            log.error("Mutex indica que ya hay una instancia corriendo — saliendo")
            _bs("EXIT: mutex already exists")
            return False
        # Keep _mutex alive for the lifetime of the process by storing it globally
        globals()['_SINGLETON_MUTEX'] = _mutex
        _bs(f"mutex acquired (handle={_mutex})")
    except Exception as _e:
        _bs(f"mutex fallback — using file lock only: {_e}")

    # Step 2: file lock (fallback + watchdog-visible PID)
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            import psutil
            if psutil.pid_exists(pid):
                proc = psutil.Process(pid)
                if 'python' in proc.name().lower():
                    log.error(f"Ya hay una instancia corriendo (PID {pid})")
                    _bs(f"EXIT: lock file holds live PID {pid}")
                    return False
        except Exception:
            pass
    LOCK_FILE.write_text(str(os.getpid()))
    _bs(f"lock file written PID={os.getpid()}")
    return True


def _handle_cli_command(cmd, recorder, tray, get_recording_path):
    """Un comando de .cli_command: start, start-live, stop, o 'live <stem>'."""
    if cmd in ('start', 'start-live') and not recorder.is_recording:
        path = get_recording_path('cli')
        recorder.on_recording_stopped = tray._on_recording_done
        if recorder.start(path) is False:
            return      # ya se grababa (ver AudioRecorder.start)
        tray.set_recording(True, path, mode='live' if cmd == 'start-live' else 'background')
        log.info("Grabación iniciada por CLI")
    elif cmd == 'live' or cmd.startswith('live '):
        # 'live <stem>' lo manda el panel: solo vale para la grabacion en curso
        # (switch_to_live comprueba el stem).
        stem = cmd[5:].strip() or None
        if tray.switch_to_live(stem):
            log.info("Transcript en vivo activado por CLI/panel")
    elif cmd == 'stop' and recorder.is_recording:
        recorder.stop()
        tray.set_recording(False)
        log.info("Grabación detenida por CLI")


def _start_cli_listener(recorder, tray, get_recording_path):
    def _loop():
        while True:
            time.sleep(0.5)
            try:
                if CLI_CONTROL_FILE.exists():
                    # utf-8: 'live <stem>' lleva el nombre de la reunion (acentos).
                    cmd = CLI_CONTROL_FILE.read_text(encoding='utf-8', errors='replace').strip()
                    CLI_CONTROL_FILE.unlink()
                    _handle_cli_command(cmd, recorder, tray, get_recording_path)
            except Exception as e:
                log.warning(f"CLI listener error: {e}")

    threading.Thread(target=_loop, daemon=True, name='CLIListener').start()


_POPUP_GUARD_EXTRA_S = 10.0


def _popup_guard_s() -> float:
    """Cuanto espera la guarda del aviso: su timeout mas un margen."""
    from config import POPUP_TIMEOUT
    return POPUP_TIMEOUT + _POPUP_GUARD_EXTRA_S


# Pila de los hilos de Python que se crean despues de arrancar (por defecto 1 MB
# en Windows). Margen ante llamadas nativas profundas (COM, psutil) en hilos
# auxiliares: el 21/10 y el 22/10/2026 el daemon murio con 0xC00000FD
# (desbordamiento de pila) dentro de un escaneo de procesos del detector.
_THREAD_STACK = 16 * 1024 * 1024


def _enable_crash_log(log_dir: Path) -> Path | None:
    """Si el proceso muere por un fallo nativo (desbordamiento de pila, acceso
    invalido dentro de una DLL), con pythonw no queda NADA en noted.log: el
    proceso desaparece. faulthandler escribe entonces en crash.log la pila de
    cada hilo, que es lo que dice que parte de Noted lo provoco. Se llama al
    principio de main(), antes de crear hilos, para que el tamano de pila
    tambien les aplique. Devuelve la ruta del crash.log, o None si no se pudo."""
    import faulthandler
    path = None
    try:
        if faulthandler.is_enabled():
            # Ya hay uno (python -X faulthandler, o pytest): no se le quita la salida.
            raise RuntimeError("faulthandler ya activo")
        path = Path(log_dir) / 'crash.log'
        f = open(path, 'a', encoding='utf-8')
        f.write(f"\n=== arranque {datetime.datetime.now():%Y-%m-%d %H:%M:%S} PID={os.getpid()} ===\n")
        f.flush()
        faulthandler.enable(file=f, all_threads=True)
    except Exception as e:
        _bs(f"crash.log no disponible: {e}")
        path = None
    try:
        threading.stack_size(_THREAD_STACK)
    except Exception as e:
        _bs(f"stack_size no aplicado: {e}")
    return path


def main():
    _bs("main() entered")
    _enable_crash_log(Path(LOG_FILE).parent)
    if not _check_single_instance():
        sys.exit(1)

    try:
        _bs("importing storage…")
        from storage import ensure_directories, cleanup_old_recordings, cleanup_old_minutes
        _bs("importing AudioRecorder…")
        from audio_recorder import AudioRecorder
        _bs("importing TeamsCallDetector…")
        from teams_detector import TeamsCallDetector
        _bs("importing TrayApp…")
        from tray_app import TrayApp
        _bs("importing InboxWatcher…")
        from inbox_watcher import InboxWatcher
        from storage import get_recording_path
        _bs("all imports OK")

        ensure_directories()
        cleanup_old_recordings()
        cleanup_old_minutes()

        _bs("creating AudioRecorder…")
        recorder  = AudioRecorder()
        _bs("creating TeamsCallDetector…")
        detector  = TeamsCallDetector()
        _bs("creating TrayApp…")
        tray      = TrayApp(recorder, detector)
        _bs("TrayApp created")

        recorder.on_loopback_unavailable = tray.warn_loopback_unavailable
        recorder.on_write_error = tray.warn_disk_full
        recorder.on_write_recovered = tray.warn_disk_recovered
        recorder.on_save_failed = tray.warn_save_failed
        recorder.on_mic_unavailable = tray.warn_mic_unavailable
        # Worker de loopback ya cargado y a la espera: al entrar en la llamada
        # no hay que arrancar Python con la CPU saturada (ver arm_loopback).
        recorder.arm_loopback()

        _popup_active      = [False]   # guard para evitar popups múltiples
        _active_generation = [None]    # generación capturada en el último on_call_started
        _popup_lock        = threading.Lock()  # hace atómico el check-and-set de _popup_active
        _popup_seq         = [0]       # nº del último aviso mostrado (para la guarda)

        def on_call_started():
            from teams_detector import get_current_meeting_name
            meeting = get_current_meeting_name() or 'reunion'

            try:
                is_reconnect = tray.has_pending_session(meeting)
            except Exception:
                is_reconnect = False

            with _popup_lock:
                # Ya se graba ("Grabar ahora" al entrar, antes de que se detecte la
                # llamada): esa grabacion pasa a ser la de esta llamada y se para al
                # colgar, igual que ya toma su titulo (tray_app._note_teams_title).
                # Sin apuntarla, on_call_ended ignoraba el fin como generacion
                # obsoleta: el 03/11/2026 una grabacion siguio 6 min tras colgar.
                if recorder.is_recording:
                    _active_generation[0] = detector.call_generation
                if recorder.is_recording or _popup_active[0]:
                    return
                # call_declined bloquea reuniones nuevas, no reconexiones a la misma
                if not is_reconnect and detector.call_declined:
                    return
                _popup_active[0]      = True
                _active_generation[0] = detector.call_generation
                _popup_seq[0] += 1
                _my_popup = _popup_seq[0]

            # Guarda: el timeout del aviso vive dentro del hilo de Tk. Si show()
            # falla alli o el hilo se cuelga, el flag se quedaba True y
            # on_call_started retornaba siempre (sin volver a ofrecer grabar hasta
            # reiniciar). Solo suelta el flag de ESTE aviso, no el de uno posterior.
            def _release_popup_guard(seq=_my_popup):
                with _popup_lock:
                    if _popup_active[0] and _popup_seq[0] == seq:
                        _popup_active[0] = False
                        log.warning("Aviso de grabación sin respuesta: se libera para "
                                    "poder ofrecer grabar de nuevo")

            _guard = threading.Timer(_popup_guard_s(), _release_popup_guard)
            _guard.daemon = True
            _guard.start()

            try:
                from popup import RecordingPopup
                try:
                    from config import get_ui_language
                    _lang = get_ui_language()
                except Exception:
                    _lang = 'es'

                def _start_recording(continuing, mode='background'):
                    _popup_active[0] = False
                    if continuing:
                        tray.request_continuation()
                    path = get_recording_path(meeting)
                    try:
                        started = recorder.start(path)
                    except Exception as e:
                        log.error(f"No se pudo iniciar la grabación: {e}")
                        tray.warn_mic_unavailable(str(e))
                        # La reconexion no existira: la sesion anterior no espera nada.
                        if continuing:
                            tray.cancel_continuation()
                        return
                    if started is False:
                        # Ya se grababa ("Grabar ahora" mientras salia este aviso).
                        # El 28/10/2026 se trato path como la grabacion en curso: el
                        # panel mostro otra reunion y se lanzo un segundo transcript
                        # en vivo de un audio que no existia. El nombre de la reunion
                        # lo recoge la grabacion en curso (tray._note_teams_title).
                        log.info(f"Ya se estaba grabando: no se abre otra grabación para '{meeting}'")
                        return
                    tray.set_recording(True, path, mode=mode)
                    log.info(f"{'Continuando (reconexión)' if continuing else 'Grabación iniciada'}"
                             f"{' (en vivo)' if mode == 'live' else ''}: {path.name}")
                    threading.Thread(target=tray.warn_if_mic_muted, daemon=True,
                                     name='MicMuteCheck').start()
                    def _send_notice():
                        try:
                            import teams_chat as _tc
                            _tc.send_recording_notice()
                        except Exception as _e:
                            log.warning(f"TeamsChatNotice: {_e}")
                    threading.Thread(target=_send_notice, daemon=True, name='TeamsChatNotice').start()

                if is_reconnect:
                    # "Seguir" continua en el modo de la parte anterior.
                    prev_mode = tray.last_recording_mode or 'background'

                    def on_yes():
                        _start_recording(continuing=True, mode=prev_mode)

                    def on_live():
                        _start_recording(continuing=True, mode='live')

                    def on_no():
                        _popup_active[0] = False
                        tray.finalize_pending_now()
                        log.info("Reconexión: usuario NO continúa — se cierra la reunión anterior")

                    if _lang == 'en':
                        RecordingPopup(on_yes=on_yes, on_no=on_no, on_alt=on_live,
                                       title='Same meeting detected',
                                       subtitle='Keep recording and merge with the previous one?',
                                       yes_label='  Keep  ', alt_label='Keep + live',
                                       no_label='No, close').show()
                    else:
                        RecordingPopup(on_yes=on_yes, on_no=on_no, on_alt=on_live,
                                       title='Misma reunión detectada',
                                       subtitle='¿Seguir grabando y unirla a la anterior?',
                                       yes_label='  Seguir  ', alt_label='Seguir + en vivo',
                                       no_label='No, cerrar').show()
                else:
                    def on_yes():
                        _start_recording(continuing=False)

                    def on_live():
                        _start_recording(continuing=False, mode='live')

                    def on_no():
                        _popup_active[0] = False
                        detector.set_declined()
                        log.info("Usuario rechazó grabar")

                    RecordingPopup(on_yes=on_yes, on_no=on_no, on_alt=on_live).show()

            except Exception as e:
                _popup_active[0] = False
                log.error(f"Error mostrando popup: {e}")

        def on_call_ended():
            if detector.call_generation != _active_generation[0]:
                log.info("on_call_ended ignorado: generación obsoleta")
                return
            if recorder.is_recording:
                recorder.stop()
                tray.set_recording(False)
                log.info("Grabación detenida: llamada Teams finalizada")

        detector.on_call_started     = on_call_started
        detector.on_call_ended       = on_call_ended
        detector.is_active_recording = lambda: recorder.is_recording

        recorder.on_recording_stopped = tray._on_recording_done

        _bs("starting detector…")
        detector.start()
        _bs("starting InboxWatcher…")
        InboxWatcher(on_wav_ready=tray._on_recording_done).start()
        _start_cli_listener(recorder, tray, get_recording_path)

        log.info("Noted iniciado")
        _bs("calling tray.start() — main thread blocks here")
        tray.start()  # bloquea el hilo principal (requerido por pystray en Windows)
        _bs("tray.start() returned — process will exit")
        recorder.disarm_loopback()

    except Exception as e:
        _bs(f"EXCEPTION in main(): {type(e).__name__}: {e}")
        log.error(f"Error fatal: {e}", exc_info=True)
    finally:
        _bs(f"finally block — cleaning up PID={os.getpid()}")
        try:
            LOCK_FILE.unlink()
        except Exception:
            pass


if __name__ == '__main__':
    main()
