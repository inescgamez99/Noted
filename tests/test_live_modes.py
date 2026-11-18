"""Dos modos de transcripcion durante la reunion (decision de Felipe, 19/10/2026).

- Por defecto: el modelo de siempre, de fondo, para que al colgar quede poco.
- En vivo: 'small' ya, y el resto de transcripciones se pausan.

LO QUE ESTOS TESTS PROTEGEN
1. Nunca dos modelos grandes a la vez: cuando el pipeline necesita Whisper, el
   proceso de fondo suelta el suyo (o se le para). Con poca memoria, dos modelos
   a la vez es lo que ya tumbo la app.
2. El modo en vivo tiene prioridad: el pipeline se pausa de verdad, y deja de
   pausarse si el proceso en vivo falla (si no, los dos se quedarian esperando).
3. Las vias para elegir o cambiar de modo (ventana, menu, panel, CLI) hacen lo
   que dicen, y un stem equivocado no cambia nada.
"""
import json
import time
import types

import pytest

import live_transcriber as lt
import transcriber
import tray_app as ta
from conftest import FakeInfo, FakeSegment

pytestmark = pytest.mark.unit

SR = lt.SR
STEM = '2026-10-19_09-00_Entrevista'


@pytest.fixture
def wav(tr_dirs):
    return ta.RECORDINGS_DIR / f'{STEM}.wav'


def rec(track, a_s, b_s, model='medium'):
    return {'track': track, 'start_frame': int(a_s * SR), 'end_frame': int(b_s * SR),
            'segments': [], 'model': model}


class Proc:
    def __init__(self, alive=True):
        self.alive = alive

    def poll(self):
        return None if self.alive else 0


# ── El fichero de control ────────────────────────────────────────────────────

def test_sin_control_el_modo_es_fondo_y_libre(wav):
    assert lt.read_ctl(wav) == {'mode': 'background', 'gate': 'free', 'yield_id': 0}


def test_valores_raros_en_el_control_no_rompen_nada(wav):
    lt.ctl_path(wav).write_text('{"mode": "turbo", "gate": 3, "yield_id": "x"}', encoding='utf-8')
    assert lt.read_ctl(wav) == {'mode': 'background', 'gate': 'free', 'yield_id': 0}


def test_escribir_el_control_conserva_lo_que_no_se_toca(wav):
    lt.write_ctl(wav, mode='live', gate='free', yield_id=4)
    lt.write_ctl(wav, gate='yield')
    assert lt.read_ctl(wav) == {'mode': 'live', 'gate': 'yield', 'yield_id': 4}


def test_spawn_no_pisa_el_control_que_ya_escribio_el_daemon(wav, tr_dirs, monkeypatch):
    """El usuario puede haber pedido en vivo mientras el spawn esperaba al loopback."""
    from conftest import REAL_LIVE_SPAWN
    monkeypatch.setattr(lt, 'START_DELAY_S', 0)
    monkeypatch.setattr(lt.subprocess, 'Popen', lambda *_a, **_k: types.SimpleNamespace(pid=1))
    lt.write_ctl(wav, mode='live', gate='free')
    REAL_LIVE_SPAWN(wav, mode='background', gate='yield')
    assert lt.read_ctl(wav) == {'mode': 'live', 'gate': 'free', 'yield_id': 0}


# ── Intervalos ───────────────────────────────────────────────────────────────

