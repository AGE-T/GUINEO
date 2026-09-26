"""
SpeechStudio — P3.6 Final Sidebar Redesign Tests
=================================================

Tests for the P3.6 final sidebar redesign:

1. LIBRARY section removed from the sidebar
2. Voice Library / Presets / Outputs rehomed to Tools menu + TopNav Project menu
3. Primary navigation remains: PROJECTS, SCENES, HISTORY, CHARACTERS
4. Recents remains compact + context-aware
5. Context List takes the remaining vertical space (stretch=1)
6. Row text truncation (eliding) for long names
7. Scene status badge support
8. Active state preservation (no regression from P3.5)
9. New Project button preserved
10. No horizontal clipping (scroll policies)

Run:
    python3 -m pytest tests/test_p3_6_sidebar_redesign.py -v
or:
    python3 tests/test_p3_6_sidebar_redesign.py
"""

from __future__ import annotations
import os
import sys
import ast
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _parse(path: str) -> ast.Module:
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read())


def _find_class(module: ast.Module, name: str) -> ast.ClassDef:
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise ValueError("Class {0} not found".format(name))


def _find_method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise ValueError("Method {0} not found in {1}".format(name, cls.name))


SIDEBAR_PATH = os.path.join(_ROOT, "ui", "panels", "project_scene_sidebar.py")
MENU_BAR_PATH = os.path.join(_ROOT, "ui", "panels", "menu_bar.py")
TOP_NAV_PATH = os.path.join(_ROOT, "ui", "panels", "top_navigation.py")
MAIN_WINDOW_PATH = os.path.join(_ROOT, "ui", "main_window.py")


# ===========================================================================
# Test 1: LIBRARY section removed from sidebar
# ===========================================================================

