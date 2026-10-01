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
    titulo, _ = mg.extract_title_from_minutes('TITULO: Aramco: fase 2\n\ncuerpo')
    assert titulo == 'Aramco: fase 2'


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
                         extra_context='hablamos del presupuesto de Aramco')
    assert 'Aramco' in p


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
