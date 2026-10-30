"""La API que el panel web llama por pywebview.

No se abre ninguna ventana: `import app_window` no toca pywebview (el import
de webview es perezoso) y la clase API se instancia con object.__new__.

Se cubre lo que tiene consecuencias sobre datos del usuario, no el pegamento
que solo lanza subprocess.Popen o dialogos nativos.
"""
import json

import pytest

import app_window as aw

pytestmark = pytest.mark.unit


@pytest.fixture
def api():
    return object.__new__(aw.AppAPI)


# ── Parseo del stem de la minuta ─────────────────────────────────────────

def test_parse_stem_extrae_titulo_fecha_y_hora():
    assert aw._parse_stem('20260917_1614_Comite_Semanal') == {
        'title': 'Comite Semanal', 'date': '2026-09-17', 'time': '16:14',
    }


def test_parse_stem_con_un_stem_no_conforme_usa_el_stem_como_titulo():
    assert aw._parse_stem('suelta') == {'title': 'suelta', 'date': '', 'time': ''}


def test_parse_stem_sin_titulo_cae_al_stem_completo():
    """'20260917_1614_' deja el slug vacio: la rama `title or stem`."""
    r = aw._parse_stem('20260917_1614_')
    assert r['title'] == '20260917_1614_'
    assert r['date'] == '2026-09-17'


# ── Clave de pin ─────────────────────────────────────────────────────────

def test_la_clave_de_pin_es_la_fecha_y_hora():
    """Asi renombrar la reunion NO pierde el pin: es la razon de ser de esta
    funcion."""
    assert aw._pin_key('20260917_1614_Nombre_Viejo') == '20260917_1614'
    assert aw._pin_key('20260917_1614_Nombre_Nuevo') == '20260917_1614'


def test_la_clave_de_pin_se_busca_en_cualquier_posicion():
    assert aw._pin_key('prefijo_20260917_1614_algo') == '20260917_1614'


def test_sin_fecha_la_clave_de_pin_es_el_stem_entero():
    assert aw._pin_key('sin_fecha') == 'sin_fecha'


def test_fijar_y_quitar_un_pin_devuelve_el_estado_nuevo(api, tr_dirs):
    ruta = str(aw.MINUTES_DIR / '20260917_1614_Comite.md')

    assert api.toggle_pin(ruta) is True
    assert api.toggle_pin(ruta) is False


def test_los_pins_se_guardan_ordenados(api, tr_dirs):
    """Fichero estable: dos ejecuciones con los mismos pins dan el mismo
    contenido, y el diff de git no cambia sin motivo."""
    for stem in ('20260917_1614_B', '20260101_0900_A'):
        api.toggle_pin(str(aw.MINUTES_DIR / f'{stem}.md'))

    guardados = json.loads((tr_dirs / 'pins.json').read_text(encoding='utf-8'))
    assert guardados == sorted(guardados)


def test_dos_toggles_dejan_el_fichero_como_estaba(api, tr_dirs):
    ruta = str(aw.MINUTES_DIR / '20260917_1614_Comite.md')
    api.toggle_pin(ruta)
    api.toggle_pin(ruta)
    assert json.loads((tr_dirs / 'pins.json').read_text(encoding='utf-8')) == []


def test_un_pins_json_corrupto_no_pierde_el_panel(api, tr_dirs):
    (tr_dirs / 'pins.json').write_text('{roto', encoding='utf-8')
    assert api._load_pins() == set()


# ── Limpieza de ejecuciones en memoria ───────────────────────────────────

def test_por_debajo_del_limite_no_se_borra_nada():
    d = {f'r{i}': {'done': True} for i in range(5)}
    aw._prune_runs(d)
    assert len(d) == 5


def test_por_encima_del_limite_se_borran_las_completadas():
    d = {f'r{i}': {'done': True} for i in range(aw._MAX_RUNS + 10)}
    aw._prune_runs(d)
    assert len(d) == aw._MAX_RUNS


def test_una_ejecucion_en_curso_nunca_se_borra():
    """Si se borrara, el panel perderia el progreso de algo que sigue
    corriendo y no habria forma de recuperarlo."""
    d = {f'r{i}': {'done': True} for i in range(aw._MAX_RUNS + 10)}
    d['en_curso'] = {'done': False}

    aw._prune_runs(d)

    assert 'en_curso' in d


# ── Titulo tomado del contenido ──────────────────────────────────────────

