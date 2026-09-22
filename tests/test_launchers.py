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
