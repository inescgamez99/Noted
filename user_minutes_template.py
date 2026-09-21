"""
Personalizacion opcional de la estructura de minutas por usuario.

- El archivo `user_minutes.json` (gitignored) contiene la plantilla del usuario.
- Cuando `enabled: true`, minutes_generator inyecta esta plantilla en el prompt,
  respetando siempre los elementos bloqueados (TITULO, cabecera de metadatos,
  seccion de Acciones Pendientes y los bloques ~~~code/instruction/document-change).
- La plantilla se puede generar a partir de un prompt en lenguaje natural via
  `claude -p`, y luego editar libremente antes de guardar.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path

from config import PROJECT_DIR, CLAUDE_BIN as _CLAUDE_BIN, clean_env as _clean_env

log = logging.getLogger(__name__)

USER_TEMPLATE_FILE = PROJECT_DIR / 'user_minutes.json'


DEFAULT_CONFIG: dict = {
    'enabled': False,
    'source_prompt': '',
    'template': {
        # Idioma de referencia con el que se genero la plantilla; solo informativo.
        'language': 'auto',
        # Lista ordenada de secciones. Cada una: {"name": str, "description": str}.
        # NOTA: la seccion de Acciones Pendientes se anade siempre al final por el generador,
        # aunque el usuario no la incluya, para no romper el pipeline de acciones.
        'sections': [],
        # Reglas globales extra (tono, terminologia, que enfatizar/evitar, etc.).
        'extra_rules': '',
    },
}


def load_config() -> dict:
    """Devuelve la config del usuario, o DEFAULT_CONFIG si no existe / esta corrupta."""
    try:
        if USER_TEMPLATE_FILE.exists():
            data = json.loads(USER_TEMPLATE_FILE.read_text(encoding='utf-8'))
            # Rellena claves faltantes con defaults sin sobreescribir las presentes.
            return _merge_defaults(data)
    except Exception as e:
        log.warning(f"user_minutes.json invalido, usando defaults: {e}")
    return json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy


def save_config(config: dict) -> bool:
    try:
        merged = _merge_defaults(config)
        USER_TEMPLATE_FILE.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False),
            encoding='utf-8',
        )
        return True
    except Exception as e:
        log.error(f"save_config user_minutes: {e}")
        return False


def _merge_defaults(data: dict) -> dict:
    out = json.loads(json.dumps(DEFAULT_CONFIG))
    if not isinstance(data, dict):
        return out
    out['enabled'] = bool(data.get('enabled', False))
    out['source_prompt'] = str(data.get('source_prompt', '') or '')
    tpl_in = data.get('template') or {}
    if isinstance(tpl_in, dict):
        out['template']['language'] = str(tpl_in.get('language', 'auto') or 'auto')
        secs = tpl_in.get('sections')
        if isinstance(secs, list):
            clean = []
            for s in secs:
                if not isinstance(s, dict):
                    continue
                name = str(s.get('name', '') or '').strip()
                if not name:
                    continue
                desc = str(s.get('description', '') or '').strip()
                clean.append({'name': name, 'description': desc})
            out['template']['sections'] = clean
        out['template']['extra_rules'] = str(tpl_in.get('extra_rules', '') or '')
    return out


# ── Meta-prompt: pedir a Claude que produzca una plantilla estructurada ──────

_META_SYSTEM = """Eres un asistente que ayuda a un usuario a definir la ESTRUCTURA que quiere
para sus propias minutas de reuniones. El usuario te describe en lenguaje natural
que tipo de minutas quiere, y tu devuelves un JSON con la lista de secciones y
reglas globales adicionales.

