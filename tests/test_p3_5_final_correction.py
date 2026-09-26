"""
SpeechStudio — P3.5 Final Correction Tests
==========================================

Tests for the two P3.5 final correction requirements:

1. HISTORY is a PRIMARY navigation context (Projects, Scenes, History,
   Characters) — not under the legacy LIBRARY section.

2. Active state in the context list is derived from the authoritative
   MainWindow state (_active_project, _active_scene, selected Character)
   — no independent booleans.

These tests are designed to run WITHOUT PySide6 installed. The sidebar
UI structure is verified via AST inspection of the source file (checking
that _nav_history is created in the primary nav section and connected to
_on_nav_clicked). The data-layer behaviour (RecentsManager,
HistoryManager, ProjectManager) is tested directly.

Run:
    python3 -m pytest tests/test_p3_5_final_correction.py -v
or:
    python3 tests/test_p3_5_final_correction.py
"""

from __future__ import annotations
import os
import sys
import ast
import json
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure the SpeechStudio root is on sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ===========================================================================
# Helper: AST inspection of project_scene_sidebar.py
# ===========================================================================

SIDEBAR_PATH = os.path.join(_ROOT, "ui", "panels", "project_scene_sidebar.py")


def _sidebar_ast() -> ast.Module:
    """Parse the sidebar source into an AST."""
    with open(SIDEBAR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    return ast.parse(source)


def _find_class(module: ast.Module, name: str) -> ast.ClassDef:
    """Find a top-level class definition by name."""
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise ValueError("Class {0} not found".format(name))


def _find_method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    """Find a method in a class by name."""
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise ValueError("Method {0} not found in {1}".format(name, cls.name))


def _method_contains_string(method: ast.FunctionDef, needle: str) -> bool:
    """Check if a method's source contains a given string."""
    return needle in ast.unparse(method)


def _find_assignments_with_attr(cls: ast.ClassDef, attr_name: str) -> list:
    """Find all self.<attr_name> = ... assignments in a class (any method)."""
    results = []
    for node in ast.walk(cls):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                        and target.attr == attr_name):
                    results.append(node)
    return results


# ===========================================================================
# Test 1: HISTORY appears as a primary navigation context
# ===========================================================================

class TestHistoryIsPrimaryNav(unittest.TestCase):
    """Tests that HISTORY is in the primary navigation, not LIBRARY."""

    def test_nav_history_exists_as_attribute(self):
        """The sidebar must have a self._nav_history attribute."""
        tree = _sidebar_ast()
        cls = _find_class(tree, "Sidebar")
        assigns = _find_assignments_with_attr(cls, "_nav_history")
        self.assertGreaterEqual(
            len(assigns), 1,
            "Sidebar must assign self._nav_history")

    def test_nav_history_connected_to_on_nav_clicked(self):
        """_nav_history must be connected to _on_nav_clicked (primary nav handler),
        NOT _on_library_clicked."""
        tree = _sidebar_ast()
        cls = _find_class(tree, "Sidebar")
        build_ui = _find_method(cls, "_build_ui")
        source = ast.unparse(build_ui)
        # Find the _nav_history assignment block
        self.assertIn("_nav_history = _NavItemButton", source)
        # It must be connected to _on_nav_clicked
        self.assertIn(
            "_nav_history.clicked_signal.connect(self._on_nav_clicked)",
            source,
            "History nav must connect to _on_nav_clicked (primary nav handler)")

    def test_nav_history_not_in_library_section(self):
        """_nav_history must NOT appear in the _on_library_clicked handler."""
        tree = _sidebar_ast()
        cls = _find_class(tree, "Sidebar")
        lib_method = _find_method(cls, "_on_library_clicked")
        lib_source = ast.unparse(lib_method)
        # The library handler should NOT have a History branch
        self.assertNotIn(
            'label == "History"',
            lib_source,
            "History must not be handled in _on_library_clicked")

    def test_on_nav_clicked_handles_history(self):
        """_on_nav_clicked must set active state for History and emit history_clicked."""
        tree = _sidebar_ast()
        cls = _find_class(tree, "Sidebar")
        nav_method = _find_method(cls, "_on_nav_clicked")
        source = ast.unparse(nav_method)
        # ast.unparse uses single quotes — check for both forms
        self.assertTrue(
            "_nav_history.set_active(label == 'History')" in source or
            '_nav_history.set_active(label == "History")' in source,
            "_on_nav_clicked must set _nav_history active for History")
        self.assertIn("self.history_clicked.emit()", source)

    def test_on_nav_clicked_deactivates_library_on_history(self):
        """When History is clicked, the primary nav handles it correctly.

        P3.6 update: The LIBRARY section has been removed, so there are no
        longer library items to deactivate. The test now verifies that
        History is one of the primary nav items that gets set_active called.
        """
        tree = _sidebar_ast()
        cls = _find_class(tree, "Sidebar")
        nav_method = _find_method(cls, "_on_nav_clicked")
        source = ast.unparse(nav_method)
        self.assertIn("'History'", source)
        # History must be in the set_active calls
        self.assertIn("_nav_history.set_active", source)


