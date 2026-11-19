import gc
import logging
import os
import re
import threading
import time
from pathlib import Path

import config
from config import WHISPER_MODEL, WHISPER_LANGUAGE, OPENAI_API_KEY, ME_NAME

# CTranslate2 trae Intel oneMKL dentro, y su gestor de memoria guarda los buferes
# liberados en vez de devolverlos. Medido el 14/10/2026: con el modelo soltado tras
# cada reunion (ver transcribe()) quedaban ~90 MB mas por transcripcion; con esto,
# ~5 MB, y el texto sale identico. Es el control que documenta Intel y MKL lo lee
# al cargarse, asi que tiene que fijarse antes de importar faster_whisper, que aqui
# solo ocurre dentro de _get_model(). setdefault: un valor puesto a mano se respeta.
os.environ.setdefault('MKL_DISABLE_FAST_MM', '1')

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


class TranscriptionDeferred(Exception):
    """Aplazada para que la grabacion en curso tenga el disco y la memoria.

    26/10/2026, decision de Felipe: grabar va antes que todo. Mientras se graba
    otra reunion con poco disco o poca memoria, should_pause() devuelve DEFER y la
    transcripcion se aparta: los finally sueltan el modelo y lo hecho queda en el
    .partial o en el .live.jsonl. No es un fallo: no se reintenta con otro modelo
    ni se cae a la API de OpenAI; el pipeline espera y la repite desde lo hecho.
    """


DEFER = 'defer'


def _check_cancel(should_cancel) -> None:
    if should_cancel and should_cancel():
        raise TranscriptionCancelled()


def _wait_if_paused(should_pause, should_cancel, poll: float = 1.0) -> None:
    """Espera mientras should_pause() sea cierto, sin dejar de atender la
    cancelacion. Si devuelve DEFER, lanza TranscriptionDeferred: esperar con el
    modelo cargado no libera nada."""
    while should_pause:
        state = should_pause()
        if state == DEFER:
            raise TranscriptionDeferred()
        if not state:
            break
        _check_cancel(should_cancel)
        time.sleep(poll)
    _check_cancel(should_cancel)


def _paced(segments, should_pause=None, should_cancel=None):
    """Recorre los segmentos de faster-whisper atendiendo pausa y cancelacion.

    faster-whisper decodifica de forma perezosa, ventana a ventana, a medida que
    se le piden segmentos: esperar aqui detiene el trabajo de verdad (salvo la
    ventana que ya estaba en curso). Lo usa la transcripcion en vivo para que,
    cuando el usuario la pide "ya", la de una reunion anterior le deje la CPU
    (ver live_transcriber.py). Nunca se suspenden hilos nativos: podria
    bloquear las reservas de memoria del grabador, que vive en este proceso.
    """
    for seg in segments:
        _wait_if_paused(should_pause, should_cancel)
        yield seg


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


# Mas hilos no es mas rapido. Medido el 16/11/2026 en un Core Ultra 7 165U (12 nucleos fisicos: 2 de
# rendimiento, 8 eficientes y 2 de bajo consumo), 'medium' sobre los mismos 90 s de voz: 4 hilos 80,6 s,
# 6 hilos 62,0 s, 8 hilos 55,6 s y 12 hilos 70,0 s, con el modo de energia "Maximo rendimiento"; en
# "Equilibrado", 12 hilos 98,8 s, mas que el propio audio. Con un hilo por nucleo fisico el trabajo cae
# tambien en los nucleos lentos, y cada paso espera al mas lento. settings.json "whisper_threads" manda.
MAX_THREADS = 8


def _threads() -> int:
    from config import whisper_threads
    fixed = whisper_threads()
    if fixed:
        return fixed
    try:
        import psutil
        physical = psutil.cpu_count(logical=False) or os.cpu_count() or 4
    except Exception:
        physical = os.cpu_count() or 4
    return min(physical, MAX_THREADS)


