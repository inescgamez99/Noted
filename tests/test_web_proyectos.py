"""Vista por proyectos del panel (web/app.js): mover reuniones y terminados.

24/11/2026: una reunion quedo en el proyecto equivocado y el usuario queria
moverla arrastrandola a su seccion, y que los proyectos terminados no estorben.

LO QUE ESTOS TESTS PROTEGEN
1. El orden: los activos con reuniones A-Z, "Sin proyecto", los activos vacios
   (solo se ven al arrastrar, debajo para no empujar nada bajo el cursor), y
   "Terminados", plegados. Un terminado sin reuniones no sale, y la seccion de un
   proyecto borrado no acepta reuniones.
2. El plegado que cambio el usuario se respeta; buscando, nada sale plegado.
3. Mover llama a set_meeting_project con la ruta y actualiza la cabecera de la
   reunion abierta. "Deshacer" la deja EXACTAMENTE como estaba (proyecto y
   origen, sin volverla manual), y la copia a la carpeta del proyecto espera a
   que pase el deshacer. Un fallo deja todo como estaba.
4. Soltar solo acepta una reunion de la lista (su tipo propio), no texto de fuera.
5. Las tarjetas abren la reunion por su ruta, no por un indice que una busqueda
   desajustaba (el clic y "Eliminar" caian en otra reunion).

Como en test_web_rename_modal.py: las funciones reales de app.js con un DOM
minimo, y el test se salta si no hay Node.
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
const slice = (a, b) => src.slice(src.indexOf(a), src.indexOf(b));
const code = slice('function _wireSidebarItems(list) {', 'function renderSidebarByDays(meetings) {')
  + slice('function renderSidebarByProject(meetings) {', '// ── Papelera (reuniones eliminadas)');
const scenario = process.argv[2];

function fakeEl(extra = {}) {
  const handlers = {}, classes = new Set();
  return Object.assign({
    dataset: {}, classes,
    addEventListener(type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
    fire(type, ev) { (handlers[type] || []).forEach(fn => fn(ev)); },
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c) },
    contains: () => false,
    querySelector: () => ({ textContent: 'Uno' }),
  }, extra);
}
function fakeDrag(types = []) {
  const data = {};
  types.forEach(t => { data[t] = 'algo'; });
  return {
    get types() { return Object.keys(data); },
    setData(t, v) { data[t] = v; }, getData: t => data[t] || '',
    effectAllowed: '', dropEffect: '',
  };
}
const fakeEvent = dt => ({ prevented: false, relatedTarget: null, dataTransfer: dt,
                           preventDefault() { this.prevented = true; } });

const item = fakeEl();
item.dataset.path = 'C:\\m\\1.md';
const group = fakeEl();
group.dataset.dropProject = 'alfa';
const list = fakeEl({
  innerHTML: '',
  querySelectorAll: sel => {
    if (scenario !== 'soltar') return [];
    if (sel.startsWith('.meeting-item[draggable')) return [item];
    if (sel === '.meeting-item') return [item];
    if (sel === '[data-drop-project]') return [group];
    if (sel === '.drag-over') return group.classes.has('drag-over') ? [group] : [];
    return [];
  },
});
const select = { value: 'zeta' };
const search = { value: scenario === 'buscando' ? 'uno' : '' };
const els = { 'meetings-list': list, 'meeting-project-select': select, 'search-input': search };

const allProjects = [
  { id: 'zeta', name: 'Zeta' }, { id: 'alfa', name: 'Alfa' }, { id: 'vacio', name: 'Vacio' },
  { id: 'viejo', name: 'Viejo', finished_at: '2026-11-01' },
  { id: 'viejo-sin', name: 'Viejo sin reuniones', finished_at: '2026-11-01' },
];
const allMeetings = [
  { path: 'C:\\m\\1.md', title: 'Uno', project_id: 'zeta', project_source: 'title' },
  { path: 'C:\\m\\2.md', title: 'Dos', project_id: 'alfa', project_source: '' },
  { path: 'C:\\m\\3.md', title: 'Tres', project_id: 'viejo', project_source: '' },
  { path: 'C:\\m\\4.md', title: 'Cuatro', project_id: '', project_source: '' },
  { path: 'C:\\m\\5.md', title: 'Cinco', project_id: 'borrado', project_source: '' },
];
const collapsed = Object.create(null);
if (scenario === 'plegado') Object.assign(collapsed, { alfa: true, viejo: false });
const calls = [], restores = [], exports = [], toasts = [], timers = [], opened = [], searches = [];
const api = {
  set_meeting_project: async (path, pid) => { calls.push([path, pid]); return scenario !== 'fallo'; },
  restore_meeting_project: async (path, pid, src) => { restores.push([path, pid, src]); return true; },
  export_to_project: async path => { exports.push(path); return 'no_directory'; },
};
const t = (k, ...a) => a.length ? `${k}:${a.join(',')}` : k;
const escHtml = s => s ? String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#039;') : '';
const samePath = (a, b) => a && b && a.replace(/\\/g, '/').toLowerCase() === b.replace(/\\/g, '/').toLowerCase();

const fn = new Function(
  'document', 'pywebview', 'allProjects', 'allMeetings', '_projectGroupCollapsed', 'currentPath',
  't', 'escHtml', '_projectColor', 'openMeeting', '_showMeetingContextMenu', 'renderSidebar',
  'onSearch', '_samePath', 'showToast', 'setTimeout',
  code + '; return {renderSidebarByProject, moveMeetingToProject};')(
  { getElementById: id => els[id] }, { api }, allProjects, allMeetings, collapsed, 'C:\\m\\1.md',
  t, escHtml, () => '#000', p => opened.push(p), () => {}, () => {},
  q => searches.push(q), samePath, (msg, opts) => toasts.push({ msg, opts }),
  (f, ms) => timers.push({ f, ms }));

const tick = () => new Promise(r => setImmediate(r));
const runTimers = async () => { const ts = timers.splice(0); for (const x of ts) await x.f(); };

function sections(html) {
  const out = { order: [], empty: [], collapsed: [], noDrop: [] };
  const re = /project-sidebar-divider|class="project-sidebar-group([^"]*)"( data-drop-project="[^"]+")?>\s*<div class="project-sidebar-header"[^>]*toggleProjectSidebarGroup\('([^']+)'\)/g;
  for (const m of html.matchAll(re)) {
    if (m[0] === 'project-sidebar-divider') { out.order.push('|'); continue; }
    out.order.push(m[3]);
    if (m[1].includes('empty')) out.empty.push(m[3]);
    if (!m[2]) out.noDrop.push(m[3]);
  }
  for (const m of html.matchAll(/class="project-sidebar-items( collapsed)?" id="pgroup-([^"]+)"/g)) {
    if (m[1]) out.collapsed.push(m[2]);
  }
  out.draggable = (html.match(/data-path="C:\\m\\1\.md" draggable="true"/g) || []).length;
  out.midx = html.includes('data-midx');
  return out;
}

(async () => {
  if (scenario === 'orden' || scenario === 'plegado') {
    fn.renderSidebarByProject(allMeetings);
    console.log(JSON.stringify(sections(list.innerHTML)));
  } else if (scenario === 'busqueda') {
    fn.renderSidebarByProject(allMeetings.slice(2, 3));            // solo "Tres", de un terminado
    console.log(JSON.stringify(sections(list.innerHTML)));
  } else if (scenario === 'mover' || scenario === 'buscando') {
    await fn.moveMeetingToProject('c:/M/1.md', 'alfa');             // otra forma de la misma ruta
    const after = { project: allMeetings[0].project_id, source: allMeetings[0].project_source, select: select.value };
    await fn.moveMeetingToProject('C:\\m\\1.md', 'alfa');           // ya esta ahi: nada
    const toast = toasts[0];
    await toast.opts.onAction();                                     // Deshacer
    await runTimers();                                               // pasa el plazo: no exporta
    const undone = { project: allMeetings[0].project_id, source: allMeetings[0].project_source, select: select.value };
    await fn.moveMeetingToProject('C:\\m\\1.md', 'alfa');
    const plazo = timers.map(x => x.ms);
    await runTimers();                                               // sin deshacer: exporta
    console.log(JSON.stringify({ calls, restores, exports, after, undone, plazo, searches,
      toast: toast.msg, action: toast.opts.action, undoToast: toasts[1].msg, undoToastHasAction: !!toasts[1].opts }));
  } else if (scenario === 'fallo') {
    select.value = 'alfa';                                           // el desplegable ya lo muestra
    await fn.moveMeetingToProject('C:\\m\\1.md', 'alfa');
    console.log(JSON.stringify({ project: allMeetings[0].project_id, source: allMeetings[0].project_source,
      select: select.value, toasts: toasts.map(x => x.msg), exports, timers: timers.length }));
  } else if (scenario === 'soltar') {
    fn.renderSidebarByProject(allMeetings);
    const fuera = fakeEvent(fakeDrag(['text/plain']));               // texto arrastrado desde fuera
    group.fire('dragover', fuera);
    group.fire('drop', fakeEvent(fakeDrag(['text/plain'])));
    await tick();
    const antes = calls.length;
    const dt = fakeDrag();
    item.fire('dragstart', fakeEvent(dt));
    const draggingAtStart = list.classes.has('dragging');           // no en el propio dragstart
    await runTimers();
    const dragging = list.classes.has('dragging');
    const sobre = fakeEvent(dt);
    group.fire('dragover', sobre);
    const resaltada = group.classes.has('drag-over');
    group.fire('drop', fakeEvent(dt));
    await tick(); await tick();
    item.fire('click', {});
    console.log(JSON.stringify({ antes, outsidePrevented: fuera.prevented, draggingAtStart, dragging, resaltada,
      overPrevented: sobre.prevented, calls, opened, types: dt.types,
      after: list.classes.has('dragging') || group.classes.has('drag-over') }));
  }
})();
"""


