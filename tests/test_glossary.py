"""Glosario: nombres, siglas y términos que Whisper transcribe mal.

Lo que protege:
- El glosario es un EXTRA: un fichero ausente, roto o con basura nunca puede
  tumbar la transcripción ni las minutas.
- Las correcciones solo tocan palabras completas ("ana" no puede cambiar
  "banana") y solo los errores que el usuario declaró.
- Las pistas a Whisper caben en su límite (~223 tokens) y llevan primero lo que
  más ayuda: los invitados de la reunión.
"""
import json
from datetime import datetime

import pytest

import glossary as gl
import minutes_generator as mg
import transcriber as tr

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sin_cache():
    gl._context_cache.clear()
    yield
    gl._context_cache.clear()


def E(term, category='term', aliases=(), meaning='', project=None):
    return {'term': term, 'category': category, 'aliases': list(aliases),
            'meaning': meaning, 'project': project}


# ── lectura y escritura ─────────────────────────────────────────────────────

def test_sin_fichero_el_glosario_esta_vacio():
    assert gl.load() == {'entries': [], 'suggestions': [], 'dismissed': []}


def test_un_fichero_roto_no_lanza():
    gl.GLOSSARY_FILE.write_text('{no es json', encoding='utf-8')
    assert gl.load() == {'entries': [], 'suggestions': [], 'dismissed': []}


@pytest.mark.parametrize('contenido', ['[]', '"hola"', '{"entries": "x"}', '{"entries": [1, null, {}]}'])
def test_contenido_con_forma_inesperada_se_ignora(contenido):
    gl.GLOSSARY_FILE.write_text(contenido, encoding='utf-8')
    assert gl.load()['entries'] == []


def test_guardar_y_cargar_conserva_las_entradas():
    assert gl.save({'entries': [E('Kinaxis', aliases=['Kinexis'], meaning='planificación')]})
    got = gl.load()['entries']
    assert got == [E('Kinaxis', aliases=['Kinexis'], meaning='planificación')]


def test_una_entrada_se_normaliza():
    gl.save({'entries': [{'term': '  IBP ', 'category': 'inventada',
                          'aliases': ['IVP', 'IVP', '', ' IVP '], 'project': ''}]})
    e = gl.load()['entries'][0]
    assert e['term'] == 'IBP'
    assert e['category'] == 'term'             # categoría desconocida
    assert e['aliases'] == ['IVP']             # sin vacías ni duplicados
    assert e['project'] is None


def test_la_escritura_no_deja_temporales():
    gl.save({'entries': [E('A')]})
    assert not list(gl.GLOSSARY_FILE.parent.glob('*.tmp'))


def test_un_bloqueo_momentaneo_al_guardar_se_reintenta(monkeypatch):
    """En Windows el renombrado falla con "Access is denied" si el antivirus o
    el indexador tienen el fichero abierto en ese instante: pasaba en estos
    mismos tests, a ratos, y en uso normal perdia la entrada recien guardada."""
    from pathlib import Path
    original, intentos = Path.replace, []

    def replace(self, target):
        intentos.append(1)
        if len(intentos) < 3:
            raise PermissionError(5, 'Access is denied')
        return original(self, target)

    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(gl.time, 'sleep', lambda _s: None)

    assert gl.save({'entries': [E('A')]}) is True
    assert len(intentos) == 3
    assert [e['term'] for e in gl.load()['entries']] == ['A']


def test_un_bloqueo_que_no_se_suelta_no_tumba_nada(monkeypatch):
    from pathlib import Path

    def replace(self, target):
        raise PermissionError(5, 'Access is denied')

    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(gl.time, 'sleep', lambda _s: None)

    assert gl.save({'entries': [E('A')]}) is False


def test_entries_mezcla_globales_y_las_del_proyecto():
    gl.save({'entries': [E('Global'), E('DeSandoz', project='sandoz'), E('DeOtro', project='otro')]})
    assert [e['term'] for e in gl.entries()] == ['Global']
    assert [e['term'] for e in gl.entries('sandoz')] == ['Global', 'DeSandoz']


# ── correcciones ────────────────────────────────────────────────────────────

def test_corrige_un_error_conocido():
    out = gl.apply_corrections('Hablamos de Kinexis hoy', [E('Kinaxis', aliases=['Kinexis'])])
    assert out == 'Hablamos de Kinaxis hoy'


