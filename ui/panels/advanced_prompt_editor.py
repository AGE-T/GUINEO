"""
SpeechStudio Advanced Prompt Editor components.

Provides reusable building blocks used by the NarrationEditor:
    - HiggsTokenHighlighter : QSyntaxHighlighter for <|...|> tokens
    - LineNumberArea         : QWidget that paints line numbers
    - CodeEditor             : QPlainTextEdit with line numbers, bracket
                               matching, syntax highlighting, monospace font
    - SearchReplaceBar       : collapsible find/replace bar

Architecture note:
    There is no AdvancedPromptEditor QDockWidget class.  This functionality
    has been merged into the NarrationEditor (BlockAwarePlainTextEdit
    inherits from CodeEditor).  These building blocks are imported and
    composed there.

AI Development Rules, section 9:
    The UI must never concatenate Higgs token strings.  This module only
    HIGHLIGHTS tokens for display; it never constructs them.
"""

from __future__ import annotations
from typing import Optional, List

from PySide6.QtCore import Qt, QRect, QSize, QRegularExpression, Signal
from PySide6.QtGui import (
    QColor, QTextFormat, QPainter, QFont, QSyntaxHighlighter,
    QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QWidget, QPlainTextEdit, QHBoxLayout, QVBoxLayout, QPushButton,
    QLineEdit, QCheckBox, QLabel, QToolButton, QSizePolicy,
)

from ui.theme import Palette


# ---------------------------------------------------------------------------
# Syntax highlighter
# ---------------------------------------------------------------------------
class HiggsTokenHighlighter(QSyntaxHighlighter):
    """Highlights Higgs control tokens in a QTextDocument.

    Recognised token families (each gets its own colour):
        <|emotion:NAME|>    -> accent
        <|style:NAME|>      -> info
        <|prosody:NAME|>    -> warning
        <|sfx:NAME|>        -> purple
    """

    def __init__(self, document):
        super().__init__(document)
        self._formats: List = [
            (QRegularExpression(r"<\|emotion:\w+\|>"),
             self._make_format(Palette.ACCENT)),
            (QRegularExpression(r"<\|style:\w+\|>"),
             self._make_format(Palette.INFO)),
            (QRegularExpression(r"<\|prosody:\w+\|>"),
             self._make_format(Palette.WARNING)),
            (QRegularExpression(r"<\|sfx:\w+\|>"),
             self._make_format("#a855f7")),
        ]
        # Bracket-matching overlay (set by the CodeEditor on each cursor move)
        self._match_start: int = -1
        self._match_end: int = -1
        self._match_format = self._make_format(Palette.BORDER_FOCUS)
        self._match_format.setBackground(QColor(Palette.BORDER_FOCUS))
        self._match_format.setForeground(QColor(Palette.BG_BASE))

    @staticmethod
    def _make_format(color: str) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        fmt.setFontWeight(QFont.Weight.Bold)
        return fmt

    def set_match(self, start: int, end: int) -> None:
        """Set the bracket-match span to highlight on every rehighlight."""
        self._match_start = start
        self._match_end = end
        self.rehighlight()

    def clear_match(self) -> None:
        self._match_start = -1
        self._match_end = -1

    def highlightBlock(self, text: str) -> None:
        # Token colouring
        for pattern, fmt in self._formats:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(),
                               match.capturedLength(), fmt)

        # Bracket matching (block-local positions are absolute within the
        # document, but highlightBlock receives a per-block string).
        block_pos = self.currentBlock().position()
        if self._match_start >= 0 and self._match_end > self._match_start:
            local_start = self._match_start - block_pos
            local_end = self._match_end - block_pos
            if 0 <= local_start < len(text) and 0 <= local_end <= len(text):
                self.setFormat(local_start,
                               max(1, local_end - local_start),
                               self._match_format)


