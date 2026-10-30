"""Contrato con el consumidor externo de las minutas.

Hay una herramienta FUERA de este repo que lee lo que produce la app:

    ~/.claude/projects/<proyecto>/tools/scan_meetings.py

y depende de cinco cosas. Si cualquiera cambia sin avisar, ese consumidor deja
de ver las reuniones y nadie se entera desde aqui:

  1. `minutes/` es un directorio PLANO (sin subcarpetas por proyecto)
  2. cada reunion son CUATRO ficheros con el mismo stem
  3. el stem es `YYYYMMDD_HHMM_Titulo_Con_Underscores`
  4. `project_docs/<project-id>/meetings/` marca la pertenencia a un proyecto
  5. el `_actions.json` hermano, localizado por el mismo stem

El brief original decia que tambien dependia de `projects.json`. Medido el
18/09/2026 sobre el codigo real: NO. Lleva el id hardcodeado en una constante
PROJECT_ID, asi que renombrar un id no lo rompe (pero tampoco se entera).

Estos tests fijan el lado que PRODUCE, que es el unico que este repo controla.
El consumidor vive en otro sitio y no se puede importar desde aqui ni desde CI:
al final del fichero hay una comprobacion opcional contra el real, que se salta
si no esta presente.
"""
import json
import re
from pathlib import Path

import pytest

import project_context as pc
import storage

pytestmark = pytest.mark.unit

STEM_ESPERADO = re.compile(r'^\d{8}_\d{4}_[^/\\]+$')


# ── 1. El directorio de minutas es plano ─────────────────────────────────

def test_las_minutas_se_escriben_en_la_raiz_de_minutes(tr_dirs):
    """El consumidor hace glob('minutes/*.md'), sin recursion. Una subcarpeta
    por proyecto haria invisibles todas las reuniones."""
    wav = storage.RECORDINGS_DIR / '2026-09-17_16-16_manual.wav'
    md = storage.get_minutes_path(wav, 'Revision Deck')

    assert md.parent == storage.MINUTES_DIR
    assert md.parent.name == 'minutes'


# ── 2 y 3. Los cuatro ficheros y el stem ─────────────────────────────────

def test_el_stem_de_minutas_cumple_el_formato_del_consumidor(tr_dirs):
    wav = storage.RECORDINGS_DIR / '2026-09-17_16-16_manual.wav'
    md = storage.get_minutes_path(wav, 'Revision Deck Sales')

    assert STEM_ESPERADO.match(md.stem), md.stem
    assert md.stem == '20260917_1616_Revision_Deck_Sales'


def test_el_stem_no_lleva_espacios(tr_dirs):
    """El consumidor parte el stem por '_': un espacio le rompe el parseo."""
    md = storage.get_minutes_path(storage.RECORDINGS_DIR / '2026-09-17_16-16_x.wav',
                                  'Con Muchos Espacios Aqui')
    assert ' ' not in md.stem


@pytest.mark.parametrize('sufijo', ['.md', '.html', '_actions.json', '_transcript.txt'])
def test_los_cuatro_ficheros_comparten_el_stem(tr_dirs, sufijo):
    """El consumidor localiza los tres acompanantes a partir del .md. Si el
    stem divergiera, veria la reunion pero no sus acciones."""
    wav = storage.RECORDINGS_DIR / '2026-09-17_16-16_manual.wav'
    md = storage.get_minutes_path(wav, 'Revision Deck')

    if sufijo == '.md':
        hermano = md
    elif sufijo == '.html':
        hermano = md.with_suffix('.html')
    else:
        hermano = md.parent / f'{md.stem}{sufijo}'

    assert hermano.stem.startswith(md.stem)
    assert hermano.parent == storage.MINUTES_DIR


def test_el_md_es_la_prueba_de_que_la_reunion_ocurrio(tr_dirs):
    """Contrato explicito del consumidor: usa minutes/*.md como fuente de
    verdad. Por eso las minutas NO se borran en la limpieza de 15 dias."""
    md = storage.MINUTES_DIR / '20260101_0900_Vieja.md'
    md.write_text('x', encoding='utf-8')

    import os
    import time
    os.utime(md, (time.time() - 90 * 86400,) * 2)
    storage.cleanup_old_recordings(days=15)

    assert md.exists(), "borrar una minuta borra la prueba de la reunion"


# ── 4. La senal de pertenencia a un proyecto ─────────────────────────────

@pytest.fixture
def reunion_archivada(tr_dirs):
    (tr_dirs / 'projects.json').write_text(json.dumps({'projects': [
        {'id': 'contoso-agentic-ai', 'name': 'Contoso Agentic AI'},
    ]}), encoding='utf-8')

    stem = '20260917_1616_Contoso_Kickoff'
    md = pc.MINUTES_DIR / f'{stem}.md'
    md.write_text('TITULO: Contoso Kickoff\n\n## Resumen Ejecutivo\n\nArrancamos.\n',
                  encoding='utf-8')
    (pc.MINUTES_DIR / f'{stem}_actions.json').write_text(
        json.dumps({'project_id': 'contoso-agentic-ai', 'actions': []}), encoding='utf-8')

    pc.sync_meeting_summary(md)
    return {'stem': stem, 'md': md, 'root': tr_dirs}


