"""El tablero de tareas: tasks.json y buckets.json.

Son los datos que el usuario edita a mano desde el panel, asi que un fallo
aqui le borra trabajo suyo, no un fichero regenerable.

NOTA DE SEGURIDAD: estos dos modulos resuelven su ruta en tiempo de IMPORT
contra la raiz del repo. Sin la fixture `tr_dirs` (autouse en el conftest),
cualquiera de estos tests escribiria en el tablero real.
"""
import json
import uuid

import pytest

import buckets_store as bs
import tasks_store as ts

pytestmark = pytest.mark.unit


# ── Normalizacion de etiquetas ───────────────────────────────────────────

@pytest.mark.parametrize('entrada, esperado', [
    (['una', 'dos'], ['una', 'dos']),
    ([{'name': 'roja', 'color': '#f00'}], [{'name': 'roja', 'color': '#f00'}]),
    (['valida', ''], ['valida']),                       # cadenas vacias fuera
    ([{'color': '#f00'}], []),                          # dict sin name fuera
    ([{'name': ''}], []),                               # name vacio fuera
    ('no soy una lista', []),
    (None, []),
    ([], []),
])
def test_normalize_tags(entrada, esperado):
    assert ts._normalize_tags(entrada) == esperado


def test_normalize_tags_conserva_el_orden_y_el_color():
    entrada = ['a', {'name': 'b', 'color': '#0f0'}, 'c']
    assert ts._normalize_tags(entrada) == entrada


def test_get_tasks_normaliza_sin_escribir_en_disco(tr_dirs):
    """El docstring lo promete: 'no disk write'. Si escribiera, abrir el panel
    modificaria el fichero del usuario en cada carga."""
    ts.create_task(project_id='none', title='con basura')
    datos = json.loads(ts.TASKS_FILE.read_text(encoding='utf-8'))
    datos['tasks'][0]['tags'] = ['ok', {'sin': 'name'}, '']
    ts.TASKS_FILE.write_text(json.dumps(datos), encoding='utf-8')
    antes = ts.TASKS_FILE.read_bytes()

    tareas = ts.get_tasks()

    assert tareas[0]['tags'] == ['ok'], "no normalizo en memoria"
    assert ts.TASKS_FILE.read_bytes() == antes, "toco el fichero al leerlo"


# ── Cargar el fichero ────────────────────────────────────────────────────

def test_sin_fichero_el_tablero_esta_vacio(tr_dirs):
    assert ts.get_tasks() == []


def test_un_tasks_json_corrupto_no_pierde_el_panel(tr_dirs):
    """Mejor un tablero vacio que una excepcion que deje el panel en blanco."""
    ts.TASKS_FILE.write_text('{ roto', encoding='utf-8')
    assert ts.get_tasks() == []


def test_DEFECTO_un_json_valido_sin_clave_tasks_rompe_al_crear(tr_dirs):
    """DEFECTO (tasks_store.py:39): _load solo devuelve el default cuando el
    fichero falta o es ilegible. Un JSON VALIDO pero sin la clave 'tasks' se
    devuelve tal cual, y create_task revienta con KeyError al hacer
    data['tasks'].append.

    Se fija el comportamiento actual. El arreglo es un setdefault."""
    ts.TASKS_FILE.write_text('{"migrated": true}', encoding='utf-8')

    assert ts.get_tasks() == [], "leer aguanta"
    with pytest.raises(KeyError):
        ts.create_task(project_id='none', title='reventara')


# ── Crear tareas ─────────────────────────────────────────────────────────

def test_crear_una_tarea_devuelve_todos_sus_campos(tr_dirs):
    t = ts.create_task(project_id='contoso', title='Enviar propuesta')

    assert t['title'] == 'Enviar propuesta'
    assert t['project_id'] == 'contoso'
    assert uuid.UUID(t['id'])                   # id valido
    assert t['created_at'].endswith('+00:00'), "created_at deberia llevar zona"


def test_sin_proyecto_la_tarea_queda_en_none(tr_dirs):
    """El panel filtra por project_id: un None de JSON rompe ese filtro."""
    assert ts.create_task(project_id='', title='x')['project_id'] == 'none'


def test_deadline_es_un_alias_de_end_date(tr_dirs):
    """Compatibilidad con las tareas creadas antes del renombrado."""
    t = ts.create_task(project_id='none', title='x', deadline='2026-09-30')
    assert t['end_date'] == '2026-09-30'