Requisitos ABSOLUTOS:
- Responde EXCLUSIVAMENTE con JSON valido. Nada mas: sin texto previo, sin ```json, sin comentarios.
- El JSON debe tener exactamente esta forma:
  {
    "sections": [
      {"name": "Nombre de la seccion", "description": "Que debe contener y como redactarla; instrucciones especificas para el modelo que generara las minutas."},
      ...
    ],
    "extra_rules": "Reglas globales opcionales sobre tono, terminologia, longitud, cosas a evitar, formato, etc. Puede estar vacio."
  }
- NO incluyas una seccion para 'Acciones Pendientes' / 'Pending Actions' / 'Accions Pendents': el sistema la anade siempre al final automaticamente.
- NO incluyas ninguna seccion de 'Titulo': el titulo se maneja aparte.
- Escribe los nombres de seccion y las descripciones en el mismo idioma que use el usuario en su peticion.
- Las descripciones deben ser instrucciones precisas y accionables para el generador de minutas: que incluir, en que formato (tabla, bullets, parrafo), a que prestar atencion, que ignorar.
- Recomendable: entre 3 y 7 secciones (aparte de la de acciones que se anade sola).
"""


_META_USER_TEMPLATE = """El usuario ha descrito asi como quiere sus minutas:

{user_prompt}

Devuelve unicamente el JSON con las secciones y reglas globales, siguiendo el esquema indicado."""


def generate_template_from_prompt(user_prompt: str) -> dict | None:
    """Llama a claude -p para producir una plantilla estructurada.

    Devuelve un dict {sections: [...], extra_rules: str} o None si falla.
    """
    if not _CLAUDE_BIN:
        log.error("claude CLI no encontrado en PATH")
        return None
    if not user_prompt or not user_prompt.strip():
        return None

    full_prompt = _META_SYSTEM + '\n\n' + _META_USER_TEMPLATE.format(user_prompt=user_prompt.strip())

    try:
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            CREATE_NO_WINDOW = 0x08000000
        else:
            si = None
            CREATE_NO_WINDOW = 0

        proc = subprocess.Popen(
            [_CLAUDE_BIN, '-p'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            env=_clean_env(),
            startupinfo=si if os.name == 'nt' else None,
            creationflags=CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        try:
            stdout, stderr = proc.communicate(input=full_prompt, timeout=180)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            log.error("generate_template_from_prompt: claude -p timeout")
            return None
        if proc.returncode != 0:
            log.error(f"generate_template_from_prompt rc={proc.returncode}: {stderr[:400]}")
            return None
    except Exception as e:
        log.error(f"generate_template_from_prompt exception: {e}")
        return None

    return _parse_template_json(stdout)


def _parse_template_json(raw: str) -> dict | None:
    """Extrae JSON del stdout de Claude, tolerando envoltorio en ```."""
    if not raw:
        return None
    text = raw.strip()
    # Quita fences de markdown si se colaron.
    fence = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Encuentra la primera { hasta la ultima } por si hay texto residual.
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        log.warning("Respuesta de plantilla sin JSON reconocible")
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception as e:
        log.warning(f"JSON de plantilla invalido: {e}")
        return None

    sections = data.get('sections')
    if not isinstance(sections, list):
        return None
    clean_secs = []
    for s in sections:
        if not isinstance(s, dict):
            continue
        name = str(s.get('name', '') or '').strip()
        if not name:
            continue
        desc = str(s.get('description', '') or '').strip()
        clean_secs.append({'name': name, 'description': desc})
    if not clean_secs:
        return None

    return {
        'sections': clean_secs,
        'extra_rules': str(data.get('extra_rules', '') or '').strip(),
    }


# ── Utilidades usadas por minutes_generator ──────────────────────────────────

def get_active_template() -> dict | None:
    """Devuelve la plantilla si esta activa y tiene contenido; si no, None."""
    cfg = load_config()
    if not cfg.get('enabled'):
        return None
    tpl = cfg.get('template') or {}
    secs = tpl.get('sections') or []
    if not secs:
        return None
    return tpl


def actions_section_label(language: str) -> str:
    """Nombre localizado de la seccion de acciones que se anadira siempre."""
    return {
        'en': "Pending Actions",
        'es': "Acciones Pendientes",
        'ca': "Accions Pendents",
    }.get(language, "Pending Actions")


def actions_section_description(language: str) -> str:
    return {
        'en': (
            "First: a table with ALL actions — columns: Action | Owner | Deadline. "
            "Mark Claude-executable actions with '(Claude)' next to the owner. "
            "Then: one technical block per Claude-executable action, using the ~~~ formats "
            "defined above (code / instruction-for-claude / document-change)."
        ),
        'es': (
            "Primero: tabla con TODAS las acciones — columnas: Accion | Responsable | Fecha limite. "
            "Marca las ejecutables por Claude con '(Claude)' junto al responsable. "
            "Despues: un bloque tecnico por cada accion Claude, en los formatos ~~~ definidos "
            "arriba (code / instruction-for-claude / document-change)."
        ),
        'ca': (
            "Primer: taula amb TOTES les accions — columnes: Accio | Responsable | Data limit. "
            "Marca les executables per Claude amb '(Claude)' al costat del responsable. "
            "Despres: un bloc tecnic per cada accio Claude, en els formats ~~~ definits "
            "a dalt (code / instruction-for-claude / document-change)."
        ),
    }.get(language, "")
