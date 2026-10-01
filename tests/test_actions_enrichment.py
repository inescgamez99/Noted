"""Enriquecimiento de acciones y deteccion del proyecto con el CLI de claude.

Sin llamar a ningun LLM: se sustituye subprocess.run.

Lo que protege: la respuesta del modelo es un JSON que puede venir con texto
alrededor, mal formado o directamente vacio. Si el parseo falla en silencio,
las acciones de la reunion se guardan sin enriquecer y nadie se entera — el
panel las muestra igual, solo peor.

El invariante mas importante: el _actions.json se escribe SIEMPRE, incluso si
el CLI falla, porque es el fichero del que dependen el panel, el ruteo a
proyectos y el consumidor externo.
"""
import json

import pytest

import actions_enricher as ae
from datetime import UTC

pytestmark = pytest.mark.unit

STEM = '20260917_1616_Comite_Semanal'

MINUTA_CON_ACCIONES = """TITULO: Comite Semanal

## Resumen Ejecutivo

Se hablo del deck.

~~~instruction-for-claude
Actualizar el deck de ventas con los numeros de Q4
~~~

## Acciones Pendientes

| Accion | Owner | Deadline |
|---|---|---|
| Revisar el presupuesto | Ines | 30/09 |
"""


@pytest.fixture
def minuta(tr_dirs):
    md = ae.PROJECT_DIR / 'minutes' / f'{STEM}.md'
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(MINUTA_CON_ACCIONES, encoding='utf-8')
    return md


@pytest.fixture
def proyectos(tr_dirs):
    d = tr_dirs / 'proyectos'
    (d / 'aramco').mkdir(parents=True)
    (d / 'aramco' / 'CLAUDE.md').write_text('Proyecto Aramco de Agentic AI.',
                                             encoding='utf-8')
    (d / 'otro').mkdir()
    return d


@pytest.fixture
def cli_falso(monkeypatch):
    """subprocess.run sustituido. Se controla la salida y se guardan las
    llamadas para comprobar el prompt."""
    estado = {'salida': '[]', 'rc': 0, 'llamadas': []}

    def run_falso(cmd, **kwargs):
        estado['llamadas'].append({'cmd': cmd, 'input': kwargs.get('input'),
                                   'timeout': kwargs.get('timeout'),
                                   'env': kwargs.get('env')})
        return type('R', (), {'returncode': estado['rc'],
                              'stdout': estado['salida'], 'stderr': ''})()

    monkeypatch.setattr(ae.subprocess, 'run', run_falso)
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: 'claude')
    # _detect_and_save_project usa el mismo subprocess; se neutraliza para
    # aislar cada test.
    monkeypatch.setattr(ae, '_detect_and_save_project', lambda *a, **k: None)
    return estado


def _acciones(minuta):
    return json.loads(ae.get_actions_path(minuta).read_text(encoding='utf-8'))['actions']


# ── El fichero se escribe siempre ────────────────────────────────────────

def test_sin_acciones_se_escribe_un_actions_vacio(tr_dirs, cli_falso):
    """El panel distingue 'sin acciones' de 'aun no generadas'. Si no se
    escribiera el fichero, se quedaria en el segundo estado para siempre."""
    md = ae.PROJECT_DIR / 'minutes' / f'{STEM}.md'
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text('TITULO: Sin acciones\n\n## Resumen\n\nNada que hacer.\n',
                  encoding='utf-8')

    ae._run_enrichment(md, tr_dirs / 'proyectos')

    assert ae.get_actions_path(md).exists()
    assert _acciones(md) == []


def test_se_guardan_las_acciones_de_los_dos_origenes(minuta, proyectos, cli_falso):
    """El bloque de Claude y la fila de la tabla describen tareas distintas:
    las dos tienen que llegar al panel."""
    ae._run_enrichment(minuta, proyectos)

    titulos = [a['title'] for a in _acciones(minuta)]
    assert any('deck de ventas' in t for t in titulos)
    assert 'Revisar el presupuesto' in titulos


def test_si_el_cli_falla_las_acciones_se_guardan_sin_enriquecer(minuta, proyectos,
                                                                cli_falso):
    """Es el caso importante: Claude no disponible no puede costar las acciones
    de la reunion."""
    cli_falso['rc'] = 1

    ae._run_enrichment(minuta, proyectos)

    acciones = _acciones(minuta)
    assert len(acciones) >= 2
    assert all(a['enriched_ok'] is False for a in acciones)
    assert acciones[0]['prompt_enriched'] == acciones[0]['prompt_original']


def test_sin_el_cli_instalado_tambien_se_guardan(minuta, proyectos, monkeypatch):
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: None)
    monkeypatch.setattr(ae, '_detect_and_save_project', lambda *a, **k: None)

    ae._run_enrichment(minuta, proyectos)

    assert len(_acciones(minuta)) >= 2


def test_una_excepcion_del_subproceso_no_pierde_las_acciones(minuta, proyectos,
                                                             monkeypatch):
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: 'claude')
    monkeypatch.setattr(ae, '_detect_and_save_project', lambda *a, **k: None)
    monkeypatch.setattr(ae.subprocess, 'run',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('boom')))

    ae._run_enrichment(minuta, proyectos)

    assert len(_acciones(minuta)) >= 2


