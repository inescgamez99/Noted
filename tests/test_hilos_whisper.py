"""Cuantos hilos usa la transcripcion del final, y cuanto tarda cada pista.

16/11/2026: una reunion de 30 min tardo mas de una hora en transcribirse. Medido
en un Core Ultra 7 165U, 8 hilos fueron un 20 % mas rapidos que los 12 de antes
(un hilo por nucleo fisico): ver transcriber.MAX_THREADS.
"""
import json
import logging

import pytest

import config
import transcriber as tr
from conftest import FakeInfo, FakeSegment

pytestmark = pytest.mark.unit


def ajustes(tr_dirs, **valores):
    (tr_dirs / 'settings.json').write_text(json.dumps(valores), encoding='utf-8')


@pytest.mark.parametrize('valor, esperado', [(6, 6), (1, 1), ('6', None), (0, None), (-2, None),
                                             (True, None), (2.5, None)])
def test_el_ajuste_solo_vale_si_es_un_entero_positivo(tr_dirs, valor, esperado):
    ajustes(tr_dirs, whisper_threads=valor)
    assert config.whisper_threads() == esperado


def test_sin_ajuste_decide_transcriber(tr_dirs):
    ajustes(tr_dirs)
    assert config.whisper_threads() is None


@pytest.mark.parametrize('fisicos, esperado', [(12, 8), (8, 8), (4, 4)])
def test_un_hilo_por_nucleo_fisico_hasta_ocho(tr_dirs, monkeypatch, fisicos, esperado):
    import psutil
    ajustes(tr_dirs)
    monkeypatch.setattr(psutil, 'cpu_count', lambda logical=True: fisicos)
    assert tr._threads() == esperado


def test_el_ajuste_manda_sobre_el_tope(tr_dirs, monkeypatch):
    import psutil
    ajustes(tr_dirs, whisper_threads=12)
    monkeypatch.setattr(psutil, 'cpu_count', lambda logical=True: 4)
    assert tr._threads() == 12


def test_sin_psutil_cuenta_los_procesadores(tr_dirs, monkeypatch):
    import psutil
    ajustes(tr_dirs)

    def roto(logical=True):
        raise RuntimeError('psutil')

    monkeypatch.setattr(psutil, 'cpu_count', roto)
    monkeypatch.setattr(tr.os, 'cpu_count', lambda: 6)
    assert tr._threads() == 6


def test_el_modelo_se_carga_con_esos_hilos(tr_dirs, monkeypatch):
    import faster_whisper
    import win_power
    pedidos = []
    monkeypatch.setattr(faster_whisper, 'WhisperModel',
                        lambda name, **kw: pedidos.append(kw['cpu_threads']) or object())
    monkeypatch.setattr(win_power, 'opt_out_of_efficiency_mode', lambda: True)
    monkeypatch.setattr(tr, '_model', None)
    monkeypatch.setattr(tr, '_model_name', None)
    ajustes(tr_dirs, whisper_threads=5)
    tr._get_model('medium')
    assert pedidos == [5]


def test_el_ritmo_de_cada_pasada_queda_en_el_log(caplog, monkeypatch):
    monkeypatch.setattr(tr.time, 'monotonic', lambda: 1000.0 + 900)
    with caplog.at_level(logging.INFO, logger='transcriber'):
        tr._log_pace('Track de Teams', 1800, 1000.0)
        tr._log_pace('Vacio', 0, 1000.0)
    assert 'Track de Teams: 30.0 min de audio en 15.0 min (0.50 veces su duracion)' in caplog.text
    assert 'Vacio: 0.0 min de audio en 15.0 min' in caplog.text


class DosPistas:
    def transcribe(self, audio, **_k):
        segs = [(0.0, 5.0, 'uno')]
        return (FakeSegment(a, b, t) for a, b, t in segs), FakeInfo('es', 10.0)


def test_las_dos_pistas_dejan_su_ritmo(tr_dirs, wav_factory, caplog):
    base = tr_dirs / 'recordings' / 'r.wav'
    wav_factory(base.with_suffix('.mic.wav'), seconds=1)
    wav_factory(base.with_suffix('.loop.wav'), seconds=1)
    with caplog.at_level(logging.INFO, logger='transcriber'):
        tr._transcribe_with_speakers(DosPistas(), base)
    assert 'Track de micrófono: ' in caplog.text and 'Track de Teams: ' in caplog.text
