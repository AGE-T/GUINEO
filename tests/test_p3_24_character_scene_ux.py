"""
SpeechStudio — P3.24 Character + Scene UX Correction
=====================================================

Runtime tests for the three UX corrections:

  A. CHARACTERS panel — a real Character management surface:
       - panel loads management rows (colour dot + name + voice dropdown)
       - empty state with an actionable "+ Add Character" button
       - "+ Add Character" quick-create (no modal), unique default names
       - inline rename (valid + empty/duplicate/too-long rejected)
       - Voice Profile assignment through the row dropdown
       - safe delete (confirmation when referenced; scene refs removed;
         block refs demoted to lost_character_id; recents cleaned)
       - canonical Character colour via the authoritative resolver
       - NarrationEditor block Character selector population
       - persistence (create → assign voice → rename → save → reload)
       - no active project → disabled/empty state, no crash

  B. New Scene menu action actually creates + activates a Scene:
       - ProjectSceneBar.new_scene_requested → _on_new_scene (the signal
         that was previously NEVER connected)
       - unique Scene ID, next sort_order, active Scene switch
       - editor state clean (text AND blocks cleared)
       - persistence through the ProjectManager architecture

  C. Assemble Audio — prominent bottom-of-sidebar action:
       - sidebar button exists, labelled, pinned at the bottom
       - visible in every primary nav context
       - clicking opens the EXISTING assembly workflow
       - Scene dropdown no longer duplicates the entry
       - File → Assemble Scenes... menu action preserved

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_24_character_scene_ux.py -v
"""

from __future__ import annotations
import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import patch, PropertyMock, MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton
_app = QApplication.instance() or QApplication([])


# ===========================================================================
# Shared harness — real Engine + real MainWindow in an isolated tmpdir
# ===========================================================================

class _MainWindowHarness:
    """Mixin providing a real MainWindow with isolated persistence."""

    @classmethod
    def _boot_main_window(cls):
        from engine.engine import Engine
        from ui.main_window import MainWindow
        from engine.project_manager import ProjectManager
        from engine.recents_manager import RecentsManager
        cls.tmpdir = tempfile.mkdtemp(prefix="ss_p324_")
        cls.engine = Engine(app_root=cls.tmpdir)
        # Bypass the model-load gate without touching the Engine class.
        cls._patcher = patch.object(
            type(cls.engine._model), "is_loaded",
            new_callable=PropertyMock, return_value=True)
        cls._patcher.start()
        cls.win = MainWindow(cls.engine)
        # Keep references to the default project so tests that null it out
        # can restore the SAME object (no mid-class identity drift).
        cls._orig_project = cls.win._active_project
        cls._orig_scene = cls.win._active_scene
        # Isolate persistence: projects → tmpdir, recents → in-memory.
        cls.projects_dir = os.path.join(cls.tmpdir, "projects")
        cls.win._project_manager = ProjectManager(cls.projects_dir)
        cls.win._recents_manager = RecentsManager()
        # Populate the sidebar's voice cache (normally done via a
        # QTimer.singleShot after startup).
        cls.win._refresh_sidebar()

    @classmethod
    def _shutdown_main_window(cls):
        try:
            cls._patcher.stop()
        except Exception:
            pass
        try:
            cls.win.close()
        except Exception:
            pass
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # -- helpers ---------------------------------------------------------
    def _char_rows(self):
        """Current _CharacterRow widgets in the sidebar context list."""
        from ui.panels.project_scene_sidebar import _CharacterRow
        lay = self.win._sidebar._context_list_layout
        rows = []
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), _CharacterRow):
                rows.append(item.widget())
        return rows

    def _goto_characters_context(self):
        """Switch the sidebar to the CHARACTERS primary context."""
        self.win._sidebar._on_nav_clicked("Characters")

    def _reload_project_from_disk(self, project_id=None):
        """Simulate Restart → Reopen: fresh ProjectManager read.

        Loads the CURRENT active project by ID (robust when multiple
        projects exist in the isolated projects dir)."""
        from engine.project_manager import ProjectManager
        pm = ProjectManager(self.projects_dir)
        pid = project_id or self.win._active_project.id
        proj = pm.get_project(pid)
        self.assertIsNotNone(proj, "project must exist on disk")
        return pm, proj

    def _restore_default_project(self):
        """Restore the original default project after a test nulled it."""
        self.win._active_project = type(self)._orig_project
        self.win._active_scene = type(self)._orig_scene


