"""Integración Semantic Layer (OntoForge).

Genera el "input de ontología" (CSV entidad/relación) de una reunión llamando a
la skill `knowledge-capture` vía `claude -p`. El SKILL.md está bundled en el repo
(exporters/semantic_layer/knowledge-capture/); no requiere instalación extra.

Todo lo relativo a esta integración vive aquí. `app_window.AppAPI` solo delega:
    from exporters import semantic_layer as semantic
    semantic.start_run(path, transcript_text, title)
    semantic.get_status(run_id)
    semantic.get_info(path)
    semantic.reveal_file(fp) / semantic.open_file(fp)
    semantic.pipeline_jobs()          # para el panel "En curso"
"""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

from config import RECORDINGS_DIR, CLAUDE_BIN, clean_env

log = logging.getLogger(__name__)

# run_id -> {pct, stage, done, error, csv_path, out_dir, output, title}
_semantic_runs: dict = {}
_MAX_RUNS = 50


def _prune(d: dict) -> None:
    """Elimina las entradas completadas más antiguas cuando el dict supera el límite."""
    if len(d) <= _MAX_RUNS:
        return
    done_keys = [k for k, v in d.items() if v.get('done')]
    for k in done_keys[:len(d) - _MAX_RUNS]:
        del d[k]


def skill_dir() -> Path | None:
    """Localiza la skill knowledge-capture.
    Primero busca la versión bundled en el repo (exporters/semantic_layer/knowledge-capture/),
    luego en ~/.claude/skills/ como fallback."""
    bundled = Path(__file__).parent / 'knowledge-capture'
    if (bundled / 'SKILL.md').exists():
        return bundled
    base = Path.home() / '.claude' / 'skills'
    for name in ('knowledge-capture', 'knowledgecapture', 'knowledge_capture'):
        candidate = base / name
        if (candidate / 'SKILL.md').exists():
            return candidate
    return None


def read_config(sdir: Path) -> dict:
    """Lee config.txt (scripts_path, data_path).
    Siempre busca primero en ~/.claude/skills/knowledge-capture/config.txt — esos
    paths son del usuario y no se commitean al repo aunque el SKILL.md sí."""
    cfg = {'scripts_path': '', 'data_path': ''}
    user_cfg = Path.home() / '.claude' / 'skills' / 'knowledge-capture' / 'config.txt'
    cfg_file = user_cfg if user_cfg.exists() else sdir / 'config.txt'
    try:
        if cfg_file.exists():
            for line in cfg_file.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                if k.strip() in cfg:
                    cfg[k.strip()] = v.strip()
    except Exception as e:
        log.warning(f"knowledge-capture config.txt: {e}")
    return cfg


