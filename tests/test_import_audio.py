"""Importar un audio o video que no grabo Noted (AppAPI.import_audio).

El panel y el daemon son procesos distintos: el panel COPIA el archivo a inbox/ con
la fecha y el titulo elegidos y el InboxWatcher del daemon lo convierte y lo procesa.
Lo que se protege aqui es que el original no se toque, que el watcher no coja una
copia a medias, que dos importaciones iguales no se pisen y que cada error tenga su
codigo (la UI los traduce).
"""
import re

import pytest

import app_window as aw
import inbox_watcher as iw
import storage

pytestmark = pytest.mark.unit


@pytest.fixture
def api():
    return object.__new__(aw.AppAPI)


@pytest.fixture
def con_ffmpeg(monkeypatch):
    monkeypatch.setattr(iw, '_find_ffmpeg', lambda: 'ffmpeg-falso')


@pytest.fixture
def mp3(tmp_path):
    f = tmp_path / 'llamada cliente.mp3'
    f.write_bytes(b'ID3' + b'x' * 2000)
    return f


def test_copia_a_inbox_con_la_fecha_y_el_titulo_elegidos(api, tr_dirs, con_ffmpeg, mp3):
    r = api.import_audio(str(mp3), 'Comite Semanal', '2026-11-03T13:00')

    assert r == {'ok': True, 'name': '2026-11-03_13-00_Comite_Semanal.mp3',
                 'stem': '2026-11-03_13-00_Comite_Semanal'}
    assert (iw.INBOX_DIR / r['name']).read_bytes() == mp3.read_bytes()


def test_el_original_no_se_mueve_ni_se_toca(api, tr_dirs, con_ffmpeg, mp3):
    antes = mp3.read_bytes()
    api.import_audio(str(mp3), 'x', '2026-11-03T13:00')
    assert mp3.exists() and mp3.read_bytes() == antes


def test_no_queda_un_temporal_y_el_watcher_lo_ignoraria(api, tr_dirs, con_ffmpeg, mp3):
    """La copia se escribe como .importing, que no es una extension de audio: el watcher
    no la ve hasta el renombrado final."""
    assert '.importing' not in iw.ALL_EXTS
    api.import_audio(str(mp3), 'x', '2026-11-03T13:00')
    assert list(iw.INBOX_DIR.glob('*.importing')) == []


def test_una_copia_que_falla_no_deja_restos(api, tr_dirs, con_ffmpeg, mp3, monkeypatch):
    def copia_que_revienta(src, dst, *a, **k):
        open(dst, 'wb').write(b'parcial')
        raise OSError('disco lleno')

    monkeypatch.setattr(aw.shutil, 'copyfile', copia_que_revienta)

    r = api.import_audio(str(mp3), 'x', '2026-11-03T13:00')

    assert r == {'ok': False, 'error': 'copy_failed'}
    assert list(iw.INBOX_DIR.iterdir()) == []


def test_dos_importaciones_iguales_no_se_pisan(api, tr_dirs, con_ffmpeg, mp3):
    a = api.import_audio(str(mp3), 'Comite', '2026-11-03T13:00')
    b = api.import_audio(str(mp3), 'Comite', '2026-11-03T13:00')

    assert a['stem'] == '2026-11-03_13-00_Comite'
    assert b['stem'] == '2026-11-03_13-00_Comite__2'
    assert len(list(iw.INBOX_DIR.glob('*.mp3'))) == 2


def test_no_pisa_una_reunion_que_ya_existe(api, tr_dirs, con_ffmpeg, mp3, wav_factory):
    wav_factory(storage.RECORDINGS_DIR / '2026-11-03_13-00_Comite.wav', seconds=0.2)

    r = api.import_audio(str(mp3), 'Comite', '2026-11-03T13:00')

    assert r['stem'] == '2026-11-03_13-00_Comite__2'


def test_el_titulo_se_sanea(api, tr_dirs, con_ffmpeg, mp3):
    r = api.import_audio(str(mp3), 'Revision: Q4 / "final"?', '2026-11-03T13:00')
    assert r['stem'] == '2026-11-03_13-00_Revision_Q4_final'


def test_sin_titulo_se_usa_el_nombre_del_archivo(api, tr_dirs, con_ffmpeg, mp3):
    r = api.import_audio(str(mp3), '   ', '2026-11-03T13:00')
    assert r['stem'] == '2026-11-03_13-00_llamada_cliente'


