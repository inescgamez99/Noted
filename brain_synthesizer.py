"""
Dream step: actualiza el wiki de un proyecto después de cada reunión.
Se ejecuta como hilo daemon — los errores nunca interrumpen el pipeline principal.
"""
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


def _fmt_timestamp() -> str:
    now = datetime.now().astimezone()
    try:
        return now.strftime('%d %b %Y · %H:%M')
    except Exception:
        return now.strftime('%Y-%m-%d %H:%M')


def update_brain_wiki(project_id: str, project_name: str,
                      minutes_path: Path, lang: str = 'es') -> bool:
    """
    Lee el wiki.md actual del proyecto y las minutas de hoy;
    llama a Claude para actualizar el wiki de forma incremental.
    """
    from config import PROJECT_DIR, CLAUDE_BIN, clean_env

    if not CLAUDE_BIN:
        log.warning("Brain: claude no está en PATH")
        return False

    brain_dir = PROJECT_DIR / 'brain' / project_id
    brain_dir.mkdir(parents=True, exist_ok=True)
    wiki_path = brain_dir / 'wiki.md'

    wiki_actual = ''
    if wiki_path.exists():
        try:
            wiki_actual = wiki_path.read_text(encoding='utf-8')
        except Exception as e:
            log.warning(f"Brain: lectura wiki: {e}")

    try:
        minutes_text = minutes_path.read_text(encoding='utf-8')
    except Exception as e:
        log.warning(f"Brain: lectura minutas: {e}")
        return False

    today = _fmt_timestamp()
    empty_note = ('(vacío — primera reunión de este proyecto)'
                  if lang != 'en' else '(empty — first meeting of this project)')

    if lang == 'en':
        prompt = _build_prompt_en(project_name, wiki_actual or empty_note, minutes_text, today)
    else:
        prompt = _build_prompt_es(project_name, wiki_actual or empty_note, minutes_text, today)

    ok = _run_claude(prompt, wiki_path, project_name, project_id)
    if ok:
        try:
            generate_snapshot(project_id, project_name, wiki_path.read_text(encoding='utf-8'), lang)
        except Exception as e:
            log.warning(f"Brain: snapshot post-meeting: {e}")
    return ok


def bulk_initialize_brain_wiki(project_id: str, project_name: str,
                               meetings: list, docs: list,
                               lang: str = 'es') -> bool:
    """
    Inicializa el wiki desde cero usando TODAS las reuniones y documentos del proyecto.
    meetings: [{'date': str, 'title': str, 'text': str}] ordenadas de más antigua a más reciente.
    docs: [{'name': str, 'text': str}] documentos SharePoint/memoria del proyecto.
    """
    from config import PROJECT_DIR, CLAUDE_BIN, clean_env

    if not CLAUDE_BIN:
        log.warning("Brain: claude no está en PATH")
        return False

    brain_dir = PROJECT_DIR / 'brain' / project_id
    brain_dir.mkdir(parents=True, exist_ok=True)
    wiki_path = brain_dir / 'wiki.md'

    today = _fmt_timestamp()

    if lang == 'en':
        prompt = _build_init_prompt_en(project_name, meetings, docs, today)
    else:
        prompt = _build_init_prompt_es(project_name, meetings, docs, today)

    ok = _run_claude(prompt, wiki_path, project_name, project_id, timeout=600)
    if ok:
        try:
            generate_snapshot(project_id, project_name, wiki_path.read_text(encoding='utf-8'), lang)
        except Exception as e:
            log.warning(f"Brain: snapshot post-init: {e}")
    return ok


