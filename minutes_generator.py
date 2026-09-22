import logging
import os
import re
import subprocess
import threading
from pathlib import Path

from config import PROJECT_DIR, CLAUDE_BIN as _CLAUDE_BIN, clean_env as _clean_env

log = logging.getLogger(__name__)

_MAX_TRANSCRIPT_CHARS = 150_000

_SYSTEM_PROMPT_BASE = """Eres un asistente especializado en generar minutas profesionales de reuniones de trabajo tecnologico.

Tu output es siempre markdown estructurado, limpio y listo para usar directamente.
{language_instruction}

## PRIMERA LINEA OBLIGATORIA

La primera linea de tu respuesta debe ser SIEMPRE exactamente en este formato (sin markdown, sin #):
TITULO: [3-5 palabras que describan el tema principal, en el idioma de las minutas]

Ejemplos validos:
TITULO: Planificacion Sprint Octubre
TITULO: Revision Diseño App Movil
TITULO: Q3 Project Kickoff
TITULO: Backend Team Daily Standup
TITULO: Daily Standup Equipo Backend
TITULO: Cierre Trimestre Ventas

Despues del titulo: una linea en blanco y luego el markdown completo de las minutas.

## CABECERA FIJA DE METADATOS

La primera linea del cuerpo de las minutas (justo despues del TITULO y la linea en blanco) debe ser SIEMPRE la linea de metadatos que te indican en el prompt del usuario. Nunca muevas la fecha/hora/duracion a otra seccion ni la omitas. Nunca inventes un formato de cabecera distinto.

## Seccion de Acciones Pendientes — regla critica

Hay UNA SOLA seccion para todas las acciones: "Acciones Pendientes" (o "Pending Actions" en ingles).
No uses secciones separadas de "Next Steps", "Cambios Tecnicos" ni similares.

### Regla de AGRUPACION (muy importante)

Agrupa en UNA SOLA accion todas las sub-tareas que afecten al MISMO entregable, archivo u objetivo.
NO crees una fila por cada pequeño cambio.

- Ejemplo: si hay que editar 5 cosas del mismo HTML deck, es UNA sola accion
  ("Editar el HTML deck: X, Y, Z...") con los 5 puntos como sub-pasos DENTRO de su
  bloque tecnico — NO 5 filas separadas en la tabla.
- Separa en acciones/filas distintas SOLO cuando sean entregables u objetivos
  claramente independientes, o cuando tengan distinto responsable o distinta fecha limite.
- En la tabla aparece UNA fila por accion agrupada, con una descripcion que resuma el conjunto.
- Los sub-pasos concretos van DENTRO del bloque tecnico correspondiente (Parte 2),
  como una lista con viñetas (checklist).

Esta seccion tiene dos partes:

### Parte 1: Tabla de acciones (TODAS las acciones de la reunion)

| Accion | Responsable | Fecha limite |
|--------|-------------|--------------|
| [descripcion concisa] | [nombre o "-"] | [fecha o "-"] |

Incluye en la tabla TODAS las acciones acordadas: manuales, tecnicas, y las que Claude puede ejecutar.
Para las acciones que Claude puede ejecutar, añade "(Claude)" junto al responsable en la tabla.
Ejemplo: | Refactorizar modulo de autenticacion | Ana (Claude) | 15 Jun |

### Parte 2: Bloques tecnicos (uno por cada accion que Claude puede ejecutar)

Despues de la tabla, incluye un bloque por cada accion marcada como "(Claude)" en la tabla.
Usa el formato adecuado segun el tipo:

Caso A — Cambio de codigo / configuracion:
~~~[lenguaje]
// ARCHIVO: ruta/del/archivo.ext   (si se menciono; sino escribe "A determinar")
// CONTEXTO: Que hace este cambio y por que se acordo en la reunion
// INSTRUCCION PARA CLAUDE CODE: [imperativo directo]

[codigo o configuracion exacta mencionada, o la mejor aproximacion]
~~~

Caso B — Instruccion tecnica sin codigo concreto (UI, diseño, arquitectura, etc.):
~~~instruction-for-claude
[Instruccion directa lista para Claude Code. Debe:
 - Empezar con verbo imperativo (Crea, Modifica, Añade, Elimina, Refactoriza...)
 - Referenciar archivos o componentes especificos si se mencionaron
 - Ser lo suficientemente especifica para ser accionable
 - Si la accion AGRUPA varios cambios sobre el mismo entregable, listalos como
   checklist de sub-pasos:
     - [ ] sub-paso 1
     - [ ] sub-paso 2]
~~~

Caso C — Cambio en documento Office (Word, Excel, PowerPoint):
~~~document-change
// ARCHIVO: nombre-del-archivo.ext
// CONTEXTO: Descripcion del cambio
// INSTRUCCION: [Descripcion precisa del cambio a realizar en el documento]
~~~

Si una accion tecnica no tiene suficiente detalle, incluye el bloque de todas formas y anota:
"DETALLE INSUFICIENTE EN REUNION: [lo que falta]"

Todos los bloques deben ser autocontenidos: quien los lea debe poder actuar sin contexto adicional.

ATRIBUCION Y DATOS NO DICHOS (regla estricta):
El transcripto es texto plano SIN etiquetas de interlocutor: no hay diarizacion,
asi que no existe informacion sobre quien dijo cada cosa.
- No atribuyas afirmaciones, compromisos ni decisiones a una persona concreta
  salvo que el propio transcripto lo diga de forma explicita (alguien se presenta,
  se dirige a otro por su nombre, o se cita "X dijo que...").
- Si lo deduces por contexto pero no esta dicho, redactalo en impersonal
  ("se acordo", "se planteo", "queda pendiente") o marcalo como
  "(atribucion inferida)". Nunca lo presentes como hecho.
- Lo mismo con fechas, horas y cifras: si no aparecen en el transcripto, escribe
  "sin concretar". No deduzcas una hora ni una fecha a partir del contexto.
- En la seccion de asistentes, lista solo los nombres que aparezcan en el
  transcripto, e indica que la lista puede estar incompleta."""


