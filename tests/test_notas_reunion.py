"""Notas escritas durante la grabacion ("Añadir contexto a la grabacion").

LO QUE PROTEGEN
1. Cada "Guardar" añade una nota con su minuto: antes pisaba la anterior.
2. Al guardar hay aviso: antes la ventana se cerraba sin decir nada.
3. La nota entra en el transcript en su minuto, tambien con las minutas a mano:
   antes el pipeline la leia y la borraba sin usarla.
"""
import pytest

import meeting_notes as mn
import transcriber
import tray_app as ta

pytestmark = pytest.mark.unit

STEM = '2026-11-16_10-32_Comite_Semanal'


# ── Guardar y leer ───────────────────────────────────────────────────────────

def test_cada_nota_se_anade_con_su_minuto_sin_pisar_la_anterior(tmp_path):
    ctx = tmp_path / f'{STEM}.context'
    assert mn.add_note(ctx, 125.7, 'el cliente pide el piloto en enero') == '[02:05]'
    assert mn.add_note(ctx, 1863, '  presupuesto   cerrado ') == '[31:03]'
    assert mn.read_notes(ctx) == [(125, 'el cliente pide el piloto en enero'), (1863, 'presupuesto cerrado')]


def test_una_nota_vacia_no_se_guarda(tmp_path):
    ctx = tmp_path / f'{STEM}.context'
    assert mn.add_note(ctx, 10, '   ') is None
    assert not ctx.exists()


def test_un_contexto_de_antes_sin_minutos_es_una_nota_al_principio(tmp_path):
    ctx = tmp_path / f'{STEM}.context'
    ctx.write_text('objetivo: cerrar alcance\ncon Contoso', encoding='utf-8')
    assert mn.read_notes(ctx) == [(0, 'objetivo: cerrar alcance con Contoso')]
    assert mn.read_notes(tmp_path / 'no_existe.context') == []


# ── Entrar en el transcript ─────────────────────────────────────────────────

TRANSCRIPT = '\n'.join([
    '[00:05] [Felipe]: empezamos',
    '[02:00] [Otros]: el piloto seria en enero',
    '',
    '[--- reconexión: continuación de la reunión ---]',
    '[04:10] [Otros]: y el presupuesto?',
])


def test_cada_nota_va_detras_de_la_ultima_linea_de_su_minuto():
    out = mn.merge(TRANSCRIPT, [(0, 'objetivo'), (125, 'ojo con enero'), (900, 'al final')], 'Nota de Felipe')
    assert out.splitlines() == [
        '[00:00] [Nota de Felipe]: objetivo',
        '[00:05] [Felipe]: empezamos',
        '[02:00] [Otros]: el piloto seria en enero',
        '[02:05] [Nota de Felipe]: ojo con enero',
        '',
        '[--- reconexión: continuación de la reunión ---]',
        '[04:10] [Otros]: y el presupuesto?',
        '[15:00] [Nota de Felipe]: al final',
    ]


def test_meterlas_dos_veces_no_las_repite():
    una = mn.merge(TRANSCRIPT, [(125, 'ojo con enero')], 'Nota')
    assert mn.merge(una, [(125, 'ojo con enero')], 'Nota') == una
    assert mn.merge(TRANSCRIPT, [], 'Nota') == TRANSCRIPT


def test_sin_etiquetas_de_quien_habla_tambien_entran():
    out = mn.merge('[00:10] hola\n[01:00] adios', [(30, 'nota')], 'Nota')
    assert out == '[00:10] hola\n[00:30] [Nota]: nota\n[01:00] adios'


def test_saber_si_ya_estan_todas():
    assert mn.all_in('[02:05] [Nota]: ojo con enero', [(125, 'ojo con enero')])
    assert not mn.all_in('texto', [(125, 'ojo con enero')])


# ── La bandeja ───────────────────────────────────────────────────────────────

@pytest.fixture
def avisos(tray, monkeypatch):
    vistos = []
    monkeypatch.setattr(tray, '_notify', lambda titulo, msg: vistos.append((titulo, msg)))
    monkeypatch.setattr(ta, 'get_ui_language', lambda: 'es')
    return vistos


