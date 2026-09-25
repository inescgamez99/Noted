"""
Exporta los artefactos de una reunión a la carpeta del proyecto configurada.
Estructura creada en la carpeta del proyecto:
  Transcripts/  → {slug}_transcript.txt
  Notas/        → {slug}.html  (copia del HTML completo)
  Email/        → {slug}_email.html  (versión limpia apta para email)
"""

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

try:
    import markdown as _markdown
    _HAS_MD = True
except ImportError:
    _HAS_MD = False


# ── Resolución del proyecto ────────────────────────────────────────────────────

def get_meeting_project(minutes_path: Path) -> dict | None:
    """Devuelve el dict del proyecto asignado a esta reunión, o None."""
    from config import PROJECT_DIR
    actions_path = minutes_path.parent / f"{minutes_path.stem}_actions.json"
    if not actions_path.exists():
        return None
    try:
        adata = json.loads(actions_path.read_text(encoding='utf-8'))
        project_id = adata.get('project_id', 'none')
    except Exception:
        return None
    if not project_id or project_id == 'none':
        return None
    projects_file = PROJECT_DIR / 'projects.json'
    if not projects_file.exists():
        return None
    try:
        for p in json.loads(projects_file.read_text(encoding='utf-8')).get('projects', []):
            if p.get('id') == project_id:
                return p
    except Exception:
        return None
    return None


# ── Exportación principal ──────────────────────────────────────────────────────

def export_to_project_folder(minutes_path: Path, transcript_txt: str | None = None) -> bool:
    """
    Exporta transcript, HTML de notas y HTML de email a la carpeta del proyecto.
    Respeta las preferencias export_save_* definidas POR PROYECTO en projects.json.
    Devuelve True si se exportó algo, False si el proyecto no tiene carpeta configurada.
    """
    project = get_meeting_project(minutes_path)
    if not project:
        return False

    proj_dir_str = (project.get('directory') or '').strip()
    if not proj_dir_str:
        return False

    proj_dir = Path(proj_dir_str)
    if not proj_dir.exists():
        log.warning(f"project_exporter: carpeta no existe: {proj_dir}")
        return False

    # Preferencias de exportación POR PROYECTO (guardadas en projects.json).
    # Por defecto todo activado si el proyecto no las tiene definidas.
    save_transcript = project.get('export_save_transcript', True)
    save_html       = project.get('export_save_html', True)
    save_email      = project.get('export_save_email', True)
    save_pdf        = project.get('export_save_pdf', False)

    from config import ME_NAME
    _prefix = re.sub(r'[^\w]', '', ME_NAME) + '_' if ME_NAME else ''
    slug = _prefix + minutes_path.stem

    # 1 – Transcript
    if save_transcript:
        transcripts_dir = proj_dir / 'Transcripts'
        transcripts_dir.mkdir(parents=True, exist_ok=True)

        if transcript_txt is None:
            tc = minutes_path.with_name(slug + '_transcript.txt')
            if tc.exists():
                try:
                    transcript_txt = tc.read_text(encoding='utf-8')
                except Exception:
                    pass

        if transcript_txt:
            try:
                (transcripts_dir / f"{slug}_transcript.txt").write_text(transcript_txt, encoding='utf-8')
            except Exception as e:
                log.warning(f"project_exporter: transcript: {e}")

    # 2 – Notas HTML (copia del .html generado por html_exporter)
    if save_html:
        notes_dir = proj_dir / 'Notas'
        notes_dir.mkdir(parents=True, exist_ok=True)
        html_src = minutes_path.with_suffix('.html')
        if html_src.exists():
            try:
                shutil.copy2(str(html_src), str(notes_dir / f"{slug}.html"))
            except Exception as e:
                log.warning(f"project_exporter: HTML notas: {e}")
        else:
            log.warning(f"project_exporter: {html_src.name} no encontrado")

    # 3 – Email HTML (versión limpia, inline-styles, apta para clientes de email)
    if save_email:
        email_dir = proj_dir / 'Email'
        email_dir.mkdir(parents=True, exist_ok=True)
        try:
            email_html = _build_project_email_html(minutes_path)
            if email_html:
                (email_dir / f"{slug}_email.html").write_text(email_html, encoding='utf-8')
        except Exception as e:
            log.warning(f"project_exporter: email HTML: {e}")

    # 4 – PDF (generado desde el HTML via Edge headless)
    if save_pdf:
        pdf_dir = proj_dir / 'PDF'
        pdf_dir.mkdir(parents=True, exist_ok=True)
        html_src = minutes_path.with_suffix('.html')
        pdf_dst = pdf_dir / f"{slug}.pdf"
        edge = next((p for p in [
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        ] if Path(p).exists()), None)
        if edge and html_src.exists():
            try:
                import subprocess as _sp
                _sp.run([edge, '--headless', '--disable-gpu', '--no-pdf-header-footer',
                         f'--print-to-pdf={pdf_dst}', f'file:///{html_src}'],
                        capture_output=True, timeout=60)
                if not pdf_dst.exists():
                    log.warning(f"project_exporter: PDF no generado para {slug}")
            except Exception as e:
                log.warning(f"project_exporter: PDF: {e}")
        else:
            log.warning(f"project_exporter: Edge no encontrado o HTML falta, PDF omitido")

    log.info(f"project_exporter: {slug} exportado a {proj_dir}")
    return True


