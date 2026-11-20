"""Se graba el micro que usa Teams, no el predeterminado de Windows.

INCIDENTE: con unos auriculares Bluetooth, Teams usaba su micro y Noted grababa
el del portatil, que quedaba en silencio: la pista de quien graba salio vacia y
en la minuta faltaban todas sus intervenciones.

Sin hardware: sounddevice y pycaw se sustituyen por dobles.
"""
import queue
import time
import types

import pytest

import audio_recorder as ar
import mic_select
from audio_recorder import AudioRecorder

pytestmark = pytest.mark.unit


class Stream:
    abiertos: list = []

    def __init__(self, callback=None, device=None, **_kw):
        self.device, self.closed = device, False
        Stream.abiertos.append(self)

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def rec(monkeypatch):
    Stream.abiertos = []
    monkeypatch.setattr(ar.sd, 'InputStream', Stream)
    monkeypatch.setattr(ar.sd, 'query_devices', lambda *a, **k: {'name': 'falso'})
    monkeypatch.setattr(AudioRecorder, '_mic_candidates', staticmethod(lambda: [None, 3, 7]))
    monkeypatch.setattr(AudioRecorder, '_refresh_portaudio', lambda self: None)
    r = AudioRecorder()
    r._recording = True
    r._mic_q = queue.Queue()
    r._mic_t0 = time.monotonic()
    yield r
    r._recording = False
    r._mic_watch_stop.set()


def test_el_micro_de_teams_se_abre_el_primero(rec, monkeypatch):
    monkeypatch.setattr(mic_select, 'input_device_index', lambda name: 7)
    rec._mic_name = 'Auriculares (Bluetooth)'
    assert rec._open_mic_stream()
    assert [s.device for s in Stream.abiertos] == [7]


def test_sin_micro_de_teams_se_usa_el_predeterminado(rec):
    rec._mic_name = None
    assert rec._open_mic_stream()
    assert Stream.abiertos[0].device is None


def test_si_el_micro_de_teams_no_esta_en_la_lista_se_usa_el_predeterminado(rec, monkeypatch):
    monkeypatch.setattr(mic_select, 'input_device_index', lambda name: None)
    rec._mic_name = 'Auriculares (Bluetooth)'
    assert rec._open_mic_stream()
    assert Stream.abiertos[0].device is None


def test_al_empezar_se_pregunta_que_micro_usa_teams(rec, monkeypatch):
    refrescos = []
    monkeypatch.setattr(AudioRecorder, '_refresh_portaudio', lambda self: refrescos.append(1))
    monkeypatch.setattr(mic_select, 'teams_capture_device', lambda: 'Auriculares (Bluetooth)')
    monkeypatch.setattr(mic_select, 'input_device_index', lambda name: None)
    rec._pick_teams_mic()
    assert rec._mic_name == 'Auriculares (Bluetooth)'
    assert refrescos == [1], "no esta en la lista de PortAudio: se refresca (conectado despues)"


def test_si_no_se_puede_preguntar_se_sigue_con_el_predeterminado(rec, monkeypatch):
    def falla():
        raise OSError('sin COM')
    monkeypatch.setattr(mic_select, 'teams_capture_device', falla)
    rec._pick_teams_mic()
    assert rec._mic_name is None


def test_si_teams_cambia_de_micro_la_grabacion_cambia_con_el(rec, monkeypatch):
    monkeypatch.setattr(ar, '_MIC_FOLLOW_S', 0.0)
    rec._mic_name = 'Microfono del portatil'
    vistos = iter(['Microfono del portatil', 'Auriculares (Bluetooth)'])
    reaperturas = []

    def micro_de_teams():
        try:
            return next(vistos)
        except StopIteration:
            rec._recording = False
            return None
    monkeypatch.setattr(mic_select, 'teams_capture_device', micro_de_teams)
    monkeypatch.setattr(rec, '_reopen_mic', lambda: reaperturas.append(rec._mic_name) or True)

    rec._follow_teams_mic()

    assert reaperturas == ['Auriculares (Bluetooth)'], "una sola reapertura, ya con el micro nuevo"


