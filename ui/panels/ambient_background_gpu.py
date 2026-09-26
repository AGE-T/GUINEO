"""
SpeechStudio — Ambient Background Factory
=========================================

Selects the best available ambient background renderer at runtime.

Hierarchy:
    1. GPU V5 (QOpenGLWidget + raw GL uniforms)
       ↓ if GPU initialization fails
    2. CPU Static Ambient (single QPixmap render, no animation)
       ↓ if static ambient also fails
    3. Plain dark background

The factory logs the selected renderer:
    "Ambient renderer: GPU V5"
    "Ambient renderer: CPU Static Fallback"
    "Ambient renderer: Plain Background"

All renderers expose the same API:
    - set_theme_active(active: bool)
    - pause()
    - resume()
    - stop()
    - is_running() -> bool
"""

from __future__ import annotations
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QWidget

_log = logging.getLogger("ambient_factory")


class _PlainBackground(QWidget):
    """Plain dark background — last-resort fallback."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(2, 2, 6))
        self.setPalette(pal)

    def set_theme_active(self, active: bool) -> None:
        self.setVisible(active)

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def is_running(self) -> bool:
        return False

    def stop(self) -> None:
        pass


def create_ambient_background(parent=None) -> QWidget:
    """Create the best available ambient background widget.

    Tries GPU V5 first, falls back to CPU Static, then to Plain.
    The application NEVER fails to start because the ambient renderer
    failed — every fallback is a valid QWidget.
    """
    # 1. Try GPU V5 (QOpenGLWidget + raw GL uniforms).
    try:
        from ui.panels.ambient_gpu_v5 import AmbientBackgroundGPUV5
        widget = AmbientBackgroundGPUV5(parent=parent)
        _log.info("Ambient renderer: GPU V5")
        return widget
    except Exception as exc:
        _log.warning("GPU V5 renderer failed to create: %s", exc, exc_info=True)

    # 2. CPU Static fallback.
    try:
        from ui.panels.ambient_cpu_static import AmbientBackgroundCPUStatic
        widget = AmbientBackgroundCPUStatic(parent=parent)
        _log.info("Ambient renderer: CPU Static Fallback")
        return widget
    except Exception as exc:
        _log.warning("CPU Static renderer failed: %s", exc, exc_info=True)

    # 3. Plain background — last resort.
    _log.info("Ambient renderer: Plain Background")
    return _PlainBackground(parent=parent)
