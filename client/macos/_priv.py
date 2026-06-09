"""
Privilege escalation helpers for the macOS client.

``run_privileged(argv, stdin=None)`` runs a command with root
privileges, trying the following strategies in order until one works:

  1. **Direct** — if we're already root (LaunchDaemon, ``sudo`` wrapper),
     just run the command.
  2. **``sudo -n``** — non-interactive sudo, succeeds when a NOPASSWD
     sudoers entry is installed (see ``packaging/classcontrol-sudoers``).
  3. **``osascript`` with "administrator privileges"** — pops the native
     macOS auth dialog asking the user to type their password. macOS
     caches that auth for ~5 minutes, so subsequent privileged commands
     don't re-prompt during a class.

Returns ``(returncode, stdout, stderr)`` exactly like ``subprocess.run``.

The osascript path requires a GUI session (which both ``sudo
./scripts/run_client.sh`` and the .app launch provide). LaunchDaemons
have no GUI session and so would already be root — strategy 1 handles
them.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from typing import Optional

ADMIN_PROMPT = "ClassControl needs to update system settings"
_PASSWORD_HINT = "password is required"


def run_privileged(
    argv: list[str],
    stdin: Optional[str] = None,
) -> tuple[int, str, str]:
    if os.geteuid() == 0:
        p = subprocess.run(
            argv,
            input=stdin if stdin is not None else None,
            capture_output=True, text=True,
        )
        return p.returncode, p.stdout, p.stderr

    # Try sudo -n (silent NOPASSWD path)
    p = subprocess.run(
        ["sudo", "-n", *argv],
        input=stdin if stdin is not None else None,
        capture_output=True, text=True,
    )
    if p.returncode == 0:
        return p.returncode, p.stdout, p.stderr
    # If sudo failed for some reason OTHER than "needs password",
    # surface that error directly.
    if _PASSWORD_HINT not in (p.stderr or ""):
        return p.returncode, p.stdout, p.stderr

    # Fall back to the GUI prompt.
    return _run_via_osascript(argv, stdin)


def run_privileged_batch(
    commands: list[list[str]],
) -> tuple[int, str, str]:
    """Run several commands with root privileges, asking only ONCE.

    Three deployments handled:

      * **Already root** (LaunchDaemon, ``sudo`` wrapper): runs each
        command directly, no prompts ever.
      * **NOPASSWD sudoers entry installed**: runs each via ``sudo -n``,
        no prompts.
      * **Plain user, nothing installed**: combines every command into a
        single ``cmd1 && cmd2 && cmd3`` shell pipeline and runs the
        whole thing via one osascript admin call — one password prompt
        for the whole operation.

    Returns ``(returncode, stdout, stderr)``. Stops at the first failing
    command in modes 1 and 2; the osascript path is all-or-nothing.
    """
    if not commands:
        return 0, "", ""

    # --- Mode 1: already root ---------------------------------------
    if os.geteuid() == 0:
        for cmd in commands:
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode != 0:
                return p.returncode, p.stdout, p.stderr
        return 0, "", ""

    # --- Mode 2: try sudo -n on the first command -------------------
    first = commands[0]
    p = subprocess.run(
        ["sudo", "-n", *first], capture_output=True, text=True,
    )
    if p.returncode == 0:
        # Sudoers is configured — run the rest the same way.
        for cmd in commands[1:]:
            p = subprocess.run(
                ["sudo", "-n", *cmd], capture_output=True, text=True,
            )
            if p.returncode != 0:
                return p.returncode, p.stdout, p.stderr
        return 0, "", ""

    if _PASSWORD_HINT not in (p.stderr or ""):
        # sudo refused for a non-password reason (e.g. command not in
        # the sudoers whitelist). Surface that directly.
        return p.returncode, p.stdout, p.stderr

    # --- Mode 3: one osascript call for the whole pipeline ----------
    pipeline = " && ".join(
        " ".join(shlex.quote(a) for a in cmd) for cmd in commands
    )
    return _osascript_admin(pipeline)


# ---------------------------------------------------------------------------
# osascript path
# ---------------------------------------------------------------------------


def _run_via_osascript(
    argv: list[str],
    stdin: Optional[str],
) -> tuple[int, str, str]:
    """Run argv elevated via ``do shell script … with administrator privileges``.

    If stdin is provided, it's spilled to a temp file first and piped in.
    """
    if stdin is not None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".classcontrol", delete=False,
        ) as fh:
            fh.write(stdin)
            stdin_path = fh.name
        try:
            piped = (
                f"cat {shlex.quote(stdin_path)} | "
                + " ".join(shlex.quote(a) for a in argv)
            )
            return _osascript_admin(piped)
        finally:
            try:
                os.unlink(stdin_path)
            except OSError:
                pass

    cmd_str = " ".join(shlex.quote(a) for a in argv)
    return _osascript_admin(cmd_str)


def _osascript_admin(shell_cmd: str) -> tuple[int, str, str]:
    # Escape for AppleScript string literal: backslashes first, then quotes.
    escaped = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
    applescript = (
        f'do shell script "{escaped}" '
        f'with prompt "{ADMIN_PROMPT}" '
        f'with administrator privileges'
    )
    p = subprocess.run(
        ["osascript", "-e", applescript],
        capture_output=True, text=True,
    )
    return p.returncode, p.stdout, p.stderr
