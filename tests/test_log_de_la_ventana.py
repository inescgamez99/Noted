"""El panel (app_window.py, otro proceso) escribe sus errores en noted.log."""
import logging

import pytest

import app_window

pytestmark = pytest.mark.unit


@pytest.fixture
def raiz_limpia():
    root = logging.getLogger()
    antes, nivel = list(root.handlers), root.level
    yield root
    for h in root.handlers:
        if h not in antes:
            root.removeHandler(h)
            h.close()
    root.setLevel(nivel)


def test_los_errores_del_panel_llegan_al_log_con_su_prefijo(tmp_path, raiz_limpia):
    log_file = tmp_path / 'noted.log'
    raiz_limpia.setLevel(logging.WARNING)
    assert app_window._setup_window_logging(log_file) is True
    logging.getLogger('app_window').error('no se pudo guardar el proyecto')
    for h in raiz_limpia.handlers:
        h.flush()
    texto = log_file.read_text(encoding='utf-8')
    assert 'ERROR window.app_window: no se pudo guardar el proyecto' in texto
    assert raiz_limpia.level == logging.INFO


def test_sin_poder_abrir_el_log_el_panel_arranca_igual(tmp_path, raiz_limpia):
    assert app_window._setup_window_logging(tmp_path / 'no' / 'existe' / 'noted.log') is False


def test_sin_ruta_usa_el_log_de_la_app(tmp_path, raiz_limpia, monkeypatch):
    import config
    monkeypatch.setattr(config, 'LOG_FILE', tmp_path / 'app.log')
    assert app_window._setup_window_logging() is True
    assert (tmp_path / 'app.log').exists()
