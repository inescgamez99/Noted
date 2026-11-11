"""El idioma de una reunion grabada con dos pistas (micro y Teams).

29/10/2026: en una reunion en castellano en la que quien grababa no hablo,
faster-whisper adivino 'en' (51 %) sobre el micro en silencio, ese idioma se
le impuso a la pista de los demas y el transcript salio traducido al ingles.
"""
from types import SimpleNamespace

import pytest

import transcriber

pytestmark = pytest.mark.unit


class PorPista:
    """Cada pista con su idioma y su voz; apunta con que argumentos se llamo."""

    def __init__(self, mic, loop):
        self.pistas = {'mic': mic, 'loop': loop}
        self.llamadas = {}

    def transcribe(self, audio, **kwargs):
        pista = 'mic' if str(audio).endswith('.mic.wav') else 'loop'
        self.llamadas[pista] = kwargs
        idioma, voz, textos = self.pistas[pista]
        info = SimpleNamespace(language=idioma, language_probability=0.5, duration=150.0)
        if voz is not None:
            info.duration_after_vad = voz
        segs = [SimpleNamespace(start=i * 5.0, end=i * 5.0 + 4.0, text=t) for i, t in enumerate(textos)]
        return iter(segs), info


@pytest.fixture
def base(tr_dirs, wav_factory):
    path = tr_dirs / 'recordings' / 'reunion.wav'
    wav_factory(path.with_suffix('.mic.wav'), seconds=1, freq=0)
    wav_factory(path.with_suffix('.loop.wav'), seconds=1)
    return path


def test_con_el_micro_en_silencio_decide_la_pista_de_los_demas(base, monkeypatch):
    monkeypatch.setattr(transcriber, 'WHISPER_LANGUAGE', '')
    modelo = PorPista(mic=('en', 0.0, []), loop=('es', 110.0, ['hola a todos', 'empezamos']))

    texto, idioma = transcriber._transcribe_with_speakers(modelo, base)

    assert idioma == 'es'
    assert modelo.llamadas['loop'].get('language') is None, "se le impuso el idioma del micro vacio"
    assert 'hola a todos' in texto


def test_con_voz_en_el_micro_su_idioma_vale_para_las_dos(base):
    modelo = PorPista(mic=('es', 60.0, ['buenos dias']), loop=('en', 110.0, ['hello']))

    _, idioma = transcriber._transcribe_with_speakers(modelo, base)

    assert idioma == 'es'
    assert modelo.llamadas['loop']['language'] == 'es'
    assert 'language_detection_segments' not in modelo.llamadas['loop']


def test_sin_dato_de_voz_se_comporta_como_antes(base):
    modelo = PorPista(mic=('es', None, ['vale']), loop=('en', None, ['ok']))

    _, idioma = transcriber._transcribe_with_speakers(modelo, base)

    assert idioma == 'es'
    assert modelo.llamadas['loop']['language'] == 'es'


def test_si_tampoco_hay_voz_en_la_otra_pista_se_queda_el_del_micro(base, monkeypatch):
    monkeypatch.setattr(transcriber, 'WHISPER_LANGUAGE', '')
    modelo = PorPista(mic=('es', 4.0, ['si']), loop=('en', 2.0, []))

    _, idioma = transcriber._transcribe_with_speakers(modelo, base)

    assert idioma == 'es'
