"""Persistent computer roster (the teacher's list of student machines)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from shared import config, protocol


@dataclass
class Computer:
    id: str               # stable internal id (uuid hex)
    name: str             # human-friendly label, e.g. "Pod-12"
    host: str             # IP or DNS name
    port: int = protocol.DEFAULT_PORT
    group: str = "default"
    mac: str = ""         # optional, used for wake-on-LAN
    notes: str = ""


@dataclass
class Roster:
    computers: list[Computer] = field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Roster":
        path = path or config.roster_path()
        if not path.exists():
            return cls(computers=[])
        try:
            data = json.loads(path.read_text())
            return cls(computers=[Computer(**c) for c in data.get("computers", [])])
        except Exception:
            return cls(computers=[])

    def save(self, path: Optional[Path] = None) -> None:
        path = path or config.roster_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"computers": [asdict(c) for c in self.computers]}, indent=2,
        ))

    def add(self, c: Computer) -> None:
        self.computers.append(c)

    def remove(self, computer_id: str) -> None:
        self.computers = [c for c in self.computers if c.id != computer_id]

    def get(self, computer_id: str) -> Optional[Computer]:
        for c in self.computers:
            if c.id == computer_id:
                return c
        return None

    def groups(self) -> list[str]:
        return sorted({c.group or "default" for c in self.computers})
