"""Generacion de minutas: prompt, invocacion del CLI y parseo de su salida.

Sin llamar a ningun LLM ni a la red: se sustituye subprocess.Popen.

Lo que protege: el titulo de la reunion sale de la PRIMERA linea de lo que
devuelve el modelo. Si ese parseo falla, todas las reuniones se llaman
'Reunion', el fichero de minutas pierde su nombre y el consumidor externo
deja de poder identificarlas.
"""
import pytest

import minutes_generator as mg

pytestmark = pytest.mark.unit


# ── Extraer el titulo de lo que devuelve el modelo ────────────────────────

def test_extrae_el_titulo_de_la_primera_linea():
    texto = 'TITULO: Revision del Deck de Ventas\n\n## Resumen\n\nContenido.'
    titulo, contenido = mg.extract_title_from_minutes(texto)

    assert titulo == 'Revision del Deck de Ventas'
    assert contenido.startswith('## Resumen')
    assert 'TITULO:' not in contenido


def test_sin_linea_de_titulo_cae_a_reunion_y_conserva_todo():
    """El modelo a veces se olvida del formato. Mejor una minuta llamada
    'Reunion' que perder el contenido."""
    texto = '## Resumen\n\nContenido sin titulo.'
    titulo, contenido = mg.extract_title_from_minutes(texto)

    assert titulo == 'Reunion'
    assert contenido == texto


def test_un_titulo_vacio_cae_a_reunion():
    titulo, _ = mg.extract_title_from_minutes('TITULO:   \n\n## Resumen')
    assert titulo == 'Reunion'


def test_el_titulo_solo_se_lee_de_la_primera_linea():
    """Si apareciera en la linea 3 seria texto del cuerpo, no el titulo."""
    texto = '## Resumen\n\nTITULO: Esto no es el titulo\n'
    titulo, _ = mg.extract_title_from_minutes(texto)
    assert titulo == 'Reunion'


def test_tolera_lineas_y_espacios_en_blanco_antes_del_titulo():
    """El modelo a veces arranca con una linea en blanco o indenta. El strip()
    previo lo absorbe, asi que el titulo se sigue leyendo."""
    titulo, contenido = mg.extract_title_from_minutes(
        '\n\n  TITULO: Con Espacios\n\ncuerpo')

    assert titulo == 'Con Espacios'
    assert contenido.strip() == 'cuerpo'


def test_un_texto_vacio_no_revienta():
    assert mg.extract_title_from_minutes('') == ('Reunion', '')


def test_un_titulo_con_dos_puntos_dentro_se_conserva_entero():
    titulo, _ = mg.extract_title_from_minutes('TITULO: Contoso: fase 2\n\ncuerpo')
    assert titulo == 'Contoso: fase 2'


# ── Guardar las minutas ──────────────────────────────────────────────────

def test_guardar_crea_el_directorio_si_no_existe(tr_dirs):
    destino = tr_dirs / 'nueva' / 'carpeta' / 'minuta.md'
    assert mg.save_minutes('contenido', destino) is True
    assert destino.read_text(encoding='utf-8') == 'contenido'


def test_guardar_usa_utf8(tr_dirs):
    """Los titulos llevan acentos y enes: con la codificacion del sistema, el
    fichero saldria corrupto."""
    destino = tr_dirs / 'minutes' / 'acentos.md'
    mg.save_minutes('Reunión de diseño con Iñigo', destino)
    assert 'Reunión de diseño' in destino.read_text(encoding='utf-8')


def test_guardar_en_una_ruta_imposible_devuelve_false_y_no_lanza(tr_dirs, monkeypatch):
    """Un fallo de disco no puede tumbar el pipeline: la transcripcion ya
    esta hecha y hay que poder seguir."""
    def boom(*_a, **_k):
        raise OSError('disco lleno')

    monkeypatch.setattr(type(tr_dirs), 'write_text', boom)
    assert mg.save_minutes('x', tr_dirs / 'minutes' / 'x.md') is False


