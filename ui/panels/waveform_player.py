"""
SpeechStudio — Production Bottom Transport (Reference V6 Vol2x).

This module implements the approved reference transport
(``bottom_transport_reference_test_v6_vol2x.py``) as the production
bottom transport bar, connected to real SpeechStudio audio data and
playback functionality.

Architecture:
    WaveformPlayer (transparent QWidget)
        ↓
    TransportIsland (QFrame, floating rounded surface)
        ├── GlowButton (Play/Pause, 68px, QPainter + QGraphicsDropShadowEffect)
        ├── GlowButton (Stop, 34px, QPainter + QGraphicsDropShadowEffect)
        ├── RealWaveform (QWidget, QPainter, real WAV peaks)
        └── VolumeControl (QWidget, 248×46, enlarged)

The old GPU shader transport (transport_gl.py) is NO LONGER the production
transport. This QPainter-based implementation renders directly at final
display size — no oversized reference downscaling.

Public API (preserved for MainWindow compatibility):
    Signals: play_requested, pause_requested, stop_requested,
             seek_requested(float seconds), volume_changed(float 0..1)
    Slots:   set_audio_info, set_play_position, set_playback_state,
             set_volume, get_volume, clear
    Property: current_path
"""

from __future__ import annotations
import math
import os
import time
import wave
from typing import List

import numpy as np

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPainterPath, QBrush, QPen, QPolygonF,
)
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QSlider, QSizePolicy, QVBoxLayout, QWidget,
)


# ============================================================
# V12 animation constants (from the approved reference)
# ============================================================
RING_RADIUS_REF = 88.0
CIRCUMFERENCE = 2.0 * math.pi * RING_RADIUS_REF
START_OFFSET = 200.0
PROGRESS_SPEED = 40.0
STOP_RESET_SECONDS = 0.35
ROTATION_SECONDS = 4.0


