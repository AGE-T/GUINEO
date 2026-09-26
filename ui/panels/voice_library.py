"""GUINEO Voice Profile manager (P3.43 unified management screen).

This is THE single authoritative Voice Profile management screen
(P3.43 §2/§3): a list/detail workflow over the authoritative
``VoiceProfile`` + ``VoiceManager`` data.

LEFT — the Voice Profile list: avatar, name, speaker metadata summary,
reference-audio availability badge per row; full keyboard navigation
(currentItemChanged drives the detail pane exactly like a mouse click,
P3.43 §20).

RIGHT — the selected profile's authoritative information (avatar, name,
gender, age, mood, description, tags, reference audio, duration, sample
rate, channels, transcript, validation state) with the actions
Play / Replace Reference / Edit / Export / Delete.

FOOTER — Create Voice Profile / Import Voice Profile (both open the
single shared editor, ``VoiceImportDialog``).

All mutations go through the Engine facade; the dialog subscribes to
VOICE_CHANGED and refreshes itself (and every other dependent selector
refreshes through the same authoritative notification, P3.43 §6/§25).
Deleting a profile is dependency-aware: a scanner callback reports the
Characters/Scenes that reference the profile, and a clearer callback
clears those references after an explicit confirmation (P3.43 §11).

Legacy note (P3.43 §12): the previous chained-QInputDialog import and
name-only create flows lived here; they are retired — Create/Import both
route into the shared editor. The class keeps its historical internal
name (VoiceLibraryDialog) for import-path compatibility; the user-facing
concept is "Voice Profiles".
"""

from __future__ import annotations
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QFileDialog, QMessageBox,
    QFrame, QSizePolicy, QPlainTextEdit, QWidget,
)

from ui.theme import Palette
from ui.feedback import FeedbackDialog
from ui.widgets.avatar import AvatarLabel
from engine.models import VoiceProfile
from engine.events import EventType

# Status dot colors — reference-audio availability badge.
_DOT_HAS_REF = "#22C55E"    # green: ready for cloning
_DOT_NO_REF = "#F59E0B"     # amber: missing reference audio


