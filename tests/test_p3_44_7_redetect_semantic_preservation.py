"""
GUINEO (SpeechStudio) — P3.44.7 runtime tests
==============================================

RE-DETECT SEMANTIC PRESERVATION (implementation + regression phase).

Locks the guarantees mandated by docs/design/
P3_44_7_REDETECT_SEMANTIC_PRESERVATION.md:

  §7    Character-only assignment is manual semantic state
        (has_overrides + has_manual_edits + the editor combo handler).
  §8-9  Character / emotion / style / prosody survive unambiguous
        Re-detect matches; ambiguity records lost_character_id
        (never silent loss); SPLITS transfer block-wide semantics to
        every child and allocate span-local SFX/pause insertions by
        the child's mapped span.
  §10-11 SFX / pause insertions (block-relative offsets) transfer with
        a deterministic offset mapping; unmappable anchors are dropped
        (logged), never guessed onto unrelated text.
  §12   Locked-block semantics intact on the Apply path.
  §17   P3.44.6 slot_id identity interaction: Re-detect replaces block
        ids → new Generate Long materialises NEW slot ids → an asset
        stamped with the OLD slot id never joins the new structure
        (identity-first, no positional fallback for modern assets).

Also locks ABSENCE OF FALSE PRESERVATION (§19): unrelated blocks never
inherit Characters, SFX or pauses; one old block never double-donates.

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \\
    QT_QPA_PLATFORM=offscreen python3 -m pytest \\
        tests/test_p3_44_7_redetect_semantic_preservation.py -v
"""
from __future__ import annotations

import os
import sys
import types
import unittest
import unittest.mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Fake torch (house pattern: test_p3_35 / test_p3_41 / test_p3_44_5).
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

from PySide6.QtWidgets import QApplication, QMessageBox as _RealMsgBox  # noqa: E402

APP = QApplication.instance() or QApplication([])

from ui.theme import apply_theme, DEFAULT_THEME  # noqa: E402
apply_theme(APP, DEFAULT_THEME)

from engine.narration_blocks import (  # noqa: E402
    PromptBlock, SfxInsertion, PauseInsertion,
)
from engine.narration_block_manager import NarrationBlockManager  # noqa: E402
from engine.block_detector import BlockDetector  # noqa: E402


# ---------------------------------------------------------------------------
# Deterministic detector double — exact partitions per detect() call.
# ---------------------------------------------------------------------------
class ScriptedDetector(BlockDetector):
    """Consumes a scripted list of (expected_text, spans) plans in order.

    Each detect(text) call pops the next plan and asserts the text —
    full determinism for both the old structure (auto_detect) and the
    new structure (preserve_overrides / rebuild_automatic_only).
    """

    def __init__(self, plans):
        self._plans = list(plans)
        self.calls = 0

    def detect(self, text):
        if self.calls >= len(self._plans):
            raise AssertionError(
                "ScriptedDetector: no plan left for text %r" % text)
        expected_text, spans = self._plans[self.calls]
        self.calls += 1
        if text != expected_text:
            raise AssertionError(
                "ScriptedDetector: expected %r, got %r" % (expected_text, text))
        return [PromptBlock(start_offset=s, end_offset=e) for s, e in spans]


def make_mgr(plans):
    """Manager whose first detect() call (auto_detect) consumes plan 0."""
    mgr = NarrationBlockManager(detector=ScriptedDetector(plans))
    mgr.auto_detect(plans[0][0])
    return mgr


# ===========================================================================
# 1. Semantic state recognition (§7 / §13 — Character-only IS manual state)
# ===========================================================================
class TestSemanticStateRecognition(unittest.TestCase):

    def test_character_only_counts_as_override(self):
        """Matrix 1: a Character assignment alone is semantic state."""
        b = PromptBlock(character_id="char_a")
        self.assertTrue(b.has_overrides())
        mgr = make_mgr([("One. Two.", [(0, 8)])])
        mgr.blocks[0].character_id = "char_a"
        self.assertTrue(mgr.has_manual_edits)

    def test_sfx_pause_label_only_count(self):
        """SFX, pause and label alone are semantic state too (loaded
        projects with only that metadata gate the direct rebuild)."""
        self.assertTrue(PromptBlock(
            sfx_insertions=[SfxInsertion("Sigh", "Ahh", 3)]).has_overrides())
        self.assertTrue(PromptBlock(
            pause_insertions=[PauseInsertion("pause", 3)]).has_overrides())
        self.assertTrue(PromptBlock(label="Intro").has_overrides())

    def test_lost_warning_alone_is_not_semantic_state(self):
        """A stale lost-character warning is display state, not content."""
        self.assertFalse(PromptBlock(lost_character_id="char_x").has_overrides())

    def test_fresh_blocks_have_no_manual_edits(self):
        """Control: pristine detected blocks keep the direct rebuild UX."""
        mgr = make_mgr([("One. Two.", [(0, 8)])])
        self.assertFalse(mgr.has_manual_edits)
        self.assertFalse(mgr.blocks[0].has_overrides())


