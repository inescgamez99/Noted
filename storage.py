import glob
import re
from datetime import datetime, timedelta
from pathlib import Path

from config import RECORDINGS_DIR, MINUTES_DIR, INBOX_DIR, MINUTES_RETENTION_DAYS


def ensure_directories():
    for d in (RECORDINGS_DIR, MINUTES_DIR, INBOX_DIR, RECORDINGS_DIR / 'processed'):
        d.mkdir(parents=True, exist_ok=True)


def _slugify(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    text = re.sub(r'[\s\-]+', '_', text)
    return text.strip('_')[:60]


def _recording_timestamp(path: Path) -> str:
    """Extract YYYYMMDD_HHMM from stem YYYY-MM-DD_HH-MM_..."""
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})', path.stem)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}_{m.group(4)}{m.group(5)}"
    return ''


# Sufijo para dos grabaciones con el mismo nombre en el mismo minuto. Es el mismo
# convenio que ya usa la papelera (stem__2).
_COLLISION_SEP = '__'


def _recording_stem_taken(stem: str) -> bool:
    """True si ya existe CUALQUIER fichero de una grabacion con este stem.

    No basta con mirar el .wav final: mientras se graba solo existen los
    temporales (.mic.part, .loop.part); tras procesar, el WAV se va a processed/;
    y el transcript (_transcript.txt) cuelga del mismo stem. Pisar cualquiera
    de ellos mezcla o pierde datos de otra grabacion.
    """
    pat = glob.escape(stem)
    for folder in (RECORDINGS_DIR, RECORDINGS_DIR / 'processed'):
        if not folder.is_dir():
            continue
        if next(folder.glob(f'{pat}.*'), None) or next(folder.glob(f'{pat}_transcript.*'), None):
            return True
    return False


def get_recording_path(name: str = 'recording') -> Path:
    """Ruta para una grabacion nueva, que no pise ninguna anterior.

    El nombre solo llega al minuto. El 2026-10-08 dos pruebas de "Grabar ahora"
    a las 14:05 recibieron el mismo nombre y la segunda sobrescribio la primera:
    se perdieron 14 s de audio. Ahora, si el nombre ya esta en uso, se anade
    __2, __3... El formato YYYY-MM-DD_HH-MM_<nombre> no cambia, porque varias
    partes de la app lo leen (sesiones, recuperacion al arrancar, minutas).
    """
    ts = datetime.now().strftime('%Y-%m-%d_%H-%M')
    base = f"{ts}_{_slugify(name)}"
    stem, n = base, 2
    while _recording_stem_taken(stem):
        stem = f"{base}{_COLLISION_SEP}{n}"
        n += 1
    return RECORDINGS_DIR / f"{stem}.wav"


def get_transcript_path(recording_path: Path) -> Path:
    return recording_path.parent / f"{recording_path.stem}_transcript.txt"


def get_minutes_path(recording_path: Path, title: str = None) -> Path:
    ts = _recording_timestamp(recording_path)
    if not ts:
        ts = datetime.now().strftime('%Y%m%d_%H%M')
    slug = _slugify(title) if title else 'Reunion'
    return MINUTES_DIR / f"{ts}_{slug}.md"


def get_latest_minutes() -> Path | None:
    files = sorted(MINUTES_DIR.glob('*.md'), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def list_recordings(limit: int = 10) -> list[Path]:
    files = sorted(
        list(RECORDINGS_DIR.glob('*.wav')) + list((RECORDINGS_DIR / 'processed').glob('*.wav')),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[:limit]


# La cabecera de un WAV sin muestras: un .part asi no tiene audio que recuperar.
_EMPTY_PART_BYTES = 1024


def _has_minutes(audio: Path) -> bool:
    """Si la reunion de este audio ya tiene minutas: el mismo criterio con el
    que tray_app._recover_pending decide si reencolarla al arrancar."""
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})', audio.name)
    if not m:
        return False
    return any(MINUTES_DIR.glob(f'{"".join(m.group(1, 2, 3))}_{"".join(m.group(4, 5))}_*.md'))


