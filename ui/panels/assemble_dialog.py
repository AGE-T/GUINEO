"""
SpeechStudio Assembly Dialog (P3.22 Scene Chain, P3.23 completion).

Dialog for assembling multiple Scene audio outputs into a single combined
audio file (WAV primary + optional MP3).

Design record compliance (SPEECHSTUDIO CHARACTERS + SCENE ASSEMBLY FULL
DECISION RECORD):

  §41  Assembly UI content: project name, scene order, scene name,
        selection checkbox, generation status, duration, audio
        availability, REORDER CONTROLS [↑][↓], output format, destination,
        silence, normalize option, TOTAL ESTIMATED DURATION.
  §42  Partial selection: any subset, user-defined order (the ↑/↓ buttons
        define the assembly order explicitly — not just sort_order).
  §44  Assembly source visibility: each row shows WHICH AudioAsset is the
        canonical source (latest successful generation + timestamp).
  §52  No silent skipping of missing audio: rows without audio are
        disabled; the engine additionally blocks on missing files.
  §53  Empty selection → Assemble disabled. Existing destination →
        Overwrite / Choose another / Cancel prompt.
  §55  Normalize option, default ON, -18 LUFS (operates on the combined
        result).
  §69  Duplicate scenes in one assembly: impossible by construction (each
        Scene appears exactly once as a checkbox row).
  §74  Security: the output filename is UNTRUSTED input — sanitized via
        combined_audio.sanitize_output_filename (no path traversal, no
        directory injection); the output may never overwrite a SOURCE
        scene audio file.
  §78B Default filename: <ProjectName>_combined_<timestamp>.wav

On Assemble:
  - Validates at least one scene with audio is selected
  - Calls combined_audio.assemble_combined_audio() (float32 WAV safe)
  - Adds result to Project.combined_outputs (with audio_asset_ids lineage)
  - MainWindow creates the History entry (design record §61)

Usage:
    dialog = AssembleScenesDialog(project, app_root, parent)
    if dialog.exec():
        result = dialog.assembly_result  # AssemblyResult
"""

from __future__ import annotations
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QCheckBox, QFileDialog, QLineEdit, QMessageBox, QGroupBox,
    QGridLayout, QFrame, QScrollArea, QSlider, QWidget, QSizePolicy,
)

from engine.models import Project, Scene
from engine.combined_audio import (
    assemble_combined_audio, AssemblySource, AssemblyResult,
    ffmpeg_available, build_combined_output_entry, is_combined_output_stale,
    sanitize_output_filename,
)
from engine.logger import get_logger
from ui.theme import Palette  # noqa: F401  (kept: row styling below)


