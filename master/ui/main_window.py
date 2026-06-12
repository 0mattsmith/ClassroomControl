"""Main teacher window: thumbnail grid + toolbar + activity log."""

from __future__ import annotations

import json
import socket
from pathlib import Path

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QGridLayout, QToolBar, QFileDialog, QMessageBox,
    QDockWidget, QListWidget, QListWidgetItem, QStatusBar, QMenu, QScrollArea,
    QVBoxLayout, QPushButton, QHBoxLayout, QLabel, QListView, QSlider,
    QSizePolicy, QToolButton, QApplication,
)

from shared import config
from shared.protocol import Op, DEFAULT_PORT
from shared.text import normalize_hostname
from shared.wol import send_magic_packet
from master.roster import Roster, Computer
from master.connection import ConnectionHub
from master.ui.thumbnail import Thumbnail
from master.ui.remote_control import RemoteControlWindow
from master.ui.demo_broadcaster import DemoBroadcaster
from master.ui.toolbar import TeacherToolBar
from master.ui.preferences_dialog import PreferencesDialog
from master.ui.running_apps_dialog import RunningAppsDialog
from master.ui.update_dialog import UpdateDialog
from master.ui.veyon_import_dialog import VeyonImportDialog
from master.ui import dialogs, icons
from shared.version import VERSION
# Local launcher (mac or windows) so the master can reveal / open the
# files it receives from students using OS-appropriate commands.
from client import platform as _local_p


