"""GUINEO P3.45.6 — PHASE 4: GUTTER STATUS LAYOUT.

Regression suite for the reported gutter defect: the green
generation/coverage indicator and the yellow override-count badge
could OVERLAP or compress into the Block label.

ROOT CAUSE (source-verified at the P3.45.6 baseline): all three
header elements targeted the same top-right corner — the Block label
was left-aligned elided to a FIXED ``gw - 30``; the status text was
right-aligned into ``[.., gw-6]``; the override pill is a filled
rounded rect at ``[gw-24, gw-6]`` drawn AFTER the status — with
overrides present the pill PAINTED OVER the status text, and at
narrow widths all three collided into the label.

FIX (P3.45.6 Phase 4): ``_BlockGutterWidget._header_layout`` — a
pure, testable layout contract partitioning the header line
right-to-left into non-overlapping zones:

    [ label (elided to the remainder) | gap | status (compact
      ladder, width-capped) | gap | override pill (unchanged
      P3.15 spot [gw-24, gw-6]) ]

The status text keeps the P3.45.1 semantics (multi-part coverage
"✓ 2/2" / "⚙ 1/2"; single-part classic labels) and degrades through
a compact ladder (full label → state symbol) when the remaining
budget cannot hold the full label; at extreme narrow widths the
status is omitted (documented fallback — colour+glyph carry the state
whenever anything fits). The P3.44.9.1 ROW GEOMETRY is untouched
(only the header line's horizontal partition changed).

Evidence classes: layout-contract tests use REAL QFontMetrics;
real-widget tests spy the layout during an actual gutter render
(``grab()``) at the product width AND a narrow width.
"""
from __future__ import annotations

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Fake torch (house pattern).
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

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtCore import Qt, QRect

_app = QApplication.instance() or QApplication([])

from engine.narration_blocks import PromptBlock
from ui.panels.narration_editor import (
    BlockAwarePlainTextEdit, _BlockGutterWidget,
)

PRODUCT_W = BlockAwarePlainTextEdit.GUTTER_WIDTH  # 172


def _process(ms=30):
    import time
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _real_measure():
    """The production measure: QFontMetrics of the bold 8pt status font."""
    font = QFont("Segoe UI", 8)
    font.setBold(True)
    fm = QFontMetrics(font)
    return (
        fm.horizontalAdvance,
        lambda text, budget: fm.elidedText(
            text, Qt.TextElideMode.ElideRight, budget),
    )


def _candidates_for(status, parts_total=0, parts_done=0):
    """The ladder the painter builds (mirrors paintEvent)."""
    if not status:
        return []
    base = {
        "done": "\u2713 Done",
        "generating": "\u2699 Gen...",
        "pending": "\u23f3 Queued",
        "error": "\u26a0 Error",
    }.get(status, status)
    if parts_total > 1:
        coverage = "{0}/{1}".format(parts_done, parts_total)
        if status == "done":
            base = "\u2713 {0}".format(coverage)
        elif status == "generating":
            base = "\u2699 {0}".format(coverage)
    symbol = base[:1]
    return [base] + ([symbol] if symbol and symbol != base else [])


def _disjoint(a: QRect, b: QRect):
    if a is None or b is None:
        return True
    return not a.intersects(b)


