"""Convencion de nombres y rutas: el invariante central del repo.

De estas funciones dependen TODOS los nombres de fichero del sistema, y
tambien un consumidor externo (ver test_consumer_contract.py). Son puras y
hasta hoy no tenian ni un test.

Las dos convenciones que conviven, y el puente entre ellas:

    GRABACION : recordings/YYYY-MM-DD_HH-MM_{slug}.wav      <- con guiones
    TRANSCRIPT: <junto al wav>/{stem}_transcript.txt
    MINUTA    : minutes/YYYYMMDD_HHMM_{slug}.md             <- compacto

`_recording_timestamp` es exactamente el puente (guiones -> compacto).
"""
import os
import time

import pytest

import storage

pytestmark = pytest.mark.unit


# ── _slugify ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('entrada, esperado', [
    ('Reunion Simple', 'Reunion_Simple'),
    ('Con  espacios   multiples', 'Con_espacios_multiples'),
    ('Con-guiones-medios', 'Con_guiones_medios'),
    ('Mezcla - de  todo', 'Mezcla_de_todo'),
    ('Reunión con acentos', 'Reunión_con_acentos'),      # NO normaliza acentos
])
def test_slugify_casos_normales(entrada, esperado):
    assert storage._slugify(entrada) == esperado


@pytest.mark.parametrize('malo', ['<', '>', ':', '"', '/', '\\', '|', '?', '*'])
def test_slugify_elimina_los_caracteres_prohibidos_en_windows(malo):
    assert malo not in storage._slugify(f'Reunion{malo}Importante')


def test_slugify_con_titulo_de_solo_caracteres_invalidos_queda_vacio():
    """CASO LIMITE NO CONTEMPLADO: no hay fallback a 'recording', asi que
    get_recording_path('***') produce un fichero '{ts}_.wav' con el nombre
    colgando. Se fija el comportamiento actual; cambiarlo es una decision."""
    assert storage._slugify('***') == ''
    assert storage._slugify('') == ''


def test_slugify_trunca_a_60_caracteres():
    assert len(storage._slugify('A' * 200)) == 60


def test_dos_titulos_largos_con_el_mismo_prefijo_colisionan():
    """Documenta una consecuencia del truncado a 60: si dos reuniones solo se
    diferencian DESPUES del caracter 60, producen el mismo slug. Hoy las salva
    el timestamp del nombre de fichero, no el slug."""
    prefijo = 'Comite de Seguimiento Semanal del Proyecto Contoso Agentic AI'
    assert len(prefijo) >= 60, "el prefijo debe llegar al corte"

    a = storage._slugify(f'{prefijo} Fase Uno')
    b = storage._slugify(f'{prefijo} Fase Dos')

    assert a == b, "si esto cambia, el truncado ya no es a 60"
    assert len(a) == 60


# ── _recording_timestamp: el puente entre las dos convenciones ────────────

@pytest.mark.parametrize('stem, esperado', [
    ('2026-09-17_16-16_manual', '20260917_1616'),
    ('2026-01-01_00-00_x', '20260101_0000'),
    ('2026-12-31_23-59', '20261231_2359'),
])
def test_recording_timestamp_convierte_guiones_a_compacto(stem, esperado, tmp_path):
    assert storage._recording_timestamp(tmp_path / f'{stem}.wav') == esperado


@pytest.mark.parametrize('stem', ['sin_fecha', '20260917_1616_ya_compacto', '', 'x'])
def test_recording_timestamp_devuelve_vacio_si_el_stem_no_cumple(stem, tmp_path):
    assert storage._recording_timestamp(tmp_path / f'{stem}.wav') == ''


# ── Rutas derivadas ───────────────────────────────────────────────────────

def test_get_recording_path_usa_el_formato_con_guiones(tr_dirs):
    import re
    p = storage.get_recording_path('Mi Reunion')
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_Mi_Reunion', p.stem), p.stem
    assert p.suffix == '.wav'
    assert p.parent == storage.RECORDINGS_DIR


