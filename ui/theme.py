"""
SpeechStudio Theme System.

Supports 4 themes:
- Dark (default, current look)
- Light (clean, professional)
- Synthwave (neon retro, '80s vibe)
- Retro Console (Game Boy inspired, monospace, pixel-art)

Each theme defines a complete color palette. The apply_theme() function
generates a QSS stylesheet from the palette and applies it to the
QApplication.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


# ============================================================
# Backwards-compatible Palette class
# Many files import `from ui.theme import Palette` and use
# Palette.ACCENT, Palette.BG_BASE, etc. This class provides
# those attributes using the Dark theme's colors as defaults.
# ============================================================
class Palette:
    """Legacy color palette (maps to Dark theme colors).

    Use ThemePalette + apply_theme() for the full theme system.
    This class exists for backwards compatibility with code that
    imports `from ui.theme import Palette`.
    """
    BG_BASE = "#16171f"
    BG_SURFACE = "#1e2030"
    BG_SURFACE_ALT = "#262940"
    BG_RAISED = "#2e3150"
    BORDER = "#3a3d5c"
    BORDER_FOCUS = "#14b8a6"
    TEXT_PRIMARY = "#e6e8f2"
    TEXT_SECONDARY = "#9aa0b8"
    TEXT_DISABLED = "#5c6080"
    ACCENT = "#14b8a6"
    ACCENT_HOVER = "#0d9488"
    ACCENT_PRESSED = "#0f766e"
    ACCENT_TEXT = "#04211e"
    SUCCESS = "#4ade80"
    WARNING = "#fbbf24"
    ERROR = "#f87171"
    INFO = "#38bdf8"


# Forward declarations for THEMES that reference Palette above
# (Palette must be defined before THEMES dict uses its values)


@dataclass(frozen=True)
class ThemePalette:
    """Complete color palette for a theme."""
    name: str
    bg_base: str
    bg_surface: str
    bg_surface_alt: str
    bg_raised: str
    border: str
    border_focus: str
    text_primary: str
    text_secondary: str
    text_disabled: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    success: str
    warning: str
    error: str
    info: str
    # Theme-specific extras
    font_family: str = '"Segoe UI", "SF Pro Text", "DejaVu Sans", sans-serif'
    font_mono: str = '"Consolas", "Courier New", monospace'
    font_size: str = "13px"
    border_radius: str = "6px"
    # Selection colors (text selection in inputs, list/table item selection).
    # Default to accent/accent_text so themes with good contrast need no
    # override. Themes where text_primary == accent (e.g. Retro Console's
    # green-on-green) MUST override these to a contrasting pair, otherwise
    # selected items become invisible.
    selection_bg: str = ""   # falls back to accent when empty
    selection_text: str = ""  # falls back to accent_text when empty
    # Glow effect (for synthwave)
    glow: bool = False
    # Uppercase text (for retro console)
    uppercase: bool = False
    # Grid background (for synthwave)
    grid_bg: bool = False
    # Heading font family (Libre Franklin for Modern Dark)
    font_heading: str = ""
    # CTA gradient (orange) for primary action buttons — empty = no gradient
    cta_gradient: str = ""
    # Surface variant for floating bars / cards visible over ambient
    surface_lowest: str = ""  # falls back to bg_base when empty
    surface_low: str = ""     # falls back to bg_surface when empty
    surface_high: str = ""    # falls back to bg_surface_alt when empty
    surface_highest: str = "" # falls back to bg_raised when empty
    # Panel translucency: 255 = fully opaque, < 255 = translucent.
    # When < 255, generate_qss() emits rgba() colors for panel backgrounds.
    panel_alpha: int = 255


# ============================================================
# THEME DEFINITIONS
# ============================================================

DARK = ThemePalette(
    name="dark",
    bg_base="#16171f",
    bg_surface="#1e2030",
    bg_surface_alt="#262940",
    bg_raised="#2e3150",
    border="#3a3d5c",
    border_focus="#14b8a6",
    text_primary="#e6e8f2",
    text_secondary="#9aa0b8",
    text_disabled="#5c6080",
    accent="#14b8a6",
    accent_hover="#0d9488",
    accent_pressed="#0f766e",
    accent_text="#04211e",
    success="#4ade80",
    warning="#fbbf24",
    error="#f87171",
    info="#38bdf8",
)

LIGHT = ThemePalette(
    name="light",
    bg_base="#f5f6fa",
    bg_surface="#ffffff",
    bg_surface_alt="#eef0f5",
    bg_raised="#e0e3eb",
    border="#d0d4de",
    border_focus="#0d9488",
    text_primary="#1a1d2e",
    text_secondary="#6b7280",
    text_disabled="#b0b4c0",
    accent="#0d9488",
    accent_hover="#0f766e",
    accent_pressed="#0d6b66",
    accent_text="#ffffff",
    success="#16a34a",
    warning="#d97706",
    error="#dc2626",
    info="#0284c7",
)

SYNTHWAVE = ThemePalette(
    name="synthwave",
    bg_base="#0d0221",
    bg_surface="#1a0b2e",
    bg_surface_alt="#2d1b4e",
    bg_raised="#3d2562",
    border="#5c2d8f",
    border_focus="#ff00ff",
    text_primary="#e0e0ff",
    text_secondary="#b090d0",
    text_disabled="#604080",
    accent="#ff00ff",
    accent_hover="#cc00cc",
    accent_pressed="#990099",
    accent_text="#0d0221",
    success="#00ff88",
    warning="#ffaa00",
    error="#ff0055",
    info="#00ddff",
    font_mono='"Consolas", "Courier New", monospace',
    glow=True,
    grid_bg=True,
)

RETRO_CONSOLE = ThemePalette(
    name="retro-console",
    bg_base="#0f380f",
    bg_surface="#1a4a1a",
    bg_surface_alt="#2a5a2a",
    bg_raised="#3a6a3a",
    border="#5a8a5a",
    border_focus="#9bbc0f",
    text_primary="#9bbc0f",
    text_secondary="#6a8a3a",
    text_disabled="#3a5a2a",
    accent="#9bbc0f",
    accent_hover="#8aaa0d",
    accent_pressed="#7a9a0b",
    accent_text="#0f380f",
    success="#9bbc0f",
    warning="#e0c020",
    error="#c02020",
    info="#4080c0",
    font_family='"Courier New", "Consolas", monospace',
    font_mono='"Courier New", "Consolas", monospace',
    font_size="12px",
    border_radius="0px",
    uppercase=True,
    # Override selection colors: in this theme text_primary == accent == #9bbc0f,
    # so the default accent/accent_text selection would render selected text
    # invisible (light-green text on a light-green background). Use a darker
    # green background with the bright accent color as the text instead.
    selection_bg="#3a6a3a",
    selection_text="#9bbc0f",
)

# ===========================================================================
# Modern Dark — based on speechstudio_modern_dark/DESIGN.md
# ===========================================================================
# Exact colors from the HTML reference design system.
# The accent is #d0bcff (lighter purple) used for UI selection/focus/active.
# The #8B5CF6 purple is used in the ambient shader, NOT the UI accent.
# The orange gradient (#F97316 → #EA580C) is reserved for CTA buttons only.
MODERN_DARK = ThemePalette(
    name="modern-dark",
    bg_base="#0F0F0F",          # DESIGN.md: bg-base
    bg_surface="#1A1A1A",       # DESIGN.md: bg-surface
    bg_surface_alt="#242424",   # DESIGN.md: bg-surface-alt
    bg_raised="#2E2E2E",        # DESIGN.md: bg-raised
    border="#333333",           # DESIGN.md: border-subtle
    border_focus="#d0bcff",     # DESIGN.md: primary (lighter purple)
    text_primary="#F5F5F5",     # DESIGN.md: text-primary
    text_secondary="#A0A0A0",   # DESIGN.md: text-secondary
    text_disabled="#555555",    # DESIGN.md: text-disabled
    accent="#d0bcff",           # DESIGN.md: primary — UI selection/focus
    accent_hover="#a078ff",     # DESIGN.md: primary-container
    accent_pressed="#6d3bd7",   # DESIGN.md: inverse-primary
    accent_text="#3c0091",      # DESIGN.md: on-primary
    success="#10B981",          # DESIGN.md: status-success
    warning="#F59E0B",          # DESIGN.md: status-warning
    error="#EF4444",            # DESIGN.md: status-error
    info="#3B82F6",
    # Typography — DESIGN.md typography tokens
    font_family='"Inter", "Segoe UI", "SF Pro Text", "DejaVu Sans", sans-serif',
    font_heading='"Libre Franklin", "Segoe UI", sans-serif',
    font_mono='"JetBrains Mono", "Consolas", "Courier New", monospace',
    font_size="13px",           # DESIGN.md: body-md
    # Shapes — DESIGN.md rounded tokens
    border_radius="8px",        # default for buttons/inputs (rounded-lg)
    # Selection — use accent/accent_text (good contrast)
    selection_bg="#353534",     # surface-container-highest
    selection_text="#F5F5F5",
    # CTA gradient — orange, reserved for Generate/Play buttons
    cta_gradient='qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #F97316, stop:1 #EA580C)',
    # Surface variants from DESIGN.md
    surface_lowest="#0e0e0e",   # surface-container-lowest (floating bars)
    surface_low="#1c1b1b",      # surface-container-low (timeline transport)
    surface_high="#2a2a2a",     # surface-container-high (modal headers)
    surface_highest="#353534",  # surface-container-highest (active nav, table hover)
    # Translucent panels — allows the ambient background to show through
    panel_alpha=200,            # 78% opaque — ambient glow subtly visible
)

THEMES: Dict[str, ThemePalette] = {
    "dark": DARK,
    "light": LIGHT,
    "synthwave": SYNTHWAVE,
    "retro-console": RETRO_CONSOLE,
    "modern-dark": MODERN_DARK,
}

DEFAULT_THEME = "modern-dark"


# ============================================================
# QSS GENERATION
# ============================================================

def _hex_to_rgba(hex_color: str, alpha: int) -> str:
    """Convert a #RRGGBB hex color to an rgba(r, g, b, a) CSS string.

    If alpha is 255, returns the hex color unchanged (fully opaque).
    """
    if alpha >= 255 or not hex_color.startswith("#"):
        return hex_color
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    a = alpha / 255.0
    return "rgba({0}, {1}, {2}, {3})".format(r, g, b, a)


def generate_qss(p: ThemePalette) -> str:
    """Generate a QSS stylesheet from a ThemePalette."""

    glow_shadow = ""
    if p.glow:
        glow_shadow = "box-shadow: 0 0 8px {accent};".format(accent=p.accent)

    text_transform = "text-transform: uppercase;" if p.uppercase else ""

    grid_bg = ""
    if p.grid_bg:
        grid_bg = """
            background-image:
                linear-gradient(rgba(255,0,255,0.06) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255,0,255,0.06) 1px, transparent 1px);
            background-size: 20px 20px;
        """

    qss = """
    * {{
        font-family: {font_family};
        font-size: {font_size};
        color: {text_primary};
        outline: none;
    }}

    QWidget {{
        background-color: transparent;
    }}

    QMainWindow, QDialog {{
        background-color: transparent;
    }}

    /* ---- Menus — translucent surface for Modern Dark ---- */
    QMenuBar {{
        background-color: {bg_surface_rgba};
        border-bottom: 1px solid {border};
        padding: 2px;
    }}
    QMenuBar::item {{
        background-color: transparent;
        padding: 6px 12px;
        border-radius: {border_radius};
        {text_transform}
    }}
    QMenuBar::item:selected {{
        background-color: {bg_surface_alt};
    }}
    QMenuBar::item:pressed {{
        background-color: {bg_raised};
    }}
    QMenu {{
        background-color: {bg_surface};
        border: 1px solid {border};
        border-radius: {border_radius};
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 24px 6px 16px;
        border-radius: {border_radius};
    }}
    QMenu::item:selected {{
        background-color: {bg_surface_alt};
    }}
    QMenu::separator {{
        height: 1px;
        background-color: {border};
        margin: 4px 8px;
    }}

    /* ---- Status bar ---- */
    QStatusBar {{
        background-color: {bg_surface_rgba};
        border-top: 1px solid {border};
        color: {text_secondary};
    }}
    QStatusBar::item {{ border: none; }}

    /* ---- Scrollbars — thin, rounded (HTML ref: 8px, rounded-full) ---- */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
        margin: 0;
    }}
    QScrollBar::handle:vertical,
    QScrollBar::handle:horizontal {{
        background: rgba(51, 51, 51, 0.5);
        border-radius: 4px;
        min-height: 30px;
        min-width: 30px;
    }}
    QScrollBar::handle:vertical:hover,
    QScrollBar::handle:horizontal:hover {{
        background: rgba(85, 85, 85, 0.8);
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        height: 0; width: 0; background: none;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{
        background: transparent;
    }}

    /* ---- Buttons ---- */
    QPushButton {{
        background-color: {bg_raised};
        border: 1px solid {border};
        border-radius: {border_radius};
        padding: 7px 14px;
        color: {text_primary};
        {text_transform}
    }}
    QPushButton:hover {{
        border: 1px solid {border_focus};
    }}
    QPushButton:pressed {{
        background-color: {bg_surface_alt};
    }}
    QPushButton:disabled {{
        color: {text_disabled};
        background-color: {bg_surface};
    }}
    QPushButton[accent="true"] {{
        background-color: {accent};
        color: {accent_text};
        border: 1px solid {accent};
        font-weight: 600;
        {glow_shadow}
    }}
    QPushButton[accent="true"]:hover {{
        background-color: {accent_hover};
        border: 1px solid {accent_hover};
    }}
    QPushButton[accent="true"]:pressed {{
        background-color: {accent_pressed};
    }}
    /* CTA (orange gradient) — reserved for Generate/Play actions */
    QPushButton[cta="true"] {{
        background: {cta_gradient};
        color: #FFFFFF;
        border: none;
        border-radius: 8px;
        font-weight: 700;
        font-size: 14px;
        padding: 12px 24px;
        letter-spacing: 0.5px;
    }}
    QPushButton[cta="true"]:hover {{
        opacity: 0.9;
    }}
    QPushButton[cta="true"]:pressed {{
        opacity: 0.8;
    }}
    QPushButton[cta="true"]:disabled {{
        background-color: {bg_raised};
        color: {text_disabled};
    }}

    /* ---- Inputs ---- */
    QLineEdit, QComboBox {{
        background-color: {bg_surface_alt_rgba};
        border: 1px solid {border};
        border-radius: {border_radius};
        padding: 6px 8px;
        selection-background-color: {selection_bg};
        selection-color: {selection_text};
    }}
    /* Text editors — translucent so ambient shows through */
    QPlainTextEdit, QTextEdit {{
        background-color: {bg_surface_rgba};
        border: 1px solid {border};
        border-radius: {border_radius};
        padding: 6px 8px;
        selection-background-color: {selection_bg};
        selection-color: {selection_text};
    }}
    /* QSpinBox/QDoubleSpinBox need less right-padding so the up/down
       arrow buttons have room to render. Too much padding hides the
       up-arrow button and makes it unclickable. */
    QSpinBox, QDoubleSpinBox {{
        background-color: {bg_surface_alt};
        border: 1px solid {border};
        border-radius: {border_radius};
        padding: 4px 2px 4px 8px;
        selection-background-color: {selection_bg};
        selection-color: {selection_text};
    }}
    /* Make the spin buttons visible and the up/down arrows clearly drawn. */
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        background-color: {bg_raised};
        border: none;
        width: 18px;
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
        background-color: {border};
    }}
    QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
    QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {{
        background-color: {bg_surface_alt};
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        width: 10px;
        height: 10px;
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        width: 10px;
        height: 10px;
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
    QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {border_focus};
    }}

    QComboBox::drop-down {{
        border: none;
        width: 22px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {bg_surface};
        border: 1px solid {border};
        border-radius: {border_radius};
        selection-background-color: {bg_surface_alt};
        outline: none;
    }}

    /* ---- Sliders — DESIGN.md: 4px track, white thumb, purple fill ---- */
    QSlider::groove:horizontal {{
        height: 4px;
        background: {surface_highest_hex};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {accent};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: #FFFFFF;
        border: none;
        width: 12px;
        height: 12px;
        margin: -4px 0;
        border-radius: 6px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {accent};
    }}

    /* ---- Tabs — DESIGN.md: label-caps style, purple active ---- */
    QTabWidget::pane {{
        border: 1px solid {border};
        border-radius: 12px;
        background: {bg_surface_rgba};
    }}
    QTabBar::tab {{
        background: transparent;
        color: {text_secondary};
        padding: 8px 16px;
        border: none;
        border-bottom: 2px solid transparent;
        font-size: 11px;
        font-weight: 600;
        {text_transform}
    }}
    QTabBar::tab:selected {{
        color: {accent};
        border-bottom: 2px solid {accent};
    }}
    QTabBar::tab:hover:!selected {{
        color: {text_primary};
    }}

    /* ---- Group boxes — DESIGN.md: 10px radius (rounded-xl for cards) ---- */
    QGroupBox {{
        background-color: {bg_surface_rgba};
        border: 1px solid {border};
        border-radius: 10px;
        margin-top: 14px;
        padding-top: 12px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        padding: 2px 8px;
        /* Give the title a background matching the groupbox surface so
           it doesn't "float" on the transparent container background.
           Without this, the title text sits on the panel's base bg and
           looks detached from the groupbox card. */
        background-color: {bg_surface_alt_rgba};
        border-radius: 4px;
        color: {text_secondary};
        font-size: 12px;
        font-weight: 600;
        {text_transform}
    }}

    /* ---- Labels ---- */
    QLabel {{ background-color: transparent; }}
    QLabel[role="title"] {{
        font-size: 22px;
        font-weight: 700;
        color: {text_primary};
    }}
    QLabel[role="subtitle"] {{
        font-size: 14px;
        color: {text_secondary};
    }}
    QLabel[role="hint"] {{
        color: {text_secondary};
    }}

    /* ---- Tool buttons ---- */
    QToolButton {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: {border_radius};
        padding: 6px 8px;
        color: {text_secondary};
    }}
    QToolButton:hover {{
        background-color: {bg_surface_alt};
        border: 1px solid {border};
    }}
    QToolButton:pressed, QToolButton:checked {{
        background-color: {bg_raised};
        color: {text_primary};
    }}

    /* ---- Splitters ---- */
    /* The splitter handle is the gutter between panels. Background is
       transparent so the ambient shows through. The handle width is set
       via QSplitter.setHandleWidth() in MainWindow (6px). No margins —
       margins create extra invisible space that widens the gap between
       panels. NO border lines — they appeared as thin vertical stripes
       on the editor edges and were visually distracting. */
    QSplitter {{
        background-color: transparent;
    }}
    QSplitter::handle {{
        background-color: transparent;
        border: none;
    }}
    QSplitter::handle:horizontal {{
        width: 6px;
        margin: 0;
    }}
    QSplitter::handle:horizontal:hover {{
        background-color: rgba(208, 188, 255, 0.1);
    }}
    QSplitter::handle:vertical {{
        height: 6px;
        margin: 0;
    }}
    QSplitter::handle:vertical:hover {{
        background-color: rgba(208, 188, 255, 0.1);
    }}

    /* (Tabs already styled above with Sliders section) */

    /* ---- Checkboxes ---- */
    QCheckBox {{
        color: {text_primary};
        spacing: 6px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {border};
        border-radius: 3px;
        background: {bg_surface_alt};
    }}
    QCheckBox::indicator:checked {{
        background: {accent};
        border: 1px solid {accent};
    }}

    /* ---- Radio buttons ---- */
    QRadioButton {{
        color: {text_primary};
        spacing: 6px;
    }}
    QRadioButton::indicator {{
        width: 14px;
        height: 14px;
        border: 2px solid {border};
        border-radius: 7px;
        background: {bg_surface_alt};
    }}
    QRadioButton::indicator:checked {{
        background: {accent};
        border: 2px solid {accent};
    }}

    /* ---- Progress bar ---- */
    QProgressBar {{
        background-color: {bg_surface_alt};
        border: 1px solid {border};
        border-radius: {border_radius};
        text-align: center;
        color: {text_primary};
    }}
    QProgressBar::chunk {{
        background-color: {accent};
        border-radius: {border_radius};
    }}

    /* ---- Table ---- */
    QTableWidget {{
        background-color: {bg_base};
        alternate-background-color: {bg_surface};
        border: 1px solid {border};
        border-radius: {border_radius};
        gridline-color: {border};
    }}
    QTableWidget::item {{
        padding: 4px 8px;
    }}
    QTableWidget::item:selected {{
        background-color: {selection_bg};
        color: {selection_text};
    }}
    QHeaderView::section {{
        background-color: {bg_surface};
        color: {text_secondary};
        padding: 4px 8px;
        border: 1px solid {border};
        {text_transform}
        font-weight: 600;
    }}

    /* ---- Tooltips ---- */
    QToolTip {{
        background-color: {bg_surface};
        color: {text_primary};
        border: 1px solid {border};
        border-radius: 4px;
        padding: 4px 8px;
    }}

    /* ---- Frame — DESIGN.md: 12px radius (rounded-xl for panels) ---- */
    QFrame[role="panel"] {{
        background-color: {bg_surface_rgba};
        border: 1px solid {border};
        border-radius: 12px;
    }}
    QFrame[role="voice-card"] {{
        background-color: {bg_surface_alt_rgba};
        border: 1px solid {border};
        border-radius: 12px;
    }}

    /* ---- Table — DESIGN.md: compact rows, surface-variant hover ---- */
    QTableWidget {{
        background-color: transparent;
        alternate-background-color: {bg_surface_rgba};
        border: 1px solid {border};
        border-radius: 12px;
        gridline-color: transparent;
    }}
    QTableWidget::item {{
        padding: 6px 12px;
        border-bottom: 1px solid {border};
    }}
    QTableWidget::item:hover {{
        background-color: {surface_highest_hex};
    }}
    QTableWidget::item:selected {{
        background-color: {surface_highest_hex};
        color: {text_primary};
        border-left: 2px solid {accent};
    }}
    QHeaderView::section {{
        background-color: {bg_surface_alt_rgba};
        color: {text_secondary};
        padding: 8px 12px;
        border: none;
        border-bottom: 1px solid {border};
        font-size: 11px;
        font-weight: 600;
        {text_transform}
    }}

    /* ---- Progress bar — DESIGN.md: thin track, purple fill ---- */
    QProgressBar {{
        background-color: {surface_highest_hex};
        border: none;
        border-radius: 3px;
        text-align: center;
        color: {text_primary};
        font-size: 10px;
    }}
    QProgressBar::chunk {{
        background-color: {accent};
        border-radius: 3px;
    }}

    /* ---- Emotion icon buttons — DESIGN.md: rounded-lg, purple active ---- */
    QPushButton[role="emotion"] {{
        background-color: {bg_surface_alt_rgba};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 6px 4px;
        font-size: 22px;
        min-width: 52px;
        min-height: 56px;
    }}
    QPushButton[role="emotion"]:checked {{
        background-color: rgba(160, 120, 255, 0.1);
        border: 2px solid {accent};
        color: {accent};
    }}
    QPushButton[role="emotion"]:hover:!checked {{
        background-color: {bg_raised};
        border: 1px solid {accent};
    }}

    /* ---- Generate button — DESIGN.md: orange gradient CTA ---- */
    QPushButton#generate-main {{
        background: {cta_gradient};
        color: #FFFFFF;
        border: none;
        border-radius: 8px;
        padding: 14px 24px;
        font-size: 14px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    QPushButton#generate-main:hover {{
        opacity: 0.9;
    }}
    QPushButton#generate-main:pressed {{
        opacity: 0.8;
    }}
    QPushButton#generate-main:disabled {{
        background-color: {bg_raised};
        color: {text_disabled};
    }}

    /* ---- Message boxes (feedback/confirmation dialogs) — P3.44.3 ----
       Central polish for EVERY QMessageBox in the app (project/scene
       saved, generation errors, batch confirmations, ...): zero call
       site changes, all variants styled at once.
       Same functionality, same semantic icons, same buttons — this
       only gives the boxes a proper GUINEO surface (the generic
       "QDialog {{ background: transparent }}" rule above left them
       without a defined background), a calmer title/details
       hierarchy and more comfortable metrics.
       Card language: 12px radius (Radii.md, medium containers). */
    QMessageBox {{
        background-color: {bg_surface};
        border: 1px solid {border};
        border-radius: 12px;
    }}
    QMessageBox QLabel {{
        background-color: transparent;
        color: {text_primary};
    }}
    /* Message title: one step above body size, semibold. */
    #qt_msgbox_label {{
        font-size: 15px;
        font-weight: 600;
        padding: 2px 12px 2px 0px;
    }}
    /* Secondary/details text: quieter colour, vertical rhythm. */
    #qt_msgbox_informativelabel {{
        color: {text_secondary};
        padding: 12px 12px 2px 0px;
    }}
    /* Icon column: comfortable inset, breathing room to the text. */
    #qt_msgboxex_icon_label {{
        padding: 8px 14px 8px 6px;
    }}
    /* Detailed text (rarely used): recessed surface. */
    #qt_msgbox_detail {{
        background-color: {bg_surface_alt};
        border: 1px solid {border};
        border-radius: {border_radius};
        color: {text_secondary};
        selection-background-color: {selection_bg};
        selection-color: {selection_text};
    }}
    /* Buttons: uniform, comfortable, never oversized. */
    QMessageBox QPushButton {{
        min-width: 96px;
        padding: 8px 20px;
    }}

    /* ---- Dock widget ---- */
    QDockWidget {{
        titlebar-close-icon: none;
        titlebar-normal-icon: none;
    }}
    QDockWidget::title {{
        background-color: {bg_surface};
        padding: 4px 8px;
        border-bottom: 1px solid {border};
        {text_transform}
        font-weight: 600;
    }}
    """.format(
        font_family=p.font_family,
        font_size=p.font_size,
        text_primary=p.text_primary,
        text_secondary=p.text_secondary,
        text_disabled=p.text_disabled,
        bg_base=p.bg_base,
        bg_surface=p.bg_surface,
        bg_surface_alt=p.bg_surface_alt,
        bg_raised=p.bg_raised,
        border=p.border,
        border_focus=p.border_focus,
        accent=p.accent,
        accent_hover=p.accent_hover,
        accent_pressed=p.accent_pressed,
        accent_text=p.accent_text,
        success=p.success,
        warning=p.warning,
        error=p.error,
        info=p.info,
        border_radius=p.border_radius,
        text_transform=text_transform,
        glow_shadow=glow_shadow,
        # Selection colors: fall back to accent/accent_text when the theme
        # does not override them (preserves existing behavior for dark/light/
        # synthwave). Themes where text_primary == accent MUST override.
        selection_bg=p.selection_bg or p.accent,
        selection_text=p.selection_text or p.accent_text,
        # Translucent surface variants (for themes with panel_alpha < 255)
        bg_surface_rgba=_hex_to_rgba(p.bg_surface, p.panel_alpha),
        bg_surface_alt_rgba=_hex_to_rgba(p.bg_surface_alt, p.panel_alpha),
        bg_raised_rgba=_hex_to_rgba(p.bg_raised, p.panel_alpha),
        # Surface highest (for slider track, table hover) — opaque hex
        surface_highest_hex=p.surface_highest or p.bg_raised,
        # CTA gradient (empty string = no gradient, falls back to accent)
        cta_gradient=p.cta_gradient or p.accent,
    )

    # Add grid background to text editors for synthwave
    if p.grid_bg:
        qss += """
        QPlainTextEdit, QTextEdit {{
            {grid_bg}
        }}
        """.format(grid_bg=grid_bg)

    return qss


def apply_theme(app: QApplication, theme_name: str = DEFAULT_THEME) -> ThemePalette:
    """Apply a theme by name. Returns the applied ThemePalette."""
    p = THEMES.get(theme_name, THEMES[DEFAULT_THEME])

    # Apply QSS
    qss = generate_qss(p)
    app.setStyleSheet(qss)

    # Apply QPalette for native dialogs
    sel_bg = p.selection_bg or p.accent
    sel_text = p.selection_text or p.accent_text
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(p.bg_base))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(p.text_primary))
    palette.setColor(QPalette.ColorRole.Base, QColor(p.bg_surface_alt))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(p.bg_surface))
    palette.setColor(QPalette.ColorRole.Text, QColor(p.text_primary))
    palette.setColor(QPalette.ColorRole.Button, QColor(p.bg_raised))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(p.text_primary))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(sel_bg))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(sel_text))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.bg_surface))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(p.text_primary))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(p.text_secondary))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(p.text_disabled))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(p.text_disabled))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(p.text_disabled))
    app.setPalette(palette)

    # Update the legacy Palette class attributes to match the active theme.
    # Many UI components (toolbar, batch dialog, narration editor) use
    # Palette.ACCENT / Palette.ACCENT_TEXT etc. for inline stylesheets.
    # Without this, they always show the Dark theme colors regardless of
    # the active theme, making buttons hard to read in other themes.
    Palette.BG_BASE = p.bg_base
    Palette.BG_SURFACE = p.bg_surface
    Palette.BG_SURFACE_ALT = p.bg_surface_alt
    Palette.BG_RAISED = p.bg_raised
    Palette.BORDER = p.border
    Palette.BORDER_FOCUS = p.border_focus
    Palette.TEXT_PRIMARY = p.text_primary
    Palette.TEXT_SECONDARY = p.text_secondary
    Palette.TEXT_DISABLED = p.text_disabled
    Palette.ACCENT = p.accent
    Palette.ACCENT_HOVER = p.accent_hover
    Palette.ACCENT_PRESSED = p.accent_pressed
    Palette.ACCENT_TEXT = p.accent_text
    Palette.SUCCESS = p.success
    Palette.WARNING = p.warning
    Palette.ERROR = p.error
    Palette.INFO = p.info

    return p
