"""Analisis de capturas dentro del transcript (screenshot_analysis.py).

Antes, una captura solo dejaba en el transcript la linea con el nombre del
archivo: ni las minutas ni las preguntas sabian que se veia en pantalla. Ahora
Claude mira cada captura con el transcript de alrededor y su analisis va en la
linea siguiente. Sin llamar a Claude: _ask_claude se sustituye por una
respuesta fija.
"""
import json

import pytest

import screenshot_analysis as sa

pytestmark = pytest.mark.unit

# El de verdad: el conftest lo sustituye en cada test para no llamar a Claude.
_ASK_CLAUDE = sa._ask_claude


@pytest.fixture
def grabacion(tmp_path):
    rec = tmp_path / '2026-11-20_10-00_Comite.wav'
    png = tmp_path / '2026-11-20_10-00_Comite_screenshot_12-30.png'
    png.write_bytes(b'\x89PNG fake')
    rec.with_suffix('.screenshots.jsonl').write_text(
        json.dumps({'file': png.name, 'offset_s': 750.0}) + '\n', encoding='utf-8')
    transcript = '\n'.join([
        '[12:20] [Otros]: os comparto los precios',
        f'[12:30] [Captura]: {png.name}',
        '[12:45] [Otros]: el piloto es caro',
        '[40:00] [Otros]: esto queda lejos de la captura',
    ])
    return rec, png, transcript


def test_el_analisis_va_detras_de_su_captura_y_no_se_repite(grabacion, monkeypatch):
    rec, png, transcript = grabacion
    llamadas = []

    def falso(folder, prompt):
        llamadas.append(prompt)
        return 'Aqui va: ' + json.dumps({png.name: 'Slide de pricing:\n piloto 180.000 EUR.'})
    monkeypatch.setattr(sa, '_ask_claude', falso)

    an = sa.analyze(rec, transcript, [(750, png.name)], 'es')
    assert an == {png.name: 'Slide de pricing: piloto 180.000 EUR.'}
    merged = sa.merge(transcript, an, 'Captura', 'es')
    lines = merged.splitlines()
    assert lines[1] == f'[12:30] [Captura]: {png.name}'       # la linea del contrato, intacta
    assert lines[2] == '[12:30] [Análisis de captura]: Slide de pricing: piloto 180.000 EUR.'
    assert sa.merge(merged, an, 'Captura', 'es') == merged       # no se repite

    # Segunda vez: sale del registro, sin volver a llamar a Claude.
    assert sa.analyze(rec, transcript, [(750, png.name)], 'es') == an
    assert len(llamadas) == 1
    # El prompt lleva el trozo de transcript de alrededor, no lo lejano.
    assert 'os comparto los precios' in llamadas[0]
    assert 'esto queda lejos' not in llamadas[0]


def test_si_claude_falla_el_transcript_queda_como_estaba(grabacion, monkeypatch):
    rec, png, transcript = grabacion
    monkeypatch.setattr(sa, '_ask_claude', lambda folder, prompt: None)
    an = sa.analyze(rec, transcript, [(750, png.name)], 'es')
    assert an == {}
    assert sa.merge(transcript, an, 'Captura', 'es') == transcript


def test_respuesta_sin_json_valido_no_rompe(grabacion, monkeypatch):
    rec, png, transcript = grabacion
    monkeypatch.setattr(sa, '_ask_claude', lambda folder, prompt: 'no puedo ver la imagen')
    assert sa.analyze(rec, transcript, [(750, png.name)], 'es') == {}


def test_se_puede_apagar_en_ajustes(grabacion, monkeypatch, tmp_path):
    rec, png, transcript = grabacion
    import config
    monkeypatch.setattr(config, 'PROJECT_DIR', tmp_path)
    (tmp_path / 'settings.json').write_text('{"screenshot_analysis": false}', encoding='utf-8')
    monkeypatch.setattr(sa, '_ask_claude', lambda *a: pytest.fail('no debe llamar a Claude'))
    assert sa.analyze(rec, transcript, [(750, png.name)], 'es') == {}


def test_en_ingles_la_etiqueta_es_la_inglesa():
    t = '[01:00] [Screenshot]: a.png'
    assert sa.merge(t, {'a.png': 'x'}, 'Screenshot', 'en').splitlines()[1] == \
        '[01:00] [Screenshot analysis]: x'


# ── En el procesado de la reunion y en las minutas ───────────────────────

def _reunion_con_captura(tr_dirs, texto):
    rec = tr_dirs / 'recordings' / '2026-11-20_10-00_Comite.wav'
    png = rec.with_name(f'{rec.stem}_screenshot_00-30.png')
    png.write_bytes(b'\x89PNG fake')
    rec.with_suffix('.screenshots.jsonl').write_text(
        json.dumps({'file': png.name, 'offset_s': 30.0}) + '\n', encoding='utf-8')
    transcript = rec.with_name(f'{rec.stem}_transcript.txt')
    transcript.write_text(texto, encoding='utf-8')
    return rec, png, transcript


