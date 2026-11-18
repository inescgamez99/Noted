"""Tres integraciones con el exterior: inbox, CLI y el aviso en el chat.

Ninguna toca nada de verdad: ffmpeg, Outlook y UIAutomation se sustituyen.

Lo que protege:
- El inbox convierte a 16 kHz mono lo que dejes en la carpeta. Si eso falla en
  silencio, Whisper recibe audio a otro sample rate.
- Los comandos `start`/`stop` del CLI son un contrato ENTRE PROCESOS a traves
  de un fichero: un cambio de formato deja la app sin control remoto.
- send_recording_notice ESCRIBE EN UN CHAT CORPORATIVO. Su kill-switch es lo
  unico que impide que lo haga sin querer.
"""
import json

import pytest

import inbox_watcher as iw
import teams_chat as tc

pytestmark = pytest.mark.unit


# ── inbox: estabilidad del fichero ───────────────────────────────────────

@pytest.fixture
def sin_esperas(monkeypatch):
    """_is_stable duerme 3 s reales para ver si el fichero sigue creciendo."""
    monkeypatch.setattr(iw.time, 'sleep', lambda _s: None)


@pytest.fixture
def watcher(tr_dirs):
    w = object.__new__(iw.InboxWatcher)
    w._seen = set()
    w._ffmpeg = None
    w._on_wav_ready = None
    return w


def test_un_fichero_que_no_crece_esta_estable(watcher, sin_esperas, wav_factory):
    f = wav_factory(iw.INBOX_DIR / 'grabacion.wav', seconds=0.2)
    assert watcher._is_stable(f) is True


def test_un_fichero_de_cero_bytes_no_esta_estable(watcher, sin_esperas):
    """Windows crea el fichero antes de volcar el contenido: procesarlo ahi
    daria un WAV vacio."""
    f = iw.INBOX_DIR / 'vacio.wav'
    f.write_bytes(b'')
    assert watcher._is_stable(f) is False


def test_un_fichero_que_desaparece_no_esta_estable(watcher, monkeypatch):
    f = iw.INBOX_DIR / 'fugaz.wav'
    f.write_bytes(b'RIFF')

    def borrar_al_dormir(_s):
        f.unlink()

    monkeypatch.setattr(iw.time, 'sleep', borrar_al_dormir)
    assert watcher._is_stable(f) is False


def test_un_fichero_inestable_se_reintenta_en_la_siguiente_pasada(watcher, monkeypatch,
                                                                  wav_factory):
    """Sale de _seen para volver a mirarlo. Sin eso, un fichero que se estaba
    copiando cuando lo vimos no se procesaria nunca."""
    f = wav_factory(iw.INBOX_DIR / 'copiandose.wav', seconds=0.2)
    watcher._seen.add(f)
    monkeypatch.setattr(iw.InboxWatcher, '_is_stable', lambda self, _f: False)

    watcher._process_stable(f)

    assert f not in watcher._seen


# ── inbox: procesar el fichero ───────────────────────────────────────────

def test_un_wav_sin_ffmpeg_se_mueve_tal_cual(watcher, wav_factory):
    listos = []
    watcher._on_wav_ready = listos.append
    f = wav_factory(iw.INBOX_DIR / 'grabacion.wav', seconds=0.2)

    watcher._process(f)

    assert not f.exists()
    assert len(listos) == 1
    assert listos[0].parent == iw.RECORDINGS_DIR
    assert listos[0].name.endswith('_grabacion.wav')


def test_el_destino_lleva_fecha_y_hora_delante(watcher, wav_factory):
    """Es lo que permite al pipeline derivar el stem de las minutas."""
    import re
    listos = []
    watcher._on_wav_ready = listos.append
    watcher._process(wav_factory(iw.INBOX_DIR / 'x.wav', seconds=0.2))

    assert re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_x\.wav', listos[0].name)


@pytest.fixture
def ffmpeg_falso(monkeypatch, watcher):
    """ffmpeg que 'convierte' escribiendo un fichero en el destino."""
    llamadas = []

    def run_falso(cmd, **kwargs):
        llamadas.append(cmd)
        destino = iw.Path(cmd[-1])
        destino.write_bytes(b'RIFF-convertido')
        return type('R', (), {'returncode': 0, 'stderr': b''})()

    monkeypatch.setattr(iw.subprocess, 'run', run_falso)
    watcher._ffmpeg = 'ffmpeg-falso'
    return llamadas


