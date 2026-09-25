@echo off
setlocal EnableDelayedExpansion
title Noted — Claude Code Setup

echo.
echo  ==========================================
echo    NOTED — Claude Code Setup
echo    No admin rights required
echo  ==========================================
echo.

REM ── Already installed? ────────────────────────────────────────────────────
where claude >nul 2>&1
if %errorlevel% == 0 (
    echo  [OK] Claude Code is already installed.
    echo.
    goto :auth
)

set "TOOLS=%USERPROFILE%\noted-tools"
set "NODE_DIR=%TOOLS%\node"
set "NPM_BIN=%APPDATA%\npm"

REM ── Node.js already on system? ────────────────────────────────────────────
where npm >nul 2>&1
if %errorlevel% == 0 (
    echo  [OK] Node.js found on this machine. Skipping download.
    goto :install_claude
)

REM ── Portable Node.js already downloaded? ──────────────────────────────────
if exist "%NODE_DIR%\node.exe" (
    echo  [OK] Portable Node.js already present.
    set "PATH=%PATH%;%NODE_DIR%;%NPM_BIN%"
    goto :install_claude
)

REM ── Download portable Node.js ─────────────────────────────────────────────
echo  [1/2] Downloading Node.js (portable, no installation needed)...
echo        This may take 1-2 minutes on a corporate network.
echo.

powershell -NoProfile -Command ^
  "try { $v = ((Invoke-RestMethod 'https://nodejs.org/dist/index.json') | Where-Object { $_.lts } | Select-Object -First 1).version; $url = \"https://nodejs.org/dist/$v/node-$v-win-x64.zip\"; Write-Host \"  Downloading Node.js $v...\"; Invoke-WebRequest -Uri $url -OutFile \"$env:TEMP\node_portable.zip\" -UseBasicParsing } catch { Write-Host \"  Falling back to known version...\"; Invoke-WebRequest -Uri 'https://nodejs.org/dist/v22.11.0/node-v22.11.0-win-x64.zip' -OutFile \"$env:TEMP\node_portable.zip\" -UseBasicParsing }"

if not exist "%TEMP%\node_portable.zip" (
    echo.
    echo  ERROR: Download failed. Check your internet connection or ask IT
    echo  to install Node.js from: https://nodejs.org
    echo.
    pause
    exit /b 1
)

echo  Extracting...
if not exist "%TOOLS%" mkdir "%TOOLS%"

powershell -NoProfile -Command ^
  "Expand-Archive -Path \"$env:TEMP\node_portable.zip\" -DestinationPath \"$env:USERPROFILE\noted-tools\_tmp\" -Force; $sub = (Get-ChildItem \"$env:USERPROFILE\noted-tools\_tmp\" -Directory | Select-Object -First 1).FullName; if (Test-Path \"$env:USERPROFILE\noted-tools\node\") { Remove-Item \"$env:USERPROFILE\noted-tools\node\" -Recurse -Force }; Move-Item $sub \"$env:USERPROFILE\noted-tools\node\"; Remove-Item \"$env:USERPROFILE\noted-tools\_tmp\" -Recurse -Force; Remove-Item \"$env:TEMP\node_portable.zip\" -Force"

if not exist "%NODE_DIR%\node.exe" (
    echo  ERROR: Extraction failed.
    pause
    exit /b 1
)

REM Add to user PATH permanently (no admin needed)
powershell -NoProfile -Command ^
  "$cur = [Environment]::GetEnvironmentVariable('PATH','User'); if ($cur -notlike '*noted-tools*') { [Environment]::SetEnvironmentVariable('PATH', $cur + ';%NODE_DIR%;%NPM_BIN%', 'User') }"

set "PATH=%PATH%;%NODE_DIR%;%NPM_BIN%"
echo  Node.js ready.
echo.

REM ── Install Claude Code ───────────────────────────────────────────────────
:install_claude
echo  [2/2] Installing Claude Code...
echo.

if exist "%NODE_DIR%\npm.cmd" (
    "%NODE_DIR%\npm.cmd" install -g @anthropic-ai/claude-code
) else (
    npm install -g @anthropic-ai/claude-code
)

if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Installation failed. Try running this file as Administrator
    echo  (right-click ^> Run as administrator).
    echo.
    pause
    exit /b 1
)

set "PATH=%PATH%;%NPM_BIN%"

echo.
echo  ==========================================
echo    Claude Code installed!
echo  ==========================================

REM ── Authenticate ─────────────────────────────────────────────────────────
:auth
echo.
echo  Last step: log in with your Anthropic account.
echo  A browser window will open. Sign in and come back here.
echo.
pause
claude
echo.
echo  ==========================================
echo    All done! You can close this window.
echo  ==========================================
echo.
pause
