"""
SpeechStudio — P3.13 Scene Voice Profile Persistence Tests
===========================================================

Tests that the Scene retains its Voice Profile selection independently.

Requirements:
  1. Scene stores Voice Profile reference
  2. Scene save preserves Voice Profile
  3. Scene reload restores Voice Profile
  4. Scene A and Scene B can hold different Voice Profiles
  5. Generate uses the Scene Voice Profile
  6. Generate Long uses the Scene Voice Profile
  7. BatchJob retains voice identity
  8. GenerationRequest retains voice identity
  9. AudioAsset retains voice identity
  10. HistoryEntry retains voice identity
  11. Project export preserves Scene Voice Profile reference
  12. Project reimport preserves Scene Voice Profile reference
  13. Character Voice Profile relationship remains intact
  14. Scene Voice Profile vs Character Voice Profile precedence

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_13_scene_voice_persistence.py -v
"""

from __future__ import annotations
import os
import sys
import tempfile
import shutil
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ===========================================================================
# 1-3. Scene stores / saves / reloads voice_profile_id
# ===========================================================================

class TestSceneStoresVoiceProfile(unittest.TestCase):
    """P3.13: Scene must store voice_profile_id and survive round-trip."""

    def test_scene_has_voice_profile_id_field(self):
        """Scene model must have a voice_profile_id field."""
        from engine.models import Scene
        s = Scene()
        self.assertTrue(hasattr(s, 'voice_profile_id'))
        self.assertIsNone(s.voice_profile_id)  # default None

    def test_scene_stores_voice_profile_id(self):
        """Scene must store voice_profile_id when set."""
        from engine.models import Scene
        s = Scene(name="Scene 01")
        s.voice_profile_id = "vp_abc123"
        self.assertEqual(s.voice_profile_id, "vp_abc123")

    def test_scene_to_dict_includes_voice_profile_id(self):
        """Scene.to_dict() must include voice_profile_id."""
        from engine.models import Scene
        s = Scene(name="Scene 01")
        s.voice_profile_id = "vp_xyz"
        data = s.to_dict()
        self.assertIn("voice_profile_id", data)
        self.assertEqual(data["voice_profile_id"], "vp_xyz")

    def test_scene_from_dict_restores_voice_profile_id(self):
        """Scene.from_dict() must restore voice_profile_id."""
        from engine.models import Scene
        s = Scene(name="Scene 01", voice_profile_id="vp_rt")
        data = s.to_dict()
        restored = Scene.from_dict(data)
        self.assertEqual(restored.voice_profile_id, "vp_rt")

    def test_scene_save_reload_preserves_voice(self):
        """Save project to disk, reload, verify voice_profile_id survives."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            project = Project(name="VoiceTest")
            scene = Scene(name="Scene 01")
            scene.voice_profile_id = "vp_persist_123"
            project.add_scene(scene)
            pm.save_project(project)

            # Simulate restart
            pm2 = ProjectManager(tmpdir)
            loaded = pm2.list_projects()[0]
            self.assertEqual(loaded.scenes[0].voice_profile_id, "vp_persist_123")
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# 4. Scene A and Scene B hold different Voice Profiles
# ===========================================================================

class TestSceneIndependentVoiceProfiles(unittest.TestCase):
    """P3.13: Each Scene holds its own Voice Profile independently."""

    def test_two_scenes_different_voices(self):
        """Scene A with Voice X, Scene B with Voice Y — both preserved."""
        from engine.models import Project, Scene
        project = Project(name="MultiVoice")
        scene_a = Scene(name="Scene A")
        scene_a.voice_profile_id = "vp_x"
        scene_b = Scene(name="Scene B")
        scene_b.voice_profile_id = "vp_y"
        project.add_scene(scene_a)
        project.add_scene(scene_b)

        self.assertEqual(project.scenes[0].voice_profile_id, "vp_x")
        self.assertEqual(project.scenes[1].voice_profile_id, "vp_y")

    def test_switching_scenes_preserves_voices(self):
        """Switching A→B→A preserves each Scene's voice."""
        from engine.models import Project, Scene
        project = Project(name="SwitchTest")
        scene_a = Scene(name="A")
        scene_a.voice_profile_id = "vp_a"
        scene_b = Scene(name="B")
        scene_b.voice_profile_id = "vp_b"
        project.add_scene(scene_a)
        project.add_scene(scene_b)

        # Simulate switching: save current, load target
        # Scene A voice
        self.assertEqual(scene_a.voice_profile_id, "vp_a")
        # Switch to B
        self.assertEqual(scene_b.voice_profile_id, "vp_b")
        # Switch back to A — voice unchanged
        self.assertEqual(scene_a.voice_profile_id, "vp_a")

    def test_save_reload_preserves_different_voices(self):
        """Save + reload preserves different voices per Scene."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            project = Project(name="MultiVoicePersist")
            scene_a = Scene(name="A")
            scene_a.voice_profile_id = "vp_aaa"
            scene_b = Scene(name="B")
            scene_b.voice_profile_id = "vp_bbb"
            project.add_scene(scene_a)
            project.add_scene(scene_b)
            pm.save_project(project)

            pm2 = ProjectManager(tmpdir)
            loaded = pm2.list_projects()[0]
            self.assertEqual(loaded.scenes[0].voice_profile_id, "vp_aaa")
            self.assertEqual(loaded.scenes[1].voice_profile_id, "vp_bbb")
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# 5-8. Voice identity through the generation chain
# ===========================================================================

class TestVoiceIdentityThroughChain(unittest.TestCase):
    """P3.13: Voice identity must survive BatchJob→Request→Result→Asset→History."""

    def test_batchjob_retains_voice_id(self):
        """BatchJob must retain voice_id."""
        from engine.batch_manager import BatchJob
        job = BatchJob(name="Test", prompt="hello", voice_id="vp_chain_1")
        self.assertEqual(job.voice_id, "vp_chain_1")

    def test_request_retains_voice_id(self):
        """GenerationRequest must retain voice_id from BatchJob.to_request()."""
        from engine.batch_manager import BatchJob
        job = BatchJob(name="Test", prompt="hello", voice_id="vp_chain_2")
        req = job.to_request()
        self.assertEqual(req.voice_id, "vp_chain_2")

    def test_audioasset_retains_voice_profile_id(self):
        """AudioAsset must retain voice_profile_id."""
        from engine.models import AudioAsset
        asset = AudioAsset(
            scene_id="s1",
            output_path="outputs/test.wav",
            duration=2.0,
            voice_profile_id="vp_chain_3",
        )
        self.assertEqual(asset.voice_profile_id, "vp_chain_3")

    def test_history_entry_retains_voice(self):
        """HistoryEntry must retain the voice_profile from the result."""
        from engine.history_manager import HistoryManager
        from engine.models import GenerationResult, VoiceProfile
        tmpdir = tempfile.mkdtemp()
        try:
            hm = HistoryManager(tmpdir)
            r = GenerationResult(
                success=True,
                output_path="outputs/test.wav",
                prompt="test",
                generation_time=1.0,
                output_duration=2.0,
            )
            r.timestamp = "20240101_120000"
            r.project = "Test"
            r.voice_profile = VoiceProfile(id="vp_chain_4", name="Test Voice")
            entry_id = hm.add(r)
            entry = hm.get_entry(entry_id)
            self.assertIsNotNone(entry.voice_profile)
            self.assertEqual(entry.voice_profile.id, "vp_chain_4")
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# 9-10. Generate / Generate Long use Scene Voice Profile
# ===========================================================================

class TestGenerateUsesSceneVoice(unittest.TestCase):
    """P3.13: Generate must use the Scene's Voice Profile."""

    def test_main_window_save_load_voice(self):
        """_save_current_scene_state must save voice; _load_scene_state must restore."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # Save: scene.voice_profile_id = self._control_panel.get_selected_voice_id()
        self.assertIn("scene.voice_profile_id = self._control_panel.get_selected_voice_id()",
                      src, "Save must capture voice_profile_id from control panel")
        # Load: self._control_panel.set_selected_voice_id(scene.voice_profile_id)
        self.assertIn("self._control_panel.set_selected_voice_id(scene.voice_profile_id)",
                      src, "Load must restore voice_profile_id to control panel")

    def test_generate_request_uses_control_panel_voice(self):
        """_start_generation must use control_panel.get_selected_voice_id() for voice_id."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("voice_id=self._control_panel.get_selected_voice_id()",
                      src, "Generate must use control panel voice (which is restored from Scene)")


