"""Regenerar acciones no puede dejar la reunion sin _actions.json.

Incidente que lo origina: regenerate_minutes, regenerate_actions y
enrich_actions hacian ap.unlink() ANTES de saber si la nueva generacion
producia algo. Si fallaba o Claude no estaba, la reunion se quedaba sin
acciones y se perdian in_panel, executed, las acciones manuales (source:
manual) y el assignee/deadline editados a mano.
"""
import json
import time

import pytest

import actions_enricher
import app_window as aw
import html_exporter
import minutes_generator

pytestmark = pytest.mark.unit

ORIGINAL = {'project_id': 'p', 'actions': [
    {'index': 0, 'title': 'Editada', 'in_panel': True, 'executed': True,
     'assignee': 'Ana', 'deadline': '2026-12-01'},
    {'index': 1, 'title': 'Manual', 'source': 'manual'},
]}


@pytest.fixture
def api():
    return object.__new__(aw.AppAPI)


@pytest.fixture
def minuta(tr_dirs):
    md = tr_dirs / 'minutes' / '20260916_1200_Reunion.md'
    md.write_text('TITULO: Reunion\n\n## Acciones\n- algo\n', encoding='utf-8')
    ap = actions_enricher.get_actions_path(md)
    ap.write_text(json.dumps(ORIGINAL), encoding='utf-8')
    return md, ap


def _original(ap):
    return json.loads(ap.read_text(encoding='utf-8')) == ORIGINAL


def test_enrich_actions_sin_resultado_restaura_el_original(api, minuta, monkeypatch):
    md, ap = minuta
    monkeypatch.setattr(actions_enricher, 'enrich_and_save',
                        lambda m, d, on_done=None, **_k: on_done and on_done())
    assert api.enrich_actions(str(md)) is True
    assert _original(ap)
    assert not ap.with_name(ap.name + '.bak').exists()


def test_enrich_actions_que_lanza_restaura_el_original(api, minuta, monkeypatch):
    md, ap = minuta

    def explota(*_a, **_k):
        raise RuntimeError('claude no esta')
    monkeypatch.setattr(actions_enricher, 'enrich_and_save', explota)
    with pytest.raises(RuntimeError):
        api.enrich_actions(str(md))
    assert _original(ap)


def test_enrich_actions_con_resultado_valido_lo_conserva(api, minuta, monkeypatch):
    md, ap = minuta

    def escribe(m, d, on_done=None, **_k):
        ap.write_text(json.dumps({'actions': [{'index': 0, 'title': 'Nueva'}]}),
                      encoding='utf-8')
        on_done and on_done()
    monkeypatch.setattr(actions_enricher, 'enrich_and_save', escribe)
    api.enrich_actions(str(md))
    assert json.loads(ap.read_text(encoding='utf-8'))['actions'][0]['title'] == 'Nueva'
    assert not ap.with_name(ap.name + '.bak').exists()


def test_un_json_a_medias_tampoco_vale_como_resultado(api, minuta, monkeypatch):
    md, ap = minuta

    def escribe_roto(m, d, on_done=None, **_k):
        ap.write_text('{"actions": [', encoding='utf-8')
        on_done and on_done()
    monkeypatch.setattr(actions_enricher, 'enrich_and_save', escribe_roto)
    api.enrich_actions(str(md))
    assert _original(ap)


def test_regenerate_actions_sin_resultado_restaura_el_original(api, minuta, monkeypatch):
    md, ap = minuta
    monkeypatch.setattr(minutes_generator, 'regenerate_actions_section',
                        lambda *a, **k: '## Acciones\n- nueva\n')
    monkeypatch.setattr(html_exporter, 'export_to_html', lambda *a, **k: None)
    monkeypatch.setattr(actions_enricher, 'enrich_and_save',
                        lambda m, d, on_done=None, **_k: on_done and on_done())

    assert api.regenerate_actions(str(md), 'cambia algo', 'es') is True
    t0 = time.monotonic()
    while not api.get_actions_regen_status(str(md))['done'] and time.monotonic() - t0 < 10:
        time.sleep(0.02)

    st = api.get_actions_regen_status(str(md))
    assert st['done'] and not st['error']
    assert _original(ap)
