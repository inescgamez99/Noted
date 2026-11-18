import json
import logging
import os
import subprocess
import threading
from datetime import datetime, UTC
from pathlib import Path

from config import get_claude_bin as _get_claude_bin, PROJECT_DIR, clean_env as _clean_env

log = logging.getLogger(__name__)

_CLAUDE_TYPES = {'instruction', 'code_change', 'document_change'}


def _detect_language(text: str) -> str:
    """Returns 'en' or 'es' based on section headers present in the minutes."""
    en_score = sum(1 for m in ('Executive Summary', 'Attendees', 'Topics Discussed', 'Pending Actions') if m in text)
    es_score = sum(1 for m in ('Resumen Ejecutivo', 'Asistentes', 'Temas Tratados', 'Acciones Pendientes') if m in text)
    return 'en' if en_score >= es_score else 'es'


def get_actions_path(minutes_path: Path) -> Path:
    return minutes_path.parent / f"{minutes_path.stem}_actions.json"


def load_enriched(minutes_path: Path) -> list[dict] | None:
    ap = get_actions_path(minutes_path)
    if not ap.exists():
        return None
    if ap.stat().st_mtime < minutes_path.stat().st_mtime:
        return None
    try:
        data = json.loads(ap.read_text(encoding='utf-8'))
        return data.get('actions', [])
    except Exception:
        return None


def update_action_prompt(minutes_path: Path, action_index: int, prompt: str):
    ap = get_actions_path(minutes_path)
    if not ap.exists():
        return
    try:
        data = json.loads(ap.read_text(encoding='utf-8'))
        for a in data.get('actions', []):
            if a['index'] == action_index:
                a['prompt_enriched'] = prompt
                break
        ap.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        log.warning(f"update_action_prompt: {e}")


def update_action_executed(minutes_path: Path, action_index: int, prompt_executed: str):
    ap = get_actions_path(minutes_path)
    if not ap.exists():
        return
    try:
        data = json.loads(ap.read_text(encoding='utf-8'))
        for a in data.get('actions', []):
            if a['index'] == action_index:
                a['executed'] = True
                a['prompt_executed'] = prompt_executed
                break
        ap.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        log.warning(f"update_action_executed: {e}")


def enrich_and_save(minutes_path: Path, projects_dir: Path, on_done=None):
    t = threading.Thread(
        target=_run_enrichment, args=(minutes_path, projects_dir, on_done),
        daemon=True, name='ActionsEnricher',
    )
    t.start()


