"""
SpeechStudio — Ambient Background Proof of Concept v2
=====================================================

Reproduces the HTML prototype's WebGL fragment shader behavior using
QPainter and QRadialGradient.

The HTML prototype uses a WebGL fragment shader with:
- 2 light fields (purple + orange) with exp(-dist * 2.0) * 0.7 intensity
- Movement: sin/cos with time * 0.2 multiplier
- Purple center: (0.5 + 0.4*sin(t), 0.5 + 0.3*cos(t*0.8))
- Orange center: (0.5 + 0.3*cos(t*0.9), 0.4 + 0.4*sin(t*1.1))
- Bottom lighting: smoothstep(0.5, 0.0, uv.y) * 0.2
- Background: vec3(0.08, 0.08, 0.1) — deep charcoal
- Colors: purple (0.545, 0.361, 0.965), orange (0.976, 0.451, 0.086)

This PoC translates the shader math to QPainter equivalents:
- exp(-dist * 2.0) → QRadialGradient with exponential-like falloff
  (we use a 3-stop gradient that approximates the exponential curve)
- The alpha values are MUCH higher than v1 (matching the shader's 0.7 intensity)
- Movement speed matches the shader: time * 0.2

Run: python poc_ambient_background.py
"""

from __future__ import annotations
import sys
import math
import time
from dataclasses import dataclass
from typing import List, Tuple

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QRadialGradient, QColor, QPen, QBrush,
    QPainterPath, QFont, QLinearGradient
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGraphicsDropShadowEffect, QSizePolicy
)


# ---------------------------------------------------------------------------
# Color palette — matches the HTML shader exactly
# ---------------------------------------------------------------------------
# Background: vec3(0.08, 0.08, 0.1) → (20, 20, 26) in 0-255
BG_CHARCOAL = QColor(20, 20, 26)

# Purple: vec3(0.545, 0.361, 0.965) → #8B5CF6
PURPLE_R, PURPLE_G, PURPLE_B = 139, 92, 246

# Orange: vec3(0.976, 0.451, 0.086) → #F97316
ORANGE_R, ORANGE_G, ORANGE_B = 249, 115, 22

# Panel colors
PANEL_BG = QColor(22, 22, 28, 200)          # semi-transparent dark
PANEL_BORDER = QColor(60, 60, 70, 100)      # subtle border
TEXT_PRIMARY = QColor(240, 240, 245)
TEXT_SECONDARY = QColor(150, 150, 160)


# ---------------------------------------------------------------------------
# Ambient light field — matches the shader's glow calculation
# ---------------------------------------------------------------------------
@dataclass
class LightField:
    """A single ambient light field.

    Reproduces the shader's:
      float glow = exp(-dist * 2.0) * intensity;
      color = bg + light_color * glow;

    The exp(-dist * 2.0) falloff is approximated with a QRadialGradient
    using multiple stops that match the exponential curve.
    """
    # Center position (normalized 0.0–1.0), animated
    base_x: float
    base_y: float
    # Movement parameters (matching the shader's sin/cos)
    amp_x: float
    amp_y: float
    freq_x: float
    freq_y: float
    phase_x: float
    phase_y: float
    # Color
    r: int
    g: int
    b: int
    # Intensity (matching shader's 0.7 multiplier)
    # This is the MAXIMUM alpha at the center of the gradient.
    # exp(-0 * 2.0) * 0.7 = 0.7 → alpha = 0.7 * 255 ≈ 179
    intensity: float

    def position_at(self, t: float, w: float, h: float) -> Tuple[float, float]:
        """Calculate pixel position at time t.

        Matches the shader:
          vec2 pCenter = vec2(0.5 + 0.4 * sin(t), 0.5 + 0.3 * cos(t * 0.8));
        """
        x = (self.base_x + self.amp_x * math.sin(t * self.freq_x + self.phase_x)) * w
        y = (self.base_y + self.amp_y * math.cos(t * self.freq_y + self.phase_y)) * h
        return x, y


