"""
Subprocess aislado para captura WASAPI loopback.
Protocolo stdout: 4 bytes samplerate (uint32 LE) + 4 bytes channels (uint32 LE) + stream float32 raw.

El header se escribe SOLO despues de abrir un stream con exito: si se escribiera
antes, el padre daria el loopback por arrancado y se quedaria esperando un audio
que nunca llega, grabando solo el microfono sin avisar.

Todo fallo se explica por stderr, y cada paso deja una linea con su tiempo
("[ 0.6s] PortAudio listo"). El 29/10/2026 el worker se colgo 90 s al entrar en
una llamada y el log solo decia "no abrio en 15s": sin saber en que paso, no
habia forma de arreglarlo.

MULTI-SALIDA
Windows solo marca UNA salida como "por defecto", pero Teams puede sonar por
otra (altavoz de conferencias, auriculares Bluetooth...). Se abre el loopback de
TODAS las salidas y se mezclan en un unico flujo mono a 48 kHz.

CADA SALIDA SE ABRE EN SU HILO. Desde el 22/10 se abrian una tras otra y la
mezcla no empezaba hasta tener todas: una salida que tarda (el altavoz del
portatil mientras Teams reconfigura el audio) retenia la cabecera, y con ella
el audio de los demas. Ahora la cabecera sale con la PRIMERA salida que abre y
las demas se unen a la mezcla cuando estan listas.

El worker termina cuando ya no queda ninguna salida activa (p. ej. se desconecto
el dispositivo): el padre lo relanza y se vuelve a enumerar.

CAMBIOS DE SALIDA A MITAD DE LLAMADA. PortAudio solo ve las salidas que
existian al iniciarse, y un stream de loopback puede quedarse abierto pero mudo
cuando Windows reconfigura el audio. Dos casos reales:
- se conectan unos auriculares y Teams pasa a sonar por ellos: esa salida no
  estaba en la mezcla;
- desaparece una salida (se desconecta o se apaga un monitor) o cambia la
  salida por defecto: la captura de las demas siguio "activa" pero solo grabo
  silencio desde el segundo 6 hasta el final de una reunion de 22 min.
En los dos, la pista de los demas quedaba en silencio sin ningun error. Ahora
un vigilante mira cada RESCAN_S las salidas de Windows (pycaw, barato): las
activas y las que estan por defecto (consola y comunicaciones). Si cambia algo,
el worker termina limpio y el padre lo relanza (rellenando el hueco con
silencio, ver audio_recorder._wasapi_reader); el nuevo vuelve a abrir todas.
"""
import os
import struct
import sys
import threading
import time

_T0 = time.monotonic()      # antes de numpy: su carga tambien cuenta

import numpy as np  # noqa: E402

MIX_SR = 48000          # frecuencia del flujo mezclado
MIX_TICK = 0.1          # segundos de audio emitidos por iteracion
MAX_BUFFER_S = 1.0      # tope por dispositivo antes de descartar lo mas antiguo
RESCAN_S = 3.0          # cada cuanto se miran las salidas activas de Windows


_TRACES = [0]


