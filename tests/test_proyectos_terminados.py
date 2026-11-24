"""Proyectos terminados y reuniones que se mueven de proyecto (24/11/2026).

Una reunion quedo en el proyecto equivocado. El usuario queria moverla
arrastrandola a la seccion que toca, y marcar como terminados los proyectos que
ya no tienen reuniones, para que no las reciban y no estorben en la lista.

LO QUE ESTOS TESTS PROTEGEN
1. Un proyecto terminado (`finished_at`) no lo elige ninguna deteccion: ni
   Claude (no esta en el prompt, y si aun asi lo devolviera no vale), ni el
   respaldo por titulo, ni las palabras clave que eligen contexto y glosario.
2. A mano, si: la eleccion del usuario manda, tambien hacia uno terminado.
3. Terminar no borra: el proyecto sigue en projects.json con su id, porque sus
   reuniones y su carpeta de project_docs lo nombran (test_consumer_contract), y
   rehacer una de sus reuniones no la saca de el.
4. La lista del panel ve el proyecto nuevo nada mas moverla: la cache de
   get_meetings ya no se queda con el de antes.
5. "Deshacer" deja la reunion exactamente como estaba: sin proyecto vuelve a la
   deteccion, y una suposicion del titulo no se vuelve manual (que se archiva).
"""
import json
import os
import time

import pytest

import actions_enricher as ae
import app_window as aw
import project_context as pc

pytestmark = pytest.mark.unit

PROJECTS = [
    {'id': 'sales-training', 'name': 'Sales Training Presentation'},
    {'id': 'atlas-project', 'name': 'Atlas Project', 'finished_at': '2026-11-24'},
]


def proyectos(tr_dirs):
    (tr_dirs / 'projects.json').write_text(json.dumps({'projects': PROJECTS}), encoding='utf-8')


def minutas(tr_dirs, nombre='20261124_1000_Atlas_Project_weekly.md'):
    md = tr_dirs / 'minutes' / nombre
    md.write_text('<!-- noted:minutes=pending stem=x -->\n\nNada.\n', encoding='utf-8')
    ae._save_json(md, [], {})
    proyectos(tr_dirs)
    return md


def leer(md):
    return json.loads(ae.get_actions_path(md).read_text(encoding='utf-8'))


# ── 1. Ninguna deteccion elige un proyecto terminado ─────────────────────────

def test_active_projects_quita_solo_los_terminados():
    assert [p['id'] for p in pc.active_projects(PROJECTS)] == ['sales-training']
    assert pc.active_projects([{'id': 'x', 'finished_at': ''}]) == [{'id': 'x', 'finished_at': ''}]


def test_el_titulo_no_da_un_proyecto_terminado(tr_dirs):
    assert pc.project_from_title('Atlas Project weekly', PROJECTS) is None
    proyectos(tr_dirs)
    assert pc.project_from_title('Atlas Project weekly') is None, "ni leyendo projects.json"
    assert pc.project_from_title('Sales Training prep')['id'] == 'sales-training'


def test_las_palabras_clave_no_eligen_un_terminado(tr_dirs):
    assert pc.detect_project('Repasamos Atlas Project', projects=PROJECTS) is None
    proyectos(tr_dirs)
    assert pc.detect_project('Repasamos Atlas Project') is None
    assert pc.detect_project('Sales Training')['id'] == 'sales-training'


def test_claude_no_ve_el_terminado_y_su_id_no_vale(tr_dirs, monkeypatch):
    md = minutas(tr_dirs)
    prompts = []

    def claude(cmd, input='', **_k):
        prompts.append(input)
        return type('R', (), {'returncode': 0, 'stdout': 'atlas-project', 'stderr': ''})()

    monkeypatch.setattr(ae, '_get_claude_bin', lambda: 'claude')
    monkeypatch.setattr(ae.subprocess, 'run', claude)
    ae._detect_and_save_project(md, tr_dirs)

    assert 'atlas-project' not in prompts[0] and 'sales-training' in prompts[0]
    assert leer(md)['project_id'] == 'none'


def test_sin_claude_el_titulo_tampoco_lo_da(tr_dirs, monkeypatch):
    md = minutas(tr_dirs)
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: None)
    ae._detect_and_save_project(md, tr_dirs)
    assert 'project_id' not in leer(md)


def test_con_todos_terminados_no_se_detecta_nada(tr_dirs, monkeypatch):
    md = minutas(tr_dirs)
    (tr_dirs / 'projects.json').write_text(json.dumps({'projects': PROJECTS[1:]}), encoding='utf-8')
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: pytest.fail('no hay a quien preguntar'))
    ae._detect_and_save_project(md, tr_dirs)
    assert 'project_id' not in leer(md)


# ── 2. A mano, si ────────────────────────────────────────────────────────────

def test_a_mano_se_puede_asignar_a_uno_terminado(tr_dirs, monkeypatch):
    md = minutas(tr_dirs)
    ae.set_meeting_project_id(md, 'atlas-project')
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: None)
    ae._detect_and_save_project(md, tr_dirs)
    assert leer(md)['project_id'] == 'atlas-project' and leer(md)['project_source'] == 'manual'


