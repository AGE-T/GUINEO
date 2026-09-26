"""
SpeechStudio Status Bar.

UI Specification, section 18:
    Displays: Model, CUDA, GPU Name, VRAM Usage, Generation Time,
    Generation Status, Current Voice.
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QStatusBar, QLabel, QWidget, QHBoxLayout,
)

from ui.theme import Palette
from engine.models import ModelStatus


class StatusDot(QLabel):
    """A small coloured circle used as a status indicator."""

    def __init__(self, state: str = "unknown", parent=None):
        super().__init__(parent)
        self.set_state(state)

    def set_state(self, state: str) -> None:
        colour_map = {
            "ok": Palette.SUCCESS,
            "warn": Palette.WARNING,
            "error": Palette.ERROR,
            "info": Palette.INFO,
            "unknown": Palette.TEXT_DISABLED,
        }
        colour = colour_map.get(state, Palette.TEXT_DISABLED)
        self.setText("\u25cf")
        self.setStyleSheet("color: %s; font-size: 14px;" % colour)


class StatusBar(QStatusBar):
    """Bottom status bar — floating pill style (HTML reference).

    The HTML reference uses a floating bottom bar with:
        - 16px margins from left/right/bottom
        - 12px border radius
        - Semi-transparent dark surface (surface-container-lowest/90)
        - JetBrains Mono font for technical data
        - Subtle top border

    In Qt, QStatusBar is docked to the MainWindow bottom. We apply an
    inline stylesheet to match the visual style as closely as possible.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizeGripEnabled(False)
        self._build_ui()
        self._apply_style()

    def _apply_style(self) -> None:
        """Apply floating pill style to match the HTML reference."""
        self.setStyleSheet("""
            QStatusBar {{
                background-color: rgba(14, 14, 14, 0.9);
                border-top: 1px solid #333333;
                border-radius: 12px;
                color: #A0A0A0;
                font-family: "JetBrains Mono", "Consolas", "Courier New", monospace;
                font-size: 11px;
                padding: 0 16px;
            }}
            QStatusBar::item {{ border: none; }}
        """)

    def _build_ui(self) -> None:
        # Model status
        self._model_dot = StatusDot("unknown")
        self._model_label = QLabel("Model: ...")
        self.addWidget(self._model_dot)
        self.addWidget(self._model_label)
        self.addWidget(self._make_sep())

        # CUDA status
        self._cuda_dot = StatusDot("unknown")
        self._cuda_label = QLabel("CUDA: ...")
        self.addWidget(self._cuda_dot)
        self.addWidget(self._cuda_label)
        self.addWidget(self._make_sep())

        # Generation status
        self._gen_label = QLabel("Ready")
        self.addWidget(self._gen_label)
        self.addWidget(self._make_sep())

        # Current voice
        self._voice_label = QLabel("Voice: None")
        self.addWidget(self._voice_label)

        # Right-aligned: generation time
        self._time_label = QLabel("")
        self.addPermanentWidget(self._time_label)

        # Right-aligned: version (always visible)
        from engine.version import get_version_string
        self._version_label = QLabel(get_version_string())
        self._version_label.setStyleSheet("color: %s;" % Palette.TEXT_DISABLED)
        self.addPermanentWidget(self._make_sep())
        self.addPermanentWidget(self._version_label)

    def _make_sep(self) -> QLabel:
        sep = QLabel("|")
        sep.setStyleSheet("color: %s;" % Palette.BORDER)
        return sep

    # ------------------------------------------------------------------
    # Update methods (called by MainWindow when engine state changes)
    # ------------------------------------------------------------------
    def update_model_status(self, status: ModelStatus) -> None:
        if status.model_loaded:
            self._model_dot.set_state("ok")
            self._model_label.setText("Model: loaded")
        elif status.loading:
            self._model_dot.set_state("info")
            self._model_label.setText("Model: loading...")
        else:
            self._model_dot.set_state("warn")
            self._model_label.setText("Model: not loaded")

        if status.cuda_available:
            self._cuda_dot.set_state("ok")
            gpu = status.gpu_name or "CUDA"
            vram = ""
            if status.vram_total_mb > 0:
                pct = (status.vram_used_mb / status.vram_total_mb) * 100
                vram = " ({0:.0f}%)".format(pct)
            self._cuda_label.setText("CUDA: {0}{1}".format(gpu, vram))
        else:
            self._cuda_dot.set_state("warn")
            self._cuda_label.setText("CUDA: unavailable")

    def update_model_status_simple(self, model_ok: bool, cuda_ok: bool,
                                    gpu_name: str = "") -> None:
        """Simplified update for Phase 1-style status (no Engine required)."""
        if model_ok:
            self._model_dot.set_state("ok")
            self._model_label.setText("Model: ready")
        else:
            self._model_dot.set_state("warn")
            self._model_label.setText("Model: not loaded")

        if cuda_ok:
            self._cuda_dot.set_state("ok")
            self._cuda_label.setText("CUDA: " + (gpu_name or "available"))
        else:
            self._cuda_dot.set_state("warn")
            self._cuda_label.setText("CUDA: unavailable")

    def set_generating(self, active: bool) -> None:
        if active:
            self._gen_label.setText("Generating...")
            self._gen_label.setStyleSheet("color: %s;" % Palette.WARNING)
        else:
            self._gen_label.setText("Ready")
            self._gen_label.setStyleSheet("color: %s;" % Palette.TEXT_SECONDARY)

    def set_generation_time(self, seconds: float, rtf: float = 0.0) -> None:
        if seconds > 0:
            rtf_str = " RTF={0:.2f}".format(rtf) if rtf > 0 else ""
            self._time_label.setText("Gen: {0:.2f}s{1}".format(seconds, rtf_str))
        else:
            self._time_label.setText("")

    def set_current_voice(self, voice_name: str) -> None:
        self._voice_label.setText("Voice: " + (voice_name or "None"))

    def set_sample_rate(self, sample_rate: int) -> None:
        """Update the sample rate display in the status bar.

        The sample rate is appended to the voice label as a suffix.
        To avoid duplication on repeated calls, we rebuild the voice
        label from the stored voice name + the sample rate.
        """
        if sample_rate and sample_rate > 0:
            # Rebuild the voice label from the stored voice name to
            # avoid appending "  |  24000 Hz" multiple times.
            # _voice_label text format: "Voice: <name>" or "Voice: <name>  |  <sr> Hz"
            current_text = self._voice_label.text()
            # Strip any existing "  |  N Hz" suffix
            if "  |  " in current_text and " Hz" in current_text:
                base_text = current_text.rsplit("  |  ", 1)[0]
            else:
                base_text = current_text
            self._voice_label.setText(base_text +
                "  |  {0} Hz".format(sample_rate))

    def set_output_format(self, fmt: str) -> None:
        """Update the output format display in the status bar."""
        if fmt:
            self._time_label.setText(self._time_label.text() +
                "  |  {0}".format(fmt.upper()))