def test_ignora_mayusculas_al_buscar():
    out = gl.apply_corrections('kinexis y KINEXIS', [E('Kinaxis', aliases=['Kinexis'])])
    assert out == 'Kinaxis y Kinaxis'


def test_solo_palabras_completas():
    """Cambiar 'banana' por 'bananaa' es peor que no corregir."""
    out = gl.apply_corrections('ana come banana', [E('Anna', aliases=['ana'])])
    assert out == 'Anna come banana'


def test_el_alias_puede_llevar_simbolos():
    out = gl.apply_corrections('el S&OP y el SOP', [E('S&OP', aliases=['S and OP', 'SNOP'])])
    assert out == 'el S&OP y el SOP'
    assert gl.apply_corrections('el S and OP', [E('S&OP', aliases=['S and OP'])]) == 'el S&OP'


def test_los_alias_mas_largos_van_primero():
    ents = [E('Kinaxis', aliases=['Kinexis']), E('Kinaxis Maestro', aliases=['Kinexis Maestro'])]
    assert gl.apply_corrections('Kinexis Maestro', ents) == 'Kinaxis Maestro'


def test_un_alias_igual_al_termino_no_hace_nada():
    assert gl.apply_corrections('IBP', [E('IBP', aliases=['ibp'])]) == 'IBP'


def test_un_alias_con_caracteres_de_regex_no_rompe():
    assert gl.apply_corrections('a (b) c', [E('X', aliases=['(b)'])]) == 'a X c'


@pytest.mark.parametrize('texto, ents', [('', [E('A', aliases=['B'])]), ('hola', []), ('hola', None)])
def test_sin_texto_o_sin_entradas_devuelve_lo_mismo(texto, ents):
    assert gl.apply_corrections(texto, ents) == texto


def test_no_toca_las_marcas_de_tiempo():
    out = gl.apply_corrections('[01:05] [Otros]: Kinexis', [E('Kinaxis', aliases=['Kinexis'])])
    assert out == '[01:05] [Otros]: Kinaxis'


# ── pistas para Whisper ─────────────────────────────────────────────────────

def test_los_invitados_van_primero():
    h = gl.build_hotwords([E('Kinaxis'), E('IBP', 'acronym')], ['Nurettin Ekizoglu'], 'IBP Review')
    assert h.split(', ')[0] == 'Nurettin Ekizoglu'
    assert 'Kinaxis' in h and 'IBP Review' in h


def test_sin_duplicados_ignorando_acentos_y_mayusculas():
    h = gl.build_hotwords([E('Inés')], ['ines'])
    assert h.lower().count('ines') + h.lower().count('inés') == 1


def test_las_pistas_respetan_el_limite():
    muchas = [E(f'Termino{i:03d}Largo') for i in range(200)]
    h = gl.build_hotwords(muchas, [f'Persona Numero{i}' for i in range(40)])
    assert len(h) <= gl._HOTWORDS_MAX_CHARS
    assert h.startswith('Persona Numero0')


def test_se_limitan_los_invitados():
    h = gl.build_hotwords([], [f'P{i}' for i in range(100)])
    assert len(h.split(', ')) == gl._MAX_INVITEES


def test_sin_nada_no_hay_pistas():
    assert gl.build_hotwords([], None, '') == ''


def test_las_siglas_van_antes_que_los_terminos():
    h = gl.build_hotwords([E('Kinaxis', 'term'), E('IBP', 'acronym')])
    assert h.split(', ') == ['IBP', 'Kinaxis']


# ── bloque del prompt de minutas ────────────────────────────────────────────

def test_el_bloque_lleva_significado_y_errores_vistos():
    s = gl.prompt_section([E('IBP', 'acronym', ['IVP'], 'Integrated Business Planning')], 'ivp hoy', 'en')
    assert '- IBP = Integrated Business Planning (also heard as: IVP)' in s
    assert 'exact spellings' in s


def test_el_bloque_en_castellano():
    s = gl.prompt_section([E('IBP', aliases=['IVP'])], '', 'es')
    assert 'a veces transcrito como: IVP' in s and 'Glosario' in s


def test_con_mas_entradas_que_el_tope_van_primero_las_que_salen():
    ents = [E(f'Relleno{i}') for i in range(gl._MAX_PROMPT_ENTRIES + 20)] + [E('Importante')]
    s = gl.prompt_section(ents, 'hablamos de importante', 'en')
    assert '- Importante' in s
    assert s.count('\n- ') == gl._MAX_PROMPT_ENTRIES


