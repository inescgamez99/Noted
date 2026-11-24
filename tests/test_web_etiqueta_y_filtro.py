"""Etiqueta de proyecto en la vista por dias y filtro por proyecto (web/app.js).

24/11/2026: el usuario queria ver de un vistazo a que proyecto quedo asignada
cada reunion, para comprobar que el tagging es correcto, sin cambiar a la vista
por proyectos. La busqueda del panel no sirve para eso: busca el texto de las
minutas, no la asignacion, asi que "Digital Brain" trae las que lo mencionan
estando en otro proyecto y no trae las asignadas que no lo nombran.

LO QUE ESTOS TESTS PROTEGEN
1. Cada reunion de la vista por dias lleva su etiqueta: el proyecto, "por
   confirmar" (adivinado por el titulo), "Sin proyecto", terminado o borrado.
2. Pulsar la etiqueta filtra por ese proyecto y NO abre la reunion; pulsar el
   resto de la tarjeta la abre. Encima de la lista sale "Proyecto: X" con su ✕.
3. El filtro vale en las dos vistas y sobre los resultados de una busqueda. En la
   vista por proyectos, los demas proyectos siguen siendo donde soltar.
4. Filtrando, un proyecto terminado no sale plegado (se veria vacio), y un
   proyecto sin reuniones dice que no hay ninguna.

Como en test_web_proyectos.py: las funciones reales de app.js con un DOM minimo,
y el test se salta si no hay Node.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which('node') is None,
                                                    reason='Node no esta instalado')]

APP_JS = Path(__file__).resolve().parent.parent / 'web' / 'app.js'

_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[1], 'utf8');
const code = src.slice(src.indexOf('let _sidebarProjectFilter = null;'),
                       src.indexOf('// ── Papelera (reuniones eliminadas)'));
const [scenario, mode] = process.argv.slice(2);

const classes = () => { const s = new Set(); return { s, add: c => s.add(c), remove: c => s.delete(c),
  contains: c => s.has(c), toggle: (c, on) => (on ? s.add(c) : s.delete(c)) }; };
const cards = [];
const card = path => {
  const h = {};
  const el = { dataset: { path }, addEventListener: (t, f) => { h[t] = f; }, fire: (t, e) => h[t] && h[t](e),
               querySelector: () => ({ textContent: path }) };
  cards.push(el);
  return el;
};
const list = { innerHTML: '', classList: classes(),
  querySelectorAll: sel => sel === '.meeting-item'
    ? [...list.innerHTML.matchAll(/class="meeting-item[^"]*" data-path="([^"]+)"/g)].map(m => card(m[1]))
    : [] };
const filterBox = { innerHTML: '', classList: classes() };
filterBox.classList.add('hidden');
const search = { value: '' };
const els = { 'meetings-list': list, 'sidebar-filter': filterBox, 'search-input': search,
              'meeting-project-select': { value: '' } };

const allProjects = [
  { id: 'zeta', name: 'Zeta', color: '#111111' }, { id: 'alfa', name: 'Alfa', color: '#222222' },
  { id: 'vacio', name: 'Vacio' }, { id: 'viejo', name: 'Viejo', finished_at: '2026-11-01' },
];
const allMeetings = [
  { path: 'm1', title: 'Uno', date: 'd1', project_id: 'zeta', project_source: '' },
  { path: 'm2', title: 'Dos', date: 'd1', project_id: 'alfa', project_source: 'title' },
  { path: 'm3', title: 'Tres', date: 'd2', project_id: '', project_source: '' },
  { path: 'm4', title: 'Cuatro', date: 'd2', project_id: 'viejo', project_source: '' },
  { path: 'm5', title: 'Cinco', date: 'd2', project_id: 'borrado', project_source: '' },
  { path: 'm6', title: 'Seis', date: 'd3', project_id: 'zeta', project_source: 'manual' },
];
const opened = [], searches = [];
const t = (k, ...a) => a.length ? `${k}:${a.join(',')}` : k;
const escHtml = s => s ? String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#039;') : '';

const fn = new Function(
  'document', 'pywebview', 'allProjects', 'allMeetings', '_projectGroupCollapsed', 'currentPath',
  'currentSidebarMode', 'dayLabel', 't', 'escHtml', '_projectColor', 'openMeeting',
  '_showMeetingContextMenu', 'onSearch', '_samePath', 'showToast', 'setTimeout',
  code + '; return {renderSidebar, _setSidebarProjectFilter,'
       + ' setLastSearch: (query, results) => { _lastSearch = { query, results }; }};')(
  { getElementById: id => els[id] }, { api: {} }, allProjects, allMeetings, Object.create(null), '',
  mode, d => d, t, escHtml, p => p.color || '#000', p => opened.push(p),
  () => {}, q => searches.push(q), (a, b) => a === b, () => {}, () => {});

const tags = html => [...html.matchAll(/class="meeting-project-tag([^"]*)" data-filter-project="([^"]+)"[^>]*title="([^"]*)"><span class="tag-name">([^<]*)<\/span>/g)]
  .map(m => ({ cls: m[1].trim(), pid: m[2], tip: m[3], name: m[4] }));
const titles = html => [...html.matchAll(/class="meeting-title">([^<]*)</g)].map(m => m[1]);
const sections = html => [...html.matchAll(/class="project-sidebar-group([^"]*)"( data-drop-project="[^"]+")?>\s*<div class="project-sidebar-header"[^>]*toggleProjectSidebarGroup\('([^']+)'\)/g)]
  .map(m => ({ pid: m[3], empty: m[1].includes('empty'), drop: !!m[2] }));
const collapsed = html => [...html.matchAll(/class="project-sidebar-items( collapsed)?" id="pgroup-([^"]+)"/g)]
  .filter(m => m[1]).map(m => m[2]);
const chip = () => ({ hidden: filterBox.classList.contains('hidden'), text: (filterBox.innerHTML.match(/sidebar-filter-label">([^<]*)</) || [])[1] || '' });

const out = {};
if (scenario === 'sin_proyectos') allProjects.length = 0;
fn.renderSidebar(allMeetings);
if (scenario === 'etiquetas' || scenario === 'sin_proyectos') {
  out.tags = tags(list.innerHTML);
  out.chip = chip();
} else if (scenario === 'clic') {
  const tagTarget = { closest: sel => sel === '.meeting-project-tag' ? { dataset: { filterProject: 'zeta' } } : null };
  cards.find(c => c.dataset.path === 'm2').fire('click', { target: tagTarget });   // la etiqueta
  out.afterTag = { titles: titles(list.innerHTML), chip: chip(), opened: [...opened] };
  cards.at(-1).fire('click', { target: { closest: () => null } });                 // el resto de la tarjeta
  out.opened = [...opened];
  fn._setSidebarProjectFilter(null);                                                 // la ✕
  out.cleared = { titles: titles(list.innerHTML), chip: chip() };
} else if (scenario === 'filtro') {
  fn._setSidebarProjectFilter(process.argv[4]);
  const html = list.innerHTML;
  out.titles = titles(html); out.sections = sections(html); out.collapsed = collapsed(html); out.chip = chip();
  out.empty = html.includes('filter_empty');
} else if (scenario === 'busqueda') {
  search.value = 'algo';
  fn._setSidebarProjectFilter('zeta');                       // sin resultados guardados: busca
  out.searches = [...searches];
  fn.renderSidebar(allMeetings.slice(3));                    // lo que devuelve la busqueda
  out.titles = titles(list.innerHTML);
  fn.renderSidebar(allMeetings.slice(2, 5));                 // una busqueda sin nada de 'zeta'
  out.emptyText = (list.innerHTML.match(/class="loading">([^<]*)</) || [])[1];
} else if (scenario === 'busqueda_guardada') {
  search.value = 'algo';
  fn.setLastSearch('algo', allMeetings.slice(3));
  fn._setSidebarProjectFilter('zeta');                       // con resultados: al momento
  out.searches = [...searches];
  out.titles = titles(list.innerHTML);
  out.chip = chip();
}
console.log(JSON.stringify(out));
"""


