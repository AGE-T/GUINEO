"""
SpeechStudio History View Dialog.

P3.3: A real History view replacing the temporary placeholder.
Displays generated audio entries in a sortable, filterable table.

Features:
- Table with columns: Date, Project, Scene, Character/Speaker, Voice, Duration, Status
- Filter by Project and Scene (dropdowns)
- Text search across visible metadata
- Sort by date (newest first by default)
- Actions: Play audio, Open Scene, Reveal file, Delete entry
- Empty state message
- Backward compatible with old entries (null scene_id/character_id)

Data source: HistoryManager (authoritative) — no second data model.
"""

from __future__ import annotations
from typing import Optional, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QLineEdit, QMessageBox, QFileDialog, QAbstractItemView,
    QWidget, QSizePolicy,
)

from engine.history_manager import HistoryEntry
from engine.models import Project
from ui.theme import Palette

import os as _os
# P3.25 (audit SS-M10): paths recorded in history entries are RELATIVE
# to the application root. The previous CWD-relative ``os.path.isfile``
# checks reported "File missing" and blocked Play/Reveal whenever the
# app was launched from a different working directory (launch.bat,
# python SpeechStudio.py elsewhere). All file checks now resolve
# against APP_ROOT — the same resolution the engine's load_wav uses.
# NOTE: this module lives at <root>/ui/panels/history_view.py — the app
# root is THREE dirname levels up.
_HISTORY_APP_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))


def _resolve_history_path(rel_path: str) -> str:
    """Resolve a history entry path against the application root."""
    if not rel_path:
        return rel_path
    if _os.path.isabs(rel_path):
        return rel_path
    return _os.path.join(_HISTORY_APP_ROOT, rel_path)


