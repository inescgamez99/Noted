"""
Identifica la serie de reuniones a la que pertenece una grabación.
Señales en cascada (orden de prioridad):
  1. Título exacto/fuzzy vs series.json
  2. Outlook calendar (IsRecurring + GlobalAppointmentID)
  3. Inferencia de Claude a partir del transcript
  4. Auto-registro de nueva serie (previo OK del usuario)
"""
import json
import logging
import re
from datetime import datetime, date
from pathlib import Path

log = logging.getLogger(__name__)


def _series_path() -> Path:
    from config import PROJECT_DIR
    return PROJECT_DIR / 'series.json'


def load_series() -> list:
    p = _series_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding='utf-8')).get('series', [])
        except Exception:
            pass
    return []


def save_series(series_list: list) -> None:
    p = _series_path()
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            pass
    data['series'] = series_list
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _norm(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', (s or '').lower())).strip()


def _fuzzy_match(name: str, series_list: list) -> dict | None:
    import difflib
    n = _norm(name)
    if not n:
        return None
    best_score = 0.0
    best = None
    for s in series_list:
        candidates = [s.get('name', '')] + list(s.get('aliases', []))
        for c in candidates:
            c_norm = _norm(c)
            if not c_norm:
                continue
            if c_norm == n or c_norm in n or n in c_norm:
                return s
            ratio = difflib.SequenceMatcher(None, n, c_norm).ratio()
            if ratio > best_score:
                best_score = ratio
                best = s
    if best_score >= 0.65:
        return best
    return None


def _make_series_id(name: str, existing: list) -> str:
    base = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    existing_ids = {s['id'] for s in existing}
    if base not in existing_ids:
        return base
    i = 2
    while f"{base}_{i}" in existing_ids:
        i += 1
    return f"{base}_{i}"


def identify_series(wav_path: Path, transcript_text: str) -> dict | None:
    """
    Returns the series dict if matched, or None.
    Side effect: may write .brain_pending.json for user confirmation of new series.
    """
    nm = re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(.+)', wav_path.stem)
    title_hint = nm.group(1).replace('_', ' ') if nm else ''

    tm = re.match(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})', wav_path.stem)
    rec_time = None
    if tm:
        try:
            rec_time = datetime(int(tm.group(1)), int(tm.group(2)), int(tm.group(3)),
                                int(tm.group(4)), int(tm.group(5)))
        except Exception:
            pass

    series_list = load_series()

    # Signal 1: fuzzy match on title_hint vs series.json
    if title_hint and title_hint.strip().lower() not in ('manual', 'recording'):
        match = _fuzzy_match(title_hint, series_list)
        if match:
            log.info(f"Brain: serie por título: {match['name']}")
            return match

    # Signal 2: Outlook calendar (IsRecurring)
    outlook_info = None
    if rec_time:
        try:
            from outlook_sender import find_meeting_series
            outlook_info = find_meeting_series(rec_time, title_hint)
        except Exception as e:
            log.debug(f"Brain: Outlook lookup: {e}")

    if outlook_info and outlook_info.get('is_recurring'):
        subj = outlook_info.get('subject', '')
        outlook_id = outlook_info.get('series_id', '')

        # Match by GlobalAppointmentID
        if outlook_id:
            for s in series_list:
                if s.get('outlook_series_id') == outlook_id:
                    log.info(f"Brain: serie por Outlook ID: {s['name']}")
                    return s

        # Fuzzy match on calendar subject
        if subj:
            match = _fuzzy_match(subj, series_list)
            if match:
                log.info(f"Brain: serie por subject Outlook: {match['name']}")
                return match

        # New recurring series — queue confirmation
        _write_pending_confirmation(subj or title_hint, outlook_info, series_list)
        return None

    # Signal 3: Claude inference (only if title is generic)
    if not title_hint or title_hint.strip().lower() in ('manual', 'recording', ''):
        if transcript_text and series_list:
            match = _infer_from_transcript(transcript_text, series_list)
            if match:
                log.info(f"Brain: serie inferida del transcript: {match['name']}")
                return match

    return None


def _write_pending_confirmation(proposed_name: str, outlook_info: dict,
                                series_list: list) -> None:
    from config import PROJECT_DIR
    pending_path = PROJECT_DIR / '.brain_pending.json'

    # Don't overwrite an unhandled confirmation for the same name
    if pending_path.exists():
        try:
            existing = json.loads(pending_path.read_text(encoding='utf-8'))
            if _norm(existing.get('proposed_name', '')) == _norm(proposed_name):
                return
        except Exception:
            pass

    data = {
        'proposed_name': proposed_name,
        'outlook_info': outlook_info or {},
        'created_at': datetime.now().isoformat(),
    }
    try:
        pending_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info(f"Brain: nueva serie candidata '{proposed_name}' — esperando confirmación")
    except Exception as e:
        log.warning(f"Brain: pending write error: {e}")


def _infer_from_transcript(transcript: str, series_list: list) -> dict | None:
    """Ask Claude to identify the series from the transcript. Best effort, non-blocking."""
    try:
        from config import CLAUDE_BIN, clean_env
        import subprocess
        if not CLAUDE_BIN:
            return None
        names = [s['name'] for s in series_list]
        prompt = (
            f"Given this meeting transcript excerpt, which of the following known meeting series "
            f"does it belong to?\nKnown series: {', '.join(names)}\n\n"
            f"Transcript (first 1500 chars):\n{transcript[:1500]}\n\n"
            f"Reply ONLY with the exact series name from the list, or 'unknown' if none match."
        )
        proc = subprocess.run(
            [CLAUDE_BIN, '-p'],
            input=prompt, capture_output=True, text=True, encoding='utf-8',
            timeout=60, env=clean_env(),
        )
        answer = proc.stdout.strip().lower()
        if answer and answer != 'unknown':
            for s in series_list:
                if _norm(s['name']) in answer or answer in _norm(s['name']):
                    return s
    except Exception as e:
        log.debug(f"Brain: transcript inference: {e}")
    return None


def confirm_new_series(proposed_name: str, outlook_info: dict = None) -> dict:
    """
    Called when user confirms a pending series. Creates and saves the new entry.
    Returns the new series dict.
    """
    from config import PROJECT_DIR
    series_list = load_series()
    sid = _make_series_id(proposed_name, series_list)
    new_series = {
        'id': sid,
        'name': proposed_name,
        'aliases': [_norm(proposed_name)],
        'outlook_series_id': (outlook_info or {}).get('series_id') or None,
        'project_id': None,
        'recurrence': (outlook_info or {}).get('recurrence_desc', '') or None,
        'auto_detected': True,
        'created_at': date.today().isoformat(),
    }
    series_list.append(new_series)
    save_series(series_list)

    pending_path = PROJECT_DIR / '.brain_pending.json'
    pending_path.unlink(missing_ok=True)
    log.info(f"Brain: nueva serie '{proposed_name}' id={sid}")
    return new_series
