import logging
import threading
import time

from config import TEAMS_POLL_INTERVAL, TEAMS_REQUIRED_CONFIRMATIONS

log = logging.getLogger(__name__)

_TEAMS_PROCESSES = {'ms-teams.exe', 'teams.exe', 'msteams.exe'}

# Teams classic keywords
CALL_TITLE_KEYWORDS = [
    'microsoft teams meeting', 'teams meeting',
    'in a call', 'en llamada', 'teams call', 'en curso',
]

# Pages that appear in Teams 2.0 title bar when NOT in a call
_TEAMS_GENERIC_PAGES = {
    'microsoft teams', 'teams', 'calendar', 'chat', 'activity',
    'calls', 'files', 'apps', 'help', '', 'copilot',
    # Teams app tabs that trigger false positives
    'amethyst', 'planner', 'tasks', 'viva', 'loop', 'whiteboard',
    'sharepoint', 'forms', 'power apps', 'power bi', 'stream',
    'wiki', 'notes', 'shifts', 'approvals', 'praise',
}


def _teams_pids() -> list[int]:
    try:
        import psutil
        return [p.pid for p in psutil.process_iter(['name'])
                if (p.info['name'] or '').lower() in _TEAMS_PROCESSES]
    except Exception:
        return []


def _check_window_titles(pids: list[int]) -> tuple[bool, bool, str | None, list[str]]:
    """Returns (classic_match, teams2_match, meeting_name, candidates).
    classic_match: keyword found ('in a call', etc.) — reliable alone.
    teams2_match: pipe-format title found — requires audio to avoid false
                  positives from Teams app tabs (Planner, Amethyst, etc.).
    candidates: todos los nombres con formato de reunión, en orden Z. El caller
                los necesita para descartar ventanas de reuniones ya terminadas
                que Teams deja abiertas y que dan nombre a la grabación
                equivocada."""
    try:
        import win32gui
        import win32process

        meeting_name = None
        classic_match = False
        teams2_match = False
        candidates: list[str] = []

        def cb(hwnd, _):
            nonlocal classic_match, teams2_match, meeting_name
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid not in pids:
                    return
                raw = win32gui.GetWindowText(hwnd)
                if not raw:
                    return
                title_lower = raw.lower()

                # Teams classic: keyword match — very reliable
                if any(kw in title_lower for kw in CALL_TITLE_KEYWORDS):
                    classic_match = True
                    if '|' in raw:
                        candidate = raw.split('|')[0].strip()
                        if candidate.lower() not in _TEAMS_GENERIC_PAGES:
                            meeting_name = candidate
                            if candidate not in candidates:
                                candidates.append(candidate)
                    return

                # Teams 2.0: "<Meeting> | Microsoft Teams" (1 pipe, reuniones rápidas/personales)
                # o "<Meeting> | <Org> [| email] | Microsoft Teams" (2+ pipes, reuniones formales).
                # Propenso a falsos positivos con tabs de apps — el caller exige audio activo.
                if raw.endswith('Microsoft Teams') and raw.count('|') >= 1:
                    parts = [p.strip() for p in raw.split('|')]
                    first = parts[0].strip()
                    if first.lower() not in _TEAMS_GENERIC_PAGES:
                        teams2_match = True
                        if first not in candidates:
                            candidates.append(first)
                        if meeting_name is None:
                            meeting_name = first
            except Exception:
                pass

        win32gui.EnumWindows(cb, None)
        return classic_match, teams2_match, meeting_name, candidates
    except Exception:
        return False, False, None, []


def _check_audio_session(pids: list[int]) -> bool:
    """Detecta si Teams tiene sesión de audio activa (State=1)."""
    try:
        from pycaw.pycaw import AudioUtilities
        for s in AudioUtilities.GetAllSessions():
            if s.Process and s.Process.pid in pids and s.State == 1:
                return True
        return False
    except Exception:
        return False