def _get_model(name: str | None = None):
    global _model, _model_name
    name = name or WHISPER_MODEL
    with _model_lock:
        if _model is None or _model_name != name:
            _model = None
            _model_name = None
            gc.collect()
            # Sin esto, Windows frena al daemon (sin ventana) al transcribir:
            # 2,4 de 14 nucleos para 12 hilos (medido el 20/10/2026). Ver win_power.
            import win_power
            if not win_power.opt_out_of_efficiency_mode():
                log.info("No se pudo sacar el proceso del modo de eficiencia de Windows")
            from faster_whisper import WhisperModel
            threads = _threads()
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


def _probs(all_probs) -> dict:
    """all_language_probs de faster-whisper ([(idioma, prob), ...] o dict) como dict."""
    try:
        return {str(k): float(v) for k, v in (all_probs.items() if isinstance(all_probs, dict) else all_probs)}
    except (TypeError, ValueError):
        return {}


def allowed_choice(detected: str | None, prob: float, all_probs=None) -> tuple[str, float]:
    """(idioma, confianza) restringido a config.meeting_languages(). Un idioma permitido
    se queda como lo dijo Whisper. Si no (un 'jw' o un 'uk' falsos) gana el permitido
    mas probable segun all_probs, con SU confianza: casi siempre baja, asi que no se
    fija y se vuelve a mirar mas adelante. Sin all_probs, la tabla de siempre."""
    allowed = config.meeting_languages()
    if detected in allowed:
        return detected, prob
    probs = _probs(all_probs)
    best = max(((probs[l], l) for l in allowed if l in probs), default=None)
    if best:
        log.info(f"Idioma detectado '{detected}' no es de los habituales {list(allowed)}: "
                 f"se usa '{best[1]}' ({best[0]:.2f})")
        return best[1], best[0]
    remapped = _remap(detected)
    return (remapped if remapped in allowed else allowed[0]), prob


def _remap_info(info) -> str:
    """El idioma de un TranscriptionInfo, restringido a los idiomas de las reuniones."""
    lang = getattr(info, 'language', None)
    return allowed_choice(lang or 'es', float(getattr(info, 'language_probability', 0) or 0),
                          getattr(info, 'all_language_probs', None))[0]


_TRANSCRIBE_ARGS = dict(
    language=WHISPER_LANGUAGE or None,
    beam_size=1,
    condition_on_previous_text=False,
    vad_filter=True,
    vad_parameters=dict(min_silence_duration_ms=500, threshold=0.3),
    language_detection_segments=5,
)


# Con dos pistas, el micro decide el idioma solo si quien graba habla lo bastante.
# 29/10/2026: en una reunion en castellano en la que no hablo, faster-whisper
# adivino 'en' (51 %) sobre el micro en silencio, se le impuso a la pista de los
# demas y el transcript salio traducido al ingles. Entonces decide la pista de
# los demas, que se transcribe detectando su propio idioma.
_LANG_MIN_VOICE_S = 30.0


def _targs(hotwords: str | None = None, **overrides) -> dict:
    """Argumentos de transcribe(), con las pistas del glosario si las hay.

    Se usa `hotwords` y no `initial_prompt`: con condition_on_previous_text=False
    faster-whisper solo aplica initial_prompt a la primera ventana de 30 s,
    mientras que hotwords se reaplica en todas."""
    args = dict(_TRANSCRIBE_ARGS)
    args.update(overrides)
    if hotwords:
        args['hotwords'] = hotwords
    return args


# Pistas que se cuelan. Cuando Whisper duda con una ventana, a veces no la
# transcribe: devuelve el texto de las pistas. El 11/11/2026, en una reunion de
# 24 min, 18 lineas con los nombres de los invitados y el asunto de la cita
# taparon hasta 14,5 de los ~16,5 min con voz de la pista de los demas.
# Esos mismos tramos, transcritos sin pistas, salen enteros: se rehacen asi.
_ECHO_MIN_WORDS = 2
_ECHO_SHARE = 0.8


def _words(text: str) -> list[str]:
    import unicodedata
    folded = unicodedata.normalize('NFKD', text.lower())
    return re.findall(r'\w+', ''.join(c for c in folded if not unicodedata.combining(c)))


