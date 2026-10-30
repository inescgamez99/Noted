"""El _actions.json: el fichero del que dependen el panel y el ruteo.

Guarda las acciones de la reunion, cual esta ejecutada, y a que proyecto
pertenece. Lo leen el panel web, el consumidor externo y el archivado por
proyecto, asi que un fallo aqui se nota en tres sitios distintos.
"""
import json
from datetime import datetime, timedelta

import pytest

import actions_enricher as ae
import project_context as pc

pytestmark = pytest.mark.unit

STEM = '20260917_1616_Comite_Semanal'


@pytest.fixture
def minuta(tr_dirs):
    md = pc.MINUTES_DIR / f'{STEM}.md'
    md.write_text('TITULO: Comite Semanal\n\n## Resumen Ejecutivo\n\nSe hablo.\n',
                  encoding='utf-8')
    return md


def _escribir_acciones(md, acciones, project_id='none'):
    ap = ae.get_actions_path(md)
    ap.write_text(json.dumps({
        'minutes': str(md), 'project_id': project_id, 'actions': acciones,
    }, ensure_ascii=False), encoding='utf-8')
    return ap


# ── Donde vive el fichero ────────────────────────────────────────────────

def test_el_actions_json_es_hermano_de_la_minuta(minuta):
    """Contrato con el consumidor externo: lo localiza por el stem."""
    ap = ae.get_actions_path(minuta)
    assert ap.name == f'{STEM}_actions.json'
    assert ap.parent == minuta.parent


# ── Cargar acciones ──────────────────────────────────────────────────────

def test_sin_fichero_devuelve_none(minuta):
    """None y no [] a proposito: distingue 'no hay acciones' de 'aun no se
    han generado'. El panel pinta cosas distintas."""
    assert ae.load_enriched(minuta) is None


def test_carga_las_acciones_guardadas(minuta):
    _escribir_acciones(minuta, [{'index': 0, 'title': 'Enviar propuesta'}])
    acciones = ae.load_enriched(minuta)
    assert [a['title'] for a in acciones] == ['Enviar propuesta']


def test_un_actions_mas_viejo_que_la_minuta_se_considera_caducado(minuta):
    """Si se regeneran las minutas, las acciones viejas ya no valen: describen
    una version anterior del texto."""
    ap = _escribir_acciones(minuta, [{'index': 0, 'title': 'Vieja'}])

    import os
    viejo = (datetime.now() - timedelta(hours=1)).timestamp()
    os.utime(ap, (viejo, viejo))

    assert ae.load_enriched(minuta) is None


def test_un_actions_corrupto_devuelve_none_y_no_lanza(minuta):
    ae.get_actions_path(minuta).write_text('{ esto no es json', encoding='utf-8')
    assert ae.load_enriched(minuta) is None


def test_un_actions_sin_clave_actions_devuelve_lista_vacia(minuta):
    ae.get_actions_path(minuta).write_text('{"project_id": "none"}', encoding='utf-8')
    assert ae.load_enriched(minuta) == []


# ── Editar el prompt de una accion ───────────────────────────────────────

def test_actualizar_el_prompt_lo_guarda(minuta):
    _escribir_acciones(minuta, [{'index': 0, 'prompt_enriched': 'viejo'}])

    ae.update_action_prompt(minuta, 0, 'nuevo prompt')

    assert ae.load_enriched(minuta)[0]['prompt_enriched'] == 'nuevo prompt'


def test_actualizar_una_accion_no_toca_las_demas(minuta):
    _escribir_acciones(minuta, [{'index': 0, 'prompt_enriched': 'a'},
                                {'index': 1, 'prompt_enriched': 'b'}])

    ae.update_action_prompt(minuta, 0, 'cambiado')

    acciones = ae.load_enriched(minuta)
    assert acciones[0]['prompt_enriched'] == 'cambiado'
    assert acciones[1]['prompt_enriched'] == 'b'


def test_actualizar_un_indice_inexistente_no_lanza(minuta):
    _escribir_acciones(minuta, [{'index': 0, 'prompt_enriched': 'a'}])
    ae.update_action_prompt(minuta, 99, 'x')
    assert ae.load_enriched(minuta)[0]['prompt_enriched'] == 'a'


def test_actualizar_sin_fichero_no_lanza(minuta):
    ae.update_action_prompt(minuta, 0, 'x')     # no debe crear nada ni fallar
    assert not ae.get_actions_path(minuta).exists()


# ── Marcar una accion como ejecutada ─────────────────────────────────────

def test_marcar_ejecutada_guarda_el_flag_y_el_prompt(minuta):
    _escribir_acciones(minuta, [{'index': 0, 'title': 'x'}])

    ae.update_action_executed(minuta, 0, 'lo que se le mando a claude')

    a = ae.load_enriched(minuta)[0]
    assert a['executed'] is True
    assert a['prompt_executed'] == 'lo que se le mando a claude'


def test_el_json_se_guarda_legible_y_con_acentos(minuta):
    """ensure_ascii=False e indent=2: el usuario edita este fichero a mano
    desde el panel, y con los acentos escapados es ilegible."""
    _escribir_acciones(minuta, [{'index': 0, 'title': 'x'}])

    ae.update_action_executed(minuta, 0, 'Revisión del diseño')

    crudo = ae.get_actions_path(minuta).read_text(encoding='utf-8')
    assert 'Revisión del diseño' in crudo
    assert '\\u00f3' not in crudo


# ── Asignar el proyecto ──────────────────────────────────────────────────

