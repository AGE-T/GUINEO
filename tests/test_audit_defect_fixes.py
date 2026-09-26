"""
SpeechStudio — End-to-End Audit Defect Fix Runtime Tests
=========================================================

P3.8: Real runtime integration tests for the 6 confirmed audit defects.

These tests require PySide6 + a QApplication. They instantiate the REAL
active widgets (RightPanel, ExportProjectDialog) — NOT legacy inactive code.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_audit_defect_fixes.py -v
"""

from __future__ import annotations
import os
import sys
import json
import shutil
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Ensure offscreen Qt platform for headless CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

# Single QApplication instance for all tests
_app = QApplication.instance() or QApplication([])


# ===========================================================================
# C1: compile_continuous None emotion/style transitions
# ===========================================================================

class TestC1CompileContinuousNoneTransitions(unittest.TestCase):
    """C1 FIX: compile_continuous must NOT crash on None emotion/style transitions."""

    def _make_block(self, start, end, emotion=None, style=None):
        from engine.narration_blocks import PromptBlock
        return PromptBlock(
            start_offset=start,
            end_offset=end,
            emotion=emotion,
            style=style,
        )

    def _compile(self, text, blocks, global_emotion=None, global_style=None):
        from engine.prompt_state import CanonicalPromptCompiler
        return CanonicalPromptCompiler.compile_continuous(
            text=text,
            blocks=blocks,
            global_emotion=global_emotion,
            global_style=global_style,
            allow_sfx=True,
        )

    def test_none_to_fear(self):
        """global_emotion=None, block 2 has emotion=Fear → should emit, no crash."""
        # Two blocks: "hello" and "world"
        text = "hello world"
        blocks = [
            self._make_block(0, 5, emotion=None),
            self._make_block(6, 11, emotion="Fear"),
        ]
        result = self._compile(text, blocks, global_emotion=None)
        self.assertIn("<|emotion:fear|>", result.final_prompt)
        self.assertNotIn("<|emotion:none|>", result.final_prompt)

    def test_fear_to_none(self):
        """Block 1 emotion=Fear, Block 2 emotion=None, global=None → no crash."""
        text = "hello world"
        blocks = [
            self._make_block(0, 5, emotion="Fear"),
            self._make_block(6, 11, emotion=None),
        ]
        result = self._compile(text, blocks, global_emotion=None)
        self.assertIn("<|emotion:fear|>", result.final_prompt)
        self.assertNotIn("<|emotion:none|>", result.final_prompt)
        self.assertNotIn("<|emotion:None|>", result.final_prompt)

    def test_none_fear_none(self):
        """None → Fear → None: full round-trip, no crash."""
        text = "aaa bbb ccc"
        blocks = [
            self._make_block(0, 3, emotion=None),
            self._make_block(4, 7, emotion="Fear"),
            self._make_block(8, 11, emotion=None),
        ]
        result = self._compile(text, blocks, global_emotion=None)
        self.assertIn("<|emotion:fear|>", result.final_prompt)

    def test_none_to_whispering(self):
        """None → Whispering: should emit <|style:whispering|>."""
        text = "hello world"
        blocks = [
            self._make_block(0, 5, style=None),
            self._make_block(6, 11, style="Whispering"),
        ]
        result = self._compile(text, blocks, global_style=None)
        self.assertIn("<|style:whispering|>", result.final_prompt)

    def test_whispering_to_none(self):
        """Whispering → None: no crash, no invalid token."""
        text = "hello world"
        blocks = [
            self._make_block(0, 5, style="Whispering"),
            self._make_block(6, 11, style=None),
        ]
        result = self._compile(text, blocks, global_style=None)
        self.assertIn("<|style:whispering|>", result.final_prompt)
        self.assertNotIn("<|style:none|>", result.final_prompt)

    def test_none_whispering_none(self):
        """None → Whispering → None: full round-trip."""
        text = "aaa bbb ccc"
        blocks = [
            self._make_block(0, 3, style=None),
            self._make_block(4, 7, style="Whispering"),
            self._make_block(8, 11, style=None),
        ]
        result = self._compile(text, blocks, global_style=None)
        self.assertIn("<|style:whispering|>", result.final_prompt)

    def test_no_crash_on_all_none(self):
        """All-None states: should produce plain text, no crash."""
        text = "hello world"
        blocks = [
            self._make_block(0, 5),
            self._make_block(6, 11),
        ]
        result = self._compile(text, blocks, global_emotion=None, global_style=None)
        self.assertIn("hello", result.final_prompt)
        self.assertIn("world", result.final_prompt)
        # No HIGGS tokens at all
        self.assertNotIn("<|", result.final_prompt)