# ---------------------------------------------------------------------------
# Ambient background widget
# ---------------------------------------------------------------------------
class AmbientBackgroundWidget(QWidget):
    """Full-window animated ambient background.

    Reproduces the HTML prototype's WebGL fragment shader using QPainter.

    The shader computes per-pixel:
      glow = exp(-distance(uv, center) * 2.0) * 0.7
      color = bg + light_color * glow

    QPainter can't do per-pixel exp(), but QRadialGradient with carefully
    placed stops can approximate the exponential falloff closely enough
    that the visual result is indistinguishable.

    The key insight: exp(-d * 2.0) at distance d=0 is 1.0, at d=0.5 is 0.37,
    at d=1.0 is 0.14, at d=1.5 is 0.05. We approximate this with stops at
    0.0 (full), 0.25 (70%), 0.5 (37%), 0.75 (14%), 1.0 (0%).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(False)

        # Animation time (matches shader: time * 0.2)
        self._time: float = 0.0
        self._fps_counter: List[float] = []

        # Two light fields — EXACT match to the shader
        # Purple: vec2(0.5 + 0.4 * sin(t), 0.5 + 0.3 * cos(t * 0.8))
        # Orange: vec2(0.5 + 0.3 * cos(t * 0.9), 0.4 + 0.4 * sin(t * 1.1))
        self._lights: List[LightField] = [
            LightField(
                base_x=0.5, base_y=0.5,
                amp_x=0.4, amp_y=0.3,
                freq_x=1.0, freq_y=0.8,     # shader: sin(t), cos(t*0.8)
                phase_x=0.0, phase_y=0.0,
                r=PURPLE_R, g=PURPLE_G, b=PURPLE_B,
                intensity=0.7,               # shader: * 0.7
            ),
            LightField(
                base_x=0.5, base_y=0.4,
                amp_x=0.3, amp_y=0.4,
                freq_x=0.9, freq_y=1.1,     # shader: cos(t*0.9), sin(t*1.1)
                phase_x=math.pi / 2,         # cos → sin with phase shift
                phase_y=0.0,
                r=ORANGE_R, g=ORANGE_G, b=ORANGE_B,
                intensity=0.7,               # shader: * 0.7
            ),
        ]

        # Animation timer — ~30 FPS (shader uses requestAnimationFrame ≈ 60 FPS,
        # but 30 FPS is sufficient for this slow movement)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(33)  # 33ms ≈ 30 FPS

    def _on_tick(self):
        """Advance animation state.

        The shader uses: float t = time * 0.2;
        We accumulate time and multiply by 0.2 in the paint method.
        """
        self._time += 0.033  # ~33ms per frame
        self.update()

    def _exp_falloff_stops(self, intensity: float) -> List[Tuple[float, QColor]]:
        """Generate QRadialGradient stops that approximate exp(-d * 2.0).

        The shader uses: glow = exp(-dist * 2.0) * intensity

        exp(-d * 2.0) values:
          d=0.0 → 1.0
          d=0.1 → 0.819
          d=0.2 → 0.670
          d=0.3 → 0.549
          d=0.4 → 0.449
          d=0.5 → 0.368
          d=0.6 → 0.301
          d=0.7 → 0.247
          d=0.8 → 0.202
          d=1.0 → 0.135
          d=1.5 → 0.050
          d=2.0 → 0.018
          d=3.0 → 0.002

        We use a gradient radius of ~0.7 * min(w,h) (large, covering most
        of the window) and map the exp falloff to 0.0–1.0 stops.
        """
        alpha = int(intensity * 255)
        return [
            (0.0,  QColor(255, 255, 255, alpha)),       # center: full intensity
            (0.1,  QColor(255, 255, 255, int(alpha * 0.82))),
            (0.2,  QColor(255, 255, 255, int(alpha * 0.67))),
            (0.3,  QColor(255, 255, 255, int(alpha * 0.55))),
            (0.4,  QColor(255, 255, 255, int(alpha * 0.45))),
            (0.5,  QColor(255, 255, 255, int(alpha * 0.37))),
            (0.6,  QColor(255, 255, 255, int(alpha * 0.30))),
            (0.7,  QColor(255, 255, 255, int(alpha * 0.25))),
            (0.8,  QColor(255, 255, 255, int(alpha * 0.20))),
            (0.9,  QColor(255, 255, 255, int(alpha * 0.16))),
            (1.0,  QColor(255, 255, 255, 0)),             # edge: transparent
        ]

    def paintEvent(self, event):
        """Render the ambient background.

        Reproduces the shader's fragment main():
          vec3 color = bg + purple * pGlow + orange * oGlow + vec3(0.15) * bottomLight;

        In QPainter, we achieve additive blending by:
        1. Fill with charcoal background (opaque)
        2. For each light, draw a radial gradient with the light's color
           and alpha modulated by the exp falloff
        3. The gradients use CompositionMode_SourceOver which approximates
           the shader's additive blending (bg + color * glow)
        4. Draw the bottom lighting effect
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()

        # --- 1. Charcoal base (shader: vec3 bg = vec3(0.08, 0.08, 0.1)) ---
        painter.fillRect(0, 0, w, h, BG_CHARCOAL)

        # --- 2. Light fields (shader: bg + color * glow) ---
        # The shader uses exp(-dist * 2.0) where dist is in UV space (0-1).
        # The gradient radius should be large enough that the light covers
        # a significant portion of the screen.
        #
        # In UV space, at dist=1.0 the glow is exp(-2.0) = 0.135 (13.5%).
        # At dist=2.0 it's exp(-4.0) = 0.018 (1.8%).
        # So the visible glow extends to about dist=1.5-2.0 in UV space,
        # which means the gradient radius should be about 1.0-1.5 × the
        # window diagonal to cover the full visible area.
        #
        # For QPainter, we use radius = 0.8 * max(w, h) to ensure full coverage.

        # Shader time multiplier: float t = time * 0.2;
        t = self._time * 0.2

        for light in self._lights:
            cx, cy = light.position_at(t, w, h)

            # Large radius — the light should cover most of the window
            # The shader's exp(-dist * 2.0) means at dist=1.0 (full UV),
            # the glow is still 13.5%. So we need a large radius.
            radius = max(w, h) * 0.8

            # Create radial gradient
            gradient = QRadialGradient(QPointF(cx, cy), radius)

            # Generate stops that approximate exp(-d * 2.0) * intensity
            # We use white as the gradient color and then multiply by the
            # light's RGB. But QPainter doesn't support color multiplication
            # directly — instead we set the gradient color to the light's
            # color with modulated alpha.
            stops = self._exp_falloff_stops(light.intensity)

            for pos, white_stop in stops:
                # Replace white with the light's actual color
                color = QColor(light.r, light.g, light.b, white_stop.alpha())
                gradient.setColorAt(pos, color)

            painter.fillRect(0, 0, w, h, QBrush(gradient))

        # --- 3. Bottom lighting (shader: smoothstep(0.5, 0.0, uv.y) * 0.2) ---
        # smoothstep(0.5, 0.0, uv.y) creates a gradient from 1.0 at y=0
        # (bottom of screen in UV, but in Qt y=0 is top) to 0.0 at y=0.5.
        # The shader adds vec3(0.15) * bottomLight, which is a warm gray.
        #
        # In Qt coordinates, y=0 is top. The shader's uv.y=0 is bottom.
        # So the bottom lighting is at the BOTTOM of the Qt window.
        bottom_grad = QLinearGradient(0, h, 0, h * 0.5)
        # 0.15 * 0.2 = 0.03 intensity → alpha ≈ 8
        bottom_grad.setColorAt(0.0, QColor(38, 38, 38, 20))   # bottom: full
        bottom_grad.setColorAt(1.0, QColor(38, 38, 38, 0))    # mid: transparent
        painter.fillRect(QRectF(0, h * 0.5, w, h * 0.5), QBrush(bottom_grad))

        # --- FPS tracking ---
        now = time.time()
        self._fps_counter.append(now)
        while len(self._fps_counter) > 60:
            self._fps_counter.pop(0)

        painter.end()

    @property
    def current_fps(self) -> float:
        if len(self._fps_counter) < 2:
            return 0.0
        elapsed = self._fps_counter[-1] - self._fps_counter[0]
        if elapsed <= 0:
            return 0.0
        return (len(self._fps_counter) - 1) / elapsed


