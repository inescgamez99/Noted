"""El idioma de una reunion solo puede ser uno de los que se usan de verdad.

Caso real, 18/11/2026: una reunion salio como 'jw' (javanes), y las minutas se
generaron con ese idioma. Whisper conoce 99 y con audio dudoso declara cualquiera.
"""
import types

import pytest

import config
import transcriber as tr

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sin_ajustes(monkeypatch):
    monkeypatch.setattr(config, 'read_settings', lambda: {})


def test_los_idiomas_de_siempre_se_dejan_como_estan():
    for lang in ('es', 'en', 'ca', 'fr', 'de'):
        assert tr.allowed_choice(lang, 0.9, [('uk', 0.05)]) == (lang, 0.9)


def test_un_idioma_raro_cede_al_permitido_mas_probable_con_su_confianza():
    probs = [('jw', 0.55), ('es', 0.30), ('en', 0.10), ('ca', 0.02)]
    assert tr.allowed_choice('jw', 0.55, probs) == ('es', 0.30)


def test_la_confianza_baja_del_sustituto_no_fija_el_idioma():
    """Con la del 'jw' (0,9) se habria fijado: ahora next_language no lo fija."""
    lang, prob = tr.allowed_choice('jw', 0.9, [('jw', 0.9), ('es', 0.05)])
    assert tr.next_language(None, (lang, prob)) is None


def test_sin_probabilidades_se_usa_la_tabla_de_siempre():
    assert tr.allowed_choice('uk', 0.66, None) == ('en', 0.66)
    assert tr.allowed_choice('gl', 0.7, []) == ('es', 0.7)


def test_sin_probabilidades_y_sin_tabla_cae_en_el_primero():
    assert tr.allowed_choice('jw', 0.5, None) == ('es', 0.5)


def test_los_ajustes_cambian_los_idiomas(monkeypatch):
    monkeypatch.setattr(config, 'read_settings', lambda: {'meeting_languages': ['en', ' PT ', 3]})
    assert config.meeting_languages() == ('en', 'pt')
    assert tr.allowed_choice('es', 0.9, [('en', 0.05)]) == ('en', 0.05)


@pytest.mark.parametrize('valor', [None, [], 'es', [1, 2], {'es': 1}])
def test_unos_ajustes_invalidos_usan_los_de_siempre(monkeypatch, valor):
    monkeypatch.setattr(config, 'read_settings', lambda: {'meeting_languages': valor})
    assert config.meeting_languages() == config.MEETING_LANGUAGES


def test_detect_usa_las_probabilidades_del_modelo():
    modelo = types.SimpleNamespace(detect_language=lambda *a, **k: ('jw', 0.6, [('jw', 0.6), ('en', 0.3)]))
    assert tr._detect(modelo, object()) == ('en', 0.3)


def test_el_idioma_de_un_info_tambien_se_restringe():
    info = types.SimpleNamespace(language='jw', language_probability=0.9,
                                 all_language_probs=[('jw', 0.9), ('es', 0.06), ('en', 0.02)])
    assert tr._remap_info(info) == 'es'
    assert tr._remap_info(types.SimpleNamespace(language=None)) == 'es'
