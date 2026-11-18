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
import re
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


# ── La resolucion del directorio del watchdog ────────────────────────────
#
# INCIDENTE (23/09/2026). watchdog.ps1 moria en cada arranque sin dejar rastro:
# ni proceso, ni una sola linea WATCHDOG en el log. La app se quedaba sin red
# de seguridad y nadie se enteraba. Venia roto desde 38b133b (21/09), el mismo
# commit titulado "watchdog robusto".
#
# La causa es una trampa de PowerShell. El script elegia su directorio asi:
#
#     foreach ($candidate in @(
#         $PSScriptRoot,
#         (Split-Path -Parent $PSCommandPath),
#         (Split-Path -Parent $MyInvocation.MyCommand.Path),
#         (Split-Path -Parent $MyInvocation.ScriptName)   # <-- vacio con -File
#     )) { ... }
#
# `Split-Path -Parent ''` es un error de BINDING DE PARAMETRO, y eso termina la
# sentencia entera: el array nunca se construye y el foreach no itera — ni
# siquiera sobre $PSScriptRoot, que era correcto. $dir se quedaba en null, y
# como $logf se deriva de $dir, el fallo no se podia ni registrar.
#
# $MyInvocation.ScriptName esta vacio precisamente cuando se invoca con -File,
# que es como lo lanzan start_watchdog.vbs y la tarea programada. O sea:
# fallaba siempre en produccion y solo funcionaba si otro script lo llamaba.

WATCHDOG = REPO / 'watchdog.ps1'


@pytest.mark.unit
def test_el_watchdog_no_evalua_split_path_dentro_de_un_literal():
    """LA COMPROBACION BARATA, sin PowerShell: ningun Split-Path puede vivir
    dentro de un literal @(...). Si su argumento viene vacio, el error de
    binding se lleva por delante la construccion del array entero."""
    texto = WATCHDOG.read_text(encoding='utf-8-sig')
    for literal in re.findall(r'in\s+@\((.*?)\)\s*\)\s*\{', texto, re.DOTALL):
        assert 'Split-Path' not in literal, (
            "Split-Path dentro de un literal @(...): con un argumento vacio "
            "aborta la sentencia y $dir se queda en null"
        )


@pytest.mark.unit
def test_el_watchdog_filtra_cada_fuente_antes_de_usarla():
    """No basta con que falte lo malo: tiene que estar lo bueno. Cada fuente
    se comprueba ANTES de pasarla a Split-Path."""
    texto = WATCHDOG.read_text(encoding='utf-8-sig')
    assert re.search(r'IsNullOrWhiteSpace\((\$\w+)\)\)\s*\{[^}]*Split-Path -Parent \1', texto), (
        "no se ve el filtro previo de cada fuente; sin el, el bug vuelve"
    )


@pytest.mark.integration
@pytest.mark.skipif(not _PWSH, reason='no hay PowerShell en esta maquina')
def test_el_watchdog_resuelve_su_directorio_invocado_con_File(tmp_path):
    """LA COMPROBACION DE VERDAD, y reproduce el incidente exacto: se invoca
    con -File —como lo lanzan start_watchdog.vbs y la tarea programada, y el
    unico modo en que $MyInvocation.ScriptName viene vacio— y se mira si $dir
    sale con valor.

    Se monta una carpeta que imita la raiz del repo (el script junto a un
    tr_env.ps1) y se corta el script justo antes de su bucle infinito.
    Con el codigo de 38b133b esto no llega ni a escribir el fichero.
    """
    (tmp_path / 'tr_env.ps1').write_text('# marcador', encoding='utf-8')

    texto = WATCHDOG.read_text(encoding='utf-8-sig')
    corte = texto.find('$mainpy = Join-Path $dir')
    assert corte > 0, "no se encontro donde cortar el script"

    salida = tmp_path / 'dir.txt'
    sonda = tmp_path / 'sonda.ps1'
    sonda.write_text(
        texto[:corte]
        + '\n"$dir" | Out-File -LiteralPath "' + str(salida) + '" -Encoding utf8\n',
        encoding='utf-8-sig')

    r = subprocess.run(
        [_PWSH, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(sonda)],
        capture_output=True, text=True, timeout=120)

    assert 'ParameterArgumentValidationError' not in (r.stdout + r.stderr), (
        "sigue abortando la construccion del array:\n" + r.stdout + r.stderr)
    assert salida.exists(), f"la sonda no llego al final:\n{r.stdout}{r.stderr}"

    resuelto = salida.read_text(encoding='utf-8-sig').strip()
    assert resuelto == str(tmp_path), (
        f"$dir no resolvio a la carpeta del script.\n"
        f"  esperado: {tmp_path}\n  obtenido: '{resuelto}'")


# ── El watchdog no se fia de un PID reciclado ───────────────────────────────
# INCIDENTE: tras reiniciar el PC, un .lock viejo con un PID que Windows habia
# reciclado para otro programa (cualquier proceso vivo) hacia que el watchdog
# diera el daemon por vivo y esperase indefinidamente: Noted no arrancaba.
# tr_env.ps1 (Get-TRLivePid) y main._check_single_instance si comprueban que
# el proceso sea Python.

@pytest.mark.unit
def test_el_watchdog_valida_el_pid_del_lock_con_Get_TRLivePid():
    texto = WATCHDOG.read_text(encoding='utf-8-sig')
    assert re.search(r'Get-TRLivePid\s+\$lockf', texto), (
        "el watchdog tiene que decidir si el .lock es de un daemon vivo con "
        "Get-TRLivePid (comprueba que el proceso sea Python)")
    assert not re.search(r'\$alive\s*=\s*Get-Process\s+-Id\s+\$pid_in_lock', texto), (
        "Get-Process -Id sobre el PID del lock sin comprobar el nombre del proceso")
