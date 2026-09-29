"""
SpeechStudio Right Control Panel.

UI Specification, section 8:
    Scrollable. Contains every Higgs configuration.
    Sections: Voice, Emotion, Style, Prosody, Sound Effects, Generation,
              Reference Audio, Prompt Preview
"""

from __future__ import annotations
from typing import Optional, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea,
    QGroupBox, QPushButton, QLabel, QComboBox, QLineEdit, QTextEdit,
    QSpinBox, QDoubleSpinBox, QSlider, QCheckBox, QButtonGroup,
    QToolButton, QSizePolicy, QFrame,
)

from ui.theme import Palette
from ui.typography import Typography
from engine.higgs_tokens import (
    EMOTIONS, STYLES, PROSODY_TOKENS, SFX_TOKENS,
    SPEED_OPTIONS, PITCH_OPTIONS, DELIVERY_OPTIONS,
)
from engine.models import GenerationParameters, VoiceProfile


class EmotionButtons(QWidget):
    """21 toggle buttons for emotions. Exactly one selected.

    Selected buttons get a purple highlight matching the Friendly view.

    Styling note: we use a SINGLE stylesheet with the ``:checked``
    pseudo-state rather than swapping between two stylesheets. This is
    required because the global QSS (theme.py generate_qss) defines
    ``QToolButton:checked { ... }`` which is MORE SPECIFIC than a plain
    ``QToolButton { ... }`` rule. If we used a plain ``QToolButton``
    rule in the inline stylesheet, the global ``:checked`` rule would
    override it — making the selected button show the global
    bg_raised/text_primary colors instead of the purple highlight.

    By using ``QToolButton:checked`` in the inline stylesheet, we match
    the specificity of the global rule, and widget-level stylesheets
    take precedence over application-level stylesheets when specificity
    is equal (standard Qt behavior).
    """

    emotion_changed = Signal(str)  # emotion name

    # P3.19: Modernized to match FriendlyView's pill-button aesthetic.
    # Uses Colors tokens from design_tokens, rounded corners (12px),
    # and consistent purple highlight on :checked state.
    _STYLE = """
        QToolButton {
            background-color: rgba(28, 27, 27, 0.6);
            color: #A0A0A0;
            border: 1px solid #333333;
            border-radius: 12px;
            padding: 8px 10px;
            font-size: 12px;
        }
        QToolButton:hover {
            border-color: #d0bcff;
            color: #d0bcff;
            background-color: rgba(208, 188, 255, 0.08);
        }
        QToolButton:checked {
            background-color: rgba(208, 188, 255, 0.15);
            color: #d0bcff;
            border: 2px solid #d0bcff;
            border-radius: 12px;
            font-weight: 600;
        }
        QToolButton:checked:hover {
            background-color: rgba(208, 188, 255, 0.25);
        }
        QToolButton:pressed {
            background-color: rgba(208, 188, 255, 0.30);
        }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(False)  # Non-exclusive: allows toggle OFF
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        cols = 3
        for i, emo in enumerate(EMOTIONS):
            btn = QToolButton()
            btn.setText(emo.name)
            btn.setToolTip("{0}\n{1}\nToken: <|emotion:{2}|>".format(
                emo.name, emo.description, emo.tag))
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setMinimumHeight(32)
            # Set the style ONCE — the :checked pseudo-state handles
            # the visual transition automatically. No need to swap
            # stylesheets on every click.
            btn.setStyleSheet(self._STYLE)
            self._group.addButton(btn)
            self._buttons[emo.name] = btn
            layout.addWidget(btn, i // cols, i % cols)

        self._group.buttonClicked.connect(self._on_clicked)

    def _on_clicked(self, button) -> None:
        # Toggle behavior with non-exclusive group:
        # - If button was unchecked → now checked → select it, deselect others
        # - If button was checked → now unchecked → deselect (emit empty)
        for name, btn in self._buttons.items():
            if btn == button:
                if btn.isChecked():
                    # Deselect all others (manual exclusivity)
                    for other_name, other_btn in self._buttons.items():
                        if other_btn != btn and other_btn.isChecked():
                            other_btn.setChecked(False)
                    self.emotion_changed.emit(name)
                else:
                    self.emotion_changed.emit("")
                break
        # No need to call _update_button_styles() — the :checked
        # pseudo-state in the stylesheet handles the visual update
        # automatically when setChecked() is called.

    def get_selected(self) -> Optional[str]:
        for name, btn in self._buttons.items():
            if btn.isChecked():
                return name
        return None

    def set_selected(self, emotion_name: Optional[str]) -> None:
        """Programmatically set the checked emotion button.

        P3.39 (token-integrity audit): this setter is SIGNAL-SILENT.
        It updates the display ONLY — it must never emit
        ``emotion_changed``, because that signal is MainWindow's
        "the user picked an emotion" contract. The previous version
        emitted here, so every programmatic display update (scene
        load, block-selection display, preset apply, Friendly↔Advanced
        sync) masqueraded as a user click and re-entered
        MainWindow._on_emotion_changed — writing the global or a
        hidden Narration-Block OVERRIDE without any user action
        ("a token enters the chain without selection").
        Genuine user selections are emitted exclusively by
        :meth:`_on_clicked` (real button clicks).
        """
        if emotion_name is None or emotion_name == "":
            for btn in self._buttons.values():
                btn.setChecked(False)
            return
        btn = self._buttons.get(emotion_name)
        if btn:
            # Deselect all others
            for other_btn in self._buttons.values():
                if other_btn != btn:
                    other_btn.setChecked(False)
            btn.setChecked(True)


class StyleButtons(QWidget):
    """3 style buttons. Exactly one active (or none).

    Selected buttons get a purple highlight matching the Friendly view.

    Uses the same single-stylesheet-with-:checked approach as
    EmotionButtons to correctly override the global QSS
    ``QToolButton:checked`` rule.
    """

    style_changed = Signal(str)

    _STYLE = """
        QToolButton {
            background-color: rgba(28, 27, 27, 0.6);
            color: #A0A0A0;
            border: 1px solid #333333;
            border-radius: 12px;
            padding: 8px 10px;
            font-size: 12px;
        }
        QToolButton:hover {
            border-color: #d0bcff;
            color: #d0bcff;
            background-color: rgba(208, 188, 255, 0.08);
        }
        QToolButton:checked {
            background-color: rgba(208, 188, 255, 0.15);
            color: #d0bcff;
            border: 2px solid #d0bcff;
            border-radius: 12px;
            font-weight: 600;
        }
        QToolButton:checked:hover {
            background-color: rgba(208, 188, 255, 0.25);
        }
        QToolButton:pressed {
            background-color: rgba(208, 188, 255, 0.30);
        }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(False)  # Non-exclusive: allows toggle OFF
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        for st in STYLES:
            btn = QToolButton()
            btn.setText(st.name)
            btn.setToolTip("Token: <|style:{0}|>".format(st.tag))
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setMinimumHeight(32)
            btn.setStyleSheet(self._STYLE)
            self._group.addButton(btn)
            self._buttons[st.name] = btn
            layout.addWidget(btn)
        self._group.buttonClicked.connect(self._on_clicked)

    def _on_clicked(self, button) -> None:
        for name, btn in self._buttons.items():
            if btn == button:
                if btn.isChecked():
                    for other_btn in self._buttons.values():
                        if other_btn != btn and other_btn.isChecked():
                            other_btn.setChecked(False)
                    self.style_changed.emit(name)
                else:
                    self.style_changed.emit("")
                break
        # No _update_button_styles() — :checked pseudo-state handles it.

    def get_selected(self) -> Optional[str]:
        for name, btn in self._buttons.items():
            if btn.isChecked():
                return name
        return None

    def set_selected(self, style_name: Optional[str]) -> None:
        """Programmatically set the checked style button.

        P3.39 (token-integrity audit): SIGNAL-SILENT — see the matching
        note on :meth:`EmotionButtons.set_selected`. Emitting here made
        programmatic display updates masquerade as user style picks.
        """
        if style_name is None or style_name == "":
            for btn in self._buttons.values():
                btn.setChecked(False)
            return
        btn = self._buttons.get(style_name)
        if btn:
            for other_btn in self._buttons.values():
                if other_btn != btn:
                    other_btn.setChecked(False)
            btn.setChecked(True)


