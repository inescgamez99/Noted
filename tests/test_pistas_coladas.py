"""Pistas que se cuelan en la transcripcion.

Cuando Whisper duda con una ventana, a veces no la transcribe: devuelve el texto
de las pistas (hotwords: invitados, asunto de la cita y glosario). El 11/11/2026
eso tapo la mayor parte de lo que dijeron los demas en una reunion. Esos tramos
se rehacen sin pistas (transcriber._redo_echoes), en los cinco sitios donde se
llama a Whisper: las dos pistas, la mezcla entera, la mezcla por trozos, los
trozos durante la reunion y el cierre de lo hecho durante ella.
"""
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import live_transcriber as lt
import transcriber
import tray_app as ta
from conftest import FakeInfo, FakeSegment

pytestmark = pytest.mark.unit

SR = 16000
PISTAS = 'Ana Pérez Gil, Luis Martín Soto, Contoso - MCP Claude Code <> Azure'
ECO = 'Gil, Luis Martín Soto, Contoso'
STEM = '2026-11-11_10-08_manual'


class Modelo:
    """Con pistas devuelve `con`; sin ellas, lo que se dijo. Apunta cada llamada."""

    def __init__(self, con, sin=((0.5, 4.0, 'lo que se dijo'),)):
        self.con, self.sin = con, sin
        self.llamadas = []

    def transcribe(self, audio, **kw):
        largo = str(audio) if isinstance(audio, (str, Path)) else len(audio)
        self.llamadas.append((largo, kw))
        segs = self.con if kw.get('hotwords') else self.sin
        return (FakeSegment(*s) for s in segs), FakeInfo('es', duration=60.0)


def silencio(segundos):
    return np.zeros(int(segundos * SR), dtype=np.float32)


def escribir(path, segundos):
    sf.write(str(path), silencio(segundos), SR)
    return path


# ── Reconocer el eco ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('texto, eco', [
    (ECO, True),
    ('Gil, Luis Martín Soto, Contoso- MCP Claude Code', True),
    ('gil luis martin soto contoso code', True),     # sin tildes ni comas
    ('UY Agents MCP Semantic MCP', False),           # lee una slide: palabras propias
    ('Azure, ¿esto está mal o está bien?', False),
    ('¿Lo ves, Luis?', False),                        # nombra a un invitado
    ('Contoso', False),                               # una palabra sola no basta
])
def test_reconoce_el_eco_de_las_pistas(texto, eco):
    assert transcriber.is_hotword_echo(texto, PISTAS) is eco


def test_sin_pistas_no_hay_eco():
    assert not transcriber.is_hotword_echo(ECO, None)
    assert not transcriber.is_hotword_echo(ECO, '')


# ── Rehacer el tramo ─────────────────────────────────────────────────────────

ARGS = {'hotwords': PISTAS, 'language': 'es', 'beam_size': 1}


def test_sin_eco_ni_se_lee_el_audio_ni_se_rehace():
    m = Modelo(con=())
    segs = [(0.0, 5.0, 'hola'), (6.0, 9.0, 'que tal')]

    class SinLeer(transcriber._Audio):
        def read(self, a, b):
            raise AssertionError("sin eco no hace falta el audio")

    assert transcriber._redo_echoes(m, segs, SinLeer(silencio(10)), ARGS) == segs
    assert transcriber._redo_echoes(m, [(0.0, 5.0, ECO)], SinLeer(silencio(10)), {}) == [(0.0, 5.0, ECO)]
    assert m.llamadas == []


def test_el_tramo_va_del_segmento_bueno_anterior_al_siguiente_y_sin_pistas():
    m = Modelo(con=())
    segs = [(0.0, 5.0, 'hola'), (8.0, 30.0, ECO), (40.0, 45.0, 'adios')]
    out = transcriber._redo_echoes(m, segs, transcriber._Audio(silencio(60)), ARGS)
    assert out == [(0.0, 5.0, 'hola'), (5.5, 9.0, 'lo que se dijo'), (40.0, 45.0, 'adios')]
    [(largo, kw)] = m.llamadas
    assert largo == 35 * SR
    assert 'hotwords' not in kw and kw['language'] == 'es' and kw['beam_size'] == 1


def test_ecos_seguidos_se_rehacen_en_un_solo_tramo():
    m = Modelo(con=())
    segs = [(0.0, 5.0, 'hola'), (8.0, 30.0, ECO), (30.0, 60.0, ECO), (70.0, 75.0, 'fin')]
    transcriber._redo_echoes(m, segs, transcriber._Audio(silencio(80)), ARGS)
    assert [largo for largo, _kw in m.llamadas] == [65 * SR]


def test_un_eco_al_principio_empieza_en_el_y_uno_al_final_llega_al_final():
    m = Modelo(con=())
    transcriber._redo_echoes(m, [(10.0, 30.0, ECO), (40.0, 45.0, 'x')], transcriber._Audio(silencio(60)), ARGS)
    transcriber._redo_echoes(m, [(0.0, 5.0, 'x'), (8.0, 30.0, ECO)], transcriber._Audio(silencio(50)), ARGS)
    assert [largo for largo, _kw in m.llamadas] == [30 * SR, 45 * SR]


