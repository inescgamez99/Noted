"""Analisis de las capturas de pantalla con Claude (vision), dentro del transcript.

Antes, una captura solo dejaba en el transcript la linea con el nombre del
archivo ("[12:30] [Captura]: <archivo>.png"): ni las minutas, ni "Preguntar", ni
quien leia el transcript despues sabian que se veia en pantalla. Ahora, al
procesar la reunion (antes de las minutas), Claude mira cada captura con el
trozo del transcript de alrededor y su analisis entra en el transcript, en la
linea siguiente a la de la captura:

    [12:30] [Captura]: <archivo>.png
    [12:30] [Analisis de captura]: Se muestra la slide de pricing con ...

Asi lo usa todo lo que usa el transcript (minutas, "Generar minutas", preguntas,
la copia que se exporta al proyecto, Claude Code leyendo minutes/) sin tocar
cada uno de esos caminos. La linea de la captura no cambia: es el contrato con
el consumidor de minutes/ (tests/test_consumer_contract.py).

El analisis se guarda tambien en el registro de capturas de la grabacion
(<grabacion>.screenshots.jsonl, campo 'analysis'), que file_for_minutes copia al
manifiesto <stem>_screenshots.json. Asi un reprocesado no vuelve a llamar a
Claude, y la pestana de capturas o un consumidor pueden leerlo.

Si Claude falla o no esta, la reunion sigue como siempre (solo con la linea de
la captura): esto es contexto extra, nunca un motivo para no hacer las minutas.
"""
import json
import logging
import os
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

LABELS = {'es': 'Análisis de captura', 'en': 'Screenshot analysis'}
_WINDOW_S = 240          # transcript de contexto: 4 min antes y 4 despues de cada captura
_MAX_CHARS = 1500        # por analisis, para que no se coma el transcript
_TIMEOUT_S = 600
_STAMP = re.compile(r'^\[(\d+):(\d{2})\]')


def label(lang: str | None) -> str:
    return LABELS['en'] if (lang or '').startswith('en') else LABELS['es']


def all_labels() -> tuple[str, ...]:
    return tuple(LABELS.values())


def _seconds(line: str) -> int | None:
    m = _STAMP.match(line)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _excerpt(transcript: str, t: int, window: int = _WINDOW_S) -> str:
    """Las lineas del transcript a +-window de t, sin otros analisis."""
    out = []
    for line in transcript.splitlines():
        s = _seconds(line)
        if s is None or abs(s - t) > window:
            continue
        if any(f"[{lab}]:" in line for lab in all_labels()):
            continue
        out.append(line)
    return '\n'.join(out)


def _log_path(rec_path: Path) -> Path:
    import screen_capture
    return rec_path.with_suffix(screen_capture.LOG_SUFFIX)


def _stored(rec_path: Path) -> dict[str, str]:
    import screen_capture
    return {e['file']: e['analysis'] for e in screen_capture.read_log(rec_path)
            if isinstance(e.get('analysis'), str) and e['analysis'].strip()}


def _store(rec_path: Path, analyses: dict[str, str]) -> None:
    """Escribe los analisis en el registro de capturas (reescritura atomica)."""
    import screen_capture
    entries = screen_capture.read_log(rec_path)
    if not entries:
        return
    for e in entries:
        if e['file'] in analyses:
            e['analysis'] = analyses[e['file']]
    path = _log_path(rec_path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in entries),
                   encoding='utf-8')
    os.replace(tmp, path)


def _prompt(items: list[tuple[int, str, Path]], transcript: str, lang: str) -> str:
    blocks = []
    for t, name, png in items:
        m, s = divmod(t, 60)
        blocks.append(
            f"### Captura `{name}` (minuto {m:02d}:{s:02d})\n"
            f"Ruta de la imagen: {png}\n"
            f"Transcript de alrededor:\n```\n{_excerpt(transcript, t) or '(sin texto en ese tramo)'}\n```")
    lang_name = 'English' if (lang or '').startswith('en') else 'castellano'
    return (
        "Eres el asistente de minutas de una reunión de Teams. Durante la reunión, quien "
        "grababa hizo capturas de la ventana de la reunión (lo que se compartía en pantalla). "
        "Para cada captura de abajo:\n"
        "1. Abre la imagen con la herramienta Read (es un PNG: la verás).\n"
        "2. Mírala junto con el transcript de alrededor y escribe un análisis breve y "
        "denso: qué se está mostrando (slide, documento, Excel, demo, diagrama...), su título, "
        "las cifras, nombres, fechas y puntos clave que se leen, y cómo encaja con lo que se "
        "estaba diciendo en ese momento. Copia literalmente cifras y nombres; no inventes nada "
        "que no se vea o no se diga. Ignora la interfaz de Teams (barras, botones, caras), "
        "salvo que sea lo único que hay; en ese caso dilo en una frase.\n"
        f"3. Escribe en {lang_name}, en un solo párrafo de 2 a 6 frases, sin saltos de línea "
        "ni viñetas.\n\n"
        "Responde SOLO con un objeto JSON, sin texto antes ni después, con el nombre de "
        "archivo de cada captura como clave y su análisis como valor:\n"
        '{"<archivo>.png": "<análisis>", ...}\n\n'
        + '\n\n'.join(blocks)
    )


