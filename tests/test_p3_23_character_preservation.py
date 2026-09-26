"""
SpeechStudio — P3.23 Character Preservation Runtime Tests
==========================================================

RUNTIME tests (real classes, no AST shortcuts) for the Character system
fixes required by the CHARACTERS + SCENE ASSEMBLY design record:

  §11   PromptBlock.lost_character_id field + serialization
  §14   Scene.character_ids auto-add on block assignment (MainWindow)
  §19   Managed Mode: Character supplies Voice; speaker_voice_map NOT
         consulted for that block (Generate Long precedence)
  §23   Speaker populated with Character name when Character exists
         without $SPEAKER; $SPEAKER remains context when both exist
  §25   Re-detect / Split / Merge / Replace preserve Character; ambiguous
         re-detect records lost_character_id (never silent loss)
  §10   Deleted Character: no orphan character_id (demoted to
         lost_character_id; no real Character color)
  §22   SplitPart.character_id → BatchJob.character_id chain
  §31   Character delete cleans block references (MainWindow cleanup)

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_23_character_preservation.py -v
"""

from __future__ import annotations
import os
import sys
import unittest
import tempfile
import shutil

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

# Keep QMessageBox from blocking tests.
from PySide6.QtWidgets import QMessageBox
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.question = staticmethod(
    lambda *a, **k: QMessageBox.StandardButton.No)


# ===========================================================================
# 1. PromptBlock.lost_character_id (§11)
# ===========================================================================

class TestLostCharacterField(unittest.TestCase):
    """§11: lost_character_id exists, defaults None, round-trips."""

    def test_field_defaults(self):
        from engine.narration_blocks import PromptBlock
        b = PromptBlock()
        self.assertIsNone(b.character_id)
        self.assertIsNone(b.lost_character_id)

    def test_serialization_round_trip(self):
        from engine.narration_blocks import PromptBlock
        b = PromptBlock(character_id="char_abc", lost_character_id="char_xyz")
        d = b.to_dict()
        self.assertEqual(d["character_id"], "char_abc")
        self.assertEqual(d["lost_character_id"], "char_xyz")
        b2 = PromptBlock.from_dict(d)
        self.assertEqual(b2.character_id, "char_abc")
        self.assertEqual(b2.lost_character_id, "char_xyz")

    def test_old_blocks_backward_compatible(self):
        from engine.narration_blocks import PromptBlock
        # Old serialized block WITHOUT the new fields → None defaults.
        b = PromptBlock.from_dict({
            "id": "oldblock", "start_offset": 0, "end_offset": 10,
            "emotion": "Awe", "style": None, "speed": None,
            "pitch": None, "delivery": None,
            "sfx_insertions": [], "pause_insertions": [],
            "label": None, "locked": False, "manually_edited": False,
            "character_id": "char_old",
        })
        self.assertEqual(b.character_id, "char_old")
        self.assertIsNone(b.lost_character_id)


# ===========================================================================
# 2. Re-detect / Split / Merge preservation (§25)
# ===========================================================================