def is_hotword_echo(text: str, hotwords: str | None) -> bool:
    """Si la linea es Whisper repitiendo las pistas en vez de transcribir: casi
    todas sus palabras son de las pistas. Una frase que nombra a un invitado
    ("¿lo ves, Miguel?") tiene palabras propias. Un falso positivo solo cuesta
    rehacer ese tramo sin pistas."""
    if not hotwords:
        return False
    words = _words(text)
    if len(words) < _ECHO_MIN_WORDS:
        return False
    vocab = set(_words(hotwords))
    return sum(w in vocab for w in words) >= _ECHO_SHARE * len(words)


class _Audio:
    """Una pista, la mezcla o un trozo ya en memoria, a 16 kHz y en mono, que es lo
    que espera Whisper. De un fichero se lee solo el tramo que se pide: una
    reunion de 2 h son ~460 MB en float32."""

    def __init__(self, source, sr: int = 16000):
        self.source, self.sr = source, sr
        self._whole = None

    def _is_file(self) -> bool:
        return isinstance(self.source, (str, Path))

    @property
    def total(self) -> float:
        if self._is_file():
            import soundfile as sf
            return float(sf.info(str(self.source)).duration)
        return len(self.source) / self.sr

    def read(self, a: float, b: float):
        import numpy as np
        if not self._is_file():
            return np.ascontiguousarray(self.source[int(a * self.sr):int(b * self.sr)])
        import soundfile as sf
        rate = sf.info(str(self.source)).samplerate
        if rate != 16000:
            # Los WAV del grabador son de 16 kHz; otro se decodifica entero, una vez.
            if self._whole is None:
                from faster_whisper import decode_audio
                self._whole = decode_audio(str(self.source), sampling_rate=16000)
            return np.ascontiguousarray(self._whole[int(a * 16000):int(b * 16000)])
        audio, _ = sf.read(str(self.source), start=int(a * rate), stop=int(b * rate),
                           dtype='float32', always_2d=False)
        return np.ascontiguousarray(audio.mean(axis=1) if audio.ndim > 1 else audio)


def _redo_spans(model, segments: list, audio: _Audio, args: dict, bad, why: str,
                fix=None) -> list:
    """Rehace con args los segmentos que marca bad(segmento).

    segments: [(inicio, fin, texto)] en segundos de audio, que solo se lee si
    hay algo que rehacer. Cada tramo va del final del segmento bueno anterior al
    inicio del siguiente, y los marcados seguidos son un solo tramo: el texto
    malo ocupa el sitio de lo que se dijo, y sus marcas de tiempo no son fiables.
    fix(segmentos, audio del tramo), si se da, corrige lo rehecho antes de juntarlo.
    """
    if not any(bad(s) for s in segments):
        return segments
    out, i, redone = [], 0, 0.0
    while i < len(segments):
        if not bad(segments[i]):
            out.append(segments[i])
            i += 1
            continue
        j = i
        while j + 1 < len(segments) and bad(segments[j + 1]):
            j += 1
        a = min(segments[i][0], out[-1][1]) if out else segments[i][0]
        b = segments[j + 1][0] if j + 1 < len(segments) else audio.total
        window = audio.read(a, b) if b > a else ()
        if len(window):
            segs, _info = model.transcribe(window, **args)
            got = [(s.start, s.end, s.text.strip()) for s in segs if s.text.strip()]
            if fix is not None:
                got = fix(got, window)
            out += [(a + s, a + e, t) for s, e, t in got]
        redone += max(0.0, b - a)
        i = j + 1
    log.info(f"{why}: {redone:.0f}s rehechos")
    return out


def _redo_echoes(model, segments: list, audio: _Audio, args: dict) -> list:
    """Rehace sin pistas los tramos que salieron como eco de ellas."""
    hotwords = args.get('hotwords')
    if not hotwords:
        return segments
    plain = {k: v for k, v in args.items() if k != 'hotwords'}
    return _redo_spans(model, segments, audio, plain, lambda s: is_hotword_echo(s[2], hotwords),
                       "Pistas coladas en la transcripcion, sin ellas")


