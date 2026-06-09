"""Shared configuration helpers and path resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "ClassControl"


def _effective_home() -> Path:
    """Return ``~`` normally, but when we're running as root via ``sudo``
    return the calling user's home instead.

    Without this, ``sudo ./scripts/run_client.sh`` would put the client's
    config under ``/var/root/Library/…`` while the master would still
    look under ``/Users/<you>/Library/…`` — meaning the auth keys never
    match and the master can't connect.
    """
    home = Path.home()
    if str(home) in ("/var/root", "/root"):
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user:
            try:
                import pwd
                return Path(pwd.getpwnam(sudo_user).pw_dir)
            except Exception:
                pass
    return home


def user_config_dir(role: str) -> Path:
    """Return the per-user config directory for the given role (master|client)."""
    home = _effective_home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / APP_NAME
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(home))) / APP_NAME
    else:
        base = home / ".config" / APP_NAME.lower()
    path = base / role
    path.mkdir(parents=True, exist_ok=True)
    return path


def shared_files_dir(role: str) -> Path:
    """Default location for transferred files (Downloads on the student side)."""
    if role == "client":
        path = _effective_home() / "Downloads" / APP_NAME
    else:
        path = user_config_dir(role) / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def key_path(role: str) -> Path:
    return user_config_dir(role) / "auth.key"


def cert_paths(role: str) -> tuple[Path, Path]:
    d = user_config_dir(role)
    return d / "cert.pem", d / "key.pem"


def roster_path() -> Path:
    return user_config_dir("master") / "roster.json"


def activity_log_path() -> Path:
    return user_config_dir("master") / "activity.log"