# Lo que aun no se ha procesado se guarda mas tiempo, pero no para siempre:
# una grabacion sin voz o con la transcripcion rota tambien se queda sin
# minutas, y _recover_pending la reencolaria (Whisper, o la API de OpenAI si
# hay OPENAI_API_KEY) en cada arranque.
PENDING_RETENTION_FACTOR = 3


def cleanup_old_recordings(days: int = 10):
    cutoff = datetime.now() - timedelta(days=days)
    pending_cutoff = datetime.now() - timedelta(days=days * PENDING_RETENTION_FACTOR)
    audio_patterns = ('*.wav', '*.mp3')
    # *.part: temporales que un corte del daemon dejo huerfanos (nadie los
    # convierte en WAV). *.live.*: restos de la transcripcion en vivo
    # (.live.jsonl, .live.json, su .tmp y .live.finishing) cuando no se usaron.
    # *.live: el formato de la transcripcion en vivo anterior, que esta sustituye.
    # *.title: el nombre de Teams de una grabacion manual (tray_app._note_teams_title).
    # *_screenshot_*.png y *.screenshots.jsonl: capturas que no llegaron a minutes/
    # (screen_capture), con la misma regla que su grabacion.
    aux_patterns   = ('*.lang', '*.partial', '*.context', '*.part', '*.live.*', '*.live', '*.title',
                      '*_screenshot_*.png', '*.screenshots.jsonl')
    for d in (RECORDINGS_DIR, RECORDINGS_DIR / 'processed'):
        for pattern in audio_patterns + aux_patterns:
            for f in d.glob(pattern):
                # Un fichero puede desaparecer entre el glob y el stat (el .tmp
                # del estado en vivo se renombra al momento). Esto corre al
                # arrancar el daemon: una excepcion aqui lo tumbaria.
                try:
                    st = f.stat()
                    mtime = datetime.fromtimestamp(st.st_mtime)
                    if mtime >= cutoff:
                        continue
                    # Un .part con audio en recordings/ es una reunion que el
                    # daemon no llego a guardar: recover_orphan_parts la
                    # convierte en WAV al arrancar, despues de esta limpieza.
                    # Borrarlo por antiguo la perdia (un portatil apagado mas de
                    # 10 dias tras quedarse sin bateria). Los vacios (solo la
                    # cabecera WAV) no son una reunion y se borran.
                    if d == RECORDINGS_DIR and pattern == '*.part' and st.st_size > _EMPTY_PART_BYTES:
                        continue
                    # Lo que esta en recordings/ sin minutas es una reunion sin
                    # procesar (fallo, o el portatil estuvo apagado): se reencola
                    # al arrancar, justo despues de esta limpieza. Borrar su audio
                    # por antiguo seria perder la reunion, y su .context (lo que
                    # escribio el usuario) o su .partial, rehacerla peor. Hasta el
                    # tope de PENDING_RETENTION_FACTOR. Lo de processed/ ya la tiene.
                    # Un .part vacio no es una reunion pendiente: ya se decidio
                    # arriba (se borra), aunque este dentro del tope.
                    if (d == RECORDINGS_DIR and pattern != '*.part'
                            and mtime >= pending_cutoff and not _has_minutes(f)):
                        continue
                    f.unlink()
                except OSError:
                    pass


def cleanup_old_minutes():
    cutoff = datetime.now() - timedelta(days=MINUTES_RETENTION_DAYS)
    for pattern in ('*.md', '*.html', '*_actions.json', '*_transcript.txt',
                    '*_screenshot_*.png', '*_screenshots.json'):
        for f in MINUTES_DIR.glob(pattern):
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                try:
                    f.unlink()
                except OSError:
                    pass
