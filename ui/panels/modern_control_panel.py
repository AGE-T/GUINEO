"""
SpeechStudio — Modern Right Control Panel
=========================================

A new, design-token-driven implementation of the right-hand control panel
that mirrors the approved HTML reference (bg-surface/80, 360px wide,
rounded corners, ``VOICE`` | ``GENERATION`` tab bar, sticky orange CTA
at the bottom).

This module provides :class:`ControlPanel` — a drop-in replacement for
the legacy :class:`ui.panels.control_panel.ControlPanel` that preserves
the EXACT same public API (14 signals + 17 methods) while swapping the
inner widget tree for the modern component set:

    * Token-driven components from :mod:`ui.components` (``VoiceCard``,
      ``EmotionGridButton``, ``SectionHeader``, ``RoundedPanel``,
      ``SegmentedControl``, ``GradientButton``, ``IconButton``,
      ``StatusPill``).
    * Design tokens from :mod:`ui.design_tokens` (``Colors``,
      ``Spacing``, ``Radii``, ``Typography``, ``Surfaces``,
      ``Elevation``, ``ComponentDims``).
    * Icon registry from :mod:`ui.icon_registry` (Material Symbols,
      cached, with inline fallbacks).
    * Engine tokens from :mod:`engine.higgs_tokens`.
    * Engine data models from :mod:`engine.models`.

Layout (matches the approved HTML reference):

    ┌──────────────────────────────────────────────┐
    │  [inheritance banner — hidden by default]    │
    │  ┌──────────┬─────────────┐                   │
    │  │  VOICE   │ GENERATION  │  ← tab bar        │
    │  └──────────┴─────────────┘                   │
    │  ┌──────────────────────────────────────┐    │
    │  │ SELECTED SPEAKER                      │    │
    │  │ ┌──────────────────────────────────┐ │    │
    │  │ │ (avatar) Voice Name  [Change][⋮] │ │    │
    │  │ │          metadata                │ │    │
    │  │ └──────────────────────────────────┘ │    │
    │  │ EMOTION & STYLE                      │    │
    │  │ ┌────┐┌────┐┌────┐┌────┐             │    │
    │  │ │icon││icon││icon││icon│  (×6 rows) │    │
    │  │ └────┘└────┘└────┘└────┘             │    │
    │  │ Custom Style:  [None ▾]              │    │
    │  │ Strength      [████████░░] 75%       │    │
    │  │ ADVANCED CONTROLS                    │    │
    │  │ Stability     [████████░░] 0.95      │    │
    │  │ Similarity    [████████░░] 1.30      │    │
    │  │ Speed         [████████░░] Normal    │    │
    │  │ Allow SFX     [toggle]               │    │
    │  │ ┌─ INLINE CONTROLS HELP ──────────┐  │    │
    │  │ │ <mono text>                     │  │    │
    │  │ └──────────────────────────────────┘  │    │
    │  └──────────────────────────────────────┘    │
    │  ┌──────────────────────────────────────┐    │
    │  │   GENERATE SELECTED LINE             │ ← sticky
    │  └──────────────────────────────────────┘    │
    └──────────────────────────────────────────────┘

The class name ``ControlPanel`` and every signal/method signature are
identical to the legacy panel — :class:`ui.main_window.MainWindow` can
switch between the two implementations with a single import edit.

Compile-time check::

    python3 -m py_compile ui/panels/modern_control_panel.py
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QScrollArea, QStackedWidget, QSizePolicy, QSlider,
    QLineEdit, QButtonGroup, QMenu,
)

from ui.design_tokens import (
    Colors, Spacing, Radii, Typography, Surfaces, Elevation, ComponentDims,
)
from ui.typography import IconSize, IconColor
from ui.icon_registry import IconRegistry
from ui.components import (
    GradientButton, IconButton, VoiceCard, EmotionGridButton,
    SectionHeader, RoundedPanel, SegmentedControl, StatusPill,
)
from engine.higgs_tokens import (
    EMOTIONS, STYLES,
    SFX_TOKENS,
)
from engine.models import GenerationParameters, VoiceProfile


__all__ = ["ControlPanel"]


# ============================================================
# Internal lookup tables
# ============================================================

# Emotion name → Material Symbols icon basename.
# The engine EmotionDef doesn't carry an icon field, so we provide a
# curated mapping that picks the closest Material Symbols glyph for each
# of the 21 emotions. All names resolve through IconRegistry's fallback
# chain (file → inline SVG → '?' placeholder), so missing files never
# break the UI.
_EMOTION_ICON_MAP = {
    "Elation":       "celebration",
    "Amusement":     "celebration",
    "Enthusiasm":    "celebration",
    "Determination": "self_improvement",
    "Pride":         "self_improvement",
    "Contentment":   "sentiment_satisfied",
    "Affection":     "sentiment_satisfied",
    "Relief":        "sentiment_satisfied",
    "Contemplation": "sentiment_neutral",
    "Confusion":     "sentiment_dissatisfied",
    "Surprise":      "sentiment_neutral",
    "Awe":           "sentiment_neutral",
    "Longing":       "sentiment_dissatisfied",
    "Arousal":       "sentiment_very_dissatisfied",
    "Anger":         "sentiment_very_dissatisfied",
    "Fear":          "mood_bad",
    "Disgust":       "mood_bad",
    "Bitterness":    "mood_bad",
    "Sadness":       "sentiment_very_dissatisfied",
    "Shame":         "sentiment_dissatisfied",
    "Helplessness":  "sentiment_very_dissatisfied",
}

# Speed slider index (0..4) → engine prosody name.
_SPEED_LABELS = ["Very Slow", "Slow", "Normal", "Fast", "Very Fast"]

# Delivery SegmentedControl labels (Low/Normal/High) → engine prosody
# delivery names (Expressive Low / Normal / Expressive High).
_DELIVERY_LABELS = ["Low", "Normal", "High"]
_DELIVERY_VALUES = ["Expressive Low", "Normal", "Expressive High"]
_DELIVERY_DEFAULT = "Normal"

# Pitch SegmentedControl labels match engine prosody names directly.
_PITCH_LABELS = ["Low", "Normal", "High"]
_PITCH_DEFAULT = "Normal"

# Slider range constants (integer internal representation for QSlider).
_TEMP_MIN, _TEMP_MAX = 10, 200          # 0.10 .. 2.00 (×100)
_TOP_P_MIN, _TOP_P_MAX = 10, 100        # 0.10 .. 1.00 (×100)
_SILENCE_MIN, _SILENCE_MAX = 0, 50      # 0.0 .. 5.0 (×10)
_STRENGTH_MIN, _STRENGTH_MAX = 0, 100   # 0% .. 100%
_SPEED_MIN, _SPEED_MAX = 0, 4           # 5 prosody options


# ============================================================
# Slider value formatters
# ============================================================
def _fmt_temp(v: int) -> str:
    """Format a temperature slider value (10..200) as ``0.xx``."""
    return "{0:.2f}".format(v / 100.0)


def _fmt_top_p(v: int) -> str:
    """Format a top_p slider value (10..100) as ``0.xx``."""
    return "{0:.2f}".format(v / 100.0)


def _fmt_strength(v: int) -> str:
    """Format a strength slider value (0..100) as ``NN%``."""
    return "{0}%".format(v)


def _fmt_speed(v: int) -> str:
    """Format a speed slider value (0..4) as the prosody name."""
    if 0 <= v < len(_SPEED_LABELS):
        return _SPEED_LABELS[v]
    return "Normal"


def _fmt_silence(v: int) -> str:
    """Format an append-silence slider value (0..50) as ``N.Ns``."""
    return "{0:.1f}s".format(v / 10.0)


# ============================================================
# 1. _ToggleSwitch — custom on/off toggle widget
# ============================================================
class _ToggleSwitch(QWidget):
    """A small custom on/off toggle switch painted from design tokens.

    Visual behaviour
    ----------------
    * **Off** : ``bg_raised`` track + ``text_primary`` handle on the left.
    * **On**  : ``Colors.primary`` track + ``text_primary`` handle on the
                right.

    Clicking anywhere on the widget flips the state and emits
    :attr:`toggled`. Programmatic updates go through :meth:`set_checked`
    which can optionally suppress the signal (``emit=False``).
    """

    toggled = Signal(bool)

    def __init__(self, checked: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self.setFixedSize(36, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def is_checked(self) -> bool:
        return self._checked

    def set_checked(self, checked: bool, emit: bool = True) -> None:
        if self._checked == bool(checked):
            return
        self._checked = bool(checked)
        self.update()
        if emit:
            self.toggled.emit(self._checked)

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.Left:
            self.set_checked(not self._checked)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                           Qt.Key.Key_Enter):
            self.set_checked(not self._checked)
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()

        # Track — primary when on, bg_raised when off.
        track_color = (
            QColor(Colors.primary) if self._checked
            else QColor(Colors.bg_raised)
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(0, 0, w, h, h / 2.0, h / 2.0)

        # Handle — text_primary circle that slides left/right.
        handle_size = h - 4
        handle_y = 2
        handle_x = (w - handle_size - 2) if self._checked else 2
        painter.setBrush(QColor(Colors.text_primary))
        painter.drawEllipse(handle_x, handle_y, handle_size, handle_size)
        painter.end()


# ============================================================
# 2. _Slider — custom-styled QSlider with label + value
# ============================================================
class _Slider(QWidget):
    """Custom-styled slider with a left-aligned label and right-aligned value.

    Wraps a :class:`QSlider` in a vertical layout with a header row
    (label + value). The slider itself uses design-token-driven QSS for
    the groove, sub-page, add-page, and handle.

    Args:
        label:           left-aligned label text.
        minimum:         minimum integer value.
        maximum:         maximum integer value.
        value:           initial integer value.
        value_formatter: callable ``int -> str`` used to render the value.
        parent:          optional parent widget.

    Signals:
        value_changed(int) — emitted whenever the slider value changes
        (suppressed during programmatic ``set_value`` calls).
    """

    value_changed = Signal(int)

    def __init__(self, label: str,
                 minimum: int, maximum: int, value: int,
                 value_formatter=None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._formatter = value_formatter or (lambda v: str(v))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.unit_base)

        # Header row: [label] [stretch] [value]
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(Spacing.unit_base)

        self._label = QLabel(label.upper(), self)
        self._label.setFont(Typography.label_caps())
        self._label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        header.addWidget(self._label)
        header.addStretch(1)

        self._value_label = QLabel(self._formatter(value), self)
        self._value_label.setFont(Typography.mono_data())
        self._value_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_primary
            )
        )
        header.addWidget(self._value_label)
        layout.addLayout(header)

        # Slider
        self._slider = QSlider(Qt.Orientation.Horizontal, self)
        self._slider.setMinimum(minimum)
        self._slider.setMaximum(maximum)
        self._slider.setValue(value)
        self._slider.setObjectName("ModernControlSlider")
        self._slider.setStyleSheet(self._slider_qss())
        self._slider.valueChanged.connect(self._on_value_changed)
        layout.addWidget(self._slider)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def value(self) -> int:
        return self._slider.value()

    def set_value(self, value: int) -> None:
        """Programmatically update the value without firing signals."""
        self._slider.blockSignals(True)
        self._slider.setValue(value)
        self._slider.blockSignals(False)
        self._value_label.setText(self._formatter(value))

    def set_label(self, label: str) -> None:
        self._label.setText(label.upper())

    # ------------------------------------------------------------------
    # Internal slot
    # ------------------------------------------------------------------
    def _on_value_changed(self, value: int) -> None:
        self._value_label.setText(self._formatter(value))
        self.value_changed.emit(value)

    # ------------------------------------------------------------------
    # QSS
    # ------------------------------------------------------------------
    @staticmethod
    def _slider_qss() -> str:
        return (
            "QSlider#ModernControlSlider {{"
            "  border: none;"
            "  background: transparent;"
            "}}"
            "\n"
            "QSlider#ModernControlSlider::groove:horizontal {{"
            "  border: none;"
            "  height: 4px;"
            "  background: {track};"
            "  border-radius: 2px;"
            "}}"
            "\n"
            "QSlider#ModernControlSlider::sub-page:horizontal {{"
            "  background: {primary};"
            "  border-radius: 2px;"
            "}}"
            "\n"
            "QSlider#ModernControlSlider::add-page:horizontal {{"
            "  background: {track};"
            "  border-radius: 2px;"
            "}}"
            "\n"
            "QSlider#ModernControlSlider::handle:horizontal {{"
            "  background: {fg};"
            "  border: 2px solid {primary};"
            "  width: 12px;"
            "  height: 12px;"
            "  margin: -6px 0;"
            "  border-radius: 8px;"
            "}}"
            "\n"
            "QSlider#ModernControlSlider::handle:horizontal:hover {{"
            "  background: {primary};"
            "}}"
            "\n"
            "QSlider#ModernControlSlider:disabled {{"
            "  opacity: 0.5;"
            "}}"
        ).format(
            track=Colors.border_subtle,
            primary=Colors.primary,
            fg=Colors.text_primary,
        )


# ============================================================
# 3. _EmotionGrid — 4-column grid of EmotionGridButton
# ============================================================
class _EmotionGrid(QWidget):
    """4-column grid of :class:`EmotionGridButton` for the 21 emotions.

    Renders all 21 engine emotions in a 4-column :class:`QGridLayout`
    (6 rows × 4 cols + 1 trailing button). Exactly one button can be
    selected at a time; passing ``None`` to :meth:`set_selected`
    deselects all.

    Signals:
        emotion_changed(str) — emits the emotion NAME (e.g. ``"Awe"``)
        when the user clicks a button. Never emits on programmatic
        selection.
    """

    emotion_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._buttons = {}  # name -> EmotionGridButton
        self._selected: Optional[str] = None

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.unit_base)

        cols = ComponentDims.emotion_grid_cols  # 4
        for i, emo in enumerate(EMOTIONS):
            row = i // cols
            col = i % cols
            icon_name = _EMOTION_ICON_MAP.get(emo.name, "sentiment_neutral")
            btn = EmotionGridButton(icon_name=icon_name, label=emo.name)
            btn.setFixedSize(72, 72)
            btn.setToolTip(emo.description)
            btn.clicked.connect(
                lambda _checked=False, name=emo.name: self._on_clicked(name)
            )
            layout.addWidget(btn, row, col)
            self._buttons[emo.name] = btn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_selected(self) -> Optional[str]:
        return self._selected

    def set_selected(self, name: Optional[str]) -> None:
        """Programmatically select an emotion (silent — no signal)."""
        # blockSignals to suppress EmotionGridButton.selected(bool) emits
        # during programmatic resync.
        for btn_name, btn in self._buttons.items():
            btn.blockSignals(True)
            btn.set_selected(btn_name == name)
            btn.blockSignals(False)
        self._selected = name

    # ------------------------------------------------------------------
    # Internal slot
    # ------------------------------------------------------------------
    def _on_clicked(self, name: str) -> None:
        if self._selected == name:
            return  # already selected — no-op
        # Reselection is silent (no selected(bool) signal propagation).
        for btn_name, btn in self._buttons.items():
            btn.blockSignals(True)
            btn.set_selected(btn_name == name)
            btn.blockSignals(False)
        self._selected = name
        self.emotion_changed.emit(name)


# ============================================================
# 4. _StyleDropdown — button that opens a popup menu of styles
# ============================================================
class _StyleDropdown(QWidget):
    """A button that opens a popup menu of engine styles.

    Renders as a single full-width :class:`QPushButton` showing the
    currently-selected style (or ``"None"``). Clicking opens a popup
    menu with the 3 engine styles plus a ``"None"`` clear option.

    Signals:
        style_changed(str) — emits the style NAME (e.g. ``"Whispering"``)
        or an empty string when the user clears the selection.
    """

    style_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._selected: Optional[str] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.unit_base)

        # Left label
        self._label = QLabel("CUSTOM STYLE", self)
        self._label.setFont(Typography.label_caps())
        self._label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        layout.addWidget(self._label)

        # Dropdown button (expands to fill available width)
        self._button = QPushButton("None", self)
        self._button.setFont(Typography.body_md())
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setObjectName("ModernStyleDropdown")
        self._button.setStyleSheet(self._button_qss())
        # Add a small chevron icon on the right via QIcon
        self._button.setIcon(IconRegistry.icon(
            "keyboard_arrow_down", size=IconSize.SM, color=IconColor.SECONDARY,
        ))
        self._button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._button.clicked.connect(self._show_menu)
        layout.addWidget(self._button, 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_selected(self) -> Optional[str]:
        return self._selected

    def set_selected(self, style: Optional[str]) -> None:
        """Silently update the displayed style (no signal)."""
        self._selected = style
        self._button.setText(style if style else "None")

    # ------------------------------------------------------------------
    # Internal slot
    # ------------------------------------------------------------------
    def _show_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_qss())

        # "None" clear option
        act_none = menu.addAction("None")
        act_none.triggered.connect(lambda: self._select(None))
        menu.addSeparator()

        for s in STYLES:
            act = menu.addAction(s.name)
            if self._selected == s.name:
                # Mark currently-selected entry visually.
                act.setIcon(IconRegistry.icon(
                    "check", size=IconSize.SM, color=IconColor.ACCENT,
                ))
            act.triggered.connect(lambda _checked=False, name=s.name: self._select(name))

        # Show below the button.
        pt = self._button.mapToGlobal(QPoint(0, self._button.height()))
        menu.exec(pt)

    def _select(self, style: Optional[str]) -> None:
        if self._selected == style:
            return
        self._selected = style
        self._button.setText(style if style else "None")
        # Per legacy StyleButtons semantics: emit the style name (or
        # empty string for None). MainWindow's _on_style_changed stores
        # None when the signal carries "".
        self.style_changed.emit(style or "")

    # ------------------------------------------------------------------
    # QSS
    # ------------------------------------------------------------------
    @staticmethod
    def _button_qss() -> str:
        return (
            "QPushButton#ModernStyleDropdown {{"
            "  background-color: {bg};"
            "  color: {fg};"
            "  border: 1px solid {border};"
            "  border-radius: {r}px;"
            "  padding: {v}px {h}px;"
            "  text-align: left;"
            "}}"
            "\n"
            "QPushButton#ModernStyleDropdown:hover {{"
            "  border: 1px solid {accent};"
            "  background-color: {hover_bg};"
            "}}"
            "\n"
            "QPushButton#ModernStyleDropdown:pressed {{"
            "  background-color: {press_bg};"
            "}}"
            "\n"
            "QPushButton#ModernStyleDropdown:disabled {{"
            "  color: {disabled};"
            "  border: 1px solid {border};"
            "}}"
        ).format(
            bg=Surfaces.level_2_container,
            fg=Colors.text_primary,
            border=Colors.border_subtle,
            r=Radii.button,
            v=Spacing.padding_input_v,
            h=Spacing.padding_input_h,
            accent=Elevation.button_hover_border,
            hover_bg=Surfaces.level_3_interactable,
            press_bg=Colors.bg_raised,
            disabled=Colors.text_disabled,
        )

    @staticmethod
    def _menu_qss() -> str:
        return (
            "QMenu {{"
            "  background-color: {bg};"
            "  color: {fg};"
            "  border: 1px solid {border};"
            "  border-radius: {r}px;"
            "  padding: {p}px;"
            "}}"
            "\n"
            "QMenu::item {{"
            "  padding: {v}px {h}px;"
            "  border-radius: {r2}px;"
            "}}"
            "\n"
            "QMenu::item:selected {{"
            "  background-color: {sel_bg};"
            "}}"
            "\n"
            "QMenu::separator {{"
            "  height: 1px;"
            "  background: {border};"
            "  margin: {m}px 0;"
            "}}"
        ).format(
            bg=Surfaces.level_2_container,
            fg=Colors.text_primary,
            border=Colors.border_subtle,
            r=Radii.default,
            p=Spacing.unit_base,
            v=Spacing.padding_input_v,
            h=Spacing.padding_input_h,
            r2=Radii.badge,
            sel_bg=Surfaces.level_3_interactable,
            m=Spacing.unit_base,
        )


# ============================================================
# 5. _TabBar — VOICE | GENERATION tab selector
# ============================================================
class _TabBar(QWidget):
    """Two-button exclusive tab selector (VOICE | GENERATION).

    Each tab is a checkable :class:`QPushButton` styled with
    :func:`Typography.label_caps`. The active tab has ``Colors.primary``
    text and a 2px bottom border in the same colour.

    Signals:
        tab_changed(int) — emits the index of the newly-active tab
        (0 = VOICE, 1 = GENERATION).
    """

    tab_changed = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("ModernTabBar")
        self.setStyleSheet(
            "QWidget#ModernTabBar {{"
            "  background-color: transparent;"
            "  border-bottom: 1px solid {border};"
            "}}".format(border=Colors.border_subtle)
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            Spacing.padding_card, 0,
            Spacing.padding_card, 0,
        )
        layout.setSpacing(0)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._tabs: List[QPushButton] = []
        for i, label in enumerate(("VOICE", "GENERATION")):
            btn = QPushButton(label, self)
            btn.setObjectName("ModernTab")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setFont(Typography.label_caps())
            btn.setStyleSheet(self._tab_qss())
            btn.clicked.connect(
                lambda _checked=False, idx=i: self._on_tab_clicked(idx)
            )
            layout.addWidget(btn, 1)
            self._group.addButton(btn, i)
            self._tabs.append(btn)

        # Default to the VOICE tab.
        self._tabs[0].setChecked(True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_active(self, index: int) -> None:
        """Programmatically activate a tab (silent)."""
        if not (0 <= index < len(self._tabs)):
            return
        if self._tabs[index].isChecked():
            return
        self._tabs[index].blockSignals(True)
        self._tabs[index].setChecked(True)
        self._tabs[index].blockSignals(False)

    # ------------------------------------------------------------------
    # Internal slot
    # ------------------------------------------------------------------
    def _on_tab_clicked(self, index: int) -> None:
        self.tab_changed.emit(index)

    # ------------------------------------------------------------------
    # QSS
    # ------------------------------------------------------------------
    @staticmethod
    def _tab_qss() -> str:
        return (
            "QPushButton#ModernTab {{"
            "  background: transparent;"
            "  color: {secondary};"
            "  border: none;"
            "  border-bottom: 2px solid transparent;"
            "  padding: {v}px {h}px;"
            "}}"
            "\n"
            "QPushButton#ModernTab:hover {{"
            "  color: {primary};"
            "}}"
            "\n"
            "QPushButton#ModernTab:checked {{"
            "  color: {primary};"
            "  border-bottom: 2px solid {primary};"
            "}}"
        ).format(
            secondary=Colors.text_secondary,
            primary=Colors.primary,
            v=Spacing.padding_input_v,
            h=Spacing.padding_input_h,
        )


# ============================================================
# 6. ControlPanel — the public class
# ============================================================
class ControlPanel(QFrame):
    """Modern right control panel matching the approved HTML reference.

    A 360px-wide :class:`QFrame` with a semi-transparent ``bg_surface``
    background, rounded corners, a VOICE | GENERATION tab bar, and a
    sticky orange ``Generate`` button at the bottom.

    Drop-in replacement for :class:`ui.panels.control_panel.ControlPanel`
    — every signal and public method signature is preserved. See the
    module docstring for the full layout map.
    """

    # ------------------------------------------------------------------
    # Public signals — EXACT same as legacy ControlPanel
    # ------------------------------------------------------------------
    emotion_changed = Signal(str)
    style_changed = Signal(str)
    speed_changed = Signal(str)
    pitch_changed = Signal(str)
    delivery_changed = Signal(str)
    sfx_inserted = Signal(str, str)             # (sfx_name, onomatopoeia)
    pause_inserted = Signal(str)                # "pause" | "long_pause"
    parameters_changed = Signal()
    voice_changed = Signal(str)                 # voice_id (declared; never emitted by this panel)
    import_voice_requested = Signal()
    create_voice_requested = Signal()
    transcript_changed = Signal(str)
    voice_library_requested = Signal()
    generate_requested = Signal()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("ModernControlPanel")

        # Fixed 360px width per the HTML reference.
        self.setFixedWidth(Spacing.control_panel_width)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Expanding,
        )

        # Semi-transparent bg_surface/80 background, panel border, rounded.
        self.setStyleSheet(
            "QFrame#ModernControlPanel {{"
            "  background-color: {bg};"
            "  border: {bw}px solid {border};"
            "  border-radius: {r}px;"
            "}}".format(
                bg=Colors.rgba(Colors.bg_surface, 204),  # 80% alpha
                bw=Elevation.panel_border_width,
                border=Elevation.panel_border,
                r=Radii.panel,
            )
        )

        # Internal state
        self._batch_update_depth: int = 0
        self._voices: List[VoiceProfile] = []
        self._selected_voice_id: Optional[str] = None
        self._current_voice: Optional[VoiceProfile] = None
        self._allow_sfx: bool = True
        self._strength: int = 75

        # Build the UI tree.
        self._build_ui()

    # ==================================================================
    # UI construction
    # ==================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Inheritance banner (hidden by default) ----
        # A two-part row: a StatusPill on the left showing the mode
        # (PRESET / PROJECT / BLOCK) + a QLabel on the right showing
        # the source description. The whole row is hidden unless
        # set_inheritance_mode is called with a non-empty mode.
        self._inheritance_row = QWidget(self)
        self._inheritance_row.setStyleSheet("background: transparent;")
        self._inheritance_row.setVisible(False)
        inh_layout = QHBoxLayout(self._inheritance_row)
        inh_layout.setContentsMargins(
            Spacing.padding_card, Spacing.unit_base,
            Spacing.padding_card, Spacing.unit_base,
        )
        inh_layout.setSpacing(Spacing.unit_base)

        self._inheritance_pill = StatusPill(
            text="PRESET", status="info", parent=self._inheritance_row,
        )
        inh_layout.addWidget(self._inheritance_pill)

        self._inheritance_banner = QLabel("")
        self._inheritance_banner.setWordWrap(True)
        self._inheritance_banner.setFont(Typography.metadata_sm())
        self._inheritance_banner.setStyleSheet(
            "QLabel {{"
            "  background-color: {bg};"
            "  color: {fg};"
            "  border: 1px solid {border};"
            "  border-radius: {r}px;"
            "  padding: {v}px {h}px;"
            "}}".format(
                bg=Colors.rgba(Colors.bg_surface, 204),
                fg=Colors.text_primary,
                border=Colors.border_subtle,
                r=Radii.badge,
                v=Spacing.unit_base,
                h=Spacing.padding_input_h,
            )
        )
        inh_layout.addWidget(self._inheritance_banner, 1)
        root.addWidget(self._inheritance_row)

        # ---- Tab bar ----
        self._tab_bar = _TabBar(self)
        self._tab_bar.tab_changed.connect(self._on_tab_changed)
        root.addWidget(self._tab_bar)

        # ---- Stacked tab content ----
        self._stack = QStackedWidget(self)
        self._stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._stack.addWidget(self._build_voice_tab())
        self._stack.addWidget(self._build_generation_tab())
        root.addWidget(self._stack, 1)

        # ---- Sticky Generate button (visible on both tabs) ----
        self._generate_btn = GradientButton(
            "GENERATE SELECTED LINE", icon_name="play_arrow", parent=self,
        )
        self._generate_btn.clicked.connect(self.generate_requested.emit)
        # Wrap with a small margin so it doesn't touch the panel edges.
        gen_wrap = QWidget(self)
        gen_wrap.setStyleSheet("background: transparent;")
        gen_layout = QHBoxLayout(gen_wrap)
        gen_layout.setContentsMargins(
            Spacing.padding_card, Spacing.padding_card,
            Spacing.padding_card, Spacing.padding_card,
        )
        gen_layout.setSpacing(0)
        gen_layout.addWidget(self._generate_btn, 1)
        root.addWidget(gen_wrap)

    # ------------------------------------------------------------------
    # VOICE tab
    # ------------------------------------------------------------------
    def _build_voice_tab(self) -> QWidget:
        """Build the VOICE tab: speaker card, emotion grid, advanced controls."""
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setStyleSheet((
            "QScrollArea {{"
            "  background: transparent;"
            "  border: none;"
            "}}"
            "\n"
            "QScrollArea > QWidget > QWidget {{"
            "  background: transparent;"
            "}}"
            "\n"
            "QScrollBar:vertical {{"
            "  background: transparent;"
            "  width: 8px;"
            "  margin: 4px 0;"
            "}}"
            "\n"
            "QScrollBar::handle:vertical {{"
            "  background: {bg};"
            "  border-radius: 4px;"
            "  min-height: 32px;"
            "}}"
            "\n"
            "QScrollBar::handle:vertical:hover {{"
            "  background: {hover};"
            "}}"
            "\n"
            "QScrollBar::add-line:vertical,"
            "QScrollBar::sub-line:vertical {{"
            "  height: 0;"
            "  background: transparent;"
            "}}"
        ).format(
            bg=Colors.bg_raised,
            hover=Colors.border_subtle,
        ))

        container = QWidget(scroll)
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(
            Spacing.padding_card, Spacing.padding_card,
            Spacing.padding_card, Spacing.padding_card,
        )
        layout.setSpacing(Spacing.margin_stack)

        # ---- SELECTED SPEAKER section ----
        layout.addWidget(SectionHeader("SELECTED SPEAKER"))
        self._voice_card = VoiceCard(parent=container)
        self._voice_card.change_voice_clicked.connect(
            self.voice_library_requested.emit
        )
        self._voice_card.more_clicked.connect(self._show_voice_menu)
        layout.addWidget(self._voice_card)

        # ---- EMOTION & STYLE section ----
        # Compose SectionHeader with an IconButton "tune" action on the
        # right. The tune button is a placeholder for future advanced
        # emotion settings — currently decorative (no signal wired).
        emo_header_row = QWidget(container)
        emo_header_row.setStyleSheet("background: transparent;")
        emo_header_layout = QHBoxLayout(emo_header_row)
        emo_header_layout.setContentsMargins(0, 0, 0, 0)
        emo_header_layout.setSpacing(Spacing.unit_base)
        emo_header_layout.addWidget(SectionHeader("EMOTION & STYLE"))
        emo_header_layout.addStretch(1)
        self._emotion_tune_btn = IconButton(
            icon_name="tune",
            size=IconSize.SM,
            color=IconColor.SECONDARY,
            button_size=28,
            parent=emo_header_row,
        )
        emo_header_layout.addWidget(self._emotion_tune_btn)
        layout.addWidget(emo_header_row)

        self._emotion_grid = _EmotionGrid(container)
        self._emotion_grid.emotion_changed.connect(self.emotion_changed.emit)
        layout.addWidget(self._emotion_grid)

        # Style dropdown + Strength slider share a tight column.
        self._style_dropdown = _StyleDropdown(container)
        self._style_dropdown.style_changed.connect(self._on_style_changed)
        layout.addWidget(self._style_dropdown)

        # Strength slider (UI-only state — emits parameters_changed).
        self._strength_slider = _Slider(
            label="STRENGTH",
            minimum=_STRENGTH_MIN,
            maximum=_STRENGTH_MAX,
            value=self._strength,
            value_formatter=_fmt_strength,
            parent=container,
        )
        self._strength_slider.value_changed.connect(
            lambda v: self._emit_parameters_changed()
        )
        layout.addWidget(self._strength_slider)

        # ---- ADVANCED CONTROLS section ----
        layout.addWidget(SectionHeader("ADVANCED CONTROLS"))

        # Stability slider → maps to temperature
        self._stability_slider = _Slider(
            label="STABILITY",
            minimum=_TEMP_MIN,
            maximum=_TEMP_MAX,
            value=_temp_to_slider(GenerationParameters().temperature),
            value_formatter=_fmt_temp,
            parent=container,
        )
        self._stability_slider.value_changed.connect(
            lambda v: self._emit_parameters_changed()
        )
        layout.addWidget(self._stability_slider)

        # Similarity slider → maps to top_p
        self._similarity_slider = _Slider(
            label="SIMILARITY",
            minimum=_TOP_P_MIN,
            maximum=_TOP_P_MAX,
            value=_top_p_to_slider(GenerationParameters().top_p),
            value_formatter=_fmt_top_p,
            parent=container,
        )
        self._similarity_slider.value_changed.connect(
            lambda v: self._emit_parameters_changed()
        )
        layout.addWidget(self._similarity_slider)

        # Speed slider → maps to prosody speed (5 options)
        self._speed_slider = _Slider(
            label="SPEED",
            minimum=_SPEED_MIN,
            maximum=_SPEED_MAX,
            value=_SPEED_LABELS.index("Normal"),
            value_formatter=_fmt_speed,
            parent=container,
        )
        self._speed_slider.value_changed.connect(self._on_speed_slider_changed)
        layout.addWidget(self._speed_slider)

        # Allow SFX toggle (UI-only state — controls SFX buttons in GEN tab).
        sfx_row = QWidget(container)
        sfx_row.setStyleSheet("background: transparent;")
        sfx_layout = QHBoxLayout(sfx_row)
        sfx_layout.setContentsMargins(0, 0, 0, 0)
        sfx_layout.setSpacing(Spacing.unit_base)
        sfx_label = QLabel("ALLOW SFX", sfx_row)
        sfx_label.setFont(Typography.label_caps())
        sfx_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        sfx_layout.addWidget(sfx_label)
        sfx_layout.addStretch(1)
        self._sfx_toggle = _ToggleSwitch(checked=self._allow_sfx, parent=sfx_row)
        self._sfx_toggle.toggled.connect(self._on_sfx_toggled)
        sfx_layout.addWidget(self._sfx_toggle)
        layout.addWidget(sfx_row)

        # ---- INLINE CONTROLS HELP panel ----
        self._help_panel = RoundedPanel(title="INLINE CONTROLS HELP", parent=container)
        help_text = QLabel(
            "Speed/Pitch/Delivery are placed at the\n"
            "start of each sentence.\n"
            "SFX markers are inserted inline at the\ncursor.\n"
            "Pause markers insert ~500ms silences.",
            self._help_panel,
        )
        help_text.setFont(Typography.mono_data())
        help_text.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        help_text.setWordWrap(True)
        self._help_panel.content_layout().addWidget(help_text)
        layout.addWidget(self._help_panel)

        layout.addStretch(1)
        scroll.setWidget(container)
        return scroll

    # ------------------------------------------------------------------
    # GENERATION tab
    # ------------------------------------------------------------------
    def _build_generation_tab(self) -> QWidget:
        """Build the GENERATION tab: prosody, SFX, sampling, output."""
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setStyleSheet((
            "QScrollArea {{"
            "  background: transparent;"
            "  border: none;"
            "}}"
            "\n"
            "QScrollArea > QWidget > QWidget {{"
            "  background: transparent;"
            "}}"
            "\n"
            "QScrollBar:vertical {{"
            "  background: transparent;"
            "  width: 8px;"
            "  margin: 4px 0;"
            "}}"
            "\n"
            "QScrollBar::handle:vertical {{"
            "  background: {bg};"
            "  border-radius: 4px;"
            "  min-height: 32px;"
            "}}"
            "\n"
            "QScrollBar::handle:vertical:hover {{"
            "  background: {hover};"
            "}}"
            "\n"
            "QScrollBar::add-line:vertical,"
            "QScrollBar::sub-line:vertical {{"
            "  height: 0;"
            "  background: transparent;"
            "}}"
        ).format(
            bg=Colors.bg_raised,
            hover=Colors.border_subtle,
        ))

        container = QWidget(scroll)
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(
            Spacing.padding_card, Spacing.padding_card,
            Spacing.padding_card, Spacing.padding_card,
        )
        layout.setSpacing(Spacing.margin_stack)

        # ---- PROSODY section ----
        layout.addWidget(SectionHeader("PROSODY"))

        # Pitch SegmentedControl (Low / Normal / High)
        pitch_label = QLabel("PITCH", container)
        pitch_label.setFont(Typography.label_caps())
        pitch_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        layout.addWidget(pitch_label)
        self._pitch_seg = SegmentedControl(
            labels=_PITCH_LABELS,
            initial=_PITCH_LABELS.index(_PITCH_DEFAULT),
            parent=container,
        )
        self._pitch_seg.segment_changed.connect(
            lambda idx, lbl: self._on_pitch_changed(lbl)
        )
        layout.addWidget(self._pitch_seg)

        # Delivery SegmentedControl (Low / Normal / High → engine prosody names)
        delivery_label = QLabel("DELIVERY", container)
        delivery_label.setFont(Typography.label_caps())
        delivery_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        layout.addWidget(delivery_label)
        self._delivery_seg = SegmentedControl(
            labels=_DELIVERY_LABELS,
            initial=_DELIVERY_LABELS.index("Normal"),
            parent=container,
        )
        self._delivery_seg.segment_changed.connect(
            lambda idx, lbl: self._on_delivery_changed(lbl)
        )
        layout.addWidget(self._delivery_seg)

        # ---- SOUND EFFECTS section ----
        layout.addWidget(SectionHeader("SOUND EFFECTS"))
        self._sfx_buttons: List[QPushButton] = []
        sfx_grid = QWidget(container)
        sfx_grid.setStyleSheet("background: transparent;")
        sfx_layout = QGridLayout(sfx_grid)
        sfx_layout.setContentsMargins(0, 0, 0, 0)
        sfx_layout.setSpacing(Spacing.unit_base)
        cols = 3
        for i, sfx in enumerate(SFX_TOKENS):
            row = i // cols
            col = i % cols
            btn = QPushButton(sfx.name, sfx_grid)
            btn.setFont(Typography.metadata_sm())
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setObjectName("ModernSfxButton")
            btn.setStyleSheet(self._sfx_button_qss())
            btn.clicked.connect(
                lambda _checked=False,
                       name=sfx.name,
                       onom=sfx.onomatopoeia:
                self.sfx_inserted.emit(name, onom)
            )
            sfx_layout.addWidget(btn, row, col)
            self._sfx_buttons.append(btn)
        layout.addWidget(sfx_grid)

        # ---- SAMPLING section ----
        layout.addWidget(SectionHeader("SAMPLING"))

        # Seed input
        seed_row = QWidget(container)
        seed_row.setStyleSheet("background: transparent;")
        seed_layout = QHBoxLayout(seed_row)
        seed_layout.setContentsMargins(0, 0, 0, 0)
        seed_layout.setSpacing(Spacing.unit_base)
        seed_label = QLabel("SEED", seed_row)
        seed_label.setFont(Typography.label_caps())
        seed_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        seed_layout.addWidget(seed_label)
        seed_layout.addStretch(1)
        self._seed_edit = QLineEdit(seed_row)
        self._seed_edit.setPlaceholderText("Random")
        self._seed_edit.setFont(Typography.mono_data())
        self._seed_edit.setFixedWidth(140)
        self._seed_edit.setObjectName("ModernSeedInput")
        self._seed_edit.setStyleSheet(self._input_qss())
        self._seed_edit.textChanged.connect(
            lambda _t: self._emit_parameters_changed()
        )
        seed_layout.addWidget(self._seed_edit)
        layout.addWidget(seed_row)

        # Top K input (line edit; integer only via validation)
        topk_row = QWidget(container)
        topk_row.setStyleSheet("background: transparent;")
        topk_layout = QHBoxLayout(topk_row)
        topk_layout.setContentsMargins(0, 0, 0, 0)
        topk_layout.setSpacing(Spacing.unit_base)
        topk_label = QLabel("TOP K", topk_row)
        topk_label.setFont(Typography.label_caps())
        topk_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        topk_layout.addWidget(topk_label)
        topk_layout.addStretch(1)
        self._topk_edit = QLineEdit(str(GenerationParameters().top_k), topk_row)
        self._topk_edit.setFont(Typography.mono_data())
        self._topk_edit.setFixedWidth(140)
        self._topk_edit.setObjectName("ModernTopKInput")
        self._topk_edit.setStyleSheet(self._input_qss())
        self._topk_edit.textChanged.connect(
            lambda _t: self._emit_parameters_changed()
        )
        topk_layout.addWidget(self._topk_edit)
        layout.addWidget(topk_row)

        # Max Tokens input
        maxtok_row = QWidget(container)
        maxtok_row.setStyleSheet("background: transparent;")
        maxtok_layout = QHBoxLayout(maxtok_row)
        maxtok_layout.setContentsMargins(0, 0, 0, 0)
        maxtok_layout.setSpacing(Spacing.unit_base)
        maxtok_label = QLabel("MAX TOKENS", maxtok_row)
        maxtok_label.setFont(Typography.label_caps())
        maxtok_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        maxtok_layout.addWidget(maxtok_label)
        maxtok_layout.addStretch(1)
        self._maxtok_edit = QLineEdit(
            str(GenerationParameters().max_new_tokens), maxtok_row
        )
        self._maxtok_edit.setFont(Typography.mono_data())
        self._maxtok_edit.setFixedWidth(140)
        self._maxtok_edit.setObjectName("ModernMaxTokInput")
        self._maxtok_edit.setStyleSheet(self._input_qss())
        self._maxtok_edit.textChanged.connect(
            lambda _t: self._emit_parameters_changed()
        )
        maxtok_layout.addWidget(self._maxtok_edit)
        layout.addWidget(maxtok_row)

        # Append Silence slider
        self._silence_slider = _Slider(
            label="APPEND SILENCE",
            minimum=_SILENCE_MIN,
            maximum=_SILENCE_MAX,
            value=_silence_to_slider(GenerationParameters().append_silence),
            value_formatter=_fmt_silence,
            parent=container,
        )
        self._silence_slider.value_changed.connect(
            lambda v: self._emit_parameters_changed()
        )
        layout.addWidget(self._silence_slider)

        # ---- OUTPUT section ----
        layout.addWidget(SectionHeader("OUTPUT"))

        # Normalize output toggle
        norm_row = QWidget(container)
        norm_row.setStyleSheet("background: transparent;")
        norm_layout = QHBoxLayout(norm_row)
        norm_layout.setContentsMargins(0, 0, 0, 0)
        norm_layout.setSpacing(Spacing.unit_base)
        norm_label = QLabel("NORMALIZE OUTPUT", norm_row)
        norm_label.setFont(Typography.label_caps())
        norm_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        norm_layout.addWidget(norm_label)
        norm_layout.addStretch(1)
        self._normalize_toggle = _ToggleSwitch(
            checked=GenerationParameters().normalize_output, parent=norm_row,
        )
        self._normalize_toggle.toggled.connect(
            lambda _checked: self._emit_parameters_changed()
        )
        norm_layout.addWidget(self._normalize_toggle)
        layout.addWidget(norm_row)

        # Auto-play toggle
        autoplay_row = QWidget(container)
        autoplay_row.setStyleSheet("background: transparent;")
        autoplay_layout = QHBoxLayout(autoplay_row)
        autoplay_layout.setContentsMargins(0, 0, 0, 0)
        autoplay_layout.setSpacing(Spacing.unit_base)
        autoplay_label = QLabel("AUTO PLAY", autoplay_row)
        autoplay_label.setFont(Typography.label_caps())
        autoplay_label.setStyleSheet(
            "color: {0}; background: transparent; border: none;".format(
                Colors.text_secondary
            )
        )
        autoplay_layout.addWidget(autoplay_label)
        autoplay_layout.addStretch(1)
        self._autoplay_toggle = _ToggleSwitch(
            checked=GenerationParameters().auto_play, parent=autoplay_row,
        )
        self._autoplay_toggle.toggled.connect(
            lambda _checked: self._emit_parameters_changed()
        )
        autoplay_layout.addWidget(self._autoplay_toggle)
        layout.addWidget(autoplay_row)

        layout.addStretch(1)
        scroll.setWidget(container)
        return scroll

    # ==================================================================
    # QSS helpers (shared by tab, dropdown, SFX, inputs)
    # ==================================================================
    @staticmethod
    def _sfx_button_qss() -> str:
        return (
            "QPushButton#ModernSfxButton {{"
            "  background-color: {bg};"
            "  color: {fg};"
            "  border: 1px solid {border};"
            "  border-radius: {r}px;"
            "  padding: {v}px {h}px;"
            "}}"
            "\n"
            "QPushButton#ModernSfxButton:hover {{"
            "  border: 1px solid {accent};"
            "  background-color: {hover_bg};"
            "}}"
            "\n"
            "QPushButton#ModernSfxButton:pressed {{"
            "  background-color: {press_bg};"
            "}}"
            "\n"
            "QPushButton#ModernSfxButton:disabled {{"
            "  color: {disabled};"
            "  border: 1px solid {border};"
            "  background-color: transparent;"
            "}}"
        ).format(
            bg=Surfaces.level_2_container,
            fg=Colors.text_primary,
            border=Colors.border_subtle,
            r=Radii.button,
            v=Spacing.padding_input_v,
            h=Spacing.padding_input_h,
            accent=Elevation.button_hover_border,
            hover_bg=Surfaces.level_3_interactable,
            press_bg=Colors.bg_raised,
            disabled=Colors.text_disabled,
        )

    @staticmethod
    def _input_qss() -> str:
        return (
            "QLineEdit#ModernSeedInput,"
            "QLineEdit#ModernTopKInput,"
            "QLineEdit#ModernMaxTokInput {{"
            "  background-color: {bg};"
            "  color: {fg};"
            "  border: 1px solid {border};"
            "  border-radius: {r}px;"
            "  padding: {v}px {h}px;"
            "  selection-background-color: {primary};"
            "  selection-color: {on_primary};"
            "}}"
            "\n"
            "QLineEdit#ModernSeedInput:focus,"
            "QLineEdit#ModernTopKInput:focus,"
            "QLineEdit#ModernMaxTokInput:focus {{"
            "  border: 1px solid {accent};"
            "}}"
            "\n"
            "QLineEdit#ModernSeedInput:disabled,"
            "QLineEdit#ModernTopKInput:disabled,"
            "QLineEdit#ModernMaxTokInput:disabled {{"
            "  color: {disabled};"
            "}}"
        ).format(
            bg=Surfaces.level_2_container,
            fg=Colors.text_primary,
            border=Colors.border_subtle,
            r=Radii.input,
            v=Spacing.padding_input_v,
            h=Spacing.padding_input_h,
            primary=Colors.primary,
            on_primary=Colors.on_primary,
            accent=Elevation.input_focus_border,
            disabled=Colors.text_disabled,
        )

    # ==================================================================
    # Public API — getters
    # ==================================================================
    def get_emotion(self) -> Optional[str]:
        """Return the selected emotion name (or ``None``)."""
        return self._emotion_grid.get_selected()

    def get_style(self) -> Optional[str]:
        """Return the selected style name (or ``None``)."""
        return self._style_dropdown.get_selected()

    def get_speed(self) -> str:
        """Return the selected speed prosody name (e.g. ``"Slow"``)."""
        idx = self._speed_slider.value()
        if 0 <= idx < len(_SPEED_LABELS):
            return _SPEED_LABELS[idx]
        return "Normal"

    def get_pitch(self) -> str:
        """Return the selected pitch prosody name (``"Low"``/``"Normal"``/``"High"``)."""
        return self._pitch_seg.selected_label() or _PITCH_DEFAULT

    def get_delivery(self) -> str:
        """Return the engine delivery name (e.g. ``"Expressive High"``)."""
        lbl = self._delivery_seg.selected_label()
        if not lbl:
            return _DELIVERY_DEFAULT
        try:
            idx = _DELIVERY_LABELS.index(lbl)
        except ValueError:
            return _DELIVERY_DEFAULT
        return _DELIVERY_VALUES[idx]

    def get_parameters(self) -> GenerationParameters:
        """Build a fresh :class:`GenerationParameters` from current widget state."""
        # Seed
        seed_text = self._seed_edit.text().strip()
        seed: Optional[int] = None
        if seed_text:
            try:
                seed = int(seed_text)
            except ValueError:
                seed = None
        # Top K
        try:
            top_k = int(self._topk_edit.text().strip())
        except ValueError:
            top_k = GenerationParameters().top_k
        # Max tokens
        try:
            max_new_tokens = int(self._maxtok_edit.text().strip())
        except ValueError:
            max_new_tokens = GenerationParameters().max_new_tokens

        return GenerationParameters(
            temperature=_slider_to_temp(self._stability_slider.value()),
            top_p=_slider_to_top_p(self._similarity_slider.value()),
            top_k=top_k,
            max_new_tokens=max_new_tokens,
            seed=seed,
            append_silence=_slider_to_silence(self._silence_slider.value()),
            normalize_output=self._normalize_toggle.is_checked(),
            auto_play=self._autoplay_toggle.is_checked(),
            output_format="wav",
        )

    def get_selected_voice_id(self) -> Optional[str]:
        """Return the currently-selected voice ID (or ``None``)."""
        return self._selected_voice_id

    # ==================================================================
    # Public API — setters (silent — use blockSignals to avoid feedback)
    # ==================================================================
    def set_emotion(self, emotion: Optional[str]) -> None:
        """Programmatically select an emotion (silent)."""
        self._emotion_grid.set_selected(emotion)

    def set_style(self, style: Optional[str]) -> None:
        """Programmatically select a style (silent).

        Per legacy semantics, an empty string is treated as ``None``.
        """
        if style == "":
            style = None
        self._style_dropdown.set_selected(style)

    def set_speed(self, value: str) -> None:
        """Programmatically set the speed prosody (silent)."""
        try:
            idx = _SPEED_LABELS.index(value)
        except ValueError:
            idx = _SPEED_LABELS.index("Normal")
        self._speed_slider.set_value(idx)

    def set_pitch(self, value: str) -> None:
        """Programmatically set the pitch prosody (silent)."""
        try:
            idx = _PITCH_LABELS.index(value)
        except ValueError:
            idx = _PITCH_LABELS.index(_PITCH_DEFAULT)
        # Use the underlying segmented control's silent setter via
        # blockSignals to suppress segment_changed emission.
        self._pitch_seg.blockSignals(True)
        self._pitch_seg.set_selected(idx)
        self._pitch_seg.blockSignals(False)

    def set_delivery(self, value: str) -> None:
        """Programmatically set the delivery prosody (silent).

        Accepts either the segmented label (``"Low"``/``"Normal"``/``"High"``)
        or the engine prosody name (``"Expressive Low"``/``"Normal"``/
        ``"Expressive High"``).
        """
        if value in _DELIVERY_VALUES:
            idx = _DELIVERY_VALUES.index(value)
        elif value in _DELIVERY_LABELS:
            idx = _DELIVERY_LABELS.index(value)
        else:
            idx = _DELIVERY_LABELS.index("Normal")
        self._delivery_seg.blockSignals(True)
        self._delivery_seg.set_selected(idx)
        self._delivery_seg.blockSignals(False)

    def set_selected_voice_id(self, voice_id: Optional[str]) -> None:
        """Programmatically select a voice by ID (silent).

        Looks up the voice in the cached ``populate_voices`` list and
        updates the VoiceCard. If the ID is ``None`` or unknown, the
        card is cleared.
        """
        self._selected_voice_id = voice_id
        voice = self._lookup_voice(voice_id)
        self._current_voice = voice
        if voice is not None:
            self._voice_card.set_voice(voice)
        else:
            self._voice_card.set_voice(_PlaceholderVoice())  # clears card

    def set_parameters(self, params: GenerationParameters) -> None:
        """Programmatically apply a full parameter set (silent).

        Each widget is updated with ``blockSignals`` so no
        ``parameters_changed`` signal fires during the sync.
        """
        # Block all parameter-affecting widgets.
        self._stability_slider.blockSignals(True)
        self._similarity_slider.blockSignals(True)
        self._silence_slider.blockSignals(True)
        self._seed_edit.blockSignals(True)
        self._topk_edit.blockSignals(True)
        self._maxtok_edit.blockSignals(True)
        self._normalize_toggle.blockSignals(True)
        self._autoplay_toggle.blockSignals(True)

        try:
            self._stability_slider.set_value(_temp_to_slider(params.temperature))
            self._similarity_slider.set_value(_top_p_to_slider(params.top_p))
            self._silence_slider.set_value(_silence_to_slider(params.append_silence))
            self._seed_edit.setText(
                str(params.seed) if params.seed is not None else ""
            )
            self._topk_edit.setText(str(params.top_k))
            self._maxtok_edit.setText(str(params.max_new_tokens))
            self._normalize_toggle.set_checked(params.normalize_output, emit=False)
            self._autoplay_toggle.set_checked(params.auto_play, emit=False)
        finally:
            self._stability_slider.blockSignals(False)
            self._similarity_slider.blockSignals(False)
            self._silence_slider.blockSignals(False)
            self._seed_edit.blockSignals(False)
            self._topk_edit.blockSignals(False)
            self._maxtok_edit.blockSignals(False)
            self._normalize_toggle.blockSignals(False)
            self._autoplay_toggle.blockSignals(False)

    def set_voice_info(self, voice: Optional[VoiceProfile]) -> None:
        """Update the VoiceCard with the given voice (silent).

        Also updates the locally-cached selected voice ID so subsequent
        :meth:`get_selected_voice_id` calls stay consistent.
        """
        self._current_voice = voice
        if voice is not None:
            self._selected_voice_id = voice.id
            self._voice_card.set_voice(voice)
        else:
            self._selected_voice_id = None
            self._voice_card.set_voice(_PlaceholderVoice())

    def populate_voices(self, voices: list) -> None:
        """Cache the list of available voices.

        The new panel has no dropdown — the VoiceCard shows the
        currently-selected voice, and ``Change Voice`` opens the voice
        library dialog. This method preserves the current selection
        across the rebuild; if the previously-selected voice is no
        longer in the list, the selection is cleared (matching the
        legacy VoiceSection behaviour).
        """
        current_id = self._selected_voice_id
        self._voices = list(voices)
        # Try to restore the previously-selected voice's display. If it
        # is no longer present, clear the selection (legacy behaviour:
        # combo falls back to "(No voice)").
        if current_id is not None and self._lookup_voice(current_id) is None:
            self._selected_voice_id = None
            self._current_voice = None
            self._voice_card.set_voice(_PlaceholderVoice())
        elif current_id is not None:
            self.set_selected_voice_id(current_id)

    # ==================================================================
    # Public API — inheritance banner
    # ==================================================================
    def set_inheritance_mode(self, mode: str, source: str = "") -> None:
        """Show or hide the inheritance banner at the top of the panel.

        Args:
            mode: one of ``""``, ``"project"``, ``"block"``, ``"preset"``.
                  An empty string hides the banner.
            source: human-readable description of the inheritance source
                    (e.g. ``"Project Defaults"``, ``"Block 2"``).
        """
        if not mode:
            self._inheritance_row.setVisible(False)
            self._inheritance_banner.setText("")
            return
        # Map the mode to a StatusPill colour + label.
        mode_label = mode.upper()
        if mode == "preset":
            pill_status = "info"      # purple (Colors.primary)
        elif mode == "project":
            pill_status = "success"   # green
        elif mode == "block":
            pill_status = "warning"   # amber
        else:
            pill_status = "info"
        self._inheritance_pill.set_text(mode_label)
        self._inheritance_pill.set_status(pill_status)
        text = "Inheriting from: {0}".format(source or mode)
        self._inheritance_banner.setText(text)
        self._inheritance_row.setVisible(True)

    # ==================================================================
    # Public API — batch update
    # ==================================================================
    def batch_update(self):
        """Return a context manager that collapses parameter_changed emissions.

        Example::

            with control_panel.batch_update():
                control_panel.set_emotion("Awe")
                control_panel.set_style("Whispering")
                control_panel.set_speed("Slow")

        Inside the ``with`` block, every :meth:`_emit_parameters_changed`
        call is suppressed; on exit, a single :attr:`parameters_changed`
        signal fires.
        """
        return _BatchUpdateContext(self)

    def _begin_batch_update(self) -> None:
        self._batch_update_depth += 1

    def _end_batch_update(self) -> None:
        if self._batch_update_depth > 0:
            self._batch_update_depth -= 1
        if self._batch_update_depth == 0:
            # Emit a single parameters_changed so MainWindow rebuilds the
            # prompt preview once for the whole batch.
            self.parameters_changed.emit()

    def _emit_parameters_changed(self) -> None:
        """Emit parameters_changed, unless we're inside a batch_update."""
        if self._batch_update_depth > 0:
            return
        self.parameters_changed.emit()

    # ==================================================================
    # Public API — generate button enable
    # ==================================================================
    def set_generate_enabled(self, enabled: bool) -> None:
        """Enable/disable the Generate button at the bottom of the panel."""
        self._generate_btn.setEnabled(enabled)

    # ==================================================================
    # Public API — reference audio / validation
    # ==================================================================
    def set_reference_validation(self, warnings: list) -> None:
        """Show reference-audio validation warnings.

        The new panel doesn't have a dedicated reference-audio panel
        (the ReferenceAudioPanel is still used by MainWindow elsewhere).
        This method is preserved for API compatibility — currently it
        surfaces the warnings via the inheritance banner row, using a
        ``warning``-coloured :class:`StatusPill`.
        """
        if not warnings:
            # Clear any previously-shown reference warnings ONLY if the
            # inheritance banner isn't currently in use for inheritance.
            if not self._inheritance_row.isVisible():
                self._inheritance_banner.setText("")
            return
        text = "Reference: " + "; ".join(warnings)
        # Show a warning-coloured pill so the issue is visually distinct
        # from inheritance-mode banners.
        self._inheritance_pill.set_text("WARNING")
        self._inheritance_pill.set_status("warning")
        self._inheritance_banner.setText(text)
        self._inheritance_row.setVisible(True)

    def update_reference_audio(self, voice: Optional[VoiceProfile]) -> None:
        """Update the reference audio display.

        The new panel doesn't embed the legacy ReferenceAudioPanel —
        that panel continues to be shown elsewhere by MainWindow. For
        API compatibility, this method updates the VoiceCard so the
        voice metadata stays in sync with the reference audio state.
        """
        if voice is None:
            return
        # If the caller passes the currently-selected voice, just refresh
        # the card; otherwise leave the card alone.
        if self._current_voice is not None and self._current_voice.id == voice.id:
            self._voice_card.set_voice(voice)

    # ==================================================================
    # Internal handlers
    # ==================================================================
    def _on_tab_changed(self, index: int) -> None:
        """Switch the QStackedWidget to the newly-active tab."""
        self._stack.setCurrentIndex(index)

    def _on_speed_slider_changed(self, value: int) -> None:
        """Translate speed slider index → prosody name and emit signal."""
        if 0 <= value < len(_SPEED_LABELS):
            self.speed_changed.emit(_SPEED_LABELS[value])
        self._emit_parameters_changed()

    def _on_pitch_changed(self, label: str) -> None:
        self.pitch_changed.emit(label)
        self._emit_parameters_changed()

    def _on_delivery_changed(self, label: str) -> None:
        """Translate delivery segmented label → engine prosody name."""
        try:
            idx = _DELIVERY_LABELS.index(label)
        except ValueError:
            return
        self.delivery_changed.emit(_DELIVERY_VALUES[idx])
        self._emit_parameters_changed()

    def _on_style_changed(self, style: str) -> None:
        """Translate empty string → None and emit style_changed."""
        # Per legacy StyleButtons semantics: empty string means None.
        self.style_changed.emit(style)

    def _on_sfx_toggled(self, checked: bool) -> None:
        """Enable/disable SFX buttons based on the toggle state."""
        self._allow_sfx = checked
        for btn in self._sfx_buttons:
            btn.setEnabled(checked)
        self._emit_parameters_changed()

    def _show_voice_menu(self) -> None:
        """Show the more_vert popup menu for the voice card."""
        menu = QMenu(self)
        menu.setStyleSheet(_StyleDropdown._menu_qss())

        act_library = menu.addAction("Voice Library…")
        act_library.triggered.connect(self.voice_library_requested.emit)
        menu.addSeparator()
        act_import = menu.addAction("Import Voice…")
        act_import.triggered.connect(self.import_voice_requested.emit)
        act_create = menu.addAction("Create Voice…")
        act_create.triggered.connect(self.create_voice_requested.emit)

        pt = self._voice_card.mapToGlobal(QPoint(
            self._voice_card.width(), 0,
        ))
        menu.exec(pt)

    # ==================================================================
    # Internal helpers
    # ==================================================================
    def _lookup_voice(self, voice_id: Optional[str]) -> Optional[VoiceProfile]:
        """Find a voice in the cached list by ID."""
        if voice_id is None:
            return None
        for v in self._voices:
            if getattr(v, "id", None) == voice_id:
                return v
        return None


