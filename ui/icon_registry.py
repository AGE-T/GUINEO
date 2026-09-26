"""
SpeechStudio — Icon Registry
============================

Loads Material Symbols Outlined SVG files from ``assets/icons/`` on demand
and renders them into ``QIcon`` instances at the requested size and color.

This module is the **preferred** API for creating icons across the
application:

    from ui.icon_registry import IconRegistry, make_icon

    icon = IconRegistry.icon("play_arrow", size=20, color="#F5F5F5")
    icon = make_icon("play_arrow", size=20, color="#F5F5F5")

The legacy ``ui.typography.make_icon(svg_str, ...)`` function still works
unchanged — it accepts a *raw SVG string* (typically one of the
``SVG_*`` constants declared in ``ui/typography.py``). The new
``ui.icon_registry.make_icon(name, ...)`` accepts a *file name* (without
the ``.svg`` extension) and resolves it through the registry. The two
functions deliberately share a name to make migration straightforward:
callers that already use ``make_icon(SVG_PLAY_ARROW, ...)`` can switch
to ``make_icon("play_arrow", ...)`` with a one-line edit.

Resolution / fallback chain
----------------------------
For a given icon ``name``, the registry tries the following sources in
order and uses the first one that yields usable SVG source:

    1. ``assets/icons/<name>.svg``
       The bundled Material Symbols Outlined file (preferred — these are
       the canonical Material glyphs at the 960-unit viewBox).

    2. ``INLINE_SVG_FALLBACKS[name]``
       A copy of the inline SVG constants from ``ui/typography.py``
       (``SVG_PLAY_ARROW``, ``SVG_PAUSE``, etc.). These exist purely as
       a safety net so a missing or corrupted file never breaks the UI.

    3. Built-in ``"?"`` placeholder icon
       Rendered at the requested size/color. Only used if both of the
       above fail. A warning is logged the first time an unknown name
       is requested so the issue is visible in the developer log.

Rendering
---------
The bundled Material Symbols SVGs use the format::

    <svg xmlns="..." height="24" viewBox="0 -960 960 960" width="24">
        <path d="..."/>
    </svg>

— the ``<path>`` has **no** ``fill`` attribute, so QSvgRenderer would
render it with the default (black) fill. The registry therefore
injects ``fill="<color>"`` into the root ``<svg>`` element so it
inherits to all paths. The legacy inline SVGs use
``fill="currentColor"`` — for those, the registry replaces the token
with the requested color (preserving the existing ``make_icon()``
behavior in ``ui/typography.py``).

Caching
-------
Every successfully rendered icon is cached under a key
``"<name>|<size>|<color>|<filled>"``. Subsequent lookups for the same
combination return the cached ``QIcon`` instance. The cache can be
cleared via :meth:`IconRegistry.clear_cache` (useful when the active
theme changes).
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional, Set, Tuple

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from engine.logger import get_logger

logger = get_logger("icon_registry")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Resolve the application root from this file's location:
#   ui/icon_registry.py  ->  ui/  ->  <app_root>
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS_DIR = os.path.join(_APP_ROOT, "assets", "icons")


# ---------------------------------------------------------------------------
# Inline SVG fallbacks (mirrors of ui/typography.py SVG_* constants)
# ---------------------------------------------------------------------------
# These are *copies* of the inline SVG constants declared in
# ``ui/typography.py``. They are duplicated here deliberately so that the
# icon registry has zero hard dependencies on the typography module (which
# is itself a heavy module that transitively imports QtGui, QtWidgets,
# etc.). Keeping a local copy means the fallback chain works even if the
# typography module is unavailable, and it makes the registry fully
# self-contained.
#
# If you add a new SVG_* constant to ui/typography.py, add the matching
# entry here too. The two should always be kept in sync.
INLINE_SVG_FALLBACKS: Dict[str, str] = {
    # Navigation / project icons
    "graphic_eq":       '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 3h2v18H7V3zm4 2h2v14h-2V5zm4 4h2v6h-2V9zm-8 2h2v2H3v-2zm12 0h2v2h-2v-2z"/></svg>',
    "folder_open":      '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm0 12H4V8h16v10z"/></svg>',
    "movie_edit":       '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z"/></svg>',
    "groups":           '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/></svg>',

    # Action icons
    "add":              '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>',
    "undo":             '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.5 8c-2.65 0-5.05.99-6.9 2.6L2 7v9h9l-3.62-3.62c1.39-1.12 3.12-1.88 5.12-1.88 3.53 0 6.55 2.31 7.6 5.5l2.37-.78C21.08 11.03 17.15 8 12.5 8z"/></svg>',
    "redo":             '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.4 10.6C16.55 8.99 14.15 8 11.5 8c-4.65 0-8.58 3.03-9.96 7.22L3.9 16c1.05-3.19 4.07-5.5 7.6-5.5 2 0 3.73.76 5.12 1.88L13 16h9V7l-3.6 3.6z"/></svg>',
    "save":             '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z"/></svg>',
    "upload":           '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M5 20h14v-2H5v2zM12 2l-5 5h3v6h4V7h3l-5-5z"/></svg>',
    "edit":             '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>',

    # Transport icons
    "play_arrow":       '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
    "pause":            '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>',
    "stop":             '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h12v12H6z"/></svg>',
    "skip_previous":    '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>',
    "skip_next":        '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>',
    "volume_up":         '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>',
    "volume_down":      '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 9v6h4l5 5V4l-5 5H7z"/></svg>',

    # Emotion icons (Material Symbols sentiment set)
    "sentiment_neutral":             '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M9 14h6v1.5H9z"/><circle cx="15.5" cy="9.5" r="1.5"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8z"/></svg>',
    "sentiment_satisfied":           '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-5.5c-.83 0-1.5.67-1.5 1.5s.67 1.5 1.5 1.5 1.5-.67 1.5-1.5-.67-1.5-1.5-1.5z"/><circle cx="15.5" cy="9.5" r="1.5"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="M12 18c1.93 0 3.62-1.15 4.38-2.8H7.62C8.38 16.85 10.07 18 12 18z"/></svg>',
    "sentiment_dissatisfied":        '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M15.5 9.5m-1.5 0a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-5.5c-.83 0-1.5.67-1.5 1.5s.67 1.5 1.5 1.5 1.5-.67 1.5-1.5-.67-1.5-1.5-1.5z"/><path d="M7.62 14.8C8.38 16.85 10.07 18 12 18s3.62-1.15 4.38-2.8H7.62z" transform="scale(1,-1) translate(0,-31.6)"/></svg>',
    "sentiment_very_dissatisfied":   '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm-5-9.5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5S7 11.33 7 10.5zm9 3.5c0-1.66-1.34-3-3-3s-3 1.34-3 3 1.34 3 3 3 3-1.34 3-3zm-2.5-3c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5-1.5-.67-1.5-1.5z"/></svg>',
    "mood_bad":                      '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm-5-9.5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5S7 11.33 7 10.5zm9 3.5c0-1.66-1.34-3-3-3s-3 1.34-3 3 1.34 3 3 3 3-1.34 3-3zm-2.5-3c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5-1.5-.67-1.5-1.5z"/></svg>',
    "self_improvement":              '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 6c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm0 15c-1.1 0-2-.9-2-2v-4h2v6zm-7-3h12v2H5v-2z"/></svg>',
    "celebration":                   '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 22l14-14-2-2L2 20v2zm16-6l-2 2 3 3 2-2-3-3zm3-12l-2 2 3 3 2-2-3-3zm-5 4l-2 2 3 3 2-2-3-3z"/></svg>',

    # Status / checkmark icons
    "check_circle":      '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>',
    "chevron_right":     '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z"/></svg>',
}


# Compiled once at import time. Matches the root <svg ...> opening tag
# and captures (a) the attributes block and (b) the closing angle bracket
# (with or without a self-closing slash) so we can inject a fill attribute.
_SVG_OPEN_RE = re.compile(r"(<svg[^>]*?)(/?>)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# IconRegistry
# ---------------------------------------------------------------------------
class IconRegistry:
    """Loads, colorises, renders, and caches SVG icons.

    All methods are classmethods — there's exactly one shared registry per
    process. The cache key is ``"<name>|<size>|<color>|<filled>"``.
    """

    # Cache of already-rendered icons. Keyed by
    # ``"<name>|<size>|<color>|<filled>"``. Public for read-only inspection
    # in tests; do not mutate directly — use :meth:`clear_cache`.
    _cache: Dict[str, QIcon] = {}

    # Names we've already warned about being missing on disk so we don't
    # spam the log on every render.
    _missing_warned: Set[str] = set()

    # ------------------------------------------------------------------
    # SVG source resolution
    # ------------------------------------------------------------------
    @classmethod
    def _resolve_svg(cls, name: str) -> Tuple[Optional[str], str]:
        """Resolve an icon name to SVG source.

        Returns ``(svg_str, source_label)`` where ``source_label`` is one
        of ``"file"``, ``"fallback"``, or ``""`` (when nothing is found).

        Tries the bundled ``assets/icons/<name>.svg`` first, then the
        inline fallback map.
        """
        file_path = os.path.join(ICONS_DIR, "{}.svg".format(name))
        if os.path.isfile(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return f.read(), "file"
            except OSError as exc:
                logger.warning("failed to read {}: {}".format(file_path, exc))
                # Fall through to inline fallback.

        if name in INLINE_SVG_FALLBACKS:
            if name not in cls._missing_warned:
                logger.warning(
                    "icon '{}' not found on disk; using inline fallback"
                    .format(name)
                )
                cls._missing_warned.add(name)
            return INLINE_SVG_FALLBACKS[name], "fallback"

        return None, ""

    # ------------------------------------------------------------------
    # Color / fill injection
    # ------------------------------------------------------------------
    @classmethod
    def _apply_color(cls, svg_str: str, color: str, filled: bool) -> str:
        """Inject the requested color into the SVG source.

        Handles both SVG dialects in use across the project:

        * **Inline legacy SVGs** (``ui/typography.py`` constants): the
          root ``<svg>`` element carries ``fill="currentColor"``. We
          replace the ``currentColor`` token with the requested color.

        * **Bundled Material Symbols files**: the ``<svg>`` element has
          no ``fill`` attribute, so QSvgRenderer would render in black.
          We inject ``fill="<color>"`` into the root ``<svg>`` element
          so it inherits to every ``<path>`` child.

        * **Filled variant**: when ``filled=True``, we additionally
          rewrite any ``fill="none"`` to the requested color so
          outlined-only glyphs render as their filled counterpart.
          (Material Symbols Outlined files don't use ``fill="none"``,
          so this is a forward-compatibility hook for the Rounded set.)
        """
        if "currentColor" in svg_str:
            svg_str = svg_str.replace("currentColor", color)

        if 'fill=' not in svg_str:
            # No fill declared anywhere — inject one into the root <svg>.
            svg_str = _SVG_OPEN_RE.sub(
                r'\1 fill="{}"\2'.format(color),
                svg_str,
                count=1,
            )

        if filled:
            # Convert any explicitly-empty fills into the requested color
            # so an "outlined" glyph renders as its filled counterpart.
            # (No-op for current Material Symbols Outlined assets, which
            # have no fill="none" attributes, but harmless and useful for
            # future Material Symbols Rounded / Filled sets.)
            svg_str = re.sub(
                r'fill="none"',
                'fill="{}"'.format(color),
                svg_str,
            )

        return svg_str

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    @classmethod
    def _render(cls, svg_str: str, size: int) -> Optional[QIcon]:
        """Render SVG source to a QIcon at the requested pixel size.

        Returns ``None`` if QSvgRenderer rejects the source (e.g. the
        XML is malformed). Callers are expected to fall back to the
        placeholder icon in that case.
        """
        if size <= 0:
            size = 1  # QPixmap(0, 0) is invalid; clamp.

        renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
        if not renderer.isValid():
            return None

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    @classmethod
    def _placeholder(cls, size: int, color: str) -> QIcon:
        """Render a '?' placeholder icon for missing/failed icons.

        Never returns None — if even the placeholder fails to render,
        returns an empty transparent QIcon so callers can always assign
        the result to a widget.
        """
        placeholder_svg = (
            '<svg viewBox="0 0 24 24" fill="{}">'
            '<path d="M11 18h2v-2h-2v2zm1-16C6.48 2 2 6.48 2 12s4.48 10 10 10 '
            '10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 '
            '8 3.59 8 8-3.59 8-8 8zm0-14c-2.21 0-4 1.79-4 4h2c0-1.1.9-2 2-2 '
            's2 .9 2 2c0 2-3 1.75-3 5h2c0-2.25 3-2.5 3-5 0-2.21-1.79-4-4-4z"/>'
            '</svg>'
        ).format(color)

        icon = cls._render(placeholder_svg, size)
        if icon is not None:
            return icon

        # Absolute last resort: empty transparent pixmap.
        empty = QPixmap(max(size, 1), max(size, 1))
        empty.fill(Qt.GlobalColor.transparent)
        return QIcon(empty)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @classmethod
    def icon(cls, name: str, size: int = 20, color: str = "#F5F5F5",
             filled: bool = False) -> QIcon:
        """Return a QIcon for the named icon.

        Args:
            name:   icon file basename (without ``.svg`` extension),
                    e.g. ``"play_arrow"``, ``"pause"``, ``"folder_open"``.
            size:   pixel size of the rendered pixmap (square).
            color:  hex color string applied to the icon's fill.
            filled: if True, attempt to render the filled variant. For
                    Material Symbols Outlined assets this is a no-op
                    (the outlined set has no fill="none" paths to flip),
                    but the parameter is kept for forward compatibility
                    with the Rounded/Filled sets.

        Returns:
            A QIcon — **never None**. If the named icon is missing or
            fails to render, a ``?`` placeholder icon of the same size
            and color is returned so the UI never breaks.
        """
        cache_key = "{}|{}|{}|{}".format(name, size, color, int(filled))
        cached = cls._cache.get(cache_key)
        if cached is not None:
            return cached

        svg_str, source = cls._resolve_svg(name)
        if svg_str is None:
            logger.warning(
                "icon '{}' not found in {} or fallbacks; using '?' placeholder"
                .format(name, ICONS_DIR)
            )
            icon = cls._placeholder(size, color)
            cls._cache[cache_key] = icon
            return icon

        colored = cls._apply_color(svg_str, color, filled)
        icon = cls._render(colored, size)
        if icon is None:
            logger.warning(
                "failed to render icon '{}' (source: {}); using '?' placeholder"
                .format(name, source)
            )
            icon = cls._placeholder(size, color)

        cls._cache[cache_key] = icon
        return icon

    @classmethod
    def has_icon(cls, name: str) -> bool:
        """Return True if ``name`` resolves to an SVG source (file or fallback)."""
        file_path = os.path.join(ICONS_DIR, "{}.svg".format(name))
        return os.path.isfile(file_path) or name in INLINE_SVG_FALLBACKS

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the rendered-icon cache.

        Call this when the active theme changes (so icons re-render with
        new colors) or in tests to force fresh rendering.
        """
        cls._cache.clear()

    @classmethod
    def cache_size(cls) -> int:
        """Return the number of icons currently in the cache."""
        return len(cls._cache)


