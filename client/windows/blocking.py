"""Application and URL blocking on Windows.

Apps
----
We maintain an in-memory set of process identifiers (executable name,
e.g. ``chrome.exe``, or partial window title). A background asyncio task
polls the running process list once per second and terminates anything
matching.

URLs
----
We append managed entries to ``C:\\Windows\\System32\\drivers\\etc\\hosts``
between BEGIN/END sentinel lines so we can cleanly remove them later. The
DNS resolver cache is flushed with ``ipconfig /flushdns`` after every
update.

The process needs Administrator privileges to write the hosts file.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Iterable

from shared.text import normalize_hostname


LOG = logging.getLogger("classcontrol.client.blocking")

try:
    import psutil
    _HAVE_PSUTIL = True
except Exception as _psutil_err:  # pragma: no cover
    psutil = None
    _HAVE_PSUTIL = False
    LOG.warning(
        "psutil not available; app blocking and the running-apps "
        "listing will not work: %s", _psutil_err,
    )


HOSTS_FILE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
HOSTS_BEGIN = "# >>> classcontrol-block >>>"
HOSTS_END = "# <<< classcontrol-block <<<"

_blocked_apps: set[str] = set()
_app_task: asyncio.Task | None = None


def set_blocked_apps(identifiers: Iterable[str]) -> None:
    """Replace the blocked app set. Identifiers are matched against
    executable name (case-insensitive, ``.exe`` optional)."""
    global _blocked_apps
    norm = set()
    for s in identifiers:
        s = (s or "").strip().lower()
        if not s:
            continue
        norm.add(s if s.endswith(".exe") else f"{s}.exe")
        norm.add(s.removesuffix(".exe"))
    LOG.info(
        "set_blocked_apps: %d entries -> %s",
        len(norm), sorted(norm) if norm else "(empty)",
    )
    _blocked_apps = norm


def blocked_apps() -> set[str]:
    return set(_blocked_apps)


def _matches_blocked(name: str) -> bool:
    """Case-insensitive match against blocklist. Both ``chrome`` and
    ``chrome.exe`` forms are accepted."""
    n = (name or "").lower()
    n_noext = n.removesuffix(".exe") if n.endswith(".exe") else n
    for entry in _blocked_apps:
        e = (entry or "").lower()
        if e == n or e == n_noext:
            return True
        # Short bare names match by substring (so "chrome" matches "chrome.exe")
        if "." not in e and (e in n or e in n_noext):
            return True
    return False


def _kill_blocked_now() -> int:
    if not (_HAVE_PSUTIL and _blocked_apps):
        return 0
    killed = 0
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info.get("name") or ""
            if _matches_blocked(name):
                proc.terminate()  # Windows uses TerminateProcess — can't be trapped
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


def list_running_apps() -> list[dict]:
    """Snapshot of running processes that look like user-facing apps.

    Returns dicts with the same keys as the macOS backend so the master
    UI can be platform-agnostic.
    """
    if not _HAVE_PSUTIL:
        return []
    out: list[dict] = []
    for proc in psutil.process_iter(["pid", "name", "username"]):
        try:
            info = proc.info
            name = info.get("name") or ""
            # Drop kernel / svchost / system services that the user can't
            # usefully act on. Heuristic: keep .exe with a non-system user.
            user = (info.get("username") or "").lower()
            if not name:
                continue
            if user.startswith("nt authority"):
                # Skip SYSTEM, LOCAL SERVICE, NETWORK SERVICE
                continue
            out.append({
                "pid": int(info.get("pid", 0)),
                "bundle_id": "",      # Windows has no bundle id; mirror the schema
                "exe":  name,
                "name": name,
                "active": False,
                "hidden": False,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    out.sort(key=lambda a: a["name"].lower())
    return out


def kill_app(pid: int = 0, bundle_id: str = "", force: bool = True) -> dict:
    """Terminate a specific process. ``bundle_id`` is unused on Windows
    but accepted for API symmetry — pass ``pid`` instead.
    Returns ``{"ok": bool, "reason": str}``."""
    if not _HAVE_PSUTIL:
        return {"ok": False, "reason": "psutil not available"}
    if not pid:
        return {"ok": False, "reason": "pid required on Windows"}
    try:
        proc = psutil.Process(int(pid))
        if force:
            proc.kill()  # TerminateProcess
        else:
            proc.terminate()
        return {"ok": True, "reason": ""}
    except psutil.NoSuchProcess:
        return {"ok": False, "reason": "process not found"}
    except psutil.AccessDenied as exc:
        return {"ok": False, "reason": f"access denied: {exc}"}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


async def _watchdog():
    LOG.info(
        "blocked-app watchdog started (psutil=%s, initial entries=%d)",
        _HAVE_PSUTIL, len(_blocked_apps),
    )
    tick = 0
    while True:
        try:
            killed = _kill_blocked_now()
            if killed:
                LOG.info(
                    "watchdog: terminated %d process(es) matching block list %s",
                    killed, sorted(_blocked_apps),
                )
            tick += 1
            if tick % 60 == 0:
                LOG.info(
                    "watchdog heartbeat: %d entries blocked, psutil=%s",
                    len(_blocked_apps), _HAVE_PSUTIL,
                )
        except Exception:
            LOG.exception("watchdog tick crashed")
        await asyncio.sleep(1.0)


def start_watchdog(loop: asyncio.AbstractEventLoop | None = None) -> None:
    global _app_task
    if _app_task and not _app_task.done():
        return
    loop = loop or asyncio.get_event_loop()
    _app_task = loop.create_task(_watchdog())
    LOG.info("app-block watchdog scheduled on loop %r", id(loop))


# normalize_hostname now lives in shared/text.py — re-exported above.


def set_blocked_urls(raw_hostnames: Iterable[str]) -> dict:
    """Returns {"ok": bool, "reason": str, "applied": list[str]}."""
    try:
        current = HOSTS_FILE.read_text()
    except Exception:
        current = ""

    lines = current.splitlines()
    cleaned, skipping = [], False
    for ln in lines:
        if ln.strip() == HOSTS_BEGIN:
            skipping = True
            continue
        if ln.strip() == HOSTS_END:
            skipping = False
            continue
        if not skipping:
            cleaned.append(ln)

    seen: set[str] = set()
    hostnames: list[str] = []
    for raw in (raw_hostnames or []):
        h = normalize_hostname(raw)
        if h and h not in seen:
            seen.add(h)
            hostnames.append(h)

    if hostnames:
        cleaned.append(HOSTS_BEGIN)
        for h in hostnames:
            cleaned.append(f"127.0.0.1 {h}")
            if not h.startswith("www."):
                cleaned.append(f"127.0.0.1 www.{h}")
        cleaned.append(HOSTS_END)

    new_contents = "\r\n".join(cleaned).rstrip("\r\n") + "\r\n"
    try:
        HOSTS_FILE.write_text(new_contents)
    except PermissionError:
        return {"ok": False,
                "reason": "cannot write hosts file — needs Administrator",
                "applied": hostnames}
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True)
    return {"ok": True, "reason": "", "applied": hostnames}
