"""Fixtures compartidas y, sobre todo, el aislamiento.

POR QUE ESTE FICHERO ES LO PRIMERO DE LA SUITE
----------------------------------------------
Sin aislamiento, estos tests no son una red de seguridad: son un peligro.

1. `TrayApp.__init__` arranca tres hilos daemon. Uno, `_notification_poller`,
   llama cada 5 s a `_check_pending_notification`, que si encuentra
   `PROJECT_DIR/.pending_notification.txt` ejecuta `hooks/send_update_email.py`
   -> Outlook COM -> MANDA UN CORREO REAL AL EQUIPO. Otro, `_recover_pending`,
   hace glob sobre `RECORDINGS_DIR` y ENCOLA LAS GRABACIONES REALES para
   transcribir. Por eso `TrayApp` se construye aqui con `object.__new__`.
2. `tasks_store.TASKS_FILE` y `buckets_store.BUCKETS_FILE` se resuelven en
   tiempo de import contra la raiz del repo. Un test que llame `create_task()`
   sin redirigir escribe en el tablero real del usuario.

TRAMPA: LAS COSTURAS NO SON UNIFORMES
-------------------------------------
Los modulos hacen `from config import PROJECT_DIR`, es decir, se quedan con
una COPIA. Parchear `config.PROJECT_DIR` no afecta a `tray_app.PROJECT_DIR` ni
al contrario. Y varias funciones RE-IMPORTAN su configuracion dentro del
cuerpo, sombreando la del modulo:

  tray_app._write_status               -> lee config.PROJECT_DIR
  app_window.get_transcript_text       -> lee config.RECORDINGS_DIR
  tasks_store.migrate_panel_actions    -> lee config.MINUTES_DIR
  project_exporter.get_meeting_project -> lee config.PROJECT_DIR
  teams_chat.send_recording_notice     -> lee config.PROJECT_DIR

Por eso se parchea `config` Y cada modulo que tenga su copia. La tabla
`_SEAMS` es exhaustiva a proposito: es mas barato redirigir una constante que
no se usa que olvidar una que si.
"""
import hashlib
import importlib
import logging
import queue
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Los modulos se importan AQUI, en tiempo de coleccion, y no dentro de la
# fixture: los tests hacen sus imports en el cuerpo, que corre despues, asi que
# una fixture que solo parchease `sys.modules` no encontraria nada que parchear.
# Se excluyen los de GUI pura (popup, tk_thread, actions_window, actions_ui):
# no hay tests para ellos y no hace falta cargar tkinter.
_APP_MODULES = (
    'config', 'storage', 'transcriber', 'teams_detector', 'audio_recorder',
    'tray_app', 'app_window', 'tasks_store', 'buckets_store', 'project_context',
    'actions_enricher', 'actions_parser', 'minutes_generator', 'html_exporter',
    'inbox_watcher', 'outlook_sender', 'project_exporter', 'teams_chat',
    'diagnostics', 'cli', 'main', 'glossary', 'live_transcriber',
)
IMPORT_ERRORS: dict = {}


def _import_app_modules() -> None:
    for name in _APP_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:                      # noqa: BLE001
            IMPORT_ERRORS[name] = exc

    # main.py y cli.py llaman a logging.basicConfig con un FileHandler sobre el
    # log REAL de la app nada mas importarse. Si se deja puesto, la suite
    # escribe en el fichero que se usa para diagnosticar crashes en produccion.
    real_log = REPO / 'noted.log'
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler):
            try:
                same = Path(handler.baseFilename) == real_log
            except Exception:                         # noqa: BLE001
                same = False
            if same:
                root.removeHandler(handler)
                handler.close()


_import_app_modules()

# (nombre del atributo -> modulos que tienen su propia copia)
_SEAMS = {
    'PROJECT_DIR': ('config', 'actions_enricher', 'actions_ui', 'actions_window',
                    'app_window', 'audio_recorder', 'buckets_store', 'cli',
                    'diagnostics', 'main', 'minutes_generator', 'project_context',
                    'tasks_store', 'tray_app'),
    'MINUTES_DIR': ('config', 'app_window', 'project_context', 'storage', 'tray_app'),
    'RECORDINGS_DIR': ('config', 'app_window', 'inbox_watcher', 'storage', 'tray_app'),
    'INBOX_DIR': ('config', 'inbox_watcher', 'storage'),
    'BASE_OUTPUT_DIR': ('config',),
}