class ProsodySection(QWidget):
    """Speed, Pitch, Delivery selectors + pause insertion buttons."""

    speed_changed = Signal(str)
    pitch_changed = Signal(str)
    delivery_changed = Signal(str)
    pause_inserted = Signal(str)  # "pause" or "long_pause"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # P3.19: Modernized combobox + button styles matching FriendlyView.
        _combo_style = """
            QComboBox {
                background-color: rgba(28, 27, 27, 0.8);
                color: #F5F5F5;
                border: 1px solid #333333;
                border-radius: 8px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QComboBox:hover {
                border-color: #d0bcff;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #A0A0A0;
                margin-right: 6px;
            }
            QComboBox QAbstractItemView {
                background-color: #1C1B1B;
                color: #F5F5F5;
                border: 1px solid #333333;
                border-radius: 8px;
                selection-background-color: rgba(208, 188, 255, 0.15);
                selection-color: #d0bcff;
                padding: 4px;
                outline: none;
            }
        """
        _label_style = "color: #A0A0A0; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;"
        _btn_style = """
            QPushButton {
                background-color: rgba(28, 27, 27, 0.6);
                color: #A0A0A0;
                border: 1px solid #333333;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                border-color: #d0bcff;
                color: #d0bcff;
                background-color: rgba(208, 188, 255, 0.08);
            }
            QPushButton:pressed {
                background-color: rgba(208, 188, 255, 0.15);
            }
        """

        # Speed
        speed_label = QLabel("Speed:")
        speed_label.setStyleSheet(_label_style)
        layout.addWidget(speed_label)
        self._speed = QComboBox()
        self._speed.setStyleSheet(_combo_style)
        for opt in SPEED_OPTIONS:
            self._speed.addItem("{0} ({1})".format(opt.name, opt.effect), opt.name)
        for i in range(self._speed.count()):
            if self._speed.itemData(i) == "Normal":
                self._speed.setCurrentIndex(i)
                break
        self._speed.currentIndexChanged.connect(
            lambda: self.speed_changed.emit(self._speed.currentData()))
        layout.addWidget(self._speed)

        # Pitch
        pitch_label = QLabel("Pitch:")
        pitch_label.setStyleSheet(_label_style)
        layout.addWidget(pitch_label)
        self._pitch = QComboBox()
        self._pitch.setStyleSheet(_combo_style)
        for opt in PITCH_OPTIONS:
            self._pitch.addItem(opt.name, opt.name)
        for i in range(self._pitch.count()):
            if self._pitch.itemData(i) == "Normal":
                self._pitch.setCurrentIndex(i)
                break
        self._pitch.currentIndexChanged.connect(
            lambda: self.pitch_changed.emit(self._pitch.currentData()))
        layout.addWidget(self._pitch)

        # Delivery
        delivery_label = QLabel("Delivery:")
        delivery_label.setStyleSheet(_label_style)
        layout.addWidget(delivery_label)
        self._delivery = QComboBox()
        self._delivery.setStyleSheet(_combo_style)
        for opt in DELIVERY_OPTIONS:
            self._delivery.addItem(opt.name, opt.name)
        for i in range(self._delivery.count()):
            if self._delivery.itemData(i) == "Normal":
                self._delivery.setCurrentIndex(i)
                break
        self._delivery.currentIndexChanged.connect(
            lambda: self.delivery_changed.emit(self._delivery.currentData()))
        layout.addWidget(self._delivery)

        # Pause buttons
        pause_row = QHBoxLayout()
        pause_row.setSpacing(6)
        pause_btn = QPushButton("Insert Pause")
        pause_btn.setStyleSheet(_btn_style)
        pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pause_btn.setToolTip(
            "Insert a short pause at cursor position.\n\n"
            "Generates a <|prosody:pause|> token inline.\n"
            "Expected duration: ~400-700 ms.\n\n"
            "Tip: the actual pause length is model-dependent and varies\n"
            "with the context. For a more noticeable difference between\n"
            "Pause and Long Pause, use a lower temperature (0.3-0.7).")
        pause_btn.clicked.connect(lambda: self.pause_inserted.emit("pause"))
        pause_row.addWidget(pause_btn)

        long_pause_btn = QPushButton("Insert Long Pause")
        long_pause_btn.setStyleSheet(_btn_style)
        long_pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        long_pause_btn.setToolTip(
            "Insert a long pause at cursor position.\n\n"
            "Generates a <|prosody:long_pause|> token inline.\n"
            "Expected duration: ~700-1500 ms (roughly 2x the short pause).\n\n"
            "Tip: the actual pause length is model-dependent and varies\n"
            "with the context. For a more noticeable difference between\n"
            "Pause and Long Pause, use a lower temperature (0.3-0.7).\n\n"
            "For even longer pauses, insert multiple long_pause tokens\n"
            "or use the 'Append Silence' setting in Generation parameters.")
        long_pause_btn.clicked.connect(lambda: self.pause_inserted.emit("long_pause"))
        pause_row.addWidget(long_pause_btn)
        layout.addLayout(pause_row)

    def get_speed(self) -> str:
        return self._speed.currentData()
    def get_pitch(self) -> str:
        return self._pitch.currentData()
    def get_delivery(self) -> str:
        return self._delivery.currentData()

    # ------------------------------------------------------------------
    # Setters (used by MainWindow to apply presets / inheritance modes
    # without re-emitting user signals).
    # ------------------------------------------------------------------
    def set_speed(self, value: str) -> None:
        """Set the speed combo to ``value`` without firing signals."""
        for i in range(self._speed.count()):
            if self._speed.itemData(i) == value:
                self._speed.blockSignals(True)
                self._speed.setCurrentIndex(i)
                self._speed.blockSignals(False)
                return

    def set_pitch(self, value: str) -> None:
        for i in range(self._pitch.count()):
            if self._pitch.itemData(i) == value:
                self._pitch.blockSignals(True)
                self._pitch.setCurrentIndex(i)
                self._pitch.blockSignals(False)
                return

    def set_delivery(self, value: str) -> None:
        for i in range(self._delivery.count()):
            if self._delivery.itemData(i) == value:
                self._delivery.blockSignals(True)
                self._delivery.setCurrentIndex(i)
                self._delivery.blockSignals(False)
                return