# ── HTML para email ────────────────────────────────────────────────────────────

_EMAIL_TEMPLATE = """\
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f7;font-family:'Segoe UI',Arial,sans-serif;font-size:15px;color:#222;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f7;padding:32px 0;">
  <tr><td align="center">
  <table width="640" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">

    <!-- Header -->
    <tr><td style="background:linear-gradient(135deg,#667eea,#764ba2);padding:36px 40px 28px;">
      <p style="margin:0 0 8px;font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:rgba(255,255,255,.75);">Minutas de Reunión</p>
      <h1 style="margin:0 0 14px;font-size:24px;font-weight:700;color:#ffffff;line-height:1.3;">{title}</h1>
      <p style="margin:0;font-size:13px;color:rgba(255,255,255,.85);">📅 {date}</p>
    </td></tr>

    <!-- Content -->
    <tr><td style="padding:32px 40px 24px;">
      {body}
    </td></tr>

    <!-- Footer -->
    <tr><td style="background:#f8f9fc;border-top:1px solid #e5e7eb;padding:16px 40px;font-size:12px;color:#9ca3af;">
      Generado automáticamente con Noted + Claude · {generated_at}
    </td></tr>

  </table>
  </td></tr>
</table>
</body>
</html>"""


def _build_project_email_html(minutes_path: Path) -> str:
    try:
        md_text = minutes_path.read_text(encoding='utf-8')
    except Exception:
        return ''

    # Extraer título
    title = minutes_path.stem
    if md_text.startswith('TITULO:'):
        first_line = md_text.splitlines()[0]
        title = first_line.removeprefix('TITULO:').strip()
        md_text = '\n'.join(md_text.splitlines()[1:]).lstrip()

    # Quitar bloques de acciones Claude (~~~...~~~)
    md_text = re.sub(r'~~~[\w-]+\n.*?~~~', '', md_text, flags=re.DOTALL)

    # Extraer fecha del stem (YYYYMMDD_HHMM)
    date_str = ''
    m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', minutes_path.stem)
    if m:
        date_str = f"{m.group(3)}/{m.group(2)}/{m.group(1)} {m.group(4)}:{m.group(5)}"

    body = _md_to_email_html(md_text)

    return _EMAIL_TEMPLATE.format(
        title=title,
        date=date_str,
        body=body,
        generated_at=datetime.now().strftime('%d/%m/%Y %H:%M'),
    )


_H2 = 'style="margin:28px 0 10px;font-size:17px;font-weight:700;color:#1a1a2e;border-bottom:2px solid #f0f0f5;padding-bottom:6px;"'
_H3 = 'style="margin:18px 0 6px;font-size:15px;font-weight:600;color:#333;"'
_P  = 'style="margin:0 0 10px;color:#374151;line-height:1.7;"'
_LI = 'style="margin-bottom:5px;color:#374151;"'
_TH = 'style="background:#f4f5f9;font-weight:600;text-align:left;padding:9px 13px;border:1px solid #e5e7eb;color:#374151;"'
_TD = 'style="padding:9px 13px;border:1px solid #e5e7eb;color:#374151;"'


def _md_to_email_html(md_text: str) -> str:
    """Convierte markdown a HTML con inline styles para email."""
    if _HAS_MD:
        raw = _markdown.markdown(
            md_text,
            extensions=['tables', 'fenced_code', 'nl2br', 'sane_lists'],
        )
        # Inyectar inline styles en los tags más comunes
        raw = re.sub(r'<h2>', f'<h2 {_H2}>', raw)
        raw = re.sub(r'<h3>', f'<h3 {_H3}>', raw)
        raw = re.sub(r'<p>', f'<p {_P}>', raw)
        raw = re.sub(r'<li>', f'<li {_LI}>', raw)
        raw = re.sub(r'<th>', f'<th {_TH}>', raw)
        raw = re.sub(r'<td>', f'<td {_TD}>', raw)
        raw = re.sub(r'<table>', '<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:14px 0;">', raw)
        return raw

    # Fallback sin librería markdown
    lines, out, in_list = md_text.splitlines(), [], False
    for line in lines:
        if line.startswith('## '):
            if in_list: out.append('</ul>'); in_list = False
            out.append(f'<h2 {_H2}>{line[3:]}</h2>')
        elif line.startswith('### '):
            if in_list: out.append('</ul>'); in_list = False
            out.append(f'<h3 {_H3}>{line[4:]}</h3>')
        elif line.startswith(('- ', '* ')):
            if not in_list: out.append('<ul style="padding-left:20px;margin:0 0 10px;">'); in_list = True
            out.append(f'<li {_LI}>{line[2:]}</li>')
        elif line.strip() == '':
            if in_list: out.append('</ul>'); in_list = False
        else:
            if in_list: out.append('</ul>'); in_list = False
            out.append(f'<p {_P}>{line}</p>')
    if in_list:
        out.append('</ul>')
    return '\n'.join(out)
