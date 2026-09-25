import gc
import logging
import os
import re
import threading
from pathlib import Path

from config import WHISPER_MODEL, WHISPER_LANGUAGE, OPENAI_API_KEY, ME_NAME

log = logging.getLogger(__name__)

_model = None
_model_name: str | None = None
_model_lock = threading.Lock()

_LANG_REMAP = {
    # Galician/Basque — speakers write in Spanish
    'gl': 'es', 'eu': 'es',
    # Celtic / Gaelic
    'cy': 'en', 'ga': 'en', 'gd': 'en',
    # Romance false-positives (pt/fr/de intentionally NOT here — real meeting languages)
    'it': 'en', 'la': 'en', 'ro': 'en',
    # Southeast Asian — common false-positive when audio starts with silence
    'ms': 'en', 'id': 'en',
    # East Asian — very common false-positives with background noise or short audio
    'zh': 'en', 'ja': 'en', 'ko': 'en',
    # Other false-positives
    'nl': 'en', 'tr': 'en', 'ar': 'en', 'hi': 'en', 'ru': 'en',
    'pl': 'en', 'cs': 'en', 'uk': 'en',
    # Nordic
    'sv': 'en', 'da': 'en', 'no': 'en', 'fi': 'en',
}

# Modelos de mayor a menor. Si no hay memoria para el configurado se va bajando:
# una transcripción con un modelo pequeño es infinitamente mejor que ninguna.
_MODEL_LADDER = ['large-v3', 'large-v2', 'large', 'medium', 'small', 'base', 'tiny']

# Segundos por trozo al transcribir troceado. faster-whisper calcula el
# espectrograma del audio completo de una vez (~290 MB en complex128 para 16
# minutos); troceando, el pico baja en proporción.
_CHUNK_SECS = 300

# Memoria aproximada que necesita cada modelo en int8, contando el trabajo de
# inferencia y no solo los pesos. Sirve para descartar intentos condenados: el
# 17/09, con 0,4 GB libres, la escalera intentó 'medium' dos veces y el proceso
# murió con 0xC0000409 al cargar el siguiente modelo, perdiendo una hora de
# transcripción que iba por el 55%.
_MODEL_RAM_MB = {
    'tiny': 200, 'base': 350, 'small': 900, 'medium': 2600,
    'large': 5200, 'large-v2': 5200, 'large-v3': 5200,
}


class TranscriptionCancelled(Exception):
    """El usuario descartó la reunión mientras se transcribía.

    No es un fallo: no debe reintentarse con otro modelo ni caer a la API de
    OpenAI. Se propaga para que el pipeline limpie y pare.
    """


def _check_cancel(should_cancel) -> None:
    if should_cancel and should_cancel():
        raise TranscriptionCancelled()


def _is_memory_error(exc: Exception) -> bool:
    if isinstance(exc, MemoryError):
        return True
    msg = str(exc).lower()
    return any(s in msg for s in (
        'mkl_malloc', 'failed to allocate', 'unable to allocate',
        'bad_alloc', 'out of memory', 'cannot allocate',
    ))


def _available_mb() -> float | None:
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 * 1024)
    except Exception:
        return None


def _fallback_plan(start: str) -> list[tuple[str, bool]]:
    """Intentos (modelo, troceado) del más capaz al más ligero."""
    try:
        models = _MODEL_LADDER[_MODEL_LADDER.index(start):]
    except ValueError:
        models = [start, 'small', 'base', 'tiny']

    # Descartar de entrada los modelos que no caben. Intentarlos no solo pierde
    # minutos: cargar un modelo con la memoria agotada es lo que tira el proceso
    # entero. Nunca se descartan todos — el más pequeño siempre se intenta,
    # porque una transcripción con 'tiny' es mejor que ninguna.
    avail = _available_mb()
    if avail is not None:
        fits = [m for m in models if _MODEL_RAM_MB.get(m, 0) <= avail * 0.8] or models[-1:]
        if len(fits) < len(models):
            skipped = ', '.join(m for m in models if m not in fits)
            log.warning(f"Solo {avail:.0f} MB disponibles: se empieza por '{fits[0]}' "
                        f"en vez de '{models[0]}' (descartados: {skipped})")
        models = fits

    plan = []
    for name in models:
        plan.append((name, False))
        plan.append((name, True))
    return plan


