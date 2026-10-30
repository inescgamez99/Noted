"""Emparejado de la reunion con el calendario y generacion del email.

Lo que protege: `_name_score` gobierna el umbral de 0,5 que decide A QUIEN se
le mandan tus minutas. Un fallo aqui manda las notas de una reunion a los
participantes de otra, y eso no se puede deshacer.

Y un invariante de seguridad: la app crea BORRADORES con Display(). Nunca
llama a Send(). El `fake_outlook` del conftest tiene un Send() que lanza a
proposito, asi que cualquier test que lo llamara fallaria a gritos.

Cero COM: se sustituye outlook_sender._get_outlook, el unico seam del repo.
"""
from datetime import datetime, timedelta, UTC

import pytest

import outlook_sender as os_mod

pytestmark = pytest.mark.unit


# ── Normalizacion del asunto ─────────────────────────────────────────────

@pytest.mark.parametrize('entrada, esperado', [
    ('Comite_Semanal - Q4!', 'comite semanal q4'),
    ('Daily|Scrum', 'daily scrum'),
    ('  ESPACIOS   DE   MAS  ', 'espacios de mas'),
    ('Reunión de diseño', 'reunión de diseño'),      # los acentos sobreviven
    ('', ''),
    (None, ''),
])
def test_norm_subject(entrada, esperado):
    assert os_mod._norm_subject(entrada) == esperado


# ── El score que decide a quien se le manda ──────────────────────────────

def test_un_nombre_vacio_puntua_cero():
    assert os_mod._name_score('', 'Comite Semanal') == 0.0
    assert os_mod._name_score('Comite Semanal', '') == 0.0


def test_la_contencion_puntua_uno_en_cualquier_direccion():
    """Teams acorta o alarga el titulo respecto al del calendario."""
    assert os_mod._name_score('Comite Semanal', 'Comite Semanal Q4 2026') == 1.0
    assert os_mod._name_score('Comite Semanal Q4 2026', 'Comite Semanal') == 1.0


def test_el_mismo_nombre_con_otro_formato_puntua_uno():
    assert os_mod._name_score('Comite_Semanal', 'Comite - Semanal') == 1.0


def test_dos_reuniones_distintas_puntuan_por_debajo_del_umbral():
    """Es el caso que evita mandar las minutas a quien no le toca."""
    assert os_mod._name_score('Comite de Direccion', 'Retro del Sprint') < 0.5


def test_el_score_siempre_esta_entre_cero_y_uno():
    pares = [('a', 'b'), ('Comite', 'Comite'), ('x' * 50, 'y' * 50),
             ('Daily Scrum', 'Retro Sprint'), ('Contoso', 'Contoso Fase 2')]
    for a, b in pares:
        assert 0.0 <= os_mod._name_score(a, b) <= 1.0


def test_solapamiento_parcial_de_palabras_largas_puntua():
    score = os_mod._name_score('Revision presupuesto anual',
                               'Revision presupuesto trimestral')
    assert score >= 0.5


def test_DEFECTO_las_palabras_vacias_pueden_emparejar_dos_reuniones_distintas():
    """DEFECTO ENCONTRADO AL MEDIRLO (18/09/2026), y de los caros.

    El solapamiento SI descarta las palabras de 3 letras o menos, pero el score
    final es el MAXIMO entre ese solapamiento y la ratio de difflib, que compara
    las cadenas enteras. Con 'Comite de la Direccion' vs 'Retro de la Semana' la
    ratio da exactamente 0.5, y el umbral de find_meeting_participants es
    `score >= 0.5`: EMPAREJA.

    Consecuencia: las minutas de una reunion pueden mandarse a los
    participantes de otra. Es un borrador, asi que hay una persona mirando
    antes de darle a enviar, pero la lista de destinatarios ya viene mal.

    Se fija el comportamiento actual. El arreglo razonable seria exigir que el
    solapamiento de palabras largas aporte algo, o subir el umbral."""
    score = os_mod._name_score('Comite de la Direccion', 'Retro de la Semana')
    assert score == 0.5, f"score {score:.3f}: si bajo de 0.5, el defecto se arreglo"

    # El caso sin palabras vacias compartidas si queda por debajo del umbral.
    assert os_mod._name_score('Comite de Direccion', 'Retro del Sprint') < 0.5


# ── Emparejado contra el calendario ──────────────────────────────────────

class CitaFalsa:
    def __init__(self, asunto, inicio, destinatarios=()):
        self.Subject = asunto
        self.Start = inicio
        self.Recipients = [
            type('R', (), {'Name': n, 'Address': e})() for n, e in destinatarios
        ]


