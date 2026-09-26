"""
SpeechStudio — P3.15 Completion: Narration Block Editor Visual + Creation UX
=============================================================================

Tests for the Block editor visual cleanup and action area.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_15_block_editor_completion.py -v
"""

from __future__ import annotations
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QToolButton
from PySide6.QtGui import QTextCursor
_app = QApplication.instance() or QApplication([])


# ===========================================================================
# Block gutter visual cleanup
# ===========================================================================

class TestBlockGutterVisualCleanup(unittest.TestCase):
    """P3.15: Block gutter must have clean visuals."""

    def test_single_left_accent_bar(self):
        """Gutter must use a single 2px left accent bar (not a 3px right bar).

        P3.22 update: the accent bar now uses a dynamic `accent_x` offset
        so it can sit just to the right of the character color strip.
        The bar width is still 2px, and the old 3px right bar is gone.
        """
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        # P3.22: left accent bar uses accent_x (dynamic), width=2
        self.assertIn("QRect(accent_x, y_top, 2, block_height)", src,
                      "Must use single 2px left accent bar (dynamic accent_x)")
        # Old 3px right bar must be gone
        self.assertNotIn("QRect(gw - 3, y_top, 3, block_height)", src,
                         "Old 3px right bar must be removed")

    def test_no_bottom_separator(self):
        """Gutter must NOT draw a bottom separator (avoids visual clutter)."""
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        # The old code drew a bottom separator for non-last blocks
        # Check that the bottom separator drawing is removed
        self.assertNotIn('painter.drawLine(0, y_bottom - 1, gw, y_bottom - 1)', src,
                         "Bottom separator must be removed")

    def test_selected_bg_starts_after_accent(self):
        """Selected background must start after the accent bar.

        P3.22 update: uses accent_x + 2 (dynamic) instead of hardcoded x=2,
        so it works correctly whether or not the character color strip is
        present.
        """
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        self.assertIn("QRect(accent_x + 2, y_top, gw - accent_x - 2, block_height)", src,
                      "Selected bg must start after accent bar (dynamic accent_x)")

    def test_consistent_left_padding(self):
        """Header text must use consistent left padding (gx=8)."""
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        self.assertIn("gx = 8", src,
                      "Header text must use gx=8 for consistent padding")

    def test_override_badge_distinct_from_block_number(self):
        """Override badge must be visually distinct (WARNING color, rounded pill)."""
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        # Badge uses WARNING color (amber) — distinct from Block number
        self.assertIn("QColor(Palette.WARNING)", src)
        # Badge is a rounded rect (pill shape)
        self.assertIn("drawRoundedRect", src)


# ===========================================================================
# Block action area
# ===========================================================================

class TestBlockActionArea(unittest.TestCase):
    """P3.15: Block action toolbar must exist with Add/Split/Merge/Delete/Re-detect."""

    def setUp(self):
        from ui.panels.narration_editor import NarrationEditor
        self.editor = NarrationEditor()

    def test_add_block_handler_exists(self):
        """_on_add_block_from_selection handler must exist."""
        self.assertTrue(hasattr(self.editor, '_on_add_block_from_selection'))

    def test_split_block_handler_exists(self):
        """_on_split_block handler must exist."""
        self.assertTrue(hasattr(self.editor, '_on_split_block'))

    def test_merge_blocks_handler_exists(self):
        """_on_merge_blocks handler must exist."""
        self.assertTrue(hasattr(self.editor, '_on_merge_blocks'))

    def test_block_action_btn_style_exists(self):
        """_block_action_btn_style method must exist for consistent styling."""
        self.assertTrue(hasattr(self.editor, '_block_action_btn_style'))
        style = self.editor._block_action_btn_style()
        self.assertIn("QToolButton", style)
        self.assertIn("border-radius", style)


# ===========================================================================
# Block handlers functionality
# ===========================================================================

class TestBlockHandlers(unittest.TestCase):
    """P3.15: Block action handlers must work correctly."""

    def setUp(self):
        from ui.panels.narration_editor import NarrationEditor
        self.editor = NarrationEditor()
        self.editor._set_mode(self.editor.MODE_BLOCKS)

    def test_add_block_from_selection(self):
        """Add Block from selection creates a new block."""
        # Set some text
        self.editor._editor.setPlainText("Hello world. This is a test.")
        # Select "world" (positions 6-11)
        cursor = self.editor._editor.textCursor()
        cursor.setPosition(6)
        cursor.setPosition(11, QTextCursor.MoveMode.KeepAnchor)
        self.editor._editor.setTextCursor(cursor)
        # Add block
        self.editor._on_add_block_from_selection()
        # Verify a block was created
        self.assertGreater(len(self.editor._block_manager.blocks), 0)

    def test_delete_block(self):
        """Delete Block removes the selected block."""
        from engine.narration_blocks import PromptBlock
        # Add a block manually
        self.editor._editor.setPlainText("Hello world.")
        block = PromptBlock(start_offset=0, end_offset=12)
        self.editor._block_manager._blocks.append(block)
        self.editor._selected_block_id = block.id
        self.editor._on_delete_block()
        self.assertEqual(len(self.editor._block_manager.blocks), 0)

    def test_split_block(self):
        """Split Block divides a block into two at the cursor position."""
        from engine.narration_blocks import PromptBlock
        self.editor._editor.setPlainText("Hello world. Goodbye world.")
        block = PromptBlock(start_offset=0, end_offset=25)
        self.editor._block_manager._blocks.append(block)
        self.editor._selected_block_id = block.id
        # Position cursor at offset 13 (after "Hello world. ")
        cursor = self.editor._editor.textCursor()
        cursor.setPosition(13)
        self.editor._editor.setTextCursor(cursor)
        # Split
        self.editor._on_split_block()
        self.assertEqual(len(self.editor._block_manager.blocks), 2)

    def test_merge_blocks(self):
        """Merge Blocks combines two adjacent blocks into one."""
        from engine.narration_blocks import PromptBlock
        self.editor._editor.setPlainText("Hello world. Goodbye world.")
        block1 = PromptBlock(start_offset=0, end_offset=12)
        block2 = PromptBlock(start_offset=13, end_offset=25)
        self.editor._block_manager._blocks.append(block1)
        self.editor._block_manager._blocks.append(block2)
        self.editor._selected_block_id = block1.id
        # Merge
        self.editor._on_merge_blocks()
        self.assertEqual(len(self.editor._block_manager.blocks), 1)


# ===========================================================================
# No UUIDs displayed
# ===========================================================================

class TestNoUUIDsInBlockDisplay(unittest.TestCase):
    """P3.15: Block IDs (UUIDs) must NOT be displayed in the gutter."""

    def test_gutter_does_not_draw_block_id(self):
        """The gutter paintEvent must NOT draw block.id (UUID)."""
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        # Find the paintEvent section of _BlockGutterWidget
        gutter_start = src.find("class _BlockGutterWidget")
        gutter_end = src.find("def mousePressEvent", gutter_start)
        gutter_src = src[gutter_start:gutter_end]
        # Should draw "B{0}" (block number) but NOT block.id
        self.assertIn("B{0}", gutter_src, "Should display block NUMBER (B1, B2...)")
        # Should NOT draw block.id directly as text
        # (block.id is a UUID — should never be drawn)
        self.assertNotIn(r'drawText.*block\.id', gutter_src,
                         "Should NOT draw block.id (UUID)")


if __name__ == "__main__":
    from PySide6.QtGui import QTextCursor
    unittest.main(verbosity=2)
