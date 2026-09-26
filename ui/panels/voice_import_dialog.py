"""Voice Profile editor dialog (P3.43 single shared editor).

This is THE single authoritative VoiceProfile editor (P3.43 §2/§4). It is
used in exactly three modes everywhere in the application:

    CREATE  — start from an empty Voice Profile (reference audio optional,
              can be added later through the manager or a later edit).
    IMPORT  — start from a reference WAV (+ optional avatar / metadata).
    EDIT    — edit an EXISTING profile in place: the profile id stays
              stable, only the changed fields are written through the
              authoritative Engine facade operations.

One form, one workflow — Create/Import/Edit share the same widget set;
the mode only adapts titles, requirement hints and the accept behaviour
(no second editor architecture, P3.43 §4).

The dialog applies its changes through the Engine facade
(engine.import_voice_profile / rename_voice / update_voice_transcript /
update_voice_details / import_voice_reference / import_voice_avatar /
remove_voice_avatar / set_voice_speaker_metadata) — it never touches
VoiceManager or profile files directly, and every mutation emits
VOICE_CHANGED so all dependent UI refreshes automatically (P3.43 §25).

Legacy note: this dialog replaces the old Phase 2.2 "Voice Import
Dialog" and the QInputDialog-based flows (P3.43 §12).
"""

from __future__ import annotations
import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLineEdit,
    QPushButton, QLabel, QComboBox, QTextEdit, QFileDialog,
    QDialogButtonBox, QSizePolicy, QWidget, QMessageBox,
)

from ui.theme import Palette
from ui.widgets.avatar import AvatarLabel, _preview_candidates


# Age range options for the dropdown. Empty string = "unspecified".
AGE_OPTIONS = [
    "",
    "Child",
    "Teen",
    "20s",
    "30s",
    "40s",
    "50s",
    "60s",
    "Old",
]

# Gender options for the dropdown. Empty string = "unspecified".
GENDER_OPTIONS = ["", "Male", "Female", "Neutral"]

# P3.43 §24 — ONE filter set, aligned with the engine capability:
# the engine accepts exactly these avatar image extensions
# (VoiceManager.AVATAR_EXTENSIONS) and WAV reference audio.
AVATAR_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.gif);;All Files (*)"
WAV_FILTER = "WAV Files (*.wav);;All Files (*)"


