"""
SpeechStudio Left Sidebar.

UI Specification, section 6:
    Contains: Voice Library, History, Presets, Projects, Output Browser
    Collapsible. Resizable.
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QLabel, QListWidget,
    QListWidgetItem, QHeaderView, QPushButton, QHBoxLayout,
)

from ui.theme import Palette
from engine.models import VoiceProfile


class VoiceLibraryTab(QWidget):
    """Lists available voice profiles."""

    voice_selected = Signal(str)  # voice_id
    create_voice_requested = Signal()
    delete_voice_requested = Signal(str)    # voice_id
    export_voice_requested = Signal(str)    # voice_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Button row
        btn_row = QHBoxLayout()
        create_btn = QPushButton("+ New")
        create_btn.setToolTip("Create a new voice profile")
        create_btn.clicked.connect(self.create_voice_requested.emit)
        btn_row.addWidget(create_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Voice list with context menu
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._list)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        voice_id = item.data(Qt.ItemDataRole.UserRole)
        if voice_id:
            self.voice_selected.emit(voice_id)

    def _on_context_menu(self, pos) -> None:
        """Show a context menu for voice list items."""
        from PySide6.QtWidgets import QMenu
        item = self._list.itemAt(pos)
        if item is None:
            return
        voice_id = item.data(Qt.ItemDataRole.UserRole)
        if not voice_id:
            return

        menu = QMenu(self)
        select_action = menu.addAction("Select")
        export_action = menu.addAction("Export...")
        menu.addSeparator()
        delete_action = menu.addAction("Delete")

        from PySide6.QtGui import QAction
        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen == select_action:
            self.voice_selected.emit(voice_id)
        elif chosen == export_action:
            self.export_voice_requested.emit(voice_id)
        elif chosen == delete_action:
            self.delete_voice_requested.emit(voice_id)

    def populate(self, voices: list) -> None:
        self._list.clear()
        for voice in voices:
            label = "{0} ({1:.1f}s)".format(voice.name, voice.duration) if voice.duration > 0 else voice.name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, voice.id)
            if voice.has_reference:
                item.setToolTip("{0}\n{1} Hz, {2} ch\n{3}".format(
                    voice.name, voice.sample_rate, voice.channels,
                    voice.reference_transcript[:100]))
            else:
                item.setToolTip(voice.name + "\nNo reference audio")
            self._list.addItem(item)


class HistoryTab(QWidget):
    """Lists recent generation history entries, grouped by project."""

    entry_selected = Signal(str)  # entry_id
    entry_reuse_requested = Signal(str)    # entry_id
    entry_delete_requested = Signal(str)   # entry_id
    entry_export_requested = Signal(str)   # entry_id
    project_changed = Signal(str)          # project name

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_project = "Default"
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Project selector + search row
        top_row = QHBoxLayout()
        top_row.setSpacing(4)

        from PySide6.QtWidgets import QComboBox
        self._project_combo = QComboBox()
        self._project_combo.setEditable(True)
        self._project_combo.setToolTip("Project name — groups history entries together")
        self._project_combo.currentTextChanged.connect(self._on_project_changed)
        top_row.addWidget(QLabel("Project:"))
        top_row.addWidget(self._project_combo, 1)
        layout.addLayout(top_row)

        # Search box
        from PySide6.QtWidgets import QLineEdit
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search history...")
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        # Tree widget for grouped display
        from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
        self._tree = QTreeWidget()
        self._tree.setAlternatingRowColors(True)
        self._tree.setHeaderHidden(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._tree)

        self._all_entries = []

    def _on_project_changed(self, text: str) -> None:
        self._current_project = text.strip() if text.strip() else "Default"
        self.project_changed.emit(self._current_project)
        self._refresh_tree()

    def _on_item_clicked(self, item) -> None:
        entry_id = item.data(0, Qt.ItemDataRole.UserRole)
        if entry_id:
            self.entry_selected.emit(entry_id)

    def _on_item_double_clicked(self, item) -> None:
        entry_id = item.data(0, Qt.ItemDataRole.UserRole)
        if entry_id:
            self.entry_reuse_requested.emit(entry_id)

    def _on_search(self, text: str) -> None:
        self._refresh_tree(text)

    def _refresh_tree(self, search_text: str = "") -> None:
        """Rebuild the tree grouped by project."""
        from PySide6.QtWidgets import QTreeWidgetItem
        self._tree.clear()
        text_lower = search_text.lower().strip()

        # Group entries by project
        projects: dict = {}  # project_name -> list of entries
        for entry in self._all_entries:
            proj = entry.project
            projects.setdefault(proj, []).append(entry)

        # Update project combo
        self._project_combo.blockSignals(True)
        current = self._current_project or self._project_combo.currentText()
        self._project_combo.clear()
        self._project_combo.addItem("Default")
        for proj in sorted(projects.keys()):
            if proj != "Default" and self._project_combo.findText(proj) < 0:
                self._project_combo.addItem(proj)
        # Always preserve the user's current project name — even if no
        # history entry exists for it yet (so a freshly-typed project name
        # does not vanish on refresh).
        if current and current != "Default" and \
                self._project_combo.findText(current) < 0:
            self._project_combo.addItem(current)
        # Restore selection
        idx = self._project_combo.findText(current)
        if idx >= 0:
            self._project_combo.setCurrentIndex(idx)
        else:
            self._project_combo.setCurrentText(current)
        self._project_combo.blockSignals(False)

        # Build tree: project nodes with entry children
        for proj_name in sorted(projects.keys()):
            entries = projects[proj_name]
            total_duration = sum(e.output_duration for e in entries)
            proj_label = "📁 {0}  ({1} items, {2:.0f}s)".format(
                proj_name, len(entries), total_duration)
            proj_item = QTreeWidgetItem([proj_label])
            proj_item.setFont(0, QFont("Segoe UI", 10))
            proj_item.setForeground(0, QColor(Palette.ACCENT))
            self._tree.addTopLevelItem(proj_item)

            for entry in entries:
                label = "{0}  {1}  ({2:.1f}s)".format(
                    entry.timestamp, entry.voice_name, entry.output_duration)
                if text_lower and text_lower not in label.lower():
                    continue
                child = QTreeWidgetItem([label])
                child.setData(0, Qt.ItemDataRole.UserRole, entry.id)
                child.setToolTip(0, "Output: {0}\nDuration: {1:.1f}s\nRTF: {2:.2f}\nProject: {3}".format(
                    entry.output_path, entry.output_duration,
                    entry.realtime_factor, entry.project))
                proj_item.addChild(child)

            proj_item.setExpanded(proj_name == self._current_project or len(projects) == 1)

    def _on_context_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        item = self._tree.itemAt(pos)
        if item is None:
            return
        entry_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not entry_id:
            return

        menu = QMenu(self)
        reuse_action = menu.addAction("Reuse Settings")
        play_action = menu.addAction("Play Audio")
        menu.addSeparator()
        export_action = menu.addAction("Export...")
        delete_action = menu.addAction("Delete")

        chosen = menu.exec(self._tree.mapToGlobal(pos))
        if chosen == reuse_action:
            self.entry_reuse_requested.emit(entry_id)
        elif chosen == play_action:
            self.entry_selected.emit(entry_id)
        elif chosen == export_action:
            self.entry_export_requested.emit(entry_id)
        elif chosen == delete_action:
            self.entry_delete_requested.emit(entry_id)

    def populate(self, entries: list) -> None:
        self._all_entries = entries
        self._refresh_tree()

    def set_project(self, project: str) -> None:
        """Set the current project name."""
        self._current_project = project
        idx = self._project_combo.findText(project)
        if idx >= 0:
            self._project_combo.setCurrentIndex(idx)
        else:
            self._project_combo.setCurrentText(project)


class PresetsTab(QWidget):
    """Lists saved presets with double-click to apply and a context menu
    for save-as / rename / delete / export.

    This tab is the UI front for the engine's PresetManager.  All actual
    persistence is delegated to the MainWindow (which owns the
    PresetManager) via signals.
    """

    apply_preset_requested = Signal(str)        # preset name
    save_preset_requested = Signal()            # save current settings as a preset
    delete_preset_requested = Signal(str)       # preset name
    rename_preset_requested = Signal(str)       # preset name
    export_preset_requested = Signal(str)       # preset name

    def __init__(self, parent=None):
        super().__init__(parent)
        # voice_id -> voice display name map, refreshed by MainWindow via
        # set_voice_lookup() before populate() is called. Falls back to
        # the raw voice_id if no lookup is available.
        self._voice_lookup = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Button row
        btn_row = QHBoxLayout()
        save_btn = QPushButton("+ Save Current")
        save_btn.setToolTip("Save the current voice/emotion/style/parameters "
                            "as a new preset")
        save_btn.clicked.connect(self.save_preset_requested.emit)
        btn_row.addWidget(save_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._list)

        # Hint label
        hint = QLabel("Double-click a preset to apply it.")
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def set_voice_lookup(self, voice_lookup) -> None:
        """Provide a {voice_id: voice_name} mapping for friendly display.

        Called by MainWindow before populate() so each preset row shows the
        human-readable voice name instead of the internal voice_id. If a
        preset references a voice_id that is no longer in the lookup, the
        row is annotated with "(voice missing)".
        """
        self._voice_lookup = dict(voice_lookup or {})

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self.apply_preset_requested.emit(name)

    def _on_context_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        item = self._list.itemAt(pos)
        menu = QMenu(self)
        apply_action = menu.addAction("Apply")
        rename_action = menu.addAction("Rename...")
        export_action = menu.addAction("Export...")
        menu.addSeparator()
        delete_action = menu.addAction("Delete")

        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen is None or item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if not name:
            return
        if chosen == apply_action:
            self.apply_preset_requested.emit(name)
        elif chosen == rename_action:
            self.rename_preset_requested.emit(name)
        elif chosen == export_action:
            self.export_preset_requested.emit(name)
        elif chosen == delete_action:
            self.delete_preset_requested.emit(name)

    def populate(self, presets: list) -> None:
        """Rebuild the list from Preset objects (or name strings).

        Each row shows:
            Preset Name
            Voice Name       (or "(voice missing)" if the voice_id no
                              longer resolves to a known VoiceProfile, or
                              "(no voice)" if preset.voice_id is None)

        The internal voice_id is NEVER shown as the primary label — it is
        an opaque engine identifier. The voice NAME is the user-facing
        representation. The voice_id is preserved in the UserRole data of
        the tooltip for debugging.
        """
        self._list.clear()
        for p in presets:
            name = p.name if hasattr(p, "name") else str(p)

            # Resolve voice_id -> display name via the lookup map.
            voice_display = "(no voice)"
            voice_id_for_tooltip = ""
            if hasattr(p, "voice_id") and p.voice_id:
                voice_id_for_tooltip = p.voice_id
                if self._voice_lookup:
                    if p.voice_id in self._voice_lookup:
                        voice_display = self._voice_lookup[p.voice_id]
                    else:
                        voice_display = "(voice missing)"
                else:
                    # No lookup map provided — fall back to the voice_id
                    # but flag it as opaque so the user understands it's
                    # not a display name.
                    voice_display = "(voice: {0})".format(p.voice_id)

            label = "{0}\n   Voice: {1}".format(name, voice_display)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            tooltip_parts = [name]
            if voice_id_for_tooltip:
                tooltip_parts.append(
                    "Voice ID: {0}".format(voice_id_for_tooltip),
                )
            if hasattr(p, "emotion") and p.emotion:
                tooltip_parts.append("Emotion: {0}".format(p.emotion))
            if hasattr(p, "style") and p.style:
                tooltip_parts.append("Style: {0}".format(p.style))
            if hasattr(p, "speed") and p.speed:
                tooltip_parts.append("Speed: {0}".format(p.speed))
            if hasattr(p, "pitch") and p.pitch:
                tooltip_parts.append("Pitch: {0}".format(p.pitch))
            if hasattr(p, "delivery") and p.delivery:
                tooltip_parts.append("Delivery: {0}".format(p.delivery))
            item.setToolTip("\n".join(tooltip_parts))
            self._list.addItem(item)


class OutputsTab(QWidget):
    """Lists generated output files."""

    file_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._list)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.file_selected.emit(path)

    def populate(self, files: list) -> None:
        self._list.clear()
        for fname, fpath in files:
            item = QListWidgetItem(fname)
            item.setData(Qt.ItemDataRole.UserRole, fpath)
            self._list.addItem(item)


class Sidebar(QWidget):
    """Left sidebar with tabs for Voice Library, History, Presets, Outputs.

    Collapsible and resizable (managed by the QSplitter in MainWindow).
    """

    voice_selected = Signal(str)
    create_voice_requested = Signal()
    delete_voice_requested = Signal(str)
    export_voice_requested = Signal(str)
    history_entry_selected = Signal(str)
    history_reuse_requested = Signal(str)
    history_delete_requested = Signal(str)
    history_export_requested = Signal(str)
    history_project_changed = Signal(str)
    output_file_selected = Signal(str)
    # Preset signals (forwarded from PresetsTab)
    apply_preset_requested = Signal(str)
    save_preset_requested = Signal()
    delete_preset_requested = Signal(str)
    rename_preset_requested = Signal(str)
    export_preset_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # voice_id -> voice display name map; refreshed by MainWindow via
        # set_voice_lookup() whenever the voice list changes.
        self._voice_lookup = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)

        self._voice_tab = VoiceLibraryTab()
        self._voice_tab.voice_selected.connect(self.voice_selected.emit)
        self._voice_tab.create_voice_requested.connect(self.create_voice_requested.emit)
        self._voice_tab.delete_voice_requested.connect(self.delete_voice_requested.emit)
        self._voice_tab.export_voice_requested.connect(self.export_voice_requested.emit)
        self._tabs.addTab(self._voice_tab, "Voices")

        self._history_tab = HistoryTab()
        self._history_tab.entry_selected.connect(self.history_entry_selected.emit)
        self._history_tab.entry_reuse_requested.connect(self.history_reuse_requested.emit)
        self._history_tab.entry_delete_requested.connect(self.history_delete_requested.emit)
        self._history_tab.entry_export_requested.connect(self.history_export_requested.emit)
        self._history_tab.project_changed.connect(self.history_project_changed.emit)
        self._tabs.addTab(self._history_tab, "History")

        self._presets_tab = PresetsTab()
        self._presets_tab.apply_preset_requested.connect(self.apply_preset_requested.emit)
        self._presets_tab.save_preset_requested.connect(self.save_preset_requested.emit)
        self._presets_tab.delete_preset_requested.connect(self.delete_preset_requested.emit)
        self._presets_tab.rename_preset_requested.connect(self.rename_preset_requested.emit)
        self._presets_tab.export_preset_requested.connect(self.export_preset_requested.emit)
        self._tabs.addTab(self._presets_tab, "Presets")

        self._outputs_tab = OutputsTab()
        self._outputs_tab.file_selected.connect(self.output_file_selected.emit)
        self._tabs.addTab(self._outputs_tab, "Outputs")

        layout.addWidget(self._tabs)

    # ------------------------------------------------------------------
    # Update methods
    # ------------------------------------------------------------------
    def update_voices(self, voices: list) -> None:
        self._voice_tab.populate(voices)

    def update_history(self, entries: list) -> None:
        self._history_tab.populate(entries)

    def set_project(self, project: str) -> None:
        """Set the current project name on the History tab.

        Forwards to HistoryTab.set_project so MainWindow can restore the
        persisted project name on startup.
        """
        self._history_tab.set_project(project)

    def update_outputs(self, files: list) -> None:
        self._outputs_tab.populate(files)

    def update_presets(self, presets: list) -> None:
        """Refresh the Presets tab with a list of Preset objects.

        Also pushes the current voice lookup (voice_id -> voice_name) into
        the Presets tab so that each preset row shows the human-readable
        voice name rather than the internal voice_id.
        """
        self._presets_tab.set_voice_lookup(self._voice_lookup)
        self._presets_tab.populate(presets)

    def set_voice_lookup(self, voice_lookup) -> None:
        """Provide a {voice_id: voice_name} mapping for preset display.

        Called by MainWindow whenever voices are refreshed. The mapping
        is used by the Presets tab to render voice names instead of
        opaque voice_ids.
        """
        self._voice_lookup = dict(voice_lookup or {})
