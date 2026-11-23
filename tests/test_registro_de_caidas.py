"""crash.log: la pila de cada hilo si Noted muere por un fallo nativo.

Con pythonw, un desbordamiento de pila dentro de una DLL no deja nada en
noted.log. main() activa faulthandler hacia crash.log al arrancar.
"""
import faulthandler
import threading

import pytest

import main

pytestmark = pytest.mark.unit


@pytest.fixture
def sin_faulthandler(monkeypatch):
    llamadas = {}
    monkeypatch.setattr(faulthandler, 'is_enabled', lambda: False)
    monkeypatch.setattr(faulthandler, 'enable',
                        lambda file, all_threads: llamadas.update(file=file, all_threads=all_threads))
    monkeypatch.setattr(threading, 'stack_size', lambda n: llamadas.update(stack=n))
    return llamadas


def test_crash_log_recibe_la_pila_de_todos_los_hilos(tmp_path, sin_faulthandler):
    path = main._enable_crash_log(tmp_path)
    try:
        assert path == tmp_path / 'crash.log'
        assert sin_faulthandler['all_threads'] is True
        assert sin_faulthandler['file'].name == str(path)
        assert sin_faulthandler['stack'] == 16 * 1024 * 1024
    finally:
        sin_faulthandler['file'].close()
    assert '=== arranque' in path.read_text(encoding='utf-8')


def test_cada_arranque_se_anade_sin_borrar_los_anteriores(tmp_path, sin_faulthandler):
    (tmp_path / 'crash.log').write_text('caida anterior\n', encoding='utf-8')
    main._enable_crash_log(tmp_path)
    sin_faulthandler['file'].close()
    texto = (tmp_path / 'crash.log').read_text(encoding='utf-8')
    assert texto.startswith('caida anterior') and '=== arranque' in texto


def test_si_ya_hay_un_faulthandler_activo_no_se_toca(tmp_path, monkeypatch):
    monkeypatch.setattr(faulthandler, 'is_enabled', lambda: True)
    monkeypatch.setattr(faulthandler, 'enable', lambda **kw: pytest.fail('no debe cambiarse'))
    monkeypatch.setattr(threading, 'stack_size', lambda n: None)
    assert main._enable_crash_log(tmp_path) is None
    assert not (tmp_path / 'crash.log').exists()


def test_un_fallo_no_impide_arrancar(tmp_path, monkeypatch):
    monkeypatch.setattr(faulthandler, 'is_enabled', lambda: False)

    def falla(n):
        raise ValueError('tamano no admitido')
    monkeypatch.setattr(threading, 'stack_size', falla)
    no_existe = tmp_path / 'no' / 'existe'
    assert main._enable_crash_log(no_existe) is None