class MainWindow(QMainWindow):
    def __init__(self, hub: ConnectionHub, roster: Roster):
        super().__init__()
        self.setWindowTitle("ClassControl – Teacher")
        self.resize(1280, 820)
        self.hub = hub
        self.roster = roster
        self.selected_ids: set[str] = set()
        self.thumbnails: dict[str, Thumbnail] = {}
        self.remote_windows: dict[str, RemoteControlWindow] = {}
        self._thumb_width: int = Thumbnail.DEFAULT_WIDTH

        # View filters — visibility on the grid. Per-machine flag lives
        # on Computer.visible; these two filter all machines at once.
        self._hide_offline: bool = False        # disconnected → hidden
        self._hide_no_session: bool = False     # connected but no user → hidden

        # Last-known info reported by each client (mainly "user" for
        # the no-session filter). Keyed by computer_id.
        self._client_info: dict[str, dict] = {}

        # Per-request post-action: when a FILE_PULL_REQUEST is sent, we
        # remember the teacher's "after it arrives" choice keyed by
        # computer_id; _on_file pops it on receipt.
        self._pending_pull_actions: dict[str, str] = {}

        # Persistent block state — dict[str, bool] per kind + master flag.
        # _effective_blocked_apps / _urls are computed from these and
        # are what actually gets pushed to clients.
        (self._app_states, self._url_states,
         self._apps_master, self._urls_master) = self._load_blocking_state()

        # Lockdown ACKs are async; remember which targets we asked so we
        # can show a clear error if the client reports failure.
        self._pending_lockdown_targets: set[str] = set()
        # Suppress duplicate failure popups within a single user action.
        self._lockdown_failure_shown: bool = False
        self._url_block_failure_shown: bool = False

        # Sticky audio silence — real value loaded just below once
        # ``self._settings`` exists. Holds the master switch only;
        # each (re)connect pushes lock/unlock to honour it.
        self._audio_silenced: bool = False

        # Persistent preferences (thumbnail FPS/quality, demo FPS, etc.)
        self._settings = self._load_settings()
        # Pull the persisted Silence state now that _settings exists.
        self._audio_silenced = bool(self._settings.get("audio_silenced", False))

        self.demo = DemoBroadcaster(
            hub,
            fps=int(self._settings.get("demo_fps", 6)),
            quality=int(self._settings.get("demo_quality", 55)),
        )

        # --- Central thumbnail grid in a scroll area --------------------
        self.grid_host = QWidget()
        self.grid_layout = QGridLayout(self.grid_host)
        self.grid_layout.setContentsMargins(12, 12, 12, 12)
        self.grid_layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid_host)
        self.setCentralWidget(scroll)

        # --- Menu bar (file/edit/class/help) ---------------------------
        self._build_menubar()

        # --- Toolbar ---------------------------------------------------
        self._build_toolbar()

        # --- Activity log dock -----------------------------------------
        self.activity_list = QListWidget()
        dock = QDockWidget("Activity log", self)
        dock.setWidget(self.activity_list)
        # Activity log dock is also movable / floatable / drop-anywhere.
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.hub.signals.activity.connect(self._log_activity)

        # --- Status bar ------------------------------------------------
        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # --- Hub signals ----------------------------------------------
        hub.signals.connectionStateChanged.connect(self._on_state_changed)
        hub.signals.frameReceived.connect(self._on_frame)
        hub.signals.messageFromClient.connect(self._on_message)
        hub.signals.fileFromClient.connect(self._on_file)

        # --- Populate roster -------------------------------------------
        for c in roster.computers:
            self._add_thumbnail(c)
            hub.add_computer(c, auto_connect=True)

        # Periodically nudge thumbnails to subscribe to a low-rate stream
        # for any connection that's idle.
        self._stream_tick = QTimer(self)
        self._stream_tick.setInterval(8000)
        self._stream_tick.timeout.connect(self._ensure_thumb_streams)
        self._stream_tick.start()

        # Auto-check for updates in the background ~3 s after launch.
        # Silent unless an update is available; controlled by the
        # "Check for updates on startup" Preference.
        QTimer.singleShot(3000, self._maybe_check_updates_at_startup)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Menu bar (Apple-style at the top of the screen on macOS)
    # ------------------------------------------------------------------

    def _build_menubar(self) -> None:
        mb = self.menuBar()
        # On macOS this property makes the menu bar use the native bar
        # at the top of the screen instead of an embedded bar inside the
        # window. Qt does this automatically, but setting it explicitly
        # is good documentation.
        mb.setNativeMenuBar(True)

        # ----- File -----------------------------------------------------
        file_menu = mb.addMenu("&File")

        act_add = QAction("Add Computer…", self)
        act_add.setShortcut("Ctrl+N")
        act_add.triggered.connect(self._add_computer)
        file_menu.addAction(act_add)

        act_reconnect = QAction("Reconnect Selected", self)
        act_reconnect.setShortcut("Ctrl+R")
        act_reconnect.triggered.connect(self._reconnect_selected)
        file_menu.addAction(act_reconnect)

        file_menu.addSeparator()

        act_import_veyon = QAction("Import from Veyon…", self)
        act_import_veyon.triggered.connect(self._import_from_veyon)
        file_menu.addAction(act_import_veyon)

        file_menu.addSeparator()

        # MenuRole.QuitRole moves this item into the macOS App menu
        # automatically and gives it the standard Cmd-Q shortcut.
        act_quit = QAction("Quit ClassControl", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.setMenuRole(QAction.MenuRole.QuitRole)
        act_quit.triggered.connect(self._on_quit)
        file_menu.addAction(act_quit)

        # ----- Edit (for Preferences placement on macOS) ----------------
        edit_menu = mb.addMenu("&Edit")
        act_prefs = QAction("Preferences…", self)
        act_prefs.setShortcut("Ctrl+,")
        # PreferencesRole moves it to the macOS App menu's "Preferences…"
        act_prefs.setMenuRole(QAction.MenuRole.PreferencesRole)
        act_prefs.triggered.connect(self._show_preferences)
        edit_menu.addAction(act_prefs)

        # ----- Class menu ----------------------------------------------
        class_menu = mb.addMenu("&Class")

        act_lock = QAction("Lock All Screens", self)
        act_lock.setShortcut("Ctrl+L")
        act_lock.triggered.connect(
            lambda: self._send_targets(Op.LOCK,
                                       {"message": "Screen locked by teacher"})
        )
        class_menu.addAction(act_lock)

        act_unlock = QAction("Unlock All Screens", self)
        act_unlock.setShortcut("Ctrl+Shift+L")
        act_unlock.triggered.connect(lambda: self._send_targets(Op.UNLOCK))
        class_menu.addAction(act_unlock)

        class_menu.addSeparator()

        act_demo_windowed = QAction("Start demo (windowed on students)", self)
        act_demo_windowed.triggered.connect(self._start_demo_windowed)
        class_menu.addAction(act_demo_windowed)
        act_demo_stop = QAction("Stop demo", self)
        act_demo_stop.triggered.connect(self._stop_demo_via_menu)
        class_menu.addAction(act_demo_stop)

        class_menu.addSeparator()

        act_msg = QAction("Send Message…", self)
        act_msg.setShortcut("Ctrl+M")
        act_msg.triggered.connect(self._send_message)
        class_menu.addAction(act_msg)

        act_open = QAction("Open App/URL…", self)
        act_open.triggered.connect(self._launch)
        class_menu.addAction(act_open)

        class_menu.addSeparator()

        act_send_file = QAction("Send File…", self)
        act_send_file.triggered.connect(self._send_file)
        class_menu.addAction(act_send_file)

        act_get_file = QAction("Request File…", self)
        act_get_file.triggered.connect(self._request_file)
        class_menu.addAction(act_get_file)

        class_menu.addSeparator()

        act_block = QAction("Block Apps / Sites…", self)
        act_block.triggered.connect(self._configure_blocking)
        class_menu.addAction(act_block)

        act_running = QAction("Show Running Apps…", self)
        act_running.setShortcut("Ctrl+Shift+R")
        act_running.triggered.connect(self._show_running_apps_for_selection)
        class_menu.addAction(act_running)

        # ----- View (visibility filters) -------------------------------
        view_menu = mb.addMenu("&View")

        self.act_hide_offline = QAction("Hide offline machines", self)
        self.act_hide_offline.setCheckable(True)
        self.act_hide_offline.setChecked(self._hide_offline)
        self.act_hide_offline.toggled.connect(self._on_hide_offline_toggled)
        view_menu.addAction(self.act_hide_offline)

        self.act_hide_no_session = QAction(
            "Hide machines without a logged-in user", self,
        )
        self.act_hide_no_session.setCheckable(True)
        self.act_hide_no_session.setChecked(self._hide_no_session)
        self.act_hide_no_session.toggled.connect(self._on_hide_no_session_toggled)
        view_menu.addAction(self.act_hide_no_session)

        view_menu.addSeparator()

        act_show_all = QAction("Show all hidden computers", self)
        act_show_all.triggered.connect(self._show_all_computers)
        view_menu.addAction(act_show_all)

        # ----- Help -----------------------------------------------------
        help_menu = mb.addMenu("&Help")
        act_update = QAction("Check for Updates…", self)
        # ApplicationSpecificRole tells macOS to fold this into the
        # bold App menu ("ClassControl Teacher") next to About /
        # Preferences / Quit — that's where Mac users look for it.
        # On other platforms it stays under Help.
        act_update.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
        act_update.triggered.connect(self._show_update_dialog)
        help_menu.addAction(act_update)
        help_menu.addSeparator()
        act_about = QAction("About ClassControl", self)
        act_about.setMenuRole(QAction.MenuRole.AboutRole)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    # ------------------------------------------------------------------
    # Preferences + About
    # ------------------------------------------------------------------

    def _settings_path(self) -> Path:
        return config.user_config_dir("master") / "settings.json"

    def _load_settings(self) -> dict:
        path = self._settings_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def _save_settings(self) -> None:
        path = self._settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._settings, indent=2))

    def _show_preferences(self) -> None:
        dlg = PreferencesDialog(self, self._settings)
        if dlg.exec() != PreferencesDialog.DialogCode.Accepted:
            return
        self._settings = dlg.values()
        self._save_settings()
        # Apply settings that take effect right now:
        # 1. Demo broadcaster — rebuild with new fps/quality
        try:
            was_active = self.demo.is_active()
            self.demo.stop()
        except Exception:
            was_active = False
        self.demo = DemoBroadcaster(
            self.hub,
            fps=int(self._settings.get("demo_fps", 6)),
            quality=int(self._settings.get("demo_quality", 55)),
        )
        if was_active:
            self.demo.start()
        # 2. Re-push thumbnail stream settings to every connected client.
        for cid in self.hub.computer_ids():
            st = self.hub.get_state(cid)
            if st and st.state == "connected":
                self.hub.send(cid, Op.START_STREAM, {
                    "fps":       int(self._settings.get("thumb_fps", 1)),
                    "max_width": int(self._settings.get("thumb_max_width", 320)),
                    "quality":   int(self._settings.get("thumb_quality", 35)),
                })
        # 3. Activity log dock visibility
        show_log = bool(self._settings.get("show_activity_log", True))
        for dock in self.findChildren(QDockWidget):
            dock.setVisible(show_log)
        self._log_activity("preferences saved")

    def _show_about(self) -> None:
        QMessageBox.about(
            self, "About ClassControl",
            f"<b>ClassControl Teacher</b><br>"
            f"Version <code>{VERSION}</code><br><br>"
            "A Veyon-style classroom management tool for macOS and Windows.<br><br>"
            "TLS + HMAC-SHA256 authentication. Open source."
        )

    def _show_update_dialog(self) -> None:
        """Help → Check for Updates… (also Mac App-menu entry).

        Opens the modal :class:`UpdateDialog` which checks the manifest
        in the background and, on the user's say-so, downloads and
        installs the new build.
        """
        dlg = UpdateDialog(self)
        dlg.exec()

    def _maybe_check_updates_at_startup(self) -> None:
        """Background update check fired ~3 s after launch.

        Silent on no-update or network failure (so the teacher app
        doesn't pester anyone). When an update IS available, the
        existing :class:`UpdateDialog` is opened — the teacher then
        sees the version + release notes and decides whether to install.

        Honours the "Check for updates on startup" Preferences toggle.
        """
        if not self._settings.get("check_updates_on_startup", True):
            return
        import threading
        from shared import updater
        from shared.version import VERSION

        def _check():
            try:
                manifest = updater.fetch_manifest()
                info = updater.find_update(manifest, "teacher",
                                           current_version=VERSION)
            except Exception:
                # Silent failure — could be no network, GH down,
                # placeholder URL still in version.py, etc.
                return
            if info is None:
                return
            # Hop back to the GUI thread before opening the dialog.
            QTimer.singleShot(0, self._show_update_dialog)

        threading.Thread(target=_check, daemon=True).start()

    def _on_quit(self) -> None:
        QApplication.instance().quit()

    # ------------------------------------------------------------------
    # Veyon import
    # ------------------------------------------------------------------

    def _import_from_veyon(self) -> None:
        """File → Import from Veyon…

        Lets the teacher point at an existing Veyon config and bulk-add
        every computer to the ClassControl roster. Computers are added
        with their Veyon hostname + MAC (so Wake-on-LAN works) and
        grouped by Veyon's location/room hierarchy.
        """
        existing_hosts = {c.host for c in self.roster.computers}
        dlg = VeyonImportDialog(self, existing_hosts=existing_hosts)
        if dlg.exec() != VeyonImportDialog.DialogCode.Accepted:
            return
        new_computers = dlg.to_import()
        if not new_computers:
            return
        for c in new_computers:
            self.roster.add(c)
            self._add_thumbnail(c)
            self.hub.add_computer(c, auto_connect=True)
        self.roster.save()
        self._log_activity(
            f"imported {len(new_computers)} computer(s) from Veyon"
        )

    # ------------------------------------------------------------------
    # Running apps on a student machine
    # ------------------------------------------------------------------

    def _show_running_apps_for_selection(self) -> None:
        """Open the Running Apps dialog for the first selected (or, if
        nothing's selected, the first connected) computer."""
        cid = next(iter(self.selected_ids), None) if self.selected_ids else None
        if cid is None:
            for c_id in self.hub.computer_ids():
                st = self.hub.get_state(c_id)
                if st and st.state == "connected":
                    cid = c_id
                    break
        if cid is None:
            QMessageBox.information(
                self, "No client selected",
                "Select a connected student first, then try again."
            )
            return
        self._open_running_apps_dialog(cid)

    def _open_running_apps_dialog(self, cid: str) -> None:
        c = self.roster.get(cid)
        target_name = c.name if c else cid
        dlg = RunningAppsDialog(self, self.hub, cid, target_name)
        dlg.blockRequested.connect(self._add_to_block_list)
        dlg.show()  # non-modal so the teacher can keep interacting

    def _add_to_block_list(self, identifier: str) -> None:
        """Merge a new identifier into the persistent block list and push
        the updated effective list to every connected client. Triggered
        by RunningAppsDialog.blockRequested when the teacher clicks
        'Kill & Block'."""
        identifier = (identifier or "").strip()
        if not identifier:
            return
        existing = {k.lower(): k for k in self._app_states.keys()}
        if identifier.lower() in existing:
            # Already present — make sure it's enabled.
            actual_key = existing[identifier.lower()]
            self._app_states[actual_key] = True
            self._log_activity(f"re-enabled in block list: {actual_key}")
        else:
            self._app_states[identifier] = True
            self._log_activity(f"added to block list: {identifier}")
        # Make sure the master switch is on so this actually applies.
        self._apps_master = True
        self._save_blocking_state()
        self._url_block_failure_shown = True
        apps, _ = self._effective_blocked()
        for c_id in self.hub.computer_ids():
            st = self.hub.get_state(c_id)
            if st and st.state == "connected":
                self.hub.send(c_id, Op.SET_BLOCKED_APPS, {"apps": apps})

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        # TeacherToolBar is movable, floatable, dockable on all four edges,
        # and exposes a right-click menu for icon size + button style.
        tb = TeacherToolBar("Main", self)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        # Keep the embedded slider's orientation in sync with the toolbar.
        tb.orientationChanged.connect(self._on_toolbar_orientation)
        self._toolbar = tb

        def add(icon: QIcon, text: str, fn, tip: str = "") -> QAction:
            act = QAction(icon, text, self)
            act.triggered.connect(fn)
            if tip:
                act.setToolTip(tip)
            tb.addAction(act)
            return act

        # --- Roster CRUD -------------------------------------------------
        add(icons.add_computer(), "Add", self._add_computer,
            "Add a new student computer to the roster")
        add(icons.edit(), "Edit", self._edit_selected,
            "Edit the selected computer")
        add(icons.remove(), "Remove", self._remove_selected,
            "Remove the selected computers")
        tb.addSeparator()

        add(icons.reconnect(), "Reconnect", self._reconnect_selected,
            "Reconnect to the selected computers")
        tb.addSeparator()

        # --- Lock toggle (combined Lock + Unlock) -----------------------
        self.lock_action = QAction(icons.lock_off(), "Lock screens", self)
        self.lock_action.setCheckable(True)
        self.lock_action.setToolTip(
            "Lock / unlock student screens. Toggle on to freeze; toggle off to release."
        )
        self.lock_action.toggled.connect(self._toggle_lock)
        tb.addAction(self.lock_action)

        add(icons.message(), "Message", self._send_message,
            "Send a popup message to students")
        tb.addSeparator()

        # --- Demo toggle (start / stop) ---------------------------------
        self.demo_action = QAction(icons.demo_start(), "Start demo", self)
        self.demo_action.setCheckable(True)
        self.demo_action.setToolTip("Broadcast your screen to every connected student")
        self.demo_action.toggled.connect(self._toggle_demo)
        tb.addAction(self.demo_action)

        # Demo Pause / Resume — only sensible while the broadcast is on,
        # so it's disabled until the user starts the demo.
        self.demo_pause_action = QAction(icons.pause(), "Pause demo", self)
        self.demo_pause_action.setCheckable(True)
        self.demo_pause_action.setEnabled(False)
        self.demo_pause_action.setToolTip(
            "Pause the broadcast — students keep seeing the last frame"
        )
        self.demo_pause_action.toggled.connect(self._toggle_demo_pause)
        tb.addAction(self.demo_pause_action)
        tb.addSeparator()

        add(icons.launch(), "Open…", self._launch,
            "Open an app, file or URL on the selected computers")
        add(icons.send_file(), "Send file", self._send_file,
            "Push a file to the selected computers")
        add(icons.request_file(), "Get file", self._request_file,
            "Request a file from the selected computers")
        tb.addSeparator()

        add(icons.block(), "Block…", self._configure_blocking,
            "Configure blocked apps and websites")

        # --- Internet lockdown toggle -----------------------------------
        self.lockdown_action = QAction(icons.lockdown_off(), "Internet", self)
        self.lockdown_action.setCheckable(True)
        self.lockdown_action.setToolTip(
            "Toggle internet lockdown — only your teacher machine remains reachable."
        )
        self.lockdown_action.toggled.connect(self._toggle_lockdown)
        tb.addAction(self.lockdown_action)

        # --- Silence toggle ---------------------------------------------
        # Sticky audio mute on every connected student. The daemon runs a
        # 1 Hz watchdog so an attempt to un-mute is immediately reversed.
        self.silence_action = QAction(icons.sound_on(), "Silence", self)
        self.silence_action.setCheckable(True)
        self.silence_action.setChecked(self._audio_silenced)
        self.silence_action.setToolTip(
            "Mute every connected student and keep them muted — they "
            "physically can't un-mute themselves while this is on."
        )
        # Reflect the persisted state in the icon at startup too.
        if self._audio_silenced:
            self.silence_action.setIcon(icons.sound_off())
            self.silence_action.setText("Unsilence")
        self.silence_action.toggled.connect(self._toggle_silence)
        tb.addAction(self.silence_action)
        tb.addSeparator()

        # --- Power menu (button with dropdown) --------------------------
        power_menu = QMenu("Power", self)
        # Lock + Unlock at the top — they're the most common quick-actions
        # during a class.
        lock_p = QAction("Lock screens", self)
        lock_p.triggered.connect(
            lambda: self._send_targets(Op.LOCK,
                                      {"message": "Screen locked by teacher",
                                       "strict": True})
        )
        power_menu.addAction(lock_p)
        unlock_p = QAction("Unlock screens", self)
        unlock_p.triggered.connect(lambda: self._send_targets(Op.UNLOCK))
        power_menu.addAction(unlock_p)
        power_menu.addSeparator()
        # Wake (kept in the menu for discoverability as well as the
        # standalone toolbar button below).
        wake_act_m = QAction("Wake (WoL)", self)
        wake_act_m.triggered.connect(self._wake_targets)
        power_menu.addAction(wake_act_m)
        power_menu.addSeparator()
        for label, action in [
            ("Shutdown", "shutdown"),
            ("Restart", "restart"),
            ("Sleep", "sleep"),
            ("Log out user", "logout"),
        ]:
            a = QAction(label, self)
            a.triggered.connect(lambda _, x=action: self._power(x))
            power_menu.addAction(a)
        power_btn = QToolButton(self)
        power_btn.setIcon(icons.power())
        power_btn.setText("Power")
        power_btn.setMenu(power_menu)
        power_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        power_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        tb.addWidget(power_btn)

        # Standalone WoL toolbar action — common enough to deserve its
        # own button (you still get it in the Power dropdown too).
        add(icons.wake_on_lan(), "Wake", self._wake_targets,
            "Send a Wake-on-LAN magic packet to the selected machines")

        tb.addSeparator()

        # --- Selection helpers ------------------------------------------
        add(icons.select_all(), "Select all", self._select_all)
        add(icons.clear(), "Clear", self._clear_selection,
            "Clear the current selection")

        # --- Right- (or bottom-)aligned thumbnail size slider -----------
        spacer = QWidget()
        # Expanding on both axes so the slider stays at the trailing edge
        # whether the toolbar is horizontal or vertical.
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding,
        )
        tb.addWidget(spacer)

        tb.addAction(icons.zoom_out(), "")        # decorative small-icon label
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(160, 560)
        self.size_slider.setSingleStep(20)
        self.size_slider.setPageStep(40)
        self.size_slider.setValue(self._thumb_width)
        self.size_slider.setFixedWidth(180)
        self.size_slider.setToolTip("Resize the student-thumbnail grid")
        self.size_slider.valueChanged.connect(self._on_thumb_size_changed)
        tb.addWidget(self.size_slider)
        tb.addAction(icons.zoom_in(), "")

    # ------------------------------------------------------------------
    # Thumbnail grid management
    # ------------------------------------------------------------------

    def _add_thumbnail(self, c: Computer) -> None:
        thumb = Thumbnail(c.id, c.name, width=self._thumb_width)
        thumb.doubleClicked.connect(self._open_remote_control)
        thumb.selected.connect(self._on_thumb_selected)
        thumb.contextMenuRequested.connect(self._on_thumb_context_menu)
        self.thumbnails[c.id] = thumb
        # Honour any active filters / per-computer visibility right away,
        # so newly-added tiles don't flash on screen then disappear.
        thumb.setVisible(self._effective_visible(c.id))
        self._relayout()

    # ------------------------------------------------------------------
    # Visibility / filtering
    # ------------------------------------------------------------------

    def _on_hide_offline_toggled(self, on: bool) -> None:
        self._hide_offline = on
        self._apply_visibility()

    def _on_hide_no_session_toggled(self, on: bool) -> None:
        self._hide_no_session = on
        self._apply_visibility()

    def _show_all_computers(self) -> None:
        """Un-hide every computer in the roster + clear the two filter
        toggles. The 'Get me back to a normal view' button."""
        changed = False
        for c in self.roster.computers:
            if not c.visible:
                c.visible = True
                changed = True
        if changed:
            self.roster.save()
        self.act_hide_offline.setChecked(False)
        self.act_hide_no_session.setChecked(False)
        # _on_*_toggled already calls _apply_visibility; if neither was
        # checked we still need a refresh:
        self._apply_visibility()

    def _effective_visible(self, cid: str) -> bool:
        """Whether this computer's tile should be visible right now,
        combining the per-machine ``Computer.visible`` flag with the
        two View-menu filters."""
        c = self.roster.get(cid)
        if c is None or not c.visible:
            return False
        st = self.hub.get_state(cid)
        connected = bool(st and st.state == "connected")
        if self._hide_offline and not connected:
            return False
        if self._hide_no_session:
            info = self._client_info.get(cid) or (st.info if st else {})
            if not (info or {}).get("user"):
                return False
        return True

    def _apply_visibility(self) -> None:
        """Show/hide each tile and re-flow the grid."""
        for cid, thumb in self.thumbnails.items():
            thumb.setVisible(self._effective_visible(cid))
        self._relayout()

    def _hide_one(self, cid: str) -> None:
        c = self.roster.get(cid)
        if c is None or not c.visible:
            return
        c.visible = False
        self.roster.save()
        self._apply_visibility()
        self._log_activity(f"hid {c.name}")

    def _unhide_one(self, cid: str) -> None:
        c = self.roster.get(cid)
        if c is None or c.visible:
            return
        c.visible = True
        self.roster.save()
        self._apply_visibility()

    # ------------------------------------------------------------------
    # Thumbnail right-click menu
    # ------------------------------------------------------------------

    def _on_thumb_context_menu(self, cid: str, global_pos) -> None:
        """Per-tile right-click menu — quick access to the most common
        per-student actions without using the toolbar."""
        c = self.roster.get(cid)
        target = c.name if c else cid
        menu = QMenu(self)
        menu.addAction(f"Remote control – {target}",
                       lambda: self._open_remote_control(cid))
        menu.addAction("Show running apps…",
                       lambda: self._open_running_apps_dialog(cid))
        menu.addSeparator()
        menu.addAction("Lock screen",
                       lambda: self.hub.send_logged(
                           cid, Op.LOCK,
                           {"message": "Screen locked by teacher"}))
        menu.addAction("Unlock screen",
                       lambda: self.hub.send_logged(cid, Op.UNLOCK, {}))
        menu.addAction("Send message…",
                       lambda: (self.selected_ids.clear(),
                                self.selected_ids.add(cid),
                                self._send_message()))
        menu.addSeparator()
        menu.addAction("Reconnect",
                       lambda: (self.hub.disconnect(cid),
                                QTimer.singleShot(
                                    300, lambda: self.hub.connect(cid))))
        menu.addSeparator()
        menu.addAction(icons.visibility_off(), "Hide this computer",
                       lambda: self._hide_one(cid))
        menu.exec(global_pos)

    def _remove_thumbnail(self, computer_id: str) -> None:
        thumb = self.thumbnails.pop(computer_id, None)
        if thumb:
            thumb.setParent(None)
            thumb.deleteLater()
        self._relayout()

    def _relayout(self) -> None:
        # Clear current layout
        for i in reversed(range(self.grid_layout.count())):
            item = self.grid_layout.itemAt(i)
            self.grid_layout.removeItem(item)
        # Column count tracks the slider's chosen thumbnail width.
        col_step = max(140, self._thumb_width + 20)
        cols = max(1, self.grid_host.width() // col_step)
        for idx, (cid, thumb) in enumerate(self.thumbnails.items()):
            self.grid_layout.addWidget(thumb, idx // cols, idx % cols)

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        self._relayout()

    # ------------------------------------------------------------------
    # Roster CRUD
    # ------------------------------------------------------------------

    def _add_computer(self) -> None:
        dlg = dialogs.ComputerDialog(self, groups=self.roster.groups())
        if dlg.exec() != dialogs.ComputerDialog.DialogCode.Accepted:
            return
        c = dlg.computer()
        if not c.host:
            QMessageBox.warning(self, "Missing host", "Please provide a hostname or IP.")
            return
        self.roster.add(c)
        self.roster.save()
        self._add_thumbnail(c)
        self.hub.add_computer(c, auto_connect=True)

    def _edit_selected(self) -> None:
        if not self.selected_ids:
            return
        cid = next(iter(self.selected_ids))
        c = self.roster.get(cid)
        if not c:
            return
        dlg = dialogs.ComputerDialog(self, existing=c, groups=self.roster.groups())
        if dlg.exec() != dialogs.ComputerDialog.DialogCode.Accepted:
            return
        updated = dlg.computer()
        c.name = updated.name; c.host = updated.host; c.port = updated.port
        c.group = updated.group; c.mac = updated.mac; c.notes = updated.notes
        self.roster.save()
        thumb = self.thumbnails.get(cid)
        if thumb:
            thumb.set_label(c.name)
        # reconnect with the new host info
        self.hub.disconnect(cid)
        QTimer.singleShot(300, lambda: self.hub.connect(cid))

    def _remove_selected(self) -> None:
        if not self.selected_ids:
            return
        ok = QMessageBox.question(
            self, "Remove computers",
            f"Remove {len(self.selected_ids)} computer(s) from the roster?",
        ) == QMessageBox.StandardButton.Yes
        if not ok:
            return
        for cid in list(self.selected_ids):
            self.hub.remove_computer(cid)
            self.roster.remove(cid)
            self._remove_thumbnail(cid)
        self.selected_ids.clear()
        self.roster.save()

    def _reconnect_selected(self) -> None:
        for cid in self._target_ids():
            self.hub.disconnect(cid)
            QTimer.singleShot(300, lambda x=cid: self.hub.connect(x))

    def _select_all(self) -> None:
        for cid, t in self.thumbnails.items():
            t.set_selected(True)
            self.selected_ids.add(cid)

    def _clear_selection(self) -> None:
        for cid, t in self.thumbnails.items():
            t.set_selected(False)
        self.selected_ids.clear()

    # ------------------------------------------------------------------
    # Hub event handlers
    # ------------------------------------------------------------------

    def _on_state_changed(self, cid: str, state: str, info: dict) -> None:
        # Cache the latest info dict — used by the View → Hide no-session
        # filter, which needs to know whether anyone's logged in.
        if info:
            self._client_info[cid] = info
        thumb = self.thumbnails.get(cid)
        if not thumb:
            return
        thumb.set_state(state)
        c = self.roster.get(cid)
        if c and info.get("hostname"):
            thumb.set_label(f"{c.name} – {info.get('user', '?')}")
        # Re-evaluate filters: a tile may now hide / unhide as a result
        # of going from connected → disconnected etc.
        self._apply_visibility()
        if state == "connected":
            # Subscribe to a low-rate stream for thumbnails (honouring prefs).
            self.hub.send(cid, Op.START_STREAM, {
                "fps":       int(self._settings.get("thumb_fps", 1)),
                "max_width": int(self._settings.get("thumb_max_width", 320)),
                "quality":   int(self._settings.get("thumb_quality", 35)),
            })
            # Re-push persisted blocking state so reconnects / new joiners
            # are immediately consistent with what the teacher had set.
            # Suppress the modal "URL blocking failed" popup for THIS
            # silent re-push — the user didn't ask for it. The failure
            # will still show in the activity dock; the popup fires only
            # when they manually re-apply via the Block… dialog.
            apps, urls = self._effective_blocked()
            if apps or urls or self._app_states or self._url_states:
                self._url_block_failure_shown = True
                self.hub.send(cid, Op.SET_BLOCKED_APPS, {"apps": apps})
                self.hub.send(cid, Op.SET_BLOCKED_URLS, {"urls": urls})
            # Re-apply sticky audio silence so a fresh / reconnected
            # client honours the master switch immediately.
            if self._audio_silenced:
                self.hub.send(cid, Op.AUDIO, {"action": "lock"})
            self._log_activity(f"connected to {c.name if c else cid}")
        elif state == "error":
            self._log_activity(f"error on {c.name if c else cid}: {info.get('error', '')}")

    def _on_frame(self, cid: str, jpeg: bytes) -> None:
        thumb = self.thumbnails.get(cid)
        if thumb:
            thumb.set_frame(jpeg)

    def _on_message(self, cid: str, op: str, header: dict) -> None:
        # Remote-control windows handle their own frame intake. Generic
        # ACK / ERROR replies just go to the activity log.
        if op == Op.ERROR:
            self._log_activity(f"error from {cid}: {header.get('reason', '')}")
            return
        if op != Op.ACK:
            return

        ack_for = header.get("for", "")
        ok = header.get("ok", True)
        reason = header.get("reason", "") or ""
        c = self.roster.get(cid)
        target = c.name if c else cid

        # Lockdown ACKs deserve a visible popup on failure — silent
        # failure was the original UX bug.
        if ack_for in (Op.INTERNET_LOCKDOWN, Op.INTERNET_RELEASE):
            if not ok:
                self._log_activity(
                    f"{ack_for} FAILED on {target}: {reason or 'unknown'}"
                )
                if not self._lockdown_failure_shown:
                    self._lockdown_failure_shown = True
                    QTimer.singleShot(
                        0, lambda r=reason: self._show_privilege_failure(
                            "Internet lockdown", r),
                    )
            else:
                self._log_activity(f"{target}: {ack_for} ok")
            return

        # URL blocking via /etc/hosts also needs root; same UX pattern.
        if ack_for == Op.SET_BLOCKED_URLS:
            applied = header.get("applied") or []
            if not ok:
                self._log_activity(
                    f"URL block FAILED on {target}: {reason or 'unknown'}"
                )
                if not self._url_block_failure_shown:
                    self._url_block_failure_shown = True
                    QTimer.singleShot(
                        0, lambda r=reason: self._show_privilege_failure(
                            "URL blocking", r),
                    )
            else:
                self._log_activity(
                    f"{target}: URL block applied "
                    f"({len(applied)} host(s): {', '.join(applied) or '—'})"
                )
            return

        if ack_for:
            self._log_activity(f"{target}: ack {ack_for}")

    def _show_privilege_failure(self, feature: str, reason: str) -> None:
        """Generic 'this feature needs root on the student machine' popup.
        Used for internet lockdown and URL blocking (anything that touches
        /etc/pf.conf, /etc/pf.anchors or /etc/hosts)."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(f"{feature} failed")
        box.setText(
            f"{feature} could not be applied on the student machine."
        )
        box.setInformativeText(
            f"Reason: {reason or 'unknown error'}\n\n"
            "This feature needs Administrator privileges on the student "
            "machine. The simplest fix for testing is to restart the "
            "student daemon as root:"
        )
        box.setDetailedText(
            "Quit the running client (Ctrl-C in its terminal), then:\n\n"
            "  sudo ./scripts/run_client.sh\n\n"
            "or with the loopback helper:\n\n"
            "  ./scripts/dev_loopback.sh --sudo\n\n"
            "For permanent deployments see README → \"Privileged actions\":\n"
            "  • LaunchDaemon (recommended for fleets) - runs the client\n"
            "    as root automatically on boot.\n"
            "  • Or install packaging/classcontrol-sudoers (narrowly scoped\n"
            "    NOPASSWD sudo for just the binaries we need)."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _on_file(self, cid: str, name: str, data: bytes, header: dict) -> None:
        import zipfile
        c = self.roster.get(cid)
        sub = (c.name if c else cid).replace("/", "_")
        dest_dir = config.shared_files_dir("master") / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        dest.write_bytes(data)

        is_folder = bool(header.get("is_folder", False))
        file_count = int(header.get("file_count", 0))

        if is_folder:
            # Auto-extract the zip alongside it. The folder name is the
            # zip filename without the .zip suffix.
            extract_dir = dest_dir / (name[:-4] if name.endswith(".zip") else name + "_extracted")
            try:
                with zipfile.ZipFile(dest, "r") as zf:
                    zf.extractall(extract_dir)
                self._log_activity(
                    f"folder {name} ({file_count} file(s), {len(data)} B) "
                    f"received from {sub} → {extract_dir}"
                )
                reveal_path = extract_dir
            except Exception as exc:
                self._log_activity(
                    f"folder {name} extract FAILED ({exc}); saved zip at {dest}"
                )
                reveal_path = dest
        else:
            self._log_activity(
                f"file {name} ({len(data)} B) received from {sub} → {dest}"
            )
            reveal_path = dest

        # Honour the post-action the teacher picked when requesting.
        action = self._pending_pull_actions.pop(cid, "reveal")
        try:
            if action == "open":
                _local_p.launcher.open_target(str(reveal_path))
            elif action == "reveal":
                _local_p.launcher.reveal_target(str(reveal_path))
            # "none" or unknown → just leave it saved on disk
        except Exception as exc:
            self._log_activity(f"post-receive action {action!r} failed: {exc}")

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _on_thumb_selected(self, cid: str, sel: bool) -> None:
        if sel:
            self.selected_ids.add(cid)
        else:
            self.selected_ids.discard(cid)
        self.status.showMessage(f"{len(self.selected_ids)} selected")

    def _target_ids(self) -> list[str]:
        """If nothing is selected, target everyone. Otherwise just the selection."""
        return list(self.selected_ids) if self.selected_ids else list(self.thumbnails.keys())

    def _send_targets(self, op: str, data: dict | None = None,
                      payload: bytes = b"") -> None:
        ids = self._target_ids()
        for cid in ids:
            self.hub.send_logged(cid, op, data, payload)

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _open_remote_control(self, cid: str) -> None:
        if cid in self.remote_windows:
            self.remote_windows[cid].raise_()
            self.remote_windows[cid].activateWindow()
            return
        c = self.roster.get(cid)
        win = RemoteControlWindow(self.hub, cid, c.name if c else cid)
        win.destroyed.connect(lambda *_: self.remote_windows.pop(cid, None))
        self.remote_windows[cid] = win
        win.show()

    def _send_message(self) -> None:
        dlg = dialogs.MessageDialog(self)
        if dlg.exec() != dialogs.MessageDialog.DialogCode.Accepted:
            return
        self._send_targets(Op.MESSAGE, dlg.payload())

    def _toggle_silence(self, on: bool) -> None:
        """Sticky audio silence. The student's daemon runs a 1 Hz
        watchdog that re-mutes the output if anything un-mutes it,
        so this is a true silence — not just a one-shot mute."""
        self._audio_silenced = on
        self._settings["audio_silenced"] = on
        self._save_settings()
        # Update icon + label so the toolbar tells the truth at a glance.
        if on:
            self.silence_action.setIcon(icons.sound_off())
            self.silence_action.setText("Unsilence")
        else:
            self.silence_action.setIcon(icons.sound_on())
            self.silence_action.setText("Silence")
        action = "lock" if on else "unlock"
        for cid in self._target_ids():
            self.hub.send_logged(cid, Op.AUDIO, {"action": action})
        self._log_activity(
            f"audio {'silenced' if on else 'unsilenced'} "
            f"on {len(self._target_ids())} target(s)"
        )

    def _toggle_lock(self, on: bool) -> None:
        """Lock/Unlock as a single toggle action.

        For real student machines we send ``strict=True`` so the daemon
        enters kiosk mode (dock + menu bar hidden, Cmd-Tab disabled,
        Force-Quit blocked). For loopback (you testing on your own Mac)
        we send ``strict=False`` so you can still get to the master to
        unlock yourself.
        """
        if on:
            self.lock_action.setIcon(icons.lock_on())
            self.lock_action.setText("Unlock")
            for cid in self._target_ids():
                strict = not self._is_loopback_target(cid)
                self.hub.send_logged(cid, Op.LOCK, {
                    "message": "Screen locked by teacher",
                    "strict": strict,
                })
        else:
            self.lock_action.setIcon(icons.lock_off())
            self.lock_action.setText("Lock screens")
            self._send_targets(Op.UNLOCK)

    def _is_loopback_target(self, computer_id: str) -> bool:
        """True if the given computer's host is loopback (so we'd lock
        ourselves out by enabling kiosk mode)."""
        c = self.roster.get(computer_id)
        if not c:
            return False
        host = (c.host or "").strip().lower()
        return (
            host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")
            or host.startswith("127.")
        )

    def _toggle_demo(self, on: bool) -> None:
        if on:
            self.demo.start(windowed=False)
            self.demo_action.setIcon(icons.demo_stop())
            self.demo_action.setText("Stop demo")
            self.demo_pause_action.setEnabled(True)
            self._log_activity("demo mode started")
        else:
            self.demo.stop()
            self.demo_action.setIcon(icons.demo_start())
            self.demo_action.setText("Start demo")
            self.demo_pause_action.setEnabled(False)
            # Reset pause toggle visually without re-firing the slot.
            self.demo_pause_action.blockSignals(True)
            self.demo_pause_action.setChecked(False)
            self.demo_pause_action.setText("Pause demo")
            self.demo_pause_action.blockSignals(False)
            self._log_activity("demo mode stopped")

    def _start_demo_windowed(self) -> None:
        """Class menu → Start demo (windowed). Students see the demo in
        a normal resizable window instead of a fullscreen overlay."""
        self.demo.start(windowed=True)
        self.demo_action.blockSignals(True)
        self.demo_action.setChecked(True)
        self.demo_action.setIcon(icons.demo_stop())
        self.demo_action.setText("Stop demo (windowed)")
        self.demo_action.blockSignals(False)
        self.demo_pause_action.setEnabled(True)
        self._log_activity("demo mode started (windowed)")

    def _stop_demo_via_menu(self) -> None:
        if self.demo.is_active():
            self.demo_action.setChecked(False)   # fires _toggle_demo(False)

    def _toggle_demo_pause(self, paused: bool) -> None:
        if paused:
            self.demo.pause()
            self.demo_pause_action.setText("Resume demo")
            self._log_activity("demo paused (last frame frozen on students)")
        else:
            self.demo.resume()
            self.demo_pause_action.setText("Pause demo")
            self._log_activity("demo resumed")

    def _on_thumb_size_changed(self, value: int) -> None:
        """Slider callback: resize every thumbnail and re-layout the grid."""
        self._thumb_width = int(value)
        for thumb in self.thumbnails.values():
            thumb.set_size(self._thumb_width)
        self._relayout()

    def _on_toolbar_orientation(self, orientation) -> None:
        """Flip the embedded thumbnail-size slider when the toolbar docks
        on a side edge so it stays readable in either orientation."""
        if hasattr(self, "size_slider"):
            self.size_slider.setOrientation(orientation)
            if orientation == Qt.Orientation.Vertical:
                self.size_slider.setFixedHeight(180)
                self.size_slider.setFixedWidth(28)
            else:
                self.size_slider.setFixedWidth(180)
                self.size_slider.setFixedHeight(28)

    def _launch(self) -> None:
        dlg = dialogs.LaunchDialog(self)
        if dlg.exec() != dialogs.LaunchDialog.DialogCode.Accepted:
            return
        target = dlg.value()
        if target:
            self._send_targets(Op.LAUNCH, {"target": target})

    def _send_file(self) -> None:
        # Pre-fill the file picker with the last file the teacher sent.
        last = self._settings.get("last_send_path", "") or ""
        dlg = dialogs.SendFileDialog(self, initial_path=last)
        if dlg.exec() != dialogs.SendFileDialog.DialogCode.Accepted:
            return
        path = dlg.file_path()
        if not path:
            return
        try:
            data = Path(path).read_bytes()
        except Exception as exc:
            QMessageBox.warning(self, "Read error", str(exc))
            return
        name = Path(path).name
        post = dlg.post_action()
        # Remember for next time.
        self._settings["last_send_path"] = path
        self._save_settings()
        for cid in self._target_ids():
            self.hub.send_logged(
                cid, Op.FILE_PUSH,
                {"name": name, "size": len(data), "post_action": post},
                data,
            )

    def _request_file(self) -> None:
        last = self._settings.get("last_request_path", "") or ""
        dlg = dialogs.RequestFileDialog(self, initial_path=last)
        if dlg.exec() != dialogs.RequestFileDialog.DialogCode.Accepted:
            return
        path = dlg.value()
        if not path:
            return
        post = dlg.post_action()
        # Persist the path so it's pre-filled next time.
        self._settings["last_request_path"] = path
        self._save_settings()
        for cid in self._target_ids():
            self._pending_pull_actions[cid] = post
            self.hub.send_logged(cid, Op.FILE_PULL_REQUEST, {"path": path})

    def _configure_blocking(self) -> None:
        dlg = dialogs.BlockingDialog(
            self,
            apps_state=self._app_states,
            urls_state=self._url_states,
            apps_master=self._apps_master,
            urls_master=self._urls_master,
        )
        if dlg.exec() != dialogs.BlockingDialog.DialogCode.Accepted:
            return
        state = dlg.values_state()
        self._app_states  = state["apps"]
        self._url_states  = state["urls"]
        self._apps_master = bool(state["apps_master"])
        self._urls_master = bool(state["urls_master"])
        self._save_blocking_state()

        apps, urls = self._effective_blocked()
        self._log_activity(
            f"block list saved (apps: {len(self._app_states)} entries, "
            f"{len(apps)} active; urls: {len(self._url_states)} entries, "
            f"{len(urls)} active) → {self._blocking_state_path()}"
        )
        self._url_block_failure_shown = False
        for cid in self._target_ids():
            self.hub.send_logged(cid, Op.SET_BLOCKED_APPS, {"apps": apps})
            self.hub.send_logged(cid, Op.SET_BLOCKED_URLS, {"urls": urls})

    # ------------------------------------------------------------------
    # Persistent state: blocked apps + URLs
    # ------------------------------------------------------------------

    def _blocking_state_path(self) -> Path:
        return config.user_config_dir("master") / "blocking.json"

    def _load_blocking_state(self) -> tuple[dict[str, bool], dict[str, bool], bool, bool]:
        """Return ``(app_states, url_states, apps_master, urls_master)``.

        Handles two on-disk shapes for backwards compat with older
        installs:
          * Old (lists of strings):  ``{"apps": ["x", "y"], "urls": [...]}``
            → loaded with every entry enabled and master toggles ON.
          * New (per-item dicts):    ``{"apps": {"x": true, "y": false},
            "urls": {...}, "apps_master": bool, "urls_master": bool}``
        """
        path = self._blocking_state_path()
        if not path.exists():
            return {}, {}, True, True
        try:
            data = json.loads(path.read_text())
        except Exception:
            return {}, {}, True, True

        def _normalize_apps(raw) -> dict[str, bool]:
            if isinstance(raw, list):
                return {str(a).strip(): True for a in raw if a}
            if isinstance(raw, dict):
                return {str(k).strip(): bool(v) for k, v in raw.items() if k}
            return {}

        def _normalize_urls(raw) -> dict[str, bool]:
            out: dict[str, bool] = {}
            if isinstance(raw, list):
                for r in raw:
                    h = normalize_hostname(r)
                    if h:
                        out.setdefault(h, True)
            elif isinstance(raw, dict):
                for k, v in raw.items():
                    h = normalize_hostname(k)
                    if h:
                        out.setdefault(h, bool(v))
            return out

        return (
            _normalize_apps(data.get("apps", {})),
            _normalize_urls(data.get("urls", {})),
            bool(data.get("apps_master", True)),
            bool(data.get("urls_master", True)),
        )

    def _save_blocking_state(self) -> None:
        path = self._blocking_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Re-normalize URL keys defensively on save.
        cleaned_urls: dict[str, bool] = {}
        for raw, on in self._url_states.items():
            h = normalize_hostname(raw)
            if h:
                cleaned_urls[h] = bool(on)
        self._url_states = cleaned_urls
        path.write_text(json.dumps({
            "apps_master": bool(self._apps_master),
            "urls_master": bool(self._urls_master),
            "apps": self._app_states,
            "urls": self._url_states,
        }, indent=2))

    def _effective_blocked(self) -> tuple[list[str], list[str]]:
        """Compute the actual lists we push to clients: enabled entries
        when the master switch for that list is on, otherwise empty."""
        apps = (
            [k for k, on in self._app_states.items() if on]
            if self._apps_master else []
        )
        urls = (
            [k for k, on in self._url_states.items() if on]
            if self._urls_master else []
        )
        return apps, urls

    def _toggle_lockdown(self, on: bool) -> None:
        # Allow a fresh failure popup for this toggle action.
        self._lockdown_failure_shown = False
        if on:
            # Suggest the saved master IP from Preferences, or auto-detect.
            saved_ip = (self._settings.get("master_ip") or "").strip()
            try:
                my_ip = saved_ip or socket.gethostbyname(socket.gethostname())
            except Exception:
                my_ip = saved_ip
            dlg = dialogs.InternetLockdownDialog(self, suggested_master_ip=my_ip)
            if dlg.exec() != dialogs.InternetLockdownDialog.DialogCode.Accepted:
                self.lockdown_action.blockSignals(True)
                self.lockdown_action.setChecked(False)
                self.lockdown_action.blockSignals(False)
                return
            ips = dlg.master_ips()
            if not ips:
                ips = [my_ip] if my_ip else []
            for cid in self._target_ids():
                self.hub.send_logged(cid, Op.INTERNET_LOCKDOWN, {"master_ips": ips})
            self.lockdown_action.setIcon(icons.lockdown_on())
            self.lockdown_action.setText("Release")
        else:
            for cid in self._target_ids():
                self.hub.send_logged(cid, Op.INTERNET_RELEASE, {})
            self.lockdown_action.setIcon(icons.lockdown_off())
            self.lockdown_action.setText("Internet")

    def _wake_targets(self) -> None:
        """Send a Wake-on-LAN magic packet to every target's MAC address.

        Works on offline machines (the NIC interprets the packet directly).
        Skips computers with no MAC field set; warns if all targets
        lacked one. WoL must also be enabled in the target's BIOS and
        Windows power settings — see README.
        """
        ids = self._target_ids()
        sent, skipped = [], []
        for cid in ids:
            c = self.roster.get(cid)
            if not c:
                continue
            if not c.mac:
                skipped.append(c.name)
                continue
            if send_magic_packet(c.mac):
                sent.append(c.name)
                self._log_activity(f"WoL packet sent to {c.name} ({c.mac})")
            else:
                self._log_activity(
                    f"WoL packet FAILED for {c.name} (bad MAC?: {c.mac!r})"
                )
        if not sent and skipped:
            QMessageBox.information(
                self, "No MAC addresses",
                "None of the selected computers have a MAC address set.\n\n"
                "Edit each computer (Edit button) and fill in the MAC field, "
                "then try Wake again.\n\n"
                "Note: Wake-on-LAN also requires:\n"
                "  • WoL enabled in the target's BIOS / UEFI\n"
                "  • Windows: power option to allow waking the NIC\n"
                "  • Same broadcast domain (no routers in between)"
            )
        elif sent:
            self._log_activity(
                f"Wake-on-LAN sent to {len(sent)} machine(s); "
                f"{len(skipped)} skipped (no MAC)."
            )

    def _power(self, action: str) -> None:
        confirm = QMessageBox.question(
            self, f"Confirm {action}",
            f"Send {action} to {len(self._target_ids())} computer(s)?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        for cid in self._target_ids():
            self.hub.send_logged(cid, Op.POWER, {"action": action})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log_activity(self, text: str) -> None:
        item = QListWidgetItem(text)
        self.activity_list.addItem(item)
        self.activity_list.scrollToBottom()
        # Keep the list bounded
        while self.activity_list.count() > 400:
            self.activity_list.takeItem(0)

    def _ensure_thumb_streams(self) -> None:
        for cid in self.thumbnails:
            st = self.hub.get_state(cid)
            if st and st.state == "connected":
                # Idempotent: client just restarts at the requested fps.
                self.hub.send(cid, Op.START_STREAM, {
                    "fps":       int(self._settings.get("thumb_fps", 1)),
                    "max_width": int(self._settings.get("thumb_max_width", 320)),
                    "quality":   int(self._settings.get("thumb_quality", 35)),
                })

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def closeEvent(self, ev):  # noqa: N802
        try:
            self.demo.stop()
        except Exception:
            pass
        self.hub.shutdown()
        super().closeEvent(ev)
