"""El microfono por defecto: cual es y si esta silenciado.

INCIDENTE QUE ORIGINO ESTOS TESTS (2026-10-08)
Una prueba de "Grabar ahora" sin auriculares grabo 27 s y el resultado fue "sin
voz". El micro integrado del portatil estaba SILENCIADO en Windows (pycaw:
GetMute = True), mientras el Jabra que se usa en las llamadas no lo estaba:
Windows guarda un silencio por dispositivo. La app abrio el micro sin problema
(Windows lo registra) y solo se supo al final, tras transcribir. En una reunion
presencial de una hora se habria perdido la hora entera.

Ahora el icono de Noted lleva una marca roja y el tooltip lo dice, el menu
permite activarlo, y al empezar a grabar se avisa en el acto.

Nada de esto toca un dispositivo real: el lector de Windows se sustituye.
"""
import threading

import pytest

import mic_status
import tray_app as ta

pytestmark = pytest.mark.unit

MUTED = mic_status.MicState(name='Microphone Array (Intel Smart Sound)', muted=True)
ACTIVE = mic_status.MicState(name='Headset Microphone (Jabra EVOLVE 20 MS)', muted=False)


class IconoFalso:
    """Lo que pystray expone y la app usa: imagen, tooltip y repintar el menu."""

    def __init__(self):
        self.icon = None
        self.title = None
        self.menus = 0

    def update_menu(self):
        self.menus += 1


@pytest.fixture
def bandeja(tray, monkeypatch):
    tray._icon = IconoFalso()
    avisos = []
    monkeypatch.setattr(tray, '_notify', lambda t, m: avisos.append((t, m)))
    monkeypatch.setattr(ta.mic_status, 'com_init', lambda: None)
    tray.avisos = avisos
    return tray


def lector(monkeypatch, *estados):
    """read_default_mic devuelve estos estados, uno por llamada."""
    it = iter(estados)
    monkeypatch.setattr(ta.mic_status, 'read_default_mic', lambda: next(it))


def cadenas():
    """El mismo idioma que resolveria la app."""
    return ta._STR.get(ta.get_ui_language(), ta._STR['en'])


# ── El nombre que se ensena ───────────────────────────────────────────────

def test_short_name_quita_el_prefijo_de_instancia_de_windows():
    assert (mic_status.short_name('Microphone Array (2- Intel Smart Sound)')
            == 'Microphone Array (Intel Smart Sound)')


def test_short_name_recorta_los_nombres_largos():
    out = mic_status.short_name(
        'Microphone Array (Intel Smart Sound Technology for Digital Microphones)')
    assert len(out) <= mic_status._MAX_NAME and out.endswith('...')


def test_short_name_nunca_devuelve_vacio():
    assert mic_status.short_name('') == '?'
    assert mic_status.short_name(None) == '?'


# ── El modulo es informativo: nunca lanza ─────────────────────────────────

def _getmicrophone_roto():
    raise OSError('sin dispositivo de audio')


def test_leer_devuelve_none_si_windows_falla(monkeypatch):
    pycaw = pytest.importorskip('pycaw.pycaw')
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetMicrophone', staticmethod(_getmicrophone_roto))
    assert mic_status.read_default_mic() is None


def test_leer_devuelve_none_si_no_hay_microfono(monkeypatch):
    """La maquina del CI no tiene tarjeta de sonido: tiene que dar None, no lanzar."""
    pycaw = pytest.importorskip('pycaw.pycaw')
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetMicrophone', staticmethod(lambda: None))
    assert mic_status.read_default_mic() is None


def test_cambiar_devuelve_false_si_windows_falla(monkeypatch):
    pycaw = pytest.importorskip('pycaw.pycaw')
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetMicrophone', staticmethod(_getmicrophone_roto))
    assert mic_status.set_default_mic_muted(False) is False


# ── Vida de los objetos COM: el fallo que tumbo el daemon ─────────────────
#
# INCIDENTE (2026-10-08, el mismo dia). La primera version convertia el puntero
# con ctypes.cast(). Eso crea un segundo puntero al mismo objeto COM sin AddRef,
# y al recolectar los dos se libera dos veces. Leyendo cada 2 s, el daemon cayo
# con 0xC0000005 a los 100 s de arrancar. Aislado: 1.200 lecturas en 3 hilos
# dieron "access violation" en comtypes Release(). Con QueryInterface, cero.

