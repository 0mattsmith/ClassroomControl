"""Lightweight machine inventory used by the master's roster grid."""

from __future__ import annotations

import getpass
import platform
import socket


def collect() -> dict:
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip = "127.0.0.1"
    return {
        "hostname": socket.gethostname(),
        "user": getpass.getuser(),
        "os": platform.system(),
        "os_version": platform.release(),
        "arch": platform.machine(),
        "ip": ip,
    }
