"""
SpeechStudio — Font Loader
==========================

Loads the bundled TTF font files from ``assets/fonts/`` at application
startup and registers them with the Qt font database.

This module is the single source of truth for *which* custom fonts are
installed into the running Qt application. The typography tokens defined
in ``ui/typography.py`` reference the family names exposed here; if a
font fails to load, Qt silently falls back to the next family in the
token's CSS-style fallback stack (e.g. ``"Segoe UI"``).

Public API
----------
    load_fonts()      -> dict[str, list[int]]
        Returns ``{family_name: [loaded_weight, ...]}``. Safe to call
        multiple times — Qt's ``addApplicationFont`` is idempotent.

    get_font_status() -> str
        Multi-line diagnostic string intended for the Help -> Developer
        menu. Shows which families are registered, what integer weights
        Qt reports for them, and any errors recorded during the last
        ``load_fonts()`` invocation.

Usage (call once at startup, *after* the QApplication is constructed)::

    from ui.font_loader import load_fonts
    load_fonts()

Font weight integers follow the CSS / OpenType ``usWeightClass``
convention:

    400  = Regular / Normal
    500  = Medium
    600  = SemiBold / DemiBold
    700  = Bold
    800  = ExtraBold
    900  = Black
"""

from __future__ import annotations

import os
from typing import Dict, List

from PySide6.QtGui import QFontDatabase

from engine.logger import get_logger

logger = get_logger("font_loader")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Resolve the application root from this file's location:
#   ui/font_loader.py  ->  ui/  ->  <app_root>
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS_DIR = os.path.join(_APP_ROOT, "assets", "fonts")

# Ordered list of font specs: file -> (expected family, expected weight).
# The expected weight is the OpenType usWeightClass of the bundled TTF
# (as set by the asset pipeline in Task B-C-assets). It's used for
# verification and as a fallback if Qt's style-query API isn't available.
FONT_SPECS: List[Dict[str, object]] = [
    {"file": "Inter_Regular.ttf",          "family": "Inter",           "weight": 400},
    {"file": "Inter_SemiBold.ttf",         "family": "Inter",           "weight": 600},
    {"file": "Inter_Bold.ttf",             "family": "Inter",           "weight": 700},
    {"file": "LibreFranklin_SemiBold.ttf", "family": "Libre Franklin",  "weight": 600},
    {"file": "LibreFranklin_Bold.ttf",     "family": "Libre Franklin",  "weight": 700},
    {"file": "JetBrainsMono_Regular.ttf",  "family": "JetBrains Mono",  "weight": 400},
]

# Cache of the most recent load result so get_font_status() doesn't
# re-run the loader if the caller only wants diagnostics.
_last_load_result: Dict[str, List[int]] = {}
_last_load_errors: List[str] = []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _query_weights(family: str, fallback_weight: int) -> List[int]:
    """Return the sorted list of integer weights Qt reports for ``family``.

    Uses the QFontDatabase instance API to enumerate the registered
    styles and their associated weights. If that API is unavailable in
    the current Qt version (the methods were flagged as deprecated in
    Qt 6.x and may be removed in a future release), falls back to the
    expected weight from FONT_SPECS so callers always get a usable list.

    Args:
        family: family name as returned by applicationFontFamilies().
        fallback_weight: integer weight declared in FONT_SPECS, used
            if the live query fails.

    Returns:
        Sorted list of integer weights (e.g. ``[400, 600, 700]``).
        Never empty when ``fallback_weight`` is provided.
    """
    weights: List[int] = []
    try:
        db = QFontDatabase()
        for style in db.styles(family):  # type: ignore[attr-defined]
            try:
                w = int(db.weight(family, style))  # type: ignore[attr-defined]
                if w not in weights:
                    weights.append(w)
            except Exception:
                # Individual style weight queries should never abort
                # the whole enumeration.
                continue
    except Exception:
        # styles()/weight() not available in this Qt build — fall back
        # to the declared weight from FONT_SPECS.
        pass

    if not weights and fallback_weight:
        weights.append(fallback_weight)
    return sorted(weights)


