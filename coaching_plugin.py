import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

_PROMPT = """You are a meeting communication coach. Your role is to help the person who recorded this meeting communicate more effectively in future calls.

Read the transcript and identify the contributions made by THE PERSON WHO RECORDED THIS MEETING ONLY. Do not evaluate anyone else. The recorder is typically the one who set up the call, speaks most often, or is referred to in the first person in any context notes.

IMPORTANT:
- If the recorder has too few observable contributions (e.g. they barely spoke, were silent, or you cannot identify their voice), return {{"insufficient": true}} and nothing else.
- Do NOT evaluate other participants, even if they spoke more.
- Base your assessment only on what is directly observable in the transcript.

## Your task

1. Identify 2–3 specific communication strengths the recorder demonstrated in this meeting, grounded in concrete moments from the transcript.
2. Give 3–5 concrete, actionable improvements — specific to what you observed, not generic advice. Frame them as things to try next time.

Return ONLY valid JSON, no markdown, no extra text. Either:
{{"insufficient": true}}
or:
{{
  "strengths": [
    "Opened with a clear agenda that kept the discussion focused",
    "Asked a clarifying question that unblocked the decision on scope"
  ],
  "improvements": [
    "Several topics ended without a named owner or deadline — close each item with 'Who owns this and by when?'",
    "Long monologues in the second half lost the room — break up explanations with a check-in question every 2–3 minutes"
  ]
}}

TRANSCRIPT:
{transcript}
"""


def _build_prompt(transcript: str) -> str:
    snippet = transcript[:14000]
    return _PROMPT.format(transcript=snippet)


def _call_claude(transcript: str) -> dict | None:
    try:
        from config import CLAUDE_BIN, clean_env
    except ImportError:
        log.warning("coaching_plugin: config not importable")
        return None
    if not CLAUDE_BIN:
        return None

    prompt = _build_prompt(transcript)

    try:
        si = None
        flags = 0
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            flags = 0x08000000

        proc = subprocess.Popen(
            [CLAUDE_BIN, '-p'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            env=clean_env(),
            startupinfo=si,
            creationflags=flags,
        )
        stdout, stderr = proc.communicate(input=prompt, timeout=120)
        if proc.returncode != 0:
            log.warning(f"coaching claude rc={proc.returncode}: {stderr[:300]}")
            return None

        raw = stdout.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw.strip())
        return json.loads(raw)
    except Exception as e:
        log.warning(f"coaching _call_claude: {e}")
        return None


def _ui_lang() -> str:
    try:
        from config import PROJECT_DIR
        import json as _j
        cfg = _j.loads((PROJECT_DIR / 'settings.json').read_text(encoding='utf-8'))
        return cfg.get('language', 'en')
    except Exception:
        return 'en'


def _li(text: str) -> str:
    import html as _h
    return f'<li style="margin-bottom:4px;">{_h.escape(text)}</li>'


def _format_sticky_text(r: dict) -> str:
    """Plain-text version used for the header preview only."""
    n = len(r.get('improvements', []))
    if _ui_lang() == 'es':
        return f"🎯 Meeting Coach — {n} consejo{'s' if n != 1 else ''}"
    if _ui_lang() == 'ca':
        return f"🎯 Meeting Coach — {n} consell{'s' if n != 1 else ''}"
    return f"🎯 Meeting Coach — {n} tip{'s' if n != 1 else ''}"


def _format_sticky_html(r: dict) -> str:
    lang = _ui_lang()
    if lang == 'es':
        label_worked = '✓ Lo que funcionó'
        label_improve = '↑ Para mejorar'
    elif lang == 'ca':
        label_worked = '✓ El que va funcionar'
        label_improve = '↑ Per millorar'
    else:
        label_worked = '✓ What worked'
        label_improve = '↑ Try next time'

    out = '<b style="font-size:13px;color:#78350f;">🎯 MEETING COACH</b>'

    strengths = r.get('strengths', [])
    if strengths:
        items = ''.join(_li(s) for s in strengths)
        out += (
            f'<div style="margin:8px 0 3px;font-weight:700;font-size:10px;text-transform:uppercase;'
            f'letter-spacing:0.6px;color:#78350f;">{label_worked}</div>'
            f'<ul style="margin:0 0 8px;padding-left:16px;color:#374151;font-size:12px;">{items}</ul>'
        )

    improvements = r.get('improvements', [])
    if improvements:
        items = ''.join(_li(i) for i in improvements)
        out += (
            f'<div style="margin:8px 0 3px;font-weight:700;font-size:10px;text-transform:uppercase;'
            f'letter-spacing:0.6px;color:#78350f;">{label_improve}</div>'
            f'<ul style="margin:0;padding-left:16px;color:#374151;font-size:12px;">{items}</ul>'
        )

    return out


def _coaching_enabled() -> bool:
    try:
        from config import PROJECT_DIR
        import json as _j
        cfg = _j.loads((PROJECT_DIR / 'settings.json').read_text(encoding='utf-8'))
        return cfg.get('coaching_enabled', False) is True
    except Exception:
        return False


def grade_and_inject(transcript_text: str, minutes_path: Path) -> None:
    if not _coaching_enabled():
        return

    stickies_path = minutes_path.parent / f"{minutes_path.stem}.stickies.json"

    if stickies_path.exists():
        try:
            existing = json.loads(stickies_path.read_text(encoding='utf-8'))
            if any(str(s.get('id', '')).startswith('coaching_') for s in existing):
                log.info("coaching_plugin: note already present, skipping")
                return
        except Exception:
            existing = []
    else:
        existing = []

    result = _call_claude(transcript_text)
    if not result:
        return

    if result.get('insufficient'):
        _lang = _ui_lang()
        if _lang == 'es':
            _no_speech_text = '🎯 Sin voz detectada'
            _no_speech_msg = 'Sin contribuciones suficientes — no se generó feedback para esta llamada.'
        elif _lang == 'ca':
            _no_speech_text = '🎯 Sense veu detectada'
            _no_speech_msg = 'Sense contribucions suficients — no s\'ha generat feedback per a aquesta trucada.'
        else:
            _no_speech_text = '🎯 No speech detected'
            _no_speech_msg = 'Not enough contributions detected — no feedback generated for this call.'
        sticky = {
            'id': f"coaching_{int(time.time() * 1000)}",
            'label': 'Meeting Coach',
            'text': _no_speech_text,
            'html': (
                '<b style="font-size:13px;color:#78350f;">🎯 MEETING COACH</b><br>'
                f'<span style="color:#92400e;font-size:12px;">{_no_speech_msg}</span>'
            ),
            'x': 8,
            'y': 8,
            'anchor': 'right',
            'minimized': True,
        }
    else:
        sticky = {
            'id': f"coaching_{int(time.time() * 1000)}",
            'label': 'Meeting Coach',
            'text': _format_sticky_text(result),
            'html': _format_sticky_html(result),
            'x': 8,
            'y': 8,
            'anchor': 'right',
            'minimized': True,
        }

    existing.append(sticky)
    stickies_path.write_text(json.dumps(existing, ensure_ascii=False), encoding='utf-8')
    log.info(f"coaching_plugin: sticky written to {stickies_path.name}")
