import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Action:
    index: int
    type: str           # 'instruction' | 'code_change' | 'document_change' | 'human'
    prompt: str
    title: str
    context: str
    archivo: str
    suggested_project: str | None
    raw_block: str
    assignee: str | None = None   # pre-populated from table for human actions
    deadline: str | None = None   # from table column


def _guess_project(text: str, projects_dir: Path | None) -> str | None:
    if not projects_dir or not projects_dir.exists():
        return None
    text_lower = text.lower()
    for d in projects_dir.iterdir():
        if d.is_dir() and d.name.lower() in text_lower:
            return d.name
    return None


def parse_actions(minutes_text: str, projects_dir: Path | None = None) -> list[Action]:
    actions = []
    idx = 0

    # Pasada 1: bloques instruction-for-claude
    for m in re.finditer(r'~~~instruction-for-claude\n(.*?)~~~', minutes_text, re.DOTALL):
        body = m.group(1).strip()
        title = body.splitlines()[0][:100] if body else ''
        actions.append(Action(
            index=idx, type='instruction',
            prompt=body, title=title,
            context='', archivo='',
            suggested_project=_guess_project(body, projects_dir),
            raw_block=m.group(0),
        ))
        idx += 1

    # Pasada 2: bloques document-change
    for m in re.finditer(r'~~~document-change\n(.*?)~~~', minutes_text, re.DOTALL):
        body = m.group(1)
        archivo = context = instruccion = ''
        for line in body.splitlines():
            l = line.lstrip('/ ').strip()
            if l.startswith('ARCHIVO:'):
                archivo = l.removeprefix('ARCHIVO:').strip()
            elif l.startswith('CONTEXTO:'):
                context = l.removeprefix('CONTEXTO:').strip()
            elif l.startswith('INSTRUCCION:'):
                instruccion = l.removeprefix('INSTRUCCION:').strip()
        prompt = instruccion or body.strip()
        actions.append(Action(
            index=idx, type='document_change',
            prompt=prompt, title=prompt[:100],
            context=context, archivo=archivo,
            suggested_project=_guess_project(body, projects_dir),
            raw_block=m.group(0),
        ))
        idx += 1

    # Pasada 3: bloques de código con INSTRUCCION PARA CLAUDE CODE
    pattern = r'~~~(\w*)\n(.*?INSTRUCCION PARA CLAUDE CODE.*?)~~~'
    for m in re.finditer(pattern, minutes_text, re.DOTALL | re.IGNORECASE):
        body = m.group(2)
        instruccion = context = archivo = codigo = ''
        code_lines = []
        for line in body.splitlines():
            stripped = line.lstrip('/ ').strip()
            if stripped.upper().startswith('INSTRUCCION PARA CLAUDE CODE:'):
                instruccion = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('CONTEXTO:'):
                context = stripped.removeprefix('CONTEXTO:').strip()
            elif stripped.startswith('ARCHIVO:'):
                archivo = stripped.removeprefix('ARCHIVO:').strip()
            elif not stripped.startswith('//'):
                code_lines.append(line)
        if code_lines:
            codigo = '\n'.join(code_lines).strip()
        prompt = instruccion
        if codigo:
            prompt += f"\n\nCódigo de referencia:\n```\n{codigo}\n```"
        actions.append(Action(
            index=idx, type='code_change',
            prompt=prompt, title=instruccion[:100],
            context=context, archivo=archivo,
            suggested_project=_guess_project(body, projects_dir),
            raw_block=m.group(0),
        ))
        idx += 1

    return actions


# ── Fusión de acciones duplicadas ─────────────────────────────────────────────

def _key_words(text: str) -> set[str]:
    stopwords = {
        'a', 'de', 'el', 'la', 'los', 'las', 'en', 'y', 'o', 'un', 'una', 'es', 'por',
        'que', 'se', 'del', 'con', 'para', 'the', 'to', 'of', 'and', 'or', 'for', 'in',
        'with', 'an', 'is', 'on', 'at', 'this', 'that',
    }
    words = re.sub(r'[^a-záéíóúñüa-z0-9\s]', ' ', text.lower()).split()
    return {w for w in words if len(w) > 2 and w not in stopwords}


def _is_same_task(human_title: str, claude_title: str, claude_prompt: str) -> bool:
    """True si la acción humana y la Claude describen la misma tarea."""
    h_words = _key_words(human_title)
    if not h_words:
        return False
    c_words = _key_words(claude_title + ' ' + claude_prompt[:400])
    overlap = h_words & c_words
    return len(overlap) / len(h_words) >= 0.5


def merge_actions(claude_actions: list[Action], human_actions: list[Action]) -> list[Action]:
    """Fusiona acciones de tabla con sus bloques Claude equivalentes.

    Cuando una fila de la tabla describe la misma tarea que un bloque Claude,
    la acción Claude absorbe el título legible y los metadatos (assignee, deadline).
    Las filas sin equivalente Claude quedan como acciones human independientes.
    """
    absorbed = set()

    for c in claude_actions:
        for i, h in enumerate(human_actions):
            if i in absorbed:
                continue
            if _is_same_task(h.title, c.title, c.prompt):
                c.title = h.title
                if h.assignee and not c.assignee:
                    c.assignee = h.assignee
                if h.deadline and not c.deadline:
                    c.deadline = h.deadline
                absorbed.add(i)
                break

    unmatched = [h for i, h in enumerate(human_actions) if i not in absorbed]
    return claude_actions + unmatched


def parse_table_actions(minutes_text: str, start_index: int = 0) -> list[Action]:
    """Extract human action rows from the Acciones/Next Steps markdown table."""
    actions = []
    idx = start_index

    # Find the actions section (supports old and new heading names, ES and EN)
    section_m = re.search(
        r'##\s+(?:Acciones Pendientes|Pending Actions|Acciones y Próximos Pasos|Actions\s*&?\s*Next Steps|Acciones|Actions)[^\n]*\n(.*?)(?=\n##\s|\Z)',
        minutes_text, re.DOTALL | re.IGNORECASE
    )
    if not section_m:
        return actions

    section_text = section_m.group(1)
    header_seen = False

    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith('|'):
            continue
        if '---' in line:
            header_seen = True
            continue

        cells = [c.strip() for c in line.split('|')]
        cells = [c for c in cells if c]
        if not cells:
            continue

        title = cells[0]

        # Skip header row (before separator or if title looks like a column header)
        if not header_seen:
            continue
        if title.lower() in ('acción', 'action', 'tarea', 'task', 'descripción', 'description'):
            continue
        if not title or title.startswith('-'):
            continue

        assignee = cells[1] if len(cells) > 1 else None
        deadline = cells[2] if len(cells) > 2 else None
        if not assignee or assignee in ('-', '—', ''):
            assignee = None
        if not deadline or deadline in ('-', '—', ''):
            deadline = None

        actions.append(Action(
            index=idx,
            type='human',
            prompt='',
            title=title,
            context='',
            archivo='',
            suggested_project=None,
            raw_block=line,
            assignee=assignee,
            deadline=deadline,
        ))
        idx += 1

    return actions