def _parse(out: str) -> dict[str, str]:
    out = (out or '').strip()
    m = re.search(r'\{.*\}', out, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    clean = {}
    for k, v in (data.items() if isinstance(data, dict) else []):
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            clean[k.strip()] = ' '.join(v.split())[:_MAX_CHARS]
    return clean


def _ask_claude(folder: Path, prompt: str) -> str | None:
    from config import clean_env, get_claude_bin
    claude = get_claude_bin()
    if not claude:
        log.warning("Análisis de capturas: no se encuentra el CLI de Claude")
        return None
    cmd = [claude, '-p', '--add-dir', str(folder), '--allowedTools', 'Read']
    kw = {}
    if os.name == 'nt':
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw = {'startupinfo': si, 'creationflags': 0x08000000}
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              encoding='utf-8', env=clean_env(), timeout=_TIMEOUT_S,
                              **kw)
    except subprocess.TimeoutExpired:
        log.warning(f"Análisis de capturas: Claude no respondió en {_TIMEOUT_S}s")
        return None
    except Exception as e:
        log.warning(f"Análisis de capturas: no se pudo llamar a Claude: {e}")
        return None
    if proc.returncode != 0:
        detail = ((proc.stderr or '') + ' ' + (proc.stdout or '')).strip()[-300:]
        log.warning(f"Análisis de capturas: Claude falló (rc={proc.returncode}): {detail}")
        return None
    return proc.stdout


def enabled() -> bool:
    """Activado salvo `"screenshot_analysis": false` en settings.json."""
    try:
        from config import PROJECT_DIR
        cfg = json.loads((PROJECT_DIR / 'settings.json').read_text(encoding='utf-8-sig'))
        return cfg.get('screenshot_analysis', True) is not False
    except Exception:
        return True


def analyze(rec_path: Path, transcript: str, shots: list[tuple[int, str]],
            lang: str | None = None) -> dict[str, str]:
    """{archivo: analisis} de las capturas de la grabacion. Las ya analizadas se
    leen del registro; las que faltan se piden a Claude en una sola llamada."""
    done = _stored(rec_path)
    todo = [(t, name, rec_path.with_name(name)) for t, name in shots
            if name not in done and rec_path.with_name(name).exists()]
    if not todo or not enabled():
        return done
    log.info(f"Analizando {len(todo)} captura(s) de {rec_path.name} con Claude...")
    out = _ask_claude(rec_path.parent, _prompt(todo, transcript, lang or 'es'))
    new = {k: v for k, v in _parse(out).items() if k in {n for _t, n, _p in todo}}
    if not new:
        if out is not None:
            log.warning("Análisis de capturas: la respuesta de Claude no traía análisis válidos")
        return done
    try:
        _store(rec_path, new)
    except OSError as e:
        log.warning(f"Análisis de capturas: no se pudo guardar en el registro: {e}")
    log.info(f"Capturas analizadas: {len(new)} de {len(todo)}")
    return {**done, **new}


def merge(transcript: str, analyses: dict[str, str], cap_label: str, lang: str | None = None) -> str:
    """Pone cada analisis en la linea siguiente a la de su captura, con su minuto.
    No repite: si ya hay un analisis justo detras, se deja."""
    if not analyses:
        return transcript
    lab = label(lang)
    lines = transcript.splitlines()
    out = []
    for i, line in enumerate(lines):
        out.append(line)
        m = re.match(r'^(\[\d+:\d{2}\]) \[' + re.escape(cap_label) + r'\]: (\S+\.png)\s*$', line)
        if not m or m.group(2) not in analyses:
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ''
        if any(f"[{x}]:" in nxt for x in all_labels()):
            continue
        out.append(f"{m.group(1)} [{lab}]: {analyses[m.group(2)]}")
    return '\n'.join(out)
