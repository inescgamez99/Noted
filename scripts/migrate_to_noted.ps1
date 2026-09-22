# migrate_to_noted.ps1 — ejecutar UNA VEZ desde la carpeta TeamsRecorder
# Actualiza la URL del remote y renombra la carpeta local a Noted

$ErrorActionPreference = 'Stop'

$here   = (Get-Location).Path
$parent = Split-Path $here -Parent
$name   = Split-Path $here -Leaf

Write-Host ""
Write-Host "=== Migracion TeamsRecorder -> Noted ===" -ForegroundColor Cyan

# 1. Remote URL
Write-Host "`n[1/3] Actualizando remote de git..."
git remote set-url origin https://github.com/inescgamez99/Noted.git
Write-Host "      OK -> https://github.com/inescgamez99/Noted.git" -ForegroundColor Green

# 2. install_path.txt
$newPath   = Join-Path $parent "Noted"
$configDir = Join-Path $env:LOCALAPPDATA "Noted"
New-Item -ItemType Directory -Force $configDir | Out-Null
[System.IO.File]::WriteAllText("$configDir\install_path.txt", $newPath)
Write-Host "`n[2/3] install_path.txt actualizado -> $newPath" -ForegroundColor Green

# 3. Renombrar carpeta (hay que salir de ella primero)
Write-Host "`n[3/3] Renombrando carpeta..."
if ($name -eq "TeamsRecorder") {
    Set-Location $parent
    Rename-Item "TeamsRecorder" "Noted"
    Write-Host "      OK: $parent\TeamsRecorder -> $parent\Noted" -ForegroundColor Green
    Write-Host "`nAbre una terminal nueva en: $newPath" -ForegroundColor Yellow
} else {
    Write-Host "      La carpeta ya se llama '$name', no hay que renombrarla." -ForegroundColor Yellow
}

Write-Host "`nMigracion completada. Reinicia el daemon desde la nueva carpeta." -ForegroundColor Cyan
Write-Host ""
