import atexit

import atomic_io

import glob

import json

import logging

import os

import re

import shutil

import subprocess

import tempfile

import threading

import uuid

from datetime import datetime, timedelta

from pathlib import Path



from config import MINUTES_DIR, PROJECT_DIR, RECORDINGS_DIR, get_claude_bin as _get_claude_bin, clean_env as _clean_env_panel
from exporters import semantic_layer as semantic



log = logging.getLogger(__name__)



_PWSH = next(

    (p for p in [r'C:\Program Files\PowerShell\7\pwsh.exe', shutil.which('pwsh')]

     if p and Path(p).exists()),

    'powershell'

)

_WEB_DIR = Path(__file__).parent / 'web'



# Instancia global de la ventana (singleton)

_window = None

_window_lock = threading.Lock()



# Estado de ejecuciones de acciones en el panel interno

_action_runs: dict = {}



# Estado de regeneración de minutas: path -> {pct, stage_key, done, error}

_regen_runs: dict = {}



# Estado de regeneración de acciones (con prompt del usuario): path -> {pct, stage, done, error}

_actions_regen_runs: dict = {}



# Estado de importaciones de transcript: run_id -> {pct, stage, done, error, path}

_import_runs: dict = {}

# Preguntas sobre una reunion (meeting_qa): run_id -> {text, done, error, upto}.
# Solo en memoria: las preguntas no se guardan (decision de Felipe, 19/10/2026).
_ask_runs: dict = {}

# Una sesion de claude abierta por conversacion ('live:<stem>' / 'meeting:<ruta>'):
# arrancarla cuesta 9,5-12 s, asi que se abre al mostrar la caja de preguntas.
_ask_sessions: dict = {}
_ask_sessions_lock = threading.Lock()
_MAX_ASK_SESSIONS = 2
# Conversaciones cuya vista se cerro: si su pregunta vuelve sin sesion, no se
# lanza la red de una sola vez (nadie va a ver la respuesta).
_ask_closed: set = set()
# El barrido que cierra las sesiones sin usar. Antes solo se miraban al abrir
# otra conversacion: el 27/10/2026 un panel abierto desde la tarde anterior
# tenia dos claude (~480 MB de reserva cada uno) con el disco a 0,86 GB.
_ASK_SWEEP_S = 30
_ask_sweeper = None
_ask_yielding = False


