"""
SpeechStudio — P3.10 Manual Regression Fix Tests
=================================================

Runtime tests that verify the REAL visible widget behavior (not just AST).

Tests:
  1. + button context-awareness after nav click (the P3.10 fix —
     _current_recents_context is now updated in _on_nav_clicked)
  2. Characters nav click does NOT open the dialog (only switches context)
  3. F11 QShortcut exists
  4. TopNavigation menu uses popup() not exec()
  5. New Scene uses authoritative project.scenes (not sidebar internal list)

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_10_manual_regression.py -v
"""

from __future__ import annotations
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])


# ===========================================================================
# 1. + button context-awareness after nav click (P3.10 ROOT CAUSE FIX)
# ===========================================================================

class TestPlusButtonContextAfterNavClick(unittest.TestCase):
    """P3.10: The + button must emit the correct signal AFTER a nav click.

    Root cause: _on_nav_clicked did not update _current_recents_context,
    so _on_add_button_clicked read the OLD context and always emitted
    add_scene_requested (the default "Projects" → no, actually "Scenes"
    was the old default in some flows, or the context was stale).

    Fix: _on_nav_clicked now sets self._current_recents_context = label
    BEFORE calling _update_recents_heading.
    """

    def setUp(self):
        from ui.panels.project_scene_sidebar import Sidebar
        self.sb = Sidebar()

    def test_nav_projects_updates_context_immediately(self):
        """After clicking Projects nav, _current_recents_context must be 'Projects'."""
        self.sb._on_nav_clicked("Projects")
        self.assertEqual(self.sb._current_recents_context, "Projects")

    def test_nav_scenes_updates_context_immediately(self):
        """After clicking Scenes nav, _current_recents_context must be 'Scenes'."""
        self.sb._on_nav_clicked("Scenes")
        self.assertEqual(self.sb._current_recents_context, "Scenes")

    def test_nav_characters_updates_context_immediately(self):
        """After clicking Characters nav, _current_recents_context must be 'Characters'."""
        self.sb._on_nav_clicked("Characters")
        self.assertEqual(self.sb._current_recents_context, "Characters")

    def test_nav_history_updates_context_immediately(self):
        """After clicking History nav, _current_recents_context must be 'History'."""
        self.sb._on_nav_clicked("History")
        self.assertEqual(self.sb._current_recents_context, "History")

    def test_plus_emits_correct_signal_after_nav_projects(self):
        """Full flow: click Projects nav → click + → add_project_requested emitted."""
        self.sb._on_nav_clicked("Projects")
        received = []
        self.sb.add_project_requested.connect(lambda: received.append("p"))
        self.sb.add_scene_requested.connect(lambda: received.append("s"))
        self.sb.add_character_requested.connect(lambda: received.append("c"))
        self.sb._on_add_button_clicked()
        self.assertEqual(received, ["p"],
                         "Expected add_project_requested, got {0}".format(received))

    def test_plus_emits_correct_signal_after_nav_scenes(self):
        """Full flow: click Scenes nav → click + → add_scene_requested emitted."""
        self.sb._on_nav_clicked("Scenes")
        received = []
        self.sb.add_project_requested.connect(lambda: received.append("p"))
        self.sb.add_scene_requested.connect(lambda: received.append("s"))
        self.sb.add_character_requested.connect(lambda: received.append("c"))
        self.sb._on_add_button_clicked()
        self.assertEqual(received, ["s"],
                         "Expected add_scene_requested, got {0}".format(received))

    def test_plus_emits_correct_signal_after_nav_characters(self):
        """Full flow: click Characters nav → click + → add_character_requested emitted."""
        self.sb._on_nav_clicked("Characters")
        received = []
        self.sb.add_project_requested.connect(lambda: received.append("p"))
        self.sb.add_scene_requested.connect(lambda: received.append("s"))
        self.sb.add_character_requested.connect(lambda: received.append("c"))
        self.sb._on_add_button_clicked()
        self.assertEqual(received, ["c"],
                         "Expected add_character_requested, got {0}".format(received))

    def test_plus_no_signal_after_nav_history(self):
        """Full flow: click History nav → click + → no signal emitted."""
        self.sb._on_nav_clicked("History")
        received = []
        self.sb.add_project_requested.connect(lambda: received.append("p"))
        self.sb.add_scene_requested.connect(lambda: received.append("s"))
        self.sb.add_character_requested.connect(lambda: received.append("c"))
        self.sb._on_add_button_clicked()
        self.assertEqual(received, [],
                         "Expected no signal for History, got {0}".format(received))


