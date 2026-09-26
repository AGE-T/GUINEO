"""
SpeechStudio Prompt Editor.

UI Specification, section 7:
    Large Prompt Editor.
    Features: Plain text editing, Undo, Redo, Copy, Paste, Drag and Drop,
              Line numbers optional, Syntax highlighting in Developer Mode.
    Supports large prompts.
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, Signal, QRect, QSize, QRegularExpression
from PySide6.QtGui import (
    QFont, QColor, QPainter, QTextFormat, QKeyEvent,
    QSyntaxHighlighter, QTextCharFormat,
)
from PySide6.QtWidgets import (
    QPlainTextEdit, QWidget, QScrollBar,
)

from ui.theme import Palette


class LineNumberArea(QWidget):
    """The gutter showing line numbers next to the text editor."""

    def __init__(self, editor: "PromptEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:
        self._editor.line_number_area_paint_event(event)


class PromptHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for inline markers and Higgs tokens.

    In normal mode: highlights inline markers ({sfx:...}, {pause}, {long_pause}).
    In developer mode: also highlights Higgs tokens (<|category:tag|>).
    """

    def __init__(self, document):
        super().__init__(document)

        # SFX marker format: {sfx:Laughter:Haha} -> highlighted in cyan
        self._sfx_format = QTextCharFormat()
        self._sfx_format.setForeground(QColor(Palette.INFO))
        self._sfx_format.setFontWeight(QFont.Weight.Bold)

        # Pause marker format: {pause}, {long_pause} -> highlighted in orange
        self._pause_format = QTextCharFormat()
        self._pause_format.setForeground(QColor(Palette.WARNING))
        self._pause_format.setFontWeight(QFont.Weight.Bold)

        # Higgs token format (developer mode): <|category:tag|> -> teal
        self._token_format = QTextCharFormat()
        self._token_format.setForeground(QColor(Palette.ACCENT))
        self._token_format.setFontWeight(QFont.Weight.Bold)

        self._patterns = [
            (QRegularExpression(r"\{sfx:[^:}]+:[^}]+\}"), self._sfx_format),
            (QRegularExpression(r"\{(?:long_)?pause\}"), self._pause_format),
            (QRegularExpression(r"<\|\w+:\w+\|>"), self._token_format),
        ]

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self._patterns:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


class PromptEditor(QPlainTextEdit):
    """Center text editor for entering the prompt text.

    Features:
    - Line numbers (toggleable)
    - Developer Mode syntax highlighting for Higgs tokens
    - Drag and drop text support
    - Large prompt support (no length limit)
    """

    text_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._line_numbers_visible = True
        self._developer_mode = False
        self._highlighter: Optional[PromptHighlighter] = None

        # Editor configuration
        self.setAcceptDrops(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setFont(QFont("Consolas", 11))
        self.setPlaceholderText(
            "Enter your prompt text here...\n\n"
            "Select emotion, style, prosody and sound effects from the "
            "right panel. The application will insert Higgs tokens "
            "automatically."
        )

        # Line number area
        self._line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self._update_line_number_area_width(0)

        # Always-on syntax highlighter for inline markers
        self._highlighter = PromptHighlighter(self.document())

        # Text change signal
        self.textChanged.connect(self._on_text_changed)

    # ------------------------------------------------------------------
    # Line number area
    # ------------------------------------------------------------------
    def line_number_area_width(self) -> int:
        if not self._line_numbers_visible:
            return 0
        digits = 1
        max_num = max(1, self.blockCount())
        while max_num >= 10:
            max_num //= 10
            digits += 1
        space = 3 + self.fontMetrics().horizontalAdvance("9") * digits + 8
        return space

    def _update_line_number_area_width(self, new_block_count: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(
                0, rect.y(), self._line_number_area.width(), rect.height()
            )
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(
            QRect(cr.left(), cr.top(),
                  self.line_number_area_width(), cr.height())
        )

    def line_number_area_paint_event(self, event) -> None:
        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), QColor(Palette.BG_SURFACE))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor(Palette.TEXT_SECONDARY))
                painter.drawText(
                    0, top,
                    self._line_number_area.width() - 4,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, number
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

    # ------------------------------------------------------------------
    # Developer mode
    # ------------------------------------------------------------------
    def set_developer_mode(self, enabled: bool) -> None:
        self._developer_mode = enabled
        # The highlighter is always active to show inline markers.
        # In developer mode it also shows Higgs tokens (which are already
        # highlighted by the same highlighter).
        if self._highlighter is None:
            self._highlighter = PromptHighlighter(self.document())

    def set_line_numbers_visible(self, visible: bool) -> None:
        self._line_numbers_visible = visible
        self._update_line_number_area_width(0)
        self._line_number_area.update()

    # ------------------------------------------------------------------
    # Text access
    # ------------------------------------------------------------------
    def _on_text_changed(self) -> None:
        self.text_changed.emit(self.toPlainText())

    def get_text(self) -> str:
        return self.toPlainText()

    def set_text(self, text: str) -> None:
        self.setPlainText(text)

    def insert_at_cursor(self, text: str) -> None:
        cursor = self.textCursor()
        cursor.insertText(text)
        self.setTextCursor(cursor)

    def clear_text(self) -> None:
        self.clear()
