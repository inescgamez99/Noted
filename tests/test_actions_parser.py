"""Parseo de las acciones que devuelve el LLM en las minutas.

Es donde un test aporta mas de todo el repo: la entrada la escribe un modelo,
asi que es impredecible por definicion. Un fallo aqui no rompe la app de forma
visible, hace algo peor — las acciones de la reunion desaparecen del panel sin
que nadie se entere.

Todo el modulo es stdlib puro: cero mocks.
"""
import pytest

from actions_parser import (
    _is_same_task,
    _key_words,
    merge_actions,
    parse_actions,
    parse_table_actions,
)

pytestmark = pytest.mark.unit


# ── Bloques de instruccion para Claude ────────────────────────────────────

def test_un_bloque_de_instruccion_se_parsea():
    md = """## Acciones

~~~instruction-for-claude
Actualizar el deck de ventas con los numeros de Q4
~~~
"""
    acciones = parse_actions(md)
    assert len(acciones) == 1
    assert acciones[0].type == 'instruction'
    assert 'deck de ventas' in acciones[0].prompt


def test_el_titulo_es_la_primera_linea_del_bloque():
    md = "~~~instruction-for-claude\nPrimera linea\nsegunda linea\n~~~"
    assert parse_actions(md)[0].title == 'Primera linea'


def test_el_titulo_se_corta_a_100_caracteres():
    md = f"~~~instruction-for-claude\n{'A' * 300}\n~~~"
    assert len(parse_actions(md)[0].title) == 100


def test_un_bloque_vacio_no_revienta():
    """El LLM a veces abre el bloque y no escribe nada."""
    md = "~~~instruction-for-claude\n~~~"
    acciones = parse_actions(md)
    assert len(acciones) == 1
    assert acciones[0].title == ''


def test_sin_bloques_no_hay_acciones():
    assert parse_actions('## Resumen\n\nNo se acordo nada.') == []


def test_varios_bloques_se_indexan_en_orden():
    md = ("~~~instruction-for-claude\nUno\n~~~\n"
          "~~~instruction-for-claude\nDos\n~~~\n")
    assert [a.index for a in parse_actions(md)] == [0, 1]


# ── Bloques de cambio de documento ────────────────────────────────────────

def test_un_document_change_extrae_sus_tres_campos():
    md = """~~~document-change
ARCHIVO: propuesta.docx
CONTEXTO: el cliente pidio recortar el alcance
INSTRUCCION: quitar la fase 3 del plan
~~~"""
    a = parse_actions(md)[0]
    assert a.type == 'document_change'
    assert a.archivo == 'propuesta.docx'
    assert a.context == 'el cliente pidio recortar el alcance'
    assert a.prompt == 'quitar la fase 3 del plan'


def test_un_document_change_sin_instruccion_usa_el_cuerpo_entero():
    """Degradacion elegante: mejor una accion con el texto crudo que ninguna."""
    md = "~~~document-change\nARCHIVO: x.docx\nhaz algo con esto\n~~~"
    a = parse_actions(md)[0]
    assert 'haz algo con esto' in a.prompt


def test_los_campos_admiten_el_prefijo_de_comentario():
    """El LLM a veces los escribe como comentarios: '// ARCHIVO: x'."""
    md = "~~~document-change\n// ARCHIVO: x.docx\n// INSTRUCCION: cambiar el titulo\n~~~"
    a = parse_actions(md)[0]
    assert a.archivo == 'x.docx'
    assert a.prompt == 'cambiar el titulo'


# ── Bloques de codigo ────────────────────────────────────────────────────

def test_un_bloque_de_codigo_separa_instruccion_y_codigo():
    md = """~~~python
// INSTRUCCION PARA CLAUDE CODE: anadir el endpoint de salud
// ARCHIVO: api.py
def health():
    return {'ok': True}
~~~"""
    a = parse_actions(md)[0]
    assert a.type == 'code_change'
    assert a.title == 'anadir el endpoint de salud'
    assert a.archivo == 'api.py'
    assert 'def health' in a.prompt
    assert 'Código de referencia' in a.prompt


def test_la_instruccion_de_codigo_es_case_insensitive():
    md = "~~~py\n// instruccion para claude code: hacer algo\n~~~"
    assert len(parse_actions(md)) == 1


def test_un_bloque_de_codigo_sin_codigo_no_anade_la_seccion_de_referencia():
    md = "~~~\n// INSTRUCCION PARA CLAUDE CODE: solo la instruccion\n~~~"
    a = parse_actions(md)[0]
    assert 'Código de referencia' not in a.prompt


# ── Sugerencia de proyecto ───────────────────────────────────────────────

def test_sugiere_el_proyecto_cuyo_nombre_aparece_en_el_texto(tmp_path):
    (tmp_path / 'contoso').mkdir()
    (tmp_path / 'otro-proyecto').mkdir()

    md = "~~~instruction-for-claude\nActualizar el plan de Contoso\n~~~"
    assert parse_actions(md, projects_dir=tmp_path)[0].suggested_project == 'contoso'