# ===========================================================================
# 2. preserve_overrides (Save path) — Character / metadata matrix
# ===========================================================================
class TestPreserveOverridesCharacter(unittest.TestCase):

    def test_character_survives_exact_match(self):
        """Matrix 2 (control): unchanged text → Character transfers."""
        text = "The cat sat on the mat."
        mgr = make_mgr([(text, [(0, len(text))]), (text, [(0, len(text))])])
        mgr.blocks[0].character_id = "char_a"
        mgr.preserve_overrides(text)
        self.assertEqual(mgr.blocks[0].character_id, "char_a")

    def test_character_survives_substitution_edit(self):
        """Matrix 3/5 — DEFECT B detector: a plain substitution edit
        ("mat" → "rug") breaks containment entirely; before P3.44.7 the
        Character vanished with NO transfer and NO lost warning."""
        old_text = "The cat sat on the mat."
        new_text = "The cat sat on the rug."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        mgr.blocks[0].character_id = "char_a"
        mgr.preserve_overrides(new_text)
        self.assertEqual(mgr.blocks[0].character_id, "char_a",
                         "Character must survive a plain substitution edit")
        self.assertIsNone(mgr.blocks[0].lost_character_id)

    def test_semantic_overrides_survive_substitution(self):
        """Matrix 7-9: emotion/style/prosody survive the substitution
        edit through the same difflib similarity match."""
        old_text = "The cat sat on the mat."
        new_text = "The cat sat on the rug."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        blk = mgr.blocks[0]
        blk.emotion = "Awe"
        blk.style = "Calm"
        blk.speed = "Slow"
        blk.pitch = "Low"
        blk.delivery = "Narrative"
        mgr.preserve_overrides(new_text)
        nb = mgr.blocks[0]
        self.assertEqual(nb.emotion, "Awe")
        self.assertEqual(nb.style, "Calm")
        self.assertEqual(nb.speed, "Slow")
        self.assertEqual(nb.pitch, "Low")
        self.assertEqual(nb.delivery, "Narrative")

    def test_character_lost_warning_on_mid_similarity(self):
        """Matrix 6: mid-similarity (0.5 <= ratio < 0.8, no containment)
        is AMBIGUOUS → lost_character_id warning, never silent loss."""
        old_text = "The cat sat on the mat."
        new_text = "The cat sat on the very comfortable mat."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        mgr.blocks[0].character_id = "char_a"
        mgr.preserve_overrides(new_text)
        nb = mgr.blocks[0]
        self.assertIsNone(nb.character_id,
                          "no unverified voice on an ambiguous match")
        self.assertEqual(nb.lost_character_id, "char_a",
                         "the Character must be recorded as lost, not lost")

    def test_character_not_transferred_when_unrelated(self):
        """§19 false-preservation guard: unrelated text → NO transfer
        and NO lost warning (nothing plausible to attach to)."""
        old_text = "The cat sat quietly."
        new_text = "Completely different content here."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        mgr.blocks[0].character_id = "char_a"
        mgr.preserve_overrides(new_text)
        nb = mgr.blocks[0]
        self.assertIsNone(nb.character_id)
        self.assertIsNone(nb.lost_character_id)

    def test_ambiguity_appended_text_still_records_lost(self):
        """P3.23 §25 control (legacy containment donation): large
        append-only edit → containment holds below 80% → lost warning."""
        old_text = "First paragraph of narration text here."
        new_text = (old_text + " The paragraph continues with "
                    "considerable additional text. It goes on and on.")
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        mgr.blocks[0].character_id = "char_a"
        mgr.preserve_overrides(new_text)
        self.assertEqual(mgr.blocks[0].lost_character_id, "char_a")
        self.assertIsNone(mgr.blocks[0].character_id)


# ===========================================================================
# 3. SPLIT / union coverage (§9)
# ===========================================================================
SPLIT_OLD = "Alpha beta gamma. Delta epsilon zeta."
SPLIT_NEW_SPANS = [(0, 17), (18, 37)]  # "Alpha beta gamma." / "Delta epsilon zeta."


def _split_mgr():
    mgr = make_mgr([(SPLIT_OLD, [(0, len(SPLIT_OLD))]),
                    (SPLIT_OLD, SPLIT_NEW_SPANS)])
    blk = mgr.blocks[0]
    blk.character_id = "char_a"
    blk.emotion = "Awe"
    blk.style = "Calm"
    blk.label = "Intro"
    blk.sfx_insertions = [SfxInsertion("Laughter", "Haha", 5)]
    blk.pause_insertions = [PauseInsertion("pause", 24)]
    return mgr