def test_get_transcript_path_cuelga_del_wav(tr_dirs):
    wav = storage.RECORDINGS_DIR / '2026-09-17_16-16_manual.wav'
    assert storage.get_transcript_path(wav).name == '2026-09-17_16-16_manual_transcript.txt'
    assert storage.get_transcript_path(wav).parent == wav.parent


def test_get_transcript_path_aplicado_dos_veces_duplica_el_sufijo(tr_dirs):
    """Contrato defensivo: la funcion NO es idempotente. Si alguien la aplica
    al resultado, sale '..._transcript_transcript.txt'."""
    wav = storage.RECORDINGS_DIR / 'x.wav'
    doble = storage.get_transcript_path(storage.get_transcript_path(wav))
    assert doble.name.count('_transcript') == 2


def test_get_minutes_path_produce_el_stem_del_contrato(tr_dirs):
    """YYYYMMDD_HHMM_Titulo_Con_Underscores: de este formato depende el
    consumidor externo scan_meetings.py del proyecto Contoso."""
    wav = storage.RECORDINGS_DIR / '2026-09-17_16-16_manual.wav'
    md = storage.get_minutes_path(wav, 'Revision Deck Sales')
    assert md.name == '20260917_1616_Revision_Deck_Sales.md'
    assert md.parent == storage.MINUTES_DIR


def test_get_minutes_path_sin_titulo_usa_reunion(tr_dirs):
    wav = storage.RECORDINGS_DIR / '2026-09-17_16-16_manual.wav'
    assert storage.get_minutes_path(wav).name == '20260917_1616_Reunion.md'


def test_get_minutes_path_con_stem_no_conforme_cae_a_la_hora_actual(tr_dirs):
    import re
    md = storage.get_minutes_path(storage.RECORDINGS_DIR / 'sin_fecha.wav', 'T')
    assert re.fullmatch(r'\d{8}_\d{4}_T', md.stem), md.stem


def test_el_stem_de_minutas_se_deriva_del_wav_y_no_del_reloj(tr_dirs):
    """Invariante que sostiene _recover_pending: al reprocesar un WAV viejo
    tras un reinicio, las minutas deben llevar la fecha de la REUNION, no la
    del reproceso."""
    wav = storage.RECORDINGS_DIR / '2026-01-15_09-30_Reunion_Vieja.wav'
    assert storage.get_minutes_path(wav, 'X').name.startswith('20260115_0930_')


# ── Directorios ───────────────────────────────────────────────────────────

def test_ensure_directories_crea_tambien_processed(tr_dirs):
    storage.ensure_directories()
    for d in (storage.RECORDINGS_DIR, storage.MINUTES_DIR, storage.INBOX_DIR,
              storage.RECORDINGS_DIR / 'processed'):
        assert d.is_dir(), d


def test_ensure_directories_es_idempotente(tr_dirs):
    storage.ensure_directories()
    storage.ensure_directories()            # no debe lanzar


# ── Listados ──────────────────────────────────────────────────────────────

def test_list_recordings_ordena_por_fecha_descendente(tr_dirs, wav_factory):
    viejo = wav_factory(storage.RECORDINGS_DIR / 'viejo.wav', seconds=0.1)
    nuevo = wav_factory(storage.RECORDINGS_DIR / 'nuevo.wav', seconds=0.1)
    os.utime(viejo, (time.time() - 10_000,) * 2)

    assert [p.name for p in storage.list_recordings()] == [nuevo.name, viejo.name]


def test_list_recordings_incluye_los_ya_procesados(tr_dirs, wav_factory):
    wav_factory(storage.RECORDINGS_DIR / 'pendiente.wav', seconds=0.1)
    wav_factory(storage.RECORDINGS_DIR / 'processed' / 'hecho.wav', seconds=0.1)
    assert {p.name for p in storage.list_recordings()} == {'pendiente.wav', 'hecho.wav'}