# Idioma por tramos. El 05/11/2026, en una sesion de una hora en ingles, los
# primeros 100 s fueron una charla en castellano: se fijo 'es' y todo lo dicho en
# ingles salio traducido al castellano. Ahora el idioma se mira por tramos de cada
# pista: el primero se fija con confianza alta, y despues un tramo solo lo cambia
# si Whisper esta muy seguro y hay voz de sobra, para que una frase suelta en otro
# idioma o un tramo con ruido no lo muevan. faster-whisper trae multilingual=True,
# que decide en cada ventana de 30 s sin umbral y sin _remap ('gl' saldria en gallego).
LANG_WINDOW_S = 300
LANG_LOCK_PROB = 0.8
LANG_SWITCH_PROB = 0.9
LANG_CHECK_MIN_VOICE_S = 20.0


def _speech_seconds(audio) -> float:
    """Segundos con voz, mirados sobre el audio normalizado: una pista muy baja no es silencio."""
    import numpy as np
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    if not len(audio):
        return 0.0
    peak = float(np.max(np.abs(audio)))
    gain = min(0.8 / peak, 50.0) if peak > 0 else 1.0
    opts = VadOptions(threshold=0.3, min_silence_duration_ms=500, speech_pad_ms=0)
    return sum(s['end'] - s['start']
               for s in get_speech_timestamps(np.ascontiguousarray(audio * gain), opts)) / 16000


def _can_detect(model) -> bool:
    return not WHISPER_LANGUAGE and callable(getattr(model, 'detect_language', None))


def _detect(model, audio) -> tuple[str, float] | None:
    """Idioma y confianza de un tramo (los primeros 30 s con voz), o None si no se puede."""
    try:
        lang, prob, _all = model.detect_language(audio, vad_filter=True, language_detection_segments=1)
    except Exception as e:
        log.warning(f"Idioma no detectado en un tramo: {e}")
        return None
    return allowed_choice(lang, float(prob), _all)


def next_language(current: str | None, found: tuple[str, float] | None) -> str | None:
    """El idioma tras mirar un tramo; found es (idioma, confianza) o None."""
    if found is None:
        return current
    lang, prob = found
    if current is None:
        return lang if prob >= LANG_LOCK_PROB else None
    return lang if lang != current and prob >= LANG_SWITCH_PROB else current


def recheck_language(model, audio, current: str | None, voiced_s: float | None = None) -> str | None:
    """El idioma para un trozo que se va a transcribir, para quien transcribe por
    trozos: el actual, salvo que el trozo diga otro con seguridad."""
    if not _can_detect(model):
        return current
    if voiced_s is None:
        voiced_s = _speech_seconds(audio)
    if voiced_s < LANG_CHECK_MIN_VOICE_S:
        return current
    new = next_language(current, _detect(model, audio))
    if current is not None and new != current:
        log.info(f"Idioma: el tramo es '{new}', no '{current}'; cambia desde aqui")
    return new


def _language_plan(model, audio: _Audio) -> list[tuple[float, float, str | None, float]]:
    """(inicio, fin, idioma, segundos de voz) de cada tramo de LANG_WINDOW_S, leyendo
    uno cada vez. Los tramos de antes del primer idioma seguro toman ese idioma."""
    if not _can_detect(model):
        return []
    plan, current, a, total = [], None, 0.0, audio.total
    while a < total:
        b = min(a + LANG_WINDOW_S, total)
        window = audio.read(a, b)
        voiced = _speech_seconds(window)
        if voiced >= LANG_CHECK_MIN_VOICE_S:
            current = next_language(current, _detect(model, window))
        plan.append((a, b, current, voiced))
        a = b
    first = next((lang for _a, _b, lang, _v in plan if lang), None)
    return [(a, b, lang or first, v) for a, b, lang, v in plan]


def _main_language(plan) -> str | None:
    """El idioma con mas voz en el plan, o None si no hay ninguno seguro."""
    from collections import Counter
    voice = Counter()
    for _a, _b, lang, v in plan:
        if lang:
            voice[lang] += v
    return voice.most_common(1)[0][0] if voice else None


