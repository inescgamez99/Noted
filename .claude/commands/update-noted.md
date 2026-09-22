Antes de hacer nada, comprueba si hay una grabación activa ejecutando este bloque PowerShell:

```powershell
$_tr_dir = Split-Path (Resolve-Path 'tr_env.ps1') -Parent
. "$_tr_dir\tr_env.ps1"
if (Test-TRRecording -Root $_tr_dir) { Write-Output "RECORDING_ACTIVE" }
```

Si el output contiene "RECORDING_ACTIVE", detente aquí y avisa al usuario: "Hay una reunión en curso. Espera a que termine antes de actualizar para no perder la grabación."

Si no hay grabación activa, haz git pull en este proyecto (Noted) y muestra un resumen de los cambios descargados. Si ya estaba al día, indícalo claramente.
