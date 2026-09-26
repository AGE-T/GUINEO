"""
SpeechStudio Engine - Character Visual Identity
================================================

Deterministic, stable color assignment for Characters.

Every Character gets a color derived from a hash of its character_id.
The color is STABLE — the same character_id always maps to the same
color, regardless of creation order, project, or session restart.

Design decisions (locked P3.22):
    - 12-color palette — distinguishable, accessible, no adjacent duplicates.
    - Theme-aware: each palette entry has a DARK variant and a LIGHT variant.
      Dark themes use a slightly desaturated/brighter shade (visible on dark
      backgrounds); light themes use a deeper shade (visible on light bg).
    - The color is used for: block gutter accent strip, block properties
      character badge, sidebar character row dot, and (future) timeline.
    - No external dependencies. Pure Python hash + lookup.
    - Backward compatible: unknown/None character_id → neutral gray.

Public API:
    get_character_color(character_id, theme="dark") -> CharacterColor
    get_character_color_hex(character_id, theme="dark") -> str

CharacterColor dataclass:
    hex_main:   primary color (for accents, dots, strips)
    hex_bg:     tinted background (for badges, pills — ~18% main on bg)
    hex_text:   readable text color on hex_bg (white or near-black)
    name:       human-readable color name (e.g. "Coral", "Sage")
"""

from __future__ import annotations
import hashlib
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class CharacterColor:
    """A resolved color for a Character.

    Attributes:
        hex_main:  primary accent color (e.g. "#EF4444").
        hex_bg:    tinted background for badges/pills (semi-transparent feel).
        hex_text:  text color that is readable on hex_bg ("#FFFFFF" or "#1A1A1A").
        name:      human-readable color name.
    """
    hex_main: str
    hex_bg: str
    hex_text: str
    name: str


@dataclass(frozen=True)
class _PaletteEntry:
    """One entry in the 12-color palette, with dark + light variants."""
    name: str
    dark_main: str
    dark_bg: str
    light_main: str
    light_bg: str


# ---------------------------------------------------------------------------
# 12-color palette
# ---------------------------------------------------------------------------
# Chosen for maximum distinguishability across the full set. Each color has:
#   - dark_main:  the accent color on dark backgrounds (slightly brightened)
#   - dark_bg:    a dark tinted background (18% main blended onto #1A1A1A)
#   - light_main: the accent color on light backgrounds (slightly deepened)
#   - light_bg:   a light tinted background (18% main blended onto #FFFFFF)
#
# The palette intentionally avoids pure blue/indigo as the first entries
# (they are hard to distinguish on common dark themes), placing warmer
# distinguishable colors first.
# ---------------------------------------------------------------------------
_PALETTE: List[_PaletteEntry] = [
    _PaletteEntry("Coral",     "#F87171", "#3D2222", "#DC2626", "#FEE2E2"),
    _PaletteEntry("Amber",     "#FBBF24", "#3D3116", "#D97706", "#FEF3C7"),
    _PaletteEntry("Lime",      "#A3E635", "#2A3318", "#65A30D", "#ECFCCB"),
    _PaletteEntry("Emerald",   "#34D399", "#1A3329", "#059669", "#D1FAE5"),
    _PaletteEntry("Teal",      "#2DD4BF", "#143330", "#0D9488", "#CCFBF1"),
    _PaletteEntry("Cyan",      "#22D3EE", "#143840", "#0891B2", "#CFFAFE"),
    _PaletteEntry("Sky",       "#38BDF8", "#163640", "#0284C7", "#E0F2FE"),
    _PaletteEntry("Violet",    "#A78BFA", "#2E2540", "#7C3AED", "#EDE9FE"),
    _PaletteEntry("Fuchsia",   "#E879F9", "#3A2540", "#C026D3", "#FAE8FF"),
    _PaletteEntry("Rose",      "#FB7185", "#3D2230", "#E11D48", "#FFE4E6"),
    _PaletteEntry("Orange",    "#FB923C", "#3D2A18", "#EA580C", "#FFEDD5"),
    _PaletteEntry("Slate",     "#94A3B8", "#2A3340", "#475569", "#E2E8F0"),
]


# Neutral fallback for unknown/None character_id
_NEUTRAL_DARK = CharacterColor("#A0A0A0", "#2E2E2E", "#F5F5F5", "Neutral")
_NEUTRAL_LIGHT = CharacterColor("#6B7280", "#F3F4F6", "#1F2937", "Neutral")


def _hash_index(character_id: str) -> int:
    """Map a character_id to a stable palette index (0..11).

    Uses SHA-256 for stability across Python versions (hash() is randomized
    per-process by default in Python 3.3+, so we can't use the builtin).
    """
    if not character_id:
        return -1  # neutral
    digest = hashlib.sha256(character_id.encode("utf-8")).hexdigest()
    # Take the first 8 hex chars (32 bits) as an integer
    val = int(digest[:8], 16)
    return val % len(_PALETTE)


def get_character_color(character_id: str, theme: str = "dark") -> CharacterColor:
    """Return the stable CharacterColor for the given character_id.

    Args:
        character_id: the Character's unique ID. None or "" → neutral gray.
        theme: "dark" (default) or "light". Selects the variant.

    Returns:
        CharacterColor with hex_main, hex_bg, hex_text, name.
    """
    if not character_id:
        return _NEUTRAL_DARK if theme == "dark" else _NEUTRAL_LIGHT

    idx = _hash_index(character_id)
    if idx < 0:
        return _NEUTRAL_DARK if theme == "dark" else _NEUTRAL_LIGHT

    entry = _PALETTE[idx]
    if theme == "light":
        # On light backgrounds, text on the light_bg should be dark
        return CharacterColor(
            hex_main=entry.light_main,
            hex_bg=entry.light_bg,
            hex_text="#1A1A1A",
            name=entry.name,
        )
    else:
        # On dark backgrounds, text on the dark_bg should be light
        return CharacterColor(
            hex_main=entry.dark_main,
            hex_bg=entry.dark_bg,
            hex_text="#F5F5F5",
            name=entry.name,
        )


def get_character_color_hex(character_id: str, theme: str = "dark") -> str:
    """Convenience: return just the primary hex color for a character."""
    return get_character_color(character_id, theme).hex_main


def get_all_palette_colors(theme: str = "dark") -> List[CharacterColor]:
    """Return all 12 palette colors (for swatches/legends in UI)."""
    results = []
    for entry in _PALETTE:
        if theme == "light":
            results.append(CharacterColor(
                hex_main=entry.light_main,
                hex_bg=entry.light_bg,
                hex_text="#1A1A1A",
                name=entry.name,
            ))
        else:
            results.append(CharacterColor(
                hex_main=entry.dark_main,
                hex_bg=entry.dark_bg,
                hex_text="#F5F5F5",
                name=entry.name,
            ))
    return results


def is_valid_color(hex_color: str) -> bool:
    """Check if a string is a valid 7-char hex color (#RRGGBB)."""
    if not isinstance(hex_color, str) or len(hex_color) != 7:
        return False
    if hex_color[0] != "#":
        return False
    try:
        int(hex_color[1:], 16)
        return True
    except ValueError:
        return False
