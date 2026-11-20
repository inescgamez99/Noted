"""Reanudar la transcripcion, elegir modelo por memoria y reportar progreso.

INCIDENTE QUE ORIGINO ESTOS TESTS (17/09/2026)
El proceso se quedo sin memoria al 55% de una reunion de 37 minutos, murio con
un crash nativo 0xC0000409 (STACK_BUFFER_OVERRUN) mientras CARGABA el modelo
siguiente, el watchdog lo relanzo y la transcripcion arranco desde el
principio. Una hora de reproceso, y la barra de progreso parecia colgada
porque en modo troceado solo se actualizaba una vez por trozo de 300 s.
"""
import pytest

import transcriber as tr

pytestmark = pytest.mark.unit


# ── Leer el .partial de un intento que murio ────────────────────────────────

def test_sin_fichero_no_reanuda():
    assert tr.partial_resume(None) == (0.0, [])


def test_fichero_inexistente_no_reanuda(tr_dirs):
    assert tr.partial_resume(tr_dirs / 'no_existe.partial') == (0.0, [])


def test_por_debajo_del_primer_trozo_no_reanuda(tr_dirs):
    """Con menos de un trozo hecho no hay nada que conservar: se rehace."""
    p = tr_dirs / 'a.partial'
    p.write_text('[00:05] hola\n[01:20] que tal\n[04:59] casi\n', encoding='utf-8')
    assert tr.partial_resume(p) == (0.0, [])


def test_reanuda_al_inicio_del_trozo_interrumpido(tr_dirs):
    """Dentro de un trozo las lineas se emiten a medida que salen, asi que las
    del trozo interrumpido pueden estar incompletas y se rehacen enteras."""
    p = tr_dirs / 'a.partial'
    p.write_text('\n'.join(f'[{m:02d}:00] linea {m}' for m in range(8)), encoding='utf-8')

    at, kept = tr.partial_resume(p)

    assert at == 300.0                                    # 07:00 -> trozo 1 -> 300 s
    assert [k[:7] for k in kept] == ['[00:00]', '[01:00]', '[02:00]', '[03:00]', '[04:00]']


def test_alinea_al_trozo_y_no_al_ultimo_timestamp(tr_dirs):
    p = tr_dirs / 'a.partial'
    p.write_text('[12:00] doce\n', encoding='utf-8')

    at, kept = tr.partial_resume(p)

    assert at == 600.0                                    # 720 s -> trozo 2 -> 600 s
    assert kept == [], "la linea del trozo interrumpido no se conserva"


def test_ignora_lineas_sin_timestamp(tr_dirs):
    """Un crash a mitad de escritura puede dejar la ultima linea a medias."""
    p = tr_dirs / 'a.partial'
    p.write_text('basura sin timestamp\n[06:00] valida\n[07:10] otra sin cerra',
                 encoding='utf-8')

    at, kept = tr.partial_resume(p)

    assert at == 300.0
    assert kept == []


def test_sin_ninguna_linea_con_timestamp_no_reanuda(tr_dirs):
    p = tr_dirs / 'a.partial'
    p.write_text('nada de nada\nni esto\n', encoding='utf-8')
    assert tr.partial_resume(p) == (0.0, [])


def test_aguanta_grabaciones_de_mas_de_una_hora(tr_dirs):
    """_format_time cuenta minutos totales, no hh:mm:ss: [72:30] son 72 min."""
    p = tr_dirs / 'a.partial'
    p.write_text('[72:30] mas de una hora\n', encoding='utf-8')
    at, _ = tr.partial_resume(p)
    assert at == 4200.0


def test_un_partial_ilegible_no_lanza(tr_dirs, monkeypatch):
    p = tr_dirs / 'a.partial'
    p.write_text('[10:00] x\n', encoding='utf-8')

    def boom(*_a, **_k):
        raise OSError('disco ocupado')

    monkeypatch.setattr(type(p), 'read_text', boom)
    assert tr.partial_resume(p) == (0.0, [])


# ── No intentar un modelo que no cabe ──────────────────────────────────────

@pytest.mark.parametrize('disponible_mb, esperado', [
    (8000.0, 'medium'),        # de sobra
    (1400.0, 'small'),         # medium (2600) no cabe
    (400.0, 'tiny'),           # solo el mas pequeno
])
def test_el_primer_modelo_del_plan_depende_de_la_memoria(monkeypatch, disponible_mb, esperado):
    monkeypatch.setattr(tr, '_available_mb', lambda: disponible_mb)
    plan = tr._fallback_plan('medium')
    assert plan[0][0] == esperado


def test_con_poca_memoria_no_se_intenta_medium(monkeypatch):
    """Cargar un modelo con la memoria agotada es lo que tiro el proceso."""
    monkeypatch.setattr(tr, '_available_mb', lambda: 400.0)
    modelos = {m for m, _ in tr._fallback_plan('medium')}
    assert 'medium' not in modelos
    assert modelos == {'tiny'}


