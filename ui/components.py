"""
SpeechStudio — Reusable Visual Components
=========================================

A library of high-level, design-token-driven widgets built on top of
``ui.design_tokens`` and ``ui.icon_registry``. Every component pulls
its colours, spacing, radii, typography, and elevation rules from the
centralised token modules — NO values are hardcoded.

Components exposed here are the visual vocabulary shared across all
SpeechStudio panels. They map 1:1 to the HTML reference design system
(``stitch_orange_ambient_desktop_prototype/…/DESIGN.md`` and the
``speechstudio_main_window_fixed_layout_architecture/code.html``
Tailwind config):

    1. GradientButton(QPushButton)
           Orange CTA gradient + bold white text + optional Material
           Symbols icon. Reserved for "Main Function" actions:
           Generate, Generate All, New Project, Start Batch.
    2. IconButton(QPushButton)
           Transparent square icon-only button with hover border accent.
           Used in toolbars, sidebars, and as inline row actions.
    3. StatusPill(QWidget)
           Small pill showing a labelled status (Saved / Generating /
           Done / Queued) with a colour-coded tint.
    4. SectionHeader(QWidget)
           Uppercase label-caps title row with an optional right-aligned
           action button. Used at the top of every right-panel section.
    5. RoundedPanel(QFrame)
           The standard card / panel surface — bg_surface background,
           1px border_subtle, Radii.panel corners, optional title.
    6. VoiceCard(QWidget)
           The voice selector card used in the right control panel's
           VOICE tab — avatar + name + metadata + Change Voice + More.
    7. EmotionGridButton(QWidget)
           Square emotion tile with a Material Symbols icon and label.
           Emits ``clicked`` on press and supports a selected glow.
    8. SegmentedControl(QWidget)
           Horizontal option selector (Pace / Pitch / Delivery / Length)
           with a single selected segment. Emits ``segment_changed``.

All components:
    * use ``ui.design_tokens`` (Colors / Spacing / Radii / Typography /
      Surfaces / Elevation / ComponentDims) for every visual value
    * use ``ui.icon_registry.IconRegistry`` for every Material Symbols
      icon (no inline SVG strings)
    * set ``WA_StyledBackground`` on custom QWidgets that rely on a
      stylesheet background
    * handle disabled state explicitly (50% opacity or text_disabled
      colour as specified per component)
    * are self-contained — they do not touch the Engine or any panel

Usage:
    from ui.components import (
        GradientButton, IconButton, StatusPill, SectionHeader,
        RoundedPanel, VoiceCard, EmotionGridButton, SegmentedControl,
    )

    btn = GradientButton("Generate All", icon_name="play_arrow")
    pill = StatusPill("Saved", status="success")
    header = SectionHeader("VOICE", action_label="More")

Compile-time check:
    python3 -m py_compile ui/components.py
"""

from __future__ import annotations

import os
from typing import List, Optional, Sequence

from PySide6.QtCore import (
    Qt, Signal, QSize, QRectF, QEvent,
)
from PySide6.QtGui import (
    QColor, QFont, QIcon, QPainter, QPixmap, QLinearGradient, QBrush,
    QPen, QPainterPath,
)
from PySide6.QtWidgets import (
    QPushButton, QWidget, QFrame, QLabel, QHBoxLayout, QVBoxLayout,
    QButtonGroup, QSizePolicy, QGraphicsDropShadowEffect, QLayout,
)

from ui.design_tokens import (
    Colors, Spacing, Radii, Typography, Surfaces, Elevation, ComponentDims,
)
from ui.icon_registry import IconRegistry
from ui.typography import IconSize, IconColor

# VoiceProfile is referenced for type-hinting only. We import it lazily
# so a missing/broken engine module never prevents the components module
# from loading (the components are pure UI and should remain decoupled
# from engine internals at import time).
try:  # pragma: no cover - exercised by the engine import test
    from engine.models import VoiceProfile  # type: ignore
except Exception:  # noqa: BLE001 — broad on purpose
    VoiceProfile = None  # type: ignore[assignment]


__all__ = [
    "GradientButton",
    "IconButton",
    "StatusPill",
    "SectionHeader",
    "RoundedPanel",
    "VoiceCard",
    "EmotionGridButton",
    "SegmentedControl",
    "IconSize",
    "IconColor",
]


# ============================================================
# Internal helpers
# ============================================================
def _lighten(hex_str: str, amount: float = 0.1) -> str:
    """Return a lightened version of a hex colour.

    ``amount`` is added to the HSL lightness channel (0..1). Hue and
    saturation are preserved. The result is clamped to [0, 1].
    """
    c = QColor(hex_str)
    h, s, l, a = c.getHslF()
    l = max(0.0, min(1.0, l + amount))
    c.setHslF(h, s, l, a)
    return c.name()


def _with_alpha(hex_str: str, alpha: int) -> str:
    """Return a CSS ``rgba(...)`` string for ``hex_str`` at the given alpha.

    ``alpha`` is in 0..255. Uses ``Colors.rgba`` so the formatting is
    consistent with the rest of the design-token system.
    """
    return Colors.rgba(hex_str, alpha)


_STATUS_COLORS = {
    "success": Colors.status_success,
    "warning": Colors.status_warning,
    "error": Colors.status_error,
    "info": Colors.primary,
}


def _status_color(status: str) -> str:
    """Map a status name to its hex colour.

    Recognised statuses: ``success``, ``warning``, ``error``, ``info``.
    Unknown statuses fall back to ``Colors.text_secondary`` so the pill
    still renders even if the caller passes a typo.
    """
    return _STATUS_COLORS.get(status, Colors.text_secondary)


