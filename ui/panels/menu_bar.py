"""
SpeechStudio Menu Bar.

UI Specification, section 4:
    File: New Project, Open Project, Save Project, Import Voice, Export Voice,
          Import Preset, Export Preset, Exit
    Generation: Generate, Stop, Replay, Clear
    Tools: Benchmark, Voice Library, History, Settings, Developer Mode
    View: Theme, Reset Layout, Fullscreen
    Help: Documentation, About
"""

from __future__ import annotations
from typing import Callable, Optional

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMainWindow, QMenuBar


class MenuBar:
    """Builds and manages the application menu bar.

    This is a controller class (not a widget) that creates the menu structure
    on a QMainWindow's menuBar() and exposes actions for the MainWindow to
    connect to.
    """

    def __init__(self, main_window: QMainWindow):
        self._window = main_window
        self._menubar = main_window.menuBar()
        self._actions = {}
        self._build()

    def _build(self) -> None:
        self._build_file_menu()
        self._build_generation_menu()
        self._build_tools_menu()
        self._build_view_menu()
        self._build_help_menu()

    # ------------------------------------------------------------------
    # File menu
    # ------------------------------------------------------------------
    def _build_file_menu(self) -> None:
        menu = self._menubar.addMenu("&File")
        self._add_action(menu, "new_project", "New Project", "Ctrl+N")
        self._add_action(menu, "new_scene", "New Scene", "Ctrl+Shift+N")
        self._add_action(menu, "open_project", "Open Project", "Ctrl+O")
        self._add_action(menu, "save_project", "Save Project", "Ctrl+S")
        # P3.7: Export Project — creates a portable directory structure
        self._add_action(menu, "export_project", "Export Project...", "Ctrl+Shift+E")
        # P3.22: Assemble Scenes — concatenate scene audio into combined output
        self._add_action(menu, "assemble_scenes", "Assemble Scenes...", "Ctrl+Shift+A")
        menu.addSeparator()
        self._add_action(menu, "save_scene", "Save Dialogue Scene...")
        self._add_action(menu, "load_scene", "Load Dialogue Scene...")
        menu.addSeparator()
        self._add_action(menu, "import_voice", "Import Voice Profile…")
        self._add_action(menu, "export_voice", "Export Voice Profile…")
        self._add_action(menu, "import_preset", "Import Preset")
        self._add_action(menu, "export_preset", "Export Preset")
        menu.addSeparator()
        self._add_action(menu, "exit", "Exit", "Ctrl+Q")

    # ------------------------------------------------------------------
    # Generation menu
    # ------------------------------------------------------------------
    def _build_generation_menu(self) -> None:
        menu = self._menubar.addMenu("&Generation")
        self._add_action(menu, "generate", "Generate", "Ctrl+Return")
        self._add_action(menu, "generate_long", "Generate Long Narration", "Ctrl+Shift+Return")
        # P3.25 (audit SS-H13): Esc is NO LONGER a QAction shortcut. An
        # ApplicationShortcut-context QAction intercepted EVERY Esc press
        # app-wide (before any widget saw the key event), so Esc could
        # never exit fullscreen or close the search bar. Esc is handled by
        # ONE authoritative handler — MainWindow.keyPressEvent — with an
        # explicit priority chain (generation stop > exit fullscreen >
        # close search bar). The Stop menu item remains clickable.
        self._add_action(menu, "stop", "Stop")
        self._add_action(menu, "replay", "Replay")
        self._add_action(menu, "clear", "Clear")

    # ------------------------------------------------------------------
    # Tools menu
    # ------------------------------------------------------------------
    def _build_tools_menu(self) -> None:
        menu = self._menubar.addMenu("&Tools")
        self._add_action(menu, "benchmark", "Benchmark")
        self._add_action(menu, "voice_library", "Voice Profiles…", "Ctrl+L")
        self._add_action(menu, "history", "History", "Ctrl+H")
        # P3.6: Preset Manager now has a permanent home in the Tools menu
        # (previously only reachable via the sidebar LIBRARY section, which
        # has been removed). Uses the same _on_open_preset_manager handler.
        self._add_action(menu, "preset_manager", "Preset Manager...")
        self._add_action(menu, "settings", "Settings")
        menu.addSeparator()
        batch_action = self._add_action(menu, "batch_generation",
                                        "Batch Generation...")
        # P3.35 §6: honest tooltip — this opens the MANUAL queue (the
        # Scene-aware workflow is Long Generation); shared by the classic
        # Tools menu AND the top-nav Project menu (same QAction).
        batch_action.setToolTip(
            "Open the manual batch queue (independent of Scenes)")
        menu.addSeparator()
        self._add_action(menu, "open_outputs", "Open Output Folder")

    # ------------------------------------------------------------------
    # View menu
    # ------------------------------------------------------------------
    def _build_view_menu(self) -> None:
        menu = self._menubar.addMenu("&View")

        # Theme submenu — lists ALL registered themes from ui.theme.THEMES.
        # Each theme is a checkable action; the active theme shows a checkmark.
        # This replaces the old single "Theme" action that just cycled themes.
        from ui.theme import THEMES, DEFAULT_THEME
        theme_menu = menu.addMenu("&Theme")
        self._theme_actions = {}
        for theme_id in sorted(THEMES.keys()):
            action = QAction(theme_id, self._window)
            action.setCheckable(True)
            action.setChecked(theme_id == DEFAULT_THEME)
            theme_menu.addAction(action)
            self._theme_actions[theme_id] = action
        self._actions["theme"] = theme_menu  # Store the submenu for reference

        self._add_action(menu, "reset_layout", "Reset Layout")
        self._add_action(menu, "fullscreen", "Fullscreen", "F11")
        menu.addSeparator()
        self._add_checkable_action(menu, "show_prompt_blocks",
                                   "Show Prompt Blocks", True)

    # ------------------------------------------------------------------
    # Help menu
    # ------------------------------------------------------------------
    def _build_help_menu(self) -> None:
        menu = self._menubar.addMenu("&Help")
        self._add_action(menu, "documentation", "Documentation")
        self._add_action(menu, "supported_languages", "Supported Languages...")
        self._add_action(menu, "token_guide", "Token Guide...")
        self._add_action(menu, "font_status", "Font Status...")
        self._add_action(menu, "copy_debug", "Copy Debug Information")
        self._add_action(menu, "about", "About")

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------
    def _add_action(self, menu, key: str, text: str,
                    shortcut: Optional[str] = None) -> QAction:
        action = QAction(text, self._window)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        menu.addAction(action)
        self._actions[key] = action
        return action

    def _add_checkable_action(self, menu, key: str, text: str,
                              checked: bool = False) -> QAction:
        action = QAction(text, self._window)
        action.setCheckable(True)
        action.setChecked(checked)
        menu.addAction(action)
        self._actions[key] = action
        return action

    # ------------------------------------------------------------------
    # Connection API
    # ------------------------------------------------------------------
    def connect(self, key: str, callback: Callable) -> None:
        """Connect a menu action to a callback.

        Wraps the callback to accept the optional 'checked' boolean that
        QAction.triggered emits, so 0-argument callbacks work correctly.
        """
        action = self._actions.get(key)
        if action:
            # Wrap to discard the 'checked' argument from triggered signal
            def _wrapper(checked=False):
                callback()
            action.triggered.connect(_wrapper)

    def connect_checkable(self, key: str,
                          callback: Callable[[bool], None]) -> None:
        """Connect a checkable menu action to a callback that receives the
        checked state as a boolean argument."""
        action = self._actions.get(key)
        if action:
            action.toggled.connect(callback)

    def get_action(self, key: str) -> Optional[QAction]:
        return self._actions.get(key)

    def connect_theme_actions(self, callback: Callable) -> None:
        """Connect each theme submenu action to a callback.

        The callback receives the theme_id (string) as its argument.
        """
        for theme_id, action in self._theme_actions.items():
            action.triggered.connect(lambda checked=False, tid=theme_id: callback(tid))

    def set_active_theme(self, theme_id: str) -> None:
        """Update the checkmark on the theme submenu.

        Called after theme switching to visually indicate the active theme.
        """
        for tid, action in self._theme_actions.items():
            action.setChecked(tid == theme_id)
