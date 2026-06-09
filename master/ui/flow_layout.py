"""
FlowLayout — a horizontal layout that wraps to the next row when the
available width is exhausted.

This is the canonical Qt FlowLayout example ported to PyQt6. It's used
by ``TeacherToolBar``'s wrap mode so the teacher's toolbar can flow onto
multiple rows instead of hiding overflow behind a chevron.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QSizePolicy, QStyle, QWidget


class FlowLayout(QLayout):
    def __init__(
        self,
        parent: QWidget | None = None,
        margin: int = -1,
        hSpacing: int = -1,
        vSpacing: int = -1,
    ):
        super().__init__(parent)
        if margin >= 0:
            self.setContentsMargins(margin, margin, margin, margin)
        self._h_space = hSpacing
        self._v_space = vSpacing
        self._items: list = []

    def __del__(self):  # pragma: no cover - defensive
        try:
            while self.takeAt(0) is not None:
                pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # QLayout overrides
    # ------------------------------------------------------------------

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    # ------------------------------------------------------------------
    # Spacing
    # ------------------------------------------------------------------

    def horizontalSpacing(self) -> int:  # noqa: N802
        if self._h_space >= 0:
            return self._h_space
        return self._smart_spacing(QStyle.PixelMetric.PM_LayoutHorizontalSpacing)

    def verticalSpacing(self) -> int:  # noqa: N802
        if self._v_space >= 0:
            return self._v_space
        return self._smart_spacing(QStyle.PixelMetric.PM_LayoutVerticalSpacing)

    def _smart_spacing(self, pm: QStyle.PixelMetric) -> int:
        parent = self.parent()
        if parent is None:
            return -1
        if parent.isWidgetType():
            return parent.style().pixelMetric(pm, None, parent)
        return parent.spacing()

    # ------------------------------------------------------------------
    # Layout engine
    # ------------------------------------------------------------------

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(
            +m.left(), +m.top(), -m.right(), -m.bottom(),
        )
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            wid = item.widget()
            space_x = self.horizontalSpacing()
            if space_x == -1 and wid is not None:
                space_x = wid.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Horizontal,
                )
            space_y = self.verticalSpacing()
            if space_y == -1 and wid is not None:
                space_y = wid.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Vertical,
                )
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y() + m.bottom()