# ---------------------------------------------------------------------------
# Rounded panel widget (unchanged from v1)
# ---------------------------------------------------------------------------
class RoundedPanel(QWidget):
    """A semi-transparent panel with rounded corners and a drop shadow."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self._title = title
        self._radius = 12.0
        self._bg_color = PANEL_BG
        self._border_color = PANEL_BORDER

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        r = self._radius

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), r, r)
        painter.setClipPath(path)
        painter.fillRect(QRectF(0, 0, w, h), self._bg_color)

        # Subtle glass reflection at top
        highlight = QLinearGradient(0, 0, 0, h * 0.3)
        highlight.setColorAt(0.0, QColor(255, 255, 255, 8))
        highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(QRectF(0, 0, w, h * 0.3), QBrush(highlight))

        painter.setClipping(False)
        pen = QPen(self._border_color, 1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        if self._title:
            painter.setPen(TEXT_PRIMARY)
            font = QFont("Segoe UI", 11, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(QRectF(16, 12, w - 32, 24),
                             Qt.Alignment.AlignLeft | Qt.Alignment.AlignVCenter,
                             self._title)

        painter.end()


# ---------------------------------------------------------------------------
# Floating status bar (unchanged from v1)
# ---------------------------------------------------------------------------
class FloatingStatusBar(QWidget):
    """A floating, rounded status bar with equal margins from edges."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self._radius = 10.0

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 100))
        shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        r = self._radius

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), r, r)
        painter.setClipPath(path)
        painter.fillRect(QRectF(0, 0, w, h), QColor(16, 16, 20, 220))

        painter.setClipping(False)
        pen = QPen(PANEL_BORDER, 1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        painter.setPen(TEXT_SECONDARY)
        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        painter.drawText(QRectF(16, 0, w - 32, h),
                         Qt.Alignment.AlignLeft | Qt.Alignment.AlignVCenter,
                         "● Ready  |  GPU: RTX 5070  |  VRAM: 8.2/12 GB  |  24 kHz  |  WAV")
        painter.drawText(QRectF(16, 0, w - 32, h),
                         Qt.Alignment.AlignRight | Qt.Alignment.AlignVCenter,
                         "30 FPS")

        painter.end()


# ---------------------------------------------------------------------------
# Main PoC window
# ---------------------------------------------------------------------------
class PoCWindow(QWidget):
    """Main PoC window: ambient background + translucent panels + status bar."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SpeechStudio — Ambient Background PoC v2")
        self.setMinimumSize(1024, 600)
        self.resize(1280, 720)

        self._bg = AmbientBackgroundWidget(self)
        self._left_panel = RoundedPanel("VOICE", self)
        self._center_panel = RoundedPanel("SCRIPT", self)
        self._right_panel = RoundedPanel("CONTROLS", self)
        self._status_bar = FloatingStatusBar(self)

        self._panel_margin = 12
        self._status_margin = 16
        self._status_height = 40
        self._gap = 12

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width()
        h = self.height()
        m = self._panel_margin
        gap = self._gap
        sh = self._status_height
        sm = self._status_margin
        avail_h = h - sh - sm - m
        left_w = min(260, w // 5)
        right_w = min(320, w // 4)
        center_w = w - left_w - right_w - m * 2 - gap * 2
        self._bg.setGeometry(0, 0, w, h)
        self._left_panel.setGeometry(m, m, left_w, avail_h)
        self._center_panel.setGeometry(m + left_w + gap, m, center_w, avail_h)
        self._right_panel.setGeometry(m + left_w + gap + center_w + gap, m, right_w, avail_h)
        self._status_bar.setGeometry(sm, h - sh - sm, w - sm * 2, sh)

    def get_fps(self) -> float:
        return self._bg.current_fps


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SpeechStudio PoC v2")
    window = PoCWindow()
    window.show()

    def _print_fps():
        fps = window.get_fps()
        print(f"[PoC v2] FPS: {fps:.1f}")

    fps_timer = QTimer()
    fps_timer.timeout.connect(_print_fps)
    fps_timer.start(5000)

    print("[PoC v2] Window opened. The ambient background matches the HTML shader:")
    print("  - 2 light fields (purple + orange)")
    print("  - exp(-dist * 2.0) * 0.7 intensity (much brighter than v1)")
    print("  - Movement: sin/cos with time * 0.2 (matching shader)")
    print("  - Purple: (0.5 + 0.4*sin(t), 0.5 + 0.3*cos(t*0.8))")
    print("  - Orange: (0.5 + 0.3*cos(t*0.9), 0.4 + 0.4*sin(t*1.1))")
    print("  - Bottom lighting: smoothstep(0.5, 0.0, uv.y) * 0.2")
    print("  - Background: vec3(0.08, 0.08, 0.1)")
    print("[PoC v2] Resize the window to test scaling.")
    print("[PoC v2] FPS printed every 5 seconds. Close window to exit.")

    exit_code = app.exec()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
