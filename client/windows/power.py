"""Power management commands for Windows."""

from __future__ import annotations

import subprocess


def _run(args: list[str]) -> int:
    return subprocess.run(args, capture_output=True).returncode


def shutdown() -> int:
    return _run(["shutdown.exe", "/s", "/f", "/t", "0"])


def restart() -> int:
    return _run(["shutdown.exe", "/r", "/f", "/t", "0"])


def sleep() -> int:
    # Use the documented rundll32 entry point.
    return _run([
        "rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"
    ])


def logout() -> int:
    return _run(["shutdown.exe", "/l", "/f"])


def wake() -> int:
    # No-op locally; documented as a network operation triggered by the master.
    return 0