def generate_snapshot(project_id: str, project_name: str,
                      wiki_text: str, lang: str = 'es') -> bool:
    """
    Generates a short TL;DR snapshot (5-7 bullets) from the current wiki.
    Stored in brain/<project_id>/snapshot.md.
    """
    from config import PROJECT_DIR, CLAUDE_BIN, clean_env

    if not CLAUDE_BIN or not wiki_text:
        return False

    brain_dir = PROJECT_DIR / 'brain' / project_id
    brain_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = brain_dir / 'snapshot.md'
    today = _fmt_timestamp()

    if lang == 'en':
        prompt = f"""Based on this project wiki, generate a concise snapshot for TODAY ({today}).

WIKI:
{wiki_text}

Return ONLY 5-7 lines in this exact format (no extra text):
**Status:** [phase + health in one phrase]
**Focus:** [the ONE most important thing right now]
**Blocker:** [main blocker, or "None"]
**Changed:** [most significant change in the last week, or "Nothing new"]
**Next:** [next meeting or deadline with date]
**Urgent action:** [the ONE action that needs attention most urgently, with owner]
**Prepare:** [what to prepare for the next meeting — be specific]

Use names, dates, concrete facts from the wiki. Never write vague generalities. Be opinionated."""
    else:
        prompt = f"""Basándote en este wiki del proyecto, genera un snapshot conciso para HOY ({today}).

WIKI:
{wiki_text}

Devuelve SOLO 5-7 líneas en este formato exacto (sin texto adicional):
**Estado:** [fase + salud en una frase]
**Foco:** [LA cosa más importante ahora mismo]
**Bloqueador:** [bloqueador principal, o "Ninguno"]
**Cambios:** [el cambio más significativo de la última semana, o "Sin novedades"]
**Próximo:** [próxima reunión o deadline con fecha]
**Acción urgente:** [LA acción que más necesita atención, con responsable]
**Preparar:** [qué preparar para la próxima reunión — sé específico]

Usa nombres, fechas, hechos concretos del wiki. Nunca generalidades vagas. Sé directo y opinionado."""

    return _run_claude(prompt, snapshot_path, project_name, project_id, timeout=60)


def update_brain_wiki_from_note(project_id: str, project_name: str,
                                note: str, lang: str = 'es') -> bool:
    """Updates the wiki based on a manual note from the user."""
    from config import PROJECT_DIR, CLAUDE_BIN, clean_env

    if not CLAUDE_BIN:
        return False

    brain_dir = PROJECT_DIR / 'brain' / project_id
    brain_dir.mkdir(parents=True, exist_ok=True)
    wiki_path = brain_dir / 'wiki.md'
    notes_path = brain_dir / 'notes.md'

    today = _fmt_timestamp()
    try:
        with notes_path.open('a', encoding='utf-8') as f:
            f.write(f"\n---\n**{today}**\n{note}\n")
    except Exception as e:
        log.warning(f"Brain: no se pudo guardar nota: {e}")

    wiki_text = ''
    if wiki_path.exists():
        try:
            wiki_text = wiki_path.read_text(encoding='utf-8')
        except Exception:
            pass

    empty_note = '(sin wiki aún)' if lang != 'en' else '(no wiki yet)'

    if lang == 'en':
        structure = _WIKI_STRUCTURE_EN.format(project_name=project_name, today=today)
        prompt = f"""Update the project wiki for "{project_name}" based on this new note.

CURRENT WIKI:
{wiki_text or empty_note}

NEW NOTE ({today}):
{note}

Instructions:
- Update ONLY the sections affected by this note
- Mark superseded info with ~~strikethrough~~
- If the note mentions new decisions → update section 4
- If the note mentions people changes → update section 7
- If the note mentions new blockers/risks → update section 5
- Leave everything else unchanged
- Max 3,000 words. Language: English

{structure}"""
    else:
        structure = _WIKI_STRUCTURE_ES.format(project_name=project_name, today=today)
        prompt = f"""Actualiza el wiki del proyecto "{project_name}" basándote en esta nota.

WIKI ACTUAL:
{wiki_text or empty_note}

NOTA NUEVA ({today}):
{note}

Instrucciones:
- Actualiza SOLO las secciones afectadas por esta nota
- Marca la información superada con ~~tachado~~
- Si la nota menciona nuevas decisiones → actualiza sección 4
- Si menciona cambios de personas → actualiza sección 7
- Si menciona nuevos bloqueos/riesgos → actualiza sección 5
- Deja todo lo demás sin cambios
- Máximo 3.000 palabras. Idioma: Español

{structure}"""

    ok = _run_claude(prompt, wiki_path, project_name, project_id, timeout=180)
    if ok:
        generate_snapshot(project_id, project_name, wiki_path.read_text(encoding='utf-8'), lang)
    return ok


