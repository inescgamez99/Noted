"""Transcripcion durante la reunion, en dos modos.

DOS MODOS (no cambiarlo sin leer esto)
- Mientras se graba, un proceso APARTE (`main`) lee los temporales que ya escribe
  el grabador (<stem>.mic.part / .loop.part), los transcribe por trozos y AÑADE
  cada trozo terminado a <stem>.live.jsonl. El daemon lo gobierna con
  <stem>.live.ctl: {"mode": "background"|"live", "gate": "free"|"yield"}.
  - background (por defecto): el modelo de siempre (WHISPER_MODEL), trozos
    largos, prioridad minima. No es para verlo al momento sino para ir ganando
    tiempo: al colgar queda poco por hacer. Medido el 19/10/2026: una reunion de
    48 min tardo 84 min en transcribirse al colgar.
  - live: 'small', trozos cortos, para verlo ya (subtitulos, preguntas). El daemon
    pausa cualquier otra transcripcion mientras dura. 'small' coincide con
    'medium' en un 91-95% de las palabras (19/10/2026), asi que el transcript que
    se guarda sigue siendo el del modelo de siempre, salvo "live_transcript_final".
- Nunca hay dos modelos grandes a la vez: cuando el pipeline necesita Whisper
  (otra reunion), pone gate=yield y el proceso descarga el modelo y espera.
- Al parar, el daemon mata el proceso y cierra con `finish_live`: aprovecha los
  trozos hechos con el modelo aceptado y transcribe solo los huecos y el final.
  Si no hay nada aprovechable, el pipeline transcribe como siempre.
- El glosario (glossary.py) llega como pistas para Whisper: el daemon escribe
  <stem>.live.hints al empezar y este proceso las usa en cuanto aparecen; el
  cierre usa las mismas. Las correcciones del texto las aplica el daemon una
  sola vez, sobre el transcript entero (tray_app._use_live_transcript).

Por que un proceso aparte: el 16/10/2026 Whisper revento con heap corruption
(0xC0000374) dentro del daemon. Dentro del proceso que graba, eso corta la reunion.
Por que cierra el daemon: si los dos tocan los mismos ficheros al colgar, un
borrado de Windows sobre un fichero abierto (WinError 32) tumba la transcripcion
entera. Con el proceso en vivo ya muerto, no puede pasar.

El daemon importa este modulo: arriba solo hay stdlib. numpy, soundfile, psutil y
faster-whisper se importan dentro de las funciones.
"""
import json
import logging
import os
import struct
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)

MODE_BACKGROUND, MODE_LIVE = 'background', 'live'
MODES = (MODE_BACKGROUND, MODE_LIVE)

# Medido el 18/10/2026 en trozos de 15 s con 4 hilos: 'small' necesita 0,26 s de
# calculo por segundo de audio (0,54 con la maquina cargada); 'medium', 0,72 (1,87
# cargada), asi que con Teams abierto no aguanta en vivo.
LIVE_MODEL = 'small'
SR = 16000

# Whisper rellena cada llamada hasta 30 s, asi que un trozo de 5 s cuesta casi lo
# mismo que uno de 30: trozos largos, cortados en una pausa para no partir
# palabras. En el modo de fondo, mas largos todavia: no hay prisa por verlo, y
# cada corte cuesta una ventana parcial de mas.
MIN_CHUNK_S = 15
MAX_CHUNK_S = 30
PROFILES = {
    MODE_LIVE: {'min_s': MIN_CHUNK_S, 'max_s': MAX_CHUNK_S, 'threads': 4},
    MODE_BACKGROUND: {'min_s': 90, 'max_s': 180, 'threads': 4},
}
MIN_PAUSE_S = 0.5
POLL_S = 1.0

# Al pasar a en vivo a mitad de reunion se empieza aqui de "ahora", no donde iba
# el modo de fondo: con minutos de atraso, 'small' tendria que ponerse al dia y no
# seria "ya". El hueco lo rellena el cierre. Un proceso lanzado ya en vivo a mitad
# de reunion lo recibe en la linea de comandos (lo decide el daemon, no un retraso).
LIVE_JUMP_BACK_S = 30
FROM_NOW_ARG = '--from-now'

# El proceso se va solo si el daemon muere o si el micro deja de crecer este
# tiempo: un proceso con un modelo cargado no puede quedarse residente.
STALL_EXIT_S = 600

# Se lanza a los START_DELAY_S de empezar y con el loopback ya arrancado:
# - no compite con los intentos del worker WASAPI justo cuando Teams reconfigura
#   el audio. Desde 77140da cada intento espera la cabecera hasta 15 s
#   (audio_recorder._LOOPBACK_HEADER_TIMEOUT): la primera ronda de 3 llega a
#   unos 50 s, y con 25 se lanzaba en mitad de ella;
# - una grabacion de pocos segundos no llega a lanzarlo. Medido el 18/10/2026:
#   un proceso recien nacido deniega el acceso unos segundos, y matarlo en ese
#   momento lo dejaba suspendido y vivo. No se pierde nada por esperar: lo que
#   falte lo hace el cierre.
START_DELAY_S = 20
LOOPBACK_WAIT_S = 50

# Bloques del cierre: el mismo tamaño que transcriber._CHUNK_SECS (acota la
# memoria), cortados en una pausa cerca del limite.
FINISH_BLOCK_S = 300

# Idioma. En vivo se fija la primera vez que un trozo lo deja claro; en el modo de
# fondo, como el post-reunion: una vez, con el tramo mas seguro de micro y Teams.
LANG_LOCK_PROB = 0.8
LANG_MIN_VOICE_S = 30
LANG_FALLBACK_AFTER_S = 180
# Despues, cada pista lo vuelve a mirar como mucho cada LANG_RECHECK_S de su audio
# (transcriber.recheck_language): en el modo de fondo, casi en cada trozo.
LANG_RECHECK_S = 120
# Si la primera deteccion no llega a LANG_LOCK_PROB, no se fija: se vuelve a mirar
# con el audio de mas adelante, a partir de este salto.
LANG_RETRY_STEP_S = 150

# Memoria. Lo que cuenta es la RESERVA contra el limite de Windows (commit), no
# la RAM en uso: 'medium' reserva ~4,7 GB al cargarse y solo toca ~0,9 GB
# (transcriber.py, 14/10/2026). Quedarse sin reserva agranda el pagefile hasta
# llenar el disco, y con el disco lleno el grabador falla a mitad de reunion.
_MODEL_COMMIT_MB = {'tiny': 400, 'base': 600, 'small': 1500, 'medium': 5000,
                    'large': 10000, 'large-v2': 10000, 'large-v3': 10000}
# 1 GB y no 2 (decision del 23/10/2026): con 2 GB, en el portatil de Felipe
# (43 de 46 GB de reserva comprometidos con sus apps abiertas) el modo de fondo
# no llego a cargar 'medium' en toda una llamada de 30 min. Sigue quedando
# 1 GB libre tras cargar, y COMMIT_FLOOR_MB lo suelta si baja de ahi.
COMMIT_MARGIN_MB = 1024
WAITING_LOG_EVERY_S = 300
RAM_MARGIN_MB = 1024
COMMIT_FLOOR_MB = 1024

# El cierre se rinde al segundo intento fallido (p. ej. el daemon cayo en medio).
FINISH_MAX_ATTEMPTS = 2

TRACKS = ('mic', 'loop')


# ── Rutas ────────────────────────────────────────────────────────────────────

def jsonl_path(wav: Path) -> Path:
    return wav.with_suffix('.live.jsonl')


def status_path(wav: Path) -> Path:
    return wav.with_suffix('.live.json')


def ctl_path(wav: Path) -> Path:
    return wav.with_suffix('.live.ctl')


def finishing_path(wav: Path) -> Path:
    """Intentos de cierre: si sobrevive a un arranque, ese cierre tumbo el daemon."""
    return wav.with_suffix('.live.finishing')