_LANG_INSTRUCTIONS = {
    'auto': (
        "Detect the language of the meeting from the transcript content and write the minutes "
        "entirely in that same language. If the meeting is clearly multilingual, use the predominant language."
    ),
    'en': "Write always in English, even if some parts of the meeting were in another language.",
    'es': "Escribe siempre en español, aunque alguna parte de la reunion sea en otro idioma.",
    'ca': "Escriu sempre en català, encara que alguna part de la reunió sigui en un altre idioma.",
}
_LANG_SECTIONS = {
    'en': [
        "Executive Summary",
        "Attendees",
        "Topics Discussed",
        "Decisions Made",
        "Pending Actions (first: table with ALL actions — columns: Action | Owner | Deadline; mark Claude-executable actions with '(Claude)' next to the owner. Then: one technical block per Claude-executable action, in the formats described above)",
        "Additional Notes",
    ],
    'es': [
        "Resumen Ejecutivo",
        "Asistentes",
        "Temas Tratados",
        "Decisiones Tomadas",
        "Acciones Pendientes (primero: tabla con TODAS las acciones — columnas: Acción | Responsable | Fecha límite; marca las ejecutables por Claude con '(Claude)' junto al responsable. Después: un bloque técnico por cada acción Claude, en los formatos descritos arriba)",
        "Notas Adicionales",
    ],
    'ca': [
        "Resum Executiu",
        "Assistents",
        "Temes Tractats",
        "Decisions Preses",
        "Accions Pendents (primer: taula amb TOTES les accions — columnes: Acció | Responsable | Data límit; marca les executables per Claude amb '(Claude)' al costat del responsable. Després: un bloc tècnic per cada acció Claude, en els formats descrits anteriorment)",
        "Notes Addicionals",
    ],
}

def _get_system_prompt(language: str = 'auto') -> str:
    lang = language if language in _LANG_INSTRUCTIONS else 'auto'
    instruction = _LANG_INSTRUCTIONS[lang]
    return _SYSTEM_PROMPT_BASE.format(language_instruction=instruction)