def partial_resume(path: Path | None) -> tuple[float, list[str]]:
    """Punto de reanudación a partir del .partial de un intento que murió.

    Devuelve (segundo desde el que seguir, líneas que se conservan). El corte se
    alinea al inicio del trozo que contenía la última línea escrita: dentro de un
    trozo las líneas se emiten a medida que salen, así que las del trozo
    interrumpido pueden estar incompletas y se rehacen. Alineando al trozo no
    hay duplicados ni huecos.
    """
    if not path or not path.exists():
        return 0.0, []
    try:
        raw = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return 0.0, []

    stamped = []
    for line in raw:
        m = re.match(r'\[(\d+):(\d{2})\]', line)
        if m:
            stamped.append((int(m.group(1)) * 60 + int(m.group(2)), line))
    if not stamped:
        return 0.0, []

    resume_at = (stamped[-1][0] // _CHUNK_SECS) * _CHUNK_SECS
    if resume_at <= 0:
        return 0.0, []
    return float(resume_at), [line for secs, line in stamped if secs < resume_at]


def _release_model():
    """Descarga el modelo actual. Imprescindible antes de probar uno más
    pequeño: si el grande sigue en memoria, el pequeño tampoco entra."""
    global _model, _model_name
    with _model_lock:
        _model = None
        _model_name = None
    gc.collect()


def _get_model(name: str | None = None):
    global _model, _model_name
    name = name or WHISPER_MODEL
    with _model_lock:
        if _model is None or _model_name != name:
            _model = None
            _model_name = None
            gc.collect()
            from faster_whisper import WhisperModel
            try:
                import psutil
                threads = psutil.cpu_count(logical=False) or os.cpu_count() or 4
            except Exception:
                threads = os.cpu_count() or 4
            log.info(f"Cargando modelo Whisper '{name}' (cpu_threads={threads})...")
            _model = WhisperModel(
                name, device='auto', compute_type='int8', cpu_threads=threads,
            )
            _model_name = name
            log.info("Modelo Whisper cargado")
        return _model


def _format_time(secs: float) -> str:
    m = int(secs // 60)
    s = int(secs % 60)
    return f"[{m:02d}:{s:02d}]"


def _remap(detected: str | None) -> str:
    detected = detected or 'es'
    if detected in _LANG_REMAP:
        remapped = _LANG_REMAP[detected]
        log.info(f"Idioma detectado '{detected}' → remap a '{remapped}'")
        detected = remapped
    return detected


_TRANSCRIBE_ARGS = dict(
    language=WHISPER_LANGUAGE or None,
    beam_size=1,
    condition_on_previous_text=False,
    vad_filter=True,
    vad_parameters=dict(min_silence_duration_ms=500, threshold=0.3),
    language_detection_segments=5,
)


def _transcribe_whole(model, audio_path: Path, on_progress=None, on_segment=None,
                      should_cancel=None):
    _check_cancel(should_cancel)
    segments, info = model.transcribe(str(audio_path), **_TRANSCRIBE_ARGS)
    detected = _remap(getattr(info, 'language', None))
    log.info(f"Idioma de la reunión: {detected}")

    lines = []
    duration = getattr(info, 'duration', None)
    for seg in segments:
        # faster-whisper devuelve un generador perezoso: salir del bucle corta
        # el trabajo de verdad, no solo descarta el resultado.
        _check_cancel(should_cancel)
        line = f"{_format_time(seg.start)} {seg.text.strip()}"
        lines.append(line)
        if on_segment:
            on_segment(line)
        if on_progress and duration:
            on_progress(min(int(seg.end / duration * 100), 100))
    return '\n'.join(lines), detected


def _transcribe_chunks(model, audio_path: Path, on_progress=None, on_segment=None,
                       should_cancel=None, resume_at: float = 0.0, prior_lines=None):
    """Igual que _transcribe_whole pero por trozos, para acotar la memoria."""
    import numpy as np
    import soundfile as sf

    info = sf.info(str(audio_path))
    sr, total = info.samplerate, info.frames
    step = _CHUNK_SECS * sr
    duration = total / sr if sr else 0
    log.info(f"Transcribiendo por trozos de {_CHUNK_SECS}s ({duration/60:.1f} min en total)")

    lines: list[str] = list(prior_lines or [])
    detected = None
    with sf.SoundFile(str(audio_path)) as f:
        pos = min(int(resume_at * sr), total)
        if pos:
            log.info(f"Reanudando en {resume_at/60:.1f} min: se conservan "
                     f"{len(lines)} líneas del intento anterior")
        while pos < total:
            _check_cancel(should_cancel)
            f.seek(pos)
            block = f.read(min(step, total - pos), dtype='float32', always_2d=False)
            if not len(block):
                break
            segments, cinfo = model.transcribe(np.ascontiguousarray(block), **_TRANSCRIBE_ARGS)
            if detected is None:
                detected = _remap(getattr(cinfo, 'language', None))
                log.info(f"Idioma de la reunión: {detected}")
            offset = pos / sr
            for seg in segments:
                _check_cancel(should_cancel)
                line = f"{_format_time(offset + seg.start)} {seg.text.strip()}"
                lines.append(line)
                if on_segment:
                    on_segment(line)
                # Progreso por segmento y no solo por trozo: con trozos de 300s
                # la barra se quedaba clavada varios minutos entre saltos de
                # 13,6% y parecía colgada aunque estuviera transcribiendo.
                if on_progress and duration:
                    on_progress(min(int((offset + seg.end) / duration * 100), 100))
            pos += len(block)
            del block
            gc.collect()
            if on_progress and duration:
                on_progress(min(int(pos / sr / duration * 100), 100))
    return '\n'.join(lines), (detected or 'es')


def _transcribe_with_speakers(model, audio_path: Path) -> tuple[str, str]:
    """Two-pass speaker-tagged transcription using .mic.wav / .loop.wav companion files.

    Mic track  → segments labelled [Tú]
    Loop track → segments labelled [Otros]
    Both lists are merged by start timestamp so the final transcript reads
    chronologically even though the two passes run sequentially.
    """
    mic_path = audio_path.with_suffix('.mic.wav')
    loop_path = audio_path.with_suffix('.loop.wav')

    log.info("Diarizando: transcribiendo track de micrófono...")
    mic_segs_raw, mic_info = model.transcribe(str(mic_path), **_TRANSCRIBE_ARGS)
    detected = _remap(getattr(mic_info, 'language', None))
    log.info(f"Idioma detectado (diarización): {detected}")

    me_label = f'[{ME_NAME}]' if ME_NAME else '[Grabador]'
    others_label = '[Otros]'
    mic_segments: list[tuple[float, str, str]] = []
    for seg in mic_segs_raw:
        t = seg.text.strip()
        if t:
            mic_segments.append((seg.start, me_label, t))

    log.info("Diarizando: transcribiendo track de Teams (otros)...")
    loop_args = dict(_TRANSCRIBE_ARGS)
    loop_args['language'] = detected
    loop_args.pop('language_detection_segments', None)
    loop_segs_raw, _ = model.transcribe(str(loop_path), **loop_args)
    loop_segments: list[tuple[float, str, str]] = []
    for seg in loop_segs_raw:
        t = seg.text.strip()
        if t:
            loop_segments.append((seg.start, others_label, t))

    all_segments = sorted(mic_segments + loop_segments, key=lambda x: x[0])
    lines = [f"{_format_time(ts)} {speaker}: {text}" for ts, speaker, text in all_segments]

    # Clean up speaker files — no longer needed once transcript is generated
    mic_path.unlink(missing_ok=True)
    loop_path.unlink(missing_ok=True)

    return '\n'.join(lines), detected


def _transcribe_local(audio_path: Path, on_progress=None, on_segment=None,
                      should_cancel=None, resume_from: Path | None = None) -> tuple[str, str]:
    """Recorre el plan de fallback hasta que uno funcione.

    Antes, cualquier fallo saltaba directamente a la API de OpenAI y, sin
    OPENAI_API_KEY, la reunión se quedaba sin transcripción. Los fallos por
    memoria son recuperables: basta trocear el audio o usar un modelo menor.
    """
    mic_path = audio_path.with_suffix('.mic.wav')
    loop_path = audio_path.with_suffix('.loop.wav')
    use_speakers = mic_path.exists() and loop_path.exists()

    plan = _fallback_plan(WHISPER_MODEL)
    resume_at, prior_lines = partial_resume(resume_from)
    if resume_at:
        # Un .partial a medias es la prueba de que el intento anterior murió a
        # mitad: ir directo al troceado, que es el único que sabe arrancar por
        # el medio y el que acota la memoria que tumbó al anterior.
        plan = [(name, True) for name, chunked in plan if chunked]
    last_exc: Exception | None = None

    for name, chunked in plan:
        if last_exc is not None:
            log.warning(
                f"Reintentando con modelo '{name}'{' troceado' if chunked else ''} "
                f"(el intento anterior se quedó sin memoria)"
            )
        try:
            _check_cancel(should_cancel)
            model = _get_model(name)
            if use_speakers and not chunked:
                return _transcribe_with_speakers(model, audio_path)
            if use_speakers and chunked:
                mic_path.unlink(missing_ok=True)
                loop_path.unlink(missing_ok=True)
                use_speakers = False
            if chunked:
                return _transcribe_chunks(
                    model, audio_path, on_progress=on_progress, on_segment=on_segment,
                    should_cancel=should_cancel, resume_at=resume_at,
                    prior_lines=prior_lines)
            return _transcribe_whole(model, audio_path, on_progress=on_progress,
                                     on_segment=on_segment, should_cancel=should_cancel)
        except TranscriptionCancelled:
            raise
        except Exception as e:
            last_exc = e
            if not _is_memory_error(e):
                raise
            log.warning(f"Sin memoria con '{name}'{' troceado' if chunked else ''}: {e}")
            _release_model()

    raise last_exc if last_exc else RuntimeError("transcripción local sin intentos")


def _transcribe_openai(audio_path: Path) -> tuple[str, str]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY no configurada")
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    with open(audio_path, 'rb') as f:
        result = client.audio.transcriptions.create(
            model='whisper-1',
            file=f,
            response_format='verbose_json',
            timestamp_granularities=['segment'],
        )
    lines = []
    for seg in result.segments:
        lines.append(f"{_format_time(seg.start)} {seg.text.strip()}")
    lang = getattr(result, 'language', 'es') or 'es'
    return '\n'.join(lines), lang


def transcribe(
    audio_path: Path,
    on_complete=None,
    on_progress=None,
    on_segment=None,
    should_cancel=None,
    resume_from: Path | None = None,
) -> tuple[str, str] | None:
    """
    Devuelve (transcript_text, detected_language) o None si falla.
    Si on_complete es callable, ejecuta en thread daemon — on_complete(text, lang).
    should_cancel: callable sin argumentos; si devuelve True se aborta y se
    propaga TranscriptionCancelled (el llamador limpia).
    resume_from: .partial de un intento que murió a medias; se sigue desde ahí
    en vez de volver a empezar (ver partial_resume).
    """
    def _run():
        try:
            log.info(f"Transcribiendo {audio_path.name}...")
            text, lang = _transcribe_local(audio_path, on_progress=on_progress,
                                           on_segment=on_segment, should_cancel=should_cancel,
                                           resume_from=resume_from)
        except TranscriptionCancelled:
            # Descartada a proposito: no reintentar ni llamar a la API de pago.
            log.info(f"Transcripción cancelada: {audio_path.name}")
            raise
        except Exception as e:
            log.warning(f"faster-whisper falló ({e}), intentando OpenAI API...")
            try:
                text, lang = _transcribe_openai(audio_path)
            except Exception as e2:
                log.error(f"Transcripción fallida: {e2}")
                if on_complete:
                    on_complete(None, 'es')
                return None
        if on_complete:
            on_complete(text, lang)
        return text, lang

    if on_complete:
        t = threading.Thread(target=_run, daemon=True, name='Transcriber')
        t.start()
        return None
    return _run()
