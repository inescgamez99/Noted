import glob
import json
import logging
import queue
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import mic_status
from config import MINUTES_DIR, RECORDINGS_DIR, PROJECT_DIR, get_ui_language
from transcriber import DEFER as _DEFER
from transcriber import TranscriptionCancelled as _Cancelled
from transcriber import TranscriptionDeferred as _Deferred

# Trabajos descartados desde la ventana web, que corre en otro proceso.
_CANCEL_FILE = PROJECT_DIR / '.cancelled_jobs.txt'


def _slug_title(slug: str) -> str:
    """Titulo legible de la parte del nombre de fichero tras la fecha. Solo se
    pone en mayuscula la primera letra: .title() minusculizaba el resto y
    convertia IBP en Ibp y RACI en Raci."""
    t = slug.replace('_', ' ').strip()
    return t[:1].upper() + t[1:]


def _read_cancel_signals() -> set[str]:
    try:
        if _CANCEL_FILE.exists():
            return {ln.strip() for ln in _CANCEL_FILE.read_text(encoding='utf-8').splitlines()
                    if ln.strip()}
    except Exception:
        pass
    return set()


def _append_cancel_signal(stem: str) -> None:
    try:
        _CANCEL_FILE.write_text('\n'.join(sorted(_read_cancel_signals() | {stem})),
                                encoding='utf-8')
    except Exception as e:
        logging.getLogger(__name__).warning(f"_append_cancel_signal: {e}")


def _remove_cancel_signal(stem: str) -> None:
    try:
        rest = _read_cancel_signals() - {stem}
        if rest:
            _CANCEL_FILE.write_text('\n'.join(sorted(rest)), encoding='utf-8')
        elif _CANCEL_FILE.exists():
            _CANCEL_FILE.unlink()
    except Exception as e:
        logging.getLogger(__name__).warning(f"_remove_cancel_signal: {e}")

_STR = {
    'es': dict(
        record_now='Grabar ahora', stop='Parar grabacion',
        record_now_live='Grabar ahora (transcript en vivo)',
        switch_live='Activar transcript en vivo',
        add_context='Añadir contexto a grabacion',
        cancel_recording='Cancelar grabacion (sin guardar)',
        view_minutes='Abrir la aplicacion',
        quit='Salir',
        recordings_queued='{n} grabaciones en cola',
        recordings_pending='{n} grabacion(es) pendiente(s) de procesar',
        action_items='Generando acciones...',
        ready='Minutas y acciones listas',
        summarizing='Resumiendo la reunión...',
        project_step='Archivando en su proyecto...',
        ready_minutes='Minutas listas',
        ready_brief='Transcript listo',
        transcription_failed='No se pudo transcribir la grabación. Revisa el log para más detalles.',
        no_speech='La grabación no contiene voz: no se ha generado transcripción.',
        minutes_failed='No se pudieron generar las minutas. Revisa el log para más detalles.',
        minutes_pending_session=('Claude no tiene la sesión iniciada. La reunión queda guardada con su título y su '
                                 'transcript, y las minutas se generarán solas en cuanto ejecutes «claude auth login» '
                                 'en una terminal.'),
        minutes_pending_missing=('No se encuentra Claude Code. La reunión queda guardada con su título y su '
                                 'transcript, y las minutas se generarán solas cuando vuelva a estar instalado.'),
        minutes_pending=('Claude no ha podido generar las minutas. La reunión queda guardada con su título y su '
                         'transcript, y Noted lo reintentará solo.'),
        untitled='Reunión {time}',
        mic_only='Grabando SOLO tu microfono: no se captura el audio de los demas. Si usas auriculares, sus voces no quedaran en la grabacion.',
        mic_unavailable='No se pudo acceder al micrófono. Comprueba que no lo usa otra app.',
        mic_menu_muted='Micrófono: {name} — SILENCIADO (clic para activar)',
        mic_menu_active='Micrófono: {name} — activo (clic para silenciar)',
        mic_tip_muted='micro SILENCIADO',
        mic_muted_warning='Tu micrófono ({name}) está silenciado en Windows: no se grabará tu voz. Actívalo desde el menú de Noted.',
        disk_full='Disco lleno: Noted guarda el audio en memoria y lo escribirá en cuanto haya espacio. Libera espacio.',
        disk_full_reserve='Disco lleno: Noted ha liberado su reserva de {released:.0f} GB y guarda el audio en memoria hasta que haya espacio. Libera espacio.',
        disk_recovered='Hay espacio otra vez: el audio está guardado completo.',
        deferred='En pausa: se está grabando otra reunión y falta disco o memoria. Sigue al colgar.',
        deferred_live='En pausa: la reunión en curso se está transcribiendo en vivo. Sigue al colgar.',
        write_failed='Noted no puede escribir el audio (¿un archivo bloqueado?). Lo guarda en memoria y lo reintenta.',
        save_failed='No se pudo guardar la grabación. El audio se conserva en recordings (archivos .part): revisa el log.',
        disk_low='Queda poco espacio en disco ({gb:.1f} GB). Noted sigue grabando.',
        disk_critical='Disco casi lleno ({gb:.1f} GB). Libera espacio: mientras tanto Noted guarda el audio en memoria.',
        disk_critical_reserve='Disco casi lleno ({gb:.1f} GB): Noted ha liberado su reserva de {released:.0f} GB para seguir grabando. Libera espacio.',
        cancel_job='Descartar esta reunion (papelera)',
        job_cancelled='Reunion descartada: el audio y lo generado se han movido a la papelera.',
        ctx_title='Contexto / objetivo de la reunion',
        ctx_subtitle='Se usara al generar las minutas',
        ctx_save='Guardar',
        ctx_cancel='Cancelar',
    ),
    'en': dict(
        record_now='Record now', stop='Stop recording',
        record_now_live='Record now (live transcript)',
        switch_live='Turn on live transcript',
        add_context='Add context to recording',
        cancel_recording='Cancel recording (discard)',
        view_minutes='Open the app',
        quit='Quit',
        recordings_queued='{n} recordings in queue',
        recordings_pending='{n} recording(s) pending processing',
        action_items='Generating action items...',
        ready='Meeting minutes & action items ready',
        summarizing='Summarizing the meeting...',
        project_step='Filing it in its project...',
        ready_minutes='Minutes ready',
        ready_brief='Transcript ready',
        transcription_failed='Transcription failed. Check the log for details.',
        no_speech='No speech in the recording: no transcript was produced.',
        minutes_failed='Minutes generation failed. Check the log for details.',
        minutes_pending_session=('Claude is not signed in. The meeting is saved with its title and transcript, and '
                                 'the minutes will be generated on their own as soon as you run «claude auth login» '
                                 'in a terminal.'),
        minutes_pending_missing=('Claude Code was not found. The meeting is saved with its title and transcript, and '
                                 'the minutes will be generated on their own once it is installed again.'),
        minutes_pending=('Claude could not generate the minutes. The meeting is saved with its title and '
                         'transcript, and Noted will retry on its own.'),
        untitled='Meeting {time}',
        mic_only='Recording your microphone ONLY — system audio is not being captured. If you are on a headset, the others will not be in the recording.',
        mic_unavailable='Could not access the microphone. Check that no other app is using it.',
        mic_menu_muted='Microphone: {name} — MUTED (click to unmute)',
        mic_menu_active='Microphone: {name} — on (click to mute)',
        mic_tip_muted='mic MUTED',
        mic_muted_warning='Your microphone ({name}) is muted in Windows: your voice will not be recorded. Unmute it from the Noted menu.',
        disk_full='Disk full: Noted keeps the audio in memory and will write it as soon as there is space. Free up some space.',
        disk_full_reserve='Disk full: Noted released its {released:.0f} GB reserve and keeps the audio in memory until there is space. Free up some space.',
        disk_recovered='Disk space is back: the audio is fully saved.',
        deferred='Paused: another meeting is recording and disk or memory is low. It continues after the call.',
        deferred_live='Paused: the current meeting is being transcribed live. It continues after the call.',
        write_failed='Noted cannot write the audio (a locked file?). It keeps it in memory and retries.',
        save_failed='The recording could not be saved. The audio is kept in recordings (.part files): check the log.',
        disk_low='Low disk space ({gb:.1f} GB left). Noted keeps recording.',
        disk_critical='Disk almost full ({gb:.1f} GB). Free up some space: meanwhile Noted keeps the audio in memory.',
        disk_critical_reserve='Disk almost full ({gb:.1f} GB): Noted released its {released:.0f} GB reserve to keep recording. Free up some space.',
        cancel_job='Discard this meeting (bin)',
        job_cancelled='Meeting discarded: the audio and generated files were moved to the bin.',
        ctx_title='Meeting context / goal',
        ctx_subtitle='Will be used when generating the minutes',
        ctx_save='Save',
        ctx_cancel='Cancel',
    ),
}

log = logging.getLogger(__name__)

# Cada cuanto se mira el disco mientras se graba (ver disk_guard).
DISK_CHECK_EVERY_S = 20.0
# Una transcripcion apartada por una grabacion (ver _defer_reason) mira cada
# tanto si ya puede seguir.
DEFER_RECHECK_S = 10.0
# Con el disco al limite, escribir puede fallar y recuperarse cada pocos segundos:
# como mucho un aviso de cada tipo en este intervalo.
DISK_TOAST_EVERY_S = 120.0
# Minutas que fallaron (marca 'pending', ver _retry_pending_minutes): cada cuanto
# se mira si Claude ya responde, y cuantas veces se reintenta cada reunion por
# arranque del daemon.
RETRY_PENDING_FIRST_S = 120.0
RETRY_PENDING_EVERY_S = 900.0
RETRY_PENDING_MAX = 3


def _make_icon(color: str):
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 4, 60, 60], fill=color)
    return img


_ICON_IDLE       = _make_icon('#6c7086')
_ICON_RECORDING  = _make_icon('#f38ba8')
_ICON_PROCESSING = _make_icon('#f9e2af')

_MIC_POLL_S = 2
_BADGED: dict[int, object] = {}


def _with_mic_badge(img):
    """El icono con una marca roja de micro silenciado en la esquina inferior.

    Se cachea por imagen base: el icono se repinta en cada cambio de estado y
    dibujarlo de nuevo cada vez no aporta nada.
    """
    cached = _BADGED.get(id(img))
    if cached is not None:
        return cached
    from PIL import ImageDraw
    out = img.copy()
    d = ImageDraw.Draw(out)
    d.ellipse([32, 32, 63, 63], fill='#d20f39', outline='#ffffff', width=3)
    d.line([39, 56, 56, 39], fill='#ffffff', width=5)
    _BADGED[id(img)] = out
    return out