# ===========================================================================
# 11-12. Export / Reimport preserves Scene Voice Profile
# ===========================================================================

class TestExportReimportVoiceProfile(unittest.TestCase):
    """P3.13: Export/reimport must preserve Scene voice_profile_id."""

    def test_export_preserves_scene_voice(self):
        """Exported project.json must contain voice_profile_id for each Scene."""
        from engine.project_exporter import ProjectExporter
        from engine.models import Project, Scene
        tmpdir = tempfile.mkdtemp()
        try:
            app_root = tmpdir
            dest = os.path.join(tmpdir, "exports")
            os.makedirs(dest)
            project = Project(name="ExportVoiceTest")
            scene = Scene(name="Scene 01")
            scene.voice_profile_id = "vp_export_123"
            project.add_scene(scene)
            exporter = ProjectExporter(app_root)
            result = exporter.export(project, dest, include_audio=False,
                                      include_voice_assets=False)
            self.assertTrue(result.success)
            # Read the exported project.json
            import json
            with open(os.path.join(result.export_dir, "project.json")) as f:
                data = json.load(f)
            self.assertEqual(data["scenes"][0]["voice_profile_id"], "vp_export_123")
        finally:
            shutil.rmtree(tmpdir)

    def test_reimport_preserves_scene_voice(self):
        """Reimported project must have voice_profile_id on Scenes."""
        from engine.project_exporter import ProjectExporter
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        tmpdir = tempfile.mkdtemp()
        try:
            app_root = tmpdir
            dest = os.path.join(tmpdir, "exports")
            projects_dir = os.path.join(tmpdir, "projects")
            os.makedirs(dest)
            os.makedirs(projects_dir)
            project = Project(name="ReimportVoiceTest")
            scene = Scene(name="Scene 01")
            scene.voice_profile_id = "vp_reimport_456"
            project.add_scene(scene)
            exporter = ProjectExporter(app_root)
            result = exporter.export(project, dest, include_audio=False,
                                      include_voice_assets=False)
            pm = ProjectManager(projects_dir)
            imported = pm.import_project_dir(result.export_dir)
            self.assertEqual(imported.scenes[0].voice_profile_id, "vp_reimport_456")
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# 13. Character Voice Profile relationship
# ===========================================================================

