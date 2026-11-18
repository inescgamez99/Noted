"""Exportacion a HTML y configuracion.

El HTML es lo que se abre al pulsar una reunion en el panel y lo que se adjunta
al email. Si el markdown de las minutas no se convierte bien, el usuario ve la
reunion en la lista pero al abrirla encuentra texto crudo.

De config solo interesa lo que tiene consecuencias: clean_env, que evita que un
subproceso herede credenciales de sesion, y _find_claude_bin, que resuelve el
wrapper .cmd de Windows (si devuelve el .cmd en vez del .exe, el subproceso se
cuelga sin consola).
"""
import pytest

import config
import html_exporter as he

pytestmark = pytest.mark.unit


# ── Markdown a HTML ──────────────────────────────────────────────────────

@pytest.mark.parametrize('nivel, etiqueta', [('##', 'h2'), ('###', 'h3')])
def test_las_cabeceras_se_convierten(nivel, etiqueta):
    assert f'<{etiqueta}' in he._md_to_html_body(f'{nivel} Titulo')


def test_las_listas_se_convierten():
    html = he._md_to_html_body('- uno\n- dos\n')
    assert html.count('<li') == 2


def test_la_negrita_y_la_cursiva_se_convierten():
    html = he._md_to_html_body('**fuerte** y *suave*')
    assert '<strong>' in html
    assert '<em>' in html


def test_una_tabla_se_convierte_en_tabla_html():
    md = '| Accion | Owner |\n|---|---|\n| Enviar | Felipe |\n'
    html = he._md_to_html_body(md)
    assert '<table' in html
    assert 'Enviar' in html
    assert '---' not in html


def test_DEFECTO_el_cuerpo_del_html_no_se_escapa():
    """DEFECTO ENCONTRADO AL MEDIRLO (18/09/2026).

    El TITULO si se escapa (export_to_html hace _html.escape), pero el cuerpo
    del markdown NO. Una etiqueta que venga en las minutas acaba ejecutandose
    en el HTML que se abre en el navegador.

    Severidad baja: el fichero se abre en local y su contenido sale de la
    transcripcion de tu propia reunion, no de una entrada ajena. Pero es una
    inconsistencia con outlook_sender._build_actions_email_html, que si escapa
    todo, y el HTML tambien se adjunta a emails que salen fuera.

    Se fija el comportamiento actual."""
    html = he._md_to_html_body('## <script>alert(1)</script>')
    assert '<script>alert' in html, "si esto cambia, el escapado se arreglo"


def test_el_titulo_si_se_escapa(tr_dirs):
    """Contraste con el defecto de arriba: el titulo pasa por _html.escape."""
    md = tr_dirs / 'minutes' / '20260917_1616_X.md'
    md.write_text('cuerpo', encoding='utf-8')

    salida = he.export_to_html(md, '<b>negrita</b>', open_browser=False)

    assert '<b>negrita</b>' not in salida.read_text(encoding='utf-8')


def test_un_markdown_vacio_no_revienta():
    assert isinstance(he._md_to_html_body(''), str)


# ── Metadatos que salen del nombre del fichero ───────────────────────────

def test_la_fecha_se_deriva_del_stem(tr_dirs):
    md = tr_dirs / 'minutes' / '20260917_1616_Comite.md'
    md.write_text('cuerpo', encoding='utf-8')

    meta = he._extract_meta(md)

    assert '17' in meta['date'] and '09' in meta['date']


def test_un_stem_no_conforme_no_revienta(tr_dirs):
    md = tr_dirs / 'minutes' / 'suelta.md'
    md.write_text('cuerpo', encoding='utf-8')
    assert isinstance(he._extract_meta(md), dict)


# ── El fichero HTML final ────────────────────────────────────────────────

@pytest.fixture
def minuta(tr_dirs):
    md = tr_dirs / 'minutes' / '20260917_1616_Comite_Semanal.md'
    md.write_text('## Resumen Ejecutivo\n\nSe hablo de cosas.\n\n'
                  '## Acciones\n\n- Enviar la propuesta\n', encoding='utf-8')
    return md


def test_exportar_crea_el_html_junto_a_la_minuta(minuta):
    salida = he.export_to_html(minuta, 'Comite Semanal', open_browser=False)

    assert salida.exists()
    assert salida.suffix == '.html'
    assert salida.stem == minuta.stem
    assert salida.parent == minuta.parent


def test_el_html_contiene_el_titulo_y_el_contenido(minuta):
    salida = he.export_to_html(minuta, 'Comite Semanal', open_browser=False)
    html = salida.read_text(encoding='utf-8')

    assert 'Comite Semanal' in html
    assert 'Se hablo de cosas' in html


