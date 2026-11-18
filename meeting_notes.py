"""Notas escritas durante una grabacion ("Añadir contexto a la grabacion").

Cada nota se guarda con el minuto de la grabacion en que se escribio, una por
linea en <stem>.context ("[MM:SS] texto"), y al procesar la reunion entra en el
transcript en ese minuto, como una linea mas con su etiqueta. Asi la ven las
minutas, el boton de minutas del panel y "Preguntar", y el usuario la ve en su
sitio.

Antes la nota solo llegaba a las minutas automaticas completas: con las minutas
a mano, el pipeline la leia y la borraba sin usarla, y cada "Guardar" pisaba la
anterior. En la semana del 09/11/2026 se perdieron asi hasta cuatro notas.
"""
import re
from pathlib import Path

_NOTE = re.compile(r'^\[(\d+):(\d{2})\]\s?(.*)$')


def _stamp(seconds: float) -> str:
    m, s = divmod(int(max(seconds, 0)), 60)
    return f"[{m:02d}:{s:02d}]"


def add_note(context_path: Path, seconds: float, text: str) -> str | None:
    """Añade una nota al final, con su minuto. Devuelve la marca de tiempo, o
    None si la nota esta vacia."""
    text = ' '.join(text.split())
    if not text:
        return None
    stamp = _stamp(seconds)
    with open(context_path, 'a', encoding='utf-8') as f:
        f.write(f"{stamp} {text}\n")
    return stamp


def read_notes(context_path: Path) -> list[tuple[int, str]]:
    """[(segundo, texto)] en orden. Un fichero de antes, sin minutos, es una sola
    nota al principio de la reunion."""
    try:
        raw = context_path.read_text(encoding='utf-8')
    except OSError:
        return []
    notes, loose = [], []
    for line in raw.splitlines():
        m = _NOTE.match(line.strip())
        if m and m.group(3).strip():
            notes.append((int(m.group(1)) * 60 + int(m.group(2)), m.group(3).strip()))
        elif line.strip():
            loose.append(line.strip())
    if loose:
        notes.insert(0, (0, ' '.join(loose)))
    return sorted(notes, key=lambda n: n[0])


def note_line(seconds: int, text: str, label: str) -> str:
    return f"{_stamp(seconds)} [{label}]: {text}"


def merge(transcript: str, notes: list[tuple[int, str]], label: str) -> str:
    """El transcript con cada nota detras de la ultima linea de su minuto o antes.
    Las que ya estan no se repiten: el pipeline puede pasar dos veces."""
    lines = transcript.splitlines()
    present = set(lines)
    pending = [(t, note_line(t, text, label)) for t, text in notes]
    pending = [(t, line) for t, line in pending if line not in present]
    if not pending:
        return transcript
    stamps = [(i, int(m.group(1)) * 60 + int(m.group(2)))
              for i, line in enumerate(lines) if (m := _NOTE.match(line))]
    after: dict[int, list[str]] = {}
    for t, note in pending:
        pos = -1
        for i, when in stamps:
            if when > t:
                break
            pos = i
        after.setdefault(pos, []).append(note)
    out = list(after.get(-1, []))
    for i, line in enumerate(lines):
        out.append(line)
        out += after.get(i, [])
    return '\n'.join(out)


def all_in(transcript: str, notes: list[tuple[int, str]]) -> bool:
    """Si todas las notas ya estan en el transcript (en cualquier formato)."""
    return all(text in transcript for _t, text in notes)
