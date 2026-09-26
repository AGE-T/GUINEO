"""
SpeechStudio — QRhiWidget Ambient Background (V5 Shader)
========================================================

GPU-accelerated ambient background using ``QRhiWidget``. This is the
correct Qt 6 integration path for GPU rendering inside a normal QWidget
hierarchy — unlike ``QOpenGLWindow + createWindowContainer`` (which
creates an opaque native window on top of everything) or
``QOpenGLWidget`` (which deadlocks on Windows).

``QRhiWidget`` behaves as a normal ``QWidget`` regarding stacking and
clipping, while rendering through the GPU via Qt's RHI abstraction.

Architecture
------------
- Subclasses ``QRhiWidget`` (available since Qt 6.7, PySide6 6.7+).
- Uses the V5 refined shader (5 blobs: violet, indigo, cyan, amber, magenta).
- Shaders are compiled at runtime from GLSL to .qsb using the ``qsb``
  tool shipped with PySide6. The .qsb format is loaded via
  ``QShader.fromSerialized()``.
- Uniforms (uTime, uResolution) are passed via a uniform buffer.
- No ``QOpenGLShaderProgram.setUniformValue()`` — uses QRhi's own
  resource binding + uniform buffer update API.

CPU Fallback
------------
If QRhi initialization fails (e.g. no GPU, driver issue, qsb tool
missing), the factory falls back to ``AmbientBackgroundCPUStatic``
(single static pixmap, no animation).
"""

from __future__ import annotations
import os
import struct
import subprocess
import sys
import tempfile
import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QByteArray
from PySide6.QtGui import (
    QShader, QRhiShaderStage, QRhiShaderResourceBinding,
    QRhiVertexInputLayout, QRhiVertexInputBinding, QRhiVertexInputAttribute,
    QRhiViewport, QRhiDepthStencilClearValue,
    QRhiBuffer,
)
from PySide6.QtWidgets import QRhiWidget, QWidget

import logging
_log = logging.getLogger("ambient_rhi")


# ===========================================================================
# V5 REFINED SHADER (from ambient_gpu_v5_refined.py, adapted for desktop GLSL 330)
# ===========================================================================
# 5 slow organic blobs: violet, indigo, cyan, amber, magenta.
# Slow independent movement, slow colour transitions, soft vignette.
# No aggressive bloom, no heavy noise.
#
# Desktop GLSL 330 core — required for qsb/SPIR-V compilation.
# ES 1.00 does NOT work with qsb because SPIR-V requires ES 310+ or
# desktop GLSL 330+. We use `in` instead of `attribute` and `out vec4
# fragColor` instead of `gl_FragColor`.

_VERTEX_SHADER_GLSL = """#version 330 core
layout(location = 0) in vec2 p;
void main() {
    gl_Position = vec4(p, 0.0, 1.0);
}
"""