# ============================================================
# 7. _BatchUpdateContext — context manager for batch_update()
# ============================================================
class _BatchUpdateContext:
    """Context manager returned by :meth:`ControlPanel.batch_update`.

    Wraps a pair of ``_begin_batch_update`` / ``_end_batch_update`` calls
    so multiple setter calls can be batched into a single
    ``parameters_changed`` emission.
    """

    def __init__(self, panel: "ControlPanel"):
        self._panel = panel

    def __enter__(self) -> "ControlPanel":
        self._panel._begin_batch_update()
        return self._panel

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self._panel._end_batch_update()
        return False  # do not swallow exceptions


# ============================================================
# 8. _PlaceholderVoice — lightweight VoiceProfile stand-in
# ============================================================
class _PlaceholderVoice:
    """A minimal stand-in object compatible with VoiceCard.set_voice.

    Used to clear the VoiceCard display when no voice is selected. We
    don't construct an actual :class:`VoiceProfile` because that would
    require an ``id`` and ``name`` argument that have no meaningful
    value in the "no voice" state.
    """

    def __init__(self) -> None:
        self.id: str = ""
        self.name: str = "—"
        self.preview_image: str = ""
        self.sample_rate: int = 0
        self.duration: float = 0.0
        self.tags: list = []


# ============================================================
# Module-level slider conversion helpers
# ============================================================
def _temp_to_slider(t: float) -> int:
    """Convert a temperature (0.1..2.0) to slider range (10..200)."""
    return max(_TEMP_MIN, min(_TEMP_MAX, int(round(t * 100))))


