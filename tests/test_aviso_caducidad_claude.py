"""Aviso de que la sesion de Claude caduca pronto (antes de que fallen las minutas)."""
import datetime as dt

import pytest

import minutes_generator as mg
import tray_app

pytestmark = pytest.mark.unit

AHORA = 1_800_000_000.0


def _expira_en(horas):
    return dt.datetime.fromtimestamp(AHORA + horas * 3600, dt.timezone.utc)


@pytest.fixture
def avisos(tray_factory, monkeypatch):
    app = tray_factory()
    lista = []
    app._notify = lambda titulo, msg: lista.append(msg)
    monkeypatch.setattr(tray_app, 'get_ui_language', lambda: 'es')
    return app, lista


def _con_expiracion(monkeypatch, valor):
    monkeypatch.setattr(mg, 'claude_login_expiry', lambda: valor)


def test_avisa_cuando_quedan_dos_dias(avisos, monkeypatch):
    app, lista = avisos
    _con_expiracion(monkeypatch, _expira_en(48))
    app._check_claude_login(now=AHORA)
    assert len(lista) == 1 and '2 día' in lista[0] and 'claude auth login' in lista[0]


def test_en_el_ultimo_dia_lo_dice_en_horas(avisos, monkeypatch):
    app, lista = avisos
    _con_expiracion(monkeypatch, _expira_en(5))
    app._check_claude_login(now=AHORA)
    assert '5 hora' in lista[0]


@pytest.mark.parametrize('horas', [24 * 28, -1])      # lejos, o ya caducada (eso lo cubre minutes_pending)
def test_no_avisa_si_falta_mucho_o_ya_caduco(avisos, monkeypatch, horas):
    app, lista = avisos
    _con_expiracion(monkeypatch, _expira_en(horas))
    app._check_claude_login(now=AHORA)
    assert lista == []


def test_no_avisa_si_no_se_sabe(avisos, monkeypatch):
    app, lista = avisos
    _con_expiracion(monkeypatch, None)
    app._check_claude_login(now=AHORA)
    assert lista == []


def test_como_mucho_un_aviso_al_dia(avisos, monkeypatch):
    app, lista = avisos
    _con_expiracion(monkeypatch, _expira_en(48))
    app._check_claude_login(now=AHORA)
    app._check_claude_login(now=AHORA + 6 * 3600)
    assert len(lista) == 1
    app._check_claude_login(now=AHORA + 25 * 3600)
    assert len(lista) == 2


@pytest.mark.parametrize('salida, esperado', [
    ('{"loggedIn": true, "loginExpiresAt": "2026-12-17T00:12:17.999Z"}',
     dt.datetime(2026, 12, 17, 0, 12, 17, 999000, dt.timezone.utc)),
    ('{"loggedIn": true}', None),
    ('{"loggedIn": false, "loginExpiresAt": "2026-12-17T00:12:17.999Z"}', None),
    ('error: unknown command auth', None),
])
def test_la_caducidad_la_dice_claude_auth_status(monkeypatch, salida, esperado):
    monkeypatch.setattr(mg, '_get_claude_bin', lambda: 'claude')
    monkeypatch.setattr(mg.subprocess, 'run', lambda cmd, **k: type('R', (), {
        'stdout': salida, 'returncode': 0})())
    assert mg.claude_login_expiry() == esperado


def test_sin_cli_no_hay_caducidad(monkeypatch):
    monkeypatch.setattr(mg, '_get_claude_bin', lambda: None)
    assert mg.claude_login_expiry() is None
