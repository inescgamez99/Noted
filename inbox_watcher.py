import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from config import INBOX_DIR, RECORDINGS_DIR

log = logging.getLogger(__name__)

AUDIO_EXTS = {'.wav', '.mp3', '.m4a', '.aac', '.ogg', '.flac', '.opus', '.wma'}
VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.m4v', '.wmv', '.ts'}
ALL_EXTS   = AUDIO_EXTS | VIDEO_EXTS


# Un nombre que ya empieza por YYYY-MM-DD_HH-MM_ lleva la fecha de la reunion (lo pone
# AppAPI.import_audio): no se le antepone la hora de ahora. Los ficheros que alguien
# suelta a mano en inbox/ siguen recibiendo la hora de ahora.
_DATED_NAME = re.compile(r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_')

# Donde queda el original despues de convertirlo. NO en recordings/: un .wav alli lo
# tomaria _recover_pending por una grabacion y lo procesaria otra vez.
ORIGINALS_DIRNAME = 'originals'

# <stem>.imported junto a la grabacion: el audio entro por inbox/ (importado desde el
# panel o soltado a mano), no lo grabo Noted. Ninguna cita de Outlook es la suya: el
# pipeline no busca invitados ni asunto para el (la hora del nombre es una etiqueta).
IMPORTED_SUFFIX = '.imported'


def _convert_timeout(f: Path) -> int:
    """Segundos que se le dan a ffmpeg: 5 min como minimo, y mas en ficheros grandes
    (un video de 1-2 h de Teams)."""
    try:
        return max(300, int(f.stat().st_size / 1_000_000 * 1.5))
    except OSError:
        return 300


def _unique_stem(stem: str) -> str:
    """Si ya existe una grabacion con este stem, anade __2, __3...: convertir encima
    de ella la sobrescribiria (y si falla, borrariamos lo de otra reunion)."""
    from storage import _COLLISION_SEP, _recording_stem_taken
    base, n = stem, 2
    while _recording_stem_taken(stem):
        stem = f"{base}{_COLLISION_SEP}{n}"
        n += 1
    return stem


def _find_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which('ffmpeg')


class InboxWatcher:
    def __init__(self, on_wav_ready=None):
        self._on_wav_ready = on_wav_ready
        self._seen: set[Path] = set()
        self._ffmpeg = _find_ffmpeg()
        if not self._ffmpeg:
            log.warning("ffmpeg no encontrado — solo se procesarán .wav en inbox/")

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name='InboxWatcher').start()

    def _loop(self):
        while True:
            try:
                for f in list(INBOX_DIR.iterdir()):
                    if f.suffix.lower() not in ALL_EXTS:
                        continue
                    if f in self._seen or f.suffix == '.error':
                        continue
                    self._seen.add(f)
                    threading.Thread(
                        target=self._process_stable, args=(f,), daemon=True,
                        name=f'InboxProcess-{f.name}',
                    ).start()
            except Exception as e:
                log.warning(f"InboxWatcher error: {e}")
            time.sleep(5)

    def _is_stable(self, f: Path) -> bool:
        try:
            s1 = f.stat().st_size
            time.sleep(3)
            s2 = f.stat().st_size
            return s1 == s2 and s1 > 0
        except Exception:
            return False

    def _process_stable(self, f: Path):
        """Espera a que el fichero esté estable antes de procesar (en hilo propio)."""
        if not self._is_stable(f):
            self._seen.discard(f)
            return
        self._process(f)

    def _process(self, f: Path):
        from datetime import datetime
        if _DATED_NAME.match(f.name):
            dest_name = f.name
        else:
            dest_name = f"{datetime.now().strftime('%Y-%m-%d_%H-%M')}_{f.name}"
        created = None      # el WAV que ESTE proceso esta creando, por si hay que descartarlo

        try:
            if f.suffix.lower() == '.wav' and not self._ffmpeg:
                wav_path = RECORDINGS_DIR / dest_name
                shutil.move(str(f), str(wav_path))
            else:
                if not self._ffmpeg:
                    log.warning(f"ffmpeg no disponible, no se puede convertir {f.name}")
                    return
                stem = _unique_stem(dest_name.rsplit('.', 1)[0])
                wav_path = RECORDINGS_DIR / f"{stem}.wav"
                originals = RECORDINGS_DIR / ORIGINALS_DIRNAME
                originals.mkdir(parents=True, exist_ok=True)
                orig_dest = originals / f"{stem}{Path(dest_name).suffix}"
                created = wav_path
                result = subprocess.run(
                    [self._ffmpeg, '-y', '-i', str(f),
                     '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
                     str(wav_path)],
                    capture_output=True, timeout=_convert_timeout(f),
                )
                if result.returncode != 0:
                    # Sin dejar un WAV a medias: _recover_pending lo transcribiria al
                    # arrancar como si fuera una grabacion buena.
                    self._discard_partial(created)
                    created = None
                    f.rename(f.parent / (f.name + '.error'))
                    log.error(f"ffmpeg falló convirtiendo {f.name}")
                    return
                created = None
                shutil.move(str(f), str(orig_dest))
                # La retencion cuenta desde que se archiva, no desde la fecha del
                # fichero (un MP3 de hace un mes se borraria en el siguiente arranque).
                try:
                    os.utime(orig_dest, None)
                except OSError:
                    pass

            self._mark_external(wav_path)
            log.info(f"Inbox: {f.name} → {wav_path.name}")
            if self._on_wav_ready:
                self._on_wav_ready(wav_path)
        except Exception as e:
            log.error(f"Error procesando {f.name}: {e}")
            if created is not None:
                self._discard_partial(created)
            try:
                f.rename(f.parent / (f.name + '.error'))
            except Exception:
                pass

    @staticmethod
    def _mark_external(wav_path: Path) -> None:
        """Antes de avisar al pipeline: si no, podria procesarlo sin la marca."""
        try:
            wav_path.with_suffix(IMPORTED_SUFFIX).write_text('inbox', encoding='utf-8')
        except OSError as e:
            log.warning(f"No se pudo marcar {wav_path.name} como audio externo: {e}")

    @staticmethod
    def _discard_partial(wav_path: Path) -> None:
        """Borra el WAV que ffmpeg dejo a medias. Solo se llama con un WAV creado por
        este proceso, cuyo nombre _unique_stem garantizo libre antes de empezar."""
        try:
            wav_path.unlink(missing_ok=True)
        except OSError as e:
            log.warning(f"No se pudo borrar el WAV parcial {wav_path.name}: {e}")