def test_end_date_gana_sobre_deadline(tr_dirs):
    t = ts.create_task(project_id='none', title='x',
                       deadline='2026-01-01', end_date='2026-12-31')
    assert t['end_date'] == '2026-12-31'


def test_las_tareas_se_acumulan(tr_dirs):
    ts.create_task(project_id='none', title='una')
    ts.create_task(project_id='none', title='dos')
    assert [t['title'] for t in ts.get_tasks()] == ['una', 'dos']


def test_DEFECTO_el_bucket_por_defecto_no_existe_en_el_tablero(tr_dirs):
    """DEFECTO (tasks_store.py:106): el default es `bucket_id or 'pendiente'`,
    pero 'pendiente' NO es uno de los buckets de buckets.json (todo,
    in-progress, testing, done). Una tarea creada por esta ruta cae en un
    bucket fantasma y puede no aparecer en la vista de tablero.

    La ruta del panel (app_window.create_task) si pasa get_first_bucket_id(),
    asi que el defecto solo se manifiesta por migracion y sincronizacion."""
    t = ts.create_task(project_id='none', title='x')
    ids_reales = {b['id'] for b in bs.get_buckets()}

    assert t['bucket_id'] == 'pendiente'
    assert t['bucket_id'] not in ids_reales, "si esto cambia, el defecto se arreglo"


def test_pasando_un_bucket_valido_se_respeta(tr_dirs):
    t = ts.create_task(project_id='none', title='x', bucket_id='in-progress')
    assert t['bucket_id'] == 'in-progress'
    assert t['bucket_id'] in {b['id'] for b in bs.get_buckets()}


# ── Actualizar tareas ────────────────────────────────────────────────────

def test_actualizar_cambia_solo_los_campos_permitidos(tr_dirs):
    t = ts.create_task(project_id='none', title='original')

    assert ts.update_task(t['id'], {'title': 'cambiado'}) is True
    assert ts.get_tasks()[0]['title'] == 'cambiado'


def test_los_campos_de_identidad_no_se_pueden_falsear_desde_el_panel(tr_dirs):
    """id, created_at y source estan fuera de la whitelist: el JS no puede
    reescribir la procedencia de una tarea."""
    t = ts.create_task(project_id='none', title='x', source='meeting')

    ts.update_task(t['id'], {'id': 'otro-id', 'source': 'manual',
                             'created_at': '1999-01-01'})

    guardada = ts.get_tasks()[0]
    assert guardada['id'] == t['id']
    assert guardada['source'] == 'meeting'
    assert guardada['created_at'] == t['created_at']


def test_actualizar_deja_marca_de_ultima_edicion(tr_dirs):
    t = ts.create_task(project_id='none', title='x')
    ts.update_task(t['id'], {'title': 'y'})
    assert 'last_edited' in ts.get_tasks()[0]


def test_actualizar_una_tarea_inexistente_devuelve_false(tr_dirs):
    assert ts.update_task('no-existe', {'title': 'x'}) is False


def test_DEFECTO_actualizar_deadline_crea_una_clave_huerfana(tr_dirs):
    """DEFECTO: 'deadline' esta en la whitelist de update_task, pero
    create_task persiste 'end_date'. Editar la fecha desde el panel por la
    clave antigua crea un campo 'deadline' que la UI no lee: el usuario cree
    que guardo la fecha y no se ve."""
    t = ts.create_task(project_id='none', title='x', end_date='2026-01-01')

    ts.update_task(t['id'], {'deadline': '2026-12-31'})

    guardada = ts.get_tasks()[0]
    assert guardada['deadline'] == '2026-12-31'
    assert guardada['end_date'] == '2026-01-01', "end_date, que es lo que lee la UI, no cambio"


# ── Borrar tareas ────────────────────────────────────────────────────────

def test_borrar_quita_la_tarea(tr_dirs):
    t = ts.create_task(project_id='none', title='x')
    assert ts.delete_task(t['id']) is True
    assert ts.get_tasks() == []


def test_borrar_se_lleva_las_subtareas_directas(tr_dirs):
    padre = ts.create_task(project_id='none', title='padre')
    ts.create_task(project_id='none', title='hija', parent_id=padre['id'])

    ts.delete_task(padre['id'])

    assert ts.get_tasks() == []


