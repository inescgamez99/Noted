import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

_FRAMEWORK = """
### Analyst
1. Active listening & accurate note-taking — captures what is said faithfully, doesn't miss key points
2. Structured clarifying questions — asks the right questions to understand before acting
3. Clear and concise communication — expresses themselves without ambiguity or over-explaining
4. Reliability on assigned contributions — follows up on what they were supposed to bring, flags blockers early

### Senior Analyst
1. Proactive contribution — adds ideas and observations without being prompted
2. Structured thinking — organises and presents their thinking logically before speaking
3. Sub-discussion facilitation — can lead a small group or agenda item confidently
4. Self-management & escalation — manages their own time, flags risks or blockers without being asked

### Consultant
1. Structured discussion leadership — owns and drives the agenda, keeps the meeting on track
2. Multi-stakeholder synthesis — pulls together inputs from different people into a coherent picture
3. Decision and clarity push — doesn't let discussions end without a clear owner, decision, or next step
4. Audience-appropriate register — adapts tone and framing to who is in the room

### Senior Consultant
1. Confident client-facing leadership — leads externally with credibility and composure
2. Constructive challenge — respectfully questions assumptions and pushes back on weak reasoning
3. Business framing and quantification — anchors discussions in numbers, impact, and business outcomes
4. Consensus building — navigates disagreement and builds alignment across competing views

### Manager
1. Strategic discussion framing — positions meetings in the context of broader goals and risks
2. Real-time adaptation — reads the room and changes approach mid-meeting when needed
3. Team development — actively develops junior participants: creates space, coaches in the moment
4. Upward management — manages expectations of senior stakeholders, flags risks proactively

### Senior Manager
1. Executive presence — commands the room, sets tone, earns authority without asserting it
2. Business impact and risk framing — every contribution is anchored in value, risk, or strategic consequence
3. Senior stakeholder relationship development — builds trust and influence with senior people in the meeting
4. Decision-making under ambiguity — drives to decisions even when information is incomplete

### Managing Director
1. Organisational influence — shapes culture, strategy, and direction through this meeting, not just outcomes
2. Business development — identifies or advances commercial opportunities; connects discussions to revenue or growth
3. Talent and culture — visibly invests in people, role-models firm values, makes the team stronger by being present
4. Commercial judgment under pressure — makes high-stakes calls with incomplete information and owns the consequences
"""

_PROMPT_AUTO = """You are a management consulting career coach. Read the meeting transcript and identify the consulting level at which THE PERSON WHO RECORDED THIS MEETING is performing. They may be facilitating, participating, presenting, advising, or any other role — assess whatever role they are actually playing in this call.

This is a quick single-meeting snapshot, not a comprehensive evaluation. Base your assessment only on what is directly observable in the transcript.

IMPORTANT: If the person who recorded this meeting has too few observable contributions to grade fairly (e.g. they barely spoke, they were silent, or there is insufficient evidence to assess any competency), return {{"insufficient": true}} and nothing else.

## Consulting Level Competency Framework

Use this framework to determine the level and score. Each level has exactly 4 competencies. These are generic and apply to any type of meeting and any role.
{framework}
---

## Your task

1. Read the transcript and identify which level the person is performing at.
2. Score them on the 4 competencies for THAT level (1–10 each).
3. Identify 2–3 specific strengths grounded in the transcript.
4. Give 3–5 concrete improvements they need to make to reach the next level — specific to what you observed, not generic advice.

Return ONLY valid JSON, no markdown, no extra text. Either:
{{"insufficient": true}}
or:
{{
  "overall": 7.5,
  "level": "Consultant",
  "next_level": "Senior Consultant",
  "dimensions": [
    {{"name": "Structured discussion leadership", "score": 8}},
    {{"name": "Multi-stakeholder synthesis", "score": 9}},
    {{"name": "Decision and clarity push", "score": 6}},
    {{"name": "Audience-appropriate register", "score": 6}}
  ],
  "strengths": [
    "Pulled together a coherent picture across five different speakers mid-session",
    "Consistently redirected tangents back to the agenda"
  ],
  "improvements": [
    "End every topic with an explicit decision or named next step before moving on — several topics closed without one",
    "Replace casual phrases like 'I don't care' with 'I want honest reactions here' — same intent, more credible register"
  ]
}}

TRANSCRIPT:
{{transcript}}
"""