def test_sin_entradas_no_hay_bloque():
    assert gl.prompt_section([], 'x', 'en') == ''


# ── contexto de una grabación ───────────────────────────────────────────────

class _Cita:
    def __init__(self, asunto, inicio, nombres):
        self.Subject, self.Start = asunto, inicio
        self.Recipients = [type('R', (), {'Name': n, 'Address': n})() for n in nombres]


def test_el_contexto_trae_los_invitados_del_calendario(fake_outlook, tr_dirs, monkeypatch):
    monkeypatch.setattr(gl, 'WHISPER_HOTWORDS', True)
    fake_outlook.appointments = [_Cita('IBP Review', datetime(2026, 10, 20, 14, 0),
                                       ['Nurettin Ekizoglu', 'Francesca Maestripieri'])]
    gl.save({'entries': [E('Kinaxis')]})

    ctx = gl.recording_context(tr_dirs / 'recordings' / '2026-10-20_14-00_IBP_Review.wav')

    assert 'Nurettin Ekizoglu' in ctx['hotwords'] and 'Kinaxis' in ctx['hotwords']
    assert [e['term'] for e in ctx['entries']] == ['Kinaxis']


def test_el_contexto_se_cachea_por_grabacion(fake_outlook, tr_dirs, monkeypatch):
    monkeypatch.setattr(gl, 'WHISPER_HOTWORDS', True)
    llamadas = []
    fake_outlook.appointments = []
    import outlook_sender
    original = outlook_sender.find_meeting_participants

    def contado(*a, **k):
        llamadas.append(1)
        return original(*a, **k)
    outlook_sender.find_meeting_participants = contado
    try:
        w = tr_dirs / 'recordings' / '2026-10-20_14-00_X.wav'
        gl.recording_context(w)
        gl.recording_context(w)
    finally:
        outlook_sender.find_meeting_participants = original
    assert len(llamadas) == 1


def test_si_outlook_falla_el_contexto_sigue_con_el_glosario(monkeypatch, tr_dirs):
    monkeypatch.setattr(gl, 'WHISPER_HOTWORDS', True)
    import outlook_sender
    monkeypatch.setattr(outlook_sender, '_get_outlook', lambda: (_ for _ in ()).throw(OSError('x')))
    gl.save({'entries': [E('Kinaxis')]})

    ctx = gl.recording_context(tr_dirs / 'recordings' / '2026-10-20_14-00_X.wav')

    assert ctx['hotwords'] == 'Kinaxis'


def test_por_defecto_el_contexto_no_lleva_pistas_para_whisper(fake_outlook, tr_dirs):
    """18/11/2026: con las pistas del glosario, Whisper escribio 5 lineas donde sin
    ellas salian 32 (mismo tramo, 125 s con voz). Por defecto no se le pasan: el
    glosario sigue sirviendo para corregir el transcript y para las minutas."""
    fake_outlook.appointments = [_Cita('IBP Review', datetime(2026, 10, 20, 14, 0),
                                       ['Nurettin Ekizoglu'])]
    gl.save({'entries': [E('Kinaxis')]})

    ctx = gl.recording_context(tr_dirs / 'recordings' / '2026-10-20_14-00_IBP_Review.wav')

    assert gl.WHISPER_HOTWORDS is False
    assert ctx['hotwords'] == ''
    assert [e['term'] for e in ctx['entries']] == ['Kinaxis'], "las correcciones y las minutas las siguen usando"


def test_sin_pistas_no_se_consulta_outlook_para_los_invitados(monkeypatch, tr_dirs):
    """Los invitados solo servian para las pistas: sin ellas, nada de COM."""
    import outlook_sender
    llamadas = []
    monkeypatch.setattr(outlook_sender, 'find_meeting_participants',
                        lambda *a, **k: llamadas.append(1) or [])

    ctx = gl.recording_context(tr_dirs / 'recordings' / '2026-10-20_14-00_IBP_Review.wav')

    assert ctx['hotwords'] == ''
    assert llamadas == [], "se consulto Outlook sin necesidad"


def test_las_correcciones_del_glosario_siguen_aplicandose_sin_pistas(tr_dirs):
    """Quitar las pistas no debe quitar la correccion posterior de errores conocidos."""
    gl.save({'entries': [gl._clean_entry({'term': 'Kinaxis', 'aliases': ['Kinexis']})]})
    ctx = gl.recording_context(tr_dirs / 'recordings' / '2026-10-20_14-00_X.wav')

    assert ctx['hotwords'] == ''
    assert tr._correct_with_glossary('usamos Kinexis hoy', ctx) == 'usamos Kinaxis hoy'


