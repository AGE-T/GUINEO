"""
SpeechStudio Toolbar.

UI Specification, section 5:
    Buttons: Generate, Stop, Replay, Open Output Folder, Voice Library,
             History, Settings, Benchmark
    Toolbar icons only. Tooltip required.
"""

from __future__ import annotations
from typing import Callable, Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QToolBar, QMainWindow, QPushButton

from ui.theme import Palette


# Icon size for toolbar buttons
ICON_SIZE = 24


class Toolbar:
    """Builds and manages the application toolbar.

    Icons-only buttons with tooltips. The MainWindow connects callbacks
    to each action.
    """

    def __init__(self, main_window: QMainWindow):
        self._window = main_window
        self._toolbar = main_window.addToolBar("Main")
        self._toolbar.setMovable(False)
        self._toolbar.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._actions = {}
        self._generate_btn = None  # QPushButton for the primary Generate action
        self._build()

    def _build(self) -> None:
        # Generate (primary action) — inline styled with Palette colors
        # that are updated by apply_theme() to match the active theme.
        # Using inline style because the QSS accent property doesn't
        # reliably style buttons inside a QToolBar.
        self._generate_btn = QPushButton("Generate")
        self._generate_btn.setToolTip("Generate speech (Ctrl+Enter)")
        self._generate_btn.setStatusTip("Generate speech (Ctrl+Enter)")
        self._generate_btn.setMinimumWidth(90)
        font = self._generate_btn.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        self._generate_btn.setFont(font)
        self._apply_generate_style()
        # Wrap as a toolbar widget so it appears in the toolbar.
        self._toolbar.addWidget(self._generate_btn)
        # Stop
        self._add_action("stop", "Stop", "tooltip: Stop generation (Esc)")
        self._toolbar.addSeparator()
        # Replay
        self._add_action("replay", "Replay", "tooltip: Replay last output")
        # Open Output Folder
        self._add_action("open_outputs", "Open Output Folder", "tooltip: Open outputs/ folder")
        self._toolbar.addSeparator()
        # Voice Library
        self._add_action("voice_library", "Voice Profiles", "tooltip: Open Voice Profiles (Ctrl+L)")
        # History
        self._add_action("history", "History", "tooltip: Open history (Ctrl+H)")
        # Settings
        self._add_action("settings", "Settings", "tooltip: Open settings")
        # Benchmark
        self._add_action("benchmark", "Benchmark", "tooltip: Open benchmark tool")
        # P3.20: Developer Mode removed — was a no-op placeholder.

    def _add_action(self, key: str, text: str, tooltip: str) -> QAction:
        # Use text as icon placeholder (Phase 7 will add real icons).
        # Using QAction with text works as a fallback when no icon is set.
        action = QAction(text, self._window)
        action.setToolTip(tooltip)
        action.setStatusTip(tooltip)
        self._toolbar.addAction(action)
        self._actions[key] = action
        return action

    # ------------------------------------------------------------------
    # Connection API
    # ------------------------------------------------------------------
    def connect(self, key: str, callback: Callable) -> None:
        # The "generate" key is a QPushButton, not a QAction.
        if key == "generate" and self._generate_btn is not None:
            self._generate_btn.clicked.connect(callback)
            return
        action = self._actions.get(key)
        if action:
            # Wrap to discard the 'checked' argument from triggered signal
            def _wrapper(checked=False):
                callback()
            action.triggered.connect(_wrapper)

    def set_generate_enabled(self, enabled: bool) -> None:
        if self._generate_btn is not None:
            self._generate_btn.setEnabled(enabled)

    def refresh_theme(self) -> None:
        """Re-apply the Generate button style after a theme change.

        Call this from MainWindow._on_theme_toggle() after apply_theme()
        so the button picks up the new Palette colors.
        """
        self._apply_generate_style()

    def _apply_generate_style(self) -> None:
        """Apply the orange gradient CTA style to the Generate button.

        Per the HTML reference, the Generate button uses the orange gradient
        (linear-gradient(135deg, #F97316, #EA580C)) — NOT the purple accent.
        Orange is reserved exclusively for primary CTA actions.
        """
        if self._generate_btn is None:
            return
        self._generate_btn.setStyleSheet(
            "QPushButton {{"
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            "    stop:0 #F97316, stop:1 #EA580C);"
            "  color: #FFFFFF;"
            "  border: none;"
            "  border-radius: 8px;"
            "  padding: 8px 20px;"
            "  font-weight: bold;"
            "  font-size: 13px;"
            "}}"
            "QPushButton:hover {{"
            "  opacity: 0.9;"
            "}}"
            "QPushButton:pressed {{"
            "  opacity: 0.8;"
            "}}"
            "QPushButton:disabled {{"
            "  background-color: {bg_surface};"
            "  color: {text_disabled};"
            "  border: 1px solid {border};"
            "}}".format(
                bg_surface=Palette.BG_SURFACE,
                text_disabled=Palette.TEXT_DISABLED,
                border=Palette.BORDER,
            )
        )

    def set_stop_enabled(self, enabled: bool) -> None:
        action = self._actions.get("stop")
        if action:
            action.setEnabled(enabled)
