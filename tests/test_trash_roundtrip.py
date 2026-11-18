"""Papelera de punta a punta: descartar -> ver en el panel -> recuperar.

Dos rutas distintas escriben en `trash/` y la web tiene que entender las dos:

  tray_app._discard_job    -> stem del WAV     (2026-09-16_12-00_Reunion)
  app_window.delete_meeting -> stem de minutas (20260916_1200_Reunion)

Si `list_trash` no sabe leer una de las dos, esa reunion aparece sin fecha ni
titulo en el panel y el usuario no puede recuperarla.
"""
import json

import pytest

import app_window as aw
import tray_app as ta

pytestmark = pytest.mark.unit

WAV_STEM = '2026-09-16_12-00_Reunion_Descartable'
MD_STEM = '20260916_1200_Reunion_Descartable'


@pytest.fixture
def api():
    """La clase API de pywebview, pero sin pywebview: sus metodos de papelera
    no lo tocan, solo mueven ficheros."""
    return object.__new__(aw.AppAPI)


@pytest.fixture
def reunion(tr_dirs, wav_factory):
    """Los cuatro artefactos de una reunion procesada."""
    wav = wav_factory(ta.RECORDINGS_DIR / f'{WAV_STEM}.wav', seconds=0.2)
    (ta.RECORDINGS_DIR / f'{WAV_STEM}_transcript.txt').write_text('t', encoding='utf-8')
    md = ta.MINUTES_DIR / f'{MD_STEM}.md'
    md.write_text('TITULO: Reunion Descartable\n\ncontenido', encoding='utf-8')
    md.with_suffix('.html').write_text('<html></html>', encoding='utf-8')
    (ta.MINUTES_DIR / f'{MD_STEM}_actions.json').write_text(
        json.dumps({'project_id': 'none', 'actions': []}), encoding='utf-8')
    (ta.MINUTES_DIR / f'{MD_STEM}_transcript.txt').write_text('t', encoding='utf-8')
    return {'wav': wav, 'md': md, 'trash': tr_dirs / 'trash'}


# ── La papelera vacia ─────────────────────────────────────────────────────

def test_sin_papelera_la_lista_esta_vacia(api, tr_dirs):
    assert api.list_trash() == []


def test_una_carpeta_sin_meta_se_ignora(api, tr_dirs):
    """Basura en trash/ no debe romper el panel."""
    (tr_dirs / 'trash' / 'suelta').mkdir(parents=True)
    assert api.list_trash() == []


# ── Ruta 1: descartar desde el pipeline (stem del WAV) ───────────────────

def test_lo_descartado_por_el_pipeline_aparece_en_el_panel(tray, api, reunion):
    tray._discard_job(reunion['wav'], 'descartada en cola', reunion['md'])

    items = api.list_trash()
    assert len(items) == 1
    assert items[0]['cancelled'] is True
    assert items[0]['reason'] == 'descartada en cola'


def test_el_panel_lee_bien_la_fecha_del_stem_del_wav(tray, api, reunion):
    tray._discard_job(reunion['wav'], 'test', reunion['md'])
    item = api.list_trash()[0]
    assert (item['date'], item['time']) == ('2026-09-16', '12:00')


def test_list_trash_anade_id_y_numero_de_ficheros(tray, api, reunion):
    """Las dos claves que el JS necesita y que no estan en el meta escrito."""
    tray._discard_job(reunion['wav'], 'test', reunion['md'])
    item = api.list_trash()[0]
    assert item['id']
    assert item['file_count'] >= 4


# ── Ruta 2: borrar desde el panel (stem de minutas) ──────────────────────

def test_lo_borrado_desde_el_panel_aparece_en_el_panel(api, reunion):
    assert api.delete_meeting(str(reunion['md'])) is True

    items = api.list_trash()
    assert len(items) == 1
    assert items[0]['title'] == 'Reunion Descartable'


def test_el_panel_lee_bien_la_fecha_del_stem_de_minutas(api, reunion):
    api.delete_meeting(str(reunion['md']))
    item = api.list_trash()[0]
    assert (item['date'], item['time']) == ('2026-09-16', '12:00')


def test_borrar_desde_el_panel_se_lleva_tambien_el_wav(api, reunion):
    """El stem de minutas no casa con el del WAV: hay que reformatear la fecha
    para encontrarlo. Si eso se rompe, el audio se queda suelto en disco."""
    api.delete_meeting(str(reunion['md']))

    assert not reunion['wav'].exists()
    nombres = {f['name'] for f in api.list_trash()[0]['files']}
    assert f'{WAV_STEM}.wav' in nombres


def test_borrar_desde_el_panel_no_marca_cancelled(api, reunion):
    """Diferencia real entre las dos rutas: borrar a mano no es cancelar un
    trabajo, y el panel lo distingue."""
    api.delete_meeting(str(reunion['md']))
    assert 'cancelled' not in api.list_trash()[0]