def test_un_mp4_se_convierte_con_los_parametros_del_pipeline(watcher, ffmpeg_falso):
    """16000 Hz y 1 canal tienen que coincidir con config.SAMPLE_RATE y
    CHANNELS, o Whisper recibe audio que no espera."""
    f = iw.INBOX_DIR / 'reunion.mp4'
    f.write_bytes(b'fake mp4')

    watcher._process(f)

    cmd = ffmpeg_falso[0]
    assert '-ar' in cmd and cmd[cmd.index('-ar') + 1] == '16000'
    assert '-ac' in cmd and cmd[cmd.index('-ac') + 1] == '1'
    assert 'pcm_s16le' in cmd


def test_si_ffmpeg_falla_el_original_se_marca_con_error(watcher, monkeypatch):
    """No se borra: el usuario tiene que poder recuperar su fichero."""
    monkeypatch.setattr(iw.subprocess, 'run',
                        lambda cmd, **k: type('R', (), {'returncode': 1,
                                                        'stderr': b'boom'})())
    watcher._ffmpeg = 'ffmpeg-falso'
    listos = []
    watcher._on_wav_ready = listos.append

    f = iw.INBOX_DIR / 'roto.mp4'
    f.write_bytes(b'fake')

    watcher._process(f)

    assert (iw.INBOX_DIR / 'roto.mp4.error').exists()
    assert listos == [], "no debe avisar de un fichero que no se convirtio"


def test_sin_ffmpeg_un_video_no_se_procesa(watcher):
    listos = []
    watcher._on_wav_ready = listos.append
    f = iw.INBOX_DIR / 'reunion.mp4'
    f.write_bytes(b'fake')

    watcher._process(f)

    assert listos == []
    assert f.exists(), "el fichero se queda para cuando haya ffmpeg"


def test_un_wav_con_ffmpeg_se_remuestrea_y_el_original_se_conserva(
        watcher, ffmpeg_falso, wav_factory):
    """Antes el WAV convertido (16 kHz mono) se sobrescribia con el original: los .wav de
    inbox/ nunca se remuestreaban. Ahora va al pipeline el convertido, y el original
    queda aparte, en recordings/originals/ (un .wav en recordings/ lo tomaria
    _recover_pending por otra grabacion)."""
    f = wav_factory(iw.INBOX_DIR / 'estereo.wav', seconds=0.2, samplerate=48000)
    original = f.read_bytes()
    listos = []
    watcher._on_wav_ready = listos.append

    watcher._process(f)

    assert listos, "deberia haber avisado de un fichero listo"
    assert listos[0].read_bytes() == b'RIFF-convertido'
    guardado = iw.RECORDINGS_DIR / iw.ORIGINALS_DIRNAME / listos[0].name
    assert guardado.read_bytes() == original
    assert not f.exists()


def test_un_nombre_con_fecha_no_recibe_otra_hora_delante(watcher, ffmpeg_falso):
    """AppAPI.import_audio pone la fecha de la reunion en el nombre."""
    listos = []
    watcher._on_wav_ready = listos.append
    f = iw.INBOX_DIR / '2026-11-03_13-00_Comite_Semanal.m4a'
    f.write_bytes(b'fake m4a')

    watcher._process(f)

    assert listos[0].name == '2026-11-03_13-00_Comite_Semanal.wav'
    assert (iw.RECORDINGS_DIR / iw.ORIGINALS_DIRNAME / '2026-11-03_13-00_Comite_Semanal.m4a').exists()


def test_un_nombre_sin_fecha_sigue_recibiendo_la_hora_de_ahora(watcher, ffmpeg_falso):
    import re
    listos = []
    watcher._on_wav_ready = listos.append
    f = iw.INBOX_DIR / 'reunion.mp3'
    f.write_bytes(b'fake mp3')

    watcher._process(f)

    assert re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_reunion\.wav', listos[0].name)


