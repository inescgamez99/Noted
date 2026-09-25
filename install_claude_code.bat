@echo off
setlocal EnableDelayedExpansion
title Noted — Install Claude Code

echo.
echo  ==========================================
echo    NOTED — Claude Code Setup
echo  ==========================================
echo.

REM ── Check if claude is already installed ──────────────────────────────────
where claude >nul 2>&1
if %errorlevel% == 0 (
    echo  [OK] Claude Code is already installed.
    claude --version
    echo.
    goto :auth
)

REM ── Check if npm is already available ─────────────────────────────────────
where npm >nul 2>&1
if %errorlevel% == 0 (
    echo  [OK] Node.js found. Skipping Node install.
    goto :install_claude
)

REM ── Install Node.js via winget ─────────────────────────────────────────────
echo  [1/2] Installing Node.js...
echo.
winget install OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Could not install Node.js automatically.
    echo  Please install it manually from: https://nodejs.org
    echo  Then run this script again.
    echo.
    pause
    exit /b 1
)

REM ── Refresh PATH so npm is available in this session ──────────────────────
echo.
echo  Refreshing environment...
for /f "tokens=*" %%A in ('powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable(\"PATH\",\"Machine\")"') do set "SysPath=%%A"
for /f "tokens=*" %%A in ('powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable(\"PATH\",\"User\")"') do set "UserPath=%%A"
set "PATH=%SysPath%;%UserPath%"

REM ── Install Claude Code ────────────────────────────────────────────────────
:install_claude
echo.
echo  [2/2] Installing Claude Code...
echo.
npm install -g @anthropic-ai/claude-code
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: npm install failed.
    echo  Try closing this window, reopening as Administrator, and running again.
    echo.
    pause
    exit /b 1
)

echo.
echo  ==========================================
echo    Claude Code installed successfully!
echo  ==========================================

REM ── Authenticate ──────────────────────────────────────────────────────────
:auth
echo.
echo  Last step: log in with your Anthropic account.
echo  A browser window will open — sign in and come back here.
echo.
pause
claude
echo.
echo  ==========================================
echo    All done! You can close this window.
echo  ==========================================
echo.
pause
