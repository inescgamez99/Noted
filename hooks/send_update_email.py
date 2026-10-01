"""
Genera un resumen ejecutivo de los cambios de git y lo envia por email al equipo
via Outlook. Llamado desde el daemon de Noted (tray_app.py) para que
claude corra en el contexto correcto con autenticacion.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from config import get_claude_bin as _get_claude_bin, clean_env

# El email va a equipos que han clonado el repo en rutas distintas, asi que no
# puede llevar un comando con la ruta fija: a quien no clono en Documents le
# fallaba el pull. La skill /noted localiza el clon y actualiza.
_UPDATE_CMD = '/update-noted'
_UPDATE_HTML = f"""<p>Para actualizar, abre Claude Code y ejecuta:</p>
<pre style="background:#f4f4f4;padding:8px;border-radius:4px;font-family:monospace">{_UPDATE_CMD}</pre>
<p>La app se reiniciará automáticamente.</p>"""

_PROMPT_TEMPLATE = """Eres el asistente de comunicación de Noted, una app de Windows que graba reuniones de Teams y genera minutas automáticas con IA.

El equipo ha publicado una nueva versión. Estos son los cambios (mensajes de commit de git):
{commits}

Escribe el cuerpo de un email de notificación en HTML con estas reglas:
- Tono: profesional y cercano, como las notas de actualización de iOS/Apple
- Máximo 150 palabras de contenido
- Sin jerga técnica: nada de nombres de ficheros, funciones, variables ni errores de código
- Agrupa los cambios en categorías si tiene sentido. Las categorías disponibles son: Nuevas funciones, Mejoras, Correcciones
- Cada categoría debe tener su nombre en <strong><u>Nombre categoría</u></strong>
- Los elementos de cada categoría van en una lista <ul> con <li> por item
- Termina siempre con este bloque HTML exacto, sin modificarlo:

{update_html}

- Responde SOLO con el HTML del cuerpo del email (sin <html>, sin <head>, sin <body>), en español, sin asunto ni firma
"""

_EMAIL_WRAPPER = """\
<div style="font-family:Calibri,Arial,sans-serif;font-size:14px;color:#1a1a1a;max-width:600px;line-height:1.6">
{body}
</div>"""


def send_update_email(commits_text: str) -> bool:
    """Genera el resumen con Claude y manda el email al equipo. Devuelve True si éxito."""
    recipients_file = PROJECT_DIR / 'team' / 'recipients.txt'
    if not recipients_file.exists():
        return False

    recipients = [
        r.strip() for r in recipients_file.read_text(encoding='utf-8').splitlines()
        if r.strip() and not r.startswith('#') and '@' in r
    ]
    if not recipients:
        return False

    html_body = _generate_html(commits_text)
    subject = f"Noted — Novedades {datetime.now().strftime('%d/%m/%Y')}"

    try:
        import win32com.client
        outlook = win32com.client.Dispatch('Outlook.Application')
        mail = outlook.CreateItem(0)
        mail.Subject = subject
        mail.HTMLBody = _EMAIL_WRAPPER.format(body=html_body)
        for email in recipients:
            mail.Recipients.Add(email)
        mail.Recipients.ResolveAll()
        mail.Send()
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"send_update_email Outlook error: {e}")
        return False


def _generate_html(commits_text: str) -> str:
    """Intenta resumen IA en HTML; si falla, devuelve HTML manual."""
    _claude_bin = _get_claude_bin()
    if _claude_bin:
        prompt = _PROMPT_TEMPLATE.format(commits=commits_text, update_html=_UPDATE_HTML)
        try:
            result = subprocess.run(
                [_claude_bin, '-p'],
                input=prompt,
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=90,
                env=clean_env(),
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or f'claude exited {result.returncode}')
            body = result.stdout.strip()
            # Claude a veces envuelve el HTML en ```html ... ``` — quitarlo
            if body.startswith('```'):
                body = body.split('\n', 1)[-1]
            if body.endswith('```'):
                body = body.rsplit('```', 1)[0]
            body = body.strip()
            if body:
                return body
        except Exception:
            pass

    # Fallback: HTML manual con bullet points
    lines = [l.strip().lstrip('- ') for l in commits_text.splitlines() if l.strip()]
    items = '\n'.join(f'<li>{l}</li>' for l in lines)
    return (
        f"<p>Hola,</p>"
        f"<p>Noted ha recibido una nueva actualización:</p>"
        f"<p><strong><u>Cambios</u></strong></p>"
        f"<ul>{items}</ul>"
        f"{_UPDATE_HTML}"
    )
