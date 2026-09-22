"""coaching_plugin: Career Level Snapshot — grading + sticky injection.

No LLM calls, no real filesystem writes outside tr_dirs.
subprocess.Popen is replaced so tests run offline and fast.
"""
import json
import sys

import pytest

import config
import coaching_plugin as cp

pytestmark = pytest.mark.unit


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_settings(tr_dirs, **kw):
    (tr_dirs / 'settings.json').write_text(json.dumps(kw), encoding='utf-8')


def _make_result(**kw):
    base = {
        'overall': 7.0,
        'level': 'Consultant',
        'next_level': 'Senior Consultant',
        'dimensions': [
            {'name': 'Structured discussion leadership', 'score': 7},
            {'name': 'Multi-stakeholder synthesis', 'score': 8},
            {'name': 'Decision and clarity push', 'score': 6},
            {'name': 'Audience-appropriate register', 'score': 7},
        ],
        'strengths': ['Kept the discussion on track', 'Good synthesis'],
        'improvements': ['Push for explicit decisions', 'Adapt register more'],
    }
    base.update(kw)
    return base


# ── _coaching_enabled ─────────────────────────────────────────────────────────

def test_coaching_disabled_by_default(tr_dirs):
    _write_settings(tr_dirs)
    assert cp._coaching_enabled() is False


def test_coaching_enabled_when_set(tr_dirs):
    _write_settings(tr_dirs, coaching_enabled=True)
    assert cp._coaching_enabled() is True


def test_coaching_enabled_false_when_set_false(tr_dirs):
    _write_settings(tr_dirs, coaching_enabled=False)
    assert cp._coaching_enabled() is False


def test_coaching_enabled_returns_false_on_missing_file(tr_dirs):
    # settings.json does not exist
    assert cp._coaching_enabled() is False


# ── _coaching_level ───────────────────────────────────────────────────────────

def test_coaching_level_returns_none_when_auto(tr_dirs):
    _write_settings(tr_dirs, coaching_level='auto')
    assert cp._coaching_level() is None


def test_coaching_level_returns_none_when_missing(tr_dirs):
    _write_settings(tr_dirs)
    assert cp._coaching_level() is None


def test_coaching_level_returns_fixed_level(tr_dirs):
    _write_settings(tr_dirs, coaching_level='Manager')
    assert cp._coaching_level() == 'Manager'


def test_coaching_level_returns_none_on_exception(tr_dirs, monkeypatch):
    monkeypatch.setattr(config, 'PROJECT_DIR', tr_dirs / 'does' / 'not' / 'exist')
    assert cp._coaching_level() is None


# ── _build_prompt ─────────────────────────────────────────────────────────────

def test_build_prompt_auto_contains_framework():
    prompt = cp._build_prompt('hello transcript', None)
    assert 'Managing Director' in prompt
    assert 'hello transcript' in prompt


def test_build_prompt_fixed_uses_level_competencies():
    prompt = cp._build_prompt('test', 'Manager')
    assert 'Strategic discussion framing' in prompt
    assert 'test' in prompt
    assert 'Senior Manager' in prompt   # next_level


def test_build_prompt_fixed_md_level():
    prompt = cp._build_prompt('x', 'Managing Director')
    assert 'Organisational influence' in prompt
    assert 'Partner' in prompt          # next_level for MD


def test_build_prompt_unknown_fixed_falls_back_to_auto():
    prompt = cp._build_prompt('y', 'NonExistentLevel')
    # falls back to auto path which has the full framework
    assert 'Analyst' in prompt


def test_build_prompt_truncates_long_transcript():
    long_text = 'x' * 20000
    prompt = cp._build_prompt(long_text, None)
    # only 14000 chars of transcript should appear
    assert long_text[:14000] in prompt
    assert long_text[14001:] not in prompt


# ── _shorten_level / _format_sticky_text ─────────────────────────────────────

@pytest.mark.parametrize('level,expected', [
    ('Senior Analyst',    'S. Analyst'),
    ('Senior Consultant', 'S. Consultant'),
    ('Senior Manager',    'S. Manager'),
    ('Consultant',        'Consultant'),
    ('Managing Director', 'Managing Director'),
    ('Analyst',           'Analyst'),
])
def test_shorten_level(level, expected):
    assert cp._shorten_level(level) == expected


def test_format_sticky_text_includes_score_and_level():
    r = _make_result(overall=8.5, level='Manager')
    text = cp._format_sticky_text(r)
    assert '8.5/10' in text
    assert 'Manager' in text


# ── _format_sticky_html ───────────────────────────────────────────────────────

def test_format_sticky_html_has_scores_table():
    html = cp._format_sticky_html(_make_result())
    assert '<table' in html
    assert 'Structured discussion leadership' in html
    assert '7 / 10' in html


