"""Launch apps, files, or URLs on Windows.

* URLs (http:, https:, mailto:, ftp:, file:) -> ``os.startfile`` opens
  with the user's default handler (browser, mail client, etc.).
* App names with no path -> resolved via the ``start`` shell command,
  which checks the user's PATH and the ``App Paths`` registry keys.
* Absolute paths -> ``os.startfile`` directly.
"""

from __future__ import annotations

import os
import subprocess
from urllib.parse import urlparse


def open_target(target: str) -> int:
    if not target:
        return 1

    parsed = urlparse(target)
    if parsed.scheme in ("http", "https", "ftp", "mailto", "file"):
        try:
            os.startfile(target)   # type: ignore[attr-defined]
            return 0
        except Exception:
            return _start_via_cmd(target)

    if os.path.isabs(target) and os.path.exists(target):
        try:
            os.startfile(target)   # type: ignore[attr-defined]
            return 0
        except Exception:
            return _start_via_cmd(target)

    # Treat as an app name or registered command
    return _start_via_cmd(target)


def _start_via_cmd(target: str) -> int:
    # `start` is a cmd.exe builtin; the empty "" is the window title.
    return subprocess.run(
        ["cmd", "/c", "start", "", target],
        capture_output=True, shell=False,
    ).returncode


def reveal_target(path: str) -> int:
    """Open Explorer with ``path`` highlighted in its parent folder.

    Uses ``explorer /select,<path>`` — the comma-glued form is required
    so Windows treats it as one argument. Returns the subprocess exit
    code (Explorer often returns 1 even on success, so the caller should
    not block on that).
    """
    if not path:
        return 1
    return subprocess.run(
        ["explorer", f"/select,{path}"], capture_output=True,
    ).returncode
