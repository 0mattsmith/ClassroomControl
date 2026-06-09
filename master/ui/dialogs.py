"""Modal dialogs for the master UI: add computer, send message, blocking,
launch app/URL, file send/request, power management."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QSpinBox, QComboBox, QTextEdit,
    QDialogButtonBox, QFileDialog, QMessageBox, QPushButton, QLabel,
    QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem, QInputDialog,
    QCheckBox, QToolButton, QMenu,
)

from master.roster import Computer
from shared import protocol
from shared.text import normalize_hostname


# ---------------------------------------------------------------------------
# Suggested block-list entries
#
# Each entry is (friendly_name, identifier). The identifier is what the
# client backend matches against — executable name on Windows, bundle ID
# on macOS. The match logic in client/{windows,macos}/blocking.py is
# case-insensitive and substring-aware for bare names without dots, so
# "taskmgr" matches "taskmgr.exe" and "com.apple.terminal" matches
# the running Terminal app.
# ---------------------------------------------------------------------------

SUGGESTED_WINDOWS_APPS: list[tuple[str, str]] = [
    ("Task Manager",                "taskmgr.exe"),
    ("Command Prompt (cmd)",        "cmd.exe"),
    ("Windows PowerShell",          "powershell.exe"),
    ("PowerShell 7+",               "pwsh.exe"),
    ("Windows Terminal",            "WindowsTerminal.exe"),
    ("Settings (Windows 10/11)",    "SystemSettings.exe"),
    ("Classic Control Panel",       "control.exe"),
    ("Registry Editor",             "regedit.exe"),
    ("Microsoft Management Console", "mmc.exe"),
    ("System Configuration (msconfig)", "msconfig.exe"),
    ("Resource Monitor",            "resmon.exe"),
    ("Performance Monitor",         "perfmon.exe"),
    ("Notepad",                     "notepad.exe"),
    ("Calculator",                  "Calculator.exe"),
    ("Microsoft Store",             "WinStore.App.exe"),
]

SUGGESTED_MACOS_APPS: list[tuple[str, str]] = [
    ("Terminal",                    "com.apple.Terminal"),
    ("iTerm2",                      "com.googlecode.iterm2"),
    ("System Settings",             "com.apple.systempreferences"),
    ("Activity Monitor",            "com.apple.ActivityMonitor"),
    ("Console (log viewer)",        "com.apple.Console"),
    ("Disk Utility",                "com.apple.DiskUtility"),
    ("Script Editor",               "com.apple.ScriptEditor2"),
    ("Automator",                   "com.apple.Automator"),
]


# ---------------------------------------------------------------------------
# Add / edit computer
# ---------------------------------------------------------------------------


class ComputerDialog(QDialog):
    def __init__(self, parent=None, existing: Computer | None = None,
                 groups: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Computer")
        self.existing = existing

        self.name = QLineEdit(existing.name if existing else "")
        self.host = QLineEdit(existing.host if existing else "")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(existing.port if existing else protocol.DEFAULT_PORT)
        self.group = QComboBox()
        self.group.setEditable(True)
        for g in groups or ["default"]:
            self.group.addItem(g)
        if existing:
            self.group.setCurrentText(existing.group)
        self.mac = QLineEdit(existing.mac if existing else "")
        self.notes = QLineEdit(existing.notes if existing else "")

        form = QFormLayout(self)
        form.addRow("Name:", self.name)
        form.addRow("Host / IP:", self.host)
        form.addRow("Port:", self.port)
        form.addRow("Group:", self.group)
        form.addRow("MAC (for WOL, optional):", self.mac)
        form.addRow("Notes:", self.notes)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def computer(self) -> Computer:
        return Computer(
            id=self.existing.id if self.existing else uuid.uuid4().hex,
            name=self.name.text().strip() or self.host.text().strip(),
            host=self.host.text().strip(),
            port=int(self.port.value()),
            group=self.group.currentText().strip() or "default",
            mac=self.mac.text().strip(),
            notes=self.notes.text().strip(),
        )


# ---------------------------------------------------------------------------
# Send message
# ---------------------------------------------------------------------------


class MessageDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Send message")
        self.title = QLineEdit("Message from teacher")
        self.body = QTextEdit()
        self.body.setPlaceholderText("Type the message you want to broadcast…")
        layout = QFormLayout(self)
        layout.addRow("Title:", self.title)
        layout.addRow("Body:", self.body)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def payload(self) -> dict:
        return {"title": self.title.text(), "body": self.body.toPlainText()}


# ---------------------------------------------------------------------------
# Blocking (apps + URLs)
# ---------------------------------------------------------------------------


class BlockingDialog(QDialog):
    """Configure blocked apps + websites with per-item AND master toggles.

    Each app / URL row has a checkbox so individual entries can be
    enabled / disabled without removing them from the list. The two
    section headers also have a master checkbox — uncheck to disable
    that entire list at once (useful between lessons).

    Persistence: any close path saves (Apply & Close, Esc, the X).
    Output of :meth:`values_state` is a dict per list mapping each entry
    to its enabled bool, plus the master flag. The caller computes the
    effective list (master_on AND per-item enabled) before pushing.
    """

    def __init__(self, parent=None,
                 apps_state: dict[str, bool] | None = None,
                 urls_state: dict[str, bool] | None = None,
                 apps_master: bool = True,
                 urls_master: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Block apps and websites")
        self.resize(620, 560)

        layout = QVBoxLayout(self)

        # --- Apps section -----------------------------------------------
        self.apps_master_cb = QCheckBox(
            "Block apps  (uncheck to temporarily disable every app below)"
        )
        self.apps_master_cb.setChecked(apps_master)
        cf = QFont(); cf.setBold(True)
        self.apps_master_cb.setFont(cf)
        layout.addWidget(self.apps_master_cb)

        self.apps = QListWidget()
        for ident, on in (apps_state or {}).items():
            if not ident:
                continue
            it = QListWidgetItem(str(ident))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(
                Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
            )
            self.apps.addItem(it)
        layout.addWidget(self.apps)

        app_btns = QHBoxLayout()
        b_add = QPushButton("Add…"); b_rm = QPushButton("Remove")
        b_add.clicked.connect(lambda: self._add(self.apps, "App identifier"))
        b_rm.clicked.connect(lambda: self._remove(self.apps))
        b_suggest = self._build_suggestions_button()
        app_btns.addWidget(b_add)
        app_btns.addWidget(b_rm)
        app_btns.addWidget(b_suggest)
        app_btns.addStretch()
        layout.addLayout(app_btns)

        # --- URLs section -----------------------------------------------
        self.urls_master_cb = QCheckBox(
            "Block websites  (uncheck to temporarily disable every URL below)"
        )
        self.urls_master_cb.setChecked(urls_master)
        self.urls_master_cb.setFont(cf)
        layout.addWidget(self.urls_master_cb)

        self.urls = QListWidget()
        for host, on in (urls_state or {}).items():
            if not host:
                continue
            it = QListWidgetItem(str(host))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(
                Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
            )
            self.urls.addItem(it)
        layout.addWidget(self.urls)

        url_btns = QHBoxLayout()
        u_add = QPushButton("Add…"); u_rm = QPushButton("Remove")
        u_add.clicked.connect(lambda: self._add(self.urls, "Hostname"))
        u_rm.clicked.connect(lambda: self._remove(self.urls))
        url_btns.addWidget(u_add); url_btns.addWidget(u_rm); url_btns.addStretch()
        layout.addLayout(url_btns)

        hint = QLabel(
            "<span style='color:#888;'>Changes save automatically when this "
            "dialog closes. Per-row checkboxes let you keep an entry on the "
            "list but temporarily disable it.</span>"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Apply && Close")
        ok_btn.setDefault(True)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)

    # Esc / X save instead of discarding.
    def reject(self) -> None:
        self.accept()

    def _add(self, listw: QListWidget, prompt: str) -> None:
        text, ok = QInputDialog.getText(self, "Add", prompt + ":")
        if not (ok and text.strip()):
            return
        cleaned = text.strip()
        if listw is self.urls:
            normalized = normalize_hostname(cleaned)
            cleaned = normalized or cleaned
        it = QListWidgetItem(cleaned)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(Qt.CheckState.Checked)
        listw.addItem(it)

    def _remove(self, listw: QListWidget) -> None:
        for it in listw.selectedItems():
            listw.takeItem(listw.row(it))

    # ------------------------------------------------------------------
    # Suggestions menu
    # ------------------------------------------------------------------

    def _build_suggestions_button(self) -> QToolButton:
        """Tool-button with a dropdown of common built-in apps to block.

        Grouped by platform. Click any single item to add it; the two
        "Add all …" entries bulk-add a whole platform's defaults. Items
        that are already in the list (case-insensitive match) are
        skipped silently so re-clicking is safe.
        """
        btn = QToolButton(self)
        btn.setText("Suggestions ▾")
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(btn)

        # Windows submenu
        win_menu = menu.addMenu("Windows built-ins")
        bulk_win = QAction("Add all Windows defaults", self)
        bulk_win.triggered.connect(
            lambda: self._bulk_add_suggestions(SUGGESTED_WINDOWS_APPS)
        )
        win_menu.addAction(bulk_win)
        win_menu.addSeparator()
        for label, ident in SUGGESTED_WINDOWS_APPS:
            act = QAction(f"{label}  ({ident})", self)
            act.triggered.connect(
                lambda _checked, i=ident: self._add_suggestion(i)
            )
            win_menu.addAction(act)

        # macOS submenu
        mac_menu = menu.addMenu("macOS built-ins")
        bulk_mac = QAction("Add all macOS defaults", self)
        bulk_mac.triggered.connect(
            lambda: self._bulk_add_suggestions(SUGGESTED_MACOS_APPS)
        )
        mac_menu.addAction(bulk_mac)
        mac_menu.addSeparator()
        for label, ident in SUGGESTED_MACOS_APPS:
            act = QAction(f"{label}  ({ident})", self)
            act.triggered.connect(
                lambda _checked, i=ident: self._add_suggestion(i)
            )
            mac_menu.addAction(act)

        btn.setMenu(menu)
        return btn

    def _add_suggestion(self, identifier: str) -> None:
        """Insert ``identifier`` into the apps list (checked / enabled)
        unless an entry with the same case-insensitive value is already
        present."""
        ident = (identifier or "").strip()
        if not ident:
            return
        lower = ident.lower()
        for i in range(self.apps.count()):
            if self.apps.item(i).text().lower() == lower:
                return  # already there — silently skip
        it = QListWidgetItem(ident)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(Qt.CheckState.Checked)
        self.apps.addItem(it)

    def _bulk_add_suggestions(
        self, suggestions: list[tuple[str, str]],
    ) -> None:
        for _label, ident in suggestions:
            self._add_suggestion(ident)

    # ------------------------------------------------------------------
    # State out
    # ------------------------------------------------------------------

    def values_state(self) -> dict:
        """Return the full state including the master toggles.

        Shape::

            {
              "apps_master": bool, "urls_master": bool,
              "apps": {"chrome": True, "vivaldi": False, ...},
              "urls": {"youtube.com": True, ...}
            }
        """
        def _read(listw):
            out: dict[str, bool] = {}
            for i in range(listw.count()):
                it = listw.item(i)
                out[it.text()] = it.checkState() == Qt.CheckState.Checked
            return out
        return {
            "apps_master": self.apps_master_cb.isChecked(),
            "urls_master": self.urls_master_cb.isChecked(),
            "apps": _read(self.apps),
            "urls": _read(self.urls),
        }


# ---------------------------------------------------------------------------
# Launch app / URL
# ---------------------------------------------------------------------------


class LaunchDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Open app, file or URL")
        self.target = QLineEdit()
        self.target.setPlaceholderText("e.g. Safari, /Applications/TextEdit.app, https://example.com")
        layout = QFormLayout(self)
        layout.addRow("Target:", self.target)
        hint = QLabel(
            "URLs open in the student's default browser.<br>"
            "App names launch matching macOS apps."
        )
        hint.setWordWrap(True)
        layout.addRow(hint)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def value(self) -> str:
        return self.target.text().strip()


# ---------------------------------------------------------------------------
# File transfer
# ---------------------------------------------------------------------------


class SendFileDialog(QDialog):
    """Send a file to selected students, with a choice of what should
    happen on the student side once the file lands."""

    POST_ACTIONS = [
        ("Do nothing (just save it)",                 "none"),
        ("Open the file in the default app",          "open"),
        ("Open the folder containing the file",       "reveal"),
    ]

    def __init__(self, parent=None, initial_path: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Send file to students")
        self._path: str = initial_path or ""
        self.path_label = QLabel(
            self._path if self._path else "(no file chosen)"
        )
        self.path_label.setWordWrap(True)
        choose = QPushButton("Choose file…")
        choose.clicked.connect(self._pick)

        self.post_action_combo = QComboBox()
        for label, value in self.POST_ACTIONS:
            self.post_action_combo.addItem(label, value)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "File to send (saved to each student's ~/Downloads/ClassControl):"
        ))
        layout.addWidget(choose)
        layout.addWidget(self.path_label)

        action_row = QFormLayout()
        action_row.addRow("After saving on student:", self.post_action_combo)
        layout.addLayout(action_row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _pick(self):
        # Start the picker in the same directory as last time, if any.
        start = self._path or ""
        p, _ = QFileDialog.getOpenFileName(self, "Select file to send", start)
        if p:
            self._path = p
            self.path_label.setText(p)

    def file_path(self) -> str:
        return self._path

    def post_action(self) -> str:
        """One of "none", "open", "reveal" — passed to the client in the
        FILE_PUSH header and honoured server-side after the file is written."""
        return self.post_action_combo.currentData() or "none"


class RequestFileDialog(QDialog):
    """Pull a file from selected students, with a choice of what should
    happen on the teacher side once the file arrives."""

    POST_ACTIONS = [
        ("Open the folder containing the file",   "reveal"),   # default
        ("Open the file in the default app",      "open"),
        ("Do nothing (just save it)",             "none"),
    ]

    def __init__(self, parent=None, initial_path: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Request file or folder from student(s)")
        self.path = QLineEdit(initial_path or "")
        self.path.setPlaceholderText(
            "e.g. assignment.pdf  OR  Projects/Maths  (relative to ~/Downloads/ClassControl)"
        )
        if initial_path:
            # Select-all so a quick edit doesn't require Cmd-A.
            self.path.selectAll()

        self.post_action_combo = QComboBox()
        for label, value in self.POST_ACTIONS:
            self.post_action_combo.addItem(label, value)

        layout = QFormLayout(self)
        layout.addRow("Path on student machine:", self.path)
        layout.addRow("When the file arrives here:", self.post_action_combo)
        hint = QLabel(
            "Use a relative path (resolved from each student's "
            "~/Downloads/ClassControl folder), or an absolute path.<br>"
            "<b>Folders are supported:</b> if the path is a directory it'll "
            "be zipped on the student machine and auto-extracted on yours."
        )
        hint.setWordWrap(True)
        layout.addRow(hint)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def value(self) -> str:
        return self.path.text().strip()

    def post_action(self) -> str:
        """One of "reveal" (default), "open", "none". Honoured locally by
        the master when the FILE_PULL_RESPONSE arrives."""
        return self.post_action_combo.currentData() or "reveal"


# ---------------------------------------------------------------------------
# Internet lockdown
# ---------------------------------------------------------------------------


class InternetLockdownDialog(QDialog):
    def __init__(self, parent=None, suggested_master_ip: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Internet lockdown")
        self.ips = QLineEdit(suggested_master_ip)
        self.ips.setPlaceholderText("Comma-separated allowed IPs (your teacher machine)")
        layout = QFormLayout(self)
        layout.addRow(QLabel(
            "Locks down the student's network so they cannot reach the internet,<br>"
            "while still allowing the teacher machine through (so you keep control)."
        ))
        layout.addRow("Allowed master IP(s):", self.ips)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def master_ips(self) -> list[str]:
        return [s.strip() for s in self.ips.text().split(",") if s.strip()]