def test_un_nombre_de_fichero_raro_no_lanza(tr_dirs):
    ctx = gl.recording_context(tr_dirs / 'recordings' / 'sin_formato.wav')
    assert ctx == {'hotwords': '', 'entries': [], 'project_id': None}


def test_las_entradas_de_proyecto_se_incluyen_si_se_detecta_el_proyecto(monkeypatch, tr_dirs):
    import project_context
    monkeypatch.setattr(project_context, 'detect_project',
                        lambda text, name='', projects=None: {'id': 'sandoz'})
    gl.save({'entries': [E('Global'), E('DeSandoz', project='sandoz'), E('DeOtro', project='otro')]})

    ctx = gl.recording_context(tr_dirs / 'recordings' / '2026-10-20_14-00_Sandoz_Sync.wav')

    assert ctx['project_id'] == 'sandoz'
    assert [e['term'] for e in ctx['entries']] == ['Global', 'DeSandoz']


# ── integración con el transcriptor ─────────────────────────────────────────

def test_targs_incluye_las_pistas_solo_si_las_hay():
    assert 'hotwords' not in tr._targs(None)
    assert 'hotwords' not in tr._targs('')
    assert tr._targs('Kinaxis')['hotwords'] == 'Kinaxis'


def test_targs_no_modifica_los_argumentos_globales():
    tr._targs('Kinaxis', language='es')
    assert 'hotwords' not in tr._TRANSCRIBE_ARGS


def test_las_pistas_llegan_a_whisper_en_todos_los_modos(tr_dirs, wav_factory, fake_whisper_model):
    wav = wav_factory(tr_dirs / 'recordings' / 'a.wav', seconds=2, subtype='FLOAT')
    modelo = fake_whisper_model()
    visto = []
    original = modelo.transcribe
    modelo.transcribe = lambda audio, **kw: (visto.append(kw.get('hotwords')), original(audio, **kw))[1]

    tr._transcribe_whole(modelo, wav, hotwords='Kinaxis')
    tr._transcribe_chunks(modelo, wav, hotwords='Kinaxis')

    assert visto == ['Kinaxis', 'Kinaxis']


def test_transcribe_pasa_las_pistas_y_corrige_el_texto(monkeypatch, tr_dirs):
    capturado = {}

    def falso(audio_path, **kw):
        capturado.update(kw)
        return '[00:01] hablamos de Kinexis', 'es'

    monkeypatch.setattr(tr, '_transcribe_local', falso)
    monkeypatch.setattr(tr, '_glossary_context', lambda p: {
        'hotwords': 'Kinaxis', 'project_id': None,
        'entries': [E('Kinaxis', aliases=['Kinexis'])]})

    text, lang = tr.transcribe(tr_dirs / 'recordings' / 'a.wav')

    assert capturado['hotwords'] == 'Kinaxis'
    assert text == '[00:01] hablamos de Kinaxis'


def test_si_el_glosario_revienta_se_transcribe_igual(monkeypatch, tr_dirs):
    monkeypatch.setattr(tr, '_transcribe_local', lambda p, **kw: ('[00:01] hola', 'es'))
    import glossary
    monkeypatch.setattr(glossary, 'recording_context',
                        lambda p: (_ for _ in ()).throw(RuntimeError('boom')))

    assert tr.transcribe(tr_dirs / 'recordings' / 'a.wav') == ('[00:01] hola', 'es')


# La transcripción durante la reunión (pistas en <stem>.live.hints, el cierre y
# las correcciones del transcript en vivo) se prueba en test_live_transcriber.py,
# sección "Glosario", con sus fixtures.


# ── integración con las minutas ─────────────────────────────────────────────

def test_el_prompt_de_minutas_lleva_el_glosario(tr_dirs):
    gl.save({'entries': [E('IBP', 'acronym', ['IVP'], 'Integrated Business Planning')]})

    p = mg._build_prompt('[00:01] [Otros]: lo del IVP', tr_dirs / 'x.wav', language='es')

    assert 'Glosario' in p
    assert '- IBP = Integrated Business Planning (a veces transcrito como: IVP)' in p


def test_sin_glosario_el_prompt_no_cambia(tr_dirs):
    p = mg._build_prompt('[00:01] [Otros]: hola', tr_dirs / 'x.wav', language='es')
    assert 'Glosario' not in p and 'Glossary' not in p