_FRAGMENT_SHADER_GLSL = """#version 330 core
layout(location = 0) out vec4 fragColor;

uniform float uTime;
uniform vec2 uResolution;

float blob(vec2 uv, vec2 center, float radius) {
    float d = distance(uv, center);
    return exp(-(d * d) / (radius * radius));
}

void main() {
    float t = uTime;
    vec2 uv = gl_FragCoord.xy / uResolution.xy;
    float aspect = uResolution.x / uResolution.y;
    vec2 p = uv;
    p.x *= aspect;

    vec2 c1 = vec2(0.24 * aspect + 0.20 * sin(t * 0.17), 0.48 + 0.18 * cos(t * 0.23));
    vec2 c2 = vec2(0.73 * aspect + 0.17 * cos(t * 0.13 + 1.4), 0.40 + 0.23 * sin(t * 0.19));
    vec2 c3 = vec2(0.47 * aspect + 0.25 * sin(t * 0.095 + 2.0), 0.72 + 0.14 * cos(t * 0.16));
    vec2 c4 = vec2(0.58 * aspect + 0.19 * cos(t * 0.12 + 3.7), 0.26 + 0.19 * sin(t * 0.21 + 0.8));
    vec2 c5 = vec2(0.36 * aspect + 0.14 * sin(t * 0.11 + 4.5), 0.34 + 0.22 * cos(t * 0.15 + 2.2));

    float b1 = blob(p, c1, 0.27);
    float b2 = blob(p, c2, 0.29);
    float b3 = blob(p, c3, 0.32);
    float b4 = blob(p, c4, 0.25);
    float b5 = blob(p, c5, 0.22);

    float phaseA = 0.5 + 0.5 * sin(t * 0.060);
    float phaseB = 0.5 + 0.5 * sin(t * 0.043 + 2.0);

    vec3 violet = vec3(0.43, 0.07, 0.95);
    vec3 indigo = vec3(0.10, 0.18, 0.90);
    vec3 cyan   = vec3(0.04, 0.58, 0.92);
    vec3 amber  = vec3(0.95, 0.27, 0.05);
    vec3 magenta = vec3(0.78, 0.08, 0.58);

    vec3 col = vec3(0.0065, 0.0080, 0.016);
    col += mix(violet, cyan, phaseA) * b1 * 0.72;
    col += mix(indigo, amber, phaseB) * b2 * 0.60;
    col += mix(cyan, violet, phaseB) * b3 * 0.52;
    col += mix(amber, magenta, phaseA) * b4 * 0.46;
    col += mix(magenta, indigo, phaseB) * b5 * 0.42;

    float wave = 0.5 + 0.5 * sin(uv.x * 4.7 + uv.y * 3.4 + t * 0.075);
    col += vec3(0.006, 0.010, 0.019) * wave;

    float drift = 0.5 + 0.5 * sin(uv.x * 1.7 - uv.y * 2.3 + t * 0.055);
    col *= 0.94 + drift * 0.08;

    vec2 q = uv - 0.5;
    float vignette = 1.0 - 0.32 * dot(q, q);
    col *= vignette;

    fragColor = vec4(max(col, vec3(0.0)), 1.0);
}
"""

# Full-screen quad vertices (two triangles).
_QUAD_VERTICES = struct.pack("12f",
    -1.0, -1.0,
     1.0, -1.0,
    -1.0,  1.0,
    -1.0,  1.0,
     1.0, -1.0,
     1.0,  1.0,
)

# Uniform buffer layout (std140):
#   offset 0:  float uTime      (4 bytes + 12 padding = 16)
#   offset 16: vec2 uResolution (8 bytes + 8 padding = 16)
#   Total: 32 bytes
_UNIFORM_BUFFER_SIZE = 32


def _find_qsb_tool() -> Optional[str]:
    """Find the qsb shader tool shipped with PySide6."""
    try:
        import PySide6
        pyside_dir = os.path.dirname(PySide6.__file__)
        qsb_name = "qsb.exe" if sys.platform == "win32" else "qsb"
        qsb_path = os.path.join(pyside_dir, qsb_name)
        if os.path.isfile(qsb_path):
            return qsb_path
    except Exception:
        pass
    return None


