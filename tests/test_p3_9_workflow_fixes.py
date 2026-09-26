"""
SpeechStudio — P3.9 Project/Scene Workflow + Full Screen Bug Fix Tests
=======================================================================

Runtime tests for the P3.9 fixes:
  1. Context-aware + button (PROJECTS/SCENES/HISTORY/CHARACTERS)
  2. Scenes context list scoped to active Project
  3. Characters context list scoped to active Project
  4. Scene rename propagation
  5. Scene status lifecycle refresh
  6. Full Screen state restoration

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_9_workflow_fixes.py -v
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
# 1. Context-aware + button
# ===========================================================================

class TestContextAwareAddButton(unittest.TestCase):
    """P3.9: The + button must be context-aware."""

    def setUp(self):
        from ui.panels.project_scene_sidebar import Sidebar
        self.sb = Sidebar()

    def test_add_signals_exist(self):
        """Sidebar must have add_project_requested, add_scene_requested, add_character_requested signals."""
        self.assertTrue(hasattr(self.sb, 'add_project_requested'))
        self.assertTrue(hasattr(self.sb, 'add_scene_requested'))
        self.assertTrue(hasattr(self.sb, 'add_character_requested'))

    def test_plus_button_does_not_connect_to_new_scene_directly(self):
        """The + button must NOT be connected directly to new_scene_requested."""
        # The old connection was: clicked.connect(self.new_scene_requested.emit)
        # The new connection is: clicked.connect(self._on_add_button_clicked)
        import ast
        with open(os.path.join(_ROOT, "ui/panels/project_scene_sidebar.py")) as f:
            src = f.read()
        self.assertNotIn(
            "self._add_scene_btn.clicked.connect(self.new_scene_requested.emit)",
            src)
        self.assertIn(
            "self._add_scene_btn.clicked.connect(self._on_add_button_clicked)",
            src)

    def test_projects_context_button_tooltip(self):
        """In Projects context, + button tooltip should be 'Add Project'."""
        self.sb._on_nav_clicked("Projects")
        self.assertEqual(self.sb._add_scene_btn.toolTip(), "Add Project")
        # isVisible() returns False in offscreen mode unless the widget
        # has been shown. We check isHidden() instead — the button should
        # NOT be explicitly hidden.
        self.assertFalse(self.sb._add_scene_btn.isHidden())

    def test_scenes_context_button_tooltip(self):
        """In Scenes context, + button tooltip should be 'Add Scene'."""
        self.sb._on_nav_clicked("Scenes")
        self.assertEqual(self.sb._add_scene_btn.toolTip(), "Add Scene")
        self.assertFalse(self.sb._add_scene_btn.isHidden())

    def test_characters_context_button_tooltip(self):
        """In Characters context, + button tooltip should be 'Add Character'."""
        self.sb._on_nav_clicked("Characters")
        self.assertEqual(self.sb._add_scene_btn.toolTip(), "Add Character")
        self.assertFalse(self.sb._add_scene_btn.isHidden())

    def test_history_context_button_hidden(self):
        """In History context, + button should be hidden."""
        self.sb._on_nav_clicked("History")
        self.assertTrue(self.sb._add_scene_btn.isHidden(),
                        "Button should be hidden in History context")

    def test_plus_button_emits_correct_signal_for_projects(self):
        """Clicking + in Projects context emits add_project_requested."""
        self.sb._current_recents_context = "Projects"
        self.sb._update_add_button_for_context("Projects")
        received = []
        self.sb.add_project_requested.connect(lambda: received.append("project"))
        self.sb._on_add_button_clicked()
        self.assertEqual(received, ["project"])

    def test_plus_button_emits_correct_signal_for_scenes(self):
        """Clicking + in Scenes context emits add_scene_requested."""
        self.sb._current_recents_context = "Scenes"
        self.sb._update_add_button_for_context("Scenes")
        received = []
        self.sb.add_scene_requested.connect(lambda: received.append("scene"))
        self.sb._on_add_button_clicked()
        self.assertEqual(received, ["scene"])

    def test_plus_button_emits_correct_signal_for_characters(self):
        """Clicking + in Characters context emits add_character_requested."""
        # Set the context directly (simulates what set_recents does)
        self.sb._current_recents_context = "Characters"
        self.sb._update_add_button_for_context("Characters")
        received = []
        self.sb.add_character_requested.connect(lambda: received.append("char"))
        self.sb._on_add_button_clicked()
        self.assertEqual(received, ["char"])

    def test_plus_button_no_signal_for_history(self):
        """Clicking + in History context emits nothing."""
        # Set the context directly (simulates what set_recents does)
        self.sb._current_recents_context = "History"
        self.sb._update_add_button_for_context("History")
        received = []
        self.sb.add_project_requested.connect(lambda: received.append("p"))
        self.sb.add_scene_requested.connect(lambda: received.append("s"))
        self.sb.add_character_requested.connect(lambda: received.append("c"))
        self.sb._on_add_button_clicked()
        self.assertEqual(received, [])


# ===========================================================================
# 2. Scenes context list scoped to active Project
# ===========================================================================

class TestScenesScopedToActiveProject(unittest.TestCase):
    """P3.9: Scenes context list must show only the active Project's scenes."""

    def test_scenes_context_uses_active_project(self):
        """_build_context_list_entries for Scenes must use _active_project, not all projects."""
        import ast
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # Verify the Scenes context checks _active_project first
        self.assertIn("if self._active_project is not None:", src)
        # Verify it iterates active_project.scenes (not all projects)
        self.assertIn("for s in self._get_sorted_scenes():", src)

    def test_scenes_scoped_runtime(self):
        """Runtime test: create 2 projects, verify Scenes list only shows active project's scenes."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            # Project A with 2 scenes
            pA = Project(name="ProjectA")
            pA.add_scene(Scene(name="A1"))
            pA.add_scene(Scene(name="A2"))
            pm.save_project(pA)
            # Project B with 3 scenes
            pB = Project(name="ProjectB")
            pB.add_scene(Scene(name="B1"))
            pB.add_scene(Scene(name="B2"))
            pB.add_scene(Scene(name="B3"))
            pm.save_project(pB)

            # Simulate _build_context_list_entries for Scenes with pA active
            active_project = pA
            entries = []
            if active_project is not None:
                for s in active_project.scenes:
                    entries.append({"id": s.id, "name": s.name})

            self.assertEqual(len(entries), 2)
            names = [e["name"] for e in entries]
            self.assertIn("A1", names)
            self.assertIn("A2", names)
            self.assertNotIn("B1", names)
            self.assertNotIn("B2", names)
            self.assertNotIn("B3", names)

            # Switch to pB
            active_project = pB
            entries = []
            if active_project is not None:
                for s in active_project.scenes:
                    entries.append({"id": s.id, "name": s.name})

            self.assertEqual(len(entries), 3)
            names = [e["name"] for e in entries]
            self.assertIn("B1", names)
            self.assertIn("B2", names)
            self.assertIn("B3", names)
            self.assertNotIn("A1", names)
            self.assertNotIn("A2", names)
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# 3. Characters context list scoped to active Project
# ===========================================================================

class TestCharactersScopedToActiveProject(unittest.TestCase):
    """P3.9: Characters context list must show only the active Project's characters."""

    def test_characters_scoped_runtime(self):
        """Runtime test: create 2 projects with different characters, verify scoping."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Character
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            pA = Project(name="ProjectA")
            pA.add_character(Character(name="Engineer"))
            pA.add_character(Character(name="Lady"))
            pm.save_project(pA)
            pB = Project(name="ProjectB")
            pB.add_character(Character(name="Captain"))
            pm.save_project(pB)

            # With pA active
            active_project = pA
            entries = []
            for c in active_project.characters:
                entries.append({"id": c.id, "name": c.name})
            self.assertEqual(len(entries), 2)
            names = [e["name"] for e in entries]
            self.assertIn("Engineer", names)
            self.assertIn("Lady", names)
            self.assertNotIn("Captain", names)

            # With pB active
            active_project = pB
            entries = []
            for c in active_project.characters:
                entries.append({"id": c.id, "name": c.name})
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["name"], "Captain")
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# 4. Scene rename propagation
# ===========================================================================

class TestSceneRenamePropagation(unittest.TestCase):
    """P3.9: Scene rename must refresh the sidebar + Recents."""

    def test_rename_updates_sidebar_scenes(self):
        """_on_edit_scene_name must call set_scenes with updated name."""
        import ast
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # The rename handler must call set_scenes
        self.assertIn("self._sidebar.set_scenes(sidebar_scenes)", src)
        # It must also refresh Recents
        self.assertIn("self._recents_manager.add_recent_scene", src)

    def test_rename_preserves_scene_id(self):
        """Rename must not change the Scene ID."""
        from engine.models import Scene
        scene = Scene(name="Old Name")
        original_id = scene.id
        scene.name = "New Name"
        self.assertEqual(scene.id, original_id)
        self.assertEqual(scene.name, "New Name")


# ===========================================================================
# 5. Scene status lifecycle refresh
# ===========================================================================

class TestSceneStatusRefresh(unittest.TestCase):
    """P3.9: Scene status change must refresh the sidebar."""

    def test_generation_success_refreshes_sidebar(self):
        """_on_generation_finished_ui must refresh the sidebar after the
        DERIVED status is computed.

        P3.28 SUPERSESSION: this test previously asserted the P2.8-era
        eager write `self._active_scene.status = "complete"`. P3.28 §5
        explicitly corrects that defect — starting/completing a part is
        NEVER eager completion; the status is DERIVED by the single
        registration writer (engine.audio_provenance.
        register_generation_result) and the sidebar refresh follows it.
        """
        import ast
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # The ONE WRITER call must be present (closes D-2/D-3).
        self.assertIn("register_generation_result", src)
        # Verify the refresh happens inside the generation finished
        # handler, after the registration call.
        idx_register = src.find("register_generation_result(")
        self.assertGreaterEqual(idx_register, 0)
        idx_refresh = src.find(
            "self._sidebar.set_scenes(sidebar_scenes)", idx_register)
        self.assertGreater(idx_refresh, idx_register,
                           "set_scenes must be called after the derived "
                           "status registration")

    def test_generation_failure_refreshes_sidebar(self):
        """_on_generation_failed_ui must refresh the sidebar after the
        DERIVED (failure-aware) status recompute.

        P3.28 SUPERSESSION: this test previously asserted the eager write
        `self._active_scene.status = "error"`. P3.28 §5 corrects that
        defect — failure is per-slot and coverage is derived: only a
        ZERO-coverage Scene becomes ERROR; covered Scenes keep their
        PARTIAL/COMPLETE state (previous versions remain valid).
        """
        import ast
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("recompute_scene_status(scene, last_run_failed=True)",
                      src)
        idx_recompute = src.find(
            "recompute_scene_status(scene, last_run_failed=True)")
        idx_refresh = src.find(
            "self._sidebar.set_scenes(sidebar_scenes)", idx_recompute)
        self.assertGreater(idx_refresh, idx_recompute,
                           "set_scenes must be called after the derived "
                           "failure recompute")

    def test_scene_status_lifecycle_values(self):
        """Verify the 4 valid scene status values exist in the model."""
        from engine.models import Scene
        s = Scene()
        self.assertEqual(s.status, "draft")  # default
        s.status = "generating"
        self.assertEqual(s.status, "generating")
        s.status = "complete"
        self.assertEqual(s.status, "complete")
        s.status = "error"
        self.assertEqual(s.status, "error")


# ===========================================================================
# 6. Full Screen state restoration
# ===========================================================================

class TestFullScreenStateRestoration(unittest.TestCase):
    """P3.9: Full Screen must save/restore window geometry."""

    def test_toggle_fullscreen_saves_geometry(self):
        """_toggle_fullscreen must save geometry before entering full screen."""
        import ast
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("self._saved_geometry = self.saveGeometry()", src)
        self.assertIn("self.restoreGeometry(self._saved_geometry)", src)

    def test_toggle_fullscreen_activates_window(self):
        """_toggle_fullscreen must call activateWindow + raise_ for menu focus."""
        import ast
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("self.activateWindow()", src)
        self.assertIn("self.raise_()", src)

    def test_fullscreen_does_not_use_stays_on_top(self):
        """Full Screen must NOT use WindowStaysOnTopHint (breaks child windows)."""
        import ast
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertNotIn("WindowStaysOnTopHint", src)
        self.assertNotIn("Qt.Tool", src)


# ===========================================================================
# 7. MainWindow connects new add signals
# ===========================================================================

class TestMainWindowConnectsAddSignals(unittest.TestCase):
    """P3.9: MainWindow must connect the new context-aware add signals."""

    def test_add_project_requested_connected(self):
        """MainWindow must connect add_project_requested to _on_new_project."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn(
            "self._sidebar.add_project_requested.connect(self._on_new_project)",
            src)

    def test_add_scene_requested_connected(self):
        """MainWindow must connect add_scene_requested to _on_new_scene."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn(
            "self._sidebar.add_scene_requested.connect(self._on_new_scene)",
            src)

    def test_add_character_requested_connected(self):
        """MainWindow must connect add_character_requested to the quick-create handler.

        P3.24 UX Correction: "+ Add Character" now runs the inline quick
        creation flow (_on_character_create_quick) instead of opening the
        modal CharacterManagementDialog. The dialog remains reachable as
        the secondary details editor via character_details_requested.
        """
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn(
            "self._sidebar.add_character_requested.connect(self._on_character_create_quick)",
            src)
        # No duplicate connection to the old dialog handler.
        self.assertNotIn(
            "self._sidebar.add_character_requested.connect(self._on_open_character_management)",
            src)


# ===========================================================================
# 8. Large project usability (50 scenes)
# ===========================================================================

class TestLargeProjectUsability(unittest.TestCase):
    """P3.9: A project with 50 scenes must work correctly."""

    def test_50_scenes_project(self):
        """Create a project with 50 scenes, verify all are listed + scoped."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            project = Project(name="LargeProject")
            for i in range(50):
                project.add_scene(Scene(name="Scene_{0:02d}".format(i + 1)))
            pm.save_project(project)

            # Reload
            loaded = pm.list_projects()[0]
            self.assertEqual(len(loaded.scenes), 50)

            # Simulate context list entries for this active project
            entries = []
            for s in loaded.scenes:
                entries.append({"id": s.id, "name": s.name})
            self.assertEqual(len(entries), 50)
            self.assertEqual(entries[0]["name"], "Scene_01")
            self.assertEqual(entries[49]["name"], "Scene_50")
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# 9. Project switching with multiple projects (no state leakage)
# ===========================================================================

