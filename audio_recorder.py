import logging
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

# Corte del paso-alto aplicado al micrófono. El micro introduce un offset DC y
# rumble por debajo de esta frecuencia que llega a dominar el espectro; como
# después se normaliza el pico a 0.8, ese rumble se come el margen dinámico de
# las voces y el filtro de voz de Whisper acaba descartando tramos con habla.
# La voz humana no tiene contenido útil por debajo de 80 Hz.
_HIGHPASS_HZ = 80.0


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
        self._loopback_proc: subprocess.Popen | None = None
        self._stream: sd.InputStream | None = None
        self._stereo_stream: sd.InputStream | None = None
        # Serializa start(): hay dos llamadores independientes en el mismo
        # proceso (el popup de detección en main.py y "Grabar ahora" en
        # tray_app.py) y sin esto pueden entrar los dos a la vez.
        self._start_lock = threading.Lock()

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

        # True si se está capturando el audio del sistema. Cuando es False la
        # grabación solo contiene tu micrófono, y con auriculares eso significa
        # que no queda ni rastro de lo que dicen los demás.
        self.loopback_active = False

        self.on_recording_stopped = None     # callable(wav_path)
        self.on_loopback_unavailable = None  # callable(reason) — grabando solo micrófono
        self.on_write_error = None           # callable(reason) — error al escribir a disco

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self, output_path: Path):
        # Reservar el flag ANTES de cualquier espera. Con la comprobación y la
        # asignación separadas por el wait() de abajo, dos llamadas casi
        # simultáneas pasaban las dos el "if" y abrían cada una su InputStream y
        # su worker WASAPI; el segundo start() sobreescribía self._stream y
        # dejaba el primero huérfano pero activo, y al recolectarlo el proceso
        # moría con ACCESS_VIOLATION dentro del callback de audio.
        with self._start_lock:
            if self._recording:
                return
            self._recording = True
        try:
            self._begin(output_path)
        except Exception:
            self._recording = False
            self._close_inputs()
            self._close_writers()
            self._save_event.set()
            raise

    def _begin(self, output_path: Path):
        # Si hay un guardado en curso del ciclo anterior, esperar a que termine
        # antes de reutilizar los temporales, para no perder datos.
        if not self._save_event.is_set():
            self._save_event.wait(timeout=30)
        self._save_event.clear()
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

        self._mic_q = queue.Queue()
        self._mic_writer = threading.Thread(
            target=self._writer_loop,
            args=(self._mic_q, self._tmp_mic, 1, SAMPLE_RATE, 'mic'),
            kwargs={'highpass': True},
            daemon=True, name='MicWriter',
        )
        self._mic_writer.start()

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            blocksize=int(SAMPLE_RATE * 0.1),
            callback=self._mic_cb,
        )
        self._stream.start()
        self._mic_t0 = time.monotonic()

        # _recording ya se puso en start(), bajo el lock.

        # El loopback se abre en segundo plano para que start() no espere por él.
        # El retraso que eso introduce se compensa con silencio al abrir su
        # fichero (ver _open_loop_writer), asi que no desalinea las voces.
        self.loopback_active = False
        threading.Thread(target=self._start_loopback_async, daemon=True,
                         name='LoopbackInit').start()

        log.info(f"Recording started → {output_path}")

    def stop(self) -> Path | None:
        if not self._recording:
            return None
        self._recording = False
        self._close_inputs()
        self._close_writers()

        t = threading.Thread(
            target=self._process_and_save, daemon=True, name='AudioProcessor',
        )
        t.start()
        return self._output_path

    def cancel(self) -> None:
        """Para la grabación y descarta el audio sin guardar ni procesar."""
        if not self._recording:
            return
        self._recording = False
        self._close_inputs()
        self._close_writers()
        self._discard_temp()
        self._save_event.set()
        log.info("Recording cancelled — audio discarded")

    def wait_for_save(self, timeout: int = 60) -> bool:
        return self._save_event.wait(timeout=timeout)

    # ── captura ──────────────────────────────────────────────────────────────

    def _mic_cb(self, indata, frames, time_info, status):
        # Solo encolar: PortAudio invoca este callback en un hilo de tiempo real
        # y escribir a disco aquí produciría cortes en el audio.
        q = self._mic_q
        if q is not None:
            q.put(indata.copy().reshape(-1))

    def _writer_loop(self, q: queue.Queue, path: Path, channels: int, samplerate: int,
                     label: str, highpass: bool = False):
        try:
            with sf.SoundFile(
                str(path), mode='w', samplerate=samplerate,
                channels=channels, format='WAV', subtype='FLOAT',
            ) as f:
                while True:
                    block = q.get()
                    if block is None:
                        break
                    if not len(block):
                        continue
                    if highpass:
                        block = self._dc_block(block)
                    f.write(block)
                    # Flush en cada bloque: mantiene el header al día (lo
                    # necesita on_chunk) y deja el audio en disco si el proceso
                    # muere a mitad de la reunión.
                    f.flush()
        except OSError as e:
            log.error(f"Writer {label} error de disco: {e}", exc_info=True)
            if self.on_write_error:
                threading.Thread(
                    target=self.on_write_error, args=(str(e),),
                    daemon=True, name='WriteErrorWarning',
                ).start()
        except Exception as e:
            log.error(f"Writer {label} falló: {e}", exc_info=True)

    def _start_loopback_async(self):
        """Abre el loopback con reintentos, fuera del hilo de start().

        Al entrar en una llamada, Teams y Windows reconfiguran los endpoints y
        WASAPI rechaza la apertura durante unos segundos; sin reintento ese
        fallo momentáneo condenaba la reunión entera a grabarse sin las voces
        de los demás.
        """
        reason = 'no se intentó'
        for attempt in range(1, _LOOPBACK_ATTEMPTS + 1):
            if not self._recording:
                return
            self._loopback_error = None
            if self._start_wasapi_loopback() or self._start_stereo_mix_loopback():
                self.loopback_active = True
                return
            reason = self._loopback_error or 'sin dispositivo loopback disponible'
            if attempt < _LOOPBACK_ATTEMPTS:
                log.warning(
                    f"Loopback no disponible (intento {attempt}/{_LOOPBACK_ATTEMPTS}): "
                    f"{reason} — reintentando en {_LOOPBACK_RETRY_WAIT}s"
                )
                time.sleep(_LOOPBACK_RETRY_WAIT)

        log.error(
            f"SIN audio del sistema tras {_LOOPBACK_ATTEMPTS} intentos ({reason}). "
            f"La grabación tendrá SOLO tu micrófono: si usas auriculares, no se "
            f"capturará nada de lo que digan los demás."
        )
        if self.on_loopback_unavailable:
            threading.Thread(
                target=self.on_loopback_unavailable, args=(reason,),
                daemon=True, name='LoopbackWarning',
            ).start()

    def _start_wasapi_loopback(self) -> bool:
        try:
            # stderr capturado, no descartado: el worker explica ahí por qué no
            # pudo abrir el loopback y antes ese motivo se perdía, dejando el
            # fallo invisible en el log.
            proc = subprocess.Popen(
                [sys.executable, _LOOPBACK_WORKER],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # Leer header con timeout para evitar deadlock si el worker se cuelga
            header_result = [b'']
            def _read_header():
                header_result[0] = proc.stdout.read(8)
            t = threading.Thread(target=_read_header, daemon=True)
            t.start()
            t.join(timeout=5.0)
            header = header_result[0]
            if not header or len(header) < 8:
                self._loopback_error = self._drain_worker_error(proc)
                log.warning(f"WASAPI loopback no arrancó: {self._loopback_error}")
                return False
            sr = struct.unpack('<I', header[:4])[0]
            n_ch = struct.unpack('<I', header[4:8])[0]
            self._loop_sr = sr
            self._loop_ch = n_ch
            self._loopback_proc = proc
            self._open_loop_writer()
            t = threading.Thread(
                target=self._wasapi_reader, args=(proc, sr, n_ch),
                daemon=True, name='WasapiLoopbackReader',
            )
            t.start()
            log.info(f"WASAPI loopback started ({sr}Hz, {n_ch}ch)")
            return True
        except Exception as e:
            self._loopback_error = str(e)
            log.warning(f"WASAPI loopback failed: {e}")
            return False

    @staticmethod
    def _drain_worker_error(proc) -> str:
        """Termina el worker y devuelve lo que dejó dicho en stderr."""
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            _, err = proc.communicate(timeout=3)
        except Exception:
            # terminate() no siempre basta si el worker está bloqueado dentro de
            # PortAudio; sin este kill quedaría un proceso huérfano por cada
            # intento fallido, y son 3 por grabación.
            try:
                proc.kill()
                proc.communicate(timeout=2)
            except Exception:
                pass
            return 'el worker de loopback no respondió'
        msg = (err or b'').decode('utf-8', 'replace').strip()
        return msg or f"el worker de loopback terminó con código {proc.returncode}"

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
        self._loop_writer = threading.Thread(
            target=self._writer_loop, args=(self._loop_q, self._tmp_loop, 1, SAMPLE_RATE, 'loop'),
            daemon=True, name='LoopWriter',
        )
        self._loop_writer.start()

        if self._mic_t0 is not None:
            lag = time.monotonic() - self._mic_t0
            pad = int(lag * SAMPLE_RATE)
            if pad > 0:
                log.info(f"Loopback arrancó {lag:.1f}s después del micrófono; se compensa")
                self._loop_q.put(np.zeros(pad, dtype=np.float32))

    def _wasapi_reader(self, proc, sr, n_ch):
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
        except Exception:
            pass

    def _reset_resampler(self):
        self._loop_carry = np.zeros(0, dtype=np.float32)
        self._loop_phase = 0.0

    def _reset_dc_filter(self):
        self._dc_x1 = 0.0
        self._dc_y1 = 0.0

    def _dc_block(self, block: np.ndarray) -> np.ndarray:
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
        x1, y1 = self._dc_x1, self._dc_y1
        # Bucle explícito: el filtro es recursivo y no se vectoriza. Son 1600
        # muestras por bloque (100 ms), coste despreciable y fuera del callback.
        for i, x in enumerate(block):
            y1 = x - x1 + r * y1
            x1 = x
            out[i] = y1
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

    def _start_stereo_mix_loopback(self) -> bool:
        keywords = ('stereo mix', 'mezcla estereo', 'what u hear', 'wave out mix', 'loopback')
        try:
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                name = dev['name'].lower()
                if dev['max_input_channels'] > 0 and any(k in name for k in keywords):
                    self._loop_sr = SAMPLE_RATE
                    self._loop_ch = 1
                    self._open_loop_writer()
                    def cb(indata, frames, time_info, status):
                        q = self._loop_q
                        if q is not None:
                            q.put(indata.copy().reshape(-1))
                    self._stereo_stream = sd.InputStream(
                        device=i, samplerate=SAMPLE_RATE, channels=1,
                        dtype='float32', blocksize=int(SAMPLE_RATE * 0.1), callback=cb,
                    )
                    self._stereo_stream.start()
                    log.info(f"Stereo Mix loopback: {dev['name']}")
                    return True
        except Exception as e:
            self._loopback_error = f"Stereo Mix: {e}"
            log.warning(f"Stereo Mix failed: {e}")
            return False
        if not self._loopback_error:
            self._loopback_error = 'no hay dispositivo Stereo Mix ni loopback WASAPI'
        return False

    # ── cierre ───────────────────────────────────────────────────────────────

    def _close_inputs(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

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
        for q in (self._mic_q, self._loop_q):
            if q is not None:
                q.put(None)
        for th in (self._mic_writer, self._loop_writer):
            if th is not None:
                th.join(timeout=30)
        self._mic_q = None
        self._loop_q = None
        self._mic_writer = None
        self._loop_writer = None

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

    def _discard_temp(self):
        for p in (self._tmp_mic, self._tmp_loop):
            if p:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    # ── mezcla y guardado ────────────────────────────────────────────────────

    def _process_and_save(self):
        try:
            mic_path, loop_path, out_path = self._tmp_mic, self._tmp_loop, self._output_path

            mic_frames = self._frames(mic_path)
            if not mic_frames:
                log.warning("No mic data recorded")
                return

            mic_peak = self._peak(mic_path)
            mic_gain = (0.8 / mic_peak) if mic_peak > 0 else 1.0

            loop_frames = self._frames(loop_path)
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

            # Save normalized speaker tracks so the transcriber can label who said what.
            # Must happen before on_recording_stopped (which triggers transcription in a thread)
            # and before _discard_temp() in the finally block.
            try:
                self._write_mono(
                    out_path.with_suffix('.mic.wav'), mic_path, mic_frames, mic_gain * gain,
                )
                if use_loop:
                    loop_norm = (0.8 / loop_peak) if loop_peak > 0 else 1.0
                    self._write_mono(
                        out_path.with_suffix('.loop.wav'), loop_path, loop_frames, loop_norm * gain,
                    )
                log.info("Speaker tracks guardados para diarización")
            except Exception as e:
                log.warning(f"Speaker tracks no guardados: {e}")

            if self.on_recording_stopped:
                threading.Thread(
                    target=self.on_recording_stopped, args=(out_path,), daemon=True,
                ).start()
        except Exception as e:
            log.error(f"Error guardando WAV: {e}", exc_info=True)
        finally:
            self._discard_temp()
            self._save_event.set()

    @staticmethod
    def _frames(path: Path | None) -> int:
        try:
            if path and path.exists():
                return sf.info(str(path)).frames
        except Exception:
            pass
        return 0

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