def _check_mic_session(pids: list[int]) -> bool:
    """Detecta si Teams está capturando micrófono activamente.
    Más fiable que la sesión de render para Teams 2.0 (WebRTC usa WASAPI para captura aunque no para playback)."""
    try:
        from pycaw.pycaw import AudioUtilities, IAudioSessionManager2, AudioSession
        from comtypes import CLSCTX_ALL
        mic = AudioUtilities.GetMicrophone()
        interface = mic.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
        manager = interface.QueryInterface(IAudioSessionManager2)
        enumerator = manager.GetSessionEnumerator()
        for i in range(enumerator.GetCount()):
            try:
                s = AudioSession(enumerator.GetSession(i))
                if s.Process and s.Process.pid in pids and s.State == 1:
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def get_current_meeting_name() -> str | None:
    pids = _teams_pids()
    if not pids:
        return None
    _, _, name, _ = _check_window_titles(pids)
    return name


class TeamsCallDetector:
    def __init__(self, poll_interval=TEAMS_POLL_INTERVAL, required_confirmations=TEAMS_REQUIRED_CONFIRMATIONS):
        self._poll = poll_interval
        self._required          = required_confirmations              # polls para detectar inicio (2 = 6s)
        self._required_end      = max(required_confirmations * 5, 10)  # polls fin conservador (10 = 30s)
        self._required_end_fast = 5   # polls fin rápido cuando título es genérico (5 = 15s)
        # Polls con título de llamada pero sin audio NI micrófono antes de dejar
        # de creer al título (15 = 45s). Teams deja abiertas ventanas de
        # reuniones ya terminadas con el mismo formato de título, y sin este
        # límite una de ellas mantenía la grabación en marcha indefinidamente.
        self._required_end_silent = 15
        self._no_audio_streak = 0
        # Un título de reunión visto tantos polls seguidos SIN llamada es una
        # ventana que Teams dejó abierta de una reunión ya terminada (20 = 60s).
        # No debe dar nombre a la siguiente grabación. El margen deja fuera el
        # caso normal de abrir la reunión y unirse acto seguido.
        self._stale_title_polls = 20
        self._idle_title_counts: dict[str, int] = {}
        self._stale_titles: set[str] = set()
        self._stale_logged: set[str] = set()
        self._required_name_chg = max(required_confirmations * 5, 10)  # polls cambio de reunión (10 = 30s)
        self._in_call = False
        self._call_streak = 0
        self._call_streak_has_title = False  # True si algún poll del streak tuvo título
        self._no_call_streak = 0
        self._current_meeting_name: str | None = None
        self._name_change_candidate: str | None = None
        self._name_change_streak = 0
        self._title_went_generic = False  # título volvió a genérico mientras en llamada
        self._call_generation = 0
        self._call_declined = False
        self._declined_generation: int | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self.on_call_started = None
        self.on_call_ended = None
        # Si está definido, se llama sin args y debe devolver bool.
        # Cuando devuelve True los cambios de nombre no disparan eventos (se absorben silenciosamente).
        self.is_active_recording = None

    @property
    def in_call(self) -> bool:
        return self._in_call

    @property
    def call_generation(self) -> int:
        return self._call_generation

    @property
    def call_declined(self) -> bool:
        if not self._call_declined:
            return False
        # Solo bloquea si seguimos en la misma llamada (generación) que fue rechazada
        return self._call_generation == self._declined_generation

    def set_declined(self):
        self._call_declined = True
        self._declined_generation = self._call_generation

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name='TeamsDetector')
        self._thread.start()
        log.info("TeamsCallDetector iniciado")

    def stop(self):
        self._stop_event.set()

    def reset_call_state(self):
        """Resetea el estado de llamada. Llamar cuando el usuario rechaza grabar para que
        el próximo ciclo de detección empiece limpio."""
        self._in_call = False
        self._call_streak = 0
        self._call_streak_has_title = False
        self._no_call_streak = 0
        self._no_audio_streak = 0
        self._title_went_generic = False
        self._name_change_candidate = None
        self._name_change_streak = 0
        self._current_meeting_name = None
        self._idle_title_counts.clear()
        self._stale_titles = set()
        log.info("Estado de llamada reseteado")

    def _loop(self):
        _com_initialized = False
        try:
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            _com_initialized = True
        except Exception:
            pass

        try:
            while not self._stop_event.is_set():
                try:
                    pids = _teams_pids()
                    detected_name = None
                    audio_active = False
                    title_active = False
                    if pids:
                        audio_active = _check_audio_session(pids)
                        (classic_match, teams2_match, detected_name,
                         title_candidates) = _check_window_titles(pids)
                        # Para INICIAR detección: teams2_match requiere mic activo para evitar
                        # falsos positivos de tabs de apps (Planner, Amethyst…).
                        # Para MANTENER una llamada ya detectada: no exigimos mic (pycaw puede
                        # perderlo momentáneamente con WebRTC) para evitar cortes falsos.
                        if self._in_call:
                            # El título mantiene la llamada viva, pero no para
                            # siempre: si no hay ni reproducción ni captura
                            # durante _required_end_silent polls, el título es de
                            # una ventana de reunión ya terminada y se ignora.
                            mic_active = _check_mic_session(pids)
                            if audio_active or mic_active:
                                self._no_audio_streak = 0
                            else:
                                self._no_audio_streak += 1
                                if self._no_audio_streak == self._required_end_silent:
                                    log.info(
                                        "Título de llamada sin audio ni micrófono durante "
                                        f"{int(self._required_end_silent * self._poll)}s: se ignora "
                                        "(ventana de una reunión ya terminada)"
                                    )
                            title_active = ((classic_match or teams2_match)
                                            and self._no_audio_streak < self._required_end_silent)
                        else:
                            self._no_audio_streak = 0
                            # 298b90a quito esta confirmacion con microfono, por
                            # considerar que el formato exacto del titulo ya basta.
                            # Se mantiene a proposito: una ventana que Teams deja
                            # abierta al colgar cumple ese formato exacto
                            # ("<Reunion> | <Persona> | Microsoft Teams") sin que haya
                            # ninguna llamada, y sin confirmacion arranca una grabacion
                            # de una reunion que no existe. Pasó de verdad: una call se
                            # guardo con el nombre de una reunion de dos horas antes.
                            # Tampoco sirve apoyarse en _stale_titles aqui: marcar un
                            # titulo como residual exige observarlo 20 polls, y la
                            # deteccion arranca en 2.
                            mic_active = _check_mic_session(pids) if teams2_match else False
                            title_active = classic_match or (teams2_match and (mic_active or audio_active))

                    # Para INICIAR: título O audio. Audio solo cubre llamadas 1:1 en
                    # Teams 2.0 donde el título siempre es genérico. Para evitar falsos
                    # positivos por notificaciones (< 18s) se exige 6 polls cuando es
                    # audio sin título, vs 2 polls cuando hay título confirmado.
                    # Para MANTENER: audio O título es suficiente.
                    #
                    # Cuando hay título en formato pipe (teams2_match), NO dejamos
                    # que audio solo arranque la detección: carpetas y canales de Teams
                    # cumplen el formato pipe Y pueden tener audio activo (notificaciones,
                    # vídeos en el canal). Solo el audio SIN título pipe cubre 1:1 calls.
                    if not self._in_call:
                        active = title_active or (audio_active and not teams2_match)
                    else:
                        active = title_active or audio_active

                    # Un título de reunión que sigue ahí sin ninguna señal de
                    # llamada es una ventana que Teams no cerró al colgar. Se
                    # cuentan los polls que lleva así; pasado el umbral deja de
                    # servir para nombrar grabaciones. Los contadores solo se
                    # tocan fuera de llamada, así que durante una reunión la
                    # lista de residuales queda congelada.
                    if not active and not self._in_call:
                        seen = set(title_candidates)
                        for known in list(self._idle_title_counts):
                            if known not in seen:
                                del self._idle_title_counts[known]
                        for name in seen:
                            self._idle_title_counts[name] = self._idle_title_counts.get(name, 0) + 1
                    self._stale_titles = {
                        name for name, n in self._idle_title_counts.items()
                        if n >= self._stale_title_polls
                    }
                    # Olvidar el aviso de los títulos que ya no están, para que
                    # si el mismo nombre reaparece se vuelva a avisar una vez.
                    self._stale_logged &= self._stale_titles

                    # Mejor sin nombre que con el de otra reunión: el fichero de
                    # audio acababa llamándose como una reunión de horas antes.
                    if detected_name and detected_name in self._stale_titles:
                        fresh = [c for c in title_candidates if c not in self._stale_titles]
                        if fresh:
                            detected_name = fresh[0]
                        else:
                            # Una vez por título, no en cada poll: una ventana de
                            # Teams abierta toda la tarde escribía una línea cada
                            # 3s (3,7 MB de log) y tapaba todo lo demás.
                            if detected_name not in self._stale_logged:
                                self._stale_logged.add(detected_name)
                                log.info(f"Ignorando título residual '{detected_name}' "
                                         "(ventana de una reunión ya terminada)")
                            detected_name = None

                    # Detectar cuándo el título vuelve a ser genérico (señal fuerte de fin)
                    if self._in_call and not title_active:
                        self._title_went_generic = True

                    if active:
                        self._call_streak += 1
                        if title_active:
                            self._call_streak_has_title = True
                        self._no_call_streak = 0
                    else:
                        self._no_call_streak += 1
                        self._call_streak = 0
                        self._call_streak_has_title = False
                        detected_name = None

                    required_now = self._required if self._call_streak_has_title else self._required * 3
                    if not self._in_call and self._call_streak >= required_now:
                        self._call_generation += 1
                        self._in_call = True
                        self._title_went_generic = False
                        self._call_streak_has_title = False
                        self._current_meeting_name = detected_name
                        log.info("Llamada Teams detectada")
                        if self.on_call_started:
                            threading.Thread(target=self.on_call_started, daemon=True,
                                             name='TeamsCallStarted').start()

                    elif (self._in_call and active
                          and detected_name and self._current_meeting_name
                          and detected_name != self._current_meeting_name):
                        # Nombre diferente — confirmar durante N polls antes de tratarlo como reunión nueva.
                        # Si el título ya fue genérico (llamada anterior terminó brevemente), confirmar
                        # más rápido: la reunión anterior terminó y empezó una nueva.
                        required_chg = (max(self._required, 2)
                                        if self._title_went_generic
                                        else self._required_name_chg)
                        if detected_name == self._name_change_candidate:
                            self._name_change_streak += 1
                        else:
                            self._name_change_candidate = detected_name
                            self._name_change_streak = 1
                        if self._name_change_streak >= required_chg:
                            recording_active = self.is_active_recording and self.is_active_recording()
                            if recording_active and not self._title_went_generic:
                                # Cambio de título dentro de la misma reunión (compartir pantalla, etc.)
                                log.info(f"Nombre actualizado (grabando): '{self._current_meeting_name}' → '{detected_name}'")
                            else:
                                log.info(f"Cambio de reunión confirmado: '{self._current_meeting_name}' → '{detected_name}'")
                                if self.on_call_ended:
                                    threading.Thread(target=self.on_call_ended, daemon=True,
                                                     name='TeamsCallEnded').start()
                                time.sleep(0.5)
                                if self.on_call_started:
                                    threading.Thread(target=self.on_call_started, daemon=True,
                                                     name='TeamsCallStarted').start()
                            self._current_meeting_name = detected_name
                            self._title_went_generic = False
                            self._name_change_candidate = None
                            self._name_change_streak = 0

                    else:
                        # Nombre estable — resetear cualquier candidato de cambio pendiente
                        self._name_change_candidate = None
                        self._name_change_streak = 0

                    # Fin rápido: título claramente genérico + sin audio por _required_end_fast polls (15s)
                    # Cubre el caso de colgar y llamar rápido a otra persona (el streak largo nunca llegaría).
                    fast_end = (self._title_went_generic and not audio_active
                                and self._no_call_streak >= self._required_end_fast)
                    slow_end = self._no_call_streak >= self._required_end

                    if self._in_call and (fast_end or slow_end):
                        self._in_call = False
                        self._call_declined = False
                        self._declined_generation = None
                        self._title_went_generic = False
                        self._no_audio_streak = 0
                        self._current_meeting_name = None
                        self._name_change_candidate = None
                        self._name_change_streak = 0
                        # Reset stale-title tracking so the next call starts clean.
                        # Without this, titles from meetings seen before this call
                        # remain permanently blacklisted for the whole session.
                        self._idle_title_counts.clear()
                        self._stale_titles = set()
                        log.info(f"Llamada Teams finalizada ({'rapido' if fast_end else 'normal'})")
                        if self.on_call_ended:
                            threading.Thread(target=self.on_call_ended, daemon=True,
                                             name='TeamsCallEnded').start()

                except Exception as e:
                    log.warning(f"TeamsDetector error: {e}")

                self._stop_event.wait(self._poll)
        finally:
            if _com_initialized:
                try:
                    import pythoncom
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
