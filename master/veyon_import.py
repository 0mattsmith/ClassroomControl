"""
Import a Veyon configuration into ClassControl's roster.

Veyon stores its "Computer Directory" (rooms + machines) inside its
main configuration file. The schema has shifted between Veyon 4.x
releases, so this importer is forgiving: it tries the documented
JSON paths first, and falls back to a deep walk that picks up
anything that looks like a Network Object Directory entry.

Authentication note
-------------------
Veyon uses asymmetric keypair auth (RSA). ClassControl uses
symmetric HMAC-SHA256 with a shared key. The two are not
interoperable, so importing a Veyon config gives you the *computer
list* but not the auth — you still need to deploy ClassControl's
``auth.key`` onto each student PC. See ``scripts/setup_windows_client.ps1``
for the one-command Windows path.

Where Veyon.json typically lives
--------------------------------
* Windows: ``C:\\ProgramData\\Veyon\\Veyon.json``
* macOS:   ``/Library/Application Support/Veyon/Veyon.json``
* Linux:   ``/etc/veyon/Veyon.json`` or
            ``~/.config/Veyon/Veyon.conf``

The Veyon Master also exports the Computer Directory as JSON via
``File → Export configuration…`` — that file works too.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.protocol import DEFAULT_PORT
from master.roster import Computer


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class VeyonImport:
    """Result of a parse — what we'd add if the user confirms."""

    source_path: str
    computers: list[Computer]
    rooms: list[str]                    # distinct group/room names seen
    skipped: int = 0                    # entries we couldn't parse
    raw_object_count: int = 0           # how many objects of any kind we saw


def parse_veyon_config(path: str | os.PathLike) -> VeyonImport:
    """Parse ``path`` as a Veyon config and return the importable bits.

    Raises ``FileNotFoundError`` / ``ValueError`` on unreadable input.
    """
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    # Veyon .conf files on Linux are sometimes INI-ish; the JSON path is
    # by far the common one. If the file isn't valid JSON we error out
    # with a clear message rather than guessing at format.
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{p} doesn't look like a Veyon JSON config "
            f"(parse error at line {exc.lineno}, col {exc.colno}). "
            "If it's a .conf INI file, use Veyon Master → "
            "File → Export configuration… to produce a JSON copy first."
        ) from exc

    objects = _find_veyon_objects(data)
    raw_count = len(objects)

    # First pass: map every Location's UID to its display name. Veyon
    # nests Locations inside other Locations; we flatten by walking up
    # parents until we hit the root.
    location_names: dict[str, str] = {}
    parent_of: dict[str, str] = {}
    for obj in objects:
        if not _is_location(obj):
            continue
        uid = obj.get("Uid", "") or obj.get("uid", "")
        name = obj.get("Name", "") or obj.get("name", "")
        parent = obj.get("ParentUid", "") or obj.get("parentUid", "")
        if uid:
            location_names[uid] = name or "Unsorted"
            parent_of[uid] = parent

    def _full_path(uid: str) -> str:
        """Build a slash-separated path of location names, e.g.
        'Lab Block / Room 12'. Cycles return whatever we have so far."""
        seen: set[str] = set()
        parts: list[str] = []
        cur = uid
        while cur and cur not in seen and cur in location_names:
            seen.add(cur)
            parts.append(location_names[cur])
            cur = parent_of.get(cur, "")
        return " / ".join(reversed([p for p in parts if p]))

    # Second pass: every Computer becomes a Computer roster entry.
    computers: list[Computer] = []
    rooms: set[str] = set()
    skipped = 0
    now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    note_prefix = f"imported from Veyon ({now_iso})"

    for obj in objects:
        if not _is_computer(obj):
            continue
        host = (
            obj.get("HostAddress")
            or obj.get("hostAddress")
            or obj.get("Hostname")
            or obj.get("hostname")
            or ""
        ).strip()
        if not host:
            skipped += 1
            continue
        name = (
            obj.get("Name") or obj.get("name") or host
        ).strip() or host
        mac = (
            obj.get("MacAddress")
            or obj.get("macAddress")
            or obj.get("MAC")
            or ""
        ).strip()
        parent_uid = obj.get("ParentUid") or obj.get("parentUid") or ""
        room_path = _full_path(parent_uid) or "Imported from Veyon"
        rooms.add(room_path)

        computers.append(Computer(
            id=uuid.uuid4().hex,
            name=name,
            host=host,
            port=DEFAULT_PORT,
            group=room_path,
            mac=mac,
            notes=note_prefix,
        ))

    return VeyonImport(
        source_path=str(p),
        computers=computers,
        rooms=sorted(rooms),
        skipped=skipped,
        raw_object_count=raw_count,
    )


