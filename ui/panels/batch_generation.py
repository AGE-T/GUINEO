"""
SpeechStudio Batch Generation Dialog.

Provides:
    - JobEditDialog   : modal dialog for editing a single BatchJob.
    - BatchGenerationDialog : non-modal dialog that drives the
                              BatchManager and shows a live job table.

Architecture:
    The dialog is a thin Qt view over the engine's BatchManager.  All
    queueing, retry, and inter-job pacing happens in the BatchManager;
    the dialog only:
        - reads ``manager.jobs`` to refresh the table,
        - calls ``manager.start()`` / ``pause()`` / ``stop()`` on button clicks,
        - connects ``manager.set_on_changed`` to ``self._refresh_table``,
        - wires the marshal_to_ui callback to ``QTimer.singleShot(0, fn)``.
"""

from __future__ import annotations
import os
from typing import Optional, List

from PySide6.QtCore import (
    Qt, QTimer, Signal, QObject, QSize, QPoint, QPointF, QRectF,
)
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush, QIcon, QPixmap,
)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QLabel,
    QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox, QAbstractItemView,
    QSizePolicy, QDialogButtonBox, QWidget, QProgressBar, QFrame,
    QApplication, QMenu, QWidgetAction,
)

from ui.theme import Palette
from ui.icon_registry import IconRegistry
from ui.feedback import FeedbackDialog
from engine.models import GenerationParameters, VoiceProfile
from engine.batch_manager import BatchJob, BatchManager, JobStatus


def _safe_int(value) -> int:
    """Tolerant int coercion for model-supplied version numbers."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _fit_button_height(btn: QPushButton, minimum: int = 24) -> None:
    """P3.35 §29/§30: metrics-aware compact button height.

    The application's global QPushButton stylesheet pads 7px vertically
    + a 1px border; a hardcoded 24px widget is TALLER-requested than its
    content box, so the label's bottom was clipped (the reported "None"
    button). Deriving the height from the widget's own sizeHint (which
    already includes the stylesheet padding + font metrics) keeps the
    compact look while guaranteeing the full glyph box fits and stays
    vertically centered at any font/DPI.
    """
    try:
        hint = btn.sizeHint().height()
    except Exception:
        hint = minimum
    btn.setFixedHeight(max(minimum, hint))


def _popup_menu_above(menu, btn):
    """P3.30(b/c): execute ``menu`` synchronously, anchored ABOVE ``btn``.

    The previous ``menu.exec()`` (no position) opened the menu at the
    cursor — on Windows it repeatedly appeared frameless at the screen's
    top-left corner. Anchoring to the button is deterministic: the menu
    opens just above the button (there is ample space in the sidebar),
    falling back to just below when the screen edge doesn't allow above.
    Returns the chosen action (or None).
    """
    menu.adjustSize()
    size = menu.sizeHint()
    btn_top_left = btn.mapToGlobal(QPoint(0, 0))
    pos = QPoint(btn_top_left.x(),
                 btn_top_left.y() - size.height() - 6)
    screen = btn.screen()
    if screen is not None:
        avail = screen.availableGeometry()
        if pos.y() < avail.top():
            pos.setY(btn_top_left.y() + btn.height() + 6)
        if pos.x() + size.width() > avail.right() + 1:
            pos.setX(max(avail.left(), avail.right() + 1 - size.width()))
    return menu.exec(pos)


# ---------------------------------------------------------------------------
# P3.44 — version popover row (§4: Play preview + Use selection)
# ---------------------------------------------------------------------------
class _VersionMenuRow(QWidget):
    """One version row inside the lazy version popover (P3.44 §4).

    Layout:  ``v01 · 9.2s · filename.wav   [▶]  [Use]``

    Play is a READ-ONLY preview: it emits ``play_clicked`` with THIS
    version's own output path (never the resolved/default version —
    §3 "do not silently play a different version") and NEVER modifies
    Scene state. Use is the provenance selection mutation: it emits
    ``use_clicked`` with the version's asset id and never plays. The two
    operations are strictly separate (§4).

    Missing file (§5): Play is disabled with an explicit "file missing"
    tooltip and the label carries a muted ``⚠ file missing`` marker — a
    clear unavailable state; no crash, no silent fallback to another
    version. The version REMAINS listed (provenance is preserved).
    """

    play_clicked = Signal(str)   # absolute output path of THIS version
    use_clicked = Signal(str)    # asset id of THIS version

    def __init__(self, entry: dict, is_current: bool, abs_path: str,
                 file_exists: bool, parent=None):
        super().__init__(parent)
        self._asset_id = str(entry.get("id") or "")
        self._abs_path = abs_path or ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 8, 4)
        layout.setSpacing(8)
        self.setStyleSheet(
            "QWidget {{ background-color: transparent; }}"
            "QLabel {{ color: {text}; border: none; font-size: 12px; }}".format(
                text=Palette.TEXT_PRIMARY))

        try:
            v = int(entry.get("part_version") or 0)
        except (TypeError, ValueError):
            v = 0
        label_text = "v{0:02d} \u00b7 {1:.1f}s \u00b7 {2}".format(
            v, float(entry.get("duration") or 0.0),
            os.path.basename(str(entry.get("output_path") or "")))
        if is_current:
            label_text += "   \u2713 in use"
        if not file_exists:
            label_text += "   \u26a0 file missing"
        label = QLabel(label_text)
        label.setToolTip(
            "Version v{0:02d} of this part.".format(v))
        layout.addWidget(label, 1)

        play_btn = QPushButton()
        play_btn.setIcon(IconRegistry.icon(
            "play_arrow", size=18, color=Palette.ACCENT_TEXT))
        play_btn.setIconSize(QSize(18, 18))
        _fit_button_height(play_btn)
        play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        play_btn.setStyleSheet(
            "QPushButton {{ background-color: {bg}; color: {text};"
            " border: 1px solid {border}; border-radius: 4px;"
            " padding: 2px 6px; }}"
            "QPushButton:hover {{ background-color: {accent};"
            " border-color: {accent}; }}"
            "QPushButton:disabled {{ color: {disabled}; }}".format(
                bg=Palette.BG_RAISED, text=Palette.TEXT_PRIMARY,
                border=Palette.BORDER, accent=Palette.ACCENT,
                disabled=Palette.TEXT_DISABLED))
        if file_exists:
            play_btn.setToolTip(
                "Preview this version (read-only \u2014 does not change\n"
                "the Scene's version selection).")
        else:
            play_btn.setToolTip(
                "Audio file missing:\n{0}\n\n"
                "The version stays listed; it cannot be previewed\n"
                "until the file is restored or the part is regenerated.".format(
                    self._abs_path))
        play_btn.setEnabled(bool(file_exists))
        play_btn.clicked.connect(self._emit_play)
        layout.addWidget(play_btn)

        use_btn = QPushButton("Use")
        use_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _fit_button_height(use_btn)
        use_btn.setToolTip(
            "Select this version for the Scene (feeds Combine Scene\n"
            "and Play). Play stays available for previewing first.")
        use_btn.setStyleSheet(
            "QPushButton {{ background-color: transparent;"
            " color: {accent}; border: 1px solid {accent};"
            " border-radius: 4px; padding: 2px 10px; font-size: 12px;"
            " font-weight: 600; }}"
            "QPushButton:hover {{ background-color: {hover}; }}".format(
                accent=Palette.ACCENT, hover=Palette.ACCENT_HOVER))
        use_btn.clicked.connect(self._emit_use)
        layout.addWidget(use_btn)

    def _emit_play(self) -> None:
        if self._abs_path:
            self.play_clicked.emit(self._abs_path)

    def _emit_use(self) -> None:
        self.use_clicked.emit(self._asset_id)


# ---------------------------------------------------------------------------
# P3.44 — per-row action band (§1/§7/§8: in-place state updates)
# ---------------------------------------------------------------------------
class _RowActions(QWidget):
    """The per-row Play + Stop + Regen button band (P3.44).

    Encapsulates what ``_build_action_widget`` built ad-hoc so refreshes
    can UPDATE the band in place instead of destroying it (§7/§8: no
    widget destruction \u2192 no focus loss / selection loss when a job's
    status changes) and so Play resolves the authoritative playback path
    at CLICK time (§3: a version selection made moments ago is honoured
    without waiting for a table refresh).

    Playback resolution (§3/§9/§11/§13 — delegated to the dialog):
      * Scene mode: the Scene provenance resolver (explicit selection,
        else latest) \u2014 NEVER BatchJob.output_path. A row is playable
        whenever the slot has a successful asset, even when the job in
        this queue run is PENDING (queue execution state and generated
        asset state are separate concepts).
      * Manual mode: BatchJob.output_path gated on COMPLETED (manual
        jobs have no Scene provenance).
    """

    def __init__(self, dialog: "BatchGenerationDialog", job: BatchJob,
                 idx: int, running: bool, parent=None):
        super().__init__(parent)
        self._dialog = dialog
        self._job = job
        self._idx = idx

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        # P3.40: explicit vertical centring of the button band. The cell
        # widget spans the item content rect (48 - 1 = 47px); the fixed
        # 4+36+4 = 44px band is centred with ≈1.5px of extra slack on each
        # side — never top-anchored, never clipped at the bottom.
        layout.setAlignment(Qt.Alignment.AlignVCenter)

        btn_style = (
            "QPushButton {{"
            "  background-color: {bg_raised};"
            "  color: {text_primary};"
            "  border: 1px solid {border};"
            "  border-radius: 4px;"
            "  padding: 1px;"
            "}}"
            "QPushButton:hover {{"
            "  background-color: {accent};"
            "  color: {accent_text};"
            "  border: 1px solid {accent};"
            "}}"
            "QPushButton:disabled {{"
            "  background-color: {bg_surface};"
            "  color: {text_disabled};"
            "  border: 1px solid {border};"
            "}}".format(
                bg_raised=Palette.BG_RAISED,
                text_primary=Palette.TEXT_PRIMARY,
                border=Palette.BORDER,
                accent=Palette.ACCENT,
                accent_text=Palette.ACCENT_TEXT,
                bg_surface=Palette.BG_SURFACE,
                text_disabled=Palette.TEXT_DISABLED))

        # Play button — plays the AUTHORITATIVE audio for the row.
        self._play_btn = QPushButton()
        self._play_btn.setToolTip("Play this part's audio")
        self._play_btn.setFixedSize(36, 36)
        self._play_btn.setIcon(IconRegistry.icon(
            "play_arrow", size=36, color=Palette.TEXT_PRIMARY))
        self._play_btn.setIconSize(QSize(32, 32))
        self._play_btn.setStyleSheet(btn_style)
        self._play_btn.clicked.connect(self._on_play_clicked)
        layout.addWidget(self._play_btn)

        # Stop playback button — stops any currently playing audio.
        stop_play_btn = QPushButton()
        stop_play_btn.setToolTip("Stop playback")
        stop_play_btn.setFixedSize(36, 36)
        stop_play_btn.setIcon(IconRegistry.icon(
            "stop", size=36, color=Palette.TEXT_PRIMARY))
        stop_play_btn.setIconSize(QSize(32, 32))
        stop_play_btn.setStyleSheet(btn_style)
        stop_play_btn.clicked.connect(dialog.stop_playback_requested.emit)
        layout.addWidget(stop_play_btn)

        # Regen button — refresh icon regenerates this single job.
        self._regen_btn = QPushButton()
        self._regen_btn.setToolTip(
            "Regenerate ONLY this part.\n"
            "Queue position and output filename are preserved.")
        self._regen_btn.setFixedSize(36, 36)
        self._regen_btn.setIcon(IconRegistry.icon(
            "refresh", size=36, color=Palette.ACCENT_TEXT))
        self._regen_btn.setIconSize(QSize(32, 32))
        # Accent style for the Regen button (primary action).
        self._regen_btn.setStyleSheet(
            "QPushButton {{"
            "  background-color: {accent};"
            "  color: {accent_text};"
            "  border: 1px solid {accent};"
            "  border-radius: 4px;"
            "  padding: 1px;"
            "}}"
            "QPushButton:hover {{"
            "  background-color: {accent_hover};"
            "}}"
            "QPushButton:disabled {{"
            "  background-color: {bg_surface};"
            "  color: {text_disabled};"
            "  border: 1px solid {border};"
            "}}".format(
                accent=Palette.ACCENT,
                accent_text=Palette.ACCENT_TEXT,
                accent_hover=Palette.ACCENT_HOVER,
                bg_surface=Palette.BG_SURFACE,
                text_disabled=Palette.TEXT_DISABLED,
                border=Palette.BORDER))
        self._regen_btn.clicked.connect(
            lambda _checked=False, idx=idx: dialog._on_regen(idx))
        layout.addWidget(self._regen_btn)

        self.update_state(job, running)

    # -- state ----------------------------------------------------------
    def update_state(self, job: BatchJob, running: bool) -> None:
        """Refresh the band's enable states IN PLACE (no rebuild).

        P3.44 §11: in Scene mode Play is derived from whether an
        AUTHORITATIVE playable asset exists for the row's slot — NOT
        from BatchJob.status/output_path (the queue execution state).
        Manual mode keeps the COMPLETED + output_path contract (§9).
        """
        self._job = job
        self._play_btn.setEnabled(
            (not running) and self._dialog._play_available(job))
        self._play_btn.setToolTip(self._dialog._play_tooltip(job))
        can_regen = (not running
                     and job.status in (JobStatus.COMPLETED,
                                        JobStatus.FAILED))
        self._regen_btn.setEnabled(can_regen)

    # -- actions --------------------------------------------------------
    def _on_play_clicked(self) -> None:
        # CLICK-TIME resolution (§3): the currently selected/default
        # version plays — a Use made moments ago is honoured immediately
        # even before any table refresh.
        path = self._dialog._resolve_playable(self._job)
        if path:
            self._dialog.play_requested.emit(path)

    def play_button(self) -> QPushButton:
        """The row's Play button (tests + focus restoration)."""
        return self._play_btn