class SfxSection(QWidget):
    """Sound effect buttons. Inserts token + onomatopoeia at cursor."""

    sfx_inserted = Signal(str, str)  # sfx_name, onomatopoeia

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        cols = 3
        for i, sfx in enumerate(SFX_TOKENS):
            btn = QToolButton()
            btn.setText(sfx.name)
            btn.setToolTip("{0}\nSuggested: {1}\nToken: <|sfx:{2}|>".format(
                sfx.name, sfx.onomatopoeia, sfx.tag))
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setMinimumHeight(32)
            btn.clicked.connect(
                lambda checked, s=sfx: self.sfx_inserted.emit(s.name, s.onomatopoeia))
            layout.addWidget(btn, i // cols, i % cols)


class GenerationSection(QWidget):
    """Generation parameters: temperature, top_p, top_k, max_new_tokens, etc."""

    parameters_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # P3.25 (audit SS-H06): the Allow SFX flag is application-level
        # SFX policy carried on GenerationParameters. The Advanced panel's
        # GenerationSection previously DROPPED the flag (get_parameters()
        # rebuilt the dataclass without allow_sfx → always the default
        # True), so the Friendly tab's toggle was a no-op end-to-end and
        # SFX tokens were always emitted. The authoritative in-panel value
        # now lives here and round-trips through get/set_parameters.
        self._allow_sfx: bool = True
        self._build_ui()
        self._set_defaults(GenerationParameters())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Temperature
        temp_label = QLabel("Temperature:")
        temp_label.setToolTip("Controls generation randomness.\nLower = predictable. Higher = expressive.\nRecommended: 1.2 to 1.4")
        layout.addWidget(temp_label)
        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.1, 2.0)
        self._temperature.setSingleStep(0.05)
        self._temperature.setDecimals(2)
        self._temperature.setStepType(QDoubleSpinBox.StepType.DefaultStepType)
        self._temperature.setCorrectionMode(QDoubleSpinBox.CorrectionMode.CorrectToPreviousValue)
        self._temperature.setToolTip("Controls generation randomness.\nLower = predictable. Higher = expressive.\nRecommended: 1.2 to 1.4")
        self._temperature.valueChanged.connect(lambda v: self.parameters_changed.emit())
        layout.addWidget(self._temperature)

        # Top P
        top_p_label = QLabel("Top P:")
        top_p_label.setToolTip("Controls nucleus sampling.\nLower = less variation. Higher = more diverse.\nRecommended: 0.95")
        layout.addWidget(top_p_label)
        self._top_p = QDoubleSpinBox()
        self._top_p.setRange(0.1, 1.0)
        self._top_p.setSingleStep(0.05)
        self._top_p.setDecimals(2)
        self._top_p.setStepType(QDoubleSpinBox.StepType.DefaultStepType)
        self._top_p.setCorrectionMode(QDoubleSpinBox.CorrectionMode.CorrectToPreviousValue)
        self._top_p.setToolTip("Controls nucleus sampling.\nLower = less variation. Higher = more diverse.\nRecommended: 0.95")
        self._top_p.valueChanged.connect(lambda v: self.parameters_changed.emit())
        layout.addWidget(self._top_p)

        # Top K
        top_k_label = QLabel("Top K:")
        top_k_label.setToolTip("Limits candidate tokens.\nHigher = more variation. Lower = more deterministic.\nRecommended: 300")
        layout.addWidget(top_k_label)
        self._top_k = QSpinBox()
        self._top_k.setRange(1, 500)
        self._top_k.setSingleStep(10)
        self._top_k.setStepType(QSpinBox.StepType.DefaultStepType)
        self._top_k.setCorrectionMode(QSpinBox.CorrectionMode.CorrectToPreviousValue)
        self._top_k.setToolTip("Limits candidate tokens.\nHigher = more variation. Lower = more deterministic.\nRecommended: 300")
        self._top_k.valueChanged.connect(lambda v: self.parameters_changed.emit())
        layout.addWidget(self._top_k)

        # Max New Tokens
        max_tokens_label = QLabel("Max New Tokens:")
        max_tokens_label.setToolTip("Max tokens generated.\nHigher = longer speech.\n2048 ≈ 7.5s, 4096 ≈ 15s, 8192 ≈ 30s\nRecommended: 4096")
        layout.addWidget(max_tokens_label)
        self._max_tokens = QSpinBox()
        self._max_tokens.setRange(128, 8192)
        self._max_tokens.setSingleStep(256)
        self._max_tokens.setStepType(QSpinBox.StepType.DefaultStepType)
        self._max_tokens.setCorrectionMode(QSpinBox.CorrectionMode.CorrectToPreviousValue)
        self._max_tokens.setToolTip("Max tokens generated.\nHigher = longer speech.\nAudio is generated at 25 frames/sec, so the\nsingle-output ceiling is max_new_tokens / 25:\n2048 ≈ 82s, 4096 ≈ 164s, 8192 ≈ 328s\nRecommended: 4096 (P3.45.2A)")
        self._max_tokens.valueChanged.connect(lambda v: self.parameters_changed.emit())
        layout.addWidget(self._max_tokens)

        # Seed
        seed_label = QLabel("Seed:")
        seed_label.setToolTip(
            "Random generation seed.\n"
            "Leave empty for random.\n\n"
            "NOTE: the Higgs Audio V3 model does NOT accept a 'seed'\n"
            "parameter directly. GUINEO sets torch.manual_seed()\n"
            "before each generation, which makes the SAMPLING reproducible\n"
            "(same token choices) when a voice profile is selected.\n\n"
            "IMPORTANT: if NO voice profile is selected, the model uses a\n"
            "different default voice each time — the seed controls the\n"
            "sampling but NOT the voice character. To get consistent voices,\n"
            "ALWAYS select a voice profile (import one or create from a\n"
            "reference audio clip).")
        layout.addWidget(seed_label)
        self._seed = QLineEdit()
        self._seed.setPlaceholderText("Random")
        self._seed.setToolTip(
            "Random generation seed.\n"
            "Leave empty for random.\n\n"
            "NOTE: the Higgs Audio V3 model does NOT accept a 'seed'\n"
            "parameter directly. GUINEO sets torch.manual_seed()\n"
            "before each generation, which makes the SAMPLING reproducible\n"
            "(same token choices) when a voice profile is selected.\n\n"
            "IMPORTANT: if NO voice profile is selected, the model uses a\n"
            "different default voice each time — the seed controls the\n"
            "sampling but NOT the voice character. To get consistent voices,\n"
            "ALWAYS select a voice profile (import one or create from a\n"
            "reference audio clip).")
        self._seed.textChanged.connect(lambda: self.parameters_changed.emit())
        layout.addWidget(self._seed)

        # Append Silence
        silence_label = QLabel("Append Silence:")
        silence_label.setToolTip("Adds silence to end of WAV.\nUseful for playback compatibility.\nRecommended: 0.5 seconds")
        layout.addWidget(silence_label)
        self._silence = QDoubleSpinBox()
        self._silence.setRange(0.0, 5.0)
        self._silence.setSingleStep(0.1)
        self._silence.setDecimals(1)
        self._silence.setCorrectionMode(QDoubleSpinBox.CorrectionMode.CorrectToPreviousValue)
        self._silence.setToolTip("Adds silence to end of WAV.\nUseful for playback compatibility.\nRecommended: 0.5 seconds")
        self._silence.valueChanged.connect(lambda v: self.parameters_changed.emit())
        layout.addWidget(self._silence)

        # Auto Play
        self._auto_play = QCheckBox("Auto Play")
        self._auto_play.setToolTip("Automatically plays audio after completion.")
        self._auto_play.stateChanged.connect(lambda v: self.parameters_changed.emit())
        layout.addWidget(self._auto_play)

        # Normalize
        self._normalize = QCheckBox("Normalize Output")
        self._normalize.setToolTip("Adjusts loudness to -18 LUFS.\nDoes not change spoken content.\nFor final exports only.")
        self._normalize.stateChanged.connect(lambda v: self.parameters_changed.emit())
        layout.addWidget(self._normalize)

    def _set_defaults(self, params: GenerationParameters) -> None:
        self._temperature.setValue(params.temperature)
        self._top_p.setValue(params.top_p)
        self._top_k.setValue(params.top_k)
        self._max_tokens.setValue(params.max_new_tokens)
        self._silence.setValue(params.append_silence)
        self._auto_play.setChecked(params.auto_play)
        self._normalize.setChecked(params.normalize_output)
        # P3.25 (audit SS-M03): a seed of None must CLEAR the field — the
        # old truthy guard left the previous seed in the spinbox, so a
        # "no seed" state silently reused the earlier seed.
        if params.seed is not None:
            self._seed.setText(str(params.seed))
        else:
            self._seed.clear()
        # P3.25 (audit SS-H06): keep the panel's allow_sfx in sync with the
        # parameters object (set_parameters / preset / scene restore paths).
        self._allow_sfx = bool(params.allow_sfx)

    def get_parameters(self) -> GenerationParameters:
        seed_text = self._seed.text().strip()
        try:
            seed = int(seed_text) if seed_text else None
        except (ValueError, TypeError):
            seed = None  # Invalid seed text → random (None)
        return GenerationParameters(
            temperature=float(self._temperature.value()),
            top_p=float(self._top_p.value()),
            top_k=int(self._top_k.value()),
            max_new_tokens=int(self._max_tokens.value()),
            seed=seed,
            append_silence=float(self._silence.value()),
            auto_play=self._auto_play.isChecked(),
            normalize_output=self._normalize.isChecked(),
            allow_sfx=self._allow_sfx,
        )

    def set_parameters(self, params: GenerationParameters) -> None:
        self._set_defaults(params)