def test_si_el_disco_se_llena_la_minuta_anterior_queda_entera(tr_dirs, monkeypatch):
    """write_text trunca antes de escribir: con el disco lleno, una minuta que
    ya existia quedaba a medias, y _recover_pending la daba por hecha."""
    destino = tr_dirs / 'minutes' / 'x.md'
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text('la minuta entera', encoding='utf-8')
    real = type(destino).write_text

    def a_medias(self, data, *a, **k):
        real(self, data[:3], *a, **k)
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(type(destino), 'write_text', a_medias)
    assert mg.save_minutes('la minuta nueva', destino) is False
    monkeypatch.undo()
    assert destino.read_text(encoding='utf-8') == 'la minuta entera'
    assert not list(destino.parent.glob('*.tmp')), "quedo el temporal"


def test_un_bloqueo_momentaneo_al_renombrar_se_reintenta(tr_dirs, monkeypatch):
    destino = tr_dirs / 'minutes' / 'x.md'
    real, intentos = mg.os.replace, []

    def replace(src, dst):
        intentos.append(1)
        if len(intentos) < 3:
            raise PermissionError(5, 'Access is denied')
        return real(src, dst)

    monkeypatch.setattr(mg.os, 'replace', replace)
    monkeypatch.setattr(mg.time, 'sleep', lambda _s: None)
    assert mg.save_minutes('hola', destino) is True
    assert destino.read_text(encoding='utf-8') == 'hola' and len(intentos) == 3


# ── El prompt que se le manda al modelo ──────────────────────────────────

@pytest.mark.parametrize('idioma', ['es', 'en', 'auto'])
def test_el_prompt_de_sistema_se_construye_en_los_tres_modos(idioma):
    p = mg._get_system_prompt(idioma)
    assert len(p) > 100
    assert '{language_instruction}' not in p, "quedo un placeholder sin rellenar"


def test_el_prompt_incluye_el_transcript(tr_dirs):
    p = mg._build_prompt('hola que tal', tr_dirs / 'recordings' / 'x.wav')
    assert 'hola que tal' in p


def test_el_prompt_incluye_el_contexto_extra_si_lo_hay(tr_dirs):
    """Es lo que el usuario escribe en 'Anadir contexto a grabacion'."""
    p = mg._build_prompt('transcript', tr_dirs / 'recordings' / 'x.wav',
                         extra_context='hablamos del presupuesto de Contoso')
    assert 'Contoso' in p


def test_la_regla_de_atribucion_esta_en_el_prompt():
    """Se anadio el 15/09 porque las minutas atribuian frases a personas que
    nunca se nombraron: el transcript no lleva diarizacion, asi que todo
    'X dijo Y' es una inferencia del modelo."""
    p = mg._get_system_prompt('es').lower()
    assert 'atribu' in p


# ── Invocacion del CLI ───────────────────────────────────────────────────

@pytest.fixture
def cli_falso(monkeypatch):
    """Sustituye subprocess.Popen y guarda con que se le llamo."""
    llamadas = []

    class ProcFalso:
        def __init__(self, salida='TITULO: Generada\n\n## Resumen', rc=0, err=''):
            self.salida, self.returncode, self.err = salida, rc, err
            self.killed = False

        def communicate(self, input=None, timeout=None):
            llamadas.append({'prompt': input, 'timeout': timeout})
            return self.salida, self.err

        def kill(self):
            self.killed = True

    estado = {'proc': ProcFalso(), 'cmd': None}

    def popen_falso(cmd, **kwargs):
        estado['cmd'] = cmd
        estado['kwargs'] = kwargs
        return estado['proc']

    monkeypatch.setattr(mg.subprocess, 'Popen', popen_falso)
    monkeypatch.setattr(mg, '_get_claude_bin', lambda: 'claude')
    estado['llamadas'] = llamadas
    estado['ProcFalso'] = ProcFalso
    return estado


