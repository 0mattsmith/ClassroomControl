"""Append-only audit log of every teacher command."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from shared import config


def log(action: str, target: str = "", detail: dict | None = None,
        path: Optional[Path] = None) -> None:
    path = path or config.activity_log_path()
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "action": action,
        "target": target,
        "detail": detail or {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def tail(n: int = 200, path: Optional[Path] = None) -> list[dict]:
    path = path or config.activity_log_path()
    if not path.exists():
        return []
    lines = path.read_text().splitlines()[-n:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out