def test_sin_memoria_para_nada_aun_se_intenta_el_mas_pequeno(monkeypatch):
    """Una transcripcion con 'tiny' es mejor que ninguna."""
    monkeypatch.setattr(tr, '_available_mb', lambda: 10.0)
    plan = tr._fallback_plan('medium')
    assert plan == [('tiny', False), ('tiny', True)]


def test_si_no_se_puede_medir_la_memoria_no_se_descarta_nada(monkeypatch):
    monkeypatch.setattr(tr, '_available_mb', lambda: None)
    assert tr._fallback_plan('medium')[0] == ('medium', False)


def test_el_plan_alterna_entero_y_troceado_por_modelo(monkeypatch):
    monkeypatch.setattr(tr, '_available_mb', lambda: None)
    plan = tr._fallback_plan('small')
    assert plan[:4] == [('small', False), ('small', True),
                        ('base', False), ('base', True)]


def test_un_modelo_desconocido_cae_a_la_escalera_por_defecto(monkeypatch):
    monkeypatch.setattr(tr, '_available_mb', lambda: None)
    modelos = [m for m, _ in tr._fallback_plan('inventado')]
    assert modelos[0] == 'inventado'
    assert 'tiny' in modelos


def test_available_mb_no_lanza_sin_psutil(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'psutil', None)
    assert tr._available_mb() is None


# ── Reanudar de verdad, sin duplicar ni perder audio ───────────────────────

SR = 1000          # WAV sintetico barato: el codigo usa info.samplerate
DUR = 900          # 15 min -> 3 trozos de 300 s


@pytest.fixture
def wav_15min(wav_factory, tr_dirs):
    return wav_factory(tr_dirs / 'recordings' / 'fake.wav',
                       seconds=DUR, samplerate=SR, freq=0, subtype='FLOAT')


@pytest.fixture
def modelo(fake_whisper_model):
    return fake_whisper_model(segments=[(0.0, 10.0, 'seg A'), (150.0, 160.0, 'seg B')])


def test_sin_reanudar_procesa_todo_el_audio(modelo, wav_15min):
    text, _ = tr._transcribe_chunks(modelo, wav_15min)

    assert [n / SR for n in modelo.audio_lengths] == [300.0, 300.0, 300.0]
    assert len(text.splitlines()) == 6


def test_reanudando_solo_procesa_el_audio_que_faltaba(modelo, wav_15min):
    prior = ['[00:00] ya estaba', '[05:00] tambien']

    text, _ = tr._transcribe_chunks(modelo, wav_15min, resume_at=600.0, prior_lines=prior)

    assert [n / SR for n in modelo.audio_lengths] == [300.0], "reproceso audio ya hecho"
    lineas = text.splitlines()
    assert lineas[:2] == prior, "perdio las lineas del intento anterior"
    assert len(lineas) == 4, f"duplico lineas: {lineas}"


def test_los_timestamps_reanudados_parten_del_offset_correcto(modelo, wav_15min):
    text, _ = tr._transcribe_chunks(modelo, wav_15min, resume_at=600.0,
                                    prior_lines=['[00:00] x'])
    assert text.splitlines()[1].startswith('[10:00]')


def test_reanudar_pasado_el_final_no_lanza_y_no_procesa_nada(modelo, wav_15min):
    prior = ['[00:00] a', '[05:00] b']

    text, _ = tr._transcribe_chunks(modelo, wav_15min, resume_at=99999.0, prior_lines=prior)

    assert modelo.audio_lengths == []
    assert text.splitlines() == prior


def test_devuelve_el_idioma_detectado(modelo, wav_15min):
    _, lang = tr._transcribe_chunks(modelo, wav_15min)
    assert lang == 'es'


def test_sin_idioma_detectado_cae_a_es(fake_whisper_model, wav_15min):
    m = fake_whisper_model(language=None)
    _, lang = tr._transcribe_chunks(m, wav_15min)
    assert lang == 'es'


# ── La barra de progreso no se queda clavada ───────────────────────────────

def test_el_progreso_se_reporta_mas_de_una_vez_por_trozo(modelo, wav_15min):
    """Con trozos de 300 s la barra saltaba de 13,6% en 13,6% con minutos de
    nada en medio, y el usuario la veia colgada aunque estuviera trabajando."""
    avisos = []
    tr._transcribe_chunks(modelo, wav_15min, on_progress=avisos.append)
    assert len(avisos) > 3, f"solo {len(avisos)} avisos en 3 trozos"


def test_el_progreso_nunca_retrocede(modelo, wav_15min):
    avisos = []
    tr._transcribe_chunks(modelo, wav_15min, on_progress=avisos.append)
    assert all(b >= a for a, b in zip(avisos, avisos[1:], strict=False))


def test_el_progreso_termina_en_cien(modelo, wav_15min):
    avisos = []
    tr._transcribe_chunks(modelo, wav_15min, on_progress=avisos.append)
    assert avisos[-1] == 100


