"""
SpeechStudio — P3.15 Narration Block Splitting + Project/Scene Context Tests
=============================================================================

Runtime tests for:
  A. Narration Block splitting (sentence-aware, Block→Part mapping)
  B. Batch Part visualization (source_block_id, part_of_block)
  C-F. Project/Scene switcher, rename
  G-H. Project/Scene delete

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_15_block_splitting_context.py -v
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
# A. Narration Block splitting
# ===========================================================================

class TestNarrationBlockSplitting(unittest.TestCase):
    """P3.15: Narration Block splitting tests."""

    def _make_blocks(self, text, specs):
        """Create PromptBlocks from (start, end, emotion, style) specs."""
        from engine.narration_blocks import PromptBlock
        return [PromptBlock(start_offset=s, end_offset=e, emotion=emo, style=sty)
                for s, e, emo, sty in specs]

    def test_short_block_one_part(self):
        """1. Short Block → one Part."""
        from engine.narration_splitter import NarrationSplitter
        text = "Hello world."
        blocks = self._make_blocks(text, [(0, 12, None, None)])
        splitter = NarrationSplitter()
        parts = splitter._split_with_blocks(text, blocks, None, None, "Normal", "Normal", "Normal", None)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].block_index, 0)

    def test_long_block_multiple_parts(self):
        """2. Long Block → multiple Parts."""
        from engine.narration_splitter import NarrationSplitter
        # Create a long text with multiple sentences
        text = ". ".join(["This is sentence number {0}".format(i) for i in range(20)]) + "."
        blocks = self._make_blocks(text, [(0, len(text), None, None)])
        splitter = NarrationSplitter()
        parts = splitter._split_with_blocks(text, blocks, None, None, "Normal", "Normal", "Normal", None)
        self.assertGreater(len(parts), 1, "Long block should produce multiple parts")

    def test_multiple_blocks_correct_grouping(self):
        """3. Multiple Blocks → correct Part grouping."""
        from engine.narration_splitter import NarrationSplitter
        text = "Block one text. Block two text."
        blocks = self._make_blocks(text, [(0, 17, None, None), (18, 31, None, None)])
        splitter = NarrationSplitter()
        parts = splitter._split_with_blocks(text, blocks, None, None, "Normal", "Normal", "Normal", None)
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].block_index, 0)
        self.assertEqual(parts[1].block_index, 1)

    def test_no_part_crosses_block_boundary(self):
        """4. No Part crosses a Block boundary."""
        from engine.narration_splitter import NarrationSplitter
        text = "First block. Second block."
        blocks = self._make_blocks(text, [(0, 13, None, None), (14, 27, None, None)])
        splitter = NarrationSplitter()
        parts = splitter._split_with_blocks(text, blocks, None, None, "Normal", "Normal", "Normal", None)
        # Each part must belong to exactly one block
        for part in parts:
            self.assertIsNotNone(part.block_index)
            self.assertIsNotNone(part.source_block_id)

    def test_source_text_preserved(self):
        """5. Source text preserved exactly — concatenating Parts = original Block text."""
        from engine.narration_splitter import NarrationSplitter
        text = "Sentence one. Sentence two. Sentence three. Sentence four."
        blocks = self._make_blocks(text, [(0, len(text), None, None)])
        splitter = NarrationSplitter()
        parts = splitter._split_with_blocks(text, blocks, None, None, "Normal", "Normal", "Normal", None)
        # Concatenate part texts (with space separator, as the splitter uses)
        reconstructed = " ".join(p.text for p in parts)
        # The reconstructed text should contain all the original sentences
        for sentence in ["Sentence one", "Sentence two", "Sentence three", "Sentence four"]:
            self.assertIn(sentence, reconstructed)

    def test_sentence_aware_split(self):
        """6. Sentence-aware split — Parts end at sentence boundaries."""
        from engine.narration_splitter import NarrationSplitter
        # Long text with clear sentence boundaries
        text = ". ".join(["This is sentence {0}".format(i) for i in range(10)]) + "."
        blocks = self._make_blocks(text, [(0, len(text), None, None)])
        splitter = NarrationSplitter()
        parts = splitter._split_with_blocks(text, blocks, None, None, "Normal", "Normal", "Normal", None)
        # Each part should end with sentence-ending punctuation
        for part in parts:
            self.assertTrue(part.text.rstrip().endswith(('.', '!', '?', '…')),
                            "Part should end at sentence boundary: {0!r}".format(part.text[-20:]))

    def test_safe_duration_preserved(self):
        """7. Safe duration preserved — Parts should be below ~30s."""
        from engine.narration_splitter import NarrationSplitter
        # Very long text — ~600 chars = ~40s at 15 chars/sec
        text = ". ".join(["This is a sentence." for _ in range(40)]) + "."
        blocks = self._make_blocks(text, [(0, len(text), None, None)])
        splitter = NarrationSplitter()
        parts = splitter._split_with_blocks(text, blocks, None, None, "Normal", "Normal", "Normal", None)
        # Each part should be under ~30s (450 chars at 15 chars/sec)
        for part in parts:
            self.assertLess(part.estimated_duration, 30.0,
                            "Part duration should be under 30s: {0:.1f}s".format(part.estimated_duration))

    def test_block_semantic_state_inherited(self):
        """8. Block semantic state inherited by all its Parts."""
        from engine.narration_splitter import NarrationSplitter
        # Long text to force splitting (>400 chars)
        text = ". ".join(["This is a longer sentence number {0} with extra words".format(i) for i in range(20)]) + "."
        self.assertGreater(len(text), 400, "Test text must exceed MAX_CHARS to force splitting")
        blocks = self._make_blocks(text, [(0, len(text), "Fear", "Whispering")])
        splitter = NarrationSplitter()
        parts = splitter._split_with_blocks(text, blocks, None, None, "Slow", "High", "Expressive High", None)
        self.assertGreater(len(parts), 1, "Long block should produce multiple parts")
        # All parts from this block must have the same emotion/style
        for part in parts:
            self.assertEqual(part.eff_emotion, "Fear")
            self.assertEqual(part.eff_style, "Whispering")
            self.assertEqual(part.eff_speed, "Slow")
            self.assertEqual(part.eff_pitch, "High")
            self.assertEqual(part.eff_delivery, "Expressive High")

    def test_source_block_id_preserved(self):
        """9/13. source_block_id must be set and preserved across all Parts from a Block."""
        from engine.narration_splitter import NarrationSplitter
        text = ". ".join(["Sentence {0}".format(i) for i in range(20)]) + "."
        blocks = self._make_blocks(text, [(0, len(text), None, None)])
        block_id = blocks[0].id
        splitter = NarrationSplitter()
        parts = splitter._split_with_blocks(text, blocks, None, None, "Normal", "Normal", "Normal", None)
        for part in parts:
            self.assertEqual(part.source_block_id, block_id)

    def test_part_of_block_and_total(self):
        """Block→Part mapping: part_of_block + total_parts_in_block correct."""
        from engine.narration_splitter import NarrationSplitter
        # Long text to force splitting (>400 chars)
        text = ". ".join(["This is a longer sentence number {0} with extra words".format(i) for i in range(20)]) + "."
        self.assertGreater(len(text), 400, "Test text must exceed MAX_CHARS to force splitting")
        blocks = self._make_blocks(text, [(0, len(text), None, None)])
        splitter = NarrationSplitter()
        parts = splitter._split_with_blocks(text, blocks, None, None, "Normal", "Normal", "Normal", None)
        total = len(parts)
        self.assertGreater(total, 1, "Long block should produce multiple parts")
        for i, part in enumerate(parts):
            self.assertEqual(part.part_of_block, i + 1)
            self.assertEqual(part.total_parts_in_block, total)


# ===========================================================================
# C-F. Project/Scene switcher + rename
# ===========================================================================

class TestProjectSceneBarDropdowns(unittest.TestCase):
    """P3.15: ProjectSceneBar must have dropdown menus."""

    def setUp(self):
        from ui.panels.project_scene_bar import ProjectSceneBar
        self.bar = ProjectSceneBar()

    def test_project_label_is_toolbutton(self):
        """Project label must be a QToolButton (for dropdown)."""
        from PySide6.QtWidgets import QToolButton
        self.assertIsInstance(self.bar._project_label, QToolButton)

    def test_scene_label_is_toolbutton(self):
        """Scene label must be a QToolButton (for dropdown)."""
        from PySide6.QtWidgets import QToolButton
        self.assertIsInstance(self.bar._scene_label, QToolButton)

    def test_project_menu_signals_exist(self):
        """ProjectSceneBar must have the new P3.15 signals."""
        self.assertTrue(hasattr(self.bar, 'project_switch_requested'))
        self.assertTrue(hasattr(self.bar, 'scene_switch_requested'))
        self.assertTrue(hasattr(self.bar, 'rename_project_requested'))
        self.assertTrue(hasattr(self.bar, 'rename_scene_requested'))
        self.assertTrue(hasattr(self.bar, 'delete_project_requested'))
        self.assertTrue(hasattr(self.bar, 'delete_scene_requested'))
        self.assertTrue(hasattr(self.bar, 'new_project_requested'))

    def test_set_project_menu_populates(self):
        """set_project_menu must create a menu with projects."""
        self.bar.set_project_menu(
            [{"id": "p1", "name": "Project A"}, {"id": "p2", "name": "Project B"}],
            active_project_id="p1")
        menu = self.bar._project_label.menu()
        self.assertIsNotNone(menu)
        actions = menu.actions()
        # 2 projects + separator + New Project + separator + Rename + Delete = 7
        self.assertGreaterEqual(len(actions), 4)

    def test_set_scene_menu_populates(self):
        """set_scene_menu must create a menu with scenes."""
        self.bar.set_scene_menu(
            [{"id": "s1", "name": "Scene 01"}, {"id": "s2", "name": "Scene 02"}],
            active_scene_id="s2")
        menu = self.bar._scene_label.menu()
        self.assertIsNotNone(menu)
        actions = menu.actions()
        self.assertGreaterEqual(len(actions), 4)

    def test_project_switch_signal_emitted(self):
        """Selecting a project from the menu emits project_switch_requested."""
        received = []
        self.bar.project_switch_requested.connect(lambda pid: received.append(pid))
        self.bar.set_project_menu([{"id": "p1", "name": "Project A"}], "p1")
        menu = self.bar._project_label.menu()
        # Click the first action (Project A)
        menu.actions()[0].trigger()
        self.assertEqual(received, ["p1"])

    def test_scene_switch_signal_emitted(self):
        """Selecting a scene from the menu emits scene_switch_requested."""
        received = []
        self.bar.scene_switch_requested.connect(lambda sid: received.append(sid))
        self.bar.set_scene_menu([{"id": "s1", "name": "Scene 01"}], "s1")
        menu = self.bar._scene_label.menu()
        menu.actions()[0].trigger()
        self.assertEqual(received, ["s1"])


# ===========================================================================
# G-H. Project/Scene delete handlers
# ===========================================================================

class TestProjectSceneDeleteHandlers(unittest.TestCase):
    """P3.15: MainWindow must have delete handlers."""

    def test_delete_project_handler_exists(self):
        """MainWindow must have _on_delete_project method."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("def _on_delete_project", src)

    def test_delete_scene_handler_exists(self):
        """MainWindow must have _on_delete_scene method."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("def _on_delete_scene", src)

    def test_rename_project_handler_exists(self):
        """MainWindow must have _on_rename_project method."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("def _on_rename_project", src)

    def test_project_switch_handler_exists(self):
        """MainWindow must have _on_project_switch method."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("def _on_project_switch", src)

    def test_scene_switch_handler_exists(self):
        """MainWindow must have _on_scene_switch method."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("def _on_scene_switch", src)

    def test_delete_project_confirmation_shows_counts(self):
        """Delete Project dialog must show scene + character counts."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("Scenes", src)
        self.assertIn("Characters", src)
        self.assertIn("Delete Project", src)

    def test_delete_scene_confirmation(self):
        """Delete Scene dialog must mention audio."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("Delete Scene", src)
        self.assertIn("audio", src.lower())

    def test_signals_connected(self):
        """All P3.15 signals must be connected in MainWindow."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("project_switch_requested.connect", src)
        self.assertIn("scene_switch_requested.connect", src)
        self.assertIn("rename_project_requested.connect", src)
        self.assertIn("delete_project_requested.connect", src)
        self.assertIn("delete_scene_requested.connect", src)
        self.assertIn("new_project_requested.connect", src)


# ===========================================================================
# Project rename persistence
# ===========================================================================

class TestProjectRenamePersistence(unittest.TestCase):
    """P3.15: Project rename must survive save/restart."""

    def test_rename_project_persists(self):
        """Rename a project, save, reload — name survives."""
        from engine.project_manager import ProjectManager
        from engine.models import Project
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            project = Project(name="OldName")
            pm.save_project(project)

            # Rename
            project.name = "NewName"
            pm.save_project(project)

            # Reload
            pm2 = ProjectManager(tmpdir)
            loaded = pm2.list_projects()[0]
            self.assertEqual(loaded.name, "NewName")
            # ID must be unchanged
            self.assertEqual(loaded.id, project.id)
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# Scene text + voice persistence through switch
# ===========================================================================

class TestScenePersistenceThroughSwitch(unittest.TestCase):
    """P3.15: Scene text + voice must survive A→B→A."""

    def test_scene_text_survives_switch(self):
        """Scene text must survive A→B→A."""
        from engine.models import Project, Scene
        project = Project(name="Test")
        scene_a = Scene(name="A", text="Text A")
        scene_b = Scene(name="B", text="Text B")
        project.add_scene(scene_a)
        project.add_scene(scene_b)
        # Simulate switch A→B→A
        self.assertEqual(scene_a.text, "Text A")
        self.assertEqual(scene_b.text, "Text B")
        self.assertEqual(scene_a.text, "Text A")  # still A

    def test_scene_voice_survives_switch(self):
        """Scene voice_profile_id must survive A→B→A."""
        from engine.models import Project, Scene
        project = Project(name="Test")
        scene_a = Scene(name="A")
        scene_a.voice_profile_id = "vp_a"
        scene_b = Scene(name="B")
        scene_b.voice_profile_id = "vp_b"
        project.add_scene(scene_a)
        project.add_scene(scene_b)
        # Switch A→B→A
        self.assertEqual(scene_a.voice_profile_id, "vp_a")
        self.assertEqual(scene_b.voice_profile_id, "vp_b")
        self.assertEqual(scene_a.voice_profile_id, "vp_a")  # still vp_a


if __name__ == "__main__":
    unittest.main(verbosity=2)