# ── El round-trip completo ────────────────────────────────────────────────

def test_recuperar_devuelve_cada_fichero_a_su_carpeta(api, reunion):
    """El test estrella: sin esto, 'recuperar' es un boton que promete algo
    que no cumple."""
    api.delete_meeting(str(reunion['md']))
    item_id = api.list_trash()[0]['id']

    assert api.recover_meeting(item_id) is True

    assert reunion['md'].exists()
    assert reunion['md'].with_suffix('.html').exists()
    assert (ta.MINUTES_DIR / f'{MD_STEM}_actions.json').exists()
    assert reunion['wav'].exists()
    assert api.list_trash() == []


def test_recuperar_dos_veces_devuelve_false(api, reunion):
    api.delete_meeting(str(reunion['md']))
    item_id = api.list_trash()[0]['id']
    api.recover_meeting(item_id)
    assert api.recover_meeting(item_id) is False


def test_recuperar_algo_inexistente_devuelve_false(api, tr_dirs):
    assert api.recover_meeting('no-existe') is False


def test_purgar_borra_definitivamente(api, reunion):
    api.delete_meeting(str(reunion['md']))
    item_id = api.list_trash()[0]['id']

    assert api.purge_trash_meeting(item_id) is True
    assert api.list_trash() == []
    assert not reunion['md'].exists(), "purgar no debe restaurar nada"


def test_purgar_dos_veces_devuelve_false(api, reunion):
    api.delete_meeting(str(reunion['md']))
    item_id = api.list_trash()[0]['id']
    api.purge_trash_meeting(item_id)
    assert api.purge_trash_meeting(item_id) is False


# ── Orden y colisiones ────────────────────────────────────────────────────

def test_las_mas_recientes_primero(api, tray, reunion, wav_factory, tr_dirs):
    tray._discard_job(reunion['wav'], 'primera', reunion['md'])
    otro_wav = wav_factory(ta.RECORDINGS_DIR / '2026-09-17_09-00_Otra.wav', seconds=0.1)
    tray._discard_job(otro_wav, 'segunda')

    fechas = [i['deleted_at'] for i in api.list_trash()]
    assert fechas == sorted(fechas, reverse=True)


def test_descartar_el_mismo_stem_dos_veces_crea_dos_entradas(api, tray, reunion,
                                                             wav_factory):
    tray._discard_job(reunion['wav'], 'primera', reunion['md'])
    otra_vez = wav_factory(ta.RECORDINGS_DIR / f'{WAV_STEM}.wav', seconds=0.1)
    tray._discard_job(otra_vez, 'segunda')

    ids = sorted(i['id'] for i in api.list_trash())
    assert len(ids) == 2
    assert ids[1].endswith('__2'), f"anticolision roto: {ids}"


# ── Arreglos de la papelera (antes fijados como test_DEFECTO_*) ──────────

def test_un_orig_dir_vacio_se_salta_y_no_restaura_en_el_cwd(api, tr_dirs, monkeypatch):
    """Antes (DEFECTO): la guarda `if not str(dst_dir)` era una rama muerta,
    porque str(Path('')) es '.', y un orig_dir vacio restauraba el fichero en
    el cwd del proceso. Ahora se salta ese fichero.

    Se ejecuta con el cwd apuntando a tmp_path para no ensuciar nada."""
    trash_dir = tr_dirs / 'trash' / 'manipulada'
    trash_dir.mkdir(parents=True)
    (trash_dir / 'suelto.md').write_text('x', encoding='utf-8')
    (trash_dir / '_trash_meta.json').write_text(json.dumps({
        'stem': 'x', 'title': 'X', 'date': '', 'time': '', 'deleted_at': '2026-01-01',
        'files': [{'name': 'suelto.md', 'orig_dir': ''}],
    }), encoding='utf-8')

    destino_falso = tr_dirs / 'cwd'
    destino_falso.mkdir()
    monkeypatch.chdir(destino_falso)

    api.recover_meeting('manipulada')
    assert not (destino_falso / 'suelto.md').exists()


def test_si_falla_un_movimiento_recuperar_conserva_la_papelera(api, tr_dirs,
                                                               monkeypatch):
    """Antes (DEFECTO): el rmtree corria incondicionalmente tras el bucle; si
    un move fallaba, la papelera se borraba igual, el fichero se perdia en
    silencio y devolvia True. Ahora devuelve False y conserva todo."""
    trash_dir = tr_dirs / 'trash' / 'fallona'
    trash_dir.mkdir(parents=True)
    (trash_dir / 'a.md').write_text('x', encoding='utf-8')
    destino = tr_dirs / 'minutes'
    (trash_dir / '_trash_meta.json').write_text(json.dumps({
        'stem': 'a', 'title': 'A', 'date': '', 'time': '', 'deleted_at': '2026-01-01',
        'files': [{'name': 'a.md', 'orig_dir': str(destino)}],
    }), encoding='utf-8')

    def move_que_falla(*_a, **_k):
        raise OSError('destino ocupado')

    monkeypatch.setattr(aw.shutil, 'move', move_que_falla)

    assert api.recover_meeting('fallona') is False
    assert (trash_dir / 'a.md').exists(), "el fichero sigue en la papelera"
    assert not (destino / 'a.md').exists()


