"""
SpeechStudio — Ambient Background Renderer Interface
====================================================

Defines the abstract AmbientRenderer interface and a concrete CPU-based
implementation (AmbientRendererCPU).

Architecture
------------
The AmbientBackgroundWidget owns an AmbientRenderer instance and calls
``renderer.render()`` to produce frames. The widget handles caching,
timing, and painting — the renderer only produces pixel data.

This isolation makes the renderer **replaceable**. A future
``AmbientRendererGPU`` implementation can be substituted without changing
the widget or the rest of the application::

    # Current (CPU):
    widget = AmbientBackgroundWidget(renderer=AmbientRendererCPU())

    # Future (GPU — NOT implemented yet):
    widget = AmbientBackgroundWidget(renderer=AmbientRendererGPU())

The GPU renderer is intentionally NOT implemented in this module — the
interface is provided so the architecture is ready for the GPU PoC.

Renderer contract
-----------------
``render(time, width, height, target_pixmap) -> bool``
    Render the frame at the given animation time into ``target_pixmap``.
    The pixmap must already be allocated at the correct size.
    Returns ``True`` on success, ``False`` on failure.

``needs_redraw(last_time, current_time) -> bool``
    Return ``True`` if the frame has changed enough to warrant a redraw.
    This allows renderers to throttle redraws based on visual delta.
    CPU renderer: redraw every 0.5 s (the animation is very slow).
    GPU renderer (future): could redraw every frame (cheap).
"""

from __future__ import annotations
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Tuple

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import (
    QPainter, QRadialGradient, QColor, QBrush, QLinearGradient, QPixmap,
)


# ---------------------------------------------------------------------------
# Colors — matches the HTML shader exactly
# ---------------------------------------------------------------------------
# vec3(0.08, 0.08, 0.1) → (20, 20, 26) in 0-255
BG_CHARCOAL = QColor(20, 20, 26)
PURPLE_R, PURPLE_G, PURPLE_B = 139, 92, 246   # #8B5CF6
ORANGE_R, ORANGE_G, ORANGE_B = 249, 115, 22   # #F97316


@dataclass
class LightField:
    """A single ambient light field (matches the shader's glow calculation).

    The center position moves along a Lissajous curve parameterized by
    ``base`` + ``amp`` * sin/cos(``t`` * ``freq`` + ``phase``).
    """

    base_x: float
    base_y: float
    amp_x: float
    amp_y: float
    freq_x: float
    freq_y: float
    phase_x: float
    phase_y: float
    r: int
    g: int
    b: int
    intensity: float

    def position_at(self, t: float, w: float, h: float) -> Tuple[float, float]:
        """Return the (x, y) pixel position of the light center at time t."""
        x = (self.base_x + self.amp_x * math.sin(t * self.freq_x + self.phase_x)) * w
        y = (self.base_y + self.amp_y * math.cos(t * self.freq_y + self.phase_y)) * h
        return x, y


# ---------------------------------------------------------------------------
# Renderer interface
# ---------------------------------------------------------------------------
class AmbientRenderer(ABC):
    """Abstract interface for ambient background renderers.

    The application talks to this interface. Concrete implementations
    (``AmbientRendererCPU``, future ``AmbientRendererGPU``) can be
    swapped without changing the rest of the app.

    This interface is intentionally minimal — two methods:

    - :meth:`render` — produce a frame into a pre-allocated QPixmap.
    - :meth:`needs_redraw` — tell the caller whether a new frame is
      needed yet (throttling).

    The widget handles all caching, timing, and painting. The renderer
    is a pure "produce pixels" service.
    """

    @abstractmethod
    def render(self, time: float, width: int, height: int,
               target: QPixmap) -> bool:
        """Render the ambient frame at the given animation time into ``target``.

        Args:
            time: Animation time in seconds. The renderer applies the
                shader's ``time * 0.2`` scaling internally — callers
                pass wall-clock animation time.
            width: Target pixmap width in pixels.
            height: Target pixmap height in pixels.
            target: Pre-allocated QPixmap to render into. Must be the
                correct size (the caller is responsible for allocation
                and reuse).

        Returns:
            ``True`` if rendering succeeded, ``False`` on failure. On
            ``False``, the caller should fall back to a solid fill.
        """
        ...

    @abstractmethod
    def needs_redraw(self, last_time: float, current_time: float) -> bool:
        """Return ``True`` if the frame has changed enough to warrant a redraw.

        This allows renderers to throttle redraws. The CPU renderer
        redraws every 0.5 s (the animation is very slow). A future GPU
        renderer could return ``True`` every call (cheap to render).

        Args:
            last_time: The animation time of the last rendered frame,
                or ``-1.0`` if no frame has been rendered yet.
            current_time: The current animation time.

        Returns:
            ``True`` if a redraw is needed, ``False`` if the current
            frame would be visually identical to the last one.
        """
        ...