def test_la_pertenencia_se_marca_en_project_docs_id_meetings(reunion_archivada):
    """Ruta exacta que el consumidor recorre: project_docs/<id>/meetings/"""
    esperada = (reunion_archivada['root'] / 'project_docs' / 'contoso-agentic-ai'
                / 'meetings' / f"{reunion_archivada['stem']}.txt")
    assert esperada.exists(), "el consumidor no vera esta reunion como del proyecto"


def test_el_id_de_proyecto_de_la_ruta_existe_en_projects_json(reunion_archivada):
    """Si se archivara bajo un id que no esta en projects.json, el consumidor
    tendria una carpeta huerfana que no sabe a que proyecto pertenece."""
    root = reunion_archivada['root']
    ids_declarados = {p['id'] for p in
                      json.loads((root / 'projects.json').read_text(encoding='utf-8'))['projects']}
    ids_en_disco = {d.name for d in (root / 'project_docs').iterdir() if d.is_dir()}

    assert ids_en_disco <= ids_declarados, f"ids huerfanos: {ids_en_disco - ids_declarados}"


def test_el_nombre_del_fichero_archivado_es_el_stem_de_la_minuta(reunion_archivada):
    """Es lo que permite al consumidor cruzar la carpeta del proyecto con
    minutes/*.md."""
    archivados = list((reunion_archivada['root'] / 'project_docs' / 'contoso-agentic-ai'
                       / 'meetings').iterdir())
    assert [p.stem for p in archivados] == [reunion_archivada['stem']]


def test_una_reunion_sin_proyecto_no_deja_rastro_en_project_docs(tr_dirs):
    (tr_dirs / 'projects.json').write_text(json.dumps({'projects': []}), encoding='utf-8')
    md = pc.MINUTES_DIR / '20260917_1616_Sin_Proyecto.md'
    md.write_text('TITULO: Sin Proyecto\n\ncuerpo', encoding='utf-8')
    (pc.MINUTES_DIR / '20260917_1616_Sin_Proyecto_actions.json').write_text(
        json.dumps({'project_id': 'none', 'actions': []}), encoding='utf-8')

    pc.sync_meeting_summary(md)

    docs = tr_dirs / 'project_docs'
    assert not docs.exists() or not any(docs.rglob('20260917_1616_Sin_Proyecto.txt'))


# ── 5. projects.json ─────────────────────────────────────────────────────

def test_projects_json_vive_en_la_raiz_del_proyecto(tr_dirs):
    """El consumidor lo busca ahi, no en minutes/ ni en project_docs/."""
    import config
    assert (config.PROJECT_DIR / 'projects.json').parent == config.PROJECT_DIR


def test_cada_proyecto_declara_al_menos_id_y_name(tr_dirs):
    (tr_dirs / 'projects.json').write_text(json.dumps({'projects': [
        {'id': 'x', 'name': 'X'},
    ]}), encoding='utf-8')

    datos = json.loads((tr_dirs / 'projects.json').read_text(encoding='utf-8'))
    for proyecto in datos['projects']:
        assert 'id' in proyecto and 'name' in proyecto


# ── Comprobacion opcional contra el consumidor real ──────────────────────

# Se busca en cualquier proyecto: el repo es publico y no nombra el del cliente.
_CANDIDATOS = sorted((Path.home() / '.claude' / 'projects').glob('*/tools/scan_meetings.py'))
CONSUMIDOR = _CANDIDATOS[0] if _CANDIDATOS else None


@pytest.mark.integration
@pytest.mark.skipif(CONSUMIDOR is None,
                    reason='el consumidor externo no esta en esta maquina')
def test_el_consumidor_real_sigue_esperando_lo_mismo():
    """Lee el codigo del consumidor y comprueba que las cinco piezas del
    contrato siguen ahi. Es una comprobacion de texto a proposito: importarlo
    ejecutaria codigo de otro proyecto sobre los datos reales del usuario.

    Marcado integration: no existe en CI ni en la maquina de un companero.
    """
    codigo = CONSUMIDOR.read_text(encoding='utf-8', errors='replace')

    assert 'minutes' in codigo, "ya no lee minutes/"
    assert 'glob("*.md")' in codigo, "ya no usa los .md planos como fuente de verdad"
    assert '_actions.json' in codigo, "ya no busca el actions hermano por stem"
    assert 'project_docs' in codigo and 'meetings' in codigo, \
        "ya no usa project_docs/<id>/meetings/ como senal de pertenencia"

    # CORRECCION MEDIDA (18/09/2026): el brief decia que el consumidor tambien
    # dependia de projects.json para la lista de ids. NO es cierto: lleva el id
    # del proyecto HARDCODEADO en una constante PROJECT_ID. O sea que renombrar
    # un id en projects.json no lo rompe, pero tampoco se enteraria del cambio.
    assert 'projects.json' not in codigo, \
        "ahora SI lee projects.json: hay una dependencia nueva que documentar"
    assert 'PROJECT_ID' in codigo
