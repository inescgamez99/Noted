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
from config import PROJECT_DIR, LOG_FILE, CLI_CONTROL_FILE
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


def _start_cli_listener(recorder, tray, get_recording_path):
    def _loop():
        while True:
            time.sleep(0.5)
            try:
                if CLI_CONTROL_FILE.exists():
                    cmd = CLI_CONTROL_FILE.read_text().strip()
                    CLI_CONTROL_FILE.unlink()
                    if cmd == 'start' and not recorder.is_recording:
                        path = get_recording_path('cli')
                        recorder.on_recording_stopped = tray._on_recording_done
                        recorder.start(path)
                        tray.set_recording(True, path)
                        log.info("Grabación iniciada por CLI")
                    elif cmd == 'stop' and recorder.is_recording:
                        recorder.stop()
                        tray.set_recording(False)
                        log.info("Grabación detenida por CLI")
            except Exception as e:
                log.warning(f"CLI listener error: {e}")

    threading.Thread(target=_loop, daemon=True, name='CLIListener').start()


def main():
    _bs("main() entered")
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

        _popup_active      = [False]   # guard para evitar popups múltiples
        _active_generation = [None]    # generación capturada en el último on_call_started
        _popup_lock        = threading.Lock()  # hace atómico el check-and-set de _popup_active

        def on_call_started():
            from teams_detector import get_current_meeting_name
            meeting = get_current_meeting_name() or 'reunion'

            try:
                is_reconnect = tray.has_pending_session(meeting)
            except Exception:
                is_reconnect = False

            with _popup_lock:
                if recorder.is_recording or _popup_active[0]:
                    return
                # call_declined bloquea reuniones nuevas, no reconexiones a la misma
                if not is_reconnect and detector.call_declined:
                    return
                _popup_active[0]      = True
                _active_generation[0] = detector.call_generation

            try:
                from popup import RecordingPopup
                try:
                    from config import get_ui_language
                    _lang = get_ui_language()
                except Exception:
                    _lang = 'es'

                def _start_recording(continuing):
                    _popup_active[0] = False
                    if continuing:
                        tray.request_continuation()
                    path = get_recording_path(meeting)
                    try:
                        recorder.start(path)
                    except Exception as e:
                        log.error(f"No se pudo iniciar la grabación: {e}")
                        tray.warn_mic_unavailable(str(e))
                        return
                    tray.set_recording(True, path)
                    log.info(f"{'Continuando (reconexión)' if continuing else 'Grabación iniciada'}: {path.name}")
                    def _send_notice():
                        try:
                            import teams_chat as _tc
                            _tc.send_recording_notice()
                        except Exception as _e:
                            log.warning(f"TeamsChatNotice: {_e}")
                    threading.Thread(target=_send_notice, daemon=True, name='TeamsChatNotice').start()

                if is_reconnect:
                    def on_yes():
                        _start_recording(continuing=True)

                    def on_no():
                        _popup_active[0] = False
                        tray.finalize_pending_now()
                        log.info("Reconexión: usuario NO continúa — se cierra la reunión anterior")

                    if _lang == 'en':
                        RecordingPopup(on_yes=on_yes, on_no=on_no,
                                       title='Same meeting detected',
                                       subtitle='Keep recording and merge with the previous one?',
                                       yes_label='  Keep  ', no_label='No, close').show()
                    else:
                        RecordingPopup(on_yes=on_yes, on_no=on_no,
                                       title='Misma reunión detectada',
                                       subtitle='¿Seguir grabando y unirla a la anterior?',
                                       yes_label='  Seguir  ', no_label='No, cerrar').show()
                else:
                    def on_yes():
                        _start_recording(continuing=False)

                    def on_no():
                        _popup_active[0] = False
                        detector.set_declined()
                        log.info("Usuario rechazó grabar")

                    RecordingPopup(on_yes=on_yes, on_no=on_no).show()

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
