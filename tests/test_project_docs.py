"""Memoria del proyecto: recolectar documentos y extraer su texto.

Es lo que se le pasa al modelo como contexto de fondo para que use la
terminologia y las siglas del proyecto. Si la recoleccion se desmadra, se
cuelga el pipeline de la reunion; si se queda corta, las minutas pierden
precision sin que nadie sepa por que.

Los limites (tamano por fichero, numero de ficheros, caracteres extraidos) son
lo mas importante de cubrir: son lo que impide que una carpeta de proyecto con
un dataset dentro bloquee la generacion de minutas.
"""
import pytest

import project_context as pc

pytestmark = pytest.mark.unit


# ── Extraer texto segun el tipo de fichero ───────────────────────────────

@pytest.mark.parametrize('ext', ['.txt', '.md', '.csv'])
def test_los_ficheros_de_texto_se_leen_tal_cual(tr_dirs, ext):
    f = tr_dirs / f'doc{ext}'
    f.write_text('contenido del documento', encoding='utf-8')
    assert pc._extract_file_text(f) == 'contenido del documento'


def test_el_texto_extraido_se_corta_al_limite(tr_dirs):
    """Sin el corte, un CSV de 40 MB entraria entero en el prompt."""
    f = tr_dirs / 'grande.txt'
    f.write_text('A' * (pc._MAX_PER_FILE * 2), encoding='utf-8')
    assert len(pc._extract_file_text(f)) == pc._MAX_PER_FILE


def test_un_fichero_con_bytes_invalidos_no_revienta(tr_dirs):
    """errors='ignore': un .txt con codificacion rara no puede tumbar la
    preparacion del contexto."""
    f = tr_dirs / 'raro.txt'
    f.write_bytes(b'texto valido \xff\xfe y basura')
    assert 'texto valido' in pc._extract_file_text(f)


def test_una_extension_no_soportada_devuelve_vacio(tr_dirs):
    f = tr_dirs / 'binario.exe'
    f.write_bytes(b'MZ')
    assert pc._extract_file_text(f) == ''


def test_un_fichero_ilegible_devuelve_vacio_y_no_lanza(tr_dirs):
    """Un .docx corrupto o un fichero bloqueado por Word: se salta, no se
    pierde el contexto entero."""
    f = tr_dirs / 'corrupto.docx'
    f.write_bytes(b'esto no es un docx')
    assert pc._extract_file_text(f) == ''


def test_un_fichero_inexistente_devuelve_vacio(tr_dirs):
    assert pc._extract_file_text(tr_dirs / 'no_existe.md') == ''


# ── Nombre seguro del fichero convertido ─────────────────────────────────

@pytest.mark.parametrize('entrada, esperado', [
    ('documento simple', 'documento simple.txt'),
    ('con:dos puntos', 'con_dos puntos.txt'),
    ('con*asteriscos?', 'con_asteriscos_.txt'),
    ('con|tuberia', 'con_tuberia.txt'),
])
def test_safe_name_limpia_los_caracteres_prohibidos(tr_dirs, entrada, esperado):
    """Solo se prueban caracteres que pueden aparecer en un stem: meter una
    barra dentro de un Path haria que Python la tratara como separador, y
    .stem seria solo el ultimo tramo."""
    assert pc._safe_name(tr_dirs / f'{entrada}.pdf') == esperado


def test_safe_name_corta_los_nombres_largos(tr_dirs):
    """Windows tiene limite de ruta: un nombre de 300 caracteres lo revienta."""
    nombre = pc._safe_name(tr_dirs / ('A' * 300 + '.pdf'))
    assert len(nombre) <= 94        # 90 + '.txt'


def test_dos_ficheros_con_el_mismo_nombre_no_se_pisan(tr_dirs):
    """Pasa de verdad: 'propuesta.pdf' y 'propuesta.docx' en carpetas
    distintas del mismo proyecto."""
    a = tr_dirs / 'a' / 'propuesta.pdf'
    b = tr_dirs / 'b' / 'propuesta.docx'

    tomados = set()
    n1 = pc._unique_out_name(a, tomados)
    tomados.add(n1)
    n2 = pc._unique_out_name(b, tomados)

    assert n1 != n2
    assert n2.endswith('_2.txt')


