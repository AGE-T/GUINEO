"""
SpeechStudio — Right Panel (Friendly + Advanced wrapper).

ARCHITECTURE:
    RightPanel (owns the surface: background, border, rounded corners)
      └─ QVBoxLayout
           ├─ TabBar (transparent bg, parent's surface shows through)
           └─ QStackedWidget
                ├─ Tab 0: QScrollArea → FriendlyView (transparent bg)
                └─ Tab 1: QScrollArea → AdvancedView (transparent bg)

KEY PRINCIPLES:
    1. ONE surface: RightPanel paints bg + border + border-radius.
       Children are transparent — they do NOT paint their own backgrounds.
    2. ONE scroll per tab: each tab has exactly one QScrollArea.
       No nested QScrollArea (the old ControlPanel was a QScrollArea
       inside the RightPanel's QStackedWidget — that's forbidden now).
    3. NO horizontal scroll: content always fits viewport width.
       All content widgets have minimumWidth=0 and use Expanding size policy.
    4. Corners: the parent's border-radius is never painted over because
       children don't paint opaque rectangles.
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QStackedWidget,
    QSizePolicy, QScrollArea, QLabel, QFrame, QButtonGroup,
)

from ui.panels.control_panel import (
    ControlPanel, VoiceSection, EmotionButtons, StyleButtons,
    ProsodySection, SfxSection, GenerationSection, PromptPreviewSection,
)
from ui.design_tokens import Colors, Spacing, Radii, Typography
from ui.typography import IconSize, IconColor
from engine.higgs_tokens import STYLES
from ui.theme import Palette


class RightPanel(QWidget):
    """Right Control Panel with Friendly + Advanced tabs.

    The RightPanel owns the visual surface (background, border, rounded
    corners). All child widgets are transparent — they don't paint their
    own backgrounds, so the parent's rounded corners are never painted over.
    """

    # Forward ALL signals from the embedded ControlPanel.
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
    transcript_changed = Signal(str)
    voice_library_requested = Signal()
    generate_requested = Signal()
    # Source indicator signal: emitted when the effective source changes.
    # Carries (property_name, source_label) e.g. ("emotion", "Inline Token")
    source_changed = Signal(str, str)
    # Block override signal: emitted when user edits a control while a block
    # is selected. Carries (property_name, value).
    block_override_changed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RightPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._active_block_id = None  # Set by MainWindow when a block is selected
        # Width is controlled by MainWindow via setFixedWidth(400).
        self.setStyleSheet(
            "QWidget#RightPanel {{"
            "  background-color: {bg};"
            "  border: 1px solid {border};"
            "  border-radius: 12px;"
            "}}".format(
                bg=Colors.bg_surface,
                border=Colors.border_subtle,
            )
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Tab bar (transparent — parent's surface shows through) ---
        tab_bar = QWidget()
        tab_bar.setObjectName("TabBar")
        tab_bar.setFixedHeight(44)
        # CRITICAL: tab bar does NOT paint its own background. It's transparent
        # so the parent RightPanel's bg_surface + border-radius show through.
        # The border-bottom gives visual separation from the content area.
        tab_bar.setStyleSheet(
            "QWidget#TabBar {{"
            "  background-color: transparent;"
            "  border-bottom: 1px solid {border};"
            "}}".format(border=Colors.border_subtle)
        )
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(16, 0, 16, 0)
        tab_layout.setSpacing(24)

        self._tab_friendly = QPushButton("FRIENDLY")
        self._tab_friendly.setCheckable(True)
        self._tab_friendly.setChecked(True)
        self._tab_friendly.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tab_friendly.setFont(Typography.label_caps())
        self._tab_friendly.setStyleSheet(self._tab_style(active=True))
        self._tab_friendly.clicked.connect(lambda: self._switch_tab(0))

        self._tab_advanced = QPushButton("ADVANCED")
        self._tab_advanced.setCheckable(True)
        self._tab_advanced.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tab_advanced.setFont(Typography.label_caps())
        self._tab_advanced.setStyleSheet(self._tab_style(active=False))
        self._tab_advanced.clicked.connect(lambda: self._switch_tab(1))

        tab_layout.addWidget(self._tab_friendly)
        tab_layout.addWidget(self._tab_advanced)
        tab_layout.addStretch()

        # Source indicator label (Section 16/38): shows where the effective
        # value comes from — "Inline Token", "Block Override", or "Global Default".
        # Updated by MainWindow via set_source_indicator().
        self._source_label = QLabel("")
        self._source_label.setFont(Typography.metadata_sm())
        self._source_label.setStyleSheet(
            f"color: {Colors.text_secondary}; background: transparent; padding-right: 8px;"
        )
        tab_layout.addWidget(self._source_label)
        layout.addWidget(tab_bar)

        # --- Stacked content ---
        self._stack = QStackedWidget()
        # The stack itself is transparent — it doesn't paint a bg.
        self._stack.setStyleSheet("QStackedWidget { background: transparent; }")
        layout.addWidget(self._stack, 1)

        # --- Tab 0: Friendly (single QScrollArea → FriendlyView) ---
        self._friendly = FriendlyView()
        self._friendly_scroll = self._make_scroll_area(self._friendly)
        self._stack.addWidget(self._friendly_scroll)

        # --- Tab 1: Advanced ---
        # The Advanced view is built directly as a QWidget with modern
        # sections. It does NOT embed the legacy ControlPanel (which was
        # itself a QScrollArea — that created nested scroll areas).
        # Instead, we create the functional widgets directly and wrap
        # them in our own modern section layout.
        self._advanced_view = AdvancedView()
        self._advanced_scroll = self._make_scroll_area(self._advanced_view)
        self._stack.addWidget(self._advanced_scroll)

        # The AdvancedView wraps the same functional widgets that the
        # old ControlPanel used. We expose them as self._advanced so
        # the existing API (get_emotion, set_emotion, etc.) still works.
        self._advanced = self._advanced_view

        # --- Wire all signals from Advanced → this wrapper ---
        self._advanced.emotion_changed.connect(self.emotion_changed.emit)
        self._advanced.style_changed.connect(self.style_changed.emit)
        self._advanced.speed_changed.connect(self.speed_changed.emit)
        self._advanced.pitch_changed.connect(self.pitch_changed.emit)
        self._advanced.delivery_changed.connect(self.delivery_changed.emit)
        self._advanced.sfx_inserted.connect(self.sfx_inserted.emit)
        self._advanced.pause_inserted.connect(self.pause_inserted.emit)
        self._advanced.parameters_changed.connect(self.parameters_changed.emit)
        self._advanced.voice_changed.connect(self.voice_changed.emit)
        self._advanced.import_voice_requested.connect(self.import_voice_requested.emit)
        self._advanced.create_voice_requested.connect(self.create_voice_requested.emit)
        self._advanced.transcript_changed.connect(self.transcript_changed.emit)
        self._advanced.voice_library_requested.connect(self.voice_library_requested.emit)
        self._advanced.generate_requested.connect(self.generate_requested.emit)

        # --- Wire Friendly → Advanced (state sync) ---
        self._friendly.emotion_changed.connect(lambda: self._sync_friendly_to_advanced('emotion'))
        self._friendly.style_changed.connect(lambda: self._sync_friendly_to_advanced('style'))
        self._friendly.speed_changed.connect(lambda: self._sync_friendly_to_advanced('speed'))
        self._friendly.pitch_changed.connect(lambda: self._sync_friendly_to_advanced('pitch'))
        self._friendly.delivery_changed.connect(lambda: self._sync_friendly_to_advanced('delivery'))
        self._friendly.sfx_inserted.connect(self.sfx_inserted.emit)
        self._friendly.pause_inserted.connect(self.pause_inserted.emit)
        self._friendly.parameters_changed.connect(self._on_friendly_parameters_changed)
        self._friendly.generate_clicked.connect(self.generate_requested.emit)
        self._friendly.change_voice_clicked.connect(self.voice_library_requested.emit)

        # --- Wire Advanced → Friendly (state sync) ---
        self.emotion_changed.connect(self._sync_advanced_to_friendly)
        self.style_changed.connect(self._sync_advanced_to_friendly)
        self.speed_changed.connect(self._sync_advanced_to_friendly)
        self.pitch_changed.connect(self._sync_advanced_to_friendly)
        self.delivery_changed.connect(self._sync_advanced_to_friendly)

    def _make_scroll_area(self, content: QWidget) -> QScrollArea:
        """Create a single vertical scroll area for a tab.

        The scroll area is transparent (parent's surface shows through).
        Horizontal scroll is always off. The content widget resizes to
        the viewport width.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setLineWidth(0)
        # CRITICAL: set transparent background on the scroll area AND its
        # viewport. Without this, the scroll area paints a default light
        # gray background (r=239) that appears as thin stripes at the edges.
        scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget {"
            "  background: transparent;"
            "  border: none;"
            "}"
            "QScrollBar:vertical {"
            "  background: transparent;"
            "  width: 14px;"
            "  border: none;"
            "  margin: 2px;"
            "}"
            "QScrollBar::handle:vertical {"
            "  background: #2E2E2E;"
            "  border-radius: 5px;"
            "  min-height: 30px;"
            "}"
            "QScrollBar::handle:vertical:hover {"
            "  background: #4a4a4a;"
            "}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
            "  height: 0; width: 0;"
            "}"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
            "  background: transparent;"
            "}"
        )
        # Also explicitly set the viewport to transparent
        scroll.viewport().setAutoFillBackground(False)
        scroll.viewport().setStyleSheet("background: transparent;")
        # Content must fit the viewport — never wider.
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        scroll.setWidget(content)
        return scroll

    def _tab_style(self, active: bool) -> str:
        color = Colors.primary if active else Colors.text_secondary
        border = f"border-bottom: 2px solid {Colors.primary};" if active else ""
        return f"""
            QPushButton {{
                background: transparent;
                border: none;
                {border}
                color: {color};
                padding: 10px 4px;
            }}
            QPushButton:hover {{
                color: {Colors.text_primary};
            }}
        """

    def _switch_tab(self, index: int):
        self._stack.setCurrentIndex(index)
        self._tab_friendly.setChecked(index == 0)
        self._tab_advanced.setChecked(index == 1)
        self._tab_friendly.setStyleSheet(self._tab_style(active=(index == 0)))
        self._tab_advanced.setStyleSheet(self._tab_style(active=(index == 1)))
        if index == 0:
            self._sync_advanced_to_friendly()

    def _sync_friendly_to_advanced(self, changed_value=None):
        """Friendly control changed → update Advanced panel + emit signal.

        CRITICAL (Section 37-40): Only emit the signal for the property
        that actually changed. Previously this method emitted ALL five
        signals on every change, which caused MainWindow to reset ALL
        properties (Style, Speed, Pitch, Delivery) when only Emotion
        changed.
        """
        self._advanced.blockSignals(True)
        try:
            self._advanced.set_emotion(self._friendly.get_emotion())
            self._advanced.set_style(self._friendly.get_style())
            self._advanced.set_speed(self._friendly.get_speed())
            self._advanced.set_pitch(self._friendly.get_pitch())
            self._advanced.set_delivery(self._friendly.get_delivery())
        finally:
            self._advanced.blockSignals(False)
        # Only emit the signal for the property that actually changed.
        # The sender is the FriendlyView signal that triggered this callback.
        if changed_value == 'emotion' or changed_value is None:
            self.emotion_changed.emit(self._friendly.get_emotion() or "")
        if changed_value == 'style' or changed_value is None:
            self.style_changed.emit(self._friendly.get_style() or "")
        if changed_value == 'speed' or changed_value is None:
            self.speed_changed.emit(self._friendly.get_speed() or "")
        if changed_value == 'pitch' or changed_value is None:
            self.pitch_changed.emit(self._friendly.get_pitch() or "")
        if changed_value == 'delivery' or changed_value is None:
            self.delivery_changed.emit(self._friendly.get_delivery() or "")

    def _sync_advanced_to_friendly(self, *args):
        emo = self._advanced.get_emotion()
        sty = self._advanced.get_style()
        spd = self._advanced.get_speed()
        pch = self._advanced.get_pitch()
        dlv = self._advanced.get_delivery()
        self._friendly._current_emotion = emo
        self._friendly._current_style = sty
        self._friendly._current_speed = spd
        self._friendly._current_pitch = pch
        self._friendly._current_delivery = dlv
        self._friendly.blockSignals(True)
        try:
            self._friendly.set_emotion(emo)
            self._friendly.set_style(sty)
            self._friendly.set_speed(spd)
            self._friendly.set_pitch(pch)
            self._friendly.set_delivery(dlv)
            # Also sync generation parameters (AI Freedom, Seed, Allow SFX)
            params = self._advanced.get_parameters()
            self._friendly.set_parameters(params)
        finally:
            self._friendly.blockSignals(False)

    def _on_friendly_parameters_changed(self):
        """Friendly generation params changed → sync to Advanced + emit."""
        params = self._friendly.get_parameters()
        self._advanced.blockSignals(True)
        try:
            self._advanced.set_parameters(params)
        finally:
            self._advanced.blockSignals(False)
        self.parameters_changed.emit()

    # ------------------------------------------------------------------
    # Public API — forward everything to the Advanced view
    # ------------------------------------------------------------------
    def get_emotion(self): return self._advanced.get_emotion()
    def get_style(self): return self._advanced.get_style()
    def get_speed(self): return self._advanced.get_speed()
    def get_pitch(self): return self._advanced.get_pitch()
    def get_delivery(self): return self._advanced.get_delivery()
    def get_parameters(self): return self._advanced.get_parameters()
    def get_selected_voice_id(self): return self._advanced.get_selected_voice_id()

    def set_emotion(self, emotion):
        if emotion == "__more__":
            return
        self._advanced.set_emotion(emotion)
        self._sync_advanced_to_friendly()

    def set_style(self, style):
        self._advanced.set_style(style)
        self._sync_advanced_to_friendly()

    def set_speed(self, value):
        self._advanced.set_speed(value)
        self._sync_advanced_to_friendly()

    def set_pitch(self, value):
        self._advanced.set_pitch(value)
        self._sync_advanced_to_friendly()

    def set_delivery(self, value):
        self._advanced.set_delivery(value)
        self._sync_advanced_to_friendly()

    def set_selected_voice_id(self, voice_id):
        self._advanced.set_selected_voice_id(voice_id)
        self._friendly.set_selected_voice_id(voice_id)

    def set_parameters(self, params):
        self._advanced.set_parameters(params)

    def set_voice_info(self, voice):
        self._advanced.set_voice_info(voice)
        self._friendly.set_voice_info(voice)

    def populate_voices(self, voices):
        self._advanced.populate_voices(voices)

    def set_inheritance_mode(self, mode, source=""):
        self._advanced.set_inheritance_mode(mode, source)

    def batch_update(self):
        return self._advanced.batch_update()

    def set_generate_enabled(self, enabled):
        self._advanced.set_generate_enabled(enabled)
        self._friendly.set_generate_enabled(enabled)

    def set_reference_validation(self, warnings):
        self._advanced.set_reference_validation(warnings)

    def update_reference_audio(self, voice):
        self._advanced.update_reference_audio(voice)

    def update_prompt_preview(self, prompt: str, warnings: list) -> None:
        self._advanced.update_prompt_preview(prompt, warnings)

    def set_source_indicator(self, property_name: str, source: str) -> None:
        """Update the source indicator label (Section 16/38).

        Args:
            property_name: e.g. "emotion", "style", "pitch"
            source: "Inline Token", "Block Override", "Global Default", or ""
        """
        if source:
            self._source_label.setText("{0}: {1}".format(property_name.capitalize(), source))
        else:
            self._source_label.setText("")

    def set_block_scope(self, block_id: Optional[str]) -> None:
        """Tell the Right Panel which Narration Block is currently selected.

        When a block is selected, control changes emit block_override_changed
        instead of the normal emotion_changed/style_changed/etc. signals.
        When no block is selected (block_id=None), controls operate on the
        global default as usual.
        """
        self._active_block_id = block_id


# ======================================================================
# ADVANCED VIEW — modern presentation of the existing functionality
# ======================================================================
class AdvancedView(QWidget):
    """Modern Advanced panel — replaces the legacy ControlPanel.

    This widget contains the SAME functional sections as the old
    ControlPanel (Voice, Emotion, Style, Prosody, SFX, Generation,
    Reference Audio, Prompt Preview), but uses a modern card-based
    layout instead of QGroupBox wrappers.

    It is NOT a QScrollArea — the parent RightPanel wraps it in one.
    It is transparent — the RightPanel's surface shows through.
    """

    # Signals (same as ControlPanel)
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
    transcript_changed = Signal(str)
    voice_library_requested = Signal()
    generate_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._batch_update_depth = 0
        self._build_ui()

    def set_generate_enabled(self, enabled: bool):
        pass  # stub — MainWindow calls this

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        # --- Inheritance banner ---
        self._inheritance_banner = QLabel("")
        self._inheritance_banner.setWordWrap(True)
        self._inheritance_banner.setVisible(False)
        self._inheritance_banner.setStyleSheet(
            "QLabel {{ background-color: {0}; color: {1}; "
            "border: 1px solid {2}; border-radius: 4px; "
            "padding: 6px 8px; }}".format(
                Palette.BG_SURFACE, Palette.TEXT_PRIMARY, Palette.BORDER))
        layout.addWidget(self._inheritance_banner)

        # --- Functional sections (same widgets as ControlPanel) ---
        self._voice_section = VoiceSection()
        self._voice_section.voice_changed.connect(self.voice_changed.emit)
        self._voice_section.import_voice_requested.connect(self.import_voice_requested.emit)
        self._voice_section.create_voice_requested.connect(self.create_voice_requested.emit)
        layout.addWidget(self._make_section("VOICE", self._voice_section))

        self._emotion_buttons = EmotionButtons()
        self._emotion_buttons.emotion_changed.connect(self.emotion_changed.emit)
        layout.addWidget(self._make_section("EMOTION", self._emotion_buttons))

        self._style_buttons = StyleButtons()
        self._style_buttons.style_changed.connect(self.style_changed.emit)
        layout.addWidget(self._make_section("STYLE", self._style_buttons))

        self._prosody = ProsodySection()
        self._prosody.speed_changed.connect(self.speed_changed.emit)
        self._prosody.pitch_changed.connect(self.pitch_changed.emit)
        self._prosody.delivery_changed.connect(self.delivery_changed.emit)
        self._prosody.pause_inserted.connect(self.pause_inserted.emit)
        layout.addWidget(self._make_section("PROSODY", self._prosody))

        self._sfx = SfxSection()
        self._sfx.sfx_inserted.connect(self.sfx_inserted.emit)
        layout.addWidget(self._make_section("SOUND EFFECTS", self._sfx))

        self._generation = GenerationSection()
        self._generation.parameters_changed.connect(self.parameters_changed.emit)
        layout.addWidget(self._make_section("GENERATION", self._generation))

        from ui.panels.reference_audio import ReferenceAudioPanel
        self._reference_audio = ReferenceAudioPanel()
        # P3.43 §18: the panel is a read-only display — transcript editing
        # is owned by the single Voice Profile editor (no transcript signal).
        layout.addWidget(self._make_section("REFERENCE AUDIO", self._reference_audio))

        self._preview = PromptPreviewSection()
        layout.addWidget(self._make_section("PROMPT PREVIEW", self._preview))

        layout.addStretch()

    def _make_section(self, title: str, content: QWidget) -> QWidget:
        """Modern section: label_caps header + card frame with content.

        P3.19: Restyled to match the FriendlyView's card aesthetic —
        uses Colors tokens, rounded corners, subtle border, and
        consistent padding matching the Friendly panel's sections.
        """
        section = QWidget()
        section.setMinimumWidth(0)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(6)

        header = QLabel(title)
        header.setFont(Typography.label_caps())
        header.setStyleSheet(
            f"color: {Colors.text_secondary}; background: transparent;"
        )
        section_layout.addWidget(header)

        card = QFrame()
        card.setObjectName("SectionCard")
        card.setStyleSheet(
            "QFrame#SectionCard {{"
            "  background-color: {bg};"
            "  border: 1px solid {border};"
            "  border-radius: 12px; padding: 1px;"
            "}}".format(bg=Colors.bg_surface_alt, border=Colors.border_subtle)
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)
        content.setMinimumWidth(0)
        card_layout.addWidget(content)
        section_layout.addWidget(card)
        return section

    # --- Access methods (same as ControlPanel) ---
    def get_emotion(self): return self._emotion_buttons.get_selected()
    def get_style(self): return self._style_buttons.get_selected()
    def get_speed(self): return self._prosody.get_speed()
    def get_pitch(self): return self._prosody.get_pitch()
    def get_delivery(self): return self._prosody.get_delivery()
    def get_parameters(self): return self._generation.get_parameters()

    def set_parameters(self, params): self._generation.set_parameters(params)

    def update_prompt_preview(self, prompt, warnings):
        self._preview.update_prompt(prompt, warnings)

    def populate_voices(self, voices): self._voice_section.populate_voices(voices)
    def set_voice_info(self, voice): self._voice_section.set_voice_info(voice)
    def get_selected_voice_id(self): return self._voice_section.get_selected_voice_id()

    def set_emotion(self, emotion): self._emotion_buttons.set_selected(emotion)
    def set_style(self, style): self._style_buttons.set_selected(style)
    def set_speed(self, value): self._prosody.set_speed(value)
    def set_pitch(self, value): self._prosody.set_pitch(value)
    def set_delivery(self, value): self._prosody.set_delivery(value)
    def set_selected_voice_id(self, voice_id): self._voice_section.set_selected_voice_id(voice_id)

    def set_inheritance_mode(self, mode, source=""):
        if not mode:
            self._inheritance_banner.setVisible(False)
            self._inheritance_banner.setText("")
            return
        text = "Inheriting from: {0}".format(source or mode)
        self._inheritance_banner.setText(text)
        self._inheritance_banner.setVisible(True)

    def batch_update(self):
        class _Ctx:
            def __init__(self, panel):
                self._panel = panel
            def __enter__(self):
                self._panel._batch_update_depth += 1
            def __exit__(self, *args):
                if self._panel._batch_update_depth > 0:
                    self._panel._batch_update_depth -= 1
                if self._panel._batch_update_depth == 0:
                    self._panel.parameters_changed.emit()
        return _Ctx(self)

    def update_reference_audio(self, voice): self._reference_audio.update_voice(voice)
    def set_reference_validation(self, warnings): self._reference_audio.set_validation_warnings(warnings)

    # P3.43 §18: transcript editing on the panel was removed (the single
    # Voice Profile editor owns transcript persistence now).


# ======================================================================
# FRIENDLY VIEW — Approved audit implementation
# ======================================================================
class FriendlyView(QWidget):
    """Friendly view — high-level user-intent layer over Advanced state.

    Two sub-tabs: VOICE and GENERATION.

    VOICE tab:
        - Selected Speaker (display + Change Voice)
        - Emotion & Style (8 primary emotions + More + Custom Style)
        - Prosody (Pace / Pitch / Expression)

    GENERATION tab:
        - AI Freedom (5 levels + Custom)
        - Seed
        - Allow SFX toggle
        - SFX grid (insert at cursor)
        - Pause buttons
        - Inline Controls Help

    All controls map to the SAME underlying state as Advanced.
    No independent Friendly state. Two-way synchronized.
    """

    # Signals emitted when Friendly controls change underlying state.
    emotion_changed = Signal(str)
    style_changed = Signal(str)
    speed_changed = Signal(str)
    pitch_changed = Signal(str)
    delivery_changed = Signal(str)
    sfx_inserted = Signal(str, str)   # sfx_name, onomatopoeia
    pause_inserted = Signal(str)       # "pause" | "long_pause"
    parameters_changed = Signal()      # generation params changed
    generate_clicked = Signal()
    change_voice_clicked = Signal()

    # 8 primary Friendly emotions.
    # Each maps to an underlying engine emotion/style.
    # "Whisper" maps to Style=Whispering (cross-category).
    # "Tense" maps to Emotion=Fear (approved audit, §6C).
    PRIMARY_EMOTIONS = [
        # (Friendly label, underlying type, underlying value, icon_name)
        ("Neutral",  "none",    None,          "sentiment_neutral"),
        ("Happy",    "emotion", "Elation",     "sentiment_satisfied"),
        ("Sad",      "emotion", "Sadness",     "sentiment_dissatisfied"),
        ("Anger",    "emotion", "Anger",       "mood_bad"),
        ("Calm",     "emotion", "Contentment", "self_improvement"),
        ("Fear",     "emotion", "Fear",        "sentiment_very_dissatisfied"),
        ("Whisper",  "style",   "Whispering",  "graphic_eq"),
        ("Joy",      "emotion", "Enthusiasm",  "celebration"),
    ]

    # The remaining 13 HIGGS emotions (accessible via "More Emotions").
    MORE_EMOTIONS = [
        "Amusement", "Determination", "Pride", "Affection",
        "Relief", "Contemplation", "Confusion", "Surprise",
        "Awe", "Longing", "Arousal", "Disgust", "Bitterness",
        "Shame", "Helplessness", "Fear",
    ]

    STYLE_OPTIONS = [s.name for s in STYLES]  # Singing, Whispering, Shouting

    # Delivery label ↔ engine name mapping.
    _EXPRESSION_LABEL_TO_ENGINE = {
        "Subtle": "Expressive Low",
        "Natural": "Normal",
        "Anim": "Expressive High",
    }
    _EXPRESSION_ENGINE_TO_LABEL = {v: k for k, v in _EXPRESSION_LABEL_TO_ENGINE.items()}

    # AI Freedom lookup table (approved audit §18).
    # Each level maps to exact Temperature / Top P / Top K values.
    _AI_FREEDOM_LEVELS = [
        # (label, temperature, top_p, top_k)
        ("Strict",    0.5, 0.80, 50),
        ("Focused",   0.8, 0.90, 100),
        ("Balanced",  1.0, 0.95, 200),
        ("Expressive", 1.3, 0.95, 300),
        ("Wild",      1.6, 1.0, 500),
    ]
    _AI_FREEDOM_CUSTOM = "Custom"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        # Underlying state (mirrors Advanced — single source of truth).
        self._current_emotion = None
        self._current_style = None
        self._current_speed = "Normal"
        self._current_pitch = "Normal"
        self._current_delivery = "Normal"
        self._voice_name = "(No voice)"
        self._voice_id = None

        # AI Freedom ownership tracking (per-parameter).
        # When AI Freedom changes a parameter, we store the previous value
        # and mark it as owned. On deactivation, only owned params are restored.
        self._ai_freedom_ownership = {}  # param_name -> previous_value

        # Generation parameters (mirrors Advanced).
        self._temperature = 1.3
        self._top_p = 0.95
        self._top_k = 300
        self._max_new_tokens = 4096
        self._seed = None
        self._append_silence = 0.5
        self._normalize_output = False
        self._auto_play = True
        self._allow_sfx = True

        self._build_ui()

    # ==================================================================
    # UI CONSTRUCTION
    # ==================================================================
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        # --- Sub-tab bar: VOICE | GENERATION ---
        self._sub_tab_bar = self._build_sub_tab_bar()
        layout.addWidget(self._sub_tab_bar)

        # --- Stacked content ---
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("QStackedWidget { background: transparent; }")
        layout.addWidget(self._stack, 1)

        # Tab 0: VOICE
        self._voice_tab = self._build_voice_tab()
        self._stack.addWidget(self._voice_tab)

        # Tab 1: GENERATION
        self._gen_tab = self._build_generation_tab()
        self._stack.addWidget(self._gen_tab)

        # --- Generate CTA (shared, at bottom) ---
        layout.addWidget(self._build_generate_cta())

    def _build_sub_tab_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet("background: transparent;")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(16)

        self._sub_tab_voice = QPushButton("VOICE")
        self._sub_tab_voice.setCheckable(True)
        self._sub_tab_voice.setChecked(True)
        self._sub_tab_voice.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sub_tab_voice.setFont(Typography.label_caps())
        self._sub_tab_voice.setStyleSheet(self._sub_tab_style(True))
        self._sub_tab_voice.clicked.connect(lambda: self._switch_sub_tab(0))

        self._sub_tab_gen = QPushButton("GENERATION")
        self._sub_tab_gen.setCheckable(True)
        self._sub_tab_gen.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sub_tab_gen.setFont(Typography.label_caps())
        self._sub_tab_gen.setStyleSheet(self._sub_tab_style(False))
        self._sub_tab_gen.clicked.connect(lambda: self._switch_sub_tab(1))

        bar_layout.addWidget(self._sub_tab_voice)
        bar_layout.addWidget(self._sub_tab_gen)
        bar_layout.addStretch()
        return bar

    def _sub_tab_style(self, active: bool) -> str:
        color = Colors.primary if active else Colors.text_secondary
        border = f"border-bottom: 2px solid {Colors.primary};" if active else ""
        return f"""
            QPushButton {{
                background: transparent;
                border: none;
                {border}
                color: {color};
                padding: 6px 2px;
            }}
            QPushButton:hover {{ color: {Colors.text_primary}; }}
        """

    def _switch_sub_tab(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._sub_tab_voice.setChecked(index == 0)
        self._sub_tab_gen.setChecked(index == 1)
        self._sub_tab_voice.setStyleSheet(self._sub_tab_style(index == 0))
        self._sub_tab_gen.setStyleSheet(self._sub_tab_style(index == 1))

    # ------------------------------------------------------------------
    # VOICE TAB
    # ------------------------------------------------------------------
    def _build_voice_tab(self) -> QWidget:
        tab = QWidget()
        tab.setMinimumWidth(0)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # --- SELECTED SPEAKER ---
        layout.addWidget(self._build_selected_speaker())

        # --- EMOTION & STYLE ---
        layout.addWidget(self._build_emotion_style())

        # --- PROSODY ---
        layout.addWidget(self._build_prosody())

        layout.addStretch()
        return tab

    def _build_selected_speaker(self) -> QWidget:
        section = QWidget()
        section.setMinimumWidth(0)
        s_layout = QVBoxLayout(section)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(4)

        label = QLabel("SELECTED SPEAKER")
        label.setFont(Typography.label_caps())
        label.setStyleSheet(f"color: {Colors.text_secondary}; background: transparent;")
        s_layout.addWidget(label)

        self._voice_card = QFrame()
        self._voice_card.setObjectName("VoiceCard")
        self._voice_card.setStyleSheet(f"""
            QFrame#VoiceCard {{
                background-color: {Colors.bg_surface_alt};
                border: 1px solid {Colors.border_subtle};
                border-radius: 12px;
            }}
        """)
        card_layout = QHBoxLayout(self._voice_card)
        card_layout.setContentsMargins(8, 6, 8, 6)
        card_layout.setSpacing(8)

        self._avatar = QLabel("?")
        self._avatar.setFixedSize(32, 32)
        self._avatar.setAlignment(Qt.Alignment.AlignCenter)
        self._avatar.setFont(Typography.headline_md())
        self._avatar.setStyleSheet(f"""
            background-color: {Colors.primary};
            color: {Colors.on_primary};
            border-radius: 16px;
        """)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        self._voice_name_label = QLabel("(No voice)")
        self._voice_name_label.setFont(Typography.body_md())
        self._voice_name_label.setStyleSheet(f"color: {Colors.text_primary}; background: transparent;")
        self._voice_meta_label = QLabel("")
        self._voice_meta_label.setFont(Typography.metadata_sm())
        self._voice_meta_label.setStyleSheet(f"color: {Colors.text_secondary}; background: transparent;")
        info_col.addWidget(self._voice_name_label)
        info_col.addWidget(self._voice_meta_label)

        card_layout.addWidget(self._avatar)
        card_layout.addLayout(info_col, 1)

        self._change_voice_btn = QPushButton("Change")
        self._change_voice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._change_voice_btn.setFont(Typography.metadata_sm())
        self._change_voice_btn.setMinimumWidth(0)
        self._change_voice_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._change_voice_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.bg_raised};
                color: {Colors.text_secondary};
                border: 1px solid {Colors.border_subtle};
                border-radius: 6px;
                padding: 4px 10px;
            }}
            QPushButton:hover {{
                border-color: {Colors.primary};
                color: {Colors.primary};
            }}
        """)
        self._change_voice_btn.clicked.connect(self.change_voice_clicked.emit)
        card_layout.addWidget(self._change_voice_btn)

        s_layout.addWidget(self._voice_card)
        return section

    def _build_emotion_style(self) -> QWidget:
        section = QWidget()
        section.setMinimumWidth(0)
        s_layout = QVBoxLayout(section)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(4)

        label = QLabel("EMOTION & STYLE")
        label.setFont(Typography.label_caps())
        label.setStyleSheet(f"color: {Colors.text_secondary}; background: transparent;")
        s_layout.addWidget(label)

        # 4×2 emotion grid
        from PySide6.QtWidgets import QGridLayout
        emo_grid = QGridLayout()
        emo_grid.setSpacing(2)

        self._emotion_buttons = {}
        for i, (friendly_label, emo_type, engine_value, icon_name) in enumerate(self.PRIMARY_EMOTIONS):
            btn = QPushButton(friendly_label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(36)
            btn.setFont(Typography.metadata_sm())
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setStyleSheet(self._emotion_btn_style(False))
            btn.clicked.connect(lambda checked, fl=friendly_label: self._on_emotion_clicked(fl))
            row, col = divmod(i, 4)
            emo_grid.addWidget(btn, row, col)
            # P3.20: Make columns stretch equally so buttons fill available width
            emo_grid.setColumnStretch(col, 1)
            self._emotion_buttons[friendly_label] = btn
        s_layout.addLayout(emo_grid)

        # More emotions button
        self._more_emotions_btn = QPushButton("More emotions")
        self._more_emotions_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more_emotions_btn.setFont(Typography.metadata_sm())
        self._more_emotions_btn.setMinimumWidth(0)
        self._more_emotions_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.bg_surface_alt};
                color: {Colors.text_secondary};
                border: 1px solid {Colors.border_subtle};
                border-radius: 6px;
                padding: 6px 10px;
            }}
            QPushButton:hover {{
                border-color: {Colors.primary};
                color: {Colors.primary};
            }}
        """)
        self._more_emotions_btn.clicked.connect(self._on_more_emotions)
        s_layout.addWidget(self._more_emotions_btn)

        # More emotions popup menu (lazy)
        self._more_emotions_menu = None

        # Custom Style dropdown
        style_row = QHBoxLayout()
        style_row.setSpacing(4)
        style_label = QLabel("Style")
        style_label.setFixedWidth(40)
        style_label.setFont(Typography.metadata_sm())
        style_label.setStyleSheet(f"color: {Colors.text_secondary}; background: transparent;")
        style_row.addWidget(style_label)

        self._style_buttons = {}
        # "None" button (clears style)
        none_btn = QPushButton("None")
        none_btn.setCheckable(True)
        none_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        none_btn.setFont(Typography.metadata_sm())
        none_btn.setMinimumWidth(0)
        none_btn.setChecked(True)
        none_btn.setStyleSheet(self._segmented_btn_style(True))
        none_btn.clicked.connect(lambda: self._on_style_clicked(None))
        style_row.addWidget(none_btn, 1)
        self._style_buttons[None] = none_btn

        for style_name in self.STYLE_OPTIONS:
            btn = QPushButton(style_name)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFont(Typography.metadata_sm())
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setStyleSheet(self._segmented_btn_style(False))
            btn.clicked.connect(lambda checked, s=style_name: self._on_style_clicked(s))
            style_row.addWidget(btn, 1)
            self._style_buttons[style_name] = btn
        s_layout.addLayout(style_row)

        return section

    def _build_prosody(self) -> QWidget:
        section = QWidget()
        section.setMinimumWidth(0)
        s_layout = QVBoxLayout(section)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(4)

        label = QLabel("PROSODY")
        label.setFont(Typography.label_caps())
        label.setStyleSheet(f"color: {Colors.text_secondary}; background: transparent;")
        s_layout.addWidget(label)

        # Pace (5 options)
        self._pace_seg = self._make_segmented(
            "Pace", ["V.Slow", "Slow", "Norm", "Fast", "V.Fast"], "Norm",
            self._on_pace_changed)
        s_layout.addLayout(self._pace_seg["layout"])

        # Pitch (3 options)
        self._pitch_seg = self._make_segmented(
            "Pitch", ["Low", "Norm", "High"], "Norm",
            self._on_pitch_changed)
        s_layout.addLayout(self._pitch_seg["layout"])

        # Expression (3 options: Subtle / Natural / Animated)
        self._expression_seg = self._make_segmented(
            "Expr", ["Subtle", "Natural", "Anim"], "Natural",
            self._on_expression_changed, label_width=40)
        s_layout.addLayout(self._expression_seg["layout"])

        return section

    # ------------------------------------------------------------------
    # GENERATION TAB
    # ------------------------------------------------------------------
    def _build_generation_tab(self) -> QWidget:
        tab = QWidget()
        tab.setMinimumWidth(0)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # --- AI FREEDOM ---
        layout.addWidget(self._build_ai_freedom())

        # --- SEED ---
        layout.addWidget(self._build_seed())

        # --- ALLOW SFX ---
        layout.addWidget(self._build_allow_sfx())

        # --- SFX ---
        layout.addWidget(self._build_sfx())

        # --- PAUSE ---
        layout.addWidget(self._build_pause())

        # Inline Controls Help removed from Friendly (Issue 6).
        # The help text was static and took up space without adding enough
        # value in the Friendly tab. SFX/pause functionality is unchanged.

        layout.addStretch()
        return tab

    def _build_ai_freedom(self) -> QWidget:
        section = QWidget()
        section.setMinimumWidth(0)
        s_layout = QVBoxLayout(section)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(4)

        label = QLabel("AI FREEDOM")
        label.setFont(Typography.label_caps())
        label.setStyleSheet(f"color: {Colors.text_secondary}; background: transparent;")
        s_layout.addWidget(label)

        # Segmented control with 5 levels + Custom
        self._ai_freedom_labels = [lvl[0] for lvl in self._AI_FREEDOM_LEVELS] + [self._AI_FREEDOM_CUSTOM]
        self._ai_freedom_seg = self._make_segmented(
            "", self._ai_freedom_labels, "Expressive",
            self._on_ai_freedom_changed, label_width=0)
        s_layout.addLayout(self._ai_freedom_seg["layout"])

        return section

    def _build_seed(self) -> QWidget:
        section = QWidget()
        section.setMinimumWidth(0)
        s_layout = QVBoxLayout(section)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(4)

        label = QLabel("SEED")
        label.setFont(Typography.label_caps())
        label.setStyleSheet(f"color: {Colors.text_secondary}; background: transparent;")
        s_layout.addWidget(label)

        row = QHBoxLayout()
        row.setSpacing(4)
        from PySide6.QtWidgets import QLineEdit
        self._seed_edit = QLineEdit()
        self._seed_edit.setPlaceholderText("Random")
        self._seed_edit.setFont(Typography.mono_data())
        self._seed_edit.setMinimumWidth(0)
        self._seed_edit.setStyleSheet(self._input_style())
        self._seed_edit.textChanged.connect(self._on_seed_changed)
        row.addWidget(self._seed_edit, 1)

        randomize_btn = QPushButton("🎲")
        randomize_btn.setFixedSize(32, 32)
        randomize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        randomize_btn.setToolTip("Clear seed (random)")
        randomize_btn.setStyleSheet(self._icon_btn_style())
        randomize_btn.clicked.connect(lambda: self._seed_edit.clear())
        row.addWidget(randomize_btn)

        s_layout.addLayout(row)
        return section

    def _build_allow_sfx(self) -> QWidget:
        section = QWidget()
        section.setMinimumWidth(0)
        s_layout = QVBoxLayout(section)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(4)

        label = QLabel("ALLOW SFX")
        label.setFont(Typography.label_caps())
        label.setStyleSheet(f"color: {Colors.text_secondary}; background: transparent;")
        s_layout.addWidget(label)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._allow_sfx_btn = QPushButton("ON")
        self._allow_sfx_btn.setCheckable(True)
        self._allow_sfx_btn.setChecked(True)
        self._allow_sfx_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._allow_sfx_btn.setFont(Typography.metadata_sm())
        self._allow_sfx_btn.setMinimumWidth(0)
        self._allow_sfx_btn.setStyleSheet(self._toggle_style(True))
        self._allow_sfx_btn.clicked.connect(self._on_allow_sfx_toggled)
        row.addWidget(self._allow_sfx_btn, 1)

        s_layout.addLayout(row)
        return section

    def _build_sfx(self) -> QWidget:
        from engine.higgs_tokens import SFX_TOKENS
        section = QWidget()
        section.setMinimumWidth(0)
        s_layout = QVBoxLayout(section)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(4)

        label = QLabel("SOUND EFFECTS")
        label.setFont(Typography.label_caps())
        label.setStyleSheet(f"color: {Colors.text_secondary}; background: transparent;")
        s_layout.addWidget(label)

        from PySide6.QtWidgets import QGridLayout
        sfx_grid = QGridLayout()
        sfx_grid.setSpacing(2)
        cols = 3
        for i, sfx in enumerate(SFX_TOKENS):
            btn = QPushButton(sfx.name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFont(Typography.metadata_sm())
            btn.setFixedHeight(36)  # Issue 7: was 28, caused descender clipping
            btn.setMinimumWidth(0)
            btn.setStyleSheet(self._small_btn_style())
            btn.setToolTip(f"Insert {sfx.name}\nSuggested: {sfx.onomatopoeia}\nToken: <|sfx:{sfx.tag}|>")
            btn.clicked.connect(
                lambda checked, name=sfx.name, onom=sfx.onomatopoeia:
                self.sfx_inserted.emit(name, onom))
            row, col = divmod(i, cols)
            sfx_grid.addWidget(btn, row, col)
        s_layout.addLayout(sfx_grid)
        return section

    def _build_pause(self) -> QWidget:
        section = QWidget()
        section.setMinimumWidth(0)
        s_layout = QVBoxLayout(section)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(4)

        label = QLabel("PAUSE")
        label.setFont(Typography.label_caps())
        label.setStyleSheet(f"color: {Colors.text_secondary}; background: transparent;")
        s_layout.addWidget(label)

        row = QHBoxLayout()
        row.setSpacing(4)
        pause_btn = QPushButton("Insert Pause")
        pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pause_btn.setFont(Typography.metadata_sm())
        pause_btn.setFixedHeight(36)  # Issue 7: was 28, caused descender clipping
        pause_btn.setMinimumWidth(0)
        pause_btn.setStyleSheet(self._small_btn_style())
        pause_btn.setToolTip("Insert a short pause (~400-700ms)")
        pause_btn.clicked.connect(lambda: self.pause_inserted.emit("pause"))
        row.addWidget(pause_btn, 1)

        long_pause_btn = QPushButton("Insert Long Pause")
        long_pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        long_pause_btn.setFont(Typography.metadata_sm())
        long_pause_btn.setFixedHeight(36)  # Issue 7: was 28, caused descender clipping
        long_pause_btn.setMinimumWidth(0)
        long_pause_btn.setStyleSheet(self._small_btn_style())
        long_pause_btn.setToolTip("Insert a long pause (~700-1500ms)")
        long_pause_btn.clicked.connect(lambda: self.pause_inserted.emit("long_pause"))
        row.addWidget(long_pause_btn, 1)

        s_layout.addLayout(row)
        return section

    def _build_inline_help(self) -> QWidget:
        section = QWidget()
        section.setMinimumWidth(0)
        s_layout = QVBoxLayout(section)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(4)

        label = QLabel("INLINE CONTROLS")
        label.setFont(Typography.label_caps())
        label.setStyleSheet(f"color: {Colors.text_secondary}; background: transparent;")
        s_layout.addWidget(label)

        help_text = (
            "SFX:  {sfx:Laughter:Haha}\n"
            "Pause:  {pause}  or  {long_pause}\n"
            "Raw tokens:  <|emotion:fear|>  <|style:whispering|>"
        )
        help_label = QLabel(help_text)
        help_label.setFont(Typography.mono_data())
        help_label.setWordWrap(True)
        help_label.setStyleSheet(f"""
            color: {Colors.text_secondary};
            background-color: {Colors.bg_surface_alt};
            border: 1px solid {Colors.border_subtle};
            border-radius: 6px;
            padding: 8px;
        """)
        s_layout.addWidget(help_label)
        return section

    def _build_generate_cta(self) -> QPushButton:
        self._generate_btn = QPushButton("▶  GENERATE SELECTED LINE")
        self._generate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._generate_btn.setFixedHeight(40)
        self._generate_btn.setFont(Typography.cta_main())
        self._generate_btn.setMinimumWidth(0)
        self._generate_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #F97316, stop:1 #EA580C);
                color: white;
                border: none;
                border-radius: 10px;
            }}
            QPushButton:hover {{ opacity: 0.9; }}
            QPushButton:disabled {{
                background: {Colors.bg_raised};
                color: {Colors.text_disabled};
            }}
        """)
        self._generate_btn.clicked.connect(self.generate_clicked.emit)
        return self._generate_btn

    # ==================================================================
    # STYLES
    # ==================================================================
    def _emotion_btn_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background-color: rgba(208, 188, 255, 0.15);
                    color: {Colors.primary};
                    border: 2px solid {Colors.primary};
                    border-radius: 6px; padding: 1px;
                    font-weight: 600;
                }}
            """
        return f"""
            QPushButton {{
                background-color: {Colors.bg_surface_alt};
                color: {Colors.text_secondary};
                border: 1px solid {Colors.border_subtle};
                border-radius: 6px; padding: 1px;
            }}
            QPushButton:hover {{
                border-color: {Colors.primary};
                color: {Colors.primary};
            }}
        """

    def _segmented_btn_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background-color: {Colors.primary};
                    color: {Colors.on_primary};
                    border: none;
                    border-radius: 6px;
                    padding: 4px 6px;
                }}
            """
        return f"""
            QPushButton {{
                background-color: {Colors.bg_surface_alt};
                color: {Colors.text_secondary};
                border: 1px solid {Colors.border_subtle};
                border-radius: 6px;
                padding: 4px 6px;
            }}
            QPushButton:hover {{
                border-color: {Colors.primary};
                color: {Colors.primary};
            }}
        """

    def _small_btn_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {Colors.bg_surface_alt};
                color: {Colors.text_secondary};
                border: 1px solid {Colors.border_subtle};
                border-radius: 6px;
                padding: 4px 6px;  /* Issue 7: ensure descenders aren't clipped */
            }}
            QPushButton:hover {{
                border-color: {Colors.primary};
                color: {Colors.primary};
            }}
        """

    def _icon_btn_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {Colors.bg_surface_alt};
                color: {Colors.text_secondary};
                border: 1px solid {Colors.border_subtle};
                border-radius: 6px;
                font-size: 16px;
            }}
            QPushButton:hover {{
                border-color: {Colors.primary};
                color: {Colors.primary};
            }}
        """

    def _input_style(self) -> str:
        return f"""
            QLineEdit {{
                background-color: {Colors.bg_surface_alt};
                color: {Colors.text_primary};
                border: 1px solid {Colors.border_subtle};
                border-radius: 6px;
                padding: 4px 8px;
            }}
            QLineEdit:focus {{
                border-color: {Colors.primary};
            }}
        """

    def _toggle_style(self, on: bool) -> str:
        if on:
            return f"""
                QPushButton {{
                    background-color: {Colors.primary};
                    color: {Colors.on_primary};
                    border: none;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-weight: 600;
                }}
            """
        return f"""
            QPushButton {{
                background-color: {Colors.bg_surface_alt};
                color: {Colors.text_secondary};
                border: 1px solid {Colors.border_subtle};
                border-radius: 6px;
                padding: 6px 12px;
            }}
        """

    def _make_segmented(self, label_text, options, default, callback, label_width=40):
        row = QHBoxLayout()
        row.setSpacing(4)
        if label_text and label_width > 0:
            label = QLabel(label_text)
            label.setMinimumWidth(0)
            label.setMaximumWidth(label_width)
            label.setFont(Typography.metadata_sm())
            label.setStyleSheet(f"color: {Colors.text_secondary}; background: transparent;")
            row.addWidget(label, 0)
        group = QButtonGroup(self)
        group.setExclusive(True)
        buttons = {}
        for opt in options:
            btn = QPushButton(opt)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setChecked(opt == default)
            btn.setFont(Typography.metadata_sm())
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setStyleSheet(self._segmented_btn_style(opt == default))
            btn.clicked.connect(lambda checked, o=opt: callback(o))
            group.addButton(btn)
            row.addWidget(btn, 1)
            buttons[opt] = btn
        return {"layout": row, "group": group, "buttons": buttons}

    # ==================================================================
    # EVENT HANDLERS — Friendly → Advanced
    # ==================================================================
    def _on_emotion_clicked(self, friendly_label: str) -> None:
        """Friendly emotion button clicked → set underlying emotion/style.

        Emotion and Style are INDEPENDENT (Issue 11-12):
        - Whisper sets Style=Whispering WITHOUT clearing Emotion.
        - Emotions set Emotion WITHOUT clearing Style.
        - Neutral clears Emotion only (not Style).
        Both can be active simultaneously.
        """
        # Find the mapping
        for fl, emo_type, engine_value, icon_name in self.PRIMARY_EMOTIONS:
            if fl == friendly_label:
                if emo_type == "style":
                    # Whisper: toggle Style=Whispering (does NOT clear Emotion)
                    if self._current_style == engine_value:
                        # Already active — toggle off
                        self._current_style = None
                        self._update_emotion_buttons(None if self._current_emotion is None else friendly_label)
                        self._update_style_buttons(None)
                    else:
                        self._current_style = engine_value
                        self._update_emotion_buttons(friendly_label)
                        self._update_style_buttons(engine_value)
                    self.style_changed.emit(self._current_style or "")
                elif emo_type == "none":
                    # Neutral: clear Emotion only (NOT Style)
                    self._current_emotion = None
                    self._update_emotion_buttons(friendly_label)
                    self.emotion_changed.emit("")
                else:
                    # Normal emotion: set Emotion (does NOT clear Style)
                    if self._current_emotion == engine_value:
                        # Already active — toggle off → Neutral
                        self._current_emotion = None
                        self._update_emotion_buttons("Neutral")
                    else:
                        self._current_emotion = engine_value
                        self._update_emotion_buttons(friendly_label)
                    self.emotion_changed.emit(self._current_emotion or "")
                return

    def _on_style_clicked(self, style_name) -> None:
        """Custom Style dropdown clicked → set underlying Style.

        Style is independent from Emotion (Issue 11-12).
        Setting a style does NOT clear the emotion.
        """
        self._current_style = style_name
        self._update_style_buttons(style_name)
        # Update Whisper highlight in emotion grid (without clearing emotion)
        if style_name == "Whispering":
            # Whispering selected via Style — activate Whisper button too
            self._update_emotion_buttons("Whisper")
        elif style_name is None:
            # Style cleared — if Whisper was active, deactivate it
            # but keep the current emotion active
            active_emo_label = None
            for fl, emo_type, engine_value, _ in self.PRIMARY_EMOTIONS:
                if emo_type == "emotion" and engine_value == self._current_emotion:
                    active_emo_label = fl
                    break
            if active_emo_label is None:
                active_emo_label = "Neutral" if self._current_emotion is None else None
            self._update_emotion_buttons(active_emo_label)
        else:
            # Non-Whisper style (Singing/Shouting) — don't touch emotion grid
            pass
        self.style_changed.emit(style_name or "")

    def _on_more_emotions(self) -> None:
        """Show More Emotions popup menu."""
        from PySide6.QtWidgets import QMenu
        if self._more_emotions_menu is None:
            self._more_emotions_menu = QMenu(self)
            for emo_name in self.MORE_EMOTIONS:
                action = self._more_emotions_menu.addAction(emo_name)
                action.triggered.connect(lambda checked, name=emo_name: self._select_more_emotion(name))
        self._more_emotions_menu.exec(self._more_emotions_btn.mapToGlobal(self._more_emotions_btn.rect().bottomLeft()))

    def _select_more_emotion(self, emotion_name: str) -> None:
        """Select a 'More' emotion → set underlying Emotion, keep Style (Issue 11-12)."""
        self._current_emotion = emotion_name
        # Do NOT clear Style — Emotion and Style are independent
        self._update_emotion_buttons(None)  # No primary button active (More emotion)
        self.emotion_changed.emit(emotion_name)

    def _on_pace_changed(self, value: str) -> None:
        """Pace segmented control changed → set underlying Speed."""
        speed_map = {"V.Slow": "Very Slow", "Slow": "Slow", "Norm": "Normal",
                     "Fast": "Fast", "V.Fast": "Very Fast"}
        self._current_speed = speed_map.get(value, "Normal")
        self._update_segmented(self._pace_seg, value)
        self.speed_changed.emit(self._current_speed)

    def _on_pitch_changed(self, value: str) -> None:
        """Pitch segmented control changed → set underlying Pitch."""
        # Map short label back to full engine value
        pitch_map = {"Low": "Low", "Norm": "Normal", "High": "High"}
        self._current_pitch = pitch_map.get(value, value)
        self._update_segmented(self._pitch_seg, value)
        self.pitch_changed.emit(self._current_pitch)

    def _on_expression_changed(self, value: str) -> None:
        """Expression segmented control changed → set underlying Delivery."""
        self._current_delivery = self._EXPRESSION_LABEL_TO_ENGINE.get(value, "Normal")
        self._update_segmented(self._expression_seg, value)
        self.delivery_changed.emit(self._current_delivery)

    def _on_ai_freedom_changed(self, label: str) -> None:
        """AI Freedom level selected → set Temperature / Top P / Top K.

        Implements per-parameter ownership tracking (§14, §25, §30-31).
        Only parameters that actually change are owned by AI Freedom.

        Issue 5 fix: also call _update_segmented to visually highlight the
        selected button immediately (not just on tab switch).
        """
        # Always update the visual state — even for Custom
        self._update_segmented(self._ai_freedom_seg, label)

        if label == self._AI_FREEDOM_CUSTOM:
            # Custom — don't change anything, just reflect current state
            return

        # Find the level
        for lvl_label, temp, top_p, top_k in self._AI_FREEDOM_LEVELS:
            if lvl_label == label:
                # Store previous values for ownership tracking
                # Only store if not already owned (avoid overwriting previous)
                if "temperature" not in self._ai_freedom_ownership:
                    self._ai_freedom_ownership["temperature"] = self._temperature
                if "top_p" not in self._ai_freedom_ownership:
                    self._ai_freedom_ownership["top_p"] = self._top_p
                if "top_k" not in self._ai_freedom_ownership:
                    self._ai_freedom_ownership["top_k"] = self._top_k

                # Set new values
                self._temperature = temp
                self._top_p = top_p
                self._top_k = top_k
                self.parameters_changed.emit()
                return

    def _on_seed_changed(self, text: str) -> None:
        """Seed input changed → set underlying Seed."""
        text = text.strip()
        if not text:
            self._seed = None
        else:
            try:
                self._seed = int(text)
            except ValueError:
                self._seed = None
        self.parameters_changed.emit()

    def _on_allow_sfx_toggled(self) -> None:
        """Allow SFX toggle clicked → set underlying allow_sfx.

        P3.25 (audit SS-H06/M01): this handler previously updated only the
        Friendly view's local state and never emitted parameters_changed —
        so the value never propagated to the Advanced panel, and the
        advanced→friendly sync later reset the toggle to ON. The toggle now
        participates in the normal parameter sync chain
        (friendly → advanced → RightPanel.get_parameters()).
        """
        self._allow_sfx = self._allow_sfx_btn.isChecked()
        self._allow_sfx_btn.setText("ON" if self._allow_sfx else "OFF")
        self._allow_sfx_btn.setStyleSheet(self._toggle_style(self._allow_sfx))
        self.parameters_changed.emit()

    # ==================================================================
    # VISUAL UPDATE — Advanced → Friendly
    # ==================================================================
    def _update_emotion_buttons(self, active_friendly_label=None) -> None:
        """Update emotion grid highlight based on current effective state.
        
        Supports MULTIPLE active buttons (Issue 11-12):
        - An emotion button is active if the current emotion matches.
        - The Whisper button is active if Style=Whispering.
        Both can be active simultaneously.
        """
        # Derive active labels from current state
        active_labels = set()
        
        # Check emotion-based buttons
        for fl, emo_type, engine_value, _ in self.PRIMARY_EMOTIONS:
            if emo_type == "emotion" and engine_value == self._current_emotion:
                active_labels.add(fl)
            if emo_type == "style" and engine_value == self._current_style:
                active_labels.add(fl)
            if emo_type == "none" and self._current_emotion is None and self._current_style is None:
                active_labels.add(fl)
        
        # Also add explicitly passed label (for click feedback)
        if active_friendly_label is not None:
            active_labels.add(active_friendly_label)
        
        # If nothing is active, show Neutral
        if not active_labels and self._current_emotion is None and self._current_style is None:
            active_labels.add("Neutral")
        
        for fl, btn in self._emotion_buttons.items():
            is_active = fl in active_labels
            btn.setChecked(is_active)
            btn.setStyleSheet(self._emotion_btn_style(is_active))

    def _update_style_buttons(self, active_style) -> None:
        """Update style button highlight based on engine style name (or None)."""
        for style_name, btn in self._style_buttons.items():
            is_active = (style_name == active_style)
            btn.setChecked(is_active)
            btn.setStyleSheet(self._segmented_btn_style(is_active))

    def _update_segmented(self, seg_dict, value) -> None:
        """Update segmented control highlight."""
        for opt, btn in seg_dict["buttons"].items():
            is_active = (opt == value)
            btn.setChecked(is_active)
            btn.setStyleSheet(self._segmented_btn_style(is_active))

    def _update_ai_freedom_from_params(self) -> None:
        """Check if current temp/top_p/top_k match a preset → highlight or Custom."""
        for lvl_label, temp, top_p, top_k in self._AI_FREEDOM_LEVELS:
            if (abs(self._temperature - temp) < 0.01 and
                abs(self._top_p - top_p) < 0.01 and
                self._top_k == top_k):
                self._update_segmented(self._ai_freedom_seg, lvl_label)
                return
        # No match → Custom
        self._update_segmented(self._ai_freedom_seg, self._AI_FREEDOM_CUSTOM)

    # ==================================================================
    # PUBLIC API — called by RightPanel for two-way sync
    # ==================================================================
    def get_emotion(self): return self._current_emotion
    def get_style(self): return self._current_style
    def get_speed(self): return self._current_speed
    def get_pitch(self): return self._current_pitch
    def get_delivery(self): return self._current_delivery

    def set_emotion(self, emotion) -> None:
        """Advanced → Friendly: update emotion grid highlight (keeps Style)."""
        self._current_emotion = emotion
        self._update_emotion_buttons()  # Derive from current state

    def set_style(self, style) -> None:
        """Advanced → Friendly: update style buttons + Whisper highlight (keeps Emotion)."""
        self._current_style = style
        self._update_style_buttons(style)
        self._update_emotion_buttons()  # Update Whisper highlight without clearing emotion

    def set_speed(self, value) -> None:
        """Advanced → Friendly: update Pace segmented control."""
        self._current_speed = value
        speed_to_label = {"Very Slow": "V.Slow", "Slow": "Slow", "Normal": "Norm",
                          "Fast": "Fast", "Very Fast": "V.Fast"}
        label = speed_to_label.get(value, "Norm")
        self._update_segmented(self._pace_seg, label)

    def set_pitch(self, value) -> None:
        """Advanced → Friendly: update Pitch segmented control."""
        self._current_pitch = value
        # Map full engine value to short label
        pitch_to_label = {"Low": "Low", "Normal": "Norm", "High": "High"}
        label = pitch_to_label.get(value, value)
        self._update_segmented(self._pitch_seg, label)

    def set_delivery(self, value) -> None:
        """Advanced → Friendly: update Expression segmented control."""
        self._current_delivery = value
        label = self._EXPRESSION_ENGINE_TO_LABEL.get(value, "Natural")
        self._update_segmented(self._expression_seg, label)

    def set_parameters(self, params) -> None:
        """Advanced → Friendly: update generation parameters."""
        self._temperature = params.temperature
        self._top_p = params.top_p
        self._top_k = params.top_k
        self._max_new_tokens = params.max_new_tokens
        self._seed = params.seed
        self._append_silence = params.append_silence
        self._normalize_output = params.normalize_output
        self._auto_play = params.auto_play
        self._allow_sfx = params.allow_sfx
        # Update AI Freedom highlight
        self._update_ai_freedom_from_params()
        # Update Seed input
        if self._seed is not None:
            self._seed_edit.setText(str(self._seed))
        else:
            self._seed_edit.clear()
        # Update Allow SFX toggle
        self._allow_sfx_btn.setChecked(self._allow_sfx)
        self._allow_sfx_btn.setText("ON" if self._allow_sfx else "OFF")
        self._allow_sfx_btn.setStyleSheet(self._toggle_style(self._allow_sfx))

    def get_parameters(self):
        """Return current generation parameters as GenerationParameters."""
        from engine.models import GenerationParameters
        return GenerationParameters(
            temperature=self._temperature,
            top_p=self._top_p,
            top_k=self._top_k,
            max_new_tokens=self._max_new_tokens,
            seed=self._seed,
            append_silence=self._append_silence,
            normalize_output=self._normalize_output,
            auto_play=self._auto_play,
            allow_sfx=self._allow_sfx,
        )

    def set_selected_voice_id(self, voice_id) -> None:
        self._voice_id = voice_id

    def set_voice_info(self, voice) -> None:
        if voice is None:
            self._voice_name = "(No voice)"
            self._voice_name_label.setText("(No voice)")
            self._voice_meta_label.setText("")
            self._set_avatar_text("?")
        else:
            self._voice_name = voice.name
            self._voice_name_label.setText(voice.name)
            meta_parts = []
            if voice.gender:
                meta_parts.append(voice.gender)
            if voice.sample_rate > 0:
                meta_parts.append(f"{voice.sample_rate} Hz")
            if voice.duration > 0:
                meta_parts.append(f"{voice.duration:.1f}s")
            self._voice_meta_label.setText(" · ".join(meta_parts))
            # P3.43: show the profile's real avatar when one resolves
            # (falls back to the initial-letter circle otherwise).
            if not self._set_avatar_pixmap(voice):
                self._set_avatar_text(
                    voice.name[0].upper() if voice.name else "?")

    def _set_avatar_text(self, text: str) -> None:
        self._avatar.setText(text)

    def _set_avatar_pixmap(self, voice) -> bool:
        """Render the profile avatar into the circular 32px card slot.

        Returns True when a real avatar image was found and rendered.
        """
        from ui.widgets.avatar import _preview_candidates, make_rounded_pixmap
        import os
        candidates = _preview_candidates(voice, os.getcwd())
        for path in candidates:
            pm = make_rounded_pixmap(path, 32, voice.name)
            if pm is not None:
                self._avatar.setText("")
                self._avatar.setPixmap(pm)
                return True
        self._avatar.setPixmap(QPixmap())
        return False

    def set_generate_enabled(self, enabled) -> None:
        self._generate_btn.setEnabled(enabled)
