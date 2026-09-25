"""Fusion de una reunion partida en varias grabaciones por reconexion.

Cuando Teams se cae a mitad de una llamada y te reconectas, la app graba dos
ficheros y tiene que entregar UNA sola minuta. La maquinaria que lo decide es
la mas delicada del pipeline, porque se equivoca en los dos sentidos:

  - de menos: dos minutas para una reunion, cada una con la mitad
  - de mas: una minuta que mezcla dos reuniones distintas (defecto conocido,
    ver test_i18n_and_session.py)

`_finalize_session` es un orquestador de 180 lineas que llama a Whisper, al CLI
de claude y a Outlook. Aqui se sustituyen esas cuatro dependencias y se prueba
lo que de verdad es suyo: que une los transcripts en orden, con el separador, y
que solo lo hace UNA vez.
"""
import threading
import time

import pytest

import tray_app as ta

pytestmark = pytest.mark.unit

STEM_A = '2026-09-17_16-16_Comite_Semanal'
STEM_B = '2026-09-17_16-45_Comite_Semanal'


@pytest.fixture
def sin_hilos(monkeypatch):
    """Neutraliza Thread y Timer: los tests llaman a las funciones a mano."""
    class HiloFalso:
        def __init__(self, target=None, args=(), **kwargs):
            self._target, self._args = target, args
            self.daemon = False
            self.iniciado = False

        def start(self):
            self.iniciado = True

        def cancel(self):
            self.iniciado = False

    monkeypatch.setattr(ta.threading, 'Thread', HiloFalso)
    monkeypatch.setattr(ta.threading, 'Timer',
                        lambda _s, _f, args=(): HiloFalso(target=_f, args=args))
    return HiloFalso


# ── Registrar la primera grabacion ───────────────────────────────────────

def test_la_primera_grabacion_abre_una_sesion(tray, sin_hilos, tr_dirs):
    tray._register_part_recorded(ta.RECORDINGS_DIR / f'{STEM_A}.wav')

    assert tray._session is not None
    assert tray._session['key'] == 'comitesemanal'
    assert len(tray._session['parts']) == 1
    assert tray._session['finalized'] is False


def test_una_grabacion_manual_abre_una_sesion_sin_nombre(tray, sin_hilos, tr_dirs):
    """Sin nombre de reunion, la clave queda vacia: es lo que impide que
    absorba la siguiente llamada que llegue."""
    tray._register_part_recorded(ta.RECORDINGS_DIR / '2026-09-17_16-16_manual.wav')
    assert tray._session['key'] == 'manual'

    tray._session = None
    tray._register_part_recorded(ta.RECORDINGS_DIR / '2026-09-17_16-16.wav')
    assert tray._session['key'] == ''


# ── Reconexion: la segunda grabacion se suma a la primera ────────────────

def test_si_se_espera_reconexion_la_segunda_grabacion_se_anade(tray, sin_hilos, tr_dirs):
    tray._register_part_recorded(ta.RECORDINGS_DIR / f'{STEM_A}.wav')
    tray._session['awaiting'] = True

    tray._register_part_recorded(ta.RECORDINGS_DIR / f'{STEM_B}.wav')

    assert len(tray._session['parts']) == 2
    assert tray._session['awaiting'] is False, "ya llego, deja de esperar"


def test_sin_esperar_reconexion_la_segunda_grabacion_abre_sesion_nueva(tray, sin_hilos,
                                                                       tr_dirs):
    """Dos reuniones seguidas son dos minutas. Solo se fusionan si alguien
    marco que se esperaba una reconexion."""
    tray._register_part_recorded(ta.RECORDINGS_DIR / f'{STEM_A}.wav')
    primera = tray._session

    tray._register_part_recorded(ta.RECORDINGS_DIR / f'{STEM_B}.wav')

    assert tray._session is not primera
    assert len(tray._session['parts']) == 1


def test_al_abrir_sesion_nueva_la_anterior_se_cierra(tray, sin_hilos, tr_dirs,
                                                     monkeypatch):
    """Si no se cerrara, la reunion anterior se quedaria sin minutas."""
    cerradas = []
    monkeypatch.setattr(ta.TrayApp, '_finalize_session',
                        lambda self, sess: cerradas.append(sess))

    tray._register_part_recorded(ta.RECORDINGS_DIR / f'{STEM_A}.wav')
    primera = tray._session
    tray._register_part_recorded(ta.RECORDINGS_DIR / f'{STEM_B}.wav')

    # El cierre va en un hilo, que aqui esta neutralizado: se comprueba que se
    # programo con la sesion correcta.
    assert tray._session is not primera