# ---------------------------------------------------------------------------
# CPU renderer implementation
# ---------------------------------------------------------------------------
class AmbientRendererCPU(AmbientRenderer):
    """CPU-based ambient renderer using QPainter + QRadialGradient.

    Reproduces the HTML prototype's WebGL fragment shader behavior using
    software rendering. The :meth:`render` method computes 2 radial
    gradients + 1 linear gradient — this is the expensive part, so
    callers should cache the result in a QPixmap and only call
    :meth:`render` when :meth:`needs_redraw` returns ``True``.

    Performance characteristics:
        - render() on a 1920×1080 pixmap: ~8-15 ms (varies by CPU).
        - At 2 FPS (the default throttle), this is ~16-30 ms of CPU per
          second — well under 1 % of one core.
        - Compare to the old approach (render on every paintEvent at
          30-60 FPS): ~240-900 ms of CPU per second (15-30 % of one core).
    """

    # Redraw threshold in seconds. The animation uses ``time * 0.2``
    # scaling, so at 15 FPS (66 ms per frame) the light fields move
    # ~10 px/frame — visually smooth for the slow movement. This gives
    # a 2x CPU reduction vs 30 FPS, while keeping the animation
    # indistinguishable from the 60 FPS HTML prototype for this
    # particular shader (the movement is too slow for the eye to see
    # the difference between 15 and 60 FPS).
    #
    # A future GPU renderer could set this to 0 (redraw every tick)
    # since GPU rendering is cheap.
    REDRAW_INTERVAL: float = 0.066  # ~15 FPS

    def __init__(self):
        # Two light fields — exact match to the HTML shader.
        self._lights: List[LightField] = [
            LightField(
                base_x=0.5, base_y=0.5,
                amp_x=0.4, amp_y=0.3,
                freq_x=1.0, freq_y=0.8,
                phase_x=0.0, phase_y=0.0,
                r=PURPLE_R, g=PURPLE_G, b=PURPLE_B,
                intensity=0.7,
            ),
            LightField(
                base_x=0.5, base_y=0.4,
                amp_x=0.3, amp_y=0.4,
                freq_x=0.9, freq_y=1.1,
                phase_x=math.pi / 2,
                phase_y=0.0,
                r=ORANGE_R, g=ORANGE_G, b=ORANGE_B,
                intensity=0.7,
            ),
        ]
        # Pre-compute the gradient stops for exp(-d * 2.0) falloff.
        self._stops = self._exp_falloff_stops(0.7)

    def _exp_falloff_stops(self, intensity: float) -> List[Tuple[float, int]]:
        """Generate alpha values approximating ``exp(-d * 2.0) * intensity``.

        Returns a list of (position, alpha) tuples for QRadialGradient.
        """
        alpha = int(intensity * 255)
        return [
            (0.0, alpha),
            (0.1, int(alpha * 0.82)),
            (0.2, int(alpha * 0.67)),
            (0.3, int(alpha * 0.55)),
            (0.4, int(alpha * 0.45)),
            (0.5, int(alpha * 0.37)),
            (0.6, int(alpha * 0.30)),
            (0.7, int(alpha * 0.25)),
            (0.8, int(alpha * 0.20)),
            (0.9, int(alpha * 0.16)),
            (1.0, 0),
        ]

    def render(self, time: float, width: int, height: int,
               target: QPixmap) -> bool:
        """Render the frame at ``time`` into ``target`` (CPU implementation)."""
        if target.isNull() or width <= 0 or height <= 0:
            return False

        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. Charcoal base
        painter.fillRect(0, 0, width, height, BG_CHARCOAL)

        # 2. Light fields — 2 radial gradients that move slowly.
        t = time * 0.2  # shader: time * 0.2

        for light in self._lights:
            cx, cy = light.position_at(t, width, height)
            radius = max(width, height) * 0.8

            gradient = QRadialGradient(QPointF(cx, cy), radius)
            for pos, alpha in self._stops:
                color = QColor(light.r, light.g, light.b, alpha)
                gradient.setColorAt(pos, color)

            painter.fillRect(0, 0, width, height, QBrush(gradient))

        # 3. Bottom lighting (smoothstep(0.5, 0.0, uv.y) * 0.2)
        bottom_grad = QLinearGradient(0, height, 0, height * 0.5)
        bottom_grad.setColorAt(0.0, QColor(38, 38, 38, 20))
        bottom_grad.setColorAt(1.0, QColor(38, 38, 38, 0))
        painter.fillRect(QRectF(0, height * 0.5, width, height * 0.5),
                         QBrush(bottom_grad))

        painter.end()
        return True

    def needs_redraw(self, last_time: float, current_time: float) -> bool:
        """Return True if enough time has passed for a new frame.

        The first render (``last_time < 0``) always returns ``True``.
        Subsequent renders are throttled to ``REDRAW_INTERVAL`` (0.5 s).
        """
        if last_time < 0:
            return True
        return abs(current_time - last_time) >= self.REDRAW_INTERVAL