@pytest.mark.parametrize('when', ['', 'no es una fecha', '2026-13-45T99:99'])
def test_una_fecha_invalida_usa_la_de_ahora(api, tr_dirs, con_ffmpeg, mp3, when):
    r = api.import_audio(str(mp3), 'x', when)
    assert re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_x$', r['stem'])


def test_acepta_el_formato_de_un_input_datetime_local(api, tr_dirs, con_ffmpeg, mp3):
    assert api.import_audio(str(mp3), 'x', '2026-11-03T13:05')['stem'].startswith('2026-11-03_13-05')


# ── errores, cada uno con su codigo ──────────────────────────────────────────

def test_un_archivo_inexistente(api, tr_dirs, tmp_path):
    assert api.import_audio(str(tmp_path / 'no.mp3')) == {'ok': False, 'error': 'not_found'}
    assert api.import_audio('') == {'ok': False, 'error': 'not_found'}


def test_una_carpeta_no_es_un_archivo(api, tr_dirs, tmp_path):
    assert api.import_audio(str(tmp_path)) == {'ok': False, 'error': 'not_found'}


def test_una_extension_que_no_es_de_audio_ni_video(api, tr_dirs, tmp_path):
    f = tmp_path / 'notas.pdf'
    f.write_bytes(b'%PDF')
    assert api.import_audio(str(f)) == {'ok': False, 'error': 'bad_ext'}


def test_un_archivo_vacio(api, tr_dirs, tmp_path, con_ffmpeg):
    f = tmp_path / 'vacio.mp3'
    f.write_bytes(b'')
    assert api.import_audio(str(f)) == {'ok': False, 'error': 'empty'}


def test_sin_ffmpeg_un_mp3_se_rechaza_antes_de_copiar(api, tr_dirs, mp3, monkeypatch):
    """Sin esto el archivo se quedaria en inbox/ y el watcher solo avisaria en el log."""
    monkeypatch.setattr(iw, '_find_ffmpeg', lambda: None)
    assert api.import_audio(str(mp3), 'x', '2026-11-03T13:00') == {'ok': False, 'error': 'no_ffmpeg'}
    assert list(iw.INBOX_DIR.iterdir()) == []


def test_sin_ffmpeg_un_wav_si_se_importa(api, tr_dirs, monkeypatch, wav_factory, tmp_path):
    monkeypatch.setattr(iw, '_find_ffmpeg', lambda: None)
    f = wav_factory(tmp_path / 'voz.wav', seconds=0.2)
    assert api.import_audio(str(f), 'x', '2026-11-03T13:00')['ok'] is True


def test_sin_espacio_en_disco(api, tr_dirs, con_ffmpeg, mp3, monkeypatch):
    monkeypatch.setattr(aw.shutil, 'disk_usage',
                        lambda p: type('U', (), {'total': 10, 'used': 10, 'free': 100})())
    assert api.import_audio(str(mp3)) == {'ok': False, 'error': 'no_space'}
    assert list(iw.INBOX_DIR.iterdir()) == []


# ── el selector ──────────────────────────────────────────────────────────────

def test_el_selector_propone_titulo_y_fecha_del_archivo(api, tr_dirs, mp3, monkeypatch):
    monkeypatch.setattr(aw.AppAPI, 'pick_files', lambda self, types=None: [str(mp3)])

    [item] = api.pick_audio_files()

    assert item['path'] == str(mp3)
    assert item['name'] == 'llamada cliente.mp3'
    assert item['title'] == 'llamada cliente'
    assert re.match(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$', item['when'])
    assert item['size_mb'] >= 0


def test_el_selector_cancelado_devuelve_una_lista_vacia(api, monkeypatch):
    monkeypatch.setattr(aw.AppAPI, 'pick_files', lambda self, types=None: [])
    assert api.pick_audio_files() == []


def test_el_selector_filtra_por_las_extensiones_del_watcher(api, monkeypatch):
    vistos = []
    monkeypatch.setattr(aw.AppAPI, 'pick_files', lambda self, types=None: vistos.append(types) or [])
    api.pick_audio_files()
    assert all(f'*{e}' in vistos[0][0] for e in iw.ALL_EXTS)