def stale_audio_use_anyway_dialog(parent, reasons, title="Scene Audio Out of Date") -> bool:
    """P3.30(f): the user-approved STALE-audio confirmation copy.

        This Scene Audio is out of date.

        B1 has a newer version: v02
        B2 has a newer version: v02

        This Combined Audio was created from older versions.

        Do you want to use the older audio anyway?

        [ Cancel ]   [ Use Anyway ]

    Returns True only for "Use Anyway". Extra reasons (e.g. missing
    source files) are appended after the version lines.
    """
    version_lines = [r for r in reasons if "newer version" in r]
    other_lines = [r for r in reasons if "newer version" not in r]
    body = "This Scene Audio is out of date.\n\n"
    if version_lines:
        body += "\n".join(version_lines) + "\n\n"
    if other_lines:
        body += "\n".join(other_lines) + "\n\n"
    body += ("This Combined Audio was created from older versions.\n\n"
             "Do you want to use the older audio anyway?")
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(body)
    cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    use_btn = box.addButton("Use Anyway", QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(cancel_btn)
    box.exec()
    return box.clickedButton() is use_btn


def _popup_menu_above(menu, btn):
    """P3.30(b/c): execute ``menu`` synchronously, anchored ABOVE ``btn``.

    ``menu.exec()`` without a position opens at the cursor — repeatedly
    a frameless top-left corner popup on Windows. Deterministic anchor:
    above the button, falling back to below at the screen edge.
    """
    menu.adjustSize()
    size = menu.sizeHint()
    btn_top_left = btn.mapToGlobal(QPoint(0, 0))
    pos = QPoint(btn_top_left.x(), btn_top_left.y() - size.height() - 6)
    screen = btn.screen()
    if screen is not None:
        avail = screen.availableGeometry()
        if pos.y() < avail.top():
            pos.setY(btn_top_left.y() + btn.height() + 6)
        if pos.x() + size.width() > avail.right() + 1:
            pos.setX(max(avail.left(), avail.right() + 1 - size.width()))
    return menu.exec(pos)

logger = get_logger("assemble_dialog")


def _fmt_duration(seconds: float) -> str:
    """Format seconds as H:MM:SS (design record §41 style: 00:41:51)."""
    try:
        s = int(round(seconds))
    except (TypeError, ValueError):
        s = 0
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return "{0:02d}:{1:02d}:{2:02d}".format(h, m, sec)


def _as_int(value) -> int:
    """Tolerant int coercion (P3.28 §26 JSON robustness)."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class _SceneCheckRow(QWidget):
    """One row in the scene selection list.

    Shows (design record §41/§44 + P3.28 §16): checkbox, status icon,
    scene name, the RESOLVED Scene output being used (source visibility:
    "Combined v02" / "Latest audio" / "Selected · CUSTOM" + STALE /
    MISSING / REVIEW REQUIRED badges), a "Choose Output…" picker, and
    duration.

    P3.28 follow-up (review remedy): a REVIEW REQUIRED row additionally
    offers [Combine Scene Now] — one click creates a fresh Combined
    output for that Scene (via the MainWindow combine handler, by scene
    id) and the row re-resolves.
    """

    combine_now_requested = Signal(str)  # scene id (review remedy)

    def __init__(self, scene: Scene, audio_path: str, duration: float,
                 stale: bool, asset_label: str, parent=None,
                 review_reason: str = "", resolved_kind: str = "",
                 resolved_id: str = ""):
        super().__init__(parent)
        self.scene = scene
        self.audio_path = audio_path
        self.duration = duration
        self.stale = stale
        self.asset_label = asset_label  # e.g. "Combined v02 · ..."
        self.review_reason = review_reason  # P3.28 §15: requires user review
        self.resolved_kind = resolved_kind
        self.resolved_id = resolved_id

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self._checkbox = QCheckBox()
        self._checkbox.setChecked(bool(audio_path))
        self._checkbox.setEnabled(bool(audio_path))
        self._checkbox.toggled.connect(lambda _: self._notify_order_changed())
        layout.addWidget(self._checkbox)

        # Status icon
        self._status_label = QLabel()
        if not audio_path:
            self._status_label.setText("\u26a0")  # warning sign
            self._status_label.setStyleSheet(
                "color: {0}; font-size: 14px;".format(Palette.ERROR))
            if review_reason:
                self._status_label.setToolTip(
                    "REVIEW REQUIRED — {0}\n\n"
                    "The Scene's audio cannot be resolved automatically.\n"
                    "Combine the Scene now, or use Choose Output… to pick "
                    "an output explicitly — stale or partial audio is "
                    "never assembled silently.".format(
                        review_reason))
            else:
                self._status_label.setToolTip(
                    "No audio file — generate this scene first, or leave it "
                    "unchecked. It will NOT be silently skipped.")
        elif stale:
            self._status_label.setText("\u21bb")  # refresh arrow
            self._status_label.setStyleSheet(
                "color: {0}; font-size: 14px;".format(Palette.WARNING))
            self._status_label.setToolTip(
                "The selected output is STALE (a source has a newer "
                "version) — you selected it explicitly, so it is used "
                "as-is.")
        else:
            self._status_label.setText("\u2713")  # check mark
            self._status_label.setStyleSheet(
                "color: {0}; font-size: 14px;".format(Palette.SUCCESS))
            self._status_label.setToolTip("Audio ready")
        layout.addWidget(self._status_label)

        # Scene name
        name_label = QLabel(scene.name or "Untitled")
        name_label.setStyleSheet(
            "color: {0}; font-size: 13px;".format(Palette.TEXT_PRIMARY))
        if not audio_path:
            name_label.setStyleSheet(
                "color: {0}; font-size: 13px;".format(Palette.TEXT_DISABLED))
        layout.addWidget(name_label, 1)

        # Canonical audio source visibility (design record §44 + P3.28 §16:
        # the RESOLVED Scene output — "Combined v02" / "Latest audio" /
        # "Selected · CUSTOM" — plus STALE / MISSING / REVIEW badges).
        if audio_path and asset_label:
            src_label = QLabel("({0})".format(asset_label))
            src_label.setStyleSheet(
                "color: {0}; font-size: 11px;".format(Palette.TEXT_SECONDARY))
            src_label.setToolTip(
                "Resolved Scene output consumed by Project Assembly "
                "(P3.28 §15: explicit selection > latest non-stale Scene "
                "Combined > provable full-scene audio, legacy).")
            layout.addWidget(src_label)
        elif review_reason:
            review_label = QLabel("REVIEW REQUIRED")
            review_label.setStyleSheet(
                "color: {0}; font-size: 10px; font-weight: 700;".format(
                    Palette.WARNING))
            review_label.setToolTip(review_reason)
            layout.addWidget(review_label)

        # P3.28 follow-up (review remedy): [Combine Scene Now] on rows
        # that cannot auto-resolve but HAVE a generation structure — one
        # click creates a fresh Combined output for THIS Scene (routed by
        # scene id, works for non-active Scenes) and the row re-resolves.
        if review_reason and (getattr(scene, "expected_audio_slots", None)
                              or []):
            combine_btn = QPushButton("Combine Scene Now")
            combine_btn.setMinimumHeight(28)
            combine_btn.setStyleSheet(
                "QPushButton {{ padding: 4px 10px; font-size: 12px; "
                "font-weight: 600; }}")
            combine_btn.setToolTip(
                "Combine this Scene's generated slot outputs into a fresh,\n"
                "versioned Combined output (full per-slot lineage). The\n"
                "new Combined becomes the Scene output — Project Assembly\n"
                "then uses it automatically.")
            combine_btn.clicked.connect(
                lambda _checked=False, _sid=scene.id:
                self.combine_now_requested.emit(_sid))
            layout.addWidget(combine_btn)

        # P3.28 §16/Rec 16: "Choose Output…" picker — latest combined /
        # any combined version / provable full-scene asset. The user
        # never faces 50 raw files; exactly ONE output represents the
        # scene (structural double-inclusion protection, §14: parts and
        # their Combined output can never appear in the same assembly).
        if getattr(scene, "combined_outputs", None) or (
                getattr(scene, "audio_assets", None)):
            choose_btn = QPushButton("Choose Output\u2026")
            choose_btn.setMinimumHeight(28)
            choose_btn.setStyleSheet(
                "QPushButton {{ padding: 4px 10px; font-size: 12px; }}")
            choose_btn.setToolTip(
                "Choose which output represents this Scene in the "
                "assembly:\n— latest Scene Combined output\n— any combined "
                "version\n— latest single asset\n\nExactly ONE output per "
                "Scene is used (a Combined output replaces its underlying "
                "parts — never both).")
            choose_btn.clicked.connect(
                lambda _checked=False, _s=scene, _b=choose_btn:
                self._choose_output(_s, _b))
            layout.addWidget(choose_btn)

        # Duration
        if duration > 0:
            dur_text = "{0:.1f}s".format(duration)
        else:
            dur_text = "\u2014"
        dur_label = QLabel(dur_text)
        dur_label.setStyleSheet(
            "color: {0}; font-family: 'JetBrains Mono', monospace; "
            "font-size: 12px;".format(Palette.TEXT_SECONDARY))
        layout.addWidget(dur_label)

    def is_selected(self) -> bool:
        return self._checkbox.isChecked() and self._checkbox.isEnabled()

    def _choose_output(self, scene: Scene, anchor_btn=None) -> None:
        """P3.28 §16/Rec 16: the per-scene output picker.

        Offers: latest Scene Combined / any combined version / latest
        single asset / automatic resolution (clear). Choosing a STALE
        output requires explicit confirmation (§15 — P3.30(f): the
        user-approved "Use Anyway?" copy). The chosen output
        replaces the row immediately; exactly ONE output represents the
        Scene (double-inclusion protection, §14).

        P3.30(c): the menu is anchored ABOVE the Choose Output… button
        (previously ``menu.exec()`` without a position — frameless
        top-left corner popup on Windows).
        """
        from PySide6.QtWidgets import QMenu, QMessageBox
        from engine.audio_provenance import (
            resolve_scene_output, is_scene_combined_stale,
            is_complete_scene_asset,
            KIND_SCENE_COMBINED, KIND_ASSET,
        )
        menu = QMenu(self)
        combined = [e for e in (scene.combined_outputs or [])
                    if isinstance(e, dict)]
        combined.sort(key=lambda e: _as_int(e.get("version")), reverse=True)
        actions = {}
        app_root = self._app_root_of()
        if combined:
            entry = combined[0]
            act = menu.addAction(
                "Latest combined (v{0:02d})".format(
                    _as_int(entry.get("version"))))
            actions[act] = (KIND_SCENE_COMBINED, entry.get("id"), entry)
            if len(combined) > 1:
                submenu = menu.addMenu("Older combined versions")
                for entry in combined[1:]:
                    stale, _reasons = is_scene_combined_stale(
                        scene, entry, app_root)
                    label = "v{0:02d}{1}".format(
                        _as_int(entry.get("version")),
                        " · STALE" if stale else "")
                    act = submenu.addAction(label)
                    actions[act] = (KIND_SCENE_COMBINED, entry.get("id"),
                                    entry)
        if scene.audio_assets and any(
                is_complete_scene_asset(a)
                for a in scene.audio_assets if isinstance(a, dict)):
            # Honest label (follow-up safety rule): the automatic legacy
            # rule resolves a PROVABLE full-scene asset — never a Part.
            act = menu.addAction("Latest full-scene audio (legacy)")
            actions[act] = (None, None, None)  # automatic legacy rule
        act = menu.addAction("Automatic resolution (default)")
        actions[act] = ("", "", None)
        anchor = anchor_btn if anchor_btn is not None else self
        chosen = _popup_menu_above(menu, anchor)
        if chosen is None or chosen not in actions:
            return
        kind, entry_id, entry = actions[chosen]
        if kind == "":
            scene.selected_output = None
        elif kind is None:
            # Automatic rule 3 = clear explicit selection; latest asset
            # is the deterministic fallback of the resolution chain.
            scene.selected_output = None
        else:
            if entry is not None:
                stale, reasons = is_scene_combined_stale(
                    scene, entry, app_root)
                if stale:
                    if not stale_audio_use_anyway_dialog(
                            self, reasons,
                            title="Scene Audio Out of Date"):
                        return
            scene.selected_output = {"kind": kind, "id": entry_id}
        # Refresh the whole dialog so every row reflects the new state.
        dialog = self._dialog_of()
        if dialog is not None:
            dialog._rebuild_rows()

    def _app_root_of(self) -> str:
        dialog = self._dialog_of()
        return dialog._app_root if dialog is not None else ""

    def _dialog_of(self):
        p = self.parentWidget()
        while p is not None:
            if isinstance(p, AssembleScenesDialog):
                return p
            p = p.parentWidget()
        return None

    def _notify_order_changed(self) -> None:
        # Bubble the selection change up so the dialog can update the
        # total-duration estimate and the Assemble button state.
        p = self.parentWidget()
        while p is not None:
            if isinstance(p, AssembleScenesDialog):
                p._update_totals()
                return
            p = p.parentWidget()


class AssembleScenesDialog(QDialog):
    """Dialog for assembling Scene audio into a combined output.

    Args:
        project:    the active Project (with scenes + combined_outputs).
        app_root:   the SpeechStudio app root (for resolving relative audio paths).
        parent:     parent widget.
    """

    # P3.28 follow-up (review remedy): a REVIEW REQUIRED row's
    # [Combine Scene Now] emits the scene id; MainWindow routes it
    # through the SAME combine handler as the Batch window (by id, so
    # non-active Scenes work) and rebuilds the dialog's rows afterwards.
    combine_scene_now_requested = Signal(str)

    # Creation sequence (deterministic "newest open dialog wins" for the
    # review-remedy refresh lookup).
    _creation_seq_counter = 0

    def __init__(self, project: Project, app_root: str, parent=None):
        super().__init__(parent)
        AssembleScenesDialog._creation_seq_counter += 1
        self._creation_seq = AssembleScenesDialog._creation_seq_counter
        self._project = project
        self._app_root = os.path.abspath(app_root)
        self.assembly_result: Optional[AssemblyResult] = None
        self.combined_entry: Optional[Dict[str, Any]] = None
        self._scene_rows: List[_SceneCheckRow] = []
        self._row_order: List[int] = []  # indices into _scene_rows, user order
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle("Assemble Scenes → Combined Audio")
        self.setMinimumSize(600, 640)
        self.setStyleSheet("background-color: {0};".format(Palette.BG_BASE))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        header = QLabel("Assemble Combined Audio")
        header.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: {0};".format(
                Palette.TEXT_PRIMARY))
        layout.addWidget(header)

        project_name = getattr(self._project, 'name', 'Unknown')
        subheader = QLabel(
            "Project: {0}  \u2022  {1} scenes".format(
                project_name, len(self._project.scenes)))
        subheader.setStyleSheet(
            "color: {0}; font-size: 12px;".format(Palette.TEXT_SECONDARY))
        layout.addWidget(subheader)

        # --- Scene selection list (§41) ---
        scene_group = QGroupBox("Scenes (assembly order — use ↑ / ↓ to reorder)")
        scene_group.setStyleSheet(
            "QGroupBox {{ color: {0}; border: 1px solid {1}; "
            "border-radius: 6px; margin-top: 8px; padding-top: 8px; }}".format(
                Palette.TEXT_PRIMARY, Palette.BORDER))
        scene_layout = QVBoxLayout(scene_group)
        scene_layout.setContentsMargins(8, 12, 8, 8)
        scene_layout.setSpacing(2)

        # Scrollable area for scene rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea {{ background-color: {0}; border: none; }}".format(
                Palette.BG_SURFACE))
        scroll_content = QWidget()
        scroll_content.setStyleSheet(
            "background-color: {0};".format(Palette.BG_SURFACE))
        self._scroll_layout = QVBoxLayout(scroll_content)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout.setSpacing(0)

        # Sort scenes by sort_order initially (P3.21 persistent order);
        # the user can then reorder the ASSEMBLY order explicitly (§42).
        sorted_scenes = sorted(self._project.scenes, key=lambda s: s.sort_order)

        for scene in sorted_scenes:
            self._add_scene_row(scene)

        self._rebuild_row_layout()
        self._scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        scene_layout.addWidget(scroll)

        # Selection + reorder controls (§41: [↑][↓])
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._select_all)
        btn_row.addWidget(select_all_btn)
        select_none_btn = QPushButton("Select None")
        select_none_btn.clicked.connect(self._select_none)
        btn_row.addWidget(select_none_btn)
        btn_row.addSpacing(12)
        # Reorder buttons — move the first SELECTED row (or the row above
        # the first selected one) up/down within the assembly order.
        self._up_btn = QPushButton("\u2191 Move Up")
        self._up_btn.setToolTip(
            "Move the selected scene earlier in the assembly order "
            "(design record §42: user-defined order)")
        self._up_btn.clicked.connect(lambda: self._move_selected(-1))
        btn_row.addWidget(self._up_btn)
        self._down_btn = QPushButton("\u2193 Move Down")
        self._down_btn.setToolTip(
            "Move the selected scene later in the assembly order")
        self._down_btn.clicked.connect(lambda: self._move_selected(1))
        btn_row.addWidget(self._down_btn)
        btn_row.addStretch()
        ready_count = sum(1 for r in self._scene_rows if r.audio_path)
        self._ready_label = QLabel(
            "{0} / {1} scenes have audio".format(ready_count, len(self._scene_rows)))
        self._ready_label.setStyleSheet(
            "color: {0}; font-size: 12px;".format(Palette.TEXT_SECONDARY))
        btn_row.addWidget(self._ready_label)
        scene_layout.addLayout(btn_row)

        layout.addWidget(scene_group)

        # --- Output settings (§41) ---
        output_group = QGroupBox("Output Settings")
        output_group.setStyleSheet(
            "QGroupBox {{ color: {0}; border: 1px solid {1}; "
            "border-radius: 6px; margin-top: 8px; padding-top: 8px; }}".format(
                Palette.TEXT_PRIMARY, Palette.BORDER))
        output_layout = QGridLayout(output_group)
        output_layout.setContentsMargins(8, 12, 8, 8)
        output_layout.setSpacing(8)

        # Output filename (§78B + P3.28 §18: version slot added —
        # {Proj}_combined_v{NN}_{timestamp}.wav; the version is
        # monotonic max+1 over Project.combined_outputs, so successive
        # assemblies never collide or silently overwrite).
        output_layout.addWidget(QLabel("Filename:"), 0, 0)
        self._filename_edit = QLineEdit()
        next_version = 1 + max(
            (_as_int(e.get("version"))
             for e in (self._project.combined_outputs or [])
             if isinstance(e, dict)), default=0)
        default_name = "{0}_combined_v{1:02d}_{2}".format(
            project_name.replace(" ", "_") if project_name else "project",
            next_version,
            datetime.now().strftime("%Y%m%d_%H%M%S"))
        self._next_combined_version = next_version
        self._filename_edit.setText(default_name)
        self._filename_edit.setToolTip(
            "Output filename (path separators are not allowed — the file "
            "is created inside the chosen folder)")
        self._filename_edit.setStyleSheet(
            "QLineEdit {{ background-color: {0}; color: {1}; "
            "border: 1px solid {2}; border-radius: 4px; padding: 6px; }}".format(
                Palette.BG_SURFACE, Palette.TEXT_PRIMARY, Palette.BORDER))
        output_layout.addWidget(self._filename_edit, 0, 1)

        # Output directory
        output_layout.addWidget(QLabel("Folder:"), 1, 0)
        dir_row = QHBoxLayout()
        self._dir_edit = QLineEdit()
        default_dir = os.path.join(self._app_root, "outputs", "combined")
        self._dir_edit.setText(default_dir)
        self._dir_edit.setStyleSheet(
            "QLineEdit {{ background-color: {0}; color: {1}; "
            "border: 1px solid {2}; border-radius: 4px; padding: 6px; }}".format(
                Palette.BG_SURFACE, Palette.TEXT_PRIMARY, Palette.BORDER))
        dir_row.addWidget(self._dir_edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_folder)
        dir_row.addWidget(browse_btn)
        output_layout.addLayout(dir_row, 1, 1)

        # Inter-scene silence (§54: one global value, default 500 ms)
        output_layout.addWidget(QLabel("Inter-scene gap:"), 2, 0)
        gap_row = QHBoxLayout()
        self._gap_slider = QSlider(Qt.Orientation.Horizontal)
        self._gap_slider.setRange(0, 30)  # 0.0 - 3.0 seconds (x10)
        self._gap_slider.setValue(5)      # default 0.5s
        self._gap_slider.setStyleSheet(
            "QSlider::groove:horizontal {{ background: {0}; height: 4px; }}"
            "QSlider::handle:horizontal {{ background: {0}; width: 14px; "
            "margin: -5px 0; border-radius: 7px; }}".format(Palette.ACCENT))
        self._gap_label = QLabel("0.5s")
        self._gap_label.setStyleSheet(
            "color: {0}; font-family: 'JetBrains Mono', monospace; "
            "font-size: 12px; min-width: 30px;".format(Palette.TEXT_SECONDARY))
        self._gap_slider.valueChanged.connect(
            lambda v: self._gap_label.setText("{0:.1f}s".format(v / 10.0)))
        gap_row.addWidget(self._gap_slider)
        gap_row.addWidget(self._gap_label)
        output_layout.addLayout(gap_row, 2, 1)

        # Normalize option (§55: default ON, -18 LUFS, on combined result)
        self._normalize_checkbox = QCheckBox(
            "Normalize to -18 LUFS (combined result)")
        self._normalize_checkbox.setChecked(True)
        self._normalize_checkbox.setStyleSheet(
            "QCheckBox {{ color: {0}; }}".format(Palette.TEXT_PRIMARY))
        self._normalize_checkbox.setToolTip(
            "Apply loudness normalization to the combined audio "
            "(design record §55: optional, default ON).")
        output_layout.addWidget(self._normalize_checkbox, 3, 0, 1, 2)

        # MP3 option (§56: export conversion only)
        self._mp3_checkbox = QCheckBox("Also create MP3 (requires FFmpeg)")
        self._mp3_checkbox.setChecked(ffmpeg_available())
        self._mp3_checkbox.setEnabled(ffmpeg_available())
        if not ffmpeg_available():
            self._mp3_checkbox.setToolTip(
                "FFmpeg not found on system PATH. Install FFmpeg to enable "
                "MP3 export. WAV export remains fully available.")
            self._mp3_checkbox.setText(
                "Also create MP3 (FFmpeg not found — WAV still available)")
        self._mp3_checkbox.setStyleSheet(
            "QCheckBox {{ color: {0}; }}".format(
                Palette.TEXT_PRIMARY if ffmpeg_available()
                else Palette.TEXT_DISABLED))
        output_layout.addWidget(self._mp3_checkbox, 4, 0, 1, 2)

        layout.addWidget(output_group)

        # --- Total duration (§41) ---
        # P3.45.2B wording correction: the §41 total is the sum of the
        # MEASURED durations of the resolved scene outputs plus the
        # configured inter-scene gap — a measured aggregate, NOT an
        # estimate (the historical "estimated" label/comment mislabelled
        # measured file durations; the user-visible string "Total: …"
        # was always neutral). No behaviour change.
        self._total_label = QLabel("Total: 00:00:00")
        self._total_label.setStyleSheet(
            "color: {0}; font-size: 13px; font-weight: bold; "
            "font-family: 'JetBrains Mono', monospace;".format(
                Palette.TEXT_PRIMARY))
        layout.addWidget(self._total_label)

        # --- Existing combined outputs (stale indicator) ---
        if self._project.combined_outputs:
            existing_group = QGroupBox("Previous Assemblies")
            existing_group.setStyleSheet(
                "QGroupBox {{ color: {0}; border: 1px solid {1}; "
                "border-radius: 6px; margin-top: 8px; padding-top: 8px; }}".format(
                    Palette.TEXT_PRIMARY, Palette.BORDER))
            existing_layout = QVBoxLayout(existing_group)
            existing_layout.setContentsMargins(8, 12, 8, 8)
            for entry in self._project.combined_outputs[-3:]:  # last 3
                stale = is_combined_output_stale(entry, self._project.scenes,
                                                 self._app_root)
                path = entry.get("output_path", "?")
                dur = entry.get("duration", 0.0)
                cnt = entry.get("scene_count", 0)
                ts = entry.get("created_at", "?")
                status_text = "  \u21bb STALE" if stale else "  \u2713 OK"
                status_color = Palette.WARNING if stale else Palette.SUCCESS
                label = QLabel(
                    "{0}  \u2014  {1} scenes, {2:.1f}s  \u2014  {3}{4}".format(
                        os.path.basename(path), cnt, dur, ts[:16], status_text))
                label.setStyleSheet(
                    "color: {0}; font-size: 12px; font-family: "
                    "'JetBrains Mono', monospace;".format(status_color))
                existing_layout.addWidget(label)
            layout.addWidget(existing_group)

        # --- Buttons (§53: empty selection → disabled) ---
        button_row = QHBoxLayout()
        button_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(90)
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        self._assemble_btn = QPushButton("Assemble")
        self._assemble_btn.setMinimumWidth(110)
        self._assemble_btn.setStyleSheet(
            "QPushButton {{ background-color: {0}; color: #FFFFFF; "
            "border: none; border-radius: 4px; padding: 8px 16px; "
            "font-weight: bold; }}"
            "QPushButton:hover {{ background-color: {1}; }}"
            "QPushButton:disabled {{ background-color: {2}; }}".format(
                Palette.ACCENT, Palette.ACCENT_PRESSED, Palette.TEXT_DISABLED))
        self._assemble_btn.clicked.connect(self._on_assemble)
        button_row.addWidget(self._assemble_btn)
        layout.addLayout(button_row)

        self._update_totals()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _add_scene_row(self, scene: Scene) -> None:
        """Build one scene row from the RESOLVED Scene output (P3.28 §16).

        Resolution (engine.audio_provenance.resolve_scene_output, §15
        correction): explicit selection > latest NON-STALE Scene Combined
        > latest valid asset. A scene whose candidates are ALL stale or
        missing gets a REVIEW REQUIRED row (unchecked, disabled) — stale
        audio is never assembled silently.
        """
        from engine.audio_provenance import resolve_scene_output
        resolution = resolve_scene_output(scene, self._app_root)
        review_reason = (resolution.get("reason", "")
                         if resolution.get("requires_review") else "")
        if resolution.get("requires_review") or not resolution.get("path"):
            row = _SceneCheckRow(
                scene, "", 0.0, False, "",
                review_reason=review_reason or "no resolvable audio")
            row._audio_asset_id = ""
            row._resolved_kind = ""
            row._resolved_id = ""
            row.combine_now_requested.connect(
                self.combine_scene_now_requested)
            self._scene_rows.append(row)
            self._row_order.append(len(self._scene_rows) - 1)
            return
        row = _SceneCheckRow(
            scene, resolution["path"],
            self._duration_of(scene, resolution),
            bool(resolution.get("stale")),
            resolution.get("label", ""))
        row._audio_asset_id = resolution.get("id", "")
        row._resolved_kind = resolution.get("kind", "")
        row._resolved_id = resolution.get("id", "")
        self._scene_rows.append(row)
        self._row_order.append(len(self._scene_rows) - 1)

    def _duration_of(self, scene: Scene, resolution: dict) -> float:
        """Duration of the resolved output (combined entry or asset)."""
        entry = resolution.get("entry") or {}
        try:
            return float(entry.get("duration") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _rebuild_rows(self) -> None:
        """Rebuild every scene row (after a Choose… selection change)."""
        # Preserve the current assembly order (by scene id).
        order_ids = [self._scene_rows[i].scene.id
                     for i in self._row_order
                     if i < len(self._scene_rows)]
        for row in self._scene_rows:
            row.setParent(None)
            row.deleteLater()
        self._scene_rows = []
        self._row_order = []
        sorted_scenes = sorted(self._project.scenes,
                               key=lambda s: s.sort_order)
        for scene in sorted_scenes:
            self._add_scene_row(scene)
        # Restore the previous order where the scene still exists.
        by_id = {row.scene.id: idx for idx, row in
                 enumerate(self._scene_rows)}
        new_order = [by_id[sid] for sid in order_ids if sid in by_id]
        for idx in range(len(self._scene_rows)):
            if idx not in new_order:
                new_order.append(idx)
        self._row_order = new_order
        self._rebuild_row_layout()
        self._update_totals()

    def _get_scene_audio(self, scene: Scene):
        """Return (absolute_path, duration, asset_label, asset_id) for the
        scene's RESOLVED output (P3.28 §16: resolve_scene_output chain).

        Kept for backward compatibility with callers/tests that expect
        the legacy tuple signature. Returns ("", 0.0, "", "") when the
        Scene requires user review or has no resolvable audio.
        """
        from engine.audio_provenance import resolve_scene_output
        resolution = resolve_scene_output(scene, self._app_root)
        if resolution.get("requires_review") or not resolution.get("path"):
            return ("", 0.0, "", "")
        return (resolution["path"], self._duration_of(scene, resolution),
                resolution.get("label", ""), resolution.get("id", ""))

    def _ordered_rows(self) -> List[_SceneCheckRow]:
        """Rows in the current user-defined assembly order."""
        return [self._scene_rows[i] for i in self._row_order]

    def _rebuild_row_layout(self) -> None:
        """Re-lay-out the rows in the current order."""
        # Remove all rows (but not the trailing stretch, which is added
        # once by _build_ui after this method's last call).
        while self._scroll_layout.count() > 0:
            item = self._scroll_layout.takeAt(0)
            w = item.widget()
            if w is not None and w not in self._scene_rows:
                # Keep the stretch for re-adding at the end.
                pass
        for row in self._ordered_rows():
            self._scroll_layout.addWidget(row)
        # Re-add the trailing stretch (or ensure one exists).
        found_stretch = False
        for i in range(self._scroll_layout.count()):
            if self._scroll_layout.itemAt(i) is not None:
                from PySide6.QtWidgets import QSpacerItem
                if isinstance(self._scroll_layout.itemAt(i), QSpacerItem):
                    found_stretch = True
                    break
        if not found_stretch:
            self._scroll_layout.addStretch()

    def _selected_rows(self) -> List[_SceneCheckRow]:
        return [r for r in self._ordered_rows() if r.is_selected()]

    def _select_all(self) -> None:
        for row in self._scene_rows:
            if row.audio_path:
                row._checkbox.setChecked(True)
        self._update_totals()

    def _select_none(self) -> None:
        for row in self._scene_rows:
            row._checkbox.setChecked(False)
        self._update_totals()

    def _move_selected(self, direction: int) -> None:
        """Move the first selected row (or first row if none selected)
        up/down in the assembly order (§41/§42)."""
        if not self._row_order:
            return
        # Find the position of the first selected row; if none selected,
        # operate on the row at the current focus-less default (index 0).
        pos = -1
        for p, idx in enumerate(self._row_order):
            if self._scene_rows[idx].is_selected():
                pos = p
                break
        if pos < 0:
            pos = 0
        new_pos = pos + direction
        if new_pos < 0 or new_pos >= len(self._row_order):
            return
        self._row_order.insert(new_pos, self._row_order.pop(pos))
        self._rebuild_row_layout()
        self._update_totals()

    def _update_totals(self) -> None:
        """Live total duration (§41) + Assemble button state (§53).

        P3.45.2B wording correction: the total aggregates the MEASURED
        durations of the selected rows' resolved outputs (scene combined
        entries / assets — measured from the written files) plus the
        configured inter-scene gap — a measured aggregate, not an
        estimate (the historical docstring said "estimated").
        """
        selected = self._selected_rows()
        total = sum(r.duration for r in selected)
        gap = self._gap_slider.value() / 10.0
        if len(selected) > 1 and gap > 0:
            total += gap * (len(selected) - 1)
        self._total_label.setText(
            "Total: {0}  \u2014  {1} scene(s) selected".format(
                _fmt_duration(total), len(selected)))
        # §53: empty selection → disable Assemble.
        self._assemble_btn.setEnabled(len(selected) > 0)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose Output Folder", self._dir_edit.text() or "")
        if folder:
            self._dir_edit.setText(folder)

    # ------------------------------------------------------------------
    # Assemble
    # ------------------------------------------------------------------
    def _resolve_output_path(self) -> Optional[str]:
        """Build + validate the output path.

        Security (§74/§85): the filename is untrusted — sanitized with
        sanitize_output_filename (blocks "../" traversal and absolute-path
        injection). The output must also never be one of the SOURCE audio
        files (a combined output must not overwrite source Scene audio —
        design record §47: source assets remain unchanged).
        """
        raw_name = self._filename_edit.text().strip()
        if not raw_name:
            QMessageBox.warning(self, "Invalid Filename",
                                "Please enter an output filename.")
            return None
        safe_name = sanitize_output_filename(raw_name)
        if not safe_name:
            QMessageBox.warning(
                self, "Invalid Filename",
                "The filename contains no usable characters:\n\n{0}".format(
                    raw_name))
            return None
        if safe_name != raw_name:
            # Inform the user the name was sanitized (no silent rewriting).
            QMessageBox.information(
                self, "Filename Adjusted",
                "Path separators are not allowed in the filename.\n"
                "Using: {0}".format(safe_name))
            self._filename_edit.setText(safe_name)
        if not safe_name.lower().endswith(".wav"):
            safe_name += ".wav"

        out_dir = self._dir_edit.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "No Folder",
                                "Please choose an output folder.")
            return None
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(
                self, "Invalid Folder",
                "The output folder cannot be created:\n{0}".format(exc))
            return None
        output_path = os.path.join(out_dir, safe_name)

        # Never overwrite a SOURCE audio file (§47).
        src_paths = {os.path.abspath(r.audio_path) for r in self._scene_rows
                     if r.audio_path}
        if os.path.abspath(output_path) in src_paths:
            QMessageBox.critical(
                self, "Unsafe Destination",
                "The destination would overwrite a source Scene audio "
                "file.\n\nChoose a different filename or folder — source "
                "Scene audio must remain unchanged (design record §47).")
            return None
        return output_path

    def _confirm_existing_destination(self, output_path: str) -> bool:
        """§53: Existing destination → Overwrite / Choose another / Cancel."""
        if not os.path.exists(output_path):
            return True
        reply = QMessageBox.question(
            self, "File Exists",
            "The destination file already exists:\n\n{0}\n\n"
            "Overwrite it?".format(output_path),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Retry
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if reply == QMessageBox.StandardButton.Yes:
            return True
        if reply == QMessageBox.StandardButton.Retry:
            # "Choose another" — open the folder picker + retry once.
            folder = QFileDialog.getExistingDirectory(
                self, "Choose a Different Folder",
                self._dir_edit.text() or "")
            if folder:
                self._dir_edit.setText(folder)
            return False  # user re-triggers Assemble with the new folder
        return False

    def _on_assemble(self) -> None:
        """Validate and run the assembly."""
        # Collect selected scenes in the user-defined order (§42)
        selected_rows = self._selected_rows()
        if not selected_rows:
            QMessageBox.warning(
                self, "No Scenes Selected",
                "Please select at least one scene with audio to assemble.")
            return

        output_path = self._resolve_output_path()
        if output_path is None:
            return
        if not self._confirm_existing_destination(output_path):
            return

        # Build sources (with AudioAsset lineage — §45; P3.28 §16: the
        # RESOLVED entity ids are recorded so combined-output lineage
        # stays exact and identity-based staleness works).
        sources = [
            AssemblySource(
                scene_id=r.scene.id,
                scene_name=r.scene.name,
                audio_path=r.audio_path,
                duration=r.duration,
                audio_asset_id=getattr(r, "_resolved_id", "")
                or getattr(r, "_audio_asset_id", ""),
            )
            for r in selected_rows
        ]

        # Run assembly (float32 WAV, normalization, optional MP3)
        gap = self._gap_slider.value() / 10.0
        create_mp3 = self._mp3_checkbox.isChecked()
        normalize = self._normalize_checkbox.isChecked()
        result = assemble_combined_audio(
            sources=sources,
            output_path=output_path,
            inter_scene_silence=gap,
            create_mp3=create_mp3,
            normalize=normalize,
            project_name=self._project.name,
        )

        if not result.success:
            # Blocked reasons name the offending scenes (§52: no silent skip)
            error_parts = result.blocked_reasons or result.errors
            error_msg = "\n".join(error_parts) if error_parts else "Unknown error"
            QMessageBox.critical(
                self, "Assembly Blocked",
                "The combined audio could NOT be assembled:\n\n{0}\n\n"
                "No output file was written. Generate the missing scene "
                "audio or deselect it.".format(error_msg))
            return

        # Build the combined_outputs entry (§45 lineage)
        source_scenes_data = [
            {"scene_id": r.scene.id, "scene_name": r.scene.name,
             "audio_path": r.audio_path,
             "audio_asset_id": getattr(r, "_audio_asset_id", ""),
             # P3.28 §16: the resolved entity (combined entry id or asset
             # id) that represented this Scene in the assembly.
             "resolved_kind": getattr(r, "_resolved_kind", ""),
             "resolved_id": getattr(r, "_resolved_id", ""),
             "duration": r.duration}
            for r in selected_rows
        ]
        entry = build_combined_output_entry(
            result, self._project.name, source_scenes_data,
            version=getattr(self, "_next_combined_version", None))

        # Add to project
        self._project.combined_outputs.append(entry)
        self.assembly_result = result
        self.combined_entry = entry

        logger.info(
            "Assembly complete: %s (%d scenes, %.2fs, mp3=%s, normalized=%s)",
            output_path, result.scene_count, result.total_duration,
            result.mp3_created, result.normalized)

        # Show summary
        summary_lines = [
            "Combined audio assembled successfully!",
            "",
            "Output: {0}".format(output_path),
            "Scenes: {0}".format(result.scene_count),
            "Duration: {0}".format(_fmt_duration(result.total_duration)),
            "Format: {0}".format("WAV + MP3" if result.mp3_created else "WAV (32-bit float)"),
            "Normalized: {0}".format("yes (-18 LUFS)" if result.normalized else "no"),
        ]
        if result.mp3_created:
            summary_lines.append("MP3: {0}".format(result.mp3_path))
        if result.errors:
            summary_lines.append("")
            summary_lines.append("Warnings:")
            for e in result.errors:
                summary_lines.append("  \u2022 {0}".format(e))

        QMessageBox.information(
            self, "Assembly Complete", "\n".join(summary_lines))

        self.accept()