def _run(scenario: str, mode: str = 'days', arg: str = '') -> dict:
    r = subprocess.run(['node', '-e', _HARNESS, str(APP_JS), scenario, mode, arg],
                       capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip())


def test_cada_reunion_lleva_la_etiqueta_de_su_proyecto():
    out = _run('etiquetas')
    por_nombre = {x['name']: x for x in out['tags']}
    assert [x['name'] for x in out['tags']] == ['Zeta', 'Alfa ?', 'no_project_label', 'Viejo', 'borrado', 'Zeta']
    assert por_nombre['Zeta']['cls'] == '' and por_nombre['Zeta']['tip'] == 'tag_filter_tip'
    assert por_nombre['Alfa ?']['cls'] == 'guess', \
        "una suposicion del titulo se distingue: no se archiva hasta confirmarla"
    assert por_nombre['Alfa ?']['tip'] == 'tag_guess_tip · tag_filter_tip', "y sigue diciendo que filtra"
    assert por_nombre['no_project_label']['cls'] == 'none' and por_nombre['no_project_label']['pid'] == 'none'
    assert por_nombre['Viejo']['cls'] == 'finished'
    assert por_nombre['borrado']['cls'] == 'deleted'
    assert por_nombre['borrado']['tip'] == 'tag_deleted_tip · tag_filter_tip'
    assert out['chip']['hidden'], "sin filtro no hay aviso encima de la lista"