# ============================================================
# 1. GradientButton
# ============================================================
class GradientButton(QPushButton):
    """Orange-gradient CTA button with optional Material Symbols icon.

    The orange gradient (``Colors.cta_gradient``) is the protected
    "Main Function" token reserved for the application's primary
    action buttons: Generate, Generate All, New Project, Start Batch
    (DESIGN.md "Main Function").

    Visual behaviour
    ----------------
    * **Normal**  : diagonal orange gradient, bold white text
                    (``Typography.cta_main()``), ``Radii.button`` radius
    * **Hover**   : gradient lightened ~8% (slightly lighter)
    * **Pressed** : contents scaled to 0.97 with a 1px inset — gives a
                    tactile "press-in" feel without distorting the text
    * **Disabled**: 50% opacity, no hover/press effects

    Args:
        text:       button label (pass ``""`` for icon-only buttons —
                    though an :class:`IconButton` is the better choice
                    in that case).
        icon_name:  optional Material Symbols icon basename (without
                    ``.svg``). Rendered via :class:`IconRegistry` in
                    white (``IconColor.WHITE``) at ``IconSize.MD``.
        parent:     optional parent widget.

    Example::

        gen = GradientButton("Generate", icon_name="play_arrow")
        gen_all = GradientButton("Generate All", icon_name="graphic_eq")
    """

    # 8% lightness shift on hover — enough to read, not enough to wash
    # out the orange. Derived empirically to keep the gradient visible.
    _HOVER_LIGHTEN: float = 0.08

    def __init__(self, text: str = "",
                 icon_name: Optional[str] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)

        self._icon_name: Optional[str] = icon_name
        self._hovered: bool = False
        self._pressed: bool = False

        # Bold white CTA text. CTA padding from ComponentDims.
        self.setFont(Typography.cta_main())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        h = ComponentDims.cta_padding_v * 2 + Typography.cta_main().pixelSize() + 4
        self.setMinimumHeight(h)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        # Padding via stylesheet so the text doesn't hug the edges.
        self.setStyleSheet(
            "QPushButton {{"
            "  border: none;"
            "  padding: {v}px {h}px;"
            "  color: {fg};"
            "  border-radius: {r}px;"
            "}}".format(
                v=ComponentDims.cta_padding_v,
                h=ComponentDims.cta_padding_h,
                fg=Colors.text_primary,
                r=Radii.button,
            )
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_icon(self, icon_name: Optional[str]) -> None:
        """Swap the leading Material Symbols icon (or clear it with ``None``)."""
        self._icon_name = icon_name
        self.update()

    # ------------------------------------------------------------------
    # Event handling — drives hover/pressed state
    # ------------------------------------------------------------------
    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self._pressed = False  # safety: don't stay pressed if mouse leaves
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.Left:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.Left:
            self._pressed = False
            self.update()
        super().mouseReleaseEvent(event)

    def changeEvent(self, event) -> None:  # type: ignore[override]
        # Repaint on enable/disable so the 50%-opacity disabled state
        # applies immediately.
        if event.type() == QEvent.Type.EnabledChange:
            self.update()
        super().changeEvent(event)

    # ------------------------------------------------------------------
    # Custom paint
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # Disabled: paint everything at 50% opacity (per spec).
        if not self.isEnabled():
            painter.setOpacity(0.5)

        # Compute the painted rect, applying the 0.97 press scale.
        w = self.width()
        h = self.height()
        if self._pressed and self.isEnabled():
            scale = 0.97
            new_w = w * scale
            new_h = h * scale
            x = (w - new_w) / 2.0
            y = (h - new_h) / 2.0
            rect = QRectF(x, y, new_w, new_h)
        else:
            rect = QRectF(0.0, 0.0, float(w), float(h))

        # ---- Gradient fill ----
        # Diagonal gradient (top-left → bottom-right) matching the
        # `qlineargradient(x1:0,y1:0,x2:1,y2:1,...)` definition in
        # Colors.cta_gradient. On hover, both stops lighten by ~8%.
        if self._hovered and self.isEnabled():
            start = _lighten(Colors.orange_gradient_start, self._HOVER_LIGHTEN)
            end = _lighten(Colors.orange_gradient_end, self._HOVER_LIGHTEN)
        else:
            start = Colors.orange_gradient_start
            end = Colors.orange_gradient_end

        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor(start))
        grad.setColorAt(1.0, QColor(end))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawRoundedRect(rect, float(Radii.button), float(Radii.button))

        # ---- Optional glow on hover ----
        # A subtle orange halo around the button on hover, using
        # Elevation.cta_glow_color + Elevation.cta_glow_radius.
        if self._hovered and self.isEnabled():
            glow_color = QColor(Elevation.cta_glow_color)
            glow_color.setAlpha(70)  # ~27% — subtle, not overpowering
            glow_pen = QPen(glow_color)
            glow_pen.setWidthF(1.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(glow_pen)
            painter.drawRoundedRect(
                rect.adjusted(0.5, 0.5, -0.5, -0.5),
                float(Radii.button), float(Radii.button),
            )

        # ---- Icon + text composition ----
        text = self.text()
        icon_px = 0
        icon_pixmap: Optional[QPixmap] = None
        if self._icon_name:
            icon_pixmap = IconRegistry.icon(
                self._icon_name,
                size=IconSize.MD,
                color=IconColor.WHITE,
            ).pixmap(IconSize.MD, IconSize.MD)
            icon_px = icon_pixmap.width()

        painter.setPen(QColor(Colors.text_primary))
        painter.setFont(self.font())

        if icon_pixmap is not None and not icon_pixmap.isNull():
            # Layout: [icon] [spacing] [text]  (centered as a group)
            metrics = painter.fontMetrics()
            text_w = metrics.horizontalAdvance(text)
            spacing = Spacing.unit_base * 2  # 8px between icon and text
            total_w = icon_px + spacing + text_w
            start_x = rect.center().x() - total_w / 2.0

            # Vertically center icon
            icon_y = rect.center().y() - icon_pixmap.height() / 2.0
            painter.drawPixmap(QPointF(start_x, icon_y), icon_pixmap)
            # Vertically center text baseline
            text_x = start_x + icon_px + spacing
            text_rect = QRectF(
                text_x, rect.top(),
                total_w - icon_px - spacing, rect.height(),
            )
            painter.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                text,
            )
        else:
            painter.drawText(
                rect,
                int(Qt.AlignmentFlag.AlignCenter),
                text,
            )

        painter.end()


