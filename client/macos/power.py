"""Power management commands for macOS."""

from __future__ import annotations

import subprocess


def _shell(cmd: list[str]) -> int:
    return subprocess.run(cmd, capture_output=True).returncode


def shutdown() -> int:
    # `shutdown -h now` requires sudo; fall back to `osascript` "shut down"
    rc = _shell(["sudo", "-n", "shutdown", "-h", "now"])
    if rc != 0:
        rc = _shell([
            "osascript", "-e",
            'tell application "System Events" to shut down'
        ])
    return rc


def restart() -> int:
    rc = _shell(["sudo", "-n", "shutdown", "-r", "now"])
    if rc != 0:
        rc = _shell([
            "osascript", "-e",
            'tell application "System Events" to restart'
        ])
    return rc


def sleep() -> int:
    return _shell(["pmset", "sleepnow"])


def logout() -> int:
    # Hard logout (no confirmation dialog) - keyword "log out"
    return _shell([
        "osascript", "-e",
        'tell application "System Events" to log out'
    ])


def wake() -> int:
    # No-op locally; documented as a network operation triggered by the master.
    return 0
