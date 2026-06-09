"""Application and URL blocking on macOS.

Application blocking
--------------------
We keep an in-memory set of bundle identifiers (or short app names) to
block. A background asyncio task polls the running process list via
NSWorkspace every second and force-quits any matching apps.

URL blocking
------------
We append managed lines to /etc/hosts, routing the blocked hostnames to
127.0.0.1. Lines are wrapped in BEGIN/END markers so we can cleanly
remove them later.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from shared.text import normalize_hostname
from client.macos._priv import run_privileged, run_privileged_batch


LOG = logging.getLogger("classcontrol.client.blocking")

try:
    from AppKit import NSWorkspace
    _HAVE_APPKIT = True
except Exception as _appkit_err:  # pragma: no cover
    NSWorkspace = None
    _HAVE_APPKIT = False
    LOG.warning(
        "AppKit (pyobjc-framework-Cocoa) not available; app blocking "
        "and the running-apps listing will not work: %s", _appkit_err,
    )


HOSTS_FILE = Path("/etc/hosts")
HOSTS_BEGIN = "# >>> classcontrol-block >>>"
HOSTS_END = "# <<< classcontrol-block <<<"


# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------


_blocked_apps: set[str] = set()
_app_task: asyncio.Task | None = None


def set_blocked_apps(identifiers: Iterable[str]) -> None:
    """Replace the set of blocked app identifiers (bundle ID or app name)."""
    global _blocked_apps
    new = {s.lower() for s in identifiers if s}
    LOG.info(
        "set_blocked_apps: %d entries -> %s",
        len(new), sorted(new) if new else "(empty)",
    )
    _blocked_apps = new


def blocked_apps() -> set[str]:
    return set(_blocked_apps)


def _running_ns_apps():
    """Return the raw NSRunningApplication objects (so we can call
    forceTerminate on them directly — much more reliable than os.kill,
    which most GUI apps trap and ignore)."""
    if not _HAVE_APPKIT:
        return []
    return list(NSWorkspace.sharedWorkspace().runningApplications())


def list_running_apps() -> list[dict]:
    """Snapshot of every GUI app currently running on this Mac.

    Returns a list of dicts (sortable, JSON-friendly) with keys::

        pid        - int, the process identifier
        bundle_id  - str, e.g. "com.apple.Safari" (may be "")
        name       - str, localized display name
        active     - bool, is it the frontmost app
        hidden     - bool, hidden via Cmd-H
    """
    out: list[dict] = []
    for app in _running_ns_apps():
        try:
            out.append({
                "pid": int(app.processIdentifier()),
                "bundle_id": app.bundleIdentifier() or "",
                "name": app.localizedName() or "",
                "active": bool(app.isActive()),
                "hidden": bool(app.isHidden()),
            })
        except Exception:
            continue
    # Active first, then alphabetical by display name.
    out.sort(key=lambda a: (not a["active"], a["name"].lower()))
    return out


def kill_app(pid: int = 0, bundle_id: str = "", force: bool = True) -> dict:
    """Terminate a specific app by pid or bundle id.

    ``force=True`` (default) calls ``NSRunningApplication.forceTerminate``,
    which is the same thing as the Force Quit menu entry — it sends a
    signal the app can't trap. Pass ``force=False`` to ask politely
    first (some apps may prompt the user to confirm).

    Returns ``{"ok": bool, "reason": str}``.
    """
    if not _HAVE_APPKIT:
        return {"ok": False, "reason": "AppKit not available on this client"}
    target = None
    bundle_id_low = (bundle_id or "").lower()
    for app in _running_ns_apps():
        if pid and int(app.processIdentifier()) == pid:
            target = app
            break
        if bundle_id_low and (app.bundleIdentifier() or "").lower() == bundle_id_low:
            target = app
            break
    if target is None:
        return {"ok": False, "reason": "app not found"}
    try:
        if force:
            ok = bool(target.forceTerminate())
        else:
            ok = bool(target.terminate())
            if not ok:
                ok = bool(target.forceTerminate())
        if ok:
            return {"ok": True, "reason": ""}
        # Last resort: SIGKILL the pid.
        try:
            os.kill(int(target.processIdentifier()), signal.SIGKILL)
            return {"ok": True, "reason": "fell back to SIGKILL"}
        except Exception as exc:
            return {"ok": False, "reason": f"kill failed: {exc}"}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def _matches_blocked(name: str, bundle_id: str) -> bool:
    """True if this app matches any entry in the blocklist.

    Match rules (case-insensitive):
      * exact match on bundle ID (e.g. "com.apple.safari")
      * exact match on the localized name (e.g. "vivaldi")
      * SHORT entries with no dot can also be substring-matched against
        either bundle ID or name (so "safari" matches "com.apple.safari").
    """
    name_low = (name or "").lower()
    bid_low = (bundle_id or "").lower()
    for entry in _blocked_apps:
        if not entry:
            continue
        if entry == bid_low or entry == name_low:
            return True
        if "." not in entry and (entry in bid_low or entry in name_low):
            return True
    return False


def _kill_blocked_now() -> int:
    """Force-quit every running app that matches the blocklist."""
    if not _blocked_apps:
        return 0
    killed = 0
    for app in _running_ns_apps():
        try:
            bid = app.bundleIdentifier() or ""
            name = app.localizedName() or ""
        except Exception:
            continue
        if not _matches_blocked(name, bid):
            continue
        try:
            # Force-quit, equivalent to Force Quit menu — SIGTERM that
            # apps can trap is not reliable here.
            ok = bool(app.forceTerminate())
            if not ok:
                # Last resort: SIGKILL by pid.
                try:
                    os.kill(int(app.processIdentifier()), signal.SIGKILL)
                except Exception:
                    pass
            killed += 1
        except Exception:
            pass
    return killed


async def _watchdog():
    LOG.info(
        "blocked-app watchdog started (AppKit=%s, initial entries=%d)",
        _HAVE_APPKIT, len(_blocked_apps),
    )
    tick = 0
    while True:
        try:
            killed = _kill_blocked_now()
            if killed:
                LOG.info(
                    "watchdog: force-killed %d app(s) matching block list %s",
                    killed, sorted(_blocked_apps),
                )
            tick += 1
            # heartbeat every minute so the operator can confirm it's alive
            if tick % 60 == 0:
                LOG.info(
                    "watchdog heartbeat: %d entries blocked, AppKit=%s",
                    len(_blocked_apps), _HAVE_APPKIT,
                )
        except Exception:
            LOG.exception("watchdog tick crashed")
        await asyncio.sleep(1.0)


def start_watchdog(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Idempotently start the background app-block watchdog."""
    global _app_task
    if _app_task and not _app_task.done():
        return
    loop = loop or asyncio.get_event_loop()
    _app_task = loop.create_task(_watchdog())
    LOG.info("app-block watchdog scheduled on loop %r", id(loop))


