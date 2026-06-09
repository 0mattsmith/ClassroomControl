"""
Detachable, resizable, all-edge-dockable toolbar for the teacher window.

Built-in Qt features used:

* ``setMovable(True)``  — drag handle appears at the start of the toolbar.
* ``setFloatable(True)`` — drag the handle out of the dock to float it.
  While floating, the toolbar is a top-level window with resize borders.
* ``setAllowedAreas(Qt.ToolBarArea.AllToolBarAreas)`` — drop it on the
  top, bottom, left or right edge of the main window.

Added on top of those:

* A **right-click context menu** with:
    - Icon size presets (16 / 24 / 32 / 48 px),
    - Button style (text below / beside / icon-only / text-only),
    - Dock area picker (top / bottom / left / right),
    - Float toolbar (toggle that works in both directions),
    - **Wrap to multiple rows** (toggle: items flow onto new rows when
      horizontal space runs out, instead of disappearing into the
      overflow chevron).
* ``closeEvent`` re-docks the toolbar when the user clicks the close
  button on the floating window, so they're never trapped without a
  toolbar.
* ``orientationChanged`` is the standard QToolBar signal — the main
  window listens for it to flip the embedded slider's orientation.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QAction, QActionGroup, QContextMenuEvent, QCloseEvent
from PyQt6.QtWidgets import (
    QFrame, QMainWindow, QMenu, QToolBar, QToolButton, QWidget, QWidgetAction,
)

from master.ui.flow_layout import FlowLayout


class TeacherToolBar(QToolBar):
    """A floatable, all-edge-dockable, right-click-configurable toolbar
    with an optional multi-row wrap mode."""

    ICON_PRESETS = [16, 24, 32, 48]
    STYLE_PRESETS = [
        ("Text below icon",  Qt.ToolButtonStyle.ToolButtonTextUnderIcon),
        ("Text beside icon", Qt.ToolButtonStyle.ToolButtonTextBesideIcon),
        ("Icon only",        Qt.ToolButtonStyle.ToolButtonIconOnly),
        ("Text only",        Qt.ToolButtonStyle.ToolButtonTextOnly),
    ]
    DOCK_PRESETS = [
        ("Dock to top",    Qt.ToolBarArea.TopToolBarArea),
        ("Dock to bottom", Qt.ToolBarArea.BottomToolBarArea),
        ("Dock to left",   Qt.ToolBarArea.LeftToolBarArea),
        ("Dock to right",  Qt.ToolBarArea.RightToolBarArea),
    ]

    def __init__(self, title: str = "Main", parent: QMainWindow | None = None):
        # ⚠ Initialise wrap-mode state BEFORE anything that might call our
        # overridden ``setIconSize`` / ``setToolButtonStyle`` (those overrides
        # read ``self._flow_widget``). ``QToolBar.__init__`` itself may also
        # trigger style/icon-size resolution, so the attribute must exist
        # before the super() call.
        self._wrap_mode: bool = False
        self._flow_widget: QWidget | None = None
        self._flow_action: QAction | None = None
        self._stolen_widgets: dict[QWidgetAction, QWidget] = {}

        super().__init__(title, parent)

        # Remember our main window even when we detach (setParent(None)
        # for floating clears self.parent(), so a walk-the-parents lookup
        # would otherwise return None and break the unfloat path).
        self._main_window_ref: QMainWindow | None = (
            parent if isinstance(parent, QMainWindow) else None
        )

        # Detach / float / drop anywhere.
        self.setMovable(True)
        self.setFloatable(True)
        self.setAllowedAreas(Qt.ToolBarArea.AllToolBarAreas)
        # Sensible defaults; user can change via the right-click menu.
        self.setIconSize(QSize(28, 28))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)

    # ------------------------------------------------------------------
    # Right-click context menu
    # ------------------------------------------------------------------

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        menu = QMenu(self)

        # --- Icon size submenu ---
        size_menu = menu.addMenu("Icon size")
        size_group = QActionGroup(self)
        size_group.setExclusive(True)
        current_px = self.iconSize().width()
        for px in self.ICON_PRESETS:
            act = QAction(f"{px} px", self)
            act.setCheckable(True)
            act.setChecked(px == current_px)
            act.triggered.connect(self._make_size_setter(px))
            size_group.addAction(act)
            size_menu.addAction(act)

        # --- Button style submenu ---
        style_menu = menu.addMenu("Button style")
        style_group = QActionGroup(self)
        style_group.setExclusive(True)
        current_style = self.toolButtonStyle()
        for label, style in self.STYLE_PRESETS:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(style == current_style)
            act.triggered.connect(self._make_style_setter(style))
            style_group.addAction(act)
            style_menu.addAction(act)

        menu.addSeparator()

        # --- Wrap toggle ---
        wrap_act = QAction("Wrap to multiple rows", self)
        wrap_act.setCheckable(True)
        wrap_act.setChecked(self._wrap_mode)
        wrap_act.setToolTip(
            "When on, toolbar items flow onto a new row instead of being "
            "hidden behind the overflow chevron when space runs out."
        )
        wrap_act.toggled.connect(self.set_wrap_mode)
        menu.addAction(wrap_act)

        menu.addSeparator()

        # --- Dock area picker ---
        for label, area in self.DOCK_PRESETS:
            act = QAction(label, self)
            act.triggered.connect(self._make_dock_setter(area))
            menu.addAction(act)

        # --- Float / unfloat ---
        float_act = QAction("Float toolbar", self)
        float_act.setCheckable(True)
        float_act.setChecked(self._is_currently_floating())
        float_act.triggered.connect(self._toggle_float)
        menu.addAction(float_act)

        menu.exec(event.globalPos())

    # ------------------------------------------------------------------
    # Float / dock
    # ------------------------------------------------------------------

    def _is_currently_floating(self) -> bool:
        """Reliable check for floating regardless of how we got there.

        QToolBar.isFloating() only returns True when Qt itself promoted
        the toolbar to a floating window via user drag; our programmatic
        ``setParent(None)`` path doesn't flip that flag, so we check for
        the absence of a main-window parent instead.
        """
        if self.isFloating():
            return True
        return self.parent() is None

    def _toggle_float(self, checked: bool) -> None:
        mw = self._main_window()
        if mw is None:
            return
        if checked and not self._is_currently_floating():
            # Detach and present as a top-level Tool window.
            mw.removeToolBar(self)
            self.setParent(None)
            self.setWindowFlags(Qt.WindowType.Tool)
            self.show()
        elif not checked:
            # Re-dock — works whether we were floating via drag OR via
            # our programmatic setParent(None) above.
            mw.addToolBar(Qt.ToolBarArea.TopToolBarArea, self)
            self.show()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Re-dock instead of hiding when the user clicks the X on a
        floating toolbar. Otherwise they'd be stuck with no toolbar
        and no way to bring it back."""
        if self._is_currently_floating():
            mw = self._main_window()
            if mw is not None:
                mw.addToolBar(Qt.ToolBarArea.TopToolBarArea, self)
                self.show()
                event.ignore()
                return
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Wrap mode
    # ------------------------------------------------------------------

    def set_wrap_mode(self, enabled: bool) -> None:
        if enabled == self._wrap_mode:
            return
        self._wrap_mode = enabled
        if enabled:
            self._enter_wrap_mode()
        else:
            self._exit_wrap_mode()

    def is_wrap_mode(self) -> bool:
        return self._wrap_mode

    def _enter_wrap_mode(self) -> None:
        # Snapshot every action currently on the toolbar (including the
        # QWidgetActions that wrap our slider + spacers + icon labels).
        original = list(self.actions())

        # Hide them so the toolbar shows nothing of them while wrap mode
        # is active. We don't remove them — we want to be able to restore
        # the exact original ordering on exit.
        for act in original:
            act.setVisible(False)

        # Build a single flow container and add it as the toolbar's only
        # visible widget. The container holds one QToolButton per action,
        # or the widget itself for QWidgetActions.
        self._flow_widget = QWidget(self)
        flow = FlowLayout(self._flow_widget, margin=2, hSpacing=4, vSpacing=4)

        for act in original:
            if act.isSeparator():
                sep = QFrame(self._flow_widget)
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setFixedHeight(max(20, self.iconSize().height()))
                flow.addWidget(sep)
                continue

            if isinstance(act, QWidgetAction):
                # Steal the embedded widget into the flow.
                w = act.defaultWidget()
                if w is not None:
                    self._stolen_widgets[act] = w
                    w.setParent(self._flow_widget)
                    flow.addWidget(w)
                continue

            btn = QToolButton(self._flow_widget)
            btn.setDefaultAction(act)
            btn.setToolButtonStyle(self.toolButtonStyle())
            btn.setIconSize(self.iconSize())
            flow.addWidget(btn)

        self._flow_action = self.addWidget(self._flow_widget)

    def _exit_wrap_mode(self) -> None:
        # Remove the flow container.
        if self._flow_action is not None:
            self.removeAction(self._flow_action)
            self._flow_action = None
        # Hand widgets back to their QWidgetActions.
        for act, w in self._stolen_widgets.items():
            act.setDefaultWidget(w)
        self._stolen_widgets.clear()
        if self._flow_widget is not None:
            self._flow_widget.deleteLater()
            self._flow_widget = None
        # Un-hide the original actions.
        for act in self.actions():
            act.setVisible(True)

    # ------------------------------------------------------------------
    # Propagate icon-size / button-style changes to flow buttons too
    # ------------------------------------------------------------------

    def setIconSize(self, size: QSize) -> None:  # noqa: N802
        super().setIconSize(size)
        # getattr guard: Qt may call setIconSize during super().__init__,
        # i.e. before our __init__ body has set the wrap-mode attrs.
        flow = getattr(self, "_flow_widget", None)
        if flow is not None:
            for btn in flow.findChildren(QToolButton):
                btn.setIconSize(size)

    def setToolButtonStyle(self, style: Qt.ToolButtonStyle) -> None:  # noqa: N802
        super().setToolButtonStyle(style)
        flow = getattr(self, "_flow_widget", None)
        if flow is not None:
            for btn in flow.findChildren(QToolButton):
                btn.setToolButtonStyle(style)

    # ------------------------------------------------------------------
    # Action factories
    # ------------------------------------------------------------------

    def _make_size_setter(self, px: int) -> Callable[[bool], None]:
        def setter(_checked: bool = False) -> None:
            self.setIconSize(QSize(px, px))
        return setter

    def _make_style_setter(self, style: Qt.ToolButtonStyle) -> Callable[[bool], None]:
        def setter(_checked: bool = False) -> None:
            self.setToolButtonStyle(style)
        return setter

    def _make_dock_setter(self, area: Qt.ToolBarArea) -> Callable[[bool], None]:
        def setter(_checked: bool = False) -> None:
            mw = self._main_window()
            if mw is not None:
                mw.addToolBar(area, self)
                self.setVisible(True)
        return setter

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _main_window(self) -> QMainWindow | None:
        if self._main_window_ref is not None:
            return self._main_window_ref
        widget = self.parent()
        while widget is not None and not isinstance(widget, QMainWindow):
            widget = widget.parent()
        return widget if isinstance(widget, QMainWindow) else None
