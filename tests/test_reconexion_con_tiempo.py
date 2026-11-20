"""has_pending_session exige volver pronto, ademas del mismo nombre.

Solo con el nombre, una reunion distinta se pegaba a la anterior: dos llamadas
seguidas cuya ventana se llamaba igual al entrar ('Meeting join') acabaron en
una sola minuta porque la primera aun se estaba procesando.
"""
import time

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def sesion(tray):
    def poner(nombre, hace_min=None, campo='ended_at'):
        tray._session = {'key': tray._norm_key(nombre), 'finalized': False}
        if hace_min is not None:
            tray._session[campo] = time.time() - hace_min * 60
        return tray
    return poner


def test_volver_a_los_pocos_minutos_es_reconexion(sesion):
    assert sesion('Daily Scrum', hace_min=5).has_pending_session('Daily Scrum') is True


def test_volver_pasados_15_minutos_ya_es_otra_reunion(sesion):
    assert sesion('Daily Scrum', hace_min=16).has_pending_session('Daily Scrum') is False


@pytest.mark.parametrize('nombre', ['Meeting join', 'manual', 'Meeting'])
def test_con_un_nombre_generico_solo_si_es_casi_inmediato(sesion, nombre):
    assert sesion(nombre, hace_min=2).has_pending_session(nombre) is True
    assert sesion(nombre, hace_min=4).has_pending_session(nombre) is False


def test_sin_marca_de_fin_cuenta_la_ultima_actividad(sesion):
    assert sesion('Daily Scrum', hace_min=30, campo='last_at').has_pending_session('Daily Scrum') is False


def test_sin_ninguna_marca_de_tiempo_decide_el_nombre_como_antes(sesion):
    assert sesion('Daily Scrum').has_pending_session('Daily Scrum') is True


def test_al_acabar_una_grabacion_la_sesion_apunta_cuando(tray, tr_dirs):
    antes = time.time()
    tray._register_part_recorded(tr_dirs / 'recordings' / '2026-11-20_10-00_Daily_Scrum.wav')
    assert tray._session['ended_at'] >= antes

    tray._session['awaiting'] = True
    tray._register_part_recorded(tr_dirs / 'recordings' / '2026-11-20_10-07_Daily_Scrum.wav')
    assert len(tray._session['parts']) == 2
    assert tray._session['ended_at'] >= antes