def test_sin_el_cli_de_claude_devuelve_none(monkeypatch, tr_dirs):
    """No hay excepcion: el pipeline sigue y avisa. La transcripcion ya esta
    guardada, que es lo caro."""
    monkeypatch.setattr(mg, '_get_claude_bin', lambda: None)
    assert mg._generate_via_cli('t', tr_dirs / 'x.wav') is None


def test_devuelve_la_salida_del_cli_sin_espacios(cli_falso, tr_dirs):
    cli_falso['proc'].salida = '  TITULO: Con espacios\n\ncuerpo  \n'
    resultado = mg._generate_via_cli('t', tr_dirs / 'x.wav')
    assert resultado == 'TITULO: Con espacios\n\ncuerpo'


def test_el_prompt_va_por_stdin_y_no_por_la_linea_de_comandos(cli_falso, tr_dirs):
    """Un transcript de 37 minutos son decenas de miles de caracteres: por
    argumento reventaria el limite de la linea de comandos de Windows."""
    mg._generate_via_cli('transcript larguisimo', tr_dirs / 'x.wav')

    assert 'transcript larguisimo' in cli_falso['llamadas'][0]['prompt']
    assert not any('transcript larguisimo' in str(a) for a in cli_falso['cmd'])


def test_se_invoca_en_modo_no_interactivo(cli_falso, tr_dirs):
    mg._generate_via_cli('t', tr_dirs / 'x.wav')
    assert '-p' in cli_falso['cmd']


def test_un_codigo_de_error_del_cli_devuelve_none(cli_falso, tr_dirs):
    cli_falso['proc'] = cli_falso['ProcFalso'](rc=1, err='algo fallo')
    assert mg._generate_via_cli('t', tr_dirs / 'x.wav') is None


def test_un_timeout_mata_el_proceso_y_devuelve_none(cli_falso, tr_dirs, monkeypatch):
    """Sin el kill quedaria un proceso claude colgado por cada reunion."""
    proc = cli_falso['proc']

    def communicate_que_expira(input=None, timeout=None):
        if timeout:
            raise mg.subprocess.TimeoutExpired('claude', timeout)
        return '', ''

    proc.communicate = communicate_que_expira

    assert mg._generate_via_cli('t', tr_dirs / 'x.wav') is None
    assert proc.killed, "el proceso colgado debe matarse"


def test_el_timeout_es_generoso(cli_falso, tr_dirs):
    """Generar minutas de una reunion larga con contexto de proyecto tarda
    minutos: un timeout corto las perderia."""
    mg._generate_via_cli('t', tr_dirs / 'x.wav')
    assert cli_falso['llamadas'][0]['timeout'] >= 600


def test_una_excepcion_al_lanzar_el_cli_devuelve_none(monkeypatch, tr_dirs):
    monkeypatch.setattr(mg, '_get_claude_bin', lambda: 'claude')

    def popen_que_revienta(*_a, **_k):
        raise OSError('no se pudo lanzar')

    monkeypatch.setattr(mg.subprocess, 'Popen', popen_que_revienta)
    assert mg._generate_via_cli('t', tr_dirs / 'x.wav') is None


def test_el_entorno_del_subproceso_va_limpio_de_tokens(cli_falso, tr_dirs):
    """clean_env quita ANTHROPIC_* y MCP_*: si el subproceso heredara el token
    de la sesion, usaria credenciales que no le corresponden."""
    mg._generate_via_cli('t', tr_dirs / 'x.wav')
    env = cli_falso['kwargs']['env']
    assert not [k for k in env if k.startswith(('ANTHROPIC_', 'MCP_'))]