def test_el_glosario_roto_no_rompe_el_prompt(tr_dirs):
    gl.GLOSSARY_FILE.write_text('{roto', encoding='utf-8')
    p = mg._build_prompt('[00:01] hola', tr_dirs / 'x.wav', language='es')
    assert 'Transcripción' in p


# ── edición desde Settings ──────────────────────────────────────────────────

def test_crear_una_entrada():
    gl.upsert_entry({'term': 'Kinaxis', 'aliases': ['Kinexis']})
    assert [e['term'] for e in gl.load()['entries']] == ['Kinaxis']


def test_crear_sin_termino_falla():
    with pytest.raises(ValueError):
        gl.upsert_entry({'term': '  ', 'aliases': ['x']})


def test_editar_cambiando_el_termino_no_deja_la_vieja():
    gl.upsert_entry({'term': 'Kinexis', 'aliases': []})
    gl.upsert_entry({'term': 'Kinaxis', 'aliases': ['Kinexis']},
                    original={'term': 'Kinexis', 'project': None})
    assert [(e['term'], e['aliases']) for e in gl.load()['entries']] == [('Kinaxis', ['Kinexis'])]


def test_editar_conserva_la_posicion():
    for t in ('A', 'B', 'C'):
        gl.upsert_entry({'term': t})
    gl.upsert_entry({'term': 'B2'}, original={'term': 'B', 'project': None})
    assert [e['term'] for e in gl.load()['entries']] == ['A', 'B2', 'C']


def test_el_mismo_termino_y_ambito_se_fusiona_en_vez_de_duplicarse():
    gl.upsert_entry({'term': 'IBP', 'aliases': ['IVP'], 'meaning': 'Integrated Business Planning'})
    gl.upsert_entry({'term': 'ibp', 'aliases': ['IBB']})
    got = gl.load()['entries']
    assert len(got) == 1
    assert got[0]['aliases'] == ['IBB', 'IVP']
    assert got[0]['meaning'] == 'Integrated Business Planning'


def test_el_mismo_termino_en_otro_ambito_es_otra_entrada():
    gl.upsert_entry({'term': 'IBP', 'project': None})
    gl.upsert_entry({'term': 'IBP', 'project': 'sandoz'})
    assert len(gl.load()['entries']) == 2


def test_cambiar_el_ambito_de_una_entrada():
    gl.upsert_entry({'term': 'IBP'})
    gl.upsert_entry({'term': 'IBP', 'project': 'sandoz'}, original={'term': 'IBP', 'project': None})
    got = gl.load()['entries']
    assert [(e['term'], e['project']) for e in got] == [('IBP', 'sandoz')]


def test_borrar_una_entrada():
    gl.upsert_entry({'term': 'A'})
    gl.upsert_entry({'term': 'B'})
    assert gl.delete_entry('a') is True
    assert [e['term'] for e in gl.load()['entries']] == ['B']


def test_borrar_lo_que_no_existe_devuelve_false():
    assert gl.delete_entry('nada') is False


def test_borrar_respeta_el_ambito():
    gl.upsert_entry({'term': 'IBP'})
    gl.upsert_entry({'term': 'IBP', 'project': 'sandoz'})
    gl.delete_entry('IBP', 'sandoz')
    assert [e['project'] for e in gl.load()['entries']] == [None]


# ── sugerencias ─────────────────────────────────────────────────────────────

TRANSCRIPT = '[00:01] [Otros]: Norethin dijo que Kinexis falla. Hablamos con Norethin Perez.'


def S(term='Nurettin', aliases=('Norethin',), category='person', meaning=''):
    return {'term': term, 'aliases': list(aliases), 'category': category, 'meaning': meaning}


def test_una_sugerencia_valida_pasa():
    assert gl.validate_suggestions([S()], TRANSCRIPT) == [S()]


def test_el_error_debe_aparecer_en_el_transcript():
    """Claude puede alucinar: si el 'error' no está en el texto, se descarta."""
    assert gl.validate_suggestions([S(aliases=['Inventado'])], TRANSCRIPT) == []


def test_el_error_debe_aparecer_como_palabra_completa():
    assert gl.validate_suggestions([S(aliases=['Noreth'])], TRANSCRIPT) == []


def test_se_quedan_solo_los_errores_que_aparecen():
    out = gl.validate_suggestions([S(aliases=['Norethin', 'Inventado'])], TRANSCRIPT)
    assert out[0]['aliases'] == ['Norethin']