def test_el_html_es_un_documento_completo(minuta):
    """Se abre directamente en el navegador y se adjunta al email: tiene que
    valerse por si mismo, sin hojas de estilo externas."""
    salida = he.export_to_html(minuta, 'Comite', open_browser=False)
    html = salida.read_text(encoding='utf-8')

    assert html.lstrip().startswith('<!DOCTYPE html>')
    assert '</html>' in html
    assert '<style' in html


def test_los_participantes_aparecen_en_el_html(minuta):
    salida = he.export_to_html(
        minuta, 'Comite', participants=[{'name': 'Ines'}, {'email': 'x@y.com'}],
        open_browser=False)
    html = salida.read_text(encoding='utf-8')

    assert 'Ines' in html
    assert 'x@y.com' in html


def test_con_muchos_participantes_se_resumen(minuta):
    """Seis nombres en la cabecera no caben: se muestran cinco y un contador."""
    participantes = [{'name': f'Persona {i}'} for i in range(8)]
    salida = he.export_to_html(minuta, 'Comite', participants=participantes,
                               open_browser=False)
    html = salida.read_text(encoding='utf-8')

    assert 'Persona 0' in html
    assert 'Persona 7' not in html
    assert '+3' in html


def test_sin_participantes_no_se_pinta_la_seccion(minuta):
    salida = he.export_to_html(minuta, 'Comite', open_browser=False)
    assert '👥' not in salida.read_text(encoding='utf-8')


def test_un_titulo_con_etiquetas_se_escapa(minuta):
    salida = he.export_to_html(minuta, '<script>alert(1)</script>', open_browser=False)
    html = salida.read_text(encoding='utf-8')
    assert '<script>alert(1)</script>' not in html


def test_sin_duracion_se_pone_na(minuta):
    salida = he.export_to_html(minuta, 'Comite', open_browser=False)
    assert 'N/A' in salida.read_text(encoding='utf-8')


def test_exportar_sobre_un_html_existente_lo_reemplaza(minuta):
    """Regenerar las minutas tiene que actualizar el HTML, no duplicarlo."""
    he.export_to_html(minuta, 'Primera version', open_browser=False)
    salida = he.export_to_html(minuta, 'Segunda version', open_browser=False)

    html = salida.read_text(encoding='utf-8')
    assert 'Segunda version' in html
    assert 'Primera version' not in html


def test_exportar_no_abre_el_navegador_si_no_se_pide(minuta, monkeypatch):
    """Por defecto SI abre: en un test eso lanzaria una ventana."""
    abiertos = []
    monkeypatch.setattr(he, 'os', type('OS', (), {
        'startfile': lambda p: abiertos.append(p),
        'name': 'nt',
    })())

    he.export_to_html(minuta, 'Comite', open_browser=False)

    assert abiertos == []


# ── config.clean_env ─────────────────────────────────────────────────────

def test_clean_env_quita_los_tokens_de_anthropic(monkeypatch):
    """Si el subproceso `claude -p` heredara el token de la sesion, usaria
    credenciales que no le corresponden."""
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'secreto')
    monkeypatch.setenv('ANTHROPIC_AUTH_TOKEN', 'otro-secreto')

    env = config.clean_env()

    assert not [k for k in env if k.startswith('ANTHROPIC_')]


def test_clean_env_quita_las_variables_de_mcp(monkeypatch):
    monkeypatch.setenv('MCP_SERVER_URL', 'http://x')
    assert not [k for k in config.clean_env() if k.startswith('MCP_')]


def test_clean_env_conserva_lo_que_el_cli_necesita(monkeypatch):
    """CLAUDE_CODE_GIT_BASH_PATH es imprescindible para que `claude -p`
    funcione en Windows: quitarlo de mas rompe la generacion de minutas."""
    monkeypatch.setenv('CLAUDE_CODE_GIT_BASH_PATH', 'C:/git/bash.exe')
    monkeypatch.setenv('PATH', 'C:/algo')

    env = config.clean_env()

    assert env['CLAUDE_CODE_GIT_BASH_PATH'] == 'C:/git/bash.exe'
    assert 'PATH' in env


def test_clean_env_no_modifica_el_entorno_real(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'secreto')
    config.clean_env()

    import os
    assert os.environ.get('ANTHROPIC_API_KEY') == 'secreto'


# ── config._find_claude_bin ──────────────────────────────────────────────