class TestSplitPreservation(unittest.TestCase):

    def test_split_transfers_block_wide_semantics_to_both_children(self):
        """Matrix 10: one old block → two new blocks; block-wide semantic
        state reaches EVERY child (not dropped, not single-child only)."""
        mgr = _split_mgr()
        mgr.preserve_overrides(SPLIT_OLD)
        self.assertEqual(len(mgr.blocks), 2)
        for child in mgr.blocks:
            self.assertEqual(child.character_id, "char_a")
            self.assertEqual(child.emotion, "Awe")
            self.assertEqual(child.style, "Calm")
            self.assertEqual(child.label, "Intro")
            self.assertTrue(child.manually_edited)
            self.assertIsNone(child.lost_character_id)

    def test_split_allocates_span_local_insertions_by_child(self):
        """Matrix 11/14/17: SFX lands on the child whose span contains
        it; pause lands on the other child — never both, never neither."""
        mgr = _split_mgr()
        mgr.preserve_overrides(SPLIT_OLD)
        c0, c1 = mgr.blocks
        # Old text: "Alpha beta gamma. Delta epsilon zeta." (37 chars)
        # child0 raw = "Alpha beta gamma." (region 0..17) → SFX 5 → 5
        # child1 raw = "Delta epsilon zeta." (region 18..37) → pause 24 → 6
        self.assertEqual([(s.sfx_name, s.offset) for s in c0.sfx_insertions],
                         [("Laughter", 5)])
        self.assertEqual(c0.pause_insertions, [])
        self.assertEqual([(p.pause_type, p.offset) for p in c1.pause_insertions],
                         [("pause", 6)])
        self.assertEqual(c1.sfx_insertions, [])

    def test_split_three_children(self):
        """A three-way split allocates by the same span rule."""
        old_text = "Ha. Ha. Ha."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (old_text, [(0, 3), (4, 7), (8, 11)])])
        blk = mgr.blocks[0]
        blk.character_id = "char_a"
        blk.emotion = "Awe"
        blk.sfx_insertions = [SfxInsertion("Sigh", "Uhh", 5)]
        mgr.preserve_overrides(old_text)
        self.assertEqual(len(mgr.blocks), 3)
        for child in mgr.blocks:
            self.assertEqual(child.character_id, "char_a")
            self.assertEqual(child.emotion, "Awe")
        # Offset 5 lies in the middle child "Ha." (region 4..7) → local 1.
        self.assertEqual([(s.sfx_name, s.offset)
                          for s in mgr.blocks[1].sfx_insertions],
                         [("Sigh", 1)])
        self.assertEqual(mgr.blocks[0].sfx_insertions, [])
        self.assertEqual(mgr.blocks[2].sfx_insertions, [])

    def test_split_repeated_children_no_double_allocation(self):
        """§19: repeated child texts allocate an insertion exactly once
        (sequential region search — never two children, never neither)."""
        old_text = "Ha. Ha. Ha."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (old_text, [(0, 3), (4, 7), (8, 11)])])
        mgr.blocks[0].sfx_insertions = [
            SfxInsertion("Sigh", "Uhh", 5),
            SfxInsertion("Cough", "Ahem", 9),
        ]
        mgr.preserve_overrides(old_text)
        counts = [sum(1 for _ in b.sfx_insertions) for b in mgr.blocks]
        self.assertEqual(counts, [0, 1, 1])
        self.assertEqual(mgr.blocks[1].sfx_insertions[0].offset, 1)
        self.assertEqual(mgr.blocks[2].sfx_insertions[0].offset, 1)

    def test_split_deterministic_on_repeat(self):
        """Matrix 12: the same scenario run twice yields identical
        semantic field snapshots."""
        def snapshot():
            mgr = _split_mgr()
            mgr.preserve_overrides(SPLIT_OLD)
            return [(b.character_id, b.emotion, b.style, b.label,
                     [(s.sfx_name, s.offset) for s in b.sfx_insertions],
                     [(p.pause_type, p.offset) for p in b.pause_insertions])
                    for b in mgr.blocks]
        self.assertEqual(snapshot(), snapshot())

    def test_split_without_semantic_state_is_noop(self):
        """§19 control: splitting a semantic-FREE block must not mark or
        populate the children (no false preservation)."""
        old_text = "Alpha beta gamma. Delta epsilon zeta."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (old_text, SPLIT_NEW_SPANS)])
        mgr.preserve_overrides(old_text)
        for child in mgr.blocks:
            self.assertFalse(child.manually_edited)
            self.assertIsNone(child.character_id)
            self.assertIsNone(child.emotion)
            self.assertEqual(child.sfx_insertions, [])

    def test_split_plus_edit_does_not_match(self):
        """Split + text edit breaks union coverage → no transfer; the
        Character still reaches the lost warning (explicit outcome)."""
        old_text = "Alpha beta gamma. Delta epsilon zeta."
        new_text = "Alpha beta gamma. Delta epsilon OTHER."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, 18), (19, len(new_text))])])
        mgr.blocks[0].character_id = "char_a"
        mgr.preserve_overrides(new_text)
        for child in mgr.blocks:
            self.assertIsNone(child.character_id)
        lost = [b.lost_character_id for b in mgr.blocks if b.lost_character_id]
        self.assertIn("char_a", lost)