def test_sin_directorio_de_proyectos_no_sugiere_nada():
    md = "~~~instruction-for-claude\nAlgo de contoso\n~~~"
    assert parse_actions(md)[0].suggested_project is None


def test_un_directorio_de_proyectos_inexistente_no_revienta(tmp_path):
    md = "~~~instruction-for-claude\nAlgo\n~~~"
    assert parse_actions(md, projects_dir=tmp_path / 'no_existe')[0].suggested_project is None


# ── Tabla de acciones humanas ────────────────────────────────────────────

TABLA = """## Resumen

Se hablo de cosas.

## Acciones Pendientes

| Accion | Owner | Deadline |
|---|---|---|
| Enviar la propuesta | Felipe | 20/09 |
| Revisar el presupuesto | Ines | - |
| Cerrar el contrato | | |

## Siguiente seccion
"""


def test_extrae_las_filas_de_la_tabla():
    acciones = parse_table_actions(TABLA)
    assert [a.title for a in acciones] == ['Enviar la propuesta',
                                           'Revisar el presupuesto',
                                           'Cerrar el contrato']


def test_las_filas_de_la_tabla_son_de_tipo_human():
    assert all(a.type == 'human' for a in parse_table_actions(TABLA))


def test_extrae_responsable_y_fecha():
    a = parse_table_actions(TABLA)[0]
    assert a.assignee == 'Felipe'
    assert a.deadline == '20/09'


@pytest.mark.parametrize('vacio', ['-', '—', ''])
def test_un_guion_en_una_celda_cuenta_como_vacio(vacio):
    """El LLM rellena las celdas sin dato con guiones de varios tipos."""
    md = f"## Acciones\n\n| A | B | C |\n|---|---|---|\n| Enviar la propuesta | {vacio} | {vacio} |\n"
    a = parse_table_actions(md)[0]
    assert a.assignee is None
    assert a.deadline is None


def test_la_fila_de_cabecera_no_se_cuela_como_accion():
    assert 'Accion' not in [a.title for a in parse_table_actions(TABLA)]


@pytest.mark.parametrize('cabecera', ['Acción', 'Action', 'Tarea', 'Task',
                                      'Descripción', 'Description'])
def test_las_cabeceras_conocidas_se_descartan(cabecera):
    md = f"## Acciones\n\n|---|---|\n| {cabecera} | Owner |\n| Enviar la propuesta | Felipe |\n"
    assert [a.title for a in parse_table_actions(md)] == ['Enviar la propuesta']


def test_la_seccion_corta_en_la_siguiente_cabecera():
    """Sin el corte, una tabla de otra seccion se colaria como acciones."""
    md = TABLA + "\n| Esto no es una accion | X | Y |\n"
    assert 'Esto no es una accion' not in [a.title for a in parse_table_actions(md)]


@pytest.mark.parametrize('titulo_seccion', [
    'Acciones Pendientes', 'Pending Actions', 'Acciones y Próximos Pasos',
    'Actions & Next Steps', 'Acciones', 'Actions',
])
def test_reconoce_los_titulos_de_seccion_en_los_dos_idiomas(titulo_seccion):
    md = f"## {titulo_seccion}\n\n|---|---|\n| Enviar la propuesta | Felipe |\n"
    assert len(parse_table_actions(md)) == 1


def test_sin_seccion_de_acciones_no_hay_acciones():
    assert parse_table_actions('## Resumen\n\nNada.') == []


def test_una_tabla_sin_separador_no_produce_acciones():
    """Se exige la linea de guiones para saber donde acaba la cabecera. Sin
    ella, la propia cabecera se colaria como tarea."""
    md = "## Acciones\n\n| Accion | Owner |\n| Enviar la propuesta | Felipe |\n"
    assert parse_table_actions(md) == []


def test_el_indice_de_inicio_se_respeta():
    """Las acciones de tabla se numeran DESPUES de las de Claude."""
    acciones = parse_table_actions(TABLA, start_index=5)
    assert [a.index for a in acciones] == [5, 6, 7]


# ── Fusion de acciones duplicadas ────────────────────────────────────────

def test_key_words_descarta_articulos_y_palabras_cortas():
    palabras = _key_words('Enviar la propuesta de precios a el cliente')
    assert 'enviar' in palabras
    assert 'propuesta' in palabras
    assert 'la' not in palabras
    assert 'de' not in palabras


def test_key_words_admite_acentos_y_enes():
    assert _key_words('Diseño') == {'diseño'}


@pytest.mark.parametrize('humana, titulo_claude, es_la_misma', [
    ('Actualizar el deck de ventas', 'Actualizar deck ventas', True),
    ('Enviar la propuesta', 'Cerrar el contrato', False),
    ('', 'cualquier cosa', False),
])
def test_is_same_task(humana, titulo_claude, es_la_misma):
    assert _is_same_task(humana, titulo_claude, '') is es_la_misma