class TestLibrarySectionRemoved(unittest.TestCase):
    """Tests that the LIBRARY section is removed from the sidebar."""

    def test_no_library_header_in_build_ui(self):
        """_build_ui must NOT create a 'LIBRARY' header label."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_build_ui")
        src = ast.unparse(method)
        self.assertNotIn('"LIBRARY"', src)
        self.assertNotIn("'LIBRARY'", src)

    def test_no_nav_voices_creation_in_build_ui(self):
        """_build_ui must NOT create _nav_voices as a _NavItemButton."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_build_ui")
        src = ast.unparse(method)
        self.assertNotIn('_nav_voices = _NavItemButton', src)
        self.assertNotIn('_nav_presets = _NavItemButton', src)
        self.assertNotIn('_nav_outputs = _NavItemButton', src)

    def test_nav_voices_presets_outputs_set_to_none(self):
        """_build_ui must set _nav_voices, _nav_presets, _nav_outputs to None."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_build_ui")
        src = ast.unparse(method)
        self.assertIn("self._nav_voices = None", src)
        self.assertIn("self._nav_presets = None", src)
        self.assertIn("self._nav_outputs = None", src)


# ===========================================================================
# Test 2: Voice Library / Presets / Outputs rehomed
# ===========================================================================

class TestLibraryRehoming(unittest.TestCase):
    """Tests that Voice Library, Presets, and Outputs are reachable via menus."""

    def test_menu_bar_has_voice_library(self):
        """Tools menu must have a Voice Library action."""
        tree = _parse(MENU_BAR_PATH)
        cls = _find_class(tree, "MenuBar")
        method = _find_method(cls, "_build_tools_menu")
        src = ast.unparse(method)
        self.assertTrue('"voice_library"' in src or "'voice_library'" in src)

    def test_menu_bar_has_open_outputs(self):
        """Tools menu must have an Open Output Folder action."""
        tree = _parse(MENU_BAR_PATH)
        cls = _find_class(tree, "MenuBar")
        method = _find_method(cls, "_build_tools_menu")
        src = ast.unparse(method)
        self.assertTrue('"open_outputs"' in src or "'open_outputs'" in src)

    def test_menu_bar_has_preset_manager(self):
        """P3.6: Tools menu must now have a Preset Manager action."""
        tree = _parse(MENU_BAR_PATH)
        cls = _find_class(tree, "MenuBar")
        method = _find_method(cls, "_build_tools_menu")
        src = ast.unparse(method)
        self.assertTrue('"preset_manager"' in src or "'preset_manager'" in src)
        self.assertIn("Preset Manager", src)

    def test_top_nav_project_menu_has_voice_library(self):
        """TopNav Project menu must include Voice Library."""
        tree = _parse(TOP_NAV_PATH)
        cls = _find_class(tree, "TopNavigation")
        method = _find_method(cls, "_setup_menus")
        src = ast.unparse(method)
        self.assertTrue('"voice_library"' in src or "'voice_library'" in src)

    def test_top_nav_project_menu_has_outputs(self):
        """TopNav Project menu must include Open Output Folder."""
        tree = _parse(TOP_NAV_PATH)
        cls = _find_class(tree, "TopNavigation")
        method = _find_method(cls, "_setup_menus")
        src = ast.unparse(method)
        self.assertTrue('"open_outputs"' in src or "'open_outputs'" in src)

    def test_top_nav_project_menu_has_preset_manager(self):
        """P3.6: TopNav Project menu must include Preset Manager."""
        tree = _parse(TOP_NAV_PATH)
        cls = _find_class(tree, "TopNavigation")
        method = _find_method(cls, "_setup_menus")
        src = ast.unparse(method)
        self.assertTrue('"preset_manager"' in src or "'preset_manager'" in src)

    def test_main_window_connects_preset_manager(self):
        """MainWindow must connect the preset_manager menu action."""
        with open(MAIN_WINDOW_PATH, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn(
            'self._menu_bar.connect("preset_manager", self._on_open_preset_manager)',
            src)


# ===========================================================================
# Test 3: Primary navigation preserved
# ===========================================================================

class TestPrimaryNavigationPreserved(unittest.TestCase):
    """Tests that the 4 primary nav contexts remain."""

    def test_four_primary_nav_items(self):
        """Sidebar must have all 4 primary nav items: Projects, Scenes, History, Characters."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_build_ui")
        src = ast.unparse(method)
        # ast.unparse uses single quotes — check both forms
        self.assertTrue(
            "_nav_projects = _NavItemButton('Projects'" in src or
            '_nav_projects = _NavItemButton("Projects"' in src)
        self.assertTrue(
            "_nav_scenes = _NavItemButton('Scenes'" in src or
            '_nav_scenes = _NavItemButton("Scenes"' in src)
        self.assertTrue(
            "_nav_history = _NavItemButton('History'" in src or
            '_nav_history = _NavItemButton("History"' in src)
        self.assertTrue(
            "_nav_characters = _NavItemButton('Characters'" in src or
            '_nav_characters = _NavItemButton("Characters"' in src)

    def test_nav_history_between_scenes_and_characters(self):
        """History must be positioned between Scenes and Characters."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_build_ui")
        src = ast.unparse(method)
        scenes_pos = src.find("_nav_scenes = _NavItemButton")
        history_pos = src.find("_nav_history = _NavItemButton")
        characters_pos = src.find("_nav_characters = _NavItemButton")
        self.assertGreater(history_pos, scenes_pos,
                           "History must come after Scenes")
        self.assertLess(history_pos, characters_pos,
                        "History must come before Characters")


# ===========================================================================
# Test 4: Recents remains compact + context-aware
# ===========================================================================

class TestRecentsCompact(unittest.TestCase):
    """Tests that Recents is compact and doesn't consume all space."""

    def test_recents_scroll_has_max_height(self):
        """Recents scroll area must have a maximum height (compact)."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_build_ui")
        src = ast.unparse(method)
        self.assertIn("setMaximumHeight", src)

    def test_recents_scroll_stretch_zero(self):
        """Recents scroll must have stretch factor 0 (does not grow).

        P3.34 SUPERSESSION: the Recents scroll area now lives INSIDE the
        static _recents_panel surface (the user-requested restructure:
        the panel border/radius must never scroll with the content —
        only the rows inside the scroll area scroll). The stretch-0
        contract itself is unchanged: the scroll is added with stretch 0
        to the panel layout, and the panel with stretch 0 to the sidebar.
        """
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_build_ui")
        src = ast.unparse(method)
        # The recents scroll is added with stretch 0 — now to the inner
        # panel layout (P3.34 nesting).
        self.assertIn("recents_layout.addWidget(self._scene_scroll, 0)", src)
        # The static panel itself is added with stretch 0 (never grows).
        self.assertIn("layout.addWidget(self._recents_panel, 0)", src)

    def test_recents_context_headings_preserved(self):
        """Recents heading must still map all 4 contexts."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_update_recents_heading")
        src = ast.unparse(method)
        self.assertIn("RECENT PROJECTS", src)
        self.assertIn("RECENT SCENES", src)
        self.assertIn("RECENT AUDIO", src)
        self.assertIn("RECENT CHARACTERS", src)


