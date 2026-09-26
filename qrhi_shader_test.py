"""
QRhiWidget Shader Solid-Colour Test
====================================

Standalone diagnostic to prove that the QRhi graphics pipeline works
end-to-end with a trivial shader.

This test contains:
    - QApplication
    - QRhiWidget (OpenGL backend)
    - One trivial vertex shader (full-screen quad)
    - One trivial fragment shader (constant solid colour — cyan)
    - A graphics pipeline + VBO + draw call

NO uniforms (the colour is hardcoded in the shader).
NO animation.
NO V5 shader.
NO noise.
NO blobs.
NO qsb tool — shaders are compiled from GLSL source inline.

The only purpose is to prove that a shader-based render through QRhi
produces visible pixels.

Usage:
    python qrhi_shader_test.py

Expected result:
    A visible solid cyan window that remains open until closed.
    The console logs every step of the pipeline creation + render.

If the cyan colour is NOT visible, the problem is in the shader
compilation / pipeline creation / draw call — NOT QRhiWidget itself
(which was proven to work in qrhi_minimal_test.py).
"""

import sys
import os
import subprocess
import tempfile
import traceback

from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import (
    QColor, QShader, QRhiShaderStage,
    QRhiVertexInputLayout, QRhiVertexInputBinding, QRhiVertexInputAttribute,
    QRhiViewport, QRhiDepthStencilClearValue,
    QRhiBuffer,
)
from PySide6.QtWidgets import QApplication, QMainWindow, QRhiWidget


def log(msg):
    print(f"[QRHI_SHADER_TEST] {msg}", flush=True)


# ===========================================================================
# Trivial shaders
# ===========================================================================
# Desktop GLSL 330 core — required for qsb/SPIR-V compilation.
# ES 1.00 (version 100) does NOT work with qsb because SPIR-V requires
# ES 310+ or desktop GLSL 330+. We use desktop GLSL 330 with modern
# syntax: `in` instead of `attribute`, `out vec4 fragColor` instead of
# `gl_FragColor`.

VERTEX_SHADER = """#version 330 core
layout(location = 0) in vec2 p;
void main() {
    gl_Position = vec4(p, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """#version 330 core
layout(location = 0) out vec4 fragColor;
void main() {
    fragColor = vec4(0.0, 1.0, 1.0, 1.0);  // solid cyan
}
"""

# Full-screen quad vertices (two triangles).
import struct
QUAD_VERTICES = struct.pack("12f",
    -1.0, -1.0,
     1.0, -1.0,
    -1.0,  1.0,
    -1.0,  1.0,
     1.0, -1.0,
     1.0,  1.0,
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
    The qsb tool reads this extension to know what stage to compile.
    Using .glsl causes qsb to default to vertex (which breaks fragment shaders).
    """
    log(f"  Compiling {stage} shader with qsb...")
    try:
        # Use the correct file extension so qsb identifies the shader stage.
        # .vert for vertex, .frag for fragment — this is the ONLY way qsb
        # knows the stage. Without it, it defaults to vertex and fragment
        # shaders fail to compile.
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
        cmd = [qsb_path, "--glsl", "330", "-o", qsb_output, glsl_path]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            log(f"  qsb FAILED (returncode={result.returncode}):")
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
            log(f"  QShader invalid after deserialization")
            return None

        log(f"  {stage} shader compiled OK ({len(qsb_data)} bytes)")
        return shader

    except Exception as exc:
        log(f"  qsb compilation EXCEPTION: {exc}")
        log(traceback.format_exc())
        return None


