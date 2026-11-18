import difflib
import logging
import os
import re
import subprocess
import threading
import time
import unicodedata
from pathlib import Path

from config import get_claude_bin as _get_claude_bin, clean_env as _clean_env, PROJECT_DIR

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

## CALIDAD DE LA MINUTA (regla critica)

Una minuta NO es el transcript con otras palabras. Es un documento que alguien que no
estuvo en la reunion puede leer en 3 minutos y entender que se hablo, que se decidio y
que queda por hacer.

- SINTETIZA POR TEMA, no por orden cronologico ni por turno de palabra. Agrupa todo lo
  dicho sobre un mismo tema aunque se repartiera por la reunion, y escribelo como
  afirmaciones sobre el contenido ("El deck separa X de Y porque..."), no como crónica
  de la conversacion ("Fulano pregunto..., otro respondio...").
- Cada tema de "Temas Tratados" recoge: de que va, las posturas o argumentos que se
  dieron y por que, y en que quedo (decision, duda abierta o pendiente).
- CONSERVA lo concreto: cifras, fechas, nombres de documentos, sistemas, herramientas,
  clientes y personas. Un resumen vago ("se hablo de la estructura") no sirve.
- DESCARTA lo que no aporta: saludos, charla informal, problemas de camara o audio,
  muletillas, repeticiones y falsos comienzos. Si un transcript trae lineas cortadas
  o frases a medias, reconstruye la idea completa en vez de copiar el fragmento.
- No copies frases literales del transcript salvo una cita breve que importe de verdad.
- Resumen Ejecutivo: un parrafo corto con el objetivo y el resultado de la reunion, y
  despues los puntos clave. Debe tener sentido leido solo.

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

REGLA DE RESPONSABLE (estricta — la columna alimenta un tablero agrupado por persona):
- Escribe UN solo nombre de persona, tal y como aparece en el transcripto, o "-" si no esta claro.
- NUNCA uses como responsable: "Otros", "[Otros]", "Equipo", "Team", "Participante", "Grabador/a",
  "Sin concretar", "TBD", "Por definir", "Todos", ni nada con "(atribucion inferida)".
  Si no sabes quien es, escribe "-". Que no se sepa el responsable NO es motivo para inventar una etiqueta.
