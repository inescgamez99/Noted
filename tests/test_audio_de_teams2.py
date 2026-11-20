"""El audio de Teams 2.0 vive en subprocesos (msedgewebview2.exe) de ms-teams.exe.

Las comprobaciones de sesion de audio y de micro solo miraban los PIDs que se
llaman como Teams, asi que con Teams 2.0 no veian la llamada. Ademas la del
micro construia AudioSession con un IAudioSessionControl (sin GetProcessId):
cada sesion lanzaba AttributeError, el except la saltaba y devolvia siempre
False. Sin audio de Windows: pycaw se sustituye por dobles.
"""
import os

import pytest
from pycaw import pycaw

import teams_detector as td

pytestmark = pytest.mark.unit

TEAMS = 100
WEBVIEW = 200      # hijo de Teams
NIETO = 300        # hijo del webview
AJENO = 900        # otro programa


@pytest.fixture
def arbol(monkeypatch):
    padres = {WEBVIEW: TEAMS, NIETO: WEBVIEW, AJENO: 1, 1: 0}
    consultas = []

    def padre(pid):
        consultas.append(pid)
        return padres.get(pid)
    monkeypatch.setattr(td, '_parent_pid', padre)
    return consultas


class Sesion:
    def __init__(self, pid, estado=1):
        self.ProcessId, self.State = pid, estado


# ── El arbol de Teams ────────────────────────────────────────────────────

def test_un_subproceso_de_teams_cuenta_como_teams(arbol):
    t = td._teams_audio_pids([TEAMS])
    assert TEAMS in t and WEBVIEW in t and NIETO in t
    assert AJENO not in t
    assert 0 not in t and None not in t


def test_cada_pid_se_consulta_una_sola_vez(arbol):
    t = td._teams_audio_pids([TEAMS])
    assert NIETO in t and NIETO in t
    assert arbol.count(NIETO) == 1


def test_un_ciclo_de_padres_no_cuelga(monkeypatch):
    monkeypatch.setattr(td, '_parent_pid', lambda pid: {5: 6, 6: 5}.get(pid))
    assert 5 not in td._teams_audio_pids([TEAMS])


def test_no_sube_mas_de_ocho_niveles(monkeypatch):
    monkeypatch.setattr(td, '_parent_pid', lambda pid: pid + 1)
    assert 1 not in td._teams_audio_pids([50])


# ── Las dos comprobaciones de audio ──────────────────────────────────────

def test_la_sesion_de_audio_de_un_subproceso_es_una_llamada(arbol, monkeypatch):
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetAllSessions',
                        staticmethod(lambda: [Sesion(AJENO), Sesion(WEBVIEW)]))
    assert td._check_audio_session([TEAMS]) is True


def test_una_sesion_inactiva_o_de_otro_no_cuenta(arbol, monkeypatch):
    monkeypatch.setattr(pycaw.AudioUtilities, 'GetAllSessions',
                        staticmethod(lambda: [Sesion(AJENO), Sesion(WEBVIEW, estado=0)]))
    assert td._check_audio_session([TEAMS]) is False


class Control:
    """Lo que devuelve GetSession: sin QueryInterface a IAudioSessionControl2
    no tiene GetProcessId, que es lo que fallaba."""

    def __init__(self, pid):
        self.pid = pid

    def QueryInterface(self, iface):
        assert iface is pycaw.IAudioSessionControl2
        return ('control2', self.pid)


class Enumerador:
    def __init__(self, pids):
        self.pids = pids

    def GetCount(self):
        return len(self.pids)

    def GetSession(self, i):
        return Control(self.pids[i])


@pytest.fixture
def micro(monkeypatch):
    def con_sesiones(*pids):
        enum = Enumerador(list(pids))

        class Manager:
            def GetSessionEnumerator(self):
                return enum

        class Interfaz:
            def QueryInterface(self, iface):
                return Manager()

        class Mic:
            def Activate(self, *a):
                return Interfaz()

        monkeypatch.setattr(pycaw.AudioUtilities, 'GetMicrophone', staticmethod(lambda: Mic()))
        monkeypatch.setattr(pycaw, 'AudioSession', lambda ctl: Sesion(ctl[1]))
    return con_sesiones


def test_el_micro_de_un_subproceso_de_teams_es_una_llamada(arbol, micro):
    micro(AJENO, NIETO)
    assert td._check_mic_session([TEAMS]) is True


def test_sin_micro_de_teams_no_hay_llamada(arbol, micro):
    micro(AJENO)
    assert td._check_mic_session([TEAMS]) is False


# ── La consulta real a Windows ───────────────────────────────────────────

@pytest.mark.skipif(os.name != 'nt', reason='NtQueryInformationProcess es de Windows')
def test_el_padre_de_este_proceso_es_el_que_dice_windows():
    import psutil
    assert td._parent_pid(os.getpid()) == psutil.Process().ppid()


@pytest.mark.skipif(os.name != 'nt', reason='OpenProcess es de Windows')
def test_un_pid_que_no_existe_no_tiene_padre():
    assert td._parent_pid(0x7FFFFFF0) is None
