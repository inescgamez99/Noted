# Reinicia el daemon de Noted de forma segura.
# Usar SIEMPRE este script en lugar de comandos WMI/CIM ad-hoc —
# los metodos Win32_Process.Create disparan alertas de seguridad en entornos corporativos.

$root = Split-Path $PSScriptRoot -Parent
$venv = Join-Path $root ".venv\Scripts\python.exe"
$main = Join-Path $root "main.py"

# ── GUARDRAIL: no reiniciar si hay una grabacion activa ──────────────────────
$sentinel = Join-Path $root ".recording_active"
if (Test-Path $sentinel) {
    $sentinelPid = (Get-Content $sentinel -ErrorAction SilentlyContinue).Trim()
    $isAlive = $false
    if ($sentinelPid) {
        try {
            $proc = Get-Process -Id ([int]$sentinelPid) -ErrorAction Stop
            $isAlive = $true
        } catch { }
    }
    if ($isAlive) {
        Write-Host ""
        Write-Host "  *** GRABACION ACTIVA — NO SE PUEDE REINICIAR ***" -ForegroundColor Red
        Write-Host "  Espera a que termine la reunion antes de reiniciar Noted." -ForegroundColor Yellow
        Write-Host ""
        exit 1
    } else {
        # Sentinel obsoleto (proceso murio sin limpiar) — ignorar y continuar
        Remove-Item $sentinel -Force -ErrorAction SilentlyContinue
    }
}

# Detener instancia existente
$lock = Join-Path $root ".lock"
if (Test-Path $lock) {
    $pid_in_lock = (Get-Content $lock -ErrorAction SilentlyContinue).Trim()
    if ($pid_in_lock) {
        Stop-Process -Id $pid_in_lock -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}

# Matar cualquier proceso python que tenga main.py en el titulo (por si acaso)
Get-Process python, pythonw -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -like '*Noted*' } |
    Stop-Process -Force -ErrorAction SilentlyContinue

Start-Sleep -Seconds 1

# Arrancar con Start-Process (NO WMI / Invoke-CimMethod)
Start-Process -FilePath $venv -ArgumentList "`"$main`"" -WorkingDirectory $root -WindowStyle Hidden
Write-Host "Noted arrancado."
