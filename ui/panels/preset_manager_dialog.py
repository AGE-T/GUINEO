"""
SpeechStudio Preset Manager Dialog.

Provides the preset management UI that is reachable from the
ProjectSceneSidebar's "Presets" library nav item. The dialog lists all
saved presets, showing each preset's name and the human-readable voice
name (not the internal voice_id), and emits the same signals the legacy
sidebar tab used to emit:

    apply_preset_requested(name)
    save_preset_requested()
    delete_preset_requested(name)
    rename_preset_requested(name)
    export_preset_requested(name)
    import_preset_requested()

MainWindow already wires these signals to its preset handlers, so the
dialog is a thin UI shell that delegates all business logic.

Preset storage path: <app_root>/presets/   (matches PresetManager)
Preset file format:   YAML preferred, JSON fallback.
"""

from __future__ import annotations
from typing import Optional, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QFileDialog, QInputDialog, QMessageBox,
    QFrame, QSizePolicy,
)

from engine.models import VoiceProfile
from engine.preset_manager import Preset, PresetManager


class PresetManagerDialog(QDialog):
    """Lists presets and exposes apply / save / delete / rename / export / import.

    The dialog takes a PresetManager and an optional voice_lookup dict
    ({voice_id: voice_name}) so each preset row can display the friendly
    voice name rather than the opaque internal voice_id.
    """

    apply_preset_requested = Signal(str)     # preset name
    save_preset_requested = Signal()          # save current settings as a preset
    delete_preset_requested = Signal(str)     # preset name
    rename_preset_requested = Signal(str)     # preset name
    export_preset_requested = Signal(str)     # preset name
    import_preset_requested = Signal()         # import a preset from file

    def __init__(
        self,
        preset_manager: PresetManager,
        voice_lookup: Optional[dict] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._preset_manager = preset_manager
        # voice_id -> voice display name. Refreshed by the caller via
        # set_voice_lookup() whenever the voice list changes.
        self._voice_lookup = dict(voice_lookup or {})
        self._selected_name: Optional[str] = None
        self.setWindowTitle("Preset Manager")
        self.resize(640, 480)
        self.setMinimumSize(520, 360)
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_voice_lookup(self, voice_lookup) -> None:
        """Refresh the voice_id -> voice_name mapping and re-render the list."""
        self._voice_lookup = dict(voice_lookup or {})
        self._refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header — show the actual on-disk preset directory so the user
        # can find their preset files without guessing.
        header_label = QLabel("Saved Presets")
        header_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(header_label)

        path_label = QLabel(
            "Storage: {0}".format(self._preset_manager.directory),
        )
        path_label.setStyleSheet("color: #9aa0b8; font-size: 11px;")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        # Preset list
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._list, 1)

        # Hint
        hint = QLabel("Double-click a preset to apply it.")
        hint.setStyleSheet("color: #9aa0b8; font-size: 11px;")
        layout.addWidget(hint)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        save_btn = QPushButton("+ Save Current")
        save_btn.setToolTip(
            "Save the current voice/emotion/style/parameters as a new preset",
        )
        save_btn.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(save_btn)

        import_btn = QPushButton("Import...")
        import_btn.setToolTip("Import a preset file from disk")
        import_btn.clicked.connect(self._on_import_clicked)
        btn_row.addWidget(import_btn)

        btn_row.addStretch()

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setToolTip("Apply the selected preset to the current settings")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply_clicked)
        btn_row.addWidget(self._apply_btn)

        self._rename_btn = QPushButton("Rename...")
        self._rename_btn.setEnabled(False)
        self._rename_btn.clicked.connect(self._on_rename_clicked)
        btn_row.addWidget(self._rename_btn)

        self._export_btn = QPushButton("Export...")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._on_export_clicked)
        btn_row.addWidget(self._export_btn)

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        btn_row.addWidget(self._delete_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # List rendering
    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        """Reload presets from PresetManager and rebuild the list."""
        try:
            presets = self._preset_manager.list()
        except Exception:
            presets = []
        self._list.clear()
        for p in presets:
            # Resolve voice_id -> display name.
            if p.voice_id:
                if p.voice_id in self._voice_lookup:
                    voice_display = self._voice_lookup[p.voice_id]
                else:
                    voice_display = "(voice missing)"
            else:
                voice_display = "(no voice)"

            label = "{0}\n   Voice: {1}".format(p.name, voice_display)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, p.name)
            # Tooltip carries the internal voice_id + all settings for
            # debugging / power users.
            tooltip_parts = [p.name]
            if p.voice_id:
                tooltip_parts.append("Voice ID: {0}".format(p.voice_id))
            if p.emotion:
                tooltip_parts.append("Emotion: {0}".format(p.emotion))
            if p.style:
                tooltip_parts.append("Style: {0}".format(p.style))
            if p.speed:
                tooltip_parts.append("Speed: {0}".format(p.speed))
            if p.pitch:
                tooltip_parts.append("Pitch: {0}".format(p.pitch))
            if p.delivery:
                tooltip_parts.append("Delivery: {0}".format(p.delivery))
            if p.parameters:
                tooltip_parts.append(
                    "Temp: {0:.2f}  top_p: {1:.2f}  top_k: {2}  "
                    "max_tokens: {3}".format(
                        p.parameters.temperature,
                        p.parameters.top_p,
                        p.parameters.top_k,
                        p.parameters.max_new_tokens,
                    ),
                )
                if p.parameters.seed is not None:
                    tooltip_parts.append(
                        "Seed: {0}".format(p.parameters.seed),
                    )
            item.setToolTip("\n".join(tooltip_parts))
            self._list.addItem(item)

    # ------------------------------------------------------------------
    # Selection handling
    # ------------------------------------------------------------------
    def _on_selection_changed(self) -> None:
        items = self._list.selectedItems()
        if not items:
            self._selected_name = None
            self._apply_btn.setEnabled(False)
            self._rename_btn.setEnabled(False)
            self._export_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)
            return
        self._selected_name = items[0].data(Qt.ItemDataRole.UserRole)
        self._apply_btn.setEnabled(True)
        self._rename_btn.setEnabled(True)
        self._export_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)

    def _on_item_double_clicked(self, item) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self.apply_preset_requested.emit(name)
            self.accept()

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------
    def _on_save_clicked(self) -> None:
        # Defer to MainWindow's save handler (which prompts for the name).
        self.save_preset_requested.emit()

    def _on_import_clicked(self) -> None:
        self.import_preset_requested.emit()

    def _on_apply_clicked(self) -> None:
        if self._selected_name:
            self.apply_preset_requested.emit(self._selected_name)
            self.accept()

    def _on_rename_clicked(self) -> None:
        if not self._selected_name:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename Preset", "New name:", text=self._selected_name,
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if new_name == self._selected_name:
            return
        try:
            self._preset_manager.validate_name(new_name)
        except ValueError as exc:
            QMessageBox.warning(self, "Rename Preset", str(exc))
            return
        if self._preset_manager.get(new_name) is not None:
            QMessageBox.warning(
                self, "Rename Preset",
                "A preset named '{0}' already exists.".format(new_name),
            )
            return
        self.rename_preset_requested.emit(self._selected_name)
        # The MainWindow handler does the actual rename; refresh the list.
        self._refresh()

    def _on_export_clicked(self) -> None:
        if self._selected_name:
            self.export_preset_requested.emit(self._selected_name)

    def _on_delete_clicked(self) -> None:
        if not self._selected_name:
            return
        reply = QMessageBox.question(
            self, "Delete Preset",
            "Delete preset '{0}'?".format(self._selected_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.delete_preset_requested.emit(self._selected_name)
        self._refresh()

    # ------------------------------------------------------------------
    # Context menu (right-click on a preset row)
    # ------------------------------------------------------------------
    def _on_context_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        item = self._list.itemAt(pos)
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if not name:
            return
        menu = QMenu(self)
        apply_action = menu.addAction("Apply")
        rename_action = menu.addAction("Rename...")
        export_action = menu.addAction("Export...")
        menu.addSeparator()
        delete_action = menu.addAction("Delete")
        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == apply_action:
            self.apply_preset_requested.emit(name)
            self.accept()
        elif chosen == rename_action:
            self._selected_name = name
            self._on_rename_clicked()
        elif chosen == export_action:
            self._selected_name = name
            self._on_export_clicked()
        elif chosen == delete_action:
            self._selected_name = name
            self._on_delete_clicked()
