"""
Task board persistence. tasks.json lives in PROJECT_DIR alongside projects.json.

Schema:
{
  "tasks": [
    {
      "id": str,                    # uuid4
      "project_id": str,            # matches projects.json id, or "none"
      "title": str,
      "description": str,           # detalle largo, se edita solo en el drawer
      "status": "not_started" | "in_progress" | "blocked" | "paused" | "pending_feedback" | "done",
      "assignee": str | null,
      "deadline": str | null,
      "priority": "high" | "medium" | "low" | null,
      "parent_id": str | null,      # null = top-level task
      "source": "meeting" | "manual",
      "meeting_path": str | null,
      "meeting_action_index": int | null,
      "created_at": str             # ISO datetime
    }
  ],
  "migrated": bool
}
"""
import functools
import json
import logging
import uuid
from datetime import datetime, timezone

import atomic_io
from config import PROJECT_DIR

log = logging.getLogger(__name__)

TASKS_FILE = PROJECT_DIR / 'tasks.json'


def _load() -> dict:
    if TASKS_FILE.exists():
        try:
            data = json.loads(TASKS_FILE.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return data
            raise ValueError('tasks.json no es un objeto')
        except OSError as e:
            log.warning(f"tasks.json no se pudo leer: {e}")
        except Exception:
            # Existe pero no se entiende: guardar copia ANTES de que el
            # siguiente _save lo pise con el tablero vacio.
            atomic_io.quarantine_corrupt(TASKS_FILE, log)
    return {'tasks': [], 'migrated': False}


def _save(data: dict) -> None:
    atomic_io.write_text_atomic(
        TASKS_FILE, json.dumps(data, ensure_ascii=False, indent=2))


def _locked(fn):
    """Serializa el ciclo leer-modificar-escribir entre hilos y entre procesos
    (la ventana y el daemon escriben el mismo tasks.json)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with atomic_io.file_lock(TASKS_FILE.with_name(TASKS_FILE.name + '.lock')):
            return fn(*args, **kwargs)
    return wrapper


def _normalize_tags(tags) -> list:
    """Ensure every tag is either a string or {name, color} dict. Remove invalids."""
    if not isinstance(tags, list):
        return []
    result = []
    for tg in tags:
        if isinstance(tg, str) and tg:
            result.append(tg)
        elif isinstance(tg, dict) and tg.get('name'):
            result.append(tg)
    return result


def get_tasks() -> list:
    data = _load()
    # Normalize tags in-memory (no disk write) so JS never gets garbage
    for task in data.get('tasks', []):
        if 'tags' in task:
            task['tags'] = _normalize_tags(task['tags'])
    return data.get('tasks', [])


@_locked
def create_task(
    project_id: str,
    title: str,
    parent_id: str | None = None,
    status: str = 'not_started',
    assignee: str | None = None,
    deadline: str | None = None,   # kept for backward compat — maps to end_date
    priority: str | None = None,
    source: str = 'manual',
    meeting_path: str | None = None,
    meeting_action_index: int | None = None,
    claude_executable: bool = False,
    description: str = '',
    bucket_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    tags: list | None = None,
    view: str | None = None,       # 'list' | 'board' | None (ambas vistas)
) -> dict:
    data = _load()
    task = {
        'id': str(uuid.uuid4()),
        'project_id': project_id or 'none',
        'title': title,
        'description': description,
        'status': status,
        'assignee': assignee,
        'start_date': start_date,
        'end_date': end_date or deadline,  # deadline alias
        'tags': tags or [],
        'priority': priority,
        'parent_id': parent_id,
        'bucket_id': bucket_id or 'pendiente',
        'source': source,
        'meeting_path': meeting_path,
        'meeting_action_index': meeting_action_index,
        'claude_executable': claude_executable,
        'view': view,
        'created_at': datetime.now(timezone.utc).isoformat(),
    }
    data['tasks'].append(task)
    _save(data)
    return task


@_locked
def update_task(task_id: str, fields: dict) -> bool:
    allowed = {'title', 'description', 'status', 'assignee', 'deadline', 'priority', 'parent_id', 'project_id', 'bucket_id', 'start_date', 'end_date', 'tags', 'view'}
    data = _load()
    for task in data['tasks']:
        if task['id'] == task_id:
            for k, v in fields.items():
                if k in allowed:
                    task[k] = v
            task['last_edited'] = datetime.now(timezone.utc).isoformat()
            _save(data)
            return True
    return False


@_locked
def delete_task(task_id: str) -> bool:
    """Deletes the task and all its direct sub-items."""
    data = _load()
    before = len(data['tasks'])
    data['tasks'] = [t for t in data['tasks']
                     if t['id'] != task_id and t.get('parent_id') != task_id]
    if len(data['tasks']) < before:
        _save(data)
        return True
    return False


def get_custom_values(field: str) -> list:
    return _load().get(field, [])


@_locked
def save_custom_values(field: str, values: list) -> bool:
    data = _load()
    data[field] = values
    _save(data)
    return True


@_locked
def migrate_panel_actions() -> int:
    """One-time import of all in_panel=True actions from meeting JSONs into tasks.json.
    Safe to call on every startup — no-op after first run."""
    data = _load()
    if data.get('migrated'):
        return 0

    from config import MINUTES_DIR

    seen = {
        (t.get('meeting_path'), t.get('meeting_action_index'))
        for t in data['tasks']
    }
    count = 0

    for actions_json in sorted(MINUTES_DIR.glob('*_actions.json')):
        try:
            adata = json.loads(actions_json.read_text(encoding='utf-8'))
            meeting_path = adata.get('minutes', str(actions_json))
            project_id = adata.get('project_id') or 'none'
            for a in adata.get('actions', []):
                if not a.get('in_panel'):
                    continue
                key = (meeting_path, a['index'])
                if key in seen:
                    continue
                task = {
                    'id': str(uuid.uuid4()),
                    'project_id': project_id,
                    'title': a.get('title', ''),
                    'description': '',
                    'status': 'done' if a.get('executed') else 'not_started',
                    'assignee': a.get('assignee'),
                    'start_date': None,
                    'end_date': a.get('deadline'),
                    'tags': [],
                    'priority': None,
                    'parent_id': None,
                    'bucket_id': 'pendiente',
                    'source': 'meeting',
                    'meeting_path': meeting_path,
                    'meeting_action_index': a['index'],
                    'claude_executable': bool(a.get('claude_executable')),
                    'created_at': datetime.now(timezone.utc).isoformat(),
                }
                data['tasks'].append(task)
                seen.add(key)
                count += 1
        except Exception as e:
            log.warning(f"migrate_panel_actions {actions_json.name}: {e}")

    data['migrated'] = True
    _save(data)
    log.info(f"Task migration: {count} actions imported to tasks.json")
    return count


