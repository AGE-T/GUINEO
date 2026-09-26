"""
SpeechStudio — Project/Scene Sidebar
=====================================

New Sidebar widget matching the HTML reference structure:

    [New Project]           (orange gradient CTA)
    
    [folder_open] Projects  (active by default)
    [movie_edit]  Scenes
    [groups]      Characters
    
    RECENT SCENES          [+]
    ┌ 02 Airlock          ← active (purple left border)
    │ 01 Landing
    │ 03 Corridor

The legacy Voice Library, History, Presets, and Outputs functionality
is preserved as separate signals — MainWindow can connect them to
dialogs or menus. The visible Sidebar only shows the HTML structure.

Architecture:
    - Custom QWidget with QVBoxLayout
    - New Project button (orange gradient)
    - Navigation items (Projects, Scenes, Characters) — custom NavItemButton
    - Recent Scenes section (header + add button + scrollable list)
    - Scene rows (custom SceneRowWidget with active/inactive states)

API (compatible with legacy Sidebar):
    - set_project(name)          — updates the project context
    - set_scenes(scenes)         — replaces the scene list
    - set_active_scene(scene_id) — highlights the active scene row
    - update_voices(voices)      — legacy: stores voices for dialog access
    - update_history(entries)    — legacy: stores history for dialog access
    - update_presets(presets)    — legacy: stores presets for dialog access
    - update_outputs(files)      — legacy: stores outputs for dialog access

Signals (compatible with legacy Sidebar):
    - new_project_requested     — user clicked New Project
    - new_scene_requested       — user clicked the + next to Recent Scenes
    - scene_selected(scene_id)  — user clicked a scene row
    - voice_selected(voice_id)   — legacy: emitted by voice dialog
    - (other legacy signals preserved)
"""

from __future__ import annotations
from typing import Optional, List

from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QFont, QPixmap, QPainter, QIcon, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QToolButton, QScrollArea, QFrame, QSizePolicy, QListWidget,
    QListWidgetItem, QComboBox, QLineEdit, QMenu,
)

from ui.typography import (
    Typography, IconSize, IconColor, make_icon,
    SVG_ADD,
)
from ui.icon_registry import IconRegistry


# Additional SVG icons for Library section
SVG_RECORD_VOICE_OVER = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M9 9c0-1.66 1.34-3 3-3s3 1.34 3 3-1.34 3-3 3-3-1.34-3-3zM3 19c0-3.31 5.37-6 9-6 1.61 0 3.11.42 4.35 1.15C14.87 14.71 14 16.23 14 18c0 .69.12 1.35.34 1.97C13.29 20.62 12.04 21 9 21c-3.63 0-9-2.69-9-6zm17.7-4.41l-1.41 1.41-2.79-2.79-2.79 2.79-1.41-1.41 2.79-2.79L12.3 9.7l1.41-1.41 2.79 2.79 2.79-2.79 1.41 1.41-2.79 2.79 2.79 2.79z"/></svg>'
SVG_HISTORY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13 3c-4.97 0-9 4.03-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42C8.27 19.99 10.51 21 13 21c4.97 0 9-4.03 9-9s-4.03-9-9-9zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/></svg>'
SVG_TUNE = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z"/></svg>'
SVG_FOLDER_SPECIAL = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-4.6 11l-2.4-1.44L10.6 17l.63-2.73L9 12.76l2.79-.06L13 10l1.21 2.7 2.79.06-2.23 1.51.63 2.73z"/></svg>'
SVG_OUTPUT = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>'


class _RecentsScrollArea(QScrollArea):
    """P3.34: a QScrollArea whose sizeHint stays LIVE.

    Qt caches QScrollArea::sizeHint from the first computation — for a
    scroll area populated AFTER construction (Recents is rebuilt on
    every context switch) the cached hint stays at the empty-boot value
    (~29px), so the layout pinned the panel at its minimum forever (the
    user-reported "unnecessarily short" panel). This subclass derives
    the hint from the content widget on every query, clamped to the
    configured min/max — the layout can then grow the panel with the
    row count (up to 5 rows fully visible) and still squeeze it down to
    the minimum in short windows.
    """

    def sizeHint(self):
        w = self.widget()
        if w is not None:
            h = w.sizeHint().height() + 4  # frame/scrollbar allowance
            from PySide6.QtCore import QSize as _QSize
            return _QSize(super().sizeHint().width(),
                          max(self.minimumHeight(),
                              min(self.maximumHeight(), h)))
        return super().sizeHint()


class _NavItemButton(QWidget):
    """A navigation item button with icon + label.

    Uses icon_registry (bundled Material Symbols Outlined SVGs) for
    canonical Google Material icons — NOT inline SVG strings or Unicode.

    Active state: raised dark bg, purple text, filled icon.
    Inactive: transparent bg, secondary text, outline icon.
    Hover: subtle surface change.

    Icon size: 28px (P3.44.4 — the user-requested visual scale-up; the
    40px row comfortably carries a 28px icon, and the previous 20px
    glyph looked undersized in the available sidebar width). Container:
    28x28 (unchanged — layout metrics identical). Row height 40px
    (P3.34 — unchanged: the larger content must fit the existing rows;
    the sidebar's vertical footprint and the Recents panel budget stay
    exactly as they are).

    Label: 16px (P3.44.4 — body_lg, the existing Inter token one step
    above body_md; carried in the widget's own stylesheet so the
    theme's global base font cannot shrink it).
    """

    clicked_signal = Signal(str)  # emits the label text

    # P3.44.4: shared visual-scale constants. The icon is rendered in
    # FOUR places (initial + active/hover/inactive) and the label font
    # in THREE stylesheets — a numeric drift in any one of them makes
    # the icon visibly jump between states. These constants lock every
    # site to the same P3.44.4 scale.
    _ICON_PX = 28    # rendered icon + container edge (28x28 label)
    _LABEL_PX = 16   # nav label font (body_lg)

    def __init__(self, label: str, icon_name: str, parent=None):
        super().__init__(parent)
        # CRITICAL: WA_StyledBackground makes QWidget paint its own stylesheet
        # background. Without this, the background-color in the stylesheet is
        # ignored and the nav item appears to have no selection background —
        # only the child labels' styles are visible, making the selection
        # look "too small" (just around the icon/text instead of the full row).
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._label_text = label
        self._icon_name = icon_name
        self._is_active = False
        self._is_hovered = False
        # P3.34: 40px rows (was 48px) — frees the vertical budget the
        # Recents panel needs at windowed sizes while staying a
        # comfortable touch target.
        # P3.44.4: row height UNCHANGED — the scale-up (28px icons,
        # 16px labels) fits inside the existing 40px row; the sidebar
        # keeps its vertical footprint.
        self.setFixedHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

        layout = QHBoxLayout(self)
        # Left margin 16px (reference px-4), top/bottom 0 (height is fixed).
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        # Icon: use IconRegistry (loads bundled Material Symbols SVGs
        # from assets/icons/). P3.44.4: 28px — the 40px row and 28x28
        # container comfortably carry the larger glyph; IconRegistry
        # stays the single source (SVG → crisp at any size).
        self._icon_label = QLabel()
        icon = IconRegistry.icon(icon_name, size=self._ICON_PX,
                                 color=IconColor.SECONDARY)
        if icon and not icon.isNull():
            self._icon_label.setPixmap(
                icon.pixmap(QSize(self._ICON_PX, self._ICON_PX)))
        self._icon_label.setFixedSize(28, 28)
        layout.addWidget(self._icon_label)

        self._text_label = QLabel(label)
        # P3.44.4: primary nav text at 16px (body_lg — the existing
        # Inter token; the 14px body_md label looked undersized next to
        # the 28px icon and the sidebar's available width).
        self._text_label.setFont(Typography.body_lg())
        layout.addWidget(self._text_label)
        layout.addStretch()

        self._update_style()

    def _update_style(self):
        # P3.34: font-size in the stylesheet (the app-wide theme QSS
        # sets a global base font that overrides programmatic
        # setFont(); the widget's own stylesheet rule wins).
        # P3.44.4: the stylesheet font-size now mirrors the programmatic
        # body_lg token (16px) via _LABEL_PX so the two never drift.
        _icon_px = self._ICON_PX
        _label_px = self._LABEL_PX
        if self._is_active:
            # Reference: bg-surface-container-highest (#353534) + text-primary (#d0bcff) + font-semibold + fill icon
            self.setStyleSheet("""
                background-color: #353534;
                border-radius: 8px;
            """)
            self._text_label.setStyleSheet(
                "color: #d0bcff; font-weight: 600; "
                f"font-size: {_label_px}px;")
            # Re-render icon with filled variant for active state
            icon = IconRegistry.icon(self._icon_name, size=_icon_px,
                                     color="#d0bcff", filled=True)
            if icon and not icon.isNull():
                self._icon_label.setPixmap(
                    icon.pixmap(QSize(_icon_px, _icon_px)))
            self._icon_label.setStyleSheet("color: #d0bcff;")
        elif self._is_hovered:
            self.setStyleSheet("""
                background-color: #2a2a2a;
                border-radius: 8px;
            """)
            self._text_label.setStyleSheet(
                f"color: #F5F5F5; font-size: {_label_px}px;")
            # Restore outline icon
            icon = IconRegistry.icon(self._icon_name, size=_icon_px,
                                     color="#cbc3d7", filled=False)
            if icon and not icon.isNull():
                self._icon_label.setPixmap(
                    icon.pixmap(QSize(_icon_px, _icon_px)))
            self._icon_label.setStyleSheet("color: #cbc3d7;")
        else:
            self.setStyleSheet("""
                background-color: transparent;
                border-radius: 8px;
            """)
            # Reference: text-on-surface-variant = #cbc3d7 (not #A0A0A0)
            self._text_label.setStyleSheet(
                f"color: #cbc3d7; font-size: {_label_px}px;")
            icon = IconRegistry.icon(self._icon_name, size=_icon_px,
                                     color="#cbc3d7", filled=False)
            if icon and not icon.isNull():
                self._icon_label.setPixmap(
                    icon.pixmap(QSize(_icon_px, _icon_px)))
            self._icon_label.setStyleSheet("color: #cbc3d7;")

    def set_active(self, active: bool):
        self._is_active = active
        self._update_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked_signal.emit(self._label_text)

    def enterEvent(self, event):
        self._is_hovered = True
        if not self._is_active:
            self._update_style()

    def leaveEvent(self, event):
        self._is_hovered = False
        if not self._is_active:
            self._update_style()


