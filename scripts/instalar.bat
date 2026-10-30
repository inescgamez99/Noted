@echo off
chcp 65001 >nul 2>&1
title Noted - Instalador
setlocal enabledelayedexpansion

rem Modo desatendido, para instalar desde un agente o terminal sin nadie delante:
rem     instalar.bat /unattended        (o NOTED_UNATTENDED=1)
rem Diferencias con el modo normal: no se relanza en otra ventana, no hace
rem pausas, sigue adelante tras instalar Python/Node (refresca el PATH en vez
rem de pedir reabrir la ventana) y NO lanza "claude login", que es interactivo
rem y dejaria colgado a quien lo ejecute. Devuelve exit code 0 si todo fue bien
rem y 1 si algo fallo.
set "UNATTENDED="
if /i "%~1"=="/unattended" set "UNATTENDED=1"
if "%NOTED_UNATTENDED%"=="1" set "UNATTENDED=1"
set "RC=0"

rem -- Relanzarse dentro de cmd /k para que la ventana nunca se cierre sola --
if not defined TR_STARTED if not defined UNATTENDED (
    set TR_STARTED=1
    cmd /k "%~f0"
    exit /b
)

echo.
echo ========================================
echo   Noted - Instalacion
echo ========================================
echo.

rem Raiz del repo (scripts/ esta un nivel por debajo)
set "REPO=%~dp0.."
for %%i in ("%REPO%") do set "REPO=%%~fi"

if not exist "%REPO%\requirements.txt" (
    echo   ERROR: este instalador debe ejecutarse desde la carpeta scripts del
    echo   repo clonado de Noted. No encuentro %REPO%\requirements.txt
    goto :fail
)

rem -------------------------------------------------------
rem 1. Python  (usar "where" para evitar abrir la Tienda de Windows)
rem -------------------------------------------------------
echo [1/5] Comprobando Python...
where python >nul 2>&1
if errorlevel 1 (
    echo   Python no encontrado. Instalando via winget...
    winget install --id Python.Python.3.11 --source winget --silent --accept-package-agreements --accept-source-agreements
    call :refresh_path
    where python >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   No se pudo instalar Python automaticamente.
        echo   Instalalo manualmente desde: https://www.python.org/downloads/
        echo   Marca "Add Python to PATH" durante la instalacion, cierra
        echo   esta ventana y vuelve a ejecutar instalar.bat
        echo.
        goto :fail
    )
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   Python %%v encontrado.
echo.

rem -------------------------------------------------------
rem 2. Node.js
rem -------------------------------------------------------
echo [2/5] Comprobando Node.js...
where npm >nul 2>&1
if errorlevel 1 (
    echo   Node.js no encontrado. Instalando via winget...
    winget install --id OpenJS.NodeJS.LTS --source winget --silent --accept-package-agreements --accept-source-agreements
    call :refresh_path
    where npm >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   No se pudo instalar Node.js automaticamente.
        echo   Instalalo manualmente desde: https://nodejs.org/ ^(version LTS^)
        echo   Cierra esta ventana y vuelve a ejecutar instalar.bat
        echo.
        goto :fail
    )
)
for /f %%v in ('npm --version 2^>^&1') do echo   npm %%v encontrado.
echo.

rem -------------------------------------------------------
rem 3. Claude CLI
rem -------------------------------------------------------
echo [3/5] Comprobando Claude CLI...
where claude >nul 2>&1
if errorlevel 1 (
    echo   Claude CLI no encontrado. Instalando...
    call npm install -g @anthropic-ai/claude-code
    if errorlevel 1 (
        echo.
        echo   No se pudo instalar Claude CLI.
        echo   Intentalo manualmente: npm install -g @anthropic-ai/claude-code
        echo.
        goto :fail
    )
    echo   Claude CLI instalado.
    rem Anadir %APPDATA%\npm al PATH de esta sesion por si acaba de instalarse
    set "PATH=%APPDATA%\npm;%PATH%"
) else (
    for /f "tokens=*" %%v in ('claude --version 2^>^&1') do echo   Claude CLI %%v encontrado.
)
echo.

rem -------------------------------------------------------
rem 4. Entorno virtual + dependencias Python
rem -------------------------------------------------------
echo [4/5] Instalando dependencias Python...
set "VENV=%REPO%\.venv"
set "PYEXE=%VENV%\Scripts\python.exe"

