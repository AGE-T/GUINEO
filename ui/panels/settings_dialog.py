"""
SpeechStudio Settings Dialog.

UI Specification, section 21:
    General, Generation, Paths, Audio, Performance, Developer, Appearance.
    No Python editing required.
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox,
    QPushButton, QFileDialog, QGroupBox, QSlider, QSizePolicy,
)

from ui.theme import Palette


class SettingsDialog(QDialog):
    """Application settings dialog with tabbed sections.

    All settings are stored as JSON by SettingsManager. This dialog provides
    a graphical editor for every setting.
    """

    settings_changed = Signal()

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self._settings = settings_manager
        self.setWindowTitle("Settings")
        self.resize(600, 500)
        self.setMinimumSize(500, 400)
        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._tabs = QTabWidget()

        self._build_general_tab()
        self._build_generation_tab()
        self._build_paths_tab()
        self._build_audio_tab()
        self._build_performance_tab()
        self._build_developer_tab()
        self._build_appearance_tab()

        layout.addWidget(self._tabs)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setToolTip("Reset all settings to their default values")
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setProperty("accent", "true")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # General tab
    # ------------------------------------------------------------------
    def _build_general_tab(self) -> None:
        tab = QWidget()
        layout = QFormLayout(tab)

        # P3.16 AUDIT: Theme was DUPLICATED here and in Appearance tab.
        # The Appearance tab is authoritative (it's the one actually saved
        # in _on_save). Removed from General to avoid confusion.
        # P3.16 AUDIT: Developer Mode was DUPLICATED here and in Developer tab.
        # The Developer tab is authoritative. Removed from General.

        self._check_model = QCheckBox("Check for model on startup")
        self._check_model.setToolTip(
            "When checked, the application checks if the TTS model is available\n"
            "on startup and offers to load it. When unchecked, the model is\n"
            "not checked or loaded until explicitly requested.")
        layout.addRow("", self._check_model)

        self._tabs.addTab(tab, "General")

    # ------------------------------------------------------------------
    # Generation tab
    # ------------------------------------------------------------------
    def _build_generation_tab(self) -> None:
        tab = QWidget()
        layout = QFormLayout(tab)

        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.1, 2.0)
        self._temperature.setSingleStep(0.05)
        self._temperature.setStepType(QDoubleSpinBox.StepType.DefaultStepType)
        layout.addRow("Default Temperature:", self._temperature)

        self._top_p = QDoubleSpinBox()
        self._top_p.setRange(0.1, 1.0)
        self._top_p.setSingleStep(0.05)
        self._top_p.setStepType(QDoubleSpinBox.StepType.DefaultStepType)
        layout.addRow("Default Top P:", self._top_p)

        self._top_k = QSpinBox()
        self._top_k.setRange(1, 500)
        self._top_k.setStepType(QSpinBox.StepType.DefaultStepType)
        layout.addRow("Default Top K:", self._top_k)

        self._max_tokens = QSpinBox()
        self._max_tokens.setRange(128, 8192)
        self._max_tokens.setSingleStep(128)
        self._max_tokens.setStepType(QSpinBox.StepType.DefaultStepType)
        layout.addRow("Default Max New Tokens:", self._max_tokens)

        self._silence = QDoubleSpinBox()
        self._silence.setRange(0.0, 5.0)
        self._silence.setSingleStep(0.1)
        self._silence.setStepType(QDoubleSpinBox.StepType.DefaultStepType)
        self._silence.setSuffix(" s")
        layout.addRow("Default Append Silence:", self._silence)

        self._auto_play = QCheckBox("Auto-play after generation")
        layout.addRow("", self._auto_play)

        self._normalize = QCheckBox("Normalize output volume")
        layout.addRow("", self._normalize)

        self._tabs.addTab(tab, "Generation")

    # ------------------------------------------------------------------
    # Paths tab
    # ------------------------------------------------------------------
    def _build_paths_tab(self) -> None:
        tab = QWidget()
        layout = QFormLayout(tab)

        self._output_dir = QLineEdit()
        output_browse = QPushButton("Browse...")
        output_browse.clicked.connect(self._browse_output_dir)
        out_row = QHBoxLayout()
        out_row.addWidget(self._output_dir)
        out_row.addWidget(output_browse)
        layout.addRow("Output Directory:", out_row)

        info = QLabel("All paths are relative to the application directory.\n"
                      "The application is portable - no absolute paths are used.")
        info.setStyleSheet("color: %s;" % Palette.TEXT_SECONDARY)
        info.setWordWrap(True)
        layout.addRow("", info)

        self._tabs.addTab(tab, "Paths")

    # ------------------------------------------------------------------
    # Audio tab
    # ------------------------------------------------------------------
    def _build_audio_tab(self) -> None:
        tab = QWidget()
        layout = QFormLayout(tab)

        self._format = QComboBox()
        self._format.addItems(["wav"])
        self._format.setEnabled(False)  # only WAV currently
        layout.addRow("Default Output Format:", self._format)

        # P3.16B: Renamed to clarify this is PLAYBACK volume only (does not
        # affect generated WAV amplitude).
        self._volume = QSlider(Qt.Orientation.Horizontal)
        self._volume.setRange(0, 100)
        self._volume.setValue(100)
        self._volume.setToolTip(
            "Default playback volume for the audio player.\n"
            "Does NOT affect the generated WAV file amplitude.")
        layout.addRow("Default Playback Volume:", self._volume)

        # P3.16B AUDIT: "Auto-play generated audio" was a DUPLICATE of
        # Generation → Auto-play after generation. Both controlled the same
        # behaviour (params.auto_play in GenerationParameters). The Audio
        # tab version (audio.auto_play) was never read at runtime.
        # Removed. The Generation tab's Auto Play is authoritative.

        # Dialogue concatenation settings (WORKING — used by MainWindow).
        from PySide6.QtWidgets import QSpinBox
        self._concat_silence = QSpinBox()
        self._concat_silence.setRange(50, 2000)
        self._concat_silence.setSingleStep(50)
        self._concat_silence.setSuffix(" ms")
        self._concat_silence.setToolTip(
            "Base silence between speakers in dialogue concatenation.\n"
            "Actual silence varies randomly between 50% and 150% of this value\n"
            "if 'Randomize silence' is checked (for natural pacing).\n"
            "Default: 300 ms")
        layout.addRow("Dialogue Silence:", self._concat_silence)

        self._concat_randomize = QCheckBox("Randomize silence (natural pacing)")
        self._concat_randomize.setToolTip(
            "When checked, the silence between dialogue lines varies\n"
            "randomly (50%-150% of the base value), simulating natural\n"
            "conversation pacing.")
        layout.addRow("", self._concat_randomize)

        self._tabs.addTab(tab, "Audio")

    # ------------------------------------------------------------------
    # Performance tab
    # ------------------------------------------------------------------
    def _build_performance_tab(self) -> None:
        tab = QWidget()
        layout = QFormLayout(tab)

        # P3.16B AUDIT: "Keep model loaded" and "Release tensors" were DEAD —
        # persisted but never read by the engine. Removed to avoid dead controls.
        # The engine manages model lifecycle internally.

        # --- Model Precision (WORKING — read by model_manager.py) ---
        from PySide6.QtWidgets import QButtonGroup, QRadioButton, QGroupBox, QVBoxLayout

        precision_group = QGroupBox("Model Precision")
        precision_layout = QVBoxLayout(precision_group)
        self._precision_group = QButtonGroup(self)
        self._precision_group.setExclusive(True)

        self._precision_bf16 = QRadioButton("bfloat16 (Recommended)")
        self._precision_bf16.setToolTip(
            "Recommended by BosonAI.\n"
            "Native checkpoint precision.\n"
            "Best balance between stability, speed and VRAM.")
        self._precision_group.addButton(self._precision_bf16)
        precision_layout.addWidget(self._precision_bf16)

        self._precision_fp16 = QRadioButton("float16 (Experimental)")
        self._precision_fp16.setToolTip(
            "Experimental.\n"
            "May exhibit reduced numerical stability on long generations.\n"
            "Same VRAM usage as bfloat16.")
        self._precision_group.addButton(self._precision_fp16)
        precision_layout.addWidget(self._precision_fp16)

        self._precision_fp32 = QRadioButton("float32 (Maximum precision)")
        self._precision_fp32.setToolTip(
            "Maximum numerical precision.\n"
            "Requires approximately twice the VRAM.\n"
            "Expected to be slower.\n"
            "Mainly useful for debugging and research.")
        self._precision_group.addButton(self._precision_fp32)
        precision_layout.addWidget(self._precision_fp32)

        precision_note = QLabel("Changes take effect after reloading the model.")
        precision_note.setStyleSheet("color: %s;" % Palette.TEXT_SECONDARY)
        precision_layout.addWidget(precision_note)

        layout.addRow("", precision_group)

        info = QLabel("These settings affect memory usage and performance.\n"
                      "Keeping the model loaded speeds up repeated generations\n"
                      "but uses ~10 GB of VRAM continuously.")
        info.setStyleSheet("color: %s;" % Palette.TEXT_SECONDARY)
        info.setWordWrap(True)
        layout.addRow("", info)

        self._tabs.addTab(tab, "Performance")

    # ------------------------------------------------------------------
    # Developer tab
    # ------------------------------------------------------------------
    def _build_developer_tab(self) -> None:
        tab = QWidget()
        layout = QFormLayout(tab)

        # P3.16B AUDIT: Developer Mode was a DEAD checkbox — _toggle_developer_mode
        # calls editor.set_developer_mode() which is a no-op (pass). The code
        # itself documents "no functional effect". Removed entirely.
        # To see literal Higgs tokens, use Raw Mode in the editor.

        info = QLabel("No developer settings are currently active.\n\n"
                      "To see literal Higgs tokens, use Raw Mode in the editor.\n"
                      "Developer diagnostics may be added in a future version.")
        info.setStyleSheet("color: %s;" % Palette.TEXT_SECONDARY)
        info.setWordWrap(True)
        layout.addRow("", info)

        self._tabs.addTab(tab, "Developer")

    # ------------------------------------------------------------------
    # Appearance tab
    # ------------------------------------------------------------------
    def _build_appearance_tab(self) -> None:
        tab = QWidget()
        layout = QFormLayout(tab)

        self._app_theme = QComboBox()
        from ui.theme import THEMES
        self._app_theme.addItems(sorted(THEMES.keys()))
        layout.addRow("Application Theme:", self._app_theme)

        self._sidebar_width = QSpinBox()
        self._sidebar_width.setRange(150, 500)
        self._sidebar_width.setSuffix(" px")
        self._sidebar_width.setToolTip(
            "Width of the left sidebar panel.\n"
            "Applied on next application startup.")
        layout.addRow("Sidebar Width:", self._sidebar_width)

        self._control_width = QSpinBox()
        self._control_width.setRange(250, 600)
        self._control_width.setSuffix(" px")
        self._control_width.setToolTip(
            "Width of the right control panel.\n"
            "Applied on next application startup.")
        layout.addRow("Control Panel Width:", self._control_width)

        # P3.16B AUDIT: "Collapse sidebar on startup" was DEAD — saved but
        # never applied. The current sidebar architecture uses setFixedWidth
        # and has no collapse/expand mechanism. Removed to avoid dead control.

        self._tabs.addTab(tab, "Appearance")

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------
    def _load_values(self) -> None:
        """Load current settings into the dialog widgets."""
        s = self._settings

        # General
        self._check_model.setChecked(s.get("application", "check_for_model_on_startup", True))

        # Generation
        gen = s.get_generation_parameters()
        self._temperature.setValue(gen.temperature)
        self._top_p.setValue(gen.top_p)
        self._top_k.setValue(gen.top_k)
        self._max_tokens.setValue(gen.max_new_tokens)
        self._silence.setValue(gen.append_silence)
        self._auto_play.setChecked(gen.auto_play)
        self._normalize.setChecked(gen.normalize_output)

        # Paths
        self._output_dir.setText(s.get("audio", "output_directory", "outputs"))

        # Audio (P3.16B: _audio_auto_play removed — duplicate of generation.auto_play)
        self._volume.setValue(int(s.get("audio", "volume", 1.0) * 100))
        self._concat_silence.setValue(int(s.get("audio", "concatenate_silence_ms", 300)))
        self._concat_randomize.setChecked(s.get("audio", "concatenate_randomize", True))

        # Performance (P3.16B: _keep_model + _release_tensors removed — dead)
        precision = s.get("performance", "model_precision", "bfloat16")
        if precision == "float16":
            self._precision_fp16.setChecked(True)
        elif precision == "float32":
            self._precision_fp32.setChecked(True)
        else:
            self._precision_bf16.setChecked(True)

        # Developer (P3.16B: _dev_startup removed — dead)
        # Appearance (P3.16B: _sidebar_collapsed removed — dead)
        self._app_theme.setCurrentText(s.get("application", "theme", "dark"))
        self._sidebar_width.setValue(s.get("window", "sidebar_width", 280))
        self._control_width.setValue(s.get("window", "control_panel_width", 360))

    def _on_save(self) -> None:
        """Save all settings to disk."""
        s = self._settings

        # Application
        s.set("application", "theme", self._app_theme.currentText())
        s.set("application", "check_for_model_on_startup", self._check_model.isChecked())

        # Generation
        from engine.models import GenerationParameters
        params = GenerationParameters(
            temperature=float(self._temperature.value()),
            top_p=float(self._top_p.value()),
            top_k=int(self._top_k.value()),
            max_new_tokens=int(self._max_tokens.value()),
            append_silence=float(self._silence.value()),
            auto_play=self._auto_play.isChecked(),
            normalize_output=self._normalize.isChecked(),
        )
        s.set_generation_parameters(params)

        # Paths
        s.set("audio", "output_directory", self._output_dir.text() or "outputs")

        # Audio (P3.16B: audio.auto_play removed — duplicate)
        s.set("audio", "volume", self._volume.value() / 100.0)
        s.set("audio", "concatenate_silence_ms", self._concat_silence.value())
        s.set("audio", "concatenate_randomize", self._concat_randomize.isChecked())

        # Performance (P3.16B: keep_model + release_tensors removed — dead)
        if self._precision_fp16.isChecked():
            s.set("performance", "model_precision", "float16")
        elif self._precision_fp32.isChecked():
            s.set("performance", "model_precision", "float32")
        else:
            s.set("performance", "model_precision", "bfloat16")

        # Window (P3.16B: sidebar_collapsed removed — dead)
        s.set("window", "sidebar_width", self._sidebar_width.value())
        s.set("window", "control_panel_width", self._control_width.value())

        self.settings_changed.emit()
        self.accept()

    def _on_reset(self) -> None:
        """Reset all settings to defaults."""
        from PySide6.QtWidgets import QMessageBox
        from engine.models import GenerationParameters
        reply = QMessageBox.question(
            self, "Reset Settings",
            "Reset all settings to their default values?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._settings.set_section("application", {
                "theme": "dark", "developer_mode": False,
                "check_for_model_on_startup": True,
                "current_project": "Default",
                "playback_volume": 1.0})
            self._settings.set_section("generation",
                GenerationParameters().to_dict())
            self._settings.set_section("audio", {
                "output_directory": "outputs", "default_format": "wav",
                "auto_play": True, "volume": 1.0})
            self._settings.set_section("performance", {
                "keep_model_loaded": True,
                "release_tensors_after_generation": True,
                "model_precision": "bfloat16"})
            self._settings.set_section("window", {
                "width": 1400, "height": 900, "sidebar_width": 280,
                "control_panel_width": 360, "sidebar_collapsed": False,
                "control_panel_collapsed": False})
            self._load_values()
            self.settings_changed.emit()

    def _browse_output_dir(self) -> None:
        """Open a folder picker for the output directory."""
        from PySide6.QtWidgets import QFileDialog
        import os
        current = self._output_dir.text() or "outputs"
        app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        start_dir = os.path.join(app_root, current)
        chosen = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", start_dir)
        if chosen:
            # Store as relative path if inside app root
            try:
                rel = os.path.relpath(chosen, app_root)
                self._output_dir.setText(rel)
            except ValueError:
                self._output_dir.setText(chosen)