def hints_path(wav: Path) -> Path:
    """Pistas del glosario para Whisper (hotwords): invitados, asunto y terminos."""
    return wav.with_suffix('.live.hints')


def write_hints(wav: Path, hotwords: str) -> None:
    """Las escribe el daemon al empezar (tray_app._write_live_hints): calcularlas
    puede consultar Outlook, y este proceso no debe esperar por eso. Atomico: el
    proceso en vivo las lee en cualquier momento."""
    tmp = hints_path(wav).with_name(hints_path(wav).name + '.tmp')
    tmp.write_text(json.dumps({'hotwords': hotwords or ''}, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, hints_path(wav))


def _load_hints(args: dict, wav: Path) -> bool:
    """Pone las pistas en args si ya estan. Devuelve si ya no hace falta volver a
    mirar: estan, o el fichero existe pero no sirve. Sin pistas se transcribe igual."""
    path = hints_path(wav)
    if not path.exists():
        return False
    try:
        hotwords = json.loads(path.read_text(encoding='utf-8')).get('hotwords') or ''
    except (OSError, ValueError, AttributeError) as e:
        log.warning(f"live: pistas del glosario ilegibles ({e}); sigo sin ellas")
        return True
    if hotwords:
        args['hotwords'] = hotwords
        log.info("live: pistas del glosario cargadas")
    return True


def part_path(wav: Path, track: str) -> Path:
    return wav.with_suffix(f'.{track}.part')


def track_wav(wav: Path, track: str) -> Path:
    return wav.with_suffix(f'.{track}.wav')


# ── Ajustes ──────────────────────────────────────────────────────────────────

def _settings() -> dict | None:
    """settings.json releido en cada llamada (como teams_chat_notice_enabled), o
    None si no se puede leer."""
    try:
        from config import PROJECT_DIR
        p = PROJECT_DIR / 'settings.json'
        return json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else {}
    except Exception as e:
        log.warning(f"live: no se pudo leer settings.json ({e})")
        return None


def background_enabled() -> bool:
    """`background_transcription`: transcribir durante la reunion en el modo por
    defecto. Activado si falta la clave; si el fichero no se puede leer se queda
    apagado, que es la app de siempre. El modo en vivo no depende de esto: lo
    pide el usuario expresamente.

    Tambien lo apagan los interruptores de la transcripcion en vivo anterior
    (77140da), para que quien la tenia apagada no la encuentre encendida:
    `live_transcription: false` en settings.json o LIVE_TRANSCRIPTION=0."""
    s = _settings()
    if s is None or s.get('background_transcription', True) is False:
        return False
    if s.get('live_transcription', True) is False:
        return False
    return os.getenv('LIVE_TRANSCRIPTION', '1').strip().lower() not in ('0', 'false', 'no', 'off')


def final_enabled() -> bool:
    """`live_transcript_final`: en el modo en vivo, guardar el transcript de
    'small' como definitivo (rapido, menos preciso). Solo si vale true."""
    s = _settings()
    return s is not None and s.get('live_transcript_final', False) is True


def final_model() -> str:
    """El modelo del transcript que se guarda: el de siempre (Ajustes)."""
    import transcriber
    return transcriber.WHISPER_MODEL


# ── Ficheros de estado: atomicos y tolerantes ────────────────────────────────

def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> bool:
    """Reescritura atomica. Si un lector tiene el fichero abierto, os.replace
    falla en Windows: se reintenta, y si no hay suerte se deja el anterior."""
    tmp = path.with_name(path.name + '.tmp')
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        for _ in range(20):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                time.sleep(0.05)
        log.warning(f"live: no se pudo actualizar {path.name}")
    except OSError as e:
        log.warning(f"live: {path.name} no escrito ({e})")
    return False


def read_status(wav: Path) -> dict:
    return _read_json(status_path(wav))


def _write_status(wav: Path, **fields) -> None:
    """El estado es informativo; nunca debe tumbar el proceso."""
    data = read_status(wav)
    data.update(fields)
    _write_json(status_path(wav), data)


def read_ctl(wav: Path) -> dict:
    """Lo que manda el daemon. Sin fichero o con valores raros: fondo y libre.

    yield_id numera cada peticion de ceder el modelo: el proceso lo devuelve al
    cederlo, y asi un 'yielded' de una peticion anterior no vale para la nueva."""
    data = _read_json(ctl_path(wav))
    mode = data.get('mode') if data.get('mode') in MODES else MODE_BACKGROUND
    gate = data.get('gate') if data.get('gate') in ('free', 'yield') else 'free'
    yield_id = data.get('yield_id') if isinstance(data.get('yield_id'), int) else 0
    return {'mode': mode, 'gate': gate, 'yield_id': yield_id}


def write_ctl(wav: Path, **fields) -> bool:
    data = read_ctl(wav)
    data.update({k: v for k, v in fields.items() if v is not None})
    return _write_json(ctl_path(wav), data)


SUSPECT_VOICED_S = 30.0  # >= esto de voz y <= 1 linea por cada tanto -> aviso (150 s y 5 lineas avisa)


def _append_record(wav: Path, record: dict) -> None:
    """Solo se añade, nunca se reescribe: un lector nunca ve el fichero a medias
    salvo la ultima linea, que read_records ignora si esta incompleta."""
    with open(jsonl_path(wav), 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
        f.flush()


def read_records(wav: Path) -> list[dict]:
    try:
        raw = jsonl_path(wav).read_text(encoding='utf-8')
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if (isinstance(r, dict) and r.get('track') in TRACKS
                and isinstance(r.get('start_frame'), int)
                and isinstance(r.get('end_frame'), int)
                and r['end_frame'] > r['start_frame']
                and isinstance(r.get('segments'), list)):
            out.append(r)
    return out


def record_model(r: dict) -> str:
    """Los registros anteriores al campo 'model' eran todos de 'small'."""
    return r.get('model') or LIVE_MODEL


def accepted_records(records: list[dict], track: str, accept=None) -> list[dict]:
    """Registros aceptados de una pista, en orden y sin solapes.

    Un registro que empieza antes de donde acaba el anterior ya tomado se
    descarta: dos escritores (un proceso que sobrevivio a su kill y el nuevo)
    podrian haber hecho el mismo tramo, y el texto saldria repetido.
    """
    out, end = [], 0
    rs = (r for r in records if r['track'] == track and (accept is None or accept(r)))
    for r in sorted(rs, key=lambda r: (r['start_frame'], -r['end_frame'])):
        if r['start_frame'] < end:
            continue
        out.append(r)
        end = r['end_frame']
    return out


def gaps(kept: list[dict], frames: int) -> list[tuple[int, int]]:
    """Tramos [a, b) de la pista que no cubre ningun registro aceptado."""
    out, pos = [], 0
    for r in kept:
        if r['start_frame'] > pos:
            out.append((pos, min(r['start_frame'], frames)))
        pos = max(pos, r['end_frame'])
    if pos < frames:
        out.append((pos, frames))
    return [(a, b) for a, b in out if b > a]


def resume_frame(records: list[dict], track: str) -> int:
    """Donde sigue un proceso relanzado: despues del ultimo registro de la pista."""
    ends = [r['end_frame'] for r in records if r['track'] == track]
    return max(ends) if ends else 0


def render(by_track: dict, labelled: bool) -> str:
    """El mismo formato que el post-reunion (transcriber._transcribe_with_speakers).

    Sin loopback no hay forma de saber quien habla: el micro capta a todos los
    de la sala. Por eso, como hoy, sin loopback las lineas van sin etiqueta.
    """
    from transcriber import ME_NAME, _format_time
    me = f'[{ME_NAME}]' if ME_NAME else '[Grabador]'
    mic = by_track.get('mic', [])
    if labelled:
        mic = _without_echo(mic, by_track.get('loop', []))
    items = [(t, me, text) for t, text in mic]
    if labelled:
        items += [(t, '[Otros]', text) for t, text in by_track.get('loop', [])]
    items.sort(key=lambda x: x[0])
    if labelled:
        return '\n'.join(f"{_format_time(t)} {who}: {text}" for t, who, text in items)
    return '\n'.join(f"{_format_time(t)} {text}" for t, _who, text in items)


def _with_ends(segs: list[tuple[float, str]]) -> list[tuple[float, float, str]]:
    """(inicio, fin, texto): aqui solo hay inicios; el fin es el inicio del
    siguiente segmento de la misma pista, como mucho 15 s despues."""
    segs = sorted(segs)
    out = []
    for i, (t, text) in enumerate(segs):
        nxt = segs[i + 1][0] if i + 1 < len(segs) else t + 15.0
        out.append((t, min(nxt, t + 15.0), text))
    return out


def _without_echo(mic: list[tuple[float, str]], loop: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Sin cascos el micro capta a los demas, y esas frases ya estan
    en la pista de Teams. Mismo criterio que el post-reunion (transcriber._drop_mic_echo)."""
    if not mic or not loop:
        return mic
    try:
        from transcriber import _drop_mic_echo
        kept, _n = _drop_mic_echo(_with_ends(mic), _with_ends(loop))
        return [(t, text) for t, _e, text in kept]
    except Exception as e:
        log.warning(f"Filtro de eco no aplicado: {e}")
        return mic


def _segments(kept: list[dict]) -> list[tuple[float, str]]:
    return [(float(s[0]), str(s[1])) for r in kept for s in r['segments']
            if isinstance(s, list) and len(s) == 2 and str(s[1]).strip()]


def live_view(wav: Path) -> dict:
    """Lo que muestra el panel mientras se graba: todo lo hecho, en orden."""
    records = read_records(wav)
    by_track = {t: _segments(accepted_records(records, t)) for t in TRACKS}
    st = read_status(wav)
    return {
        'lines': [ln for ln in render(by_track, labelled=bool(by_track['loop'])).splitlines() if ln],
        'state': st.get('state'),
        'lang': st.get('lang'),
        'mode': read_ctl(wav)['mode'] if ctl_path(wav).exists() else None,
    }


# ── Lectura de los .part mientras el grabador escribe ───────────────────────

_WAVE_FORMAT_IEEE_FLOAT = 3
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE


def _parse_header(head: bytes) -> tuple[int, int] | None:
    """(offset de los datos, bytes por frame) de un WAV float32 mono, o None.

    Se recorren los chunks: la cabecera de un .part ocupa 80 bytes (fmt, fact y
    PEAK) y no se puede suponer 44. Mientras el fichero esta abierto, libsndfile
    deja a 0 el tamaño del chunk de datos, asi que los frames salen del tamaño
    real del fichero y no de la cabecera.
    """
    if len(head) < 12 or head[:4] != b'RIFF' or head[8:12] != b'WAVE':
        return None
    pos, fmt = 12, None
    while pos + 8 <= len(head):
        cid = head[pos:pos + 4]
        size = struct.unpack('<I', head[pos + 4:pos + 8])[0]
        if cid == b'fmt ':
            if pos + 24 > len(head):
                return None
            tag, channels, _sr, _bps, block_align, bits = struct.unpack('<HHIIHH', head[pos + 8:pos + 24])
            fmt = (tag, channels, block_align, bits)
        elif cid == b'data':
            if fmt is None:
                return None
            tag, channels, block_align, bits = fmt
            if tag not in (_WAVE_FORMAT_IEEE_FLOAT, _WAVE_FORMAT_EXTENSIBLE) or channels != 1 or bits != 32:
                return None
            return pos + 8, block_align
        pos += 8 + size + (size & 1)
    return None


def read_part(path: Path, start: int, max_frames: int):
    """Frames nuevos de un .part que el grabador sigue escribiendo.

    Abre, lee y cierra: nunca deja un handle abierto entre lecturas. El open() de
    Python comparte lectura y escritura pero NO borrado, asi que no puede impedir
    que el grabador escriba ni que recree el fichero (lo hace si falla un intento
    de loopback). Lo peor posible es que un borrado coincida con esta lectura
    (<1 ms) y el .part se quede huerfano: ocupa disco, no se pierde audio.

    Devuelve (audio float32, frames que hay en disco); (vacio, 0) si no se puede.
    """
    import numpy as np
    empty = np.zeros(0, dtype=np.float32)
    try:
        with open(path, 'rb') as f:
            hdr = _parse_header(f.read(4096))
            if hdr is None:
                return empty, 0
            offset, bpf = hdr
            total = max(0, (os.fstat(f.fileno()).st_size - offset) // bpf)
            n = max(0, min(max_frames, total - start))
            if n == 0:
                return empty, total
            f.seek(offset + start * bpf)
            raw = f.read(n * bpf)
    except OSError:
        return empty, 0
    n = len(raw) // bpf
    return np.frombuffer(raw[:n * bpf], dtype='<f4').copy(), total


def choose_cut(n: int, speech: list[dict], min_s: float = None, max_s: float = None) -> int | None:
    """Donde cortar un bufer de n frames, o None para esperar mas audio.

    speech: tramos con voz [{'start', 'end'}] en frames (get_speech_timestamps,
    sin relleno). Se corta en mitad de la ultima pausa de al menos MIN_PAUSE_S que
    deje un trozo de entre min_s y max_s. Si nadie calla, a los max_s. Un bufer
    sin voz se compromete entero, sin transcribir.
    """
    min_s = MIN_CHUNK_S if min_s is None else min_s
    max_s = MAX_CHUNK_S if max_s is None else max_s
    lo, hi, pause = int(min_s * SR), int(max_s * SR), int(MIN_PAUSE_S * SR)
    if n < lo:
        return None
    if not speech:
        return min(n, hi)
    gap_list, prev_end = [], 0
    for s in speech:
        gap_list.append((prev_end, s['start']))
        prev_end = s['end']
    gap_list.append((prev_end, n))
    cuts = [(a + b) // 2 for a, b in gap_list if b - a >= pause]
    cuts = [c for c in cuts if lo <= c <= hi]
    if cuts:
        return max(cuts)
    return hi if n >= hi else None


# ── Memoria y prioridad ─────────────────────────────────────────────────────

def _mem_status() -> tuple[float, float] | None:
    """(RAM disponible, reserva disponible) en MB, o None si no se puede saber."""
    if os.name != 'nt':
        return None
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return st.ullAvailPhys / 2 ** 20, st.ullAvailPageFile / 2 ** 20
    except Exception:
        return None


def _memory_ok(name: str) -> bool:
    """Hay memoria para cargar este modelo: reserva y RAM, cada una con margen."""
    st = _mem_status()
    if st is None:
        return True
    import transcriber
    ram, commit = st
    need_commit = _MODEL_COMMIT_MB.get(name, 5000) + COMMIT_MARGIN_MB
    need_ram = transcriber._MODEL_RAM_MB.get(name, 2600) + RAM_MARGIN_MB
    return commit >= need_commit and ram >= need_ram


def _commit_free_and_need(name: str) -> tuple[int | None, int]:
    """(reserva libre, reserva que hace falta) en MB, para avisar de la espera."""
    st = _mem_status()
    need = _MODEL_COMMIT_MB.get(name, 5000) + COMMIT_MARGIN_MB
    return (int(st[1]) if st else None), need


def _memory_floor_ok() -> bool:
    """Con el modelo ya cargado: que quede un minimo de reserva para el resto."""
    st = _mem_status()
    return st is None or st[1] >= COMMIT_FLOOR_MB


# La reserva de memoria no basta: con el pagefile gestionado por Windows, el tope
# de reserva sube cuando el pagefile crece en el disco. El 26/10/2026 a las 15:56
# 'medium' cargo porque el pagefile acababa de crecer, y a las 16:06 el disco
# estaba lleno y la grabacion de la reunion siguiente perdio 11 min. Grabar va
# antes que transcribir durante la reunion: con poco disco el modelo no se carga
# (o se suelta) y la reunion se transcribe al colgar.
DISK_FLOOR_GB = 2.0


def _disk_free_and_need(name: str) -> tuple[float | None, float]:
    """(GB libres en el disco del sistema, GB que pide la reserva del modelo).
    Nunca menos que el suelo con margen: con 'base' o 'tiny' (1,4-1,6 GB) se
    cargaria y se soltaria en cada vuelta entre 1,6 y 2 GB libres."""
    import disk_guard
    need = max((_MODEL_COMMIT_MB.get(name, 5000) + COMMIT_MARGIN_MB) / 1024, DISK_FLOOR_GB + 0.5)
    return disk_guard.system_free_gb(), need


def _disk_ok(name: str) -> bool:
    """Hay disco para que el pagefile absorba la reserva del modelo si crece."""
    free, need = _disk_free_and_need(name)
    return free is None or free >= need


def _disk_floor_ok() -> bool:
    import disk_guard
    free = disk_guard.system_free_gb()
    return free is None or free >= DISK_FLOOR_GB


def _work_priority(mode: str) -> str:
    """La prioridad para transcribir. En vivo, normal: medido el 20/10/2026 en
    una reunion real, con 'below' Windows lo trataba como proceso en segundo
    plano y le daba 0,7 nucleos para 4 hilos ('small' a 1-3 veces tiempo real,
    4 min de retraso); con 'normal', 2,2 nucleos y 0,3-0,5 veces tiempo real.
    De fondo, la minima: ahi si debe ceder ante todo (Teams incluido)."""
    return 'idle' if mode == MODE_BACKGROUND else 'normal'


def _opt_out_of_efficiency_mode() -> bool:
    """Fuera del modo de eficiencia de Windows (ver win_power)."""
    import win_power
    return win_power.opt_out_of_efficiency_mode()


def _set_priority(level: str) -> None:
    """'normal' para cargar el modelo; luego la de _work_priority. La prioridad
    se baja DESPUES de cargar: medido el 18/10/2026, un proceso lanzado ya con
    prioridad baja tardaba ~48 s en arrancar (frente a 2 s) y mas de 60 s en
    cargar el modelo."""
    if os.name != 'nt':
        return
    try:
        import psutil
        cls = {'normal': psutil.NORMAL_PRIORITY_CLASS, 'idle': psutil.IDLE_PRIORITY_CLASS,
               'below': psutil.BELOW_NORMAL_PRIORITY_CLASS}[level]
        psutil.Process().nice(cls)
    except Exception:
        pass


# ── El proceso en vivo ───────────────────────────────────────────────────────

class _Track:
    def __init__(self, name: str, part: Path, start: int):
        import numpy as np
        self.name, self.part = name, part
        self.pos = start              # siguiente frame a leer del .part
        self.buf_start = start        # frame donde empieza el bufer sin comprometer
        self.buf = np.zeros(0, dtype=np.float32)
        self.peak = 0.0
        self.vad_n = 0                # tamaño del bufer en la ultima pasada del VAD
        self.total = 0
        self.lang = None              # el idioma de esta pista, una vez mirado
        self.lang_at = None           # frame del ultimo trozo en que se miro

    def jump_to(self, frame: int) -> None:
        import numpy as np
        if frame > self.buf_start:
            self.pos = self.buf_start = frame
            self.buf = np.zeros(0, dtype=np.float32)
            self.vad_n = 0


def _transcribe_args() -> dict:
    import transcriber
    return {k: v for k, v in transcriber._TRANSCRIBE_ARGS.items()
            if k not in ('language', 'language_detection_segments')}


def _daemon_alive(pid: int, create_time: float) -> bool:
    try:
        import psutil
        return abs(psutil.Process(pid).create_time() - create_time) < 1.0
    except Exception:
        return False


def _start_watchdog(pid: int, create_time: float) -> None:
    """Si el daemon muere, este proceso se va en segundos, aunque este en mitad
    de un trozo largo: un modelo huerfano cargado competiria con el daemon que
    se relanza y recupera reuniones con el suyo."""
    def watch():
        while True:
            time.sleep(2)
            if not _daemon_alive(pid, create_time):
                log.info("live: el daemon ya no existe; salgo")
                os._exit(0)
    threading.Thread(target=watch, daemon=True, name='LiveWatchdog').start()


def _setup_logging() -> None:
    from logging.handlers import RotatingFileHandler

    from config import PROJECT_DIR
    handler = RotatingFileHandler(PROJECT_DIR / 'live.log', maxBytes=1_000_000,
                                  backupCount=1, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _step(track: _Track, model, lang: str | None, args: dict, vad, vad_opts, wav: Path,
          model_name: str = LIVE_MODEL, min_s: float = None, max_s: float = None, abort=None):
    """Lee lo nuevo de una pista y, si toca, compromete un trozo.

    abort(): si devuelve True entre segmentos, el trozo se abandona sin
    comprometer (el daemon pidio el modelo o cambio el modo) y se rehara.
    Devuelve (si comprometio un trozo, idioma detectado o None).
    """
    import numpy as np
    max_s = MAX_CHUNK_S if max_s is None else max_s
    min_s = MIN_CHUNK_S if min_s is None else min_s
    t_read = time.perf_counter()
    room = int((max_s + 5) * SR) - len(track.buf)
    audio, total = read_part(track.part, track.pos, max(0, room))
    t_read = time.perf_counter() - t_read
    track.total = total
    if len(audio):
        track.buf = np.concatenate([track.buf, audio])
        track.pos += len(audio)
        track.peak = max(track.peak, float(np.max(np.abs(audio))))

    n = len(track.buf)
    if n < min_s * SR:
        return False, None
    # El VAD solo cuando hay al menos un segundo nuevo desde la ultima pasada.
    if n - track.vad_n < SR and n < max_s * SR:
        return False, None
    # Pico acumulado de la pista, no del trozo: subir el volumen de un trozo casi
    # en silencio hace alucinar a Whisper. El VAD tambien ve el audio normalizado,
    # como en el post-reunion (que lo pasa por pistas ya normalizadas): sin eso,
    # una pista muy baja daria "sin voz", el trozo se comprometeria vacio y esa
    # voz no la recuperaria nadie.
    gain = min(0.8 / track.peak, 50.0) if track.peak > 0 else 1.0
    t_vad = time.perf_counter()
    speech = vad(np.ascontiguousarray(track.buf * gain), vad_opts)
    t_vad = time.perf_counter() - t_vad
    track.vad_n = n
    cut = choose_cut(n, speech, min_s, max_s)
    if cut is None:
        return False, None

    chunk = track.buf[:cut]
    voiced = sum(min(s['end'], cut) - s['start'] for s in speech if s['start'] < cut) / SR
    segments, detected, chunk_lang = [], None, track.lang or lang
    t_asr = time.perf_counter()
    if voiced > 0:
        import transcriber
        audio = np.ascontiguousarray(chunk * gain)
        # Cada pista vuelve a mirar su idioma: el de la reunion se fijo una vez, con
        # los primeros minutos, y puede no ser el de esta pista ni el de ahora.
        if chunk_lang and (track.lang_at is None
                           or track.buf_start - track.lang_at >= LANG_RECHECK_S * SR):
            track.lang_at = track.buf_start
            new = transcriber.recheck_language(model, audio, chunk_lang, voiced)
            if new != chunk_lang:
                log.info(f"live: {track.name} pasa de '{chunk_lang}' a '{new}' en {track.buf_start / SR:.0f}s")
            track.lang = chunk_lang = new
        asked = chunk_lang
        segs, info = model.transcribe(audio, language=asked, **args)
        offset = track.buf_start / SR
        done = []
        for s in segs:
            if abort is not None and abort():
                log.info(f"live: {track.name} trozo abandonado a mitad (el daemon lo pidio)")
                return False, None
            text = s.text.strip()
            if text:
                done.append((s.start, s.end, text))
        if asked is None:
            chunk_lang, chunk_prob = transcriber.allowed_choice(
                getattr(info, 'language', None) or 'es', float(getattr(info, 'language_probability', 0) or 0),
                getattr(info, 'all_language_probs', None))
            # Solo se fija con confianza alta: fijarlo con los primeros segundos de
            # charla impondria un idioma dudoso a toda la reunion.
            if chunk_prob >= LANG_LOCK_PROB:
                detected = chunk_lang
        done = transcriber._redo_echoes(model, done, transcriber._Audio(audio, SR),
                                        dict(args, language=chunk_lang))
        segments = [[round(offset + s, 2), text] for s, _e, text in done]
    t_asr = time.perf_counter() - t_asr
    _append_record(wav, {
        'track': track.name, 'start_frame': track.buf_start,
        'end_frame': track.buf_start + cut, 'segments': segments,
        'lang': chunk_lang if segments else (track.lang or lang), 'model': model_name,
        'voiced': round(voiced, 1),
    })
    if voiced >= SUSPECT_VOICED_S and len(segments) <= voiced / SUSPECT_VOICED_S:
        # 18/11/2026: un trozo de la pista de Teams con 150 s de voz y 5 lineas (las
        # pistas del glosario a Whisper, ya quitadas). Solo se mide: no cambia que
        # se acepte ni que se rehaga.
        log.warning(f"live: trozo sospechoso: mucha voz y casi sin texto | {track.name} "
                    f"{track.buf_start / SR:.1f}-{(track.buf_start + cut) / SR:.1f}s "
                    f"voz {voiced:.1f}s lineas {len(segments)}")
    # Una linea por trozo: es lo que permite saber, en una reunion real, si va al
    # dia o se esta quedando atras, y por que.
    behind = (track.total - (track.buf_start + cut)) / SR
    log.info(f"live: {track.name} {track.buf_start / SR:7.1f}-{(track.buf_start + cut) / SR:7.1f}s "
             f"[{model_name}] voz {voiced:4.1f}s lineas {len(segments):2d} | leer {t_read * 1000:4.0f}ms "
             f"vad {t_vad:4.1f}s whisper {t_asr:5.1f}s | atrasado {behind:5.0f}s")
    track.buf = track.buf[cut:]
    track.buf_start += cut
    track.vad_n = 0
    return True, detected


def _voiced(audio, vad, vad_opts) -> float:
    import numpy as np
    if not len(audio):
        return 0.0
    peak = float(np.max(np.abs(audio)))
    gain = min(0.8 / peak, 50.0) if peak > 0 else 1.0
    return sum(s['end'] - s['start'] for s in vad(np.ascontiguousarray(audio * gain), vad_opts)) / SR


def _detect_language(model, wav: Path, vad, vad_opts,
                     start_s: float = 0.0) -> tuple[str | None, float]:
    """(idioma, confianza) para el modo de fondo, como lo decide el post-reunion:
    el del tramo mas seguro de los dos (micro y Teams) entre los que tienen voz
    suficiente, mirando desde start_s. Un tramo vale con LANG_MIN_VOICE_S de voz, o,
    pasados LANG_FALLBACK_AFTER_S de audio, con algo de voz (quien graba no habla).
    (None, 0.0) mientras ninguno tenga voz bastante.

    18/11/2026: se miraba solo el micro, con 37 s de voz, y Whisper dijo 'uk' con
    0,66: el remapeo lo convirtio en 'en', se fijo sin mirar la confianza y el
    castellano del micro salio traducido al ingles durante 7 minutos. La pista de
    Teams, con 141 s de voz, daba 'es' con mas de 0,9."""
    import numpy as np

    import transcriber
    window, start = 150 * SR, int(start_s * SR)
    best_lang, best_prob = None, 0.0
    for name in TRACKS:
        audio, total = read_part(part_path(wav, name), start, window)
        voice = _voiced(audio, vad, vad_opts)
        if not (voice >= LANG_MIN_VOICE_S
                or (voice >= 5 and total >= start + LANG_FALLBACK_AFTER_S * SR)):
            continue
        peak = float(np.max(np.abs(audio)))
        gain = min(0.8 / peak, 50.0) if peak > 0 else 1.0
        lang, prob, _ = model.detect_language(np.ascontiguousarray(audio * gain), vad_filter=True,
                                              language_detection_segments=5)
        log.info(f"live: idioma detectado '{lang}' ({prob:.2f}) en {name}")
        lang, prob = transcriber.allowed_choice(lang, float(prob), _)
        if prob > best_prob:
            best_lang, best_prob = lang, float(prob)
    return best_lang, best_prob


def _settle_language(found: str | None, prob: float, start_s: float) -> tuple[str | None, float]:
    """(idioma a fijar o None, desde donde mirar la proxima vez). Solo se fija con
    confianza alta (LANG_LOCK_PROB), como en el post-reunion y en _step: fijarlo con
    una deteccion dudosa impone un idioma equivocado a toda la reunion."""
    if found is None:
        return None, start_s
    if prob >= LANG_LOCK_PROB:
        return found, start_s
    log.info(f"live: idioma '{found}' sin confianza ({prob:.2f}, hacen falta {LANG_LOCK_PROB}): "
             f"no se fija; vuelvo a mirar con el audio de mas adelante")
    return None, start_s + LANG_RETRY_STEP_S


def _run(wav: Path, daemon_pid: int, daemon_ct: float, from_now: bool = False) -> None:
    import gc

    import transcriber            # antes que faster_whisper: fija MKL_DISABLE_FAST_MM
    from faster_whisper import WhisperModel
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    # Arrancar puede tardar (ver kill): si al llegar aqui la grabacion ya paro,
    # no hay nada que hacer y no merece la pena cargar el modelo.
    if wav.exists():
        log.info(f"live: {wav.name} ya termino antes de arrancar; salgo")
        return
    _start_watchdog(daemon_pid, daemon_ct)
    if not _opt_out_of_efficiency_mode():
        log.info("live: no se pudo sacar el proceso del modo de eficiencia de Windows")

    # Si se relanzara, sigue despues de lo ultimo que hizo: lo hecho no se repite.
    records = read_records(wav)
    tracks = [_Track(t, part_path(wav, t), resume_frame(records, t)) for t in TRACKS]
    lang = transcriber.WHISPER_LANGUAGE or read_status(wav).get('lang') or None
    args = _transcribe_args()
    vad_opts = VadOptions(threshold=0.3, min_silence_duration_ms=500, speech_pad_ms=0)

    mode = model = model_name = None
    detect_from = 0.0                  # desde donde mirar el idioma (ver _settle_language)
    waiting_logged = -WAITING_LOG_EVERY_S        # el primer aviso de memoria sale ya
    last_ctl, ctl = 0.0, read_ctl(wav)

    def current_ctl():
        nonlocal last_ctl, ctl
        if time.monotonic() - last_ctl >= 1.0:
            ctl, last_ctl = read_ctl(wav), time.monotonic()
        return ctl

    def abort():
        c = current_ctl()
        return c['mode'] != mode or (mode == MODE_BACKGROUND and c['gate'] == 'yield')

    mic = tracks[0]
    started = time.monotonic()
    last_growth, last_mic_total, seen_mic = started, -1, False
    hints_done = False
    while True:
        if wav.exists():
            # El grabador ya cerro los .part: no se vuelven a tocar.
            log.info("live: la grabacion paro; salgo")
            break
        # Las salidas y el minimo de reserva van aqui, antes de las ramas que
        # esperan (ceder, memoria, idioma): esas hacen 'continue' y se los saltaban.
        _, mic.total = read_part(mic.part, mic.pos, 0)
        if mic.total:
            seen_mic = True
        elif not mic.part.exists():
            # Que no exista, no que una lectura falle: un bloqueo momentaneo (el
            # antivirus) no debe acabar con el en vivo del resto de la reunion.
            if seen_mic:
                log.info("live: el .part del micro desaparecio (grabacion descartada); salgo")
                break
            if time.monotonic() - started > 60:
                log.info("live: el .part del micro no aparecio en 60s (grabacion descartada); salgo")
                break
        if mic.total != last_mic_total:
            last_mic_total, last_growth = mic.total, time.monotonic()
        elif time.monotonic() - last_growth > STALL_EXIT_S:
            log.info(f"live: el micro lleva {STALL_EXIT_S}s sin crecer; salgo")
            break
        if model is not None and not _memory_floor_ok():
            log.warning("live: reserva de memoria por debajo del minimo; descargo el modelo y espero")
            model = None
            gc.collect()
        if model is not None and not _disk_floor_ok():
            log.warning(f"live: menos de {DISK_FLOOR_GB:g} GB libres en disco; descargo el modelo "
                        f"para que la grabacion siga (se transcribira al colgar)")
            model = None
            gc.collect()

        c = current_ctl()
        if c['mode'] != mode:
            # Al pasar a en vivo se empieza en "ahora": ponerse al dia con minutos
            # de atraso no seria "ya". El hueco lo rellena el cierre. Al arrancar,
            # solo si el daemon lo pidio (en vivo a mitad de reunion): un "Grabar
            # + en vivo" en una maquina lenta no debe saltarse el principio.
            if c['mode'] == MODE_LIVE and (mode is not None or from_now):
                for t in tracks:
                    _, total = read_part(t.part, t.pos, 0)
                    t.jump_to(max(0, total - LIVE_JUMP_BACK_S * SR))
            log.info(f"live: modo {mode} -> {c['mode']}")
            mode = c['mode']
            if model is not None and model_name != _model_for(mode):
                model = None
                gc.collect()
        if mode == MODE_BACKGROUND and c['gate'] == 'yield':
            if model is not None:
                model = None
                gc.collect()
                log.info("live: el pipeline necesita Whisper; modelo descargado")
            _write_status(wav, state='yielded', yielded_id=c['yield_id'])
            time.sleep(2)
            continue
        if model is None:
            name = _model_for(mode)
            if not _memory_ok(name):
                # Antes esperaba en silencio: el 23/10 paso una llamada entera
                # sin transcribir nada y sin que nadie lo supiera.
                free_mb, need_mb = _commit_free_and_need(name)
                if time.monotonic() - waiting_logged >= WAITING_LOG_EVERY_S:
                    log.info(f"live: sin memoria para cargar '{name}': reserva libre {free_mb} MB, "
                             f"hacen falta {need_mb} MB; espero (se transcribira al colgar)")
                    waiting_logged = time.monotonic()
                _write_status(wav, state='waiting_memory', free_mb=free_mb, need_mb=need_mb)
                time.sleep(5)
                continue
            if not _disk_ok(name):
                free_gb, need_gb = _disk_free_and_need(name)
                if time.monotonic() - waiting_logged >= WAITING_LOG_EVERY_S:
                    log.info(f"live: sin disco para cargar '{name}': {free_gb:.1f} GB libres, "
                             f"hacen falta {need_gb:.1f} GB; espero (se transcribira al colgar)")
                    waiting_logged = time.monotonic()
                _write_status(wav, state='waiting_disk', free_gb=round(free_gb, 1),
                              need_gb=round(need_gb, 1))
                time.sleep(5)
                continue
            waiting_logged = -WAITING_LOG_EVERY_S
            # Antes de cargar, no despues: mientras carga (hasta un minuto), un
            # 'yielded' anterior diria que no tiene modelo.
            _write_status(wav, state='loading', mode=mode, model=name)
            _set_priority('normal')
            t0 = time.monotonic()
            model = WhisperModel(name, device='cpu', compute_type='int8',
                                 cpu_threads=PROFILES[mode]['threads'], local_files_only=True)
            model_name = name
            _set_priority(_work_priority(mode))
            log.info(f"live: modelo '{name}' cargado en {time.monotonic() - t0:.1f}s ({mode})")
            _write_status(wav, state='running', mode=mode, model=name)
        if mode == MODE_BACKGROUND and lang is None:
            found, prob = _detect_language(model, wav, get_speech_timestamps, vad_opts, detect_from)
            lang, detect_from = _settle_language(found, prob, detect_from)
            if lang is None:
                time.sleep(5)
                continue
            _write_status(wav, lang=lang)
            log.info(f"live: idioma fijado en '{lang}'")

        if not hints_done:
            hints_done = _load_hints(args, wav)
        progressed = False
        prof = PROFILES[mode]
        for track in tracks:
            done, detected = _step(track, model, lang, args, get_speech_timestamps, vad_opts, wav,
                                   model_name=model_name, min_s=prof['min_s'], max_s=prof['max_s'],
                                   abort=abort)
            progressed |= done
            if detected and lang is None:
                lang = detected
                _write_status(wav, lang=lang)
                log.info(f"live: idioma fijado en '{lang}'")
        if not progressed:
            time.sleep(POLL_S)


def _model_for(mode: str) -> str:
    return LIVE_MODEL if mode == MODE_LIVE else final_model()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    wav, daemon_pid, daemon_ct = Path(argv[0]), int(argv[1]), float(argv[2])
    from_now = FROM_NOW_ARG in argv[3:]
    try:
        import psutil
        me = psutil.Process()
        # Lo primero, antes de cargar nada pesado: el daemon lo necesita para
        # matarlo. Sin tocar 'lang': si ya se fijo, un relanzamiento lo conserva.
        _write_status(wav, pid=me.pid, create_time=me.create_time(), state='starting')
    except Exception:
        pass
    try:
        _setup_logging()
        _run(wav, daemon_pid, daemon_ct, from_now=from_now)
        _write_status(wav, state='exited')
        return 0
    except Exception:
        log.exception(f"live: fallo transcribiendo {wav.name} durante la reunion")
        _write_status(wav, state='failed')
        return 1


# ── Lo que usa el daemon ─────────────────────────────────────────────────────

def spawn(wav: Path, recorder=None, still_wanted=None, mode: str = MODE_BACKGROUND,
          gate: str = 'free', mid_meeting: bool = False):
    """Lanza el proceso para una grabacion que acaba de empezar.

    Bloquea entre START_DELAY_S y LOOPBACK_WAIT_S: llamar desde un hilo.
    mid_meeting (en vivo pedido a mitad de reunion, sin proceso de fondo): sin
    START_DELAY_S, que es para no lanzar procesos por grabaciones que se cancelan
    al empezar, y el proceso empieza en "ahora" en vez de por el principio.
    Devuelve el Popen, o None si no toca lanzarlo. Mismo patron que el worker
    WASAPI del grabador; nunca WMI. La memoria la vigila el propio proceso antes
    de cargar el modelo (y espera si no hay).
    """
    wanted = still_wanted or (lambda: True)
    if mode == MODE_BACKGROUND and not background_enabled():
        log.info("Transcripcion durante la reunion desactivada (background_transcription=false)")
        return None
    delay = 0 if mid_meeting else START_DELAY_S
    t0 = time.monotonic()
    while True:
        if not wanted():
            return None
        waited = time.monotonic() - t0
        loop_ready = recorder is None or getattr(recorder, 'loopback_active', False)
        if waited >= delay and (loop_ready or waited >= LOOPBACK_WAIT_S):
            break
        time.sleep(0.5)

    import psutil
    # El modo y el permiso de usar el modelo los mantiene el daemon en .ctl (el
    # usuario puede haber pedido en vivo mientras se esperaba): si ya existe, manda.
    if not ctl_path(wav).exists():
        write_ctl(wav, mode=mode, gate=gate)
    me = psutil.Process()
    cmd = [sys.executable, str(Path(__file__).resolve()), str(wav), str(me.pid), repr(me.create_time())]
    if mid_meeting:
        cmd.append(FROM_NOW_ARG)
    # Sin bajar la prioridad aqui: la baja el propio proceso al cargar el modelo.
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True,
                            cwd=str(Path(__file__).resolve().parent))
    log.info(f"Transcripcion durante la reunion lanzada (pid {proc.pid}, {read_ctl(wav)['mode']}) "
             f"para {wav.name}")
    return proc


def wait_yielded(wav: Path, proc=None, timeout: float = 60.0, yield_id: int | None = None,
                 should_stop=None) -> bool:
    """Espera a que el proceso haya soltado el modelo o haya muerto. True si se
    puede usar Whisper sin tenerlo cargado a la vez.

    Con yield_id, solo vale el 'yielded' que responde a ESA peticion: uno anterior
    puede ser de antes de que volviera a cargar el modelo. 'waiting_memory' no
    vale: puede ponerse a cargar en cualquier momento (y cede en segundos).
    should_stop(): dejar de esperar sin que haga falta (p. ej. paso a en vivo).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = read_status(wav)
        if st.get('state') in ('exited', 'failed'):
            return True
        if st.get('state') == 'yielded' and (yield_id is None or st.get('yielded_id') == yield_id):
            return True
        if proc is not None and proc.poll() is not None:
            return True
        if should_stop is not None and should_stop():
            return True
        time.sleep(0.5)
    return False


def child_usable(wav: Path, proc=None) -> bool:
    """El proceso sigue haciendo su trabajo (o esta arrancando). Uno que espera
    memoria no: el pipeline no debe pausarse esperandolo. 'yielded' si: en modo en
    vivo solo dura los segundos que tarda en enterarse del cambio (el .ctl se
    escribe entero y, si no se puede, no se cambia de modo), y contarlo como no
    usable dejaba al pipeline cargar 'medium' justo en esa ventana. Uno que espera
    disco tampoco: si el pipeline ya tiene 'medium' cargado, pausarlo toda la
    reunion no encoge el pagefile y nadie transcribiria. Que el pipeline ceda con
    poco disco mientras se graba es otra decision (pendiente, 26/10/2026)."""
    if proc is not None and proc.poll() is not None:
        return False
    return read_status(wav).get('state') not in ('failed', 'exited', 'waiting_memory', 'waiting_disk')


def kill(proc=None, wav: Path | None = None, timeout: float = 5.0) -> None:
    """Mata el proceso en vivo y espera a que muera. Idempotente; nunca lanza.

    El lanzador pythonw del venv arranca el interprete real como hijo, asi que
    se matan los dos: el arbol del Popen y el pid que el propio proceso dejo en
    .live.json, comprobando su hora de creacion para no matar a otro proceso
    que hubiera heredado el pid.

    Medido el 18/10/2026 con la maquina cargada: el lanzador crea el interprete
    real SUSPENDIDO y lo reanuda cuando Windows termina de revisarlo, hasta 25 s
    despues; mientras tanto, matarlo da AccessDenied. Por eso el orden: primero el
    interprete, y el lanzador solo cuando el interprete ya murio. Si se matara el
    lanzador antes, el interprete se quedaria congelado para siempre; vivo el
    lanzador, lo reanuda, y el interprete se va solo al ver la grabacion parada.
    Lo que sobreviva a `timeout` lo sigue intentando un hilo hasta REAP_S.
    """
    try:
        import psutil
    except Exception:
        psutil = None
    if psutil is None:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        return
    launcher, kids = None, {}
    if proc is not None:
        try:
            launcher = psutil.Process(proc.pid)
            for v in launcher.children(recursive=True):
                kids[v.pid] = v
        except psutil.Error:
            pass
    if wav is not None:
        st = read_status(wav)
        pid, ct = st.get('pid'), st.get('create_time')
        if isinstance(pid, int) and (launcher is None or pid != launcher.pid):
            try:
                p = psutil.Process(pid)
                if ct is None or abs(p.create_time() - float(ct)) < 1.0:
                    kids[p.pid] = p
            except psutil.Error:
                pass
    left = _kill_until(psutil, list(kids.values()), timeout)
    if left:
        log.warning(f"live: {[p.pid for p in left]} no murieron en {timeout:.0f}s; "
                    f"se sigue intentando en segundo plano")
        threading.Thread(target=_reap, args=(psutil, left, launcher), daemon=True,
                         name='LiveReaper').start()
        return
    if launcher is not None:
        _kill_until(psutil, [launcher], 1.0)
    if proc is not None:
        try:
            proc.wait(timeout=1)
        except Exception:
            pass


REAP_S = 180


def _kill_until(psutil, victims: list, timeout: float) -> list:
    """Intenta matar hasta que no quede ninguno o pase `timeout`; devuelve los vivos."""
    def alive(p):
        try:
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except psutil.Error:
            return True

    deadline = time.monotonic() + timeout
    while victims:
        for p in victims:
            try:
                p.kill()
            except psutil.Error:
                pass
        time.sleep(0.1)
        victims = [p for p in victims if alive(p)]
        if time.monotonic() > deadline:
            break
    return victims


def _reap(psutil, victims: list, launcher=None) -> None:
    """Sigue intentando matar a los rezagados; el lanzador, solo cuando ya murieron."""
    left = victims
    deadline = time.monotonic() + REAP_S
    while left and time.monotonic() < deadline:
        time.sleep(2)
        left = _kill_until(psutil, left, 1.0)
    if left:
        log.warning(f"live: no se pudo matar {[p.pid for p in left]} en {REAP_S}s")
        return
    if launcher is not None:
        _kill_until(psutil, [launcher], 1.0)
    log.info("live: procesos rezagados terminados")


# ── El cierre ────────────────────────────────────────────────────────────────

def _remainder_models(fast: bool) -> list[str]:
    """Modelos para lo que falta, del preferido al mas ligero que quepa."""
    if fast:
        return [LIVE_MODEL]
    import transcriber
    names = []
    for name, _chunked in transcriber._fallback_plan(final_model()):
        if name not in names:
            names.append(name)
    return names


def _block_end(f, pos: int, end: int, vad, vad_opts) -> int:
    """Fin de un bloque del cierre: cerca de FINISH_BLOCK_S, en una pausa."""
    import numpy as np
    target = pos + FINISH_BLOCK_S * SR
    if target >= end:
        return end
    lo = max(pos + SR, target - 20 * SR)
    f.seek(lo)
    window = f.read(target + 5 * SR - lo, dtype='float32', always_2d=False)
    if not len(window):
        return target
    peak = float(np.max(np.abs(window)))
    gain = min(0.8 / peak, 50.0) if peak > 0 else 1.0
    speech = vad(np.ascontiguousarray(window * gain), vad_opts)
    gap_list, prev = [], 0
    for s in speech:
        gap_list.append((prev, s['start']))
        prev = s['end']
    gap_list.append((prev, len(window)))
    gap_list = [(a, b) for a, b in gap_list if b - a >= int(MIN_PAUSE_S * SR)]
    if not gap_list:
        return target
    a, b = max(gap_list, key=lambda g: g[1] - g[0])
    return min(end, lo + (a + b) // 2)


def finish_live(wav: Path, on_progress=None, should_cancel=None,
                should_pause=None, hotwords: str | None = None) -> tuple[str, str] | None:
    """Ver _finish_live. Un cierre aplazado (TranscriptionDeferred: se graba otra
    reunion con poco disco o memoria) no cuenta como intento: con
    FINISH_MAX_ATTEMPTS se rendiria y el pipeline lo transcribiria todo de nuevo.
    Lo hecho hasta la pausa ya esta en el .live.jsonl."""
    import transcriber
    marker = finishing_path(wav)
    try:
        before = marker.read_text() if marker.exists() else None
    except OSError:
        before = None
    try:
        return _finish_live(wav, on_progress=on_progress, should_cancel=should_cancel,
                            should_pause=should_pause, hotwords=hotwords)
    except transcriber.TranscriptionDeferred:
        try:
            if before is None:
                marker.unlink(missing_ok=True)
            else:
                marker.write_text(before)
        except OSError:
            pass
        raise


def _finish_live(wav: Path, on_progress=None, should_cancel=None,
                 should_pause=None, hotwords: str | None = None) -> tuple[str, str] | None:
    """Cierra el transcript de una grabacion ya parada con lo hecho durante ella.

    Aprovecha los trozos hechos con un modelo aceptado (el de siempre; tambien
    'small' si "live_transcript_final") y transcribe solo los huecos y el final,
    con el primer modelo de la escalera que quepa. Cada bloque nuevo se guarda
    como registro: si el daemon cae a mitad, el siguiente intento no lo repite.

    Devuelve (texto, idioma), o None si no hay nada aprovechable o no procede, y
    entonces el pipeline transcribe como siempre. Lanza TranscriptionCancelled si
    la reunion se descarta. No borra nada: eso lo hace discard_artifacts.
    """
    if not jsonl_path(wav).exists():
        return None
    marker = finishing_path(wav)
    try:
        attempts = int(marker.read_text().strip() or 0) if marker.exists() else 0
    except (OSError, ValueError):
        attempts = FINISH_MAX_ATTEMPTS
    if attempts >= FINISH_MAX_ATTEMPTS:
        log.warning(f"live: {attempts} cierres de {wav.name} no terminaron; se transcribe como siempre")
        return None
    kill(wav=wav)
    records = read_records(wav)
    if not records:
        return None
    mic_wav, loop_wav = track_wav(wav, 'mic'), track_wav(wav, 'loop')
    if not mic_wav.exists():
        log.warning(f"live: falta {mic_wav.name}; se transcribe como siempre")
        return None
    # Sin .loop.wav el grabador descarto el loopback (silencio o demasiado corto)
    # y, como hoy, el transcript va sin etiquetas. Pero si durante la reunion el
    # loopback SI tenia voz, lo mas probable es que fallara escribir .loop.wav:
    # renderizar solo el micro perderia a los demas, y el camino de siempre
    # transcribe la mezcla, que si los tiene.
    use_loop = loop_wav.exists()
    if not use_loop and any(r['segments'] for r in records if r['track'] == 'loop'):
        log.warning(f"live: el loopback tenia voz pero no hay {loop_wav.name}; "
                    f"se transcribe como siempre")
        return None

    fast = final_enabled()
    accepted_models = {final_model()} | ({LIVE_MODEL} if fast else set())

    def accept(r):
        return record_model(r) in accepted_models

    import numpy as np
    import soundfile as sf

    import transcriber
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    tracks = [('mic', mic_wav)] + ([('loop', loop_wav)] if use_loop else [])
    kept, todo, covered = {}, [], 0
    for name, path in tracks:
        frames = sf.info(str(path)).frames
        kept[name] = accepted_records(records, name, accept)
        if any(r['end_frame'] > frames for r in kept[name]):
            log.warning(f"live: {name} tiene trozos mas alla del final de {path.name}; "
                        f"se transcribe como siempre")
            return None
        covered += sum(r['end_frame'] - r['start_frame'] for r in kept[name])
        todo += [(name, path, a, b) for a, b in gaps(kept[name], frames)]
    if covered == 0:
        log.info(f"live: nada aprovechable con {sorted(accepted_models)} en {wav.name}; "
                 f"se transcribe como siempre")
        return None

    marker.write_text(str(attempts + 1))
    # El fijado durante la reunion; si no llego a fijarse, el de la mayoria.
    langs = Counter(r.get('lang') for name in kept for r in kept[name] if r.get('lang') and r['segments'])
    locked = read_status(wav).get('lang')
    lang = locked or (langs.most_common(1)[0][0] if langs else None)
    rest = sum(b - a for _n, _p, a, b in todo)
    by_track = {name: _segments(kept[name]) for name, _path in tracks}
    log.info(f"live: cerrando {wav.name}; aprovechado {covered / SR:.0f}s, "
             f"queda por transcribir {rest / SR:.0f}s")
    if rest > 0:
        model = model_name = None
        # La pausa antes del plan: el plan mira la RAM libre, y con el 'small' del
        # en vivo cargado se saltaria 'medium' sin avisar.
        transcriber._wait_if_paused(should_pause, should_cancel)
        for name in _remainder_models(fast):
            try:
                transcriber._wait_if_paused(should_pause, should_cancel)
                model, model_name = transcriber._get_model(name), name
                break
            except (transcriber.TranscriptionCancelled, transcriber.TranscriptionDeferred):
                raise
            except Exception as e:
                if not transcriber._is_memory_error(e):
                    raise
                log.warning(f"live: sin memoria para '{name}' en el cierre: {e}")
                transcriber._release_model()
        if model is None:
            raise RuntimeError("cierre sin modelo que quepa en memoria")
        args = _transcribe_args()
        if hotwords:
            args['hotwords'] = hotwords
        vad_opts = VadOptions(threshold=0.3, min_silence_duration_ms=500, speech_pad_ms=0)
        def lang_before(name, frame):
            """El idioma de la pista justo antes del hueco: cada pista lleva el suyo.
            Solo si se fijo durante la reunion; si no, cada trozo lo adivino solo."""
            prev = [r for r in kept[name] if r['end_frame'] <= frame and r['segments'] and r.get('lang')]
            return prev[-1]['lang'] if locked and prev else None

        try:
            done = 0
            for name, path, a, b in todo:
                cur = lang_before(name, a) or lang
                with sf.SoundFile(str(path)) as f:
                    pos = a
                    while pos < b:
                        transcriber._wait_if_paused(should_pause, should_cancel)
                        end = _block_end(f, pos, b, get_speech_timestamps, vad_opts)
                        f.seek(pos)
                        block = f.read(end - pos, dtype='float32', always_2d=False)
                        if not len(block):
                            break
                        block = np.ascontiguousarray(block)
                        cur = transcriber.recheck_language(model, block, cur)
                        segs, info = model.transcribe(block, language=cur, **args)
                        if cur is None:
                            cur = transcriber._remap_info(info)
                        lang = lang or cur
                        got = []
                        for s in transcriber._paced(segs, should_pause, should_cancel):
                            text = s.text.strip()
                            if text:
                                got.append((s.start, s.end, text))
                        got = transcriber._redo_echoes(model, got, transcriber._Audio(block, SR),
                                                       dict(args, language=cur))
                        offset = pos / SR
                        new = [[round(offset + s, 2), text] for s, _e, text in got]
                        _append_record(wav, {'track': name, 'start_frame': pos, 'end_frame': end,
                                             'segments': new, 'lang': cur, 'model': model_name})
                        by_track[name] += [(t, txt) for t, txt in new]
                        done += end - pos
                        pos = end
                        if on_progress:
                            on_progress(min(int(done / rest * 100), 100))
        finally:
            transcriber._release_model()
    marker.unlink(missing_ok=True)
    return render(by_track, labelled=use_loop), (lang or 'es')


def discard_artifacts(wav: Path, tracks: bool = True, parts: bool = False) -> None:
    """Borra lo que dejo la transcripcion durante la reunion.

    - Con el transcript ya en disco: todo, pistas incluidas, como hace hoy
      transcriber._transcribe_with_speakers con las pistas.
    - Si se vuelve al camino de siempre (tracks=False): solo los ficheros en vivo;
      las pistas las necesita la transcripcion con etiquetas.
    - Al cancelar una grabacion (parts=True): tambien los .part, por si un
      borrado del grabador coincidio con una lectura. La reunion se descarto, y
      ni su audio ni su texto deben quedarse en disco.
    """
    paths = [jsonl_path(wav), status_path(wav), ctl_path(wav), finishing_path(wav), hints_path(wav)]
    if tracks:
        paths += [track_wav(wav, 'mic'), track_wav(wav, 'loop')]
    if parts:
        paths += [part_path(wav, 'mic'), part_path(wav, 'loop')]
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError as e:
            log.warning(f"live: no se pudo borrar {p.name}: {e}")


if __name__ == '__main__':
    sys.exit(main())
