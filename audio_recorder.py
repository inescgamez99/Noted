import collections
import logging
import os
import queue
import subprocess
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from config import SAMPLE_RATE, PROJECT_DIR

_RECORDING_SENTINEL = PROJECT_DIR / '.recording_active'


def _write_sentinel():
    try:
        _RECORDING_SENTINEL.write_text(str(os.getpid()))
    except OSError:
        pass


def _remove_sentinel():
    try:
        _RECORDING_SENTINEL.unlink(missing_ok=True)
    except OSError:
        pass

log = logging.getLogger(__name__)

_LOOPBACK_WORKER = str(PROJECT_DIR / 'wasapi_loopback_worker.py')

# Bloque de mezcla: 262144 muestras = 1 MB en float32. La memoria usada al
# guardar es este bloque por stream, independientemente de lo que dure la
# reunión.
_BLOCK = 1 << 18

# Al entrar en una llamada, Teams y Windows reconfiguran los endpoints de audio y
# WASAPI puede rechazar la apertura del loopback durante unos segundos. Sin
# reintento, ese fallo momentaneo condena la reunion entera a grabarse sin las
# voces de los demas.
_LOOPBACK_ATTEMPTS = 3
_LOOPBACK_RETRY_WAIT = 1.5
# Cuanto se espera a que el worker abra el loopback y mande su cabecera. Al
# entrar en una llamada Teams reconfigura los dispositivos y abrirlos puede
# tardar mas de 5 s; con ese limite el worker (vivo, solo lento) se mataba y el
# log decia "termino con codigo 1", que es el codigo de terminate().
_LOOPBACK_HEADER_TIMEOUT = 40.0
# Aviso al usuario si pasa tanto sin audio del sistema: no hace falta esperar a
# agotar los intentos (con workers lentos pueden ser minutos).
_LOOPBACK_WARN_AFTER = 20.0
# El worker NO se mata a los 15 s para empezar de cero: se lanza otro al lado
# cada _LOOPBACK_STAGGER s (hasta _LOOPBACK_MAX_WORKERS) y gana el primero que
# mande la cabecera. El 29/10/2026 cuatro workers seguidos tardaron mas de 15 s
# (arrancar Python con la CPU saturada) y a cada uno se le mataba justo antes de
# terminar: 94 s sin audio de los demas, cuando uno solo habria llegado a los 20.
_LOOPBACK_STAGGER = 4.0
_LOOPBACK_MAX_WORKERS = 3
# Un worker "armado" (ver arm_loopback) tiene ya Python cargado: si no responde
# en tanto, se lanza uno normal a su lado.
_LOOPBACK_STANDBY_GRACE = 3.0
# Tras usar el armado, el siguiente se arranca con la reunion ya en marcha.
_LOOPBACK_REARM_DELAY = 20.0
# Pasados los intentos iniciales se sigue reintentando cada tanto mientras dure
# la grabacion: es mejor tener las voces de los demas desde el minuto 5 que no
# tenerlas nunca.
_LOOPBACK_LATE_WAIT = 10.0

# Corte del paso-alto aplicado al micrófono. El micro introduce un offset DC y
# rumble por debajo de esta frecuencia que llega a dominar el espectro; como
# después se normaliza el pico a 0.8, ese rumble se come el margen dinámico de
# las voces y el filtro de voz de Whisper acaba descartando tramos con habla.
# La voz humana no tiene contenido útil por debajo de 80 Hz.
_HIGHPASS_HZ = 80.0

# Vigilancia del micrófono. Con auriculares Bluetooth, Windows cambia de perfil
# y de dispositivo por defecto al entrar en una llamada, y el stream que se
# abrió al empezar puede quedarse mudo o parar sin lanzar ningún error. Antes
# eso dejaba la grabación sin la voz de quien graba y nadie se enteraba.
_MIC_STALL_SECONDS = 2.0      # sin callbacks: el stream está muerto
_MIC_SILENT_SECONDS = 15.0    # solo ceros digitales: el dispositivo ya no entrega señal
_MIC_SILENT_RETRY = 30.0      # entre reaperturas por silencio (puede ser mute de hardware)
_MIC_RETRY_WAIT = 2.0         # entre intentos mientras no hay micrófono
# Cada cuanto se mira si Teams ha cambiado de micro (ver _follow_teams_mic).
_MIC_FOLLOW_S = 5.0
_MIC_WARN_AFTER = 6.0         # sin micrófono tanto tiempo: avisar al usuario
# Un dispositivo muerto no entrega ceros exactos sino ±1-2 LSB de 16 bits
# (~3e-5 a 6e-5): en la prueba del 28/10 el micro estuvo así 25 s y con el
# umbral anterior (1e-5) el vigilante no lo vio. Un micro vivo en una sala
# silenciosa queda muy por encima de esto (~1e-3).
_MIC_SILENCE_PEAK = 2e-4

# Disco lleno. 26/10/2026: el escritor moria al primer fallo y el resto de una
# reunion (11 min) se quedo en la cola y se tiro al colgar. Ahora el audio espera
# en memoria y el escritor reintenta cada _WRITE_RETRY_S; el guardado final, cada
# _SAVE_RETRY_S, y los .part no se borran hasta tener el WAV.
_WRITE_RETRY_S = 5.0
_SAVE_RETRY_S = 30.0

# libsndfile solo reescribe el header de un fichero abierto en 'r+' al cerrarlo.
# Medido el 26/10/2026: reabierto un .part, escritos 8000 frames mas y muerto el
# proceso sin cerrar, sf.info seguia diciendo los de la reapertura, y la
# recuperacion habria cortado la reunion ahi. Un fichero nuevo no lo necesita:
# mientras esta abierto el chunk de datos vale 0 y los lectores usan el tamaño.
# soundfile no le da nombre: 0x1060 es SFC_UPDATE_HEADER_NOW en sndfile.h.
_SFC_UPDATE_HEADER_NOW = 0x1060


def _update_header_now(f) -> None:
    sf._snd.sf_command(f._file, _SFC_UPDATE_HEADER_NOW, sf._ffi.NULL, 0)


def _is_retryable(e: Exception) -> bool:
    """Lo que puede arreglarse esperando: disco lleno, un fichero bloqueado, falta
    de memoria. Un fallo de formato o del codigo no; reintentarlo cada 30 s
    dejaria la puerta de reinicio cerrada para siempre."""
    if isinstance(e, (OSError, MemoryError)):
        return True
    return isinstance(e, sf.LibsndfileError) and e.code == 2      # SF_ERR_SYSTEM


