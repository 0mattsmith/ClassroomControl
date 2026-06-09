"""Single student-machine thumbnail tile shown in the main grid."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap, QImage, QColor, QPainter, QPen, QFont
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QFrame


class Thumbnail(QFrame):
    doubleClicked = pyqtSignal(str)        # computer_id
    selected = pyqtSignal(str, bool)        # computer_id, is_selected
    contextMenuRequested = pyqtSignal(str, object)  # computer_id, global QPoint

    DEFAULT_WIDTH = 280
    ASPECT = 5 / 7   # height / width — gives the 280×200 default ratio

    def __init__(self, computer_id: str, label: str, width: int = DEFAULT_WIDTH):
        super().__init__()
        self.computer_id = computer_id
        self.setFrameShape(QFrame.Shape.Box)
        self.set_size(width)
        self._image_label = QLabel(self)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background: #222; color: #999;")
        self._image_label.setText("(no signal)")
        self._caption = QLabel(label, self)
        self._caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._caption.setStyleSheet("color: white; background: #333; padding: 4px;")
        f = QFont(); f.setBold(True); self._caption.setFont(f)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        layout.addWidget(self._image_label, 1)
        layout.addWidget(self._caption)

        self._is_selected = False
        self._state = "disconnected"
        self._update_border()

    def set_label(self, text: str) -> None:
        self._caption.setText(text)

    def set_size(self, width: int) -> None:
        """Resize the tile to ``width`` pixels, keeping the aspect ratio."""
        width = max(120, int(width))
        height = int(width * self.ASPECT) + 28   # +28 for the caption strip
        self.setFixedSize(QSize(width, height))

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "connected":
            self._image_label.setText("(waiting for frames)")
        elif state == "connecting":
            self._image_label.setText("Connecting…")
            self._image_label.setPixmap(QPixmap())
        elif state == "error":
            self._image_label.setText("⚠  connection error")
            self._image_label.setPixmap(QPixmap())
        else:
            self._image_label.setText("(disconnected)")
            self._image_label.setPixmap(QPixmap())
        self._update_border()

    def set_frame(self, jpeg: bytes) -> None:
        # Format-agnostic decode (WEBP, JPEG, PNG all auto-detected by Qt)
        img = QImage.fromData(jpeg)
        if img.isNull():
            return
        pix = QPixmap.fromImage(img).scaled(
            self._image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(pix)
        self._image_label.setText("")

    def set_selected(self, value: bool) -> None:
        self._is_selected = value
        self._update_border()

    def _update_border(self) -> None:
        color = {
            "connected": "#4caf50",
            "connecting": "#ff9800",
            "error": "#f44336",
            "disconnected": "#666",
        }.get(self._state, "#666")
        border = "3px solid #2196f3" if self._is_selected else f"2px solid {color}"
        self.setStyleSheet(f"Thumbnail {{ border: {border}; background: #111; }}")

    # ------------- Mouse handling -------------

    def mousePressEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self.set_selected(not self._is_selected)
            self.selected.emit(self.computer_id, self._is_selected)
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self.computer_id)

    def contextMenuEvent(self, ev):  # noqa: N802
        # Let the main window pop a per-tile menu (remote control,
        # running apps, lock, etc.).
        self.contextMenuRequested.emit(self.computer_id, ev.globalPos())
        ev.accept()
