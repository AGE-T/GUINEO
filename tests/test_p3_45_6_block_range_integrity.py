"""GUINEO P3.45.6 — PHASE 2: NARRATION BLOCK RANGE INTEGRITY.

Regression suite for the reported data-integrity symptoms:

  * a complete sentence followed by a second Part containing a
    TRAILING SUBSTRING of that sentence;
  * text beginning/ending mid-word;
  * old-looking fragments remaining after large text deletions.

ROOT CAUSE (reproduced deterministically below): the edit
reconstruction in ``NarrationBlockManager.on_text_changed`` infers the
changed segment by common-prefix/common-suffix comparison, which
attributes a change to the EARLIEST position consistent with the two
strings. When a user deletes ONE of two adjacent identical passages,
the diff attributes the deletion to the SECOND occurrence: the block
covering the DELETED first occurrence is left "untouched" (its range
now aliases the SURVIVING text) while the second block is SHIFTED ONTO
THE SAME REGION — the two ranges OVERLAP and Generate Long slices the
same text twice (part 2 = trailing substring of part 1). The same
misattribution produces mid-word boundaries.

FIX (P3.45.6 Phase 2, three coordinated pieces):

  1. ``NarrationBlockManager.on_document_edit(position, removed,
     added, new_text)`` — exact-position edit tracking fed by
     ``QTextDocument.contentsChange`` (probe-verified to carry exact
     coordinates for every relevant edit type and to fire BEFORE
     ``textChanged``), transforming through the SAME P3.44.9
     authoritative ``_apply_edit_to_blocks`` — no second edit engine.
     ``on_text_changed`` remains as the fallback/contract entry point.
  2. ``NarrationEditor._on_contents_change`` — wires the exact feed
     with the P3.26 scene-load guard and a suppression flag around
     the pre-applied Replace All document swap.
  3. ``validate_block_ranges`` + the Generate Long PRECONDITION gate —
     every active Block range is validated against the CURRENT source
     text BEFORE any Part/slot/job is constructed; invalid ranges
     fail CLOSED with full diagnostics and the existing recovery
     surface (Re-detect Blocks), never silently clamped.

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_45_6_block_range_integrity.py -v
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
from PySide6.QtGui import QTextCursor

_app = QApplication.instance() or QApplication([])

from engine.narration_blocks import (
    PromptBlock, SfxInsertion, PauseInsertion,
)
from engine.narration_block_manager import (
    NarrationBlockManager, validate_block_ranges,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers (positions DERIVED, never hand-counted — house rule)
# ---------------------------------------------------------------------------
S = "The rain fell quietly over the hills."


def make_mgr(text, spans, with_semantics=False):
    """Manager with blocks placed directly (no detector — deterministic)."""
    mgr = NarrationBlockManager()
    blocks = []
    for i, (s, e) in enumerate(spans):
        b = PromptBlock(start_offset=s, end_offset=e)
        if with_semantics:
            b.label = "L{0}".format(i)
            b.character_id = "char-{0}".format(i)
            b.locked = (i == 0)
            b.emotion = "calm"
            b.sfx_insertions = [SfxInsertion("wind", "whoosh",
                                              max(0, min(3, e - s - 1)))]
            b.pause_insertions = [PauseInsertion("medium", 1)]
        blocks.append(b)
    mgr._blocks = blocks
    mgr._last_text = text
    return mgr


def ranges(mgr):
    return [(b.start_offset, b.end_offset) for b in mgr._blocks]


# ===========================================================================
# §A — Exact-position feed vs. the ambiguous prefix/suffix inference
# ===========================================================================
class TestExactVsAmbiguous(unittest.TestCase):
    """The reproduced defect and its fix, at the manager level."""

    def _adjacent_repeats(self):
        """Doc = S + S (two ADJACENT identical sentences).

        Blocks: A covers the whole FIRST copy; B covers a TRAILING
        SUBSTRING of the second copy. The user deletes the FIRST copy
        (selection [0, len(S))).
        """
        text = S + S
        a = (0, len(S))
        b_start = len(S) + S.index("quietly")
        b = (b_start, 2 * len(S))
        return text, a, b

    def test_reproduced_wrong_block_symmetry_via_ambiguous_diff(self):
        """CHARACTERISATION of the defect (the pre-fix production path).

        Deleting the FIRST of two ADJACENT identical sentences: the
        prefix/suffix diff attributes the deletion to the SECOND copy
        (new == the first copy matches the whole prefix). The block
        covering the DELETED first copy (A) is left untouched — it now
        ALIASES the surviving text with ITS semantics — while block B,
        whose text SURVIVED as the trailing substring, is REMOVED with
        all its metadata (label/Character/overrides). Wrong block
        survives, wrong block dies (the "Block behaving strangely
        after some of its lines were removed" symptom).
        """
        text, a_span, b_span = self._adjacent_repeats()
        mgr = make_mgr(text, [a_span, b_span])
        new_text = text[len(S):]          # delete the FIRST copy
        mgr.on_text_changed(new_text)      # the OLD (diff) path

        r = ranges(mgr)
        self.assertEqual(r, [a_span],
                         "the old path keeps the DELETED copy's block "
                         "(aliased onto the survivor) and destroys the "
                         "SURVIVING copy's block")
        self.assertEqual(len(mgr.blocks), 1)
        self.assertEqual(new_text[mgr.blocks[0].start_offset:
                                  mgr.blocks[0].end_offset], new_text)

    def test_exact_path_no_overlap_same_edit(self):
        """THE FIX: the exact-position feed keeps ownership exact.

        contentsChange carries (0, len(S), 0) — the deletion of the
        FIRST copy at its true position: A (whose text is deleted)
        collapses to a zero-width range and is REMOVED; B is mapped
        onto the corresponding trailing substring of the SURVIVING
        copy with its metadata intact. No overlap; every remaining
        block owns exactly its surviving characters.
        """
        text, a_span, b_span = self._adjacent_repeats()
        mgr = make_mgr(text, [a_span, b_span])
        new_text = text[len(S):]
        mgr.on_document_edit(0, len(S), 0, new_text)  # exact coordinates

        self.assertEqual(ranges(mgr), [
            (S.index("quietly"), len(S))],
            "A must be removed (deleted text); B must own the trailing "
            "substring of the surviving copy")
        self.assertEqual(validate_block_ranges(mgr.blocks, new_text), [])

    def test_reproduced_multi_edit_coalescing_bleeds_text(self):
        """CHARACTERISATION — the strongest reported symptom.

        Two disjoint deletions delivered through ONE textChanged
        emission (undo of a compound command, programmatic multi-edit):
        the diff coalesces them into ONE spanning edit. The MIDDLE
        block — whose text survived BOTH deletions untouched — ends up
        owning nearly the whole document (its Part then contains other
        blocks' sentences), and the LAST block collapses to a
        one-character trailing fragment ("a second Part containing a
        trailing substring of that sentence").
        """
        old = "AAAA-first BBBB-middle CCCC-last"
        spans = [(0, 10), (11, 22), (23, len(old))]
        e1s, e1e = 4, 9      # delete "A-fir"
        e2s, e2e = 26, 31    # delete "C-las"
        new = old[:e1s] + old[e1e:e2s] + old[e2e:]

        mgr = make_mgr(old, spans)
        mgr.on_text_changed(new)   # ONE coalesced diff

        r = ranges(mgr)
        slices = [new[s:e] for s, e in r]
        self.assertEqual(slices[0], "AAAAt BBBB-middle CCC",
                         "the middle block bled into its neighbours' "
                         "text (one Part containing other blocks' "
                         "sentences)")
        self.assertEqual(slices[-1], "t",
                         "the last block collapsed to a trailing "
                         "substring fragment")

    def test_exact_fragments_same_multi_edit(self):
        """THE FIX for the coalescing family: contentsChange delivers
        each fragment edit with exact coordinates — every block keeps
        exactly its surviving text."""
        old = "AAAA-first BBBB-middle CCCC-last"
        spans = [(0, 10), (11, 22), (23, len(old))]
        e1s, e1e = 4, 9
        e2s, e2e = 26, 31
        new = old[:e1s] + old[e1e:e2s] + old[e2e:]

        mgr = make_mgr(old, spans)
        mgr.on_document_edit(e1s, e1e - e1s, 0,
                             old[:e1s] + old[e1e:])          # fragment 1
        mgr.on_document_edit(e2s - (e1e - e1s), e2e - e2s, 0,
                             new)                                # fragment 2

        self.assertEqual(ranges(mgr), [(0, 5), (6, 17), (18, 22)])
        self.assertEqual([new[s:e] for s, e in ranges(mgr)],
                         ["AAAAt", "BBBB-middle", "CCCt"])
        self.assertEqual(validate_block_ranges(mgr.blocks, new), [])

    def test_exact_path_delete_later_occurrence(self):
        """Deleting the LATER of two adjacent identical passages."""
        text = S + S
        mgr = make_mgr(text, [(0, len(S)), (len(S), 2 * len(S))])
        new_text = text[:len(S)]
        mgr.on_document_edit(len(S), len(S), 0, new_text)

        self.assertEqual(ranges(mgr), [(0, len(S))])
        self.assertEqual(validate_block_ranges(mgr.blocks, new_text), [])

    def test_exact_path_midword_boundaries_stay_exact(self):
        """A word split between two Blocks keeps exact ownership.

        Blocks [0,4) and [4,8) over "wordword" (mid-word boundary by
        the user's own choice): deleting one char before the word
        shifts both boundaries EXACTLY; the two slices reconstruct the
        original word without duplication or loss.
        """
        text = "x wordword y"
        w = text.index("wordword")
        mgr = make_mgr(text, [(w, w + 4), (w + 4, w + 8)])
        pos = text.index("x")
        new_text = text[:pos] + text[pos + 1:]
        mgr.on_document_edit(pos, 1, 0, new_text)

        self.assertEqual(ranges(mgr), [(w - 1, w + 3), (w + 3, w + 7)])
        slices = [new_text[b.start_offset:b.end_offset]
                  for b in mgr.blocks]
        self.assertEqual("".join(slices), "wordword")
        self.assertEqual(validate_block_ranges(mgr.blocks, new_text), [])

    def test_exact_path_semantics_preserved(self):
        """Character/label/lock/overrides/SFX/pause ride the exact path."""
        text = S + "\n\n" + S
        a_end = len(S)
        b_start = text.index(S, a_end)
        mgr = make_mgr(text, [(0, a_end), (b_start, len(text))],
                       with_semantics=True)
        before = mgr.blocks
        # Insert two characters inside the FIRST block.
        pos = 5
        new_text = text[:pos] + "--" + text[pos:]
        mgr.on_document_edit(pos, 0, 2, new_text)

        self.assertEqual(len(mgr.blocks), 2)
        a, b = mgr.blocks
        self.assertIs(a, before[0])
        self.assertIs(b, before[1])
        self.assertEqual(a.label, "L0")
        self.assertEqual(a.character_id, "char-0")
        self.assertTrue(a.locked)
        self.assertEqual(a.emotion, "calm")
        self.assertEqual(len(a.sfx_insertions), 1)
        self.assertEqual(len(a.pause_insertions), 1)
        self.assertEqual(b.label, "L1")
        self.assertEqual(b.character_id, "char-1")
        self.assertEqual(ranges(mgr)[1], (b_start + 2, len(text) + 2))
        self.assertEqual(validate_block_ranges(mgr.blocks, new_text), [])

    def test_whole_document_swap_hold_then_resolve(self):
        """setPlainText = TWO emissions (clear-all + insert-all); the
        pair is HELD and resolved by the command-level textChanged
        through the frozen semantics.

        (a) a wholly different document -> every block dropped;
        (b) a small SUBSTITUTION -> blocks transformed (re-anchored),
            NOT destroyed — the P3.44.7 substitution-edit contract.
        """
        # (a) wholly different document
        text = S
        mgr = make_mgr(text, [(0, len(S))])
        mgr.on_document_edit(0, len(S), 0, "")   # clear-all emission
        # Held: blocks survive the intermediate empty state.
        self.assertEqual(len(mgr.blocks), 1)
        mgr.on_document_edit(0, 0, 5, "hello")   # insert-all emission
        self.assertEqual(len(mgr.blocks), 1)      # still held
        mgr.on_text_changed("hello")              # command-level resolve
        self.assertEqual(mgr.blocks, [])
        self.assertEqual(mgr._last_text, "hello")

        # (b) small substitution (the P3.44.7 control case)
        text2 = "Sentence A. Sentence B."
        b_end = text2.index("Sentence B.")
        mgr2 = make_mgr(text2, [(0, b_end - 1), (b_end, len(text2))])
        new2 = text2.replace("Sentence A.", "Sentences A.")
        mgr2.on_document_edit(0, len(text2), 0, "")    # clear-all
        mgr2.on_document_edit(0, 0, len(new2), new2)    # insert-all
        mgr2.on_text_changed(new2)                      # resolve

        self.assertEqual(len(mgr2.blocks), 2,
                         "a small substitution must re-anchor the blocks, "
                         "not destroy them (P3.44.7 contract)")
        self.assertEqual(validate_block_ranges(mgr2.blocks, new2), [])

    def test_exact_path_preapplied_noop(self):
        """After on_multi_replace pre-applied the edits, the document
        swap's TWO emissions are held and the command-level textChanged
        no-ops on the equality guard — the pre-applied offsets survive
        untouched."""
        text = "one TAG two TAG three"
        mgr = make_mgr(text, [(0, 7), (8, 11), (12, len(text))])
        matches = [(4, 7), (12, 15)]
        new_text = text.replace("TAG", "XX")
        mgr.on_multi_replace(text, new_text, matches, "XX")
        expected = ranges(mgr)
        # The real emission pattern of setPlainText(new_text):
        mgr.on_document_edit(0, len(text), 0, "")       # clear-all
        mgr.on_document_edit(0, 0, len(new_text), new_text)  # insert-all
        mgr.on_text_changed(new_text)                    # command resolve
        self.assertEqual(ranges(mgr), expected)

    def test_exact_path_equivalent_to_diff_on_unambiguous_edits(self):
        """Equivalence on the unambiguous domain: for edits where the
        diff is provably correct, both paths agree byte-for-byte."""
        text = "First sentence here.\n\nSecond block text.\n\nThird."
        a_end = text.index("\n\n")
        b_start = text.index("Second")
        b_end = text.index("\n\n", b_start)
        c_start = text.index("Third")
        spans = [(0, a_end), (b_start, b_end), (c_start, len(text))]
        cases = [
            (3, 3, 4),    # insertion inside A
            (a_end + 1, a_end + 3, 0),   # deletion in the gap
            (b_start + 2, b_start + 6, 3),   # replacement inside B
            (0, 0, 10),   # insertion at document start
            (len(text) - 1, len(text), 0),   # deletion at document end
        ]
        for cs, ce, ins in cases:
            new_text = text[:cs] + "X" * ins + text[ce:]
            m1 = make_mgr(text, spans)
            m2 = make_mgr(text, spans)
            m1.on_text_changed(new_text)
            m2.on_document_edit(cs, ce - cs, ins, new_text)
            self.assertEqual(ranges(m1), ranges(m2),
                             "paths disagree for edit [{0}:{1})+{2}"
                             .format(cs, ce, ins))

    def test_exact_path_typical_large_deletion(self):
        """Large multi-line selected deletion keeps every surviving
        block exact (no stale fragments)."""
        paras = [S + " {0}.".format(i) for i in range(6)]
        text = "\n\n".join(paras)
        spans = []
        for p in paras:
            i = text.index(p)
            spans.append((i, i + len(p)))
        mgr = make_mgr(text, spans)
        # Delete paragraphs 2..4 (a large middle selection).
        sel_start = spans[2][0]
        sel_end = spans[4][1]
        new_text = text[:sel_start] + text[sel_end:]
        mgr.on_document_edit(sel_start, sel_end - sel_start, 0, new_text)

        delta = -(sel_end - sel_start)
        self.assertEqual(ranges(mgr), [
            spans[0], spans[1],
            (spans[5][0] + delta, spans[5][1] + delta)])
        self.assertEqual(validate_block_ranges(mgr.blocks, new_text), [])
        for b in mgr.blocks:
            self.assertIn(new_text[b.start_offset:b.end_offset],
                          new_text)


# ===========================================================================
# §B — Real editor widget: the exact feed through real Qt signals
# ===========================================================================
class _EditorHarness(unittest.TestCase):
    """Real NarrationEditor (real widgets, real manager, real signals)."""

    def setUp(self):
        from ui.panels.narration_editor import NarrationEditor
        self.ed = NarrationEditor()
        self.ed._mode = self.ed.MODE_BLOCKS

    def tearDown(self):
        try:
            self.ed.close()
            self.ed.deleteLater()
        except Exception:
            pass
        self.ed = None
        _process(50)

    def load(self, text, spans, with_semantics=False):
        """Production-faithful restore: the MainWindow load path."""
        from engine.narration_blocks import PromptBlock as _PB
        blocks = []
        for i, (s, e) in enumerate(spans):
            b = _PB(start_offset=s, end_offset=e)
            if with_semantics:
                b.label = "L{0}".format(i)
                b.character_id = "char-{0}".format(i)
            blocks.append(b)
        self.ed.begin_scene_load()
        try:
            self.ed.set_text(text)
        finally:
            self.ed.end_scene_load()
        self.ed.begin_scene_load()
        try:
            self.ed.set_scene_blocks(blocks, text)
        finally:
            self.ed.end_scene_load()
        return blocks

    def select(self, start, end):
        cur = self.ed._editor.textCursor()
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.ed._editor.setTextCursor(cur)
        return self.ed._editor.textCursor()

    def delete_selection(self, start, end):
        cur = self.select(start, end)
        cur.removeSelectedText()

    def type_at(self, pos, text):
        cur = self.select(pos, pos)
        cur.insertText(text)

    def ranges(self):
        return [(b.start_offset, b.end_offset)
                for b in self.ed._block_manager.blocks]


def _process(ms=30):
    end = __import__("time").time() + ms / 1000.0
    while __import__("time").time() < end:
        QApplication.processEvents()
        __import__("time").sleep(0.005)


class TestEditorExactFeed(_EditorHarness):

    def test_real_editor_first_copy_deletion_is_exact(self):
        """REAL regression: select+delete the FIRST of two adjacent
        identical sentences through the REAL widget — the exact feed
        removes the deleted block and maps the survivor precisely.

        (With the pre-fix diff-only wiring this test FAILS: the
        surviving block overlaps the stale first block — the
        reproduced user symptom.)
        """
        text = S + S
        b_start = len(S) + S.index("quietly")
        self.load(text, [(0, len(S)), (b_start, 2 * len(S))])

        self.delete_selection(0, len(S))  # delete the FIRST copy

        self.assertEqual(self.ranges(), [(S.index("quietly"), len(S))])
        new_text = self.ed._editor.toPlainText()
        self.assertEqual(new_text, S)
        self.assertEqual(
            validate_block_ranges(self.ed._block_manager.blocks, new_text),
            [])

    def test_real_editor_undo_redo_roundtrip(self):
        """Typed deletion + undo + redo keep ranges EXACT (Qt document
        undo emits exact reverse deltas through contentsChange)."""
        text = S + "\n\n" + S
        a_end = len(S)
        b_start = text.index(S, a_end)
        self.load(text, [(0, a_end), (b_start, len(text))])
        snapshot = self.ranges()

        # Typed deletion inside the first block.
        self.delete_selection(4, 9)
        after_delete = self.ranges()
        self.assertNotEqual(after_delete, snapshot)

        self.ed._editor.document().undo()
        self.assertEqual(self.ranges(), snapshot)

        self.ed._editor.document().redo()
        self.assertEqual(self.ranges(), after_delete)

        new_text = self.ed._editor.toPlainText()
        self.assertEqual(
            validate_block_ranges(self.ed._block_manager.blocks, new_text),
            [])

    def test_real_editor_replace_all_preapplied_intact(self):
        """Replace All (pre-applied offsets + suppressed exact feed)
        leaves exactly the sequential multi-replace result."""
        text = "one TAG two TAG three TAG"
        spans = [(0, 7), (8, 11), (12, 19), (20, len(text))]
        self.load(text, spans)

        self.ed._on_replace_all("TAG", "XX", True)

        new_text = self.ed._editor.toPlainText()
        self.assertEqual(new_text, "one XX two XX three XX")
        # Expected via the pure sequential rule:
        ref = make_mgr(text, spans)
        ref.on_multi_replace(text, new_text, [(4, 7), (12, 15), (20, 23)],
                             "XX")
        self.assertEqual(self.ranges(), ranges(ref))
        self.assertEqual(
            validate_block_ranges(self.ed._block_manager.blocks, new_text),
            [])

    def test_real_editor_paste_over_selection_exact(self):
        """Select + insert (paste-over-selection) transforms exactly."""
        text = "aaaa BLOCKTEXT bbbb"
        s = text.index("BLOCKTEXT")
        self.load(text, [(s, s + len("BLOCKTEXT"))])

        cur = self.select(s + 2, s + 6)  # "OCKT"
        cur.insertText("NEWTEXT")

        new_text = self.ed._editor.toPlainText()
        expected_new = text[:s + 2] + "NEWTEXT" + text[s + 6:]
        self.assertEqual(new_text, expected_new)
        b = self.ed._block_manager.blocks[0]
        self.assertEqual((b.start_offset, b.end_offset),
                         (s, s + 2 + len("NEWTEXT") + 3))
        self.assertEqual(new_text[b.start_offset:b.end_offset],
                         "BLNEWTEXTEXT")

    def test_real_editor_scene_load_guard_verbatim(self):
        """During scene load the exact feed must NOT transform the
        previous scene's blocks — the restore installs verbatim."""
        text = S + "\n\n" + S
        a_end = len(S)
        b_start = text.index(S, a_end)
        spans = [(0, a_end), (b_start, len(text))]
        self.load(text, spans)
        self.assertEqual(self.ranges(), spans)
        # Reload a DIFFERENT document through the guarded path.
        text2 = "Something completely different."
        self.load(text2, [(0, len(text2))])
        self.assertEqual(self.ranges(), [(0, len(text2))])
        self.assertEqual(self.ed._editor.toPlainText(), text2)

    def test_real_editor_large_repeated_deletion_valid(self):
        """The user scenario: large deletion containing repeated
        phrases — every surviving range stays valid and exact."""
        para = S + " Copy {0}."
        paras = [para.format(i) for i in range(5)]
        text = "\n\n".join(paras)
        spans = []
        for p in paras:
            i = text.index(p)
            spans.append((i, i + len(p)))
        self.load(text, spans)

        # Delete a large middle section (paras 1..3), which contains
        # repeated sentence material.
        sel_start, sel_end = spans[1][0], spans[3][1]
        self.delete_selection(sel_start, sel_end)

        new_text = self.ed._editor.toPlainText()
        self.assertEqual(
            validate_block_ranges(self.ed._block_manager.blocks, new_text),
            [])
        owned = "".join(new_text[b.start_offset:b.end_offset]
                        for b in self.ed._block_manager.blocks)
        self.assertIn(S, owned)
        self.assertNotIn("Copy 1.", owned)
        self.assertNotIn("Copy 2.", owned)

    def test_real_editor_document_reset_redetects(self):
        """Programmatic whole-document reset (set_text outside a scene
        load) drops the blocks and re-detects in Blocks mode."""
        text = S + "\n\n" + S
        a_end = len(S)
        b_start = text.index(S, a_end)
        self.load(text, [(0, a_end), (b_start, len(text))])
        self.assertTrue(self.ed._block_manager.has_blocks)

        self.ed.set_text("Fresh document with one sentence.")

        blocks = self.ed._block_manager.blocks
        self.assertTrue(len(blocks) >= 1)
        new_text = self.ed._editor.toPlainText()
        self.assertEqual(
            validate_block_ranges(blocks, new_text), [])


# ===========================================================================
# §C — validate_block_ranges contract
# ===========================================================================
class TestValidateBlockRanges(unittest.TestCase):

    def test_valid_ordered_gapped_ranges_pass(self):
        text = "AAAA\n\nBBBB\n\nCCCC"
        blocks = [PromptBlock(start_offset=0, end_offset=4),
                  PromptBlock(start_offset=6, end_offset=10),
                  PromptBlock(start_offset=12, end_offset=16)]
        self.assertEqual(validate_block_ranges(blocks, text), [])

    def test_out_of_bounds_reported_with_diagnostics(self):
        text = "short"
        b = PromptBlock(start_offset=0, end_offset=999)
        issues = validate_block_ranges([b], text)
        self.assertEqual(len(issues), 1)
        i = issues[0]
        self.assertEqual(i.block_id, b.id)
        self.assertEqual((i.start_offset, i.end_offset), (0, 999))
        self.assertEqual(i.text_length, len(text))
        self.assertEqual(i.source_slice, "short")
        self.assertIn("out of bounds", i.reason)
        self.assertIn("[0:999)", i.describe())

    def test_empty_range_reported(self):
        text = "abcdef"
        b = PromptBlock(start_offset=3, end_offset=3)
        issues = validate_block_ranges([b], text)
        self.assertEqual(len(issues), 1)
        self.assertIn("empty", issues[0].reason)

    def test_negative_start_reported(self):
        text = "abcdef"
        b = PromptBlock(start_offset=-2, end_offset=3)
        issues = validate_block_ranges([b], text)
        self.assertEqual(len(issues), 1)
        self.assertIn("out of bounds", issues[0].reason)

    def test_overlap_reported_with_previous_end(self):
        text = "0123456789"
        blocks = [PromptBlock(start_offset=0, end_offset=6),
                  PromptBlock(start_offset=4, end_offset=9)]
        issues = validate_block_ranges(blocks, text)
        self.assertEqual(len(issues), 1)
        self.assertIn("overlaps", issues[0].reason)
        self.assertIn("previous ends at 6", issues[0].reason)
        # The overlapping (second) block is the one reported.
        self.assertEqual(issues[0].block_id, blocks[1].id)

    def test_out_of_order_reported(self):
        text = "0123456789"
        blocks = [PromptBlock(start_offset=5, end_offset=9),
                  PromptBlock(start_offset=0, end_offset=4)]
        issues = validate_block_ranges(blocks, text)
        self.assertTrue(any("out of order" in i.reason for i in issues))

    def test_pure_function_no_mutation(self):
        text = "0123456789"
        blocks = [PromptBlock(start_offset=0, end_offset=99)]
        before = [(b.start_offset, b.end_offset) for b in blocks]
        validate_block_ranges(blocks, text)
        self.assertEqual([(b.start_offset, b.end_offset) for b in blocks],
                         before)


# ===========================================================================
# §D — Generate Long precondition gate (real MainWindow + Engine)
# ===========================================================================
class _WinHarness(unittest.TestCase):

    def setUp(self):
        import shutil
        import tempfile
        import time
        from unittest.mock import patch, PropertyMock
        self._time = time
        from engine.engine import Engine
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        from ui.main_window import MainWindow
        import ui.main_window as mw_mod
        self.mw_mod = mw_mod

        self.tmp = tempfile.mkdtemp(prefix="ss_p3456_")
        self.engine = Engine(app_root=self.tmp)
        self.patchers = [
            patch.object(type(self.engine._model), "is_loaded",
                         new_callable=PropertyMock, return_value=True),
            patch.object(type(self.engine._model), "device",
                         new_callable=PropertyMock, return_value="cpu"),
        ]
        try:
            from tests.test_p3_44_4_batch_execution_run import (
                FakeHiggsModel)
            self.patchers.append(patch.object(
                type(self.engine._model), "get_model_and_tokenizer",
                return_value=(FakeHiggsModel(), None)))
            FakeHiggsModel.calls = []
        except Exception:
            self.patchers.append(patch.object(
                type(self.engine._model), "get_model_and_tokenizer",
                return_value=(object(), None)))
        for p in self.patchers:
            p.start()
        self.engine.play_audio = lambda p: None
        self.win = MainWindow(self.engine)
        self.pm = ProjectManager(os.path.join(self.tmp, "projects"))
        self.pa = Project(name="Alpha", id="projA")
        self.scene_a = Scene(id="scA00001", name="01 Scene",
                             project_id="projA")
        self.pa.add_scene(self.scene_a)
        self.pm.save_project(self.pa)
        self.win._active_project = self.pa
        self.win._active_scene = self.scene_a
        self.win._current_project = "Alpha"

        # Record every warning dialog the gate shows.
        self.warnings = []
        self._warn_patch = patch.object(
            mw_mod.QMessageBox, "warning",
            lambda *a, **k: self.warnings.append(a))
        self._warn_patch.start()

        # Record splitter invocations (parts construction).
        from engine import narration_splitter as ns_mod
        self.split_calls = []
        orig_split = ns_mod.NarrationSplitter.split

        def rec_split(inner_self, **kwargs):
            parts = orig_split(inner_self, **kwargs)
            self.split_calls.append(
                {"parts": parts, "text": kwargs.get("text"),
                 "blocks": kwargs.get("blocks")})
            return parts
        self._split_patch = patch.object(ns_mod.NarrationSplitter, "split",
                                         rec_split)
        self._split_patch.start()

        # Non-blocking preview dialog.
        from ui.panels.long_narration_dialog import LongNarrationDialog
        self._exec_patch = patch.object(
            LongNarrationDialog, "exec", lambda self_d: 0)
        self._exec_patch.start()

    def tearDown(self):
        for p in (self._warn_patch, self._split_patch, self._exec_patch):
            try:
                p.stop()
            except Exception:
                pass
        for p in self.patchers:
            p.stop()
        try:
            if self.win._batch_dialog is not None:
                self.win._batch_dialog.close()
        except Exception:
            pass
        try:
            self.win.close()
            self.win.deleteLater()
        except Exception:
            pass
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        _process(60)
        self.win = None
        self.engine = None
        import gc
        gc.collect()

    def load_editor(self, text, spans):
        from engine.narration_blocks import PromptBlock as _PB
        blocks = [_PB(start_offset=s, end_offset=e) for s, e in spans]
        ed = self.win._editor
        ed.begin_scene_load()
        try:
            ed.set_text(text)
        finally:
            ed.end_scene_load()
        ed.begin_scene_load()
        try:
            ed.set_scene_blocks(blocks, text)
        finally:
            ed.end_scene_load()
        return blocks


class TestGenerateLongPrecondition(_WinHarness):

    def test_out_of_bounds_ranges_block_generation(self):
        """Invalid (out-of-bounds) ranges block Generate Long BEFORE
        any part/slot/job is constructed; the user sees the exact
        diagnostics + recovery guidance."""
        text = S
        self.load_editor(text, [(0, 999)])   # end far beyond the text
        self.scene_a.expected_audio_slots = [{"slot_id": "old"}]

        self.win._on_generate_long_narration()

        self.assertEqual(self.split_calls, [],
                         "the splitter must never run on invalid ranges")
        self.assertEqual(len(self.warnings), 1)
        msg = self.warnings[0][2]
        self.assertIn("Generate Long stopped", msg)
        self.assertIn("[0:999)", msg)
        self.assertIn("Re-detect", msg)
        self.assertEqual(len(self.win._batch_manager.jobs), 0)
        # No new slots were created — the old structure is untouched.
        self.assertEqual(self.scene_a.expected_audio_slots,
                         [{"slot_id": "old"}])

    def test_overlapping_ranges_block_generation(self):
        """The trailing-substring duplication symptom: overlapping
        ranges are blocked, not silently generated."""
        text = S + S
        b_start = len(S) + S.index("quietly")
        self.load_editor(text, [(0, len(S)), (b_start, 2 * len(S))])
        # Force the legacy corruption state: the first Block covers the
        # whole first sentence while the second Block overlaps its
        # TRAILING SUBSTRING (part 2 would duplicate the sentence's
        # tail — the reported symptom).
        blocks = self.win._editor._block_manager._blocks
        blocks[0].start_offset = 0
        blocks[0].end_offset = len(S)
        blocks[1].start_offset = S.index("quietly")
        blocks[1].end_offset = len(S)

        self.win._on_generate_long_narration()

        self.assertEqual(self.split_calls, [])
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("overlaps", self.warnings[0][2])

    def test_valid_ranges_proceed_to_splitter(self):
        """Valid ranges reach the splitter (parts constructed from the
        CURRENT text only)."""
        text = S + "\n\n" + S
        a_end = len(S)
        b_start = text.index(S, a_end)
        self.load_editor(text, [(0, a_end), (b_start, len(text))])

        self.win._on_generate_long_narration()

        self.assertEqual(len(self.split_calls), 1)
        self.assertEqual(self.split_calls[0]["text"], text)
        self.assertEqual(len(self.split_calls[0]["parts"]), 2)
        self.assertEqual(self.warnings, [])

    def test_generate_after_deletion_uses_only_current_text(self):
        """Previous slots/assets present; a large deletion containing
        repeated phrases; Generate Long's parts derive exclusively
        from the CURRENT text slices (no stale fragments)."""
        para = S + " Copy {0}."
        paras = [para.format(i) for i in range(4)]
        text = "\n\n".join(paras)
        spans = []
        for p in paras:
            i = text.index(p)
            spans.append((i, i + len(p)))
        self.load_editor(text, spans)
        # PREVIOUS generation history exists (old slots + assets).
        self.scene_a.expected_audio_slots = [
            {"slot_id": "b1:1", "part_index": 1}]
        self.scene_a.audio_assets = [
            {"id": "asset-old", "slot_id": "b1:1",
             "output_path": "old.wav", "duration": 12.0}]

        # REAL editor edit: delete the middle two paragraphs.
        ed = self.win._editor
        cur_start, cur_end = spans[1][0], spans[2][1]
        cur = ed._editor.textCursor()
        cur.setPosition(cur_start)
        cur.setPosition(cur_end, QTextCursor.MoveMode.KeepAnchor)
        ed._editor.setTextCursor(cur)
        cur.removeSelectedText()
        _process(50)

        self.win._on_generate_long_narration()

        self.assertEqual(len(self.split_calls), 1)
        parts = self.split_calls[0]["parts"]
        joined = "\n".join(p.text for p in parts)
        current = ed._editor.toPlainText()
        self.assertIn(S, joined)
        self.assertNotIn("Copy 1.", joined)
        self.assertNotIn("Copy 2.", joined)
        for p in parts:
            self.assertIn(p.text, current,
                          "every part must be a slice of the CURRENT "
                          "text: {0!r}".format(p.text[:60]))
        # The old structure is untouched by a preview-only run…
        self.assertEqual(self.scene_a.expected_audio_slots,
                         [{"slot_id": "b1:1", "part_index": 1}])
        # …and the old assets remain (append-only history) but never
        # contributed text:
        self.assertEqual(len(self.scene_a.audio_assets), 1)

    def test_save_load_after_range_transforming_edits(self):
        """Save/load round trip preserves the transformed ranges
        byte-for-byte (real editor edits, real serialisation path)."""
        text = S + "\n\n" + S
        a_end = len(S)
        b_start = text.index(S, a_end)
        self.load_editor(text, [(0, a_end), (b_start, len(text))])

        # REAL edit: delete a chunk inside the first block.
        ed = self.win._editor
        cur = ed._editor.textCursor()
        cur.setPosition(4)
        cur.setPosition(12, QTextCursor.MoveMode.KeepAnchor)
        ed._editor.setTextCursor(cur)
        cur.removeSelectedText()
        _process(50)

        live = [(b.id, b.start_offset, b.end_offset)
                for b in ed._block_manager.blocks]
        payload = [b.to_dict() for b in ed._block_manager.blocks]
        final_text = ed._editor.toPlainText()

        # Reload through the production restore path (dicts → from_dict
        # → set_scene_blocks, preserving the saved identities).
        from engine.narration_blocks import PromptBlock as _PB2
        restored_blocks = [_PB2.from_dict(d) for d in payload]
        ed.begin_scene_load()
        try:
            ed.set_text(final_text)
        finally:
            ed.end_scene_load()
        ed.begin_scene_load()
        try:
            ed.set_scene_blocks(restored_blocks, final_text)
        finally:
            ed.end_scene_load()
        restored = [(b.id, b.start_offset, b.end_offset)
                    for b in ed._block_manager.blocks]
        self.assertEqual(restored, live)
        self.assertEqual(
            validate_block_ranges(ed._block_manager.blocks, final_text),
            [])

    def test_legacy_corrupt_scene_blocked(self):
        """Legacy invalid ranges restored verbatim from Scene data are
        caught by the gate (fail closed) — not silently used."""
        text = S
        # A hand-corrupted legacy payload (end beyond the text).
        corrupt = PromptBlock(start_offset=0, end_offset=len(S) + 50)
        payload = [corrupt.to_dict()]
        self.scene_a.text = text
        self.scene_a.narration_blocks = payload
        # Restore exactly as _load_scene_state does.
        from engine.narration_blocks import PromptBlock as _PB
        restored = [_PB.from_dict(d) for d in payload]
        ed = self.win._editor
        ed.begin_scene_load()
        try:
            ed.set_text(text)
        finally:
            ed.end_scene_load()
        ed.begin_scene_load()
        try:
            ed.set_scene_blocks(restored, text)
        finally:
            ed.end_scene_load()

        self.win._on_generate_long_narration()

        self.assertEqual(self.split_calls, [])
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("out of bounds", self.warnings[0][2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