class _SceneRow(QWidget):
    """A row in the Recents or Context list.

    P3.11 visual cleanup:
      - NO internal IDs (UUIDs) shown — only user-facing names
      - ONE active indicator (single left accent bar, no double border)
      - NO extra right edge line (clean right boundary)
      - Larger status indicator (20px — P3.44.4 scale-up from 16px;
        visible and usable against the 44px row without dominating the
        Scene name) using Material icons from IconRegistry
      - Long names elided gracefully (status icon stays right-aligned)

    Active: single purple left accent bar (2px), subtle purple-tinted bg.
    Inactive: no border.
    Hover: subtle surface change.
    """

    clicked_signal = Signal(str)  # emits entity_id
    # P3.34: dedicated Rename Scene affordance (ALL SCENES context rows).
    rename_requested = Signal(str)  # emits entity_id

    # P3.44.4: row-action icon scale constants. The status badge and the
    # rename pencil sit on the RIGHT of 44px rows — previously both
    # rendered at 16px (pencil button 24x24), visually too small relative
    # to the row and the sidebar. Both are now 20px (pencil button 28x28);
    # the ROW HEIGHT itself is unchanged. These constants also feed the
    # elide budget in _elide_text so the geometry never drifts.
    _STATUS_ICON_PX = 20    # status badge icon + box
    _RENAME_ICON_PX = 20    # rename pencil icon
    _RENAME_BTN_PX = 28     # rename pencil button (28x28)

    # P3.11: Scene status → icon name + color mapping.
    # Uses the canonical IconRegistry (Material Symbols Outlined SVGs).
    # P3.34: "draft" and "not_generated" no longer render the EDIT
    # (pencil) glyph — a pencil-looking STATUS badge was mistakable for
    # an edit affordance (user report: the pencil "does nothing and its
    # tooltip says NOT_GENERATED"). No-audio states now use the neutral
    # audio_file glyph; the REAL pencil is the dedicated Rename Scene
    # button on Scene context rows (renameable=True).
    _STATUS_ICONS = {
        "draft":      ("audio_file", "#6B7280"),     # gray — no audio yet
        "ready":      ("check", "#d0bcff"),         # purple — ready check
        "generating": ("schedule", "#F59E0B"),      # amber — in progress
        "complete":   ("check_circle", "#10B981"),  # green — done
        "error":      ("error", "#EF4444"),         # red — error
        # P3.28 derived Scene completeness states (§5) — the same field,
        # derived values; unknown values render no badge (tolerant).
        "not_generated": ("audio_file", "#6B7280"), # gray — nothing generated
        "partial":    ("tune", "#F59E0B"),          # amber — some slots covered
    }

    def __init__(self, scene_id: str, scene_name: str, parent=None,
                 subtitle: str = "", status: str = "",
                 character_id: str = "", renameable: bool = False):
        super().__init__(parent)
        # WA_StyledBackground: ensures the stylesheet background-color is
        # actually painted on the widget.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._scene_id = scene_id
        self._scene_name = scene_name
        self._subtitle = subtitle or ""
        self._status = status or ""
        self._character_id = character_id or ""
        self._is_active = False
        self._is_hovered = False
        # P3.34: taller, more comfortable rows (user-requested 44-56px
        # band). Two-line rows (with subtitle) get a little extra room.
        if self._subtitle:
            self.setFixedHeight(48)
        else:
            self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        # P3.11: left margin 16px for alignment; right margin 8px.
        # NO border on the layout — the active indicator is painted via
        # the widget stylesheet (single 2px left border, not double).
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(6)

        # P3.22 Character Visual Identity: colored dot for character rows.
        # Rendered as a small 8px circle using the character's deterministic
        # color. Only shown when character_id is set.
        self._char_dot = None
        if self._character_id:
            from engine.character_colors import get_character_color
            cc = get_character_color(self._character_id)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(
                "background-color: {0}; border-radius: 5px; "
                "border: none;".format(cc.hex_main))
            dot.setToolTip(cc.name)
            layout.addWidget(dot, 0)
            self._char_dot = dot

        # Primary name label — elided to fit available width.
        # P3.34: primary labels use the 14px body token (was 11px
        # metadata_sm — the rows were too small to read comfortably in
        # the enlarged Recents panel).
        self._label = QLabel(scene_name)
        self._label.setFont(Typography.body_md())
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setWordWrap(False)
        self._label.setMinimumWidth(0)
        layout.addWidget(self._label, 1)  # stretches

        # Optional subtitle label (secondary, smaller, muted)
        if self._subtitle:
            sub_label = QLabel(self._subtitle)
            # P3.34: secondary metadata at 12px (new metadata_md token).
            sub_label.setFont(Typography.metadata_md())
            sub_label.setStyleSheet("color: #6B7280; font-size: 12px;")
            sub_label.setTextFormat(Qt.TextFormat.PlainText)
            sub_label.setWordWrap(False)
            sub_label.setMinimumWidth(0)
            sub_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            layout.addWidget(sub_label, 0)
            self._sub_label = sub_label
        else:
            self._sub_label = None

        # P3.11: Status indicator — Material icon from IconRegistry.
        # P3.44.4: 20px (was 16px) — clearly visible against the 44px row
        # while staying secondary to the Scene name.
        # No frame, no border, no extra line — just a clean icon label.
        self._badge = None
        if self._status and self._status in self._STATUS_ICONS:
            icon_name, color = self._STATUS_ICONS[self._status]
            badge = QLabel()
            _status_px = self._STATUS_ICON_PX
            badge.setFixedSize(_status_px, _status_px)
            badge.setAlignment(Qt.Alignment.AlignCenter)
            # Use IconRegistry to get the canonical Material Symbols icon
            icon = IconRegistry.icon(icon_name, size=_status_px, color=color)
            if icon and not icon.isNull():
                badge.setPixmap(icon.pixmap(QSize(_status_px, _status_px)))
            else:
                # Fallback: larger colored dot if icon not found
                badge.setText("\u25cf")
                badge.setStyleSheet(
                    f"color: {color}; font-size: {_status_px - 4}px;")
            badge.setToolTip(self._status.upper())
            # P3.11: NO border on the badge — prevents the extra right edge line
            badge.setStyleSheet(badge.styleSheet() + "border: none; background: transparent;")
            layout.addWidget(badge, 0)
            self._badge = badge

        # P3.34: dedicated Rename Scene pencil (ALL SCENES context rows
        # only — renameable=True). A real QToolButton with hover /
        # pressed states in the modern SpeechStudio language; tooltip is
        # exactly "Rename Scene" (never a generation status). The button
        # consumes its own clicks (clicking it renames; it does NOT
        # select the row).
        self._rename_btn = None
        if renameable:
            btn = QToolButton()
            # P3.44.4: 20px pencil on a 28x28 button (was 16px / 24x24) —
            # comfortably visible and clickable on the 44px row without
            # changing the row height or the sidebar's footprint.
            _rename_px = self._RENAME_ICON_PX
            pen_icon = IconRegistry.icon("edit", size=_rename_px,
                                         color="#8a8a8a")
            if pen_icon and not pen_icon.isNull():
                btn.setIcon(pen_icon)
                btn.setIconSize(QSize(_rename_px, _rename_px))
            else:
                btn.setText("\u270e")
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setFixedSize(self._RENAME_BTN_PX, self._RENAME_BTN_PX)
            btn.setToolTip("Rename Scene")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QToolButton {
                    background-color: transparent;
                    border: none;
                    border-radius: 5px;
                }
                QToolButton:hover {
                    background-color: #2E2E2E;
                }
                QToolButton:pressed {
                    background-color: #353534;
                }
            """)
            btn.clicked.connect(
                lambda: self.rename_requested.emit(self._scene_id))
            layout.addWidget(btn, 0)
            self._rename_btn = btn

        self._update_style()

    def resizeEvent(self, event):
        """P3.6/P3.11: Re-elide text on resize so long names always fit gracefully."""
        super().resizeEvent(event)
        self._elide_text()

    def _elide_text(self):
        """Truncate the primary label text with an ellipsis if it doesn't fit."""
        if not hasattr(self, "_label") or self._label is None:
            return
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self._label.font())
        # Available width = row width minus margins minus subtitle/badge space
        available = self.width() - 32  # left+right margins
        if self._sub_label is not None:
            available -= self._sub_label.sizeHint().width() + 6
        if self._badge is not None:
            # P3.44.4: 20px badge + 6px spacing (constants — the budget
            # tracks the actual icon geometry).
            available -= self._STATUS_ICON_PX + 6
        if self._char_dot is not None:
            available -= 16  # 10px dot + 6px spacing
        if self._rename_btn is not None:
            # P3.44.4: 28px pencil button + 6px spacing.
            available -= self._RENAME_BTN_PX + 6
        if available < 40:
            available = 40
        elided = fm.elidedText(self._scene_name, Qt.TextElideMode.ElideRight, available)
        self._label.setText(elided)
        # Update tooltip to show the full name when truncated
        if elided != self._scene_name:
            tip = self._scene_name
            if self._subtitle:
                tip += "  ·  " + self._subtitle
            self.setToolTip(tip)
        elif self._subtitle:
            self.setToolTip(self._subtitle)

    def _update_style(self):
        # P3.34: the font-size lives IN THE STYLESHEET — the app-wide
        # theme QSS sets a global 13px base font that silently overrides
        # programmatic setFont() in production; the widget's own
        # stylesheet rule is more specific and wins.
        if self._is_active:
            # P3.11: SINGLE left accent bar (2px). No double border.
            # The old style had border-left on both the widget AND a
            # transparent border creating a visual double line.
            self.setStyleSheet("""
                _SceneRow {
                    background-color: rgba(208, 188, 255, 0.08);
                    border-left: 2px solid #d0bcff;
                    border-right: none;
                    border-top: none;
                    border-bottom: none;
                    border-radius: 0px;
                }
            """)
            self._label.setStyleSheet("color: #d0bcff; font-weight: 600; font-size: 14px; border: none; background: transparent;")
            if self._sub_label is not None:
                self._sub_label.setStyleSheet(
                    "color: #d0bcff; font-size: 12px; opacity: 0.8; border: none; background: transparent;")
        elif self._is_hovered:
            self.setStyleSheet("""
                _SceneRow {
                    background-color: rgba(53, 53, 52, 0.5);
                    border-left: 2px solid transparent;
                    border-right: none;
                    border-top: none;
                    border-bottom: none;
                    border-radius: 0px;
                }
            """)
            self._label.setStyleSheet("color: #F5F5F5; font-size: 14px; border: none; background: transparent;")
            if self._sub_label is not None:
                self._sub_label.setStyleSheet("color: #6B7280; font-size: 12px; border: none; background: transparent;")
        else:
            self.setStyleSheet("""
                _SceneRow {
                    background-color: transparent;
                    border-left: 2px solid transparent;
                    border-right: none;
                    border-top: none;
                    border-bottom: none;
                    border-radius: 0px;
                }
            """)
            self._label.setStyleSheet("color: #A0A0A0; font-size: 14px; border: none; background: transparent;")
            if self._sub_label is not None:
                self._sub_label.setStyleSheet("color: #6B7280; font-size: 12px; border: none; background: transparent;")

    def set_active(self, active: bool):
        self._is_active = active
        self._update_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked_signal.emit(self._scene_id)

    def enterEvent(self, event):
        self._is_hovered = True
        if not self._is_active:
            self._update_style()

    def leaveEvent(self, event):
        self._is_hovered = False
        if not self._is_active:
            self._update_style()


