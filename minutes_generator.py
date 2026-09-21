import logging
import os
import re
import subprocess
import threading
from pathlib import Path

from config import PROJECT_DIR, CLAUDE_BIN as _CLAUDE_BIN, clean_env as _clean_env
from user_minutes_template import (
    get_active_template,
    actions_section_label,
    actions_section_description,
)

log = logging.getLogger(__name__)

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
 - Ser lo suficientemente especifica para ser accionable]
~~~

Caso C — Cambio en documento Office (Word, Excel, PowerPoint):
~~~document-change
// ARCHIVO: nombre-del-archivo.ext
// CONTEXTO: Descripcion del cambio
// INSTRUCCION: [Descripcion precisa del cambio a realizar en el documento]
~~~

Si una accion tecnica no tiene suficiente detalle, incluye el bloque de todas formas y anota:
"DETALLE INSUFICIENTE EN REUNION: [lo que falta]"

Todos los bloques deben ser autocontenidos: quien los lea debe poder actuar sin contexto adicional."""


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
        "Open Questions & Risks (unresolved questions raised, decisions still pending, and any risks/blockers mentioned. Use a bullet list. If there are none, write 'None')",
        "Additional Notes",
    ],
    'es': [
        "Resumen Ejecutivo",
        "Asistentes",
        "Temas Tratados",
        "Decisiones Tomadas",
        "Acciones Pendientes (primero: tabla con TODAS las acciones — columnas: Acción | Responsable | Fecha límite; marca las ejecutables por Claude con '(Claude)' junto al responsable. Después: un bloque técnico por cada acción Claude, en los formatos descritos arriba)",
        "Preguntas Abiertas y Riesgos (preguntas sin resolver que se plantearon, decisiones aún pendientes, y riesgos o bloqueos mencionados. Usa una lista con viñetas. Si no hay, escribe 'Ninguno')",
        "Notas Adicionales",
    ],
    'ca': [
        "Resum Executiu",
        "Assistents",
        "Temes Tractats",
        "Decisions Preses",
        "Accions Pendents (primer: taula amb TOTES les accions — columnes: Acció | Responsable | Data límit; marca les executables per Claude amb '(Claude)' al costat del responsable. Després: un bloc tècnic per cada acció Claude, en els formats descrits anteriorment)",
        "Preguntes Obertes i Riscos (preguntes sense resoldre que es van plantejar, decisions encara pendents, i riscos o bloquejos esmentats. Utilitza una llista amb vinyetes. Si no n'hi ha, escriu 'Cap')",
        "Notes Addicionals",
    ],
}

def _get_system_prompt(language: str = 'auto') -> str:
    lang = language if language in _LANG_INSTRUCTIONS else 'auto'
    instruction = _LANG_INSTRUCTIONS[lang]
    return _SYSTEM_PROMPT_BASE.format(language_instruction=instruction)


def _build_prompt(transcript: str, recording_path: Path, extra_context: str | None = None, language: str = 'auto') -> str:
    m = re.match(r'(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})', recording_path.stem)
    fecha = m.group(1) if m else 'desconocida'
    hora = f"{m.group(2)}:{m.group(3)}" if m else 'desconocida'

    duracion = 'desconocida'
    times = re.findall(r'\[(\d{2}):(\d{2})\]', transcript)
    if times:
        last_m, last_s = int(times[-1][0]), int(times[-1][1])
        duracion = f"{last_m}m {last_s}s"

    lang = language if language in _LANG_SECTIONS else 'en'

    user_tpl = get_active_template()
    if user_tpl:
        log.info(f"user_minutes: plantilla personalizada activa ({len(user_tpl.get('sections') or [])} secciones)")
        # Secciones del usuario + seccion de acciones bloqueada al final.
        # Se envian como bullets "Nombre — descripcion" para dar contexto al modelo.
        user_sections = list(user_tpl.get('sections') or [])
        actions_entry = {
            'name': actions_section_label(lang),
            'description': actions_section_description(lang),
        }
        sections_data = user_sections + [actions_entry]
        sections = [
            (f"{s['name']} — {s['description']}" if s.get('description') else s['name'])
            for s in sections_data
        ]
    else:
        sections_data = None
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

    parts = [
        f"## Context / Contexto",
        date_label,
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

    if user_tpl:
        preamble = {
            'en': (
                "\n## User-personalized structure\n"
                "The sections above were defined by this user. Follow their names, order, and "
                "per-section descriptions strictly. Keep the locked rules from the system prompt "
                "(TITULO line, metadata header, and the Pending Actions section with its ~~~ blocks)."
            ),
            'ca': (
                "\n## Estructura personalitzada per l'usuari\n"
                "Les seccions anteriors les ha definit aquest usuari. Segueix els seus noms, ordre "
                "i descripcions estrictament. Mante les regles bloquejades del system prompt "
                "(linia TITULO, capçalera de metadades, i la seccio Accions Pendents amb els blocs ~~~)."
            ),
            'es': (
                "\n## Estructura personalizada por el usuario\n"
                "Las secciones anteriores las ha definido este usuario. Sigue estrictamente sus "
                "nombres, orden y descripciones. Mantén las reglas bloqueadas del system prompt "
                "(línea TITULO, cabecera de metadatos, y la sección Acciones Pendientes con sus bloques ~~~)."
            ),
        }.get(lang, "")
        parts.append(preamble)
        extra_rules = (user_tpl.get('extra_rules') or '').strip()
        if extra_rules:
            rules_header = {
                'en': "\n## User's additional rules\n",
                'ca': "\n## Regles addicionals de l'usuari\n",
                'es': "\n## Reglas adicionales del usuario\n",
            }.get(lang, "\n## User's additional rules\n")
            parts.append(rules_header + extra_rules)

    if extra_context:
        parts.insert(3, f"{ctx_label}\n{extra_context}\n")

    return '\n'.join(parts)


def _generate_via_cli(transcript: str, recording_path: Path, extra_context: str | None = None,
                      language: str = 'auto', context_dir: str | None = None) -> str | None:
    if not _CLAUDE_BIN:
        log.error("claude CLI no encontrado en PATH")
        return None

    user_prompt = _build_prompt(transcript, recording_path, extra_context, language)
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
