"""Salidas de audio que aparecen a mitad de llamada (wasapi_loopback_worker).

PortAudio solo ve las salidas que existian al iniciarse. Si a mitad de llamada
se conectan unos auriculares y Teams pasa a sonar por ellos, esa salida no
estaba en la mezcla. El worker vigila las salidas activas de Windows y, si
aparece una nueva, termina limpio para que el padre lo relance con ella.
Sin audio real: PortAudio y pycaw se sustituyen por dobles.
"""
import io
import sys
import threading
import types

import pytest

import wasapi_loopback_worker as w

pytestmark = pytest.mark.unit


@pytest.fixture
def rapido(monkeypatch):
    monkeypatch.setattr(w, 'RESCAN_S', 0.01)


def test_una_salida_nueva_pide_reiniciar(rapido, monkeypatch):
    vistas = iter([{'altavoz'}, {'altavoz'}, {'altavoz', 'auriculares'}])
    monkeypatch.setattr(w, '_active_outputs', lambda: next(vistas, {'altavoz', 'auriculares'}))
    restart, stop = threading.Event(), threading.Event()
    w._watch_new_outputs(restart, stop)
    assert restart.is_set()


def test_una_salida_que_desaparece_no_reinicia(rapido, monkeypatch):
    vistas = iter([{'altavoz', 'monitor'}, {'altavoz'}, {'altavoz'}])
    stop = threading.Event()

    def ver():
        try:
            return next(vistas)
        except StopIteration:
            stop.set()
            return {'altavoz'}
    monkeypatch.setattr(w, '_active_outputs', ver)
    restart = threading.Event()
    w._watch_new_outputs(restart, stop)
    assert not restart.is_set()


def test_si_windows_no_responde_un_momento_sigue_vigilando(rapido, monkeypatch):
    vistas = iter([{'altavoz'}, None, {'altavoz', 'auriculares'}])
    monkeypatch.setattr(w, '_active_outputs', lambda: next(vistas))
    restart = threading.Event()
    w._watch_new_outputs(restart, threading.Event())
    assert restart.is_set()


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


def test_active_outputs_lee_las_salidas_activas(monkeypatch):
    from pycaw import pycaw

    class Coll:
        def GetCount(self):
            return 2

        def Item(self, i):
            return types.SimpleNamespace(GetId=lambda: f'id{i}')

    class Enum:
        def EnumAudioEndpoints(self, flujo, estado):
            assert (flujo, estado) == (0, 1), "salidas (eRender), solo activas"
            return Coll()
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetDeviceEnumerator', staticmethod(lambda: Enum()))
    assert w._active_outputs() == {'id0', 'id1'}


def test_active_outputs_si_falla_devuelve_none(monkeypatch):
    from pycaw import pycaw

    def falla():
        raise OSError('sin audio')
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetDeviceEnumerator', staticmethod(falla))
    assert w._active_outputs() is None


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
    vistas = iter([{'altavoz'}] * 3)
    monkeypatch.setattr(w, '_active_outputs', lambda: next(vistas, {'altavoz', 'auriculares'}))
    salidas = []

    # os._exit es lo ultimo de la mezcla: el doble solo lo apunta y la hebra acaba.
    monkeypatch.setattr(w.os, '_exit', salidas.append)

    w.main()

    assert salidas == [0], "termina con 0: el padre lo relanza"
    datos = salida.getvalue()
    bloque = int(w.MIX_SR * w.MIX_TICK) * 4
    assert len(datos) > 8 and (len(datos) - 8) % bloque == 0, "solo bloques enteros"