def test_con_contexto_de_proyecto_se_anade_la_carpeta_en_solo_lectura(cli_falso, tr_dirs):
    """--allowedTools limitado a lectura: el subproceso no debe poder escribir
    en la memoria del proyecto."""
    mg._generate_via_cli('t', tr_dirs / 'x.wav', context_dir=str(tr_dirs))

    cmd = cli_falso['cmd']
    assert '--add-dir' in cmd
    assert str(tr_dirs) in cmd
    herramientas = cmd[cmd.index('--allowedTools') + 1:]
    assert 'Read' in herramientas
    assert not any(h in ('Write', 'Edit', 'Bash') for h in herramientas)


def test_sin_contexto_de_proyecto_no_se_anade_ninguna_carpeta(cli_falso, tr_dirs):
    mg._generate_via_cli('t', tr_dirs / 'x.wav')
    assert '--add-dir' not in cli_falso['cmd']


def test_los_backticks_del_prompt_de_sistema_se_convierten(cli_falso, tr_dirs):
    """Se pasan a ~~~ porque las minutas llevan bloques de codigo y los
    backticks anidados rompian el parseo de acciones."""
    mg._generate_via_cli('t', tr_dirs / 'x.wav')
    prompt = cli_falso['llamadas'][0]['prompt']
    assert '```' not in prompt.split('## ')[0]


# ── Nombre de quien graba ────────────────────────────────────────────────

def test_el_nombre_del_usuario_llega_al_prompt(cli_falso, tr_dirs, monkeypatch):
    """Regresion: minutes_generator usaba PROJECT_DIR sin importarlo. El
    NameError lo tragaba un `except Exception` y me_name quedaba SIEMPRE
    vacio: el prompt hablaba de [Grabador/a] mientras el transcript traia
    [Ines], y los asistentes y responsables salian como 'Grabador/a'/'Otros'."""
    (tr_dirs / 'settings.json').write_text('{"user_name": "Ines"}', encoding='utf-8')
    monkeypatch.setattr(mg, 'PROJECT_DIR', tr_dirs)

    mg._generate_via_cli('[Ines]: hola\n[Otros]: buenas', tr_dirs / 'x.wav', language='es')

    prompt = cli_falso['llamadas'][0]['prompt']
    assert 'La persona que grabó esta reunión es **Ines**' in prompt
    assert '[Grabador/a]' not in prompt


def test_project_dir_esta_importado_en_el_generador():
    assert hasattr(mg, 'PROJECT_DIR')


@pytest.mark.parametrize('idioma', ['es', 'en', 'ca'])
def test_el_prompt_prohibe_otros_como_responsable(idioma):
    """Cada variante ('Otros', 'Otros (atribucion inferida)', 'Equipo'...)
    se convertia en una columna distinta del tablero."""
    assert 'REGLA DE RESPONSABLE' in mg._get_system_prompt(idioma)


# ── Calidad: el modelo recibe intervenciones, no un chat entrecortado ─────

def test_las_lineas_seguidas_del_mismo_hablante_se_unen():
    t = ('[00:01] [Otros]: Is this rolling\n'
         '[00:04] [Otros]: or just here today,\n'
         "[00:07] [Otros]: it's not rolling 12 months, right?")
    assert mg.compact_transcript(t) == (
        "[00:01] [Otros]: Is this rolling or just here today, it's not rolling 12 months, right?")


def test_cambiar_de_hablante_abre_otra_intervencion():
    t = '[00:01] [Ines]: hola\n[00:03] [Otros]: buenas\n[00:05] [Ines]: vamos'
    assert mg.compact_transcript(t).splitlines() == [
        '[00:01] [Ines]: hola', '[00:03] [Otros]: buenas', '[00:05] [Ines]: vamos']


def test_un_silencio_largo_abre_otra_intervencion():
    t = '[00:01] [Otros]: uno\n[00:30] [Otros]: dos'
    assert len(mg.compact_transcript(t).splitlines()) == 2


def test_un_transcript_sin_etiquetas_tambien_se_compacta():
    t = '[00:01] hola\n[00:03] que tal\n[01:00] otra cosa'
    assert mg.compact_transcript(t).splitlines() == ['[00:01] hola que tal', '[01:00] otra cosa']