def test_list_recordings_no_deduplica_por_nombre(tr_dirs, wav_factory):
    """Un mismo nombre en recordings/ y en processed/ aparece DOS veces. Se
    fija el comportamiento actual."""
    wav_factory(storage.RECORDINGS_DIR / 'igual.wav', seconds=0.1)
    wav_factory(storage.RECORDINGS_DIR / 'processed' / 'igual.wav', seconds=0.1)
    assert [p.name for p in storage.list_recordings()].count('igual.wav') == 2


def test_list_recordings_respeta_el_limite(tr_dirs, wav_factory):
    for i in range(5):
        wav_factory(storage.RECORDINGS_DIR / f'r{i}.wav', seconds=0.1)
    assert len(storage.list_recordings(limit=3)) == 3


def test_get_latest_minutes_devuelve_la_mas_reciente(tr_dirs):
    vieja = storage.MINUTES_DIR / 'a.md'
    nueva = storage.MINUTES_DIR / 'b.md'
    vieja.write_text('x', encoding='utf-8')
    nueva.write_text('y', encoding='utf-8')
    os.utime(vieja, (time.time() - 10_000,) * 2)

    assert storage.get_latest_minutes() == nueva


def test_get_latest_minutes_sin_minutas_devuelve_none(tr_dirs):
    assert storage.get_latest_minutes() is None


# ── cleanup_old_recordings: destructiva, hoy sin red ─────────────────────

@pytest.fixture
def grabaciones_viejas(tr_dirs, wav_factory):
    """Un juego de ficheros con 20 dias, mas minutas que NO deben tocarse."""
    viejos = []
    for nombre in ('vieja.wav', 'vieja.lang', 'vieja.partial', 'vieja.context'):
        p = storage.RECORDINGS_DIR / nombre
        if p.suffix == '.wav':
            wav_factory(p, seconds=0.1)
        else:
            p.write_text('x', encoding='utf-8')
        os.utime(p, (time.time() - 20 * 86400,) * 2)
        viejos.append(p)

    reciente = wav_factory(storage.RECORDINGS_DIR / 'reciente.wav', seconds=0.1)
    minuta = storage.MINUTES_DIR / '20260101_0900_Vieja.md'
    minuta.write_text('minuta', encoding='utf-8')
    os.utime(minuta, (time.time() - 20 * 86400,) * 2)
    return {'viejos': viejos, 'reciente': reciente, 'minuta': minuta}


def test_cleanup_borra_lo_pendiente_pasado_el_tope(grabaciones_viejas):
    """Sin minutas se guarda PENDING_RETENTION_FACTOR veces la retencion (aqui
    15 dias con days=5); los ficheros del fixture tienen 20."""
    storage.cleanup_old_recordings(days=5)
    assert not any(p.exists() for p in grabaciones_viejas['viejos'])


def test_cleanup_no_toca_lo_reciente(grabaciones_viejas):
    storage.cleanup_old_recordings(days=15)
    assert grabaciones_viejas['reciente'].exists()


def test_cleanup_nunca_toca_las_minutas(grabaciones_viejas):
    """Las minutas son el resultado del trabajo: el audio es material de
    partida y se puede tirar, la minuta no."""
    storage.cleanup_old_recordings(days=15)
    assert grabaciones_viejas['minuta'].exists()


def test_cleanup_tambien_limpia_processed(tr_dirs, wav_factory):
    p = wav_factory(storage.RECORDINGS_DIR / 'processed' / 'vieja.wav', seconds=0.1)
    os.utime(p, (time.time() - 20 * 86400,) * 2)
    storage.cleanup_old_recordings(days=15)
    assert not p.exists()


def test_cleanup_no_borra_un_wav_sin_minutas(grabaciones_viejas):
    """Era un DEFECTO CONOCIDO que este test fijaba: no se comprobaba si la
    reunion llego a generar minutas, y un WAV de hace 16 dias que nunca se
    proceso se borraba sin dejar rastro. Ahora se queda hasta que se procese
    (detalle en test_retention.py)."""
    huerfano = grabaciones_viejas['viejos'][0]
    assert not huerfano.with_suffix('.md').exists()
    storage.cleanup_old_recordings(days=15)
    assert huerfano.exists()
