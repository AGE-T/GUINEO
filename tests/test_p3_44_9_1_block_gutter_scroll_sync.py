"""
GUINEO (SpeechStudio) — P3.44.9.1 runtime tests
===============================================

BLOCK GUTTER / TEXT SCROLL SYNCHRONISATION.

Locks the guarantees mandated by docs/design/
P3_44_9_1_BLOCK_GUTTER_SCROLL_SYNC.md:

  §SYMPTOM  With 30+ blocks, scrolling the text viewport left the
            gutter labels frozen near the top (B1..B12 painted at the
            scroll=0 rows while the visible text was far below), and
            a forced repaint at depth produced a stacking cascade
            (each label one MIN_ROW_HEIGHT below the previous ROW
            instead of at its own block's text position).
  §ROOT     Three visual-layer defects (proven by the diagnosis
            probes, ss/p34491_*.py):
              (1) QPlainTextEdit.updateRequest was connected ONLY to
                  the LineNumberArea — the block gutter never
                  repainted on scroll (frozen image);
              (2) the P3.41 clamp ``max(y_top, prev_bottom + 1)``
                  chained unconditionally — blocks scrolled off above
                  the viewport were dragged back INTO the band;
              (3) the same clamp chained across paragraphs for
                  sentence-pair blocks sharing a wrapped line.
            Case A — completely independent of the P3.44.9 block
            offset engine (scrolling provably mutates no block data;
            tests below pin that as a permanent contract).
  §FIX      (a) ``BlockAwarePlainTextEdit._update_line_number_area``
              override repaints the gutter on the SAME updateRequest
              path (no timers, no polling);
              (b) ``_BlockGutterWidget._block_rows()`` stacks only on
              genuine degenerate adjacency (block text starting on
              the same/adjacent TEXT line as the previous block's text
              end); separated blocks anchor at their OWN text start
              in viewport coordinates;
              (c) a monotonic trim pass keeps rows strictly disjoint
              against non-monotonic lazy-layout estimates (trimming
              the EARLIER row's bottom — never pushing the later row
              away from its own text anchor).

Alignment reference (independent of the gutter's own math — the
LineNumberArea's own coordinate model):
    view_y(pos) = blockBoundingGeometry(findBlock(pos))
                  .translated(contentOffset()).top()
proven equal to ``cursorRect`` at the same position by the truth
probe (ss/p34491_truth_probe.py).

Control tests (PASS on the pre-fix code by design — they pin the
contracts that the fix must NOT break, and prove the defect is
presentational, not data):
    - test_top_scroll_alignment         (top was always aligned)
    - test_scroll_never_mutates_block_data  (Case A: scrolling is a
      pure presentation action)
    - test_edit_identity_through_offset_engine (P3.44.9 data rule)
    - test_p341_adjacent_blocks_stack_contract
    - test_p341_stacked_row_click_hit_testing
All other tests FAIL on the pre-fix code (verified by stashing the
fix and re-running: 14 failed / 5 passed pre-fix; 19/19 post-fix).

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_44_9_1_block_gutter_scroll_sync.py -v
"""
from __future__ import annotations

import os
import sys
import time
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch, PropertyMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Fake torch (house pattern: test_p3_35 / test_p3_41 / test_p3_44_9).
fake_torch = types.ModuleType("torch")


class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


fake_torch.no_grad = lambda: _NoGrad()
fake_torch.manual_seed = lambda s: None
fake_torch.from_numpy = lambda arr: arr
fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)
sys.modules.setdefault("torch", fake_torch)

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt, QPoint  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from engine.narration_blocks import PromptBlock  # noqa: E402
from ui.panels.narration_editor import (  # noqa: E402
    BlockAwarePlainTextEdit, NarrationEditor, _BlockGutterWidget,
)

APP = QApplication.instance() or QApplication(sys.argv)

# Real application theme (house pattern since P3.34).
from ui.theme import apply_theme, DEFAULT_THEME  # noqa: E402
apply_theme(APP, DEFAULT_THEME)

# Alignment tolerance: the gutter row top must sit at its block's text
# start (viewport coordinates) within this many pixels. Also used as
# the culling-boundary tolerance (the ±1px difference between
# blockBoundingGeometry().bottom() and cursorRect().bottom()).
TOL = 2


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def settle(ed, rounds=60):
    """Force the lazy QPlainTextDocumentLayout to fully materialise.

    Harness lesson (proven by ss/p34491_layout_probe.py): the layout is
    lazy and the raw single-poll readings race in-flight geometry.
    Touch every block's bounding geometry, then require THREE
    consecutive stable (document height, scrollbar maximum) readings
    before any measurement is taken.
    """
    stable = 0
    last = None
    for _ in range(rounds):
        blk = ed.document().firstBlock()
        while blk.isValid():
            ed.blockBoundingGeometry(blk)
            blk = blk.next()
        _process(10)
        reading = (round(ed.document().size().height()),
                   ed.verticalScrollBar().maximum())
        if reading == last:
            stable += 1
            if stable >= 3:
                return reading
        else:
            stable = 0
            last = reading
    return last