# ===========================================================================
# 1. Sidebar "+" button (P3.26 icon-only action; superseded the P3.24
#    "+ Add" pill — documented task Part 1: the icon+text pair was
#    redundant, the compact Recent action is now a clearly visible
#    plus icon only, same dispatcher + functionality)
# ===========================================================================

class TestAddButtonVisual(unittest.TestCase):
    """P3.26 §1: the + control is an icon-only square ACTION button."""

    def setUp(self):
        from ui.panels.project_scene_sidebar import Sidebar
        self.sb = Sidebar()

    def test_button_icon_only_no_text(self):
        """P3.26: icon-only — no text label, but a real visible icon."""
        self.assertFalse(self.sb._add_scene_btn.text().strip(),
                        "P3.26: the + button must NOT carry a text label")
        icon = self.sb._add_scene_btn.icon()
        self.assertFalse(icon.isNull(),
                         "the + button must show a clearly visible icon")
        self.assertGreaterEqual(self.sb._add_scene_btn.iconSize().width(), 20,
                                "icon must be a clearly visible size (>=20px)")

    def test_button_comfortable_click_target(self):
        """28×28 square (comfortable click target)."""
        self.assertGreaterEqual(self.sb._add_scene_btn.height(), 28)
        self.assertGreaterEqual(self.sb._add_scene_btn.width(), 28)

    def test_button_context_tooltips(self):
        """P3.26: per-context tooltip; no text label in any context."""
        for ctx in ("Projects", "Scenes", "Characters"):
            self.sb._on_nav_clicked(ctx)
            self.assertEqual(self.sb._add_scene_btn.text(), "")
            self.assertEqual(self.sb._add_scene_btn.toolTip(),
                             "Add {0}".format(ctx[:-1]))

    def test_button_hidden_for_history(self):
        """History has no creation action — button hidden."""
        self.sb._on_nav_clicked("History")
        self.assertTrue(self.sb._add_scene_btn.isHidden())

    def test_button_hover_and_pressed_states(self):
        """Stylesheet defines hover + pressed states (visual affordance)."""
        ss = self.sb._add_scene_btn.styleSheet()
        self.assertIn("QToolButton:hover", ss)
        self.assertIn("QToolButton:pressed", ss)

    def test_button_is_real_clickable_control(self):
        """Real QToolButton + the context-aware dispatcher stays wired
        (functionality unchanged — behavioural proof)."""
        from PySide6.QtWidgets import QToolButton
        self.assertIsInstance(self.sb._add_scene_btn, QToolButton)
        emitted = []
        self.sb.add_scene_requested.connect(lambda: emitted.append("scene"))
        self.sb._on_nav_clicked("Scenes")
        self.sb._on_add_button_clicked()
        self.assertEqual(emitted, ["scene"])


# ===========================================================================
# 2. Sidebar bottom Assemble Audio action
# ===========================================================================

class TestSidebarAssembleAudioButton(unittest.TestCase):
    """P3.24 §8: Assemble Audio is a prominent bottom-of-sidebar action."""

    def setUp(self):
        from ui.panels.project_scene_sidebar import Sidebar
        self.sb = Sidebar()

    def test_button_exists_and_labelled(self):
        self.assertTrue(hasattr(self.sb, "_assemble_btn"))
        self.assertEqual(self.sb._assemble_btn.text().strip(),
                         "Assemble Audio")

    def test_button_is_last_widget_in_layout(self):
        """Pinned at the BOTTOM of the sidebar layout."""
        lay = self.sb.layout()
        last = None
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item and item.widget():
                last = item.widget()
        self.assertIs(last, self.sb._assemble_btn,
                      "Assemble Audio must be the bottom-most widget")

    def test_button_not_hidden_in_any_context(self):
        """Reachable regardless of the selected primary context."""
        for ctx in ("Projects", "Scenes", "History", "Characters"):
            self.sb._on_nav_clicked(ctx)
            self.assertFalse(self.sb._assemble_btn.isHidden(),
                             "hidden in context: {0}".format(ctx))

    def test_button_hover_and_pressed_states(self):
        ss = self.sb._assemble_btn.styleSheet()
        self.assertIn("QPushButton:hover", ss)
        self.assertIn("QPushButton:pressed", ss)

    def test_button_emits_signal(self):
        received = []
        self.sb.assemble_audio_requested.connect(lambda: received.append(1))
        self.sb._assemble_btn.click()
        self.assertEqual(received, [1])

    def test_button_strong_affordance(self):
        """Accent-styled button — visually distinct from plain nav rows."""
        self.assertGreaterEqual(self.sb._assemble_btn.height(), 40)
        self.assertIn("qlineargradient", self.sb._assemble_btn.styleSheet())


