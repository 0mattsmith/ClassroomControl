"""'Import from Veyon…' file picker + preview dialog.

Pick the Veyon config, see what we'd add (with room counts), optionally
prefix a string onto every imported group name (handy if you're
running both tools in parallel and want to tell them apart at a
glance), then commit.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QListWidget, QListWidgetItem, QLineEdit, QFormLayout, QMessageBox,
    QDialogButtonBox, QCheckBox,
)

from master.roster import Computer
from master.veyon_import import (
    parse_veyon_config, default_veyon_paths, VeyonImport,
)


class VeyonImportDialog(QDialog):
    """File chooser + preview + Import button."""

    def __init__(self, parent=None, existing_hosts: set[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Import from Veyon")
        self.resize(620, 520)
        # Hosts already in the roster — used to flag duplicates in the preview.
        self._existing_hosts = {h.lower() for h in (existing_hosts or set())}
        self._import: VeyonImport | None = None
        self._selected_path: str = ""

        # --- File picker ------------------------------------------------
        self.path_label = QLabel("(no Veyon config selected)")
        self.path_label.setWordWrap(True)
        pick_btn = QPushButton("Choose Veyon.json…")
        pick_btn.clicked.connect(self._pick_file)

        # --- Options ----------------------------------------------------
        self.group_prefix = QLineEdit()
        self.group_prefix.setPlaceholderText(
            "(optional) — prepend to every imported group, e.g. 'Veyon /'"
        )
        self.skip_duplicates = QCheckBox(
            "Skip computers whose hostname is already in the roster"
        )
        self.skip_duplicates.setChecked(True)

        opts_form = QFormLayout()
        opts_form.addRow("Group prefix:", self.group_prefix)
        opts_form.addRow(self.skip_duplicates)

        # --- Preview list ----------------------------------------------
        self.summary = QLabel(
            "Pick a Veyon configuration file to see what would be imported."
        )
        self.summary.setWordWrap(True)
        self.preview = QListWidget()

        # --- Buttons ----------------------------------------------------
        self.import_btn = QPushButton("Import")
        self.import_btn.setEnabled(False)
        self.import_btn.setDefault(True)
        self.import_btn.clicked.connect(self.accept)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        # --- Layout -----------------------------------------------------
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Step 1.</b> Pick the Veyon configuration file."))
        h = QHBoxLayout()
        h.addWidget(pick_btn)
        h.addWidget(self.path_label, 1)
        layout.addLayout(h)

        layout.addSpacing(8)
        layout.addWidget(QLabel("<b>Step 2.</b> Options."))
        layout.addLayout(opts_form)

        layout.addSpacing(8)
        layout.addWidget(QLabel("<b>Step 3.</b> Preview."))
        layout.addWidget(self.summary)
        layout.addWidget(self.preview, 1)

        layout.addWidget(QLabel(
            "<span style='color:#888;'>Note: only the computer list is "
            "imported. ClassControl uses its own auth (shared HMAC key), "
            "so you still need to deploy <code>auth.key</code> + the "
            "client daemon to each machine.</span>"
        ))

        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(self.import_btn)
        btns.addWidget(self.cancel_btn)
        layout.addLayout(btns)

        # Re-render the preview when the prefix or dedupe toggle changes.
        self.group_prefix.textChanged.connect(self._refresh_preview)
        self.skip_duplicates.toggled.connect(self._refresh_preview)

    # ------------------------------------------------------------------
    # File picking
    # ------------------------------------------------------------------

    def _pick_file(self):
        defaults = [str(p) for p in default_veyon_paths() if p.exists()]
        start_dir = defaults[0] if defaults else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Veyon configuration",
            start_dir,
            "Veyon config (*.json *.conf);;All files (*)",
        )
        if not path:
            return
        try:
            self._import = parse_veyon_config(path)
            self._selected_path = path
        except Exception as exc:
            QMessageBox.warning(
                self, "Could not parse Veyon file", str(exc),
            )
            return
        self.path_label.setText(path)
        self._refresh_preview()

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _refresh_preview(self):
        self.preview.clear()
        if self._import is None:
            self.summary.setText(
                "Pick a Veyon configuration file to see what would be imported."
            )
            self.import_btn.setEnabled(False)
            return

        info = self._import
        prefix = self.group_prefix.text().strip()
        skip_dupes = self.skip_duplicates.isChecked()

        # Compute the list as it'll actually be added.
        to_import: list[Computer] = []
        dropped_dupes: list[Computer] = []
        for c in info.computers:
            group = (
                f"{prefix} {c.group}".strip()
                if prefix else c.group
            )
            cc = Computer(
                id=c.id, name=c.name, host=c.host, port=c.port,
                group=group, mac=c.mac, notes=c.notes,
            )
            if skip_dupes and cc.host.lower() in self._existing_hosts:
                dropped_dupes.append(cc)
            else:
                to_import.append(cc)

        # Render
        rooms_in_set: set[str] = set()
        for c in to_import:
            rooms_in_set.add(c.group)
            label = f"  {c.name}    {c.host}"
            if c.mac:
                label += f"    [{c.mac}]"
            label += f"    — {c.group}"
            self.preview.addItem(QListWidgetItem(label))

        if dropped_dupes:
            self.preview.addItem(QListWidgetItem(""))
            self.preview.addItem(QListWidgetItem(
                f"— {len(dropped_dupes)} duplicate(s) hidden —"
            ))
            for c in dropped_dupes:
                self.preview.addItem(QListWidgetItem(
                    f"  (dupe)  {c.name}    {c.host}"
                ))

        bits = [
            f"{len(to_import)} computer(s) will be added",
            f"in {len(rooms_in_set)} group(s)",
        ]
        if dropped_dupes:
            bits.append(f"{len(dropped_dupes)} duplicate(s) skipped")
        if info.skipped:
            bits.append(f"{info.skipped} entries had no hostname and were dropped")
        bits.append(f"({info.raw_object_count} raw objects parsed from {info.source_path})")
        self.summary.setText("\n".join(bits))

        self.import_btn.setEnabled(bool(to_import))

    # ------------------------------------------------------------------
    # Result accessor
    # ------------------------------------------------------------------

    def to_import(self) -> list[Computer]:
        """Compute the final import list using the current options.

        Called by the main window after the dialog is accepted.
        """
        if self._import is None:
            return []
        prefix = self.group_prefix.text().strip()
        skip_dupes = self.skip_duplicates.isChecked()
        out: list[Computer] = []
        for c in self._import.computers:
            group = (
                f"{prefix} {c.group}".strip()
                if prefix else c.group
            )
            cc = Computer(
                id=c.id, name=c.name, host=c.host, port=c.port,
                group=group, mac=c.mac, notes=c.notes,
            )
            if skip_dupes and cc.host.lower() in self._existing_hosts:
                continue
            out.append(cc)
        return out
