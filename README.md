# Noted

Daemon de Windows que detecta llamadas de Teams automáticamente, graba el audio (micrófono + loopback del sistema), transcribe con Whisper y genera minutas estructuradas usando Claude. Todo se consulta desde un icono en la bandeja del sistema.

## Requisitos

- **Windows 10/11** (64-bit)
- **Python 3.11+**
- **[Claude Code CLI](https://docs.anthropic.com/claude-code)** instalado y autenticado (`claude --version` debe funcionar en el terminal)

## Instalación

```bash
pip install -r requirements.txt
```

## Primera ejecución

```bash
python main.py
```

Aparece un icono de Noted en la bandeja del sistema. Al hacer clic se abre la interfaz web, que lanza el **tour guiado** automáticamente la primera vez. El tour pide idioma y nombre, y muestra las funcionalidades principales.

> **Nota:** La primera transcripción descarga el modelo Whisper `medium` (~1,5 GB). Puede tardar unos minutos dependiendo de la conexión.

## Uso básico

- **Detección automática:** Noted detecta cuando entras en una llamada de Teams y muestra una ventana de confirmación (30 s) para empezar a grabar.
- **Grabación manual:** Clic derecho en el icono de bandeja → *Iniciar grabación*.
- **Minutas:** Cuando termina la llamada, Noted transcribe y genera las minutas automáticamente. Aparecen en la pestaña *Notas*.
- **Acciones:** Las tareas detectadas en la reunión se extraen y muestran en la pestaña *Acciones*.

## Configuración adicional (opcional)

Todas las opciones se pueden ajustar desde la interfaz web (icono de ajustes):

| Ajuste | Descripción |
|--------|-------------|
| Nombre | Se usa para identificar tu voz en el transcript |
| Idioma | Idioma de la interfaz y por defecto de las minutas |
| Modelo Whisper | `medium` es el equilibrio recomendado; `large-v3` es más preciso pero más lento |
| Meeting Coach | Activa consejos de comunicación tras cada reunión |
| Proyectos | Asocia reuniones a proyectos para archivar y buscar por contexto |

Variables de entorno avanzadas en `.env`:
```
WHISPER_MODEL=medium        # tiny | base | small | medium | large-v3
WHISPER_LANGUAGE=es         # omitir para detección automática
OUTPUT_DIR=C:\ruta\salida   # carpeta de minutas y grabaciones (por defecto: carpeta del proyecto)
```

## Desarrollo

Ver `CLAUDE.md` para arquitectura, comandos de desarrollo y guía de contribución.

Para reiniciar el daemon durante desarrollo:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\restart_noted.ps1
```
