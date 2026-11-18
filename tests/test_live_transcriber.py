"""Transcripcion en vivo: el transcript se escribe mientras la reunion ocurre.

LO QUE ESTOS TESTS PROTEGEN, POR ORDEN DE IMPORTANCIA
1. El camino de siempre sigue intacto. Por defecto el transcript que se guarda
   es el de siempre ('medium'); el en vivo es para verlo durante la reunion.
   Sin transcript en vivo, o si algo de el falla, nada cambia.
2. En el modo opcional (en vivo como definitivo) el transcript sale completo: el
   daemon rehace lo que el proceso en vivo no llego a comprometer, ni un tramo
   menos ni uno repetido.
3. El formato es identico al del post-reunion, porque de el viven las minutas.
4. El proceso en vivo no estorba al grabador: lee sin dejar handles abiertos y
   nunca escribe ni borra nada de lo que usa el pipeline.

Diseno y medidas en live_transcriber.py.
"""
import json
import queue
import time

import numpy as np
import pytest
import soundfile as sf

import live_transcriber as lt
import storage
import transcriber
import tray_app as ta
from conftest import REAL_LIVE_SPAWN, FakeInfo, FakeSegment

pytestmark = pytest.mark.unit

SR = lt.SR
STEM = '2026-10-18_10-00_Entrevista_Cliente'


@pytest.fixture
def wav(tr_dirs):
    return ta.RECORDINGS_DIR / f'{STEM}.wav'


@pytest.fixture
def me(monkeypatch):
    monkeypatch.setattr(transcriber, 'ME_NAME', 'Felipe')
    return '[Felipe]'