def _build_prompt(transcript: str, recording_path: Path, extra_context: str | None = None, language: str = 'auto', me_name: str = '') -> str:
    m = re.match(r'(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})', recording_path.stem)
    fecha = m.group(1) if m else 'desconocida'
    hora = f"{m.group(2)}:{m.group(3)}" if m else 'desconocida'

    duracion = 'desconocida'
    times = re.findall(r'\[(\d{2}):(\d{2})\]', transcript)
    if times:
        last_m, last_s = int(times[-1][0]), int(times[-1][1])
        duracion = f"{last_m}m {last_s}s"

    lang = language if language in _LANG_SECTIONS else 'en'
    sections = _LANG_SECTIONS[lang]
    if lang == 'en':
        section_label = "Required minutes structure"
        date_label = f"Date: {fecha}  Start time: {hora}  Estimated duration: {duracion}"
        ctx_label = "## Additional context from user:"
    elif lang == 'ca':
        section_label = "Estructura requerida de les actes"
        date_label = f"Data: {fecha}  Hora d'inici: {hora}  Durada estimada: {duracion}"
        ctx_label = "## Context addicional de l'usuari:"
    else:
        section_label = "Estructura requerida de las minutas"
        date_label = f"Fecha: {fecha}  Hora de inicio: {hora}  Duración estimada: {duracion}"
        ctx_label = "## Contexto adicional del usuario:"

    if lang == 'en':
        header_block = f"**Date:** {fecha} | **Start:** {hora} | **Est. duration:** {duracion}"
    elif lang == 'ca':
        header_block = f"**Data:** {fecha} | **Inici:** {hora} | **Durada estimada:** {duracion}"
    else:
        header_block = f"**Fecha:** {fecha} | **Inicio:** {hora} | **Duración estimada:** {duracion}"

    header_instruction = (
        "After the TITULO line (and a blank line), the FIRST line of the minutes body must be exactly:"
        if lang == 'en' else
        "Después de la línea TITULO (y una línea en blanco), la PRIMERA línea del cuerpo de las minutas debe ser exactamente:"
    )

    recorder_note = ''
    if me_name:
        if lang == 'en':
            recorder_note = (
                f"\nThe person who recorded this meeting is **{me_name}**. "
                f"In the transcript, [{me_name}] refers to them. Use their real name throughout the minutes."
            )
        else:
            recorder_note = (
                f"\nLa persona que grabó esta reunión es **{me_name}**. "
                f"En el transcript, [{me_name}] se refiere a ella. Usa su nombre real en toda la minuta."
            )

    if len(transcript) > _MAX_TRANSCRIPT_CHARS:
        log.warning(
            f"Transcript truncado: {len(transcript)} → {_MAX_TRANSCRIPT_CHARS} chars"
        )
        transcript = (
            transcript[:_MAX_TRANSCRIPT_CHARS]
            + f"\n\n[TRANSCRIPCIÓN TRUNCADA: primeros {_MAX_TRANSCRIPT_CHARS} de {len(transcript)} chars]"
        )

    parts = [
        f"## Context / Contexto",
        date_label + recorder_note,
        "",
        "## Transcript" if lang == 'en' else "## Transcripción",
        transcript,
        "",
        f"## {section_label}",
        f"{header_instruction}",
        f"`{header_block}`",
        "",
        "(Then a blank line, then the sections in this order:)",
    ] + [f"- {s}" for s in sections]

    if extra_context:
        parts.insert(3, f"{ctx_label}\n{extra_context}\n")

    return '\n'.join(parts)


def _generate_via_cli(transcript: str, recording_path: Path, extra_context: str | None = None,
                      language: str = 'auto', context_dir: str | None = None) -> str | None:
    if not _CLAUDE_BIN:
        log.error("claude CLI no encontrado en PATH")
        return None

    try:
        import json as _json
        _cfg = _json.loads((PROJECT_DIR / 'settings.json').read_text(encoding='utf-8'))
        me_name = _cfg.get('user_name', '').strip()
    except Exception:
        me_name = ''
    user_prompt = _build_prompt(transcript, recording_path, extra_context, language, me_name=me_name)
    system_prompt = _get_system_prompt(language).replace('```', '~~~')

    cmd = [_CLAUDE_BIN, '-p']
    if context_dir:
        cmd += ['--add-dir', context_dir, '--allowedTools', 'Read', 'Grep', 'Glob']
        agentic_note = (
            "\n\n## Contexto del proyecto (carpeta añadida)\n"
            "Tienes acceso de solo lectura a una carpeta con la MEMORIA del proyecto: "
            "documentos vinculados (en `docs/`) y resúmenes de reuniones anteriores (en `meetings/`). "
            "Busca (grep) y lee lo que sea relevante para esta reunión para: usar la terminología, "
            "siglas y nombres correctos; dar continuidad con decisiones previas; y ganar precisión. "
            "NO copies ni resumas esos documentos: úsalos solo como conocimiento de fondo."
            if language != 'en' else
            "\n\n## Project context (added folder)\n"
            "You have read-only access to a folder with the project's MEMORY: linked documents "
            "(in `docs/`) and summaries of previous meetings (in `meetings/`). Search (grep) and read "
            "whatever is relevant to this meeting to: use correct terminology, acronyms and names; "
            "keep continuity with previous decisions; and be more precise. Do NOT copy or summarize "
            "those documents: use them only as background knowledge."
        )
        system_prompt = system_prompt + agentic_note

    full_prompt = system_prompt + '\n\n' + user_prompt

    try:
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            CREATE_NO_WINDOW = 0x08000000
        else:
            si = None
            CREATE_NO_WINDOW = 0

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            env=_clean_env(),
            startupinfo=si if os.name == 'nt' else None,
            creationflags=CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        try:
            stdout, stderr = proc.communicate(input=full_prompt, timeout=900)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            log.error("claude -p timeout (900s)")
            return None
        if proc.returncode != 0:
            log.error(f"claude -p error (rc={proc.returncode}): {stderr[:500]}")
            return None
        return stdout.strip()
    except Exception as e:
        log.error(f"Error llamando claude CLI: {e}")
        return None