def test_la_vista_por_proyectos_no_lleva_etiqueta():
    assert _run('etiquetas', 'projects')['tags'] == []


def test_sin_ningun_proyecto_no_hay_etiquetas():
    """Recien instalado, sin projects.json: "Sin proyecto" en cada tarjeta no dice nada."""
    assert _run('sin_proyectos')['tags'] == []


def test_pulsar_la_etiqueta_filtra_y_no_abre_la_reunion():
    out = _run('clic')
    assert out['afterTag'] == {'titles': ['Uno', 'Seis'], 'opened': [],
                               'chip': {'hidden': False, 'text': 'filter_chip:Zeta'}}
    assert out['opened'] == ['m6'], "el resto de la tarjeta sigue abriendo la reunion"
    assert out['cleared']['chip']['hidden'] and len(out['cleared']['titles']) == 6


def test_filtrando_en_proyectos_los_demas_siguen_siendo_donde_soltar():
    out = _run('filtro', 'projects', 'zeta')
    assert out['titles'] == ['Uno', 'Seis']
    secciones = {s['pid']: s for s in out['sections']}
    assert not secciones['zeta']['empty']
    assert all(secciones[p]['empty'] and secciones[p]['drop'] for p in ('alfa', 'vacio', 'none')), \
        "para sacar una reunion mal clasificada del proyecto filtrado"
    assert 'viejo' not in secciones


def test_un_terminado_filtrado_no_sale_plegado():
    assert _run('filtro', 'projects', '')['collapsed'] == ['viejo'], "control: sin filtro, plegado"
    out = _run('filtro', 'projects', 'viejo')
    assert out['titles'] == ['Cuatro'] and out['collapsed'] == []


def test_sin_proyecto_tambien_se_filtra():
    assert _run('filtro', 'days', 'none')['titles'] == ['Tres']


def test_un_proyecto_sin_reuniones_lo_dice():
    out = _run('filtro', 'days', 'vacio')
    assert out['empty'] and out['titles'] == [] and out['chip']['text'] == 'filter_chip:Vacio'


def test_el_filtro_se_aplica_a_los_resultados_de_la_busqueda():
    out = _run('busqueda')
    assert out['searches'] == ['algo'], "sin resultados guardados, se vuelve a lanzar la busqueda"
    assert out['titles'] == ['Seis']
    assert out['emptyText'] == 'no_meetings', "buscando, lo vacio es la busqueda, no el proyecto"


def test_con_la_busqueda_ya_hecha_el_filtro_es_inmediato():
    """Sin volver a leer todas las minutas: onSearch espera 300 ms y busca en disco."""
    out = _run('busqueda_guardada')
    assert out['searches'] == [] and out['titles'] == ['Seis']
    assert out['chip'] == {'hidden': False, 'text': 'filter_chip:Zeta'}
