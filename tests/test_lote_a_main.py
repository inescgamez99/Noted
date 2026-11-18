"""Los callbacks de main() que dejaban la app 'ocupada' para siempre (lote A).

Como test_fin_de_llamada: se arranca main() de verdad con dobles del grabador,
el detector y la bandeja, y se usan los callbacks que conecta.
"""
import sys
import time

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
        self.start_result = True        # True | False | excepcion
        self.on_recording_stopped = None

    def arm_loopback(self):
        pass

    def disarm_loopback(self):
        pass

    def stop(self):
        self.is_recording = False

    def start(self, _path):
        if isinstance(self.start_result, Exception):
            raise self.start_result
        if self.start_result is not False:
            self.is_recording = True
        return self.start_result


class Detector:
    def __init__(self):
        self.call_generation = 0
        self.call_declined = False
        self.on_call_started = None
        self.on_call_ended = None
        self.is_active_recording = None
        self.declined = 0

    def start(self):
        pass

    def set_declined(self):
        self.declined += 1


class Bandeja:
    last_recording_mode = None

    def __init__(self):
        self.pendiente = False
        self.llamadas = []

    def set_recording(self, active, *_a, **_k):
        self.llamadas.append(('set_recording', active))

    def has_pending_session(self, _meeting):
        return self.pendiente

    def request_continuation(self):
        self.llamadas.append('request_continuation')

    def cancel_continuation(self):
        self.llamadas.append('cancel_continuation')

    def start(self):
        pass

    def __getattr__(self, _name):
        return lambda *_a, **_k: None


class PopupMudo:
    """Un aviso que nunca llama a ningun callback (el hilo de Tk se colgo)."""
    mostrados = 0

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def show(self):
        PopupMudo.mostrados += 1


class PopupQueDice:
    """Un aviso que responde 'si' en cuanto se muestra."""
    respuesta = 'on_yes'

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def show(self):
        self.kwargs[self.respuesta]()


@pytest.fixture
def app(monkeypatch, tmp_path):
    grabador, detector, bandeja = Grabador(), Detector(), Bandeja()
    monkeypatch.setattr(main, '_BS_LOG', tmp_path / 'startup.log')
    monkeypatch.setattr(main, '_check_single_instance', lambda: True)
    monkeypatch.setattr(main, '_start_cli_listener', lambda *_a: None)
    monkeypatch.setattr(storage, 'ensure_directories', lambda: None)
    monkeypatch.setattr(storage, 'cleanup_old_recordings', lambda *_a, **_k: None)
    monkeypatch.setattr(storage, 'cleanup_old_minutes', lambda: None)
    monkeypatch.setattr(storage, 'get_recording_path', lambda name: tmp_path / f'{name}.wav')
    monkeypatch.setattr(audio_recorder, 'AudioRecorder', lambda: grabador)
    monkeypatch.setattr(teams_detector, 'TeamsCallDetector', lambda: detector)
    monkeypatch.setattr(teams_detector, 'get_current_meeting_name', lambda: 'Revision semanal')
    monkeypatch.setattr(tray_app, 'TrayApp', lambda _r, _d: bandeja)
    monkeypatch.setattr(inbox_watcher, 'InboxWatcher',
                        lambda **_k: type('W', (), {'start': lambda self: None})())
    import teams_chat
    monkeypatch.setattr(teams_chat, 'send_recording_notice', lambda *a, **k: None)
    PopupMudo.mostrados = 0
    main.main()
    assert detector.on_call_started is not None, 'main() no llego a conectar el detector'
    return grabador, detector, bandeja


def _con_popup(monkeypatch, clase):
    modulo = type(sys)('popup')
    modulo.RecordingPopup = clase
    monkeypatch.setitem(sys.modules, 'popup', modulo)


# ── A5: la guarda del aviso ──────────────────────────────────────────────

def test_si_el_aviso_nunca_responde_se_vuelve_a_ofrecer_grabar(app, monkeypatch):
    """Si RecordingPopup.show() fallaba dentro de Tk o el hilo se colgaba,
    _popup_active se quedaba True (solo se resetea en si/no/timeout, y el timeout
    vive en Tk): on_call_started retornaba siempre y no se volvia a ofrecer grabar
    hasta reiniciar."""
    _grabador, detector, _ = app
    _con_popup(monkeypatch, PopupMudo)
    monkeypatch.setattr(main, '_popup_guard_s', lambda: 0.05)

    detector.on_call_started()
    detector.on_call_started()                  # con el aviso 'vivo': no abre otro
    assert PopupMudo.mostrados == 1

    time.sleep(0.4)
    detector.on_call_started()
    assert PopupMudo.mostrados == 2, "la guarda no libero el flag"


def test_la_guarda_de_un_aviso_no_libera_el_de_uno_posterior(app, monkeypatch):
    """Aviso 1 responde (si) y se libera; llega el aviso 2 (mudo). La guarda del 1
    vence con el 2 aun abierto: no debe soltarlo."""
    grabador, detector, _ = app
    _con_popup(monkeypatch, PopupQueDice)
    tiempos = iter([0.2, 5.0])
    monkeypatch.setattr(main, '_popup_guard_s', lambda: next(tiempos))

    detector.on_call_started()                  # aviso 1: dice que si y graba
    assert grabador.is_recording
    grabador.is_recording = False               # cuelga y la llamada vuelve
    _con_popup(monkeypatch, PopupMudo)
    detector.on_call_started()                  # aviso 2: mudo
    assert PopupMudo.mostrados == 1

    time.sleep(0.5)                             # vence la guarda del aviso 1
    detector.on_call_started()
    assert PopupMudo.mostrados == 1, "la guarda del aviso 1 libero el del aviso 2"


# ── A4: la continuacion que no llega a existir ───────────────────────────

def _reconexion(app, monkeypatch):
    _grabador, detector, bandeja = app
    _con_popup(monkeypatch, PopupQueDice)
    bandeja.pendiente = True
    return detector, bandeja


def test_si_start_lanza_en_una_reconexion_se_deshace_la_continuacion(app, monkeypatch):
    """request_continuation marcaba la sesion 'esperando' ANTES de recorder.start():
    si start lanzaba, la sesion esperaba para siempre (sin minutas hasta reiniciar,
    la siguiente grabacion se unia a ella, _pipeline_idle() nunca True)."""
    grabador, _, _ = app
    grabador.start_result = OSError('sin microfono')
    detector, bandeja = _reconexion(app, monkeypatch)

    detector.on_call_started()

    assert bandeja.llamadas == ['request_continuation', 'cancel_continuation']


def test_si_start_funciona_la_continuacion_sigue_esperando(app, monkeypatch):
    detector, bandeja = _reconexion(app, monkeypatch)

    detector.on_call_started()

    assert 'request_continuation' in bandeja.llamadas
    assert 'cancel_continuation' not in bandeja.llamadas


def test_si_ya_se_grababa_no_se_cancela_la_continuacion(app, monkeypatch):
    """start() -> False: hay otra grabacion en curso (la de 'Grabar ahora') que al
    terminar se unira a la sesion que espera. Cancelar romperia esa union; si esa
    grabacion se descarta, _cancel_recording deshace la espera."""
    grabador, _, _ = app
    grabador.start_result = False
    detector, bandeja = _reconexion(app, monkeypatch)

    detector.on_call_started()

    assert 'cancel_continuation' not in bandeja.llamadas