def test_el_desempate_sigue_contando_si_ya_hay_varios(tr_dirs):
    f = tr_dirs / 'doc.pdf'
    tomados = {'doc.txt', 'doc_2.txt', 'doc_3.txt'}
    assert pc._unique_out_name(f, tomados) == 'doc_4.txt'


# ── Recolectar ficheros ──────────────────────────────────────────────────

@pytest.fixture
def carpeta_con_documentos(tr_dirs):
    d = tr_dirs / 'docs_del_proyecto'
    (d / 'subcarpeta').mkdir(parents=True)
    (d / 'uno.md').write_text('uno', encoding='utf-8')
    (d / 'subcarpeta' / 'dos.txt').write_text('dos', encoding='utf-8')
    (d / 'ignorame.exe').write_bytes(b'MZ')
    (d / '~$temporal.docx').write_bytes(b'basura')
    return d


def test_recolecta_en_profundidad(carpeta_con_documentos):
    nombres = {f.name for f in pc._collect_files([carpeta_con_documentos])}
    assert nombres == {'uno.md', 'dos.txt'}


def test_se_ignoran_las_extensiones_no_soportadas(carpeta_con_documentos):
    nombres = {f.name for f in pc._collect_files([carpeta_con_documentos])}
    assert 'ignorame.exe' not in nombres


def test_se_ignoran_los_temporales_de_office(carpeta_con_documentos):
    """Word deja '~$documento.docx' abiertos y bloqueados mientras editas."""
    nombres = {f.name for f in pc._collect_files([carpeta_con_documentos])}
    assert not any(n.startswith('~$') for n in nombres)


def test_una_carpeta_inexistente_se_salta(tr_dirs, carpeta_con_documentos):
    ficheros = pc._collect_files([tr_dirs / 'no_existe', carpeta_con_documentos])
    assert len(ficheros) == 2


def test_un_fichero_pasado_como_carpeta_se_salta(tr_dirs, carpeta_con_documentos):
    suelto = tr_dirs / 'suelto.md'
    suelto.write_text('x', encoding='utf-8')
    assert len(pc._collect_files([suelto, carpeta_con_documentos])) == 2


def test_sin_carpetas_no_hay_ficheros():
    assert pc._collect_files([]) == []
    assert pc._collect_files(None) == []


def test_un_fichero_demasiado_grande_se_salta(tr_dirs, monkeypatch):
    """Es lo que impide que un dataset dentro de la carpeta del proyecto
    bloquee la generacion de minutas."""
    d = tr_dirs / 'docs'
    d.mkdir()
    grande = d / 'dataset.csv'
    grande.write_text('x', encoding='utf-8')

    monkeypatch.setattr(pc, '_MAX_FILE_SIZE', 0)

    assert pc._collect_files([d]) == []


def test_se_respeta_el_maximo_de_ficheros(tr_dirs, monkeypatch):
    d = tr_dirs / 'muchos'
    d.mkdir()
    for i in range(10):
        (d / f'f{i}.md').write_text('x', encoding='utf-8')

    monkeypatch.setattr(pc, '_MAX_FILES', 3)

    assert len(pc._collect_files([d])) == 3


def test_la_recoleccion_se_puede_cancelar(tr_dirs):
    """Sin esto, sincronizar una carpeta enorme no se podria abortar desde el
    panel."""
    d = tr_dirs / 'muchos'
    d.mkdir()
    for i in range(20):
        (d / f'f{i}.md').write_text('x', encoding='utf-8')

    vistos = {'n': 0}

    def cancelar_a_las_cinco():
        vistos['n'] += 1
        return vistos['n'] > 5

    ficheros = pc._collect_files([d], should_cancel=cancelar_a_las_cinco)

    assert len(ficheros) < 20


# ── Sincronizar la memoria del proyecto ──────────────────────────────────

@pytest.fixture
def proyecto(tr_dirs, carpeta_con_documentos):
    return {'id': 'contoso', 'name': 'Contoso',
            'context_dirs': [str(carpeta_con_documentos)]}