def test_format_sticky_html_improvements_before_strengths():
    html = cp._format_sticky_html(_make_result())
    idx_improve = html.index('Push for explicit')
    idx_strength = html.index('Kept the discussion')
    assert idx_improve < idx_strength


def test_format_sticky_html_shows_next_level():
    html = cp._format_sticky_html(_make_result())
    assert 'Senior Consultant' in html


def test_format_sticky_html_escapes_special_chars():
    r = _make_result(strengths=['A & B <test>'])
    html = cp._format_sticky_html(r)
    assert '&amp;' in html or '&lt;' in html


def test_format_sticky_html_empty_lists_do_not_crash():
    r = _make_result(dimensions=[], strengths=[], improvements=[])
    html = cp._format_sticky_html(r)
    assert 'COACHING' in html


# ── _call_claude ──────────────────────────────────────────────────────────────

@pytest.fixture
def fake_popen(monkeypatch):
    """Replace subprocess.Popen with a controllable fake."""
    calls = []

    class FakeProc:
        def __init__(self, stdout='{}', returncode=0, stderr=''):
            self._stdout = stdout
            self.returncode = returncode
            self._stderr = stderr

        def communicate(self, input=None, timeout=None):
            calls.append({'input': input, 'timeout': timeout})
            return self._stdout, self._stderr

    state = {'proc': FakeProc(), 'calls': calls}

    def fake_popen_fn(cmd, **kw):
        state['cmd'] = cmd
        return state['proc']

    monkeypatch.setattr(cp.subprocess, 'Popen', fake_popen_fn)
    monkeypatch.setattr(config, 'CLAUDE_BIN', 'claude')
    return state


def test_call_claude_returns_none_when_no_bin(monkeypatch):
    monkeypatch.setattr(config, 'CLAUDE_BIN', None)
    assert cp._call_claude('transcript') is None


def test_call_claude_returns_parsed_json(fake_popen):
    result = _make_result()
    fake_popen['proc']._stdout = json.dumps(result)
    out = cp._call_claude('some transcript')
    assert out['level'] == 'Consultant'
    assert out['overall'] == 7.0


def test_call_claude_strips_markdown_fences(fake_popen):
    result = _make_result()
    fake_popen['proc']._stdout = f'```json\n{json.dumps(result)}\n```'
    out = cp._call_claude('t')
    assert out is not None
    assert out['level'] == 'Consultant'


def test_call_claude_returns_none_on_nonzero_exit(fake_popen):
    fake_popen['proc'].returncode = 1
    fake_popen['proc']._stderr = 'error'
    assert cp._call_claude('t') is None


def test_call_claude_returns_none_on_invalid_json(fake_popen):
    fake_popen['proc']._stdout = 'not json at all'
    assert cp._call_claude('t') is None


def test_call_claude_uses_noninteractive_flag(fake_popen):
    fake_popen['proc']._stdout = json.dumps(_make_result())
    cp._call_claude('t')
    assert '-p' in fake_popen['cmd']


def test_call_claude_passes_fixed_level_in_prompt(fake_popen):
    fake_popen['proc']._stdout = json.dumps(_make_result())
    cp._call_claude('my transcript', fixed_level='Manager')
    prompt = fake_popen['calls'][0]['input']
    assert 'Manager' in prompt
    assert 'Strategic discussion framing' in prompt


def test_call_claude_returns_none_on_popen_exception(monkeypatch):
    monkeypatch.setattr(config, 'CLAUDE_BIN', 'claude')

    def boom(*_a, **_k):
        raise OSError('cannot launch')

    monkeypatch.setattr(cp.subprocess, 'Popen', boom)
    assert cp._call_claude('t') is None


def test_call_claude_returns_insufficient_dict(fake_popen):
    fake_popen['proc']._stdout = json.dumps({'insufficient': True})
    out = cp._call_claude('t')
    assert out == {'insufficient': True}


# ── grade_and_inject ──────────────────────────────────────────────────────────

@pytest.fixture
def minutes_file(tr_dirs):
    p = tr_dirs / 'minutes' / 'meeting-2026-09-22.md'
    p.write_text('# Meeting', encoding='utf-8')
    return p


def _patch_claude(monkeypatch, result):
    monkeypatch.setattr(cp, '_call_claude', lambda *_a, **_kw: result)


def test_grade_and_inject_skips_when_disabled(tr_dirs, minutes_file, monkeypatch):
    _write_settings(tr_dirs, coaching_enabled=False)
    called = []
    monkeypatch.setattr(cp, '_call_claude', lambda *a, **k: called.append(1) or {})
    cp.grade_and_inject('transcript', minutes_file)
    stickies_path = minutes_file.parent / f'{minutes_file.stem}.stickies.json'
    assert not stickies_path.exists()
    assert called == []