class TestReDetectPreservation(unittest.TestCase):
    """§25: Character assignments survive Re-detect / Split / Merge."""

    def setUp(self):
        from engine.narration_block_manager import NarrationBlockManager
        from engine.narration_blocks import PromptBlock
        self.mgr = NarrationBlockManager()
        # Two paragraphs → 2 blocks via auto-detect.
        self.text = ("First paragraph of narration text here.\n\n"
                     "Second paragraph with other content.\n\n")
        self.mgr.auto_detect(self.text)
        self.assertGreaterEqual(len(self.mgr.blocks), 2)
        # Assign characters to both blocks.
        self.mgr.blocks[0].character_id = "char_engineer"
        self.mgr.blocks[1].character_id = "char_lady"
        self.mgr.blocks[0].emotion = "Awe"
        self.mgr.blocks[1].emotion = "Affection"

    def test_re_detect_preserve_overrides_transfers_character(self):
        """Unchanged text + preserve_overrides → Character transfers."""
        self.mgr.preserve_overrides(self.text)
        chars = [b.character_id for b in self.mgr.blocks]
        self.assertIn("char_engineer", chars)
        self.assertIn("char_lady", chars)

    def test_re_detect_similarity_match_transfers_character(self):
        """Small append-only text change (containment >= 80%, the same
        strategy used for semantic overrides) → Character transfers (§25)."""
        changed = ("First paragraph of narration text here. More.\n\n"
                   "Second paragraph with other content. Plus.\n\n")
        self.mgr.preserve_overrides(changed)
        chars = [b.character_id for b in self.mgr.blocks]
        self.assertIn("char_engineer", chars)
        self.assertIn("char_lady", chars)

    def test_re_detect_locked_block_preserves_character(self):
        """§25: Locked block → Character preserved via rebuild_automatic_only."""
        self.mgr.blocks[0].locked = True
        self.mgr.rebuild_automatic_only(self.text)
        # The locked block's text still exists → character preserved.
        chars = [b.character_id for b in self.mgr.blocks]
        self.assertIn("char_engineer", chars)

    def test_re_detect_ambiguity_records_lost_character(self):
        """§25: block text changed enough that the containment score falls
        BELOW the 80% threshold (related but unreliable) → lost_character_id
        recorded, character_id cleared — NEVER silent loss."""
        # Old text is still contained (append-only), but the appended
        # material is large → containment score far below 80% → ambiguous.
        unrelated = (
            "First paragraph of narration text here. The paragraph "
            "continues with considerable additional text. It goes on and "
            "on with more words. Eventually the similarity falls below "
            "the threshold.\n\n"
            "Second paragraph with other content. This one as well "
            "received a very large amount of new appended text. It keeps "
            "going with further sentences.\n\n")
        self.mgr.preserve_overrides(unrelated)
        lost = [b.lost_character_id for b in self.mgr.blocks
                if b.lost_character_id]
        self.assertTrue(lost,
                        "Expected lost_character_id to be recorded for the "
                        "unreliably-matched Character — silent loss is "
                        "forbidden (§25).")
        self.assertIn("char_engineer", lost)
        self.assertIn("char_lady", lost)
        # No block carries an ACTIVE character that wasn't reliably matched.
        for b in self.mgr.blocks:
            self.assertIsNone(b.character_id)

    def test_split_preserves_character_on_both_blocks(self):
        """§25: Split → the new block inherits the Character."""
        blk = self.mgr.blocks[0]
        blk.character_id = "char_engineer"
        mid = (blk.start_offset + blk.end_offset) // 2
        self.assertTrue(self.mgr.split_block(blk.id, mid))
        blocks = self.mgr.blocks
        self.assertEqual(len(blocks), 3)
        # Block 0 and the new block 1 (the split second half) both carry it.
        self.assertEqual(blocks[0].character_id, "char_engineer")
        self.assertEqual(blocks[1].character_id, "char_engineer")

    def test_merge_inherits_character_when_survivor_has_none(self):
        """§25: Merge → survivor without Character inherits the removed
        block's Character."""
        # Block above has NO character; block below HAS one.
        self.mgr.blocks[0].character_id = None
        below = self.mgr.blocks[1]
        below.character_id = "char_lady"
        self.assertTrue(self.mgr.merge_with_above(below.id))
        # Survivor is blocks[0]; it must now carry char_lady.
        self.assertEqual(self.mgr.blocks[0].character_id, "char_lady")

    def test_merge_conflict_records_lost(self):
        """§25: Merge with DIFFERENT Characters on both → survivor keeps its
        own; the removed block's Character is recorded as lost (no silent
        loss)."""
        self.mgr.blocks[0].character_id = "char_engineer"
        below = self.mgr.blocks[1]
        below.character_id = "char_lady"
        self.assertTrue(self.mgr.merge_with_above(below.id))
        self.assertEqual(self.mgr.blocks[0].character_id, "char_engineer")
        self.assertEqual(self.mgr.blocks[0].lost_character_id, "char_lady")

    def test_transfer_clears_stale_lost_warning(self):
        """A successful match clears any previous lost_character_id."""
        self.mgr.blocks[0].lost_character_id = "char_stale"
        self.mgr.preserve_overrides(self.text)
        for b in self.mgr.blocks:
            self.assertIsNone(b.lost_character_id)


# ===========================================================================
# 3. Multi-speaker splitter propagation (§19/§23)
# ===========================================================================