def test_el_endpoint_se_obtiene_con_queryinterface_no_con_cast():
    """El patron correcto, fijado: Activate() y luego QueryInterface()."""
    pytest.importorskip('pycaw.pycaw')
    esperado = object()

    class Interfaz:
        def QueryInterface(self, iid):
            return esperado

    class Dispositivo:
        def Activate(self, iid, ctx, params):
            return Interfaz()

    assert mic_status._endpoint_volume(Dispositivo()) is esperado, \
        "no paso por QueryInterface: un cast() vuelve a liberar dos veces"


@pytest.mark.integration
def test_leer_el_micro_muchas_veces_en_varios_hilos_no_corrompe_memoria():
    """La reproduccion real del incidente, contra el dispositivo de verdad.

    Corre en un proceso aparte porque una liberacion doble no siempre tumba el
    proceso: a veces solo deja 'Exception ignored ... access violation' en stderr.
    Eso es lo que se busca.

    ES PROBABILISTA: con cast() reventó en 1.200 lecturas y no en 450. La
    proteccion determinista es el test de QueryInterface de arriba; este es una
    comprobacion extra contra el micro real.
    """
    import subprocess
    import sys
    if mic_status.read_default_mic() is None:
        pytest.skip('no hay microfono en esta maquina')
    script = (
        'import threading, mic_status\n'
        'def p():\n'
        '    mic_status.com_init()\n'
        '    for _ in range(400): mic_status.read_default_mic()\n'
        'hs = [threading.Thread(target=p) for _ in range(3)]\n'
        '[h.start() for h in hs]; [h.join() for h in hs]\n'
        'print("FIN")\n'
    )
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent   # no PROJECT_DIR: los tests lo redirigen
    r = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                       timeout=240, cwd=str(raiz))
    assert 'access violation' not in r.stderr.lower(), r.stderr[-800:]
    assert 'FIN' in r.stdout, f"el proceso no termino:\n{r.stderr[-800:]}"


# ── El icono ──────────────────────────────────────────────────────────────

def test_micro_silenciado_pone_la_marca_y_lo_dice_el_tooltip(bandeja, monkeypatch):
    """EL TEST CLAVE: es lo que habria avisado el 2026-10-08 antes de grabar."""
    lector(monkeypatch, MUTED)
    bandeja._mic_poll_once()
    assert bandeja._icon.icon is not ta._ICON_IDLE, "no se pinto la marca roja"
    assert cadenas()['mic_tip_muted'] in bandeja._icon.title


def test_micro_activo_deja_el_icono_normal(bandeja, monkeypatch):
    lector(monkeypatch, ACTIVE)
    bandeja._mic_poll_once()
    assert bandeja._icon.icon is ta._ICON_IDLE
    assert bandeja._icon.title == f'Noted · {ACTIVE.name}', "el tooltip dice que micro graba"


def test_al_activarlo_se_quita_la_marca(bandeja, monkeypatch):
    lector(monkeypatch, MUTED, ACTIVE)
    bandeja._mic_poll_once()
    bandeja._mic_poll_once()
    assert bandeja._icon.icon is ta._ICON_IDLE
    assert cadenas()['mic_tip_muted'] not in bandeja._icon.title


def test_la_marca_sobrevive_cuando_el_icono_cambia_de_estado(bandeja, monkeypatch):
    """El icono se repinta al grabar y al procesar: la marca no puede perderse."""
    lector(monkeypatch, MUTED)
    bandeja._mic_poll_once()
    bandeja._set_icon(ta._ICON_RECORDING, 'Noted - Recording 00:00')
    assert bandeja._icon.icon is not ta._ICON_RECORDING
    assert bandeja._icon.title.startswith('Noted - Recording 00:00')
    assert cadenas()['mic_tip_muted'] in bandeja._icon.title


def test_sin_cambios_no_se_repinta(bandeja, monkeypatch):
    """Se lee cada 2 s; repintar el menu en cada lectura seria ruido."""
    lector(monkeypatch, ACTIVE, ACTIVE)
    bandeja._mic_poll_once()
    menus = bandeja._icon.menus
    bandeja._mic_poll_once()
    assert bandeja._icon.menus == menus


