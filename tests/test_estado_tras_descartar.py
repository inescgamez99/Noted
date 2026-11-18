""".pipeline_status.json al parar o descartar una grabacion.

29/10/2026: al descartar una grabacion, la ultima escritura del ticker llego
despues de la de set_recording(False) y el estado se quedo en "grabando 03:29".
El panel lo mostraba y la puerta de reinicio no dejaba reiniciar, hasta que
empezo la grabacion siguiente.
"""
import json
import threading
import time

import pytest

pytestmark = pytest.mark.unit


def test_la_ultima_escritura_del_ticker_no_deja_la_grabacion_en_curso(tray, tr_dirs, monkeypatch):
    tray._recording_path = tr_dirs / 'recordings' / '2026-10-29_09-08_manual.wav'
    tray._recording_start = time.time() - 209

    dumps = json.dumps
    dentro, seguir = threading.Event(), threading.Event()

    def dumps_del_ticker_lento(obj, *a, **k):
        # El ticker ya leyo el estado "grabando" y el sistema le quita la CPU
        # justo antes de escribirlo.
        if threading.current_thread().name == 'TickerFalso':
            dentro.set()
            seguir.wait(5)
        return dumps(obj, *a, **k)

    monkeypatch.setattr(json, 'dumps', dumps_del_ticker_lento)
    ticker = threading.Thread(target=tray._write_status, name='TickerFalso')
    ticker.start()
    assert dentro.wait(5)

    parar = threading.Thread(target=tray.set_recording, args=(False,))
    parar.start()
    time.sleep(0.3)          # sin el lock, aqui ya se habria escrito el estado vacio
    seguir.set()
    ticker.join(5)
    parar.join(5)

    jobs = json.loads((tr_dirs / '.pipeline_status.json').read_text(encoding='utf-8'))['jobs']
    assert jobs == [], f"se quedo una grabacion que ya no existe: {jobs}"


def test_el_constructor_crea_el_lock(monkeypatch):
    """tray_factory no pasa por __init__; este test si, sin arrancar sus hilos."""
    import tray_app as ta

    class SinArrancar:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(ta.threading, 'Thread', SinArrancar)
    monkeypatch.setattr(ta.TrayApp, '_start_daily_brain_scan', lambda self: None, raising=False)
    app = ta.TrayApp(recorder=None, detector=None)
    assert app._status_lock.acquire(blocking=False)
    app._status_lock.release()