# ============================================================
# GlowButton — Play/Pause and Stop (from reference, verbatim)
# ============================================================
class GlowButton(QWidget):
    """Play/Pause or Stop button rendered at final display size.

    Play: 68×68, circular core, purple border, progress ring, icon.
    Stop: 34×34, rounded square, purple border, stop icon.

    Uses QGraphicsDropShadowEffect for the purple glow halo.
    QPainter + Antialiasing for the button body, ring, and icon.
    """

    toggledSignal = Signal(bool)
    stopSignal = Signal()

    def __init__(self, play: bool, parent=None):
        super().__init__(parent)

        self._play = play
        self._playing = False
        self._hovered = False
        self._pressed = False

        # V12 transport state.
        self._offset = START_OFFSET
        self._rotation = 0.0

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            True,
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        if play:
            self.setFixedSize(68, 68)
        else:
            self.setFixedSize(34, 34)

        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setOffset(0.0, 0.0)

        if play:
            self._shadow.setBlurRadius(22.0)
            self._shadow.setColor(QColor(168, 85, 247, 105))
        else:
            self._shadow.setBlurRadius(16.0)
            self._shadow.setColor(QColor(168, 85, 247, 125))

        self.setGraphicsEffect(self._shadow)

    def set_transport_state(
        self,
        playing: bool,
        offset: float,
        rotation: float,
    ):
        self._playing = bool(playing)
        self._offset = float(offset) % CIRCUMFERENCE
        self._rotation = float(rotation) % (2.0 * math.pi)

        if self._play:
            self._shadow.setBlurRadius(
                28.0 if self._playing else 22.0
            )

        self.update()

    def set_playing(self, active: bool):
        self._playing = bool(active)

        if self._play:
            self._shadow.setBlurRadius(
                28.0 if self._playing else 22.0
            )

        self.update()

    def set_progress(self, value: float):
        value = max(0.0, min(1.0, value))
        self._offset = CIRCUMFERENCE * (1.0 - value)
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self._shadow.setBlurRadius(
            32.0 if self._play else 20.0
        )
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        if not self._pressed:
            self._shadow.setBlurRadius(
                28.0 if self._playing and self._play else
                22.0 if self._play else
                16.0
            )
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        self._pressed = True

        if self._play:
            self._playing = not self._playing
            self._shadow.setBlurRadius(35.0)
            self.toggledSignal.emit(self._playing)
        else:
            self._shadow.setBlurRadius(24.0)
            self.stopSignal.emit()

        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._pressed = False

        if self._play:
            self._shadow.setBlurRadius(
                32.0 if self._hovered else
                28.0 if self._playing else
                22.0
            )
        else:
            self._shadow.setBlurRadius(
                20.0 if self._hovered else 16.0
            )

        self.update()
        super().mouseReleaseEvent(event)

    def _play_radius(self) -> float:
        return 30.0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        cx = self.width() * 0.5
        cy = self.height() * 0.5

        if self._play:
            base_radius = self._play_radius()

            scale = (
                0.95 if self._pressed else
                1.03 if self._hovered else
                1.0
            )

            radius = base_radius * scale

            # Core.
            painter.setPen(
                QPen(
                    QColor("#A855F7"),
                    2.0,
                )
            )
            painter.setBrush(
                QBrush(
                    QColor("#1C1B1B"),
                )
            )

            painter.drawEllipse(
                QPointF(cx, cy),
                radius,
                radius,
            )

            # Inner ambient edge.
            if self._playing:
                inner_pen = QPen(
                    QColor(168, 85, 247, 75),
                    1.5,
                )
                painter.setPen(inner_pen)
                painter.drawEllipse(
                    QPointF(cx, cy),
                    radius - 4.0,
                    radius - 4.0,
                )

            # Progress track.
            ring_radius = radius - 3.0

            painter.setPen(
                QPen(
                    QColor(70, 66, 78, 190),
                    4.0,
                    Qt.PenStyle.SolidLine,
                )
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)

            ring_rect = QRectF(
                cx - ring_radius,
                cy - ring_radius,
                ring_radius * 2,
                ring_radius * 2,
            )

            painter.drawEllipse(ring_rect)

            # V12 active progress model.
            visible = max(
                0.0,
                min(
                    1.0,
                    1.0 - self._offset / CIRCUMFERENCE,
                ),
            )

            if visible > 0.0:
                progress_pen = QPen(
                    QColor("#D0BCFF"),
                    5.0,
                    Qt.PenStyle.SolidLine,
                )
                progress_pen.setCapStyle(
                    Qt.PenCapStyle.RoundCap
                )
                painter.setPen(progress_pen)

                rotation_deg = math.degrees(
                    self._rotation
                )

                start_deg = 90.0 - rotation_deg
                span_deg = -360.0 * visible

                painter.drawArc(
                    ring_rect,
                    int(round(start_deg * 16.0)),
                    int(round(span_deg * 16.0)),
                )

                # Explicit end cap.
                end_deg = start_deg + span_deg
                theta = math.radians(end_deg)

                cap_x = cx + ring_radius * math.cos(theta)
                cap_y = cy - ring_radius * math.sin(theta)

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(
                    QBrush(
                        QColor("#D0BCFF")
                    )
                )

                cap_r = 2.5

                painter.drawEllipse(
                    QPointF(
                        cap_x,
                        cap_y,
                    ),
                    cap_r,
                    cap_r,
                )

            # Icon.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                QBrush(
                    QColor("#D0BCFF")
                )
            )

            if self._playing:
                painter.drawRoundedRect(
                    QRectF(
                        cx - 9.0,
                        cy - 11.0,
                        7.0,
                        22.0,
                    ),
                    1.0,
                    1.0,
                )
                painter.drawRoundedRect(
                    QRectF(
                        cx + 2.0,
                        cy - 11.0,
                        7.0,
                        22.0,
                    ),
                    1.0,
                    1.0,
                )
            else:
                painter.drawPolygon(
                    QPolygonF(
                        [
                            QPointF(cx - 9.0, cy - 14.0),
                            QPointF(cx - 9.0, cy + 14.0),
                            QPointF(cx + 15.0, cy),
                        ]
                    )
                )

        else:
            # Stop button.
            size = 30.0

            scale = (
                0.90 if self._pressed else
                1.03 if self._hovered else
                1.0
            )

            size *= scale

            radius = 8.0

            path = QPainterPath()
            path.addRoundedRect(
                QRectF(
                    cx - size * 0.5,
                    cy - size * 0.5,
                    size,
                    size,
                ),
                radius,
                radius,
            )

            painter.setPen(
                QPen(
                    QColor("#A855F7"),
                    1.5,
                )
            )
            painter.setBrush(
                QBrush(
                    QColor("#1C1B1B")
                )
            )
            painter.drawPath(path)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                QBrush(
                    QColor("#D0BCFF")
                )
            )

            stop_size = 10.0 * scale

            painter.drawRoundedRect(
                QRectF(
                    cx - stop_size * 0.5,
                    cy - stop_size * 0.5,
                    stop_size,
                    stop_size,
                ),
                1.5,
                1.5,
            )

        painter.end()


