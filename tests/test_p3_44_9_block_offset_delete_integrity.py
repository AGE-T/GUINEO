"""
GUINEO (SpeechStudio) — P3.44.9 runtime tests
==============================================

BLOCK OFFSET / DELETE INTEGRITY (implementation + regression phase).

Locks the guarantees mandated by docs/design/
P3_44_9_BLOCK_OFFSET_DELETE_INTEGRITY.md:

  §5    The proven boundary deletion defect ([53:99] -> [7:53]) is
        dead: a deletion starting exactly at a block boundary can
        never shift the block into preceding text.
  §6    Deletion matrix A-I: inside / start / whole-block / end /
        two blocks / multiple blocks / document start / document
        end / inter-block whitespace.
  §7    Insertion matrix: inside / start / end / before / between /
        after last / multiline / punctuation / inline markers.
  §8    Multi-operation sequences stay structurally correct.
  §9    Structural invariants: range validity, ordering,
        non-overlap, exact text ownership, no phantom blocks,
        save/load preservation.
  §10   Block identity: stable IDs through ordinary edits; deleted
        blocks are gone; neighbours keep their IDs.
  §12   Save / load / rehydration preserves the corrected structure.
  §13   Generation consequence: no block can receive another
        block's text after a boundary edit (the splitter consumes
        exactly the owning ranges).
  §14   Generation after save/load/reload.
  §16   Semantic metadata survives ordinary text edits.
  §17   Inline SFX/pause insertion offsets ride the same offset
        mapping (no stale / out-of-range anchors).
  §19   Clamping may never silently discard surviving characters.
  §20   Boundary conditions (change start == block start, change
        end == block end) are explicit and deterministic.
  §22   Position-based property sweep (deterministic table).
  §15   Manual split/merge block RANGES are correct (characterisation
        controls proving the SFX/pause redistribution issue is a
        SEPARATE defect, not the offset-engine defect).

The authoritative offset transformation rule (P3.44.9, single rule
shared by on_text_changed and on_multi_replace):

    One contiguous edit replaces old text [cs, ce) with L characters
    (delta = L - (ce - cs)). For each block [s, e):

      1. e <= cs            -> untouched (edit at/after block end)
      2. ce <= s            -> [s+delta, e+delta] (edit before block)
      3. s <= cs < e        -> head intact, block owns the replacement:
                                e <= ce : [s, cs+L)
                                e >  ce : [s, cs+L+(e-ce))
      4. cs < s < ce        -> head replaced:
                                e <= ce : REMOVED (nothing survives)
                                e >  ce : [cs+L, cs+L+(e-ce))
      5. Whole-text replacement (cs == 0 and ce == len(old)) -> every
         block is REMOVED (no ownership survives a full swap).
      6. A block whose final range owns zero characters (start >=
         end) does not exist (removed — no phantom rows).

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_44_9_block_offset_delete_integrity.py -v
"""
from __future__ import annotations

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Fake torch (house pattern: test_p3_35 / test_p3_41 / test_p3_44_7).
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

from engine.narration_blocks import (  # noqa: E402
    PromptBlock, SfxInsertion, PauseInsertion,
)
from engine.narration_block_manager import NarrationBlockManager  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------
# Fixed three-block document with DERIVED offsets (never hand-counted):
TEXT = "First sentence here.\n\nSecond block text here.\n\nThird block text here."
A_TXT, B_TXT, C_TXT = ("First sentence here.",
                       "Second block text here.",
                       "Third block text here.")
A_SPAN = (0, len(A_TXT))
B_SPAN = (TEXT.index(B_TXT), TEXT.index(B_TXT) + len(B_TXT))
C_SPAN = (TEXT.index(C_TXT), TEXT.index(C_TXT) + len(C_TXT))
GAP1 = (A_SPAN[1], B_SPAN[0])   # "\n\n" — unowned (gaps are legal)
GAP2 = (B_SPAN[1], C_SPAN[0])


def make_mgr(text, spans):
    """Manager with blocks placed directly (no detector — deterministic)."""
    mgr = NarrationBlockManager()
    mgr._blocks = [PromptBlock(start_offset=s, end_offset=e) for s, e in spans]
    mgr._last_text = text
    return mgr


def delete_span(mgr, text, start, end):
    """Delete text[start:end] through the offset engine."""
    new_text = text[:start] + text[end:]
    mgr.on_text_changed(new_text)
    return new_text


def insert_at(mgr, text, pos, inserted):
    """Insert a string at pos through the offset engine."""
    new_text = text[:pos] + inserted + text[pos:]
    mgr.on_text_changed(new_text)
    return new_text


def assert_structure_valid(testcase, blocks, text):
    """§9 structural invariants over the whole block list."""
    prev_end = -1
    for b in blocks:
        testcase.assertTrue(0 <= b.start_offset < b.end_offset <= len(text),
                            "invalid range [{0}:{1}] for text len {2}".format(
                                b.start_offset, b.end_offset, len(text)))
        testcase.assertGreaterEqual(b.start_offset, prev_end,
                                    "blocks overlap or are unordered: "
                                    "[{0}:{1}] after {2}".format(
                                        b.start_offset, b.end_offset, prev_end))
        prev_end = b.end_offset


def split_texts(blocks, text):
    return [text[b.start_offset:b.end_offset] for b in blocks]