def test_un_fallo_al_mirar_el_micro_no_para_el_seguimiento(rec, monkeypatch):
    monkeypatch.setattr(ar, '_MIC_FOLLOW_S', 0.0)
    llamadas = []

    def falla():
        llamadas.append(1)
        if len(llamadas) > 2:
            rec._recording = False
        raise OSError('COM ocupado')
    monkeypatch.setattr(mic_select, 'teams_capture_device', falla)
    rec._follow_teams_mic()
    assert len(llamadas) == 3


# ── mic_select ───────────────────────────────────────────────────────────

def test_el_indice_se_busca_por_prefijo_en_mme(monkeypatch):
    """MME corta los nombres a 31 caracteres."""
    apis = [{'name': 'MME'}, {'name': 'Windows WASAPI'}]
    devs = [{'name': 'Microsoft Sound Mapper - Input', 'hostapi': 0, 'max_input_channels': 2},
            {'name': 'Auriculares (Bluetooth', 'hostapi': 1, 'max_input_channels': 1},
            {'name': 'Altavoces', 'hostapi': 0, 'max_input_channels': 0},
            {'name': 'Auriculares (Bluetooth', 'hostapi': 0, 'max_input_channels': 1}]
    falso = types.SimpleNamespace(query_hostapis=lambda: apis, query_devices=lambda: devs)
    monkeypatch.setitem(__import__('sys').modules, 'sounddevice', falso)
    assert mic_select.input_device_index('Auriculares (Bluetooth Hands-Free)') == 3
    assert mic_select.input_device_index('Otro micro') is None


def test_sin_mme_no_hay_indice(monkeypatch):
    falso = types.SimpleNamespace(query_hostapis=lambda: [{'name': 'WASAPI'}], query_devices=lambda: [])
    monkeypatch.setitem(__import__('sys').modules, 'sounddevice', falso)
    assert mic_select.input_device_index('Auriculares') is None


@pytest.fixture
def endpoints(monkeypatch):
    """Dos micros; Teams (pid 100) captura en el segundo con un subproceso (200)."""
    from pycaw import pycaw
    import teams_detector

    class Ctl:
        def __init__(self, pid, estado=1):
            self.pid, self.estado = pid, estado

        def QueryInterface(self, iface):
            return self

        def GetProcessId(self):
            return self.pid

        def GetState(self):
            return self.estado

    class Sesiones:
        def __init__(self, ctls):
            self.ctls = ctls

        def GetCount(self):
            return len(self.ctls)

        def GetSession(self, i):
            return self.ctls[i]

    class Dispositivo:
        def __init__(self, nombre, ctls):
            self.nombre, self.ctls = nombre, ctls

        def Activate(self, *a):
            return types.SimpleNamespace(QueryInterface=lambda i: types.SimpleNamespace(
                GetSessionEnumerator=lambda: Sesiones(self.ctls)))

    micros = [Dispositivo('Microfono del portatil', [Ctl(900)]),
              Dispositivo('Auriculares (Bluetooth)', [Ctl(200, estado=0), Ctl(200)])]
    coll = types.SimpleNamespace(GetCount=lambda: len(micros), Item=lambda i: micros[i])
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetDeviceEnumerator', staticmethod(
        lambda: types.SimpleNamespace(EnumAudioEndpoints=lambda flujo, estado: coll)))
    monkeypatch.setattr(pycaw.AudioUtilities, 'CreateDevice', staticmethod(
        lambda d: types.SimpleNamespace(FriendlyName=d.nombre)))
    monkeypatch.setattr(teams_detector, '_teams_pids', lambda: [100])
    monkeypatch.setattr(teams_detector, '_parent_pid', lambda pid: {200: 100}.get(pid))
    return micros


def test_teams_captura_en_el_micro_de_los_auriculares(endpoints):
    assert mic_select.teams_capture_device() == 'Auriculares (Bluetooth)'


def test_sin_teams_abierto_no_hay_micro_de_teams(endpoints, monkeypatch):
    import teams_detector
    monkeypatch.setattr(teams_detector, '_teams_pids', lambda: [])
    assert mic_select.teams_capture_device() is None


def test_si_windows_falla_no_hay_micro_de_teams(endpoints, monkeypatch):
    from pycaw import pycaw

    def falla():
        raise OSError('sin audio')
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetDeviceEnumerator', staticmethod(falla))
    assert mic_select.teams_capture_device() is None
