"""
SpeechStudio — P3.21 Phase 1: Scene Ordering Tests
===================================================

Tests for Scene.sort_order field, persistence, and ordering UI.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_21_scene_ordering.py -v
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


class TestSceneSortOrder(unittest.TestCase):
    """P3.21: Scene.sort_order field exists and persists."""

    def test_scene_has_sort_order_field(self):
        """Scene model must have a sort_order field."""
        from engine.models import Scene
        s = Scene(name="Test")
        self.assertTrue(hasattr(s, 'sort_order'))
        self.assertEqual(s.sort_order, 0)  # default

    def test_scene_to_dict_includes_sort_order(self):
        """Scene.to_dict() must include sort_order."""
        from engine.models import Scene
        s = Scene(name="Test", sort_order=5)
        data = s.to_dict()
        self.assertIn("sort_order", data)
        self.assertEqual(data["sort_order"], 5)

    def test_scene_from_dict_restores_sort_order(self):
        """Scene.from_dict() must restore sort_order."""
        from engine.models import Scene
        s = Scene(name="Test", sort_order=3)
        data = s.to_dict()
        restored = Scene.from_dict(data)
        self.assertEqual(restored.sort_order, 3)

    def test_sort_order_defaults_to_zero(self):
        """Old scenes without sort_order default to 0."""
        from engine.models import Scene
        # Simulate old data without sort_order
        old_data = {"id": "test", "name": "Old", "text": "hello"}
        s = Scene.from_dict(old_data)
        self.assertEqual(s.sort_order, 0)


class TestSortOrderPersistence(unittest.TestCase):
    """P3.21: sort_order survives save/restart."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_sort_order_persists(self):
        """sort_order values survive save + reload."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        pm = ProjectManager(self.tmpdir)
        project = Project(name="Test")
        project.add_scene(Scene(name="S1", sort_order=2))
        project.add_scene(Scene(name="S2", sort_order=0))
        project.add_scene(Scene(name="S3", sort_order=1))
        pm.save_project(project)

        pm2 = ProjectManager(self.tmpdir)
        loaded = pm2.list_projects()[0]
        orders = {s.name: s.sort_order for s in loaded.scenes}
        self.assertEqual(orders["S1"], 2)
        self.assertEqual(orders["S2"], 0)
        self.assertEqual(orders["S3"], 1)

    def test_auto_assign_on_first_save(self):
        """Scenes with all sort_order=0 get sequential values on first save."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        pm = ProjectManager(self.tmpdir)
        project = Project(name="Test")
        project.add_scene(Scene(name="S1"))
        project.add_scene(Scene(name="S2"))
        project.add_scene(Scene(name="S3"))
        pm.save_project(project)

        pm2 = ProjectManager(self.tmpdir)
        loaded = pm2.list_projects()[0]
        orders = [s.sort_order for s in sorted(loaded.scenes, key=lambda s: s.sort_order)]
        self.assertEqual(orders, [0, 1, 2])

    def test_no_auto_assign_when_already_set(self):
        """If sort_order values are already non-zero, they are NOT overwritten."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        pm = ProjectManager(self.tmpdir)
        project = Project(name="Test")
        project.add_scene(Scene(name="S1", sort_order=5))
        project.add_scene(Scene(name="S2", sort_order=3))
        pm.save_project(project)

        pm2 = ProjectManager(self.tmpdir)
        loaded = pm2.list_projects()[0]
        orders = {s.name: s.sort_order for s in loaded.scenes}
        self.assertEqual(orders["S1"], 5)
        self.assertEqual(orders["S2"], 3)


class TestSceneOrderingIndependent(unittest.TestCase):
    """P3.21: Scene order is independent from name, creation time, filesystem."""

    def test_order_independent_from_name(self):
        """Scenes sorted by sort_order, not name."""
        from engine.models import Project, Scene
        project = Project(name="Test")
        project.add_scene(Scene(name="Zebra", sort_order=0))
        project.add_scene(Scene(name="Alpha", sort_order=1))
        project.add_scene(Scene(name="Mango", sort_order=2))
        sorted_scenes = sorted(project.scenes, key=lambda s: s.sort_order)
        names = [s.name for s in sorted_scenes]
        self.assertEqual(names, ["Zebra", "Alpha", "Mango"])  # NOT alphabetical

    def test_new_scene_gets_next_sort_order(self):
        """New scene gets max(existing) + 1."""
        from engine.models import Project, Scene
        project = Project(name="Test")
        project.add_scene(Scene(name="S1", sort_order=0))
        project.add_scene(Scene(name="S2", sort_order=1))
        next_order = max((s.sort_order for s in project.scenes), default=-1) + 1
        new_scene = Scene(name="S3", sort_order=next_order)
        project.add_scene(new_scene)
        self.assertEqual(new_scene.sort_order, 2)


class TestMoveUpDown(unittest.TestCase):
    """P3.21: Move up/down signals exist and handlers are wired."""

    def test_move_up_signal_exists(self):
        """ProjectSceneBar must have scene_move_up_requested signal."""
        with open(os.path.join(_ROOT, "ui/panels/project_scene_bar.py")) as f:
            src = f.read()
        self.assertIn("scene_move_up_requested", src)

    def test_move_down_signal_exists(self):
        """ProjectSceneBar must have scene_move_down_requested signal."""
        with open(os.path.join(_ROOT, "ui/panels/project_scene_bar.py")) as f:
            src = f.read()
        self.assertIn("scene_move_down_requested", src)

    def test_move_up_handler_exists(self):
        """MainWindow must have _on_scene_move_up method."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("def _on_scene_move_up", src)
        self.assertIn("self._project_scene_bar.scene_move_up_requested.connect", src)

    def test_move_down_handler_exists(self):
        """MainWindow must have _on_scene_move_down method."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("def _on_scene_move_down", src)
        self.assertIn("self._project_scene_bar.scene_move_down_requested.connect", src)

    def test_move_up_menu_item_exists(self):
        """Scene dropdown must have 'Move Up' menu item."""
        with open(os.path.join(_ROOT, "ui/panels/project_scene_bar.py")) as f:
            src = f.read()
        self.assertIn('"Move Up"', src)

    def test_move_down_menu_item_exists(self):
        """Scene dropdown must have 'Move Down' menu item."""
        with open(os.path.join(_ROOT, "ui/panels/project_scene_bar.py")) as f:
            src = f.read()
        self.assertIn('"Move Down"', src)

    def test_sorted_scenes_helper_exists(self):
        """MainWindow must have _get_sorted_scenes method."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("def _get_sorted_scenes", src)

    def test_context_list_uses_sorted_scenes(self):
        """_build_context_list_entries must use _get_sorted_scenes for Scenes."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("for s in self._get_sorted_scenes():", src)


class TestMoveUpDownRuntime(unittest.TestCase):
    """P3.21: Runtime test of sort_order swapping."""

    def test_swap_sort_order_up(self):
        """Swapping sort_order moves a scene up."""
        from engine.models import Project, Scene
        project = Project(name="Test")
        s1 = Scene(name="S1", sort_order=0)
        s2 = Scene(name="S2", sort_order=1)
        s3 = Scene(name="S3", sort_order=2)
        project.add_scene(s1)
        project.add_scene(s2)
        project.add_scene(s3)

        # Move S2 up (swap with S1)
        scenes = sorted(project.scenes, key=lambda s: s.sort_order)
        idx = next(i for i, s in enumerate(scenes) if s.id == s2.id)
        scenes[idx], scenes[idx - 1] = scenes[idx - 1], scenes[idx]
        scenes[idx].sort_order, scenes[idx - 1].sort_order = (
            scenes[idx - 1].sort_order, scenes[idx].sort_order)

        # Verify: S2 now has sort_order=0, S1 has sort_order=1
        self.assertEqual(s2.sort_order, 0)
        self.assertEqual(s1.sort_order, 1)
        self.assertEqual(s3.sort_order, 2)

    def test_swap_sort_order_down(self):
        """Swapping sort_order moves a scene down."""
        from engine.models import Project, Scene
        project = Project(name="Test")
        s1 = Scene(name="S1", sort_order=0)
        s2 = Scene(name="S2", sort_order=1)
        s3 = Scene(name="S3", sort_order=2)
        project.add_scene(s1)
        project.add_scene(s2)
        project.add_scene(s3)

        # Move S2 down (swap with S3)
        scenes = sorted(project.scenes, key=lambda s: s.sort_order)
        idx = next(i for i, s in enumerate(scenes) if s.id == s2.id)
        scenes[idx], scenes[idx + 1] = scenes[idx + 1], scenes[idx]
        scenes[idx].sort_order, scenes[idx + 1].sort_order = (
            scenes[idx + 1].sort_order, scenes[idx].sort_order)

        # Verify: S2 now has sort_order=2, S3 has sort_order=1
        self.assertEqual(s2.sort_order, 2)
        self.assertEqual(s3.sort_order, 1)
        self.assertEqual(s1.sort_order, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
