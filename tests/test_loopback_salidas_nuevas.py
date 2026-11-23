"""Cambios de salida de audio a mitad de llamada (wasapi_loopback_worker).

PortAudio solo ve las salidas que existian al iniciarse, y un stream de
loopback puede quedarse abierto pero mudo cuando Windows reconfigura el audio.
Casos reales: unos auriculares que se conectan a mitad de llamada, y un monitor
que desaparece (la captura de los altavoces siguio "activa" pero solo grabo
silencio). El worker vigila las salidas de Windows y, si cambia algo, termina
limpio para que el padre lo relance con las de ahora.
Sin audio real: PortAudio y pycaw se sustituyen por dobles.
"""
import io
import sys
import threading
import types

import pytest

import wasapi_loopback_worker as w

pytestmark = pytest.mark.unit

ALTAVOZ = (frozenset({'altavoz'}), 'altavoz', 'altavoz')
CON_MONITOR = (frozenset({'altavoz', 'monitor'}), 'monitor', 'monitor')


@pytest.fixture
def rapido(monkeypatch):
    monkeypatch.setattr(w, 'RESCAN_S', 0.01)
    monkeypatch.setattr(w, '_playing_peak', lambda: 0.0)


def vigilar(monkeypatch, vistas, para_al_acabar=True):
    """Corre el vigilante con esa secuencia de estados; True si pidio reiniciar."""
    vistas = iter(vistas)
    stop, restart = threading.Event(), threading.Event()

    def ver():
        try:
            return next(vistas)
        except StopIteration:
            if para_al_acabar:
                stop.set()
            return None
    monkeypatch.setattr(w, '_active_outputs', ver)
    w._watch_new_outputs(restart, stop)
    return restart.is_set()


def test_una_salida_nueva_pide_reiniciar(rapido, monkeypatch):
    auriculares = (frozenset({'altavoz', 'auriculares'}), 'auriculares', 'auriculares')
    assert vigilar(monkeypatch, [ALTAVOZ, ALTAVOZ, auriculares])


def test_una_salida_que_desaparece_tambien_reinicia(rapido, monkeypatch):
    """El caso de la reunion de 22 min: desaparece el monitor y la captura de
    los altavoces se queda muda."""
    assert vigilar(monkeypatch, [CON_MONITOR, CON_MONITOR, ALTAVOZ])


def test_un_cambio_de_salida_por_defecto_reinicia(rapido, monkeypatch):
    otra_por_defecto = (CON_MONITOR[0], 'altavoz', 'monitor')
    assert vigilar(monkeypatch, [CON_MONITOR, otra_por_defecto])


def test_sin_cambios_no_reinicia(rapido, monkeypatch):
    assert not vigilar(monkeypatch, [ALTAVOZ] * 5)


def test_si_windows_no_responde_un_momento_sigue_vigilando(rapido, monkeypatch):
    nueva = (frozenset({'altavoz', 'auriculares'}), 'altavoz', 'altavoz')
    assert vigilar(monkeypatch, [ALTAVOZ, None, nueva])


def test_sin_pycaw_no_vigila_y_no_reinicia(monkeypatch):
    monkeypatch.setattr(w, '_active_outputs', lambda: None)
    restart = threading.Event()
    w._watch_new_outputs(restart, threading.Event())
    assert not restart.is_set()


def test_sin_com_no_vigila(monkeypatch):
    falso = types.ModuleType('comtypes')

    def falla():
        raise OSError('sin COM')
    falso.CoInitialize = falla
    monkeypatch.setitem(sys.modules, 'comtypes', falso)
    monkeypatch.setattr(w, '_active_outputs', lambda: pytest.fail('no debe consultar'))
    w._watch_new_outputs(threading.Event(), threading.Event())


def test_el_log_dice_que_ha_cambiado():
    assert w._describe_change(CON_MONITOR, ALTAVOZ) == '1 salida(s) quitada(s), cambio la salida por defecto'
    assert w._describe_change(ALTAVOZ, CON_MONITOR).startswith('1 salida(s) nueva(s)')
    assert w._describe_change(ALTAVOZ, ALTAVOZ) == 'cambio'


class Coll:
    def GetCount(self):
        return 2

    def Item(self, i):
        return types.SimpleNamespace(GetId=lambda: f'id{i}')


def test_active_outputs_lee_las_activas_y_las_por_defecto(monkeypatch):
    from pycaw import pycaw
    roles = []

    class Enum:
        def EnumAudioEndpoints(self, flujo, estado):
            assert (flujo, estado) == (0, 1), "salidas (eRender), solo activas"
            return Coll()

        def GetDefaultAudioEndpoint(self, flujo, rol):
            roles.append((flujo, rol))
            return types.SimpleNamespace(GetId=lambda: f'defecto{rol}')
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetDeviceEnumerator', staticmethod(lambda: Enum()))
    assert w._active_outputs() == (frozenset({'id0', 'id1'}), 'defecto0', 'defecto2')
    assert roles == [(0, 0), (0, 2)], "consola y comunicaciones"


def test_sin_salida_por_defecto_sigue_funcionando(monkeypatch):
    from pycaw import pycaw

    class Enum:
        def EnumAudioEndpoints(self, flujo, estado):
            return Coll()

        def GetDefaultAudioEndpoint(self, flujo, rol):
            raise OSError('no hay salida por defecto')
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetDeviceEnumerator', staticmethod(lambda: Enum()))
    assert w._active_outputs() == (frozenset({'id0', 'id1'}), None, None)


def test_active_outputs_si_falla_devuelve_none(monkeypatch):
    from pycaw import pycaw

    def falla():
        raise OSError('sin audio')
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetDeviceEnumerator', staticmethod(falla))
    assert w._active_outputs() is None