# ===========================================================================
# C2: BatchJob.to_request() field propagation
# ===========================================================================

class TestC2BatchJobContextPropagation(unittest.TestCase):
    """C2 FIX: BatchJob.to_request() must forward speaker/scene/character context."""

    def test_to_request_forwards_speaker(self):
        """speaker field must survive BatchJob → GenerationRequest."""
        from engine.batch_manager import BatchJob
        job = BatchJob(
            name="Test",
            prompt="hello",
            voice_id="vp_1",
            speaker="ENGINEER",
        )
        req = job.to_request()
        self.assertEqual(req.speaker, "ENGINEER")

    def test_to_request_forwards_scene_id(self):
        """scene_id must survive BatchJob → GenerationRequest."""
        from engine.batch_manager import BatchJob
        job = BatchJob(
            name="Test",
            prompt="hello",
            scene_id="scene_123",
            scene_name="Scene 01",
        )
        req = job.to_request()
        self.assertEqual(req.scene_id, "scene_123")
        self.assertEqual(req.scene_name, "Scene 01")

    def test_to_request_forwards_character_id(self):
        """character_id must survive BatchJob → GenerationRequest."""
        from engine.batch_manager import BatchJob
        job = BatchJob(
            name="Test",
            prompt="hello",
            character_id="char_456",
        )
        req = job.to_request()
        self.assertEqual(req.character_id, "char_456")

    def test_to_request_forwards_all_context(self):
        """All context fields forwarded together."""
        from engine.batch_manager import BatchJob
        job = BatchJob(
            name="Full",
            prompt="hello",
            voice_id="vp_1",
            speaker="LADY",
            scene_id="scene_abc",
            scene_name="Chapter 3",
            character_id="char_def",
        )
        req = job.to_request()
        self.assertEqual(req.speaker, "LADY")
        self.assertEqual(req.scene_id, "scene_abc")
        self.assertEqual(req.scene_name, "Chapter 3")
        self.assertEqual(req.character_id, "char_def")

    def test_batchjob_round_trip_preserves_context(self):
        """BatchJob.to_dict() → from_dict() preserves all context fields."""
        from engine.batch_manager import BatchJob
        job = BatchJob(
            name="RT",
            prompt="hello",
            speaker="ENGINEER",
            scene_id="scene_1",
            scene_name="Scene 1",
            character_id="char_1",
        )
        data = job.to_dict()
        restored = BatchJob.from_dict(data)
        self.assertEqual(restored.speaker, "ENGINEER")
        self.assertEqual(restored.scene_id, "scene_1")
        self.assertEqual(restored.scene_name, "Scene 1")
        self.assertEqual(restored.character_id, "char_1")


# ===========================================================================
# C5: GenerationRequest Character/Speaker propagation
# ===========================================================================

