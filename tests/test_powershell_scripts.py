"""Los scripts de PowerShell tienen que poder ejecutarse.

INCIDENTE QUE ORIGINO ESTOS TESTS (21/09/2026)
`watchdog.ps1` dejo de parsear y la app se quedo SIN ARRANCAR. Nada lo detecto:
el CI solo miraba Python, y un .ps1 roto no da error hasta que alguien intenta
ejecutarlo.

La causa es una trampa de Windows PowerShell 5.1 que merece explicarse entera,
porque no es evidente:

  1. El fichero llevaba una raya larga (—, U+2014) en un mensaje de log.
  2. En UTF-8 son tres bytes: E2 80 94.
  3. El fichero estaba guardado SIN BOM.
  4. PowerShell 5.1 lee los ficheros sin BOM con la codificacion ANSI del
     sistema, no como UTF-8.
  5. En CP1252, el byte 94 es U+201D: la comilla tipografica de cierre.
  6. **PowerShell acepta las comillas tipograficas como delimitador de cadena.**

Resultado: la cadena se cerraba a mitad de la linea, el resto se parseaba como
codigo, y la comilla final abria otra que nunca terminaba. El error se
reportaba 34 lineas mas abajo, donde el parser se rendia.

El arreglo es el BOM. Estos tests lo vigilan.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
# rglob, no glob: el refactor 647ac36 movio install_task.ps1 a scripts/, y un
# glob plano habria dejado de vigilarlo sin que nada avisara.
#
# Hay que excluir lo que no es nuestro: el .venv trae sus propios activate.ps1,
# que ni controlamos ni tiene sentido exigirles BOM.
_AJENOS = {'.git', '.venv', 'venv', 'env', 'node_modules', '.pytest_tmp', '.tox'}


def _nuestros(ruta: Path) -> bool:
    return not any(parte in _AJENOS for parte in ruta.parts)


SCRIPTS = sorted(p for p in REPO.rglob('*.ps1') if _nuestros(p.relative_to(REPO)))
BOM = b'\xef\xbb\xbf'


def test_hay_scripts_que_vigilar():
    """Si algun dia no hay ninguno, estos tests pasarian vacios sin avisar."""
    assert SCRIPTS, "no se encontro ningun .ps1 en la raiz del repo"


@pytest.mark.unit
@pytest.mark.parametrize('script', SCRIPTS, ids=lambda p: p.name)
def test_un_ps1_con_caracteres_no_ascii_tiene_BOM(script):
    """LA COMPROBACION QUE HABRIA EVITADO EL INCIDENTE, y no necesita
    PowerShell: es aritmetica de bytes.

    Sin BOM, PowerShell 5.1 decodifica como ANSI y cualquier caracter no-ASCII
    puede convertirse en algo que rompa el script. Con acentos espanoles en los
    mensajes de log esto no es hipotetico.
    """
    datos = script.read_bytes()
    texto = datos.decode('utf-8', errors='replace')
    no_ascii = [c for c in texto if ord(c) > 127]

    if not no_ascii:
        pytest.skip('solo ASCII: el BOM no es necesario')

    muestra = ''.join(sorted(set(no_ascii))[:10])
    assert datos.startswith(BOM), (
        f"{script.name} tiene {len(no_ascii)} caracteres no-ASCII ({muestra}) "
        f"y NO lleva BOM. PowerShell 5.1 lo leera como ANSI y puede romperse."
    )


@pytest.mark.unit
@pytest.mark.parametrize('script', SCRIPTS, ids=lambda p: p.name)
def test_un_ps1_no_lleva_comillas_tipograficas(script):
    """PowerShell las trata como delimitador de cadena, asi que una comilla
    curva colada por un editor o un copiar-pegar abre una cadena de verdad."""
    texto = script.read_bytes().decode('utf-8', errors='replace')
    curvas = {c for c in texto if c in '‘’“”'}
    assert not curvas, (
        f"{script.name} contiene comillas tipograficas {curvas!r}: PowerShell "
        "las interpreta como delimitador de cadena"
    )


# ── Parseo real, cuando hay PowerShell disponible ────────────────────────

_PWSH = shutil.which('powershell') or shutil.which('pwsh')


@pytest.mark.integration
@pytest.mark.skipif(not _PWSH, reason='no hay PowerShell en esta maquina')
@pytest.mark.parametrize('script', SCRIPTS, ids=lambda p: p.name)
def test_un_ps1_parsea_de_verdad(script):
    """La comprobacion definitiva: se lo damos al parser de PowerShell tal y
    como lo leeria al ejecutarlo, sin forzar codificacion."""
    comando = (
        '$e = $null; '
        f'$null = [System.Management.Automation.Language.Parser]::ParseFile('
        f"'{script}', [ref]$null, [ref]$e); "
        'if ($e) { $e | ForEach-Object { '
        '"linea " + $_.Extent.StartLineNumber + ": " + $_.Message }; exit 1 }'
    )
    r = subprocess.run([_PWSH, '-NoProfile', '-Command', comando],
                       capture_output=True, text=True, timeout=120)

    assert r.returncode == 0, f"{script.name} no parsea:\n{r.stdout}{r.stderr}"