def test_el_procesado_mete_el_analisis_en_el_transcript(tray, tr_dirs, monkeypatch):
    rec, png, transcript = _reunion_con_captura(tr_dirs, '[00:20] [Otros]: os enseño la slide')
    monkeypatch.setattr(sa, '_ask_claude',
                        lambda folder, prompt: json.dumps({png.name: 'Slide de precios.'}))

    texto = tray._merge_screenshots(rec, transcript, transcript.read_text(encoding='utf-8'), 'es')

    assert texto.splitlines()[-1].endswith('Slide de precios.')
    assert 'Slide de precios.' in transcript.read_text(encoding='utf-8')


def test_si_el_analisis_revienta_la_reunion_sigue(tray, tr_dirs, monkeypatch):
    rec, png, transcript = _reunion_con_captura(tr_dirs, '[00:20] [Otros]: hola')

    def roto(*_a, **_k):
        raise RuntimeError('fallo inesperado')
    monkeypatch.setattr(sa, 'analyze', roto)

    texto = tray._merge_screenshots(rec, transcript, '[00:20] [Otros]: hola', 'es')
    assert png.name in texto, "la captura entra igual, sin analisis"


def test_las_minutas_saben_que_el_analisis_no_es_un_hablante(tmp_path):
    import minutes_generator as mg
    wav = tmp_path / '2026-11-20_10-00_Comite.wav'
    t = ('[00:20] [Otros]: os enseño la slide\n'
         '[00:30] [Análisis de captura]: Slide de precios: piloto 180.000 EUR.')
    assert 'no son intervenciones' in mg._build_prompt(t, wav, language='es')
    assert 'are not speech' in mg._build_prompt(t, wav, language='en')
    assert 'Capturas en el transcript' not in mg._build_prompt('[00:20] [Otros]: hola', wav, language='es')


# ── La llamada al CLI de Claude (sin lanzarlo) ───────────────────────────

@pytest.fixture
def cli(monkeypatch, tmp_path):
    """Deshace el doble del conftest y sustituye subprocess.run."""
    import subprocess

    import config
    monkeypatch.setattr(sa, '_ask_claude', _ASK_CLAUDE)
    monkeypatch.setattr(config, 'get_claude_bin', lambda: 'claude.exe')
    llamadas = []

    def run(respuesta):
        def falso(cmd, **kw):
            llamadas.append((cmd, kw))
            if isinstance(respuesta, BaseException):
                raise respuesta
            return respuesta
        monkeypatch.setattr(subprocess, 'run', falso)
    run.llamadas = llamadas
    return run


def test_claude_puede_leer_la_carpeta_de_las_capturas_y_nada_mas(cli, tmp_path):
    import subprocess
    cli(subprocess.CompletedProcess([], 0, stdout='{"a.png": "x"}', stderr=''))
    assert sa._ask_claude(tmp_path, 'mira') == '{"a.png": "x"}'
    cmd, kw = cli.llamadas[0]
    assert cmd == ['claude.exe', '-p', '--add-dir', str(tmp_path), '--allowedTools', 'Read']
    assert kw['input'] == 'mira'


@pytest.mark.parametrize('respuesta', [
    'rc', 'timeout', 'excepcion',
])
def test_si_claude_falla_no_hay_analisis(cli, tmp_path, respuesta):
    import subprocess
    cli({'rc': subprocess.CompletedProcess([], 1, stdout='', stderr='sin sesion'),
         'timeout': subprocess.TimeoutExpired('claude', 1),
         'excepcion': OSError('no se puede lanzar')}[respuesta])
    assert sa._ask_claude(tmp_path, 'mira') is None


def test_sin_claude_instalado_no_hay_analisis(cli, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, 'get_claude_bin', lambda: None)
    assert sa._ask_claude(tmp_path, 'mira') is None
    assert cli.llamadas == []


# ── Casos raros ──────────────────────────────────────────────────────────

def test_el_contexto_no_incluye_otros_analisis():
    t = ('[01:00] [Otros]: primera\n'
         '[01:00] [Análisis de captura]: analisis viejo\n'
         '[01:10] [Otros]: segunda')
    trozo = sa._excerpt(t, 60)
    assert 'primera' in trozo and 'segunda' in trozo and 'analisis viejo' not in trozo


@pytest.mark.parametrize('salida', ['', 'nada', '{roto', '["lista"]', '{"a.png": 3}'])
def test_respuestas_sin_analisis_validos(salida):
    assert sa._parse(salida) == {}


def test_sin_registro_de_capturas_no_se_guarda_nada(tmp_path):
    rec = tmp_path / 'sin_registro.wav'
    sa._store(rec, {'a.png': 'x'})
    assert not rec.with_suffix('.screenshots.jsonl').exists()


def test_si_no_se_puede_guardar_el_analisis_se_usa_igual(grabacion, monkeypatch):
    rec, png, transcript = grabacion
    monkeypatch.setattr(sa, '_ask_claude', lambda folder, prompt: json.dumps({png.name: 'Slide.'}))

    def sin_disco(*_a):
        raise OSError('disco lleno')
    monkeypatch.setattr(sa, '_store', sin_disco)
    assert sa.analyze(rec, transcript, [(750, png.name)], 'es') == {png.name: 'Slide.'}