AHORA = datetime(2026, 9, 17, 16, 0)


def test_sin_citas_en_el_calendario_no_hay_participantes(fake_outlook):
    assert os_mod.find_meeting_participants(AHORA, 'Comite') == []


def test_devuelve_los_participantes_de_la_cita(fake_outlook):
    fake_outlook.appointments = [
        CitaFalsa('Comite Semanal', AHORA,
                  [('Ines Mollet', 'ines@x.com'), ('Felipe', 'felipe@x.com')]),
    ]

    participantes = os_mod.find_meeting_participants(AHORA, 'Comite Semanal')

    assert [p['email'] for p in participantes] == ['ines@x.com', 'felipe@x.com']
    assert participantes[0]['name'] == 'Ines Mollet'


def test_el_nombre_gana_a_la_hora(fake_outlook):
    """Con dos reuniones solapadas, la que importa es la que se llama igual,
    no la que esta mas cerca en el reloj."""
    fake_outlook.appointments = [
        CitaFalsa('Otra Cosa', AHORA, [('X', 'x@x.com')]),
        CitaFalsa('Comite Semanal', AHORA + timedelta(minutes=30),
                  [('Ines', 'ines@x.com')]),
    ]

    participantes = os_mod.find_meeting_participants(AHORA, 'Comite Semanal')

    assert [p['email'] for p in participantes] == ['ines@x.com']


def test_sin_nombre_se_empareja_por_proximidad_de_hora(fake_outlook):
    """Una grabacion manual no tiene nombre de reunion: se usa el reloj."""
    fake_outlook.appointments = [
        CitaFalsa('Lejana', AHORA + timedelta(hours=2), [('Lejos', 'lejos@x.com')]),
        CitaFalsa('Cercana', AHORA + timedelta(minutes=2), [('Cerca', 'cerca@x.com')]),
    ]

    participantes = os_mod.find_meeting_participants(AHORA)

    assert [p['email'] for p in participantes] == ['cerca@x.com']


def test_si_ningun_nombre_alcanza_el_umbral_se_cae_a_la_hora(fake_outlook):
    fake_outlook.appointments = [
        CitaFalsa('Nada Que Ver', AHORA + timedelta(minutes=1), [('X', 'x@x.com')]),
    ]

    participantes = os_mod.find_meeting_participants(AHORA, 'Comite de Direccion')

    assert [p['email'] for p in participantes] == ['x@x.com']


def test_un_fallo_de_com_devuelve_lista_vacia_y_no_lanza(monkeypatch):
    """Outlook cerrado o COM caido no puede tumbar el pipeline: las minutas
    ya estan generadas y hay que poder guardarlas."""
    def boom():
        raise OSError('Outlook no responde')

    monkeypatch.setattr(os_mod, '_get_outlook', boom)
    assert os_mod.find_meeting_participants(AHORA, 'Comite') == []


def test_un_destinatario_ilegible_no_descarta_a_los_demas(fake_outlook):
    class DestinatarioRoto:
        @property
        def Name(self):
            raise OSError('COM error')

    cita = CitaFalsa('Comite', AHORA, [('Ines', 'ines@x.com')])
    cita.Recipients = [DestinatarioRoto(), *cita.Recipients]
    fake_outlook.appointments = [cita]

    participantes = os_mod.find_meeting_participants(AHORA, 'Comite')

    assert [p['email'] for p in participantes] == ['ines@x.com']


# ── Asistentes: regresiones del 20/10/2026 ───────────────────────────────

def _rec(nombre, email, tipo=1, respuesta=0):
    return type('R', (), {'Name': nombre, 'Address': email, 'Type': tipo,
                          'MeetingResponseStatus': respuesta})()


def test_REGRESION_las_fechas_de_outlook_llevan_zona_horaria(fake_outlook):
    """pywin32 devuelve Start con tzinfo (+00:00). Restarla de una fecha sin zona
    lanzaba TypeError, un `except: continue` lo tragaba, y NINGUNA reunión
    encontró jamás a sus invitados (60 de 60 en noted.log)."""
    fake_outlook.appointments = [
        CitaFalsa('Comite Semanal', AHORA.replace(tzinfo=UTC),
                  [('Ines', 'ines@x.com')]),
    ]

    participantes = os_mod.find_meeting_participants(AHORA, 'Comite Semanal')

    assert [p['email'] for p in participantes] == ['ines@x.com']