if not exist "%PYEXE%" (
    echo   Creando entorno virtual .venv...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo.
        echo   ERROR: no se pudo crear el entorno virtual.
        goto :fail
    )
)

"%PYEXE%" -m pip install --upgrade pip --quiet
"%PYEXE%" -m pip install -r "%REPO%\requirements.txt"
if errorlevel 1 (
    echo.
    echo   ERROR instalando dependencias. Revisa el mensaje anterior.
    goto :fail
)

if not exist "%REPO%\.env" (
    copy "%REPO%\.env.example" "%REPO%\.env" >nul
    echo   Creado .env desde plantilla.
)
echo.

rem -------------------------------------------------------
rem 5. Arranque automatico (Task Scheduler) + hook de actualizacion
rem -------------------------------------------------------
echo [5/5] Registrando arranque automatico...
powershell.exe -NonInteractive -ExecutionPolicy Bypass -File "%REPO%\scripts\install_task.ps1"
if errorlevel 1 (
    echo.
    echo   AVISO: no se pudo registrar la tarea. Puedes hacerlo mas tarde
    echo   ejecutando: scripts\install_autostart.bat
    echo.
) else (
    echo   Tarea "Noted" registrada en el Programador de tareas.
)

rem El hook post-merge reinicia Noted tras cada /update-noted. Sin el, el pull
rem actualiza el codigo pero la app sigue con la version vieja hasta reiniciarla.
if exist "%REPO%\.git\hooks" if exist "%REPO%\hooks\post-merge" (
    copy /y "%REPO%\hooks\post-merge" "%REPO%\.git\hooks\post-merge" >nul
    echo   Hook post-merge instalado ^(/update-noted reinicia Noted solo^).
)
echo.

rem -------------------------------------------------------
rem Listo
rem -------------------------------------------------------
echo ========================================
echo   Noted instalado correctamente.
echo ========================================
echo.
if defined UNATTENDED (
    echo   Modo desatendido: se omite "claude login". Si aun no has iniciado
    echo   sesion con Claude, ejecuta en un terminal:  claude login
) else (
    echo   Autenticando Claude CLI ^(se abrira el navegador^)...
    echo   Vincula tu cuenta de Anthropic para que las minutas funcionen.
    echo.
    rem "call": si claude es el wrapper claude.cmd de npm, sin call el .bat se
    rem entrega a el y no vuelve, y Noted no llega a arrancarse.
    call claude login
    if errorlevel 1 (
        echo.
        echo   AVISO: La autenticacion no se completo. Puedes hacerlo mas tarde
        echo   abriendo un terminal y ejecutando:  claude login
        echo.
    )
)
echo.

rem -------------------------------------------------------
rem Arrancar Noted ahora
rem -------------------------------------------------------
echo   Arrancando Noted en segundo plano...
if defined UNATTENDED (
    rem Start-TRDaemon pide el watchdog al Explorador: lanzado como hijo de este
    rem terminal, Noted moriria al cerrarse quien ejecuta el instalador.
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ". '%REPO%\tr_env.ps1'; if (Start-TRDaemon -Root '%REPO%') { exit 0 } else { exit 1 }"
    if errorlevel 1 echo   AVISO: Noted no ha arrancado todavia. Revisa noted.log en la carpeta del repo.
) else (
    start "" powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File "%REPO%\watchdog.ps1"
)
echo   Listo. Busca el icono de Noted en la bandeja del sistema
echo   ^(esquina inferior derecha, puede estar oculto bajo la flecha de arriba^)
echo.
echo   Para actualizar Noted en el futuro, abre Claude Code en esta
echo   carpeta y escribe:  /update-noted
echo.
goto :end

:fail
set "RC=1"

:end
if defined UNATTENDED exit /b %RC%
echo   Pulsa cualquier tecla para cerrar esta ventana.
pause >nul
exit /b %RC%

rem Relee el PATH de maquina y usuario del registro: lo que winget acaba de
rem instalar no esta en el PATH de esta sesion, y sin esto habria que cerrar la
rem ventana y volver a ejecutar el instalador.
:refresh_path
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "[Environment]::ExpandEnvironmentVariables([Environment]::GetEnvironmentVariable('Path','Machine')+';'+[Environment]::GetEnvironmentVariable('Path','User'))"`) do set "PATH=%%p"
exit /b 0