# ===========================================================================
# 3. Scene dropdown cleanup (duplicate Assemble Audio removed)
# ===========================================================================

class TestSceneDropdownCleanup(unittest.TestCase):
    """P3.24 §10: one clear primary location for Assemble Audio."""

    def setUp(self):
        from ui.panels.project_scene_bar import ProjectSceneBar
        self.bar = ProjectSceneBar()
        self.bar.set_scene_menu([{"id": "s1", "name": "Scene One"}], "s1")
        self.actions = [a.text() for a in self.bar._scene_label.menu().actions()]

    def test_assemble_audio_removed_from_scene_dropdown(self):
        self.assertNotIn("Assemble Audio...", self.actions)

    def test_new_scene_still_in_dropdown(self):
        self.assertIn("New Scene", self.actions)

    def test_scene_actions_preserved(self):
        for expected in ("Rename Scene", "Delete Scene", "Move Up", "Move Down"):
            self.assertIn(expected, self.actions)

    def test_assemble_signal_removed_from_bar(self):
        """No dead signal remains on ProjectSceneBar."""
        self.assertFalse(hasattr(self.bar, "assemble_audio_requested"))

    def test_file_menu_assemble_action_preserved(self):
        """File → Assemble Scenes... remains (standard menu path)."""
        with open(os.path.join(_ROOT, "ui/panels/menu_bar.py")) as f:
            src = f.read()
        self.assertIn("Assemble Scenes...", src)


# ===========================================================================
# 4. CHARACTERS panel — runtime (real MainWindow)
# ===========================================================================

