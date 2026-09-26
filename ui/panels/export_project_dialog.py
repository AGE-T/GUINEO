"""
SpeechStudio Export Project Dialog (P3.7).

Compact dialog with 3 options:
  - Include Generated Audio (checkbox)
  - Include Referenced Voice Assets (checkbox)
  - Destination folder picker

Project Structure is ALWAYS exported (not toggleable).

On Export:
  - Validates destination
  - Handles overwrite safety (Overwrite / Choose another / Cancel)
  - Runs ProjectExporter
  - Shows the export report (scenes/characters/audio/voice counts + warnings)
"""

from __future__ import annotations
import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QCheckBox, QFileDialog, QLineEdit, QMessageBox, QGroupBox,
    QGridLayout, QFrame,
)
from PySide6.QtGui import QFont

from engine.project_exporter import ProjectExporter, ExportResult
from ui.theme import Palette


class ExportProjectDialog(QDialog):
    """Dialog for exporting a Project to a portable directory.

    Usage:
        dialog = ExportProjectDialog(project, app_root, voice_lookup, parent)
        if dialog.exec():
            # export was performed; dialog.export_result has the result
            pass
    """

    def __init__(self, project, app_root: str, voice_lookup=None, parent=None):
        super().__init__(parent)
        self._project = project
        self._app_root = app_root
        self._voice_lookup = voice_lookup
        self.export_result: Optional[ExportResult] = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle("Export Project")
        self.setMinimumSize(500, 400)
        self.setStyleSheet("background-color: {0};".format(Palette.BG_BASE))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        project_name = getattr(self._project, 'name', 'Unknown')
        header = QLabel("Export Project: {0}".format(project_name))
        header.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: {0};".format(
                Palette.TEXT_PRIMARY))
        layout.addWidget(header)

        # Destination group
        dest_group = QGroupBox("Destination")
        dest_group.setStyleSheet(
            "QGroupBox {{ color: {0}; }}".format(Palette.TEXT_PRIMARY))
        dest_layout = QGridLayout(dest_group)
        self._dest_edit = QLineEdit()
        self._dest_edit.setStyleSheet(
            "QLineEdit {{ background-color: {0}; color: {1}; "
            "border: 1px solid {2}; border-radius: 4px; padding: 6px; }}".format(
                Palette.BG_SURFACE, Palette.TEXT_PRIMARY, Palette.BORDER))
        self._dest_edit.setPlaceholderText("Choose destination folder...")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._on_browse)
        dest_layout.addWidget(QLabel("Folder:"), 0, 0)
        dest_layout.addWidget(self._dest_edit, 0, 1)
        dest_layout.addWidget(browse_btn, 0, 2)
        # Info label
        info = QLabel(
            "A folder named \"{0}\" will be created inside the destination.".format(
                _safe_name(project_name)))
        info.setStyleSheet("color: {0}; font-size: 11px;".format(Palette.TEXT_SECONDARY))
        info.setWordWrap(True)
        dest_layout.addWidget(info, 1, 0, 1, 3)
        layout.addWidget(dest_group)

        # Options group
        opts_group = QGroupBox("Export Options")
        opts_group.setStyleSheet(
            "QGroupBox {{ color: {0}; }}".format(Palette.TEXT_PRIMARY))
        opts_layout = QVBoxLayout(opts_group)

        # Project Structure (always on, not toggleable)
        self._structure_label = QLabel(
            "✓  Project Structure (project.json, scenes, characters)")
        self._structure_label.setStyleSheet(
            "color: {0}; padding: 4px 8px;".format(Palette.TEXT_PRIMARY))
        opts_layout.addWidget(self._structure_label)

        self._audio_check = QCheckBox(
            "Include Generated Audio\n(copies WAV files into each scene's audio/ folder)")
        self._audio_check.setChecked(True)
        self._audio_check.setStyleSheet(
            "QCheckBox {{ color: {0}; padding: 4px 8px; }}".format(Palette.TEXT_PRIMARY))
        opts_layout.addWidget(self._audio_check)

        self._voice_check = QCheckBox(
            "Include Referenced Voice Assets\n(copies reference audio + transcripts for voices used by Characters)")
        self._voice_check.setChecked(True)
        self._voice_check.setStyleSheet(
            "QCheckBox {{ color: {0}; padding: 4px 8px; }}".format(Palette.TEXT_PRIMARY))
        opts_layout.addWidget(self._voice_check)

        layout.addWidget(opts_group)

        layout.addStretch()

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        export_btn = QPushButton("Export")
        export_btn.setMinimumWidth(100)
        export_btn.clicked.connect(self._on_export)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(export_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _on_browse(self) -> None:
        dest = QFileDialog.getExistingDirectory(
            self, "Choose Destination Folder")
        if dest:
            self._dest_edit.setText(dest)

    def _on_export(self) -> None:
        dest_parent = self._dest_edit.text().strip()
        if not dest_parent:
            QMessageBox.warning(self, "No Destination",
                                "Please choose a destination folder.")
            return
        if not os.path.isdir(dest_parent):
            QMessageBox.warning(self, "Invalid Destination",
                                "The destination folder does not exist.")
            return
        if self._project is None:
            QMessageBox.warning(self, "No Project",
                                "No active project to export.")
            return

        # Check for existing export folder
        project_dir_name = _safe_name(getattr(self._project, 'name', 'project'))
        target_path = os.path.join(dest_parent, project_dir_name)
        overwrite = False
        if os.path.exists(target_path):
            reply = QMessageBox.question(
                self, "Folder Exists",
                "The folder \"{0}\" already exists in the destination.\n\n"
                "Do you want to overwrite it?".format(project_dir_name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Yes:
                overwrite = True
            else:
                # "No" — continue with unique suffix (ProjectExporter default)
                overwrite = False

        # Perform the export
        # P3.25 (audit SS-M08): the exporter previously let unexpected
        # exceptions (hostile names, filesystem errors) propagate straight
        # out of the dialog handler. The export call is now guarded —
        # failures surface as a dialog error, never as a crash.
        exporter = ProjectExporter(self._app_root)
        try:
            result = exporter.export(
                project=self._project,
                dest_parent=dest_parent,
                include_audio=self._audio_check.isChecked(),
                include_voice_assets=self._voice_check.isChecked(),
                voice_lookup=self._voice_lookup,
                overwrite=overwrite,
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "Export Failed",
                "The export could not be completed:\n\n{0}".format(exc))
            return
        self.export_result = result

        if result.success:
            self._show_report(result)
            self.accept()
        else:
            QMessageBox.critical(
                self, "Export Failed",
                "The export failed:\n\n{0}".format(result.error or "Unknown error"))

    def _show_report(self, result: ExportResult) -> None:
        """Show the export report dialog."""
        msg = "Project exported successfully.\n\n"
        msg += "Location:\n{0}\n\n".format(result.export_dir)
        msg += "Summary:\n{0}".format(result.summary())
        if result.warnings:
            msg += "\n\nWarnings:\n" + "\n".join("  • " + w for w in result.warnings)
        QMessageBox.information(self, "Export Complete", msg)


def _safe_name(name: str) -> str:
    """Convert a name to a filesystem-safe directory name."""
    import re
    if not name or not name.strip():
        return "project"
    safe = re.sub(r'[\\/:*?"<>|\s]+', '_', name.strip())
    safe = safe.strip('_.')
    return safe or "project"
