"""Grabar va antes: la transcripcion de una reunion ya terminada se aparta
mientras se graba otra con poco disco o memoria (26/10/2026).

Ese dia, a las 16:06, el daemon terminaba con 'medium' (~4,7 GB de reserva) la
reunion de las 15:33 mientras grababa la siguiente, y el pagefile lleno el disco.
Decision de Felipe: "Noted DEBE SIEMPRE SEGUIR GRABANDO; si es necesario, no
hacer el transcript en el momento".

LO QUE PROTEGEN
1. La pausa suelta el modelo: esperar con el cargado no libera nada.
2. No se confunde con un fallo: ni otro modelo, ni la API de OpenAI, ni "el en
   vivo no sirve, empiezo de cero", ni un intento fallido de cierre.
3. Lo hecho no se repite al seguir.
4. Sin grabacion en curso, nada cambia.
"""
import pytest

import live_transcriber as lt
import transcriber
import tray_app as ta
from test_live_transcriber import TrackModel, make_track, rec, write_records

pytestmark = pytest.mark.unit

SR = lt.SR
STEM = '2026-10-26_15-33_Revision_Deck'


@pytest.fixture
def wav(tr_dirs):
    return ta.RECORDINGS_DIR / f'{STEM}.wav'


@pytest.fixture
def model(monkeypatch):
    m = TrackModel()
    liberado = []
    monkeypatch.setattr(transcriber, '_get_model', lambda name=None: m)
    monkeypatch.setattr(transcriber, '_release_model', lambda: liberado.append(1))
    m.liberado = liberado
    return m


# ── 1. La pausa ─────────────────────────────────────────────────────────────

def test_defer_aparta_la_transcripcion():
    with pytest.raises(transcriber.TranscriptionDeferred):
        transcriber._wait_if_paused(lambda: transcriber.DEFER, None, poll=0)


def test_sin_defer_la_pausa_es_la_de_siempre():
    estados = iter([True, True, False])
    transcriber._wait_if_paused(lambda: next(estados), None, poll=0)


def test_transcribe_aplazada_no_cae_a_openai_ni_a_otro_modelo(tr_dirs, wav_factory, model, monkeypatch):
    audio = wav_factory(tr_dirs / 'recordings' / 'x.wav', seconds=1.0)
    monkeypatch.setattr(transcriber, '_transcribe_openai',
                        lambda *_a: pytest.fail('aplazar no es fallar: nada de la API de pago'))
    with pytest.raises(transcriber.TranscriptionDeferred):
        transcriber.transcribe(audio, should_pause=lambda: transcriber.DEFER)
    assert model.liberado, "el modelo debe soltarse al apartarse"
    assert model.lengths == [], "no debia llegar a transcribir"


# ── 2 y 3. El cierre con lo hecho durante la reunion ────────────────────────

def _dos_huecos(wav):
    """Micro y sistema comprometidos hasta los 15 s; quedan dos bloques de 5 s."""
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')), rec('loop', 0, 15, (3.0, 'buenas')))
    make_track(lt.track_wav(wav, 'mic'), 20)
    make_track(lt.track_wav(wav, 'loop'), 20)
    return len(lt.read_records(wav))


def test_el_cierre_aplazado_guarda_lo_hecho_y_al_seguir_no_lo_repite(wav, model):
    antes = _dos_huecos(wav)
    tras_un_bloque = lambda: transcriber.DEFER if len(lt.read_records(wav)) > antes else False  # noqa: E731

    with pytest.raises(transcriber.TranscriptionDeferred):
        lt.finish_live(wav, should_pause=tras_un_bloque)
    assert model.lengths == [5 * SR]
    assert len(lt.read_records(wav)) == antes + 1, "el bloque hecho debe quedar guardado"

    model.lengths.clear()
    text, _ = lt.finish_live(wav)
    assert model.lengths == [5 * SR], "solo el bloque que faltaba"
    # El doble devuelve 'resto' en las dos pistas en el mismo instante: el del
    # micro es eco del de Teams (mismo texto, mismo momento) y se quita.
    assert 'hola' in text and 'buenas' in text and text.count('resto') == 1


