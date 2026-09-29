"""Exportacion de la reunion a la carpeta del proyecto en disco.

Copia el transcript, el HTML de notas y el HTML de email a la carpeta que el
usuario configuro para ese proyecto. Las preferencias son POR PROYECTO, asi que
un fallo aqui escribe ficheros donde no toca, o deja de escribirlos sin avisar.

No se toca la rama PDF: lanza msedge.exe --headless y depende de rutas de
Program Files. Queda fuera a proposito (su default es False).
"""
import json

import pytest

import project_exporter as pe

pytestmark = pytest.mark.unit

STEM = '20260917_1616_Comite_Semanal'


@pytest.fixture(autouse=True)
def _blank_me_name(monkeypatch):
    """Aísla los tests de ME_NAME real del usuario: sin prefijo en el slug."""
    import config
    monkeypatch.setattr(config, 'ME_NAME', '', raising=False)


@pytest.fixture
def reunion(tr_dirs):
    """Una reunion con sus artefactos y un proyecto con carpeta configurada."""
    destino = tr_dirs / 'carpeta_del_proyecto'
    destino.mkdir()

    md = tr_dirs / 'minutes' / f'{STEM}.md'
    md.write_text('TITULO: Comite Semanal\n\n## Resumen\n\nSe hablo.\n', encoding='utf-8')
    md.with_suffix('.html').write_text('<html>notas</html>', encoding='utf-8')
    (md.parent / f'{STEM}_transcript.txt').write_text('texto transcrito',
                                                       encoding='utf-8')

    def configurar(**extra):
        proyecto = {'id': 'aramco', 'name': 'Aramco', 'directory': str(destino)}
        proyecto.update(extra)
        (tr_dirs / 'projects.json').write_text(
            json.dumps({'projects': [proyecto]}), encoding='utf-8')
        (md.parent / f'{STEM}_actions.json').write_text(
            json.dumps({'project_id': 'aramco', 'actions': []}), encoding='utf-8')

    return {'md': md, 'destino': destino, 'configurar': configurar}


# ── Localizar el proyecto de la reunion ──────────────────────────────────

def test_sin_actions_json_no_hay_proyecto(reunion):
    assert pe.get_meeting_project(reunion['md']) is None


def test_devuelve_el_proyecto_completo(reunion):
    reunion['configurar']()
    proyecto = pe.get_meeting_project(reunion['md'])
    assert proyecto['id'] == 'aramco'
    assert proyecto['directory'] == str(reunion['destino'])


@pytest.mark.parametrize('contenido', [
    '{roto',                                  # JSON corrupto
    '{"actions": []}',                        # sin project_id
    '{"project_id": "none"}',                 # desasignada
    '{"project_id": "inexistente"}',          # id que no esta en projects.json
])
def test_las_cuatro_rutas_a_none(reunion, tr_dirs, contenido):
    (tr_dirs / 'projects.json').write_text(
        json.dumps({'projects': [{'id': 'aramco', 'name': 'A'}]}), encoding='utf-8')
    (reunion['md'].parent / f'{STEM}_actions.json').write_text(contenido,
                                                                encoding='utf-8')
    assert pe.get_meeting_project(reunion['md']) is None


# ── Exportar ─────────────────────────────────────────────────────────────

def test_sin_proyecto_no_exporta(reunion):
    assert pe.export_to_project_folder(reunion['md']) is False


def test_sin_carpeta_configurada_no_exporta(reunion, tr_dirs):
    reunion['configurar'](directory='')
    assert pe.export_to_project_folder(reunion['md']) is False


def test_con_una_carpeta_en_blanco_no_exporta(reunion):
    reunion['configurar'](directory='   ')
    assert pe.export_to_project_folder(reunion['md']) is False


def test_si_la_carpeta_no_existe_no_se_crea(reunion, tr_dirs):
    """Crearla a ciegas dejaria carpetas sueltas por el disco si el usuario se
    equivoco al escribir la ruta."""
    inexistente = tr_dirs / 'no' / 'existe'
    reunion['configurar'](directory=str(inexistente))

    assert pe.export_to_project_folder(reunion['md']) is False
    assert not inexistente.exists()


def test_exporta_las_tres_carpetas_por_defecto(reunion):
    """Los defaults son True para transcript, html y email; False para el PDF."""
    reunion['configurar']()

    assert pe.export_to_project_folder(reunion['md']) is True

    destino = reunion['destino']
    assert (destino / 'Transcripts' / f'{STEM}_transcript.txt').exists()
    assert (destino / 'Notas' / f'{STEM}.html').exists()
    assert (destino / 'Email' / f'{STEM}_email.html').exists()


def test_el_transcript_se_lee_del_hermano_si_no_se_pasa(reunion):
    reunion['configurar']()
    pe.export_to_project_folder(reunion['md'])

    guardado = (reunion['destino'] / 'Transcripts' / f'{STEM}_transcript.txt')
    assert guardado.read_text(encoding='utf-8') == 'texto transcrito'


def test_el_transcript_pasado_por_argumento_gana(reunion):
    reunion['configurar']()
    pe.export_to_project_folder(reunion['md'], transcript_txt='texto de memoria')

    guardado = (reunion['destino'] / 'Transcripts' / f'{STEM}_transcript.txt')
    assert guardado.read_text(encoding='utf-8') == 'texto de memoria'