# ---------------------------------------------------------------------------
# Line number area
# ---------------------------------------------------------------------------
class LineNumberArea(QWidget):
    """A widget that paints line numbers for its parent CodeEditor."""

    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor
        self.setAutoFillBackground(True)
        self._bg = QColor(Palette.BG_SURFACE)
        self._fg = QColor(Palette.TEXT_SECONDARY)

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(event.rect(), self._bg)
        painter.setPen(self._fg)
        font = QFont("Consolas", 10)
        painter.setFont(font)

        block = self._editor.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self._editor.blockBoundingGeometry(block)
                    .translated(self._editor.contentOffset()).top())
        bottom = top + round(self._editor.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                rect = QRect(0, top, self.width(),
                             self._editor.fontMetrics().height())
                painter.drawText(rect, Qt.Alignment.AlignRight |
                                 Qt.Alignment.AlignVCenter,
                                 " " + number + " ")
            block = block.next()
            top = bottom
            bottom = top + round(self._editor.blockBoundingRect(block).height())
            block_number += 1


# ---------------------------------------------------------------------------
# CodeEditor
# ---------------------------------------------------------------------------
class CodeEditor(QPlainTextEdit):
    """A QPlainTextEdit with line numbers, syntax highlighting,
    bracket matching, monospace font, and no wrap.

    Designed to be subclassed (BlockAwarePlainTextEdit inherits from this
    class so that block decorations and line numbers coexist).
    """

    BRACKETS = "()"  # only Higgs-token brackets are matched here

    def __init__(self, parent=None):
        super().__init__(parent)

        # --- Appearance ---
        self.setFont(QFont("Consolas", 11))
        # Use WidgetWidth line wrapping so long lines wrap to the viewport
        # width instead of extending horizontally (Issue 3: word wrapping).
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setMouseTracking(True)

        # --- Line number area ---
        self._line_number_area = LineNumberArea(self)
        self._line_number_area.setVisible(True)

        # --- Syntax highlighter ---
        self._highlighter = HiggsTokenHighlighter(self.document())

        # --- Signals ---
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)

        # Initial state
        self._update_line_number_area_width()
        self._highlight_current_line()

    # ------------------------------------------------------------------
    # Line number area geometry
    # ------------------------------------------------------------------
    def line_number_area_width(self) -> int:
        digits = 1
        max_num = max(1, self.blockCount())
        while max_num >= 10:
            max_num //= 10
            digits += 1
        # space + digits + space
        return 8 + self.fontMetrics().horizontalAdvance("9") * (digits + 2)

    def _update_line_number_area_width(self) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(
                0, rect.y(),
                self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        # Position the line number area at x=0 (the very left edge).
        self._line_number_area.setGeometry(
            QRect(cr.left(), cr.top(),
                  self.line_number_area_width(), cr.height()))

    # ------------------------------------------------------------------
    # Bracket matching + current-line highlight
    # ------------------------------------------------------------------
    def _highlight_current_line(self) -> None:
        # Current-line extra selection
        from PySide6.QtWidgets import QTextEdit
        selections: List = []
        line_sel = QTextEdit.ExtraSelection()
        line_color = QColor(Palette.ACCENT)
        line_color.setAlpha(20)
        line_sel.format.setBackground(line_color)
        line_sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        line_sel.cursor = self.textCursor()
        line_sel.cursor.clearSelection()
        selections.append(line_sel)
        self.setExtraSelections(selections)

        # Bracket matching
        self._match_brackets()

    def _match_brackets(self) -> None:
        cursor = self.textCursor()
        if not cursor.atStart() and not cursor.atEnd():
            # Try char to the left first, then to the right
            pass

        # Build a list of candidate positions to inspect
        positions = []
        if cursor.position() > 0:
            positions.append(cursor.position() - 1)
        positions.append(cursor.position())

        match_start, match_end = -1, -1
        for pos in positions:
            if pos < 0 or pos >= len(self.toPlainText()):
                continue
            cursor.setPosition(pos)
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter,
                                QTextCursor.MoveMode.KeepAnchor)
            ch = cursor.selectedText()
            if ch == "":
                continue
            if ch not in "()":
                # Handle the unicode PS replacement character Qt uses
                # for paragraph breaks; only ASCII brackets are matched.
                continue
            match = self._find_matching(pos, ch)
            if match is not None:
                match_start = pos
                match_end = match
                break

        if match_start >= 0 and match_end > match_start:
            self._highlighter.set_match(match_start, match_end + 1)
        else:
            self._highlighter.clear_match()

    def _find_matching(self, pos: int, ch: str) -> Optional[int]:
        """Find the matching bracket for the character at ``pos``."""
        text = self.toPlainText()
        if ch == "(":
            depth = 0
            for i in range(pos, len(text)):
                if text[i] == "(":
                    depth += 1
                elif text[i] == ")":
                    depth -= 1
                    if depth == 0:
                        return i
            return None
        elif ch == ")":
            depth = 0
            for i in range(pos, -1, -1):
                if text[i] == ")":
                    depth += 1
                elif text[i] == "(":
                    depth -= 1
                    if depth == 0:
                        return i
            return None
        return None

    # ------------------------------------------------------------------
    # Public accessors used by subclasses (e.g. NarrationEditor)
    # ------------------------------------------------------------------
    @property
    def line_number_area(self) -> LineNumberArea:
        return self._line_number_area

    @property
    def highlighter(self) -> HiggsTokenHighlighter:
        return self._highlighter


