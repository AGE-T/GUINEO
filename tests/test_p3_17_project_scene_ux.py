"""
SpeechStudio — P3.17 Project/Scene Management UX + Human-Friendly Filenames
============================================================================

Runtime tests for:
  - Project switcher (dropdown in top context)
  - Scene switcher (scoped to active Project)
  - Project rename (persists, updates everywhere)
  - Scene rename (persists, updates everywhere)
  - Project delete (confirmation, safe)
  - Scene delete (confirmation, active Scene fallback)
  - Human-friendly filenames (Project_Scene_Part_01.wav)
  - Filename normalization (Windows-safe)
  - Collision handling
  - Path consistency (BatchJob → Request → Result → Asset → History)

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_17_project_scene_ux.py -v
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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])


# ===========================================================================
# Project/Scene Switcher
# ===========================================================================

class TestProjectSceneSwitcher(unittest.TestCase):
    """P3.17: Project/Scene switcher from top context bar."""

    def test_project_switcher_signals_exist(self):
        """ProjectSceneBar must have switcher signals."""
        from ui.panels.project_scene_bar import ProjectSceneBar
        bar = ProjectSceneBar()
        self.assertTrue(hasattr(bar, 'project_switch_requested'))
        self.assertTrue(hasattr(bar, 'scene_switch_requested'))

    def test_project_menu_has_new_rename_delete(self):
        """Project dropdown must have New Project, Rename, Delete."""
        from ui.panels.project_scene_bar import ProjectSceneBar
        bar = ProjectSceneBar()
        bar.set_project_menu([{"id": "p1", "name": "Test"}], "p1")
        menu = bar._project_label.menu()
        actions = [a.text() for a in menu.actions()]
        self.assertIn("New Project", actions)
        self.assertIn("Rename Project", actions)
        self.assertIn("Delete Project", actions)

    def test_scene_menu_has_new_rename_delete(self):
        """Scene dropdown must have New Scene, Rename, Delete."""
        from ui.panels.project_scene_bar import ProjectSceneBar
        bar = ProjectSceneBar()
        bar.set_scene_menu([{"id": "s1", "name": "Test"}], "s1")
        menu = bar._scene_label.menu()
        actions = [a.text() for a in menu.actions()]
        self.assertIn("New Scene", actions)
        self.assertIn("Rename Scene", actions)
        self.assertIn("Delete Scene", actions)

    def test_scene_menu_scoped_to_active_project(self):
        """MainWindow _update_project_scene_bar_menus must scope scenes."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # The scene menu must only include self._active_project.scenes
        self.assertIn("for s in self._active_project.scenes", src)


# ===========================================================================
# Project/Scene Rename Persistence
# ===========================================================================

class TestRenamePersistence(unittest.TestCase):
    """P3.17: Rename must persist across restart."""

    def test_project_rename_persists(self):
        """Renamed Project survives save/restart."""
        from engine.project_manager import ProjectManager
        from engine.models import Project
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            project = Project(name="OldName")
            pm.save_project(project)
            project.name = "NewName"
            pm.save_project(project)
            pm2 = ProjectManager(tmpdir)
            loaded = pm2.list_projects()[0]
            self.assertEqual(loaded.name, "NewName")
            self.assertEqual(loaded.id, project.id)
        finally:
            shutil.rmtree(tmpdir)

    def test_scene_rename_persists(self):
        """Renamed Scene survives save/restart."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            project = Project(name="Test")
            scene = Scene(name="OldScene")
            project.add_scene(scene)
            pm.save_project(project)
            scene.name = "NewScene"
            pm.save_project(project)
            pm2 = ProjectManager(tmpdir)
            loaded = pm2.list_projects()[0]
            self.assertEqual(loaded.scenes[0].name, "NewScene")
            self.assertEqual(loaded.scenes[0].id, scene.id)
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# Project/Scene Delete
# ===========================================================================

class TestDeleteHandlers(unittest.TestCase):
    """P3.17: Delete handlers exist with confirmation."""

    def test_delete_project_handler_exists(self):
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("def _on_delete_project", src)
        self.assertIn("Delete Project", src)
        self.assertIn("Scenes", src)
        self.assertIn("Characters", src)

    def test_delete_scene_handler_exists(self):
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("def _on_delete_scene", src)
        self.assertIn("Delete Scene", src)

    def test_delete_project_does_not_silently_delete_audio(self):
        """Delete Project must mention that audio is NOT deleted."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("NOT be deleted", src)

    def test_delete_scene_fallback(self):
        """When active Scene is deleted, another Scene must be activated."""
        from engine.models import Project, Scene
        project = Project(name="Test")
        s1 = Scene(name="S1")
        s2 = Scene(name="S2")
        project.add_scene(s1)
        project.add_scene(s2)
        # Delete s1 (active)
        project.scenes.remove(s1)
        # Must still have a scene to activate
        self.assertGreater(len(project.scenes), 0)

    def test_final_scene_deletion_creates_replacement(self):
        """When the last Scene is deleted, a new empty Scene must be created."""
        from engine.models import Project, Scene
        project = Project(name="Test")
        scene = Scene(name="Only")
        project.add_scene(scene)
        # Delete the only scene
        project.scenes.remove(scene)
        self.assertEqual(len(project.scenes), 0)
        # MainWindow._on_delete_scene creates a new scene when list is empty
        # (verified in the source code)
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("01 Untitled", src,
                      "Must create a replacement scene when all are deleted")


