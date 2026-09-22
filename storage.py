import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from config import RECORDINGS_DIR, MINUTES_DIR, INBOX_DIR, MINUTES_RETENTION_DAYS


def ensure_directories():
    for d in (RECORDINGS_DIR, MINUTES_DIR, INBOX_DIR, RECORDINGS_DIR / 'processed'):
        d.mkdir(parents=True, exist_ok=True)


def _slugify(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    text = re.sub(r'[\s\-]+', '_', text)
    return text.strip('_')[:60]


def _recording_timestamp(path: Path) -> str:
    """Extract YYYYMMDD_HHMM from stem YYYY-MM-DD_HH-MM_..."""
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})', path.stem)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}_{m.group(4)}{m.group(5)}"
    return ''


def get_recording_path(name: str = 'recording') -> Path:
    ts = datetime.now().strftime('%Y-%m-%d_%H-%M')
    slug = _slugify(name)
    return RECORDINGS_DIR / f"{ts}_{slug}.wav"


def get_transcript_path(recording_path: Path) -> Path:
    return recording_path.parent / f"{recording_path.stem}_transcript.txt"


def get_minutes_path(recording_path: Path, title: str = None) -> Path:
    ts = _recording_timestamp(recording_path)
    if not ts:
        ts = datetime.now().strftime('%Y%m%d_%H%M')
    slug = _slugify(title) if title else 'Reunion'
    return MINUTES_DIR / f"{ts}_{slug}.md"


def get_latest_minutes() -> Path | None:
    files = sorted(MINUTES_DIR.glob('*.md'), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def list_recordings(limit: int = 10) -> list[Path]:
    files = sorted(
        list(RECORDINGS_DIR.glob('*.wav')) + list((RECORDINGS_DIR / 'processed').glob('*.wav')),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[:limit]


def cleanup_old_recordings(days: int = 15):
    cutoff = datetime.now() - timedelta(days=days)
    audio_patterns = ('*.wav', '*.mp3')
    aux_patterns   = ('*.lang', '*.partial', '*.context')
    for d in (RECORDINGS_DIR, RECORDINGS_DIR / 'processed'):
        for pattern in audio_patterns + aux_patterns:
            for f in d.glob(pattern):
                if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    try:
                        f.unlink()
                    except OSError:
                        pass


def cleanup_old_minutes():
    cutoff = datetime.now() - timedelta(days=MINUTES_RETENTION_DAYS)
    for pattern in ('*.md', '*.html', '*_actions.json', '*_transcript.txt'):
        for f in MINUTES_DIR.glob(pattern):
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                try:
                    f.unlink()
                except OSError:
                    pass