def test_al_guardar_se_avisa_con_el_minuto(tray, avisos, tr_dirs, monkeypatch):
    wav = ta.RECORDINGS_DIR / f'{STEM}.wav'
    monkeypatch.setattr(ta.time, 'time', lambda: 1000.0 + 754)
    assert tray._save_note(wav, 1000.0, 'el piloto en enero') == '[12:34]'
    assert tray._save_note(wav, 1000.0, 'y el presupuesto') == '[12:34]'
    assert avisos == [('Noted', 'Nota guardada en el minuto 12:34: entrara en el transcript.')] * 2
    assert [t for _s, t in mn.read_notes(wav.with_suffix('.context'))] == ['el piloto en enero', 'y el presupuesto']


def test_una_nota_vacia_ni_se_guarda_ni_avisa(tray, avisos, tr_dirs):
    assert tray._save_note(ta.RECORDINGS_DIR / f'{STEM}.wav', None, '  ') is None
    assert avisos == []


def test_si_no_se_puede_guardar_se_avisa_del_fallo(tray, avisos, tr_dirs):
    assert tray._save_note(tr_dirs / 'no' / 'existe' / f'{STEM}.wav', None, 'nota') is None
    assert avisos == [('Noted ⚠', 'No se pudo guardar la nota (el detalle esta en noted.log).')]


def test_al_procesar_las_notas_entran_en_el_transcript(tray, avisos, tr_dirs, monkeypatch):
    monkeypatch.setattr(transcriber, 'ME_NAME', 'Felipe')
    wav = ta.RECORDINGS_DIR / f'{STEM}.wav'
    transcript = ta.RECORDINGS_DIR / f'{STEM}_transcript.txt'
    transcript.write_text(TRANSCRIPT, encoding='utf-8')
    mn.add_note(wav.with_suffix('.context'), 125, 'ojo con enero')

    out = tray._merge_notes(wav, transcript, TRANSCRIPT)

    assert '[02:05] [Nota de Felipe]: ojo con enero' in out.splitlines()
    assert transcript.read_text(encoding='utf-8') == out
    assert tray._merge_notes(wav, transcript, out) == out, "dos pasadas, una sola vez"


def test_si_no_se_puede_escribir_el_transcript_sigue_el_de_antes(tray, avisos, tr_dirs):
    wav = ta.RECORDINGS_DIR / f'{STEM}.wav'
    mn.add_note(wav.with_suffix('.context'), 125, 'ojo')
    no_escribible = tr_dirs / 'no' / 'existe' / f'{STEM}_transcript.txt'
    assert tray._merge_notes(wav, no_escribible, TRANSCRIPT) == TRANSCRIPT


def test_el_dialogo_se_abre_con_el_inicio_de_la_grabacion(tray, tr_dirs, monkeypatch):
    abiertos = []
    monkeypatch.setattr(tray, '_show_context_dialog', lambda path, started: abiertos.append((path, started)))
    monkeypatch.setattr(ta.threading, 'Thread',
                        lambda target, **_k: type('H', (), {'start': lambda self: target()})())
    tray._recording_path, tray._recording_start = None, None
    tray._add_context()
    assert abiertos == [], "sin grabacion no hay a que añadir la nota"
    wav = ta.RECORDINGS_DIR / f'{STEM}.wav'
    tray._recording_path, tray._recording_start = wav, 1000.0
    tray._add_context()
    assert abiertos == [(wav, 1000.0)]


def test_sin_notas_el_transcript_no_se_toca(tray, avisos, tr_dirs):
    wav = ta.RECORDINGS_DIR / f'{STEM}.wav'
    transcript = ta.RECORDINGS_DIR / f'{STEM}_transcript.txt'
    assert tray._merge_notes(wav, transcript, TRANSCRIPT) == TRANSCRIPT
    assert not transcript.exists()


def test_las_notas_tambien_se_buscan_en_processed(tray, avisos, tr_dirs, monkeypatch):
    monkeypatch.setattr(transcriber, 'ME_NAME', '')
    wav = ta.RECORDINGS_DIR / f'{STEM}.wav'
    mn.add_note(ta.RECORDINGS_DIR / 'processed' / f'{STEM}.context', 125, 'ojo')
    out = tray._merge_notes(wav, ta.RECORDINGS_DIR / f'{STEM}_transcript.txt', TRANSCRIPT)
    assert '[02:05] [Nota]: ojo' in out
