"""
macOS-specific tweaks for the running Qt app.

When you run ``python -m master.app`` (or any Python GUI script) on
macOS, the menu bar at the top of the screen reads "Python" because
that's the process the OS sees. Inside a real ``.app`` bundle the
``CFBundleName`` key in ``Info.plist`` overrides that — but when
running from source there is no bundle, so we have to mutate the
in-memory bundle info dict before Qt asks the OS for the name.

Call :func:`set_app_name` *before* constructing the ``QApplication``.

On non-macOS platforms this is a no-op.
"""

from __future__ import annotations

import sys


def set_app_name(name: str) -> None:
    """Override the name shown in the macOS menu bar at the top.

    Call this BEFORE creating QApplication, otherwise the menu bar is
    already cached and won't update.
    """
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle           # type: ignore[import-not-found]
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = name
            info["CFBundleDisplayName"] = name
            # Without an explicit executable name, macOS sometimes still
            # falls back to "Python" in the App menu's "About Python" /
            # "Quit Python" items. Setting CFBundleExecutable too covers it.
            info["CFBundleExecutable"] = name
    except Exception:
        # pyobjc not present on this machine (e.g. Linux dev box) — no-op.
        pass