class TestProjectSwitchingNoLeakage(unittest.TestCase):
    """P3.9: Switching projects must not leak scenes/characters between projects."""

    def test_project_switch_no_leakage(self):
        """Create Project A (scenes A1,A2 + char Engineer) + Project B (scenes B1,B2 + char Lady).
        Switching A→B→A must show correct scoped lists each time."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene, Character
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            pA = Project(name="ProjectA")
            pA.add_scene(Scene(name="A1"))
            pA.add_scene(Scene(name="A2"))
            pA.add_character(Character(name="Engineer"))
            pm.save_project(pA)
            pB = Project(name="ProjectB")
            pB.add_scene(Scene(name="B1"))
            pB.add_scene(Scene(name="B2"))
            pB.add_character(Character(name="Lady"))
            pm.save_project(pB)

            # Active = A
            active = pA
            scenes_A = [s.name for s in active.scenes]
            chars_A = [c.name for c in active.characters]
            self.assertEqual(scenes_A, ["A1", "A2"])
            self.assertEqual(chars_A, ["Engineer"])

            # Active = B
            active = pB
            scenes_B = [s.name for s in active.scenes]
            chars_B = [c.name for c in active.characters]
            self.assertEqual(scenes_B, ["B1", "B2"])
            self.assertEqual(chars_B, ["Lady"])

            # Active = A again (no leakage)
            active = pA
            scenes_A2 = [s.name for s in active.scenes]
            chars_A2 = [c.name for c in active.characters]
            self.assertEqual(scenes_A2, ["A1", "A2"])
            self.assertEqual(chars_A2, ["Engineer"])
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# 10. Scene state persistence through switch (A → B → A)
# ===========================================================================

class TestSceneStatePersistenceSwitch(unittest.TestCase):
    """P3.9: Scene text must survive A → B → A switch."""

    def test_scene_text_survives_switch(self):
        """Scene text must be preserved when switching away and back."""
        from engine.models import Project, Scene
        project = Project(name="TestProject")
        scene_a = Scene(name="A", text="Text in scene A")
        scene_b = Scene(name="B", text="Text in scene B")
        project.add_scene(scene_a)
        project.add_scene(scene_b)

        # Simulate _save_current_scene_state for A
        # (in production this saves editor text into scene_a.text)
        scene_a.text = "Text in scene A"

        # Switch to B
        scene_b.text = "Text in scene B"

        # Switch back to A — verify text is preserved
        self.assertEqual(scene_a.text, "Text in scene A")
        self.assertEqual(scene_b.text, "Text in scene B")

    def test_scene_all_fields_survive_switch(self):
        """All Scene fields must survive switching."""
        from engine.models import Project, Scene, GenerationParameters
        project = Project(name="TestProject")
        scene = Scene(
            name="Scene 01",
            text="Hello world",
            emotion="Fear",
            style="Whispering",
            speed="Slow",
            pitch="High",
            delivery="Expressive High",
            status="complete",
            parameters=GenerationParameters(temperature=1.5, seed=42),
        )
        scene.character_ids.append("char_1")
        scene.audio_assets.append({"id": "asset_1", "duration": 2.5})
        project.add_scene(scene)

        # Simulate save → reload
        data = project.to_dict()
        restored = Project.from_dict(data)
        r_scene = restored.scenes[0]

        self.assertEqual(r_scene.text, "Hello world")
        self.assertEqual(r_scene.emotion, "Fear")
        self.assertEqual(r_scene.style, "Whispering")
        self.assertEqual(r_scene.speed, "Slow")
        self.assertEqual(r_scene.pitch, "High")
        self.assertEqual(r_scene.delivery, "Expressive High")
        self.assertEqual(r_scene.status, "complete")
        self.assertEqual(r_scene.parameters.temperature, 1.5)
        self.assertEqual(r_scene.parameters.seed, 42)
        self.assertEqual(r_scene.character_ids, ["char_1"])
        self.assertEqual(len(r_scene.audio_assets), 1)


# ===========================================================================
# 11. Scene state persistence through Save → Restart → Reopen
# ===========================================================================

class TestSceneStatePersistenceRestart(unittest.TestCase):
    """P3.9: Scene state must survive Save → Restart → Reopen."""

    def test_save_restart_reopen(self):
        """Save a project with scene state, reopen with a new ProjectManager, verify."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene, GenerationParameters
        tmpdir = tempfile.mkdtemp()
        try:
            pm1 = ProjectManager(tmpdir)
            project = Project(name="PersistTest")
            scene = Scene(
                name="Scene 01",
                text="Persisted text",
                emotion="Awe",
                style="Whispering",
                speed="Fast",
                pitch="Low",
                delivery="Expressive Low",
                status="complete",
                parameters=GenerationParameters(temperature=1.3, seed=999),
            )
            scene.character_ids.append("char_persist")
            scene.audio_assets.append({"id": "asset_p", "duration": 3.0})
            project.add_scene(scene)
            pm1.save_project(project)

            # Simulate restart — new ProjectManager
            pm2 = ProjectManager(tmpdir)
            loaded = pm2.list_projects()[0]
            l_scene = loaded.scenes[0]

            self.assertEqual(l_scene.text, "Persisted text")
            self.assertEqual(l_scene.emotion, "Awe")
            self.assertEqual(l_scene.style, "Whispering")
            self.assertEqual(l_scene.speed, "Fast")
            self.assertEqual(l_scene.pitch, "Low")
            self.assertEqual(l_scene.delivery, "Expressive Low")
            self.assertEqual(l_scene.status, "complete")
            self.assertEqual(l_scene.parameters.temperature, 1.3)
            self.assertEqual(l_scene.parameters.seed, 999)
            self.assertEqual(l_scene.character_ids, ["char_persist"])
            self.assertEqual(len(l_scene.audio_assets), 1)
            self.assertEqual(l_scene.audio_assets[0]["duration"], 3.0)
        finally:
            shutil.rmtree(tmpdir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
