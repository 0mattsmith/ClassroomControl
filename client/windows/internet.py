"""Internet lockdown on Windows via the built-in firewall (``netsh``).

When the master enables lockdown we add three Windows Firewall rules
under the group ``ClassControl-Lockdown``:

* ``ClassControl-Allow-Master``    – allow ALL traffic to the master IP(s)
* ``ClassControl-Block-Out-TCP``   – block all other outbound TCP
* ``ClassControl-Block-Out-UDP``   – block all other outbound UDP

Removing the lockdown deletes every rule under that group.

The client process must be elevated (Administrator) for ``netsh
advfirewall`` calls to succeed; the README explains how to ensure that
via a scheduled task that runs as SYSTEM.
"""

from __future__ import annotations

import subprocess
from typing import Iterable

GROUP = "ClassControl-Lockdown"


def _netsh(*args: str) -> tuple[int, str]:
    p = subprocess.run(["netsh", *args], capture_output=True, text=True)
    return p.returncode, (p.stderr or p.stdout or "").strip()


def _delete_group() -> None:
    _netsh("advfirewall", "firewall", "delete", "rule", f"group={GROUP}")


def enable_lockdown(master_ips: Iterable[str]) -> dict:
    """Returns {"ok": bool, "reason": str}. Requires Administrator."""
    _delete_group()
    ips = [ip.strip() for ip in master_ips if ip and ip.strip()]
    if not ips:
        return {"ok": False, "reason": "no master IPs supplied"}

    # Allow master both directions
    rc, err = _netsh(
        "advfirewall", "firewall", "add", "rule",
        "name=ClassControl-Allow-Master",
        f"group={GROUP}",
        "dir=out", "action=allow",
        f"remoteip={','.join(ips)}",
        "enable=yes", "profile=any",
    )
    if rc != 0:
        return {"ok": False, "reason":
                err or "netsh failed — needs Administrator privileges"}
    _netsh(
        "advfirewall", "firewall", "add", "rule",
        "name=ClassControl-Allow-Master-In",
        f"group={GROUP}",
        "dir=in", "action=allow",
        f"remoteip={','.join(ips)}",
        "enable=yes", "profile=any",
    )

    # Block everything else outbound
    rc, err = _netsh(
        "advfirewall", "firewall", "add", "rule",
        "name=ClassControl-Block-Out-TCP",
        f"group={GROUP}",
        "dir=out", "action=block", "protocol=TCP",
        "enable=yes", "profile=any",
    )
    if rc != 0:
        return {"ok": False, "reason": err or "netsh failed"}
    rc, err = _netsh(
        "advfirewall", "firewall", "add", "rule",
        "name=ClassControl-Block-Out-UDP",
        f"group={GROUP}",
        "dir=out", "action=block", "protocol=UDP",
        "enable=yes", "profile=any",
    )
    if rc != 0:
        return {"ok": False, "reason": err or "netsh failed"}
    return {"ok": True, "reason": ""}


def disable_lockdown() -> dict:
    _delete_group()
    return {"ok": True, "reason": ""}


def is_active() -> bool:
    p = subprocess.run(
        ["netsh", "advfirewall", "firewall", "show", "rule",
         "name=ClassControl-Block-Out-TCP"],
        capture_output=True, text=True,
    )
    return p.returncode == 0 and "No rules match" not in p.stdout