class VoiceSection(QWidget):
    """Voice profile selector and info display."""

    voice_changed = Signal(str)  # voice_id
    import_voice_requested = Signal()
    create_voice_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # P3.19: Modernized voice selector with styled combobox + buttons
        # matching the FriendlyView aesthetic.
        voice_label = QLabel("Voice Profile:")
        voice_label.setStyleSheet("color: #A0A0A0; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;")
        layout.addWidget(voice_label)

        self._voice_combo = QComboBox()
        self._voice_combo.setStyleSheet("""
            QComboBox {
                background-color: rgba(28, 27, 27, 0.8);
                color: #F5F5F5;
                border: 1px solid #333333;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 13px;
            }
            QComboBox:hover {
                border-color: #d0bcff;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #A0A0A0;
                margin-right: 8px;
            }
            QComboBox QAbstractItemView {
                background-color: #1C1B1B;
                color: #F5F5F5;
                border: 1px solid #333333;
                border-radius: 8px;
                selection-background-color: rgba(208, 188, 255, 0.15);
                selection-color: #d0bcff;
                padding: 4px;
                outline: none;
            }
        """)
        self._voice_combo.currentIndexChanged.connect(
            lambda: self.voice_changed.emit(self._voice_combo.currentData()))
        layout.addWidget(self._voice_combo)

        # Buttons — pill-shaped, matching FriendlyView button style
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        _btn_style = """
            QPushButton {
                background-color: rgba(28, 27, 27, 0.6);
                color: #A0A0A0;
                border: 1px solid #333333;
                border-radius: 8px;
                padding: 8px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                border-color: #d0bcff;
                color: #d0bcff;
                background-color: rgba(208, 188, 255, 0.08);
            }
            QPushButton:pressed {
                background-color: rgba(208, 188, 255, 0.15);
            }
        """

        import_btn = QPushButton("Import")
        import_btn.setToolTip("Import a reference WAV as a new voice")
        import_btn.setStyleSheet(_btn_style)
        import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        import_btn.clicked.connect(self.import_voice_requested.emit)
        btn_row.addWidget(import_btn)

        create_btn = QPushButton("Create")
        create_btn.setToolTip("Create a new empty voice profile")
        create_btn.setStyleSheet(_btn_style)
        create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_btn.clicked.connect(self.create_voice_requested.emit)
        btn_row.addWidget(create_btn)
        layout.addLayout(btn_row)

        # Voice info
        self._info_label = QLabel("No voice selected")
        self._info_label.setProperty("role", "hint")
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet("color: #555555; font-size: 11px; padding: 4px 2px;")
        layout.addWidget(self._info_label)

    def populate_voices(self, voices: List[VoiceProfile]) -> None:
        """Rebuild the voice dropdown while preserving the current selection."""
        current_id = self._voice_combo.currentData()

        self._voice_combo.blockSignals(True)
        self._voice_combo.clear()
        self._voice_combo.addItem("(No voice)", None)
        for v in voices:
            label = v.name
            if v.duration > 0:
                label += " ({0:.1f}s)".format(v.duration)
            self._voice_combo.addItem(label, v.id)

        restored = False
        if current_id is not None:
            for i in range(self._voice_combo.count()):
                if self._voice_combo.itemData(i) == current_id:
                    self._voice_combo.setCurrentIndex(i)
                    restored = True
                    break
        if not restored:
            self._voice_combo.setCurrentIndex(0)
        self._voice_combo.blockSignals(False)

    def get_selected_voice_id(self) -> Optional[str]:
        return self._voice_combo.currentData()

    def set_selected_voice_id(self, voice_id: Optional[str]) -> None:
        """Programmatically select a voice by id without firing signals."""
        self._voice_combo.blockSignals(True)
        if voice_id is None:
            self._voice_combo.setCurrentIndex(0)
        else:
            target_idx = 0
            for i in range(self._voice_combo.count()):
                if self._voice_combo.itemData(i) == voice_id:
                    target_idx = i
                    break
            self._voice_combo.setCurrentIndex(target_idx)
        self._voice_combo.blockSignals(False)

    def set_voice_info(self, voice: Optional[VoiceProfile]) -> None:
        if voice is None:
            self._info_label.setText("No voice selected")
            return
        info = "Name: {0}\nSample Rate: {1} Hz\nChannels: {2}\nDuration: {3:.1f}s".format(
            voice.name, voice.sample_rate, voice.channels, voice.duration)
        if voice.reference_transcript:
            info += "\nTranscript: {0}".format(voice.reference_transcript[:80])
        self._info_label.setText(info)