class TrayApp:
    def __init__(self, recorder, detector):
        self._recorder = recorder
        self._detector = detector
        self._icon = None
        self._pipeline_queue: queue.Queue = queue.Queue()
        self._pipeline_queued: list = []
        self._recording_start: float | None = None
        self._recording_path: Path | None = None
        self._ticker_stop = threading.Event()
        self._processing_msg: str = ''
        self._current_job: dict = {}

        self._session: dict | None = None
        self._session_lock = threading.RLock()
        self._MERGE_GRACE = 90

        # Trabajos que el usuario ha descartado. Cubre el hueco entre parar la
        # grabación y tener la minuta: ahí se gastan los minutos de Whisper, los
        # tokens de Claude y el WAV en disco, y antes no había forma de abortar.
        # Se respalda en un fichero porque quien cancela es la ventana web, que
        # corre en otro proceso (mismo patrón que .cli_command).
        self._cancelled: set[str] = set()
        self._cancel_lock = threading.Lock()

        # Microfono por defecto y si esta silenciado (mic_status). El icono base
        # se guarda para poder repintarlo con o sin la marca roja cuando cambia.
        self._mic_state: mic_status.MicState | None = None
        self._icon_base = (_ICON_IDLE, 'Noted')

        # Transcripcion en vivo (live_transcriber.py): stem -> (Popen, ruta del WAV)
        # del proceso que transcribe la grabacion en curso. Solo vive mientras se
        # graba; al parar se mata y el pipeline cierra el transcript.
        self._live: dict = {}
        self._live_lock = threading.Lock()
        self._live_spawning: set = set()
        # Modo de la grabacion en curso: 'background' (transcribe durante la
        # reunion con el modelo de siempre, para ir ganando tiempo) o 'live'
        # ('small', para verlo ya). Y el arbitro de Whisper: mientras el pipeline
        # lo usa, el proceso de fondo cede el modelo (nunca dos grandes a la vez);
        # en modo live es el pipeline el que se pausa.
        self._rec_mode: str | None = None
        self.last_recording_mode: str | None = None
        self._whisper_busy = False
        self._whisper_lock = threading.Lock()
        # El .ctl lo escriben tres hilos (grabacion, pipeline, panel): siempre
        # entero y desde el estado actual, de uno en uno. _yield_seq numera cada
        # peticion de ceder, para no fiarse de un 'yielded' de la anterior.
        self._ctl_lock = threading.RLock()
        self._yield_seq = 0
        # stem -> reintentos de sus minutas en este arranque (_retry_pending_minutes).
        self._pending_retries: dict[str, int] = {}

        threading.Thread(target=self._pipeline_loop, daemon=True, name='PipelineWorker').start()
        threading.Thread(target=self._mic_poller, daemon=True, name='MicStatusPoller').start()
        threading.Thread(target=self._recover_pending, daemon=True, name='PipelineRecovery').start()
        threading.Thread(target=self._retry_pending_loop, daemon=True, name='PendingMinutesRetry').start()
        threading.Thread(target=self._notification_poller, daemon=True, name='NotificationPoller').start()
        if (PROJECT_DIR / 'email_brain_updater.py').exists():
            self._start_daily_brain_scan()

    def start(self):
        import pystray
        s = _STR.get(get_ui_language(), _STR['en'])
        self._icon = pystray.Icon(
            'Noted',
            _ICON_IDLE,
            'Noted',
            menu=pystray.Menu(
                pystray.MenuItem(lambda _: s['stop'] if self._recorder.is_recording else s['record_now'],
                                 self._toggle_recording),
                pystray.MenuItem(s['record_now_live'], self._record_now_live,
                                 visible=lambda _: not self._recorder.is_recording),
                pystray.MenuItem(s['switch_live'], self._switch_live_from_menu,
                                 visible=lambda _: bool(self._recorder.is_recording
                                                        and self._rec_mode != 'live')),
                pystray.MenuItem(s['add_context'],
                                 self._add_context,
                                 visible=lambda _: bool(self._recorder.is_recording)),
                pystray.MenuItem(lambda _: (s['cancel_recording'] if self._recorder.is_recording
                                            else s['cancel_job']),
                                 self._cancel_recording,
                                 visible=lambda _: bool(self._recorder.is_recording
                                                        or self._active_job_stem())),
                pystray.MenuItem(lambda _: self._mic_menu_label(),
                                 self._toggle_mic_mute,
                                 visible=lambda _: getattr(self, '_mic_state', None) is not None),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(s['view_minutes'], self._open_actions_ui, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(s['quit'], self._quit),
            )
        )
        self._icon.run()

    def _write_status(self):
        import json as _json
        import re as _re
        from config import PROJECT_DIR
        jobs = []
        # Una sola lectura: set_recording(False) la pone a None desde otro hilo, y
        # leerla dos veces podia dar AttributeError a mitad de esta funcion.
        rec_path = self._recording_path
        if self._recording_start:
            elapsed = int(time.time() - self._recording_start)
            m, s = divmod(elapsed, 60)
            _rm = _re.match(r'\d{4}-\d{2}-\d{2}_(\d{2})-(\d{2})(?:_(.+))?', rec_path.stem) if rec_path else None
            _rtitle = _slug_title(_rm.group(3)) if (_rm and _rm.group(3)) else None
            _rtime  = f"{_rm.group(1)}:{_rm.group(2)}" if _rm else None
            jobs.append({'stage': 'recording', 'label': f'Recording {m:02d}:{s:02d}', 'elapsed': elapsed, 'pct': None,
                         'title': _rtitle, 'time': _rtime, 'step': 0, 'total_steps': 3, 'step_label': 'Grabando'})
            # 'live_stem' y no 'stem': 'stem' es lo que habilita el boton de
            # descartar, y una grabacion en curso no se descarta desde ahi.
            if rec_path:
                jobs[-1]['mode'] = self._rec_mode
                jobs[-1]['rec_stem'] = rec_path.stem
                if rec_path.stem in self._live:
                    jobs[-1]['live_stem'] = rec_path.stem
                    # El estado del proceso de la reunion (p. ej. esperando
                    # memoria), para que el panel diga por que no avanza.
                    try:
                        import live_transcriber
                        jobs[-1]['live_state'] = live_transcriber.read_status(rec_path).get('state')
                    except Exception:
                        pass
        if self._processing_msg:
            pct_m = _re.search(r'(\d+)%', self._processing_msg)
            job = {'stage': 'processing', 'label': self._processing_msg, 'pct': int(pct_m.group(1)) if pct_m else None}
            job.update(self._current_job)
            jobs.append(job)
        for name in self._pipeline_queued:
            jobs.append({'stage': 'queued', 'label': name, 'pct': 0, 'stem': name})
        try:
            (PROJECT_DIR / '.pipeline_status.json').write_text(_json.dumps({'jobs': jobs}), encoding='utf-8')
        except Exception:
            pass

    def set_recording(self, active: bool, path: Path = None, mode: str = None):
        if active:
            self._rec_mode = mode or 'background'
            self.last_recording_mode = self._rec_mode
            self._recording_path = path
            self._recording_start = time.time()
            import disk_guard
            self._disk_levels = disk_guard.Levels()
            self._disk_next_check = 0.0
            self._ticker_stop.clear()
            t = threading.Thread(target=self._ticker, daemon=True, name='TrayTicker')
            t.start()
            self._set_icon(_ICON_RECORDING, 'Noted - Recording 00:00')
            self._write_live_ctl(path)
            self._start_live(path, self._rec_mode)
        else:
            self._recording_path = None
            self._rec_mode = None
            self._stop_live()
            self._ticker_stop.set()
            self._recording_start = None
            if self._processing_msg:
                self._set_icon(_ICON_PROCESSING, f'Noted - {self._processing_msg}')
            else:
                self._set_icon(_ICON_IDLE, 'Noted')
        self._write_status()
        try:
            if self._icon:
                self._icon.update_menu()
        except Exception:
            pass

    def _start_live(self, path: Path, mode: str = 'background', mid_meeting: bool = False):
        """Lanza la transcripcion durante la reunion (live_transcriber.py).

        En un hilo y bajo try: si algo falla, la grabacion sigue igual y el
        transcript se hace al terminar, como siempre.
        """
        try:
            import live_transcriber
            # Se resuelven aqui y no dentro del hilo: el hilo arranca despues, y
            # en los tests el parche de spawn podria haberse deshecho ya.
            spawn, kill = live_transcriber.spawn, live_transcriber.kill
        except Exception as e:
            log.warning(f"La transcripcion durante la reunion no arranco: {e}")
            return
        with self._live_lock:
            if path.stem in self._live or path.stem in self._live_spawning:
                return
            self._live_spawning.add(path.stem)

        def run():
            try:
                proc = spawn(path, self._recorder, still_wanted=lambda: self._recording_path == path,
                             mode=mode, gate='yield' if self._whisper_busy else 'free',
                             mid_meeting=mid_meeting)
                if proc is None:
                    return
                with self._live_lock:
                    # Si la grabacion paro mientras se lanzaba, _stop_live ya
                    # paso por aqui sin verlo: se mata ahora.
                    if self._recording_path == path:
                        self._live[path.stem] = (proc, path)
                        return
                kill(proc, path)
            except Exception as e:
                log.warning(f"La transcripcion durante la reunion no arranco: {e}")
            finally:
                with self._live_lock:
                    self._live_spawning.discard(path.stem)

        threading.Thread(target=run, daemon=True, name='LiveSpawn').start()
        threading.Thread(target=self._write_live_hints, args=(path,), daemon=True,
                         name='LiveHints').start()

    def _write_live_hints(self, path: Path):
        """Pistas del glosario (invitados, asunto, terminos) para la transcripcion
        durante la reunion, que las lee de <stem>.live.hints. En su propio hilo:
        recording_context puede consultar Outlook, y eso no debe retrasar el
        lanzamiento. Si falla, se transcribe sin pistas."""
        try:
            import live_transcriber
            import transcriber
            live_transcriber.write_hints(path, transcriber._glossary_context(path)['hotwords'])
        except Exception as e:
            log.warning(f"Pistas del glosario no disponibles para {path.name}: {e}")

    def _write_live_ctl(self, path: Path) -> bool:
        """Lo que el proceso de la reunion tiene que hacer: modo y si debe ceder
        el modelo. Se escribe aunque el proceso aun no exista (se lanza a los
        20 s): al arrancar, lo lee. Siempre entero y bajo el lock: dos hilos que
        escribieran cada uno su campo podian dejar un modo y un permiso que ya no
        son los de nadie."""
        try:
            import live_transcriber
            with self._ctl_lock:
                return live_transcriber.write_ctl(
                    path, mode=self._rec_mode or 'background',
                    gate='yield' if self._whisper_busy else 'free', yield_id=self._yield_seq)
        except Exception as e:
            log.warning(f"No se pudo escribir el control de la transcripcion en vivo: {e}")
            return False

    def switch_to_live(self, stem: str | None = None) -> bool:
        """Pasa la grabacion en curso a modo en vivo ('small', ya). Si se indica
        stem (el panel, por .cli_command), tiene que ser el de esta grabacion."""
        # Bajo el lock del .ctl, y _claim_whisper tambien mira el modo bajo el:
        # si no, un 'live' que luego se deshace le haria saltarse el ceder, y
        # habria dos 'medium' a la vez.
        with self._ctl_lock:
            path = self._recording_path
            if path is None or (stem and path.stem != stem) or self._rec_mode == 'live':
                return False
            prev_mode = self._rec_mode
            self._rec_mode = 'live'
            if not self._write_live_ctl(path):
                # El proceso no se enteraria: el pipeline se pausaria esperando un
                # en vivo que no llega. Mejor decir que no se pudo.
                self._rec_mode = prev_mode
                log.warning(f"No se pudo activar el en vivo de {path.name}")
                return False
        self.last_recording_mode = 'live'
        # Sin proceso (fondo desactivado o retirado), se lanza uno en vivo, ya.
        self._start_live(path, 'live', mid_meeting=True)
        log.info(f"Transcripcion en vivo activada a mitad de la grabacion: {path.name}")
        self._write_status()
        try:
            if self._icon:
                self._icon.update_menu()
        except Exception:
            pass
        return True

    def _switch_live_from_menu(self):
        self.switch_to_live()

    def _pipeline_should_pause(self):
        """should_pause de la transcripcion del pipeline: DEFER si una grabacion
        necesita el disco o la memoria (_defer_reason); si no, la pausa del modo
        en vivo (_live_priority)."""
        if self._defer_reason() is not None:
            return _DEFER
        if not self._live_priority():
            return False
        # Con el modelo ya cargado, pausar seria tenerlo cargado (4,7 GB de
        # reserva) junto al del en vivo toda la reunion: se aparta, lo suelta y
        # sigue al colgar. Sin el modelo, la pausa de siempre.
        import transcriber
        return _DEFER if transcriber._model is not None else True

    def _defer_reason(self) -> str | None:
        """'disco' o 'memoria' si se esta grabando una reunion, o guardando la que
        acaba de terminar, y la transcripcion de otra ya terminada debe
        apartarse; None si no. Guardando tambien: con el disco lleno el guardado
        espera sitio con el audio en memoria, y cargar 'medium' se lo quitaria.

        26/10/2026, decision de Felipe: grabar va antes que todo. Ese dia, a las
        16:06, el daemon terminaba con 'medium' la reunion anterior mientras
        grababa la siguiente, y el pagefile lleno el disco. Los umbrales son los
        del modo de fondo (live_transcriber): sin el modelo cargado, lo que pide
        su reserva; ya cargado, los suelos. Sin grabacion, nunca se aparta."""
        if not self._recording_or_saving():
            return None
        try:
            import live_transcriber
            import transcriber
            if transcriber._model is not None:
                if not live_transcriber._disk_floor_ok():
                    return 'disco'
                if not live_transcriber._memory_floor_ok():
                    return 'memoria'
                return None
            if not live_transcriber._disk_ok(transcriber.WHISPER_MODEL):
                return 'disco'
            if not live_transcriber._memory_ok(transcriber.WHISPER_MODEL):
                return 'memoria'
        except Exception as e:
            log.warning(f"No se pudo comprobar el margen para transcribir: {e}")
        return None

    def _live_priority(self) -> bool:
        """El modo en vivo tiene prioridad: mientras dure, el pipeline se pausa.
        Deja de pausarse si el proceso en vivo fallo, termino o espera memoria:
        si no, los dos se quedarian esperando."""
        path = self._recording_path
        if path is None or self._rec_mode != 'live':
            return False
        entry = self._live.get(path.stem)
        if entry is None:
            # Aun lanzandose: la pausa empieza cuando se elige, no 20 s despues.
            return path.stem in self._live_spawning
        try:
            import live_transcriber
            return live_transcriber.child_usable(path, entry[0])
        except Exception:
            return False

    def _claim_whisper(self):
        """El pipeline va a usar Whisper. Si la reunion en curso se transcribe de
        fondo, su proceso suelta el modelo antes: nunca dos modelos grandes a la
        vez, que es lo que ya tumbo la app con poca memoria. En modo en vivo no
        cede nadie: el pipeline se pausa (_live_priority)."""
        with self._whisper_lock:
            self._whisper_busy = True
            self._yield_seq += 1
            yield_id = self._yield_seq
        with self._ctl_lock:
            path = self._recording_path
            if path is None or self._rec_mode == 'live':
                return
            self._write_live_ctl(path)
        try:
            import live_transcriber
            entry = self._live.get(path.stem)
            if entry is None:
                return
            # Deja de esperar si el usuario pasa a en vivo mientras tanto: ese
            # proceso ya no va a ceder (el pipeline se pausara) y no hay que matarlo.
            # Si la grabacion para, se sigue esperando a que ceda o muera (lo mata
            # _stop_live en otro hilo): no basta con que haya parado.
            ok = live_transcriber.wait_yielded(
                path, entry[0], timeout=60, yield_id=yield_id,
                should_stop=lambda: self._rec_mode == 'live')
            if not ok and self._rec_mode != 'live' and self._recording_path == path:
                log.warning(f"La transcripcion de fondo de {path.name} no solto el modelo; se para")
                with self._live_lock:
                    self._live.pop(path.stem, None)
                live_transcriber.kill(entry[0], path)
        except Exception as e:
            log.warning(f"_claim_whisper: {e}")

    def _release_whisper(self, force: bool = False):
        """Fin de un trabajo del pipeline. El modelo vuelve al proceso de fondo
        solo cuando no queda nada en cola: la reunion mas antigua termina primero.
        Con force, tambien con cola: el pipeline se aparto por una grabacion y no
        va a usar Whisper hasta que termine (ver _run_pipeline_until_done)."""
        if not force and not self._pipeline_queue.empty():
            return
        with self._whisper_lock:
            if not self._whisper_busy:
                return
            self._whisper_busy = False
        path = self._recording_path
        if path is not None:
            self._write_live_ctl(path)

    def _stop_live(self, wait: bool = False):
        """Mata el proceso en vivo. Todos los caminos que paran una grabacion
        pasan por set_recording(False), y por eso se hace ahi.

        Por defecto en un hilo: matarlo suele ser instantaneo, pero un proceso
        recien nacido puede negar el acceso unos segundos, y set_recording no
        debe quedarse esperando con el estado a medio cambiar (una reconexion
        rapida mostraria la nueva grabacion como parada). finish_live lo vuelve a
        matar, esperando, antes de tocar las pistas. wait=True es para cancelar,
        que necesita el proceso muerto antes de borrar lo que dejo.
        """
        with self._live_lock:
            running = list(self._live.values())
            self._live.clear()
        if not running:
            return

        def stop():
            for proc, path in running:
                try:
                    import live_transcriber
                    live_transcriber.kill(proc, path)
                except Exception as e:
                    log.warning(f"No se pudo parar la transcripcion en vivo de {path.name}: {e}")

        if wait:
            stop()
        else:
            threading.Thread(target=stop, daemon=True, name='LiveStop').start()

    def set_processing(self, msg: str = ''):
        self._processing_msg = msg
        self._write_status()
        if not self._recording_start:
            if msg:
                self._set_icon(_ICON_PROCESSING, f'Noted - {msg}')
            else:
                self._set_icon(_ICON_IDLE, 'Noted')

    def _set_icon(self, img, tooltip: str):
        self._icon_base = (img, tooltip)
        try:
            if self._icon:
                self._icon.icon = self._mic_decorated_icon(img)
                self._icon.title = self._mic_decorated_title(tooltip)
        except Exception:
            pass

    # ── Microfono: cual es y si esta silenciado ───────────────────────────────

    def _mic_muted(self) -> bool:
        st = getattr(self, '_mic_state', None)
        return bool(st and st.muted)

    def _mic_decorated_icon(self, img):
        return _with_mic_badge(img) if self._mic_muted() else img

    def _mic_decorated_title(self, tooltip: str) -> str:
        if not self._mic_muted():
            return tooltip
        s = _STR.get(get_ui_language(), _STR['en'])
        return f"{tooltip} · {s['mic_tip_muted']}"

    def _mic_menu_label(self) -> str:
        st = getattr(self, '_mic_state', None)
        if st is None:
            return ''
        s = _STR.get(get_ui_language(), _STR['en'])
        return (s['mic_menu_muted'] if st.muted else s['mic_menu_active']).format(name=st.name)

    def _refresh_icon(self):
        img, tooltip = getattr(self, '_icon_base', (_ICON_IDLE, 'Noted'))
        self._set_icon(img, tooltip)
        try:
            if self._icon:
                self._icon.update_menu()
        except Exception:
            pass

    def _mic_poll_once(self):
        """Lee el micro por defecto y repinta solo si algo cambio.

        Cambia solo al enchufar o quitar unos auriculares, o al pulsar la tecla de
        silencio, asi que basta con comparar con el ultimo estado leido.
        """
        try:
            state = mic_status.read_default_mic()
        except Exception:
            state = None
        if state == getattr(self, '_mic_state', None):
            return
        self._mic_state = state
        if state is not None:
            log.info(f"Microfono por defecto: {state.name} — silenciado={state.muted}")
        self._refresh_icon()

    def _mic_poller(self):
        mic_status.com_init()
        while True:
            try:
                self._mic_poll_once()
            except Exception as e:
                log.debug(f"MicStatusPoller: {e}")
            time.sleep(_MIC_POLL_S)

    def _toggle_mic_mute(self):
        mic_status.com_init()
        st = getattr(self, '_mic_state', None) or mic_status.read_default_mic()
        if st is None:
            return
        if mic_status.set_default_mic_muted(not st.muted):
            self._mic_poll_once()

    def warn_if_mic_muted(self):
        """Al empezar a grabar: si el micro esta silenciado, avisar YA.

        Sin esto el problema solo aparecia al final, como un 'sin voz' tras la
        transcripcion, con la reunion ya perdida. No bloquea la grabacion: el
        audio del sistema (los demas en una llamada) se sigue capturando.
        """
        mic_status.com_init()
        st = mic_status.read_default_mic()
        if st is None or not st.muted:
            return
        log.warning(f"Grabando con el microfono silenciado en Windows: {st.name}")
        s = _STR.get(get_ui_language(), _STR['en'])
        self._notify('Noted ⚠', s['mic_muted_warning'].format(name=st.name))

    def _ticker(self):
        while not self._ticker_stop.wait(1.0):
            if self._recording_start:
                elapsed = int(time.time() - self._recording_start)
                m, s = divmod(elapsed, 60)
                rec_str = f'Recording {m:02d}:{s:02d}'
                proc = self._processing_msg
                tooltip = f'Noted - {rec_str}' + (f' | {proc}' if proc else '')
                try:
                    if self._icon:
                        self._icon.title = self._mic_decorated_title(tooltip)
                except Exception:
                    pass
                try:
                    self._note_teams_title()
                except Exception as e:
                    log.warning(f"No se pudo apuntar el título de la reunión: {e}")
            self._write_status()
            self._check_pending_notification()
            if self._recording_start and time.monotonic() >= self._disk_next_check:
                self._disk_next_check = time.monotonic() + DISK_CHECK_EVERY_S
                try:
                    self._check_disk()
                except Exception as e:
                    log.warning(f"No se pudo comprobar el disco: {e}")

    def _note_teams_title(self):
        """Apunta junto a la grabacion en curso (<stem>.title) el nombre de su
        reunion de Teams cuando el nombre del fichero no lo lleva: "Grabar
        ahora", o el aviso de Teams aceptado con una grabacion ya en marcha. El
        28/10/2026 dos reuniones asi se quedaron como "Reunion" al fallar Claude.
        Vale el primero: un cambio de titulo a mitad suele ser otra ventana."""
        path = self._recording_path
        if path is None or self._stem_meeting_name(path.stem):
            return
        name = getattr(self._detector, 'current_meeting_name', None)
        if not isinstance(name, str) or not self._is_meeting_title(name):
            return
        sidecar = path.with_suffix('.title')
        if sidecar.exists():
            return
        sidecar.write_text(name.strip(), encoding='utf-8')
        log.info(f"Título de Teams para {path.name}: {name.strip()}")

    def _check_disk(self):
        """Disco libre mientras se graba (ver disk_guard). El 26/10/2026 se lleno
        en minutos a mitad de una reunion y nadie aviso."""
        import disk_guard
        free = disk_guard.free_gb()
        level = self._disk_levels.update(free)
        if level is None:
            return
        s = _STR.get(get_ui_language(), _STR['en'])
        if level == 'warn':
            log.warning(f"Poco disco durante la grabacion: {free:.1f} GB libres")
            self._notify('Noted ⚠', s['disk_low'].format(gb=free))
            return
        released = disk_guard.release_reserve()
        log.error(f"Disco casi lleno durante la grabacion: {free:.1f} GB libres; "
                  f"reserva liberada {released:.1f} GB")
        msg = (s['disk_critical_reserve'].format(gb=free, released=released) if released
               else s['disk_critical'].format(gb=free))
        self._notify('Noted ⚠', msg)

    def _refresh_disk_reserve(self):
        """Repone la reserva de disco fuera de las grabaciones (ver disk_guard)."""
        try:
            if getattr(self._recorder, 'is_recording', False):
                return
            import disk_guard
            disk_guard.ensure_reserve()
        except Exception as e:
            log.warning(f"No se pudo reponer la reserva de disco: {e}")

    def _start_daily_brain_scan(self):
        """Background thread: runs email brain scan daily at ~08:00."""
        import datetime as _dt

        def _loop():
            last_run_date = None
            while True:
                now = _dt.datetime.now()
                today = _dt.date.today()
                if now.hour == 8 and last_run_date != today:
                    last_run_date = today
                    try:
                        from email_brain_updater import run_daily_scan
                        from config import PROJECT_DIR
                        import json
                        projects_file = PROJECT_DIR / 'projects.json'
                        if projects_file.exists():
                            projects = json.loads(
                                projects_file.read_text(encoding='utf-8')
                            ).get('projects', [])
                            run_daily_scan(projects)
                    except Exception as e:
                        log.warning(f"BrainDailyScan: {e}")
                time.sleep(60)

        threading.Thread(target=_loop, daemon=True, name='BrainDailyScan').start()

    def _notification_poller(self):
        while True:
            time.sleep(5)
            self._check_pending_notification()

    def _check_pending_notification(self):
        notification_file = PROJECT_DIR / '.pending_notification.txt'
        if not notification_file.exists():
            return
        try:
            commits_text = notification_file.read_text(encoding='utf-8').strip()
            notification_file.unlink()
        except Exception:
            return
        if not commits_text:
            return
        threading.Thread(
            target=self._send_push_notification, args=(commits_text,),
            daemon=True, name='PushNotification',
        ).start()

    def _send_push_notification(self, commits_text: str):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                'send_update_email',
                PROJECT_DIR / 'hooks' / 'send_update_email.py',
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            ok = mod.send_update_email(commits_text)
            log.info(f"Push notification email {'enviado' if ok else 'fallido'}")
        except Exception as e:
            log.error(f"Push notification error: {e}")

    # ── cancelación de trabajos del pipeline ─────────────────────────────────

    def cancel_job(self, stem: str) -> bool:
        """Marca un trabajo para descartarlo: en cola o ya en proceso."""
        if not stem:
            return False
        with self._cancel_lock:
            self._cancelled.add(stem)
        _append_cancel_signal(stem)
        self._pipeline_queued = [n for n in self._pipeline_queued if n != stem]
        self._write_status()
        log.info(f"Trabajo marcado para descartar: {stem}")
        return True

    def _is_cancelled(self, stem: str) -> bool:
        with self._cancel_lock:
            if stem in self._cancelled:
                return True
        if stem in _read_cancel_signals():
            with self._cancel_lock:
                self._cancelled.add(stem)
            return True
        return False

    def _forget_cancelled(self, stem: str) -> None:
        with self._cancel_lock:
            self._cancelled.discard(stem)
        _remove_cancel_signal(stem)

    def _discard_job(self, wav_path: Path, reason: str, minutes_path: Path | None = None) -> None:
        """Manda a la papelera todo lo producido por un trabajo descartado.

        Borrado suave, igual que delete_meeting: si el usuario se arrepiente, la
        vista Papelera lo recupera. Se reutiliza el mismo formato de carpeta y
        _trash_meta.json para que list_trash y recover_meeting no necesiten
        saber que esto existe.
        """
        stem = wav_path.stem
        trash_root = MINUTES_DIR.parent / 'trash'
        trash_dir = trash_root / stem
        n = 2
        while trash_dir.exists():
            trash_dir = trash_root / f"{stem}__{n}"
            n += 1
        try:
            trash_dir.mkdir(parents=True, exist_ok=True)
            candidates: list[Path] = []
            # escape: _slugify conserva los corchetes, y "[EXT]" en el patron es
            # una clase de caracteres que no casa con el nombre real.
            pat = glob.escape(stem)
            for folder in (RECORDINGS_DIR, RECORDINGS_DIR / 'processed'):
                if folder.exists():
                    candidates.extend(folder.glob(f"{pat}.*"))
                    candidates.extend(folder.glob(f"{pat}_transcript.*"))
            if minutes_path:
                candidates += [
                    minutes_path,
                    minutes_path.with_suffix('.html'),
                    minutes_path.parent / f"{minutes_path.stem}_actions.json",
                    minutes_path.parent / f"{minutes_path.stem}_transcript.txt",
                ]

            files_meta = []
            for f in dict.fromkeys(candidates):        # sin duplicados, orden estable
                if f.exists() and f.is_file():
                    try:
                        shutil.move(str(f), str(trash_dir / f.name))
                        files_meta.append({'name': f.name, 'orig_dir': str(f.parent)})
                    except Exception as e:
                        log.warning(f"_discard_job move {f.name}: {e}")

            m = re.match(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_(.+))?', stem)
            title = (_slug_title(m.group(6)) if (m and m.group(6)) else stem)
            (trash_dir / '_trash_meta.json').write_text(json.dumps({
                'stem':       stem,
                'title':      title,
                'date':       f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else '',
                'time':       f"{m.group(4)}:{m.group(5)}" if m else '',
                'deleted_at': datetime.now().isoformat(),
                'cancelled':  True,
                'reason':     reason,
                'files':      files_meta,
            }, ensure_ascii=False, indent=2), encoding='utf-8')
            log.info(f"Trabajo descartado ({reason}): {stem} → papelera "
                     f"({len(files_meta)} ficheros)")
        except Exception as e:
            log.warning(f"_discard_job: {e}")
        finally:
            self._forget_cancelled(stem)
            self.set_processing('')
            self._current_job = {}
            self._write_status()

    def _on_recording_done(self, wav_path: Path):
        self._register_part_recorded(wav_path)
        self._pipeline_queued.append(wav_path.stem)
        self._pipeline_queue.put(wav_path)
        self._write_status()
        n = self._pipeline_queue.qsize()
        if n > 1:
            s = _STR.get(get_ui_language(), _STR['en'])
            self._notify('Noted', s['recordings_queued'].format(n=n))

    def _pipeline_loop(self):
        while True:
            wav_path = self._pipeline_queue.get()
            if wav_path is None:
                break
            try:
                self._pipeline_queued = [n for n in self._pipeline_queued if n != wav_path.stem]
                self._write_status()
                if self._is_cancelled(wav_path.stem):
                    self._discard_job(wav_path, 'descartada en cola')
                    continue
                self._run_pipeline_until_done(wav_path)
            except _Cancelled as c:
                # Descartada a mitad del proceso. c.minutes_path apunta a las
                # minutas si ya se habían escrito, para que también se vayan.
                self._discard_job(wav_path, 'descartada durante el proceso',
                                  getattr(c, 'minutes_path', None))
            except Exception as e:
                log.error(f"Pipeline error: {e}", exc_info=True)
                self.set_processing('')
            finally:
                self._release_whisper()

    def _run_pipeline_until_done(self, wav_path: Path):
        """_run_pipeline_sync; si se aparta por una grabacion (TranscriptionDeferred),
        espera a que la grabacion termine y lo repite desde lo hecho: el .partial
        (transcripcion por trozos) o el .live.jsonl (cierre de lo hecho en vivo).
        Con las dos pistas (diarizada) no hay .partial: esa transcripcion vuelve a
        empezar. Se pierde tiempo, no texto. No reintenta antes: cada intento paga una carga del modelo
        (30-60 s, el pico de memoria) y, si volviera a apartarse antes de cerrar un
        trozo, no avanzaria (revision del 26/10/2026)."""
        import gc
        while True:
            try:
                return self._run_pipeline_sync(wav_path)
            except _Deferred:
                pass
            # Fuera del except: mientras la excepcion vive, su traceback retiene el
            # generador de faster-whisper y con el el modelo (4,7 GB de reserva).
            gc.collect()
            self._wait_while_deferred(wav_path)

    def _wait_while_deferred(self, wav_path: Path):
        reason = self._defer_reason()
        s = _STR.get(get_ui_language(), _STR['en'])
        if reason is None and self._live_priority():
            log.warning(f"Transcripción de {wav_path.name} en pausa: la reunión en curso va en vivo. "
                        f"Sigue al colgar")
            self.set_processing(s['deferred_live'])
        else:
            log.warning(f"Transcripción de {wav_path.name} en pausa: se está grabando otra reunión "
                        f"y falta {reason or 'disco o memoria'}. Sigue al colgar")
            self.set_processing(s['deferred'])
        # La transcripcion de fondo de la reunion en curso recupera el modelo: con
        # sus propios umbrales decide si puede transcribir mientras tanto.
        self._release_whisper(force=True)
        t0 = time.monotonic()
        while self._recording_or_saving():
            if self._is_cancelled(wav_path.stem):
                raise _Cancelled()
            time.sleep(DEFER_RECHECK_S)
        log.info(f"Transcripción de {wav_path.name}: sigue tras {time.monotonic() - t0:.0f} s en pausa")

    def _recording_or_saving(self) -> bool:
        """Grabando, o guardando el WAV de la que acaba de terminar (con el disco
        lleno el guardado espera sitio, y cargar 'medium' se lo quitaria)."""
        rec = self._recorder
        return bool(getattr(rec, 'is_recording', False) or getattr(rec, 'is_saving', False))

    def _run_pipeline_sync(self, wav_path: Path):
        from storage import get_transcript_path
        from transcriber import partial_resume, transcribe

        s = _STR.get(get_ui_language(), _STR['en'])
        transcript_path = get_transcript_path(wav_path)
        partial_path = wav_path.parent / f"{wav_path.stem}.partial"

        detected_language = 'auto'

        _m = re.match(r'\d{4}-\d{2}-\d{2}_(\d{2})-(\d{2})(?:_(.+))?', wav_path.stem)
        _job_time  = f"{_m.group(1)}:{_m.group(2)}" if _m else ''
        _job_title = _slug_title(_m.group(3)) if (_m and _m.group(3)) else wav_path.stem
        # 'stem' viaja hasta .pipeline_status.json para que la ventana web sepa
        # qué trabajo está cancelando.
        self._current_job = {'stem': wav_path.stem, 'title': _job_title, 'time': _job_time,
                             'step': 1, 'total_steps': 3, 'step_label': 'Transcribiendo',
                             'step_started': time.time()}

        lang_path = transcript_path.with_suffix('.lang')
        if not transcript_path.exists():
            # Antes de pedir el modelo a la transcripcion de fondo de la reunion en
            # curso: si de todos modos hay que apartarse, que ella siga.
            if self._defer_reason() is not None:
                raise _Deferred()
            self._claim_whisper()
            self._use_live_transcript(wav_path, transcript_path, lang_path)
        if transcript_path.exists():
            transcript_text = transcript_path.read_text(encoding='utf-8')
            if lang_path.exists():
                detected_language = lang_path.read_text().strip()
            log.info(f"Transcript ya existe, saltando: {transcript_path.name}")
        else:
            # Si un intento anterior murió a medias (el 17/09 el proceso se fue
            # con 0xC0000409 al 55% y el watchdog lo relanzó), se sigue desde su
            # .partial en vez de volver a empezar. Las líneas ya transcritas se
            # siembran aquí para que el .partial siga completo si vuelve a caer.
            _resume_at, _kept = partial_resume(partial_path)
            if _resume_at:
                log.info(f"Reanudando transcripción de {wav_path.name} en "
                         f"{_resume_at/60:.1f} min ({len(_kept)} líneas ya hechas)")
            segments = list(_kept)
            self.set_processing('Transcribiendo 0%...')

            def on_seg(line):
                segments.append(line)
                try:
                    partial_path.write_text('\n'.join(segments), encoding='utf-8')
                except Exception:
                    pass

            def on_progress(pct):
                self.set_processing(f'Transcribiendo {pct}%...')

            result = transcribe(wav_path, on_progress=on_progress, on_segment=on_seg,
                                should_cancel=lambda: self._is_cancelled(wav_path.stem),
                                resume_from=partial_path, should_pause=self._pipeline_should_pause)
            if partial_path.exists():
                partial_path.unlink()

            if not result:
                log.error(f"Transcripción fallida para {wav_path.name}")
                self._notify('Noted ⚠', s['transcription_failed'])
                self.set_processing('')
                return

            transcript_text, detected_language = result
            lang_path.write_text(detected_language)

            if not transcript_text:
                # NO es un fallo: transcribe() devolvio sin excepcion y con
                # idioma detectado. El VAD de Whisper se llevo todo el audio
                # porque no habia voz. Tratarlo como error hacia imposible
                # distinguir "no habia nada que transcribir" de "el
                # transcriptor se rompio" — misma linea de log y misma
                # notificacion. Paso el 22/09/2026 con una grabacion de 8,5s
                # disparada por una ventana residual de Teams.
                log.info(f"Sin voz en {wav_path.name}: nada que transcribir "
                         f"(idioma detectado '{detected_language}'; la duracion "
                         f"la registra faster_whisper justo encima)")
                self._notify('Noted', s['no_speech'])
                # Y se saca de recordings/, como hace el camino bueno. Sin
                # esto la grabacion se queda ahi para siempre: _recover_pending
                # busca WAVs sin minutas, y una grabacion sin voz NUNCA las
                # tendra, asi que se reencolaba en CADA arranque del daemon.
                # Cada vez costaba una carga de Whisper — ~1 GB y 15 s — en una
                # maquina donde quedarse sin memoria es lo que tumba el worker
                # del loopback y hace perder las voces de los demas.
                self._move_to_processed(wav_path, transcript_path)
                self._discard_live_files(wav_path)
                self.set_processing('')
                return

            transcript_path.write_text(transcript_text, encoding='utf-8')

        self._discard_live_files(wav_path)
        self._register_part(wav_path, transcript_text, detected_language)

    def _use_live_transcript(self, wav_path: Path, transcript_path: Path, lang_path: Path):
        """Cierra el transcript con lo hecho durante la reunion y lo deja donde
        _run_pipeline_sync busca uno ya hecho (live_transcriber.finish_live).

        Aprovecha solo los trozos hechos con el modelo de siempre (el modo de
        fondo), y tambien los de 'small' si "live_transcript_final" esta activado;
        lo que falte lo transcribe ahora. Sin nada aprovechable, o ante cualquier
        problema, no deja nada escrito y el pipeline transcribe como siempre. Solo
        la cancelacion se propaga: es el usuario descartando la reunion.
        """
        tmp = transcript_path.with_name(transcript_path.name + '.tmp')
        try:
            import live_transcriber
            if not live_transcriber.jsonl_path(wav_path).exists():
                return
        except Exception as e:
            log.warning(f"Transcript en vivo no utilizable para {wav_path.name} ({e})")
            return
        try:
            self.set_processing('Transcribiendo...')
            import transcriber
            glossary_ctx = transcriber._glossary_context(wav_path)
            result = live_transcriber.finish_live(
                wav_path,
                on_progress=lambda pct: self.set_processing(f'Transcribiendo {pct}%...'),
                should_cancel=lambda: self._is_cancelled(wav_path.stem),
                should_pause=self._pipeline_should_pause,
                hotwords=glossary_ctx['hotwords'])
            text, lang = result if result is not None else ('', '')
            if text.strip():
                # Las correcciones, una vez y sobre todo el texto: lo hecho durante
                # la reunion y la cola (con las entradas de ahora, ver
                # transcriber._correct_with_glossary).
                text = transcriber._correct_with_glossary(text, glossary_ctx)
            if result is not None and not text.strip():
                # Sin voz: que lo confirme el camino de siempre, que es el que
                # sabe tratar una grabacion vacia.
                log.info(f"En vivo no se encontro voz en {wav_path.name}: "
                         f"se comprueba con la transcripcion de siempre")
            if text.strip():
                lang_path.write_text(lang)
                # Atomico: un transcript a medias en disco se tomaria por bueno.
                tmp.write_text(text, encoding='utf-8')
                tmp.replace(transcript_path)
                log.info(f"Transcript en vivo cerrado: {wav_path.name} "
                         f"({len(text.splitlines())} lineas, idioma {lang})")
                live_transcriber.discard_artifacts(wav_path, parts=True)
            # Si no, camino de siempre. Los ficheros en vivo se quitan cuando ese
            # termine bien (_discard_live_files): si fallara, serian el unico
            # texto de la reunion.
        except (_Cancelled, _Deferred):
            # Aplazada no es "el en vivo no sirve": empezar de cero perderia lo hecho.
            raise
        except Exception as e:
            log.warning(f"Transcript en vivo no utilizable para {wav_path.name} ({e}): "
                        f"se transcribe como siempre", exc_info=True)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _discard_live_files(self, wav_path: Path):
        """El transcript que se guarda ya esta hecho: lo que se escribio en vivo
        (live_transcriber.py) era para verlo durante la reunion y no debe
        quedarse en recordings/. Si la transcripcion falla no se llega aqui y se
        conserva: es el unico texto de esa reunion. Las pistas no se tocan, las
        gestiona el transcriptor."""
        try:
            import live_transcriber
            live_transcriber.discard_artifacts(wav_path, tracks=False, parts=True)
        except Exception as e:
            log.warning(f"No se pudieron borrar los ficheros en vivo de {wav_path.name}: {e}")

    # ── Sesión de reunión ─────────────────────────────────────────────────────

    def _norm_key(self, name: str) -> str:
        return re.sub(r'[^a-z0-9]', '', (name or '').lower())

    def _register_part_recorded(self, wav_path):
        _nm = re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(.+)', wav_path.stem)
        key = self._norm_key(_nm.group(1).replace('_', ' ')) if _nm else ''
        with self._session_lock:
            sess = self._session
            if sess and sess.get('awaiting') and not sess.get('finalized'):
                sess['parts'].append(wav_path)
                sess['transcripts'][wav_path.stem] = None
                sess['awaiting'] = False
                sess['last_at'] = time.time()
                log.info("Reconexión: grabación añadida a la reunión anterior")
            else:
                if sess and not sess.get('finalized'):
                    threading.Thread(target=self._finalize_session, args=(sess,),
                                     daemon=True, name='FinalizeOld').start()
                self._session = {
                    'base_wav': wav_path, 'parts': [wav_path],
                    'transcripts': {wav_path.stem: None}, 'lang': 'auto',
                    'key': key, 'awaiting': False, 'finalized': False,
                    'timer': None, 'last_at': time.time(),
                }

    def _register_part(self, wav_path, transcript_text, detected_language):
        with self._session_lock:
            sess = self._session
            if (not sess or sess.get('finalized')
                    or wav_path.stem not in sess.get('transcripts', {})):
                _nm = re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(.+)', wav_path.stem)
                key = self._norm_key(_nm.group(1).replace('_', ' ')) if _nm else ''
                sess = {
                    'base_wav': wav_path, 'parts': [wav_path],
                    'transcripts': {wav_path.stem: transcript_text}, 'lang': detected_language,
                    'key': key, 'awaiting': False, 'finalized': False,
                    'timer': None, 'last_at': time.time(),
                }
                self._session = sess
            else:
                sess['transcripts'][wav_path.stem] = transcript_text
                if detected_language and detected_language != 'auto':
                    sess['lang'] = detected_language
                sess['last_at'] = time.time()
            complete = (not sess.get('awaiting')
                        and all(v is not None for v in sess['transcripts'].values()))
        if complete:
            self._schedule_finalize(sess)
            self.set_processing('Transcrito — cerrando (por si te reconectas)')
        else:
            self.set_processing('Transcrito — esperando la reconexión')

    def _schedule_finalize(self, sess):
        with self._session_lock:
            t = sess.get('timer')
            if t:
                try: t.cancel()
                except Exception: pass
            timer = threading.Timer(self._MERGE_GRACE, self._finalize_session, args=(sess,))
            timer.daemon = True
            sess['timer'] = timer
            timer.start()

    # Titulos de ventanas pasajeras de Teams, no nombres de reunion: con ellos no
    # se puede decir que dos llamadas sean la misma. 20/10/2026: una reunion
    # grabada como 'Unión a la reunión' hizo preguntar "Misma reunión
    # detectada" al unirse a otra llamada distinta.
    _TRANSIENT_TITLES = ('Unión a la reunión', 'Unirse a la reunión', 'Join meeting', 'Joining meeting',
                         'Barra de control de uso compartido', 'Sharing control bar',
                         'Vista compacta de la reunión', 'Meeting compact view')
    # Nombres que la app da a una grabacion sin reunion detras: no son titulos.
    _GENERIC_NAMES = ('manual', 'recording', 'cli', 'reunion', 'reunión', 'meeting')

    def _is_meeting_title(self, name: str) -> bool:
        key = self._norm_key(name)
        return bool(key) and key not in {self._norm_key(t) for t in self._GENERIC_NAMES + self._TRANSIENT_TITLES}

    def _stem_meeting_name(self, stem: str) -> str | None:
        """El nombre de reunion que lleva el fichero (YYYY-MM-DD_HH-MM_<nombre>),
        sin el sufijo de colision (__2); None si es 'manual' o un titulo pasajero."""
        m = re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(.+)', stem)
        if not m:
            return None
        name = re.sub(r'__\d+$', '', m.group(1)).replace('_', ' ').strip()
        return name if self._is_meeting_title(name) else None

    def _recording_meeting_name(self, wav_path: Path) -> str | None:
        """El nombre de reunion de una grabacion: el del fichero o, si no lo
        lleva, el titulo de Teams apuntado en su <stem>.title."""
        name = self._stem_meeting_name(wav_path.stem)
        if name:
            return name
        for folder in dict.fromkeys((wav_path.parent, RECORDINGS_DIR, RECORDINGS_DIR / 'processed')):
            try:
                title = (folder / f'{wav_path.stem}.title').read_text(encoding='utf-8').strip()
            except OSError:
                continue
            if self._is_meeting_title(title):
                return title
        return None

    def _fallback_title(self, parts: list, s: dict, use_outlook: bool = False) -> str:
        """El titulo de una reunion sin el de Claude, siempre uno que la distinga:
        el nombre de Teams de cualquiera de sus grabaciones (el del fichero o su
        .title), el asunto de la cita que empieza a esa hora, o la hora. Outlook
        solo con use_outlook (las minutas completas, que ya hablan con el): con
        las breves no se abre."""
        for p in parts:
            name = self._stem_meeting_name(p.stem)
            if name:
                return name
        for p in parts:
            for folder in dict.fromkeys((p.parent, RECORDINGS_DIR, RECORDINGS_DIR / 'processed')):
                try:
                    name = (folder / f'{p.stem}.title').read_text(encoding='utf-8').strip()
                except OSError:
                    continue
                if self._is_meeting_title(name):
                    return name
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})', parts[0].stem) if parts else None
        if use_outlook and m:
            from outlook_sender import find_meeting_subject
            subject = find_meeting_subject(datetime(*map(int, m.groups())))
            if subject and subject.strip():
                return subject.strip()
        return s['untitled'].format(time=f"{m.group(4)}:{m.group(5)}" if m else datetime.now().strftime('%H:%M'))

    def has_pending_session(self, meeting_name: str) -> bool:
        with self._session_lock:
            sess = self._session
            if not sess or sess.get('finalized'):
                return False
            key = self._norm_key(meeting_name)
            skey = sess.get('key') or ''
            if not key or not skey:
                return False
            transient = {self._norm_key(t) for t in self._TRANSIENT_TITLES}
            if key in transient or skey in transient:
                return False
            return key == skey or key in skey or skey in key

    def request_continuation(self):
        with self._session_lock:
            sess = self._session
            if sess and not sess.get('finalized'):
                sess['awaiting'] = True
                t = sess.get('timer')
                if t:
                    try: t.cancel()
                    except Exception: pass
                    sess['timer'] = None
                log.info("Reunión marcada para continuar (esperando la reconexión)")

    def finalize_pending_now(self):
        with self._session_lock:
            sess = self._session
            if sess and not sess.get('finalized'):
                t = sess.get('timer')
                if t:
                    try: t.cancel()
                    except Exception: pass
                threading.Thread(target=self._finalize_session, args=(sess,),
                                 daemon=True, name='FinalizeNow').start()

    def _finalize_session(self, session):
        """Cierra la reunión. Corre en un hilo suyo (el Timer de _schedule_finalize,
        finalize_pending_now o la llegada de otra grabación), así que lo que lance
        no lo recoge nadie: el descarte de _pipeline_loop está en otro hilo. Hasta
        el 20/10/2026 un descarte a mitad del cierre moría aquí, con las minutas en
        el panel, el WAV en recordings/ y el trabajo colgado en "Generando minutas".
        """
        try:
            self._finalize_session_body(session)
        except _Cancelled as c:
            self._discard_job(session['base_wav'], 'descartada durante el proceso',
                              getattr(c, 'minutes_path', None))
        except Exception as e:
            log.error(f"Error cerrando la reunión {session.get('base_wav')}: {e}", exc_info=True)
            self._current_job = {}
            self.set_processing('')
        self._refresh_disk_reserve()

    def _finalize_session_body(self, session):
        from storage import get_transcript_path, get_minutes_path
        from minutes_generator import generate_minutes, extract_title_from_minutes, save_minutes
        from html_exporter import export_to_html
        from actions_enricher import enrich_and_save
        import config as _config

        # Leidos ahora y no al importar: se cambian desde el panel, otro proceso.
        minutes_mode = _config.minutes_mode()
        actions_on = _config.actions_enabled()

        with self._session_lock:
            if session.get('finalized'):
                return
            session['finalized'] = True
            _t = session.get('timer')
            if _t:
                try: _t.cancel()
                except Exception: pass

        parts = session.get('parts', [])
        tmap = session.get('transcripts', {})
        transcripts = [t for t in (tmap.get(p.stem) for p in parts) if t]
        if not parts or not transcripts:
            return
        wav_path = session['base_wav']
        detected_language = session.get('lang', 'auto')
        if len(transcripts) > 1:
            transcript_text = "\n\n[--- reconexión: continuación de la reunión ---]\n\n".join(transcripts)
            log.info(f"Cerrando reunión: {len(parts)} grabaciones unidas")
        else:
            transcript_text = transcripts[0]

        s = _STR.get(get_ui_language(), _STR['en'])
        # Un reintento de unas minutas que fallaron (_retry_pending_minutes) trae
        # sus minimas: ya estan en el panel, archivadas con su nombre, y las nuevas
        # van al mismo fichero. Su transcript no se deja en recordings/: sin un WAV
        # al lado, _recover_pending no lo recogeria si el cierre se cortase.
        retry_md = session.get('pending_md')
        transcript_path = get_transcript_path(wav_path)
        if retry_md is None:
            try:
                transcript_path.write_text(transcript_text, encoding='utf-8')
            except Exception:
                pass

        _mj = re.match(r'\d{4}-\d{2}-\d{2}_(\d{2})-(\d{2})(?:_(.+))?', wav_path.stem)
        self._current_job = {
            'title': (_slug_title(_mj.group(3)) if (_mj and _mj.group(3)) else wav_path.stem),
            'time': f"{_mj.group(1)}:{_mj.group(2)}" if _mj else '',
            'step': 2, 'total_steps': 3, 'step_label': 'Generando minutas', 'step_started': time.time(),
        }

        for extra in parts[1:]:
            try:
                if extra.exists():
                    (RECORDINGS_DIR / 'processed').mkdir(exist_ok=True)
                    shutil.move(str(extra), str((RECORDINGS_DIR / 'processed') / extra.name))
            except Exception as e:
                log.warning(f"mover parte extra {extra.name}: {e}")
            for aux in (extra.with_name(extra.stem + '_transcript.txt'),
                        extra.with_suffix('.lang'), extra.with_suffix('.partial'),
                        extra.with_suffix('.context'), extra.with_suffix('.live')):
                try:
                    if aux.exists():
                        aux.unlink()
                except Exception:
                    pass

        extra_context = None
        context_path = wav_path.with_suffix('.context')
        if not context_path.exists():
            context_path = RECORDINGS_DIR / 'processed' / wav_path.with_suffix('.context').name
        if context_path.exists():
            try:
                extra_context = context_path.read_text(encoding='utf-8').strip() or None
                context_path.unlink()
            except Exception:
                pass

        # La memoria del proyecto y los participantes solo alimentan las minutas
        # completas: con minutas breves no se pagan (recorrer documentos, Outlook).
        _proj, _ctx_dir = None, None
        participants = []
        if minutes_mode == 'full':
            try:
                from project_context import prepare_context
                _nm = re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(.+)', wav_path.stem)
                _mtg_name = _nm.group(1).replace('_', ' ') if _nm else ''
                _proj, _ctx_dir = prepare_context(transcript_text, _mtg_name)
                if _ctx_dir:
                    log.info(f"Memoria de proyecto activa: {_proj.get('name')}")
            except Exception as e:
                log.warning(f"No se pudo preparar la memoria de proyecto: {e}")

            # Fetch Outlook participants BEFORE generating minutes so they can be injected into the prompt
            try:
                from outlook_sender import find_meeting_participants
                _pm = re.match(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})', wav_path.stem)
                if _pm:
                    from datetime import datetime as _dt
                    _rec_time = _dt(int(_pm.group(1)), int(_pm.group(2)), int(_pm.group(3)),
                                    int(_pm.group(4)), int(_pm.group(5)))
                    # El nombre del fichero o, si es 'manual', el titulo de Teams
                    # apuntado en <stem>.title: sin nombre se empareja solo por
                    # hora y con dos citas a las 11:00 ganaba la equivocada.
                    _rec_name = self._recording_meeting_name(wav_path)
                    participants = find_meeting_participants(_rec_time, meeting_name=_rec_name)
                    log.info(f"Participantes detectados: {len(participants)}")
            except Exception as e:
                log.warning(f"No se pudieron detectar participantes: {e}")

        # Última salida antes del paso caro: generar minutas es una llamada a
        # Claude, y no tiene sentido pagarla si la reunión ya está descartada.
        if self._is_cancelled(wav_path.stem):
            raise _Cancelled()

        pending_md = retry_md or self._pending_minutes_of(wav_path.stem)
        if retry_md is not None and not self._still_pending(retry_md, wav_path.stem):
            log.info(f"Minutas de {retry_md.name}: ya no están pendientes, no se reintentan")
            self._current_job = {}
            self.set_processing('')
            return
        # Si el usuario renombro la reunion mientras estaba pendiente, su titulo se queda.
        user_title = self._declared_title(pending_md) if pending_md is not None else None

        if minutes_mode == 'full':
            self._current_job.update({'step': 2, 'step_label': 'Generando minutas', 'step_started': time.time()})
            self.set_processing('Generando minutas...')
            raw = generate_minutes(transcript_text, wav_path, extra_context=extra_context,
                                   language=detected_language, context_dir=_ctx_dir,
                                   participants=participants)
            if not raw:
                # Antes se hacia return: sin .md la reunion no salia en el panel y
                # se reprocesaba en cada arranque (revision del 20/10/2026). Quedan
                # unas minimas con su titulo y la marca 'pending', y se rehacen
                # solas en cuanto Claude responda (_retry_pending_minutes).
                from minutes_generator import claude_problem
                reason = claude_problem()
                log.error(f"Generación de minutas fallida ({reason or 'Claude no respondió'}): "
                          f"quedan pendientes y se reintentarán solas")
                if pending_md is None:
                    self._notify('Noted ⚠', s[{'no_session': 'minutes_pending_session',
                                               'missing': 'minutes_pending_missing'}.get(reason, 'minutes_pending')])
                raw = self._brief_minutes(transcript_text, wav_path, 'pending', detected_language, s,
                                          title=user_title or self._fallback_title(parts, s, use_outlook=True),
                                          reason=reason)
                minutes_mode = 'off'
        else:
            raw = self._brief_minutes(transcript_text, wav_path, minutes_mode, detected_language, s,
                                      title=user_title or self._fallback_title(parts, s))

        title, content = extract_title_from_minutes(raw)
        if pending_md is not None:
            minutes_path = pending_md
            # El fichero conserva su nombre. El panel muestra la linea TITULO, como
            # tras renombrar: la del usuario si la renombro, si no la de Claude.
            if user_title or minutes_mode == 'full':
                content = f"TITULO: {user_title or title}\n\n{content}"
            # El boton "Generar minutas completas" (el panel, otro proceso) puede
            # haberlas hecho mientras Claude generaba estas: las suyas se quedan.
            if retry_md is not None and not self._still_pending(retry_md, wav_path.stem):
                log.info(f"Minutas de {retry_md.name}: ya se generaron mientras se reintentaba")
                self._current_job = {}
                self.set_processing('')
                return
        else:
            minutes_path = get_minutes_path(wav_path, title)
        existed = minutes_path.exists()
        saved = save_minutes(content, minutes_path)

        # Si se descartó mientras Claude generaba, las minutas ya están en disco:
        # se adjuntan a la excepción para que se vayan a la papelera con el resto.
        if self._is_cancelled(wav_path.stem):
            exc = _Cancelled()
            exc.minutes_path = minutes_path
            raise exc

        # Sin .md la reunión no existe en el panel. El WAV y el transcript se
        # quedan en recordings/: _recover_pending la reencola en el siguiente
        # arranque y el pipeline reaprovecha el transcript, sin pasar por Whisper.
        # write_text trunca antes de escribir: con el disco lleno deja un .md a
        # medias, y _recover_pending lo daría por hecho. Solo se borra si lo
        # creó este intento; uno que ya estaba no es suyo.
        if not saved:
            if not existed:
                try:
                    minutes_path.unlink(missing_ok=True)
                except OSError as e:
                    log.warning(f"No se pudo borrar la minuta a medias {minutes_path.name}: {e}")
            self._notify('Noted ⚠', s['minutes_failed'])
            self._current_job = {}
            self.set_processing('')
            return

        # El archivado en la carpeta del proyecto NO se hace aquí: _proj viene de
        # detect_project() (palabras clave sobre el transcript) y solo sirve para
        # elegir el contexto que se pasa al LLM. El proyecto definitivo lo decide
        # enrich_and_save más abajo, y el archivado ocurre en su callback.

        try:
            transcript_copy = minutes_path.with_name(minutes_path.stem + '_transcript.txt')
            transcript_copy.write_text(transcript_text, encoding='utf-8')
        except Exception as e:
            log.warning(f"No se pudo copiar transcript a minutes: {e}")

        self._move_to_processed(wav_path, transcript_path)
        for extra in parts[1:]:
            try:
                extra.with_suffix('.title').unlink(missing_ok=True)
            except OSError:
                pass

        try:
            mins_text = minutes_path.read_text(encoding='utf-8')
            title, _ = extract_title_from_minutes(mins_text) if mins_text.startswith('TITULO:') else (minutes_path.stem, mins_text)
            export_to_html(minutes_path, title, participants=participants, open_browser=False)
        except Exception as e:
            log.warning(f"Error exportando HTML: {e}")

        try:
            import coaching_plugin
            coaching_plugin.grade_and_inject(transcript_text, minutes_path)
        except ImportError:
            pass
        except Exception as _ce:
            log.warning(f"coaching_plugin: {_ce}")

        s = _STR.get(get_ui_language(), _STR['en'])
        # Las acciones se sacan de las minutas completas: con minutas breves no
        # hay de donde. El proyecto se detecta igual, para archivar la reunion.
        with_actions = actions_on and minutes_mode == 'full'
        if with_actions:
            self._notify('Noted', s['action_items'])
        self._current_job.update({'step': 3, 'step_label': 'Generando acciones' if with_actions else 'Archivando',
                                  'step_started': time.time()})
        self.set_processing(s['action_items'] if with_actions else s['project_step'])

        _transcript_for_export = transcript_text
        _ready = s['ready'] if with_actions else (s['ready_minutes'] if minutes_mode == 'full' else s['ready_brief'])

        def on_done():
            self._current_job = {}
            self.set_processing('')
            self._notify('Noted', _ready)
            # Aquí ya existe el _actions.json con el project_id definitivo, así
            # que este es el único punto donde se decide la carpeta del proyecto.
            try:
                from project_context import sync_meeting_summary
                _pid = sync_meeting_summary(minutes_path)
                if _pid:
                    log.info(f"Reunión archivada en el proyecto '{_pid}'")
            except Exception as e:
                log.warning(f"sync_meeting_summary on_done: {e}")
            try:
                from project_exporter import export_to_project_folder
                if export_to_project_folder(minutes_path, _transcript_for_export):
                    log.info(f"Exportado a carpeta de proyecto: {minutes_path.stem}")
            except Exception as e:
                log.warning(f"project_exporter on_done: {e}")
            # Un reintento no abre el panel: llega en cualquier momento, quiza
            # compartiendo pantalla en una llamada con otro cliente.
            if retry_md is None:
                try:
                    from app_window import open_app
                    open_app(str(minutes_path), focus=False)
                except Exception as e:
                    log.warning(f"Error abriendo app: {e}")
            # Al final y en su propio hilo: es otra llamada a Claude y no debe
            # solaparse con el enriquecimiento ni retrasar el aviso de "listo".
            # Solo con minutas completas: con las minutas breves o apagadas, quien
            # usa Noted eligio no mandarle la reunion entera a Claude.
            if minutes_mode == 'full':
                threading.Thread(
                    target=self._suggest_glossary_terms,
                    args=(_transcript_for_export, content, participants, minutes_path.stem),
                    daemon=True, name='GlossarySuggest',
                ).start()

        if with_actions:
            enrich_and_save(minutes_path, PROJECT_DIR.parent, on_done=on_done)
        else:
            from actions_enricher import save_without_actions
            save_without_actions(minutes_path, PROJECT_DIR.parent, on_done=on_done,
                                 extra_text=transcript_text[:4000] if minutes_mode != 'full' else None)

        def _brain_update():
            try:
                from brain_synthesizer import update_brain_wiki
                if _proj and _proj.get('id'):
                    update_brain_wiki(_proj['id'], _proj.get('name', _proj['id']),
                                      minutes_path, detected_language)
                    log.info(f"Brain actualizado: {_proj['name']}")
            except Exception as e:
                log.warning(f"Brain synthesis no crítico: {e}")

        threading.Thread(target=_brain_update, daemon=True, name='BrainDream').start()

    def _suggest_glossary_terms(self, transcript: str, minutes_text: str,
                                participants: list, stem: str) -> None:
        """Deja como sugerencias pendientes las correcciones de nombres y siglas
        que Claude detecta en esta reunión. Nunca entran solas en el glosario:
        las aprueba el usuario desde Settings. Es un extra; si falla, se loguea."""
        try:
            import glossary
            n = glossary.suggest_from_meeting(
                transcript, minutes_text,
                [p['name'] for p in participants if p.get('name')], stem)
            if n:
                log.info(f"Glosario: {n} sugerencia(s) nueva(s) de {stem}")
        except Exception as e:
            log.warning(f"Glosario: sin sugerencias de {stem}: {e}")

    def _brief_minutes(self, transcript_text: str, wav_path: Path, mode: str, language: str, s: dict,
                       title: str | None = None, reason: str | None = None) -> str:
        """Minutas de los niveles 'summary' y 'off', y las 'pending' de unas
        completas que fallaron. Nunca fallan: si el resumen no sale, quedan las
        minimas, y la reunion se guarda y se ve igual."""
        from minutes_generator import brief_minutes
        title = title or self._fallback_title([wav_path], s)
        if mode == 'pending':
            return brief_minutes(title, 'pending', None, 'en' if language == 'en' else 'es',
                                 reason=reason, stem=wav_path.stem)
        summary = None
        if mode == 'summary':
            self._current_job.update({'step': 2, 'step_label': 'Resumiendo', 'step_started': time.time()})
            self.set_processing(s['summarizing'])
            try:
                import meeting_qa
                summary = meeting_qa.summarize(transcript_text)
            except Exception as e:
                log.warning(f"Resumen de la reunion fallido: {e}")
            if not summary:
                log.warning("Sin resumen de la reunion: se guardan minutas minimas")
        return brief_minutes(title, 'summary' if summary else 'off', summary,
                             'en' if language == 'en' else 'es')

    def _move_to_processed(self, wav_path: Path, transcript_path: Path):
        dest = RECORDINGS_DIR / 'processed'
        dest.mkdir(exist_ok=True)
        if wav_path.exists():
            try:
                shutil.move(str(wav_path), str(dest / wav_path.name))
            except Exception as e:
                log.warning(f"No se pudo mover {wav_path.name}: {e}")
        if transcript_path.exists():
            try:
                transcript_path.unlink()
            except Exception as e:
                log.warning(f"No se pudo eliminar {transcript_path.name}: {e}")
        for suffix in ('.lang', '.partial', '.context', '.live', '.title'):
            aux = wav_path.with_suffix(suffix)
            if aux.exists():
                try:
                    aux.unlink()
                except Exception:
                    pass

    def _pending_minutes_of(self, stem: str) -> Path | None:
        """Las minutas 'pending' de esta grabacion, si las hay (ver brief_minutes)."""
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})', stem)
        if not m:
            return None
        return next((md for md in MINUTES_DIR.glob(f"{''.join(m.group(1, 2, 3))}_{''.join(m.group(4, 5))}_*.md")
                     if self._still_pending(md, stem)), None)

    @staticmethod
    def _still_pending(md: Path, stem: str) -> bool:
        from minutes_generator import pending_stem_of
        try:
            with md.open(encoding='utf-8') as f:
                return pending_stem_of(f.read(600)) == stem
        except OSError:
            return False

    @staticmethod
    def _declared_title(md: Path) -> str | None:
        """La linea TITULO: de unas minutas (la que escribe rename_meeting), o None."""
        try:
            with md.open(encoding='utf-8') as f:
                for _ in range(3):
                    line = f.readline()
                    if line.startswith('TITULO:'):
                        return line.removeprefix('TITULO:').strip() or None
        except OSError:
            pass
        return None

    def _retry_pending_loop(self):
        time.sleep(RETRY_PENDING_FIRST_S)
        while True:
            try:
                self._retry_pending_minutes()
            except Exception as e:
                log.warning(f"Reintento de minutas pendientes: {e}", exc_info=True)
            time.sleep(RETRY_PENDING_EVERY_S)

    def _pipeline_idle(self) -> bool:
        with self._session_lock:
            sess = self._session
            open_session = bool(sess and not sess.get('finalized'))
        return not (self._recording_or_saving() or open_session or self._current_job
                    or self._processing_msg or self._pipeline_queued
                    or not self._pipeline_queue.empty())

    def _retry_pending_minutes(self):
        """Rehace las minutas que fallaron (marca 'pending') cuando Claude vuelve.
        El 28/10/2026 caduco la sesion de Claude y dos reuniones se quedaron sin
        minutas, sin acciones y sin coaching.

        Cada reunion se cierra aqui mismo con su transcript ya hecho (sin
        Whisper), de una en una y con Noted ocioso antes de cada una. No pasa por
        la sesion de reunion: una sesion abierta con el nombre de la reunion de
        ayer haria preguntar "Misma reunion detectada" al unirse a la de hoy, y
        las uniria. Solo con las minutas completas activadas, y como mucho
        RETRY_PENDING_MAX veces por reunion y arranque: una que falle por otra
        cosa no gasta Claude cada 15 minutos."""
        import config as _config
        from minutes_generator import claude_problem, pending_stem_of
        from storage import get_transcript_path
        if _config.minutes_mode() != 'full' or not self._pipeline_idle():
            return
        todo = []
        for md in sorted(MINUTES_DIR.glob('*.md')):
            try:
                with md.open(encoding='utf-8') as f:
                    stem = pending_stem_of(f.read(600))
            except OSError:
                continue
            if stem and self._pending_retries.get(stem, 0) < RETRY_PENDING_MAX:
                todo.append((md, stem))
        if not todo:
            return
        problem = claude_problem()
        if problem:
            log.info(f"{len(todo)} reunión(es) con minutas pendientes: Claude aún no está listo ({problem})")
            return
        for md, stem in todo:
            if not self._pipeline_idle():
                return
            self._pending_retries[stem] = self._pending_retries.get(stem, 0) + 1
            try:
                text = md.with_name(md.stem + '_transcript.txt').read_text(encoding='utf-8')
            except OSError as e:
                text, why = '', e
            else:
                why = 'el transcript está vacío'
            if not text.strip():
                log.warning(f"Minutas pendientes de {md.name}: no se pueden reintentar ({why})")
                self._pending_retries[stem] = RETRY_PENDING_MAX
                continue
            # El WAV puede estar en processed/ o ya borrado: no se vuelve a leer.
            wav = RECORDINGS_DIR / f'{stem}.wav'
            try:
                lang = get_transcript_path(wav).with_suffix('.lang').read_text().strip() or 'auto'
            except OSError:
                lang = 'auto'
            log.info(f"Minutas pendientes de {md.name}: se reintentan "
                     f"(intento {self._pending_retries[stem]} de {RETRY_PENDING_MAX})")
            self._finalize_session({'base_wav': wav, 'parts': [wav], 'transcripts': {stem: text},
                                    'lang': lang, 'key': '', 'awaiting': False, 'finalized': False,
                                    'timer': None, 'last_at': time.time(), 'pending_md': md})

    def _recover_pending(self):
        time.sleep(3)
        # Primero los .part que un cierre del daemon dejo sin convertir en WAV:
        # asi entran en la cola como cualquier otra grabacion pendiente.
        try:
            self._recorder.recover_orphan_parts(RECORDINGS_DIR)
        except Exception as e:
            log.error(f"No se pudieron recuperar las grabaciones a medio guardar: {e}")
        pending = []
        for wav in RECORDINGS_DIR.glob('*.wav'):
            # Las pistas .mic.wav / .loop.wav acompañan a una grabacion: no son
            # reuniones. Encolarlas daba minutas fantasma (las del 15/10 15:17
            # salieron tres veces: .wav, .mic.wav y .loop.wav por separado). Se
            # reconocen por su grabacion principal, no solo por el nombre: un
            # titulo puede acabar en ".mic" y esa si es una reunion.
            if wav.name.endswith(('.mic.wav', '.loop.wav')):
                base = wav.name.rsplit('.', 2)[0] + '.wav'
                if (RECORDINGS_DIR / base).exists() or (RECORDINGS_DIR / 'processed' / base).exists():
                    continue
            stem_ts = wav.stem[:16]
            ts_compact = stem_ts[:4] + stem_ts[5:7] + stem_ts[8:10] + '_' + stem_ts[11:13] + stem_ts[14:16]
            has_minutes = any(MINUTES_DIR.glob(f"{glob.escape(ts_compact)}_*.md"))
            if not has_minutes:
                pending.append(wav)

        if pending:
            # Sin toast: reaparecia en cada reinicio del daemon aunque las
            # grabaciones se reanudan solas y el icono ya muestra el progreso.
            log.info(f"{len(pending)} grabacion(es) pendiente(s) de procesar: se reanudan")
            for wav in pending:
                self._pipeline_queue.put(wav)
        self._refresh_disk_reserve()

    def _toggle_recording(self):
        if self._recorder.is_recording:
            self._recorder.stop()
            self.set_recording(False)
        else:
            self._start_manual('background')

    def _record_now_live(self):
        """'Grabar ahora (en vivo)': como 'Grabar ahora', pero con el transcript ya.
        Metodo propio y sin argumentos: pystray decide que pasarle a cada accion
        por su numero de parametros."""
        if not self._recorder.is_recording:
            self._start_manual('live')

    def _start_manual(self, mode: str):
        from storage import get_recording_path
        path = get_recording_path('manual')
        # on_recording_stopped se asigna una sola vez al inicio (en main.py)
        if self._recorder.start(path) is False:
            return      # ya se grababa: el aviso de Teams se acepto un instante antes
        self.set_recording(True, path, mode=mode)
        threading.Thread(target=self.warn_if_mic_muted, daemon=True, name='MicMuteCheck').start()

        def _send_notice():
            try:
                import teams_chat as _tc
                _tc.send_recording_notice()
            except Exception as _e:
                log.warning(f"TeamsChatNotice: {_e}")
        threading.Thread(target=_send_notice, daemon=True, name='TeamsChatNotice').start()

    def _active_job_stem(self) -> str:
        """Trabajo que se puede descartar ahora: el que se procesa, o el primero
        de la cola."""
        stem = (self._current_job or {}).get('stem')
        if stem:
            return stem
        return self._pipeline_queued[0] if self._pipeline_queued else ''

    def _cancel_recording(self):
        # Mientras graba, se descarta el audio sin guardar. Una vez parada, lo
        # que queda por descartar es el trabajo del pipeline.
        if self._recorder.is_recording:
            path = self._recording_path
            # La marca, antes que nada: si los .part no se dejan borrar (el proceso
            # en vivo aun leyendolos, un antivirus), recover_orphan_parts los
            # borraria en el siguiente arranque en vez de recuperar la reunion.
            marker = path.with_suffix('.discarded') if path is not None else None
            if marker is not None:
                try:
                    marker.touch()
                except OSError as e:
                    log.warning(f"No se pudo marcar la grabacion descartada: {e}")
            # cancel() primero: es lo que deja sin efecto un stop() simultaneo.
            # Despues el proceso en vivo, esperando a que muera, y se borra lo
            # que dejo: su texto y los .part, por si el borrado de cancel()
            # coincidio con una lectura suya. La reunion se descarto.
            self._recorder.cancel()
            self._stop_live(wait=True)
            if path is not None:
                try:
                    import live_transcriber
                    live_transcriber.discard_artifacts(path, parts=True)
                    if not any(live_transcriber.part_path(path, t).exists() for t in live_transcriber.TRACKS):
                        marker.unlink(missing_ok=True)
                except Exception as e:
                    log.warning(f"No se pudo limpiar la transcripcion en vivo descartada: {e}")
            self.set_recording(False)
            if path is not None:
                # Lo que apunto el ticker (_note_teams_title): sin reunion que
                # cerrar, nadie lo recogeria.
                try:
                    path.with_suffix('.title').unlink(missing_ok=True)
                except OSError:
                    pass
            return
        stem = self._active_job_stem()
        if stem:
            self.cancel_job(stem)
            s = _STR.get(get_ui_language(), _STR['en'])
            self._notify('Noted', s['job_cancelled'])

    def _add_context(self):
        rec_path = self._recording_path
        if not rec_path:
            return
        threading.Thread(target=lambda: self._show_context_dialog(rec_path), daemon=True).start()

    def _show_context_dialog(self, rec_path: Path):
        import tkinter as tk
        from tk_thread import get_root, run_in_tk

        BG = '#1e1e2e'; CARD = '#313244'; FG = '#cdd6f4'; MUTED = '#a6adc8'
        BORDER = '#585b70'; BTN = '#89b4fa'
        s = _STR.get(get_ui_language(), _STR['en'])

        def _create():
            root = get_root()
            top = tk.Toplevel(root)
            top.overrideredirect(True)
            top.attributes('-topmost', True)
            top.attributes('-alpha', 0.96)
            top.lift()
            W, H = 360, 130
            sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
            top.geometry(f"{W}x{H}+{sw - W - 20}+{sh - H - 64}")
            top.configure(bg=BORDER)

            frame = tk.Frame(top, bg=BG, padx=14, pady=10)
            frame.pack(fill='both', expand=True, padx=1, pady=1)

            tk.Label(frame, text=s['ctx_title'],
                     font=('Segoe UI', 10, 'bold'), fg=FG, bg=BG).pack(anchor='w')
            tk.Label(frame, text=s['ctx_subtitle'],
                     font=('Segoe UI', 8), fg=MUTED, bg=BG).pack(anchor='w', pady=(2, 6))

            entry = tk.Entry(frame, font=('Segoe UI', 9), bg=CARD, fg=FG,
                             relief='flat', insertbackground=FG, bd=4)
            entry.pack(fill='x')
            entry.focus_set()

            def save():
                ctx = entry.get().strip()
                if ctx:
                    try:
                        rec_path.with_suffix('.context').write_text(ctx, encoding='utf-8')
                        log.info(f"Contexto guardado para {rec_path.name}")
                    except Exception as e:
                        log.warning(f"No se pudo guardar contexto: {e}")
                top.destroy()

            entry.bind('<Return>', lambda _: save())
            entry.bind('<Escape>', lambda _: top.destroy())

            btn_f = tk.Frame(frame, bg=BG)
            btn_f.pack(fill='x', pady=(8, 0))
            tk.Button(btn_f, text=s['ctx_save'], font=('Segoe UI', 9, 'bold'),
                      bg=BTN, fg=BG, relief='flat', cursor='hand2',
                      command=save).pack(side='left', padx=(0, 8))
            tk.Button(btn_f, text=s['ctx_cancel'], font=('Segoe UI', 9),
                      bg='#45475a', fg=FG, relief='flat', cursor='hand2',
                      command=top.destroy).pack(side='left')

        run_in_tk(_create)

    def _open_actions_ui(self):
        try:
            from app_window import open_app
            open_app()
        except Exception as e:
            log.error(f"Error abriendo app: {e}")

    def warn_loopback_unavailable(self, reason: str = ''):
        """La grabación va a salir sin las voces de los demás: avisar en el momento,
        no cuando el usuario descubra el transcript incompleto días después."""
        log.error(f"Grabando sin audio del sistema: {reason}")
        s = _STR.get(get_ui_language(), _STR['en'])
        self._notify('Noted', s['mic_only'])

    def warn_mic_unavailable(self, reason: str = ''):
        log.error(f"Micrófono no disponible: {reason}")
        s = _STR.get(get_ui_language(), _STR['en'])
        self._notify('Noted ⚠', s['mic_unavailable'])

    def warn_disk_full(self, reason: str = ''):
        """Un writer o el guardado no pueden escribir. Es justo para lo que esta
        la reserva: tambien fuera de una grabacion (un guardado esperando sitio),
        donde el ticker no mira el disco."""
        log.error(f"No se puede escribir el audio: {reason}")
        released, low = 0.0, True
        try:
            import disk_guard
            free = disk_guard.free_gb()
            # Un antivirus que bloquea el fichero no es disco lleno: ni se gasta
            # la reserva ni se dice "disco lleno".
            low = free is None or free < disk_guard.WARN_GB
            if low:
                released = disk_guard.release_reserve()
        except Exception as e:
            log.warning(f"No se pudo liberar la reserva de disco: {e}")
        if self._toast_due('disk_full'):
            s = _STR.get(get_ui_language(), _STR['en'])
            msg = (s['disk_full_reserve'].format(released=released) if released
                   else s['disk_full'] if low else s['write_failed'])
            self._notify('Noted ⚠', msg)

    def warn_disk_recovered(self):
        log.warning("Se puede escribir otra vez: el audio está guardado completo")
        if self._toast_due('disk_recovered'):
            s = _STR.get(get_ui_language(), _STR['en'])
            self._notify('Noted', s['disk_recovered'])
            # Si vuelve a llenarse, que se diga otra vez aunque no hayan pasado
            # DISK_TOAST_EVERY_S: el ultimo aviso visible no puede ser "todo bien".
            self._toast_last.pop('disk_full', None)

    def warn_save_failed(self, reason: str = ''):
        log.error(f"Grabación sin guardar (los .part se conservan): {reason}")
        s = _STR.get(get_ui_language(), _STR['en'])
        self._notify('Noted ⚠', s['save_failed'])

    def _toast_due(self, key: str) -> bool:
        """True si este aviso no salio en los ultimos DISK_TOAST_EVERY_S."""
        last = getattr(self, '_toast_last', None)
        if last is None:
            last = self._toast_last = {}
        now = time.monotonic()
        if now - last.get(key, -DISK_TOAST_EVERY_S) < DISK_TOAST_EVERY_S:
            return False
        last[key] = now
        return True

    def _notify(self, title: str, msg: str):
        try:
            if self._icon:
                self._icon.notify(msg, title)
        except Exception:
            pass

    def _quit(self):
        if self._recorder.is_recording:
            self._recorder.stop()
            self.set_recording(False)
            self._recorder.wait_for_save(60)
        self._pipeline_queue.put(None)
        self._detector.stop()
        try:
            if self._icon:
                self._icon.stop()
        except Exception:
            pass