def update_brain_wiki_from_emails(project_id: str, project_name: str,
                                  emails: list, lang: str = 'es') -> bool:
    """
    Updates the wiki based on relevant emails.
    emails: [{'subject': str, 'body': str, 'sender': str, 'received': str}]
    """
    from config import PROJECT_DIR, CLAUDE_BIN, clean_env

    if not CLAUDE_BIN or not emails:
        return False

    brain_dir = PROJECT_DIR / 'brain' / project_id
    brain_dir.mkdir(parents=True, exist_ok=True)
    wiki_path = brain_dir / 'wiki.md'

    wiki_text = ''
    if wiki_path.exists():
        try:
            wiki_text = wiki_path.read_text(encoding='utf-8')
        except Exception:
            pass

    emails_block = '\n'.join(
        f"\n--- Email ({e.get('received', '')[:16]}) ---\n"
        f"De: {e.get('sender', '')}\n"
        f"Asunto: {e.get('subject', '')}\n"
        f"{e.get('body', '')[:1500]}"
        for e in emails[:10]
    )

    today = _fmt_timestamp()
    empty_note = '(sin wiki aún)' if lang != 'en' else '(no wiki yet)'

    if lang == 'en':
        structure = _WIKI_STRUCTURE_EN.format(project_name=project_name, today=today)
        prompt = f"""Update the project wiki for "{project_name}" based on these recent emails.

CURRENT WIKI:
{wiki_text or empty_note}

RECENT EMAILS ({len(emails)} emails):
{emails_block}

Instructions:
- Only incorporate information that is relevant to the project
- Update only sections where emails contain new information
- Mark superseded info with ~~strikethrough~~
- Ignore email boilerplate, auto-replies, and irrelevant content
- Max 3,000 words. Language: English

{structure}"""
    else:
        structure = _WIKI_STRUCTURE_ES.format(project_name=project_name, today=today)
        prompt = f"""Actualiza el wiki del proyecto "{project_name}" basándote en estos emails recientes.

WIKI ACTUAL:
{wiki_text or empty_note}

EMAILS RECIENTES ({len(emails)} emails):
{emails_block}

Instrucciones:
- Incorpora solo la información relevante para el proyecto
- Actualiza solo las secciones donde los emails aportan información nueva
- Marca la información superada con ~~tachado~~
- Ignora boilerplate, auto-respuestas y contenido irrelevante
- Máximo 3.000 palabras. Idioma: Español

{structure}"""

    ok = _run_claude(prompt, wiki_path, project_name, project_id, timeout=180)
    if ok:
        generate_snapshot(project_id, project_name, wiki_path.read_text(encoding='utf-8'), lang)
    return ok