def test_si_sin_pistas_no_sale_nada_el_eco_se_quita():
    m = Modelo(con=(), sin=())
    out = transcriber._redo_echoes(m, [(0.0, 5.0, 'hola'), (8.0, 30.0, ECO)], transcriber._Audio(silencio(40)), ARGS)
    assert out == [(0.0, 5.0, 'hola')]


def test_de_un_fichero_se_lee_solo_el_tramo_en_mono_y_a_16_khz(tmp_path):
    estereo = tmp_path / 'e.wav'
    sf.write(str(estereo), np.ones((10 * SR, 2), dtype=np.float32) * 0.5, SR)
    audio = transcriber._Audio(estereo)
    assert audio.total == 10.0
    assert audio.read(2.0, 5.0).shape == (3 * SR,)
    a_8k = tmp_path / 'b.wav'
    sf.write(str(a_8k), np.zeros(4 * 8000, dtype=np.float32), 8000)
    assert len(transcriber._Audio(a_8k).read(1.0, 3.0)) == 2 * SR


# ── Los cinco sitios donde se llama a Whisper ────────────────────────────────

def test_dos_pistas_el_eco_de_los_demas_sale_rehecho(tmp_path, monkeypatch):
    monkeypatch.setattr(transcriber, 'ME_NAME', 'Felipe')
    audio = tmp_path / f'{STEM}.wav'
    escribir(audio.with_suffix('.mic.wav'), 60)
    escribir(audio.with_suffix('.loop.wav'), 60)

    class PorPista(Modelo):
        def transcribe(self, a, **kw):
            if str(a).endswith('.mic.wav'):
                self.con = [(1.0, 3.0, 'hola, soy yo')]
            elif str(a).endswith('.loop.wav'):
                self.con = [(2.0, 20.0, ECO), (25.0, 27.0, 'seguimos')]
            return super().transcribe(a, **kw)

    text, _lang = transcriber._transcribe_with_speakers(PorPista(con=()), audio, hotwords=PISTAS)
    assert ECO not in text
    assert text.splitlines() == ['[00:01] [Felipe]: hola, soy yo',
                                 '[00:02] [Otros]: lo que se dijo',
                                 '[00:25] [Otros]: seguimos']


def test_mezcla_entera_el_texto_sale_sin_eco(tmp_path):
    audio = escribir(tmp_path / f'{STEM}.wav', 30)
    m = Modelo(con=[(0.0, 2.0, 'uno'), (3.0, 9.0, ECO)])
    text, _lang = transcriber._transcribe_whole(m, audio, hotwords=PISTAS)
    assert text.splitlines() == ['[00:00] uno', '[00:02] lo que se dijo']


def test_por_trozos_el_partial_tampoco_lleva_el_eco(tmp_path, monkeypatch):
    monkeypatch.setattr(transcriber, '_CHUNK_SECS', 10)
    audio = escribir(tmp_path / f'{STEM}.wav', 20)
    m = Modelo(con=[(0.0, 2.0, 'uno'), (3.0, 9.0, ECO)])
    partial = []
    text, _lang = transcriber._transcribe_chunks(m, audio, on_segment=partial.append, hotwords=PISTAS)
    assert ECO not in text and not any(ECO in line for line in partial)
    assert partial == ['[00:00] uno', '[00:02] lo que se dijo', '[00:10] uno', '[00:12] lo que se dijo']


@pytest.fixture
def wav(tr_dirs):
    return ta.RECORDINGS_DIR / f'{STEM}.wav'


def test_durante_la_reunion_el_trozo_se_guarda_sin_eco(wav):
    part = lt.part_path(wav, 'mic')
    sf.write(str(part), np.full(20 * SR, 0.1, dtype=np.float32), SR, format='WAV', subtype='FLOAT')
    track = lt._Track('mic', part, 0)
    m = Modelo(con=[(0.0, 2.0, 'uno'), (3.0, 9.0, ECO)])
    done, _lang = lt._step(track, m, 'es', {'hotwords': PISTAS},
                           lambda _a, _o: [{'start': 0, 'end': 18 * SR}], None, wav)
    assert done
    [r] = lt.read_records(wav)
    assert r['segments'] == [[0.0, 'uno'], [2.5, 'lo que se dijo']]
    assert m.llamadas[-1][1]['language'] == 'es'


def test_el_cierre_rehace_el_eco_de_un_hueco(wav, monkeypatch):
    m = Modelo(con=[(0.0, 2.0, 'uno'), (3.0, 9.0, ECO)])
    monkeypatch.setattr(transcriber, '_get_model', lambda name=None: m)
    monkeypatch.setattr(transcriber, '_release_model', lambda: None)
    monkeypatch.setattr(transcriber, 'ME_NAME', 'Felipe')
    lt._append_record(wav, {'track': 'mic', 'start_frame': 0, 'end_frame': 10 * SR,
                            'segments': [[1.0, 'hola']], 'lang': 'es', 'model': lt.final_model()})
    escribir(lt.track_wav(wav, 'mic'), 20)
    text, _lang = lt.finish_live(wav, hotwords=PISTAS)
    assert ECO not in text
    assert text.splitlines() == ['[00:01] hola', '[00:10] uno', '[00:12] lo que se dijo']
