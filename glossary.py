"""Glosario: nombres, siglas y términos que Whisper transcribe mal.

Se usa en tres puntos:
- Pistas para Whisper (`hotwords`): los invitados de la reunión y los términos
  del glosario, para que se transcriban bien desde el principio.
- Correcciones sobre el transcript: los errores ya vistos (`aliases`) se
  sustituyen por la forma correcta, solo como palabra completa.
- Contexto para el prompt de minutas, con la grafía exacta a usar.

glossary.json (junto a settings.json, no se versiona):
    {"entries": [{"term": "Kinaxis", "category": "term",
                  "aliases": ["Kinexis"], "meaning": "", "project": null}],
     "suggestions": [{"term": "Nurettin", "aliases": ["Norethin"],
                      "category": "person", "meaning": "", "source": "<minuta>"}],
     "dismissed": ["nurettin>norethin"]}
`project` es el id de un proyecto, o null si aplica a todos.

Las `suggestions` las propone Claude al generar cada minuta y NUNCA entran solas
en el glosario: el usuario las aprueba o las descarta desde Settings. Lo
descartado queda en `dismissed` para no volver a proponerlo.
"""
import json
import logging
import re
import threading
import time
import unicodedata
from pathlib import Path

from config import PROJECT_DIR

log = logging.getLogger(__name__)

GLOSSARY_FILE = PROJECT_DIR / 'glossary.json'

CATEGORIES = ('person', 'company', 'acronym', 'term', 'english')

# hotwords de faster-whisper se recorta a ~223 tokens. Los nombres propios
# gastan muchos tokens por carácter, así que el tope es conservador: es mejor
# que quepan los invitados y las siglas clave a que se corte el final.
_HOTWORDS_MAX_CHARS = 450
_MAX_INVITEES = 25
_MAX_PROMPT_ENTRIES = 80

_lock = threading.Lock()


# ── lectura y escritura ─────────────────────────────────────────────────────