- No añadas anotaciones entre parentesis salvo "(Claude)". Una persona = una sola grafia, la misma en todas las filas.
- Si el responsable es la persona que grabo, usa su nombre real, no "Grabador/a".

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
- En la seccion de asistentes, si el contexto trae una lista de invitados del
  calendario, sigue las reglas de asistentes que acompanan esa lista. Si no la
  trae, lista solo los nombres que aparezcan en el transcripto e indica que la
  lista puede estar incompleta.
  Formato: una vineta por persona ("- Nombre"), sin entradas genericas como "Otros" o "Grabador/a".
  Los nombres solo mencionados (no presentes) van en una unica linea final "Mencionados: ..."."""


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


_NAME_PARTICLES = {'de', 'del', 'la', 'las', 'los', 'van', 'von', 'der', 'den', 'da', 'dos',
                   'das', 'di', 'du', 'le', 'el', 'bin', 'al'}
_MAX_LISTED_INVITEES = 60


def _fold(text: str) -> str:
    """Minúsculas y sin acentos: Whisper escribe 'Ines' o 'Inés' indistintamente."""
    text = unicodedata.normalize('NFKD', text or '')
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


def _name_tokens(name: str) -> list[str]:
    return [t for t in re.findall(r'[a-z]+', _fold(name))
            if len(t) >= 3 and t not in _NAME_PARTICLES]


def _spoken_text(transcript: str) -> str:
    """El transcript sin marcas de tiempo ni etiquetas de hablante: si no, el
    nombre del que graba ('[Inés]:') cuenta como si alguien lo hubiera dicho."""
    return _fold(re.sub(r'\[[^\]\n]*\]:?', ' ', transcript or ''))


def _evidence(person_name: str, spoken: str, words: set[str]) -> str:
    """'full' si salen todos los nombres/apellidos, 'partial' si solo uno, 'none'.

    Un token cuenta aunque Whisper lo haya escrito mal (>=80% de parecido, solo
    en palabras de 5+ letras para no inventar coincidencias)."""
    tokens = _name_tokens(person_name)
    if not tokens:
        return 'none'
    found = 0
    for t in tokens:
        if re.search(rf'\b{re.escape(t)}\b', spoken):
            found += 1
        elif len(t) >= 5 and difflib.get_close_matches(t, words, n=1, cutoff=0.8):
            found += 1
    if found >= 2 or found == len(tokens):
        return 'full'
    return 'partial' if found else 'none'


def _participants_note(participants: list, transcript: str, me_name: str, lang: str) -> str:
    """Invitados del calendario + evidencia de cada uno en el transcript, con las
    reglas para decidir quién consta como asistente."""
    people = [p for p in participants if p.get('name') or p.get('email')]
    if not people:
        return ''

    spoken = _spoken_text(transcript)
    words = set(re.findall(r'[a-z]{5,}', spoken))
    me = set(_name_tokens(me_name))
    subject = next((p.get('meeting_subject') for p in people if p.get('meeting_subject')), '')

    labels = {
        'en': {'me': 'is the person who recorded', 'prefix': 'named in transcript',
               'full': 'yes', 'partial': 'first or last name only', 'none': 'no'},
        'es': {'me': 'es quien graba', 'prefix': 'nombrado en el transcript',
               'full': 'sí', 'partial': 'solo nombre o apellido', 'none': 'no'},
    }['en' if lang == 'en' else 'es']

    # Outlook solo informa de las respuestas al organizador: para el resto de
    # invitados todo sale 'no response', y presentarlo como dato engañaría.
    has_responses = any(p.get('response') in ('accepted', 'declined', 'tentative')
                        for p in people)

    lines = []
    for p in people[:_MAX_LISTED_INVITEES]:
        name = p.get('name') or p.get('email')
        facts = [p.get('role') or 'required']
        if has_responses and p.get('response'):
            facts.append(p['response'])
        if me and me & set(_name_tokens(name)):
            ev = labels['me']
        else:
            ev = f"{labels['prefix']}: {labels[_evidence(name, spoken, words)]}"
        lines.append(f"- {name} [{'; '.join(facts)}] — {ev}")
    if len(people) > _MAX_LISTED_INVITEES:
        lines.append(f"- (+{len(people) - _MAX_LISTED_INVITEES} more)" if lang == 'en'
                     else f"- (+{len(people) - _MAX_LISTED_INVITEES} más)")

    if lang == 'en':
        head = f"\nCalendar invitation{f' ({subject!r})' if subject else ''} — invitees:"
        rules = (
            "\nAttendees rules (use the invitation AND the transcript):\n"
            "- List as attendees (one bullet per person): the recorder; every invitee named in the "
            "transcript; every invitee who accepted; the organizer; and anyone who speaks, introduces "
            "themselves or is addressed by name in the transcript even if not invited.\n"
            "- Do NOT list as attendees an invitee who declined, or who has no response and is never "
            "named. Put them in one final line \"Invited, attendance not confirmed: ...\".\n"
            "- 'first or last name only' is weak evidence: use it together with the response; "
            "a common first name alone is not proof.\n"
            "- Whisper spells names by ear (Kostas/Costas, Nurettin/Norethin): match invitees to names "
            "heard in the transcript by sound, not only by exact spelling.\n"
            "- Use the spelling from the invitation for names the transcript misspells.\n"
            "- Do not claim the list is complete: Teams gives no roster, so say it is inferred "
            "from the invitation and the transcript."
        )
    else:
        head = f"\nInvitación del calendario{f' ({subject!r})' if subject else ''} — invitados:"
        rules = (
            "\nReglas de asistentes (usa la invitación Y el transcript):\n"
            "- Lista como asistentes (una viñeta por persona): quien graba; todo invitado nombrado "
            "en el transcript; todo invitado que aceptó; el organizador; y cualquiera que hable, se "
            "presente o sea llamado por su nombre en el transcript aunque no estuviera invitado.\n"
            "- NO listes como asistente a un invitado que rechazó, ni a uno sin respuesta que nunca "
            "se nombra. Ponlos en una última línea \"Invitados sin confirmar asistencia: ...\".\n"
            "- 'solo nombre o apellido' es una evidencia débil: úsala junto con la respuesta; "
            "un nombre de pila común por sí solo no prueba nada.\n"
            "- Whisper escribe los nombres de oído (Kostas/Costas, Nurettin/Norethin): empareja a los "
            "invitados con los nombres oídos por su sonido, no solo por la grafía exacta.\n"
            "- Usa la grafía de la invitación para los nombres que el transcript escriba mal.\n"
            "- No afirmes que la lista es completa: Teams no da la lista de participantes, así que "
            "indica que se infiere de la invitación y del transcript."
        )
    return head + '\n' + '\n'.join(lines) + rules


_LINE_RE = re.compile(r'^\[(\d+):(\d{2})\]\s*(?:\[([^\]]+)\]:\s*)?(.*)$')
_TURN_GAP_SECS = 8        # más silencio que esto = otra intervención
_TURN_MAX_CHARS = 700


def compact_transcript(transcript: str) -> str:
    """Une las líneas seguidas del mismo hablante en intervenciones.

    Whisper emite una línea cada 2-3 segundos, cada una con marca de tiempo y
    etiqueta: una hora de reunión son ~900 líneas entrecortadas. Con eso el
    modelo parafrasea línea a línea ("Otros explicó…") en vez de sintetizar por
    temas, y además se gastan tokens en repetir las etiquetas. Cada
    intervención conserva la marca de tiempo de su primera línea.
    """
    turns: list[list] = []        # [segundos de inicio, segundos del último, hablante, texto]
    for raw in transcript.splitlines():
        m = _LINE_RE.match(raw.strip())
        if not m:
            if raw.strip():
                turns.append([None, None, None, raw.strip()])      # línea suelta, se respeta
            continue
        secs = int(m.group(1)) * 60 + int(m.group(2))
        who, text = m.group(3), m.group(4).strip()
        if not text:
            continue
        last = turns[-1] if turns else None
        if (last and last[0] is not None and last[2] == who
                and secs - last[1] <= _TURN_GAP_SECS
                and len(last[3]) + len(text) < _TURN_MAX_CHARS):
            last[3] += ' ' + text
            last[1] = secs
        else:
            turns.append([secs, secs, who, text])

    out = []
    for start, _end, who, text in turns:
        if start is None:
            out.append(text)
            continue
        label = f" [{who}]:" if who else ''
        out.append(f"[{start // 60:02d}:{start % 60:02d}]{label} {text}")
    return '\n'.join(out)


def _build_prompt(transcript: str, recording_path: Path, extra_context: str | None = None, language: str = 'auto', me_name: str = '', participants: list | None = None) -> str:
    m = re.match(r'(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})', recording_path.stem)
    fecha = m.group(1) if m else 'desconocida'
    hora = f"{m.group(2)}:{m.group(3)}" if m else 'desconocida'

    duracion = 'desconocida'
    times = re.findall(r'\[(\d+):(\d{2})\]', transcript)
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

    # A: inject Outlook calendar attendees into context
    participants_note = _participants_note(participants, transcript, me_name, lang) if participants else ''

    # Glosario: grafía correcta de nombres, siglas y términos (globales + los del
    # proyecto que detecta el pipeline por el nombre y el contenido).
    glossary_note = ''
    try:
        import glossary
        from project_context import detect_project
        _nm = re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(.+)', recording_path.stem)
        _proj = detect_project(transcript, _nm.group(1).replace('_', ' ') if _nm else '')
        glossary_note = glossary.prompt_section(
            glossary.entries(_proj.get('id') if _proj else None), transcript,
            'en' if lang == 'en' else 'es')
    except Exception as e:
        log.debug(f"Sin glosario en el prompt: {e}")

    # B: diarization override — transcript has speaker labels
    diarization_override = ''
    if re.search(r'^\[.+?\]:', transcript, re.MULTILINE):
        speaker_name = me_name or ('Recorder' if lang == 'en' else 'Grabador/a')
        has_attendees = bool(participants_note)
        if lang == 'en':
            diarization_override = (
                "\n## IMPORTANT OVERRIDE — Speaker labels present\n"
                "Ignore the 'no diarization' rule in the system prompt. "
                "This transcript HAS speaker labels:\n"
                f"- [{speaker_name}]: the person who recorded this meeting\n"
                "- [Otros]: all remote participants combined — treat as a group; "
                "do NOT invent sub-labels like 'Speaker 1', 'Speaker 2', etc.\n"
                "The labels only help you follow the dialogue (who asks, who answers). "
                "Do NOT narrate the meeting speaker by speaker: never write 'Otros said', "
                "'Others explained' or 'Recorder noted' as the subject of a bullet, and never "
                "call anyone 'Recorder'. Write by topic, about the content. Name a person only "
                f"when the transcript says their real name; {speaker_name}'s own statements and "
                "commitments may be attributed to them. "
                f"List {speaker_name} in the Attendees section, one bullet per person"
                + (" (follow the Attendees rules in the Context section for the invitees)" if has_attendees else "")
                + ". "
                "'Others' is NOT an attendee and NEVER a valid Owner in the actions table: "
                "use a real name said in the transcript, or '-'."
            )
        else:
            diarization_override = (
                "\n## OVERRIDE — Etiquetas de hablante presentes\n"
                "Ignora la instrucción 'no hay diarización' del system prompt. "
                "Este transcript SÍ contiene etiquetas de hablante:\n"
                f"- [{speaker_name}]: la persona que grabó esta reunión\n"
                "- [Otros]: el resto de participantes remotos — trátalo como grupo; "
                "NO inventes sub-etiquetas como 'Speaker 1', 'Speaker 2', 'Hablante 2', etc.\n"
                "Las etiquetas solo te ayudan a seguir el diálogo (quién pregunta, quién responde). "
                "NO narres la reunión hablante por hablante: nunca escribas 'Otros dijo', "
                "'Otros explicó' ni 'Grabador/a señaló' como sujeto de una viñeta, y no llames a "
                "nadie 'Grabador/a'. Escribe por temas, sobre el contenido. Nombra a una persona "
                f"solo si el transcript dice su nombre real; las intervenciones y compromisos de {speaker_name} "
                "sí pueden atribuirse a esa persona. "
                f"Incluye a {speaker_name} en la sección de Asistentes, una viñeta por persona"
                + (" (para los invitados sigue las reglas de asistentes de la sección Contexto)" if has_attendees else "")
                + ". "
                "'Otros' NO es un asistente y NUNCA es un Responsable válido en la tabla de acciones: "
                "usa un nombre real dicho en el transcript, o '-'."
            )

    transcript = compact_transcript(transcript)

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
        date_label + recorder_note + participants_note + glossary_note,
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

    if diarization_override:
        parts.append(diarization_override)

    if extra_context:
        parts.insert(3, f"{ctx_label}\n{extra_context}\n")

    return '\n'.join(parts)


def _generate_via_cli(transcript: str, recording_path: Path, extra_context: str | None = None,
                      language: str = 'auto', context_dir: str | None = None,
                      participants: list | None = None) -> str | None:
    _claude_bin = _get_claude_bin()
    if not _claude_bin:
        log.error("claude CLI no encontrado en PATH")
        return None

    try:
        import json as _json
        _cfg = _json.loads((PROJECT_DIR / 'settings.json').read_text(encoding='utf-8-sig'))
        me_name = _cfg.get('user_name', '').strip()
    except Exception:
        me_name = ''
    user_prompt = _build_prompt(transcript, recording_path, extra_context, language, me_name=me_name,
                                participants=participants)
    system_prompt = _get_system_prompt(language).replace('```', '~~~')

    cmd = [_claude_bin, '-p']
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
            log.error(f"claude -p error (rc={proc.returncode}): {_cli_error(stdout, stderr)[:500]}")
            return None
        return stdout.strip()
    except Exception as e:
        log.error(f"Error llamando claude CLI: {e}")
        return None


def run_claude(prompt: str, timeout: int = 300) -> str | None:
    """Ejecuta `claude -p` con un prompt suelto (sin system prompt de minutas) y
    devuelve su salida, o None si el CLI falta, falla o tarda demasiado."""
    _claude_bin = _get_claude_bin()
    if not _claude_bin:
        log.error("claude CLI no encontrado en PATH")
        return None
    try:
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
        else:
            si = None
        proc = subprocess.Popen(
            # Sin guardar la sesion: el prompt lleva el transcript.
            [_claude_bin, '-p', '--no-session-persistence'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', env=_clean_env(),
            startupinfo=si if os.name == 'nt' else None,
            creationflags=0x08000000 if os.name == 'nt' else 0,
        )
        try:
            stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            log.error(f"claude -p timeout ({timeout}s)")
            return None
        if proc.returncode != 0:
            log.error(f"claude -p error (rc={proc.returncode}): {_cli_error(stdout, stderr)[:300]}")
            return None
        return (stdout or '').strip() or None
    except Exception as e:
        log.error(f"Error llamando claude CLI: {e}")
        return None


def _cli_error(stdout: str | None, stderr: str | None) -> str:
    """El error de `claude -p`. Lo escribe en stdout ("Failed to authenticate:
    OAuth session expired...") con stderr vacio: el log del 28/10/2026 decia
    solo "claude -p error (rc=1): " y no habia forma de saber que pasaba."""
    return ((stderr or '').strip() or (stdout or '').strip())


def claude_problem() -> str | None:
    """Por que no se puede usar Claude ahora mismo: 'missing' (no se encuentra
    el CLI), 'no_session' (sin sesion iniciada) o None (parece listo, o no se
    sabe). Lo dice `claude auth status`, que no gasta tokens."""
    claude_bin = _get_claude_bin()
    if not claude_bin:
        return 'missing'
    try:
        import json
        r = subprocess.run(
            [claude_bin, 'auth', 'status'], capture_output=True, text=True, encoding='utf-8',
            timeout=30, env=_clean_env(),
            creationflags=0x08000000 if os.name == 'nt' else 0,
        )
        if json.loads(r.stdout or '{}').get('loggedIn') is False:
            return 'no_session'
    except Exception as e:
        log.info(f"claude auth status no aclara el estado de Claude: {e}")
    return None


def regenerate_actions_section(transcript: str, current_actions_md: str,
                               instruction: str, language: str = 'auto') -> str | None:
    """Reescribe SOLO la sección de Acciones Pendientes según la instrucción del usuario.
    Devuelve el markdown de la nueva sección (empezando por '## ...') o None."""
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

    out = run_claude(prompt, timeout=900)
    if not out:
        return None
    if not out.lstrip().startswith('#'):
        out = f"## {heading}\n\n{out}"
    return out


def extract_title_from_minutes(raw_text: str) -> tuple[str, str]:
    lines = raw_text.strip().splitlines()
    if lines and lines[0].startswith('TITULO:'):
        title = lines[0].removeprefix('TITULO:').strip()
        content = '\n'.join(lines[1:]).lstrip('\n')
        return title or 'Reunion', content
    return 'Reunion', raw_text


# 'pending': las completas fallaron (Claude sin sesion, sin CLI, sin respuesta) y
# Noted las reintenta solo. Lleva el stem de su grabacion: es lo que identifica
# la reunion, porque dos grabaciones del mismo minuto comparten la hora.
_MODE_MARKER = re.compile(r'^<!-- noted:minutes=(summary|off|pending)(?: stem=(\S+))? -->', re.MULTILINE)

_BRIEF_NOTE = {
    'es': ("_Minutas completas desactivadas en Ajustes. El transcript está en su pestaña, y puedes "
           "generar las minutas de esta reunión con «Generar minutas completas»._"),
    'en': ("_Full minutes are turned off in Settings. The transcript is in its tab, and you can "
           "generate this meeting's minutes with «Generate full minutes»._"),
}

_PENDING_NOTE = {
    'es': ("_No se pudieron generar las minutas completas: {reason}. Noted las generará solo en "
           "cuanto Claude vuelva a responder, o puedes pedirlas ya con «Generar minutas completas». "
           "El transcript está en su pestaña._"),
    'en': ("_The full minutes could not be generated: {reason}. Noted will generate them on its own "
           "as soon as Claude responds again, or you can ask for them now with «Generate full minutes». "
           "The transcript is in its tab._"),
}

_PENDING_REASON = {
    'es': {'no_session': 'Claude no tenía la sesión iniciada (se arregla con «claude auth login»)',
           'missing': 'no se encontró Claude Code',
           None: 'Claude no respondió'},
    'en': {'no_session': 'Claude was not signed in (fix it with «claude auth login»)',
           'missing': 'Claude Code was not found',
           None: 'Claude did not respond'},
}


def brief_minutes(title: str, mode: str, summary: str | None = None, language: str = 'es',
                  reason: str | None = None, stem: str = '') -> str:
    """Las minutas de los niveles 'summary' y 'off' (decision de Felipe, 19/10/2026),
    y las 'pending' que quedan cuando las completas fallan.

    Sin ellas la reunion no saldria en el panel (que lista por .md), su
    transcript no se podria abrir y se reprocesaria en cada arranque. Llevan la
    linea TITULO: como las completas, y un marcador ESTRUCTURAL (un comentario
    HTML, invisible al renderizar) que dice que son breves: el panel lo usa para
    ofrecer las completas. summary: lo que devolvio meeting_qa.summarize, o None.
    reason: lo que devolvio claude_problem(), para 'pending'.
    """
    lang = 'en' if language == 'en' else 'es'
    if mode == 'pending':
        note = _PENDING_NOTE[lang].format(reason=_PENDING_REASON[lang].get(reason, _PENDING_REASON[lang][None]))
        marker = f"<!-- noted:minutes=pending stem={stem} -->" if stem else "<!-- noted:minutes=pending -->"
    else:
        note = _BRIEF_NOTE[lang]
        marker = f"<!-- noted:minutes={mode} -->"
    body = ''
    if summary:
        first, _, rest = summary.partition('\n')
        if first.startswith('TITULO:'):
            title = first.removeprefix('TITULO:').strip() or title
            summary = rest.strip()
        body = f"{summary.strip()}\n\n" if summary.strip() else ''
    return f"TITULO: {title}\n{marker}\n\n{body}{note}\n"


def brief_mode_of(md_text: str) -> str | None:
    """'summary', 'off' o 'pending' si son minutas breves; None si son completas."""
    m = _MODE_MARKER.search(md_text[:500])
    return m.group(1) if m else None


def pending_stem_of(md_text: str) -> str | None:
    """El stem de la grabacion de unas minutas 'pending', o None."""
    m = _MODE_MARKER.search(md_text[:500])
    return m.group(2) if m and m.group(1) == 'pending' else None


def save_minutes(minutes: str, output_path: Path) -> bool:
    """Escritura atomica: al lado y renombrada encima. write_text trunca antes de
    escribir, y con el disco lleno una minuta que ya existia quedaba a medias,
    no se borraba (no la habia creado este intento) y _recover_pending la daba
    por hecha. El renombrado se reintenta si Windows tiene el destino abierto
    (antivirus, indexador, el panel leyendola)."""
    tmp = output_path.with_name(output_path.name + '.tmp')
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(minutes, encoding='utf-8')
        for intento in range(10):
            try:
                os.replace(tmp, output_path)
                break
            except PermissionError:
                if intento == 9:
                    raise
                time.sleep(0.05)
        log.info(f"Minutas guardadas: {output_path}")
        return True
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        log.error(f"Error guardando minutas: {e}")
        return False


def generate_minutes(
    transcript: str,
    recording_path: Path,
    extra_context: str | None = None,
    on_complete=None,
    language: str = 'auto',
    context_dir: str | None = None,
    participants: list | None = None,
) -> str | None:
    """
    Si on_complete es callable, ejecuta en thread daemon y devuelve None.
    Si no, bloquea y devuelve el texto de las minutas.
    context_dir: carpeta del proyecto para acceso agéntico (memoria + documentos).
    participants: lista de asistentes del calendario (Outlook) para inyectar en el prompt.
    """
    def _run():
        log.info(f"Generando minutas con claude CLI (idioma: {language}"
                 f"{', con contexto de proyecto' if context_dir else ''})...")
        result = _generate_via_cli(transcript, recording_path, extra_context, language, context_dir,
                                   participants=participants)
        if on_complete:
            on_complete(result)
        return result

    if on_complete:
        t = threading.Thread(target=_run, daemon=True, name='MinutesGenerator')
        t.start()
        return None
    return _run()