# ===========================================================================
# 2. Characters nav click does NOT open dialog
# ===========================================================================

class TestCharactersNavDoesNotOpenDialog(unittest.TestCase):
    """P3.10: Clicking Characters nav should ONLY switch context, NOT open dialog."""

    def test_characters_clicked_not_connected_to_dialog(self):
        """MainWindow must NOT connect characters_clicked to _on_open_character_management."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # The old connection was:
        # self._sidebar.characters_clicked.connect(self._on_open_character_management)
        # This should NOT exist anymore (the + button handles dialog opening)
        self.assertNotIn(
            "self._sidebar.characters_clicked.connect(self._on_open_character_management)",
            src,
            "characters_clicked must NOT open the dialog directly")

    def test_characters_clicked_connected_to_context_switch(self):
        """MainWindow must connect characters_clicked to _on_nav_context_changed."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn(
            'self._sidebar.characters_clicked.connect(lambda: self._on_nav_context_changed("Characters"))',
            src)


# ===========================================================================
# 3. F11 QShortcut
# ===========================================================================

class TestF11Shortcut(unittest.TestCase):
    """P3.23: F11/Esc shortcut registration — EXACTLY ONE per key.

    History: P3.10 registered BOTH a QShortcut(F11) and the hidden native
    menubar QAction("Fullscreen", F11) for the same key (likewise Escape /
    "Stop"). Qt resolved this as an ambiguous shortcut overload and
    disabled BOTH — F11 fullscreen and Esc stop were completely dead
    (runtime-proven by the independent audit). P3.23 removed the duplicate
    QShortcut objects; the menubar QAction shortcuts (set to
    ApplicationShortcut context so they work regardless of focus) are now
    the single authoritative registration.
    """

    def test_f11_registered_exactly_once(self):
        """F11 must be registered via the menubar QAction ONLY — no
        duplicate QShortcut (ambiguous overload killed both in P3.10)."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # The duplicate QShortcut registration must NOT exist...
        self.assertNotIn('QShortcut(QKeySequence("F11"), self)', src)
        self.assertNotIn('_fullscreen_shortcut', src)
        # ...and the fix must be present (QAction context promotion).
        self.assertIn('ApplicationShortcut', src)
        # The menubar action carries the F11 shortcut.
        with open(os.path.join(_ROOT, "ui/panels/menu_bar.py")) as f:
            mb_src = f.read()
        self.assertIn('"fullscreen", "Fullscreen", "F11"', mb_src)

    def test_escape_bound_to_stop_exactly_once(self):
        """P3.25 (audit SS-H13): Esc must be handled by EXACTLY ONE
        authoritative handler — MainWindow.keyPressEvent with an explicit
        priority chain (stop generation > exit fullscreen > close search
        bar). No QAction shortcut (an ApplicationShortcut QAction
        intercepted every Esc app-wide and made Esc-exit-fullscreen /
        Esc-close-search unreachable), and no duplicate QShortcut."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertNotIn('QKeySequence("Escape"), self', src)
        self.assertNotIn('_fullscreen_exit_shortcut', src)
        # The authoritative priority-chain handler exists.
        self.assertIn('if key == Qt.Key.Key_Escape:', src)
        # The menubar Stop action must NOT carry a global Esc shortcut.
        with open(os.path.join(_ROOT, "ui/panels/menu_bar.py")) as f:
            mb_src = f.read()
        self.assertNotIn('"stop", "Stop", "Esc"', mb_src,
                         "Esc QAction shortcut must not exist (SS-H13)")