def _slider_to_temp(v: int) -> float:
    """Convert a slider value (10..200) to temperature (0.1..2.0)."""
    return v / 100.0


def _top_p_to_slider(p: float) -> int:
    """Convert a top_p (0.1..1.0) to slider range (10..100)."""
    return max(_TOP_P_MIN, min(_TOP_P_MAX, int(round(p * 100))))


def _slider_to_top_p(v: int) -> float:
    """Convert a slider value (10..100) to top_p (0.1..1.0)."""
    return v / 100.0


def _silence_to_slider(s: float) -> int:
    """Convert append_silence (0.0..5.0) to slider range (0..50)."""
    return max(_SILENCE_MIN, min(_SILENCE_MAX, int(round(s * 10))))


def _slider_to_silence(v: int) -> float:
    """Convert a slider value (0..50) to append_silence (0.0..5.0)."""
    return v / 10.0


# ============================================================
# Standalone smoke test
# ============================================================
if __name__ == "__main__":  # pragma: no cover — manual visual check
    import sys
    from PySide6.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)

    # Apply the application theme so colours match the production UI.
    try:
        from ui.theme import apply_theme
        apply_theme(app)
    except Exception:
        pass

    win = QMainWindow()
    win.setWindowTitle("SpeechStudio — Modern Control Panel Smoke Test")
    win.setStyleSheet("background-color: {0};".format(Colors.bg_base))

    panel = ControlPanel()
    win.setCentralWidget(panel)
    win.resize(420, 800)
    win.show()

    sys.exit(app.exec())
