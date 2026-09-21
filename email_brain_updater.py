"""
Scans Outlook emails from the last 24h for project-relevant content
and triggers wiki updates. Called daily at 8am.
"""
import logging
import re
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


def run_daily_scan(projects: list, lang_fn=None) -> None:
    """
    projects: list of project dicts with 'id' and 'name' keys.
    lang_fn: callable(project_id) -> 'es'|'en', or None (defaults to 'es').
    """
    try:
        emails = _read_recent_emails(hours_back=24)
    except Exception as e:
        log.warning(f"EmailBrain: no se pudieron leer emails de Outlook: {e}")
        return

    if not emails:
        log.info("EmailBrain: sin emails nuevos en las últimas 24h")
        return

    log.info(f"EmailBrain: {len(emails)} emails leídos, analizando proyectos...")

    for project in projects:
        project_id = project.get('id', '')
        project_name = project.get('name', '')
        if not project_id or not project_name:
            continue

        relevant = _match_emails_to_project(emails, project_name)
        if not relevant:
            continue

        log.info(f"EmailBrain: {len(relevant)} emails relevantes para '{project_name}'")
        lang = lang_fn(project_id) if lang_fn else 'es'

        try:
            from brain_synthesizer import update_brain_wiki_from_emails
            update_brain_wiki_from_emails(project_id, project_name, relevant, lang)
        except Exception as e:
            log.warning(f"EmailBrain: error al actualizar wiki de '{project_name}': {e}")


def _read_recent_emails(hours_back: int = 24) -> list:
    """Reads Inbox + Sent emails from the last N hours via Outlook COM."""
    import win32com.client

    outlook = win32com.client.Dispatch('Outlook.Application')
    ns = outlook.GetNamespace('MAPI')

    cutoff = datetime.now() - timedelta(hours=hours_back)
    cutoff_str = cutoff.strftime('%m/%d/%Y %H:%M %p')

    emails = []
    # 6 = Inbox, 5 = Sent Items
    for folder_id in (6, 5):
        try:
            folder = ns.GetDefaultFolder(folder_id)
            items = folder.Items
            items.Sort('[ReceivedTime]', True)
            filtered = items.Restrict(f"[ReceivedTime] >= '{cutoff_str}'")
            for item in filtered:
                try:
                    emails.append({
                        'subject': str(item.Subject or ''),
                        'body': str(item.Body or '')[:2000],
                        'sender': str(getattr(item, 'SenderName', '') or ''),
                        'sender_email': str(getattr(item, 'SenderEmailAddress', '') or ''),
                        'to': str(getattr(item, 'To', '') or ''),
                        'received': str(getattr(item, 'ReceivedTime', '') or ''),
                    })
                except Exception:
                    continue
        except Exception as e:
            log.warning(f"EmailBrain: error leyendo carpeta {folder_id}: {e}")

    return emails


def _match_emails_to_project(emails: list, project_name: str) -> list:
    """
    Returns emails whose subject or body mentions the project.
    Matches full name and significant individual tokens (>3 chars).
    """
    name_lower = project_name.lower()
    tokens = [w for w in re.split(r'\W+', name_lower) if len(w) > 3]

    relevant = []
    for email in emails:
        haystack = (email['subject'] + ' ' + email['body'][:800]).lower()
        if name_lower in haystack or any(tok in haystack for tok in tokens):
            relevant.append(email)

    return relevant
