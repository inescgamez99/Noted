---
name: noted
description: Instala o actualiza Noted en Windows. Noted detecta llamadas de Microsoft Teams automáticamente, graba el audio y genera minutas estructuradas con IA (Whisper + Claude). Úsalo la primera vez para instalar, o cuando el administrador publique una actualización.
---

# Noted — Instalación y actualización

Esta skill es exclusiva para Windows. Si el usuario no está en Windows, infórmale y termina.

## Reglas que gobiernan todo el flujo

1. **La ruta de instalación nunca se asume.** Cada usuario clona donde quiere. Resuélvela en el Paso 0 y úsala a través de `$TR` en todos los pasos siguientes.
2. **Nunca ejecutes `git clone` sin haber confirmado que no hay instalación previa.** Un segundo clon deja dos apps compitiendo por el mismo `.lock`, dos watchdogs y grabaciones duplicadas.
3. **El watchdog es el único que arranca la app.** No lances `main.py` por tu cuenta: el watchdog lo relanza solo, y arrancarlo a mano crea una segunda instancia.
4. **Nunca reinicies la app si hay una grabación en curso.** Se perdería la reunión que está grabando.

---

## Paso 0 — Localizar la instalación

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

- Si imprime `ENCONTRADO: <ruta>` → esa es `$TR`. Ve al **flujo de actualización**.
- Si imprime `NO ENCONTRADO` → **pregunta al usuario** antes de hacer nada:

  > ¿Ya tienes Noted clonado en alguna carpeta? Si sí, dime la ruta. Si no, te lo instalo.

  Si da una ruta, verifícala con el bloque de arriba usando esa ruta como único candidato y continúa con el flujo de actualización. Solo si confirma que **no** lo tiene, ve al flujo de instalación.

Una vez conocida `$TR`, carga las funciones compartidas del repo. Todos los pasos posteriores las usan:

```powershell
. (Join-Path $TR "tr_env.ps1")
Save-TRRoot $TR
```

---

## Flujo de instalación (primera vez)

### 1. Elegir la carpeta y clonar

Pregunta al usuario dónde quiere instalarlo y ofrece `$env:USERPROFILE\Documents\Noted` como sugerencia. Con la ruta confirmada:

```powershell
$TR = "<ruta confirmada por el usuario>"
git clone https://github.com/inescgamez99/noted $TR
. (Join-Path $TR "tr_env.ps1")
Save-TRRoot $TR
```

### 2. Crear el entorno virtual e instalar dependencias

El repo trabaja con un `.venv` propio (está en `.gitignore`). Instalar en el Python del sistema deja la app sin sus paquetes.

```powershell
python -m venv (Join-Path $TR ".venv")
& (Join-Path $TR ".venv\Scripts\python.exe") -m pip install --upgrade pip --quiet
& (Join-Path $TR ".venv\Scripts\python.exe") -m pip install -r (Join-Path $TR "requirements.txt")
```

Si hay errores de dependencias, muéstralos al usuario y ayúdale a resolverlos.

### 3. Iniciar sesión en Claude Code

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

No se necesita ninguna API key en el `.env`. La autenticación de Claude Code es suficiente.

Opcionalmente, crea un `.env` para ajustar el modelo de Whisper:

```powershell
Copy-Item (Join-Path $TR ".env.example") (Join-Path $TR ".env")
```

Los únicos campos relevantes son:
- `WHISPER_MODEL` — Recomendado: `medium`. Opciones: `tiny`, `base`, `small`, `medium`, `large`.
- `WHISPER_LANGUAGE` — `es` para español, `en` para inglés, o vacío para autodetección.

Espera a que el usuario confirme que `claude login` ha ido bien antes de continuar.

### 4. Configurar el arranque automático con Windows

```powershell
Start-Process -FilePath (Join-Path $TR "scripts\install_autostart.bat") -Wait -WorkingDirectory $TR
```

Esto registra una tarea en el **Programador de tareas de Windows** (Task Scheduler) con el nombre "Noted" que arranca el watchdog 60 segundos después de iniciar sesión. Si había un lanzador VBS antiguo en la carpeta de Inicio, lo elimina automáticamente.

### 4b. Instalar el hook de actualización

```powershell
Copy-Item (Join-Path $TR "hooks\post-merge") (Join-Path $TR ".git\hooks\post-merge") -Force
```

El hook `post-merge` reinicia la app automáticamente tras cada `git pull`.

**El hook `pre-push` NO se instala por defecto.** Envía un email de novedades a todos los destinatarios de `team/recipients.txt` en cada push, y solo debe usarlo quien mantiene el repo. Instálalo únicamente si el usuario confirma explícitamente que es el mantenedor:

```powershell
Copy-Item (Join-Path $TR "hooks\pre-push") (Join-Path $TR ".git\hooks\pre-push") -Force
```

### 5. Arrancar la app por primera vez

```powershell
Start-TRDaemon -Root $TR
Get-TRDaemonProcess -Root $TR | Select-Object ProcessId, Name
```

`Start-TRDaemon` devuelve `True` si el daemon quedó corriendo. Si devuelve `False`, revisa `noted.log` en `$TR`.

### 6. Confirmar instalación

Informa al usuario:
- El icono de Noted aparecerá en la bandeja del sistema (esquina inferior derecha, puede estar oculto bajo la flecha `^`)
- La próxima vez que entre en una reunión de Teams, aparecerá un popup preguntando si quiere grabar
- Para actualizar en el futuro, solo tiene que ejecutar `/noted` de nuevo

---

## Flujo de actualización (ya instalado)

### 1. Comprobar que no hay una grabación en curso

```powershell
Test-TRRecording -Root $TR
```

Si devuelve `True`, **para aquí**. Dile al usuario que hay una reunión grabándose y que vuelva a lanzar `/noted` cuando termine.

### 2. Asegurar que el hook de actualización está instalado

Se instala **antes** del pull, para que sea él quien haga el reinicio en este mismo pull.

```powershell
$hookInstalled = Test-Path (Join-Path $TR ".git\hooks\post-merge")
Copy-Item (Join-Path $TR "hooks\post-merge") (Join-Path $TR ".git\hooks\post-merge") -Force
$hookInstalled
```

Guarda el valor de `$hookInstalled`: indica si el hook ya existía y por tanto se ejecutará en el pull del paso siguiente.

### 3. Actualizar el código

```powershell
git -C $TR pull --ff-only
```

Se usa `--ff-only` a propósito: si el clon del usuario tiene commits locales, es mejor que el pull falle de forma visible que crear un merge silencioso. Si falla por divergencia, muestra `git -C $TR status` al usuario y pregúntale antes de tocar nada.

Muestra al usuario qué ficheros han cambiado.

### 4. Reiniciar la app (solo si el hook no lo hizo ya)

Si `$hookInstalled` era `True`, el hook `post-merge` ya actualizó las dependencias y reinició la app durante el pull: **no repitas nada**, solo verifica.

Si era `False` (primera vez que se instala el hook), reinicia a mano:

```powershell
& (Join-Path $TR "restart_after_update.ps1")
```

Verificación final en ambos casos:

```powershell
Get-TRDaemonProcess -Root $TR | Select-Object ProcessId, Name
Get-Content (Join-Path $TR "noted.log") -Tail 5
```

Debe haber uno o dos procesos (el daemon y el proceso de su interfaz) y un `Noted iniciado` reciente en el log. Confirma al usuario que está actualizado y corriendo con la última versión.
