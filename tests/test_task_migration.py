"""Importar al tablero las acciones marcadas en las reuniones.

`migrate_panel_actions` corre en CADA arranque de la app y dice de si misma
"safe to call on every startup". Si esa promesa se rompe, cada arranque
duplica tareas en el tablero del usuario, y eso no se deshace solo.

OJO CON LA COSTURA: esta funcion re-importa MINUTES_DIR dentro del cuerpo, asi
que hay que parchear `config.MINUTES_DIR`, no `tasks_store.MINUTES_DIR` (que
no existe).
"""
import json

import pytest

import tasks_store as ts

pytestmark = pytest.mark.unit


@pytest.fixture
def reunion_con_acciones(tr_dirs):
    """Un _actions.json con dos acciones marcadas para el panel y una que no."""
    import config

    md = config.MINUTES_DIR / '20260917_1616_Comite.md'
    md.write_text('TITULO: Comite\n\ncuerpo', encoding='utf-8')
    (config.MINUTES_DIR / '20260917_1616_Comite_actions.json').write_text(
        json.dumps({
            'minutes': str(md),
            'project_id': 'contoso',
            'actions': [
                {'index': 0, 'title': 'Enviar la propuesta', 'in_panel': True,
                 'assignee': 'Felipe', 'deadline': '30/09',
                 'claude_executable': False},
                {'index': 1, 'title': 'Actualizar el deck', 'in_panel': True,
                 'executed': True, 'claude_executable': True},
                {'index': 2, 'title': 'Esta no esta en el panel'},
            ],
        }), encoding='utf-8')
    return md


# ── La migracion ─────────────────────────────────────────────────────────

def test_importa_solo_las_acciones_marcadas_para_el_panel(reunion_con_acciones):
    """Las que no estan marcadas son acciones de la reunion que el usuario no
    ha querido llevar a su tablero."""
    n = ts.migrate_panel_actions()

    assert n == 2
    titulos = [t['title'] for t in ts.get_tasks()]
    assert 'Enviar la propuesta' in titulos
    assert 'Esta no esta en el panel' not in titulos


def test_LA_MIGRACION_ES_IDEMPOTENTE(reunion_con_acciones):
    """LA PROMESA DEL DOCSTRING. Corre en cada arranque: si no fuera
    idempotente, el tablero del usuario crece solo cada vez que abre la app."""
    ts.migrate_panel_actions()
    antes = len(ts.get_tasks())

    segunda = ts.migrate_panel_actions()
    tercera = ts.migrate_panel_actions()

    assert segunda == 0 and tercera == 0
    assert len(ts.get_tasks()) == antes


def test_la_segunda_vez_no_toca_el_fichero(reunion_con_acciones):
    ts.migrate_panel_actions()
    antes = ts.TASKS_FILE.read_bytes()

    ts.migrate_panel_actions()

    assert ts.TASKS_FILE.read_bytes() == antes


def test_el_estado_de_la_tarea_refleja_si_la_accion_se_ejecuto(reunion_con_acciones):
    ts.migrate_panel_actions()

    por_titulo = {t['title']: t['status'] for t in ts.get_tasks()}
    assert por_titulo['Actualizar el deck'] == 'done'
    assert por_titulo['Enviar la propuesta'] == 'not_started'


def test_se_conservan_responsable_y_fecha(reunion_con_acciones):
    ts.migrate_panel_actions()
    t = next(t for t in ts.get_tasks() if t['title'] == 'Enviar la propuesta')
    assert t['assignee'] == 'Felipe'
    assert t['end_date'] == '30/09'


def test_se_conserva_el_proyecto_de_la_reunion(reunion_con_acciones):
    ts.migrate_panel_actions()
    assert all(t['project_id'] == 'contoso' for t in ts.get_tasks())


def test_la_tarea_recuerda_de_que_reunion_salio(reunion_con_acciones):
    """Es lo que permite volver a la reunion desde el tablero, y lo que usa la
    deduplicacion para no importarla dos veces."""
    ts.migrate_panel_actions()
    t = next(t for t in ts.get_tasks() if t['title'] == 'Enviar la propuesta')
    assert t['source'] == 'meeting'
    assert t['meeting_path'].endswith('20260917_1616_Comite.md')
    assert t['meeting_action_index'] == 0


def test_no_se_duplica_una_accion_que_ya_estaba_en_el_tablero(reunion_con_acciones):
    """La deduplicacion es por (reunion, indice), no por titulo: dos acciones
    distintas pueden llamarse igual."""
    import config
    md = config.MINUTES_DIR / '20260917_1616_Comite.md'
    ts.create_task(project_id='contoso', title='Enviar la propuesta',
                   source='meeting', meeting_path=str(md), meeting_action_index=0)

    n = ts.migrate_panel_actions()

    assert n == 1, "solo deberia importar la que faltaba"
    titulos = [t['title'] for t in ts.get_tasks()]
    assert titulos.count('Enviar la propuesta') == 1