def test_una_reunion_en_curso_se_empareja_aunque_empezara_hace_horas(fake_outlook):
    """Entras a las 16:00 a una reunión de 14:00-17:00: la ventana tiene que
    mirar hacia atrás, y por hora está a distancia 0."""
    cita = CitaFalsa('Sin Nombre Util', AHORA - timedelta(hours=2), [('A', 'a@x.com')])
    cita.End = AHORA + timedelta(hours=1)
    fake_outlook.appointments = [cita]

    assert [p['email'] for p in os_mod.find_meeting_participants(AHORA)] == ['a@x.com']


def test_REGRESION_la_que_empieza_ahora_gana_a_la_reunion_anterior_aun_en_curso(fake_outlook):
    """13:00-14:00 sigue 'en curso' a las 14:00, pero la que se graba es la que
    EMPIEZA a las 14:00. Con las dos a distancia 0 ganaba la anterior y las
    minutas salían con los invitados de otra reunión."""
    anterior = CitaFalsa('Reunion de las 13', AHORA - timedelta(hours=1), [('A', 'a@x.com')])
    anterior.End = AHORA
    actual = CitaFalsa('Reunion de las 14', AHORA, [('B', 'b@x.com')])
    actual.End = AHORA + timedelta(hours=1)
    fake_outlook.appointments = [anterior, actual]

    assert [p['email'] for p in os_mod.find_meeting_participants(AHORA)] == ['b@x.com']


def test_por_hora_no_se_acepta_una_cita_lejana(fake_outlook):
    """Antes se cogía la 'más cercana' aunque empezase 2 h después, y se le
    atribuían los invitados de otra reunión."""
    fake_outlook.appointments = [
        CitaFalsa('Otra', AHORA + timedelta(hours=2), [('X', 'x@x.com')]),
    ]

    assert os_mod.find_meeting_participants(AHORA) == []


def test_los_eventos_de_todo_el_dia_no_cuentan(fake_outlook):
    todo_el_dia = CitaFalsa('Vacaciones de Marta', AHORA, [('Marta', 'm@x.com')])
    todo_el_dia.AllDayEvent = True
    fake_outlook.appointments = [todo_el_dia]

    assert os_mod.find_meeting_participants(AHORA) == []


def test_se_descartan_las_salas_y_se_guarda_rol_y_respuesta(fake_outlook):
    cita = CitaFalsa('Comite', AHORA)
    cita.Recipients = [
        _rec('Maestripieri, Francesca', 'f@x.com', tipo=1, respuesta=3),
        _rec('Sala Madrid 3', 'sala@x.com', tipo=3, respuesta=3),
        _rec('Opcional', 'o@x.com', tipo=2, respuesta=5),
    ]
    cita.Organizer = 'Maestripieri, Francesca'
    fake_outlook.appointments = [cita]

    gente = os_mod.find_meeting_participants(AHORA, 'Comite')

    assert [p['name'] for p in gente] == ['Francesca Maestripieri', 'Opcional']
    assert gente[0]['role'] == 'organizer' and gente[0]['response'] == 'accepted'
    assert gente[1]['role'] == 'optional' and gente[1]['response'] == 'no response'
    assert gente[0]['meeting_subject'] == 'Comite'


@pytest.mark.parametrize('crudo, limpio', [
    ('Campos Gamez, Inés', 'Inés Campos Gamez'),
    ('Ines Mollet', 'Ines Mollet'),
    ('x@y.com', 'x@y.com'),
    ('', ''),
])
def test_clean_name(crudo, limpio):
    assert os_mod._clean_name(crudo) == limpio


# ── Markdown a HTML de email ─────────────────────────────────────────────

def test_escapar_protege_de_inyeccion():
    assert '<script>' not in os_mod._esc('<script>alert(1)</script>')
    assert os_mod._esc(None) == ''


def test_el_formato_inline_se_convierte():
    html = os_mod._inline('texto con **negrita**, *cursiva* y `codigo`')
    assert '<strong>negrita</strong>' in html
    assert '<em>cursiva</em>' in html
    assert '<code' in html


def test_la_negrita_se_procesa_antes_que_la_cursiva():
    """Si fuera al contrario, **x** saldria como <em>*x*</em>."""
    html = os_mod._inline('**solo negrita**')
    assert '<strong>solo negrita</strong>' in html
    assert '<em>' not in html


def test_dos_cursivas_en_la_misma_linea_no_se_fusionan():
    html = os_mod._inline('*una* y *otra*')
    assert html.count('<em>') == 2


def test_una_multiplicacion_produce_una_cursiva_falsa():
    """CASO LIMITE CONOCIDO: '2*3*4' parece cursiva. Queda fijado."""
    assert '<em>' in os_mod._inline('2*3*4')