def _run_enrichment(minutes_path: Path, projects_dir: Path, on_done=None):
    from actions_parser import parse_actions, parse_table_actions, merge_actions

    try:
        text = minutes_path.read_text(encoding='utf-8')
        lang = _detect_language(text)

        # Parse both sources and merge duplicates
        claude_actions = parse_actions(text, projects_dir)
        human_actions = parse_table_actions(text, start_index=len(claude_actions))
        all_actions = merge_actions(claude_actions, human_actions)

        if not all_actions:
            # on_done lo llama el finally: llamarlo tambien aqui archivaba y
            # exportaba la reunion dos veces. Y sin acciones el proyecto se
            # detecta igual: si no, la reunion no se archivaba.
            log.info("Sin acciones en las minutas")
            _save_json(minutes_path, all_actions, {})
            _detect_and_save_project(minutes_path, projects_dir)
            return

        enriched_map = {}

        # Only enrich claude-type actions (human ones already have all the info they need)
        _claude_bin = _get_claude_bin()
        if claude_actions and _claude_bin:
            # Resúmenes de proyectos
            project_summaries = []
            if projects_dir and projects_dir.exists():
                for d in projects_dir.iterdir():
                    if not d.is_dir():
                        continue
                    summary = ''
                    for fname in ('CLAUDE.md', 'README.md', 'readme.md'):
                        f = d / fname
                        if f.exists():
                            content = f.read_text(encoding='utf-8', errors='ignore')
                            summary = content[:350]
                            break
                    project_summaries.append({'name': d.name, 'summary': summary[:350]})

            proj_block = '\n'.join(
                f"- {p['name']}: {p['summary'][:200]}" for p in project_summaries
            ) or '(ninguno)'

            actions_block = '\n'.join(
                f"{a.index}. [{a.type}] {a.title}\n   Archivo: {a.archivo}\n   {a.prompt[:300]}"
                for a in claude_actions
            )

            if lang == 'en':
                lang_instruction = "Write the 'prompt' field in English, matching the language of the meeting."
                intro = f"Given these available projects:\n{proj_block}\n\nAnd these actions from the meeting:\n{actions_block}"
                footer = 'For each action return a JSON array with exactly these fields:\n[{"index": N, "project": "project_name_or_null", "assignee": "person_name_or_null", "prompt": "complete_enriched_prompt"}]\n\nRespond ONLY with the JSON array, no additional text.'
            else:
                lang_instruction = "Escribe el campo 'prompt' en español, en el mismo idioma que la reunión."
                intro = f"Dado este listado de proyectos disponibles:\n{proj_block}\n\nY estas acciones de la reunion:\n{actions_block}"
                footer = 'Para cada accion devuelve un JSON array con exactamente estos campos:\n[{"index": N, "project": "nombre_proyecto_o_null", "assignee": "nombre_persona_o_null", "prompt": "prompt_enriquecido_completo"}]\n\nResponde SOLO con el JSON array, sin texto adicional.'

            prompt = f"""{intro}

{lang_instruction}

{footer}"""

            try:
                env = _clean_env()

                if os.name == 'nt':
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    si.wShowWindow = 0
                    CREATE_NO_WINDOW = 0x08000000
                else:
                    si = None
                    CREATE_NO_WINDOW = 0

                result = subprocess.run(
                    [_claude_bin, '-p'],
                    input=prompt, capture_output=True, text=True,
                    encoding='utf-8', timeout=120, env=env,
                    startupinfo=si if os.name == 'nt' else None,
                    creationflags=CREATE_NO_WINDOW if os.name == 'nt' else 0,
                )
                if result.returncode == 0:
                    raw = result.stdout.strip()
                    import re
                    m = re.search(r'\[.*\]', raw, re.DOTALL)
                    if m:
                        parsed = json.loads(m.group(0))
                        enriched_map = {item['index']: item for item in parsed}
                else:
                    from minutes_generator import _cli_error
                    log.warning(f"Enrichment CLI rc={result.returncode}: "
                                f"{_cli_error(result.stdout, result.stderr)[:300]}")
            except Exception as e:
                log.warning(f"Enrichment CLI failed: {e}")

        _save_json(minutes_path, all_actions, enriched_map)

        # Detect which project this meeting belongs to
        _detect_and_save_project(minutes_path, projects_dir)

    except Exception as e:
        log.error(f"_run_enrichment error: {e}", exc_info=True)
    finally:
        if on_done:
            try:
                on_done()
            except Exception as e:
                log.warning(f"_run_enrichment: on_done fallo: {e}", exc_info=True)


def save_without_actions(minutes_path: Path, projects_dir: Path, extra_text: str | None = None,
                         on_done=None):
    """Acciones apagadas o minutas breves (decision de Felipe, 19/10/2026): sin
    extraer ni enriquecer acciones, pero con su _actions.json vacio y el
    proyecto detectado, que es de lo que dependen el archivado, la exportacion
    y scan_meetings.py. extra_text: el principio del transcript, cuando las
    minutas son breves y no bastan para reconocer el proyecto."""
    def run():
        try:
            _save_json(minutes_path, [], {})
            _detect_and_save_project(minutes_path, projects_dir, extra_text)
        except Exception as e:
            log.error(f"save_without_actions: {e}", exc_info=True)
        finally:
            if on_done:
                try:
                    on_done()
                except Exception as e:
                    log.warning(f"save_without_actions: on_done fallo: {e}", exc_info=True)
    threading.Thread(target=run, daemon=True, name='ProjectDetect').start()


