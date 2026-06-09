"""Captures the teacher's screen and broadcasts each frame to every
connected student. Uses the same macOS screen-capture helper as the
client. On platforms where capture isn't available, broadcasts nothing
and quietly stops."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer

from shared.protocol import Op

# Cross-platform capture: picks macOS or Windows backend automatically.
from master import screen_capture as mac_screen


class DemoBroadcaster(QObject):
    def __init__(self, hub, fps: int = 10, max_width: int = 0, quality: int = 88,
                 fmt: str = "JPEG", draw_cursor: bool = True):
        super().__init__()
        self.hub = hub
        self._timer = QTimer(self)
        self._timer.setInterval(max(50, int(1000 / max(1, fps))))
        self._timer.timeout.connect(self._tick)
        self._max_width = max_width    # 0 = native
        self._quality = quality
        self._fmt = fmt
        self._draw_cursor = draw_cursor
        self._active = False
        self._paused = False
        # Tracks whether the *last* start() call asked for windowed.
        # Surfaced so the toolbar can update its labels.
        self._windowed = False

    def start(self, windowed: bool = False) -> None:
        """Start broadcasting. If ``windowed=True``, students see the
        demo in a regular resizable window instead of a full-screen
        overlay (they can carry on with other work alongside)."""
        if self._active:
            # Already running — switch mode silently if the caller asked
            # for a different presentation.
            if windowed != self._windowed:
                self._windowed = windowed
                self.hub.broadcast(Op.DEMO_START, {"windowed": windowed})
            return
        self._windowed = windowed
        self.hub.broadcast(Op.DEMO_START, {"windowed": windowed})
        self._active = True
        self._paused = False
        self._timer.start()

    def stop(self) -> None:
        if not self._active:
            return
        self._timer.stop()
        self.hub.broadcast(Op.DEMO_STOP, {})
        self._active = False
        self._paused = False

    def pause(self) -> None:
        """Stop sending new frames; students keep seeing the last frame
        until ``resume()`` or ``stop()`` is called."""
        if not self._active or self._paused:
            return
        self._timer.stop()
        self._paused = True

    def resume(self) -> None:
        if not self._active or not self._paused:
            return
        self._timer.start()
        self._paused = False

    def is_active(self) -> bool:
        return self._active

    def is_paused(self) -> bool:
        return self._paused

    def is_windowed(self) -> bool:
        return self._windowed

    def _tick(self) -> None:
        blob = mac_screen.capture_screen_jpeg(
            max_width=self._max_width,
            quality=self._quality,
            fmt=self._fmt,
            draw_cursor=self._draw_cursor,
        )
        if not blob:
            return
        for cid in self.hub.computer_ids():
            self.hub.send(
                cid, Op.DEMO_FRAME,
                {"size": len(blob), "format": self._fmt},
                blob,
            )