def _clean_entry(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    term = str(raw.get('term') or '').strip()
    if not term:
        return None
    aliases = [str(a).strip() for a in (raw.get('aliases') or []) if str(a).strip()]
    category = raw.get('category')
    return {
        'term': term,
        'category': category if category in CATEGORIES else 'term',
        'aliases': list(dict.fromkeys(aliases)),
        'meaning': str(raw.get('meaning') or '').strip(),
        'project': raw.get('project') or None,
    }


def _clean_suggestion(raw) -> dict | None:
    entry = _clean_entry(raw)
    if not entry or not entry['aliases']:
        return None
    entry['source'] = str(raw.get('source') or '')
    del entry['project']
    return entry


def load() -> dict:
    """Siempre devuelve {'entries', 'suggestions', 'dismissed'}, aunque el
    fichero no exista o esté roto: el glosario es un extra y nunca debe tumbar
    la transcripción ni las minutas."""
    data = {'entries': [], 'suggestions': [], 'dismissed': []}
    try:
        raw = json.loads(Path(GLOSSARY_FILE).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return data
    except Exception as e:
        log.warning(f"glossary.json ilegible, se ignora: {e}")
        return data
    if isinstance(raw, dict):
        data['entries'] = [e for e in map(_clean_entry, raw.get('entries') or []) if e]
        data['suggestions'] = [s for s in map(_clean_suggestion, raw.get('suggestions') or []) if s]
        data['dismissed'] = [str(d) for d in (raw.get('dismissed') or []) if d]
    return data


def save(data: dict) -> bool:
    payload = {
        'entries': [e for e in map(_clean_entry, data.get('entries') or []) if e],
        'suggestions': [s for s in map(_clean_suggestion, data.get('suggestions') or []) if s],
        'dismissed': [str(d) for d in (data.get('dismissed') or []) if d],
    }
    path = Path(GLOSSARY_FILE)
    tmp = path.with_name(path.name + '.tmp')
    with _lock:
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            _replace(tmp, path)
            return True
        except OSError as e:
            log.warning(f"No se pudo guardar glossary.json: {e}")
            return False


def _replace(tmp: Path, path: Path, attempts: int = 10, wait: float = 0.05) -> None:
    """os.replace con reintentos. En Windows falla con "Access is denied"
    (WinError 5) si otro proceso tiene el destino abierto en ese instante: el
    antivirus o el indexador, que acaban de ver el fichero. Pasa en los tests
    y en uso normal, y sin reintento se perdia la entrada recien guardada."""
    for i in range(attempts):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(wait)


def entries(project_id: str | None = None) -> list[dict]:
    """Entradas globales más las del proyecto indicado."""
    return [e for e in load()['entries']
            if not e['project'] or (project_id and e['project'] == project_id)]


# ── edición (la usa Settings) ───────────────────────────────────────────────

def _key(term: str, project: str | None) -> tuple:
    return (_fold(term), project or None)


def _pair(term: str, alias: str) -> str:
    return f"{_fold(term)}>{_fold(alias)}"


def _upsert(data: dict, new: dict, original_key: tuple | None = None) -> None:
    """Pone `new` en data['entries']. Si ya había otra con el mismo término y
    ámbito se fusionan (errores y significado), en vez de quedar duplicadas."""
    nkey = _key(new['term'], new['project'])
    out, placed = [], False
    for e in data['entries']:
        k = _key(e['term'], e['project'])
        if original_key is not None and k == original_key:
            if not placed:
                out.append(new)
                placed = True
            continue
        if k == nkey:
            new['aliases'] = list(dict.fromkeys(new['aliases'] + e['aliases']))
            new['meaning'] = new['meaning'] or e['meaning']
            if not placed:
                out.append(new)
                placed = True
            continue
        out.append(e)
    if not placed:
        out.append(new)
    data['entries'] = out


def upsert_entry(entry: dict, original: dict | None = None) -> dict:
    """Crea o edita una entrada. `original` ({'term', 'project'}) identifica la
    que se está editando, por si cambia el término o el ámbito. Lanza
    ValueError si el término está vacío."""
    new = _clean_entry(entry)
    if not new:
        raise ValueError('term')
    data = load()
    okey = _key(original['term'], original.get('project')) if original else None
    _upsert(data, new, okey)
    save(data)
    return new


def delete_entry(term: str, project: str | None = None) -> bool:
    data = load()
    key = _key(term, project)
    kept = [e for e in data['entries'] if _key(e['term'], e['project']) != key]
    if len(kept) == len(data['entries']):
        return False
    data['entries'] = kept
    return save(data)


# ── sugerencias: las propone Claude, las decide el usuario ──────────────────

_MAX_SUGGESTIONS_PER_MEETING = 8
_MAX_FIELD_CHARS = 60
_SUGGEST_TRANSCRIPT_CHARS = 120_000


def _already_known(data: dict, term: str, alias: str) -> bool:
    """Ese error ya está corregido, o ya está descartado."""
    if _pair(term, alias) in data['dismissed']:
        return True
    a = _fold(alias)
    return any(a in (_fold(x) for x in e['aliases']) for e in data['entries'])


def validate_suggestions(raw, transcript: str) -> list[dict]:
    """Filtra lo que devuelve Claude. Nunca se fía de él: cada error propuesto
    tiene que aparecer de verdad, como palabra completa, en el transcript, y
    no puede estar ya en el glosario ni haber sido descartado."""
    if not isinstance(raw, list):
        return []
    data = load()
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        term = str(item.get('term') or '').strip()
        if not term or len(term) > _MAX_FIELD_CHARS:
            continue
        aliases = []
        for a in item.get('aliases') or []:
            a = str(a).strip()
            if (a and len(a) <= _MAX_FIELD_CHARS and _fold(a) != _fold(term)
                    and _alias_pattern(a).search(transcript or '')
                    and not _already_known(data, term, a)):
                aliases.append(a)
        if not aliases:
            continue
        category = item.get('category')
        out.append({
            'term': term,
            'category': category if category in CATEGORIES else 'term',
            'aliases': list(dict.fromkeys(aliases)),
            'meaning': str(item.get('meaning') or '').strip()[:200],
        })
        if len(out) >= _MAX_SUGGESTIONS_PER_MEETING:
            break
    return out


def add_suggestions(items: list[dict], source: str) -> int:
    """Guarda sugerencias ya validadas, fusionando las del mismo término.
    Devuelve cuántas quedan nuevas."""
    if not items:
        return 0
    data = load()
    by_term = {_fold(s['term']): s for s in data['suggestions']}
    added = 0
    for it in items:
        aliases = [a for a in it['aliases'] if not _already_known(data, it['term'], a)]
        if not aliases:
            continue
        cur = by_term.get(_fold(it['term']))
        if cur:
            cur['aliases'] = list(dict.fromkeys(cur['aliases'] + aliases))
        else:
            new = {**it, 'aliases': aliases, 'source': source}
            data['suggestions'].append(new)
            by_term[_fold(it['term'])] = new
            added += 1
    save(data)
    return added


def _pop_suggestion(data: dict, term: str) -> dict | None:
    key = _fold(term)
    for i, s in enumerate(data['suggestions']):
        if _fold(s['term']) == key:
            return data['suggestions'].pop(i)
    return None


def approve_suggestion(term: str, edits: dict | None = None) -> dict | None:
    """Convierte una sugerencia en entrada del glosario. `edits` permite
    corregirla antes (término, categoría, errores, significado, proyecto)."""
    data = load()
    sug = _pop_suggestion(data, term)
    if not sug:
        return None
    merged = {**sug, 'project': None, **(edits or {})}
    new = _clean_entry(merged)
    if not new:
        return None
    _upsert(data, new)
    save(data)
    return new


def dismiss_suggestion(term: str) -> bool:
    """Descarta una sugerencia y recuerda cada par (término, error) para no
    volver a proponerlo en la siguiente reunión."""
    data = load()
    sug = _pop_suggestion(data, term)
    if not sug:
        return False
    for a in sug['aliases']:
        p = _pair(sug['term'], a)
        if p not in data['dismissed']:
            data['dismissed'].append(p)
    return save(data)


_SUGGEST_PROMPT = """You review the transcript of a work meeting that was transcribed automatically by Whisper, which often misspells proper names, companies, products and acronyms.

Find words in the TRANSCRIPT that are clearly WRONG spellings of a proper name, company, product or acronym, and whose CORRECT spelling you can establish from: the invitee list, the minutes, or consistent correct usage elsewhere in the transcript.

Reply with ONLY a JSON array, no prose, at most {max_items} items:
[{{"term": "correct spelling", "aliases": ["wrong spelling exactly as written in the transcript"], "category": "person|company|acronym|term|english", "meaning": "short explanation, or empty"}}]

Rules:
- High confidence only. If you are not sure the correct spelling, leave it out.
- Every alias must appear verbatim in the transcript.
- Never include ordinary words, and never include anything already in the glossary below.
- If there is nothing to suggest, reply [].

## Glossary (already known)
{known}

## Invitees
{invitees}

## Minutes (use the spellings Claude chose there as a hint)
{minutes}

## Transcript
{transcript}"""


def _parse_json_list(text: str) -> list | None:
    text = (text or '').strip()
    a, b = text.find('['), text.rfind(']')
    if a == -1 or b <= a:
        return None
    try:
        out = json.loads(text[a:b + 1])
    except ValueError:
        return None
    return out if isinstance(out, list) else None


def suggest_from_meeting(transcript: str, minutes_text: str, invitees: list[str],
                         source: str) -> int:
    """Pide a Claude posibles correcciones para esta reunión y guarda las que
    pasan la validación como sugerencias pendientes. Devuelve cuántas nuevas.
    Es un extra: si Claude falla, simplemente no hay sugerencias."""
    import minutes_generator
    from minutes_generator import compact_transcript

    data = load()
    known = '\n'.join(
        f"- {e['term']}" + (f" (errors: {', '.join(e['aliases'])})" if e['aliases'] else '')
        for e in data['entries']) or '(empty)'
    prompt = _SUGGEST_PROMPT.format(
        max_items=_MAX_SUGGESTIONS_PER_MEETING, known=known,
        invitees=', '.join(invitees) or '(none)',
        minutes=(minutes_text or '')[:20_000],
        transcript=compact_transcript(transcript or '')[:_SUGGEST_TRANSCRIPT_CHARS])

    out = minutes_generator.run_claude(prompt, timeout=300)
    if not out:
        return 0
    items = validate_suggestions(_parse_json_list(out), transcript)
    return add_suggestions(items, source)


# ── texto ───────────────────────────────────────────────────────────────────

def _fold(text: str) -> str:
    text = unicodedata.normalize('NFKD', text or '')
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


def _alias_pattern(alias: str) -> re.Pattern:
    # (?<!\w)/(?!\w) y no \b: el alias puede empezar o acabar en un símbolo
    # ("S&OP"), donde \b no funciona.
    return re.compile(rf'(?<!\w){re.escape(alias)}(?!\w)', re.IGNORECASE)


def apply_corrections(text: str, glossary_entries: list[dict]) -> str:
    """Sustituye los errores conocidos por la forma correcta, como palabra
    completa y sin distinguir mayúsculas. Los más largos primero, para que
    "Kinaxis Maestro" no lo pise antes "Kinaxis"."""
    if not text or not glossary_entries:
        return text
    pairs = []
    for e in glossary_entries:
        for alias in e.get('aliases', []):
            if alias and alias.lower() != e['term'].lower():
                pairs.append((alias, e['term']))
    for alias, term in sorted(pairs, key=lambda p: -len(p[0])):
        text = _alias_pattern(alias).sub(lambda _m, t=term: t, text)
    return text


def build_hotwords(glossary_entries: list[dict], invitees: list[str] | None = None,
                   subject: str = '') -> str:
    """Pistas para Whisper, por orden de utilidad: invitados, asunto, y después
    las entradas del glosario. Se corta al llegar al límite de caracteres."""
    candidates = [n for n in (invitees or [])[:_MAX_INVITEES] if n]
    if subject:
        candidates.append(subject)
    order = {'person': 0, 'acronym': 1, 'company': 2, 'term': 3, 'english': 4}
    for e in sorted(glossary_entries, key=lambda e: order.get(e['category'], 9)):
        candidates.append(e['term'])

    seen, out, size = set(), [], 0
    for c in candidates:
        key = _fold(c)
        if key in seen:
            continue
        if size + len(c) + 2 > _HOTWORDS_MAX_CHARS:
            break
        seen.add(key)
        out.append(c)
        size += len(c) + 2
    return ', '.join(out)


def prompt_section(glossary_entries: list[dict], transcript: str, lang: str) -> str:
    """Bloque para el prompt de minutas. Si hay más entradas que el tope, van
    primero las que aparecen en el transcript (término o error conocido), que
    son las que importan."""
    if not glossary_entries:
        return ''
    spoken = _fold(transcript)

    def hit(e):
        names = [e['term']] + e['aliases']
        return any(re.search(rf'(?<!\w){re.escape(_fold(n))}(?!\w)', spoken) for n in names if n)

    ranked = sorted(glossary_entries, key=lambda e: not hit(e))[:_MAX_PROMPT_ENTRIES]
    heard = 'also heard as' if lang == 'en' else 'a veces transcrito como'
    head = ("\n## Glossary (use these exact spellings; the transcript may spell them wrong)"
            if lang == 'en' else
            "\n## Glosario (usa exactamente estas grafías; el transcript puede escribirlas mal)")
    lines = []
    for e in ranked:
        line = f"- {e['term']}"
        if e['meaning']:
            line += f" = {e['meaning']}"
        if e['aliases']:
            line += f" ({heard}: {', '.join(e['aliases'])})"
        lines.append(line)
    return head + '\n' + '\n'.join(lines)


# ── contexto de una grabación (invitados + proyecto) ───────────────────────

_context_cache: dict[str, dict] = {}


# Pistas (hotwords) para Whisper. Desactivadas desde el 18/11/2026: con ellas, Whisper
# escribia casi nada en ciertos tramos con voz. Mismo tramo de 125 s de una reunion:
# 32 lineas y 303 palabras sin pistas, 5 lineas y 64 con las del glosario (y 4 con solo
# 8 terminos, 21 con 16, 4 con 28: es inestabilidad, no depende de la longitud). Lo que
# se perdia no era un eco de las pistas, asi que _redo_echoes no lo detectaba. Los
# nombres se siguen arreglando despues: apply_corrections sobre el transcript y la
# grafia exacta en el prompt de las minutas. Para volver a pasarlas a Whisper: True.
WHISPER_HOTWORDS = False


def recording_context(wav_path: Path) -> dict:
    """{'hotwords', 'entries', 'project_id'} para una grabación.

    'hotwords' va vacio mientras WHISPER_HOTWORDS sea False (ver arriba): entonces
    tampoco se consulta Outlook, porque los invitados solo servian para las pistas.

    Busca la reunión en el calendario por la hora y el nombre del fichero, igual
    que hace el pipeline para las minutas, y detecta el proyecto por el nombre.
    Se cachea por grabación: lo piden la transcripción en vivo, la final y las
    correcciones, y la consulta a Outlook no es gratis. Nunca lanza.
    """
    stem = Path(wav_path).stem
    if stem in _context_cache:
        return _context_cache[stem]

    ctx = {'hotwords': '', 'entries': [], 'project_id': None}
    try:
        name = None
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_(.+))?', stem)
        if m and m.group(6) and m.group(6).strip().lower() not in ('manual', 'recording'):
            name = m.group(6).replace('_', ' ')

        try:
            from project_context import detect_project
            proj = detect_project('', name or '')
            ctx['project_id'] = proj.get('id') if proj else None
        except Exception as e:
            log.debug(f"glosario: sin detección de proyecto: {e}")

        ctx['entries'] = entries(ctx['project_id'])

        if WHISPER_HOTWORDS:
            invitees, subject = [], ''
            if m:
                try:
                    from datetime import datetime
                    from outlook_sender import find_meeting_participants
                    when = datetime(*(int(m.group(i)) for i in range(1, 6)))
                    people = find_meeting_participants(when, meeting_name=name)
                    invitees = [p['name'] for p in people if p.get('name')]
                    subject = next((p['meeting_subject'] for p in people if p.get('meeting_subject')), '')
                except Exception as e:
                    log.debug(f"glosario: sin invitados de calendario: {e}")

            ctx['hotwords'] = build_hotwords(ctx['entries'], invitees, subject)
    except Exception as e:
        log.warning(f"glosario: contexto de grabación no disponible: {e}")

    _context_cache[stem] = ctx
    return ctx
