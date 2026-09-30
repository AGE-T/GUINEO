"""
SpeechStudio Main Window - Phase 3 (Sprint 03: GUI Framework).

UI Specification, section 3:
    +-----------------------------------------------------------+
    | Menu Bar                                                   |
    +-----------------------------------------------------------+
    | Toolbar                                                    |
    +---------+---------------------------+----------------------+
    | Sidebar | Prompt Editor             | Control Panel        |
    +---------+---------------------------+----------------------+
    | Waveform / Audio Player                                    |
    +-----------------------------------------------------------+
    | Status Bar                                                 |
    +-----------------------------------------------------------+

This module assembles the complete application shell with all panels
defined by the UI Specification, connected to the Engine.
"""

import os
import sys
import platform
import logging
import hashlib
import subprocess
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QMessageBox, QFileDialog, QLabel, QInputDialog,
)

from ui.theme import Palette
from ui.feedback import FeedbackDialog
from ui.panels.menu_bar import MenuBar
from ui.panels.toolbar import Toolbar
from ui.panels.project_scene_sidebar import Sidebar as ProjectSceneSidebar
from ui.panels.narration_editor import NarrationEditor
from ui.panels.right_panel import RightPanel as ControlPanel
from ui.panels.waveform_player import WaveformPlayer
from ui.panels.status_bar import StatusBar
from engine.prompt_builder import PromptBuilder
from engine.preset_manager import Preset, PresetManager
from engine.batch_manager import BatchManager, BatchJob
from engine.models import GenerationParameters, Project, Scene, Character, AudioAsset
from engine.project_manager import ProjectManager
from engine.recents_manager import RecentsManager

logger = logging.getLogger("speechstudio.ui.main_window")