# ── Red de seguridad: suena audio pero la captura esta muda ──────────────

@pytest.fixture
def mute(monkeypatch):
    monkeypatch.setattr(w, 'MUTE_GRACE_S', 0.0)
    monkeypatch.setattr(w, '_LAST_SOUND', [0.0])
    return w._MuteCheck(started=0.0)


def test_sonando_y_sin_captura_es_una_captura_muda(mute):
    assert mute.mute(0.3, now=100.0) is False, "un solo sondeo no basta"
    assert mute.mute(0.3, now=103.0) is True


def test_si_la_captura_recoge_algo_no_esta_muda(mute):
    mute.mute(0.3, now=100.0)
    w._LAST_SOUND[0] = 101.0                 # llego sonido despues del primer sondeo
    assert mute.mute(0.3, now=103.0) is False


def test_en_silencio_no_hay_nada_que_capturar(mute):
    for t in (100.0, 103.0, 106.0):
        assert mute.mute(0.0, now=t) is False
    assert mute.mute(None, now=109.0) is False, "sin medidor no se decide nada"


def test_sondeos_con_sonido_muy_separados_no_cuentan(mute):
    mute.mute(0.3, now=100.0)
    assert mute.mute(0.3, now=100.0 + w.MUTE_WINDOW_S + 1) is False


def test_al_arrancar_hay_margen(monkeypatch):
    monkeypatch.setattr(w, '_LAST_SOUND', [0.0])
    check = w._MuteCheck(started=100.0)
    check.mute(0.3, now=101.0)
    assert check.mute(0.3, now=104.0) is False, "dentro de MUTE_GRACE_S"


def test_la_captura_marca_cuando_recoge_sonido(monkeypatch):
    import numpy as np
    monkeypatch.setattr(w, '_LAST_SOUND', [0.0])
    buf = w.DeviceBuffer('altavoz', 48000, 1)
    buf.push(np.zeros(480, dtype=np.float32).tobytes())
    assert w._LAST_SOUND[0] == 0.0, "el silencio no cuenta"
    buf.push(np.full(480, 0.2, dtype=np.float32).tobytes())
    assert w._LAST_SOUND[0] > 0.0


def test_el_vigilante_reinicia_si_la_captura_esta_muda(rapido, monkeypatch):
    monkeypatch.setattr(w, 'MUTE_GRACE_S', 0.0)
    monkeypatch.setattr(w, '_LAST_SOUND', [0.0])
    monkeypatch.setattr(w, '_active_outputs', lambda: ALTAVOZ)
    monkeypatch.setattr(w, '_playing_peak', lambda: 0.4)
    restart = threading.Event()
    w._watch_new_outputs(restart, threading.Event())
    assert restart.is_set()


def test_el_medidor_lee_el_maximo_de_las_salidas(monkeypatch):
    from pycaw import pycaw

    class Medidor:
        def __init__(self, pico):
            self.pico = pico

        def QueryInterface(self, iface):
            return self

        def GetPeakValue(self):
            return self.pico

    class Salidas:
        picos = [0.1, 0.5]

        def GetCount(self):
            return 2

        def Item(self, i):
            return types.SimpleNamespace(Activate=lambda *a: Medidor(self.picos[i]))

    class Enum:
        def EnumAudioEndpoints(self, flujo, estado):
            return Salidas()
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetDeviceEnumerator', staticmethod(lambda: Enum()))
    assert w._playing_peak() == 0.5


def test_si_el_medidor_falla_no_se_decide_nada(monkeypatch):
    from pycaw import pycaw

    def falla():
        raise OSError('sin audio')
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetDeviceEnumerator', staticmethod(falla))
    assert w._playing_peak() is None


# ── El worker entero, con un PortAudio de mentira ────────────────────────

class Stream:
    def __init__(self, callback):
        self.callback = callback

    def start_stream(self):
        self.callback(b'\x00' * 4 * 480, 480, None, 0)

    def is_active(self):
        return True


class PyAudio:
    def get_host_api_info_by_type(self, api):
        return {'defaultOutputDevice': 0}

    def get_device_info_by_index(self, i):
        return {'index': 0, 'name': 'Altavoz [Loopback]', 'isLoopbackDevice': True,
                'maxInputChannels': 2, 'defaultSampleRate': 48000}

    def open(self, **kw):
        return Stream(kw['stream_callback'])


class Salida(io.BytesIO):
    @property
    def buffer(self):
        return self


def test_el_worker_termina_limpio_entre_bloques_al_aparecer_una_salida(rapido, monkeypatch):
    falso = types.ModuleType('pyaudiowpatch')
    falso.PyAudio, falso.paWASAPI, falso.paFloat32, falso.paContinue = PyAudio, 13, 1, 0
    monkeypatch.setitem(sys.modules, 'pyaudiowpatch', falso)
    monkeypatch.setattr(sys, 'argv', ['worker'])
    salida = Salida()
    monkeypatch.setattr(sys, 'stdout', salida)
    monkeypatch.setattr(w, 'MIX_TICK', 0.01)
    nueva = (frozenset({'altavoz', 'auriculares'}), 'altavoz', 'altavoz')
    vistas = iter([ALTAVOZ] * 3)
    monkeypatch.setattr(w, '_active_outputs', lambda: next(vistas, nueva))
    salidas = []

    # os._exit es lo ultimo de la mezcla: el doble solo lo apunta y la hebra acaba.
    monkeypatch.setattr(w.os, '_exit', salidas.append)

    w.main()

    assert salidas == [0], "termina con 0: el padre lo relanza"
    datos = salida.getvalue()
    bloque = int(w.MIX_SR * w.MIX_TICK) * 4
    assert len(datos) > 8 and (len(datos) - 8) % bloque == 0, "solo bloques enteros"
