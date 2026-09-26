"""
QRhiWidget Minimal Solid-Colour Test
=====================================

Standalone diagnostic to prove that QRhiWidget itself produces visible
pixels on the target machine.

This test contains ONLY:
    - QApplication
    - QRhiWidget
    - A simple solid-colour render (magenta via clear color)

NO shaders.
NO qsb.
NO uniform buffer.
NO V5 code.
NO textures.
NO animation.

The only purpose is to prove that QRhiWidget produces visible pixels.

Usage:
    python qrhi_minimal_test.py

Expected result:
    A visible solid magenta window that remains open until closed.
    The console logs every step of the QRhiWidget lifecycle.

If the solid colour is NOT visible, the problem is QRhiWidget itself
(not the V5 shader, not SpeechStudio integration).
"""

import sys
import traceback

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QRhiDepthStencilClearValue
from PySide6.QtWidgets import QApplication, QMainWindow, QRhiWidget


def log(msg):
    """Print with flush so output appears immediately."""
    print(f"[QRHI_TEST] {msg}", flush=True)


class SolidColorRhiWidget(QRhiWidget):
    """Minimal QRhiWidget that clears to solid magenta.

    No shaders, no pipeline, no draw calls — just beginPass with a
    clear colour and immediately endPass.
    """

    def __init__(self, parent=None):
        log("QRhiWidget constructor — start")
        super().__init__(parent)

        log("setApi(OpenGL) — calling...")
        try:
            self.setApi(QRhiWidget.Api.OpenGL)
            log("setApi(OpenGL) — OK")
        except Exception as exc:
            log(f"setApi(OpenGL) — FAILED: {exc}")
            log(traceback.format_exc())
            raise

        self._render_count = 0
        self._rhi_checked = False

        log("QRhiWidget constructor — done")

    def initialize(self, cb):
        """Called by Qt when the RHI is ready."""
        log("initialize() callback — ENTER")

        # Check QRhi validity.
        try:
            rhi = self.rhi()
            if rhi is None:
                log("rhi() — returned None")
                log("initialize() — FAILED: rhi is None")
                return
            log(f"rhi() — valid, backend={rhi.backendName()}")
        except Exception as exc:
            log(f"rhi() — EXCEPTION: {exc}")
            log(traceback.format_exc())
            return

        # Check render target validity.
        try:
            rt = self.renderTarget()
            if rt is None:
                log("renderTarget() — returned None")
                log("initialize() — FAILED: render target is None")
                return
            log(f"renderTarget() — valid, type={type(rt).__name__}")
        except Exception as exc:
            log(f"renderTarget() — EXCEPTION: {exc}")
            log(traceback.format_exc())
            return

        # Check command buffer availability.
        try:
            if cb is None:
                log("command buffer — None (FAILED)")
                return
            log(f"command buffer — valid, type={type(cb).__name__}")
        except Exception as exc:
            log(f"command buffer check — EXCEPTION: {exc}")
            log(traceback.format_exc())
            return

        self._rhi_checked = True
        log("initialize() callback — EXIT (success)")

    def render(self, cb):
        """Called by Qt to render a frame."""
        self._render_count += 1

        if self._render_count <= 5:
            log(f"render() callback — ENTER (call #{self._render_count})")

        if not self._rhi_checked:
            if self._render_count <= 5:
                log(f"render() — skipping (RHI not initialized)")
            return

        try:
            # Check command buffer.
            if cb is None:
                if self._render_count <= 5:
                    log("render() — command buffer is None")
                return

            # Get render target.
            rt = self.renderTarget()
            if rt is None:
                if self._render_count <= 5:
                    log("render() — render target is None")
                return

            if self._render_count <= 5:
                log(f"render() — beginPass (clear=magenta)")

            # Begin pass with magenta clear colour.
            # No depth/stencil needed, but beginPass requires a value.
            clear_color = QColor(255, 0, 255)  # pure magenta
            depth_clear = QRhiDepthStencilClearValue()
            cb.beginPass(rt, clear_color, depth_clear)

            if self._render_count <= 5:
                log(f"render() — endPass")

            # End pass immediately — no draw calls, no pipeline.
            # The clear colour fills the entire render target.
            cb.endPass()

            if self._render_count <= 5:
                log(f"render() callback — EXIT (call #{self._render_count} OK)")

        except Exception as exc:
            log(f"render() — EXCEPTION (call #{self._render_count}): {exc}")
            log(traceback.format_exc())
            # Don't re-raise — let Qt continue.

    def releaseResources(self):
        """Called by Qt when RHI resources should be released."""
        log("releaseResources() callback — called")


def main():
    log("=" * 60)
    log("QRhiWidget Minimal Solid-Colour Test")
    log("=" * 60)
    log(f"Python: {sys.version}")
    log(f"Platform: {sys.platform}")

    # Check PySide6 version.
    try:
        import PySide6
        log(f"PySide6: {PySide6.__version__}")
    except Exception as exc:
        log(f"PySide6 import FAILED: {exc}")
        return 1

    # Check QRhiWidget availability.
    try:
        log("Checking QRhiWidget availability...")
        log(f"  QRhiWidget in QtWidgets: {hasattr(__import__('PySide6.QtWidgets', fromlist=['QRhiWidget']), 'QRhiWidget')}")
        log(f"  QRhiWidget.Api enum: { hasattr(QRhiWidget, 'Api')}")
        if hasattr(QRhiWidget, 'Api'):
            log(f"  Api.OpenGL = {QRhiWidget.Api.OpenGL}")
    except Exception as exc:
        log(f"QRhiWidget check FAILED: {exc}")
        log(traceback.format_exc())
        return 1

    # Create QApplication.
    log("Creating QApplication...")
    app = QApplication(sys.argv)
    log("QApplication created")

    # Create the QRhiWidget.
    log("Creating SolidColorRhiWidget...")
    try:
        widget = SolidColorRhiWidget()
        log("SolidColorRhiWidget created")
    except Exception as exc:
        log(f"SolidColorRhiWidget creation FAILED: {exc}")
        log(traceback.format_exc())
        return 1

    # Create a main window to host the widget.
    log("Creating QMainWindow...")
    window = QMainWindow()
    window.setWindowTitle("QRhiWidget Solid Colour Test")
    window.resize(1280, 720)
    window.setCentralWidget(widget)
    log("QMainWindow created (1280x720)")

    # Show the window.
    log("Showing window...")
    window.show()
    log("Window shown — entering event loop")
    log("Expected: solid magenta window visible")
    log("=" * 60)

    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"\nFATAL: {exc!r}")
        log(traceback.format_exc())
        raise