def _apply_plan(model, segments: list, audio: _Audio, args: dict, plan, lang: str) -> list:
    """Rehace en su idioma los tramos del plan que no son de lang, el idioma con el
    que se transcribio la pista entera. Tramos seguidos del mismo idioma, de una vez."""
    runs = []
    for a, b, tramo, _v in plan:
        if not tramo or tramo == lang:
            continue
        if runs and runs[-1][2] == tramo and runs[-1][1] == a:
            runs[-1][1] = b
        else:
            runs.append([a, b, tramo])
    for a, b, tramo in runs:
        largs = dict(args, language=tramo)
        largs.pop('language_detection_segments', None)
        segments = _redo_spans(model, segments, audio, largs,
                               lambda s, a=a, b=b: a <= s[0] < b,
                               f"Idioma '{tramo}' entre {a:.0f}s y {b:.0f}s",
                               fix=lambda got, w, largs=largs: _redo_echoes(model, got, _Audio(w), largs))
    return segments


def _transcribe_whole(model, audio_path: Path, on_progress=None, on_segment=None,
                      should_cancel=None, should_pause=None, hotwords: str | None = None):
    _wait_if_paused(should_pause, should_cancel)
    audio = _Audio(audio_path)
    plan = _language_plan(model, audio)
    meeting = _main_language(plan)
    args = _targs(hotwords, language=meeting) if meeting else _targs(hotwords)
    segments, info = model.transcribe(str(audio_path), **args)
    detected = meeting or _remap_info(info)
    log.info(f"Idioma de la reunión: {detected}")

    done = []
    duration = getattr(info, 'duration', None)
    for seg in _paced(segments, should_pause, should_cancel):
        # faster-whisper devuelve un generador perezoso: salir del bucle corta
        # el trabajo de verdad, no solo descarta el resultado.
        _check_cancel(should_cancel)
        done.append((seg.start, seg.end, seg.text.strip()))
        # El .partial puede llevar un eco de las pistas: solo sirve para reanudar,
        # y el texto que se devuelve ya va sin el.
        if on_segment:
            on_segment(f"{_format_time(seg.start)} {seg.text.strip()}")
        if on_progress and duration:
            on_progress(min(int(seg.end / duration * 100), 100))
    done = _redo_echoes(model, done, audio, dict(args, language=detected))
    if meeting:
        done = _apply_plan(model, done, audio, args, plan, meeting)
    return '\n'.join(f"{_format_time(s)} {t}" for s, _e, t in done), detected


def _transcribe_chunks(model, audio_path: Path, on_progress=None, on_segment=None,
                       should_cancel=None, resume_at: float = 0.0, prior_lines=None,
                       should_pause=None, hotwords: str | None = None):
    """Igual que _transcribe_whole pero por trozos, para acotar la memoria."""
    import numpy as np
    import soundfile as sf

    info = sf.info(str(audio_path))
    sr, total = info.samplerate, info.frames
    step = _CHUNK_SECS * sr
    duration = total / sr if sr else 0
    log.info(f"Transcribiendo por trozos de {_CHUNK_SECS}s ({duration/60:.1f} min en total)")

    from collections import Counter
    lines: list[str] = list(prior_lines or [])
    detected, current, voice = None, None, Counter()
    with sf.SoundFile(str(audio_path)) as f:
        pos = min(int(resume_at * sr), total)
        if pos:
            log.info(f"Reanudando en {resume_at/60:.1f} min: se conservan "
                     f"{len(lines)} líneas del intento anterior")
        while pos < total:
            _wait_if_paused(should_pause, should_cancel)
            f.seek(pos)
            block = f.read(min(step, total - pos), dtype='float32', always_2d=False)
            if not len(block):
                break
            block = np.ascontiguousarray(block)
            # El idioma se vuelve a mirar en cada trozo (ver LANG_WINDOW_S).
            current = recheck_language(model, block, current)
            args = _targs(hotwords, language=current) if current else _targs(hotwords)
            segments, cinfo = model.transcribe(block, **args)
            block_lang = current or _remap_info(cinfo)
            voice[block_lang] += len(block)
            if detected is None:
                detected = block_lang
                log.info(f"Idioma de la reunión: {detected}")
            offset = pos / sr
            done = []
            for seg in _paced(segments, should_pause, should_cancel):
                _check_cancel(should_cancel)
                done.append((seg.start, seg.end, seg.text.strip()))
                # Progreso por segmento y no solo por trozo: con trozos de 300s
                # la barra se quedaba clavada varios minutos entre saltos de
                # 13,6% y parecía colgada aunque estuviera transcribiendo.
                if on_progress and duration:
                    on_progress(min(int((offset + seg.end) / duration * 100), 100))
            # Las lineas salen por trozo, ya sin ecos de las pistas: van al .partial,
            # y un eco ahi se conservaria al reanudar.
            done = _redo_echoes(model, done, _Audio(block, sr), dict(args, language=block_lang))
            for start, _end, text in done:
                line = f"{_format_time(offset + start)} {text}"
                lines.append(line)
                if on_segment:
                    on_segment(line)
            pos += len(block)
            del block
            gc.collect()
            if on_progress and duration:
                on_progress(min(int(pos / sr / duration * 100), 100))
    # El de la mayor parte de la reunion, que es en el que van las minutas.
    return '\n'.join(lines), (voice.most_common(1)[0][0] if voice else detected or 'es')


