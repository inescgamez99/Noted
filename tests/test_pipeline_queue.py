"""La cola de trabajos, el movimiento a processed/ y las notificaciones.

Es la parte del pipeline que decide si una grabacion se procesa, se reprocesa
o se queda olvidada en disco. Los dos defectos que fijan estos tests salieron
al escribirlos y estan documentados como tal: no se arreglan aqui, se fijan
para poder arreglarlos con red.
"""
import queue

import pytest

import tray_app as ta

pytestmark = pytest.mark.unit

STEM = '2026-09-17_16-16_manual'


@pytest.fixture
def espia_notificaciones(monkeypatch):
    """_notify solo llama a pystray; aqui interesa como espia."""
    avisos = []
    monkeypatch.setattr(ta.TrayApp, '_notify', lambda self, t, m: avisos.append((t, m)))
    return avisos


@pytest.fixture
def worker_parado(monkeypatch):
    """Neutraliza el consumidor para poder inspeccionar la cola con calma."""
    monkeypatch.setattr(ta.TrayApp, '_register_part_recorded', lambda self, p: None)


# ── Encolar una grabacion terminada ───────────────────────────────────────

def test_encolar_mete_el_trabajo_en_las_dos_estructuras(tray, worker_parado, tr_dirs):
    """Hay doble contabilidad a proposito: la Queue es la cola real y
    _pipeline_queued es lo que ve la web."""
    wav = ta.RECORDINGS_DIR / f'{STEM}.wav'
    tray._on_recording_done(wav)

    assert tray._pipeline_queued == [STEM]
    assert tray._pipeline_queue.qsize() == 1


def test_el_orden_de_la_cola_es_fifo(tray, worker_parado, tr_dirs):
    for n in ('a', 'b', 'c'):
        tray._on_recording_done(ta.RECORDINGS_DIR / f'{n}.wav')

    assert tray._pipeline_queued == ['a', 'b', 'c']
    assert [tray._pipeline_queue.get().stem for _ in range(3)] == ['a', 'b', 'c']


def test_una_sola_grabacion_no_dispara_notificacion(tray, worker_parado,
                                                    espia_notificaciones, tr_dirs):
    """Avisar de cada grabacion seria ruido: solo interesa si se acumulan."""
    tray._on_recording_done(ta.RECORDINGS_DIR / f'{STEM}.wav')
    assert espia_notificaciones == []


def test_dos_grabaciones_en_cola_avisan_al_usuario(tray, worker_parado,
                                                   espia_notificaciones, tr_dirs):
    tray._on_recording_done(ta.RECORDINGS_DIR / 'a.wav')
    tray._on_recording_done(ta.RECORDINGS_DIR / 'b.wav')

    assert len(espia_notificaciones) == 1
    assert '2' in espia_notificaciones[0][1]


# ── El consumidor ─────────────────────────────────────────────────────────

def test_el_centinela_none_para_el_worker(tray, monkeypatch):
    """Es como _quit apaga el pipeline. Ojo: el centinela entra al FINAL de la
    cola, asi que no es una parada inmediata."""
    monkeypatch.setattr(ta.TrayApp, '_run_pipeline_sync', lambda self, p: None)
    tray._pipeline_queue.put(None)

    tray._pipeline_loop()          # si no rompiera el bucle, esto colgaria


def test_al_desencolar_el_trabajo_sale_de_la_cola_visible(tray, monkeypatch, tr_dirs):
    procesados = []
    monkeypatch.setattr(ta.TrayApp, '_run_pipeline_sync',
                        lambda self, p: procesados.append(p.stem))

    wav = ta.RECORDINGS_DIR / f'{STEM}.wav'
    tray._pipeline_queued = [STEM]
    tray._pipeline_queue.put(wav)
    tray._pipeline_queue.put(None)

    tray._pipeline_loop()

    assert procesados == [STEM]
    assert tray._pipeline_queued == []


def test_un_trabajo_cancelado_se_descarta_sin_procesarlo(tray, monkeypatch, tr_dirs,
                                                         wav_factory):
    """Cancelar mientras esta en cola: no debe gastar ni un segundo de Whisper."""
    procesados, descartados = [], []
    monkeypatch.setattr(ta.TrayApp, '_run_pipeline_sync',
                        lambda self, p: procesados.append(p))
    monkeypatch.setattr(ta.TrayApp, '_discard_job',
                        lambda self, p, r, m=None: descartados.append((p.stem, r)))

    wav = wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)
    tray._pipeline_queued = [STEM]
    tray.cancel_job(STEM)
    tray._pipeline_queue.put(wav)
    tray._pipeline_queue.put(None)

    tray._pipeline_loop()

    assert procesados == []
    assert descartados == [(STEM, 'descartada en cola')]