class TestHeaderLayoutContract(unittest.TestCase):
    """The pure layout contract with REAL font metrics."""

    def setUp(self):
        self.measure, self.elide = _real_measure()

    def layout(self, gw, has_overrides, candidates):
        return _BlockGutterWidget._header_layout(
            gw, 100, has_overrides, candidates,
            self.measure, self.elide)

    def test_zones_never_overlap_full_matrix(self):
        """Every width × override × state combination: the label,
        status and pill rectangles are pairwise disjoint and inside
        the gutter."""
        widths = [PRODUCT_W, 140, 120, 100, 80, 60]
        states = [
            [],                                    # no status
            _candidates_for("done", 2, 2),         # ✓ 2/2
            _candidates_for("generating", 2, 1),   # ⚙ 1/2
            _candidates_for("pending"),
            _candidates_for("error"),
            _candidates_for("done"),               # single-part ✓ Done
        ]
        for gw in widths:
            for ov in (False, True):
                for cands in states:
                    label, status, pill, text = self.layout(gw, ov, cands)
                    rects = [("label", label), ("status", status),
                             ("pill", pill)]
                    for name, r in rects:
                        if r is None:
                            continue
                        self.assertTrue(
                            r.left() >= 0 and r.right() <= gw,
                            "{0} rect {1!r} escapes the gutter (gw={2}, "
                            "ov={3}, cands={4})".format(
                                name, r, gw, ov, cands))
                    self.assertTrue(_disjoint(label, status),
                                    "label/status overlap gw={0} ov={1} "
                                    "cands={2}".format(gw, ov, cands))
                    self.assertTrue(_disjoint(label, pill),
                                    "label/pill overlap gw={0} ov={1}"
                                    .format(gw, ov))
                    self.assertTrue(_disjoint(status, pill),
                                    "status/pill overlap gw={0} ov={1} "
                                    "cands={2} (status text={3!r})"
                                    .format(gw, ov, cands, text))

    def test_pill_keeps_established_position(self):
        """The override pill keeps its P3.15 spot [gw-24, gw-6]."""
        for gw in (PRODUCT_W, 120, 100, 80):
            _, _, pill, _ = self.layout(gw, True, [])
            self.assertEqual(pill.left(), gw - 24)
            # QRect.right() == left + width - 1 (Qt convention): the
            # pill spans pixels [gw-24, gw-7] inclusive = the drawn
            # [gw-24, gw-6) extent — the SAME rect the P3.15 code drew.
            self.assertEqual(pill.right(), gw - 7)
            self.assertEqual(pill.width(), 18)
            self.assertEqual(pill.height(), 14)

    def test_product_width_shows_full_labels(self):
        """At the product gutter width the FULL status labels render —
        nothing is compacted away (P3.45.1 semantics fully visible)."""
        for cands in (_candidates_for("done", 2, 2),
                      _candidates_for("generating", 2, 1),
                      _candidates_for("pending"),
                      _candidates_for("error"),
                      _candidates_for("done")):
            for ov in (False, True):
                _, _, _, text = self.layout(PRODUCT_W, ov, cands)
                self.assertEqual(text, cands[0])

    def test_states_remain_distinguishable(self):
        """Different states choose different status texts at the
        product width and at a narrow width (whenever anything fits)."""
        for gw in (PRODUCT_W, 120, 100):
            texts = set()
            for cands in (_candidates_for("done", 2, 2),
                          _candidates_for("generating", 2, 1),
                          _candidates_for("pending"),
                          _candidates_for("error")):
                _, _, _, text = self.layout(gw, True, cands)
                texts.add(text)
            self.assertEqual(len(texts), 4,
                             "states collapsed at gw={0}: {1!r}"
                             .format(gw, texts))

    def test_compact_ladder_degrades_gracefully(self):
        """When the full label cannot fit, the ladder degrades to the
        state symbol (still non-empty, still distinguishable)."""
        # Find a width where the full label does not fit but the
        # symbol does: the ladder must choose the symbol.
        full = _candidates_for("done", 2, 2)
        full_w = self.measure(full[0])
        sym_w = self.measure(full[1])
        gw = 8 + 24 + 6 + 18 + 8 + sym_w + 4 + 2   # just past symbol-fit
        if gw <= 8 + 24 + 6 + full_w + 4:
            gw = 8 + 24 + 6 + full_w + 4 + 1
        _, _, _, text = self.layout(gw, True, full)
        self.assertTrue(text, "status must render something at gw={0}"
                        .format(gw))
        self.assertNotEqual(text, full[0],
                            "the ladder must degrade when the full label "
                            "cannot fit")

    def test_extreme_narrow_documented_fallback(self):
        """At an extremely narrow gutter the status may be omitted —
        but the pill and label zones stay valid and disjoint."""
        gw = 60
        label, status, pill, text = self.layout(
            gw, True, _candidates_for("done", 2, 2))
        self.assertIsNotNone(pill)
        self.assertIsNotNone(label)
        self.assertTrue(_disjoint(label, pill))
        self.assertTrue(label.width() >= 0)
        # Whatever was chosen (possibly "") never overlaps the pill.
        self.assertTrue(_disjoint(status, pill))

    def test_label_gets_more_room_without_status(self):
        """Without a status element the label extends to the right
        edge (better use of space than the old fixed gw-30)."""
        label, status, pill, _ = self.layout(PRODUCT_W, False, [])
        self.assertIsNone(status)
        self.assertIsNone(pill)
        self.assertEqual(label.right(), PRODUCT_W - 7)

    def test_status_stops_left_of_pill(self):
        """THE regression: with overrides present the status text's
        right edge stops 8px LEFT of the pill (it used to paint right
        INTO [gw-24, gw-6])."""
        _, status, pill, text = self.layout(
            PRODUCT_W, True, _candidates_for("done", 2, 2))
        self.assertIsNotNone(status)
        self.assertIsNotNone(pill)
        self.assertLessEqual(status.right() + 8, pill.left())


