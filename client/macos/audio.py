"""Audio volume / mute control via osascript, plus a *sticky* silence
watchdog the teacher can toggle from the master UI.

Soft actions (``set_volume`` / ``set_muted``) are one-off — the student
can change them right back. The locked / sticky path:

* :func:`lock_audio` mutes + sets volume to 0, then schedules a 1 Hz
  watchdog that immediately re-mutes if anything (Spotlight beep,
  the student themselves, an app, anything) un-mutes the output.
* :func:`unlock_audio` cancels the watchdog and restores the unmuted
  state.

Returns the usual ``{"ok": bool, "reason": str}`` shape.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess

LOG = logging.getLogger("classcontrol.client.audio")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _osa(*args: str) -> int:
    return subprocess.run(
        ["osascript", "-e", " ".join(args)], capture_output=True,
    ).returncode


def _is_muted() -> bool:
    """Return current output-mute state, best-effort."""
    p = subprocess.run(
        ["osascript", "-e", "output muted of (get volume settings)"],
        capture_output=True, text=True,
    )
    return "true" in (p.stdout or "").lower()


# ---------------------------------------------------------------------------
# Soft actions (unchanged — used by master's existing AUDIO ops)
# ---------------------------------------------------------------------------


def set_volume(percent: int) -> int:
    percent = max(0, min(100, int(percent)))
    return subprocess.run(
        ["osascript", "-e", f"set volume output volume {percent}"],
        capture_output=True,
    ).returncode


def set_muted(muted: bool) -> int:
    flag = "true" if muted else "false"
    return subprocess.run(
        ["osascript", "-e", f"set volume output muted {flag}"],
        capture_output=True,
    ).returncode


# ---------------------------------------------------------------------------
# Sticky silence — lock_audio / unlock_audio with a watchdog
# ---------------------------------------------------------------------------


_locked: bool = False
_watch_task: asyncio.Task | None = None


def is_locked() -> bool:
    return _locked


async def _watchdog():
    """Every second, force the output back to muted + 0% if anything has
    drifted. Idempotent: setting an already-muted output to muted is a
    cheap no-op."""
    LOG.info("audio-silence watchdog started")
    try:
        while True:
            try:
                if not _is_muted():
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
            LOG.info("lock_audio: scheduled watchdog on loop %r", id(loop))
    except RuntimeError as exc:
        # No running loop — daemon hasn't started one yet. The state is
        # set, the next loop launch will pick it up; this is harmless.
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