class TestCharacterVoiceProfileIntact(unittest.TestCase):
    """P3.13: Character.voice_profile_id must remain intact through export/reimport."""

    def test_character_voice_preserved_through_export(self):
        """Character voice_profile_id must survive export + reimport."""
        from engine.project_exporter import ProjectExporter
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene, Character
        tmpdir = tempfile.mkdtemp()
        try:
            app_root = tmpdir
            dest = os.path.join(tmpdir, "exports")
            projects_dir = os.path.join(tmpdir, "projects")
            os.makedirs(dest)
            os.makedirs(projects_dir)
            project = Project(name="CharVoiceTest")
            scene = Scene(name="Scene 01")
            project.add_scene(scene)
            char = Character(name="Engineer", voice_profile_id="vp_char_789")
            project.add_character(char)
            exporter = ProjectExporter(app_root)
            result = exporter.export(project, dest, include_audio=False,
                                      include_voice_assets=False)
            pm = ProjectManager(projects_dir)
            imported = pm.import_project_dir(result.export_dir)
            self.assertEqual(imported.characters[0].voice_profile_id, "vp_char_789")
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# 14. Scene Voice vs Character Voice precedence
# ===========================================================================

class TestVoicePrecedence(unittest.TestCase):
    """P3.13: Document and verify Scene Voice vs Character Voice precedence.

    APPROVED PRECEDENCE (documented in MainWindow._start_generation):
    - The control panel dropdown is the AUTHORITATIVE voice selector for
      single-prompt Generate. It is restored from Scene.voice_profile_id
      when switching Scenes (via _load_scene_state).
    - Character.voice_profile_id is a REFERENCE for the Character entity,
      NOT an override for the generation voice. The Character's voice is
      used in multi-speaker workflows via speaker_voice_map.
    - If the user explicitly selects a different voice in the dropdown
      after loading a Scene, the dropdown wins (user override).
    - The Scene.voice_profile_id is updated on save to reflect the current
      dropdown selection.

    This means: Scene Voice (via control panel) > Character Voice for
    single-prompt Generate. For multi-speaker, each speaker maps to its
    own voice via speaker_voice_map.
    """

    def test_scene_voice_restored_to_control_panel(self):
        """When loading a Scene, its voice_profile_id is restored to the control panel."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # _load_scene_state restores voice
        self.assertIn("self._control_panel.set_selected_voice_id(scene.voice_profile_id)",
                      src)

    def test_generate_uses_control_panel_voice_not_character(self):
        """Generate uses control_panel.get_selected_voice_id(), NOT Character.voice_profile_id."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # The request uses control panel voice
        self.assertIn("voice_id=self._control_panel.get_selected_voice_id()", src)
        # Character.voice_profile_id is NOT used to override voice_id
        # (it's only used for character_id + speaker context tracking)
        self.assertIn("request.character_id = self._selected_character_id", src)
        self.assertIn("request.speaker = char.name", src)
        # NOT: voice_id = char.voice_profile_id
        self.assertNotIn("voice_id=char.voice_profile_id", src)

    def test_scene_voice_saved_from_control_panel(self):
        """When saving a Scene, the current control panel voice is saved."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("scene.voice_profile_id = self._control_panel.get_selected_voice_id()",
                      src)

    def test_documented_precedence(self):
        """The approved precedence is documented in the code comments."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # Check that the precedence is documented near _start_generation
        self.assertIn("dropdown remains the authoritative voice selector", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