def _log_pace(what: str, audio_secs: float, started: float) -> None:
    """Cuanto tardo una pasada, contando sus pausas: es lo que dice si la transcripcion se queda
    atras de las reuniones (hilos, CPU, modo de energia)."""
    took = time.monotonic() - started
    pace = f" ({took / audio_secs:.2f} veces su duracion)" if audio_secs else ""
    log.info(f"{what}: {audio_secs / 60:.1f} min de audio en {took / 60:.1f} min{pace}")


def _track_seconds(path: Path) -> float:
    try:
        import soundfile as sf
        return float(sf.info(str(path)).duration)
    except Exception:
        return 0.0


def _transcribe_with_speakers(model, audio_path: Path, on_progress=None, should_cancel=None,
                              should_pause=None,
                              hotwords: str | None = None) -> tuple[str, str]:
    """Two-pass speaker-tagged transcription using .mic.wav / .loop.wav companion files.

    Mic track  → segments labelled [Tú]
    Loop track → segments labelled [Otros]
    Both lists are merged by start timestamp so the final transcript reads
    chronologically even though the two passes run sequentially.

    El progreso cuenta las dos pistas segun su duracion: sin el, la reunion de
    48 min del 19/10/2026 paso 84 min en "Transcribiendo 0%".
    """
    mic_path = audio_path.with_suffix('.mic.wav')
    loop_path = audio_path.with_suffix('.loop.wav')
    mic_secs, loop_secs = _track_seconds(mic_path), _track_seconds(loop_path)
    total_secs = mic_secs + loop_secs

    def progress(done_secs):
        if on_progress and total_secs:
            on_progress(min(int(done_secs / total_secs * 100), 100))

    def fixed(lang):
        args = _targs(hotwords, language=lang)
        args.pop('language_detection_segments', None)
        return args

    # El idioma de cada pista por tramos (ver LANG_WINDOW_S): cada una se
    # transcribe en el suyo y los tramos en otro idioma se rehacen en el suyo. Si
    # ninguna lo deja claro, como antes: decide el micro si quien graba habla.
    _wait_if_paused(should_pause, should_cancel)
    plans = {'mic': _language_plan(model, _Audio(mic_path)),
             'loop': _language_plan(model, _Audio(loop_path))}
    meeting = _main_language(plans['mic'] + plans['loop'])
    langs = {t: _main_language(p) or meeting for t, p in plans.items()} if meeting else {}
    if meeting:
        log.info(f"Idioma de la reunión: {meeting} (micro: {langs['mic']}, Teams: {langs['loop']})")

    _wait_if_paused(should_pause, should_cancel)
    log.info("Diarizando: transcribiendo track de micrófono...")
    started = time.monotonic()
    mic_args = fixed(langs['mic']) if meeting else _targs(hotwords)
    mic_segs_raw, mic_info = model.transcribe(str(mic_path), **mic_args)
    detected = meeting or _remap_info(mic_info)
    mic_voice = getattr(mic_info, 'duration_after_vad', None)
    mic_decides = mic_voice is None or mic_voice >= _LANG_MIN_VOICE_S
    if not meeting:
        log.info(f"Idioma detectado (diarización): {detected}"
                 + ('' if mic_decides else f" — el micro tiene {mic_voice:.0f}s de voz; decide la otra pista"))

    me_label = f'[{ME_NAME}]' if ME_NAME else '[Grabador]'
    others_label = '[Otros]'
    mic_done = []
    for seg in _paced(mic_segs_raw, should_pause, should_cancel):
        t = seg.text.strip()
        if t:
            mic_done.append((seg.start, seg.end, t))
        progress(seg.end)
    mic_lang = langs.get('mic') or detected
    mic_done = _redo_echoes(model, mic_done, _Audio(mic_path), dict(mic_args, language=mic_lang))
    if meeting:
        mic_done = _apply_plan(model, mic_done, _Audio(mic_path), mic_args, plans['mic'], mic_lang)
    mic_segments = [(s, me_label, t) for s, _e, t in mic_done]
    _log_pace("Track de micrófono", mic_secs, started)

    _wait_if_paused(should_pause, should_cancel)
    log.info("Diarizando: transcribiendo track de Teams (otros)...")
    started = time.monotonic()
    if meeting:
        loop_args = fixed(langs['loop'])
    elif mic_decides:
        loop_args = fixed(detected)
    else:
        loop_args = _targs(hotwords)
    loop_segs_raw, loop_info = model.transcribe(str(loop_path), **loop_args)
    if not meeting and not mic_decides and (getattr(loop_info, 'duration_after_vad', 0) or 0) > mic_voice:
        detected = _remap_info(loop_info)
        log.info(f"Idioma de la reunión (pista de Teams): {detected}")
    loop_done = []
    for seg in _paced(loop_segs_raw, should_pause, should_cancel):
        t = seg.text.strip()
        if t:
            loop_done.append((seg.start, seg.end, t))
        progress(mic_secs + seg.end)
    loop_lang = loop_args.get('language') or _remap_info(loop_info)
    loop_done = _redo_echoes(model, loop_done, _Audio(loop_path), dict(loop_args, language=loop_lang))
    if meeting:
        loop_done = _apply_plan(model, loop_done, _Audio(loop_path), loop_args, plans['loop'], loop_lang)
    loop_segments = [(s, others_label, t) for s, _e, t in loop_done]
    _log_pace("Track de Teams", loop_secs, started)

    all_segments = sorted(mic_segments + loop_segments, key=lambda x: x[0])
    lines = [f"{_format_time(ts)} {speaker}: {text}" for ts, speaker, text in all_segments]

    # Clean up speaker files — no longer needed once transcript is generated
    mic_path.unlink(missing_ok=True)
    loop_path.unlink(missing_ok=True)

    return '\n'.join(lines), detected


