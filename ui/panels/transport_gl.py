"""
SpeechStudio — V12 GPU Horizontal Transport Renderer
=====================================================

This module is the GPU rendering layer for the bottom transport. It is a
direct port of the validated standalone reference
``gpu_transport_horizontal_waveform_v12_test_v6.py`` and reproduces:

    * QOpenGLWidget + QOpenGLShaderProgram + QOpenGLBuffer (VBO)
    * QOpenGLTexture for Material Symbols icons (play_arrow / pause / stop)
    * OpenGL 4.6 Compatibility Profile (set globally in SpeechStudio.py)
    * Fullscreen VBO (two triangles) + single fragment shader
    * GPU-generated island body + border
    * GPU-generated Play/Pause glow (exp falloff from circle SDF)
    * GPU-generated progress ring (radius 88, half-width 4, round caps)
    * GPU-generated progress rotation (4-second clockwise sweep when playing)
    * GPU-generated Stop glow (exp falloff from rounded-rect SDF)
    * GPU-sampled Material Symbols icon textures
    * GPU smooth reset (~350 ms) of the progress offset when Stop is pressed

WHAT THIS MODULE IS NOT
-----------------------
    * It does NOT own the playback state machine — that lives in
      WaveformPlayer, which calls ``set_playing`` / ``set_progress`` /
      ``set_playhead`` / ``trigger_stop_reset``.
    * It does NOT load WAV files or compute peaks — that is
      WaveformCanvas.  The GPU renderer draws only the transport controls
      and the waveform CONTAINER (a dark rounded surface).  The real
      waveform bars are painted by WaveformCanvas on top of the container.
    * It does NOT replace QPainter for the waveform data — only for the
      transport surface, controls, glow, progress, and icons.

PUBLIC API
----------
    HorizontalTransportGL(parent)
        .play_clicked (Signal)         # emitted on left-click inside Play
        .stop_clicked (Signal)          # emitted on left-click inside Stop
        .set_playing(bool)              # drive icon swap + glow strength
        .set_progress(float 0..1)       # drive the progress ring fill
        .set_playhead(float 0..1)       # drive the waveform playhead uniform
        .trigger_stop_reset()           # start the ~350 ms offset unwind

The widget is transparent (alpha 0) outside the island body so it can
sit behind the real WaveformCanvas, which paints the actual waveform
bars on top of the GPU-drawn container.
"""

from __future__ import annotations

import ctypes
import math
import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QByteArray
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLTexture,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtSvg import QSvgRenderer


# ============================================================
# OpenGL constants (avoid pulling in PyOpenGL)
# ============================================================
GL_COLOR_BUFFER_BIT = 0x00004000
GL_TRIANGLES = 0x0004
GL_FLOAT = 0x1406


# ============================================================
# Validated V12 transport constants (DO NOT CHANGE)
# ============================================================
# These exact values are reproduced verbatim from the standalone V12
# reference. They define the progress ring geometry, the rotation speed,
# and the smooth-stop time constant. Changing any of them breaks visual
# parity with the validated renderer.
RING_RADIUS = 88.0
CIRCUMFERENCE = 2.0 * math.pi * RING_RADIUS        # 552.92...
START_OFFSET = 200.0                               # initial dashoffset
PROGRESS_SPEED = 40.0                               # px / second while playing
STOP_RESET_SECONDS = 0.35                           # ~350 ms visual reset
ROTATION_SECONDS = 4.0                              # full rotation period


# V6 horizontal layout geometry (verified against the reference shader).
# These define where the island, Play/Pause, Stop, and waveform container
# are positioned inside the 1280x300 reference canvas. The shader scales
# the reference canvas to fit the widget while preserving aspect ratio.
V6_REF_W = 1280.0
V6_REF_H = 300.0


# Material Symbols Outlined glyph paths (exact strings from the V6 file).
PLAY_ARROW_PATH = "M8 5v14l11-7z"
PAUSE_PATH = "M6 19h4V5H6v14zm8-14v14h4V5h-4z"
STOP_PATH = "M6 6h12v12H6z"


# ============================================================
# Shaders
# ============================================================
# The fragment shader is reproduced verbatim from the validated V12
# reference (gpu_transport_horizontal_waveform_v12_test_v6.py).
# It renders, in one fullscreen pass:
#   1. Island body + border
#   2. Play/Pause glow + core
#   3. Progress track + active arc + round caps (start + end)
#   4. Play/Pause Material Symbols icon (GPU-sampled texture)
#   5. Stop glow + body + border
#   6. Stop Material Symbols icon (GPU-sampled texture)
#   7. Waveform CONTAINER (dark rounded surface) — but NOT the
#      waveform bars themselves. The real waveform is painted by
#      WaveformCanvas on top of this renderer.
#
# The shader's mock waveform block (waveformAmplitude + bar rendering)
# is intentionally OMITTED here. We keep only the container body +
# border + center line + playhead so the real WaveformCanvas can draw
# the actual WAV peaks on top without double-rendering.
VERTEX_SHADER = """
attribute vec2 p;

void main()
{
    gl_Position = vec4(p, 0.0, 1.0);
}
"""


