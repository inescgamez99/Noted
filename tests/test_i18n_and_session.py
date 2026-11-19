"""Textos de la bandeja y fusion de reuniones por reconexion.

Dos cosas pequenas con consecuencias grandes:

- Un `KeyError` en los textos de la bandeja rompe la notificacion justo cuando
  la app quiere avisar de algo (una grabacion en cola, un fallo, que solo se
  esta captando el microfono).
- `has_pending_session` decide si una llamada de Teams es una RECONEXION a la
  reunion anterior. Si acierta de mas, fusiona dos reuniones distintas en una
  sola minuta.
"""
import pytest

import config
import tray_app as ta

pytestmark = pytest.mark.unit


# ── Textos de la bandeja ──────────────────────────────────────────────────

def test_los_dos_idiomas_tienen_las_mismas_claves():
    """Una clave que falta en un idioma es un KeyError en produccion, en el
    momento exacto en que la app intentaba avisar de algo."""
    assert set(ta._STR['es']) == set(ta._STR['en'])


def test_solo_hay_es_y_en():
    assert set(ta._STR) == {'es', 'en'}


@pytest.mark.parametrize('lang', ['es', 'en'])
def test_ningun_texto_esta_vacio(lang):
    vacios = [k for k, v in ta._STR[lang].items() if not (v or '').strip()]
    assert vacios == []


@pytest.mark.parametrize('lang', ['es', 'en'])
def test_todos_los_textos_admiten_el_formato_con_n(lang):
    """Las claves con contador usan {n}, las del microfono {name}, las del disco
    {gb} y {released}, la de la reunion sin titulo {time} y la de la caducidad de
    Claude {when}. Un .format(...) sobre una clave que lleve cualquier OTRA llave
    sin rellenar lanzaria KeyError: ese es el caso que este test caza."""
    for clave, texto in ta._STR[lang].items():
        try:
            texto.format(n=1, name='Microfono de prueba', gb=2.5, released=2.0, time='10:33',
                         when='2 días')
        except (KeyError, IndexError) as e:
            pytest.fail(f"_STR['{lang}']['{clave}'] revienta con .format(n=1): {e}")


@pytest.mark.parametrize('clave', ['recordings_queued', 'recordings_pending'])
def test_las_claves_con_contador_llevan_n_en_los_dos_idiomas(clave):
    for lang in ('es', 'en'):
        assert '{n}' in ta._STR[lang][clave], f"falta {{n}} en {lang}/{clave}"


def test_un_idioma_desconocido_cae_a_ingles(monkeypatch):
    """Todas las llamadas usan _STR.get(get_ui_language(), _STR['en']). Es una
    inconsistencia deliberada con config.get_ui_language(), que cae a 'es'."""
    monkeypatch.setattr(ta, 'get_ui_language', lambda: 'fr')
    s = ta._STR.get(ta.get_ui_language(), ta._STR['en'])
    assert s is ta._STR['en']


def test_get_ui_language_cae_a_es_sin_configuracion(monkeypatch):
    monkeypatch.setattr(config, '_settings', {})
    assert config.get_ui_language() == 'es'


def test_get_ui_language_lee_la_configuracion_en_cada_llamada(monkeypatch):
    """Cambiar el idioma en el panel debe surtir efecto sin reiniciar."""
    monkeypatch.setitem(config._settings, 'language', 'en')
    assert config.get_ui_language() == 'en'
    monkeypatch.setitem(config._settings, 'language', 'es')
    assert config.get_ui_language() == 'es'


def test_el_aviso_de_solo_microfono_advierte_de_los_auriculares():
    """Es el aviso del incidente del 10/09: una reunion entera grabada sin las
    voces de los demas. Si el texto pierde la mencion a los auriculares, el
    usuario no entiende que esta perdiendo."""
    assert 'auricular' in ta._STR['es']['mic_only'].lower()
    assert 'headset' in ta._STR['en']['mic_only'].lower()


# ── Normalizacion de nombres de reunion ──────────────────────────────────

@pytest.mark.parametrize('entrada, esperado', [
    ('Daily Scrum', 'dailyscrum'),
    ('Daily-Scrum', 'dailyscrum'),
    ('DAILY   SCRUM', 'dailyscrum'),
    ('Daily_Scrum!', 'dailyscrum'),
    ('', ''),
    (None, ''),
])
def test_norm_key_normaliza(tray, entrada, esperado):
    assert tray._norm_key(entrada) == esperado


