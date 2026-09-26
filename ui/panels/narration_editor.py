"""
SpeechStudio Narration Editor.

Three editing modes:
- Plain Text: standard text editor (default, beginner-friendly)
- Narration Blocks: block boundaries visible, properties editable
- Preview: read-only, syntax-highlighted final prompt with statistics

The AdvancedPromptEditor functionality (line numbers, syntax highlighting,
bracket matching, search/replace) has been MERGED into this editor via
the BlockAwarePlainTextEdit inheriting from CodeEditor instead of
QPlainTextEdit.  A "Raw mode" checkbox exposes the editor without block
decorations; a search/replace bar is available via the spyglass button.
"""

from __future__ import annotations
from typing import Optional, List

from PySide6.QtCore import Qt, Signal, QRegularExpression, QTimer, QRect
from PySide6.QtGui import (
    QFont, QColor, QSyntaxHighlighter, QTextCharFormat,
    QTextCursor, QTextFormat, QPainter, QPen, QBrush, QFontMetrics,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QPlainTextEdit,
    QLabel, QButtonGroup, QRadioButton, QComboBox, QCheckBox,
    QMessageBox, QFrame, QToolButton, QSizePolicy, QLineEdit,
)

from ui.theme import Palette
from engine.logger import get_logger

logger = get_logger("narration_editor")
from ui.typography import Typography
from ui.panels.advanced_prompt_editor import (
    CodeEditor, HiggsTokenHighlighter, SearchReplaceBar,
)
from engine.narration_blocks import PromptBlock, NarrationTemplate, BUILTIN_TEMPLATES
from engine.narration_block_manager import NarrationBlockManager
from engine.prompt_optimizer import PromptOptimizer
from engine.prompt_builder import PromptBuilder
from engine.character_colors import get_character_color


# ---------------------------------------------------------------------------
# Block gutter widget (separate child widget — paints in the margin area)
# ---------------------------------------------------------------------------
class _BlockGutterWidget(QWidget):
    """Separate widget that paints block headers in the left margin.

    This widget is positioned by BlockAwarePlainTextEdit in the area
    between the LineNumberArea and the text viewport.  It paints
    block numbers, emotion/style labels, override badges, status badges,
    and boundary lines — all WITHOUT touching the text viewport.

    P3.41 (adjacent-block collapse fix) — ROW LAYOUT CONTRACT:
        Every block is painted into exactly one ROW. Pass 1 maps each
        block's offsets to the ACTUAL document geometry (QTextCursor +
        cursorRect — the same math the editor itself uses). Pass 2 gives
        every row its document position, a minimum height of
        MIN_ROW_HEIGHT, and a monotonically stacked position so that
        consecutive rows can NEVER overlap.

        Why: when the user removes the blank line(s) between two blocks,
        both blocks' offsets legitimately map to the SAME document line
        (the offsets themselves stay correct — NarrationBlockManager
        delta-tracks them). Painting raw document y ranges painted both
        headers at the same y — the labels collapsed into each other
        ("B1 · ANNA" over "B2 · MÁRK"). The row layout follows the
        document geometry wherever it can and only stacks when the
        geometry is degenerate — no arbitrary spacing is added.

        Painting and click hit-testing share the ONE `_block_rows()`
        layout, so clicking a stacked row selects exactly that block.

    Block status tracking (HTML reference):
        - Pending: clock icon (text_disabled), dimmed
        - Generating: spinning gear (accent), pulsing bg
        - Done: green check (success)
        - Error: red warning (error)
        - None: no status (not yet generated, editor mode)
    """

    # P3.41: minimum painted row height for one block = its own header
    # line: 1px separator + 2px top inset + 16px header band (the
    # drawRect height used for the header text) - 1px = 18px. Derived
    # from the header geometry below — NOT an arbitrary spacing value.
    MIN_ROW_HEIGHT = 18

    def __init__(self, editor: "BlockAwarePlainTextEdit"):
        super().__init__(editor)
        self._editor = editor
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(True)

    def _block_rows(self):
        """P3.41: the ONE gutter row layout (shared by painting + clicks).

        Returns a list aligned with ``editor._blocks``: each entry is
        ``(block, row_top, row_bottom)`` or ``None`` for a block whose
        offsets are degenerate (start >= end — nothing to paint).
        """
        editor = self._editor
        text = editor.toPlainText()
        rows = []
        prev_bottom = -2
        for block in editor._blocks:
            start = max(0, min(block.start_offset, len(text)))
            end = max(start, min(block.end_offset, len(text)))
            if start >= end:
                rows.append(None)
                continue
            cur = QTextCursor(editor.document())
            cur.setPosition(start)
            y_top = editor.cursorRect(cur).top()
            cur2 = QTextCursor(editor.document())
            cur2.setPosition(max(start, end - 1))
            y_bottom = editor.cursorRect(cur2).bottom()
            # P3.41: anchor at the document position, never overlap the
            # previous row, and always fit one full header line.
            row_top = max(y_top, prev_bottom + 1)
            row_bottom = max(y_bottom, row_top + self.MIN_ROW_HEIGHT)
            rows.append((block, row_top, row_bottom))
            prev_bottom = row_bottom
        return rows

    def paintEvent(self, event) -> None:
        editor = self._editor
        blocks = editor._blocks
        if not blocks or not editor._show_blocks:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gw = self.width()
        vh = self.height()
        text = editor.toPlainText()
        ln_width = editor.line_number_area_width()

        # Background — semi-transparent so ambient shows through
        bg = QColor(Palette.BG_SURFACE)
        bg.setAlpha(200)
        painter.fillRect(self.rect(), bg)

        # P3.41: paint the shared ROW layout (see _block_rows) — every
        # block gets ONE non-overlapping row anchored at its document
        # geometry, tall enough to hold its own header line. Blocks that
        # share a document line (e.g. after the user removed the
        # separator blank line) stack in document order instead of
        # painting their labels on top of each other.
        for i, row in enumerate(self._block_rows()):
            if row is None:
                continue
            block, y_top, y_bottom = row

            if y_bottom < 0 or y_top > vh:
                continue

            # Block height in pixels — determines how much header text fits
            block_height = y_bottom - y_top
            # Minimum heights for each level of detail
            MIN_FOR_FULL = 60   # Block N + E: + S: + P:
            MIN_FOR_MEDIUM = 40  # Block N + E:
            MIN_FOR_COMPACT = 20  # Block N only

            is_selected = (block.id == editor._selected_block_id)
            has_overrides = block.has_overrides()

            # P3.22 Character System: Character color strip (3px) at the very
            # left edge. Drawn BEFORE the accent bar so the accent bar sits
            # just to the right of it. Only drawn when the block has a
            # character_id assigned.
            # P3.23 (design record §10): a missing/deleted Character must NOT
            # receive a real Character color — the strip is only painted when
            # the ID resolves to a known Character in the active Project.
            # P3.23 (design record §25): a lost_character_id paints an amber
            # warning strip so the user immediately sees the block needs
            # re-assignment ("B1 ⚠").
            char_id = getattr(block, 'character_id', None)
            lost_id = getattr(block, 'lost_character_id', None)
            accent_x = 0  # default: accent bar starts at x=0
            # P3.23: the gutter's `editor` is the inner BlockAwarePlainTextEdit;
            # the character list is mirrored onto it by
            # NarrationEditor.set_characters() as _gutter_characters.
            characters = getattr(editor, "_gutter_characters", None) or []
            if char_id:
                known_ids = {c.get("id") for c in characters}
                if char_id in known_ids:
                    char_color = QColor(get_character_color(char_id).hex_main)
                    painter.fillRect(QRect(0, y_top, 3, block_height), char_color)
                    accent_x = 3  # shift accent bar right to make room
            elif lost_id:
                painter.fillRect(QRect(0, y_top, 3, block_height),
                                QColor(Palette.WARNING))
                accent_x = 3

            # P3.15: Single left accent bar (2px) — NOT a 3px bar at the right
            # edge. This is the ONE active indicator (no double border).
            if is_selected:
                bar_color = QColor(Palette.ACCENT)
            elif has_overrides:
                bar_color = QColor(Palette.WARNING)
            else:
                bar_color = QColor(Palette.BORDER_FOCUS)
            bar_color.setAlpha(220)

            # P3.15: Single left accent bar (2px wide, clean)
            painter.fillRect(QRect(accent_x, y_top, 2, block_height), bar_color)

            # Selected block background (subtle, starts AFTER the accent bar)
            if is_selected:
                sel_bg = QColor(Palette.ACCENT)
                sel_bg.setAlpha(20)
                painter.fillRect(QRect(accent_x + 2, y_top, gw - accent_x - 2, block_height), sel_bg)

            # P3.15: Single top separator line (no bottom separator to avoid
            # visual clutter — the top line of the NEXT block is the boundary)
            sep_pen = QPen(QColor(Palette.BORDER))
            sep_pen.setWidth(1)
            painter.setPen(sep_pen)
            painter.drawLine(0, y_top, gw, y_top)

            # Header text — adapt detail level to block height
            gx = 8  # P3.15: consistent left padding (was 6)
            gy = y_top

            # Block number (always shown) — P3.15: clear, bold
            num_font = QFont("Segoe UI", 9)
            num_font.setBold(True)
            painter.setFont(num_font)
            painter.setPen(
                QColor(Palette.TEXT_PRIMARY) if is_selected
                else QColor(Palette.TEXT_SECONDARY))
            # P3.23 (design record §13): the gutter shows the Character name
            # next to the block number — "B1 · Engineer" — so the user
            # immediately sees which block has a Character, which Character
            # it is, and where the Character changes between blocks. The
            # name is truncated (elided) when space requires it. No UUIDs
            # are exposed.
            header_text = "B{0}".format(i + 1)
            if char_id:
                char_name = next(
                    (c.get("name") for c in characters
                     if c.get("id") == char_id), None)
                if char_name:
                    header_text = "B{0} \u00b7 {1}".format(i + 1, char_name)
            elif lost_id:
                # P3.23 (design record §25): warning marker for a lost
                # Character assignment (Re-detect ambiguity / deletion).
                header_text = "B{0} \u26a0".format(i + 1)
            # P3.15: leave room for override badge on the right (gw - 30)
            fm = QFontMetrics(num_font)
            header_text = fm.elidedText(
                header_text, Qt.TextElideMode.ElideRight, gw - 30)
            painter.drawText(
                QRect(gx, gy + 2, gw - 30, 16),
                Qt.Alignment.AlignLeft | Qt.Alignment.AlignTop,
                header_text)

            # Block status badge (HTML reference: Done/Generating/Pending/Error)
            status = getattr(block, "status", None)
            if status:
                status_font = QFont("Segoe UI", 8)
                status_font.setBold(True)
                painter.setFont(status_font)

                status_colors = {
                    "done": (Palette.SUCCESS, "\u2713 Done"),
                    "generating": (Palette.ACCENT, "\u2699 Gen..."),
                    "pending": (Palette.TEXT_DISABLED, "\u23f3 Queued"),
                    "error": (Palette.ERROR, "\u26a0 Error"),
                }
                color, label = status_colors.get(status,
                                                  (Palette.TEXT_SECONDARY, status))
                painter.setPen(QColor(color))

                # Draw status badge at top-right of block
                painter.drawText(
                    QRect(gx, gy + 2, gw - 6, 16),
                    Qt.Alignment.AlignRight | Qt.Alignment.AlignTop,
                    label)

                # If generating, draw a subtle progress indicator
                if status == "generating":
                    progress = getattr(block, "progress", 0.0)
                    if progress > 0:
                        bar_y = gy + block_height - 4
                        bar_w = gw - 8
                        painter.fillRect(
                            QRect(gx, bar_y, bar_w, 3),
                            QColor(Palette.BG_RAISED))
                        fill_w = int(bar_w * progress)
                        painter.fillRect(
                            QRect(gx, bar_y, fill_w, 3),
                            QColor(Palette.ACCENT))

            # Detail font for E:/S:/P: — P3.15: consistent alignment
            detail_font = QFont("Segoe UI", 8)

            if block_height >= MIN_FOR_FULL:
                # Full detail: Block N, E:, S:, P:
                painter.setFont(detail_font)
                emo_val = (block.emotion if block.emotion is not None
                           else editor._global_defaults.get("emotion"))
                emo_ov = block.emotion is not None
                painter.setPen(
                    QColor(Palette.ACCENT) if emo_ov
                    else QColor(Palette.TEXT_SECONDARY))
                painter.drawText(
                    QRect(gx, gy + 18, gw - 12, 14),
                    Qt.Alignment.AlignLeft | Qt.Alignment.AlignTop,
                    "E:{0}".format(emo_val or "\u2014"))

                style_val = (block.style if block.style is not None
                             else editor._global_defaults.get("style"))
                style_ov = block.style is not None
                painter.setPen(
                    QColor(Palette.ACCENT) if style_ov
                    else QColor(Palette.TEXT_SECONDARY))
                painter.drawText(
                    QRect(gx, gy + 32, gw - 12, 14),
                    Qt.Alignment.AlignLeft | Qt.Alignment.AlignTop,
                    "S:{0}".format(style_val or "\u2014"))

                prosody_ov = [v for v in (block.speed, block.pitch,
                                          block.delivery)
                              if v is not None and v != "Normal"]
                if prosody_ov:
                    painter.setPen(QColor(Palette.INFO))
                    painter.drawText(
                        QRect(gx, gy + 46, gw - 12, 14),
                        Qt.Alignment.AlignLeft | Qt.Alignment.AlignTop,
                        "P:{0}".format(",".join(prosody_ov)))

            elif block_height >= MIN_FOR_MEDIUM:
                # Medium detail: Block N + E: only
                painter.setFont(detail_font)
                emo_val = (block.emotion if block.emotion is not None
                           else editor._global_defaults.get("emotion"))
                emo_ov = block.emotion is not None
                painter.setPen(
                    QColor(Palette.ACCENT) if emo_ov
                    else QColor(Palette.TEXT_SECONDARY))
                painter.drawText(
                    QRect(gx, gy + 18, gw - 12, 14),
                    Qt.Alignment.AlignLeft | Qt.Alignment.AlignTop,
                    "E:{0}".format(emo_val or "\u2014"))

            # P3.15: Override count badge — visually distinct from Block number.
            # Drawn as a small rounded pill at the right edge with the count.
            # Uses WARNING color (amber) to distinguish from Block number
            # (which uses TEXT_PRIMARY/SECONDARY).
            if has_overrides:
                count = sum(1 for v in (block.emotion, block.style,
                                        block.speed, block.pitch,
                                        block.delivery) if v is not None)
                # P3.15: Badge at top-right, below the block number line
                badge_x = gw - 24
                badge_y = gy + 3
                badge_w = 18
                badge_h = 14
                painter.setBrush(QBrush(QColor(Palette.WARNING)))
                painter.setPen(Qt.PenStyle.NoPen)
                badge_rect = QRect(badge_x, badge_y, badge_w, badge_h)
                painter.drawRoundedRect(badge_rect, 4, 4)
                painter.setPen(QColor(Palette.BG_BASE))
                badge_font = QFont("Segoe UI", 7)
                badge_font.setBold(True)
                painter.setFont(badge_font)
                painter.drawText(badge_rect, Qt.Alignment.AlignCenter, str(count))

            # Flash overlay
            if block.id in editor._flash_ids and editor._flash_alpha > 0:
                flash = QColor(Palette.ACCENT)
                flash.setAlpha(int(90 * editor._flash_alpha))
                painter.fillRect(QRect(0, y_top, gw, y_bottom - y_top), flash)

        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        editor = self._editor
        click_y = int(event.position().y())
        text = editor.toPlainText()
        # P3.41: hit-test against the SAME row layout the painter uses —
        # a stacked row (blocks sharing a document line after the user
        # removed the separator) selects exactly the block whose row was
        # clicked, not whichever block happens to map to the raw
        # document y range first.
        for row in self._block_rows():
            if row is None:
                continue
            block, y_top, y_bot = row
            if y_top <= click_y <= y_bot:
                start = max(0, min(block.start_offset, len(text)))
                cursor = QTextCursor(editor.document())
                cursor.setPosition(start)
                editor.setTextCursor(cursor)
                editor.setFocus()
                # P3.26: setTextCursor alone emits cursorPositionChanged
                # ONLY when the cursor position actually changes. When the
                # cursor already sits at this block's start offset (e.g.
                # offset 0 right after a Scene load / auto-detect), the
                # signal never fires and the block is never selected —
                # the "B1 first click" defect. Drive the selection
                # explicitly as well.
                editor.block_click_requested.emit(start)
                return