FRAGMENT_SHADER = """
uniform vec2  uResolution;
uniform float uPlaying;
uniform float uPlayHover;
uniform float uStopHover;
uniform float uPressed;
uniform float uOffset;
uniform float uRotation;
uniform float uPlayhead;
uniform float uTime;

uniform sampler2D uPlayIcon;
uniform sampler2D uPauseIcon;
uniform sampler2D uStopIcon;
uniform sampler2D uWaveform;

const float PI = 3.14159265359;
const float TAU = 6.28318530718;

const float R = 88.0;
const float C = 552.92030703;

// ------------------------------------------------------------------
// Utilities
// ------------------------------------------------------------------

float ringMask(float d, float r, float w)
{
    return 1.0 - smoothstep(
        w,
        w + 1.2,
        abs(d - r)
    );
}

float roundBox(
    vec2 p,
    vec2 halfSize,
    float radius
)
{
    vec2 q =
        abs(p) -
        halfSize +
        vec2(radius);

    return length(max(q, 0.0))
         + min(max(q.x, q.y), 0.0)
         - radius;
}

// ------------------------------------------------------------------
// Main
// ------------------------------------------------------------------

void main()
{
    // ---- Left-anchored compact strip scaling ----
    // scale_y = widget_height / 300.0 (the V6 reference canvas height).
    // With widget height = 105px, scale_y = 0.35.
    // The control cluster (Play at x=120, Stop at x=315) is LEFT-ANCHORED
    // via origin.x = left_margin. The island stretches from left_margin to
    // (width - right_margin). The waveform stretches from the V6 gap (460)
    // to (width - right_margin - volume_reserved_width).
    float scale_y = uResolution.y / 300.0;

    // Actual-pixel margins (constant, not scaled).
    float left_margin = 20.0;
    float right_margin = 20.0;
    // Volume reserved area: icon + gap + slider + internal padding + waveform gap
    float volume_reserved = 186.0;

    // Left-anchored origin: the reference canvas starts from left_margin.
    // Vertical origin = 0 (the 300px reference maps to the full widget height,
    // so the island at ref y=34..266 maps to ~12px..93px — naturally floating).
    vec2 origin = vec2(left_margin, 0.0);

    // Map fragment coordinate to V6 reference coordinate system.
    vec2 ref =
        (gl_FragCoord.xy - origin) /
        max(scale_y, 0.0001);

    // Island geometry: stretches from x=0 to x=island_right_ref in reference
    // coords (left_margin to width-right_margin in actual pixels).
    float island_right_ref =
        (uResolution.x - left_margin - right_margin) /
        max(scale_y, 0.0001);
    float island_half_w = island_right_ref * 0.5;
    float island_cx = island_half_w;  // center = half the total width

    // Base background: matches the ambient GPU V5 renderer's base color
    // (#0C0A0E → vec3(0.047, 0.040, 0.055)). The GL widget is OPAQUE
    // (no WA_TranslucentBackground) — areas outside the island body output
    // this color, which matches the application's ambient background, so
    // the island appears to float without requiring alpha compositing.
    vec3 color = vec3(
        0.047,
        0.040,
        0.055
    );
    float alpha = 1.0;

    vec3 purple = vec3(
        0.6588235,
        0.3333333,
        0.9686275
    );

    vec3 primary = vec3(
        0.8156863,
        0.7372549,
        1.0
    );

    // ================================================================
    // HORIZONTAL TRANSPORT ISLAND (floating, left-anchored, full width)
    // ================================================================
    // The island stretches from left_margin to (width - right_margin).
    // In reference coords: x=0 to x=island_right_ref.
    vec2 islandP =
        ref -
        vec2(
            island_cx,
            150.0
        );

    float islandSdf =
        roundBox(
            islandP,
            vec2(
                island_half_w,
                116.0
            ),
            18.0
        );

    float islandBody =
        1.0 -
        smoothstep(
            0.0,
            1.5,
            islandSdf
        );

    vec3 islandColor = vec3(
        0.082,
        0.074,
        0.092
    );

    color = mix(
        color,
        islandColor,
        islandBody
    );

    float islandBorder =
        1.0 -
        smoothstep(
            0.8,
            2.0,
            abs(islandSdf)
        );

    color +=
        vec3(
            0.16,
            0.13,
            0.19
        ) *
        islandBorder *
        0.75 * islandBody;

    // Island is drawn on top of the ambient-matching base color.
    // No alpha manipulation needed — the GL widget is opaque.

    // ================================================================
    // PLAY / PAUSE  (HTML fidelity corrections applied)
    // ================================================================
    vec2 playP =
        ref -
        vec2(
            120.0,
            150.0
        );

    // ---- Hover + pressed + pulse scaling (uniform, around center) ----
    // HTML: hover:scale-105 (1.05), active:scale-95 (0.95)
    // HTML: pulseGlow scale 1.0→1.02→1.0 over 2s when playing
    float playHover = 1.0 + 0.05 * uPlayHover;
    float playPressed = 1.0 - 0.05 * uPlayHover * uPressed;
    // Pulse: 2-second period, cos-based for 0→1→0 shape
    float pulseT = uPlaying > 0.5
        ? (1.0 - cos(uTime * PI)) * 0.5
        : 0.0;
    float playPulse = 1.0 + 0.02 * pulseT;
    float playScale = playHover * playPressed * playPulse;
    playP /= playScale;

    float d = length(playP);

    // V12 PLAY GLOW (GPU-generated, derived from circle SDF)
    // ROOT CAUSE of the "purple blob": the playingGlow had a wide /45.0
    // term that extended 45 ref px beyond the core edge at 0.22 intensity,
    // creating a large visible halo. Combined with the /25.0 term at 0.42,
    // the total playing glow at the core edge was 0.64 — nearly 2x brighter
    // than Stop (0.40).
    //
    // FIX: removed the /45.0 wide term. Now playingGlow has only the /25.0
    // term at 0.42 — comparable to Stop's 0.40 at /15.0, with a slightly
    // wider but still controlled falloff. The glow falls off from the core
    // edge (d=80) outward, matching the Stop glow's behavior.
    float baseGlow =
        exp(
            -max(d - 80.0, 0.0) / 15.0
        ) * 0.22;

    float playingGlow =
        exp(
            -max(d - 80.0, 0.0) / 25.0
        ) * 0.42;

    float glow =
        mix(
            baseGlow,
            playingGlow,
            uPlaying
        );

    glow *=
        1.0 +
        0.18 * uPlayHover;

    color +=
        purple * glow;

    // ---- Glow border (HTML: 2px solid rgba(168,85,247,0.6/0.9)) ----
    float borderAlpha =
        mix(0.6, 0.9, uPlaying);
    float glowBorder =
        1.0 -
        smoothstep(
            79.0,
            81.0,
            abs(d - 80.0)
        );
    color += purple * glowBorder * borderAlpha;

    // ---- Inner core (HTML: bg-surface-container-low = #1C1B1B) ----
    float coreRadius = 80.0 * playPulse;
    float core =
        1.0 -
        smoothstep(
            coreRadius - 1.0,
            coreRadius,
            d
        );

    // Core color: #1C1B1B (HTML reference, was #0E0D11)
    float pulseOpacity = 1.0 - 0.2 * pulseT;
    vec3 coreColor = vec3(
        0.1098,
        0.1059,
        0.1059
    );
    color = mix(
        color,
        coreColor * pulseOpacity,
        core
    );

    // ---- Inset glow (HTML: inset 0 0 10px rgba(168,85,247,0.3)) ----
    float innerGlow =
        exp(
            -max(80.0 - d, 0.0) / 10.0
        ) * 0.3 * core;
    color += purple * innerGlow;

    // V12 PROGRESS RING (GPU-generated, with round caps)
    // The progress ring uses the UNSCALED playP (before hover/pressed/pulse
    // scaling) so it always stays at radius R=88 regardless of control scale.
    // The entire ring (background track + active arc + caps) rotates together
    // via uRotation, matching the HTML reference where the entire SVG
    // .progress-ring element rotates.
    vec2 playPRing = playP * playScale;  // undo the scaling to get unscaled coords
    float dRing = length(playPRing);

    float track =
        ringMask(
            dRing,
            R,
            3.0
        );

    color +=
        vec3(
            0.165,
            0.165,
            0.165
        ) *
        track;

    float angle =
        atan(
            playPRing.y,
            playPRing.x
        );

    angle += uRotation;

    float fromBottom =
        mod(
            angle -
            PI * 0.5 +
            TAU,
            TAU
        );

    float visible =
        clamp(
            1.0 -
            uOffset / C,
            0.0,
            1.0
        );

    float endAngle =
        visible * TAU;

    float activeTrack =
        ringMask(
            dRing,
            R,
            4.0
        );

    float active =
        step(
            fromBottom,
            endAngle
        ) *
        activeTrack;

    // V12 round caps — start and end points are computed in the SAME
    // rotated coordinate system as the active arc. Both caps share the
    // same radius R and the same rotation uRotation, so they stay
    // attached to the arc endpoints at every angle.
    float startCartesianAngle =
        PI * 0.5 -
        uRotation;

    vec2 startPoint =
        vec2(
            R * cos(startCartesianAngle),
            R * sin(startCartesianAngle)
        );

    float endCartesianAngle =
        PI * 0.5 +
        endAngle -
        uRotation;

    vec2 endPoint =
        vec2(
            R * cos(endCartesianAngle),
            R * sin(endCartesianAngle)
        );

    float startCap =
        1.0 -
        smoothstep(
            3.25,
            4.25,
            distance(
                playPRing,
                startPoint
            )
        );

    float endCap =
        1.0 -
        smoothstep(
            3.25,
            4.25,
            distance(
                playPRing,
                endPoint
            )
        );

    active =
        max(
            active,
            max(
                startCap,
                endCap
            )
        );

    color +=
        primary *
        active *
        0.92;

    color +=
        purple *
        active *
        0.12;

    // Play/Pause icon — GPU-sampled Material Symbols texture.
    // HTML: text-6xl = 60px. Divisor 80 maps the icon to ~60px visual.
    vec2 playUv =
        playP / 80.0 +
        vec2(0.5);

    vec4 playIcon =
        uPlaying > 0.5
        ? texture2D(
            uPauseIcon,
            playUv
        )
        : texture2D(
            uPlayIcon,
            playUv
        );

    // Icon halo removed — the previous distance-based formula added
    // purple * 0.049 EVERYWHERE outside the glyph (where playIcon.a=0),
    // contributing to the purple blob. The icon itself is crisp at
    // primary color via the mix() below, which is sufficient.
    // The HTML text-shadow effect is approximated by the outer glow
    // which now properly falls off from the core edge.

    color =
        mix(
            color,
            primary,
            playIcon.a
        );

    // ================================================================
    // STOP  (HTML fidelity corrections applied)
    // ================================================================
    vec2 stopP =
        ref -
        vec2(
            315.0,
            150.0
        );

    // ---- Hover + pressed scaling (uniform, around center) ----
    // HTML: hover:scale-105 (1.05), active:scale-90 (0.90)
    float stopHover = 1.0 + 0.05 * uStopHover;
    float stopPressed = 1.0 - 0.10 * uStopHover * uPressed;
    float stopScale = stopHover * stopPressed;
    stopP /= stopScale;

    // HTML: rounded-2xl = 1rem = 16px (was 12px)
    float stopD =
        roundBox(
            stopP,
            vec2(40.0),
            16.0
        );

    // V12 STOP GLOW (GPU-generated, derived from rounded-rect SDF)
    float stopGlow =
        exp(
            -max(stopD, 0.0) / 15.0
        ) *
        0.40;

    stopGlow *=
        1.0 +
        0.30 * uStopHover;

    if (
        uPressed > 0.5 &&
        uStopHover > 0.5
    )
    {
        stopGlow +=
            exp(
                -max(stopD, 0.0) / 25.0
            ) *
            0.70;
    }

    color +=
        purple *
        stopGlow;

    float stopBody =
        1.0 -
        smoothstep(
            0.0,
            1.5,
            stopD
        );

    color = mix(
        color,
        vec3(
            0.1098,
            0.1059,
            0.1059
        ),
        stopBody
    );

    float stopBorder =
        1.0 -
        smoothstep(
            1.0,
            2.5,
            abs(stopD)
        );

    color +=
        purple *
        stopBorder *
        0.55 * stopBody;

    // Stop icon — GPU-sampled Material Symbols texture.
    // HTML: text-4xl = 36px. Increased divisor from 40→60 to match.
    vec2 stopUv =
        stopP / 60.0 +
        vec2(0.5);

    vec4 stopIcon =
        texture2D(
            uStopIcon,
            stopUv
        );

    // Icon halo removed (same fix as Play — the formula added purple
    // everywhere outside the glyph). The Stop glow already provides
    // the halo effect via the outer exp falloff.

    color =
        mix(
            color,
            primary,
            stopIcon.a
        );

    // ================================================================
    // WAVEFORM CONTAINER (body + border + center line + playhead)
    //
    // NOTE: the V12 reference also renders mock waveform bars here.
    // We DELIBERATELY OMIT the mock bars — the real waveform is
    // painted by WaveformCanvas (QPainter) on top of this renderer.
    // We keep only the container surface + border + playhead so the
    // GPU renderer defines the visual character of the container,
    // and the real WAV data is drawn on top.
    // ================================================================
    // The waveform container stretches from the V6 gap (x=460) to the
    // right edge minus the volume reserved area. In reference coords:
    //   wave_left = 460.0 (V6 Stop→Wave gap)
    //   wave_right = island_right_ref - volume_reserved / scale_y
    vec2 waveCenter =
        vec2(
            (460.0 + island_right_ref - volume_reserved / max(scale_y, 0.0001)) * 0.5,
            150.0
        );

    vec2 waveHalf =
        vec2(
            (island_right_ref - volume_reserved / max(scale_y, 0.0001) - 460.0) * 0.5,
            96.0
        );

    vec2 waveP =
        ref -
        waveCenter;

    float waveSdf =
        roundBox(
            waveP,
            waveHalf,
            10.0
        );

    float waveBody =
        1.0 -
        smoothstep(
            0.0,
            1.5,
            waveSdf
        );

    color = mix(
        color,
        vec3(
            0.065,
            0.060,
            0.072
        ),
        waveBody
    );

    float waveBorder =
        1.0 -
        smoothstep(
            0.8,
            2.0,
            abs(waveSdf)
        );

    color +=
        vec3(
            0.12,
            0.10,
            0.15
        ) *
        waveBorder *
        0.40 * waveBody;

    // Fine center reference line — kept on the GPU layer because it is
    // part of the container's visual character (not the WAV data).
    if (
        waveP.x >= -waveHalf.x &&
        waveP.x <= waveHalf.x &&
        abs(waveP.y) <= waveHalf.y
    )
    {
        float x01 =
            (waveP.x + waveHalf.x) /
            (2.0 * waveHalf.x);

        // Sample the REAL waveform peaks from the uWaveform texture.
        // This replaces the V12 mock ``waveformAmplitude(x01)`` function
        // with real WAV peak data uploaded by set_waveform_peaks().
        // The texture is 1-D (height=1); the red channel stores the
        // normalised peak amplitude (0..1).
        float amp = texture2D(uWaveform, vec2(x01, 0.5)).r;

        float centerDistance =
            abs(waveP.y);

        // V12 bar mask — bars extend ±amp*34 px from center.
        float barMask =
            1.0 -
            smoothstep(
                amp * 34.0,
                amp * 34.0 + 1.0,
                centerDistance
            );

        // V12 color mix — inactive → primary, based on playhead position.
        vec3 waveformColor =
            mix(
                vec3(
                    0.28,
                    0.24,
                    0.34
                ),
                primary,
                smoothstep(
                    0.15,
                    0.75,
                    uPlayhead - x01
                )
            );

        color +=
            waveformColor *
            barMask *
            0.90 * waveBody;

        // Fine center line.
        float centerLine =
            1.0 -
            smoothstep(
                0.0,
                0.8,
                centerDistance
            );

        color +=
            vec3(
                0.22,
                0.18,
                0.28
            ) *
            centerLine *
            0.25 * waveBody;

        // GPU playhead line — driven by uPlayhead.
        // Widened from 1.0 to 3.0 reference px so it's visible at the
        // compact 0.35 display scale (3.0 * 0.35 = 1.05 actual px).
        float playX =
            -waveHalf.x +
            uPlayhead *
            2.0 *
            waveHalf.x;

        float playhead =
            1.0 -
            smoothstep(
                0.0,
                3.0,
                abs(
                    waveP.x -
                    playX
                )
            );

        color +=
            primary *
            playhead *
            0.78 * waveBody;
    }

    gl_FragColor = vec4(color, 1.0);
}
"""