class TestCharactersPanel(_MainWindowHarness, unittest.TestCase):
    """P3.24 §1/§3/§11/§12/§13/§14/§15: the real Character management panel."""

    @classmethod
    def setUpClass(cls):
        cls._boot_main_window()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown_main_window()

    # -- panel loads -----------------------------------------------------
    def test_panel_loads_management_rows(self):
        """Characters context renders management rows with voice dropdowns."""
        from engine.models import Character
        v = self.engine.create_voice("Male Deep")
        c = Character(name="Engineer", voice_profile_id=v.id)
        self.win._active_project.characters = [c]
        self._goto_characters_context()
        rows = self._char_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]._name_label.text(), "Engineer")
        # The row dropdown shows the assigned Voice Profile.
        self.assertEqual(rows[0]._voice_combo.currentText(), "Male Deep")

    def test_panel_shows_all_project_characters(self):
        from engine.models import Character
        self.win._active_project.characters = [
            Character(name="Engineer"),
            Character(name="Lady"),
            Character(name="Narrator"),
        ]
        self._goto_characters_context()
        rows = self._char_rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual([r._name_label.text() for r in rows],
                         ["Engineer", "Lady", "Narrator"])

    # -- empty state -----------------------------------------------------
    def test_empty_state_message_and_add_button(self):
        """No Characters → 'No characters yet' + actionable Add button."""
        self.win._active_project.characters = []
        self._goto_characters_context()
        lay = self.win._sidebar._context_list_layout
        labels = []
        buttons = []
        for i in range(lay.count()):
            w = lay.itemAt(i).widget() if lay.itemAt(i) else None
            if w:
                from PySide6.QtWidgets import QLabel
                if isinstance(w, QLabel):
                    labels.append(w.text())
                elif isinstance(w, QPushButton):
                    buttons.append(w.text())
        self.assertIn("No characters yet", labels)
        self.assertIn("+ Add Character", buttons)

    def test_empty_state_add_button_creates_character(self):
        """Clicking the empty-state Add button runs the quick-create flow."""
        self.win._active_project.characters = []
        self._goto_characters_context()
        lay = self.win._sidebar._context_list_layout
        add_btn = None
        for i in range(lay.count()):
            w = lay.itemAt(i).widget() if lay.itemAt(i) else None
            if isinstance(w, QPushButton) and "Add Character" in w.text():
                add_btn = w
        self.assertIsNotNone(add_btn, "empty-state Add button missing")
        add_btn.click()
        self.assertEqual(len(self.win._active_project.characters), 1)
        self.assertEqual(self.win._active_project.characters[0].name,
                         "New Character")

    # -- quick create ----------------------------------------------------
    def test_quick_create_no_modal(self):
        """'+ Add Character' creates immediately — no dialog required."""
        self.win._active_project.characters = []
        self._goto_characters_context()
        self.win._on_character_create_quick()
        chars = self.win._active_project.characters
        self.assertEqual(len(chars), 1)
        self.assertEqual(chars[0].name, "New Character")
        # Panel refreshed: the new row appears + is selected.
        rows = self._char_rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]._is_active)
        # Editor Character selector contains the new Character.
        combo_texts = [self.win._editor._character_combo.itemText(i)
                       for i in range(self.win._editor._character_combo.count())]
        self.assertIn("New Character", combo_texts)

    def test_quick_create_unique_default_names(self):
        """Second create → 'New Character 2' (no silent merging)."""
        self.win._active_project.characters = []
        self.win._on_character_create_quick()
        self.win._on_character_create_quick()
        names = [c.name for c in self.win._active_project.characters]
        self.assertEqual(names, ["New Character", "New Character 2"])

    def test_quick_create_persists(self):
        self.win._active_project.characters = []
        self.win._on_character_create_quick()
        chars = self.win._active_project.characters
        self.assertTrue(chars, "character must exist")
        pm, proj = self._reload_project_from_disk()
        self.assertIn(chars[0].id, [c.id for c in proj.characters])

    # -- rename ------------------------------------------------------------
    def test_rename_valid(self):
        from engine.models import Character
        c = Character(name="Engineer")
        self.win._active_project.characters = [c]
        self.win._on_character_rename_requested(c.id, "  Captain  ")
        self.assertEqual(c.name, "Captain")
        pm, proj = self._reload_project_from_disk()
        self.assertEqual(proj.characters[0].name, "Captain")

    def test_rename_empty_rejected(self):
        from engine.models import Character
        c = Character(name="Engineer")
        self.win._active_project.characters = [c]
        self.win._on_character_rename_requested(c.id, "   ")
        self.assertEqual(c.name, "Engineer", "empty rename must be rejected")

    def test_rename_duplicate_rejected(self):
        from engine.models import Character
        c1 = Character(name="Engineer")
        c2 = Character(name="Lady")
        self.win._active_project.characters = [c1, c2]
        # No modal warning blocks the test: patch it away.
        with patch("ui.main_window.QMessageBox") as mb:
            mb.warning = MagicMock()
            self.win._on_character_rename_requested(c1.id, "Lady")
        self.assertEqual(c1.name, "Engineer", "duplicate rename rejected")
        self.assertNotEqual(c1.id, c2.id, "IDs remain authoritative")

    def test_rename_too_long_rejected(self):
        from engine.models import Character
        c = Character(name="Engineer")
        self.win._active_project.characters = [c]
        self.win._on_character_rename_requested(c.id, "X" * 100)
        self.assertEqual(c.name, "Engineer", "overlong rename rejected")

    def test_rename_special_characters_allowed(self):
        from engine.models import Character
        c = Character(name="Engineer")
        self.win._active_project.characters = [c]
        self.win._on_character_rename_requested(c.id, "Dr. Éva #1 (main)")
        self.assertEqual(c.name, "Dr. Éva #1 (main)")

    # -- voice assignment --------------------------------------------------
    def test_voice_assignment_from_row(self):
        from engine.models import Character
        v = self.engine.create_voice("Female Soft")
        c = Character(name="Lady")
        self.win._active_project.characters = [c]
        self._goto_characters_context()
        self.win._on_character_voice_requested(c.id, v.id)
        self.assertEqual(c.voice_profile_id, v.id)
        # Row dropdown reflects the assignment.
        rows = self._char_rows()
        self.assertEqual(rows[0]._voice_combo.currentText(), "Female Soft")
        # Persisted.
        pm, proj = self._reload_project_from_disk()
        self.assertEqual(proj.characters[0].voice_profile_id, v.id)

    def test_voice_clear_from_row(self):
        from engine.models import Character
        v = self.engine.create_voice("Male Deep")
        c = Character(name="Engineer", voice_profile_id=v.id)
        self.win._active_project.characters = [c]
        self.win._on_character_voice_requested(c.id, "")
        self.assertIsNone(c.voice_profile_id)

    def test_row_voice_combo_lists_engine_voices(self):
        from engine.models import Character
        v1 = self.engine.create_voice("Male Deep")
        v2 = self.engine.create_voice("Female Soft")
        c = Character(name="Narrator")
        self.win._active_project.characters = [c]
        self.win._refresh_sidebar()  # voices → sidebar cache
        self._goto_characters_context()
        rows = self._char_rows()
        combo_texts = [rows[0]._voice_combo.itemText(i)
                       for i in range(rows[0]._voice_combo.count())]
        self.assertIn("No voice", combo_texts)
        self.assertIn("Male Deep", combo_texts)
        self.assertIn("Female Soft", combo_texts)

    # -- canonical colour --------------------------------------------------
    def test_row_dot_uses_authoritative_color(self):
        from engine.models import Character
        from engine.character_colors import get_character_color
        c = Character(name="Engineer")
        self.win._active_project.characters = [c]
        self._goto_characters_context()
        rows = self._char_rows()
        expected = get_character_color(c.id).hex_main
        ss = rows[0]._dot.styleSheet()
        self.assertIn(expected, ss,
                      "row dot must use the authoritative Character colour")

    # -- block Character selector (§3) -------------------------------------
    def test_editor_selector_populated_after_create(self):
        """After creating Engineer, the block selector shows Engineer."""
        self.win._active_project.characters = []
        self._goto_characters_context()
        self.win._on_character_create_quick()
        self.win._on_character_rename_requested(
            self.win._active_project.characters[0].id, "Engineer")
        combo = self.win._editor._character_combo
        texts = [combo.itemText(i) for i in range(combo.count())]
        self.assertIn("Engineer", texts)

    def test_block_character_assignment_via_selector(self):
        """Selecting a Character on a block sets block.character_id."""
        from engine.models import Character
        from engine.narration_blocks import PromptBlock
        c = Character(name="Engineer")
        self.win._active_project.characters = [c]
        self.win._update_editor_characters()
        text = "Some narration text for the block."
        block = PromptBlock(start_offset=0, end_offset=len(text),
                            character_id=None)
        self.win._editor.block_manager._blocks = [block]
        self.win._editor._selected_block_id = block.id
        combo = self.win._editor._character_combo
        idx = combo.findData(c.id)
        self.assertGreaterEqual(idx, 0, "character must be in the selector")
        combo.setCurrentIndex(idx)  # fires _on_character_changed
        self.assertEqual(block.character_id, c.id)

    def test_character_removal_clears_block_reference(self):
        """Deleting the Character clears block refs (lost_character_id)."""
        from engine.models import Character
        from engine.narration_blocks import PromptBlock
        c = Character(name="Engineer")
        self.win._active_project.characters = [c]
        text = "Some narration text for the block."
        block = PromptBlock(start_offset=0, end_offset=len(text),
                            character_id=c.id)
        self.win._editor.block_manager._blocks = [block]
        self.win._active_scene.character_ids = [c.id]
        # Confirm deletion (mock the modal).
        with patch("ui.main_window.QMessageBox") as mb:
            mb.question.return_value = mb.StandardButton.Yes
            self.win._on_character_delete_requested(c.id)
        self.assertEqual(self.win._active_project.characters, [])
        self.assertEqual(self.win._active_scene.character_ids, [],
                         "scene character_ids must be cleaned")
        self.assertIsNone(block.character_id,
                          "block character_id must be cleared")
        self.assertEqual(block.lost_character_id, c.id,
                         "block ref demoted to lost_character_id")

    # -- delete -------------------------------------------------------------
    def test_delete_unreferenced_character(self):
        from engine.models import Character
        c = Character(name="Lonely")
        self.win._active_project.characters = [c]
        with patch("ui.main_window.QMessageBox") as mb:
            mb.question.return_value = mb.StandardButton.Yes
            self.win._on_character_delete_requested(c.id)
        self.assertEqual(self.win._active_project.characters, [])
        pm, proj = self._reload_project_from_disk()
        self.assertEqual(proj.characters, [], "delete must persist")

    def test_delete_referenced_character_confirmation(self):
        """Referenced Character: confirmation message mentions references."""
        from engine.models import Character, Scene
        c = Character(name="Engineer")
        self.win._active_project.characters = [c]
        scene = Scene(name="Main Scene")
        scene.character_ids = [c.id]
        self.win._active_project.scenes.append(scene)
        captured = {}
        with patch("ui.main_window.QMessageBox") as mb:
            mb.StandardButton.Yes = 1
            mb.question.return_value = 1
            # Capture the message text.
            def _q(parent, title, text, *a, **k):
                captured["text"] = text
                return 1
            mb.question.side_effect = _q
            self.win._on_character_delete_requested(c.id)
        self.assertIn("Main Scene", captured.get("text", ""),
                      "confirmation must list referencing scenes")

    def test_delete_cancelled_keeps_character(self):
        from engine.models import Character
        c = Character(name="Engineer")
        self.win._active_project.characters = [c]
        with patch("ui.main_window.QMessageBox") as mb:
            mb.StandardButton.No = 0
            mb.question.return_value = 0  # No
            self.win._on_character_delete_requested(c.id)
        self.assertEqual(len(self.win._active_project.characters), 1)

    def test_delete_clears_selected_character(self):
        from engine.models import Character
        c = Character(name="Engineer")
        self.win._active_project.characters = [c]
        self.win._selected_character_id = c.id
        with patch("ui.main_window.QMessageBox") as mb:
            mb.StandardButton.Yes = 1
            mb.question.return_value = 1
            self.win._on_character_delete_requested(c.id)
        self.assertIsNone(self.win._selected_character_id)

    # -- no active project (§12) -------------------------------------------
    def test_no_active_project_empty_state(self):
        self.win._active_project = None
        try:
            self._goto_characters_context()
            entries = self.win._build_context_list_entries("Characters")
            self.assertEqual(entries, [], "no global characters without project")
            # Quick create refuses gracefully (no crash, no character).
            with patch("ui.main_window.QMessageBox") as mb:
                mb.information = MagicMock()
                self.win._on_character_create_quick()
            self.assertEqual(self._char_rows(), [])
        finally:
            self._restore_default_project()

    # -- persistence (§16) --------------------------------------------------
    def test_character_persistence_round_trip(self):
        from engine.models import Character
        from engine.narration_blocks import PromptBlock
        v = self.engine.create_voice("Male Deep")
        c = Character(name="Engineer", voice_profile_id=v.id)
        self.win._active_project.characters = [c]
        # Block Character assignment on the active scene.
        text = "Narration for persistence test."
        block = PromptBlock(start_offset=0, end_offset=len(text),
                            character_id=c.id)
        self.win._active_scene.narration_blocks = [block.to_dict()]
        self.win._active_scene.character_ids = [c.id]
        # Save via the authoritative handler path.
        self.win._project_manager.save_project(self.win._active_project)
        # Restart → Reopen.
        pm, proj = self._reload_project_from_disk()
        self.assertEqual(len(proj.characters), 1)
        self.assertEqual(proj.characters[0].name, "Engineer")
        self.assertEqual(proj.characters[0].voice_profile_id, v.id)
        self.assertEqual(proj.scenes[0].character_ids, [c.id])
        restored_blocks = proj.scenes[0].narration_blocks
        self.assertEqual(restored_blocks[0].get("character_id"), c.id)

    def test_recents_updated_after_character_changes(self):
        from engine.models import Character
        c = Character(name="Engineer")
        self.win._active_project.characters = [c]
        self._goto_characters_context()
        self.win._on_character_create_quick()
        recent_ids = [r.entity_id
                      for r in self.win._recents_manager.get_recent_characters()]
        self.assertIn(self.win._active_project.characters[-1].id, recent_ids)