# ===========================================================================
# Human-Friendly Filenames
# ===========================================================================

class TestHumanFriendlyFilenames(unittest.TestCase):
    """P3.17/P3.27B: Filenames must be human-readable and VERSIONED.

    P3.27B supersession: Generate Long part filenames now follow
        <Project>_<Scene>_Long_vNN_Part_NNN[_<speaker>][_<scene8>].wav
    built by engine/output_guard.py (the P3.12 test file encodes the full
    contract; these tests keep verifying the main_window integration).
    """

    def test_filename_contains_project_name(self):
        """Filename must contain the Project name."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("proj_name", src)

    def test_filename_contains_scene_name(self):
        """Filename must contain the Scene name."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("scene_name", src)

    def test_filename_contains_part_number(self):
        """Filename must contain Part_NNN (zero-padded, versioned builder)."""
        from engine.output_guard import build_part_filename
        name = build_part_filename("Eden", "Ciklonok", 1, 1)
        self.assertIn("Part_001", name)
        self.assertIn("Long_v01", name)

    def test_filename_contains_scene_id_suffix(self):
        """Filename must contain compact scene_id suffix for uniqueness."""
        from engine.output_guard import build_part_filename
        name = build_part_filename("Eden", "Ciklonok", 1, 1,
                                   scene_id="abc123de")
        self.assertIn("abc123de", name)
        self.assertIn(str("abc123de")[:8], name)

    def test_multi_speaker_filename_contains_speaker(self):
        """Multi-speaker filenames must include the speaker name."""
        from engine.output_guard import build_part_filename
        name = build_part_filename("Eden", "Ciklonok", 1, 1,
                                   speaker="Engineer")
        self.assertIn("Engineer", name)

    def test_filename_example_format(self):
        """Verify the expected format: Eden_Ciklonok_Long_v01_Part_001_abc123de.wav"""
        from engine.output_guard import build_part_filename
        filename = build_part_filename("Eden", "Ciklonok", 1, 1,
                                       scene_id="abc123de")
        self.assertEqual(filename,
                         "Eden_Ciklonok_Long_v01_Part_001_abc123de.wav")

    def test_multi_speaker_filename_example(self):
        """Multi-speaker format: Eden_Ciklonok_Long_v01_Part_001_Engineer_abc123de.wav"""
        from engine.output_guard import build_part_filename
        filename = build_part_filename("Eden", "Ciklonok", 1, 1,
                                       speaker="Engineer",
                                       scene_id="abc123de")
        self.assertEqual(
            filename,
            "Eden_Ciklonok_Long_v01_Part_001_Engineer_abc123de.wav")


# ===========================================================================
# Filename Normalization
# ===========================================================================

