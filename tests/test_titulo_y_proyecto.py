"""Titulo y proyecto siempre, aunque Claude falle, y las minutas que fallan se
rehacen solas (28/10/2026).

Ese dia caduco la sesion de Claude. Dos reuniones grabadas con "Grabar ahora"
quedaron como "Reunion", sin proyecto, sin minutas y sin acciones: no habia
forma de saber cual era cual sin abrir el transcript. Y el log decia solo
"claude -p error (rc=1): ", porque el CLI escribe su error en stdout.

LO QUE ESTOS TESTS PROTEGEN
1. El titulo sin Claude: el de Teams (del fichero o de su .title), el asunto de
   la cita que empieza a esa hora, o la hora. Nunca "Reunion".
2. El proyecto sin Claude: el que nombra el titulo (dos tercios de las palabras
   de su nombre, nunca el transcript, ninguno si empatan), como SUPOSICION: se
   ve en el panel, pero no archiva ni exporta a la carpeta de un cliente.
3. Las minutas que fallan quedan 'pending' con su grabacion y se rehacen en el
   MISMO fichero cuando Claude vuelve, sin abrir una sesion de reunion (que
   uniria la reunion de hoy a la de ayer), sin abrir el panel, y sin pisar lo
   que el usuario haya hecho mientras tanto.
4. El aviso dice el motivo real (sin sesion, sin CLI).
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import actions_enricher as ae
import main
import minutes_generator as mg
import outlook_sender as os_mod
import project_context as pc
import project_exporter as pe
import tray_app as ta
from audio_recorder import AudioRecorder

pytestmark = pytest.mark.unit

# La funcion real: conftest la sustituye en todos los tests (sin_claude_de_verdad).
CLAUDE_PROBLEM = mg.claude_problem

STEM = '2026-10-28_10-33_manual'
TRANSCRIPT = '[00:05] [Felipe]: Repasamos la sesion de formacion.\n[00:20] [Otros]: Vale.'
# Como los de verdad: nombres con palabras de cualquier reunion.
PROJECTS = [
    {'id': 'sales-training', 'name': 'Sales Training Presentation'},
    {'id': 'data-brain', 'name': 'Intelligent Data Brain'},
    {'id': 'knowledge-brain', 'name': 'Knowledge Brain'},
    {'id': 'atlas-project', 'name': 'Atlas Project'},
    {'id': 'acme-agentic-strategy', 'name': 'Acme — Agentic AI Strategy & Target Architecture POC'},
    {'id': 'process-reinvention', 'name': 'Process Reinvention'},
    {'id': 'reinvention-deployed-engineer-rde', 'name': 'Reinvention Deployed Engineer (RDE)'},
    {'id': 'interviews', 'name': 'Interviews'},
]
ES = ta._STR['es']


def ajustes(tr_dirs, **valores):
    (tr_dirs / 'settings.json').write_text(json.dumps(valores), encoding='utf-8')


# ── 1. El titulo sin Claude ──────────────────────────────────────────────────

@pytest.mark.parametrize('stem, nombre', [
    ('2026-10-28_10-34_GTM_Sales_Training', 'GTM Sales Training'),
    ('2026-10-28_10-34_Comite_Semanal__2', 'Comite Semanal'),     # sin el sufijo de colision
    ('2026-10-28_10-33_manual', None),
    ('2026-10-28_10-33_reunion', None),
    ('2026-10-28_14-32_Unión_a_la_reunión', None),                # titulo pasajero de Teams
    ('sin_fecha', None),
])
def test_el_nombre_del_fichero_solo_cuenta_si_es_una_reunion(tray, stem, nombre):
    assert tray._stem_meeting_name(stem) == nombre


class Detector:
    def __init__(self, nombre):
        self.current_meeting_name = nombre


def grabando(tray, stem, nombre):
    tray._recording_path = ta.RECORDINGS_DIR / f'{stem}.wav'
    tray._detector = Detector(nombre)
    return tray._recording_path.with_suffix('.title')


def test_una_grabacion_manual_apunta_el_titulo_de_teams(tray):
    """EL CASO: a las 14:33 Teams ya sabia el nombre de la reunion y la grabacion
    manual no lo guardo en ningun sitio."""
    titulo = grabando(tray, STEM, 'IFC Roadmapping on steroids')
    tray._note_teams_title()
    assert titulo.read_text(encoding='utf-8') == 'IFC Roadmapping on steroids'


def test_vale_el_primer_titulo(tray):
    titulo = grabando(tray, STEM, 'Kickoff Orion')
    tray._note_teams_title()
    tray._detector.current_meeting_name = 'Otra ventana de Teams'
    tray._note_teams_title()
    assert titulo.read_text(encoding='utf-8') == 'Kickoff Orion'


@pytest.mark.parametrize('stem, nombre', [
    (STEM, None),                                       # sin llamada
    (STEM, 'Unión a la reunión'),                       # titulo pasajero
    ('2026-10-28_10-34_Kickoff_Orion', 'Otra'),         # el fichero ya lo lleva
])
def test_no_apunta_lo_que_no_es_un_titulo_nuevo(tray, stem, nombre):
    titulo = grabando(tray, stem, nombre)
    tray._note_teams_title()
    assert not titulo.exists()


def test_sin_grabacion_no_apunta_nada(tray):
    tray._recording_path = None
    tray._detector = Detector('Kickoff Orion')
    tray._note_teams_title()
    assert not list(ta.RECORDINGS_DIR.glob('*.title'))


def test_el_ticker_lo_apunta_y_un_error_no_lo_para(tray, monkeypatch):
    llamadas = []
    tray._recording_start = time.time()
    tray._disk_next_check = float('inf')
    monkeypatch.setattr(ta.TrayApp, '_write_status', lambda self: llamadas.append('estado'))
    monkeypatch.setattr(ta.TrayApp, '_check_pending_notification', lambda self: None)

    def apunta(self):
        llamadas.append('titulo')
        self._ticker_stop.set()
        raise OSError('disco lleno')

    monkeypatch.setattr(ta.TrayApp, '_note_teams_title', apunta)
    tray._ticker()
    assert llamadas == ['titulo', 'estado']


def test_el_titulo_de_respaldo_va_por_orden(tray, monkeypatch):
    manual = ta.RECORDINGS_DIR / f'{STEM}.wav'
    teams = ta.RECORDINGS_DIR / '2026-10-28_11-02_Kickoff_Orion.wav'
    preguntas = []
    monkeypatch.setattr(os_mod, 'find_meeting_subject', lambda t: preguntas.append(t) or 'Asunto de Outlook')

    # 1. El nombre de Teams de cualquier grabacion de la reunion (reconexiones).
    assert tray._fallback_title([manual, teams], ES, use_outlook=True) == 'Kickoff Orion'
    # 2. El .title de una grabacion manual, tambien con el WAV ya en processed/.
    titulo = ta.RECORDINGS_DIR / 'processed' / f'{STEM}.title'
    titulo.parent.mkdir(exist_ok=True)
    titulo.write_text('IFC Roadmapping', encoding='utf-8')
    assert tray._fallback_title([manual], ES, use_outlook=True) == 'IFC Roadmapping'
    titulo.unlink()
    assert preguntas == []
    # 3. El asunto de la cita de esa hora, solo con las minutas completas...
    assert tray._fallback_title([manual], ES, use_outlook=True) == 'Asunto de Outlook'
    assert preguntas == [datetime(2026, 10, 28, 10, 33)]
    # ...con las breves no se abre Outlook.
    assert tray._fallback_title([manual], ES) == 'Reunión 10:33'
    assert len(preguntas) == 1


@pytest.mark.parametrize('lang, esperado', [('es', 'Reunión 10:33'), ('en', 'Meeting 10:33')])
def test_sin_nada_mas_queda_la_hora(tray, lang, esperado):
    """conftest: no hay Outlook."""
    s = ta._STR[lang]
    assert tray._fallback_title([ta.RECORDINGS_DIR / f'{STEM}.wav'], s, use_outlook=True) == esperado
    assert tray._fallback_title([], s).startswith(esperado[:-5])


class CitaFalsa:
    def __init__(self, asunto, inicio, fin=None, estado=0, respuesta=0):
        self.Subject = asunto
        self.Start = inicio
        if fin is not None:
            self.End = fin
        self.MeetingStatus = estado
        self.ResponseStatus = respuesta
        self.Recipients = [type('R', (), {'Name': 'Ines', 'Address': 'ines@x.com'})()]


AHORA = datetime(2026, 10, 28, 10, 33)


def test_el_asunto_es_el_de_la_cita_que_empieza_a_esa_hora(fake_outlook):
    fake_outlook.appointments = [CitaFalsa('Revision Trimestral', AHORA + timedelta(minutes=2))]
    assert os_mod.find_meeting_subject(AHORA) == 'Revision Trimestral'


def test_una_cita_cancelada_o_rechazada_no_da_nombre(fake_outlook):
    fake_outlook.appointments = [
        CitaFalsa('Cancelada: Comite', AHORA, estado=5),
        CitaFalsa('Formacion que rechace', AHORA, respuesta=4),
    ]
    assert os_mod.find_meeting_subject(AHORA) is None


def test_la_reunion_anterior_que_sigue_en_curso_no_da_nombre(fake_outlook):
    """Para los participantes si vale (find_meeting_participants), para el
    titulo no: seria el nombre de otra reunion."""
    fake_outlook.appointments = [CitaFalsa('La de antes', AHORA - timedelta(minutes=50),
                                           fin=AHORA + timedelta(minutes=10))]
    assert os_mod.find_meeting_subject(AHORA) is None
    assert [p['email'] for p in os_mod.find_meeting_participants(AHORA)] == ['ines@x.com']


def test_sin_outlook_no_hay_asunto():
    """conftest: Outlook no disponible."""
    assert os_mod.find_meeting_subject(AHORA) is None


def test_descartar_la_grabacion_borra_su_titulo(tray):
    """Una grabacion descartada no llega al cierre, que es quien recoge el .title:
    el 29/10/2026 se quedo uno suelto en recordings/."""
    wav = ta.RECORDINGS_DIR / f'{STEM}.wav'
    titulo = wav.with_suffix('.title')
    titulo.write_text('Revision semanal', encoding='utf-8')
    tray._recorder = type('R', (), {'is_recording': True, 'cancel': lambda self: None})()
    tray._recording_path = wav
    tray._cancel_recording()
    assert not titulo.exists()


def test_descartar_no_falla_si_el_titulo_esta_bloqueado(tray, monkeypatch):
    """Un .title abierto por otro proceso no impide descartar la grabacion."""
    wav = ta.RECORDINGS_DIR / f'{STEM}.wav'
    wav.with_suffix('.title').write_text('Revision semanal', encoding='utf-8')
    unlink = Path.unlink

    def bloqueado(self, *a, **k):
        if self.suffix == '.title':
            raise PermissionError(13, 'en uso por otro proceso')
        return unlink(self, *a, **k)

    monkeypatch.setattr(Path, 'unlink', bloqueado)
    tray._recorder = type('R', (), {'is_recording': True, 'cancel': lambda self: None})()
    tray._recording_path = wav
    tray._cancel_recording()
    assert tray._recording_path is None


# ── 2. El proyecto sin Claude ────────────────────────────────────────────────

@pytest.mark.parametrize('titulo, proyecto', [
    ('GTM Sales Training ConsultantsAnalysts Session Prep', 'sales-training'),
    ('20261028_1033_GTM_Sales_Training_Session_Prep', 'sales-training'),   # un nombre de fichero
    ('Knowledge Brain weekly', 'knowledge-brain'),
    ('Process Reinvention kickoff', 'process-reinvention'),
    ('Atlas weekly', 'atlas-project'),
    # Una palabra de cualquier reunion no basta (revision del 28/10/2026).
    ('AI Strategy workshop with Globex', None),
    ('Target architecture review - Initech', None),
    ('Process mapping session', None),
    ('Data engineer interview', None),
    ('Revision Slides Deck Brain', None),
    ('Project kickoff', None),                   # "project" no distingue un proyecto
    ('Atlas Interviews', None),                  # dos proyectos: empate, ninguno
    ('IFC Roadmapping on steroids', None),
    ('', None),
])
def test_el_proyecto_sale_del_titulo(titulo, proyecto):
    p = pc.project_from_title(titulo, PROJECTS)
    assert (p['id'] if p else None) == proyecto


def test_sin_lista_lee_los_proyectos(tr_dirs):
    (tr_dirs / 'projects.json').write_text(json.dumps({'projects': PROJECTS}), encoding='utf-8')
    assert pc.project_from_title('Atlas weekly')['id'] == 'atlas-project'


def minutas(tr_dirs, nombre='20261028_1033_GTM_Sales_Training.md', **json_extra):
    md = tr_dirs / 'minutes' / nombre
    md.write_text('<!-- noted:minutes=pending stem=x -->\n\nNada.\n', encoding='utf-8')
    ae._save_json(md, [], {})
    if json_extra:
        data = json.loads(ae.get_actions_path(md).read_text(encoding='utf-8'))
        data.update(json_extra)
        ae.get_actions_path(md).write_text(json.dumps(data), encoding='utf-8')
    (tr_dirs / 'projects.json').write_text(json.dumps({'projects': PROJECTS}), encoding='utf-8')
    return md


def leer(md):
    return json.loads(ae.get_actions_path(md).read_text(encoding='utf-8'))


def claude_responde(monkeypatch, rc, stdout):
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: 'claude')
    monkeypatch.setattr(ae.subprocess, 'run', lambda *a, **k: type('R', (), {
        'returncode': rc, 'stdout': stdout, 'stderr': ''})())


def test_sin_sesion_de_claude_el_proyecto_sale_del_titulo_como_suposicion(tr_dirs, monkeypatch, caplog):
    md = minutas(tr_dirs)
    claude_responde(monkeypatch, 1, 'Failed to authenticate: OAuth session expired')
    ae._detect_and_save_project(md, tr_dirs)
    assert leer(md)['project_id'] == 'sales-training' and leer(md)['project_source'] == 'title'
    assert 'OAuth session expired' in caplog.text, "el motivo real, que va en stdout"


def test_una_suposicion_se_ve_pero_no_archiva_ni_exporta(tr_dirs, monkeypatch):
    """Una reunion en la carpeta de otro cliente es peor que una sin proyecto."""
    md = minutas(tr_dirs, project_id='sales-training', project_source='title')
    assert pc.meeting_project_id(md) == ''
    assert pe.get_meeting_project(md) is None
    # Confirmada por Claude, si.
    claude_responde(monkeypatch, 0, 'sales-training')
    ae._detect_and_save_project(md, tr_dirs)
    assert 'project_source' not in leer(md) and pc.meeting_project_id(md) == 'sales-training'
    assert pe.get_meeting_project(md)['id'] == 'sales-training'


def test_sin_claude_instalado_tambien(tr_dirs, monkeypatch):
    md = minutas(tr_dirs)
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: None)
    ae._detect_and_save_project(md, tr_dirs)
    assert leer(md)['project_id'] == 'sales-training'


def test_si_claude_revienta_tambien(tr_dirs, monkeypatch):
    md = minutas(tr_dirs)
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: 'claude')

    def revienta(*_a, **_k):
        raise OSError('timeout')

    monkeypatch.setattr(ae.subprocess, 'run', revienta)
    ae._detect_and_save_project(md, tr_dirs)
    assert leer(md)['project_id'] == 'sales-training'


def test_si_el_titulo_no_nombra_ninguno_queda_sin_proyecto(tr_dirs, monkeypatch):
    """Sin project_id, y no con 'none': asi una deteccion posterior lo vuelve a intentar."""
    md = minutas(tr_dirs, '20261028_1433_IFC_Roadmapping.md')
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: None)
    ae._detect_and_save_project(md, tr_dirs)
    assert 'project_id' not in leer(md)


def test_un_fallo_buscando_el_proyecto_no_rompe_nada(tr_dirs, monkeypatch, caplog):
    md = minutas(tr_dirs)
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: None)

    def revienta(*_a):
        raise ValueError('boom')

    monkeypatch.setattr(pc, 'project_from_title', revienta)
    ae._detect_and_save_project(md, tr_dirs)
    assert 'project_id' not in leer(md) and 'boom' in caplog.text


def test_el_proyecto_elegido_a_mano_no_lo_cambia_nadie(tr_dirs, monkeypatch):
    md = minutas(tr_dirs)
    ae.set_meeting_project_id(md, 'atlas-project')
    claude_responde(monkeypatch, 0, 'sales-training')
    ae._detect_and_save_project(md, tr_dirs)
    assert leer(md)['project_id'] == 'atlas-project'
    # Ni al rehacer las acciones (el reintento de unas minutas pendientes).
    ae._save_json(md, [], {})
    assert leer(md)['project_id'] == 'atlas-project' and leer(md)['project_source'] == 'manual'


def test_la_deteccion_masiva_revisa_las_suposiciones_y_no_hace_nuevas(tr_dirs, monkeypatch):
    """Con Claude caido no se aplica una suposicion a todo el historial."""
    import config
    supuesta = minutas(tr_dirs, project_id='sales-training', project_source='title')
    sin_nada = minutas(tr_dirs, '20261028_0900_Atlas_weekly.md')
    revisadas = []
    monkeypatch.setattr(config, 'MINUTES_DIR', tr_dirs / 'minutes')
    monkeypatch.setattr(ae, '_sync_project_folder', lambda md: None)
    monkeypatch.setattr(ae, '_get_claude_bin', lambda: None)
    real = ae._detect_and_save_project
    monkeypatch.setattr(ae, '_detect_and_save_project',
                        lambda md, d, extra=None, title_fallback=True: revisadas.append(md) or
                        real(md, d, extra, title_fallback=title_fallback))
    ae.detect_projects_for_all_meetings()
    assert set(revisadas) == {supuesta, sin_nada}
    assert 'project_id' not in leer(sin_nada)


def test_rehacer_las_acciones_conserva_las_manuales_con_su_indice(tr_dirs):
    """Las tareas referencian la accion por su indice: el de la manual no cambia,
    y las nuevas de Claude no lo reutilizan."""
    md = minutas(tr_dirs)
    manual = ae.add_manual_action(md, 'Llamar a Ana')
    nueva = type('A', (), {'index': manual['index'], 'type': 'human', 'title': 'Enviar deck', 'archivo': None,
                           'context': None, 'prompt': '', 'assignee': None, 'deadline': None})()
    ae._save_json(md, [nueva], {})
    acciones = {a['title']: a['index'] for a in leer(md)['actions']}
    assert acciones['Llamar a Ana'] == manual['index']
    assert acciones['Enviar deck'] != manual['index']


# ── El motivo del fallo ──────────────────────────────────────────────────────

@pytest.fixture
def claude_problem_real(monkeypatch):
    monkeypatch.setattr(mg, 'claude_problem', CLAUDE_PROBLEM)


def test_sin_cli_el_motivo_es_que_falta(claude_problem_real, monkeypatch):
    monkeypatch.setattr(mg, '_get_claude_bin', lambda: None)
    assert mg.claude_problem() == 'missing'


@pytest.mark.parametrize('salida, motivo', [
    ('{"loggedIn": false, "authMethod": "none"}', 'no_session'),
    ('{"loggedIn": true}', None),
    ('error: unknown command auth', None),          # un CLI antiguo: no se sabe, se intenta
])
def test_el_motivo_lo_dice_claude_auth_status(claude_problem_real, monkeypatch, salida, motivo):
    pedidos = []
    monkeypatch.setattr(mg, '_get_claude_bin', lambda: 'claude')
    monkeypatch.setattr(mg.subprocess, 'run', lambda cmd, **k: pedidos.append(cmd) or type('R', (), {
        'stdout': salida, 'returncode': 1})())
    assert mg.claude_problem() == motivo
    assert pedidos == [['claude', 'auth', 'status']]


class ClaudeSinSesion:
    returncode = 1

    def __init__(self, *_a, **_k):
        pass

    def communicate(self, input=None, timeout=None):
        return 'Failed to authenticate: OAuth session expired\n', ''


@pytest.mark.parametrize('llamada', ['minutas', 'suelta'])
def test_el_log_trae_el_error_que_el_cli_escribe_en_stdout(monkeypatch, caplog, llamada):
    monkeypatch.setattr(mg, '_get_claude_bin', lambda: 'claude')
    monkeypatch.setattr(mg.subprocess, 'Popen', ClaudeSinSesion)
    if llamada == 'minutas':
        assert mg._generate_via_cli(TRANSCRIPT, ta.RECORDINGS_DIR / f'{STEM}.wav') is None
    else:
        assert mg.run_claude('hola') is None
    assert 'OAuth session expired' in caplog.text


def test_el_error_del_cli_prefiere_stderr():
    assert mg._cli_error('salida', 'el error') == 'el error'
    assert mg._cli_error(None, None) == ''


def test_las_minutas_pendientes_llevan_su_grabacion_y_el_motivo():
    raw = mg.brief_minutes('Kickoff Orion', 'pending', language='en', reason='no_session', stem=STEM)
    title, content = mg.extract_title_from_minutes(raw)
    assert title == 'Kickoff Orion' and mg.brief_mode_of(content) == 'pending'
    assert mg.pending_stem_of(content) == STEM and 'claude auth login' in content
    # Sin stem tambien son 'pending', pero no se pueden reintentar: no se sabe de que grabacion.
    assert mg.pending_stem_of(mg.brief_minutes('X', 'pending')) is None
    assert mg.pending_stem_of(mg.brief_minutes('X', 'off')) is None


# ── 3. El cierre: minutas pendientes y su reintento ──────────────────────────

@pytest.fixture
def cierre(monkeypatch, tr_dirs):
    """_finalize_session sin Claude, sin Outlook y sin archivar."""
    reg = {'avisos': [], 'completas': None, 'guardadas': [], 'paneles': [], 'mientras': None}

    def generate(*_a, **_k):
        if reg['mientras']:
            reg['mientras']()
        return reg['completas']

    def save(minutes, output_path):
        reg['guardadas'].append(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(minutes, encoding='utf-8')
        return True

    import app_window
    import html_exporter
    monkeypatch.setattr(mg, 'generate_minutes', generate)
    monkeypatch.setattr(mg, 'save_minutes', save)
    monkeypatch.setattr(html_exporter, 'export_to_html', lambda *a, **k: None)
    monkeypatch.setattr(pc, 'prepare_context', lambda *a, **k: (None, None))
    monkeypatch.setattr(ae, 'enrich_and_save', lambda md, d, on_done=None: on_done and on_done())
    monkeypatch.setattr(ae, 'save_without_actions', lambda md, d, extra_text=None, on_done=None:
                        on_done and on_done())
    monkeypatch.setattr(pc, 'sync_meeting_summary', lambda md: None)
    monkeypatch.setattr(pe, 'export_to_project_folder', lambda *a: False)
    monkeypatch.setattr(app_window, 'open_app', lambda path, **k: reg['paneles'].append(path))
    monkeypatch.setattr(ta.TrayApp, '_suggest_glossary_terms', lambda self, *a: None)
    monkeypatch.setattr(ta.TrayApp, '_notify', lambda self, t, m: reg['avisos'].append(m))
    monkeypatch.setattr(ta.TrayApp, 'set_processing', lambda self, m='': None)
    monkeypatch.setattr(ta.TrayApp, '_write_status', lambda self: None)
    return reg


def cerrar(tray, wav_factory, stem=STEM):
    wav = wav_factory(ta.RECORDINGS_DIR / f'{stem}.wav', seconds=0.1)
    tray._finalize_session({'base_wav': wav, 'parts': [wav], 'transcripts': {wav.stem: TRANSCRIPT},
                            'lang': 'es', 'key': '', 'awaiting': False, 'finalized': False,
                            'timer': None, 'last_at': time.time()})


def test_si_claude_falla_la_reunion_lleva_el_titulo_de_teams(tray, cierre, wav_factory, monkeypatch):
    """EL CASO del 28/10/2026: "Grabar ahora" durante una llamada de Teams."""
    monkeypatch.setattr(mg, 'claude_problem', lambda: 'no_session')
    (ta.RECORDINGS_DIR / f'{STEM}.title').write_text('IFC Roadmapping on steroids', encoding='utf-8')
    cerrar(tray, wav_factory)
    [md] = cierre['guardadas']
    assert md.name == '20261028_1033_IFC_Roadmapping_on_steroids.md'
    texto = md.read_text(encoding='utf-8')
    assert mg.brief_mode_of(texto) == 'pending' and mg.pending_stem_of(texto) == STEM
    assert 'claude auth login' in texto
    assert cierre['avisos'][0] == ES['minutes_pending_session']
    assert not (ta.RECORDINGS_DIR / f'{STEM}.title').exists(), "se va con el WAV a processed/"


@pytest.mark.parametrize('motivo, clave', [('missing', 'minutes_pending_missing'), (None, 'minutes_pending')])
def test_el_aviso_dice_el_motivo(tray, cierre, wav_factory, monkeypatch, motivo, clave):
    monkeypatch.setattr(mg, 'claude_problem', lambda: motivo)
    cerrar(tray, wav_factory)
    assert cierre['avisos'][0] == ES[clave]
    assert cierre['guardadas'][0].name == '20261028_1033_Reunión_1033.md'


def test_otra_grabacion_del_mismo_minuto_no_pisa_las_pendientes(tray, cierre, wav_factory):
    """Dos grabaciones en el mismo minuto comparten la hora del nombre: lo que
    identifica las minutas pendientes es el stem de su grabacion."""
    cerrar(tray, wav_factory)
    [pendiente] = cierre['guardadas']
    cierre['completas'] = 'TITULO: Otra Reunion\n\n## Resumen\n\nOtra cosa.'

    cerrar(tray, wav_factory, stem=f'{STEM}__2')

    assert cierre['guardadas'][1] != pendiente
    assert mg.pending_stem_of(pendiente.read_text(encoding='utf-8')) == STEM


# ── El reintento automatico ──────────────────────────────────────────────────

def pendiente(stem=STEM, nombre='20261028_1033_IFC_Roadmapping.md', transcript=TRANSCRIPT, renombrada=None):
    md = ta.MINUTES_DIR / nombre
    _t, content = mg.extract_title_from_minutes(mg.brief_minutes('IFC Roadmapping', 'pending', stem=stem))
    md.write_text((f'TITULO: {renombrada}\n\n' if renombrada else '') + content, encoding='utf-8')
    if transcript is not None:
        md.with_name(md.stem + '_transcript.txt').write_text(transcript, encoding='utf-8')
    return md


class Grabadora:
    is_recording = False
    is_saving = False


@pytest.fixture
def ocioso(tray, monkeypatch):
    tray._recorder = Grabadora()
    monkeypatch.setattr(ta.TrayApp, '_write_status', lambda self: None)
    return tray


@pytest.fixture
def cierres(monkeypatch):
    """Las reuniones que el reintento manda cerrar, sin cerrarlas."""
    vistos = []
    monkeypatch.setattr(ta.TrayApp, '_finalize_session', lambda self, sess: vistos.append(sess))
    return vistos


def test_cuando_claude_vuelve_la_reunion_se_cierra_con_su_transcript(ocioso, cierres):
    md = pendiente()
    (ta.RECORDINGS_DIR / f'{STEM}_transcript.lang').write_text('en')
    ocioso._retry_pending_minutes()
    [sess] = cierres
    assert sess['pending_md'] == md and sess['transcripts'] == {STEM: TRANSCRIPT} and sess['lang'] == 'en'
    assert sess['base_wav'].name == f'{STEM}.wav'
    # Sin sesion de reunion: no hay nada a lo que "reconectarse".
    assert ocioso._session is None and not ocioso.has_pending_session('IFC Roadmapping')
    assert ocioso._pipeline_queue.empty()


def test_sin_sesion_ni_se_intenta(ocioso, cierres, monkeypatch):
    pendiente()
    monkeypatch.setattr(mg, 'claude_problem', lambda: 'no_session')
    ocioso._retry_pending_minutes()
    assert cierres == [] and ocioso._pending_retries == {}


def test_como_mucho_tres_intentos_por_arranque(ocioso, cierres):
    pendiente()
    for _ in range(5):
        ocioso._retry_pending_minutes()
    assert len(cierres) == ta.RETRY_PENDING_MAX == ocioso._pending_retries[STEM]


def test_de_una_en_una(ocioso, monkeypatch):
    """Cada cierre es una llamada a Claude de hasta 15 minutos: con dos a la vez
    compartirian el estado del trabajo en curso."""
    pendiente()
    pendiente(stem='2026-10-28_14-33_manual', nombre='20261028_1433_Otra.md')
    vistos = []

    def cierra(self, sess):
        vistos.append(sess['base_wav'].stem)
        self._current_job = {'step': 3}                 # sus acciones aun en marcha

    monkeypatch.setattr(ta.TrayApp, '_finalize_session', cierra)
    ocioso._retry_pending_minutes()
    assert vistos == [STEM]


@pytest.mark.parametrize('ocupado', ['grabando', 'guardando', 'cola', 'sesion', 'procesando', 'minutas_breves'])
def test_solo_con_noted_ocioso_y_las_minutas_completas(ocioso, cierres, tr_dirs, ocupado):
    pendiente()
    if ocupado == 'grabando':
        ocioso._recorder.is_recording = True
    elif ocupado == 'guardando':
        ocioso._recorder.is_saving = True
    elif ocupado == 'cola':
        ocioso._pipeline_queue.put(ta.RECORDINGS_DIR / 'otra.wav')
    elif ocupado == 'sesion':
        ocioso._session = {'finalized': False}
    elif ocupado == 'procesando':
        ocioso._current_job = {'stem': 'otra'}
    else:
        ajustes(tr_dirs, minutes_mode='summary')
    ocioso._retry_pending_minutes()
    assert cierres == [] and ocioso._pending_retries == {}


@pytest.mark.parametrize('transcript', [None, '  \n'])
def test_sin_transcript_no_se_puede_reintentar_y_no_se_insiste(ocioso, cierres, caplog, transcript):
    pendiente(transcript=transcript)
    ocioso._retry_pending_minutes()
    assert cierres == []
    assert ocioso._pending_retries[STEM] == ta.RETRY_PENDING_MAX and 'no se pueden reintentar' in caplog.text


def test_las_completas_y_las_breves_no_se_reintentan(ocioso, cierres):
    (ta.MINUTES_DIR / '20261028_1033_Hecha.md').write_text('## Resumen\n\nHecho.\n', encoding='utf-8')
    _t, breve = mg.extract_title_from_minutes(mg.brief_minutes('Breve', 'off'))
    (ta.MINUTES_DIR / '20261028_1034_Breve.md').write_text(breve, encoding='utf-8')
    ocioso._retry_pending_minutes()
    assert cierres == []


def test_el_reintento_rehace_las_mismas_minutas_sin_abrir_el_panel(ocioso, cierre):
    """De punta a punta y sin el WAV (ya en processed/ o borrado por la
    retencion): basta el transcript. Las minimas ya se archivaron y exportaron
    con su nombre, asi que las nuevas van al mismo fichero. Y el panel no se
    abre: el reintento llega en cualquier momento, quiza compartiendo pantalla."""
    md = pendiente()
    cierre['completas'] = 'TITULO: Roadmap Q1\n\n## Resumen\n\nSe reviso.'

    ocioso._retry_pending_minutes()

    assert cierre['guardadas'] == [md]
    texto = md.read_text(encoding='utf-8')
    assert texto.startswith('TITULO: Roadmap Q1\n') and mg.brief_mode_of(texto) is None
    assert [p.name for p in ta.MINUTES_DIR.glob('*.md')] == [md.name]
    assert cierre['paneles'] == []
    assert not list(ta.RECORDINGS_DIR.glob('*_transcript.txt')), "nada suelto en recordings/"
    assert cierre['avisos'] == [ES['action_items'], ES['ready']], "el camino de siempre, sin aviso de fallo"


def test_un_reintento_que_vuelve_a_fallar_no_avisa_otra_vez(ocioso, cierre):
    md = pendiente()
    ocioso._retry_pending_minutes()
    assert cierre['guardadas'] == [md]
    assert mg.pending_stem_of(md.read_text(encoding='utf-8')) == STEM
    assert not any(a in cierre['avisos'] for a in (ES['minutes_pending'], ES['minutes_pending_session']))


@pytest.mark.parametrize('completas', [None, 'TITULO: Roadmap Q1\n\n## Resumen\n\nSe reviso.'])
def test_el_titulo_que_puso_el_usuario_se_queda(ocioso, cierre, completas):
    """"Reunion 10:33" es justo la que el usuario renombra mientras esta pendiente."""
    md = pendiente(renombrada='Comite con Ana')
    cierre['completas'] = completas
    ocioso._retry_pending_minutes()
    texto = md.read_text(encoding='utf-8')
    assert texto.startswith('TITULO: Comite con Ana\n')
    assert (mg.brief_mode_of(texto) == 'pending') is (completas is None)


def test_si_ya_no_estan_pendientes_no_se_tocan(tray, cierre):
    """El boton "Generar minutas completas" (el panel, otro proceso) pudo
    hacerlas despues de que el reintento las eligiera."""
    md = ta.MINUTES_DIR / '20261028_1033_IFC_Roadmapping.md'
    md.write_text('TITULO: Las del panel\n\n## Resumen\n\nCompletas.\n', encoding='utf-8')
    cierre['completas'] = 'TITULO: Las del reintento\n\n## Resumen\n\nOtras.'
    wav = ta.RECORDINGS_DIR / f'{STEM}.wav'
    tray._finalize_session({'base_wav': wav, 'parts': [wav], 'transcripts': {STEM: TRANSCRIPT}, 'lang': 'es',
                            'key': '', 'awaiting': False, 'finalized': False, 'timer': None,
                            'last_at': time.time(), 'pending_md': md})
    assert cierre['guardadas'] == [] and 'Las del panel' in md.read_text(encoding='utf-8')


def test_si_las_hacen_mientras_claude_genera_estas_no_se_guardan(ocioso, cierre):
    md = pendiente()
    cierre['completas'] = 'TITULO: Las del reintento\n\n## Resumen\n\nOtras.'
    cierre['mientras'] = lambda: md.write_text('TITULO: Las del panel\n\nCompletas.\n', encoding='utf-8')
    ocioso._retry_pending_minutes()
    assert cierre['guardadas'] == [] and 'Las del panel' in md.read_text(encoding='utf-8')
    assert [p.name for p in ta.MINUTES_DIR.glob('*.md')] == [md.name]


def test_el_bucle_sigue_aunque_un_intento_falle(tray, monkeypatch):
    intentos, esperas = [], []

    class Basta(Exception):
        pass

    def espera(s):
        esperas.append(s)
        if len(esperas) == 3:
            raise Basta

    def falla(self):
        intentos.append(1)
        raise RuntimeError('boom')

    monkeypatch.setattr(ta.TrayApp, '_retry_pending_minutes', falla)
    monkeypatch.setattr(ta.time, 'sleep', espera)
    with pytest.raises(Basta):
        tray._retry_pending_loop()
    assert esperas == [ta.RETRY_PENDING_FIRST_S, ta.RETRY_PENDING_EVERY_S, ta.RETRY_PENDING_EVERY_S]
    assert len(intentos) == 2


# ── 4. Empezar a grabar con una grabacion ya en marcha ───────────────────────

def test_start_con_una_grabacion_en_marcha_devuelve_false():
    """El 28/10/2026 el aviso de Teams aceptado con una grabacion manual en
    marcha trato su ruta como la grabacion en curso: lanzo un segundo transcript
    en vivo de un audio que no existia."""
    rec = AudioRecorder()
    rec._recording = True
    assert rec.start(Path('otra.wav')) is False


def test_start_sin_grabacion_devuelve_true(monkeypatch):
    rec = AudioRecorder()
    monkeypatch.setattr(rec, '_begin', lambda path: None)
    assert rec.start(Path('una.wav')) is True and rec.is_recording


class YaGrabando:
    is_recording = False                # la comprobacion pasa, pero otro hilo gano
    on_recording_stopped = None

    def start(self, path):
        return False


def test_grabar_ahora_no_toma_otra_ruta_por_la_grabacion_en_curso(tray, monkeypatch):
    marcadas = []
    tray._recorder = YaGrabando()
    monkeypatch.setattr(ta.TrayApp, 'set_recording', lambda self, *a, **k: marcadas.append(a))
    tray._start_manual('background')
    assert marcadas == []


def test_el_cli_tampoco(monkeypatch):
    marcadas = []
    tray = type('T', (), {'_on_recording_done': None,
                          'set_recording': lambda self, *a, **k: marcadas.append(a)})()
    main._handle_cli_command('start', YaGrabando(), tray, lambda name: Path(f'{name}.wav'))
    assert marcadas == []


def test_el_detector_solo_da_nombre_durante_una_llamada():
    from teams_detector import TeamsCallDetector
    d = TeamsCallDetector()
    d._current_meeting_name = 'Kickoff Orion'
    assert d.current_meeting_name is None
    d._in_call = True
    assert d.current_meeting_name == 'Kickoff Orion'