# ===========================================================================
# 4. SFX / pause span-local transfer (§10-11) + false preservation (§19)
# ===========================================================================
class TestSfxPausePreservation(unittest.TestCase):

    def test_sfx_pause_survive_exact_match(self):
        """Matrix 13: SFX and pause survive Re-detect on a matching block
        with identical offsets (span-local semantics kept)."""
        text = "Hello wonderful world today."
        mgr = make_mgr([(text, [(0, len(text))]), (text, [(0, len(text))])])
        mgr.blocks[0].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 6)]
        mgr.blocks[0].pause_insertions = [PauseInsertion("long_pause", 20)]
        mgr.preserve_overrides(text)
        nb = mgr.blocks[0]
        self.assertEqual([(s.sfx_name, s.onomatopoeia, s.offset)
                          for s in nb.sfx_insertions],
                         [("Sigh", "Ahh", 6)])
        self.assertEqual([(p.pause_type, p.offset) for p in nb.pause_insertions],
                         [("long_pause", 20)])

    def test_sfx_offset_shifted_on_growth(self):
        """Growth with a prefix: the insertion offset follows the text
        (rule 3 — old text inside new text → position shift)."""
        old_text = "Say hello world now."
        new_text = "Please say hello world now."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        mgr.blocks[0].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 4)]
        mgr.preserve_overrides(new_text)
        # "Say hello world now." starts at offset 7 in the new text.
        self.assertEqual(mgr.blocks[0].sfx_insertions[0].offset, 4 + 7)

    def test_sfx_in_edited_region_dropped_deterministically(self):
        """§3 explicit ambiguity outcome: an insertion anchored in the
        EDITED region is dropped (logged) — never guessed; one anchored
        in the unchanged region maps precisely."""
        old_text = "The cat sat on the mat."
        new_text = "The cat sat on the rug."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        mgr.blocks[0].sfx_insertions = [
            SfxInsertion("Sigh", "Ahh", 4),     # inside unchanged "The cat…"
            SfxInsertion("Cough", "Ahem", 21),  # inside edited "mat"/"rug"
        ]
        mgr.preserve_overrides(new_text)
        mapped = [(s.sfx_name, s.offset) for s in mgr.blocks[0].sfx_insertions]
        self.assertEqual(mapped, [("Sigh", 4)])

    def test_sfx_not_attached_to_unrelated_block(self):
        """§19: SFX must NOT appear on an unrelated new block."""
        old_text = "The cat sat quietly."
        new_text = "Completely different content here."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        mgr.blocks[0].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 4)]
        mgr.preserve_overrides(new_text)
        self.assertEqual(mgr.blocks[0].sfx_insertions, [])

    def test_pause_not_moved_to_unrelated_boundary(self):
        """§19: a pause must not move to an unrelated new boundary."""
        old_text = "The cat sat quietly."
        new_text = "Completely different content here."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        mgr.blocks[0].pause_insertions = [PauseInsertion("pause", 11)]
        mgr.preserve_overrides(new_text)
        self.assertEqual(mgr.blocks[0].pause_insertions, [])

    def test_sfx_materializes_at_the_transferred_location(self):
        """End-to-end: after the transfer, the insertion materializes
        into the SAME semantic location in the block text."""
        from engine.prompt_builder import materialize_inline_markers
        old_text = "Say hello world now."
        new_text = "Please say hello world now."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        mgr.blocks[0].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 4)]
        mgr.preserve_overrides(new_text)
        ins = mgr.blocks[0].sfx_insertions
        mat, count = materialize_inline_markers(new_text, ins, [])
        self.assertEqual(count, 1)
        # The marker sits before "hello" — the same anchor word as before.
        self.assertIn("{sfx:Sigh:Ahh} hello", mat.replace("  ", " "))

    def test_sfx_survives_apply_path_locked_exact(self):
        """Matrix 21: the locked-block Apply path also preserves SFX
        and pause metadata (was silently dropped before P3.44.7)."""
        text = "Hello wonderful world today."
        mgr = make_mgr([(text, [(0, len(text))]), (text, [(0, len(text))])])
        mgr.blocks[0].locked = True
        mgr.blocks[0].character_id = "char_a"
        mgr.blocks[0].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 6)]
        mgr.blocks[0].pause_insertions = [PauseInsertion("pause", 20)]
        mgr.rebuild_automatic_only(text)
        nb = mgr.blocks[0]
        self.assertTrue(nb.locked)
        self.assertEqual(nb.character_id, "char_a")
        self.assertEqual([(s.sfx_name, s.offset) for s in nb.sfx_insertions],
                         [("Sigh", 6)])
        self.assertEqual([(p.pause_type, p.offset) for p in nb.pause_insertions],
                         [("pause", 20)])