def test_norm_key_elimina_los_acentos_junto_con_todo_lo_no_alfanumerico(tray):
    """CUIDADO: el regex es [^a-z0-9], asi que 'Reunión' pierde la tilde Y la
    letra. 'Reunion' y 'Reunión' NO dan la misma clave."""
    assert tray._norm_key('Reunion') == 'reunion'
    assert tray._norm_key('Reunión') == 'reuni' + 'n'


# ── has_pending_session: la heuristica de reconexion ─────────────────────

@pytest.fixture
def con_sesion(tray):
    def poner(nombre, finalizada=False):
        tray._session = {'key': tray._norm_key(nombre), 'finalized': finalizada}
        return tray
    return poner


def test_sin_sesion_no_hay_reconexion(tray):
    assert tray.has_pending_session('Daily Scrum') is False


def test_una_sesion_ya_cerrada_no_admite_reconexion(con_sesion):
    app = con_sesion('Daily Scrum', finalizada=True)
    assert app.has_pending_session('Daily Scrum') is False


def test_el_mismo_nombre_es_una_reconexion(con_sesion):
    app = con_sesion('Daily Scrum')
    assert app.has_pending_session('Daily Scrum') is True


def test_el_nombre_con_otro_formato_sigue_siendo_la_misma(con_sesion):
    """Teams cambia el titulo entre 'Daily-Scrum' y 'Daily Scrum'."""
    app = con_sesion('Daily Scrum')
    assert app.has_pending_session('DAILY_SCRUM') is True


def test_un_nombre_mas_corto_contenido_en_el_anterior_cuenta(con_sesion):
    """Es lo que salva el caso real: Teams acorta el titulo al reconectar."""
    app = con_sesion('Daily Scrum Proyecto X')
    assert app.has_pending_session('Daily Scrum') is True


def test_un_nombre_mas_largo_que_contiene_al_anterior_cuenta(con_sesion):
    app = con_sesion('Daily Scrum')
    assert app.has_pending_session('Daily Scrum Proyecto X') is True


def test_una_reunion_distinta_no_es_una_reconexion(con_sesion):
    app = con_sesion('Daily Scrum')
    assert app.has_pending_session('Comite de Direccion') is False


def test_un_nombre_vacio_nunca_es_una_reconexion(con_sesion):
    app = con_sesion('Daily Scrum')
    assert app.has_pending_session('') is False


@pytest.mark.parametrize('pasajero', ['Unión a la reunión', 'Join meeting',
                                      'Barra de control de uso compartido', 'Sharing control bar'])
def test_un_titulo_pasajero_de_teams_no_es_una_reconexion(con_sesion, pasajero):
    """20/10/2026, caso real: la reunion de las 09:21 quedo grabada como 'Unión a
    la reunión' (la ventana de Teams mientras te unes). A las 11:04, al unirse a
    otra llamada, su ventana tenia ese mismo titulo y salio 'Misma reunión
    detectada' para una reunion distinta. Esos titulos no son nombres."""
    assert con_sesion(pasajero).has_pending_session(pasajero) is False
    assert con_sesion('Daily Touchpoint').has_pending_session(pasajero) is False
    assert con_sesion(pasajero).has_pending_session('Daily Touchpoint') is False


def test_una_sesion_sin_nombre_no_admite_reconexion(con_sesion):
    """Una grabacion manual no tiene nombre de reunion: no debe absorber la
    siguiente llamada que llegue."""
    app = con_sesion('')
    assert app.has_pending_session('Daily Scrum') is False


def test_DEFECTO_la_subcadena_produce_falsos_positivos(con_sesion):
    """DEFECTO CONOCIDO: la regla es contencion bidireccional sobre la clave
    normalizada, asi que un nombre corto casa con cualquiera que lo contenga.
    'AI' -> 'ai' esta dentro de 'mailreview', asi que una reunion llamada 'AI'
    y otra 'Mail Review' se fusionarian en una sola minuta.

    Se fija el comportamiento actual. El arreglo razonable seria exigir una
    longitud minima o comparar por palabras completas."""
    app = con_sesion('Mail Review')
    assert app.has_pending_session('AI') is True, \
        "si esto cambia, el falso positivo se arreglo"