def _recording_now() -> bool:
    """.recording_active, si su PID es un Python vivo: si el daemon murio
    grabando, el archivo se queda hasta la siguiente grabacion. El criterio de
    Get-TRLivePid (tr_env.ps1)."""
    try:
        pid = int((PROJECT_DIR / '.recording_active').read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return False
    try:
        import psutil
    except ImportError:
        return True
    try:
        return 'python' in psutil.Process(pid).name().lower()
    except psutil.Error:
        return False


def _ask_yield_reason() -> str | None:
    """'disco' o 'memoria' si se esta grabando y falta: grabar va antes (decision
    de Felipe, 26/10/2026), asi que entonces no se deja ningun claude abierto.
    Los umbrales, los de siempre: el aviso de disco, con su histeresis, y el
    suelo de memoria del modo de fondo."""
    global _ask_yielding
    reason = None
    if _recording_now():
        import disk_guard
        import live_transcriber
        free = disk_guard.system_free_gb()
        limit = disk_guard.WARN_CLEAR_GB if _ask_yielding else disk_guard.WARN_GB
        if free is not None and free < limit:
            reason = 'disco'
        elif not live_transcriber._memory_floor_ok():
            reason = 'memoria'
    _ask_yielding = reason is not None
    return reason


def _sweep_ask_sessions() -> None:
    """Cierra las que llevan IDLE_S sin usarse o han muerto y, grabando con poco
    disco o memoria, todas. Nunca una que este respondiendo."""
    reason = _ask_yield_reason()
    to_close = []
    with _ask_sessions_lock:
        for k, s in list(_ask_sessions.items()):
            if not s.busy() and (reason or s.idle() or not s.alive()):
                to_close.append((k, _ask_sessions.pop(k)))
    for k, s in to_close:
        log.info(f"meeting_qa: sesion {k} cerrada ({'grabando con poco ' + reason if reason else 'sin usar'})")
        s.close()


def _ask_sweep_loop() -> None:
    import time
    global _ask_sweeper
    while True:
        time.sleep(_ASK_SWEEP_S)
        try:
            _sweep_ask_sessions()
        except Exception as e:
            log.warning(f"meeting_qa: barrido de sesiones: {e}")
        with _ask_sessions_lock:
            if not _ask_sessions:
                _ask_sweeper = None
                return


def _ensure_ask_sweeper() -> None:
    """Con _ask_sessions_lock tomado: corre mientras haya sesiones abiertas."""
    global _ask_sweeper
    if _ask_sweeper is None:
        t = threading.Thread(target=_ask_sweep_loop, daemon=True, name='AskSweeper')
        try:
            t.start()
        except RuntimeError as e:             # sin memoria: se reintenta con la siguiente sesion
            log.warning(f"meeting_qa: no se pudo arrancar el barrido de sesiones: {e}")
            return
        _ask_sweeper = t


def _ask_session(key: str, create: bool = True):
    """La sesion de esa conversacion, abriendola si hace falta. De paso cierra
    las que llevan IDLE_S sin usarse o han muerto, y la de un modelo que ya no
    es el de Ajustes. Nunca cierra una que este respondiendo ni la en vivo por
    hacer sitio (revision del 20/10/2026). Los cierres, fuera del lock: cada
    uno puede tardar hasta 3 s."""
    import time
    import meeting_qa
    import transcriber
    to_close = []
    with _ask_sessions_lock:
        for k, s in list(_ask_sessions.items()):
            if k != key and (s.idle() or not s.alive()):
                to_close.append(_ask_sessions.pop(k))
        s = _ask_sessions.get(key)
        if s is not None and not s.busy() and (not s.alive() or s.model != meeting_qa.qa_model()):
            to_close.append(_ask_sessions.pop(key))
            s = None
        if s is not None:
            s.last_used = time.monotonic()    # pedirla es usarla: que el barrido no la cierre ahora
        elif create:
            s = _ask_sessions[key] = meeting_qa.Session(me_label=transcriber.ME_NAME or 'Grabador')
            _ensure_ask_sweeper()
            # Cada sesion es un proceso: como mucho dos abiertas (la de la reunion
            # en curso y la ultima consultada). Si las demas estan ocupadas o son
            # la en vivo, se pasa del tope un rato antes que cortar una respuesta.
            while len(_ask_sessions) > _MAX_ASK_SESSIONS:
                spare = [k for k in _ask_sessions
                         if k != key and not k.startswith('live:') and not _ask_sessions[k].busy()]
                if not spare:
                    break
                to_close.append(_ask_sessions.pop(min(spare, key=lambda k: _ask_sessions[k].last_used)))
    for old in to_close:
        old.close()
    return s


def _close_ask_sessions() -> None:
    with _ask_sessions_lock:
        for s in _ask_sessions.values():
            s.close()
        _ask_sessions.clear()


atexit.register(_close_ask_sessions)

# La integración Semantic Layer (estado, helpers y lógica) vive en
# exporters/semantic_layer/. AppAPI solo delega en el módulo `semantic`.

# Watchers para acciones completadas desde terminal externo

_terminal_watchers: dict = {}       # (path, index) -> True

_terminal_completions: list = []    # [{path, index, title}] pendientes de notificar a JS



_MAX_RUNS = 50  # límite de entradas en _action_runs y _regen_runs





def _start_terminal_watcher(path: str, index: int, title: str = '') -> None:

    """Polls the actions JSON every 3s until the action is marked executed=True."""

    key = (path, index)

    if key in _terminal_watchers:

        return

    _terminal_watchers[key] = True



    def _watch():

        import time

        from pathlib import Path as _P

        from actions_enricher import get_actions_path

        ap = get_actions_path(_P(path))

        deadline = time.time() + 7200  # stop after 2h max

        while key in _terminal_watchers and time.time() < deadline:

            time.sleep(3)

            try:

                data = json.loads(ap.read_text(encoding='utf-8'))

                for a in data.get('actions', []):

                    if a.get('index') == index and a.get('executed'):

                        _terminal_completions.append({'path': path, 'index': index, 'title': title})

                        _terminal_watchers.pop(key, None)

                        return

            except Exception:

                pass

        _terminal_watchers.pop(key, None)



    threading.Thread(target=_watch, daemon=True, name=f'TermWatch-{index}').start()





def _prune_runs(d: dict) -> None:

    """Elimina las entradas completadas más antiguas cuando el dict supera el límite."""

    if len(d) <= _MAX_RUNS:

        return

    done_keys = [k for k, v in d.items() if v.get('done')]

    for k in done_keys[:len(d) - _MAX_RUNS]:

        del d[k]





def _cleanup_old_tmp_dirs() -> None:

    """Limpia directorios temporales de sesiones anteriores (más de 24h)."""

    try:

        tmp_base = Path(tempfile.gettempdir())

        cutoff = datetime.now() - timedelta(hours=24)

        for d in tmp_base.iterdir():

            if not d.is_dir():

                continue

            if datetime.fromtimestamp(d.stat().st_mtime) >= cutoff:

                continue

            if any((d / n).exists() for n in ('prompt.txt', 'run.ps1', 'chat.ps1', 'context.txt')):

                shutil.rmtree(d, ignore_errors=True)

    except Exception:

        pass





def _pin_key(stem: str) -> str:

    """Extrae YYYYMMDD_HHMM del stem del archivo como clave de pin."""

    m = re.search(r'(\d{8}_\d{4})', stem)

    return m.group(1) if m else stem


def _brief_mode(md: Path) -> str | None:
    """'summary', 'off' o 'pending' si las minutas son breves (minutes_generator.brief_minutes)."""
    try:
        with md.open(encoding='utf-8') as f:
            head = f.read(600)
    except OSError:
        return None
    from minutes_generator import brief_mode_of
    return brief_mode_of(head)





def _backup_actions(ap: Path) -> Path | None:
    """Copia `_actions.json` a `.bak` antes de borrarlo para regenerar: ahi
    estan in_panel, executed, las acciones manuales y lo editado a mano."""
    if not ap.exists():
        return None
    bak = ap.with_name(ap.name + '.bak')
    try:
        shutil.copy2(ap, bak)
        return bak
    except OSError as e:
        log.warning(f"No se pudo copiar {ap.name} a .bak: {e}")
        return None


def _actions_file_valid(ap: Path) -> bool:
    try:
        data = json.loads(ap.read_text(encoding='utf-8'))
    except Exception:
        return False
    return isinstance(data, dict) and isinstance(data.get('actions'), list)


def _finish_actions_regen(ap: Path, bak: Path | None) -> None:
    """Tras regenerar: si no hay un `_actions.json` valido, se restaura el
    original; si lo hay, el `.bak` ya no hace falta."""
    if bak is None:
        return
    try:
        if _actions_file_valid(ap):
            bak.unlink(missing_ok=True)
        else:
            os.replace(bak, ap)
            log.warning(f"{ap.name}: la regeneracion no produjo acciones validas; se restaura el original")
    except OSError as e:
        log.warning(f"No se pudo restaurar {ap.name} desde .bak: {e}")


class AppAPI:

    """API Python expuesta a JavaScript via pywebview."""

    def __init__(self):
        self._meetings_cache: dict[tuple, dict] = {}

    def _pins_path(self) -> Path:

        return PROJECT_DIR / 'pins.json'



    def _load_pins(self) -> set:

        try:

            p = self._pins_path()

            if p.exists():

                return set(json.loads(p.read_text(encoding='utf-8')))

        except Exception:

            pass

        return set()



    def toggle_pin(self, path: str) -> bool:

        key = _pin_key(Path(path).stem)

        pins = self._load_pins()

        if key in pins:

            pins.discard(key)

            state = False

        else:

            pins.add(key)

            state = True

        self._pins_path().write_text(json.dumps(sorted(pins)), encoding='utf-8')

        return state



    @staticmethod

    def _title_from_content(md: Path) -> str:

        """Título declarado dentro del .md, si lo hay.



        El panel mostraba el título deducido del nombre del fichero, pero

        rename_meeting() solo reescribe la línea TITULO: y no renombra el

        fichero (arrastraría el .html, el _actions.json y el _transcript.txt).

        Con lo cual una reunión renombrada seguía apareciendo con su nombre

        viejo y el usuario no la encontraba.

        """

        try:

            with md.open(encoding='utf-8') as f:

                for _ in range(3):

                    line = f.readline()

                    if not line:

                        break

                    if line.startswith('TITULO:'):

                        return line[len('TITULO:'):].strip()

        except Exception:

            pass

        return ''



    def download_pdf(self, md_path: str) -> dict:
        """Genera PDF con Edge headless, pregunta dónde guardarlo y abre la carpeta."""
        import shutil
        import tempfile
        md   = Path(md_path)
        html = md.with_suffix('.html')
        if not html.exists():
            return {'ok': False, 'error': 'HTML no encontrado'}

        edge = next(
            (p for p in [
                r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
                shutil.which('msedge') or '',
            ] if p and Path(p).exists()),
            None,
        )
        if not edge:
            return {'ok': False, 'error': 'Microsoft Edge no encontrado'}

        # Generar PDF en carpeta temporal
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_pdf = Path(tmp) / 'output.pdf'
                subprocess.run(
                    [edge, '--headless', '--disable-gpu',
                     f'--print-to-pdf={tmp_pdf}',
                     '--no-pdf-header-footer',
                     f'--user-data-dir={tmp}',
                     html.as_uri()],
                    capture_output=True, timeout=30,
                )
                if not tmp_pdf.exists():
                    return {'ok': False, 'error': 'Edge no generó el PDF'}

                # Diálogo Save As via PowerShell con form TopMost para que no quede detrás
                ps = (
                    'Add-Type -AssemblyName System.Windows.Forms;'
                    '$d = New-Object System.Windows.Forms.SaveFileDialog;'
                    f'$d.FileName = "{md.stem}.pdf";'
                    '$d.Filter = "PDF|*.pdf";'
                    '$d.Title = "Guardar PDF";'
                    '$d.InitialDirectory = [Environment]::GetFolderPath("Desktop");'
                    '$f = New-Object System.Windows.Forms.Form;'
                    '$f.TopMost = $true; $f.ShowInTaskbar = $false;'
                    '$f.WindowState = [System.Windows.Forms.FormWindowState]::Minimized;'
                    '$f.Show();'
                    'if ($d.ShowDialog($f) -eq "OK") { $d.FileName };'
                    '$f.Dispose();'
                )
                r = subprocess.run(
                    ['powershell', '-NoProfile', '-Command', ps],
                    capture_output=True, text=True, timeout=120,
                )
                dest = r.stdout.strip()
                if not dest:
                    return {'ok': False, 'error': 'cancelado'}

                shutil.copy2(tmp_pdf, dest)

        except Exception as e:
            return {'ok': False, 'error': str(e)}

        # Abrir la carpeta con el archivo seleccionado en el Explorador
        subprocess.Popen(['explorer', '/select,', dest])
        return {'ok': True, 'path': dest}

    def get_diagnostics(self) -> dict:
        """Estado del diagnostico para el panel de Settings.

        Devuelve claves i18n, no textos: los traduce la UI con su propio
        diccionario. Es barato, asi que la UI puede refrescarlo en bucle.
        """
        try:
            import diagnostics
            return {'ok': True, **diagnostics.collect()}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def restart_app(self) -> dict:
        """Reinicia Noted. Se asegura antes de que haya vigilante que lo
        vuelva a levantar: si no, la app no volveria."""
        try:
            import diagnostics
            return diagnostics.restart_app()
        except Exception as e:
            return {'ok': False, 'reason': 'error', 'error': str(e)}

    def apply_diagnostic_fix(self, action: str) -> dict:
        """Ejecuta el arreglo que ofrece una fila del diagnostico."""
        try:
            import diagnostics
            return diagnostics.apply_fix(action)
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def run_audio_test(self) -> dict:
        """Suena un tono y graba por microfono y por loopback a la vez.

        Bloquea un par de segundos: es una grabacion real, que es lo unico
        que demuestra que una reunion se grabaria.
        """
        try:
            import diagnostics
            return diagnostics.run_audio_test()
        except Exception as e:
            return {'ok': False, 'reason': 'error', 'error': str(e)}

    def get_meetings(self) -> list:

        """Lista todas las minutas agrupadas por fecha, con conteo de pendientes."""

        pins = self._load_pins()

        meetings = []

        if not hasattr(self, '_meetings_cache'):
            self._meetings_cache = {}

        seen_keys: set[tuple] = set()

        for md in sorted(MINUTES_DIR.glob('*.md'), key=lambda p: p.stat().st_mtime, reverse=True):

            cache_key = (str(md), md.stat().st_mtime)

            seen_keys.add(cache_key)

            if cache_key in self._meetings_cache:

                entry = dict(self._meetings_cache[cache_key])

                entry['pinned'] = _pin_key(md.stem) in pins

                meetings.append(entry)

                continue

            meta = _parse_stem(md.stem)

            title = self._title_from_content(md) or meta['title']

            actions_path = md.parent / f"{md.stem}_actions.json"

            pending = 0

            has_actions = False

            project_id = ''

            if actions_path.exists():

                try:

                    data = json.loads(actions_path.read_text(encoding='utf-8'))

                    acts = data.get('actions', [])

                    has_actions = bool(acts)

                    pending = sum(1 for a in acts if not a.get('executed', False))

                    project_id = data.get('project_id', '')

                except Exception:

                    pass

            entry = {

                'path':          str(md),

                'title':         title,

                'date':          meta['date'],

                'time':          meta['time'],

                'has_actions':   has_actions,

                'pending_count': pending,

                'project_id':    project_id,

                'pinned':        _pin_key(md.stem) in pins,

                # 'summary' / 'off' si son minutas breves (nivel de Ajustes): el
                # panel ofrece entonces "Generar minutas completas".
                'brief':         _brief_mode(md),

            }

            self._meetings_cache[cache_key] = entry

            meetings.append(entry)

        # Evict stale entries (deleted files)
        for key in list(self._meetings_cache):
            if key not in seen_keys:
                del self._meetings_cache[key]

        return meetings



    def get_minutes_html(self, path: str) -> str:

        """Devuelve el cuerpo HTML de las minutas sin la sección de acciones pendientes."""

        md_path = Path(path)

        if not md_path.exists():

            return '<em>Archivo no encontrado</em>'

        try:

            md_text = md_path.read_text(encoding='utf-8')

            # Strip the pending-actions table section (it lives in Gestionar acciones)

            md_text = re.sub(

                r'\n##\s+(Acciones\s+Pendientes|Pending\s+Actions)\b.*?(?=\n##\s|\Z)',

                '',

                md_text,

                flags=re.IGNORECASE | re.DOTALL,

            )

            try:

                import markdown

                return markdown.markdown(

                    md_text,

                    extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists'],

                )

            except ImportError:

                return f'<pre style="white-space:pre-wrap">{md_text}</pre>'

        except Exception as e:

            return f'<em>Error: {e}</em>'



    def get_actions(self, path: str) -> list:

        """Devuelve las acciones de una minuta (del JSON enriquecido)."""

        from actions_enricher import load_enriched

        acts = load_enriched(Path(path))

        return acts or []



    def rename_meeting(self, path: str, new_title: str) -> bool:

        """Actualiza el título de una reunión en su archivo .md."""

        md_path = Path(path)

        if not md_path.exists():

            return False

        try:

            text = md_path.read_text(encoding='utf-8')

            import re as _re

            # Sin saltos de linea (romperian la cabecera) y con funcion de
            # reemplazo: como cadena, `\1` o `\g<` del titulo se interpretan.
            title = _re.sub(r'\s*[\r\n]+\s*', ' ', new_title).strip()

            if _re.match(r'TITULO:', text):

                text = _re.sub(r'^TITULO:.*', lambda _m: f'TITULO: {title}', text, count=1)

            else:

                text = f'TITULO: {title}\n\n' + text

            atomic_io.write_text_atomic(md_path, text)

            return True

        except Exception:

            return False



    def pick_folder(self) -> str:

        """Abre el explorador de carpetas nativo de Windows. Devuelve la ruta o ''."""

        try:

            import webview

            wins = webview.windows

            if not wins:

                return ''

            result = wins[0].create_file_dialog(webview.FOLDER_DIALOG)

            if result and len(result) > 0:

                return result[0]

        except Exception as e:

            log.warning(f"pick_folder: {e}")

        return ''



    def pick_file(self, file_types: list = None) -> str:

        """Abre el selector de archivos nativo de Windows. Devuelve la ruta o ''."""

        try:

            import webview

            wins = webview.windows

            if not wins:

                return ''

            kwargs = {}

            if file_types:

                kwargs['file_types'] = file_types

            result = wins[0].create_file_dialog(webview.OPEN_DIALOG, **kwargs)

            if result and len(result) > 0:

                return result[0]

        except Exception as e:

            log.warning(f"pick_file: {e}")

        return ''

    def pick_files(self, file_types: list = None) -> list:
        """Abre el selector de archivos nativo con multi-selección.
        Devuelve una lista de rutas (o [] si se cancela)."""
        try:
            import webview
            wins = webview.windows
            if not wins:
                return []
            kwargs = {'allow_multiple': True}
            if file_types:
                kwargs['file_types'] = file_types
            result = wins[0].create_file_dialog(webview.OPEN_DIALOG, **kwargs)
            if result:
                return list(result)
        except Exception as e:
            log.warning(f"pick_files: {e}")
        return []

    def get_action_working_dir(self, path: str, index: int) -> str:

        """Devuelve el directorio de trabajo sugerido para una acción."""

        from actions_enricher import load_enriched

        acts = load_enriched(Path(path))

        action = next((a for a in acts if a['index'] == index), None) if acts else None

        proj = action.get('project', '') if action else ''

        if proj:

            try:

                projects_file = PROJECT_DIR / 'projects.json'

                if projects_file.exists():

                    pdata = json.loads(projects_file.read_text(encoding='utf-8'))

                    found = next((p for p in pdata.get('projects', [])

                                  if p['id'] == proj or p['name'] == proj), None)

                    if found:

                        d = found.get('output_dir') or found.get('directory', '')

                        if d and Path(d).exists():

                            return d

            except Exception:

                pass

            candidate = PROJECT_DIR.parent / proj

            if candidate.exists():

                return str(candidate)

        return str(PROJECT_DIR)



    def get_action(self, path: str, index: int) -> dict:

        """Returns a single action dict from the meeting actions JSON."""

        from actions_enricher import get_actions_path

        try:

            ap = get_actions_path(Path(path))

            if ap.exists():

                data = json.loads(ap.read_text(encoding='utf-8'))

                for a in data.get('actions', []):

                    if a.get('index') == index:

                        return a

        except Exception:

            pass

        return {}



    def execute_action(self, path: str, index: int) -> bool:

        """Abre Windows Terminal con claude -p para ejecutar la acción."""

        from actions_enricher import load_enriched, update_action_executed

        acts = load_enriched(Path(path))

        if not acts:

            return False

        action = next((a for a in acts if a['index'] == index), None)

        if not action:

            return False



        prompt = action.get('prompt_enriched') or action.get('prompt_original', '')

        proj = action.get('project', '')

        proj_path = PROJECT_DIR

        try:

            if proj:

                projects_file = PROJECT_DIR / 'projects.json'

                if projects_file.exists():

                    pdata = json.loads(projects_file.read_text(encoding='utf-8'))

                    found = next((p for p in pdata.get('projects', []) if p['id'] == proj or p['name'] == proj), None)

                    if found and found.get('output_dir'):

                        proj_path = Path(found['output_dir'])

                    elif proj:

                        proj_path = PROJECT_DIR.parent / proj

        except Exception:

            proj_path = (PROJECT_DIR.parent / proj) if proj else PROJECT_DIR



        tmp_dir    = Path(tempfile.mkdtemp())

        tmp_prompt = tmp_dir / 'prompt.txt'

        tmp_prompt.write_text(prompt, encoding='utf-8')



        ps1 = tmp_dir / 'run.ps1'

        ps1.write_text('\n'.join([

            '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8',

            f'Set-Location "{proj_path}"',

            f'Get-Content -Raw -Encoding UTF8 "{tmp_prompt}" | claude --verbose -p',

            "Read-Host 'Presiona Enter para cerrar'",

        ]), encoding='utf-8')



        try:

            subprocess.Popen([

                'wt', '--window', '0', 'new-tab',

                _PWSH, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ps1)

            ])

        except FileNotFoundError:

            subprocess.Popen([_PWSH, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ps1)])



        update_action_executed(Path(path), index, prompt)

        return True



    def update_prompt(self, path: str, index: int, prompt: str) -> bool:

        from actions_enricher import update_action_prompt

        try:

            update_action_prompt(Path(path), index, prompt)

            return True

        except Exception:

            return False



    def mark_done(self, path: str, index: int) -> bool:

        from actions_enricher import update_action_executed, load_enriched

        acts = load_enriched(Path(path))

        if not acts:

            return False

        action = next((a for a in acts if a['index'] == index), None)

        if not action:

            return False

        prompt = action.get('prompt_enriched') or action.get('prompt_original', '')

        try:

            update_action_executed(Path(path), index, prompt)

            return True

        except Exception:

            return False



    def create_action(self, path: str, title: str, assignee: str = '',

                      deadline: str = '') -> dict:

        """Crea una acción manual en el JSON de la reunión. Devuelve la acción creada o {}."""

        from actions_enricher import get_actions_path

        md_path = Path(path)

        ap = get_actions_path(md_path)

        try:

            if ap.exists():

                data = json.loads(ap.read_text(encoding='utf-8'))

            else:

                data = {'minutes': path, 'project_id': '', 'actions': []}

            actions = data.get('actions', [])

            next_index = max((a.get('index', -1) for a in actions), default=-1) + 1

            action = {

                'index': next_index,

                'title': title.strip(),

                'type': 'human',

                'prompt_original': '',

                'prompt_enriched': '',

                'assignee': assignee.strip(),

                'deadline': deadline.strip(),

                'executed': False,

                'claude_executable': False,

                'source': 'manual',

                'created_at': datetime.now().strftime('%Y-%m-%d'),

            }

            actions.append(action)

            data['actions'] = actions

            ap.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

            return action

        except Exception as e:

            log.error(f"create_action: {e}")

            return {}



    def update_action(self, path: str, index: int, title: str,

                      assignee: str = '', deadline: str = '') -> bool:

        """Actualiza título, responsable y deadline de una acción existente."""

        from actions_enricher import get_actions_path

        ap = get_actions_path(Path(path))

        if not ap.exists():

            return False

        try:

            data = json.loads(ap.read_text(encoding='utf-8'))

            for a in data.get('actions', []):

                if a['index'] == index:

                    a['title']    = title.strip()

                    a['assignee'] = assignee.strip()

                    a['deadline'] = deadline.strip()

                    break

            ap.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

            return True

        except Exception as e:

            log.error(f"update_action: {e}")

            return False



    def delete_action(self, path: str, index: int) -> bool:

        """Elimina una acción del JSON de la reunión."""

        from actions_enricher import get_actions_path

        md_path = Path(path)

        ap = get_actions_path(md_path)

        if not ap.exists():

            return False

        try:

            data = json.loads(ap.read_text(encoding='utf-8'))

            data['actions'] = [a for a in data.get('actions', []) if a['index'] != index]

            ap.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

            return True

        except Exception as e:

            log.error(f"delete_action: {e}")

            return False




    # ── Task board ────────────────────────────────────────────────────────────



    def get_tasks(self) -> dict:

        """Returns all tasks + projects + buckets + custom field values for the task board."""

        from tasks_store import get_tasks as _get, migrate_panel_actions, get_custom_values

        from buckets_store import get_buckets as _get_buckets

        migrate_panel_actions()

        tasks = _get()

        projects_file = PROJECT_DIR / 'projects.json'

        projects = []

        try:

            if projects_file.exists():

                pdata = json.loads(projects_file.read_text(encoding='utf-8'))

                projects = pdata.get('projects', [])

        except Exception:

            pass

        return {

            'projects': projects,

            'tasks': tasks,

            'buckets': _get_buckets(),

            'custom_statuses': get_custom_values('custom_statuses'),

            'custom_priorities': get_custom_values('custom_priorities'),

            'status_column_order': get_custom_values('status_column_order'),

        }




    def save_buckets(self, buckets: list) -> bool:

        from buckets_store import save_buckets as _save

        return _save(buckets)



    def create_task(self, project_id: str, title: str, parent_id: str = '',

                    assignee: str = '', deadline: str = '', priority: str = '',

                    bucket_id: str = '', start_date: str = '', end_date: str = '',

                    tags: list = None, view: str = '', status: str = '') -> dict:

        from tasks_store import create_task as _create

        from buckets_store import get_first_bucket_id

        return _create(

            project_id=project_id,

            title=title,

            parent_id=parent_id or None,

            assignee=assignee or None,

            end_date=end_date or deadline or None,

            start_date=start_date or None,

            tags=tags or [],

            priority=priority or None,

            bucket_id=bucket_id or get_first_bucket_id(),

            source='manual',

            view=view or None,

            status=status or 'not_started',

        )



    def save_custom_statuses(self, statuses: list) -> bool:

        from tasks_store import save_custom_values

        return save_custom_values('custom_statuses', statuses)



    def save_custom_priorities(self, priorities: list) -> bool:

        from tasks_store import save_custom_values

        return save_custom_values('custom_priorities', priorities)



    def save_status_column_order(self, order: list) -> bool:

        from tasks_store import save_custom_values

        return save_custom_values('status_column_order', order)



    def update_task(self, task_id: str, fields: dict) -> bool:

        from tasks_store import update_task as _update

        return _update(task_id, fields)



    def delete_task(self, task_id: str) -> bool:

        from tasks_store import delete_task as _delete, get_tasks as _get

        # Antes de borrar, limpiar el flag in_panel de la acción de reunión origen

        # (si la hay) para mantener la coherencia y permitir volver a añadirla.

        try:

            for tk in _get():

                if tk.get('id') == task_id and tk.get('meeting_path') and tk.get('meeting_action_index') is not None:

                    self._clear_action_in_panel(tk['meeting_path'], tk['meeting_action_index'])

                    break

        except Exception as e:

            log.warning(f"delete_task clear in_panel: {e}")

        return _delete(task_id)



    def _clear_action_in_panel(self, path: str, index: int) -> None:

        from actions_enricher import get_actions_path

        md_path = Path(path)

        ap = get_actions_path(md_path)

        if not ap.exists():

            # Meeting may have been renamed — search by YYYYMMDD_HHMM_ prefix

            m = re.match(r'(\d{8}_\d{4})_', md_path.stem)

            if m:

                candidates = list(md_path.parent.glob(f"{m.group(1)}_*_actions.json"))

                if candidates:

                    ap = candidates[0]

        if not ap.exists():

            return

        try:

            data = json.loads(ap.read_text(encoding='utf-8'))

            changed = False

            for a in data.get('actions', []):

                if a.get('index') == index and a.get('in_panel'):

                    a['in_panel'] = False

                    changed = True

                    break

            if changed:

                ap.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

        except Exception as e:

            log.warning(f"_clear_action_in_panel: {e}")



    def move_action_to_panel(self, path: str, index: int,

                             project_id: str, parent_id: str = '',

                             bucket_id: str = '') -> str:

        """Creates a task linked to a meeting action. Returns new task id."""

        from actions_enricher import get_actions_path

        from tasks_store import create_task as _create

        action = None

        try:

            ap = get_actions_path(Path(path))

            if ap.exists():

                adata = json.loads(ap.read_text(encoding='utf-8'))

                for a in adata.get('actions', []):

                    if a['index'] == index:

                        action = a

                        break

        except Exception:

            pass

        if not action:

            return ''

        task = _create(

            project_id=project_id or action.get('project') or 'none',

            title=action.get('title', ''),

            parent_id=parent_id or None,

            status='done' if action.get('executed') else 'not_started',

            assignee=action.get('assignee'),

            deadline=action.get('deadline'),

            source='meeting',

            meeting_path=path,

            meeting_action_index=index,

            claude_executable=bool(action.get('claude_executable')),

            bucket_id=bucket_id or None,

        )

        # Mark in_panel on the source action

        try:

            ap = get_actions_path(Path(path))

            adata = json.loads(ap.read_text(encoding='utf-8'))

            for a in adata.get('actions', []):

                if a['index'] == index:

                    a['in_panel'] = True

                    break

            ap.write_text(json.dumps(adata, ensure_ascii=False, indent=2), encoding='utf-8')

        except Exception as e:

            log.warning(f"move_action_to_panel mark in_panel: {e}")

        return task['id']



    def enrich_actions(self, path: str) -> bool:

        """Elimina el JSON de acciones de una minuta y relanza el enriquecimiento."""

        from actions_enricher import enrich_and_save, get_actions_path

        md_path = Path(path)

        if not md_path.exists():

            return False

        ap = get_actions_path(md_path)

        bak = _backup_actions(ap)

        if ap.exists():

            ap.unlink()

        try:

            enrich_and_save(md_path, PROJECT_DIR.parent,
                            on_done=lambda: _finish_actions_regen(ap, bak))

        except Exception:

            _finish_actions_regen(ap, bak)

            raise

        return True



    def add_meeting_action(self, path: str, title: str,

                           deadline: str = '', assignee: str = '') -> bool:

        """Añade una acción manual a una reunión (para acciones que la IA no detectó)."""

        from actions_enricher import add_manual_action

        if not title or not title.strip():

            return False

        try:

            result = add_manual_action(Path(path), title.strip(),

                                       (deadline or '').strip(), (assignee or '').strip())

            return result is not None

        except Exception as e:

            log.error(f"add_meeting_action: {e}")

            return False




    def export_to_project(self, path: str) -> str:

        """Exporta transcript+HTML+email a la carpeta del proyecto. Devuelve '' si ok, mensaje de error si falla."""

        try:

            from project_exporter import export_to_project_folder, get_meeting_project

            md_path = Path(path)

            project = get_meeting_project(md_path)

            if not project:

                return 'no_project'

            proj_dir = (project.get('directory') or '').strip()

            if not proj_dir:

                return 'no_directory'

            ok = export_to_project_folder(md_path)

            return '' if ok else 'export_failed'

        except Exception as e:

            log.error(f"export_to_project: {e}")

            return str(e)



    def open_project_dir(self, path: str) -> bool:

        try:

            p = Path(path)

            p.mkdir(parents=True, exist_ok=True)

            subprocess.Popen(['explorer', str(p)])

            return True

        except Exception as e:

            log.error(f"open_project_dir: {e}")

            return False



    def browse_project_folder(self) -> str:

        """Opens native OS folder picker and returns selected path."""

        import webview

        if _window is None:

            return ''

        try:

            result = _window.create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=False)

            if result:

                return result[0] if isinstance(result, (list, tuple)) else str(result)

        except Exception as e:

            log.warning(f"browse_project_folder: {e}")

        return ''



    def get_projects(self) -> list:

        """Returns list of projects from projects.json."""

        p = PROJECT_DIR / 'projects.json'

        if not p.exists():

            return []

        try:

            data = json.loads(p.read_text(encoding='utf-8'))

            return data.get('projects', [])

        except Exception:

            return []



    def save_project(self, project: dict) -> bool:

        """Create or update a project. project must have: name, description, stakeholders (list of emails). id is auto-generated from name if absent."""

        import re as _re

        p = PROJECT_DIR / 'projects.json'

        try:

            data = {'projects': []}

            if p.exists():

                # Corrupto: la excepcion corta aqui, no se re-guarda encima.
                data = json.loads(p.read_text(encoding='utf-8'))

                if not isinstance(data, dict):

                    raise ValueError('projects.json no es un objeto')

            projects = data.get('projects', [])

            if not project.get('id'):

                project['id'] = _re.sub(r'[^a-z0-9]+', '-', project.get('name', 'project').lower()).strip('-')

                # ensure uniqueness

                existing_ids = {pr['id'] for pr in projects}

                base = project['id']

                i = 2

                while project['id'] in existing_ids:

                    project['id'] = f"{base}-{i}"

                    i += 1

            # update if exists, else append

            idx = next((i for i, pr in enumerate(projects) if pr['id'] == project['id']), None)

            is_new = idx is None

            if idx is not None:

                projects[idx] = project

            else:

                projects.append(project)

            data['projects'] = projects

            atomic_io.write_text_atomic(p, json.dumps(data, ensure_ascii=False, indent=2))

            if is_new:

                self.detect_projects_for_all()

            return True

        except Exception as e:

            log.error(f"save_project: {e}")

            return False



    def delete_meeting(self, path: str) -> bool:

        """Mueve una reunión y sus ficheros (incluido el WAV) a la papelera.

        Borrado suave: recuperable desde la vista Papelera."""

        md_path = Path(path)

        stem = md_path.stem

        trash_root = MINUTES_DIR.parent / 'trash'

        trash_dir = trash_root / stem

        n = 2

        while trash_dir.exists():

            trash_dir = trash_root / f"{stem}__{n}"

            n += 1

        try:

            trash_dir.mkdir(parents=True, exist_ok=True)

            candidates = [

                md_path,

                md_path.with_suffix('.html'),

                md_path.parent / f"{stem}_actions.json",

                md_path.parent / f"{stem}_transcript.txt",

            ]

            m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', stem)

            if m:

                y, mo, d, hh, mm = m.groups()

                wav_stem = f"{y}-{mo}-{d}_{hh}-{mm}"

                # Solo los WAV de ESTA reunion: el minuto solo no basta, otra
                # reunion iniciada en el mismo minuto tendria el mismo prefijo.
                # El slug de las minutas sale del titulo generado y puede no
                # coincidir con el nombre del WAV: se acepta el nombre exacto y,
                # si no hay, un unico WAV de ese minuto. Con varios y ninguno
                # exacto se conserva todo (ante la duda, no borrar audio).
                same_minute = []

                for folder in [RECORDINGS_DIR / 'processed', RECORDINGS_DIR]:

                    if folder.exists():

                        same_minute.extend(
                            w for w in folder.glob(f"{glob.escape(wav_stem)}*.wav")
                            if w.stem == wav_stem or w.stem.startswith(wav_stem + '_'))

                exact_stem = wav_stem + stem[13:]

                exact = [w for w in same_minute
                         if w.stem == exact_stem or w.stem.startswith(exact_stem + '__')]

                candidates.extend(exact or (same_minute if len(same_minute) == 1 else []))

            files_meta = []

            intentos = 0

            for f in candidates:

                if f.exists():

                    intentos += 1

                    try:

                        shutil.move(str(f), str(trash_dir / f.name))

                        files_meta.append({'name': f.name, 'orig_dir': str(f.parent)})

                    except Exception as e:

                        log.warning(f"delete_meeting move {f.name}: {e}")

            if intentos and not files_meta:

                # Todo fallo (ficheros en uso): no hay nada en la papelera.
                shutil.rmtree(trash_dir, ignore_errors=True)

                log.warning(f"delete_meeting: no se pudo mover ningun fichero de {stem}")

                return False

            meta = _parse_stem(stem)

            (trash_dir / '_trash_meta.json').write_text(json.dumps({

                'stem':       stem,

                'title':      meta['title'],

                'date':       meta['date'],

                'time':       meta['time'],

                'deleted_at': datetime.now().isoformat(),

                'files':      files_meta,

            }, ensure_ascii=False, indent=2), encoding='utf-8')

            log.info(f"delete_meeting (papelera): {stem} → {trash_dir.name} ({len(files_meta)} ficheros)")

            return True

        except Exception as e:

            log.warning(f"delete_meeting: {e}")

            return False



    def list_trash(self) -> list:

        """Devuelve las reuniones en la papelera, más recientes primero."""

        trash_root = MINUTES_DIR.parent / 'trash'

        items = []

        if not trash_root.exists():

            return items

        for d in trash_root.iterdir():

            meta_f = d / '_trash_meta.json'

            if not d.is_dir() or not meta_f.exists():

                continue

            try:

                meta = json.loads(meta_f.read_text(encoding='utf-8'))

                meta['id'] = d.name

                meta['file_count'] = len(meta.get('files', []))

                items.append(meta)

            except Exception:

                pass

        items.sort(key=lambda x: x.get('deleted_at', ''), reverse=True)

        return items



    @staticmethod
    def _trash_dir_for(trash_id):
        """Carpeta de papelera de un id, o None si el id no es un nombre simple
        o el directorio resuelto cae fuera de la papelera (../, rutas absolutas)."""
        if not isinstance(trash_id, str) or not trash_id.strip():
            return None
        if '/' in trash_id or '\\' in trash_id or '..' in trash_id or ':' in trash_id:
            return None
        root = (MINUTES_DIR.parent / 'trash').resolve()
        d = (root / trash_id).resolve()
        if d == root or root not in d.parents:
            return None
        return d



    def recover_meeting(self, trash_id: str) -> bool:

        """Restaura una reunión desde la papelera a sus ubicaciones originales."""

        trash_dir = self._trash_dir_for(trash_id)

        if trash_dir is None:

            return False

        meta_f = trash_dir / '_trash_meta.json'

        if not meta_f.exists():

            return False

        try:

            meta = json.loads(meta_f.read_text(encoding='utf-8'))

            todo_ok = True

            for fm in meta.get('files', []):

                # orig_dir vacio o manipulado: no restaurar en el cwd.
                if not fm.get('orig_dir'):

                    continue

                name = Path(str(fm.get('name', ''))).name

                if not name or name != fm.get('name'):

                    todo_ok = False

                    continue

                src = trash_dir / name

                dst_dir = Path(fm['orig_dir'])

                if src.exists():

                    try:

                        dst_dir.mkdir(parents=True, exist_ok=True)

                        dst = dst_dir / name

                        if dst.exists():

                            # shutil.move pisaria el fichero que hay ahora.
                            dst = dst_dir / f"{Path(name).stem}_recuperada{Path(name).suffix}"

                            n = 2

                            while dst.exists():

                                dst = dst_dir / f"{Path(name).stem}_recuperada{n}{Path(name).suffix}"

                                n += 1

                        shutil.move(str(src), str(dst))

                    except Exception as e:

                        todo_ok = False

                        log.warning(f"recover_meeting move {name}: {e}")

            if not todo_ok:

                log.warning(f"recover_meeting: {trash_id} incompleto, se conserva la papelera")

                return False

            shutil.rmtree(trash_dir, ignore_errors=True)

            log.info(f"recover_meeting: {trash_id}")

            return True

        except Exception as e:

            log.warning(f"recover_meeting: {e}")

            return False



    def purge_trash_meeting(self, trash_id: str) -> bool:

        """Elimina permanentemente una entrada de la papelera."""

        trash_dir = self._trash_dir_for(trash_id)

        if trash_dir is None or not trash_dir.exists():

            return False

        try:

            shutil.rmtree(trash_dir, ignore_errors=True)

            log.info(f"purge_trash_meeting: {trash_id}")

            return True

        except Exception as e:

            log.warning(f"purge_trash_meeting: {e}")

            return False



    def cancel_job(self, stem: str) -> bool:

        """Descarta un trabajo del pipeline: en cola o ya transcribiendo.



        El audio y lo que se haya generado van a la papelera, no se borran.

        Se comunica por fichero porque el daemon corre en otro proceso (mismo

        mecanismo que .cli_command).

        """

        if not stem:

            return False

        try:

            from tray_app import _append_cancel_signal

            _append_cancel_signal(stem)

            log.info(f"cancel_job solicitado: {stem}")

            return True

        except Exception as e:

            log.error(f"cancel_job: {e}")

            return False



    def set_meeting_project(self, path: str, project_id: str) -> bool:

        """Manually assign (or override) the project for a meeting."""

        try:

            from actions_enricher import set_meeting_project_id

            ok = set_meeting_project_id(Path(path), project_id)

            if ok:

                # Reubicar también el resumen en project_docs/. Antes esto solo

                # cambiaba el project_id del _actions.json y la carpeta se

                # quedaba con la asignación anterior, o sin crear si la reunión

                # es previa a la existencia del proyecto.

                try:

                    from project_context import sync_meeting_summary

                    sync_meeting_summary(Path(path))

                except Exception as e:

                    log.warning(f"set_meeting_project sync: {e}")

            return ok

        except Exception as e:

            log.error(f"set_meeting_project: {e}")

            return False



    def detect_projects_for_all(self) -> None:

        """Background: run Claude project detection for all unassigned meetings."""

        import threading



        def _run():

            try:

                from actions_enricher import detect_projects_for_all_meetings

                results = detect_projects_for_all_meetings()

                log.info(f"detect_projects_for_all done: {results}")

            except Exception as e:

                log.error(f"detect_projects_for_all: {e}", exc_info=True)



        threading.Thread(target=_run, daemon=True, name='ProjectDetect').start()



    def delete_project(self, project_id: str) -> bool:

        """Delete a project by id."""

        p = PROJECT_DIR / 'projects.json'

        if not p.exists():

            return False

        try:

            data = json.loads(p.read_text(encoding='utf-8'))

            data['projects'] = [pr for pr in data.get('projects', []) if pr['id'] != project_id]

            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

            return True

        except Exception as e:

            log.error(f"delete_project: {e}")

            return False



    def get_pipeline_status(self) -> dict:

        """Lee el estado del pipeline (daemon + importaciones en curso)."""

        jobs = []

        try:

            p = PROJECT_DIR / '.pipeline_status.json'

            if p.exists():

                jobs = json.loads(p.read_text(encoding='utf-8')).get('jobs', [])

        except Exception:

            pass



        _stage_labels = {

            'reading':    'Importando transcript...',

            'generating': 'Generando minutas...',

            'saving':     'Guardando minutas...',

            'html':       'Exportando HTML...',

            'actions':    'Generando acciones...',

        }

        for state in _import_runs.values():

            if state.get('done'):

                continue

            stage = state.get('stage', '')

            pct   = state.get('pct', 0)

            label = _stage_labels.get(stage, 'Importando transcript...')

            jobs.append({'stage': 'processing', 'label': label, 'pct': pct})

        jobs.extend(semantic.pipeline_jobs())

        # Regen jobs activos (regeneración de minutas en curso)
        for regen_path, state in _regen_runs.items():
            if state.get('done'):
                continue
            stem = Path(regen_path).stem
            m = re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(.+)', stem)
            title = m.group(1).replace('_', ' ') if m else stem
            jobs.append({
                'stage':     'processing',
                'kind':      'regen',
                'label':     'Regenerar notas',
                'stage_key': state.get('stage', ''),
                'subtitle':  title,
                'pct':       state.get('pct', 0),
                'path':      regen_path,
            })

        return {'jobs': jobs}



    def get_navigate_request(self) -> str:

        """Lee y elimina .app_navigate.txt; devuelve la ruta o '' si no hay nada."""

        nav_file = PROJECT_DIR / '.app_navigate.txt'

        if not nav_file.exists():

            return ''

        try:

            path = nav_file.read_text(encoding='utf-8').strip()

            nav_file.unlink(missing_ok=True)

            return path

        except Exception:

            return ''





    def save_minutes_notes(self, path: str, notes_md: str) -> bool:

        """Guarda notas editadas desde el editor visual (preserva la sección de Acciones)."""

        p = Path(path)

        if not isinstance(notes_md, str) or not notes_md.strip():

            # Un editor vacio por error dejaria la minuta en blanco.
            return False

        try:

            current = ''

            try:

                current = p.read_text(encoding='utf-8')

            except FileNotFoundError:

                pass

            except Exception as e:

                # Sin leer lo que hay no se puede preservar la seccion de Acciones.
                log.error(f"save_minutes_notes: no se pudo leer {p.name}: {e}")

                return False

            m = re.search(

                r'\n(##\s+(?:Acciones\s+Pendientes|Pending\s+Actions)\b.*?)(?=\n##\s|\Z)',

                current, flags=re.IGNORECASE | re.DOTALL,

            )

            actions_section = m.group(1).rstrip() if m else ''

            new_md = notes_md.rstrip() + (('\n\n' + actions_section + '\n') if actions_section else '\n')

            atomic_io.write_text_atomic(p, new_md)

            try:

                from html_exporter import export_to_html

                meta = _parse_stem(p.stem)

                export_to_html(p, meta['title'], open_browser=False)

            except Exception as e:

                log.warning(f"save_minutes_notes html: {e}")

            log.info(f"save_minutes_notes: {p.stem}")

            return True

        except Exception as e:

            log.error(f"save_minutes_notes: {e}")

            return False



    # ── Glosario (Settings) ────────────────────────────────────────────────
    # Todos los métodos devuelven el estado completo, para que el front se
    # repinte desde ahí sin tener que mantener su propia copia.

    def _glossary_payload(self) -> dict:
        import glossary
        data = glossary.load()
        return {
            'entries': data['entries'],
            'suggestions': data['suggestions'],
            'pending': len(data['suggestions']),
            'projects': [{'id': p.get('id'), 'name': p.get('name') or p.get('id')}
                         for p in self.get_projects() if p.get('id')],
        }

    def get_glossary(self) -> dict:
        return self._glossary_payload()

    def save_glossary_entry(self, entry: dict, original: dict | None = None) -> dict:
        """Crea o edita una entrada. `original` ({term, project}) es la que se
        está editando, por si cambia el término o el ámbito."""
        import glossary
        try:
            glossary.upsert_entry(entry, original)
        except ValueError:
            return {'error': 'term'}
        return self._glossary_payload()

    def delete_glossary_entry(self, term: str, project: str | None = None) -> dict:
        import glossary
        glossary.delete_entry(term, project)
        return self._glossary_payload()

    def resolve_glossary_suggestion(self, term: str, action: str, edits: dict | None = None) -> dict:
        """action: 'approve' (entra en el glosario, con `edits` opcionales) o
        'dismiss' (se descarta y no vuelve a proponerse)."""
        import glossary
        if action == 'approve':
            glossary.approve_suggestion(term, edits)
        elif action == 'dismiss':
            glossary.dismiss_suggestion(term)
        else:
            return {'error': 'action'}
        return self._glossary_payload()

    def get_settings(self) -> dict:

        try:

            p = PROJECT_DIR / 'settings.json'

            if p.exists():

                return json.loads(p.read_text(encoding='utf-8'))

        except Exception:

            pass

        return {'language': 'es'}



    def save_settings(self, settings: dict) -> bool:

        try:

            p = PROJECT_DIR / 'settings.json'

            existing = {}

            if p.exists():

                # Corrupto: la excepcion corta aqui, no se re-guarda encima.
                existing = json.loads(p.read_text(encoding='utf-8'))

                if not isinstance(existing, dict):

                    raise ValueError('settings.json no es un objeto')

            existing.update(settings)

            atomic_io.write_text_atomic(p, json.dumps(existing, indent=2, ensure_ascii=False))

            return True

        except Exception as e:

            log.error(f"save_settings: {e}")

            return False



    def _screenshots_context(self, md_path, lang: str = 'es') -> list:
        """Lineas para el contexto de Claude (terminal): que capturas tiene la reunion y en
        que minuto, para que pueda abrirlas cuando la pregunta trate de lo que se veia en
        pantalla. Claude arranca en la carpeta de las minutas, donde estan los PNG. Vacio
        si no hay capturas: el contexto queda exactamente como antes."""
        shots = self.list_screenshots(str(md_path))
        if not shots:
            return []
        if lang == 'en':
            head = ("## SCREENSHOTS",
                    "Screenshots taken during the meeting are in the current directory. Open one with "
                    "your file-reading tool only when a question is about what was shown on screen; "
                    "the transcript marks each one with [Screenshot] at its minute.")
        else:
            head = ("## CAPTURAS",
                    "Las capturas de pantalla hechas durante la reunión están en el directorio actual. "
                    "Abre una con tu herramienta de lectura de ficheros solo cuando una pregunta trate de "
                    "lo que se veía en pantalla; la transcripción marca cada una con [Screenshot] en su minuto.")
        return ["", head[0], head[1], *[f"- [{s['time']}] {s['file']}" for s in shots]]

    def open_minutes_in_claude(self, path: str, lang: str = 'es') -> bool:

        """Abre Claude en modo interactivo con las minutas como contexto.

        Claude arranca la conversación solo con un mensaje de bienvenida."""

        md_path = Path(path)

        if not md_path.exists():

            return False



        meta  = _parse_stem(md_path.stem)

        title = meta['title']

        date  = meta.get('date', '')



        # Buscar transcript: primero junto a las notas (minutes/), luego en recordings/

        transcript_content = ''

        # 1. Mismo directorio que el .md: <stem>_transcript.txt

        sibling = md_path.with_name(md_path.stem + '_transcript.txt')

        if sibling.exists():

            try:

                transcript_content = sibling.read_text(encoding='utf-8')

            except Exception:

                pass



        # 2. Fallback: recordings/processed y recordings/ con fecha reformateada

        if not transcript_content:

            m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', md_path.stem)

            if m:

                y, mo, d, hh, mm = m.groups()

                stem = f"{y}-{mo}-{d}_{hh}-{mm}"

                for folder in ['recordings/processed', 'recordings']:

                    folder_path = PROJECT_DIR / folder

                    if not folder_path.exists():

                        continue

                    candidates = list(folder_path.glob(f"{stem}*_transcript.txt"))

                    if candidates:

                        try:

                            transcript_content = candidates[0].read_text(encoding='utf-8')

                        except Exception:

                            pass

                        break



        # Fall back to meeting minutes if no transcript file exists

        using_transcript = bool(transcript_content)

        if not transcript_content:

            try:

                transcript_content = md_path.read_text(encoding='utf-8')

            except Exception:

                transcript_content = ''



        if not using_transcript:

            log.warning(f"open_minutes_in_claude: transcripción no encontrada para {md_path.stem}")

            return False



        if lang == 'en':

            greeting_text = f"I've read the transcript for \"{title}\". What would you like to explore?"

            lang_instruction = "Respond always in English throughout the conversation."

            context_label = "TRANSCRIPT"

        else:

            greeting_text = f"He leído la transcripción de \"{title}\". ¿Hay algún tema en el que quieras profundizar?"

            lang_instruction = "Responde siempre en español durante toda la conversación."

            context_label = "TRANSCRIPCIÓN"



        context_content = '\n'.join([

            lang_instruction,

            "The transcript below is your background context. Answer questions about this meeting based on it.",

            "Do NOT summarise or repeat the transcript unless explicitly asked.",

            "",

            f"## {context_label} — {title} ({date})",

            transcript_content,
            *self._screenshots_context(md_path, lang),
        ])

        tmp_dir     = Path(tempfile.mkdtemp())

        tmp_context = tmp_dir / 'context.txt'

        tmp_context.write_text(context_content, encoding='utf-8')



        # Print greeting via Write-Host so it appears without a "Human:" turn.

        # Claude starts in interactive mode with no initial user message.

        tmp_greeting = tmp_dir / 'greeting.txt'

        tmp_greeting.write_text(greeting_text, encoding='utf-8-sig')

        ps1 = tmp_dir / 'chat.ps1'

        ps1_lines = [

            '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8',

            f'Set-Location "{MINUTES_DIR}"',

            f'$gr = (Get-Content -Raw -Encoding UTF8 "{tmp_greeting}").Trim()',

            'Write-Host ""',

            'Write-Host "  $gr" -ForegroundColor Cyan',

            'Write-Host ""',

            f'claude --append-system-prompt-file "{tmp_context}"',

        ]

        ps1.write_bytes(('\n'.join(ps1_lines)).encode('utf-8-sig'))



        try:

            subprocess.Popen([

                'wt', '--window', '0', 'new-tab',

                _PWSH, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ps1)

            ])

        except FileNotFoundError:

            subprocess.Popen([_PWSH, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ps1)])



        return True



    def open_minutes_in_claude_app(self, path: str, lang: str = 'es') -> bool:

        """Copia el transcript al portapapeles listo para pegar en Claude y abre claude.ai."""

        import webbrowser

        md_path = Path(path)

        if not md_path.exists():

            return False



        # Buscar transcript (misma lógica que open_minutes_in_claude)

        transcript_content = ''

        sibling = md_path.with_name(md_path.stem + '_transcript.txt')

        if sibling.exists():

            try:

                transcript_content = sibling.read_text(encoding='utf-8')

            except Exception:

                pass

        if not transcript_content:

            m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', md_path.stem)

            if m:

                y, mo, d, hh, mm = m.groups()

                stem = f"{y}-{mo}-{d}_{hh}-{mm}"

                for folder in ['recordings/processed', 'recordings']:

                    folder_path = PROJECT_DIR / folder

                    if not folder_path.exists():

                        continue

                    candidates = list(folder_path.glob(f"{stem}*_transcript.txt"))

                    if candidates:

                        try:

                            transcript_content = candidates[0].read_text(encoding='utf-8')

                        except Exception:

                            pass

                        break

        if not transcript_content:

            return False



        meta  = _parse_stem(md_path.stem)

        title = meta['title']

        date  = meta.get('date', '')



        if lang == 'en':

            prompt = (

                f'I have the transcript of the meeting "{title}" from {date}. '

                f'Use it as context to answer my questions:\n\n{transcript_content}'

            )

        else:

            prompt = (

                f'Tengo el transcript de la reunión "{title}" del {date}. '

                f'Úsalo como contexto para responder mis preguntas:\n\n{transcript_content}'

            )



        try:

            # Copiar al portapapeles via PowerShell (evita dependencias extra)

            subprocess.run(

                [_PWSH, '-NoProfile', '-Command',

                 '$input | Set-Clipboard'],

                input=prompt, text=True, encoding='utf-8',

                capture_output=True,

            )

        except Exception as e:

            log.warning(f"open_minutes_in_claude_app clipboard error: {e}")



        try:

            if os.name == 'nt':

                os.startfile('claude:')

            else:

                webbrowser.open('https://claude.ai/new')

        except Exception:

            webbrowser.open('https://claude.ai/new')

        return True



    def open_html(self, path: str) -> bool:

        """Abre el HTML exportado de las minutas en el navegador predeterminado."""

        import webbrowser

        md_path = Path(path)

        html_path = md_path.with_suffix('.html')

        if not html_path.exists():

            from html_exporter import export_to_html

            meta = _parse_stem(md_path.stem)

            export_to_html(md_path, meta['title'], open_browser=False)

        if html_path.exists():

            webbrowser.open(html_path.as_uri())

            return True

        return False



    def send_email(self, path: str) -> bool:

        from outlook_sender import send_minutes_email

        from html_exporter import export_to_html

        md_path   = Path(path)

        html_path = md_path.with_suffix('.html')

        if not html_path.exists():

            export_to_html(md_path, md_path.stem, open_browser=False)

        meta = _parse_stem(md_path.stem)

        # Look up default_recipients from the meeting's project

        participants = []

        try:

            from actions_enricher import get_actions_path

            ap = get_actions_path(md_path)

            if ap.exists():

                adata = json.loads(ap.read_text(encoding='utf-8'))

                project_id = adata.get('project_id', '')

                if project_id and project_id != 'none':

                    projects_file = PROJECT_DIR / 'projects.json'

                    if projects_file.exists():

                        pdata = json.loads(projects_file.read_text(encoding='utf-8'))

                        proj = next((p for p in pdata.get('projects', []) if p['id'] == project_id), None)

                        if proj:

                            stakeholders = proj.get('stakeholders', [])

                            participants = [{'email': e, 'name': e} for e in stakeholders if e.strip()]

        except Exception:

            pass

        lang = _detect_notes_language(md_path)

        try:

            send_minutes_email(

                minutes_path=md_path,

                html_path=html_path,

                title=meta['title'],

                participants=participants,

                language=lang,

            )

            return True

        except Exception as e:

            log.error(f"send_email: {e}")

            return False



    def send_actions_email(self, path: str, title: str, date_str: str, actions: list) -> bool:

        try:

            from outlook_sender import send_actions_email as _send

            from config import get_ui_language

            return _send(title, date_str, actions, [], language=get_ui_language())

        except Exception as e:

            log.error(f"send_actions_email: {e}")

            return False



    def send_transcript_email(self, path: str, title: str, date_str: str) -> bool:

        try:

            from outlook_sender import send_transcript_email as _send

            from config import get_ui_language

            md_path = Path(path)

            transcript = md_path.parent / f"{md_path.stem}_transcript.txt"

            if not transcript.exists():

                m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', md_path.stem)

                if m:

                    y, mo, d, hh, mm = m.groups()

                    stem = f"{y}-{mo}-{d}_{hh}-{mm}"

                    for folder in (RECORDINGS_DIR / 'processed', RECORDINGS_DIR):

                        if not folder.exists():

                            continue

                        candidates = list(folder.glob(f"{stem}*_transcript.txt"))

                        if candidates:

                            transcript = candidates[0]

                            break

            if not transcript.exists():

                return False

            return _send(title, date_str, transcript, [], language=get_ui_language())

        except Exception as e:

            log.error(f"send_transcript_email: {e}")

            return False



    def regenerate_minutes(self, path: str, extra_context: str, lang: str = '') -> bool:

        """Regenera las minutas de una reunión usando el transcript original y contexto adicional."""

        md_path = Path(path)

        if not md_path.exists():

            return False



        m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', md_path.stem)

        if not m:

            return False



        y, mo, d, hh, mm = m.groups()

        stem = f"{y}-{mo}-{d}_{hh}-{mm}"



        transcript_content = ''



        # 1. Copia en minutes/ (guardada desde la v2 del pipeline)

        minutes_transcript = md_path.with_name(md_path.stem + '_transcript.txt')

        if minutes_transcript.exists():

            try:

                transcript_content = minutes_transcript.read_text(encoding='utf-8')

            except Exception as e:

                log.warning(f"regenerate_minutes: lectura minutes transcript fallida: {e}")



        # 2. Fallback: recordings/processed y recordings (formato YYYY-MM-DD_HH-MM)

        if not transcript_content:

            for folder_path in [RECORDINGS_DIR / 'processed', RECORDINGS_DIR]:

                if not folder_path.exists():

                    continue

                candidates = list(folder_path.glob(f"{stem}*_transcript.txt"))

                if candidates:

                    try:

                        transcript_content = candidates[0].read_text(encoding='utf-8')

                    except Exception as e:

                        log.warning(f"regenerate_minutes: read failed: {e}")

                    break



        if not transcript_content:

            log.warning(f"regenerate_minutes: no transcript found for {md_path.stem}")

            return False



        if not lang:

            lang = _detect_notes_language(md_path)



        # Synthetic path that carries the YYYY-MM-DD_HH-MM stem for date extraction

        synthetic_wav = RECORDINGS_DIR / f"{stem}_reunion.wav"



        _prune_runs(_regen_runs)

        _regen_runs[path] = {'pct': 5, 'stage': 'transcript_ok', 'done': False, 'error': ''}



        def _run():

            import time as _time

            from minutes_generator import generate_minutes, extract_title_from_minutes, save_minutes

            from html_exporter import export_to_html

            from actions_enricher import enrich_and_save, get_actions_path



            state = _regen_runs[path]



            # Ticker: incrementa el % lentamente durante la llamada a Claude (15% → 80%)

            ticker_active = [True]

            def _tick():

                while ticker_active[0] and state['pct'] < 80:

                    _time.sleep(3)

                    if ticker_active[0] and state['pct'] < 80:

                        state['pct'] = min(80, state['pct'] + 1)

            threading.Thread(target=_tick, daemon=True).start()



            try:

                state['pct'] = 15

                state['stage'] = 'generating'

                raw = generate_minutes(transcript_content, synthetic_wav,

                                       extra_context=extra_context.strip() or None,

                                       language=lang)

                ticker_active[0] = False



                if not raw:

                    state['error'] = 'Claude returned empty'

                    state['done'] = True

                    log.error("regenerate_minutes: Claude devolvió vacío")

                    return



                state['pct'] = 85

                state['stage'] = 'saving'

                title, content = extract_title_from_minutes(raw)

                save_minutes(content, md_path)



                state['pct'] = 92

                state['stage'] = 'html'

                try:

                    export_to_html(md_path, title, open_browser=False)

                except Exception as e:

                    log.warning(f"regenerate HTML: {e}")



                state['pct'] = 97

                state['stage'] = 'actions'

                # Con las acciones apagadas (Ajustes) no se rehacen, y el
                # _actions.json se conserva: ahi esta el proyecto de la reunion.
                import config as _cfg
                if _cfg.actions_enabled():

                    ap = get_actions_path(md_path)

                    bak = _backup_actions(ap)

                    if ap.exists():

                        ap.unlink()

                    _enrich_done = threading.Event()

                    def _regen_done(ap=ap, bak=bak):

                        _finish_actions_regen(ap, bak)

                        _enrich_done.set()

                    try:

                        enrich_and_save(md_path, PROJECT_DIR.parent,

                                        on_done=_regen_done)

                    except Exception:

                        _finish_actions_regen(ap, bak)

                        raise

                    _enrich_done.wait(timeout=300)  # wait for actions to be re-enriched



                state['pct'] = 100

                state['stage'] = 'done'

                state['done'] = True



            except Exception as e:

                ticker_active[0] = False

                state['error'] = str(e)

                state['done'] = True

                log.error(f"regenerate_minutes thread: {e}")



        threading.Thread(target=_run, daemon=True, name='RegenerateMinutes').start()

        return True



    def get_regen_status(self, path: str) -> dict:

        """Devuelve el progreso de una regeneración en curso: {pct, stage, done, error}."""

        return _regen_runs.get(path, {'pct': 0, 'stage': '', 'done': False, 'error': ''})



    def regenerate_actions(self, path: str, instruction: str, lang: str = '') -> bool:

        """Regenera SOLO las acciones de una reunión con una instrucción del usuario.

        Reescribe la sección de Acciones del .md via Claude y re-parsea las acciones."""

        md_path = Path(path)

        if not md_path.exists() or not (instruction or '').strip():

            return False



        m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', md_path.stem)

        stem = f"{m.group(1)}-{m.group(2)}-{m.group(3)}_{m.group(4)}-{m.group(5)}" if m else ''



        # Buscar transcript: junto al .md, luego en recordings/; si no, usar el propio .md

        transcript = ''

        sib = md_path.with_name(md_path.stem + '_transcript.txt')

        if sib.exists():

            try: transcript = sib.read_text(encoding='utf-8')

            except Exception: pass

        if not transcript and stem:

            for folder in [RECORDINGS_DIR / 'processed', RECORDINGS_DIR]:

                if folder.exists():

                    cands = list(folder.glob(f"{stem}*_transcript.txt"))

                    if cands:

                        try: transcript = cands[0].read_text(encoding='utf-8')

                        except Exception: pass

                        break

        if not transcript:

            try: transcript = md_path.read_text(encoding='utf-8')

            except Exception: transcript = ''



        if not lang:

            lang = _detect_notes_language(md_path)



        _prune_runs(_actions_regen_runs)

        _actions_regen_runs[path] = {'pct': 5, 'stage': 'start', 'done': False, 'error': ''}



        def _run():

            import time as _time

            from minutes_generator import regenerate_actions_section

            from actions_enricher import enrich_and_save, get_actions_path

            from html_exporter import export_to_html

            state = _actions_regen_runs[path]

            ticker = [True]

            def _tick():

                while ticker[0] and state['pct'] < 80:

                    _time.sleep(3)

                    if ticker[0] and state['pct'] < 80:

                        state['pct'] = min(80, state['pct'] + 1)

            threading.Thread(target=_tick, daemon=True).start()

            try:

                state['pct'] = 15; state['stage'] = 'generating'

                md_text = md_path.read_text(encoding='utf-8')

                sec_re = re.compile(

                    r'\n##\s+(?:Acciones Pendientes|Pending Actions|Accions Pendents)\b.*?(?=\n##\s|\Z)',

                    re.IGNORECASE | re.DOTALL,

                )

                cur_m = sec_re.search(md_text)

                current_section = cur_m.group(0).strip() if cur_m else ''

                new_section = regenerate_actions_section(transcript, current_section, instruction.strip(), lang)

                ticker[0] = False

                if not new_section:

                    state['error'] = 'Claude returned empty'; state['done'] = True

                    log.error("regenerate_actions: Claude devolvió vacío")

                    return

                state['pct'] = 85; state['stage'] = 'saving'

                if cur_m:

                    md_text = md_text[:cur_m.start()] + '\n' + new_section.strip() + '\n' + md_text[cur_m.end():]

                else:

                    md_text = md_text.rstrip() + '\n\n' + new_section.strip() + '\n'

                md_path.write_text(md_text, encoding='utf-8')

                state['pct'] = 92; state['stage'] = 'html'

                try:

                    meta = _parse_stem(md_path.stem)

                    export_to_html(md_path, meta['title'], open_browser=False)

                except Exception as e:

                    log.warning(f"regenerate_actions html: {e}")

                state['pct'] = 97; state['stage'] = 'actions'

                ap = get_actions_path(md_path)

                bak = _backup_actions(ap)

                if ap.exists():

                    ap.unlink()

                def _after():

                    _finish_actions_regen(ap, bak)

                    state['pct'] = 100; state['stage'] = 'done'; state['done'] = True

                try:

                    enrich_and_save(md_path, PROJECT_DIR.parent, on_done=_after)

                except Exception:

                    _finish_actions_regen(ap, bak)

                    raise

            except Exception as e:

                ticker[0] = False

                state['error'] = str(e); state['done'] = True

                log.error(f"regenerate_actions thread: {e}")



        threading.Thread(target=_run, daemon=True, name='RegenerateActions').start()

        return True



    def get_actions_regen_status(self, path: str) -> dict:

        return _actions_regen_runs.get(path, {'pct': 0, 'stage': '', 'done': False, 'error': ''})



    def import_transcript(self) -> dict:

        """Abre selector de .txt, genera minutas y acciones a partir del transcript importado."""

        txt_path_str = self.pick_file(['Text Files (*.txt)', 'All files (*.*)'])

        if not txt_path_str:

            return {'ok': False}



        txt_path = Path(txt_path_str)

        try:

            transcript_text = txt_path.read_text(encoding='utf-8')

        except Exception as e:

            log.error(f"import_transcript: read failed: {e}")

            return {'ok': False, 'error': str(e)}



        if not transcript_text.strip():

            return {'ok': False, 'error': 'empty'}



        run_id = str(uuid.uuid4())

        _prune_runs(_import_runs)

        _import_runs[run_id] = {'pct': 5, 'stage': 'reading', 'done': False, 'error': '', 'path': ''}



        def _run():

            import time as _time

            from minutes_generator import generate_minutes, extract_title_from_minutes, save_minutes

            from html_exporter import export_to_html

            from actions_enricher import enrich_and_save

            from storage import get_minutes_path



            state = _import_runs[run_id]



            ticker_active = [True]

            def _tick():

                while ticker_active[0] and state['pct'] < 80:

                    _time.sleep(3)

                    if ticker_active[0] and state['pct'] < 80:

                        state['pct'] = min(80, state['pct'] + 1)

            threading.Thread(target=_tick, daemon=True).start()



            try:

                ts = datetime.now().strftime('%Y-%m-%d_%H-%M')

                fake_wav = RECORDINGS_DIR / f"{ts}_imported_{run_id[:6]}.wav"



                state['pct'] = 15

                state['stage'] = 'generating'

                # El nivel de minutas de Ajustes, como al cerrar una reunion.
                import config as _cfg
                mode = _cfg.minutes_mode()
                if mode == 'full':
                    raw = generate_minutes(transcript_text, fake_wav, language='auto')
                    if not raw:
                        # Como al cerrar una reunion: el transcript importado se
                        # guarda y se ve igual, con unas minutas minimas.
                        from minutes_generator import brief_minutes
                        log.error("import_transcript: Claude no devolvio minutas; se guardan minimas")
                        mode = 'off'
                        raw = brief_minutes('Transcript importado', 'off')
                else:
                    import meeting_qa
                    from minutes_generator import brief_minutes
                    summary = meeting_qa.summarize(transcript_text) if mode == 'summary' else None
                    raw = brief_minutes('Transcript importado', 'summary' if summary else 'off', summary)

                ticker_active[0] = False



                if not raw:

                    state['error'] = 'Claude returned empty'

                    state['done'] = True

                    return



                state['pct'] = 85

                state['stage'] = 'saving'

                title, content = extract_title_from_minutes(raw)

                minutes_path = get_minutes_path(fake_wav, title)

                save_minutes(content, minutes_path)



                try:

                    transcript_copy = minutes_path.with_name(minutes_path.stem + '_transcript.txt')

                    transcript_copy.write_text(transcript_text, encoding='utf-8')

                except Exception as e:

                    log.warning(f"import_transcript: transcript copy: {e}")



                state['pct'] = 92

                state['stage'] = 'html'

                try:

                    export_to_html(minutes_path, title, open_browser=False)

                except Exception as e:

                    log.warning(f"import_transcript HTML: {e}")



                state['pct'] = 97

                state['stage'] = 'actions'

                if mode == 'full' and _cfg.actions_enabled():
                    enrich_and_save(minutes_path, PROJECT_DIR.parent)
                else:
                    from actions_enricher import save_without_actions
                    save_without_actions(minutes_path, PROJECT_DIR.parent,
                                         extra_text=transcript_text[:4000] if mode != 'full' else None)



                state['pct'] = 100

                state['stage'] = 'done'

                state['done'] = True

                state['path'] = str(minutes_path)



            except Exception as e:

                ticker_active[0] = False

                state['error'] = str(e)

                state['done'] = True

                log.error(f"import_transcript thread: {e}")



        threading.Thread(target=_run, daemon=True, name='ImportTranscript').start()

        return {'ok': True, 'run_id': run_id}



    def pick_audio_files(self) -> list:
        """Selector nativo de audio y video (varios). De cada archivo devuelve lo que el
        cuadro de importacion propone: titulo (su nombre) y fecha (la de modificacion)."""
        import inbox_watcher as iw
        exts = ';'.join(f'*{e}' for e in sorted(iw.ALL_EXTS))
        out = []
        for p in self.pick_files([f'Audio y video ({exts})', 'All files (*.*)']):
            try:
                f = Path(p)
                st = f.stat()
            except OSError:
                continue
            out.append({'path': str(f), 'name': f.name,
                        'title': re.sub(r'_+', ' ', f.stem).strip(),
                        'when': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%dT%H:%M'),
                        'size_mb': round(st.st_size / 1_048_576, 1)})
        return out

    def import_audio(self, path: str, title: str = '', when: str = '') -> dict:
        """Importa un audio o video que no grabo Noted: lo COPIA a inbox/ con la fecha y
        el titulo elegidos, y el daemon (InboxWatcher) lo convierte y lo procesa como a
        cualquier reunion. El panel y el daemon son procesos distintos: inbox/ es el
        canal entre los dos. El original no se mueve ni se toca.

        Devuelve {'ok': True, 'name', 'stem'} o {'ok': False, 'error': codigo} con
        not_found, bad_ext, empty, no_ffmpeg, no_space o copy_failed (la UI los traduce)."""
        import inbox_watcher as iw
        from storage import _COLLISION_SEP, _recording_stem_taken, _slugify
        src = Path(path or '')
        try:
            if not src.is_file():
                return {'ok': False, 'error': 'not_found'}
            size = src.stat().st_size
        except OSError:
            return {'ok': False, 'error': 'not_found'}
        ext = src.suffix.lower()
        if ext not in iw.ALL_EXTS:
            return {'ok': False, 'error': 'bad_ext'}
        if size <= 0:
            return {'ok': False, 'error': 'empty'}
        # Sin ffmpeg solo se procesan los .wav: lo demas se quedaria en inbox/ sin
        # que nadie lo avise. Mejor decirlo antes de copiar nada.
        if ext != '.wav' and not iw._find_ffmpeg():
            return {'ok': False, 'error': 'no_ffmpeg'}
        try:
            iw.INBOX_DIR.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(iw.INBOX_DIR).free < size + 500 * 1_048_576:
                return {'ok': False, 'error': 'no_space'}
        except OSError as e:
            log.warning(f"import_audio: disco: {e}")

        try:
            dt = datetime.fromisoformat((when or '').strip())
        except ValueError:
            dt = datetime.now()
        base = f"{dt.strftime('%Y-%m-%d_%H-%M')}_{_slugify(title or '') or _slugify(src.stem) or 'Audio_importado'}"
        stem, n = base, 2
        while _recording_stem_taken(stem) or any(iw.INBOX_DIR.glob(glob.escape(stem) + '.*')):
            stem = f"{base}{_COLLISION_SEP}{n}"
            n += 1

        # Primero a un temporal que el watcher ignora (.importing no es una extension
        # de audio) y luego se renombra: un archivo grande copiandose parece estable
        # si la copia se para un momento, y se procesaria a medias.
        tmp = iw.INBOX_DIR / f"{stem}{ext}.importing"
        final = iw.INBOX_DIR / f"{stem}{ext}"
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, final)
        except OSError as e:
            log.error(f"import_audio: no se pudo copiar {src.name}: {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return {'ok': False, 'error': 'copy_failed'}
        log.info(f"import_audio: {src.name} -> inbox/{final.name}")
        return {'ok': True, 'name': final.name, 'stem': stem}

    def get_import_status(self, run_id: str) -> dict:

        """Devuelve el progreso de una importación en curso: {pct, stage, done, error, path}."""

        return _import_runs.get(run_id, {'pct': 0, 'stage': '', 'done': False, 'error': '', 'path': ''})

    def run_semantic_layer(self, path: str, title: str = '', extra_docs: list = None) -> dict:
        """Genera la capa semántica (OntoForge) de una reunión. Delega en el módulo
        exporters.semantic_layer; aquí solo se resuelve el transcript. `extra_docs` son
        documentos adicionales elegidos por el usuario para enriquecer la captura."""
        transcript_text = self.get_transcript_text(path)
        return semantic.start_run(path, transcript_text, title, extra_docs)

    def get_semantic_status(self, run_id: str) -> dict:
        """Progreso de una ejecución de Semantic Layer (delegado)."""
        return semantic.get_status(run_id)

    def get_semantic_info(self, path: str) -> dict:
        """Info de la capa semántica generada para una reunión (delegado)."""
        return semantic.get_info(path)

    def reveal_file(self, file_path: str) -> bool:
        """Abre el Explorador con el fichero seleccionado (delegado)."""
        return semantic.reveal_file(file_path)

    def open_file(self, file_path: str) -> bool:
        """Abre un fichero con su app por defecto (delegado)."""
        return semantic.open_file(file_path)

    def open_screenshot(self, md_path: str, file_name: str) -> bool:
        """Abre una captura que nombra el transcript de una reunion. Solo un nombre de
        captura, sin carpetas, que este junto a sus minutas: el nombre sale del texto
        del transcript, y no puede servir para abrir otra cosa."""
        name = (file_name or '').strip()
        if not re.fullmatch(r'[^/\\:]+_screenshot_(p\d+_)?\d{2,}-\d{2}(_\d+)?\.png', name):
            log.warning(f"open_screenshot: nombre no valido: {name[:80]}")
            return False
        path = Path(md_path).parent / name
        if not path.is_file():
            log.warning(f"open_screenshot: no esta junto a las minutas: {name}")
            return False
        return semantic.open_file(str(path))

    # ── Capturas de la reunion (pestaña «Capturas») ──────────────────────────────

    @staticmethod
    def _shot_path(md_path: str, file_name: str) -> Path | None:
        """La captura `file_name` de las minutas `md_path`, o None. Solo el nombre de
        una captura DE ESTA reunion, sin carpetas: sale del DOM y no puede servir para
        leer otra cosa."""
        name = (file_name or '').strip()
        stem = Path(md_path).stem
        if not name.startswith(stem + '_screenshot_') or not re.fullmatch(
                r'[^/\\:]+_screenshot_(p\d+_)?\d{2,}-\d{2}(_\d+)?\.png', name):
            return None
        path = Path(md_path).parent / name
        return path if path.is_file() else None

    def list_screenshots(self, md_path: str) -> list:
        """Las capturas de una reunion, en orden: [{file, time}] ('MM:SS' del minuto en
        que se tomaron). Sin leer las imagenes: abrir la reunion no debe esperar."""
        try:
            md = Path(md_path)
            pat = re.compile(re.escape(md.stem) + r'_screenshot_(?:p(\d+)_)?(\d{2,})-(\d{2})(?:_\d+)?\.png')
            found = []
            for f in md.parent.glob(f'{glob.escape(md.stem)}_screenshot_*.png'):
                m = pat.fullmatch(f.name)
                if m:
                    part, mm, ss = int(m.group(1) or 1), int(m.group(2)), int(m.group(3))
                    found.append(((part, mm * 60 + ss, f.name), {'file': f.name, 'time': f'{mm:02d}:{ss:02d}'}))
            return [item for _, item in sorted(found)]
        except Exception as e:
            log.warning(f"list_screenshots: {e}")
            return []

    def get_screenshot_thumb(self, md_path: str, file_name: str) -> str:
        """Miniatura de una captura como data URL (JPEG de 480 px de ancho), o ''."""
        path = self._shot_path(md_path, file_name)
        if path is None:
            return ''
        try:
            import base64
            import io
            from PIL import Image
            with Image.open(path) as im:
                im = im.convert('RGB')
                im.thumbnail((480, 480))
                buf = io.BytesIO()
                im.save(buf, 'JPEG', quality=80)
            return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')
        except Exception as e:
            log.warning(f"get_screenshot_thumb {file_name}: {e}")
            return ''

    def copy_screenshot(self, md_path: str, file_name: str) -> bool:
        """Deja la captura (a tamaño completo) en el portapapeles, lista para pegar en
        un correo, Word, PowerPoint o Teams. Desde Python: el portapapeles de imagenes
        de la ventana web no es fiable en todas las instalaciones."""
        path = self._shot_path(md_path, file_name)
        if path is None:
            log.warning(f"copy_screenshot: no valida: {(file_name or '')[:80]}")
            return False
        try:
            import io
            import win32clipboard
            from PIL import Image
            with Image.open(path) as im:
                buf = io.BytesIO()
                im.convert('RGB').save(buf, 'BMP')
            dib = buf.getvalue()[14:]                  # un DIB es el BMP sin su cabecera de fichero
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
            finally:
                win32clipboard.CloseClipboard()
            return True
        except Exception as e:
            log.error(f"copy_screenshot {file_name}: {e}")
            return False

    def open_url(self, url: str) -> bool:
        """Abre una URL http/https en el navegador por defecto."""
        import webbrowser
        u = (url or '').strip()
        if not (u.startswith('http://') or u.startswith('https://')):
            log.warning(f"open_url: URL no válida: {u[:80]}")
            return False
        try:
            webbrowser.open(u)
            return True
        except Exception as e:
            log.error(f"open_url: {e}")
            return False

    def get_transcript_text(self, path: str) -> str:

        try:

            md_path = Path(path)

            stem = md_path.stem

            transcript = md_path.parent / f"{stem}_transcript.txt"

            if transcript.exists():

                return transcript.read_text(encoding='utf-8')

            from config import RECORDINGS_DIR

            for folder in (RECORDINGS_DIR / 'processed', RECORDINGS_DIR):

                for ext in ('_transcript.txt', '.txt'):

                    candidate = folder / f"{stem}{ext}"

                    if candidate.exists():

                        return candidate.read_text(encoding='utf-8')

                for lang_candidate in folder.glob(f"{glob.escape(stem)}*.txt"):

                    return lang_candidate.read_text(encoding='utf-8')

            return ''

        except Exception:

            return ''

    def get_live_transcript(self, stem: str) -> dict:
        """Transcript en vivo de la grabacion en curso (live_transcriber.py).

        El stem llega del job 'recording' de .pipeline_status.json ('live_stem').
        """
        empty = {'lines': [], 'state': None, 'lang': None, 'mode': None}
        try:
            from config import RECORDINGS_DIR
            import live_transcriber
            if not stem or Path(stem).name != stem:
                return empty
            return live_transcriber.live_view(RECORDINGS_DIR / f'{stem}.wav')
        except Exception as e:
            log.warning(f"get_live_transcript: {e}")
            return empty

    def switch_live(self, stem: str) -> bool:
        """Pide al daemon pasar la grabacion en curso a transcript en vivo. Va por
        .cli_command, el canal que ya usa la CLI; el daemon comprueba que el stem
        es el de la grabacion actual (main._start_cli_listener)."""
        try:
            from config import CLI_CONTROL_FILE
            if not stem or Path(stem).name != stem or any(c.isspace() for c in stem):
                return False
            CLI_CONTROL_FILE.write_text(f'live {stem}', encoding='utf-8')
            return True
        except Exception as e:
            log.warning(f"switch_live: {e}")
            return False

    def ask_meeting(self, source: str, ref: str, question: str, history: list | None = None) -> dict:
        """Pregunta sobre una reunion (meeting_qa): en vivo (source='live', ref =
        stem de la grabacion en curso) o terminada (source='meeting', ref = ruta
        de su .md). Devuelve {'ok', 'run_id', 'upto'}; la respuesta se va
        leyendo con get_ask_status, segun la escribe el modelo.

        history: [{'q', 'a'}] de esta conversacion, para poder repreguntar. Lo
        lleva el panel en memoria: no se guarda en ningun sitio.
        """
        import meeting_qa
        import transcriber
        question = (question or '').strip()
        if not question:
            return {'ok': False, 'error': 'empty'}
        err = self._ask_ref_error(source, ref)
        if err:
            return {'ok': False, 'error': err}
        if source == 'live':
            lines = self.get_live_transcript(ref).get('lines') or []
        else:
            lines = self.get_transcript_text(ref).splitlines()
        lines = [line for line in lines if line.strip()]
        if not lines:
            return {'ok': False, 'error': 'no_transcript'}
        hist = [h for h in (history or []) if isinstance(h, dict)][-meeting_qa.HISTORY_TURNS:]
        upto = meeting_qa.last_timestamp(lines)
        run_id = uuid.uuid4().hex[:12]
        _prune_runs(_ask_runs)
        state = _ask_runs[run_id] = {'text': '', 'done': False, 'error': None, 'upto': upto}
        key = f'{source}:{ref}'
        _ask_closed.discard(key)

        def on_text(t):
            state['text'] = t

        def run():
            try:
                # Una sola vez, sin sesion: paga el arranque, pero responde. Es la
                # red si la sesion muere, y lo de siempre grabando con poco disco
                # o memoria: asi el claude termina al responder.
                one_shot = _ask_yield_reason() is not None
                if one_shot:
                    # Antes de abrir otro claude, cerrar las que hubiera. Y una que
                    # siguiera abierta no veria esta pregunta: la siguiente sesion
                    # arranca con el historial.
                    _sweep_ask_sessions()
                else:
                    r = _ask_session(key).ask(lines, question, hist, on_text=on_text)
                    if r['error'] == 'session_dead' and key in _ask_closed:
                        r['error'] = 'closed'
                    elif r['error'] == 'session_dead':
                        one_shot = True
                if one_shot:
                    r = meeting_qa.ask(lines, question, hist, me_label=transcriber.ME_NAME or 'Grabador',
                                       on_text=on_text)
                state['text'] = r['text'] or state['text']
                state['error'] = r['error']
            except Exception as e:
                state['error'] = str(e)
            finally:
                state['done'] = True

        threading.Thread(target=run, daemon=True, name='AskMeeting').start()
        return {'ok': True, 'run_id': run_id, 'upto': upto}

    def _ask_ref_error(self, source: str, ref: str) -> str | None:
        if source == 'live':
            return None if ref and Path(ref).name == ref else 'bad_ref'
        if source == 'meeting':
            # Comparando textos, sin resolve(): con una ruta de red (\\host\share)
            # resolve() contactaba con ella antes de rechazarla.
            md = Path(ref or '')
            expected = MINUTES_DIR / md.name
            ok = md.suffix == '.md' and os.path.normcase(str(md)) == os.path.normcase(str(expected))
            return None if ok else 'bad_ref'
        return 'bad_source'

    def ask_warmup(self, source: str, ref: str) -> bool:
        """Abre ya la sesion de claude de esa conversacion: arrancar cuesta
        9,5-12 s, y asi la primera pregunta responde en un par de segundos.
        Grabando con poco disco o memoria, no: la pregunta ira sin sesion."""
        if self._ask_ref_error(source, ref) or _ask_yield_reason():
            return False
        threading.Thread(target=_ask_session, args=(f'{source}:{ref}',), daemon=True,
                         name='AskWarmup').start()
        return True

    def ask_close(self, source: str, ref: str) -> bool:
        """Cierra la sesion al cerrar la vista: cada una es un proceso abierto."""
        key = f'{source}:{ref}'
        _ask_closed.add(key)
        with _ask_sessions_lock:
            s = _ask_sessions.pop(key, None)
        if s is not None:
            s.close()
        return True

    def get_ask_status(self, run_id: str) -> dict:
        """{'text', 'done', 'error', 'upto'} de una pregunta en curso o terminada."""
        st = _ask_runs.get(run_id)
        return dict(st) if st else {'text': '', 'done': True, 'error': 'unknown_run', 'upto': None}



    def get_stickies(self, path: str) -> list:

        try:

            import json as _json

            stickies_path = Path(path).parent / f"{Path(path).stem}.stickies.json"

            if stickies_path.exists():

                return _json.loads(stickies_path.read_text(encoding='utf-8'))

        except Exception:

            pass

        return []



    def save_stickies(self, path: str, stickies: list) -> None:

        try:

            import json as _json

            stickies_path = Path(path).parent / f"{Path(path).stem}.stickies.json"

            stickies_path.write_text(_json.dumps(stickies), encoding='utf-8')

        except Exception:

            pass



    def export_transcript_file(self, path: str) -> str:

        """Abre un diálogo Guardar Como y exporta el transcript de la reunión al destino elegido.

        Devuelve 'ok', 'cancelled', 'no_transcript' o 'error'."""

        md_path = Path(path)



        # Buscar el transcript: primero copia en minutes/, luego recordings/

        transcript_text = ''

        minutes_transcript = md_path.with_name(md_path.stem + '_transcript.txt')

        if minutes_transcript.exists():

            try:

                transcript_text = minutes_transcript.read_text(encoding='utf-8')

            except Exception:

                pass



        if not transcript_text:

            m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', md_path.stem)

            if m:

                y, mo, d, hh, mm = m.groups()

                stem = f"{y}-{mo}-{d}_{hh}-{mm}"

                for folder in [RECORDINGS_DIR / 'processed', RECORDINGS_DIR]:

                    if not folder.exists():

                        continue

                    candidates = list(folder.glob(f"{stem}*_transcript.txt"))

                    if candidates:

                        try:

                            transcript_text = candidates[0].read_text(encoding='utf-8')

                        except Exception:

                            pass

                        break



        if not transcript_text:

            return 'no_transcript'



        default_name = md_path.stem + '_transcript.txt'

        try:

            import webview

            wins = webview.windows

            if not wins:

                return 'error'

            result = wins[0].create_file_dialog(

                webview.SAVE_DIALOG,

                save_filename=default_name,

                file_types=['Text Files (*.txt)', 'All files (*.*)'],

            )

            if not result:

                return 'cancelled'

            dest = result[0] if isinstance(result, (list, tuple)) else result

            Path(dest).write_text(transcript_text, encoding='utf-8')

            return 'ok'

        except Exception as e:

            log.error(f"export_transcript_file: {e}")

            return 'error'



    def execute_action_panel(self, path: str, index: int,

                             working_dir: str = '', prompt_override: str = '') -> str:

        """Ejecuta una acción con Claude en el panel interno. Devuelve run_id para polling."""

        _claude_bin = _get_claude_bin()
        if not _claude_bin:

            return ''  # Sin claude en PATH: JS hace fallback a terminal

        from actions_enricher import load_enriched, update_action_executed

        acts = load_enriched(Path(path))

        if not acts:

            return ''

        action = next((a for a in acts if a['index'] == index), None)

        if not action:

            return ''



        prompt = prompt_override.strip() or action.get('prompt_enriched') or action.get('prompt_original', '')

        if not prompt.strip():

            return ''



        if working_dir.strip():

            wd = Path(working_dir.strip())

            if wd.is_file():

                proj_path = wd.parent

            elif wd.is_dir():

                proj_path = wd

            else:

                proj_path = PROJECT_DIR

        else:

            proj_path = PROJECT_DIR

            proj = action.get('project', '')

            if proj:

                try:

                    projects_file = PROJECT_DIR / 'projects.json'

                    if projects_file.exists():

                        pdata = json.loads(projects_file.read_text(encoding='utf-8'))

                        found = next((p for p in pdata.get('projects', []) if p['id'] == proj or p['name'] == proj), None)

                        if found and found.get('output_dir'):

                            proj_path = Path(found['output_dir'])

                except Exception:

                    pass



        run_id = uuid.uuid4().hex[:8]

        _prune_runs(_action_runs)

        _action_runs[run_id] = {

            'output': '',

            'done': False,

            'error': '',

            'title': action.get('title', ''),

            'prompt': prompt,

            'proj_path': str(proj_path),

            'path': path,

            'index': index,

        }



        def _run():

            try:

                env = _clean_env_panel()

                proc = subprocess.Popen(

                    [_claude_bin, '-p', '--allowedTools', 'Edit,Write,Read,Bash,Glob,Grep'],

                    stdin=subprocess.PIPE,

                    stdout=subprocess.PIPE,

                    stderr=subprocess.PIPE,

                    text=True,

                    encoding='utf-8',

                    cwd=str(proj_path),

                    env=env,

                    creationflags=0x08000000 if os.name == 'nt' else 0,

                )

                proc.stdin.write(prompt)

                proc.stdin.close()

                try:

                    out, err = proc.communicate(timeout=600)

                except subprocess.TimeoutExpired:

                    proc.kill()

                    _action_runs[run_id]['output'] = '[TIMEOUT: la acción tardó más de 10 minutos]'

                    _action_runs[run_id]['done'] = True

                    _action_runs[run_id]['error'] = 'timeout'

                    return

                _action_runs[run_id]['output'] = out

                if err.strip():

                    _action_runs[run_id]['output'] += f'\n[stderr]: {err.strip()}'

                _action_runs[run_id]['done'] = True

                if proc.returncode == 0:

                    update_action_executed(Path(path), index, prompt)

                else:

                    _action_runs[run_id]['error'] = f"Exit {proc.returncode}"

            except Exception as e:

                _action_runs[run_id]['output'] += f'\n[ERROR: {e}]'

                _action_runs[run_id]['done'] = True

                _action_runs[run_id]['error'] = str(e)



        threading.Thread(target=_run, daemon=True, name=f'ActionPanel-{run_id}').start()

        return run_id



    def get_action_run_status(self, run_id: str) -> dict:

        """Devuelve el estado actual de una ejecución en el panel."""

        r = _action_runs.get(run_id)

        if not r:

            return {'output': '', 'done': True, 'error': 'not found', 'title': '', 'proj_path': '', 'path': '', 'index': -1}

        return {

            'output': r['output'],

            'done': r['done'],

            'error': r['error'],

            'title': r['title'],

            'proj_path': r.get('proj_path', ''),

            'path': r.get('path', ''),

            'index': r.get('index', -1),

        }



    def continue_in_terminal(self, path: str, index: int, proj_path: str = '', run_output: str = '') -> None:

        """Opens a terminal in the target file's directory with action context + Claude ready."""

        import subprocess as _sp

        import json as _json

        import re as _re

        import tempfile as _tmp

        from pathlib import Path as _P

        from actions_enricher import get_actions_path

        from config import get_claude_bin as _get_cb
        _cb = _get_cb()



        # Load action data from the meeting's actions JSON

        action = None

        try:

            ap = get_actions_path(_P(path))

            if ap.exists():

                data = _json.loads(ap.read_text(encoding='utf-8'))

                for a in data.get('actions', []):

                    if a.get('index') == index:

                        action = a

                        break

        except Exception:

            pass



        # Find the target file and its directory from the action prompt

        working_dir = None

        target_file = None

        if action:

            prompt = action.get('prompt_enriched') or action.get('prompt_original') or ''

            m = _re.search(r'(?:Archivo|File):\s*([A-Za-z]:\\.+?)(?:\n|$)', prompt)

            if m:

                fp = _P(m.group(1).strip())

                target_file = str(fp)

                if fp.exists():

                    working_dir = str(fp.parent)

                elif fp.parent.exists():

                    working_dir = str(fp.parent)



        if not working_dir:

            working_dir = proj_path if (proj_path and _P(proj_path).exists()) else str(_P(path).parent)



        # Write a context summary to a temp file shown on terminal open

        lines = ['=' * 60 + '\n', 'ACTION CONTEXT\n', '=' * 60 + '\n']

        if action:

            lines.append(f"Task    : {action.get('title', '')}\n")

            if target_file:

                lines.append(f"File    : {target_file}\n")

            lines.append(f"Type    : {action.get('type', '')}\n")

            prompt_text = action.get('prompt_executed') or action.get('prompt_enriched') or ''

            if prompt_text:

                lines.append(f"\nInstruction:\n{prompt_text[:800]}\n")

        if run_output and run_output.strip():

            lines.append('\n' + '=' * 60 + '\n')

            lines.append('CLAUDE ASKED:\n')

            lines.append('=' * 60 + '\n')

            lines.append(run_output.strip()[:4000] + '\n')

            lines.append('=' * 60 + '\n')

            lines.append('\n>> Responde a continuacion. Claude retomara la conversacion.\n')

        lines.append('\n' + '=' * 60 + '\n')

        ctx_file = _P(_tmp.gettempdir()) / f'tr_action_{index}.txt'

        ctx_file.write_text(''.join(lines), encoding='utf-8')



        # Open fresh claude session; copy context to clipboard so user can paste it

        claude_cmd = f'"{_cb}"' if _cb else 'claude'

        ctx_path = str(ctx_file).replace("'", "''")

        clip_cmd = f"powershell -NoProfile -Command \"Get-Content -Path '{ctx_path}' | Set-Clipboard\""

        banner = 'echo. && echo [Contexto copiado al portapapeles. Pega con Ctrl+V en Claude y responde.] && echo.'

        inner = f'type "{ctx_file}" && {clip_cmd} && {banner} && {claude_cmd}'



        CREATE_NEW_CONSOLE = 0x00000010

        try:

            _sp.Popen(['wt.exe', '-d', working_dir, '--', 'cmd.exe', '/K', inner],

                      creationflags=CREATE_NEW_CONSOLE)

        except FileNotFoundError:

            _sp.Popen(['cmd.exe', '/K', f'cd /d "{working_dir}" && {inner}'],

                      creationflags=CREATE_NEW_CONSOLE)



        # Watch for completion in the terminal so the UI can update automatically

        _start_terminal_watcher(path, index, action.get('title', '') if action else '')



    def get_terminal_completions(self) -> list:

        """Returns and clears actions marked as executed from an external terminal session."""

        result = list(_terminal_completions)

        _terminal_completions.clear()

        return result



    def send_action_followup(self, run_id: str, message: str) -> str:

        """Re-ejecuta con contexto adicional. Devuelve nuevo run_id."""

        _claude_bin = _get_claude_bin()
        if not _claude_bin:

            return ''

        prev = _action_runs.get(run_id)

        if not prev:

            return ''



        combined = (

            prev['prompt']

            + '\n\n---\nRespuesta anterior de Claude:\n'

            + prev['output']

            + '\n\n---\nContexto adicional / pregunta del usuario:\n'

            + message.strip()

        )



        new_id = uuid.uuid4().hex[:8]

        _prune_runs(_action_runs)

        _action_runs[new_id] = {

            'output': '',

            'done': False,

            'error': '',

            'title': prev['title'] + ' (seguimiento)',

            'prompt': combined,

            'proj_path': prev['proj_path'],

            'path': prev['path'],

            'index': prev['index'],

        }



        def _run():

            try:

                env = _clean_env_panel()

                proc = subprocess.Popen(

                    [_claude_bin, '-p', '--allowedTools', 'Edit,Write,Read,Bash,Glob,Grep'],

                    stdin=subprocess.PIPE,

                    stdout=subprocess.PIPE,

                    stderr=subprocess.PIPE,

                    text=True,

                    encoding='utf-8',

                    cwd=prev['proj_path'],

                    env=env,

                    creationflags=0x08000000 if os.name == 'nt' else 0,

                )

                proc.stdin.write(combined)

                proc.stdin.close()

                try:

                    out, err = proc.communicate(timeout=600)

                except subprocess.TimeoutExpired:

                    proc.kill()

                    _action_runs[new_id]['output'] = '[TIMEOUT: la acción tardó más de 10 minutos]'

                    _action_runs[new_id]['done'] = True

                    _action_runs[new_id]['error'] = 'timeout'

                    return

                _action_runs[new_id]['output'] = out

                if err.strip():

                    _action_runs[new_id]['output'] += f'\n[stderr]: {err.strip()}'

                _action_runs[new_id]['done'] = True

                if proc.returncode != 0:

                    _action_runs[new_id]['error'] = f"Exit {proc.returncode}"

            except Exception as e:

                _action_runs[new_id]['output'] += f'\n[ERROR: {e}]'

                _action_runs[new_id]['done'] = True

                _action_runs[new_id]['error'] = str(e)



        threading.Thread(target=_run, daemon=True, name=f'ActionPanel-{new_id}').start()

        return new_id



    def search(self, query: str) -> list:

        """Busca query en todas las minutas y devuelve la lista filtrada."""

        q = query.lower().strip()

        if not q:

            return self.get_meetings()

        results = []

        for md in sorted(MINUTES_DIR.glob('*.md'), key=lambda p: p.stat().st_mtime, reverse=True):

            try:

                content = md.read_text(encoding='utf-8').lower()

                if q in content or q in md.stem.lower():

                    meta = _parse_stem(md.stem)

                    actions_path = md.parent / f"{md.stem}_actions.json"

                    pending = 0

                    has_actions = False

                    project_id = ''

                    if actions_path.exists():

                        try:

                            data = json.loads(actions_path.read_text(encoding='utf-8'))

                            acts = data.get('actions', [])

                            has_actions = bool(acts)

                            pending = sum(1 for a in acts if not a.get('executed', False))

                            project_id = data.get('project_id', '')

                        except Exception:

                            pass

                    results.append({

                        'path':          str(md),

                        'title':         meta['title'],

                        'date':          meta['date'],

                        'time':          meta['time'],

                        'has_actions':   has_actions,

                        'pending_count': pending,

                        'project_id':    project_id,

                    })

            except Exception:

                pass

        return results








# ── Helpers ──────────────────────────────────────────────────────────────────



def _detect_notes_language(md_path: Path) -> str:

    """Returns 'es' or 'en' by inspecting the first 600 chars of a minutes file."""

    try:

        head = md_path.read_text(encoding='utf-8', errors='ignore')[:600].lower()

        if any(kw in head for kw in ('minuta', 'reunión', 'asistentes', 'resumen ejecutivo')):

            return 'es'

        if any(kw in head for kw in ('meeting minutes', 'attendees', 'executive summary')):

            return 'en'

    except Exception:

        pass

    return 'es'





def _parse_stem(stem: str) -> dict:

    """Extrae título, fecha y hora del stem de la minuta."""

    m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})_(.*)', stem)

    if m:

        y, mo, d, hh, mm, slug = m.groups()

        title = slug.replace('_', ' ').strip()

        return {

            'title': title or stem,

            'date':  f"{y}-{mo}-{d}",

            'time':  f"{hh}:{mm}",

        }

    return {'title': stem, 'date': '', 'time': ''}





