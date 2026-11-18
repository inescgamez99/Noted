"""Todo import de terceros del codigo tiene que estar en requirements.txt.

INCIDENTE QUE ORIGINO ESTE TEST
`project_context._extract_file_text` importa de forma perezosa `docx`,
`openpyxl`, `pptx` y `pypdf`, y ninguno estaba en requirements.txt. En una
instalacion limpia la memoria de proyecto no leia Word, Excel, PowerPoint ni
PDF y noted.log se llenaba de avisos (~15) por un ImportError tragado. Nada lo
detectaba porque un import dentro de una funcion no falla al arrancar.

El test recorre con ast los .py del repo (tambien los imports dentro de
funciones) y comprueba cada paquete de terceros contra requirements.txt.
"""
import ast
import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
_EXCLUIDOS = {'tests', 'tools', '.venv', 'venv', 'env', 'node_modules', '.git',
              '.claude', 'build', 'dist'}

# nombre de import -> nombre del paquete en requirements.txt
IMPORT_A_PAQUETE = {
    'docx': 'python-docx',
    'pptx': 'python-pptx',
    'openpyxl': 'openpyxl',
    'pypdf': 'pypdf',
    'win32com': 'pywin32',
    'pythoncom': 'pywin32',
    'pywintypes': 'pywin32',
    'win32gui': 'pywin32',
    'win32ui': 'pywin32',
    'win32con': 'pywin32',
    'win32api': 'pywin32',
    'win32process': 'pywin32',
    'win32event': 'pywin32',
    'win32file': 'pywin32',
    'win32pipe': 'pywin32',
    'win32clipboard': 'pywin32',
    'win32service': 'pywin32',
    'win32serviceutil': 'pywin32',
    'win32security': 'pywin32',
    'winerror': 'pywin32',
    'webview': 'pywebview',
    'dotenv': 'python-dotenv',
    'PIL': 'Pillow',
    'faster_whisper': 'faster-whisper',
    'imageio_ffmpeg': 'imageio-ffmpeg',
    'pyaudiowpatch': 'pyaudiowpatch',
    'yaml': 'pyyaml',
}

# Imports que NO estan en requirements.txt a proposito.
OPCIONALES_PERMITIDOS = {
    # Modulos locales que se importan con try/except y pueden no existir en el
    # repo (no son paquetes de pip): se ignoran.
    'brain_synthesizer',
    'email_brain_updater',
}


def _normaliza(nombre: str) -> str:
    return re.sub(r'[-_.]+', '-', nombre).lower()


def _requirements() -> set:
    paquetes = set()
    for linea in (RAIZ / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        linea = linea.split('#')[0].strip()
        if not linea:
            continue
        m = re.match(r'[A-Za-z0-9_.\-]+', linea)
        if m:
            paquetes.add(_normaliza(m.group(0)))
    return paquetes


def _modulos_locales() -> set:
    locales = set()
    for p in RAIZ.iterdir():
        if p.suffix == '.py':
            locales.add(p.stem)
        elif p.is_dir() and (p / '__init__.py').exists():
            locales.add(p.name)
    return locales


def _imports_de_terceros() -> dict:
    """{modulo_de_nivel_superior: primer fichero que lo importa}"""
    stdlib = set(sys.stdlib_module_names)
    locales = _modulos_locales()
    encontrados = {}
    for py in RAIZ.rglob('*.py'):
        rel = py.relative_to(RAIZ)
        if set(rel.parts) & _EXCLUIDOS:
            continue
        arbol = ast.parse(py.read_text(encoding='utf-8-sig'), filename=str(py))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                nombres = [a.name for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
                nombres = [nodo.module]
            else:
                continue
            for n in nombres:
                top = n.split('.')[0]
                if top in stdlib or top in locales or top in OPCIONALES_PERMITIDOS:
                    continue
                encontrados.setdefault(top, str(rel))
    return encontrados


@pytest.mark.unit
def test_los_imports_de_terceros_estan_en_requirements():
    reqs = _requirements()
    faltan = []
    for modulo, fichero in sorted(_imports_de_terceros().items()):
        paquete = IMPORT_A_PAQUETE.get(modulo, modulo)
        if _normaliza(paquete) not in reqs:
            faltan.append(f"{modulo} (paquete '{paquete}'), importado en {fichero}")
    assert not faltan, (
        "Imports de terceros que no estan en requirements.txt. Añadelos, o si "
        "son opcionales a proposito, listalos en OPCIONALES_PERMITIDOS:\n  "
        + "\n  ".join(faltan))


@pytest.mark.unit
def test_los_cuatro_lectores_de_documentos_estan_en_requirements():
    """Los que originaron el incidente, por nombre, para que el test de arriba
    no pueda volverse vacuo (p. ej. si cambia la exclusion de carpetas)."""
    assert {'python-docx', 'openpyxl', 'python-pptx', 'pypdf'} <= _requirements()