# ---------------------------------------------------------------------------
# Job edit dialog
# ---------------------------------------------------------------------------
class JobEditDialog(QDialog):
    """Modal dialog for editing a single BatchJob.

    Includes a seed field (QLineEdit, empty = random, integer = specific seed).
    """

    def __init__(self, job: BatchJob, voices: List[VoiceProfile], parent=None):
        super().__init__(parent)
        self._job = job
        self._voices = voices
        self.setWindowTitle("Edit Batch Job")
        self.setMinimumWidth(500)
        self._build_ui()
        self._load_from_job()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        grid = QGridLayout()
        grid.setSpacing(6)
        row = 0

        # Name
        grid.addWidget(QLabel("Name:"), row, 0)
        self._name = QLineEdit()
        self._name.setPlaceholderText("Job name (optional)")
        grid.addWidget(self._name, row, 1)
        row += 1

        # Prompt
        grid.addWidget(QLabel("Prompt:"), row, 0, alignment=Qt.Alignment.AlignTop)
        self._prompt = QLineEdit()
        self._prompt.setPlaceholderText("Prompt text (may include Higgs tokens)")
        grid.addWidget(self._prompt, row, 1)
        row += 1

        # Voice
        grid.addWidget(QLabel("Voice:"), row, 0)
        self._voice = QComboBox()
        self._voice.addItem("(No voice)", None)
        for v in self._voices:
            label = v.name
            if v.duration > 0:
                label += " ({0:.1f}s)".format(v.duration)
            self._voice.addItem(label, v.id)
        grid.addWidget(self._voice, row, 1)
        row += 1

        # Output filename
        grid.addWidget(QLabel("Output Filename:"), row, 0)
        self._output_filename = QLineEdit()
        self._output_filename.setPlaceholderText("auto (timestamp) or my_audio.wav")
        grid.addWidget(self._output_filename, row, 1)
        row += 1

        # Sampling parameters
        grid.addWidget(QLabel("Temperature:"), row, 0)
        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.1, 2.0)
        self._temperature.setSingleStep(0.05)
        self._temperature.setDecimals(2)
        grid.addWidget(self._temperature, row, 1)
        row += 1

        grid.addWidget(QLabel("Top P:"), row, 0)
        self._top_p = QDoubleSpinBox()
        self._top_p.setRange(0.1, 1.0)
        self._top_p.setSingleStep(0.05)
        self._top_p.setDecimals(2)
        grid.addWidget(self._top_p, row, 1)
        row += 1

        grid.addWidget(QLabel("Top K:"), row, 0)
        self._top_k = QSpinBox()
        self._top_k.setRange(1, 500)
        self._top_k.setSingleStep(10)
        grid.addWidget(self._top_k, row, 1)
        row += 1

        grid.addWidget(QLabel("Max Tokens:"), row, 0)
        self._max_tokens = QSpinBox()
        self._max_tokens.setRange(128, 8192)
        self._max_tokens.setSingleStep(128)
        grid.addWidget(self._max_tokens, row, 1)
        row += 1

        grid.addWidget(QLabel("Seed:"), row, 0)
        self._seed = QLineEdit()
        self._seed.setPlaceholderText("empty = random, or integer for reproducible")
        self._seed.setValidator(None)  # accept empty
        grid.addWidget(self._seed, row, 1)
        row += 1

        grid.addWidget(QLabel("Append Silence (s):"), row, 0)
        self._silence = QDoubleSpinBox()
        self._silence.setRange(0.0, 5.0)
        self._silence.setSingleStep(0.1)
        self._silence.setDecimals(1)
        grid.addWidget(self._silence, row, 1)
        row += 1

        self._normalize = QCheckBox("Normalize Output (-18 LUFS)")
        grid.addWidget(self._normalize, row, 1)
        row += 1

        layout.addLayout(grid)

        # Button box
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _load_from_job(self) -> None:
        j = self._job
        self._name.setText(j.name or "")
        self._prompt.setText(j.prompt or "")
        # Voice
        for i in range(self._voice.count()):
            if self._voice.itemData(i) == j.voice_id:
                self._voice.setCurrentIndex(i)
                break
        self._output_filename.setText(j.output_filename or "")
        p = j.parameters
        self._temperature.setValue(p.temperature)
        self._top_p.setValue(p.top_p)
        self._top_k.setValue(p.top_k)
        self._max_tokens.setValue(p.max_new_tokens)
        if p.seed is not None:
            self._seed.setText(str(p.seed))
        else:
            self._seed.setText("")
        self._silence.setValue(p.append_silence)
        self._normalize.setChecked(p.normalize_output)

    def apply_to_job(self) -> BatchJob:
        """Write the dialog's values back into the BatchJob and return it."""
        j = self._job
        j.name = self._name.text().strip()
        j.prompt = self._prompt.text()
        j.voice_id = self._voice.currentData()
        fname = self._output_filename.text().strip()
        j.output_filename = fname if fname else None
        seed_text = self._seed.text().strip()
        seed = int(seed_text) if seed_text else None
        j.parameters = GenerationParameters(
            temperature=float(self._temperature.value()),
            top_p=float(self._top_p.value()),
            top_k=int(self._top_k.value()),
            max_new_tokens=int(self._max_tokens.value()),
            seed=seed,
            append_silence=float(self._silence.value()),
            normalize_output=self._normalize.isChecked(),
            auto_play=False,  # never auto-play during batch
        )
        return j


# ---------------------------------------------------------------------------
# AnimatedHourglass — small painted widget for the "Generating" status pill
# ---------------------------------------------------------------------------
class AnimatedHourglass(QWidget):
    """Small QWidget that paints a spinning hourglass using QPainter.

    Used in the status pill of GENERATING jobs to give the user a clear
    visual signal that the engine is actively working. The animation is
    driven by an internal QTimer; call ``start()`` to begin spinning and
    ``stop()`` to halt. The widget is parented to its table cell, so when
    the cell widget is replaced (next _refresh_table call) Qt destroys
    the widget and its timer together — no manual cleanup is required.

    Color: ``Palette.WARNING`` (amber) — matches the mock's amber spinner
    but comes from the theme system, not a hardcoded value.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        # 22x22 fits comfortably inside a 48px table row.
        self.setFixedSize(22, 22)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def start(self) -> None:
        """Begin the spin animation (idempotent)."""
        if not self._timer.isActive():
            self._timer.start(45)  # ~22 FPS — smooth and cheap.
        self.show()

    def stop(self) -> None:
        """Halt the spin animation and reset the angle."""
        self._timer.stop()
        self._angle = 0
        self.update()
        self.hide()

    def _advance(self) -> None:
        self._angle = (self._angle + 8) % 360
        self.update()

    def paintEvent(self, event):
        """Paint the hourglass glyph rotated by ``self._angle`` degrees.

        The shape is two horizontal caps plus an X for the glass walls,
        with two sand triangles (top-full, bottom-empty-or-filling) and
        a thin falling stream — same geometry as the standalone mock.
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(self.width() / 2.0, self.height() / 2.0)
        p.rotate(self._angle)

        color = QColor(Palette.WARNING)
        pen = QPen(color, 1.8)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Hourglass frame (top + bottom caps + X glass walls).
        p.drawLine(-6, -7, 6, -7)
        p.drawLine(-6, 7, 6, 7)
        p.drawLine(-6, -7, 6, 7)
        p.drawLine(6, -7, -6, 7)

        # Sand (top triangle + bottom triangle + falling stream).
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        p.drawPolygon([
            QPointF(-4.0, -5.0),
            QPointF(4.0, -5.0),
            QPointF(0.0, -1.0),
        ])
        p.drawPolygon([
            QPointF(-4.0, 5.0),
            QPointF(4.0, 5.0),
            QPointF(0.0, 1.0),
        ])
        p.drawRect(QRectF(-0.65, -1.0, 1.3, 2.0))
        p.end()


# ---------------------------------------------------------------------------
# Batch generation dialog
# ---------------------------------------------------------------------------
class _UiMarshaler(QObject):
    """Thread-safe UI marshaller using a Qt signal.

    QTimer.singleShot(0, fn) from a worker thread DOES NOT WORK because
    the worker thread has no Qt event loop.  A Qt signal, however, is
    automatically marshalled to the receiver's thread (the UI thread)
    when emitted from a different thread.

    Usage:
        marshaler = _UiMarshaler()
        marshaler.call.connect(fn)  # on UI thread
        marshaler.call.emit(fn)     # from any thread — fn runs on UI thread
    """
    call = Signal(object)  # carries a callable

    def __init__(self, parent=None):
        super().__init__(parent)
        self.call.connect(self._on_call)

    def _on_call(self, fn):
        fn()

    def marshal(self, fn):
        """Can be called from any thread.  fn will run on the UI thread."""
        self.call.emit(fn)


