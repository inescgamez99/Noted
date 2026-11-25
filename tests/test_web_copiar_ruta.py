"""«Copiar ruta» en la pestaña Capturas (web/app.js), 25/11/2026.

Cada captura lleva un boton "Copiar ruta" que pide la ruta al backend
(get_screenshot_path) y la deja en el portapapeles con el helper de siempre
(_copyPlain). Sin ruta, el aviso de error, no un portapapeles vacio.

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
const slice = (a, b) => src.slice(src.indexOf(a), src.indexOf(b, src.indexOf(a)));
const code = slice('function renderShotsTab(path, shots) {', 'function showToast(')
  + slice('function _copyFilePath(', 'async function _copyActionsTable');
const scenario = process.argv[2];
let onClick = null;
const box = { dataset: {}, innerHTML: '', querySelectorAll: () => [],
              addEventListener: (type, fn) => { if (type === 'click') onClick = fn; } };
const copied = [], toasts = [], pedidas = [];
const api = {
  get_screenshot_path: async (md, file) => { pedidas.push([md, file]); return scenario === 'sin_ruta' ? '' : 'C:\\m\\' + file; },
  get_screenshot_thumb: async () => '', copy_screenshot: async () => true, open_screenshot: () => {},
};
const nav = { clipboard: { writeText: async text => { copied.push(text); } } };
const t = k => k;
const escHtml = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const fn = new Function('document', 'pywebview', 'navigator', 't', 'escHtml', 'showToast', 'setTimeout',
  code + '; return {renderShotsTab};')(
  { getElementById: id => id === 'section-shots' ? box : null, querySelectorAll: () => [] },
  { api }, nav, t, escHtml, msg => toasts.push(msg), () => {});

(async () => {
  fn.renderShotsTab('C:\\m\\R.md', [{ file: 'R_screenshot_04-09.png', time: '04:09' }]);
  const target = { closest: sel => sel === '[data-shot]' ? { dataset: { shot: 'path' } }
                                  : sel === '.shot-card' ? { dataset: { file: 'R_screenshot_04-09.png' } } : null };
  await onClick({ target });
  await new Promise(r => setTimeout(r, 0));
  console.log(JSON.stringify({ hasButton: box.innerHTML.includes('data-shot="path"'), pedidas, copied, toasts }));
})();
"""


def _run(scenario: str) -> dict:
    r = subprocess.run(['node', '-e', _HARNESS, str(APP_JS), scenario],
                       capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip())


def test_copiar_ruta_de_una_captura():
    out = _run('normal')
    assert out['hasButton']
    assert out['pedidas'] == [['C:\\m\\R.md', 'R_screenshot_04-09.png']]
    assert out['copied'] == ['C:\\m\\R_screenshot_04-09.png']
    assert out['toasts'] == ['path_copied']


def test_sin_ruta_avisa_y_no_copia_nada():
    out = _run('sin_ruta')
    assert out['copied'] == [] and out['toasts'] == ['copy_failed']