def test_pedir_continuacion_marca_la_espera_y_cancela_el_cierre(tray, sin_hilos,
                                                                tr_dirs):
    tray._register_part_recorded(ta.RECORDINGS_DIR / f'{STEM_A}.wav')
    tray._session['timer'] = sin_hilos()
    tray._session['timer'].iniciado = True

    tray.request_continuation()

    assert tray._session['awaiting'] is True
    assert tray._session['timer'] is None, "el cierre programado debe cancelarse"


def test_pedir_continuacion_sin_sesion_no_lanza(tray, sin_hilos):
    tray.request_continuation()


def test_pedir_continuacion_sobre_una_sesion_cerrada_no_la_reabre(tray, sin_hilos,
                                                                  tr_dirs):
    tray._register_part_recorded(ta.RECORDINGS_DIR / f'{STEM_A}.wav')
    tray._session['finalized'] = True

    tray.request_continuation()

    assert tray._session.get('awaiting') is not True


# ── Registrar el transcript de cada parte ────────────────────────────────

def test_el_transcript_se_guarda_en_su_parte(tray, sin_hilos, tr_dirs, monkeypatch):
    monkeypatch.setattr(ta.TrayApp, 'set_processing', lambda self, m='': None)
    wav = ta.RECORDINGS_DIR / f'{STEM_A}.wav'
    tray._register_part_recorded(wav)

    tray._register_part(wav, 'lo que se dijo', 'es')

    assert tray._session['transcripts'][wav.stem] == 'lo que se dijo'
    assert tray._session['lang'] == 'es'


def test_con_todas_las_partes_transcritas_la_sesion_esta_completa(tray, sin_hilos,
                                                                  tr_dirs, monkeypatch):
    mensajes = []
    monkeypatch.setattr(ta.TrayApp, 'set_processing', lambda self, m='': mensajes.append(m))
    a = ta.RECORDINGS_DIR / f'{STEM_A}.wav'
    b = ta.RECORDINGS_DIR / f'{STEM_B}.wav'

    tray._register_part_recorded(a)
    tray._session['awaiting'] = True
    tray._register_part_recorded(b)

    tray._register_part(a, 'primera mitad', 'es')
    assert 'esperando' in mensajes[-1], "falta la parte b: no debe cerrarse aun"

    tray._register_part(b, 'segunda mitad', 'es')
    assert 'cerrando' in mensajes[-1]


def test_un_idioma_auto_no_pisa_uno_ya_detectado(tray, sin_hilos, tr_dirs, monkeypatch):
    """El segundo trozo puede no detectar idioma; el de la reunion ya se sabe."""
    monkeypatch.setattr(ta.TrayApp, 'set_processing', lambda self, m='': None)
    a = ta.RECORDINGS_DIR / f'{STEM_A}.wav'
    tray._register_part_recorded(a)

    tray._register_part(a, 'texto', 'en')
    tray._register_part(a, 'texto', 'auto')

    assert tray._session['lang'] == 'en'


def test_un_transcript_de_una_parte_desconocida_abre_sesion_nueva(tray, sin_hilos,
                                                                  tr_dirs, monkeypatch):
    """Pasa al reprocesar un WAV viejo tras un reinicio: el transcript llega
    sin que exista sesion para el."""
    monkeypatch.setattr(ta.TrayApp, 'set_processing', lambda self, m='': None)
    tray._register_part_recorded(ta.RECORDINGS_DIR / f'{STEM_A}.wav')

    huerfano = ta.RECORDINGS_DIR / '2026-01-01_09-00_Otra_Reunion.wav'
    tray._register_part(huerfano, 'texto suelto', 'es')

    assert tray._session['base_wav'] == huerfano
    assert tray._session['transcripts'] == {huerfano.stem: 'texto suelto'}


# ── Cerrar la sesion: unir los transcripts ───────────────────────────────

@pytest.fixture
def cierre_aislado(monkeypatch, tr_dirs):
    """Sustituye las cuatro dependencias externas de _finalize_session."""
    registro = {'minutas_de': None, 'guardadas': None}

    def generate_falso(transcript, wav_path, extra_context=None, language='auto',
                       context_dir=None, participants=None):
        registro['minutas_de'] = transcript
        registro['contexto'] = extra_context
        return 'TITULO: Comite Semanal\n\n## Resumen\n\ngenerado'

    def save_falso(minutes, output_path):
        registro['guardadas'] = output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(minutes, encoding='utf-8')
        return True

    import html_exporter
    import minutes_generator
    monkeypatch.setattr(minutes_generator, 'generate_minutes', generate_falso)
    monkeypatch.setattr(minutes_generator, 'save_minutes', save_falso)
    monkeypatch.setattr(html_exporter, 'export_to_html',
                        lambda *a, **k: tr_dirs / 'minutes' / 'x.html')

    import actions_enricher
    monkeypatch.setattr(actions_enricher, 'enrich_and_save', lambda *a, **k: None)

    import project_context
    monkeypatch.setattr(project_context, 'prepare_context', lambda *a, **k: (None, None))

    monkeypatch.setattr(ta.TrayApp, '_notify', lambda self, t, m: None)
    monkeypatch.setattr(ta.TrayApp, 'set_processing', lambda self, m='': None)
    monkeypatch.setattr(ta.TrayApp, '_write_status', lambda self: None)
    monkeypatch.setattr(ta.TrayApp, '_move_to_processed', lambda self, w, t: None)
    return registro