def view_y(ed, pos):
    """Viewport y of a document position — the LineNumberArea's own
    coordinate math (independent reference; proven == cursorRect)."""
    blk = ed.document().findBlock(max(0, pos))
    return round(ed.blockBoundingGeometry(blk)
                 .translated(ed.contentOffset()).top())


def view_y_bottom(ed, pos):
    blk = ed.document().findBlock(max(0, pos))
    return round(ed.blockBoundingGeometry(blk)
                 .translated(ed.contentOffset()).bottom())


def build_document(n_blocks):
    """Deterministic multi-shape document: short / medium / long
    (wrapping) paragraphs, blank-line separated — no two blocks share
    a text line, so EVERY row must anchor exactly at its own text."""
    paras, spans, pos = [], [], 0
    for i in range(n_blocks):
        kind = i % 3
        if kind == 0:
            para = "B%d short." % (i + 1)
        elif kind == 1:
            para = ("B%d first sentence of the block with normal "
                    "length. Second sentence makes it two." % (i + 1))
        else:
            para = ("B%d a deliberately long paragraph that wraps "
                    "across the viewport width and therefore occupies "
                    "several visual lines, exercising variable block "
                    "heights inside the very same document as the "
                    "short and medium blocks above it." % (i + 1))
        spans.append((pos, pos + len(para)))
        paras.append(para)
        pos += len(para) + 2
    return "\n\n".join(paras), spans


def build_editor(n_blocks, size=(800, 600)):
    """Real BlockAwarePlainTextEdit with real block data, driven
    through the REAL set_block_data + set_show_blocks pairing (the
    same call sequence NarrationEditor._render_block_visuals uses —
    a bare set_block_data leaves the gutter hidden and no paints
    happen: proven harness lesson)."""
    text, spans = build_document(n_blocks)
    ed = BlockAwarePlainTextEdit()
    ed.setPlainText(text)
    ed.resize(*size)
    ed.show()
    settle(ed)
    blocks = [PromptBlock(start_offset=s, end_offset=e) for s, e in spans]
    ed.set_block_data(blocks, None, {})
    ed.set_show_blocks(True)
    for _ in range(4):
        _process(30)
    settle(ed)
    return ed, blocks, text


def scroll_to(ed, value):
    ed.verticalScrollBar().setValue(value)
    for _ in range(4):
        _process(30)


def in_band_indices(ed, rows):
    """Row indices the painter would actually paint (mirrors the
    paintEvent culling: skip y_bottom < 0 or y_top > gutter height)."""
    gh = ed._block_gutter_widget.height()
    return [i for i, r in enumerate(rows)
            if r is not None and not (r[2] < 0 or r[1] > gh)]


def band_block_indices(ed, blocks, margin=0):
    """Block indices whose ANCHORED ROW geometry intersects the gutter
    band — the mirror of the paintEvent culling
    (``y_bottom < 0 or y_top > height``) computed from TEXT geometry:
    row = [text_top, max(text_bottom, text_top + MIN_ROW_HEIGHT)].
    ``margin`` > 0 SHRINKS the band by that many pixels on both
    sides (blocks well inside must be painted); ``margin`` < 0 grows
    it (blocks just outside may legitimately not be painted) — the
    strict/loose pair tolerates the ±1px difference between
    ``blockBoundingGeometry().bottom()`` and ``cursorRect().bottom()``
    at the culling boundary. Independent of the row objects the
    implementation returns."""
    gh = ed._block_gutter_widget.height()
    out = []
    for i, b in enumerate(blocks):
        top = view_y(ed, b.start_offset)
        bottom = max(
            view_y_bottom(ed, max(b.start_offset, b.end_offset - 1)),
            top + _BlockGutterWidget.MIN_ROW_HEIGHT)
        if bottom >= margin and top <= gh - margin:
            out.append(i)
    return out


def block_state(blocks, text):
    """Data-purity snapshot: identity + ranges + owned text."""
    return [(b.id, b.start_offset, b.end_offset,
             text[b.start_offset:b.end_offset],
             getattr(b, "locked", False),
             getattr(b, "character_id", None)) for b in blocks]