def test_al_fusionar_la_accion_de_claude_toma_el_titulo_legible():
    """El bloque de Claude tiene un prompt tecnico; la tabla tiene el titulo
    que el usuario entiende. En el panel debe verse el segundo."""
    claude = parse_actions("~~~instruction-for-claude\nactualizar deck ventas Q4\n~~~")
    humanas = parse_table_actions(
        "## Acciones\n\n|---|---|---|\n| Actualizar el deck de ventas | Felipe | 20/09 |\n")

    fusionadas = merge_actions(claude, humanas)

    assert len(fusionadas) == 1, "la fila deberia haberse absorbido"
    assert fusionadas[0].title == 'Actualizar el deck de ventas'
    assert fusionadas[0].assignee == 'Felipe'
    assert fusionadas[0].deadline == '20/09'


def test_una_fila_sin_equivalente_sobrevive_como_accion_humana():
    claude = parse_actions("~~~instruction-for-claude\nactualizar deck ventas\n~~~")
    humanas = parse_table_actions(
        "## Acciones\n\n|---|---|\n| Contratar a un becario | RRHH |\n")

    fusionadas = merge_actions(claude, humanas)

    assert len(fusionadas) == 2
    assert any(a.type == 'human' and a.title == 'Contratar a un becario'
               for a in fusionadas)


def test_una_fila_solo_se_absorbe_una_vez():
    """Con dos bloques de Claude parecidos, la fila no puede duplicarse."""
    claude = parse_actions("~~~instruction-for-claude\nactualizar deck ventas\n~~~\n"
                           "~~~instruction-for-claude\nactualizar deck ventas otra vez\n~~~")
    humanas = parse_table_actions(
        "## Acciones\n\n|---|---|\n| Actualizar el deck de ventas | Felipe |\n")

    fusionadas = merge_actions(claude, humanas)

    assert len(fusionadas) == 2, "los 2 bloques de Claude, y la fila absorbida"
    con_ese_titulo = [a for a in fusionadas if a.title == 'Actualizar el deck de ventas']
    assert len(con_ese_titulo) == 1


def test_fusionar_sin_acciones_humanas_devuelve_las_de_claude():
    claude = parse_actions("~~~instruction-for-claude\nalgo\n~~~")
    assert merge_actions(claude, []) == claude


def test_fusionar_sin_acciones_de_claude_devuelve_las_humanas():
    humanas = parse_table_actions("## Acciones\n\n|---|---|\n| Enviar la propuesta | Felipe |\n")
    assert merge_actions([], humanas) == humanas


def test_el_assignee_de_claude_no_se_pisa_si_ya_tenia_uno():
    claude = parse_actions("~~~instruction-for-claude\nactualizar deck ventas\n~~~")
    claude[0].assignee = 'YaAsignado'
    humanas = parse_table_actions(
        "## Acciones\n\n|---|---|\n| Actualizar el deck de ventas | Felipe |\n")

    merge_actions(claude, humanas)

    assert claude[0].assignee == 'YaAsignado'


# ── Normalizacion del responsable ─────────────────────────────────────────
# El tablero agrupa por la cadena exacta del responsable. Medido sobre las
# minutas reales: 'Otros', '[Otros]', 'Otros (sin concretar)', 'Participant
# (Otros)', 'Equipo', 'TBD', 'Por definir'... eran cada uno su propio grupo.

from actions_parser import normalize_assignee


@pytest.mark.parametrize('raw', [
    None, '', '-', '—', 'Otros', '[Otros]', 'Otros (atribución inferida)',
    'Otros (sin concretar)', 'Participant (Otros)', 'Participant (Otros), name unclear',
    'Equipo', 'Team', 'TBD', 'Por definir', 'Sin concretar', 'Todos',
    "Recorder's team (attribution inferred) (Claude)", '- (Claude)',
    'Presentador/a (Claude)', 'Equipo (sin concretar)',
])
def test_las_etiquetas_genericas_no_son_un_responsable(raw):
    assert normalize_assignee(raw) is None


def test_el_marcador_claude_no_crea_otro_grupo():
    assert normalize_assignee('Ana (Claude)') == 'Ana'


def test_se_conserva_el_nombre_dentro_de_una_etiqueta_generica():
    assert normalize_assignee('Otros (Martin)') == 'Martin'


def test_varios_responsables_se_unen_con_barra_sin_duplicar():
    assert normalize_assignee('Ana / Elisa (Claude)') == 'Ana / Elisa'
    assert normalize_assignee('Ana y Ana') == 'Ana'


def test_las_grafias_del_usuario_convergen_en_la_de_ajustes():
    for raw in ('Ines', 'Inés', 'Ines Campos', 'Inés (Claude)'):
        assert normalize_assignee(raw, me_name='Inés') == 'Inés'


def test_grabador_se_sustituye_por_el_nombre_real():
    assert normalize_assignee('Grabador/a', me_name='Inés') == 'Inés'
    assert normalize_assignee('Grabador/a') is None


def test_la_tabla_no_genera_responsable_otros():
    acciones = parse_table_actions(
        "## Acciones Pendientes\n\n| Accion | Responsable | Fecha |\n|---|---|---|\n"
        "| Enviar el correo | Otros (atribución inferida) | Mañana |\n"
        "| Preparar deck | Ana (Claude) | - |\n")
    assert [a.assignee for a in acciones] == [None, 'Ana']
