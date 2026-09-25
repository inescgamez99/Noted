import json
import logging
import queue
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from config import MINUTES_DIR, RECORDINGS_DIR, PROJECT_DIR, get_ui_language
from transcriber import TranscriptionCancelled as _Cancelled

# Trabajos descartados desde la ventana web, que corre en otro proceso.
_CANCEL_FILE = PROJECT_DIR / '.cancelled_jobs.txt'


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
        add_context='Añadir contexto a grabacion',
        cancel_recording='Cancelar grabacion (sin guardar)',
        view_minutes='Abrir la aplicacion',
        quit='Salir',
        recordings_queued='{n} grabaciones en cola',
        recordings_pending='{n} grabacion(es) pendiente(s) de procesar',
        action_items='Generando acciones...',
        ready='Minutas y acciones listas',
        transcription_failed='No se pudo transcribir la grabación. Revisa el log para más detalles.',
        no_speech='La grabación no contiene voz: no se ha generado transcripción.',
        minutes_failed='No se pudieron generar las minutas. Revisa el log para más detalles.',
        mic_only='Grabando SOLO tu microfono: no se captura el audio de los demas. Si usas auriculares, sus voces no quedaran en la grabacion.',
        mic_unavailable='No se pudo acceder al micrófono. Comprueba que no lo usa otra app.',
        disk_full='Disco lleno: la grabación se ha interrumpido. Libera espacio.',
        cancel_job='Descartar esta reunion (papelera)',
        job_cancelled='Reunion descartada: el audio y lo generado se han movido a la papelera.',
    ),
    'en': dict(
        record_now='Record now', stop='Stop recording',
        add_context='Add context to recording',
        cancel_recording='Cancel recording (discard)',
        view_minutes='Open the app',
        quit='Quit',
        recordings_queued='{n} recordings in queue',
        recordings_pending='{n} recording(s) pending processing',
        action_items='Generating action items...',
        ready='Meeting minutes & action items ready',
        transcription_failed='Transcription failed. Check the log for details.',
        no_speech='No speech in the recording: no transcript was produced.',
        minutes_failed='Minutes generation failed. Check the log for details.',
        mic_only='Recording your microphone ONLY — system audio is not being captured. If you are on a headset, the others will not be in the recording.',
        mic_unavailable='Could not access the microphone. Check that no other app is using it.',
        disk_full='Disk full: the recording has stopped. Free up some space.',
        cancel_job='Discard this meeting (bin)',
        job_cancelled='Meeting discarded: the audio and generated files were moved to the bin.',
    ),
}

log = logging.getLogger(__name__)


def _make_icon(color: str):
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 4, 60, 60], fill=color)
    return img