def _run_claude(prompt: str, wiki_path: Path, project_name: str,
                project_id: str, timeout: int = 300) -> bool:
    from config import CLAUDE_BIN, clean_env
    try:
        env = clean_env()
        env['PYTHONUTF8'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        proc = subprocess.run(
            [CLAUDE_BIN, '-p'],
            input=prompt.encode('utf-8'),
            capture_output=True,
            timeout=timeout,
            env=env,
            creationflags=_CREATE_NO_WINDOW,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            stderr = proc.stderr.decode('utf-8', errors='replace')[:200]
            log.warning(f"Brain: Claude rc={proc.returncode}: {stderr}")
            return False
        new_wiki = proc.stdout.decode('utf-8', errors='replace').strip()
        wiki_path.write_text(new_wiki, encoding='utf-8')
        log.info(f"Brain: wiki actualizado '{project_name}' ({len(new_wiki)} chars)")
        return True
    except subprocess.TimeoutExpired:
        log.warning(f"Brain: timeout wiki {project_id}")
        return False
    except Exception as e:
        log.warning(f"Brain: error wiki: {e}")
        return False


def extract_key_sections(md_text: str, max_chars: int = 1200) -> str:
    """
    Extrae del markdown de minutas solo lo esencial:
    header de fecha + Resumen Ejecutivo + Decisiones Tomadas + Acciones Pendientes.
    Trunca a max_chars para no saturar el contexto.
    """
    sections = {}
    current = 'header'
    buf = []

    for line in md_text.splitlines():
        low = line.strip().lower()
        if low.startswith('## resumen') or low.startswith('## executive summary'):
            _flush(sections, current, buf)
            current = 'resumen'; buf = []
        elif low.startswith('## decisiones') or low.startswith('## active decisions'):
            _flush(sections, current, buf)
            current = 'decisiones'; buf = []
        elif low.startswith('## acciones') or low.startswith('## open action') or low.startswith('## pending'):
            _flush(sections, current, buf)
            current = 'acciones'; buf = []
        elif line.startswith('## ') and current in ('resumen', 'decisiones', 'acciones'):
            _flush(sections, current, buf)
            current = 'other'; buf = []
        else:
            buf.append(line)

    _flush(sections, current, buf)

    parts = []
    if sections.get('header'):
        parts.append(sections['header'].strip())
    if sections.get('resumen'):
        parts.append('**Resumen:** ' + sections['resumen'].strip()[:400])
    if sections.get('decisiones'):
        parts.append('**Decisiones:**\n' + sections['decisiones'].strip()[:500])
    if sections.get('acciones'):
        parts.append('**Acciones:**\n' + sections['acciones'].strip()[:400])

    result = '\n\n'.join(parts)
    return result[:max_chars]


def _flush(sections: dict, key: str, buf: list):
    text = '\n'.join(buf).strip()
    if text:
        sections[key] = sections.get(key, '') + '\n' + text


def _meetings_block(meetings: list) -> str:
    return ''.join(f"\n---\n### [{m['date']}] {m['title']}\n{m['text']}\n" for m in meetings)


def _docs_block_es(docs: list) -> str:
    if not docs:
        return ''
    return '\n\nDOCUMENTOS DEL PROYECTO (SharePoint / memoria):\n' + \
           ''.join(f"\n--- {d['name']} ---\n{d['text'][:2000]}\n" for d in docs)


def _docs_block_en(docs: list) -> str:
    if not docs:
        return ''
    return '\n\nPROJECT DOCUMENTS (SharePoint / memory):\n' + \
           ''.join(f"\n--- {d['name']} ---\n{d['text'][:2000]}\n" for d in docs)


_WIKI_STRUCTURE_ES = """\
# Brain — {project_name}
**Última actualización:** {today}

## 1. Estado actual
**Fase:** [fase actual del proyecto]
**Salud general:** [verde / ámbar / rojo + 1 frase de motivo]
**Foco principal:** [qué es lo más importante ahora mismo]
**Bloqueador principal:** [si lo hay; si no, "Ninguno"]
**Próximo hito:** [qué hay que conseguir y cuándo]

## 2. Qué ha cambiado (últimas 2 semanas)
**Nuevo:** [lo que se ha añadido / decidido / arrancado]
**Modificado:** [lo que ha cambiado respecto a antes]
**Cerrado:** [lo que se ha completado o resuelto]
**Superado:** [decisiones o enfoques que ya no aplican]

## 3. Estado por workstream
[Para cada workstream activo: nombre, estado (activo/pausado/bloqueado), 1-2 frases de situación]

## 4. Decisiones
**Confirmadas recientemente:**
[lista con fecha]

**Pendientes de decisión:**
[lista de decisiones abiertas que alguien debe tomar]

**Superadas:**
[decisiones que ya no aplican, con contexto mínimo]

## 5. Riesgos y bloqueadores
[Solo los activos ahora mismo. Formato: riesgo / quién lo gestiona / urgencia]

## 6. Inteligencia de acciones
[NO hagas una tabla completa de acciones. En su lugar:]
**X abiertas · Y vencidas · Z bloqueadas**

**Necesitan atención:**
- [acción 1 — responsable — motivo de urgencia]
- [acción 2 — responsable — motivo de urgencia]
- [máximo 5]

**Cerradas recientemente:**
- [acción cerrada 1]
- [acción cerrada 2]

## 7. Personas y ownership
| Persona | Organización | Rol | Responsabilidad actual | Workstreams |
|---------|-------------|-----|----------------------|-------------|
[Una fila por persona clave. Responsabilidad actual = qué está haciendo concretamente ahora]

**Responsabilidades sin dueño claro:**
[si las hay]

**Cambios de responsabilidad recientes:**
[si los hay]

## 8. Próximamente
**Próximas reuniones:** [lista con fecha y propósito]
**Qué preparar:** [no copies acciones; decide qué es relevante para esa reunión concreta]
**Decisiones esperadas:** [qué decisiones deben salir de las próximas reuniones]
**Dependencias antes de la próxima reunión:** [qué tiene que ocurrir antes]

## 9. Artefactos clave
[Documentos, decks, prototipos o outputs relevantes ahora mismo. Nombre + estado + dónde está]

## 10. Contexto del proyecto
[Información estable: qué es el proyecto, objetivos, cliente, alcance. Actualiza solo si cambia algo fundamental]

## 11. Historia comprimida
[Timeline de hitos y decisiones clave de hace más de 2 semanas que siguen siendo relevantes. Muy resumido]"""


_WIKI_STRUCTURE_EN = """\
# Brain — {project_name}
**Last updated:** {today}

## 1. Current state
**Phase:** [current project phase]
**Overall health:** [green / amber / red + 1-sentence reason]
**Main focus:** [what matters most right now]
**Main blocker:** [if any; otherwise "None"]
**Next milestone:** [what needs to happen and by when]

## 2. What changed (last 2 weeks)
**New:** [what was added / decided / started]
**Changed:** [what shifted compared to before]
**Closed:** [what was completed or resolved]
**Superseded:** [decisions or approaches that no longer apply]

## 3. Workstream status
[For each active workstream: name, status (active/paused/blocked), 1-2 sentences on current situation]

## 4. Decisions
**Recently confirmed:**
[list with date]

**Pending decisions:**
[open decisions that someone needs to make]

**Superseded:**
[decisions that no longer apply, with minimal context]

## 5. Risks & blockers
[Only currently active ones. Format: risk / who owns it / urgency]

## 6. Action intelligence
[Do NOT produce a full action table. Instead:]
**X open · Y overdue · Z blocked**

**Needs attention:**
- [action 1 — owner — reason for urgency]
- [action 2 — owner — reason for urgency]
- [max 5]

**Recently closed:**
- [closed action 1]
- [closed action 2]

## 7. People & ownership
| Person | Organisation | Role | Current responsibility | Workstreams |
|--------|-------------|------|----------------------|-------------|
[One row per key person. Current responsibility = what they are concretely doing now]

**Ownership gaps:**
[if any]

**Recent responsibility changes:**
[if any]

## 8. Upcoming
**Next meetings:** [list with date and purpose]
**What to prepare:** [do not copy actions; decide what is relevant for that specific meeting]
**Decisions expected:** [what decisions should come out of upcoming meetings]
**Dependencies before next meeting:** [what needs to happen first]

## 9. Key artifacts
[Relevant docs, decks, prototypes or outputs right now. Name + status + where it lives]

## 10. Project context
[Stable info: what the project is, objectives, client, scope. Update only if something fundamental changes]

## 11. Compressed history
[Timeline of milestones and key decisions from more than 2 weeks ago that are still relevant. Very brief]"""


def _build_init_prompt_es(project_name: str, meetings: list,
                          docs: list, today: str) -> str:
    structure = _WIKI_STRUCTURE_ES.format(project_name=project_name, today=today)
    return f"""Eres el responsable de crear el wiki inicial del proyecto "{project_name}".

Tienes acceso al historial completo de reuniones (de más antigua a más reciente) y a los documentos de referencia del proyecto.{_docs_block_es(docs)}

HISTORIAL DE REUNIONES ({len(meetings)} reuniones):
{_meetings_block(meetings)}

INSTRUCCIONES:
- Construye el wiki reflejando el ESTADO ACTUAL, no un resumen cronológico
- Las decisiones superadas ya no aparecen como vigentes
- La sección 6 (Inteligencia de acciones) NO es una tabla: es un resumen interpretado de qué importa ahora
- La sección 7 (Personas) es una tabla única — no repitas personas en otras secciones
- La sección 8 (Próximamente) debe decidir qué preparar, no copiar acciones abiertas
- Máximo 3.500 palabras
- IDIOMA: TODO el wiki en ESPAÑOL. Las reuniones pueden estar en inglés — da igual, el wiki es en español sin excepción. No mezcles idiomas.

{structure}"""


def _build_init_prompt_en(project_name: str, meetings: list,
                          docs: list, today: str) -> str:
    structure = _WIKI_STRUCTURE_EN.format(project_name=project_name, today=today)
    return f"""You are responsible for creating the initial wiki for the project "{project_name}".

You have access to the complete meeting history (oldest to most recent) and project reference documents.{_docs_block_en(docs)}

MEETING HISTORY ({len(meetings)} meetings):
{_meetings_block(meetings)}

INSTRUCTIONS:
- Build the wiki reflecting CURRENT STATE, not a chronological summary
- Superseded decisions do not appear as active
- Section 6 (Action intelligence) is NOT a table: it is an interpreted summary of what matters now
- Section 7 (People) is a single table — do not repeat people in other sections
- Section 8 (Upcoming) must decide what to prepare, not copy open actions
- Max 3,500 words
- LANGUAGE: The ENTIRE wiki must be in English without exception. Source meetings may be in Spanish — translate as needed. Do not mix languages.

{structure}"""


def _build_prompt_es(project_name: str, wiki_actual: str,
                     minutes_text: str, today: str) -> str:
    structure = _WIKI_STRUCTURE_ES.format(project_name=project_name, today=today)
    return f"""Eres el responsable de mantener el wiki del proyecto "{project_name}" siempre actualizado.

WIKI ACTUAL:
{wiki_actual}

REUNIÓN DE HOY:
{minutes_text}

INSTRUCCIONES:
- Actualiza solo lo que haya cambiado. Lo que no cambió, déjalo igual
- Marca con ~~tachado~~ lo que quedó obsoleto hoy
- Sección 6: no tabla; interpreta qué acciones importan ahora tras esta reunión
- Sección 7: actualiza responsabilidades si cambiaron
- Sección 8: decide qué preparar para la PRÓXIMA reunión basándote en el contexto, no en copiar acciones
- Máximo 3.000 palabras
- IDIOMA: TODO el wiki en ESPAÑOL sin excepción. Aunque la reunión esté en inglés, el wiki es en español.

{structure}"""


def _build_prompt_en(project_name: str, wiki_actual: str,
                     minutes_text: str, today: str) -> str:
    structure = _WIKI_STRUCTURE_EN.format(project_name=project_name, today=today)
    return f"""You are responsible for keeping the wiki for project "{project_name}" always up to date.

CURRENT WIKI:
{wiki_actual}

TODAY'S MEETING:
{minutes_text}

INSTRUCTIONS:
- Update only what changed. Leave unchanged sections as-is
- Mark with ~~strikethrough~~ what became obsolete today
- Section 6: no table; interpret which actions matter now after this meeting
- Section 7: update responsibilities if they changed
- Section 8: decide what to prepare for the NEXT meeting based on context, not by copying actions
- Max 3,000 words
- LANGUAGE: The ENTIRE wiki must be in English without exception. Source meetings may be in Spanish — translate as needed. Do not mix languages.

{structure}"""