# Ficheros concretos: (modulos con copia, nombre bajo la raiz temporal)
_FILE_SEAMS = {
    'TASKS_FILE': (('tasks_store',), 'tasks.json'),
    'GLOSSARY_FILE': (('glossary',), 'glossary.json'),
    'BUCKETS_FILE': (('buckets_store',), 'buckets.json'),
    '_CANCEL_FILE': (('tray_app',), '.cancelled_jobs.txt'),
    'LOCK_FILE': (('main',), '.lock'),
    'CLI_CONTROL_FILE': (('config', 'cli', 'main'), '.cli_command'),
    'LOG_FILE': (('config', 'cli', 'main'), 'noted.log'),
    # 29/10/2026: ejecutar la suite con una reunion grabandose borraba el
    # .recording_active REAL (los tests de AudioRecorder llaman a stop()), y con
    # el la puerta que impide reiniciar el daemon en mitad de una grabacion.
    '_RECORDING_SENTINEL': (('audio_recorder',), '.recording_active'),
}

# Datos reales del usuario que la suite NUNCA debe tocar.
# .pipeline_status.json queda FUERA a proposito: el daemon lo reescribe cada
# segundo mientras corre, asi que vigilarlo daria falsos fallos. Si, en cambio,
# .cancelled_jobs.txt se vigila: un test que olvide redirigirlo escribiria una
# senal de cancelacion real y el daemon descartaria una reunion de verdad.
_USER_DATA = ('tasks.json', 'buckets.json', 'settings.json', 'projects.json',
              'pins.json', 'glossary.json', '.cancelled_jobs.txt')