class TestSplitterCharacterPropagation(unittest.TestCase):
    """§19/§23: $SPEAKER parts inherit the overlapping block's Character."""

    def _make_block(self, start, end, char_id):
        from engine.narration_blocks import PromptBlock
        return PromptBlock(start_offset=start, end_offset=end,
                           character_id=char_id)

    def _split(self, text, blocks, detect_speakers=False):
        from engine.narration_splitter import NarrationSplitter
        return NarrationSplitter().split(
            text, blocks=blocks,
            global_emotion=None, global_style=None,
            global_speed="Normal", global_pitch="Normal",
            global_delivery="Normal",
            detect_speakers=detect_speakers)

    def test_speaker_part_gets_block_character(self):
        """§19: $SPEAKER dialogue lines covered by a Character block → the
        Character propagates to every SplitPart (speaker map cannot
        override it). The app's $SPEAKER syntax is a STANDALONE declaration
        line followed by the spoken text."""
        text = ("$CAPTAIN:\nWe have to leave now.\n\n"
                "$ENGINEER:\nThe engine is not ready.\n\n")
        # One block covering the whole text with a Character.
        blocks = [self._make_block(0, len(text), "char_engineer")]
        parts = self._split(text, blocks, detect_speakers=True)
        self.assertGreaterEqual(len(parts), 2)
        for p in parts:
            # §19: the block Character is authoritative for EVERY part
            # covered by the block — the speaker_voice_map must not be
            # able to override it later.
            self.assertEqual(getattr(p, "character_id", None),
                             "char_engineer")
            # $SPEAKER remains speaker context.
            self.assertTrue(p.speaker in ("CAPTAIN", "ENGINEER"))

    def test_block_part_character_survives(self):
        """Block path (no $SPEAKER): character_id carried on SplitPart."""
        text = ("First paragraph of narration text here.\n\n"
                "Second paragraph with other content.\n\n")
        blocks = [self._make_block(0, 30, "char_a"),
                  self._make_block(30, len(text), "char_b")]
        parts = self._split(text, blocks)
        got = {getattr(p, "character_id", None) for p in parts}
        self.assertIn("char_a", got)
        self.assertIn("char_b", got)


# ===========================================================================
# 4. Generate Long voice resolution chain (§19/§23) — real MainWindow
# ===========================================================================