class ShaderColorRhiWidget(QRhiWidget):
    """QRhiWidget that renders a trivial shader to produce solid cyan."""

    def __init__(self, parent=None):
        log("QRhiWidget constructor — start")
        super().__init__(parent)

        log("setApi(OpenGL) — calling...")
        self.setApi(QRhiWidget.Api.OpenGL)
        log("setApi(OpenGL) — OK")

        self._rhi = None
        self._pipeline = None
        self._srb = None
        self._vbo = None
        self._target = None
        self._rp_desc = None
        self._initialized = False
        self._render_count = 0

        log("QRhiWidget constructor — done")

    def initialize(self, cb):
        """Called by Qt when the RHI is ready."""
        log("initialize() callback — ENTER")

        try:
            self._rhi = self.rhi()
            if self._rhi is None:
                log("rhi() — None (FAILED)")
                return
            log(f"rhi() — valid, backend={self._rhi.backendName()}")

            rt = self.renderTarget()
            if rt is None:
                log("renderTarget() — None (FAILED)")
                return
            log(f"renderTarget() — valid")

            self._rp_desc = rt.renderPassDescriptor()
            if self._rp_desc is None:
                log("renderPassDescriptor() — None (FAILED)")
                return
            log(f"renderPassDescriptor() — valid")
            self._target = rt

            # Find qsb tool.
            qsb_path = find_qsb_tool()
            if qsb_path is None:
                log("qsb tool NOT FOUND (FAILED)")
                return
            log(f"qsb tool: {qsb_path}")

            # Compile shaders.
            vs = compile_shader(qsb_path, VERTEX_SHADER, "vertex")
            fs = compile_shader(qsb_path, FRAGMENT_SHADER, "fragment")
            if vs is None or fs is None:
                log("Shader compilation FAILED")
                return

            # Create vertex buffer.
            # Use proper enum types — PySide6 rejects raw ints.
            log("Creating VBO...")
            self._vbo = self._rhi.newBuffer(
                QRhiBuffer.Type.Dynamic,
                QRhiBuffer.UsageFlag.VertexBuffer,
                len(QUAD_VERTICES),
            )
            if not self._vbo.create():
                log("VBO create FAILED")
                return
            log("VBO created OK")

            # Upload vertex data.
            log("Uploading vertex data...")
            batch = self._rhi.nextResourceUpdateBatch()
            batch.uploadStaticBuffer(self._vbo, QByteArray(QUAD_VERTICES))
            cb.resourceUpdate(batch)
            log("Vertex data uploaded")

            # Shader resource bindings (empty — no uniforms).
            log("Creating shader resource bindings...")
            self._srb = self._rhi.newShaderResourceBindings()
            if not self._srb.create():
                log("Shader resource bindings create FAILED")
                return
            log("Shader resource bindings created OK")

            # Graphics pipeline.
            log("Creating graphics pipeline...")
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

            log("Creating pipeline (create())...")
            if not self._pipeline.create():
                log("Pipeline create FAILED")
                return
            log("Pipeline created OK")

            self._initialized = True
            log("initialize() callback — EXIT (success)")

        except Exception as exc:
            log(f"initialize() — EXCEPTION: {exc}")
            log(traceback.format_exc())

    def render(self, cb):
        """Called by Qt to render a frame."""
        self._render_count += 1

        if self._render_count <= 5:
            log(f"render() — ENTER (call #{self._render_count})")

        if not self._initialized:
            if self._render_count <= 5:
                log(f"render() — not initialized, skipping")
            return

        try:
            if cb is None:
                log("render() — command buffer is None")
                return

            # Begin pass (clear to black — the shader will overdraw with cyan).
            clear_color = QColor(0, 0, 0)
            depth_clear = QRhiDepthStencilClearValue()
            cb.beginPass(self._target, clear_color, depth_clear)

            if self._render_count <= 5:
                log(f"render() — setGraphicsPipeline")

            cb.setGraphicsPipeline(self._pipeline)
            cb.setShaderResources(self._srb)

            # Set viewport.
            w = max(1, self.width())
            h = max(1, self.height())
            cb.setViewport(QRhiViewport(0.0, 0.0, float(w), float(h)))

            if self._render_count <= 5:
                log(f"render() — setVertexInput + draw(6)")

            # Bind vertex buffer and draw.
            cb.setVertexInput(0, [(self._vbo, 0)])
            cb.draw(6)

            cb.endPass()

            if self._render_count <= 5:
                log(f"render() — EXIT (call #{self._render_count} OK)")

        except Exception as exc:
            log(f"render() — EXCEPTION (call #{self._render_count}): {exc}")
            log(traceback.format_exc())

    def releaseResources(self):
        log("releaseResources() — called")
        for res in (self._pipeline, self._srb, self._vbo):
            if res is not None:
                try:
                    res.destroy()
                except Exception:
                    pass


def main():
    log("=" * 60)
    log("QRhiWidget Shader Solid-Colour Test")
    log("=" * 60)

    app = QApplication(sys.argv)

    log("Creating ShaderColorRhiWidget...")
    widget = ShaderColorRhiWidget()

    log("Creating QMainWindow (1280x720)...")
    window = QMainWindow()
    window.setWindowTitle("QRhiWidget Shader Colour Test — should be CYAN")
    window.resize(1280, 720)
    window.setCentralWidget(widget)

    log("Showing window...")
    window.show()
    log("Window shown — expected: solid CYAN window")
    log("=" * 60)

    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"\nFATAL: {exc!r}")
        log(traceback.format_exc())
        raise
