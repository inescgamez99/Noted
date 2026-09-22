@echo off
rem Trabajar desde la raiz del repo: este .bat vive en scripts/, pero
rem requirements.txt y .env.example estan un nivel mas arriba.
pushd "%~dp0.."
setlocal

rem -- Usar el .venv del repo si existe, crearlo si no --
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
    echo Usando entorno virtual existente (.venv)
) else (
    echo Creando entorno virtual (.venv)...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: no se pudo crear el entorno virtual.
        echo Comprueba que Python esta instalado y en PATH.
        pause
        exit /b 1
    )
    set "PYTHON=.venv\Scripts\python.exe"
    echo Entorno virtual creado.
)

echo.
echo Instalando dependencias de Noted...
%PYTHON% -m pip install --upgrade pip --quiet
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR instalando dependencias. Revisa el mensaje anterior.
    pause
    exit /b 1
)

if not exist .env (copy .env.example .env && echo Creado .env desde plantilla)

echo.
echo Verificando imports clave...
%PYTHON% -c "import sounddevice; print('sounddevice OK')"
%PYTHON% -c "import faster_whisper; print('faster-whisper OK')"
%PYTHON% -c "import pystray; print('pystray OK')"
%PYTHON% -c "import pyaudiowpatch; print('pyaudiowpatch OK')"
echo.
echo Verificando claude CLI...
claude --version
if errorlevel 1 (
    echo [AVISO] claude CLI no encontrado. Ejecuta:
    echo   npm install -g @anthropic-ai/claude-code
    echo   claude login
) else (
    echo claude CLI OK
)
echo.
echo Setup completado.
popd
pause
