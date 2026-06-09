"""PyQt6 overlay windows shown on the student machine.

Two overlays are supported:

* LockOverlay - opaque full-screen window with a teacher message.
  Blocks keyboard/mouse focus and stays above all other windows.

* DemoOverlay - full-screen window that displays incoming JPEG frames
  broadcast by the teacher (Demo Mode).

A single ``OverlayController`` instance, created on the GUI thread,
owns both windows and exposes thread-safe slots that the asyncio
worker thread can invoke.
"""

from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import Qt, QObject, pyqtSignal, pyqtSlot, QSize, QTimer
from PyQt6.QtGui import QPixmap, QImage, QFont, QPalette, QColor, QKeyEvent
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QMessageBox, QDialog,
    QPushButton, QHBoxLayout,
)


LOG = logging.getLogger("classcontrol.client.overlay")


def _set_windows_kiosk(strict: bool) -> None:
    """Toggle the Windows kiosk: low-level keyboard hook + hidden
    taskbar + DisableTaskMgr policy. No-op everywhere except Windows."""
    if sys.platform != "win32":
        return
    try:
        from client.windows._kiosk import enter_kiosk, exit_kiosk
        if strict:
            enter_kiosk()
        else:
            exit_kiosk()
    except Exception:
        LOG.exception("Windows kiosk toggle failed")


def _set_kiosk(strict: bool) -> None:
    """Cross-platform kiosk-mode dispatcher used by the lock overlay."""
    _set_macos_kiosk(strict)
    _set_windows_kiosk(strict)


