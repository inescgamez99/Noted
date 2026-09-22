---
name: install-noted
description: Instala Noted en Windows por primera vez. Noted detecta llamadas de Microsoft Teams automáticamente, graba el audio y genera minutas estructuradas con IA (Whisper + Claude). Úsalo cuando alguien acaba de clonar el repo y quiere instalarlo.
---

# Noted — Instalación

Esta skill es exclusiva para Windows. Si el usuario no está en Windows, infórmale y termina.

## Reglas que gobiernan todo el flujo

1. **La ruta de instalación nunca se asume.** Cada usuario clona donde quiere. Resuélvela en el Paso 0 y úsala a través de `$TR` en todos los pasos siguientes.
2. **Nunca ejecutes `git clone` sin haber confirmado que no hay instalación previa.** Un segundo clon deja dos apps compitiendo por el mismo `.lock`, dos watchdogs y grabaciones duplicadas.
3. **El watchdog es el único que arranca la app.** No lances `main.py` por tu cuenta: el watchdog lo relanza solo, y arrancarlo a mano crea una segunda instancia.

---

## Paso 0 — Localizar el repo clonado

```powershell
$candidates = @(
    $env:NOTED_HOME,
    (Get-Content "$env:LOCALAPPDATA\Noted\install_path.txt" -Raw -ErrorAction SilentlyContinue),
    "$env:USERPROFILE\Documents\Noted",
    "$env:USERPROFILE\repos\noted",
    "$env:USERPROFILE\source\repos\noted",
    "$env:USERPROFILE\git\noted",
    "$env:USERPROFILE\Noted"
)
$TR = $null
foreach ($c in $candidates) {
    if ([string]::IsNullOrWhiteSpace($c)) { continue }
    $c = $c.Trim()
    if ((Test-Path (Join-Path $c '.git')) -and (Test-Path (Join-Path $c 'main.py'))) {
        $TR = (Resolve-Path $c).Path
        break
    }
}
if ($TR) { "ENCONTRADO: $TR" } else { "NO ENCONTRADO" }
```

- Si imprime `ENCONTRADO: <ruta>` → esa es `$TR`. Continúa con el Paso 1.
- Si imprime `NO ENCONTRADO` → pregunta al usuario dónde ha clonado el repo. Si da una ruta, verifícala con el bloque de arriba. Si no lo ha clonado todavía, indícale que lo haga primero:
  ```
  git clone https://github.com/inescgamez99/Noted.git %USERPROFILE%\Documents\Noted
  ```
  y luego vuelve a ejecutar el bloque de localización.

---

## Paso 1 — Crear el entorno virtual e instalar dependencias

El repo trabaja con un `.venv` propio (está en `.gitignore`). Instalar en el Python del sistema deja la app sin sus paquetes.

```powershell
python -m venv (Join-Path $TR ".venv")
& (Join-Path $TR ".venv\Scripts\python.exe") -m pip install --upgrade pip --quiet
& (Join-Path $TR ".venv\Scripts\python.exe") -m pip install -r (Join-Path $TR "requirements.txt")
```

Si hay errores de dependencias, muéstralos al usuario y ayúdale a resolverlos.

Copia también el `.env` si no existe:

```powershell
if (-not (Test-Path (Join-Path $TR ".env"))) {
    Copy-Item (Join-Path $TR ".env.example") (Join-Path $TR ".env")
}
```

---

## Paso 2 — Iniciar sesión en Claude Code

La app usa `claude -p` (Claude Code CLI) para generar las minutas. Comprueba si ya está disponible:

```powershell
claude --version
```

Si el comando no existe, instálalo:

```powershell
npm install -g @anthropic-ai/claude-code
```

Luego autentícate (abre el navegador):

```powershell
claude login
```

Espera a que el usuario confirme que `claude login` ha ido bien antes de continuar.

---

## Paso 3 — Configurar el arranque automático con Windows

```powershell
powershell.exe -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $TR "scripts\install_task.ps1")
```

Esto registra una tarea en el **Programador de tareas de Windows** con el nombre "Noted" que arranca el watchdog 60 segundos después de iniciar sesión.

Instala también el hook de actualización automática:

```powershell
Copy-Item (Join-Path $TR "hooks\post-merge") (Join-Path $TR ".git\hooks\post-merge") -Force
```

El hook `post-merge` reinicia la app automáticamente tras cada `/update-noted`.

---

## Paso 4 — Arrancar la app por primera vez

```powershell
Start-Process powershell.exe -ArgumentList "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $TR 'watchdog.ps1')`""
```

Espera 5 segundos y verifica que está corriendo:

```powershell
Start-Sleep 5
Get-Process | Where-Object { $_.MainWindowTitle -like '*Noted*' -or $_.Name -eq 'python' } | Select-Object Id, Name
```

---

## Paso 5 — Confirmar instalación

Informa al usuario:
- El icono de Noted aparecerá en la bandeja del sistema (esquina inferior derecha, puede estar oculto bajo la flecha `^`)
- La próxima vez que entre en una reunión de Teams, aparecerá un popup preguntando si quiere grabar
- Para actualizar en el futuro: abrir Claude Code en esta carpeta y escribir `/update-noted`
