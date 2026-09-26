"""
SpeechStudio — Top Navigation Bar
==================================

Custom QWidget-based top navigation bar that reproduces the HTML
reference's first 56px navigation row.

Layout (left to right):
    [graphic_eq] SpeechStudio    File  Edit  View  Project  Help    [undo] [redo] | [save] [export] [GENERATE]

Architecture:
    - Custom QWidget with QHBoxLayout
    - Branding area (icon + text label)
    - Navigation buttons (File, Edit, View, Project, Help) — each opens a QMenu
    - Action area (Undo, Redo, separator, Save, Export, Generate CTA)
    - The QMenu popups use the existing MenuBar actions (reorganized)

The existing MenuBar class still creates all QAction objects and their
shortcuts. The TopNavigation widget reorganizes them into 5 menus:
    File: New/Open/Save Project, Scene, Import/Export Voice/Preset, Exit
    Edit: Undo, Redo, Clear, (generation actions: Generate, Stop, Replay)
    View: Theme, Reset Layout, Fullscreen, Show Prompt Blocks
    Project: Batch Generation, Voice Library, History, Open Output Folder
    Help: Documentation, Token Guide, About, Developer Mode, Settings, Benchmark

The QMenuBar is hidden (native menu bar not shown). The TopNavigation
provides the visible navigation instead.
"""

from __future__ import annotations
import os
from typing import Callable, Optional

from PySide6.QtCore import Qt, QSize, QPoint
from PySide6.QtGui import QAction, QKeySequence, QFont, QPainter, QColor, QPixmap
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QToolButton, QLabel,
    QMenu, QSizePolicy, QFrame,
)

from ui.typography import Typography, IconSize, IconColor
from ui.icon_registry import IconRegistry

# ---------------------------------------------------------------------------
# P3.38: official GUINEO brand wordmark (white on transparent).
# ---------------------------------------------------------------------------
# Resolve the application root from this file's location:
#   ui/panels/top_navigation.py  ->  ui/panels/  ->  ui/  ->  <app_root>
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Pre-trimmed display source (transparent margins removed; derived from
#: the official GUINEO_LOGO_TEXT_nobg_720_240P.png asset).
BRAND_LOGO_PATH = os.path.join(_APP_ROOT, "assets", "brand", "guineo_wordmark.png")

#: Display height of the wordmark in logical (device-independent) pixels.
#: The old brand slot was a 32px icon + headline text; a 26px wordmark
#: matches that optical weight on the 56px bar.
BRAND_LOGO_HEIGHT = 26

#: The pixmap is rendered at 2x and marked with this device pixel ratio,
#: so the wordmark stays crisp on HiDPI (150%/200% Windows scaling).
BRAND_LOGO_RENDER_DPR = 2.0



class NavButton(QToolButton):
    """A flat navigation button with popup menu for the top nav.

    P3.20: Changed from QPushButton to QToolButton with InstantPopup mode.
    This is the Qt-native way to do popup menus from a button — it handles
    the popup lifecycle, mouse grabbing, and focus correctly. The previous
    QPushButton + clicked + menu.popup() approach failed on real displays
    because the button's checked state interfered with the popup.

    Normal state: transparent bg, text-secondary color.
    Hover: text changes to primary (accent), subtle transition.
    Active (menu open): accent color, 2px bottom border.
    """

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(48)
        self.setFont(Typography.nav_item_active())
        self.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 0;
                color: #A0A0A0;
                padding: 10px 16px;
                font-size: 14px;
            }
            QToolButton:hover {
                color: #d0bcff;
            }
            QToolButton:pressed,
            QToolButton:checked,
            QToolButton:on {
                color: #d0bcff;
                border-bottom: 2px solid #d0bcff;
            }
            QToolButton::menu-indicator {
                image: none;
            }
        """)



class IconButton(QToolButton):
    """A compact icon-only button for Undo, Redo, Save, Export.

    Button size increased to 36x36 and icon size to IconSize.LG (22px)
    for better visibility and touch targets. Icons are rendered from
    SVG vectors so they stay crisp at any size.
    """

    def __init__(self, tooltip: str, parent=None):
        super().__init__(parent)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 40)
        self.setIconSize(QSize(IconSize.LG, IconSize.LG))
        self.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 6px;
                color: #A0A0A0;
            }
            QToolButton:hover {
                color: #d0bcff;
                border-color: #333333;
                background-color: #2E2E2E;
            }
            QToolButton:pressed {
                background-color: #242424;
            }
        """)


