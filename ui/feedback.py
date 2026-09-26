"""
GUINEO — Structured Feedback Dialog (P3.44.3)
=============================================

Central, restrained feedback dialog for confirmation-style messages
that carry both a short summary and technical detail (counts, file
locations, hints).

Why this exists
---------------
Before P3.44.3 these confirmations were plain ``QMessageBox.information``
calls with hand-formatted ``"\\n\\n"`` blobs: summary, technical counts and
paths all rendered identically, and file locations were not selectable.

``FeedbackDialog`` keeps the exact interaction model of QMessageBox —
same standard buttons, same modal behaviour, same keyboard handling
(Enter = default button, Escape = reject), same return values — but
splits the content into two semantic levels:

  * ``summary``  → the dialog's main text. Rendered slightly larger and
    semibold by the message-box QSS in ``ui/theme.py`` (title level).
  * ``details``  → the informative text. Rendered in the theme's
    secondary colour, word-wrapped, and mouse-selectable so paths can
    be copied.

Visual styling is NOT done here: the look comes from the central
``QMessageBox`` QSS section in ``ui/theme.py`` so that every message
box in the application (this class and plain ``QMessageBox`` calls
alike) stays visually consistent across all themes.

Usage
-----
    from ui.feedback import FeedbackDialog

    FeedbackDialog.information(
        parent, "GUINEO",
        "Project saved: {0}".format(name),
        "Scenes: {1}\\nCharacters: {2}\\n\\nLocation: projects/{3}/".format(...))

No state, no engine access, no behaviour beyond QMessageBox.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

__all__ = ["FeedbackDialog"]


class FeedbackDialog(QMessageBox):
    """QMessageBox with a summary/details content hierarchy.

    Functionally identical to ``QMessageBox`` (buttons, keyboard,
    modality, result codes). The only differences:

    1. ``set_structured_text()`` routes the summary to the main text
       and the technical details to the informative label.
    2. The informative label is made mouse-selectable (``Ctrl+C``-free
       path copying) — only for this class, where technical detail is
       expected; plain QMessageBox call sites are untouched.
    """

    # Qt's object name for the informative-text label inside
    # QMessageBox. Stable since Qt 4 and verified at runtime in the
    # P3.44.3 probes; if it ever disappears, the selectable-details
    # feature silently degrades to normal (still correct) behaviour.
    _INFORMATIVE_LABEL_NAME = "qt_msgbox_informativelabel"

    def set_structured_text(self, summary: str,
                            details: Optional[str] = None) -> None:
        """Set the summary (title level) and optional details text.

        Keeping the two levels separate lets the theme's message-box
        QSS build the visual hierarchy instead of hand-formatting
        ``\\n\\n`` blobs with the same style for everything.
        """
        self.setText(summary)
        if details:
            self.setInformativeText(details)
        self._make_details_selectable()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _make_details_selectable(self) -> None:
        """Allow selecting/copying the details text with the mouse.

        The informative label is created by ``setInformativeText``
        (verified: it exists immediately after the call, before the
        dialog is shown), so this runs synchronously right after the
        text is set. Selection never blocks the buttons or keyboard.
        """
        if not self.informativeText():
            return
        label = self._find_informative_label()
        if label is not None:
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)

    def _find_informative_label(self) -> Optional[QLabel]:
        for label in self.findChildren(QLabel):
            if label.objectName() == self._INFORMATIVE_LABEL_NAME:
                return label
        return None

    # ------------------------------------------------------------------
    # QMessageBox-compatible entry point (drop-in for information())
    # ------------------------------------------------------------------
    @classmethod
    def information(cls, parent: Optional[QWidget], title: str,
                    summary: str, details: Optional[str] = None
                    ) -> QMessageBox.StandardButton:
        """Show a structured information dialog; returns when closed.

        Mirrors ``QMessageBox.information(parent, title, text)`` —
        same arguments, same modal behaviour, same return value —
        with an optional fourth ``details`` argument for the technical
        content that used to live inside the same text blob.
        """
        box = cls(parent)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(title)
        box.set_structured_text(summary, details)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setDefaultButton(QMessageBox.StandardButton.Ok)
        return box.exec()