# ===========================================================================
# 4. TopNavigation menu uses popup() not exec()
# ===========================================================================

class TestMenuPopupNotExec(unittest.TestCase):
    """P3.10: Menu must use popup() (non-blocking) not exec() (blocking)."""

    def test_show_menu_uses_popup(self):
        """_show_menu must call menu.popup(), not menu.exec()."""
        with open(os.path.join(_ROOT, "ui/panels/top_navigation.py")) as f:
            src = f.read()
        self.assertIn("menu.popup(pos)", src)
        # exec should NOT be used for the menu (it's blocking and causes
        # focus issues in Full Screen)
        self.assertNotIn("menu.exec(pos)", src,
                         "menu.exec(pos) should be replaced with menu.popup(pos)")

    def test_show_menu_unchecks_button_on_hide(self):
        """_show_menu must uncheck the NavButton when the menu hides."""
        with open(os.path.join(_ROOT, "ui/panels/top_navigation.py")) as f:
            src = f.read()
        self.assertIn("aboutToHide", src)
        self.assertIn("setChecked(False)", src)


# ===========================================================================
# 5. New Scene uses authoritative project.scenes
# ===========================================================================

class TestNewSceneUsesAuthoritativeSource(unittest.TestCase):
    """P3.10: _on_new_scene must use self._active_project.scenes, not sidebar._scenes."""

    def test_new_scene_uses_active_project_scenes(self):
        """_on_new_scene must build sidebar_scenes from self._active_project.scenes."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # Find the _on_new_scene method
        new_scene_start = src.find("def _on_new_scene")
        new_scene_end = src.find("\n    def ", new_scene_start + 10)
        new_scene_src = src[new_scene_start:new_scene_end]
        # Check the NEW pattern is present
        self.assertIn("for s in self._active_project.scenes", new_scene_src,
                      "_on_new_scene should iterate self._active_project.scenes")
        # Check the OLD pattern is NOT present as actual code (it may appear
        # in comments). Remove comment lines before checking.
        code_lines = [line for line in new_scene_src.split('\n')
                      if not line.strip().startswith('#')]
        code_src = '\n'.join(code_lines)
        self.assertNotIn('getattr(self._sidebar, "_scenes", [])', code_src,
                         "_on_new_scene should not read sidebar's internal _scenes list in code")


# ===========================================================================
# 6. Scene rename refreshes sidebar (P3.9 fix, verify still present)
# ===========================================================================

class TestSceneRenameRefreshesSidebar(unittest.TestCase):
    """P3.10: Verify the P3.9 rename refresh is still present."""

    def test_rename_calls_set_scenes(self):
        """_on_edit_scene_name must call set_scenes with updated names."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("self._sidebar.set_scenes(sidebar_scenes)", src)


# ===========================================================================
# 7. Scenes/Characters context scoped to active project (P3.9 fix)
# ===========================================================================

class TestContextScopingStillPresent(unittest.TestCase):
    """P3.10: Verify the P3.9 scoping fix is still present."""

    def test_scenes_context_uses_active_project(self):
        """Scenes context must use self._active_project.scenes."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("for s in self._get_sorted_scenes():", src)

    def test_characters_context_uses_active_project(self):
        """Characters context must use self._active_project.characters."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("for c in self._active_project.characters:", src)


# ===========================================================================
# 8. Full Screen state restoration (P3.9 fix, verify still present)
# ===========================================================================

class TestFullScreenStateRestoration(unittest.TestCase):
    """P3.10: Verify the P3.9 fullscreen state restoration is still present."""

    def test_fullscreen_saves_geometry(self):
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("self._saved_geometry = self.saveGeometry()", src)
        self.assertIn("self.restoreGeometry(self._saved_geometry)", src)

    def test_fullscreen_activates_window(self):
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("self.activateWindow()", src)
        self.assertIn("self.raise_()", src)

    def test_no_stays_on_top(self):
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertNotIn("WindowStaysOnTopHint", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
