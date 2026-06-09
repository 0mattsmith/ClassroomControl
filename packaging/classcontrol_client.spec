# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the ClassControl student (client) daemon on Windows.
# Build with:  pyinstaller packaging/classcontrol_client.spec
# Output:      dist/ClassControlClient/ClassControlClient.exe
#
# The client uses `console=False` so it runs as a windowless background
# process; logs go to %APPDATA%\ClassControl\client\client.log.

from pathlib import Path
import sys

PROJECT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(PROJECT))

block_cipher = None


a = Analysis(
    [str(PROJECT / "client" / "daemon.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
        "shared", "client", "client.platform", "client.windows",
        "client.windows.screen", "client.windows.input_inject",
        "client.windows.internet", "client.windows.blocking",
        "client.windows.power", "client.windows.audio",
        "client.windows.launcher", "client.windows.info",
        "PIL", "PIL.Image",
        "cryptography",
        "mss", "psutil", "pycaw", "comtypes",
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
    name="ClassControlClient",
    debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
    icon=str(PROJECT / "assets" / "icon.ico") if (PROJECT / "assets" / "icon.ico").exists() else None,
    uac_admin=True,    # request elevation so firewall / hosts / shutdown work
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name="ClassControlClient",
)