def test_aplazar_el_cierre_no_cuenta_como_intento(wav, model):
    """Con FINISH_MAX_ATTEMPTS el cierre se rinde y el pipeline lo repite todo."""
    _dos_huecos(wav)
    lt.finishing_path(wav).write_text('1')
    with pytest.raises(transcriber.TranscriptionDeferred):
        lt.finish_live(wav, should_pause=lambda: transcriber.DEFER)
    assert lt.finishing_path(wav).read_text() == '1'


def test_sin_intentos_previos_aplazar_no_deja_contador(wav, model):
    _dos_huecos(wav)
    with pytest.raises(transcriber.TranscriptionDeferred):
        lt.finish_live(wav, should_pause=lambda: transcriber.DEFER)
    assert not lt.finishing_path(wav).exists()


def test_el_pipeline_no_toma_la_pausa_por_un_en_vivo_inservible(tray, wav, monkeypatch):
    """Si la tomara por un fallo, transcribiria la reunion entera de nuevo."""
    write_records(wav, rec('mic', 0, 15, (1.0, 'hola')))

    def aplazada(*_a, **_k):
        raise transcriber.TranscriptionDeferred()

    monkeypatch.setattr(lt, 'finish_live', aplazada)
    monkeypatch.setattr(tray, 'set_processing', lambda *_a: None)
    transcript = wav.with_name(f'{wav.stem}_transcript.txt')
    with pytest.raises(transcriber.TranscriptionDeferred):
        tray._use_live_transcript(wav, transcript, transcript.with_suffix('.lang'))
    assert not transcript.exists()


# ── El pipeline espera y sigue ──────────────────────────────────────────────

class GrabaUnRato:
    """Graba durante unas cuantas comprobaciones y luego guarda otras tantas."""

    def __init__(self, grabando=3, guardando=0):
        self.grabando, self.guardando = grabando, guardando

    @property
    def is_recording(self):
        self.grabando -= 1
        return self.grabando >= 0

    @property
    def is_saving(self):
        self.guardando -= 1
        return self.guardando >= 0


def test_el_pipeline_espera_a_que_termine_la_grabacion_y_sigue(tray, wav, monkeypatch):
    """Sin reintentar a mitad de la llamada: cada intento paga una carga del
    modelo y, apartandose otra vez antes de cerrar un trozo, no avanzaria."""
    monkeypatch.setattr(ta, 'DEFER_RECHECK_S', 0.01)
    mensajes, intentos = [], []
    monkeypatch.setattr(tray, 'set_processing', mensajes.append)
    monkeypatch.setattr(tray, '_defer_reason', lambda: 'disco')
    tray._recorder = GrabaUnRato(grabando=3, guardando=2)

    def run(_w):
        intentos.append(tray._recorder.grabando)
        if len(intentos) == 1:
            raise transcriber.TranscriptionDeferred()

    monkeypatch.setattr(tray, '_run_pipeline_sync', run)
    tray._run_pipeline_until_done(wav)
    assert len(intentos) == 2
    assert tray._recorder.grabando < 0 and tray._recorder.guardando < 0, \
        "siguio antes de terminar la grabacion y su guardado"
    assert mensajes[0] == ta._STR[ta.get_ui_language()]['deferred']


def test_mientras_espera_la_reunion_en_curso_recupera_el_modelo(tray, wav, monkeypatch):
    """Si no, con el disco entre 2 y 6 GB nadie transcribiria (revision del 26/10)."""
    monkeypatch.setattr(ta, 'DEFER_RECHECK_S', 0.01)
    monkeypatch.setattr(tray, 'set_processing', lambda *_a: None)
    tray._recorder = GrabaUnRato(grabando=1)
    tray._whisper_busy = True
    tray._pipeline_queue.put(wav.with_name('otra.wav'))          # aunque haya cola
    tray._wait_while_deferred(wav)
    assert tray._whisper_busy is False


def test_si_hay_que_apartarse_no_pide_el_modelo_a_la_reunion_en_curso(tray, wav, monkeypatch):
    monkeypatch.setattr(tray, '_defer_reason', lambda: 'disco')
    monkeypatch.setattr(tray, '_claim_whisper',
                        lambda: pytest.fail('le quitaria el modelo para no usarlo'))
    with pytest.raises(transcriber.TranscriptionDeferred):
        tray._run_pipeline_sync(wav)


