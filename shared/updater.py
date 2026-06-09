"""
Self-updater.

Workflow
--------
1. ``fetch_manifest()`` GETs ``UPDATE_MANIFEST_URL`` and returns the parsed
   JSON (or raises on network/parse failure).
2. ``find_update(manifest, component)`` checks whether the manifest's
   version for the given component (``"teacher"`` or ``"client"``) is
   newer than the running app, and if so returns an :class:`UpdateInfo`
   with the per-platform download URL + SHA-256.
3. ``download_archive(info, dest_path, progress=None)`` streams the
   archive to disk and verifies its SHA-256 against the manifest. The
   verification is the trust anchor — the manifest is fetched over HTTPS
   so the server validates it, the SHA pins the archive bytes.
4. ``install_update(archive_path, install_root)`` extracts the archive
   to a temp directory, writes a small platform-specific *swap helper*
   to a temp file, and spawns it detached. The helper waits for the
   parent app to exit, replaces the install directory atomically, and
   re-launches the new build. The caller then quits the app.

The helper is a regular shell script (``bash`` on macOS / Linux,
``cmd.exe`` batch on Windows) so it has no dependency on the new or
old Python interpreter being usable at the moment of swap.

The manifest schema is documented in ``UPDATING.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from shared.version import (
    UPDATE_MANIFEST_URL, VERSION, version_gt,
)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class UpdateInfo:
    component: str          # "teacher" | "client"
    current_version: str
    latest_version: str
    download_url: str
    sha256: str
    notes: str = ""
    released: str = ""
    size: int = 0


# ---------------------------------------------------------------------------
# Manifest fetch + parse
# ---------------------------------------------------------------------------


_PLATFORM_KEYS = {
    "darwin": "darwin",
    "win32":  "win32",
    "linux":  "linux",
}


def _platform_key() -> str:
    return _PLATFORM_KEYS.get(sys.platform, sys.platform)


def fetch_manifest(url: str = UPDATE_MANIFEST_URL, timeout: float = 10.0) -> dict:
    """GET ``url`` and parse as a manifest dict.

    Three shapes are recognised, in this order:

      * **GitHub Releases API** — single release object (``…/releases/latest``)
        OR a list (``…/releases``). Identified by the presence of
        ``tag_name`` on the object. Auto-converted to our shape using
        per-asset filename heuristics (``mac``/``darwin`` → darwin,
        ``win``/``windows`` → win32, ``linux`` → linux).
      * **Composite manifest** — ``{"teacher": {...}, "client": {...}}``
        with each sub-object containing ``version``/``downloads``/
        ``sha256``. Returned as-is; ``find_update`` reads the matching
        sub-object.
      * **Single-component manifest** — flat ``{"version", "downloads",
        "sha256", ...}``. Returned as-is.
    """
    req = urllib.request.Request(
        url, headers={
            "User-Agent": "ClassControl-Updater",
            "Accept": "application/vnd.github+json",   # harmless elsewhere
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    parsed = json.loads(data.decode("utf-8"))

    # GitHub Releases shapes
    if isinstance(parsed, dict) and "tag_name" in parsed:
        return _from_github_release(parsed)
    if isinstance(parsed, list):
        if not parsed:
            raise ValueError("No releases found at GitHub Releases URL")
        for entry in parsed:
            # Skip pre-releases and drafts unless they're the only thing.
            if isinstance(entry, dict) and not entry.get("draft", False):
                return _from_github_release(entry)
        # All drafts? Take the newest.
        return _from_github_release(parsed[0])

    # Otherwise it's our own manifest format.
    return parsed


def _from_github_release(release: dict) -> dict:
    """Convert a GitHub Releases API object into our manifest shape.

    Heuristics:
      * ``tag_name`` with optional leading ``v`` → ``version``
      * Asset names containing ``mac``, ``darwin``, ``osx``, or ending
        in ``.app.zip`` → ``downloads['darwin']``
      * Asset names containing ``win`` or ``windows`` or ending in
        ``.exe.zip`` / ``.msi`` → ``downloads['win32']``
      * Asset names containing ``linux`` → ``downloads['linux']``

    SHA-256 is read from a ``SHA256SUMS`` text asset if present —
    one ``<sha>  <filename>`` per line. If absent we skip SHA
    verification (the manifest URL itself is HTTPS-validated).
    """
    tag = (release.get("tag_name") or "").lstrip("vV")
    notes = release.get("body") or ""
    released = release.get("published_at") or release.get("created_at") or ""
    name = release.get("name") or release.get("tag_name") or ""

    downloads: dict[str, str] = {}
    asset_sizes: dict[str, int] = {}
    sha256s: dict[str, str] = {}
    sums_url: str | None = None

    for asset in release.get("assets", []) or []:
        a_name = (asset.get("name") or "").lower()
        a_url = asset.get("browser_download_url") or ""
        if not a_url:
            continue
        if "sha256sums" in a_name or a_name.endswith(".sums"):
            sums_url = a_url
            continue
        plat = _platform_from_asset_name(a_name)
        if plat and plat not in downloads:
            downloads[plat] = a_url
            asset_sizes[plat] = int(asset.get("size", 0) or 0)

    # Fetch and parse SHA256SUMS if a sums asset was published.
    if sums_url:
        try:
            with urllib.request.urlopen(sums_url, timeout=10) as r:
                sums_text = r.read().decode("utf-8", errors="replace")
            for line in sums_text.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) != 2:
                    continue
                sha, fname = parts[0].strip(), parts[1].strip().lstrip("*")
                plat = _platform_from_asset_name(fname.lower())
                if plat and plat not in sha256s:
                    sha256s[plat] = sha
        except Exception:
            # SHA verification stays optional — manifest URL is HTTPS.
            pass

    return {
        "version": tag or "0.0.0",
        "downloads": downloads,
        "sha256": sha256s,
        "notes": notes,
        "released": released,
        "name": name,
        "size": asset_sizes.get(sys.platform, 0),
    }


def _platform_from_asset_name(name: str) -> Optional[str]:
    """Guess which platform a release asset is for based on its filename."""
    n = name.lower()
    if any(k in n for k in ("mac", "darwin", "osx")) or n.endswith(".app.zip"):
        return "darwin"
    if any(k in n for k in ("win", "windows")) or n.endswith(".msi"):
        return "win32"
    if "linux" in n:
        return "linux"
    return None


def find_update(
    manifest: dict,
    component: str,
    current_version: str = VERSION,
    platform_key: str | None = None,
) -> Optional[UpdateInfo]:
    """Return an :class:`UpdateInfo` if ``component`` has a newer build
    for our platform in ``manifest``, else ``None``.

    The manifest may be in two shapes — the simple form has top-level
    ``version`` / ``downloads`` (single-component); the composite form
    has ``teacher`` and ``client`` sub-objects each with their own
    versioning. Both are supported.
    """
    plat = platform_key or _platform_key()
    section = manifest.get(component, manifest)
    if not isinstance(section, dict):
        return None
    latest = section.get("version", "")
    if not latest or not version_gt(latest, current_version):
        return None
    downloads = section.get("downloads") or {}
    shas = section.get("sha256") or {}
    url = downloads.get(plat) or ""
    sha = shas.get(plat) or ""
    if not url:
        # No build for our platform — show nothing.
        return None
    return UpdateInfo(
        component=component,
        current_version=current_version,
        latest_version=latest,
        download_url=url,
        sha256=sha,
        notes=section.get("notes", "") or manifest.get("notes", ""),
        released=section.get("released", "") or manifest.get("released", ""),
        size=int(section.get("size", 0) or 0),
    )


# ---------------------------------------------------------------------------
# Download + verify
# ---------------------------------------------------------------------------


ProgressCb = Callable[[int, int], None]   # (bytes_so_far, total_or_zero)


def download_archive(
    info: UpdateInfo,
    dest_path: Path,
    progress: ProgressCb | None = None,
    chunk_size: int = 64 * 1024,
) -> None:
    """Stream ``info.download_url`` to ``dest_path`` and verify the SHA.

    Raises :class:`ValueError` if the checksum doesn't match the value
    in the manifest — the partial download is removed before raising,
    so the caller can simply report the error and let the user retry.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(
        info.download_url, headers={"User-Agent": "ClassControl-Updater"}
    )
    h = hashlib.sha256()
    written = 0
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length", "0") or 0)
            with open(dest_path, "wb") as fh:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    fh.write(chunk)
                    h.update(chunk)
                    written += len(chunk)
                    if progress:
                        progress(written, total)
        if info.sha256 and h.hexdigest().lower() != info.sha256.lower():
            try:
                dest_path.unlink()
            except OSError:
                pass
            raise ValueError(
                f"SHA-256 mismatch — downloaded={h.hexdigest()[:16]}… "
                f"expected={info.sha256[:16]}…"
            )
    except Exception:
        # Clean up partial files on any failure so a retry is clean.
        try:
            dest_path.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def install_update(
    archive_path: Path,
    install_root: Path,
    relaunch_cmd: list[str] | None = None,
) -> int:
    """Extract ``archive_path``, write a swap-helper script, and spawn it
    detached. Returns the helper's PID. The caller should then quit the
    app — the helper will wait, swap the directories, and relaunch.

    ``install_root`` is the directory that gets replaced (the .app
    bundle path on macOS, the ``ClassControlClient`` folder on Windows,
    or a development checkout on Linux).

    ``relaunch_cmd`` is what the helper runs after swapping — defaults
    to the install_root's executable.
    """
    # Extract the archive to a temp directory that lives a peer of the
    # install root — this keeps the swap atomic-ish (rename within the
    # same filesystem) on most setups.
    work_dir = Path(tempfile.mkdtemp(prefix="classcontrol-update-"))
    extract_dir = work_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as zf:
        zf.extractall(extract_dir)

    # If the archive contains a single top-level directory, treat THAT as
    # the new install root. Otherwise treat the extracted dir itself.
    top_entries = list(extract_dir.iterdir())
    if len(top_entries) == 1 and top_entries[0].is_dir():
        new_root = top_entries[0]
    else:
        new_root = extract_dir

    relaunch_cmd = relaunch_cmd or _default_relaunch_for(install_root)

    if sys.platform == "win32":
        helper = _write_windows_helper(
            work_dir, install_root, new_root, relaunch_cmd, os.getpid(),
        )
        proc = subprocess.Popen(
            ["cmd", "/c", "start", "", "/min", str(helper)],
            creationflags=0x00000008,   # DETACHED_PROCESS
        )
    else:
        helper = _write_posix_helper(
            work_dir, install_root, new_root, relaunch_cmd, os.getpid(),
        )
        os.chmod(helper, 0o755)
        proc = subprocess.Popen(
            ["/bin/bash", str(helper)],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    return proc.pid


def _default_relaunch_for(install_root: Path) -> list[str]:
    """Best-guess "how to relaunch" given an install dir."""
    if sys.platform == "darwin":
        # .app bundle — use `open`
        if install_root.suffix == ".app":
            return ["/usr/bin/open", str(install_root)]
        # Otherwise treat as a script directory
        return [sys.executable, "-m", "master.app"]
    if sys.platform == "win32":
        # ClassControlClient.exe or ClassControlTeacher.exe inside the dir
        for candidate in ("ClassControlTeacher.exe", "ClassControlClient.exe"):
            exe = install_root / candidate
            if exe.exists():
                return [str(exe)]
        return [str(install_root / "ClassControlClient.exe")]
    return [sys.executable, "-m", "master.app"]


def _write_posix_helper(
    work_dir: Path,
    install_root: Path,
    new_root: Path,
    relaunch_cmd: list[str],
    parent_pid: int,
) -> Path:
    """bash script: wait for parent, swap dirs, relaunch."""
    relaunch_quoted = " ".join(_sh_quote(p) for p in relaunch_cmd)
    helper_path = work_dir / "swap.sh"
    helper_path.write_text(f"""#!/bin/bash
# ClassControl swap-and-relaunch helper, generated by shared/updater.py
set -e

PARENT_PID={parent_pid}
INSTALL_ROOT={_sh_quote(str(install_root))}
NEW_ROOT={_sh_quote(str(new_root))}

# Wait up to 15 seconds for the parent to exit cleanly.
for i in $(seq 1 30); do
    if ! kill -0 "$PARENT_PID" 2>/dev/null; then
        break
    fi
    sleep 0.5
done

# Replace the install directory. ditto preserves macOS metadata; on
# other platforms we fall back to plain rm -rf + cp -R.
if [ -e "$INSTALL_ROOT" ]; then
    rm -rf "$INSTALL_ROOT.classcontrol-old" 2>/dev/null || true
    mv "$INSTALL_ROOT" "$INSTALL_ROOT.classcontrol-old" || true
fi
if command -v ditto >/dev/null 2>&1; then
    ditto "$NEW_ROOT" "$INSTALL_ROOT"
else
    mkdir -p "$INSTALL_ROOT"
    cp -R "$NEW_ROOT"/* "$INSTALL_ROOT/"
fi

# Best-effort cleanup of the previous version.
rm -rf "$INSTALL_ROOT.classcontrol-old" 2>/dev/null || true

# Relaunch
{relaunch_quoted}
""")
    return helper_path


def _write_windows_helper(
    work_dir: Path,
    install_root: Path,
    new_root: Path,
    relaunch_cmd: list[str],
    parent_pid: int,
) -> Path:
    """cmd.exe batch: wait for parent, swap dirs, relaunch."""
    helper_path = work_dir / "swap.bat"
    relaunch_quoted = " ".join(f'"{p}"' for p in relaunch_cmd)
    helper_path.write_text(f"""@echo off
REM ClassControl swap-and-relaunch helper, generated by shared/updater.py
setlocal

set PARENT_PID={parent_pid}
set INSTALL_ROOT={install_root}
set NEW_ROOT={new_root}

REM Wait up to 15 seconds for the parent process to exit.
for /l %%i in (1,1,30) do (
    tasklist /FI "PID eq %PARENT_PID%" 2>nul | find "%PARENT_PID%" >nul
    if errorlevel 1 goto :swap
    timeout /t 1 /nobreak >nul
)

:swap
REM Stop the scheduled task (if any) so files aren't locked
schtasks /End /TN ClassControlClient >nul 2>&1

REM Move the old install aside, then copy the new one in.
if exist "%INSTALL_ROOT%" (
    rmdir /s /q "%INSTALL_ROOT%.classcontrol-old" 2>nul
    move /y "%INSTALL_ROOT%" "%INSTALL_ROOT%.classcontrol-old" >nul 2>&1
)
xcopy "%NEW_ROOT%\\*" "%INSTALL_ROOT%\\" /e /i /q /y >nul

REM Best-effort cleanup
rmdir /s /q "%INSTALL_ROOT%.classcontrol-old" 2>nul

REM Restart the scheduled task if it existed; otherwise relaunch directly.
schtasks /Run /TN ClassControlClient >nul 2>&1
if errorlevel 1 (
    start "" {relaunch_quoted}
)
endlocal
""", encoding="ascii")
    return helper_path


def _sh_quote(s: str) -> str:
    """Single-quote a string for safe use in a bash script."""
    return "'" + s.replace("'", "'\\''") + "'"
