"""
SpeechStudio — Central Typography & Icon System
================================================

Defines all typography tokens and icon infrastructure used across
the entire application. Based directly on the HTML DESIGN.md design
system tokens.

Typography tokens (from DESIGN.md):
    headline-lg:   24px, weight 700, line-height 32px, Libre Franklin
    headline-md:   18px, weight 600, line-height 24px, Libre Franklin
    body-lg:       15px, weight 400, line-height 22px, Inter
    body-md:       13px, weight 400, line-height 20px, Inter
    cta-main:      14px, weight 700, line-height 18px, letter-spacing 0.5px, Inter
    label-caps:    11px, weight 600, line-height 16px, letter-spacing 0.5px, Inter
    mono-data:     12px, weight 400, line-height 16px, JetBrains Mono

Icon size tokens:
    icon-sm:  16px  (small inline icons, badges)
    icon-md:  20px  (standard UI icons, nav items, action buttons)
    icon-lg:  24px  (brand icons, prominent elements)
    icon-xl:  48px  (empty state heroes, large displays)

Icon colour tokens:
    icon_default:   #F5F5F5  (text-primary)
    icon_secondary: #A0A0A0  (text-secondary)
    icon_accent:    #d0bcff  (primary/accent)
    icon_white:     #FFFFFF  (on CTA buttons)
    icon_disabled:  #555555  (text-disabled)

Font families:
    body:       "Inter", "Segoe UI", "SF Pro Text", sans-serif
    heading:    "Libre Franklin", "Segoe UI", sans-serif
    mono:       "JetBrains Mono", "Consolas", "Courier New", monospace

Usage:
    from ui.typography import Typography, IconSize, IconColor, make_icon

    # Create a QFont with a specific token
    font = Typography.body_md()
    font = Typography.label_caps()

    # Create an icon
    icon = make_icon(SVG_PLAY_ARROW, IconSize.MD, IconColor.WHITE)
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, QByteArray, QSize
from PySide6.QtGui import QFont, QPixmap, QPainter, QIcon


# ===========================================================================
# FONT FAMILIES
# ===========================================================================
# IMPORTANT: Qt's QFont.setFamily() takes a SINGLE family name, not a CSS
# fallback stack. Passing '"Inter", "Segoe UI", ...' makes Qt look for a
# family with that literal string (including quotes and commas), which
# doesn't exist — Qt then silently falls back to the system default font,
# making all text look pixelated even though the Google Fonts are loaded.
#
# The correct approach: use just the primary family name. Qt has its own
# internal fallback mechanism — if the primary isn't found, it tries the
# next best match automatically.
FONT_BODY = "Inter"
FONT_HEADING = "Libre Franklin"
FONT_MONO = "JetBrains Mono"


# ===========================================================================
# TYPOGRAPHY TOKENS
# ===========================================================================
# Each token returns a QFont configured with the exact HTML design system values.
# Sizes are in POINTS (not pixels) for QFont — Qt handles DPI scaling.
# At 96 DPI (Windows default), 1pt ≈ 1.33px, so we use pixel sizes directly
# via QFont.setPixelSize() for exact matching with the HTML reference.

class Typography:
    """Central typography tokens matching the HTML DESIGN.md system.

    All sizes are set in PIXELS via QFont.setPixelSize() to exactly match
    the HTML reference values (which are in px).
    """

    @staticmethod
    def _make(family: str, px_size: int, weight: QFont.Weight,
              letter_spacing: float = 0.0) -> QFont:
        """Create a QFont with the given parameters."""
        f = QFont()
        f.setFamily(family)
        f.setPixelSize(px_size)
        f.setWeight(weight)
        if letter_spacing > 0:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
        return f

    # --- Headlines (Libre Franklin) ---
    @staticmethod
    def headline_lg() -> QFont:
        """26px, weight 700, Libre Franklin."""
        return Typography._make(FONT_HEADING, 26, QFont.Weight.Bold)

    @staticmethod
    def headline_md() -> QFont:
        """19px, weight 600, Libre Franklin."""
        return Typography._make(FONT_HEADING, 19, QFont.Weight.DemiBold)

    # --- Body text (Inter) ---
    @staticmethod
    def body_lg() -> QFont:
        """16px, weight 400, Inter."""
        return Typography._make(FONT_BODY, 16, QFont.Weight.Normal)

    @staticmethod
    def body_md() -> QFont:
        """14px, weight 400, Inter."""
        return Typography._make(FONT_BODY, 14, QFont.Weight.Normal)

    # --- CTA (Inter) ---
    @staticmethod
    def cta_main() -> QFont:
        """15px, weight 700, letter-spacing 0.5px, Inter."""
        return Typography._make(FONT_BODY, 15, QFont.Weight.Bold, 0.5)

    # --- Label caps (Inter) ---
    @staticmethod
    def label_caps() -> QFont:
        """12px, weight 600, letter-spacing 0.5px, Inter."""
        return Typography._make(FONT_BODY, 12, QFont.Weight.DemiBold, 0.5)

    # --- Mono data (JetBrains Mono) ---
    @staticmethod
    def mono_data() -> QFont:
        """13px, weight 400, JetBrains Mono."""
        return Typography._make(FONT_MONO, 13, QFont.Weight.Normal)

    # --- Small metadata (Inter, 11px — used for scene rows, badges) ---
    @staticmethod
    def metadata_sm() -> QFont:
        """11px, weight 400, Inter. Used for small badges and scene rows."""
        return Typography._make(FONT_BODY, 11, QFont.Weight.Normal)

    # --- Secondary metadata (Inter, 12px — P3.34 row subtitles) ---
    @staticmethod
    def metadata_md() -> QFont:
        """12px, weight 400, Inter.

        P3.34 typography hierarchy: primary row labels use body_md (14px);
        secondary metadata (Recents/context row subtitles) uses this
        12px token — one step down, still comfortably readable. Same
        Inter family (no new font introduced).
        """
        return Typography._make(FONT_BODY, 12, QFont.Weight.Normal)

    # --- Navigation (Inter, 15px — used for top nav menu items) ---
    @staticmethod
    def nav_item() -> QFont:
        """15px, weight 400 (normal) / 600 (active), Inter."""
        return Typography._make(FONT_BODY, 15, QFont.Weight.Normal)

    @staticmethod
    def nav_item_active() -> QFont:
        """15px, weight 600 (active), Inter."""
        return Typography._make(FONT_BODY, 15, QFont.Weight.DemiBold)

    # --- Sidebar nav items ---
    @staticmethod
    def sidebar_nav() -> QFont:
        """14px, weight 400, Inter."""
        return Typography._make(FONT_BODY, 14, QFont.Weight.Normal)

    @staticmethod
    def sidebar_nav_active() -> QFont:
        """14px, weight 600, Inter."""
        return Typography._make(FONT_BODY, 14, QFont.Weight.DemiBold)


# ===========================================================================
# ICON SIZE TOKENS
# ===========================================================================
class IconSize:
    """Icon size tokens derived from the HTML reference.

    Sizes increased slightly for better readability and to match the
    enlarged typography scale. Icons are rendered from SVG vectors so
    they stay crisp at any size — no pixelation.

    Different roles use different sizes:
        - 18px: small inline icons (badges, scene row indicators)
        - 22px: standard UI icons (nav items, action buttons)
        - 26px: prominent icons (brand, large buttons)
        - 52px: hero/empty-state icons
    """
    SM = 18
    MD = 22
    LG = 26
    XL = 52


# ===========================================================================
# ICON COLOUR TOKENS
# ===========================================================================
class IconColor:
    """Icon colour tokens matching the HTML design system."""
    DEFAULT = "#F5F5F5"    # text-primary — standard icons
    SECONDARY = "#A0A0A0"  # text-secondary — less prominent icons
    ACCENT = "#d0bcff"     # primary/accent — active/selected icons
    WHITE = "#FFFFFF"      # on CTA buttons (orange gradient)
    DISABLED = "#555555"   # text-disabled
    SUCCESS = "#10B981"    # status-success
    WARNING = "#F59E0B"     # status-warning
    ERROR = "#EF4444"       # status-error


# ===========================================================================
# SVG ICON LIBRARY (Material Symbols Outlined)
# ===========================================================================
# All SVGs use fill="currentColor" — the make_icon() function replaces
# this with the actual colour before rendering via QSvgRenderer.

# Navigation / project icons
SVG_GRAPHIC_EQ = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 3h2v18H7V3zm4 2h2v14h-2V5zm4 4h2v6h-2V9zm-8 2h2v2H3v-2zm12 0h2v2h-2v-2z"/></svg>'
SVG_FOLDER_OPEN = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm0 12H4V8h16v10z"/></svg>'
SVG_MOVIE_EDIT = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z"/></svg>'
SVG_GROUPS = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/></svg>'

# Action icons
SVG_ADD = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>'
SVG_UNDO = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.5 8c-2.65 0-5.05.99-6.9 2.6L2 7v9h9l-3.62-3.62c1.39-1.12 3.12-1.88 5.12-1.88 3.53 0 6.55 2.31 7.6 5.5l2.37-.78C21.08 11.03 17.15 8 12.5 8z"/></svg>'
SVG_REDO = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.4 10.6C16.55 8.99 14.15 8 11.5 8c-4.65 0-8.58 3.03-9.96 7.22L3.9 16c1.05-3.19 4.07-5.5 7.6-5.5 2 0 3.73.76 5.12 1.88L13 16h9V7l-3.6 3.6z"/></svg>'
SVG_SAVE = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z"/></svg>'
SVG_UPLOAD = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M5 20h14v-2H5v2zM12 2l-5 5h3v6h4V7h3l-5-5z"/></svg>'
SVG_EDIT = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>'

# Transport icons
SVG_PLAY_ARROW = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>'
SVG_PAUSE = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>'
SVG_STOP = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h12v12H6z"/></svg>'
SVG_SKIP_PREVIOUS = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>'
SVG_SKIP_NEXT = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>'
SVG_VOLUME_UP = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>'

# Emotion icons (Material Symbols sentiment set)
SVG_SENTIMENT_NEUTRAL = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M9 14h6v1.5H9z"/><circle cx="15.5" cy="9.5" r="1.5"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8z"/></svg>'
SVG_SENTIMENT_SATISFIED = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-5.5c-.83 0-1.5.67-1.5 1.5s.67 1.5 1.5 1.5 1.5-.67 1.5-1.5-.67-1.5-1.5-1.5z"/><circle cx="15.5" cy="9.5" r="1.5"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="M12 18c1.93 0 3.62-1.15 4.38-2.8H7.62C8.38 16.85 10.07 18 12 18z"/></svg>'
SVG_SENTIMENT_DISSATISFIED = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M15.5 9.5m-1.5 0a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-5.5c-.83 0-1.5.67-1.5 1.5s.67 1.5 1.5 1.5 1.5-.67 1.5-1.5-.67-1.5-1.5-1.5z"/><path d="M7.62 14.8C8.38 16.85 10.07 18 12 18s3.62-1.15 4.38-2.8H7.62z" transform="scale(1,-1) translate(0,-31.6)"/></svg>'
SVG_SENTIMENT_VERY_DISSATISFIED = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm-5-9.5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5S7 11.33 7 10.5zm9 3.5c0-1.66-1.34-3-3-3s-3 1.34-3 3 1.34 3 3 3 3-1.34 3-3zm-2.5-3c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5-1.5-.67-1.5-1.5z"/></svg>'
SVG_MOOD_BAD = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm-5-9.5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5S7 11.33 7 10.5zm9 3.5c0-1.66-1.34-3-3-3s-3 1.34-3 3 1.34 3 3 3 3-1.34 3-3zm-2.5-3c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5-1.5-.67-1.5-1.5z"/></svg>'
SVG_SENTIMENT_EXTREMELY_DISSATISFIED = SVG_MOOD_BAD
SVG_SELF_IMPROVEMENT = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 6c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm0 15c-1.1 0-2-.9-2-2v-4h2v6zm-7-3h12v2H5v-2z"/></svg>'
SVG_CELEBRATION = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 22l14-14-2-2L2 20v2zm16-6l-2 2 3 3 2-2-3-3zm3-12l-2 2 3 3 2-2-3-3zm-5 4l-2 2 3 3 2-2-3-3z"/></svg>'
SVG_VOLUME_DOWN = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 9v6h4l5 5V4l-5 5H7z"/></svg>'

# Status / checkmark icons
SVG_CHECK_CIRCLE = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>'
SVG_CHEVRON_RIGHT = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z"/></svg>'


# ===========================================================================
# ICON RENDERING
# ===========================================================================
def make_icon(svg_str: str, size: int = IconSize.MD,
              color: str = IconColor.DEFAULT) -> Optional[QIcon]:
    """Create a QIcon from an SVG string, rendered at the given size and color.

    The SVG must use fill="currentColor" — this function replaces it with
    the actual colour before rendering via QSvgRenderer, because QSvgRenderer
    does NOT inherit the widget's stylesheet color property.

    Args:
        svg_str: SVG source string with fill="currentColor".
        size: Icon size in pixels (use IconSize tokens).
        color: Hex color string (use IconColor tokens).

    Returns:
        QIcon, or None if rendering failed.
    """
    from PySide6.QtSvg import QSvgRenderer

    # Replace "currentColor" with the actual colour
    svg_colored = svg_str.replace("currentColor", color)

    renderer = QSvgRenderer(QByteArray(svg_colored.encode("utf-8")))
    if not renderer.isValid():
        return None

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
