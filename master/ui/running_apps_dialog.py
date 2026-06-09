"""
Per-client "Running Apps" dialog.

Asks the student daemon for its current list of GUI apps and presents
them in a sortable table with three actions per selection:

  * **Kill** — force-quit the app on the student machine right now.
  * **Kill & Block** — same as Kill, then add the app's bundle ID
    (or executable name) to the persistent block list so it can't be
    relaunched.
  * **Refresh** — re-request the list.

The dialog auto-refreshes every 3 seconds while open. It listens for
``RUNNING_APPS`` frames from the hub addressed to its target computer
and updates the table in place.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QLabel, QHeaderView, QAbstractItemView, QMessageBox,
)

from shared.protocol import Op


class RunningAppsDialog(QDialog):
    """Shows the apps running on one specific student machine."""

    # Emitted when the teacher wants to add an app to the persistent
    # block list. The main window decides how to merge into _blocked_apps.
    blockRequested = pyqtSignal(str)   # identifier (bundle_id or exe name)

    def __init__(self, parent, hub, computer_id: str, target_name: str):
        super().__init__(parent)
        self.hub = hub
        self.computer_id = computer_id
        self.setWindowTitle(f"Running apps – {target_name}")
        self.resize(720, 480)

        # ---- Layout ----------------------------------------------------
        outer = QVBoxLayout(self)

        info = QLabel(
            f"Live list of GUI apps running on <b>{target_name}</b>. "
            "Select rows then use Kill or Kill &amp; Block."
        )
        info.setWordWrap(True)
        outer.addWidget(info)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(
            ["App", "Bundle ID / Executable", "PID", ""]
        )
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch,
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch,
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection,
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        outer.addWidget(self.table, 1)

        # ---- Buttons ---------------------------------------------------
        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.kill_btn = QPushButton("Kill")
        self.kill_block_btn = QPushButton("Kill && Block")
        self.close_btn = QPushButton("Close")
        for b in (self.refresh_btn, self.kill_btn, self.kill_block_btn, self.close_btn):
            btn_row.addWidget(b)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        self.refresh_btn.clicked.connect(self._request)
        self.kill_btn.clicked.connect(self._on_kill)
        self.kill_block_btn.clicked.connect(self._on_kill_and_block)
        self.close_btn.clicked.connect(self.accept)

        # ---- Listen for RUNNING_APPS frames ----------------------------
        hub.signals.messageFromClient.connect(self._on_message)

        # ---- Auto-refresh + initial request ----------------------------
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._request)
        self._timer.start()
        QTimer.singleShot(0, self._request)

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    def _request(self) -> None:
        self.hub.send(self.computer_id, Op.GET_RUNNING_APPS, {})

    def _on_message(self, cid: str, op: str, header: dict) -> None:
        if cid != self.computer_id or op != Op.RUNNING_APPS:
            return
        apps = header.get("apps", []) or []
        self._populate(apps)

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def _populate(self, apps: list[dict]) -> None:
        # Preserve selection (by PID) across refreshes.
        selected_pids = {
            int(self.table.item(r, 2).text())
            for r in {ix.row() for ix in self.table.selectedIndexes()}
            if self.table.item(r, 2) is not None
        }

        self.table.setRowCount(0)
        bold = QFont(); bold.setBold(True)
        for app in apps:
            row = self.table.rowCount()
            self.table.insertRow(row)

            name_item = QTableWidgetItem(app.get("name") or "")
            if app.get("active"):
                name_item.setFont(bold)
            id_item = QTableWidgetItem(
                app.get("bundle_id") or app.get("exe") or ""
            )
            pid_item = QTableWidgetItem(str(app.get("pid", "")))
            # store the whole dict on the row for easy access
            name_item.setData(Qt.ItemDataRole.UserRole, app)

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, id_item)
            self.table.setItem(row, 2, pid_item)
            self.table.setItem(row, 3, QTableWidgetItem(""))

            if int(app.get("pid", 0)) in selected_pids:
                self.table.selectRow(row)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch,
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch,
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _selected_apps(self) -> list[dict]:
        seen: set[int] = set()
        out: list[dict] = []
        for ix in self.table.selectedIndexes():
            row = ix.row()
            if row in seen:
                continue
            seen.add(row)
            name_item = self.table.item(row, 0)
            if name_item is None:
                continue
            app = name_item.data(Qt.ItemDataRole.UserRole) or {}
            if app:
                out.append(app)
        return out

    def _on_kill(self) -> None:
        apps = self._selected_apps()
        if not apps:
            return
        for app in apps:
            self.hub.send(self.computer_id, Op.KILL_APP, {
                "pid": int(app.get("pid", 0)),
                "bundle_id": app.get("bundle_id") or "",
                "force": True,
            })
        # Refresh shortly after so the table reflects what's gone.
        QTimer.singleShot(400, self._request)

    def _on_kill_and_block(self) -> None:
        apps = self._selected_apps()
        if not apps:
            return
        # Confirm bulk
        if len(apps) > 1:
            ans = QMessageBox.question(
                self, "Block apps",
                f"Kill and add {len(apps)} app(s) to the block list?",
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        for app in apps:
            # Prefer the bundle ID (stable across renames) over the
            # localized name; fall back to exe name on Windows.
            identifier = (
                app.get("bundle_id")
                or app.get("exe")
                or app.get("name") or ""
            )
            if identifier:
                self.blockRequested.emit(identifier)
            self.hub.send(self.computer_id, Op.KILL_APP, {
                "pid": int(app.get("pid", 0)),
                "bundle_id": app.get("bundle_id") or "",
                "force": True,
            })
        QTimer.singleShot(400, self._request)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:   # noqa: N802
        self._timer.stop()
        try:
            self.hub.signals.messageFromClient.disconnect(self._on_message)
        except Exception:
            pass
        super().closeEvent(event)
