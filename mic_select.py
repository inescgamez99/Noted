"""Elegir el micrófono que está usando Teams.

Noted grababa siempre el micro predeterminado de Windows (el del portátil).
Con unos auriculares Bluetooth, Teams usa el micro de los auriculares y el del
portátil queda en silencio: la pista de quien graba salía vacía y en la minuta
faltaban todas sus intervenciones (pasó en una reunión real de 29 min).

Aquí se busca el micro en el que Teams (o sus subprocesos WebView2) tiene una
sesión de captura activa. AudioRecorder lo pone el primero de sus candidatos y
lo sigue durante la grabación; si no se encuentra, el predeterminado.
"""
import logging

log = logging.getLogger(__name__)


def teams_capture_device() -> str | None:
    """Devuelve el nombre del micro donde Teams está capturando ahora, o None.

    Returns:
        FriendlyName del endpoint de captura con sesión activa de Teams.
    """
    try:
        import comtypes
        comtypes.CoInitialize()
        from comtypes import CLSCTX_ALL
        from pycaw.constants import DEVICE_STATE, EDataFlow
        from pycaw.pycaw import AudioUtilities, IAudioSessionControl2, IAudioSessionManager2

        from teams_detector import _teams_audio_pids, _teams_pids
    except Exception as e:
        log.warning(f"No se puede consultar el micro de Teams: {e}")
        return None
    pids = _teams_pids()
    if not pids:
        return None
    apids = _teams_audio_pids(pids)
    try:
        coll = AudioUtilities.GetDeviceEnumerator().EnumAudioEndpoints(
            EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value)
        for i in range(coll.GetCount()):
            dev = coll.Item(i)
            mgr = dev.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None) \
                     .QueryInterface(IAudioSessionManager2)
            sessions = mgr.GetSessionEnumerator()
            for j in range(sessions.GetCount()):
                ctl = sessions.GetSession(j).QueryInterface(IAudioSessionControl2)
                if ctl.GetProcessId() in apids and ctl.GetState() == 1:
                    return AudioUtilities.CreateDevice(dev).FriendlyName
    except Exception as e:
        log.warning(f"No se pudo ver qué micro usa Teams: {e}")
    return None


def input_device_index(friendly: str) -> int | None:
    """Índice de sounddevice (API MME, que remuestrea sola) para ese micro.

    Args:
        friendly: nombre del endpoint tal como lo da Windows.

    Returns:
        Índice del dispositivo, o None si no está. MME corta los nombres a 31
        caracteres, por eso se compara por prefijo.
    """
    import sounddevice as sd
    try:
        mme = next(i for i, h in enumerate(sd.query_hostapis()) if h['name'] == 'MME')
    except StopIteration:
        return None
    for i, d in enumerate(sd.query_devices()):
        name = d['name'] or ''
        if d['hostapi'] == mme and d['max_input_channels'] > 0 and name \
                and 'Sound Mapper' not in name and friendly.startswith(name):
            return i
    return None