def test_un_error_ya_corregido_no_se_vuelve_a_sugerir():
    gl.upsert_entry({'term': 'Kinaxis', 'aliases': ['Kinexis']})
    assert gl.validate_suggestions([S('Kinaxis', ['Kinexis'], 'term')], TRANSCRIPT) == []


def test_un_error_descartado_no_se_vuelve_a_sugerir():
    gl.add_suggestions([S()], 'm1')
    gl.dismiss_suggestion('Nurettin')
    assert gl.validate_suggestions([S()], TRANSCRIPT) == []


def test_descartar_un_error_no_bloquea_otros_del_mismo_termino():
    gl.add_suggestions([S()], 'm1')
    gl.dismiss_suggestion('Nurettin')
    assert gl.validate_suggestions([S(aliases=['Norethin Perez'])], TRANSCRIPT)


@pytest.mark.parametrize('raw', [None, 'texto', {'term': 'x'}, [1, None, 'a', {}], [{'term': 'x'}],
                                 [{'term': 'x', 'aliases': []}],
                                 [{'term': '', 'aliases': ['Norethin']}]])
def test_formas_raras_se_ignoran(raw):
    assert gl.validate_suggestions(raw, TRANSCRIPT) == []


def test_una_categoria_desconocida_pasa_a_term():
    assert gl.validate_suggestions([S(category='raro')], TRANSCRIPT)[0]['category'] == 'term'


def test_alias_igual_al_termino_se_descarta():
    assert gl.validate_suggestions([S('Norethin', ['norethin'])], TRANSCRIPT) == []


def test_campos_enormes_se_descartan():
    assert gl.validate_suggestions([S('x' * 200, ['Norethin'])], TRANSCRIPT) == []


def test_hay_un_maximo_por_reunion():
    texto = ' '.join(f'Palabra{i}' for i in range(30))
    raw = [S(f'Termino{i}', [f'Palabra{i}']) for i in range(30)]
    assert len(gl.validate_suggestions(raw, texto)) == gl._MAX_SUGGESTIONS_PER_MEETING


def test_guardar_sugerencias_las_deja_pendientes_con_su_origen():
    assert gl.add_suggestions([S()], 'minuta_1') == 1
    got = gl.load()['suggestions']
    assert got[0]['term'] == 'Nurettin' and got[0]['source'] == 'minuta_1'
    assert len(gl.load()['suggestions']) == 1
    assert gl.load()['entries'] == []             # NUNCA entran solas


def test_la_misma_sugerencia_de_otra_reunion_fusiona_los_errores():
    gl.add_suggestions([S(aliases=['Norethin'])], 'm1')
    assert gl.add_suggestions([S(aliases=['Noretin'])], 'm2') == 0
    got = gl.load()['suggestions']
    assert len(got) == 1 and got[0]['aliases'] == ['Norethin', 'Noretin']
    assert got[0]['source'] == 'm1'


def test_sin_sugerencias_no_se_escribe_nada():
    assert gl.add_suggestions([], 'm1') == 0
    assert not gl.GLOSSARY_FILE.exists()


def test_aprobar_crea_la_entrada_y_quita_la_sugerencia():
    gl.add_suggestions([S(meaning='Head of IDP')], 'm1')
    entry = gl.approve_suggestion('nurettin')
    assert entry['term'] == 'Nurettin' and entry['aliases'] == ['Norethin']
    data = gl.load()
    assert data['suggestions'] == []
    assert [e['term'] for e in data['entries']] == ['Nurettin']
    assert 'source' not in data['entries'][0]


def test_aprobar_con_correcciones_del_usuario():
    gl.add_suggestions([S()], 'm1')
    gl.approve_suggestion('Nurettin', {'term': 'Nurettin Ekizoglu', 'project': 'sandoz'})
    e = gl.load()['entries'][0]
    assert (e['term'], e['project'], e['aliases']) == ('Nurettin Ekizoglu', 'sandoz', ['Norethin'])


def test_aprobar_fusiona_con_una_entrada_existente():
    gl.upsert_entry({'term': 'Nurettin', 'aliases': ['Nuretin'], 'category': 'person'})
    gl.add_suggestions([S()], 'm1')
    gl.approve_suggestion('Nurettin')
    got = gl.load()['entries']
    assert len(got) == 1 and got[0]['aliases'] == ['Norethin', 'Nuretin']


def test_aprobar_algo_que_no_existe_devuelve_none():
    assert gl.approve_suggestion('nada') is None