def test_las_lineas_sin_marca_de_tiempo_se_respetan_y_no_se_unen():
    t = '[00:01] [Ines]: hola\n[TRANSCRIPCIÓN IMPORTADA]\n[00:03] [Ines]: adios'
    assert len(mg.compact_transcript(t).splitlines()) == 3


def test_compactar_no_pierde_texto():
    t = '\n'.join(f'[00:{i:02d}] [Otros]: palabra{i}' for i in range(0, 50, 2))
    out = mg.compact_transcript(t)
    assert all(f'palabra{i}' in out for i in range(0, 50, 2))


def test_compactar_vacio_no_lanza():
    assert mg.compact_transcript('') == ''


def test_el_prompt_lleva_el_transcript_compactado(tr_dirs):
    t = '\n'.join(f'[00:{i:02d}] [Otros]: frase{i}' for i in range(0, 20, 2))
    p = mg._build_prompt(t, tr_dirs / 'x.wav', language='es', me_name='Ines')
    assert p.count('[Otros]:') < 10 + 3      # una intervención, no diez líneas


@pytest.mark.parametrize('idioma', ['es', 'en', 'ca'])
def test_el_prompt_de_sistema_pide_sintetizar_por_tema(idioma):
    assert 'CALIDAD DE LA MINUTA' in mg._get_system_prompt(idioma)


@pytest.mark.parametrize('idioma, frase', [
    ('es', "nunca escribas 'Otros dijo'"),
    ('en', "never write 'Otros said'"),
])
def test_el_override_prohibe_narrar_por_hablante(tr_dirs, idioma, frase):
    p = mg._build_prompt('[Ines]: hola\n[Otros]: buenas', tr_dirs / 'x.wav',
                         language=idioma, me_name='Ines')
    assert frase in p


# ── Asistentes: invitación + transcript ──────────────────────────────────

INVITADOS = [
    {'name': 'Francesca Maestripieri', 'role': 'organizer', 'response': 'accepted',
     'meeting_subject': 'IBP Demand Review'},
    {'name': 'Andras Kovacs', 'role': 'required', 'response': 'no response'},
    {'name': 'Sharon Lee', 'role': 'required', 'response': 'declined'},
    {'name': 'Inés Campos', 'role': 'required', 'response': 'accepted'},
]
TRANSCRIPT = ('[00:01] [Inés]: hola a todos\n'
              '[00:09] [Otros]: gracias, soy Francesca, empezamos. Andras, ¿puedes compartir?')


def test_la_nota_de_asistentes_marca_quien_se_nombra_en_el_transcript():
    nota = mg._participants_note(INVITADOS, TRANSCRIPT, 'Inés', 'es')

    assert "IBP Demand Review" in nota
    assert 'Francesca Maestripieri [organizer; accepted] — nombrado en el transcript: solo nombre o apellido' in nota
    assert 'Andras Kovacs [required; no response] — nombrado en el transcript: solo nombre o apellido' in nota
    assert 'Sharon Lee [required; declined] — nombrado en el transcript: no' in nota


def test_quien_graba_se_reconoce_sin_depender_del_transcript():
    nota = mg._participants_note(INVITADOS, 'sin nombres aqui', 'Inés', 'es')
    assert 'Inés Campos [required; accepted] — es quien graba' in nota


def test_el_nombre_de_quien_graba_en_la_etiqueta_no_cuenta_como_dicho():
    """'[Sharon]:' como etiqueta de hablante no es que alguien la nombre."""
    spoken = mg._spoken_text('[00:01] [Sharon]: hola')
    assert 'sharon' not in spoken


