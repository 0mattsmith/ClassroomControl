"""Remote-control window: shows live stream from one student and forwards
local mouse/keyboard input back over the wire as INPUT_EVENT frames."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QEvent, QPoint, QSize, pyqtSignal
from PyQt6.QtGui import (
    QImage, QPixmap, QMouseEvent, QKeyEvent, QWheelEvent, QPainter,
)
from PyQt6.QtWidgets import QMainWindow, QLabel, QToolBar, QCheckBox, QWidget

from shared.protocol import Op


class _StreamCanvas(QLabel):
    mouseEvent = pyqtSignal(str, float, float, str)
    wheelEvent_sig = pyqtSignal(int, int)
    keyEvent = pyqtSignal(str, bool, str, list)

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: #000; color: #aaa;")
        self.setText("Waiting for stream…")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._control = True
        self._last_pix_size = QSize(1, 1)

    def set_control(self, enabled: bool) -> None:
        self._control = enabled

    def set_frame(self, jpeg: bytes) -> None:
        # Format-agnostic decode: QImage auto-detects WEBP / JPEG / PNG
        # from the byte signature, so the master doesn't need to track
        # what format the daemon decided to send.
        img = QImage.fromData(jpeg)
        if img.isNull():
            return
        pix = QPixmap.fromImage(img).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._last_pix_size = pix.size()
        self.setPixmap(pix)
        self.setText("")

    def _to_norm(self, pos: QPoint) -> tuple[float, float]:
        # Convert widget coordinates to 0-1 relative to the rendered pixmap.
        w, h = self._last_pix_size.width(), self._last_pix_size.height()
        if w == 0 or h == 0:
            return 0.0, 0.0
        offset_x = (self.width() - w) / 2
        offset_y = (self.height() - h) / 2
        nx = (pos.x() - offset_x) / w
        ny = (pos.y() - offset_y) / h
        return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))

    @staticmethod
    def _btn(ev: QMouseEvent) -> str:
        b = ev.button()
        if b == Qt.MouseButton.RightButton:
            return "right"
        if b == Qt.MouseButton.MiddleButton:
            return "other"
        return "left"

    def mousePressEvent(self, ev: QMouseEvent):  # noqa: N802
        if self._control:
            nx, ny = self._to_norm(ev.position().toPoint())
            self.mouseEvent.emit("down", nx, ny, self._btn(ev))

    def mouseReleaseEvent(self, ev: QMouseEvent):  # noqa: N802
        if self._control:
            nx, ny = self._to_norm(ev.position().toPoint())
            self.mouseEvent.emit("up", nx, ny, self._btn(ev))

    def mouseMoveEvent(self, ev: QMouseEvent):  # noqa: N802
        if self._control:
            nx, ny = self._to_norm(ev.position().toPoint())
            kind = "drag" if ev.buttons() else "move"
            btn = "left"
            if ev.buttons() & Qt.MouseButton.RightButton:
                btn = "right"
            elif ev.buttons() & Qt.MouseButton.MiddleButton:
                btn = "other"
            self.mouseEvent.emit(kind, nx, ny, btn)

    def wheelEvent(self, ev: QWheelEvent):  # noqa: N802
        if self._control:
            d = ev.angleDelta()
            self.wheelEvent_sig.emit(d.y(), d.x())

    def keyPressEvent(self, ev: QKeyEvent):  # noqa: N802
        if self._control:
            self._emit_key(ev, True)

    def keyReleaseEvent(self, ev: QKeyEvent):  # noqa: N802
        if self._control:
            self._emit_key(ev, False)

    def _emit_key(self, ev: QKeyEvent, pressed: bool) -> None:
        mods = []
        m = ev.modifiers()
        if m & Qt.KeyboardModifier.ShiftModifier:
            mods.append("shift")
        if m & Qt.KeyboardModifier.ControlModifier:
            mods.append("ctrl")
        if m & Qt.KeyboardModifier.AltModifier:
            mods.append("alt")
        if m & Qt.KeyboardModifier.MetaModifier:
            mods.append("cmd")

        # Resolve key name
        special = {
            Qt.Key.Key_Return: "return", Qt.Key.Key_Enter: "return",
            Qt.Key.Key_Tab: "tab", Qt.Key.Key_Backspace: "backspace",
            Qt.Key.Key_Escape: "escape", Qt.Key.Key_Space: "space",
            Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right",
            Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down",
            Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end",
            Qt.Key.Key_PageUp: "pageup", Qt.Key.Key_PageDown: "pagedown",
        }
        name = special.get(ev.key(), "")
        text = ev.text() if not name else ""
        self.keyEvent.emit(name, pressed, text, mods)


class RemoteControlWindow(QMainWindow):
    def __init__(self, hub, computer_id: str, label: str):
        super().__init__()
        self.hub = hub
        self.computer_id = computer_id
        self.setWindowTitle(f"Remote control – {label}")
        self.resize(1100, 750)

        self.canvas = _StreamCanvas()
        self.setCentralWidget(self.canvas)

        tb = QToolBar("Controls")
        self.addToolBar(tb)
        self._control_check = QCheckBox("Take control")
        self._control_check.setChecked(True)
        self._control_check.toggled.connect(self.canvas.set_control)
        tb.addWidget(self._control_check)

        self.canvas.mouseEvent.connect(self._on_mouse)
        self.canvas.wheelEvent_sig.connect(self._on_wheel)
        self.canvas.keyEvent.connect(self._on_key)
        hub.signals.frameReceived.connect(self._on_frame)

        # Native-resolution JPEG@92 — visually sharp and works on every
        # Pillow / Qt build (WebP needs both ends to have plugins, which
        # not all builds do). Tune via Preferences if needed.
        hub.send(
            self.computer_id, Op.START_STREAM,
            {"fps": 20, "max_width": 0, "quality": 92, "format": "JPEG"},
        )

    # --------------------------- frame intake ---------------------------

    def _on_frame(self, cid: str, jpeg: bytes) -> None:
        if cid != self.computer_id:
            return
        self.canvas.set_frame(jpeg)

    # --------------------------- input out ------------------------------

    def _on_mouse(self, kind: str, nx: float, ny: float, btn: str) -> None:
        self.hub.send(self.computer_id, Op.INPUT_EVENT, {
            "kind": "mouse", "event": kind, "x": nx, "y": ny, "button": btn,
        })

    def _on_wheel(self, dy: int, dx: int) -> None:
        self.hub.send(self.computer_id, Op.INPUT_EVENT, {
            "kind": "scroll", "dy": dy, "dx": dx,
        })

    def _on_key(self, name: str, pressed: bool, text: str, mods: list) -> None:
        self.hub.send(self.computer_id, Op.INPUT_EVENT, {
            "kind": "key", "key": name, "pressed": pressed,
            "text": text, "modifiers": mods,
        })

    # --------------------------- cleanup --------------------------------

    def closeEvent(self, ev):  # noqa: N802
        # Stop the high-rate stream; the main grid restarts the slow
        # thumbnail stream via _ensure_thumb_streams using the user's
        # current Preferences. We just need to stop ours.
        self.hub.send(self.computer_id, Op.STOP_STREAM, {})
        self.hub.send(
            self.computer_id, Op.START_STREAM,
            {"fps": 2, "max_width": 480, "quality": 50},
        )
        super().closeEvent(ev)