def test_si_no_se_puede_leer_no_cambia_nada(bandeja, monkeypatch):
    """Sin pycaw o sin micro, la app queda exactamente como antes: sin marca y
    sin opcion en el menu."""
    lector(monkeypatch, None)
    bandeja._mic_poll_once()
    assert not bandeja._mic_muted()
    assert bandeja._mic_decorated_title('Noted') == 'Noted'
    assert opcion_micro(bandeja).visible is False


def test_un_error_del_lector_no_rompe_nada(bandeja, monkeypatch):
    def roto():
        raise RuntimeError('COM caido')
    monkeypatch.setattr(ta.mic_status, 'read_default_mic', roto)
    bandeja._mic_poll_once()
    assert bandeja._mic_state is None


# ── El menu ───────────────────────────────────────────────────────────────

def opcion_micro(bandeja):
    """La opcion del micro en el menu de verdad (pystray), sin pintar nada."""
    from types import SimpleNamespace
    bandeja._recorder = bandeja._recorder or SimpleNamespace(is_recording=False)
    return next(i for i in bandeja._build_menu().items if i.text == cadenas()['mic_mute'])


def test_silenciado_el_menu_lo_marca(bandeja):
    bandeja._mic_state = MUTED
    assert opcion_micro(bandeja).checked is True


def test_activo_el_menu_no_lo_marca(bandeja):
    bandeja._mic_state = ACTIVE
    assert opcion_micro(bandeja).checked is False


def test_el_clic_invierte_el_silencio(bandeja, monkeypatch):
    pedidos = []
    monkeypatch.setattr(ta.mic_status, 'set_default_mic_muted',
                        lambda m: pedidos.append(m) or True)
    lector(monkeypatch, ACTIVE)            # la relectura despues del clic
    bandeja._mic_state = MUTED
    bandeja._toggle_mic_mute()
    assert pedidos == [False], "silenciado -> tiene que pedir activarlo"
    assert bandeja._mic_state == ACTIVE


def test_si_windows_rechaza_el_cambio_no_se_finge(bandeja, monkeypatch):
    monkeypatch.setattr(ta.mic_status, 'set_default_mic_muted', lambda m: False)
    bandeja._mic_state = MUTED
    bandeja._toggle_mic_mute()
    assert bandeja._mic_state == MUTED


# ── El aviso al empezar a grabar ──────────────────────────────────────────

def test_al_grabar_con_el_micro_silenciado_se_avisa(bandeja, monkeypatch):
    lector(monkeypatch, MUTED)
    bandeja.warn_if_mic_muted()
    assert [m for _t, m in bandeja.avisos] == [
        cadenas()['mic_muted_warning'].format(name=MUTED.name)]


def test_con_el_micro_activo_no_se_avisa(bandeja, monkeypatch):
    lector(monkeypatch, ACTIVE)
    bandeja.warn_if_mic_muted()
    assert bandeja.avisos == []


def test_si_no_se_sabe_no_se_avisa(bandeja, monkeypatch):
    """Una falsa alarma en cada grabacion acaba con nadie leyendo el aviso."""
    lector(monkeypatch, None)
    bandeja.warn_if_mic_muted()
    assert bandeja.avisos == []


class GrabadorFalso:
    def __init__(self):
        self.is_recording = False

    def start(self, path):
        self.is_recording = True


def test_grabar_ahora_lanza_la_comprobacion(tray_factory, monkeypatch):
    """Que exista el aviso no basta: el boton tiene que llamarlo."""
    import teams_chat
    monkeypatch.setattr(teams_chat, 'send_recording_notice', lambda: None)
    app = tray_factory(recorder=GrabadorFalso())
    llamado = threading.Event()
    monkeypatch.setattr(app, 'warn_if_mic_muted', llamado.set)
    try:
        app._record_now()
        assert llamado.wait(5), "Grabar ahora no comprobo el micro"
    finally:
        app._ticker_stop.set()


# ── Textos ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('clave', ['mic_mute', 'mic_tip_muted', 'mic_muted_warning'])
@pytest.mark.parametrize('idioma', sorted(ta._STR))
def test_textos_del_micro_en_todos_los_idiomas(idioma, clave):
    assert ta._STR[idioma].get(clave), f"falta {clave} en '{idioma}'"
