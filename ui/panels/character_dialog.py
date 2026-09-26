"""
SpeechStudio Character Management Dialog.

P3.2: Foundation for Character entity management.
- Lists Characters from the active Project
- Create / rename / delete Characters
- Assign Voice Profile to a Character
- Add Character references to the active Scene

Backed directly by Project.characters — no second data source.
"""

from __future__ import annotations
from typing import Optional, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QInputDialog, QMessageBox,
    QComboBox, QGroupBox, QFormLayout, QLineEdit, QWidget,
)

from engine.models import Character, VoiceProfile
from engine.logger import get_logger
# P3.43: fixed latent import — engine.logger has no module-level `logger`
# name, so importing it crashed the dialog the moment Character
# Management was opened (tests only AST-parsed the file, never imported
# it, which is why this survived).
logger = get_logger("character_dialog")
from ui.theme import Palette


class CharacterManagementDialog(QDialog):
    """Dialog for managing Characters in the active Project.

    All changes are made directly on the Project's character list.
    The caller is responsible for saving the Project after the dialog
    closes.
    """

    characters_changed = Signal()  # emitted when characters are modified
    # P3.43 §16: explicit management entry — opens the unified Voice
    # Profiles screen (never a Character-specific voice management UI).
    manage_voices_requested = Signal()

    def __init__(self, project, voices: List[VoiceProfile],
                 active_scene=None, parent=None):
        """
        Args:
            project: the active Project (authoritative source).
            voices: list of available VoiceProfiles for assignment.
            active_scene: the currently active Scene (for character refs).
            parent: parent widget.
        """
        super().__init__(parent)
        self._project = project
        self._voices = voices
        self._active_scene = active_scene
        self._selected_character: Optional[Character] = None
        self._build_ui()
        self._refresh_character_list()

    def _build_ui(self) -> None:
        self.setWindowTitle("Character Management")
        self.setMinimumSize(500, 400)
        self.setStyleSheet("background-color: {0};".format(Palette.BG_BASE))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Header
        header = QLabel("Characters in Project: {0}".format(self._project.name))
        header.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: {0};".format(
                Palette.TEXT_PRIMARY))
        layout.addWidget(header)

        # Character list
        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget {{ background-color: {0}; color: {1}; "
            "border: 1px solid {2}; border-radius: 4px; }}".format(
                Palette.BG_SURFACE, Palette.TEXT_PRIMARY, Palette.BORDER))
        self._list.currentRowChanged.connect(self._on_character_selected)
        layout.addWidget(self._list, 1)

        # Character details
        detail_group = QGroupBox("Character Details")
        # P3.43: fixed latent crash — the stylesheet belongs to the
        # QGroupBox WIDGET (QFormLayout has no setStyleSheet; the dialog
        # crashed on construction, i.e. the moment Character Management
        # was opened).
        detail_layout = QFormLayout(detail_group)
        detail_group.setStyleSheet(
            "QGroupBox {{ color: {0}; }}".format(Palette.TEXT_PRIMARY))

        self._name_edit = QLineEdit()
        self._name_edit.setStyleSheet(
            "QLineEdit {{ background-color: {0}; color: {1}; "
            "border: 1px solid {2}; border-radius: 4px; padding: 4px; }}".format(
                Palette.BG_SURFACE_ALT, Palette.TEXT_PRIMARY, Palette.BORDER))
        detail_layout.addRow("Name:", self._name_edit)

        self._role_edit = QLineEdit()
        self._role_edit.setPlaceholderText("e.g. protagonist, narrator")
        self._role_edit.setStyleSheet(self._name_edit.styleSheet())
        detail_layout.addRow("Role:", self._role_edit)

        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("Optional description")
        self._desc_edit.setStyleSheet(self._name_edit.styleSheet())
        detail_layout.addRow("Description:", self._desc_edit)

        # Voice Profile assignment
        voice_row = QHBoxLayout()
        voice_row.setSpacing(4)
        self._voice_combo = QComboBox()
        self._voice_combo.addItem("(no voice assigned)", None)
        for v in self._voices:
            label = v.name
            if v.duration > 0:
                label += " ({0:.1f}s)".format(v.duration)
            self._voice_combo.addItem(label, v.id)
        self._voice_combo.setStyleSheet(self._name_edit.styleSheet())
        voice_row.addWidget(self._voice_combo, 1)
        # P3.43 §16: "Manage Voice Profiles…" opens the unified manager —
        # Characters use the SAME VoiceProfile entity, never a copy.
        manage_voices_btn = QPushButton("Manage Voice Profiles…")
        manage_voices_btn.setToolTip(
            "Open the Voice Profiles management screen")
        manage_voices_btn.clicked.connect(self.manage_voices_requested.emit)
        voice_row.addWidget(manage_voices_btn)
        voice_row_w = QWidget()
        voice_row_w.setLayout(voice_row)
        detail_layout.addRow("Voice Profile:", voice_row_w)

        layout.addWidget(detail_group)

        # Buttons row
        btn_row = QHBoxLayout()

        self._add_btn = QPushButton("New Character")
        self._add_btn.clicked.connect(self._on_add_character)
        btn_row.addWidget(self._add_btn)

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._on_delete_character)
        btn_row.addWidget(self._delete_btn)

        self._apply_btn = QPushButton("Apply Changes")
        self._apply_btn.clicked.connect(self._on_apply_changes)
        btn_row.addWidget(self._apply_btn)

        self._add_to_scene_btn = QPushButton("Add to Scene")
        self._add_to_scene_btn.clicked.connect(self._on_add_to_scene)
        self._add_to_scene_btn.setToolTip(
            "Add this Character to the current Scene's character references")
        if self._active_scene is None:
            self._add_to_scene_btn.setEnabled(False)
        btn_row.addWidget(self._add_to_scene_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _refresh_character_list(self) -> None:
        """Refresh the character list from the authoritative Project."""
        self._list.clear()
        for char in self._project.characters:
            voice_name = "(no voice)"
            if char.voice_profile_id:
                for v in self._voices:
                    if v.id == char.voice_profile_id:
                        voice_name = v.name
                        break
            display = "{0}  →  {1}".format(char.name, voice_name)
            if char.role:
                display += "  [{0}]".format(char.role)
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, char.id)
            self._list.addItem(item)

    # P3.5 Final Correction: expose the selected character id so MainWindow
    # can derive the active Character for context list highlighting. This is
    # a read-only property sourced from the authoritative _selected_character
    # — NOT an independent boolean.
    @property
    def selected_character_id(self) -> Optional[str]:
        """Return the id of the currently selected Character, or None."""
        if self._selected_character is not None:
            return self._selected_character.id
        return None

    def _on_character_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._project.characters):
            self._selected_character = None
            self._name_edit.clear()
            self._role_edit.clear()
            self._desc_edit.clear()
            self._voice_combo.setCurrentIndex(0)
            return
        char = self._project.characters[row]
        self._selected_character = char
        self._name_edit.setText(char.name)
        self._role_edit.setText(char.role or "")
        self._desc_edit.setText(char.description or "")
        # Select the voice in the combo
        if char.voice_profile_id:
            for i in range(self._voice_combo.count()):
                if self._voice_combo.itemData(i) == char.voice_profile_id:
                    self._voice_combo.setCurrentIndex(i)
                    break
        else:
            self._voice_combo.setCurrentIndex(0)

    def _on_add_character(self) -> None:
        name, ok = QInputDialog.getText(self, "New Character", "Character name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        # Check for duplicate name
        for c in self._project.characters:
            if c.name == name:
                QMessageBox.warning(self, "Duplicate", "A character with this name already exists.")
                return
        char = Character(name=name)
        self._project.add_character(char)
        self._refresh_character_list()
        self.characters_changed.emit()
        logger.info("P3.2: Character created: '%s' (id=%s)", name, char.id)

    def _on_delete_character(self) -> None:
        if self._selected_character is None:
            return
        char = self._selected_character
        # C6: Check if character is referenced by any scene
        referencing_scenes = []
        for scene in self._project.scenes:
            if char.id in scene.character_ids:
                referencing_scenes.append(scene.name)
        if referencing_scenes:
            reply = QMessageBox.question(
                self, "Delete Character",
                "Character '{0}' is referenced by {1} scene(s):\n{2}\n\n"
                "Deleting will remove the character references from these scenes.\n"
                "Continue?".format(
                    char.name, len(referencing_scenes),
                    "\n".join("  • " + s for s in referencing_scenes)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            # Remove character_id from all scenes
            for scene in self._project.scenes:
                if char.id in scene.character_ids:
                    scene.character_ids.remove(char.id)
        # Delete the character
        self._project.characters.remove(char)
        self._refresh_character_list()
        self._on_character_selected(-1)
        self.characters_changed.emit()
        logger.info("P3.2: Character deleted: '%s' (id=%s)", char.name, char.id)

    def _on_apply_changes(self) -> None:
        if self._selected_character is None:
            return
        char = self._selected_character
        new_name = self._name_edit.text().strip()
        if not new_name:
            QMessageBox.warning(self, "Invalid", "Character name cannot be empty.")
            return
        # Check for duplicate name (excluding self)
        for c in self._project.characters:
            if c.id != char.id and c.name == new_name:
                QMessageBox.warning(self, "Duplicate", "Another character with this name already exists.")
                return
        char.name = new_name
        char.role = self._role_edit.text().strip() or ""
        char.description = self._desc_edit.text().strip() or ""
        char.voice_profile_id = self._voice_combo.currentData()
        self._refresh_character_list()
        self.characters_changed.emit()
        logger.info("P3.2: Character updated: '%s' (voice=%s)",
                     char.name, char.voice_profile_id)

    def refresh_voices(self, voices: List[VoiceProfile]) -> None:
        """P3.43 §16/§28: rebuild the voice combo from the authoritative
        list (after the unified Voice Profiles manager changed it while
        this dialog was open). The previously selected id is preserved
        when it still exists; a deleted id falls back to no assignment —
        the stale id is NEVER kept silently (no dangling references).
        """
        selected = self._voice_combo.currentData()
        self._voice_combo.blockSignals(True)
        self._voice_combo.clear()
        self._voice_combo.addItem("(no voice assigned)", None)
        for v in voices:
            label = v.name
            if v.duration > 0:
                label += " ({0:.1f}s)".format(v.duration)
            self._voice_combo.addItem(label, v.id)
        if selected is not None:
            index = self._voice_combo.findData(selected)
            self._voice_combo.setCurrentIndex(max(0, index))
        self._voice_combo.blockSignals(False)
        self._voices = list(voices)

    def _on_add_to_scene(self) -> None:
        if self._selected_character is None or self._active_scene is None:
            return
        char = self._selected_character
        if char.id not in self._active_scene.character_ids:
            self._active_scene.character_ids.append(char.id)
            QMessageBox.information(
                self, "Added to Scene",
                "Character '{0}' added to Scene '{1}'.".format(
                    char.name, self._active_scene.name))
            self.characters_changed.emit()
        else:
            QMessageBox.information(
                self, "Already Added",
                "Character '{0}' is already referenced by Scene '{1}'.".format(
                    char.name, self._active_scene.name))