# ── Parseo de la respuesta del modelo ────────────────────────────────────

def test_se_aplica_el_enriquecimiento_devuelto(minuta, proyectos, cli_falso):
    cli_falso['salida'] = json.dumps([
        {'index': 0, 'project': 'aramco', 'assignee': 'Felipe',
         'prompt': 'prompt enriquecido y completo'},
    ])

    ae._run_enrichment(minuta, proyectos)

    accion = _acciones(minuta)[0]
    assert accion['prompt_enriched'] == 'prompt enriquecido y completo'
    assert accion['project'] == 'aramco'
    assert accion['enriched_ok'] is True


def test_se_extrae_el_json_aunque_venga_con_texto_alrededor(minuta, proyectos,
                                                            cli_falso):
    """El modelo ignora a veces el 'responde SOLO con el JSON'. La busqueda por
    regex del array es lo que salva esos casos."""
    cli_falso['salida'] = (
        'Claro, aqui tienes el analisis:\n\n'
        '[{"index": 0, "project": null, "assignee": null, "prompt": "hazlo"}]\n\n'
        'Espero que te sirva.'
    )

    ae._run_enrichment(minuta, proyectos)

    assert _acciones(minuta)[0]['prompt_enriched'] == 'hazlo'


@pytest.mark.parametrize('basura', [
    'no hay ningun json aqui',
    '',
    '[esto no es json valido',
    '{"index": 0}',                    # objeto, no array
])
def test_una_respuesta_inutilizable_no_pierde_las_acciones(minuta, proyectos,
                                                           cli_falso, basura):
    cli_falso['salida'] = basura

    ae._run_enrichment(minuta, proyectos)

    acciones = _acciones(minuta)
    assert len(acciones) >= 2
    assert acciones[0]['prompt_enriched'] == acciones[0]['prompt_original']


def test_un_enriquecimiento_parcial_solo_afecta_a_su_accion(minuta, proyectos,
                                                            cli_falso):
    cli_falso['salida'] = json.dumps([
        {'index': 0, 'project': None, 'assignee': None, 'prompt': 'solo la cero'}])

    ae._run_enrichment(minuta, proyectos)

    acciones = _acciones(minuta)
    assert acciones[0]['enriched_ok'] is True
    assert acciones[1]['enriched_ok'] is False


# ── El prompt que se le manda ────────────────────────────────────────────

def test_el_prompt_incluye_los_proyectos_disponibles(minuta, proyectos, cli_falso):
    """Sin ellos el modelo no puede asignar la accion a un proyecto."""
    ae._run_enrichment(minuta, proyectos)

    prompt = cli_falso['llamadas'][0]['input']
    assert 'aramco' in prompt
    assert 'Agentic AI' in prompt, "falta el resumen del CLAUDE.md del proyecto"


def test_el_prompt_incluye_las_acciones_a_enriquecer(minuta, proyectos, cli_falso):
    prompt = None
    ae._run_enrichment(minuta, proyectos)
    prompt = cli_falso['llamadas'][0]['input']
    assert 'deck de ventas' in prompt


def test_sin_proyectos_el_prompt_lo_dice_explicitamente(minuta, tr_dirs, cli_falso):
    ae._run_enrichment(minuta, tr_dirs / 'no_existe')
    assert '(ninguno)' in cli_falso['llamadas'][0]['input']


def test_el_prompt_pide_el_idioma_de_la_reunion(minuta, proyectos, cli_falso):
    """La minuta esta en espanol: el prompt enriquecido tambien debe estarlo, o
    el panel mezcla idiomas."""
    prompt = None
    ae._run_enrichment(minuta, proyectos)
    prompt = cli_falso['llamadas'][0]['input']
    assert 'español' in prompt


def test_con_una_minuta_en_ingles_se_pide_ingles(tr_dirs, proyectos, cli_falso):
    md = ae.PROJECT_DIR / 'minutes' / f'{STEM}.md'
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text('## Executive Summary\n\nx\n\n'
                  '~~~instruction-for-claude\nupdate the deck\n~~~\n',
                  encoding='utf-8')

    ae._run_enrichment(md, proyectos)

    assert 'in English' in cli_falso['llamadas'][0]['input']


def test_el_entorno_del_subproceso_va_limpio_de_tokens(minuta, proyectos, cli_falso):
    ae._run_enrichment(minuta, proyectos)
    env = cli_falso['llamadas'][0]['env']
    assert not [k for k in env if k.startswith(('ANTHROPIC_', 'MCP_'))]


def test_el_enriquecimiento_tiene_timeout(minuta, proyectos, cli_falso):
    """Sin el, un claude colgado bloquearia el pipeline de la reunion."""
    ae._run_enrichment(minuta, proyectos)
    assert cli_falso['llamadas'][0]['timeout'] == 120


# ── Forma del fichero guardado ───────────────────────────────────────────