# ---------------------------------------------------------------------------
# Search/replace bar
# ---------------------------------------------------------------------------
class SearchReplaceBar(QWidget):
    """Collapsible find/replace bar with case-sensitivity toggle.

    Signals:
        find_next_requested(text, case_sensitive)
        find_prev_requested(text, case_sensitive)
        replace_requested(text, new_text, case_sensitive)
        replace_all_requested(text, new_text, case_sensitive)
        closed()
    """

    find_next_requested = Signal(str, bool)
    find_prev_requested = Signal(str, bool)
    replace_requested = Signal(str, str, bool)
    replace_all_requested = Signal(str, str, bool)
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.setVisible(False)
        self.setMaximumHeight(80)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        # Find row
        find_row = QHBoxLayout()
        find_row.setSpacing(4)
        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText("Find...")
        self._find_edit.returnPressed.connect(self._on_find_next)
        find_row.addWidget(self._find_edit, 1)

        self._case_cb = QCheckBox("Aa")
        self._case_cb.setToolTip("Case sensitive")
        find_row.addWidget(self._case_cb)

        self._prev_btn = QToolButton()
        self._prev_btn.setText("\u2191")
        self._prev_btn.setToolTip("Find previous (Shift+Enter)")
        self._prev_btn.clicked.connect(self._on_find_prev)
        find_row.addWidget(self._prev_btn)

        self._next_btn = QToolButton()
        self._next_btn.setText("\u2193")
        self._next_btn.setToolTip("Find next (Enter)")
        self._next_btn.clicked.connect(self._on_find_next)
        find_row.addWidget(self._next_btn)

        self._close_btn = QToolButton()
        self._close_btn.setText("\u00d7")
        self._close_btn.setToolTip("Close search bar")
        self._close_btn.clicked.connect(self.hide)
        find_row.addWidget(self._close_btn)

        layout.addLayout(find_row)

        # Replace row
        replace_row = QHBoxLayout()
        replace_row.setSpacing(4)
        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText("Replace with...")
        replace_row.addWidget(self._replace_edit, 1)

        self._replace_btn = QPushButton("Replace")
        self._replace_btn.clicked.connect(self._on_replace)
        replace_row.addWidget(self._replace_btn)

        self._replace_all_btn = QPushButton("Replace All")
        self._replace_all_btn.clicked.connect(self._on_replace_all)
        replace_row.addWidget(self._replace_all_btn)

        layout.addLayout(replace_row)

        # Status label
        self._status = QLabel("")
        self._status.setStyleSheet(
            "color: {0}; font-size: 11px;".format(Palette.TEXT_SECONDARY))
        layout.addWidget(self._status)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def show(self) -> None:  # type: ignore[override]
        super().show()
        self._find_edit.setFocus()
        self._find_edit.selectAll()

    def hide(self) -> None:  # type: ignore[override]
        super().hide()
        self.closed.emit()

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _on_find_next(self) -> None:
        self.find_next_requested.emit(
            self._find_edit.text(), self._case_cb.isChecked())

    def _on_find_prev(self) -> None:
        self.find_prev_requested.emit(
            self._find_edit.text(), self._case_cb.isChecked())

    def _on_replace(self) -> None:
        self.replace_requested.emit(
            self._find_edit.text(), self._replace_edit.text(),
            self._case_cb.isChecked())

    def _on_replace_all(self) -> None:
        self.replace_all_requested.emit(
            self._find_edit.text(), self._replace_edit.text(),
            self._case_cb.isChecked())