# ---------------------------------------------------------------------------
# URLs (hosts file)
# ---------------------------------------------------------------------------


# normalize_hostname lives in shared/text.py — re-exported above.


def _apply_hosts(new_contents: str) -> tuple[bool, str]:
    """Replace /etc/hosts and refresh DNS caches as a single privileged
    batch — so the user sees ONE password prompt for the whole operation
    instead of three."""
    # If we can write the file directly (root or sudoers tee), just do it
    # — saves the temp-file dance.
    try:
        HOSTS_FILE.write_text(new_contents)
        # DNS flush is best-effort and doesn't matter much for the result
        run_privileged_batch([
            ["dscacheutil", "-flushcache"],
            ["killall", "-HUP", "mDNSResponder"],
        ])
        return True, ""
    except PermissionError:
        pass

    # Spill the desired contents to a temp file (no privileges needed)
    # then atomically install + flush DNS via ONE elevated batch.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".hosts.classcontrol", delete=False,
    ) as fh:
        fh.write(new_contents)
        tmp_path = fh.name
    try:
        rc, _, err = run_privileged_batch([
            ["cp", tmp_path, str(HOSTS_FILE)],
            ["dscacheutil", "-flushcache"],
            ["killall", "-HUP", "mDNSResponder"],
        ])
        if rc == 0:
            return True, ""
        return False, (err or "permission denied writing /etc/hosts").strip()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def set_blocked_urls(raw_hostnames: Iterable[str]) -> dict:
    """Replace the managed block list in /etc/hosts atomically.

    Returns ``{"ok": bool, "reason": str, "applied": list[str]}``.
    "applied" is the cleaned set of hostnames that actually got written.
    """
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

    # Normalize every entry the teacher gave us. Drop dupes + empties.
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

    new_contents = "\n".join(cleaned).rstrip("\n") + "\n"
    # ONE prompt for the whole operation: write hosts + flush DNS + restart resolver.
    ok, reason = _apply_hosts(new_contents)
    if not ok:
        return {"ok": False, "reason": reason, "applied": hostnames}
    return {"ok": True, "reason": "", "applied": hostnames}
