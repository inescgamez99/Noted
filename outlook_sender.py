"""
Detecta participantes del calendario de Outlook y envía minutas por email.
"""
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

def _get_outlook():
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
    import win32com.client
    return win32com.client.Dispatch('Outlook.Application')


def _norm_subject(s: str) -> str:
    s = (s or '').lower()
    s = re.sub(r'[_\-|]+', ' ', s)
    s = re.sub(r'[^\w\s]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _name_score(meeting_name: str, subject: str) -> float:
    import difflib
    a, b = _norm_subject(meeting_name), _norm_subject(subject)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    wa = {w for w in a.split() if len(w) > 3}
    wb = {w for w in b.split() if len(w) > 3}
    overlap = len(wa & wb) / max(1, len(wa)) if wa else 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return max(overlap, ratio)


def _naive(dt):
    """pywin32 devuelve las fechas de Outlook CON tzinfo (+00:00), aunque la
    hora es la local de la pared. Restarlas de una fecha sin zona lanza
    TypeError: durante semanas ese error se tragaba en un `except: continue` y
    find_meeting_participants devolvía siempre [] sin dejar rastro."""
    return dt.replace(tzinfo=None) if getattr(dt, 'tzinfo', None) else dt


# Si no hay nombre que case, solo se acepta por hora una cita que empiece (o
# esté en curso) a esta distancia: antes se cogía la "más cercana" aunque
# fuese de dos horas después, y se le atribuían invitados de otra reunión.
_MAX_TIME_FALLBACK = timedelta(minutes=20)

_ROLES = {1: 'required', 2: 'optional', 3: 'resource'}
_RESPONSES = {1: 'organizer', 2: 'tentative', 3: 'accepted', 4: 'declined', 5: 'no response'}


def _clean_name(name: str) -> str:
    """'Apellido, Nombre' (formato de la GAL de Exchange) -> 'Nombre Apellido'."""
    name = (name or '').strip()
    m = re.match(r'^([^,@]+),\s*([^,@]+)$', name)
    return f"{m.group(2).strip()} {m.group(1).strip()}" if m else name


def _recipient_info(rec, organizer: str = '') -> dict | None:
    name = rec.Name
    info = {'name': _clean_name(name), 'email': rec.Address}
    # Rol y respuesta son un extra: un destinatario sin ellos (o un COM que los
    # rechaza) sigue siendo un participante válido.
    try:
        info['role'] = _ROLES.get(int(rec.Type), 'required')
    except Exception:
        pass
    try:
        info['response'] = _RESPONSES.get(int(rec.MeetingResponseStatus), 'no response')
    except Exception:
        pass
    if organizer and _norm_subject(name) == _norm_subject(organizer):
        info['role'] = 'organizer'
    return info


def find_meeting_participants(recording_time: datetime, meeting_name: str = None,
                             window_minutes: int = 15) -> list[dict]:
    """
    Busca en el calendario de Outlook la reunión que corresponde a la grabación y
    devuelve la lista de invitados (nombre, email, rol y respuesta). Las
    invitaciones de Teams son citas del calendario, así que esto las cubre.

    Empareja por NOMBRE (asunto del evento vs. nombre detectado de la reunión) y,
    como respaldo, por proximidad de HORA. Cada participante lleva además
    'meeting_subject' con el asunto de la cita elegida.
    """
    try:
        best_item = _find_meeting_item(recording_time, meeting_name)
        if best_item is None:
            return []

        try:
            subject = best_item.Subject or ''
        except Exception:
            subject = ''
        try:
            organizer = best_item.Organizer or ''
        except Exception:
            organizer = ''

        participants = []
        for rec in best_item.Recipients:
            try:
                info = _recipient_info(rec, organizer)
            except Exception:
                continue
            if info.get('role') == 'resource':      # salas y equipos, no personas
                continue
            info['meeting_subject'] = subject
            participants.append(info)
        return participants
    except Exception as e:
        log.warning(f"No se pudieron obtener participantes de Outlook: {e}")
        return []


def find_meeting_subject(recording_time: datetime) -> str | None:
    """El asunto de la cita que EMPIEZA junto a la grabacion, o None. Es el
    titulo de la reunion cuando no hay otro (una grabacion manual fuera de
    Teams y sin Claude). Mas estricto que find_meeting_participants: una cita
    cancelada o rechazada, o la anterior que sigue en curso, darian a la
    reunion el nombre de otra."""
    try:
        item = _find_meeting_item(recording_time, None, for_title=True)
        return ((item.Subject or '').strip() or None) if item is not None else None
    except Exception as e:
        log.warning(f"No se pudo leer el asunto de la reunión en Outlook: {e}")
        return None


# MeetingStatus: olMeetingCanceled, olMeetingReceivedAndCanceled. ResponseStatus: olResponseDeclined.
_CANCELED_STATUS = (5, 7)
_DECLINED_RESPONSE = 4


def _find_meeting_item(recording_time: datetime, meeting_name: str = None, for_title: bool = False):
    """La cita del calendario que corresponde a la grabacion, o None. Empareja
    por NOMBRE y, como respaldo, por HORA (ver find_meeting_participants).
    for_title: solo una cita que empiece junto a la grabacion, ni cancelada ni
    rechazada (ver find_meeting_subject)."""
    outlook = _get_outlook()
    ns = outlook.GetNamespace('MAPI')
    calendar = ns.GetDefaultFolder(9)  # olFolderCalendar

    rec_time = _naive(recording_time)
    # Desde 4 h antes: una reunión larga a la que entras tarde empezó mucho
    # antes de que se disparase la grabación.
    start = rec_time - timedelta(hours=4)
    end   = rec_time + timedelta(hours=3)

    items = calendar.Items
    items.Sort('[Start]')
    items.IncludeRecurrences = True

    filter_str = (
        f"[Start] >= '{start.strftime('%m/%d/%Y %I:%M %p')}' AND "
        f"[Start] <= '{end.strftime('%m/%d/%Y %I:%M %p')}'"
    )
    filtered = items.Restrict(filter_str)

    candidates = []
    for item in filtered:
        try:
            if getattr(item, 'AllDayEvent', False):
                continue
            if for_title and (getattr(item, 'MeetingStatus', 0) in _CANCELED_STATUS
                              or getattr(item, 'ResponseStatus', 0) == _DECLINED_RESPONSE):
                continue
            item_start = _naive(item.Start)
            if not hasattr(item_start, 'year'):
                continue
            try:
                item_end = _naive(item.End)
            except Exception:
                item_end = item_start
            running = item_start <= rec_time <= item_end
            candidates.append((item, abs(item_start - rec_time), running, item_start))
        except Exception as e:
            log.warning(f"Cita de calendario ilegible: {e}")
            continue

    if not candidates:
        log.info(f"Calendario sin citas cerca de {rec_time:%d/%m %H:%M}")
        return None

    best_item = None

    if meeting_name:
        scored = []
        for item, diff, *_ in candidates:
            try:
                subj = item.Subject or ''
            except Exception:
                subj = ''
            score = _name_score(meeting_name, subj)
            if score >= 0.5:
                scored.append((score, diff, item))
        if scored:
            scored.sort(key=lambda x: (-x[0], x[1]))
            best_item = scored[0][2]
            log.info(f"Reunión emparejada por nombre (score {scored[0][0]:.2f}): {best_item.Subject}")

    if best_item is None and meeting_name:
        # La grabacion SABE como se llama su reunion (Teams) y ninguna cita se llama
        # asi: no es de ninguna. Emparejar por hora cogia la reunion que el usuario se
        # saltaba (18/11/2026: una llamada con una persona, "Coll Corominas, Nil",
        # recibio los 6 invitados de la cita de las 12:00) y los metia en las minutas.
        log.info(f"Ninguna cita se llama como '{meeting_name}': no se empareja por hora")
        return None

    if best_item is None:
        # Primero las que EMPIEZAN junto a la grabación. Las que ya estaban
        # en marcha (la reunión anterior, que aún no ha acabado) solo valen
        # si no hay ninguna: con las dos a distancia 0 ganaba la anterior.
        near = [c for c in candidates if c[1] <= _MAX_TIME_FALLBACK]
        if near:
            best_item = min(near, key=lambda x: x[1])[0]
        elif for_title:
            return None
        else:
            running = [c for c in candidates if c[2]]
            if not running:
                log.info("Ninguna cita casa por nombre, empieza a menos de "
                         f"{_MAX_TIME_FALLBACK.seconds // 60} min ni está en curso")
                return None
            best_item = max(running, key=lambda x: x[3])[0]
        log.info(f"Reunión emparejada por hora: {best_item.Subject}")
    return best_item


def find_meeting_series(recording_time: datetime, title_hint: str = None,
                        window_minutes: int = 60) -> dict | None:
    """
    Looks up the Outlook calendar entry for a recording time and returns series info:
    subject, is_recurring, series_id (GlobalAppointmentID), organizer, recurrence_desc.
    Returns None if Outlook is unavailable or no matching item found.
    """
    try:
        outlook = _get_outlook()
        ns = outlook.GetNamespace('MAPI')
        calendar = ns.GetDefaultFolder(9)  # olFolderCalendar

        start = recording_time - timedelta(minutes=window_minutes)
        end   = recording_time + timedelta(hours=4)

        items = calendar.Items
        items.Sort('[Start]')
        items.IncludeRecurrences = True

        filter_str = (
            f"[Start] >= '{start.strftime('%m/%d/%Y %I:%M %p')}' AND "
            f"[Start] <= '{end.strftime('%m/%d/%Y %I:%M %p')}'"
        )
        filtered = items.Restrict(filter_str)

        candidates = []
        for item in filtered:
            try:
                item_start = item.Start
                if hasattr(item_start, 'year'):
                    diff = abs(item_start - recording_time.replace(tzinfo=None))
                    candidates.append((item, diff))
            except Exception:
                continue

        if not candidates:
            return None

        best_item = None
        if title_hint:
            scored = []
            for item, diff, *_ in candidates:
                try:
                    subj = item.Subject or ''
                except Exception:
                    subj = ''
                score = _name_score(title_hint, subj)
                if score >= 0.4:
                    scored.append((score, diff, item))
            if scored:
                scored.sort(key=lambda x: (-x[0], x[1]))
                best_item = scored[0][2]

        if best_item is None:
            best_item = min(candidates, key=lambda x: x[1])[0]

        try:
            is_recurring = bool(getattr(best_item, 'IsRecurring', False))
        except Exception:
            is_recurring = False

        try:
            subject = getattr(best_item, 'Subject', '') or ''
        except Exception:
            subject = ''

        series_id = None
        try:
            series_id = getattr(best_item, 'GlobalAppointmentID', None)
        except Exception:
            pass

        try:
            organizer = getattr(best_item, 'Organizer', '') or ''
        except Exception:
            organizer = ''

        recurrence_desc = ''
        if is_recurring:
            try:
                rp = best_item.GetRecurrencePattern()
                rt = rp.RecurrenceType
                # 0=daily 1=weekly 2=monthly 3=monthly-Nth 4=yearly 5=yearly-Nth 6=biweekly
                desc_map = {0: 'Daily', 1: 'Weekly', 2: 'Monthly', 3: 'Monthly',
                            4: 'Yearly', 5: 'Yearly', 6: 'Biweekly'}
                recurrence_desc = desc_map.get(rt, 'Recurring')
            except Exception:
                recurrence_desc = 'Recurring'

        log.info(f"find_meeting_series: '{subject}' recurring={is_recurring}")
        return {
            'subject': subject,
            'is_recurring': is_recurring,
            'series_id': series_id,
            'organizer': organizer,
            'recurrence_desc': recurrence_desc,
        }

    except Exception as e:
        log.debug(f"find_meeting_series: {e}")
        return None


def send_minutes_email(
    minutes_path: Path,
    html_path: Path,
    title: str,
    participants: list[dict],
    recording_time: datetime | None = None,
    language: str = 'es',
) -> bool:
    try:
        outlook = _get_outlook()
        mail = outlook.CreateItem(0)

        date_str = recording_time.strftime('%d/%m/%Y') if recording_time else ''
        if language == 'en':
            mail.Subject = f"Meeting notes: {title}" + (f" — {date_str}" if date_str else '')
        else:
            mail.Subject = f"Notas de reunión: {title}" + (f" — {date_str}" if date_str else '')

        for p in participants:
            mail.Recipients.Add(p.get('email') or p.get('name', ''))

        minutes_text = minutes_path.read_text(encoding='utf-8') if minutes_path.exists() else ''
        mail.HTMLBody = _build_email_html(minutes_text, title, date_str, participants, language)

        mail.Display()
        log.info(f"Borrador creado en Outlook con {len(participants)} destinatarios")
        return True

    except Exception as e:
        log.error(f"Error creando email en Outlook: {e}")
        return False


def _build_email_html(minutes_text: str, title: str, date_str: str, participants: list[dict], language: str = 'es') -> str:
    import json as _json

    user_name = ''
    try:
        from pathlib import Path as _Path
        settings_path = _Path(__file__).parent / 'settings.json'
        if settings_path.exists():
            user_name = _json.loads(settings_path.read_text(encoding='utf-8')).get('user_name', '')
    except Exception:
        pass

    body_html = _md_to_email_html(minutes_text)

    if language == 'en':
        saludo  = "Hi All,"
        intro   = f"Please find below the notes from our meeting <strong>{title}</strong>{(' on ' + date_str) if date_str else ''}. Review the key decisions and action items assigned."
        closing = f"Best regards,<br><strong>{user_name}</strong>" if user_name else "Best regards,"
    else:
        saludo  = "Hola a todos,"
        intro   = f"Os comparto las notas de nuestra reunión <strong>{title}</strong>{(' del ' + date_str) if date_str else ''}. A continuación encontraréis el resumen, las decisiones tomadas y las acciones pendientes."
        closing = f"Un saludo,<br><strong>{user_name}</strong>" if user_name else "Un saludo,"

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Calibri,Arial,sans-serif;font-size:14px;color:#1a1a1a;max-width:680px;line-height:1.5">
<p style="margin:0 0 12px">{saludo}</p>
<p style="margin:0 0 20px">{intro}</p>
<p style="margin:0 0 24px">{closing}</p>
<hr style="border:none;border-top:1px solid #ddd;margin:0 0 16px">
{body_html}
</body></html>"""


def _md_to_email_html(md_text: str) -> str:
    lines = md_text.splitlines()
    output = []
    i = 0
    in_list = False
    list_type = ''
    in_code = False

    while i < len(lines):
        line = lines[i]

        if line.strip().startswith('~~~') or line.strip().startswith('```'):
            in_code = not in_code
            i += 1
            continue
        if in_code:
            i += 1
            continue

        if '|' in line and i + 1 < len(lines) and re.match(r'[\|\s\-:]+', lines[i + 1]):
            table_lines = []
            while i < len(lines) and '|' in lines[i]:
                table_lines.append(lines[i])
                i += 1
            output.append(_md_table_to_html(table_lines))
            continue

        if line.startswith('### '):
            if in_list: output.append(f'</{list_type}>'); in_list = False; list_type = ''
            output.append(f'<h3 style="font-size:14px;margin:18px 0 6px;border-bottom:1px solid #eee;padding-bottom:4px">{_inline(line[4:])}</h3>')
        elif line.startswith('## '):
            if in_list: output.append(f'</{list_type}>'); in_list = False; list_type = ''
            output.append(f'<h2 style="font-size:15px;margin:22px 0 8px;border-bottom:1px solid #ccc;padding-bottom:4px">{_inline(line[3:])}</h2>')
        elif line.startswith('# '):
            if in_list: output.append(f'</{list_type}>'); in_list = False; list_type = ''
            output.append(f'<h2 style="font-size:16px;margin:22px 0 8px">{_inline(line[2:])}</h2>')
        elif re.match(r'^[-*]\s', line):
            if not in_list:
                output.append('<ul style="margin:6px 0;padding-left:20px">')
                in_list = True; list_type = 'ul'
            output.append(f'<li style="margin-bottom:4px">{_inline(line[2:])}</li>')
        elif re.match(r'^\d+\.\s', line):
            if not in_list:
                output.append('<ol style="margin:6px 0;padding-left:20px">')
                in_list = True; list_type = 'ol'
            output.append(f'<li style="margin-bottom:4px">{_inline(re.sub(r"^\d+\.\s", "", line))}</li>')
        elif line.strip() == '':
            if in_list: output.append(f'</{list_type}>'); in_list = False; list_type = ''
            if output and output[-1] != '':
                output.append('')
        else:
            if in_list: output.append(f'</{list_type}>'); in_list = False; list_type = ''
            output.append(f'<p style="margin:4px 0">{_inline(line)}</p>')

        i += 1

    if in_list:
        output.append(f'</{list_type}>')

    return '\n'.join(output)


def _md_table_to_html(table_lines: list[str]) -> str:
    rows = [
        [cell.strip() for cell in line.strip().strip('|').split('|')]
        for line in table_lines
        if not re.match(r'^[\|\s\-:]+$', line)
    ]
    if not rows:
        return ''

    header = rows[0]
    body   = rows[1:]

    th_cells = ''.join(
        f'<th style="text-align:left;padding:8px 12px;background:#f0f0f0;border:1px solid #ccc;font-weight:bold">{_inline(h)}</th>'
        for h in header
    )
    tr_rows = ''
    for ridx, row in enumerate(body):
        bg = '#fafafa' if ridx % 2 else '#ffffff'
        td_cells = ''.join(
            f'<td style="padding:7px 12px;border:1px solid #ddd;background:{bg}">{_inline(c if ci < len(row) else "")}</td>'
            for ci, c in enumerate(row)
        )
        tr_rows += f'<tr>{td_cells}</tr>\n'

    return (
        '<table style="border-collapse:collapse;width:100%;margin:12px 0;font-size:13px">'
        f'<thead><tr>{th_cells}</tr></thead>'
        f'<tbody>{tr_rows}</tbody>'
        '</table>'
    )


def _inline(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',         text)
    text = re.sub(r'`(.+?)`',       r'<code style="background:#f0f0f0;padding:1px 4px;border-radius:3px;font-size:12px">\1</code>', text)
    return text


def get_my_email() -> str | None:
    try:
        outlook = _get_outlook()
        ns = outlook.GetNamespace('MAPI')
        accounts = ns.Accounts
        if accounts.Count > 0:
            return accounts.Item(1).SmtpAddress
    except Exception:
        pass
    return None


def _esc(text) -> str:
    import html
    return html.escape(str(text if text is not None else ''))


def send_actions_email(
    title: str,
    date_str: str,
    actions: list[dict],
    participants: list[dict],
    language: str = 'es',
) -> bool:
    try:
        outlook = _get_outlook()
        mail = outlook.CreateItem(0)
        if language == 'en':
            mail.Subject = f"Action items: {title}" + (f" — {date_str}" if date_str else '')
        else:
            mail.Subject = f"Acciones de reunión: {title}" + (f" — {date_str}" if date_str else '')
        for p in participants:
            mail.Recipients.Add(p.get('email') or p.get('name', ''))
        mail.HTMLBody = _build_actions_email_html(title, date_str, actions, language)
        mail.Display()
        log.info(f"Borrador de acciones creado en Outlook ({len(actions)} acciones)")
        return True
    except Exception as e:
        log.error(f"Error creando email de acciones en Outlook: {e}")
        return False


def _build_actions_email_html(title: str, date_str: str, actions: list[dict], language: str = 'es') -> str:
    import json as _json

    user_name = ''
    try:
        settings_path = Path(__file__).parent / 'settings.json'
        if settings_path.exists():
            user_name = _json.loads(settings_path.read_text(encoding='utf-8')).get('user_name', '')
    except Exception:
        pass

    if language == 'en':
        saludo  = "Hi All,"
        intro   = f"Please find below the action items from our meeting <strong>{_esc(title)}</strong>{(' on ' + _esc(date_str)) if date_str else ''}."
        closing = f"Best regards,<br><strong>{_esc(user_name)}</strong>" if user_name else "Best regards,"
        heads   = ['Action', 'Owner', 'Date', 'Deadline']
        empty   = 'No action items were recorded.'
    else:
        saludo  = "Hola a todos,"
        intro   = f"Os comparto las acciones acordadas en nuestra reunión <strong>{_esc(title)}</strong>{(' del ' + _esc(date_str)) if date_str else ''}."
        closing = f"Un saludo,<br><strong>{_esc(user_name)}</strong>" if user_name else "Un saludo,"
        heads   = ['Acción', 'Owner', 'Fecha', 'Deadline']
        empty   = 'No se registraron acciones.'

    th = ''.join(
        f'<th style="text-align:left;padding:8px 12px;background:#f0f0f0;border:1px solid #ccc;font-weight:bold">{_esc(h)}</th>'
        for h in heads
    )
    rows_html = ''
    for idx, a in enumerate(actions):
        bg = '#fafafa' if idx % 2 else '#ffffff'
        cells = [
            a.get('title', '') or '',
            a.get('assignee', '') or '—',
            date_str or '—',
            a.get('deadline', '') or '—',
        ]
        tds = ''.join(
            f'<td style="padding:7px 12px;border:1px solid #ddd;background:{bg}">{_esc(c)}</td>'
            for c in cells
        )
        rows_html += f'<tr>{tds}</tr>\n'

    if actions:
        table = (
            '<table style="border-collapse:collapse;width:100%;font-size:13px">'
            f'<thead><tr>{th}</tr></thead><tbody>{rows_html}</tbody></table>'
        )
    else:
        table = f'<p>{empty}</p>'

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Calibri,Arial,sans-serif;font-size:14px;color:#1a1a1a;max-width:720px;line-height:1.5">
<p style="margin:0 0 12px">{saludo}</p>
<p style="margin:0 0 20px">{intro}</p>
<p style="margin:0 0 24px">{closing}</p>
<hr style="border:none;border-top:1px solid #ddd;margin:0 0 16px">
{table}
</body></html>"""


def send_transcript_email(
    title: str,
    date_str: str,
    transcript_path: Path,
    participants: list[dict],
    language: str = 'es',
) -> bool:
    try:
        tp = Path(transcript_path)
        if not tp.exists():
            log.error(f"send_transcript_email: no existe {tp}")
            return False
        outlook = _get_outlook()
        mail = outlook.CreateItem(0)
        if language == 'en':
            mail.Subject = f"Transcript: {title}" + (f" — {date_str}" if date_str else '')
            body = (f"Hi All,<br><br>Please find attached the full transcript of our meeting "
                    f"<strong>{_esc(title)}</strong>{(' on ' + _esc(date_str)) if date_str else ''}.<br><br>Best regards,")
        else:
            mail.Subject = f"Transcripción: {title}" + (f" — {date_str}" if date_str else '')
            body = (f"Hola a todos,<br><br>Adjunto la transcripción completa de nuestra reunión "
                    f"<strong>{_esc(title)}</strong>{(' del ' + _esc(date_str)) if date_str else ''}.<br><br>Un saludo,")
        for p in participants:
            mail.Recipients.Add(p.get('email') or p.get('name', ''))
        mail.HTMLBody = (
            '<body style="font-family:Calibri,Arial,sans-serif;font-size:14px;color:#1a1a1a;line-height:1.5">'
            f'<p>{body}</p></body>'
        )
        mail.Attachments.Add(str(tp.resolve()))
        mail.Display()
        log.info("Borrador de transcripción creado en Outlook con adjunto")
        return True
    except Exception as e:
        log.error(f"Error creando email de transcripción en Outlook: {e}")
        return False