# ===========================================================================
# 5. rebuild_automatic_only (Apply path, §12 / §5 path equivalence)
# ===========================================================================
class TestApplyPath(unittest.TestCase):

    def test_apply_preserves_locked_block_semantics(self):
        """Matrix 21: locked block with exact text keeps overrides,
        label, lock, Character AND SFX/pause (full field matrix)."""
        text = "Alpha beta gamma here."
        mgr = make_mgr([(text, [(0, len(text))]), (text, [(0, len(text))])])
        blk = mgr.blocks[0]
        blk.locked = True
        blk.character_id = "char_a"
        blk.emotion = "Awe"
        blk.style = "Calm"
        blk.label = "Keep"
        blk.sfx_insertions = [SfxInsertion("Sigh", "Ahh", 3)]
        mgr.rebuild_automatic_only(text)
        nb = mgr.blocks[0]
        self.assertTrue(nb.locked)
        self.assertTrue(nb.manually_edited)
        self.assertEqual(nb.character_id, "char_a")
        self.assertEqual(nb.emotion, "Awe")
        self.assertEqual(nb.style, "Calm")
        self.assertEqual(nb.label, "Keep")
        self.assertEqual([(s.sfx_name, s.offset) for s in nb.sfx_insertions],
                         [("Sigh", 3)])

    def test_apply_split_locked_block(self):
        """A locked block split into consecutive new blocks keeps its
        semantics on every child (path equivalence with the Save path)."""
        old_text = "Alpha beta. Gamma delta."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (old_text, [(0, 11), (12, 24)])])
        blk = mgr.blocks[0]
        blk.locked = True
        blk.character_id = "char_a"
        blk.emotion = "Awe"
        blk.sfx_insertions = [SfxInsertion("Sigh", "Ahh", 3)]
        mgr.rebuild_automatic_only(old_text)
        self.assertEqual(len(mgr.blocks), 2)
        for child in mgr.blocks:
            self.assertTrue(child.locked)
            self.assertEqual(child.character_id, "char_a")
            self.assertEqual(child.emotion, "Awe")
        self.assertEqual(mgr.blocks[0].sfx_insertions[0].offset, 3)
        self.assertEqual(mgr.blocks[1].sfx_insertions, [])

    def test_apply_lost_character_when_locked_text_gone(self):
        """Path equivalence: a locked block whose text was edited away
        donates its Character as lost_character_id — never silent loss."""
        old_text = "Alpha beta gamma here."
        new_text = "Alpha delta epsilon here."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        mgr.blocks[0].locked = True
        mgr.blocks[0].character_id = "char_a"
        mgr.rebuild_automatic_only(new_text)
        nb = mgr.blocks[0]
        self.assertIsNone(nb.character_id)
        self.assertEqual(nb.lost_character_id, "char_a")

    def test_apply_no_donation_when_unrelated(self):
        """Control: a locked block replaced by unrelated text records
        nothing (no plausible home for the warning)."""
        old_text = "The cat sat quietly."
        new_text = "Completely different content here."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        mgr.blocks[0].locked = True
        mgr.blocks[0].character_id = "char_a"
        mgr.rebuild_automatic_only(new_text)
        nb = mgr.blocks[0]
        self.assertIsNone(nb.character_id)
        self.assertIsNone(nb.lost_character_id)

    def test_unlocked_blocks_rebuilt_on_apply(self):
        """Control: automatic (unlocked) blocks are recalculated — their
        semantics do NOT survive the Apply path (by design)."""
        text = "Alpha beta. Gamma delta."
        mgr = make_mgr([(text, [(0, 11), (12, 24)]),
                        (text, [(0, 11), (12, 24)])])
        mgr.blocks[1].character_id = "char_b"  # NOT locked
        mgr.blocks[0].locked = True
        mgr.rebuild_automatic_only(text)
        chars = [b.character_id for b in mgr.blocks]
        self.assertIn(None, chars)
        self.assertNotIn("char_b", chars)


