"""
SpeechStudio — GPU Ambient Background V5 (QOpenGLWidget)
========================================================

GPU-accelerated ambient background using QOpenGLWidget + raw OpenGL
uniform uploads. Based directly on the proven working standalone
reference implementation (ambient_gpu_v5_refined.py).

Architecture
------------
- Subclasses QOpenGLWidget (from PySide6.QtOpenGLWidgets)
- Uses the V5 refined shader: 5 organic blobs (violet, indigo, cyan,
  amber, magenta) with slow independent movement and slow colour
  transitions, soft vignette.
- Uses RAW OpenGL calls for uniforms (glUseProgram, glUniform1f,
  glUniform2f, glDrawArrays) — NOT QOpenGLShaderProgram.setUniformValue()
  which caused GL_INVALID_OPERATION errors.
- QSurfaceFormat 4.6 CompatibilityProfile must be set globally in
  SpeechStudio.py before QApplication is created.

Widget stacking
---------------
The QOpenGLWidget is created as a child of the root/central widget and
lower()'d to the back. The normal QWidget UI is above it. This works
without WA_TranslucentBackground on the MainWindow.

API
---
Same as the CPU fallback:
    - set_theme_active(active: bool)
    - pause()
    - resume()
    - stop()
    - is_running() -> bool
"""

from __future__ import annotations
import ctypes
import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtOpenGL import (
        QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram,
    )
    _OPENGL_AVAILABLE = True
except ImportError:
    _OPENGL_AVAILABLE = False
    QOpenGLWidget = None  # type: ignore

import logging
_log = logging.getLogger("ambient_gpu_v5")


# OpenGL constants (from the reference implementation).
GL_COLOR_BUFFER_BIT = 0x00004000
GL_TRIANGLES = 0x0004
GL_FLOAT = 0x1406


# ===========================================================================
# AMBIENT SHADER — 2-blob reference (purple + orange)
# ===========================================================================
# Direct port of the user's provided reference HTML shader.
# Two large, bright, slowly-moving glow fields on a dark charcoal background.
# Purple (#8B5CF6) and Orange (#F97316) — the SpeechStudio brand colors.
# This is intentionally simpler and more visually striking than the V5
# 5-blob version. The blobs are larger, brighter, and more colorful.

_VERTEX_SHADER = """
attribute vec2 p;

void main()
{
    gl_Position = vec4(p, 0.0, 1.0);
}
"""

_FRAGMENT_SHADER = """
uniform float uTime;
uniform vec2 uResolution;

void main()
{
    vec2 uv = gl_FragCoord.xy / uResolution.xy;

    // Dark charcoal background
    vec3 bg = vec3(0.08, 0.08, 0.1);

    // Colors — bright, saturated brand colors
    vec3 purple = vec3(0.545, 0.361, 0.965); // #8B5CF6
    vec3 orange = vec3(0.976, 0.451, 0.086); // #F97316

    // Very slow movement
    float t = uTime * 0.2;

    // Centers for glows — large, slow Lissajous movement
    vec2 pCenter = vec2(0.5 + 0.4 * sin(t), 0.5 + 0.3 * cos(t * 0.8));
    vec2 oCenter = vec2(0.5 + 0.3 * cos(t * 0.9), 0.4 + 0.4 * sin(t * 1.1));

    // Calculate distances
    float pDist = distance(uv, pCenter);
    float oDist = distance(uv, oCenter);

    // Subtle, bright glows — exp(-d * 2.0) * 0.7
    float pGlow = exp(-pDist * 2.0) * 0.7;
    float oGlow = exp(-oDist * 2.0) * 0.7;

    // Bottom lighting effect
    float bottomLight = smoothstep(0.5, 0.0, uv.y) * 0.2;

    // Combine colors
    vec3 color = bg + purple * pGlow + orange * oGlow + vec3(0.15) * bottomLight;

    gl_FragColor = vec4(color, 1.0);
}
"""

# Full-screen quad vertices (6 vertices = 2 triangles, from the reference).
_QUAD_VERTICES = (
    -1.0, -1.0,
     1.0, -1.0,
    -1.0,  1.0,
    -1.0,  1.0,
     1.0, -1.0,
     1.0,  1.0,
)