def test_hay_progreso_intermedio_dentro_del_primer_trozo(modelo, wav_15min):
    avisos = []
    tr._transcribe_chunks(modelo, wav_15min, on_progress=avisos.append)
    assert any(0 < v < 33 for v in avisos)


def test_reanudando_el_progreso_no_empieza_en_cero(modelo, wav_15min):
    avisos = []
    tr._transcribe_chunks(modelo, wav_15min, resume_at=600.0, on_progress=avisos.append)
    assert avisos[0] >= 60, f"la barra volveria atras: {avisos}"


# ── Al reanudar no se reintenta el modo entero ─────────────────────────────

def test_con_partial_usable_va_directo_al_troceado(monkeypatch, tr_dirs, wav_15min,
                                                   fake_whisper_model):
    """El modo entero no sabe arrancar por el medio, y es el que se quedo sin
    memoria. Con un .partial a medias ir por ahi es perder minutos seguro."""
    llamadas = {'entero': 0, 'troceado': 0, 'resume_at': None}

    def fake_whole(*_a, **_k):
        llamadas['entero'] += 1
        return ('x', 'es')

    def fake_chunks(*_a, **k):
        llamadas['troceado'] += 1
        llamadas['resume_at'] = k.get('resume_at')
        return ('y', 'es')

    monkeypatch.setattr(tr, '_get_model', lambda name=None: fake_whisper_model())
    monkeypatch.setattr(tr, '_transcribe_whole', fake_whole)
    monkeypatch.setattr(tr, '_transcribe_chunks', fake_chunks)

    partial = tr_dirs / 'r.partial'
    partial.write_text('[00:00] a\n[06:00] b\n', encoding='utf-8')
    tr._transcribe_local(wav_15min, resume_from=partial)

    assert llamadas['entero'] == 0
    assert llamadas['troceado'] == 1
    assert llamadas['resume_at'] == 300.0


def test_sin_partial_se_mantiene_el_orden_normal(monkeypatch, wav_15min, fake_whisper_model):
    llamadas = {'entero': 0, 'troceado': 0}
    monkeypatch.setattr(tr, '_get_model', lambda name=None: fake_whisper_model())
    monkeypatch.setattr(tr, '_transcribe_whole',
                        lambda *a, **k: (llamadas.__setitem__('entero', 1), ('x', 'es'))[1])
    monkeypatch.setattr(tr, '_transcribe_chunks',
                        lambda *a, **k: (llamadas.__setitem__('troceado', 1), ('y', 'es'))[1])

    tr._transcribe_local(wav_15min, resume_from=None)

    assert llamadas == {'entero': 1, 'troceado': 0}


def test_un_partial_vacio_no_fuerza_el_troceado(monkeypatch, tr_dirs, wav_15min,
                                                fake_whisper_model):
    llamadas = {'entero': 0}
    monkeypatch.setattr(tr, '_get_model', lambda name=None: fake_whisper_model())
    monkeypatch.setattr(tr, '_transcribe_whole',
                        lambda *a, **k: (llamadas.__setitem__('entero', 1), ('x', 'es'))[1])

    partial = tr_dirs / 'r.partial'
    partial.write_text('[00:10] apenas nada\n', encoding='utf-8')
    tr._transcribe_local(wav_15min, resume_from=partial)

    assert llamadas['entero'] == 1


# ── Modelo minimo opcional (whisper_min_model) ───────────────────────────

def test_con_minimo_medium_no_se_baja_aunque_falte_memoria(monkeypatch):
    """Quien prefiere calidad: con poca memoria se trocea medium en vez de bajar."""
    monkeypatch.setattr(tr, '_available_mb', lambda: 400.0)
    plan = tr._fallback_plan('medium', floor='medium')
    assert plan == [('medium', False), ('medium', True)]


def test_con_minimo_desde_un_modelo_mayor_se_para_en_el_minimo(monkeypatch):
    monkeypatch.setattr(tr, '_available_mb', lambda: None)
    modelos = [m for m, _ in tr._fallback_plan('large-v3', floor='medium')]
    assert modelos[-1] == 'medium' and 'small' not in modelos and modelos[0] == 'large-v3'


def test_sin_minimo_se_baja_como_siempre(monkeypatch):
    monkeypatch.setattr(tr, '_available_mb', lambda: None)
    monkeypatch.setattr(tr, 'WHISPER_MIN_MODEL', None)
    assert [m for m, _ in tr._fallback_plan('medium')][-1] == 'tiny'


def test_el_minimo_sale_de_los_ajustes(monkeypatch):
    monkeypatch.setattr(tr, '_available_mb', lambda: None)
    monkeypatch.setattr(tr, 'WHISPER_MIN_MODEL', 'small')
    assert [m for m, _ in tr._fallback_plan('medium')] == ['medium', 'medium', 'small', 'small']


def test_un_minimo_desconocido_se_ignora(monkeypatch):
    monkeypatch.setattr(tr, '_available_mb', lambda: None)
    assert [m for m, _ in tr._fallback_plan('medium', floor='enorme')][-1] == 'tiny'