def _sesion(tray, partes, transcripts, lang='es'):
    return {
        'base_wav': partes[0], 'parts': partes,
        'transcripts': dict(zip(
            [p.stem for p in partes], transcripts, strict=True)),
        'lang': lang, 'key': 'comitesemanal', 'awaiting': False,
        'finalized': False, 'timer': None, 'last_at': time.time(),
    }


def test_una_sola_parte_no_lleva_separador(tray, cierre_aislado, tr_dirs, wav_factory):
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)

    tray._finalize_session(_sesion(tray, [a], ['texto unico']))

    assert cierre_aislado['minutas_de'] == 'texto unico'


def test_dos_partes_se_unen_en_orden_con_el_separador(tray, cierre_aislado, tr_dirs,
                                                      wav_factory):
    """EL INVARIANTE DE LA FUSION. Sin el separador, el modelo no sabe que hubo
    un corte y puede inventar continuidad donde no la hay."""
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)
    b = wav_factory(ta.RECORDINGS_DIR / f'{STEM_B}.wav', seconds=0.1)

    tray._finalize_session(_sesion(tray, [a, b], ['primera mitad', 'segunda mitad']))

    unido = cierre_aislado['minutas_de']
    assert unido.index('primera mitad') < unido.index('segunda mitad')
    assert 'reconexión' in unido


def test_el_transcript_unido_se_guarda_junto_a_la_parte_base(tray, cierre_aislado,
                                                             tr_dirs, wav_factory):
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)
    b = wav_factory(ta.RECORDINGS_DIR / f'{STEM_B}.wav', seconds=0.1)

    tray._finalize_session(_sesion(tray, [a, b], ['uno', 'dos']))

    guardado = ta.RECORDINGS_DIR / f'{STEM_A}_transcript.txt'
    assert guardado.exists()
    assert 'uno' in guardado.read_text(encoding='utf-8')
    assert 'dos' in guardado.read_text(encoding='utf-8')


def test_las_minutas_llevan_la_fecha_de_la_PRIMERA_parte(tray, cierre_aislado, tr_dirs,
                                                         wav_factory):
    """Si llevaran la de la reconexion, la reunion figuraria media hora mas
    tarde de lo que empezo."""
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)
    b = wav_factory(ta.RECORDINGS_DIR / f'{STEM_B}.wav', seconds=0.1)

    tray._finalize_session(_sesion(tray, [a, b], ['uno', 'dos']))

    assert cierre_aislado['guardadas'].stem.startswith('20260917_1616_')


def test_cerrar_dos_veces_no_genera_dos_minutas(tray, cierre_aislado, tr_dirs,
                                                wav_factory):
    """El cierre lo pueden disparar el temporizador, el usuario y la llegada de
    una sesion nueva. Sin el flag, tres minutas para una reunion."""
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)
    sess = _sesion(tray, [a], ['texto'])

    tray._finalize_session(sess)
    cierre_aislado['minutas_de'] = None
    tray._finalize_session(sess)

    assert cierre_aislado['minutas_de'] is None, "genero minutas dos veces"


def test_sin_transcripts_no_se_genera_nada(tray, cierre_aislado, tr_dirs, wav_factory):
    """Si la transcripcion fallo, generar minutas de la nada produciria una
    reunion inventada."""
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)

    tray._finalize_session(_sesion(tray, [a], [None]))

    assert cierre_aislado['minutas_de'] is None


def test_una_parte_sin_transcript_no_bloquea_a_las_demas(tray, cierre_aislado, tr_dirs,
                                                         wav_factory):
    """Mejor una minuta con la mitad que ninguna."""
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)
    b = wav_factory(ta.RECORDINGS_DIR / f'{STEM_B}.wav', seconds=0.1)

    tray._finalize_session(_sesion(tray, [a, b], ['solo la primera', None]))

    assert cierre_aislado['minutas_de'] == 'solo la primera'