@pytest.mark.parametrize('flag, carpeta', [
    ('export_save_transcript', 'Transcripts'),
    ('export_save_html', 'Notas'),
    ('export_save_email', 'Email'),
])
def test_desactivar_una_preferencia_no_crea_su_carpeta(reunion, flag, carpeta):
    """Son preferencias POR PROYECTO: un proyecto puede querer solo el email."""
    reunion['configurar'](**{flag: False})

    pe.export_to_project_folder(reunion['md'])

    assert not (reunion['destino'] / carpeta).exists()


def test_el_pdf_esta_desactivado_por_defecto(reunion):
    """Generar el PDF lanza msedge --headless: no debe pasar sin pedirlo."""
    reunion['configurar']()
    pe.export_to_project_folder(reunion['md'])
    assert not list(reunion['destino'].rglob('*.pdf'))


def test_un_html_de_notas_ausente_no_aborta_la_exportacion(reunion):
    """El resto de artefactos debe salir igual: media exportacion es mejor que
    ninguna."""
    reunion['configurar']()
    reunion['md'].with_suffix('.html').unlink()

    assert pe.export_to_project_folder(reunion['md']) is True
    assert (reunion['destino'] / 'Transcripts').exists()
    assert (reunion['destino'] / 'Email').exists()


# ── El HTML del email ────────────────────────────────────────────────────

def test_el_email_lleva_el_titulo_de_la_minuta(reunion):
    reunion['configurar']()
    pe.export_to_project_folder(reunion['md'])

    html = (reunion['destino'] / 'Email' / f'{STEM}_email.html').read_text(
        encoding='utf-8')
    assert 'Comite Semanal' in html


def test_la_linea_TITULO_no_aparece_en_el_cuerpo_del_email(reunion):
    reunion['configurar']()
    pe.export_to_project_folder(reunion['md'])

    html = (reunion['destino'] / 'Email' / f'{STEM}_email.html').read_text(
        encoding='utf-8')
    assert 'TITULO:' not in html


def test_los_bloques_de_acciones_para_claude_se_quitan_del_email(reunion):
    """Un email al cliente no debe llevar instrucciones tecnicas internas."""
    reunion['md'].write_text(
        'TITULO: Comite\n\n## Resumen\n\nTexto.\n\n'
        '~~~instruction-for-claude\nactualizar el deck interno\n~~~\n',
        encoding='utf-8')
    reunion['configurar']()

    pe.export_to_project_folder(reunion['md'])

    html = (reunion['destino'] / 'Email' / f'{STEM}_email.html').read_text(
        encoding='utf-8')
    assert 'actualizar el deck interno' not in html


def test_la_fecha_del_email_sale_del_stem(reunion):
    reunion['configurar']()
    pe.export_to_project_folder(reunion['md'])

    html = (reunion['destino'] / 'Email' / f'{STEM}_email.html').read_text(
        encoding='utf-8')
    assert '17/09/2026' in html


def test_un_stem_sin_fecha_no_rompe_el_email(reunion, tr_dirs):
    suelta = tr_dirs / 'minutes' / 'suelta.md'
    suelta.write_text('TITULO: Suelta\n\ncuerpo', encoding='utf-8')
    (tr_dirs / 'projects.json').write_text(json.dumps({'projects': [
        {'id': 'aramco', 'name': 'A', 'directory': str(reunion['destino'])}]}),
        encoding='utf-8')
    (tr_dirs / 'minutes' / 'suelta_actions.json').write_text(
        json.dumps({'project_id': 'aramco', 'actions': []}), encoding='utf-8')

    assert pe.export_to_project_folder(suelta) is True


# ── Conversion de markdown, con y sin la libreria ────────────────────────

def test_con_la_libreria_markdown_los_tags_llevan_estilos_inline():
    if not pe._HAS_MD:
        pytest.skip('la libreria markdown no esta instalada')

    html = pe._md_to_email_html('## Titulo\n\ntexto\n\n- uno\n')

    assert 'style=' in html
    assert '<h2 ' in html


def test_sin_la_libreria_el_fallback_sigue_produciendo_html(monkeypatch):
    """Degradacion elegante: markdown es opcional, y sin el el email no puede
    llegar en texto crudo."""
    monkeypatch.setattr(pe, '_HAS_MD', False)

    html = pe._md_to_email_html('## Titulo\n\n- uno\n- dos\n')

    assert '<h2' in html
    assert html.count('<li') == 2


def test_el_fallback_cierra_la_lista_al_llegar_una_linea_en_blanco(monkeypatch):
    monkeypatch.setattr(pe, '_HAS_MD', False)
    html = pe._md_to_email_html('- uno\n\nparrafo')
    assert html.count('<ul') == html.count('</ul>') == 1


def test_el_fallback_cierra_una_lista_que_acaba_el_texto(monkeypatch):
    """Sin ese cierre el HTML queda malformado y algunos clientes de correo se
    comen el resto del mensaje."""
    monkeypatch.setattr(pe, '_HAS_MD', False)
    html = pe._md_to_email_html('parrafo\n\n- uno\n- dos')
    assert html.count('<ul') == html.count('</ul>') == 1