# ===========================================================================
# 6. Invariants (§18 23-26)
# ===========================================================================
class TestInvariants(unittest.TestCase):

    def test_caller_blocks_not_mutated(self):
        """Matrix 23: the caller's original PromptBlock objects are never
        mutated by preserve_overrides (new objects carry the transfer)."""
        old_text = "The cat sat on the mat."
        new_text = "The cat sat on the rug."
        mgr = make_mgr([(old_text, [(0, len(old_text))]),
                        (new_text, [(0, len(new_text))])])
        blk = mgr.blocks[0]
        blk.character_id = "char_a"
        blk.emotion = "Awe"
        blk.sfx_insertions = [SfxInsertion("Sigh", "Ahh", 4)]
        before = (blk.character_id, blk.emotion,
                  [(s.sfx_name, s.offset) for s in blk.sfx_insertions],
                  blk.manually_edited, blk.locked, blk.start_offset,
                  blk.end_offset, blk.id)
        mgr.preserve_overrides(new_text)
        after = (blk.character_id, blk.emotion,
                 [(s.sfx_name, s.offset) for s in blk.sfx_insertions],
                 blk.manually_edited, blk.locked, blk.start_offset,
                 blk.end_offset, blk.id)
        self.assertEqual(before, after)

    def test_one_to_one_no_duplicate_character(self):
        """Matrix 24: two IDENTICAL new blocks — the single old block's
        Character transfers to exactly ONE of them (1:1 discipline)."""
        text = "Same same."
        # New structure: two blocks with the same text.
        mgr = make_mgr([(text, [(0, len(text))]),
                        (text * 2, [(0, 10), (10, 20)])])
        mgr.blocks[0].character_id = "char_a"
        mgr.preserve_overrides(text * 2)
        holders = [b for b in mgr.blocks if b.character_id == "char_a"]
        self.assertEqual(len(holders), 1)

    def test_new_structure_valid(self):
        """Matrix 26: after a split Re-detect the block spans stay a
        sorted, disjoint, in-bounds structure."""
        mgr = _split_mgr()
        mgr.preserve_overrides(SPLIT_OLD)
        blocks = mgr._blocks
        self.assertEqual([b.start_offset for b in blocks],
                         sorted(b.start_offset for b in blocks))
        for a, b in zip(blocks, blocks[1:]):
            self.assertLessEqual(a.end_offset, b.start_offset)
        for b in blocks:
            self.assertGreaterEqual(b.start_offset, 0)
            self.assertLessEqual(b.end_offset, len(SPLIT_OLD))

    def test_semantic_conservation_or_explicit_loss(self):
        """Matrix 25: every old Character is either ACTIVE on some new
        block or recorded as lost — never vanished silently."""
        old_text = ("Alpha beta gamma. Delta epsilon zeta.\n\n"
                    "The cat sat on the mat.")
        # New: first paragraph splits; second block gets a substitution.
        new_text = ("Alpha beta gamma. Delta epsilon zeta.\n\n"
                    "The cat sat on the rug.")
        first_len = 37
        gap = 2
        mgr = make_mgr([
            (old_text, [(0, first_len),
                        (first_len + gap, len(old_text))]),
            (new_text, [(0, 17), (18, first_len),
                        (first_len + gap, len(new_text))]),
        ])
        mgr.blocks[0].character_id = "char_a"
        mgr.blocks[0].emotion = "Awe"
        mgr.blocks[1].character_id = "char_b"
        mgr.preserve_overrides(new_text)
        active = {b.character_id for b in mgr.blocks if b.character_id}
        lost = {b.lost_character_id for b in mgr.blocks if b.lost_character_id}
        self.assertIn("char_a", active)   # split children keep it
        self.assertIn("char_b", active)   # substitution match keeps it
        for char in ("char_a", "char_b"):
            self.assertTrue(char in active or char in lost,
                            "%s vanished silently" % char)


# ===========================================================================
# 7. P3.44.6 slot identity interaction (§17)
# ===========================================================================
class TestSlotIdentityInteraction(unittest.TestCase):

    def _parts_for(self, block_ids):
        import types as _t
        return [_t.SimpleNamespace(
            source_block_id=bid, part_of_block=1, block_label="",
            speaker="", character_id=None) for bid in block_ids]

    def test_redetect_new_ids_new_slot_ids_old_asset_does_not_join(self):
        """Re-detect replaces block ids → the next Generate Long
        materialises NEW slot ids → a MODERN asset stamped with the OLD
        slot id joins nothing (identity-first, no positional fallback)."""
        from engine.audio_provenance import (
            materialize_expected_slots, slot_of_asset, block_slot_id)
        old_block_id = "oldblk123456"
        new_block_id = "newblk654321"
        old_slots = materialize_expected_slots(
            self._parts_for([old_block_id]))
        new_slots = materialize_expected_slots(
            self._parts_for([new_block_id]))
        self.assertNotEqual(old_slots[0]["slot_id"], new_slots[0]["slot_id"])
        scene = types.SimpleNamespace(expected_audio_slots=new_slots)
        # Modern asset generated for the OLD structure (part_index 1 —
        # which ALSO exists in the new structure positionally).
        asset = {"slot_id": old_slots[0]["slot_id"],
                 "block_id": old_block_id, "part_index": 1}
        self.assertIsNone(slot_of_asset(asset, scene),
                          "a modern asset must not be re-anchored to the "
                          "new structure by position")
        # The asset for the NEW slot still joins correctly.
        new_asset = {"slot_id": new_slots[0]["slot_id"],
                     "block_id": new_block_id, "part_index": 1}
        self.assertEqual(slot_of_asset(new_asset, scene),
                         new_slots[0]["slot_id"])

    def test_legacy_asset_control_still_joins(self):
        """Control: a legacy (pre-P3.44.6) asset with a block_id that
        matches the new structure's block still joins positionally."""
        from engine.audio_provenance import materialize_expected_slots, slot_of_asset
        new_block_id = "newblk654321"
        new_slots = materialize_expected_slots(
            self._parts_for([new_block_id]))
        scene = types.SimpleNamespace(expected_audio_slots=new_slots)
        legacy_asset = {"block_id": new_block_id, "part_index": 1}
        self.assertEqual(slot_of_asset(legacy_asset, scene),
                         new_slots[0]["slot_id"])


