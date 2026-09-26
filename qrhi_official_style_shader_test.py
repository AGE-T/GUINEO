"""
QRhiWidget Official-Style Shader Test — Cyan Triangle
=====================================================

Minimal QRhiWidget test following the official Qt QRhiWidget example
patterns as closely as possible.

Goal: prove that the QRhi graphics pipeline can render a visible cyan
triangle using QShader + qsb + QRhiGraphicsPipeline.

Key differences from the previous (failed) test:
    1. GLSL #version 440 (Vulkan-style, as in official Qt examples)
    2. QRhiBuffer.Immutable (not Dynamic) for the vertex buffer
    3. Resource update batch passed directly to beginPass() — NOT
       called separately via cb.resourceUpdate(batch) before beginPass
    4. No artificial empty ShaderResourceBindings — setShaderResources()
       called with no argument (defaults to None)
    5. Simple triangle (3 vertices), not fullscreen quad

NO uniforms. NO animation. NO V5. NO blobs. NO textures.
"""

import sys
import os
import struct
import subprocess
import tempfile
import traceback

from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import (
    QColor, QShader, QRhiShaderStage,
    QRhiVertexInputLayout, QRhiVertexInputBinding, QRhiVertexInputAttribute,
    QRhiViewport, QRhiDepthStencilClearValue, QRhiBuffer,
)
from PySide6.QtWidgets import QApplication, QMainWindow, QRhiWidget


def log(msg):
    print(f"[QRHI_SHADER] {msg}", flush=True)


# ===========================================================================
# Shaders — GLSL 440 (Vulkan-style, as in official Qt QRhiWidget examples)
# ===========================================================================
VERTEX_SHADER = """#version 440

layout(location = 0) in vec2 position;

void main()
{
    gl_Position = vec4(position, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """#version 440

layout(location = 0) out vec4 fragColor;