class AudioRecorder:
    """Graba micrófono + audio del sistema escribiendo a disco de forma
    incremental.

    El audio NO se acumula en memoria: cada callback encola su bloque y un hilo
    escritor lo vuelca a un fichero temporal. El consumo de RAM es constante,
    dure la reunión 5 minutos o 3 horas, y como se hace flush en cada escritura,
    un corte del proceso deja el audio grabado hasta ese instante en lugar de
    perderlo entero.
    """

    def __init__(self):
        self._loop_sr: int = SAMPLE_RATE
        self._loop_ch: int = 1
        self._recording = False
        self._output_path: Path | None = None
        # Arranca "set": no hay ningún guardado en curso todavía. Sin esto, el
        # primer start() se queda esperando el timeout completo en el wait() de
        # más abajo.
        self._save_event = threading.Event()
        self._save_event.set()
        self._mic_name: str | None = None   # micro de Teams que se está siguiendo
        self._loopback_proc: subprocess.Popen | None = None
        # Worker de loopback ya arrancado y esperando la orden (arm_loopback).
        self._standby: subprocess.Popen | None = None
        self._standby_enabled = False
        self._standby_lock = threading.Lock()
        # Ultimo audio del sistema recibido: para rellenar con silencio el hueco
        # si el worker muere y se relanza en mitad de la reunion.
        self._loop_t_last: float | None = None
        self._stream: sd.InputStream | None = None
        self._stereo_stream: sd.InputStream | None = None
        # Serializa start(): hay dos llamadores independientes en el mismo
        # proceso (el popup de detección en main.py y "Grabar ahora" en
        # tray_app.py) y sin esto pueden entrar los dos a la vez.
        self._start_lock = threading.Lock()

        # Una por grabacion: cancel() la activa para que un escritor que reintenta
        # con el disco lleno suelte el audio descartado en vez de seguir.
        self._writer_abort = threading.Event()
        # Stems cuyo WAV se esta guardando. _save_event esta "set" cuando no hay
        # ninguno: lo espera _quit para no cortar un guardado.
        self._saving_stems: set[str] = set()
        self._saves_lock = threading.Lock()
        self._failing: set[str] = set()      # ver _set_failing
        self._failing_lock = threading.Lock()
        self._mic_q: queue.Queue | None = None
        self._loop_q: queue.Queue | None = None
        self._mic_writer: threading.Thread | None = None
        self._loop_writer: threading.Thread | None = None
        self._tmp_mic: Path | None = None
        self._tmp_loop: Path | None = None
        self._chunk_mic_pos: int = 0
        self._loop_carry: np.ndarray = np.zeros(0, dtype=np.float32)
        self._loop_phase: float = 0.0
        self._loopback_error: str | None = None
        self._dc_x1: float = 0.0
        self._dc_y1: float = 0.0
        # Instante en que arrancó el micrófono, para compensar el retraso con
        # que se abre el loopback en segundo plano.
        self._mic_t0: float | None = None

        # Estado de la vigilancia del micrófono (ver _mic_watchdog).
        self._mic_lock = threading.Lock()        # contadores que toca el callback
        self._mic_swap_lock = threading.Lock()   # serializa reabrir/cerrar el stream
        self._mic_last_cb: float = 0.0
        self._mic_last_loud: float = 0.0
        self._mic_samples: int = 0
        self._mic_error: str | None = None
        self._mic_watch_stop = threading.Event()
        self._mic_watch: threading.Thread | None = None

        # True si se está capturando el audio del sistema. Cuando es False la
        # grabación solo contiene tu micrófono, y con auriculares eso significa
        # que no queda ni rastro de lo que dicen los demás.
        self.loopback_active = False

        self.on_recording_stopped = None     # callable(wav_path)
        self.on_loopback_unavailable = None  # callable(reason) — grabando solo micrófono
        self.on_write_error = None           # callable(reason) — error al escribir a disco
        self.on_write_recovered = None       # callable() — se pudo escribir otra vez
        self.on_save_failed = None           # callable(reason) — WAV sin guardar, .part conservados
        self.on_mic_unavailable = None       # callable(reason) — sin micrófono, se sigue reintentando

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_saving(self) -> bool:
        """Algun WAV se esta guardando (o esperando sitio en el disco)."""
        return not self._save_event.is_set()

    def start(self, output_path: Path) -> bool:
        """False si ya se estaba grabando: no se abre otra grabacion encima.
        Quien llama no debe tratar output_path como la grabacion en curso."""
        # Reservar el flag ANTES de cualquier espera. Con la comprobación y la
        # asignación separadas por el wait() de abajo, dos llamadas casi
        # simultáneas pasaban las dos el "if" y abrían cada una su InputStream y
        # su worker WASAPI; el segundo start() sobreescribía self._stream y
        # dejaba el primero huérfano pero activo, y al recolectarlo el proceso
        # moría con ACCESS_VIOLATION dentro del callback de audio.
        # _begin entero bajo el lock: un stop() que llegara mientras se abre el
        # InputStream (~1 s) soltaba los writers y dejaba un stream sin cerrar.
        with self._start_lock:
            if self._recording:
                return False
            self._recording = True
            try:
                self._begin(output_path)
            except Exception:
                self._recording = False
                self._close_inputs()
                self._writer_abort.set()
                self._close_writers()
                raise
        return True

    def _begin(self, output_path: Path):
        # El guardado de la reunion anterior ya no se espera: tiene sus propias
        # rutas y, con el disco lleno, puede tardar lo que tarde en haber sitio.
        # Esperarlo retrasaba el micro de la siguiente hasta 30 s. Solo si fuera
        # el mismo stem (get_recording_path ya lo evita) se pisarian los .part.
        if output_path.stem in self._saving_stems:
            log.warning(f"Aun se guarda {output_path.name}; espero antes de grabar encima")
            self._save_event.wait(timeout=30)
        self._writer_abort = threading.Event()
        self._output_path = output_path
        self._chunk_mic_pos = 0
        self._reset_resampler()
        self._reset_dc_filter()

        # Extensión .part a propósito: el pipeline busca *.wav en recordings/ y
        # no debe recoger un temporal a medio escribir.
        stem = str(output_path.with_suffix(''))
        self._tmp_mic = Path(stem + '.mic.part')
        self._tmp_loop = Path(stem + '.loop.part')
        self._discard_temp()
        # El writer abre el .part con el primer bloque. Si esa apertura falla
        # (disco lleno), sin este fichero ningun {stem}.* diria que el nombre esta
        # ocupado y otra grabacion del mismo minuto podria recibirlo.
        try:
            self._tmp_mic.touch()
        except OSError:
            pass

        self._mic_q = queue.Queue()
        self._mic_writer = threading.Thread(
            target=self._writer_loop,
            args=(self._mic_q, self._tmp_mic, 1, SAMPLE_RATE, 'mic'),
            kwargs={'highpass': True, 'abort': self._writer_abort},
            daemon=True, name='MicWriter',
        )
        self._mic_writer.start()

        self._pick_teams_mic()
        # Si el micrófono no abre no se aborta la grabación: el loopback puede
        # funcionar, y el vigilante sigue reintentando el micro mientras dure.
        self._mic_samples = 0
        self._mic_error = None
        if not self._open_mic_stream():
            self._refresh_portaudio()
            self._open_mic_stream()
        self._mic_t0 = time.monotonic()
        if self._stream is None:
            log.error(f"Micrófono no disponible al empezar ({self._mic_error}); "
                      f"se seguirá intentando durante la grabación")
        self._mic_watch_stop = threading.Event()
        self._mic_watch = threading.Thread(
            target=self._mic_watchdog, args=(self._mic_watch_stop,),
            daemon=True, name='MicWatchdog',
        )
        self._mic_watch.start()
        threading.Thread(target=self._follow_teams_mic, daemon=True, name='MicFollow').start()

        # _recording ya se puso en start(), bajo el lock.

        # El loopback se abre en segundo plano para que start() no espere por él.
        # El retraso que eso introduce se compensa con silencio al abrir su
        # fichero (ver _open_loop_writer), asi que no desalinea las voces.
        self.loopback_active = False
        threading.Thread(target=self._start_loopback_async, args=(self._writer_abort,),
                         daemon=True, name='LoopbackInit').start()

        _write_sentinel()
        log.info(f"Recording started → {output_path}")

    def stop(self) -> Path | None:
        # Bajo _start_lock: un start() de la reunion siguiente no puede colarse
        # entre bajar el flag y soltar los writers, o se quedaria con los de esta.
        with self._start_lock:
            if not self._recording:
                return None
            self._recording = False
            self._close_inputs()
            # Si el micro estuvo caído al final, el fichero se queda corto y, si no
            # llegó a abrirse nunca, vacío (y entonces se descartaba todo, loopback
            # incluido). Rellenar con silencio mantiene la línea temporal. Antes de
            # soltar los writers, que se llevan la cola.
            self._mic_pad_to_now()
            # Sin esperar a los writers: con el disco lleno tardan lo que tarde
            # en haber sitio, y stop() lo llama la bandeja. Los espera el guardado.
            writers = self._detach_writers()
            mic_path, loop_path, out_path = self._tmp_mic, self._tmp_loop, self._output_path
            with self._saves_lock:
                self._saving_stems.add(out_path.stem)
                self._save_event.clear()

        t = threading.Thread(
            target=self._process_and_save, args=(mic_path, loop_path, out_path, writers),
            daemon=True, name='AudioProcessor',
        )
        t.start()
        return out_path

    def cancel(self) -> None:
        """Para la grabación y descarta el audio sin guardar ni procesar."""
        with self._start_lock:
            if not self._recording:
                return
            self._recording = False
            self._close_inputs()
            self._writer_abort.set()
            writers = self._detach_writers()
            temps = (self._tmp_mic, self._tmp_loop)
        # Los joins fuera del lock: un writer atascado en libsndfile no debe
        # retrasar el start() de la reunion siguiente. Al abortar, cada writer
        # borra su .part; esto recoge lo que quede.
        for th in writers:
            th.join(timeout=30)
        self._discard_temp(*temps)
        self._remove_sentinel_if_idle()
        log.info("Recording cancelled — audio discarded")

    def _remove_sentinel_if_idle(self) -> None:
        """.recording_active es la puerta de reinicio del daemon (tr_env.ps1): no
        se quita mientras quede una grabacion o un guardado con audio en memoria."""
        with self._saves_lock:
            if not self._recording and not self._saving_stems:
                _remove_sentinel()

    def _set_failing(self, key: str, reason: str | None) -> None:
        """Lleva la cuenta de lo que no puede escribir (cada writer, cada guardado).
        Avisa al empezar el primer fallo y cuando ya no falla ninguno: con dos
        writers, que se recupere el micro no significa que el sistema escriba."""
        with self._failing_lock:
            was = bool(self._failing)
            if reason is None:
                self._failing.discard(key)
            else:
                self._failing.add(key)
            now = bool(self._failing)
        if now and not was:
            self._fire(self.on_write_error, reason, 'WriteErrorWarning')
        elif was and not now:
            self._fire(self.on_write_recovered, None, 'WriteRecovered')

    def _clear_failing(self, key: str) -> None:
        """Quita un fallo sin avisar de recuperacion: una reunion descartada o un
        guardado abandonado no han vuelto a escribir."""
        with self._failing_lock:
            self._failing.discard(key)

    def wait_for_save(self, timeout: int = 60) -> bool:
        return self._save_event.wait(timeout=timeout)

    # ── captura ──────────────────────────────────────────────────────────────

    def _pick_teams_mic(self) -> None:
        """El micro que usa Teams (p.ej. unos auriculares Bluetooth) va primero,
        no el predeterminado de Windows: ver mic_select.py y _open_mic_stream."""
        try:
            from mic_select import teams_capture_device
            self._mic_name = teams_capture_device()
        except Exception as e:
            log.debug(f"No se pudo saber que micro usa Teams: {e}")
            self._mic_name = None
        if self._mic_name and self._teams_mic_index() is None:
            self._refresh_portaudio()    # p.ej. auriculares conectados despues de arrancar Noted
        log.info(f"Micro de Teams: {self._mic_name or 'no detectado, se usa el predeterminado'}")

    def _teams_mic_index(self):
        """Indice PortAudio del micro que usa Teams (p.ej. los AirPods), o None."""
        if not self._mic_name:
            return None
        try:
            from mic_select import input_device_index
            return input_device_index(self._mic_name)
        except Exception as e:
            log.debug(f"No se pudo buscar el micro de Teams: {e}")
            return None

    def _follow_teams_mic(self):
        """Si Teams cambia de micro a mitad de llamada (p.ej. se conectan los
        AirPods), la grabacion se cambia con el. Ver mic_select.py."""
        from mic_select import teams_capture_device
        while self._recording:
            time.sleep(_MIC_FOLLOW_S)
            if not self._recording:
                break
            try:
                name = teams_capture_device()
            except Exception:
                continue
            if name and name != self._mic_name:
                old, self._mic_name = self._mic_name, name
                log.info(f"Teams cambio de micro: '{old or 'predeterminado'}' -> '{name}'")
                # _reopen_mic rellena el hueco con silencio y abre primero el de Teams.
                self._reopen_mic()

    def _mic_cb(self, indata, frames, time_info, status):
        # Solo encolar: PortAudio invoca este callback en un hilo de tiempo real
        # y escribir a disco aquí produciría cortes en el audio.
        q = self._mic_q
        if q is None:
            return
        block = indata.copy().reshape(-1)
        now = time.monotonic()
        with self._mic_lock:
            self._mic_last_cb = now
            self._mic_samples += len(block)
            if len(block) and float(np.abs(block).max()) > _MIC_SILENCE_PEAK:
                self._mic_last_loud = now
        q.put(block)

    # ── vigilancia del micrófono ─────────────────────────────────────────────

    @staticmethod
    def _mic_candidates() -> list:
        """El dispositivo por defecto primero y, si falla, el resto de entradas
        de su misma API (excluyendo el loopback), para no quedarse sin micro
        por un dispositivo concreto que Windows acaba de retirar."""
        cands: list = [None]
        try:
            default_api = sd.query_hostapis(sd.default.hostapi)['name']
            apis = sd.query_hostapis()
            for i, dev in enumerate(sd.query_devices()):
                name = dev['name'].lower()
                if (dev['max_input_channels'] > 0
                        and apis[dev['hostapi']]['name'] == default_api
                        and not any(k in name for k in ('stereo mix', 'mezcla', 'loopback', 'mapper'))):
                    cands.append(i)
        except Exception as e:
            log.debug(f"No se pudo listar los micrófonos alternativos: {e}")
        return cands

    def _open_mic_stream(self) -> bool:
        last = None
        cands = self._mic_candidates()
        teams_dev = self._teams_mic_index()
        if teams_dev is not None:
            cands = [teams_dev] + [c for c in cands if c != teams_dev]
        for dev in cands:
            stream = None
            try:
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE, channels=1, dtype='float32',
                    blocksize=int(SAMPLE_RATE * 0.1), device=dev,
                    callback=self._mic_cb,
                )
                stream.start()
            except Exception as e:
                last = e
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                continue
            now = time.monotonic()
            with self._mic_lock:
                self._mic_last_cb = now
                self._mic_last_loud = now
            self._stream = stream
            try:
                name = sd.query_devices(stream.device)['name']
            except Exception:
                name = str(dev)
            log.info(f"Micrófono abierto: {name}")
            return True
        self._mic_error = str(last) if last else 'no hay dispositivos de entrada'
        return False

    def _refresh_portaudio(self):
        """PortAudio guarda la lista de dispositivos del momento en que arrancó.
        Tras un cambio de auriculares el "por defecto" sigue apuntando al
        anterior hasta reiniciarlo. No se hace si el loopback por Stereo Mix
        tiene un stream abierto, porque se lo llevaría por delante."""
        if self._stereo_stream is not None:
            return
        try:
            sd._terminate()
            sd._initialize()
        except Exception as e:
            log.warning(f"No se pudo refrescar la lista de dispositivos de audio: {e}")

    def _mic_pad_to_now(self):
        """Rellena con silencio lo que falta del micrófono respecto al reloj,
        para que el hueco de una caída no desalinee el micro con el loopback."""
        q, t0 = self._mic_q, self._mic_t0
        if q is None or t0 is None:
            return
        with self._mic_lock:
            deficit = int((time.monotonic() - t0) * SAMPLE_RATE) - self._mic_samples
            if deficit <= SAMPLE_RATE * 0.2:
                return
            self._mic_samples += deficit
        q.put(np.zeros(deficit, dtype=np.float32))

    def _reopen_mic(self) -> bool:
        with self._mic_swap_lock:
            if not self._recording:
                return False
            old, self._stream = self._stream, None
            if old is not None:
                # Cerrar un dispositivo que ya murió puede bloquearse: en un hilo
                # aparte para que la vigilancia no se quede colgada con él.
                def _close(s=old):
                    try:
                        s.stop()
                        s.close()
                    except Exception:
                        pass
                closer = threading.Thread(target=_close, daemon=True, name='MicClose')
                closer.start()
                closer.join(timeout=3)
            self._refresh_portaudio()
            self._mic_pad_to_now()
            return self._open_mic_stream()

    def _mic_watchdog(self, stop: threading.Event):
        """Garantiza que el micrófono sigue entregando audio durante toda la
        grabación y lo reabre (en el dispositivo por defecto actual) si no."""
        outage_since: float | None = None
        warned = False
        next_try = 0.0
        silent_next = 0.0
        while not stop.wait(1.0):
            if not self._recording:
                return
            now = time.monotonic()
            with self._mic_lock:
                since_cb = now - self._mic_last_cb
                since_loud = now - self._mic_last_loud
            reason = None
            if self._stream is None:
                reason = f"sin stream ({self._mic_error})"
            elif since_cb > _MIC_STALL_SECONDS:
                reason = f"sin datos desde hace {since_cb:.0f}s"
            elif since_loud > _MIC_SILENT_SECONDS and now >= silent_next:
                reason = f"solo silencio digital desde hace {since_loud:.0f}s"
                silent_next = now + _MIC_SILENT_RETRY
            if reason is None:
                if outage_since is not None:
                    log.info(f"Micrófono recuperado tras {now - outage_since:.0f}s")
                    outage_since, warned = None, False
                continue
            if outage_since is None:
                outage_since = now
                log.warning(f"Micrófono caído: {reason}. Reabriendo en el dispositivo por defecto")
            if now < next_try:
                continue
            next_try = now + _MIC_RETRY_WAIT
            if self._reopen_mic():
                outage_since, warned = None, False
                log.info("Micrófono reabierto")
                continue
            if not warned and now - outage_since >= _MIC_WARN_AFTER:
                warned = True
                log.error(f"SIN micrófono desde hace {now - outage_since:.0f}s: {self._mic_error}. "
                          f"Se sigue reintentando.")
                if self.on_mic_unavailable:
                    threading.Thread(
                        target=self.on_mic_unavailable, args=(self._mic_error or reason,),
                        daemon=True, name='MicWarning',
                    ).start()

    def _writer_loop(self, q: queue.Queue, path: Path, channels: int, samplerate: int,
                     label: str, highpass: bool = False, abort: threading.Event | None = None):
        """Vuelca la cola al .part. Un fallo al escribir no lo para: los bloques
        esperan en memoria, en orden, y cada _WRITE_RETRY_S se reabre el fichero
        y se escriben. Con el disco lleno libsndfile lanza LibsndfileError, que no
        es un OSError: por eso vale cualquier excepcion (26/10/2026, ver arriba).

        Si se aborta (cancel) suelta lo pendiente y borra su .part: con el
        fichero abierto, cancel() no podria borrarlo y la reunion descartada
        volveria al arrancar como grabacion a medio guardar."""
        key = f'{label}:{path.name}'
        pending = collections.deque()
        dc = [0.0, 0.0]         # paso-alto propio: con stop() sin esperar, dos writers pueden convivir
        f = None
        reopened = False        # en 'r+' el header no se reescribe solo
        written = 0             # frames que este writer sabe en disco
        failing_since = None
        next_try = 0.0
        done = False
        try:
            while not done or pending:
                if abort is not None and abort.is_set():
                    return
                if not done:
                    try:
                        block = q.get(timeout=_WRITE_RETRY_S if pending else None)
                    except queue.Empty:
                        block = ()
                    if block is None:
                        done = True
                    elif len(block):
                        pending.append(self._dc_block(block, dc) if highpass else block)
                elif failing_since is not None:
                    # Ya no llega nada: esperar al siguiente intento sin girar.
                    wait = next_try - time.monotonic()
                    if wait > 0:
                        (abort.wait(wait) if abort is not None else time.sleep(wait))
                if not pending or time.monotonic() < next_try:
                    continue
                try:
                    if f is None:
                        if written and not (path.exists() and path.stat().st_size > 0):
                            # Entre reintentos el .part queda cerrado y alguien lo
                            # borro (liberando espacio, como pide el aviso). Lo ya
                            # escrito se perdio; lo pendiente no tiene por que.
                            log.error(f"Writer {label}: {path.name} desaparecio con {written} "
                                      f"frames escritos; sigo con lo pendiente en uno nuevo")
                            written = 0
                        f = self._open_part(path, channels, samplerate, written or None)
                        reopened = bool(written)
                    while pending:
                        if abort is not None and abort.is_set():
                            return
                        f.write(pending[0])
                        # Flush en cada bloque: deja el audio en disco si el
                        # proceso muere a mitad de la reunión.
                        f.flush()
                        if reopened:
                            _update_header_now(f)
                        written += len(pending.popleft())
                except Exception as e:
                    f = self._close_quietly(f)
                    next_try = time.monotonic() + _WRITE_RETRY_S
                    if failing_since is None:
                        failing_since = time.monotonic()
                        log.error(f"Writer {label}: no se pudo escribir ({e}); el audio sigue "
                                  f"en memoria y se reintenta cada {_WRITE_RETRY_S:.0f} s",
                                  exc_info=True)
                        self._set_failing(key, str(e))
                else:
                    if failing_since is not None:
                        log.warning(f"Writer {label}: escribe otra vez tras "
                                    f"{time.monotonic() - failing_since:.0f} s, sin perder audio")
                        failing_since = None
                        self._set_failing(key, None)
        finally:
            self._close_quietly(f)
            if abort is not None and abort.is_set():
                self._clear_failing(key)
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _open_part(path: Path, channels: int, samplerate: int, frames: int | None = None):
        """Sin `frames`, fichero nuevo. Con `frames` (los que el writer ya sabe en
        disco), 'r+' y truncate a esa cifra: quita lo que una escritura a medias
        dejo detras, para no repetir ni perder bloques (probado con libsndfile
        real, ver tests/test_disk_full.py)."""
        if frames:
            f = sf.SoundFile(str(path), mode='r+')
            try:
                if f.frames < frames:
                    log.warning(f"{path.name}: el header dice {f.frames} frames y hay {frames}")
                f.truncate(min(frames, f.frames))
                _update_header_now(f)
            except Exception:
                f.close()
                raise
            return f
        return sf.SoundFile(
            str(path), mode='w', samplerate=samplerate,
            channels=channels, format='WAV', subtype='FLOAT',
        )

    @staticmethod
    def _close_quietly(f):
        if f is not None:
            try:
                f.close()
            except Exception:
                pass
        return None

    @staticmethod
    def _fire(callback, arg, name: str) -> None:
        """Avisos en un hilo aparte: un toast lento no debe frenar al escritor."""
        if callback:
            threading.Thread(target=callback, args=() if arg is None else (arg,),
                             daemon=True, name=name).start()

    def _start_loopback_async(self, abort: threading.Event | None = None,
                              resume: bool = False):
        """Abre el loopback con reintentos, fuera del hilo de start().

        Al entrar en una llamada, Teams y Windows reconfiguran los endpoints y
        WASAPI rechaza la apertura durante unos segundos; sin reintento ese
        fallo momentáneo condenaba la reunión entera a grabarse sin las voces
        de los demás.

        Atado a la grabación que lo lanzó: _writer_abort es nuevo en cada
        _begin, y start() ya no espera a que se guarde la reunión anterior. Con
        "mientras se grabe", un reintento de la reunión A dormido 10 s se
        despertaba con la B ya grabando y le abría un segundo worker encima.
        abort llega de _begin al crear el hilo: leído aquí dentro, un hilo que
        tardara en arrancar podría ver ya el de la reunión siguiente.

        resume=True: el worker murió en mitad de la grabación. Se reutiliza el
        mismo fichero y el hueco se rellena con silencio (ver _start_wasapi_loopback).
        """
        if abort is None:
            abort = self._writer_abort

        def mine() -> bool:
            return self._recording and self._writer_abort is abort

        # El lector lo usa para relanzar el worker si muere (ver _wasapi_reader).
        mine.abort = abort

        state = {'warned': False}
        notice_lock = threading.Lock()

        def warn_user(why: str, header: str) -> None:
            """Una sola vez por episodio: el aviso del usuario y la linea del log."""
            with notice_lock:
                if state['warned'] or not mine():
                    return
                state['warned'] = True
            log.error(
                f"{header} ({why}). La grabación tendrá SOLO tu micrófono hasta que se "
                f"recupere: si usas auriculares, no se capturará nada de lo que digan "
                f"los demás. Se sigue intentando."
            )
            if self.on_loopback_unavailable:
                threading.Thread(
                    target=self.on_loopback_unavailable, args=(why,),
                    daemon=True, name='LoopbackWarning',
                ).start()

        # El aviso sale a los _LOOPBACK_WARN_AFTER s sin audio del sistema, no
        # cuando se agotan los intentos: el 29/10 eso habria sido a los 94 s, y con
        # workers lentos podria ser mucho mas.
        notice = threading.Timer(
            _LOOPBACK_WARN_AFTER,
            lambda: (not self.loopback_active) and warn_user(
                self._loopback_error or 'el worker aún no ha abierto el loopback',
                f"SIN audio del sistema tras {_LOOPBACK_WARN_AFTER:.0f}s"),
        )
        notice.daemon = True
        notice.start()
        try:
            self._loopback_attempts(mine, abort, resume, warn_user, state)
        finally:
            notice.cancel()

    def _loopback_attempts(self, mine, abort, resume, warn_user, state) -> None:
        reason = 'no se intentó'
        attempt = 0
        while mine():
            attempt += 1
            self._loopback_error = None
            if resume:
                ok = self._start_wasapi_loopback(mine, resume=True)
            else:
                ok = self._start_wasapi_loopback(mine)
            if ok or (not resume and mine() and self._start_stereo_mix_loopback(mine)):
                self.loopback_active = True
                if state['warned']:
                    log.info(f"Audio del sistema recuperado en el intento {attempt}; "
                             f"el tramo anterior solo tiene el micrófono")
                return
            if not mine():
                # Su grabación terminó durante el intento: ni avisos de "solo
                # micrófono" que el usuario vería en la reunión siguiente.
                return
            reason = self._loopback_error or 'sin dispositivo loopback disponible'
            if attempt < _LOOPBACK_ATTEMPTS:
                log.warning(
                    f"Loopback no disponible (intento {attempt}/{_LOOPBACK_ATTEMPTS}): "
                    f"{reason} — reintentando en {_LOOPBACK_RETRY_WAIT}s"
                )
                abort.wait(_LOOPBACK_RETRY_WAIT)       # un cancel() lo despierta ya
                continue
            if not state['warned']:
                warn_user(reason, f"SIN audio del sistema tras {attempt} intentos")
            else:
                log.warning(f"Loopback sigue sin arrancar (intento {attempt}): {reason}")
            abort.wait(_LOOPBACK_LATE_WAIT)

    # ── workers de loopback ──────────────────────────────────────────────────

    @staticmethod
    def _popen_loopback_worker(standby: bool = False) -> subprocess.Popen:
        # stderr capturado, no descartado: el worker explica ahí por qué no
        # pudo abrir el loopback y antes ese motivo se perdía, dejando el
        # fallo invisible en el log.
        _si = subprocess.STARTUPINFO()
        _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        _CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
        return subprocess.Popen(
            [sys.executable, _LOOPBACK_WORKER] + (['--standby'] if standby else []),
            stdin=subprocess.PIPE if standby else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=_si if os.name == 'nt' else None,
            creationflags=_CREATE_NO_WINDOW,
        )

    def arm_loopback(self) -> None:
        """Deja un worker de loopback YA ARRANCADO esperando la orden de grabar.

        Arrancar un Python nuevo al entrar en la llamada, justo cuando Teams, el
        pipeline y la transcripcion compiten por la CPU, tardaba 4-7 s en esta
        maquina y el 29/10/2026 paso de 15 s cuatro veces seguidas. Armado, el
        proceso ya tiene el interprete y las librerias cargados y solo le queda
        abrir los dispositivos (~1 s). Si no esta o no responde, se usa el
        arranque normal: esto solo acelera, nada depende de ello."""
        self._standby_enabled = True
        self._spawn_standby()

    def disarm_loopback(self) -> None:
        with self._standby_lock:
            self._standby_enabled = False
            p, self._standby = self._standby, None
        if p is not None:
            self._reap_workers([p], 'desarmado')

    def _spawn_standby(self) -> None:
        with self._standby_lock:
            if not self._standby_enabled:
                return
            if self._standby is not None and self._standby.poll() is None:
                return
            try:
                self._standby = self._popen_loopback_worker(standby=True)
            except Exception as e:
                self._standby = None
                log.warning(f"No se pudo armar el worker de loopback: {e}")

    def _take_standby(self):
        """El worker armado listo para usar, ya con la orden enviada, o None."""
        with self._standby_lock:
            p, self._standby = self._standby, None
        if p is None:
            return None
        try:
            if p.poll() is not None:
                return None
            p.stdin.write(b'go\n')
            p.stdin.flush()
            p.stdin.close()
        except Exception:
            self._reap_workers([p], 'armado roto')
            return None
        if self._standby_enabled:
            t = threading.Timer(_LOOPBACK_REARM_DELAY, self._spawn_standby)
            t.daemon = True
            t.start()
        return p

    def _reap_workers(self, procs, why: str) -> None:
        """Cierra los workers sobrantes sin retrasar a quien llama, y deja en el
        log lo que cada uno dijo por stderr: ahi esta el paso en el que se
        quedo."""
        def run():
            for p in procs:
                msg = self._drain_worker_error(p)
                if why != 'desarmado':
                    log.warning(f"Worker de loopback descartado ({why}): "
                                f"{msg.replace(chr(10), ' | ')}")
        threading.Thread(target=run, daemon=True, name='LoopbackReap').start()

    def _race_loopback_workers(self, still=None):
        """Lanza workers hasta que uno manda la cabecera.

        Devuelve (proc, header, None) o (None, None, motivo). Los que no ganan se
        cierran. Un worker lento NO se mata para empezar otro: se le suma uno al
        lado cada _LOOPBACK_STAGGER s, y gana el que llegue primero.
        """
        results: queue.Queue = queue.Queue()
        procs: list = []
        errors: list[str] = []

        def reader(p):
            try:
                h = p.stdout.read(8)
            except Exception:
                h = b''
            results.put((p, h))

        def launch(p):
            procs.append(p)
            threading.Thread(target=reader, args=(p,), daemon=True,
                             name='LoopbackHeader').start()

        t0 = time.monotonic()
        deadline = t0 + _LOOPBACK_HEADER_TIMEOUT
        pending = 0
        cold = 0
        winner = None
        next_cold = t0
        first = self._take_standby()
        if first is not None:
            launch(first)
            pending += 1
            next_cold = t0 + _LOOPBACK_STANDBY_GRACE

        while winner is None:
            now = time.monotonic()
            if now >= next_cold and now < deadline and cold < _LOOPBACK_MAX_WORKERS:
                try:
                    launch(self._popen_loopback_worker())
                    pending += 1
                    cold += 1
                except Exception as e:
                    errors.append(f"no se pudo lanzar el worker: {e}")
                next_cold = now + _LOOPBACK_STAGGER
            if still is not None and not still():
                break
            if pending == 0 and (now >= deadline or cold >= _LOOPBACK_MAX_WORKERS or errors):
                break
            if now >= deadline:
                break
            nxt = next_cold if cold < _LOOPBACK_MAX_WORKERS else deadline
            wait = max(0.05, min(nxt, deadline) - now)
            try:
                p, h = results.get(timeout=min(wait, 0.5))
            except queue.Empty:
                continue
            pending -= 1
            if len(h) == 8:
                winner = (p, h)
            else:
                # Salio sin cabecera (fallo rapido): su motivo, y se deja que el
                # reintento de fuera lo vuelva a probar tras una pausa corta.
                procs.remove(p)
                errors.append(self._drain_worker_error(p))
                if pending == 0:
                    break

        losers = [p for p in procs if winner is None or p is not winner[0]]
        if losers:
            self._reap_workers(losers, 'sin cabecera a tiempo' if winner is None
                               else 'otro llegó antes')
        if winner is not None:
            return winner[0], winner[1], None
        if errors:
            return None, None, errors[-1]
        return None, None, (f"ningún worker abrió el loopback en "
                            f"{_LOOPBACK_HEADER_TIMEOUT:.0f}s "
                            f"({len(procs)} lanzados; dispositivo de audio ocupado o lento)")

    def _start_wasapi_loopback(self, still=None, resume: bool = False) -> bool:
        """still(): si la grabación que lo pidió sigue siendo la de ahora (ver
        _start_loopback_async). Sin él, basta con que se esté grabando.

        resume=True: el worker anterior murió con la grabación en marcha. El
        fichero y su cola siguen abiertos; solo se rellena el hueco con silencio
        y se engancha el worker nuevo."""
        try:
            proc, header, reason = self._race_loopback_workers(still)
            if proc is None:
                self._loopback_error = reason
                log.warning(f"WASAPI loopback no arrancó: {reason}")
                return False
            sr = struct.unpack('<I', header[:4])[0]
            n_ch = struct.unpack('<I', header[4:8])[0]
            # La cabecera puede tardar hasta _LOOPBACK_HEADER_TIMEOUT: para
            # entonces la grabación pudo parar, o empezar otra. Se comprueba bajo
            # el lock de start()/stop(), que es con el que cambian, y antes de
            # abrir nada sobre sus ficheros.
            with self._start_lock:
                vigente = still() if still else self._recording
                if vigente:
                    self._loop_sr = sr
                    self._loop_ch = n_ch
                    self._loopback_proc = proc
                    if resume and self._loop_q is not None:
                        self._resume_loop_writer()
                    else:
                        self._open_loop_writer()
                    threading.Thread(
                        target=self._wasapi_reader, args=(proc, sr, n_ch, still),
                        daemon=True, name='WasapiLoopbackReader',
                    ).start()
            if not vigente:
                self._drain_worker_error(proc)
                log.info("WASAPI loopback abierto tarde: su grabación ya no es la de ahora; se cierra")
                return False
            log.info(f"WASAPI loopback started ({sr}Hz, {n_ch}ch)")
            return True
        except Exception as e:
            self._loopback_error = str(e)
            log.warning(f"WASAPI loopback failed: {e}")
            return False

    @staticmethod
    def _drain_worker_error(proc) -> str:
        """Termina el worker y devuelve lo que dejó dicho en stderr."""
        # Hay que mirarlo ANTES de terminate(): despues el codigo de salida es
        # siempre 1 y no distingue un fallo del worker de uno matado por lentitud.
        code = proc.poll()
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            _, err = proc.communicate(timeout=3)
        except Exception:
            # terminate() no siempre basta si el worker está bloqueado dentro de
            # PortAudio; sin este kill quedaría un proceso huérfano por cada
            # intento fallido.
            try:
                proc.kill()
                proc.communicate(timeout=2)
            except Exception:
                pass
            return 'el worker de loopback no respondió'
        msg = (err or b'').decode('utf-8', 'replace').strip()
        if msg:
            return msg
        if code is None:
            return (f"el worker no abrió el loopback en {_LOOPBACK_HEADER_TIMEOUT:.0f}s "
                    f"(dispositivo de audio ocupado o lento)")
        return f"el worker de loopback terminó con código {code}"

    def _resume_loop_writer(self):
        """Engancha un worker nuevo al fichero del loopback que ya estaba abierto:
        el hueco desde el ultimo audio se rellena con silencio para no desalinear
        las voces."""
        t_last = self._loop_t_last
        self._reset_resampler()
        if t_last is not None and self._loop_q is not None:
            gap = time.monotonic() - t_last
            pad = int(gap * SAMPLE_RATE)
            if pad > 0:
                self._loop_q.put(np.zeros(pad, dtype=np.float32))
            log.info(f"Audio del sistema reanudado tras {gap:.1f}s sin él; se rellena con silencio")
        self._loop_t_last = time.monotonic()

    def _open_loop_writer(self):
        """El temporal del loopback se guarda ya en mono y al sample rate del
        micrófono, así la mezcla final es una suma directa bloque a bloque.

        Se rellena de entrada con el silencio equivalente a lo que el loopback
        tardó en arrancar. Al abrirse en segundo plano (y más aún con
        reintentos) empieza después del micrófono, y sin esta compensación las
        voces de los demás quedarían adelantadas respecto a lo que dice quien
        graba.
        """
        self._close_stale_loop_writer()
        self._loop_q = queue.Queue()
        self._loop_t_last = time.monotonic()
        self._loop_writer = threading.Thread(
            target=self._writer_loop, args=(self._loop_q, self._tmp_loop, 1, SAMPLE_RATE, 'loop'),
            kwargs={'abort': self._writer_abort}, daemon=True, name='LoopWriter',
        )
        self._loop_writer.start()

        if self._mic_t0 is not None:
            lag = time.monotonic() - self._mic_t0
            pad = int(lag * SAMPLE_RATE)
            if pad > 0:
                log.info(f"Loopback arrancó {lag:.1f}s después del micrófono; se compensa")
                self._loop_q.put(np.zeros(pad, dtype=np.float32))

    def _wasapi_reader(self, proc, sr, n_ch, still=None):
        bytes_per_frame = 4 * n_ch
        chunk_size = int(sr * 0.1) * bytes_per_frame
        try:
            while self._recording or proc.poll() is None:
                data = proc.stdout.read(chunk_size)
                if not data:
                    break
                arr = np.frombuffer(data, dtype=np.float32)
                q = self._loop_q
                if q is not None:
                    q.put(self._to_mic_format(arr, sr, n_ch))
                    self._loop_t_last = time.monotonic()
        except Exception:
            pass
        # El worker se acabo. Si la grabacion sigue y este era el worker en uso,
        # murio por su cuenta (se desconecto la salida, o se cayo): se relanza y
        # el hueco se rellena con silencio. Antes el audio de los demas se perdia
        # ahi hasta el final de la reunion, sin aviso.
        abort = getattr(still, 'abort', None)
        if (abort is not None and still() and proc is self._loopback_proc
                and not abort.is_set()):
            log.warning("El worker de loopback terminó con la grabación en marcha; se relanza")
            self.loopback_active = False
            threading.Thread(target=self._start_loopback_async, args=(abort, True),
                             daemon=True, name='LoopbackResume').start()

    def _reset_resampler(self):
        self._loop_carry = np.zeros(0, dtype=np.float32)
        self._loop_phase = 0.0

    def _reset_dc_filter(self):
        self._dc_x1 = 0.0
        self._dc_y1 = 0.0

    def _dc_block(self, block: np.ndarray, state: list | None = None) -> np.ndarray:
        """Paso-alto de primer orden (DC blocker) sobre un bloque del micrófono.

            y[n] = x[n] - x[n-1] + R * y[n-1]     R = 1 - 2*pi*fc/sr

        Mantiene x[n-1] e y[n-1] entre bloques, así que filtrar por bloques da
        el mismo resultado que filtrar la grabación entera. Se usa un IIR de un
        polo en lugar de un Butterworth de scipy para no añadir una dependencia
        de 30 MB: a 0 Hz elimina por completo, y la voz empieza muy por encima
        del corte.
        """
        r = 1.0 - (2.0 * np.pi * _HIGHPASS_HZ / SAMPLE_RATE)
        out = np.empty(len(block), dtype=np.float32)
        # `state` ([x1, y1]) es el de un writer; sin el, el de la instancia.
        x1, y1 = state if state is not None else (self._dc_x1, self._dc_y1)
        # Bucle explícito: el filtro es recursivo y no se vectoriza. Son 1600
        # muestras por bloque (100 ms), coste despreciable y fuera del callback.
        for i, x in enumerate(block):
            y1 = x - x1 + r * y1
            x1 = x
            out[i] = y1
        if state is not None:
            state[0], state[1] = float(x1), float(y1)
        else:
            self._dc_x1, self._dc_y1 = float(x1), float(y1)
        return out

    def _to_mic_format(self, arr: np.ndarray, sr: int, n_ch: int) -> np.ndarray:
        """Pasa un bloque interleaved a mono y al sample rate del micrófono.

        Se convierte bloque a bloque (100 ms) y no sobre la grabación completa:
        hacerlo al final obligaba a construir los índices de np.interp para
        todas las muestras a la vez, y np.arange/np.linspace devuelven float64,
        con lo que una hora de loopback estéreo pedía cientos de MB solo en
        índices.

        Para que encadenar bloques dé el mismo audio que interpolar la
        grabación entera, se arrastran al bloque siguiente la cola no consumida
        y la fase fraccionaria. Sin ese arrastre cada bloque se resamplea
        aislado y aparece un error audible en las costuras.
        """
        if n_ch > 1:
            rem = len(arr) % n_ch
            if rem:
                arr = arr[:-rem]
            if not len(arr):
                return np.zeros(0, dtype=np.float32)
            arr = arr.reshape(-1, n_ch).mean(axis=1).astype(np.float32)
        if sr == SAMPLE_RATE:
            return np.asarray(arr, dtype=np.float32)

        buf = np.concatenate((self._loop_carry, arr)) if len(self._loop_carry) else arr
        step = sr / SAMPLE_RATE
        if len(buf) < 2:
            self._loop_carry = np.asarray(buf, dtype=np.float32)
            return np.zeros(0, dtype=np.float32)

        n = int(np.floor((len(buf) - 1 - self._loop_phase) / step)) + 1
        if n <= 0:
            self._loop_carry = np.asarray(buf, dtype=np.float32)
            return np.zeros(0, dtype=np.float32)

        idx = self._loop_phase + np.arange(n) * step
        out = np.interp(idx, np.arange(len(buf)), buf).astype(np.float32)

        next_pos = self._loop_phase + n * step
        keep_from = min(int(np.floor(next_pos)), len(buf) - 1)
        self._loop_carry = np.asarray(buf[keep_from:], dtype=np.float32)
        self._loop_phase = next_pos - keep_from
        return out

    def _start_stereo_mix_loopback(self, still=None) -> bool:
        keywords = ('stereo mix', 'mezcla estereo', 'what u hear', 'wave out mix', 'loopback')
        try:
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                name = dev['name'].lower()
                if dev['max_input_channels'] > 0 and any(k in name for k in keywords):
                    def cb(indata, frames, time_info, status):
                        q = self._loop_q
                        if q is not None:
                            q.put(indata.copy().reshape(-1))

                    # El fichero se abre DESPUES de construir el InputStream, no
                    # antes. "Invalid device" salta en el constructor, y abrir el
                    # .part primero dejaba un temporal huerfano cada vez que este
                    # equipo (sin Stereo Mix) pasaba por aqui. Si ademas la
                    # grabacion ya termino, el reintento escribia un .part nuevo
                    # despues de que la limpieza hubiera corrido: 824 KB de ceros
                    # el 22/09/2026.
                    stream = sd.InputStream(
                        device=i, samplerate=SAMPLE_RATE, channels=1,
                        dtype='float32', blocksize=int(SAMPLE_RATE * 0.1), callback=cb,
                    )
                    # Bajo el lock de start()/stop(), como en el WASAPI, y en una
                    # variable local hasta saberlo: si la grabación ya no es la
                    # que lo pidió, no se toca nada suyo (ni el stream, ni su
                    # sample rate, ni su fichero).
                    with self._start_lock:
                        if not (still() if still else self._recording):
                            stream.close()
                            return False
                        self._loop_sr = SAMPLE_RATE
                        self._loop_ch = 1
                        self._stereo_stream = stream
                        self._open_loop_writer()
                        stream.start()
                    log.info(f"Stereo Mix loopback: {dev['name']}")
                    return True
        except Exception as e:
            self._loopback_error = f"Stereo Mix: {e}"
            log.warning(f"Stereo Mix failed: {e}")
            self._abortar_loop_writer()
            return False
        if not self._loopback_error:
            self._loopback_error = 'no hay dispositivo Stereo Mix ni loopback WASAPI'
        return False

    # ── cierre ───────────────────────────────────────────────────────────────

    def _close_inputs(self):
        self._mic_watch_stop.set()
        with self._mic_swap_lock:
            stream, self._stream = self._stream, None
        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception as e:
                log.warning(f"Error cerrando el micrófono: {e}")

        if self._stereo_stream:
            try:
                self._stereo_stream.stop()
                self._stereo_stream.close()
            except Exception:
                pass
            self._stereo_stream = None

        if self._loopback_proc:
            try:
                self._loopback_proc.terminate()
                self._loopback_proc.wait(timeout=3)
            except Exception:
                pass
            self._loopback_proc = None

    def _close_writers(self):
        """Se llama con los productores ya parados: encola el centinela y espera
        a que cada writer vacíe su cola, para no perder los últimos bloques."""
        for th in self._detach_writers():
            th.join(timeout=30)

    def _detach_writers(self) -> list[threading.Thread]:
        """Encola el centinela de cada writer y los suelta de la grabacion. Los
        devuelve para que los espere quien vaya a leer sus .part."""
        for q in (self._mic_q, self._loop_q):
            if q is not None:
                q.put(None)
        writers = [th for th in (self._mic_writer, self._loop_writer) if th is not None]
        self._mic_q = None
        self._loop_q = None
        self._mic_writer = None
        self._loop_writer = None
        return writers

    def _close_stale_loop_writer(self):
        """Cierra el writer del loopback de un intento anterior, si quedó uno.

        _start_stereo_mix_loopback abre el fichero antes de saber si el
        InputStream funciona, así que un intento fallido deja un writer vivo.
        Cuando el reintento abre el segundo writer sobre el mismo .part, el
        primero se queda esperando para siempre en q.get() con el fichero
        abierto, y en Windows un fichero con un handle vivo no se puede borrar:
        quedan ~100 MB huérfanos por grabación. Pasó el 16/09 con la reunión de
        las 16:40 (Stereo Mix falló, el reintento de WASAPI funcionó).
        """
        q, th = self._loop_q, self._loop_writer
        if q is None and th is None:
            return
        self._loop_q = None
        self._loop_writer = None
        if q is not None:
            q.put(None)
        if th is not None:
            th.join(timeout=5)

    def _abortar_loop_writer(self):
        """Cierra el writer del loopback y borra su temporal.

        Se llama cuando un intento falla despues de haber abierto el fichero.
        _close_stale_loop_writer solo cierra el hilo; si nadie borra el .part
        se queda en recordings/ para siempre, porque _discard_temp corre en el
        finally de la parada y el reintento puede llegar despues.
        """
        self._close_stale_loop_writer()
        try:
            if self._tmp_loop and self._tmp_loop.exists():
                self._tmp_loop.unlink()
        except OSError as e:
            log.warning(f"No se pudo borrar el temporal del loopback: {e}")

    def _discard_temp(self, *paths: Path | None):
        """Borra los .part de la grabacion actual, o los que se le pasen."""
        for p in (paths or (self._tmp_mic, self._tmp_loop)):
            if p:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    # ── mezcla y guardado ────────────────────────────────────────────────────

    def _process_and_save(self, mic_path: Path, loop_path: Path | None, out_path: Path,
                          writers=()):
        """Espera a que los writers vuelquen todo y guarda el WAV.

        Recibe sus rutas: con dos reuniones seguidas, self._tmp_* ya son las de la
        siguiente, y borrarlas al acabar perdia la reunion que se estaba grabando.
        Los .part solo se borran con el WAV guardado: si no se puede escribir
        (disco lleno), se reintenta cada _SAVE_RETRY_S. Antes el finally los
        borraba igual y se perdia la reunion entera.
        """
        key = f'save:{out_path.name}'
        try:
            for th in writers:
                th.join()
            warned = False
            while True:
                try:
                    saved, keep_loop = self._save_wav(mic_path, loop_path, out_path)
                    break
                except Exception as e:
                    self._remove_outputs(out_path)
                    if not _is_retryable(e):
                        log.error(f"No se pudo guardar {out_path.name}: {e}. Los temporales se "
                                  f"conservan y se reintentara al arrancar Noted", exc_info=True)
                        self._clear_failing(key)
                        self._fire(self.on_save_failed, str(e), 'SaveFailed')
                        return
                    if not warned:
                        warned = True
                        log.error(f"Error guardando WAV: {e}; los temporales se conservan y "
                                  f"se reintenta cada {_SAVE_RETRY_S:.0f} s", exc_info=True)
                        self._set_failing(key, str(e))
                    time.sleep(_SAVE_RETRY_S)
            if warned:
                log.warning(f"WAV guardado al reintentar: {out_path.name}")
                self._set_failing(key, None)
            self._discard_temp(mic_path, None if keep_loop else loop_path)
            if saved and self.on_recording_stopped:
                threading.Thread(
                    target=self.on_recording_stopped, args=(out_path,), daemon=True,
                ).start()
        except Exception as e:
            log.error(f"Error guardando WAV: {e}", exc_info=True)
        finally:
            with self._saves_lock:
                self._saving_stems.discard(out_path.stem)
                if not self._saving_stems:
                    self._save_event.set()
            self._remove_sentinel_if_idle()

    @staticmethod
    def _remove_outputs(out_path: Path) -> None:
        """Lo que dejo un guardado fallido: un WAV a medias se procesaria cortado."""
        for p in (out_path, out_path.with_suffix('.mic.wav'), out_path.with_suffix('.loop.wav')):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    def recover_orphan_parts(self, directory: Path) -> list[Path]:
        """Convierte en WAV los .part que dejo un daemon cerrado antes de
        guardarlos (por ejemplo, reiniciado mientras esperaba sitio en el disco).
        Hasta el 26/10/2026 nadie los convertia y se borraban a los 15 dias.

        Unos .part sin su WAV en processed/ son un guardado sin terminar: los
        .part solo se borran despues del WAV, asi que se rehace aunque haya uno."""
        recovered = []
        busy = set(self._saving_stems)
        if self._recording and self._output_path is not None:
            busy.add(self._output_path.stem)
        for mic in sorted(directory.glob('*.mic.part')):
            stem = mic.name[:-len('.mic.part')]
            loop = directory / f'{stem}.loop.part'
            out = directory / f'{stem}.wav'
            if stem in busy:
                continue
            marker = directory / f'{stem}.discarded'
            if marker.exists():
                # Descartada mientras se grababa (tray_app._cancel_recording) y no
                # se pudieron borrar sus .part: el audio no se recupera, se borra.
                # Recuperarlo la transcribiria y la mandaria a Claude.
                self._discard_temp(mic, loop)
                if not mic.exists() and not loop.exists():
                    marker.unlink(missing_ok=True)
                log.info(f"Temporales de una grabacion descartada, borrados: {stem}")
                continue
            if (directory / 'processed' / out.name).exists():
                continue
            try:
                saved, keep_loop = self._save_wav(mic, loop if loop.exists() else None, out)
                if saved:
                    self._discard_temp(mic, None if keep_loop else loop)
                    log.warning(f"Grabacion recuperada de sus temporales: {out.name}")
                    recovered.append(out)
            except Exception as e:
                self._remove_outputs(out)
                log.error(f"No se pudo recuperar {mic.name}: {e}", exc_info=True)
        return recovered

    def _save_wav(self, mic_path: Path, loop_path: Path | None,
                  out_path: Path) -> tuple[bool, bool]:
        """Mezcla los .part en el WAV final y guarda las pistas de cada voz.

        Devuelve (guardado, conservar el .part del sistema). (False, False) si no
        hay audio del micro; lanza si no se pudo escribir o leer el del micro. Un
        .part del sistema ilegible no impide guardar el micro, pero se conserva."""
        mic_frames = self._frames(mic_path)
        if not mic_frames:
            log.warning("No mic data recorded")
            return False, False

        mic_peak = self._peak(mic_path)
        mic_gain = (0.8 / mic_peak) if mic_peak > 0 else 1.0

        keep_loop = False
        try:
            loop_frames = self._frames(loop_path)
        except Exception as e:
            log.error(f"El audio del sistema ({loop_path.name}) no se puede leer: {e}; "
                      f"se guarda solo el micro y el .part se conserva")
            loop_frames, keep_loop = 0, True
        use_loop = False
        if loop_frames:
            loop_peak = self._peak(loop_path)
            if loop_peak < 0.02 or loop_frames < mic_frames * 0.1:
                log.info("Loopback descartado (peak bajo o duración insuficiente)")
            else:
                use_loop = True

        if use_loop:
            # La mezcla dura lo que el stream MÁS largo, rellenando el otro
            # con silencio. Truncar al más corto (min) perdía el final de la
            # reunión: WASAPI no entrega loopback mientras nadie habla, así
            # que el fichero del sistema se queda corto y con `min` se
            # tiraban los minutos de micrófono que venían después.
            total = max(mic_frames, loop_frames)
            mix_peak = self._mix_peak(mic_path, loop_path, total, mic_gain)
            gain = (0.9 / mix_peak) if mix_peak > 1.0 else 1.0
            self._write_mix(out_path, mic_path, loop_path, total, mic_gain, gain)
        else:
            gain = 1.0
            self._write_mix(out_path, mic_path, None, mic_frames, mic_gain, gain)

        log.info(f"WAV guardado: {out_path}")

        # Las pistas de cada voz, para que el transcriber sepa quien dijo que.
        # Antes de on_recording_stopped (lanza la transcripcion) y de borrar los
        # .part. Si no caben, el guardado entero se reintenta: el 26/10/2026 la
        # reunion de las 15:33 se quedo sin ellas con el disco lleno.
        self._write_mono(
            out_path.with_suffix('.mic.wav'), mic_path, mic_frames, mic_gain * gain,
        )
        if use_loop:
            loop_norm = (0.8 / loop_peak) if loop_peak > 0 else 1.0
            self._write_mono(
                out_path.with_suffix('.loop.wav'), loop_path, loop_frames, loop_norm * gain,
            )
        log.info("Speaker tracks guardados para diarización")
        return True, keep_loop

    @staticmethod
    def _frames(path: Path | None) -> int:
        """0 si no existe o esta vacio. Si existe y no se puede leer, lanza:
        darlo por vacio borraba un .part que nunca se habia guardado."""
        if not path or not path.exists() or path.stat().st_size == 0:
            return 0
        return sf.info(str(path)).frames

    @staticmethod
    def _blocks(path: Path, limit: int | None = None):
        with sf.SoundFile(str(path)) as f:
            remaining = f.frames if limit is None else min(limit, f.frames)
            while remaining > 0:
                block = f.read(min(_BLOCK, remaining), dtype='float32', always_2d=False)
                if not len(block):
                    break
                remaining -= len(block)
                yield block

    @classmethod
    def _peak(cls, path: Path) -> float:
        peak = 0.0
        for block in cls._blocks(path):
            peak = max(peak, float(np.abs(block).max()))
        return peak

    @classmethod
    def _aligned_blocks(cls, mic_path: Path, loop_path: Path | None, total: int):
        """Bloques alineados de micrófono y sistema hasta `total`, rellenando
        con silencio el stream que se agote antes.

        Es lo que permite que la mezcla dure lo que el más largo: el fichero del
        loopback se queda corto siempre que nadie habla (WASAPI no entrega nada
        en silencio), y con un `zip` la grabación se cortaba ahí.
        """
        mic_f = sf.SoundFile(str(mic_path))
        loop_f = sf.SoundFile(str(loop_path)) if loop_path else None
        try:
            done = 0
            while done < total:
                n = min(_BLOCK, total - done)
                mic_block = np.zeros(n, dtype=np.float32)
                got = mic_f.read(n, dtype='float32', always_2d=False)
                if len(got):
                    mic_block[:len(got)] = got
                loop_block = np.zeros(n, dtype=np.float32)
                if loop_f is not None:
                    got = loop_f.read(n, dtype='float32', always_2d=False)
                    if len(got):
                        loop_block[:len(got)] = got
                yield mic_block, loop_block
                done += n
        finally:
            mic_f.close()
            if loop_f is not None:
                loop_f.close()

    @classmethod
    def _mix_peak(cls, mic_path: Path, loop_path: Path, total: int, mic_gain: float) -> float:
        peak = 0.0
        for mic_block, loop_block in cls._aligned_blocks(mic_path, loop_path, total):
            peak = max(peak, float(np.abs(mic_block * mic_gain + loop_block).max()))
        return peak

    @classmethod
    def _write_mix(cls, out_path: Path, mic_path: Path, loop_path: Path | None,
                   total: int, mic_gain: float, gain: float):
        with sf.SoundFile(
            str(out_path), mode='w', samplerate=SAMPLE_RATE,
            channels=1, format='WAV', subtype='PCM_16',
        ) as out:
            for mic_block, loop_block in cls._aligned_blocks(mic_path, loop_path, total):
                out.write(((mic_block * mic_gain + loop_block) * gain).astype(np.float32))

    @classmethod
    def _write_mono(cls, out_path: Path, src_path: Path, frames: int, gain: float):
        with sf.SoundFile(
            str(out_path), mode='w', samplerate=SAMPLE_RATE,
            channels=1, format='WAV', subtype='PCM_16',
        ) as out:
            for block in cls._blocks(src_path, limit=frames):
                out.write((block * gain).astype(np.float32))
