"""
SpeechStudio — Project & Scene Context Bar
==========================================

Custom QWidget-based context bar (56px high) that reproduces the HTML
reference's second navigation row.

Layout (left to right):
    [folder] PROJECTS › Project Name › Scene Name [edit]    ● Changes Saved

Architecture:
    - Custom QWidget with QHBoxLayout
    - Breadcrumb area (folder icon, PROJECTS label, chevron, project name,
      chevron, scene name, edit icon)
    - Save state indicator (green dot + text)
    - Signals for new_scene_requested, edit_scene_requested

The MainWindow is the controller — this widget just displays state
and emits signals. It does NOT interact with the Engine directly.
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor, QIcon, QAction
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QToolButton, QFrame,
    QSizePolicy, QMenu,
)

from ui.typography import Typography, IconSize, IconColor
from ui.icon_registry import IconRegistry


class ProjectSceneBar(QWidget):
    """Project and Scene context bar — second 56px navigation row.

    Displays the current project → scene breadcrumb and save state.
    Emits signals when the user requests new scene or edit actions.

    API:
        set_project(name)         — set the project name in the breadcrumb
        set_scene(name)           — set the scene name in the breadcrumb
        set_saved_state(state)    — "saved" | "unsaved" | "saving" | "failed"
        set_unsaved()             — shortcut for set_saved_state("unsaved")
        set_saved()               — shortcut for set_saved_state("saved")

    Signals:
        new_scene_requested      — user clicked "New Scene" (future)
        edit_scene_requested     — user clicked the edit icon
    """

    new_scene_requested = Signal()
    edit_scene_requested = Signal()
    # P3.15: New signals for Project/Scene switching + rename + delete
    project_switch_requested = Signal(str)   # project_id
    scene_switch_requested = Signal(str)     # scene_id
    rename_project_requested = Signal()
    rename_scene_requested = Signal()
    delete_project_requested = Signal()
    delete_scene_requested = Signal()
    new_project_requested = Signal()
    # P3.21: Scene reordering signals
    scene_move_up_requested = Signal()
    scene_move_down_requested = Signal()
    # P3.24 UX Correction: "Assemble Audio..." has been REMOVED from the
    # Scene dropdown and moved to a prominent bottom-of-sidebar action
    # (see project_scene_sidebar.py — assemble_audio_requested). One
    # clear primary location; File → Assemble Scenes... remains as the
    # standard menu-bar path. The old assemble_audio_requested signal is
    # gone — do not reconnect it here.

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(56)
        # CRITICAL: WA_StyledBackground makes QWidget paint its own stylesheet
        # background. Without this, the stylesheet background-color is ignored
        # and the widget appears transparent — the ambient bleeds through.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("ProjectSceneBar")
        self.setStyleSheet("""
            QWidget#ProjectSceneBar {
                background-color: #1A1A1A;
                border-bottom: 1px solid #333333;
            }
        """)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(0)

        # ---- Left: Breadcrumb ----
        # Folder icon — rendered at IconSize.MD (22px) in a 28x28 container
        self._folder_icon = QLabel()
        icon = IconRegistry.icon("folder_open", size=IconSize.MD, color=IconColor.SECONDARY)
        if icon:
            self._folder_icon.setPixmap(icon.pixmap(QSize(IconSize.MD, IconSize.MD)))
        else:
            self._folder_icon.setText("\U0001F4C1")
        self._folder_icon.setStyleSheet("color: #A0A0A0;")
        self._folder_icon.setFixedSize(28, 28)
        layout.addWidget(self._folder_icon)

        # PROJECTS label (uppercase, compact)
        self._projects_label = QLabel("PROJECTS")
        self._projects_label.setFont(Typography.label_caps())
        self._projects_label.setStyleSheet(
            "color: #A0A0A0; padding-left: 8px; letter-spacing: 0.5px;")
        layout.addWidget(self._projects_label)

        # Chevron 1 (PROJECTS › Project)
        chev1 = self._make_chevron()
        layout.addWidget(chev1)

        # Project name — P3.15: now a clickable QToolButton with dropdown menu
        # for Project switching, rename, and delete.
        self._project_label = QToolButton()
        self._project_label.setText("Default")
        self._project_label.setFont(Typography.body_lg())
        self._project_label.setStyleSheet("""
            QToolButton {
                color: #F5F5F5;
                padding: 0 8px;
                border: none;
                border-radius: 4px;
                background: transparent;
            }
            QToolButton:hover {
                background-color: #2E2E2E;
                color: #d0bcff;
            }
            QToolButton::menu-indicator {
                image: none;
            }
        """)
        self._project_label.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._project_label.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._project_label.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._project_label)

        # Chevron 2 (Project › Scene)
        chev2 = self._make_chevron()
        layout.addWidget(chev2)

        # Scene name — P3.15: now a clickable QToolButton with dropdown menu
        # for Scene switching, rename, and delete.
        self._scene_label = QToolButton()
        self._scene_label.setText("Untitled")
        self._scene_label.setFont(Typography.body_lg())
        self._scene_label.setStyleSheet("""
            QToolButton {
                color: #F5F5F5;
                padding: 0 8px;
                border: none;
                border-radius: 4px;
                background: transparent;
            }
            QToolButton:hover {
                background-color: #2E2E2E;
                color: #d0bcff;
            }
            QToolButton::menu-indicator {
                image: none;
            }
        """)
        self._scene_label.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._scene_label.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._scene_label.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._scene_label)

        # Edit icon button — rendered at IconSize.SM (18px) in a 28x28 container
        self._edit_btn = QToolButton()
        edit_icon = IconRegistry.icon("edit", size=IconSize.SM, color=IconColor.SECONDARY)
        if edit_icon:
            self._edit_btn.setIcon(edit_icon)
            self._edit_btn.setIconSize(QSize(IconSize.SM, IconSize.SM))
        else:
            self._edit_btn.setText("\u270E")
        self._edit_btn.setFixedSize(28, 28)
        self._edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_btn.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                color: #A0A0A0;
                margin-left: 4px;
            }
            QToolButton:hover {
                color: #d0bcff;
            }
        """)
        self._edit_btn.setToolTip("Edit scene name")
        self._edit_btn.clicked.connect(self.edit_scene_requested.emit)
        layout.addWidget(self._edit_btn)

        # ---- Stretch pushes save state to the right ----
        layout.addStretch()

        # ---- Right: Save state indicator ----
        self._save_dot = QLabel("\u25cf")
        self._save_dot.setStyleSheet(
            "color: #10B981; font-size: 8px;")  # green dot, small
        layout.addWidget(self._save_dot)

        self._save_label = QLabel("Changes Saved")
        self._save_label.setFont(Typography.mono_data())
        self._save_label.setStyleSheet(
            "color: #10B981; padding-left: 6px;")
        layout.addWidget(self._save_label)

    def _make_chevron(self) -> QLabel:
        """Create a chevron_right icon label."""
        chev = QLabel()
        icon = IconRegistry.icon("chevron_right", size=IconSize.SM, color=IconColor.DISABLED)
        if icon:
            chev.setPixmap(icon.pixmap(QSize(16, 16)))
        else:
            chev.setText("\u203A")
        chev.setStyleSheet("color: #555555; padding: 0 4px;")
        chev.setFixedSize(20, 24)
        return chev

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_project(self, name: str) -> None:
        """Set the project name in the breadcrumb."""
        self._project_label.setText(name or "Default")

    def set_scene(self, name: str) -> None:
        """Set the scene name in the breadcrumb."""
        self._scene_label.setText(name or "Untitled")

    def set_saved_state(self, state: str) -> None:
        """Set the save state indicator.

        Args:
            state: One of "saved", "unsaved", "saving", "failed".
        """
        states = {
            "saved":    ("#10B981", "Changes Saved"),      # green
            "unsaved":  ("#F59E0B", "Unsaved Changes"),     # amber
            "saving":   ("#d0bcff", "Saving..."),            # purple
            "failed":   ("#EF4444", "Save Failed"),          # red
        }
        color, text = states.get(state, states["saved"])
        self._save_dot.setStyleSheet(
            f"color: {color}; font-size: 8px;")
        self._save_label.setStyleSheet(
            f"color: {color}; padding-left: 6px;")
        self._save_label.setText(text)

    def set_unsaved(self) -> None:
        """Shortcut for set_saved_state('unsaved')."""
        self.set_saved_state("unsaved")

    def set_saved(self) -> None:
        """Shortcut for set_saved_state('saved')."""
        self.set_saved_state("saved")

    def set_saving(self) -> None:
        """Shortcut for set_saved_state('saving')."""
        self.set_saved_state("saving")

    def set_save_failed(self) -> None:
        """Shortcut for set_saved_state('failed')."""
        self.set_saved_state("failed")

    # ------------------------------------------------------------------
    # P3.15: Project / Scene dropdown menu population
    # ------------------------------------------------------------------
    def set_project_menu(self, projects: list, active_project_id: str = "") -> None:
        """Populate the Project dropdown menu.

        Args:
            projects: list of dicts with 'id' and 'name'.
            active_project_id: the active project ID (shown with a checkmark).
        """
        menu = QMenu(self)
        for p in projects:
            pid = p.get("id", "")
            pname = p.get("name", "Untitled")
            action = menu.addAction(pname)
            action.setCheckable(True)
            action.setChecked(pid == active_project_id)
            action.triggered.connect(
                lambda checked=False, id=pid: self.project_switch_requested.emit(id))
        menu.addSeparator()
        menu.addAction("New Project", self.new_project_requested.emit)
        menu.addSeparator()
        menu.addAction("Rename Project", self.rename_project_requested.emit)
        menu.addAction("Delete Project", self.delete_project_requested.emit)
        self._project_label.setMenu(menu)

    def set_scene_menu(self, scenes: list, active_scene_id: str = "") -> None:
        """Populate the Scene dropdown menu.

        Args:
            scenes: list of dicts with 'id' and 'name'.
            active_scene_id: the active scene ID (shown with a checkmark).
        """
        menu = QMenu(self)
        for s in scenes:
            sid = s.get("id", "")
            sname = s.get("name", "Untitled")
            action = menu.addAction(sname)
            action.setCheckable(True)
            action.setChecked(sid == active_scene_id)
            action.triggered.connect(
                lambda checked=False, id=sid: self.scene_switch_requested.emit(id))
        menu.addSeparator()
        menu.addAction("New Scene", self.new_scene_requested.emit)
        menu.addSeparator()
        # P3.21: Scene reordering
        menu.addAction("Move Up", self.scene_move_up_requested.emit)
        menu.addAction("Move Down", self.scene_move_down_requested.emit)
        menu.addSeparator()
        # P3.24 UX Correction: "Assemble Audio..." was removed from this
        # dropdown (hidden entry point, duplicated the new bottom-sidebar
        # action). The prominent entry is now the sidebar button; the
        # File → Assemble Scenes... menu action remains as the secondary
        # standard-menu path.
        menu.addAction("Rename Scene", self.rename_scene_requested.emit)
        menu.addAction("Delete Scene", self.delete_scene_requested.emit)
        self._scene_label.setMenu(menu)