# ===========================================================================
# 5. New Scene (real MainWindow)
# ===========================================================================

class TestNewSceneAction(_MainWindowHarness, unittest.TestCase):
    """P3.24 §5/§6/§7: the Scene dropdown's New Scene actually works."""

    @classmethod
    def setUpClass(cls):
        cls._boot_main_window()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown_main_window()

    def test_new_scene_signal_connected(self):
        """The previously-dead signal is wired to the handler."""
        # Runtime proof: emitting the bar's signal creates a Scene.
        before = len(self.win._active_project.scenes)
        self.win._project_scene_bar.new_scene_requested.emit()
        self.assertEqual(len(self.win._active_project.scenes), before + 1)

    def test_new_scene_unique_id_and_sort_order(self):
        before = len(self.win._active_project.scenes)
        max_order = max((s.sort_order for s in self.win._active_project.scenes),
                        default=-1)
        self.win._project_scene_bar.new_scene_requested.emit()
        new_scene = self.win._active_project.scenes[-1]
        ids = [s.id for s in self.win._active_project.scenes]
        self.assertEqual(len(ids), len(set(ids)), "scene IDs unique")
        self.assertEqual(new_scene.sort_order, max_order + 1)

    def test_new_scene_becomes_active(self):
        self.win._project_scene_bar.new_scene_requested.emit()
        new_scene = self.win._active_project.scenes[-1]
        self.assertIs(self.win._active_scene, new_scene)
        self.assertEqual(self.win._project_scene_bar._scene_label.text(),
                         new_scene.name)

    def test_new_scene_default_name_sequence(self):
        # Isolate from scenes created by earlier tests in this class.
        from engine.models import Scene
        self.win._active_project.scenes = [
            Scene(name="01 Untitled", sort_order=0)]
        self.win._active_scene = self.win._active_project.scenes[0]
        self.win._project_scene_bar.new_scene_requested.emit()
        first = self.win._active_project.scenes[-1].name
        self.assertEqual(first, "Untitled Scene")
        self.win._project_scene_bar.new_scene_requested.emit()
        second = self.win._active_project.scenes[-1].name
        self.assertEqual(second, "Untitled Scene 2")

    def test_new_scene_editor_state_clean(self):
        """Previous scene's text AND blocks do not leak into the new scene."""
        from engine.narration_blocks import PromptBlock
        self.win._editor.set_text("Old scene content.")
        text = "Old scene content."
        block = PromptBlock(start_offset=0, end_offset=len(text),
                            character_id=None)
        self.win._editor.block_manager._blocks = [block]
        self.win._project_scene_bar.new_scene_requested.emit()
        self.assertEqual(self.win._editor.get_text(), "")
        self.assertEqual(self.win._editor.block_manager.blocks, [])

    def test_new_scene_persists(self):
        self.win._project_scene_bar.new_scene_requested.emit()
        created = self.win._active_project.scenes[-1]
        pm, proj = self._reload_project_from_disk()
        self.assertIn(created.id, [s.id for s in proj.scenes],
                      "new scene must survive save/reload")

    def test_new_scene_updates_sidebar_and_recents(self):
        self.win._project_scene_bar.new_scene_requested.emit()
        created = self.win._active_project.scenes[-1]
        recent_scene_ids = [r.entity_id
                            for r in self.win._recents_manager.get_recent_scenes()]
        self.assertIn(created.id, recent_scene_ids)
        # Scene menu contains the new scene.
        menu = self.win._project_scene_bar._scene_label.menu()
        menu_names = [a.text() for a in menu.actions()]
        self.assertIn(created.name, menu_names)

    def test_new_scene_requires_project(self):
        self.win._active_project = None
        try:
            with patch("ui.main_window.QMessageBox") as mb:
                mb.information = MagicMock()
                self.win._project_scene_bar.new_scene_requested.emit()
        finally:
            self._restore_default_project()

    def test_scene_switch_via_dropdown_signal(self):
        """Scene switching still works after the menu changes."""
        self.win._project_scene_bar.new_scene_requested.emit()
        created = self.win._active_project.scenes[-1]
        # Switch back to the first scene via the authoritative signal.
        first = self.win._get_sorted_scenes()[0]
        self.win._project_scene_bar.scene_switch_requested.emit(first.id)
        self.assertIs(self.win._active_scene, first)


