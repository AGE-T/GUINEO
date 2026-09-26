"""
SpeechStudio — CPU Static Ambient Fallback
==========================================

Static CPU ambient background. Renders the ambient background EXACTLY
ONCE into a QPixmap, then ``paintEvent()`` only blits the cached pixmap.

No timer. No animation. No continuous repaint. No per-frame QPainter
rendering. After the initial pixmap generation, the CPU cost is
essentially zero.

Used as a fallback when the GPU V5 renderer cannot initialize. The
static pixmap contains several soft static colour fields inspired by
the V5 shader's palette (violet, indigo, cyan, amber, magenta).
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QRadialGradient, QLinearGradient,
)
from PySide6.QtWidgets import QWidget

import logging
_log = logging.getLogger("ambient_cpu_static")


# Base background color (matches the V5 shader's base).
_BG_COLOR = QColor(2, 2, 6)  # vec3(0.0065, 0.0080, 0.016) * 255 ≈ (2, 2, 4)


# Static light field positions and colors (inspired by V5 at t≈5).
# Each field is a soft radial gradient blob.
_LIGHT_FIELDS = [
    # (x_norm, y_norm, radius_norm, color)
    (0.24, 0.48, 0.30, QColor(110, 20, 242)),   # violet
    (0.73, 0.40, 0.32, QColor(25, 46, 230)),     # indigo
    (0.47, 0.72, 0.35, QColor(10, 148, 235)),    # cyan
    (0.58, 0.26, 0.28, QColor(242, 69, 13)),     # amber
    (0.36, 0.34, 0.25, QColor(199, 20, 148)),    # magenta
]


class AmbientBackgroundCPUStatic(QWidget):
    """Static CPU ambient background.

    Renders the ambient background ONCE into a QPixmap on first paint,
    then ``paintEvent()`` only blits the cached pixmap. No timer, no
    animation, no continuous repaint.

    Exposes the same API as the GPU V5 renderer:
        - ``set_theme_active(active: bool)``
        - ``pause()``
        - ``resume()``
        - ``stop()``
        - ``is_running() -> bool``
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._pixmap: Optional[QPixmap] = None
        self._pixmap_width: int = 0
        self._pixmap_height: int = 0
        self._theme_active: bool = False

    def set_theme_active(self, active: bool) -> None:
        """Enable or disable the ambient background based on the theme."""
        self._theme_active = active
        self.setVisible(active)

    def pause(self) -> None:
        """No-op — static background has no animation to pause."""
        pass

    def resume(self) -> None:
        """No-op — static background has no animation to resume."""
        pass

    def is_running(self) -> bool:
        """Returns False — static background has no animation timer."""
        return False

    def stop(self) -> None:
        """No-op — static background has no timer to stop."""
        pass

    def _is_cache_valid(self) -> bool:
        """Return True if the cached pixmap matches the current widget size."""
        return (self._pixmap is not None
                and not self._pixmap.isNull()
                and self._pixmap_width == self.width()
                and self._pixmap_height == self.height())

    def _regenerate_pixmap(self) -> None:
        """Render the static ambient background into ``self._pixmap``.

        Called ONCE (on first paint) and on resize. Uses QPainter +
        QRadialGradient to render 5 soft colour fields on a dark base.
        No animation, no per-frame rendering.
        """
        w = max(1, self.width())
        h = max(1, self.height())

        self._pixmap = QPixmap(w, h)
        if self._pixmap.isNull():
            return
        self._pixmap_width = w
        self._pixmap_height = h

        painter = QPainter(self._pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. Base background.
        painter.fillRect(0, 0, w, h, _BG_COLOR)

        # 2. Light fields — 5 soft radial gradients.
        aspect = w / h if h > 0 else 1.0
        for (x_norm, y_norm, r_norm, color) in _LIGHT_FIELDS:
            cx = x_norm * aspect * w / aspect  # keep in pixel coords
            cy = y_norm * h
            # Actually: the V5 shader uses aspect-corrected coordinates.
            # x_norm is in aspect-corrected space, so we convert:
            #   pixel_x = x_norm * w  (x_norm already includes aspect)
            # But our _LIGHT_FIELDS use raw normalized coords [0,1],
            # so just scale to pixels directly.
            cx = x_norm * w
            cy = (1.0 - y_norm) * h  # flip Y (Qt Y is top-down, GL is bottom-up)
            radius = r_norm * max(w, h)

            gradient = QRadialGradient(QPointF(cx, cy), radius)
            # Use the color at low alpha for soft glow.
            r, g, b, _ = color.getRgb()
            # Exp(-d*d/(r*r)) falloff approximated with gradient stops.
            # At d=0: full intensity. At d=r: exp(-1)=0.37. At d=2r: exp(-4)=0.018.
            for pos, alpha_mul in [
                (0.0, 0.72),
                (0.2, 0.55),
                (0.4, 0.37),
                (0.6, 0.22),
                (0.8, 0.11),
                (1.0, 0.0),
            ]:
                gradient.setColorAt(pos, QColor(r, g, b, int(255 * alpha_mul * 0.6)))

            painter.fillRect(0, 0, w, h, gradient)

        # 3. Subtle complementary wave (static approximation).
        # The V5 shader uses sin(uv.x*4.7 + uv.y*3.4 + t*0.075).
        # We approximate with a static diagonal gradient.
        wave_grad = QLinearGradient(0, 0, w, h)
        wave_grad.setColorAt(0.0, QColor(6, 10, 19, 20))
        wave_grad.setColorAt(0.5, QColor(6, 10, 19, 40))
        wave_grad.setColorAt(1.0, QColor(6, 10, 19, 20))
        painter.fillRect(QRectF(0, 0, w, h), wave_grad)

        # 4. Soft vignette.
        # 1.0 - 0.32 * dot(q,q) where q = uv - 0.5.
        # At center: 1.0. At corners: 1.0 - 0.32*0.5 = 0.84.
        # We darken the edges with a radial gradient.
        center = QPointF(w / 2, h / 2)
        max_dist = (w ** 2 + h ** 2) ** 0.5 / 2
        vig_grad = QRadialGradient(center, max_dist)
        vig_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        vig_grad.setColorAt(0.5, QColor(0, 0, 0, 0))
        vig_grad.setColorAt(1.0, QColor(0, 0, 0, 80))  # darken edges ~30%
        painter.fillRect(QRectF(0, 0, w, h), vig_grad)

        painter.end()
        _log.info("Static ambient pixmap generated: %dx%d", w, h)

    def paintEvent(self, event) -> None:
        """Blit the cached pixmap, or generate it on first paint."""
        if not self._is_cache_valid():
            self._regenerate_pixmap()

        painter = QPainter(self)
        if self._pixmap is not None and not self._pixmap.isNull():
            painter.drawPixmap(0, 0, self._pixmap)
        else:
            painter.fillRect(self.rect(), _BG_COLOR)
        painter.end()

    def resizeEvent(self, event) -> None:
        """On resize, invalidate the cached pixmap."""
        self._pixmap = None
        self._pixmap_width = 0
        self._pixmap_height = 0
        super().resizeEvent(event)
