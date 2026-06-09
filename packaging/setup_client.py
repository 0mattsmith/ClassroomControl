"""py2app build script for the ClassControl client/student daemon.

Build with:
    cd <project-root>
    source .venv/bin/activate
    python packaging/setup_client.py py2app
The resulting .app appears in dist/ClassControl Client.app
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from setuptools import setup  # noqa: E402


APP = [str(PROJECT_ROOT / "client" / "daemon.py")]
DATA_FILES = []

OPTIONS = {
    "argv_emulation": False,
    "iconfile": str(PROJECT_ROOT / "assets" / "icon.icns") if (PROJECT_ROOT / "assets" / "icon.icns").exists() else None,
    "packages": ["PyQt6", "shared", "client"],
    "includes": [
        "Quartz", "AppKit", "Foundation",
        "PIL", "PIL.Image",
        "cryptography",
    ],
    "plist": {
        "CFBundleName": "ClassControl Client",
        "CFBundleDisplayName": "ClassControl Client",
        "CFBundleIdentifier": "io.classcontrol.client",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        # Background-only agent: no Dock icon, no menu bar UI by default.
        "LSUIElement": True,
        "LSBackgroundOnly": False,
        "NSHighResolutionCapable": True,
        "NSScreenCaptureUsageDescription":
            "ClassControl Client lets the teacher monitor this Mac and broadcast demos.",
        "NSAccessibilityUsageDescription":
            "ClassControl Client lets the teacher remotely control input on this Mac.",
        "NSAppleEventsUsageDescription":
            "ClassControl Client uses AppleScript for power and audio commands.",
    },
}

if OPTIONS["iconfile"] is None:
    OPTIONS.pop("iconfile")

setup(
    name="ClassControl Client",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