def _fingerprint() -> dict:
    """Huella de los datos del usuario, para detectar si la suite los toca."""
    out = {}
    for name in _USER_DATA:
        p = REPO / name
        out[name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    for d in ('recordings', 'minutes'):
        folder = REPO / d
        out[d] = sorted(p.name for p in folder.iterdir()) if folder.is_dir() else None
    return out


@pytest.fixture(scope='session', autouse=True)
def _guard_user_data():
    """Falla si la suite modifico los datos reales del usuario.

    No es paranoia: es la comprobacion de que el aislamiento funciona. Un test
    que ensucia los datos reales es peor que no tener test.
    """
    before = _fingerprint()
    yield
    after = _fingerprint()
    changed = [k for k in before if before[k] != after[k]]
    assert not changed, (
        f"LA SUITE TOCO DATOS REALES DEL USUARIO: {changed}. "
        "Falta una costura en _SEAMS/_FILE_SEAMS de tests/conftest.py"
    )


@pytest.fixture(autouse=True)
def tr_dirs(tmp_path, monkeypatch):
    """Redirige TODAS las rutas de la app a tmp_path. Autouse: sin excepciones.

    Devuelve la raiz temporal con recordings/, recordings/processed/, minutes/
    e inbox/ ya creados, que es lo que la app espera encontrar.
    """
    root = tmp_path / 'tr'
    for sub in ('recordings', 'recordings/processed', 'minutes', 'inbox'):
        (root / sub).mkdir(parents=True, exist_ok=True)

    values = {
        'PROJECT_DIR': root,
        'BASE_OUTPUT_DIR': root,
        'RECORDINGS_DIR': root / 'recordings',
        'MINUTES_DIR': root / 'minutes',
        'INBOX_DIR': root / 'inbox',
    }
    for attr, modules in _SEAMS.items():
        for mod_name in modules:
            mod = sys.modules.get(mod_name)
            if mod is not None:
                monkeypatch.setattr(mod, attr, values[attr], raising=False)

    for attr, (modules, filename) in _FILE_SEAMS.items():
        for mod_name in modules:
            mod = sys.modules.get(mod_name)
            if mod is not None:
                monkeypatch.setattr(mod, attr, root / filename, raising=False)

    # El nombre del usuario sale de settings.json: sin esto, los tests dependen
    # de quien los ejecute (normalize_assignee lo usa para unificar grafias).
    for mod_name in ('config', 'actions_parser'):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            monkeypatch.setattr(mod, 'ME_NAME', '', raising=False)

    # El idioma de la UI tambien sale del settings.json REAL, cargado al importar
    # config: con la app en ingles, los tests que esperan los textos en espanol
    # fallaban solo en la maquina de quien los ejecuta (30/10/2026).
    cfg = sys.modules.get('config')
    if cfg is not None:
        monkeypatch.setattr(cfg, '_settings', {}, raising=False)

    # Red de seguridad: toda ruta debe caer DENTRO de la raiz de este test.
    # Se comprueba asi, y no como "fuera del repo", porque --basetemp cuelga de
    # .pytest_tmp/ dentro del propio repo (ver el comentario en pyproject.toml).
    for attr, modules in _SEAMS.items():
        for mod_name in modules:
            mod = sys.modules.get(mod_name)
            current = getattr(mod, attr, None) if mod else None
            if current is not None:
                p = Path(current)
                assert p == root or root in p.parents, (
                    f"{mod_name}.{attr} no quedo aislado: {current}"
                )
    return root


# El spawn de verdad, para los tests que lo prueban a el.
import live_transcriber as _live_mod  # noqa: E402

REAL_LIVE_SPAWN = _live_mod.spawn


@pytest.fixture(autouse=True)
def live_spawns(monkeypatch):
    """Ningun test lanza el proceso en vivo de verdad.

    set_recording(True) lo lanza en un hilo (tray_app._start_live): sin esto,
    cualquier test que simule el inicio de una grabacion arrancaria un proceso
    que carga Whisper. Devuelve la lista de rutas con las que se pidio lanzar.
    """
    calls = []

    def fake_spawn(wav, recorder=None, still_wanted=None, **_kwargs):
        calls.append(wav)
        return None

    monkeypatch.setattr(_live_mod, 'spawn', fake_spawn)
    return calls


@pytest.fixture(autouse=True)
def sin_outlook_de_verdad(monkeypatch):
    """Ningun test habla con el Outlook de la maquina.

    Al empezar una grabacion, tray_app calcula las pistas del glosario
    (glossary.recording_context), que buscan la reunion en el calendario: sin
    esto, un test leeria el Outlook real de quien lo corre. Los que necesitan
    uno lo piden con fake_outlook, que sustituye a este.
    """
    try:
        import outlook_sender
    except ImportError:
        return

    def sin_outlook():
        raise OSError('Outlook no disponible en los tests')

    monkeypatch.setattr(outlook_sender, '_get_outlook', sin_outlook)


@pytest.fixture(autouse=True)
def sin_claude_de_verdad(monkeypatch):
    """Ningun test pregunta al claude de la maquina si tiene sesion (`claude
    auth status`, al fallar unas minutas): la respuesta dependeria de quien
    corre la suite. Los tests de claude_problem usan la funcion real."""
    import minutes_generator
    monkeypatch.setattr(minutes_generator, 'claude_problem', lambda: None)


@pytest.fixture(autouse=True)
def cierre_sin_errores_ocultos(caplog):
    """Un error dentro de _finalize_session vuelve a ser un fallo del test.

    En la app, _finalize_session recoge cualquier excepcion de su cuerpo y la
    deja en el log, porque corre en un hilo donde nadie mas la veria. En un test
    eso la esconde: un cierre que revienta despues de lo que el test comprueba
    pasaria en verde. El test que provoca el error a proposito lo comprueba y
    vacia caplog.
    """
    yield
    ocultos = [r.getMessage() for r in caplog.get_records('call')
               if r.name == 'tray_app' and r.getMessage().startswith('Error cerrando la reuni')]
    assert not ocultos, ocultos[0]


@pytest.fixture
def tray_factory(tr_dirs):
    """Fabrica TrayApp SIN pasar por __init__, para no arrancar los tres hilos.

    Replica el estado que monta el constructor (tray_app.py:96-124). Si algun
    dia se anade un atributo ahi, el test que lo necesite fallara con
    AttributeError, que es el fallo correcto: ruidoso y localizable.

    Es una fabrica y no una instancia suelta porque hay invariantes que solo se
    ven con DOS instancias: la senal de cancelacion viaja por fichero
    precisamente porque quien cancela es la ventana web, que corre en otro
    proceso.
    """
    import tray_app

    def make(recorder=None, detector=None):
        app = object.__new__(tray_app.TrayApp)
        app._recorder = recorder
        app._detector = detector
        app._icon = None
        app._pipeline_queue = queue.Queue()
        app._pipeline_queued = []
        app._recording_start = None
        app._recording_path = None
        app._ticker_stop = threading.Event()
        app._processing_msg = ''
        app._current_job = {}
        app._session = None
        app._session_lock = threading.RLock()
        app._MERGE_GRACE = 90
        app._cancelled = set()
        app._cancel_lock = threading.Lock()
        app._mic_state = None
        app._icon_base = (tray_app._ICON_IDLE, 'Noted')
        app._live = {}
        app._live_lock = threading.Lock()
        app._live_spawning = set()
        app._rec_mode = None
        app.last_recording_mode = None
        app._whisper_busy = False
        app._whisper_lock = threading.Lock()
        app._ctl_lock = threading.RLock()
        app._yield_seq = 0
        app._pending_retries = {}
        return app

    return make


@pytest.fixture
def tray(tray_factory):
    return tray_factory()


@pytest.fixture
def wav_factory(tr_dirs):
    """Genera WAVs sinteticos.

    El repo ignora *.wav, asi que no se versiona ningun binario de test: el
    audio se fabrica en cada ejecucion.
    """
    import numpy as np
    import soundfile as sf

    def make(path, seconds=1.0, samplerate=16000, freq=440.0, amplitude=0.3,
             subtype='PCM_16'):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        n = int(seconds * samplerate)
        if freq:
            t = np.arange(n, dtype=np.float32) / samplerate
            data = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        else:
            data = np.zeros(n, dtype=np.float32)
        sf.write(str(path), data, samplerate, subtype=subtype)
        return path

    return make


class FakeSegment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class FakeInfo:
    def __init__(self, language='es', duration=0.0):
        self.language, self.duration = language, duration


@pytest.fixture
def fake_whisper_model():
    """Modelo falso con la misma forma que faster-whisper.

    Devuelve un GENERADOR perezoso de segmentos, que es lo que hace posible
    que la cancelacion corte el trabajo de verdad al salir del bucle.
    """
    class FakeModel:
        def __init__(self, segments=None, language='es', duration=100.0):
            self._segments = segments or [(0.0, 5.0, 'hola'), (5.0, 10.0, 'que tal')]
            self.language, self.duration = language, duration
            self.calls = 0
            self.audio_lengths = []

        def transcribe(self, audio, **kwargs):
            self.calls += 1
            if not isinstance(audio, (str, Path)):
                self.audio_lengths.append(len(audio))
            segs = (FakeSegment(s, e, t) for s, e, t in self._segments)
            return segs, FakeInfo(self.language, self.duration)

    return FakeModel


@pytest.fixture
def fake_outlook(monkeypatch):
    """Sustituye el unico seam COM del repo: outlook_sender._get_outlook.

    Invariante de seguridad que protege: la app crea BORRADORES con Display()
    y no debe llamar nunca a Send().
    """
    import outlook_sender

    class FakeRecipients(list):
        """Colección COM de destinatarios: se rellena con .Add(direccion)."""

        def Add(self, address):
            entrada = type('Recipient', (), {'Address': address, 'Resolved': True})()
            self.append(entrada)
            return entrada

    class FakeMail:
        def __init__(self):
            self.displayed = False
            self.To = self.Subject = self.HTMLBody = self.Body = ''
            self.Recipients = FakeRecipients()
            self.Attachments = FakeRecipients()

        def Display(self):
            self.displayed = True

        def Send(self):
            raise AssertionError(
                "La app NUNCA debe llamar a Send(): solo crea borradores"
            )

    class FakeFolder:
        def __init__(self, appointments):
            class Items(list):
                IncludeRecurrences = False

                def Sort(self, *a, **k):
                    pass

                def Restrict(self, *_a):
                    return list(appointments)

            self.Items = Items(appointments)

    class FakeOutlook:
        def __init__(self):
            self.mails = []
            self.appointments = []

        def CreateItem(self, _kind):
            m = FakeMail()
            self.mails.append(m)
            return m

        def GetNamespace(self, _name):
            return self

        def GetDefaultFolder(self, _n):
            return FakeFolder(self.appointments)

    fake = FakeOutlook()
    monkeypatch.setattr(outlook_sender, '_get_outlook', lambda: fake)
    return fake