def default_veyon_paths() -> list[Path]:
    """Return the platform-standard paths Veyon usually lives at, in the
    order the importer's file picker should preselect them."""
    candidates: list[Path] = []
    if sys.platform.startswith("win"):
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        candidates += [
            Path(program_data) / "Veyon" / "Veyon.json",
            Path(program_data) / "Veyon" / "Veyon.conf",
        ]
    elif sys.platform == "darwin":
        candidates += [
            Path("/Library/Application Support/Veyon/Veyon.json"),
            Path.home() / "Library/Application Support/Veyon/Veyon.json",
        ]
    else:
        candidates += [
            Path("/etc/veyon/Veyon.json"),
            Path("/etc/veyon/Veyon.conf"),
            Path.home() / ".config/Veyon/Veyon.conf",
            Path.home() / ".config/Veyon/Veyon.json",
        ]
    return candidates


# ---------------------------------------------------------------------------
# Internals — Veyon-schema guesswork
# ---------------------------------------------------------------------------


# Veyon's "Type" field is an int in JSON exports (4 = Computer, 3 =
# Location), but Qt-config-style imports sometimes use the string name.
_COMPUTER_TYPE_INTS = {4}
_LOCATION_TYPE_INTS = {3}
_COMPUTER_TYPE_STRS = {"computer", "type_computer", "networkobject::computer"}
_LOCATION_TYPE_STRS = {"location", "room", "group", "type_location"}


def _is_computer(obj: dict) -> bool:
    t = obj.get("Type", obj.get("type"))
    if isinstance(t, int):
        return t in _COMPUTER_TYPE_INTS
    if isinstance(t, str):
        return t.lower() in _COMPUTER_TYPE_STRS
    # No explicit type but has a HostAddress field → treat as a computer.
    return "HostAddress" in obj or "hostAddress" in obj


def _is_location(obj: dict) -> bool:
    t = obj.get("Type", obj.get("type"))
    if isinstance(t, int):
        return t in _LOCATION_TYPE_INTS
    if isinstance(t, str):
        return t.lower() in _LOCATION_TYPE_STRS
    return False


def _find_veyon_objects(data) -> list[dict]:
    """Return the flat list of network objects.

    Veyon nests them differently across versions; we try the common
    JSON paths first, then walk the whole tree for anything that
    looks like an object dict with ``Uid`` and ``Type``.
    """
    if not isinstance(data, dict):
        return []

    # Common direct paths.
    candidates = [
        ("NetworkObjectDirectory", "DefaultDirectory", "Objects"),
        ("NetworkObjectDirectory", "Objects"),
        ("BuiltinDirectory", "NetworkObjects"),
        ("Objects",),
    ]
    for path in candidates:
        node = data
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, list) and node and isinstance(node[0], dict):
            return node

    # Fallback: deep walk. Anything that has a Uid AND ("HostAddress" or
    # "Name") is plausible.
    found: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            keys = node.keys()
            if ("Uid" in keys or "uid" in keys) and (
                "HostAddress" in keys
                or "hostAddress" in keys
                or "Hostname" in keys
                or "hostname" in keys
                or "Name" in keys
                or "name" in keys
            ):
                found.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found