def test_una_tabla_markdown_se_convierte_en_tabla_html():
    lineas = ['| Accion | Owner |', '|---|---|', '| Enviar | Felipe |']
    html = os_mod._md_table_to_html(lineas)

    assert '<table' in html
    assert '<th' in html and 'Accion' in html
    assert '<td' in html and 'Felipe' in html
    assert '---' not in html, "la fila separadora no debe salir"


def test_una_tabla_vacia_devuelve_cadena_vacia():
    assert os_mod._md_table_to_html([]) == ''


def test_DEFECTO_una_fila_con_menos_celdas_que_la_cabecera_no_se_rellena():
    """DEFECTO (outlook_sender.py:267): la guarda del relleno usa
    `ci < len(row)` iterando sobre enumerate(row), asi que es SIEMPRE cierta y
    nunca anade las celdas que faltan. La tabla del email sale desalineada.

    Se fija el comportamiento actual."""
    lineas = ['| A | B | C |', '|---|---|---|', '| solo una |']
    html = os_mod._md_table_to_html(lineas)

    filas_td = html.count('<td')
    assert filas_td == 1, f"{filas_td} celdas: si son 3, el relleno se arreglo"


def test_los_bloques_de_codigo_se_eliminan_del_email():
    """Las minutas llevan bloques de instrucciones para Claude. En un email al
    cliente no pintan nada."""
    md = ('## Resumen\n\nTexto normal.\n\n'
          '~~~instruction-for-claude\nactualizar el deck\n~~~\n\nMas texto.')
    html = os_mod._md_to_email_html(md)

    assert 'actualizar el deck' not in html
    assert 'Texto normal' in html
    assert 'Mas texto' in html


def test_las_listas_se_convierten_y_se_cierran():
    html = os_mod._md_to_email_html('- uno\n- dos\n\nparrafo suelto')
    assert html.count('<ul') == 1
    assert html.count('</ul>') == 1
    assert html.count('<li') == 2


def test_una_lista_al_final_del_texto_tambien_se_cierra():
    """Sin el cierre, el HTML queda malformado y algunos clientes de correo se
    comen el resto del mensaje."""
    html = os_mod._md_to_email_html('parrafo\n\n- uno\n- dos')
    assert html.count('<ul') == html.count('</ul>') == 1


@pytest.mark.parametrize('nivel, etiqueta', [('#', 'h2'), ('##', 'h2'), ('###', 'h3')])
def test_las_cabeceras_se_convierten(nivel, etiqueta):
    """'#' se mapea a h2 y no a h1 a proposito: un h1 en un email se ve
    desproporcionado."""
    html = os_mod._md_to_email_html(f'{nivel} Titulo')
    assert f'<{etiqueta}' in html


def test_todo_el_html_del_email_lleva_estilos_en_linea():
    """Los clientes de correo ignoran las hojas de estilo: sin estilos inline
    el email llega sin formato."""
    html = os_mod._md_to_email_html('## Titulo\n\n- uno\n')
    assert 'style=' in html


# ── Invariante de seguridad: borradores, nunca enviar ────────────────────

def test_el_email_de_minutas_se_queda_en_borrador(fake_outlook, tr_dirs):
    """Si alguna vez se llamara a Send(), el fake del conftest lanza y este
    test falla. Es la red que impide que la app mande un correo sola."""
    md = tr_dirs / 'minutes' / '20260917_1616_Comite.md'
    md.write_text('TITULO: Comite\n\n## Resumen\n\nTexto.', encoding='utf-8')

    html = md.with_suffix('.html')
    html.write_text('<html></html>', encoding='utf-8')

    os_mod.send_minutes_email(md, html, 'Comite',
                              [{'name': 'Ines', 'email': 'ines@x.com'}])

    assert fake_outlook.mails, "no se creo ningun borrador"
    assert fake_outlook.mails[0].displayed, "el borrador no se mostro"


def test_si_outlook_falla_el_envio_devuelve_false_y_no_lanza(monkeypatch, tr_dirs):
    md = tr_dirs / 'minutes' / '20260917_1616_Comite.md'
    md.write_text('TITULO: Comite\n\ncuerpo', encoding='utf-8')

    def boom():
        raise OSError('Outlook cerrado')

    html = md.with_suffix('.html')
    html.write_text('<html></html>', encoding='utf-8')

    monkeypatch.setattr(os_mod, '_get_outlook', boom)
    assert os_mod.send_minutes_email(md, html, 'Comite', []) is False