# ── Ventana ───────────────────────────────────────────────────────────────────



def open_app(initial_path: str = None, focus: bool = True):

    """

    Lanza la ventana como proceso separado (pywebview necesita el hilo principal,

    que ya está ocupado por pystray). Si la ventana ya está abierta, le envía

    la ruta de navegación en lugar de abrir una segunda instancia.

    """

    import sys

    pid_file = PROJECT_DIR / '.app_window.pid'

    nav_file = PROJECT_DIR / '.app_navigate.txt'

    if pid_file.exists():

        try:

            import psutil

            pid = int(pid_file.read_text(encoding='utf-8').strip())

            if psutil.pid_exists(pid):

                nav_file.write_text(initial_path or '', encoding='utf-8')

                if focus:

                    _focus_existing_window()

                return

        except Exception:

            pass

        pid_file.unlink(missing_ok=True)



    if os.name == 'nt':

        try:

            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY

        except Exception:

            pass

    args = [sys.executable, str(Path(__file__).resolve())]

    if initial_path:

        args.append(initial_path)

    subprocess.Popen(args, creationflags=0x08000000 if os.name == 'nt' else 0)





def _set_taskbar_icon(title: str, icon_path: str) -> None:

    """Envía WM_SETICON al HWND para que el icono aparezca en la taskbar de Windows."""

    if os.name != 'nt':

        return

    try:

        import ctypes

        hwnd = ctypes.windll.user32.FindWindowW(None, title)

        if not hwnd:

            return

        # IMAGE_ICON=1, LR_LOADFROMFILE=0x10, LR_DEFAULTSIZE=0x40

        # LR_DEFAULTSIZE lets Windows pick SM_CXICON (32px) — loads our pre-optimised 32px frame directly

        icon = ctypes.windll.user32.LoadImageW(None, icon_path, 1, 0, 0, 0x10 | 0x40)

        if icon:

            ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, icon)  # ICON_BIG  (taskbar)

            ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, icon)  # ICON_SMALL (title bar)

    except Exception as e:

        log.warning(f"_set_taskbar_icon: {e}")