# ============================================================
# RealWaveform — reference visual, real WAV data
# ============================================================
class RealWaveform(QWidget):
    """Waveform widget with the reference visual style but real audio data.

    Replaces the reference's MockWaveform. The visual rendering (container,
    bars, center line, playhead, active coloring) is taken verbatim from
    the reference. The data source is real WAV peak data from the existing
    SpeechStudio audio pipeline.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self._playing = False
        self._progress = 0.0
        self._phase = 0.0
        self._peaks: List[float] = []
        self._duration = 0.0
        self._hover_x = -1.0

        self.setMinimumWidth(180)
        self.setMinimumHeight(68)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_peaks(self, peaks: List[float]) -> None:
        self._peaks = list(peaks) if peaks else []
        self.update()

    def set_duration(self, duration: float) -> None:
        self._duration = duration

    def set_state(self, playing: bool, progress: float) -> None:
        self._playing = playing
        self._progress = max(0.0, min(1.0, progress))
        self.update()

    def advance(self, dt: float) -> None:
        self._phase += dt
        self.update()

    def _load_wav_peaks(self, path: str) -> bool:
        """Load WAV file and extract peaks (existing SpeechStudio logic)."""
        if not path or not os.path.isfile(path):
            self._peaks = []
            self._duration = 0.0
            self.update()
            return False

        try:
            with wave.open(path, 'rb') as wf:
                n_frames = wf.getnframes()
                n_channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()
                self._duration = n_frames / sample_rate if sample_rate > 0 else 0.0
                raw = wf.readframes(n_frames)

            if sample_width == 1:
                samples = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
                samples = (samples - 128) / 128.0
            elif sample_width == 2:
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                samples = samples / 32768.0
            elif sample_width == 4:
                try:
                    samples = np.frombuffer(raw, dtype=np.float32)
                except Exception:
                    samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32)
                    samples = samples / 2147483648.0
            else:
                import soundfile as sf
                samples, sr = sf.read(path, dtype='float32')
                self._duration = len(samples) / sr if sr > 0 else 0.0

            if n_channels > 1 and len(samples) % n_channels == 0:
                samples = samples.reshape(-1, n_channels).mean(axis=1)

            target_peaks = 256
            chunk_size = max(1, len(samples) // target_peaks)
            n_chunks = len(samples) // chunk_size
            if n_chunks > 0:
                reshaped = samples[:n_chunks * chunk_size].reshape(n_chunks, chunk_size)
                self._peaks = np.abs(reshaped).max(axis=1).tolist()
            else:
                self._peaks = [abs(s) for s in samples[:target_peaks]]

            max_peak = max(self._peaks) if self._peaks else 1.0
            if max_peak > 0:
                self._peaks = [p / max_peak for p in self._peaks]

            self.update()
            return True

        except Exception:
            try:
                import soundfile as sf
                samples, sr = sf.read(path, dtype='float32')
                self._duration = len(samples) / sr if sr > 0 else 0.0
                if len(samples.shape) > 1:
                    samples = samples.mean(axis=1)

                target_peaks = 256
                chunk_size = max(1, len(samples) // target_peaks)
                n_chunks = len(samples) // chunk_size
                if n_chunks > 0:
                    reshaped = samples[:n_chunks * chunk_size].reshape(n_chunks, chunk_size)
                    self._peaks = np.abs(reshaped).max(axis=1).tolist()
                else:
                    self._peaks = [abs(s) for s in samples[:target_peaks]]

                max_peak = max(self._peaks) if self._peaks else 1.0
                if max_peak > 0:
                    self._peaks = [p / max_peak for p in self._peaks]

                self.update()
                return True
            except Exception:
                self._peaks = []
                self._duration = 0.0
                self.update()
                return False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        # Waveform container.
        painter.setPen(QPen(QColor(75, 68, 92, 130), 1.0))
        painter.setBrush(QBrush(QColor(14, 14, 17, 205)))
        painter.drawRoundedRect(rect, 10.0, 10.0)

        # Subtle center line.
        center_y = rect.center().y()
        painter.setPen(QPen(QColor(100, 90, 120, 75), 1.0))
        painter.drawLine(
            QPointF(rect.left() + 8, center_y),
            QPointF(rect.right() - 8, center_y),
        )

        left = rect.left() + 12.0
        right = rect.right() - 12.0

        if not self._peaks:
            painter.setPen(QColor(160, 160, 160))
            painter.setFont(QFont("Segoe UI", 9))
            text = "{0:.1f}s".format(self._duration) if self._duration > 0 else "No audio"
            painter.drawText(self.rect(), Qt.Alignment.AlignCenter, text)
            painter.end()
            return

        # Draw real waveform peaks as a filled path.
        n_peaks = len(self._peaks)
        step = (right - left) / max(n_peaks - 1, 1)
        max_h = 22.0

        points_top = []
        points_bottom = []

        for i, peak in enumerate(self._peaks):
            x = left + i * step
            amount = peak * max_h

            if self._playing:
                amount *= (
                    0.92 + 0.08 * math.sin(self._phase * 4.0 + i * 0.3)
                )

            points_top.append(QPointF(x, center_y - amount))
            points_bottom.append(QPointF(x, center_y + amount))

        path = QPainterPath()
        if points_top:
            path.moveTo(points_top[0])
            for p in points_top[1:]:
                path.lineTo(p)
            for p in reversed(points_bottom):
                path.lineTo(p)
            path.closeSubpath()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(105, 94, 126, 145)))
        painter.drawPath(path)

        # Active waveform coloring.
        if self._playing or self._progress > 0:
            active_x = left + (right - left) * self._progress
            active_width = 18.0
            active_rect = QRectF(
                active_x - active_width, rect.top(),
                active_width * 2.0, rect.height(),
            )
            painter.save()
            painter.setClipRect(active_rect)
            painter.setBrush(QBrush(QColor(208, 188, 255, 105)))
            painter.drawPath(path)
            painter.restore()

        # Playhead.
        playhead_x = left + (right - left) * self._progress
        painter.setPen(QPen(QColor("#D0BCFF"), 1.0))
        painter.drawLine(
            QPointF(playhead_x, rect.top() + 6),
            QPointF(playhead_x, rect.bottom() - 6),
        )

        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._duration > 0:
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            left = rect.left() + 12.0
            right = rect.right() - 12.0
            pos = (event.position().x() - left) / max(right - left, 0.0001)
            pos = max(0.0, min(1.0, pos))
            parent = self.parent()
            while parent is not None:
                if isinstance(parent, WaveformPlayer):
                    parent._on_waveform_seek(pos)
                    break
                parent = parent.parent()

    def mouseMoveEvent(self, event):
        if self._duration > 0:
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            left = rect.left() + 12.0
            right = rect.right() - 12.0
            self._hover_x = event.position().x()
            if event.buttons() & Qt.MouseButton.LeftButton:
                pos = max(0.0, min(1.0, (event.position().x() - left) / max(right - left, 0.0001)))
                parent = self.parent()
                while parent is not None:
                    if isinstance(parent, WaveformPlayer):
                        parent._on_waveform_seek(pos)
                        break
                    parent = parent.parent()
            self.update()

    def leaveEvent(self, event):
        self._hover_x = -1.0
        self.update()


# ============================================================
# VolumeControl — enlarged V6 reference (248×46)
# ============================================================
class VolumeControl(QWidget):
    """Volume control matching the reference V6 enlarged size (248×46)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(248)
        self.setFixedHeight(46)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._label = QLabel("Vol")
        self._label.setStyleSheet(
            "color: rgba(240,240,255,220);"
            "background: transparent;"
            "font-size: 16px;"
            "font-weight: 500;"
        )
        layout.addWidget(self._label)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(82)
        # P3.34 FIX (clipped volume handle): QSlider's vertical size
        # policy is FIXED, so the layout gave the widget only its 15px
        # size-hint — while the styled handle (16px wide, 17px tall via
        # the -5px groove margins) needs 17px of vertical room. The
        # handle rect was y=-1..16 inside a 15px widget: the circle was
        # clipped at the top AND bottom (subControlRect proof). Give the
        # slider explicit vertical geometry (28px): the 7px groove and
        # the 17px handle both center inside it with equal ~5.5px
        # clearance — the intended 16px handle size and style unchanged.
        self._slider.setFixedHeight(28)
        self._slider.setStyleSheet("""
            QSlider { background: transparent; }
            QSlider::groove:horizontal {
                height: 7px;
                background: rgba(100, 90, 120, 145);
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #D0BCFF;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 16px;
                margin: -5px 0;
                border-radius: 8px;
                background: #D0BCFF;
            }
        """)
        layout.addWidget(self._slider, 1)

    @property
    def slider(self) -> QSlider:
        return self._slider


