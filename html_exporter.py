import html as _html
import os
import re
from datetime import datetime
from pathlib import Path

try:
    import markdown
    _HAS_MARKDOWN = True
except ImportError:
    _HAS_MARKDOWN = False


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    font-size: 15px;
    line-height: 1.7;
    color: #1a1a2e;
    background: #f8f9fc;
    padding: 0;
  }}
  .page {{
    max-width: 860px;
    margin: 0 auto;
    background: #ffffff;
    min-height: 100vh;
    box-shadow: 0 0 40px rgba(0,0,0,0.08);
  }}
  .header {{
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 40px 48px 32px;
  }}
  .header .label {{
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    opacity: 0.8;
    margin-bottom: 10px;
  }}
  .header h1 {{
    font-size: 28px;
    font-weight: 700;
    margin-bottom: 12px;
    line-height: 1.3;
  }}
  .header .meta {{
    font-size: 13px;
    opacity: 0.85;
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
  }}
  .header .meta span {{ display: flex; align-items: center; gap: 5px; }}
  .content {{ padding: 40px 48px 60px; }}
  h2 {{
    font-size: 17px;
    font-weight: 700;
    color: #1a1a2e;
    margin: 36px 0 12px;
    padding-bottom: 6px;
    border-bottom: 2px solid #f0f0f5;
  }}
  h3 {{ font-size: 15px; font-weight: 600; margin: 20px 0 8px; color: #333; }}
  p {{ margin-bottom: 12px; color: #374151; }}
  ul, ol {{ padding-left: 22px; margin-bottom: 12px; }}
  li {{ margin-bottom: 5px; color: #374151; }}
  a {{ color: #667eea; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 16px 0;
    font-size: 14px;
  }}
  th {{
    background: #f4f5f9;
    font-weight: 600;
    text-align: left;
    padding: 10px 14px;
    border: 1px solid #e5e7eb;
    color: #374151;
  }}
  td {{
    padding: 9px 14px;
    border: 1px solid #e5e7eb;
    color: #374151;
  }}
  tr:nth-child(even) td {{ background: #fafafa; }}
  pre {{
    background: #1e1e2e;
    color: #cdd6f4;
    padding: 18px 20px;
    border-radius: 8px;
    overflow-x: auto;
    margin: 16px 0;
    font-size: 13px;
    line-height: 1.6;
    font-family: 'Cascadia Code', 'Consolas', monospace;
  }}
  code {{
    background: #f0f0f8;
    color: #7c3aed;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 13px;
    font-family: 'Cascadia Code', 'Consolas', monospace;
  }}
  pre code {{ background: none; color: inherit; padding: 0; }}
  blockquote {{
    border-left: 3px solid #667eea;
    padding: 8px 16px;
    margin: 16px 0;
    background: #f5f5ff;
    border-radius: 0 6px 6px 0;
    color: #555;
  }}
  .action-block {{
    background: #fff8e6;
    border: 1px solid #fbbf24;
    border-left: 4px solid #f59e0b;
    border-radius: 6px;
    padding: 14px 18px;
    margin: 12px 0;
    font-size: 13.5px;
  }}
  .action-block .action-label {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: #92400e;
    margin-bottom: 6px;
  }}
  .instruction-block {{
    background: #f0fdf4;
    border: 1px solid #86efac;
    border-left: 4px solid #22c55e;
    border-radius: 6px;
    padding: 14px 18px;
    margin: 12px 0;
    font-size: 13.5px;
  }}
  .instruction-block .action-label {{ color: #14532d; }}
  .footer {{
    background: #f8f9fc;
    border-top: 1px solid #e5e7eb;
    padding: 20px 48px;
    font-size: 12px;
    color: #9ca3af;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .badge {{
    display: inline-block;
    background: #ede9fe;
    color: #7c3aed;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 10px;
    border-radius: 20px;
    margin-right: 6px;
  }}
  @media print {{
    body {{ background: white; }}
    .page {{ box-shadow: none; }}
    .header {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  }}
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div class="label">Minutas de Reunión</div>
    <h1>{title}</h1>
    <div class="meta">
      <span>📅 {date}</span>
      <span>⏱ {duration}</span>
      {participants_html}
    </div>
  </div>
  <div class="content">
    {body}
  </div>
  <div class="footer">
    <span>Generado automáticamente con Noted + Claude</span>
    <span>{generated_at}</span>
  </div>
</div>
</body>
</html>"""


def _md_to_html_body(md_text: str) -> str:
    if _HAS_MARKDOWN:
        return markdown.markdown(
            md_text,
            extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists'],
        )
    # Fallback simple
    lines = []
    for line in md_text.splitlines():
        if line.startswith('### '):
            lines.append(f'<h3>{line[4:]}</h3>')
        elif line.startswith('## '):
            lines.append(f'<h2>{line[3:]}</h2>')
        elif line.startswith('# '):
            lines.append(f'<h2>{line[2:]}</h2>')
        elif line.startswith('- ') or line.startswith('* '):
            if not lines or not lines[-1].startswith('<li>'):
                lines.append('<ul>')
            lines.append(f'<li>{line[2:]}</li>')
        elif line.strip() == '':
            if lines and lines[-1].startswith('<li>'):
                lines.append('</ul>')
            lines.append('<br>')
        else:
            if lines and lines[-1].startswith('<li>'):
                lines.append('</ul>')
            lines.append(f'<p>{line}</p>')
    if lines and lines[-1].startswith('<li>'):
        lines.append('</ul>')
    return '\n'.join(lines)


def _extract_meta(minutes_path: Path) -> dict:
    stem = minutes_path.stem
    date_str = 'Fecha desconocida'
    time_str = ''

    m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', stem)
    if m:
        date_str = f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
        time_str = f"{m.group(4)}:{m.group(5)}"

    return {'date': f"{date_str} {time_str}".strip()}


def export_to_html(
    minutes_path: Path,
    title: str,
    participants: list[dict] | None = None,
    duration: str = '',
    open_browser: bool = True,
) -> Path:
    md_text = minutes_path.read_text(encoding='utf-8')
    meta = _extract_meta(minutes_path)

    # Participantes HTML
    parts_html = ''
    if participants:
        names = ', '.join(p.get('name', p.get('email', '')) for p in participants[:5])
        if len(participants) > 5:
            names += f' +{len(participants)-5} más'
        parts_html = f'<span>👥 {names}</span>'

    body_html = _md_to_html_body(md_text)
    generated_at = datetime.now().strftime('%d/%m/%Y %H:%M')

    html = _HTML_TEMPLATE.format(
        title=_html.escape(title),
        date=meta['date'],
        duration=duration or 'N/A',
        participants_html=parts_html,
        body=body_html,
        generated_at=f"Generado el {generated_at}",
    )

    html_path = minutes_path.with_suffix('.html')
    html_path.write_text(html, encoding='utf-8')

    if open_browser:
        os.startfile(str(html_path))

    return html_path