def _set_macos_kiosk(strict: bool) -> None:
    """Toggle macOS kiosk presentation mode.

    When ``strict=True``: hides the dock and menu bar, disables ``Cmd-Tab``
    process switching, blocks the ``Cmd-Option-Esc`` Force-Quit dialog,
    disables session termination (logout / shutdown shortcuts), and
    prevents the user hiding the front app with ``Cmd-H``. Combined with
    the full-screen ``LockOverlay``, this means a real student can't get
    past the lock without the master sending an UNLOCK.

    When ``strict=False`` it restores ``NSApplicationPresentationDefault``
    — important for loopback testing so the teacher doesn't lock themselves
    out of their own machine.

    No-op on non-macOS platforms or when pyobjc isn't available.
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import (   # type: ignore[import-not-found]
            NSApplication,
            NSApplicationPresentationDefault,
            NSApplicationPresentationHideDock,
            NSApplicationPresentationHideMenuBar,
            NSApplicationPresentationDisableProcessSwitching,
            NSApplicationPresentationDisableForceQuit,
            NSApplicationPresentationDisableSessionTermination,
            NSApplicationPresentationDisableHideApplication,
            NSApplicationPresentationDisableAppleMenu,
        )
    except Exception as exc:
        LOG.warning("AppKit unavailable; strict lock won't be enforced: %s", exc)
        return
    try:
        if strict:
            opts = (
                NSApplicationPresentationHideDock
                | NSApplicationPresentationHideMenuBar
                | NSApplicationPresentationDisableProcessSwitching
                | NSApplicationPresentationDisableForceQuit
                | NSApplicationPresentationDisableSessionTermination
                | NSApplicationPresentationDisableHideApplication
                | NSApplicationPresentationDisableAppleMenu
            )
        else:
            opts = NSApplicationPresentationDefault
        NSApplication.sharedApplication().setPresentationOptions_(opts)
        LOG.info("kiosk mode set to %s", "STRICT" if strict else "default")
    except Exception:
        LOG.exception("could not set NSApplicationPresentationOptions")


class _LockWindow(QWidget):
    """One opaque black window on one screen, with the teacher's
    message centered. Used internally by :class:`LockOverlay`."""

    def __init__(self, message: str):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor(10, 10, 20))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel(message, self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont(); font.setPointSize(36); font.setBold(True)
        self._label.setFont(font)
        self._label.setStyleSheet("color: white;")
        layout.addWidget(self._label)

    def set_message(self, message: str) -> None:
        self._label.setText(message)

    # Swallow keys so even the QShortcut path can't act on the locked desktop.
    def keyPressEvent(self, ev: QKeyEvent) -> None:    # noqa: N802
        ev.accept()
    def keyReleaseEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        ev.accept()


class LockOverlay:
    """Composite of one :class:`_LockWindow` per attached screen.

    Multi-monitor classrooms get every display blacked out — not just the
    primary one — which was the original gap. Rebuilt each ``show()`` so
    monitor hot-plug between locks is handled correctly.
    """

    def __init__(self, message: str = "Screen locked by teacher"):
        self._message = message
        self._windows: list[_LockWindow] = []

    def set_message(self, message: str) -> None:
        self._message = message
        for w in self._windows:
            w.set_message(message)

    def show(self) -> None:    # mirrors QWidget.show for caller ergonomics
        # Tear down any stale windows from a previous lock (eg. monitor
        # was unplugged in between) and rebuild from the current screen list.
        self.hide()
        app = QApplication.instance()
        for screen in app.screens():
            win = _LockWindow(self._message)
            win.setGeometry(screen.geometry())
            self._windows.append(win)
        for w in self._windows:
            w.showFullScreen()
            w.raise_()
            w.activateWindow()

    def hide(self) -> None:
        for w in self._windows:
            try:
                w.hide()
                w.deleteLater()
            except Exception:
                pass
        self._windows.clear()

    def isVisible(self) -> bool:  # noqa: N802 - matches Qt vocab
        return any(w.isVisible() for w in self._windows)


class _DemoWindow(QWidget):
    """One frame-display window covering one screen."""

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

    def show_frame(self, jpeg_bytes: bytes) -> None:
        # Format-agnostic decode (WebP / JPEG auto-detected)
        img = QImage.fromData(jpeg_bytes)
        if img.isNull():
            return
        pix = QPixmap.fromImage(img).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(pix)

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        ev.accept()


class DemoOverlay:
    """Composite demo broadcaster — one window per screen so the
    teacher's broadcast covers every monitor on the student's machine."""

    def __init__(self):
        self._windows: list[_DemoWindow] = []

    def show(self) -> None:
        self.hide()
        app = QApplication.instance()
        for screen in app.screens():
            win = _DemoWindow()
            win.setGeometry(screen.geometry())
            self._windows.append(win)
        for w in self._windows:
            w.showFullScreen()
            w.raise_()

    def hide(self) -> None:
        for w in self._windows:
            try:
                w.hide()
                w.deleteLater()
            except Exception:
                pass
        self._windows.clear()

    def show_frame(self, jpeg_bytes: bytes) -> None:
        for w in self._windows:
            w.show_frame(jpeg_bytes)

    def isVisible(self) -> bool:  # noqa: N802
        return any(w.isVisible() for w in self._windows)


class TeacherMessageDialog(QDialog):
    """An attention-grabbing message popup for student machines.

    * Always-on-top (``WindowStaysOnTopHint`` + ``raise_`` + ``activateWindow``).
    * Big window, big readable body text (20pt).
    * **No minimize / maximize buttons** — only Close.
    * Close button is **disabled for 10 seconds** with a live countdown,
      so the student can't dismiss the message before they've actually
      had time to read it. Esc and the window's X are ignored during
      this lock period.
    """

    LOCKED_SECONDS = 10

    def __init__(self, title: str, body: str, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint,
            # NOTE: deliberately NOT WindowMinimizeButtonHint /
            # WindowMaximizeButtonHint / WindowCloseButtonHint — close
            # is via our own button so we control the 10-second lock.
        )
        self.setWindowTitle(title or "Message from teacher")
        self.setModal(True)
        self.setMinimumSize(640, 420)

        # Title strip
        title_lbl = QLabel(title or "Message from teacher")
        tf = QFont(); tf.setPointSize(22); tf.setBold(True)
        title_lbl.setFont(tf)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setStyleSheet("color: white; background: #2a3550; padding: 16px;")

        # Body
        body_lbl = QLabel(body or "")
        bf = QFont(); bf.setPointSize(20)
        body_lbl.setFont(bf)
        body_lbl.setWordWrap(True)
        body_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_lbl.setStyleSheet("padding: 24px;")
        body_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        # Close button — big, disabled at first
        self.close_btn = QPushButton(f"Close  ({self.LOCKED_SECONDS}s)")
        cb_font = QFont(); cb_font.setPointSize(16); cb_font.setBold(True)
        self.close_btn.setFont(cb_font)
        self.close_btn.setMinimumHeight(56)
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(24, 12, 24, 24)
        bottom.addStretch()
        bottom.addWidget(self.close_btn)
        bottom.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(title_lbl)
        layout.addWidget(body_lbl, 1)
        layout.addLayout(bottom)

        # Countdown
        self._remaining = self.LOCKED_SECONDS
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
            self.close_btn.setText("Close")
            self.close_btn.setEnabled(True)
        else:
            self.close_btn.setText(f"Close  ({self._remaining}s)")

    # Block escape and the window-frame X while the timer is running.
    def reject(self) -> None:        # Esc key, programmatic reject
        if self.close_btn.isEnabled():
            super().reject()
        # else: silently ignore

    def closeEvent(self, ev) -> None:    # title-bar X (if the OS shows one)
        if self.close_btn.isEnabled():
            super().closeEvent(ev)
        else:
            ev.ignore()

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        if (ev.key() == Qt.Key.Key_Escape
                and not self.close_btn.isEnabled()):
            ev.accept()
            return
        super().keyPressEvent(ev)


class OverlayController(QObject):
    """GUI-thread owner of the overlay windows. Exposes thread-safe slots."""

    # str = message, bool = strict (enter kiosk mode? Off for loopback testing)
    requestLock = pyqtSignal(str, bool)
    requestUnlock = pyqtSignal()
    requestMessage = pyqtSignal(str, str)  # title, body
    requestDemoStart = pyqtSignal()
    requestDemoFrame = pyqtSignal(bytes)
    requestDemoStop = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._lock: LockOverlay | None = None
        self._demo: DemoOverlay | None = None
        self._kiosk_active: bool = False
        # Active TeacherMessageDialog instances — held so they aren't
        # garbage-collected mid-display. We pop them out in the finished
        # callback.
        self._active_messages: list[QDialog] = []

        self.requestLock.connect(self._on_lock)
        self.requestUnlock.connect(self._on_unlock)
        self.requestMessage.connect(self._on_message)
        self.requestDemoStart.connect(self._on_demo_start)
        self.requestDemoFrame.connect(self._on_demo_frame)
        self.requestDemoStop.connect(self._on_demo_stop)

    @pyqtSlot(str, bool)
    def _on_lock(self, message: str, strict: bool):
        if self._lock is None:
            self._lock = LockOverlay(message or "Screen locked by teacher")
        else:
            self._lock.set_message(message or "Screen locked by teacher")
        # Apply kiosk presentation options first so the dock/menu bar are
        # already gone by the time the overlay windows paint on top.
        if strict and not self._kiosk_active:
            _set_kiosk(True)
            self._kiosk_active = True
        elif not strict and self._kiosk_active:
            _set_kiosk(False)
            self._kiosk_active = False
        # Multi-window show — covers every attached display.
        self._lock.show()

    @pyqtSlot()
    def _on_unlock(self):
        if self._lock:
            self._lock.hide()
        if self._kiosk_active:
            _set_kiosk(False)
            self._kiosk_active = False

    @pyqtSlot(str, str)
    def _on_message(self, title: str, body: str):
        dlg = TeacherMessageDialog(
            title or "Message from teacher", body or "",
        )
        # Keep a reference so it survives until dismissed.
        self._active_messages.append(dlg)
        dlg.finished.connect(
            lambda _result, d=dlg: self._active_messages.remove(d)
            if d in self._active_messages else None
        )
        # show() is non-blocking; raise + activate to grab focus across
        # whatever the student currently has open.
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    @pyqtSlot()
    def _on_demo_start(self):
        if self._demo is None:
            self._demo = DemoOverlay()
        self._demo.show()       # composite multi-screen show

    @pyqtSlot(bytes)
    def _on_demo_frame(self, jpeg: bytes):
        if self._demo and self._demo.isVisible():
            self._demo.show_frame(jpeg)

    @pyqtSlot()
    def _on_demo_stop(self):
        if self._demo:
            self._demo.hide()