def test_un_actions_json_corrupto_no_aborta_la_migracion(reunion_con_acciones):
    """Un fichero roto no puede impedir que se importen las acciones de las
    demas reuniones."""
    import config
    (config.MINUTES_DIR / '20260101_0900_Roto_actions.json').write_text(
        '{ esto no es json', encoding='utf-8')

    n = ts.migrate_panel_actions()

    assert n == 2


def test_sin_reuniones_la_migracion_no_hace_nada(tr_dirs):
    assert ts.migrate_panel_actions() == 0
    assert ts.get_tasks() == []


def test_DEFECTO_la_migracion_se_marca_hecha_aunque_no_importe_nada(tr_dirs):
    """DEFECTO: pone migrated=True incluso si MINUTES_DIR estaba vacio o no
    existia. En el primer arranque de una instalacion nueva eso deja la
    migracion inutilizada PARA SIEMPRE: las acciones marcadas en reuniones
    anteriores nunca llegaran al tablero.

    Se fija el comportamiento actual."""
    ts.migrate_panel_actions()          # sin reuniones: 0 importadas

    datos = json.loads(ts.TASKS_FILE.read_text(encoding='utf-8'))
    assert datos['migrated'] is True, "si esto cambia, el defecto se arreglo"


def test_DEFECTO_la_migracion_usa_el_bucket_fantasma(reunion_con_acciones):
    """DEFECTO: hardcodea bucket_id='pendiente', que no existe en
    buckets.json. Las tareas importadas caen en un bucket que la vista de
    tablero no conoce."""
    import buckets_store as bs

    ts.migrate_panel_actions()

    ids_reales = {b['id'] for b in bs.get_buckets()}
    assert all(t['bucket_id'] == 'pendiente' for t in ts.get_tasks())
    assert 'pendiente' not in ids_reales


# ── Sincronizacion automatica de las acciones de una reunion ─────────────

ACCIONES = [
    {'index': 0, 'title': 'Primera', 'executed': False},
    {'index': 1, 'title': 'Segunda', 'executed': True},
]


def test_sincronizar_crea_una_tarea_por_accion(tr_dirs):
    n = ts.auto_sync_meeting_tasks('/ruta/reunion.md', ACCIONES, 'todo')

    assert n == 2
    assert {t['title'] for t in ts.get_tasks()} == {'Primera', 'Segunda'}


def test_sincronizar_respeta_el_bucket_que_se_le_pasa(tr_dirs):
    """Por aqui si se puede pasar un bucket real, al contrario que en la
    migracion."""
    ts.auto_sync_meeting_tasks('/ruta/reunion.md', ACCIONES, 'in-progress')
    assert all(t['bucket_id'] == 'in-progress' for t in ts.get_tasks())


def test_sincronizar_marca_hechas_las_acciones_ejecutadas(tr_dirs):
    ts.auto_sync_meeting_tasks('/ruta/reunion.md', ACCIONES, 'todo')
    por_titulo = {t['title']: t['status'] for t in ts.get_tasks()}
    assert por_titulo['Segunda'] == 'done'


def test_sincronizar_dos_veces_no_duplica(tr_dirs):
    ts.auto_sync_meeting_tasks('/ruta/reunion.md', ACCIONES, 'todo')
    segunda = ts.auto_sync_meeting_tasks('/ruta/reunion.md', ACCIONES, 'todo')

    assert segunda == 0
    assert len(ts.get_tasks()) == 2


def test_sincronizar_sin_acciones_no_escribe_el_fichero(tr_dirs):
    """_save solo se llama si count > 0: abrir una reunion sin acciones no
    debe tocar el tablero."""
    assert ts.auto_sync_meeting_tasks('/ruta/reunion.md', [], 'todo') == 0
    assert not ts.TASKS_FILE.exists()


def test_una_accion_nueva_en_una_reunion_ya_sincronizada_si_se_anade(tr_dirs):
    """Pasa al regenerar las minutas: aparece una accion que antes no estaba."""
    ts.auto_sync_meeting_tasks('/ruta/reunion.md', ACCIONES, 'todo')

    ampliadas = [*ACCIONES, {'index': 2, 'title': 'Tercera', 'executed': False}]
    n = ts.auto_sync_meeting_tasks('/ruta/reunion.md', ampliadas, 'todo')

    assert n == 1
    assert len(ts.get_tasks()) == 3