# Un solo panel. 26/10/2026: un doble clic en el icono de la bandeja abria dos
# (pystray lanza "Abrir la aplicacion" en cada WM_LBUTTONUP, y .app_window.pid
# aun no existia). Mutex con nombre: el patron estandar de Windows, el mismo
# que usa main.py para el daemon.
_WINDOW_MUTEX = 'Noted_AppWindow'
_WINDOW_MUTEX_HANDLE = None


def _claim_single_window() -> bool:
    """True si este proceso es el unico panel. Si no se puede saber, True:
    mejor abrir de mas que no abrir."""
    global _WINDOW_MUTEX_HANDLE
    if os.name != 'nt':
        return True
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        handle = k32.CreateMutexW(None, False, _WINDOW_MUTEX)
        if not handle:
            return True
        _WINDOW_MUTEX_HANDLE = handle            # vive lo que viva el proceso
        return ctypes.get_last_error() != 183    # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _focus_existing_window() -> None:
    if os.name != 'nt':
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, 'Noted')
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)        # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _release_pid_file(pid_file: Path, pid: int) -> None:
    """Al cerrar, borrar el .app_window.pid solo si es el nuestro: con dos
    paneles, cerrar uno lo borraba y cada clic siguiente abria otro mas."""
    try:
        if pid_file.read_text(encoding='utf-8').strip() == str(pid):
            pid_file.unlink(missing_ok=True)
    except OSError:
        pass


