"""
'Check for Updates…' dialog.

State machine:

  CHECKING  --(no update)-->  UP-TO-DATE  →  close
            --(update found)-->  UPDATE-AVAILABLE  --(user clicks Install)-->
                                  DOWNLOADING (progress bar)  -->
                                  INSTALLING (writes helper, spawns it) -->
                                  QUITTING

Network + download happen on a worker thread so the UI stays responsive.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QMessageBox,
)

from shared.version import VERSION, UPDATE_MANIFEST_URL
from shared import updater


# ---------------------------------------------------------------------------
# Worker — runs blocking network ops off the UI thread
# ---------------------------------------------------------------------------


class _UpdateWorker(QObject):
    checked = pyqtSignal(object, str)       # UpdateInfo|None, error_message
    progress = pyqtSignal(int, int)          # bytes_so_far, total
    downloaded = pyqtSignal(str)             # path to archive
    failed = pyqtSignal(str)                 # error message

    def __init__(self, manifest_url: str = UPDATE_MANIFEST_URL):
        super().__init__()
        self.manifest_url = manifest_url

    def check(self):
        def run():
            try:
                manifest = updater.fetch_manifest(self.manifest_url)
                info = updater.find_update(manifest, "teacher")
                self.checked.emit(info, "")
            except Exception as exc:
                self.checked.emit(None, f"{type(exc).__name__}: {exc}")
        threading.Thread(target=run, daemon=True).start()

    def download(self, info):
        def run():
            try:
                dest = Path(tempfile.mkdtemp(prefix="cc-update-")) / "update.zip"
                updater.download_archive(
                    info, dest, progress=self.progress.emit,
                )
                self.downloaded.emit(str(dest))
            except Exception as exc:
                self.failed.emit(f"{type(exc).__name__}: {exc}")
        threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class UpdateDialog(QDialog):
    def __init__(self, parent=None, install_root: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle("Check for Updates")
        self.setMinimumWidth(540)
        self._install_root = install_root or _guess_install_root()
        self._info = None
        self._worker = _UpdateWorker()

        # --- UI ---
        self.status_label = QLabel("Checking for updates…")
        self.status_label.setStyleSheet("font-weight: 500; font-size: 14px;")

        self.current_label = QLabel(f"Current version:  <b>{VERSION}</b>")
        self.latest_label  = QLabel("")
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(160)
        self.notes.hide()

        self.progress = QProgressBar()
        self.progress.hide()

        self.install_btn = QPushButton("Install update")
        self.install_btn.setDefault(True)
        self.install_btn.setEnabled(False)
        self.install_btn.hide()
        self.install_btn.clicked.connect(self._on_install)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addWidget(self.current_label)
        layout.addWidget(self.latest_label)
        layout.addWidget(self.notes)
        layout.addWidget(self.progress)
        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(self.install_btn)
        btns.addWidget(self.close_btn)
        layout.addLayout(btns)

        # --- Worker wiring ---
        self._worker.checked.connect(self._on_checked)
        self._worker.progress.connect(self._on_progress)
        self._worker.downloaded.connect(self._on_downloaded)
        self._worker.failed.connect(self._on_failed)

        # Kick off the check after the dialog is on screen.
        QTimer.singleShot(50, self._worker.check)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_checked(self, info, error: str):
        if error:
            self.status_label.setText("Could not check for updates.")
            self.notes.setPlainText(
                f"{error}\n\n"
                f"Manifest URL: {UPDATE_MANIFEST_URL}\n\n"
                "Check your internet connection or the manifest URL."
            )
            self.notes.show()
            return
        if info is None:
            self.status_label.setText("You're up to date.")
            self.latest_label.setText(
                f"Latest available:  <b>{VERSION}</b> (same as installed)"
            )
            return

        self._info = info
        self.status_label.setText("Update available!")
        self.latest_label.setText(
            f"Latest available:  <b>{info.latest_version}</b>"
            + (f"  ({info.released})" if info.released else "")
        )
        if info.notes:
            self.notes.setPlainText(info.notes)
            self.notes.show()
        self.install_btn.show()
        self.install_btn.setEnabled(True)

    def _on_install(self):
        if not self._info:
            return
        self.install_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.status_label.setText(
            f"Downloading {self._info.latest_version}…"
        )
        self.progress.setRange(0, 0)   # indeterminate until we know total
        self.progress.show()
        self._worker.download(self._info)

    def _on_progress(self, done: int, total: int):
        if total > 0:
            if self.progress.maximum() == 0:
                self.progress.setRange(0, max(1, total))
            self.progress.setValue(done)
        else:
            # Unknown total — flicker between values to show life.
            self.progress.setValue((done // 65536) % 100)

    def _on_downloaded(self, archive_path: str):
        self.status_label.setText("Installing — the app will relaunch…")
        try:
            updater.install_update(
                Path(archive_path), self._install_root,
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "Install failed",
                f"Update download succeeded but install failed:\n\n{exc}\n\n"
                f"You can manually extract:\n  {archive_path}\nover:\n  {self._install_root}",
            )
            self.close_btn.setEnabled(True)
            return
        # Give the helper a moment to start, then quit.
        QTimer.singleShot(800, QApplication.instance().quit)

    def _on_failed(self, error: str):
        self.status_label.setText("Update failed.")
        self.notes.setPlainText(error)
        self.notes.show()
        self.progress.hide()
        self.install_btn.setEnabled(True)
        self.close_btn.setEnabled(True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _guess_install_root() -> Path:
    """Best-effort detection of the directory the running app lives in.

    Inside a .app bundle on macOS, ``sys.executable`` lives at
    ``/Applications/ClassControl Teacher.app/Contents/MacOS/...`` —
    we want the .app dir itself.

    On Windows packaged with PyInstaller it's
    ``...\\ClassControlTeacher\\ClassControlTeacher.exe`` — we want
    the folder.

    From source it's wherever the user checked the repo out — we point
    at the project root so the swap helper at least has a reasonable
    target. (Updates from source are mostly for testing.)
    """
    exe = Path(sys.executable).resolve()
    # macOS .app
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    # Windows PyInstaller-style: ...\ClassControlTeacher\python.exe
    if sys.platform == "win32":
        return exe.parent
    # Fallback: project root inferred from this file
    return Path(__file__).resolve().parents[2]