def test_descartar_mientras_espera_la_descarta(tray, wav, monkeypatch):
    monkeypatch.setattr(ta, 'DEFER_RECHECK_S', 0.01)
    monkeypatch.setattr(tray, 'set_processing', lambda *_a: None)
    tray._recorder = GrabaUnRato(grabando=10 ** 6)

    def run(_w):
        raise transcriber.TranscriptionDeferred()

    monkeypatch.setattr(tray, '_run_pipeline_sync', run)
    monkeypatch.setattr(tray, '_is_cancelled', lambda _stem: True)
    with pytest.raises(transcriber.TranscriptionCancelled):
        tray._run_pipeline_until_done(wav)


# ── Cuando se aparta ────────────────────────────────────────────────────────

class Grabador:
    def __init__(self, grabando):
        self.is_recording = grabando


@pytest.fixture
def recursos(monkeypatch):
    estado = {'disk_ok': True, 'mem_ok': True, 'disk_floor': True, 'mem_floor': True}
    monkeypatch.setattr(lt, '_disk_ok', lambda _n: estado['disk_ok'])
    monkeypatch.setattr(lt, '_memory_ok', lambda _n: estado['mem_ok'])
    monkeypatch.setattr(lt, '_disk_floor_ok', lambda: estado['disk_floor'])
    monkeypatch.setattr(lt, '_memory_floor_ok', lambda: estado['mem_floor'])
    monkeypatch.setattr(transcriber, '_model', None)
    return estado


def test_sin_grabacion_nunca_se_aparta(tray, recursos):
    recursos.update(disk_ok=False, mem_ok=False, disk_floor=False, mem_floor=False)
    tray._recorder = Grabador(False)
    assert tray._defer_reason() is None
    tray._recorder = None
    assert tray._defer_reason() is None


def test_antes_de_cargar_pide_lo_que_pide_el_modelo(tray, recursos):
    tray._recorder = Grabador(True)
    assert tray._defer_reason() is None
    recursos['disk_ok'] = False
    assert tray._defer_reason() == 'disco'
    recursos.update(disk_ok=True, mem_ok=False)
    assert tray._defer_reason() == 'memoria'


def test_con_el_modelo_cargado_solo_cede_bajo_los_suelos(tray, recursos, monkeypatch):
    """Si no, con 4-6 GB libres cargaria y cederia en cada bloque."""
    monkeypatch.setattr(transcriber, '_model', object())
    tray._recorder = Grabador(True)
    recursos.update(disk_ok=False, mem_ok=False)
    assert tray._defer_reason() is None
    recursos['disk_floor'] = False
    assert tray._defer_reason() == 'disco'
    recursos.update(disk_floor=True, mem_floor=False)
    assert tray._defer_reason() == 'memoria'


def test_el_should_pause_del_pipeline(tray, monkeypatch):
    monkeypatch.setattr(tray, '_defer_reason', lambda: 'disco')
    assert tray._pipeline_should_pause() == transcriber.DEFER
    monkeypatch.setattr(tray, '_defer_reason', lambda: None)
    monkeypatch.setattr(tray, '_live_priority', lambda: False)
    assert tray._pipeline_should_pause() is False
    monkeypatch.setattr(tray, '_live_priority', lambda: True)
    monkeypatch.setattr(transcriber, '_model', None)
    assert tray._pipeline_should_pause() is True


def test_con_el_modelo_cargado_el_en_vivo_lo_aparta(tray, monkeypatch):
    """Revision del 28/10: pausar con 'medium' cargado lo dejaba toda la reunion
    en memoria junto al 'small' del en vivo. Se aparta: suelta el modelo y sigue
    al colgar."""
    monkeypatch.setattr(tray, '_defer_reason', lambda: None)
    monkeypatch.setattr(tray, '_live_priority', lambda: True)
    monkeypatch.setattr(transcriber, '_model', object())
    assert tray._pipeline_should_pause() == transcriber.DEFER


def test_mientras_se_guarda_la_anterior_tambien_se_aparta(tray, recursos):
    """Con el disco lleno, el guardado de la reunion que acaba de terminar
    espera sitio con su audio en memoria: no se le puede quitar con 'medium'."""
    class Guardando:
        is_recording = False
        is_saving = True

    tray._recorder = Guardando()
    recursos['disk_ok'] = False
    assert tray._defer_reason() == 'disco'