def regenerate_actions_section(transcript: str, current_actions_md: str,
                               instruction: str, language: str = 'auto') -> str | None:
    """Reescribe SOLO la sección de Acciones Pendientes según la instrucción del usuario.
    Devuelve el markdown de la nueva sección (empezando por '## ...') o None."""
    if not _CLAUDE_BIN:
        log.error("claude CLI no encontrado en PATH")
        return None

    lang = language if language in _LANG_INSTRUCTIONS else 'auto'
    heading = 'Pending Actions' if lang == 'en' else ('Accions Pendents' if lang == 'ca' else 'Acciones Pendientes')
    lang_instruction = _LANG_INSTRUCTIONS[lang]

    prompt = f"""Eres un asistente que edita la seccion de Acciones de una reunion de trabajo.
{lang_instruction}

Te doy el transcript de la reunion, la seccion de Acciones ACTUAL y una instruccion con los cambios que quiere el usuario. Aplica esos cambios y devuelve SOLO la seccion de Acciones reescrita.

## Formato OBLIGATORIO de la respuesta
Empieza EXACTAMENTE con esta linea:
## {heading}

Despues:
1) Una tabla con TODAS las acciones — columnas: | Accion | Responsable | Fecha limite |. Marca las ejecutables por Claude con "(Claude)" junto al responsable.
2) Despues de la tabla, un bloque tecnico por cada accion "(Claude)":
   - Instruccion sin codigo: ~~~instruction-for-claude ... ~~~
   - Cambio en documento Office: ~~~document-change ... ~~~
   - Cambio de codigo: ~~~lenguaje con "// INSTRUCCION PARA CLAUDE CODE:" ... ~~~

## Regla de AGRUPACION (muy importante)
Agrupa en UNA SOLA accion todas las sub-tareas que afecten al MISMO entregable/archivo/objetivo (una fila en la tabla), y pon los sub-pasos como checklist ("- [ ] ...") DENTRO de su bloque tecnico. No crees una fila por cada pequeño cambio.

No incluyas ninguna otra seccion, ni el titulo de la reunion, ni texto extra. Responde solo con el markdown de la seccion de Acciones.

## Transcript
{transcript}

## Seccion de Acciones ACTUAL
{current_actions_md or '(vacía)'}

## Cambios solicitados por el usuario
{instruction}"""

    try:
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            CREATE_NO_WINDOW = 0x08000000
        else:
            si = None
            CREATE_NO_WINDOW = 0

        proc = subprocess.Popen(
            [_CLAUDE_BIN, '-p'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', env=_clean_env(),
            startupinfo=si if os.name == 'nt' else None,
            creationflags=CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        try:
            stdout, stderr = proc.communicate(input=prompt, timeout=900)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.communicate()
            log.error("regenerate_actions_section: timeout (900s)")
            return None
        if proc.returncode != 0:
            log.error(f"regenerate_actions_section: claude -p error (rc={proc.returncode}): {stderr[:300]}")
            return None
        out = (stdout or '').strip()
        if not out:
            return None
        if not out.lstrip().startswith('#'):
            out = f"## {heading}\n\n{out}"
        return out
    except Exception as e:
        log.error(f"regenerate_actions_section: {e}")
        return None


def extract_title_from_minutes(raw_text: str) -> tuple[str, str]:
    lines = raw_text.strip().splitlines()
    if lines and lines[0].startswith('TITULO:'):
        title = lines[0].removeprefix('TITULO:').strip()
        content = '\n'.join(lines[1:]).lstrip('\n')
        return title or 'Reunion', content
    return 'Reunion', raw_text


def save_minutes(minutes: str, output_path: Path) -> bool:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(minutes, encoding='utf-8')
        log.info(f"Minutas guardadas: {output_path}")
        return True
    except Exception as e:
        log.error(f"Error guardando minutas: {e}")
        return False


def generate_minutes(
    transcript: str,
    recording_path: Path,
    extra_context: str | None = None,
    on_complete=None,
    language: str = 'auto',
    context_dir: str | None = None,
) -> str | None:
    """
    Si on_complete es callable, ejecuta en thread daemon y devuelve None.
    Si no, bloquea y devuelve el texto de las minutas.
    context_dir: carpeta del proyecto para acceso agéntico (memoria + documentos).
    """
    def _run():
        log.info(f"Generando minutas con claude CLI (idioma: {language}"
                 f"{', con contexto de proyecto' if context_dir else ''})...")
        result = _generate_via_cli(transcript, recording_path, extra_context, language, context_dir)
        if on_complete:
            on_complete(result)
        return result

    if on_complete:
        t = threading.Thread(target=_run, daemon=True, name='MinutesGenerator')
        t.start()
        return None
    return _run()