class _CharacterRow(QWidget):
    """P3.24 UX Correction: a Character management row for the CHARACTERS panel.

    The CHARACTERS context list is a real Character management surface —
    each row shows:

        [●] Engineer        [ Male Deep ▼ ] [🗑]

      - the Character's canonical colour dot (authoritative resolver:
        engine.character_colors.get_character_color — same colour as the
        block gutter, block selector, Recents and History),
      - the Character name (double-click for inline rename),
      - a Voice Profile dropdown (assign / change directly),
      - a delete button (safe deletion is confirmed by MainWindow).

    Right-click → "Edit Details..." opens the existing secondary
    CharacterManagementDialog (role / description / tags editor).

    Signals:
        selected(str)                   — row body clicked (character_id)
        rename_requested(str, str)      — (character_id, new_name)
        voice_change_requested(str, str) — (character_id, voice_profile_id
                                           or "" for none)
        delete_requested(str)           — (character_id)
        details_requested(str)          — (character_id)

    The row NEVER mutates the Project itself — MainWindow is the
    controller (same architecture as the rest of the Sidebar).
    """

    selected = Signal(str)
    rename_requested = Signal(str, str)
    voice_change_requested = Signal(str, str)
    delete_requested = Signal(str)
    details_requested = Signal(str)

    def __init__(self, character_id: str, name: str, voices: list,
                 voice_profile_id: str = "", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._character_id = character_id
        self._name = name or "Unnamed"
        self._voices = list(voices or [])
        self._voice_profile_id = voice_profile_id or ""
        self._is_active = False
        self._is_hovered = False
        self._editing = False
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(6)

        # Canonical colour dot — the ONE authoritative resolver.
        from engine.character_colors import get_character_color
        cc = get_character_color(self._character_id)
        self._dot = QLabel()
        self._dot.setFixedSize(10, 10)
        self._dot.setStyleSheet(
            "background-color: {0}; border-radius: 5px; border: none;".format(
                cc.hex_main))
        self._dot.setToolTip("{0} · {1}".format(self._name, cc.name))
        layout.addWidget(self._dot, 0)

        # Name label — double-click starts inline rename.
        self._name_label = QLabel(self._name)
        self._name_label.setFont(Typography.body_md())
        self._name_label.setTextFormat(Qt.TextFormat.PlainText)
        self._name_label.setWordWrap(False)
        self._name_label.setMinimumWidth(0)
        self._name_label.setToolTip(
            "{0}\nDouble-click to rename · Right-click for details".format(
                self._name))
        layout.addWidget(self._name_label, 1)  # stretches

        # Inline rename editor (hidden until double-click).
        self._name_edit = QLineEdit()
        self._name_edit.setFont(Typography.body_md())
        self._name_edit.setFixedHeight(26)
        self._name_edit.setStyleSheet("""
            QLineEdit {
                background-color: #232323;
                color: #F5F5F5;
                border: 1px solid #d0bcff;
                border-radius: 4px;
                padding: 2px 6px;
            }
        """)
        self._name_edit.returnPressed.connect(self._commit_rename)
        self._name_edit.editingFinished.connect(self._commit_rename_safe)
        self._name_edit.hide()
        layout.addWidget(self._name_edit, 1)

        # Voice Profile dropdown — assign / change the Character's voice.
        self._voice_combo = QComboBox()
        self._voice_combo.setFont(Typography.metadata_sm())
        self._voice_combo.setToolTip("Voice Profile used by this Character")
        self._voice_combo.addItem("No voice", "")
        for v in self._voices:
            self._voice_combo.addItem(v.name if hasattr(v, "name") else str(v),
                                      v.id if hasattr(v, "id") else str(v))
        # Select the current voice (if any).
        idx = self._voice_combo.findData(self._voice_profile_id)
        self._voice_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._voice_combo.setMinimumWidth(88)
        self._voice_combo.setMaximumWidth(118)
        self._voice_combo.setFixedHeight(26)
        self._voice_combo.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._voice_combo.setStyleSheet("""
            QComboBox {
                background-color: #232323;
                color: #cbc3d7;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                padding: 2px 6px;
            }
            QComboBox:hover { border-color: #d0bcff; color: #F5F5F5; }
            QComboBox::drop-down { border: none; width: 16px; }
            QComboBox QAbstractItemView {
                background-color: #232323;
                color: #F5F5F5;
                selection-background-color: #353534;
                selection-color: #d0bcff;
            }
        """)
        # IMPORTANT: the combo must not swallow row clicks — it is
        # interactive on its own and starts the popup on press.
        self._voice_combo.setCursor(Qt.CursorShape.ArrowCursor)
        self._voice_combo.currentIndexChanged.connect(self._on_voice_changed)
        layout.addWidget(self._voice_combo, 0)

        # Delete button — MainWindow performs the safe-delete confirmation.
        self._delete_btn = QToolButton()
        del_icon = IconRegistry.icon("delete", size=16, color="#8a8a8a")
        if del_icon and not del_icon.isNull():
            self._delete_btn.setIcon(del_icon)
            self._delete_btn.setIconSize(QSize(16, 16))
        else:
            self._delete_btn.setText("\u2715")
        self._delete_btn.setFixedSize(26, 26)
        self._delete_btn.setToolTip("Delete Character")
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
            }
            QToolButton:hover {
                background-color: #3a2222;
            }
            QToolButton:pressed {
                background-color: #4a2828;
            }
        """)
        self._delete_btn.clicked.connect(
            lambda: self.delete_requested.emit(self._character_id))
        layout.addWidget(self._delete_btn, 0)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        self._update_style()

    # ------------------------------------------------------------------
    # Inline rename
    # ------------------------------------------------------------------
    def start_rename(self) -> None:
        """Enter inline rename mode (double-click on the name)."""
        self._editing = True
        self._name_edit.setText(self._name)
        self._name_label.hide()
        self._name_edit.show()
        self._name_edit.selectAll()
        self._name_edit.setFocus()

    def _commit_rename_safe(self) -> None:
        """editingFinished handler — commit only while editing.

        editingFinished also fires when the editor is already hidden
        (e.g. after returnPressed committed and hid it) — guard against
        double emission."""
        if self._editing:
            self._commit_rename()

    def _commit_rename(self) -> None:
        """Finish inline rename and emit rename_requested."""
        self._editing = False
        new_name = self._name_edit.text().strip()
        self._name_edit.hide()
        self._name_label.show()
        if new_name and new_name != self._name:
            self.rename_requested.emit(self._character_id, new_name)

    def _cancel_rename(self) -> None:
        self._editing = False
        self._name_edit.hide()
        self._name_label.show()

    # ------------------------------------------------------------------
    # Voice / delete / details
    # ------------------------------------------------------------------
    def _on_voice_changed(self, index: int) -> None:
        vid = self._voice_combo.itemData(index) or ""
        self.voice_change_requested.emit(self._character_id, vid)

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act_rename = menu.addAction("Rename")
        act_details = menu.addAction("Edit Details...")
        act_delete = menu.addAction("Delete Character")
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen == act_rename:
            self.start_rename()
        elif chosen == act_details:
            self.details_requested.emit(self._character_id)
        elif chosen == act_delete:
            self.delete_requested.emit(self._character_id)

    # ------------------------------------------------------------------
    # Active / hover visuals (matches _SceneRow language)
    # ------------------------------------------------------------------
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_rename()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self._character_id)

    def enterEvent(self, event):
        self._is_hovered = True
        if not self._is_active:
            self._update_style()

    def leaveEvent(self, event):
        self._is_hovered = False
        if not self._is_active:
            self._update_style()

    def set_active(self, active: bool) -> None:
        self._is_active = active
        self._update_style()

    def _update_style(self) -> None:
        if self._is_active:
            self.setStyleSheet("""
                _CharacterRow {
                    background-color: rgba(208, 188, 255, 0.08);
                    border-left: 2px solid #d0bcff;
                    border-right: none;
                    border-top: none;
                    border-bottom: none;
                    border-radius: 0px;
                }
            """)
            self._name_label.setStyleSheet(
                "color: #d0bcff; font-weight: 600; border: none; "
                "background: transparent;")
        elif self._is_hovered:
            self.setStyleSheet("""
                _CharacterRow {
                    background-color: rgba(53, 53, 52, 0.5);
                    border-left: 2px solid transparent;
                    border-right: none;
                    border-top: none;
                    border-bottom: none;
                    border-radius: 0px;
                }
            """)
            self._name_label.setStyleSheet(
                "color: #F5F5F5; border: none; background: transparent;")
        else:
            self.setStyleSheet("""
                _CharacterRow {
                    background-color: transparent;
                    border-left: 2px solid transparent;
                    border-right: none;
                    border-top: none;
                    border-bottom: none;
                    border-radius: 0px;
                }
            """)
            self._name_label.setStyleSheet(
                "color: #A0A0A0; border: none; background: transparent;")


class Sidebar(QWidget):
    """New Project/Scene Sidebar matching the HTML reference.

    Replaces the legacy Voice Library / History / Presets / Outputs
    QTabWidget with the HTML structure:
        - New Project button
        - Projects / Scenes / Characters navigation
        - Recent Scenes list

    Legacy Voice/History/Preset/Output functionality is preserved via
    signals — MainWindow can connect them to dialogs.
    """

    # New signals (HTML reference)
    new_project_requested = Signal()
    new_scene_requested = Signal()
    scene_selected = Signal(str)  # scene_id (legacy — for scene list clicks)
    recent_item_clicked = Signal(str, str)  # P3.4: (context, entity_id) — context-aware recents click
    projects_clicked = Signal()
    scenes_clicked = Signal()
    characters_clicked = Signal()
    # P3.9: context-aware "add" signals from the + button. The + button
    # now emits the appropriate signal based on the current primary nav
    # context (Projects/Scenes/Characters). History has no add action.
    add_project_requested = Signal()    # + in Projects context
    add_scene_requested = Signal()      # + in Scenes context
    add_character_requested = Signal()  # + in Characters context
    # P3.24 UX Correction: the CHARACTERS panel is a real management
    # surface — these signals carry the inline row actions to MainWindow
    # (the controller that mutates the authoritative Project).
    character_rename_requested = Signal(str, str)       # (id, new_name)
    character_voice_requested = Signal(str, str)        # (id, voice_id or "")
    character_delete_requested = Signal(str)            # (id)
    character_details_requested = Signal(str)           # (id) — secondary dialog
    # P3.24 UX Correction: Assemble Audio lives at the BOTTOM of the
    # sidebar as a prominent, always-reachable action (moved out of the
    # Scene dropdown). It opens the EXISTING assembly workflow — this is
    # NOT a second assembly implementation.
    assemble_audio_requested = Signal()
    # P3.34 UX Correction: dedicated signals for the two new context
    # actions. Both reuse EXISTING authoritative workflows in
    # MainWindow — no second rename system, no second History window.
    scene_rename_requested = Signal(str)      # (scene_id) — pencil on ALL SCENES rows
    open_history_view_requested = Signal()    # ALL HISTORY header icon
    # Library section signals (legacy functionality preserved)
    voice_library_clicked = Signal()
    history_clicked = Signal()
    presets_clicked = Signal()
    outputs_clicked = Signal()

    # Legacy signals (preserved for MainWindow compatibility)
    voice_selected = Signal(str)
    create_voice_requested = Signal()
    delete_voice_requested = Signal(str)
    export_voice_requested = Signal(str)
    history_entry_selected = Signal(str)
    history_reuse_requested = Signal(str)
    history_delete_requested = Signal(str)
    history_export_requested = Signal(str)
    history_project_changed = Signal(str)
    output_file_selected = Signal(str)
    apply_preset_requested = Signal(str)
    save_preset_requested = Signal()
    delete_preset_requested = Signal(str)
    rename_preset_requested = Signal(str)
    export_preset_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # CRITICAL: WA_StyledBackground makes QWidget paint its own stylesheet
        # background. Without this, the stylesheet background-color is ignored
        # and the widget appears transparent — the ambient bleeds through.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("ProjectSceneSidebar")
        self.setStyleSheet("""
            QWidget#ProjectSceneSidebar {
                background-color: #1A1A1A;
                border-radius: 12px;
                border: 1px solid #333333;
            }
        """)
        self._scenes: List[dict] = []  # [{id, name, ...}]
        self._active_scene_id: Optional[str] = None
        self._current_project: str = "Default"
        self._current_recents_context: str = "Projects"  # P3.4: tracks which recents list is shown
        # P3.24: last context-list render (entries + active id + custom
        # empty message) so character rows can be rebuilt when the voice
        # list changes (update_voices → refresh_character_rows_voices).
        self._context_entries: Optional[list] = None
        self._context_active_id: Optional[str] = None
        self._context_empty_message: str = ""

        # Legacy data storage (for dialog access)
        self._voices = []
        self._history = []
        self._presets = []
        self._outputs = []

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        # P3.34: margins 12 / spacing 10 (was 16/16) — the sidebar's
        # fixed-height items (buttons, nav rows, headers) previously
        # exceeded the vertical budget at the default 1440x900 window,
        # crushing the Recents panel below its minimum height (the
        # user-reported "unnecessarily short" panel). Tighter rhythm
        # frees the budget the panel needs; still comfortable spacing.
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ---- New Project button (orange gradient CTA) ----
        self._new_project_btn = QPushButton()
        add_icon = IconRegistry.icon("add", size=IconSize.MD, color=IconColor.WHITE)
        if add_icon and not add_icon.isNull():
            self._new_project_btn.setIcon(add_icon)
            self._new_project_btn.setIconSize(QSize(IconSize.MD, IconSize.MD))
        self._new_project_btn.setText("  New Project")
        self._new_project_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_project_btn.setFixedHeight(40)
        # Use Typography token instead of hardcoded font-size.
        self._new_project_btn.setFont(Typography.cta_main())
        self._new_project_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #F97316, stop:1 #EA580C);
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
            }
            QPushButton:hover { opacity: 0.9; }
            QPushButton:pressed { opacity: 0.8; }
        """)
        self._new_project_btn.clicked.connect(self.new_project_requested.emit)
        layout.addWidget(self._new_project_btn)

        # ---- Navigation items ----
        self._nav_projects = _NavItemButton("Projects", "folder_open")
        self._nav_projects.set_active(True)  # Projects is active by default
        self._nav_projects.clicked_signal.connect(self._on_nav_clicked)
        layout.addWidget(self._nav_projects)

        self._nav_scenes = _NavItemButton("Scenes", "movie_edit")
        self._nav_scenes.clicked_signal.connect(self._on_nav_clicked)
        layout.addWidget(self._nav_scenes)

        # P3.5 Final Correction: HISTORY is now a PRIMARY navigation context
        # (not under LIBRARY). Order: PROJECTS, SCENES, HISTORY, CHARACTERS.
        self._nav_history = _NavItemButton("History", "schedule")
        self._nav_history.clicked_signal.connect(self._on_nav_clicked)
        layout.addWidget(self._nav_history)

        self._nav_characters = _NavItemButton("Characters", "groups")
        self._nav_characters.clicked_signal.connect(self._on_nav_clicked)
        layout.addWidget(self._nav_characters)

        # ---- Recent Scenes section ----
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        header_label = QLabel("RECENT PROJECTS")
        header_label.setFont(Typography.label_caps())
        header_label.setStyleSheet(
            "color: #A0A0A0; letter-spacing: 0.5px;")
        self._recents_header_label = header_label  # P3.4: renamed for context switching
        header_layout.addWidget(header_label)
        header_layout.addStretch()

        # Add button — P3.26 UX fine tune: ICON-ONLY square action button.
        # The previous "+ Add" pill duplicated the plus glyph and the
        # word — redundant. The compact Recent header action is now a
        # clearly visible plus icon only: a real clickable QToolButton
        # (28×28, 22px icon) with distinct hover / pressed states in the
        # modern SpeechStudio language. It reads as an ACTION control,
        # not a navigation item (nav items are full-width rows). The
        # `_add_scene_btn` attribute and the context-aware
        # `_on_add_button_clicked` dispatcher are unchanged.
        self._add_scene_btn = QToolButton()
        add_icon_small = IconRegistry.icon("add", size=IconSize.MD,
                                           color=IconColor.DEFAULT)
        if add_icon_small and not add_icon_small.isNull():
            self._add_scene_btn.setIcon(add_icon_small)
            self._add_scene_btn.setIconSize(QSize(IconSize.MD, IconSize.MD))
        self._add_scene_btn.setText("")
        self._add_scene_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._add_scene_btn.setFixedSize(28, 28)
        self._add_scene_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_scene_btn.setStyleSheet("""
            QToolButton {
                background-color: #262626;
                border: 1px solid #3a3a3a;
                border-radius: 7px;
                padding: 2px;
            }
            QToolButton:hover {
                background-color: #2E2E2E;
                border: 1px solid #d0bcff;
            }
            QToolButton:pressed {
                background-color: #353534;
                border: 1px solid #d0bcff;
            }
        """)
        self._add_scene_btn.setToolTip("New Scene")
        # P3.9: The + button is now context-aware. Instead of always
        # emitting new_scene_requested, it calls _on_add_button_clicked
        # which dispatches to the correct signal based on the current
        # primary nav context (Projects/Scenes/Characters). For History,
        # the button is hidden (no creation action).
        self._add_scene_btn.clicked.connect(self._on_add_button_clicked)
        header_layout.addWidget(self._add_scene_btn)

        layout.addLayout(header_layout)

        # ---- Recents panel (P3.34: STATIC outer surface + inner scroll) ----
        # P3.26 gave the Recents list a visually DISTINCT surface (lifted
        # background, rounded corners, subtle border) — but the styled
        # widget was the SCROLLING content itself, so whenever the list
        # exceeded the viewport the border and rounded corners scrolled
        # away with the rows (user report: the panel must be a fixed
        # floating surface; ONLY the contents may scroll).
        # P3.34 structure:
        #     _recents_panel  (STATIC QFrame: bg + border + radius + padding)
        #       └ _scene_scroll (QScrollArea, transparent, NoFrame)
        #           └ _scene_container (transparent content widget)
        #               └ _scene_list_layout (rows + stretch)
        # The panel never scrolls; only the rows inside the scroll area
        # do. The same shared widget renders Recent Projects / Scenes /
        # Characters / Audio, so the treatment applies to all four
        # contexts. Data model and row widgets are unchanged.
        self._recents_panel = QFrame()
        self._recents_panel.setObjectName("RecentsPanel")
        self._recents_panel.setStyleSheet("""
            QFrame#RecentsPanel {
                background-color: #202024;
                border: 1px solid #323236;
                border-radius: 8px;
            }
        """)
        recents_layout = QVBoxLayout(self._recents_panel)
        # Internal padding of the floating surface.
        recents_layout.setContentsMargins(10, 8, 10, 8)
        recents_layout.setSpacing(0)

        self._scene_scroll = _RecentsScrollArea()
        self._scene_scroll.setWidgetResizable(True)
        self._scene_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scene_scroll.setStyleSheet("background: transparent;")
        self._scene_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # P3.34 heights: the panel guarantees a comfortable presence
        # (min 120px) and grows with content up to 248px — at the new
        # 44-48px row height five recent items (5×48 + 4×2 spacing =
        # 248) fit WITHOUT scrolling at normal desktop resolution;
        # scrolling kicks in only for small windows, large DPI scaling
        # or longer lists (the scrollbar lives INSIDE the static panel).
        self._scene_scroll.setMinimumHeight(120)
        self._scene_scroll.setMaximumHeight(248)

        # P3.34: the scrolling content widget is now TRANSPARENT — the
        # panel surface styling lives on the static outer _recents_panel
        # so the border/radius can never scroll with the content.
        self._scene_container = QWidget()
        self._scene_container.setStyleSheet("background: transparent;")
        self._scene_list_layout = QVBoxLayout(self._scene_container)
        self._scene_list_layout.setContentsMargins(0, 0, 0, 0)
        self._scene_list_layout.setSpacing(2)
        self._scene_list_layout.addStretch()

        self._scene_scroll.setWidget(self._scene_container)
        # The scroll area (with its content) sits INSIDE the static
        # panel surface — stretch 0: the panel never grows to consume
        # the sidebar (P3.6 compact contract).
        recents_layout.addWidget(self._scene_scroll, 0)
        layout.addWidget(self._recents_panel, 0)  # does NOT stretch

        # ---- Placeholder for empty scene list ----
        self._empty_label = QLabel("No scenes yet")
        self._empty_label.setFont(Typography.metadata_sm())
        self._empty_label.setStyleSheet("color: #555555; padding: 8px 16px;")
        self._scene_list_layout.insertWidget(0, self._empty_label)

        # ---- P3.5/P3.6: Full Context List (takes remaining vertical space) ----
        # A second scrollable area that shows ALL items for the current
        # context (all Projects, all Scenes, all Characters, or all History).
        # This is separate from Recents (max 5 MRU) — it shows the
        # complete authoritative dataset.
        # P3.6: removed setMaximumHeight(200) — the context list now stretches
        # to fill the remaining sidebar height (stretch factor 1).
        # P3.34: the header is now a ROW (label + stretch + optional
        # context action). In the History context a compact icon button
        # opens the full Generation History view — clicking a History
        # ENTRY itself only selects it (like every other context list).
        ctx_header_layout = QHBoxLayout()
        ctx_header_layout.setContentsMargins(0, 0, 0, 0)
        ctx_header_layout.setSpacing(8)
        ctx_header = QLabel("ALL ITEMS")
        ctx_header.setFont(Typography.label_caps())
        ctx_header.setStyleSheet(
            "color: #A0A0A0; letter-spacing: 0.5px; margin-top: 8px;")
        self._context_header_label = ctx_header
        ctx_header_layout.addWidget(ctx_header)
        ctx_header_layout.addStretch()

        # P3.34: ALL HISTORY header action — the explicit, always-reachable
        # opener for the full Generation History view. Reuses the EXISTING
        # HistoryViewDialog via MainWindow's canonical handler
        # (_focus_history) — this is NOT a second History window. Same
        # compact icon-button language as the Recents + button (28×28,
        # 22px icon, hover/pressed states). Visible in the History
        # context only; enabled even when History is empty (the view has
        # a proper empty state — "No generated audio yet.").
        self._history_open_btn = QToolButton()
        hist_icon = IconRegistry.icon("schedule", size=IconSize.MD,
                                      color=IconColor.SECONDARY)
        if hist_icon and not hist_icon.isNull():
            self._history_open_btn.setIcon(hist_icon)
            self._history_open_btn.setIconSize(QSize(IconSize.MD, IconSize.MD))
        self._history_open_btn.setText("")
        self._history_open_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._history_open_btn.setFixedSize(28, 28)
        self._history_open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._history_open_btn.setToolTip("Open Generation History")
        self._history_open_btn.setStyleSheet("""
            QToolButton {
                background-color: #262626;
                border: 1px solid #3a3a3a;
                border-radius: 7px;
                padding: 2px;
            }
            QToolButton:hover {
                background-color: #2E2E2E;
                border: 1px solid #d0bcff;
            }
            QToolButton:pressed {
                background-color: #353534;
                border: 1px solid #d0bcff;
            }
        """)
        self._history_open_btn.clicked.connect(
            self.open_history_view_requested.emit)
        self._history_open_btn.setVisible(False)  # History context only
        ctx_header_layout.addWidget(self._history_open_btn)
        layout.addLayout(ctx_header_layout)

        self._context_scroll = QScrollArea()
        self._context_scroll.setWidgetResizable(True)
        self._context_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._context_scroll.setStyleSheet("background: transparent;")
        self._context_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # P3.34: min 100 (was 120) — the sidebar's fixed-height items +
        # the Recents panel minimum previously exceeded the vertical
        # budget at the default 1440x900 window (Qt then crushed BOTH
        # flexible lists below their minimums). The context list
        # stretches to fill all remaining space, so a lower floor costs
        # nothing at normal sizes and keeps the Recents panel in its
        # 120px+ band at windowed sizes.
        self._context_scroll.setMinimumHeight(100)  # always usable

        self._context_container = QWidget()
        self._context_container.setStyleSheet("background: transparent;")
        self._context_list_layout = QVBoxLayout(self._context_container)
        self._context_list_layout.setContentsMargins(0, 0, 0, 0)
        self._context_list_layout.setSpacing(2)
        self._context_list_layout.addStretch()

        self._context_scroll.setWidget(self._context_container)
        layout.addWidget(self._context_scroll, 1)  # P3.6: STRETCHES to fill

        # ---- P3.24 UX Correction: Assemble Audio — prominent bottom action ----
        # "Assemble Audio" was previously buried inside the Scene dropdown
        # (top bar) and was not discoverable. It now lives as a clearly
        # labelled, accent-styled action pinned to the BOTTOM of the left
        # sidebar — reachable regardless of which primary context is
        # selected. It invokes the EXISTING AssembleScenesDialog workflow
        # via MainWindow._on_assemble_scenes (no second implementation).
        self._assemble_btn = QPushButton()
        asm_icon = IconRegistry.icon("graphic_eq", size=IconSize.SM,
                                     color=IconColor.WHITE)
        if asm_icon and not asm_icon.isNull():
            self._assemble_btn.setIcon(asm_icon)
            self._assemble_btn.setIconSize(QSize(IconSize.SM, IconSize.SM))
        self._assemble_btn.setText("  Assemble Audio")
        self._assemble_btn.setFont(Typography.cta_main())
        self._assemble_btn.setFixedHeight(40)
        self._assemble_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._assemble_btn.setToolTip(
            "Combine the generated audio of selected Scenes into one file "
            "(WAV / MP3)")
        self._assemble_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #7C5CD6, stop:1 #6247AA);
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 8px 14px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #8A6BE0, stop:1 #6F53BC);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6A4CC0, stop:1 #553E96);
            }
        """)
        self._assemble_btn.clicked.connect(self.assemble_audio_requested.emit)
        layout.addWidget(self._assemble_btn, 0)  # pinned at the bottom

        # Context list empty state
        self._context_empty_label = QLabel("No items")
        self._context_empty_label.setFont(Typography.metadata_sm())
        self._context_empty_label.setStyleSheet("color: #555555; padding: 8px 16px;")
        self._context_list_layout.insertWidget(0, self._context_empty_label)

        # ---- P3.6: LIBRARY section REMOVED ----
        # The legacy LIBRARY section (Voice Library, Presets, Outputs) has
        # been removed from the sidebar. All three functionalities are
        # preserved and now have permanent homes:
        #   - Voice Library → Tools menu (Ctrl+L) + TopNav "Project" menu +
        #                     Control panel "Change Voice" button
        #   - Presets       → Tools menu "Preset Manager..." + TopNav "Project" menu
        #   - Outputs       → Tools menu "Open Output Folder" + TopNav "Project" menu
        # The sidebar now contains ONLY: New Project, Primary Navigation,
        # Recents, and the full Context List — no duplication with the top
        # navigation menus.
        # The old _nav_voices / _nav_presets / _nav_outputs attributes are
        # kept as None so any legacy references don't crash, but they are
        # no longer created or added to the layout.
        self._nav_voices = None
        self._nav_presets = None
        self._nav_outputs = None

    def _on_nav_clicked(self, label: str) -> None:
        """Handle primary navigation item clicks.

        P3.5 Final Correction: HISTORY is now a primary context alongside
        Projects, Scenes, and Characters. The four primary contexts are
        exclusive — clicking one deactivates the others.

        P3.6: The LIBRARY section has been removed, so there are no longer
        any library items to deactivate. The _nav_voices/_nav_presets/
        _nav_outputs attributes are None — guarded with hasattr checks.

        Emits the corresponding *_clicked signal so MainWindow can update
        the Recents + Context List for the new context.
        """
        # Update active state — only primary nav items are exclusive.
        # P3.5 Final Correction: History is now in this set.
        self._nav_projects.set_active(label == "Projects")
        self._nav_scenes.set_active(label == "Scenes")
        self._nav_history.set_active(label == "History")
        self._nav_characters.set_active(label == "Characters")
        # P3.6: Library items removed — no deactivation needed (they're None).

        # P3.10 FIX: Update _current_recents_context IMMEDIATELY when the
        # nav button is clicked. Previously this was only updated in
        # set_recents() (which is called later by MainWindow). This caused
        # the + button dispatcher (_on_add_button_clicked) to read the OLD
        # context and emit the wrong signal (always New Scene).
        self._current_recents_context = label

        # P3.4: Update the Recents heading based on context
        # (this also calls _update_add_button_for_context)
        self._update_recents_heading(label)

        if label == "Projects":
            self.projects_clicked.emit()
        elif label == "Scenes":
            self.scenes_clicked.emit()
        elif label == "History":
            # P3.5 Final Correction: History nav click switches the context
            # to show the full History list. It does NOT open the HistoryView
            # dialog — the dialog opens when a History ENTRY is clicked.
            # The menu bar / toolbar History action still opens the dialog.
            self.history_clicked.emit()
        elif label == "Characters":
            self.characters_clicked.emit()

    def _on_add_button_clicked(self) -> None:
        """P3.9: Context-aware + button click handler.

        Dispatches to the correct creation signal based on the current
        primary nav context:
          - Projects  → add_project_requested
          - Scenes    → add_scene_requested
          - Characters → add_character_requested
          - History   → no action (button should be hidden)
        """
        ctx = self._current_recents_context
        if ctx == "Projects":
            self.add_project_requested.emit()
        elif ctx == "Scenes":
            self.add_scene_requested.emit()
        elif ctx == "Characters":
            self.add_character_requested.emit()
        elif ctx == "History":
            # No creation action for History — button should be hidden.
            pass

    def _update_add_button_for_context(self, context: str) -> None:
        """P3.9/P3.24/P3.26: Update the + button visibility + tooltip for
        the context.

        P3.26: the button is now ICON-ONLY (a clearly visible plus glyph
        in a 28×28 square) — no text label. The full action name lives in
        the tooltip. The CHARACTERS panel additionally shows a prominent
        "+ Add Character" CTA row at the top of its management list (see
        set_context_list).

        Projects   → visible (tooltip "Add Project")
        Scenes     → visible (tooltip "Add Scene")
        Characters → visible (tooltip "Add Character")
        History    → hidden (no creation action)
        """
        if not hasattr(self, "_add_scene_btn") or self._add_scene_btn is None:
            return
        tooltips = {
            "Projects": "Add Project",
            "Scenes": "Add Scene",
            "Characters": "Add Character",
        }
        if context in tooltips:
            self._add_scene_btn.setVisible(True)
            self._add_scene_btn.setEnabled(True)
            self._add_scene_btn.setToolTip(tooltips[context])
        else:
            # History or unknown — hide the button
            self._add_scene_btn.setVisible(False)

    def _update_recents_heading(self, context: str) -> None:
        """P3.4/P3.5/P3.9: Update the Recents + Context List headings based on context.

        P3.9: Also updates the + button tooltip/visibility for the context.
        """
        headings = {
            "Projects": "RECENT PROJECTS",
            "Scenes": "RECENT SCENES",
            "Characters": "RECENT CHARACTERS",
            "History": "RECENT AUDIO",
        }
        heading_text = headings.get(context, "RECENTS")
        if hasattr(self, "_recents_header_label"):
            self._recents_header_label.setText(heading_text)
        # P3.5: Update the context list header
        ctx_headers = {
            "Projects": "ALL PROJECTS",
            "Scenes": "ALL SCENES",
            "Characters": "ALL CHARACTERS",
            "History": "ALL HISTORY",
        }
        ctx_text = ctx_headers.get(context, "ALL ITEMS")
        if hasattr(self, "_context_header_label"):
            self._context_header_label.setText(ctx_text)
        # P3.34: the ALL HISTORY header icon is visible only in the
        # History context (the explicit opener of the full Generation
        # History view — entries themselves just select).
        if hasattr(self, "_history_open_btn") \
                and self._history_open_btn is not None:
            self._history_open_btn.setVisible(context == "History")
        # P3.9: Update the + button for this context
        self._update_add_button_for_context(context)

    def set_context_list(self, entries: list, active_id: Optional[str] = None,
                         empty_message: str = "") -> None:
        """P3.5: Replace the full context list with the given entries.

        Args:
            entries: list of dicts with 'id', 'name', 'subtitle' (optional),
                     'project_id' (optional, for scenes/characters),
                     'voice_profile_id' (optional, Characters context).
            active_id: P3.5 Final Correction — the entity_id of the currently
                       active row (e.g. active project_id, scene_id, or
                       character_id). The matching row is highlighted.
                       Derived from authoritative MainWindow state; never
                       an independent boolean.
            empty_message: P3.24 — optional custom empty-state message
                       (e.g. "No active project" when no Project is open).

        P3.24 UX Correction: in the CHARACTERS context the rows are
        _CharacterRow management rows (colour dot + inline rename +
        Voice Profile dropdown + delete) — the CHARACTERS panel is a
        real Character management surface, not a read-only list.
        """
        # Remember the last render so update_voices() can rebuild the
        # character rows with fresh Voice Profile lists.
        self._context_entries = list(entries) if entries else []
        self._context_active_id = active_id
        self._context_empty_message = empty_message or ""

        # Clear existing context list rows
        while self._context_list_layout.count() > 1:  # keep the stretch
            item = self._context_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not entries:
            # P3.6: context-specific empty state messages
            empty_messages = {
                "Projects": "No projects yet",
                "Scenes": "No scenes yet",
                "Characters": "No characters yet",
                "History": "No generated audio yet",
            }
            msg = self._context_empty_message or empty_messages.get(
                self._current_recents_context, "No items")
            self._context_empty_label = QLabel(msg)
            self._context_empty_label.setFont(Typography.metadata_sm())
            self._context_empty_label.setStyleSheet(
                "color: #555555; padding: 8px 16px;")
            self._context_list_layout.insertWidget(0, self._context_empty_label)
            # P3.34 FIX (pre-existing): with widgetResizable, a content
            # widget populated AFTER construction keeps its stale viewport
            # size — the fixed-height rows overflowed INVISIBLY with NO
            # scrollbar (e.g. 20 scenes: rows clipped at y≈153 in a 120px
            # viewport, barMax 0). adjustSize() re-applies the content's
            # proper size so the scrollbar range is computed correctly.
            self._context_container.adjustSize()
            self._context_scroll.updateGeometry()
            # P3.24: the CHARACTERS empty state must be actionable —
            # show a real "+ Add Character" button so the user
            # immediately understands what to do.
            if self._current_recents_context == "Characters":
                add_btn = QPushButton("+ Add Character")
                add_btn.setFont(Typography.label_caps())
                add_btn.setFixedHeight(30)
                add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                add_btn.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        color: #d0bcff;
                        border: 1px solid #d0bcff;
                        border-radius: 6px;
                        padding: 4px 12px;
                    }
                    QPushButton:hover {
                        background-color: rgba(208, 188, 255, 0.12);
                    }
                    QPushButton:pressed {
                        background-color: rgba(208, 188, 255, 0.22);
                    }
                """)
                add_btn.clicked.connect(self.add_character_requested.emit)
                self._context_list_layout.insertWidget(1, add_btn)
            return

        # P3.24: CHARACTERS context renders management rows.
        if self._current_recents_context == "Characters":
            # Panel-top CTA — the task's preferred CHARACTERS panel layout:
            #   CHARACTERS            [+ Add Character]
            #   ● Engineer            [ Male Deep ▾ ]
            # Same signal/handler as the header pill — ONE authoritative
            # creation path, a prominent full-label affordance.
            cta = QPushButton("+ Add Character")
            cta.setFont(Typography.label_caps())
            cta.setFixedHeight(30)
            cta.setCursor(Qt.CursorShape.PointingHandCursor)
            cta.setToolTip("Create a new Character in the active Project")
            cta.setStyleSheet("""
                QPushButton {
                    background-color: rgba(208, 188, 255, 0.10);
                    color: #d0bcff;
                    border: 1px solid #d0bcff;
                    border-radius: 6px;
                    padding: 4px 12px;
                }
                QPushButton:hover {
                    background-color: rgba(208, 188, 255, 0.20);
                }
                QPushButton:pressed {
                    background-color: rgba(208, 188, 255, 0.30);
                }
            """)
            cta.clicked.connect(self.add_character_requested.emit)
            self._context_list_layout.insertWidget(0, cta)
            for entry in entries:
                entity_id = entry.get("id", "")
                display_name = entry.get("name", "Unknown")
                voice_profile_id = entry.get("voice_profile_id", "") or ""
                row = _CharacterRow(
                    entity_id, display_name, self._voices,
                    voice_profile_id=voice_profile_id, parent=self)
                if active_id is not None and entity_id == active_id:
                    row.set_active(True)
                row.selected.connect(self._on_character_row_selected)
                row.rename_requested.connect(
                    lambda cid, n: self.character_rename_requested.emit(cid, n))
                row.voice_change_requested.connect(
                    lambda cid, vid: self.character_voice_requested.emit(cid, vid))
                row.delete_requested.connect(
                    lambda cid: self.character_delete_requested.emit(cid))
                row.details_requested.connect(
                    lambda cid: self.character_details_requested.emit(cid))
                self._context_list_layout.insertWidget(
                    self._context_list_layout.count() - 1, row)
            return

        for entry in entries:
            entity_id = entry.get("id", "")
            display_name = entry.get("name", "Unknown")
            subtitle = entry.get("subtitle", "")
            status = entry.get("status", "")  # P3.6: Scene status badge
            # P3.22: Character color dot for character entries
            character_id = entry.get("character_id", "")
            # P3.6: Use the new two-line _SceneRow with subtitle + status badge.
            # The row handles its own text eliding and tooltip.
            # P3.34: in the Scenes context every row is RENAMEABLE — the
            # pencil (tooltip "Rename Scene") triggers the authoritative
            # scene rename workflow via scene_rename_requested. Other
            # contexts render no pencil (their rename lives elsewhere).
            current_context = self._current_recents_context
            row = _SceneRow(entity_id, display_name, self,
                            subtitle=subtitle, status=status,
                            character_id=character_id,
                            renameable=(current_context == "Scenes"))
            # P3.5 Final Correction: apply active state derived from
            # authoritative MainWindow state.
            if active_id is not None and entity_id == active_id:
                row.set_active(True)
            # P3.5: Connect to context-aware click signal
            row.clicked_signal.connect(
                lambda eid, ctx=current_context: self.recent_item_clicked.emit(ctx, eid))
            # P3.34: pencil → the ONE authoritative scene rename workflow.
            if row._rename_btn is not None:
                row.rename_requested.connect(self.scene_rename_requested.emit)
            self._context_list_layout.insertWidget(self._context_list_layout.count() - 1, row)

        # P3.34 FIX (pre-existing): re-apply the content size so the
        # scrollbar range reflects the newly added rows (see the empty-
        # state branch above for the full rationale).
        self._context_container.adjustSize()
        self._context_scroll.updateGeometry()

    def _on_character_row_selected(self, character_id: str) -> None:
        """P3.24: a character management row was clicked — select it.

        Re-emits recent_item_clicked so MainWindow selects the Character
        (same context-aware path as the other contexts)."""
        self.recent_item_clicked.emit("Characters", character_id)

    def refresh_character_rows_voices(self) -> None:
        """P3.24: rebuild the Characters context list after the available
        Voice Profiles changed (update_voices) so the row dropdowns stay
        in sync with the authoritative engine voice list."""
        if (self._current_recents_context == "Characters"
                and getattr(self, "_context_entries", None) is not None):
            self.set_context_list(self._context_entries,
                                  self._context_active_id,
                                  self._context_empty_message)

    def set_active_context_item(self, entity_id: Optional[str]) -> None:
        """P3.5 Final Correction: Update the active row in the context list.

        Iterates the existing context list rows and sets the active state
        on the row whose entity_id matches. Does NOT rebuild the list —
        this is a lightweight refresh for when the active entity changes
        (e.g. user switches Scene while viewing the Scenes context).

        Active state is derived solely from the entity_id passed in,
        which MainWindow computes from _active_project / _active_scene /
        selected Character. No independent booleans are stored here.

        P3.24: also handles _CharacterRow management rows (Characters
        context) — both row types expose set_active(row_id == match).

        Args:
            entity_id: the active entity_id, or None to clear active state.
        """
        for i in range(self._context_list_layout.count()):
            item = self._context_list_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), (_SceneRow, _CharacterRow)):
                row = item.widget()
                row.set_active(row._scene_id == entity_id if isinstance(row, _SceneRow)
                               else row._character_id == entity_id)

    def set_recents(self, entries: list, context: str = "") -> None:
        """P3.4: Replace the Recents list with the given entries.

        Args:
            entries: list of dicts with 'id', 'name', 'subtitle' (optional).
            context: the current context name (for heading + click resolution).
        """
        if context:
            self._update_recents_heading(context)
            self._current_recents_context = context

        # Clear existing recents rows
        while self._scene_list_layout.count() > 1:  # keep the stretch
            item = self._scene_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not entries:
            empty = QLabel("No recent items")
            empty.setFont(Typography.metadata_sm())
            empty.setStyleSheet("color: #555555; padding: 8px 16px;")
            self._scene_list_layout.insertWidget(0, empty)
            self._sync_recents_content_size()
            return

        for entry in entries:
            entity_id = entry.get("id", "")
            display_name = entry.get("name", "Unknown")
            subtitle = entry.get("subtitle", "")  # P3.6: now passed to _SceneRow
            # P3.22: Character color dot for character recents
            character_id = entry.get("character_id", "")
            # P3.6: Use the new _SceneRow with subtitle support.
            row = _SceneRow(entity_id, display_name, self, subtitle=subtitle,
                            character_id=character_id)
            # P3.4: Connect to context-aware recent_item_clicked signal
            # instead of the legacy scene_selected signal.
            current_context = self._current_recents_context
            row.clicked_signal.connect(
                lambda eid, ctx=current_context: self.recent_item_clicked.emit(ctx, eid))
            self._scene_list_layout.insertWidget(self._scene_list_layout.count() - 1, row)

        # P3.34: re-apply the content size (scrollbar range + the live
        # sizeHint of _RecentsScrollArea follow the new row count).
        self._sync_recents_content_size()

    def _sync_recents_content_size(self) -> None:
        """P3.34: keep the Recents panel correctly sized after repopulation.

        Two Qt behaviours make post-construction population fragile with
        widgetResizable: (1) the content widget keeps its stale viewport
        size so fixed-height rows overflow INVISIBLY without a scrollbar
        — adjustSize() re-applies the content's proper size so the
        scrollbar range is computed correctly; (2) QScrollArea::sizeHint
        is cached from the first computation and freshly-added widgets'
        size hints are not synchronously available — so the panel height
        is computed DETERMINISTICALLY from the rows' minimumHeight()
        values (set immediately by setFixedHeight) and applied as the
        scroll area's minimumHeight, clamped to the [120, 248] band.
        Under window squeeze Qt still compresses the panel below the
        minimum (proportional distribution), exactly like the
        pre-P3.34 behaviour.
        """
        spacing = self._scene_list_layout.spacing() or 0
        content_h = 0
        n = 0
        for i in range(self._scene_list_layout.count()):
            w = self._scene_list_layout.itemAt(i).widget()
            if w is not None:
                content_h += max(w.minimumHeight(), 0)
                n += 1
        content_h += max(0, n - 1) * spacing
        desired = max(120, min(248, content_h + 4))
        self._scene_scroll.setMinimumHeight(desired)
        # Re-apply the content size so the scrollbar range matches the
        # new rows (see the docstring, point 1).
        self._scene_container.adjustSize()
        self._scene_scroll.updateGeometry()
        # The synchronous minimum application is swallowed by the
        # deleteLater reaping of the replaced rows — re-apply once the
        # event loop has settled (the layout then honours the minimum
        # immediately; empirically verified). The timer is a CHILD of
        # this sidebar: it can never fire on a destroyed widget and
        # holds no zombie closures.
        if not hasattr(self, "_recents_sync_timer") \
                or self._recents_sync_timer is None:
            self._recents_sync_timer = QTimer(self)
            self._recents_sync_timer.setSingleShot(True)
            self._recents_sync_timer.setInterval(0)
            self._recents_sync_timer.timeout.connect(
                self._deferred_recents_sync)
        self._recents_sync_timer.start()

    def _deferred_recents_sync(self) -> None:
        """P3.34: second half of _sync_recents_content_size (deferred)."""
        self._scene_container.adjustSize()
        self._scene_scroll.updateGeometry()

    def _on_library_clicked(self, label: str) -> None:
        """P3.6: Library section has been REMOVED from the sidebar.

        This method is retained for backward compatibility (in case any
        external caller still connects to it), but it is now a no-op —
        the Voice Library, Presets, and Outputs are reached via the
        Tools menu / TopNav "Project" menu instead.

        The signals (voice_library_clicked, presets_clicked, outputs_clicked)
        are still emitted so MainWindow's existing handlers remain connected
        and functional if anything calls this method.
        """
        # Deactivate ALL primary nav items
        self._nav_projects.set_active(False)
        self._nav_scenes.set_active(False)
        self._nav_history.set_active(False)
        self._nav_characters.set_active(False)
        # P3.6: library nav items are None — no set_active calls needed.

        if label == "Voice Library":
            self.voice_library_clicked.emit()
        elif label == "Presets":
            self.presets_clicked.emit()
        elif label == "Outputs":
            self.outputs_clicked.emit()

    # ------------------------------------------------------------------
    # Public API — Scene management
    # ------------------------------------------------------------------
    def set_project(self, project: str) -> None:
        """Set the current project name (updates context)."""
        self._current_project = project

    def set_scenes(self, scenes: List[dict]) -> None:
        """Replace the scene list.

        Args:
            scenes: List of dicts with keys: id, name.
        """
        self._scenes = list(scenes) if scenes else []

        # Clear existing scene rows (but keep the stretch at the end)
        while self._scene_list_layout.count() > 1:
            item = self._scene_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._scenes:
            self._empty_label = QLabel("No scenes yet")
            self._empty_label.setFont(Typography.metadata_sm())
            self._empty_label.setStyleSheet(
                "color: #555555; padding: 8px 16px;")
            self._scene_list_layout.insertWidget(0, self._empty_label)
        else:
            for scene in self._scenes:
                # P3.6: pass status if available for the status badge
                row = _SceneRow(scene.get("id", ""), scene.get("name", "Untitled"),
                                status=scene.get("status", ""))
                row.clicked_signal.connect(self._on_scene_clicked)
                self._scene_list_layout.insertWidget(
                    self._scene_list_layout.count() - 1, row)
        # P3.34: keep the scroll content correctly sized (see
        # _sync_recents_content_size).
        self._sync_recents_content_size()

        # Re-apply active scene highlight
        if self._active_scene_id:
            self.set_active_scene(self._active_scene_id)

    def set_active_scene(self, scene_id: str) -> None:
        """Highlight the active scene row."""
        self._active_scene_id = scene_id

        # Update all scene rows
        for i in range(self._scene_list_layout.count()):
            item = self._scene_list_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), _SceneRow):
                row = item.widget()
                row.set_active(row._scene_id == scene_id)

    def _on_scene_clicked(self, scene_id: str) -> None:
        """Handle scene row click."""
        self.scene_selected.emit(scene_id)

    # ------------------------------------------------------------------
    # Legacy API (preserved for MainWindow compatibility)
    # ------------------------------------------------------------------
    def update_voices(self, voices: list) -> None:
        """Legacy: store voice list for dialog access.

        P3.24: if the CHARACTERS management panel is currently shown,
        rebuild its rows so the Voice Profile dropdowns reflect the
        updated authoritative engine voice list.
        """
        self._voices = list(voices) if voices else []
        self.refresh_character_rows_voices()

    def update_history(self, entries: list) -> None:
        """Legacy: store history for dialog access."""
        self._history = list(entries) if entries else []

    def update_presets(self, presets: list) -> None:
        """Legacy: store presets for dialog access."""
        self._presets = list(presets) if presets else []

    def update_outputs(self, files: list) -> None:
        """Legacy: store outputs for dialog access."""
        self._outputs = list(files) if files else []
