"""El idioma se mira por tramos de cada pista, no una sola vez al principio.

05/11/2026: en una sesion de una hora en ingles, los primeros 100 s fueron una
charla en castellano. Se fijo 'es' y todo lo dicho en ingles salio traducido al
castellano. Ahora el primero se fija con confianza alta (LANG_LOCK_PROB) y un
tramo solo lo cambia si Whisper esta muy seguro (LANG_SWITCH_PROB) y hay voz de
sobra (LANG_CHECK_MIN_VOICE_S). Sin ningun tramo seguro, como antes (ver
test_idioma_dos_pistas.py).
"""
import numpy as np
import pytest
import soundfile as sf

import live_transcriber as lt
import transcriber
import tray_app as ta
from conftest import FakeInfo, FakeSegment

pytestmark = pytest.mark.unit

SR = 16000
ES, EN = 0.1, 0.2          # el doble de Whisper deduce el idioma del volumen


def idioma(audio) -> str:
    nivel = float(np.mean(np.abs(audio))) if len(audio) else 0.0
    return 'en' if nivel > 0.15 else 'es'


class Bilingue:
    """Detecta por el volumen y transcribe un segmento cada 60 s con el idioma pedido."""

    def __init__(self, prob=0.95):
        self.prob = prob
        self.detecciones = 0
        self.llamadas = []          # (que, idioma pedido)

    def detect_language(self, audio, **_kw):
        self.detecciones += 1
        return idioma(audio), self.prob, []

    def transcribe(self, audio, **kw):
        if isinstance(audio, str):
            que, segundos = audio, sf.info(audio).duration
        else:
            que, segundos = len(audio), len(audio) / SR
        lang = kw.get('language')
        self.llamadas.append((que, lang))
        segs = [FakeSegment(t, t + 50.0, f'dicho en {lang}') for t in np.arange(0, segundos, 60.0)]
        return iter(segs), FakeInfo(lang or 'es', segundos)


@pytest.fixture(autouse=True)
def voz_si_no_es_silencio(monkeypatch):
    monkeypatch.setattr(transcriber, 'WHISPER_LANGUAGE', '')
    monkeypatch.setattr(transcriber, '_speech_seconds',
                        lambda audio: len(audio) / SR if len(audio) and np.max(np.abs(audio)) > 0 else 0.0)


def pista(path, *tramos):
    """tramos: (segundos, nivel); nivel 0 es silencio."""
    audio = np.concatenate([np.full(int(s * SR), nivel, dtype=np.float32) for s, nivel in tramos])
    sf.write(str(path), audio, SR)
    return path


# ── La regla ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('actual, visto, queda', [
    (None, None, None),
    (None, ('es', 0.5), None),        # el primero, solo con confianza alta
    (None, ('es', 0.85), 'es'),
    ('es', ('en', 0.85), 'es'),       # cambiar pide mas: un tramo dudoso no lo mueve
    ('es', ('en', 0.95), 'en'),
    ('es', ('es', 0.99), 'es'),
    ('es', None, 'es'),
])
def test_cuando_cambia_el_idioma(actual, visto, queda):
    assert transcriber.next_language(actual, visto) == queda


def test_sin_deteccion_o_con_idioma_fijo_no_se_mira(monkeypatch):
    sin_detectar = object()
    assert transcriber.recheck_language(sin_detectar, np.full(SR * 60, EN), 'es') == 'es'
    m = Bilingue()
    monkeypatch.setattr(transcriber, 'WHISPER_LANGUAGE', 'es')
    assert transcriber.recheck_language(m, np.full(SR * 60, EN), 'es') == 'es'
    assert transcriber._language_plan(m, transcriber._Audio(np.full(SR * 60, EN))) == []
    assert m.detecciones == 0


def test_con_poca_voz_no_se_mira():
    m = Bilingue()
    assert transcriber.recheck_language(m, np.full(SR * 60, EN), 'es', voiced_s=5.0) == 'es'
    assert m.detecciones == 0
    assert transcriber.recheck_language(m, np.full(SR * 60, EN), 'es') == 'en'


def test_si_detectar_falla_sigue_el_que_habia():
    class Roto(Bilingue):
        def detect_language(self, audio, **_kw):
            raise RuntimeError("sin memoria")

    assert transcriber.recheck_language(Roto(), np.full(SR * 60, EN), 'es') == 'es'


def test_el_plan_fija_con_el_primer_tramo_seguro_y_cambia_solo_con_seguridad():
    audio = np.concatenate([np.zeros(300 * SR, dtype=np.float32), np.full(300 * SR, ES, dtype=np.float32),
                            np.full(300 * SR, EN, dtype=np.float32)])
    plan = transcriber._language_plan(Bilingue(), transcriber._Audio(audio))
    assert [(a, b, lang) for a, b, lang, _v in plan] == [(0, 300, 'es'), (300, 600, 'es'), (600, 900, 'en')]
    dudoso = transcriber._language_plan(Bilingue(prob=0.85), transcriber._Audio(audio))
    assert [lang for _a, _b, lang, _v in dudoso] == ['es', 'es', 'es']
    assert transcriber._main_language(plan) in ('es', 'en')
    assert transcriber._main_language([(0, 300, 'es', 300.0), (300, 900, 'en', 600.0)]) == 'en'
    assert transcriber._main_language([(0, 300, None, 0.0)]) is None


# ── Dos pistas: el caso del 05/11 ───────────────────────────────────────────

