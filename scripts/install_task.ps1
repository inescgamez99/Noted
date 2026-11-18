param([string]$WatchdogPath = "")

# Si Register-ScheduledTask (o cualquier otro paso) falla, el script tiene que salir
# con codigo != 0: antes terminaba con 0 y instalar.bat anunciaba "Tarea registrada"
# sin que lo estuviera.
$ErrorActionPreference = 'Stop'
trap {
    Write-Host "ERROR registrando la tarea: $_"
    exit 1
}

if (-not $WatchdogPath) {
    # La raiz del repo: este script vive en scripts/, watchdog.ps1 en la raiz.
    $WatchdogPath = Join-Path (Split-Path $PSScriptRoot -Parent) "watchdog.ps1"
}
$WatchdogPath = (Resolve-Path $WatchdogPath).Path

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$WatchdogPath`""

# Trigger 1: al iniciar sesión interactiva
# 60 s en lugar de 10 s: con 10 s, el subsistema de audio y el perfil de usuario
# aun no estan listos y la tarea falla (LastTaskResult != 0) la mayoria de boots frios.
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$logonTrigger.Delay = "PT60S"

# Trigger 2: al despertar de suspensión/hibernación
# EventID 1 de Microsoft-Windows-Power-Troubleshooter indica que el sistema se wake
$wakeQuery = '<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name=''Microsoft-Windows-Power-Troubleshooter''] and EventID=1]]</Select></Query></QueryList>'
$wakeClass = Get-CimClass -Namespace 'Root/Microsoft/Windows/TaskScheduler' -ClassName 'MSFT_TaskEventTrigger' -ErrorAction SilentlyContinue
$triggers = @($logonTrigger)
if ($wakeClass) {
    $wakeTrigger = New-CimInstance -CimClass $wakeClass -ClientOnly -Property @{
        Enabled      = $true
        Subscription = $wakeQuery
        Delay        = 'PT20S'
    }
    $triggers += $wakeTrigger
    $triggerDesc = "AtLogOn (60s) + wake-from-sleep (20s)"
} else {
    $triggerDesc = "AtLogOn (60s)  [wake trigger no disponible en este sistema]"
}

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 3)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName "Noted" `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

# Tarea heartbeat: verifica cada 30 min si el watchdog sigue vivo y lo relanza si no.
# watchdog.ps1 sale solo si detecta que ya hay otro corriendo, asi que es seguro llamarlo periodicamente.
$hbTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$hbSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries

Register-ScheduledTask `
    -TaskName "Noted-Heartbeat" `
    -Action $action `
    -Trigger $hbTrigger `
    -Settings $hbSettings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Tarea 'Noted' registrada para $env:USERDOMAIN\$env:USERNAME"
Write-Host "Tarea 'Noted-Heartbeat' registrada (cada 30 min)"
Write-Host "Triggers: $triggerDesc"
Write-Host "Watchdog: $WatchdogPath"