def test_los_registros_solapados_se_descartan():
    """Dos escritores pudieron hacer el mismo tramo: el texto saldria repetido."""
    rs = [rec('mic', 0, 30), rec('mic', 20, 50), rec('mic', 30, 60)]
    kept = lt.accepted_records(rs, 'mic')
    assert [(r['start_frame'] // SR, r['end_frame'] // SR) for r in kept] == [(0, 30), (30, 60)]


def test_los_huecos_son_lo_que_no_cubre_nadie():
    kept = [rec('mic', 10, 20), rec('mic', 30, 40)]
    assert lt.gaps(kept, 50 * SR) == [(0, 10 * SR), (20 * SR, 30 * SR), (40 * SR, 50 * SR)]


def test_sin_registros_toda_la_pista_es_hueco():
    assert lt.gaps([], 10 * SR) == [(0, 10 * SR)]


def test_un_proceso_relanzado_sigue_despues_de_lo_ultimo():
    rs = [rec('mic', 0, 30), rec('mic', 60, 90, model='small'), rec('loop', 0, 15)]
    assert lt.resume_frame(rs, 'mic') == 90 * SR
    assert lt.resume_frame(rs, 'loop') == 15 * SR
    assert lt.resume_frame([], 'mic') == 0


# ── El arbitro: nunca dos modelos grandes a la vez ───────────────────────────

@pytest.fixture
def grabando(tray, wav):
    tray._recording_path = wav
    tray._recording_start = time.time()
    tray._rec_mode = 'background'
    return tray


def contestar_al_ceder(wav):
    """El proceso de fondo, en pequeño: cuando le piden ceder, responde a ESA peticion."""
    import threading

    def run():
        t0 = time.monotonic()
        while time.monotonic() - t0 < 5:
            c = lt.read_ctl(wav)
            if c['gate'] == 'yield':
                lt._write_status(wav, state='yielded', yielded_id=c['yield_id'])
                return
            time.sleep(0.01)
    hilo = threading.Thread(target=run, daemon=True)
    hilo.start()
    return hilo


def test_al_usar_whisper_el_proceso_de_fondo_cede_el_modelo(grabando, wav, monkeypatch):
    proc = Proc()
    grabando._live[wav.stem] = (proc, wav)
    muertos = []
    monkeypatch.setattr(lt, 'kill', lambda *a: muertos.append(a))
    contestar_al_ceder(wav)
    t0 = time.monotonic()
    grabando._claim_whisper()
    assert time.monotonic() - t0 < 5
    assert lt.read_ctl(wav)['gate'] == 'yield'
    assert grabando._whisper_busy and muertos == []
    assert wav.stem in grabando._live


def test_un_yielded_de_una_peticion_anterior_no_vale(wav):
    """Revision del 19/10: al volver a 'free' el proceso recarga el modelo y su
    estado sigue diciendo 'yielded' hasta un minuto. Fiarse de eso era cargar dos
    'medium' a la vez."""
    lt._write_status(wav, state='yielded', yielded_id=1)
    assert lt.wait_yielded(wav, Proc(), timeout=0.3, yield_id=2) is False
    lt._write_status(wav, state='yielded', yielded_id=2)
    assert lt.wait_yielded(wav, Proc(), timeout=0.3, yield_id=2) is True


@pytest.mark.parametrize('estado, vale', [
    ('waiting_memory', False),   # puede ponerse a cargar en cualquier momento
    ('loading', False),
    ('running', False),
    ('exited', True),
    ('failed', True),
])
def test_que_estados_dejan_usar_whisper(wav, estado, vale):
    lt._write_status(wav, state=estado)
    assert lt.wait_yielded(wav, Proc(), timeout=0.2, yield_id=1) is vale


def test_si_pasa_a_en_vivo_mientras_se_espera_no_se_mata(grabando, wav, monkeypatch, live_spawns):
    """Revision del 19/10: en vivo el proceso ya no cede (el pipeline se pausa).
    Esperar los 60 s y matarlo dejaba sin en vivo al usuario que lo acababa de pedir."""
    import threading
    grabando._live[wav.stem] = (Proc(), wav)
    muertos = []
    monkeypatch.setattr(lt, 'kill', lambda *a: muertos.append(a))
    threading.Timer(0.3, lambda: grabando.switch_to_live(wav.stem)).start()
    t0 = time.monotonic()
    grabando._claim_whisper()
    assert time.monotonic() - t0 < 5
    assert muertos == [] and wav.stem in grabando._live


def test_el_control_se_escribe_siempre_entero(grabando, wav):
    """Revision del 19/10: cada hilo escribia su campo y podian quedar un modo y
    un permiso que ya no eran los de nadie."""
    grabando._claim_whisper()
    grabando._rec_mode = 'live'
    grabando._write_live_ctl(wav)
    assert lt.read_ctl(wav) == {'mode': 'live', 'gate': 'yield', 'yield_id': grabando._yield_seq}


def test_si_no_se_puede_escribir_el_control_no_se_activa_el_en_vivo(grabando, wav, monkeypatch):
    """Si el proceso no se entera, el pipeline se pausaria esperando un en vivo que no llega."""
    monkeypatch.setattr(lt, 'write_ctl', lambda *_a, **_k: False)
    assert grabando.switch_to_live(wav.stem) is False
    assert grabando._rec_mode == 'background'


def test_pasar_a_en_vivo_y_pedir_el_modelo_no_se_cruzan(grabando, wav, monkeypatch):
    """Segunda revision del 19/10: mientras "Activar en vivo" intentaba escribir el
    control (y luego fallaba y lo deshacia), el pipeline veia 'live', no pedia
    ceder y cargaba 'medium' junto al del proceso de fondo."""
    import threading
    dentro, soltar, escritos = threading.Event(), threading.Event(), []

    def write_ctl(_wav, **campos):
        if campos.get('mode') == 'live':
            dentro.set()
            soltar.wait(5)
            return False                       # la escritura del en vivo falla
        escritos.append(campos)
        return True

    monkeypatch.setattr(lt, 'write_ctl', write_ctl)
    cambio = threading.Thread(target=grabando.switch_to_live, args=(wav.stem,), daemon=True)
    cambio.start()
    assert dentro.wait(5)
    pide = threading.Thread(target=grabando._claim_whisper, daemon=True)
    pide.start()
    pide.join(0.3)
    assert pide.is_alive()                     # espera a que el cambio termine
    soltar.set()
    cambio.join(5)
    pide.join(5)
    assert grabando._rec_mode == 'background'
    assert escritos and escritos[-1]['mode'] == 'background' and escritos[-1]['gate'] == 'yield'


def test_si_la_grabacion_para_se_sigue_esperando_a_que_ceda(grabando, wav, monkeypatch):
    """Segunda revision del 19/10: que la grabacion pare no quita el modelo al
    proceso (lo mata otro hilo, y puede tardar): hay que esperar a que muera."""
    import threading
    proc = Proc()
    grabando._live[wav.stem] = (proc, wav)
    muertos = []
    monkeypatch.setattr(lt, 'kill', lambda *a: muertos.append(a))
    threading.Timer(0.2, lambda: setattr(grabando, '_recording_path', None)).start()
    threading.Timer(0.8, lambda: setattr(proc, 'alive', False)).start()
    t0 = time.monotonic()
    grabando._claim_whisper()
    assert time.monotonic() - t0 >= 0.7
    assert muertos == []


def test_si_no_cede_a_tiempo_se_para(grabando, wav, monkeypatch):
    proc = Proc()
    grabando._live[wav.stem] = (proc, wav)
    muertos = []
    monkeypatch.setattr(lt, 'wait_yielded', lambda *_a, **_k: False)
    monkeypatch.setattr(lt, 'kill', lambda p, path: muertos.append(p))
    grabando._claim_whisper()
    assert muertos == [proc] and wav.stem not in grabando._live


def test_en_modo_en_vivo_no_se_le_quita_el_modelo(grabando, wav):
    grabando._rec_mode = 'live'
    lt.write_ctl(wav, mode='live', gate='free')
    grabando._claim_whisper()
    assert lt.read_ctl(wav)['gate'] == 'free'


def test_el_modelo_vuelve_solo_con_la_cola_vacia(grabando, wav):
    """La reunion mas antigua termina primero."""
    grabando._claim_whisper()
    grabando._pipeline_queue.put(ta.RECORDINGS_DIR / 'otra.wav')
    grabando._release_whisper()
    assert grabando._whisper_busy and lt.read_ctl(wav)['gate'] == 'yield'
    grabando._pipeline_queue.get()
    grabando._release_whisper()
    assert not grabando._whisper_busy and lt.read_ctl(wav)['gate'] == 'free'


def test_una_grabacion_que_empieza_con_el_pipeline_ocupado_arranca_cediendo(tray, wav, live_spawns):
    tray._whisper_busy = True
    tray.set_recording(True, wav)
    assert lt.read_ctl(wav) == {'mode': 'background', 'gate': 'yield', 'yield_id': 0}
    tray.set_recording(False)


def test_el_pipeline_suelta_whisper_aunque_el_trabajo_falle(tray, wav, monkeypatch):
    import threading
    hechos = []

    def boom(_w):
        tray._whisper_busy = True
        hechos.append(1)
        raise RuntimeError('fallo')

    monkeypatch.setattr(tray, '_run_pipeline_sync', boom)
    monkeypatch.setattr(tray, 'set_processing', lambda *_a: None)
    hilo = threading.Thread(target=tray._pipeline_loop, daemon=True)
    hilo.start()
    tray._pipeline_queue.put(wav)
    esperar(hechos)
    t0 = time.monotonic()
    while tray._whisper_busy and time.monotonic() - t0 < 2:
        time.sleep(0.01)
    assert tray._whisper_busy is False
    tray._pipeline_queue.put(None)
    hilo.join(2)


# ── Prioridad del modo en vivo ───────────────────────────────────────────────

def test_en_modo_de_fondo_no_se_pausa_nada(grabando):
    assert grabando._live_priority() is False


def test_en_vivo_se_pausa_desde_que_se_elige(grabando, wav):
    """Aunque el proceso tarde 20 s en lanzarse."""
    grabando._rec_mode = 'live'
    grabando._live_spawning.add(wav.stem)
    assert grabando._live_priority() is True


@pytest.mark.parametrize('estado, vivo, pausa', [
    ('running', True, True),
    ('failed', True, False),
    ('waiting_memory', True, False),      # si no, los dos esperarian para siempre
    # Recien pasado a en vivo, el proceso tarda unos segundos en enterarse: si no
    # se pausara ahi, el pipeline cargaria 'medium' justo en esa ventana.
    ('yielded', True, True),
    ('loading', True, True),
    ('running', False, False),
])
def test_en_vivo_la_pausa_depende_del_proceso(grabando, wav, estado, vivo, pausa):
    grabando._rec_mode = 'live'
    grabando._live[wav.stem] = (Proc(vivo), wav)
    lt._write_status(wav, state=estado)
    assert grabando._live_priority() is pausa


def test_el_pipeline_pasa_la_pausa_al_transcriptor(tray, wav, wav_factory, monkeypatch):
    wav_factory(wav, seconds=0.5)
    vistos = {}

    def fake_transcribe(*_a, **kw):
        vistos.update(kw)
        return '[00:00] hola', 'es'

    monkeypatch.setattr(transcriber, 'transcribe', fake_transcribe)
    monkeypatch.setattr(tray, 'set_processing', lambda *_a: None)
    monkeypatch.setattr(tray, '_register_part', lambda *_a: None)
    tray._run_pipeline_sync(wav)
    # La del modo en vivo va dentro (ver test_pipeline_yields: _pipeline_should_pause).
    assert vistos['should_pause'] == tray._pipeline_should_pause


class UnaLinea:
    def transcribe(self, audio, **_k):
        return iter([FakeSegment(0.0, 1.0, 'hola')]), FakeInfo('es', 1.0)


def test_en_pausa_el_pipeline_no_carga_el_modelo(tr_dirs, wav_factory, monkeypatch, sin_dormir):
    """Revision del 19/10: cargaba 'medium' y LUEGO se pausaba, y se quedaba
    cargado junto al 'small' del en vivo toda la reunion."""
    audio = ta.RECORDINGS_DIR / 'previa.wav'
    wav_factory(audio, seconds=1)
    pausas = iter([True, True, False])
    estado = {'pausa': True}

    def should_pause():
        estado['pausa'] = next(pausas, False)
        return estado['pausa']

    cargas = []

    def get_model(_name=None):
        cargas.append(estado['pausa'])
        return UnaLinea()

    monkeypatch.setattr(transcriber, '_get_model', get_model)
    text, _lang = transcriber._transcribe_local(audio, should_pause=should_pause)
    assert 'hola' in text
    assert cargas == [False]


# ── Pausa cooperativa en el transcriptor ─────────────────────────────────────

@pytest.fixture
def sin_dormir(monkeypatch):
    monkeypatch.setattr(transcriber, 'time', types.SimpleNamespace(sleep=lambda _s: None))


def test_paced_espera_mientras_haya_pausa(sin_dormir):
    estados = iter([True, True, False])
    preguntas = []

    def pausa():
        v = next(estados, False)
        preguntas.append(v)
        return v

    assert list(transcriber._paced(iter(['a', 'b']), should_pause=pausa)) == ['a', 'b']
    assert preguntas[:3] == [True, True, False]


def test_paced_cancela_aunque_este_en_pausa(sin_dormir):
    with pytest.raises(transcriber.TranscriptionCancelled):
        list(transcriber._paced(iter(['a']), should_pause=lambda: True, should_cancel=lambda: True))


def test_paced_sin_pausa_no_cambia_nada():
    assert list(transcriber._paced(iter([1, 2, 3]))) == [1, 2, 3]


class DosPistas:
    def transcribe(self, audio, **_k):
        segs = [(0.0, 5.0, 'uno'), (5.0, 10.0, 'dos')]
        return (FakeSegment(a, b, t) for a, b, t in segs), FakeInfo('es', 10.0)


def test_con_dos_pistas_hay_progreso_y_cancelacion(tr_dirs, wav_factory):
    """Sin progreso, la reunion del 19/10 paso 84 min en 'Transcribiendo 0%'."""
    base = ta.RECORDINGS_DIR / 'dos.wav'
    wav_factory(base.with_suffix('.mic.wav'), seconds=10)
    wav_factory(base.with_suffix('.loop.wav'), seconds=10)
    pcts = []
    transcriber._transcribe_with_speakers(DosPistas(), base, on_progress=pcts.append)
    assert pcts == sorted(pcts) and pcts[-1] == 100 and 0 < pcts[0] < 100

    wav_factory(base.with_suffix('.mic.wav'), seconds=10)
    wav_factory(base.with_suffix('.loop.wav'), seconds=10)
    with pytest.raises(transcriber.TranscriptionCancelled):
        transcriber._transcribe_with_speakers(DosPistas(), base, should_cancel=lambda: True)


# ── Cambiar a en vivo ────────────────────────────────────────────────────────

def test_activar_en_vivo_cambia_el_modo_y_el_control(grabando, wav, live_spawns):
    assert grabando.switch_to_live(wav.stem) is True
    assert grabando._rec_mode == 'live'
    assert lt.read_ctl(wav)['mode'] == 'live'


def test_un_stem_equivocado_no_cambia_nada(grabando, wav):
    assert grabando.switch_to_live('otra_reunion') is False
    assert grabando._rec_mode == 'background'


def test_sin_grabacion_no_hay_nada_que_activar(tray):
    assert tray.switch_to_live() is False


def test_el_estado_dice_el_modo_y_el_stem_de_la_grabacion(grabando, tr_dirs):
    grabando._write_status()
    job = json.loads((tr_dirs / '.pipeline_status.json').read_text(encoding='utf-8'))['jobs'][0]
    assert job['mode'] == 'background' and job['rec_stem'] == STEM
    assert 'stem' not in job


def test_el_estado_dice_si_el_proceso_de_fondo_espera_memoria(grabando, wav, tr_dirs):
    """23/10/2026: el proceso de fondo espero memoria toda una llamada de 30 min
    y el panel no lo decia en ningun sitio."""
    grabando._live[wav.stem] = (Proc(), wav)
    lt._write_status(wav, state='waiting_memory', free_mb=3152, need_mb=6024)
    grabando._write_status()
    job = json.loads((tr_dirs / '.pipeline_status.json').read_text(encoding='utf-8'))['jobs'][0]
    assert job['live_state'] == 'waiting_memory'


def test_reconexion_sigue_en_el_modo_de_la_parte_anterior(tray, wav, live_spawns):
    tray.set_recording(True, wav, mode='live')
    tray.set_recording(False)
    assert tray.last_recording_mode == 'live'


# ── La CLI y el panel ────────────────────────────────────────────────────────

class FakeTray:
    def __init__(self):
        self.calls = []

    def switch_to_live(self, stem):
        self.calls.append(('live', stem))
        return True

    def set_recording(self, active, path=None, mode=None):
        self.calls.append(('rec', active, mode))

    def _on_recording_done(self, _p):
        pass


class FakeRecorder:
    is_recording = False

    def start(self, _p):
        self.is_recording = True


@pytest.mark.parametrize('cmd, esperado', [
    ('live 2026-10-19_09-00_García', ('live', '2026-10-19_09-00_García')),
    ('live', ('live', None)),
    ('start-live', ('rec', True, 'live')),
    ('start', ('rec', True, 'background')),
])
def test_comandos_de_la_cli(cmd, esperado, tr_dirs):
    import main
    t = FakeTray()
    main._handle_cli_command(cmd, FakeRecorder(), t, lambda n: tr_dirs / 'recordings' / f'{n}.wav')
    assert t.calls == [esperado]


def test_el_panel_manda_el_cambio_por_la_cli(tr_dirs):
    import app_window
    import config
    assert app_window.AppAPI().switch_live('2026-10-19_09-00_García') is True
    assert config.CLI_CONTROL_FILE.read_text(encoding='utf-8') == 'live 2026-10-19_09-00_García'


@pytest.mark.parametrize('stem', ['', '../x', 'a b'])
def test_el_panel_rechaza_stems_raros(tr_dirs, stem):
    import app_window
    assert app_window.AppAPI().switch_live(stem) is False


# ── La ventana de inicio ─────────────────────────────────────────────────────

@pytest.fixture
def sin_tk(monkeypatch):
    import popup
    monkeypatch.setattr(popup, 'run_in_tk', lambda _fn: None)
    return popup


def esperar(lista, n=1, timeout=2.0):
    t0 = time.monotonic()
    while len(lista) < n and time.monotonic() - t0 < timeout:
        time.sleep(0.01)
    return lista


def test_el_tercer_boton_graba_en_vivo(sin_tk):
    llamadas = []
    p = sin_tk.RecordingPopup(on_yes=lambda: llamadas.append('si'), on_no=lambda: llamadas.append('no'),
                              on_alt=lambda: llamadas.append('vivo'))
    p._alt()
    p._yes()
    assert esperar(llamadas) == ['vivo']


def test_sin_respuesta_no_se_graba(sin_tk):
    """Decision de Felipe (19/10): sin respuesta en 30 s, no se graba."""
    llamadas = []
    p = sin_tk.RecordingPopup(on_yes=lambda: llamadas.append('si'), on_no=lambda: llamadas.append('no'),
                              on_alt=lambda: llamadas.append('vivo'))
    p._top = types.SimpleNamespace(after=lambda *_a: None)
    p._remaining = 1
    p._tick()
    assert esperar(llamadas) == ['no']


# ── El proceso de la reunion (lt._run) con un modelo falso ───────────────────

@pytest.fixture
def proceso(wav, monkeypatch):
    """lt._run en un hilo: modelo y VAD falsos, sin dormir de verdad. El VAD no
    oye voz, asi que cada trozo se compromete vacio sin llamar al modelo."""
    import sys
    import threading

    import numpy as np
    import soundfile as sf
    real_sleep = time.sleep
    cargando, soltar = threading.Event(), threading.Event()
    soltar.set()

    class Modelo:
        def __init__(self, name, **_k):
            cargando.set()
            soltar.wait(5)
            self.name = name

        def transcribe(self, audio, **_k):
            return iter([]), FakeInfo('es', len(audio) / SR)

    fw = types.ModuleType('faster_whisper')
    fw.WhisperModel = Modelo
    vad = types.ModuleType('faster_whisper.vad')
    vad.VadOptions = lambda **k: k
    vad.get_speech_timestamps = lambda _audio, _opts: []
    monkeypatch.setitem(sys.modules, 'faster_whisper', fw)
    monkeypatch.setitem(sys.modules, 'faster_whisper.vad', vad)
    monkeypatch.setattr(lt, '_start_watchdog', lambda *_a: None)
    monkeypatch.setattr(lt, '_memory_ok', lambda _n: True)
    monkeypatch.setattr(lt, '_memory_floor_ok', lambda: True)
    # Ni del disco de la maquina: con menos de ~6 GB libres esperaria disco.
    monkeypatch.setattr(lt, '_disk_ok', lambda _n: True)
    monkeypatch.setattr(lt, '_disk_floor_ok', lambda: True)
    prioridades, eficiencia = [], []
    monkeypatch.setattr(lt, '_set_priority', prioridades.append)
    monkeypatch.setattr(lt, '_opt_out_of_efficiency_mode', lambda: eficiencia.append(1) or True)
    monkeypatch.setattr(lt, 'time', types.SimpleNamespace(
        sleep=lambda _s: real_sleep(0.01), monotonic=time.monotonic, perf_counter=time.perf_counter))
    monkeypatch.setattr(transcriber, 'WHISPER_LANGUAGE', 'es')

    def partes(segundos):
        for t in lt.TRACKS:
            sf.write(str(lt.part_path(wav, t)), np.zeros(int(segundos * SR), dtype=np.float32), SR,
                     format='WAV', subtype='FLOAT')

    hilos = []

    def arrancar(from_now=False):
        hilo = threading.Thread(target=lt._run, args=(wav, 0, 0.0, from_now), daemon=True)
        hilo.start()
        hilos.append(hilo)
        return hilo

    def hasta(cond, timeout=5.0):
        t0 = time.monotonic()
        while not cond() and time.monotonic() - t0 < timeout:
            real_sleep(0.01)
        return cond()

    yield types.SimpleNamespace(partes=partes, arrancar=arrancar, hasta=hasta,
                                cargando=cargando, soltar=soltar,
                                prioridades=prioridades, eficiencia=eficiencia)
    soltar.set()
    wav.write_bytes(b'')          # la grabacion paro: el proceso sale
    for hilo in hilos:
        hilo.join(5)


def primer_registro(wav, track='mic'):
    rs = [r for r in lt.read_records(wav) if r['track'] == track]
    return rs[0] if rs else None


def test_mientras_carga_el_modelo_el_estado_dice_loading(proceso, wav):
    """Revision del 19/10: decia 'yielded' hasta terminar de cargar, y el pipeline
    podia cargar el suyo a la vez."""
    proceso.partes(5)
    proceso.soltar.clear()
    lt.write_ctl(wav, mode='background', gate='free')
    lt._write_status(wav, state='yielded', yielded_id=1)
    proceso.arrancar()
    assert proceso.cargando.wait(5)
    assert lt.read_status(wav)['state'] == 'loading'
    proceso.soltar.set()
    assert proceso.hasta(lambda: lt.read_status(wav).get('state') == 'running')


def test_al_ceder_contesta_a_la_peticion_en_curso(proceso, wav):
    proceso.partes(5)
    lt.write_ctl(wav, mode='background', gate='yield', yield_id=7)
    proceso.arrancar()
    assert proceso.hasta(lambda: lt.read_status(wav).get('yielded_id') == 7)
    assert lt.read_status(wav)['state'] == 'yielded'
    assert not proceso.cargando.is_set()


def test_cediendo_sale_si_la_grabacion_se_descarta(proceso, wav):
    """Revision del 19/10: las ramas que esperan se saltaban las salidas. Un
    proceso cedido cuya grabacion se descartaba no se iba nunca."""
    proceso.partes(5)
    lt.write_ctl(wav, mode='background', gate='yield', yield_id=1)
    hilo = proceso.arrancar()
    assert proceso.hasta(lambda: lt.read_status(wav).get('state') == 'yielded')
    for t in lt.TRACKS:
        borrar_aunque_se_este_leyendo(lt.part_path(wav, t))
    hilo.join(5)
    assert not hilo.is_alive()


def borrar_aunque_se_este_leyendo(path, intentos=50):
    """En Windows, borrar un fichero mientras el proceso lo tiene abierto para
    leer (read_part: abrir, leer, cerrar) da WinError 32 en ese instante. Como
    el grabador de verdad, se reintenta."""
    for _ in range(intentos):
        try:
            path.unlink()
            return
        except PermissionError:
            time.sleep(0.02)
    path.unlink()


def test_lanzado_en_vivo_a_mitad_de_reunion_empieza_en_ahora(proceso, wav):
    """Revision del 19/10: sin proceso de fondo, "Activar en vivo" en el minuto 40
    empezaba por el principio, 'small' con 40 min de atraso."""
    proceso.partes(100)
    lt.write_ctl(wav, mode='live', gate='free')
    proceso.arrancar(from_now=True)
    assert proceso.hasta(lambda: primer_registro(wav) is not None)
    assert primer_registro(wav)['start_frame'] == (100 - lt.LIVE_JUMP_BACK_S) * SR


def test_grabar_en_vivo_en_una_maquina_lenta_no_se_salta_el_principio(proceso, wav):
    """Segunda revision del 19/10: deducirlo del retraso hacia que un arranque
    lento (minuto y pico) saltara el principio. Lo decide el daemon."""
    proceso.partes(100)
    lt.write_ctl(wav, mode='live', gate='free')
    proceso.arrancar()
    assert proceso.hasta(lambda: primer_registro(wav) is not None)
    assert primer_registro(wav)['start_frame'] == 0


def test_al_pasar_de_fondo_a_en_vivo_salta_a_ahora_con_small(proceso, wav):
    proceso.partes(100)
    lt.write_ctl(wav, mode='background', gate='yield', yield_id=1)
    proceso.arrancar()
    assert proceso.hasta(lambda: lt.read_status(wav).get('state') == 'yielded')
    lt.write_ctl(wav, mode='live')
    assert proceso.hasta(lambda: primer_registro(wav) is not None)
    r = primer_registro(wav)
    assert r['model'] == 'small' and r['start_frame'] == (100 - lt.LIVE_JUMP_BACK_S) * SR


def test_en_vivo_trabaja_con_prioridad_normal_y_fuera_del_modo_eficiencia(proceso, wav):
    """Reunion real del 20/10/2026: con prioridad 'below' y sin ventana, Windows
    le daba 0,7 nucleos para 4 hilos; 'small' iba a 1-3 veces tiempo real y el
    transcript en vivo llego a 4 min de retraso. Con prioridad normal: 2,2
    nucleos y 0,3-0,5 veces tiempo real."""
    proceso.partes(40)
    lt.write_ctl(wav, mode='live', gate='free')
    proceso.arrancar()
    assert proceso.hasta(lambda: primer_registro(wav) is not None)
    assert proceso.prioridades[-1] == 'normal' and 'below' not in proceso.prioridades
    assert proceso.eficiencia == [1]


def test_sin_memoria_lo_dice_en_el_log_y_en_el_estado(proceso, wav, monkeypatch, caplog):
    """23/10/2026: esperaba en silencio una llamada entera. Ahora lo dice, con
    cuanta reserva hay y cuanta falta."""
    import logging
    caplog.set_level(logging.INFO, logger='live_transcriber')
    monkeypatch.setattr(lt, '_memory_ok', lambda _n: False)
    monkeypatch.setattr(lt, '_mem_status', lambda: (6216.0, 3152.0))
    proceso.partes(5)
    lt.write_ctl(wav, mode='background', gate='free')
    proceso.arrancar()
    assert proceso.hasta(lambda: lt.read_status(wav).get('state') == 'waiting_memory')
    st = lt.read_status(wav)
    assert st['free_mb'] == 3152 and st['need_mb'] == lt._MODEL_COMMIT_MB['medium'] + lt.COMMIT_MARGIN_MB
    assert proceso.hasta(lambda: 'sin memoria para cargar' in caplog.text)
    assert not proceso.cargando.is_set()


def test_el_margen_de_memoria_es_de_1_gb():
    """Decision del 23/10/2026: con 2 GB, en el portatil de Felipe el modo de
    fondo no cargaba 'medium' en toda una llamada."""
    assert lt.COMMIT_MARGIN_MB == 1024
    assert lt.COMMIT_FLOOR_MB <= lt.COMMIT_MARGIN_MB


def test_de_fondo_sigue_cediendo_con_la_prioridad_minima():
    assert lt._work_priority(lt.MODE_BACKGROUND) == 'idle'
    assert lt._work_priority(lt.MODE_LIVE) == 'normal'


def test_salir_del_modo_eficiencia_usa_la_api_documentada(monkeypatch):
    """SetProcessInformation(ProcessPowerThrottling=4) con version 1,
    ControlMask=EXECUTION_SPEED (1) y StateMask=0: "no me frenes"."""
    import ctypes
    if not hasattr(ctypes, 'windll'):
        pytest.skip('solo Windows')
    vistos = []

    class K32:
        def SetProcessInformation(self, handle, clase, ref, size):
            st = ref._obj
            # El handle tiene que viajar como HANDLE de 64 bits: un int de
            # Python se pasaba truncado a 32 y Windows daba ERROR_INVALID_HANDLE.
            vistos.append((isinstance(handle, ctypes.c_void_p), clase, st.Version, st.ControlMask,
                           st.StateMask, size))
            return 1

    import win_power
    monkeypatch.setattr(win_power, '_done', False)
    monkeypatch.setattr(ctypes, 'windll', types.SimpleNamespace(kernel32=K32()))
    assert lt._opt_out_of_efficiency_mode() is True
    assert vistos == [(True, 4, 1, 1, 0, 12)]
    assert lt._opt_out_of_efficiency_mode() is True and len(vistos) == 1, "una vez por proceso"


def test_salir_del_modo_eficiencia_funciona_con_el_windows_de_verdad(monkeypatch):
    """23/10/2026: el arreglo del 20/10 nunca funciono en produccion. El log
    decia "No se pudo sacar el proceso del modo de eficiencia" (GetLastError 6,
    ERROR_INVALID_HANDLE): GetCurrentProcess devolvia un handle que ctypes
    recortaba a 32 bits. El test anterior, con la API simulada, no podia verlo.
    Este llama a la de verdad (sobre el propio proceso de pytest: inocuo)."""
    import ctypes
    if not hasattr(ctypes, 'windll'):
        pytest.skip('solo Windows')
    import win_power
    monkeypatch.setattr(win_power, '_done', False)
    assert win_power.opt_out_of_efficiency_mode() is True, f"GetLastError {ctypes.GetLastError()}"


def test_en_vivo_a_mitad_se_lanza_ya_y_desde_ahora(wav, monkeypatch):
    from conftest import REAL_LIVE_SPAWN
    monkeypatch.setattr(lt, 'START_DELAY_S', 30)
    cmds = []
    monkeypatch.setattr(lt.subprocess, 'Popen',
                        lambda cmd, **_k: cmds.append(cmd) or types.SimpleNamespace(pid=1))
    t0 = time.monotonic()
    REAL_LIVE_SPAWN(wav, mode='live', mid_meeting=True)
    assert time.monotonic() - t0 < 5
    assert lt.FROM_NOW_ARG in cmds[0]

def test_el_proceso_usa_las_pistas_del_glosario(proceso, wav, monkeypatch):
    """Si _run no las cargara (_load_hints), ningun trozo hecho durante la
    reunion llevaria las pistas, y los de medium son los que se guardan."""
    vistos = []
    real_step = lt._step

    def step(track, model, lang, args, *a, **k):
        vistos.append(args.get('hotwords'))
        return real_step(track, model, lang, args, *a, **k)

    monkeypatch.setattr(lt, '_step', step)
    proceso.partes(5)
    lt.write_ctl(wav, mode='background', gate='free')
    lt.write_hints(wav, 'Kinaxis')
    proceso.arrancar()
    assert proceso.hasta(lambda: 'Kinaxis' in vistos)


# ── Idioma inicial del modo de fondo ──────────────────────────────────────────
# 18/11/2026: el micro tenia 37 s de voz y Whisper dijo 'uk' con 0,66. El remapeo lo
# convirtio en 'en', se fijo sin mirar la confianza y el castellano del micro salio
# traducido al ingles durante 7 minutos; las minutas salieron en ingles. La pista de
# Teams, con 141 s de voz, daba 'es' con mas de 0,9.

@pytest.fixture
def idioma(proceso, wav, monkeypatch):
    """_run en modo de fondo SIN idioma fijado: la voz la oye el VAD falso y el modelo
    contesta lo que diga el guion (idioma, confianza) a cada detect_language."""
    import sys

    import numpy as np
    import soundfile as sf
    monkeypatch.setattr(transcriber, 'WHISPER_LANGUAGE', None)
    sys.modules['faster_whisper.vad'].get_speech_timestamps = (
        lambda audio, _o: [{'start': 0, 'end': len(audio)}])
    guion, miradas = [], []

    def detect(self, audio, **_k):
        return guion.pop(0) + ({},) if guion else ('en', 0.0, {})

    sys.modules['faster_whisper'].WhisperModel.detect_language = detect
    real_read = lt.read_part

    def espia(path, start, max_frames):
        if max_frames == 150 * lt.SR:          # la ventana de la deteccion, no _step
            miradas.append((path.name.split('.')[-2], start // lt.SR))
        return real_read(path, start, max_frames)

    monkeypatch.setattr(lt, 'read_part', espia)

    def partes(segundos):
        rng = np.random.default_rng(1)
        for t in lt.TRACKS:
            sf.write(str(lt.part_path(wav, t)),
                     (rng.standard_normal(int(segundos * lt.SR)) * 0.1).astype('float32'),
                     lt.SR, format='WAV', subtype='FLOAT')

    lt.write_ctl(wav, mode='background', gate='free')
    return types.SimpleNamespace(guion=guion, miradas=miradas, partes=partes)


def test_se_fija_el_idioma_del_tramo_mas_seguro(idioma, proceso, wav):
    """El micro dice 'uk' 0,66 y Teams 'es' 0,93: gana Teams. Sin el arreglo se miraba
    solo el micro y se fijaba 'en' (uk remapeado)."""
    idioma.guion += [('uk', 0.66), ('es', 0.93)]
    idioma.partes(200)
    proceso.arrancar()
    assert proceso.hasta(lambda: lt.read_status(wav).get('lang') is not None)
    assert lt.read_status(wav)['lang'] == 'es'


def test_con_poca_confianza_no_se_fija_y_se_mira_mas_adelante(idioma, proceso, wav):
    """Si ninguno de los dos llega a 0,8, no se impone un idioma dudoso: se vuelve a
    mirar con el audio de 150 s mas adelante, donde si hay confianza."""
    idioma.guion += [('uk', 0.66), ('en', 0.5),          # primera mirada: nada seguro
                     ('es', 0.91), ('es', 0.6)]          # segunda, 150 s despues
    idioma.partes(400)
    proceso.arrancar()
    assert proceso.hasta(lambda: lt.read_status(wav).get('lang') is not None)
    assert lt.read_status(wav)['lang'] == 'es'
    assert ('mic', 0) in idioma.miradas and ('mic', 150) in idioma.miradas
    assert idioma.miradas.index(('mic', 0)) < idioma.miradas.index(('mic', 150))


def test_detect_language_sin_voz_suficiente_no_decide(wav, monkeypatch):
    import numpy as np
    import soundfile as sf
    for t in lt.TRACKS:
        sf.write(str(lt.part_path(wav, t)), np.zeros(10 * lt.SR, dtype=np.float32), lt.SR,
                 format='WAV', subtype='FLOAT')
    modelo = types.SimpleNamespace(detect_language=lambda *a, **k: pytest.fail('sin voz no se detecta'))
    assert lt._detect_language(modelo, wav, lambda _a, _o: [], None) == (None, 0.0)


def test_detect_language_remapea_solo_el_idioma_elegido(wav):
    """'uk' pasa a 'en' por el remapeo de siempre, pero con su confianza real: es el
    llamador quien decide si basta para fijarlo (ver _settle_language)."""
    import numpy as np
    import soundfile as sf
    for t in lt.TRACKS:
        sf.write(str(lt.part_path(wav, t)), np.ones(40 * lt.SR, dtype=np.float32) * 0.1, lt.SR,
                 format='WAV', subtype='FLOAT')
    respuestas = iter([('uk', 0.66, {}), ('fr', 0.5, {})])
    modelo = types.SimpleNamespace(detect_language=lambda *a, **k: next(respuestas))

    def vad(audio, _o):
        return [{'start': 0, 'end': len(audio)}]

    assert lt._detect_language(modelo, wav, vad, None) == ('en', 0.66)


@pytest.mark.parametrize('found, prob, start, esperado', [
    (None, 0.0, 0.0, (None, 0.0)),                          # aun sin voz bastante
    ('es', 0.93, 0.0, ('es', 0.0)),                         # seguro: se fija
    ('es', lt.LANG_LOCK_PROB, 150.0, ('es', 150.0)),        # justo en el umbral: se fija
    ('en', 0.66, 0.0, (None, lt.LANG_RETRY_STEP_S)),        # dudoso: no, y se salta
    ('en', 0.5, 150.0, (None, 150.0 + lt.LANG_RETRY_STEP_S)),
])
def test_settle_language(found, prob, start, esperado):
    assert lt._settle_language(found, prob, start) == esperado
