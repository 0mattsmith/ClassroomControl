"""Per-OS backend dispatcher.

Importing ``client.platform`` gives the daemon a stable API surface of
eight submodules (``screen``, ``input_inject``, ``internet``, ``blocking``,
``power``, ``audio``, ``launcher``, ``info``) regardless of whether the
underlying implementation is macOS, Windows, or the no-op Linux fallback.

Usage from the daemon::

    from client import platform as p

    jpeg = p.screen.capture_screen_jpeg()
    p.input_inject.inject_mouse("click", 0.5, 0.5)
    p.power.shutdown()
"""

from __future__ import annotations

import sys

if sys.platform.startswith("win"):
    from client.windows import (  # noqa: F401
        screen, input_inject, internet, blocking, power, audio, launcher, info,
    )
    NAME = "windows"
elif sys.platform == "darwin":
    from client.macos import (  # noqa: F401
        screen, input_inject, internet, blocking, power, audio, launcher, info,
    )
    NAME = "macos"
else:
    # Linux / unknown: the macOS modules wrap their dependencies in
    # try/except and silently no-op, which is good enough for dev work.
    from client.macos import (  # noqa: F401
        screen, input_inject, internet, blocking, power, audio, launcher, info,
    )
    NAME = sys.platform
