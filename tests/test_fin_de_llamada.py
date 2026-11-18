"""Al colgar se para la grabacion de la llamada, tambien la que ya estaba en
marcha cuando se detecto la llamada (03/11/2026).

Con "Grabar ahora" pulsado al entrar, la grabacion empieza antes de que el
detector vea la llamada. on_call_started salia sin apuntar la llamada y, al
colgar, on_call_ended ignoraba el fin como "generacion obsoleta": la grabacion
siguio 6 min hasta que se paro a mano.

Vale tambien para una grabacion empezada mucho antes de la llamada: si mientras
tanto entra una llamada corta, la grabacion se para al colgarla. Se prefiere a
que siga grabando sin que nadie lo sepa.

Se arranca main() de verdad, con dobles del grabador, el detector y la bandeja,
y se llama a los on_call_started y on_call_ended que conecta: son los de la app,
no una copia.
"""
import pytest

import audio_recorder
import inbox_watcher
import main
import storage
import teams_detector
import tray_app

pytestmark = pytest.mark.unit


class Grabador:
    def __init__(self):
        self.is_recording = False
        self.paradas = 0

    def arm_loopback(self):
        pass

    def disarm_loopback(self):
        pass

    def stop(self):
        self.paradas += 1
        self.is_recording = False


class Detector:
    def __init__(self):
        self.call_generation = 0
        self.call_declined = False
        self.on_call_started = None
        self.on_call_ended = None
        self.is_active_recording = None

    def start(self):
        pass


class Bandeja:
    def __init__(self):
        self.estados = []

    def set_recording(self, active, *_a, **_k):
        self.estados.append(active)

    def has_pending_session(self, _meeting):
        return False

    def start(self):
        pass        # en la app bloquea el hilo principal; aqui main() sigue

    def __getattr__(self, _name):
        # Los avisos y callbacks que main() solo conecta (warn_*, _on_recording_done)
        return lambda *_a, **_k: None


@pytest.fixture
def app(monkeypatch, tmp_path):
    grabador, detector, bandeja = Grabador(), Detector(), Bandeja()
    monkeypatch.setattr(main, '_BS_LOG', tmp_path / 'startup.log')
    monkeypatch.setattr(main, '_check_single_instance', lambda: True)
    monkeypatch.setattr(main, '_start_cli_listener', lambda *_a: None)
    monkeypatch.setattr(storage, 'ensure_directories', lambda: None)
    monkeypatch.setattr(storage, 'cleanup_old_recordings', lambda *_a, **_k: None)
    monkeypatch.setattr(storage, 'cleanup_old_minutes', lambda: None)
    monkeypatch.setattr(audio_recorder, 'AudioRecorder', lambda: grabador)
    monkeypatch.setattr(teams_detector, 'TeamsCallDetector', lambda: detector)
    monkeypatch.setattr(teams_detector, 'get_current_meeting_name', lambda: 'Revision semanal')
    monkeypatch.setattr(tray_app, 'TrayApp', lambda _r, _d: bandeja)
    monkeypatch.setattr(inbox_watcher, 'InboxWatcher',
                        lambda **_k: type('W', (), {'start': lambda self: None})())
    main.main()
    # main() recoge cualquier excepcion: si un doble no encaja, esto lo dice.
    assert detector.on_call_started is not None, 'main() no llego a conectar el detector'
    return grabador, detector, bandeja


def test_al_colgar_se_para_la_grabacion_empezada_antes_de_detectar_la_llamada(app):
    grabador, detector, bandeja = app
    grabador.is_recording = True        # "Grabar ahora" al entrar
    detector.call_generation = 1        # el detector ve la llamada despues
    detector.on_call_started()
    detector.on_call_ended()
    assert grabador.paradas == 1
    assert bandeja.estados == [False]


def test_el_fin_de_una_llamada_no_apuntada_no_para_la_grabacion(app):
    """La guarda sigue: el fin de una llamada que on_call_started no apunto (la
    2, aqui) no para lo que se esta grabando."""
    grabador, detector, _ = app
    grabador.is_recording = True
    detector.call_generation = 1
    detector.on_call_started()
    detector.call_generation = 2
    detector.on_call_ended()
    assert grabador.paradas == 0
    assert grabador.is_recording