class TestC5CharacterSpeakerPropagation(unittest.TestCase):
    """C5 FIX: Normal Generate must propagate character_id + speaker.

    Since we can't easily instantiate the full MainWindow (requires Engine,
    model loading, etc.), we test the GenerationRequest dataclass directly
    and verify the field assignment logic that MainWindow uses.
    """

    def test_generation_request_has_character_id_field(self):
        """GenerationRequest must have character_id and speaker fields."""
        from engine.models import GenerationRequest
        req = GenerationRequest(text="hello")
        self.assertTrue(hasattr(req, 'character_id'))
        self.assertTrue(hasattr(req, 'speaker'))
        self.assertIsNone(req.character_id)
        self.assertIsNone(req.speaker)

    def test_character_id_assignment(self):
        """Setting character_id on a request preserves it."""
        from engine.models import GenerationRequest
        req = GenerationRequest(text="hello")
        req.character_id = "char_abc"
        req.speaker = "Engineer"
        self.assertEqual(req.character_id, "char_abc")
        self.assertEqual(req.speaker, "Engineer")

    def test_main_window_has_selected_character_id(self):
        """MainWindow must track _selected_character_id (used for C5 propagation)."""
        import ast
        with open(os.path.join(_ROOT, "ui", "main_window.py")) as f:
            src = f.read()
        self.assertIn("_selected_character_id", src)
        # Verify it's used in the generation request construction
        self.assertIn("request.character_id = self._selected_character_id", src)

    def test_main_window_resolves_character_name_for_speaker(self):
        """MainWindow must resolve the character name for the speaker field."""
        with open(os.path.join(_ROOT, "ui", "main_window.py")) as f:
            src = f.read()
        self.assertIn("request.speaker = char.name", src)

    def test_audioasset_populated_with_context(self):
        """AudioAsset creation must include speaker + character_id +
        generated_at.

        P3.28 SUPERSESSION: this test previously asserted the inline
        AudioAsset construction inside MainWindow's
        _on_generation_finished_ui (getattr(result, 'speaker', ...) etc.).
        P3.28 §23 replaces that inline writer with THE ONE WRITER —
        engine.audio_provenance.register_generation_result — which is
        routed by result.scene_id (closes D-3). The equivalent field
        population now lives there and is asserted below.
        """
        import ast
        with open(os.path.join(_ROOT, "engine", "audio_provenance.py")) as f:
            prov_src = f.read()
        self.assertIn("\"speaker\": getattr(result, \"speaker\", None)",
                      prov_src)
        self.assertIn(
            "\"character_id\": getattr(result, \"character_id\", None)",
            prov_src)
        self.assertIn(
            "\"generated_at\": getattr(result, \"timestamp\", None)",
            prov_src)
        # The ONE WRITER must be called from the generation finished
        # handler (single writer rule, P3.28 §23 / design record §6).
        with open(os.path.join(_ROOT, "ui", "main_window.py")) as f:
            src = f.read()
        self.assertIn("register_generation_result", src)


# ===========================================================================
# H1: ExportProjectDialog construction (REAL QApplication)
# ===========================================================================

class TestH1ExportProjectDialogConstruction(unittest.TestCase):
    """H1 FIX: ExportProjectDialog must construct without crashing."""

    def test_dialog_constructs_successfully(self):
        """The real ExportProjectDialog must instantiate under QApplication."""
        from ui.panels.export_project_dialog import ExportProjectDialog
        from engine.models import Project
        project = Project(name="TestProject")
        dlg = ExportProjectDialog(project, "/tmp", None)
        self.assertIsNotNone(dlg)

    def test_dialog_has_audio_checkbox(self):
        """The dialog must have an 'Include Generated Audio' checkbox."""
        from ui.panels.export_project_dialog import ExportProjectDialog
        from engine.models import Project
        project = Project(name="TestProject")
        dlg = ExportProjectDialog(project, "/tmp", None)
        self.assertTrue(hasattr(dlg, '_audio_check'))
        self.assertTrue(dlg._audio_check.isChecked())  # default ON

    def test_dialog_has_voice_checkbox(self):
        """The dialog must have an 'Include Referenced Voice Assets' checkbox."""
        from ui.panels.export_project_dialog import ExportProjectDialog
        from engine.models import Project
        project = Project(name="TestProject")
        dlg = ExportProjectDialog(project, "/tmp", None)
        self.assertTrue(hasattr(dlg, '_voice_check'))
        self.assertTrue(dlg._voice_check.isChecked())  # default ON

    def test_dialog_has_dest_edit(self):
        """The dialog must have a destination folder QLineEdit."""
        from ui.panels.export_project_dialog import ExportProjectDialog
        from engine.models import Project
        project = Project(name="TestProject")
        dlg = ExportProjectDialog(project, "/tmp", None)
        self.assertTrue(hasattr(dlg, '_dest_edit'))

    def test_dialog_no_setStyleSheet_on_layout(self):
        """No QLayout object should receive setStyleSheet (the H1 bug)."""
        import ast
        with open(os.path.join(_ROOT, "ui", "panels", "export_project_dialog.py")) as f:
            src = f.read()
        # The bug was: dest_layout.setStyleSheet(...) and opts_layout.setStyleSheet(...)
        # These should NOT exist anymore
        self.assertNotIn("dest_layout.setStyleSheet", src)
        self.assertNotIn("opts_layout.setStyleSheet", src)
        # The fix: setStyleSheet on the QGroupBox instead
        self.assertIn("dest_group.setStyleSheet", src)
        self.assertIn("opts_group.setStyleSheet", src)


