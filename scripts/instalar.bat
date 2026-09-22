@echo off
chcp 65001 >nul 2>&1
title Noted - Instalador
setlocal enabledelayedexpansion

rem -- Relanzarse dentro de cmd /k para que la ventana nunca se cierre sola --
if not defined TR_STARTED (
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

rem -------------------------------------------------------
rem 1. Python  (usar "where" para evitar abrir la Tienda de Windows)
rem -------------------------------------------------------
echo [1/5] Comprobando Python...
where python >nul 2>&1
if errorlevel 1 (
    echo   Python no encontrado. Instalando via winget...
    winget install --id Python.Python.3.11 --source winget --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo.
        echo   No se pudo instalar Python automaticamente.
        echo   Instalalo manualmente desde: https://www.python.org/downloads/
        echo   Marca "Add Python to PATH" durante la instalacion, cierra
        echo   esta ventana y vuelve a hacer doble clic en instalar.bat
        echo.
        goto :end
    )
    echo   Python instalado. Abre una nueva ventana y vuelve a ejecutar instalar.bat
    echo   para que Windows reconozca la instalacion.
    goto :end
) else (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   Python %%v encontrado.
)
echo.

rem -------------------------------------------------------
rem 2. Node.js
rem -------------------------------------------------------
echo [2/5] Comprobando Node.js...
where npm >nul 2>&1
if errorlevel 1 (
    echo   Node.js no encontrado. Instalando via winget...
    winget install --id OpenJS.NodeJS.LTS --source winget --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo.
        echo   No se pudo instalar Node.js automaticamente.
        echo   Instalalo manualmente desde: https://nodejs.org/ ^(version LTS^)
        echo   Cierra esta ventana y vuelve a hacer doble clic en instalar.bat
        echo.
        goto :end
    )
    echo   Node.js instalado. Abre una nueva ventana y vuelve a ejecutar instalar.bat.
    goto :end
) else (
    for /f %%v in ('npm --version 2^>^&1') do echo   npm %%v encontrado.
)
echo.

rem -------------------------------------------------------
rem 3. Claude CLI
rem -------------------------------------------------------
echo [3/5] Comprobando Claude CLI...
where claude >nul 2>&1
if errorlevel 1 (
    echo   Claude CLI no encontrado. Instalando...
    npm install -g @anthropic-ai/claude-code
    if errorlevel 1 (
        echo.
        echo   No se pudo instalar Claude CLI.
        echo   Intentalo manualmente: npm install -g @anthropic-ai/claude-code
        echo.
        goto :end
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
        goto :end
    )
)

"%PYEXE%" -m pip install --upgrade pip --quiet
"%PYEXE%" -m pip install -r "%REPO%\requirements.txt"
if errorlevel 1 (
    echo.
    echo   ERROR instalando dependencias. Revisa el mensaje anterior.
    goto :end
)

if not exist "%REPO%\.env" (
    copy "%REPO%\.env.example" "%REPO%\.env" >nul
    echo   Creado .env desde plantilla.
)
echo.

rem -------------------------------------------------------
rem 5. Arranque automatico (Task Scheduler)
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
echo.

rem -------------------------------------------------------
rem Listo
rem -------------------------------------------------------
echo ========================================
echo   Noted instalado correctamente.
echo ========================================
echo.
echo   SIGUIENTE PASO OBLIGATORIO - autenticate en Claude Code:
echo.
echo       claude login
echo.
echo   Esto abre el navegador para vincular tu cuenta de Claude.
echo   Sin este paso las minutas no se generaran.
echo.
pause

rem -------------------------------------------------------
rem Arrancar Noted ahora (sin esperar al proximo login)
rem -------------------------------------------------------
echo.
echo   Arrancando Noted en segundo plano...
start "" powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File "%REPO%\watchdog.ps1"
echo   Listo. Busca el icono de Noted en la bandeja del sistema
echo   ^(esquina inferior derecha, puede estar bajo la flecha ^^^)^)
echo.
echo   Para actualizar Noted en el futuro, abre Claude Code en esta
echo   carpeta y escribe:  /noted
echo.

:end
echo   Pulsa cualquier tecla para cerrar esta ventana.
pause >nul