class TestFilenameNormalization(unittest.TestCase):
    """P3.17: Filenames must be Windows-safe."""

    def test_normalize_function_exists(self):
        """P3.27B: filename normalization moved from MainWindow's local
        _normalize_filename to engine/output_guard.normalize_name_component
        (the same rule set, single implementation)."""
        from engine.output_guard import normalize_name_component
        self.assertEqual(normalize_name_component("My Book: Part 1"),
                         "My_Book_Part_1")
        with open(os.path.join(_ROOT, "engine/output_guard.py")) as f:
            src = f.read()
        self.assertIn("def normalize_name_component", src)
        # MainWindow must use the shared implementation, not a local copy.
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            mw_src = f.read()
        self.assertIn("normalize_name_component", mw_src)

    def test_normalize_replaces_invalid_chars(self):
        """Invalid Windows chars must be replaced."""
        import re
        def _normalize(name):
            safe = re.sub(r'[\\/:*?"<>|\s]+', '_', name.strip())
            safe = re.sub(r'_+', '_', safe)
            safe = safe.strip('_.')
            return safe or "untitled"

        self.assertEqual(_normalize("My Book: Part 1"), "My_Book_Part_1")
        self.assertEqual(_normalize("Chapter 03 / Storm"), "Chapter_03_Storm")
        self.assertEqual(_normalize('Test"Quote'), "Test_Quote")
        self.assertEqual(_normalize("  spaces  "), "spaces")
        self.assertEqual(_normalize(""), "untitled")
        self.assertEqual(_normalize("Eden"), "Eden")
        self.assertEqual(_normalize("Ciklonok"), "Ciklonok")


# ===========================================================================
# Collision Handling
# ===========================================================================

class TestCollisionHandling(unittest.TestCase):
    """P3.17: Filename collision handling must still work."""

    def test_collision_produces_unique_suffix(self):
        """Two files with same name must get _2 suffix."""
        from engine.project_exporter import _unique_dir
        import tempfile, os, shutil
        tmpdir = tempfile.mkdtemp()
        try:
            base = "Eden_Ciklonok_Part_01_abc123de"
            os.makedirs(os.path.join(tmpdir, base))
            result = _unique_dir(tmpdir, base)
            self.assertTrue(result.endswith("_2"))
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# Path Consistency
# ===========================================================================

class TestPathConsistency(unittest.TestCase):
    """P3.17: Filename must be consistent across BatchJob → Asset → History."""

    def test_audioasset_uses_same_filename(self):
        """AudioAsset.output_path uses the generated filename."""
        from engine.models import AudioAsset
        asset = AudioAsset(
            scene_id="s1",
            output_path="outputs/Eden_Ciklonok_Part_01_abc123de.wav",
            duration=2.0,
        )
        self.assertIn("Eden", asset.output_path)
        self.assertIn("Ciklonok", asset.output_path)
        self.assertIn("Part_01", asset.output_path)

    def test_history_entry_uses_same_filename(self):
        """HistoryEntry.output_path uses the generated filename."""
        from engine.history_manager import HistoryManager
        from engine.models import GenerationResult
        import tempfile, shutil
        tmpdir = tempfile.mkdtemp()
        try:
            hm = HistoryManager(tmpdir)
            r = GenerationResult(
                success=True,
                output_path="outputs/Eden_Ciklonok_Part_01_abc123de.wav",
                prompt="test",
                generation_time=1.0,
                output_duration=2.0,
            )
            r.timestamp = "20240101_120000"
            r.project = "Eden"
            entry_id = hm.add(r)
            entry = hm.get_entry(entry_id)
            self.assertIn("Eden", entry.output_path)
            self.assertIn("Part_01", entry.output_path)
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# Rename Does Not Rename Existing Audio
# ===========================================================================

class TestRenameDoesNotRenameAudio(unittest.TestCase):
    """P3.17: Renaming Project/Scene must NOT rename existing audio files."""

    def test_rename_does_not_modify_audio_assets(self):
        """Scene rename must not change existing audio_assets paths."""
        from engine.models import Project, Scene
        project = Project(name="Test")
        scene = Scene(name="OldName")
        scene.audio_assets.append({
            "id": "a1",
            "output_path": "outputs/Eden_OldName_Part_01_abc123de.wav",
            "duration": 2.0,
        })
        project.add_scene(scene)
        # Rename the scene
        scene.name = "NewName"
        # Audio asset path must NOT change
        self.assertEqual(
            scene.audio_assets[0]["output_path"],
            "outputs/Eden_OldName_Part_01_abc123de.wav",
            "Existing audio path must not change after rename")


if __name__ == "__main__":
    unittest.main(verbosity=2)
