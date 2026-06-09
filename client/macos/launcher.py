"""Launch apps, files, or URLs on macOS via the `open` command."""

from __future__ import annotations

import subprocess
from urllib.parse import urlparse


def open_target(target: str) -> int:
    """Open ``target`` which may be an app name, file path, or URL."""
    if not target:
        return 1

    parsed = urlparse(target)
    if parsed.scheme in ("http", "https", "ftp", "mailto", "file"):
        return subprocess.run(["open", target], capture_output=True).returncode

    # Heuristic: looks like an app name -> use `-a`
    if target.lower().endswith(".app") or " " in target or "/" not in target:
        rc = subprocess.run(
            ["open", "-a", target], capture_output=True
        ).returncode
        if rc == 0:
            return 0
    return subprocess.run(["open", target], capture_output=True).returncode


def reveal_target(path: str) -> int:
    """Open Finder with ``path`` highlighted in its parent folder.

    Uses ``open -R <path>``, the canonical macOS "reveal in Finder"
    command. Returns the subprocess exit code (0 = success).
    """
    if not path:
        return 1
    return subprocess.run(["open", "-R", path], capture_output=True).returncode