def test_recuperar_no_pisa_un_fichero_existente(api, reunion):
    """shutil.move sobre un destino existente lo sobrescribe: si el usuario ya
    tiene otra minuta con ese nombre, se restaura como <stem>_recuperada."""
    api.delete_meeting(str(reunion['md']))
    reunion['md'].write_text('NUEVA, no pisar', encoding='utf-8')
    item_id = api.list_trash()[0]['id']

    assert api.recover_meeting(item_id) is True

    assert reunion['md'].read_text(encoding='utf-8') == 'NUEVA, no pisar'
    rec = reunion['md'].with_name(f'{MD_STEM}_recuperada.md')
    assert rec.read_text(encoding='utf-8').startswith('TITULO:')


@pytest.mark.parametrize('malo', ['..', '../x', 'a/b', 'a\\b', '', '   '])
def test_trash_id_peligroso_se_rechaza(api, tr_dirs, malo):
    """Un trash_id con separadores o '..' podia apuntar fuera de la papelera:
    purge_trash_meeting hacia rmtree de esa ruta."""
    fuera = tr_dirs / 'x'
    fuera.mkdir()
    (fuera / 'f.txt').write_text('no tocar', encoding='utf-8')
    (tr_dirs / 'trash').mkdir(exist_ok=True)

    assert api.purge_trash_meeting(malo) is False
    assert api.recover_meeting(malo) is False
    assert (fuera / 'f.txt').exists()


def test_delete_meeting_si_fallan_todos_los_movimientos_devuelve_false(
        api, reunion, monkeypatch):
    """Con los ficheros en uso, delete_meeting devolvia True y escribia un
    _trash_meta.json vacio: la reunion seguia en su sitio y la papelera
    mostraba una entrada fantasma."""
    def move_que_falla(*_a, **_k):
        raise OSError('en uso')
    monkeypatch.setattr(aw.shutil, 'move', move_que_falla)

    assert api.delete_meeting(str(reunion['md'])) is False

    assert reunion['md'].exists()
    assert api.list_trash() == []
    assert not any(reunion['trash'].glob('*/_trash_meta.json'))


def test_delete_meeting_no_se_lleva_el_wav_de_otra_reunion_del_mismo_minuto(
        api, reunion, wav_factory):
    """El glob `{wav_stem}*.wav` capturaba el WAV de OTRA reunion iniciada en
    el mismo minuto."""
    otro = wav_factory(ta.RECORDINGS_DIR / '2026-09-16_12-00_Otra_Reunion.wav',
                       seconds=0.1)

    assert api.delete_meeting(str(reunion['md'])) is True

    assert not reunion['wav'].exists()
    assert otro.exists()


def test_delete_meeting_con_titulo_distinto_y_un_solo_wav_lo_incluye(
        api, tr_dirs, wav_factory):
    """El slug de las minutas sale del titulo generado, no del nombre del WAV:
    si solo hay un WAV en ese minuto, es el de la reunion."""
    wav = wav_factory(ta.RECORDINGS_DIR / '2026-09-16_12-00_Grabacion.wav', seconds=0.1)
    md = ta.MINUTES_DIR / '20260916_1200_Titulo_Generado.md'
    md.write_text('TITULO: Titulo Generado\n\nx', encoding='utf-8')

    assert api.delete_meeting(str(md)) is True
    assert not wav.exists()


def test_delete_meeting_con_varios_wav_y_ninguno_exacto_los_conserva(
        api, tr_dirs, wav_factory):
    a = wav_factory(ta.RECORDINGS_DIR / '2026-09-16_12-00_A.wav', seconds=0.1)
    b = wav_factory(ta.RECORDINGS_DIR / '2026-09-16_12-00_B.wav', seconds=0.1)
    md = ta.MINUTES_DIR / '20260916_1200_Otro_Titulo.md'
    md.write_text('TITULO: x\n\nx', encoding='utf-8')

    assert api.delete_meeting(str(md)) is True
    assert a.exists() and b.exists()


def test_trash_id_absoluto_hacia_fuera_se_rechaza(api, tr_dirs):
    """Ruta absoluta como trash_id: `trash / absoluta` es la absoluta."""
    fuera = tr_dirs / 'otra'
    fuera.mkdir()
    (fuera / 'f.txt').write_text('no tocar', encoding='utf-8')
    (tr_dirs / 'trash').mkdir(exist_ok=True)
    assert api.purge_trash_meeting(str(fuera)) is False
    assert (fuera / 'f.txt').exists()