# ===========================================================================
# 8. Real editor paths (dialog gating + dispatch, §5 / §7)
# ===========================================================================
class _DialogStub:
    """Patches narration_editor.QMessageBox — records dialog use."""

    StandardButton = _RealMsgBox.StandardButton
    calls = []
    exec_result = _RealMsgBox.StandardButton.Cancel
    question_result = _RealMsgBox.StandardButton.Yes

    def __init__(self, *a, **k):
        pass

    def setWindowTitle(self, *a):
        pass

    def setText(self, *a):
        _DialogStub.calls.append("text")

    def setInformativeText(self, *a):
        pass

    def setStandardButtons(self, *a):
        pass

    def exec(self):
        _DialogStub.calls.append("exec")
        return _DialogStub.exec_result

    @staticmethod
    def question(*a, **k):
        _DialogStub.calls.append("question")
        return _DialogStub.question_result


class TestEditorPaths(unittest.TestCase):
    """Real NarrationEditor (real widget, real manager, real dialog
    dispatch — only QMessageBox is stubbed)."""

    def setUp(self):
        from ui.panels.narration_editor import NarrationEditor
        self.ed = NarrationEditor()
        self.ed.set_characters([{"id": "char-anna", "name": "ANNA"},
                                {"id": "char-mark", "name": "MARK"}])
        _DialogStub.calls = []
        _DialogStub.exec_result = _DialogStub.StandardButton.Cancel
        _DialogStub.question_result = _DialogStub.StandardButton.Yes
        self._patcher = unittest.mock.patch(
            "ui.panels.narration_editor.QMessageBox", _DialogStub)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.ed.deleteLater()

    def _setup_blocks(self, text="Sentence A.\n\nSentence B."):
        self.ed.clear_text()
        self.ed._plain_btn.setChecked(True)
        self.ed._editor.setPlainText(text)
        self.ed._blocks_btn.setChecked(True)
        return self.ed._block_manager.blocks

    def _select(self, block):
        self.ed._selected_block_id = block.id
        self.ed._update_properties_panel()

    def test_character_combo_marks_manual_edit(self):
        """§7 DEFECT A detector (UI level): assigning a Character via the
        combo marks the block manually_edited → has_manual_edits True."""
        blocks = self._setup_blocks()
        self.assertFalse(self.ed._block_manager.has_manual_edits)
        self._select(blocks[0])
        idx = self.ed._character_combo.findData("char-anna")
        self.assertGreaterEqual(idx, 0)
        self.ed._character_combo.setCurrentIndex(idx)
        self.assertEqual(blocks[0].character_id, "char-anna")
        self.assertTrue(blocks[0].manually_edited,
                        "a Character assignment alone must be recognised "
                        "as manual semantic state")
        self.assertTrue(self.ed._block_manager.has_manual_edits)

    def test_direct_rebuild_shows_dialog_when_character_only(self):
        """§7/§2A: Character-only assignment + Analyze Again → the DIALOG
        path (never the silent direct rebuild). Cancel keeps everything.
        OLD CODE DETECTOR: has_manual_edits was False → the direct branch
        wiped the Character with no dialog at all."""
        blocks = self._setup_blocks()
        blocks[0].character_id = "char-anna"
        ids = [b.id for b in blocks]
        self.ed._on_analyze_again()
        self.assertIn("exec", _DialogStub.calls,
                      "Re-detect must ask when Character state exists")
        self.assertEqual([b.id for b in self.ed._block_manager.blocks], ids)
        self.assertEqual(
            self.ed._block_manager.blocks[0].character_id, "char-anna")

    def test_sfx_only_state_also_gates_dialog(self):
        """Loaded SFX metadata alone also gates the direct rebuild."""
        blocks = self._setup_blocks()
        blocks[0].sfx_insertions = [SfxInsertion("Sigh", "Ahh", 3)]
        ids = [b.id for b in blocks]
        self.ed._on_analyze_again()
        self.assertIn("exec", _DialogStub.calls)
        self.assertEqual([b.id for b in self.ed._block_manager.blocks], ids)
        self.assertEqual(
            self.ed._block_manager.blocks[0].sfx_insertions[0].sfx_name,
            "Sigh")

    def test_pristine_blocks_still_rebuild_silently(self):
        """Control (§18 18): semantic-free blocks keep the historical
        direct rebuild UX — no dialog, structure rebuilt."""
        blocks = self._setup_blocks()
        ids = [b.id for b in blocks]
        self.ed._on_analyze_again()
        self.assertEqual(_DialogStub.calls, [])
        new_ids = [b.id for b in self.ed._block_manager.blocks]
        self.assertNotEqual(new_ids, ids)

    def test_save_path_via_dialog_preserves_character(self):
        """Matrix 19: the dialog's Save option dispatches to
        preserve_overrides and keeps the Characters."""
        blocks = self._setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        _DialogStub.exec_result = _DialogStub.StandardButton.Save
        self.ed._on_analyze_again()
        chars = [b.character_id for b in self.ed._block_manager.blocks]
        self.assertIn("char-anna", chars)
        self.assertIn("char-mark", chars)

    def test_apply_path_via_dialog_preserves_locked(self):
        """Matrix 21: the dialog's Apply option (locked blocks present)
        dispatches to rebuild_automatic_only and keeps the locked block."""
        blocks = self._setup_blocks()
        blocks[0].locked = True
        blocks[0].character_id = "char-anna"
        _DialogStub.exec_result = _DialogStub.StandardButton.Apply
        self.ed._on_analyze_again()
        nb = self.ed._block_manager.blocks
        locked = [b for b in nb if b.locked]
        self.assertEqual(len(locked), 1)
        self.assertEqual(locked[0].character_id, "char-anna")

    def test_discard_path_destroys_after_confirm(self):
        """Matrix path control: Discard (confirmed) is the EXPLICIT
        destruction path — semantics are dropped by user decision."""
        blocks = self._setup_blocks()
        blocks[0].character_id = "char-anna"
        _DialogStub.exec_result = _DialogStub.StandardButton.Discard
        # question_result defaults to Yes (the user confirms).
        self.ed._on_analyze_again()
        self.assertIn("question", _DialogStub.calls)
        for b in self.ed._block_manager.blocks:
            self.assertIsNone(b.character_id)

    def test_character_survives_save_with_substitution_edit(self):
        """Real-editor control: an in-block edit goes through
        on_text_changed offset tracking → the old block's extracted text
        already reflects the edit → exact-match preservation."""
        blocks = self._setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        # Substitute inside block A (containment breaks in the raw text,
        # but the offset tracker re-anchors the old block first).
        self.ed._editor.setPlainText("Sentences A.\n\nSentence B.")
        _DialogStub.exec_result = _DialogStub.StandardButton.Save
        self.ed._on_analyze_again()
        mgr = self.ed._block_manager
        text = self.ed._editor.toPlainText()
        found = {}
        for b in mgr.blocks:
            btext = text[b.start_offset:b.end_offset]
            if "Sentences A" in btext:
                found["A"] = b.character_id
            if "Sentence B" in btext:
                found["B"] = b.character_id
        self.assertEqual(found.get("A"), "char-anna")
        self.assertEqual(found.get("B"), "char-mark")

    def test_character_survives_save_with_stale_last_text(self):
        """Real-editor DEFECT B detector: text swapped under the scene
        load guard (manager's _last_text stays STALE — the documented
        legacy-offset session scenario) → the old block's extracted text
        differs by substitution → difflib match keeps the Character.
        OLD CODE DETECTOR: containment-only matching lost it silently."""
        blocks = self._setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        # Swap the text with the load guard active (no offset tracking).
        self.ed._loading_scene = True
        try:
            self.ed._editor.setPlainText("Sentence Z.\n\nSentence B.")
        finally:
            self.ed._loading_scene = False
        _DialogStub.exec_result = _DialogStub.StandardButton.Save
        self.ed._on_analyze_again()
        mgr = self.ed._block_manager
        text = self.ed._editor.toPlainText()
        found = {}
        for b in mgr.blocks:
            btext = text[b.start_offset:b.end_offset]
            if "Sentence Z" in btext:
                found["A"] = b.character_id
            if "Sentence B" in btext:
                found["B"] = b.character_id
        self.assertEqual(found.get("A"), "char-anna",
                         "Character must survive a substitution edit when "
                         "the manager's last text is stale")
        self.assertEqual(found.get("B"), "char-mark")


if __name__ == "__main__":
    unittest.main()