def _run(scenario: str) -> dict:
    r = subprocess.run(['node', '-e', _HARNESS, str(APP_JS), scenario],
                       capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip())


def test_el_orden_de_las_secciones():
    out = _run('orden')
    assert out['order'] == ['alfa', 'borrado', 'zeta', 'none', 'vacio', '|', 'viejo'], \
        "un terminado sin reuniones no sale; un activo vacio si, debajo, para soltar"
    assert out['empty'] == ['vacio']
    assert out['collapsed'] == ['viejo']
    assert out['noDrop'] == ['borrado'], "un proyecto borrado no recibe reuniones"
    assert out['draggable'] == 1 and not out['midx'], "la tarjeta lleva su ruta, no un indice"


def test_el_plegado_que_cambio_el_usuario_se_respeta():
    assert _run('plegado')['collapsed'] == ['alfa']


def test_buscando_no_se_pliega_nada():
    out = _run('busqueda')
    assert out['order'] == ['none', 'alfa', 'vacio', 'zeta', '|', 'viejo']
    assert out['collapsed'] == [], "el resultado de un terminado plegado parece una busqueda vacia"


def test_mover_y_deshacer_deja_la_reunion_como_estaba():
    out = _run('mover')
    assert out['calls'] == [['c:/M/1.md', 'alfa'], ['C:\\m\\1.md', 'alfa']], \
        "mover, nada si ya estaba ahi, y otra vez tras deshacer"
    assert out['after'] == {'project': 'alfa', 'source': 'manual', 'select': 'alfa'}
    assert out['restores'] == [['c:/M/1.md', 'zeta', 'title']], \
        "deshacer devuelve el proyecto Y su origen: una suposicion no se vuelve manual"
    assert out['undone'] == {'project': 'zeta', 'source': 'title', 'select': 'zeta'}
    assert out['toast'] == 'toast_meeting_moved:Alfa' and out['action'] == 'toast_undo'
    assert out['undoToast'] == 'toast_meeting_moved:Zeta' and not out['undoToastHasAction']
    assert out['plazo'] == [6000], "la copia a la carpeta espera a que pase el deshacer"
    assert out['exports'] == ['C:\\m\\1.md'], "deshecha no se copia; sin deshacer, si"
    assert out['searches'] == []


def test_con_una_busqueda_escrita_se_redibujan_sus_resultados():
    out = _run('buscando')
    assert out['searches'] and set(out['searches']) == {'uno'}


def test_si_falla_todo_queda_como_estaba():
    out = _run('fallo')
    assert out['project'] == 'zeta' and out['source'] == 'title' and out['select'] == 'zeta'
    assert out['toasts'] == ['toast_project_move_failed'] and out['timers'] == 0 and out['exports'] == []


def test_soltar_solo_acepta_una_reunion_de_la_lista():
    out = _run('soltar')
    assert out['antes'] == 0 and not out['outsidePrevented'], "texto de fuera: ni se acepta ni mueve nada"
    assert out['types'] == ['application/x-noted-meeting'], "sin text/plain: no se escribe en un campo"
    assert not out['draggingAtStart'] and out['dragging'], "el modo arrastre, fuera del dragstart"
    assert out['resaltada'] and out['overPrevented']
    assert out['calls'] == [['C:\\m\\1.md', 'alfa']]
    assert not out['after'], "al soltar se quitan el resaltado y el modo arrastre"
    assert out['opened'] == ['C:\\m\\1.md'], "el clic abre la reunion de la tarjeta, por su ruta"