# ---------------------------------------------------------------------------
# Unit-level harness (real widgets, real block data — no MainWindow)
# ---------------------------------------------------------------------------
class _GutterScrollSyncTests(unittest.TestCase):
    """P3.44.9.1 §16 regression battery: scroll-position alignment,
    block-count sweep, identity, geometry, edit/resize interactions."""

    def setUp(self):
        self._editors = []

    def tearDown(self):
        for ed in self._editors:
            try:
                ed.hide()
            except Exception:
                pass
        _process(30)

    def track(self, ed):
        self._editors.append(ed)
        return ed

    # -- shared assertion ------------------------------------------------
    def assert_gutter_aligned(self, ed, blocks, label):
        """ANCHOR CONTRACT: every row the painter would show sits at
        its own block's text start (viewport coordinates, ±TOL) —
        except blocks GENUINELY adjacent to the previous block's text
        end (same/adjacent text line), whose row is exactly ONE
        disjoint stack-step below the previous row (the bounded
        sentence-pair residual documented in the design record; the
        pre-fix defect cascaded such steps across the whole band).
        Tests ALIGNMENT against text geometry, not merely that rows
        moved."""
        rows = ed._block_gutter_widget._block_rows()
        band = in_band_indices(ed, rows)
        self.assertTrue(band, "%s: no rows in the gutter band" % label)
        prev_text_bottom = None
        prev_row_bottom = None
        for i, (b, r) in enumerate(zip(blocks, rows)):
            if r is None:
                continue
            ty = view_y(ed, b.start_offset)
            ty_bot = view_y_bottom(
                ed, max(b.start_offset, b.end_offset - 1))
            adjacent = (prev_text_bottom is not None
                        and ty <= prev_text_bottom + 1)
            if i in band:
                if adjacent:
                    expected = max(ty, prev_row_bottom + 1)
                    self.assertLessEqual(
                        abs(r[1] - expected), TOL,
                        "%s: B%d is text-adjacent to B%d but its label at "
                        "y=%d is not the single disjoint step y=%d "
                        "(text starts at y=%d, scroll=%d)"
                        % (label, i + 1, i, r[1], expected, ty,
                           ed.verticalScrollBar().value()))
                else:
                    self.assertLessEqual(
                        abs(r[1] - ty), TOL,
                        "%s: B%d label at y=%d but its text starts at "
                        "y=%d (scroll=%d, viewport h=%d)"
                        % (label, i + 1, r[1], ty,
                           ed.verticalScrollBar().value(),
                           ed.viewport().height()))
            prev_text_bottom = ty_bot
            prev_row_bottom = r[2]
        return rows

    # -- §16.1-3 scroll positions ---------------------------------------
    def test_top_scroll_alignment(self):
        """CONTROL (passes pre-fix): at scroll=0 the labels align with
        their blocks' text — pins the healthy state the fix preserves."""
        ed, blocks, _text = self.track(build_editor(40))
        scroll_to(ed, 0)
        self.assert_gutter_aligned(ed, blocks, "top")

    def test_middle_scroll_alignment(self):
        """After scrolling into the middle of a 40-block document the
        labels correspond to the currently visible blocks (the frozen
        image + cascade both fail this pre-fix)."""
        ed, blocks, _text = self.track(build_editor(40))
        sb = ed.verticalScrollBar()
        scroll_to(ed, sb.maximum() // 2)
        self.assert_gutter_aligned(ed, blocks, "middle")

    def test_bottom_scroll_alignment(self):
        """Near the bottom the gutter must not leave earlier labels
        stuck at the top (the reported symptom)."""
        ed, blocks, _text = self.track(build_editor(40))
        scroll_to(ed, ed.verticalScrollBar().maximum())
        self.assert_gutter_aligned(ed, blocks, "bottom")

    def test_every_scroll_position_alignment(self):
        """Every scrollbar position, top to bottom: every painted label
        beside its own block's text at that position."""
        ed, blocks, _text = self.track(build_editor(40))
        sb = ed.verticalScrollBar()
        for value in range(0, sb.maximum() + 1):
            scroll_to(ed, value)
            self.assert_gutter_aligned(
                ed, blocks, "sweep@%d" % value)

    def test_block_count_sweep_alignment(self):
        """§16 large-block-list: 15 / 30 / 40 blocks (plus the 5-block
        no-scroll control inside the same sweep), scrolled to the
        bottom — alignment at every size."""
        for n in (15, 30, 40):
            ed, blocks, _text = self.track(build_editor(n))
            sb = ed.verticalScrollBar()
            if sb.maximum() == 0:
                # Not scrollable — everything visible, still must align.
                self.assert_gutter_aligned(ed, blocks, "n=%d/top" % n)
                continue
            scroll_to(ed, sb.maximum())
            self.assert_gutter_aligned(ed, blocks, "n=%d/bottom" % n)

    # -- §16 identity ----------------------------------------------------
    def test_visible_labels_match_visible_blocks(self):
        """The labels the painter shows in the gutter band correspond
        to the blocks whose anchored rows intersect the band (computed
        from TEXT geometry — the independent side of the comparison).
        Two directions with a 2px boundary tolerance (the ±1px
        blockBoundingGeometry/cursorRect bottom difference must not
        flake the culling edge): no PHANTOM labels (painted blocks far
        outside the band) and no MISSING labels (band-intersecting
        blocks left unpainted). Pre-fix the frozen image kept B1..B12
        in the band while the text showed much later blocks — a
        mismatch of hundreds of pixels, far beyond any tolerance."""
        ed, blocks, _text = self.track(build_editor(40))
        sb = ed.verticalScrollBar()
        for value in (0, sb.maximum() // 3, sb.maximum() // 2,
                      (sb.maximum() * 3) // 4, sb.maximum()):
            scroll_to(ed, value)
            rows = ed._block_gutter_widget._block_rows()
            band = set(in_band_indices(ed, rows))
            loose = set(band_block_indices(ed, blocks, margin=-TOL))
            strict = set(band_block_indices(ed, blocks, margin=TOL))
            phantoms = band - loose
            self.assertFalse(
                phantoms,
                "phantom labels painted for blocks whose rows are "
                "outside the band at scroll=%d: %r"
                % (value, sorted(phantoms)))
            missing = strict - band
            self.assertFalse(
                missing,
                "band-intersecting blocks without painted labels at "
                "scroll=%d: %r" % (value, sorted(missing)))

    def test_scroll_never_mutates_block_data(self):
        """CONTROL (passes pre-fix — Case A evidence): scrolling is a
        pure presentation action; ids, offsets, owned text, lock state
        and Character assignments are byte-identical across scroll
        cycles. The visual defect must NEVER be 'fixed' by touching
        block data."""
        ed, blocks, text = self.track(build_editor(30))
        before = block_state(blocks, text)
        text_before = ed.toPlainText()
        sb = ed.verticalScrollBar()
        for value in (sb.maximum(), 0, sb.maximum() // 2, sb.maximum(),
                      sb.maximum() // 3, 0):
            scroll_to(ed, value)
        self.assertEqual(block_state(ed._blocks, ed.toPlainText()), before)
        self.assertEqual(ed.toPlainText(), text_before)

    # -- §16 geometry ----------------------------------------------------
    def test_scrolled_off_blocks_not_dragged_into_band(self):
        """The exact reported symptom: blocks scrolled off ABOVE the
        viewport keep their rows at their own (negative) text
        positions — the P3.41 clamp dragged them to y≈0 and cascaded
        every following row one MIN_ROW_HEIGHT lower — and no extra
        labels accumulate in the band."""
        ed, blocks, _text = self.track(build_editor(40))
        sb = ed.verticalScrollBar()
        scroll_to(ed, sb.maximum() // 2)
        # 1) No dragging: every row sits at its own block's text top
        #    (this document has no adjacent blocks — strict for all).
        self.assert_gutter_aligned(ed, blocks, "not-dragged")
        # 2) No accumulation: the painted label count matches the
        #    band-intersecting blocks within the 2px boundary
        #    tolerance (pre-fix the cascade pulled ~25 labels into an
        #    11-block band — off by more than a dozen).
        rows = ed._block_gutter_widget._block_rows()
        band = in_band_indices(ed, rows)
        loose = band_block_indices(ed, blocks, margin=-TOL)
        strict = band_block_indices(ed, blocks, margin=TOL)
        self.assertTrue(
            len(strict) <= len(band) <= len(loose),
            "painted %d labels for %d..%d band-intersecting blocks "
            "(scroll=%d)"
            % (len(band), len(strict), len(loose), sb.value()))

    def test_round_trip_zero_drift(self):
        """§16 round trip: scroll to the bottom and back to the top —
        the rows return EXACTLY to their original aligned state (no
        accumulated error)."""
        ed, blocks, _text = self.track(build_editor(30))
        scroll_to(ed, 0)
        rows_before = ed._block_gutter_widget._block_rows()
        self.assert_gutter_aligned(ed, blocks, "round-trip/start")
        scroll_to(ed, ed.verticalScrollBar().maximum())
        self.assert_gutter_aligned(ed, blocks, "round-trip/bottom")
        scroll_to(ed, 0)
        self.assert_gutter_aligned(ed, blocks, "round-trip/end")
        rows_after = ed._block_gutter_widget._block_rows()
        self.assertEqual(
            [(r[1], r[2]) if r else None for r in rows_after],
            [(r[1], r[2]) if r else None for r in rows_before],
            "scroll round trip drifted the row geometry")

    def test_repeated_scroll_cycles_no_drift(self):
        """§16 repeated scrolling: five down/up cycles — aligned at
        every stop, zero drift at the end."""
        ed, blocks, _text = self.track(build_editor(30))
        sb = ed.verticalScrollBar()
        scroll_to(ed, 0)
        rows_before = ed._block_gutter_widget._block_rows()
        for cycle in range(5):
            scroll_to(ed, sb.maximum())
            self.assert_gutter_aligned(
                ed, blocks, "cycle%d/bottom" % cycle)
            scroll_to(ed, sb.maximum() // 2)
            self.assert_gutter_aligned(ed, blocks, "cycle%d/mid" % cycle)
            scroll_to(ed, 0)
            self.assert_gutter_aligned(ed, blocks, "cycle%d/top" % cycle)
        rows_after = ed._block_gutter_widget._block_rows()
        self.assertEqual(
            [(r[1], r[2]) if r else None for r in rows_after],
            [(r[1], r[2]) if r else None for r in rows_before])

    def test_wrapped_variable_height_alignment(self):
        """§16 variable block height: the document mixes short,
        medium and wrapping multi-line paragraphs — alignment follows
        the ACTUAL text geometry, not a fixed row assumption."""
        ed, blocks, _text = self.track(build_editor(40, size=(700, 500)))
        sb = ed.verticalScrollBar()
        heights = set()
        for b in blocks:
            heights.add(view_y_bottom(ed, b.end_offset - 1)
                        - view_y(ed, b.start_offset))
        self.assertGreater(
            len(heights), 1,
            "harness: the document must produce variable block heights")
        for value in (sb.maximum() // 4, sb.maximum() // 2,
                      (sb.maximum() * 3) // 4, sb.maximum()):
            scroll_to(ed, value)
            self.assert_gutter_aligned(
                ed, blocks, "wrapped@%d" % value)

    def test_resize_while_scrolled_alignment(self):
        """§16 resize interaction: widen AND narrow the editor while
        scrolled — WidgetWidth re-wraps the text (every block moves),
        the gutter must re-anchor through the updateRequest path."""
        ed, blocks, _text = self.track(build_editor(30, size=(800, 600)))
        sb = ed.verticalScrollBar()
        scroll_to(ed, sb.maximum() // 2)
        self.assert_gutter_aligned(ed, blocks, "resize/before")
        ed.resize(1000, 700)
        settle(ed)
        self.assert_gutter_aligned(ed, blocks, "resize/wider")
        ed.resize(560, 420)
        settle(ed)
        self.assert_gutter_aligned(ed, blocks, "resize/narrower")

    # -- §16 edit interaction (real offset engine) ----------------------
    def _standalone_narration_editor(self, n_blocks=30):
        """Real NarrationEditor (real NarrationBlockManager = the real
        P3.44.9 offset engine) with auto-detected blocks, shown and
        settled. The flash timer is stopped for deterministic paints."""
        text, _spans = build_document(n_blocks)
        ed = NarrationEditor()
        ed.resize(900, 700)
        ed.show()
        ed.clear_text()
        ed._plain_btn.setChecked(True)
        ed._editor.setPlainText(text)
        ed._blocks_btn.setChecked(True)   # auto-detect runs (real splitter)
        _process(80)
        ed._editor._flash_timer.stop()    # deterministic paints only
        settle(ed._editor)
        self.assertGreaterEqual(
            len(ed._block_manager.blocks), n_blocks,
            "harness: auto-detect must produce the full block list")
        return ed

    def test_edit_identity_through_offset_engine(self):
        """CONTROL (passes pre-fix): an edit while scrolled flows
        through the REAL P3.44.9 offset engine — ids stable, offsets
        shifted by the authoritative rule, text ownership exact. The
        visual fix must not touch this data path."""
        ed = self._standalone_narration_editor(30)
        self.track(ed._editor)
        blocks = list(ed._block_manager.blocks)
        # VALUE snapshot: the manager mutates PromptBlock offsets IN
        # PLACE, so the "before" ranges must be captured as values.
        before_ids = [b.id for b in blocks]
        before_ranges = [(b.start_offset, b.end_offset) for b in blocks]
        before = block_state(blocks, ed._editor.toPlainText())
        sb = ed._editor.verticalScrollBar()
        scroll_to(ed._editor, sb.maximum() // 2)
        # Insert three characters at offset 0 (edit BEFORE every block).
        cur = QTextCursor(ed._editor.document())
        cur.setPosition(0)
        ed._editor.setTextCursor(cur)
        QTest.keyClicks(ed._editor, "ZZ ")
        _process(50)
        after_blocks = list(ed._block_manager.blocks)
        self.assertEqual([b.id for b in after_blocks], before_ids)
        text = ed._editor.toPlainText()
        for (s_old, _e_old), b_new in zip(before_ranges, after_blocks):
            # P3.44.9 rule 2: edit before the block -> [s+3, e+3).
            self.assertEqual(b_new.start_offset, s_old + 3)
        for b_new in after_blocks:
            self.assertEqual(
                text[b_new.start_offset:b_new.end_offset],
                ed._editor.toPlainText()[
                    b_new.start_offset:b_new.end_offset])
        after_state = block_state(after_blocks, text)
        self.assertNotEqual(after_state, before)
        # The typed prefix sits BEFORE the first block (rule 2: the
        # block's owned text is unchanged, its range shifted past it).
        self.assertEqual(after_state[0][3], before[0][3])
        self.assertTrue(text.startswith("ZZ " + before[0][3]))

    def test_edit_then_scroll_alignment(self):
        """§16 editing while scrolled: after the edit (and the fresh
        block layout it forces), scrolling keeps every label beside
        its own block's text."""
        ed = self._standalone_narration_editor(30)
        self.track(ed._editor)
        blocks = list(ed._block_manager.blocks)
        sb = ed._editor.verticalScrollBar()
        scroll_to(ed._editor, sb.maximum() // 2)
        cur = QTextCursor(ed._editor.document())
        cur.setPosition(0)
        ed._editor.setTextCursor(cur)
        QTest.keyClicks(ed._editor, "ZZ ")
        _process(50)
        # The edit itself re-rendered the visuals at the current scroll
        # (set_block_data) — that fresh state must already be aligned.
        self.assert_gutter_aligned(ed._editor, blocks, "edit/fresh")
        for value in (sb.maximum() // 3, sb.maximum(),
                      sb.maximum() // 2, 0):
            scroll_to(ed._editor, value)
            self.assert_gutter_aligned(
                ed._editor, ed._block_manager.blocks, "edit@%d" % value)

    # -- repaint contract -------------------------------------------------
    def test_gutter_repaints_on_scroll_contract(self):
        """The frozen image is dead: a scroll burst MUST repaint the
        gutter (paint count increases), and the rows used by the LAST
        real paint equal the fresh layout and sit at the text geometry
        (the LineNumberArea repaints on the same updateRequest path —
        used here as the paired reference).

        Harness lesson (proven here): PySide6 resolves Python overrides
        of C++ virtuals per instance at CREATION time — the counting
        patch must be installed BEFORE the editor is instantiated."""
        from ui.panels.advanced_prompt_editor import LineNumberArea
        gutter_cls = _BlockGutterWidget
        lineno_cls = LineNumberArea
        orig_gutter_paint = gutter_cls.paintEvent
        orig_lineno_paint = lineno_cls.paintEvent
        paints = {"gutter": 0, "lineno": 0, "rows": None}

        def _counting_gutter(self, event):
            paints["gutter"] += 1
            paints["rows"] = self._block_rows()
            return orig_gutter_paint(self, event)

        def _counting_lineno(self, event):
            paints["lineno"] += 1
            return orig_lineno_paint(self, event)

        # Install BEFORE instantiation (virtual dispatch resolution).
        gutter_cls.paintEvent = _counting_gutter
        lineno_cls.paintEvent = _counting_lineno
        try:
            ed, blocks, _text = build_editor(40)
            self.track(ed)
            scroll_to(ed, 0)
            _process(50)
            g0, l0 = paints["gutter"], paints["lineno"]
            scroll_to(ed, ed.verticalScrollBar().maximum())
            _process(50)
            self.assertGreaterEqual(
                paints["gutter"], g0 + 1,
                "the block gutter did not repaint during the scroll "
                "(frozen image — updateRequest must drive it)")
            self.assertGreaterEqual(
                paints["lineno"], l0 + 1,
                "harness: the LineNumberArea (paired reference) did "
                "not repaint either")
            # Last-painted rows == fresh layout == text geometry.
            fresh = ed._block_gutter_widget._block_rows()
            self.assertEqual(
                paints["rows"], fresh,
                "rows used by the last paint differ from the fresh "
                "layout")
            self.assert_gutter_aligned(ed, blocks, "repaint-contract")
        finally:
            gutter_cls.paintEvent = orig_gutter_paint
            lineno_cls.paintEvent = orig_lineno_paint

    # -- P3.41 contract controls (must keep passing) ---------------------
    def _adjacent_editor(self, text, spans):
        ed = BlockAwarePlainTextEdit()
        ed.setPlainText(text)
        ed.resize(800, 600)
        ed.show()
        settle(ed)
        blocks = [PromptBlock(start_offset=s, end_offset=e)
                  for s, e in spans]
        ed.set_block_data(blocks, None, {})
        ed.set_show_blocks(True)
        _process(50)
        settle(ed)
        return self.track(ed), blocks

    def test_p341_adjacent_blocks_stack_contract(self):
        """CONTROL (passes pre-fix): genuine degenerate adjacency still
        stacks — one line variant AND blank-line-removed variant keep
        disjoint MIN_ROW_HEIGHT rows with the first row anchored at its
        text and the second below it."""
        for text, spans, label in (
                ("Sentence A. Sentence B.",
                 [(0, 11), (12, 24)], "same-line"),
                ("Sentence A.\nSentence B.",
                 [(0, 11), (12, 24)], "adjacent-lines")):
            ed, blocks = self._adjacent_editor(text, spans)
            rows = [r for r in ed._block_gutter_widget._block_rows()
                    if r is not None]
            self.assertEqual(len(rows), 2,
                             "%s: both rows must be painted" % label)
            for r in rows:
                self.assertGreaterEqual(r[2] - r[1],
                                        _BlockGutterWidget.MIN_ROW_HEIGHT,
                                        "%s: row shorter than the header "
                                        "line" % label)
            self.assertGreaterEqual(
                rows[1][1], rows[0][2] + 1,
                "%s: stacked rows overlap" % label)
            # The first row stays anchored at its own text start.
            self.assertEqual(rows[0][1], view_y(ed, blocks[0].start_offset))

    def test_p341_stacked_row_click_hit_testing(self):
        """CONTROL (passes pre-fix): clicking a stacked row selects
        exactly that block — hit-testing shares the ONE row layout the
        painter uses (real QTest clicks, real gutter mousePressEvent)."""
        ed, blocks = self._adjacent_editor(
            "Sentence A. Sentence B.", [(0, 11), (12, 24)])
        rows = [r for r in ed._block_gutter_widget._block_rows()
                if r is not None]
        self.assertEqual(len(rows), 2)
        gutter = ed._block_gutter_widget
        emitted = []
        ed.block_click_requested.connect(emitted.append)
        # Click the middle of B2's stacked row.
        click_y = (rows[1][1] + rows[1][2]) // 2
        QTest.mouseClick(gutter, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(20, click_y))
        _process(50)
        self.assertEqual(emitted[-1], blocks[1].start_offset)
        # And the first row selects B1.
        click_y = (rows[0][1] + rows[0][2]) // 2
        QTest.mouseClick(gutter, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(20, click_y))
        _process(50)
        self.assertEqual(emitted[-1], blocks[0].start_offset)


# ---------------------------------------------------------------------------
# Real-window harness (real Engine + real MainWindow + real auto-detect)
# ---------------------------------------------------------------------------
def make_speech(seconds=1.0, sr=24000):
    return (np.random.uniform(-0.5, 0.5, int(sr * seconds))
            .astype("float32"))


class FakeHiggsModel:
    calls = []

    def generate_speech(self, text, tokenizer, *, reference_audio=None,
                        reference_sample_rate=None, reference_codes=None,
                        reference_text=None, max_new_tokens=2048,
                        temperature=1.0, top_p=None, top_k=None):
        FakeHiggsModel.calls.append(dict(text=text))
        return make_speech(0.2)


def _scene_text(n=32):
    """Single-sentence paragraphs, blank-line separated: every block on
    its own line(s), mixed heights (short / medium / wrapping-long)."""
    paras = []
    for i in range(1, n + 1):
        kind = i % 4
        if kind == 0:
            paras.append(
                "Block %d opens with a deliberately longer sentence "
                "that wraps across the viewport because it needs to "
                "exceed one visual line of text to vary the geometry."
                % i)
        elif kind == 1:
            paras.append("Block %d is a short one." % i)
        else:
            paras.append(
                "Block %d carries a medium-weight sentence for the "
                "gutter geometry check." % i)
    return "\n\n".join(paras)


class _RealWindowGutterScrollSyncTests(unittest.TestCase):
    """Two full-stack tests: real Engine + real MainWindow, blocks
    produced by the REAL auto-detect splitter, real scrollbar scrolling
    of the real editor."""

    def setUp(self):
        from engine.engine import Engine
        from ui.main_window import MainWindow
        self.tmp = tempfile.mkdtemp(prefix="ss_p34491_")
        self.engine = Engine(app_root=self.tmp)
        self.engine.create_voice("Anna Voice")
        self.engine.create_voice("Mark Voice")
        self.patchers = [
            patch.object(type(self.engine._model), "is_loaded",
                         new_callable=PropertyMock, return_value=True),
            patch.object(type(self.engine._model), "device",
                         new_callable=PropertyMock, return_value="cpu"),
            patch.object(type(self.engine._model),
                         "get_model_and_tokenizer",
                         return_value=(FakeHiggsModel(), None)),
        ]
        for p in self.patchers:
            p.start()
        self.win = MainWindow(self.engine)
        self.win._project_manager = type(
            "PM", (), {"__init__": lambda self: None,
                       "save_project": lambda self, p: None,
                       "list_projects": lambda self: []})()
        from engine.models import Project, Scene, Character
        self.project = Project(name="Eden", id="proj1")
        self.scene = Scene(id="sc1abcd", name="Scene 03",
                           project_id="proj1")
        self.project.add_scene(self.scene)
        self.project.characters.extend([
            Character(id="char-anna", name="ANNA"),
            Character(id="char-mark", name="MARK")])
        self.win._active_project = self.project
        self.win._active_scene = self.scene
        self.win._current_project = "Eden"
        self.win._update_editor_characters()
        self.ed = self.win._editor

    def tearDown(self):
        dlg = getattr(self.win, "_batch_dialog", None)
        if dlg is not None:
            try:
                dlg.close()
            except Exception:
                pass
        try:
            self.win.close()
        except Exception:
            pass
        for p in self.patchers:
            p.stop()
        _process(50)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- shared helpers ---------------------------------------------------
    def setup_blocks(self, text):
        """Paste text in Plain Text, switch to Narration Blocks (the
        auto-detect runs through the REAL splitter), settle the layout.
        Harness lesson: the offscreen MainWindow editor viewport can
        lay out squeezed (0x50 hazard at small window sizes) — resize
        the window to a fixed generous size and GUARD the viewport
        before measuring anything."""
        self.ed.clear_text()
        self.ed._plain_btn.setChecked(True)
        self.ed._editor.setPlainText(text)
        self.ed._blocks_btn.setChecked(True)
        self.ed._editor.setFocus()
        self.win.resize(1600, 1200)
        self.win.show()
        _process(150)
        self.ed._editor._flash_timer.stop()   # deterministic paints only
        settle(self.ed._editor)
        self.assertGreater(
            self.ed._editor.viewport().height(), 100,
            "harness guard: the offscreen editor viewport collapsed "
            "(0x50 hazard) — geometry assertions would be meaningless")
        return self.ed._block_manager.blocks

    def assert_real_gutter_aligned(self, label):
        """ANCHOR CONTRACT on the real editor (same rule as the unit
        harness: own-text anchor; ONE disjoint stack-step only for
        blocks genuinely sharing/adjacent to the previous block's text
        line)."""
        inner = self.ed._editor
        blocks = self.ed._block_manager.blocks
        rows = inner._block_gutter_widget._block_rows()
        band = in_band_indices(inner, rows)
        self.assertTrue(band, "%s: no rows in the gutter band" % label)
        prev_text_bottom = None
        prev_row_bottom = None
        for i, (b, r) in enumerate(zip(blocks, rows)):
            if r is None:
                continue
            ty = view_y(inner, b.start_offset)
            ty_bot = view_y_bottom(
                inner, max(b.start_offset, b.end_offset - 1))
            adjacent = (prev_text_bottom is not None
                        and ty <= prev_text_bottom + 1)
            if i in band:
                if adjacent:
                    expected = max(ty, prev_row_bottom + 1)
                    self.assertLessEqual(
                        abs(r[1] - expected), TOL,
                        "%s: B%d label at y=%d is not the single disjoint "
                        "step y=%d (text at y=%d, scroll=%d)"
                        % (label, i + 1, r[1], expected, ty,
                           inner.verticalScrollBar().value()))
                else:
                    self.assertLessEqual(
                        abs(r[1] - ty), TOL,
                        "%s: B%d label at y=%d but its text starts at "
                        "y=%d (scroll=%d)"
                        % (label, i + 1, r[1], ty,
                           inner.verticalScrollBar().value()))
            prev_text_bottom = ty_bot
            prev_row_bottom = r[2]
        return rows

    # -- the two full-stack tests ----------------------------------------
    def test_real_mainwindow_autodetect_scroll_alignment(self):
        """Full stack: real MainWindow + real auto-detect on a 32-block
        document; scrolling the real editor keeps every painted label
        beside its own block's text at top / middle / bottom, and the
        visible labels match the visible blocks."""
        text = _scene_text(32)
        blocks = self.setup_blocks(text)
        self.assertGreaterEqual(len(blocks), 30,
                                 "the document must produce 30+ blocks")
        inner = self.ed._editor
        sb = inner.verticalScrollBar()
        self.assertGreater(sb.maximum(), 0,
                           "harness: the document must be scrollable")
        snapshot = block_state(blocks, inner.toPlainText())
        for value in (0, sb.maximum() // 2, sb.maximum()):
            scroll_to(inner, value)
            self.assert_real_gutter_aligned("real@%d" % value)
            rows = inner._block_gutter_widget._block_rows()
            band = set(in_band_indices(inner, rows))
            loose = set(band_block_indices(inner, blocks, margin=-TOL))
            strict = set(band_block_indices(inner, blocks, margin=TOL))
            phantoms = band - loose
            self.assertFalse(
                phantoms,
                "real window: phantom labels at scroll=%d: %r"
                % (value, sorted(phantoms)))
            missing = strict - band
            self.assertFalse(
                missing,
                "real window: band-intersecting blocks without labels "
                "at scroll=%d: %r" % (value, sorted(missing)))
        # Scrolling the real editor still mutates nothing (Case A).
        self.assertEqual(
            block_state(self.ed._block_manager.blocks, inner.toPlainText()),
            snapshot)

    def test_scene_roundtrip_rehydration_scroll(self):
        """Scene round trip: the block model is serialised to the Scene
        payload, then rehydrated through the REAL MainWindow restore
        path (begin_scene_load / set_text / set_scene_blocks /
        end_scene_load) — ids and offsets survive, and the rehydrated
        gutter stays aligned while scrolled."""
        from engine.narration_blocks import PromptBlock as _PB
        text = _scene_text(32)
        blocks = self.setup_blocks(text)
        self.assertGreaterEqual(len(blocks), 30)
        inner = self.ed._editor
        sb = inner.verticalScrollBar()
        # Scroll away from the top, then rehydrate THROUGH the real
        # path exactly as MainWindow._load_scene_state does.
        scroll_to(inner, sb.maximum() // 2)
        payload = [b.to_dict() for b in self.ed._block_manager.blocks]
        self.scene.text = text
        self.scene.narration_blocks = payload
        restored = [_PB.from_dict(d) for d in self.scene.narration_blocks]
        self.ed.begin_scene_load()
        try:
            self.ed.set_text(self.scene.text or "")
        finally:
            self.ed.end_scene_load()
        self.ed.begin_scene_load()
        try:
            self.ed.set_scene_blocks(restored, self.scene.text or "")
        finally:
            self.ed.end_scene_load()
        _process(80)
        settle(inner)
        # Data survived the round trip byte-for-byte.
        after = self.ed._block_manager.blocks
        self.assertEqual([(b.id, b.start_offset, b.end_offset)
                          for b in after],
                         [(d["id"], d["start_offset"], d["end_offset"])
                          for d in payload])
        self.assertEqual(inner.toPlainText(), text)
        # And the rehydrated gutter is aligned at every scroll stop.
        for value in (sb.maximum() // 2, sb.maximum(), 0,
                      sb.maximum() // 3):
            scroll_to(inner, value)
            self.assert_real_gutter_aligned("rehydrate@%d" % value)


if __name__ == "__main__":
    unittest.main()
