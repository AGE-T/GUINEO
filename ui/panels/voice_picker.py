"""GUINEO lightweight Voice picker (P3.43 §15/§27).

A small selection popup used where the user only wants to CHOOSE the
active voice (the Friendly tab's "Change" button). It deliberately does
NOT embed the full Voice Profile management experience: it lists the
authoritative VoiceProfiles, highlights the current selection, and
offers one explicit "Manage Voice Profiles…" action that opens the
unified management screen.

The RightPanel selectors stay lightweight; full management always leads
back to the unified Voice Profiles screen (P3.43 final principle).
"""

from __future__ import annotations
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QFrame, QWidget,
)

from ui.theme import Palette
from ui.widgets.avatar import AvatarLabel
from engine.models import VoiceProfile


class VoicePickerDialog(QDialog):
    """Lightweight VoiceProfile selection popup.

    Signals:
        voice_selected(str): the chosen voice id (explicit user action).
        manage_requested(): the user asked for the full management screen.
    """

    voice_selected = Signal(str)
    manage_requested = Signal()

    def __init__(self, voices: List[VoiceProfile],
                 current_voice_id: Optional[str] = None,
                 app_root: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._app_root = app_root
        self.setWindowTitle("Select Voice")
        self.setModal(True)
        self.resize(360, 420)
        self.setMinimumSize(320, 300)
        self._build_ui(voices, current_voice_id)

    def _build_ui(self, voices: List[VoiceProfile],
                  current_voice_id: Optional[str]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QLabel("Select a voice profile for this scene:")
        header.setStyleSheet(
            "color: {0}; font-size: 12px;".format(Palette.TEXT_SECONDARY))
        header.setWordWrap(True)
        layout.addWidget(header)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_current_changed)
        self._list.itemDoubleClicked.connect(self._on_activated)
        layout.addWidget(self._list, 1)

        for v in voices:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, v.id)
            item.setSizeHint(_row_size())
            self._list.addItem(item)
            self._list.setItemWidget(item, _PickerRowWidget(v, self._app_root))
            if v.id == current_voice_id:
                self._list.blockSignals(True)
                self._list.setCurrentItem(item)
                self._list.blockSignals(False)

        # Empty state
        if not voices:
            empty = QLabel("No voice profiles yet.\n"
                           "Use 'Manage Voice Profiles…' to create one.")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                "color: {0}; font-size: 12px; padding: 18px;".format(
                    Palette.TEXT_SECONDARY))
            layout.addWidget(empty)

        # Bottom bar: Manage (explicit management entry) + OK/Cancel
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        manage_btn = QPushButton("Manage Voice Profiles…")
        manage_btn.setToolTip("Open the Voice Profiles management screen")
        manage_btn.clicked.connect(self._on_manage)
        bottom.addWidget(manage_btn)
        bottom.addStretch()

        ok_btn = QPushButton("Select")
        ok_btn.setProperty("accent", "true")
        ok_btn.clicked.connect(self._on_activated_current)
        bottom.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(cancel_btn)
        layout.addLayout(bottom)

        self._ok_btn = ok_btn
        self._update_ok_enabled()

    # ------------------------------------------------------------------
    def selected_voice_id(self) -> Optional[str]:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_current_changed(self, current, previous) -> None:
        self._update_ok_enabled()

    def _update_ok_enabled(self) -> None:
        self._ok_btn.setEnabled(self._list.currentItem() is not None)

    def _on_activated_current(self) -> None:
        voice_id = self.selected_voice_id()
        if voice_id:
            self.voice_selected.emit(voice_id)
            self.accept()

    def _on_activated(self, item: QListWidgetItem) -> None:
        self.voice_selected.emit(item.data(Qt.ItemDataRole.UserRole))
        self.accept()

    def _on_manage(self) -> None:
        # Explicit management entry — opens the unified screen; the
        # picker closes without changing the selection.
        self.manage_requested.emit()
        self.reject()


def _row_size():
    from PySide6.QtCore import QSize
    return QSize(0, 50)


class _PickerRowWidget(QWidget):
    """One row in the picker list: avatar + name + short metadata."""

    def __init__(self, voice: VoiceProfile, app_root: Optional[str] = None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        avatar = AvatarLabel(size=28)
        if app_root:
            avatar.set_app_root(app_root)
        avatar.set_voice(voice)
        layout.addWidget(avatar)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        name = QLabel(voice.name)
        name.setStyleSheet(
            "color: {0}; font-size: 12px; font-weight: bold; "
            "background: transparent; border: none;".format(
                Palette.TEXT_PRIMARY))
        text_col.addWidget(name)
        meta_parts = []
        if voice.gender:
            meta_parts.append(voice.gender)
        if voice.duration > 0:
            meta_parts.append("{0:.1f}s".format(voice.duration))
        if not voice.has_reference:
            meta_parts.append("no reference")
        meta = QLabel(" · ".join(meta_parts) if meta_parts else "No metadata")
        meta.setStyleSheet(
            "color: {0}; font-size: 11px; background: transparent; "
            "border: none;".format(Palette.TEXT_SECONDARY))
        text_col.addWidget(meta)
        layout.addLayout(text_col, 1)