# ===========================================================================
# Test 2: Selecting HISTORY updates Recents to Recent Audio
# ===========================================================================

class TestHistoryRecentsHeading(unittest.TestCase):
    """Tests that selecting History updates the Recents heading."""

    def test_update_recents_heading_has_history(self):
        """_update_recents_heading must map History to 'RECENT AUDIO'."""
        tree = _sidebar_ast()
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_update_recents_heading")
        source = ast.unparse(method)
        # ast.unparse uses single quotes
        self.assertTrue(
            "'History': 'RECENT AUDIO'" in source or
            '"History": "RECENT AUDIO"' in source,
            "History must map to 'RECENT AUDIO'")

    def test_update_recents_heading_has_all_history(self):
        """_update_recents_heading must map History context list to 'ALL HISTORY'."""
        tree = _sidebar_ast()
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_update_recents_heading")
        source = ast.unparse(method)
        self.assertTrue(
            "'History': 'ALL HISTORY'" in source or
            '"History": "ALL HISTORY"' in source,
            "History context list must map to 'ALL HISTORY'")


# ===========================================================================
# Test 3 & 4: History context list uses HistoryManager data
# ===========================================================================

class TestHistoryContextListSource(unittest.TestCase):
    """Tests that the History context list is built from HistoryManager."""

    def test_build_context_list_entries_history_uses_engine(self):
        """MainWindow._build_context_list_entries for History must call
        self._engine.get_history()."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_build_context_list_entries")
        src = ast.unparse(method)
        self.assertIn("get_history()", src,
                        "History context list must use engine.get_history()")

    def test_history_manager_list_entries_newest_first(self):
        """HistoryManager.list_entries() returns entries newest-first."""
        from engine.history_manager import HistoryManager
        from engine.models import GenerationResult
        tmpdir = tempfile.mkdtemp()
        try:
            hm = HistoryManager(tmpdir)
            # Add two entries
            r1 = GenerationResult(
                success=True,
                output_path="a.wav",
                prompt="test1",
                generation_time=1.0,
                output_duration=2.0,
            )
            r1.timestamp = "20240101_120000"
            r1.project = "Proj"
            hm.add(r1)

            r2 = GenerationResult(
                success=True,
                output_path="b.wav",
                prompt="test2",
                generation_time=1.0,
                output_duration=3.0,
            )
            r2.timestamp = "20240102_120000"
            r2.project = "Proj"
            hm.add(r2)

            entries = hm.list_entries()
            self.assertEqual(len(entries), 2)
            # Newest first
            self.assertEqual(entries[0].id, "20240102_120000")
            self.assertEqual(entries[1].id, "20240101_120000")
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# Test 5, 6, 7: Active state derivation
# ===========================================================================

class TestActiveStateDerivation(unittest.TestCase):
    """Tests that _get_active_context_entity_id derives active state
    from authoritative MainWindow state."""

    def test_get_active_context_entity_id_method_exists(self):
        """MainWindow must have _get_active_context_entity_id method."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        # This should NOT raise
        method = _find_method(cls, "_get_active_context_entity_id")
        self.assertIsNotNone(method)

    def test_active_projects_uses_active_project_id(self):
        """For Projects context, active id = _active_project.id."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_get_active_context_entity_id")
        src = ast.unparse(method)
        self.assertIn("self._active_project.id", src)

    def test_active_scenes_uses_active_scene_id(self):
        """For Scenes context, active id = _active_scene.id."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_get_active_context_entity_id")
        src = ast.unparse(method)
        self.assertIn("self._active_scene.id", src)

    def test_active_characters_uses_selected_character_id(self):
        """For Characters context, active id = _selected_character_id."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_get_active_context_entity_id")
        src = ast.unparse(method)
        self.assertIn("self._selected_character_id", src)

    def test_no_independent_active_boolean(self):
        """There must be no independent boolean tracking active state.
        The active state must be derived from entity IDs only."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        # There should NOT be a boolean like _is_active_project or _active_in_list
        self.assertNotIn("_is_active_project", source)
        self.assertNotIn("_is_active_scene", source)
        self.assertNotIn("_is_active_character", source)
        self.assertNotIn("_active_in_context_list", source)