class TopNavigation(QWidget):
    """Custom top navigation bar (56px high).

    Replaces the native QMenuBar and QToolBar with a single unified
    navigation bar that matches the HTML reference.

    The existing MenuBar class still creates all QAction objects;
    this widget reorganizes them into 5 popup menus and provides
    visible action buttons (Undo, Redo, Save, Export, Generate).
    """

    def __init__(self, menu_bar, toolbar, parent=None):
        """
        Args:
            menu_bar: The existing MenuBar instance (provides QActions).
            toolbar: The existing Toolbar instance (provides Generate button).
            parent: Parent widget.
        """
        super().__init__(parent)
        self._menu_bar = menu_bar
        self._toolbar = toolbar
        self.setFixedHeight(56)
        # CRITICAL: WA_StyledBackground makes QWidget paint its own stylesheet
        # background. Without this, the stylesheet background-color is ignored
        # and the widget appears transparent — the ambient bleeds through.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#TopNavigation {
                background-color: #1A1A1A;
                border-bottom: 1px solid #333333;
            }
        """)
        self.setObjectName("TopNavigation")
        self._build_ui()

    # ------------------------------------------------------------------
    # P3.38: brand wordmark
    # ------------------------------------------------------------------
    def _apply_brand_logo(self) -> None:
        """Show the official GUINEO wordmark in the brand slot.

        The asset is rendered at 2x and tagged with a device pixel ratio
        so it stays crisp on HiDPI displays. Every failure mode (missing
        file, undecodable image) falls back to the pre-P3.38 text brand
        ("GUINEO" in the accent colour) — the bar is never left empty.
        """
        pm = QPixmap(BRAND_LOGO_PATH)
        if pm.isNull():
            self._brand_label.setText("GUINEO")
            self._brand_label.setFont(Typography.headline_md())
            self._brand_label.setStyleSheet("color: #d0bcff;")
            return
        render_h = int(round(BRAND_LOGO_HEIGHT * BRAND_LOGO_RENDER_DPR))
        render_w = int(round(pm.width() * render_h / pm.height()))
        scaled = pm.scaled(
            render_w, render_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(BRAND_LOGO_RENDER_DPR)
        self._brand_label.setPixmap(scaled)

    def brand_logo_label(self) -> QLabel:
        """The brand wordmark label (exposed for tests)."""
        return self._brand_label

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(0)

        # ---- LEFT ZONE: Branding ----
        # P3.38: the official GUINEO wordmark (white on transparent)
        # replaces the former graphic_eq icon + text label. The top
        # navigation keeps its fixed dark #1A1A1A surface in EVERY
        # theme (the widget stylesheet wins over the app theme), so
        # the white logo is the correct variant in all five themes.
        brand_widget = QWidget()
        brand_layout = QHBoxLayout(brand_widget)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(8)

        self._brand_label = QLabel()
        self._brand_label.setObjectName("GuineoBrandLogo")
        self._brand_label.setToolTip("GUINEO")
        self._brand_label.setAccessibleName("GUINEO")
        self._apply_brand_logo()
        brand_layout.addWidget(self._brand_label)

        layout.addWidget(brand_widget)

        # ---- CENTER ZONE: Navigation menus (genuinely centered) ----
        layout.addStretch(1)

        nav_widget = QWidget()
        nav_layout = QHBoxLayout(nav_widget)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(24)

        self._nav_buttons = {}
        for label in ["File", "Edit", "View", "Project", "Help"]:
            btn = NavButton(label)
            nav_layout.addWidget(btn)
            self._nav_buttons[label] = btn

        layout.addWidget(nav_widget)

        layout.addStretch(1)

        # ---- RIGHT ZONE: Action buttons ----
        action_widget = QWidget()
        action_layout = QHBoxLayout(action_widget)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)

        # Undo
        self._undo_btn = IconButton("Undo")
        undo_icon = IconRegistry.icon("undo", size=IconSize.LG, color=IconColor.DEFAULT)
        if undo_icon:
            self._undo_btn.setIcon(undo_icon)
        else:
            self._undo_btn.setText("U")
        action_layout.addWidget(self._undo_btn)

        # Redo
        self._redo_btn = IconButton("Redo")
        redo_icon = IconRegistry.icon("redo", size=IconSize.LG, color=IconColor.DEFAULT)
        if redo_icon:
            self._redo_btn.setIcon(redo_icon)
        else:
            self._redo_btn.setText("R")
        action_layout.addWidget(self._redo_btn)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #333333;")
        sep.setFixedWidth(1)
        sep.setFixedHeight(20)
        action_layout.addWidget(sep)

        # Save
        self._save_btn = IconButton("Save Project (Ctrl+S)")
        save_icon = IconRegistry.icon("save", size=IconSize.LG, color=IconColor.DEFAULT)
        if save_icon:
            self._save_btn.setIcon(save_icon)
        else:
            self._save_btn.setText("S")
        action_layout.addWidget(self._save_btn)

        # Export
        # P3.25 (audit SS-M12): the button opens the VOICE PROFILE export
        # (export_voice action), not an audio export — the old "Export
        # Audio" label misdescribed the action.
        self._export_btn = IconButton("Export Voice")
        export_icon = IconRegistry.icon("upload", size=IconSize.LG, color=IconColor.DEFAULT)
        if export_icon:
            self._export_btn.setIcon(export_icon)
        else:
            self._export_btn.setText("E")
        action_layout.addWidget(self._export_btn)

        # Generate — large orange gradient CTA
        self._generate_btn = QPushButton()
        gen_icon = IconRegistry.icon("play_arrow", size=IconSize.LG, color=IconColor.WHITE)
        if gen_icon:
            self._generate_btn.setIcon(gen_icon)
            self._generate_btn.setIconSize(QSize(IconSize.LG, IconSize.LG))
        self._generate_btn.setText("  Generate")
        self._generate_btn.setToolTip("Generate speech (Ctrl+Enter)")
        self._generate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Use Typography token instead of hardcoded font-size.
        self._generate_btn.setFont(Typography.cta_main())
        self._generate_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #F97316, stop:1 #EA580C);
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 8px 20px;
            }
            QPushButton:hover {
                opacity: 0.9;
            }
            QPushButton:pressed {
                opacity: 0.8;
            }
            QPushButton:disabled {
                background-color: #2E2E2E;
                color: #555555;
            }
        """)
        action_layout.addWidget(self._generate_btn)

        layout.addWidget(action_widget)

        # Setup popup menus for each nav button
        self._setup_menus()

    def _setup_menus(self) -> None:
        """Reorganize existing MenuBar actions into 5 popup menus.

        The menus are attached to the NavButton widgets. Clicking a nav
        button opens its popup menu.

        Menu structure (matching HTML reference: File/Edit/View/Project/Help):
            File: New/Open/Save Project, Scene save/load, Import/Export Voice/Preset, Exit
            Edit: Generate, Stop, Replay, Clear, Generate Long
            View: Theme, Reset Layout, Fullscreen, Show Prompt Blocks
            Project: Batch Generation, Voice Library, History, Open Output Folder
            Help: Documentation, Token Guide, About, Settings, Benchmark, Developer Mode
        """
        mb = self._menu_bar

        # ---- File menu ----
        file_menu = QMenu(self)
        file_menu.addAction(mb.get_action("new_project"))
        file_menu.addAction(mb.get_action("new_scene"))
        file_menu.addAction(mb.get_action("open_project"))
        file_menu.addAction(mb.get_action("save_project"))
        # P3.7: Export Project action in the File menu
        file_menu.addAction(mb.get_action("export_project"))
        file_menu.addSeparator()
        file_menu.addAction(mb.get_action("save_scene"))
        file_menu.addAction(mb.get_action("load_scene"))
        file_menu.addSeparator()
        file_menu.addAction(mb.get_action("import_voice"))
        file_menu.addAction(mb.get_action("export_voice"))
        file_menu.addAction(mb.get_action("import_preset"))
        file_menu.addAction(mb.get_action("export_preset"))
        file_menu.addSeparator()
        file_menu.addAction(mb.get_action("exit"))
        self._nav_buttons["File"].setMenu(file_menu)

        # ---- Edit menu (generation actions moved here) ----
        edit_menu = QMenu(self)
        edit_menu.addAction(mb.get_action("generate"))
        edit_menu.addAction(mb.get_action("generate_long"))
        edit_menu.addAction(mb.get_action("stop"))
        edit_menu.addAction(mb.get_action("replay"))
        edit_menu.addAction(mb.get_action("clear"))
        self._nav_buttons["Edit"].setMenu(edit_menu)

        # ---- View menu ----
        view_menu = QMenu(self)
        # "theme" is now a QMenu (submenu), not a QAction — use addMenu()
        theme_menu = mb.get_action("theme")
        if isinstance(theme_menu, QMenu):
            view_menu.addMenu(theme_menu)
        else:
            view_menu.addAction(theme_menu)
        view_menu.addAction(mb.get_action("reset_layout"))
        view_menu.addAction(mb.get_action("fullscreen"))
        view_menu.addSeparator()
        view_menu.addAction(mb.get_action("show_prompt_blocks"))
        self._nav_buttons["View"].setMenu(view_menu)

        # ---- Project menu (tools actions moved here) ----
        project_menu = QMenu(self)
        project_menu.addAction(mb.get_action("batch_generation"))
        project_menu.addAction(mb.get_action("voice_library"))
        project_menu.addAction(mb.get_action("history"))
        # P3.6: Preset Manager now in the Project menu (replaces the old
        # sidebar LIBRARY "Presets" entry that was removed).
        project_menu.addAction(mb.get_action("preset_manager"))
        project_menu.addSeparator()
        project_menu.addAction(mb.get_action("open_outputs"))
        self._nav_buttons["Project"].setMenu(project_menu)

        # ---- Help menu (settings, benchmark moved here) ----
        help_menu = QMenu(self)
        help_menu.addAction(mb.get_action("documentation"))
        help_menu.addAction(mb.get_action("token_guide"))
        help_menu.addAction(mb.get_action("supported_languages"))
        help_menu.addAction(mb.get_action("copy_debug"))
        # P3.25 (audit SS-M11): Font Status was only in the hidden native
        # menubar — unreachable from the visible TopNav Help menu. It is
        # now reachable like every other help action.
        help_menu.addAction(mb.get_action("font_status"))
        help_menu.addAction(mb.get_action("about"))
        help_menu.addSeparator()
        help_menu.addAction(mb.get_action("settings"))
        help_menu.addAction(mb.get_action("benchmark"))
        # P3.20: Developer Mode removed from Help menu.
        self._nav_buttons["Help"].setMenu(help_menu)

    def _show_menu(self, menu: QMenu, nav_name: str) -> None:
        """Show a popup menu below the clicked nav button.

        P3.10 FIX: Use menu.popup() (non-blocking) instead of menu.exec()
        (blocking). The blocking exec() caused issues in Full Screen mode
        where the menu wouldn't appear because the window focus was
        captured by the modal event loop. popup() schedules the menu to
        appear and returns immediately, letting Qt handle the focus
        naturally.

        Also: uncheck the button when the menu hides (previously the
        checkable NavButton stayed checked after the menu closed).
        """
        btn = self._nav_buttons.get(nav_name)
        if btn is None:
            return
        # Update checked state — only the clicked button is checked
        for name, b in self._nav_buttons.items():
            b.setChecked(name == nav_name)
        # Position menu below the button
        pos = btn.mapToGlobal(QPoint(0, btn.height()))
        # P3.10: Uncheck the button when the menu hides
        menu.aboutToHide.connect(lambda: btn.setChecked(False))
        # P3.10: Use popup() (non-blocking) instead of exec() (blocking)
        menu.popup(pos)

    # ------------------------------------------------------------------
    # Connection API (compatible with existing Toolbar API)
    # ------------------------------------------------------------------
    def connect_generate(self, callback: Callable) -> None:
        """Connect the Generate button click to a callback."""
        self._generate_btn.clicked.connect(callback)

    def connect_save(self, callback: Callable) -> None:
        """Connect the Save button click to a callback."""
        self._save_btn.clicked.connect(callback)

    def connect_export(self, callback: Callable) -> None:
        """Connect the Export button click to a callback."""
        self._export_btn.clicked.connect(callback)

    def connect_undo(self, callback: Callable) -> None:
        """Connect the Undo button click to a callback."""
        self._undo_btn.clicked.connect(callback)

    def connect_redo(self, callback: Callable) -> None:
        """Connect the Redo button click to a callback."""
        self._redo_btn.clicked.connect(callback)

    def set_generate_enabled(self, enabled: bool) -> None:
        """Enable/disable the Generate button."""
        self._generate_btn.setEnabled(enabled)

    def refresh_theme(self) -> None:
        """Re-apply styles after a theme change (no-op — styles are hardcoded)."""
        pass

    # Stubs for compatibility with existing Toolbar API
    def set_stop_enabled(self, enabled: bool) -> None:
        """Stop is now in the Edit menu only — no visible button."""
        action = self._menu_bar.get_action("stop")
        if action:
            action.setEnabled(enabled)