# ===========================================================================
# QOpenGLWidget subclass
# ===========================================================================
class AmbientBackgroundGPUV5(QOpenGLWidget if _OPENGL_AVAILABLE else QWidget):
    """GPU ambient background using QOpenGLWidget + V5 shader.

    Uses raw OpenGL calls for uniforms (NOT setUniformValue).
    Follows the proven standalone reference implementation.
    """

    TIMER_INTERVAL_MS: int = 16  # ~60 FPS

    def __init__(self, parent=None):
        if not _OPENGL_AVAILABLE:
            raise RuntimeError(
                "QOpenGLWidget is not available. "
                "Install PySide6 with OpenGL support.")

        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # OpenGL state — initialized in initializeGL (called lazily by Qt).
        self._program: Optional[QOpenGLShaderProgram] = None
        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._program_id = 0
        self._time_location = -1
        self._resolution_location = -1
        self._gl_initialized = False
        self._init_failed = False

        # Animation clock.
        self._started = time.perf_counter()

        # Timer state — dual-gating.
        self._theme_active: bool = False
        self._explicitly_paused: bool = False

        # Animation timer — PreciseTimer at ~60 FPS.
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self.TIMER_INTERVAL_MS)
        self._timer.timeout.connect(self.update)

    # ------------------------------------------------------------------
    # OpenGL lifecycle (called by Qt, NOT by us)
    # ------------------------------------------------------------------
    def initializeGL(self) -> None:
        """Called once by Qt when the OpenGL context is first created."""
        _log.info("initializeGL — start")
        try:
            ctx = self.context()
            if not ctx or not ctx.isValid():
                raise RuntimeError("Invalid OpenGL context")

            fmt = ctx.format()
            _log.info("OpenGL %d.%d profile=%s",
                      fmt.majorVersion(), fmt.minorVersion(), fmt.profile())

            self._program = QOpenGLShaderProgram(self)

            if not self._program.addShaderFromSourceCode(
                    QOpenGLShader.ShaderTypeBit.Vertex, _VERTEX_SHADER):
                raise RuntimeError("Vertex shader: " + self._program.log())

            if not self._program.addShaderFromSourceCode(
                    QOpenGLShader.ShaderTypeBit.Fragment, _FRAGMENT_SHADER):
                raise RuntimeError("Fragment shader: " + self._program.log())

            self._program.bindAttributeLocation("p", 0)

            if not self._program.link():
                raise RuntimeError("Link: " + self._program.log())

            self._program_id = self._program.programId()
            self._time_location = self._program.uniformLocation("uTime")
            self._resolution_location = self._program.uniformLocation("uResolution")

            _log.info("program=%d uTime=%d uResolution=%d",
                      self._program_id, self._time_location,
                      self._resolution_location)

            if self._program_id <= 0:
                raise RuntimeError("Invalid shader program")
            if self._time_location < 0:
                raise RuntimeError("uTime not found")
            if self._resolution_location < 0:
                raise RuntimeError("uResolution not found")

            if not self._vbo.create():
                raise RuntimeError("VBO create failed")
            if not self._vbo.bind():
                raise RuntimeError("VBO bind failed")

            data = (ctypes.c_float * len(_QUAD_VERTICES))(*_QUAD_VERTICES)
            self._vbo.allocate(data, ctypes.sizeof(data))
            self._vbo.release()

            self._gl_initialized = True
            _log.info("GPU V5 ambient initialized — success")
        except Exception as exc:
            _log.error("GPU V5 initializeGL FAILED: %s", exc, exc_info=True)
            self._gl_initialized = False
            self._init_failed = True

    def resizeGL(self, width: int, height: int) -> None:
        """Called by Qt when the widget is resized."""
        try:
            self.context().functions().glViewport(0, 0, width, height)
        except Exception:
            pass

    def paintGL(self) -> None:
        """Called by Qt to render a frame.

        Uses RAW OpenGL calls for uniforms (NOT setUniformValue).
        Follows the proven standalone reference paintGL sequence.
        """
        if self._init_failed or not self._gl_initialized:
            return
        if self._program is None:
            return

        try:
            functions = self.context().functions()
            elapsed = time.perf_counter() - self._started
            width = max(1, self.width())
            height = max(1, self.height())

            # RAW OpenGL calls — do NOT use setUniformValue.
            functions.glUseProgram(self._program_id)
            functions.glUniform1f(self._time_location, float(elapsed))
            functions.glUniform2f(self._resolution_location,
                                  float(width), float(height))

            if not self._vbo.bind():
                raise RuntimeError("VBO bind failed in paintGL")

            self._program.enableAttributeArray(0)
            self._program.setAttributeBuffer(0, GL_FLOAT, 0, 2, 0)

            functions.glClearColor(0.004, 0.006, 0.012, 1.0)
            functions.glClear(GL_COLOR_BUFFER_BIT)
            functions.glDrawArrays(GL_TRIANGLES, 0, 6)

            self._program.disableAttributeArray(0)
            self._vbo.release()
        except Exception as exc:
            _log.error("GPU V5 paintGL error: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Timer state management
    # ------------------------------------------------------------------
    def _update_timer_state(self) -> None:
        should_run = (self._theme_active
                      and not self._explicitly_paused
                      and self._gl_initialized)
        if should_run and not self._timer.isActive():
            self._timer.start()
        elif not should_run and self._timer.isActive():
            self._timer.stop()

    def set_theme_active(self, active: bool) -> None:
        self._theme_active = active
        self.setVisible(active)
        self._update_timer_state()

    def pause(self) -> None:
        self._explicitly_paused = True
        self._update_timer_state()

    def resume(self) -> None:
        self._explicitly_paused = False
        self._update_timer_state()

    def is_running(self) -> bool:
        return self._timer.isActive()

    def stop(self) -> None:
        self._timer.stop()
        if self._vbo is not None and self._vbo.isCreated():
            try:
                self._vbo.destroy()
            except Exception:
                pass
        if self._program is not None:
            try:
                self._program.removeAllShaders()
            except Exception:
                pass