def start_run(path: str, transcript_text: str, title: str = '', extra_docs=None) -> dict:
    """Lanza la generación de la capa semántica en segundo plano.

    `transcript_text` lo resuelve el llamador (AppAPI.get_transcript_text).
    `extra_docs` es una lista opcional de rutas a documentos adicionales que el usuario
    quiere incluir en la captura (además de la transcripción). Devuelve {ok, run_id}
    para hacer polling con get_status, o {ok: False, error} si no se puede empezar."""
    if not CLAUDE_BIN:
        return {'ok': False, 'error': 'no_claude'}

    sdir = skill_dir()
    if not sdir:
        return {'ok': False, 'error': 'no_skill'}

    md_path = Path(path)

    if not (transcript_text or '').strip():
        return {'ok': False, 'error': 'no_transcript'}

    stem = md_path.stem

    # 1) Transcript (fuente principal): localiza el fichero real para pasarlo como input
    transcript_file = None
    cand = md_path.parent / f"{stem}_transcript.txt"
    if cand.exists():
        transcript_file = cand
    else:
        for folder in (RECORDINGS_DIR / 'processed', RECORDINGS_DIR):
            for ext in ('_transcript.txt', '.txt'):
                c = folder / f"{stem}{ext}"
                if c.exists():
                    transcript_file = c
                    break
            if transcript_file:
                break

    # 2) Audio (fuente adicional, opcional)
    audio_file = None
    for folder in (RECORDINGS_DIR / 'processed', RECORDINGS_DIR):
        c = folder / f"{stem}.wav"
        if c.exists():
            audio_file = c
            break

    # 2b) Documentos extra aportados por el usuario (fuentes primarias adicionales)
    extra_files = []
    for d in (extra_docs or []):
        try:
            p = Path(d)
            if p.exists() and p.is_file() and p not in extra_files:
                extra_files.append(p)
        except Exception as e:
            log.warning(f"semantic_layer: documento extra inválido ({d!r}): {e}")

    meeting_name = (title or '').strip()
    if not meeting_name:
        _nm = re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(.+)', stem)
        meeting_name = _nm.group(1).replace('_', ' ') if _nm else stem

    run_id = uuid.uuid4().hex[:8]
    _prune(_semantic_runs)
    _semantic_runs[run_id] = {
        'pct': 5, 'stage': 'starting', 'done': False, 'error': '',
        'csv_path': '', 'out_dir': '', 'output': '', 'title': meeting_name,
    }
    log.info(f"semantic_layer: iniciando '{meeting_name}' (run {run_id})")

    def _run():
        import time as _time
        state = _semantic_runs[run_id]
        ticker = [True]

        def _tick():
            while ticker[0] and state['pct'] < 90:
                _time.sleep(4)
                if ticker[0] and state['pct'] < 90:
                    state['pct'] = min(90, state['pct'] + 1)
        threading.Thread(target=_tick, daemon=True).start()

        try:
            # 3) Memoria del proyecto (docs vinculados + resúmenes previos), opcional
            state['stage'] = 'context'
            context_dir = None
            try:
                from project_context import prepare_context
                m = re.match(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(.+)', stem)
                mtg_name = m.group(1).replace('_', ' ') if m else stem
                _proj, context_dir = prepare_context(transcript_text, mtg_name)
            except Exception as e:
                log.warning(f"semantic_layer: project context: {e}")

            # 4) Config de la skill (destino de escritura + scripts)
            cfg = read_config(sdir)
            data_path = cfg.get('data_path', '')
            scripts_path = cfg.get('scripts_path', '')

            # ---- prompt para invocar la skill ----
            inputs_lines = []
            if transcript_file:
                inputs_lines.append(f"- Transcript (cleaned, primary source): {transcript_file}")
            if audio_file:
                inputs_lines.append(f"- Audio recording (additional source): {audio_file}")
            for ef in extra_files:
                inputs_lines.append(
                    "- Additional document provided by the user (primary source — read it "
                    f"and fold its entities/relationships into the CSV): {ef}")
            if context_dir:
                inputs_lines.append(
                    "- Project memory folder (linked documents in `docs/`, previous meeting "
                    "summaries in `meetings/`) — additional sources; treat the documents as "
                    f"primary sources and fold any previous ontology into the CSV: {context_dir}")

            today = datetime.now().strftime('%Y-%m-%d')
            skill_instructions = (sdir / 'SKILL.md').read_text(encoding='utf-8')
            prompt = (
                f"{skill_instructions}\n\n"
                "---\n\n"
                "Follow the skill instructions above to process this meeting. "
                "The transcript already exists on disk — do NOT re-record or re-transcribe.\n\n"
                f"Meeting: {stem}\n"
                f"Date: {today}\n\n"
                "Inputs already on disk:\n"
                + "\n".join(inputs_lines) + "\n\n"
                "Create the meeting folder under data_path, copy the inputs into its `inputs/` "
                "subfolder, consolidate ALL of them into ONE deduplicated CSV, and save the "
                "readable record. When finished, print the final report including the exact "
                "absolute path of the generated `*_ontology_input.csv`."
            )

            add_dirs = {str(md_path.parent)}  # siempre incluir la carpeta de minutas
            for p in (transcript_file, audio_file, *extra_files):
                if p:
                    add_dirs.add(str(p.parent))
            if context_dir:
                add_dirs.add(str(context_dir))
            if data_path:
                add_dirs.add(data_path)
            if scripts_path:
                add_dirs.add(scripts_path)

            cmd = [CLAUDE_BIN, '-p', '--allowedTools', 'Read,Write,Edit,Bash,Glob,Grep']
            for d in add_dirs:
                cmd += ['--add-dir', d]

            state['stage'] = 'processing'
            state['pct'] = 15
            env = clean_env()
            cwd = data_path if data_path else str(md_path.parent)
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', cwd=cwd, env=env,
                creationflags=0x08000000 if os.name == 'nt' else 0,
            )
            try:
                out, err = proc.communicate(input=prompt, timeout=1800)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.communicate()
                ticker[0] = False
                state.update({'error': 'timeout', 'done': True, 'pct': 100, 'stage': 'error'})
                log.error("semantic_layer: timeout (1800s)")
                return

            ticker[0] = False
            out = out or ''
            state['output'] = out[-4000:]
            if proc.returncode != 0:
                state.update({'error': f"exit {proc.returncode}: {(err or '')[:300]}",
                              'done': True, 'pct': 100, 'stage': 'error'})
                log.error(f"semantic_layer: claude -p rc={proc.returncode}: {(err or '')[:300]}")
                return

            csv_path = ''
            mm = re.search(r'([A-Za-z]:\\[^\r\n"]*?_ontology_input\.csv)', out)
            if not mm:
                mm = re.search(r'(/[^\r\n"]*?_ontology_input\.csv)', out)
            if mm:
                csv_path = mm.group(1).strip()
            state['csv_path'] = csv_path
            state['out_dir'] = str(Path(csv_path).parent) if csv_path else (data_path or '')

            # Copiar los outputs a la carpeta de la reunión (junto a las minutas)
            # para tenerlos todos en un sitio y poder subirlos fácil a OntoForge.
            local_csv = local_record = inputs_dir = ''
            if csv_path:
                try:
                    src_csv = Path(csv_path)
                    mid = src_csv.stem.removesuffix('_ontology_input')
                    inputs_dir = str(src_csv.parent / 'inputs')
                    dest_csv = md_path.with_name(f"{md_path.stem}_ontology_input.csv")
                    shutil.copy2(src_csv, dest_csv)
                    local_csv = str(dest_csv)
                    # busca el Word doc generado por el skill (preferencia: .docx, fallback: .html)
                    for rec_name in (f"{mid}_record.docx", f"{mid}_record.html"):
                        src_record = src_csv.parent / rec_name
                        if src_record.exists():
                            ext = src_record.suffix
                            dest_record = md_path.with_name(f"{md_path.stem}_ontology_record{ext}")
                            shutil.copy2(src_record, dest_record)
                            local_record = str(dest_record)
                            break
                except Exception as e:
                    log.warning(f"semantic_layer: copiar outputs: {e}")

            # Sidecar con la info para la pestaña Semantic Layer
            try:
                info = {
                    'generated_at':  datetime.now().isoformat(timespec='seconds'),
                    'meeting_title': meeting_name,
                    'source_dir':    state['out_dir'],
                    'csv_source':    csv_path,
                    'csv_local':     local_csv,
                    'record_local':  local_record,
                    'inputs_dir':    inputs_dir,
                }
                md_path.with_name(f"{md_path.stem}.semantic.json").write_text(
                    json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception as e:
                log.warning(f"semantic_layer: sidecar: {e}")

            state.update({'done': True, 'pct': 100, 'stage': 'done'})
            log.info(f"semantic_layer: OK csv={csv_path or '(no detectado)'} local={local_csv or '-'}")

        except Exception as e:
            ticker[0] = False
            state.update({'error': str(e), 'done': True, 'pct': 100, 'stage': 'error'})
            log.error(f"semantic_layer thread: {e}")

    threading.Thread(target=_run, daemon=True, name=f'SemanticLayer-{run_id}').start()
    return {'ok': True, 'run_id': run_id}


def get_status(run_id: str) -> dict:
    """Progreso de una ejecución: {pct, stage, done, error, csv_path, out_dir, output}."""
    return _semantic_runs.get(run_id, {
        'pct': 0, 'stage': '', 'done': True, 'error': 'not found',
        'csv_path': '', 'out_dir': '', 'output': '',
    })


def get_info(path: str) -> dict:
    """Lee el sidecar {stem}.semantic.json de una reunión (o {} si aún no se ha generado).
    Añade flags *_exists para que la pestaña sepa si los ficheros siguen presentes."""
    try:
        md_path = Path(path)
        info_file = md_path.with_name(f"{md_path.stem}.semantic.json")
        if not info_file.exists():
            return {}
        info = json.loads(info_file.read_text(encoding='utf-8'))
        for key in ('csv_local', 'record_local', 'csv_source'):
            p = info.get(key)
            info[f'{key}_exists'] = bool(p and Path(p).exists())
        info['inputs_dir_exists'] = bool(info.get('inputs_dir') and Path(info['inputs_dir']).exists())
        return info
    except Exception as e:
        log.error(f"semantic_layer.get_info: {e}")
        return {}


def reveal_file(file_path: str) -> bool:
    """Abre el Explorador de Windows con el fichero SELECCIONADO (o abre la carpeta si es un dir)."""
    try:
        p = Path(file_path)
        if not p.exists():
            return False
        if p.is_dir():
            subprocess.Popen(['explorer', str(p)])
        else:
            subprocess.Popen(['explorer', '/select,', str(p)])
        return True
    except Exception as e:
        log.error(f"semantic_layer.reveal_file: {e}")
        return False


def open_file(file_path: str) -> bool:
    """Abre un fichero con su aplicación por defecto (p.ej. el CSV en Excel)."""
    try:
        p = Path(file_path)
        if not p.exists():
            return False
        os.startfile(str(p))  # noqa: S606 (Windows)
        return True
    except Exception as e:
        log.error(f"semantic_layer.open_file: {e}")
        return False


def pipeline_jobs() -> list:
    """Jobs en curso para el panel 'En curso' (uno por run no terminado)."""
    jobs = []
    for state in _semantic_runs.values():
        if state.get('done'):
            continue
        jobs.append({
            'stage':    'processing',
            'kind':     'semantic',
            'label':    'Semantic Layer',
            'subtitle': state.get('title', ''),
            'pct':      state.get('pct', 0),
        })
    return jobs
