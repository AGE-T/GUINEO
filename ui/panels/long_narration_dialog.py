"""
SpeechStudio Long Narration Generator Dialog.

Shows a preview of how text will be split into generation-safe parts.
Each part is editable. User can generate all parts via Batch Generation.

Features:
- Editable text parts (QTextEdit per part)
- Block label and estimated duration per part
- Multi-speaker: speaker → voice mapping UI (dropdown from VoiceManager)
- Time estimate
"""

from __future__ import annotations
from typing import List, Optional, Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QWidget, QGroupBox, QTextEdit,
    QFrame, QSizePolicy, QComboBox, QGridLayout, QCheckBox,
)

from ui.theme import Palette
from engine.narration_splitter import SplitPart
from engine.models import VoiceProfile


class LongNarrationDialog(QDialog):
    """Preview and generate long narration from split parts.

    For multi-speaker dialogue ($SPEAKER: syntax), displays a speaker→voice
    mapping section where the user assigns a VoiceProfile to each detected
    speaker via a dropdown — no settings.json editing required.
    """

    generate_requested = Signal(list, dict)  # parts, speaker_voice_map

    def __init__(self, parts: List[SplitPart], voice_name: str = "",
                 voices: Optional[List[VoiceProfile]] = None,
                 default_voice_id: Optional[str] = None,
                 saved_speaker_map: Optional[dict] = None,
                 allow_sfx: bool = True,
                 parent=None):
        super().__init__(parent)
        self._parts = list(parts)
        self._voice_name = voice_name
        self._voices = voices or []
        self._default_voice_id = default_voice_id
        # Previously-saved speaker→voice mapping (loaded from settings.json).
        # Used to pre-select the combo boxes so the user doesn't have to
        # reassign voices every time they open the dialog.
        self._saved_speaker_map = saved_speaker_map or {}
        # P3.25 (audit SS-M02): the Allow SFX policy from the control
        # panel. The initial split already applied it; the dialog's part
        # REBUILD (edit → recompile) must apply the same policy — editing
        # a part previously re-injected <|sfx:...|> tokens that the user
        # had explicitly suppressed.
        self._allow_sfx = bool(allow_sfx)
        self._editors: List[QTextEdit] = []
        # speaker label -> QComboBox (for reading the mapping on generate)
        self._speaker_combos: Dict[str, QComboBox] = {}
        # P3.28 §10 (design record Rec 11): "Review before generating".
        # OFF (default) = today's automatic behaviour (batch window opens
        # and generation starts). ON = the batch window opens in REVIEW
        # MODE: jobs stay pending, per-part checkboxes are visible, and
        # generation starts only when the user presses Generate there.
        # Persisted in settings.json (long_generation.review_before_generate)
        # so the choice survives restarts.
        self._review_checkbox: Optional[QCheckBox] = None
        self._build_ui()

    def review_mode(self) -> bool:
        """P3.28 §10: the current state of "Review before generating"."""
        return bool(self._review_checkbox is not None
                    and self._review_checkbox.isChecked())

    def _build_ui(self) -> None:
        self.setWindowTitle("Long Narration Generator")
        self.setMinimumSize(720, 560)
        self.setStyleSheet("background-color: {0};".format(Palette.BG_BASE))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # --- Detect speakers ---
        speakers = sorted({p.speaker for p in self._parts if p.speaker})
        is_multi = len(speakers) > 0

        # --- Header ---
        total_est = sum(p.estimated_duration for p in self._parts)
        if is_multi:
            header_text = (
                "🎙 Multi-speaker dialogue  |  "
                "{0} parts  |  {1} speakers  |  "
                "Estimated: ~{2:.0f}s").format(
                    len(self._parts), len(speakers), total_est)
        else:
            header_text = (
                "{0} parts will be generated  |  "
                "Estimated total: ~{1:.0f}s  |  "
                "Voice: {2}").format(
                    len(self._parts), total_est,
                    self._voice_name or "(no voice)")
        header = QLabel(header_text)
        header.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: {0};".format(
                Palette.TEXT_PRIMARY))
        header.setWordWrap(True)
        layout.addWidget(header)

        # --- Info label ---
        if is_multi:
            info = QLabel(
                "Each speaker line becomes a separate generation job with the "
                "assigned voice.\nSpeaker switches use the configured "
                "inter-part silence from settings (no crossfade).\n"
                "Edit any part's text below before generating.")
        else:
            info = QLabel(
                "Each part ends with 0.5s silence for natural breathing.\n"
                "Edit any part below before generating.\n"
                "After generation, use Combine Scene (or Concatenate) in the "
                "Batch dialog to merge the parts.")
        info.setStyleSheet("color: {0}; font-size: 11px;".format(
            Palette.TEXT_SECONDARY))
        info.setWordWrap(True)
        layout.addWidget(info)

        # --- Speaker → Voice mapping section (multi-speaker only) ---
        if is_multi:
            mapping_group = QGroupBox("Speaker → Voice Mapping")
            mapping_layout = QGridLayout(mapping_group)
            mapping_layout.setSpacing(6)
            mapping_layout.setContentsMargins(8, 8, 8, 8)

            # Header row
            mapping_layout.addWidget(QLabel("Speaker"), 0, 0)
            mapping_layout.addWidget(QLabel("Voice Profile"), 0, 1)

            for row, spk in enumerate(speakers, start=1):
                lbl = QLabel(spk)
                lbl.setStyleSheet(
                    "font-weight: bold; color: {0};".format(Palette.ACCENT))
                mapping_layout.addWidget(lbl, row, 0)

                combo = QComboBox()
                combo.addItem("(not assigned)", None)
                for v in self._voices:
                    label = v.name
                    if v.duration > 0:
                        label += " ({0:.1f}s)".format(v.duration)
                    combo.addItem(label, v.id)
                # Pre-select the saved voice for this speaker if we have one.
                saved_vid = self._saved_speaker_map.get(spk)
                if saved_vid:
                    for i in range(combo.count()):
                        if combo.itemData(i) == saved_vid:
                            combo.setCurrentIndex(i)
                            break
                elif self._default_voice_id:
                    # Fall back to the default voice from the control panel.
                    for i in range(combo.count()):
                        if combo.itemData(i) == self._default_voice_id:
                            combo.setCurrentIndex(i)
                            break
                combo.setMinimumWidth(250)
                mapping_layout.addWidget(combo, row, 1)
                self._speaker_combos[spk] = combo

            mapping_layout.setColumnStretch(1, 1)
            layout.addWidget(mapping_group)

        # --- Scrollable parts area ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea {{ border: 1px solid {0}; border-radius: 6px; }}".format(
                Palette.BORDER))

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(8, 8, 8, 8)
        scroll_layout.setSpacing(8)

        for i, part in enumerate(self._parts):
            part_frame = self._build_part_frame(i, part)
            scroll_layout.addWidget(part_frame)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        # P3.28 §10: the review-mode checkbox sits immediately left of the
        # Generate button (the natural place to decide "review first?").
        self._review_checkbox = QCheckBox("Review before generating")
        self._review_checkbox.setToolTip(
            "Open the Batch Generation window to review parts and choose "
            "what to generate —\ngeneration starts only when you press "
            "Generate there.\n\nWhen unchecked, generation starts "
            "automatically (current behaviour).")
        # Restore the persisted preference (default OFF — zero surprise
        # for existing users).
        try:
            import os as _os
            from engine.settings_manager import SettingsManager
            _root = _os.path.dirname(_os.path.dirname(_os.path.dirname(
                _os.path.abspath(__file__))))
            _sm = SettingsManager.instance(_os.path.join(_root, "settings"))
            self._review_checkbox.setChecked(bool(
                _sm.get("long_generation", "review_before_generate", False)))
            self._review_checkbox.toggled.connect(lambda checked: (
                SettingsManager.instance(_os.path.join(_root, "settings"))
                .set("long_generation", "review_before_generate", checked)))
        except Exception:
            pass
        btn_row.addWidget(self._review_checkbox)
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        # Generate button — uses the SAME visual treatment as the main
        # SpeechStudio primary Generate CTA in toolbar.py (orange gradient,
        # large button, same border radius, typography, hover/pressed/
        # disabled behaviour). We reuse the identical inline style so the
        # Long Narration Generate button immediately looks like the same
        # Generate action used elsewhere in SpeechStudio.
        #
        # Only the visual presentation is shared — the functionality
        # (BatchJob creation, prompt compilation, generation parameters,
        # part editing, voice selection, concatenation, generation flow)
        # is unchanged. See _on_generate for the validated generation path.
        generate_btn = QPushButton("Generate")
        generate_btn.setMinimumWidth(100)
        font = generate_btn.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        generate_btn.setFont(font)
        generate_btn.setStyleSheet(self._cta_button_style())
        generate_btn.clicked.connect(self._on_generate)
        btn_row.addWidget(generate_btn)
        layout.addLayout(btn_row)

    @staticmethod
    def _cta_button_style() -> str:
        """Return the orange CTA button stylesheet.

        This is the SAME style used by toolbar.Toolbar._apply_generate_style
        for the main SpeechStudio Generate button. It is duplicated here
        (rather than imported) to avoid coupling the dialog to the toolbar
        module — but the visual values are identical so the two buttons
        look the same.

        If the main CTA style changes, this method should be updated to
        match.
        """
        return (
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

    # ------------------------------------------------------------------
    # Effective token state display
    # ------------------------------------------------------------------
    # Category colour coding for the part header token display.
    # Each category uses one consistent colour family so the user can
    # instantly identify which kind of token is active.
    #
    # These colours are display-only — they do NOT affect prompt
    # compilation or the BatchJob. The displayed values come from the
    # SAME eff_* fields on SplitPart that are used by
    # CanonicalPromptCompiler.compile_for_batch_part to build the
    # actual generation prompt.
    #
    #   PART HEADER TOKEN DISPLAY
    #   == EFFECTIVE GENERATION STATE
    #   == STATE USED TO COMPILE BatchJob.prompt
    #
    _TOKEN_COLORS = {
        "emotion":  "#f87171",  # warm red/coral
        "style":    "#c084fc",  # purple
        "speed":    "#14b8a6",  # teal
        "pitch":    "#fbbf24",  # amber/gold
        "delivery": "#4ade80",  # green
    }

    def _build_token_display_html(self, part: SplitPart) -> str:
        """Build compact HTML showing the effective token state for a part.

        Returns an HTML string with coloured spans, or "" if no tokens
        are active.

        The values are the SAME effective semantic state used to compile
        the BatchJob prompt (part.eff_emotion, eff_style, eff_speed,
        eff_pitch, eff_delivery). This is DISPLAY ONLY — it does not
        modify part.text, part.prompt, or any semantic state.

        Display rules:
        - Emotion: shown if eff_emotion is not None.
        - Style: shown if eff_style is not None.
        - Speed/Pitch/Delivery: shown only if the effective value is not
          "Normal" (the default). This keeps the header compact —
          "Normal" prosody values are implied and add noise.
        - Values are uppercased for compactness.
        - No raw HIGGS syntax (e.g. <|emotion:fear|>) is displayed —
          only human-readable semantic values.
        """
        spans = []

        # Emotion
        if part.eff_emotion:
            spans.append(self._token_span("emotion", part.eff_emotion))

        # Style
        if part.eff_style:
            spans.append(self._token_span("style", part.eff_style))

        # Speed — skip "Normal" (default, adds noise)
        if part.eff_speed and part.eff_speed != "Normal":
            spans.append(self._token_span("speed", part.eff_speed))

        # Pitch — skip "Normal"
        if part.eff_pitch and part.eff_pitch != "Normal":
            spans.append(self._token_span("pitch", part.eff_pitch))

        # Delivery — skip "Normal"
        if part.eff_delivery and part.eff_delivery != "Normal":
            spans.append(self._token_span("delivery", part.eff_delivery))

        return " ".join(spans)

    @classmethod
    def _token_span(cls, category: str, value: str) -> str:
        """Build a single coloured HTML span for a token value.

        Uses compact styling — no padding, no border, just coloured
        uppercase text. This keeps the part header subtle and compact.
        """
        color = cls._TOKEN_COLORS.get(category, "#e6e8f2")
        # Escape HTML special characters in the value (safety).
        safe_value = (value.replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;"))
        return ('<span style="color:{0}; font-weight:bold;">{1}</span>'
                .format(color, safe_value.upper()))

    def _build_part_frame(self, index: int, part: SplitPart) -> QFrame:
        """Build an editable frame for one part."""
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame {{ background-color: {0}; border: 1px solid {1}; "
            "border-radius: 6px; }}".format(Palette.BG_SURFACE, Palette.BORDER))

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Part header — includes the effective token state display.
        # The token display is derived from the SAME effective semantic
        # state used to compile the BatchJob prompt (eff_emotion, eff_style,
        # eff_speed, eff_pitch, eff_delivery on SplitPart). It is DISPLAY
        # ONLY — it does not modify part.text, part.prompt, or the semantic
        # state. See _build_token_display_html for details.
        header_text = "Part {0}".format(index + 1)
        if part.speaker:
            header_text += "  |  🎙 {0}".format(part.speaker)
        elif part.block_label:
            header_text += "  |  {0}".format(part.block_label)
        header_text += "  |  ~{0:.0f}s".format(part.estimated_duration)

        # Effective token display (compact, colored, human-readable).
        token_html = self._build_token_display_html(part)
        if token_html:
            header_text += '  |  {0}'.format(token_html)

        header_text += "  |  {0} chars".format(part.char_count)

        header = QLabel(header_text)
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setStyleSheet(
            "font-weight: bold; color: {0}; font-size: 11px;".format(
                Palette.ACCENT))
        layout.addWidget(header)

        # Editable text — shows the PLAIN TEXT (not the tokenized prompt).
        # The user edits dialogue text, not Higgs tokens. The tokenized
        # prompt is rebuilt from the edited text + effective block settings
        # when Generate is clicked.
        editor = QTextEdit()
        editor.setPlainText(part.text)  # plain text, NOT part.prompt
        editor.setFont(QFont("Consolas", 10))
        editor.setMinimumHeight(60)
        editor.setMaximumHeight(120)
        editor.setStyleSheet(
            "QTextEdit {{ background-color: {0}; color: {1}; "
            "border: 1px solid {2}; border-radius: 4px; padding: 4px; }}".format(
                Palette.BG_SURFACE_ALT, Palette.TEXT_PRIMARY, Palette.BORDER))
        layout.addWidget(editor)
        self._editors.append(editor)

        # Silence indicator
        silence_label = QLabel("+ 0.5s silence (automatic)")
        silence_label.setStyleSheet(
            "color: {0}; font-size: 10px;".format(Palette.TEXT_SECONDARY))
        layout.addWidget(silence_label)

        return frame

    def _get_speaker_voice_map(self) -> dict:
        """Read the speaker→voice_id mapping from the combo boxes.

        Returns a dict {speaker_label: voice_id}. Raises ValueError if any
        speaker has no voice assigned (combo at index 0 = "(not assigned)").
        """
        mapping = {}
        for spk, combo in self._speaker_combos.items():
            voice_id = combo.currentData()
            if voice_id is None:
                raise ValueError(
                    "Speaker '{0}' has no voice profile assigned. "
                    "Please select a voice for each speaker.".format(spk))
            mapping[spk] = voice_id
        return mapping

    def _on_generate(self) -> None:
        """Collect edited parts and the speaker mapping, emit signal.

        ARCHITECTURE (ADR 003):
        - MANAGED MODE (is_raw=False): the prompt is rebuilt via
          CanonicalPromptCompiler.compile_for_batch_part() — the single
          canonical entry point. No direct PromptBuilder usage, no
          ``<|`` shortcut. SpeechStudio owns the prompt.
        - RAW MODE (is_raw=True): the edited text IS the prompt — used
          as-is, no rebuild, no compiler. The user owns the prompt
          (ADR 003 §8).

        This method does NOT contain a hybrid "detect <| in text and
        bypass the compiler" shortcut. That pattern was a second
        semantic prompt system and has been removed. If the user wants
        literal Higgs tokens, they must use Raw Mode.
        """
        # Validate speaker mapping first.
        speaker_voice_map = {}
        if self._speaker_combos:
            try:
                speaker_voice_map = self._get_speaker_voice_map()
            except ValueError as exc:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Speaker Mapping Error",
                                     str(exc))
                return

        from engine.prompt_state import CanonicalPromptCompiler
        parts = []
        for i, editor in enumerate(self._editors):
            text = editor.toPlainText().strip()
            if text:
                p = self._parts[i]
                p.text = text

                if p.is_raw:
                    # RAW MODE: user owns the prompt. Use the edited
                    # text as-is — no compiler, no token manipulation.
                    # ADR 003 §8: no silent rewriting, no global prepend.
                    p.prompt = text
                else:
                    # MANAGED MODE: rebuild via the canonical compiler.
                    # The effective values were resolved by
                    # NarrationSplitter from block overrides + global
                    # defaults and stored on the SplitPart.
                    eff_emotion = getattr(p, 'eff_emotion', None)
                    eff_style = getattr(p, 'eff_style', None)
                    eff_speed = getattr(p, 'eff_speed', None)
                    eff_pitch = getattr(p, 'eff_pitch', None)
                    eff_delivery = getattr(p, 'eff_delivery', None)
                    try:
                        prompt_data = CanonicalPromptCompiler.compile_for_batch_part(
                            text=text,
                            global_emotion=eff_emotion,
                            global_style=eff_style,
                            global_speed=eff_speed,
                            global_pitch=eff_pitch,
                            global_delivery=eff_delivery,
                            # P3.25 (SS-M02): apply the SAME Allow SFX
                            # policy the initial split applied — the edit
                            # rebuild must not resurrect suppressed tokens.
                            allow_sfx=self._allow_sfx,
                        )
                        p.prompt = prompt_data.final_prompt
                    except Exception:
                        p.prompt = text
                p.char_count = len(p.prompt)
                parts.append(p)

        if not parts:
            return

        # Log each part's final prompt before emitting — for debugging.
        import logging
        log = logging.getLogger("speechstudio.ui.long_narration")
        for i, p in enumerate(parts):
            log.info(
                "BatchJob %d: speaker=%s, voice=%s, raw=%s, prompt=%s",
                i, p.speaker,
                speaker_voice_map.get(p.speaker, "(default)"),
                p.is_raw,
                p.prompt[:120],
            )

        self.generate_requested.emit(parts, speaker_voice_map)
        self.accept()