def trace(msg: str):
    """Una linea por paso, con su tiempo. El padre solo lee stderr al terminar
    el worker, y una tuberia sin lector se llena: por eso hay tope."""
    if _TRACES[0] >= 40:
        return
    _TRACES[0] += 1
    try:
        sys.stderr.write(f"[{time.monotonic() - _T0:5.1f}s] {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


def fail(msg: str):
    trace(msg)
    # os._exit y no sys.exit: con PortAudio abierto, el cierre normal del
    # interprete se quedaba colgado y el worker no terminaba nunca.
    os._exit(1)


class Resampler:
    """Pasa bloques interleaved a mono y a `out_sr`, conservando cola y fase
    entre bloques para que encadenarlos no deje costuras audibles."""

    def __init__(self, in_sr: int, n_ch: int, out_sr: int = MIX_SR):
        self.in_sr = in_sr
        self.n_ch = max(1, n_ch)
        self.out_sr = out_sr
        self._carry = np.zeros(0, dtype=np.float32)
        self._phase = 0.0

    def process(self, arr: np.ndarray) -> np.ndarray:
        if self.n_ch > 1:
            rem = len(arr) % self.n_ch
            if rem:
                arr = arr[:-rem]
            if not len(arr):
                return np.zeros(0, dtype=np.float32)
            arr = arr.reshape(-1, self.n_ch).mean(axis=1).astype(np.float32)
        if self.in_sr == self.out_sr:
            return np.asarray(arr, dtype=np.float32)

        buf = np.concatenate((self._carry, arr)) if len(self._carry) else arr
        if len(buf) < 2:
            self._carry = np.asarray(buf, dtype=np.float32)
            return np.zeros(0, dtype=np.float32)
        step = self.in_sr / self.out_sr
        n = int(np.floor((len(buf) - 1 - self._phase) / step)) + 1
        if n <= 0:
            self._carry = np.asarray(buf, dtype=np.float32)
            return np.zeros(0, dtype=np.float32)

        idx = self._phase + np.arange(n) * step
        out = np.interp(idx, np.arange(len(buf)), buf).astype(np.float32)
        next_pos = self._phase + n * step
        keep_from = min(int(np.floor(next_pos)), len(buf) - 1)
        self._carry = np.asarray(buf[keep_from:], dtype=np.float32)
        self._phase = next_pos - keep_from
        return out


def mix_blocks(blocks, n: int) -> np.ndarray:
    """Suma `blocks` (arrays mono) en un bloque de `n` muestras. Los bloques
    cortos se rellenan con silencio y la suma se limita a [-1, 1] para no
    saturar cuando dos salidas suenan a la vez."""
    out = np.zeros(n, dtype=np.float32)
    for b in blocks:
        m = min(len(b), n)
        if m:
            out[:m] += b[:m]
    np.clip(out, -1.0, 1.0, out=out)
    return out


class DeviceBuffer:
    """Cola mono a MIX_SR alimentada por el callback de un dispositivo."""

    def __init__(self, name: str, in_sr: int, n_ch: int):
        self.name = name
        self.resampler = Resampler(in_sr, n_ch)
        self._lock = threading.Lock()
        self._data = np.zeros(0, dtype=np.float32)

    def push(self, raw: bytes):
        block = self.resampler.process(np.frombuffer(raw, dtype=np.float32))
        if not len(block):
            return
        with self._lock:
            self._data = np.concatenate((self._data, block))
            cap = int(MAX_BUFFER_S * MIX_SR)
            if len(self._data) > cap:
                self._data = self._data[-cap:]

    def pop(self, n: int) -> np.ndarray:
        with self._lock:
            out, self._data = self._data[:n], self._data[n:]
        return out


def _list_candidates(pa, pyaudio):
    """Loopbacks ordenados: el de la salida por defecto primero."""
    try:
        wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError as e:
        pa.terminate()
        fail(f"WASAPI no disponible en este equipo: {e}")

    try:
        default_speakers = pa.get_device_info_by_index(wasapi_info['defaultOutputDevice'])
    except Exception as e:
        pa.terminate()
        fail(f"no se puede leer el dispositivo de salida por defecto: {e}")

    candidates = []
    if default_speakers.get('isLoopbackDevice', False):
        candidates.append(default_speakers)
    else:
        others = []
        for i in range(pa.get_device_count()):
            try:
                info = pa.get_device_info_by_index(i)
            except Exception:
                continue
            if not info.get('isLoopbackDevice', False):
                continue
            if default_speakers['name'] in info['name']:
                candidates.append(info)
            else:
                others.append(info)
        candidates.extend(others)

    if not candidates:
        pa.terminate()
        fail(f"no hay ningun dispositivo loopback (salida por defecto: "
             f"'{default_speakers['name']}')")
    return candidates


def _active_outputs():
    """Estado de las salidas de audio segun Windows (MMDevice): (ids de las
    activas, id por defecto de consola, id por defecto de comunicaciones), o
    None si no se puede saber. Llamar desde una hebra con COM iniciado."""
    try:
        from pycaw.pycaw import AudioUtilities
        enum = AudioUtilities.GetDeviceEnumerator()
        coll = enum.EnumAudioEndpoints(0, 1)  # eRender, ACTIVE
        active = frozenset(coll.Item(i).GetId() for i in range(coll.GetCount()))
    except Exception:
        return None
    defaults = []
    for role in (0, 2):                       # eConsole, eCommunications (la de Teams)
        try:
            defaults.append(enum.GetDefaultAudioEndpoint(0, role).GetId())
        except Exception:
            defaults.append(None)             # sin salida por defecto
    return active, defaults[0], defaults[1]


def _describe_change(before, now) -> str:
    added, removed = now[0] - before[0], before[0] - now[0]
    parts = []
    if added:
        parts.append(f"{len(added)} salida(s) nueva(s)")
    if removed:
        parts.append(f"{len(removed)} salida(s) quitada(s)")
    if before[1:] != now[1:]:
        parts.append("cambio la salida por defecto")
    return ', '.join(parts) or 'cambio'


def _watch_new_outputs(restart: threading.Event, stopping: threading.Event):
    """Pide reiniciar el worker si cambian las salidas de Windows respecto a
    las de cuando se enumeraron: una nueva, una que desaparece o un cambio de
    salida por defecto (ver CAMBIOS DE SALIDA en la cabecera). En su propia
    hebra con su COM: la principal es la de PortAudio y no se le toca el
    apartamento."""
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception as e:
        trace(f"vigilante de salidas no disponible: {e}")
        return
    known = _active_outputs()
    if known is None:
        trace("vigilante de salidas no disponible: pycaw no responde")
        return
    while not stopping.wait(RESCAN_S):
        now = _active_outputs()
        if now is None:
            continue
        if now != known:
            trace(f"{_describe_change(known, now)}: reinicio para volver a abrirlas")
            restart.set()
            return


def _emit_header(sr: int, n_ch: int):
    sys.stdout.buffer.write(struct.pack('<I', sr))
    sys.stdout.buffer.write(struct.pack('<I', n_ch))
    sys.stdout.buffer.flush()


def main():
    trace("arranca")
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as e:
        fail(f"pyaudiowpatch no disponible: {e}")
    trace("pyaudiowpatch importado")

    if '--standby' in sys.argv:
        # Worker "armado": el interprete y las librerias ya estan cargados, y
        # espera la orden del padre. Arrancar un Python nuevo al entrar en la
        # llamada tardaba 4-7 s con la CPU cargada (python -c pass: 1,5-2 s), y
        # el 29/10 pasaron de 15 s cuatro veces seguidas. PortAudio se inicia
        # DESPUES de la orden para enumerar las salidas de ese momento.
        trace("en espera")
        try:
            orden = sys.stdin.readline()
        except Exception:
            orden = ''
        if not orden.strip().startswith('go'):
            os._exit(0)
        trace("orden recibida")

    pa = pyaudio.PyAudio()
    trace("PortAudio listo")
    # Justo despues de enumerar PortAudio: la foto del vigilante es la de ahora.
    restart = threading.Event()
    watch_stop = threading.Event()
    threading.Thread(target=_watch_new_outputs, args=(restart, watch_stop),
                     daemon=True, name="outputs-watch").start()
    candidates = _list_candidates(pa, pyaudio)
    trace(f"{len(candidates)} salida(s): " + ", ".join(f"'{c['name']}'" for c in candidates))

    lock = threading.Lock()
    header_sent = threading.Event()
    stopping = threading.Event()
    started = []          # (dev, stream, DeviceBuffer) ya en la mezcla
    errors = []
    pending = [len(candidates)]

    def attach(dev, stream, buf):
        with lock:
            if stopping.is_set():
                return False
            started.append((dev, stream, buf))
            if not header_sent.is_set():
                _emit_header(MIX_SR, 1)
                header_sent.set()
        return True

    def open_one(dev):
        stream = None
        try:
            n_ch = min(int(dev['maxInputChannels']), 2)
            if n_ch < 1:
                raise RuntimeError("sin canales de entrada")
            sr = int(dev['defaultSampleRate'])
            buf = DeviceBuffer(dev['name'], sr, n_ch)

            def callback(in_data, frame_count, time_info, status):
                buf.push(in_data)
                return (None, pyaudio.paContinue)

            trace(f"abriendo '{dev['name']}'")
            stream = pa.open(
                format=pyaudio.paFloat32, channels=n_ch, rate=sr, input=True,
                input_device_index=dev['index'], frames_per_buffer=int(sr * 0.1),
                stream_callback=callback, start=False,
            )
            stream.start_stream()
            if attach(dev, stream, buf):
                trace(f"activa '{dev['name']}' ({sr}Hz, {n_ch}ch)")
            else:
                stream.stop_stream()
                stream.close()
        except Exception as e:
            errors.append(f"'{dev['name']}': {e}")
            trace(f"fallo '{dev['name']}': {e}")
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        finally:
            with lock:
                pending[0] -= 1

    def mixer():
        # Se emite al ritmo del reloj, no al de los dispositivos: WASAPI no
        # entrega nada mientras una salida esta en silencio y esperar a todos
        # los streams bloquearia la mezcla.
        n = int(MIX_SR * MIX_TICK)
        t_next = time.monotonic()
        try:
            while True:
                with lock:
                    live = []
                    for item in started:
                        try:
                            if item[1].is_active():
                                live.append(item)
                        except Exception:
                            pass
                    if len(live) != len(started):
                        trace(f"salidas activas: {len(live)} de {len(started)}")
                        started[:] = live
                    snapshot = list(started)
                if not snapshot:
                    trace("ya no queda ninguna salida activa: termino para que me relancen")
                    break
                if restart.is_set():
                    # Entre dos bloques, nunca a mitad de uno: el padre lee
                    # bloques enteros y relanza al ver que el worker termino.
                    trace("termino para que me relancen con las salidas de ahora")
                    break
                t_next += MIX_TICK
                wait = t_next - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                elif wait < -1.0:
                    t_next = time.monotonic()
                block = mix_blocks([b.pop(n) for _d, _s, b in snapshot], n)
                sys.stdout.buffer.write(block.tobytes())
                sys.stdout.buffer.flush()
        except Exception as e:
            trace(f"fin: {e}")
        sys.stderr.flush()
        # Sin cierre ordenado de PortAudio: el proceso se va y Windows libera
        # los streams; cerrarlos desde aqui podia bloquearse.
        os._exit(0)

    mixer_thread = threading.Thread(target=mixer, daemon=True, name="mixer")

    # Las salidas se abren en ESTA hebra (la principal) y una tras otra. Probado
    # el 29/10: abrirlas desde otra hebra o a la vez falla siempre con
    # "Unanticipated host error" (-9999), porque WASAPI necesita la hebra en la
    # que se inicializo PortAudio. La mezcla corre aparte; la primera salida que
    # abre da la cabecera y las demas se unen despues, sin retenerla.
    for dev in candidates:
        open_one(dev)
        if header_sent.is_set() and not mixer_thread.is_alive():
            mixer_thread.start()
    if not header_sent.is_set():
        fail("ningun dispositivo loopback se pudo abrir -> " + " | ".join(errors))
    mixer_thread.join()


if __name__ == '__main__':
    main()