# ===========================================================================
# M1: HistoryManager.delete audio path resolution
# ===========================================================================

class TestM1HistoryDeleteAudio(unittest.TestCase):
    """M1 FIX: HistoryManager.delete(delete_audio=True) must actually delete audio."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Simulate <app_root>/settings/history/ and <app_root>/outputs/
        self.app_root = self.tmpdir
        self.history_dir = os.path.join(self.tmpdir, "settings", "history")
        self.outputs_dir = os.path.join(self.tmpdir, "outputs")
        os.makedirs(self.history_dir, exist_ok=True)
        os.makedirs(self.outputs_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _create_entry_with_audio(self, entry_id="20240101_120000"):
        """Create a HistoryEntry JSON + a real audio file in outputs/."""
        from engine.history_manager import HistoryManager
        from engine.models import GenerationResult
        # Create the audio file
        audio_rel = "outputs/test_audio.wav"
        audio_abs = os.path.join(self.app_root, audio_rel)
        with open(audio_abs, "wb") as f:
            f.write(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00")
        # Create the history entry
        hm = HistoryManager(self.history_dir)
        result = GenerationResult(
            success=True,
            output_path=audio_rel,
            prompt="test",
            generation_time=1.0,
            output_duration=2.0,
        )
        result.timestamp = entry_id
        result.project = "TestProj"
        hm.add(result)
        return hm, audio_abs

    def test_delete_with_audio_removes_json_and_wav(self):
        """delete_audio=True must delete both the JSON and the WAV file."""
        hm, audio_abs = self._create_entry_with_audio()
        self.assertTrue(os.path.isfile(audio_abs))
        deleted = hm.delete("20240101_120000", delete_audio=True)
        self.assertTrue(deleted)
        self.assertFalse(os.path.isfile(audio_abs),
                          "Audio file should be deleted")
        self.assertFalse(os.path.isfile(os.path.join(self.history_dir, "20240101_120000.json")),
                          "JSON file should be deleted")

    def test_delete_without_audio_keeps_wav(self):
        """delete_audio=False must NOT delete the WAV file."""
        hm, audio_abs = self._create_entry_with_audio()
        deleted = hm.delete("20240101_120000", delete_audio=False)
        self.assertTrue(deleted)
        self.assertTrue(os.path.isfile(audio_abs),
                         "Audio file should still exist")
        self.assertFalse(os.path.isfile(os.path.join(self.history_dir, "20240101_120000.json")),
                          "JSON file should be deleted")

    def test_delete_nonexistent_audio_no_crash(self):
        """Deleting an entry whose audio is missing should not crash."""
        hm, audio_abs = self._create_entry_with_audio()
        # Remove the audio file manually
        os.remove(audio_abs)
        deleted = hm.delete("20240101_120000", delete_audio=True)
        self.assertTrue(deleted)
        # JSON should still be deleted
        self.assertFalse(os.path.isfile(os.path.join(self.history_dir, "20240101_120000.json")))


# ===========================================================================
# C3/C4: Allow SFX + Seed clearing (FALSE POSITIVES — verify active RightPanel)
# ===========================================================================

class TestC3C4AllowSfxAndSeed(unittest.TestCase):
    """Verify the ACTIVE RightPanel handles allow_sfx + seed correctly.

    The audit initially flagged C3 (allow_sfx no-op) and C4 (seed clearing)
    as critical bugs based on the legacy control_panel.py GenerationSection.

    However, the ACTIVE runtime path is RightPanel (imported as ControlPanel).
    The RightPanel contains FriendlyView which has:
      - _allow_sfx state + _allow_sfx_btn UI
      - get_parameters() returning allow_sfx=self._allow_sfx
      - _on_seed_changed that sets _seed=None on empty text
      - set_parameters that clears the seed edit field

    These tests verify the active RightPanel behavior at runtime.
    """

    def test_friendly_view_has_allow_sfx(self):
        """FriendlyView must have _allow_sfx state."""
        from ui.panels.right_panel import FriendlyView
        fv = FriendlyView()
        self.assertTrue(hasattr(fv, '_allow_sfx'))
        self.assertTrue(hasattr(fv, '_allow_sfx_btn'))

    def test_friendly_view_get_parameters_includes_allow_sfx(self):
        """FriendlyView.get_parameters() must return allow_sfx."""
        from ui.panels.right_panel import FriendlyView
        fv = FriendlyView()
        params = fv.get_parameters()
        self.assertTrue(hasattr(params, 'allow_sfx'))
        # Default should be True
        self.assertTrue(params.allow_sfx)

    def test_friendly_view_allow_sfx_toggle(self):
        """Toggling allow_sfx OFF must change get_parameters().allow_sfx."""
        from ui.panels.right_panel import FriendlyView
        fv = FriendlyView()
        # Toggle OFF
        fv._allow_sfx = False
        fv._allow_sfx_btn.setChecked(False)
        params = fv.get_parameters()
        self.assertFalse(params.allow_sfx)

    def test_friendly_view_has_seed_state(self):
        """FriendlyView must have _seed state."""
        from ui.panels.right_panel import FriendlyView
        fv = FriendlyView()
        self.assertTrue(hasattr(fv, '_seed'))
        self.assertTrue(hasattr(fv, '_seed_edit'))

    def test_friendly_view_seed_clearing(self):
        """Clearing the seed edit field must set _seed to None."""
        from ui.panels.right_panel import FriendlyView
        fv = FriendlyView()
        # Set a seed
        fv._seed_edit.setText("77777")
        self.assertEqual(fv._seed, 77777)
        # Clear it
        fv._seed_edit.setText("")
        self.assertIsNone(fv._seed,
                           "Seed should be None after clearing the edit field")

    def test_friendly_view_set_parameters_clears_seed(self):
        """set_parameters with seed=None must clear the seed edit field."""
        from ui.panels.right_panel import FriendlyView
        from engine.models import GenerationParameters
        fv = FriendlyView()
        # Set a seed first
        fv._seed_edit.setText("12345")
        self.assertEqual(fv._seed, 12345)
        # Now set_parameters with seed=None
        params = GenerationParameters(seed=None)
        fv.set_parameters(params)
        self.assertIsNone(fv._seed)
        self.assertEqual(fv._seed_edit.text(), "",
                         "Seed edit field should be cleared")

    def test_right_panel_has_both_views(self):
        """RightPanel must instantiate both AdvancedView and FriendlyView."""
        from ui.panels.right_panel import RightPanel
        rp = RightPanel()
        self.assertTrue(hasattr(rp, '_advanced'))
        self.assertTrue(hasattr(rp, '_friendly'))


# ===========================================================================
# L1: Dead code removal
# ===========================================================================

class TestL1DeadCodeRemoved(unittest.TestCase):
    """L1 FIX: _on_history_entry_selected dead code must be removed."""

    def test_handler_removed(self):
        """The _on_history_entry_selected method must be removed."""
        with open(os.path.join(_ROOT, "ui", "main_window.py")) as f:
            src = f.read()
        self.assertNotIn("def _on_history_entry_selected", src,
                          "Dead handler should be removed")

    def test_connection_removed(self):
        """The signal connection must be removed."""
        with open(os.path.join(_ROOT, "ui", "main_window.py")) as f:
            src = f.read()
        self.assertNotIn(
            "self._sidebar.history_entry_selected.connect(self._on_history_entry_selected)",
            src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