@pytest.fixture
def base(tr_dirs):
    return tr_dirs / 'recordings' / 'sesion.wav'


def test_una_charla_al_principio_ya_no_traduce_toda_la_reunion(base, monkeypatch):
    monkeypatch.setattr(transcriber, 'ME_NAME', 'Felipe')
    pista(base.with_suffix('.mic.wav'), (300, ES), (600, EN))
    pista(base.with_suffix('.loop.wav'), (900, 0.0))
    m = Bilingue()

    texto, lang = transcriber._transcribe_with_speakers(m, base)

    assert lang == 'en'
    mias = [x for x in texto.splitlines() if '[Felipe]' in x]
    assert mias and all('dicho en es' in x for x in mias if x < '[05:00]')
    assert all('dicho en en' in x for x in mias if x >= '[05:00]')
    assert (str(base.with_suffix('.mic.wav')), 'en') in m.llamadas
    assert (str(base.with_suffix('.loop.wav')), 'en') in m.llamadas, "sin voz, la de la reunion"
    assert (300 * SR, 'es') in m.llamadas, "solo el tramo en castellano se rehace"


def test_cada_pista_va_en_su_idioma(base):
    pista(base.with_suffix('.mic.wav'), (300, ES))
    pista(base.with_suffix('.loop.wav'), (600, EN))
    m = Bilingue()

    _texto, lang = transcriber._transcribe_with_speakers(m, base)

    assert lang == 'en', "el de la reunion es el que tiene mas voz"
    assert (str(base.with_suffix('.mic.wav')), 'es') in m.llamadas
    assert (str(base.with_suffix('.loop.wav')), 'en') in m.llamadas


# ── La mezcla, entera y por trozos ──────────────────────────────────────────

def test_mezcla_entera_tambien_por_tramos(tmp_path):
    mezcla = pista(tmp_path / 'm.wav', (300, ES), (600, EN))
    m = Bilingue()
    texto, lang = transcriber._transcribe_whole(m, mezcla)
    assert lang == 'en'
    assert texto.splitlines()[0] == '[00:00] dicho en es'
    assert texto.splitlines()[-1] == '[14:00] dicho en en'


def test_por_trozos_cada_trozo_mira_su_idioma(tmp_path):
    mezcla = pista(tmp_path / 'm.wav', (300, ES), (600, EN))
    m = Bilingue()
    partial = []
    _texto, lang = transcriber._transcribe_chunks(m, mezcla, on_segment=partial.append)
    assert [lang for _que, lang in m.llamadas] == ['es', 'en', 'en']
    assert lang == 'en', "el de la mayor parte de la reunion"


# ── Durante la reunion y al cerrar ──────────────────────────────────────────

@pytest.fixture
def wav(tr_dirs):
    return ta.RECORDINGS_DIR / '2026-11-05_16-02_Sesion.wav'


class Fijo(Bilingue):
    """En vivo el trozo llega normalizado: el idioma no sale del volumen."""

    def __init__(self, lang, prob=0.95):
        super().__init__(prob)
        self.lang = lang

    def detect_language(self, audio, **_kw):
        self.detecciones += 1
        return self.lang, self.prob, []


def test_durante_la_reunion_cada_pista_vuelve_a_mirar_su_idioma(wav):
    part = lt.part_path(wav, 'loop')
    sf.write(str(part), np.full(70 * SR, 0.1, dtype=np.float32), SR, format='WAV', subtype='FLOAT')
    track = lt._Track('loop', part, 0)
    m = Fijo('en')

    def vad(audio, _opts):
        return [{'start': 0, 'end': len(audio)}]

    done, _ = lt._step(track, m, 'es', {}, vad, None, wav, min_s=15, max_s=30)
    assert done and track.lang == 'en'
    assert m.llamadas[-1][1] == 'en'
    assert lt.read_records(wav)[-1]['lang'] == 'en'

    done, _ = lt._step(track, m, 'es', {}, vad, None, wav, min_s=15, max_s=30)
    assert done and m.detecciones == 1, "no se vuelve a mirar hasta LANG_RECHECK_S despues"
    assert m.llamadas[-1][1] == 'en', "la pista sigue en el suyo"


def test_al_cerrar_cada_hueco_va_en_el_idioma_de_su_pista(wav, monkeypatch):
    m = Fijo('en', prob=0.5)      # dudoso: no cambia nada, solo cuenta el de la pista
    monkeypatch.setattr(transcriber, '_get_model', lambda name=None: m)
    monkeypatch.setattr(transcriber, '_release_model', lambda: None)
    lt._write_status(wav, lang='es')
    for r in ({'track': 'mic', 'start_frame': 0, 'end_frame': 60 * SR,
               'segments': [[1.0, 'hola']], 'lang': 'es', 'model': lt.final_model()},
              {'track': 'loop', 'start_frame': 0, 'end_frame': 60 * SR,
               'segments': [[2.0, 'hello']], 'lang': 'en', 'model': lt.final_model()}):
        lt._append_record(wav, r)
    pista(lt.track_wav(wav, 'mic'), (90, ES))
    pista(lt.track_wav(wav, 'loop'), (90, EN))

    _texto, lang = lt.finish_live(wav)

    assert lang == 'es'
    nuevos = [r for r in lt.read_records(wav) if r['start_frame'] == 60 * SR]
    assert {r['track']: r['lang'] for r in nuevos} == {'mic': 'es', 'loop': 'en'}
