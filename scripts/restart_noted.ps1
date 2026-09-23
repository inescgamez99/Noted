# Reinicia el daemon de Noted de forma segura.
# Usar SIEMPRE este script en lugar de comandos WMI/CIM ad-hoc —
# los metodos Win32_Process.Create disparan alertas de seguridad en entornos corporativos.

$root = Split-Path $PSScriptRoot -Parent
$venv = Join-Path $root ".venv\Scripts\python.exe"
$main = Join-Path $root "main.py"

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
Start-Process -FilePath $venv -ArgumentList "`"$main`"" -WorkingDirectory $root
Write-Host "Noted arrancado."