class TestGenerateLongVoiceResolution(unittest.TestCase):
    """RUNTIME: real MainWindow + real splitter → BatchJob voice/character.

    Verifies the approved precedence (design record §19/§23):
        Block Character Voice  >  speaker_voice_map  >  dropdown default
    and §23: speaker populated with Character name when no $SPEAKER.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="ss_p323_gl_")
        from unittest.mock import patch, PropertyMock
        from engine.engine import Engine
        cls.engine = Engine(app_root=cls.tmpdir)
        # Bypass the model-load gate without touching the Engine class
        # globally: patch the instance's ModelManager class flag.
        cls._patcher = patch.object(
            type(cls.engine._model), "is_loaded",
            new_callable=PropertyMock, return_value=True)
        cls._patcher.start()
        from ui.main_window import MainWindow
        cls.win = MainWindow(cls.engine)

    @classmethod
    def tearDownClass(cls):
        try:
            cls._patcher.stop()
        except Exception:
            pass
        try:
            cls.win.close()
        except Exception:
            pass
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _setup_project(self):
        """Active project with two characters having distinct voices."""
        from engine.models import Character
        proj = self.win._active_project
        # Two fake voice profiles via engine.
        v1 = self.engine.create_voice("Male Deep")
        v2 = self.engine.create_voice("Female Soft")
        c1 = Character(name="Engineer", voice_profile_id=v1.id)
        c2 = Character(name="Lady", voice_profile_id=v2.id)
        proj.characters = [c1, c2]
        return proj, c1, c2, v1, v2

    def _split_parts(self, text, blocks, detect_speakers=False,
                     speaker_voice_map=None):
        from engine.narration_splitter import NarrationSplitter
        return NarrationSplitter().split(
            text, blocks=blocks,
            global_emotion=None, global_style=None,
            global_speed="Normal", global_pitch="Normal",
            global_delivery="Normal",
            detect_speakers=detect_speakers,
            speaker_voice_map=speaker_voice_map)

    def _run_resolution(self, text, blocks, speaker_voice_map=None):
        """Run _start_long_narration with generation start no-op'd and
        return the created BatchJobs (REAL resolution chain)."""
        from engine.models import GenerationParameters
        from engine.batch_manager import BatchManager

        parts = self._split_parts(
            text, blocks,
            detect_speakers=bool(speaker_voice_map),
            speaker_voice_map=speaker_voice_map)
        self.assertGreaterEqual(len(parts), 1)

        # Capture jobs instead of starting real generation.
        captured = {}
        orig_start = BatchManager.start
        orig_show = None
        try:
            BatchManager.start = lambda self_: captured.setdefault(
                "jobs", list(self_._jobs)) or None
            import ui.panels.batch_generation as bg
            orig_show = bg.BatchGenerationDialog.show
            bg.BatchGenerationDialog.show = lambda self_: None

            self.win._start_long_narration(
                parts,
                default_voice_id="voice_dropdown_default",
                base_params=GenerationParameters(),
                speaker_voice_map=speaker_voice_map or {},
            )
        finally:
            BatchManager.start = orig_start
            if orig_show is not None:
                import ui.panels.batch_generation as bg
                bg.BatchGenerationDialog.show = orig_show
        return captured.get("jobs", [])

    def test_character_beats_speaker_map_and_dropdown(self):
        """§19: block Character's voice wins over speaker map AND dropdown."""
        proj, c1, c2, v1, v2 = self._setup_project()
        text = "$CAPTAIN: We must go.\n$ENGINEER: Not yet.\n"
        from engine.narration_blocks import PromptBlock
        blocks = [PromptBlock(start_offset=0, end_offset=len(text),
                              character_id=c1.id)]
        jobs = self._run_resolution(
            text, blocks,
            speaker_voice_map={"CAPTAIN": "voice_mapped_speaker"})
        self.assertTrue(jobs)
        for j in jobs:
            self.assertEqual(j.voice_id, v1.id,
                             "Character voice must win over speaker map")
            self.assertEqual(j.character_id, c1.id)
        # Close any batch dialog opened.
        self._close_batch_dialog()

    def test_character_voice_equal_to_default_still_wins(self):
        """§19 edge case (P3.22 bug): Character whose voice EQUALS the
        dropdown default must still NOT be overridden by speaker map."""
        proj, c1, c2, v1, v2 = self._setup_project()
        text = "$CAPTAIN: We must go.\n"
        from engine.narration_blocks import PromptBlock
        blocks = [PromptBlock(start_offset=0, end_offset=len(text),
                              character_id=c1.id)]
        # Character voice == default → old code let the speaker map win.
        jobs = self._run_resolution_with_default(
            text, blocks, v1.id, {"CAPTAIN": "voice_mapped_speaker"})
        self.assertTrue(jobs)
        for j in jobs:
            self.assertEqual(j.voice_id, v1.id)
            self.assertEqual(j.character_id, c1.id)
        self._close_batch_dialog()

    def _run_resolution_with_default(self, text, blocks, default_voice_id,
                                     speaker_voice_map):
        from engine.models import GenerationParameters
        from engine.batch_manager import BatchManager
        parts = self._split_parts(
            text, blocks,
            detect_speakers=bool(speaker_voice_map),
            speaker_voice_map=speaker_voice_map)
        captured = {}
        orig_start = BatchManager.start
        import ui.panels.batch_generation as bg
        orig_show = bg.BatchGenerationDialog.show
        try:
            BatchManager.start = lambda self_: captured.setdefault(
                "jobs", list(self_._jobs)) or None
            bg.BatchGenerationDialog.show = lambda self_: None
            self.win._start_long_narration(
                parts, default_voice_id,
                GenerationParameters(), speaker_voice_map)
        finally:
            BatchManager.start = orig_start
            bg.BatchGenerationDialog.show = orig_show
        return captured.get("jobs", [])

    def test_speaker_map_used_when_no_character(self):
        """§23: no Character → speaker map provides the voice."""
        proj, c1, c2, v1, v2 = self._setup_project()
        text = "$CAPTAIN:\nWe must go.\n\n"
        from engine.narration_blocks import PromptBlock
        blocks = [PromptBlock(start_offset=0, end_offset=len(text))]
        jobs = self._run_resolution_with_default(
            text, blocks, "voice_dropdown_default",
            {"CAPTAIN": "voice_mapped_speaker"})
        self.assertTrue(jobs)
        for j in jobs:
            self.assertEqual(j.voice_id, "voice_mapped_speaker")
        self._close_batch_dialog()

    def test_speaker_populated_with_character_name(self):
        """§23: Character without $SPEAKER → speaker == Character name."""
        proj, c1, c2, v1, v2 = self._setup_project()
        text = "A plain narration paragraph without speakers.\n\n"
        from engine.narration_blocks import PromptBlock
        blocks = [PromptBlock(start_offset=0, end_offset=len(text),
                              character_id=c1.id)]
        jobs = self._run_resolution_with_default(
            text, blocks, "voice_dropdown_default", {})
        self.assertTrue(jobs)
        for j in jobs:
            self.assertEqual(j.speaker, "Engineer",
                             "speaker must be populated with the Character "
                             "name for context and History (§23)")
            self.assertEqual(j.character_id, c1.id)
        self._close_batch_dialog()

    def test_batchjob_character_not_polluted_by_sidebar(self):
        """Sidebar-selected Character must NOT attach to parts without a
        block Character (the P3.22 `or self._selected_character_id` bug)."""
        proj, c1, c2, v1, v2 = self._setup_project()
        self.win._selected_character_id = c2.id  # sidebar viewing context
        text = "A plain narration paragraph without speakers.\n\n"
        from engine.narration_blocks import PromptBlock
        blocks = [PromptBlock(start_offset=0, end_offset=len(text))]
        jobs = self._run_resolution_with_default(
            text, blocks, "voice_dropdown_default", {})
        self.assertTrue(jobs)
        for j in jobs:
            self.assertIsNone(j.character_id,
                              "Parts without a block Character must not "
                              "inherit the sidebar viewing selection.")
        self.win._selected_character_id = None
        self._close_batch_dialog()

    def _close_batch_dialog(self):
        dlg = getattr(self.win, "_batch_dialog", None)
        if dlg is not None:
            try:
                dlg.deleteLater()
            except Exception:
                pass
            self.win._batch_dialog = None


