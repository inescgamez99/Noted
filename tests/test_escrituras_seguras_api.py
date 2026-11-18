r"""Escrituras de AppAPI que podian perder datos del usuario.

Incidentes (revision del codigo, sin reporte de usuario todavia):
 - save_minutes_notes con notes_md vacio dejaba la minuta en blanco, y si la
   lectura previa fallaba escribia sin la seccion de Acciones.
 - save_settings/save_project hacian write_text directo: un crash a mitad
   dejaba el JSON truncado. Ahora la escritura es atomica y, si el existente
   esta corrupto, no se re-guarda encima.
 - rename_meeting metia el titulo en un f-string como REEMPLAZO de re.sub: un
   titulo con `\1` o `\g<` lanzaba error o se interpretaba, y un salto de
   linea en el titulo rompia la cabecera TITULO:.
"""
import json

import pytest

import app_window as aw

pytestmark = pytest.mark.unit

ACCIONES = '## Acciones Pendientes\n\n- [ ] Hacer algo (Ana)\n'


@pytest.fixture
def api():
    return object.__new__(aw.AppAPI)


@pytest.fixture
def minuta(tr_dirs):
    md = tr_dirs / 'minutes' / '20260916_1200_Reunion.md'
    md.write_text('TITULO: Reunion\n\nnotas viejas\n\n' + ACCIONES, encoding='utf-8')
    return md


# ── save_minutes_notes ────────────────────────────────────────────────────

@pytest.mark.parametrize('vacio', ['', '   ', '\n\n  \n'])
def test_notas_vacias_se_rechazan_y_no_tocan_la_minuta(api, minuta, vacio):
    antes = minuta.read_text(encoding='utf-8')
    assert api.save_minutes_notes(str(minuta), vacio) is False
    assert minuta.read_text(encoding='utf-8') == antes


def test_notas_conservan_la_seccion_de_acciones(api, minuta):
    assert api.save_minutes_notes(str(minuta), 'TITULO: Reunion\n\nnotas nuevas') is True
    texto = minuta.read_text(encoding='utf-8')
    assert 'notas nuevas' in texto and 'Hacer algo (Ana)' in texto
    assert not minuta.with_name(minuta.name + '.tmp').exists()


def test_si_la_lectura_previa_falla_no_se_pierde_la_seccion_de_acciones(
        api, minuta, monkeypatch):
    antes = minuta.read_text(encoding='utf-8')
    real = aw.Path.read_text

    def lectura_rota(self, *a, **k):
        if self == minuta:
            raise PermissionError('en uso')
        return real(self, *a, **k)
    monkeypatch.setattr(aw.Path, 'read_text', lectura_rota)

    assert api.save_minutes_notes(str(minuta), 'notas nuevas') is False
    monkeypatch.undo()
    assert minuta.read_text(encoding='utf-8') == antes


def test_notas_se_escriben_de_forma_atomica(api, minuta, monkeypatch):
    antes = minuta.read_text(encoding='utf-8')

    def replace_roto(*_a, **_k):
        raise OSError('crash')
    import atomic_io
    monkeypatch.setattr(atomic_io.os, 'replace', replace_roto)

    assert api.save_minutes_notes(str(minuta), 'notas nuevas') is False
    assert minuta.read_text(encoding='utf-8') == antes


# ── save_settings / save_project ──────────────────────────────────────────

def test_save_settings_guarda_y_fusiona(api, tr_dirs):
    (aw.PROJECT_DIR / 'settings.json').write_text('{"a": 1}', encoding='utf-8')
    assert api.save_settings({'b': 2}) is True
    assert json.loads((aw.PROJECT_DIR / 'settings.json').read_text(encoding='utf-8')) == \
        {'a': 1, 'b': 2}


def test_save_settings_con_json_corrupto_no_guarda_encima(api, tr_dirs):
    p = aw.PROJECT_DIR / 'settings.json'
    p.write_text('{"a": 1, "b"', encoding='utf-8')
    assert api.save_settings({'x': 1}) is False
    assert p.read_text(encoding='utf-8') == '{"a": 1, "b"'


def test_save_settings_es_atomico(api, tr_dirs, monkeypatch):
    p = aw.PROJECT_DIR / 'settings.json'
    p.write_text('{"a": 1}', encoding='utf-8')
    import atomic_io

    def replace_roto(*_a, **_k):
        raise OSError('crash')
    monkeypatch.setattr(atomic_io.os, 'replace', replace_roto)
    assert api.save_settings({'b': 2}) is False
    assert p.read_text(encoding='utf-8') == '{"a": 1}'


def test_save_project_con_json_corrupto_no_guarda_encima(api, tr_dirs):
    p = aw.PROJECT_DIR / 'projects.json'
    p.write_text('{"projects": [ {"id"', encoding='utf-8')
    assert api.save_project({'name': 'Nuevo', 'description': '', 'stakeholders': []}) is False
    assert p.read_text(encoding='utf-8') == '{"projects": [ {"id"'


def test_save_project_guarda_y_es_atomico(api, tr_dirs, monkeypatch):
    p = aw.PROJECT_DIR / 'projects.json'
    assert api.save_project({'name': 'Uno', 'description': '', 'stakeholders': []}) is True
    guardado = p.read_text(encoding='utf-8')
    assert json.loads(guardado)['projects'][0]['id'] == 'uno'

    import atomic_io

    def replace_roto(*_a, **_k):
        raise OSError('crash')
    monkeypatch.setattr(atomic_io.os, 'replace', replace_roto)
    assert api.save_project({'name': 'Dos', 'description': '', 'stakeholders': []}) is False
    assert p.read_text(encoding='utf-8') == guardado


# ── rename_meeting ────────────────────────────────────────────────────────

@pytest.mark.parametrize('titulo', [r'Plan \1 final', r'Hola \g<0>', r'\g<nada>', r'a\\b', r'\9'])
def test_rename_con_barras_invertidas_se_guarda_literal(api, minuta, titulo):
    assert api.rename_meeting(str(minuta), titulo) is True
    assert minuta.read_text(encoding='utf-8').splitlines()[0] == f'TITULO: {titulo}'


def test_rename_sin_saltos_de_linea_en_el_titulo(api, minuta):
    assert api.rename_meeting(str(minuta), 'Linea uno\nLinea dos\r\nTres') is True
    lineas = minuta.read_text(encoding='utf-8').splitlines()
    assert lineas[0] == 'TITULO: Linea uno Linea dos Tres'
    assert lineas[1] == '' and 'notas viejas' in lineas[2]


def test_rename_anade_titulo_si_la_minuta_no_lo_tenia(api, tr_dirs):
    md = tr_dirs / 'minutes' / '20260916_1300_Sin.md'
    md.write_text('contenido\n', encoding='utf-8')
    assert api.rename_meeting(str(md), r'Nuevo \1') is True
    assert md.read_text(encoding='utf-8') == 'TITULO: Nuevo \\1\n\ncontenido\n'