def test_rehacer_una_reunion_no_la_saca_de_su_proyecto_terminado(tr_dirs, monkeypatch):
    """La eligio Claude cuando el proyecto estaba activo. Ya no puede volver a
    elegirlo: un reintento de las minutas la mandaria a otro proyecto."""
    md = minutas(tr_dirs)
    data = leer(md)
    data['project_id'] = 'atlas-project'
    ae.get_actions_path(md).write_text(json.dumps(data), encoding='utf-8')

    ae._save_json(md, [], {})
    assert leer(md)['project_id'] == 'atlas-project'
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: 'claude')
    monkeypatch.setattr(ae.subprocess, 'run', lambda *a, **k: type('R', (), {
        'returncode': 0, 'stdout': 'sales-training', 'stderr': ''})())
    ae._detect_and_save_project(md, tr_dirs)
    assert leer(md)['project_id'] == 'atlas-project'


def test_una_suposicion_del_titulo_hacia_un_terminado_si_se_revisa(tr_dirs, monkeypatch):
    md = minutas(tr_dirs)
    data = leer(md)
    data.update(project_id='atlas-project', project_source='title')
    ae.get_actions_path(md).write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: 'claude')
    monkeypatch.setattr(ae.subprocess, 'run', lambda *a, **k: type('R', (), {
        'returncode': 0, 'stdout': 'sales-training', 'stderr': ''})())
    ae._detect_and_save_project(md, tr_dirs)
    assert leer(md)['project_id'] == 'sales-training' and 'project_source' not in leer(md)


# ── 3. Terminar no borra ─────────────────────────────────────────────────────

@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(aw.AppAPI, 'detect_projects_for_all', lambda self: None)
    return object.__new__(aw.AppAPI)


def test_terminar_y_reabrir_guardan_el_proyecto_entero(api, tr_dirs):
    api.save_project({'name': 'Contoso', 'stakeholders': ['a@contoso.com']})
    proyecto = api.get_projects()[0]

    api.save_project({**proyecto, 'finished_at': '2026-11-24'})
    terminado = api.get_projects()[0]
    assert terminado['finished_at'] == '2026-11-24'
    assert terminado['id'] == 'contoso' and terminado['stakeholders'] == ['a@contoso.com']

    api.save_project({k: v for k, v in terminado.items() if k != 'finished_at'})
    assert 'finished_at' not in api.get_projects()[0]


# ── 4. La lista ve el proyecto nuevo ─────────────────────────────────────────

def test_la_busqueda_trae_el_origen_del_proyecto(api, tr_dirs):
    """La etiqueta de la vista por dias marca "por confirmar" tambien en los
    resultados de una busqueda, no solo en la lista entera."""
    md = minutas(tr_dirs)
    data = leer(md)
    data.update(project_id='sales-training', project_source='title')
    ae.get_actions_path(md).write_text(json.dumps(data), encoding='utf-8')

    resultados = api.search('atlas')

    assert [(r['project_id'], r['project_source']) for r in resultados] == [('sales-training', 'title')]


def test_la_lista_ve_el_proyecto_nuevo_nada_mas_moverla(api, tr_dirs):
    md = minutas(tr_dirs)
    ae.set_meeting_project_id(md, 'sales-training')
    # La reasignacion de verdad llega segundos despues, no en el mismo tic del
    # reloj del sistema de ficheros.
    hace_un_rato = time.time() - 60
    os.utime(ae.get_actions_path(md), (hace_un_rato, hace_un_rato))
    assert api.get_meetings()[0]['project_id'] == 'sales-training'

    assert api.set_meeting_project(str(md), 'atlas-project')

    reunion = api.get_meetings()[0]
    assert reunion['project_id'] == 'atlas-project' and reunion['project_source'] == 'manual'


# ── 5. Deshacer deja la reunion como estaba ──────────────────────────────────

def test_deshacer_una_reunion_sin_proyecto_la_devuelve_a_la_deteccion(api, tr_dirs, monkeypatch):
    """Movida sin querer y deshecha: si quedara como 'none' a mano, ninguna
    deteccion volveria a mirarla."""
    md = minutas(tr_dirs)
    api.set_meeting_project(str(md), 'sales-training')

    assert api.restore_meeting_project(str(md), '', '')

    assert 'project_id' not in leer(md) and 'project_source' not in leer(md)
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: 'claude')
    monkeypatch.setattr(ae.subprocess, 'run', lambda *a, **k: type('R', (), {
        'returncode': 0, 'stdout': 'sales-training', 'stderr': ''})())
    ae._detect_and_save_project(md, tr_dirs)
    assert leer(md)['project_id'] == 'sales-training'


def test_deshacer_devuelve_una_suposicion_que_no_se_archiva(api, tr_dirs):
    """Una suposicion del titulo no se archiva en la carpeta del proyecto:
    deshacer no puede convertirla en una eleccion manual que si se archiva."""
    md = minutas(tr_dirs)
    api.set_meeting_project(str(md), 'atlas-project')
    copia = tr_dirs / 'project_docs' / 'atlas-project' / 'meetings' / f'{md.stem}.txt'
    assert copia.exists()

    assert api.restore_meeting_project(str(md), 'sales-training', 'title')

    assert leer(md)['project_id'] == 'sales-training' and leer(md)['project_source'] == 'title'
    assert pc.meeting_project_id(md) == '' and not copia.exists()


def test_deshacer_sin_fichero_de_acciones_falla_sin_romper(api, tr_dirs):
    md = tr_dirs / 'minutes' / '20261124_1200_Sin_Acciones.md'
    md.write_text('Nada.\n', encoding='utf-8')
    assert api.restore_meeting_project(str(md), 'sales-training', '') is False
