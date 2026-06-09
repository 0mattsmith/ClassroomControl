"""Audio volume / mute control via osascript."""

from __future__ import annotations

import subprocess


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
