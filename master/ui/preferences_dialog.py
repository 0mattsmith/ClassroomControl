"""Teacher app Preferences (Cmd-, on macOS).

Settings persisted to ``~/Library/Application Support/ClassControl/master/settings.json``
and applied as soon as the dialog is accepted. Most knobs affect new
sessions; the thumbnail-stream changes are pushed to every connected
client right away.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QSpinBox, QLineEdit, QDialogButtonBox, QLabel,
    QCheckBox,
)


class PreferencesDialog(QDialog):
    """Lightweight preferences form. Lives entirely in memory; the
    caller is responsible for persisting ``values()``."""

    DEFAULTS = {
        "thumb_fps": 2,
        "thumb_quality": 60,
        "thumb_max_width": 480,
        "demo_fps": 10,
        "demo_quality": 88,             # WebP — visually near-loss-free
        "remote_control_fps": 20,
        "remote_control_quality": 92,   # WebP — sharp UI text
        "remote_control_max_width": 0,  # 0 = native, no downscale
        "master_ip": "",
        "auto_reconnect": True,
        "show_activity_log": True,
        "last_send_path": "",
        "last_request_path": "",
    }

    def __init__(self, parent=None, settings: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("ClassControl Preferences")
        s = dict(self.DEFAULTS)
        s.update(settings or {})

        form = QFormLayout(self)
        form.addRow(QLabel("<b>Thumbnail grid stream</b>"))

        self.thumb_fps = QSpinBox()
        self.thumb_fps.setRange(1, 15)
        self.thumb_fps.setValue(int(s["thumb_fps"]))
        self.thumb_fps.setSuffix(" fps")
        form.addRow("Frames per second:", self.thumb_fps)

        self.thumb_quality = QSpinBox()
        self.thumb_quality.setRange(10, 95)
        self.thumb_quality.setValue(int(s["thumb_quality"]))
        self.thumb_quality.setSuffix(" %")
        form.addRow("JPEG quality:", self.thumb_quality)

        self.thumb_max_width = QSpinBox()
        self.thumb_max_width.setRange(160, 960)
        self.thumb_max_width.setSingleStep(80)
        self.thumb_max_width.setValue(int(s["thumb_max_width"]))
        self.thumb_max_width.setSuffix(" px")
        form.addRow("Max thumbnail width:", self.thumb_max_width)

        form.addRow(QLabel("<b>Demo broadcast</b>"))

        self.demo_fps = QSpinBox()
        self.demo_fps.setRange(1, 30)
        self.demo_fps.setValue(int(s["demo_fps"]))
        self.demo_fps.setSuffix(" fps")
        form.addRow("Demo frames per second:", self.demo_fps)

        self.demo_quality = QSpinBox()
        self.demo_quality.setRange(10, 95)
        self.demo_quality.setValue(int(s["demo_quality"]))
        self.demo_quality.setSuffix(" %")
        form.addRow("Demo JPEG quality:", self.demo_quality)

        form.addRow(QLabel("<b>Remote control window</b>"))

        self.rc_fps = QSpinBox()
        self.rc_fps.setRange(1, 30)
        self.rc_fps.setValue(int(s["remote_control_fps"]))
        self.rc_fps.setSuffix(" fps")
        form.addRow("Frames per second:", self.rc_fps)

        self.rc_quality = QSpinBox()
        self.rc_quality.setRange(10, 95)
        self.rc_quality.setValue(int(s["remote_control_quality"]))
        self.rc_quality.setSuffix(" %")
        form.addRow("JPEG quality:", self.rc_quality)

        form.addRow(QLabel("<b>Networking</b>"))

        self.master_ip = QLineEdit(s["master_ip"])
        self.master_ip.setPlaceholderText("Auto-detect")
        form.addRow("Your IP (for Internet lockdown):", self.master_ip)

        form.addRow(QLabel("<b>Behaviour</b>"))

        self.auto_reconnect = QCheckBox("Automatically reconnect to disconnected students")
        self.auto_reconnect.setChecked(bool(s["auto_reconnect"]))
        form.addRow(self.auto_reconnect)

        self.show_activity_log = QCheckBox("Show the activity log dock")
        self.show_activity_log.setChecked(bool(s["show_activity_log"]))
        form.addRow(self.show_activity_log)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def values(self) -> dict:
        return {
            "thumb_fps":            int(self.thumb_fps.value()),
            "thumb_quality":        int(self.thumb_quality.value()),
            "thumb_max_width":      int(self.thumb_max_width.value()),
            "demo_fps":             int(self.demo_fps.value()),
            "demo_quality":         int(self.demo_quality.value()),
            "remote_control_fps":   int(self.rc_fps.value()),
            "remote_control_quality": int(self.rc_quality.value()),
            "master_ip":            self.master_ip.text().strip(),
            "auto_reconnect":       bool(self.auto_reconnect.isChecked()),
            "show_activity_log":    bool(self.show_activity_log.isChecked()),
        }
