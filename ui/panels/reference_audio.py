"""GUINEO Reference Audio display panel (P3.43 §18).

This panel is the point-of-use DISPLAY of the currently selected voice
profile's reference audio inside the right panel (Advanced tab):
file name, duration, sample rate, channels, status and the validation
state, plus a read-only transcript VIEW.

P3.43 (Voice Profile Unification): the panel is NO LONGER a second
authoritative voice editor. Transcript and profile metadata editing is
owned exclusively by the single Voice Profile editor
(``ui.panels.voice_import_dialog.VoiceImportDialog``) reached through
the unified Voice Profiles screen — a disconnected legacy editor here
was the source of duplicated persistence logic.

History: the previous implementation also offered Trim / Normalize /
Refresh buttons and an editable transcript whose signals were never
connected anywhere (dead controls, removed per P3.43 §31 "no dead
controls" — transcript editing was wired to a private engine reach-in
which the facade now owns).
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QPlainTextEdit, QFrame,
)

from ui.theme import Palette
from engine.models import VoiceProfile


class ReferenceAudioPanel(QWidget):
    """Read-only reference audio info + transcript view for a voice profile.

    Public API (unchanged shape):
        - update_voice(voice)          — display a profile's reference info
        - set_validation_warnings(list) — show the authoritative validation
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_voice: Optional[VoiceProfile] = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # --- Metadata grid ---
        info_grid = QGridLayout()
        info_grid.setContentsMargins(0, 0, 0, 0)
        info_grid.setHorizontalSpacing(8)
        info_grid.setVerticalSpacing(2)

        self._file_label = QLabel("(no file)")
        self._file_label.setStyleSheet("color: %s;" % Palette.TEXT_PRIMARY)
        self._duration_label = QLabel("--")
        self._sr_label = QLabel("--")
        self._channels_label = QLabel("--")
        self._status_label = QLabel("No voice selected")
        self._status_label.setStyleSheet("color: %s;" % Palette.TEXT_SECONDARY)

        info_grid.addWidget(self._make_caption("File:"), 0, 0)
        info_grid.addWidget(self._file_label, 0, 1)
        info_grid.addWidget(self._make_caption("Duration:"), 1, 0)
        info_grid.addWidget(self._duration_label, 1, 1)
        info_grid.addWidget(self._make_caption("Sample Rate:"), 2, 0)
        info_grid.addWidget(self._sr_label, 2, 1)
        info_grid.addWidget(self._make_caption("Channels:"), 3, 0)
        info_grid.addWidget(self._channels_label, 3, 1)
        info_grid.addWidget(self._make_caption("Status:"), 4, 0)
        info_grid.addWidget(self._status_label, 4, 1)
        info_grid.setColumnStretch(1, 1)
        layout.addLayout(info_grid)

        # --- Transcript (read-only view — editing belongs to the single
        # Voice Profile editor, P3.43 §18) ---
        layout.addWidget(self._make_caption("Reference Transcript:"))
        self._transcript = QPlainTextEdit()
        self._transcript.setReadOnly(True)
        self._transcript.setPlaceholderText(
            "(no transcript)")
        self._transcript.setMaximumHeight(100)
        self._transcript.setStyleSheet(
            "QPlainTextEdit { background-color: transparent; border: none; }")
        layout.addWidget(self._transcript)

        # Editing hint — sends the user to the authoritative editor.
        self._edit_hint = QLabel(
            "Edit this profile in Tools → Voice Profiles…")
        self._edit_hint.setStyleSheet("color: %s; font-size: 11px;" % Palette.TEXT_SECONDARY)
        self._edit_hint.setVisible(False)
        layout.addWidget(self._edit_hint)

    def _make_caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: %s;" % Palette.TEXT_SECONDARY)
        return label

    # ------------------------------------------------------------------
    # Update methods
    # ------------------------------------------------------------------
    def update_voice(self, voice: Optional[VoiceProfile]) -> None:
        """Update the panel with a voice profile's reference audio info.

        Pure display — no signal emission, no persistence (P3.43 §29:
        programmatic refreshes are never mutations).
        """
        self._current_voice = voice

        if voice is None or not voice.has_reference:
            self._file_label.setText("(no reference audio)")
            self._duration_label.setText("--")
            self._sr_label.setText("--")
            self._channels_label.setText("--")
            self._status_label.setText("No reference audio")
            self._status_label.setStyleSheet("color: %s;" % Palette.WARNING)
            self._transcript.setPlainText(
                (voice.reference_transcript if voice else "") or "")
            self._edit_hint.setVisible(voice is not None)
            return

        # Populate metadata
        import os
        fname = os.path.basename(voice.reference_audio_path)
        self._file_label.setText(fname)
        self._file_label.setToolTip(voice.reference_audio_path)
        self._duration_label.setText("{0:.2f} s".format(voice.duration))
        self._sr_label.setText("{0} Hz".format(voice.sample_rate))
        self._channels_label.setText("{0} ({1})".format(
            voice.channels,
            "mono" if voice.channels == 1 else "stereo"))
        self._status_label.setText("Valid")
        self._status_label.setStyleSheet("color: %s;" % Palette.SUCCESS)

        self._transcript.setPlainText(voice.reference_transcript or "")
        self._edit_hint.setVisible(True)

    def set_validation_warnings(self, warnings: list) -> None:
        """Show validation warnings in the status line."""
        if not warnings:
            self._status_label.setText("Valid")
            self._status_label.setStyleSheet("color: %s;" % Palette.SUCCESS)
        else:
            self._status_label.setText("Warnings: {0}".format(len(warnings)))
            self._status_label.setStyleSheet("color: %s;" % Palette.WARNING)
            self._status_label.setToolTip("\n".join(warnings))

    def get_transcript(self) -> str:
        """The displayed transcript text (display copy, read-only)."""
        return self._transcript.toPlainText()