class BatchGenerationDialog(QDialog):
    """Non-modal-style dialog that drives a BatchManager.

    The dialog owns a BatchManager instance (passed in from MainWindow)
    and exposes a table of jobs with add/edit/remove/move/duplicate
    actions plus start/pause/stop controls and a live summary.
    """

    # Emitted when the user clicks "Concatenate". The MainWindow connects
    # this to its concatenation handler, which has access to the engine
    # and the waveform player. The argument is the list of COMPLETED
    # job output paths (absolute) in queue order.
    concatenate_requested = Signal(list)

    # Emitted when the user clicks the per-row "Play" button.
    # The argument is the output_path of the job to play (relative to APP_ROOT).
    play_requested = Signal(str)

    # Emitted when the user clicks the per-row "Stop" playback button.
    stop_playback_requested = Signal()

    # P3.27B: emitted when the user confirms regeneration of a single
    # part. MainWindow owns the versioned-filename allocation (it knows
    # the engine's outputs dir), so the dialog no longer resets the job
    # itself — for Generate Long parts the handler allocates a NEW
    # version (never overwriting the previous file) before resetting the
    # job and starting the batch. Manual jobs keep the legacy behaviour.
    regen_requested = Signal(int)

    # P3.28 §13: emitted when the user clicks "Combine Scene" (scene
    # mode only). MainWindow resolves the per-slot audio, allocates the
    # next Scene Combined version, concatenates, and appends the
    # lineage-tracked entry to Scene.combined_outputs.
    combine_scene_requested = Signal()

    # P3.28 §15/Rec 16: per-slot version selection. Args:
    # (slot_id, asset_id) — asset_id "" clears the selection (latest).
    use_version_requested = Signal(str, str)

    # P3.28 §15/Rec 16: explicit Scene output selection. Args:
    # (kind, entry_id) — kind "scene_combined"|"asset"; empty kind/id
    # clears the selection.
    select_output_requested = Signal(str, str)

    # P3.28 §11–§12: selective generation — args: the list of CHECKED
    # row indices. MainWindow allocates fresh versions for already-
    # covered checked slots, marks unchecked pending jobs SKIPPED, and
    # starts the SAME pipeline for exactly the checked rows.
    generate_selected_requested = Signal(list)

    # P3.28 §17: batch audio export (copy-only). Args: (scope,
    # checked_indices) — scope "selected"|"parts"|"combined"|"everything".
    export_audio_requested = Signal(str, list)

    def __init__(self, manager: BatchManager, voices: List[VoiceProfile],
                 parent=None, scene=None, project=None, app_root: str = "",
                 review_mode: bool = False, settings_manager=None):
        super().__init__(parent)
        self._manager = manager
        self._voices = voices
        # P3.28 scene context (None = manual Batch Queue window — all
        # P3.28 provenance UI is hidden and behaviour is unchanged).
        self._scene = scene
        self._project = project
        self._app_root = app_root or os.getcwd()
        self._review_mode = bool(review_mode) and scene is not None
        # P3.35: optional SHARED SettingsManager (MainWindow passes its
        # own instance so window geometry / column state land in the
        # app's settings namespace — never in the project file). When
        # omitted (legacy direct constructions) the app-root settings
        # folder is used, exactly as before.
        self._settings_manager = settings_manager
        # User check states for the selective-generation column — set
        # ONCE at open (defaults per P3.28 §11: not-generated slots
        # checked, already-generated slots unchecked) and preserved
        # across _refresh_table rebuilds (P3.44 §7: the in-place update
        # path keeps the checkbox WIDGETS themselves — states can never
        # be lost; the dict is the rebuild-path backup).
        self._check_states: dict = {}
        self._init_check_defaults()
        # P3.44 §1/§7/§8: render-state tracking for targeted refreshes.
        # _rendered_signature holds the identity tuple of the job
        # objects last rendered; BatchJob is a mutable dataclass the
        # manager updates IN PLACE, so an unchanged signature means a
        # status/duration/output-only change — handled by in-place cell
        # updates that never destroy row widgets (no focus loss, no
        # selection loss). A changed signature (add/remove/move/reopen)
        # takes the full rebuild path WITH state preservation.
        self._rendered_signature = None
        self._row_refs: list = []
        # P3.35 §5: the two workflows must be visually distinguishable
        # IMMEDIATELY — scene mode names the Scene, manual mode is the
        # honest "Batch Queue" (never the identical old "Batch
        # Generation" title that made it look like a broken twin).
        if scene is not None:
            self.setWindowTitle("Batch Generation \u2014 {0}".format(
                scene.name or "Scene"))
        else:
            self.setWindowTitle("Batch Queue")
        # P3.18: default size bumped from 900x600 to 1100x680 to give the
        # new two-panel layout (table + QUEUE SUMMARY sidebar) comfortable
        # breathing room. The user's saved geometry (settings.json) still
        # takes precedence via _restore_geometry().
        self.resize(1100, 680)
        # P3.35 §22: debounced save on live resize (close/hide still save
        # unconditionally — the timer covers destroy-without-close paths
        # and gives "resize → saved" its literal reading).
        self._geo_save_timer = QTimer(self)
        self._geo_save_timer.setSingleShot(True)
        self._geo_save_timer.setInterval(600)
        self._geo_save_timer.timeout.connect(self._save_geometry)
        # P3.35 §11: mode-switch close flag — close_without_stopping_batch()
        # sets it so the single-live-instance hand-over NEVER stops a
        # running batch (the replacement dialog keeps showing progress).
        self._close_preserve_batch = False
        self._ui_state_saved = False

        # Restore saved window size from settings (if any).
        self._restore_geometry()

        # Wire the manager so all callbacks marshal into the UI thread.
        self._connect_manager()
        self._build_ui()
        self._refresh_table()
        self._update_buttons()
        # P3.28: initial fill of the scene-derived surfaces (coverage
        # header, GENERATE CHECKED label, combined outputs section).
        if self._scene is not None:
            self._update_coverage_header()
            self._update_start_button()
            self._refresh_combined_section()

    @property
    def is_scene_mode(self) -> bool:
        """P3.35 §3: mode identity for the single-live-instance policy."""
        return self._scene is not None

    @property
    def scene(self):
        """The Scene bound to this dialog (None in manual Batch Queue mode)."""
        return self._scene

    def _checked_indices(self) -> list:
        """The CHECKED row indices (derived from the current dict)."""
        return sorted(idx for idx, val in self._check_states.items() if val)

    def _init_check_defaults(self) -> None:
        """P3.28 §11 default selection: ONLY not-generated slots checked.

        Regenerating costs minutes of GPU per part and existing audio is
        never invalid (§0 product rule), so pre-checking generated slots
        would invite accidental mass re-spend. The user can select
        already-generated slots explicitly (partial regeneration — §12).
        """
        if self._scene is None or not self._manager:
            return
        try:
            from engine.audio_provenance import is_slot_covered
            from engine.batch_manager import JobStatus
            for idx, job in enumerate(self._manager.jobs):
                slot_id = getattr(job, "slot_id", None)
                if slot_id and self._scene is not None:
                    covered = is_slot_covered(self._scene, slot_id)
                else:
                    covered = (job.status == JobStatus.COMPLETED)
                # not-generated → checked; already-generated → unchecked
                self._check_states[idx] = not covered
        except Exception:
            pass

    def refresh_scene_context(self) -> None:
        """P3.28: refresh every scene-derived surface (coverage header,
        per-row generated cells, combined outputs section, buttons).

        Called after batch completion, version/output selection, Scene
        Combine, and job completion (via _on_manager_changed).
        """
        if self._scene is None:
            return
        self._update_coverage_header()
        self._update_start_button()
        self._refresh_table()

    def _update_coverage_header(self) -> None:
        """P3.28 §5/Rec 12: the coverage header line above the table."""
        if self._scene is None or not hasattr(self, "_coverage_label"):
            return
        from engine.audio_provenance import (
            coverage_counts, scene_coverage_state, SCENE_STATE_LABELS,
        )
        covered, total = coverage_counts(self._scene)
        state = scene_coverage_state(self._scene)
        run_hint = ""
        for job in self._manager.jobs:
            run_hint = getattr(job, "generation_run", None) or ""
            if run_hint:
                break
        text = "{0}  —  coverage: {1} ({2}/{3})".format(
            self._scene.name or "Scene",
            SCENE_STATE_LABELS.get(state, state.upper()), covered, total)
        if run_hint:
            text += "  —  run {0}".format(run_hint)
        self._coverage_label.setText(text)

    def _update_start_button(self) -> None:
        """P3.28: the primary button's label reflects the checked set.

        Scene mode: "GENERATE CHECKED (N)" (selective generation — §11/§12).
        Legacy manual mode: unchanged "START BATCH".
        """
        if not hasattr(self, "_start_btn"):
            return
        if self._scene is None:
            self._start_btn.setText("START BATCH")
            return
        checked = len(self._checked_indices())
        self._start_btn.setText(
            "GENERATE CHECKED ({0})".format(checked) if checked
            else "GENERATE CHECKED")

    def _settings(self):
        """P3.35: the ONE settings channel for this dialog.

        Prefers the SHARED SettingsManager injected by MainWindow (so
        geometry / column state follow the app's settings directory,
        e.g. a test's isolated app root); falls back to the app-root
        settings folder exactly as P3.25 established.
        """
        if self._settings_manager is not None:
            return self._settings_manager
        try:
            from engine.settings_manager import SettingsManager
            _root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            return SettingsManager.instance(os.path.join(_root, "settings"))
        except Exception:
            return None

    def _restore_geometry(self) -> None:
        """Restore the saved dialog size from settings.json.

        P3.25 (audit SS-H04): reads via the SHARED SettingsManager — no
        raw independent file reads. P3.35 §22: both modes share ONE
        geometry key (``window/batch_dialog``) ON PURPOSE — the two
        workflows use the same two-panel architecture, and a size the
        user chose for one is a fine default for the other.
        """
        sm = self._settings()
        if sm is None:
            return
        try:
            geo = sm.get("window", "batch_dialog", {}) or {}
            if geo:
                w = geo.get("width", 900)
                h = geo.get("height", 600)
                self.resize(int(w), int(h))
        except Exception:
            pass

    def _save_geometry(self) -> None:
        """Save the current dialog size to settings.json.

        P3.25 (audit SS-H04): the dialog previously performed a RAW
        read-modify-write of settings.json — an independent fourth writer
        that bypassed the SettingsManager merge/atomicity discipline. It
        now writes through the SHARED SettingsManager (atomic,
        merge-on-write, coherent with every other component).
        """
        sm = self._settings()
        if sm is None:
            return
        try:
            sm.set("window", "batch_dialog", {
                "width": self.width(),
                "height": self.height(),
            })
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        """P3.35 §22: persist the user's resize (debounced) — the saved
        geometry survives close/reopen/restart via SettingsManager."""
        super().resizeEvent(event)
        if self._geo_save_timer is not None:
            self._geo_save_timer.start()

    # ------------------------------------------------------------------
    # P3.35 §23-27: per-mode column configuration persistence
    # ------------------------------------------------------------------
    def _column_state_key(self) -> str:
        """Column state is persisted PER MODE (different table schemas:
        scene mode = 7 columns, manual queue = 5). One shared blob would
        produce invalid/missing mappings for the other mode."""
        return ("batch_generation_scene_columns" if self._scene_mode
                else "batch_queue_columns")

    def _save_column_state(self) -> None:
        """Persist widths + order + visibility via the native Qt header
        state (QHeaderView.saveState) — plus a title-keyed width map used
        as the schema-evolution fallback (a stored state whose column
        count no longer matches is never applied wholesale)."""
        sm = self._settings()
        if sm is None:
            return
        try:
            header = self._table.horizontalHeader()
            titles = [self._table.horizontalHeaderItem(i).text()
                      for i in range(self._table.columnCount())]
            widths = {}
            for i, title in enumerate(titles):
                if title:
                    widths[title] = int(header.sectionSize(i))
            import base64
            blob = base64.b64encode(bytes(header.saveState())).decode("ascii")
            sm.set("window", self._column_state_key(), {
                "version": 1,
                "columns": titles,
                "state": blob,
                "widths": widths,
            })
        except Exception:
            pass

    def _restore_column_state(self) -> None:
        """Restore the saved column configuration for THIS mode.

        Migration policy (P3.35 §27): the native state blob is applied
        ONLY when the stored schema (column titles) matches the current
        table exactly. On a schema mismatch (obsolete or new columns)
        only the title-keyed WIDTHS are re-applied — obsolete titles are
        ignored safely, new columns keep their default position /
        visibility / width, and a corrupt blob can never crash the open.
        """
        sm = self._settings()
        if sm is None:
            return
        try:
            data = sm.get("window", self._column_state_key(), None)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        header = self._table.horizontalHeader()
        titles = [self._table.horizontalHeaderItem(i).text()
                  for i in range(self._table.columnCount())]
        try:
            saved_titles = [str(t) for t in (data.get("columns") or [])]
            blob = str(data.get("state") or "")
            if blob and saved_titles == titles:
                from PySide6.QtCore import QByteArray
                raw = QByteArray.fromBase64(blob.encode("ascii"))
                if not raw.isEmpty() and header.restoreState(raw):
                    return  # exact-schema restore (widths+order+visibility)
            # Schema-evolution fallback: compatible widths by title only.
            widths = data.get("widths")
            if isinstance(widths, dict):
                for i, title in enumerate(titles):
                    if title and title in widths:
                        try:
                            header.resizeSection(i, int(widths[title]))
                        except (TypeError, ValueError):
                            continue
        except Exception:
            pass

    def _on_header_context_menu(self, pos) -> None:
        """P3.35 §26: right-click header menu — checkable show/hide per
        column (the native Qt table idiom, not a new management UX)."""
        from PySide6.QtWidgets import QMenu
        header = self._table.horizontalHeader()
        menu = QMenu(self)
        menu.setWindowTitle("Columns")
        for col in range(self._table.columnCount()):
            item = self._table.horizontalHeaderItem(col)
            label = (item.text() if item and item.text()
                     else "Column {0}".format(col + 1))
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(not header.isSectionHidden(col))
            act.toggled.connect(
                lambda visible, _c=col: header.setSectionHidden(_c, not visible))
        menu.exec(header.viewport().mapToGlobal(pos))

    def showEvent(self, event) -> None:
        """P3.44.5 §4/§5: every presentation guarantees a LIVE manager
        subscription + a presentation re-synced from manager truth.

        ``closeEvent`` releases this dialog's manager listener, but the
        dialog OBJECT survives close — the scene-aware single-instance
        policy (MainWindow._focus_existing_batch_dialog) later finds and
        re-shows the very SAME object. Before P3.44.5 that reuse path
        never re-registered the listener: the reopened dialog received
        NO further manager events, its rows kept the pre-close status
        pills, the summary froze, and Start stayed disabled while
        Pause/Stop stayed enabled — recovering only when the user pressed
        Pause/Stop (those handlers force a local refresh). This is the
        audited B/D reproduction.

        Two actions on EVERY show (fresh construction AND reuse):

        1. ``_connect_manager`` — now idempotent — re-establishes the
           subscription released by closeEvent. Reconnection reuses the
           SAME callback objects, so the manager's identity-based
           dedupe guarantees exactly one logical listener (one manager
           event → one UI update, never N duplicated updates).
        2. ``_on_manager_changed`` re-syncs the table / buttons / summary
           / scene surfaces from the manager's CURRENT state — covering
           everything that changed while the dialog was closed and no
           events were delivered (e.g. a batch that finished or was
           stopped while the dialog was hidden).
        """
        self._connect_manager()
        self._on_manager_changed()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        """P3.35 §22/§28: persist user customizations as soon as the
        dialog leaves the screen (covers paths that bypass closeEvent)."""
        if not self._ui_state_saved:
            self._save_geometry()
            self._save_column_state()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # Manager wiring
    # ------------------------------------------------------------------
    def _connect_manager(self) -> None:
        """Configure the BatchManager to marshal callbacks to the UI thread
        via a Qt signal (thread-safe, unlike QTimer.singleShot).

        P3.44.1 §4 (callback architecture): the manager now supports
        MULTIPLE change listeners (``add_on_changed``). This dialog
        registers its own listener and removes it on close/destroy —
        coexisting observers (another Batch dialog, MainWindow) are
        never overwritten. The previous single-slot design meant a
        second Batch dialog silently replaced the first dialog's
        callback, and the first dialog then received NO manager updates
        (stale table, dead controls) until manually refreshed.

        The marshaler hand-over remains a disciplined swap: the previous
        ``marshal_to_ui`` is remembered and restored on release (only
        when the manager still points at OURS — a newer owner is never
        clobbered).

        P3.44.5 §4 (stale-listener fix): this method is now IDEMPOTENT,
        and ``showEvent`` funnels every presentation of this dialog
        (fresh construction AND reuse-after-close) through it. Rules:

        * NO-OP while OUR exact listener is already registered — checked
          against the manager's ACTUAL listener list, never a second
          bookkeeping flag (external listener-list changes cannot leave
          a stale "connected" belief). A newer marshal owner is never
          clobbered here either: events still reach OUR listener through
          the manager's listener list.
        * The listener + marshaler callback objects are created ONCE per
          dialog and REUSED on every reconnect, so ``add_on_changed``'s
          identity dedupe guarantees exactly one logical listener — one
          manager event can never fan out into duplicated UI updates
          across close/reopen cycles.
        * ``_prev_marshal`` is only re-snapshotted when the current
          marshaler is NOT our own, so a reconnect can never adopt its
          own marshaler as the "previous owner" to later restore.
        """
        already = (
            getattr(self, "_on_changed_cb", None) is not None
            and any(cb is self._on_changed_cb
                    for cb in self._manager.on_changed_listeners))
        if already:
            return
        if getattr(self, "_on_changed_cb", None) is None:
            self._marshaler = _UiMarshaler(self)
            self._on_changed_cb = lambda _m: self._on_manager_changed()
            # Store the bound method ONCE: attribute access creates a NEW
            # bound-method object each time, so ``is`` comparisons against
            # ``self._marshaler.marshal`` would never match (release must
            # compare with == / the stored reference).
            self._marshal_cb = self._marshaler.marshal
            # Death safety: if this dialog is destroyed WITHOUT a
            # closeEvent (e.g. parent teardown), never leave the manager
            # calling a dead dialog. The closure captures only the
            # manager + our callback — it does not keep the dialog alive.
            # Connected ONCE (the callback objects live for the whole
            # dialog lifetime), so reconnect cycles cannot accumulate
            # duplicate destroy handlers.
            _mgr, _on_cb, _mcb = (self._manager, self._on_changed_cb,
                                  self._marshal_cb)

            def _on_destroyed(*_args):
                try:
                    _mgr.remove_on_changed(_on_cb)
                except Exception:
                    pass
                try:
                    if getattr(_mgr, "marshal_to_ui", None) == _mcb:
                        _mgr.marshal_to_ui = None
                except Exception:
                    pass
            self.destroyed.connect(_on_destroyed)
        # Snapshot the CURRENT marshal owner so release can give it back
        # — never our own marshaler (a reconnect after a partial release
        # must keep the original pre-dialog owner as the restore target).
        current_marshal = getattr(self._manager, "marshal_to_ui", None)
        if current_marshal != getattr(self, "_marshal_cb", None):
            self._prev_marshal = current_marshal
        self._manager.marshal_to_ui = self._marshal_cb
        self._manager.add_on_changed(self._on_changed_cb)

    def _release_manager(self) -> None:
        """P3.44.1 §4: remove this dialog's listener and give the shared
        manager's marshaler back.

        Called from closeEvent. Removing OUR listener never touches any
        other observer; the marshaler is restored only when the manager
        still points at THIS dialog's marshaler — a newer owner is
        never clobbered, so there is no callback theft in either
        direction.
        """
        try:
            self._manager.remove_on_changed(self._on_changed_cb)
        except Exception:
            pass
        try:
            if (getattr(self._manager, "marshal_to_ui", None)
                    == self._marshal_cb):
                self._manager.marshal_to_ui = self._prev_marshal
        except Exception:
            pass

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """Build the two-panel layout (P3.18 visual redesign).

        Left panel  (~70%): toolbar rows + job table (7 columns in scene
                            mode, 5 in the manual Batch Queue).
        Right panel (~30%): "QUEUE SUMMARY" sidebar with progress bar,
                            stat lines, Merge Completed Parts (manual
                            mode) / Combine Scene + Export Audio (scene
                            mode), START/GENERATE, Pause/Stop.

        All colors come from :class:`ui.theme.Palette` — no mock hex values.
        All icons come from :class:`ui.icon_registry.IconRegistry`.
        """
        # Top-level horizontal split: left = table, right = summary sidebar.
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # ---------------------------------------------------------------
        # LEFT PANEL — toolbar rows + coverage header + 7-column job table
        #        + Scene combined outputs section (P3.28)
        # ---------------------------------------------------------------
        left = QVBoxLayout()
        left.setSpacing(8)

        # P3.28 §5/Rec 12: coverage header (scene mode only) —
        # "SCENE 03 — coverage: PARTIAL (3/4) — run {scene8}-r004" plus
        # All/None quick-select for the per-row checkboxes.
        if self._scene is not None:
            header_row = QHBoxLayout()
            self._coverage_label = QLabel("")
            self._coverage_label.setStyleSheet(
                "color: {accent}; font-size: 12px; font-weight: 700;"
                .format(accent=Palette.ACCENT))
            header_row.addWidget(self._coverage_label, 1)
            self._sel_all_btn = QPushButton("All")
            # P3.35 §29: metrics-aware height — the old fixed 24px clipped
            # the label bottom (global button QSS pads 7px + 1px border).
            _fit_button_height(self._sel_all_btn)
            self._sel_all_btn.setToolTip(
                "Check every part (generate everything)")
            self._sel_all_btn.clicked.connect(self._on_select_all)
            header_row.addWidget(self._sel_all_btn)
            self._sel_none_btn = QPushButton("None")
            # P3.35 §29: the reported clipped "None" button — same root
            # cause as "All" (shared geometry), same metrics-aware fix.
            _fit_button_height(self._sel_none_btn)
            self._sel_none_btn.setToolTip("Uncheck every part")
            self._sel_none_btn.clicked.connect(self._on_select_none)
            header_row.addWidget(self._sel_none_btn)
            left.addLayout(header_row)

        # Toolbar row 1: Add / Edit / Duplicate / Remove ... Up / Down
        job_row = QHBoxLayout()
        job_row.setSpacing(4)
        self._add_btn = QPushButton("+ Add")
        self._add_btn.clicked.connect(self._on_add)
        job_row.addWidget(self._add_btn)

        self._edit_btn = QPushButton("Edit...")
        self._edit_btn.clicked.connect(self._on_edit)
        job_row.addWidget(self._edit_btn)

        self._dup_btn = QPushButton("Duplicate")
        self._dup_btn.clicked.connect(self._on_duplicate)
        job_row.addWidget(self._dup_btn)

        self._remove_btn = QPushButton("- Remove")
        self._remove_btn.clicked.connect(self._on_remove)
        job_row.addWidget(self._remove_btn)

        job_row.addStretch()

        self._up_btn = QPushButton("\u2191 Up")
        self._up_btn.clicked.connect(self._on_move_up)
        job_row.addWidget(self._up_btn)

        self._down_btn = QPushButton("\u2193 Down")
        self._down_btn.clicked.connect(self._on_move_down)
        job_row.addWidget(self._down_btn)

        left.addLayout(job_row)

        # Toolbar row 2: Save / Load / Clear
        file_row = QHBoxLayout()
        file_row.setSpacing(4)
        self._save_btn = QPushButton("Save Queue...")
        self._save_btn.clicked.connect(self._on_save_queue)
        file_row.addWidget(self._save_btn)

        self._load_btn = QPushButton("Load Queue...")
        self._load_btn.clicked.connect(self._on_load_queue)
        file_row.addWidget(self._load_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._on_clear)
        file_row.addWidget(self._clear_btn)

        file_row.addStretch()
        left.addLayout(file_row)

        # 7 columns (P3.28): #, [✓], File Name, Generated, Status,
        # Duration, Actions — SCENE MODE. In the LEGACY manual window the
        # original P3.18 five-column layout is kept EXACTLY (#, File Name,
        # Status, Duration, Actions at the original indices) so existing
        # behaviour and tests are untouched; the checkbox and Generated
        # columns exist only in scene mode.
        self._scene_mode = self._scene is not None
        if self._scene_mode:
            self._col_num, self._col_check, self._col_name = 0, 1, 2
            self._col_gen, self._col_status = 3, 4
            self._col_dur, self._col_act = 5, 6
            self._table = QTableWidget(0, 7)
            self._table.setHorizontalHeaderLabels(
                ["#", "", "File Name", "Generated", "Status", "Duration",
                 "Actions"])
        else:
            self._col_num, self._col_name = 0, 1
            self._col_status, self._col_dur, self._col_act = 2, 3, 4
            self._table = QTableWidget(0, 5)
            self._table.setHorizontalHeaderLabels(
                ["#", "File Name", "Status", "Duration", "Actions"])
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        # P3.21: Rounded corners on the table container to match the
        # modern card aesthetic. The QTableWidget itself doesn't support
        # border-radius, but we can style the viewport + header.
        #
        # P3.40 (row rendering audit) — TWO consolidation rules:
        #
        # (1) ONE selection treatment. The APP-level stylesheet
        #     (ui.theme generate_qss) styles selected table cells with
        #     ``border-left: 2px solid {accent}`` (Modern Dark accent
        #     #d0bcff). Because a widget stylesheet only overrides the
        #     PROPERTIES it explicitly declares, that rule used to cascade
        #     into this table and paint a bright 2px purple vertical line
        #     at EVERY cell boundary of the selected row (one per column,
        #     full row height — runtime-verified). This stylesheet now
        #     explicitly resets every per-cell border (including
        #     border-left) on ``::item`` AND on ``::item:selected`` so the
        #     ONLY selection treatment is the single translucent purple
        #     row fill below. ``::item:focus`` (declared BEFORE
        #     ``::item:selected`` so the fill still wins on the current
        #     cell) suppresses any native focus border around the current
        #     cell — no second indicator.
        # (2) Cell-widget geometry. ``::item`` padding insets every
        #     setCellWidget() into the item's CONTENT rect. With the old
        #     ``padding: 6px`` + 1px border-bottom, the action widget got
        #     48 - 6 - 6 - 1 = 35px of height for 4+36+4 = 44px of fixed
        #     buttons — the buttons overflowed 5px past the widget bottom
        #     and were clipped (icons cut, row looked bottom-heavy).
        #     Vertical padding is now 0: content height = 48 - 1 = 47px,
        #     which fits the 44px button band with symmetric clearance.
        #     Delegate-painted text (#/Name/Duration) is unaffected — it
        #     is vertically centred by item alignment, not by padding.
        self._table.setStyleSheet("""
            QTableWidget {
                background-color: rgba(36, 36, 36, 150);
                border: 1px solid #333333;
                border-radius: 8px;
                outline: none;
                gridline-color: transparent;
                font-size: 13px;
            }
            QTableWidget::item {
                border: none;
                border-bottom: 1px solid #333333;
                padding: 0px 6px;
            }
            QTableWidget::item:focus {
                border: none;
                outline: none;
            }
            QTableWidget::item:selected {
                background: rgba(139, 92, 246, 35);
                color: #F5F5F5;
                border: none;
                border-bottom: 1px solid #333333;
            }
            QHeaderView::section {
                background: rgba(32, 31, 31, 235);
                color: #A0A0A0;
                border: none;
                border-bottom: 1px solid #333333;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 8px;
                font-size: 11px;
                font-weight: 700;
            }
        """)
        header = self._table.horizontalHeader()
        header.setMinimumSectionSize(40)
        header.setStretchLastSection(False)
        # Column resize modes — Fixed keeps the pill column tidy; the Name
        # column stretches to fill whatever horizontal space the table gets.
        # P3.35 §23-26: the user-resizable columns are Interactive (Fixed
        # mode made column RESIZING impossible — the "lost" column
        # configuration), sections are movable (drag to reorder) and the
        # header has a checkable show/hide context menu. The whole state
        # (widths + order + visibility) persists per mode via
        # QHeaderView.saveState/restoreState.
        if self._scene_mode:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)      # #
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)      # check
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)    # Name / Segment
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)  # Generated
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)  # Status (pills)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)  # Duration
            header.setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)  # Actions
            header.resizeSection(0, 40)    # #
            header.resizeSection(1, 36)    # checkbox
            header.resizeSection(3, 190)   # Generated + versions ▾
            header.resizeSection(4, 130)   # Status (icon + pill)
            header.resizeSection(5, 80)    # Duration
            header.resizeSection(6, 140)   # Actions (3 × 36px + margins + spacing)
            # P3.40: derived MINIMUM width for the Actions column =
            #   3 buttons × 36 + 2 spacing × 4 + 2 margins × 4
            #   + 2 × 6px horizontal ::item padding = 124px.
            # The 140px default keeps the full button row comfortably
            # visible; shrinking below 124 clips buttons horizontally.
        else:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)      # #
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)    # Name / Segment
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)  # Status (pills)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)  # Duration
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)  # Actions
            header.resizeSection(0, 40)    # #
            header.resizeSection(2, 130)   # Status (icon + pill)
            header.resizeSection(3, 80)    # Duration
            header.resizeSection(4, 140)   # Actions (3 × 36px + 4px margins + 4px spacing)
            # P3.40: see the scene-mode note above — 124px derived minimum
            # (3×36 buttons + 8 spacing + 8 margins + 12 ::item padding).
        # P3.35 §23-27: restore the user's saved column configuration for
        # THIS mode (widths + order + visibility), then enable the native
        # reorder + hide/show interactions. Everything persists on close.
        self._restore_column_state()
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(
            self._on_header_context_menu)
        # Row height 48px accommodates the pill widget + the action buttons.
        # P3.40: the ``::item`` box (0 vertical padding + 1px border-bottom)
        # leaves 47px of content height per cell — the 4+36+4 = 44px action
        # button band fits fully INSIDE the row (no bottom clipping).
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(48)
        self._table.verticalHeader().setMinimumSectionSize(48)
        left.addWidget(self._table, 1)

        # ---------------------------------------------------------------
        # P3.28 §13: SCENE COMBINED OUTPUTS section (scene mode only) —
        # versioned, lineage-tracked combined outputs with STALE badges
        # and "Use as Scene output" selection. One row per COMBINED
        # OUTPUT (never per version asset — Rec 22: no widget per file).
        # ---------------------------------------------------------------
        if self._scene is not None:
            combined_group = QFrame()
            combined_group.setObjectName("SceneCombinedGroup")
            combined_group.setStyleSheet(
                "QFrame#SceneCombinedGroup {{"
                "  background-color: {bg_surface};"
                "  border: 1px solid {border};"
                "  border-radius: 8px;"
                "}}".format(bg_surface=Palette.BG_SURFACE,
                            border=Palette.BORDER))
            cg_layout = QVBoxLayout(combined_group)
            cg_layout.setContentsMargins(10, 8, 10, 8)
            cg_layout.setSpacing(4)
            cg_header = QLabel("SCENE COMBINED OUTPUTS")
            cg_header.setStyleSheet(
                "color: {muted}; font-size: 10px; font-weight: 700;"
                " letter-spacing: 1px;".format(muted=Palette.TEXT_SECONDARY))
            cg_layout.addWidget(cg_header)
            self._resolved_output_label = QLabel("")
            self._resolved_output_label.setStyleSheet(
                "color: {text}; font-size: 11px;".format(
                    text=Palette.TEXT_PRIMARY))
            self._resolved_output_label.setWordWrap(True)
            cg_layout.addWidget(self._resolved_output_label)
            self._combined_rows_layout = QVBoxLayout()
            self._combined_rows_layout.setSpacing(2)
            cg_layout.addLayout(self._combined_rows_layout)
            self._combined_group = combined_group
            left.addWidget(combined_group)

        layout.addLayout(left, 7)  # ~70% of horizontal space

        # ---------------------------------------------------------------
        # RIGHT PANEL — "QUEUE SUMMARY" sidebar
        # ---------------------------------------------------------------
        side = QFrame()
        side.setObjectName("BatchQueueSidePanel")
        side.setMinimumWidth(280)
        side.setMaximumWidth(360)
        side.setStyleSheet(
            "QFrame#BatchQueueSidePanel {{"
            "  background-color: {bg_surface};"
            "  border: 1px solid {border};"
            "  border-radius: 8px;"
            "}}".format(bg_surface=Palette.BG_SURFACE, border=Palette.BORDER))
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 16, 16, 16)
        sl.setSpacing(12)

        # --- QUEUE SUMMARY section header ---
        section_label = QLabel("QUEUE SUMMARY")
        section_label.setStyleSheet(
            "QLabel {{"
            "  color: {muted};"
            "  font-size: 11px;"
            "  font-weight: 700;"
            "  letter-spacing: 1px;"
            "}}".format(muted=Palette.TEXT_SECONDARY))
        sl.addWidget(section_label)

        # --- Summary card: progress text + progress bar + 4 stat lines ---
        self._summary_card = QFrame()
        self._summary_card.setObjectName("BatchSummaryCard")
        self._summary_card.setStyleSheet(
            "QFrame#BatchSummaryCard {{"
            "  background-color: {bg_raised};"
            "  border: 1px solid {border};"
            "  border-radius: 6px;"
            "}}".format(bg_raised=Palette.BG_RAISED, border=Palette.BORDER))
        sc = QVBoxLayout(self._summary_card)
        sc.setContentsMargins(12, 12, 12, 12)
        sc.setSpacing(8)

        # Progress headline ("Progress    1/5 completed")
        self._progress_text = QLabel("Idle")
        self._progress_text.setStyleSheet(
            "color: {accent};"
            " font-family: 'JetBrains Mono','Consolas','Courier New',monospace;"
            " font-size: 12px;".format(accent=Palette.ACCENT))
        sc.addWidget(self._progress_text)

        # Thin progress bar — accent-colored chunk on a muted groove.
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setStyleSheet(
            "QProgressBar {{"
            "  background-color: {bg_surface};"
            "  border: none;"
            "  border-radius: 3px;"
            "}}"
            "QProgressBar::chunk {{"
            "  background-color: {accent};"
            "  border-radius: 3px;"
            "}}".format(bg_surface=Palette.BG_SURFACE, accent=Palette.ACCENT))
        sc.addWidget(self._progress)

        # Stat lines (mono font, muted color, label + value).
        self._audio_label = self._make_summary_line("Generated Audio")
        self._rtf_label    = self._make_summary_line("Real Time Factor")
        self._wall_label   = self._make_summary_line("Wall Time")
        self._state_label  = self._make_summary_line("State")
        for w in (self._audio_label, self._rtf_label,
                  self._wall_label, self._state_label):
            sc.addWidget(w)

        sl.addWidget(self._summary_card)

        sl.addStretch(1)

        # --- Combine Scene button (P3.28 §13; scene mode only) ---
        # Combines the RESOLVED audio of every expected slot (selected
        # version per slot, else latest) into a VERSIONED, lineage-tracked
        # Scene Combined output: {Proj}_{Scene}_Combined_v01_{scene8}.wav.
        # Older combined outputs remain; nothing is rebuilt automatically.
        if self._scene is not None:
            self._combine_btn = QPushButton("Combine Scene")
            self._combine_btn.setToolTip(
                "Combine the generated slot outputs of this Scene into a\n"
                "VERSIONED combined WAV (e.g. Combined_v01) with full\n"
                "per-slot lineage. Older combined outputs are kept.\n"
                "\n"
                "Project Assembly consumes the Scene's resolved output\n"
                "(latest non-stale combined output by default).")
            self._combine_btn.clicked.connect(
                self.combine_scene_requested.emit)
            self._combine_btn.setStyleSheet(self._tool_button_style())
            sl.addWidget(self._combine_btn)

        # --- Merge Completed Parts button (manual Batch Queue only) ---
        # P3.35 §9: Concatenate is NOT a Scene-level operation — in scene
        # mode the equivalent is the versioned, lineage-tracked "Combine
        # Scene" above, so the manual merge utility lives ONLY in the
        # manual queue (previously labelled "Concatenate"; renamed per the
        # accepted COMBINE_VS_CONCATENATE design to state exactly what it
        # does and what it does NOT touch).
        if self._scene is None:
            self._concat_btn = QPushButton("Merge Completed Parts")
            self._concat_btn.setToolTip(
                "Merge the completed jobs in this queue in queue order into "
                "one audio file.\nThis output is not linked to a Scene and "
                "does not affect Project Assembly.")
            self._concat_btn.clicked.connect(self._on_concatenate)
            self._concat_btn.setStyleSheet(self._tool_button_style())
            sl.addWidget(self._concat_btn)

        # --- Export Audio button (P3.28 §17; scene mode only) ---
        # Copy-only export of the provenance-named files (never a second
        # naming system): scope menu + destination picker in MainWindow.
        if self._scene is not None:
            self._export_btn = QPushButton("Export Audio...")
            self._export_btn.setToolTip(
                "Copy generated audio files to a folder you choose.\n"
                "\n"
                "Scopes: selected rows / all generated parts / Scene\n"
                "combined outputs / everything in this batch.\n"
                "Files keep their provenance filenames; collisions are\n"
                "never overwritten silently.")
            self._export_btn.clicked.connect(self._on_export_audio)
            self._export_btn.setStyleSheet(self._tool_button_style())
            sl.addWidget(self._export_btn)

        # --- START BATCH / GENERATE CHECKED button (primary CTA) ---
        # Mock used an orange gradient; this app uses Palette.ACCENT (the
        # theme's primary CTA color) per the "no hardcoded mock colors"
        # constraint. The gradient (ACCENT -> ACCENT_PRESSED) gives the
        # same visual emphasis as the mock's orange gradient.
        # P3.28: in scene mode this button becomes GENERATE CHECKED —
        # selective generation of exactly the checked rows (§11/§12).
        self._start_btn = QPushButton("START BATCH")
        if self._scene is not None:
            self._start_btn.setText("GENERATE CHECKED")
        self._start_btn.clicked.connect(self._on_start)
        self._start_btn.setStyleSheet(
            "QPushButton {{"
            "  background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            "    stop:0 {accent}, stop:1 {accent_pressed});"
            "  color: {accent_text};"
            "  border: none;"
            "  border-radius: 5px;"
            "  padding: 10px 14px;"
            "  font-size: 13px;"
            "  font-weight: 700;"
            "  letter-spacing: 0.5px;"
            "}}"
            "QPushButton:hover {{"
            "  background-color: {accent_hover};"
            "}}"
            "QPushButton:disabled {{"
            "  background-color: {bg_surface};"
            "  color: {text_disabled};"
            "  border: 1px solid {border};"
            "}}".format(
                accent=Palette.ACCENT,
                accent_hover=Palette.ACCENT_HOVER,
                accent_pressed=Palette.ACCENT_PRESSED,
                accent_text=Palette.ACCENT_TEXT,
                bg_surface=Palette.BG_SURFACE,
                text_disabled=Palette.TEXT_DISABLED,
                border=Palette.BORDER))
        sl.addWidget(self._start_btn)

        # --- Pause + Stop side by side ---
        run_row = QHBoxLayout()
        run_row.setSpacing(6)
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._on_pause)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setStyleSheet(self._tool_button_style())
        run_row.addWidget(self._pause_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(self._tool_button_style(danger=True))
        run_row.addWidget(self._stop_btn)
        sl.addLayout(run_row)

        layout.addWidget(side, 3)  # ~30% of horizontal space

    # ------------------------------------------------------------------
    # Style helpers (P3.18 — extracted for reuse across _build_ui)
    # ------------------------------------------------------------------
    @staticmethod
    def _make_summary_line(label: str) -> QLabel:
        """Create a mono-font stat-line label.

        The text format is ``"<label>    <value>"`` (filled in by
        :meth:`_update_summary`). The muted color + JetBrains Mono font
        matches the mock's summaryCard lines.
        """
        lbl = QLabel(label)
        lbl.setStyleSheet(
            "color: {muted};"
            " font-family: 'JetBrains Mono','Consolas','Courier New',monospace;"
            " font-size: 11px;".format(muted=Palette.TEXT_SECONDARY))
        return lbl

    @staticmethod
    def _tool_button_style(danger: bool = False) -> str:
        """Standard toolbutton QSS for the sidebar buttons.

        ``danger=True`` tints the text + hover border with Palette.ERROR
        (used for the Stop button, matching the mock's red Stop).
        """
        color = Palette.ERROR if danger else Palette.TEXT_PRIMARY
        hover = Palette.ERROR if danger else Palette.ACCENT
        return (
            "QPushButton {{"
            "  background-color: {bg_raised};"
            "  color: {color};"
            "  border: 1px solid {border};"
            "  border-radius: 5px;"
            "  padding: 7px 12px;"
            "  font-size: 12px;"
            "}}"
            "QPushButton:hover {{"
            "  border-color: {hover};"
            "  background-color: {bg_surface_alt};"
            "}}"
            "QPushButton:disabled {{"
            "  background-color: {bg_surface};"
            "  color: {text_disabled};"
            "  border: 1px solid {border};"
            "}}".format(
                bg_raised=Palette.BG_RAISED,
                bg_surface=Palette.BG_SURFACE,
                bg_surface_alt=Palette.BG_SURFACE_ALT,
                color=color,
                border=Palette.BORDER,
                hover=hover,
                text_disabled=Palette.TEXT_DISABLED))

    @staticmethod
    def _tint_bg(color_hex: str) -> str:
        """Blend a status color toward Palette.BG_BASE for a soft pill bg.

        The result is a dark tinted background that lets the colored pill
        text pop without using a hardcoded hex value (the mock used
        #10251E for green, #2A2112 for amber, etc. — we derive similar
        values from the active theme palette).
        """
        try:
            c = QColor(color_hex)
            bg = QColor(Palette.BG_BASE)
            # 18% color + 82% bg — dark enough to read as a "tint", bright
            # enough to distinguish from the surrounding BG_SURFACE row.
            r = int(c.red()   * 0.18 + bg.red()   * 0.82)
            g = int(c.green() * 0.18 + bg.green() * 0.82)
            b = int(c.blue()  * 0.18 + bg.blue()  * 0.82)
            return QColor(r, g, b).name()
        except Exception:
            return Palette.BG_RAISED

    @staticmethod
    def _pill_style(color: str) -> str:
        """QSS for a colored status pill (rounded bg + colored text)."""
        bg = BatchGenerationDialog._tint_bg(color)
        return (
            "QLabel {{"
            "  color: {color};"
            "  background-color: {bg};"
            "  border-radius: 10px;"
            "  padding: 3px 8px;"
            "  font-size: 11px;"
            "  font-weight: 700;"
            "}}".format(color=color, bg=bg))

    # ------------------------------------------------------------------
    # Refresh helpers
    # ------------------------------------------------------------------
    def _on_manager_changed(self) -> None:
        self._refresh_table()
        self._update_buttons()
        self._update_summary()
        # P3.28: scene-derived surfaces (coverage header, generated
        # cells, combined outputs) follow every queue change.
        if self._scene is not None:
            self._update_coverage_header()
            self._update_start_button()
            self._refresh_combined_section()

    def _refresh_table(self) -> None:
        """Refresh the table — WITHOUT unnecessary destruction (P3.44 §1/§7/§8).

        Two paths, selected by the queue's STRUCTURE signature (the job
        objects' identities in order — BatchJob is a mutable dataclass
        the manager mutates in place, so a run's status/duration/output
        updates keep the same objects):

        * signature UNCHANGED → targeted in-place updates: text items,
          the status-pill cell (only when that job's status actually
          changed), the generated cell (only when its derived version
          signature changed), and the action band's enable states. NO
          row widget is ever destroyed → the current row selection,
          keyboard focus, scroll position, checkbox states and version
          badges all survive (§7 "the table should not appear to
          randomly lose focus").

        * signature CHANGED (jobs added/removed/moved, dialog reopen) →
          full rebuild with state preservation: current row (nearest
          surviving row when rows were removed), scroll position,
          checkbox states (slot-keyed in scene mode), and focus is
          returned to the table itself when a destroyed cell widget had
          it (never stolen to an unrelated widget).

        P3.18 visual redesign: pill-based Status column. P3.28: two new
        scene-mode columns — the per-row selection checkbox (column 1,
        §11/§12) and the DERIVED generated state with the lazy version
        popover (column 3, §4/Rec 22: "✓ Generated · N versions ▾" —
        one row per slot, NEVER a widget per version).
        """
        jobs = self._manager.jobs
        signature = tuple(map(id, jobs))
        if signature != self._rendered_signature:
            self._rendered_signature = signature
            self._rebuild_table(jobs)
        else:
            self._update_rows_in_place(jobs)

    def _rebuild_table(self, jobs) -> None:
        """Full table rebuild with interaction-state preservation."""
        # --- capture pre-rebuild interaction state (P3.44 §7) ---------
        prev_current = self._table.currentRow()
        vbar = self._table.verticalScrollBar()
        prev_scroll = vbar.value() if vbar is not None else 0
        had_table_focus = self._focus_in_table()

        self._table.setRowCount(len(jobs))
        self._row_refs = []
        running = self._manager.is_running
        for i, job in enumerate(jobs):
            # Row height 48px accommodates the pill widget + action buttons
            # (P3.40: 47px usable content after the 1px ::item border-bottom;
            # the 44px action-button band fits fully — no clipping).
            self._table.setRowHeight(i, 48)

            # Column 0 — "#" (zero-padded like the mock: 01, 02, ...)
            num_item = QTableWidgetItem("{0:02d}".format(i + 1))
            num_item.setTextAlignment(Qt.Alignment.AlignCenter)
            num_item.setForeground(QColor(Palette.TEXT_SECONDARY))
            self._table.setItem(i, self._col_num, num_item)

            # "[✓]" selection checkbox (scene mode only).
            # Preserved across rebuilds via _check_states (user choices
            # are never reset by a refresh).
            check_widget = None
            if self._scene_mode:
                check_widget = self._build_check_widget(i, job, running)
                self._table.setCellWidget(
                    i, self._col_check, check_widget)

            # "Name / Segment" — show the output filename
            # (the actual generated file name), falling back to job.name
            # or truncated prompt if no filename is available yet.
            name_item = QTableWidgetItem(self._display_name(job))
            if job.status == JobStatus.SKIPPED:
                # De-emphasize skipped rows at a glance.
                name_item.setForeground(QColor(Palette.TEXT_DISABLED))
            self._table.setItem(i, self._col_name, name_item)

            # "Generated" (derived state + versions ▾) — scene mode only.
            gen_widget = None
            if self._scene_mode:
                gen_widget = self._build_generated_widget(job)
                self._table.setCellWidget(i, self._col_gen, gen_widget)

            # "Status" (colored pill widget with icon).
            self._table.setCellWidget(
                i, self._col_status, self._build_status_widget(job))

            # Column 5 — "Duration" (output audio length, right-aligned).
            # P3.27B: an output-guard anomaly (e.g. a runaway generation
            # with a long silent tail) appends a visible ⚠ marker — the
            # audio is NEVER discarded; the user can keep, inspect (Play)
            # or regenerate the part (task §17).
            dur_item = QTableWidgetItem(self._duration_text(job))
            dur_item.setTextAlignment(
                Qt.Alignment.AlignRight | Qt.Alignment.AlignVCenter)
            anomaly = getattr(job, "anomaly", None)
            if anomaly:
                dur_item.setForeground(QColor(Palette.WARNING))
                dur_item.setToolTip(self._anomaly_tooltip(job, anomaly))
            else:
                dur_item.setForeground(QColor(Palette.TEXT_SECONDARY))
            self._table.setItem(i, self._col_dur, dur_item)

            # "Actions" (Play + Stop + Regen per-row buttons).
            actions = _RowActions(self, job, i, running)
            self._table.setCellWidget(i, self._col_act, actions)

            self._row_refs.append({
                "job": job,
                "status": job.status,
                "gen_sig": self._generated_cell_signature(job),
                "actions": actions,
                "check": getattr(check_widget, "_checkbox", None),
            })

        # --- restore the captured interaction state (P3.44 §7) --------
        if jobs and prev_current >= 0:
            # Nearest surviving row when rows were removed; NO selection
            # is invented when nothing was selected (prev_current -1).
            restore_row = prev_current if prev_current < len(jobs) \
                else len(jobs) - 1
            self._table.blockSignals(True)
            self._table.setCurrentCell(restore_row, 0)
            self._table.blockSignals(False)
        if vbar is not None:
            vbar.setValue(min(prev_scroll, vbar.maximum()))
        if had_table_focus and QApplication.focusWidget() is None:
            # The focused cell widget was destroyed by the rebuild —
            # return focus to the containing table (the least movement
            # possible; never steal to an unrelated widget).
            self._table.setFocus()

    def _update_rows_in_place(self, jobs) -> None:
        """Targeted update path — ZERO row-widget destruction (§8).

        Only the cells whose derived content changed are touched:
        name text, status pill (per-row, only on status change),
        duration text, the generated cell (only when its version
        signature changed — e.g. a new version landed or the user
        selected a different version), checkbox CHECK state (P3.44.1
        §1: synchronised with ``_check_states``, signals blocked) and
        enable state, and the action band's enable states via
        update_state().
        """
        running = self._manager.is_running
        for i, job in enumerate(jobs):
            refs = self._row_refs[i] if i < len(self._row_refs) else None
            if refs is None or refs.get("job") is not job:
                # Defensive: identity mismatch (should not happen — the
                # signature check guarantees alignment) → fall back to a
                # full rebuild.
                self._rendered_signature = None
                self._rebuild_table(jobs)
                return

            # 1. Name item (output filename appears when the job
            #    completes; skipped rows de-emphasize).
            item = self._table.item(i, self._col_name)
            if item is not None:
                display_name = self._display_name(job)
                if item.text() != display_name:
                    item.setText(display_name)
                fg = QColor(Palette.TEXT_DISABLED
                            if job.status == JobStatus.SKIPPED
                            else Palette.TEXT_PRIMARY)
                if item.foreground() != fg:
                    item.setForeground(fg)

            # 2. Status pill — replace ONLY when this job's status
            #    actually changed. The pill has no focusable children,
            #    so replacing it can never steal keyboard focus.
            if refs.get("status") is not job.status:
                self._replace_cell_widget(
                    i, self._col_status, self._build_status_widget(job))
                refs["status"] = job.status

            # 3. Duration item.
            dur_item = self._table.item(i, self._col_dur)
            if dur_item is not None:
                anomaly = getattr(job, "anomaly", None)
                new_text = self._duration_text(job)
                if dur_item.text() != new_text:
                    dur_item.setText(new_text)
                if anomaly:
                    dur_item.setForeground(QColor(Palette.WARNING))
                    dur_item.setToolTip(self._anomaly_tooltip(job, anomaly))
                else:
                    dur_item.setForeground(QColor(Palette.TEXT_SECONDARY))

            # 4. Generated cell (scene mode) — only when the slot's
            #    derived version state changed (new version, selection
            #    change, coverage change).
            if self._scene_mode:
                sig = self._generated_cell_signature(job)
                if sig != refs.get("gen_sig"):
                    self._replace_cell_widget(
                        i, self._col_gen, self._build_generated_widget(job))
                    refs["gen_sig"] = sig

            # 5. Checkbox (scene mode) — P3.44.1 §1: the VISIBLE check
            #    state is synchronised with the authoritative
            #    ``_check_states`` here. This was the D1 root cause: the
            #    in-place path only updated the ENABLE state, so
            #    All/None (which mutate the dict, then refresh through
            #    the SAME-structure in-place path) changed the internal
            #    selection while the visible QCheckBox stayed stale.
            #    setChecked is wrapped in blockSignals so the
            #    programmatic sync never re-enters _on_toggle (no
            #    recursive signal feedback).
            cb = refs.get("check")
            if cb is not None:
                want = bool(self._check_states.get(i, False))
                if cb.isChecked() != want:
                    cb.blockSignals(True)
                    cb.setChecked(want)
                    cb.blockSignals(False)
                # Disabled while running, enabled when idle.
                if cb.isEnabled() == running:
                    cb.setEnabled(not running)

            # 6. Action band enable states (Play follows the
            #    authoritative asset state, not the job status).
            actions = refs.get("actions")
            if actions is not None:
                actions.update_state(job, running)

    def _replace_cell_widget(self, row: int, col: int, new_widget) -> None:
        """Replace ONE cell widget, preserving keyboard focus (P3.44 §7).

        If the currently focused widget lives inside the OLD cell widget
        (e.g. the versions ▾ button), focus moves to the NEW widget's
        first button — the same control, same position — so the user's
        keyboard context survives the replacement instead of being
        dropped to the dialog.
        """
        old = self._table.cellWidget(row, col)
        refocus = False
        if old is not None:
            fw = QApplication.focusWidget()
            w = fw
            while w is not None:
                if w is old:
                    refocus = True
                    break
                w = w.parentWidget()
        self._table.setCellWidget(row, col, new_widget)
        if refocus:
            target = None
            for child in new_widget.findChildren(QPushButton):
                target = child
                break
            (target if target is not None else self._table).setFocus()

    def _focus_in_table(self) -> bool:
        """Is the keyboard focus currently inside the table (incl. its
        cell widgets)?"""
        fw = QApplication.focusWidget()
        w = fw
        while w is not None:
            if w is self._table:
                return True
            w = w.parentWidget()
        return False

    def _display_name(self, job: BatchJob) -> str:
        """The Name column text (output filename → job name → prompt)."""
        if job.output_path:
            return os.path.basename(job.output_path)
        elif job.output_filename:
            return job.output_filename
        return job.name or job.prompt[:40]

    def _duration_text(self, job: BatchJob) -> str:
        """The Duration column text (with the P3.27B anomaly marker)."""
        anomaly = getattr(job, "anomaly", None)
        if job.output_duration > 0:
            dur_text = "{0:.2f}s".format(job.output_duration)
            if anomaly:
                dur_text += " \u26a0"   # ⚠ warning marker
            return dur_text
        return "\u2014"  # em dash for "no duration yet"

    def _generated_cell_signature(self, job):
        """The derived-state signature of a row's Generated cell.

        Used by the in-place update path to decide whether the cell
        needs rebuilding: it changes when a new version is registered,
        the explicit selection changes, or coverage flips. The signature
        is derived from the SAME authoritative provenance functions the
        cell renders (§10: one resolver everywhere).
        """
        if (not self._scene_mode or self._scene is None
                or not getattr(job, "slot_id", None)):
            return ("none",)
        from engine.audio_provenance import (
            slot_versions, resolved_asset_for_slot,
        )
        versions = slot_versions(self._scene, job.slot_id)
        resolved = resolved_asset_for_slot(self._scene, job.slot_id)
        selected = (getattr(self._scene, "selected_block_audio", None)
                    or {}).get(job.slot_id)
        return (len(versions),
                (resolved or {}).get("id"),
                bool(selected))

    # ------------------------------------------------------------------
    # P3.44: authoritative playback resolution (§3/§9/§11/§13)
    # ------------------------------------------------------------------
    def _resolve_playable(self, job: BatchJob) -> str:
        """The AUTHORITATIVE playback path for a row (P3.44 §3/§9).

        Scene mode: the Scene provenance resolver (explicit selection,
        else latest) — NEVER BatchJob.output_path. §13: the job's
        output_path stays queue-execution state; paths are NEVER copied
        into it as a hidden mirror of provenance.
        Manual mode: the job's own output_path (no Scene provenance
        exists there — §9: the two resolution paths stay independent).

        Returns an ABSOLUTE path (resolved against the dialog's app
        root, which MainWindow passes as the ENGINE's root), or "" when
        nothing playable exists.
        """
        if (self._scene_mode and self._scene is not None
                and getattr(job, "slot_id", None)):
            from engine.audio_provenance import resolved_asset_for_slot
            asset = resolved_asset_for_slot(self._scene, job.slot_id)
            if not asset:
                return ""
            rel = str(asset.get("output_path") or "")
            if not rel:
                return ""
            return rel if os.path.isabs(rel) \
                else os.path.join(self._app_root, rel)
        rel = job.output_path or ""
        if not rel:
            return ""
        return rel if os.path.isabs(rel) \
            else os.path.join(self._app_root, rel)

    def _play_available(self, job: BatchJob) -> bool:
        """Is a playable audio source available for this row?

        Scene mode (§11): derived from the authoritative Scene asset —
        the file must EXIST (§5: validated before enabling; a missing
        file is a clear unavailable state, never a silent fallback).
        Independent of BatchJob.status (queue execution state and
        generated asset state are separate concepts).
        Manual mode (§9): COMPLETED + output_path (the legacy contract)
        plus the same file-exists validation.
        """
        if (self._scene_mode and self._scene is not None
                and getattr(job, "slot_id", None)):
            path = self._resolve_playable(job)
            return bool(path) and os.path.isfile(path)
        if job.status != JobStatus.COMPLETED or not job.output_path:
            return False
        path = self._resolve_playable(job)
        return bool(path) and os.path.isfile(path)

    def _play_tooltip(self, job: BatchJob) -> str:
        """The Play button tooltip — explains WHAT will play (§3) or why
        nothing can (§5), derived from the same resolver."""
        if (self._scene_mode and self._scene is not None
                and getattr(job, "slot_id", None)):
            from engine.audio_provenance import resolved_asset_for_slot
            asset = resolved_asset_for_slot(self._scene, job.slot_id)
            if not asset:
                return ("Play this part's audio\n"
                        "(not generated yet — generate it first)")
            rel = str(asset.get("output_path") or "")
            abs_path = rel if (os.path.isabs(rel) or not rel) \
                else os.path.join(self._app_root, rel)
            try:
                v = int(asset.get("part_version") or 0)
            except (TypeError, ValueError):
                v = 0
            selected = (getattr(self._scene, "selected_block_audio", None)
                        or {}).get(job.slot_id)
            which = "explicitly selected" if selected else "latest"
            if abs_path and not os.path.isfile(abs_path):
                return ("Audio file missing:\n{0}\n\n"
                        "The selected version stays selected (provenance\n"
                        "is preserved); restore the file or regenerate\n"
                        "the part.".format(os.path.basename(abs_path)))
            return ("Play this part's audio\n"
                    "(plays v{0:02d} — the {1} version for this slot)".format(
                        v, which))
        return "Play this part's audio"

    # ------------------------------------------------------------------
    # P3.28 row-widget builders: selection checkbox + generated state
    # ------------------------------------------------------------------
    def _build_check_widget(self, idx: int, job: BatchJob,
                            running: bool) -> QWidget:
        """Column 1 — the per-row selective-generation checkbox.

        Default state comes from _init_check_defaults (§11: only
        not-generated slots checked); user toggles are stored in
        _check_states (P3.44: keyed by slot id in scene mode) and
        survive table rebuilds. Disabled while the batch runs.
        """
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.Alignment.AlignCenter)
        checkbox = QCheckBox()
        checkbox.setChecked(bool(self._check_states.get(idx, False)))
        checkbox.setEnabled(not running)
        checkbox.setToolTip(
            "Include this part in the next generation.\n"
            "Checking an already-generated part regenerates it as a NEW\n"
            "VERSION — the existing audio is never overwritten.")

        def _on_toggle(checked, _idx=idx):
            self._check_states[_idx] = bool(checked)
            self._update_start_button()

        checkbox.toggled.connect(_on_toggle)
        layout.addWidget(checkbox)
        # P3.44: expose the checkbox for in-place enable-state updates.
        widget._checkbox = checkbox
        return widget

    def _build_generated_widget(self, job: BatchJob) -> QWidget:
        """Column 3 — the DERIVED generated state + lazy version popover.

        "✓ Generated · N versions ▾" or "— Not generated" (P3.28 §4).
        The ▾ button opens a lazily-built QMenu listing each version
        (v01 · 12.4s · filename) with a "Use" action — never a widget
        per version (§24/Rec 22). No slot provenance (manual job) → "—".
        """
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(4)
        # P3.40: explicit vertical centring (never rely on layout defaults).
        layout.setAlignment(Qt.Alignment.AlignVCenter)
        slot_id = getattr(job, "slot_id", None)
        if not slot_id or self._scene is None:
            label = QLabel("\u2014")
            label.setStyleSheet("color: {0};".format(Palette.TEXT_DISABLED))
            layout.addWidget(label)
            layout.addStretch()
            return widget
        from engine.audio_provenance import (
            slot_versions, resolved_asset_for_slot, block_slot_id,
        )
        versions = slot_versions(self._scene, slot_id)
        resolved = resolved_asset_for_slot(self._scene, slot_id)
        if not versions:
            label = QLabel("\u2014 Not generated")
            label.setStyleSheet("color: {0};".format(Palette.TEXT_DISABLED))
            layout.addWidget(label)
            layout.addStretch()
            return widget
        # ✓ Generated · N versions ▾
        state_label = QLabel("\u2713 Generated")
        state_label.setStyleSheet(
            "color: {0}; font-weight: 700;".format(Palette.SUCCESS))
        layout.addWidget(state_label)
        versions_btn = QPushButton(
            "\u00b7 {0} version{1} \u25be".format(
                len(versions), "" if len(versions) == 1 else "s"))
        versions_btn.setFlat(True)
        versions_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        versions_btn.setStyleSheet(
            "QPushButton {{ color: {accent}; border: none;"
            " padding: 3px 8px; font-size: 12px; font-weight: 600; }}"
            "QPushButton:hover {{ color: {hover}; }}".format(
                accent=Palette.ACCENT, hover=Palette.ACCENT_HOVER))
        versions_btn.clicked.connect(
            lambda _checked=False, _slot=slot_id, _btn=versions_btn:
            self._show_versions_menu(_slot, _btn))
        layout.addWidget(versions_btn)
        # Per-slot resolved version badge (explicit selection marker).
        selected = (self._scene.selected_block_audio or {}).get(slot_id)
        if selected and resolved is not None:
            try:
                v = int(resolved.get("part_version") or 0)
            except (TypeError, ValueError):
                v = 0
            badge = QLabel("\u00b7 v{0:02d} selected".format(v))
            badge.setStyleSheet("color: {0};".format(Palette.WARNING))
            badge.setToolTip(
                "An explicitly selected version feeds Combine and the\n"
                "version list (an explicit user action — never automatic).")
            layout.addWidget(badge)
        layout.addStretch()
        return widget

    def _show_versions_menu(self, slot_id: str, anchor_btn=None) -> None:
        """Lazy version popover for a slot (P3.28 §4/Rec 22).

        P3.44 §4: every existing version is PLAYABLE from the list
        (read-only preview) AND selectable (Use). Building is split into
        _build_versions_menu (no exec — inspectable/testable); this
        method only anchors + executes.

        P3.30(c): the menu is anchored ABOVE the "· N versions ▾" button
        (previously ``menu.exec()`` without a position — frameless
        top-left corner popup on Windows).
        """
        menu = self._build_versions_menu(slot_id)
        anchor = anchor_btn or self
        _popup_menu_above(menu, anchor)

    def _build_versions_menu(self, slot_id: str) -> QMenu:
        """Build the version popover WITHOUT executing it (P3.44 §4).

        One _VersionMenuRow per version: "v01 · 12.4s · filename"
        with [▶ Play] (read-only preview — never a Scene mutation) and
        [Use] (the provenance selection mutation — never plays). Plus
        "Latest (default)" to clear an explicit selection. The menu is
        returned, NOT executed — _show_versions_menu executes it; tests
        inspect the rows directly.

        Missing files (§5): the affected version's Play button is
        disabled with an explanatory tooltip and the label carries a
        "⚠ file missing" marker — the version stays listed (provenance
        preserved), no silent fallback to another version.
        """
        from engine.audio_provenance import (
            slot_versions, resolved_asset_for_slot,
        )
        versions = slot_versions(self._scene, slot_id)
        resolved = resolved_asset_for_slot(self._scene, slot_id)
        resolved_id = resolved.get("id") if resolved else None
        menu = QMenu(self)
        menu.setWindowTitle("Versions")
        for entry in versions:
            rel = str(entry.get("output_path") or "")
            abs_path = rel if (os.path.isabs(rel) or not rel) \
                else os.path.join(self._app_root, rel)
            file_exists = bool(abs_path) and os.path.isfile(abs_path)
            row = _VersionMenuRow(
                entry, is_current=(entry.get("id") == resolved_id),
                abs_path=abs_path, file_exists=file_exists)
            action = QWidgetAction(menu)
            action.setDefaultWidget(row)
            menu.addAction(action)
            # §4: Play = read-only preview (same transport path as the
            # row Play button — ONE playback mechanism, §13); the menu
            # STAYS OPEN so the user can audition several versions.
            row.play_clicked.connect(
                lambda p: self.play_requested.emit(p))
            # §4: Use = selection mutation only (never plays). It closes
            # the menu (the choice is made); MainWindow owns the Scene
            # mutation and refreshes the dialog via refresh_scene_context.
            row.use_clicked.connect(
                lambda _a, _s=slot_id: self.use_version_requested.emit(_s, _a))
            row.use_clicked.connect(lambda: menu.close())
        menu.addSeparator()
        clear_action = menu.addAction("Latest version (default)")
        clear_action.triggered.connect(
            lambda _checked=False, _s=slot_id:
            self.use_version_requested.emit(_s, ""))
        return menu

    # ------------------------------------------------------------------
    # P3.28: Scene combined outputs section (§13–§15)
    # ------------------------------------------------------------------
    def _refresh_combined_section(self) -> None:
        """Rebuild the SCENE COMBINED OUTPUTS section (scene mode only).

        Shows the Scene's resolved output (the ONE thing Project
        Assembly consumes — §15) and one row per combined output with
        STALE badges and "Use as Scene output" selection.
        """
        if (self._scene is None
                or not hasattr(self, "_combined_rows_layout")):
            return
        from engine.audio_provenance import (
            resolve_scene_output, is_scene_combined_stale,
            KIND_SCENE_COMBINED, KIND_ASSET,
        )
        # --- resolved output line ---
        resolution = resolve_scene_output(self._scene, self._app_root)
        if resolution.get("requires_review"):
            res_html = (
                "<span style='color:{warn};'>Scene output: REVIEW "
                "REQUIRED — {reason}</span>").format(
                    warn=Palette.WARNING,
                    reason=resolution.get("reason", ""))
        else:
            color = (Palette.WARNING if resolution.get("stale")
                     else Palette.SUCCESS)
            stale_note = (" \u00b7 STALE"
                          if resolution.get("stale") else "")
            res_html = (
                "Scene output: <b>{label}</b>{stale}"
                " <span style='color:{muted};'>({path})</span>").format(
                    label=resolution.get("label", ""),
                    stale=("<span style='color:{0};'>".format(color)
                           + stale_note + "</span>") if stale_note else "",
                    muted=Palette.TEXT_SECONDARY,
                    path=os.path.basename(
                        str(resolution.get("rel_path") or "")))
        self._resolved_output_label.setText(res_html)
        # --- combined output rows (newest first; ONE row per output) ---
        while self._combined_rows_layout.count():
            item = self._combined_rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        entries = [e for e in (self._scene.combined_outputs or [])
                   if isinstance(e, dict)]
        entries.sort(key=lambda e: _safe_int(e.get("version")),
                     reverse=True)
        selected = (self._scene.selected_output or {})
        for entry in entries:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            try:
                v = int(entry.get("version") or 0)
            except (TypeError, ValueError):
                v = 0
            stale, reasons = is_scene_combined_stale(
                self._scene, entry, self._app_root)
            text = "Combined v{0:02d} \u00b7 {1:.1f}s".format(
                v, float(entry.get("duration") or 0.0))
            label = QLabel(text)
            label.setStyleSheet("color: {0}; font-size: 11px;".format(
                Palette.TEXT_PRIMARY))
            row_layout.addWidget(label)
            if stale:
                stale_badge = QLabel("STALE")
                stale_badge.setStyleSheet(
                    "color: {0}; background-color: {1}; border-radius: 7px;"
                    " padding: 1px 6px; font-size: 10px; font-weight: 700;"
                    .format(Palette.WARNING,
                            self._tint_bg(Palette.WARNING)))
                stale_badge.setToolTip("\n".join(reasons) or "stale")
                row_layout.addWidget(stale_badge)
            is_selected = (selected.get("kind") == KIND_SCENE_COMBINED
                           and selected.get("id") == entry.get("id"))
            use_btn = QPushButton(
                "\u2713 selected" if is_selected else "Use as output")
            use_btn.setMinimumHeight(28)
            use_btn.setStyleSheet(
                "QPushButton {{ color: {accent}; border: 1px solid"
                " {border}; border-radius: 4px; padding: 4px 10px;"
                " font-size: 12px; background-color: {bg}; }}"
                "QPushButton:hover {{ border-color: {accent}; }}"
                "QPushButton:disabled {{ color: {success};"
                " border-color: {success}; background-color: {tint}; }}"
                .format(accent=Palette.ACCENT, border=Palette.BORDER,
                        bg=Palette.BG_RAISED,
                        success=Palette.SUCCESS,
                        tint=self._tint_bg(Palette.SUCCESS)))
            use_btn.setEnabled(not is_selected)
            use_btn.setToolTip(
                "Make this combined output the Scene's explicit output\n"
                "for Project Assembly (an explicit user selection — a\n"
                "STALE one requires confirmation and is clearly flagged).")
            entry_id = entry.get("id") or ""
            use_btn.clicked.connect(
                lambda _checked=False, _id=entry_id:
                self.select_output_requested.emit(KIND_SCENE_COMBINED, _id))
            row_layout.addWidget(use_btn)
            row_layout.addStretch()
            self._combined_rows_layout.addWidget(row)
        if not entries:
            empty = QLabel("No combined outputs yet — press Combine Scene.")
            empty.setStyleSheet("color: {0}; font-size: 11px;".format(
                Palette.TEXT_DISABLED))
            self._combined_rows_layout.addWidget(empty)
        # "Clear selection" row when an explicit selection exists.
        if selected:
            clear_row = QWidget()
            clear_layout = QHBoxLayout(clear_row)
            clear_layout.setContentsMargins(0, 0, 0, 0)
            clear_btn = QPushButton("\u00d7 Clear explicit selection "
                                    "(use automatic resolution)")
            clear_btn.setFlat(True)
            clear_btn.setStyleSheet(
                "color: {0}; padding: 4px 6px; font-size: 12px;".format(
                    Palette.TEXT_SECONDARY))
            clear_btn.clicked.connect(
                lambda _checked=False:
                self.select_output_requested.emit("", ""))
            clear_layout.addWidget(clear_btn)
            clear_layout.addStretch()
            self._combined_rows_layout.addWidget(clear_row)

    def _on_select_all(self) -> None:
        """Check every part (generate everything)."""
        for idx in range(len(self._manager.jobs)):
            self._check_states[idx] = True
        self._refresh_table()
        self._update_start_button()

    def _on_select_none(self) -> None:
        """Uncheck every part."""
        for idx in range(len(self._manager.jobs)):
            self._check_states[idx] = False
        self._refresh_table()
        self._update_start_button()

    def _on_export_audio(self) -> None:
        """P3.28 §17: Export Audio… scope menu (copy-only export).

        P3.30(b): the scope menu is anchored ABOVE the Export Audio
        button (previously ``menu.exec()`` without a position — frameless
        top-left corner popup on Windows).
        """
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        # P3.44: derive the CHECKED row indices from the CURRENT job
        # order (slot-keyed states survive index shifts).
        checked = self._checked_indices()
        act_sel = menu.addAction(
            "Selected rows ({0})".format(len(checked)))
        act_parts = menu.addAction("All generated parts")
        act_combined = menu.addAction("Scene combined outputs")
        act_every = menu.addAction("Everything in this batch")
        anchor = getattr(self, "_export_btn", None) or self
        chosen = _popup_menu_above(menu, anchor)
        if chosen is act_sel:
            self.export_audio_requested.emit("selected", checked)
        elif chosen is act_parts:
            self.export_audio_requested.emit("parts", [])
        elif chosen is act_combined:
            self.export_audio_requested.emit("combined", [])
        elif chosen is act_every:
            self.export_audio_requested.emit("everything", [])

    # ------------------------------------------------------------------
    # Row-widget builders (P3.18 — extracted from the old _refresh_table)
    # ------------------------------------------------------------------
    def _build_status_widget(self, job: BatchJob) -> QWidget:
        """Build a colored pill widget for the Status column.

        Pill variants (all using Palette colors, not mock hex values):
            COMPLETED  → green pill + check_circle icon ("Done")
            GENERATING → amber pill + AnimatedHourglass      ("Gen")
            PENDING    → gray pill + schedule icon           ("Queued")
            FAILED     → red pill   + error icon             ("Error")
            SKIPPED    → red pill   + stop icon              ("Stopped")

        The widget also carries a tooltip merging the old Output and
        Error columns (so the information is one hover away, without
        cluttering the table).
        """
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(5)
        layout.setAlignment(Qt.Alignment.AlignCenter)

        status = job.status
        icon_label: Optional[QLabel] = None
        pill_text = "Queued"
        pill_color = Palette.TEXT_SECONDARY

        if status == JobStatus.GENERATING:
            # Animated spinning hourglass replaces the static icon.
            hourglass = AnimatedHourglass()
            hourglass.start()
            layout.addWidget(hourglass)
            pill_text = "Gen"
            pill_color = Palette.WARNING
        elif status == JobStatus.COMPLETED:
            icon_label = QLabel()
            icon_label.setPixmap(
                IconRegistry.icon("check_circle", size=14,
                                  color=Palette.SUCCESS).pixmap(QSize(14, 14)))
            pill_text = "Done"
            pill_color = Palette.SUCCESS
        elif status == JobStatus.FAILED:
            icon_label = QLabel()
            icon_label.setPixmap(
                IconRegistry.icon("error", size=14,
                                  color=Palette.ERROR).pixmap(QSize(14, 14)))
            pill_text = "Error"
            pill_color = Palette.ERROR
        elif status == JobStatus.SKIPPED:
            icon_label = QLabel()
            icon_label.setPixmap(
                IconRegistry.icon("stop", size=14,
                                  color=Palette.ERROR).pixmap(QSize(14, 14)))
            pill_text = "Stopped"
            pill_color = Palette.ERROR
        else:  # PENDING → "Queued"
            icon_label = QLabel()
            icon_label.setPixmap(
                IconRegistry.icon("schedule", size=14,
                                  color=Palette.TEXT_SECONDARY
                                  ).pixmap(QSize(14, 14)))
            pill_text = "Queued"
            pill_color = Palette.TEXT_SECONDARY

        if icon_label is not None:
            layout.addWidget(icon_label)

        pill = QLabel(pill_text)
        pill.setStyleSheet(self._pill_style(pill_color))
        layout.addWidget(pill)

        # Merge old Output + Error columns into the pill tooltip.
        tips = []
        if job.error:
            tips.append("Error: " + job.error)
        if job.output_path:
            tips.append("Output: " + os.path.basename(job.output_path))
        # P3.27B: surface the output-guard verdict in the pill tooltip too.
        anomaly = getattr(job, "anomaly", None)
        if anomaly:
            tips.append("Warning: unusually long output — see the "
                        "Duration cell tooltip for details")
        if tips:
            widget.setToolTip("\n".join(tips))
        return widget

    def _anomaly_tooltip(self, job: BatchJob,
                         anomaly: dict) -> str:
        """P3.27B: tooltip text for an anomalous generation output.

        Shows the guard's reasons plus the measured speech/silence
        breakdown, and states explicitly that the audio was kept (the
        user decides: keep / inspect / regenerate).
        """
        analysis = anomaly.get("analysis", {}) if isinstance(anomaly, dict) else {}
        lines = ["Warning: unusually long output"]
        for reason in (anomaly.get("reasons", [])
                       if isinstance(anomaly, dict) else []):
            lines.append("  • " + str(reason))
        if analysis:
            lines.append(
                "Measured: {0:.1f}s total, {1:.1f}s speech, "
                "{2:.1f}s trailing silence".format(
                    float(analysis.get("total_s", 0.0) or 0.0),
                    float(analysis.get("speech_s", 0.0) or 0.0),
                    float(analysis.get("trailing_silence_s", 0.0) or 0.0)))
        lines.append("")
        lines.append("The audio file was kept — nothing was deleted.")
        lines.append("You can keep it, inspect it (Play), or regenerate "
                     "this part (Regen button).")
        return "\n".join(lines)

    # P3.44: the per-row Play/Stop/Regen band is now the _RowActions
    # class (module level) — built by _rebuild_table and UPDATED IN
    # PLACE by _update_rows_in_place. The old ad-hoc
    # _build_action_widget (rebuilt every refresh; Play bound to
    # BatchJob.output_path + COMPLETED, which left previously generated
    # Scene audio unplayable after a reopen — the P3.44 defect) was
    # removed.

    @staticmethod
    def _status_color(status: JobStatus) -> QColor:
        """Map a JobStatus to its canonical Palette color.

        Retained for backwards compatibility and used by the status-pill
        builder as the single source of truth for status → color mapping.
        """
        try:
            if status == JobStatus.COMPLETED:
                return QColor(Palette.SUCCESS)
            if status == JobStatus.FAILED:
                return QColor(Palette.ERROR)
            if status == JobStatus.GENERATING:
                return QColor(Palette.WARNING)
            if status == JobStatus.SKIPPED:
                return QColor(Palette.ERROR)
            if status == JobStatus.PENDING:
                return QColor(Palette.TEXT_SECONDARY)
        except Exception:
            pass
        return QColor(Palette.TEXT_PRIMARY)

    def _update_buttons(self) -> None:
        running = self._manager.is_running
        paused = self._manager.is_paused
        stopping = running and not paused and getattr(
            self._manager, "stop_requested", False)
        self._start_btn.setEnabled(not running or paused)
        self._pause_btn.setEnabled(running and not paused)
        self._stop_btn.setEnabled(running and not stopping)
        # P3.30(d): a visible "Stopping…" state — the queue is ending; a
        # generation already inside the model call must run out (the
        # model API has no mid-call interruption hook), so the button
        # explains the wait instead of looking dead.
        self._stop_btn.setText("Stopping\u2026" if stopping else "Stop")
        # Job editing is disabled while running.
        editable = not running
        for btn in (self._add_btn, self._edit_btn, self._remove_btn,
                    self._dup_btn, self._up_btn, self._down_btn,
                    self._save_btn, self._load_btn, self._clear_btn):
            btn.setEnabled(editable)
        # Merge Completed Parts (manual queue only — scene mode has
        # Combine Scene) is enabled when not running AND at least 2
        # COMPLETED jobs exist (merging 0 or 1 part is pointless).
        completed_count = sum(
            1 for j in self._manager.jobs if j.status == JobStatus.COMPLETED)
        if hasattr(self, "_concat_btn"):
            self._concat_btn.setEnabled(not running and completed_count >= 2)
        # P3.28: Combine Scene + Export Audio (scene mode only) — enabled
        # when the queue is idle. The combine handler blocks with a clear
        # message when slots are uncovered (no silent skipping).
        if self._scene is not None:
            slots = [s for s in (getattr(self._scene, "expected_audio_slots",
                                         None) or []) if isinstance(s, dict)]
            if hasattr(self, "_combine_btn"):
                self._combine_btn.setEnabled(
                    not running and bool(slots))
            if hasattr(self, "_export_btn"):
                self._export_btn.setEnabled(
                    not running and (completed_count >= 1
                                     or bool(self._scene.combined_outputs)))

    def _update_summary(self) -> None:
        """Populate the QUEUE SUMMARY sidebar widgets.

        Replaces the old single multi-line HTML summary label with the
        new sidebar layout: progress headline + thin progress bar + four
        mono-font stat lines (Generated Audio / RTF / Wall Time / State).

        Counts (total / completed / failed / skipped / audio total) are
        computed from the manager's CURRENT job list so the sidebar
        always reflects what the user sees in the table — even before a
        batch has been started (the old code read from
        ``manager.summary`` which is only populated during a run, so a
        freshly-loaded queue showed "Queue is empty."). Run-level
        statistics (RTF, wall time) still come from ``manager.summary``
        because they only make sense in the context of a finished run.

        Method signature unchanged.
        """
        jobs = self._manager.jobs
        total = len(jobs)
        completed = sum(1 for j in jobs if j.status == JobStatus.COMPLETED)
        failed    = sum(1 for j in jobs if j.status == JobStatus.FAILED)
        skipped   = sum(1 for j in jobs if j.status == JobStatus.SKIPPED)
        audio_total = sum(j.output_duration for j in jobs
                          if j.status == JobStatus.COMPLETED)

        if total == 0:
            self._progress_text.setText("Idle")
            self._progress.setValue(0)
            self._audio_label.setText("Generated Audio    \u2014")
            self._rtf_label.setText("Real Time Factor    \u2014")
            self._wall_label.setText("Wall Time    \u2014")
            self._state_label.setText("State    Idle")
            return

        # Paused takes precedence over Running (visual improvement: the
        # mock shows "Paused" when paused, even if the batch loop is
        # still technically running underneath).
        if self._manager.is_paused:
            state = "Paused"
        elif self._manager.is_running:
            state = "Running"
        elif (completed + failed + skipped) == total:
            state = "Finished"
        else:
            state = "Idle"

        done = completed + failed + skipped
        self._progress_text.setText(
            "Progress    {done}/{total} completed".format(
                done=done, total=total))
        self._progress.setValue(int(round((done / total) * 100)))
        self._audio_label.setText(
            "Generated Audio    {0:.2f}s".format(audio_total))

        # RTF + wall time come from BatchSummary (only meaningful after a
        # run; show an em dash when no run data is available yet).
        s = self._manager.summary
        if s.total_wall_seconds > 0:
            self._rtf_label.setText(
                "Real Time Factor    {0:.2f} RTF".format(s.realtime_factor))
            self._wall_label.setText(
                "Wall Time    {0:.1f}s".format(s.total_wall_seconds))
        else:
            self._rtf_label.setText("Real Time Factor    \u2014")
            self._wall_label.setText("Wall Time    \u2014")
        self._state_label.setText("State    {0}".format(state))

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------
    def _selected_row(self) -> Optional[int]:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        return rows[0].row()

    def _on_add(self) -> None:
        job = BatchJob(
            name="New Job",
            prompt="",
            parameters=GenerationParameters(),
        )
        dlg = JobEditDialog(job, self._voices, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.apply_to_job()
            self._manager.add_job(job)
            self._refresh_table()

    def _on_edit(self) -> None:
        idx = self._selected_row()
        if idx is None:
            return
        jobs = self._manager.jobs
        if idx >= len(jobs):
            return
        # Edit a *copy* so the user can cancel without side-effects.
        original = jobs[idx]
        import dataclasses
        # Deep-ish copy via dict round-trip
        from engine.batch_manager import BatchJob as _BJ
        clone = _BJ.from_dict(original.to_dict())
        dlg = JobEditDialog(clone, self._voices, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.apply_to_job()
            # Replace the original in-place
            self._manager.remove_job(idx)
            self._manager._jobs.insert(idx, clone)  # noqa: SLF001
            self._refresh_table()

    def _on_remove(self) -> None:
        idx = self._selected_row()
        if idx is None:
            return
        self._manager.remove_job(idx)
        self._refresh_table()

    def _on_duplicate(self) -> None:
        idx = self._selected_row()
        if idx is None:
            return
        self._manager.duplicate_job(idx)
        self._refresh_table()

    def _on_move_up(self) -> None:
        idx = self._selected_row()
        if idx is None or idx == 0:
            return
        self._manager.move_job(idx, idx - 1)
        self._refresh_table()
        self._table.selectRow(idx - 1)

    def _on_move_down(self) -> None:
        idx = self._selected_row()
        if idx is None:
            return
        jobs = self._manager.jobs
        if idx >= len(jobs) - 1:
            return
        self._manager.move_job(idx, idx + 1)
        self._refresh_table()
        self._table.selectRow(idx + 1)

    def _on_clear(self) -> None:
        if not self._manager.jobs:
            return
        reply = QMessageBox.question(
            self, "Clear Queue",
            "Remove all jobs from the queue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._manager.clear_all()
            self._refresh_table()

    def _on_save_queue(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Batch Queue",
            "batch_queue.yaml",
            "YAML Files (*.yaml *.yml);;JSON Files (*.json);;All Files (*)")
        if not path:
            return
        try:
            self._manager.save_to_file(path)
            FeedbackDialog.information(self, "Saved",
                                       "Batch queue saved",
                                       "Location: {0}".format(path))
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed",
                                 "Could not save the queue: {0}".format(exc))

    def _on_load_queue(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Batch Queue",
            "", "YAML Files (*.yaml *.yml);;JSON Files (*.json);;All Files (*)")
        if not path:
            return
        try:
            n = self._manager.load_from_file(path, replace=True)
            FeedbackDialog.information(
                self, "Loaded", "Batch queue loaded",
                "{0} jobs loaded from:\n{1}".format(n, path))
        except Exception as exc:
            QMessageBox.critical(self, "Load Failed",
                                 "Could not load the queue: {0}".format(exc))
        self._refresh_table()

    def _on_start(self) -> None:
        if not self._manager.jobs:
            QMessageBox.information(self, "Empty Queue",
                                    "Add at least one job before starting.")
            return
        # P3.28 §11–§12 (P3.44.4 semantics): scene mode = SELECTIVE
        # generation — exactly the checked rows. MainWindow allocates
        # fresh versions for covered checked slots, resets the checked
        # jobs, and runs the SAME pipeline with the checked set as the
        # EXECUTION RUN (BatchManager.start(only=...)): unchecked jobs
        # are untouched (never scheduled, never flipped Stopped by this
        # run's Stop). Legacy manual mode = plain START BATCH (the whole
        # queue is the run).
        if self._scene is not None:
            checked = self._checked_indices()
            if not checked:
                QMessageBox.information(
                    self, "Nothing Selected",
                    "No parts are checked.\n\nCheck at least one part to "
                    "generate (use the All button to check every part).")
                return
            self.generate_selected_requested.emit(checked)
            # Force immediate button refresh — don't wait for the
            # marshal_to_ui callback (which can be delayed).
            self._update_buttons()
            self._refresh_table()
            self._update_summary()
            return
        # P3.45.2A — manual-mode execution-run preflight. The whole
        # queue is the run (start(reset_failed=True) resets SKIPPED/
        # FAILED to PENDING), so every job that would execute is checked
        # against its CURRENT prompt + CURRENT parameters through the
        # single source in engine/output_guard. One confirmation listing
        # the affected jobs; declining starts NOTHING (no job state,
        # order, parameters or filenames are touched).
        try:
            from engine.batch_manager import JobStatus
            from engine.output_guard import (
                preflight_generation_size, preflight_summary_line,
                PREFLIGHT_BLOCKED,
            )
            runnable = [j for j in self._manager.jobs
                        if j.status in (JobStatus.PENDING, JobStatus.SKIPPED,
                                        JobStatus.FAILED)]
            flagged = []
            any_blocked = False
            for j in runnable:
                verdict = preflight_generation_size(
                    text=j.prompt,
                    max_new_tokens=j.parameters.max_new_tokens,
                )
                if verdict["state"] != "safe":
                    line = preflight_summary_line(
                        verdict, j.name or j.prompt[:40])
                    if line:
                        flagged.append(line)
                if verdict["state"] == PREFLIGHT_BLOCKED:
                    any_blocked = True
            if any_blocked:
                reply = QMessageBox.question(
                    self, "Oversized Jobs",
                    "{0} of {1} jobs exceed the single-output generation "
                    "limit:\n\n  {2}\n\n"
                    "A single generation cannot produce more audio than "
                    "the token budget allows — affected outputs would be "
                    "cut at the limit, likely mid-speech.\n\n"
                    "Start the batch anyway?".format(
                        len(flagged), len(runnable), "\n  ".join(flagged)),
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return
        except Exception:
            pass
        ok = self._manager.start()
        if not ok:
            QMessageBox.information(self, "Already Running",
                                    "The batch is already running.")
            return
        # Force immediate button refresh — don't wait for the
        # marshal_to_ui callback (which can be delayed).
        self._update_buttons()
        self._refresh_table()
        self._update_summary()

    def _on_regen(self, idx: int) -> None:
        """Regenerate a single job in-place.

        P3.27B: the actual reset + versioned-filename allocation is
        delegated to MainWindow via the ``regen_requested`` signal:
        Generate Long parts (part_index set) get a NEW VERSIONED output
        file (the previous version is preserved on disk — a pathological
        outlier regeneration can no longer destroy the previous good
        audio), while manual batch jobs keep the legacy
        overwrite-in-place contract. Queue position is preserved either
        way, so concatenation order is unaffected.
        """
        if self._manager.is_running:
            QMessageBox.warning(
                self, "Batch Running",
                "Stop the current batch before regenerating a single item.")
            return
        jobs = self._manager.jobs
        if idx < 0 or idx >= len(jobs):
            return
        job = jobs[idx]
        is_part = getattr(job, "part_index", None) is not None
        # P3.45.2A — preflight the job about to be regenerated (its
        # CURRENT prompt and CURRENT parameters — a prompt/parameter edit
        # made in this workspace is the basis). The verdict is folded
        # into the EXISTING confirmation (no second modal): the user
        # decides with full information, and the job itself is never
        # rewritten or re-split here.
        preflight_note = ""
        try:
            from engine.output_guard import (
                preflight_generation_size, preflight_display_message,
            )
            verdict = preflight_generation_size(
                text=job.prompt,
                max_new_tokens=job.parameters.max_new_tokens,
            )
            if verdict["state"] == "blocked":
                preflight_note = "\n\n⚠ PREFLIGHT (P3.45.2A):\n{0}".format(
                    preflight_display_message(verdict, "This part"))
            elif verdict["state"] == "warning":
                preflight_note = (
                    "\n\n⚠ PREFLIGHT (P3.45.2A): this part is exactly at "
                    "the single-output limit (~{0:.0f}s) — no "
                    "margin.".format(verdict["maximum_output_seconds"]))
        except Exception:
            pass
        if is_part:
            message = (
                "Regenerate ONLY this part?\n\n"
                "  Part: {0}\n"
                "  Current output: {1}\n\n"
                "A NEW VERSIONED file will be created (e.g. v02); the\n"
                "previous version's audio is kept on disk. Queue position\n"
                "is preserved, so concatenation order is not affected.\n"
                "Other parts are NOT re-generated.{2}").format(
                    job.name or job.prompt[:40],
                    job.output_filename or "(auto)",
                    preflight_note)
        else:
            message = (
                "Regenerate ONLY this item?\n\n"
                "  Item: {0}\n"
                "  Output: {1}\n\n"
                "The output file will be overwritten in place. Queue position\n"
                "is preserved, so concatenation order is not affected. Other\n"
                "items are NOT re-generated.{2}").format(
                    job.name or job.prompt[:40],
                    job.output_filename or "(auto)",
                    preflight_note)
        reply = QMessageBox.question(
            self, "Regenerate Part", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes)
        if reply != QMessageBox.StandardButton.Yes:
            return
        # P3.27B: MainWindow allocates the next version (Generate Long
        # parts) and performs the reset + start.
        self.regen_requested.emit(idx)

    def _on_pause(self) -> None:
        self._manager.pause()
        # Force immediate button refresh.
        self._update_buttons()

    def _on_stop(self) -> None:
        """Stop the batch and immediately update the UI (do not wait for
        the next marshal_to_ui callback - the user wants visual feedback
        the instant they click Stop).

        P3.30(d): pending jobs flip to SKIPPED instantly; a queued (not
        yet started) generation is cancelled cooperatively by the
        engine; a generation already inside the model call cannot be
        interrupted (documented model limitation — generate_speech() is
        one blocking native call), finishes, and its audio is KEPT. The
        Stop button shows "Stopping…" until the batch actually ends.
        """
        self._manager.stop()
        # Immediate refresh so SKIPPED rows appear at once.
        self._refresh_table()
        self._update_buttons()
        self._update_summary()

    def _on_concatenate(self) -> None:
        """Concatenate all COMPLETED parts into a single WAV.

        Collects the output paths of every COMPLETED job IN QUEUE ORDER
        and emits concatenate_requested(paths). The MainWindow handler
        does the actual concatenation via the engine's AudioManager and
        loads the result into the waveform player.

        Using the queue order directly guarantees the concatenation
        sequence is always correct, regardless of regen operations —
        regen preserves both the queue position and the output filename,
        so the list order IS the final audio order.
        """
        if self._manager.is_running:
            QMessageBox.warning(
                self, "Batch Running",
                "Stop the batch before concatenating.")
            return
        # Collect COMPLETED job output paths in queue order.
        paths = []
        for job in self._manager.jobs:
            if job.status == JobStatus.COMPLETED and job.output_path:
                # Resolve to absolute path for the engine's AudioManager.
                full = job.output_path
                if not os.path.isabs(full):
                    # The engine resolves relative paths against APP_ROOT,
                    # so pass the relative path as-is — the MainWindow
                    # handler will resolve it.
                    pass
                paths.append(full)
        if len(paths) < 2:
            QMessageBox.information(
                self, "Nothing to Concatenate",
                "Need at least 2 completed parts to concatenate.\n"
                "Current completed: {0}".format(len(paths)))
            return
        # Emit so MainWindow can do the actual work (it has the engine).
        self.concatenate_requested.emit(paths)

    # ------------------------------------------------------------------
    # Close handling
    # ------------------------------------------------------------------
    def close_without_stopping_batch(self) -> None:
        """P3.35 §3/§11: single-live-instance hand-over close.

        Closes this dialog (saving UI state and RELEASING the shared
        manager's callbacks) WITHOUT stopping a running batch — the
        replacement dialog shows the same shared queue and its live
        progress continues seamlessly.
        """
        self._close_preserve_batch = True
        try:
            self.close()
        finally:
            self._close_preserve_batch = False

    def closeEvent(self, event) -> None:
        # Save the dialog size + column configuration so they persist
        # across reopens and application restarts (P3.35 §22-§28).
        self._geo_save_timer.stop()
        self._save_geometry()
        self._save_column_state()
        self._ui_state_saved = True
        # Stop the batch (mark PENDING as SKIPPED) when the USER closes
        # the dialog so we don't leave orphaned jobs running in the
        # background. A mode-switch close (close_without_stopping_batch)
        # keeps the batch alive — the replacement dialog takes over the
        # very same shared queue (P3.35 §3/§11).
        if (not self._close_preserve_batch and self._manager.is_running):
            self._manager.stop()
        # P3.35 §4: give the shared manager's single-slot callbacks back —
        # never leave them pointing at a hidden/closed dialog.
        self._release_manager()
        super().closeEvent(event)