def test_sin_claude_en_el_path_devuelve_none(monkeypatch, tmp_path):
    monkeypatch.setattr(config.shutil, 'which', lambda _n: None)
    # Tambien se mira en las rutas habituales de instalacion (npm, AnthropicClaude,
    # ~/.local/bin):
    # en un PC con claude instalado el test encontraba el real y fallaba.
    monkeypatch.setenv('APPDATA', str(tmp_path))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    assert config._find_claude_bin() is None


def test_sin_path_encuentra_el_instalador_nativo(monkeypatch, tmp_path):
    """El instalador nativo de Claude Code deja claude.exe en ~/.local/bin, que
    no siempre esta en el PATH del daemon (Task Scheduler, PATH limitado)."""
    monkeypatch.setattr(config.shutil, 'which', lambda _n: None)
    monkeypatch.setenv('APPDATA', str(tmp_path / 'roaming'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    exe = tmp_path / '.local' / 'bin' / 'claude.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'MZ')
    assert config._find_claude_bin() == str(exe)


def test_un_exe_directo_se_devuelve_tal_cual(monkeypatch, tmp_path):
    exe = tmp_path / 'claude.exe'
    exe.write_bytes(b'MZ')
    monkeypatch.setattr(config.shutil, 'which', lambda _n: str(exe))

    assert config._find_claude_bin() == str(exe)


def test_de_un_wrapper_cmd_se_extrae_el_exe_real(monkeypatch, tmp_path):
    """El wrapper .cmd de npm se CUELGA cuando se lanza sin consola, que es
    como corre el daemon. Hay que resolver el .exe de dentro."""
    exe = tmp_path / 'node_modules' / 'claude.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'MZ')

    wrapper = tmp_path / 'claude.cmd'
    wrapper.write_text(f'@echo off\n"{exe}" %*\n', encoding='utf-8')
    monkeypatch.setattr(config.shutil, 'which', lambda _n: str(wrapper))

    assert config._find_claude_bin() == str(exe)


def test_un_wrapper_cmd_con_dp0_se_resuelve(monkeypatch, tmp_path):
    """npm escribe las rutas relativas al directorio del wrapper con %dp0%."""
    exe = tmp_path / 'claude-real.exe'
    exe.write_bytes(b'MZ')

    wrapper = tmp_path / 'claude.cmd'
    wrapper.write_text('@echo off\n"%dp0%\\claude-real.exe" %*\n', encoding='utf-8')
    monkeypatch.setattr(config.shutil, 'which', lambda _n: str(wrapper))

    assert config._find_claude_bin() == str(exe)


def test_un_wrapper_que_apunta_a_un_exe_inexistente_cae_al_cmd(monkeypatch, tmp_path):
    """Degradacion elegante: mejor intentarlo con el .cmd que no intentarlo."""
    wrapper = tmp_path / 'claude.cmd'
    wrapper.write_text('@echo off\n"C:\\no\\existe\\claude.exe" %*\n', encoding='utf-8')
    monkeypatch.setattr(config.shutil, 'which', lambda _n: str(wrapper))

    assert config._find_claude_bin() == str(wrapper)


def test_un_wrapper_ilegible_cae_al_cmd(monkeypatch, tmp_path):
    wrapper = tmp_path / 'claude.cmd'
    wrapper.write_bytes(b'\xff\xfe\x00binario')
    monkeypatch.setattr(config.shutil, 'which', lambda _n: str(wrapper))

    assert config._find_claude_bin() == str(wrapper)



# ── settings.json con BOM ────────────────────────────────────────────────

def test_settings_con_bom_se_leen_en_config_y_coaching(tmp_path, tr_dirs):
    """config.py leia settings.json con utf-8 a secas: con BOM json.loads fallaba,
    se tragaba y se ignoraban whisper_model, user_name, output_dir, language y
    minutes_retention_days. Se carga una COPIA de config.py para no tocar el
    modulo real."""
    import importlib.util
    import shutil
    from pathlib import Path

    import config as real
    carpeta = tmp_path / 'copia'
    carpeta.mkdir()
    shutil.copy(Path(real.__file__), carpeta / 'config.py')
    (carpeta / 'settings.json').write_bytes(
        b'\xef\xbb\xbf{"user_name": "Ines", "language": "es", "coaching_enabled": true}')
    spec = importlib.util.spec_from_file_location('config_copia', carpeta / 'config.py')
    copia = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copia)
    assert copia._settings.get('user_name') == 'Ines'

    import coaching_plugin
    (tr_dirs / 'settings.json').write_bytes(
        b'\xef\xbb\xbf{"language": "es", "coaching_enabled": true}')
    assert coaching_plugin._ui_lang() == 'es'
    assert coaching_plugin._coaching_enabled() is True