# ============================================================
# 2. IconButton
# ============================================================
class IconButton(QPushButton):
    """Transparent icon-only button with hover border accent.

    Used everywhere a compact icon action is needed: toolbars, sidebar
    items, inline row actions (delete, edit, share), the "more" menu
    trigger on cards.

    Visual behaviour
    ----------------
    * **Normal**   : transparent background, ``IconColor.DEFAULT`` icon
    * **Hover**    : ``bg_surface_alt`` background + 1px ``accent``
                     border
    * **Pressed**  : ``bg_raised`` background (slightly darker hover)
    * **Disabled** : 50% opacity + ``IconColor.DISABLED`` icon

    The icon is built once at construction time with BOTH the normal
    AND the disabled pixmap (via ``QIcon.Mode.Disabled``), so the
    disabled colour is rendered natively by Qt's button painting —
    no manual re-rendering is needed on enable/disable transitions.

    Args:
        icon_name:    Material Symbols basename (e.g. ``"settings"``).
        size:         icon pixel size (default ``IconSize.MD`` = 20).
        color:        icon hex colour (default ``IconColor.DEFAULT``).
        button_size:  the button's fixed square size (default 36).
        parent:       optional parent widget.

    Example::

        more = IconButton("more_horiz")
        settings_btn = IconButton("settings", button_size=32)
    """

    def __init__(self, icon_name: str,
                 size: int = IconSize.MD,
                 color: str = IconColor.DEFAULT,
                 button_size: int = 36,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._icon_name = icon_name
        self._icon_size = size
        self._icon_color = color
        self._button_size = button_size

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(button_size, button_size)

        # Build a dual-mode QIcon (Normal + Disabled). The disabled
        # pixmap uses IconColor.DISABLED so the icon visibly greys out
        # in addition to the QSS opacity reduction.
        self._refresh_icon()

        # QSS for the 3 background states + radius. The accent border
        # on hover uses Elevation.button_hover_border (the design
        # system's canonical button-hover border colour).
        self.setObjectName("SpeechStudioIconButton")
        self.setStyleSheet(
            "QPushButton#SpeechStudioIconButton {{"
            "  background: transparent;"
            "  border: 1px solid transparent;"
            "  border-radius: {r}px;"
            "  padding: 0;"
            "}}".format(r=Radii.button)
            + "\n"
            + "QPushButton#SpeechStudioIconButton:hover {{"
            "  background-color: {bg};"
            "  border: 1px solid {accent};"
            "}}".format(bg=Colors.bg_surface_alt, accent=Elevation.button_hover_border)
            + "\n"
            + "QPushButton#SpeechStudioIconButton:pressed {{"
            "  background-color: {bg};"
            "  border: 1px solid {accent};"
            "}}".format(bg=Surfaces.level_3_interactable, accent=Elevation.button_hover_border)
            + "\n"
            + "QPushButton#SpeechStudioIconButton:disabled {{"
            "  background: transparent;"
            "  border: 1px solid transparent;"
            "  color: {disabled};"
            "}}".format(disabled=Colors.text_disabled)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_icon_name(self, icon_name: str,
                      size: Optional[int] = None,
                      color: Optional[str] = None) -> None:
        """Swap the icon (and optionally its size/colour)."""
        self._icon_name = icon_name
        if size is not None:
            self._icon_size = size
        if color is not None:
            self._icon_color = color
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        """Rebuild the dual-mode QIcon and assign it to this button."""
        icon = QIcon()
        normal_pix = IconRegistry.icon(
            self._icon_name,
            size=self._icon_size,
            color=self._icon_color,
        ).pixmap(self._icon_size, self._icon_size)
        disabled_pix = IconRegistry.icon(
            self._icon_name,
            size=self._icon_size,
            color=IconColor.DISABLED,
        ).pixmap(self._icon_size, self._icon_size)
        icon.addPixmap(normal_pix, QIcon.Mode.Normal, QIcon.State.Off)
        icon.addPixmap(disabled_pix, QIcon.Mode.Disabled, QIcon.State.Off)
        super().setIcon(icon)
        super().setIconSize(QSize(self._icon_size, self._icon_size))


# ============================================================
# 3. StatusPill
# ============================================================
class StatusPill(QWidget):
    """Small pill-shaped status indicator.

    Renders a one-word status (Saved / Generating / Done / Queued) inside
    a colour-tinted rounded pill. The tint comes from the status colour
    at 20% opacity for the background and 100% opacity for the text — a
    common dark-theme pattern that keeps the pill legible without
    overwhelming the surrounding surface.

    Args:
        text:    the status label (e.g. ``"Saved"``).
        status:  one of ``"success"``, ``"warning"``, ``"error"``,
                 ``"info"``. Selects the colour from the design tokens
                 (``Colors.status_success``, ``status_warning``,
                 ``status_error``, ``Colors.primary``).
        parent:  optional parent widget.

    API:
        set_text(text)       — change the label
        set_status(status)  — change the colour

    Example::

        pill = StatusPill("Saved", status="success")
        pill.set_text("Generating")
        pill.set_status("warning")
    """

    def __init__(self, text: str = "",
                 status: str = "info",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._text = text
        self._status = status

        # Fixed height per ComponentDims. Horizontal sizing is content-driven.
        self.setFixedHeight(ComponentDims.status_pill_height)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        # Internal label — keeps the QSS simple (the pill bg goes on
        # this QWidget, the text colour goes on the QLabel).
        self._label = QLabel(text, self)
        self._label.setFont(Typography.label_caps())
        self._label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
            | Qt.AlignmentFlag.AlignVCenter,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            Spacing.padding_input_h, 0,
            Spacing.padding_input_h, 0,
        )
        layout.setSpacing(0)
        layout.addWidget(self._label)

        self._apply_style()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_text(self, text: str) -> None:
        self._text = text
        self._label.setText(text)
        self.updateGeometry()

    def set_status(self, status: str) -> None:
        self._status = status
        self._apply_style()

    def text(self) -> str:
        """Return the current status label text."""
        return self._text

    def status(self) -> str:
        """Return the current status name (e.g. ``"success"``)."""
        return self._status

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------
    def _apply_style(self) -> None:
        """Re-apply the stylesheet with the current status colour.

        Background = status colour at 20% alpha (51/255 ≈ 0.2).
        Text       = status colour at 100% alpha.
        """
        c = _status_color(self._status)
        self.setStyleSheet(
            "StatusPill {{"
            "  background-color: {bg};"
            "  border: 1px solid {border};"
            "  border-radius: {r}px;"
            "}}".format(
                bg=_with_alpha(c, 51),
                border=_with_alpha(c, 80),
                r=ComponentDims.status_pill_radius,
            )
        )
        self._label.setStyleSheet("color: {0};".format(c))


# ============================================================
# 4. SectionHeader
# ============================================================
class SectionHeader(QWidget):
    """Uppercase label-caps section header with optional action.

    Used at the top of every section in the right control panel. The
    label uses ``Typography.label_caps()`` (11px, weight 600, letter
    spacing 0.5px) in ``Colors.text_secondary``.

    The optional right-aligned action is a small text button with an
    accent border (e.g. "More", "Reset"). When clicked, the
    ``action_clicked`` signal fires.

    Args:
        title:         section title text (will be uppercased visually
                       by the typography style; pass any case).
        action_label:  optional right-aligned action button text.
                       Pass ``None`` or ``""`` for no action.
        parent:        optional parent widget.

    Signals:
        action_clicked  — emitted when the right action button is
                          clicked (only fires if ``action_label`` was
                          provided at construction time).

    Example::

        hdr = SectionHeader("VOICE", action_label="More")
        hdr.action_clicked.connect(self._open_voice_browser)
    """

    action_clicked = Signal()

    def __init__(self, title: str = "",
                 action_label: Optional[str] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.setFixedHeight(ComponentDims.section_header_height)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.unit_base)

        # Title (left)
        self._title_label = QLabel(title.upper(), self)
        self._title_label.setFont(Typography.label_caps())
        self._title_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        layout.addWidget(self._title_label)

        # Optional action button (right)
        self._action_button: Optional[QPushButton] = None
        if action_label:
            self._action_button = QPushButton(action_label, self)
            self._action_button.setFont(Typography.label_caps())
            self._action_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._action_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._action_button.setObjectName("SectionHeaderAction")
            self._action_button.setStyleSheet(
                "QPushButton#SectionHeaderAction {{"
                "  background: transparent;"
                "  color: {accent};"
                "  border: 1px solid {border};"
                "  border-radius: {r}px;"
                "  padding: {v}px {h}px;"
                "}}".format(
                    accent=Colors.text_primary,
                    border=Elevation.button_hover_border,
                    r=Radii.badge,
                    v=Spacing.unit_base,
                    h=Spacing.padding_input_h,
                )
                + "\n"
                + "QPushButton#SectionHeaderAction:hover {{"
                "  background-color: {bg};"
                "}}".format(bg=Colors.bg_surface_alt)
            )
            self._action_button.clicked.connect(self.action_clicked.emit)
            layout.addStretch(1)
            layout.addWidget(self._action_button)
        else:
            layout.addStretch(1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_title(self, title: str) -> None:
        self._title_label.setText(title.upper())


# ============================================================
# 5. RoundedPanel
# ============================================================
class RoundedPanel(QFrame):
    """Standard card / panel surface.

    The base surface for any "card" in the UI. Background is
    ``Colors.bg_surface``, border is 1px ``Colors.border_subtle``, and
    corners are ``Radii.panel``. The panel can optionally show a
    section title at the top-left (label-caps style).

    Args:
        title:   optional title shown at the top-left of the panel.
        parent:  optional parent widget.

    Usage:
        Callers add their widgets to ``panel.content_layout()``:

            panel = RoundedPanel(title="VOICE")
            panel.content_layout().addWidget(my_widget)

    Example::

        panel = RoundedPanel(title="EMOTION")
        panel.content_layout().addWidget(emotion_grid)
    """

    def __init__(self, title: Optional[str] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("SpeechStudioRoundedPanel")

        # Frame styling — keep the frame shape clean.
        self.setFrameShape(QFrame.Shape.NoFrame)

        self.setStyleSheet(
            "QFrame#SpeechStudioRoundedPanel {{"
            "  background-color: {bg};"
            "  border: {bw}px solid {border};"
            "  border-radius: {r}px;"
            "}}".format(
                bg=Surfaces.level_1_surface,
                bw=Elevation.panel_border_width,
                border=Elevation.panel_border,
                r=Radii.panel,
            )
        )

        # Root layout: just holds the title (if any) + the content area.
        # Margin matches Spacing.padding_card so contents don't touch
        # the panel border.
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(
            Spacing.padding_card,
            Spacing.padding_card,
            Spacing.padding_card,
            Spacing.padding_card,
        )
        self._root_layout.setSpacing(Spacing.margin_stack)

        # Optional title (top-left, label-caps)
        self._title_label: Optional[QLabel] = None
        if title:
            self._title_label = QLabel(title.upper(), self)
            self._title_label.setFont(Typography.label_caps())
            self._title_label.setStyleSheet(
                "color: {0}; background: transparent; border: none;".format(
                    Colors.text_secondary
                )
            )
            self._root_layout.addWidget(self._title_label)

        # Content container — caller adds widgets here.
        self._content = QWidget(self)
        self._content.setStyleSheet(
            "background: transparent; border: none;"
        )
        self._content_layout = QHBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(Spacing.unit_base)
        self._root_layout.addWidget(self._content, 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def content_layout(self) -> QHBoxLayout:
        """Return the layout callers should add their widgets to."""
        return self._content_layout

    def set_title(self, title: Optional[str]) -> None:
        """Show or hide the title label."""
        if title and self._title_label is not None:
            self._title_label.setText(title.upper())
            self._title_label.show()
        elif title and self._title_label is None:
            self._title_label = QLabel(title.upper(), self)
            self._title_label.setFont(Typography.label_caps())
            self._title_label.setStyleSheet(
                "color: {0}; background: transparent; border: none;".format(
                    Colors.text_secondary
                )
            )
            # Insert at the top of the root layout (before content).
            self._root_layout.insertWidget(0, self._title_label)
        elif self._title_label is not None:
            self._title_label.hide()


# ============================================================
# 6. VoiceCard
# ============================================================
class VoiceCard(QWidget):
    """Voice selector card used in the right control panel VOICE tab.

    Composite widget:
        ┌───────────────────────────────────────────────────┐
        │ (avatar) Voice Name (semibold)         [Change] [⋯] │
        │          metadata line (mono_data, secondary)      │
        └───────────────────────────────────────────────────┘

    * Avatar: 40×40 circular (ComponentDims.voice_card_avatar). If the
      VoiceProfile has a ``preview_image`` it is loaded and clipped to
      a circle; otherwise a placeholder is rendered with the
      ``person`` Material Symbols icon.
    * Voice name uses ``Typography.body_md()`` set to DemiBold weight.
    * Metadata uses ``Typography.mono_data()`` in ``text_secondary``.
    * "Change Voice" is a small bordered QPushButton with an accent border.
    * "More" is an :class:`IconButton` (``more_horiz``).

    Args:
        voice:   optional :class:`VoiceProfile`. Pass ``None`` and call
                 :meth:`set_voice` later for lazy updates.
        parent:  optional parent widget.

    Signals:
        change_voice_clicked  — user pressed the "Change Voice" button.
        more_clicked          — user pressed the ⋯ (more) button.

    Example::

        card = VoiceCard(voice=profile)
        card.change_voice_clicked.connect(self._open_voice_browser)
        card.more_clicked.connect(self._open_voice_menu)
    """

    change_voice_clicked = Signal()
    more_clicked = Signal()

    def __init__(self, voice: Optional["VoiceProfile"] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("SpeechStudioVoiceCard")

        self.setStyleSheet(
            "QWidget#SpeechStudioVoiceCard {{"
            "  background-color: {bg};"
            "  border: {bw}px solid {border};"
            "  border-radius: {r}px;"
            "}}".format(
                bg=Surfaces.level_2_container,
                bw=Elevation.card_border_width,
                border=Elevation.card_border,
                r=Radii.card,
            )
        )

        self._voice = voice

        # Root layout — Spacing.padding_card margin around the contents.
        root = QHBoxLayout(self)
        root.setContentsMargins(
            Spacing.padding_card, Spacing.padding_card,
            Spacing.padding_card, Spacing.padding_card,
        )
        root.setSpacing(Spacing.margin_stack)

        # ---- Avatar ----
        self._avatar_label = QLabel(self)
        self._avatar_label.setFixedSize(
            ComponentDims.voice_card_avatar,
            ComponentDims.voice_card_avatar,
        )
        self._avatar_label.setStyleSheet("background: transparent; border: none;")
        root.addWidget(self._avatar_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # ---- Name + metadata column ----
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(Spacing.unit_base)

        self._name_label = QLabel("—", self)
        name_font = Typography.body_md()
        name_font.setWeight(QFont.Weight.DemiBold)
        self._name_label.setFont(name_font)
        self._name_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_primary
            )
        )
        text_col.addWidget(self._name_label)

        self._metadata_label = QLabel("", self)
        self._metadata_label.setFont(Typography.mono_data())
        self._metadata_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        text_col.addWidget(self._metadata_label)

        root.addLayout(text_col, 1)

        # ---- "Change Voice" button (small, accent border) ----
        self._change_button = QPushButton("Change Voice", self)
        self._change_button.setFont(Typography.label_caps())
        self._change_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._change_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._change_button.setObjectName("VoiceCardChange")
        self._change_button.setStyleSheet(
            "QPushButton#VoiceCardChange {{"
            "  background: transparent;"
            "  color: {fg};"
            "  border: 1px solid {border};"
            "  border-radius: {r}px;"
            "  padding: {v}px {h}px;"
            "}}".format(
                fg=Colors.text_primary,
                border=Elevation.button_hover_border,
                r=Radii.button,
                v=Spacing.unit_base,
                h=Spacing.padding_input_h,
            )
            + "\n"
            + "QPushButton#VoiceCardChange:hover {{"
            "  background-color: {bg};"
            "}}".format(bg=Colors.bg_surface_alt)
            + "\n"
            + "QPushButton#VoiceCardChange:pressed {{"
            "  background-color: {bg};"
            "}}".format(bg=Surfaces.level_3_interactable)
            + "\n"
            + "QPushButton#VoiceCardChange:disabled {{"
            "  color: {disabled};"
            "  border: 1px solid {border_disabled};"
            "}}".format(
                disabled=Colors.text_disabled,
                border_disabled=Colors.border_subtle,
            )
        )
        self._change_button.clicked.connect(self.change_voice_clicked.emit)
        root.addWidget(self._change_button, 0, Qt.AlignmentFlag.AlignVCenter)

        # ---- More (...) button ----
        self._more_button = IconButton(
            icon_name="more_horiz",
            size=IconSize.MD,
            color=IconColor.DEFAULT,
            button_size=28,
            parent=self,
        )
        self._more_button.clicked.connect(self.more_clicked.emit)
        root.addWidget(self._more_button, 0, Qt.AlignmentFlag.AlignVCenter)

        # Populate from the voice (if given).
        if voice is not None:
            self.set_voice(voice)
        else:
            self._render_placeholder_avatar()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_voice(self, voice: "VoiceProfile") -> None:
        """Update the card to display ``voice``."""
        self._voice = voice
        self._name_label.setText(voice.name or voice.id)

        # Metadata line — sample rate + duration if available.
        meta_parts: List[str] = []
        if voice.sample_rate:
            meta_parts.append("{0} Hz".format(voice.sample_rate))
        if voice.duration:
            meta_parts.append("{0:.1f}s".format(voice.duration))
        if voice.tags:
            meta_parts.append(", ".join(voice.tags[:3]))
        self._metadata_label.setText(" · ".join(meta_parts))

        # Avatar — from preview_image if present, else placeholder.
        avatar_px = self._load_avatar_pixmap(voice)
        if avatar_px is not None:
            self._avatar_label.setPixmap(avatar_px)
        else:
            self._render_placeholder_avatar()

    def voice(self) -> Optional["VoiceProfile"]:
        """Return the currently displayed VoiceProfile (or ``None``)."""
        return self._voice

    # ------------------------------------------------------------------
    # Avatar rendering
    # ------------------------------------------------------------------
    def _load_avatar_pixmap(self, voice: "VoiceProfile") -> Optional[QPixmap]:
        """Load the voice's preview_image into a circular QPixmap.

        Returns ``None`` if no preview image is configured or the file
        can't be read.
        """
        path = getattr(voice, "preview_image", "") or ""
        if not path:
            return None
        if not os.path.isabs(path):
            # Voices are stored under the application's voices/ folder;
            # the voice manager resolves this at runtime. We attempt a
            # best-effort relative-to-cwd lookup — if it fails we fall
            # through to the placeholder avatar.
            path = os.path.abspath(path)
        pm = QPixmap(path)
        if pm.isNull():
            return None
        return _make_circular_pixmap(pm, ComponentDims.voice_card_avatar)

    def _render_placeholder_avatar(self) -> None:
        """Render a circular avatar with the ``person`` Material Symbol."""
        pm = _make_placeholder_avatar(ComponentDims.voice_card_avatar)
        self._avatar_label.setPixmap(pm)


# ============================================================
# 7. EmotionGridButton
# ============================================================
class EmotionGridButton(QWidget):
    """Square emotion tile for the emotion grid.

    Each button shows a Material Symbols icon (24px) above a small
    label. The button supports three visual states:

    * **Normal**   : transparent background
    * **Hover**    : ``bg_raised`` background
    * **Selected** : ``accent`` border + subtle purple glow
                     (``Elevation.emotion_selected_glow``)

    Clicking emits the ``clicked`` signal. Selection state is tracked
    internally — callers use :meth:`set_selected` and :meth:`is_selected`
    to drive the grid's "one selected at a time" logic.

    Args:
        icon_name:  Material Symbols basename (e.g. ``"sentiment_neutral"``).
        label:      short caption shown under the icon.
        parent:     optional parent widget.

    Signals:
        clicked   — emitted on left-click (same trigger as QPushButton).
        selected  — emitted when this button becomes selected.

    Example::

        btn = EmotionGridButton("sentiment_satisfied", "Happy")
        btn.clicked.connect(self._on_emotion_clicked)
    """

    clicked = Signal()
    selected = Signal(bool)

    def __init__(self, icon_name: str, label: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("SpeechStudioEmotionButton")

        self._icon_name = icon_name
        self._label_text = label
        self._selected = False
        self._hovered = False
        self._pressed = False

        self.setMinimumSize(
            ComponentDims.emotion_button_min_size,
            ComponentDims.emotion_button_min_size,
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Glow effect — disabled by default; enabled when selected.
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setColor(QColor(Elevation.emotion_selected_glow))
        self._glow.setBlurRadius(Elevation.emotion_selected_glow_radius)
        self._glow.setOffset(0, 0)
        self._glow.setEnabled(False)
        self.setGraphicsEffect(self._glow)

        # Vertical layout: icon (top, centered) + label (bottom).
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Spacing.unit_base, Spacing.unit_base,
            Spacing.unit_base, Spacing.unit_base,
        )
        layout.setSpacing(Spacing.unit_base)

        self._icon_label = QLabel(self)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setStyleSheet("background: transparent; border: none;")
        self._refresh_icon()
        layout.addWidget(self._icon_label, 1)

        self._text_label = QLabel(label, self)
        self._text_label.setFont(Typography.metadata_sm())
        self._text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._text_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        layout.addWidget(self._text_label, 0, Qt.AlignmentFlag.AlignBottom)

        self._apply_style()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_label(self, label: str) -> None:
        self._label_text = label
        self._text_label.setText(label)

    def set_icon_name(self, icon_name: str) -> None:
        self._icon_name = icon_name
        self._refresh_icon()

    def set_selected(self, selected: bool) -> None:
        """Toggle the selected visual state and glow."""
        if self._selected == selected:
            return
        self._selected = selected
        self._glow.setEnabled(selected)
        self._apply_style()
        self.selected.emit(selected)

    def is_selected(self) -> bool:
        return self._selected

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------
    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self._pressed = False
        self._apply_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.Left:
            self._pressed = True
            self._apply_style()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.Left and self._pressed:
            self._pressed = False
            self._apply_style()
            # Only fire clicked if the release is inside the widget.
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------
    def _refresh_icon(self) -> None:
        """Re-render the Material Symbols icon into the icon QLabel."""
        # Selected = accent text colour, otherwise DEFAULT.
        color = (
            Elevation.emotion_selected_glow
            if self._selected
            else IconColor.DEFAULT
        )
        pix = IconRegistry.icon(
            self._icon_name, size=Spacing.icon_lg, color=color,
        ).pixmap(Spacing.icon_lg, Spacing.icon_lg)
        self._icon_label.setPixmap(pix)

    def _apply_style(self) -> None:
        """Rebuild the stylesheet based on the current state."""
        if self._selected:
            bg = "transparent"
            border_color = Elevation.emotion_selected_glow
            text_color = Colors.text_primary
        elif self._hovered:
            bg = Surfaces.level_3_interactable
            border_color = Colors.border_subtle
            text_color = Colors.text_primary
        elif self._pressed:
            bg = Surfaces.level_2_container
            border_color = Colors.border_subtle
            text_color = Colors.text_primary
        else:
            bg = "transparent"
            border_color = "transparent"
            text_color = Colors.text_secondary

        self.setStyleSheet(
            "QWidget#SpeechStudioEmotionButton {{"
            "  background-color: {bg};"
            "  border: 1px solid {border};"
            "  border-radius: {r}px;"
            "}}".format(bg=bg, border=border_color, r=Radii.default)
        )
        self._text_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(text_color)
        )
        # Refresh icon colour to match the new state.
        self._refresh_icon()


# ============================================================
# 8. SegmentedControl
# ============================================================
class SegmentedControl(QWidget):
    """Horizontal option selector (segmented tab bar).

    A row of equal-width segments. Exactly one segment is selected at a
    time. Used in the right control panel for the Pace / Pitch /
    Delivery / Length selectors.

    Visual behaviour
    ----------------
    * **Selected**    : ``Colors.primary`` background, ``on_primary``
                        text colour, no border
    * **Unselected**  : transparent background, ``text_secondary`` text
    * **Hover (unselected)**: ``Colors.bg_surface_alt`` background
    * **Pressed**      : ``Colors.bg_raised`` background

    Args:
        labels:    list of segment labels (e.g. ``["Slow", "Normal",
                   "Fast"]``).
        initial:   index of the initially-selected segment (default 0).
        parent:    optional parent widget.

    Signals:
        segment_changed(int index, str label)
                  — emitted when the selected segment changes.

    Example::

        pace = SegmentedControl(["Slow", "Normal", "Fast", "Very Fast"])
        pace.segment_changed.connect(self._on_pace_changed)
    """

    segment_changed = Signal(int, str)

    def __init__(self, labels: Sequence[str], initial: int = 0,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("SpeechStudioSegmentedControl")

        self._labels: List[str] = list(labels)
        self._selected_index: int = max(0, min(initial, len(self._labels) - 1)) \
            if self._labels else 0
        self._buttons: List[QPushButton] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        # Background of the entire control — a thin inset strip that
        # visually unifies the segments (matches the HTML reference's
        # "track" container for segmented controls).
        self.setStyleSheet(
            "QWidget#SpeechStudioSegmentedControl {{"
            "  background-color: {bg};"
            "  border: 1px solid {border};"
            "  border-radius: {r}px;"
            "}}".format(
                bg=Surfaces.level_0_floor,
                border=Colors.border_subtle,
                r=Radii.default,
            )
        )

        # Horizontal layout — segments fill the control evenly.
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            Spacing.unit_base, Spacing.unit_base,
            Spacing.unit_base, Spacing.unit_base,
        )
        layout.setSpacing(Spacing.unit_base)

        for i, lbl in enumerate(self._labels):
            btn = QPushButton(lbl, self)
            btn.setFont(Typography.label_caps())
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCheckable(True)
            btn.setObjectName("SpeechStudioSegment")
            btn.setStyleSheet(self._segment_qss())
            btn.clicked.connect(lambda _checked=False, idx=i: self._on_segment_clicked(idx))
            layout.addWidget(btn, 1)
            self._buttons.append(btn)
            self._group.addButton(btn, i)

        # Apply the initial selection.
        if self._buttons:
            self._buttons[self._selected_index].setChecked(True)
            self._refresh_segment_styles()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_labels(self, labels: Sequence[str],
                   initial: Optional[int] = None) -> None:
        """Replace the segment labels.

        ``initial`` selects the initially-checked segment after the
        rebuild (defaults to the current index, clamped).
        """
        # Remember the current selection before rebuilding.
        if initial is None:
            initial = self._selected_index
        # Clear the group + layout.
        for btn in self._buttons:
            self._group.removeButton(btn)
            btn.setParent(None)
            btn.deleteLater()
        self._buttons.clear()
        self._labels = list(labels)

        # Re-populate (mirrors __init__).
        layout = self.layout()
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

        for i, lbl in enumerate(self._labels):
            btn = QPushButton(lbl, self)
            btn.setFont(Typography.label_caps())
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCheckable(True)
            btn.setObjectName("SpeechStudioSegment")
            btn.setStyleSheet(self._segment_qss())
            btn.clicked.connect(lambda _checked=False, idx=i: self._on_segment_clicked(idx))
            layout.addWidget(btn, 1)
            self._buttons.append(btn)
            self._group.addButton(btn, i)

        self._selected_index = (
            max(0, min(initial, len(self._labels) - 1))
            if self._labels else 0
        )
        if self._buttons:
            self._buttons[self._selected_index].setChecked(True)
            self._refresh_segment_styles()

    def selected_index(self) -> int:
        """Return the index of the currently-selected segment."""
        return self._selected_index

    def selected_label(self) -> str:
        """Return the label of the currently-selected segment."""
        if 0 <= self._selected_index < len(self._labels):
            return self._labels[self._selected_index]
        return ""

    def set_selected(self, index: int) -> None:
        """Programmatically select a segment by index."""
        if not self._labels or not (0 <= index < len(self._labels)):
            return
        if index == self._selected_index:
            return
        self._selected_index = index
        self._buttons[index].setChecked(True)
        self._refresh_segment_styles()
        self.segment_changed.emit(index, self._labels[index])

    # ------------------------------------------------------------------
    # Internal slot
    # ------------------------------------------------------------------
    def _on_segment_clicked(self, index: int) -> None:
        if index == self._selected_index:
            return
        self._selected_index = index
        self._refresh_segment_styles()
        self.segment_changed.emit(index, self._labels[index])

    def _refresh_segment_styles(self) -> None:
        """Re-apply the segment stylesheet to reflect selection state."""
        for i, btn in enumerate(self._buttons):
            btn.setStyleSheet(self._segment_qss(selected=(i == self._selected_index)))

    def _segment_qss(self, selected: bool = False) -> str:
        """Build the QSS for a single segment in the given state."""
        if selected:
            bg = Colors.primary
            fg = Colors.on_primary
            border = "transparent"
        else:
            bg = "transparent"
            fg = Colors.text_secondary
            border = "transparent"
        return (
            "QPushButton#SpeechStudioSegment {{"
            "  background-color: {bg};"
            "  color: {fg};"
            "  border: 1px solid {border};"
            "  border-radius: {r}px;"
            "  padding: {v}px {h}px;"
            "}}".format(
                bg=bg, fg=fg, border=border,
                r=Radii.badge,
                v=Spacing.unit_base,
                h=Spacing.padding_input_h,
            )
            + "\n"
            + "QPushButton#SpeechStudioSegment:hover {{"
            "  background-color: {hover_bg};"
            "  color: {hover_fg};"
            "}}".format(
                hover_bg=Colors.bg_surface_alt
                          if not selected else Colors.primary,
                hover_fg=Colors.text_primary
                          if not selected else Colors.on_primary,
            )
            + "\n"
            + "QPushButton#SpeechStudioSegment:pressed {{"
            "  background-color: {press_bg};"
            "}}".format(
                press_bg=Colors.bg_raised
                          if not selected else Colors.primary_container,
            )
            + "\n"
            + "QPushButton#SpeechStudioSegment:checked {{"
            "  background-color: {checked_bg};"
            "  color: {checked_fg};"
            "}}".format(
                checked_bg=Colors.primary,
                checked_fg=Colors.on_primary,
            )
        )


# ============================================================
# Module-level avatar helpers (used by VoiceCard)
# ============================================================
def _make_circular_pixmap(source: QPixmap, diameter: int) -> QPixmap:
    """Render ``source`` into a square-cropped circular QPixmap.

    The source is scaled to cover the destination (aspect ratio
    preserved via ``KeepAspectRatioByExpanding``) and clipped to an
    ellipse so the visible avatar is always perfectly round.
    """
    result = QPixmap(diameter, diameter)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    clip = QPainterPath()
    clip.addEllipse(QRectF(0.0, 0.0, float(diameter), float(diameter)))
    painter.setClipPath(clip)

    if not source.isNull():
        scaled = source.scaled(
            diameter, diameter,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (diameter - scaled.width()) // 2
        y = (diameter - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
    painter.end()
    return result


def _make_placeholder_avatar(diameter: int) -> QPixmap:
    """Render a circular avatar with the ``person`` Material Symbol.

    Used as a fallback when a VoiceProfile has no preview image. The
    circle background is ``Colors.bg_surface_alt`` so the avatar reads
    as a clearly-defined circular token even without an image.
    """
    result = QPixmap(diameter, diameter)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Circle background
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(Colors.bg_surface_alt))
    painter.drawEllipse(QRectF(0.0, 0.0, float(diameter), float(diameter)))

    # Person icon — sized to ~60% of the avatar diameter and centred.
    icon_px = max(12, int(diameter * 0.6))
    person_pix = IconRegistry.icon(
        "person", size=icon_px, color=Colors.text_secondary,
    ).pixmap(icon_px, icon_px)
    x = (diameter - person_pix.width()) // 2
    y = (diameter - person_pix.height()) // 2
    painter.drawPixmap(x, y, person_pix)

    painter.end()
    return result


# ============================================================
# Standalone smoke test
# ============================================================
if __name__ == "__main__":  # pragma: no cover — manual visual check
    import sys
    from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

    app = QApplication(sys.argv)

    # Apply the application theme so colours match the production UI.
    try:
        from ui.theme import apply_theme
        apply_theme(app)
    except Exception:
        pass  # fall back to default — components still render.

    container = QWidget()
    container.setStyleSheet(
        "background-color: {0};".format(Colors.bg_base)
    )
    root = QVBoxLayout(container)
    root.setContentsMargins(24, 24, 24, 24)
    root.setSpacing(16)

    # 1. GradientButton (with + without icon)
    root.addWidget(GradientButton("Generate", icon_name="play_arrow"))
    root.addWidget(GradientButton("Generate All", icon_name="graphic_eq"))
    disabled_cta = GradientButton("Disabled CTA", icon_name="add")
    disabled_cta.setEnabled(False)
    root.addWidget(disabled_cta)

    # 2. IconButton row
    icon_row = QWidget()
    icon_layout = QHBoxLayout(icon_row)
    icon_layout.setContentsMargins(0, 0, 0, 0)
    icon_layout.setSpacing(8)
    for name in ("add", "edit", "save", "upload", "settings", "more_horiz"):
        icon_layout.addWidget(IconButton(name))
    disabled_icon = IconButton("save")
    disabled_icon.setEnabled(False)
    icon_layout.addWidget(disabled_icon)
    icon_layout.addStretch(1)
    root.addWidget(icon_row)

    # 3. StatusPill row
    pill_row = QWidget()
    pill_layout = QHBoxLayout(pill_row)
    pill_layout.setContentsMargins(0, 0, 0, 0)
    pill_layout.setSpacing(8)
    for txt, status in (
        ("Saved", "success"),
        ("Generating", "warning"),
        ("Failed", "error"),
        ("Queued", "info"),
    ):
        pill_layout.addWidget(StatusPill(txt, status=status))
    pill_layout.addStretch(1)
    root.addWidget(pill_row)

    # 4. SectionHeader
    root.addWidget(SectionHeader("VOICE", action_label="More"))

    # 5. RoundedPanel
    panel = RoundedPanel(title="EMOTION")
    panel.content_layout().addWidget(QLabel("panel contents go here"))
    root.addWidget(panel)

    # 6. VoiceCard (placeholder, no real voice available)
    root.addWidget(VoiceCard())

    # 7. EmotionGridButton row
    emo_row = QWidget()
    emo_layout = QHBoxLayout(emo_row)
    emo_layout.setContentsMargins(0, 0, 0, 0)
    emo_layout.setSpacing(8)
    emotions = [
        ("sentiment_neutral", "Neutral"),
        ("sentiment_satisfied", "Happy"),
        ("sentiment_dissatisfied", "Sad"),
        ("celebration", "Excited"),
    ]
    for name, label in emotions:
        emo_layout.addWidget(EmotionGridButton(name, label))
    emo_layout.addStretch(1)
    root.addWidget(emo_row)

    # 8. SegmentedControl
    seg = SegmentedControl(["Slow", "Normal", "Fast", "Very Fast"])
    seg.segment_changed.connect(
        lambda idx, lbl: print("segment_changed ->", idx, lbl)
    )
    root.addWidget(seg)

    root.addStretch(1)

    win = QMainWindow()
    win.setWindowTitle("SpeechStudio Components — Visual Smoke Test")
    win.setCentralWidget(container)
    win.resize(560, 720)
    win.show()

    sys.exit(app.exec())