def test_DEFECTO_borrar_solo_baja_un_nivel_y_deja_nietos_huerfanos(tr_dirs):
    """DEFECTO: delete_task borra la tarea y sus hijos DIRECTOS. Con tres
    niveles, el nieto sobrevive apuntando a un parent_id que ya no existe, y
    el panel no sabe donde colgarlo."""
    abuelo = ts.create_task(project_id='none', title='abuelo')
    padre = ts.create_task(project_id='none', title='padre', parent_id=abuelo['id'])
    ts.create_task(project_id='none', title='nieto', parent_id=padre['id'])

    ts.delete_task(abuelo['id'])

    restantes = ts.get_tasks()
    assert [t['title'] for t in restantes] == ['nieto']
    assert restantes[0]['parent_id'] == padre['id'], "apunta a un padre inexistente"


def test_borrar_una_tarea_inexistente_no_lanza(tr_dirs):
    ts.delete_task('no-existe')


# ── Valores personalizados ───────────────────────────────────────────────

def test_los_valores_personalizados_hacen_round_trip(tr_dirs):
    ts.save_custom_values('custom_statuses', ['bloqueada', 'en revision'])
    assert ts.get_custom_values('custom_statuses') == ['bloqueada', 'en revision']


def test_un_campo_personalizado_inexistente_devuelve_lista_vacia(tr_dirs):
    assert ts.get_custom_values('no_existe') == []


def test_guardar_valores_personalizados_no_toca_las_tareas(tr_dirs):
    """Van en el mismo fichero: un guardado descuidado borraria el tablero."""
    ts.create_task(project_id='none', title='no me borres')

    ts.save_custom_values('custom_priorities', ['urgente'])

    assert [t['title'] for t in ts.get_tasks()] == ['no me borres']


# ── Buckets ──────────────────────────────────────────────────────────────

def test_sin_fichero_se_siembran_los_buckets_por_defecto(tr_dirs):
    """OJO: es un getter CON efecto de escritura. Se documenta porque
    sorprende: leer los buckets crea el fichero."""
    assert not bs.BUCKETS_FILE.exists()

    buckets = bs.get_buckets()

    assert [b['id'] for b in buckets] == ['todo', 'in-progress', 'testing', 'done']
    assert bs.BUCKETS_FILE.exists(), "el getter deberia haber sembrado el fichero"


def test_un_buckets_json_corrupto_se_resiembra(tr_dirs):
    bs.BUCKETS_FILE.write_text('{roto', encoding='utf-8')
    assert len(bs.get_buckets()) == 4


def test_una_lista_de_buckets_vacia_se_resiembra(tr_dirs):
    bs.BUCKETS_FILE.write_text('{"buckets": []}', encoding='utf-8')
    assert len(bs.get_buckets()) == 4


def test_los_buckets_se_devuelven_ordenados(tr_dirs):
    bs.save_buckets([
        {'id': 'c', 'name': 'C', 'order': 2},
        {'id': 'a', 'name': 'A', 'order': 0},
        {'id': 'b', 'name': 'B', 'order': 1},
    ])
    assert [b['id'] for b in bs.get_buckets()] == ['a', 'b', 'c']


def test_un_bucket_sin_order_cuenta_como_cero(tr_dirs):
    bs.save_buckets([{'id': 'con', 'name': 'C', 'order': 1}, {'id': 'sin', 'name': 'S'}])
    assert [b['id'] for b in bs.get_buckets()] == ['sin', 'con']


def test_el_primer_bucket_es_el_de_orden_mas_bajo(tr_dirs):
    assert bs.get_first_bucket_id() == 'todo'


def test_guardar_buckets_hace_round_trip(tr_dirs):
    propios = [{'id': 'x', 'name': 'Mio', 'color': '#fff', 'order': 0}]
    assert bs.save_buckets(propios) is True
    assert bs.get_buckets() == propios


def test_el_fallback_pendiente_de_get_first_bucket_id_es_codigo_muerto(tr_dirs):
    """get_buckets() nunca devuelve lista vacia (siempre resiembra), asi que
    la rama `else 'pendiente'` es inalcanzable. Queda anotado para quien la
    lea y crea que 'pendiente' es un bucket real."""
    assert bs.get_first_bucket_id() != 'pendiente'