# ===========================================================================
# Test 8, 9, 10: Active state updates after switches
# ===========================================================================

class TestActiveStateUpdates(unittest.TestCase):
    """Tests that active state refreshes are wired into switch methods."""

    def test_load_native_project_calls_update_recents(self):
        """_load_native_project must call _update_recents (which rebuilds
        the context list with active state)."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_load_native_project")
        src = ast.unparse(method)
        self.assertIn("self._update_recents()", src)

    def test_switch_to_scene_calls_update_recents(self):
        """_switch_to_scene must call _update_recents (refreshes active scene)."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_switch_to_scene")
        src = ast.unparse(method)
        self.assertIn("self._update_recents()", src)

    def test_character_dialog_selection_captured(self):
        """_on_open_character_management must capture selected_character_id
        from the dialog after it closes."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_on_open_character_management")
        src = ast.unparse(method)
        self.assertIn("selected_character_id", src)
        self.assertIn("self._selected_character_id", src)

    def test_character_dialog_exposes_selected_character_id(self):
        """CharacterManagementDialog must expose selected_character_id property."""
        cd_path = os.path.join(_ROOT, "ui", "panels", "character_dialog.py")
        with open(cd_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "CharacterManagementDialog")
        # Should have a property named selected_character_id
        found = False
        for node in cls.body:
            if isinstance(node, ast.FunctionDef) and node.name == "selected_character_id":
                # Check it's decorated as a property
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name) and dec.id == "property":
                        found = True
                        break
        self.assertTrue(found, "CharacterManagementDialog must have a "
                               "selected_character_id @property")


# ===========================================================================
# Test 11: Existing Recents behaviour remains intact
# ===========================================================================

class TestRecentsBehaviourIntact(unittest.TestCase):
    """Tests that the Recents system still works as before."""

    def test_recents_manager_four_lists(self):
        """RecentsManager must still have 4 MRU lists."""
        from engine.recents_manager import RecentsManager
        tmpdir = tempfile.mkdtemp()
        try:
            sm = MagicMock()
            sm.get.return_value = {}
            rm = RecentsManager(sm)
            self.assertTrue(hasattr(rm, "get_recent_projects"))
            self.assertTrue(hasattr(rm, "get_recent_scenes"))
            self.assertTrue(hasattr(rm, "get_recent_characters"))
            self.assertTrue(hasattr(rm, "get_recent_history"))
        finally:
            shutil.rmtree(tmpdir)

    def test_recents_max_five(self):
        """RecentsManager must cap each list at 5 items."""
        from engine.recents_manager import RecentsManager, MAX_RECENTS
        self.assertEqual(MAX_RECENTS, 5)
        sm = MagicMock()
        sm.get.return_value = {}
        rm = RecentsManager(sm)
        for i in range(10):
            rm.add_recent_project("proj_{0}".format(i), "Project {0}".format(i))
        self.assertEqual(len(rm.get_recent_projects()), 5)

    def test_sidebar_set_recents_method_exists(self):
        """Sidebar must still have the set_recents method."""
        tree = _sidebar_ast()
        cls = _find_class(tree, "Sidebar")
        # Should not raise
        method = _find_method(cls, "set_recents")
        self.assertIsNotNone(method)

    def test_sidebar_recent_item_clicked_signal_exists(self):
        """Sidebar must still have the recent_item_clicked signal."""
        with open(SIDEBAR_PATH, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("recent_item_clicked = Signal", source)

    def test_context_list_click_uses_recent_item_clicked(self):
        """Context list rows must emit recent_item_clicked (same as Recents)."""
        tree = _sidebar_ast()
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "set_context_list")
        src = ast.unparse(method)
        self.assertIn("recent_item_clicked.emit", src)


# ===========================================================================
# Test: set_active_context_item method
# ===========================================================================

class TestSetActiveContextItem(unittest.TestCase):
    """Tests that the sidebar has set_active_context_item for lightweight refresh."""

    def test_method_exists(self):
        """Sidebar must have set_active_context_item method."""
        tree = _sidebar_ast()
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "set_active_context_item")
        self.assertIsNotNone(method)

    def test_method_iterates_context_list(self):
        """set_active_context_item must iterate _context_list_layout."""
        tree = _sidebar_ast()
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "set_active_context_item")
        src = ast.unparse(method)
        self.assertIn("_context_list_layout", src)
        self.assertIn("set_active", src)

    def test_set_context_list_accepts_active_id(self):
        """set_context_list must accept an active_id parameter."""
        tree = _sidebar_ast()
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "set_context_list")
        # Check the signature includes active_id
        args = [a.arg for a in method.args.args]
        self.assertIn("active_id", args,
                       "set_context_list must accept active_id parameter")


# ===========================================================================
# Test: Context list click resolution fallback
# ===========================================================================

class TestContextListClickResolution(unittest.TestCase):
    """Tests that context list clicks resolve even when not in RecentsManager."""

    def test_scene_click_fallback_searches_all_projects(self):
        """_on_recent_scene_clicked must fall back to searching all projects
        via ProjectManager when the scene is not in RecentsManager."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_on_recent_scene_clicked")
        src = ast.unparse(method)
        # Must have a fallback that iterates list_projects()
        self.assertIn("list_projects()", src)
        self.assertIn("get_scene(entity_id)", src)

    def test_character_click_fallback_searches_all_projects(self):
        """_on_recent_character_clicked must fall back to searching all projects."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_on_recent_character_clicked")
        src = ast.unparse(method)
        self.assertIn("list_projects()", src)
        self.assertIn("get_character(entity_id)", src)


# ===========================================================================
# Test: History click opens HistoryView (not dialog on nav click)
# ===========================================================================

class TestHistoryNavDoesNotOpenDialog(unittest.TestCase):
    """Tests that clicking History nav switches context, not opens dialog."""

    def test_sidebar_history_clicked_not_connected_to_focus_history(self):
        """The sidebar's history_clicked signal must NOT be connected to
        _focus_history (which opens the dialog). It should only switch context."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        # The line "self._sidebar.history_clicked.connect(self._focus_history)"
        # must NOT exist anymore
        self.assertNotIn(
            "self._sidebar.history_clicked.connect(self._focus_history)",
            source,
            "Sidebar history nav must not open the dialog directly")

    def test_sidebar_history_clicked_connected_to_nav_context(self):
        """The sidebar's history_clicked must be connected to context switch."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn(
            'self._sidebar.history_clicked.connect(lambda: self._on_nav_context_changed("History"))',
            source)

    def test_history_item_click_opens_dialog(self):
        """P3.34 SUPERSESSION: a History ENTRY click must NOT open the
        HistoryViewDialog.

        The original P3.5 contract (click entry → open the modal) is
        explicitly reversed by the P3.34 user decision: the History list
        behaves like every other context list — clicking an entry only
        SELECTS it (authoritative _last_viewed_history_id highlight).
        The full view opens explicitly via the ALL HISTORY header icon
        (open_history_view_requested → _focus_history) or the View menu.
        """
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_on_recent_history_clicked")
        # Unparse the BODY only (skip the docstring — it references the
        # old/new behaviour by name in prose).
        body = [stmt for stmt in method.body
                if not (isinstance(stmt, ast.Expr)
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str))]
        src = "\n".join(ast.unparse(stmt) for stmt in body)
        # The click handler must select, never open the modal view.
        self.assertNotIn(
            "HistoryViewDialog", src,
            "History entry clicks must not open the HistoryViewDialog "
            "(P3.34 UX correction)")
        self.assertIn("_last_viewed_history_id", src,
                      "click handler must update the authoritative "
                      "selection state")
        # The full view opens via the header icon → the EXISTING handler.
        self.assertIn(
            "self._sidebar.open_history_view_requested.connect(self._focus_history)",
            source,
            "the ALL HISTORY header icon must reuse the canonical "
            "_focus_history opener")


# ===========================================================================
# Test: _update_context_list_active method
# ===========================================================================

class TestUpdateContextListActive(unittest.TestCase):
    """Tests the lightweight active-state refresh method."""

    def test_method_exists(self):
        """MainWindow must have _update_context_list_active method."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_update_context_list_active")
        self.assertIsNotNone(method)

    def test_method_calls_set_active_context_item(self):
        """_update_context_list_active must call sidebar.set_active_context_item."""
        mw_path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(mw_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_update_context_list_active")
        src = ast.unparse(method)
        self.assertIn("set_active_context_item", src)


# ===========================================================================
# Integration: RecentsManager + ProjectManager round-trip
# ===========================================================================

class TestRecentsProjectManagerIntegration(unittest.TestCase):
    """Integration test: RecentsManager + ProjectManager work together."""

    def test_project_appears_in_list_after_save(self):
        """A saved project must appear in list_projects()."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            proj = Project(name="TestProject")
            scene = Scene(name="Scene 1")
            proj.add_scene(scene)
            pm.save_project(proj)
            projects = pm.list_projects()
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0].name, "TestProject")
        finally:
            shutil.rmtree(tmpdir)

    def test_scene_identity_is_project_plus_scene_id(self):
        """Two scenes with the same name in different projects must have
        distinct identities (project_id + scene_id)."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            p1 = Project(name="Proj A")
            s1 = Scene(name="Scene 01")
            p1.add_scene(s1)
            pm.save_project(p1)

            p2 = Project(name="Proj B")
            s2 = Scene(name="Scene 01")  # same name!
            p2.add_scene(s2)
            pm.save_project(p2)

            projects = pm.list_projects()
            self.assertEqual(len(projects), 2)
            # Same scene name but different project + scene id
            scene_names = [p.scenes[0].name for p in projects]
            self.assertEqual(scene_names, ["Scene 01", "Scene 01"])
            # But project ids differ
            project_ids = [p.id for p in projects]
            self.assertNotEqual(project_ids[0], project_ids[1])
            # And scene ids differ
            scene_ids = [p.scenes[0].id for p in projects]
            self.assertNotEqual(scene_ids[0], scene_ids[1])
        finally:
            shutil.rmtree(tmpdir)

    def test_character_identity_is_project_plus_character_id(self):
        """Two characters with the same name in different projects must have
        distinct identities."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Character
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            p1 = Project(name="Proj A")
            c1 = Character(name="Engineer")
            p1.add_character(c1)
            pm.save_project(p1)

            p2 = Project(name="Proj B")
            c2 = Character(name="Engineer")  # same name!
            p2.add_character(c2)
            pm.save_project(p2)

            projects = pm.list_projects()
            char_names = [p.characters[0].name for p in projects]
            self.assertEqual(char_names, ["Engineer", "Engineer"])
            char_ids = [p.characters[0].id for p in projects]
            self.assertNotEqual(char_ids[0], char_ids[1])
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# Test: Restart preserves authoritative datasets
# ===========================================================================

class TestRestartPreservesData(unittest.TestCase):
    """Tests that data survives a restart (persistence)."""

    def test_project_persistence(self):
        """A project saved to disk must be loadable in a new ProjectManager instance."""
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene, Character
        tmpdir = tempfile.mkdtemp()
        try:
            pm1 = ProjectManager(tmpdir)
            proj = Project(name="PersistTest")
            proj.add_scene(Scene(name="S1"))
            proj.add_character(Character(name="C1"))
            pm1.save_project(proj)

            # Simulate restart — new ProjectManager instance
            pm2 = ProjectManager(tmpdir)
            projects = pm2.list_projects()
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0].name, "PersistTest")
            self.assertEqual(len(projects[0].scenes), 1)
            self.assertEqual(len(projects[0].characters), 1)
        finally:
            shutil.rmtree(tmpdir)

    def test_recents_persistence(self):
        """Recents must survive a restart via settings.json."""
        from engine.recents_manager import RecentsManager
        from engine.settings_manager import SettingsManager
        tmpdir = tempfile.mkdtemp()
        try:
            settings_dir = os.path.join(tmpdir, "settings")
            os.makedirs(settings_dir)
            sm1 = SettingsManager(settings_dir)
            rm1 = RecentsManager(sm1)
            rm1.add_recent_project("proj_123", "My Project")

            # Simulate restart
            sm2 = SettingsManager(settings_dir)
            rm2 = RecentsManager(sm2)
            recents = rm2.get_recent_projects()
            self.assertEqual(len(recents), 1)
            self.assertEqual(recents[0].entity_id, "proj_123")
            self.assertEqual(recents[0].display_name, "My Project")
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# Test: Empty states
# ===========================================================================

class TestEmptyStates(unittest.TestCase):
    """Tests that empty states are handled."""

    def test_empty_project_list(self):
        """An empty projects directory returns an empty list."""
        from engine.project_manager import ProjectManager
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            self.assertEqual(pm.list_projects(), [])
        finally:
            shutil.rmtree(tmpdir)

    def test_empty_history(self):
        """An empty history directory returns an empty list."""
        from engine.history_manager import HistoryManager
        tmpdir = tempfile.mkdtemp()
        try:
            hm = HistoryManager(tmpdir)
            self.assertEqual(hm.list_entries(), [])
        finally:
            shutil.rmtree(tmpdir)

    def test_empty_recents(self):
        """A fresh RecentsManager has empty lists."""
        from engine.recents_manager import RecentsManager
        sm = MagicMock()
        sm.get.return_value = {}
        rm = RecentsManager(sm)
        self.assertEqual(rm.get_recent_projects(), [])
        self.assertEqual(rm.get_recent_scenes(), [])
        self.assertEqual(rm.get_recent_characters(), [])
        self.assertEqual(rm.get_recent_history(), [])


# ===========================================================================
# Test: Stale cleanup
# ===========================================================================

class TestStaleCleanup(unittest.TestCase):
    """Tests that stale entries are cleaned up."""

    def test_remove_stale_project(self):
        """remove_stale_project removes the project AND its scenes/characters."""
        from engine.recents_manager import RecentsManager
        sm = MagicMock()
        sm.get.return_value = {}
        rm = RecentsManager(sm)
        rm.add_recent_project("p1", "Proj 1")
        rm.add_recent_scene("p1", "s1", "Scene 1")
        rm.add_recent_character("p1", "c1", "Char 1")
        rm.remove_stale_project("p1")
        self.assertEqual(rm.get_recent_projects(), [])
        self.assertEqual(rm.get_recent_scenes(), [])
        self.assertEqual(rm.get_recent_characters(), [])

    def test_remove_stale_scene(self):
        """remove_stale_scene removes only the specified scene."""
        from engine.recents_manager import RecentsManager
        sm = MagicMock()
        sm.get.return_value = {}
        rm = RecentsManager(sm)
        rm.add_recent_scene("p1", "s1", "Scene 1")
        rm.add_recent_scene("p1", "s2", "Scene 2")
        rm.remove_stale_scene("p1", "s1")
        scenes = rm.get_recent_scenes()
        self.assertEqual(len(scenes), 1)
        self.assertEqual(scenes[0].entity_id, "s2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
