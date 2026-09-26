"""Rounded avatar widget used in the sidebar and control panel.

Renders a circular avatar image from a voice profile's ``preview_image``
path. If no image is available, falls back to a colored circle with the
voice's first initial — matching the HTML prototype's "rounded-full" avatar
style.

Used by:
    - ui/panels/sidebar.py    — voice list rows
    - ui/panels/control_panel.py — voice card at the top of the panel
"""

from __future__ import annotations
import os
from typing import Optional

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (
    QPixmap, QPainter, QPainterPath, QColor, QFont, QPen, QBrush,
)
from PySide6.QtWidgets import QLabel

from ui.theme import Palette


# Deterministic colors for fallback initial-avatars. Indexed by the first
# character's lowercase ordinal. All colors are from the HTML prototype's
# speaker palette (blue, purple, orange, green, red, pink, cyan, dark orange).
AVATAR_COLORS = [
    "#3B82F6",  # blue
    "#8B5CF6",  # purple
    "#F59E0B",  # orange
    "#10B981",  # green
    "#EF4444",  # red
    "#EC4899",  # pink
    "#06B6D4",  # cyan
    "#F97316",  # dark orange
]


def _color_for_name(name: str) -> str:
    """Pick a deterministic avatar color from a name."""
    if not name:
        return AVATAR_COLORS[0]
    return AVATAR_COLORS[ord(name[0].lower()) % len(AVATAR_COLORS)]


def make_rounded_pixmap(image_path: str, size: int = 40,
                        fallback_name: str = "") -> Optional[QPixmap]:
    """Load an image and return a circular, size×size QPixmap.

    Returns None if the image cannot be loaded. In that case the caller
    should use :func:`make_initial_pixmap` as a fallback.

    P3.43: the aspect-ratio mode uses the correct Qt enum name
    (KeepAspectRatioByExpanding — the previous "...ByExpansion" spelling
    does not exist in PySide6 and raised AttributeError whenever an
    avatar image was actually resolved).
    """
    if not image_path or not os.path.isfile(image_path):
        return None
    src = QPixmap(image_path)
    if src.isNull():
        return None

    # Scale to fill the circle (KeepAspectRatioByExpanding crops evenly).
    scaled = src.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )

    # Composite onto a transparent circular mask.
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, size, size))
    painter.setClipPath(path)
    # Center the scaled source in case aspect expansion produced a slightly
    # larger pixmap than requested.
    x = (scaled.width() - size) // 2
    y = (scaled.height() - size) // 2
    painter.drawPixmap(0, 0, scaled, x, y, size, size)
    painter.end()
    return out


def make_initial_pixmap(initial: str, size: int = 40,
                        color: str = "") -> QPixmap:
    """Render a colored circle with a single character (initial) inside.

    Used as a fallback when no avatar image is available.
    """
    if not color:
        color = _color_for_name(initial)
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Filled circle background.
    painter.setBrush(QBrush(QColor(color)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(0, 0, size, size))

    # Initial character — white, bold, centered.
    painter.setPen(QColor("#FFFFFF"))
    font = QFont("Segoe UI", max(10, size // 3), QFont.Weight.Bold)
    painter.setFont(font)
    char = (initial.strip()[:1] or "?").upper()
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, char)
    painter.end()
    return out


class AvatarLabel(QLabel):
    """A QLabel that displays a circular avatar.

    Call :meth:`set_voice` with a VoiceProfile (or None) to update the
    avatar. If the profile has a ``preview_image`` that exists on disk,
    the image is rendered as a circle. Otherwise a colored circle with
    the voice name's first initial is shown.
    """

    def __init__(self, size: int = 40, parent=None):
        super().__init__(parent)
        self._size = size
        self._app_root: Optional[str] = None
        self.setFixedSize(size, size)
        self._clear()

    def set_app_root(self, app_root: str) -> None:
        """Set the application root used to resolve app-root-relative
        preview_image paths (P3.43: "voices/<id>/avatar.png").

        When not set, resolution falls back to the current working
        directory (the application root in a normal launch).
        """
        self._app_root = app_root if app_root else None

    def _clear(self) -> None:
        """Show a neutral placeholder (grey circle with '?')."""
        out = QPixmap(self._size, self._size)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(Palette.BG_RAISED)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(0, 0, self._size, self._size))
        painter.setPen(QColor(Palette.TEXT_SECONDARY))
        font = QFont("Segoe UI", max(10, self._size // 3), QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, self._size, self._size),
                         Qt.AlignmentFlag.AlignCenter, "?")
        painter.end()
        self.setPixmap(out)

    def set_voice(self, voice, app_root: Optional[str] = None) -> None:
        """Update the avatar from a VoiceProfile (or None).

        Args:
            voice: a VoiceProfile with optional ``preview_image`` and
                   ``name`` fields, or None to show the placeholder.
            app_root: optional application root for resolving
                   app-root-relative preview paths (P3.43 convention,
                   ``voices/<id>/avatar.png``). Falls back to the root
                   set via :meth:`set_app_root` / the cwd.
        """
        if voice is None:
            self._clear()
            return

        root = app_root or self._app_root or os.getcwd()
        for image_path in _preview_candidates(voice, root):
            pix = make_rounded_pixmap(image_path, self._size, voice.name)
            if pix is not None:
                self.setPixmap(pix)
                return
        # Fallback: colored circle with initial.
        self.setPixmap(make_initial_pixmap(voice.name, self._size))


def _preview_candidates(voice, root: str) -> list:
    """All plausible on-disk locations for a profile's preview image.

    P3.43: preview_image is stored app-root-relative
    ("voices/<id>/avatar.png") — that is the authoritative resolution.
    Absolute paths and legacy profile-relative bare filenames are still
    honoured as fallbacks so profiles written by older versions keep
    rendering (data compatibility, P3.43 §22).
    """
    preview = getattr(voice, "preview_image", "") or ""
    if not preview:
        return []
    candidates = []
    if os.path.isabs(preview):
        candidates.append(preview)
    else:
        candidates.append(os.path.join(root, preview))
        # Legacy bare filename ("avatar.png") — profile-directory relative.
        if os.sep not in preview:
            vid = getattr(voice, "id", "")
            if vid:
                candidates.append(
                    os.path.join(root, "voices", vid, preview))
            candidates.append(os.path.join(os.getcwd(), preview))
    return [c for c in candidates if os.path.isfile(c)]
