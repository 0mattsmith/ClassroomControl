"""Audio volume / mute control on Windows via pycaw + Core Audio.

If ``pycaw`` isn't installed we fall back to PowerShell's
WScript.Shell SendKeys volume keys, which only gets us coarse
volume up/down (not a target percentage), so it's labelled as a
last-resort fallback.
"""

from __future__ import annotations

import subprocess

try:
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL    # type: ignore
    from pycaw.pycaw import (          # type: ignore
        AudioUtilities, IAudioEndpointVolume,
    )
    _HAVE_PYCAW = True
except Exception:                       # pragma: no cover
    AudioUtilities = None
    IAudioEndpointVolume = None
    _HAVE_PYCAW = False


def _get_endpoint():
    if not _HAVE_PYCAW:
        return None
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def set_volume(percent: int) -> int:
    percent = max(0, min(100, int(percent)))
    ep = _get_endpoint()
    if ep is None:
        # Fallback: nudge with VK_VOLUME_UP/DOWN via PowerShell SendKeys
        delta_keys = "{VOLUME_UP}" if percent >= 50 else "{VOLUME_DOWN}"
        cmd = (
            "powershell", "-NoProfile", "-Command",
            f"(New-Object -ComObject WScript.Shell).SendKeys('{delta_keys}')",
        )
        return subprocess.run(cmd, capture_output=True).returncode
    ep.SetMasterVolumeLevelScalar(percent / 100.0, None)
    return 0


def set_muted(muted: bool) -> int:
    ep = _get_endpoint()
    if ep is None:
        cmd = (
            "powershell", "-NoProfile", "-Command",
            "(New-Object -ComObject WScript.Shell).SendKeys('{VOLUME_MUTE}')",
        )
        return subprocess.run(cmd, capture_output=True).returncode
    ep.SetMute(1 if muted else 0, None)
    return 0