def test_descartar_quita_la_sugerencia_y_recuerda_cada_par():
    gl.add_suggestions([S(aliases=['Norethin', 'Noretin'])], 'm1')
    assert gl.dismiss_suggestion('Nurettin') is True
    data = gl.load()
    assert data['suggestions'] == []
    assert data['dismissed'] == ['nurettin>norethin', 'nurettin>noretin']


def test_descartar_algo_que_no_existe_devuelve_false():
    assert gl.dismiss_suggestion('nada') is False


def test_una_sugerencia_sin_errores_en_el_fichero_se_ignora():
    gl.GLOSSARY_FILE.write_text(json.dumps({'suggestions': [{'term': 'X', 'aliases': []}]}),
                                encoding='utf-8')
    assert gl.load()['suggestions'] == []


# ── generación con Claude ───────────────────────────────────────────────────

def test_parse_json_con_vallas_y_texto_alrededor():
    assert gl._parse_json_list('Claro:\n```json\n[{"term": "A"}]\n```\nlisto') == [{'term': 'A'}]


@pytest.mark.parametrize('texto', ['', 'no hay json', '{"a": 1}', '[roto', None])
def test_parse_json_invalido_devuelve_none(texto):
    assert gl._parse_json_list(texto) is None


def test_sugerir_guarda_lo_que_valida(monkeypatch):
    visto = {}

    def falso(prompt, timeout=0):
        visto['prompt'] = prompt
        return ('[{"term": "Nurettin Ekizoglu", "aliases": ["Norethin", "Inventado"], '
                '"category": "person"}]')

    monkeypatch.setattr(mg, 'run_claude', falso)
    gl.upsert_entry({'term': 'Kinaxis', 'aliases': ['Kinexis']})

    n = gl.suggest_from_meeting(TRANSCRIPT, '## Minuta', ['Nurettin Ekizoglu'], 'm1')

    assert n == 1
    got = gl.load()['suggestions'][0]
    assert got['aliases'] == ['Norethin']              # 'Inventado' no está en el transcript
    assert 'Nurettin Ekizoglu' in visto['prompt']      # invitados
    assert 'Kinaxis' in visto['prompt']                # lo ya conocido, para no repetirlo
    assert 'Norethin dijo' in visto['prompt']          # transcript


@pytest.mark.parametrize('respuesta', [None, '', 'sin json', '[]', '{"x": 1}'])
def test_si_claude_no_aporta_no_hay_sugerencias(monkeypatch, respuesta):
    monkeypatch.setattr(mg, 'run_claude', lambda prompt, timeout=0: respuesta)
    assert gl.suggest_from_meeting(TRANSCRIPT, '', [], 'm1') == 0
    assert gl.load()['suggestions'] == []


# ── API que usa la interfaz (app_window.AppAPI) ─────────────────────────────

@pytest.fixture
def api():
    import app_window as aw
    return object.__new__(aw.AppAPI)


def test_get_glossary_devuelve_entradas_sugerencias_y_proyectos(api, tr_dirs):
    gl.upsert_entry({'term': 'IBP', 'aliases': ['IVP']})
    gl.add_suggestions([S()], 'm1')
    (tr_dirs / 'projects.json').write_text(
        json.dumps({'projects': [{'id': 'sandoz', 'name': 'Sandoz'}, {'name': 'sin id'}]}),
        encoding='utf-8')

    got = api.get_glossary()

    assert [e['term'] for e in got['entries']] == ['IBP']
    assert [s['term'] for s in got['suggestions']] == ['Nurettin']
    assert got['pending'] == 1
    assert got['projects'] == [{'id': 'sandoz', 'name': 'Sandoz'}]


def test_get_glossary_sin_nada_no_lanza(api):
    got = api.get_glossary()
    assert got['entries'] == [] and got['suggestions'] == [] and got['pending'] == 0


def test_guardar_una_entrada_devuelve_el_estado_nuevo(api):
    got = api.save_glossary_entry({'term': 'Kinaxis', 'aliases': ['Kinexis'], 'category': 'term'})
    assert [e['term'] for e in got['entries']] == ['Kinaxis']


def test_guardar_sin_termino_devuelve_error_y_no_escribe(api):
    assert api.save_glossary_entry({'term': ' '}) == {'error': 'term'}
    assert gl.load()['entries'] == []


