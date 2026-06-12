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


# ---------------------------------------------------------------------------
# Sticky silence — lock_audio / unlock_audio with a watchdog
# ---------------------------------------------------------------------------

import asyncio
import logging

LOG = logging.getLogger("classcontrol.client.audio")

_locked: bool = False
_watch_task: "asyncio.Task | None" = None


def is_locked() -> bool:
    return _locked


def _current_mute() -> bool:
    ep = _get_endpoint()
    if ep is None:
        return False
    try:
        return bool(ep.GetMute())
    except Exception:
        return False


async def _watchdog():
    LOG.info("audio-silence watchdog started")
    try:
        while True:
            try:
                if not _current_mute():
                    set_muted(True)
                    LOG.info("re-muted after student unmute")
                set_volume(0)
            except Exception:
                LOG.exception("audio watchdog tick failed")
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        return


def lock_audio() -> dict:
    """Mute + 0% volume + start the 1 Hz re-mute watchdog. Idempotent."""
    global _locked, _watch_task
    set_muted(True)
    set_volume(0)
    _locked = True
    try:
        loop = asyncio.get_event_loop()
        if _watch_task is None or _watch_task.done():
            _watch_task = loop.create_task(_watchdog())
            LOG.info("lock_audio: watchdog scheduled on loop %r", id(loop))
    except RuntimeError as exc:
        LOG.warning("lock_audio: no asyncio loop yet (%s)", exc)
    return {"ok": True, "reason": ""}


def unlock_audio() -> dict:
    global _locked, _watch_task
    _locked = False
    if _watch_task and not _watch_task.done():
        _watch_task.cancel()
        LOG.info("unlock_audio: watchdog cancelled")
    _watch_task = None
    set_muted(False)
    return {"ok": True, "reason": ""}