_ICON_IDLE       = _make_icon('#6c7086')
_ICON_RECORDING  = _make_icon('#f38ba8')
_ICON_PROCESSING = _make_icon('#f9e2af')


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

        threading.Thread(target=self._pipeline_loop, daemon=True, name='PipelineWorker').start()
        threading.Thread(target=self._recover_pending, daemon=True, name='PipelineRecovery').start()
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
                pystray.MenuItem(s['add_context'],
                                 self._add_context,
                                 visible=lambda _: bool(self._recorder.is_recording)),
                pystray.MenuItem(lambda _: (s['cancel_recording'] if self._recorder.is_recording
                                            else s['cancel_job']),
                                 self._cancel_recording,
                                 visible=lambda _: bool(self._recorder.is_recording
                                                        or self._active_job_stem())),
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
        if self._recording_start:
            elapsed = int(time.time() - self._recording_start)
            m, s = divmod(elapsed, 60)
            _rm = _re.match(r'\d{4}-\d{2}-\d{2}_(\d{2})-(\d{2})(?:_(.+))?', self._recording_path.stem) if self._recording_path else None
            _rtitle = _rm.group(3).replace('_', ' ').title() if (_rm and _rm.group(3)) else None
            _rtime  = f"{_rm.group(1)}:{_rm.group(2)}" if _rm else None
            jobs.append({'stage': 'recording', 'label': f'Recording {m:02d}:{s:02d}', 'elapsed': elapsed, 'pct': None,
                         'title': _rtitle, 'time': _rtime, 'step': 0, 'total_steps': 3, 'step_label': 'Grabando'})
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

    def set_recording(self, active: bool, path: Path = None):
        if active:
            self._recording_path = path
            self._recording_start = time.time()
            self._ticker_stop.clear()
            t = threading.Thread(target=self._ticker, daemon=True, name='TrayTicker')
            t.start()
            self._set_icon(_ICON_RECORDING, 'Noted - Recording 00:00')
        else:
            self._recording_path = None
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

    def set_processing(self, msg: str = ''):
        self._processing_msg = msg
        self._write_status()
        if not self._recording_start:
            if msg:
                self._set_icon(_ICON_PROCESSING, f'Noted - {msg}')
            else:
                self._set_icon(_ICON_IDLE, 'Noted')

    def _set_icon(self, img, tooltip: str):
        try:
            if self._icon:
                self._icon.icon = img
                self._icon.title = tooltip
        except Exception:
            pass

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
                        self._icon.title = tooltip
                except Exception:
                    pass
            self._write_status()
            self._check_pending_notification()

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
            for folder in (RECORDINGS_DIR, RECORDINGS_DIR / 'processed'):
                if folder.exists():
                    candidates.extend(folder.glob(f"{stem}.*"))
                    candidates.extend(folder.glob(f"{stem}_transcript.*"))
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
            title = (m.group(6).replace('_', ' ').title() if (m and m.group(6)) else stem)
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
                self._run_pipeline_sync(wav_path)
            except _Cancelled as c:
                # Descartada a mitad del proceso. c.minutes_path apunta a las
                # minutas si ya se habían escrito, para que también se vayan.
                self._discard_job(wav_path, 'descartada durante el proceso',
                                  getattr(c, 'minutes_path', None))
            except Exception as e:
                log.error(f"Pipeline error: {e}", exc_info=True)
                self.set_processing('')

    def _run_pipeline_sync(self, wav_path: Path):
        from storage import get_transcript_path
        from transcriber import partial_resume, transcribe

        s = _STR.get(get_ui_language(), _STR['en'])
        transcript_path = get_transcript_path(wav_path)
        partial_path = wav_path.parent / f"{wav_path.stem}.partial"

        detected_language = 'auto'

        _m = re.match(r'\d{4}-\d{2}-\d{2}_(\d{2})-(\d{2})(?:_(.+))?', wav_path.stem)
        _job_time  = f"{_m.group(1)}:{_m.group(2)}" if _m else ''
        _job_title = _m.group(3).replace('_', ' ').title() if (_m and _m.group(3)) else wav_path.stem
        # 'stem' viaja hasta .pipeline_status.json para que la ventana web sepa
        # qué trabajo está cancelando.
        self._current_job = {'stem': wav_path.stem, 'title': _job_title, 'time': _job_time,
                             'step': 1, 'total_steps': 3, 'step_label': 'Transcribiendo',
                             'step_started': time.time()}

        lang_path = transcript_path.with_suffix('.lang')
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
                                resume_from=partial_path)
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
                self.set_processing('')
                return

            transcript_path.write_text(transcript_text, encoding='utf-8')

        self._register_part(wav_path, transcript_text, detected_language)

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

    def has_pending_session(self, meeting_name: str) -> bool:
        with self._session_lock:
            sess = self._session
            if not sess or sess.get('finalized'):
                return False
            key = self._norm_key(meeting_name)
            skey = sess.get('key') or ''
            if not key or not skey:
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
        from storage import get_transcript_path, get_minutes_path
        from minutes_generator import generate_minutes, extract_title_from_minutes, save_minutes
        from html_exporter import export_to_html
        from actions_enricher import enrich_and_save

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
        transcript_path = get_transcript_path(wav_path)
        try:
            transcript_path.write_text(transcript_text, encoding='utf-8')
        except Exception:
            pass

        _mj = re.match(r'\d{4}-\d{2}-\d{2}_(\d{2})-(\d{2})(?:_(.+))?', wav_path.stem)
        self._current_job = {
            'title': (_mj.group(3).replace('_', ' ').title() if (_mj and _mj.group(3)) else wav_path.stem),
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
                        extra.with_suffix('.context')):
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

        _proj, _ctx_dir = None, None
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
        participants = []
        try:
            from outlook_sender import find_meeting_participants
            _pm = re.match(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})', wav_path.stem)
            if _pm:
                from datetime import datetime as _dt
                _rec_time = _dt(int(_pm.group(1)), int(_pm.group(2)), int(_pm.group(3)),
                                int(_pm.group(4)), int(_pm.group(5)))
                _pnm = re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(.+)', wav_path.stem)
                _rec_name = _pnm.group(1).replace('_', ' ') if _pnm else None
                if _rec_name and _rec_name.strip().lower() in ('manual', 'recording'):
                    _rec_name = None
                participants = find_meeting_participants(_rec_time, meeting_name=_rec_name)
                log.info(f"Participantes detectados: {len(participants)}")
        except Exception as e:
            log.warning(f"No se pudieron detectar participantes: {e}")

        # Última salida antes del paso caro: generar minutas es una llamada a
        # Claude, y no tiene sentido pagarla si la reunión ya está descartada.
        if self._is_cancelled(wav_path.stem):
            raise _Cancelled()

        self._current_job.update({'step': 2, 'step_label': 'Generando minutas', 'step_started': time.time()})
        self.set_processing('Generando minutas...')
        raw = generate_minutes(transcript_text, wav_path, extra_context=extra_context,
                               language=detected_language, context_dir=_ctx_dir,
                               participants=participants)
        if not raw:
            log.error("Generación de minutas fallida")
            self._notify('Noted ⚠', s['minutes_failed'])
            self.set_processing('')
            return

        title, content = extract_title_from_minutes(raw)
        minutes_path = get_minutes_path(wav_path, title)
        save_minutes(content, minutes_path)

        # Si se descartó mientras Claude generaba, las minutas ya están en disco:
        # se adjuntan a la excepción para que se vayan a la papelera con el resto.
        if self._is_cancelled(wav_path.stem):
            exc = _Cancelled()
            exc.minutes_path = minutes_path
            raise exc

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
        self._notify('Noted', s['action_items'])
        self._current_job.update({'step': 3, 'step_label': 'Generando acciones', 'step_started': time.time()})
        self.set_processing(s['action_items'])

        _transcript_for_export = transcript_text

        def on_done():
            self._current_job = {}
            self.set_processing('')
            self._notify('Noted', s['ready'])
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
            try:
                from app_window import open_app
                open_app(str(minutes_path))
            except Exception as e:
                log.warning(f"Error abriendo app: {e}")

        enrich_and_save(minutes_path, PROJECT_DIR.parent, on_done=on_done)

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
        for suffix in ('.lang', '.partial', '.context'):
            aux = wav_path.with_suffix(suffix)
            if aux.exists():
                try:
                    aux.unlink()
                except Exception:
                    pass

    def _recover_pending(self):
        time.sleep(3)
        pending = []
        for wav in RECORDINGS_DIR.glob('*.wav'):
            stem_ts = wav.stem[:16]
            ts_compact = stem_ts[:4] + stem_ts[5:7] + stem_ts[8:10] + '_' + stem_ts[11:13] + stem_ts[14:16]
            has_minutes = any(MINUTES_DIR.glob(f"{ts_compact}_*.md"))
            if not has_minutes:
                pending.append(wav)

        if pending:
            s = _STR.get(get_ui_language(), _STR['en'])
            self._notify('Noted', s['recordings_pending'].format(n=len(pending)))
            for wav in pending:
                self._pipeline_queue.put(wav)

    def _toggle_recording(self):
        if self._recorder.is_recording:
            self._recorder.stop()
            self.set_recording(False)
        else:
            from storage import get_recording_path
            path = get_recording_path('manual')
            # on_recording_stopped se asigna una sola vez al inicio (en main.py)
            self._recorder.start(path)
            self.set_recording(True, path)
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
            self._recorder.cancel()
            self.set_recording(False)
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

            tk.Label(frame, text="Contexto / objetivo de la reunion",
                     font=('Segoe UI', 10, 'bold'), fg=FG, bg=BG).pack(anchor='w')
            tk.Label(frame, text="Se usara al generar las minutas",
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
            tk.Button(btn_f, text="Guardar", font=('Segoe UI', 9, 'bold'),
                      bg=BTN, fg=BG, relief='flat', cursor='hand2',
                      command=save).pack(side='left', padx=(0, 8))
            tk.Button(btn_f, text="Cancelar", font=('Segoe UI', 9),
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
        log.error(f"Disco lleno durante grabación: {reason}")
        s = _STR.get(get_ui_language(), _STR['en'])
        self._notify('Noted ⚠', s['disk_full'])

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