def write_records(wav, *records, partial_tail=''):
    with open(lt.jsonl_path(wav), 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
        f.write(partial_tail)


def rec(track, start_s, end_s, *segments, lang='es', model='medium'):
    """Un trozo comprometido. Por defecto, hecho con el modelo de siempre (el modo
    de fondo), que es el que el cierre aprovecha."""
    return {'track': track, 'start_frame': int(start_s * SR), 'end_frame': int(end_s * SR),
            'segments': [list(s) for s in segments], 'lang': lang, 'model': model}


def make_track(path, seconds, subtype='PCM_16'):
    sf.write(str(path), np.zeros(int(seconds * SR), dtype=np.float32), SR, subtype=subtype)
    return path


class TrackModel:
    """Modelo falso que apunta cuanto audio le piden transcribir."""

    def __init__(self, segments=((0.0, 2.0, 'resto'),), language='es'):
        self.segments, self.language = segments, language
        self.lengths, self.kwargs = [], []

    def transcribe(self, audio, **kwargs):
        self.lengths.append(len(audio))
        self.kwargs.append(kwargs)
        segs = (FakeSegment(s, e, t) for s, e, t in self.segments)
        info = FakeInfo(self.language)
        info.language_probability = 0.99
        return segs, info


@pytest.fixture
def model(monkeypatch):
    m = TrackModel()
    monkeypatch.setattr(transcriber, '_get_model', lambda name=None: m)
    monkeypatch.setattr(transcriber, '_release_model', lambda: None)
    return m


# ── Lectura de un .part que el grabador sigue escribiendo ────────────────────

def test_lee_un_part_abierto_como_lo_escribe_el_grabador(tmp_path):
    """El mismo SoundFile, formato y flush que audio_recorder._writer_loop. Con
    el fichero abierto la cabecera dice 0 bytes de datos: hay que leer igual."""
    path = tmp_path / 'x.mic.part'
    data = np.linspace(-0.5, 0.5, SR, dtype=np.float32)
    with sf.SoundFile(str(path), mode='w', samplerate=SR, channels=1, format='WAV', subtype='FLOAT') as f:
        f.write(data)
        f.flush()
        audio, total = lt.read_part(path, 0, 10 * SR)
    assert total == SR
    assert np.allclose(audio, data)


def test_lee_solo_lo_nuevo_cuando_el_fichero_crece(tmp_path):
    path = tmp_path / 'x.mic.part'
    a = np.full(SR, 0.1, dtype=np.float32)
    b = np.full(SR // 2, 0.2, dtype=np.float32)
    with sf.SoundFile(str(path), mode='w', samplerate=SR, channels=1, format='WAV', subtype='FLOAT') as f:
        f.write(a)
        f.flush()
        first, _ = lt.read_part(path, 0, 10 * SR)
        f.write(b)
        f.flush()
        second, total = lt.read_part(path, len(first), 10 * SR)
    assert len(first) == SR and total == SR + SR // 2
    assert np.allclose(second, b)


def test_respeta_el_maximo_de_frames(tmp_path):
    path = tmp_path / 'x.mic.part'
    sf.write(str(path), np.zeros(3 * SR, dtype=np.float32), SR, format='WAV', subtype='FLOAT')
    audio, total = lt.read_part(path, 0, SR)
    assert len(audio) == SR and total == 3 * SR


def test_fichero_ausente_no_lanza(tmp_path):
    audio, total = lt.read_part(tmp_path / 'no.mic.part', 0, SR)
    assert len(audio) == 0 and total == 0


def test_si_el_fichero_encoge_espera_sin_lanzar(tmp_path):
    """Si el grabador recrea .loop.part (un intento de loopback fallido), el
    fichero nuevo puede ser mas corto que lo ya leido: se espera a que crezca."""
    path = tmp_path / 'x.loop.part'
    sf.write(str(path), np.zeros(SR, dtype=np.float32), SR, format='WAV', subtype='FLOAT')
    audio, total = lt.read_part(path, 5 * SR, SR)
    assert len(audio) == 0 and total == SR


def test_un_wav_que_no_es_float_mono_no_se_lee_como_si_lo_fuera(tmp_path):
    """Leer PCM16 como float32 daria basura, no un error."""
    path = make_track(tmp_path / 'x.mic.wav', 1.0, subtype='PCM_16')
    audio, total = lt.read_part(path, 0, SR)
    assert len(audio) == 0 and total == 0


# ── Donde se corta el audio ─────────────────────────────────────────────────

def s(start_s, end_s):
    return {'start': int(start_s * SR), 'end': int(end_s * SR)}


def test_no_corta_antes_del_minimo():
    assert lt.choose_cut(10 * SR, [s(0, 9)]) is None


def test_un_bufer_sin_voz_se_compromete_entero():
    assert lt.choose_cut(16 * SR, []) == 16 * SR


def test_corta_en_mitad_de_una_pausa():
    cut = lt.choose_cut(20 * SR, [s(0, 15), s(16, 20)])
    assert cut == int(15.5 * SR)


def test_elige_la_ultima_pausa_valida():
    cut = lt.choose_cut(28 * SR, [s(0, 15), s(16, 20), s(21, 28)])
    assert cut == int(20.5 * SR)


def test_una_pausa_demasiado_corta_no_cuenta():
    assert lt.choose_cut(20 * SR, [s(0, 15), s(15.2, 20)]) is None


def test_sin_pausas_corta_al_maximo():
    assert lt.choose_cut(31 * SR, [s(0, 31)]) == lt.MAX_CHUNK_S * SR


def test_el_silencio_final_tambien_es_una_pausa():
    cut = lt.choose_cut(18 * SR, [s(0, 16)])
    assert cut == 17 * SR


# ── Registros comprometidos ─────────────────────────────────────────────────

def test_una_ultima_linea_a_medias_se_ignora(wav):
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')), partial_tail='{"track": "mic", "sta')
    assert len(lt.read_records(wav)) == 1


def test_registros_invalidos_se_ignoran(wav):
    write_records(wav, rec('mic', 0, 15), {'track': 'otra'}, {'track': 'mic', 'start_frame': 5,
                                                              'end_frame': 5, 'segments': []})
    assert len(lt.read_records(wav)) == 1


def test_lo_comprometido_es_lo_contiguo_desde_cero(wav):
    records = [rec('mic', 0, 15), rec('mic', 15, 30), rec('mic', 45, 60)]
    pos, kept = lt.committed(records, 'mic')
    assert pos == 30 * SR and len(kept) == 2


def test_sin_registros_de_una_pista_no_hay_nada_comprometido():
    assert lt.committed([rec('mic', 0, 15)], 'loop') == (0, [])


# ── Formato: identico al post-reunion ───────────────────────────────────────

class PerFileModel:
    def __init__(self, by_name):
        self.by_name = by_name

    def transcribe(self, audio, **_k):
        segs = self.by_name['mic' if str(audio).endswith('.mic.wav') else 'loop']
        return (FakeSegment(a, b, t) for a, b, t in segs), FakeInfo('es')


def test_con_loopback_el_formato_es_el_de_transcribe_with_speakers(tr_dirs, me, wav_factory):
    mic = [(1.0, 2.0, 'hola'), (10.0, 11.0, 'vale')]
    loop = [(1.0, 3.0, 'buenas'), (5.0, 6.0, 'que tal')]
    base = ta.RECORDINGS_DIR / 'paridad.wav'
    wav_factory(base.with_suffix('.mic.wav'))
    wav_factory(base.with_suffix('.loop.wav'))
    esperado, _ = transcriber._transcribe_with_speakers(PerFileModel({'mic': mic, 'loop': loop}), base)

    by_track = {'mic': [(a, t) for a, _b, t in mic], 'loop': [(a, t) for a, _b, t in loop]}
    assert lt.render(by_track, labelled=True) == esperado


def test_sin_loopback_el_formato_va_sin_etiquetas(me):
    """Como _transcribe_whole: sin loopback el micro capta a todos los de la sala."""
    out = lt.render({'mic': [(65.0, 'hola a todos')], 'loop': [(1.0, 'ruido')]}, labelled=False)
    assert out == '[01:05] hola a todos'


# ── El cierre: finish_live ──────────────────────────────────────────────────

def test_sin_transcript_en_vivo_no_hace_nada(wav, model):
    assert lt.finish_live(wav) is None
    assert model.lengths == []


def test_sin_registros_validos_no_hace_nada(wav, model):
    write_records(wav, partial_tail='basura')
    make_track(lt.track_wav(wav, 'mic'), 5)
    assert lt.finish_live(wav) is None


def test_sin_pista_de_micro_no_hace_nada(wav, model):
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    assert lt.finish_live(wav) is None


def test_solo_transcribe_lo_que_falta(wav, model, me):
    """EL TEST CLAVE: el proceso en vivo comprometio 15 s del micro y el loopback
    entero. El cierre transcribe solo los 5 s que faltan del micro."""
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')), rec('loop', 0, 20, (3.0, 'buenas')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    make_track(lt.track_wav(wav, 'loop'), 20)

    text, lang = lt.finish_live(wav)

    assert model.lengths == [5 * SR]
    assert text.splitlines() == ['[00:01] [Felipe]: hola', '[00:03] [Otros]: buenas',
                                 '[00:15] [Felipe]: resto']
    assert lang == 'es'


def test_si_todo_estaba_comprometido_no_carga_el_modelo(wav, me, monkeypatch):
    monkeypatch.setattr(transcriber, '_get_model',
                        lambda *_a: pytest.fail('no hacia falta cargar Whisper'))
    write_records(wav, rec('mic', 0, 20, (1.0, 'hola')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    text, _ = lt.finish_live(wav)
    assert text == '[00:01] hola'


def test_un_hueco_se_rellena_y_lo_de_despues_se_aprovecha(wav, model, me):
    """Al pasar a en vivo a mitad, queda un hueco: el cierre rellena solo el hueco
    y aprovecha lo que hay a los dos lados."""
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')), rec('mic', 30, 45, (31.0, 'despues')))
    make_track(lt.track_wav(wav, 'mic'), 45)
    text, _ = lt.finish_live(wav)
    assert model.lengths == [15 * SR]
    assert text.splitlines() == ['[00:01] hola', '[00:15] resto', '[00:31] despues']


def test_por_defecto_lo_hecho_en_small_se_rehace_con_el_modelo_de_siempre(wav, model, me):
    """Medium del modo de fondo hasta los 60 s, small en vivo despues: se aprovecha
    el medium y se rehace el tramo de small."""
    write_records(wav, rec('mic', 0, 60, (1.0, 'fondo')),
                  rec('mic', 60, 90, (61.0, 'en vivo'), model='small'))
    make_track(lt.track_wav(wav, 'mic'), 90)
    text, _ = lt.finish_live(wav)
    assert model.lengths == [30 * SR]
    assert 'en vivo' not in text and 'fondo' in text


def test_solo_small_y_sin_modo_definitivo_se_transcribe_como_siempre(wav, model):
    write_records(wav, rec('mic', 0, 60, (1.0, 'en vivo'), model='small'))
    make_track(lt.track_wav(wav, 'mic'), 60)
    assert lt.finish_live(wav) is None
    assert model.lengths == []


def test_con_modo_definitivo_se_aprovecha_lo_de_small(wav, model, me, tr_dirs):
    (tr_dirs / 'settings.json').write_text('{"live_transcript_final": true}', encoding='utf-8')
    write_records(wav, rec('mic', 0, 60, (1.0, 'en vivo'), model='small'))
    make_track(lt.track_wav(wav, 'mic'), 60)
    text, _ = lt.finish_live(wav)
    assert text == '[00:01] en vivo' and model.lengths == []


def test_registros_sin_modelo_cuentan_como_small(wav, model):
    """Los anteriores al campo 'model' eran todos de small."""
    r = rec('mic', 0, 60, (1.0, 'viejo'))
    del r['model']
    write_records(wav, r)
    make_track(lt.track_wav(wav, 'mic'), 60)
    assert lt.finish_live(wav) is None


def test_cada_bloque_del_cierre_queda_guardado(wav, model, me):
    """Si el daemon cae a mitad del cierre, el siguiente intento no lo repite."""
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    lt.finish_live(wav)
    nuevos = [r for r in lt.read_records(wav) if r['start_frame'] == 15 * SR]
    assert len(nuevos) == 1 and nuevos[0]['model'] == 'medium'


def test_sin_loop_wav_el_transcript_va_sin_etiquetas(wav, model, me):
    """El grabador descarto el loopback (silencio, p. ej. una reunion presencial):
    como hoy, el micro capta a todos y el transcript va sin etiquetas."""
    write_records(wav, rec('mic', 0, 10, (1.0, 'hola')), rec('loop', 0, 10))
    make_track(lt.track_wav(wav, 'mic'), 10)
    text, _ = lt.finish_live(wav)
    assert text == '[00:01] hola'


def test_usa_el_idioma_fijado_en_vivo(wav, model, me):
    write_records(wav, rec('mic', 0, 15, (1.0, 'hello'), lang='en'))
    lt._write_status(wav, lang='en')
    make_track(lt.track_wav(wav, 'mic'), 20)
    _, lang = lt.finish_live(wav)
    assert lang == 'en'
    assert model.kwargs[0]['language'] == 'en'


def test_comprometido_mas_largo_que_la_pista_no_se_usa(wav, model):
    write_records(wav, rec('mic', 0, 30, (1.0, 'hola')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    assert lt.finish_live(wav) is None


def test_la_cancelacion_se_propaga(wav, model):
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    with pytest.raises(transcriber.TranscriptionCancelled):
        lt.finish_live(wav, should_cancel=lambda: True)


def test_finish_live_no_borra_nada(wav, model):
    """Borrar es cosa de discard_artifacts, cuando el transcript ya esta en disco."""
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    mic = make_track(lt.track_wav(wav, 'mic'), 20)
    lt.finish_live(wav)
    assert mic.exists() and lt.jsonl_path(wav).exists()


# ── Por defecto: el transcript que se guarda es el de siempre ───────────────

@pytest.fixture
def pipeline(tray, wav, wav_factory, monkeypatch):
    wav_factory(wav, seconds=0.5)
    registrados = []
    monkeypatch.setattr(tray, '_notify', lambda *_a: None)
    monkeypatch.setattr(tray, 'set_processing', lambda *_a: None)
    monkeypatch.setattr(tray, '_register_part', lambda w, text, lang: registrados.append((text, lang)))
    calls = []

    def fake_transcribe(*_a, **_k):
        calls.append(1)
        return '[00:00] como siempre', 'es'

    monkeypatch.setattr(transcriber, 'transcribe', fake_transcribe)
    return registrados, calls


def test_lo_hecho_en_vivo_con_small_no_es_el_transcript_que_se_guarda(tray, wav, pipeline):
    """EL TEST CLAVE: si durante la reunion solo se hizo 'small' (modo en vivo), el
    transcript que se guarda lo hace el pipeline de siempre. Medido el 19/10:
    'small' coincide un 91-95% y falla mas en terminos en ingles."""
    registrados, calls = pipeline
    write_records(wav, rec('mic', 0, 1, (0.0, 'en vivo'), model='small'))
    mic = make_track(lt.track_wav(wav, 'mic'), 1)
    tray._run_pipeline_sync(wav)
    assert calls == [1]
    assert registrados == [('[00:00] como siempre', 'es')]
    assert mic.exists(), "las pistas son del transcriptor: el en vivo no las toca"


def test_lo_hecho_de_fondo_con_medium_ahorra_la_transcripcion(tray, wav, pipeline, model, me):
    """EL TEST CLAVE del modo por defecto: lo hecho durante la reunion con el
    modelo de siempre no se repite al colgar."""
    registrados, calls = pipeline
    write_records(wav, rec('mic', 0, 20, (1.0, 'de fondo')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    tray._run_pipeline_sync(wav)
    assert calls == [], "el pipeline no debia transcribir de nuevo"
    assert registrados == [('[00:01] de fondo', 'es')]


def test_con_el_transcript_guardado_se_borra_el_en_vivo(tray, wav, pipeline):
    write_records(wav, rec('mic', 0, 1, (0.0, 'en vivo')))
    lt._write_status(wav, state='live')
    huerfano = lt.part_path(wav, 'mic')
    huerfano.write_text('x')          # un borrado del grabador que coincidio con una lectura
    tray._run_pipeline_sync(wav)
    assert not lt.jsonl_path(wav).exists() and not lt.status_path(wav).exists()
    assert not huerfano.exists()


def test_si_la_transcripcion_falla_se_conserva_el_texto_en_vivo(tray, wav, pipeline, monkeypatch):
    """Si 'medium' falla, el en vivo es el unico texto de esa reunion."""
    monkeypatch.setattr(transcriber, 'transcribe', lambda *_a, **_k: None)
    write_records(wav, rec('mic', 0, 1, (0.0, 'en vivo')))
    tray._run_pipeline_sync(wav)
    assert lt.jsonl_path(wav).exists()


def test_sin_voz_tambien_se_borra_el_en_vivo(tray, wav, pipeline, monkeypatch):
    monkeypatch.setattr(transcriber, 'transcribe', lambda *_a, **_k: ('', 'es'))
    write_records(wav, rec('mic', 0, 1))
    tray._run_pipeline_sync(wav)
    assert not lt.jsonl_path(wav).exists()


@pytest.mark.parametrize('contenido, esperado', [
    ('{}', False),
    ('{"live_transcript_final": true}', True),
    ('{"live_transcript_final": "true"}', False),
    ('{"live_transcript_final": false}', False),
    ('{roto', False),
])
def test_el_modo_rapido_solo_con_true(tr_dirs, contenido, esperado):
    (tr_dirs / 'settings.json').write_text(contenido, encoding='utf-8')
    assert lt.final_enabled() is esperado


@pytest.mark.parametrize('ajustes, de_fondo, definitivo', [
    ({}, True, False),
    ({'background_transcription': False}, False, False),
    ({'live_transcript_final': True}, True, True),
])
def test_lo_que_guarda_ajustes_es_lo_que_lee_la_app(tr_dirs, ajustes, de_fondo, definitivo):
    """Los interruptores de Ajustes (web/app.js) guardan con AppAPI.save_settings;
    el daemon los lee con background_enabled()/final_enabled()."""
    import app_window
    app_window.AppAPI().save_settings({'language': 'es', **ajustes})
    assert lt.background_enabled() is de_fondo
    assert lt.final_enabled() is definitivo


@pytest.mark.parametrize('contenido, entorno, esperado', [
    ('{"live_transcription": false}', None, False),
    ('{"live_transcription": true}', None, True),
    ('{}', '0', False),
    ('{}', 'off', False),
    ('{}', '1', True),
])
def test_los_interruptores_de_la_version_anterior_la_siguen_apagando(
        tr_dirs, monkeypatch, contenido, entorno, esperado):
    """77140da (main) tenia su propia transcripcion en vivo, que se apagaba con
    "live_transcription": false o LIVE_TRANSCRIPTION=0. Quien la tenia apagada
    no debe encontrarse esta encendida al actualizar."""
    (tr_dirs / 'settings.json').write_text(contenido, encoding='utf-8')
    if entorno is None:
        monkeypatch.delenv('LIVE_TRANSCRIPTION', raising=False)
    else:
        monkeypatch.setenv('LIVE_TRANSCRIPTION', entorno)
    assert lt.background_enabled() is esperado


# ── Opcional: el transcript en vivo como definitivo (modo rapido) ───────────

@pytest.fixture
def modo_rapido(tr_dirs):
    (tr_dirs / 'settings.json').write_text('{"live_transcript_final": true}', encoding='utf-8')


def test_con_transcript_en_vivo_no_se_vuelve_a_transcribir(tray, wav, pipeline, monkeypatch, modo_rapido):
    registrados, calls = pipeline
    write_records(wav, rec('mic', 0, 1, (0.0, 'hola')))
    monkeypatch.setattr(lt, 'finish_live', lambda *_a, **_k: ('[00:00] en vivo', 'es'))
    tray._run_pipeline_sync(wav)
    assert calls == []
    assert registrados == [('[00:00] en vivo', 'es')]
    assert storage.get_transcript_path(wav).read_text(encoding='utf-8') == '[00:00] en vivo'


def test_con_transcript_en_vivo_se_borran_pistas_y_ficheros_en_vivo(tray, wav, pipeline, monkeypatch,
                                                                    modo_rapido):
    write_records(wav, rec('mic', 0, 1, (0.0, 'hola')))
    mic = make_track(lt.track_wav(wav, 'mic'), 1)
    huerfano = lt.part_path(wav, 'mic')
    huerfano.write_text('x')          # un borrado del grabador que coincidio con una lectura
    monkeypatch.setattr(lt, 'finish_live', lambda *_a, **_k: ('[00:00] en vivo', 'es'))
    tray._run_pipeline_sync(wav)
    assert not mic.exists() and not lt.jsonl_path(wav).exists() and not huerfano.exists()


def test_sin_ficheros_en_vivo_ni_se_intenta(tray, wav, pipeline, monkeypatch, modo_rapido):
    """Con la transcripcion en vivo apagada, el pipeline es el de siempre."""
    _registrados, calls = pipeline
    monkeypatch.setattr(lt, 'finish_live', lambda *_a, **_k: pytest.fail('no debia llamarse'))
    tray._run_pipeline_sync(wav)
    assert calls == [1]


@pytest.mark.parametrize('resultado', [None, ('', 'es'), ('   ', 'es')])
def test_sin_resultado_util_se_transcribe_como_siempre(tray, wav, pipeline, monkeypatch, resultado,
                                                       modo_rapido):
    registrados, calls = pipeline
    write_records(wav, rec('mic', 0, 1))
    monkeypatch.setattr(lt, 'finish_live', lambda *_a, **_k: resultado)
    tray._run_pipeline_sync(wav)
    assert calls == [1]
    assert registrados == [('[00:00] como siempre', 'es')]


def test_si_el_cierre_falla_se_transcribe_como_siempre_y_no_se_borra_nada(tray, wav, pipeline,
                                                                          monkeypatch, modo_rapido):
    """CONTROL NEGATIVO: el fallo de lo nuevo no puede costar la reunion."""
    registrados, calls = pipeline
    write_records(wav, rec('mic', 0, 1))
    mic = make_track(lt.track_wav(wav, 'mic'), 1)

    def boom(*_a, **_k):
        raise RuntimeError('fallo provocado')

    monkeypatch.setattr(lt, 'finish_live', boom)
    tray._run_pipeline_sync(wav)
    assert calls == [1]
    assert registrados == [('[00:00] como siempre', 'es')]
    assert mic.exists(), "las pistas tienen que seguir ahi para el camino de siempre"
    assert not storage.get_transcript_path(wav).with_name(
        storage.get_transcript_path(wav).name + '.tmp').exists()
    assert not lt.jsonl_path(wav).exists(), "el texto en vivo no debe quedarse en recordings/"


def test_descartar_durante_el_cierre_se_propaga(tray, wav, pipeline, monkeypatch, modo_rapido):
    _registrados, calls = pipeline
    write_records(wav, rec('mic', 0, 1))

    def cancel(*_a, **_k):
        raise transcriber.TranscriptionCancelled()

    monkeypatch.setattr(lt, 'finish_live', cancel)
    with pytest.raises(transcriber.TranscriptionCancelled):
        tray._run_pipeline_sync(wav)
    assert calls == [], "una reunion descartada no se transcribe"


def test_extremo_a_extremo_con_finish_live_real(tray, wav, pipeline, model, me, modo_rapido):
    """Sin parchear finish_live: registros + pistas reales -> transcript en disco."""
    registrados, calls = pipeline
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')), rec('loop', 0, 20, (3.0, 'buenas')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    make_track(lt.track_wav(wav, 'loop'), 20)
    tray._run_pipeline_sync(wav)
    assert calls == []
    text = registrados[0][0]
    assert text.splitlines() == ['[00:01] [Felipe]: hola', '[00:03] [Otros]: buenas',
                                 '[00:15] [Felipe]: resto']


# ── Glosario ────────────────────────────────────────────────────────────────
#
# Las pistas del glosario (invitados, asunto, terminos) llegan a Whisper durante
# la reunion por <stem>.live.hints, que escribe el daemon, y al cerrar por
# finish_live. Las correcciones se aplican una vez, sobre el transcript entero.

def test_las_pistas_se_usan_en_cuanto_aparecen(wav):
    args = {}
    assert lt._load_hints(args, wav) is False, "sin fichero hay que seguir mirando"
    lt.write_hints(wav, 'Nurettin Ekizoglu, Kinaxis')
    assert lt._load_hints(args, wav) is True
    assert args['hotwords'] == 'Nurettin Ekizoglu, Kinaxis'


@pytest.mark.parametrize('contenido', ['{roto', '[]', '{"hotwords": ""}'])
def test_sin_pistas_utiles_se_transcribe_sin_ellas(wav, contenido):
    lt.hints_path(wav).write_text(contenido, encoding='utf-8')
    args = {}
    assert lt._load_hints(args, wav) is True, "un fichero que no sirve no se vuelve a mirar"
    assert 'hotwords' not in args


def test_el_cierre_pasa_las_pistas_a_whisper(wav, model, me):
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    lt.finish_live(wav, hotwords='Kinaxis')
    assert model.kwargs and all(k['hotwords'] == 'Kinaxis' for k in model.kwargs)


def test_el_cierre_sin_pistas_no_las_inventa(wav, model, me):
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    lt.finish_live(wav)
    assert model.kwargs and all('hotwords' not in k for k in model.kwargs)


def test_el_transcript_en_vivo_sale_corregido_con_el_glosario_de_ahora(tray, wav, pipeline,
                                                                       monkeypatch, modo_rapido):
    """Las entradas se releen al cerrar: ctx trae las del principio de la reunion
    (ninguna) y la del glosario se aprobo durante ella."""
    import glossary
    registrados, calls = pipeline
    write_records(wav, rec('mic', 0, 1, (0.0, 'hola')))
    visto = {}

    def cierre(*_a, **k):
        visto.update(k)
        return '[00:00] hablamos de Kinexis', 'es'

    monkeypatch.setattr(lt, 'finish_live', cierre)
    monkeypatch.setattr(transcriber, '_glossary_context',
                        lambda p: {'hotwords': 'Kinaxis', 'project_id': None, 'entries': []})
    monkeypatch.setattr(glossary, 'entries', lambda project_id=None: [
        {'term': 'Kinaxis', 'category': 'company', 'aliases': ['Kinexis'], 'meaning': '', 'project': None}])

    tray._run_pipeline_sync(wav)

    assert calls == []
    assert visto['hotwords'] == 'Kinaxis'
    assert registrados == [('[00:00] hablamos de Kinaxis', 'es')]
    assert storage.get_transcript_path(wav).read_text(encoding='utf-8') == '[00:00] hablamos de Kinaxis'


def test_descartar_borra_tambien_las_pistas(wav):
    lt.write_hints(wav, 'X')
    lt.discard_artifacts(wav, tracks=False)
    assert not lt.hints_path(wav).exists()


# ── Lanzar y parar ──────────────────────────────────────────────────────────

def esperar(cond, timeout=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_empezar_a_grabar_lanza_el_proceso_en_vivo(tray, wav, live_spawns):
    tray.set_recording(True, wav)
    assert esperar(lambda: live_spawns == [wav])
    tray.set_recording(False)


def test_al_empezar_a_grabar_se_escriben_las_pistas_del_glosario(tray, wav, live_spawns, monkeypatch):
    monkeypatch.setattr(transcriber, '_glossary_context',
                        lambda p: {'hotwords': 'Kinaxis', 'project_id': None, 'entries': []})
    tray.set_recording(True, wav)
    assert esperar(lambda: lt.hints_path(wav).exists())
    tray.set_recording(False)
    args = {}
    assert lt._load_hints(args, wav) and args['hotwords'] == 'Kinaxis'


def test_si_las_pistas_fallan_el_proceso_en_vivo_arranca_igual(tray, wav, live_spawns, monkeypatch):
    """Calcular las pistas puede consultar Outlook: va en su hilo, y si falla o
    se cuelga, la transcripcion durante la reunion no espera por ellas."""
    def colgado(_p):
        time.sleep(5)

    monkeypatch.setattr(transcriber, '_glossary_context', colgado)
    tray.set_recording(True, wav)
    assert esperar(lambda: live_spawns == [wav])
    tray.set_recording(False)
    assert not lt.hints_path(wav).exists()


class FakeProc:
    pid = 999_999_999


def test_parar_mata_el_proceso_en_vivo(tray, wav, monkeypatch):
    matados = []
    monkeypatch.setattr(lt, 'kill', lambda proc, path: matados.append((proc, path)))
    proc = FakeProc()
    tray._recording_path = wav
    tray._live[wav.stem] = (proc, wav)
    tray.set_recording(False)
    assert tray._live == {}
    assert esperar(lambda: matados == [(proc, wav)])


def test_parar_no_espera_a_que_muera(tray, wav, monkeypatch):
    """Un proceso recien nacido puede negar el acceso unos segundos: set_recording
    no puede quedarse esperando con el estado a medio cambiar."""
    import threading
    soltar = threading.Event()
    monkeypatch.setattr(lt, 'kill', lambda *_a: soltar.wait(5))
    tray._recording_path = wav
    tray._live[wav.stem] = (FakeProc(), wav)
    t0 = time.monotonic()
    tray.set_recording(False)
    assert time.monotonic() - t0 < 1
    soltar.set()


def test_si_paro_mientras_se_lanzaba_se_mata_al_llegar(tray, wav, monkeypatch):
    """El spawn espera al loopback; si la grabacion para antes, el proceso que
    llegue tarde no puede quedarse vivo."""
    matados, proc = [], FakeProc()
    monkeypatch.setattr(lt, 'kill', lambda p, path: matados.append(p))

    def lento(path, recorder=None, still_wanted=None, **_k):
        tray._recording_path = None          # la grabacion paro mientras tanto
        return proc

    monkeypatch.setattr(lt, 'spawn', lento)
    tray._recording_path = wav
    tray._start_live(wav)
    assert esperar(lambda: matados == [proc])
    assert tray._live == {}


def test_un_fallo_al_lanzar_no_afecta_a_la_grabacion(tray, wav, monkeypatch):
    def boom(*_a, **_k):
        raise OSError('sin python')

    monkeypatch.setattr(lt, 'spawn', boom)
    tray.set_recording(True, wav)
    time.sleep(0.1)
    assert tray._recording_path == wav
    tray.set_recording(False)


# ── Estado para el panel ────────────────────────────────────────────────────

def leer_estado(root):
    return json.loads((root / '.pipeline_status.json').read_text(encoding='utf-8'))['jobs']


def test_la_grabacion_con_proceso_en_vivo_lleva_live_stem(tray, wav, tr_dirs):
    tray._recording_path, tray._recording_start = wav, time.time()
    tray._live[wav.stem] = (FakeProc(), wav)
    tray._write_status()
    job = leer_estado(tr_dirs)[0]
    assert job['live_stem'] == STEM
    assert 'stem' not in job, "'stem' habilitaria el boton de descartar"


def test_sin_proceso_en_vivo_no_hay_live_stem(tray, wav, tr_dirs):
    tray._recording_path, tray._recording_start = wav, time.time()
    tray._write_status()
    assert 'live_stem' not in leer_estado(tr_dirs)[0]


def test_get_live_transcript_devuelve_las_lineas(wav, me):
    import app_window
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')), rec('loop', 0, 15, (2.0, 'buenas')))
    out = app_window.AppAPI().get_live_transcript(STEM)
    assert out['lines'] == ['[00:01] [Felipe]: hola', '[00:02] [Otros]: buenas']


def test_get_live_transcript_sin_loopback_va_sin_etiquetas(wav, me):
    import app_window
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    assert app_window.AppAPI().get_live_transcript(STEM)['lines'] == ['[00:01] hola']


@pytest.mark.parametrize('stem', ['', '..\\settings', '../x', 'a/b'])
def test_get_live_transcript_rechaza_rutas(stem):
    import app_window
    assert app_window.AppAPI().get_live_transcript(stem)['lines'] == []


def test_get_transcript_text_no_confunde_el_jsonl_con_un_transcript(wav):
    """get_transcript_text busca {stem}*.txt: el .live.jsonl no debe colarse."""
    import app_window
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    assert app_window.AppAPI().get_transcript_text(str(ta.MINUTES_DIR / f'{STEM}.md')) == ''


# ── Recuperacion al arrancar y limpieza ─────────────────────────────────────

def test_recover_pending_ignora_las_pistas(tray, wav_factory, monkeypatch):
    for suffix in ('.wav', '.mic.wav', '.loop.wav'):
        wav_factory(ta.RECORDINGS_DIR / f'{STEM}{suffix}', seconds=0.1)
    monkeypatch.setattr(ta.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(tray, '_notify', lambda *_a: None)
    tray._pipeline_queue = queue.Queue()
    tray._recover_pending()
    assert [p.name for p in tray._pipeline_queue.queue] == [f'{STEM}.wav']


@pytest.mark.parametrize('name', [f'{STEM}.mic.part', f'{STEM}.live.jsonl', f'{STEM}.live.json',
                                  f'{STEM}.live.json.tmp', f'{STEM}.live.finishing'])
def test_la_limpieza_recoge_los_restos_de_una_reunion_procesada(tr_dirs, name):
    """Con minutas: lo de una reunion sin procesar se guarda mas tiempo, para
    rehacerla, y eso no son restos."""
    import os
    (storage.MINUTES_DIR / '20261018_1000_Entrevista_Cliente.md').write_text('# minutas')
    f = ta.RECORDINGS_DIR / name
    f.write_text('x')
    viejo = time.time() - 20 * 86400
    os.utime(f, (viejo, viejo))
    storage.cleanup_old_recordings()
    assert not f.exists()


def test_la_limpieza_no_toca_restos_recientes(tr_dirs):
    f = ta.RECORDINGS_DIR / f'{STEM}.mic.part'
    f.write_text('x')
    storage.cleanup_old_recordings()
    assert f.exists()


# ── Interruptor y lanzamiento real ──────────────────────────────────────────

def reloj_falso(**overrides):
    """Sustituto del modulo time SOLO para live_transcriber: parchear time.sleep o
    time.monotonic en el modulo global afectaria a todos los hilos del proceso."""
    import types
    base = dict(monotonic=time.monotonic, sleep=time.sleep, perf_counter=time.perf_counter,
                time=time.time)
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture
def sin_espera(monkeypatch):
    """El spawn real espera START_DELAY_S antes de lanzar; en los tests, no."""
    monkeypatch.setattr(lt, 'START_DELAY_S', 0)


def escribir_ajustes(root, contenido, bom=False):
    (root / 'settings.json').write_text(('﻿' if bom else '') + contenido, encoding='utf-8')


def test_activado_por_defecto(tr_dirs):
    escribir_ajustes(tr_dirs, '{"language": "es"}')
    assert lt.background_enabled() is True


def test_se_apaga_con_false(tr_dirs):
    escribir_ajustes(tr_dirs, '{"background_transcription": false}')
    assert lt.background_enabled() is False


def test_ajustes_ilegibles_lo_apagan(tr_dirs):
    escribir_ajustes(tr_dirs, '{roto')
    assert lt.background_enabled() is False


def test_ajustes_con_bom_se_leen(tr_dirs):
    escribir_ajustes(tr_dirs, '{"background_transcription": false}', bom=True)
    assert lt.background_enabled() is False


def test_apagado_no_lanza_ningun_proceso(tr_dirs, wav, monkeypatch):
    escribir_ajustes(tr_dirs, '{"background_transcription": false}')
    monkeypatch.setattr(lt.subprocess, 'Popen', lambda *_a, **_k: pytest.fail('lanzo un proceso'))
    assert REAL_LIVE_SPAWN(wav) is None


def test_el_modo_en_vivo_no_depende_del_interruptor_de_fondo(tr_dirs, wav, monkeypatch, sin_espera):
    """En vivo lo pide el usuario expresamente: se lanza aunque el fondo este apagado."""
    escribir_ajustes(tr_dirs, '{"background_transcription": false}')
    monkeypatch.setattr(lt.subprocess, 'Popen', lambda *_a, **_k: FakeProc())
    assert REAL_LIVE_SPAWN(wav, mode='live') is not None
    assert lt.read_ctl(wav)['mode'] == 'live'


def test_si_la_grabacion_ya_no_interesa_no_se_lanza(tr_dirs, wav, monkeypatch):
    escribir_ajustes(tr_dirs, '{}')
    monkeypatch.setattr(lt.subprocess, 'Popen', lambda *_a, **_k: pytest.fail('lanzo un proceso'))
    assert REAL_LIVE_SPAWN(wav, still_wanted=lambda: False) is None


@pytest.mark.parametrize('ram, reserva, cabe', [
    (20000, 20000, True),
    (20000, 3000, False),   # la RAM sobra pero no hay reserva: medium reserva ~5 GB
    (2000, 20000, False),
])
def test_la_memoria_se_mide_como_reserva_y_ram(monkeypatch, ram, reserva, cabe):
    """Lo que cuenta es la reserva contra el limite de Windows, no la RAM en uso
    (medium reserva ~4,7 GB y toca ~0,9). Sin reserva, el pagefile llena el disco."""
    monkeypatch.setattr(lt, '_mem_status', lambda: (ram, reserva))
    assert lt._memory_ok('medium') is cabe


def test_sin_poder_medir_la_memoria_no_se_bloquea(monkeypatch):
    monkeypatch.setattr(lt, '_mem_status', lambda: None)
    assert lt._memory_ok('medium') is True


def test_espera_al_loopback_antes_de_lanzar(tr_dirs, wav, monkeypatch, sin_espera):
    """No debe competir con los intentos del worker WASAPI al empezar."""
    escribir_ajustes(tr_dirs, '{}')
    recorder = type('R', (), {'loopback_active': False})()
    lanzado = []

    def fake_popen(cmd, **_k):
        lanzado.append(recorder.loopback_active)
        return FakeProc()

    def dormir(_s):
        recorder.loopback_active = True

    monkeypatch.setattr(lt.subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(lt, 'time', reloj_falso(sleep=dormir))
    assert REAL_LIVE_SPAWN(wav, recorder) is not None
    assert lanzado == [True]


def test_una_grabacion_corta_no_llega_a_lanzarlo(tr_dirs, wav, monkeypatch):
    """Si para antes de START_DELAY_S, no se lanza: un proceso recien nacido no
    se deja matar durante unos segundos y quedaria suspendido y vivo."""
    escribir_ajustes(tr_dirs, '{}')
    reloj = {'t': 0.0}
    grabando = {'si': True}

    def dormir(s):
        reloj['t'] += s
        if reloj['t'] >= 5:
            grabando['si'] = False           # paro a los 5 s

    monkeypatch.setattr(lt, 'time', reloj_falso(monotonic=lambda: reloj['t'], sleep=dormir))
    monkeypatch.setattr(lt.subprocess, 'Popen', lambda *_a, **_k: pytest.fail('lanzo un proceso'))
    assert REAL_LIVE_SPAWN(wav, still_wanted=lambda: grabando['si']) is None


def test_cancelar_borra_audio_y_texto_en_vivo(tray, wav, monkeypatch):
    """cancel() primero (deja sin efecto un stop() simultaneo), despues el proceso
    en vivo, y despues se borra lo que quede: la reunion se descarto, y ni su
    audio ni su texto deben quedarse en disco."""
    orden = []
    recorder = type('R', (), {'is_recording': True,
                              'cancel': lambda self: orden.append('cancel')})()
    tray._recorder = recorder
    tray._recording_path = wav
    tray._live[wav.stem] = (FakeProc(), wav)
    monkeypatch.setattr(lt, 'kill', lambda *_a: orden.append('kill'))
    restos = [lt.part_path(wav, 'mic'), lt.part_path(wav, 'loop'),
              lt.jsonl_path(wav), lt.status_path(wav)]
    for p in restos:
        p.write_text('x')
    tray._cancel_recording()
    assert orden == ['cancel', 'kill']
    assert [p.name for p in restos if p.exists()] == []


class TercoProc:
    """Proceso que deniega el acceso las primeras veces, como uno recien nacido."""

    def __init__(self, psutil, rechazos):
        self.psutil, self.rechazos, self.pid, self.vivo = psutil, rechazos, 4242, True

    def kill(self):
        if self.rechazos:
            self.rechazos -= 1
            raise self.psutil.AccessDenied(self.pid)
        self.vivo = False

    def is_running(self):
        return self.vivo

    def status(self):
        return 'running'


def test_kill_insiste_mientras_le_deniegan_el_acceso():
    import psutil
    p = TercoProc(psutil, rechazos=3)
    assert lt._kill_until(psutil, [p], timeout=5) == []
    assert not p.vivo


def test_lo_que_sobrevive_al_tope_se_devuelve_para_el_reaper():
    import psutil
    p = TercoProc(psutil, rechazos=10 ** 6)
    assert lt._kill_until(psutil, [p], timeout=0.3) == [p]


def test_kill_sin_nada_que_matar_no_lanza(wav):
    lt.kill(None, wav)
    lt.kill(None, None)


# ── Lo que encontro la revision adversaria del 18/10 ────────────────────────

class Terco:
    """Proceso falso que apunta en que orden se le mata."""

    def __init__(self, psutil, nombre, orden, rechazos=0, hijos=()):
        self.psutil, self.nombre, self.orden = psutil, nombre, orden
        self.rechazos, self.hijos, self.vivo, self.pid = rechazos, list(hijos), True, id(self)

    def kill(self):
        if self.rechazos:
            self.rechazos -= 1
            raise self.psutil.AccessDenied(self.pid)
        self.vivo = False
        self.orden.append(self.nombre)

    def is_running(self):
        return self.vivo

    def status(self):
        return 'running'

    def children(self, recursive=False):
        return self.hijos


def test_el_lanzador_solo_muere_despues_del_interprete(monkeypatch):
    """Matar el lanzador con el interprete aun suspendido lo dejaba congelado para
    siempre (medido el 18/10). Vivo el lanzador, lo reanuda y se va solo."""
    import psutil
    orden = []
    hijo = Terco(psutil, 'interprete', orden, rechazos=2)
    lanzador = Terco(psutil, 'lanzador', orden)
    monkeypatch.setattr(lt, 'time', reloj_falso(sleep=lambda _s: None))
    lt._reap(psutil, [hijo], lanzador)
    assert orden == ['interprete', 'lanzador']


def test_si_el_interprete_no_se_deja_matar_el_lanzador_sigue_vivo(monkeypatch):
    import psutil
    orden, reaped = [], []
    hijo = Terco(psutil, 'interprete', orden, rechazos=10 ** 6)
    lanzador = Terco(psutil, 'lanzador', orden, hijos=[hijo])
    monkeypatch.setattr(psutil, 'Process', lambda _pid: lanzador)
    monkeypatch.setattr(lt, '_reap', lambda _ps, left, launcher: reaped.append((left, launcher)))
    proc = type('P', (), {'pid': 1, 'kill': lambda s: orden.append('popen'),
                          'wait': lambda s, timeout=None: None})()
    lt.kill(proc, None, timeout=0.3)
    assert esperar(lambda: reaped == [([hijo], lanzador)])
    assert 'lanzador' not in orden and 'popen' not in orden


def test_con_poca_memoria_el_cierre_baja_de_modelo(wav, model, monkeypatch, me):
    """La misma escalera que el camino de siempre (transcriber._fallback_plan):
    cargar un modelo sin memoria tumba el daemon (17/09)."""
    pedidos = []
    monkeypatch.setattr(transcriber, '_available_mb', lambda: 500)
    monkeypatch.setattr(transcriber, '_get_model', lambda name=None: pedidos.append(name) or model)
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    assert lt.finish_live(wav) is not None
    assert pedidos and pedidos[0] not in ('medium', 'small')


def test_en_pausa_el_cierre_no_elige_modelo_hasta_que_acaba(wav, model, monkeypatch, me):
    """Revision del 19/10: la escalera miraba la RAM antes de la pausa, con el
    'small' del en vivo aun cargado, y podia saltarse 'medium' sin avisar."""
    import types
    estado = {'pausa': True}
    pausas = iter([True, True, False])

    def should_pause():
        estado['pausa'] = next(pausas, False)
        return estado['pausa']

    planes = []
    real = lt._remainder_models
    monkeypatch.setattr(lt, '_remainder_models', lambda fast: planes.append(estado['pausa']) or real(fast))
    monkeypatch.setattr(transcriber, 'time', types.SimpleNamespace(sleep=lambda _s: None))
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    assert lt.finish_live(wav, should_pause=should_pause) is not None
    assert planes == [False]


def test_tras_dos_cierres_fallidos_se_usa_el_camino_de_siempre(wav, model):
    """Si el daemon cayo dos veces a mitad del cierre, no se vuelve a intentar:
    podria tumbarlo otra vez en cada arranque."""
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    lt.finishing_path(wav).write_text('2')
    assert lt.finish_live(wav) is None
    assert model.lengths == []


def test_tras_un_cierre_fallido_se_reintenta(wav, model, me):
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    lt.finishing_path(wav).write_text('1')
    assert lt.finish_live(wav) is not None


def test_la_marca_existe_mientras_se_cierra_y_desaparece_al_terminar(wav, model, me):
    vista = []
    original = model.transcribe

    def mirar(audio, **kw):
        vista.append(lt.finishing_path(wav).exists())
        return original(audio, **kw)

    model.transcribe = mirar
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    assert lt.finish_live(wav) is not None
    assert vista == [True]
    assert not lt.finishing_path(wav).exists()


def test_si_el_loopback_tenia_voz_pero_falta_loop_wav_se_usa_el_camino_de_siempre(wav, model):
    """Fallo escribir .loop.wav: solo el micro perderia a los demas; la mezcla los tiene."""
    write_records(wav, rec('mic', 0, 10, (1.0, 'hola')), rec('loop', 0, 10, (2.0, 'buenas')))
    make_track(lt.track_wav(wav, 'mic'), 10)
    assert lt.finish_live(wav) is None


class VadEspia:
    def __init__(self, speech):
        self.speech, self.picos = speech, []

    def __call__(self, audio, _opts):
        self.picos.append(float(np.max(np.abs(audio))))
        return self.speech


def pista_baja(wav, segundos=20, pico=0.01):
    part = lt.part_path(wav, 'mic')
    sf.write(str(part), np.full(int(segundos * SR), pico, dtype=np.float32), SR,
             format='WAV', subtype='FLOAT')
    return lt._Track('mic', part, 0)


def test_el_vad_ve_el_audio_normalizado(wav):
    """Una pista muy baja no puede darse por 'sin voz': ese trozo se
    comprometeria vacio y esa voz no la recuperaria nadie."""
    vad = VadEspia([])
    lt._step(pista_baja(wav), TrackModel(), None, {}, vad, None, wav)
    # pico 0,01 -> ganancia 50 (el tope) -> el VAD ve 0,5, no 0,01
    assert vad.picos and vad.picos[0] == pytest.approx(0.5, rel=1e-3)


def info_con(prob):
    class M(TrackModel):
        def transcribe(self, audio, **kw):
            segs, info = super().transcribe(audio, **kw)
            info.language, info.language_probability = 'en', prob
            return segs, info
    return M()


def test_el_idioma_no_se_fija_con_poca_confianza(wav):
    vad = VadEspia([{'start': 0, 'end': 18 * SR}])
    done, detected = lt._step(pista_baja(wav), info_con(0.5), None, {}, vad, None, wav)
    assert done and detected is None
    assert lt.read_records(wav)[0]['lang'] == 'en', "el trozo guarda su idioma para la mayoria"


def test_el_idioma_se_fija_con_confianza_alta(wav):
    vad = VadEspia([{'start': 0, 'end': 18 * SR}])
    _done, detected = lt._step(pista_baja(wav), info_con(0.95), None, {}, vad, None, wav)
    assert detected == 'en'


def test_sin_idioma_fijado_el_cierre_usa_el_de_la_mayoria(wav, model, me):
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola'), lang='en'),
                  rec('mic', 15, 30, (16.0, 'que tal'), lang='en'),
                  rec('mic', 30, 45, (31.0, 'bien'), lang='pt'))
    make_track(lt.track_wav(wav, 'mic'), 50)
    _, lang = lt.finish_live(wav)
    assert lang == 'en'


def test_la_limpieza_no_tumba_el_arranque_si_un_fichero_desaparece(tr_dirs, monkeypatch):
    """Corre al arrancar el daemon: una excepcion aqui lo dejaba sin arrancar."""
    f = ta.RECORDINGS_DIR / f'{STEM}.live.json.tmp'
    f.write_text('x')
    clase = type(f)
    original = clase.stat

    def stat(self, *a, **k):
        if self.name.endswith('.tmp'):
            raise FileNotFoundError(self)
        return original(self, *a, **k)

    monkeypatch.setattr(clase, 'stat', stat)
    storage.cleanup_old_recordings()


def test_recover_pending_no_confunde_un_titulo_acabado_en_mic(tray, wav_factory, monkeypatch):
    """Una reunion titulada 'Sync.mic' es una reunion; una pista con su grabacion
    principal al lado (o ya en processed/) no lo es."""
    real = wav_factory(ta.RECORDINGS_DIR / '2026-10-18_09-00_Sync.mic.wav', seconds=0.1)
    wav_factory(ta.RECORDINGS_DIR / 'processed' / '2026-10-18_08-00_Daily.wav', seconds=0.1)
    pista = wav_factory(ta.RECORDINGS_DIR / '2026-10-18_08-00_Daily.loop.wav', seconds=0.1)
    monkeypatch.setattr(ta.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(tray, '_notify', lambda *_a: None)
    tray._pipeline_queue = queue.Queue()
    tray._recover_pending()
    encolados = [p.name for p in tray._pipeline_queue.queue]
    assert real.name in encolados and pista.name not in encolados


def test_el_hilo_de_lanzamiento_usa_el_spawn_de_cuando_se_pidio(tray, wav, monkeypatch):
    """Si buscara spawn al arrancar el hilo, un parche ya deshecho (tests) dejaria
    lanzar un proceso real."""
    import types
    hilos, llamados = [], []
    monkeypatch.setattr(ta, 'threading', types.SimpleNamespace(
        Thread=lambda target, **_k: types.SimpleNamespace(start=lambda: hilos.append(target))))
    monkeypatch.setattr(lt, 'spawn', lambda *_a, **_k: llamados.append('A'))
    tray._start_live(wav)
    monkeypatch.setattr(lt, 'spawn', lambda *_a, **_k: llamados.append('B'))
    hilos[0]()
    assert llamados == ['A']


# -- Utilidades del proceso aparte -------------------------------------------

def test_la_memoria_libre_se_lee_de_windows():
    """GlobalMemoryStatusEx: de ahi salen la RAM libre y la reserva de memoria
    (commit) que miran _memory_ok y _memory_floor_ok antes de cargar Whisper."""
    import os
    st = lt._mem_status()
    if os.name != 'nt':
        assert st is None
        return
    ram, reserva = st
    assert ram > 0 and reserva > 0


@pytest.mark.parametrize('reserva, esperado', [(lt.COMMIT_FLOOR_MB - 1, False), (lt.COMMIT_FLOOR_MB, True)])
def test_el_suelo_de_memoria(monkeypatch, reserva, esperado):
    monkeypatch.setattr(lt, '_mem_status', lambda: (8000.0, float(reserva)))
    assert lt._memory_floor_ok() is esperado


def test_sin_poder_leer_la_memoria_no_se_frena(monkeypatch):
    """Si Windows no contesta, se sigue: frenar sin datos dejaria la reunion sin
    transcribir hasta el cierre por una lectura que fallo."""
    monkeypatch.setattr(lt, '_mem_status', lambda: None)
    assert lt._memory_floor_ok() is True


def test_la_prioridad_se_pide_a_windows(monkeypatch):
    import os

    import psutil
    pedidas = []

    class Proceso:
        def nice(self, cls):
            pedidas.append(cls)

    monkeypatch.setattr(psutil, 'Process', Proceso)
    lt._set_priority('idle')
    if os.name == 'nt':
        assert pedidas == [psutil.IDLE_PRIORITY_CLASS]
    else:
        assert pedidas == []


def test_una_prioridad_que_falla_no_tumba_el_proceso(monkeypatch):
    import psutil

    def revienta():
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, 'Process', revienta)
    lt._set_priority('below')           # no debe lanzar


def test_el_daemon_vivo_se_reconoce_por_pid_y_hora_de_arranque():
    """Pid y hora juntos: un pid reciclado por otro proceso no cuenta como vivo."""
    import os

    import psutil
    yo = psutil.Process(os.getpid())
    assert lt._daemon_alive(yo.pid, yo.create_time()) is True
    assert lt._daemon_alive(yo.pid, yo.create_time() - 60) is False
    assert lt._daemon_alive(2 ** 31 - 1, 0.0) is False


def test_el_proceso_aparte_escribe_su_propio_log(tr_dirs):
    import logging
    raiz = logging.getLogger()
    antes = list(raiz.handlers)
    try:
        lt._setup_logging()
        nuevos = [h for h in raiz.handlers if h not in antes]
        assert len(nuevos) == 1 and nuevos[0].baseFilename.endswith('live.log')
    finally:
        for h in [h for h in raiz.handlers if h not in antes]:
            raiz.removeHandler(h)
            h.close()

def test_el_cierre_dice_cuanto_lleva_de_lo_que_queda(wav, model, me):
    """La tarjeta "En curso" muestra el % del cierre: de 0 a 100 sobre lo que
    queda por transcribir, no sobre la reunion entera."""
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    vistos = []
    lt.finish_live(wav, on_progress=vistos.append)
    assert vistos and vistos[-1] == 100 and vistos == sorted(vistos)

def test_al_cancelar_la_marca_se_queda_solo_si_quedan_temporales(tray, wav, monkeypatch):
    """La marca .discarded es para el siguiente arranque (recover_orphan_parts):
    si los .part se borraron, sobra; si no, se queda y alli se borran."""
    recorder = type('R', (), {'is_recording': True, 'cancel': lambda self: None})()
    tray._recorder = recorder
    tray._recording_path = wav
    terco = lt.part_path(wav, 'mic')
    terco.write_text('x')
    monkeypatch.setattr(lt, 'discard_artifacts', lambda *_a, **_k: None)   # el borrado falla
    tray._cancel_recording()
    assert wav.with_suffix('.discarded').exists()

    tray._recorder = recorder
    tray._recording_path = wav
    terco.unlink()
    tray._cancel_recording()
    assert not wav.with_suffix('.discarded').exists()



# ── Medicion: voz por trozo y aviso de trozo sospechoso ──────────────────────

def _trozo(wav, voz_s, lineas, caplog):
    segs = tuple((float(i), i + 1.0, f'l{i}') for i in range(lineas))
    vad = VadEspia([{'start': 0, 'end': int(voz_s * SR)}])
    caplog.set_level('INFO', logger=lt.log.name)
    done, _ = lt._step(pista_baja(wav, 160), TrackModel(segments=segs), None, {}, vad, None,
                       wav, min_s=15, max_s=160)
    assert done
    return lt.read_records(wav)[0]


def test_el_registro_del_trozo_guarda_los_segundos_con_voz(wav, caplog):
    """18/11/2026: un trozo de la pista de Teams tuvo 150 s de voz y 5 lineas y
    no habia forma de medir cuanta voz vio cada trozo."""
    r = _trozo(wav, 100, 40, caplog)
    assert r['voiced'] == pytest.approx(100, abs=1)


def test_un_registro_viejo_sin_voiced_se_sigue_leyendo(wav, model, me):
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))
    assert 'voiced' not in lt.read_records(wav)[0]
    make_track(lt.track_wav(wav, 'mic'), 20)
    assert lt.finish_live(wav) is not None


def test_avisa_de_un_trozo_con_mucha_voz_y_casi_sin_texto(wav, caplog):
    _trozo(wav, 150, 5, caplog)
    avisos = [r for r in caplog.records if 'trozo sospechoso' in r.getMessage()]
    assert len(avisos) == 1
    m = avisos[0].getMessage()
    assert 'mic' in m and 'lineas 5' in m and 'voz 150' in m


def test_no_avisa_con_texto_suficiente(wav, caplog):
    _trozo(wav, 150, 40, caplog)
    assert not [r for r in caplog.records if 'sospechoso' in r.getMessage()]


def test_no_avisa_con_poca_voz_aunque_haya_pocas_lineas(wav, caplog):
    _trozo(wav, 20, 0, caplog)
    assert not [r for r in caplog.records if 'sospechoso' in r.getMessage()]