# ===========================================================================
# 5. duplicate_job keeps character context (§22)
# ===========================================================================

class TestDuplicateJobCharacter(unittest.TestCase):
    def test_duplicate_keeps_character_context(self):
        from engine.batch_manager import BatchManager, BatchJob
        from engine.models import GenerationParameters
        bm = BatchManager(submit_fn=lambda req: None)
        job = BatchJob(
            name="Engineer: 1", prompt="hello", voice_id="v1",
            parameters=GenerationParameters(),
            project="Proj", speaker="Engineer",
            scene_id="scene_1", scene_name="S1",
            character_id="char_engineer")
        bm.add_job(job)
        idx = bm.duplicate_job(0)
        self.assertIsNotNone(idx)
        clone = bm._jobs[idx]
        self.assertEqual(clone.character_id, "char_engineer")
        self.assertEqual(clone.scene_id, "scene_1")
        self.assertEqual(clone.speaker, "Engineer")


# ===========================================================================
# 6. MainWindow: Scene.character_ids auto-add + delete cleanup (§14/§10/§31)
# ===========================================================================

class TestMainWindowCharacterSync(unittest.TestCase):
    """RUNTIME: real MainWindow — scene sync + stale cleanup."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="ss_p323_sync_")
        from engine.engine import Engine
        cls.engine = Engine(app_root=cls.tmpdir)
        from ui.main_window import MainWindow
        cls.win = MainWindow(cls.engine)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.win.close()
        except Exception:
            pass
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_sync_scene_characters_auto_add(self):
        """§14: assigning a Character to a block adds it to the Scene."""
        from engine.models import Character
        from engine.narration_blocks import PromptBlock
        proj = self.win._active_project
        scene = self.win._active_scene
        char = Character(name="Engineer")
        proj.characters = [char]
        # Put a block with the character into the editor's manager.
        text = "Some narration text for the block.\n\n"
        self.win._editor.set_text(text)
        self.win._editor._block_manager._blocks = [
            PromptBlock(start_offset=0, end_offset=len(text),
                        character_id=char.id)]
        self.win._editor.set_characters(
            [{"id": char.id, "name": char.name}])
        scene.character_ids = []
        # Trigger the sync (blocks_changed path).
        self.win._sync_scene_characters()
        self.assertIn(char.id, scene.character_ids)

    def test_cleanup_stale_block_characters(self):
        """§10/§31: deleted Character → block refs demoted to
        lost_character_id, no active character_id remains."""
        from engine.models import Character
        from engine.narration_blocks import PromptBlock
        proj = self.win._active_project
        char = Character(name="Ghost")
        proj.characters = [char]
        text = "Some narration text for the block.\n\n"
        self.win._editor._block_manager._blocks = [
            PromptBlock(start_offset=0, end_offset=len(text),
                        character_id=char.id)]
        # Simulate the delete: remove the character, refresh editor.
        proj.characters = []
        self.win._update_editor_characters()
        blocks = self.win._editor.block_manager.blocks
        self.assertTrue(blocks)
        for b in blocks:
            self.assertIsNone(b.character_id,
                              "orphan character_id must be cleared")
            self.assertEqual(b.lost_character_id, char.id,
                             "demoted to lost_character_id (no silent loss)")


if __name__ == "__main__":
    unittest.main()
