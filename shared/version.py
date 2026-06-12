"""Single source of truth for the app's version + update channel.

Bump :data:`VERSION` for every release. The updater compares this against
the ``version`` field of the manifest at :data:`UPDATE_MANIFEST_URL` and
offers an install when the manifest is newer.

The default URL is a placeholder — edit it for your deployment, or
override at runtime via the ``CLASSCONTROL_UPDATE_URL`` environment
variable (handy for testing against a staging manifest).
"""

from __future__ import annotations

import os

VERSION = "0.2.1"

# Where the updater fetches release info from.
#
# Default points at the GitHub Releases API, which the updater knows
# how to translate into our manifest shape natively (see
# ``shared.updater._from_github_release``).
#
# To use:
#   1. Create a GitHub repo and push this codebase to it.
#   2. Edit GITHUB_OWNER / GITHUB_REPO below (or override the URL via the
#      ``CLASSCONTROL_UPDATE_URL`` env var per-machine).
#   3. Publish releases with one .zip per platform — the updater
#      picks the right one by filename (see UPDATING.md).
#
# A static manifest.json hosted on GitHub Pages, S3, or any HTTPS URL
# is also supported — just point the env var at it. The updater
# auto-detects the response shape.
GITHUB_OWNER = "0mattsmith"
GITHUB_REPO  = "ClassroomControl"

UPDATE_MANIFEST_URL = os.environ.get(
    "CLASSCONTROL_UPDATE_URL",
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest",
)


def version_tuple(v: str) -> tuple[int, ...]:
    """Parse a dotted version like '0.2.0' into a tuple of ints.

    Non-numeric segments are stripped down to leading digits, so
    '0.2.0-rc1' compares as (0, 2, 0). Empty strings become (0,).
    """
    out: list[int] = []
    for part in (v or "0").split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out) if out else (0,)


def version_gt(a: str, b: str) -> bool:
    """True if version string ``a`` is newer than ``b``."""
    return version_tuple(a) > version_tuple(b)