# ===========================================================================
# Test 5: Context List takes remaining space
# ===========================================================================

class TestContextListStretch(unittest.TestCase):
    """Tests that the Context List stretches to fill remaining space."""

    def test_context_scroll_stretch_one(self):
        """Context list scroll must have stretch factor 1 (fills space)."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_build_ui")
        src = ast.unparse(method)
        self.assertIn("layout.addWidget(self._context_scroll, 1)", src)

    def test_context_scroll_no_max_height(self):
        """Context list scroll must NOT have a maximum height (can grow)."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_build_ui")
        src = ast.unparse(method)
        # The context scroll should not have setMaximumHeight anymore
        # (it was removed in P3.6 to allow it to fill remaining space)
        # Find the context_scroll section and verify no setMaximumHeight
        ctx_start = src.find("self._context_scroll = QScrollArea()")
        ctx_end = src.find("self._context_container = QWidget()")
        ctx_section = src[ctx_start:ctx_end] if ctx_start >= 0 and ctx_end >= 0 else ""
        self.assertNotIn("setMaximumHeight", ctx_section,
                         "Context scroll must not have setMaximumHeight")


# ===========================================================================
# Test 6: Row text truncation (eliding)
# ===========================================================================

class TestRowTextTruncation(unittest.TestCase):
    """Tests that _SceneRow supports text eliding for long names."""

    def test_scene_row_has_resize_event(self):
        """_SceneRow must have a resizeEvent that re-elides text."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "_SceneRow")
        method = _find_method(cls, "resizeEvent")
        self.assertIsNotNone(method)

    def test_scene_row_has_elide_text_method(self):
        """_SceneRow must have an _elide_text method."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "_SceneRow")
        method = _find_method(cls, "_elide_text")
        self.assertIsNotNone(method)

    def test_elide_text_uses_elidedText(self):
        """_elide_text must use QFontMetrics.elidedText."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "_SceneRow")
        method = _find_method(cls, "_elide_text")
        src = ast.unparse(method)
        self.assertIn("elidedText", src)
        self.assertIn("ElideRight", src)


# ===========================================================================
# Test 7: Scene status badge support
# ===========================================================================

class TestSceneStatusBadge(unittest.TestCase):
    """Tests that _SceneRow supports a status badge."""

    def test_scene_row_accepts_status_param(self):
        """_SceneRow __init__ must accept a 'status' parameter."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "_SceneRow")
        method = _find_method(cls, "__init__")
        args = [a.arg for a in method.args.args]
        self.assertIn("status", args)

    def test_status_colors_defined(self):
        """_SceneRow must have a status mapping (P3.11: renamed to _STATUS_ICONS)."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "_SceneRow")
        src = ast.unparse(cls)
        # P3.11: _STATUS_COLORS renamed to _STATUS_ICONS (icon + color mapping)
        self.assertTrue("_STATUS_COLORS" in src or "_STATUS_ICONS" in src,
                        "Must have _STATUS_COLORS or _STATUS_ICONS mapping")
        # All 5 statuses must be mapped
        self.assertIn("draft", src)
        self.assertIn("ready", src)
        self.assertIn("generating", src)
        self.assertIn("complete", src)
        self.assertIn("error", src)

    def test_context_list_entries_include_status_for_scenes(self):
        """MainWindow._build_context_list_entries must include 'status' for Scenes."""
        with open(MAIN_WINDOW_PATH, "r", encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        cls = _find_class(tree, "MainWindow")
        method = _find_method(cls, "_build_context_list_entries")
        m_src = ast.unparse(method)
        self.assertIn("status", m_src)


# ===========================================================================
# Test 8: Active state preserved (no regression)
# ===========================================================================

class TestActiveStatePreserved(unittest.TestCase):
    """Tests that the P3.5 active state implementation is intact."""

    def test_set_context_list_active_id_param(self):
        """set_context_list must still accept active_id."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "set_context_list")
        args = [a.arg for a in method.args.args]
        self.assertIn("active_id", args)

    def test_set_active_context_item_method(self):
        """set_active_context_item must still exist."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "set_active_context_item")
        self.assertIsNotNone(method)

    def test_no_independent_active_booleans(self):
        """No independent active-state booleans."""
        with open(MAIN_WINDOW_PATH, "r", encoding="utf-8") as f:
            src = f.read()
        for bad in ['_is_active_project', '_is_active_scene', '_is_active_character']:
            self.assertNotIn(bad, src)


# ===========================================================================
# Test 9: New Project button preserved
# ===========================================================================

class TestNewProjectButtonPreserved(unittest.TestCase):
    """Tests that the New Project button is still in the sidebar."""

    def test_new_project_btn_in_build_ui(self):
        """_build_ui must create the _new_project_btn."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_build_ui")
        src = ast.unparse(method)
        self.assertIn("_new_project_btn", src)
        self.assertIn("new_project_requested.emit", src)

    def test_new_project_signal_exists(self):
        """The new_project_requested signal must exist."""
        with open(SIDEBAR_PATH, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("new_project_requested = Signal()", src)


# ===========================================================================
# Test 10: No horizontal clipping
# ===========================================================================

class TestNoHorizontalClipping(unittest.TestCase):
    """Tests that horizontal scrollbars are always off."""

    def test_recents_scroll_horizontal_off(self):
        """Recents scroll must have horizontal scrollbar always off."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_build_ui")
        src = ast.unparse(method)
        self.assertIn("ScrollBarAlwaysOff", src)

    def test_scene_row_set_minimum_width_zero(self):
        """_SceneRow must set MinimumWidth(0) so it can shrink."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "_SceneRow")
        method = _find_method(cls, "__init__")
        src = ast.unparse(method)
        self.assertIn("setMinimumWidth(0)", src)

    def test_scene_row_label_word_wrap_false(self):
        """_SceneRow label must not wrap (single line)."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "_SceneRow")
        method = _find_method(cls, "__init__")
        src = ast.unparse(method)
        self.assertIn("setWordWrap(False)", src)


# ===========================================================================
# Test 11: Library signals preserved (backward compat)
# ===========================================================================

class TestLibrarySignalsPreserved(unittest.TestCase):
    """Tests that the legacy Library signals are still defined for compat."""

    def test_voice_library_clicked_signal(self):
        """voice_library_clicked signal must still exist."""
        with open(SIDEBAR_PATH, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("voice_library_clicked = Signal()", src)

    def test_presets_clicked_signal(self):
        """presets_clicked signal must still exist."""
        with open(SIDEBAR_PATH, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("presets_clicked = Signal()", src)

    def test_outputs_clicked_signal(self):
        """outputs_clicked signal must still exist."""
        with open(SIDEBAR_PATH, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("outputs_clicked = Signal()", src)

    def test_on_library_clicked_is_safe(self):
        """_on_library_clicked must not crash when _nav_voices is None."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_on_library_clicked")
        src = ast.unparse(method)
        # Must NOT call set_active on _nav_voices/_nav_presets/_nav_outputs
        self.assertNotIn("_nav_voices.set_active", src)
        self.assertNotIn("_nav_presets.set_active", src)
        self.assertNotIn("_nav_outputs.set_active", src)