# ===========================================================================
# 6. Assemble Audio runtime (real MainWindow)
# ===========================================================================

class TestAssembleAudioRuntime(_MainWindowHarness, unittest.TestCase):
    """P3.24 §8/§9: the sidebar action opens the EXISTING assembly workflow."""

    @classmethod
    def setUpClass(cls):
        cls._boot_main_window()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown_main_window()

    class _FakeDialog:
        """Stands in for AssembleScenesDialog — records construction."""
        instances = []

        def __init__(self, project, app_root, parent=None):
            type(self).instances.append({"project": project,
                                         "app_root": app_root})
            self.assembly_result = None
            self.combined_entry = None

        def exec(self):
            return 0

    def test_sidebar_button_opens_assembly_workflow(self):
        """Clicking the bottom-sidebar button opens AssembleScenesDialog."""
        import ui.panels.assemble_dialog as ad
        self._FakeDialog.instances = []
        with patch.object(ad, "AssembleScenesDialog", self._FakeDialog):
            self.win._sidebar._assemble_btn.click()
        self.assertEqual(len(self._FakeDialog.instances), 1,
                         "assembly dialog must be constructed")
        self.assertIs(self._FakeDialog.instances[0]["project"],
                      self.win._active_project)

    def test_assembly_handler_is_single_authoritative_path(self):
        """The sidebar action reuses _on_assemble_scenes — no second path."""
        with patch.object(self.win, "_on_assemble_scenes") as handler:
            self.win._sidebar._assemble_btn.click()
        handler.assert_called_once()

    def test_no_project_assembly_shows_info(self):
        self.win._active_project = None
        try:
            with patch("ui.main_window.QMessageBox") as mb:
                mb.information = MagicMock()
                self.win._sidebar._assemble_btn.click()
                mb.information.assert_called_once()
        finally:
            self._restore_default_project()

    def test_scene_bar_no_assemble_connection(self):
        """No dead connection to the removed bar signal remains."""
        import inspect
        src = inspect.getsource(type(self.win))
        self.assertNotIn(
            "_project_scene_bar.assemble_audio_requested.connect", src)


# ===========================================================================
# 7. Sidebar layout sanity (§20)
# ===========================================================================

class TestSidebarLayoutSanity(unittest.TestCase):
    """P3.24 §20: the new Assemble Audio action must not regress layout."""

    def test_layout_widget_order(self):
        from ui.panels.project_scene_sidebar import Sidebar
        sb = Sidebar()
        lay = sb.layout()
        widgets = []
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item and item.widget():
                widgets.append(type(item.widget()).__name__)
        # CTA, 4 nav items, assemble button — plus scrolls/labels.
        # Bottom-most widget must be the Assemble Audio QPushButton.
        self.assertGreaterEqual(widgets.count("_NavItemButton"), 4)
        self.assertEqual(widgets[-1], "QPushButton",
                         "bottom widget must be the Assemble Audio button")

    def test_sidebar_minimal_height_reasonable(self):
        """Sidebar must remain usable at small window heights."""
        from ui.panels.project_scene_sidebar import Sidebar
        sb = Sidebar()
        sb.resize(280, 480)
        self.assertLessEqual(sb._assemble_btn.geometry().bottom(), 480)
        self.assertFalse(sb._assemble_btn.isHidden())


if __name__ == "__main__":
    unittest.main()
