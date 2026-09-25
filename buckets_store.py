import json

from config import PROJECT_DIR

BUCKETS_FILE = PROJECT_DIR / 'buckets.json'

DEFAULT_BUCKETS = [
    {'id': 'todo',        'name': 'To Do',       'color': '#6b7280', 'order': 0},
    {'id': 'in-progress', 'name': 'In Progress', 'color': '#3b82f6', 'order': 1},
    {'id': 'testing',     'name': 'Testing',     'color': '#f59e0b', 'order': 2},
    {'id': 'done',        'name': 'Done',        'color': '#10b981', 'order': 3},
]


def get_buckets() -> list:
    if BUCKETS_FILE.exists():
        try:
            data = json.loads(BUCKETS_FILE.read_text(encoding='utf-8'))
            buckets = data.get('buckets', [])
            if buckets:
                return sorted(buckets, key=lambda b: b.get('order', 0))
        except Exception:
            pass
    _save({'buckets': list(DEFAULT_BUCKETS)})
    return list(DEFAULT_BUCKETS)


def save_buckets(buckets: list) -> bool:
    try:
        _save({'buckets': buckets})
        return True
    except Exception:
        return False


def get_first_bucket_id() -> str:
    buckets = get_buckets()
    return buckets[0]['id'] if buckets else 'pendiente'


def _save(data: dict) -> None:
    BUCKETS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