class PromptPreviewSection(QWidget):
    """Read-only preview of the final prompt with tokens."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header_row = QHBoxLayout()
        header_label = QLabel("Final Prompt:")
        header_label.setFont(Typography.body_md())
        header_row.addWidget(header_label)
        copy_btn = QPushButton("Copy")
        copy_btn.setToolTip("Copy the final prompt to clipboard")
        copy_btn.setFont(Typography.body_md())
        copy_btn.clicked.connect(self._copy_prompt)
        header_row.addWidget(copy_btn)
        layout.addLayout(header_row)

        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        # Issue #33: use the central Typography token (JetBrains Mono 12px)
        # instead of a hardcoded QFont("Consolas", 10). Consistent with the
        # mono_data token used across all panels.
        self._preview.setFont(Typography.mono_data())
        self._preview.setPlaceholderText("Prompt preview will appear here...")
        self._preview.setMinimumHeight(100)
        layout.addWidget(self._preview)

        self._warnings_label = QLabel("")
        self._warnings_label.setFont(Typography.metadata_sm())
        self._warnings_label.setStyleSheet("color: %s;" % Palette.WARNING)
        self._warnings_label.setWordWrap(True)
        layout.addWidget(self._warnings_label)

    def _copy_prompt(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._preview.toPlainText())

    def update_prompt(self, prompt: str, warnings: list) -> None:
        self._preview.setPlainText(prompt)
        if warnings:
            self._warnings_label.setText("Warnings: " + "; ".join(warnings))
        else:
            self._warnings_label.setText("")


class ControlPanel(QScrollArea):
    """Right control panel containing all Higgs configuration sections.

    UI Specification section 8: scrollable, contains Voice, Emotion, Style,
    Prosody, Sound Effects, Generation, Reference Audio, Prompt Preview.
    """

    # Signals forwarded to MainWindow
    emotion_changed = Signal(str)
    style_changed = Signal(str)
    speed_changed = Signal(str)
    pitch_changed = Signal(str)
    delivery_changed = Signal(str)
    sfx_inserted = Signal(str, str)
    pause_inserted = Signal(str)
    parameters_changed = Signal()
    voice_changed = Signal(str)
    import_voice_requested = Signal()
    create_voice_requested = Signal()
    transcript_changed = Signal(str)          # reference transcript edited
    voice_library_requested = Signal()       # open the voice library dialog
    generate_requested = Signal()            # Phase 0: missing signal — MainWindow expects this at line 334

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Prevent horizontal scroll: the container should never be wider
        # than the viewport. setWidgetResizable(True) + this policy usually
        # suffices, but some child widgets (e.g. EmotionButtons grid) have
        # a minimumSizeHint that forces the container wider. Setting the
        # container's minimumWidth to 0 and sizePolicy to Ignored on the
        # horizontal axis ensures it always fits the viewport width.
        self.setObjectName("AdvancedControlPanel")
        # Wider scrollbar (12px) with a visible rounded thumb — the global
        # theme scrollbar is only 8px which is too thin to grab comfortably.
        # This panel-specific override gives the Advanced view a usable
        # scrollbar without affecting other widgets.
        self.setStyleSheet(
            "QScrollArea#AdvancedControlPanel {{"
            "  background-color: {bg};"
            "  border: none;"
            "}}"
            "QScrollBar:vertical {{"
            "  background: {bg_base};"
            "  width: 12px;"
            "  border: none;"
            "  margin: 2px;"
            "}}"
            "QScrollBar::handle:vertical {{"
            "  background: {thumb};"
            "  border-radius: 5px;"
            "  min-height: 36px;"
            "}}"
            "QScrollBar::handle:vertical:hover {{"
            "  background: {thumb_hover};"
            "}}"
            "QScrollBar::handle:vertical:pressed {{"
            "  background: {primary};"
            "}}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{"
            "  height: 0; width: 0;"
            "}}"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{"
            "  background: transparent;"
            "}}"
            .format(
                bg=Palette.BG_SURFACE,
                bg_base=Palette.BG_BASE,
                thumb=Palette.BG_SURFACE_ALT,
                thumb_hover="#4a4a4a",
                primary="#d0bcff",
            )
        )
        self._batch_update_depth = 0
        self._build_ui()

    def set_generate_enabled(self, enabled: bool) -> None:
        """Enable/disable the Generate button.

        Phase 0: This method is referenced by MainWindow in 4+ places
        but was missing from ControlPanel. The actual Generate button
        lives in TopNavigation, so this is a no-op stub that prevents
        AttributeError. MainWindow also calls set_generate_enabled on
        _toolbar and _top_nav which have real implementations.
        """
        pass

    def _build_ui(self) -> None:
        container = QWidget()
        container.setObjectName("ControlPanelContainer")
        container.setMinimumWidth(0)  # prevent horizontal scroll
        # Give the container an opaque background matching the RightPanel's
        # bg_surface + bottom border-radius matching the parent's 12px
        # rounded corners. Without this, the QScrollArea viewport is
        # transparent and the sections appear to "float" on the ambient
        # background bleeding through the panel's rounded corners.
        container.setStyleSheet(
            "QWidget#ControlPanelContainer {{"
            "  background-color: {bg};"
            "  border-bottom-left-radius: 12px;"
            "  border-bottom-right-radius: 12px;"
            "}}".format(bg=Palette.BG_SURFACE)
        )
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # --- Inheritance banner (top of panel) ---
        self._inheritance_banner = QLabel("")
        self._inheritance_banner.setProperty("role", "hint")
        self._inheritance_banner.setWordWrap(True)
        self._inheritance_banner.setVisible(False)
        self._inheritance_banner.setStyleSheet(
            "QLabel {{ background-color: {0}; color: {1}; "
            "border: 1px solid {2}; border-radius: 4px; "
            "padding: 6px 8px; }}".format(
                Palette.BG_SURFACE, Palette.TEXT_PRIMARY, Palette.BORDER))
        layout.addWidget(self._inheritance_banner)

        # ---- Modern section builders ----
        # Each section uses a modern header (Typography.label_caps) + content
        # widget, replacing the legacy QGroupBox. The functional widgets
        # (VoiceSection, EmotionButtons, etc.) are preserved unchanged —
        # only the visual wrapper changes.

        # Voice section
        self._voice_section = VoiceSection()
        self._voice_section.voice_changed.connect(self.voice_changed.emit)
        self._voice_section.import_voice_requested.connect(self.import_voice_requested.emit)
        self._voice_section.create_voice_requested.connect(self.create_voice_requested.emit)
        layout.addWidget(self._make_section("VOICE", self._voice_section))

        # Emotion section
        self._emotion_buttons = EmotionButtons()
        self._emotion_buttons.emotion_changed.connect(self.emotion_changed.emit)
        layout.addWidget(self._make_section("EMOTION", self._emotion_buttons))

        # Style section
        self._style_buttons = StyleButtons()
        self._style_buttons.style_changed.connect(self.style_changed.emit)
        layout.addWidget(self._make_section("STYLE", self._style_buttons))

        # Prosody section
        self._prosody = ProsodySection()
        self._prosody.speed_changed.connect(self.speed_changed.emit)
        self._prosody.pitch_changed.connect(self.pitch_changed.emit)
        self._prosody.delivery_changed.connect(self.delivery_changed.emit)
        self._prosody.pause_inserted.connect(self.pause_inserted.emit)
        layout.addWidget(self._make_section("PROSODY", self._prosody))

        # Sound Effects section
        self._sfx = SfxSection()
        self._sfx.sfx_inserted.connect(self.sfx_inserted.emit)
        layout.addWidget(self._make_section("SOUND EFFECTS", self._sfx))

        # Generation section
        self._generation = GenerationSection()
        self._generation.parameters_changed.connect(self.parameters_changed.emit)
        layout.addWidget(self._make_section("GENERATION", self._generation))

        # Reference Audio section
        from ui.panels.reference_audio import ReferenceAudioPanel
        self._reference_audio = ReferenceAudioPanel()
        self._reference_audio.transcript_changed.connect(self._on_ref_transcript_changed)
        layout.addWidget(self._make_section("REFERENCE AUDIO", self._reference_audio))

        # Prompt Preview section
        self._preview = PromptPreviewSection()
        layout.addWidget(self._make_section("PROMPT PREVIEW", self._preview))

        layout.addStretch()
        self.setWidget(container)

    def _make_section(self, title: str, content: QWidget) -> QWidget:
        """Build a modern section with a label_caps header + content widget.

        Replaces the legacy QGroupBox with a cleaner visual hierarchy:
        - Section header: Typography.label_caps() (uppercase, secondary text)
        - Content: the functional widget, on a modern surface card

        This matches the Friendly view's visual language (section labels
        like "SELECTED SPEAKER", "EMOTION & STYLE", "PROSODY").
        """
        section = QWidget()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        header = QLabel(title)
        header.setFont(Typography.label_caps())
        header.setStyleSheet(
            f"color: {Palette.TEXT_SECONDARY}; background: transparent;"
        )
        section_layout.addWidget(header)

        # Wrap content in a subtle surface card for visual grouping
        card = QFrame()
        card.setObjectName("SectionCard")
        card.setStyleSheet(
            "QFrame#SectionCard {{"
            "  background-color: {bg};"
            "  border: 1px solid {border};"
            "  border-radius: 8px;"
            "}}".format(bg=Palette.BG_SURFACE_ALT, border=Palette.BORDER)
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)
        card_layout.addWidget(content)
        section_layout.addWidget(card)

        return section

    # ------------------------------------------------------------------
    # Access methods for MainWindow
    # ------------------------------------------------------------------
    def get_emotion(self) -> Optional[str]:
        return self._emotion_buttons.get_selected()

    def get_style(self) -> Optional[str]:
        return self._style_buttons.get_selected()

    def get_speed(self) -> str:
        return self._prosody.get_speed()

    def get_pitch(self) -> str:
        return self._prosody.get_pitch()

    def get_delivery(self) -> str:
        return self._prosody.get_delivery()

    def get_parameters(self) -> GenerationParameters:
        return self._generation.get_parameters()

    def set_parameters(self, params: GenerationParameters) -> None:
        self._generation.set_parameters(params)

    def update_prompt_preview(self, prompt: str, warnings: list) -> None:
        self._preview.update_prompt(prompt, warnings)

    def populate_voices(self, voices: list) -> None:
        self._voice_section.populate_voices(voices)

    def set_voice_info(self, voice) -> None:
        self._voice_section.set_voice_info(voice)

    def get_selected_voice_id(self) -> Optional[str]:
        return self._voice_section.get_selected_voice_id()

    # ------------------------------------------------------------------
    # Setters (used by preset apply, inheritance modes, etc.)
    # ------------------------------------------------------------------
    def set_emotion(self, emotion: Optional[str]) -> None:
        """Set the selected emotion (None = deselect all)."""
        self._emotion_buttons.set_selected(emotion)

    def set_style(self, style: Optional[str]) -> None:
        """Set the selected style (None = deselect all)."""
        self._style_buttons.set_selected(style)

    def set_speed(self, value: str) -> None:
        self._prosody.set_speed(value)

    def set_pitch(self, value: str) -> None:
        self._prosody.set_pitch(value)

    def set_delivery(self, value: str) -> None:
        self._prosody.set_delivery(value)

    def set_selected_voice_id(self, voice_id: Optional[str]) -> None:
        """Set the voice dropdown selection without firing signals."""
        self._voice_section.set_selected_voice_id(voice_id)

    # ------------------------------------------------------------------
    # Inheritance banner
    # ------------------------------------------------------------------
    def set_inheritance_mode(self, mode: str, source: str = "") -> None:
        """Show or hide the inheritance banner at the top of the panel.

        Args:
            mode: one of "", "project", "block", "preset".
            source: human-readable description of the inheritance source
                    (e.g. "Project Defaults", "Block 2").
        """
        if not mode:
            self._inheritance_banner.setVisible(False)
            self._inheritance_banner.setText("")
            return
        text = "Inheriting from: {0}".format(source or mode)
        self._inheritance_banner.setText(text)
        self._inheritance_banner.setVisible(True)

    # ------------------------------------------------------------------
    # Batch update context manager
    # ------------------------------------------------------------------
    def batch_update(self):
        """Return a context manager that suppresses rebuilds while many
        properties are changed at once.

        Example::

            with control_panel.batch_update():
                control_panel.set_emotion("Calm")
                control_panel.set_style("Narration")
                control_panel.set_speed("Slow")
        """
        return _BatchUpdateContext(self)

    def _begin_batch_update(self) -> None:
        self._batch_update_depth += 1

    def _end_batch_update(self) -> None:
        if self._batch_update_depth > 0:
            self._batch_update_depth -= 1
        if self._batch_update_depth == 0:
            # Trigger a single refresh of the prompt preview now that the
            # batch is complete.
            self.parameters_changed.emit()

    # ------------------------------------------------------------------
    # Reference audio
    # ------------------------------------------------------------------
    def update_reference_audio(self, voice) -> None:
        """Update the reference audio panel with a voice profile."""
        self._reference_audio.update_voice(voice)

    def set_reference_validation(self, warnings: list) -> None:
        self._reference_audio.set_validation_warnings(warnings)

    def _on_ref_transcript_changed(self, transcript: str) -> None:
        """Called when the user edits the reference transcript."""
        # Forward to MainWindow via a signal
        self.transcript_changed.emit(transcript)


# ---------------------------------------------------------------------------
# Batch update context manager
# ---------------------------------------------------------------------------
class _BatchUpdateContext:
    """Context manager returned by :meth:`ControlPanel.batch_update`.

    Wraps a pair of ``_begin_batch_update`` / ``_end_batch_update`` calls
    so multiple setter calls can be batched into a single refresh.
    """

    def __init__(self, panel: "ControlPanel"):
        self._panel = panel

    def __enter__(self) -> "ControlPanel":
        self._panel._begin_batch_update()
        return self._panel

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self._panel._end_batch_update()
        return False  # do not swallow exceptions