def _transcribe_local(audio_path: Path, on_progress=None, on_segment=None,
                      should_cancel=None, resume_from: Path | None = None,
                      should_pause=None, hotwords: str | None = None) -> tuple[str, str]:
    """Recorre el plan de fallback hasta que uno funcione.

    Antes, cualquier fallo saltaba directamente a la API de OpenAI y, sin
    OPENAI_API_KEY, la reunión se quedaba sin transcripción. Los fallos por
    memoria son recuperables: basta trocear el audio o usar un modelo menor.
    """
    mic_path = audio_path.with_suffix('.mic.wav')
    loop_path = audio_path.with_suffix('.loop.wav')
    use_speakers = mic_path.exists() and loop_path.exists()

    # La pausa (modo en vivo) antes de cargar el modelo, no despues: si no, el
    # modelo grande se queda cargado y parado junto al del en vivo toda la reunion.
    # Y antes del plan, que mira la memoria libre de ese momento.
    _wait_if_paused(should_pause, should_cancel)
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
            _wait_if_paused(should_pause, should_cancel)
            model = _get_model(name)
            if use_speakers and not chunked:
                return _transcribe_with_speakers(model, audio_path, on_progress=on_progress,
                                                 should_cancel=should_cancel,
                                                 should_pause=should_pause, hotwords=hotwords)
            if use_speakers and chunked:
                mic_path.unlink(missing_ok=True)
                loop_path.unlink(missing_ok=True)
                use_speakers = False
            if chunked:
                return _transcribe_chunks(
                    model, audio_path, on_progress=on_progress, on_segment=on_segment,
                    should_cancel=should_cancel, resume_at=resume_at,
                    prior_lines=prior_lines, should_pause=should_pause, hotwords=hotwords)
            return _transcribe_whole(model, audio_path, on_progress=on_progress,
                                     on_segment=on_segment, should_cancel=should_cancel,
                                     should_pause=should_pause, hotwords=hotwords)
        except (TranscriptionCancelled, TranscriptionDeferred):
            raise
        except Exception as e:
            last_exc = e
            if not _is_memory_error(e):
                raise
            log.warning(f"Sin memoria con '{name}'{' troceado' if chunked else ''}: {e}")
            _release_model()

    raise last_exc if last_exc else RuntimeError("transcripción local sin intentos")