# Application metadata (P3.37: user-facing product identity is GUINEO;
# internal identifiers keep their historical SpeechStudio names)
APP_NAME = "GUINEO"
APP_VERSION = "1.0.0"
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MainWindow(QMainWindow):
    """Top level application window with the full five-region layout."""

    # Signal emitted when the user requests speech generation
    generate_requested = Signal()
    stop_requested = Signal()

    # Internal signals for thread-safe UI updates (worker -> UI thread)
    _model_load_done_signal = Signal(object)       # ModelStatus
    _model_load_for_gen_signal = Signal(object, str)  # ModelStatus, text
    _generation_finished_signal = Signal(object)   # GenerationResult
    _generation_failed_signal = Signal(object)     # GenerationResult
    _playback_position_signal = Signal(float, float)  # position_sec, duration_sec
    _playback_finished_signal = Signal()
    # P3.23 FIX (audit: thread affinity — HISTORY_UPDATED was emitted on
    # the worker thread and its handler touched widgets directly).
    _history_changed_signal = Signal()
    # P3.25 FIX (audit SS-M14): MODEL_LOADED / MODEL_UNLOADED /
    # ENGINE_STATUS_CHANGED are emitted from background threads. The
    # previous handlers called QTimer.singleShot(0, ...) FROM THE WORKER
    # THREAD — a timer created on a thread without a Qt event loop NEVER
    # fires, so these refresh paths were silently dead. Marshalling via
    # a Qt signal (queued cross-thread) actually delivers.
    _model_status_signal = Signal()

    def __init__(self, engine=None, parent=None):
        super().__init__(parent)
        self._engine = engine
        self.setWindowTitle(APP_NAME)
        # P3.37: GUINEO application icon (also set at the QApplication
        # level by the entry point — setting it here as well makes the
        # icon independent of the construction path). Missing asset is
        # non-fatal.
        try:
            from PySide6.QtGui import QIcon
            _logo = os.path.join(APP_ROOT, "assets", "brand", "guineo_logo.png")
            if os.path.isfile(_logo):
                self.setWindowIcon(QIcon(_logo))
        except Exception:
            pass
        self.resize(1440, 900)
        # Minimum window size must accommodate the fixed 400px side panels
        # + 200px editor minimum + margins. 1100px ensures all panels
        # are visible even on smaller displays.
        self.setMinimumSize(1100, 700)
        # NOTE: Do NOT set WA_TranslucentBackground on the MainWindow.
        # It causes a deadlock with QOpenGLWindow/createWindowContainer
        # on Windows. The ambient renderer works without it.

        # UI state
        self._emotion: Optional[str] = None
        self._style: Optional[str] = None
        self._speed: str = "Normal"
        self._pitch: str = "Normal"
        self._delivery: str = "Normal"
        self._current_project: str = "Default"
        self._current_scene: str = "Untitled"  # NEW: separate from project
        self._scene_unsaved: bool = False  # NEW: track unsaved scene changes
        self._model_load_dialog = None

        # P2.1: Authoritative Project/Scene entities (Phase 2 runtime integration).
        # These hold the real domain model objects. The string labels above
        # (_current_project, _current_scene) remain as convenient UI display
        # names but always derive from these entities.
        self._active_project: Optional[Project] = None
        self._active_scene: Optional[Scene] = None
        self._project_manager: Optional[ProjectManager] = None
        self._recents_manager: Optional[RecentsManager] = None
        self._current_nav_context: str = "Projects"  # P3.4: tracks active nav
        # P3.16 FIX: SINGLE persistent SettingsManager instance for the entire
        # MainWindow. Previously, 13 separate SettingsManager instances were
        # created throughout MainWindow — each loaded settings from disk
        # independently and had its own in-memory copy. When one instance
        # saved, the others didn't know about it, causing stale copies to
        # overwrite each other. This is the root cause of settings not
        # persisting across restarts. Now there is ONE instance, created
        # once in __init__ and reused everywhere via self._settings_manager.
        # P3.25 (audit SS-H04): derive the settings dir from the ENGINE's
        # app root when an engine is provided — MainWindow previously
        # hardwired the module-relative APP_ROOT, so whenever the Engine
        # ran with a different app_root the two held DIFFERENT settings
        # files (a hard split-brain the audit reproduced with probe_c3).
        if engine is not None and getattr(engine, "_app_root", None):
            settings_dir = os.path.join(engine._app_root, "settings")
        else:
            settings_dir = os.path.join(APP_ROOT, "settings")
        from engine.settings_manager import SettingsManager
        # P3.25 (audit SS-H04): the SHARED settings owner (Engine and the
        # entry point obtain the same instance) — one authoritative
        # SettingsManager per settings file per process.
        self._settings_manager = SettingsManager.instance(settings_dir)
        # P3.5 Final Correction: selected Character id, derived from the
        # authoritative CharacterManagementDialog selection. Used to highlight
        # the active Character row in the Characters context list. This is
        # NOT an independent boolean — it is always sourced from the
        # authoritative Project.characters list via the dialog.
        self._selected_character_id: Optional[str] = None
        # P3.23 (design record §17): True while the Voice dropdown displays
        # a block Character's Voice (display-only override; scene saves
        # ignore it and any manual selection clears it).
        self._voice_display_overridden = False
        # P3.5 Final Correction: last-viewed History entry id, used to
        # optionally highlight a row in the History context list.
        self._last_viewed_history_id: Optional[str] = None

        # Prompt builder shared by the prompt-preview path and the
        # generation path so the user sees the exact string that will be
        # sent to the engine.
        self._prompt_builder = PromptBuilder()

        # NOTE: The Read Tokens / SemanticTokenState workflow has been
        # REMOVED from the UI. The current UX is Managed Mode vs Raw Mode
        # (ADR 003 §1). Managed Mode uses the CanonicalPromptCompiler with
        # global defaults + block overrides. Raw Mode uses the literal
        # editor text. There is no "import inline tokens into semantic
        # state" workflow. The CanonicalPromptCompiler still accepts a
        # token_state parameter (kept for engine-level compatibility) but
        # MainWindow always passes None — there is no import path.

        # Preset + batch managers (created in _connect_engine once we
        # know the app root).
        self._preset_manager: Optional[PresetManager] = None
        # Holds a reference to the open PresetManagerDialog while it is
        # visible, so _refresh_presets() can refresh its voice lookup.
        self._preset_dialog = None
        self._batch_manager: Optional[BatchManager] = None
        self._batch_dialog = None

        # Connect internal signals for thread-safe UI updates.
        # Signals are automatically marshalled from worker threads to the
        # UI thread — unlike QTimer.singleShot which does NOT cross threads
        # when used with a bare lambda.
        self._model_load_done_signal.connect(self._close_model_load_dialog)
        self._model_load_for_gen_signal.connect(self._on_model_loaded_for_generation)
        self._generation_finished_signal.connect(self._on_generation_finished_ui)
        # P3.25 (SS-M14): model status refresh runs on the GUI thread via
        # queued signal delivery (the previous QTimer.singleShot(0) from
        # the worker thread never fired).
        self._model_status_signal.connect(self._refresh_model_status)
        self._generation_failed_signal.connect(self._on_generation_failed_ui)
        self._playback_position_signal.connect(self._on_playback_position)
        self._playback_finished_signal.connect(self._on_playback_finished)
        self._history_changed_signal.connect(self._refresh_sidebar)

        self._build_ui()
        self._connect_signals()

        if engine is not None:
            self._connect_engine()
            self._restore_layout()
            # Gate the ambient timer based on the initial theme.
            try:
                settings_dir = os.path.join(APP_ROOT, "settings")
                from engine.settings_manager import SettingsManager
                _sm = self._settings_manager
                _initial_theme = _sm.get("application", "theme", "dark")
                self._update_ambient_for_theme(_initial_theme)
            except Exception:
                pass

        # Refresh sidebar data on startup
        QTimer.singleShot(100, self._refresh_sidebar)

        # Offer to load the model on startup
        if engine is not None:
            QTimer.singleShot(1000, self._offer_model_load)

        logger.info("Main window initialised (Phase 6).")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # Menu bar — creates all QAction objects (kept for shortcuts + popup menus)
        self._menu_bar = MenuBar(self)
        # Hide the native QMenuBar — the TopNavigation widget replaces it visually
        self.menuBar().setVisible(False)

        # Toolbar — creates the Generate button (kept for API compatibility)
        self._toolbar = Toolbar(self)
        # Hide the native QToolBar — TopNavigation has its own Generate button
        self._toolbar._toolbar.setVisible(False)

        # --- Ambient Background Renderer ---
        # Placed as a child of QMainWindow, behind the central widget.
        # It is transparent to mouse events — all input passes through.
        # The factory selects the best renderer: GPU V5 (QOpenGLWidget)
        # → CPU Static → Plain background.
        try:
            from ui.panels.ambient_background_gpu import create_ambient_background
            self._ambient_bg = create_ambient_background(parent=self)
            self._ambient_bg.lower()  # send to back of z-order
            renderer_name = type(self._ambient_bg).__name__
            logger.info("Ambient background: %s", renderer_name)
        except Exception as exc:
            logger.warning("Ambient background failed to initialize: %s", exc)
            self._ambient_bg = None

        # --- Central Shell ---
        # A single QWidget that owns the entire application UI via QVBoxLayout:
        #   TopNavigation (56px) + ProjectSceneBar (56px) + MainArea (stretch)
        # This ensures correct geometry — Qt layouts handle positioning,
        # no manual setGeometry calls needed.
        central_shell = QWidget()
        central_shell.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        central_shell.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        central_shell.setAutoFillBackground(False)

        shell_layout = QVBoxLayout(central_shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        # --- Top Navigation (56px, first row) ---
        from ui.panels.top_navigation import TopNavigation
        self._top_nav = TopNavigation(self._menu_bar, self._toolbar, parent=central_shell)
        shell_layout.addWidget(self._top_nav)

        # --- Project & Scene Context Bar (56px, second row) ---
        from ui.panels.project_scene_bar import ProjectSceneBar
        self._project_scene_bar = ProjectSceneBar(parent=central_shell)
        self._project_scene_bar.set_project(self._current_project)
        self._project_scene_bar.set_scene(self._current_scene)
        self._project_scene_bar.set_saved()
        shell_layout.addWidget(self._project_scene_bar)

        # --- Main Area (sidebar | editor | control panel + waveform) ---
        # This is a QVBoxLayout with 16px margins (HTML reference gutter).
        main_area = QWidget()
        main_area.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        main_area.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        main_area.setAutoFillBackground(False)

        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(0)

        # ---- FIXED THREE-COLUMN SHELL ----
        # Production decision: LEFT PANEL width == RIGHT PANEL width = 400px.
        #
        # Architecture: QSplitter with FIXED-width side panels (min=max=400)
        # and an EXPANDING editor. The QSplitter is used instead of QHBoxLayout
        # because QSplitter correctly handles WA_TranslucentBackground on
        # Windows with the GPU ambient renderer (QOpenGLWidget). A plain
        # QWidget container with QHBoxLayout crashed on Windows during show().
        #
        # P3.16: Side panel widths are now loaded from settings (previously
        # hardcoded to 400px). The saved values persist across restarts.
        # Falls back to 400 if no setting exists.
        sidebar_w = self._settings_manager.get("window", "sidebar_width", 400)
        control_w = self._settings_manager.get("window", "control_panel_width", 400)
        SIDE_PANEL_WIDTH = sidebar_w
        CONTROL_PANEL_WIDTH = control_w

        self._h_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._h_splitter.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._h_splitter.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._h_splitter.setAutoFillBackground(False)
        self._h_splitter.setHandleWidth(6)

        # Left sidebar: width from settings (previously fixed at 400).
        self._sidebar = ProjectSceneSidebar()
        self._sidebar.setFixedWidth(SIDE_PANEL_WIDTH)
        self._h_splitter.addWidget(self._sidebar)

        # Editor: EXPANDING — absorbs all remaining horizontal space.
        self._editor = NarrationEditor()
        self._editor.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._editor.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._editor.setAutoFillBackground(False)
        self._editor.setMinimumWidth(200)
        self._h_splitter.addWidget(self._editor)

        # Right control panel: FIXED width (same as sidebar, symmetric).
        # CRITICAL: Do NOT set WA_TranslucentBackground on the control_panel.
        # On Windows, WA_TranslucentBackground + WA_StyledBackground on the
        # RightPanel causes a stack overflow during show() (infinite paint
        # recursion). The control_panel renders its own opaque background
        # via its stylesheet — no translucency needed.
        self._control_panel = ControlPanel()
        self._control_panel.setFixedWidth(CONTROL_PANEL_WIDTH)
        self._h_splitter.addWidget(self._control_panel)

        # Set initial sizes: side panels at their configured widths, editor gets the rest.
        self._h_splitter.setSizes([SIDE_PANEL_WIDTH, 700, CONTROL_PANEL_WIDTH])
        self._h_splitter.setStretchFactor(0, 0)  # sidebar doesn't stretch
        self._h_splitter.setStretchFactor(1, 1)  # editor stretches (flexible)
        self._h_splitter.setStretchFactor(2, 0)  # control panel doesn't stretch

        main_layout.addWidget(self._h_splitter, 1)

        # Waveform player (bottom of main area)
        self._waveform = WaveformPlayer()
        main_layout.addWidget(self._waveform)

        shell_layout.addWidget(main_area, 1)

        self.setCentralWidget(central_shell)

        # Status bar
        self._status_bar = StatusBar()
        self.setStatusBar(self._status_bar)

        # Apply initial state
        self._update_status_bar()

    # ------------------------------------------------------------------
    # Signal connections
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        # Menu actions
        self._menu_bar.connect("generate", self._on_generate)
        self._menu_bar.connect("generate_long", self._on_generate_long_narration)
        self._menu_bar.connect("stop", self._on_stop)
        self._menu_bar.connect("clear", self._on_clear)
        self._menu_bar.connect("replay", self._on_replay)
        self._menu_bar.connect("exit", self.close)
        self._menu_bar.connect("fullscreen", self._toggle_fullscreen)
        self._menu_bar.connect("about", self._show_about)
        self._menu_bar.connect("settings", self._on_open_settings)
        self._menu_bar.connect("history", self._focus_history)
        # P3.6: Preset Manager now has a permanent home in the Tools menu /
        # TopNav Project menu (replaces the removed sidebar LIBRARY entry).
        self._menu_bar.connect("preset_manager", self._on_open_preset_manager)
        self._menu_bar.connect("import_voice", self._on_import_voice)
        self._menu_bar.connect("export_voice", self._on_export_voice_menu)
        # P3.43: Tools → Voice Profiles + Ctrl+L now actually open the
        # unified Voice Profile management screen. (This QAction existed
        # since P3.6 but was never connected — the entry point was dead.)
        self._menu_bar.connect("voice_library", self._on_open_voice_library)
        self._menu_bar.connect("open_outputs", self._on_open_output_folder)
        self._menu_bar.connect("reset_layout", self._reset_layout)
        # P3.23 FIX (audit: Settings opened TWICE): the duplicated
        # "settings" connect above (line ~336) + here registered the
        # handler twice — MenuBar.connect wraps each registration in a
        # fresh closure, so Qt treated them as two slots and the modal
        # Settings dialog appeared again right after closing it.
        # The duplicate registration is removed (kept once, above).
        self._menu_bar.connect("benchmark", self._on_open_benchmark)
        self._menu_bar.connect("documentation", self._on_documentation)
        self._menu_bar.connect("supported_languages", self._on_supported_languages)
        self._menu_bar.connect("token_guide", self._on_token_guide)
        self._menu_bar.connect("font_status", self._on_font_status)
        self._menu_bar.connect("copy_debug", self._on_copy_debug)
        # Theme submenu — each theme action calls _on_theme_selected(theme_id)
        self._menu_bar.connect_theme_actions(self._on_theme_selected)
        self._menu_bar.connect("batch_generation", self._on_open_batch_generation)
        self._menu_bar.connect("new_scene", self._on_new_scene)
        self._menu_bar.connect_checkable("show_prompt_blocks",
                                         self._on_toggle_show_blocks)
        # Project actions
        self._menu_bar.connect("new_project", self._on_new_project)
        self._menu_bar.connect("open_project", self._on_open_project)
        self._menu_bar.connect("save_project", self._on_save_project)
        # P3.7: Export Project — portable directory export
        self._menu_bar.connect("export_project", self._on_export_project)
        # P3.22: Assemble Scenes — combined audio assembly
        self._menu_bar.connect("assemble_scenes", self._on_assemble_scenes)
        self._menu_bar.connect("save_scene", self._on_save_scene)
        self._menu_bar.connect("load_scene", self._on_load_scene)
        # Preset import/export are wired to real handlers below.
        self._menu_bar.connect("import_preset", self._on_import_preset)
        self._menu_bar.connect("export_preset", self._on_export_preset)

        # P3.23 FIX (audit: F11/Esc "Ambiguous shortcut overload"):
        # Previously BOTH the hidden native menubar QActions ("Fullscreen"
        # with F11, "Stop" with Esc) AND standalone QShortcut objects were
        # registered for the same keys — Qt disables BOTH on ambiguity,
        # killing F11 fullscreen and Esc stop entirely. The duplicates are
        # removed; the QAction shortcuts (below) are set to
        # ApplicationShortcut context so they fire regardless of which
        # child widget has focus (and in Full Screen mode).
        for _action_name in ("fullscreen", "stop"):
            _act = self._menu_bar.get_action(_action_name)
            if _act is not None and _act.shortcut() is not None:
                _act.setShortcutContext(
                    Qt.ShortcutContext.ApplicationShortcut)
        # Esc semantics (documented): Stop generation. F11 toggles fullscreen.

        # Toolbar actions
        # Toolbar actions (now accessed via TopNavigation popup menus)
        self._toolbar.connect("generate", self._on_generate)
        self._toolbar.connect("stop", self._on_stop)
        self._toolbar.connect("replay", self._on_replay)
        self._toolbar.connect("open_outputs", self._on_open_output_folder)
        self._toolbar.connect("voice_library", self._on_open_voice_library)
        self._toolbar.connect("history", self._focus_history)
        self._toolbar.connect("settings", self._on_open_settings)
        self._toolbar.connect("benchmark", self._on_open_benchmark)
        # P3.20: Developer Mode toolbar button + handler removed (was no-op).

        # Top Navigation visible action buttons
        self._top_nav.connect_generate(self._on_generate)
        self._top_nav.connect_save(lambda: self._menu_bar.get_action("save_project").trigger())
        self._top_nav.connect_export(lambda: self._menu_bar.get_action("export_voice").trigger())
        # Undo/Redo: delegate to the editor's QTextEdit undo/redo stack.
        # The NarrationEditor wraps a QPlainTextEdit which has built-in
        # undo/redo via document().undo()/document().redo().
        self._top_nav.connect_undo(self._on_undo)
        self._top_nav.connect_redo(self._on_redo)

        # Project & Scene Context Bar signals
        self._project_scene_bar.edit_scene_requested.connect(self._on_edit_scene_name)
        # P3.15: Project/Scene switching + rename + delete from the top bar
        self._project_scene_bar.project_switch_requested.connect(self._on_project_switch)
        self._project_scene_bar.scene_switch_requested.connect(self._on_scene_switch)
        self._project_scene_bar.rename_project_requested.connect(self._on_rename_project)
        self._project_scene_bar.rename_scene_requested.connect(self._on_edit_scene_name)
        self._project_scene_bar.delete_project_requested.connect(self._on_delete_project)
        self._project_scene_bar.delete_scene_requested.connect(self._on_delete_scene)
        self._project_scene_bar.new_project_requested.connect(self._on_new_project)
        # P3.24 UX Correction (BUG FIX): the Scene dropdown's "New Scene"
        # action emits new_scene_requested — but this signal was NEVER
        # connected, so "New Scene" in the top Scene dropdown did nothing.
        # Connected now to the single authoritative scene-creation handler
        # (same handler as the menu bar and the sidebar + button).
        self._project_scene_bar.new_scene_requested.connect(self._on_new_scene)
        # P3.21: Scene reordering
        self._project_scene_bar.scene_move_up_requested.connect(self._on_scene_move_up)
        self._project_scene_bar.scene_move_down_requested.connect(self._on_scene_move_down)
        # P3.24 UX Correction: "Assemble Audio" moved from the Scene
        # dropdown to a prominent bottom-of-sidebar action. Same
        # authoritative handler (_on_assemble_scenes) — NOT a second
        # assembly implementation.

        # Control panel signals
        self._control_panel.emotion_changed.connect(self._on_emotion_changed)
        self._control_panel.style_changed.connect(self._on_style_changed)
        self._control_panel.speed_changed.connect(self._on_speed_changed)
        self._control_panel.pitch_changed.connect(self._on_pitch_changed)
        self._control_panel.delivery_changed.connect(self._on_delivery_changed)
        self._control_panel.sfx_inserted.connect(self._on_sfx_inserted)
        self._control_panel.pause_inserted.connect(self._on_pause_inserted)
        self._control_panel.parameters_changed.connect(self._on_parameters_changed)
        self._control_panel.voice_changed.connect(self._on_voice_changed)
        self._control_panel.import_voice_requested.connect(self._on_import_voice)
        self._control_panel.create_voice_requested.connect(self._on_create_voice)
        # P3.43: the Friendly tab's "Change" action opens the LIGHTWEIGHT
        # picker (selection only) — full management is the explicit
        # "Manage Voice Profiles…" affordance inside the picker.
        self._control_panel.voice_library_requested.connect(self._on_pick_voice)
        # Phase 1.3 — big purple Generate button at the bottom of the panel.
        self._control_panel.generate_requested.connect(self._on_generate)

        # Editor text change -> update prompt preview + narration editor
        self._editor.text_changed.connect(self._on_editor_text_changed)
        self._editor.blocks_changed.connect(self._update_prompt_preview)
        # P3.23 (design record §14): block changes also maintain
        # Scene.character_ids — assigning a Character to a block adds the
        # Character to the Scene automatically.
        self._editor.blocks_changed.connect(self._sync_scene_characters)
        # P3.45.1: block STRUCTURE changes (Scene/project restore via
        # set_scene_blocks, Re-detect, programmatic text swap) re-derive
        # the gutter generation-state badges from the active Scene's
        # provenance state — state-transition driven, no polling.
        self._editor.blocks_changed.connect(self._sync_block_status_badges)
        self._editor.block_selected.connect(self._on_block_selected)
        self._editor.generate_long_requested.connect(
            self._on_generate_long_narration)
        # Generate capability signal (Requirement 3)
        self._editor.generate_capability_changed.connect(self._on_generate_capability_changed)
        # NOTE: The read_tokens_requested signal and _on_read_tokens handler
        # have been REMOVED. The Read Tokens workflow is obsolete (ADR 003 §1).

        # Sidebar signals
        self._sidebar.new_project_requested.connect(self._on_new_project)
        self._sidebar.new_scene_requested.connect(self._on_new_scene)
        # P3.9: Context-aware + button signals
        self._sidebar.add_project_requested.connect(self._on_new_project)
        self._sidebar.add_scene_requested.connect(self._on_new_scene)
        # P3.24 UX Correction: "+ Add Character" now runs the QUICK inline
        # creation flow (no modal). The detailed CharacterManagementDialog
        # remains available as the secondary details editor via the row
        # context menu (character_details_requested).
        self._sidebar.add_character_requested.connect(self._on_character_create_quick)
        # P3.24 UX Correction: inline CHARACTERS panel management signals.
        # MainWindow is the controller — it mutates the authoritative
        # Project and refreshes every dependent surface.
        self._sidebar.character_rename_requested.connect(
            self._on_character_rename_requested)
        self._sidebar.character_voice_requested.connect(
            self._on_character_voice_requested)
        self._sidebar.character_delete_requested.connect(
            self._on_character_delete_requested)
        self._sidebar.character_details_requested.connect(
            self._on_character_details_requested)
        # P3.24 UX Correction: bottom-of-sidebar Assemble Audio action.
        self._sidebar.assemble_audio_requested.connect(self._on_assemble_scenes)
        self._sidebar.scene_selected.connect(self._on_sidebar_scene_selected)
        # P3.4: Wire context-aware recents click signal
        self._sidebar.recent_item_clicked.connect(self._on_recent_item_clicked)
        # P3.34 UX Correction wiring:
        #   - the ALL HISTORY header icon opens the EXISTING full History
        #     view (the canonical _focus_history handler — no second
        #     History window implementation);
        #   - the ALL SCENES pencil triggers the authoritative scene
        #     rename workflow (same system as the ProjectSceneBar
        #     "Rename Scene" action — no second rename system).
        self._sidebar.open_history_view_requested.connect(self._focus_history)
        self._sidebar.scene_rename_requested.connect(
            self._on_sidebar_scene_rename)
        # P3.2: Wire the Characters navigation signal
        # P3.10 FIX: Clicking "Characters" in the primary nav should ONLY
        # switch the context to show the Characters list — it should NOT
        # open the Character Management dialog (that's what the + button
        # and clicking a specific character are for). Previously this was
        # connected to _on_open_character_management which opened the dialog
        # every time the user clicked the nav item.
        self._sidebar.characters_clicked.connect(lambda: self._on_nav_context_changed("Characters"))
        # P3.4: Wire nav signals for context-aware Recents
        self._sidebar.projects_clicked.connect(lambda: self._on_nav_context_changed("Projects"))
        self._sidebar.scenes_clicked.connect(lambda: self._on_nav_context_changed("Scenes"))
        # P3.10: characters_clicked is already connected above (line 443).
        # Removed the duplicate connection that called _on_nav_context_changed
        # a second time AND opened the character management dialog.
        # P3.5 Final Correction: History is now a PRIMARY navigation context.
        # Clicking "History" in the sidebar switches the context to show the
        # full History list (Recents + ALL HISTORY). It does NOT open the
        # HistoryView dialog — the dialog opens when a History ENTRY is
        # clicked (via recent_item_clicked → _on_recent_history_clicked).
        # The menu bar / toolbar History action still opens the dialog directly.
        self._sidebar.history_clicked.connect(lambda: self._on_nav_context_changed("History"))
        self._sidebar.presets_clicked.connect(self._on_open_preset_manager)
        self._sidebar.outputs_clicked.connect(self._on_open_output_folder)
        # Legacy signals (preserved for MainWindow compatibility)
        self._sidebar.voice_selected.connect(self._on_sidebar_voice_selected)
        self._sidebar.output_file_selected.connect(self._on_output_file_selected)
        self._sidebar.delete_voice_requested.connect(self._on_delete_voice)
        self._sidebar.export_voice_requested.connect(self._on_export_voice)
        self._sidebar.create_voice_requested.connect(self._on_create_voice)
        # L1: Removed dead _on_history_entry_selected connection.
        # The sidebar's history_entry_selected signal is declared but never
        # emitted — the active ProjectSceneSidebar uses recent_item_clicked
        # instead. The handler was dead code with missing else-branches for
        # missing audio. If the signal is ever revived, add proper warning
        # messages for missing audio files.
        self._sidebar.history_reuse_requested.connect(self._on_history_reuse)
        self._sidebar.history_delete_requested.connect(self._on_history_delete)
        self._sidebar.history_export_requested.connect(self._on_history_export)
        self._sidebar.history_project_changed.connect(self._on_history_project_changed)
        self._sidebar.apply_preset_requested.connect(self._on_apply_preset)
        self._sidebar.save_preset_requested.connect(self._on_save_current_as_preset)
        self._sidebar.delete_preset_requested.connect(self._on_delete_preset)
        self._sidebar.rename_preset_requested.connect(self._on_rename_preset)
        self._sidebar.export_preset_requested.connect(self._on_export_preset)
        # NOTE: Each preset signal is connected EXACTLY ONCE.
        # (An earlier revision had a duplicate block here that caused every
        # preset action to fire twice — double "save preset" prompts, etc.
        # See worklog Task ID 56 for the root-cause analysis.)

        # Waveform player
        self._waveform.play_requested.connect(self._on_play)
        self._waveform.pause_requested.connect(self._on_pause)
        self._waveform.stop_requested.connect(self._on_stop_playback)
        self._waveform.seek_requested.connect(self._on_seek)
        self._waveform.volume_changed.connect(self._on_volume_changed)

    # ------------------------------------------------------------------
    # Engine connection
    # ------------------------------------------------------------------
    def _connect_engine(self) -> None:
        """Connect to the Engine and subscribe to events."""
        if self._engine is None:
            return

        from engine.events import EventType

        # Subscribe to engine events
        self._engine.subscribe(EventType.MODEL_LOADED, self._on_model_loaded)
        self._engine.subscribe(EventType.MODEL_UNLOADED, self._on_model_unloaded)
        self._engine.subscribe(EventType.ENGINE_STATUS_CHANGED, self._on_engine_status)
        self._engine.subscribe(EventType.GENERATION_STARTED, self._on_generation_started)
        self._engine.subscribe(EventType.GENERATION_FINISHED, self._on_generation_finished)
        self._engine.subscribe(EventType.GENERATION_FAILED, self._on_generation_failed)
        self._engine.subscribe(EventType.VOICE_CHANGED, self._on_voices_changed)
        self._engine.subscribe(EventType.HISTORY_UPDATED, self._on_history_changed)

        # Load default generation parameters into the control panel
        defaults = self._engine.get_generation_defaults()
        self._control_panel.set_parameters(defaults)

        # Update model status
        status = self._engine.get_model_status()
        self._status_bar.update_model_status(status)

        # Restore the persisted project name (from settings.json) so it
        # survives application restarts.
        try:
            settings_dir = os.path.join(APP_ROOT, "settings")
            from engine.settings_manager import SettingsManager
            _sm = self._settings_manager
            persisted_project = _sm.get("application", "current_project", "Default")
            if persisted_project:
                self._current_project = persisted_project
                self._sidebar.set_project(persisted_project)
            # Restore the persisted current scene (separate from project).
            persisted_scene = _sm.get("application", "current_scene", "Untitled")
            if persisted_scene:
                self._current_scene = persisted_scene
            # Update the context bar with restored project/scene.
            if hasattr(self, "_project_scene_bar"):
                self._project_scene_bar.set_project(self._current_project)
                self._project_scene_bar.set_scene(self._current_scene)
            # Restore the persisted playback volume.
            persisted_volume = _sm.get("application", "playback_volume", 1.0)
            try:
                vol = float(persisted_volume)
            except (TypeError, ValueError):
                vol = 1.0
            self._waveform.set_volume(vol)
            if self._engine is not None:
                self._engine.set_playback_volume(vol)

            # --- First-run demo text (Issue: PLAIN TEXT DEMO CONTENT) ---
            # On the VERY FIRST application startup (settings.json did not
            # exist before this run), insert the Hungarian demo text into
            # the Plain Text editor so the user can immediately try Generate.
            # This ONLY happens once — the first_run_completed flag is set
            # to True and persisted, so subsequent startups never reinsert.
            first_run = _sm.get("application", "first_run_completed", False)
            if not first_run:
                current_text = self._editor.get_text().strip()
                if not current_text and not self._editor.is_raw_mode():
                    demo_text = (
                        "Ez egy rövid GUINEO teszt. Ezt a mintaszöveget "
                        "azért használjuk, hogy az alkalmazás első indításakor "
                        "azonnal kipróbálható legyen a hanggenerálás. Egy "
                        "folyamatos generálás körülbelül harminc másodpercig "
                        "ajánlott stabilan. Ha ennél hosszabb anyagot "
                        "szeretnél készíteni, érdemes a szöveget Narration "
                        "Blocks segítségével kisebb részekre bontani. Így "
                        "minden rész külön generálható, ellenőrizhető és "
                        "szükség esetén újra elkészíthető."
                    )
                    self._editor.set_text(demo_text)
                    logger.info("First run: demo text inserted into Plain Text editor")
            # Mark first run as completed — persist immediately.
            _sm.set("application", "first_run_completed", True)
        except Exception as exc:
            logger.warning("Could not load persisted settings: %s", exc)

        # --- Preset + batch managers ---
        presets_dir = os.path.join(APP_ROOT, "presets")
        self._preset_manager = PresetManager(presets_dir)
        self._refresh_presets()

        # P2.1: Initialize ProjectManager for authoritative project/scene persistence.
        projects_dir = os.path.join(APP_ROOT, "projects")
        self._project_manager = ProjectManager(projects_dir)

        # P3.4: Initialize RecentsManager for context-aware MRU lists.
        try:
            settings_dir = os.path.join(APP_ROOT, "settings")
            from engine.settings_manager import SettingsManager
            _sm = self._settings_manager
            self._recents_manager = RecentsManager(_sm)
        except Exception as exc:
            logger.warning("P3.4: Could not init RecentsManager: %s", exc)
            self._recents_manager = RecentsManager()

        # P2.1: Create a default in-memory Project if none exists yet.
        # This ensures the authoritative model is always available even
        # before the user explicitly creates or opens a project.
        if self._active_project is None:
            self._active_project = Project(name=self._current_project)
            # Add a default scene matching the current UI state.
            default_scene = Scene(
                name=self._current_scene,
                project_id=self._active_project.id,
                text=self._editor.get_text(),
            )
            self._active_project.add_scene(default_scene)
            self._active_scene = default_scene
            logger.info("P2: Created default in-memory Project '%s' with scene '%s'",
                        self._active_project.name, default_scene.name)

        # The batch manager calls ``submit_fn`` (= engine.generate) and
        # ``cancel_fn`` (= engine.cancel_generation).  All callbacks are
        # marshalled to the UI thread via QTimer.singleShot(0, fn).
        # The batch manager uses a Qt signal for thread-safe UI marshalling.
        # QTimer.singleShot(0, fn) from a worker thread does NOT work
        # because the worker thread has no Qt event loop.
        from ui.panels.batch_generation import _UiMarshaler
        self._batch_marshaler = _UiMarshaler(self)
        self._batch_manager = BatchManager(
            submit_fn=self._engine.generate,
            cancel_fn=self._engine.cancel_generation,
            marshal_to_ui=self._batch_marshaler.marshal,
        )

        # Register playback callbacks (position updates + natural finish).
        # These fire from the audio thread, so we marshal to the UI thread
        # via Qt signals (defined on MainWindow).
        self._engine.set_playback_position_callback(
            lambda pos, dur: self._playback_position_signal.emit(pos, dur))
        self._engine.set_playback_finished_callback(
            self._playback_finished_signal.emit)

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------
    def _offer_model_load(self) -> None:
        """Offer to load the model on startup if not already loaded."""
        if self._engine is None:
            return
        if self._engine.is_model_loaded:
            return

        # Check if the model files exist
        import os
        model_dir = os.path.join(APP_ROOT, "models", "higgs-audio-v3")
        weights_file = os.path.join(model_dir, "model.safetensors")
        if not os.path.isfile(weights_file):
            # Model not downloaded yet - show a non-blocking message
            self._status_bar.showMessage(
                "Model not downloaded. Run launch.bat to download it.", 10000)
            logger.warning("Model weights not found at %s", weights_file)
            return

        # Ask the user if they want to load the model now
        reply = QMessageBox.question(
            self, APP_NAME,
            "The model is downloaded but not loaded into memory.\n\n"
            "Load the model now? This takes 10-30 seconds and uses "
            "~10 GB of VRAM.\n\n"
            "You can also load it later by pressing Generate.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if reply == QMessageBox.StandardButton.Yes:
            self._load_model()

    def _load_model(self) -> None:
        """Load the model on a worker thread with a progress dialog."""
        if self._engine is None or self._engine.is_model_loaded:
            return

        # Pause the ambient background during model loading. The ambient
        # paintEvent (2 large QRadialGradient fills) is CPU-intensive, and
        # during model loading the UI thread is already busy with progress
        # dialog updates. Without this pause, the 20 FPS paint events pile
        # up and cause "QWidget::repaint: Recursive repaint detected" +
        # apparent freeze when the model finishes loading.
        if self._ambient_bg is not None:
            self._ambient_bg.pause()
            logger.debug("Ambient background paused for model loading")

        from ui.panels.model_load_dialog import ModelLoadDialog
        # P3.29: the dialog polls the (guaranteed non-blocking)
        # ModelManager status to surface the current load phase
        # ("Loading tokenizer..." / "Loading model weights..." / ...).
        self._model_load_dialog = ModelLoadDialog(
            self, status_fn=self._engine.get_model_status)
        self._model_load_dialog.load_cancelled.connect(self._on_load_cancelled)

        # P3.25 FIX (white/blank loading window): show AND paint the
        # dialog BEFORE submitting the load job. ``start()`` forces a
        # synchronous paint while this (GUI) thread still owns the GIL.
        # The previous order submitted the load first — the worker thread
        # immediately began ``import torch`` (which holds the GIL in long
        # C-extension stretches on Windows), starving the GUI thread so
        # the freshly-shown dialog could not receive its first paint
        # event — the user saw a white, content-free window with only
        # the OS-drawn title bar for the whole import.
        self._model_load_dialog.start()

        # Start the load on a worker thread. If submission itself fails,
        # close the dialog again (no stuck modal) and re-raise.
        try:
            self._engine.load_model(use_cuda=True,
                                    callback=self._on_model_load_done)
        except Exception:
            if self._model_load_dialog is not None:
                self._model_load_dialog.stop()
                self._model_load_dialog = None
            raise

    def _on_model_load_done(self, status) -> None:
        """Called when model loading completes (on the worker thread).

        Emits a signal which is automatically marshalled to the UI thread.
        QTimer.singleShot does NOT cross threads with a bare lambda, so
        we must use a Signal instead.
        """
        self._model_load_done_signal.emit(status)

    def _close_model_load_dialog(self, status) -> None:
        """Close the load dialog and update UI (on UI thread).

        The actual work is deferred to the next event-loop iteration via
        ``QTimer.singleShot(0, ...)``. This is critical: the ModelLoaded
        signal fires from a worker-thread callback, and if we do the
        dialog close + status bar update + ambient resume synchronously,
        the cascading repaints cause "QWidget::repaint: Recursive
        repaint detected" and "QBackingStore::endPaint() called with
        active painter" errors that freeze the UI.

        By deferring to the next iteration, the signal handler returns
        immediately, and the UI updates happen cleanly when the event
        loop is ready to process them.

        P3.29 FIX (stale deferred close kills the NEXT load's dialog):
        the deferred close is IDENTITY-GUARDED — it closes only the
        dialog instance that was current when the close was scheduled.
        Sequence (reproduced by ss/e2e/e2e_p329.py S7): the user cancels
        load #1 (dialog closes, the background load CONTINUES), then
        starts load #2 (a new dialog); load #1 finishes in the worker
        and its close is deferred; the deferred close used to run inside
        the new dialog's own ``start()`` (which pumps events to paint)
        and killed the NEW dialog — the load continued invisibly and
        the "auto-generate after load" chain never fired.
        """
        dlg = self._model_load_dialog
        QTimer.singleShot(
            0, lambda: self._do_close_model_load_dialog(status, dlg))

    def _do_close_model_load_dialog(self, status, dlg=None) -> None:
        """Actual implementation of _close_model_load_dialog (deferred).

        P3.29: ``dlg`` is the dialog instance this close was scheduled
        for. If a DIFFERENT dialog is current (a newer load started in
        the meantime), this close is stale and must be ignored.
        """
        current = self._model_load_dialog
        if current is None:
            return
        if current is not dlg:
            logger.debug(
                "Ignoring stale model-load dialog close "
                "(a newer load's dialog is open).")
            return
        self._model_load_dialog.stop()
        self._model_load_dialog = None

        # Update the status bar. This calls setStyleSheet on the StatusDot
        # and VramBar widgets, which triggers repaints. By deferring the
        # entire close to the next event-loop iteration (above), we ensure
        # these repaints don't cascade with the dialog close repaint.
        self._status_bar.update_model_status(status)
        if status.model_loaded:
            self._status_bar.showMessage("Model loaded successfully", 3000)
            logger.info("Model loaded: %s", status.gpu_name)
        else:
            QMessageBox.warning(
                self, APP_NAME,
                "The model could not be loaded.\n"
                "Check logs/engine.log for details.\n\n"
                "Common causes:\n"
                "- Not enough VRAM (need ~10 GB)\n"
                "- CUDA drivers out of date\n"
                "- Corrupt model files (re-run launch.bat to re-download)")

        # Resume the ambient background now that model loading is done.
        if self._ambient_bg is not None:
            self._ambient_bg.resume()
            logger.debug("Ambient background resumed after model load")

    def _on_load_cancelled(self) -> None:
        """User cancelled the model load."""
        logger.info("Model load cancelled by user.")
        self._model_load_dialog = None
        # Resume the ambient background (was paused in _load_model).
        # This is the cancellation path — resume() must be called to
        # balance the pause(), otherwise the ambient stays paused
        # permanently (requirement 10).
        if self._ambient_bg is not None:
            self._ambient_bg.resume()
            logger.debug("Ambient background resumed after model load cancelled")

    # ------------------------------------------------------------------
    # Sidebar data refresh
    # ------------------------------------------------------------------
    def _refresh_sidebar(self) -> None:
        if self._engine is None:
            return
        try:
            voices = self._engine.list_voices()
            self._sidebar.update_voices(voices)
            self._control_panel.populate_voices(voices)

            history = self._engine.get_history()
            self._sidebar.update_history(history)

            self._refresh_outputs()
            self._refresh_presets()
        except Exception as exc:
            logger.error("Failed to refresh sidebar: %s", exc)

    def _refresh_presets(self) -> None:
        """Reload the preset list from disk and push it to the sidebar.

        Also refreshes the voice_id -> voice_name lookup map so the
        sidebar (and the PresetManagerDialog) can display friendly voice
        names instead of opaque internal voice_ids.
        """
        if self._preset_manager is None:
            return
        try:
            # Build the voice lookup map for preset display.
            voice_lookup = {}
            if self._engine is not None:
                for v in self._engine.list_voices():
                    voice_lookup[v.id] = v.name
            # Push the lookup into the sidebar (legacy sidebar uses it
            # directly; project_scene_sidebar caches it for any dialog
            # that needs it).
            if hasattr(self._sidebar, "set_voice_lookup"):
                self._sidebar.set_voice_lookup(voice_lookup)
            presets = self._preset_manager.list()
            self._sidebar.update_presets(presets)
            # If the PresetManagerDialog is currently open, refresh it too.
            if (
                getattr(self, "_preset_dialog", None) is not None
                and hasattr(self._preset_dialog, "set_voice_lookup")
            ):
                self._preset_dialog.set_voice_lookup(voice_lookup)
        except Exception as exc:
            logger.error("Failed to refresh presets: %s", exc)

    def _refresh_outputs(self) -> None:
        """Scan the outputs/ directory for WAV files."""
        outputs_dir = os.path.join(APP_ROOT, "outputs")
        files = []
        if os.path.isdir(outputs_dir):
            for fname in sorted(os.listdir(outputs_dir), reverse=True):
                if fname.endswith(".wav"):
                    fpath = os.path.join("outputs", fname)
                    files.append((fname, fpath))
        self._sidebar.update_outputs(files)

    # ------------------------------------------------------------------
    # Prompt preview update
    # ------------------------------------------------------------------
    def _update_prompt_preview(self) -> None:
        """Rebuild the prompt preview from current selections.

        Also updates the NarrationEditor's global defaults so that
        Narration Blocks inherit the correct values.

        When Narration Blocks exist (and raw mode is off), the preview is
        built from the block overrides (via the editor's get_final_prompt)
        so that per-block Higgs tokens are reflected immediately.  When no
        blocks exist or raw mode is on, the engine's standard prompt
        builder is used (or the literal editor text in raw mode).
        """
        text = self._editor.get_text()
        if not text.strip():
            self._control_panel.update_prompt_preview("", [])
            return

        # Update the NarrationEditor's global defaults (blocks inherit these)
        self._editor.set_global_defaults(
            emotion=self._emotion,
            style=self._style,
            speed=self._speed,
            pitch=self._pitch,
            delivery=self._delivery,
        )

        # Build the unified prompt (Stage 1 verification logs SHA-256).
        try:
            final_prompt = self._build_prompt()
        except Exception as exc:
            logger.warning("main_window: Prompt build failed: %s", exc)
            self._control_panel.update_prompt_preview(text, [str(exc)])
            return

        # --- Stage 1: Pipeline verification (SHA-256) ---
        try:
            prompt_hash = hashlib.sha256(
                final_prompt.encode("utf-8")).hexdigest()
            logger.info(
                "PIPELINE VERIFY [Stage 1 - MainWindow._update_prompt_preview] "
                "prompt_sha256=%s len=%d",
                prompt_hash, len(final_prompt))
        except Exception:
            pass

        # P3.25 (audit SS-H07): compiler warnings (e.g. an invalid
        # emotion/style value was rejected) must reach the user — they were
        # previously discarded here (literal []). They are shown in the
        # Preview panel's warning area and, when present, the status bar.
        compile_warnings = list(getattr(self, "_last_prompt_warnings", []))
        self._control_panel.update_prompt_preview(final_prompt, compile_warnings)
        if compile_warnings:
            try:
                self._status_bar.showMessage(
                    "Prompt warning: {0}".format(compile_warnings[0]), 8000)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Unified prompt builder
    # ------------------------------------------------------------------
    def _build_prompt(self) -> str:
        """Return the final prompt string that will be sent to the engine.

        Uses the CanonicalPromptCompiler for ALL paths — there is no
        second prompt builder (Section 27/28).

        Decision tree (ADR 003):
            1. Raw mode ON → return editor text as-is (literal passthrough).
            2. Narration Blocks present → use CanonicalPromptCompiler.compile_continuous()
               with block overrides + global defaults.
            3. No blocks → use CanonicalPromptCompiler.compile_for_generate()
               with global defaults.

        P3.25 (audit SS-H07): every compile also records its warnings in
        ``self._last_prompt_warnings`` so callers (preview / generate) can
        surface them — invalid semantic values are NEVER silently dropped.

        NOTE: The Read Tokens / SemanticTokenState workflow has been REMOVED.
        token_state is always None — there is no inline token import path.
        Users who want literal Higgs tokens must use Raw Mode (ADR 003 §1, §8).
        """
        text = self._editor.get_text()
        self._last_prompt_warnings = []
        if self._editor.is_raw_mode():
            return text

        from engine.prompt_state import CanonicalPromptCompiler

        if self._editor.block_manager.has_blocks:
            # Narration Blocks: use the canonical continuous compiler
            # (Section 27 — replaces editor.get_final_prompt()).
            blocks = self._editor.block_manager.blocks
            params = self._control_panel.get_parameters()
            prompt_data = CanonicalPromptCompiler.compile_continuous(
                text=text,
                blocks=blocks,
                global_emotion=self._emotion,
                global_style=self._style,
                global_speed=self._speed,
                global_pitch=self._pitch,
                global_delivery=self._delivery,
                token_state=None,  # No inline token import (Read Tokens removed)
                allow_sfx=params.allow_sfx,
            )
            self._last_prompt_warnings = list(prompt_data.warnings or [])
            return prompt_data.final_prompt

        # Plain Text: use the canonical compiler with global defaults
        params = self._control_panel.get_parameters()
        prompt_data = CanonicalPromptCompiler.compile_for_generate(
            text=text,
            global_emotion=self._emotion,
            global_style=self._style,
            global_speed=self._speed,
            global_pitch=self._pitch,
            global_delivery=self._delivery,
            token_state=None,  # No inline token import (Read Tokens removed)
            allow_sfx=params.allow_sfx,
        )
        self._last_prompt_warnings = list(prompt_data.warnings or [])
        return prompt_data.final_prompt

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_editor_text_changed(self, text: str) -> None:
        """Handle editor text changes — update preview."""
        # NOTE: The stale token state check has been REMOVED.
        # The Read Tokens workflow is obsolete (ADR 003 §1).
        self._update_prompt_preview()

    # NOTE: The _on_read_tokens() handler has been REMOVED.
    # The Read Tokens workflow is obsolete (ADR 003 §1, §8).
    # Users who want literal Higgs tokens must use Raw Mode.

    def _on_generate_capability_changed(self, can_generate: bool, can_generate_long: bool) -> None:
        """Update Generate / Generate Long button states based on mode (Requirement 3).

        Plain Text: Generate ON, Generate Long ON
        Narration Blocks: Generate OFF, Generate Long ON
        Preview: Generate OFF, Generate Long OFF
        Raw Mode: Generate ON, Generate Long ON
        """
        self._top_nav.set_generate_enabled(can_generate)
        self._control_panel.set_generate_enabled(can_generate)
        # Generate Long button in the editor mode bar
        self._editor._generate_long_btn.setEnabled(can_generate_long)
        self._editor._generate_long_btn.setVisible(can_generate_long)
        logger.info("Generate capability: generate=%s, generate_long=%s",
                    can_generate, can_generate_long)

    def _on_block_selected(self, block_id: str) -> None:
        """Handle Narration Block selection — update Right Panel scope.

        When a block is selected:
        - If the block has an override, show the override value + source
        - If the block has no override, show the inherited global value + source
        - Control changes create/update the block override (not global)
        - P3.23 (design record §17): if the block has a Character with a
          Voice Profile, the visible Voice dropdown DISPLAYS the Character
          Voice with a "Character: <name>" source indicator. This is a
          display/fallback mechanism only — the underlying Scene Voice /
          manual selection is NOT modified (the character-driven display is
          transient and reverted on deselection; scene saves ignore it).

        When deselected (block_id=""):
        - Right Panel returns to global scope
        - The dropdown shows the Scene Voice again (§17)
        """
        self._apply_voice_display_for_block(block_id)
        if block_id:
            block = None
            for b in self._editor.block_manager.blocks:
                if b.id == block_id:
                    block = b
                    break
            if block:
                self._control_panel.set_block_scope(block_id)
                # Show source indicators
                self._control_panel.set_source_indicator(
                    "emotion", "Block Override" if block.emotion else "Global Default")
                # P3.39: update the panel display through the single honest
                # re-assertion path (block's effective values — signal-silent).
                self._refresh_panel_from_state()
        else:
            self._control_panel.set_block_scope(None)
            self._control_panel.set_source_indicator("", "")
            # Restore global values (P3.39: single honest re-assertion path)
            self._refresh_panel_from_state()

    def _apply_voice_display_for_block(self, block_id: str) -> None:
        """P3.23 (design record §17): Character-driven Voice dropdown DISPLAY.

        When the selected block carries a Character with a Voice Profile,
        the visible dropdown shows that voice + a "Character: <name>"
        source indicator (so the user sees which voice will be used). When
        no block is selected (or the block has no Character), the dropdown
        reverts to the Scene Voice / last manual selection.

        The display override is tracked in _voice_display_overridden so
        that (a) scene saves never persist the transient character voice
        as the Scene Voice, and (b) a manual dropdown change clears the
        override (§17: the dropdown is a display and fallback/override
        mechanism — the Character still wins for generation per §15).
        """
        self._voice_display_overridden = False
        self._control_panel.set_source_indicator("voice", "")
        if not block_id or self._active_project is None:
            return
        block = next((b for b in self._editor.block_manager.blocks
                      if b.id == block_id), None)
        if block is None:
            return
        char_id = getattr(block, "character_id", None)
        if not char_id:
            return
        char = self._active_project.get_character(char_id)
        if char is None or not char.voice_profile_id:
            return
        # Display the Character Voice (no signals — display only).
        self._control_panel.blockSignals(True)
        self._control_panel.set_selected_voice_id(char.voice_profile_id)
        self._control_panel.blockSignals(False)
        self._control_panel.set_source_indicator(
            "voice", "Character: {0}".format(char.name))
        self._voice_display_overridden = True

    def _on_emotion_changed(self, emotion: str) -> None:
        # P3.25 (audit SS-H02): with a Narration Block selected, a control
        # change writes ONLY the block override — the Scene-global value is
        # untouched (as documented in _on_block_selected). The global is
        # written exclusively when NO block is selected.
        # P3.26: the block scope is validated first — a stale scope (the
        # selected block was deleted, re-detected away, or belongs to
        # another Scene / editor mode) previously swallowed the change
        # into a phantom override (the "global emotion does nothing"
        # defect).
        # Empty string means the Friendly control was toggled OFF — convert
        # to None so PromptBuilder skips the emotion token (issue #34/#35).
        value = emotion if emotion else None
        block_id = self._block_scope_valid_id()
        if block_id:
            self._update_block_override('emotion', value, block_id)
        else:
            self._emotion = value
        self._update_prompt_preview()
        # P3.39: honest display — after a scope-routed write the panel must
        # show the value the prompt chain will ACTUALLY emit (the block's
        # effective value), not the raw widget state.
        self._refresh_panel_from_state()

    def _on_style_changed(self, style: str) -> None:
        # P3.25 (SS-H02): block-scoped edit — see _on_emotion_changed.
        # P3.26: stale-scope validation — see _on_emotion_changed.
        # Empty string means the Friendly control was toggled OFF — convert
        # to None so PromptBuilder skips the style token (issue #34/#35).
        value = style if style else None
        block_id = self._block_scope_valid_id()
        if block_id:
            self._update_block_override('style', value, block_id)
        else:
            self._style = value
        self._update_prompt_preview()
        self._refresh_panel_from_state()

    def _on_speed_changed(self, speed: str) -> None:
        # P3.25 (SS-H02): block-scoped edit — see _on_emotion_changed.
        # P3.26: stale-scope validation — see _on_emotion_changed.
        block_id = self._block_scope_valid_id()
        if block_id:
            self._update_block_override('speed', speed, block_id)
        else:
            self._speed = speed
        self._update_prompt_preview()
        self._refresh_panel_from_state()

    def _on_pitch_changed(self, pitch: str) -> None:
        # P3.25 (SS-H02): block-scoped edit — see _on_emotion_changed.
        # P3.26: stale-scope validation — see _on_emotion_changed.
        block_id = self._block_scope_valid_id()
        if block_id:
            self._update_block_override('pitch', pitch, block_id)
        else:
            self._pitch = pitch
        self._update_prompt_preview()
        self._refresh_panel_from_state()

    def _on_delivery_changed(self, delivery: str) -> None:
        # P3.25 (SS-H02): block-scoped edit — see _on_emotion_changed.
        # P3.26: stale-scope validation — see _on_emotion_changed.
        block_id = self._block_scope_valid_id()
        if block_id:
            self._update_block_override('delivery', delivery, block_id)
        else:
            self._delivery = delivery
        self._update_prompt_preview()
        self._refresh_panel_from_state()

    def _refresh_panel_from_state(self) -> None:
        """P3.39 (token-integrity audit): re-assert the Right Panel's
        semantic display from the AUTHORITATIVE state.

        Root cause fixed here (the "ghost emotion" the user could not
        remove): after a block-scoped change (e.g. the user clicked the
        Friendly "Neutral" pill while a Narration Block was selected),
        the panel widgets kept the RAW widget value (None) as their
        display — while the prompt chain still emitted the inherited
        GLOBAL emotion token for every block. The panel showed "no
        token selected" although ``<|emotion:affection|>`` was in the
        chain: an unremovable, invisible token.

        This re-assertion makes the panel always display what the chain
        will actually emit for the current scope:
          - block scope active → the block's EFFECTIVE value
            (override if set, otherwise the inherited global)
          - no block scope   → the global value
        The setters used here are SIGNAL-SILENT (P3.39 Fix 1), so this
        can never re-enter the change handlers.
        """
        try:
            block_id = self._block_scope_valid_id()
            block = None
            if block_id:
                block = self._editor.block_manager.get_block(block_id)

            def eff(attr, global_value):
                if block is not None:
                    v = getattr(block, attr, None)
                    if v is not None:
                        return v
                return global_value

            self._control_panel.blockSignals(True)
            try:
                self._control_panel.set_emotion(eff('emotion', self._emotion))
                self._control_panel.set_style(eff('style', self._style))
                self._control_panel.set_speed(eff('speed', self._speed) or 'Normal')
                self._control_panel.set_pitch(eff('pitch', self._pitch) or 'Normal')
                self._control_panel.set_delivery(
                    eff('delivery', self._delivery) or 'Normal')
            finally:
                self._control_panel.blockSignals(False)
        except Exception as exc:
            logger.warning("P3.39: panel display re-assert failed: %s", exc)

    def _block_scope_valid_id(self):
        """P3.26: validate the control panel's block scope BEFORE a
        semantic control change is routed into a block override.

        The scope is valid ONLY when:
          1. a block id is tracked, AND
          2. the editor is in Narration Blocks mode (in Plain Text /
             Preview mode the properties panel is hidden — the user is
             visibly editing the GLOBAL), AND
          3. the tracked id still exists in the current block model
             (re-detect, delete, and Scene switches replace block ids).

        A scope that fails validation is a stale phantom: previously it
        silently swallowed control changes (the "Advanced global emotion
        does nothing" defect — the value was written into a hidden block
        override or dropped entirely). It is cleared here and None is
        returned so the caller writes the GLOBAL value.
        """
        block_id = getattr(self._control_panel, "_active_block_id", None)
        if not block_id:
            return None
        if self._editor is None or self._editor._mode != self._editor.MODE_BLOCKS:
            self._control_panel.set_block_scope(None)
            return None
        if self._editor.block_manager.get_block(block_id) is None:
            logger.warning(
                "P3.26: stale block scope %r cleared (block no longer "
                "exists); control change applies to the global value",
                block_id)
            self._control_panel.set_block_scope(None)
            return None
        return block_id

    def _update_block_override(self, property_name: str, value,
                               block_id: str = None) -> None:
        """Create or update a Narration Block override (Section 39).

        When a block is selected and the user changes a control, this
        creates/updates the block's override instead of changing the global.
        """
        if block_id is None:
            block_id = self._control_panel._active_block_id
        if not block_id:
            return
        for block in self._editor.block_manager.blocks:
            if block.id == block_id:
                setattr(block, property_name, value)
                logger.info("Block override updated: block=%s, %s=%s",
                           block_id, property_name, value)
                self._update_prompt_preview()
                break

    def _on_sfx_inserted(self, sfx_name: str, onomatopoeia: str) -> None:
        """Insert an SFX marker at the cursor position.

        The marker {sfx:Laughter:Haha} is NOT a Higgs token - it is a
        placeholder that only the PromptBuilder can convert to the real
        token <|sfx:laughter|>Haha. This keeps the UI token-syntax-free
        (AI Development Rules section 9).
        """
        from engine.prompt_builder import PromptBuilder
        marker = PromptBuilder.make_sfx_marker(sfx_name, onomatopoeia)
        self._editor.insert_at_cursor(marker + " ")
        self._update_prompt_preview()

    def _on_pause_inserted(self, pause_type: str) -> None:
        """Insert a pause marker at the cursor position.

        The marker {pause} or {long_pause} is converted to the real Higgs
        token <|prosody:pause|> by the PromptBuilder.
        """
        from engine.prompt_builder import PromptBuilder
        marker = PromptBuilder.make_pause_marker(pause_type)
        self._editor.insert_at_cursor(marker + " ")
        self._update_prompt_preview()

    def _on_parameters_changed(self) -> None:
        """Generation parameters changed - could save as defaults."""
        pass  # Phase 7 will persist changed parameters

    def _on_voice_changed(self, voice_id: str) -> None:
        """Voice profile selection changed in the control panel dropdown.

        P3.23 (design record §17): a MANUAL dropdown change clears any
        character-driven display override — the user's explicit selection
        becomes the visible selection (the Character still controls the
        generated voice per precedence §15; the block-level override
        dialog is a documented later-polish phase).
        """
        if getattr(self, "_voice_display_overridden", False):
            self._voice_display_overridden = False
            self._control_panel.set_source_indicator("voice", "")
        if self._engine is None or not voice_id:
            self._status_bar.set_current_voice("None")
            self._control_panel.update_reference_audio(None)
            self._control_panel.set_reference_validation([])
            return
        voice = self._engine.get_voice(voice_id)
        if voice:
            self._status_bar.set_current_voice(voice.name)
            self._control_panel.set_voice_info(voice)
            self._control_panel.update_reference_audio(voice)
            warnings = self._engine.validate_voice_profile(voice_id)
            self._control_panel.set_reference_validation(warnings)

    def _on_sidebar_voice_selected(self, voice_id: str) -> None:
        """Voice selected from the sidebar list.

        This is the single entry point that synchronises:
            sidebar selection
          → ControlPanel voice combo (via the public set_selected_voice_id API)
          → status bar voice name
          → control panel voice info card
          → reference audio panel
          → reference validation badge
          → generation (via ControlPanel.get_selected_voice_id)

        All of the above refer to the same voice_id. We do NOT touch any
        private widget internals of ControlPanel — there is a public
        set_selected_voice_id(voice_id) that does the right thing
        (blocks signals so the cascade stops here).
        """
        if self._engine is None:
            return
        voice = self._engine.get_voice(voice_id)
        if not voice:
            QMessageBox.warning(
                self, APP_NAME,
                "Voice profile not found: {0}\n\n"
                "It may have been deleted. The voice list will be "
                "refreshed.".format(voice_id),
            )
            self._refresh_sidebar()
            return
        self._status_bar.set_current_voice(voice.name)
        self._control_panel.set_voice_info(voice)
        self._control_panel.update_reference_audio(voice)
        warnings = self._engine.validate_voice_profile(voice_id)
        self._control_panel.set_reference_validation(warnings)
        # Update the dropdown selection via the PUBLIC API (blocks signals
        # internally so voice_changed does not re-fire into this handler).
        self._control_panel.set_selected_voice_id(voice_id)
        logger.info(
            "Sidebar voice selected: voice_id=%s voice_name=%s "
            "reference_audio=%s",
            voice_id, voice.name, voice.reference_audio_path or "(none)",
        )

    def _on_sidebar_scene_selected(self, scene_id: str) -> None:
        """Scene selected from the sidebar Recent Scenes list.

        P2.2: Now uses _switch_to_scene which saves the current scene's
        state and loads the target scene's state. Previously this just
        changed the label without saving/restoring editor content.
        """
        if not scene_id:
            return
        # P2.2: Try to find the scene in the authoritative Project model.
        if self._active_project is not None:
            scene = self._active_project.get_scene(scene_id)
            if scene is not None:
                self._switch_to_scene(scene.id)
                return
        # Fallback: legacy sidebar scene list (scene_id is the name)
        scene_name = scene_id
        for scene in getattr(self._sidebar, "_scenes", []):
            if scene.get("id") == scene_id:
                scene_name = scene.get("name", scene_id)
                break
        # Try to find by name in the project
        if self._active_project is not None:
            for s in self._get_sorted_scenes():
                if s.name == scene_name:
                    self._switch_to_scene(s.id)
                    return
        # Last resort: just update the label (legacy behaviour)
        self._current_scene = scene_name
        self._scene_unsaved = True
        self._sidebar.set_active_scene(scene_id)
        if hasattr(self, "_project_scene_bar"):
            self._project_scene_bar.set_scene(scene_name)
            self._project_scene_bar.set_unsaved()
        logger.info("Scene switched via sidebar (legacy): %s", scene_name)
        self._status_bar.showMessage(
            "Switched to scene: {0}".format(scene_name), 3000)

    def _on_transcript_changed(self, transcript: str) -> None:
        """Deprecated wiring point (P3.43 §18): the ReferenceAudioPanel is
        a read-only display now — transcript editing belongs to the single
        Voice Profile editor. Kept as a no-op so any residual signal
        connection can never write state silently (P3.43 §29).
        """
        return

    def _on_output_file_selected(self, path: str) -> None:
        """Output file double-clicked in the sidebar."""
        if self._engine is not None:
            self._engine.play_audio(path)

    def _on_open_voice_library(self) -> None:
        """Open the unified Voice Profile management screen (P3.43).

        ONE authoritative manager: list/detail with Play / Replace
        Reference / Edit / Export / Delete + the shared Create/Import
        editor. Delete is dependency-aware — the scanner/clearer
        callbacks below operate on the ACTIVE PROJECT's Characters and
        Scenes (the authoritative references) and are passed in so the
        dialog never needs to know about project state.
        """
        if self._engine is None:
            return
        from ui.panels.voice_library import VoiceLibraryDialog
        dialog = VoiceLibraryDialog(
            self._engine, self,
            dependency_scanner=self._find_voice_dependencies,
            reference_clearer=self._clear_voice_references)
        dialog.voice_selected.connect(self._on_sidebar_voice_selected)
        dialog.exec()
        # The engine's VOICE_CHANGED notification already refreshed the
        # selectors during the dialog's lifetime; one final sync keeps
        # the info card honest after Delete/Select flows.
        self._refresh_sidebar()

    def _on_pick_voice(self) -> None:
        """Friendly tab 'Change' → the LIGHTWEIGHT voice picker (P3.43 §15).

        The picker is selection-focused; full management lives behind its
        explicit 'Manage Voice Profiles…' action which opens the unified
        manager. The RightPanel never becomes a management surface.
        """
        if self._engine is None:
            return
        from ui.panels.voice_picker import VoicePickerDialog
        voices = self._engine.list_voices()
        picker = VoicePickerDialog(
            voices,
            current_voice_id=self._control_panel.get_selected_voice_id(),
            app_root=self._engine.app_root,
            parent=self)
        picker.voice_selected.connect(self._on_sidebar_voice_selected)
        picker.manage_requested.connect(self._on_open_voice_library)
        picker.exec()

    # ------------------------------------------------------------------
    # P3.43 §11 — dependency-aware voice deletion helpers
    # ------------------------------------------------------------------
    def _find_voice_dependencies(self, voice_id: str) -> list:
        """Return human-readable references to the voice in the ACTIVE
        project (Characters + Scenes). Empty list = unreferenced."""
        dependencies = []
        project = self._active_project
        if project is None:
            return dependencies
        for char in project.characters:
            if char.voice_profile_id == voice_id:
                dependencies.append(
                    "Character: {0}".format(char.name))
        for scene in project.scenes:
            if scene.voice_profile_id == voice_id:
                dependencies.append(
                    "Scene: {0}".format(scene.name))
        return dependencies

    def _clear_voice_references(self, voice_id: str) -> None:
        """Clear Character/Scene references to a DELETED voice profile.

        Runs after the profile is actually gone; mutates the authoritative
        project entities and persists through the existing project/scene
        save paths — no silent dangling ids remain (P3.43 §11).
        """
        project = self._active_project
        if project is None:
            return
        changed = False
        for char in project.characters:
            if char.voice_profile_id == voice_id:
                char.voice_profile_id = None
                changed = True
        for scene in project.scenes:
            if scene.voice_profile_id == voice_id:
                scene.voice_profile_id = None
                changed = True
        if not changed:
            return
        try:
            self._project_manager.save_project(project)
            # The ACTIVE scene is persisted through the regular scene
            # state save (which also writes the cleared voice selection).
            self._save_current_scene_state()
            self._update_editor_characters()
            logger.info("Cleared voice references to deleted profile %s",
                        voice_id)
        except Exception as exc:
            logger.error("Failed to clear voice references: %s", exc)

    def _on_open_character_management(self) -> None:
        """P3.2: Open the Character Management dialog.

        Backed directly by the active Project's character list.
        Changes are made on the authoritative Project entity.

        P3.5 Final Correction: After the dialog closes, capture the selected
        character id (if any) so the Characters context list can highlight
        it as active. The id is sourced from the dialog's authoritative
        selection — no independent boolean.
        """
        if self._active_project is None:
            QMessageBox.information(self, APP_NAME,
                "No active project.\n\nCreate or open a project first.")
            return
        voices = self._engine.list_voices() if self._engine else []
        from ui.panels.character_dialog import CharacterManagementDialog
        dialog = CharacterManagementDialog(
            self._active_project, voices, self._active_scene, self)
        dialog.characters_changed.connect(lambda: self._save_current_scene_state())
        # P3.43 §16: "Manage Voice Profiles…" inside the Character dialog
        # opens the unified manager; afterwards the dialog's voice combo
        # is refreshed from the authoritative state (§28 refresh).
        def _manage_voices_from_characters():
            from ui.panels.voice_library import VoiceLibraryDialog
            manager = VoiceLibraryDialog(
                self._engine, self,
                dependency_scanner=self._find_voice_dependencies,
                reference_clearer=self._clear_voice_references)
            manager.exec()
            try:
                dialog.refresh_voices(
                    self._engine.list_voices() if self._engine else [])
            except Exception as exc:
                logger.warning("Character dialog voice refresh failed: %s", exc)
        dialog.manage_voices_requested.connect(_manage_voices_from_characters)
        dialog.exec()
        # P3.5 Final Correction: capture the selected character for active highlighting
        selected_id = getattr(dialog, 'selected_character_id', None)
        if selected_id:
            self._selected_character_id = selected_id
        logger.info("P3.2: Character management dialog closed (project='%s', %d characters)",
                     self._active_project.name, len(self._active_project.characters))
        # P3.22: Refresh the NarrationEditor's block Character selector
        # (characters may have been added/removed/renamed).
        self._update_editor_characters()
        # Refresh context list active state (and rebuild if characters changed)
        self._update_recents()

    def _update_editor_characters(self) -> None:
        """P3.22: Populate the NarrationEditor's block Character selector.

        Called when the active project changes or when characters are
        added/removed/renamed via the Character Management dialog.
        """
        if self._active_project is None:
            self._editor.set_characters([])
            return
        chars = [{"id": c.id, "name": c.name}
                 for c in self._active_project.characters]
        self._editor.set_characters(chars)
        # P3.23 (design record §10/§31): a deleted Character must leave no
        # orphan character_id on blocks — demote orphans to
        # lost_character_id so no real Character color is painted for a
        # missing Character and nothing silently lingers.
        self._cleanup_stale_block_characters()

    def _sync_scene_characters(self) -> None:
        """P3.23 (design record §14): maintain Scene.character_ids.

        When a Character is assigned to a block, its ID is added to the
        active Scene's character_ids automatically (if not already present).
        Removing a Character from all blocks does NOT remove it from the
        Scene or Project (design record §14) — auto-add only.
        """
        if self._active_scene is None or self._active_project is None:
            return
        try:
            valid_ids = {c.id for c in self._active_project.characters}
            for block in self._editor.block_manager.blocks:
                cid = getattr(block, "character_id", None)
                if cid and cid in valid_ids \
                        and cid not in self._active_scene.character_ids:
                    self._active_scene.character_ids.append(cid)
                    logger.info(
                        "P3.23: Character %s auto-added to Scene '%s' "
                        "(assigned to a block).", cid,
                        self._active_scene.name)
        except Exception as exc:
            logger.warning("_sync_scene_characters failed: %s", exc)

    def _cleanup_stale_block_characters(self) -> None:
        """P3.23 (design record §10/§31): no orphan Character IDs.

        Any block whose character_id no longer resolves to a Character in
        the active Project is demoted: character_id is cleared and retained
        as lost_character_id so the UI can show the re-assign warning
        instead of painting a real Character color for a deleted Character.
        """
        if self._active_project is None:
            return
        try:
            valid_ids = {c.id for c in self._active_project.characters}
            changed = False
            for block in self._editor.block_manager.blocks:
                cid = getattr(block, "character_id", None)
                if cid and cid not in valid_ids:
                    block.lost_character_id = cid
                    block.character_id = None
                    changed = True
                    logger.info(
                        "P3.23: block %s referenced deleted Character %s — "
                        "demoted to lost_character_id.", block.id, cid)
            if changed:
                self._editor.refresh_blocks()
        except Exception as exc:
            logger.warning("_cleanup_stale_block_characters failed: %s", exc)

    # ------------------------------------------------------------------
    # P3.4: Context-aware Recents
    # ------------------------------------------------------------------
    def _on_nav_context_changed(self, context: str) -> None:
        """P3.4: Handle primary navigation context change.

        Updates the Recents section in the sidebar to show items
        relevant to the selected context (Projects/Scenes/Characters/History).
        """
        self._current_nav_context = context
        self._update_recents()

    def _update_recents(self) -> None:
        """P3.4/P3.5: Refresh the Recents section AND the full context list.

        P3.5 Final Correction: The context list now receives an `active_id`
        derived from the authoritative MainWindow state so the active row
        is visually highlighted. No independent booleans are used.
        """
        if self._recents_manager is None:
            return
        context = self._current_nav_context

        # --- Recents (max 5 MRU) ---
        recent_entries = []
        if context == "Projects":
            for r in self._recents_manager.get_recent_projects():
                recent_entries.append({"id": r.entity_id, "name": r.display_name})
        elif context == "Scenes":
            for r in self._recents_manager.get_recent_scenes():
                name = r.display_name
                # P3.11 FIX: The subtitle should show the Project NAME (user-facing),
                # NOT the project_id (which is a UUID). Previously this passed
                # r.project_id as the subtitle, causing a long hex string to
                # appear next to the scene name in the Recents list.
                subtitle = ""
                if r.project_id:
                    if self._active_project and r.project_id == self._active_project.id:
                        subtitle = self._active_project.name  # same project
                    elif self._project_manager:
                        p = self._project_manager.get_project(r.project_id)
                        if p:
                            subtitle = p.name
                            if self._active_project and r.project_id != self._active_project.id:
                                name += " (other project)"
                recent_entries.append({"id": r.entity_id, "name": name, "subtitle": subtitle})
        elif context == "Characters":
            for r in self._recents_manager.get_recent_characters():
                name = r.display_name
                if r.subtitle:
                    name += " → " + r.subtitle
                # P3.11 FIX: Show Project NAME, not project_id UUID
                subtitle = ""
                if r.project_id:
                    if self._active_project and r.project_id == self._active_project.id:
                        subtitle = self._active_project.name
                    elif self._project_manager:
                        p = self._project_manager.get_project(r.project_id)
                        if p:
                            subtitle = p.name
                recent_entries.append({"id": r.entity_id, "name": name, "subtitle": subtitle,
                                       "character_id": r.entity_id})
        elif context == "History":
            for r in self._recents_manager.get_recent_history():
                recent_entries.append({"id": r.entity_id, "name": r.display_name, "subtitle": r.subtitle})
        self._sidebar.set_recents(recent_entries, context)

        # --- P3.5: Full Context List (all items, not limited) ---
        ctx_entries = self._build_context_list_entries(context)
        # P3.24 (§12): when no Project is active, the CHARACTERS panel
        # shows an explicit "no active project" empty state (no global
        # Characters — creation requires a Project context).
        empty_message = ""
        if context == "Characters" and self._active_project is None:
            empty_message = ("No active project.\n"
                             "Characters belong to a Project — open one to "
                             "manage its Characters.")
        # P3.5 Final Correction: derive the active entity id from
        # authoritative state for the current context.
        active_id = self._get_active_context_entity_id(context)
        self._sidebar.set_context_list(ctx_entries, active_id=active_id,
                                       empty_message=empty_message)
        # P3.15: Also refresh the Project/Scene dropdown menus in the top bar
        # so they stay synchronized with the current state.
        self._update_project_scene_bar_menus()

    def _get_active_context_entity_id(self, context: str) -> Optional[str]:
        """P3.5 Final Correction: Derive the active entity id for the context.

        Active state is derived SOLELY from authoritative MainWindow state:
          - Projects  → _active_project.id
          - Scenes    → _active_scene.id
          - Characters → _selected_character_id (from CharacterManagementDialog)
          - History   → _last_viewed_history_id (optional highlight)

        No independent booleans. Returns None if no active entity exists
        for the given context (clears the highlight).
        """
        if context == "Projects":
            return self._active_project.id if self._active_project else None
        elif context == "Scenes":
            return self._active_scene.id if self._active_scene else None
        elif context == "Characters":
            return self._selected_character_id
        elif context == "History":
            return self._last_viewed_history_id
        return None

    def _update_context_list_active(self) -> None:
        """P3.5 Final Correction: Lightweight refresh of the context list
        active state without rebuilding the entire list.

        Called after the active Project / Scene / Character changes
        (e.g. via _load_native_project, _switch_to_scene, or Character
        Management dialog close). The active id is derived from the
        current nav context's authoritative state.
        """
        if self._sidebar is None:
            return
        active_id = self._get_active_context_entity_id(self._current_nav_context)
        self._sidebar.set_active_context_item(active_id)

    def _build_context_list_entries(self, context: str) -> list:
        """P3.5/P3.6/P3.9: Build the full context list from authoritative data sources.

        P3.6: Scenes entries now include 'status' for the status badge.
        P3.9: Scenes and Characters are now SCOPED to the active Project
              (previously listed ALL scenes/characters across ALL projects,
              which caused confusion when switching projects). Projects and
              History remain global.
        """
        entries = []
        if context == "Projects":
            # All Projects from ProjectManager (global)
            if self._project_manager is not None:
                for p in self._project_manager.list_projects():
                    subtitle = "{0} scenes".format(len(p.scenes)) if p.scenes else ""
                    entries.append({"id": p.id, "name": p.name, "subtitle": subtitle})
        elif context == "Scenes":
            # P3.9: Scenes scoped to the ACTIVE Project only.
            # Previously listed all scenes from all projects — incorrect.
            if self._active_project is not None:
                for s in self._get_sorted_scenes():
                    entries.append({
                        "id": s.id,
                        "name": s.name,
                        "subtitle": "",  # same project — no subtitle needed
                        "status": s.status or "draft",
                    })
            elif self._project_manager is not None:
                # No active project — show all (fallback for first-run)
                for p in self._project_manager.list_projects():
                    for s in p.scenes:
                        entries.append({
                            "id": s.id,
                            "name": s.name,
                            "subtitle": p.name,
                            "status": s.status or "draft",
                        })
        elif context == "Characters":
            # P3.9/P3.24: Characters scoped to the ACTIVE Project only.
            # P3.24 (§12): when NO Project is active the panel shows a clear
            # disabled/empty state instead of a global character list —
            # Characters ALWAYS require a Project context.
            if self._active_project is not None:
                for c in self._active_project.characters:
                    voice_name = ""
                    if c.voice_profile_id and self._engine:
                        v = self._engine.get_voice(c.voice_profile_id)
                        if v:
                            voice_name = v.name
                    subtitle = ""
                    if voice_name:
                        subtitle = voice_name
                    entries.append({
                        "id": c.id,
                        "name": c.name,
                        "subtitle": subtitle,
                        # P3.22: pass character_id for the color dot
                        "character_id": c.id,
                        # P3.24: the current Voice Profile — drives the
                        # row dropdown selection in the management panel.
                        "voice_profile_id": c.voice_profile_id or "",
                    })
        elif context == "History":
            # All History entries from HistoryManager
            if self._engine is not None:
                for e in self._engine.get_history():
                    name = e.timestamp or "Unknown"
                    subtitle_parts = []
                    if e.project:
                        subtitle_parts.append(e.project)
                    if e.scene_name:
                        subtitle_parts.append(e.scene_name)
                    if e.speaker:
                        subtitle_parts.append(e.speaker)
                    if e.output_duration > 0:
                        subtitle_parts.append("{0:.1f}s".format(e.output_duration))
                    entries.append({
                        "id": e.id,
                        "name": name,
                        "subtitle": " · ".join(subtitle_parts) if subtitle_parts else "",
                    })
        return entries

    def _on_recent_item_clicked(self, context: str, entity_id: str) -> None:
        """P3.4 Finalization: Handle a click on a Recent item.

        Resolves the clicked item to its authoritative entity and
        activates it. Stale entries are cleaned up safely.
        """
        if context == "Projects":
            self._on_recent_project_clicked(entity_id)
        elif context == "Scenes":
            self._on_recent_scene_clicked(entity_id)
        elif context == "Characters":
            self._on_recent_character_clicked(entity_id)
        elif context == "History":
            self._on_recent_history_clicked(entity_id)

    def _on_recent_project_clicked(self, project_id: str) -> None:
        """Load a Project from Recents."""
        if self._project_manager is None:
            return
        project = self._project_manager.get_project(project_id)
        if project is None:
            # Stale — remove and refresh
            if self._recents_manager:
                self._recents_manager.remove_stale_project(project_id)
                self._update_recents()
            QMessageBox.information(self, APP_NAME,
                "This project is no longer available.\nIt may have been deleted.")
            return
        self._load_native_project(project)

    def _on_recent_scene_clicked(self, entity_id: str) -> None:
        """Load the Project + Scene from Recents or the full Context List.

        entity_id is the scene_id. We need to find which project it belongs to.

        P3.5 Final Correction: The click may come from the FULL context list
        (not just Recents). If the scene is NOT in RecentsManager, fall back
        to searching all Projects via ProjectManager. This ensures context
        list clicks resolve correctly even for scenes never added to Recents.
        """
        if self._recents_manager is None or self._project_manager is None:
            return
        # Find the scene entry in recents to get the project_id
        project_id = None
        scene_name = None
        for r in self._recents_manager.get_recent_scenes():
            if r.entity_id == entity_id:
                project_id = r.project_id
                scene_name = r.display_name
                break
        # P3.5 Final Correction: fallback — search all projects for the scene.
        # This handles clicks from the full Context List where the scene was
        # never added to Recents.
        if not project_id:
            for p in self._project_manager.list_projects():
                s = p.get_scene(entity_id)
                if s is not None:
                    project_id = p.id
                    scene_name = s.name
                    break
        if not project_id:
            return
        # Check if the project is already active
        if self._active_project is not None and self._active_project.id == project_id:
            # Same project — just switch scene
            self._switch_to_scene(entity_id)
            return
        # Different project — load it first
        project = self._project_manager.get_project(project_id)
        if project is None:
            if self._recents_manager:
                self._recents_manager.remove_stale_scene(project_id, entity_id)
                self._update_recents()
            QMessageBox.information(self, APP_NAME,
                "The project for this scene is no longer available.")
            return
        # Load the project
        self._load_native_project(project)
        # Now switch to the specific scene
        scene = project.get_scene(entity_id)
        if scene is not None:
            self._switch_to_scene(entity_id)
        else:
            # Scene was deleted from the project
            if self._recents_manager:
                self._recents_manager.remove_stale_scene(project_id, entity_id)
                self._update_recents()
            QMessageBox.information(self, APP_NAME,
                "Scene '{0}' is no longer in this project.".format(scene_name or entity_id))

    def _on_recent_character_clicked(self, entity_id: str) -> None:
        """Load the Project + SELECT the Character (Recents / Context List).

        entity_id is the character_id. We need the project_id.

        P3.5 Final Correction: The click may come from the FULL context list.
        If the character is NOT in RecentsManager, fall back to searching all
        Projects via ProjectManager.

        P3.24 UX Correction: clicking a Character now SELECTS it in the
        CHARACTERS management panel (highlight + MRU update) instead of
        opening the modal CharacterManagementDialog. The basic management
        workflow is inline; the dialog stays reachable via the row context
        menu ("Edit Details...").
        """
        if self._recents_manager is None or self._project_manager is None:
            return
        # Find the character entry in recents
        project_id = None
        char_name = None
        for r in self._recents_manager.get_recent_characters():
            if r.entity_id == entity_id:
                project_id = r.project_id
                char_name = r.display_name
                break
        # P3.5 Final Correction: fallback — search all projects.
        if not project_id:
            for p in self._project_manager.list_projects():
                c = p.get_character(entity_id)
                if c is not None:
                    project_id = p.id
                    char_name = c.name
                    break
        if not project_id:
            return
        # Track the selected character for active-state highlighting
        # (authoritative state — drives the row highlight).
        self._selected_character_id = entity_id
        # Check if the project is already active
        if self._active_project is not None and self._active_project.id == project_id:
            # Same project — select the character in the management panel
            # (P3.24: no modal dialog for the basic workflow).
            self._update_recents()
            return
        # Different project — load it first
        project = self._project_manager.get_project(project_id)
        if project is None:
            if self._recents_manager:
                self._recents_manager.remove_stale_character(project_id, entity_id)
                self._update_recents()
            QMessageBox.information(self, APP_NAME,
                "The project for this character is no longer available.")
            return
        self._load_native_project(project)
        # Check if the character still exists
        char = project.get_character(entity_id)
        if char is None:
            if self._recents_manager:
                self._recents_manager.remove_stale_character(project_id, entity_id)
                self._update_recents()
            QMessageBox.information(self, APP_NAME,
                "Character '{0}' is no longer in this project.".format(char_name or entity_id))
            return
        # Select the character in the CHARACTERS management panel
        # (P3.24: no modal dialog for the basic workflow).
        self._selected_character_id = entity_id
        # Update recents MRU
        self._recents_manager.add_recent_character(project_id, entity_id, char.name)
        self._update_recents()

    def _on_recent_history_clicked(self, entity_id: str) -> None:
        """Select a History entry from Recents or the full Context List.

        P3.34 UX CORRECTION (user decision): clicking a History entry
        now behaves like every other context list — it SELECTS the entry
        (highlight via the authoritative _last_viewed_history_id) and
        does NOT open the modal HistoryViewDialog. The full view opens
        explicitly via the ALL HISTORY header icon
        (open_history_view_requested → _focus_history), the View menu or
        the toolbar History action. No independent boolean state is
        introduced — the active-entry state remains _last_viewed_history_id.

        P3.44: clicking a History entry now also LOADS its audio into the
        bottom WaveformPlayer so it can be auditioned outside the History
        screen itself. Selection semantics are unchanged (P3.34 contract:
        select + MRU, no dialog); the player load is additive. Entries with
        a missing/unreadable file keep their selection and surface a
        non-blocking status-bar message (no modal dialog — click must
        never block the list workflow).
        """
        if self._engine is None:
            return
        # Verify the entry still exists
        entry = self._engine._history.get_entry(entity_id)
        if entry is None:
            if self._recents_manager:
                self._recents_manager.remove_stale_history(entity_id)
                self._update_recents()
            QMessageBox.information(self, APP_NAME,
                "This history entry is no longer available.")
            return
        # P3.5 Final Correction: track the selected entry for active
        # highlighting (selection ONLY — no dialog).
        self._last_viewed_history_id = entity_id
        self._update_context_list_active()
        # P3.44: load the entry's audio into the bottom transport.
        self._load_history_entry_into_player(entry)
        # Update MRU so Recent Audio reflects the interaction — the same
        # contract as the other contexts (clicking an entry refreshes
        # its MRU position).
        if self._recents_manager:
            display = "{0:.1f}s".format(entry.output_duration) if entry.output_duration > 0 else "Audio"
            subtitle = entry.project or ""
            if entry.scene_name:
                subtitle += " / " + entry.scene_name
            self._recents_manager.add_recent_history(entity_id, display, subtitle)
            self._update_recents()

    # ------------------------------------------------------------------
    # P3.44: History entry → bottom WaveformPlayer
    # ------------------------------------------------------------------
    def _load_history_entry_into_player(self, entry) -> None:
        """Load a History entry's audio into the bottom WaveformPlayer.

        P3.44: the History entry click no longer merely SELECTS the row —
        the entry's audio becomes the transport's current track so it can
        be auditioned from anywhere (sidebar, Recents, History screen
        playback button) with the normal Play/Pause/Stop/Seek controls.

        Contract:
        - output_path in HistoryEntry is app-root-relative (the same
          convention the engine's load_wav and history_view resolve
          against) — never resolve against the process CWD (P3.25 class
          of bugs).
        - Missing/unreadable file: keep the current transport content,
          surface a temporary status-bar message, do NOT open a modal
          dialog (the click must never block list navigation).
        - No output_path at all (legacy entry): informative status
          message only.
        - Playback is NOT auto-started: loading the waveform is the
          action; the user presses Play deliberately (matches the
          generation-finished flow which only auto-plays when the user
          asked for it via parameters).
        - Sample rate: use the entry's authoritative output_sample_rate;
          duration comes from output_duration (both recorded at
          generation time). The waveform peaks are read from the real
          file by the player itself.
        """
        if entry is None or self._waveform is None:
            return
        rel_path = entry.output_path or ""
        if not rel_path:
            self._status_bar.showMessage(
                "History entry has no audio file", 3000)
            return
        # App-root-relative resolution (never CWD-relative). The ENGINE's
        # app root is authoritative (tests boot MainWindow with a tmpdir
        # Engine); APP_ROOT is only the fallback.
        if os.path.isabs(rel_path):
            full_path = rel_path
        else:
            base = (self._engine.app_root if self._engine is not None
                    else APP_ROOT)
            full_path = os.path.join(base, rel_path)
        if not os.path.isfile(full_path):
            self._status_bar.showMessage(
                "Audio file not found: {0}".format(rel_path), 5000)
            return
        # Guard against the destructive "reload same track" case: loading
        # the same path again would reset the transport mid-playback. Only
        # (re)load when the track actually changes.
        if self._waveform.current_path != full_path:
            self._waveform.set_audio_info(
                entry.output_duration or 0.0,
                entry.output_sample_rate or 0,
                full_path,
            )
        # Reflect the authoritative sample rate in the status bar (same
        # surface the generation flow updates).
        if entry.output_sample_rate:
            self._status_bar.set_sample_rate(entry.output_sample_rate)

    def _on_open_preset_manager(self) -> None:
        """Open the Preset Manager dialog.

        This is the user-facing entry point for managing presets from the
        new ProjectSceneSidebar. The dialog lists presets with friendly
        voice names (not internal voice_ids) and delegates all actions
        back through the existing preset handlers via signals.

        Storage path displayed in the dialog header:
            <app_root>/presets/
        (matches PresetManager.directory and F-301_preset_system.md)
        """
        if self._preset_manager is None:
            QMessageBox.warning(
                self, APP_NAME,
                "Preset storage is not initialised.",
            )
            return
        from ui.panels.preset_manager_dialog import PresetManagerDialog
        # Build the voice lookup for friendly display.
        voice_lookup = {}
        if self._engine is not None:
            for v in self._engine.list_voices():
                voice_lookup[v.id] = v.name
        dialog = PresetManagerDialog(
            self._preset_manager, voice_lookup, self,
        )
        # Wire the dialog's signals to the existing handlers. Each signal
        # is connected exactly once here (the sidebar signals remain
        # connected for any legacy callers).
        dialog.apply_preset_requested.connect(self._on_apply_preset)
        dialog.save_preset_requested.connect(self._on_save_current_as_preset)
        dialog.delete_preset_requested.connect(self._on_delete_preset)
        dialog.rename_preset_requested.connect(self._on_rename_preset)
        dialog.export_preset_requested.connect(self._on_export_preset)
        dialog.import_preset_requested.connect(self._on_import_preset)
        # Keep a reference so _refresh_presets can refresh the open dialog
        # if voices change while it's open.
        self._preset_dialog = dialog
        dialog.raise_()
        dialog.activateWindow()
        dialog.exec()
        self._preset_dialog = None
        # Refresh the sidebar preset list after the dialog closes (in
        # case the user deleted/renamed anything).
        self._refresh_presets()

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def _on_undo(self) -> None:
        """Undo the last edit in the prompt editor."""
        editor = self._editor._editor  # NarrationEditor's internal QPlainTextEdit
        if editor.document().isUndoAvailable():
            editor.document().undo()

    def _on_redo(self) -> None:
        """Redo the last undone edit in the prompt editor."""
        editor = self._editor._editor  # NarrationEditor's internal QPlainTextEdit
        if editor.document().isRedoAvailable():
            editor.document().redo()

    def _on_generate(self) -> None:
        """User pressed Generate (Ctrl+Enter)."""
        if self._engine is None:
            QMessageBox.warning(self, APP_NAME,
                                "The engine is not available.\n"
                                "Make sure the application started correctly.")
            return

        text = self._editor.get_text()
        if not text.strip():
            QMessageBox.warning(self, APP_NAME,
                                "The prompt text is empty.\n"
                                "Enter some text to synthesise.")
            return

        # If the model is not loaded, offer to load it first
        if not self._engine.is_model_loaded:
            reply = QMessageBox.question(
                self, APP_NAME,
                "The model is not loaded. Load it now?\n\n"
                "This takes 10-30 seconds and uses ~10 GB of VRAM.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes)
            if reply != QMessageBox.StandardButton.Yes:
                return
            # Load the model, then generate when ready
            self._load_model_and_generate(text)
            return

        self._start_generation(text)

    def _load_model_and_generate(self, text: str) -> None:
        """Load the model, then start generation automatically."""
        # Pause the ambient background during model loading (same as
        # _load_model — see comment there for rationale).
        if self._ambient_bg is not None:
            self._ambient_bg.pause()
            logger.debug("Ambient background paused for model loading (generate)")

        from ui.panels.model_load_dialog import ModelLoadDialog
        # P3.29: same phase-poll wiring as _load_model.
        self._model_load_dialog = ModelLoadDialog(
            self, status_fn=self._engine.get_model_status)
        self._model_load_dialog.load_cancelled.connect(self._on_load_cancelled)

        # Use a signal to marshal the callback from the worker thread to
        # the UI thread. QTimer.singleShot does NOT cross threads.
        def on_done(status):
            self._model_load_for_gen_signal.emit(status, text)

        # P3.25 FIX (white/blank loading window): paint the dialog BEFORE
        # submitting the load job — see _load_model for the full rationale.
        self._model_load_dialog.start()
        try:
            self._engine.load_model(use_cuda=True, callback=on_done)
        except Exception:
            if self._model_load_dialog is not None:
                self._model_load_dialog.stop()
                self._model_load_dialog = None
            raise

    def _on_model_loaded_for_generation(self, status, text: str) -> None:
        """Called after model load completes when user wanted to generate."""
        # Close the dialog (deferred to next event-loop iteration to avoid
        # recursive repaints — see _close_model_load_dialog docstring).
        self._close_model_load_dialog(status)
        # Defer the generation start to the NEXT iteration after the dialog
        # close, so the dialog has time to close cleanly before we start
        # the generation (which will show its own progress in the status bar).
        if status.model_loaded:
            QTimer.singleShot(0, lambda: self._start_generation(text))
        else:
            QTimer.singleShot(0, lambda: QMessageBox.critical(
                self, APP_NAME,
                "Could not load the model. Generation cancelled.\n"
                "Check logs/engine.log for details."))

    def _start_generation(self, text: str) -> None:
        """Build the request and submit it to the engine."""
        # NOTE: The STALE state check has been REMOVED.
        # The Read Tokens workflow is obsolete (ADR 003 §1).
        # Build the unified prompt.  Stage 2 verification logs SHA-256.
        try:
            final_prompt = self._build_prompt()
        except Exception as exc:
            # Catch ALL exceptions (not just RuntimeError) — Issue 4:
            # Do NOT silently fall back to plain text. Surface the error.
            QMessageBox.critical(
                self, APP_NAME,
                "Prompt compilation failed:\n\n{0}\n\n"
                "Generation blocked. Check your settings and try again.".format(exc))
            return
        try:
            prompt_hash = hashlib.sha256(
                final_prompt.encode("utf-8")).hexdigest()
            logger.info(
                "PIPELINE VERIFY [Stage 2 - MainWindow._start_generation] "
                "prompt_sha256=%s len=%d",
                prompt_hash, len(final_prompt))
            # Log effective state
            logger.info(
                "GENERATE STATE: mode=%s, raw=%s, blocks=%s, "
                "emotion=%s, style=%s, speed=%s, pitch=%s, delivery=%s, "
                "final_prompt=%s",
                "plain" if not self._editor.is_raw_mode() else "raw",
                self._editor.is_raw_mode(),
                self._editor.block_manager.has_blocks,
                self._emotion, self._style, self._speed,
                self._pitch, self._delivery,
                final_prompt[:200],
            )
        except Exception:
            pass

        from engine.models import GenerationRequest
        request = GenerationRequest(
            text=final_prompt,
            emotion=self._emotion,
            style=self._style,
            speed=self._speed,
            pitch=self._pitch,
            delivery=self._delivery,
            voice_id=self._control_panel.get_selected_voice_id(),
            parameters=self._control_panel.get_parameters(),
            project=self._current_project,
        )

        # P3.45.2A — oversized-request preflight (single-output ceiling).
        # The classification and the hard-limit arithmetic live in ONE
        # place (engine/output_guard.preflight_generation_size); this call
        # site only supplies the ACTUAL request basis (the compiled
        # prompt) and the EFFECTIVE parameters, then surfaces the
        # verdict. BLOCKED = the request cannot fit one model output —
        # the user must acknowledge before anything is submitted.
        # WARNING = exactly at the ceiling (no margin) — informational
        # only. Either way the request is NEVER rewritten or split here,
        # and an abort happens BEFORE any state mutation (scene status,
        # submission, history).
        try:
            from engine.output_guard import (
                preflight_generation_size, preflight_display_message,
                PREFLIGHT_BLOCKED, PREFLIGHT_WARNING,
            )
            preflight = preflight_generation_size(
                text=final_prompt,
                max_new_tokens=request.parameters.max_new_tokens,
            )
            if preflight["state"] == PREFLIGHT_BLOCKED:
                reply = QMessageBox.question(
                    self, APP_NAME, preflight_display_message(preflight)
                    + "\n\nGenerate anyway?",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    logger.info(
                        "PREFLIGHT P3.45.2A: single generation declined "
                        "by user (est=%ss ceiling=%ss chars=%s)",
                        preflight.get("estimated_request_seconds"),
                        preflight.get("maximum_output_seconds"),
                        preflight.get("basis_chars"))
                    return
                logger.info(
                    "PREFLIGHT P3.45.2A: oversized single generation "
                    "acknowledged by user — proceeding unchanged "
                    "(est=%ss ceiling=%ss)",
                    preflight.get("estimated_request_seconds"),
                    preflight.get("maximum_output_seconds"))
            elif preflight["state"] == PREFLIGHT_WARNING:
                self._status_bar.showMessage(
                    "Note: this prompt is exactly at the single-output "
                    "limit (~{0:.0f}s). Generation proceeds with no "
                    "margin.".format(preflight["maximum_output_seconds"]),
                    8000)
        except Exception:
            # A preflight failure must never block a normal generation
            # (the same defensive posture as the P3.27B output guard).
            logger.debug("PREFLIGHT P3.45.2A: unavailable", exc_info=True)

        # P2.6: Set scene context on the request so HistoryEntry can carry it.
        # P2.8: Set scene status to "generating".
        if self._active_scene is not None:
            self._active_scene.status = "generating"
        # P2.6: The scene_id/scene_name will be picked up by the Engine
        # from the request's project field + the active scene. We store
        # them as attributes on the request (they'll flow through to
        # GenerationResult → HistoryEntry).
        if self._active_scene is not None:
            request.scene_id = self._active_scene.id
            request.scene_name = self._active_scene.name
        # C5 FIX: Propagate the selected Character / Speaker context into
        # the GenerationRequest. Previously _selected_character_id was
        # tracked only for sidebar highlighting but never sent to the engine,
        # causing AudioAsset + HistoryEntry to lose Character/Speaker linkage
        # for single-prompt Generate.
        # Architecture: Scene → Character → Voice Profile. The Character's
        # voice_profile_id is the intended voice, but the control panel
        # dropdown remains the authoritative voice selector (user can
        # override). Here we set character_id + speaker for context tracking;
        # we do NOT override voice_id (the dropdown wins).
        if self._selected_character_id is not None:
            request.character_id = self._selected_character_id
            # Resolve the character name for the speaker field
            if self._active_project is not None:
                char = self._active_project.get_character(self._selected_character_id)
                if char is not None:
                    request.speaker = char.name

        # Submit to the engine (runs on a worker thread)
        try:
            future = self._engine.generate(request)
            future.add_done_callback(self._on_generation_done)
        except RuntimeError as exc:
            QMessageBox.warning(self, APP_NAME,
                                "Cannot start generation: {0}".format(exc))

    def _on_generation_done(self, future) -> None:
        """Called when the generation future completes."""
        try:
            future.result()
        except Exception as exc:
            logger.error("Generation future error: %s", exc)

    def _on_stop(self) -> None:
        """User pressed Stop (Esc)."""
        if self._engine is not None:
            self._engine.cancel_generation()
            self._status_bar.set_generating(False)
            # Re-derive Generate capability from the current editor mode
            # (NOT blindly re-enable). In Narration Blocks mode, Generate
            # must remain OFF even after Stop.
            self._editor.refresh_generate_capability()
            self._toolbar.set_stop_enabled(False)
            self._status_bar.showMessage("Generation stopped", 3000)

    def _on_clear(self) -> None:
        self._editor.clear_text()
        self._waveform.clear()

    def _on_replay(self) -> None:
        """Replay the most recent output."""
        if self._engine is None:
            return
        path = self._waveform.current_path
        if path:
            self._engine.play_audio(path)
            # Switch to "playing" state so Pause/Stop are active and the
            # playhead tracks position.
            self._waveform.set_playback_state("playing")
        else:
            self._status_bar.showMessage("No audio to replay", 3000)

    def _on_play(self) -> None:
        """Play / Resume button in the waveform player."""
        if self._engine is None:
            return
        state = self._engine.get_playback_state()
        if state == "paused":
            # Resume from where we paused.
            self._engine.resume_playback()
            self._waveform.set_playback_state("playing")
        elif state == "stopped":
            # Start a fresh playback.
            path = self._waveform.current_path
            if path:
                self._engine.play_audio(path)
                self._waveform.set_playback_state("playing")
        # If already playing, do nothing.

    def _on_pause(self) -> None:
        """Pause button in the waveform player."""
        if self._engine is None:
            return
        state = self._engine.get_playback_state()
        if state == "playing":
            self._engine.pause_playback()
            self._waveform.set_playback_state("paused")

    def _on_stop_playback(self) -> None:
        """Stop button in the waveform player (stops playback, not generation)."""
        if self._engine is not None:
            self._engine.stop_playback()
        self._waveform.set_playback_state("stopped")

    def _on_seek(self, position_sec: float) -> None:
        """Seek to a position in the audio (waveform click)."""
        if self._engine is None:
            return
        state = self._engine.get_playback_state()
        if state == "stopped":
            # No active playback: start playing from the clicked position.
            path = self._waveform.current_path
            if path:
                self._engine.play_audio_from_position(path, position_sec)
                self._waveform.set_playback_state("playing")
                self._waveform.set_play_position(position_sec)
        else:
            # Playback active (playing or paused): just reposition.
            self._engine.seek_audio(position_sec)
            self._waveform.set_play_position(position_sec)

    def _on_playback_position(self, position_sec: float,
                              duration_sec: float) -> None:
        """UI-thread handler for playback position updates (~20 Hz)."""
        self._waveform.set_play_position(position_sec)

    def _on_playback_finished(self) -> None:
        """UI-thread handler for natural playback completion."""
        self._waveform.set_playback_state("stopped")
        # Reset the playhead to the start.
        self._waveform.set_play_position(0.0)

    def _on_volume_changed(self, volume: float) -> None:
        """Volume slider moved in the waveform player.

        Applies the volume to the engine (live + future playbacks) and
        persists it to settings.json so it survives restarts.
        """
        if self._engine is not None:
            self._engine.set_playback_volume(volume)
        try:
            settings_dir = os.path.join(APP_ROOT, "settings")
            from engine.settings_manager import SettingsManager
            sm = self._settings_manager
            sm.set("application", "playback_volume", volume)
        except Exception as exc:
            logger.warning("Could not persist playback_volume: %s", exc)

    # ------------------------------------------------------------------
    # Engine event handlers
    # ------------------------------------------------------------------
    def _on_model_loaded(self, event) -> None:
        # P3.25 (SS-M14): the ModelLoaded event fires from a worker
        # thread — QTimer.singleShot(0) created THERE never fires. Emit a
        # Qt signal instead; the queued connection runs the refresh on
        # the GUI thread (and defers it past cascading repaints).
        self._model_status_signal.emit()

    def _refresh_model_status(self) -> None:
        """Refresh the status bar's model status (deferred, GUI thread)."""
        if self._engine is not None:
            status = self._engine.get_model_status()
            self._status_bar.update_model_status(status)

    def _on_model_unloaded(self, event) -> None:
        self._model_status_signal.emit()

    def _on_engine_status(self, event) -> None:
        self._model_status_signal.emit()

    def _on_generation_started(self, event) -> None:
        self._status_bar.set_generating(True)
        self._toolbar.set_generate_enabled(False); self._top_nav.set_generate_enabled(False)
        self._toolbar.set_stop_enabled(True)
        self._control_panel.set_generate_enabled(False)
        self._status_bar.showMessage("Generating...", 0)

    def _on_generation_finished(self, event) -> None:
        """Called when generation finishes (may be on worker thread).

        Emits a signal to marshal the UI update to the UI thread.
        """
        self._generation_finished_signal.emit(event.data)

    def _on_generation_finished_ui(self, result) -> None:
        """Handle generation completion on the UI thread."""
        self._status_bar.set_generating(False)
        # Re-derive Generate capability from the current editor mode
        # (NOT blindly re-enable). In Narration Blocks mode, Generate
        # must remain OFF even after generation finishes.
        self._editor.refresh_generate_capability()
        self._toolbar.set_stop_enabled(False)
        self._status_bar.showMessage("Ready", 3000)
        if result and result.success:
            self._status_bar.set_generation_time(
                result.generation_time, result.realtime_factor)
            self._waveform.set_audio_info(
                result.output_duration, result.output_sample_rate,
                result.output_path)
            # Update the status bar's sample rate + format badges.
            self._status_bar.set_sample_rate(result.output_sample_rate)
            params = result.parameters
            if params:
                self._status_bar.set_output_format(params.output_format)
            if params and params.auto_play and result.output_path:
                self._engine.play_audio(result.output_path)
            self._refresh_sidebar()
            self._status_bar.showMessage(
                "Generated {0:.1f}s in {1:.1f}s (RTF {2:.2f})".format(
                    result.output_duration,
                    result.generation_time,
                    result.realtime_factor), 5000)
            # P2.7/P3.28: register the generated audio on its Scene.
            # THE ONE WRITER (P3.28 §23 / design record §6): every
            # successful generation — single, long, review, regen — flows
            # through engine.audio_provenance.register_generation_result,
            # which resolves the Scene by result.scene_id (NEVER by
            # whichever Scene happens to be active — closes defect D-3),
            # attaches the provenance tags (block_id / part_index /
            # part_version / generation_run), appends the asset to that
            # Scene's authoritative stream, and recomputes that Scene's
            # derived coverage state (closes defect D-2: a Scene is never
            # marked COMPLETE merely because one part finished).
            from engine.audio_provenance import register_generation_result
            # P3.44.1 §6: route to the OWNING project's Scene — a batch
            # may still be running for project A's Scene while the user
            # has switched to project B (the previous active-project +
            # active-scene fallback registered A's asset on B's scene).
            owner_project, owner_scene = self._scene_owner(
                getattr(result, "scene_id", None),
                fallback_project=self._active_project,
                fallback_scene=self._active_scene)
            asset = register_generation_result(
                owner_project, result, scene_fallback=owner_scene)
            if asset is not None:
                scene_obj = (owner_project.get_scene(asset["scene_id"])
                             if owner_project else None)
                logger.info(
                    "P3.28: generation result registered on scene '%s' "
                    "(path=%s, part=%s v%s, run=%s)",
                    getattr(scene_obj, "name", "?") if scene_obj else "?",
                    result.output_path, asset.get("part_index"),
                    asset.get("part_version"), asset.get("generation_run"))
                # P3.9: Refresh the sidebar scene list so the status badge
                # reflects the DERIVED coverage state.
                if hasattr(self, "_sidebar") and self._active_project is not None:
                    sidebar_scenes = [{"id": s.id, "name": s.name, "status": s.status}
                                      for s in self._get_sorted_scenes()]
                    self._sidebar.set_scenes(sidebar_scenes)
                    if self._active_scene is not None:
                        self._sidebar.set_active_scene(self._active_scene.id)
                # P3.45.1: the active Scene's asset stream just changed —
                # re-derive the editor's per-block generation-state badges
                # (a fully covered block flips to "done" immediately; a
                # multi-part block shows its new coverage).
                if (getattr(asset, "scene_id", None)
                        == getattr(self._active_scene, "id", None)):
                    self._sync_block_status_badges()
                # P3.4: Add to History Recents
                if self._recents_manager is not None:
                    display = "{0:.1f}s".format(result.output_duration)
                    subtitle = result.project or ""
                    if result.scene_name:
                        subtitle += " / " + result.scene_name
                    self._recents_manager.add_recent_history(
                        result.timestamp or "unknown", display, subtitle)
                    self._update_recents()

    def _on_generation_failed(self, event) -> None:
        """Called when generation fails (may be on worker thread).

        Emits a signal to marshal the UI update to the UI thread.
        """
        self._generation_failed_signal.emit(event.data)

    def _on_generation_failed_ui(self, result) -> None:
        """Handle generation failure on the UI thread."""
        self._status_bar.set_generating(False)
        # Re-derive Generate capability from the current editor mode
        # (NOT blindly re-enable). In Narration Blocks mode, Generate
        # must remain OFF even after generation fails.
        self._editor.refresh_generate_capability()
        self._toolbar.set_stop_enabled(False)
        self._status_bar.showMessage("Generation failed", 5000)
        # P3.28 (§5 / Rec 9): failure is per-slot. The Scene's coverage
        # is DERIVED from its assets — a failed (re)generation never
        # destroys previous successful versions, so a covered Scene
        # stays COMPLETE/PARTIAL. Only a Scene with ZERO covered slots
        # transitions to ERROR (last run had failures). Routed by
        # scene_id when available (P3.28 §23), never by whichever Scene
        # happens to be active.
        # P3.44.1 §6: the owner routing also covers a batch running for
        # ANOTHER project's Scene while the user has switched projects.
        scene_id = getattr(result, "scene_id", None) if result else None
        _owner, scene = self._scene_owner(
            scene_id, fallback_scene=self._active_scene)
        if scene is not None:
            from engine.audio_provenance import (
                scene_coverage_state, recompute_scene_status, SCENE_GENERATING,
            )
            # While a batch run is still in flight for this scene the
            # state stays GENERATING (the batch-end recompute decides
            # the final state); otherwise recompute with the failure
            # hint — zero coverage + failure = ERROR; ANY coverage keeps
            # its derived PARTIAL/COMPLETE state (previous versions stay
            # valid; a failed regen never degrades coverage).
            if scene.status != SCENE_GENERATING:
                recompute_scene_status(scene, last_run_failed=True)
            # P3.9: Refresh sidebar so the status badge reflects the
            # derived state.
            if hasattr(self, "_sidebar") and self._active_project is not None:
                sidebar_scenes = [{"id": s.id, "name": s.name, "status": s.status}
                                  for s in self._get_sorted_scenes()]
                self._sidebar.set_scenes(sidebar_scenes)
                self._sidebar.set_active_scene(self._active_scene.id)
            # P3.45.1: the failure recompute may have moved the ACTIVE
            # Scene to ERROR (zero coverage + failure) — re-derive the
            # per-block badges so affected blocks show the existing
            # error representation instead of a stale/absent state.
            if scene is self._active_scene:
                self._sync_block_status_badges()
        if result and result.errors:
            error = result.errors[0]
            # P3.44.1: BATCH job failures are surfaced in the Batch
            # table itself (FAILED pill + error tooltip + summary
            # counters + the log) — never as a stack of blocking modal
            # dialogs (one per failed job; a modal per row mid-run also
            # froze the UI flow). Single (user-initiated) generations
            # keep the modal: they have no table row to carry the error.
            batch_running = (self._batch_manager is not None
                             and self._batch_manager.is_running)
            if not batch_running:
                QMessageBox.critical(
                    self, APP_NAME,
                    "{0}\n\n{1}".format(
                        error.get("message", "Generation failed"),
                        error.get("suggested_action", "")))
            else:
                logger.warning(
                    "P3.44.1: batch job failed (surfaced in the Batch "
                    "table, no modal): %s",
                    error.get("message", "Generation failed"))

    def _on_voices_changed(self, event) -> None:
        self._refresh_sidebar()

    def _on_history_changed(self, event) -> None:
        """Engine HISTORY_UPDATED handler.

        P3.23 FIX (audit: thread affinity): the engine emits this event on
        the WORKER thread (inside _execute_generation). Touching widgets
        from a non-UI thread is unsafe. The handler now only emits a Qt
        signal — Qt automatically queues cross-thread signal emissions to
        the UI thread, where _refresh_sidebar runs.
        """
        self._history_changed_signal.emit()

    # ------------------------------------------------------------------
    # Preset handlers
    # ------------------------------------------------------------------
    def _on_apply_preset(self, name: str) -> None:
        """Apply a saved preset to the current control-panel state.

        Voice restoration policy:
            - If preset.voice_id is set AND the voice profile still exists,
              the voice is restored (combo, info card, reference audio,
              validation badge) and logged.
            - If preset.voice_id is set BUT the voice profile is missing,
              the application MUST NOT silently pick another voice. The
              user is shown a clear warning, the rest of the preset
              (emotion/style/prosody/parameters) is still applied, and the
              voice combo is left at "(No voice)".
            - If preset.voice_id is None, the voice combo is set to
              "(No voice)".
        """
        if self._preset_manager is None:
            return
        # Block preset application while a batch is running — modifying the
        # control panel state mid-generation can cause a freeze because the
        # generation worker reads the same state.
        if self._batch_manager is not None and self._batch_manager.is_running:
            QMessageBox.warning(
                self, APP_NAME,
                "Cannot apply a preset while a batch generation is running.\n\n"
                "Please stop or wait for the batch to finish first.")
            return
        preset = self._preset_manager.get(name)
        if preset is None:
            QMessageBox.warning(self, APP_NAME,
                                "Preset '{0}' not found.".format(name))
            return
        # Resolve the voice profile BEFORE mutating any UI, so we can warn
        # the user up-front about a missing voice.
        resolved_voice = None
        voice_missing = False
        if preset.voice_id:
            if self._engine is not None:
                resolved_voice = self._engine.get_voice(preset.voice_id)
            if resolved_voice is None:
                voice_missing = True

        if voice_missing:
            # CRITICAL: do NOT silently pick another voice. Warn the user
            # and leave the voice combo at "(No voice)".
            QMessageBox.warning(
                self, APP_NAME,
                "Preset '{0}' references voice \"{1}\", but that voice is "
                "no longer available.\n\n"
                "The voice selection has been left at \"(No voice)\". "
                "All other preset settings (emotion, style, prosody, "
                "parameters) have been applied.\n\n"
                "Re-create the voice profile or pick a different voice "
                "before generating.".format(name, preset.voice_id),
            )
            logger.warning(
                "Preset '%s' references missing voice_id=%s — applied "
                "non-voice settings only.",
                name, preset.voice_id,
            )

        # Batch the setter calls so we don't trigger N prompt-preview
        # rebuilds on the way.
        with self._control_panel.batch_update():
            self._control_panel.set_emotion(preset.emotion)
            self._control_panel.set_style(preset.style)
            self._control_panel.set_speed(preset.speed or "Normal")
            self._control_panel.set_pitch(preset.pitch or "Normal")
            self._control_panel.set_delivery(preset.delivery or "Normal")
            # Voice selection: only restore if the voice still exists.
            # If the voice is missing, leave the combo at "(No voice)".
            if not voice_missing:
                self._control_panel.set_selected_voice_id(preset.voice_id)
            else:
                self._control_panel.set_selected_voice_id(None)
            self._control_panel.set_parameters(preset.parameters)
        # Sync our internal state so the prompt preview reflects the new
        # emotion/style/prosody immediately.
        self._emotion = preset.emotion
        self._style = preset.style
        self._speed = preset.speed or "Normal"
        self._pitch = preset.pitch or "Normal"
        self._delivery = preset.delivery or "Normal"
        # Refresh voice info + reference audio card if the voice resolved.
        if resolved_voice is not None:
            self._control_panel.set_voice_info(resolved_voice)
            self._control_panel.update_reference_audio(resolved_voice)
            warnings = self._engine.validate_voice_profile(preset.voice_id)
            self._control_panel.set_reference_validation(warnings)
            self._status_bar.set_current_voice(resolved_voice.name)
            logger.info(
                "Preset applied: name=%s voice_id=%s voice_name=%s "
                "reference_audio=%s",
                name, preset.voice_id, resolved_voice.name,
                resolved_voice.reference_audio_path or "(none)",
            )
        else:
            self._status_bar.set_current_voice("None")
            self._control_panel.set_voice_info(None)
            self._control_panel.update_reference_audio(None)
            self._control_panel.set_reference_validation([])
            if not voice_missing:
                logger.info(
                    "Preset applied (no voice): name=%s voice_id=None", name,
                )
        self._control_panel.set_inheritance_mode("preset", source=name)
        self._update_prompt_preview()
        self._status_bar.showMessage("Applied preset: {0}".format(name), 3000)

    def _on_save_current_as_preset(self) -> None:
        """Capture the current settings as a new preset."""
        if self._preset_manager is None:
            return
        # Block preset saving while a batch is running — the control panel
        # state may be in flux and reading it could produce inconsistent
        # preset data or freeze the UI.
        if self._batch_manager is not None and self._batch_manager.is_running:
            QMessageBox.warning(
                self, APP_NAME,
                "Cannot save a preset while a batch generation is running.\n\n"
                "Please stop or wait for the batch to finish first.")
            return
        name, ok = QInputDialog.getText(
            self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            self._preset_manager.validate_name(name)
        except ValueError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        if self._preset_manager.get(name) is not None:
            reply = QMessageBox.question(
                self, APP_NAME,
                "A preset named '{0}' already exists. Overwrite?".format(name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        preset = Preset(
            name=name,
            voice_id=self._control_panel.get_selected_voice_id(),
            emotion=self._emotion,
            style=self._style,
            speed=self._speed,
            pitch=self._pitch,
            delivery=self._delivery,
            parameters=self._control_panel.get_parameters(),
        )
        try:
            saved_relpath = self._preset_manager.save(preset)
            saved_abspath = os.path.join(
                self._preset_manager.directory,
                os.path.basename(saved_relpath),
            )
            # Resolve the voice name for the log so the user can confirm
            # which voice got persisted (the internal voice_id is opaque).
            voice_name_for_log = "(none)"
            if preset.voice_id and self._engine is not None:
                v = self._engine.get_voice(preset.voice_id)
                if v is not None:
                    voice_name_for_log = v.name
            logger.info(
                "Preset saved: name=%s voice_id=%s voice_name=%s "
                "emotion=%s style=%s speed=%s pitch=%s delivery=%s "
                "file=%s",
                name,
                preset.voice_id or "(none)",
                voice_name_for_log,
                preset.emotion or "(none)",
                preset.style or "(none)",
                preset.speed or "(none)",
                preset.pitch or "(none)",
                preset.delivery or "(none)",
                saved_abspath,
            )
            self._refresh_presets()
            self._status_bar.showMessage(
                "Saved preset: {0}".format(name), 3000)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME,
                                 "Failed to save preset: {0}".format(exc))

    def _on_delete_preset(self, name: str) -> None:
        if self._preset_manager is None:
            return
        reply = QMessageBox.question(
            self, APP_NAME,
            "Delete preset '{0}'?".format(name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if self._preset_manager.delete(name):
                self._refresh_presets()
                self._status_bar.showMessage(
                    "Deleted preset: {0}".format(name), 3000)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME,
                                 "Failed to delete preset: {0}".format(exc))

    def _on_rename_preset(self, old_name: str) -> None:
        if self._preset_manager is None:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename Preset", "New name:", text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        try:
            self._preset_manager.rename(old_name, new_name.strip())
            self._refresh_presets()
            self._status_bar.showMessage(
                "Renamed preset to: {0}".format(new_name.strip()), 3000)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME,
                                 "Failed to rename preset: {0}".format(exc))

    def _on_import_preset(self) -> None:
        """Import a preset file from disk into the preset directory."""
        if self._preset_manager is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Preset",
            "", "Preset Files (*.yaml *.yml *.json);;All Files (*)")
        if not path:
            return
        try:
            import json as _json
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            if path.endswith(".json"):
                data = _json.loads(text)
            else:
                try:
                    import yaml
                    data = yaml.safe_load(text)
                except ImportError:
                    data = _json.loads(text)
            preset = Preset.from_dict(data if isinstance(data, dict) else {})
            if not preset.name:
                preset.name = os.path.splitext(os.path.basename(path))[0]
            self._preset_manager.save(preset)
            self._refresh_presets()
            QMessageBox.information(self, APP_NAME,
                                    "Imported preset: {0}".format(preset.name))
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME,
                                 "Failed to import preset: {0}".format(exc))

    def _on_export_preset(self, name: str = "") -> None:
        """Export a preset to a user-chosen file.

        If ``name`` is empty (e.g. when invoked from the File menu), the
        user is asked which preset to export.
        """
        if self._preset_manager is None:
            return
        if not name:
            presets = self._preset_manager.list()
            if not presets:
                QMessageBox.information(self, APP_NAME,
                                        "No presets available to export.")
                return
            items = [p.name for p in presets]
            chosen, ok = QInputDialog.getItem(
                self, "Export Preset", "Preset:", items, 0, False)
            if not ok or not chosen:
                return
            name = chosen
        preset = self._preset_manager.get(name)
        if preset is None:
            QMessageBox.warning(self, APP_NAME,
                                "Preset '{0}' not found.".format(name))
            return
        default_fname = "{0}.yaml".format(name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Preset", default_fname,
            "YAML Files (*.yaml *.yml);;JSON Files (*.json);;All Files (*)")
        if not path:
            return
        try:
            import json as _json
            data = preset.to_dict()
            data["schema_version"] = 1
            if path.endswith(".json"):
                with open(path, "w", encoding="utf-8") as fh:
                    _json.dump(data, fh, indent=2, ensure_ascii=False)
            else:
                try:
                    import yaml
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(yaml.safe_dump(data, sort_keys=False,
                                                allow_unicode=True))
                except ImportError:
                    with open(path, "w", encoding="utf-8") as fh:
                        _json.dump(data, fh, indent=2, ensure_ascii=False)
            FeedbackDialog.information(self, APP_NAME,
                                        "Preset exported",
                                        "Location: {0}".format(path))
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME,
                                 "Failed to export preset: {0}".format(exc))

    # ------------------------------------------------------------------
    # Batch generation
    # ------------------------------------------------------------------
    def _on_open_batch_generation(self) -> None:
        """Open the manual Batch Queue (Tools / Project menu).

        P3.35: ONE implementation, TWO workflows. This action opens the
        MANUAL queue (arbitrary jobs, queue persistence, Merge Completed
        Parts) — the Scene-aware workflow is Long Generation. The
        single-live-instance policy applies: an already-open Batch dialog
        in manual mode is focused (never duplicated); an open dialog in
        the other mode is cleanly replaced.
        """
        if self._batch_manager is None:
            QMessageBox.warning(self, APP_NAME,
                                "Batch generation is not available "
                                "(engine not connected).")
            return
        self._present_batch_dialog(scene=None)

    # ------------------------------------------------------------------
    # P3.35: one BatchGenerationDialog — shared wiring + single instance
    # ------------------------------------------------------------------
    def _wire_batch_dialog(self, dialog, scene_id: str = "") -> None:
        """Wire EVERY dialog-level Batch signal — ONE shared code path.

        Used by all three construction paths (Long Generation scene mode,
        Tools/Project manual Batch Queue, File→Load Dialogue Scene) so the
        wiring can never drift again. Audit finding: the menu path
        previously connected ZERO signals — per-row Play / Stop-playback /
        Regen and Merge were dead buttons. Scene-only signals are
        connected unconditionally: their buttons don't exist in manual
        mode, so the connections are harmless there.
        """
        dialog.concatenate_requested.connect(self._on_concatenate_requested)
        dialog.combine_scene_requested.connect(
            self._on_combine_scene_requested)
        dialog.use_version_requested.connect(self._on_use_version_requested)
        dialog.select_output_requested.connect(
            self._on_select_output_requested)
        dialog.play_requested.connect(self._on_batch_play)
        dialog.stop_playback_requested.connect(self._on_batch_stop_playback)
        dialog.regen_requested.connect(self._on_batch_regen)
        dialog.generate_selected_requested.connect(self._on_generate_selected)
        dialog.export_audio_requested.connect(self._on_batch_export_audio)
        # Completion callback: the manager has a SINGLE slot — it is set
        # here (never duplicated per handler), scoped to the workflow.
        bm = self._batch_manager
        if bm is None:
            return
        if scene_id:
            bm.set_on_batch_completed(
                lambda summary, _sid=scene_id:
                self._on_scene_batch_completed(_sid, summary))
        else:
            # Manual queue: clear any stale scene-completion callback so a
            # manual run can never trigger another Scene's recompute.
            bm.set_on_batch_completed(None)

    def _alive_batch_dialog(self):
        """The live batch dialog, or None (None/dead refs are dropped)."""
        dlg = getattr(self, "_batch_dialog", None)
        if dlg is None:
            return None
        try:
            import shiboken6
            if not shiboken6.isValid(dlg):
                self._batch_dialog = None
                return None
        except ImportError:
            try:
                _ = dlg.isVisible  # raises RuntimeError on a deleted QObject
            except RuntimeError:
                self._batch_dialog = None
                return None
        return dlg

    def _focus_existing_batch_dialog(self, want_scene_mode: bool,
                                     want_scene_id: str = ""):
        """P3.35 §3 + P3.41: single-live-instance policy.

        * same mode AND the same scene identity (manual ↔ manual, or a
          scene dialog already bound to the Scene being generated) →
          focus/raise/activate it and return it (NO second instance is
          ever created);
        * different mode, OR a scene-mode dialog bound to a DIFFERENT
          Scene than the one being generated → close it cleanly (a
          RUNNING batch is preserved — its queue is shared, the
          replacement dialog keeps showing live progress), clear the
          reference, return None.

        P3.41 (issue 2 — obsolete Batch window from Long Generation):
        the previous policy compared ONLY the mode. A scene dialog left
        open from Scene A was focused and REUSED when the user switched
        to Scene B and ran Long Generation: the window kept Scene A's
        title / coverage header / combined outputs / completion
        callback while the queue held Scene B's jobs — an obsolete,
        differently configured Batch Generation window that recomputed
        the WRONG Scene's coverage on completion. The scene identity is
        now part of the match: a Long Generation activation always ends
        up with a dialog bound to the Scene being generated.
        """
        dlg = self._alive_batch_dialog()
        if dlg is None:
            return None
        try:
            if dlg.is_scene_mode == want_scene_mode:
                same_scene = True
                if want_scene_mode:
                    want_id = (want_scene_id or "").strip()
                    have_id = ((dlg.scene.id if dlg.scene is not None
                                else "") or "").strip()
                    same_scene = bool(want_id) and bool(have_id) \
                        and want_id == have_id
                if same_scene:
                    dlg.show()
                    dlg.raise_()
                    dlg.activateWindow()
                    return dlg
            dlg.close_without_stopping_batch()
        except RuntimeError:
            pass
        dlg.deleteLater()
        self._batch_dialog = None
        return None

    def _close_batch_dialog_if_open(self) -> None:
        """Close any live batch dialog WITHOUT stopping its batch.

        Used by the queue-populating flows (Long Generation, Load Dialogue
        Scene): a fresh job list supersedes the open window, but a running
        generation must survive the hand-over (P3.35 §11).
        """
        dlg = self._alive_batch_dialog()
        if dlg is None:
            return
        try:
            dlg.close_without_stopping_batch()
        except RuntimeError:
            pass
        dlg.deleteLater()
        self._batch_dialog = None

    def _present_batch_dialog(self, scene=None, scene_id: str = "",
                              review_mode: bool = False):
        """P3.35: build + wire + show the ONE live BatchGenerationDialog.

        ``scene=None`` → manual Batch Queue; a Scene → scene mode. The
        shared SettingsManager is injected so geometry / column state
        persist in the application's settings namespace (never in the
        project file). Returns the shown dialog (None only when the
        engine/batch manager is unavailable).
        """
        bm = self._batch_manager
        if bm is None:
            return None
        # P3.41: the focus match includes the SCENE IDENTITY — an
        # open scene dialog bound to a different Scene is superseded
        # (rebuilt for the Scene being generated), never reused with
        # its obsolete context.
        focused = self._focus_existing_batch_dialog(
            want_scene_mode=(scene is not None),
            want_scene_id=(scene_id or
                           (scene.id if scene is not None else "")))
        if focused is not None:
            return focused
        # A queue-populating flow (or a mode switch) replaces the open
        # dialog; its callbacks are released cleanly first (no theft).
        self._close_batch_dialog_if_open()
        from ui.panels.batch_generation import BatchGenerationDialog
        voices = self._engine.list_voices() if self._engine is not None else []
        if scene is not None:
            dlg = BatchGenerationDialog(
                bm, voices, self,
                scene=scene, project=self._active_project,
                app_root=self._engine_app_root(), review_mode=review_mode,
                settings_manager=self._settings_manager)
        else:
            dlg = BatchGenerationDialog(
                bm, voices, self, app_root=self._engine_app_root(),
                settings_manager=self._settings_manager)
        self._wire_batch_dialog(dlg, scene_id=scene_id or "")
        self._batch_dialog = dlg
        dlg.show()
        return dlg

    def _on_generate_long_narration(self) -> None:
        """Split text into safe parts and generate via Batch."""
        if self._engine is None or self._batch_manager is None:
            return

        text = self._editor.get_text()
        if not text.strip():
            QMessageBox.warning(self, APP_NAME,
                                "Enter some text first.")
            return

        # NOTE: The STALE state check has been REMOVED.
        # The Read Tokens workflow is obsolete (ADR 003 §1).

        # Get current blocks and global settings
        blocks = self._editor.block_manager.blocks
        params = self._control_panel.get_parameters()
        voice_id = self._control_panel.get_selected_voice_id()
        # Detect Raw Mode: if ON, the user wrote literal Higgs tokens and
        # we must NOT prepend global emotion/style/prosody tokens.
        raw_mode = self._editor.is_raw_mode()

        # Split text — detect_speakers=True so "$SPEAKER:" lines are
        # recognized and each becomes its own SplitPart with .speaker set.
        # NOTE: the speaker_voice_map is NOT passed here — the mapping is
        # chosen by the user in the preview dialog (UI-based, no settings.json
        # editing). The splitter just detects the $SPEAKER: syntax.
        from engine.narration_splitter import NarrationSplitter
        splitter = NarrationSplitter()
        try:
            parts = splitter.split(
                text=text,
                blocks=blocks if blocks else None,
                global_emotion=self._emotion,
                global_style=self._style,
                global_speed=self._speed,
                global_pitch=self._pitch,
                global_delivery=self._delivery,
                base_parameters=params,
                raw_mode=raw_mode,
                detect_speakers=True,
                allow_sfx=params.allow_sfx,
            )
        except ValueError as exc:
            # Unknown speaker — should not happen now (no map passed), but
            # keep the handler for safety.
            QMessageBox.critical(
                self, APP_NAME,
                "Speaker detection error:\n\n{0}".format(str(exc)))
            return

        if not parts:
            QMessageBox.warning(self, APP_NAME,
                                "Could not split text into parts.")
            return

        # Get voice name for display (single-speaker fallback).
        voice_name = ""
        if voice_id and self._engine is not None:
            voice = self._engine.get_voice(voice_id)
            if voice:
                voice_name = voice.name

        # Load available voices for the speaker mapping dropdown.
        voices = self._engine.list_voices() if self._engine else []

        # Load the previously-saved speaker→voice mapping from settings so
        # the user doesn't have to reassign voices every time they open the
        # Narration dialog. This mapping is updated automatically when the
        # user clicks Generate — no manual settings.json editing required.
        saved_speaker_map = {}
        try:
            settings_dir = os.path.join(APP_ROOT, "settings")
            from engine.settings_manager import SettingsManager
            _sm = self._settings_manager
            saved_speaker_map = _sm.get("application", "speaker_voice_map", {}) or {}
        except Exception:
            pass

        # Show preview dialog — the dialog handles speaker→voice mapping UI.
        # P3.45.2A: the EFFECTIVE generation parameters are passed so the
        # dialog's per-part preflight markers use the actual request budget
        # (max_new_tokens is user-editable per request; the single source
        # of the limit math stays in engine/output_guard).
        from ui.panels.long_narration_dialog import LongNarrationDialog
        dialog = LongNarrationDialog(
            parts, voice_name, voices, voice_id, saved_speaker_map,
            allow_sfx=params.allow_sfx, parent=self,
            generation_parameters=params)

        def _on_generate_with_save(edited_parts, speaker_voice_map):
            # Save the speaker→voice mapping so it persists across dialog reopens.
            try:
                settings_dir = os.path.join(APP_ROOT, "settings")
                from engine.settings_manager import SettingsManager
                _sm = self._settings_manager
                _sm.set("application", "speaker_voice_map", speaker_voice_map)
            except Exception as exc:
                logger.warning("Could not save speaker_voice_map: %s", exc)
            # Now start the generation. P3.28 §10: when "Review before
            # generating" is checked the batch window opens in REVIEW MODE
            # (jobs stay pending; the user selects what to generate) —
            # the SAME pipeline either way, only auto-start differs.
            self._start_long_narration(
                edited_parts, voice_id, params, speaker_voice_map,
                review_mode=dialog.review_mode())

        dialog.generate_requested.connect(_on_generate_with_save)
        dialog.exec()

    def _start_long_narration(self, parts, default_voice_id, base_params,
                              speaker_voice_map=None,
                              review_mode: bool = False) -> None:
        """Start batch generation for long narration parts.

        If a part has a ``speaker`` and the speaker_voice_map contains a
        mapping for it, that voice_id is used for the part's BatchJob;
        otherwise the default_voice_id (from the control panel) is used.

        Concatenation is NO LONGER automatic — the user clicks the
        "Concatenate"/"Combine Scene" button in the Batch Generation
        dialog when ready.

        P3.28:
          - GENERATION STRUCTURE EVENT: the splitter run materialises the
            Scene's ``expected_audio_slots`` (only here and on re-detect —
            never on a keystroke; §0 product rule).
          - GENERATION RUN: one activation allocates a scene-scoped run id
            ("{scene8}-r{NNN}") carried by every job/asset/history entry.
          - VERSIONS: per-slot monotonic max+1 (asset-aware) with the
            disk-guard — a re-run can NEVER overwrite the previous run's
            audio (defect D-1 closed at the allocation layer).
          - COVERAGE: batch start sets the Scene to GENERATING (routed by
            scene_id); completion is DERIVED, never set here (D-2).
          - REVIEW MODE (P3.28 §10): when review_mode is True the batch
            window opens with jobs PENDING and checkboxes visible — the
            user selects what to generate and presses Generate. The SAME
            pipeline is used either way; only auto-start differs.
        """
        if self._engine is None or not self._engine.is_model_loaded:
            QMessageBox.warning(self, APP_NAME,
                                "Load the model first.")
            return

        if speaker_voice_map is None:
            speaker_voice_map = {}

        try:
            from engine.models import GenerationParameters
            from engine.batch_manager import BatchJob
            from engine.audio_provenance import (
                materialize_expected_slots, allocate_generation_run,
                next_slot_version, block_slot_id, plain_slot_id,
                recompute_scene_status, SCENE_GENERATING,
            )

            bm = self._batch_manager
            bm.clear_all()

            scene = self._active_scene
            scene_id_full = scene.id if scene else None
            # P3.28 §3: materialise the expected slot structure (the ONE
            # structure event — Generate Long preview). Slots replace any
            # previous structure; coverage is derived against the CURRENT
            # structure (legacy scenes keep legacy semantics until now).
            slots = materialize_expected_slots(parts)
            slot_by_index = {s["part_index"]: s for s in slots}
            if scene is not None:
                scene.expected_audio_slots = slots
            # P3.28 §6: allocate this activation's generation run id.
            generation_run = (allocate_generation_run(scene)
                              if scene is not None else None)

            # Add jobs — each BatchJob may have a different voice_id if the
            # part has a speaker mapped in speaker_voice_map.
            for i, part in enumerate(parts):
                job_params = GenerationParameters(
                    temperature=base_params.temperature,
                    top_p=base_params.top_p,
                    top_k=base_params.top_k,
                    max_new_tokens=base_params.max_new_tokens,
                    seed=base_params.seed,
                    append_silence=0.5,
                    normalize_output=base_params.normalize_output,
                    auto_play=False,
                    output_format=base_params.output_format,
                    # P3.25 (audit SS-M02): forward the Allow SFX policy —
                    # the copy previously omitted the flag, so Generate
                    # Long parts always re-emitted SFX tokens even when the
                    # user had suppressed them.
                    allow_sfx=base_params.allow_sfx,
                )
                # Resolve the voice_id for this part.
                # P3.23 Voice Precedence (design record §19/§23 — Character
                # is authoritative when assigned to the block; the legacy
                # speaker map may NOT override an explicit Character):
                #   1. Block Character (part.character_id → character.voice_profile_id)
                #   2. Speaker Voice Map (speaker_voice_map.get(speaker)) —
                #      only consulted when the Character resolved NO voice
                #   3. Control Panel dropdown (default_voice_id)
                part_voice_id = default_voice_id
                part_name = "Part {0}".format(i + 1)
                part_speaker = getattr(part, "speaker", "") or None
                part_character_id = getattr(part, "character_id", None)
                # Resolve the Character object first (may be None).
                char = None
                if part_character_id and self._active_project is not None:
                    char = self._active_project.get_character(part_character_id)
                # 1. Block Character voice resolution — when the Character
                #    has a Voice Profile, it WINS and speaker_voice_map is
                #    not consulted for this part (design record §19).
                if char is not None and char.voice_profile_id:
                    part_voice_id = char.voice_profile_id
                    part_name = "{0}: {1}".format(char.name, i + 1)
                # 2. Speaker voice map — only when the Character resolved
                #    no voice (no Character, or Character without a Voice
                #    Profile). This fixes the P3.22 edge case where the
                #    speaker map could override a Character whose voice
                #    happened to equal the dropdown default.
                elif part_speaker and speaker_voice_map:
                    mapped = speaker_voice_map.get(part_speaker)
                    if mapped:
                        part_voice_id = mapped
                        part_name = "{0}: {1}".format(part_speaker, i + 1)
                # 3. Design record §23: "If Character exists without
                #    $SPEAKER: speaker is populated with Character name for
                #    context and History."
                if char is not None and not part_speaker:
                    part_speaker = char.name
                elif char is not None:
                    # Character + $SPEAKER coexist: Character provides the
                    # voice (handled above); $SPEAKER remains speaker context
                    # (design record §19) — keep part_speaker as-is.
                    part_name = "{0}: {1}".format(char.name, i + 1)
                # P3.17/P3.27B/P3.28: Human-friendly, VERSIONED audio
                # filenames.
                # Format:
                #   <Project>_<Scene>_Long_v01_Part_001.wav        (single)
                #   <Project>_<Scene>_Long_v01_Part_001_<spk>.wav   (multi)
                # The version slot guarantees a regenerated part can NEVER
                # overwrite the previous good version (P3.27B §18; P3.28
                # §7) — the allocator bumps vNN until the filename is free
                # on disk. P3.28: the START version is asset-aware (max
                # part_version among the slot's existing assets + 1), so
                # even deleted files never cause version reuse.
                from engine.output_guard import normalize_name_component

                proj_name = normalize_name_component(self._current_project or "Project")
                scene_name = normalize_name_component(
                    scene.name if scene else "Scene")

                part_index = i + 1
                slot = slot_by_index.get(part_index)
                slot_id = slot["slot_id"] if slot else (
                    plain_slot_id(part_index))
                if scene is not None and slot is not None:
                    filename, part_version = next_slot_version(
                        scene, slot_id, self._engine_outputs_dir(),
                        project=proj_name, scene_name=scene_name,
                        part_index=part_index, speaker=part_speaker,
                        scene_id=scene_id_full)
                else:
                    from engine.output_guard import next_free_part_filename
                    filename, part_version = next_free_part_filename(
                        self._engine_outputs_dir(),
                        project=proj_name, scene=scene_name,
                        part_index=part_index, speaker=part_speaker,
                        scene_id=scene_id_full)
                job = BatchJob(
                    name=part_name,
                    prompt=part.prompt,
                    voice_id=part_voice_id,
                    parameters=job_params,
                    output_filename=filename,
                    project=self._current_project,
                    speaker=part_speaker,  # dedikált speaker mező
                    # C2 FIX: propagate scene/character context so they
                    # survive into HistoryEntry via to_request().
                    scene_id=scene_id_full,
                    scene_name=scene.name if scene else None,
                    # P3.23: use ONLY the part's block character_id so each
                    # part links to the correct character for HistoryEntry +
                    # AudioAsset. The sidebar selection is a UI viewing
                    # context, not a block-level generation decision, and must
                    # not be attached to parts that have no block Character.
                    character_id=part_character_id,
                    # P3.27B: part provenance for the output guard (expected
                    # duration from the splitter's estimate) + history
                    # traceability (part index + allocated version).
                    part_index=part_index,
                    part_version=part_version,
                    expected_duration=getattr(part, "estimated_duration", None) or None,
                    # P3.28: provenance — source block (closes D-5),
                    # generation run, and slot id.
                    source_block_id=getattr(part, "source_block_id", None),
                    generation_run=generation_run,
                    slot_id=slot_id,
                )
                bm.add_job(job)

            # P3.28 §5: batch start = GENERATING (routed by scene_id —
            # switching Scenes mid-batch cannot corrupt another Scene's
            # state). Completion is derived later; starting a generation
            # is NEVER completion (D-2).
            if scene is not None:
                recompute_scene_status(scene, generating=True)
                if hasattr(self, "_sidebar") and self._active_project is not None:
                    sidebar_scenes = [{"id": s.id, "name": s.name,
                                       "status": s.status}
                                      for s in self._get_sorted_scenes()]
                    self._sidebar.set_scenes(sidebar_scenes)
                # P3.45.1: the run started AND the expected-slot structure
                # was just (re)materialised — push the derived per-block
                # badges now (blocks with jobs show the generating state;
                # already-covered blocks keep "done": their previous
                # versions remain valid through the run).
                self._sync_block_status_badges()

            # Open batch dialog. Concatenation is now a manual button in
            # the dialog — no auto-callback that could freeze the UI.
            # P3.35: single live instance (any open Batch dialog is
            # cleanly replaced, its callbacks released first) + ONE
            # shared wiring helper for every construction path.
            self._batch_dialog = self._present_batch_dialog(
                scene=scene, scene_id=scene_id_full or "",
                review_mode=review_mode)
            if self._batch_dialog is None:
                return
            # Log every BatchJob's prompt before starting — for verification
            # that tokens are present in each independent generation call.
            for i, job in enumerate(bm._jobs):
                logger.info(
                    "BatchJob %d BEFORE START: name=%s, speaker=%s, voice_id=%s, "
                    "part=%s v=%s, run=%s, slot=%s, prompt=%s",
                    i, job.name,
                    getattr(job, 'speaker', '(none)'),
                    job.voice_id,
                    job.part_index, job.part_version,
                    job.generation_run, job.slot_id,
                    job.prompt[:120],
                )
            if review_mode:
                # P3.28 §10 review mode: jobs stay PENDING; the user
                # selects what to generate in the batch window and
                # presses Generate there. Same pipeline — no start().
                self._status_bar.showMessage(
                    "Review mode: {0} parts queued — select and press "
                    "Generate in the Batch window.".format(len(bm.jobs)),
                    5000)
            else:
                # P3.44.1: start() False is never a silent no-op (the
                # queue hand-over path ACCEPTS a replaced queue, so a
                # False means a genuine double-start).
                if not bm.start():
                    QMessageBox.information(
                        self, APP_NAME,
                        "A batch generation is already running.\n\n"
                        "Wait for it to finish or press Stop first.")
                    return
            # Force an immediate button refresh so the Start button disables
            # and Pause/Stop enable the instant the batch starts running.
            # Without this, the buttons only update when the first
            # marshal_to_ui callback arrives (which can be delayed).
            self._batch_dialog._update_buttons()
            self._batch_dialog._refresh_table()
            self._batch_dialog._update_summary()
        except Exception as exc:
            # Qt can swallow exceptions raised inside signal handlers, so we
            # log explicitly and surface the error to the user.
            logger.exception("Failed to start long narration: %s", exc)
            QMessageBox.critical(
                self, APP_NAME,
                "Failed to start long narration:\n{0}\n\n"
                "See logs/application.log for details.".format(exc))

    def _on_scene_batch_completed(self, scene_id, summary) -> None:
        """P3.28 §5: the FINAL coverage recompute for a Scene's batch run.

        Runs on the UI thread (BatchManager._finish_batch invokes the
        callback there). Completed jobs keep their assets (registered by
        the single writer); pending jobs became SKIPPED (uncovered). The
        Scene's derived state lands honestly on COMPLETE / PARTIAL /
        ERROR — a Scene that was COMPLETE before a partial regen STAYS
        COMPLETE (nothing was invalidated).
        """
        try:
            if self._active_project is None or not scene_id:
                return
            # P3.44.1 §6: the batch's Scene may belong to ANOTHER
            # project (the user switched during the run) — resolve the
            # OWNER, not the active project (the active project's
            # get_scene returned None and the final coverage recompute
            # was silently SKIPPED).
            scene = None
            scene = self._active_project.get_scene(scene_id)
            if scene is None:
                _owner, scene = self._scene_owner(
                    scene_id, fallback_scene=self._active_scene)
            if scene is None:
                return
            from engine.audio_provenance import recompute_scene_status
            failed = bool(summary and getattr(summary, "failed", 0))
            recompute_scene_status(scene, last_run_failed=failed)
            if hasattr(self, "_sidebar"):
                sidebar_scenes = [{"id": s.id, "name": s.name,
                                   "status": s.status}
                                  for s in self._get_sorted_scenes()]
                self._sidebar.set_scenes(sidebar_scenes)
            if self._batch_dialog is not None:
                self._batch_dialog.refresh_scene_context()
            # P3.45.1: the run ended — the final coverage recompute just
            # cleared SCENE_GENERATING, so the editor badges settle to
            # their truthful end state (done / error / no badge).
            if scene is self._active_scene:
                self._sync_block_status_badges()
            logger.info(
                "P3.28: batch completed for scene '%s' — derived status=%s "
                "(completed=%s failed=%s skipped=%s)",
                scene.name, scene.status,
                getattr(summary, "completed", "?"),
                getattr(summary, "failed", "?"),
                getattr(summary, "skipped", "?"))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("P3.28: batch-completed recompute failed: %s", exc)

    def _sync_block_status_badges(self) -> None:
        """P3.45.1: push the DERIVED per-block generation state into the
        editor's existing gutter status badges.

        This is the wiring the P3.45 triage found dead: the badge painter
        and the ``update_block_status`` API existed but had ZERO callers,
        while the tested data source (``block_slot_states``) was consumed
        only by tests. The chain is now:

            provenance state (slots × append-only assets)
              → block_slot_states (existing, tested)
              → this sync (pure derivation, state-transition driven)
              → editor update_block_status (existing API)
              → existing badge/progress painter

        Per block (never stored — re-derived on every call):

          covered == total            → "done"       ("✓ N/N" multi-part)
          run in flight + uncovered   → "generating" ("⚙ N/N" multi-part)
          zero coverage + ERROR scene → "error"      (existing taxonomy)
          anything else               → None         (NO badge — never a
                                       stale Done/Gen; a partial block
                                       with no run in flight claims
                                       nothing, the Batch window holds
                                       the per-part detail)

        The run-in-flight signal is the Scene's OWN derived status
        (SCENE_GENERATING — set at batch start / selective generation /
        regen, recomputed at batch end); the failure signal is
        SCENE_ERROR (zero coverage + last run failed) — both existing
        scene-level state, no new state machine. Blocks with no expected
        slots (never Generate-Long'ed, legacy scenes) show no badge:
        single generations carry no block-scoped provenance and the
        badge must not invent any.
        """
        editor = getattr(self, "_editor", None)
        if editor is None:
            return
        scene = getattr(self, "_active_scene", None)
        states = {}
        running = False
        error_scene = False
        if scene is not None:
            try:
                from engine.audio_provenance import (
                    block_slot_states, normalize_legacy_status,
                    SCENE_GENERATING, SCENE_ERROR,
                )
                for entry in block_slot_states(scene):
                    states[entry["block_id"]] = entry
                scene_status = normalize_legacy_status(
                    getattr(scene, "status", "") or "")
                running = (scene_status == SCENE_GENERATING)
                error_scene = (scene_status == SCENE_ERROR)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "P3.45.1: block status derivation failed: %s", exc)
                return
        try:
            for block in editor.block_manager.blocks:
                entry = states.get(block.id)
                status = None
                parts_done = 0
                parts_total = 0
                if entry is not None and entry.get("total", 0) > 0:
                    parts_total = int(entry["total"])
                    parts_done = int(entry["covered"])
                    if entry["covered"] == entry["total"]:
                        status = "done"
                    elif running:
                        status = "generating"
                    elif entry["covered"] == 0 and error_scene:
                        status = "error"
                editor.update_block_status(
                    block.id, status, 0.0, 0.0, parts_done, parts_total)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("P3.45.1: block status push failed: %s", exc)

    def _engine_outputs_dir(self) -> str:
        """The engine's outputs directory (where generated WAVs are saved).

        P3.27B: used by the versioned part-filename allocator so the
        disk-guard can see which version files already exist.
        """
        if self._engine is not None:
            audio = getattr(self._engine, "_audio", None)
            if audio is not None and hasattr(audio, "outputs_dir"):
                return audio.outputs_dir
        # Defensive fallback: <app_root>/outputs
        return os.path.join(APP_ROOT, "outputs")

    def _engine_app_root(self) -> str:
        """P3.28: the ENGINE's app root — the root all relative asset
        paths in GenerationResult/AudioAsset are relative TO (save_wav
        returns relpath from the audio manager's app root). Identical to
        APP_ROOT in production; kept separate so the provenance handlers
        resolve paths against the same root the engine wrote them from.
        """
        return os.path.dirname(self._engine_outputs_dir())

    def _scene_owner(self, scene_id, fallback_project=None, fallback_scene=None):
        """P3.44.1 §6/§7: the (project, scene) that OWNS ``scene_id``.

        A batch can still be running for a Scene of project A while the
        user has already switched to project B. Every Scene-scoped
        operation triggered by the BATCH (asset registration, failure
        recomputes, batch-completion coverage recompute, regen version
        allocation) must route to the OWNING project's live Scene —
        never to whichever project/scene happens to be ACTIVE (the
        runtime-verified defect: A's audio asset was registered on B's
        active scene, corrupting B and losing A's provenance).

        Resolution order:
          1. the active project (fast path, no scan);
          2. the ProjectManager's session cache (find_scene_owner —
             returns the SAME live instance switching back would);
          3. the caller's fallbacks (legacy scene-less results only).
        """
        if scene_id:
            if self._active_project is not None:
                scene = self._active_project.get_scene(scene_id)
                if scene is not None:
                    return self._active_project, scene
            pm = getattr(self, "_project_manager", None)
            finder = getattr(pm, "find_scene_owner", None)
            if callable(finder):
                try:
                    owner = finder(scene_id)
                    if owner is not None:
                        scene = owner.get_scene(scene_id)
                        if scene is not None:
                            return owner, scene
                except Exception as exc:
                    logger.warning("P3.44.1: scene-owner resolution failed "
                                   "for %s: %s", scene_id, exc)
        return fallback_project, fallback_scene

    def _on_batch_regen(self, idx: int) -> None:
        """Regenerate a single Generate Long part as a NEW VERSION.

        P3.27B (task §18): a regenerated part must never overwrite the
        previous good version. For jobs that carry part provenance
        (part_index set — i.e. Generate Long parts), a fresh versioned
        output filename is allocated with the same disk-guarded rule used
        at batch creation (engine/output_guard.next_free_part_filename),
        so an outlier regeneration (e.g. a 163 s runaway) leaves the
        previous version's file untouched on disk. Manual batch jobs
        (no part_index) keep the legacy overwrite-in-place contract —
        their filename is user-chosen and versioning it would create a
        second, unrequested convention.

        Queue position is preserved (concatenation order is driven by
        queue order, not filenames — see BatchGenerationDialog
        _on_concatenate), and only the reset job is processed.
        """
        bm = self._batch_manager
        jobs = bm.jobs
        if idx < 0 or idx >= len(jobs):
            return
        job = jobs[idx]
        if getattr(job, "part_index", None) is not None:
            try:
                from engine.output_guard import (
                    next_free_part_filename, normalize_name_component,
                    extract_version_hint,
                )
                from engine.audio_provenance import (
                    next_slot_version, allocate_generation_run,
                    recompute_scene_status,
                )
                # P3.28: asset-aware version allocation — the start version
                # is max(part_version among the slot's existing assets)+1
                # (a failed generation appended no asset and consumed no
                # version; deleted files never cause reuse), then the
                # disk-guard bumps upward. Each activation is a NEW
                # generation run (Rec 4).
                # P3.44.1 §6: resolve the OWNING Scene (the user may have
                # switched projects — regen versioning must not fall back
                # to the no-scene path and silently lose provenance).
                scene = None
                if job.scene_id:
                    _owner, scene = self._scene_owner(job.scene_id)
                if scene is not None and getattr(job, "slot_id", None):
                    filename, version = next_slot_version(
                        scene, job.slot_id, self._engine_outputs_dir(),
                        project=normalize_name_component(job.project or "Project"),
                        scene_name=normalize_name_component(job.scene_name or "Scene"),
                        part_index=job.part_index, speaker=job.speaker,
                        scene_id=job.scene_id)
                    job.generation_run = allocate_generation_run(scene)
                    recompute_scene_status(scene, generating=True)
                else:
                    hint = extract_version_hint(job.output_filename or "")
                    filename, version = next_free_part_filename(
                        self._engine_outputs_dir(),
                        project=normalize_name_component(job.project or "Project"),
                        scene=normalize_name_component(job.scene_name or "Scene"),
                        part_index=job.part_index,
                        speaker=job.speaker,
                        scene_id=job.scene_id,
                        start_version=(hint + 1) if hint else 1,
                    )
                job.output_filename = filename
                job.part_version = version
                logger.info(
                    "P3.28 regen: part %s allocated version v%02d -> %s "
                    "(run=%s; previous version's file is preserved)",
                    job.part_index, version, filename, job.generation_run)
            except Exception as exc:
                logger.warning(
                    "P3.28 regen: version allocation failed (%s); "
                    "falling back to the existing filename", exc)
        ok = bm.regen_job(idx)
        if not ok:
            QMessageBox.warning(
                self, APP_NAME,
                "This part cannot be regenerated (it may be pending or\n"
                "currently generating).")
            return
        # P3.44.4: the regen execution run is EXACTLY the reset job —
        # other PENDING rows in the table (e.g. never-selected Scene
        # parts) are not part of this run and must neither execute nor
        # flip to Stopped when this run is stopped.
        bm.start(reset_failed=False, only=[idx])

    # ------------------------------------------------------------------
    # P3.28: selective generation / version selection / scene combine /
    # scene output selection / batch export
    # ------------------------------------------------------------------
    def _on_generate_selected(self, checked_indices: list) -> None:
        """P3.28 §11–§12 + P3.44.4: generate ONLY the checked rows.

        Semantics per the approved design:
          - checked PENDING/SKIPPED/FAILED jobs run normally (allocating
            the next version where the slot is already covered);
          - checked COMPLETED jobs are REGENERATED as a NEW VERSION
            (v01 → v02 → …) — the previous version's file and asset are
            never touched, so a COMPLETE Scene stays COMPLETE;
          - UNCHECKED jobs are UNTOUCHED: P3.44.4 §1/§2 — table
            membership is NOT execution-run membership. A job that was
            not selected did not enter the execution run and must not
            change state (previously unchecked PENDING jobs were
            flipped to SKIPPED here, which rendered them "Stopped"
            merely because another row was generated — the runtime
            reproduction of P3.44.4 §1).

        The execution run is defined EXPLICITLY: the checked job objects
        are handed to ``BatchManager.start(only=...)``; the manager
        scopes scheduling AND Stop semantics to exactly those jobs.

        This activation is a NEW generation run (Rec 4) for the Scene.
        """
        bm = self._batch_manager
        if bm is None:
            return
        if bm.is_running:
            # P3.44.2 (pause resume): while the run is PAUSED the Start
            # button is the ONLY enabled run control (the Pause button
            # disables itself while paused). Treat the GENERATE click as
            # a RESUME request — the same semantics the manual-mode
            # Start button received in BatchManager.start(). Runtime-
            # proven defect this closes: the modal below used to block
            # the click, so a PAUSED scene batch could never be resumed
            # from the UI.
            if bm.is_paused and not bm.stop_requested:
                bm.resume()
                return
            # P3.44.1 §2: NEVER silent — the user pressed GENERATE and
            # must know why nothing started (previously this early
            # return made the click a no-op while the table sat in the
            # stuck running state).
            QMessageBox.information(
                self, APP_NAME,
                "A batch generation is already running.\n\n"
                "Wait for it to finish or press Stop first.")
            return
        jobs = bm.jobs
        if not jobs:
            return
        checked = set(int(i) for i in checked_indices
                      if 0 <= int(i) < len(jobs))
        if not checked:
            QMessageBox.information(
                self, APP_NAME,
                "No parts are selected.\n\nCheck at least one part to "
                "generate.")
            return

        # P3.45.2A — execution-run preflight. BEFORE any version
        # allocation or status reset: a declined confirmation leaves the
        # queue, slot ids, versions, parameters and edits byte-identical
        # (the "no mutation" contract). Affected jobs are REPORTED (one
        # confirmation listing them) rather than silently blocked or
        # removed — the user can cancel, uncheck the affected parts and
        # generate the rest. The check uses each job's CURRENT prompt and
        # CURRENT parameters (edits made in the batch workspace are the
        # basis), all through the single source in output_guard.
        try:
            from engine.output_guard import (
                preflight_generation_size, preflight_summary_line,
                PREFLIGHT_BLOCKED,
            )
            flagged = []
            any_blocked = False
            for idx in sorted(checked):
                job = jobs[idx]
                verdict = preflight_generation_size(
                    text=job.prompt,
                    max_new_tokens=job.parameters.max_new_tokens,
                )
                if verdict["state"] != "safe":
                    line = preflight_summary_line(verdict, job.name
                                                 or "Part {0}".format(idx + 1))
                    if line:
                        flagged.append(line)
                if verdict["state"] == PREFLIGHT_BLOCKED:
                    any_blocked = True
            if any_blocked:
                reply = QMessageBox.question(
                    self, APP_NAME,
                    "{0} of {1} selected parts exceed the single-output "
                    "generation limit:\n\n  {2}\n\n"
                    "A single generation cannot produce more audio than "
                    "the token budget allows — affected outputs would be "
                    "cut at the limit, likely mid-speech.\n\n"
                    "You can cancel, uncheck the affected parts, and "
                    "generate the rest.\n\nStart this generation run "
                    "anyway?".format(
                        len(flagged), len(checked), "\n  ".join(flagged)),
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    logger.info(
                        "PREFLIGHT P3.45.2A: selective run declined by "
                        "user (%d flagged of %d selected)",
                        len(flagged), len(checked))
                    return
                logger.info(
                    "PREFLIGHT P3.45.2A: selective run with oversized "
                    "parts acknowledged by user — proceeding unchanged "
                    "(%d flagged)", len(flagged))
        except Exception:
            logger.debug("PREFLIGHT P3.45.2A: unavailable", exc_info=True)

        scene = None
        run_id = None
        from engine.audio_provenance import (
            next_slot_version, allocate_generation_run, recompute_scene_status,
        )
        from engine.batch_manager import JobStatus
        from engine.output_guard import normalize_name_component
        regen_count = 0
        try:
            # First pass: allocate fresh versions for checked jobs whose
            # slot is already covered (regeneration), and a fresh run id.
            for idx in sorted(checked):
                job = jobs[idx]
                if job.status not in (JobStatus.PENDING, JobStatus.SKIPPED,
                                      JobStatus.FAILED, JobStatus.COMPLETED):
                    continue
                if (getattr(job, "part_index", None) is not None
                        and job.scene_id and self._active_project is not None):
                    if scene is None or scene.id != job.scene_id:
                        scene = self._active_project.get_scene(job.scene_id)
                    if scene is not None and run_id is None:
                        run_id = allocate_generation_run(scene)
                    if (scene is not None and getattr(job, "slot_id", None)
                            and job.status == JobStatus.COMPLETED):
                        # Regeneration of a covered slot → NEW VERSION.
                        filename, version = next_slot_version(
                            scene, job.slot_id, self._engine_outputs_dir(),
                            project=normalize_name_component(
                                job.project or "Project"),
                            scene_name=normalize_name_component(
                                job.scene_name or "Scene"),
                            part_index=job.part_index, speaker=job.speaker,
                            scene_id=job.scene_id)
                        job.output_filename = filename
                        job.part_version = version
                        regen_count += 1
                    if scene is not None and run_id is not None:
                        job.generation_run = run_id
            # Second pass: reset the checked jobs to PENDING (regen_job
            # handles COMPLETED/FAILED; PENDING/SKIPPED are set directly).
            # P3.44.4 §1/§2: unchecked jobs are NOT touched — they were
            # never part of this execution run and keep their state
            # (PENDING stays PENDING; previously they were flipped to
            # SKIPPED and rendered "Stopped" merely because another row
            # was generated).
            for idx, job in enumerate(jobs):
                if idx not in checked:
                    continue
                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                    bm.regen_job(idx)
                else:  # pending / skipped
                    job.status = JobStatus.PENDING
                    job.error = None
            if scene is not None:
                recompute_scene_status(scene, generating=True)
            # P3.44.1 §2: surface a failed start (defensive — the
            # hand-over path accepts queue replacements, so a False
            # here means a genuine double-start and must not be a
            # silent no-op).
            # P3.44.4: the execution run is EXPLICITLY the checked set —
            # the manager schedules and stops exactly these jobs.
            started = bm.start(reset_failed=False, only=sorted(checked))
            if not started:
                QMessageBox.information(
                    self, APP_NAME,
                    "A batch generation is already running.\n\n"
                    "Wait for it to finish or press Stop first.")
                return
            logger.info(
                "P3.28 selective generation: %d checked (%d regenerations "
                "as new versions), run=%s",
                len(checked), regen_count, run_id)
        except Exception as exc:
            logger.exception("P3.28: selective generation failed: %s", exc)
            QMessageBox.critical(
                self, APP_NAME,
                "Selective generation failed:\n{0}".format(exc))

    def _on_use_version_requested(self, slot_id: str, asset_id: str) -> None:
        """P3.28 §15/Rec 16: explicit per-slot version selection.

        Sets ``Scene.selected_block_audio[slot_id] = asset_id`` (a pure
        user action — never automatic; nothing is deleted). Feeds Scene
        Combine and the version list; does NOT change scene-level
        resolution by itself.
        """
        if (self._active_project is None or self._active_scene is None):
            return
        scene = self._active_scene
        if not slot_id:
            return
        if asset_id:
            scene.selected_block_audio[slot_id] = asset_id
        else:
            scene.selected_block_audio.pop(slot_id, None)
        logger.info("P3.28: slot %s version selection -> %s",
                    slot_id, asset_id or "(latest)")
        if self._batch_dialog is not None:
            self._batch_dialog.refresh_scene_context()

    def _on_select_output_requested(self, kind: str, entry_id: str) -> None:
        """P3.28 §15/Rec 16: explicit Scene-level output selection.

        ``kind`` is "scene_combined" or "asset"; ``entry_id`` "" clears
        the selection (back to the deterministic resolution chain).
        Selecting a STALE output is honoured only after a clear warning
        (P3.28 §15: the user must deliberately request it).
        """
        if (self._active_project is None or self._active_scene is None):
            return
        scene = self._active_scene
        from engine.audio_provenance import (
            KIND_SCENE_COMBINED, KIND_ASSET, is_scene_combined_stale,
        )
        app_root = self._engine_app_root()
        if not kind or not entry_id:
            scene.selected_output = None
            logger.info("P3.28: scene output selection cleared (default "
                        "resolution)")
        else:
            if kind == KIND_SCENE_COMBINED:
                entry = next((e for e in scene.combined_outputs
                              if isinstance(e, dict)
                              and e.get("id") == entry_id), None)
                if entry is not None:
                    stale, reasons = is_scene_combined_stale(
                        scene, entry, app_root)
                    if stale:
                        # P3.30(f): the user-approved friendly copy
                        # ("This Scene Audio is out of date. … Cancel /
                        # Use Anyway") replaces the old terse Yes/No.
                        from ui.panels.assemble_dialog import (
                            stale_audio_use_anyway_dialog,
                        )
                        if not stale_audio_use_anyway_dialog(
                                self, reasons,
                                title="Scene Audio Out of Date"):
                            return
            scene.selected_output = {"kind": kind, "id": entry_id}
            logger.info("P3.28: scene output selection -> %s %s",
                        kind, entry_id)
        if self._batch_dialog is not None:
            self._batch_dialog.refresh_scene_context()

    def _on_combine_scene_requested(self) -> None:
        """Batch window [Combine Scene] — combines the ACTIVE Scene."""
        if self._engine is None or self._active_scene is None:
            return
        self._combine_scene(self._active_scene)

    def _on_combine_scene_by_id(self, scene_id: str) -> None:
        """P3.28 follow-up (review remedy): Combine a Scene by id.

        Backs the assembly dialog's [Combine Scene Now] action so a
        REVIEW REQUIRED row can be fixed in one click — including for a
        Scene that is NOT the currently active one. After a successful
        combine the dialog's rows are rebuilt so the row immediately
        reflects the fresh Combined output.
        """
        if (self._engine is None or self._active_project is None
                or not scene_id):
            return
        scene = self._active_project.get_scene(scene_id)
        if scene is None:
            return
        # Same running-generation guard as assembly (§68): combining
        # while a batch writes parts could read partially-written files.
        batch_running = (self._batch_manager is not None
                         and self._batch_manager.is_running)
        engine_busy = (self._engine is not None
                       and getattr(self._engine, "is_generating", False))
        if batch_running or engine_busy:
            QMessageBox.warning(
                self, APP_NAME,
                "Audio generation is currently running.\n\n"
                "Please wait for it to finish (or stop it) before "
                "combining the Scene.")
            return
        if self._combine_scene(scene):
            dlg = self._find_assemble_dialog()
            if dlg is not None:
                dlg._rebuild_rows()

    def _find_assemble_dialog(self):
        """The open AssembleScenesDialog to refresh after a remedy
        combine. Deterministic rule: the NEWEST open dialog wins (the
        one the user is looking at; modal exec means there is normally
        exactly one — stale undestroyed instances from long-lived
        processes must never steal the refresh)."""
        try:
            from PySide6.QtWidgets import QApplication
            from ui.panels.assemble_dialog import AssembleScenesDialog
            candidates = [w for w in QApplication.topLevelWidgets()
                          if isinstance(w, AssembleScenesDialog)]
            if not candidates:
                return None
            return max(candidates,
                       key=lambda w: getattr(w, "_creation_seq", 0))
        except Exception:
            return None

    def _combine_scene(self, scene) -> bool:
        """P3.28 §13: combine the Scene's generated slot outputs into a
        versioned Scene Combined output with full per-slot lineage.

        Sources = the resolved audio of EVERY expected slot in slot order
        (explicit per-slot selection, else latest version). A combined
        output is structurally NEVER a source (double-inclusion
        protection, P3.28 §14). Missing slots BLOCK the combine with a
        clear message (no silent skipping). Older combined outputs remain
        on disk and in the list; nothing is rebuilt automatically.

        P3.28 follow-up R3 (accepted): a successful Combine takes over
        the resolved output — any older explicit selection is CLEARED so
        the fresh snapshot wins the default rule (it is born fresh: it
        was built from the current composition and the current slot
        set). Returns True on success.
        """
        if self._engine is None or scene is None:
            return False
        app_root = self._engine_app_root()
        try:
            from engine.audio_provenance import (
                resolved_slot_sources, next_scene_combined_version,
                build_scene_combined_entry, allocate_generation_run,
            )
            sources, blockers = resolved_slot_sources(scene, app_root)
            if blockers:
                QMessageBox.warning(
                    self, APP_NAME,
                    "The Scene cannot be combined yet:\n\n  • {0}\n\n"
                    "Generate the missing parts first — no audio was "
                    "skipped silently.".format("\n  • ".join(blockers)))
                return False
            if not sources:
                QMessageBox.information(
                    self, APP_NAME,
                    "This Scene has no generation structure yet.\n\n"
                    "Use Generate Long first — a Scene Combine works on "
                    "the generated slot outputs.")
                return False

            # Inter-part silence from the existing concatenate settings.
            concat_silence_ms = 300
            try:
                _sm = self._settings_manager
                concat_silence_ms = int(
                    _sm.get("audio", "concatenate_silence_ms", 300))
            except Exception:
                pass

            output_dir = self._engine_outputs_dir()
            os.makedirs(output_dir, exist_ok=True)
            filename, version = next_scene_combined_version(
                scene, output_dir, project=self._current_project or "Project",
                scene_name=scene.name, scene_id=scene.id)
            combined_path = os.path.join(output_dir, filename)

            abs_paths = [os.path.join(app_root, s["output_path"])
                         if not os.path.isabs(s["output_path"])
                         else s["output_path"] for s in sources]
            if concat_silence_ms > 0:
                self._engine._audio.concatenate_with_silence(
                    abs_paths, silence_ms=concat_silence_ms,
                    output_path=combined_path, randomize_silence=False)
            else:
                self._engine._audio.concatenate(
                    abs_paths, combined_path, crossfade_ms=100)

            combined_rel = os.path.relpath(combined_path, app_root)
            combined_duration = 0.0
            try:
                samples, sr = self._engine._audio.load_wav(combined_path)
                combined_duration = (len(samples) / float(sr)
                                     if sr > 0 else 0.0)
            except Exception as exc:
                logger.warning("Could not read combined duration: %s", exc)

            run_id = allocate_generation_run(scene)
            entry = build_scene_combined_entry(
                scene, version=version, output_path=combined_rel,
                duration=combined_duration, sources=sources,
                silence_ms=concat_silence_ms, generation_run=run_id,
                project_name=self._current_project or "")
            scene.combined_outputs.append(entry)

            # P3.28 follow-up R3 (accepted): Combine takes over the
            # resolved output. Clear any older explicit selection — the
            # fresh snapshot wins the default rule and is born fresh
            # (built from the current composition and slot set). Keeping
            # an older selection would make Combine appear to do nothing
            # to what Project Assembly consumes.
            if scene.selected_output is not None:
                scene.selected_output = None
                logger.info(
                    "P3.31 R3: Combine cleared the explicit scene output "
                    "selection for '%s' — fresh Combined v%02d is now the "
                    "resolved output", scene.name, version)

            # History entry with scene_combined lineage (P3.28 §20).
            try:
                from engine.models import GenerationResult
                from engine.events import Event, EventType
                from datetime import datetime as _dt
                prompt_summary = (
                    "[Scene Combined - {0} parts]\n{1}".format(
                        len(sources),
                        "\n".join("  {0}: v{1:02d} ({2:.1f}s)".format(
                            (s.get("block_label")
                             or s.get("speaker") or s.get("slot_id", "?")),
                            int(s.get("part_version") or 0),
                            float(s.get("duration") or 0.0))
                            for s in sources)))
                result = GenerationResult(
                    success=True,
                    output_path=combined_rel,
                    output_duration=combined_duration,
                    output_sample_rate=24000,
                    generation_time=0.0,
                    realtime_factor=0.0,
                    parameters=None,
                    voice_profile=None,
                    prompt=prompt_summary,
                    timestamp=_dt.now().strftime("%Y%m%d_%H%M%S"),
                    warnings=[],
                    errors=[],
                    project=self._current_project or "Default",
                    scene_id=scene.id,
                    scene_name=scene.name,
                )
                result.generation_run = run_id
                self._engine._history.add(
                    result, entry_type="scene_combined",
                    extra={
                        "scene_ids": [scene.id],
                        "source_ids": [s.get("asset_id", "")
                                       for s in sources],
                        "format": "wav",
                        "combined_version": version,
                    })
                self._engine._event_bus.emit(
                    Event(EventType.HISTORY_UPDATED))
            except Exception as exc:
                logger.warning(
                    "Could not add history entry for scene combine: %s", exc)

            self._waveform.set_audio_info(
                combined_duration, 24000, combined_rel)
            self._status_bar.showMessage(
                "Scene combined: {0} parts → {1} ({2:.1f}s) — Scene output "
                "→ Combined v{3:02d}".format(
                    len(sources), filename, combined_duration, version), 5000)
            logger.info(
                "P3.28: Scene Combined v%02d for '%s': %d slots, %.1fs, "
                "run=%s", version, scene.name, len(sources),
                combined_duration, run_id)
            if self._batch_dialog is not None:
                self._batch_dialog.refresh_scene_context()
            if (self._project_manager is not None
                    and self._active_project is not None):
                self._project_manager.save_project(self._active_project)
            return True
        except Exception as exc:
            logger.exception("P3.28: Scene Combine failed: %s", exc)
            QMessageBox.critical(
                self, APP_NAME, "Scene Combine failed:\n{0}".format(exc))
            return False

    def _on_batch_export_audio(self, scope: str, checked_indices: list) -> None:
        """P3.28 §17: Batch Audio Export — file copy ONLY.

        Scopes: "selected" (checked rows), "parts" (all generated parts),
        "combined" (Scene combined outputs), "everything". Files keep
        their provenance filenames (no second naming system); collisions
        at the destination are auto-suffixed — never a silent overwrite.
        """
        if self._active_scene is None:
            return
        scene = self._active_scene
        try:
            from PySide6.QtWidgets import QFileDialog
            dest = QFileDialog.getExistingDirectory(
                self, "Export Audio — Choose Destination Folder", "")
            if not dest:
                return
            # Collect source files per scope (relative paths).
            rel_files: list = []
            if scope in ("selected", "parts", "everything"):
                jobs = (self._batch_manager.jobs
                        if self._batch_manager else [])
                checked = set(int(i) for i in (checked_indices or []))
                for idx, job in enumerate(jobs):
                    if not job.output_path:
                        continue
                    if scope == "selected" and idx not in checked:
                        continue
                    if scope == "parts" and getattr(
                            job, "part_index", None) is None:
                        continue
                    rel_files.append(job.output_path)
            if scope in ("combined", "everything"):
                for entry in scene.combined_outputs:
                    if isinstance(entry, dict) and entry.get("output_path"):
                        rel_files.append(entry["output_path"])
            # De-duplicate, preserve order.
            seen = set()
            unique_files = []
            for rel in rel_files:
                if rel not in seen:
                    seen.add(rel)
                    unique_files.append(rel)
            if not unique_files:
                QMessageBox.information(
                    self, APP_NAME,
                    "Nothing to export for this scope.")
                return
            import shutil
            copied = []
            skipped = []
            for rel in unique_files:
                src = rel if os.path.isabs(rel) else os.path.join(
                    self._engine_app_root(), rel)
                if not os.path.isfile(src):
                    skipped.append(os.path.basename(rel))
                    continue
                # Containment: the destination file is the BASENAME of the
                # provenance filename inside the chosen folder (Rec 21 —
                # a crafted name cannot escape the destination).
                base = os.path.basename(rel.replace("\\", "/"))
                dst = os.path.join(dest, base)
                if os.path.exists(dst):
                    root, ext = os.path.splitext(base)
                    counter = 2
                    while os.path.exists(dst):
                        dst = os.path.join(
                            dest, "{0} ({1}){2}".format(root, counter, ext))
                        counter += 1
                try:
                    shutil.copy2(src, dst)
                    copied.append(os.path.basename(dst))
                except OSError as exc:
                    skipped.append("{0} ({1})".format(base, exc))
            summary_lines = ["Exported {0} file(s) to:".format(len(copied)),
                             "  {0}".format(dest), ""]
            for name in copied:
                summary_lines.append("  ✓ {0}".format(name))
            if skipped:
                summary_lines.append("")
                summary_lines.append(
                    "Skipped {0} (missing/failed):".format(len(skipped)))
                for name in skipped:
                    summary_lines.append("  ✗ {0}".format(name))
            QMessageBox.information(
                self, APP_NAME, "\n".join(summary_lines))
            logger.info("P3.28: batch audio export (scope=%s): %d copied, "
                        "%d skipped", scope, len(copied), len(skipped))
        except Exception as exc:
            logger.exception("P3.28: batch audio export failed: %s", exc)
            QMessageBox.critical(
                self, APP_NAME, "Audio export failed:\n{0}".format(exc))

    def _on_batch_play(self, output_path: str) -> None:
        """Play a single batch job's audio (from the per-row Play button).

        P3.44: the path may be ABSOLUTE (Scene-mode Play resolves the
        authoritative AudioAsset against the dialog's engine app root)
        or engine-app-root-relative (manual-mode job outputs). It is
        always resolved to an ABSOLUTE path before touching the
        WaveformPlayer — the player's peak loader resolves plain paths
        against the process CWD (the P3.25 class of bugs), which
        rendered a trackless transport for relative paths.
        """
        if self._engine is None or not output_path:
            return
        if os.path.isabs(output_path):
            abs_path = output_path
        else:
            abs_path = os.path.join(self._engine_app_root(), output_path)
        # Stop any currently playing audio first.
        self._engine.stop_playback()
        # Load the audio into the waveform player for visual feedback.
        try:
            samples, sr = self._engine._audio.load_wav(abs_path)
            duration = len(samples) / float(sr) if sr > 0 else 0.0
            self._waveform.set_audio_info(duration, sr, abs_path)
        except Exception:
            # If we can't load for waveform display, still try to play.
            pass
        # Play the audio.
        self._engine.play_audio(abs_path)
        self._waveform.set_playback_state("playing")

    def _on_batch_stop_playback(self) -> None:
        """Stop audio playback (from the per-row Stop button)."""
        if self._engine is not None:
            self._engine.stop_playback()
        self._waveform.set_playback_state("stopped")

    def _on_concatenate_requested(self, paths: list) -> None:
        """Concatenate completed parts into a single WAV.

        Called when the user clicks the "Concatenate" button in the
        Batch Generation dialog. The ``paths`` argument is a list of
        output paths (relative to APP_ROOT) in queue order — this IS
        the final audio order.

        If any of the batch jobs has a speaker label (multi-speaker
        dialogue), silence-based concatenation is used (500ms gap
        between parts) instead of crossfade — this sounds more natural
        for speaker switches and avoids blending two voices.

        After concatenation, a History entry is created for the combined
        audio so it appears in the History tab.
        """
        if self._engine is None or not paths:
            return
        try:
            import os
            from datetime import datetime
            output_dir = os.path.join(APP_ROOT, "outputs")
            os.makedirs(output_dir, exist_ok=True)
            # Resolve relative paths to absolute for AudioManager.
            abs_paths = []
            for p in paths:
                if os.path.isabs(p):
                    abs_paths.append(p)
                else:
                    abs_paths.append(os.path.join(APP_ROOT, p))
            # Verify all files exist.
            missing = [p for p in abs_paths if not os.path.isfile(p)]
            if missing:
                QMessageBox.warning(
                    self, APP_NAME,
                    "Some output files are missing:\n{0}\n\n"
                    "Regenerate the missing parts first.".format(
                        "\n".join(os.path.basename(p) for p in missing)))
                return

            # Detect multi-speaker dialogue using the DEDICATED speaker field
            # on BatchJob (not a string heuristic on the job name).
            is_dialogue = False
            completed_jobs = []
            if self._batch_dialog is not None:
                from engine.batch_manager import JobStatus
                for job in self._batch_dialog._manager.jobs:
                    if job.status == JobStatus.COMPLETED and job.output_path:
                        completed_jobs.append(job)
                        if job.speaker:
                            is_dialogue = True

            # Load concatenate settings from settings.json BEFORE the
            # if/else branch so both dialogue and single-speaker paths
            # can use them.
            concat_silence_ms = 300
            concat_randomize = True
            try:
                settings_dir = os.path.join(APP_ROOT, "settings")
                from engine.settings_manager import SettingsManager
                _sm = self._settings_manager
                concat_silence_ms = int(_sm.get("audio", "concatenate_silence_ms", 300))
                concat_randomize = bool(_sm.get("audio", "concatenate_randomize", True))
            except Exception:
                pass

            if is_dialogue:
                # Multi-speaker: use silence-based concatenation (no crossfade).
                # Also generate per-speaker stems for Unreal Engine integration.
                scene_name = self._current_project or "dialogue"
                scene_slug = scene_name.lower().replace(" ", "_").replace("/", "_")
                scene_dir = os.path.join(output_dir, scene_slug)
                os.makedirs(scene_dir, exist_ok=True)
                lines_dir = os.path.join(scene_dir, "lines")
                stems_dir = os.path.join(scene_dir, "stems")
                os.makedirs(lines_dir, exist_ok=True)
                os.makedirs(stems_dir, exist_ok=True)

                # Copy per-line WAVs into lines/ (organized structure).
                import shutil
                for job in completed_jobs:
                    src = os.path.join(APP_ROOT, job.output_path)
                    if os.path.isfile(src):
                        dst = os.path.join(lines_dir, os.path.basename(job.output_path))
                        try:
                            shutil.copy2(src, dst)
                        except Exception:
                            pass

                # Full dialogue WAV with silence between speakers.
                # (concat_silence_ms and concat_randomize were loaded above.)
                combined_path = os.path.join(scene_dir, "{0}_full.wav".format(scene_slug))
                self._engine._audio.concatenate_with_silence(
                    abs_paths, silence_ms=concat_silence_ms,
                    output_path=combined_path,
                    randomize_silence=concat_randomize)

                # Per-speaker stems: concatenate each speaker's lines.
                stems_created = []
                speakers_found = sorted({j.speaker for j in completed_jobs if j.speaker})
                for spk in speakers_found:
                    spk_paths = [os.path.join(APP_ROOT, j.output_path)
                                 for j in completed_jobs
                                 if j.speaker == spk and j.output_path]
                    spk_paths = [p for p in spk_paths if os.path.isfile(p)]
                    if spk_paths:
                        spk_slug = spk.lower().replace(" ", "_")
                        stem_path = os.path.join(stems_dir, "{0}.wav".format(spk_slug))
                        try:
                            self._engine._audio.concatenate(
                                spk_paths, stem_path, crossfade_ms=50)
                            stems_created.append(spk)
                        except Exception as exc:
                            logger.warning("Stem export failed for %s: %s", spk, exc)
            else:
                # Single-speaker narration: use the concatenate settings.
                # If silence_ms > 0, use silence-based concatenation (the
                # user can control the gap between parts). If silence_ms == 0,
                # fall back to crossfade (original behavior).
                if concat_silence_ms > 0:
                    combined_path = os.path.join(output_dir, "long_narration_full.wav")
                    self._engine._audio.concatenate_with_silence(
                        abs_paths, silence_ms=concat_silence_ms,
                        output_path=combined_path,
                        randomize_silence=concat_randomize)
                else:
                    combined_path = os.path.join(output_dir, "long_narration_full.wav")
                    self._engine._audio.concatenate(
                        abs_paths, combined_path, crossfade_ms=100)
            combined_rel = os.path.relpath(combined_path, APP_ROOT)

            # Compute the combined duration by loading the result.
            combined_duration = 0.0
            try:
                samples, sr = self._engine._audio.load_wav(combined_path)
                combined_duration = len(samples) / float(sr) if sr > 0 else 0.0
            except Exception as exc:
                logger.warning("Could not read combined duration: %s", exc)

            # Load the combined audio into the waveform player.
            self._waveform.set_audio_info(
                combined_duration, 24000, combined_rel)
            if is_dialogue:
                self._status_bar.showMessage(
                    "Dialogue: {0} parts concatenated ({1:.1f}s) + {2} stems".format(
                        len(abs_paths), combined_duration, len(stems_created)),
                    5000)
            else:
                self._status_bar.showMessage(
                    "Concatenated {0} parts ({1:.1f}s)".format(
                        len(abs_paths), combined_duration),
                    5000)
            logger.info("Manual concatenation: %d parts -> %s (%.1fs) dialogue=%s",
                        len(abs_paths), combined_rel, combined_duration, is_dialogue)

            # --- Add a History entry for the combined audio ---
            # The individual parts already have their own history entries
            # from their generations; this creates a distinct entry for the
            # merged result so the user can find/replay/export it from the
            # History tab.
            try:
                from engine.models import GenerationResult
                from engine.batch_manager import JobStatus
                # Gather metadata from the batch jobs (voice, parameters,
                # project) so the entry is reproducible-context-rich.
                voice_profile = None
                parameters = None
                project = self._current_project
                prompt_summary = ""
                if self._batch_dialog is not None:
                    bm = self._batch_dialog._manager
                    completed_jobs = [
                        j for j in bm.jobs
                        if j.status == JobStatus.COMPLETED]
                    if completed_jobs:
                        first = completed_jobs[0]
                        # Voice profile (from the first completed job).
                        if first.voice_id and self._engine is not None:
                            try:
                                voice_profile = self._engine.get_voice(
                                    first.voice_id)
                            except Exception:
                                voice_profile = None
                        # Parameters from the first completed job.
                        parameters = first.parameters
                        project = first.project or self._current_project
                        # Build a short prompt summary listing the parts.
                        prompt_summary = "[Concatenated Long Narration - {0} parts]\n{1}".format(
                            len(completed_jobs),
                            "\n".join(
                                "  {0}: {1}".format(j.name, j.prompt[:80])
                                for j in completed_jobs))

                result = GenerationResult(
                    success=True,
                    output_path=combined_rel,
                    output_duration=combined_duration,
                    output_sample_rate=24000,
                    generation_time=0.0,  # N/A for concatenation
                    realtime_factor=0.0,
                    parameters=parameters,
                    voice_profile=voice_profile,
                    prompt=prompt_summary,
                    timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
                    warnings=[],
                    errors=[],
                    gpu_name="",
                    vram_usage_mb=0.0,
                    project=project,
                )
                self._engine._history.add(result)
                # Notify the UI to refresh the History tab.
                from engine.events import Event, EventType
                self._engine._event_bus.emit(
                    Event(EventType.HISTORY_UPDATED))
                logger.info("History entry added for concatenated audio: %s",
                            combined_rel)
            except Exception as exc:
                logger.warning(
                    "Could not add history entry for concatenated audio: %s",
                    exc)
        except Exception as exc:
            logger.exception("Concatenation failed: %s", exc)
            QMessageBox.critical(
                self, APP_NAME,
                "Concatenation failed:\n{0}".format(exc))

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------
    def _on_import_voice(self) -> None:
        """Import a voice profile from a reference WAV (P3.43).

        Opens the SINGLE shared Voice Profile editor in IMPORT mode. The
        editor applies the change through the transactional engine import
        (engine.import_voice_profile) and VOICE_CHANGED refreshes every
        dependent selector — this handler performs no voice operations
        itself (P3.43 §25: one authoritative operation, one place).
        """
        if self._engine is None:
            return
        from ui.panels.voice_import_dialog import VoiceImportDialog

        dialog = VoiceImportDialog(
            mode=VoiceImportDialog.MODE_IMPORT, engine=self._engine,
            parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        new_id = dialog.applied_profile_id()
        if new_id:
            QMessageBox.information(
                self, APP_NAME,
                "Voice profile '{0}' imported successfully.".format(
                    dialog.values()["name"]))

    def _on_create_voice(self) -> None:
        """Create a new voice profile (P3.43).

        Opens the SINGLE shared editor in CREATE mode — replacing the old
        name-only QInputDialog flow (P3.43 §12: legacy creation flows
        redirect into the unified editor). VOICE_CHANGED refreshes the
        selectors; no manual refresh choreography here.
        """
        if self._engine is None:
            return
        from ui.panels.voice_import_dialog import VoiceImportDialog
        dialog = VoiceImportDialog(
            mode=VoiceImportDialog.MODE_CREATE, engine=self._engine,
            parent=self)
        dialog.exec()

    def _on_new_scene(self) -> None:
        """Create a new Scene within the current Project.

        P3.24 UX Correction: QUICK CREATE — no modal name dialog. The new
        Scene is created immediately with the next default name, activated,
        loaded into the editor, and PERSISTED, so the user sees it at once:

          - unique Scene ID (model default)
          - next appropriate sort_order (appended after the final Scene)
          - added to the active Project
          - becomes the ACTIVE Scene (editor switches to it)
          - clean Scene state loaded (previous Scene's state is saved
            first; the new Scene's editor text and blocks are empty)
          - ProjectSceneBar, sidebar Scene list, Context List and Recents
            all refresh; the Project is saved through the existing
            ProjectManager persistence.

        The existing rename workflow (Scene dropdown → Rename Scene, or
        the edit icon) is available immediately afterwards — no second
        creation path is introduced.
        """
        if self._active_project is None:
            QMessageBox.information(self, APP_NAME,
                "No active project.\n\nCreate or open a project first.")
            return
        # Default name — unique within the Project (never merge duplicates).
        base_name = "Untitled Scene"
        existing = {s.name for s in self._active_project.scenes}
        name = base_name
        n = 2
        while name in existing:
            name = "{0} {1}".format(base_name, n)
            n += 1
        # Save the CURRENT scene's state before switching away.
        self._save_current_scene_state()
        # Create a new Scene entity.
        # P3.21: sort_order = max(existing) + 1 (append after the final
        # Scene — never derived from filenames).
        next_order = max((s.sort_order for s in self._active_project.scenes),
                         default=-1) + 1
        new_scene = Scene(name=name, status="draft", sort_order=next_order)
        # P3.26 (Scene state contract): a FRESH Scene inherits the CURRENT
        # editor mode (workflow continuity — a user working in Narration
        # Blocks keeps that mode in the new Scene; this matches the
        # pre-P3.26 behaviour where the mode was never touched on scene
        # creation). Switching between EXISTING Scenes always restores
        # each Scene's own stored mode.
        try:
            new_scene.editor_mode = self._editor.editor_mode_name
        except Exception:
            pass
        self._active_project.add_scene(new_scene)
        # Switch to the new scene.
        self._active_scene = new_scene
        self._current_scene = name
        self._scene_unsaved = True
        # Load the new Scene's state — a FRESH Scene means empty text AND
        # empty Narration Blocks. _load_scene_state also clears any
        # character-driven voice display override from the previous Scene.
        # (Previously only clear_text() was called, which left the
        # previous Scene's blocks visible in the editor.)
        self._load_scene_state(new_scene)
        # Update context bar
        if hasattr(self, "_project_scene_bar"):
            self._project_scene_bar.set_scene(name)
            self._project_scene_bar.set_unsaved()
        # Update sidebar
        if hasattr(self, "_sidebar"):
            # P3.10 FIX: Use the authoritative self._active_project.scenes
            # as the source of truth, NOT the sidebar's internal _scenes
            # list (which may be stale or missing scenes). Previously this
            # read getattr(self._sidebar, "_scenes", []) which could lose
            # scenes that weren't displayed yet.
            sidebar_scenes = [{"id": s.id, "name": s.name, "status": s.status}
                              for s in self._get_sorted_scenes()]
            self._sidebar.set_scenes(sidebar_scenes)
            self._sidebar.set_active_scene(new_scene.id)
        # Persist the new Scene through the existing Project architecture
        # (silent save — no modal "Project saved" box for this action).
        if self._project_manager is not None:
            try:
                self._project_manager.save_project(self._active_project)
            except Exception as exc:
                logger.warning("P3.24: could not persist new scene: %s", exc)
        # Persist current_scene in settings
        try:
            sm = self._settings_manager
            sm.set("application", "current_scene", name)
        except Exception:
            pass
        logger.info("P3.24: New scene created: '%s' (id=%s, sort_order=%d, project=%s)",
                     name, new_scene.id, next_order, self._current_project)
        self._status_bar.showMessage(
            "Scene '{0}' created".format(name), 3000)
        # P3.5 Final Correction: refresh the context list so the new scene
        # appears and is highlighted as active in the Scenes context.
        if self._recents_manager is not None:
            self._recents_manager.add_recent_scene(
                self._active_project.id, new_scene.id, new_scene.name)
            self._update_recents()
        else:
            self._update_project_scene_bar_menus()

    # ------------------------------------------------------------------
    # P3.24 UX Correction: CHARACTERS panel — quick inline management
    # ------------------------------------------------------------------
    def _next_default_character_name(self) -> str:
        """P3.24: next unique default Character name in the active Project."""
        existing = {c.name for c in self._active_project.characters}
        base = "New Character"
        if base not in existing:
            return base
        n = 2
        while "{0} {1}".format(base, n) in existing:
            n += 1
        return "{0} {1}".format(base, n)

    def _on_character_create_quick(self) -> None:
        """P3.24 UX Correction: QUICK Character creation — no modal dialog.

        The "+ Add Character" flow creates the Character immediately with
        the next default name. The user then renames inline (double-click)
        and assigns the Voice Profile via the row dropdown. Description /
        role / tags remain optional details in the secondary dialog.

        The Character is created on the ACTIVE PROJECT only (no global
        Characters) and persisted through the existing ProjectManager.
        """
        if self._active_project is None:
            QMessageBox.information(self, APP_NAME,
                "No active project.\n\nCharacters belong to a Project — "
                "create or open a project first.")
            return
        from engine.models import Character
        name = self._next_default_character_name()
        char = Character(name=name)
        self._active_project.add_character(char)
        # Persist through the existing Project architecture (silent).
        if self._project_manager is not None:
            try:
                self._project_manager.save_project(self._active_project)
            except Exception as exc:
                logger.warning("P3.24: could not persist new character: %s", exc)
        # Select the new Character and refresh every dependent surface.
        self._selected_character_id = char.id
        self._refresh_character_surfaces()
        if self._recents_manager is not None:
            self._recents_manager.add_recent_character(
                self._active_project.id, char.id, char.name)
            self._update_recents()
        self._status_bar.showMessage(
            "Character '{0}' created — double-click its name to rename, "
            "use the dropdown to assign a Voice Profile".format(name), 5000)
        logger.info("P3.24: Character created (quick): '%s' (id=%s, project=%s)",
                     name, char.id, self._active_project.name)

    def _refresh_character_surfaces(self) -> None:
        """P3.24: refresh every surface that shows Characters after a
        character mutation (create / rename / voice change / delete):

          - NarrationEditor block Character selector (+ stale-Character
            cleanup for deleted Characters)
          - Context List (management rows) + Recents
        """
        self._update_editor_characters()
        self._update_recents()

    def _on_character_rename_requested(self, character_id: str,
                                       new_name: str) -> None:
        """P3.24: inline rename from the CHARACTERS panel row.

        Validation (§14): empty names are rejected, duplicate names are
        rejected (no silent merging — IDs remain authoritative), very
        long names are rejected, and surrounding whitespace is stripped.
        The row reverts on rejection (the list is rebuilt from the
        authoritative Project).
        """
        if self._active_project is None:
            return
        char = self._active_project.get_character(character_id)
        if char is None:
            return
        name = (new_name or "").strip()
        if not name:
            self._status_bar.showMessage(
                "Character name cannot be empty — rename cancelled", 4000)
            self._update_recents()
            return
        if len(name) > 64:
            self._status_bar.showMessage(
                "Character name is too long (max 64 characters) — "
                "rename cancelled", 4000)
            self._update_recents()
            return
        if any(c.id != character_id and c.name == name
               for c in self._active_project.characters):
            QMessageBox.warning(
                self, "Duplicate Name",
                "Another Character named '{0}' already exists in this "
                "Project.\n\nCharacter names must be unique.".format(name))
            self._update_recents()
            return
        old_name = char.name
        char.name = name
        if self._project_manager is not None:
            try:
                self._project_manager.save_project(self._active_project)
            except Exception as exc:
                logger.warning("P3.24: could not persist rename: %s", exc)
        self._refresh_character_surfaces()
        logger.info("P3.24: Character renamed: '%s' → '%s' (id=%s)",
                     old_name, name, character_id)

    def _on_character_voice_requested(self, character_id: str,
                                      voice_profile_id: str) -> None:
        """P3.24: Voice Profile assignment from the CHARACTERS panel row.

        Assigns (or clears, when voice_profile_id is empty) the
        Character's Voice Profile. This is the mapping the approved voice
        precedence resolves during generation:
            Block Character > Scene Voice > UI Voice.
        """
        if self._active_project is None:
            return
        char = self._active_project.get_character(character_id)
        if char is None:
            return
        char.voice_profile_id = voice_profile_id or None
        if self._project_manager is not None:
            try:
                self._project_manager.save_project(self._active_project)
            except Exception as exc:
                logger.warning("P3.24: could not persist voice change: %s", exc)
        self._refresh_character_surfaces()
        voice_name = "no voice"
        if char.voice_profile_id and self._engine is not None:
            v = self._engine.get_voice(char.voice_profile_id)
            if v:
                voice_name = v.name
        self._status_bar.showMessage(
            "Character '{0}' → {1}".format(char.name, voice_name), 4000)
        logger.info("P3.24: Character voice assigned: '%s' → '%s' (id=%s)",
                     char.name, voice_name, character_id)

    def _on_character_delete_requested(self, character_id: str) -> None:
        """P3.24: safe Character deletion from the CHARACTERS panel row.

        §15: if the Character is referenced by any Scene (character_ids)
        or Narration Block (block.character_id), a confirmation lists the
        references — never a silent delete. After deletion:

          - Scene character_ids references are removed
          - Block character_id references are demoted to
            lost_character_id (existing P3.23 cleanup — no orphan IDs,
            no real Character colour painted for a deleted Character)
          - Recents entries are removed (no stale entries)
          - the active Character selection is cleared if it pointed at
            the deleted Character
          - the block Character selector is refreshed (the Character
            disappears from it)
          - the Project is saved
        """
        if self._active_project is None:
            return
        char = self._active_project.get_character(character_id)
        if char is None:
            return
        # Gather references for the confirmation (§15).
        referencing_scenes = [s.name for s in self._active_project.scenes
                              if character_id in s.character_ids]
        block_refs = []
        try:
            for block in self._editor.block_manager.blocks:
                if getattr(block, "character_id", None) == character_id:
                    block_refs.append(block)
        except Exception:
            pass
        msg = ("Delete Character '{0}'?".format(char.name))
        if referencing_scenes or block_refs:
            msg += "\n\nThis Character is referenced by:"
            if referencing_scenes:
                msg += "\n  • {0} Scene(s): {1}".format(
                    len(referencing_scenes), ", ".join(referencing_scenes))
            if block_refs:
                msg += "\n  • {0} Narration Block(s)".format(len(block_refs))
            msg += ("\n\nThe references will be removed. Blocks that used "
                    "this Character will show a re-assign warning and fall "
                    "back to the Scene / UI voice.")
        else:
            msg += "\n\nThis Character is not referenced by any Scene or Block."
        reply = QMessageBox.question(
            self, "Delete Character", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Remove Scene references.
        for scene in self._active_project.scenes:
            if character_id in scene.character_ids:
                scene.character_ids.remove(character_id)
        # Remove the Character from the authoritative Project list.
        self._active_project.characters = [
            c for c in self._active_project.characters if c.id != character_id]
        # Block references: demote to lost_character_id via the existing
        # P3.23 cleanup (runs inside _update_editor_characters).
        if self._selected_character_id == character_id:
            self._selected_character_id = None
        # Recents: remove stale entries.
        if self._recents_manager is not None:
            self._recents_manager.remove_stale_character(
                self._active_project.id, character_id)
        if self._project_manager is not None:
            try:
                self._project_manager.save_project(self._active_project)
            except Exception as exc:
                logger.warning("P3.24: could not persist delete: %s", exc)
        self._refresh_character_surfaces()
        self._status_bar.showMessage(
            "Character '{0}' deleted".format(char.name), 4000)
        logger.info("P3.24: Character deleted: '%s' (id=%s) — %d scene refs, "
                    "%d block refs cleaned",
                    char.name, character_id, len(referencing_scenes),
                    len(block_refs))

    def _on_character_details_requested(self, character_id: str) -> None:
        """P3.24: open the existing CharacterManagementDialog as the
        SECONDARY details editor (description / role / tags) for the
        given Character. The basic name + Voice Profile workflow lives
        inline in the CHARACTERS panel."""
        if self._active_project is None:
            return
        # Ensure the Character is the highlighted one when the dialog
        # closes (the dialog reports its own selection).
        self._selected_character_id = character_id
        self._on_open_character_management()

    # ------------------------------------------------------------------
    # P3.15: Project / Scene switcher + rename + delete handlers
    # ------------------------------------------------------------------
    def _on_project_switch(self, project_id: str) -> None:
        """P3.15: Switch to a different Project from the top bar dropdown."""
        if self._project_manager is None or not project_id:
            return
        project = self._project_manager.get_project(project_id)
        if project is not None:
            self._load_native_project(project)

    def _on_scene_switch(self, scene_id: str) -> None:
        """P3.15: Switch to a different Scene from the top bar dropdown."""
        if not scene_id:
            return
        self._switch_to_scene(scene_id)

    def _on_rename_project(self) -> None:
        """P3.15: Rename the active Project."""
        from PySide6.QtWidgets import QInputDialog
        if self._active_project is None:
            return
        old_name = self._active_project.name
        name, ok = QInputDialog.getText(
            self, "Rename Project", "New project name:", text=old_name)
        if not ok or not name.strip() or name.strip() == old_name:
            return
        name = name.strip()
        self._active_project.name = name
        self._current_project = name
        if hasattr(self, "_project_scene_bar"):
            self._project_scene_bar.set_project(name)
        if self._project_manager is not None:
            self._project_manager.save_project(self._active_project)
        self._update_project_scene_bar_menus()
        self._update_recents()
        logger.info("P3.15: Project renamed: %s → %s", old_name, name)

    def _on_delete_project(self) -> None:
        """P3.15: Delete the active Project with confirmation."""
        from PySide6.QtWidgets import QMessageBox
        if self._active_project is None or self._project_manager is None:
            return
        project = self._active_project
        reply = QMessageBox.question(
            self, "Delete Project",
            "Delete Project '{0}'?\n\n"
            "Contains:\n"
            "  {1} Scenes\n"
            "  {2} Characters\n\n"
            "This action removes the Project and its project-owned data.\n"
            "Generated audio files will NOT be deleted.".format(
                project.name, len(project.scenes), len(project.characters)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Remove from Recents
        if self._recents_manager:
            self._recents_manager.remove_stale_project(project.id)
        # Delete from disk
        self._project_manager.delete_project(project.id)
        # Clear active project
        self._active_project = None
        self._active_scene = None
        # Load another project if available
        projects = self._project_manager.list_projects()
        if projects:
            self._load_native_project(projects[0])
        else:
            self._on_new_project()
        self._update_recents()
        logger.info("P3.15: Project deleted: %s", project.name)

    def _on_delete_scene(self) -> None:
        """P3.15: Delete the active Scene with confirmation."""
        from PySide6.QtWidgets import QMessageBox
        if self._active_project is None or self._active_scene is None:
            return
        scene = self._active_scene
        has_audio = bool(scene.audio_assets)
        msg = "Delete Scene '{0}'?".format(scene.name)
        if has_audio:
            msg += "\n\nThis scene contains generated audio.\nAudio files will NOT be deleted."
        reply = QMessageBox.question(
            self, "Delete Scene", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Remove from project
        self._active_project.scenes.remove(scene)
        # Remove from Recents
        if self._recents_manager:
            self._recents_manager.remove_stale_scene(
                self._active_project.id, scene.id)
        # Activate another scene or create a new one
        if self._active_project.scenes:
            self._switch_to_scene(self._active_project.scenes[0].id)
        else:
            # No scenes left — create a new empty one
            new_scene = Scene(name="01 Untitled", status="draft")
            self._active_project.add_scene(new_scene)
            self._active_scene = new_scene
            self._current_scene = new_scene.name
            self._load_scene_state(new_scene)
        # P3.25 (audit SS-M09): PERSIST the deletion. The removal was
        # previously in-memory only — the deleted scene resurrected after
        # restart because the project.json on disk still contained it.
        if self._project_manager is not None and self._active_project is not None:
            try:
                self._project_manager.save_project(self._active_project)
            except Exception as exc:
                logger.error("P3.15: Failed to persist scene deletion: %s", exc)
                QMessageBox.warning(
                    self, APP_NAME,
                    "The scene was removed in this session, but saving the "
                    "project failed:\n{0}\n\nThe deletion may not survive a "
                    "restart.".format(exc))
        # Refresh UI
        self._update_project_scene_bar_menus()
        self._update_recents()
        logger.info("P3.15: Scene deleted: %s", scene.name)

    def _get_sorted_scenes(self) -> list:
        """P3.21: Return the active project's scenes sorted by sort_order."""
        if self._active_project is None:
            return []
        return sorted(self._active_project.scenes, key=lambda s: s.sort_order)

    def _on_scene_move_up(self) -> None:
        """P3.21: Move the active Scene up in the sort order."""
        if self._active_project is None or self._active_scene is None:
            return
        scenes = self._get_sorted_scenes()
        idx = next((i for i, s in enumerate(scenes) if s.id == self._active_scene.id), -1)
        if idx <= 0:
            return  # Already first
        # Swap sort_order values
        scenes[idx], scenes[idx - 1] = scenes[idx - 1], scenes[idx]
        scenes[idx].sort_order, scenes[idx - 1].sort_order = (
            scenes[idx - 1].sort_order, scenes[idx].sort_order)
        # Save + refresh
        if self._project_manager is not None:
            self._project_manager.save_project(self._active_project)
        self._update_project_scene_bar_menus()
        self._update_recents()
        logger.info("P3.21: Scene moved up: '%s'", self._active_scene.name)

    def _on_scene_move_down(self) -> None:
        """P3.21: Move the active Scene down in the sort order."""
        if self._active_project is None or self._active_scene is None:
            return
        scenes = self._get_sorted_scenes()
        idx = next((i for i, s in enumerate(scenes) if s.id == self._active_scene.id), -1)
        if idx < 0 or idx >= len(scenes) - 1:
            return  # Already last
        # Swap sort_order values
        scenes[idx], scenes[idx + 1] = scenes[idx + 1], scenes[idx]
        scenes[idx].sort_order, scenes[idx + 1].sort_order = (
            scenes[idx + 1].sort_order, scenes[idx].sort_order)
        # Save + refresh
        if self._project_manager is not None:
            self._project_manager.save_project(self._active_project)
        self._update_project_scene_bar_menus()
        self._update_recents()
        logger.info("P3.21: Scene moved down: '%s'", self._active_scene.name)

    def _update_project_scene_bar_menus(self) -> None:
        """P3.15: Refresh the Project + Scene dropdown menus in the top bar."""
        if not hasattr(self, "_project_scene_bar"):
            return
        # Project menu — all projects
        projects_data = []
        if self._project_manager is not None:
            for p in self._project_manager.list_projects():
                projects_data.append({"id": p.id, "name": p.name})
        active_pid = self._active_project.id if self._active_project else ""
        self._project_scene_bar.set_project_menu(projects_data, active_pid)
        # Scene menu — only active project's scenes
        scenes_data = []
        if self._active_project is not None:
            for s in self._get_sorted_scenes():
                scenes_data.append({"id": s.id, "name": s.name})
        active_sid = self._active_scene.id if self._active_scene else ""
        self._project_scene_bar.set_scene_menu(scenes_data, active_sid)

    def _on_edit_scene_name(self) -> None:
        """Edit the current scene name (via the context bar edit icon).

        P3.9: Now refreshes the sidebar scene list + Recents so the new
        name propagates everywhere immediately. Previously the sidebar
        continued showing the old name.

        P3.34: thin wrapper — the shared authoritative rename core is
        _rename_scene_entity (also used by the ALL SCENES pencil, which
        targets a SPECIFIC scene). One rename system, two entry points.
        """
        self._rename_scene_entity(None)

    def _on_sidebar_scene_rename(self, scene_id: str) -> None:
        """P3.34: rename a SPECIFIC Scene (ALL SCENES pencil click).

        Reuses the ONE authoritative rename workflow (_rename_scene_entity)
        — the same QInputDialog, entity mutation, unsaved tracking and
        UI-surface refresh as the ProjectSceneBar "Rename Scene" action.
        Works for the active Scene AND inactive Scenes (first/middle/last,
        any name length); it never reorders Scenes, never touches
        generation state or Scene identity (the scene_id is preserved).
        """
        self._rename_scene_entity(scene_id)

    def _rename_scene_entity(self, scene_id: Optional[str]) -> None:
        """Authoritative Scene rename core (P3.9 flow, P3.34 parameterized).

        scene_id None → the ACTIVE scene (legacy ProjectSceneBar path).
        scene_id set  → that specific Scene (ALL SCENES pencil path),
                        resolved in the active Project first, then across
                        all Projects (mirrors the context-list scope).

        The rename updates the actual Scene entity (scene.name), marks
        unsaved state, and refreshes every relevant UI surface:
        ALL SCENES context list, RECENTS, ProjectSceneBar (active scene
        label + its dropdown menus) — persistence flows through the
        normal Project save (the entity is the source of truth).
        """
        from PySide6.QtWidgets import QInputDialog
        # ---- Resolve the target Scene (+ its Project) ----
        scene = None
        project = None
        if scene_id is None:
            scene = self._active_scene
            project = self._active_project
        else:
            if self._active_project is not None:
                s = self._active_project.get_scene(scene_id)
                if s is not None:
                    scene = s
                    project = self._active_project
            if scene is None and self._project_manager is not None:
                # No-active-project fallback: search all Projects (the
                # ALL SCENES list can list scenes from every Project).
                for p in self._project_manager.list_projects():
                    s = p.get_scene(scene_id)
                    if s is not None:
                        scene = s
                        project = p
                        break
        if scene is None:
            return
        old_name = scene.name
        name, ok = QInputDialog.getText(
            self, "Rename Scene", "New scene name:", text=old_name)
        if not ok or not name.strip() or name.strip() == old_name:
            return
        name = name.strip()
        # ---- Mutate the authoritative entity (identity preserved) ----
        scene.name = name
        is_active = (self._active_scene is not None
                     and self._active_scene.id == scene.id)
        if is_active:
            self._current_scene = name
        self._scene_unsaved = True
        if hasattr(self, "_project_scene_bar"):
            if is_active:
                self._project_scene_bar.set_scene(name)
            self._project_scene_bar.set_unsaved()
        logger.info("Scene renamed: %s → %s", old_name, name)
        # P3.9: Refresh the sidebar scene list so the new name appears.
        if hasattr(self, "_sidebar") and self._active_project is not None:
            sidebar_scenes = [{"id": s.id, "name": s.name, "status": s.status}
                              for s in self._get_sorted_scenes()]
            self._sidebar.set_scenes(sidebar_scenes)
            if self._active_scene is not None:
                self._sidebar.set_active_scene(self._active_scene.id)
        # P3.9/P3.34: Refresh Recents AND the full context list so the new
        # name appears everywhere (ALL SCENES rows, Recent Scenes, the
        # ProjectSceneBar dropdown menus via _update_project_scene_bar_menus).
        if self._recents_manager is not None and project is not None:
            self._recents_manager.add_recent_scene(project.id, scene.id, name)
            self._update_recents()

    # ------------------------------------------------------------------
    # P2.2: Scene save/restore helpers — switching scenes preserves state
    # ------------------------------------------------------------------
    def _save_current_scene_state(self) -> None:
        """Save the current editor state into the active Scene entity.

        Called before switching to a different scene or saving the project.
        Captures: text, emotion/style/speed/pitch/delivery, generation
        parameters, Narration Block state, and P3.13: voice_profile_id.

        P3.25 (audit SS-H15): the previous silent ``except: pass`` blocks
        around params/blocks/voice persistence masked real failures — a
        raise swallowed here meant user work (Narration Blocks) was lost
        with NO trace. Each field is now handled individually and failures
        are logged (block-state failures at ERROR level — they are user
        work and must never disappear silently).
        """
        if self._active_scene is None:
            return
        scene = self._active_scene
        scene.text = self._editor.get_text()
        scene.emotion = self._emotion
        scene.style = self._style
        scene.speed = self._speed
        scene.pitch = self._pitch
        scene.delivery = self._delivery
        try:
            scene.parameters = self._control_panel.get_parameters()
        except Exception as exc:
            logger.error("Scene state save: failed to read generation "
                         "parameters for '%s': %s", scene.name, exc)
        # Save Narration Block state (user work — never silent on failure)
        try:
            scene.narration_blocks = [b.to_dict() for b in self._editor.block_manager.blocks]
        except Exception as exc:
            logger.error("Scene state save: FAILED to persist Narration "
                         "Blocks for '%s': %s", scene.name, exc)
        # P3.26 (Scene state contract): the editor mode is part of the
        # Scene's state — each Scene restores its own mode instead of
        # globally retaining the last one used.
        try:
            scene.editor_mode = self._editor.editor_mode_name
        except Exception as exc:
            logger.warning("Scene state save: editor mode persistence "
                           "failed for '%s': %s", scene.name, exc)
        # P3.26 (Part 4): the block selection is Scene state. It is only
        # meaningful in Narration Blocks mode (leaving that mode clears
        # the selection).
        try:
            scene.selected_block_id = (
                self._editor._selected_block_id
                if self._editor._mode == self._editor.MODE_BLOCKS else None)
        except Exception as exc:
            logger.warning("Scene state save: block selection persistence "
                           "failed for '%s': %s", scene.name, exc)
        # P3.13: Save the current Voice Profile selection into the Scene.
        # This makes the voice Scene-scoped — switching Scenes preserves
        # each Scene's independent voice selection.
        # P3.23 (design record §17): when a Character-driven Voice DISPLAY
        # override is active (a block with a Character is selected), the
        # transient displayed voice must NOT be persisted as the Scene
        # Voice — the Scene keeps its own voice.
        try:
            if not getattr(self, "_voice_display_overridden", False):
                scene.voice_profile_id = self._control_panel.get_selected_voice_id()
        except Exception as exc:
            logger.error("Scene state save: failed to persist voice "
                         "selection for '%s': %s", scene.name, exc)
        logger.info("P2: Saved scene state: '%s' (text=%d chars, blocks=%d, voice=%s)",
                     scene.name, len(scene.text), len(scene.narration_blocks),
                     scene.voice_profile_id)

    def _load_scene_state(self, scene: Scene) -> None:
        """Load a Scene's state into the editor and control panel.

        Called after switching to a different scene. Restores: text,
        emotion/style/speed/pitch/delivery, generation parameters,
        Narration Block state, and P3.13: voice_profile_id.

        P3.25 (audit SS-H01/H03): the previous truthy guards
        (``if scene.parameters:`` / ``if scene.voice_profile_id:``) made a
        Scene with a None value silently KEEP the previous Scene's value —
        Scene B (no voice / no params) inherited Scene A's state and the
        next save permanently destroyed B's None state. Both restores are
        now UNCONDITIONAL: None parameters reset the panel to defaults and
        None voice selects "(No voice)".

        P3.25 (audit SS-H15): each restore step now has its own narrow
        handler, so one failing setter no longer silently skips the
        remaining restores (partial-restore state leak).
        """
        # P3.23 (§17): a fresh scene load clears any character-driven
        # Voice display override — the new Scene's own voice is displayed.
        self._voice_display_overridden = False
        try:
            self._control_panel.set_source_indicator("voice", "")
        except Exception as exc:
            logger.warning("Scene state load: source indicator reset failed: %s", exc)
        # P3.26 (Scene state contract): the block scope is reset FIRST,
        # deterministically — never via cursor-signal side effects. The
        # previous Scene's selected block must not leak into this Scene.
        try:
            self._control_panel.set_block_scope(None)
            self._control_panel.set_source_indicator("", "")
        except Exception as exc:
            logger.warning("Scene state load: block scope reset failed: %s", exc)
        # Restore text — P3.26: inside the atomic scene-load window so the
        # text swap never offset-shifts the PREVIOUS Scene's blocks (the
        # restore below replaces the whole block model; see the block
        # restore section for the full contract).
        self._editor.begin_scene_load()
        try:
            self._editor.set_text(scene.text or "")
        finally:
            self._editor.end_scene_load()
        # Restore emotion/style/prosody (scene-level globals)
        self._emotion = scene.emotion
        self._style = scene.style
        self._speed = scene.speed or "Normal"
        self._pitch = scene.pitch or "Normal"
        self._delivery = scene.delivery or "Normal"
        # Update control panel — one handler per setter so a single
        # failure cannot skip the remaining restores (SS-H15).
        try:
            self._control_panel.set_emotion(self._emotion)
        except Exception as exc:
            logger.warning("Scene state load: set_emotion failed: %s", exc)
        try:
            self._control_panel.set_style(self._style)
        except Exception as exc:
            logger.warning("Scene state load: set_style failed: %s", exc)
        try:
            self._control_panel.set_speed(self._speed)
        except Exception as exc:
            logger.warning("Scene state load: set_speed failed: %s", exc)
        try:
            self._control_panel.set_pitch(self._pitch)
        except Exception as exc:
            logger.warning("Scene state load: set_pitch failed: %s", exc)
        try:
            self._control_panel.set_delivery(self._delivery)
        except Exception as exc:
            logger.warning("Scene state load: set_delivery failed: %s", exc)
        # P3.25 (SS-H03): UNCONDITIONAL parameters restore. A Scene with
        # parameters=None resets the panel to factory defaults — it must
        # never inherit the previous Scene's values.
        try:
            from engine.models import GenerationParameters
            params = scene.parameters if scene.parameters is not None \
                else GenerationParameters()
            self._control_panel.set_parameters(params)
        except Exception as exc:
            logger.warning("Scene state load: set_parameters failed: %s", exc)
        # P3.13 + P3.25 (SS-H01): UNCONDITIONAL voice restore. A Scene with
        # voice_profile_id=None selects "(No voice)" (the setter already
        # maps None → combo index 0) — the previous Scene's voice must not
        # leak across the switch and must not be persisted into the None
        # Scene on the next save.
        try:
            self._control_panel.set_selected_voice_id(scene.voice_profile_id)
        except Exception as exc:
            logger.warning("Scene state load: set_selected_voice_id failed: %s", exc)
        # Update the voice info display (clears to "No voice" when None).
        try:
            if scene.voice_profile_id and self._engine is not None:
                voice = self._engine.get_voice(scene.voice_profile_id)
                if voice:
                    self._control_panel.set_voice_info(voice)
                else:
                    self._control_panel.set_voice_info(None)
            else:
                self._control_panel.set_voice_info(None)
        except Exception as exc:
            logger.warning("Scene state load: set_voice_info failed: %s", exc)
        # Restore Narration Blocks + editor mode — P3.26 Scene state
        # contract. The whole restore is ATOMIC with respect to the block
        # manager: the text swap must never be fed into
        # NarrationBlockManager.on_text_changed while the PREVIOUS Scene's
        # blocks are still loaded (they would be offset-shifted as if the
        # user had edited the text — the stale-visuals defect).
        try:
            from engine.narration_blocks import PromptBlock
            restored_blocks = ([PromptBlock.from_dict(b)
                                for b in scene.narration_blocks]
                               if scene.narration_blocks else [])
            self._editor.begin_scene_load()
            try:
                self._editor.set_scene_blocks(restored_blocks,
                                              scene.text or "")
            finally:
                self._editor.end_scene_load()
        except Exception as exc:
            logger.warning("P2: Failed to restore narration blocks: %s", exc)
        # P3.26: restore the per-Scene editor mode through the REAL mode
        # machinery (radio buttons -> _set_mode), so all mode-driven UI
        # (properties panel, Re-detect button, generate capability)
        # follows. A Scene saved in Plain Text must come back in Plain
        # Text even if the user left it in Narration Blocks in another
        # Scene, and vice versa.
        try:
            self._editor.set_editor_mode(getattr(scene, "editor_mode", "plain"))
        except Exception as exc:
            logger.warning("Scene state load: editor mode restore failed "
                           "for '%s': %s", scene.name, exc)
        # P3.26 (Part 4): restore the Scene's block selection — only when
        # the id still exists in the restored blocks AND the Scene is in
        # Narration Blocks mode (deterministic policy: a stale/absent id
        # restores with no selection, never garbage).
        try:
            sel_id = getattr(scene, "selected_block_id", None)
            if (sel_id and self._editor._mode == self._editor.MODE_BLOCKS
                    and self._editor.block_manager.get_block(sel_id) is not None):
                self._editor._selected_block_id = sel_id
                self._editor._update_properties_panel()
                self._editor._render_block_visuals()
                # Drive the real selection path so the Right Panel scope,
                # effective values and Character voice display follow.
                self._editor.block_selected.emit(sel_id)
        except Exception as exc:
            logger.warning("Scene state load: block selection restore "
                           "failed for '%s': %s", scene.name, exc)
        # Update prompt preview
        self._update_prompt_preview()
        logger.info("P2: Loaded scene state: '%s' (text=%d chars, blocks=%d)",
                     scene.name, len(scene.text), len(scene.narration_blocks))

    def _switch_to_scene(self, scene_id: str) -> None:
        """Switch from the current scene to a different scene.

        P2.2: Saves the current scene's state, then loads the new scene's
        state into the editor. This is the critical fix for the scene
        switching data loss bug.
        """
        if self._active_project is None:
            return
        target = self._active_project.get_scene(scene_id)
        if target is None:
            logger.warning("P2: Scene not found: %s", scene_id)
            return
        if self._active_scene is not None and target.id == self._active_scene.id:
            return  # Already on this scene
        # Save current scene state
        self._save_current_scene_state()
        # Switch
        self._active_scene = target
        self._current_scene = target.name
        # Load new scene state
        self._load_scene_state(target)
        # Update UI
        if hasattr(self, "_project_scene_bar"):
            self._project_scene_bar.set_scene(target.name)
            self._project_scene_bar.set_saved()
        self._sidebar.set_active_scene(scene_id)
        logger.info("P2: Switched to scene: '%s'", target.name)
        # P3.4: Add to Recents
        if self._recents_manager is not None and self._active_project is not None:
            self._recents_manager.add_recent_scene(
                self._active_project.id, target.id, target.name)
            self._update_recents()

    def _on_delete_voice(self, voice_id: str) -> None:
        """Delete a voice profile (sidebar context menu).

        P3.43 §11: dependency-aware — Characters/Scenes referencing the
        profile are listed in the confirmation and their assignments are
        CLEARED after the delete (never silently dangling). The same flow
        the unified Voice Profiles screen uses (shared helpers).
        """
        if self._engine is None or not voice_id:
            return
        voice = self._engine.get_voice(voice_id)
        if voice is None:
            return
        dependencies = self._find_voice_dependencies(voice_id)
        if dependencies:
            message = (
                "Delete voice profile '{0}'?\n\n"
                "This profile is referenced by:\n{1}\n\n"
                "Deleting it will CLEAR these voice assignments.".format(
                    voice.name,
                    "\n".join("- {0}".format(d) for d in dependencies)))
        else:
            message = ("Are you sure you want to delete '{0}'?\n"
                       "This cannot be undone.".format(voice.name))
        reply = QMessageBox.question(
            self, "Delete Voice Profile", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._engine.delete_voice(voice_id)
        except Exception as exc:
            QMessageBox.critical(
                self, APP_NAME,
                "Failed to delete voice profile:\n{0}".format(exc))
            return
        if dependencies:
            self._clear_voice_references(voice_id)
        # VOICE_CHANGED refreshes all selectors from the authoritative state.
        self._refresh_sidebar()

    def _on_export_voice(self, voice_id: str) -> None:
        """Export a voice profile (from sidebar context menu).

        Exports the voice profile directory (WAV + metadata JSON) to a
        user-chosen destination folder. A subfolder named after the voice
        profile is created inside the destination.
        """
        if self._engine is None or not voice_id:
            return
        # Get the voice name for a better default folder name.
        voice = self._engine.get_voice(voice_id)
        voice_name = voice.name if voice else voice_id
        # Ask the user for a destination folder.
        export_dir = QFileDialog.getExistingDirectory(
            self, "Select Export Folder (a subfolder '{0}' will be created)".format(voice_id))
        if not export_dir:
            return
        try:
            path = self._engine.export_voice_profile(voice_id, export_dir)
            QMessageBox.information(
                self, "Export Success",
                "Voice '{0}' exported to:\n{1}\n\n"
                "The exported folder contains the reference WAV and metadata.".format(
                    voice_name, path))
        except Exception as exc:
            logger.exception("Voice export failed: %s", exc)
            QMessageBox.critical(self, "Export Failed",
                                 "Failed to export voice '{0}':\n{1}".format(
                                     voice_name, exc))

    def _on_open_output_folder(self) -> None:
        """Open the outputs/ folder in the file explorer."""
        outputs_dir = os.path.join(APP_ROOT, "outputs")
        os.makedirs(outputs_dir, exist_ok=True)
        try:
            if platform.system() == "Windows":
                os.startfile(outputs_dir)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", outputs_dir])
            else:
                subprocess.Popen(["xdg-open", outputs_dir])
        except Exception as exc:
            logger.error("Failed to open output folder: {0}".format(exc))
            QMessageBox.information(
                self, APP_NAME,
                "Could not open the output folder automatically.\n"
                "The folder is located at:\n{0}".format(outputs_dir))

    def _on_export_voice_menu(self) -> None:
        """File → Export Voice Profile: an ACTUAL export workflow (P3.43 §14).

        Previously this entry opened the whole Voice Library dialog as if
        the user were selecting a voice to manage — a misleading picker.
        Now it exports the CURRENTLY SELECTED voice profile (the right
        panel selection) directly through the authoritative facade. When
        nothing is selected it points the user to the Voice Profiles
        screen instead of guessing.
        """
        if self._engine is None:
            return
        voice_id = self._control_panel.get_selected_voice_id()
        if not voice_id:
            QMessageBox.information(
                self, APP_NAME,
                "No voice profile is currently selected.\n\n"
                "Select a voice first (Tools → Voice Profiles) and then "
                "use Export Voice Profile.")
            return
        voice = self._engine.get_voice(voice_id)
        if voice is None:
            QMessageBox.warning(
                self, APP_NAME,
                "The selected voice profile no longer exists:\n{0}".format(
                    voice_id))
            self._refresh_sidebar()
            return
        export_dir = QFileDialog.getExistingDirectory(
            self, "Select Export Folder (a subfolder '{0}' will be created)"
            .format(voice_id))
        if not export_dir:
            return
        try:
            path = self._engine.export_voice_profile(voice_id, export_dir)
            FeedbackDialog.information(
                self, APP_NAME,
                "Voice profile '{0}' exported".format(voice.name),
                "Location: {0}\n\n"
                "The exported folder contains the profile metadata, "
                "reference WAV, transcript and avatar.".format(path))
        except Exception as exc:
            logger.exception("Voice export failed: %s", exc)
            QMessageBox.critical(
                self, APP_NAME,
                "Failed to export voice profile:\n{0}".format(exc))

    def _on_documentation(self) -> None:
        """Show documentation info."""
        QMessageBox.information(
            self, APP_NAME,
            "GUINEO Documentation\n\n"
            "The user guide is README.md in the application folder.\n"
            "Specifications are in the spec/ folder.\n"
            "Reference materials are in the research/ folder.\n"
            "Third-party licences: THIRD_PARTY_LICENSES.md\n\n"
            "Key references:\n"
            "- Official model: https://huggingface.co/bosonai/higgs-tts-v3-4b\n"
            "- Transformers port: https://huggingface.co/multimodalart/higgs-audio-v3-tts-4b-transformers\n"
            "- Prompt syntax: https://huggingface.co/bosonai/higgs-tts-v3-4b/blob/main/PROMPTING.md")

    def _on_supported_languages(self) -> None:
        """Show the list of languages supported by the Higgs Audio V3 model."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea, QPushButton, QHBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("Supported Languages")
        dialog.setMinimumSize(600, 500)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        intro = QLabel(
            "<h3>Supported Languages</h3>"
            "<p>The Higgs Audio V3 model reaches single-digit WER/CER on "
            "<b>102 languages</b>, split into two quality tiers:</p>")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(6)

        tier1 = QLabel(
            "<b>Tier 1 — Production quality (WER/CER &lt; 5): 85 languages</b><br><br>"
            "Afrikaans, Arabic, Armenian, Assamese, Asturian, Azerbaijani, "
            "Bashkir, Basque, Belarusian, Bengali, Bosnian, Bulgarian, "
            "Catalan, Cebuano, Central Kurdish, Chinese, Croatian, Czech, "
            "Danish, Dutch, Eastern Mari, English, Esperanto, Estonian, "
            "Finnish, French, Galician, Georgian, German, Greek, Gujarati, "
            "Haitian Creole, Hausa, Hebrew, Hindi, Hungarian, Indonesian, "
            "Italian, Japanese, Javanese, Kannada, Kazakh, Korean, "
            "Kinyarwanda, Kyrgyz, Latvian, Lingala, Lithuanian, Luo, "
            "Macedonian, Malay, Malayalam, Maltese, M\u0101ori, Marathi, "
            "Mongolian, Nepali, Norwegian, Occitan, Persian, Polish, "
            "Portuguese, Romanian, Russian, Sepedi, Serbian, Shona, "
            "Slovak, Slovene, Spanish, Swahili, Swedish, Tagalog, Tajik, "
            "Tamil, Telugu, Thai, Turkish, Ukrainian, Urdu, Uyghur, "
            "Uzbek, Vietnamese, Xhosa, Zulu.")
        tier1.setWordWrap(True)
        tier1.setStyleSheet("QLabel { background-color: #1a2a1a; padding: 8px; border-radius: 4px; }")
        content_layout.addWidget(tier1)

        tier2 = QLabel(
            "<br><b>Tier 2 — Usable, less polished (WER/CER 5-10): 17 languages</b><br><br>"
            "Albanian, Chichewa/Nyanja, Eastern Punjabi, Ganda, Icelandic, "
            "Irish, Kabyle, Kabuverdianu, Kamba, Latin, Luxembourgish, "
            "Oromo, Pashto, Sindhi, Somali, Umbundu, Welsh.")
        tier2.setWordWrap(True)
        tier2.setStyleSheet("QLabel { background-color: #2a2a1a; padding: 8px; border-radius: 4px; }")
        content_layout.addWidget(tier2)

        note = QLabel(
            "<br><i>Note: Hungarian is in Tier 1 with production-quality "
            "output. The model handles 100+ languages total \u2014 the above are "
            "the evaluated ones with measured quality metrics.</i>")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 11px;")
        content_layout.addWidget(note)

        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dialog.exec()

    def _on_token_guide(self) -> None:
        """Show the Higgs token combination guide."""
        from ui.panels.token_guide import TokenGuideDialog
        dialog = TokenGuideDialog(self)
        dialog.exec()

    def _on_font_status(self) -> None:
        """Show the HTML design system font loading status.

        Displays which font families (Inter, Libre Franklin, JetBrains Mono)
        were successfully loaded from assets/fonts/ and their available
        weights. This is a developer diagnostic — it helps verify that the
        DESIGN.md typography tokens are backed by actually-loaded fonts.
        """
        try:
            from ui.font_loader import get_font_status
            status = get_font_status()
        except Exception as exc:
            status = "Font loader error: {0}".format(exc)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, APP_NAME,
            "HTML Design System — Font Status\n\n{0}".format(status),
        )

    def _on_copy_debug(self) -> None:
        """Copy debug information to clipboard."""
        from engine.version import get_debug_info
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(get_debug_info())
        self._status_bar.showMessage("Debug info copied to clipboard", 3000)

    def _on_theme_selected(self, theme_id: str) -> None:
        """Handle theme selection from the VIEW → Theme submenu.

        Applies the selected theme, persists it to settings.json,
        updates the submenu checkmarks, and refreshes all theme-dependent
        UI elements.

        Args:
            theme_id: the theme ID from ui.theme.THEMES (e.g. "dark",
                      "light", "synthwave", "retro-console", "modern-dark").
        """
        from ui.theme import THEMES, apply_theme
        from PySide6.QtWidgets import QApplication

        if theme_id not in THEMES:
            logger.warning("Unknown theme: %s", theme_id)
            return

        # Apply the new theme immediately.
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme_id)

        # Refresh the toolbar Generate button style (it uses Palette colors
        # which were just updated by apply_theme).
        if self._toolbar is not None:
            self._toolbar.refresh_theme()
        if self._top_nav is not None:
            self._top_nav.refresh_theme()
        # P3.45.4 (D-R6): the editor's block stripes are extra-selection
        # formats computed at highlight time — re-derive them so the new
        # theme's surface tokens apply immediately (same convention as the
        # toolbar/top_nav refresh above).
        if self._editor is not None:
            self._editor.refresh_theme()

        # Gate the ambient timer by theme (only run for translucent themes)
        self._update_ambient_for_theme(theme_id)

        # Update the VIEW → Theme submenu checkmarks.
        self._menu_bar.set_active_theme(theme_id)

        # Persist to settings.
        try:
            settings_dir = os.path.join(APP_ROOT, "settings")
            from engine.settings_manager import SettingsManager
            sm = self._settings_manager
            sm.set("application", "theme", theme_id)
        except Exception as exc:
            logger.warning("Could not persist theme: %s", exc)

        self._status_bar.showMessage(
            "Theme: {0}".format(theme_id.title()), 3000)
        logger.info("Theme switched to: %s", theme_id)

    def _on_toggle_show_blocks(self, checked: bool) -> None:
        """Toggle visual rendering of Prompt Blocks in the editor."""
        self._editor.set_show_blocks(checked)

    def _not_implemented(self) -> None:
        """Placeholder for unimplemented features."""
        QMessageBox.information(
            self, APP_NAME,
            "This feature is not yet implemented.")

    # ------------------------------------------------------------------
    # Settings and Benchmark
    # ------------------------------------------------------------------
    def _on_open_settings(self) -> None:
        """Open the settings dialog."""
        if self._engine is None:
            return
        from ui.panels.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self._engine._settings, self)
        dialog.settings_changed.connect(self._on_settings_changed)
        dialog.raise_()
        dialog.activateWindow()
        dialog.exec()

    def _on_settings_changed(self) -> None:
        """Called when settings are saved in the settings dialog."""
        # Reload generation defaults into the control panel
        defaults = self._engine.get_generation_defaults()
        self._control_panel.set_parameters(defaults)

        # Apply the new theme if it changed
        theme_name = self._engine._settings.get("application", "theme", "dark")
        from ui.theme import apply_theme
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme_name)

        # Refresh the toolbar Generate button style after a theme change.
        if self._toolbar is not None:
            self._toolbar.refresh_theme()
        if self._top_nav is not None:
            self._top_nav.refresh_theme()
        # P3.45.4 (D-R6): re-derive the editor's Palette-derived block
        # stripes immediately after the Settings-dialog theme change.
        if self._editor is not None:
            self._editor.refresh_theme()

        # Gate the ambient timer by theme (only run for translucent themes)
        self._update_ambient_for_theme(theme_name)

        # Update the VIEW → Theme submenu checkmark to match Settings.
        self._menu_bar.set_active_theme(theme_name)

        self._status_bar.showMessage("Settings saved — theme: {0}".format(theme_name), 3000)

    def _on_open_benchmark(self) -> None:
        """Open the benchmark dialog."""
        if self._engine is None:
            return
        from ui.panels.benchmark_dialog import BenchmarkDialog
        dialog = BenchmarkDialog(self._engine, self)
        dialog.raise_()
        dialog.activateWindow()
        dialog.exec()

    # ------------------------------------------------------------------
    # History actions
    # ------------------------------------------------------------------
    # L1: Removed dead _on_history_entry_selected handler.
    # It was connected to a signal that is never emitted by the active
    # ProjectSceneSidebar. History entry playback is handled via the
    # HistoryViewDialog and _on_recent_history_clicked instead.

    def _on_history_reuse(self, entry_id: str) -> None:
        """Reuse settings from a history entry."""
        if self._engine is None:
            return
        entry = self._engine._history.get_entry(entry_id)
        if entry is None:
            return
        # Load the prompt text
        self._editor.set_text(entry.prompt)
        # Load the generation parameters
        self._control_panel.set_parameters(entry.parameters)
        self._status_bar.showMessage(
            "Settings loaded from history entry {0}".format(entry.timestamp), 3000)

    def _on_history_delete(self, entry_id: str) -> None:
        """Delete a history entry."""
        if self._engine is None:
            return
        reply = QMessageBox.question(
            self, "Delete History Entry",
            "Delete this history entry?\n"
            "The audio file will also be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._engine.delete_history_entry(entry_id, delete_audio=True)
            self._refresh_sidebar()

    def _on_history_export(self, entry_id: str) -> None:
        """Export a history entry to a JSON file."""
        if self._engine is None:
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export History Entry",
            entry_id + ".json",
            "JSON Files (*.json);;All Files (*)")
        if file_path:
            self._engine._history.export_entry(entry_id, file_path)
            QMessageBox.information(self, "Export Success",
                                    "History entry exported to:\n{0}".format(file_path))

    def _on_history_project_changed(self, project: str) -> None:
        """Project name changed in the History tab.

        Persists the active project name to settings.json so it survives
        application restarts, then updates the status bar.
        """
        self._current_project = project
        # Persist to settings so the project name survives restart.
        try:
            settings_dir = os.path.join(APP_ROOT, "settings")
            from engine.settings_manager import SettingsManager
            sm = self._settings_manager
            sm.set("application", "current_project", project)
        except Exception as exc:
            logger.warning("Could not persist current_project: %s", exc)
        self._status_bar.showMessage(
            "Project: {0}".format(project), 3000)

    def _on_new_project(self) -> None:
        """File → New Project.

        P2.1: Creates a new Project entity with an initial Scene. Saves
        the current scene state first, then switches to the new project.
        The new Project is saved to disk via ProjectManager.
        """
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "New Project",
            "Project name:",
            text=self._current_project if self._current_project != "Default" else "")
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, APP_NAME,
                                "Project name cannot be empty.")
            return

        # P2.1: Save current scene state before switching projects
        self._save_current_scene_state()

        # Create a new Project entity
        new_project = Project(name=name)
        initial_scene = Scene(name="01 Untitled", status="draft")
        new_project.add_scene(initial_scene)

        # Save to disk via ProjectManager
        if self._project_manager is not None:
            try:
                self._project_manager.save_project(new_project)
            except Exception as exc:
                logger.warning("P2: Could not save new project: %s", exc)

        # Switch to the new project
        self._active_project = new_project
        self._active_scene = initial_scene
        self._current_project = name
        self._current_scene = "01 Untitled"
        self._scene_unsaved = True
        # P3.5 Final Correction: clear selected character (new project)
        self._selected_character_id = None

        # Persist project and scene to settings
        try:
            settings_dir = os.path.join(APP_ROOT, "settings")
            from engine.settings_manager import SettingsManager
            sm = self._settings_manager
            sm.set("application", "current_project", name)
            sm.set("application", "current_scene", self._current_scene)
        except Exception as exc:
            logger.warning("Could not persist project/scene: %s", exc)

        # Update UI
        if hasattr(self, "_project_scene_bar"):
            self._project_scene_bar.set_project(name)
            self._project_scene_bar.set_scene(self._current_scene)
            self._project_scene_bar.set_unsaved()
        if hasattr(self, "_sidebar"):
            self._sidebar.set_project(name)
            self._sidebar.set_scenes([
                {"id": initial_scene.id, "name": self._current_scene}
            ])
            self._sidebar.set_active_scene(initial_scene.id)
        self._editor.clear_text()

        self._status_bar.showMessage(
            "Project '{0}' created with scene '{1}'".format(
                name, self._current_scene), 4000)
        logger.info("P2: New project created: '%s' (id=%s), scene: '%s'",
                     name, new_project.id, self._current_scene)
        # P3.5 Final Correction: refresh context list so the new project
        # appears and is highlighted as active.
        if self._recents_manager is not None:
            self._recents_manager.add_recent_project(new_project.id, new_project.name)
            self._recents_manager.add_recent_scene(
                new_project.id, initial_scene.id, initial_scene.name)
            self._update_recents()

    def _on_open_project(self) -> None:
        """File → Open Project.

        P3.1: Supports both native Project format (via ProjectManager) and
        legacy .sproj import. Presents a dialog that lets the user choose:
        - Open a native Project from the projects/ directory
        - Import a legacy .sproj file

        When a native Project is opened:
        - ProjectManager loads the full Project (all Scenes, Characters)
        - The first Scene becomes active
        - Editor, controls, and sidebar are restored from the Scene state
        """
        from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

        # P3.1: First try to list native projects from ProjectManager
        native_projects = []
        if self._project_manager is not None:
            try:
                native_projects = self._project_manager.list_projects()
            except Exception:
                pass

        if native_projects:
            # Show a dialog: choose native project or import legacy
            choices = [f"{p.name} ({len(p.scenes)} scenes)" for p in native_projects]
            choices.append("── Import legacy .sproj file ──")
            choice, ok = QInputDialog.getItem(
                self, "Open Project",
                "Select a project to open:",
                choices, 0, False)
            if not ok:
                return
            if choice == "── Import legacy .sproj file ──":
                self._import_legacy_sproj()
                return
            # Open the selected native project
            idx = choices.index(choice)
            selected_project = native_projects[idx]
            self._load_native_project(selected_project)
        else:
            # No native projects — offer legacy import
            reply = QMessageBox.question(
                self, APP_NAME,
                "No native projects found in projects/ directory.\n\n"
                "Would you like to import a legacy .sproj file?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if reply == QMessageBox.StandardButton.Yes:
                self._import_legacy_sproj()

    def _load_native_project(self, project: Project) -> None:
        """Load a native Project (from ProjectManager) into the runtime.

        P3.1: Restores the full Project including all Scenes and Characters.
        The first Scene becomes active — its text, blocks, and settings
        are loaded into the editor.

        P3.5 Final Correction: Resets the selected character id when
        switching projects, since the old selection belongs to the previous
        project. The context list active state is refreshed via _update_recents.
        """
        # Save current scene state before switching
        self._save_current_scene_state()

        # Set the active project
        self._active_project = project
        self._current_project = project.name

        # P3.28 (§5 / Rec 7): recompute every Scene's DERIVED coverage
        # state on load — a persisted GENERATING (e.g. after a crash)
        # honestly downgrades, and legacy "complete" values may honestly
        # downgrade to "partial" (corrected semantics, not data loss).
        try:
            from engine.audio_provenance import recompute_all_scene_status
            recompute_all_scene_status(project)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("P3.28: coverage recompute on load failed: %s",
                           exc)

        # P3.5 Final Correction: clear the selected character — it belonged
        # to the previous project. Will be re-set if the user selects a
        # character in the new project.
        self._selected_character_id = None

        # Set the first scene as active
        if project.scenes:
            self._active_scene = project.scenes[0]
            self._current_scene = project.scenes[0].name
            self._load_scene_state(project.scenes[0])
        else:
            # Project has no scenes — create a default one
            default_scene = Scene(name="01 Untitled", project_id=project.id, status="draft")
            project.add_scene(default_scene)
            self._active_scene = default_scene
            self._current_scene = default_scene.name
            self._editor.clear_text()

        # Update UI
        if hasattr(self, "_project_scene_bar"):
            self._project_scene_bar.set_project(project.name)
            self._project_scene_bar.set_scene(self._current_scene)
            self._project_scene_bar.set_saved()
        if hasattr(self, "_sidebar"):
            self._sidebar.set_project(project.name)
            # P3.6: include scene status for the status badge
            sidebar_scenes = [{"id": s.id, "name": s.name, "status": s.status}
                              for s in project.scenes]
            self._sidebar.set_scenes(sidebar_scenes)
            if project.scenes:
                self._sidebar.set_active_scene(project.scenes[0].id)

        # Persist current project selection
        try:
            settings_dir = os.path.join(APP_ROOT, "settings")
            from engine.settings_manager import SettingsManager
            sm = self._settings_manager
            sm.set("application", "current_project", project.name)
            sm.set("application", "current_scene", self._current_scene)
        except Exception:
            pass

        # P3.22: Populate the NarrationEditor's block Character selector
        # with the project's characters.
        self._update_editor_characters()

        self._status_bar.showMessage(
            "Project loaded: {0} ({1} scenes, {2} characters)".format(
                project.name, len(project.scenes), len(project.characters)), 4000)
        logger.info("P3.1: Native project loaded: '%s' (id=%s, %d scenes, %d characters)",
                     project.name, project.id,
                     len(project.scenes), len(project.characters))
        # P3.4: Add to Recents
        if self._recents_manager is not None:
            self._recents_manager.add_recent_project(project.id, project.name)
            if self._active_scene is not None:
                self._recents_manager.add_recent_scene(project.id, self._active_scene.id, self._active_scene.name)
            self._update_recents()

    def _import_legacy_sproj(self) -> None:
        """Import a legacy .sproj file as a new native Project.

        P3.1/B2: Legacy .sproj files are imported (not opened directly).
        The import creates a new Project with a single Scene containing
        the .sproj's text and settings. The user can then save and
        reopen it as a native project.
        """
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Legacy Project File",
            "",
            "GUINEO Legacy Project (*.sproj);;All Files (*)")
        if not path:
            return
        if self._project_manager is None:
            QMessageBox.critical(self, APP_NAME, "ProjectManager not initialized.")
            return
        try:
            # Save current scene state
            self._save_current_scene_state()
            # Import the .sproj as a new Project
            project = self._project_manager.import_sproj(path)
            if project is None:
                QMessageBox.critical(self, APP_NAME, "Failed to import .sproj file.")
                return
            # Load the imported project
            self._load_native_project(project)
            QMessageBox.information(
                self, APP_NAME,
                "Legacy project imported as new native Project:\n"
                "  {0}\n\n"
                "The project has been saved to projects/{1}/.\n"
                "Use Save Project to update it going forward.".format(
                    project.name, project.id))
        except Exception as exc:
            logger.exception("Failed to import .sproj: %s", exc)
            QMessageBox.critical(self, APP_NAME,
                "Failed to import .sproj file:\n{0}".format(exc))

    def _on_save_project(self) -> None:
        """File → Save Project.

        P2.3: Saves the complete Project (all Scenes, Characters, settings)
        to disk via ProjectManager. The legacy .sproj format is still
        available as an export option — the new format uses project.json
        in a project directory.
        """
        # P2.3: Save current scene state into the Scene entity first
        self._save_current_scene_state()

        if self._active_project is None or self._project_manager is None:
            # Fallback: legacy .sproj save if project model not initialized
            self._save_project_legacy()
            return

        try:
            # Save the authoritative Project to disk
            self._project_manager.save_project(self._active_project)
            self._scene_unsaved = False
            if hasattr(self, "_project_scene_bar"):
                self._project_scene_bar.set_saved()
            project_dir = os.path.join(self._project_manager.directory,
                                      self._active_project.id)
            self._status_bar.showMessage(
                "Project saved: {0} ({1} scenes)".format(
                    self._active_project.name,
                    len(self._active_project.scenes)), 4000)
            logger.info("P2: Project saved: '%s' (id=%s, %d scenes, %d chars)",
                        self._active_project.name, self._active_project.id,
                        len(self._active_project.scenes),
                        len(self._active_scene.text) if self._active_scene else 0)
            FeedbackDialog.information(
                self, APP_NAME,
                "Project saved: {0}".format(self._active_project.name),
                "Scenes: {0}\nCharacters: {1}\n\n"
                "Location: projects/{2}/".format(
                    len(self._active_project.scenes),
                    len(self._active_project.characters),
                    self._active_project.id))
        except Exception as exc:
            logger.exception("P2: Failed to save project: %s", exc)
            QMessageBox.critical(
                self, APP_NAME,
                "Failed to save project:\n{0}".format(exc))

    def _save_project_legacy(self) -> None:
        """Legacy .sproj save (fallback when project model is not available)."""
        from PySide6.QtWidgets import QFileDialog
        default_fname = "{0}.sproj".format(
            self._current_project or "project")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project File (Legacy)",
            default_fname,
            "GUINEO Project (*.sproj);;All Files (*)")
        if not path:
            return
        if not path.lower().endswith(".sproj"):
            path += ".sproj"
        try:
            import json as _json
            from datetime import datetime
            text = self._editor.get_text() if self._editor else ""
            voice_id = self._control_panel.get_selected_voice_id() \
                if self._control_panel else None
            params = self._control_panel.get_parameters() \
                if self._control_panel else None
            data = {
                "format": "speechstudio-project",
                "version": 1,
                "project": self._current_project,
                "text": text,
                "voice_id": voice_id,
                "emotion": self._emotion,
                "style": self._style,
                "speed": self._speed,
                "pitch": self._pitch,
                "delivery": self._delivery,
                "parameters": params.to_dict() if params else {},
                "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(path, "w", encoding="utf-8") as fh:
                _json.dump(data, fh, indent=2, ensure_ascii=False)
            self._status_bar.showMessage(
                "Project saved (legacy): {0}".format(path), 4000)
            logger.info("Legacy project file saved: %s", path)
        except Exception as exc:
            logger.exception("Failed to save legacy project file: %s", exc)
            QMessageBox.critical(
                self, APP_NAME,
                "Failed to save project file:\n{0}".format(exc))

    def _on_export_project(self) -> None:
        """P3.7: File → Export Project.

        Opens the ExportProjectDialog which lets the user choose a
        destination folder and toggle audio/voice asset inclusion.
        The export is a COPY operation — the original Project is never
        modified.
        """
        if self._active_project is None:
            QMessageBox.information(self, APP_NAME,
                "No active project.\n\nCreate or open a project first.")
            return
        from ui.panels.export_project_dialog import ExportProjectDialog
        voice_lookup = None
        if self._engine is not None:
            voice_lookup = self._engine.get_voice
        dialog = ExportProjectDialog(
            self._active_project, APP_ROOT, voice_lookup, self)
        dialog.exec()
        result = dialog.export_result
        if result and result.success:
            logger.info("P3.7: Project '%s' exported to %s",
                        self._active_project.name, result.export_dir)

    def _on_assemble_scenes(self) -> None:
        """P3.22/P3.23 Scene Chain: File → Assemble Scenes...

        Opens the AssembleScenesDialog which lets the user select scenes,
        reorder them, configure inter-scene silence + normalization, and
        optionally create an MP3. The combined audio is assembled from the
        scenes' generated WAV files and added to Project.combined_outputs.

        P3.23 (design record §15/§61): every Combined Audio export creates
        its own History entry (type "combined") referencing the Project,
        the ordered Scene IDs, format, duration and output path — WITHOUT
        duplicating the original per-Scene generation records.

        P3.23 (design record §68): if Scene generation is running, the
        assembly is blocked (prevents racing the batch writer).
        """
        if self._active_project is None:
            QMessageBox.information(self, APP_NAME,
                "No active project.\n\nCreate or open a project first.")
            return
        # §68: block assembly while generation is running.
        batch_running = (self._batch_manager is not None
                         and self._batch_manager.is_running)
        engine_busy = (self._engine is not None
                       and getattr(self._engine, "is_generating", False))
        if batch_running or engine_busy:
            QMessageBox.warning(
                self, APP_NAME,
                "Audio generation is currently running.\n\n"
                "Please wait for it to finish (or stop it) before "
                "assembling combined audio — assembling during generation "
                "could read partially-written files.")
            return
        from ui.panels.assemble_dialog import AssembleScenesDialog
        dialog = AssembleScenesDialog(self._active_project, APP_ROOT, self)
        # P3.28 follow-up (review remedy): the dialog's [Combine Scene
        # Now] rows route through the SAME combine handler, by scene id.
        dialog.combine_scene_now_requested.connect(
            self._on_combine_scene_by_id)
        dialog.exec()
        if dialog.assembly_result and dialog.assembly_result.success:
            result = dialog.assembly_result
            entry = dialog.combined_entry or {}
            # P3.23 (design record §15/§61): one HistoryEntry per Combined
            # Audio export. The original per-Scene generation entries are
            # NOT duplicated or modified.
            try:
                from engine.models import GenerationResult
                from datetime import datetime as _dt
                scene_names = []
                for sid in (entry.get("scene_ids")
                            or entry.get("source_scene_ids") or []):
                    scn = next((s for s in self._active_project.scenes
                                if s.id == sid), None)
                    scene_names.append(scn.name if scn else sid)
                combined_rel = result.output_path
                if os.path.isabs(combined_rel):
                    try:
                        combined_rel = os.path.relpath(
                            combined_rel, APP_ROOT)
                    except ValueError:
                        combined_rel = result.output_path
                fmt_desc = "WAV + MP3" if result.mp3_created else "WAV"
                hist_result = GenerationResult(
                    success=True,
                    output_path=combined_rel,
                    output_duration=result.total_duration,
                    output_sample_rate=24000,
                    generation_time=0.0,   # N/A for assembly
                    realtime_factor=0.0,   # N/A for assembly
                    parameters=None,
                    voice_profile=None,
                    prompt=("[Combined Audio - {0} scenes: {1}]".format(
                        result.scene_count, ", ".join(scene_names))),
                    timestamp=_dt.now().strftime("%Y%m%d_%H%M%S"),
                    warnings=list(result.errors),
                    errors=[],
                    gpu_name="",
                    vram_usage_mb=0.0,
                    project=self._active_project.name,
                    scene_id=None,
                    scene_name="Combined ({0} scenes)".format(
                        result.scene_count),
                    character_id=None,
                    speaker=None,
                )
                self._engine._history.add(
                    hist_result,
                    entry_type="project_combined",
                    extra={
                        # P3.28 §20: combined outputs retain scene IDs,
                        # source asset IDs, format, duration, output path.
                        "scene_ids": list(entry.get("scene_ids") or []),
                        "source_ids": list(
                            entry.get("audio_asset_ids") or []),
                        "format": ("wav+mp3" if result.mp3_created
                                   else "wav"),
                        "combined_version": entry.get("version"),
                    })
                # Notify the UI to refresh the History tab (main thread —
                # this is a UI-triggered action, not a worker callback).
                from engine.events import Event, EventType
                self._engine._event_bus.emit(
                    Event(EventType.HISTORY_UPDATED))
                logger.info(
                    "P3.23: History entry added for combined audio "
                    "(%s scenes, %s, %.2fs): %s",
                    result.scene_count, fmt_desc, result.total_duration,
                    combined_rel)
            except Exception as exc:
                logger.warning(
                    "Could not add history entry for combined audio: %s",
                    exc)
            # Save the project so combined_outputs persists.
            # P3.23 FIX (E2E-found bug): this called the non-existent
            # self._save_project() — an AttributeError crashed the save
            # after EVERY successful assembly (combined_outputs never
            # persisted; the P3.22 wiring tests were AST-only and never
            # executed this path). The real handler is _on_save_project().
            self._on_save_project()
            logger.info("P3.22: Combined audio assembled for '%s' (%d scenes, %.2fs)",
                        self._active_project.name,
                        dialog.assembly_result.scene_count,
                        dialog.assembly_result.total_duration)

    def _on_save_scene(self) -> None:
        """File → Save Dialogue Scene.

        Saves the current batch jobs as a .scene.json file so the user can
        resume the multi-speaker dialogue later.
        """
        from PySide6.QtWidgets import QFileDialog
        if self._batch_manager is None or not self._batch_manager.jobs:
            QMessageBox.information(
                self, APP_NAME,
                "No batch jobs to save. Generate a multi-speaker narration "
                "first, then save the scene.")
            return
        # Use scene name (not project name) for the default filename
        default_fname = "{0}.scene.json".format(
            self._current_scene or self._current_project or "dialogue")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Dialogue Scene",
            default_fname,
            "GUINEO Scene (*.scene.json);;All Files (*)")
        if not path:
            return
        if not path.endswith(".scene.json"):
            path += ".scene.json"
        try:
            # Update save state
            if hasattr(self, "_project_scene_bar"):
                self._project_scene_bar.set_saving()
            from engine.scene_persistence import scene_from_batch_jobs
            scene = scene_from_batch_jobs(
                self._batch_manager.jobs,
                scene_name=self._current_scene or "Untitled",
                project=self._current_project)
            scene.save(path)
            self._scene_unsaved = False
            if hasattr(self, "_project_scene_bar"):
                self._project_scene_bar.set_saved()
            FeedbackDialog.information(
                self, APP_NAME,
                "Scene saved",
                "Location: {0}\n\n"
                "{1} lines, {2} speakers.\n"
                "Use File → Load Dialogue Scene to resume later.".format(
                    path, len(scene.lines),
                    len({l.speaker for l in scene.lines if l.speaker})))
        except Exception as exc:
            logger.exception("Failed to save scene: %s", exc)
            if hasattr(self, "_project_scene_bar"):
                self._project_scene_bar.set_save_failed()
            QMessageBox.critical(
                self, APP_NAME,
                "Failed to save scene:\n{0}".format(exc))

    def _on_load_scene(self) -> None:
        """File → Load Dialogue Scene.

        Loads a .scene.json file and populates the batch manager with the
        scene's jobs (resetting their status to PENDING so they can be
        regenerated, but preserving output paths for already-completed lines).
        """
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Dialogue Scene",
            "",
            "GUINEO Scene (*.scene.json);;All Files (*)")
        if not path:
            return
        try:
            from engine.scene_persistence import DialogueScene
            from engine.batch_manager import BatchJob, JobStatus
            from engine.models import GenerationParameters
            scene = DialogueScene.load(path)
            if not scene.lines:
                QMessageBox.warning(self, APP_NAME, "Scene has no lines.")
                return
            # Populate the batch manager with the scene's lines.
            if self._batch_manager is None:
                return
            bm = self._batch_manager
            bm.clear_all()
            for i, line in enumerate(scene.lines):
                # Determine job status from the line's generation_status.
                try:
                    status = JobStatus(line.generation_status)
                except ValueError:
                    status = JobStatus.PENDING
                # If the output file still exists, keep it COMPLETED; otherwise reset.
                if status == JobStatus.COMPLETED and line.output_path:
                    full = line.output_path
                    if not os.path.isabs(full):
                        full = os.path.join(APP_ROOT, full)
                    if not os.path.isfile(full):
                        status = JobStatus.PENDING
                job = BatchJob(
                    name="{0}: {1}".format(line.speaker, i+1) if line.speaker else "Line {0}".format(i+1),
                    prompt=line.text,
                    voice_id=line.voice_id,
                    parameters=GenerationParameters(),
                    output_filename=line.output_filename,
                    project=scene.project,
                    speaker=line.speaker or None,
                    # C2 FIX: propagate scene/character context
                    scene_id=scene.id,
                    scene_name=scene.name,
                    character_id=None,  # per-line character not tracked in saved scenes
                    status=status,
                    output_path=line.output_path if status == JobStatus.COMPLETED else None,
                    output_duration=line.output_duration if status == JobStatus.COMPLETED else 0.0,
                )
                bm.add_job(job)
            # Open the batch dialog.
            # P3.35: single live instance + the ONE shared wiring helper
            # (same code path as the menu Batch Queue and Long Generation).
            if self._engine is not None:
                self._present_batch_dialog(scene=None)
            QMessageBox.information(
                self, APP_NAME,
                "Scene loaded: {0}\n{1} lines, {2} speakers.\n\n"
                "Completed lines are preserved. Pending/failed lines can be\n"
                "generated via the Start button in the Batch dialog.".format(
                    scene.name, len(scene.lines),
                    len({l.speaker for l in scene.lines if l.speaker})))
        except Exception as exc:
            logger.exception("Failed to load scene: %s", exc)
            QMessageBox.critical(
                self, APP_NAME,
                "Failed to load scene:\n{0}".format(exc))

    # ------------------------------------------------------------------
    # View operations
    # ------------------------------------------------------------------
    def _toggle_fullscreen(self) -> None:
        """P3.20: Toggle full screen mode — flicker-free.

        Root cause of flicker: showFullScreen()/showNormal() trigger
        multiple window state changes + repaints. The fix:
        1. Use setUpdatesEnabled(False) during the transition
        2. Use setWindowState() instead of showFullScreen()/showNormal()
        3. Restore geometry from saved state
        4. Re-enable updates after the transition
        """
        self.setUpdatesEnabled(False)
        try:
            if self.isFullScreen():
                self.setWindowState(Qt.WindowState.WindowNoState)
                if hasattr(self, "_saved_geometry") and self._saved_geometry:
                    self.restoreGeometry(self._saved_geometry)
            else:
                self._saved_geometry = self.saveGeometry()
                self.setWindowState(Qt.WindowState.WindowFullScreen)
        finally:
            self.setUpdatesEnabled(True)
            self.activateWindow()
            self.raise_()

    def _focus_sidebar(self) -> None:
        self._sidebar.setFocus()

    def _focus_history(self) -> None:
        """P3.3: Open the History view dialog.

        Replaces the temporary QMessageBox placeholder with a real
        History view backed by HistoryManager.
        """
        if self._engine is None:
            return
        entries = self._engine.get_history()
        from ui.panels.history_view import HistoryViewDialog
        dialog = HistoryViewDialog(entries, self._active_project, self)
        dialog.open_scene_requested.connect(self._on_history_open_scene)
        dialog.exec()

    def _on_history_open_scene(self, project_name: str, scene_id: str,
                                scene_name: str) -> None:
        """P3.3: Open a Scene from a History entry.

        Uses the authoritative ProjectManager / Project model to find
        the referenced Scene. Does NOT reconstruct a Scene from History JSON.
        """
        if not scene_id:
            QMessageBox.information(self, APP_NAME,
                "This history entry has no Scene reference.")
            return
        # Try to find the project by name
        if self._project_manager is None:
            QMessageBox.warning(self, APP_NAME, "ProjectManager not available.")
            return
        # Search all projects for the scene
        projects = self._project_manager.list_projects()
        for project in projects:
            scene = project.get_scene(scene_id)
            if scene is not None:
                # Found the project + scene — load it
                self._load_native_project(project)
                # Switch to the specific scene
                self._switch_to_scene(scene_id)
                self._status_bar.showMessage(
                    "Opened Scene '{0}' from History (Project: {1})".format(
                        scene.name, project.name), 4000)
                return
        # Scene not found in any project
        QMessageBox.warning(self, APP_NAME,
            "Scene not found.\n\n"
            "The Scene referenced by this history entry may have been "
            "deleted or belongs to a project that no longer exists.\n\n"
            "Scene ID: {0}\nScene name: {1}".format(scene_id, scene_name or "Unknown"))

    def _reset_layout(self) -> None:
        """Reset the splitter sizes: symmetric 400px side panels, fluid editor."""
        self._h_splitter.setSizes([400, 700, 400])
        self.resize(1440, 900)
        self._save_layout()

    def _save_layout(self) -> None:
        """Save window geometry. Side panel widths are fixed (400px) via
        setFixedWidth() and are NOT saved — only window size is persisted."""
        if self._engine is None:
            return
        try:
            s = self._engine._settings
            s.set("window", "width", self.width())
            s.set("window", "height", self.height())
            # Deliberately do NOT save sidebar_width / control_panel_width.
            # The panels are fixed-width via setFixedWidth(400).
        except Exception:
            pass

    def _restore_layout(self) -> None:
        """Restore window geometry. Side panel widths are fixed (400px) via
        setFixedWidth() and are NOT restored from saved settings."""
        if self._engine is None:
            return
        try:
            s = self._engine._settings
            width = s.get("window", "width", 1440)
            height = s.get("window", "height", 900)
            # Clamp to minimum that accommodates fixed 400px panels + 200px editor
            if width < 1100:
                width = 1440
            if height < 600:
                height = 900
            self.resize(width, height)
            # Panels are fixed-width — just set splitter sizes for the editor
            editor_w = max(200, width - 800 - 32 - 12)  # 2×400 panels + margins + handles
            self._h_splitter.setSizes([400, editor_w, 400])
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------
    def _update_status_bar(self) -> None:
        """Update the status bar with current environment info."""
        if self._engine is not None:
            status = self._engine.get_model_status()
            self._status_bar.update_model_status(status)
        else:
            # Fallback for when engine is not available
            self._status_bar.update_model_status_simple(
                model_ok=False, cuda_ok=False)

    # ------------------------------------------------------------------
    # About dialog
    # ------------------------------------------------------------------
    def _show_about(self) -> None:
        from engine.version import get_debug_info, get_full_version_string
        from PySide6.QtWidgets import QApplication

        debug_info = get_debug_info()

        msg = QMessageBox(self)
        msg.setWindowTitle("About " + APP_NAME)
        msg.setIcon(QMessageBox.Icon.Information)

        debug_html = debug_info.replace("\n", "<br>")
        msg.setText("<h3>{0}</h3><p>{1}</p>"
                     "<p>A portable desktop frontend for local Higgs Audio V3 TTS.</p>"
                     "<p>Built with Higgs TTS 3 licensed from Boson AI USA, Inc. "
                     "Research and non-commercial use; see THIRD_PARTY_LICENSES.md "
                     "for licensing details.</p>"
                     "<p style='color: gray; font-family: monospace; font-size: 11px;'>{2}</p>"
                     "<p style='color: gray'>All processing is local. No cloud, no telemetry.</p>".format(
            APP_NAME, get_full_version_string(), debug_html))

        copy_btn = msg.addButton("Copy Debug Info", QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Ok)
        msg.exec()

        if msg.clickedButton() == copy_btn:
            QApplication.clipboard().setText(debug_info)
            self._status_bar.showMessage("Debug info copied to clipboard", 3000)

    # ------------------------------------------------------------------
    # Qt event hooks
    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        key = event.key()
        mod = event.modifiers()

        # Ctrl+Enter -> Generate
        if mod & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_Return:
            self._on_generate()
            return
        # P3.25 (audit SS-H13): ONE authoritative Esc handler with an
        # explicit priority chain. Esc was previously registered BOTH as a
        # Stop QAction shortcut (ApplicationShortcut context — intercepted
        # every Esc app-wide) and in keyPressEvent, so the key could never
        # exit fullscreen or close the search bar.
        #
        # Priority: (1) stop a running generation, (2) exit fullscreen,
        # (3) close the search bar, (4) default behaviour. Modal dialogs
        # run their own event loops and reject/close themselves on Esc
        # before this handler is ever reached.
        if key == Qt.Key.Key_Escape:
            if self._engine is not None and \
                    getattr(self._engine, "is_generating", False):
                self._on_stop()
                return
            if self.windowState() & Qt.WindowState.WindowFullScreen:
                self._toggle_fullscreen()
                return
            search_bar = getattr(self._editor, "_search_bar", None)
            if search_bar is not None and search_bar.isVisible():
                self._editor._on_toggle_search()
                return
            # Nothing to dismiss — fall through to default handling.
            super().keyPressEvent(event)
            return
        # Space -> Play/Pause toggle (only when editor is not focused)
        if key == Qt.Key.Key_Space and not self._editor.hasFocus():
            if self._engine is not None:
                state = self._engine.get_playback_state()
                if state == "playing":
                    self._on_pause()
                else:
                    self._on_play()
            return

        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        """Keep the ambient background sized to the full window.

        The TopNavigation, ProjectSceneBar, and MainArea are all positioned
        by the Qt layout system (QVBoxLayout on the central shell). No
        manual setGeometry calls are needed for them.
        """
        super().resizeEvent(event)
        if self._ambient_bg is not None:
            self._ambient_bg.setGeometry(0, 0, self.width(), self.height())

    def _update_ambient_for_theme(self, theme_name: str) -> None:
        """Enable or disable the ambient background based on the active theme.

        Calls :meth:`AmbientBackgroundWidget.set_theme_active` which:
          - Shows/hides the widget.
          - Starts/stops the animation timer.
          - Does NOT affect the explicit-pause state (so a model-load
            pause survives theme changes).

        For translucent themes (``panel_alpha < 255``), the ambient
        background is active. For opaque themes, it is hidden and the
        timer is stopped (saves CPU).
        """
        if self._ambient_bg is None:
            return
        from ui.theme import THEMES
        p = THEMES.get(theme_name)
        is_translucent = (p is not None and p.panel_alpha < 255)
        self._ambient_bg.set_theme_active(is_translucent)
        if is_translucent:
            logger.info("Ambient background active for theme: %s", theme_name)
        else:
            logger.info("Ambient background inactive for theme: %s", theme_name)

    def hideEvent(self, event) -> None:
        """Handle window minimize/hide — let Qt handle it normally.

        The ambient background's animation timer continues to run while
        the window is minimized (the CPU cost is negligible at 2 FPS
        with pixmap caching). If we wanted to save even that, we could
        call ``pause()`` here and ``resume()`` in ``showEvent``, but
        the 2 FPS timer + cached blit is cheap enough that it's not
        worth the complexity.
        """
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        """Handle window restore — refresh the ambient background state."""
        super().showEvent(event)
        if self._ambient_bg is not None:
            try:
                settings_dir = os.path.join(APP_ROOT, "settings")
                from engine.settings_manager import SettingsManager
                _sm = self._settings_manager
                theme = _sm.get("application", "theme", "dark")
                self._update_ambient_for_theme(theme)
            except Exception:
                pass

    def closeEvent(self, event) -> None:
        """Handle window close - save layout and shut down the engine."""
        # Stop the ambient background animation cleanly.
        if self._ambient_bg is not None:
            self._ambient_bg.stop()
        self._save_layout()
        if self._engine is not None:
            self._engine.shutdown()
        event.accept()
        logger.info("Main window closed.")
