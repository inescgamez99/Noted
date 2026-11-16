"""El menu de la bandeja, agrupado por uso, y «Descartar…» que pregunta antes.

16/11/2026: con «Capturar pantalla de Teams» recien puesta encima de «Cancelar
grabacion (sin guardar)», la opcion que se pulsa varias veces por llamada quedaba
a un clic de la que borra la reunion entera, sin preguntar y sin vuelta atras.

LO QUE PROTEGEN
1. El orden: arriba lo que se pulsa durante la llamada, luego los interruptores
   con su marca, y parar y descartar aparte.
2. Descartar pregunta, y descarta lo que habia al pulsar, no lo que haya al
   confirmar.
3. «Grabar ahora» y «Parar» hacen solo lo suyo: el menu se pinta antes del clic.
4. El tooltip dice que micro graba sin pasarse del limite de Windows.

Se construye el menu de verdad (pystray) sin pintarlo.
"""
import ctypes
import threading
from pathlib import Path

import pystray
import pytest

import mic_status
import tray_app as ta

pytestmark = pytest.mark.unit

SEP = '---'
JABRA = mic_status.MicState(name='Headset Microphone (Contoso USB)', muted=False)


class Grabador:
    def __init__(self, grabando):
        self.is_recording = grabando
        self.started = []
        self.stopped = 0

    def start(self, path):
        self.started.append(path)
        self.is_recording = True

    def stop(self):
        self.stopped += 1
        self.is_recording = False


def s():
    return ta._STR.get(ta.get_ui_language(), ta._STR['en'])


def visibles(app):
    """Lo que se veria: las opciones visibles, con un solo separador entre grupos
    y ninguno en los extremos, como los deja pystray."""
    out = []
    for item in app._build_menu().items:
        if not item.visible:
            continue
        text = SEP if item is pystray.Menu.SEPARATOR else item.text
        if text == SEP and (not out or out[-1] == SEP):
            continue
        out.append(text)
    while out and out[-1] == SEP:
        out.pop()
    return out


def opcion(app, clave):
    return next(i for i in app._build_menu().items if i.text == s()[clave])


@pytest.fixture
def grabando(tray_factory):
    app = tray_factory(recorder=Grabador(True))
    app._recording_path = Path('2026-11-16_10-32_Comite_Semanal.wav')
    app._rec_mode = 'background'
    app._mic_state = JABRA
    return app


@pytest.fixture
def parado(tray_factory):
    app = tray_factory(recorder=Grabador(False))
    app._mic_state = JABRA
    return app


# ── 1. El orden ───────────────────────────────────────────────────────────

def test_grabando_lo_de_la_llamada_arriba_y_descartar_aparte(grabando):
    t = s()
    assert visibles(grabando) == [
        t['cap_menu'], t['add_context'], SEP,
        t['switch_live'], t['mic_mute'], SEP,
        t['stop'], t['cancel_recording'], SEP,
        t['view_minutes'], SEP, t['quit']]


def test_sin_grabar(parado):
    t = s()
    assert visibles(parado) == [t['record_now'], t['record_now_live'], SEP, t['mic_mute'], SEP,
                                t['view_minutes'], SEP, t['quit']]


def test_sin_grabar_y_procesando_se_puede_descartar_la_reunion(parado):
    parado._current_job = {'stem': '20261116_1032_Comite_Semanal'}
    t = s()
    assert visibles(parado) == [t['record_now'], t['record_now_live'], SEP, t['mic_mute'], SEP,
                                t['cancel_job'], SEP, t['view_minutes'], SEP, t['quit']]


def test_sin_micro_legible_no_hay_opcion_de_micro(parado):
    parado._mic_state = None
    assert s()['mic_mute'] not in visibles(parado)


def test_en_vivo_queda_marcado_y_gris_porque_no_se_apaga(grabando):
    assert opcion(grabando, 'switch_live').checked is False
    assert opcion(grabando, 'switch_live').enabled is True
    grabando._rec_mode = 'live'
    assert opcion(grabando, 'switch_live').checked is True
    assert opcion(grabando, 'switch_live').enabled is False


def test_los_textos_de_lo_que_abre_una_ventana_acaban_en_puntos_suspensivos():
    """Convencion de Windows: «…» avisa de que la opcion pide algo mas."""
    for idioma, t in ta._STR.items():
        for clave in ('add_context', 'cancel_recording', 'cancel_job'):
            assert t[clave].endswith('…'), f"{idioma}:{clave}"


# ── 2. Descartar pregunta ─────────────────────────────────────────────────

def test_pulsar_descartar_pregunta_por_lo_que_se_graba(grabando, monkeypatch):
    pedidos, hecho = [], threading.Event()
    monkeypatch.setattr(grabando, '_discard_if_confirmed',
                        lambda target, key: pedidos.append((target, key)) or hecho.set())
    grabando._confirm_cancel()
    assert hecho.wait(5)
    assert pedidos == [(grabando._recording_path, 'confirm_discard_rec')]