# ============================================================
# TransportIsland — floating rounded surface (from reference)
# ============================================================
class TransportIsland(QFrame):
    """Floating transport island matching the reference V6 composition."""

    def __init__(self):
        super().__init__()
        self.setObjectName("TransportIsland")
        self.setMinimumHeight(96)
        self.setMaximumHeight(96)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            True,
        )
        self.setStyleSheet("""
            QFrame#TransportIsland {
                background: rgba(15, 15, 15, 225);
                border: 1px solid rgba(168, 85, 247, 100);
                border-radius: 18px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 6, 22, 6)
        layout.setSpacing(20)

        self.play = GlowButton(play=True, parent=self)
        self.stop = GlowButton(play=False, parent=self)
        self.waveform = RealWaveform(parent=self)
        self.volume = VolumeControl(parent=self)

        layout.addWidget(self.play, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.stop, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.waveform, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.volume, 0, Qt.AlignmentFlag.AlignVCenter)


# ============================================================
# WaveformPlayer — production transport (preserves public API)
# ============================================================
class WaveformPlayer(QWidget):
    """Production bottom transport using the approved reference V6 design.

    Public API preserved for MainWindow compatibility:
        Signals: play_requested, pause_requested, stop_requested,
                 seek_requested(float), volume_changed(float)
        Slots:   set_audio_info, set_play_position, set_playback_state,
                 set_volume, get_volume, clear
        Property: current_path
    """

    play_requested = Signal()
    pause_requested = Signal()
    stop_requested = Signal()
    seek_requested = Signal(float)
    volume_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path = ""
        self._playback_state = "stopped"  # stopped | playing | paused | ended
        self._duration = 0.0
        self._position_sec = 0.0  # real audio position (seconds, from engine)
        self._last_engine_pos = 0.0  # last position received from engine
        self._last_engine_time = 0.0  # perf_counter when last engine pos arrived
        self._visual_pos = 0.0  # smoothed visual position for the playhead

        # V12 visual animation state (COSMETIC ONLY — does NOT drive playback).
        # Rotation is a cosmetic visual effect while playing.
        # Progress offset is driven by REAL audio position via set_play_position().
        self._rotation = 0.0
        self._offset = CIRCUMFERENCE  # FULL CIRCLE = idle/100% progress
        self._target_offset = CIRCUMFERENCE
        self._last_tick = time.perf_counter()

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(0)

        self._island = TransportIsland()
        layout.addWidget(self._island)

        # Wire signals.
        self._island.play.toggledSignal.connect(self._on_play_pause_toggled)
        self._island.stop.stopSignal.connect(self._on_stop_clicked)
        self._island.volume.slider.valueChanged.connect(
            lambda v: self.volume_changed.emit(v / 100.0)
        )

        # Animation timer (COSMETIC ONLY — glow + rotation refresh).
        # Does NOT advance playback position. Real audio position comes
        # from set_play_position() called by the audio engine.
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ------------------------------------------------------------------
    # Internal: Play/Pause/Stop/Waveform handlers
    # ------------------------------------------------------------------
    def _on_play_pause_toggled(self, playing: bool):
        """GlowButton toggled — emit the appropriate signal.

        The GlowButton manages its own local _playing toggle for immediate
        visual feedback, but the AUTHORITATIVE state is set by
        set_playback_state() which is called by MainWindow after the audio
        engine confirms the state change.
        """
        if playing:
            self.play_requested.emit()
        else:
            self.pause_requested.emit()

    def _on_stop_clicked(self):
        """Stop button clicked — emit signal. MainWindow will call
        set_playback_state('stopped') after the audio engine confirms."""
        self.stop_requested.emit()

    def _on_waveform_seek(self, pos_fraction: float):
        """User clicked/dragged the waveform — emit seek.

        The visual playhead updates immediately for responsiveness. The
        real audio position will be confirmed by set_play_position()
        once the audio engine processes the seek.
        """
        pos = max(0.0, min(1.0, float(pos_fraction)))
        # Update visual playhead immediately (snap, no smoothing).
        self._position_sec = pos * self._duration if self._duration > 0 else 0.0
        self._visual_pos = self._position_sec
        self._island.waveform.set_state(
            self._playback_state == "playing", pos,
        )
        if self._duration > 0:
            self.seek_requested.emit(pos * self._duration)

    def _tick(self):
        """COSMETIC animation tick — smooth playhead + glow + rotation.

        The real audio position arrives via set_play_position() at ~50 Hz.
        We smooth the visual playhead toward the real position using an
        exponential filter with a short time constant (~40ms). This kills
        micro-jitter from engine timing variance while staying responsive.
        """
        now = time.perf_counter()
        dt = max(0.0, min(0.05, now - self._last_tick))
        self._last_tick = now

        is_playing = (self._playback_state == "playing")

        # 1. Compute the "ideal" position from engine data + interpolation.
        if is_playing and self._duration > 0:
            elapsed_since_engine = now - self._last_engine_time
            if elapsed_since_engine < 0.2:
                ideal_pos = self._last_engine_pos + elapsed_since_engine
                ideal_pos = min(ideal_pos, self._duration)
                self._position_sec = ideal_pos
            # else: keep last _position_sec (engine stalled, don't drift)
        elif self._playback_state in ("stopped", "ended"):
            self._visual_pos = 0.0

        # 2. Smooth the visual playhead toward the ideal position.
        #    Time constant ~40ms: fast enough to feel real-time, slow
        #    enough to kill 1-frame jitter from engine callback timing.
        SMOOTH_TAU = 0.04
        alpha = 1.0 - math.exp(-dt / SMOOTH_TAU)
        target_visual = self._position_sec if self._duration > 0 else 0.0

        # When seeking (large jump), snap immediately (no smoothing) so
        # the playhead jumps to the clicked position without delay.
        seek_threshold = self._duration * 0.1 if self._duration > 0 else 1.0
        if abs(target_visual - self._visual_pos) > seek_threshold:
            self._visual_pos = target_visual
        else:
            self._visual_pos += (target_visual - self._visual_pos) * alpha

        # 3. Drive the V12 progress offset from the smoothed visual position.
        if self._duration > 0:
            pos_norm = self._visual_pos / self._duration
            self._target_offset = CIRCUMFERENCE * (1.0 - pos_norm)

        # 4. Cosmetic rotation (only while actually playing).
        if is_playing:
            self._rotation += 2.0 * math.pi * dt / ROTATION_SECONDS
            if self._rotation >= 2.0 * math.pi:
                self._rotation -= 2.0 * math.pi

        # 5. Smooth offset toward target (for stop reset + seek visual).
        smoothing = 1.0 - math.exp(-dt / STOP_RESET_SECONDS)
        delta = self._target_offset - self._offset
        if delta > CIRCUMFERENCE * 0.5:
            delta -= CIRCUMFERENCE
        elif delta < -CIRCUMFERENCE * 0.5:
            delta += CIRCUMFERENCE
        self._offset += delta * smoothing
        if abs(delta) < 0.02:
            self._offset = self._target_offset

        # 6. Update Play button visual state.
        self._island.play.set_transport_state(
            is_playing, self._offset, self._rotation,
        )

        # 7. Update waveform visual state from the SMOOTHED position.
        normalized_pos = (
            self._visual_pos / self._duration
            if self._duration > 0 else 0.0
        )
        self._island.waveform.set_state(is_playing, normalized_pos)
        self._island.waveform.advance(dt)

    # ------------------------------------------------------------------
    # Public API (preserved for MainWindow)
    # ------------------------------------------------------------------
    def set_audio_info(self, duration: float, sample_rate: int,
                       output_path: str = "") -> None:
        """Load new audio. Resets transport to IDLE state."""
        self._duration = duration
        self._current_path = output_path
        self._position_sec = 0.0
        if output_path:
            self._island.waveform.set_duration(duration)
            self._island.waveform._load_wav_peaks(output_path)
        else:
            self._island.waveform.set_peaks([])
            self._island.waveform.set_duration(0.0)
        # Reset to idle: full progress ring, playhead at 0.
        self._offset = CIRCUMFERENCE
        self._target_offset = CIRCUMFERENCE
        self._rotation = 0.0
        self._position_sec = 0.0
        self._visual_pos = 0.0
        self._playback_state = "stopped"
        self._island.play.set_playing(False)

    def set_play_position(self, position_sec: float) -> None:
        """Update playhead from REAL audio engine position.

        Called by the audio engine's position callback (~50 Hz).
        This is the AUTHORITATIVE source of playback position.
        """
        self._position_sec = max(0.0, position_sec)
        self._last_engine_pos = self._position_sec
        self._last_engine_time = time.perf_counter()

    def set_playback_state(self, state: str) -> None:
        """Set transport state from the AUTHORITATIVE audio engine.

        Args:
            state: "stopped", "playing", "paused", or "ended".

        The GlowButton's local _playing toggle is overridden here
        to match the real audio state.
        """
        self._playback_state = state
        is_playing = (state == "playing")
        # Override the GlowButton's local toggle with the real state.
        self._island.play._playing = is_playing
        self._island.play.set_transport_state(
            is_playing, self._offset, self._rotation,
        )

        if state == "stopped":
            # Reset to idle: full ring, playhead at 0.
            self._position_sec = 0.0
            self._visual_pos = 0.0
            self._target_offset = CIRCUMFERENCE
            self._rotation = 0.0
        elif state == "ended":
            # Audio finished naturally: reset to idle.
            self._position_sec = 0.0
            self._visual_pos = 0.0
            self._target_offset = CIRCUMFERENCE
            self._rotation = 0.0
            self._island.play._playing = False
            self._island.play.set_transport_state(
                False, self._offset, self._rotation,
            )
        # "paused" — keep current position, stop rotation, keep ring.

    @property
    def current_path(self) -> str:
        return self._current_path

    def set_volume(self, volume: float) -> None:
        v = int(max(0.0, min(1.0, float(volume))) * 100)
        self._island.volume.slider.blockSignals(True)
        self._island.volume.slider.setValue(v)
        self._island.volume.slider.blockSignals(False)

    def get_volume(self) -> float:
        return self._island.volume.slider.value() / 100.0

    def clear(self) -> None:
        self._current_path = ""
        self._duration = 0.0
        self._position_sec = 0.0
        self._visual_pos = 0.0
        self._offset = CIRCUMFERENCE
        self._target_offset = CIRCUMFERENCE
        self._rotation = 0.0
        self._playback_state = "stopped"
        self._island.waveform.set_peaks([])
        self._island.waveform.set_duration(0.0)
        self._island.play.set_playing(False)
