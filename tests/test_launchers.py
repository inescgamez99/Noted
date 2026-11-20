"""Los lanzadores tienen que apuntar a ficheros que existan.

INCIDENTE QUE ORIGINO ESTOS TESTS (22/09/2026)
El refactor 647ac36 movio los lanzadores a `scripts/` pero dejo `watchdog.ps1`
y `main.py` en la raiz. Cada lanzador calculaba su carpeta con UN nivel de
`GetParentFolderName` / `$PSScriptRoot`, asi que a partir del movimiento
construian rutas a `scripts/watchdog.ps1` y `scripts/main.py`, que no existen.

Nada lo detecto. Son ficheros que no compilan, no se importan y no aparecen en
ninguna suite: la unica forma de enterarse era instalar de cero y descubrir que
el watchdog no arranca. Cinco ficheros estaban rotos a la vez.

La comprobacion es la obvia y no se le habia hecho nunca: coger la ruta que el
lanzador construye de verdad, y mirar si ese fichero existe.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / 'scripts'

# Lo que cada lanzador tiene que acabar ejecutando.
DESTINOS = {
    'start_watchdog.vbs': 'watchdog.ps1',
    'start_silent.vbs': 'main.py',
}


@pytest.mark.unit
@pytest.mark.parametrize('lanzador, destino', sorted(DESTINOS.items()))
def test_el_destino_del_lanzador_existe_donde_se_dice(lanzador, destino):
    """Red de seguridad barata: si alguien vuelve a mover el destino, esto
    salta aunque no haya cscript delante."""
    assert (RAIZ / destino).exists(), (
        f"{lanzador} espera {destino} en la raiz del repo y no esta. "
        f"Si se ha movido a proposito, hay que actualizar el lanzador."
    )


@pytest.mark.unit
@pytest.mark.parametrize('lanzador', sorted(DESTINOS))
def test_un_vbs_en_scripts_sube_un_nivel_para_llegar_a_la_raiz(lanzador):
    """LA COMPROBACION QUE HABRIA EVITADO EL INCIDENTE sin depender de nada.

    El .vbs vive en scripts/ y su destino en la raiz, asi que necesita DOS
    GetParentFolderName anidados. Con uno solo apunta dentro de scripts/.
    """
    texto = (SCRIPTS / lanzador).read_text(encoding='utf-8-sig')
    anidado = re.search(
        r'GetParentFolderName\(\s*\w*\.?GetParentFolderName\(', texto)
    assert anidado, (
        f"{lanzador} resuelve su carpeta con un solo GetParentFolderName: "
        f"apunta a scripts/ en vez de a la raiz del repo"
    )


@pytest.mark.unit
def test_install_task_registra_el_watchdog_de_la_raiz():
    """Mismo fallo en PowerShell: $PSScriptRoot es scripts/, no la raiz."""
    texto = (SCRIPTS / 'install_task.ps1').read_text(encoding='utf-8-sig')
    assert re.search(r'Split-Path\s+\$PSScriptRoot\s+-Parent', texto), (
        "install_task.ps1 usa $PSScriptRoot directamente: registraria la tarea "
        "apuntando a scripts/watchdog.ps1, que no existe"
    )


@pytest.mark.unit
def test_install_task_lanza_el_watchdog_sin_ventana():
    """Con Windows Terminal por defecto (Windows 11), -WindowStyle Hidden se
    ignora y el watchdog queda como una ventana negra vacia: cerrarla lo mata.
    conhost --headless da una consola sin ninguna ventana."""
    texto = (SCRIPTS / 'install_task.ps1').read_text(encoding='utf-8-sig')
    accion = texto[texto.index('$action = New-ScheduledTaskAction'):]
    accion = accion[:accion.index('\n\n')]
    assert re.search(r'-Execute\s+"conhost\.exe"', accion), accion
    assert re.search(r'-Argument\s+"--headless powershell\.exe ', accion), accion
    assert '-File `"$WatchdogPath`"' in accion


@pytest.mark.unit
def test_setup_bat_trabaja_desde_la_raiz():
    """requirements.txt y .env.example estan en la raiz. Con doble clic el cwd
    es scripts/, asi que sin pushd el pip install falla."""
    texto = (SCRIPTS / 'setup.bat').read_text(encoding='utf-8-sig')
    assert 'pushd "%~dp0.."' in texto, "setup.bat no se situa en la raiz"
    assert 'popd' in texto, "setup.bat hace pushd y no deshace"
    assert (RAIZ / 'requirements.txt').exists()


@pytest.mark.unit
def test_diagnostico_pone_la_raiz_en_sys_path():
    """diagnostico.py importa config, que esta en la raiz. Desde tools/ hay que
    subir dos niveles, no uno."""
    texto = (RAIZ / 'tools' / 'diagnostico.py').read_text(encoding='utf-8')
    assert 'parent.parent' in texto, (
        "diagnostico.py inserta tools/ en sys.path en vez de la raiz: "
        "`import config` falla"
    )
    assert (RAIZ / 'config.py').exists()


# -- Ejecucion real, cuando hay cscript ---------------------------------

_CSCRIPT = shutil.which('cscript')


@pytest.mark.integration
@pytest.mark.skipif(not _CSCRIPT, reason='no hay cscript en esta maquina')
@pytest.mark.parametrize('lanzador, destino', sorted(DESTINOS.items()))
def test_un_vbs_construye_una_ruta_que_existe(lanzador, destino, tmp_path):
    """La comprobacion definitiva: se ejecuta el .vbs de verdad, con el Run
    sustituido por un Echo, y se mira la ruta que habria lanzado.

    La copia va en un subdirectorio de tmp_path que imita la estructura del
    repo, para que los GetParentFolderName tengan de donde subir.
    """
    falso_scripts = tmp_path / 'scripts'
    falso_scripts.mkdir()

    texto = (SCRIPTS / lanzador).read_text(encoding='utf-8-sig')
    texto = re.sub(r'(?:CreateObject\("WScript\.Shell"\)|WshShell)\.Run\s',
                   'WScript.Echo ', texto)
    texto = re.sub(r',\s*0,\s*False', '', texto)
    copia = falso_scripts / lanzador
    copia.write_text(texto, encoding='ascii')

    r = subprocess.run([_CSCRIPT, '//nologo', str(copia)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"el .vbs no corre:\n{r.stdout}{r.stderr}"

    salida = r.stdout.strip()
    assert str(tmp_path / destino) in salida, (
        f"{lanzador} apunta a otro sitio.\n"
        f"  esperado que contenga: {tmp_path / destino}\n"
        f"  lanzaria:              {salida}"
    )


# ── Instalacion: instalar.bat / install_task.ps1 ────────────────────────────
# INCIDENTES: (1) install_task.ps1 no tenia $ErrorActionPreference='Stop', asi que
# un Register-ScheduledTask fallido acababa con exit 0 e instalar.bat decia
# "Tarea registrada" sin que lo estuviera. (2) instalar.bat usaba `where python`
# y el alias de WindowsApps (la Tienda) cuenta como encontrado, aunque no es un
# Python (tr_env.ps1 ya lo filtraba). (3) Una segunda instalacion desde otra
# carpeta pisaba install_path.txt y la tarea programada de la primera.

INSTALAR = SCRIPTS / 'instalar.bat'
INSTALL_TASK = SCRIPTS / 'install_task.ps1'


def _bat() -> str:
    return INSTALAR.read_text(encoding='utf-8-sig').replace('\r\n', '\n')


@pytest.mark.unit
def test_install_task_sale_con_error_si_el_registro_falla():
    texto = INSTALL_TASK.read_text(encoding='utf-8-sig')
    assert re.search(r"\$ErrorActionPreference\s*=\s*'Stop'", texto), (
        "sin ErrorActionPreference=Stop, Register-ScheduledTask puede fallar y el "
        "script acabar con exit 0")
    assert re.search(r'trap\s*\{[^}]*exit\s+1', texto, re.DOTALL), (
        "un error tiene que acabar en exit 1 explicito")


@pytest.mark.unit
def test_instalar_bat_comprueba_el_registro_de_la_tarea_y_lo_dice():
    texto = _bat()
    bloque = texto[texto.index('install_task.ps1'):]
    bloque = bloque[:bloque.index('post-merge')]
    assert 'if errorlevel 1' in bloque
    assert 'set "RC=1"' in bloque, "un registro fallido tiene que dar exit code != 0"
    assert 'ERROR' in bloque.split('else')[0]


@pytest.mark.unit
def test_instalar_bat_no_usa_where_python_sin_filtrar_windowsapps():
    texto = _bat()
    assert 'WindowsApps' in texto
    # el unico `where python` que queda es el de la rutina que filtra
    for m in re.finditer(r'where python', texto):
        linea = texto[texto.rfind('\n', 0, m.start()) + 1: texto.find('\n', m.end())]
        if linea.lstrip().lower().startswith('rem '):
            continue
        assert 'for /f' in linea, f"`where python` sin filtrar: {linea.strip()}"


@pytest.mark.unit
def test_instalar_bat_usa_el_python_filtrado_para_crear_el_venv():
    texto = _bat()
    assert '"%PYTHON%" -m venv' in texto
    assert not re.search(r'^\s*python -m venv', texto, re.MULTILINE)


@pytest.mark.unit
def test_instalar_bat_para_si_ya_hay_otra_instalacion():
    texto = _bat()
    assert 'install_path.txt' in texto
    i = texto.index('install_path.txt')
    resto = texto[i:i + 1500]
    assert 'main.py' in resto and 'goto :fail' in resto, (
        "con otra instalacion existente tiene que parar (goto :fail da exit 1 "
        "en /unattended)")
    # y antes de instalar nada
    assert i < texto.index('[1/5]')


_CMD = shutil.which('cmd')


def _trozo(texto: str, desde: str, hasta: str) -> str:
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i)]


@pytest.mark.integration
@pytest.mark.skipif(not _CMD, reason='no hay cmd')
def test_find_python_ignora_el_alias_de_windowsapps(tmp_path):
    """Se ejecuta de verdad la rutina :find_python con un PATH que tiene primero
    un falso WindowsApps\\python.exe y luego un python 'real'."""
    sys32 = Path(shutil.which('where')).parent
    tienda = tmp_path / 'WindowsApps'
    real = tmp_path / 'real'
    for d in (tienda, real):
        d.mkdir()
        shutil.copy(_CMD, d / 'python.exe')
    rutina = _trozo(_bat(), '\n:find_python\n', '\nrem Relee')
    bat = tmp_path / 't.bat'
    bat.write_text('@echo off\nsetlocal enabledelayedexpansion\n'
                   'call :find_python\nif errorlevel 1 (echo NOFOUND) else (echo FOUND=!PYTHON!)\n'
                   'exit /b 0\n' + rutina, encoding='ascii')

    def corre(path):
        return subprocess.run([_CMD, '/c', str(bat)], capture_output=True, text=True,
                              env={'PATH': path, 'SystemRoot': str(sys32.parent)},
                              timeout=60).stdout

    assert f'FOUND={real}' in corre(f'{tienda};{real};{sys32}')
    assert 'NOFOUND' in corre(f'{tienda};{sys32}')


@pytest.mark.integration
@pytest.mark.skipif(not _CMD, reason='no hay cmd')
@pytest.mark.parametrize('hay_main, esperado', [(True, 'PARADO'), (False, 'SIGUE')])
def test_segunda_instalacion_se_para_solo_si_la_otra_existe(tmp_path, hay_main, esperado):
    otra = tmp_path / 'otra'
    otra.mkdir()
    if hay_main:
        (otra / 'main.py').write_text('')
    aqui = tmp_path / 'aqui'
    aqui.mkdir()
    cfg = tmp_path / 'Noted'
    cfg.mkdir()
    (cfg / 'install_path.txt').write_text(str(otra), encoding='ascii')
    bloque = _trozo(_bat(), 'set "PREV="', 'rem ----')
    bat = tmp_path / 't.bat'
    bat.write_text(f'@echo off\nsetlocal enabledelayedexpansion\nset "REPO={aqui}"\n'
                   + bloque + 'echo SIGUE\nexit /b 0\n:fail\necho PARADO\nexit /b 1\n',
                   encoding='ascii')
    r = subprocess.run([_CMD, '/c', str(bat)], capture_output=True, text=True,
                       env={'LOCALAPPDATA': str(tmp_path), 'SystemRoot': r'C:\Windows',
                            'PATH': r'C:\Windows\System32'}, timeout=60)
    assert esperado in r.stdout, r.stdout + r.stderr
    assert r.returncode == (1 if hay_main else 0)