def test_pulsar_descartar_sin_grabar_pregunta_por_la_reunion(parado, monkeypatch):
    parado._pipeline_queued = ['20261116_1032_Comite_Semanal']
    pedidos, hecho = [], threading.Event()
    monkeypatch.setattr(parado, '_discard_if_confirmed',
                        lambda target, key: pedidos.append((target, key)) or hecho.set())
    parado._confirm_cancel()
    assert hecho.wait(5)
    assert pedidos == [('20261116_1032_Comite_Semanal', 'confirm_discard_job')]


def test_sin_nada_que_descartar_no_pregunta(parado, monkeypatch):
    monkeypatch.setattr(ta, '_ask_yes_no', lambda *a: pytest.fail('pregunto sin nada que descartar'))
    parado._confirm_cancel()


def descartes(app, monkeypatch, respuesta):
    hechos, avisos = [], []
    monkeypatch.setattr(ta, '_ask_yes_no', lambda title, text: respuesta)
    monkeypatch.setattr(app, '_cancel_recording', lambda: hechos.append(True))
    monkeypatch.setattr(app, '_notify', lambda t, m: avisos.append(m))
    return hechos, avisos


def test_si_dice_que_no_no_se_descarta(grabando, monkeypatch):
    hechos, _ = descartes(grabando, monkeypatch, False)
    assert grabando._discard_if_confirmed(grabando._recording_path, 'confirm_discard_rec') is False
    assert hechos == []


def test_si_dice_que_si_se_descarta(grabando, monkeypatch):
    hechos, _ = descartes(grabando, monkeypatch, True)
    assert grabando._discard_if_confirmed(grabando._recording_path, 'confirm_discard_rec') is True
    assert hechos == [True]


def test_si_la_grabacion_paro_mientras_confirmaba_no_se_toca_nada(grabando, monkeypatch):
    """Colgo la llamada con el aviso abierto: lo que queda ya es otra cosa (la
    reunion procesandose), y el usuario no confirmo descartar eso."""
    hechos, avisos = descartes(grabando, monkeypatch, True)
    target = grabando._recording_path
    grabando._recorder.is_recording = False
    grabando._current_job = {'stem': '20261116_1032_Comite_Semanal'}
    assert grabando._discard_if_confirmed(target, 'confirm_discard_rec') is False
    assert hechos == []
    assert avisos == [s()['discard_changed']]


def test_la_reunion_procesandose_se_descarta_si_sigue_siendo_la_misma(parado, monkeypatch):
    hechos, _ = descartes(parado, monkeypatch, True)
    parado._current_job = {'stem': '20261116_1032_Comite_Semanal'}
    assert parado._discard_if_confirmed('20261116_1032_Comite_Semanal', 'confirm_discard_job') is True
    assert hechos == [True]


@pytest.mark.parametrize('boton, esperado', [(6, True), (7, False)])
def test_el_aviso_de_windows_tiene_no_por_defecto(monkeypatch, boton, esperado):
    pedidos = []
    monkeypatch.setattr(ctypes.windll.user32, 'MessageBoxW',
                        lambda hwnd, text, title, flags: pedidos.append(flags) or boton)
    assert ta._ask_yes_no('Noted', s()['confirm_discard_rec']) is esperado
    flags = pedidos[0]
    assert flags & 0x4, "Si/No"
    assert flags & 0x100, "con 'No' por defecto, un Enter no descarta"
    assert flags & 0x40000, "encima de Teams"


# ── 3. Cada opcion hace solo lo suyo ──────────────────────────────────────

def test_grabar_ahora_no_para_una_grabacion_que_empezo_entretanto(grabando):
    grabando._record_now()
    assert grabando._recorder.started == [] and grabando._recorder.is_recording


def test_parar_no_empieza_una_grabacion_si_ya_habia_parado(parado):
    parado._stop_recording()
    assert parado._recorder.stopped == 0 and parado._recorder.started == []


# ── 4. El tooltip ─────────────────────────────────────────────────────────

def test_el_tooltip_largo_se_recorta_sin_perder_el_silencio(tray):
    tray._mic_state = mic_status.MicState(name='Microphone Array ' + 'x' * 120, muted=True)
    title = tray._mic_decorated_title('Noted - Recording 12:34 | Transcribiendo la reunion anterior')
    assert len(title) == ta._TIP_MAX and title.endswith('…')
    assert s()['mic_tip_muted'] in title


def test_el_tooltip_cabe_en_el_de_windows(tray):
    """Con un caracter de mas, pystray lanza ValueError y el tooltip se congela."""
    from pystray._util import win32
    tray._mic_state = mic_status.MicState(name='y' * 300, muted=False)
    win32.NOTIFYICONDATAW().szTip = tray._mic_decorated_title('Noted - Recording 00:01')