def _compile_shader_with_qsb(qsb_path: str, source: str, stage: str) -> Optional[QShader]:
    """Compile GLSL source to a QShader using the qsb tool.

    The shader stage is determined by the file extension:
        .vert → vertex shader
        .frag → fragment shader
    The qsb tool reads this extension to know what stage to compile.
    Using .glsl causes qsb to default to vertex (which breaks fragment shaders).
    """
    try:
        # Use the correct file extension so qsb identifies the shader stage.
        ext = ".vert" if stage == "vertex" else ".frag"
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=ext, delete=False, encoding="utf-8") as f:
            f.write(source)
            glsl_path = f.name

        qsb_output = glsl_path + ".qsb"
        # qsb tool: outputs .qsb by default. No --qsb flag needed.
        # --glsl 330 = generate desktop GLSL 3.30 (required for SPIR-V)
        # -o = output file
        # The stage is auto-detected from the input file extension (.vert/.frag)
        cmd = [
            qsb_path,
            "--glsl", "330",
            "-o", qsb_output,
            glsl_path,
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            _log.error("qsb compilation failed (stage=%s): %s",
                       stage, result.stderr)
            return None

        with open(qsb_output, "rb") as f:
            qsb_data = f.read()

        try:
            os.unlink(glsl_path)
            os.unlink(qsb_output)
        except OSError:
            pass

        shader = QShader.fromSerialized(QByteArray(qsb_data))
        if not shader.isValid():
            _log.error("QShader invalid after deserialization (stage=%s)", stage)
            return None

        _log.info("qsb compiled %s shader OK", stage)
        return shader

    except subprocess.TimeoutExpired:
        _log.error("qsb timed out (stage=%s)", stage)
    except Exception as exc:
        _log.error("qsb compilation error (stage=%s): %s", stage, exc,
                   exc_info=True)
    return None


# ===========================================================================
# QRhiWidget subclass
# ===========================================================================
class AmbientBackgroundRhi(QRhiWidget):
    """GPU ambient background using QRhiWidget + V5 shader.

    Behaves as a normal QWidget regarding stacking/clipping, while
    rendering the V5 ambient effect through the GPU.

    Exposes the same API as the CPU fallback:
        - ``set_theme_active(active: bool)``
        - ``pause()``
        - ``resume()``
        - ``stop()``
        - ``is_running() -> bool``
    """

    TIMER_INTERVAL_MS: int = 16  # ~60 FPS

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Request OpenGL backend (most compatible on Windows + NVIDIA).
        try:
            self.setApi(QRhiWidget.Api.OpenGL)
        except Exception:
            pass

        # QRhi resources (created in initialize(), released in releaseResources()).
        self._rhi = None
        self._pipeline = None
        self._srb = None
        self._ubuf = None
        self._vbo = None
        self._target = None
        self._rp_desc = None
        self._gl_initialized = False
        self._init_failed = False

        # Animation state.
        self._started = time.perf_counter()

        # Timer state — dual-gating.
        self._theme_active: bool = False
        self._explicitly_paused: bool = False

        # Animation timer — drives repaint at 60 FPS.
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self.TIMER_INTERVAL_MS)
        self._timer.timeout.connect(self.update)

    # ------------------------------------------------------------------
    # QRhiWidget lifecycle
    # ------------------------------------------------------------------
    def initialize(self, cb) -> None:
        """Called by Qt when the RHI is ready. Create pipeline + buffers."""
        _log.info("QRhiWidget initialize() called")
        try:
            self._rhi = self.rhi()
            if self._rhi is None:
                raise RuntimeError("QRhi is None")

            _log.info("QRhi backend: %s", self._rhi.backendName())

            # Compile shaders using the qsb tool.
            qsb_path = _find_qsb_tool()
            if qsb_path is None:
                raise RuntimeError("qsb tool not found in PySide6")

            vs = _compile_shader_with_qsb(qsb_path, _VERTEX_SHADER_GLSL, "vertex")
            fs = _compile_shader_with_qsb(qsb_path, _FRAGMENT_SHADER_GLSL, "fragment")
            if vs is None or fs is None:
                raise RuntimeError("Shader compilation failed")

            # Vertex buffer (Dynamic type, VertexBuffer usage).
            self._vbo = self._rhi.newBuffer(
                QRhiBuffer.Type.Dynamic,
                QRhiBuffer.UsageFlag.VertexBuffer,
                len(_QUAD_VERTICES),
            )
            if not self._vbo.create():
                raise RuntimeError("VBO create failed")

            # Uniform buffer (Dynamic type, UniformBuffer usage).
            self._ubuf = self._rhi.newBuffer(
                QRhiBuffer.Type.Dynamic,
                QRhiBuffer.UsageFlag.UniformBuffer,
                _UNIFORM_BUFFER_SIZE,
            )
            if not self._ubuf.create():
                raise RuntimeError("Uniform buffer create failed")

            # Upload vertex data.
            batch = self._rhi.nextResourceUpdateBatch()
            batch.uploadStaticBuffer(self._vbo, QByteArray(_QUAD_VERTICES))

            # Shader resource bindings — uniform buffer at binding 0.
            self._srb = self._rhi.newShaderResourceBindings()
            binding = QRhiShaderResourceBinding.uniformBuffer(
                0,                                              # binding point
                QRhiShaderResourceBinding.StageFlag.FragmentStage,  # stage
                self._ubuf,
            )
            self._srb.setBindings([binding])
            if not self._srb.create():
                raise RuntimeError("Shader resource bindings create failed")

            # Render target + pass descriptor.
            self._target = self.renderTarget()
            if self._target is None:
                raise RuntimeError("Render target is None")
            self._rp_desc = self._target.renderPassDescriptor()
            if self._rp_desc is None:
                raise RuntimeError("Render pass descriptor is None")

            # Graphics pipeline.
            self._pipeline = self._rhi.newGraphicsPipeline()
            self._pipeline.setShaderStages([
                QRhiShaderStage(QRhiShaderStage.Type.Vertex, vs),
                QRhiShaderStage(QRhiShaderStage.Type.Fragment, fs),
            ])

            # Vertex input layout: 2 floats per vertex.
            input_layout = QRhiVertexInputLayout()
            input_layout.setBindings([
                QRhiVertexInputBinding(2 * 4)  # stride: 2 floats * 4 bytes
            ])
            input_layout.setAttributes([
                # binding=0, location=0, format=Float2, offset=0
                QRhiVertexInputAttribute(0, 0, QRhiVertexInputAttribute.Format.Float2, 0)
            ])
            self._pipeline.setVertexInputLayout(input_layout)
            self._pipeline.setShaderResourceBindings(self._srb)
            self._pipeline.setRenderPassDescriptor(self._rp_desc)

            if not self._pipeline.create():
                raise RuntimeError("Pipeline create failed")

            # Execute the resource update batch (upload vertex data).
            cb.resourceUpdate(batch)

            self._gl_initialized = True
            _log.info("QRhiWidget pipeline created successfully")
        except Exception as exc:
            _log.error("QRhiWidget initialize FAILED: %s", exc, exc_info=True)
            self._gl_initialized = False
            self._init_failed = True

    def releaseResources(self) -> None:
        """Called by Qt when RHI resources should be released."""
        _log.info("QRhiWidget releaseResources() called")
        for res in (self._pipeline, self._srb, self._ubuf, self._vbo):
            if res is not None:
                try:
                    res.destroy()
                except Exception:
                    pass
        self._pipeline = None
        self._srb = None
        self._ubuf = None
        self._vbo = None
        self._rp_desc = None
        self._target = None
        self._gl_initialized = False

    def render(self, cb) -> None:
        """Called by Qt to render a frame."""
        if self._init_failed or not self._gl_initialized:
            return
        if (self._pipeline is None or self._rhi is None
                or self._ubuf is None or self._vbo is None
                or self._srb is None or self._target is None):
            return

        try:
            elapsed = time.perf_counter() - self._started
            width = max(1, self.width())
            height = max(1, self.height())

            # Update uniform buffer: std140 layout
            # offset 0:  float uTime (4 bytes + 12 padding)
            # offset 16: vec2 uResolution (8 bytes + 8 padding)
            # Total: 32 bytes
            uniform_data = (
                struct.pack("<f", float(elapsed)) +           # uTime (4 bytes)
                b"\x00" * 12 +                                 # padding (12 bytes)
                struct.pack("<ff", float(width), float(height)) +  # uResolution (8 bytes)
                b"\x00" * 8                                     # padding (8 bytes)
            )

            batch = self._rhi.nextResourceUpdateBatch()
            batch.updateDynamicBuffer(self._ubuf, 0, QByteArray(uniform_data))
            cb.resourceUpdate(batch)

            # Begin pass (clear to black).
            cb.beginPass(self._target, Qt.GlobalColor.black,
                         QRhiDepthStencilClearValue())

            cb.setGraphicsPipeline(self._pipeline)
            cb.setShaderResources(self._srb)
            cb.setViewport(QRhiViewport(0.0, 0.0, float(width), float(height)))

            # Bind vertex buffer and draw.
            # setVertexInput(startBinding, [(buffer, offset), ...])
            cb.setVertexInput(0, [(self._vbo, 0)])
            cb.draw(6)

            cb.endPass()
        except Exception as exc:
            _log.error("QRhiWidget render error: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Timer state management (same API as CPU fallback)
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
        self.releaseResources()