# ============================================================
# Icon texture builder (verbatim from V12 reference)
# ============================================================
def _icon_texture(path_data: str) -> QOpenGLTexture:
    """Rasterise a Material Symbols SVG path into a QOpenGLTexture.

    Reproduces the validated V12 ``icon_texture()`` helper exactly:
        * SVG viewBox 0 0 24 24, fill #FFFFFF
        * QSvgRenderer → QImage(256, 256, RGBA8888)
        * QOpenGLTexture with Linear min/mag filter, ClampToEdge wrap
    """
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg"'
        ' width="24" height="24" viewBox="0 0 24 24">'
        '<path d="{0}" fill="#FFFFFF"/></svg>'
    ).format(path_data).encode("utf-8")

    renderer = QSvgRenderer(QByteArray(svg))
    if not renderer.isValid():
        raise RuntimeError("Icon SVG could not be loaded: {0}".format(path_data))

    image = QImage(256, 256, QImage.Format.Format_RGBA8888)
    image.fill(QColor(0, 0, 0, 0))

    painter = QPainter(image)
    if not painter.isActive():
        raise RuntimeError("QPainter could not be started for icon rasterization.")
    try:
        renderer.render(painter)
    finally:
        painter.end()

    texture = QOpenGLTexture(image)
    if not texture.isCreated():
        raise RuntimeError("QOpenGLTexture creation failed for {0}".format(path_data))

    texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
    texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
    texture.setWrapMode(
        QOpenGLTexture.CoordinateDirection.DirectionS,
        QOpenGLTexture.WrapMode.ClampToEdge,
    )
    texture.setWrapMode(
        QOpenGLTexture.CoordinateDirection.DirectionT,
        QOpenGLTexture.WrapMode.ClampToEdge,
    )
    return texture


