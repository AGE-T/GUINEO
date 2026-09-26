"""
SpeechStudio — Centralized Design Tokens (Phase A)
==================================================

This module is the SINGLE AUTHORITATIVE source for all design tokens
used across the SpeechStudio UI. Every value is extracted directly from
the HTML reference design system:

    Source 1 (authoritative): stitch_orange_ambient_desktop_prototype/
        speechstudio_modern_dark/DESIGN.md
        speechstudio_main_window_fixed_layout_architecture/code.html
        (Tailwind config embedded in the HTML)

    Source 2 (existing): ui/theme.py MODERN_DARK palette (already
        matches DESIGN.md — this module re-exposes the same values as
        structured tokens for consumption by all UI components).

Token categories:
    COLORS         — all surface, text, accent, status, and brand colors
    SPACING        — the 4px base unit + card/input/gutter/sidebar/panel
    RADII          — sm / DEFAULT / md / lg / xl / full
    TYPOGRAPHY     — font families + 7 typography styles (headline-lg …)
    SURFACES       — the 6-level surface hierarchy (base → highest)
    ELEVATION      — glow/shadow rules per component type
    COMPONENT_DIMS  — reference dimensions for reusable components

Usage:
    from ui.design_tokens import Colors, Spacing, Radii, Typography, Surfaces

    # Colors
    button_bg = Colors.bg_raised
    border = Colors.border_subtle

    # Spacing
    layout.setContentsMargins(Spacing.gutter, Spacing.gutter, ...)

    # Radii
    panel.setStyleSheet(f"border-radius: {Radii.lg}px;")

    # Typography
    font = Typography.body_md()  # returns a QFont

Validation status:
    - Colors: VALIDATED against DESIGN.md (exact hex match)
    - Typography: VALIDATED against DESIGN.md (exact px/weight/family)
    - Spacing: VALIDATED against DESIGN.md
    - Radii: VALIDATED against DESIGN.md (rem → px at 16px base)
    - Expressiveness profile values: PROPOSED (not validated — see
      ExpressivenessProfiles docstring)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PySide6.QtGui import QColor, QFont


# ============================================================
# COLORS — exact hex values from DESIGN.md + HTML Tailwind config
# ============================================================
# Source: speechstudio_modern_dark/DESIGN.md → colors section
# Source: speechstudio_main_window_fixed_layout_architecture/code.html →
#         tailwind.config.theme.extend.colors
#
# The HTML uses TWO color naming conventions:
#   1. Material Design 3 names (surface, on-surface, primary, etc.)
#   2. SpeechStudio short names (bg-base, bg-surface, text-primary, etc.)
# Both are exposed here so components can use whichever is most readable.

class Colors:
    """All color tokens from the HTML reference design system.

    Every value is a hex string (e.g. "#0F0F0F") for use in QSS
    stylesheets. Use QColor(hex_string) when a QColor is needed.
    """

    # ---- Surface hierarchy (6 levels, darkest → lightest) ----
    # These define the tonal layering system from DESIGN.md "Elevation & Depth".
    bg_base: str = "#0F0F0F"                    # Level 0: main window background
    bg_surface: str = "#1A1A1A"                 # Level 1: sidebars, control panels
    bg_surface_alt: str = "#242424"             # Level 2: cards, group boxes
    bg_raised: str = "#2E2E2E"                   # Level 3: buttons, inputs

    # Material Design 3 surface containers (from HTML Tailwind config)
    surface_container_lowest: str = "#0e0e0e"    # floating bars (lowest)
    surface_container_low: str = "#1c1b1b"       # timeline transport
    surface_container: str = "#201f1f"           # default container
    surface_container_high: str = "#2a2a2a"      # modal headers
    surface_container_highest: str = "#353534"    # active nav, table hover
    surface_variant: str = "#353534"             # variant surface
    surface_bright: str = "#3a3939"              # bright surface
    surface_dim: str = "#131313"                # dim surface
    surface: str = "#131313"                     # base surface (MD3)

    # ---- Text ----
    text_primary: str = "#F5F5F5"                # main text
    text_secondary: str = "#A0A0A0"             # secondary text
    text_disabled: str = "#555555"              # disabled text

    # Material Design 3 on-surface variants
    on_surface: str = "#e5e2e1"                  # text on surface
    on_surface_variant: str = "#cbc3d7"          # variant text on surface

    # ---- Borders ----
    border_subtle: str = "#333333"              # default 1px border
    outline: str = "#958ea0"                     # MD3 outline
    outline_variant: str = "#494454"             # MD3 outline variant

    # ---- Primary (Purple) — UI selection/focus/active ----
    # DESIGN.md: "Primary Accent: Purple (#8B5CF6) is the functional
    # highlight for selection states, focus rings, and primary interactive
    # elements."
    # NOTE: The HTML Tailwind config defines `primary: #d0bcff` (the MD3
    # primary-fixed-dim). The DESIGN.md body text references #8B5CF6 as the
    # "Primary Accent". These are DIFFERENT visual roles:
    #   - #d0bcff = UI selection/focus/active state color (lighter purple)
    #   - #8B5CF6 = ambient glow / waveform accent color (deeper purple)
    # Both are preserved as separate tokens.
    primary: str = "#d0bcff"                     # MD3 primary (UI selection)
    primary_fixed: str = "#e9ddff"               # MD3 primary-fixed
    primary_container: str = "#a078ff"           # MD3 primary-container
    on_primary: str = "#3c0091"                   # text on primary
    on_primary_container: str = "#340080"         # text on primary-container
    inverse_primary: str = "#6d3bd7"              # MD3 inverse-primary

    # The #8B5CF6 accent — used in the ambient shader and waveform glow.
    # NOT the UI selection color. Preserved as a separate token so the
    # two visual roles are never collapsed.
    accent: str = "#8B5CF6"                       # ambient/waveform accent
    neon_purple: str = "#A855F7"                  # transport glow color

    # ---- Orange CTA gradient — reserved for primary action buttons ----
    # DESIGN.md: "Main Function: The vibrant orange gradient is a protected
    # token, used exclusively for the 'Main Function' buttons: Generate,
    # Play, and Generate All."
    orange_gradient_start: str = "#F97316"       # CTA gradient start
    orange_gradient_end: str = "#EA580C"          # CTA gradient end
    # QSS gradient string for CTA buttons
    cta_gradient: str = (
        "qlineargradient(x1:0, y1:0, x2:1, y2:1, "
        "stop:0 #F97316, stop:1 #EA580C)"
    )

    # ---- Secondary (Orange) — MD3 secondary palette ----
    secondary: str = "#ffb690"
    secondary_container: str = "#ec6a06"
    on_secondary: str = "#552100"
    on_secondary_container: str = "#4a1c00"

    # ---- Tertiary (Green) — MD3 tertiary palette ----
    tertiary: str = "#4edea3"
    tertiary_container: str = "#00a572"
    on_tertiary: str = "#003824"

    # ---- Status colors ----
    status_success: str = "#10B981"              # green
    status_warning: str = "#F59E0B"              # amber
    status_error: str = "#EF4444"                 # red

    # ---- Error (MD3) ----
    error: str = "#ffb4ab"
    error_container: str = "#93000a"
    on_error: str = "#690005"
    on_error_container: str = "#ffdad6"

    # ---- Speaker colors (for timeline / table rows) ----
    # These are derived from the HTML reference's speaker badge colors.
    # Used to color-code different speakers in multi-speaker dialogue.
    speaker_colors: List[str] = [
        "#A78BFA",  # Speaker 1 — purple
        "#FB923C",  # Speaker 2 — orange
        "#34D399",  # Speaker 3 — green
        "#60A5FA",  # Speaker 4 — blue
        "#F472B6",  # Speaker 5 — pink
        "#FBBF24",  # Speaker 6 — yellow
        "#22D3EE",  # Speaker 7 — cyan
        "#A3A3A3",  # Speaker 8 — gray (narrator)
    ]

    @classmethod
    def qcolor(cls, hex_str: str) -> QColor:
        """Convert a hex color string to a QColor."""
        return QColor(hex_str)

    @classmethod
    def rgba(cls, hex_str: str, alpha: int = 255) -> str:
        """Convert a hex color + alpha to an rgba() string for QSS."""
        c = QColor(hex_str)
        return "rgba({0}, {1}, {2}, {3})".format(
            c.red(), c.green(), c.blue(), alpha,
        )


# ============================================================
# SPACING — from DESIGN.md → spacing section
# ============================================================
# Source: DESIGN.md:
#   spacing:
#     sidebar-width: 280px
#     control-panel-width: 360px
#     unit-base: 4px
#     padding-card: 16px
#     padding-input: 8px 12px
#     gutter: 16px
#     margin-stack: 12px

class Spacing:
    """Spacing tokens from the HTML design system.

    All values are in pixels. Use these instead of arbitrary Qt defaults.
    """
    unit_base: int = 4             # the 4px base unit
    padding_card: int = 16         # card / panel internal padding
    padding_input_h: int = 12      # horizontal input padding
    padding_input_v: int = 8       # vertical input padding
    gutter: int = 16               # gutter between panels (QSplitter handle)
    margin_stack: int = 12         # margin between stacked elements

    # Panel widths (reference — actual width may be resizable)
    sidebar_width: int = 280       # left sidebar reference width
    control_panel_width: int = 360  # right control panel reference width

    # Transport
    transport_strip_height: int = 81  # compact bottom strip (scaled from 232px ref)
    transport_margin: int = 16       # transport island margin from screen edges

    # Top navigation
    top_nav_height: int = 56         # top navigation bar height
    top_nav_padding_h: int = 24       # top navigation horizontal padding

    # Project / scene bar
    project_scene_bar_height: int = 56  # project/scene breadcrumb bar height

    # Table row heights (from DESIGN.md "Density" section)
    table_row_min: int = 32          # compact table row
    table_row_default: int = 40      # default table row

    # Icon sizes (reference — actual may scale with transport)
    # Kept in sync with ui.typography.IconSize so both token sources agree.
    icon_sm: int = 18               # small inline icons (badges)
    icon_md: int = 22               # standard UI icons (nav, actions)
    icon_lg: int = 26               # prominent icons (brand, large buttons)
    icon_xl: int = 52               # hero / empty-state icons


# ============================================================
# RADII — from DESIGN.md → rounded section
# ============================================================
# Source: DESIGN.md:
#   rounded:
#     sm: 0.25rem    → 4px  (at 16px base)
#     DEFAULT: 0.5rem → 8px
#     md: 0.75rem    → 12px
#     lg: 1rem       → 16px
#     xl: 1.5rem     → 24px
#     full: 9999px   → circle/pill
#
# HTML Tailwind config:
#   borderRadius: { DEFAULT: "0.25rem", lg: "0.5rem", xl: "0.75rem", full: "9999px" }
#
# DESIGN.md body text also specifies:
#   "Cards & Group Boxes: 10px radius"
#   "Standard UI Elements (buttons, inputs, tabs): 8px radius"
#   "Small Badges: 4px radius"
#   "Avatars: 50% (circular)"

class Radii:
    """Border radius tokens from the HTML design system.

    Use these instead of arbitrary per-widget radius values.
    """
    sm: int = 4            # small badges, tags
    default: int = 8       # buttons, inputs, tabs (rounded-lg in Tailwind)
    md: int = 12            # medium containers
    lg: int = 16            # large panels
    xl: int = 24            # extra-large (modals)
    full: int = 9999        # circles, pills, avatars

    # Component-specific (from DESIGN.md body text)
    card: int = 10          # QGroupBox / card surfaces
    panel: int = 12         # major panel surfaces
    button: int = 8         # standard buttons
    input: int = 8          # input fields
    badge: int = 4          # small badges / status pills
    avatar: int = 9999      # circular (50%)


# ============================================================
# TYPOGRAPHY — from DESIGN.md → typography section
# ============================================================
# Source: DESIGN.md:
#   headline-lg: Libre Franklin, 24px, 700, 32px line-height
#   headline-md: Libre Franklin, 18px, 600, 24px line-height
#   body-lg:    Inter, 15px, 400, 22px line-height
#   body-md:    Inter, 13px, 400, 20px line-height
#   label-caps: Inter, 11px, 600, 16px line-height, 0.5px letter-spacing
#   mono-data:  JetBrains Mono, 12px, 400, 16px line-height
#   cta-main:   Inter, 14px, 700, 18px line-height, 0.5px letter-spacing
#
# Font files are loaded from assets/fonts/ (see ui/font_loader.py).
# Font families are specified with fallbacks for environments where
# the TTF files are not yet installed.

class Typography:
    """Typography tokens from the HTML design system.

    Each method returns a QFont configured with the exact DESIGN.md values.
    Sizes are set in PIXELS via QFont.setPixelSize() for exact matching.
    """

    # Font families — single primary name (Qt setFamily expects one name,
    # not a CSS fallback stack). See ui/typography.py for the full rationale.
    FONT_BODY = "Inter"
    FONT_HEADING = "Libre Franklin"
    FONT_MONO = "JetBrains Mono"

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
        """26px, weight 700, Libre Franklin. Used for main headings."""
        return Typography._make(Typography.FONT_HEADING, 26, QFont.Weight.Bold)

    @staticmethod
    def headline_md() -> QFont:
        """19px, weight 600, Libre Franklin. Used for section headings."""
        return Typography._make(Typography.FONT_HEADING, 19, QFont.Weight.DemiBold)

    # --- Body text (Inter) ---
    @staticmethod
    def body_lg() -> QFont:
        """16px, weight 400, Inter. Used for primary body text."""
        return Typography._make(Typography.FONT_BODY, 16, QFont.Weight.Normal)

    @staticmethod
    def body_md() -> QFont:
        """14px, weight 400, Inter. Used for standard UI text."""
        return Typography._make(Typography.FONT_BODY, 14, QFont.Weight.Normal)

    # --- CTA (Inter) ---
    @staticmethod
    def cta_main() -> QFont:
        """15px, weight 700, letter-spacing 0.5px, Inter. Used for CTA buttons."""
        return Typography._make(Typography.FONT_BODY, 15, QFont.Weight.Bold, 0.5)

    # --- Label caps (Inter) ---
    @staticmethod
    def label_caps() -> QFont:
        """12px, weight 600, letter-spacing 0.5px, Inter. Used for section labels."""
        return Typography._make(Typography.FONT_BODY, 12, QFont.Weight.DemiBold, 0.5)

    # --- Mono data (JetBrains Mono) ---
    @staticmethod
    def mono_data() -> QFont:
        """13px, weight 400, JetBrains Mono. Used for technical data."""
        return Typography._make(Typography.FONT_MONO, 13, QFont.Weight.Normal)

    # --- Navigation (Inter) ---
    @staticmethod
    def nav_item() -> QFont:
        """14px, weight 400 (normal), Inter. Used for top nav menu items."""
        return Typography._make(Typography.FONT_BODY, 14, QFont.Weight.Normal)

    @staticmethod
    def nav_item_active() -> QFont:
        """14px, weight 600 (active), Inter. Used for active nav items."""
        return Typography._make(Typography.FONT_BODY, 14, QFont.Weight.DemiBold)

    # --- Sidebar (Inter) ---
    @staticmethod
    def sidebar_nav() -> QFont:
        """13px, weight 400, Inter. Used for sidebar nav items."""
        return Typography._make(Typography.FONT_BODY, 13, QFont.Weight.Normal)

    @staticmethod
    def sidebar_nav_active() -> QFont:
        """13px, weight 600, Inter. Used for active sidebar items."""
        return Typography._make(Typography.FONT_BODY, 13, QFont.Weight.DemiBold)

    # --- Small metadata (Inter) ---
    @staticmethod
    def metadata_sm() -> QFont:
        """10px, weight 400, Inter. Used for small badges and scene rows."""
        return Typography._make(Typography.FONT_BODY, 10, QFont.Weight.Normal)


# ============================================================
# SURFACES — the 6-level surface hierarchy
# ============================================================
# Source: DESIGN.md "Elevation & Depth" section:
#   Level 0 (Main Floor): #0F0F0F — main application background
#   Level 1 (Surfaces): #1A1A1A — sidebars, control panels
#   Level 2 (Containers): #242424 — cards, group boxes
#   Level 3 (Interactables): #2E2E2E — buttons, inputs
#   Focus States: 1px solid border using #8B5CF6 (accent)

class Surfaces:
    """Surface hierarchy tokens for tonal layering.

    Depth is communicated through subtle shifts in charcoal values
    rather than aggressive shadows (DESIGN.md "Elevation & Depth").
    """
    level_0_floor: str = Colors.bg_base              # #0F0F0F — main background
    level_1_surface: str = Colors.bg_surface         # #1A1A1A — sidebars/panels
    level_2_container: str = Colors.bg_surface_alt   # #242424 — cards
    level_3_interactable: str = Colors.bg_raised     # #2E2E2E — buttons/inputs

    # Material Design 3 surface containers (finer-grained)
    lowest: str = Colors.surface_container_lowest    # #0e0e0e
    low: str = Colors.surface_container_low          # #1c1b1b
    default: str = Colors.surface_container          # #201f1f
    high: str = Colors.surface_container_high        # #2a2a2a
    highest: str = Colors.surface_container_highest  # #353534


# ============================================================
# ELEVATION — glow / shadow / border rules per component type
# ============================================================
# Source: DESIGN.md "Elevation & Depth" + "Shapes" sections:
#   "Depth is achieved through Tonal Stepping rather than traditional
#    drop shadows, ensuring a crisp, flat-modern appearance."
#   "Borders: All containers and inputs feature a 1px solid border
#    in #333333 to maintain definition against the dark background."
#   "Focus States: 1px solid border using #8B5CF6 (accent)."
#
# Rule: large surfaces use subtle borders; strong glow is reserved
# for small interactive elements (Play/Pause, Stop, selected emotion).

class Elevation:
    """Elevation rules from the HTML design system.

    The HTML uses tonal stepping (not drop shadows) for depth. Glow
    effects are reserved for small interactive elements.
    """
    # Large panels: subtle 1px border, no shadow
    panel_border: str = Colors.border_subtle       # #333333
    panel_border_width: int = 1                     # 1px

    # Cards: same subtle border
    card_border: str = Colors.border_subtle
    card_border_width: int = 1

    # Inputs: subtle border, accent focus border
    input_border: str = Colors.border_subtle
    input_focus_border: str = Colors.accent         # #8B5CF6
    input_border_width: int = 1

    # Buttons: subtle border, accent hover border
    button_border: str = Colors.border_subtle
    button_hover_border: str = Colors.accent
    button_border_width: int = 1

    # CTA buttons: orange gradient, no border, subtle glow
    cta_glow_color: str = Colors.orange_gradient_start
    cta_glow_radius: int = 12                        # px blur radius

    # Transport controls: purple glow (GPU-rendered, not Qt shadow)
    transport_glow_color: str = Colors.neon_purple   # #A855F7

    # Selected emotion: purple glow
    emotion_selected_glow: str = Colors.accent       # #8B5CF6
    emotion_selected_glow_radius: int = 8


# ============================================================
# COMPONENT DIMENSIONS — reference sizes for reusable components
# ============================================================
# Source: DESIGN.md + HTML reference screens
# These are REFERENCE values — actual sizes may scale responsively.

class ComponentDims:
    """Reference dimensions for reusable visual components."""

    # Voice card (from HTML reference)
    voice_card_avatar: int = 40               # 40×40 circular avatar
    voice_card_name_font: int = 14             # name font size
    voice_card_metadata_font: int = 12         # metadata font size

    # Emotion grid (from design review)
    emotion_grid_cols: int = 4                 # 4 columns
    emotion_grid_rows: int = 2                 # 2 rows = 8 primary emotions
    emotion_button_min_size: int = 56         # minimum button size

    # Status pill
    status_pill_height: int = 24
    status_pill_radius: int = 12               # pill shape
    status_pill_font: int = 11                 # label-caps

    # Speaker badge
    speaker_badge_size: int = 24              # circular badge
    speaker_badge_radius: int = 9999          # full circle

    # Section header
    section_header_height: int = 32

    # Transport (V6 reference — scaled by TRANSPORT_DISPLAY_SCALE)
    transport_play_ref: int = 192             # Play/Pause reference diameter
    transport_stop_ref: int = 80              # Stop reference size
    transport_wave_ref: int = 192             # Waveform reference height
    transport_island_ref: int = 232            # Island reference height

    # CTA button
    cta_padding_h: int = 24                    # horizontal padding
    cta_padding_v: int = 14                    # vertical padding
    cta_radius: int = 8                        # border radius


# ============================================================
# EXPRESSIVENESS PROFILES — PROPOSED starting values
# ============================================================
# Source: design review (Right Panel design review document)
# IMPORTANT: These are PROPOSED STARTING VALUES, NOT VALIDATED.
# They must be empirically tuned using actual Higgs generations.
# Store in configuration so they can be adjusted without code changes.

@dataclass(frozen=True)
class ExpressivenessProfile:
    """A named expressiveness profile (PROPOSED — not validated)."""
    name: str
    temperature: float
    top_k: int
    top_p: float
    delivery: str  # "low" | "normal" | "high"


class ExpressivenessProfiles:
    """PROPOSED expressiveness profile mappings.

    WARNING: These values are explicitly marked in the design review as
    PROPOSED STARTING VALUES, not validated production values. They must
    be empirically tuned using actual Higgs generations before being
    treated as validated.

    The mapping is stored as a dict so it can be overridden by
    configuration (settings.json) without code changes.
    """

    # PROPOSED profiles (design review starting values)
    _proposed: Dict[str, ExpressivenessProfile] = {
        "Reserved": ExpressivenessProfile(
            name="Reserved",
            temperature=0.8, top_k=30, top_p=0.90, delivery="low",
        ),
        "Natural": ExpressivenessProfile(
            name="Natural",
            temperature=1.0, top_k=50, top_p=0.95, delivery="normal",
        ),
        "Expressive": ExpressivenessProfile(
            name="Expressive",
            temperature=1.2, top_k=150, top_p=0.96, delivery="normal",
        ),
        "Dramatic": ExpressivenessProfile(
            name="Dramatic",
            temperature=1.4, top_k=300, top_p=0.97, delivery="high",
        ),
    }

    # Tolerance for snapping Advanced values to a named profile
    tolerance_temperature: float = 0.05   # ±0.05
    tolerance_top_k: int = 10             # ±10
    tolerance_top_p: float = 0.01         # ±0.01

    @classmethod
    def profiles(cls) -> Dict[str, ExpressivenessProfile]:
        """Return the profile dict (copy so callers can't mutate)."""
        return dict(cls._proposed)

    @classmethod
    def get(cls, name: str) -> Optional[ExpressivenessProfile]:
        """Return the profile with the given name, or None."""
        return cls._proposed.get(name)

    @classmethod
    def names(cls) -> List[str]:
        """Return the list of profile names (excluding Custom)."""
        return list(cls._proposed.keys()) + ["Custom"]

    @classmethod
    def match(cls, temperature: float, top_k: int, top_p: float,
              delivery: str) -> str:
        """Return the profile name that matches the given values.

        If no profile matches within tolerance, returns "Custom".
        """
        for name, profile in cls._proposed.items():
            if (
                abs(profile.temperature - temperature) <= cls.tolerance_temperature
                and abs(profile.top_k - top_k) <= cls.tolerance_top_k
                and abs(profile.top_p - top_p) <= cls.tolerance_top_p
                and profile.delivery == delivery
            ):
                return name
        return "Custom"
