"""El hilo Tk de los avisos (tk_thread.py), con un tkinter de mentira.

INCIDENTE: el primer aviso del dia fallaba con "'NoneType' object has no
attribute 'after'": el hilo nacia con el primer aviso y solo tenia 5 s para
arrancar. Ahora arranca con Noted, espera hasta START_TIMEOUT, nunca hay dos,
y si no esta listo lanza TkNotReady (un error claro que main reintenta).
"""
import threading
import time

import pytest

import tk_thread

pytestmark = pytest.mark.unit


class Root:
    creados = 0

    def __init__(self):
        Root.creados += 1
        self.despues = []
        self._fin = threading.Event()

    def withdraw(self):
        pass

    def after(self, ms, fn):
        self.despues.append(fn)

    def mainloop(self):
        self._fin.wait(5)

    def salir(self):
        self._fin.set()


@pytest.fixture
def tk_falso(monkeypatch):
    Root.creados = 0
    monkeypatch.setattr(tk_thread.tk, 'Tk', Root)
    monkeypatch.setattr(tk_thread, '_thread', None)
    monkeypatch.setattr(tk_thread, '_root', None)
    tk_thread._ready.clear()
    yield
    root = tk_thread._root
    if root is not None:
        root.salir()
    if tk_thread._thread is not None:
        tk_thread._thread.join(2)


def test_run_in_tk_espera_al_hilo_y_encola(tk_falso):
    hecho = []
    tk_thread.run_in_tk(lambda: hecho.append(1))
    root = tk_thread.get_root()
    assert len(root.despues) == 1
    root.despues[0]()
    assert hecho == [1]


def test_nunca_hay_dos_hilos_tk(tk_falso):
    for _ in range(5):
        tk_thread.start()
    tk_thread.get_root()
    assert Root.creados == 1


def test_si_no_arranca_a_tiempo_lanza_un_error_claro(tk_falso, monkeypatch):
    monkeypatch.setattr(tk_thread, '_tk_main', lambda: time.sleep(0.5))
    with pytest.raises(tk_thread.TkNotReady):
        tk_thread._ensure_running(timeout=0.05)


def test_si_tk_no_puede_arrancar_queda_en_el_log(tk_falso, monkeypatch, caplog):
    def roto():
        raise RuntimeError('sin pantalla')
    monkeypatch.setattr(tk_thread.tk, 'Tk', roto)
    with pytest.raises(tk_thread.TkNotReady):
        tk_thread._ensure_running(timeout=0.3)
    assert 'Tk no pudo arrancar' in caplog.text


def test_si_el_hilo_termina_se_relanza_con_el_siguiente_aviso(tk_falso):
    primero = tk_thread.get_root()
    primero.salir()
    tk_thread._thread.join(2)
    assert tk_thread._root is None
    segundo = tk_thread.get_root()
    assert segundo is not primero and Root.creados == 2


def test_un_error_en_un_callback_va_al_log(caplog):
    try:
        raise ValueError('fallo en el aviso')
    except ValueError as e:
        tk_thread._log_callback_error(type(e), e, e.__traceback__)
    assert 'Error en un callback de Tk' in caplog.text