def test_un_error_en_un_trabajo_no_tumba_el_worker(tray, monkeypatch, tr_dirs):
    """Si el pipeline muriera con el primer fallo, las grabaciones siguientes
    se quedarian en la cola para siempre."""
    procesados = []

    def a_veces_falla(self, p):
        if p.stem == 'malo':
            raise RuntimeError('fallo simulado')
        procesados.append(p.stem)

    monkeypatch.setattr(ta.TrayApp, '_run_pipeline_sync', a_veces_falla)
    monkeypatch.setattr(ta.TrayApp, 'set_processing', lambda self, m='': None)

    for n in ('malo', 'bueno'):
        tray._pipeline_queue.put(ta.RECORDINGS_DIR / f'{n}.wav')
    tray._pipeline_queue.put(None)

    tray._pipeline_loop()

    assert procesados == ['bueno']


# ── Recuperar trabajos pendientes al arrancar ─────────────────────────────

@pytest.fixture
def sin_espera(monkeypatch):
    """_recover_pending duerme 3 s antes de mirar; en un test no hace falta."""
    monkeypatch.setattr(ta.time, 'sleep', lambda _s: None)


def test_recupera_una_grabacion_sin_minutas(tray, sin_espera, espia_notificaciones,
                                            wav_factory, tr_dirs):
    """El caso del 17/09: el proceso murio a mitad y al relanzarse hay que
    retomar el WAV que se quedo sin procesar."""
    wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)

    tray._recover_pending()

    assert tray._pipeline_queue.qsize() == 1
    # Sin toast: salia en cada reinicio del daemon.
    assert espia_notificaciones == []


def test_no_encola_las_pistas_mic_y_loop_como_si_fueran_grabaciones(tray, sin_espera,
                                                                    espia_notificaciones,
                                                                    wav_factory, tr_dirs):
    """<grab>.mic.wav y <grab>.loop.wav son pistas de diarizacion: encolarlas"""
    """triplicaba el trabajo y generaba minutas de una sola pista."""
    for suf in ('.wav', '.mic.wav', '.loop.wav'):
        wav_factory(ta.RECORDINGS_DIR / f'{STEM}{suf}', seconds=0.1)

    tray._recover_pending()

    assert tray._pipeline_queue.qsize() == 1


def test_no_recupera_una_grabacion_que_ya_tiene_minutas(tray, sin_espera,
                                                        espia_notificaciones,
                                                        wav_factory, tr_dirs):
    """Reprocesar una reunion ya hecha gastaria Whisper y Claude para nada, y
    sobreescribiria minutas que el usuario puede haber editado."""
    wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)
    (ta.MINUTES_DIR / '20260917_1616_Ya_Procesada.md').write_text('x', encoding='utf-8')

    tray._recover_pending()

    assert tray._pipeline_queue.qsize() == 0
    assert espia_notificaciones == []


def test_sin_grabaciones_pendientes_no_avisa(tray, sin_espera, espia_notificaciones,
                                             tr_dirs):
    tray._recover_pending()
    assert espia_notificaciones == []


def test_DEFECTO_los_trabajos_recuperados_no_entran_en_la_cola_visible(
        tray, sin_espera, espia_notificaciones, wav_factory, tr_dirs):
    """DEFECTO CONOCIDO (tray_app.py:778): _recover_pending encola en la Queue
    pero NO en _pipeline_queued. Consecuencias: no aparecen como 'queued' en el
    panel y _active_job_stem() devuelve '', asi que el boton de descartar no se
    pinta y el item del menu queda oculto. Se fija el comportamiento actual
    para poder arreglarlo con red."""
    wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)

    tray._recover_pending()

    assert tray._pipeline_queue.qsize() == 1
    assert tray._pipeline_queued == [], "si esto cambia, el defecto se arreglo"
    assert tray._active_job_stem() == '', "y entonces ya se puede descartar"


# ── Mover a processed/ ────────────────────────────────────────────────────

@pytest.fixture
def grabacion_procesada(tr_dirs, wav_factory):
    wav = wav_factory(ta.RECORDINGS_DIR / f'{STEM}.wav', seconds=0.1)
    transcript = ta.RECORDINGS_DIR / f'{STEM}_transcript.txt'
    transcript.write_text('texto', encoding='utf-8')
    for suf in ('.lang', '.partial', '.context'):
        wav.with_suffix(suf).write_text('x', encoding='utf-8')
    return wav, transcript


def test_el_wav_se_mueve_a_processed(tray, grabacion_procesada):
    wav, transcript = grabacion_procesada
    tray._move_to_processed(wav, transcript)

    assert not wav.exists()
    assert (ta.RECORDINGS_DIR / 'processed' / wav.name).exists()


def test_el_transcript_se_borra_porque_ya_esta_junto_a_las_minutas(tray,
                                                                   grabacion_procesada):
    wav, transcript = grabacion_procesada
    tray._move_to_processed(wav, transcript)
    assert not transcript.exists()


@pytest.mark.parametrize('suf', ['.lang', '.partial', '.context'])
def test_los_auxiliares_se_borran(tray, grabacion_procesada, suf):
    wav, transcript = grabacion_procesada
    tray._move_to_processed(wav, transcript)
    assert not wav.with_suffix(suf).exists()


