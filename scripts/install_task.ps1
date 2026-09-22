param([string]$WatchdogPath = "")

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
    -MultipleInstances IgnoreNew

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

Write-Host "Tarea 'Noted' registrada para $env:USERDOMAIN\$env:USERNAME"
Write-Host "Triggers: $triggerDesc"
Write-Host "Watchdog: $WatchdogPath"