class HistoryViewDialog(QDialog):
    """History view dialog — displays generated audio index.

    Backed directly by HistoryManager.list_entries(). No second data model.
    """

    # Signal emitted when the user wants to open a Scene from History.
    # Carries (project_name, scene_id, scene_name).
    open_scene_requested = Signal(str, str, str)

    def __init__(self, entries: List[HistoryEntry], project: Optional[Project] = None,
                 parent=None):
        """
        Args:
            entries: history entries from HistoryManager.list_entries().
            project: the active Project (for character resolution). May be None.
            parent: parent widget.
        """
        super().__init__(parent)
        self._entries = list(entries)  # snapshot
        self._project = project
        self._filtered: List[HistoryEntry] = []
        # P3.44: ONE resolution base for the whole dialog — the ENGINE's
        # app root when the dialog is parented to the MainWindow (the
        # authoritative base, matching engine.load_wav and the sidebar's
        # _load_history_entry_into_player), else the module-derived app
        # root. The previous per-method mix (module root for the
        # existence pre-check, engine root for playback) made Play open
        # a "file missing" modal for entries that were perfectly
        # playable whenever the two roots differed.
        base = None
        ancestor = self.parent()
        while ancestor is not None:
            eng = getattr(ancestor, "_engine", None)
            if eng is not None:
                base = getattr(eng, "app_root", None)
                break
            ancestor = ancestor.parent()
        self._app_root = base or _HISTORY_APP_ROOT
        self._build_ui()
        self._apply_filters()

    def _resolve_path(self, rel_path: str) -> str:
        """Resolve an entry path against the dialog's authoritative base."""
        if not rel_path or _os.path.isabs(rel_path):
            return rel_path
        return _os.path.join(self._app_root, rel_path)

    def _build_ui(self) -> None:
        self.setWindowTitle("Generation History")
        self.setMinimumSize(800, 500)
        self.setStyleSheet("background-color: {0};".format(Palette.BG_BASE))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # --- Header ---
        header = QLabel("Generation History")
        header.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: {0};".format(
                Palette.TEXT_PRIMARY))
        layout.addWidget(header)

        # --- Filter row ---
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        filter_row.addWidget(QLabel("Project:"))
        self._project_filter = QComboBox()
        self._project_filter.addItem("(all)", None)
        self._project_filter.setStyleSheet(self._combo_style())
        self._project_filter.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self._project_filter)

        filter_row.addWidget(QLabel("Scene:"))
        self._scene_filter = QComboBox()
        self._scene_filter.addItem("(all)", None)
        self._scene_filter.setStyleSheet(self._combo_style())
        self._scene_filter.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self._scene_filter)

        filter_row.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter by text...")
        self._search_edit.setStyleSheet(self._input_style())
        self._search_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self._search_edit, 1)

        layout.addLayout(filter_row)

        # --- Table ---
        self._table = QTableWidget()
        self._table.setColumnCount(8)
        self._table.setHorizontalHeaderLabels([
            "Date / Time", "Project", "Scene", "Character / Speaker",
            "Voice", "Duration", "Status", "Output File",
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(self._table_style())
        # Column widths
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Date
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Project
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Scene
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Character
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Voice
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)  # Duration
        header_view.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)  # Status
        header_view.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)            # Output
        self._table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table, 1)

        # --- Empty state label ---
        self._empty_label = QLabel("No generated audio yet.")
        self._empty_label.setAlignment(Qt.Alignment.AlignCenter)
        self._empty_label.setStyleSheet(
            "color: {0}; font-size: 14px; padding: 40px;".format(
                Palette.TEXT_SECONDARY))
        self._empty_label.setVisible(False)
        layout.addWidget(self._empty_label)

        # --- Action buttons ---
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._play_btn = QPushButton("▶ Play")
        self._play_btn.clicked.connect(self._on_play)
        self._play_btn.setStyleSheet(self._btn_style())
        btn_row.addWidget(self._play_btn)

        self._open_scene_btn = QPushButton("Open Scene")
        self._open_scene_btn.clicked.connect(self._on_open_scene)
        self._open_scene_btn.setStyleSheet(self._btn_style())
        btn_row.addWidget(self._open_scene_btn)

        self._reveal_btn = QPushButton("Reveal File")
        self._reveal_btn.clicked.connect(self._on_reveal)
        self._reveal_btn.setStyleSheet(self._btn_style())
        btn_row.addWidget(self._reveal_btn)

        self._delete_btn = QPushButton("Delete Entry")
        self._delete_btn.clicked.connect(self._on_delete)
        self._delete_btn.setStyleSheet(self._btn_style())
        btn_row.addWidget(self._delete_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setStyleSheet(self._btn_style())
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

        # Populate filter dropdowns
        self._populate_filters()

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    def _populate_filters(self) -> None:
        """Populate the Project and Scene filter dropdowns from entries."""
        projects = set()
        scenes = set()
        for e in self._entries:
            if e.project:
                projects.add(e.project)
            if e.scene_name:
                scenes.add(e.scene_name)
        for p in sorted(projects):
            self._project_filter.addItem(p, p)
        for s in sorted(scenes):
            self._scene_filter.addItem(s, s)

    def _apply_filters(self) -> None:
        """Apply the current filter selections and refresh the table."""
        project_filter = self._project_filter.currentData()
        scene_filter = self._scene_filter.currentData()
        search_text = self._search_edit.text().strip().lower()

        self._filtered = []
        for e in self._entries:
            # Project filter
            if project_filter is not None and e.project != project_filter:
                continue
            # Scene filter
            if scene_filter is not None and e.scene_name != scene_filter:
                continue
            # Text search
            if search_text:
                searchable = " ".join([
                    e.project or "", e.scene_name or "",
                    e.speaker or "", e.voice_name or "",
                    e.output_path or "",
                ]).lower()
                if search_text not in searchable:
                    continue
            self._filtered.append(e)

        # Sort newest first (entries are already sorted by HistoryManager,
        # but we re-sort to be safe)
        self._filtered.sort(key=lambda e: e.timestamp, reverse=True)

        self._refresh_table()

    def _refresh_table(self) -> None:
        """Refresh the table with the filtered entries."""
        if not self._filtered:
            self._table.setVisible(False)
            self._empty_label.setVisible(True)
            return
        self._table.setVisible(True)
        self._empty_label.setVisible(False)

        self._table.setRowCount(len(self._filtered))
        for row, entry in enumerate(self._filtered):
            # Date / Time
            self._table.setItem(row, 0, QTableWidgetItem(entry.timestamp or "Unknown"))
            # Project
            self._table.setItem(row, 1, QTableWidgetItem(entry.project or "—"))
            # Scene
            scene_display = entry.scene_name or "—"
            if entry.scene_id is None and entry.scene_name is None:
                scene_display = "—"
            self._table.setItem(row, 2, QTableWidgetItem(scene_display))
            # Character / Speaker
            char_display = "—"
            if entry.speaker:
                char_display = entry.speaker
            if entry.character_id and self._project:
                char = self._project.get_character(entry.character_id)
                if char:
                    char_display = char.name
            self._table.setItem(row, 3, QTableWidgetItem(char_display))
            # Voice
            self._table.setItem(row, 4, QTableWidgetItem(entry.voice_name or "—"))
            # Duration
            dur = entry.output_duration
            dur_text = "{0:.1f}s".format(dur) if dur > 0 else "—"
            self._table.setItem(row, 5, QTableWidgetItem(dur_text))
            # Status (derive from output_path existence — resolved against
            # the SAME authoritative base as playback, P3.44)
            if entry.output_path:
                import os
                status = "Available" if os.path.isfile(
                    self._resolve_path(entry.output_path)) else "File missing"
            else:
                status = "No output"
            self._table.setItem(row, 6, QTableWidgetItem(status))
            # Output file
            self._table.setItem(row, 7, QTableWidgetItem(entry.output_path or "—"))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _get_selected_entry(self) -> Optional[HistoryEntry]:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._filtered):
            return None
        return self._filtered[row]

    def _on_double_click(self) -> None:
        """Double-click = play audio."""
        self._on_play()

    def _on_play(self) -> None:
        """Play the selected entry's audio file."""
        entry = self._get_selected_entry()
        if entry is None:
            return
        if not entry.output_path:
            QMessageBox.information(self, "History", "No audio file for this entry.")
            return
        import os
        if not os.path.isfile(self._resolve_path(entry.output_path)):
            QMessageBox.warning(self, "History",
                "Audio file not found:\n{0}\n\n"
                "The file may have been moved or deleted.".format(entry.output_path))
            return
        # Emit signal for MainWindow to handle playback — the path is
        # resolved against the same authoritative base the pre-check
        # used (P3.44: one resolution, no modal-vs-playback mismatch).
        self._play_requested(self._resolve_path(entry.output_path))

    def _play_requested(self, path: str) -> None:
        """Request audio playback — handled by MainWindow.

        P3.44: ``path`` arrives RESOLVED (absolute — see _on_play /
        _resolve_path; the engine app root is the authoritative base,
        matching MainWindow's _load_history_entry_into_player and the
        engine's own load_wav). The WaveformPlayer's peak loader
        resolves plain paths against the process CWD — passing an
        app-root-relative string produced a trackless transport (no
        waveform, misleading playhead). The absolute path is also what
        the entry-click load path uses, so the History screen Play and
        the sidebar click land on the SAME current track.
        """
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, '_engine') and parent._engine is not None:
                parent._engine.play_audio(path)
                if hasattr(parent, '_waveform'):
                    entry = self._get_selected_entry()
                    if entry:
                        # Only replace the transport when the track actually
                        # differs — replaying the current entry must not
                        # reset its waveform mid-audition.
                        if parent._waveform.current_path != path:
                            parent._waveform.set_audio_info(
                                entry.output_duration, entry.output_sample_rate,
                                path)
                        parent._waveform.set_playback_state("playing")
                return
            parent = parent.parent()

    def _on_open_scene(self) -> None:
        """Open the Scene referenced by the selected History entry."""
        entry = self._get_selected_entry()
        if entry is None:
            return
        if not entry.scene_id:
            QMessageBox.information(self, "History",
                "This entry has no Scene reference.\n\n"
                "It may be from an older version before Scene tracking was added.")
            return
        # Emit the open-scene signal — MainWindow will handle navigation
        self.open_scene_requested.emit(
            entry.project or "",
            entry.scene_id or "",
            entry.scene_name or "",
        )
        self.accept()

    def _on_reveal(self) -> None:
        """Reveal the output file in the file manager."""
        entry = self._get_selected_entry()
        if entry is None or not entry.output_path:
            QMessageBox.information(self, "History", "No output file for this entry.")
            return
        import os, subprocess, platform
        path = os.path.abspath(self._resolve_path(entry.output_path))
        if not os.path.isfile(path):
            QMessageBox.warning(self, "History", "File not found:\n{0}".format(path))
            return
        system = platform.system()
        try:
            if system == "Windows":
                subprocess.Popen(["explorer", "/select,", path])
            elif system == "Darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception as exc:
            QMessageBox.warning(self, "History",
                "Could not open file manager:\n{0}".format(exc))

    def _on_delete(self) -> None:
        """Delete the selected History entry."""
        entry = self._get_selected_entry()
        if entry is None:
            return
        reply = QMessageBox.question(
            self, "Delete History Entry",
            "Delete this history entry?\n\n"
            "  Date: {0}\n  Project: {1}\n  Scene: {2}\n  Output: {3}\n\n"
            "The audio file will NOT be deleted.".format(
                entry.timestamp, entry.project or "—",
                entry.scene_name or "—", entry.output_path or "—"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Find the HistoryManager via the parent chain
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, '_engine') and parent._engine is not None:
                parent._engine.delete_history_entry(entry.id, delete_audio=False)
                break
            parent = parent.parent()
        # Remove from our local list and refresh
        self._entries = [e for e in self._entries if e.id != entry.id]
        self._apply_filters()
        self._populate_filters()

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------
    def _combo_style(self) -> str:
        return (
            "QComboBox {{ background-color: {bg_alt}; color: {text}; "
            "border: 1px solid {border}; border-radius: 4px; padding: 4px 8px; }}"
        ).format(bg_alt=Palette.BG_SURFACE_ALT, text=Palette.TEXT_PRIMARY,
                 border=Palette.BORDER)

    def _input_style(self) -> str:
        return (
            "QLineEdit {{ background-color: {bg_alt}; color: {text}; "
            "border: 1px solid {border}; border-radius: 4px; padding: 4px 8px; }}"
        ).format(bg_alt=Palette.BG_SURFACE_ALT, text=Palette.TEXT_PRIMARY,
                 border=Palette.BORDER)

    def _table_style(self) -> str:
        return (
            "QTableWidget {{ background-color: {bg}; color: {text}; "
            "border: 1px solid {border}; border-radius: 4px; "
            "gridline-color: {border}; }}"
            "QHeaderView::section {{ background-color: {bg_alt}; "
            "color: {text_sec}; padding: 6px; border: none; "
            "border-bottom: 1px solid {border}; }}"
            "QTableWidget::item:alternate {{ background-color: {bg_alt}; }}"
        ).format(bg=Palette.BG_SURFACE, text=Palette.TEXT_PRIMARY,
                 bg_alt=Palette.BG_SURFACE_ALT, border=Palette.BORDER,
                 text_sec=Palette.TEXT_SECONDARY)

    def _btn_style(self) -> str:
        return (
            "QPushButton {{ background-color: {bg_alt}; color: {text_sec}; "
            "border: 1px solid {border}; border-radius: 6px; padding: 6px 14px; }}"
            "QPushButton:hover {{ border-color: {accent}; color: {accent}; }}"
            "QPushButton:disabled {{ color: {disabled}; }}"
        ).format(bg_alt=Palette.BG_SURFACE_ALT, text_sec=Palette.TEXT_SECONDARY,
                 border=Palette.BORDER, accent=Palette.ACCENT,
                 disabled=Palette.TEXT_DISABLED)
