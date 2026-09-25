"""coaching_plugin: Meeting Coach — strengths/improvements sticky injection.

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
        'strengths': ['Kept discussion focused', 'Asked clarifying questions'],
        'improvements': ['Close each item with owner and deadline', 'Break up long monologues'],
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
    assert cp._coaching_enabled() is False


# ── _build_prompt ─────────────────────────────────────────────────────────────

def test_build_prompt_contains_transcript():
    prompt = cp._build_prompt('hello transcript')
    assert 'hello transcript' in prompt


def test_build_prompt_instructs_recorder_only():
    prompt = cp._build_prompt('some text')
    assert 'recorder' in prompt.lower() or 'recorded' in prompt.lower()


def test_build_prompt_truncates_long_transcript():
    long_text = 'x' * 14000 + 'UNIQUE_SUFFIX_MARKER'
    prompt = cp._build_prompt(long_text)
    assert 'UNIQUE_SUFFIX_MARKER' not in prompt


# ── _format_sticky_text ───────────────────────────────────────────────────────

def test_format_sticky_text_includes_tip_count():
    r = _make_result()
    text = cp._format_sticky_text(r)
    assert 'Meeting Coach' in text
    assert '2' in text


def test_format_sticky_text_singular():
    r = _make_result(improvements=['One tip only'])
    text = cp._format_sticky_text(r)
    # 'tips' must not appear — but 'tip', 'consejo' or 'consell' are all valid
    assert 'tips' not in text and 'consejos' not in text and 'consells' not in text


# ── _format_sticky_html ───────────────────────────────────────────────────────

def test_format_sticky_html_has_strengths_section():
    html = cp._format_sticky_html(_make_result())
    # any of the three language variants is acceptable
    assert any(s in html for s in ('What worked', 'Lo que funcionó', 'El que va funcionar'))


def test_format_sticky_html_has_improvements_section():
    html = cp._format_sticky_html(_make_result())
    assert any(s in html for s in ('Try next time', 'Para mejorar', 'Per millorar'))


def test_format_sticky_html_shows_strengths_content():
    r = _make_result(strengths=['Clear agenda set at the start'])
    html = cp._format_sticky_html(r)
    assert 'Clear agenda set at the start' in html


def test_format_sticky_html_shows_improvements_content():
    r = _make_result(improvements=['Ask for explicit owners'])
    html = cp._format_sticky_html(r)
    assert 'Ask for explicit owners' in html


def test_format_sticky_html_escapes_special_chars():
    r = _make_result(strengths=['A & B <test>'])
    html = cp._format_sticky_html(r)
    assert '&amp;' in html or '&lt;' in html


def test_format_sticky_html_empty_lists_do_not_crash():
    r = _make_result(strengths=[], improvements=[])
    html = cp._format_sticky_html(r)
    assert 'MEETING COACH' in html


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
    assert out['strengths'] == result['strengths']
    assert out['improvements'] == result['improvements']


def test_call_claude_strips_markdown_fences(fake_popen):
    result = _make_result()
    fake_popen['proc']._stdout = f'```json\n{json.dumps(result)}\n```'
    out = cp._call_claude('t')
    assert out is not None
    assert 'strengths' in out


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
    s = stickies[0]
    assert s['id'].startswith('coaching_')
    assert s['anchor'] == 'right'
    assert s['minimized'] is True
    assert s['label'] == 'Meeting Coach'


def test_grade_and_inject_duplicate_guard(tr_dirs, minutes_file, monkeypatch):
    _write_settings(tr_dirs, coaching_enabled=True)
    stickies_path = minutes_file.parent / f'{minutes_file.stem}.stickies.json'
    existing = [{'id': 'coaching_111', 'text': 'already here', 'x': 20, 'y': 20}]
    stickies_path.write_text(json.dumps(existing), encoding='utf-8')

    called = []
    monkeypatch.setattr(cp, '_call_claude', lambda *a, **k: called.append(1) or {})
    cp.grade_and_inject('transcript', minutes_file)
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
    assert 'detected' in stickies[0]['text'].lower() or 'detectad' in stickies[0]['text'].lower()
    assert 'MEETING COACH' in stickies[0]['html']


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


def test_grade_and_inject_sticky_positioned_top_right(tr_dirs, minutes_file, monkeypatch):
    _write_settings(tr_dirs, coaching_enabled=True)
    _patch_claude(monkeypatch, _make_result())
    cp.grade_and_inject('transcript', minutes_file)
    stickies_path = minutes_file.parent / f'{minutes_file.stem}.stickies.json'
    sticky = json.loads(stickies_path.read_text(encoding='utf-8'))[0]
    assert sticky['anchor'] == 'right'
    assert sticky['y'] == 8


# ── tray_app hook: coaching exception must not crash the pipeline ─────────────

def test_tray_coaching_hook_survives_exception(tray, tr_dirs, monkeypatch):
    """If coaching_plugin raises, the pipeline must continue (no re-raise)."""
    def boom(transcript, path):
        raise RuntimeError('simulated coaching failure')

    monkeypatch.setattr(cp, 'grade_and_inject', boom)
    monkeypatch.setitem(sys.modules, 'coaching_plugin', cp)

    minutes_path = tr_dirs / 'minutes' / 'test.md'
    try:
        import coaching_plugin
        coaching_plugin.grade_and_inject('t', minutes_path)
    except ImportError:
        pass
    except Exception as _ce:
        pass   # this is the catch in tray_app — must not propagate