# ============================================================
# HorizontalTransportGL — the GPU transport widget
# ============================================================
class HorizontalTransportGL(QOpenGLWidget):
    """GPU transport renderer (V12 architecture, V6 horizontal geometry).

    Signals:
        play_clicked   — emitted on left-click inside the Play/Pause control
        stop_clicked   — emitted on left-click inside the Stop control

    State setters (called by WaveformPlayer):
        set_playing(bool)        — drive icon swap (play_arrow ↔ pause) and
                                   strengthen the glow while playing.
        set_progress(float 0..1) — drive the active progress arc fill.
                                   Maps the existing WaveformCanvas
                                   playhead fraction to the V12 ``uOffset``
                                   uniform via ``offset = (1 - progress) * C``.
        set_playhead(float 0..1) — drive the GPU playhead line inside the
                                    waveform container.
        trigger_stop_reset()    — start the ~350 ms smooth unwind of the
                                   progress offset back to the full circle
                                   (V12 STOP_RESET_SECONDS = 0.35).

    The widget itself is transparent outside the island body, so the
    existing WaveformCanvas (real WAV data) can sit on top of it and
    paint the actual waveform bars over the GPU-drawn container.
    """

    play_clicked = Signal()
    stop_clicked = Signal()
    waveform_seek_requested = Signal(float)  # position 0..1

    def __init__(self, parent=None):
        super().__init__(parent)
        # OPAQUE GL widget — no WA_TranslucentBackground. The shader
        # outputs the ambient background color (vec3(0.047, 0.040, 0.055))
        # outside the island body, which matches the application's ambient
        # background. This makes the island appear to float without
        # requiring QOpenGLWidget alpha compositing (which is unreliable
        # on Windows).
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)

        # GL resources (created in initializeGL).
        self.program: Optional[QOpenGLShaderProgram] = None
        self.vbo: Optional[QOpenGLBuffer] = QOpenGLBuffer(
            QOpenGLBuffer.Type.VertexBuffer,
        )
        self.play_texture: Optional[QOpenGLTexture] = None
        self.pause_texture: Optional[QOpenGLTexture] = None
        self.stop_texture: Optional[QOpenGLTexture] = None
        self.waveform_texture: Optional[QOpenGLTexture] = None
        # Cached peak data (List[float] 0..1) for texture (re)creation.
        self._waveform_peaks: list = []

        # Uniform locations (resolved in initializeGL).
        self.u_resolution = -1
        self.u_playing = -1
        self.u_play_hover = -1
        self.u_stop_hover = -1
        self.u_pressed = -1
        self.u_offset = -1
        self.u_rotation = -1
        self.u_playhead = -1
        self.u_play = -1
        self.u_pause = -1
        self.u_stop = -1
        self.u_waveform = -1
        self.u_time = -1

        self.initialized = False
        self._init_failed = False

        # State (driven by WaveformPlayer from the existing app state).
        self.playing = False
        self.play_hover = False
        self.stop_hover = False
        self.pressed = False

        # V12 progress animation state.
        # offset is the live (smoothed) value; target_offset is where we
        # are heading. While playing, target_offset decreases at
        # PROGRESS_SPEED px/s. On Stop, target_offset jumps to CIRCUMFERENCE
        # and offset smoothly catches up over ~350 ms.
        self.offset = START_OFFSET
        self.target_offset = START_OFFSET
        self.rotation = 0.0
        self.playhead = 0.0
        self._external_progress = 0.0  # last value pushed by set_progress

        # Elapsed time for pulse animation (uTime uniform)
        self._elapsed = 0.0

        # Stop reset animation state (CSS ease, ~350ms)
        self._reset_active = False
        self._reset_start_offset = START_OFFSET
        self._reset_start_time = 0.0

        # Animation timer — runs only while visible and initialized.
        self.last_tick = 0.0
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)  # ~60 fps
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------
    # OpenGL lifecycle
    # ------------------------------------------------------------------
    def initializeGL(self) -> None:
        """Initialize GL resources. Failures are logged, not raised.

        If GL initialization fails (e.g. shader compile error, missing
        uniform, VBO creation failure), we log the error and set
        ``_init_failed = True``. ``paintGL`` checks this flag and
        returns early — the widget renders nothing but the application
        does NOT crash. This matches the defensive pattern used by the
        ambient GPU V5 renderer.
        """
        try:
            context = self.context()
            if context is None or not context.isValid():
                raise RuntimeError("Invalid OpenGL context for HorizontalTransportGL.")

            fmt = context.format()
            # Log the actual GL version we got (useful for runtime validation).
            try:
                from engine.logger import get_logger
                get_logger("transport_gl").info(
                    "HorizontalTransportGL: OpenGL %d.%d profile=%s",
                    fmt.majorVersion(), fmt.minorVersion(), str(fmt.profile()),
                )
            except Exception:
                pass

            # Compile + link the shader program.
            self.program = QOpenGLShaderProgram(self)
            if not self.program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Vertex, VERTEX_SHADER,
            ):
                raise RuntimeError("Vertex shader compile failed: {0}".format(
                    self.program.log(),
                ))
            if not self.program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Fragment, FRAGMENT_SHADER,
            ):
                raise RuntimeError("Fragment shader compile failed: {0}".format(
                    self.program.log(),
                ))
            self.program.bindAttributeLocation("p", 0)
            if not self.program.link():
                raise RuntimeError("Shader link failed: {0}".format(
                    self.program.log(),
                ))

            # Resolve uniform locations.
            self.u_resolution = self.program.uniformLocation("uResolution")
            self.u_playing = self.program.uniformLocation("uPlaying")
            self.u_play_hover = self.program.uniformLocation("uPlayHover")
            self.u_stop_hover = self.program.uniformLocation("uStopHover")
            self.u_pressed = self.program.uniformLocation("uPressed")
            self.u_offset = self.program.uniformLocation("uOffset")
            self.u_rotation = self.program.uniformLocation("uRotation")
            self.u_playhead = self.program.uniformLocation("uPlayhead")
            self.u_play = self.program.uniformLocation("uPlayIcon")
            self.u_pause = self.program.uniformLocation("uPauseIcon")
            self.u_stop = self.program.uniformLocation("uStopIcon")
            self.u_waveform = self.program.uniformLocation("uWaveform")
            self.u_time = self.program.uniformLocation("uTime")
            for name, loc in (
                ("uResolution", self.u_resolution),
                ("uPlaying", self.u_playing),
                ("uPlayHover", self.u_play_hover),
                ("uStopHover", self.u_stop_hover),
                ("uPressed", self.u_pressed),
                ("uOffset", self.u_offset),
                ("uRotation", self.u_rotation),
                ("uPlayhead", self.u_playhead),
                ("uPlayIcon", self.u_play),
                ("uPauseIcon", self.u_pause),
                ("uStopIcon", self.u_stop),
                ("uWaveform", self.u_waveform),
                ("uTime", self.u_time),
            ):
                if loc < 0:
                    raise RuntimeError("Uniform missing: {0}".format(name))

            # Fullscreen VBO: two triangles covering clip space [-1, 1]².
            if not self.vbo.create():
                raise RuntimeError("VBO create failed.")
            if not self.vbo.bind():
                raise RuntimeError("VBO bind failed.")
            vertices = (
                -1.0, -1.0,
                 1.0, -1.0,
                -1.0,  1.0,
                -1.0,  1.0,
                 1.0, -1.0,
                 1.0,  1.0,
            )
            data = (ctypes.c_float * len(vertices))(*vertices)
            self.vbo.allocate(data, ctypes.sizeof(data))
            self.vbo.release()

            # Material Symbols icon textures.
            self.play_texture = _icon_texture(PLAY_ARROW_PATH)
            self.pause_texture = _icon_texture(PAUSE_PATH)
            self.stop_texture = _icon_texture(STOP_PATH)

            # Waveform peaks texture — 1D (height=1), red channel = peak
            # amplitude (0..1). Created empty (all zeros) here; updated
            # by set_waveform_peaks() when real WAV data is loaded.
            self._rebuild_waveform_texture()

            self.initialized = True
            self.last_tick = time.perf_counter()
            # Only tick while we are visible; hideEvent stops the timer.
            if self.isVisible():
                self.timer.start()

            try:
                from engine.logger import get_logger
                get_logger("transport_gl").info(
                    "HorizontalTransportGL initialized — success "
                    "(V12 shader linked, 3 icon textures created)",
                )
            except Exception:
                pass

        except Exception as exc:
            # Log the failure but do NOT re-raise. The app continues to
            # run; the transport just renders nothing until the issue is
            # resolved. This prevents a GL init failure from crashing
            # the entire application.
            self.initialized = False
            self._init_failed = True
            try:
                from engine.logger import get_logger
                get_logger("transport_gl").error(
                    "HorizontalTransportGL initializeGL FAILED: %s",
                    exc, exc_info=True,
                )
            except Exception:
                # Logger unavailable — print to stderr as last resort.
                import sys
                print(
                    "[transport_gl] initializeGL FAILED: {0}".format(exc),
                    file=sys.stderr, flush=True,
                )

    def resizeGL(self, width: int, height: int) -> None:
        try:
            self.context().functions().glViewport(0, 0, width, height)
        except Exception:
            pass

    def paintGL(self) -> None:
        if not self.initialized or self._init_failed or self.program is None:
            return
        try:
            gl = self.context().functions()

            self.program.bind()

            gl.glUniform2f(self.u_resolution,
                           float(self.width()), float(self.height()))
            gl.glUniform1f(self.u_playing, 1.0 if self.playing else 0.0)
            gl.glUniform1f(self.u_play_hover, 1.0 if self.play_hover else 0.0)
            gl.glUniform1f(self.u_stop_hover, 1.0 if self.stop_hover else 0.0)
            gl.glUniform1f(self.u_pressed, 1.0 if self.pressed else 0.0)
            gl.glUniform1f(self.u_offset, float(self.offset))
            gl.glUniform1f(self.u_rotation, float(self.rotation))
            gl.glUniform1f(self.u_playhead, float(self.playhead))
            gl.glUniform1f(self.u_time, float(self._elapsed))

            # Bind the three icon textures to texture units 0, 1, 2.
            if self.play_texture is not None:
                self.play_texture.bind(0)
            if self.pause_texture is not None:
                self.pause_texture.bind(1)
            if self.stop_texture is not None:
                self.stop_texture.bind(2)
            # Bind the waveform peaks texture to texture unit 3.
            if self.waveform_texture is not None:
                self.waveform_texture.bind(3)
            gl.glUniform1i(self.u_play, 0)
            gl.glUniform1i(self.u_pause, 1)
            gl.glUniform1i(self.u_stop, 2)
            gl.glUniform1i(self.u_waveform, 3)

            # Draw the fullscreen quad.
            if not self.vbo.bind():
                raise RuntimeError("VBO bind failed in paintGL.")
            self.program.enableAttributeArray(0)
            self.program.setAttributeBuffer(0, GL_FLOAT, 0, 2, 0)

            # Clear to the ambient background base color — the shader
            # outputs this same color outside the island body, so the
            # transition is seamless.
            gl.glClearColor(0.047, 0.040, 0.055, 1.0)
            gl.glClear(GL_COLOR_BUFFER_BIT)
            gl.glDrawArrays(GL_TRIANGLES, 0, 6)

            self.program.disableAttributeArray(0)
            self.vbo.release()

            if self.play_texture is not None:
                self.play_texture.release()
            if self.pause_texture is not None:
                self.pause_texture.release()
            if self.stop_texture is not None:
                self.stop_texture.release()
            if self.waveform_texture is not None:
                self.waveform_texture.release()

            self.program.release()
        except Exception as exc:
            # Runtime GL error — log once, then mark as failed so we
            # don't spam the log on every frame.
            self._init_failed = True
            self.initialized = False
            try:
                from engine.logger import get_logger
                get_logger("transport_gl").error(
                    "HorizontalTransportGL paintGL FAILED: %s",
                    exc, exc_info=True,
                )
            except Exception:
                import sys
                print(
                    "[transport_gl] paintGL FAILED: {0}".format(exc),
                    file=sys.stderr, flush=True,
                )

    # ------------------------------------------------------------------
    # Animation tick — reproduces the V12 _tick() math + HTML fidelity
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        now = time.perf_counter()
        dt = max(0.0, now - self.last_tick)
        self.last_tick = now

        # Accumulate elapsed time for the pulse animation (uTime uniform).
        self._elapsed += dt

        if self.playing:
            # Advance the mock offset + rotation. The real progress bar
            # fill is driven by set_progress() (existing WaveformCanvas
            # playhead); the offset/rotation are the V12 ambient animation
            # that runs alongside the real progress while playing.
            self.target_offset -= PROGRESS_SPEED * dt
            if self.target_offset < 0.0:
                self.target_offset += CIRCUMFERENCE

            self.rotation += (2.0 * math.pi) * dt / ROTATION_SECONDS
            if self.rotation >= 2.0 * math.pi:
                self.rotation -= 2.0 * math.pi

        # ---- Stop reset easing (HTML: CSS ease, ~350ms) ----
        # The HTML reference uses `transition: stroke-dashoffset 0.35s`
        # with the default CSS `ease` curve = cubic-bezier(0.25, 0.1, 0.25, 1).
        # We approximate this with a cubic ease-out: 1 - (1-t)^3, which has a
        # similar fast-start / slow-decay profile.
        # When a stop reset is active (triggered by trigger_stop_reset()),
        # we animate from _reset_start_offset to target_offset over 350ms.
        if getattr(self, '_reset_active', False):
            elapsed_reset = now - self._reset_start_time
            if elapsed_reset >= STOP_RESET_SECONDS:
                self.offset = self.target_offset
                self._reset_active = False
            else:
                t = elapsed_reset / STOP_RESET_SECONDS
                # CSS ease approximation: cubic ease-out
                eased = 1.0 - (1.0 - t) ** 3
                self.offset = self._reset_start_offset + (
                    self.target_offset - self._reset_start_offset
                ) * eased
        else:
            # Normal smoothing for progress changes (exponential decay).
            smoothing = 1.0 - math.exp(-dt / STOP_RESET_SECONDS)
            delta = self.target_offset - self.offset
            # Progress offset is circular.
            if delta > CIRCUMFERENCE * 0.5:
                delta -= CIRCUMFERENCE
            elif delta < -CIRCUMFERENCE * 0.5:
                delta += CIRCUMFERENCE
            self.offset += delta * smoothing
            if abs(delta) < 0.02:
                self.offset = self.target_offset

        self.update()

    # ------------------------------------------------------------------
    # Public state setters (called by WaveformPlayer)
    # ------------------------------------------------------------------
    def set_playing(self, playing: bool) -> None:
        """Switch the icon (play_arrow ↔ pause) and adjust glow strength."""
        if self.playing != playing:
            self.playing = playing
            self.update()

    def set_progress(self, progress: float) -> None:
        """Drive the progress arc fill from the existing playhead fraction.

        Maps the WaveformCanvas playhead (0..1) to the V12 ``uOffset``
        uniform. The V12 shader computes the visible arc fraction as
        ``visible = clamp(1 - uOffset / C, 0, 1)``, so to make the arc
        fill proportionally to ``progress`` we set
        ``uOffset = (1 - progress) * CIRCUMFERENCE``.

        We do NOT drive the offset directly through ``self.offset`` here,
        because the V12 _tick() smoothing would fight us. Instead we set
        ``target_offset`` so the smoothing unwinds naturally to the
        requested fill over ~350 ms — which gives the same smooth visual
        transition the V12 reference uses for the Stop reset.
        """
        p = max(0.0, min(1.0, float(progress)))
        self._external_progress = p
        # Map progress 0..1 → offset CIRCUMFERENCE..0 (full circle → empty).
        # progress=0  → offset=CIRCUMFERENCE → visible=0   (empty ring)
        # progress=1  → offset=0             → visible=1   (full ring)
        self.target_offset = (1.0 - p) * CIRCUMFERENCE
        self.update()

    def set_playhead(self, playhead: float) -> None:
        """Drive the GPU playhead line inside the waveform container."""
        self.playhead = max(0.0, min(1.0, float(playhead)))
        self.update()

    def trigger_stop_reset(self) -> None:
        """Start the V12 smooth-stop: target jumps to full circle.

        Uses a CSS ease approximation (cubic ease-out, ~350ms) to match the
        HTML reference's `transition: stroke-dashoffset 0.35s` with default
        CSS `ease` curve. Records the start offset + time so _tick() can
        interpolate with the correct easing curve.
        """
        self._reset_start_offset = self.offset
        self._reset_start_time = time.perf_counter()
        self._reset_active = True
        self.target_offset = CIRCUMFERENCE
        self.update()

    # ------------------------------------------------------------------
    # Waveform peaks — real WAV data uploaded as a GPU texture
    # ------------------------------------------------------------------
    def set_waveform_peaks(self, peaks: list) -> None:
        """Upload real WAV peak data to the GPU as a 1D texture.

        The peaks (List[float] 0..1) are packed into the red channel of a
        1-pixel-tall RGBA texture. The fragment shader samples this texture
        with ``texture2D(uWaveform, vec2(x01, 0.5)).r`` to draw the bars
        inside the waveform container — exactly like the V12 reference,
        but with real data instead of the procedural mock waveform.

        This eliminates the need for a separate WaveformCanvas QWidget
        overlay, avoiding the QWidget-on-QOpenGLWidget compositing issues
        that occur on Windows (translucent child widgets get an opaque
        system background, covering the GPU-drawn container).
        """
        self._waveform_peaks = list(peaks) if peaks else []
        if self.initialized and not self._init_failed:
            self._rebuild_waveform_texture()
        self.update()

    def _rebuild_waveform_texture(self) -> None:
        """Create or update the waveform peaks QOpenGLTexture.

        Must be called with a current GL context (i.e. from initializeGL
        or paintGL, or after makeCurrent()).
        """
        # Destroy the old texture if it exists.
        if self.waveform_texture is not None:
            try:
                self.waveform_texture.destroy()
            except Exception:
                pass
            self.waveform_texture = None

        # Build a 1-pixel-tall RGBA image whose width = number of peaks.
        # Each pixel's red channel stores the normalised peak (0..255).
        # If no peaks, create a 1×1 zero texture (shader will sample 0).
        from PySide6.QtGui import QImage, QColor
        from PySide6.QtCore import QByteArray

        n = max(1, len(self._waveform_peaks))
        img = QImage(n, 1, QImage.Format.Format_RGBA8888)
        if self._waveform_peaks:
            for i, peak in enumerate(self._waveform_peaks):
                v = max(0, min(255, int(max(0.0, min(1.0, float(peak))) * 255)))
                img.setPixelColor(i, 0, QColor(v, 0, 0, 255))
        else:
            img.fill(QColor(0, 0, 0, 255))

        self.waveform_texture = QOpenGLTexture(img)
        if not self.waveform_texture.isCreated():
            raise RuntimeError("Waveform QOpenGLTexture creation failed.")
        self.waveform_texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
        self.waveform_texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
        self.waveform_texture.setWrapMode(
            QOpenGLTexture.CoordinateDirection.DirectionS,
            QOpenGLTexture.WrapMode.ClampToEdge,
        )
        self.waveform_texture.setWrapMode(
            QOpenGLTexture.CoordinateDirection.DirectionT,
            QOpenGLTexture.WrapMode.ClampToEdge,
        )

    # ------------------------------------------------------------------
    # Visibility — only tick while visible (saves GPU cycles when the
    # transport is hidden, e.g. during full-screen editor mode).
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.initialized and not self.timer.isActive():
            self.last_tick = time.perf_counter()
            self.timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self.timer.isActive():
            self.timer.stop()

    # ------------------------------------------------------------------
    # Geometry logging (for debugging / validation)
    # ------------------------------------------------------------------
    def log_geometry(self) -> None:
        """Log the current transport geometry for debugging."""
        w = float(self.width())
        h = float(self.height())
        scale_y = h / V6_REF_H
        left_margin = 20.0
        right_margin = 20.0
        volume_reserved = 186.0
        island_left = left_margin
        island_right = w - right_margin
        island_top = (V6_REF_H - 232) * 0.5 * scale_y
        island_bottom = h - island_top
        play_cx = left_margin + 120.0 * scale_y
        stop_cx = left_margin + 315.0 * scale_y
        wave_left = left_margin + 460.0 * scale_y
        island_right_ref = (w - left_margin - right_margin) / max(scale_y, 0.0001)
        wave_right_ref = island_right_ref - volume_reserved / max(scale_y, 0.0001)
        wave_right = left_margin + wave_right_ref * scale_y
        vol_x = w - right_margin - min(120, volume_reserved - 30)
        try:
            from engine.logger import get_logger
            get_logger("transport_gl").info(
                "TRANSPORT GEOMETRY: w=%d h=%d scale_y=%.4f "
                "island[L=%.0f R=%.0f T=%.1f B=%.1f] "
                "play_cx=%.1f stop_cx=%.1f "
                "wave[L=%.1f R=%.1f] vol_x=%.0f",
                int(w), int(h), scale_y,
                island_left, island_right, island_top, island_bottom,
                play_cx, stop_cx,
                wave_left, wave_right, vol_x,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Mouse handling — hit testing in the V6 reference coordinate space
    # ------------------------------------------------------------------
    def _hit_test(self, event) -> tuple:
        """Return (play_hit, stop_hit, wave_x01) for a mouse event.

        Uses the same left-anchored coordinate system as the shader:
          - origin.x = left_margin (14px)
          - scale_y = h / 300
          - Play at ref (120, 150), Stop at ref (315, 150)
          - Waveform from ref x=460 to wave_right_ref
        """
        w = float(self.width())
        h = float(self.height())
        scale_y = h / V6_REF_H
        left_margin = 20.0
        right_margin = 20.0
        volume_reserved = 186.0

        # Left-anchored origin
        ox = left_margin
        oy = 0.0
        x = (event.position().x() - ox) / max(scale_y, 0.0001)
        y = (event.position().y() - oy) / max(scale_y, 0.0001)

        # Island right edge in reference coords
        island_right_ref = (w - left_margin - right_margin) / max(scale_y, 0.0001)

        # Play and Stop at V6 reference positions (left-anchored)
        play = math.hypot(x - 120.0, y - 150.0) <= 96.0
        stop = (abs(x - 315.0) <= 40.0) and (abs(y - 150.0) <= 40.0)

        # Waveform: left=460 (V6 ref), right=island_right_ref - volume_reserved/scale_y
        wave_left = 460.0
        wave_right = island_right_ref - volume_reserved / max(scale_y, 0.0001)
        wave_top = 150.0 - 96.0    # 54
        wave_bot = 150.0 + 96.0    # 246
        if (wave_left <= x <= wave_right and wave_top <= y <= wave_bot):
            wave_x01 = (x - wave_left) / max(wave_right - wave_left, 0.0001)
        else:
            wave_x01 = -1.0

        return play, stop, wave_x01

    def mouseMoveEvent(self, event) -> None:
        play, stop, wave_x01 = self._hit_test(event)
        # Independent hover states — only the control under the cursor
        # lights up. Waveform hover (outside both controls) does NOT
        # activate either.
        if play != self.play_hover or stop != self.stop_hover:
            self.play_hover = play
            self.stop_hover = stop
            self.update()
        # Drag-to-scrub: if left button is held down and we're inside
        # the waveform, emit seek continuously.
        if (event.buttons() & Qt.MouseButton.LeftButton) and wave_x01 >= 0.0:
            self.waveform_seek_requested.emit(wave_x01)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        play, stop, wave_x01 = self._hit_test(event)
        if play:
            self.play_clicked.emit()
        elif stop:
            self.stop_clicked.emit()
            # V12 Stop behaviour: jump the target offset to full circle;
            # the _tick smoothing produces the ~350 ms visual reset.
            self.trigger_stop_reset()
        elif wave_x01 >= 0.0:
            # Click-to-seek on the waveform container.
            self.waveform_seek_requested.emit(wave_x01)
        if play or stop:
            self.pressed = True
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self.pressed:
            self.pressed = False
            self.update()

    def leaveEvent(self, event) -> None:
        if self.play_hover or self.stop_hover or self.pressed:
            self.play_hover = False
            self.stop_hover = False
            self.pressed = False
            self.update()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """Release GL resources. Called when the widget is destroyed."""
        if self.timer.isActive():
            self.timer.stop()
        if not self.initialized:
            return
        # Make the context current so GL resource destruction is valid.
        ctx = self.context()
        if ctx is not None and ctx.isValid():
            self.makeCurrent()
            try:
                if self.program is not None:
                    self.program = None
                if self.vbo is not None and self.vbo.isCreated():
                    self.vbo.destroy()
                    self.vbo = None
                for tex_attr in ("play_texture", "pause_texture",
                                 "stop_texture", "waveform_texture"):
                    tex = getattr(self, tex_attr, None)
                    if tex is not None:
                        tex.destroy()
                        setattr(self, tex_attr, None)
            finally:
                self.doneCurrent()
        self.initialized = False
