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
from engine.duration_estimation import estimate_speech_seconds


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
                 parent=None,
                 generation_parameters=None):
        super().__init__(parent)
        self._parts = list(parts)
        self._voice_name = voice_name
        # P3.45.2A — the EFFECTIVE generation parameters for the upcoming
        # request (passed by MainWindow; the max_new_tokens budget drives
        # the single-output ceiling). None = the configured dataclass
        # default (backwards-compatible with older callers/tests — the
        # preflight itself resolves the default through the single source
        # in engine/output_guard).
        self._generation_parameters = generation_parameters
        # P3.45.2A — per-part preflight verdicts (filled in _build_ui;
        # safe default so _build_part_frame can never hit a missing
        # attribute even if the preflight import fails).
        self._preflight_verdicts: list = []
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
        # P3.45.2B — live estimate refresh: the per-part header labels
        # and the aggregate header are rebuilt when the user edits a
        # part (the split-time numbers were previously frozen at dialog
        # construction — a proven stale surface).
        self._part_headers: List[QLabel] = []
        self._total_header: Optional[QLabel] = None
        self._header_is_multi = False
        self._header_speaker_count = 0
        # P3.45.2A limit note (kept for live re-evaluation on edit).
        self._limit_note: Optional[QLabel] = None
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
        # P3.45.2B: the aggregate estimate is the sum of the CURRENT
        # part estimates (canonical estimator); it is refreshed live
        # while the user edits (see _on_part_text_changed).
        total_est = sum(estimate_speech_seconds(p.text) for p in self._parts)
        self._header_is_multi = is_multi
        self._header_speaker_count = len(speakers)
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
        self._total_header = header
        header.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: {0};".format(
                Palette.TEXT_PRIMARY))
        header.setWordWrap(True)
        layout.addWidget(header)

        # P3.45.2A — per-part oversized-request markers. Every part is
        # classified against the single-output ceiling through the ONE
        # source (engine/output_guard.preflight_generation_size) using
        # the split-time part text — the SAME basis as the "~Xs" shown
        # next to it (post-edit text is re-checked on Generate). Parts
        # that cannot fit one output are visibly marked BEFORE the user
        # commits; the effective limit line appears only when needed.
        try:
            from engine.output_guard import (
                preflight_generation_size, PREFLIGHT_WARNING,
                PREFLIGHT_BLOCKED,
            )
            self._preflight_verdicts = [
                preflight_generation_size(
                    text=(p.text or ""),
                    max_new_tokens=(self._generation_parameters.max_new_tokens
                                    if self._generation_parameters is not None
                                    else None),
                )
                for p in self._parts
            ]
            flagged = [v for v in self._preflight_verdicts
                       if v["state"] in (PREFLIGHT_WARNING,
                                          PREFLIGHT_BLOCKED)]
        except Exception:
            self._preflight_verdicts = []
            flagged = []
        if flagged:
            limit = flagged[0].get("maximum_output_seconds")
            tokens = flagged[0].get("effective_max_new_tokens")
            limit_note = QLabel(
                "Single-output limit: ~{0:.0f}s (max_new_tokens={1}) — "
                "parts marked ⚠ exceed it.".format(limit or 0, tokens or "?"))
            limit_note.setStyleSheet(
                "color: {0}; font-size: 11px;".format(Palette.WARNING))
            limit_note.setWordWrap(True)
            layout.addWidget(limit_note)
            self._limit_note = limit_note

        # --- Info label ---
        if is_multi:
            info = QLabel(
                "Each speaker turn becomes one or more generation jobs "
                "(long turns are split at sentence boundaries) with the "
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
        #
        # P3.45.2B — the "~Xs" estimate and the char count describe the
        # CURRENT editor text on ONE basis (the canonical plain-text
        # estimator): both numbers are rebuilt live while the user edits
        # (_on_part_text_changed), so no stale value is ever shown next
        # to text the user just changed.
        header = QLabel(self._part_header_text(index, part))
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setStyleSheet(
            "font-weight: bold; color: {0}; font-size: 11px;".format(
                Palette.ACCENT))
        layout.addWidget(header)
        self._part_headers.append(header)

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
        # P3.45.2B — LIVE ESTIMATE REFRESH: rebuild this part's header
        # (estimate + chars + oversized marker) and the aggregate header
        # whenever the user edits the text. The split-time numbers were
        # previously frozen at construction — the header kept showing
        # the pre-edit estimate next to text the user had just changed,
        # and the stale value silently flowed into
        # BatchJob.expected_duration at Generate time.
        editor.textChanged.connect(
            lambda i=index: self._on_part_text_changed(i))

        # Silence indicator
        silence_label = QLabel("+ 0.5s silence (automatic)")
        silence_label.setStyleSheet(
            "color: {0}; font-size: 10px;".format(Palette.TEXT_SECONDARY))
        layout.addWidget(silence_label)

        return frame

    # ------------------------------------------------------------------
    # P3.45.2B — live header construction / refresh
    # ------------------------------------------------------------------
    def _part_header_text(self, index: int, part: SplitPart,
                          live_text: Optional[str] = None) -> str:
        """Build one part's header line (rich text).

        The "~Xs" estimate and the char count are computed from
        ``live_text`` (the CURRENT editor content; defaults to the
        part's split-time text) through the canonical estimator, so the
        header always describes the text the user actually sees — and
        the "N chars" basis is the SAME plain-text basis as the "~Xs"
        (the historical display mixed prompt-chars with a text-based
        estimate in one line).

        The P3.45.2A oversized marker is re-classified for the same
        live text (the same pure function and basis the Generate-click
        re-check uses), keeping header markers truthful after edits.
        """
        text = live_text if live_text is not None else (part.text or "")
        est = estimate_speech_seconds(text)

        header_text = "Part {0}".format(index + 1)
        if part.speaker:
            header_text += "  |  🎙 {0}".format(part.speaker)
        elif part.block_label:
            header_text += "  |  {0}".format(part.block_label)
        header_text += "  |  ~{0:.0f}s".format(est)

        # Effective token display (compact, colored, human-readable) —
        # derived from the part's resolved semantic state (unchanged
        # by text edits; the state is re-applied at prompt rebuild).
        token_html = self._build_token_display_html(part)
        if token_html:
            header_text += '  |  {0}'.format(token_html)

        header_text += "  |  {0} chars".format(len(text))

        # P3.45.2A oversized-part marker — same basis as the "~Xs"
        # above (the live text). When called at build time the verdict
        # is the split-time classification (identical basis); when
        # called from the live refresh the verdict is re-classified
        # from the SAME pure function the Generate-click re-check uses.
        verdict = (self._preflight_verdicts[index]
                   if index < len(self._preflight_verdicts) else None)
        if verdict and verdict.get("state") in ("warning", "blocked"):
            limit = verdict.get("maximum_output_seconds") or 0.0
            vest = verdict.get("estimated_request_seconds") or 0.0
            if verdict.get("state") == "warning":
                marker = ('<span style="color:{0}; font-weight:bold;">'
                          "⚠ ~{1:.0f}s — exactly at the ~{2:.0f}s "
                          "single-output limit</span>").format(
                              Palette.WARNING, vest, limit)
            else:
                marker = ('<span style="color:{0}; font-weight:bold;">'
                          "⚠ ~{1:.0f}s exceeds the ~{2:.0f}s single-output "
                          "limit</span>").format(
                              Palette.WARNING, vest, limit)
            header_text += '  |  {0}'.format(marker)
        return header_text

    def _on_part_text_changed(self, index: int) -> None:
        """P3.45.2B live refresh for one edited part.

        Rebuilds (a) the part's header — estimate, char count and the
        oversized marker all on the CURRENT text basis, (b) the
        aggregate header total, (c) the P3.45.2A limit note visibility.
        Never mutates part.text/prompt/char_count (that happens at
        Generate — the emitted values are recomputed there from the
        same canonical estimator, so what the user saw is what flows
        into BatchJob.expected_duration).
        """
        if not (0 <= index < len(self._editors)):
            return
        text = self._editors[index].toPlainText()
        part = self._parts[index]

        # Re-classify the ceiling verdict for the CURRENT text (the
        # same pure function + basis as the Generate-click re-check;
        # import/exception failure keeps the previous verdict — the
        # Generate re-check remains the truth gate).
        try:
            from engine.output_guard import preflight_generation_size
            verdict = preflight_generation_size(
                text=text,
                max_new_tokens=(self._generation_parameters.max_new_tokens
                                if self._generation_parameters is not None
                                else None),
            )
            if index < len(self._preflight_verdicts):
                self._preflight_verdicts[index] = verdict
            else:
                self._preflight_verdicts.append(verdict)
        except Exception:
            pass

        if index < len(self._part_headers):
            self._part_headers[index].setText(
                self._part_header_text(index, part, live_text=text))

        self._refresh_aggregate_header()

    def _refresh_aggregate_header(self) -> None:
        """Rebuild the top aggregate header + limit note from the
        CURRENT editor texts (P3.45.2B — the total was previously
        frozen at dialog construction)."""
        if self._total_header is not None:
            total_est = sum(
                (estimate_speech_seconds(e.toPlainText())
                 for e in self._editors),
            ) if self._editors else 0.0
            if self._header_is_multi:
                header_text = (
                    "🎙 Multi-speaker dialogue  |  "
                    "{0} parts  |  {1} speakers  |  "
                    "Estimated: ~{2:.0f}s").format(
                        len(self._parts), self._header_speaker_count,
                        total_est)
            else:
                header_text = (
                    "{0} parts will be generated  |  "
                    "Estimated total: ~{1:.0f}s  |  "
                    "Voice: {2}").format(
                        len(self._parts), total_est,
                        self._voice_name or "(no voice)")
            self._total_header.setText(header_text)

        # Limit note visibility: shown while ANY current verdict is
        # flagged, hidden otherwise (the split-time note previously
        # stayed visible even after every flagged part was shrunk).
        flagged = [v for v in self._preflight_verdicts
                   if v and v.get("state") in ("warning", "blocked")]
        if self._limit_note is not None:
            if flagged:
                limit = flagged[0].get("maximum_output_seconds")
                tokens = flagged[0].get("effective_max_new_tokens")
                self._limit_note.setText(
                    "Single-output limit: ~{0:.0f}s (max_new_tokens={1}) — "
                    "parts marked ⚠ exceed it.".format(limit or 0,
                                                        tokens or "?"))
                self._limit_note.show()
            else:
                self._limit_note.hide()

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
                # P3.45.2B — recompute the part's estimate from the
                # EDITED text through the canonical estimator: this is
                # the value MainWindow copies into
                # BatchJob.expected_duration (the output guard R2
                # comparison basis). Previously the SPLIT-TIME estimate
                # of the PRE-EDIT text was emitted — silently stale.
                p.estimated_duration = estimate_speech_seconds(text)
                parts.append(p)

        if not parts:
            return

        # P3.45.2A — Generate-click preflight on the EDITED text (the
        # header markers reflect split-time text; this re-check closes
        # the edit gap truthfully: the basis is what will actually be
        # sent). One confirmation listing the affected parts; declining
        # keeps the dialog open — nothing was emitted, nothing mutated.
        # Warning-only parts are already informed via the header markers
        # and do not raise a modal (documented P3.45.2A residual).
        try:
            from engine.output_guard import (
                preflight_generation_size, preflight_summary_line,
                PREFLIGHT_BLOCKED,
            )
            flagged = []
            any_blocked = False
            for p in parts:
                verdict = preflight_generation_size(
                    text=(p.text or ""),
                    max_new_tokens=(
                        self._generation_parameters.max_new_tokens
                        if self._generation_parameters is not None
                        else None),
                )
                if verdict["state"] == PREFLIGHT_BLOCKED:
                    any_blocked = True
                    # Identity lookup — SplitPart is a dataclass (value
                    # equality), duplicate parts must not mislabel.
                    num = next((i + 1 for i, orig in enumerate(self._parts)
                                if orig is p), None)
                    label = "Part {0}".format(num) if num else "Part"
                    if p.speaker:
                        label = "{0} (🎙 {1})".format(label, p.speaker)
                    line = preflight_summary_line(verdict, label)
                    if line:
                        flagged.append(line)
            if any_blocked:
                from PySide6.QtWidgets import QMessageBox
                reply = QMessageBox.question(
                    self, "Oversized Parts",
                    "{0} of {1} parts exceed the single-output generation "
                    "limit:\n\n  {2}\n\n"
                    "A single generation cannot produce more audio than "
                    "the token budget allows — affected outputs would be "
                    "cut at the limit, likely mid-speech.\n\n"
                    "You can cancel and edit the affected parts below, or "
                    "use smaller parts.\n\nGenerate anyway?".format(
                        len(flagged), len(parts), "\n  ".join(flagged)),
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return
        except Exception:
            pass

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