def test_si_ffmpeg_falla_no_queda_un_wav_a_medias(watcher, monkeypatch):
    """Un WAV parcial en recordings/ lo reencolaba _recover_pending al arrancar y se
    transcribia audio cortado como si fuera bueno."""
    def run_que_escribe_y_falla(cmd, **kwargs):
        iw.Path(cmd[-1]).write_bytes(b'RIFF-a-medias')
        return type('R', (), {'returncode': 1, 'stderr': b'boom'})()

    monkeypatch.setattr(iw.subprocess, 'run', run_que_escribe_y_falla)
    watcher._ffmpeg = 'ffmpeg-falso'
    f = iw.INBOX_DIR / '2026-11-03_13-00_roto.mp4'
    f.write_bytes(b'fake')

    watcher._process(f)

    assert list(iw.RECORDINGS_DIR.glob('*.wav')) == []
    assert (iw.INBOX_DIR / '2026-11-03_13-00_roto.mp4.error').exists()


def test_si_ffmpeg_tarda_demasiado_tampoco_queda_un_wav_a_medias(watcher, monkeypatch):
    def run_que_se_cuelga(cmd, **kwargs):
        iw.Path(cmd[-1]).write_bytes(b'RIFF-a-medias')
        raise iw.subprocess.TimeoutExpired(cmd, 300)

    monkeypatch.setattr(iw.subprocess, 'run', run_que_se_cuelga)
    watcher._ffmpeg = 'ffmpeg-falso'
    f = iw.INBOX_DIR / '2026-11-03_13-00_largo.mp4'
    f.write_bytes(b'fake')

    watcher._process(f)       # no debe lanzar

    assert list(iw.RECORDINGS_DIR.glob('*.wav')) == []
    assert (iw.INBOX_DIR / '2026-11-03_13-00_largo.mp4.error').exists()


def test_un_fallo_de_ffmpeg_no_borra_la_grabacion_de_otra_reunion(watcher, monkeypatch, wav_factory):
    """Mismo nombre y mismo minuto que una reunion que ya existe: se le da otro stem
    (__2) en vez de convertir encima, y si falla, solo se descarta lo suyo."""
    existente = wav_factory(iw.RECORDINGS_DIR / '2026-11-03_13-00_Comite.wav', seconds=0.2)
    contenido = existente.read_bytes()

    def run_falla(cmd, **kwargs):
        iw.Path(cmd[-1]).write_bytes(b'RIFF-a-medias')
        return type('R', (), {'returncode': 1, 'stderr': b''})()

    monkeypatch.setattr(iw.subprocess, 'run', run_falla)
    watcher._ffmpeg = 'ffmpeg-falso'
    f = iw.INBOX_DIR / '2026-11-03_13-00_Comite.mp3'
    f.write_bytes(b'fake')

    watcher._process(f)

    assert existente.read_bytes() == contenido
    assert not (iw.RECORDINGS_DIR / '2026-11-03_13-00_Comite__2.wav').exists()


def test_una_importacion_repetida_recibe_otro_stem(watcher, ffmpeg_falso, wav_factory):
    wav_factory(iw.RECORDINGS_DIR / '2026-11-03_13-00_Comite.wav', seconds=0.2)
    listos = []
    watcher._on_wav_ready = listos.append
    f = iw.INBOX_DIR / '2026-11-03_13-00_Comite.mp3'
    f.write_bytes(b'fake')

    watcher._process(f)

    assert listos[0].name == '2026-11-03_13-00_Comite__2.wav'


def test_el_tiempo_de_conversion_crece_con_el_tamano(tmp_path):
    chico = tmp_path / 'a.mp3'
    chico.write_bytes(b'x' * 1000)
    assert iw._convert_timeout(chico) == 300
    assert iw._convert_timeout(tmp_path / 'no_existe.mp3') == 300


def test_una_excepcion_al_procesar_marca_el_fichero_y_no_propaga(watcher, monkeypatch,
                                                                 wav_factory):
    """El hilo del inbox no puede morir: dejaria de vigilar la carpeta."""
    f = wav_factory(iw.INBOX_DIR / 'x.wav', seconds=0.2)

    def move_que_revienta(*_a, **_k):
        raise OSError('disco lleno')

    monkeypatch.setattr(iw.shutil, 'move', move_que_revienta)

    watcher._process(f)          # no debe lanzar

    assert (iw.INBOX_DIR / 'x.wav.error').exists()


# ── inbox: deteccion de ffmpeg ───────────────────────────────────────────