def test_mover_dos_veces_no_lanza(tray, grabacion_procesada):
    wav, transcript = grabacion_procesada
    tray._move_to_processed(wav, transcript)
    tray._move_to_processed(wav, transcript)          # idempotente


def test_si_el_destino_esta_ocupado_se_sobrescribe_en_silencio(tray,
                                                               grabacion_procesada,
                                                               wav_factory):
    """Comportamiento real, medido: shutil.move NO falla cuando el destino
    existe. os.rename si, pero shutil cae a copy2 + unlink y SOBRESCRIBE.

    O sea que un WAV homonimo ya procesado se pierde sin un solo aviso en el
    log. Es un caso raro (el nombre lleva fecha y hora al minuto) pero
    silencioso, y por eso queda fijado aqui."""
    wav, transcript = grabacion_procesada
    destino = ta.RECORDINGS_DIR / 'processed' / wav.name
    wav_factory(destino, seconds=0.5)                 # el viejo dura 0,5 s
    tamano_viejo = destino.stat().st_size

    tray._move_to_processed(wav, transcript)

    assert not wav.exists()
    assert destino.stat().st_size != tamano_viejo, "el de 0,1 s piso al de 0,5 s"


def test_mover_sin_que_exista_recordings_lanza(tray, grabacion_procesada, monkeypatch,
                                               tr_dirs):
    """CASO LIMITE: el mkdir es sin parents=True, asi que si RECORDINGS_DIR no
    existe revienta FUERA del try. Se fija el comportamiento actual."""
    wav, transcript = grabacion_procesada
    monkeypatch.setattr(ta, 'RECORDINGS_DIR', tr_dirs / 'no' / 'existe')

    with pytest.raises(FileNotFoundError):
        tray._move_to_processed(wav, transcript)


# ── La notificacion pendiente se consume una sola vez ────────────────────

@pytest.fixture
def espia_push(monkeypatch):
    enviados = []
    monkeypatch.setattr(ta.TrayApp, '_send_push_notification',
                        lambda self, txt: enviados.append(txt))
    monkeypatch.setattr(ta.threading, 'Thread',
                        lambda target, args=(), **k: type(
                            'T', (), {'start': lambda self: target(*args)})())
    return enviados


def test_sin_fichero_pendiente_no_pasa_nada(tray, espia_push, tr_dirs):
    tray._check_pending_notification()
    assert espia_push == []


def test_la_notificacion_se_envia_una_sola_vez(tray, espia_push, tr_dirs):
    """El fichero se BORRA antes de lanzar el hilo. Sin eso, el poller cada
    5 s mandaria el mismo correo al equipo una y otra vez."""
    f = ta.PROJECT_DIR / '.pending_notification.txt'
    f.write_text('tres commits nuevos', encoding='utf-8')

    tray._check_pending_notification()
    tray._check_pending_notification()

    assert espia_push == ['tres commits nuevos']
    assert not f.exists()


def test_un_fichero_vacio_se_borra_y_no_notifica(tray, espia_push, tr_dirs):
    f = ta.PROJECT_DIR / '.pending_notification.txt'
    f.write_text('   \n  ', encoding='utf-8')

    tray._check_pending_notification()

    assert espia_push == []
    assert not f.exists(), "un fichero vacio debe limpiarse igual"


# ── El menu: grabar / parar ───────────────────────────────────────────────

class RecorderFalso:
    def __init__(self, grabando=False):
        self.is_recording = grabando
        self.started = []
        self.stopped = 0

    def start(self, path):
        self.started.append(path)
        self.is_recording = True

    def stop(self):
        self.stopped += 1
        self.is_recording = False

    def wait_for_save(self, timeout=None):
        return True


def test_grabar_ahora_arranca_con_el_nombre_manual(tray_factory, monkeypatch, tr_dirs):
    rec = RecorderFalso(grabando=False)
    app = tray_factory(recorder=rec)
    monkeypatch.setattr(ta.TrayApp, 'set_recording', lambda self, a, p=None, mode=None: None)

    app._record_now()

    assert len(rec.started) == 1
    assert rec.started[0].stem.endswith('_manual')


def test_parar_detiene_la_grabacion(tray_factory, monkeypatch, tr_dirs):
    rec = RecorderFalso(grabando=True)
    app = tray_factory(recorder=rec)
    estados = []
    monkeypatch.setattr(ta.TrayApp, 'set_recording',
                        lambda self, a, p=None: estados.append(a))

    app._stop_recording()

    assert rec.stopped == 1
    assert estados == [False]


def test_quit_apaga_el_worker_y_para_todo(tray_factory, monkeypatch, tr_dirs):
    rec = RecorderFalso(grabando=True)

    class DetectorFalso:
        def __init__(self):
            self.stopped = 0

        def stop(self):
            self.stopped += 1

    det = DetectorFalso()
    app = tray_factory(recorder=rec, detector=det)
    app._pipeline_queue = queue.Queue()

    app._quit()

    assert det.stopped == 1
    assert app._pipeline_queue.get_nowait() is None, "falta el centinela de parada"
