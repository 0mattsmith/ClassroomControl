"""py2app build script for the ClassControl teacher app.

Build with:
    cd <project-root>
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python packaging/setup_master.py py2app
The resulting .app appears in dist/ClassControl Teacher.app
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from setuptools import setup  # noqa: E402


APP = [str(PROJECT_ROOT / "master" / "app.py")]
DATA_FILES = []

OPTIONS = {
    "argv_emulation": False,
    "iconfile": str(PROJECT_ROOT / "assets" / "icon.icns") if (PROJECT_ROOT / "assets" / "icon.icns").exists() else None,
    "packages": ["PyQt6", "shared", "master", "client"],  # pulls in macOS modules too
    "includes": [
        "Quartz", "AppKit", "Foundation",
        "PIL", "PIL.Image",
        "cryptography",
    ],
    "plist": {
        "CFBundleName": "ClassControl Teacher",
        "CFBundleDisplayName": "ClassControl Teacher",
        "CFBundleIdentifier": "io.classcontrol.teacher",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSApplicationCategoryType": "public.app-category.education",
        "NSHighResolutionCapable": True,
        # macOS permission strings shown in the system consent prompts.
        "NSScreenCaptureUsageDescription":
            "ClassControl Teacher captures your screen so it can broadcast demos to students.",
        "NSAppleEventsUsageDescription":
            "ClassControl Teacher uses AppleScript to control system events.",
    },
}

# Strip the iconfile option if no icon exists - py2app errors otherwise
if OPTIONS["iconfile"] is None:
    OPTIONS.pop("iconfile")


setup(
    name="ClassControl Teacher",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