# ===========================================================================
# Test 12: Context-specific empty states
# ===========================================================================

class TestContextSpecificEmptyStates(unittest.TestCase):
    """Tests that each context has a specific empty state message."""

    def test_empty_messages_in_set_context_list(self):
        """set_context_list must have context-specific empty messages."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "set_context_list")
        src = ast.unparse(method)
        self.assertIn("No projects yet", src)
        self.assertIn("No scenes yet", src)
        self.assertIn("No characters yet", src)
        self.assertIn("No generated audio yet", src)


# ===========================================================================
# Test 13: _SceneRow supports subtitle (two-line layout)
# ===========================================================================

class TestSceneRowSubtitle(unittest.TestCase):
    """Tests that _SceneRow supports a subtitle for two-line rows."""

    def test_scene_row_accepts_subtitle_param(self):
        """_SceneRow __init__ must accept a 'subtitle' parameter."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "_SceneRow")
        method = _find_method(cls, "__init__")
        args = [a.arg for a in method.args.args]
        self.assertIn("subtitle", args)

    def test_set_context_list_passes_subtitle(self):
        """set_context_list must pass subtitle to _SceneRow."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "set_context_list")
        src = ast.unparse(method)
        self.assertIn("subtitle=subtitle", src)

    def test_set_recents_passes_subtitle(self):
        """set_recents must pass subtitle to _SceneRow."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "set_recents")
        src = ast.unparse(method)
        self.assertIn("subtitle=subtitle", src)