def _load_one(spec: Dict[str, object]) -> tuple[List[str], List[int], List[str]]:
    """Load a single TTF file.

    Returns ``(families, weights_loaded, errors)``.

    * ``families``        — list of family names Qt reports for the file
                            (typically one element, but a TTF can declare
                            multiple).
    * ``weights_loaded``  — sorted list of integer weights registered for
                            the (first) primary family.
    * ``errors``          — list of human-readable error/warning strings.
                            Empty list on a clean load.
    """
    errors: List[str] = []
    file_name = str(spec["file"])
    expected_family = str(spec["family"])
    expected_weight = int(spec["weight"])  # type: ignore[arg-type]

    path = os.path.join(FONTS_DIR, file_name)
    if not os.path.isfile(path):
        msg = "missing font file: {}".format(file_name)
        logger.warning(msg)
        errors.append(msg)
        return [], [], errors

    font_id = QFontDatabase.addApplicationFont(path)
    if font_id == -1:
        msg = "QFontDatabase.addApplicationFont() rejected file: {}".format(file_name)
        logger.error(msg)
        errors.append(msg)
        return [], [], errors

    families = list(QFontDatabase.applicationFontFamilies(font_id))
    if not families:
        msg = "no families returned for {}".format(file_name)
        logger.warning(msg)
        errors.append(msg)
        return [], [], errors

    # Verify the family name matches what we expected. We don't bail on
    # mismatch — we still record whatever Qt reported so the diagnostic
    # output is honest about what got registered.
    if expected_family not in families:
        msg = ("family mismatch for {}: expected '{}', Qt reported {}"
               .format(file_name, expected_family, families))
        logger.warning(msg)
        errors.append(msg)

    # Query weights for the *primary* family (the first one Qt returned).
    # If the family name doesn't match expectation we still query the
    # primary so the caller gets usable data.
    primary = families[0]
    weights_loaded = _query_weights(primary, expected_weight)

    logger.info(
        "loaded {} -> families={}, weights={}"
        .format(file_name, families, weights_loaded)
    )
    return families, weights_loaded, errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_fonts() -> Dict[str, List[int]]:
    """Load all bundled TTF fonts into the Qt font database.

    Returns:
        Dict mapping family name -> sorted list of integer weights
        successfully loaded for that family. Example::

            {"Inter": [400, 600, 700],
             "Libre Franklin": [600, 700],
             "JetBrains Mono": [400]}

    Safe to call multiple times: Qt's ``addApplicationFont`` is
    idempotent (registering the same file twice just adds a duplicate
    entry that is deduplicated by family name).

    Never raises — every error is caught, logged via the engine logger,
    and recorded in ``_last_load_errors`` so ``get_font_status()`` can
    surface it to the developer.
    """
    global _last_load_result, _last_load_errors
    result: Dict[str, List[int]] = {}
    errors: List[str] = []

    logger.info("loading {} font files from {}"
                .format(len(FONT_SPECS), FONTS_DIR))

    if not os.path.isdir(FONTS_DIR):
        msg = "fonts directory missing: {}".format(FONTS_DIR)
        logger.error(msg)
        errors.append(msg)
        _last_load_result = result
        _last_load_errors = errors
        return result

    for spec in FONT_SPECS:
        try:
            families, weights, errs = _load_one(spec)
            errors.extend(errs)
            for fam in families:
                # Merge weights into any already registered for this
                # family (e.g. multiple Inter_*.ttf files all register
                # under "Inter").
                existing = result.setdefault(fam, [])
                for w in weights:
                    if w not in existing:
                        existing.append(w)
        except Exception as exc:  # never crash the app on a bad font
            msg = "unexpected error loading {}: {}".format(spec["file"], exc)
            logger.exception("font load failed: %s", spec["file"])
            errors.append(msg)

    # Sort weights for clean diagnostic output.
    for fam in result:
        result[fam] = sorted(result[fam])

    _last_load_result = result
    _last_load_errors = errors

    logger.info("font load complete: {} families registered"
                .format(len(result)))
    return result


def get_font_status() -> str:
    """Return a multi-line diagnostic string for the Help -> Developer menu.

    The string summarises:
      * Which font families Qt reports as available.
      * The integer weight(s) actually registered for each family.
      * Any errors recorded during the last ``load_fonts()`` call.

    If ``load_fonts()`` has not been called yet, this triggers a load so
    the diagnostic always shows real data.
    """
    if not _last_load_result and not _last_load_errors:
        load_fonts()

    lines: List[str] = []
    lines.append("Font Loader Status")
    lines.append("=" * 60)
    lines.append("Fonts directory: {}".format(FONTS_DIR))
    lines.append("")

    if not _last_load_result:
        lines.append("No custom fonts registered.")
    else:
        lines.append("Registered families:")
        for fam in sorted(_last_load_result.keys()):
            weights = _last_load_result[fam]
            weight_str = ", ".join(str(w) for w in weights) if weights else "(none)"
            lines.append("  - {}  --  weights: {}".format(fam, weight_str))
        lines.append("")

    # Cross-check what Qt actually knows about the expected families.
    try:
        db = QFontDatabase()
        qt_families = set(db.families())  # type: ignore[attr-defined]
    except Exception:
        qt_families = set()

    lines.append("Qt family presence check (expected tokens):")
    for fam in ("Inter", "Libre Franklin", "JetBrains Mono"):
        marker = "[OK]" if fam in qt_families else "[MISSING]"
        lines.append("  {}  {}".format(marker, fam))
    lines.append("")

    if _last_load_errors:
        lines.append("Errors / warnings during last load:")
        for e in _last_load_errors:
            lines.append("  ! {}".format(e))
    else:
        lines.append("No errors recorded during last load.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standalone smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Run as:  python3 -m ui.font_loader
    # (requires a QApplication because Qt APIs need one)
    import sys
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    result = load_fonts()
    print("load_fonts() returned:")
    for fam, weights in sorted(result.items()):
        print("  {} = {}".format(fam, weights))
    print()
    print(get_font_status())