def test_editar_una_entrada_desde_la_interfaz(api):
    api.save_glossary_entry({'term': 'Kinexis'})
    got = api.save_glossary_entry({'term': 'Kinaxis'}, {'term': 'Kinexis', 'project': None})
    assert [e['term'] for e in got['entries']] == ['Kinaxis']


def test_borrar_una_entrada_desde_la_interfaz(api):
    api.save_glossary_entry({'term': 'A'})
    api.save_glossary_entry({'term': 'B'})
    got = api.delete_glossary_entry('A')
    assert [e['term'] for e in got['entries']] == ['B']


def test_aprobar_una_sugerencia_desde_la_interfaz(api):
    gl.add_suggestions([S()], 'm1')
    got = api.resolve_glossary_suggestion('Nurettin', 'approve', {'project': 'sandoz'})
    assert got['pending'] == 0
    assert [(e['term'], e['project']) for e in got['entries']] == [('Nurettin', 'sandoz')]


def test_editar_una_sugerencia_y_aprobarla_desde_la_interfaz(api):
    """El formulario de edicion cambia termino, errores y significado, y al
    guardar se aprueba la sugerencia ya corregida (sin dejar la original)."""
    gl.add_suggestions([S()], 'm1')
    got = api.resolve_glossary_suggestion('Nurettin', 'approve', {
        'term': 'Nurettin K.', 'category': 'person', 'aliases': ['Norethin', 'Nuretin'],
        'meaning': 'Sponsor', 'project': None})
    assert got['pending'] == 0
    [e] = got['entries']
    assert (e['term'], e['category'], e['aliases'], e['meaning']) == (
        'Nurettin K.', 'person', ['Norethin', 'Nuretin'], 'Sponsor')
    assert gl.load()['suggestions'] == []


def test_descartar_una_sugerencia_desde_la_interfaz(api):
    gl.add_suggestions([S()], 'm1')
    got = api.resolve_glossary_suggestion('Nurettin', 'dismiss')
    assert got['pending'] == 0 and got['entries'] == []
    assert gl.load()['dismissed'] == ['nurettin>norethin']


def test_una_accion_desconocida_devuelve_error(api):
    gl.add_suggestions([S()], 'm1')
    assert api.resolve_glossary_suggestion('Nurettin', 'borrar-todo') == {'error': 'action'}
    assert len(gl.load()['suggestions']) == 1


def test_resolver_una_sugerencia_que_ya_no_existe_devuelve_el_estado(api):
    """La interfaz puede tener la lista desactualizada (otra ventana ya la resolvió)."""
    got = api.resolve_glossary_suggestion('fantasma', 'approve')
    assert got['pending'] == 0 and 'error' not in got


# ── enganche con el pipeline ────────────────────────────────────────────────

def test_el_pipeline_pide_sugerencias_con_los_nombres_de_los_invitados(tray_factory, monkeypatch):
    visto = {}
    monkeypatch.setattr(gl, 'suggest_from_meeting',
                        lambda t, m, inv, src: visto.update(t=t, m=m, inv=inv, src=src) or 2)

    tray_factory()._suggest_glossary_terms(
        'transcript', 'minuta', [{'name': 'Ana'}, {'email': 'x@y.com'}, {'name': 'Luis'}], 'm1')

    assert visto == {'t': 'transcript', 'm': 'minuta', 'inv': ['Ana', 'Luis'], 'src': 'm1'}


def test_si_las_sugerencias_fallan_el_pipeline_no_se_entera(tray_factory, monkeypatch):
    def boom(*a):
        raise RuntimeError('claude caido')
    monkeypatch.setattr(gl, 'suggest_from_meeting', boom)

    tray_factory()._suggest_glossary_terms('t', 'm', [], 'm1')      # no debe lanzar

def test_transcribe_corrige_con_las_entradas_de_ahora(monkeypatch, tr_dirs):
    """recording_context se cachea al empezar a grabar: una sugerencia aprobada
    durante la reunion no estaria en el contexto, y tiene que corregir igual."""
    monkeypatch.setattr(tr, '_transcribe_local', lambda p, **kw: ('[00:01] hablamos de Kinexis', 'es'))
    monkeypatch.setattr(tr, '_glossary_context',
                        lambda p: {'hotwords': '', 'project_id': None, 'entries': []})
    gl.upsert_entry({'term': 'Kinaxis', 'aliases': ['Kinexis']})

    assert tr.transcribe(tr_dirs / 'recordings' / 'a.wav')[0] == '[00:01] hablamos de Kinaxis'