void main()
{
    fragColor = vec4(0.0, 1.0, 1.0, 1.0);
}
"""

# Simple triangle: 3 vertices, 2 floats each (x, y)
TRIANGLE_VERTICES = struct.pack("6f",
    0.0,  0.6,   # top
   -0.6, -0.6,   # bottom-left
    0.6, -0.6,   # bottom-right
)


def find_qsb_tool():
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


def compile_shader(qsb_path, source, stage):
    """Compile GLSL source to QShader using the qsb tool.

    The shader stage is determined by the file extension:
        .vert → vertex shader
        .frag → fragment shader
    """
    log(f"qsb {stage} compilation — start")
    try:
        ext = ".vert" if stage == "vertex" else ".frag"
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=ext, delete=False, encoding="utf-8") as f:
            f.write(source)
            glsl_path = f.name

        qsb_output = glsl_path + ".qsb"
        # qsb: outputs .qsb by default.
        # --glsl 440 = generate GLSL 4.40 (Vulkan-style, required for SPIR-V)
        cmd = [qsb_path, "--glsl", "440", "-o", qsb_output, glsl_path]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            log(f"qsb {stage} compilation — FAILED")
            log(f"  returncode: {result.returncode}")
            log(f"  stdout: {result.stdout}")
            log(f"  stderr: {result.stderr}")
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
            log(f"qsb {stage} compilation — QShader invalid")
            return None

        log(f"qsb {stage} compilation — OK ({len(qsb_data)} bytes)")
        return shader

    except Exception as exc:
        log(f"qsb {stage} compilation — EXCEPTION: {exc}")
        log(traceback.format_exc())
        return None


class TriangleRhiWidget(QRhiWidget):
    """Minimal QRhiWidget that renders a cyan triangle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setApi(QRhiWidget.Api.OpenGL)

        self._rhi = None
        self._pipeline = None
        self._vbo = None
        self._target = None
        self._rp_desc = None
        self._initialized = False
        self._render_count = 0

    def initialize(self, cb):
        """Called by Qt when the RHI is ready."""
        log("initialize — start")

        try:
            self._rhi = self.rhi()
            if self._rhi is None:
                log("initialize — FAILED: rhi is None")
                return
            log("QRhi valid — backend=" + self._rhi.backendName())

            self._target = self.renderTarget()
            if self._target is None:
                log("initialize — FAILED: render target is None")
                return
            log("render target valid")

            self._rp_desc = self._target.renderPassDescriptor()
            if self._rp_desc is None:
                log("initialize — FAILED: render pass descriptor is None")
                return

            # Compile shaders.
            qsb_path = find_qsb_tool()
            if qsb_path is None:
                log("initialize — FAILED: qsb tool not found")
                return

            vs = compile_shader(qsb_path, VERTEX_SHADER, "vertex")
            if vs is None:
                log("initialize — FAILED: vertex shader")
                return
            log("QShader vertex valid")

            fs = compile_shader(qsb_path, FRAGMENT_SHADER, "fragment")
            if fs is None:
                log("initialize — FAILED: fragment shader")
                return
            log("QShader fragment valid")

            # Vertex buffer — Immutable + VertexBuffer (as in official example).
            log("pipeline creation — start")
            self._vbo = self._rhi.newBuffer(
                QRhiBuffer.Type.Immutable,
                QRhiBuffer.UsageFlag.VertexBuffer,
                len(TRIANGLE_VERTICES),
            )
            if not self._vbo.create():
                log("initialize — FAILED: VBO create")
                return

            # Upload vertex data via resource update batch.
            # IMPORTANT: pass this batch to beginPass(), NOT cb.resourceUpdate().
            self._initial_batch = self._rhi.nextResourceUpdateBatch()
            self._initial_batch.uploadStaticBuffer(self._vbo, QByteArray(TRIANGLE_VERTICES))

            # Graphics pipeline.
            self._pipeline = self._rhi.newGraphicsPipeline()
            self._pipeline.setShaderStages([
                QRhiShaderStage(QRhiShaderStage.Type.Vertex, vs),
                QRhiShaderStage(QRhiShaderStage.Type.Fragment, fs),
            ])

            # Vertex input layout: 1 binding, 1 attribute.
            input_layout = QRhiVertexInputLayout()
            input_layout.setBindings([
                QRhiVertexInputBinding(2 * 4)  # stride: 2 floats * 4 bytes
            ])
            input_layout.setAttributes([
                # binding=0, location=0, format=Float2, offset=0
                QRhiVertexInputAttribute(0, 0, QRhiVertexInputAttribute.Format.Float2, 0)
            ])
            self._pipeline.setVertexInputLayout(input_layout)

            # ShaderResourceBindings — REQUIRED even when empty (no uniforms).
            # The pipeline cannot be created without it.
            self._srb = self._rhi.newShaderResourceBindings()
            if not self._srb.create():
                log("initialize — FAILED: ShaderResourceBindings create")
                return
            self._pipeline.setShaderResourceBindings(self._srb)

            self._pipeline.setRenderPassDescriptor(self._rp_desc)

            if not self._pipeline.create():
                log("initialize — FAILED: pipeline create")
                return
            log("pipeline creation — OK")

            self._initialized = True
            log("initialize — EXIT (success)")

        except Exception as exc:
            log(f"initialize — EXCEPTION: {exc}")
            log(traceback.format_exc())

    def render(self, cb):
        """Called by Qt to render a frame."""
        self._render_count += 1

        if self._render_count <= 3:
            log(f"render — start (call #{self._render_count})")

        if not self._initialized:
            return

        try:
            # For the FIRST render, pass the initial resource update batch
            # (with vertex data upload) directly to beginPass().
            # For subsequent renders, no resource updates needed.
            batch = None
            if self._render_count == 1 and hasattr(self, "_initial_batch"):
                batch = self._initial_batch
                self._initial_batch = None

            # beginPass with clear color (dark blue) + resource updates.
            clear_color = QColor(10, 10, 40)  # dark blue background
            depth_clear = QRhiDepthStencilClearValue()
            cb.beginPass(self._target, clear_color, depth_clear, batch)

            cb.setGraphicsPipeline(self._pipeline)
            # setShaderResources with the (empty) shader resource bindings.
            cb.setShaderResources(self._srb)

            # Viewport.
            w = max(1, self.width())
            h = max(1, self.height())
            cb.setViewport(QRhiViewport(0.0, 0.0, float(w), float(h)))

            # Vertex input + draw.
            cb.setVertexInput(0, [(self._vbo, 0)])
            cb.draw(3)

            cb.endPass()

            if self._render_count <= 3:
                log(f"draw complete (call #{self._render_count})")

        except Exception as exc:
            log(f"render — EXCEPTION (call #{self._render_count}): {exc}")
            log(traceback.format_exc())

    def releaseResources(self):
        log("releaseResources — called")
        for res in (self._pipeline, self._vbo):
            if res is not None:
                try:
                    res.destroy()
                except Exception:
                    pass


def main():
    log("=" * 60)
    log("QRhiWidget Official-Style Shader Test — Cyan Triangle")
    log("=" * 60)

    app = QApplication(sys.argv)

    widget = TriangleRhiWidget()

    window = QMainWindow()
    window.setWindowTitle("QRhi Shader Test — Cyan Triangle")
    window.resize(1280, 720)
    window.setCentralWidget(widget)
    window.show()

    log("Window shown — expected: cyan triangle on dark blue background")
    log("=" * 60)

    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"\nFATAL: {exc!r}")
        log(traceback.format_exc())
        raise