def test_el_titulo_se_lee_de_la_linea_TITULO(tr_dirs):
    md = aw.MINUTES_DIR / '20260917_1614_Nombre_De_Fichero.md'
    md.write_text('TITULO: Titulo De Dentro\n\ncuerpo', encoding='utf-8')

    assert aw.AppAPI._title_from_content(md) == 'Titulo De Dentro'


def test_solo_se_miran_las_primeras_lineas(tr_dirs):
    md = aw.MINUTES_DIR / 'x.md'
    md.write_text('a\nb\nc\nTITULO: Demasiado Abajo\n', encoding='utf-8')
    assert aw.AppAPI._title_from_content(md) == ''


def test_un_fichero_inexistente_devuelve_cadena_vacia(tr_dirs):
    assert aw.AppAPI._title_from_content(aw.MINUTES_DIR / 'no_existe.md') == ''


# ── Idioma de las notas ──────────────────────────────────────────────────

def test_detecta_espanol_por_las_palabras_de_las_minutas(tr_dirs):
    md = aw.MINUTES_DIR / 'x.md'
    md.write_text('## Resumen Ejecutivo\n\nAsistentes: varios.\n', encoding='utf-8')
    assert aw._detect_notes_language(md) == 'es'


def test_el_idioma_por_defecto_de_las_notas_es_espanol(tr_dirs):
    md = aw.MINUTES_DIR / 'x.md'
    md.write_text('something completely unrelated\n', encoding='utf-8')
    assert aw._detect_notes_language(md) == 'es'


def test_un_fichero_inexistente_tambien_cae_a_espanol(tr_dirs):
    assert aw._detect_notes_language(aw.MINUTES_DIR / 'no.md') == 'es'


# ── Ajustes: semantica de fusion ─────────────────────────────────────────

def test_sin_fichero_los_ajustes_traen_el_idioma_por_defecto(api, tr_dirs):
    assert api.get_settings() == {'language': 'es'}


def test_un_settings_corrupto_no_deja_el_panel_sin_ajustes(api, tr_dirs):
    (tr_dirs / 'settings.json').write_text('{roto', encoding='utf-8')
    assert api.get_settings() == {'language': 'es'}


def test_guardar_ajustes_FUSIONA_y_no_reemplaza(api, tr_dirs):
    """EL INVARIANTE MAS VALIOSO DEL MODULO. El panel manda solo la clave que
    se acaba de cambiar; si esto reemplazara, guardar el idioma borraria el
    modelo de Whisper, el directorio de salida y todo lo demas."""
    (tr_dirs / 'settings.json').write_text(json.dumps({
        'language': 'es', 'whisper_model': 'medium', 'output_dir': 'D:/salida',
    }), encoding='utf-8')

    assert api.save_settings({'language': 'en'}) is True

    guardados = json.loads((tr_dirs / 'settings.json').read_text(encoding='utf-8'))
    assert guardados['language'] == 'en'
    assert guardados['whisper_model'] == 'medium'
    assert guardados['output_dir'] == 'D:/salida'


def test_guardar_ajustes_sin_fichero_previo_lo_crea(api, tr_dirs):
    assert api.save_settings({'language': 'en'}) is True
    assert json.loads((tr_dirs / 'settings.json').read_text(encoding='utf-8')) == {
        'language': 'en'}


def test_los_ajustes_se_guardan_legibles(api, tr_dirs):
    """El usuario los edita a mano: indent y acentos sin escapar."""
    api.save_settings({'user_name': 'Felipe Antúnez'})
    crudo = (tr_dirs / 'settings.json').read_text(encoding='utf-8')
    assert 'Felipe Antúnez' in crudo
    assert '\n' in crudo


# ── Proyectos ────────────────────────────────────────────────────────────

def test_sin_projects_json_la_lista_esta_vacia(api, tr_dirs):
    assert api.get_projects() == []


def test_un_projects_json_corrupto_devuelve_lista_vacia(api, tr_dirs):
    (tr_dirs / 'projects.json').write_text('{roto', encoding='utf-8')
    assert api.get_projects() == []


@pytest.fixture
def sin_deteccion(monkeypatch):
    """Crear un proyecto dispara detect_projects_for_all, que lanza un hilo
    con el CLI de claude: en un test no debe ocurrir."""
    monkeypatch.setattr(aw.AppAPI, 'detect_projects_for_all', lambda self: None)