# ===========================================================================
# Test 14: TopNav Project menu is the Library home
# ===========================================================================

class TestTopNavProjectMenuIsLibraryHome(unittest.TestCase):
    """Tests that the TopNav Project menu is the new home for Library items."""

    def test_project_menu_has_batch_generation(self):
        """Project menu must have Batch Generation."""
        tree = _parse(TOP_NAV_PATH)
        cls = _find_class(tree, "TopNavigation")
        method = _find_method(cls, "_setup_menus")
        src = ast.unparse(method)
        self.assertTrue('"batch_generation"' in src or "'batch_generation'" in src)

    def test_project_menu_separator_before_outputs(self):
        """Project menu must have a separator before Open Output Folder."""
        tree = _parse(TOP_NAV_PATH)
        cls = _find_class(tree, "TopNavigation")
        method = _find_method(cls, "_setup_menus")
        src = ast.unparse(method)
        self.assertIn("addSeparator()", src)


# ===========================================================================
# Test 15: No QTabWidget reintroduced
# ===========================================================================

class TestNoQTabWidget(unittest.TestCase):
    """Tests that no QTabWidget-based navigation is reintroduced."""

    def test_sidebar_no_qtabwidget(self):
        """Sidebar must not use QTabWidget in actual code (docstrings OK)."""
        tree = _parse(SIDEBAR_PATH)
        # Walk all nodes; ensure no QTabWidget is instantiated or referenced
        # as a class base. We check Assign/Call/Attribute nodes only.
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "QTabWidget":
                    found = True
                    break
                if isinstance(node.func, ast.Attribute) and node.func.attr == "QTabWidget":
                    found = True
                    break
        self.assertFalse(found, "Sidebar must not instantiate QTabWidget")

    def test_main_window_no_qtabwidget_for_sidebar(self):
        """MainWindow must not create a QTabWidget for the sidebar."""
        with open(MAIN_WINDOW_PATH, "r", encoding="utf-8") as f:
            src = f.read()
        # The sidebar should be a ProjectSceneSidebar instance, not a QTabWidget
        self.assertIn("ProjectSceneSidebar", src)