@pytest.mark.parametrize('clave', [
    'index', 'type', 'title', 'archivo', 'context', 'prompt_original', 'project',
    'project_path', 'prompt_enriched', 'enriched_ok', 'assignee', 'deadline',
    'created_at', 'claude_executable', 'executed',
])
def test_cada_accion_lleva_las_quince_claves(minuta, proyectos, cli_falso, clave):
    """El JS del panel las lee todas: una que falte es un undefined en la UI."""
    ae._run_enrichment(minuta, proyectos)
    assert clave in _acciones(minuta)[0]


def test_la_fecha_de_creacion_sale_del_stem_no_del_reloj(minuta, proyectos, cli_falso):
    """Al reprocesar una reunion vieja, sus acciones deben datarse el dia de la
    reunion, no el del reproceso."""
    ae._run_enrichment(minuta, proyectos)
    assert _acciones(minuta)[0]['created_at'] == '2026-09-17'


def test_con_un_stem_no_conforme_la_fecha_cae_a_hoy(tr_dirs, proyectos, cli_falso):
    from datetime import datetime
    md = ae.PROJECT_DIR / 'minutes' / 'suelta.md'
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text('~~~instruction-for-claude\nalgo\n~~~', encoding='utf-8')

    ae._run_enrichment(md, proyectos)

    hoy = datetime.now(UTC).date().isoformat()
    assert _acciones(md)[0]['created_at'] == hoy


def test_solo_las_acciones_de_claude_son_ejecutables(minuta, proyectos, cli_falso):
    """Una fila de tabla es una tarea para una persona: el panel no debe
    ofrecer un boton de 'ejecutar con Claude' sobre ella."""
    ae._run_enrichment(minuta, proyectos)

    por_tipo = {a['type']: a['claude_executable'] for a in _acciones(minuta)}
    assert por_tipo['instruction'] is True
    assert por_tipo['human'] is False


def test_las_acciones_nacen_sin_ejecutar(minuta, proyectos, cli_falso):
    ae._run_enrichment(minuta, proyectos)
    assert all(a['executed'] is False for a in _acciones(minuta))


def test_el_responsable_de_la_tabla_gana_al_que_adivina_el_modelo(minuta, proyectos,
                                                                  cli_falso):
    """La tabla la escribio alguien mirando la reunion; el modelo adivina."""
    cli_falso['salida'] = json.dumps([
        {'index': 1, 'project': None, 'assignee': 'Quien Sea', 'prompt': 'x'}])

    ae._run_enrichment(minuta, proyectos)

    de_tabla = next(a for a in _acciones(minuta) if a['type'] == 'human')
    assert de_tabla['assignee'] == 'Ines'


def test_el_fichero_guardado_es_legible(minuta, proyectos, cli_falso):
    ae._run_enrichment(minuta, proyectos)
    crudo = ae.get_actions_path(minuta).read_text(encoding='utf-8')
    assert '\n  ' in crudo, "deberia estar indentado"


def test_el_callback_de_fin_se_llama_siempre(minuta, proyectos, cli_falso):
    """La UI lo usa para quitar el spinner: si no se llamara, se quedaria
    girando para siempre."""
    llamado = []
    ae._run_enrichment(minuta, proyectos, on_done=lambda: llamado.append(True))
    assert llamado == [True]


def test_el_callback_se_llama_aunque_todo_falle(minuta, proyectos, monkeypatch):
    llamado = []

    def leer_que_revienta(*_a, **_k):
        raise OSError('disco')

    monkeypatch.setattr(type(minuta), 'read_text', leer_que_revienta)

    ae._run_enrichment(minuta, proyectos, on_done=lambda: llamado.append(True))

    assert llamado == [True]


def test_un_callback_que_revienta_no_propaga(minuta, proyectos, cli_falso):
    def boom():
        raise RuntimeError('la UI fallo')

    ae._run_enrichment(minuta, proyectos, on_done=boom)      # no debe lanzar


# ── Anadir una accion a mano ─────────────────────────────────────────────

def test_anadir_una_accion_manual_sobre_un_fichero_existente(minuta, proyectos,
                                                             cli_falso):
    ae._run_enrichment(minuta, proyectos)
    antes = len(_acciones(minuta))

    ae.add_manual_action(minuta, 'Tarea puesta a mano')

    acciones = _acciones(minuta)
    assert len(acciones) == antes + 1
    assert acciones[-1]['title'] == 'Tarea puesta a mano'


def test_los_indices_no_se_reutilizan_al_anadir(minuta, proyectos, cli_falso):
    """Si se reutilizaran, editar una accion editaria otra: el indice es su
    identidad en el _actions.json."""
    ae._run_enrichment(minuta, proyectos)
    indices_antes = {a['index'] for a in _acciones(minuta)}

    ae.add_manual_action(minuta, 'Nueva')

    nueva = _acciones(minuta)[-1]
    assert nueva['index'] not in indices_antes


def test_anadir_una_accion_sin_actions_previo_crea_el_fichero(minuta):
    ae.add_manual_action(minuta, 'Primera a mano')
    assert [a['title'] for a in _acciones(minuta)] == ['Primera a mano']