def test_crear_un_proyecto_genera_un_id_desde_el_nombre(api, tr_dirs, sin_deteccion):
    api.save_project({'name': 'Contoso Agentic AI'})
    assert [p['id'] for p in api.get_projects()] == ['contoso-agentic-ai']


def test_dos_proyectos_con_el_mismo_nombre_no_colisionan(api, tr_dirs, sin_deteccion):
    api.save_project({'name': 'Contoso'})
    api.save_project({'name': 'Contoso'})

    ids = [p['id'] for p in api.get_projects()]
    assert ids == ['contoso', 'contoso-2']


def test_guardar_con_un_id_existente_actualiza_en_vez_de_duplicar(api, tr_dirs,
                                                                  sin_deteccion):
    api.save_project({'name': 'Contoso'})
    api.save_project({'id': 'contoso', 'name': 'Contoso Fase 2'})

    proyectos = api.get_projects()
    assert len(proyectos) == 1
    assert proyectos[0]['name'] == 'Contoso Fase 2'


def test_borrar_un_proyecto_inexistente_no_toca_el_fichero(api, tr_dirs, sin_deteccion):
    api.save_project({'name': 'Contoso'})
    antes = (tr_dirs / 'projects.json').read_bytes()

    api.delete_project('no-existe')

    assert (tr_dirs / 'projects.json').read_bytes() == antes


# ── Navegacion pedida desde fuera ────────────────────────────────────────

def test_la_peticion_de_navegacion_se_consume_una_sola_vez(api, tr_dirs):
    """El daemon escribe el fichero para que el panel abra una reunion. Si no
    se consumiera, el panel volveria a saltar a esa reunion en cada refresco."""
    (tr_dirs / '.app_navigate.txt').write_text('  /ruta/a/minuta.md  ', encoding='utf-8')

    assert api.get_navigate_request() == '/ruta/a/minuta.md'
    assert api.get_navigate_request() == ''


def test_sin_peticion_de_navegacion_devuelve_cadena_vacia(api, tr_dirs):
    assert api.get_navigate_request() == ''


# ── Listado de reuniones ─────────────────────────────────────────────────

@pytest.fixture
def dos_reuniones(tr_dirs):
    a = aw.MINUTES_DIR / '20260917_1614_Nombre_Del_Fichero.md'
    a.write_text('TITULO: Titulo De Dentro\n\n## Resumen\n\ncuerpo', encoding='utf-8')
    b = aw.MINUTES_DIR / '20260101_0900_Sin_Titulo_Dentro.md'
    b.write_text('## Resumen\n\ncuerpo', encoding='utf-8')
    return a, b


def test_el_titulo_de_dentro_gana_al_del_nombre_de_fichero(api, dos_reuniones):
    """Renombrar una reunion desde el panel escribe TITULO: en el contenido y
    NO renombra el fichero (para no perder pins ni romper enlaces). Si el
    listado ignorara ese titulo, el panel seguiria mostrando el nombre viejo."""
    reuniones = {m['title'] for m in api.get_meetings()}
    assert 'Titulo De Dentro' in reuniones


def test_sin_titulo_dentro_se_usa_el_del_nombre_de_fichero(api, dos_reuniones):
    reuniones = {m['title'] for m in api.get_meetings()}
    assert 'Sin Titulo Dentro' in reuniones


def test_las_reuniones_vienen_de_mas_nueva_a_mas_vieja(api, dos_reuniones):
    import os
    import time
    a, b = dos_reuniones
    os.utime(b, (time.time() - 10_000,) * 2)

    titulos = [m['title'] for m in api.get_meetings()]
    assert titulos.index('Titulo De Dentro') < titulos.index('Sin Titulo Dentro')


def test_una_reunion_sin_acciones_se_marca_como_tal(api, dos_reuniones):
    for m in api.get_meetings():
        assert m['has_actions'] is False


def test_las_acciones_pendientes_se_cuentan(api, dos_reuniones):
    a, _ = dos_reuniones
    (a.parent / f'{a.stem}_actions.json').write_text(json.dumps({
        'project_id': 'none',
        'actions': [{'index': 0, 'executed': True}, {'index': 1}, {'index': 2}],
    }), encoding='utf-8')

    reunion = next(m for m in api.get_meetings() if m['title'] == 'Titulo De Dentro')

    assert reunion['has_actions'] is True
    assert reunion['pending_count'] == 2, "solo las no ejecutadas"


def test_sin_minutas_el_listado_esta_vacio(api, tr_dirs):
    assert api.get_meetings() == []