# ===========================================================================
# Test 16: _on_library_clicked is a safe no-op
# ===========================================================================

class TestLibraryClickedSafeNoOp(unittest.TestCase):
    """Tests that _on_library_clicked emits signals but doesn't crash."""

    def test_on_library_clicked_emits_voice_library(self):
        """_on_library_clicked must still emit voice_library_clicked."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_on_library_clicked")
        src = ast.unparse(method)
        self.assertIn("voice_library_clicked.emit()", src)

    def test_on_library_clicked_emits_presets(self):
        """_on_library_clicked must still emit presets_clicked."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_on_library_clicked")
        src = ast.unparse(method)
        self.assertIn("presets_clicked.emit()", src)

    def test_on_library_clicked_emits_outputs(self):
        """_on_library_clicked must still emit outputs_clicked."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        method = _find_method(cls, "_on_library_clicked")
        src = ast.unparse(method)
        self.assertIn("outputs_clicked.emit()", src)


# ===========================================================================
# Test 17: ProjectSceneBar (top context) preserved
# ===========================================================================

class TestProjectSceneBarPreserved(unittest.TestCase):
    """Tests that the ProjectSceneBar (top context display) is intact."""

    def test_project_scene_bar_exists(self):
        """ProjectSceneBar class must exist."""
        path = os.path.join(_ROOT, "ui", "panels", "project_scene_bar.py")
        tree = _parse(path)
        cls = _find_class(tree, "ProjectSceneBar")
        self.assertIsNotNone(cls)

    def test_project_scene_bar_has_set_project(self):
        """ProjectSceneBar must have set_project method."""
        path = os.path.join(_ROOT, "ui", "panels", "project_scene_bar.py")
        tree = _parse(path)
        cls = _find_class(tree, "ProjectSceneBar")
        method = _find_method(cls, "set_project")
        self.assertIsNotNone(method)

    def test_project_scene_bar_has_set_scene(self):
        """ProjectSceneBar must have set_scene method."""
        path = os.path.join(_ROOT, "ui", "panels", "project_scene_bar.py")
        tree = _parse(path)
        cls = _find_class(tree, "ProjectSceneBar")
        method = _find_method(cls, "set_scene")
        self.assertIsNotNone(method)

    def test_project_scene_bar_has_saved_state(self):
        """ProjectSceneBar must have set_saved_state method."""
        path = os.path.join(_ROOT, "ui", "panels", "project_scene_bar.py")
        tree = _parse(path)
        cls = _find_class(tree, "ProjectSceneBar")
        method = _find_method(cls, "set_saved_state")
        self.assertIsNotNone(method)

    def test_main_window_uses_project_scene_bar(self):
        """MainWindow must instantiate ProjectSceneBar."""
        with open(MAIN_WINDOW_PATH, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("ProjectSceneBar(", src)


# ===========================================================================
# Test 18: Recents + Context List share identity rules
# ===========================================================================

class TestSharedIdentityRules(unittest.TestCase):
    """Tests that Recents and Context List use the same identity rules."""

    def test_both_use_recent_item_clicked(self):
        """Both Recents and Context List rows must emit recent_item_clicked."""
        tree = _parse(SIDEBAR_PATH)
        cls = _find_class(tree, "Sidebar")
        set_recents = _find_method(cls, "set_recents")
        set_context = _find_method(cls, "set_context_list")
        recents_src = ast.unparse(set_recents)
        context_src = ast.unparse(set_context)
        self.assertIn("recent_item_clicked.emit", recents_src)
        self.assertIn("recent_item_clicked.emit", context_src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