def test_asignar_un_proyecto_sin_actions_previo_crea_el_fichero(minuta):
    """Pasa de verdad: el usuario asigna el proyecto desde el panel antes de
    que se hayan generado las acciones."""
    assert ae.set_meeting_project_id(minuta, 'contoso') is True

    datos = json.loads(ae.get_actions_path(minuta).read_text(encoding='utf-8'))
    assert datos['project_id'] == 'contoso'
    assert datos['actions'] == []


def test_asignar_un_proyecto_conserva_las_acciones_existentes(minuta):
    _escribir_acciones(minuta, [{'index': 0, 'title': 'Enviar propuesta'}])

    ae.set_meeting_project_id(minuta, 'contoso')

    acciones = ae.load_enriched(minuta)
    assert acciones is not None and len(acciones) == 1
    assert acciones[0]['title'] == 'Enviar propuesta'


def test_desasignar_guarda_none_como_texto(minuta):
    """El consumidor y el archivado comparan con la cadena 'none', no con
    None: un null de JSON rompe esa comparacion."""
    ae.set_meeting_project_id(minuta, '')

    datos = json.loads(ae.get_actions_path(minuta).read_text(encoding='utf-8'))
    assert datos['project_id'] == 'none'


def test_reasignar_sobrescribe_el_proyecto_anterior(minuta):
    ae.set_meeting_project_id(minuta, 'proyecto-a')
    ae.set_meeting_project_id(minuta, 'proyecto-b')

    datos = json.loads(ae.get_actions_path(minuta).read_text(encoding='utf-8'))
    assert datos['project_id'] == 'proyecto-b'


# ── El proyecto que lee el resto del sistema ─────────────────────────────

def test_meeting_project_id_lee_el_actions(minuta):
    _escribir_acciones(minuta, [], project_id='contoso')
    assert pc.meeting_project_id(minuta) == 'contoso'


def test_sin_actions_el_proyecto_es_none(minuta):
    assert pc.meeting_project_id(minuta) in ('none', '')


def test_un_actions_corrupto_no_revienta_el_ruteo(minuta):
    ae.get_actions_path(minuta).write_text('{roto', encoding='utf-8')
    assert pc.meeting_project_id(minuta) in ('none', '')


# ── Idioma de las minutas ────────────────────────────────────────────────

def test_detecta_espanol_por_las_cabeceras():
    texto = '## Resumen Ejecutivo\n## Asistentes\n## Acciones Pendientes'
    assert ae._detect_language(texto) == 'es'


def test_detecta_ingles_por_las_cabeceras():
    texto = '## Executive Summary\n## Attendees\n## Pending Actions'
    assert ae._detect_language(texto) == 'en'


def test_sin_cabeceras_reconocibles_cae_a_INGLES():
    """INCONSISTENCIA DE IDIOMA, la tercera del repo. Con cero marcadores los
    dos scores son 0 y la comparacion es `en_score >= es_score`, asi que gana
    el ingles. Para un usuario que trabaja en espanol, unas minutas con
    cabeceras raras hacen que las acciones se enriquezcan en ingles.

    Los tres fallbacks que conviven:
      config.get_ui_language()        -> 'es'
      tray_app._STR.get(..., ['en'])  -> 'en'
      actions_enricher._detect_language -> 'en'
    """
    assert ae._detect_language('texto suelto sin estructura') == 'en'


def test_con_empate_gana_el_ingles():
    texto = '## Executive Summary\n## Resumen Ejecutivo'
    assert ae._detect_language(texto) == 'en'


# ── Titulo y fecha para el resumen archivado ─────────────────────────────

def test_el_titulo_sale_de_la_linea_TITULO(minuta):
    titulo, fecha = pc._title_and_date(minuta, minuta.read_text(encoding='utf-8'))
    assert titulo == 'Comite Semanal'
    assert fecha == '2026-09-17'


def test_sin_linea_TITULO_el_titulo_sale_del_stem(tr_dirs):
    md = pc.MINUTES_DIR / '20260917_1616_Sin_Titulo_Dentro.md'
    md.write_text('## Resumen\n\ncuerpo', encoding='utf-8')

    titulo, fecha = pc._title_and_date(md, md.read_text(encoding='utf-8'))

    assert 'Sin Titulo Dentro' in titulo
    assert fecha == '2026-09-17'


def test_un_stem_sin_fecha_no_revienta(tr_dirs):
    md = pc.MINUTES_DIR / 'suelta.md'
    md.write_text('cuerpo', encoding='utf-8')
    titulo, fecha = pc._title_and_date(md, 'cuerpo')
    assert isinstance(titulo, str) and isinstance(fecha, str)


# ── El resumen que se archiva en el proyecto ─────────────────────────────

def test_el_resumen_extrae_la_seccion_ejecutiva():
    md = ('## Resumen Ejecutivo\n\nLo importante de la reunion.\n\n'
          '## Temas Tratados\n\nDetalle largo que no hace falta.\n')
    resumen = pc._extract_summary(md)

    assert 'Lo importante' in resumen
    assert 'Detalle largo' not in resumen


def test_sin_seccion_ejecutiva_el_resumen_no_esta_vacio():
    """Degradacion elegante: la memoria del proyecto prefiere algo a nada."""
    resumen = pc._extract_summary('## Temas Tratados\n\nSe hablo de cosas.\n')
    assert resumen.strip() != ''


def test_load_projects_sin_fichero_devuelve_lista_vacia(tr_dirs):
    assert pc.load_projects() == []


def test_load_projects_con_json_corrupto_devuelve_lista_vacia(tr_dirs):
    (tr_dirs / 'projects.json').write_text('{roto', encoding='utf-8')
    assert pc.load_projects() == []


def test_project_docs_dir_cuelga_de_project_docs(tr_dirs):
    d = pc.project_docs_dir('contoso')
    assert d.name == 'contoso'
    assert d.parent.name == 'project_docs'