class VoiceLibraryDialog(QDialog):
    """The unified Voice Profile management screen.

    Args:
        engine: the Engine (facade operations + VOICE_CHANGED events).
        parent: parent widget.
        dependency_scanner: optional ``voice_id -> List[str]`` callback
            returning human-readable descriptions of the Characters /
            Scenes that reference the profile (delete safety, P3.43 §11).
        reference_clearer: optional ``voice_id -> None`` callback invoked
            AFTER a confirmed delete to clear the references reported by
            the scanner (prevents dangling Character/Scene ids).
    """

    voice_selected = Signal(str)  # voice_id (double-click / Select action)

    def __init__(self, engine, parent=None,
                 dependency_scanner: Optional[Callable[[str], List[str]]] = None,
                 reference_clearer: Optional[Callable[[str], None]] = None):
        super().__init__(parent)
        self._engine = engine
        self._dependency_scanner = dependency_scanner
        self._reference_clearer = reference_clearer
        self._selected_voice_id: Optional[str] = None
        self._playing_id: Optional[str] = None

        self.setWindowTitle("Voice Profiles")
        self.resize(900, 580)
        self.setMinimumSize(740, 480)
        self._build_ui()
        self._refresh()

        # Refresh from the authoritative state on every mutation — the
        # manager stays live while the shared editor (a child modal)
        # changes the data underneath it (P3.43 §6/§28).
        if self._engine is not None:
            self._engine.subscribe(EventType.VOICE_CHANGED,
                                   self._on_engine_voice_changed)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # --- Left: the Voice Profile list ---
        left = QVBoxLayout()
        left.setSpacing(6)

        header = QLabel("Voice Profiles")
        header.setStyleSheet(
            "color: {0}; font-size: 14px; font-weight: bold;".format(
                Palette.TEXT_PRIMARY))
        left.addWidget(header)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(False)
        # currentItemChanged covers BOTH mouse and keyboard selection —
        # the detail pane can never go stale after keyboard navigation
        # (P3.43 §20).
        self._list.currentItemChanged.connect(self._on_current_item_changed)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        left.addWidget(self._list, 1)

        # Empty state (shown when there are no profiles at all).
        self._empty_label = QLabel(
            "No voice profiles yet.\n"
            "Create or import your first voice profile below.")
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            "color: {0}; font-size: 12px; padding: 24px;".format(
                Palette.TEXT_SECONDARY))
        left.addWidget(self._empty_label)

        # Footer actions: Create / Import (the single shared editor).
        footer = QHBoxLayout()
        footer.setSpacing(6)
        create_btn = QPushButton("+ Create Voice Profile")
        create_btn.setProperty("accent", "true")
        create_btn.setToolTip("Create a new, empty voice profile")
        create_btn.clicked.connect(self._on_create)
        footer.addWidget(create_btn, 1)

        import_btn = QPushButton("Import Voice Profile")
        import_btn.setToolTip("Import a voice profile from a reference WAV")
        import_btn.clicked.connect(self._on_import)
        footer.addWidget(import_btn, 1)
        left.addLayout(footer)

        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMinimumWidth(280)
        left_widget.setMaximumWidth(420)
        layout.addWidget(left_widget, 2)

        # --- Right: detail area ---
        right = QVBoxLayout()
        right.setSpacing(8)

        # Header card: avatar + name + summary
        header_frame = QFrame()
        header_frame.setObjectName("VoiceDetailHeader")
        header_frame.setStyleSheet(
            "QFrame#VoiceDetailHeader {{ background-color: {0}; "
            "border: 1px solid {1}; border-radius: 8px; }}".format(
                Palette.BG_SURFACE, Palette.BORDER))
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(12)

        self._detail_avatar = AvatarLabel(size=56)
        if self._engine is not None:
            self._detail_avatar.set_app_root(self._engine.app_root)
        header_layout.addWidget(self._detail_avatar)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self._name_label = QLabel("(no profile selected)")
        self._name_label.setStyleSheet(
            "color: {0}; font-size: 16px; font-weight: bold;".format(
                Palette.TEXT_PRIMARY))
        self._name_label.setWordWrap(True)
        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(
            "color: {0}; font-size: 12px;".format(Palette.TEXT_SECONDARY))
        self._summary_label.setWordWrap(True)
        title_col.addWidget(self._name_label)
        title_col.addWidget(self._summary_label)
        title_col.addStretch()
        header_layout.addLayout(title_col, 1)
        right.addWidget(header_frame)

        # Info grid
        info_frame = QFrame()
        info_frame.setObjectName("VoiceDetailInfo")
        info_frame.setStyleSheet(
            "QFrame#VoiceDetailInfo {{ background-color: {0}; "
            "border: 1px solid {1}; border-radius: 8px; }}".format(
                Palette.BG_SURFACE, Palette.BORDER))
        info_layout = QGridLayout(info_frame)
        info_layout.setContentsMargins(12, 10, 12, 10)
        info_layout.setHorizontalSpacing(12)
        info_layout.setVerticalSpacing(6)
        info_layout.setColumnStretch(1, 1)
        info_layout.setColumnStretch(3, 1)

        self._gender_label = QLabel("--")
        self._age_label = QLabel("--")
        self._mood_label = QLabel("--")
        self._duration_label = QLabel("--")
        self._sr_label = QLabel("--")
        self._channels_label = QLabel("--")
        self._description_label = QLabel("--")
        self._description_label.setWordWrap(True)
        self._tags_label = QLabel("--")
        self._tags_label.setWordWrap(True)

        rows = [
            ("Gender:", self._gender_label, "Age:", self._age_label),
            ("Mood:", self._mood_label, "Duration:", self._duration_label),
            ("Sample Rate:", self._sr_label, "Channels:", self._channels_label),
            ("Description:", self._description_label, "Tags:", self._tags_label),
        ]
        for row, (c1, w1, c2, w2) in enumerate(rows):
            info_layout.addWidget(self._make_caption(c1), row, 0)
            info_layout.addWidget(w1, row, 1)
            info_layout.addWidget(self._make_caption(c2), row, 2)
            info_layout.addWidget(w2, row, 3)
        right.addWidget(info_frame)

        # Reference audio card
        ref_frame = QFrame()
        ref_frame.setObjectName("VoiceDetailRef")
        ref_frame.setStyleSheet(
            "QFrame#VoiceDetailRef {{ background-color: {0}; "
            "border: 1px solid {1}; border-radius: 8px; }}".format(
                Palette.BG_SURFACE, Palette.BORDER))
        ref_layout = QVBoxLayout(ref_frame)
        ref_layout.setContentsMargins(12, 10, 12, 10)
        ref_layout.setSpacing(6)

        ref_title = QLabel("Reference Audio")
        ref_title.setStyleSheet(
            "color: {0}; font-size: 12px; font-weight: bold;".format(
                Palette.TEXT_PRIMARY))
        ref_layout.addWidget(ref_title)

        ref_grid = QGridLayout()
        ref_grid.setHorizontalSpacing(12)
        ref_grid.setVerticalSpacing(4)
        ref_grid.setColumnStretch(1, 1)
        self._file_label = QLabel("--")
        self._ref_status_label = QLabel("--")
        self._ref_status_label.setWordWrap(True)
        ref_grid.addWidget(self._make_caption("File:"), 0, 0)
        ref_grid.addWidget(self._file_label, 0, 1)
        ref_grid.addWidget(self._make_caption("Status:"), 1, 0)
        ref_grid.addWidget(self._ref_status_label, 1, 1)
        ref_layout.addLayout(ref_grid)

        self._warnings_label = QLabel("")
        self._warnings_label.setWordWrap(True)
        self._warnings_label.setStyleSheet(
            "color: {0}; font-size: 12px;".format(Palette.WARNING))
        ref_layout.addWidget(self._warnings_label)

        # Action row: Play / Replace Reference / Edit
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self._play_btn = QPushButton("Play")
        self._play_btn.setToolTip("Play the reference audio of this profile")
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._on_play)
        action_row.addWidget(self._play_btn)

        replace_btn = QPushButton("Replace Reference...")
        replace_btn.setToolTip("Replace the reference WAV of this profile "
                               "(validated before the old one is touched)")
        replace_btn.clicked.connect(self._on_replace_reference)
        action_row.addWidget(replace_btn)

        edit_btn = QPushButton("Edit...")
        edit_btn.setToolTip("Edit this voice profile (name, metadata, "
                            "transcript, avatar, reference audio)")
        edit_btn.clicked.connect(self._on_edit)
        action_row.addWidget(edit_btn)
        action_row.addStretch()
        ref_layout.addLayout(action_row)
        right.addWidget(ref_frame)

        # Transcript (read-only view — editing belongs to the single editor)
        transcript_title = QLabel("Reference Transcript")
        transcript_title.setStyleSheet(
            "color: {0}; font-size: 12px; font-weight: bold;".format(
                Palette.TEXT_PRIMARY))
        right.addWidget(transcript_title)
        self._transcript_view = QPlainTextEdit()
        self._transcript_view.setReadOnly(True)
        self._transcript_view.setPlaceholderText("(no transcript)")
        self._transcript_view.setMaximumHeight(120)
        right.addWidget(self._transcript_view, 1)

        # Bottom row: Export / Delete / Select / Close
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)
        export_btn = QPushButton("Export...")
        export_btn.setToolTip("Export this voice profile (metadata, "
                              "reference WAV, transcript, avatar)")
        export_btn.clicked.connect(self._on_export)
        bottom_row.addWidget(export_btn)

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setToolTip("Delete this voice profile "
                                    "(dependency-checked)")
        self._delete_btn.setStyleSheet("color: {0};".format(Palette.ERROR))
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._on_delete)
        bottom_row.addWidget(self._delete_btn)

        bottom_row.addStretch()

        self._select_btn = QPushButton("Select This Voice")
        self._select_btn.setProperty("accent", "true")
        self._select_btn.setEnabled(False)
        self._select_btn.clicked.connect(self._on_select)
        bottom_row.addWidget(self._select_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bottom_row.addWidget(close_btn)
        right.addLayout(bottom_row)

        right_widget = QWidget()
        right_widget.setLayout(right)
        layout.addWidget(right_widget, 3)

    def _make_caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: {0};".format(Palette.TEXT_SECONDARY))
        return label

    # ------------------------------------------------------------------
    # Data refresh
    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        """Reload the voice list from the authoritative state."""
        self._list.blockSignals(True)
        self._list.clear()
        voices: List[VoiceProfile] = []
        if self._engine is not None:
            voices = self._engine.list_voices()
        for v in voices:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, v.id)
            item.setSizeHint(_row_widget_size())
            self._list.addItem(item)
            self._list.setItemWidget(item, _VoiceRowWidget(
                v, self._engine.app_root if self._engine is not None else None))
        self._list.blockSignals(False)

        has_profiles = bool(voices)
        self._empty_label.setVisible(not has_profiles)

        # Restore the selection if the profile still exists, else clear.
        selected_still_exists = any(
            v.id == self._selected_voice_id for v in voices)
        if self._selected_voice_id is not None and selected_still_exists:
            self._select_by_id(self._selected_voice_id)
            self._show_voice_info(self._selected_voice_id)
        elif has_profiles:
            # First profile becomes the selection (silent programmatic
            # selection — display only, never a mutation, P3.43 §29).
            self._select_by_id(voices[0].id)
            self._show_voice_info(voices[0].id)
        else:
            self._selected_voice_id = None
            self._clear_info()

    def _select_by_id(self, voice_id: str) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == voice_id:
                self._list.blockSignals(True)
                self._list.setCurrentItem(item)
                self._list.blockSignals(False)
                self._list.scrollToItem(item)
                return

    def _on_engine_voice_changed(self, event) -> None:
        """VOICE_CHANGED → refresh from the authoritative state."""
        self._refresh()

    # ------------------------------------------------------------------
    # Selection (mouse AND keyboard — one path, P3.43 §20)
    # ------------------------------------------------------------------
    def _on_current_item_changed(self, current, previous) -> None:
        if current is None:
            return
        voice_id = current.data(Qt.ItemDataRole.UserRole)
        self._show_voice_info(voice_id)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        # Double-click = the quick "select and close" path (legacy
        # contract kept — the primary management path is the detail pane).
        self._show_voice_info(item.data(Qt.ItemDataRole.UserRole))
        self._on_select()

    def _show_voice_info(self, voice_id: str) -> None:
        self._selected_voice_id = voice_id
        self._select_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)
        if self._engine is None:
            return
        voice = self._engine.get_voice(voice_id)
        if voice is None:
            self._clear_info()
            return

        # Header
        self._detail_avatar.set_voice(voice)
        self._name_label.setText(voice.name)
        summary_parts = []
        if voice.gender:
            summary_parts.append(voice.gender)
        if voice.age_range:
            summary_parts.append(voice.age_range)
        if voice.duration > 0:
            summary_parts.append("{0:.1f}s reference".format(voice.duration))
        summary = " · ".join(summary_parts) if summary_parts else "No metadata"
        self._summary_label.setText(summary)

        # Info grid
        self._gender_label.setText(voice.gender or "(unset)")
        self._age_label.setText(voice.age_range or "(unset)")
        self._mood_label.setText(voice.mood or "(unset)")
        self._description_label.setText(voice.description or "(none)")
        self._tags_label.setText(", ".join(voice.tags) if voice.tags else "(none)")

        # Reference audio
        if voice.has_reference:
            self._file_label.setText("reference.wav")
            self._file_label.setToolTip(voice.reference_audio_path)
            self._duration_label.setText("{0:.2f} s".format(voice.duration))
            self._sr_label.setText("{0} Hz".format(voice.sample_rate))
            self._channels_label.setText("{0} ({1})".format(
                voice.channels,
                "mono" if voice.channels == 1 else "stereo"))
            self._ref_status_label.setText("Reference audio available")
            self._ref_status_label.setStyleSheet(
                "color: {0};".format(Palette.SUCCESS))
            self._play_btn.setEnabled(True)
        else:
            self._file_label.setText("(no reference audio)")
            self._file_label.setToolTip("")
            self._duration_label.setText("--")
            self._sr_label.setText("--")
            self._channels_label.setText("--")
            self._ref_status_label.setText(
                "Missing reference audio — add one to use this profile "
                "for voice cloning")
            self._ref_status_label.setStyleSheet(
                "color: {0};".format(Palette.WARNING))
            self._play_btn.setEnabled(False)

        # Validation (authoritative)
        warnings = self._engine.validate_voice_profile(voice_id)
        if warnings:
            self._warnings_label.setText(
                "\n".join("- {0}".format(w) for w in warnings))
        else:
            self._warnings_label.setText("")

        # Transcript
        self._transcript_view.setPlainText(voice.reference_transcript or "")

    def _clear_info(self) -> None:
        self._selected_voice_id = None
        self._select_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        self._play_btn.setEnabled(False)
        self._detail_avatar.set_voice(None)
        self._name_label.setText("(no profile selected)")
        self._summary_label.setText("")
        for lbl in (self._gender_label, self._age_label, self._mood_label,
                    self._description_label, self._tags_label):
            lbl.setText("--")
        self._file_label.setText("--")
        self._duration_label.setText("--")
        self._sr_label.setText("--")
        self._channels_label.setText("--")
        self._ref_status_label.setText("--")
        self._ref_status_label.setStyleSheet("color: {0};".format(Palette.TEXT_SECONDARY))
        self._warnings_label.setText("")
        self._transcript_view.setPlainText("")

    # ------------------------------------------------------------------
    # Actions — all through the Engine facade (P3.43 §25)
    # ------------------------------------------------------------------
    def _on_create(self) -> None:
        """Create: the shared editor in CREATE mode (empty profile)."""
        if self._engine is None:
            return
        from ui.panels.voice_import_dialog import VoiceImportDialog
        dialog = VoiceImportDialog(
            mode=VoiceImportDialog.MODE_CREATE, engine=self._engine,
            parent=self)
        dialog.exec()
        # VOICE_CHANGED refreshes the list; select the new profile.
        if dialog.applied_profile_id():
            self._selected_voice_id = dialog.applied_profile_id()
            self._refresh()

    def _on_import(self) -> None:
        """Import: the shared editor in IMPORT mode (reference WAV)."""
        if self._engine is None:
            return
        from ui.panels.voice_import_dialog import VoiceImportDialog
        dialog = VoiceImportDialog(
            mode=VoiceImportDialog.MODE_IMPORT, engine=self._engine,
            parent=self)
        dialog.exec()
        if dialog.applied_profile_id():
            self._selected_voice_id = dialog.applied_profile_id()
            self._refresh()

    def _on_edit(self) -> None:
        """Edit: the shared editor in EDIT mode (existing profile, stable id)."""
        if self._engine is None or not self._selected_voice_id:
            return
        voice = self._engine.get_voice(self._selected_voice_id)
        if voice is None:
            return
        from ui.panels.voice_import_dialog import VoiceImportDialog
        dialog = VoiceImportDialog(
            mode=VoiceImportDialog.MODE_EDIT, engine=self._engine,
            profile=voice, parent=self)
        dialog.exec()
        self._refresh()

    def _on_play(self) -> None:
        """Play / stop the reference audio through the shared engine player."""
        if self._engine is None or not self._selected_voice_id:
            return
        if self._playing_id == self._selected_voice_id:
            self._engine.stop_playback()
            self._playing_id = None
            self._play_btn.setText("Play")
            return
        path = self._engine.voice_reference_path(self._selected_voice_id)
        if not path:
            return
        self._engine.play_audio(path)
        self._playing_id = self._selected_voice_id
        self._play_btn.setText("Stop")

    def _on_replace_reference(self) -> None:
        """Replace the reference WAV (validated before the swap)."""
        if self._engine is None or not self._selected_voice_id:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select New Reference WAV", "", "WAV Files (*.wav)")
        if not path:
            return
        try:
            # Empty transcript → the existing transcript is preserved.
            self._engine.import_voice_reference(
                self._selected_voice_id, path, "")
            self._show_voice_info(self._selected_voice_id)
        except Exception as exc:
            QMessageBox.critical(
                self, "Replace Reference Audio",
                "The reference audio was not replaced:\n{0}".format(exc))

    def _on_export(self) -> None:
        """Export the selected voice profile to a folder."""
        if self._engine is None or not self._selected_voice_id:
            QMessageBox.information(
                self, "Export Voice Profile",
                "Select a voice profile to export first.")
            return
        voice = self._engine.get_voice(self._selected_voice_id)
        voice_name = voice.name if voice else self._selected_voice_id
        export_dir = QFileDialog.getExistingDirectory(
            self, "Select Export Folder (a subfolder '{0}' will be created)"
            .format(self._selected_voice_id))
        if not export_dir:
            return
        try:
            path = self._engine.export_voice_profile(
                self._selected_voice_id, export_dir)
            FeedbackDialog.information(
                self, "Export Voice Profile",
                "Voice profile '{0}' exported".format(voice_name),
                "Location: {0}\n\n"
                "The exported folder contains the profile metadata, "
                "reference WAV, transcript and avatar.".format(path))
        except Exception as exc:
            QMessageBox.critical(
                self, "Export Voice Profile",
                "Failed to export voice profile:\n{0}".format(exc))

    def _on_delete(self) -> None:
        """Delete the selected profile — dependency-aware (P3.43 §11)."""
        if self._engine is None or not self._selected_voice_id:
            return
        voice_id = self._selected_voice_id
        voice = self._engine.get_voice(voice_id)
        if voice is None:
            return

        dependencies: List[str] = []
        if self._dependency_scanner is not None:
            try:
                dependencies = list(self._dependency_scanner(voice_id) or [])
            except Exception:
                dependencies = []

        if dependencies:
            message = (
                "Delete voice profile '{0}'?\n\n"
                "This profile is referenced by:\n{1}\n\n"
                "Deleting it will CLEAR these voice assignments."
                .format(voice.name,
                        "\n".join("- {0}".format(d) for d in dependencies)))
        else:
            message = ("Are you sure you want to delete '{0}'?\n"
                       "This cannot be undone.".format(voice.name))

        reply = QMessageBox.question(
            self, "Delete Voice Profile", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self._engine.delete_voice(voice_id)
        except Exception as exc:
            QMessageBox.critical(
                self, "Delete Voice Profile",
                "Failed to delete voice profile:\n{0}".format(exc))
            return
        # Clear the references AFTER a successful delete (never silently
        # leave dangling Character/Scene ids, P3.43 §11).
        if dependencies and self._reference_clearer is not None:
            try:
                self._reference_clearer(voice_id)
            except Exception as exc:
                logger_warn("Failed to clear voice references: {0}".format(exc))
        self._selected_voice_id = None
        self._clear_info()
        self._refresh()

    def _on_select(self) -> None:
        """Select the current profile and close the dialog."""
        if self._selected_voice_id:
            self.voice_selected.emit(self._selected_voice_id)
            self.accept()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def done(self, result: int) -> None:
        # Unsubscribe from the engine before closing so the event bus
        # never keeps a reference to a destroyed dialog.
        if self._engine is not None:
            try:
                self._engine.unsubscribe(EventType.VOICE_CHANGED,
                                         self._on_engine_voice_changed)
            except Exception:
                pass
        if self._playing_id is not None and self._engine is not None:
            try:
                self._engine.stop_playback()
            except Exception:
                pass
            self._playing_id = None
        super().done(result)


def logger_warn(text: str) -> None:
    from engine.logger import get_logger
    get_logger("voice_library").warning("%s", text)


def _row_widget_size():
    from PySide6.QtCore import QSize
    return QSize(0, 58)


class _VoiceRowWidget(QWidget):
    """One Voice Profile row in the manager list.

    Shows avatar + name + speaker-metadata summary + a reference
    availability dot. Entirely passive (transparent for mouse events →
    the QListWidget handles selection, including keyboard navigation).
    """

    def __init__(self, voice: VoiceProfile, app_root: Optional[str] = None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        self._avatar = AvatarLabel(size=36)
        if app_root:
            self._avatar.set_app_root(app_root)
        self._avatar.set_voice(voice)
        layout.addWidget(self._avatar)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        name = QLabel(voice.name)
        name.setStyleSheet(
            "color: {0}; font-size: 13px; font-weight: bold; "
            "background: transparent; border: none;".format(
                Palette.TEXT_PRIMARY))
        text_col.addWidget(name)

        meta_parts = []
        if voice.gender:
            meta_parts.append(voice.gender)
        if voice.age_range:
            meta_parts.append(voice.age_range)
        if voice.duration > 0:
            meta_parts.append("{0:.1f}s".format(voice.duration))
        meta = QLabel(" · ".join(meta_parts) if meta_parts else "No metadata")
        meta.setStyleSheet(
            "color: {0}; font-size: 12px; background: transparent; "
            "border: none;".format(Palette.TEXT_SECONDARY))
        text_col.addWidget(meta)
        layout.addLayout(text_col, 1)

        dot_color = _DOT_HAS_REF if voice.has_reference else _DOT_NO_REF
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(
            "background-color: {0}; border-radius: 5px; "
            "border: none;".format(dot_color))
        dot.setToolTip("Reference audio available" if voice.has_reference
                       else "No reference audio")
        layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