@pytest.mark.parametrize('nombre, texto, esperado', [
    ('Andras Kovacs', 'andras kovacs presenta', 'full'),
    ('Andras Kovacs', 'andras presenta', 'partial'),
    ('Andras Kovacs', 'nadie habla de el', 'none'),
    ('Inés Campos', 'ines campos y nadie mas', 'full'),          # sin acentos
    ('Francesca Maestripieri', 'francesca y mestripieri', 'full'),  # Whisper escribe mal
    ('Ana', 'hablamos de banana', 'none'),                          # no es subcadena
])
def test_evidence(nombre, texto, esperado):
    palabras = set(__import__('re').findall(r'[a-z]{5,}', texto))
    assert mg._evidence(nombre, texto, palabras) == esperado


def test_las_reglas_dejan_fuera_a_quien_rechazo_y_no_aparece():
    nota = mg._participants_note(INVITADOS, TRANSCRIPT, 'Inés', 'es')
    assert 'rechazó' in nota and 'Invitados sin confirmar asistencia' in nota


def test_el_prompt_incluye_la_invitacion_y_no_contradice_la_regla(tr_dirs):
    p = mg._build_prompt(TRANSCRIPT, tr_dirs / 'x.wav', language='es', me_name='Inés',
                         participants=INVITADOS)
    assert 'Reglas de asistentes' in p
    assert 'Maestripieri' in p


def test_sin_invitados_el_prompt_no_cambia(tr_dirs):
    p = mg._build_prompt(TRANSCRIPT, tr_dirs / 'x.wav', language='es', me_name='Inés')
    assert 'Reglas de asistentes' not in p


def test_el_override_de_diarizacion_prohibe_otros_como_responsable(tr_dirs):
    p = mg._build_prompt('[Ines]: hola\n[Otros]: buenas', tr_dirs / 'x.wav',
                         language='es', me_name='Ines')
    assert "'Otros' NO es un asistente" in p


# ── run_claude y regenerate_actions_section ──────────────────────────────

def test_run_claude_devuelve_la_salida_sin_espacios(cli_falso):
    cli_falso['proc'].salida = '  hola  \n'
    assert mg.run_claude('un prompt', timeout=12) == 'hola'
    assert cli_falso['llamadas'][0] == {'prompt': 'un prompt', 'timeout': 12}


def test_run_claude_sin_cli_devuelve_none(monkeypatch):
    monkeypatch.setattr(mg, '_get_claude_bin', lambda: None)
    assert mg.run_claude('x') is None


def test_run_claude_con_error_del_cli_devuelve_none(cli_falso):
    cli_falso['proc'].returncode = 1
    cli_falso['proc'].err = 'boom'
    assert mg.run_claude('x') is None


def test_run_claude_con_salida_vacia_devuelve_none(cli_falso):
    cli_falso['proc'].salida = '   '
    assert mg.run_claude('x') is None


def test_run_claude_con_timeout_mata_el_proceso(cli_falso):
    import subprocess

    def lento(input=None, timeout=None):
        if timeout:
            raise subprocess.TimeoutExpired('claude', timeout)
        return '', ''
    cli_falso['proc'].communicate = lento
    assert mg.run_claude('x', timeout=1) is None
    assert cli_falso['proc'].killed


def test_regenerar_acciones_anade_la_cabecera_si_falta(cli_falso):
    cli_falso['proc'].salida = '| Accion | Responsable |'
    out = mg.regenerate_actions_section('t', '', 'cambia algo', language='es')
    assert out.startswith('## Acciones Pendientes\n\n| Accion')
    assert cli_falso['llamadas'][0]['timeout'] == 900


def test_regenerar_acciones_respeta_la_cabecera_que_trae(cli_falso):
    cli_falso['proc'].salida = '## Pending Actions\n\n| a |'
    assert mg.regenerate_actions_section('t', '', 'x', language='en').startswith('## Pending Actions')


def test_regenerar_acciones_sin_respuesta_devuelve_none(cli_falso):
    cli_falso['proc'].returncode = 2
    assert mg.regenerate_actions_section('t', '', 'x') is None