def test_las_partes_extra_se_mueven_a_processed(tray, cierre_aislado, tr_dirs,
                                                wav_factory):
    """La parte base la mueve _move_to_processed al final; las extra se mueven
    aqui. Si no, _recover_pending las reprocesaria en el siguiente arranque."""
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)
    b = wav_factory(ta.RECORDINGS_DIR / f'{STEM_B}.wav', seconds=0.1)

    tray._finalize_session(_sesion(tray, [a, b], ['uno', 'dos']))

    assert not b.exists()
    assert (ta.RECORDINGS_DIR / 'processed' / b.name).exists()


def test_los_auxiliares_de_las_partes_extra_se_limpian(tray, cierre_aislado, tr_dirs,
                                                       wav_factory):
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)
    b = wav_factory(ta.RECORDINGS_DIR / f'{STEM_B}.wav', seconds=0.1)
    for suf in ('.lang', '.partial', '.context'):
        b.with_suffix(suf).write_text('x', encoding='utf-8')

    tray._finalize_session(_sesion(tray, [a, b], ['uno', 'dos']))

    for suf in ('.lang', '.partial', '.context'):
        assert not b.with_suffix(suf).exists()


def test_el_contexto_que_escribio_el_usuario_llega_al_modelo(tray, cierre_aislado,
                                                             tr_dirs, wav_factory):
    """Es lo que se escribe en 'Anadir contexto a grabacion' desde la bandeja:
    si se perdiera, el usuario habria escrito para nada."""
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)
    a.with_suffix('.context').write_text('hablamos del presupuesto de Aramco',
                                          encoding='utf-8')

    tray._finalize_session(_sesion(tray, [a], ['texto']))

    assert cierre_aislado['contexto'] == 'hablamos del presupuesto de Aramco'


def test_el_fichero_de_contexto_se_consume(tray, cierre_aislado, tr_dirs, wav_factory):
    """Si no se borrara, el contexto de una reunion se colaria en la siguiente
    que reutilizara el nombre."""
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)
    contexto = a.with_suffix('.context')
    contexto.write_text('algo', encoding='utf-8')

    tray._finalize_session(_sesion(tray, [a], ['texto']))

    assert not contexto.exists()


def test_el_contexto_tambien_se_busca_en_processed(tray, cierre_aislado, tr_dirs,
                                                   wav_factory):
    """Al reprocesar un WAV ya movido, el .context esta en processed/."""
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)
    en_processed = ta.RECORDINGS_DIR / 'processed' / f'{STEM_A}.context'
    en_processed.write_text('contexto guardado', encoding='utf-8')

    tray._finalize_session(_sesion(tray, [a], ['texto']))

    assert cierre_aislado['contexto'] == 'contexto guardado'


def test_un_contexto_vacio_no_se_pasa_como_contexto(tray, cierre_aislado, tr_dirs,
                                                    wav_factory):
    a = wav_factory(ta.RECORDINGS_DIR / f'{STEM_A}.wav', seconds=0.1)
    a.with_suffix('.context').write_text('   \n  ', encoding='utf-8')

    tray._finalize_session(_sesion(tray, [a], ['texto']))

    assert cierre_aislado['contexto'] is None


def test_cerrar_ahora_cancela_el_temporizador(tray, sin_hilos, tr_dirs):
    """El usuario pulsa 'no espero mas' y la reunion se cierra ya."""
    tray._register_part_recorded(ta.RECORDINGS_DIR / f'{STEM_A}.wav')
    temporizador = sin_hilos()
    temporizador.iniciado = True
    tray._session['timer'] = temporizador

    tray.finalize_pending_now()

    assert temporizador.iniciado is False


def test_cerrar_ahora_sin_sesion_no_lanza(tray, sin_hilos):
    tray.finalize_pending_now()


def test_programar_el_cierre_cancela_el_anterior(tray, sin_hilos, tr_dirs):
    """Cada parte nueva reinicia la cuenta atras: si no se cancelara el
    anterior, se cerraria antes de tiempo."""
    tray._register_part_recorded(ta.RECORDINGS_DIR / f'{STEM_A}.wav')
    sess = tray._session

    tray._schedule_finalize(sess)
    primero = sess['timer']
    tray._schedule_finalize(sess)

    assert sess['timer'] is not primero
    assert primero.iniciado is False


def test_el_margen_de_espera_es_de_minuto_y_medio(tray):
    """Suficiente para una reconexion real, poco para que el usuario crea que
    la app se colgo."""
    assert tray._MERGE_GRACE == 90


def test_la_sesion_se_protege_con_un_lock(tray):
    """El detector, el pipeline y el temporizador la tocan desde tres hilos."""
    assert isinstance(tray._session_lock, type(threading.RLock()))
