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

P3.45.6 closeout — the success icon
-----------------------------------
Every POSITIVE event-feedback dialog in the application routes through
``FeedbackDialog.information`` (project/scene saved, presets/voices
exported, batch queue saved/loaded, imports and export completions).
These dialogs used to display the stock platform ``Icon.Information``
glyph — on the affected platforms it reads as a generic default
exclamation — so they now display the GUINEO "OK" mascot artwork
instead:

  * ``assets/icons/ok2.png``              — the user-supplied artwork,
    kept VERBATIM (fully opaque, black drawing on white).
  * ``assets/icons/ok2_transparent.png``  — the background-transparency
    prepared variant actually displayed: the white background is removed
    (border flood-fill + coverage-encoded alpha) so no white rectangle
    appears on the 4 dark themes, while the drawing itself — including
    its internal white elements (sunglass lenses, snout, finger gaps) —
    is pixel-identical to the original.

The icon is resolved APP_ROOT-relative (never an absolute developer
path), scaled into the standard 48px message-box icon box with the
aspect ratio preserved, and cached. If the asset is missing or fails
to load the dialog silently falls back to the stock information icon
(the same non-fatal-asset house rule as the window logo).

Scope guard: ONLY this class changes its icon. Plain
``QMessageBox.critical`` / ``.warning`` / ``.question`` calls and the
neutral ``.information`` notices keep their stock semantic icons — the
error/warning/confirmation vocabulary of the application is unchanged.

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

import os
from typing import Optional, Union

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

__all__ = ["FeedbackDialog"]

# ---------------------------------------------------------------------------
# The success icon (P3.45.6 closeout)
# ---------------------------------------------------------------------------
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: The displayed, background-transparency prepared variant of the
#: user-supplied ok2.png artwork (see the module docstring).
_SUCCESS_ICON_FILE = "ok2_transparent.png"
#: The standard message-box icon box (logical pixels). Matches the stock
#: icon size the Fusion style renders, so the icon column geometry and
#: the text layout are unchanged.
_SUCCESS_ICON_SIZE = 48
#: Cached scaled pixmap (None = not yet resolved, False = asset missing).
_success_icon_cache = None  # type: Union[QPixmap, None, bool]


def _success_icon_pixmap() -> Optional[QPixmap]:
    """Return the scaled success-icon pixmap, or ``None`` when the asset
    is unavailable (the caller then keeps the stock information icon).

    Resolution is APP_ROOT-relative — the packaged application resolves
    the asset next to its own code, never through an absolute developer
    path. The pixmap is scaled into a ``_SUCCESS_ICON_SIZE`` box with the
    aspect ratio preserved (no distortion, no clipping) and, when a GUI
    application exists, rendered at the device pixel ratio for crisp
    high-DPI display.
    """
    global _success_icon_cache
    if _success_icon_cache is not False and _success_icon_cache is not None:
        return _success_icon_cache  # type: ignore[return-value]
    path = os.path.join(_APP_ROOT, "assets", "icons", _SUCCESS_ICON_FILE)
    pixmap = QPixmap(path)
    if pixmap.isNull():
        _success_icon_cache = False
        return None
    dpr = 1.0
    try:
        app = QGuiApplication.instance()
        if app is not None:
            dpr = float(app.devicePixelRatio()) or 1.0
    except Exception:
        dpr = 1.0
    target = _SUCCESS_ICON_SIZE
    scaled = pixmap.scaled(
        max(1, int(round(target * dpr))), max(1, int(round(target * dpr))),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation)
    if dpr != 1.0:
        scaled.setDevicePixelRatio(dpr)
    _success_icon_cache = scaled
    return scaled


def _reset_success_icon_cache() -> None:
    """Test hook: drop the cached pixmap so the next call re-resolves."""
    global _success_icon_cache
    _success_icon_cache = None


class FeedbackDialog(QMessageBox):
    """QMessageBox with a summary/details content hierarchy.

    Functionally identical to ``QMessageBox`` (buttons, keyboard,
    modality, result codes). The only differences:

    1. ``set_structured_text()`` routes the summary to the main text
       and the technical details to the informative label.
    2. The informative label is made mouse-selectable (``Ctrl+C``-free
       path copying) — only for this class, where technical detail is
       expected; plain QMessageBox call sites are untouched.
    3. The positive event-feedback icon: the GUINEO "OK" mascot
       (``assets/icons/ok2_transparent.png``) replaces the stock
       information glyph — with a graceful fallback to the stock icon
       when the asset is unavailable.
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
    def _make_information_box(cls, parent: Optional[QWidget], title: str,
                              summary: str,
                              details: Optional[str] = None
                              ) -> "FeedbackDialog":
        """Build (but do not show) the structured information dialog.

        Split from ``information()`` (P3.45.6 closeout) so the dialog
        can be inspected without executing its modal loop — the icon,
        the summary/details split and the buttons are identical for
        both entry points.
        """
        box = cls(parent)
        box.setIcon(QMessageBox.Icon.Information)
        # P3.45.6 closeout: positive event-feedback icon — the GUINEO
        # "OK" mascot replaces the stock information glyph. Missing
        # asset (or a headless environment without a GUI application)
        # degrades gracefully to the stock icon set above.
        icon = _success_icon_pixmap()
        if icon is not None:
            box.setIconPixmap(icon)
        box.setWindowTitle(title)
        box.set_structured_text(summary, details)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setDefaultButton(QMessageBox.StandardButton.Ok)
        return box

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
        box = cls._make_information_box(parent, title, summary, details)
        return box.exec()