class PreviewHighlighter(QSyntaxHighlighter):
    """Highlights Higgs tokens in the Preview mode."""

    def __init__(self, document):
        super().__init__(document)
        self._formats = [
            (QRegularExpression(r"<\|emotion:\w+\|>"),
             self._make_format(Palette.ACCENT)),
            (QRegularExpression(r"<\|style:\w+\|>"),
             self._make_format(Palette.INFO)),
            (QRegularExpression(r"<\|prosody:\w+\|>"),
             self._make_format(Palette.WARNING)),
            (QRegularExpression(r"<\|sfx:\w+\|>"),
             self._make_format("#a855f7")),
        ]

    def _make_format(self, color):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        fmt.setFontWeight(QFont.Weight.Bold)
        return fmt

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self._formats:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


class BlockAwarePlainTextEdit(CodeEditor):
    """A CodeEditor that also renders Prompt Block decorations.

    When block visualization is enabled, a left gutter is reserved where
    each block's header (number, emotion, style, override badge) is painted.
    A colored left border marks each block's vertical extent in the text
    area.  The selected block is highlighted, and newly detected blocks
    can be flashed via :meth:`flash_blocks`.

    The rendering layer stays synchronized with the NarrationBlockManager
    model via :meth:`set_block_data` -- no backend redesign is performed
    here.  Only rendering, interaction and user feedback.

    The CodeEditor base class already provides:
        - Line numbers (LineNumberArea)
        - Higgs token syntax highlighting (HiggsTokenHighlighter)
        - Bracket matching
        - Monospace font + no wrap
    The block gutter is painted to the RIGHT of the line-number area so
    both decorations coexist.

    P3.26: ``block_click_requested`` is emitted on every primary click that
    lands inside a block (gutter header OR text area). Selection can NOT
    rely on ``cursorPositionChanged`` alone: a click that does not MOVE
    the text cursor (e.g. the cursor already sits at the block's start
    offset after a Scene load or auto-detect) produces no cursor signal,
    which previously made the first block impossible to select with the
    first click.
    """

    GUTTER_WIDTH = 172

    # P3.26: offset of the block the user clicked (gutter or text area).
    block_click_requested = Signal(int)

    def __init__(self, parent=None):
        # IMPORTANT: set instance state BEFORE calling super().__init__()
        self._blocks: list = []
        self._selected_block_id: Optional[str] = None
        self._show_blocks: bool = True
        self._global_defaults: dict = {}
        self._flash_ids: set = set()
        self._flash_alpha: float = 0.0
        self._flash_timer = QTimer()
        self._flash_timer.setInterval(30)
        self._flash_timer.timeout.connect(self._on_flash_tick)
        super().__init__(parent)
        # Create a separate widget for the block gutter (like LineNumberArea).
        # The gutter CANNOT be painted on the viewport because the viewport
        # only covers the text area (margins are OUTSIDE the viewport).
        self._block_gutter_widget = _BlockGutterWidget(self)
        self.setViewportMargins(0, 0, 0, 0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_block_data(self, blocks, selected_block_id,
                       global_defaults) -> None:
        self._blocks = list(blocks) if blocks else []
        self._selected_block_id = selected_block_id
        self._global_defaults = global_defaults or {}
        self._update_margins()
        self._block_gutter_widget.update()
        self.viewport().update()

    def set_show_blocks(self, show: bool) -> None:
        """Toggle the visual rendering of Prompt Blocks."""
        self._show_blocks = show
        self._update_margins()
        self._block_gutter_widget.setVisible(show and bool(self._blocks))
        self._block_gutter_widget.update()
        self.viewport().update()

    def flash_blocks(self, block_ids) -> None:
        """Briefly highlight the given block ids (after auto-detect)."""
        self._flash_ids = set(block_ids) if block_ids else set()
        self._flash_alpha = 1.0
        self._flash_timer.start()
        self._block_gutter_widget.update()
        self.viewport().update()

    def update_block_status(self, block_id: str, status: str,
                            progress: float = 0.0,
                            duration: float = 0.0) -> None:
        """Update the generation status of a block.

        Called by MainWindow during generation to update the block gutter
        with status badges (Done/Generating/Pending/Error) and progress bars.

        Args:
            block_id: The block's unique id.
            status: One of "pending", "generating", "done", "error".
            progress: 0.0 - 1.0 (only meaningful for "generating").
            duration: seconds of generated audio (only for "done").
        """
        for block in self._blocks:
            if block.id == block_id:
                block.status = status
                block.progress = progress
                block.duration = duration
                break
        self._block_gutter_widget.update()

    def reset_all_block_statuses(self) -> None:
        """Clear all block statuses (e.g. when loading a new project)."""
        for block in self._blocks:
            block.status = None
            block.progress = 0.0
            block.duration = 0.0
        self._block_gutter_widget.update()

    def get_all_blocks_done(self) -> bool:
        """Check if all blocks have status "done".

        Returns False if there are no blocks or any block is not done.
        Used by MainWindow to determine if the inspector should be read-only.
        """
        if not self._blocks:
            return False
        return all(getattr(b, "status", None) == "done" for b in self._blocks)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _update_margins(self) -> None:
        """Reserve space for line numbers (always) + block gutter (when
        blocks are visible)."""
        ln_width = self.line_number_area_width()
        if self._show_blocks and self._blocks:
            gutter_width = self.GUTTER_WIDTH
        else:
            gutter_width = 0
        self.setViewportMargins(ln_width + gutter_width, 0, 0, 0)

    def _update_line_number_area_width(self) -> None:
        """Override CodeEditor's slot to also recompute the block gutter."""
        self._update_margins()

    def _on_flash_tick(self) -> None:
        self._flash_alpha = max(0.0, self._flash_alpha - 0.035)
        if self._flash_alpha <= 0:
            self._flash_ids.clear()
            self._flash_timer.stop()
        self._block_gutter_widget.update()
        self.viewport().update()

    # ------------------------------------------------------------------
    # Override CodeEditor._highlight_current_line to MERGE block selections
    # instead of WIPING them.
    # ------------------------------------------------------------------
    def _highlight_current_line(self) -> None:
        """Highlight current line + preserve block background selections.

        CodeEditor._highlight_current_line calls setExtraSelections([line_sel])
        which WIPES OUT the block backgrounds set by _render_block_visuals.
        We merge them instead.
        """
        from PySide6.QtWidgets import QTextEdit
        selections: List = []

        # 1. Current-line highlight
        line_sel = QTextEdit.ExtraSelection()
        line_color = QColor(Palette.ACCENT)
        line_color.setAlpha(20)
        line_sel.format.setBackground(line_color)
        line_sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        line_sel.cursor = self.textCursor()
        line_sel.cursor.clearSelection()
        selections.append(line_sel)

        # 2. Block background selections (if blocks are visible)
        if self._blocks and self._show_blocks:
            text = self.toPlainText()
            color_a = QColor("#252840")
            color_a.setAlpha(45)
            color_b = QColor("#222538")
            color_b.setAlpha(45)
            color_selected = QColor(Palette.ACCENT)
            color_selected.setAlpha(35)
            color_override = QColor(Palette.WARNING)
            color_override.setAlpha(22)
            for i, block in enumerate(self._blocks):
                start = max(0, min(block.start_offset, len(text)))
                end = max(start, min(block.end_offset, len(text)))
                if start >= end:
                    continue
                sel = QTextEdit.ExtraSelection()
                sel.cursor = QTextCursor(self.document())
                sel.cursor.setPosition(start)
                sel.cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                is_selected = (block.id == self._selected_block_id)
                has_overrides = block.has_overrides()
                if is_selected:
                    sel.format.setBackground(color_selected)
                elif has_overrides:
                    sel.format.setBackground(color_override)
                else:
                    sel.format.setBackground(color_a if i % 2 == 0 else color_b)
                selections.append(sel)

        self.setExtraSelections(selections)
        # Bracket matching
        self._match_brackets()

    # ------------------------------------------------------------------
    # Geometry overrides
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        """P3.26: drive block selection EXPLICITLY on every left click.

        The base class (QPlainTextEdit via CodeEditor) processes the click
        and moves the text cursor. When the click does not MOVE the cursor
        (it already sat at the resulting position — e.g. offset 0 after a
        Scene load), Qt emits NO ``cursorPositionChanged``, and the
        outer NarrationEditor's cursor-driven selection never ran. This
        made the first visible block unselectable on the first click.
        Emitting ``block_click_requested`` with the (possibly unchanged)
        cursor position makes text clicks ALWAYS resolve the block under
        the cursor.
        """
        super().mousePressEvent(event)
        if (self._show_blocks and self._blocks
                and event.button() == Qt.MouseButton.LeftButton):
            self.block_click_requested.emit(self.textCursor().position())

    def resizeEvent(self, event) -> None:
        """Position LineNumberArea at x=0 and BlockGutterWidget to its right."""
        QPlainTextEdit.resizeEvent(self, event)
        cr = self.contentsRect()
        ln_width = self.line_number_area_width()
        # LineNumberArea at x=0
        self._line_number_area.setGeometry(
            QRect(0, cr.top(), ln_width, cr.height()))
        # BlockGutterWidget to the right of LineNumberArea
        gutter_visible = self._show_blocks and bool(self._blocks)
        if gutter_visible:
            self._block_gutter_widget.setVisible(True)
            self._block_gutter_widget.setGeometry(
                QRect(ln_width, cr.top(), self.GUTTER_WIDTH, cr.height()))
        else:
            self._block_gutter_widget.setVisible(False)

    # ------------------------------------------------------------------
    # NOTE: paintEvent and _paint_block_decorations are REMOVED.
    # Block gutter painting is now handled by _BlockGutterWidget (a
    # separate child widget positioned in the margin area).
    # mousePressEvent for gutter clicks is also handled by
    # _BlockGutterWidget.mousePressEvent.
    # ------------------------------------------------------------------

    # _paint_block_decorations and mousePressEvent are REMOVED —
    # the _BlockGutterWidget handles all painting and click handling.


class NarrationEditor(QWidget):
    """The main prompt editor with three modes."""

    text_changed = Signal(str)
    blocks_changed = Signal()
    block_selected = Signal(str)  # block_id ("" when deselected)
    generate_long_requested = Signal()
    # New signal: emitted when the mode changes to update Generate button states
    generate_capability_changed = Signal(bool, bool)  # (can_generate, can_generate_long)

    MODE_PLAIN = 0
    MODE_BLOCKS = 1
    MODE_PREVIEW = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._block_manager = NarrationBlockManager()
        self._optimizer = PromptOptimizer()
        self._builder = PromptBuilder()
        self._mode = self.MODE_PLAIN
        # P3.26: while a Scene's text+blocks are being loaded (see
        # begin_scene_load/end_scene_load), text-change signals must NOT be
        # fed into the block manager — the manager still holds the
        # PREVIOUS Scene's blocks and would offset-shift them as if the
        # user had edited the text (corrupting live state and painting
        # stale visuals before the restore finishes).
        self._loading_scene: bool = False
        self._global_emotion: Optional[str] = None
        self._global_style: Optional[str] = None
        self._global_speed: str = "Normal"
        self._global_pitch: str = "Normal"
        self._global_delivery: str = "Normal"
        self._selected_block_id: Optional[str] = None
        self._show_blocks: bool = True
        # Raw-mode state: when True, the editor shows raw Higgs tokens
        # without block decorations and bypasses the block-aware prompt
        # builder (the user is editing the literal prompt).
        self._raw_mode: bool = False
        # P3.22 Character System: list of Character dicts {id, name} for the
        # block properties Character selector. Populated by MainWindow via
        # set_characters() when the active project changes.
        self._characters: list = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Mode toggle bar
        mode_bar = QHBoxLayout()
        mode_bar.setContentsMargins(8, 8, 8, 8)
        mode_bar.setSpacing(16)

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)

        # Mode tabs use Typography.body_lg() (16px) + DemiBold for prominence.
        # Previously they used the default QRadioButton font (small, thin).
        self._plain_btn = QRadioButton("Plain Text")
        self._plain_btn.setChecked(True)
        self._plain_btn.setFont(Typography.body_lg())
        self._plain_btn.setStyleSheet(
            f"QRadioButton {{ color: {Palette.TEXT_PRIMARY}; font-weight: 600; spacing: 6px; }}"
            f"QRadioButton::indicator {{ width: 16px; height: 16px; }}"
        )
        self._plain_btn.toggled.connect(
            lambda checked: checked and self._set_mode(self.MODE_PLAIN))
        self._mode_group.addButton(self._plain_btn, self.MODE_PLAIN)
        mode_bar.addWidget(self._plain_btn)

        self._blocks_btn = QRadioButton("Narration Blocks")
        self._blocks_btn.setFont(Typography.body_lg())
        self._blocks_btn.setStyleSheet(
            f"QRadioButton {{ color: {Palette.TEXT_PRIMARY}; font-weight: 600; spacing: 6px; }}"
            f"QRadioButton::indicator {{ width: 16px; height: 16px; }}"
        )
        self._blocks_btn.toggled.connect(
            lambda checked: checked and self._set_mode(self.MODE_BLOCKS))
        self._mode_group.addButton(self._blocks_btn, self.MODE_BLOCKS)
        mode_bar.addWidget(self._blocks_btn)

        self._preview_btn = QRadioButton("Preview")
        self._preview_btn.setFont(Typography.body_lg())
        self._preview_btn.setStyleSheet(
            f"QRadioButton {{ color: {Palette.TEXT_PRIMARY}; font-weight: 600; spacing: 6px; }}"
            f"QRadioButton::indicator {{ width: 16px; height: 16px; }}"
        )
        self._preview_btn.toggled.connect(
            lambda checked: checked and self._set_mode(self.MODE_PREVIEW))
        self._mode_group.addButton(self._preview_btn, self.MODE_PREVIEW)
        mode_bar.addWidget(self._preview_btn)

        mode_bar.addStretch()

        # NOTE: The "Read Tokens" button and its signal have been REMOVED.
        # The current UX is Managed Mode (SpeechStudio owns prompt state)
        # vs Raw Mode (user owns prompt state). There is no "import inline
        # tokens into semantic state" workflow. Users who want literal
        # Higgs tokens must use Raw Mode. See ADR 003 §1 and §8.

        # Raw-mode checkbox: when checked, the user is editing the literal
        # Higgs prompt (no block detection, no PromptBuilder involvement).
        self._raw_mode_cb = QCheckBox("Raw Mode")
        self._raw_mode_cb.setToolTip(
            "Edit the literal Higgs prompt (no block detection).\n"
            "Use this for advanced users who want full control over tokens.\n\n"
            "Effect on Generate Long:\n"
            "  When Raw Mode is ON, the Long Narration splitter will NOT\n"
            "  detect emotion/style blocks — each part is split purely by\n"
            "  sentence boundaries, and the literal text (including any\n"
            "  manual tokens you wrote) is sent to the model as-is.\n"
            "  When Raw Mode is OFF, the splitter detects blocks and\n"
            "  applies global emotion/style/speed/pitch/delivery tokens\n"
            "  automatically to each part.")
        self._raw_mode_cb.toggled.connect(self._on_raw_mode_toggled)
        mode_bar.addWidget(self._raw_mode_cb)

        # Search button (toggles the SearchReplaceBar)
        self._search_btn = QToolButton()
        self._search_btn.setText("\U0001f50d")
        self._search_btn.setToolTip("Show search/replace bar (Ctrl+F)")
        self._search_btn.setCheckable(True)
        self._search_btn.clicked.connect(self._on_toggle_search)
        mode_bar.addWidget(self._search_btn)

        self._analyze_btn = QPushButton("Re-detect")
        self._analyze_btn.setVisible(False)
        self._analyze_btn.clicked.connect(self._on_analyze_again)
        mode_bar.addWidget(self._analyze_btn)

        # Generate Long Narration button — orange gradient CTA
        self._generate_long_btn = QPushButton("▶ Generate Long")
        self._generate_long_btn.setToolTip(
            "Split text into safe parts and generate all at once.\n"
            "Each part gets 0.5s silence at the end.")
        self._generate_long_btn.setStyleSheet(
            "QPushButton {{"
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            "    stop:0 #F97316, stop:1 #EA580C);"
            "  color: #FFFFFF;"
            "  border: none;"
            "  border-radius: 8px;"
            "  padding: 8px 16px;"
            "  font-weight: bold;"
            "}}"
            "QPushButton:hover {{"
            "  opacity: 0.9;"
            "}}"
            "QPushButton:pressed {{"
            "  opacity: 0.8;"
            "}}"
            "QPushButton:disabled {{"
            "  background-color: {bg_surface};"
            "  color: {text_disabled};"
            "  border: 1px solid {border};"
            "}}".format(
                bg_surface=Palette.BG_SURFACE,
                text_disabled=Palette.TEXT_DISABLED,
                border=Palette.BORDER))
        self._generate_long_btn.clicked.connect(
            self.generate_long_requested.emit)
        mode_bar.addWidget(self._generate_long_btn)

        layout.addLayout(mode_bar)

        # P3.20: Block action toolbar REMOVED — was duplicate of the
        # properties panel buttons + context menu. All functions remain
        # accessible via:
        #   - Properties panel: Split at Cursor, Merge with Above, Delete Block, Lock
        #   - Context menu (right-click): Create Block, Split, Merge, Delete, Re-detect
        #   - Mode bar: Re-detect button

        # Editor
        self._editor = BlockAwarePlainTextEdit()
        # P3.26: clicks (gutter header or text area) drive block selection
        # explicitly — see BlockAwarePlainTextEdit.block_click_requested.
        self._editor.block_click_requested.connect(self._on_block_click_requested)
        self._editor.setFont(QFont("Consolas", 11))
        self._editor.setPlaceholderText(
            "Enter your prompt text here...\n\n"
            "Switch to 'Narration Blocks' for per-section emotion control.\n"
            "Switch to 'Preview' to see the exact prompt sent to the model.")
        self._editor.textChanged.connect(self._on_text_changed)
        self._editor.cursorPositionChanged.connect(self._on_cursor_moved)
        self._editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._editor.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._editor, 1)

        # Search/replace bar (initially hidden)
        self._search_bar = SearchReplaceBar(self)
        self._search_bar.find_next_requested.connect(self._on_find_next)
        self._search_bar.find_prev_requested.connect(self._on_find_prev)
        self._search_bar.replace_requested.connect(self._on_replace)
        self._search_bar.replace_all_requested.connect(self._on_replace_all)
        self._search_bar.closed.connect(lambda: self._search_btn.setChecked(False))
        self._search_bar.setVisible(False)
        layout.addWidget(self._search_bar)

        # Preview widget
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        # Issue #33: use the central Typography.mono_data() token
        # (JetBrains Mono 12px) instead of a hardcoded QFont("Consolas", 11).
        self._preview.setFont(Typography.mono_data())
        self._preview.setVisible(False)
        self._preview_highlighter = PreviewHighlighter(self._preview.document())
        layout.addWidget(self._preview, 1)

        # Statistics bar
        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(
            "color: {0}; padding: 4px 8px; background-color: {1};".format(
                Palette.TEXT_SECONDARY, Palette.BG_SURFACE))
        self._stats_label.setVisible(False)
        layout.addWidget(self._stats_label)

        # Properties panel
        self._properties_panel = self._build_properties_panel()
        self._properties_panel.setVisible(False)
        layout.addWidget(self._properties_panel)

    def _build_properties_panel(self) -> QWidget:
        from PySide6.QtWidgets import QFrame, QFormLayout
        from engine.higgs_tokens import (
            EMOTIONS, STYLES, SPEED_OPTIONS, PITCH_OPTIONS, DELIVERY_OPTIONS,
        )

        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setStyleSheet(
            "QFrame {{ background-color: {0}; border-top: 1px solid {1}; }}".format(
                Palette.BG_SURFACE, Palette.BORDER))
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 4)
        # Tighter row spacing so the panel doesn't dominate the editor.
        layout.setSpacing(6)

        self._block_title = QLabel("No block selected")
        self._block_title.setStyleSheet(
            "font-weight: bold; color: {0};".format(Palette.TEXT_PRIMARY))
        layout.addWidget(self._block_title)

        # P3.23 (design record §12): CHARACTER sits at the TOP of the Block
        # Properties panel, above the semantic overrides — it is a normal
        # block level setting and the first thing the user sees.
        #
        # Unlike the override rows below (which use a checkbox to enable an
        # override), the Character selector is a simple dropdown where the
        # first item "None (inherit scene voice)" means no character is
        # assigned — the block uses the Scene Voice or Control Panel dropdown.
        char_row = QHBoxLayout()
        char_row.setSpacing(6)
        char_dot = QLabel("")
        char_dot.setFixedSize(14, 14)
        char_dot.setStyleSheet(
            "background-color: {0}; border-radius: 7px;".format(Palette.TEXT_DISABLED))
        char_row.addWidget(char_dot)
        self._char_dot = char_dot

        char_label = QLabel("Character:")
        char_label.setMinimumWidth(90)
        char_label.setStyleSheet("color: {0};".format(Palette.TEXT_PRIMARY))
        char_row.addWidget(char_label)

        self._character_combo = QComboBox()
        self._character_combo.addItem("None (inherit scene voice)", None)
        self._character_combo.setMinimumWidth(180)
        self._character_combo.setSizePolicy(QSizePolicy.Policy.Expanding,
                                            QSizePolicy.Policy.Fixed)
        self._character_combo.currentIndexChanged.connect(self._on_character_changed)
        char_row.addWidget(self._character_combo, 1)

        self._character_status = QLabel("Inherited")
        self._character_status.setMinimumWidth(120)
        self._character_status.setStyleSheet(
            "color: {0}; font-size: 11px; padding: 0 4px;".format(
                Palette.TEXT_SECONDARY))
        char_row.addWidget(self._character_status)
        layout.addLayout(char_row)

        # P3.23 (design record §25): lost-Character warning row. Shown only
        # when the selected block carries a lost_character_id (Re-detect
        # could not reliably preserve the assignment). Offers re-assignment
        # via the Character combo above.
        self._lost_warning = QLabel("")
        self._lost_warning.setStyleSheet(
            "color: {0}; font-size: 11px; font-weight: bold; padding: 0 4px;".format(
                Palette.WARNING))
        self._lost_warning.setWordWrap(True)
        self._lost_warning.hide()
        layout.addWidget(self._lost_warning)

        # Override checkboxes + dropdowns
        for prop_name, label_text, options in [
            ("emotion", "Emotion Override", [(e.name, e.name) for e in EMOTIONS]),
            ("style", "Style Override", [(s.name, s.name) for s in STYLES]),
            ("speed", "Speed Override",
             [(p.name, p.name) for p in SPEED_OPTIONS]),
            ("pitch", "Pitch Override",
             [(p.name, p.name) for p in PITCH_OPTIONS]),
            ("delivery", "Delivery Override",
             [(p.name, p.name) for p in DELIVERY_OPTIONS]),
        ]:
            row = QHBoxLayout()
            row.setSpacing(6)
            cb = QCheckBox(label_text)
            # Give the checkbox a sensible minimum width so its label is
            # fully visible across DPI/font sizes.
            cb.setMinimumWidth(140)
            cb.toggled.connect(
                lambda v, p=prop_name: self._on_override_toggled(p, v))
            row.addWidget(cb)
            combo = QComboBox()
            for display, data in options:
                combo.addItem(display, data)
            combo.setEnabled(False)
            # Ensure the combo can shrink / grow predictably.
            combo.setMinimumWidth(120)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Fixed)
            combo.currentIndexChanged.connect(
                lambda _, p=prop_name: self._on_override_changed(p))
            row.addWidget(combo, 1)
            # Status label: clearly indicates Inherited vs Overridden
            status = QLabel("Inherited")
            status.setMinimumWidth(120)
            status.setStyleSheet(
                "color: {0}; font-size: 11px; padding: 0 4px;".format(
                    Palette.TEXT_SECONDARY))
            row.addWidget(status)
            layout.addLayout(row)
            setattr(self, f"_{prop_name}_override", cb)
            setattr(self, f"_{prop_name}_combo", combo)
            setattr(self, f"_{prop_name}_status", status)

        # (The Character selector row now lives at the TOP of this panel —
        # design record §12. See the char_row block above.)

        # Label + Lock
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)
        bottom_row.addWidget(QLabel("Label:"))
        self._label_combo = QComboBox()
        self._label_combo.setEditable(True)
        self._label_combo.addItem("")
        self._label_combo.addItems([
            "Introduction", "Description", "Personal Opinion", "Pros", "Cons",
            "Comparison", "Story", "Surprise", "Dramatic Reveal",
            "Conclusion", "Question", "Transition", "Call to Action",
        ])
        self._label_combo.setMinimumWidth(140)
        self._label_combo.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Fixed)
        self._label_combo.currentTextChanged.connect(self._on_label_changed)
        bottom_row.addWidget(self._label_combo, 1)

        self._lock_btn = QPushButton("Lock")
        self._lock_btn.setCheckable(True)
        self._lock_btn.setToolTip("Lock this block to prevent modification by Re-detect")
        self._lock_btn.toggled.connect(self._on_lock_toggled)
        bottom_row.addWidget(self._lock_btn)
        layout.addLayout(bottom_row)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._split_btn = QPushButton("Split at Cursor")
        self._split_btn.clicked.connect(self._on_split)
        btn_row.addWidget(self._split_btn)
        self._merge_btn = QPushButton("Merge with Above")
        self._merge_btn.clicked.connect(self._on_merge)
        btn_row.addWidget(self._merge_btn)
        self._delete_btn = QPushButton("Delete Block")
        self._delete_btn.clicked.connect(self._on_delete_block)
        btn_row.addWidget(self._delete_btn)
        layout.addLayout(btn_row)

        # (Character row moved to the TOP of the panel — design record §12.)

        # Debug token display (Requirement 2): shows the effective HIGGS
        # tokens for the selected block. Derived from the same canonical
        # state used by Preview and Generate.
        self._debug_tokens_label = QLabel("")
        self._debug_tokens_label.setWordWrap(True)
        self._debug_tokens_label.setStyleSheet(
            "QLabel {{ background-color: {0}; color: {1}; "
            "border: 1px solid {2}; border-radius: 4px; "
            "padding: 6px; font-family: 'JetBrains Mono', 'Consolas', monospace; "
            "font-size: 11px; }}".format(
                Palette.BG_SURFACE_ALT, Palette.TEXT_PRIMARY, Palette.BORDER))
        layout.addWidget(self._debug_tokens_label)

        return panel

    def _set_mode(self, mode: int) -> None:
        prev_mode = self._mode
        self._mode = mode
        # Emit the mode-driven capability. The central resolver
        # (get_generate_capability) is the SINGLE source of truth.
        can_generate, can_generate_long = self.get_generate_capability()
        self.generate_capability_changed.emit(can_generate, can_generate_long)

        # P3.26 (Scene state contract / stale block scope): leaving
        # Narration Blocks mode ends the block selection. The Right Panel's
        # block scope must not silently survive a mode switch — in Plain
        # Text / Preview mode the properties panel is hidden, so a
        # surviving scope would route control changes into hidden block
        # overrides (the "global emotion does nothing" defect).
        if prev_mode == self.MODE_BLOCKS and mode != self.MODE_BLOCKS \
                and self._selected_block_id:
            self._selected_block_id = None
            self._update_properties_panel()
            self.block_selected.emit("")

        if mode == self.MODE_PLAIN:
            self._editor.setVisible(True)
            self._preview.setVisible(False)
            self._properties_panel.setVisible(False)
            self._stats_label.setVisible(False)
            self._analyze_btn.setVisible(False)
            self._editor.setExtraSelections([])
            self._editor.set_block_data([], None, {})
        elif mode == self.MODE_BLOCKS:
            if not self._block_manager.has_blocks:
                self._block_manager.auto_detect(self._editor.toPlainText())
                self._post_analyze()
            self._editor.setVisible(True)
            self._preview.setVisible(False)
            self._properties_panel.setVisible(True)
            self._stats_label.setVisible(False)
            self._analyze_btn.setVisible(True)
            self._update_properties_panel()
            self._render_block_visuals()
        elif mode == self.MODE_PREVIEW:
            self._editor.setExtraSelections([])
            self._editor.setVisible(False)
            self._preview.setVisible(True)
            self._properties_panel.setVisible(False)
            self._stats_label.setVisible(True)
            self._analyze_btn.setVisible(False)
            self._update_preview()

    def _on_text_changed(self) -> None:
        text = self._editor.toPlainText()
        # P3.26: during a Scene load the text swap is NOT a user edit —
        # skip block-offset tracking (the previous Scene's blocks must
        # not be shifted) and skip intermediate re-renders (the restore
        # renders the final state atomically at the end).
        if not self._loading_scene:
            self._block_manager.on_text_changed(text)
        self.text_changed.emit(text)
        if self._loading_scene:
            return
        if self._mode == self.MODE_PREVIEW:
            self._update_preview()
        elif self._mode == self.MODE_BLOCKS:
            self._render_block_visuals()

    def _on_cursor_moved(self) -> None:
        if self._mode != self.MODE_BLOCKS:
            return
        pos = self._editor.textCursor().position()
        block = self._block_manager.get_block_at_offset(pos)
        if block and block.id != self._selected_block_id:
            self._selected_block_id = block.id
            self._update_properties_panel()
            self._render_block_visuals()
            self.block_selected.emit(block.id)
        elif not block and self._selected_block_id:
            self._selected_block_id = None
            self._update_properties_panel()
            self._render_block_visuals()
            self.block_selected.emit("")

    def _on_context_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        cursor = self._editor.textCursor()
        selected_text = cursor.selectedText()
        click_pos = self._editor.mapToGlobal(pos)
        click_cursor = self._editor.cursorForPosition(pos)
        click_offset = click_cursor.position()

        menu = QMenu(self._editor)
        if self._mode == self.MODE_BLOCKS:
            if selected_text and len(selected_text.strip()) > 0:
                menu.addAction("Create Block from Selection",
                               self._create_block_from_selection)
            block = self._block_manager.get_block_at_offset(click_offset)
            if block:
                menu.addAction("Split Block at Cursor",
                               lambda: self._do_split_at(click_offset))
                idx = self._block_manager.blocks.index(block) if block in self._block_manager.blocks else -1
                if idx > 0:
                    menu.addAction("Merge with Block Above",
                                   lambda: self._do_merge(block.id))
                menu.addAction("Delete Block (keep text)",
                               lambda: self._do_delete(block.id))
            menu.addSeparator()
            menu.addAction("Re-detect Blocks", self._on_analyze_again)
        else:
            menu.addAction("Switch to Narration Blocks Mode",
                           lambda: self._blocks_btn.setChecked(True))
        if menu.actions():
            menu.exec(click_pos)

    def _create_block_from_selection(self) -> None:
        cursor = self._editor.textCursor()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        if start >= end:
            return
        new_blocks = []
        for b in self._block_manager.blocks:
            if b.end_offset <= start or b.start_offset >= end:
                new_blocks.append(b)
            else:
                if b.start_offset < start:
                    b.end_offset = start
                    new_blocks.append(b)
                if b.end_offset > end:
                    b.start_offset = end
                    new_blocks.append(b)
        new_block = PromptBlock(start_offset=start, end_offset=end, manually_edited=True)
        new_blocks.append(new_block)
        new_blocks.sort(key=lambda b: b.start_offset)
        self._block_manager._blocks = new_blocks
        self._selected_block_id = new_block.id
        self.blocks_changed.emit()
        self._update_properties_panel()
        self._render_block_visuals()
        # P3.26: selection changed — keep the MainWindow scope in sync.
        self.block_selected.emit(new_block.id)

    def _do_split_at(self, offset: int) -> None:
        block = self._block_manager.get_block_at_offset(offset)
        if block:
            self._block_manager.split_block(block.id, offset)
            self.blocks_changed.emit()
            self._update_properties_panel()
            self._render_block_visuals()

    def _do_merge(self, block_id: str) -> None:
        if self._block_manager.merge_with_above(block_id):
            self._selected_block_id = None
            self.blocks_changed.emit()
            self._update_properties_panel()
            self._render_block_visuals()
            # P3.26: selection ended — clear the MainWindow block scope.
            self.block_selected.emit("")

    def _do_delete(self, block_id: str) -> None:
        block = self._block_manager.get_block(block_id)
        if block and block.locked:
            reply = QMessageBox.question(
                self, "Delete Locked Block",
                "This block is locked. Delete it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        if self._block_manager.delete_block(block_id):
            self._selected_block_id = None
            self.blocks_changed.emit()
            self._update_properties_panel()
            self._render_block_visuals()
            # P3.26: the selected block no longer exists — clear the
            # MainWindow block scope (previously the stale id silently
            # swallowed control changes: the "global emotion does
            # nothing" defect).
            self.block_selected.emit("")

    def _render_block_visuals(self) -> None:
        # P3.30(a): the editor's VISUAL block layer (gutter + selections)
        # is fed ONLY in Narration Blocks mode. In Plain Text / Preview the
        # block data is CLEARED — a scene restore used to re-populate the
        # editor's blocks while the mode stayed Plain (the mode radio is a
        # no-op when already checked), so the block gutter painted blocks
        # in Plain Text mode.
        # The block MODEL always lives in self._block_manager — only the
        # visual mirror on the editor is gated.
        if self._mode == self.MODE_BLOCKS:
            global_defaults = {
                "emotion": self._global_emotion,
                "style": self._global_style,
                "speed": self._global_speed,
                "pitch": self._global_pitch,
                "delivery": self._global_delivery,
            }
            self._editor.set_block_data(
                self._block_manager.blocks,
                self._selected_block_id,
                global_defaults)
            self._editor.set_show_blocks(self._show_blocks)

            # _highlight_current_line merges current-line highlight + block
            # backgrounds into a single setExtraSelections call.  Calling it
            # here ensures the block backgrounds are refreshed when block data
            # changes, without wiping the current-line highlight.
            if self._show_blocks:
                self._editor._highlight_current_line()
            else:
                self._editor.setExtraSelections([])
        else:
            # Plain Text / Preview: NO block visuals — clear the editor's
            # block mirror entirely (hides the gutter, resets margins).
            self._editor.set_block_data([], None, {})
            self._editor.setExtraSelections([])

    def _update_properties_panel(self) -> None:
        if not self._selected_block_id:
            self._block_title.setText("No block selected")
            return
        block = self._block_manager.get_block(self._selected_block_id)
        if block is None:
            self._selected_block_id = None
            self._block_title.setText("No block selected")
            return
        text = self._editor.toPlainText()
        block_text = text[block.start_offset:block.end_offset].strip()[:40]
        label_text = block.label or "Block"
        self._block_title.setText('{0}: "{1}..."'.format(label_text, block_text))

        for prop in ["emotion", "style", "speed", "pitch", "delivery"]:
            cb = getattr(self, f"_{prop}_override")
            combo = getattr(self, f"_{prop}_combo")
            status = getattr(self, f"_{prop}_status")
            cb.blockSignals(True)
            combo.blockSignals(True)
            val = getattr(block, prop)
            cb.setChecked(val is not None)
            if val:
                idx = combo.findData(val)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.setEnabled(val is not None)
            # Update status label: Inherited (with global value) or Overridden
            if val is not None:
                status.setText("Overridden")
                status.setStyleSheet(
                    "color: {0}; font-size: 11px; font-weight: bold; "
                    "padding: 0 4px;".format(Palette.ACCENT))
            else:
                global_val = getattr(self, f"_global_{prop}", None)
                if global_val:
                    status.setText("Inherited: {0}".format(global_val))
                else:
                    status.setText("Inherited")
                status.setStyleSheet(
                    "color: {0}; font-size: 11px; padding: 0 4px;".format(
                        Palette.TEXT_SECONDARY))
            cb.blockSignals(False)
            combo.blockSignals(False)

        self._label_combo.blockSignals(True)
        self._label_combo.setEditText(block.label or "")
        self._label_combo.blockSignals(False)

        # P3.22 Character System: update the Character combo + colored dot.
        self._character_combo.blockSignals(True)
        idx = self._character_combo.findData(block.character_id)
        if idx >= 0:
            self._character_combo.setCurrentIndex(idx)
        else:
            self._character_combo.setCurrentIndex(0)  # None
        self._character_combo.blockSignals(False)
        # Update the colored dot + status label
        if block.character_id:
            from engine.character_colors import get_character_color
            # P3.23 (design record §10): a missing/deleted Character must
            # NOT receive a real Character color — paint the neutral dot
            # unless the ID resolves to a known Character.
            known_ids = {c.get("id") for c in self._characters}
            if block.character_id in known_ids:
                cc = get_character_color(block.character_id)
                self._char_dot.setStyleSheet(
                    "background-color: {0}; border-radius: 7px;".format(cc.hex_main))
            else:
                self._char_dot.setStyleSheet(
                    "background-color: {0}; border-radius: 7px;".format(
                        Palette.TEXT_DISABLED))
            # Find the character name
            char_name = next(
                (c["name"] for c in self._characters if c.get("id") == block.character_id),
                "Unknown")
            self._character_status.setText("Character: {0}".format(char_name))
            self._character_status.setStyleSheet(
                "color: {0}; font-size: 11px; font-weight: bold; padding: 0 4px;".format(
                    Palette.ACCENT))
        else:
            self._char_dot.setStyleSheet(
                "background-color: {0}; border-radius: 7px;".format(Palette.TEXT_DISABLED))
            self._character_status.setText("Inherited (scene voice)")
            self._character_status.setStyleSheet(
                "color: {0}; font-size: 11px; padding: 0 4px;".format(
                    Palette.TEXT_SECONDARY))
        # P3.23 (design record §25): lost-Character warning state.
        self._update_lost_warning(block)

        self._lock_btn.blockSignals(True)
        self._lock_btn.setChecked(block.locked)
        self._lock_btn.setText("Locked" if block.locked else "Lock")
        self._lock_btn.blockSignals(False)

        # Update debug token display (Requirement 2)
        self._update_debug_tokens(block)

    def _update_debug_tokens(self, block) -> None:
        """Show the effective HIGGS tokens for the selected block (Requirement 2).

        Derives the token display from the same canonical state used by
        Preview and Generate — no second prompt representation.
        """
        from engine.higgs_tokens import EMOTION_BY_NAME, STYLE_BY_NAME, PROSODY_BY_NAME

        tokens = []
        # Resolve effective values: Block override > Global default
        eff_emotion = block.emotion if block.emotion is not None else self._global_emotion
        eff_style = block.style if block.style is not None else self._global_style
        eff_speed = block.speed if block.speed is not None else self._global_speed
        eff_pitch = block.pitch if block.pitch is not None else self._global_pitch
        eff_delivery = block.delivery if block.delivery is not None else self._global_delivery

        # Build token strings
        if eff_emotion and eff_emotion != "Normal":
            emo = EMOTION_BY_NAME.get(eff_emotion)
            if emo:
                tokens.append(f"<|emotion:{emo.tag}|>")
        if eff_style:
            st = STYLE_BY_NAME.get(eff_style)
            if st:
                tokens.append(f"<|style:{st.tag}|>")
        if eff_speed and eff_speed != "Normal":
            spd = PROSODY_BY_NAME.get(eff_speed)
            if spd and spd.category == "speed":
                tokens.append(f"<|prosody:{spd.tag}|>")
        if eff_pitch and eff_pitch != "Normal":
            ptc = PROSODY_BY_NAME.get(eff_pitch)
            if ptc and ptc.category == "pitch":
                tokens.append(f"<|prosody:{ptc.tag}|>")
        if eff_delivery and eff_delivery != "Normal":
            dlv = PROSODY_BY_NAME.get(eff_delivery)
            if dlv and dlv.category == "delivery":
                tokens.append(f"<|prosody:{dlv.tag}|>")

        # Show source annotations
        sources = []
        for prop, val, block_val in [
            ("emotion", eff_emotion, block.emotion),
            ("style", eff_style, block.style),
            ("speed", eff_speed, block.speed),
            ("pitch", eff_pitch, block.pitch),
            ("delivery", eff_delivery, block.delivery),
        ]:
            if val and val != "Normal":
                src = "Block" if block_val is not None else "Global"
                sources.append(f"{prop}={val} ({src})")

        token_str = "".join(tokens) if tokens else "(no tokens)"
        source_str = " | ".join(sources) if sources else "(all inherited/normal)"
        self._debug_tokens_label.setText(
            f"TOKENS: {token_str}\nSOURCES: {source_str}")

    def _on_override_toggled(self, prop: str, checked: bool) -> None:
        if not self._selected_block_id:
            return
        combo = getattr(self, f"_{prop}_combo")
        status = getattr(self, f"_{prop}_status")
        if checked:
            # State 2: Override Enabled -- store the selected value
            combo.setEnabled(True)
            value = combo.currentData()
            status.setText("Overridden")
            status.setStyleSheet(
                "color: {0}; font-size: 11px; font-weight: bold; "
                "padding: 0 4px;".format(Palette.ACCENT))
        else:
            # State 3: Override Removed -- clear to None (inherited)
            # The underlying PromptBlock attribute becomes None, so no
            # prompt token is generated for this property.
            combo.setEnabled(False)
            value = None
            global_val = getattr(self, f"_global_{prop}", None)
            if global_val:
                status.setText("Inherited: {0}".format(global_val))
            else:
                status.setText("Inherited")
            status.setStyleSheet(
                "color: {0}; font-size: 11px; padding: 0 4px;".format(
                    Palette.TEXT_SECONDARY))
        # Set the override (None clears it completely -> inherited state)
        self._block_manager.set_override(self._selected_block_id, prop, value)
        # Prompt preview updates immediately via blocks_changed signal,
        # which triggers MainWindow._update_prompt_preview.
        self.blocks_changed.emit()
        self._render_block_visuals()

    def _on_override_changed(self, prop: str) -> None:
        if not self._selected_block_id:
            return
        combo = getattr(self, f"_{prop}_combo")
        if combo.isEnabled():
            self._block_manager.set_override(
                self._selected_block_id, prop, combo.currentData())
            self.blocks_changed.emit()
            self._render_block_visuals()

    def _on_label_changed(self, text: str) -> None:
        if not self._selected_block_id:
            return
        self._block_manager.set_label(
            self._selected_block_id, text if text else None)
        self.blocks_changed.emit()

    def _on_lock_toggled(self, checked: bool) -> None:
        if not self._selected_block_id:
            return
        self._block_manager.toggle_lock(self._selected_block_id)
        self._lock_btn.setText("Locked" if checked else "Lock")
        self.blocks_changed.emit()

    def _on_character_changed(self) -> None:
        """P3.22: Handle Character selector change in the block properties panel.

        Sets block.character_id on the selected block. None = inherit the
        scene/control-panel voice.

        P3.23 (design record §25): any explicit selection (including None)
        resolves an outstanding lost_character_id warning — the user has
        made a decision.

        P3.44.7 — an explicit Character selection (including clearing it)
        is MANUAL SEMANTIC STATE, exactly like an override combo change
        (set_override marks the block the same way): it sets
        ``manually_edited`` so ``has_manual_edits`` recognises it and
        Re-detect can never take the direct-rebuild branch and silently
        destroy the assignment. The combo's signals are blocked during
        programmatic refreshes (_update_properties_panel / set_characters),
        so this only fires on real user interaction.
        """
        if not self._selected_block_id:
            return
        char_id = self._character_combo.currentData()
        block = self._block_manager.get_block(self._selected_block_id)
        if block is not None:
            block.character_id = char_id
            # P3.44.7: a Character assignment alone is user-authored
            # semantic state — mark the block as manually edited.
            block.manually_edited = True
            # Explicit user decision → clear the lost warning.
            if getattr(block, "lost_character_id", None):
                block.lost_character_id = None
        # Update the colored dot + status label
        if char_id:
            from engine.character_colors import get_character_color
            cc = get_character_color(char_id)
            self._char_dot.setStyleSheet(
                "background-color: {0}; border-radius: 7px;".format(cc.hex_main))
            char_name = next(
                (c["name"] for c in self._characters if c.get("id") == char_id),
                "Unknown")
            self._character_status.setText("Character: {0}".format(char_name))
            self._character_status.setStyleSheet(
                "color: {0}; font-size: 11px; font-weight: bold; padding: 0 4px;".format(
                    Palette.ACCENT))
        else:
            self._char_dot.setStyleSheet(
                "background-color: {0}; border-radius: 7px;".format(Palette.TEXT_DISABLED))
            self._character_status.setText("Inherited (scene voice)")
            self._character_status.setStyleSheet(
                "color: {0}; font-size: 11px; padding: 0 4px;".format(
                    Palette.TEXT_SECONDARY))
        self._update_lost_warning(block)
        self.blocks_changed.emit()
        self._render_block_visuals()

    def _update_lost_warning(self, block) -> None:
        """P3.23 (design record §25): show/hide the lost-Character warning.

        When a block carries lost_character_id (Re-detect could not reliably
        preserve the assignment, or the Character was deleted), display
        "⚠ Character was 'X' — re-assign below" so the user can re-assign,
        choose a different Character, or dismiss by picking None.
        """
        if not hasattr(self, "_lost_warning"):
            return
        lost_id = getattr(block, "lost_character_id", None) if block else None
        if lost_id:
            lost_name = next(
                (c["name"] for c in self._characters if c.get("id") == lost_id),
                None)
            if lost_name:
                self._lost_warning.setText(
                    "⚠ Character was '{0}' — re-assign above, or pick "
                    "None to dismiss.".format(lost_name))
            else:
                # The Character no longer exists in the Project (deleted).
                self._lost_warning.setText(
                    "⚠ Character was deleted — re-assign above, or pick "
                    "None to dismiss.")
            self._lost_warning.show()
        else:
            self._lost_warning.hide()

    def set_characters(self, characters: list) -> None:
        """P3.22: Populate the block properties Character selector.

        Args:
            characters: list of dicts with 'id' and 'name' keys.
        """
        self._characters = list(characters) if characters else []
        # P3.23: mirror the character list onto the inner editor so the
        # block gutter (which holds a reference to the INNER editor) can
        # resolve Character names + valid IDs without reaching into the
        # outer NarrationEditor.
        try:
            self._editor._gutter_characters = list(self._characters)
        except Exception:
            pass
        # Rebuild the combo (preserving the current selection if possible)
        current_id = None
        if hasattr(self, '_character_combo'):
            current_id = self._character_combo.currentData()
        self._character_combo.blockSignals(True)
        self._character_combo.clear()
        self._character_combo.addItem("None (inherit scene voice)", None)
        for c in self._characters:
            self._character_combo.addItem(c.get("name", "?"), c.get("id"))
        # Restore selection
        idx = self._character_combo.findData(current_id)
        if idx >= 0:
            self._character_combo.setCurrentIndex(idx)
        self._character_combo.blockSignals(False)

    def refresh_blocks(self) -> None:
        """P3.23: Repaint the block visuals + properties panel.

        Called by MainWindow after external block-state changes (e.g. the
        stale-Character cleanup demoting orphaned character_ids to
        lost_character_id after a Character delete) so the gutter and
        properties panel reflect the corrected state immediately.
        """
        try:
            self._update_properties_panel()
        except Exception:
            pass
        try:
            self._render_block_visuals()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # P3.26: Scene state contract — atomic Scene restore API
    # ------------------------------------------------------------------
    def begin_scene_load(self) -> None:
        """P3.26: suspend block-offset tracking + intermediate renders
        while a Scene's text and blocks are being loaded.

        Without this, ``set_text`` during a Scene switch feeds the text
        swap into ``NarrationBlockManager.on_text_changed`` while the
        manager still holds the PREVIOUS Scene's blocks — they get
        offset-shifted as if the user had edited the text, and stale
        visuals are painted before the restore finishes.
        """
        self._loading_scene = True

    def end_scene_load(self) -> None:
        """P3.26: resume normal text-change tracking after the Scene
        restore completed."""
        self._loading_scene = False

    def set_scene_blocks(self, blocks: list, text: str) -> None:
        """P3.26: replace the WHOLE block model for a Scene restore.

        Full lifecycle reset (Scene state contract):
          - the previous Scene's block objects are discarded (their
            offsets were never shifted — see begin_scene_load)
          - the selected block is cleared (the previous Scene's selection
            must never leak)
          - the properties panel + ALL editor visuals are re-rendered,
            INCLUDING the zero-blocks case (previously the visuals kept
            showing the previous Scene's blocks when the target Scene
            had none)
        """
        self._block_manager._blocks = list(blocks)
        self._block_manager._last_text = text
        self._selected_block_id = None
        self.blocks_changed.emit()
        self._update_properties_panel()
        self._render_block_visuals()
        # P3.26: the previous Scene's selection is gone — tell MainWindow
        # so its control-panel block scope is cleared deterministically
        # (not via cursor-signal side effects).
        self.block_selected.emit("")

    @property
    def editor_mode_name(self) -> str:
        """P3.26: the current editor mode as a persistable Scene value."""
        if self._mode == self.MODE_BLOCKS:
            return "blocks"
        if self._mode == self.MODE_PREVIEW:
            return "preview"
        return "plain"

    def set_editor_mode(self, mode: str) -> None:
        """P3.26: restore the per-Scene editor mode.

        Drives the REAL _set_mode path so all mode-driven UI (properties
        panel, Re-detect button, generate capability) follows. Unknown
        values deterministically restore to Plain Text.

        P3.30(a): _set_mode is called DIRECTLY — driving only the radio
        buttons is a NO-OP when the target radio is already checked (no
        toggled signal is emitted), which left stale block visuals behind
        after a scene switch into the same mode. _set_mode is idempotent,
        so calling it and then syncing the radios is always correct.
        """
        if mode == "blocks":
            self._set_mode(self.MODE_BLOCKS)
            if not self._blocks_btn.isChecked():
                self._blocks_btn.setChecked(True)
        elif mode == "preview":
            self._set_mode(self.MODE_PREVIEW)
            if not self._preview_btn.isChecked():
                self._preview_btn.setChecked(True)
        else:
            self._set_mode(self.MODE_PLAIN)
            if not self._plain_btn.isChecked():
                self._plain_btn.setChecked(True)

    def _on_block_click_requested(self, offset: int) -> None:
        """P3.26: explicit block selection from a user click.

        Called for gutter clicks AND text-area clicks. Unlike the
        cursor-driven path, this does not depend on the text cursor
        MOVING: clicking the first block while the cursor already sits
        at its start offset (the common state right after a Scene load
        or auto-detect) still selects it — the "B1 first click" defect.

        Idempotent: re-clicking the already-selected block refreshes the
        properties panel so every click produces visible feedback.
        """
        if self._mode != self.MODE_BLOCKS or self._raw_mode:
            return
        block = self._block_manager.get_block_at_offset(offset)
        if block is None:
            return
        changed = block.id != self._selected_block_id
        self._selected_block_id = block.id
        self._update_properties_panel()
        self._render_block_visuals()
        if changed:
            self.block_selected.emit(block.id)

    def _on_split(self) -> None:
        if not self._selected_block_id:
            return
        cursor_pos = self._editor.textCursor().position()
        if self._block_manager.split_block(self._selected_block_id, cursor_pos):
            self.blocks_changed.emit()
            self._update_properties_panel()
            self._render_block_visuals()

    def _on_merge(self) -> None:
        if not self._selected_block_id:
            return
        if self._block_manager.merge_with_above(self._selected_block_id):
            self._selected_block_id = None
            self.blocks_changed.emit()
            self._update_properties_panel()
            self._render_block_visuals()
            # P3.26: selection ended — clear the MainWindow block scope.
            self.block_selected.emit("")

    def _on_add_block_from_selection(self) -> None:
        """P3.15: Add a new Narration Block from the current text selection.

        If there's a selection, the block covers the selected text range.
        If no selection, the block covers the current line.
        Reuses the BlockManager's block creation logic.
        """
        cursor = self._editor.textCursor()
        if cursor.hasSelection():
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
        else:
            # No selection — use the current line
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
        if start >= end:
            return
        # Create a new block covering the selection
        from engine.narration_blocks import PromptBlock
        new_block = PromptBlock(start_offset=start, end_offset=end,
                                 manually_edited=True)
        self._block_manager._blocks.append(new_block)
        # P3.15: Sort blocks by start_offset to maintain order
        self._block_manager._blocks.sort(key=lambda b: b.start_offset)
        self._block_manager._last_text = self._editor.toPlainText()
        self._selected_block_id = new_block.id
        self.blocks_changed.emit()
        self._update_properties_panel()
        self._render_block_visuals()
        # P3.26: selection changed — keep the MainWindow scope in sync.
        self.block_selected.emit(new_block.id)
        logger.info("P3.15: Block added from selection (start=%d, end=%d)", start, end)

    def _on_split_block(self) -> None:
        """P3.15: Split the selected Block at the cursor position.

        Preserves text and semantic state — the two resulting Blocks
        inherit the original Block's overrides.
        """
        if not self._selected_block_id:
            return
        cursor = self._editor.textCursor()
        split_offset = cursor.position()
        if self._block_manager.split_block(self._selected_block_id, split_offset):
            self.blocks_changed.emit()
            self._update_properties_panel()
            self._render_block_visuals()
            logger.info("P3.15: Block split at offset %d", split_offset)

    def _on_merge_blocks(self) -> None:
        """P3.15: Merge the selected Block with the next Block.

        Preserves text (the text is already contiguous in the editor).
        The merged Block inherits the first Block's overrides.
        """
        if not self._selected_block_id:
            return
        # Find the next block and merge the selected block with it
        blocks = self._block_manager.blocks
        for i, b in enumerate(blocks):
            if b.id == self._selected_block_id:
                if i + 1 < len(blocks):
                    # Merge next block INTO current (extend end_offset)
                    next_block = blocks[i + 1]
                    b.end_offset = next_block.end_offset
                    self._block_manager._blocks.remove(next_block)
                    self._block_manager._last_text = self._editor.toPlainText()
                    self.blocks_changed.emit()
                    self._update_properties_panel()
                    self._render_block_visuals()
                    logger.info("P3.15: Blocks merged (block %d + %d)", i + 1, i + 2)
                break

    def _block_action_btn_style(self) -> str:
        """P3.15: Consistent styling for Block action buttons."""
        return (
            "QToolButton {{"
            "  background-color: {bg_surface};"
            "  color: {text_secondary};"
            "  border: 1px solid {border};"
            "  border-radius: 4px;"
            "  padding: 4px 10px;"
            "  font-size: 11px;"
            "}}"
            "QToolButton:hover {{"
            "  background-color: {bg_raised};"
            "  color: {text_primary};"
            "  border-color: {accent};"
            "}}"
            "QToolButton:pressed {{"
            "  background-color: {bg_surface_alt};"
            "}}"
            "QToolButton:disabled {{"
            "  color: {text_disabled};"
            "  border-color: {border};"
            "}}".format(
                bg_surface=Palette.BG_SURFACE,
                bg_surface_alt=Palette.BG_SURFACE_ALT,
                bg_raised=Palette.BG_RAISED,
                text_primary=Palette.TEXT_PRIMARY,
                text_secondary=Palette.TEXT_SECONDARY,
                text_disabled=Palette.TEXT_DISABLED,
                border=Palette.BORDER,
                accent=Palette.ACCENT,
            )
        )

    def _on_delete_block(self) -> None:
        if not self._selected_block_id:
            return
        block = self._block_manager.get_block(self._selected_block_id)
        if block and block.locked:
            reply = QMessageBox.question(
                self, "Delete Locked Block",
                "This block is locked. Delete it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        if self._block_manager.delete_block(self._selected_block_id):
            self._selected_block_id = None
            self.blocks_changed.emit()
            self._update_properties_panel()
            self._render_block_visuals()
            # P3.26: the selected block no longer exists — clear the
            # MainWindow block scope (stale-scope defect).
            self.block_selected.emit("")

    def _on_analyze_again(self) -> None:
        text = self._editor.toPlainText()
        has_manual = self._block_manager.has_manual_edits
        has_locked = self._block_manager.has_locked_blocks
        if not has_manual and not has_locked:
            self._block_manager.rebuild_everything(text)
            self._post_analyze()
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Re-detect Narration Blocks")
        dialog.setText("Current: {0} blocks ({1} locked, {2} with overrides)".format(
            len(self._block_manager.blocks),
            sum(1 for b in self._block_manager.blocks if b.locked),
            sum(1 for b in self._block_manager.blocks if b.has_overrides()),
        ))
        if has_locked:
            dialog.setInformativeText(
                "• Apply = Rebuild Automatic Only (preserve locked)\n"
                "• Save = Preserve Overrides\n"
                "• Discard = Rebuild Everything\n"
                "• Cancel")
            dialog.setStandardButtons(
                QMessageBox.StandardButton.Apply |
                QMessageBox.StandardButton.Save |
                QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel)
        else:
            dialog.setInformativeText(
                "• Save = Preserve Overrides\n"
                "• Discard = Rebuild Everything\n"
                "• Cancel")
            dialog.setStandardButtons(
                QMessageBox.StandardButton.Save |
                QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel)
        result = dialog.exec()
        if result == QMessageBox.StandardButton.Apply:
            self._block_manager.rebuild_automatic_only(text)
        elif result == QMessageBox.StandardButton.Save:
            self._block_manager.preserve_overrides(text)
        elif result == QMessageBox.StandardButton.Discard:
            reply = QMessageBox.question(
                self, "Rebuild Everything",
                "This will discard ALL block overrides, Characters, "
                "SFX/pause insertions, labels, and locks.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self._block_manager.rebuild_everything(text)
            else:
                return
        else:
            return
        self._post_analyze()

    def _post_analyze(self) -> None:
        blocks = self._block_manager.blocks
        if blocks:
            self._selected_block_id = blocks[0].id
        else:
            self._selected_block_id = None
        self.blocks_changed.emit()
        self._update_properties_panel()
        self._render_block_visuals()
        # P3.26 (stale block scope): Re-detect replaces EVERY block id.
        # The MainWindow/control-panel block scope must follow the NEW
        # selection, otherwise the old id becomes a phantom scope and
        # control changes are silently routed into (or dropped by)
        # _update_block_override — the "global emotion does nothing"
        # defect.
        self.block_selected.emit(self._selected_block_id or "")
        if blocks:
            self._block_title.setText(
                "{0} blocks detected - click any block to edit.".format(len(blocks)))
            # Briefly highlight newly detected blocks so the user
            # immediately understands what was detected.
            self._editor.flash_blocks([b.id for b in blocks])
        else:
            self._block_title.setText(
                "No blocks detected - right-click text to create manually.")

    def _update_preview(self) -> None:
        text = self._editor.toPlainText()
        # P3.25 (audit SS-H05): RAW MODE — the user owns the prompt. The
        # Preview tab must show EXACTLY what the model receives: the
        # literal editor text, verbatim. No automatic Emotion/Style/
        # Prosody/SFX/pause tokens may be injected into the preview when
        # Raw Mode is active (ADR 003 §6/§8: preview == model input).
        if self._raw_mode:
            final_prompt = text
        else:
            blocks = self._block_manager.blocks
            if blocks:
                states = self._optimizer.resolve_and_optimize(
                    blocks, text,
                    global_emotion=self._global_emotion,
                    global_style=self._global_style,
                    global_speed=self._global_speed,
                    global_pitch=self._global_pitch,
                    global_delivery=self._global_delivery,
                )
                emissions = self._optimizer.optimize_emissions(states)
                final_prompt = self._builder.build_from_emissions(states, emissions)
            else:
                try:
                    prompt_data = self._builder.build(
                        text, emotion=self._global_emotion, style=self._global_style,
                        speed=self._global_speed, pitch=self._global_pitch,
                        delivery=self._global_delivery)
                    final_prompt = prompt_data.final_prompt
                except Exception:
                    final_prompt = text
        self._preview.setPlainText(final_prompt)
        import re
        token_count = len(re.findall(r"<\|\w+:\w+\|>", final_prompt))
        prompt_len = len(final_prompt)
        est_duration = prompt_len / 15.0
        block_count = len(self._block_manager.blocks) if self._block_manager.blocks else 1
        self._stats_label.setText(
            "Prompt length: {0} chars  |  Tokens: {1}  |  Blocks: {2}  |  "
            "Est. duration: ~{3:.0f}s".format(
                prompt_len, token_count, block_count, est_duration))

    # Public API
    def get_text(self) -> str:
        return self._editor.toPlainText()

    def set_text(self, text: str) -> None:
        self._editor.setPlainText(text)
        self._block_manager.on_text_changed(text)

    def insert_at_cursor(self, text: str) -> None:
        cursor = self._editor.textCursor()
        cursor.insertText(text)
        self._editor.setTextCursor(cursor)

    def clear_text(self) -> None:
        self._editor.clear()
        self._block_manager.clear()

    def set_global_defaults(self, emotion=None, style=None, speed="Normal",
                            pitch="Normal", delivery="Normal") -> None:
        self._global_emotion = emotion
        self._global_style = style
        self._global_speed = speed
        self._global_pitch = pitch
        self._global_delivery = delivery
        self._update_properties_panel()
        # CRITICAL (issue #37): when the user is in Preview mode and changes
        # a Friendly control (emotion/style/prosody), the Preview widget
        # must refresh immediately so the resolved prompt — including all
        # tokens — is visible without a tab switch or text edit.
        # set_global_defaults is called by MainWindow._update_prompt_preview
        # on every Friendly control change, so this is the single reliable
        # hook that catches every path (Friendly tab, Advanced tab, preset
        # apply, scene load).
        if self._mode == self.MODE_PREVIEW:
            self._update_preview()

    def get_final_prompt(self) -> str:
        text = self._editor.toPlainText()
        blocks = self._block_manager.blocks
        if blocks:
            states = self._optimizer.resolve_and_optimize(
                blocks, text,
                global_emotion=self._global_emotion,
                global_style=self._global_style,
                global_speed=self._global_speed,
                global_pitch=self._global_pitch,
                global_delivery=self._global_delivery,
            )
            emissions = self._optimizer.optimize_emissions(states)
            return self._builder.build_from_emissions(states, emissions)
        else:
            try:
                prompt_data = self._builder.build(
                    text, emotion=self._global_emotion, style=self._global_style,
                    speed=self._global_speed, pitch=self._global_pitch,
                    delivery=self._global_delivery)
                return prompt_data.final_prompt
            except Exception:
                return text

    @property
    def block_manager(self) -> NarrationBlockManager:
        return self._block_manager

    def set_show_blocks(self, show: bool) -> None:
        """Toggle visual rendering of Prompt Blocks (View menu)."""
        self._show_blocks = show
        self._render_block_visuals()

    def update_block_status(self, block_id: str, status: str,
                            progress: float = 0.0,
                            duration: float = 0.0) -> None:
        """Update the generation status of a block (delegates to editor)."""
        self._editor.update_block_status(block_id, status, progress, duration)

    def reset_all_block_statuses(self) -> None:
        """Clear all block statuses."""
        self._editor.reset_all_block_statuses()

    def get_all_blocks_done(self) -> bool:
        """Check if all blocks are done."""
        return self._editor.get_all_blocks_done()

    def hasFocus(self) -> bool:
        return self._editor.hasFocus() or super().hasFocus()

    # ------------------------------------------------------------------
    # Raw mode + search/replace
    # ------------------------------------------------------------------
    def is_raw_mode(self) -> bool:
        """Return True if the editor is in raw Higgs-token editing mode.

        When raw mode is on, the MainWindow bypasses the block-aware
        prompt builder and submits ``self._editor.toPlainText()`` directly
        to the engine.
        """
        return self._raw_mode

    def get_generate_capability(self) -> tuple:
        """Return the mode-driven Generate capability (can_generate, can_generate_long).

        This is the SINGLE central capability resolver. It derives the
        capability from the CURRENT EDITOR MODE only — not from batch
        lifecycle state.

        Capability matrix (Requirement 3 / ADR 003):

            Plain Text:      Generate = ON,  Generate Long = ON
            Narration Blocks: Generate = OFF, Generate Long = ON
            Preview:         Generate = OFF, Generate Long = OFF
            Raw Mode:        Generate = ON,  Generate Long = ON

        Batch lifecycle events (start, progress, completion, failure,
        cancellation) must NEVER modify these values directly. They
        may temporarily disable Generate DURING generation (via
        ``set_generate_enabled(False)``), but on completion/failure/
        cancellation they must call ``refresh_generate_capability()``
        which re-derives the capability from the current mode — NOT
        blindly call ``set_generate_enabled(True)``.
        """
        if self._raw_mode:
            return (True, True)
        if self._mode == self.MODE_PLAIN:
            return (True, True)
        if self._mode == self.MODE_BLOCKS:
            return (False, True)
        # MODE_PREVIEW
        return (False, False)

    def refresh_generate_capability(self) -> None:
        """Re-emit the mode-driven Generate capability.

        Call this after batch completion/failure/cancellation to restore
        the Generate button to its correct mode-driven state. This
        prevents the bug where batch completion blindly calls
        ``set_generate_enabled(True)`` even in Narration Blocks mode
        (where Generate should remain OFF).
        """
        can_generate, can_generate_long = self.get_generate_capability()
        self.generate_capability_changed.emit(can_generate, can_generate_long)

    def _on_raw_mode_toggled(self, checked: bool) -> None:
        self._raw_mode = checked
        # Re-emit mode-driven Generate capability via the central resolver.
        self.refresh_generate_capability()
        # In raw mode we hide the block gutter and disable block detection
        # so the user sees the literal prompt text.
        if checked:
            # Show warning if managed token state exists
            from PySide6.QtWidgets import QMessageBox
            has_managed_state = (
                self._block_manager.has_blocks or
                any(emo is not None for emo in [
                    getattr(self, '_global_emotion', None),
                    getattr(self, '_global_style', None),
                ])
            )
            if has_managed_state:
                reply = QMessageBox.warning(
                    self, "Raw Mode",
                    "Raw Mode disables automatic token handling. "
                    "Narration Block settings and Right Panel token settings "
                    "will no longer be applied. Tokens written directly in "
                    "the editor are treated as literal prompt content.\n\n"
                    "Do you want to continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    self._raw_mode_cb.setChecked(False)
                    return
            self._editor.set_show_blocks(False)
            # Block-manager state is preserved; we just stop rendering and
            # stop considering blocks for prompt building.
        else:
            self._editor.set_show_blocks(self._show_blocks)
        # Re-emit text_changed so the prompt preview refreshes.
        self._on_text_changed()

    def _on_toggle_search(self) -> None:
        if self._search_bar.isVisible():
            self._search_bar.hide()
            self._search_btn.setChecked(False)
        else:
            self._search_bar.show()
            self._search_btn.setChecked(True)

    # --- Search/replace operations on the underlying editor ---
    def _find_in_document(self, text: str, case_sensitive: bool,
                          forward: bool = True):
        """Return the QTextCursor of the next match, or None."""
        if not text:
            return None
        from PySide6.QtGui import QTextDocument
        flags = QTextDocument.FindFlag(0)
        if not case_sensitive:
            flags |= QTextDocument.FindFlag.FindCaseSensitively  # invert below
        # NB: QPlainTextEdit.find() takes FindFlags; case-insensitive is the
        # default.  Set FindCaseSensitively only when the user asks for it.
        find_flags = QTextDocument.FindFlag(0)
        if case_sensitive:
            find_flags |= QTextDocument.FindFlag.FindCaseSensitively
        if not forward:
            find_flags |= QTextDocument.FindFlag.FindBackward

        # Save the cursor; QPlainTextEdit.find() moves it.
        cursor = self._editor.textCursor()
        found = self._editor.find(text, find_flags)
        result_cursor = self._editor.textCursor() if found else None
        if not found:
            # Wrap around
            if forward:
                cursor.movePosition(QTextCursor.MoveOperation.Start)
            else:
                cursor.movePosition(QTextCursor.MoveOperation.End)
            self._editor.setTextCursor(cursor)
            found = self._editor.find(text, find_flags)
            result_cursor = self._editor.textCursor() if found else None
            if not found:
                # No match anywhere; restore the original cursor.
                self._editor.setTextCursor(cursor)
        return result_cursor

    def _on_find_next(self, text: str, case_sensitive: bool) -> None:
        cursor = self._find_in_document(text, case_sensitive, forward=True)
        if cursor is None:
            self._search_bar.set_status("No matches.")
        else:
            self._search_bar.set_status("")

    def _on_find_prev(self, text: str, case_sensitive: bool) -> None:
        cursor = self._find_in_document(text, case_sensitive, forward=False)
        if cursor is None:
            self._search_bar.set_status("No matches.")
        else:
            self._search_bar.set_status("")

    def _on_replace(self, text: str, new_text: str,
                    case_sensitive: bool) -> None:
        """Replace the current match (if any) and advance to the next.

        UX:
        - If the cursor has an active selection that matches the search
          text, replace it with new_text and advance to the next match.
        - If the cursor has NO active match (or the selection doesn't
          match), find the next occurrence and replace THAT one.
        - The user should not need to manually select the exact search
          result before pressing Replace.

        This preserves the Narration Block offset fix because the
        replacement is a single contiguous edit (one match at a time),
        not a multi-replace operation. The block manager's
        ``on_text_changed()`` handles single-edit offset adjustment
        correctly.
        """
        cursor = self._editor.textCursor()
        replaced = False

        if cursor.hasSelection():
            selected = cursor.selectedText()
            # Qt uses \u2029 for paragraph separators in selectedText.
            selected = selected.replace("\u2029", "\n")
            match_text = text
            target_text = selected
            if not case_sensitive:
                match_text = match_text.lower()
                target_text = target_text.lower()
            if match_text and target_text == match_text:
                cursor.insertText(new_text)
                self._search_bar.set_status("Replaced 1 occurrence.")
                replaced = True

        if not replaced:
            # No active match — find the next occurrence and replace it.
            next_cursor = self._find_in_document(text, case_sensitive, forward=True)
            if next_cursor is not None and next_cursor.hasSelection():
                # Verify the found selection actually matches (it should,
                # but be defensive).
                selected = next_cursor.selectedText().replace("\u2029", "\n")
                match_text = text
                target_text = selected
                if not case_sensitive:
                    match_text = match_text.lower()
                    target_text = target_text.lower()
                if match_text and target_text == match_text:
                    next_cursor.insertText(new_text)
                    self._search_bar.set_status("Replaced 1 occurrence.")
                    replaced = True

        if not replaced:
            self._search_bar.set_status("No matches found.")
            return

        # Advance to the next match after replace.
        self._on_find_next(text, case_sensitive)

    def _on_replace_all(self, text: str, new_text: str,
                        case_sensitive: bool) -> None:
        if not text:
            return
        content = self._editor.toPlainText()

        # Compute all match positions in the OLD text BEFORE replacement.
        # Each match is a (start, end) tuple of character offsets.
        # This is essential so NarrationBlockManager.on_multi_replace can
        # adjust block offsets correctly for each independent replacement.
        if case_sensitive:
            search_content = content
            search_text = text
        else:
            search_content = content.lower()
            search_text = text.lower()

        matches = []
        search_idx = 0
        text_len = len(search_text)
        while True:
            pos = search_content.find(search_text, search_idx)
            if pos == -1:
                break
            matches.append((pos, pos + text_len))
            search_idx = pos + text_len

        count = len(matches)
        if count == 0:
            self._search_bar.set_status("No matches found.")
            return

        # Build the new content from the matches (preserves case-insensitivity).
        rebuilt_parts = []
        last_end = 0
        for m_start, m_end in matches:
            rebuilt_parts.append(content[last_end:m_start])
            rebuilt_parts.append(new_text)
            last_end = m_end
        rebuilt_parts.append(content[last_end:])
        new_content = "".join(rebuilt_parts)

        # Adjust block offsets for each independent replacement BEFORE
        # calling setPlainText. This is the fix for the offset corruption
        # bug: on_text_changed() assumes a single contiguous edit, but
        # Replace All produces multiple disjoint edits. on_multi_replace()
        # handles each match independently and updates _last_text so the
        # textChanged signal from setPlainText becomes a no-op.
        self._block_manager.on_multi_replace(
            old_text=content,
            new_text=new_content,
            matches=matches,
            replacement=new_text,
        )

        self._editor.setPlainText(new_content)
        self._search_bar.set_status(
            "Replaced {0} occurrence(s).".format(count))

    # ------------------------------------------------------------------
    # Key handling for search shortcut
    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        from PySide6.QtGui import QShortcut
        # Ctrl+F toggles the search bar
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier \
                and event.key() == Qt.Key.Key_F:
            self._on_toggle_search()
            return
        # Escape closes the search bar if visible
        if event.key() == Qt.Key.Key_Escape and self._search_bar.isVisible():
            self._search_bar.hide()
            self._search_btn.setChecked(False)
            return
        super().keyPressEvent(event)