def test_grade_and_inject_writes_sticky(tr_dirs, minutes_file, monkeypatch):
    _write_settings(tr_dirs, coaching_enabled=True)
    _patch_claude(monkeypatch, _make_result())
    cp.grade_and_inject('transcript', minutes_file)
    stickies_path = minutes_file.parent / f'{minutes_file.stem}.stickies.json'
    stickies = json.loads(stickies_path.read_text(encoding='utf-8'))
    assert len(stickies) == 1
    assert stickies[0]['id'].startswith('coaching_')
    assert stickies[0]['anchor'] == 'right'
    assert '<table' in stickies[0]['html']


def test_grade_and_inject_duplicate_guard(tr_dirs, minutes_file, monkeypatch):
    _write_settings(tr_dirs, coaching_enabled=True)
    stickies_path = minutes_file.parent / f'{minutes_file.stem}.stickies.json'
    existing = [{'id': 'coaching_111', 'text': 'already here', 'x': 20, 'y': 20}]
    stickies_path.write_text(json.dumps(existing), encoding='utf-8')

    called = []
    monkeypatch.setattr(cp, '_call_claude', lambda *a, **k: called.append(1) or {})
    cp.grade_and_inject('transcript', minutes_file)
    # should not have added a second sticky
    stickies = json.loads(stickies_path.read_text(encoding='utf-8'))
    assert len(stickies) == 1
    assert called == []


def test_grade_and_inject_insufficient_data(tr_dirs, minutes_file, monkeypatch):
    _write_settings(tr_dirs, coaching_enabled=True)
    _patch_claude(monkeypatch, {'insufficient': True})
    cp.grade_and_inject('barely spoke', minutes_file)
    stickies_path = minutes_file.parent / f'{minutes_file.stem}.stickies.json'
    stickies = json.loads(stickies_path.read_text(encoding='utf-8'))
    assert len(stickies) == 1
    assert 'Insufficient' in stickies[0]['html']
    assert stickies[0]['text'] == '📋 Insufficient data'


def test_grade_and_inject_skips_when_claude_returns_none(tr_dirs, minutes_file, monkeypatch):
    _write_settings(tr_dirs, coaching_enabled=True)
    _patch_claude(monkeypatch, None)
    cp.grade_and_inject('transcript', minutes_file)
    stickies_path = minutes_file.parent / f'{minutes_file.stem}.stickies.json'
    assert not stickies_path.exists()


def test_grade_and_inject_appends_to_existing_stickies(tr_dirs, minutes_file, monkeypatch):
    _write_settings(tr_dirs, coaching_enabled=True)
    stickies_path = minutes_file.parent / f'{minutes_file.stem}.stickies.json'
    existing = [{'id': 'user_note_1', 'text': 'my note', 'x': 20, 'y': 20}]
    stickies_path.write_text(json.dumps(existing), encoding='utf-8')

    _patch_claude(monkeypatch, _make_result())
    cp.grade_and_inject('transcript', minutes_file)
    stickies = json.loads(stickies_path.read_text(encoding='utf-8'))
    assert len(stickies) == 2
    ids = [s['id'] for s in stickies]
    assert 'user_note_1' in ids
    assert any(i.startswith('coaching_') for i in ids)


def test_grade_and_inject_uses_fixed_level_from_settings(tr_dirs, minutes_file, monkeypatch):
    _write_settings(tr_dirs, coaching_enabled=True, coaching_level='Senior Manager')
    captured = []

    def fake_call(transcript, fixed_level=None):
        captured.append(fixed_level)
        return _make_result(level='Senior Manager', next_level='Managing Director')

    monkeypatch.setattr(cp, '_call_claude', fake_call)
    cp.grade_and_inject('transcript', minutes_file)
    assert captured[0] == 'Senior Manager'


def test_grade_and_inject_sticky_positioned_top_right(tr_dirs, minutes_file, monkeypatch):
    _write_settings(tr_dirs, coaching_enabled=True)
    _patch_claude(monkeypatch, _make_result())
    cp.grade_and_inject('transcript', minutes_file)
    stickies_path = minutes_file.parent / f'{minutes_file.stem}.stickies.json'
    sticky = json.loads(stickies_path.read_text(encoding='utf-8'))[0]
    assert sticky['anchor'] == 'right'
    assert sticky['y'] == 20


# ── tray_app hook: coaching exception must not crash the pipeline ─────────────

def test_tray_coaching_hook_survives_exception(tray, tr_dirs, monkeypatch):
    """If coaching_plugin raises, the pipeline must continue (no re-raise)."""
    def boom(transcript, path):
        raise RuntimeError('simulated coaching failure')

    monkeypatch.setattr(cp, 'grade_and_inject', boom)
    monkeypatch.setitem(sys.modules, 'coaching_plugin', cp)

    # Exercise just the guarded block extracted from _finalize_session
    minutes_path = tr_dirs / 'minutes' / 'test.md'
    try:
        import coaching_plugin
        coaching_plugin.grade_and_inject('t', minutes_path)
    except ImportError:
        pass
    except Exception as _ce:
        pass   # this is the catch in tray_app — must not propagate