def _run_window(initial_path: str = None):
    """Punto de entrada cuando se ejecuta como proceso independiente."""
    global _window
    if not _claim_single_window():
        # Ya hay un panel: que vaya a esta reunion (lo lee de .app_navigate.txt)
        # y se ponga delante. Sin cargar pywebview ni abrir ventana.
        (PROJECT_DIR / '.app_navigate.txt').write_text(initial_path or '', encoding='utf-8')
        _focus_existing_window()
        return
    import webview
    _cleanup_old_tmp_dirs()

    pid_file = PROJECT_DIR / '.app_window.pid'
    pid_file.write_text(str(os.getpid()), encoding='utf-8')
    import atexit
    _my_pid = os.getpid()
    atexit.register(lambda: _release_pid_file(pid_file, _my_pid))

    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Noted.App')
        except Exception:
            pass

    _icon = Path(__file__).parent / 'noted_icon.ico'
    api = AppAPI()

    win = webview.create_window(
        'Noted',
        url=str(_WEB_DIR / 'index.html'),
        js_api=api,
        width=1140,
        height=780,
        min_size=(800, 600),
        maximized=True,
        text_select=True,
    )
    _window = win

    def on_ready():
        if _icon.exists():
            _set_taskbar_icon('Noted', str(_icon))
        if initial_path:
            win.evaluate_js(f"if(typeof openMeeting==='function') openMeeting({json.dumps(initial_path)})")
        if os.name == 'nt':
            try:
                import ctypes
                hwnd = ctypes.windll.user32.FindWindowW(None, 'Noted')
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    win.events.loaded += on_ready
    try:
        webview.start(icon=str(_icon) if _icon.exists() else None, debug=False)
    except Exception as _wv_err:
        log.error(f"webview.start falló: {_wv_err}", exc_info=True)
        _msg = str(_wv_err).lower()
        if 'webview2' in _msg or 'edgechromium' in _msg:
            try:
                import tkinter.messagebox as _mb
                _mb.showerror(
                    'Noted — Componente requerido no encontrado',
                    'La aplicación requiere Microsoft WebView2 Runtime.\n\n'
                    'Descárgalo desde:\n'
                    'https://developer.microsoft.com/microsoft-edge/webview2/\n\n'
                    '(Instalador "Evergreen Bootstrapper")',
                )
            except Exception:
                pass
        import sys
        sys.exit(1)



if __name__ == '__main__':

    import sys

    _initial = sys.argv[1] if len(sys.argv) > 1 else None

    _run_window(_initial)