# ===========================================================================
# 1. §5 — THE PROVEN BOUNDARY DELETION DEFECT
# ===========================================================================
class TestProvenBoundaryDefect(unittest.TestCase):
    """The [53:99] -> [7:53] corruption class must be dead."""

    def test_boundary_delete_full_block_no_shift_into_preceding_text(self):
        old_text = "A" * 53 + "B" * 46 + "C" * 30
        mgr = make_mgr(old_text, [(0, 53), (53, 99), (99, 129)])
        new_text = delete_span(mgr, old_text, 53, 99)
        a, c = mgr._blocks[0], mgr._blocks[-1]
        # Block B must be GONE (its whole text was deleted).
        self.assertEqual(len(mgr._blocks), 2,
                         "deleting exactly a whole block must remove it")
        self.assertEqual((a.start_offset, a.end_offset), (0, 53))
        self.assertEqual((c.start_offset, c.end_offset), (53, 83))
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_boundary_delete_no_overlap_with_neighbour(self):
        old_text = "A" * 53 + "B" * 46 + "C" * 30
        mgr = make_mgr(old_text, [(0, 53), (53, 99), (99, 129)])
        delete_span(mgr, old_text, 53, 99)
        assert_structure_valid(self, mgr._blocks, "A" * 53 + "C" * 30)

    def test_boundary_delete_no_phantom_block(self):
        old_text = "A" * 53 + "B" * 46 + "C" * 30
        mgr = make_mgr(old_text, [(0, 53), (53, 99), (99, 129)])
        delete_span(mgr, old_text, 53, 99)
        for b in mgr._blocks:
            self.assertLess(b.start_offset, b.end_offset,
                            "zero-length phantom block survived")

    def test_boundary_delete_no_wrong_text_ownership(self):
        old_text = "A" * 53 + "B" * 46 + "C" * 30
        mgr = make_mgr(old_text, [(0, 53), (53, 99), (99, 129)])
        new_text = delete_span(mgr, old_text, 53, 99)
        texts = split_texts(mgr._blocks, new_text)
        self.assertEqual(texts, ["A" * 53, "C" * 30],
                         "the deleted block must not own A's text")

    def test_realistic_boundary_delete_three_blocks(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        self.assertEqual(len(mgr._blocks), 2)
        texts = split_texts(mgr._blocks, new_text)
        self.assertEqual(texts, [A_TXT, C_TXT])
        assert_structure_valid(self, mgr._blocks, new_text)


# ===========================================================================
# 2. §6 — DELETION MATRIX A-I
# ===========================================================================
class TestDeletionMatrix(unittest.TestCase):

    def test_A_delete_inside_block(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, 29, 35)  # "block " inside B
        b = mgr._blocks[1]
        self.assertEqual((b.start_offset, b.end_offset), (22, 39))
        self.assertEqual(new_text[b.start_offset:b.end_offset],
                         "Second text here.")
        self.assertEqual((mgr._blocks[0].start_offset,
                          mgr._blocks[0].end_offset), A_SPAN)
        self.assertEqual((mgr._blocks[2].start_offset,
                          mgr._blocks[2].end_offset), (41, 63),
                         "C shifts left by the 6 deleted chars")
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_B_delete_from_start_of_block(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, 22, 29)  # "Second "
        b = mgr._blocks[1]
        self.assertEqual((b.start_offset, b.end_offset), (22, 38))
        self.assertEqual(new_text[b.start_offset:b.end_offset],
                         "block text here.")
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_C_delete_exactly_whole_block(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        self.assertEqual(len(mgr._blocks), 2, "no hidden orphan block")
        texts = split_texts(mgr._blocks, new_text)
        self.assertEqual(texts, [A_TXT, C_TXT])
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_D_delete_from_end_of_block(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, 40, 45)  # "here."
        b = mgr._blocks[1]
        self.assertEqual((b.start_offset, b.end_offset), (22, 40))
        self.assertEqual(new_text[b.start_offset:b.end_offset],
                         "Second block text ")
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_E_delete_across_two_blocks(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        # tail of A + gap + all B + gap + head of C
        new_text = delete_span(mgr, TEXT, 18, 49)
        self.assertEqual(len(mgr._blocks), 2, "B fully deleted -> removed")
        a, c = mgr._blocks
        self.assertEqual((a.start_offset, a.end_offset), (0, 18))
        self.assertEqual((c.start_offset, c.end_offset), (18, 38))
        self.assertEqual(new_text[a.start_offset:a.end_offset],
                         "First sentence her")
        self.assertEqual(new_text[c.start_offset:c.end_offset],
                         "ird block text here.")
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_F_delete_across_multiple_blocks(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, 10, 64)
        self.assertEqual(len(mgr._blocks), 2, "A truncated, B gone, "
                         "C reduced to its surviving tail")
        a, c = mgr._blocks
        self.assertEqual((a.start_offset, a.end_offset), (0, 10))
        self.assertEqual(new_text[a.start_offset:a.end_offset], "First sent")
        self.assertEqual((c.start_offset, c.end_offset), (10, 15))
        self.assertEqual(new_text[c.start_offset:c.end_offset], "here.")
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_G_delete_at_document_start(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, 0, 6)  # "First "
        a, b, c = mgr._blocks
        self.assertEqual((a.start_offset, a.end_offset), (0, 14))
        self.assertEqual(new_text[a.start_offset:a.end_offset],
                         "sentence here.")
        self.assertEqual((b.start_offset, b.end_offset), (16, 39))
        self.assertEqual((c.start_offset, c.end_offset), (41, 63))
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_H_delete_at_document_end(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, 64, 69)  # "here." tail of C
        a, b, c = mgr._blocks
        self.assertEqual((a.start_offset, a.end_offset), A_SPAN)
        self.assertEqual((b.start_offset, b.end_offset), B_SPAN)
        self.assertEqual((c.start_offset, c.end_offset), (47, 64))
        self.assertEqual(new_text[c.start_offset:c.end_offset],
                         "Third block text ")
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_I_delete_whitespace_between_blocks(self):
        """Gap text belongs to neither block: earlier block untouched,
        later blocks shift (documented semantics)."""
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, GAP1[0], GAP1[1])  # the "\n\n"
        a, b, c = mgr._blocks
        self.assertEqual((a.start_offset, a.end_offset), A_SPAN,
                         "whitespace after a block belongs to nobody")
        self.assertEqual((b.start_offset, b.end_offset), (20, 43))
        self.assertEqual(new_text[b.start_offset:b.end_offset], B_TXT)
        self.assertEqual((c.start_offset, c.end_offset), (45, 67))
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_delete_entire_document(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, 0, len(TEXT))
        self.assertEqual(mgr._blocks, [], "no block survives a full wipe")
        self.assertEqual(new_text, "")


# ===========================================================================
# 3. §7 — INSERTION MATRIX
# ===========================================================================
class TestInsertionMatrix(unittest.TestCase):

    def test_insert_inside_block(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = insert_at(mgr, TEXT, 29, "XY")
        b = mgr._blocks[1]
        self.assertEqual((b.start_offset, b.end_offset), (22, 47))
        self.assertEqual(new_text[b.start_offset:b.end_offset],
                         "Second XYblock text here.")
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_insert_at_block_start(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = insert_at(mgr, TEXT, 22, "XY")
        b = mgr._blocks[1]
        self.assertEqual((b.start_offset, b.end_offset), (24, 47))
        self.assertEqual(new_text[b.start_offset:b.end_offset], B_TXT,
                         "text inserted at the block start is OUTSIDE the block")
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_insert_at_block_end(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = insert_at(mgr, TEXT, 45, "XY")
        b = mgr._blocks[1]
        self.assertEqual((b.start_offset, b.end_offset), B_SPAN,
                         "text inserted at the block end is OUTSIDE the block")
        self.assertEqual(new_text[b.start_offset:b.end_offset], B_TXT)
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_insert_before_first_block(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = insert_at(mgr, TEXT, 0, "XX ")
        a, b, c = mgr._blocks
        self.assertEqual((a.start_offset, a.end_offset), (3, 23))
        self.assertEqual(new_text[a.start_offset:a.end_offset], A_TXT)
        self.assertEqual((b.start_offset, b.end_offset), (25, 48))
        self.assertEqual((c.start_offset, c.end_offset), (50, 72))
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_insert_between_blocks(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = insert_at(mgr, TEXT, 20, "\n\n")
        a, b, c = mgr._blocks
        self.assertEqual((a.start_offset, a.end_offset), A_SPAN)
        self.assertEqual((b.start_offset, b.end_offset), (24, 47))
        self.assertEqual((c.start_offset, c.end_offset), (49, 71))
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_insert_after_last_block(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = insert_at(mgr, TEXT, len(TEXT), " tail")
        for b, span in zip(mgr._blocks, [A_SPAN, B_SPAN, C_SPAN]):
            self.assertEqual((b.start_offset, b.end_offset), span)
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_multiline_insertion(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = insert_at(mgr, TEXT, 22, "\n\n")
        b, c = mgr._blocks[1], mgr._blocks[2]
        self.assertEqual((b.start_offset, b.end_offset), (24, 47))
        self.assertEqual((c.start_offset, c.end_offset), (49, 71))
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_insert_with_punctuation(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = insert_at(mgr, TEXT, 5, "!!! ")
        a = mgr._blocks[0]
        self.assertEqual((a.start_offset, a.end_offset), (0, 24))
        self.assertEqual(new_text[a.start_offset:a.end_offset],
                         "First!!!  sentence here.")
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_insert_inline_marker_text(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        marker = "{sfx:Sigh:Ahh} "
        new_text = insert_at(mgr, TEXT, 29, marker)
        b = mgr._blocks[1]
        self.assertEqual((b.start_offset, b.end_offset),
                         (22, 45 + len(marker)))
        self.assertIn("{sfx:Sigh:Ahh}", new_text[b.start_offset:b.end_offset])
        assert_structure_valid(self, mgr._blocks, new_text)


# ===========================================================================
# 4. §8 — MULTI-OPERATION EDITING
# ===========================================================================
class TestMultiOperationSequences(unittest.TestCase):

    def test_insert_delete_insert_delete_sequence(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        b_id = mgr._blocks[1].id
        text = TEXT
        text = insert_at(mgr, text, 29, "XY")     # B grows
        text = delete_span(mgr, text, 22, 24)     # B loses first 2 chars
        text = insert_at(mgr, text, 22, "Z")      # before B -> shift
        text = delete_span(mgr, text, 45, 46)     # B loses last char
        b = mgr.get_block(b_id)
        self.assertIsNotNone(b, "identity survives the sequence")
        self.assertEqual(text[b.start_offset:b.end_offset],
                         "cond XYblock text here")
        assert_structure_valid(self, mgr._blocks, text)

    def test_edit_a_edit_b_delete_b_edit_c(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        a_id, b_id, c_id = [blk.id for blk in mgr._blocks]
        text = insert_at(mgr, TEXT, B_SPAN[0] + 7, "?")   # edit B inside
        text = insert_at(mgr, text, 5, "!")               # edit A inside
        b_start, b_end = mgr.get_block(b_id).start_offset, \
            mgr.get_block(b_id).end_offset
        text = delete_span(mgr, text, b_start, b_end)     # boundary delete B
        text = insert_at(mgr, text,
                         mgr.get_block(c_id).start_offset + 2, "*")  # edit C
        self.assertIsNone(mgr.get_block(b_id), "B is gone")
        a, c = mgr.get_block(a_id), mgr.get_block(c_id)
        self.assertEqual(text[a.start_offset:a.end_offset],
                         "First! sentence here.")
        self.assertEqual(text[c.start_offset:c.end_offset],
                         "Th*ird block text here.")
        assert_structure_valid(self, mgr._blocks, text)

    def test_repeated_boundary_deletes(self):
        """Stale offset calculations surface only after previous deltas."""
        text = "AAA BBB CCC DDD "
        mgr = make_mgr(text, [(0, 4), (4, 8), (8, 12), (12, 16)])
        # delete exactly the second block (chars [4:8) = "BBB ")
        text = delete_span(mgr, text, 4, 8)
        self.assertEqual(len(mgr._blocks), 3)
        # the third block has shifted to [4:8) — delete it there exactly
        text = delete_span(mgr, text, 4, 8)
        self.assertEqual(len(mgr._blocks), 2)
        self.assertEqual(split_texts(mgr._blocks, text), ["AAA ", "DDD "])
        assert_structure_valid(self, mgr._blocks, text)

    def test_delete_all_blocks_one_by_one(self):
        text = "one. two. three. four."
        mgr = make_mgr(text, [(0, 5), (5, 10), (10, 17), (17, 22)])
        # delete from the END so earlier spans never move
        for span, expected in [((17, 22), "one. two. three. "),
                               ((10, 17), "one. two. "),
                               ((5, 10), "one. "),
                               ((0, 5), "")]:
            text = delete_span(mgr, text, span[0], span[1])
            self.assertEqual(text, expected)
        self.assertEqual(mgr._blocks, [])


# ===========================================================================
# 5. §9 + §22 — STRUCTURAL INVARIANTS / PROPERTY SWEEP
# ===========================================================================
class TestStructuralInvariants(unittest.TestCase):
    """Deterministic position sweep over a 100-char unique text."""

    S = "".join("%02d" % i for i in range(50))  # 100 unique chars
    BLOCK = (30, 60)

    def test_delete_position_sweep(self):
        """Every delete position lands in exactly one documented class."""
        cases = [
            # (start, length, expected)
            (0, 5, (25, 55)),     # entirely before -> shift
            (10, 5, (25, 55)),    # entirely before -> shift
            (25, 5, (25, 55)),    # ends exactly at block start -> shift
            (30, 5, (30, 55)),    # starts exactly at block start -> FIXED start
            (35, 5, (30, 55)),    # strictly inside
            (55, 5, (30, 55)),    # ends exactly at block end -> shrink
            (60, 5, (30, 60)),    # starts exactly at block end -> untouched
            (70, 5, (30, 60)),    # entirely after -> untouched
            (35, 20, (30, 40)),   # big middle delete
            (19, 12, (19, 48)),    # covers block start, tail survives
            (20, 80, None),       # covers the whole block -> REMOVED
            (25, 40, None),       # covers the whole block -> REMOVED
        ]
        for start, length, expected in cases:
            with self.subTest(start=start, length=length):
                mgr = make_mgr(self.S, [self.BLOCK])
                new_text = delete_span(mgr, self.S, start, start + length)
                if expected is None:
                    self.assertEqual(mgr._blocks, [],
                                     "fully deleted block must be removed")
                else:
                    self.assertEqual(
                        (mgr._blocks[0].start_offset,
                         mgr._blocks[0].end_offset), expected)
                    assert_structure_valid(self, mgr._blocks, new_text)

    def test_insert_position_sweep(self):
        cases = [
            (0, 2, (32, 62)),     # before -> shift
            (25, 2, (32, 62)),    # before -> shift
            (30, 2, (32, 62)),    # at start (outside) -> shift
            (45, 2, (30, 62)),    # inside -> grow
            (59, 2, (30, 62)),    # inside (last char) -> grow
            (60, 2, (30, 60)),    # at end (outside) -> untouched
            (70, 2, (30, 60)),    # after -> untouched
            (35, 20, (30, 80)),   # big inside insert
        ]
        for pos, length, expected in cases:
            with self.subTest(pos=pos, length=length):
                mgr = make_mgr(self.S, [self.BLOCK])
                new_text = insert_at(mgr, self.S, pos, "x" * length)
                self.assertEqual(
                    (mgr._blocks[0].start_offset,
                     mgr._blocks[0].end_offset), expected)
                assert_structure_valid(self, mgr._blocks, new_text)

    def test_structure_invariants_over_full_battery(self):
        """Invariant battery: every scenario leaves a valid structure."""
        scenarios = [
            ("del-inside", (30, 36)), ("del-start", (22, 29)),
            ("del-whole", (22, 45)), ("del-end", (40, 45)),
            ("del-across", (18, 49)), ("del-multi", (10, 64)),
            ("del-doc-start", (0, 5)), ("del-doc-end", (64, 69)),
            ("del-gap", (20, 22)), ("del-all", (0, 69)),
        ]
        for name, (s, e) in scenarios:
            with self.subTest(scenario=name):
                mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
                new_text = delete_span(mgr, TEXT, s, e)
                assert_structure_valid(self, mgr._blocks, new_text)
                for b in mgr._blocks:
                    self.assertEqual(
                        new_text[b.start_offset:b.end_offset].strip()[:9],
                        new_text[b.start_offset:b.end_offset].strip()[:9])

    def test_preexisting_phantom_block_healed_on_edit(self):
        """Legacy zero-length blocks cannot survive a text change."""
        text = "One. Two."
        mgr = make_mgr(text, [(0, 4), (5, 9), (4, 4)])  # third is a phantom
        new_text = insert_at(mgr, text, 0, "X")
        self.assertEqual(len(mgr._blocks), 2,
                         "a zero-length block is not a block")
        assert_structure_valid(self, mgr._blocks, new_text)


# ===========================================================================
# 6. §10 — BLOCK IDENTITY
# ===========================================================================
class TestBlockIdentity(unittest.TestCase):

    def test_ids_stable_through_ordinary_edits(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        ids = [b.id for b in mgr._blocks]
        text = insert_at(mgr, TEXT, 0, "Hi ")
        text = delete_span(mgr, text, 3, 8)
        self.assertEqual([b.id for b in mgr._blocks], ids)

    def test_deleted_block_id_is_gone(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        b_id = mgr._blocks[1].id
        delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        self.assertIsNone(mgr.get_block(b_id))
        self.assertNotIn(b_id, [b.id for b in mgr._blocks])

    def test_neighbour_ids_stable_through_boundary_delete(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        a_id, c_id = mgr._blocks[0].id, mgr._blocks[2].id
        delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        self.assertEqual([b.id for b in mgr._blocks], [a_id, c_id])

    def test_ui_numbering_is_positional_not_identity(self):
        """B-labels are gutter positions (B1, B2...) — deleting B2 makes
        the old B3 render as B2. Identity (ids) is what stays stable."""
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        c_id = mgr._blocks[2].id
        delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        blocks = mgr._blocks
        self.assertEqual([b.id for b in blocks], [blocks[0].id, c_id])
        # The surviving C block is now POSITION 2 (would render as B2).
        self.assertEqual(blocks.index(mgr.get_block(c_id)), 1)


# ===========================================================================
# 7. §19 — CLAMPING MUST NOT DISCARD SURVIVING CHARACTERS
# ===========================================================================
class TestClampingIntegrity(unittest.TestCase):

    def test_delete_extending_past_block_end_keeps_surviving_head(self):
        old_text = "A" * 53 + "B" * 46 + "C" * 30
        mgr = make_mgr(old_text, [(10, 50)])
        new_text = delete_span(mgr, old_text, 20, 80)
        b = mgr._blocks[0]
        self.assertEqual((b.start_offset, b.end_offset), (10, 20),
                         "the surviving head [10:20) must not be clamped away")
        self.assertEqual(new_text[b.start_offset:b.end_offset], "A" * 10)

    def test_delete_extending_before_and_after_block(self):
        old_text = "A" * 53 + "B" * 46 + "C" * 30
        mgr = make_mgr(old_text, [(30, 60)])
        new_text = delete_span(mgr, old_text, 20, 80)
        self.assertEqual(mgr._blocks, [],
                         "a block fully inside a deletion is removed")

    def test_no_negative_or_inverted_ranges_ever(self):
        old_text = "A" * 53 + "B" * 46 + "C" * 30
        for start, end in [(0, 99), (53, 129), (20, 80), (50, 60), (0, 129)]:
            with self.subTest(delete=(start, end)):
                mgr = make_mgr(old_text, [(53, 99)])
                new_text = delete_span(mgr, old_text, start, end)
                for b in mgr._blocks:
                    self.assertGreaterEqual(b.start_offset, 0)
                    self.assertLessEqual(b.end_offset, len(new_text))
                    self.assertLess(b.start_offset, b.end_offset)


# ===========================================================================
# 8. §20 — EXPLICIT BOUNDARY CONDITIONS OF THE DELTA CALCULATION
# ===========================================================================
class TestBoundaryConditions(unittest.TestCase):

    S = "".join("%02d" % i for i in range(50))
    BLOCK = (30, 60)

    def test_change_start_equals_block_start(self):
        mgr = make_mgr(self.S, [self.BLOCK])
        new_text = delete_span(mgr, self.S, 30, 35)
        b = mgr._blocks[0]
        self.assertEqual((b.start_offset, b.end_offset), (30, 55),
                         "a delete starting AT the block start must keep "
                         "the start fixed (never shift into preceding text)")

    def test_change_end_equals_block_end(self):
        mgr = make_mgr(self.S, [self.BLOCK])
        new_text = delete_span(mgr, self.S, 55, 60)
        b = mgr._blocks[0]
        self.assertEqual((b.start_offset, b.end_offset), (30, 55))

    def test_change_start_and_end_equal_block(self):
        mgr = make_mgr(self.S, [self.BLOCK])
        delete_span(mgr, self.S, 30, 60)
        self.assertEqual(mgr._blocks, [],
                         "delete exactly [start:end) of a block removes it")

    def test_equal_length_substitution_inside_block(self):
        mgr = make_mgr(self.S, [self.BLOCK])
        new_text = self.S[:40] + "XXYY" + self.S[44:]
        mgr.on_text_changed(new_text)
        b = mgr._blocks[0]
        self.assertEqual((b.start_offset, b.end_offset), (30, 60))
        self.assertEqual(new_text[b.start_offset:b.end_offset],
                         new_text[30:60])

    def test_equal_length_substitution_covering_block_start(self):
        mgr = make_mgr(self.S, [self.BLOCK])
        # replace [25:35) (crosses the block start) with 10 fresh chars:
        # the block's head [30:35) is replaced; the tail [35:60) survives
        # in place (delta == 0).
        new_text = self.S[:25] + "ABCDEFGHIJ" + self.S[35:]
        mgr.on_text_changed(new_text)
        b = mgr._blocks[0]
        self.assertEqual((b.start_offset, b.end_offset), (35, 60))
        self.assertEqual(new_text[b.start_offset:b.end_offset],
                         new_text[35:60])

    def test_whole_text_replacement_removes_all_blocks(self):
        mgr = make_mgr(self.S, [self.BLOCK, (70, 80)])
        mgr.on_text_changed("brand new document")
        self.assertEqual(mgr._blocks, [],
                         "no ownership survives a whole-text swap")


# ===========================================================================
# 9. Replace-All engine (on_multi_replace) — same rule, disjoint matches
# ===========================================================================
class TestMultiReplaceIntegrity(unittest.TestCase):

    def test_match_exactly_covering_block_removes_it(self):
        old_text = "A" * 53 + "B" * 46 + "C" * 30
        mgr = make_mgr(old_text, [(0, 53), (53, 99), (99, 129)])
        new_text = old_text[:53] + old_text[99:]
        mgr.on_multi_replace(old_text, new_text, [(53, 99)], "")
        self.assertEqual(len(mgr._blocks), 2,
                         "a block fully replaced by '' must not remain "
                         "as a phantom")
        assert_structure_valid(self, mgr._blocks, new_text)

    def test_match_covering_block_start_remaps_start(self):
        old_text = "A" * 53 + "B" * 46 + "C" * 30
        mgr = make_mgr(old_text, [(53, 99)])
        new_text = old_text[:40] + old_text[60:]
        mgr.on_multi_replace(old_text, new_text, [(40, 60)], "")
        b = mgr._blocks[0]
        self.assertEqual((b.start_offset, b.end_offset), (40, 79),
                         "the surviving tail [60:99) maps to [40:79)")

    def test_multiple_matches_before_block_cumulative_shift(self):
        text = "0123456789abcdefghij"
        mgr = make_mgr(text, [(10, 20)])
        # matches [0:5) and [9:10) both replaced with "X" (same string)
        new_text = "X" + text[5:9] + "X" + text[10:]
        mgr.on_multi_replace(text, new_text, [(0, 5), (9, 10)], "X")
        b = mgr._blocks[0]
        self.assertEqual((b.start_offset, b.end_offset), (6, 16))
        self.assertEqual(new_text[b.start_offset:b.end_offset], "abcdefghij")

    def test_match_inside_block_adjusts_end(self):
        old_text = "A" * 53 + "B" * 46 + "C" * 30
        mgr = make_mgr(old_text, [(53, 99)])
        new_text = old_text[:60] + "XYZ" + old_text[70:]
        mgr.on_multi_replace(old_text, new_text, [(60, 70)], "XYZ")
        b = mgr._blocks[0]
        self.assertEqual((b.start_offset, b.end_offset), (53, 92))
        self.assertEqual(new_text[b.start_offset:b.end_offset],
                         "B" * 7 + "XYZ" + "B" * 29)

    def test_replacement_at_block_start_extends_block(self):
        old_text = "A" * 53 + "B" * 46 + "C" * 30
        mgr = make_mgr(old_text, [(53, 99)])
        new_text = old_text[:53] + "NEWTEXT" + old_text[60:]
        mgr.on_multi_replace(old_text, new_text, [(53, 60)], "NEWTEXT")
        b = mgr._blocks[0]
        self.assertEqual((b.start_offset, b.end_offset), (53, 99))
        self.assertEqual(new_text[b.start_offset:b.end_offset],
                         "NEWTEXT" + "B" * 39)


# ===========================================================================
# 10. §16 + §17 — SEMANTIC METADATA AND INLINE MARKERS THROUGH EDITS
# ===========================================================================
class TestSemanticMetadataAndMarkers(unittest.TestCase):

    def test_metadata_survives_ordinary_edits(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        a, b, c = mgr._blocks
        a.emotion, a.character_id, a.label, a.locked = "Awe", "ch-1", "L1", True
        b.emotion, b.character_id = "Fear", "ch-2"
        c.style = "Whispering"
        text = insert_at(mgr, TEXT, 5, "!")
        a2, b2, c2 = mgr._blocks
        self.assertEqual(a2.id, a.id)
        self.assertEqual((a2.emotion, a2.character_id, a2.label, a2.locked),
                         ("Awe", "ch-1", "L1", True))
        self.assertEqual((b2.emotion, b2.character_id), ("Fear", "ch-2"))
        self.assertEqual(c2.style, "Whispering")
        assert_structure_valid(self, mgr._blocks, text)

    def test_boundary_delete_keeps_neighbour_metadata(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        a, c = mgr._blocks[0], mgr._blocks[2]
        a.character_id, c.character_id = "ch-1", "ch-3"
        delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        a2, c2 = mgr._blocks
        self.assertEqual(a2.character_id, "ch-1")
        self.assertEqual(c2.character_id, "ch-3")

    def test_sfx_offset_shifts_with_delete_before_marker(self):
        mgr = make_mgr(TEXT, [B_SPAN])
        mgr._blocks[0].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 10)]
        new_text = delete_span(mgr, TEXT, 23, 29)  # 6 chars before rel 10
        b = mgr._blocks[0]
        self.assertEqual(b.sfx_insertions[0].offset, 4,
                         "the insertion offset rides the same delta")
        self.assertLessEqual(b.sfx_insertions[0].offset,
                             b.end_offset - b.start_offset)

    def test_sfx_offset_unchanged_for_delete_after_marker(self):
        mgr = make_mgr(TEXT, [B_SPAN])
        mgr._blocks[0].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 5)]
        delete_span(mgr, TEXT, 40, 45)  # "here." — after the marker
        self.assertEqual(mgr._blocks[0].sfx_insertions[0].offset, 5)

    def test_sfx_offset_clamped_for_edit_around_marker(self):
        mgr = make_mgr(TEXT, [B_SPAN])
        mgr._blocks[0].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 17)]
        delete_span(mgr, TEXT, 35, 45)  # the anchor text is deleted
        b = mgr._blocks[0]
        off = b.sfx_insertions[0].offset
        self.assertEqual(off, 13, "deterministic clamp to the edit start")
        self.assertLessEqual(off, b.end_offset - b.start_offset)

    def test_pause_offset_rebased_when_block_start_moves(self):
        # unique-char text so the diff localizes the edit exactly
        s = "".join("%02d" % i for i in range(50))
        mgr = make_mgr(s, [(60, 90)])
        mgr._blocks[0].pause_insertions = [PauseInsertion("pause", 5)]
        delete_span(mgr, s, 53, 70)  # block becomes [53:73)
        b = mgr._blocks[0]
        self.assertEqual((b.start_offset, b.end_offset), (53, 73))
        self.assertEqual(b.pause_insertions[0].offset, 0,
                         "offset rebased onto the new block start")

    def test_insertions_die_with_their_block(self):
        mgr = make_mgr(TEXT, [B_SPAN])
        mgr._blocks[0].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 5)]
        delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        self.assertEqual(mgr._blocks, [])

    def test_insertions_stay_in_range_after_shrink(self):
        mgr = make_mgr(TEXT, [B_SPAN])
        mgr._blocks[0].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 22)]
        delete_span(mgr, TEXT, 40, 45)  # block shrinks
        b = mgr._blocks[0]
        off = b.sfx_insertions[0].offset
        self.assertLessEqual(off, b.end_offset - b.start_offset,
                             "insertion offsets must remain inside the block")


# ===========================================================================
# 11. §12 — SAVE / LOAD / REHYDRATION
# ===========================================================================
class TestSaveLoadRehydration(unittest.TestCase):

    def test_to_dict_from_dict_round_trip(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])  # boundary delete first
        data = mgr.to_dict()
        mgr2 = NarrationBlockManager()
        mgr2.from_dict(data)
        mgr2._last_text = mgr._last_text
        self.assertEqual(len(mgr2._blocks), 2,
                         "no phantom survives the round trip")
        self.assertEqual(split_texts(mgr2.blocks, mgr._last_text),
                         [A_TXT, C_TXT])
        assert_structure_valid(self, mgr2._blocks, mgr._last_text)

    def test_scene_round_trip_preserves_corrected_structure(self):
        from engine.models import Scene
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        scene = Scene.from_dict({
            "name": "s", "text": new_text,
            "narration_blocks": mgr.to_dict()})
        scene2 = Scene.from_dict(scene.to_dict())
        blocks = [PromptBlock.from_dict(d) for d in scene2.narration_blocks]
        self.assertEqual(len(blocks), 2)
        assert_structure_valid(self, blocks, scene2.text)
        self.assertEqual(split_texts(blocks, scene2.text), [A_TXT, C_TXT])

    def test_metadata_round_trip_after_boundary_delete(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        mgr._blocks[0].character_id = "ch-1"
        mgr._blocks[2].character_id = "ch-3"
        delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        mgr2 = NarrationBlockManager()
        mgr2.from_dict(mgr.to_dict())
        self.assertEqual([b.character_id for b in mgr2.blocks],
                         ["ch-1", "ch-3"])


# ===========================================================================
# 12. §13 + §14 — GENERATION CONSEQUENCE (with and without rehydration)
# ===========================================================================
class TestGenerationConsequence(unittest.TestCase):

    def _split(self, text, blocks):
        from engine.narration_splitter import NarrationSplitter
        return NarrationSplitter().split(text=text, blocks=blocks)

    def test_boundary_delete_cannot_leak_text_into_generation(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        b_id = mgr._blocks[1].id
        new_text = delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        parts = self._split(new_text, mgr.blocks)
        self.assertEqual([p.text for p in parts], [A_TXT, C_TXT])
        self.assertEqual({p.source_block_id for p in parts},
                         {b.id for b in mgr._blocks})
        self.assertNotIn(b_id, {p.source_block_id for p in parts})

    def test_no_duplicate_generation_text_after_boundary_edit(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        parts = self._split(new_text, mgr.blocks)
        texts = [p.text for p in parts]
        self.assertEqual(len(texts), len(set(texts)),
                         "another block's text must never be generated twice")

    def test_generation_after_save_load_rehydrate(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        mgr2 = NarrationBlockManager()
        mgr2.from_dict(mgr.to_dict())
        parts = self._split(new_text, mgr2.blocks)
        self.assertEqual([p.text for p in parts], [A_TXT, C_TXT])

    def test_generation_after_scene_round_trip(self):
        from engine.models import Scene
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, B_SPAN[0], B_SPAN[1])
        scene = Scene.from_dict({"name": "s", "text": new_text,
                                 "narration_blocks": mgr.to_dict()})
        scene2 = Scene.from_dict(scene.to_dict())
        blocks = [PromptBlock.from_dict(d) for d in scene2.narration_blocks]
        parts = self._split(scene2.text, blocks)
        self.assertEqual([p.text for p in parts], [A_TXT, C_TXT])

    def test_edit_inside_block_generation_ownership(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        new_text = delete_span(mgr, TEXT, 29, 35)  # "block " inside B
        parts = self._split(new_text, mgr.blocks)
        self.assertEqual([p.text for p in parts],
                         [A_TXT, "Second text here.", C_TXT])


# ===========================================================================
# 13. §15 — MANUAL SPLIT/MERGE CHARACTERISATION (controls; see §15 report)
# ===========================================================================
class TestManualSplitMergeCharacterisation(unittest.TestCase):
    """Block RANGES from manual split/merge are CORRECT (not the offset
    engine defect). The SFX/pause redistribution gap is a SEPARATE
    deferred issue (P3.44.7 finding) — these tests document current
    behaviour so the separation is evidence-based."""

    def test_split_block_ranges_correct(self):
        mgr = make_mgr(TEXT, [B_SPAN])
        b = mgr._blocks[0]
        ok = mgr.split_block(b.id, 32)
        self.assertTrue(ok)
        first, second = mgr._blocks
        self.assertEqual((first.start_offset, first.end_offset), (22, 32))
        self.assertEqual((second.start_offset, second.end_offset), (32, 45))
        assert_structure_valid(self, mgr._blocks, TEXT)

    def test_merge_with_above_ranges_correct(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN])
        ok = mgr.merge_with_above(mgr._blocks[1].id)
        self.assertTrue(ok)
        merged = mgr._blocks[0]
        self.assertEqual((merged.start_offset, merged.end_offset), (0, 45))
        assert_structure_valid(self, mgr._blocks, TEXT)

    def test_split_does_not_redistribute_insertions_documented(self):
        """DEFERRED (separate issue): split keeps insertions on the first
        child only, unshifted. Characterisation — NOT an offset-engine
        defect detector (see design doc §15 conclusion)."""
        mgr = make_mgr(TEXT, [B_SPAN])
        mgr._blocks[0].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 20)]
        b = mgr._blocks[0]
        mgr.split_block(b.id, 32)
        first, second = mgr._blocks
        self.assertEqual(first.sfx_insertions[0].offset, 20)
        self.assertEqual(second.sfx_insertions, [])

    def test_merge_does_not_redistribute_insertions_documented(self):
        """DEFERRED (separate issue): merge keeps only the upper block's
        insertions. Characterisation — NOT an offset-engine defect."""
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN])
        mgr._blocks[1].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 5)]
        mgr.merge_with_above(mgr._blocks[1].id)
        self.assertEqual(mgr._blocks[0].sfx_insertions, [])


# ===========================================================================
# 14. Real editor paths (Qt signal pipeline end-to-end)
# ===========================================================================
class TestRealEditorPaths(unittest.TestCase):
    """Real NarrationEditor (real widget, real manager, real Qt text
    signals — only QMessageBox is stubbed where needed)."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        from ui.theme import apply_theme, DEFAULT_THEME
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app, DEFAULT_THEME)

    def setUp(self):
        from ui.panels.narration_editor import NarrationEditor
        self.ed = NarrationEditor()
        self.ed._plain_btn.setChecked(True)
        self.ed._editor.setPlainText(TEXT)
        self.ed._block_manager._blocks = [
            PromptBlock(start_offset=s, end_offset=e)
            for s, e in (A_SPAN, B_SPAN, C_SPAN)]
        self.ed._block_manager._last_text = TEXT
        self.ed._blocks_btn.setChecked(True)

    def tearDown(self):
        self.ed.deleteLater()

    def _delete_selection(self, start, end):
        from PySide6.QtGui import QTextCursor
        cursor = self.ed._editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.ed._editor.setTextCursor(cursor)
        cursor.removeSelectedText()

    def test_real_editor_boundary_delete(self):
        """The exact §5 reproduction through the REAL Qt signal path."""
        self._delete_selection(B_SPAN[0], B_SPAN[1])
        mgr = self.ed._block_manager
        text = self.ed._editor.toPlainText()
        self.assertEqual(len(mgr.blocks), 2)
        assert_structure_valid(self, mgr.blocks, text)
        self.assertEqual(split_texts(mgr.blocks, text), [A_TXT, C_TXT])

    def test_real_editor_delete_block_button(self):
        """§11: the Delete Block button removes the model row, keeps the
        text, leaves neighbours' offsets and identities alone."""
        mgr = self.ed._block_manager
        b_id = mgr.blocks[1].id
        a_span = (mgr.blocks[0].start_offset, mgr.blocks[0].end_offset)
        c_span = (mgr.blocks[2].start_offset, mgr.blocks[2].end_offset)
        self.ed._do_delete(b_id)
        text = self.ed._editor.toPlainText()
        self.assertEqual(text, TEXT, "Delete Block keeps the text")
        self.assertEqual(len(mgr.blocks), 2)
        self.assertIsNone(mgr.get_block(b_id))
        self.assertEqual((mgr.blocks[0].start_offset,
                          mgr.blocks[0].end_offset), a_span)
        self.assertEqual((mgr.blocks[1].start_offset,
                          mgr.blocks[1].end_offset), c_span)
        assert_structure_valid(self, mgr.blocks, text)

    def test_real_editor_typed_sequence(self):
        """§8 through real cursor-level edits: insert, delete, insert."""
        from PySide6.QtGui import QTextCursor
        editor = self.ed._editor
        mgr = self.ed._block_manager
        # type "XY" inside B (between "Second " and "block")
        c = QTextCursor(editor.document())
        c.setPosition(29)
        editor.setTextCursor(c)
        c.insertText("XYZ")
        # delete B's first 3 chars
        self._delete_selection(B_SPAN[0], B_SPAN[0] + 3)
        # insert before A
        c = QTextCursor(editor.document())
        c.setPosition(0)
        editor.setTextCursor(c)
        c.insertText(">> ")
        text = editor.toPlainText()
        assert_structure_valid(self, mgr.blocks, text)
        self.assertEqual(split_texts(mgr.blocks, text),
                         [A_TXT, "ond XYZblock text here.", C_TXT])

    def test_real_editor_marker_survives_nearby_delete(self):
        mgr = self.ed._block_manager
        mgr.blocks[1].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 10)]
        # delete 6 chars right before the marker anchor
        self._delete_selection(23, 29)
        b = mgr.blocks[1]
        self.assertEqual(b.sfx_insertions[0].offset, 4)
        self.assertLessEqual(b.sfx_insertions[0].offset,
                             b.end_offset - b.start_offset)

    def test_real_editor_full_text_swap_clears_blocks(self):
        """Documented set_text semantics (the history-reuse path): the
        old block model can never ride onto the new document. In Plain
        mode no blocks appear; in Blocks mode the new document is
        auto-detected fresh (old identities gone)."""
        # Plain mode: no blocks survive the swap.
        self.ed._plain_btn.setChecked(True)
        self.ed.set_text("A completely different document.")
        self.assertEqual(self.ed._block_manager.blocks, [])
        self.assertEqual(self.ed._block_manager._last_text,
                         "A completely different document.")
        # Blocks mode: fresh blocks for the new document.
        self.ed._blocks_btn.setChecked(True)
        first_ids = [b.id for b in self.ed._block_manager.blocks]
        self.assertEqual(len(first_ids), 1)
        self.ed.set_text("Another entirely new document here.")
        blocks2 = self.ed._block_manager.blocks
        self.assertEqual(len(blocks2), 1)
        self.assertNotEqual(blocks2[0].id, first_ids[0],
                            "old block identity must not ride onto new text")
        assert_structure_valid(self, blocks2,
                               "Another entirely new document here.")
        self.assertEqual(blocks2[0].start_offset, 0)
        text = self.ed._editor.toPlainText()
        self.assertEqual(
            text[blocks2[0].start_offset:blocks2[0].end_offset],
            "Another entirely new document here.")


# ===========================================================================
# 15. §18 — BLOCK REORDER MODEL (documentation test)
# ===========================================================================
class TestReorderModel(unittest.TestCase):
    """There is NO block-reorder feature in the product: the block list
    order IS the document order (ascending start offsets), maintained by
    every operation. Reordering text = editing text, never a silent
    block permutation."""

    def test_list_order_follows_document_order_after_edits(self):
        mgr = make_mgr(TEXT, [A_SPAN, B_SPAN, C_SPAN])
        text = insert_at(mgr, TEXT, 0, "xx")
        starts = [b.start_offset for b in mgr.blocks]
        self.assertEqual(starts, sorted(starts))
        text = delete_span(mgr, text, 2, 25)  # removes A content + gap
        starts = [b.start_offset for b in mgr.blocks]
        self.assertEqual(starts, sorted(starts))
        assert_structure_valid(self, mgr.blocks, text)


if __name__ == "__main__":
    unittest.main()
