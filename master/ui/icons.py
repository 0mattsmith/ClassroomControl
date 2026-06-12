"""
Toolbar icons drawn programmatically with QPainter.

No binary asset files are shipped — each icon is a small set of vector
primitives rendered onto a 32 × 32 transparent QPixmap. The stroke colour
is pulled from the current application palette so the icons remain
legible in both light and dark themes.

Each public function returns a fresh ``QIcon``. Call them after
``QApplication`` has been constructed.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QPointF, QRect, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF,
)
from PyQt6.QtWidgets import QApplication

ICON_PX = 32
STROKE_WIDTH = 2.4


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _stroke_color() -> QColor:
    app = QApplication.instance()
    if app is not None:
        return app.palette().color(QPalette.ColorRole.WindowText)
    return QColor("#333")


def _new() -> tuple[QPixmap, QPainter]:
    pm = QPixmap(ICON_PX, ICON_PX)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(
        _stroke_color(), STROKE_WIDTH,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    return pm, p


def _icon(draw_fn: Callable[[QPainter], None]) -> QIcon:
    pm, p = _new()
    try:
        draw_fn(p)
    finally:
        p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# Icons
# ---------------------------------------------------------------------------


def add_computer() -> QIcon:
    def d(p: QPainter) -> None:
        # Monitor outline
        p.drawRoundedRect(QRect(6, 7, 20, 14), 2, 2)
        p.drawLine(11, 25, 21, 25)
        p.drawLine(16, 21, 16, 25)
        # Plus inside the screen
        p.drawLine(13, 14, 19, 14)
        p.drawLine(16, 11, 16, 17)
    return _icon(d)


def edit() -> QIcon:
    def d(p: QPainter) -> None:
        # Diagonal pencil
        p.drawLine(8, 24, 22, 10)
        p.drawLine(20, 8, 24, 12)
        p.drawLine(7, 25, 9, 23)
        # Underline (page)
        p.drawLine(7, 27, 25, 27)
    return _icon(d)


def remove() -> QIcon:
    def d(p: QPainter) -> None:
        # Lid + handle
        p.drawLine(7, 10, 25, 10)
        p.drawLine(13, 7, 19, 7)
        # Bin sides
        p.drawLine(9, 11, 11, 25)
        p.drawLine(23, 11, 21, 25)
        p.drawLine(11, 25, 21, 25)
        # Interior bars
        p.drawLine(14, 13, 14, 23)
        p.drawLine(18, 13, 18, 23)
    return _icon(d)


def reconnect() -> QIcon:
    def d(p: QPainter) -> None:
        # Three-quarter circular arrow
        p.drawArc(7, 7, 18, 18, 30 * 16, 280 * 16)
        head = [QPointF(23, 6), QPointF(27, 11), QPointF(20, 10)]
        p.drawPolyline(QPolygonF(head))
    return _icon(d)


def lock_off() -> QIcon:
    """Padlock with the shackle slightly open — used when nothing is locked yet."""
    def d(p: QPainter) -> None:
        p.drawArc(11, 5, 14, 12, 0, 180 * 16)
        p.drawRoundedRect(QRect(9, 14, 14, 12), 2, 2)
        p.drawEllipse(15, 18, 4, 4)
    return _icon(d)


def lock_on() -> QIcon:
    """Closed padlock — used when student screens are currently locked."""
    def d(p: QPainter) -> None:
        p.drawArc(11, 4, 12, 14, 0, 180 * 16)
        p.drawRoundedRect(QRect(8, 13, 16, 13), 2, 2)
        p.drawEllipse(14, 17, 4, 4)
    return _icon(d)


def message() -> QIcon:
    def d(p: QPainter) -> None:
        p.drawRoundedRect(QRect(6, 7, 20, 13), 3, 3)
        tail = [QPointF(11, 20), QPointF(10, 26), QPointF(16, 20)]
        p.drawPolygon(QPolygonF(tail))
    return _icon(d)


def demo_start() -> QIcon:
    """Play triangle — broadcast is off."""
    def d(p: QPainter) -> None:
        tri = [QPointF(11, 7), QPointF(11, 25), QPointF(26, 16)]
        p.setBrush(QBrush(_stroke_color()))
        p.drawPolygon(QPolygonF(tri))
    return _icon(d)


def demo_stop() -> QIcon:
    """Stop square — broadcast is currently on."""
    def d(p: QPainter) -> None:
        p.setBrush(QBrush(_stroke_color()))
        p.drawRect(10, 10, 12, 12)
    return _icon(d)


def launch() -> QIcon:
    """External-link arrow."""
    def d(p: QPainter) -> None:
        p.drawRect(7, 10, 12, 15)
        p.drawLine(15, 7, 25, 7)
        p.drawLine(25, 7, 25, 17)
        p.drawLine(25, 7, 14, 18)
    return _icon(d)


def send_file() -> QIcon:
    def d(p: QPainter) -> None:
        # Folder outline
        p.drawLine(6, 11, 14, 11)
        p.drawLine(14, 11, 16, 13)
        p.drawLine(16, 13, 26, 13)
        p.drawLine(26, 13, 26, 25)
        p.drawLine(6, 11, 6, 25)
        p.drawLine(6, 25, 26, 25)
        # Up arrow
        p.drawLine(16, 23, 16, 16)
        p.drawLine(13, 19, 16, 16)
        p.drawLine(19, 19, 16, 16)
    return _icon(d)


def request_file() -> QIcon:
    def d(p: QPainter) -> None:
        p.drawLine(6, 11, 14, 11)
        p.drawLine(14, 11, 16, 13)
        p.drawLine(16, 13, 26, 13)
        p.drawLine(26, 13, 26, 25)
        p.drawLine(6, 11, 6, 25)
        p.drawLine(6, 25, 26, 25)
        # Down arrow
        p.drawLine(16, 16, 16, 23)
        p.drawLine(13, 20, 16, 23)
        p.drawLine(19, 20, 16, 23)
    return _icon(d)


def block() -> QIcon:
    """Red 'no entry' circle for app/site blocking."""
    def d(p: QPainter) -> None:
        red_pen = QPen(
            QColor("#c0392b"), STROKE_WIDTH,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
        )
        p.setPen(red_pen)
        p.drawEllipse(6, 6, 20, 20)
        p.drawLine(11, 11, 22, 22)
    return _icon(d)


def lockdown_off() -> QIcon:
    """Empty shield outline — internet lockdown is off."""
    def d(p: QPainter) -> None:
        path = [
            QPointF(16, 5), QPointF(25, 9), QPointF(25, 17),
            QPointF(16, 27), QPointF(7, 17), QPointF(7, 9),
        ]
        p.drawPolygon(QPolygonF(path))
    return _icon(d)


def lockdown_on() -> QIcon:
    """Filled shield with a check mark — lockdown is engaged."""
    def d(p: QPainter) -> None:
        path = [
            QPointF(16, 5), QPointF(25, 9), QPointF(25, 17),
            QPointF(16, 27), QPointF(7, 17), QPointF(7, 9),
        ]
        p.setBrush(QBrush(QColor("#2980b9")))
        p.drawPolygon(QPolygonF(path))
        check_pen = QPen(
            QColor("#ffffff"), STROKE_WIDTH,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        p.setPen(check_pen)
        p.drawLine(11, 16, 14, 19)
        p.drawLine(14, 19, 21, 12)
    return _icon(d)


def power() -> QIcon:
    def d(p: QPainter) -> None:
        # ⏻ symbol: broken circle + vertical stroke
        p.drawArc(7, 9, 18, 18, 60 * 16, 240 * 16)
        p.drawLine(16, 6, 16, 16)
    return _icon(d)


def select_all() -> QIcon:
    def d(p: QPainter) -> None:
        # 3x3 grid of small filled squares
        p.setBrush(QBrush(_stroke_color()))
        for row in range(3):
            for col in range(3):
                p.drawRect(7 + col * 7, 7 + row * 7, 4, 4)
    return _icon(d)


def clear() -> QIcon:
    def d(p: QPainter) -> None:
        p.drawLine(9, 9, 23, 23)
        p.drawLine(23, 9, 9, 23)
    return _icon(d)


def sound_on() -> QIcon:
    """Speaker glyph with three propagation arcs — audio is enabled."""
    def d(p: QPainter) -> None:
        # Speaker body
        body = [
            QPointF(8, 13), QPointF(12, 13), QPointF(17, 8),
            QPointF(17, 24), QPointF(12, 19), QPointF(8, 19),
        ]
        p.setBrush(QBrush(_stroke_color()))
        p.drawPolygon(QPolygonF(body))
        # Three increasing arcs
        for r in (4, 8, 12):
            p.drawArc(17 - r // 2, 16 - r, r * 2, r * 2,
                      -60 * 16, 120 * 16)
    return _icon(d)


def sound_off() -> QIcon:
    """Speaker glyph with a slash — audio is silenced."""
    def d(p: QPainter) -> None:
        body = [
            QPointF(8, 13), QPointF(12, 13), QPointF(17, 8),
            QPointF(17, 24), QPointF(12, 19), QPointF(8, 19),
        ]
        p.setBrush(QBrush(_stroke_color()))
        p.drawPolygon(QPolygonF(body))
        # Diagonal slash for "muted"
        red_pen = QPen(
            QColor("#c0392b"), STROKE_WIDTH + 1,
            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
        )
        p.setPen(red_pen)
        p.drawLine(20, 9, 28, 23)
    return _icon(d)


def zoom_out() -> QIcon:
    """Minus-in-magnifying-glass for the size-slider's left label."""
    def d(p: QPainter) -> None:
        p.drawEllipse(7, 7, 14, 14)
        p.drawLine(19, 19, 25, 25)
        p.drawLine(11, 14, 17, 14)
    return _icon(d)