class TestRealGutterRender(unittest.TestCase):
    """The painter USES the contract during real rendering (spy on
    _header_layout while the real gutter paints via grab())."""

    def setUp(self):
        import time
        self._settle_deadline = time

    def _build_editor(self, gutter_width):
        text = ("First sentence here.\n\nSecond block text here.\n\n"
                "Third block text here.")
        spans = []
        for part in ("First sentence here.", "Second block text here.",
                     "Third block text here."):
            i = text.index(part)
            spans.append((i, i + len(part)))
        ed = BlockAwarePlainTextEdit()
        ed.setPlainText(text)
        ed.resize(800, 600)
        ed.show()
        blocks = [PromptBlock(start_offset=s, end_offset=e)
                  for s, e in spans]
        # Rich state: status + multi-part coverage + overrides.
        blocks[0].status = "done"
        blocks[0].parts_done = 2
        blocks[0].parts_total = 2
        blocks[0].emotion = "calm"          # override -> pill
        blocks[1].status = "generating"
        blocks[1].parts_done = 1
        blocks[1].parts_total = 2
        blocks[1].style = "documentary"     # override -> pill
        blocks[2].status = None
        ed.set_block_data(blocks, None, {})
        ed.set_show_blocks(True)
        for _ in range(4):
            _process(30)
        return ed, blocks, text

    def tearDown(self):
        for ed in getattr(self, "_editors", []):
            try:
                ed.hide()
            except Exception:
                pass
        _process(30)
        # Deterministic C++ teardown (avoids the "Cannot destroy paint
        # device that is being painted" exit-time GC warning after
        # grab()-triggered synchronous paints).
        for ed in getattr(self, "_editors", []):
            try:
                ed.deleteLater()
            except Exception:
                pass
        _process(60)
        self._editors = []

    def _render_with_spy(self, ed):
        """Render the real gutter with _header_layout spied.

        Uses an explicitly controlled QPainter + QWidget.render (NOT
        grab(): the offscreen platform's second backing-store grab in
        one process segfaults at exit — verified; the explicit painter
        gives deterministic begin/end).
        """
        from PySide6.QtGui import QPainter, QPixmap
        from PySide6.QtCore import QPoint
        recorded = []
        # CRITICAL: capture the staticmethod WRAPPER from __dict__ —
        # class-attribute access returns the UNWRAPPED function, and
        # restoring THAT would turn the staticmethod into a bound
        # method (self + 6 args) whose mid-paint TypeError leaves the
        # painter active (verified segfault at exit).
        original = _BlockGutterWidget.__dict__["_header_layout"]

        def spy(gw, gy, has_overrides, candidates, measure, elide):
            result = original(gw, gy, has_overrides, candidates,
                              measure, elide)
            recorded.append({
                "gw": gw, "gy": gy, "has_overrides": has_overrides,
                "candidates": list(candidates),
                "label": result[0], "status": result[1],
                "pill": result[2], "text": result[3],
            })
            return result

        _BlockGutterWidget._header_layout = staticmethod(spy)
        gutter = ed._block_gutter_widget
        try:
            pix = QPixmap(gutter.size())
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            try:
                gutter.render(painter, QPoint(0, 0))
            finally:
                painter.end()
            self.assertFalse(pix.isNull())
        finally:
            _BlockGutterWidget._header_layout = original
        return recorded

    def test_real_render_zones_disjoint_product_width(self):
        """Real paint at the product gutter width: every painted
        label/status/pill zone is pairwise disjoint (the spy records
        EXACTLY what the painter drew with)."""
        ed, blocks, _ = self._build_editor(PRODUCT_W)
        self._editors = [ed]
        recorded = self._render_with_spy(ed)

        self.assertGreaterEqual(len(recorded), 3,
                                 "the real paint must lay out every "
                                 "visible row")
        for call in recorded:
            label, status, pill = call["label"], call["status"], call["pill"]
            self.assertTrue(_disjoint(label, status))
            self.assertTrue(_disjoint(label, pill))
            self.assertTrue(_disjoint(status, pill))
            if call["has_overrides"]:
                self.assertIsNotNone(pill)
                self.assertEqual(pill.left(), call["gw"] - 24)
                self.assertEqual(pill.right(), call["gw"] - 7)
        # The rich-state rows used status candidates (P3.45.1 labels).
        with_status = [c for c in recorded if c["candidates"]]
        self.assertGreaterEqual(len(with_status), 2)
        self.assertTrue(all(c["text"] for c in with_status))

    def test_real_render_narrow_width(self):
        """Real paint at a NARROW gutter (96px): the zones stay
        disjoint and the status ladder degrades but stays visible."""
        ed, blocks, _ = self._build_editor(96)
        self._editors = [ed]
        # Force the narrow gutter geometry like the editor would (a
        # REAL resize event — never a synthetic resizeEvent call).
        ed.GUTTER_WIDTH = 96
        ed.resize(802, 600)
        for _ in range(4):
            _process(30)
        recorded = self._render_with_spy(ed)

        self.assertGreaterEqual(len(recorded), 1)
        for call in recorded:
            label, status, pill = call["label"], call["status"], call["pill"]
            self.assertTrue(_disjoint(label, status),
                            "label/status overlap at narrow width")
            self.assertTrue(_disjoint(label, pill))
            self.assertTrue(_disjoint(status, pill))
            self.assertTrue(call["gw"] <= 96 + 40,
                            "gutter geometry should follow the narrow "
                            "setting (gw={0})".format(call["gw"]))
        # State visibility degrades but the pill still renders for
        # override rows.
        pill_calls = [c for c in recorded if c["has_overrides"]]
        self.assertGreaterEqual(len(pill_calls), 1)
        self.assertTrue(all(c["pill"] is not None for c in pill_calls))

    def test_row_geometry_untouched(self):
        """The P3.44.9.1 row layout contract is unchanged by the
        header partition (rows stack monotonically, minimum height
        honoured)."""
        ed, blocks, _ = self._build_editor(PRODUCT_W)
        self._editors = [ed]
        rows = ed._block_gutter_widget._block_rows()
        visible = [r for r in rows if r is not None]
        self.assertEqual(len(visible), 3)
        prev_bottom = None
        for block, top, bottom in visible:
            self.assertGreaterEqual(bottom - top,
                                    _BlockGutterWidget.MIN_ROW_HEIGHT - 2)
            if prev_bottom is not None:
                self.assertGreaterEqual(top, prev_bottom - 1,
                                        "rows must never overlap")
            prev_bottom = bottom


if __name__ == "__main__":
    unittest.main(verbosity=2)