class VoiceImportDialog(QDialog):
    """The single Voice Profile editor (Create / Import / Edit)."""

    MODE_CREATE = "create"
    MODE_IMPORT = "import"
    MODE_EDIT = "edit"

    def __init__(self, mode: str = "import", engine=None, profile=None,
                 default_name: str = "", parent=None):
        """Build the editor.

        Args:
            mode: "create" | "import" | "edit" (see class docstring).
            engine: the Engine instance used to APPLY the changes. When
                None the dialog behaves as a pure form (legacy/testing
                behaviour — values() still works, nothing is applied).
            profile: the VoiceProfile to edit (required for edit mode).
            default_name: optional initial name (legacy parameter).
            parent: parent widget.
        """
        super().__init__(parent)
        if mode not in (self.MODE_CREATE, self.MODE_IMPORT, self.MODE_EDIT):
            raise ValueError("Unknown voice editor mode: {0!r}".format(mode))
        if mode == self.MODE_EDIT and profile is None:
            raise ValueError("Edit mode requires the profile to edit.")
        self._mode = mode
        self._engine = engine
        self._profile = profile
        self._applied_profile_id: Optional[str] = None
        # True when the user cleared the avatar (edit mode: remove it).
        self._avatar_cleared = False

        self._avatar_path = ""
        self._wav_path = ""
        self._build_ui(default_name)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self, default_name: str) -> None:
        titles = {
            self.MODE_CREATE: "Create Voice Profile",
            self.MODE_IMPORT: "Import Voice Profile",
            self.MODE_EDIT: "Edit Voice Profile",
        }
        headers = {
            self.MODE_CREATE: "Create a new voice profile. Reference audio "
                              "can be added now or later.",
            self.MODE_IMPORT: "Import a new voice profile from a reference "
                              "WAV.",
            self.MODE_EDIT: "Edit the selected voice profile. Only changed "
                            "fields are written; the profile id stays "
                            "stable.",
        }
        self.setWindowTitle(titles[self._mode])
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Header
        header = QLabel(headers[self._mode])
        header.setStyleSheet("color: {0}; font-size: 12px;".format(Palette.TEXT_SECONDARY))
        header.setWordWrap(True)
        layout.addWidget(header)

        # Form
        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Name
        initial_name = default_name
        if self._mode == self.MODE_EDIT and self._profile is not None:
            initial_name = self._profile.name
        self._name_edit = QLineEdit(initial_name)
        self._name_edit.setPlaceholderText("e.g. Captain")
        form.addRow("Name:", self._name_edit)

        # WAV file row (line edit + Browse)
        wav_row = QHBoxLayout()
        wav_row.setSpacing(4)
        self._wav_edit = QLineEdit()
        if self._mode == self.MODE_EDIT and self._profile is not None \
                and self._profile.has_reference:
            self._wav_edit.setPlaceholderText(
                "Current reference kept — choose a new WAV to replace it")
        else:
            self._wav_edit.setPlaceholderText("reference.wav")
        self._wav_edit.setReadOnly(True)
        wav_row.addWidget(self._wav_edit, 1)
        wav_browse = QPushButton("Browse...")
        wav_browse.clicked.connect(self._browse_wav)
        wav_row.addWidget(wav_browse)
        wav_container = QWidget()
        wav_container.setLayout(wav_row)
        wav_label = "Replace reference audio:" if self._mode == self.MODE_EDIT \
            else "WAV file:"
        if self._mode == self.MODE_IMPORT:
            wav_label = "WAV file (required):"
        form.addRow(wav_label, wav_container)

        # Avatar row (preview + line edit + Browse + Clear)
        avatar_row = QHBoxLayout()
        avatar_row.setSpacing(8)
        self._avatar_preview = AvatarLabel(size=44)
        if self._engine is not None:
            self._avatar_preview.set_app_root(self._engine.app_root)
        avatar_row.addWidget(self._avatar_preview)

        # Right column: line edit on top, buttons below.
        avatar_right = QVBoxLayout()
        avatar_right.setSpacing(2)
        self._avatar_edit = QLineEdit()
        self._avatar_edit.setPlaceholderText("avatar.png (optional)")
        self._avatar_edit.setReadOnly(True)
        avatar_right.addWidget(self._avatar_edit)

        avatar_btn_row = QHBoxLayout()
        avatar_btn_row.setSpacing(4)
        avatar_browse = QPushButton("Browse...")
        avatar_browse.clicked.connect(self._browse_avatar)
        avatar_btn_row.addWidget(avatar_browse)
        avatar_clear = QPushButton("Clear")
        avatar_clear.clicked.connect(self._clear_avatar)
        avatar_btn_row.addWidget(avatar_clear)
        avatar_btn_row.addStretch()
        avatar_btns = QWidget()
        avatar_btns.setLayout(avatar_btn_row)
        avatar_right.addWidget(avatar_btns)

        avatar_right_w = QWidget()
        avatar_right_w.setLayout(avatar_right)
        avatar_row.addWidget(avatar_right_w, 1)
        avatar_container = QWidget()
        avatar_container.setLayout(avatar_row)
        form.addRow("Avatar:", avatar_container)

        # Gender
        self._gender_combo = QComboBox()
        self._gender_combo.addItems(GENDER_OPTIONS)
        form.addRow("Gender:", self._gender_combo)

        # Age range
        self._age_combo = QComboBox()
        self._age_combo.addItems(AGE_OPTIONS)
        form.addRow("Age:", self._age_combo)

        # Mood (free text)
        self._mood_edit = QLineEdit()
        self._mood_edit.setPlaceholderText("e.g. Calm, Energetic, Tired")
        form.addRow("Mood:", self._mood_edit)

        # Description (P3.43: the single editor owns all profile fields)
        self._description_edit = QLineEdit()
        self._description_edit.setPlaceholderText("Free-form notes (optional)")
        form.addRow("Description:", self._description_edit)

        # Tags (comma separated)
        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("comma, separated, tags")
        form.addRow("Tags:", self._tags_edit)

        layout.addLayout(form)

        # Transcript
        layout.addWidget(QLabel("Reference transcript (optional):"))
        self._transcript_edit = QTextEdit()
        self._transcript_edit.setPlaceholderText(
            "The transcript of the reference audio. Recommended for best voice cloning quality.")
        self._transcript_edit.setMinimumHeight(80)
        self._transcript_edit.setMaximumHeight(140)
        layout.addWidget(self._transcript_edit)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Prefill from the profile in edit mode.
        if self._mode == self.MODE_EDIT and self._profile is not None:
            self._prefill_from_profile(self._profile)

    def _prefill_from_profile(self, profile) -> None:
        """Fill the form from an existing profile (EDIT mode)."""
        self._gender_combo.setCurrentText(profile.gender or "")
        self._age_combo.setCurrentText(profile.age_range or "")
        self._mood_edit.setText(profile.mood or "")
        self._description_edit.setText(profile.description or "")
        if profile.tags:
            self._tags_edit.setText(", ".join(profile.tags))
        self._transcript_edit.setPlainText(profile.reference_transcript or "")
        # Show the current avatar in the preview (resolved through the
        # P3.43 app-root-relative convention).
        self._avatar_preview.set_voice(profile)

    # ------------------------------------------------------------------
    # Browse handlers
    # ------------------------------------------------------------------
    def _browse_wav(self) -> None:
        start_dir = os.path.join(os.getcwd(), "voices")
        if not os.path.isdir(start_dir):
            start_dir = os.getcwd()
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Reference WAV", start_dir, WAV_FILTER)
        if path:
            self._wav_path = os.path.normpath(path)
            self._wav_edit.setText(self._wav_path)
            # If the name is empty, prefill it from the filename.
            if not self._name_edit.text().strip():
                base = os.path.splitext(os.path.basename(self._wav_path))[0]
                self._name_edit.setText(base)

    def _browse_avatar(self) -> None:
        start_dir = os.path.join(os.getcwd(), "voices")
        if not os.path.isdir(start_dir):
            start_dir = os.getcwd()
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Avatar Image", start_dir, AVATAR_FILTER)
        if path:
            self._avatar_path = os.path.normpath(path)
            self._avatar_cleared = False
            self._avatar_edit.setText(self._avatar_path)
            # Update the preview (an absolute path always resolves).
            from engine.models import VoiceProfile
            tmp = VoiceProfile(id="_tmp", name=self._name_edit.text().strip() or "?",
                               preview_image=self._avatar_path)
            self._avatar_preview.set_voice(tmp)

    def _clear_avatar(self) -> None:
        """Clear the avatar selection.

        EDIT mode: with an existing profile avatar this means REMOVE the
        avatar from the profile on save (P3.43 §8). Otherwise it just
        resets the selection.
        """
        self._avatar_path = ""
        self._avatar_edit.setText("")
        if self._mode == self.MODE_EDIT and self._profile is not None \
                and self._profile.preview_image:
            self._avatar_cleared = True
        self._avatar_preview.set_voice(None)

    # ------------------------------------------------------------------
    # Accept / validate / apply
    # ------------------------------------------------------------------
    def _on_accept(self) -> None:
        """Validate inputs, then apply the change through the facade."""
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing name",
                                "Please enter a name for the voice profile.")
            return
        if self._mode == self.MODE_IMPORT:
            # Import REQUIRES a reference WAV (the whole point of the mode).
            if not self._wav_path or not os.path.isfile(self._wav_path):
                QMessageBox.warning(self, "Missing WAV file",
                                    "Please select a reference WAV file.")
                return
        if self._wav_path and not os.path.isfile(self._wav_path):
            QMessageBox.warning(self, "Missing WAV file",
                                "The selected WAV file no longer exists:\n{0}"
                                .format(self._wav_path))
            return
        if self._engine is not None:
            try:
                self._apply()
            except Exception as exc:
                # The engine keeps the profile untouched on failure
                # (transactional import / validated replacements) — show
                # the error and keep the dialog open for correction.
                QMessageBox.critical(
                    self, "Voice Profile",
                    "The operation failed:\n{0}\n\nNo changes were saved."
                    .format(exc))
                return
        self.accept()

    def _apply(self):
        """Apply the form through the authoritative Engine operations."""
        values = self.values()
        if self._mode in (self.MODE_CREATE, self.MODE_IMPORT):
            voice = self._engine.import_voice_profile(
                values["name"],
                wav_path=values["wav_path"],
                transcript=values["transcript"],
                avatar_path=values["avatar_path"],
                gender=values["gender"],
                age_range=values["age_range"],
                mood=values["mood"],
                description=values["description"],
                tags=values["tags"] or None,
            )
            self._applied_profile_id = voice.id
            return

        # ---- EDIT mode: mutate the EXISTING profile (stable id) ----
        profile = self._profile
        pid = profile.id
        if values["name"] != profile.name:
            self._engine.rename_voice(pid, values["name"])
        if values["transcript"] != (profile.reference_transcript or ""):
            self._engine.update_voice_transcript(pid, values["transcript"])
        if values["description"] != (profile.description or "") or \
                values["tags"] != list(profile.tags):
            self._engine.update_voice_details(
                pid, values["description"], values["tags"])
        if values["gender"] != (profile.gender or "") or \
                values["age_range"] != (profile.age_range or "") or \
                values["mood"] != (profile.mood or ""):
            # All three fields are applied EXPLICITLY: an empty string is
            # the user clearing the field (P3.43 §7).
            self._engine.set_voice_speaker_metadata(
                pid, values["gender"], values["age_range"], values["mood"])
        if values["wav_path"]:
            # Replace the reference audio (validated + atomic inside the
            # engine; the transcript is preserved because "" is passed).
            self._engine.import_voice_reference(pid, values["wav_path"], "")
        if self._avatar_cleared:
            self._engine.remove_voice_avatar(pid)
        elif values["avatar_path"]:
            self._engine.import_voice_avatar(pid, values["avatar_path"])
        self._applied_profile_id = pid

    def applied_profile_id(self) -> Optional[str]:
        """The id of the created/edited profile (after accept)."""
        return self._applied_profile_id

    # ------------------------------------------------------------------
    # Result accessor (kept for callers that read the form values)
    # ------------------------------------------------------------------
    def values(self) -> dict:
        """Return the dialog's values as a dict (call after accept())."""
        tags_text = self._tags_edit.text().strip()
        tags = [t.strip() for t in tags_text.split(",") if t.strip()]
        return {
            "name": self._name_edit.text().strip(),
            "wav_path": self._wav_path,
            "avatar_path": self._avatar_path,
            "avatar_cleared": self._avatar_cleared,
            "gender": self._gender_combo.currentText(),
            "age_range": self._age_combo.currentText(),
            "mood": self._mood_edit.text().strip(),
            "transcript": self._transcript_edit.toPlainText().strip(),
            "description": self._description_edit.text().strip(),
            "tags": tags,
        }