def _glossary_context(audio_path: Path) -> dict:
    """Invitados + glosario de esta grabación. El glosario es un extra: si algo
    falla, se transcribe como siempre."""
    try:
        import glossary
        return glossary.recording_context(audio_path)
    except Exception as e:
        log.warning(f"Glosario no disponible: {e}")
        return {'hotwords': '', 'entries': [], 'project_id': None}


def _correct_with_glossary(text: str, ctx: dict) -> str:
    try:
        import glossary
        # Las entradas de ahora, no las del contexto: recording_context se
        # cachea al empezar a grabar, y una sugerencia aprobada durante la
        # reunion tiene que contar. Sin ninguna, las del contexto.
        entries = glossary.entries(ctx.get('project_id')) or ctx['entries']
        fixed = glossary.apply_corrections(text, entries)
        if fixed != text:
            log.info("Glosario: transcript corregido")
        return fixed
    except Exception as e:
        log.warning(f"Correcciones de glosario no aplicadas: {e}")
        return text


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
    should_pause=None,
) -> tuple[str, str] | None:
    """
    Devuelve (transcript_text, detected_language) o None si falla.
    Si on_complete es callable, ejecuta en thread daemon — on_complete(text, lang).
    should_cancel: callable sin argumentos; si devuelve True se aborta y se
    propaga TranscriptionCancelled (el llamador limpia).
    resume_from: .partial de un intento que murió a medias; se sigue desde ahí
    en vez de volver a empezar (ver partial_resume).
    should_pause: callable sin argumentos; mientras devuelva True, la
    transcripcion espera (ver _paced). La usa el modo en vivo.
    """
    def _run():
        try:
            log.info(f"Transcribiendo {audio_path.name}...")
            # Lo hecho durante la reunion no pasa por aqui: lo recoge
            # tray_app._use_live_transcript con live_transcriber.finish_live.
            glossary_ctx = _glossary_context(audio_path)
            text, lang = _transcribe_local(audio_path, on_progress=on_progress,
                                           on_segment=on_segment, should_cancel=should_cancel,
                                           resume_from=resume_from, should_pause=should_pause,
                                           hotwords=glossary_ctx['hotwords'])
            text = _correct_with_glossary(text, glossary_ctx)
        except TranscriptionCancelled:
            # Descartada a proposito: no reintentar ni llamar a la API de pago.
            log.info(f"Transcripción cancelada: {audio_path.name}")
            raise
        except TranscriptionDeferred:
            log.info(f"Transcripción aplazada: {audio_path.name} (se está grabando otra "
                     f"reunión con poco disco o memoria)")
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
        finally:
            # El modelo NO se queda cargado entre reuniones. Medido el 14/10/2026:
            # 'medium' en int8 con cpu_threads=12 reserva 4,7 GB de memoria al
            # cargarse (solo toca 0,9 GB) y el daemon lo guardaba para siempre, asi
            # que tras la primera transcripcion vivia en ~4,9 GB durante dias. Esa
            # reserva cuenta contra el limite de memoria de Windows, que agrandaba
            # el pagefile hasta dejar el disco sin espacio. Liberarlo devuelve
            # 4,2 GB; volver a cargarlo en la siguiente reunion cuesta 30-60 s
            # frente a 10-20 min de transcripcion.
            _release_model()
        if on_complete:
            on_complete(text, lang)
        return text, lang

    if on_complete:
        t = threading.Thread(target=_run, daemon=True, name='Transcriber')
        t.start()
        return None
    return _run()
