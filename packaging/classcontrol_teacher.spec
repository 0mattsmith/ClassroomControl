# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the ClassControl teacher (master) app on Windows.
# Build with:  pyinstaller packaging/classcontrol_teacher.spec
# Output:      dist/ClassControlTeacher/ClassControlTeacher.exe

from pathlib import Path
import sys

PROJECT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(PROJECT))

block_cipher = None


a = Analysis(
    [str(PROJECT / "master" / "app.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
        "shared", "master", "client",
        "client.platform", "client.windows.screen",
        "PIL", "PIL.Image",
        "cryptography",
        "mss",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["client.macos"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ClassControlTeacher",
    debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
    icon=str(PROJECT / "assets" / "icon.ico") if (PROJECT / "assets" / "icon.ico").exists() else None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name="ClassControlTeacher",
)
