"""Los hilos que graban o detectan llamadas suben su prioridad.

Mientras se graba una reunion, el mismo proceso puede estar transcribiendo otra
con varios hilos de Whisper. Escribir la grabacion, leer el audio de los demas
y detectar llamadas no deben esperar por ellos.
"""
import os
import threading

import pytest

import prio

pytestmark = pytest.mark.unit

windows = pytest.mark.skipif(os.name != 'nt', reason='prioridades de hilo de Windows')


def en_un_hilo(fn):
    out = []
    t = threading.Thread(target=lambda: out.append(fn()))
    t.start()
    t.join(5)
    return out[0]


@windows
def test_el_hilo_sube_a_la_prioridad_mas_alta_y_solo_el():
    def subir():
        return prio.boost_current_thread(), prio.current_thread_priority()
    assert en_un_hilo(subir) == (True, prio.THREAD_PRIORITY_HIGHEST)
    assert en_un_hilo(prio.current_thread_priority) != prio.THREAD_PRIORITY_HIGHEST, \
        "los demas hilos no cambian"


def test_fuera_de_windows_no_hace_nada(monkeypatch):
    monkeypatch.setattr(prio.os, 'name', 'posix')
    assert prio.boost_current_thread() is False
    assert prio.current_thread_priority() is None


@windows
def test_un_fallo_de_windows_no_rompe_nada(monkeypatch):
    def roto():
        raise OSError('sin kernel32')
    monkeypatch.setattr(prio, '_k32', roto)
    assert prio.boost_current_thread() is False
    assert prio.current_thread_priority() is None