def test_sincronizar_convierte_cada_documento_a_texto(proyecto, tr_dirs):
    n = pc.sync_project_docs(proyecto)

    assert n == 2
    convertidos = {p.name for p in (pc.project_docs_dir('contoso') / 'docs').iterdir()}
    assert convertidos == {'uno.txt', 'dos.txt'}


def test_el_texto_convertido_lleva_el_contenido(proyecto):
    pc.sync_project_docs(proyecto)
    texto = (pc.project_docs_dir('contoso') / 'docs' / 'uno.txt').read_text(
        encoding='utf-8')
    assert 'uno' in texto


def test_sincronizar_informa_del_progreso(proyecto):
    """El panel pinta una barra con esto: sin avisos, parece colgado."""
    avisos = []
    pc.sync_project_docs(proyecto, progress_cb=lambda *a: avisos.append(a))
    assert avisos


def test_sincronizar_se_puede_cancelar(proyecto):
    n = pc.sync_project_docs(proyecto, should_cancel=lambda: True)
    assert n == 0


def test_un_proyecto_sin_carpetas_no_sincroniza_nada(tr_dirs):
    assert pc.sync_project_docs({'id': 'vacio', 'name': 'V'}) == 0


def test_sincronizar_dos_veces_no_duplica(proyecto):
    pc.sync_project_docs(proyecto)
    pc.sync_project_docs(proyecto)

    convertidos = list((pc.project_docs_dir('contoso') / 'docs').iterdir())
    assert len(convertidos) == 2


# ── Preparar el contexto para una reunion ────────────────────────────────

def test_sin_proyectos_no_hay_contexto(tr_dirs):
    proyecto, directorio = pc.prepare_context('transcript de la reunion', 'Comite')
    assert proyecto is None
    assert directorio is None


def test_con_un_proyecto_detectado_se_devuelve_su_carpeta(tr_dirs):
    import json
    (tr_dirs / 'projects.json').write_text(json.dumps({'projects': [
        {'id': 'contoso', 'name': 'Contoso'},
    ]}), encoding='utf-8')
    docs = pc.project_docs_dir('contoso')
    (docs / 'docs').mkdir(parents=True)
    (docs / 'docs' / 'x.txt').write_text('contexto', encoding='utf-8')

    proyecto, directorio = pc.prepare_context(
        'hablamos del proyecto Contoso y sus fases', 'Contoso Kickoff')

    assert proyecto is not None and proyecto['id'] == 'contoso'
    assert directorio == str(docs)


def test_un_proyecto_sin_documentos_no_da_carpeta_de_contexto(tr_dirs):
    """Pasarle al modelo una carpeta vacia solo gasta tokens."""
    import json
    (tr_dirs / 'projects.json').write_text(json.dumps({'projects': [
        {'id': 'contoso', 'name': 'Contoso'},
    ]}), encoding='utf-8')

    proyecto, directorio = pc.prepare_context('proyecto Contoso', 'Contoso')

    assert directorio is None


# ── Detectar el proyecto por palabras clave ──────────────────────────────

PROYECTOS = [{'id': 'contoso', 'name': 'Contoso Agentic AI'},
             {'id': 'campo', 'name': 'Campo App'}]


def test_detecta_el_proyecto_por_su_nombre():
    p = pc.detect_project('hablamos del avance de Contoso Agentic AI', '', PROYECTOS)
    assert p['id'] == 'contoso'


def test_detecta_el_proyecto_por_el_nombre_de_la_reunion():
    p = pc.detect_project('texto sin pistas', 'Kickoff Campo App', PROYECTOS)
    assert p['id'] == 'campo'


def test_sin_coincidencia_no_detecta_nada():
    assert pc.detect_project('reunion sobre cualquier otra cosa', '', PROYECTOS) is None


def test_sin_proyectos_no_detecta_nada():
    assert pc.detect_project('Contoso', '', []) is None


def test_norm_pone_en_minusculas_y_conserva_los_espacios():
    """Distinto de tray_app._norm_key, que si los quita. Aqui se conservan
    porque la deteccion busca el nombre del proyecto como frase dentro del
    transcript."""
    assert pc._norm('Contoso-Agentic AI!') == 'contoso agentic ai'