# ---------------------------------------------------------------------------
# Convenience function (preferred public API for new code)
# ---------------------------------------------------------------------------
def make_icon(name: str, size: int = 20, color: str = "#F5F5F5") -> QIcon:
    """Return a QIcon for the named bundled icon.

    Mirrors the function name of ``ui.typography.make_icon`` so callers
    can switch from::

        from ui.typography import make_icon, SVG_PLAY_ARROW
        icon = make_icon(SVG_PLAY_ARROW, 20, "#FFFFFF")

    to::

        from ui.icon_registry import make_icon
        icon = make_icon("play_arrow", 20, "#FFFFFF")

    with a one-line edit. Note the parameter difference: the new function
    takes a *file name* (no ``.svg`` extension) instead of a raw SVG
    string.
    """
    return IconRegistry.icon(name, size=size, color=color)


# ---------------------------------------------------------------------------
# Standalone smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Run as:  python3 -m ui.icon_registry
    # (requires a QApplication because Qt rendering needs one)
    import sys
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    test_names = [
        "play_arrow", "pause", "stop", "folder_open", "settings",
        "mic", "check", "missing_icon_test",
    ]
    for n in test_names:
        icon = IconRegistry.icon(n, size=20, color="#F5F5F5")
        available = IconRegistry.has_icon(n)
        print("  {:<22}  has_icon={:<5}  cache_size={}"
              .format(n, available, IconRegistry.cache_size()))
        # Touch the icon so it actually exists (otherwise QPixmap is lazy).
        _ = icon.availableSizes()

    print()
    print("Final cache size:", IconRegistry.cache_size())