def wake_on_lan() -> QIcon:
    """Power-plug-with-lightning glyph for the dedicated WoL toolbar action."""
    def d(p: QPainter) -> None:
        # Outline of a stylised power button
        p.drawArc(7, 9, 18, 18, 60 * 16, 240 * 16)
        # Lightning bolt across the centre — signals "wake remotely"
        bolt = [
            QPointF(16, 7), QPointF(13, 16),
            QPointF(17, 16), QPointF(15, 25),
            QPointF(20, 14), QPointF(16, 14),
        ]
        p.setBrush(QBrush(QColor(255, 215, 60)))
        p.drawPolygon(QPolygonF(bolt))
    return _icon(d)


def visibility_on() -> QIcon:
    """Open eye — 'computer is shown in the grid'."""
    def d(p: QPainter) -> None:
        # Almond / lens shape
        path = [
            QPointF(5, 16), QPointF(16, 8), QPointF(27, 16),
            QPointF(16, 24),
        ]
        p.drawPolygon(QPolygonF(path))
        # Pupil
        p.setBrush(QBrush(_stroke_color()))
        p.drawEllipse(13, 13, 6, 6)
    return _icon(d)


def visibility_off() -> QIcon:
    """Crossed-out eye — 'computer is hidden from the grid'."""
    def d(p: QPainter) -> None:
        path = [
            QPointF(5, 16), QPointF(16, 8), QPointF(27, 16),
            QPointF(16, 24),
        ]
        p.drawPolygon(QPolygonF(path))
        # Strike-through
        p.drawLine(6, 6, 26, 26)
    return _icon(d)


def pause() -> QIcon:
    """Two vertical bars — the universal pause glyph."""
    def d(p: QPainter) -> None:
        p.setBrush(QBrush(_stroke_color()))
        p.drawRect(11, 8, 4, 16)
        p.drawRect(17, 8, 4, 16)
    return _icon(d)


def zoom_in() -> QIcon:
    """Plus-in-magnifying-glass for the size-slider's right label."""
    def d(p: QPainter) -> None:
        p.drawEllipse(7, 7, 14, 14)
        p.drawLine(19, 19, 25, 25)
        p.drawLine(11, 14, 17, 14)
        p.drawLine(14, 11, 14, 17)
    return _icon(d)