_PROMPT_FIXED = """You are a management consulting career coach. The person who recorded this meeting has told you their consulting level is: {fixed_level}. Grade them ONLY against the 4 competencies for that level. They may be facilitating, participating, presenting, advising, or any other role — assess whatever role they are actually playing in this call.

This is a quick single-meeting snapshot, not a comprehensive evaluation. Base your assessment only on what is directly observable in the transcript.

IMPORTANT: If the person has too few observable contributions to grade fairly, return {{"insufficient": true}} and nothing else.

## {fixed_level} Competencies
{competencies}

---

## Your task

1. Score them on each of the 4 {fixed_level} competencies (1–10 each).
2. Identify 2–3 specific strengths grounded in the transcript.
3. Give 3–5 concrete improvements to reach the next level — specific to what you observed, not generic advice.

Return ONLY valid JSON, no markdown, no extra text. Either:
{{"insufficient": true}}
or:
{{
  "overall": 7.5,
  "level": "{fixed_level}",
  "next_level": "{next_level}",
  "dimensions": [
    {{"name": "competency name", "score": 8}}
  ],
  "strengths": ["..."],
  "improvements": ["..."]
}}

TRANSCRIPT:
{{transcript}}
"""


_NEXT_LEVEL = {
    'Analyst':          'Senior Analyst',
    'Senior Analyst':   'Consultant',
    'Consultant':       'Senior Consultant',
    'Senior Consultant':'Manager',
    'Manager':          'Senior Manager',
    'Senior Manager':   'Managing Director',
    'Managing Director':'Partner',
}

_LEVEL_COMPETENCIES = {
    'Analyst': (
        "1. Active listening & accurate note-taking\n"
        "2. Structured clarifying questions\n"
        "3. Clear and concise communication\n"
        "4. Reliability on assigned contributions"
    ),
    'Senior Analyst': (
        "1. Proactive contribution\n"
        "2. Structured thinking\n"
        "3. Sub-discussion facilitation\n"
        "4. Self-management & escalation"
    ),
    'Consultant': (
        "1. Structured discussion leadership\n"
        "2. Multi-stakeholder synthesis\n"
        "3. Decision and clarity push\n"
        "4. Audience-appropriate register"
    ),
    'Senior Consultant': (
        "1. Confident client-facing leadership\n"
        "2. Constructive challenge\n"
        "3. Business framing and quantification\n"
        "4. Consensus building"
    ),
    'Manager': (
        "1. Strategic discussion framing\n"
        "2. Real-time adaptation\n"
        "3. Team development\n"
        "4. Upward management"
    ),
    'Senior Manager': (
        "1. Executive presence\n"
        "2. Business impact and risk framing\n"
        "3. Senior stakeholder relationship development\n"
        "4. Decision-making under ambiguity"
    ),
    'Managing Director': (
        "1. Organisational influence\n"
        "2. Business development\n"
        "3. Talent and culture\n"
        "4. Commercial judgment under pressure"
    ),
}


def _coaching_level() -> str | None:
    """Return the fixed level from settings, or None if auto-detect."""
    try:
        from config import PROJECT_DIR
        import json as _j
        cfg = _j.loads((PROJECT_DIR / 'settings.json').read_text(encoding='utf-8'))
        level = cfg.get('coaching_level', 'auto')
        if level and level != 'auto':
            return level
    except Exception:
        pass
    return None


def _build_prompt(transcript: str, fixed_level: str | None) -> str:
    snippet = transcript[:14000]
    if fixed_level and fixed_level in _LEVEL_COMPETENCIES:
        competencies = _LEVEL_COMPETENCIES[fixed_level]
        next_level = _NEXT_LEVEL.get(fixed_level, 'Partner')
        base = _PROMPT_FIXED.format(
            fixed_level=fixed_level,
            competencies=competencies,
            next_level=next_level,
        )
    else:
        base = _PROMPT_AUTO.format(framework=_FRAMEWORK)
    return base.replace('{transcript}', snippet)


