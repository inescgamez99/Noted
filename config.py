import json as _json
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_DIR = Path(__file__).parent

# settings.json overrides take priority over .env
_settings: dict = {}
_settings_path = PROJECT_DIR / 'settings.json'
if _settings_path.exists():
    try:
        _settings = _json.loads(_settings_path.read_text(encoding='utf-8'))
    except Exception:
        pass

BASE_OUTPUT_DIR = Path(_settings.get('output_dir') or os.getenv('OUTPUT_DIR', str(PROJECT_DIR)))
RECORDINGS_DIR = BASE_OUTPUT_DIR / 'recordings'
MINUTES_DIR    = BASE_OUTPUT_DIR / 'minutes'
INBOX_DIR      = BASE_OUTPUT_DIR / 'inbox'

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
OPENAI_API_KEY    = os.getenv('OPENAI_API_KEY', '')

SAMPLE_RATE = 16000
CHANNELS    = 1

WHISPER_MODEL    = _settings.get('whisper_model') or os.getenv('WHISPER_MODEL', 'medium')
WHISPER_LANGUAGE = os.getenv('WHISPER_LANGUAGE', '') or None

ME_NAME = _settings.get('user_name', '').strip()

CLAUDE_MODEL      = _settings.get('claude_model') or os.getenv('CLAUDE_MODEL', 'claude-sonnet-4-6')
CLAUDE_MAX_TOKENS = 8192

def _find_claude_bin() -> str | None:
    import re as _re
    # Preferir siempre el exe directo (no el wrapper CMD que se cuelga sin consola)
    # Buscar via which — si es .cmd/.bat, leer el wrapper para extraer el exe real
    cmd_path = shutil.which('claude')
    if cmd_path:
        p = Path(cmd_path)
        if p.suffix.lower() in ('.cmd', '.bat'):
            try:
                content = p.read_text(encoding='utf-8', errors='ignore')
                # Resolver %dp0% (directorio del CMD) y buscar rutas a .exe
                cmd_dir = str(p.parent)
                content_resolved = _re.sub(r'%dp0%', cmd_dir.replace('\\', '\\\\'), content, flags=_re.IGNORECASE)
                m = _re.search(r'"([^"]+\.exe)"', content_resolved, _re.IGNORECASE)
                if m:
                    exe = Path(m.group(1))
                    if exe.exists():
                        return str(exe)
            except Exception:
                pass
        return cmd_path
    return None

CLAUDE_BIN: str | None = _find_claude_bin()

TEAMS_CHAT_MESSAGE = _settings.get('teams_chat_message', '')

MINUTES_RETENTION_DAYS = int(_settings.get('minutes_retention_days') or os.getenv('MINUTES_RETENTION_DAYS', 90))

TEAMS_POLL_INTERVAL          = 3.0
TEAMS_REQUIRED_CONFIRMATIONS = 2

POPUP_TIMEOUT = 30

CLI_CONTROL_FILE = PROJECT_DIR / '.cli_command'
LOG_FILE         = PROJECT_DIR / 'noted.log'


def get_ui_language() -> str:
    """Devuelve el idioma de UI desde la config cargada en memoria."""
    return _settings.get('language', 'es')


def clean_env() -> dict:
    """Entorno limpio para subprocesos: elimina tokens de sesión de Anthropic/MCP
    pero preserva variables de configuración de Claude Code (ej. CLAUDE_CODE_GIT_BASH_PATH)
    que el subproceso 'claude -p' necesita para funcionar en Windows."""
    env = os.environ.copy()
    for key in list(env.keys()):
        if key.startswith(('ANTHROPIC_', 'MCP_')):
            del env[key]
    return env