def test_sin_ffmpeg_por_ningun_lado_devuelve_none(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', None)
    monkeypatch.setattr(iw.shutil, 'which', lambda _n: None)
    assert iw._find_ffmpeg() is None


def test_el_ffmpeg_del_path_se_usa_si_no_hay_imageio(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', None)
    monkeypatch.setattr(iw.shutil, 'which', lambda _n: 'C:/bin/ffmpeg.exe')
    assert iw._find_ffmpeg() == 'C:/bin/ffmpeg.exe'


# ── CLI: el contrato entre procesos ──────────────────────────────────────

@pytest.fixture
def runner():
    from click.testing import CliRunner
    return CliRunner()


def test_el_comando_start_escribe_la_orden_exacta(runner, tr_dirs):
    """El daemon lee este fichero y compara con 'start' tras un strip. Escribir
    otra cosa deja la app sin control remoto, en silencio."""
    import cli

    resultado = runner.invoke(cli.cli, ['start'])

    assert resultado.exit_code == 0
    assert cli.CLI_CONTROL_FILE.read_text(encoding='utf-8').strip() == 'start'


def test_el_comando_stop_escribe_la_orden_exacta(runner, tr_dirs):
    import cli

    resultado = runner.invoke(cli.cli, ['stop'])

    assert resultado.exit_code == 0
    assert cli.CLI_CONTROL_FILE.read_text(encoding='utf-8').strip() == 'stop'


def test_la_ayuda_lista_los_comandos_publicos(runner):
    """'transcribe' se registra a mano con un alias (cli.py:160). Si ese
    renombrado se rompe, el comando desaparece sin ruido."""
    import cli

    resultado = runner.invoke(cli.cli, ['--help'])

    assert resultado.exit_code == 0
    for comando in ('start', 'stop', 'transcribe', 'minutes'):
        assert comando in resultado.output


def test_transcribir_un_fichero_inexistente_falla_con_codigo_dos(runner, tr_dirs):
    """click.Path(exists=True) valida antes de cargar Whisper: no tiene sentido
    levantar el modelo para un fichero que no esta."""
    import cli

    resultado = runner.invoke(cli.cli, ['transcribe', 'no_existe.wav'])

    assert resultado.exit_code == 2


# ── El aviso en el chat de Teams: el kill-switch ─────────────────────────

def test_sin_la_opcion_activada_no_se_escribe_en_el_chat(tr_dirs, monkeypatch):
    """LA GUARDA QUE IMPORTA. Escribe en un chat corporativo, asi que por
    defecto esta desactivado y debe salir ANTES de cargar UIAutomation."""
    (tr_dirs / 'settings.json').write_text(json.dumps({'language': 'es'}),
                                           encoding='utf-8')

    def no_deberia_llegar_aqui(*_a, **_k):
        raise AssertionError("cargo UIAutomation con el aviso desactivado")

    monkeypatch.setattr(tc.comtypes.client, 'GetModule', no_deberia_llegar_aqui)

    assert tc.send_recording_notice() is False


def test_sin_fichero_de_ajustes_tampoco_se_escribe(tr_dirs, monkeypatch):
    monkeypatch.setattr(tc.comtypes.client, 'GetModule',
                        lambda *_a: (_ for _ in ()).throw(AssertionError('no')))
    assert tc.send_recording_notice() is False


def test_la_opcion_se_relee_en_cada_llamada(tr_dirs, monkeypatch):
    """Por diseno: cambiar el interruptor en el panel surte efecto sin
    reiniciar el daemon."""
    ajustes = tr_dirs / 'settings.json'
    ajustes.write_text(json.dumps({'teams_chat_notice_enabled': False}),
                       encoding='utf-8')
    assert tc.send_recording_notice() is False

    ajustes.write_text(json.dumps({'teams_chat_notice_enabled': True}),
                       encoding='utf-8')
    monkeypatch.setattr(tc, '_get_teams_pids', lambda: set())

    # Ahora si pasa la guarda de ajustes y llega a buscar Teams, que no esta.
    assert tc.send_recording_notice() is False


def test_con_el_aviso_activado_y_sin_teams_no_lanza(tr_dirs, monkeypatch):
    (tr_dirs / 'settings.json').write_text(
        json.dumps({'teams_chat_notice_enabled': True}), encoding='utf-8')
    monkeypatch.setattr(tc, '_get_teams_pids', lambda: set())

    assert tc.send_recording_notice() is False