def _detect_and_save_project(minutes_path: Path, projects_dir: Path, extra_text: str | None = None,
                             title_fallback: bool = True):
    """Ask Claude to identify which project this meeting belongs to. Si Claude no
    responde (sin sesion, sin CLI), el proyecto que nombre el titulo, como
    suposicion (_save_project_from_title): hasta el 28/10/2026 esas reuniones se
    quedaban sin proyecto. title_fallback=False en la deteccion masiva: con Claude
    caido no se aplica una suposicion a todo el historial."""
    # Load projects
    projects_file = PROJECT_DIR / 'projects.json'
    if not projects_file.exists():
        return
    try:
        pdata = json.loads(projects_file.read_text(encoding='utf-8'))
        projects = pdata.get('projects', [])
    except Exception:
        return
    if not projects:
        return

    # Read meeting content
    try:
        raw = minutes_path.read_text(encoding='utf-8')
    except Exception:
        return

    # Extract clean title from TITULO: line if present
    meeting_title = minutes_path.stem
    for line in raw.splitlines()[:5]:
        if line.startswith('TITULO:'):
            meeting_title = line.removeprefix('TITULO:').strip()
            break

    _claude_bin = _get_claude_bin()
    if not _claude_bin:
        if title_fallback:
            _save_project_from_title(minutes_path, meeting_title, projects)
        return

    content = raw[:4000]
    if extra_text:
        content += f"\n\nTranscript (beginning):\n{extra_text[:4000]}"

    proj_block = '\n'.join(
        f"- id: {p['id']} | name: {p['name']} | description: {p.get('description','')} | stakeholders: {', '.join(p.get('stakeholders', []))}"
        for p in projects
    )

    prompt = f"""Given these projects:
{proj_block}

Meeting title: {meeting_title}
Meeting content (excerpt):
{content}

Which project does this meeting belong to? Consider the title, content, people mentioned, client names, and topics.
Return ONLY the project id from the list above (e.g. "my-project"), or "none" if no project matches clearly.
No explanation, just the id or "none"."""

    try:
        env = _clean_env()
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            CREATE_NO_WINDOW = 0x08000000
        else:
            si = None
            CREATE_NO_WINDOW = 0
        # Sin guardar la sesion: el prompt lleva extracto de minutas o transcript.
        result = subprocess.run(
            [_claude_bin, '-p', '--no-session-persistence'],
            input=prompt, capture_output=True, text=True,
            encoding='utf-8', timeout=60, env=env,
            startupinfo=si if os.name == 'nt' else None,
            creationflags=CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        if result.returncode == 0:
            detected = result.stdout.strip().lower().strip('"\'')
            valid_ids = {p['id'] for p in projects}
            project_id = detected if detected in valid_ids else 'none'
            if _store_project_id(minutes_path, project_id):
                log.info(f"Project detected: {project_id} for {minutes_path.name}")
            return
        log.warning(f"Project detection: claude rc={result.returncode}: "
                    f"{((result.stderr or '').strip() or (result.stdout or '').strip())[:300]}")
    except Exception as e:
        log.warning(f"Project detection failed: {e}")
    if title_fallback:
        _save_project_from_title(minutes_path, meeting_title, projects)


def _store_project_id(minutes_path: Path, project_id: str, source: str | None = None) -> bool:
    """Guarda el proyecto de la reunion. source='title' lo marca como suposicion
    (no se archiva ni se exporta, y la deteccion con Claude lo revisa). El que
    eligio el usuario (project_source 'manual') no se toca."""
    ap = get_actions_path(minutes_path)
    if not ap.exists():
        return False
    adata = json.loads(ap.read_text(encoding='utf-8'))
    if adata.get('project_source') == 'manual':
        log.info(f"Proyecto elegido a mano en {minutes_path.name}: se conserva")
        return False
    adata['project_id'] = project_id
    if source:
        adata['project_source'] = source
    else:
        adata.pop('project_source', None)
    ap.write_text(json.dumps(adata, ensure_ascii=False, indent=2), encoding='utf-8')
    return True


def _save_project_from_title(minutes_path: Path, meeting_title: str, projects: list) -> None:
    """Sin respuesta de Claude: el proyecto que nombre el titulo, marcado como
    suposicion. El panel lo muestra, pero no se archiva ni se exporta a la
    carpeta del proyecto hasta que Claude o el usuario lo confirmen: una
    reunion en la carpeta de otro cliente es peor que una sin proyecto. Si el
    titulo no nombra ninguno (o empatan dos), sin project_id, para que una
    deteccion posterior con Claude lo vuelva a intentar."""
    from project_context import project_from_title
    try:
        p = project_from_title(meeting_title, projects)
        if p and _store_project_id(minutes_path, p['id'], source='title'):
            log.info(f"Proyecto por el título (sin Claude): {p['id']} para {minutes_path.name}")
        elif not p:
            log.info(f"Sin proyecto para {minutes_path.name}: Claude no respondió y el título "
                     f"no nombra ninguno")
    except Exception as e:
        log.warning(f"Proyecto por el título: {e}")


def add_manual_action(minutes_path: Path, title: str,
                      deadline: str = '', assignee: str = '') -> dict | None:
    """Añade una acción manual (type='human') a la reunión. Crea el JSON si no existe."""
    import re as _re
    ap = get_actions_path(minutes_path)
    try:
        if ap.exists():
            data = json.loads(ap.read_text(encoding='utf-8'))
        else:
            data = {
                'minutes':      str(minutes_path),
                'generated_at': datetime.now(UTC).isoformat(),
                'actions':      [],
            }
        actions = data.setdefault('actions', [])
        next_index = max((a.get('index', -1) for a in actions), default=-1) + 1
        m = _re.match(r'(\d{4})(\d{2})(\d{2})', minutes_path.stem)
        created_at = (f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m
                      else datetime.now(UTC).date().isoformat())
        action = {
            'index':            next_index,
            'type':             'human',
            'title':            title,
            'archivo':          None,
            'context':          None,
            'prompt_original':  '',
            'project':          None,
            'project_path':     None,
            'prompt_enriched':  '',
            'enriched_ok':      False,
            'assignee':         assignee or None,
            'deadline':         deadline or None,
            'created_at':       created_at,
            'claude_executable': False,
            'executed':         False,
            'manual':           True,
        }
        actions.append(action)
        ap.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info(f"Acción manual añadida a {minutes_path.name}: {title[:60]}")
        return action
    except Exception as e:
        log.warning(f"add_manual_action: {e}")
        return None


def set_meeting_project_id(minutes_path: Path, project_id: str) -> bool:
    """Set (or override) project_id for a meeting. Creates a stub actions.json if none exists."""
    ap = get_actions_path(minutes_path)
    try:
        if ap.exists():
            data = json.loads(ap.read_text(encoding='utf-8'))
        else:
            data = {
                'minutes':      str(minutes_path),
                'generated_at': datetime.now(UTC).isoformat(),
                'actions':      [],
            }
        data['project_id'] = project_id or 'none'
        # Lo eligio el usuario: ni una deteccion ni un reintento de las minutas lo cambian.
        data['project_source'] = 'manual'
        ap.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info(f"Project set to '{project_id}' for {minutes_path.name}")
        return True
    except Exception as e:
        log.warning(f"set_meeting_project_id: {e}")
        return False


def _sync_project_folder(md: Path) -> None:
    """Archiva el resumen bajo el proyecto que dice el _actions.json.

    Se llama también para las reuniones que YA tenían project_id: la carpeta
    puede no corresponder (la elegía otro detector) o no existir (la reunión es
    anterior a la creación del proyecto). Es idempotente.
    """
    try:
        from project_context import sync_meeting_summary
        sync_meeting_summary(md)
    except Exception as e:
        log.warning(f"sync_meeting_summary {md.stem}: {e}")


def detect_projects_for_all_meetings() -> dict:
    """Run Claude-based project detection for all meetings that don't yet have a project_id.
    Returns {filename: project_id} for every meeting processed."""
    from config import MINUTES_DIR as _MINUTES_DIR
    results = {}
    for md in sorted(_MINUTES_DIR.glob('*.md')):
        ap = get_actions_path(md)
        if ap.exists():
            try:
                data = json.loads(ap.read_text(encoding='utf-8'))
                # Una suposicion por el titulo se revisa con Claude.
                if 'project_id' in data and data.get('project_source') != 'title':
                    results[md.name] = data['project_id']
                    _sync_project_folder(md)
                    continue
            except Exception:
                pass
        # Create stub if needed, then detect
        if not ap.exists():
            stub = {
                'minutes':      str(md),
                'generated_at': datetime.now(UTC).isoformat(),
                'actions':      [],
            }
            ap.write_text(json.dumps(stub, ensure_ascii=False, indent=2), encoding='utf-8')
        _detect_and_save_project(md, None, title_fallback=False)
        _sync_project_folder(md)
        try:
            results[md.name] = json.loads(ap.read_text(encoding='utf-8')).get('project_id', 'none')
        except Exception:
            results[md.name] = 'none'
    return results


def _save_json(minutes_path: Path, actions, enriched_map):
    import re as _re
    from actions_parser import normalize_assignee
    from config import ME_NAME
    stem = minutes_path.stem
    m_date = _re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', stem)
    created_at = f"{m_date.group(1)}-{m_date.group(2)}-{m_date.group(3)}" if m_date else datetime.now(UTC).date().isoformat()

    result = []
    for a in actions:
        enriched = enriched_map.get(a.index, {}) if isinstance(enriched_map, dict) else {}
        claude_executable = a.type in _CLAUDE_TYPES
        result.append({
            'index':          a.index,
            'type':           a.type,
            'title':          a.title,
            'archivo':        a.archivo,
            'context':        a.context,
            'prompt_original': a.prompt,
            'project':        enriched.get('project'),
            'project_path':   None,
            'prompt_enriched': enriched.get('prompt', a.prompt),
            'enriched_ok':    bool(enriched),
            'assignee':       normalize_assignee(
                a.assignee if a.assignee is not None else enriched.get('assignee'), ME_NAME),
            'deadline':       a.deadline,
            'created_at':     created_at,
            'claude_executable': claude_executable,
            'executed':       False,
        })

    ap = get_actions_path(minutes_path)
    # Al rehacer unas minutas (el reintento de unas 'pending') no se pierde lo que
    # puso el usuario: sus acciones manuales, con su indice (las tareas lo
    # referencian), y el proyecto que eligio a mano.
    prev = {}
    if ap.exists():
        try:
            prev = json.loads(ap.read_text(encoding='utf-8'))
        except Exception:
            prev = {}
    kept = [a for a in prev.get('actions', []) if a.get('manual')]
    taken = {a.get('index') for a in kept}
    nxt = max([i for i in taken if isinstance(i, int)] + [a['index'] for a in result], default=-1) + 1
    for a in result:
        if a['index'] in taken:
            a['index'], nxt = nxt, nxt + 1
    data = {
        'minutes':      str(minutes_path),
        'generated_at': datetime.now(UTC).isoformat(),
        'actions':      result + kept,
    }
    if prev.get('project_source') == 'manual':
        data['project_id'] = prev.get('project_id', 'none')
        data['project_source'] = 'manual'
    ap.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info(f"Actions JSON guardado: {ap.name} ({len(result)} acciones)")
