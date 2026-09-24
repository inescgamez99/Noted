# Noted Watchdog — reinicia el daemon si se cae

# ── Resolución robusta del directorio de instalación ─────────────────────────
# $PSScriptRoot es la variable más fiable (PowerShell 3+, establecida por el
# engine al cargar el archivo). Usamos varias fuentes en cascada para cubrir
# escenarios donde Task Scheduler no establece $MyInvocation correctamente.
$dir = $null
foreach ($candidate in @(
    $PSScriptRoot,
    (Split-Path -Parent $PSCommandPath),
    (Split-Path -Parent $MyInvocation.MyCommand.Path),
    (Split-Path -Parent $MyInvocation.ScriptName)
)) {
    if (-not [string]::IsNullOrWhiteSpace($candidate) -and
        (Test-Path (Join-Path $candidate 'tr_env.ps1'))) {
        $dir = $candidate
        break
    }
}

# Último recurso: config guardada por tr_env.ps1 (Save-TRRoot)
if (-not $dir) {
    $savedCfg = Join-Path $env:LOCALAPPDATA 'Noted\install_path.txt'
    if (Test-Path $savedCfg) {
        $saved = (Get-Content $savedCfg -Raw -ErrorAction SilentlyContinue).Trim()
        if (-not [string]::IsNullOrWhiteSpace($saved) -and
            (Test-Path (Join-Path $saved 'tr_env.ps1'))) {
            $dir = $saved
        }
    }
}

# Exploración de ubicaciones conocidas
if (-not $dir) {
    foreach ($cand in @(
        (Join-Path $env:USERPROFILE 'Documents\Noted'),
        (Join-Path $env:USERPROFILE 'repos\noted'),
        (Join-Path $env:USERPROFILE 'source\repos\noted'),
        (Join-Path $env:USERPROFILE 'git\noted'),
        (Join-Path $env:USERPROFILE 'Noted')
    )) {
        if (Test-Path (Join-Path $cand 'tr_env.ps1')) {
            $dir = $cand
            break
        }
    }
}

$mainpy = Join-Path $dir "main.py"
$lockf  = Join-Path $dir ".lock"
$logf   = Join-Path $dir "noted.log"

# Log definido antes de cargar tr_env.ps1 para capturar cualquier fallo de arranque
function Log($msg) {
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $logf -Value "$ts WATCHDOG: $msg" -Encoding UTF8
}

if (-not $dir) {
    # Sin $logf válido no podemos escribir en el log; escribir al menos al EventLog
    try {
        $src = 'Noted'
        if (-not [System.Diagnostics.EventLog]::SourceExists($src)) {
            [System.Diagnostics.EventLog]::CreateEventSource($src, 'Application')
        }
        [System.Diagnostics.EventLog]::WriteEntry($src,
            'Watchdog no pudo determinar el directorio de instalacion. Abortando.',
            [System.Diagnostics.EventLogEntryType]::Error)
    } catch {}
    exit 1
}

Log "Watchdog iniciado (PID $PID) desde dir=$dir"

try {
    . (Join-Path $dir "tr_env.ps1")
} catch {
    Log "ERROR cargando tr_env.ps1: $_"
    exit 1
}

# Actualizar install_path.txt con la ruta correcta encontrada en este arranque.
# Evita que un install_path.txt obsoleto (p.ej. tras renombrar el directorio)
# rompa el fallback en el siguiente arranque sin $PSScriptRoot.
Save-TRRoot $dir

$pyEnv = Get-TRPython -Root $dir
if (-not $pyEnv) { Log "ERROR: no se encontro python"; exit 1 }

# Salir si ya hay otro watchdog corriendo para esta instalacion
$existing = @(Get-TRWatchdogProcess -Root $dir | Where-Object { $_.ProcessId -ne $PID })
if ($existing.Count -gt 0) {
    Log "Otro watchdog ya corre (PIDs: $($existing.ProcessId -join ', ')). Saliendo."
    exit 0
}

$python = $pyEnv.Pythonw
Log "Python: $python (origen: $($pyEnv.Source))"

while ($true) {
    # Limpiar lock huerfano
    if (Test-Path $lockf) {
        $pid_in_lock = Get-Content $lockf -ErrorAction SilentlyContinue
        if ($pid_in_lock) {
            $alive = Get-Process -Id $pid_in_lock -ErrorAction SilentlyContinue
            if (-not $alive) {
                Remove-Item $lockf -Force -ErrorAction SilentlyContinue
                Log "Lock huerfano eliminado (PID $pid_in_lock)"
            } else {
                Log "Lock valido — daemon PID $pid_in_lock ya corre. Watchdog en espera pasiva."
                # No lanzar otro daemon; esperar a que el existente muera.
                # WaitForExit() puede lanzar excepcion si el proceso no fue iniciado
                # por este watchdog (no tenemos el handle). Fallback: polling cada 5s.
                try {
                    $alive.WaitForExit()
                } catch {
                    Log "WaitForExit fallo (sin handle); usando polling cada 5s: $_"
                    while (Get-Process -Id $pid_in_lock -ErrorAction SilentlyContinue) {
                        Start-Sleep -Seconds 5
                    }
                }
                Log "Daemon PID $pid_in_lock termino (exit $($alive.ExitCode)). Reiniciando en 5s..."
                Start-Sleep -Seconds 5
                continue
            }
        }
    }

    $startTime = Get-Date
    Log "Iniciando daemon... (pythonw=$python)"
    $proc = Start-Process -FilePath $python `
        -ArgumentList "`"$mainpy`"" `
        -PassThru `
        -WorkingDirectory $dir
    if (-not $proc) {
        Log "ERROR: Start-Process no devolvio un objeto de proceso. Reintentando en 15s..."
        Start-Sleep -Seconds 15
        continue
    }
    Log "Daemon arrancado PID=$($proc.Id)"
    $proc.WaitForExit()
    $exitCode = $proc.ExitCode
    $elapsed  = [int]((Get-Date) - $startTime).TotalSeconds
    Log "Daemon PID=$($proc.Id) termino tras ${elapsed}s (exit $exitCode). Reiniciando en 5s..."
    Start-Sleep -Seconds 5
}