def _call_claude(transcript: str, fixed_level: str | None = None) -> dict | None:
    try:
        from config import CLAUDE_BIN, clean_env
    except ImportError:
        log.warning("coaching_plugin: config not importable")
        return None
    if not CLAUDE_BIN:
        return None

    prompt = _build_prompt(transcript, fixed_level)

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


_LEVEL_SHORT = {
    'Senior Analyst':       'S. Analyst',
    'Senior Consultant':    'S. Consultant',
    'Senior Manager':       'S. Manager',
}


def _shorten_level(level: str) -> str:
    return _LEVEL_SHORT.get(level, level)


def _format_sticky_text(r: dict) -> str:
    """Plain-text version used for the header preview only."""
    level = _shorten_level(r.get('level', '?'))
    return f"📋 {r.get('overall', '?')}/10  {level}"


def _li(text: str) -> str:
    import html as _h
    return f'<li style="margin-bottom:4px;">{_h.escape(text)}</li>'


def _format_sticky_html(r: dict) -> str:
    import html as _h

    overall   = r.get('overall', '?')
    level     = _h.escape(r.get('level', '?'))
    next_lvl  = _h.escape(r.get('next_level', '?'))

    # Header
    out = (
        f'<b style="font-size:13px;color:#78350f;">📋 COACHING &nbsp; {overall}/10</b><br>'
        f'<span style="color:#92400e;font-size:11px;">Level: {level} &nbsp;→&nbsp; Next: {next_lvl}</span>'
    )

    # Scores table
    dims = r.get('dimensions', [])
    if dims:
        rows = ''.join(
            f'<tr>'
            f'<td style="padding:3px 10px 3px 0;color:#374151;">{_h.escape(d["name"])}</td>'
            f'<td style="text-align:right;font-weight:700;color:#92400e;white-space:nowrap;">{d["score"]} / 10</td>'
            f'</tr>'
            for d in dims
        )
        out += (
            '<div style="margin:8px 0 3px;font-weight:700;font-size:10px;text-transform:uppercase;'
            'letter-spacing:0.6px;color:#78350f;">Scores</div>'
            '<table style="width:100%;border-collapse:collapse;border-top:1px solid #fde68a;'
            'border-bottom:1px solid #fde68a;margin-bottom:8px;">'
            + rows +
            '</table>'
        )

    # Improvements
    improvements = r.get('improvements', [])
    if improvements:
        items = ''.join(_li(i) for i in improvements)
        out += (
            f'<div style="margin:8px 0 3px;font-weight:700;font-size:10px;text-transform:uppercase;'
            f'letter-spacing:0.6px;color:#78350f;">↑ To reach {next_lvl}</div>'
            f'<ul style="margin:0 0 8px;padding-left:16px;color:#374151;font-size:12px;">{items}</ul>'
        )

    # Strengths
    strengths = r.get('strengths', [])
    if strengths:
        items = ''.join(_li(s) for s in strengths)
        out += (
            '<div style="margin:8px 0 3px;font-weight:700;font-size:10px;text-transform:uppercase;'
            'letter-spacing:0.6px;color:#78350f;">✓ What worked</div>'
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

    fixed_level = _coaching_level()
    result = _call_claude(transcript_text, fixed_level)
    if not result:
        return

    if result.get('insufficient'):
        sticky = {
            'id': f"coaching_{int(time.time() * 1000)}",
            'label': 'Career Level Snapshot',
            'text': '📋 Insufficient data',
            'html': (
                '<b style="font-size:13px;color:#78350f;">📋 COACHING</b><br>'
                '<span style="color:#92400e;font-size:12px;">'
                'Insufficient data to grade this call — not enough observable contributions.'
                '</span>'
            ),
            'x': 20,
            'y': 20,
            'anchor': 'right',
            'minimized': False,
        }
    else:
        sticky = {
            'id': f"coaching_{int(time.time() * 1000)}",
            'label': 'Career Level Snapshot',
            'text': _format_sticky_text(result),
            'html': _format_sticky_html(result),
            'x': 20,
            'y': 20,
            'anchor': 'right',
            'minimized': False,
        }

    existing.append(sticky)
    stickies_path.write_text(json.dumps(existing, ensure_ascii=False), encoding='utf-8')
    log.info(f"coaching_plugin: sticky written to {stickies_path.name}")
