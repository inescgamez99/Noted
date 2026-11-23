"""Sin cascos, el micro de quien graba tambien capta a los demas por el altavoz.

Esas frases ya estan, limpias, en la pista de Teams, y salian dos veces en el
transcript, una atribuida a quien graba (entre 47 y 75 por reunion en reuniones
reales). transcriber._drop_mic_echo las quita del micro; lo que dice quien
graba, aunque sea a la vez, tiene otro texto y se conserva.
"""
import pytest

import live_transcriber as lt
import transcriber as tr

pytestmark = pytest.mark.unit

OTROS = [(10.0, 14.0, 'el presupuesto del piloto sube a ciento ochenta mil euros'),
         (30.0, 33.0, 'vale'),
         (50.0, 55.0, 'hay que validarlo con Marta antes del viernes')]


def test_la_frase_de_los_demas_captada_por_el_micro_se_quita():
    micro = [(10.5, 14.2, 'el presupuesto del piloto sube a ciento ochenta mil')]
    kept, quitadas = tr._drop_mic_echo(micro, OTROS)
    assert kept == [] and quitadas == 1


def test_lo_que_dice_quien_graba_a_la_vez_se_conserva():
    micro = [(11.0, 13.0, 'perdona, no te oigo bien desde aqui')]
    kept, quitadas = tr._drop_mic_echo(micro, OTROS)
    assert kept == micro and quitadas == 0


def test_la_misma_frase_lejos_en_el_tiempo_no_es_eco():
    micro = [(200.0, 204.0, 'el presupuesto del piloto sube a ciento ochenta mil euros')]
    kept, _ = tr._drop_mic_echo(micro, OTROS)
    assert kept == micro, "repetir algo minutos despues es una intervencion"


def test_una_frase_corta_solo_es_eco_si_coincide_entera():
    assert tr._drop_mic_echo([(30.2, 31.0, 'Vale.')], OTROS)[1] == 1
    assert tr._drop_mic_echo([(30.2, 31.0, 'claro')], OTROS)[1] == 0


def test_las_palabras_cortas_no_cuentan_para_decidir():
    """'de', 'que', 'el' coinciden entre frases distintas."""
    micro = [(51.0, 54.0, 'que no es lo de antes ni lo de hoy')]
    assert tr._drop_mic_echo(micro, OTROS)[1] == 0


def test_sin_pista_de_teams_no_se_quita_nada():
    micro = [(1.0, 2.0, 'hola')]
    assert tr._drop_mic_echo(micro, []) == (micro, 0)


def test_el_transcript_por_pistas_no_repite_la_frase(tmp_path, monkeypatch):
    """_transcribe_with_speakers: la frase sale una vez, como [Otros]."""
    import numpy as np
    import soundfile as sf
    base = tmp_path / 'reunion.wav'
    for pista in ('mic', 'loop'):
        sf.write(str(base.with_suffix(f'.{pista}.wav')), np.zeros(16000, dtype=np.float32), 16000)

    class Seg:
        def __init__(self, start, end, text):
            self.start, self.end, self.text = start, end, text

    class Info:
        language, duration, duration_after_vad = 'es', 1.0, 1.0

    class Modelo:
        def transcribe(self, path, **_kw):
            frase = Seg(0.1, 0.9, 'el presupuesto del piloto sube a ciento ochenta mil euros')
            propia = Seg(0.2, 0.8, 'perdona, repite la cifra por favor')
            return ([frase, propia] if path.endswith('.mic.wav') else [frase]), Info()

    monkeypatch.setattr(tr, '_language_plan', lambda *_a, **_k: [])
    texto, _ = tr._transcribe_with_speakers(Modelo(), base)
    assert texto.count('ciento ochenta mil') == 1
    assert '[Otros]: el presupuesto' in texto
    assert 'repite la cifra' in texto


# ── Durante la reunion (transcript en vivo) ──────────────────────────────

def test_en_vivo_tambien_se_quita_el_eco():
    por_pista = {'mic': [(10.4, 'el presupuesto del piloto sube a ciento ochenta mil euros'),
                         (40.0, 'yo me encargo del documento')],
                 'loop': [(10.0, 'el presupuesto del piloto sube a ciento ochenta mil euros')]}
    texto = lt.render(por_pista, labelled=True)
    assert texto.count('ciento ochenta mil') == 1
    assert 'yo me encargo' in texto


def test_en_vivo_sin_pista_de_teams_no_se_toca():
    por_pista = {'mic': [(1.0, 'hola a todos')]}
    assert 'hola a todos' in lt.render(por_pista, labelled=False)


def test_los_fines_se_estiman_por_el_siguiente_segmento():
    assert lt._with_ends([(5.0, 'b'), (0.0, 'a')]) == [(0.0, 5.0, 'a'), (5.0, 20.0, 'b')]


def test_si_el_filtro_falla_el_transcript_en_vivo_sale_igual(monkeypatch):
    def roto(*_a):
        raise ValueError('formato inesperado')
    monkeypatch.setattr(tr, '_drop_mic_echo', roto)
    mic = [(1.0, 'hola')]
    assert lt._without_echo(mic, [(1.0, 'hola')]) == mic
