"""
SpeechStudio — P3.11 Sidebar Visual Cleanup Tests
==================================================

Tests for the visual cleanup:
  1. No internal Scene ID (UUID) displayed in Recents rows
  2. No internal Scene ID (UUID) displayed in Context List rows
  3. Active Scene has exactly one active indicator (no double border)
  4. Inactive Scene has no active indicator
  5. Status indicator is present (16px icon)
  6. Status indicator changes according to Scene.status
  7. Long Scene names do not clip the status indicator
  8. Scene identity still uses project_id + scene_id internally

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_11_visual_cleanup.py -v
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
# 1-2. No internal Scene ID (UUID) displayed
# ===========================================================================

class TestNoUUIDDisplayed(unittest.TestCase):
    """P3.11: Internal Scene IDs (UUIDs) must NOT be displayed in rows."""

    def test_recents_scenes_subtitle_is_project_name_not_id(self):
        """MainWindow._update_recents for Scenes must pass project NAME as subtitle, not project_id."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # The OLD code: "subtitle": r.project_id
        # The NEW code: resolves project name via get_project()
        self.assertNotIn('"subtitle": r.project_id', src,
                         "Must not pass project_id as subtitle (it's a UUID)")

    def test_recents_characters_subtitle_is_project_name_not_id(self):
        """MainWindow._update_recents for Characters must pass project NAME, not project_id."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # Check that the Characters context also resolves project name
        # (not passing r.project_id directly as subtitle)
        chars_section = src[src.find('elif context == "Characters":'):]
        chars_section = chars_section[:chars_section.find('elif context ==')]
        self.assertNotIn('"subtitle": r.project_id', chars_section,
                         "Characters must not pass project_id as subtitle")

    def test_scene_row_does_not_display_scene_id(self):
        """_SceneRow must NOT display the scene_id in any visible label."""
        from ui.panels.project_scene_sidebar import _SceneRow
        # Create a row with a UUID-like scene_id
        row = _SceneRow("9e51969d1234abcd", "Scene 01", status="draft")
        # The label text should be "Scene 01", not the UUID
        self.assertEqual(row._label.text(), "Scene 01")
        self.assertNotIn("9e51969d", row._label.text())

    def test_scene_row_with_subtitle_does_not_display_id(self):
        """_SceneRow with subtitle must show subtitle, not project_id."""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("abc123def456", "Scene 01", subtitle="Eden", status="draft")
        # The subtitle label should show "Eden", not the UUID
        self.assertIsNotNone(row._sub_label)
        self.assertEqual(row._sub_label.text(), "Eden")
        self.assertNotIn("abc123", row._sub_label.text())


# ===========================================================================
# 3-4. Active indicator (single, not double)
# ===========================================================================

class TestActiveIndicator(unittest.TestCase):
    """P3.11: Active Scene must have exactly ONE active indicator (no double border)."""

    def test_active_style_has_single_left_border(self):
        """_SceneRow active style must have exactly one border-left, not double."""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("s1", "Scene 01", status="draft")
        row.set_active(True)
        # The stylesheet should have border-left: 2px solid (one indicator)
        style = row.styleSheet()
        self.assertIn("border-left: 2px solid #d0bcff", style)
        # Should NOT have a second border-left (double border bug)
        self.assertEqual(style.count("border-left"), 1,
                         "Should have exactly one border-left declaration")

    def test_inactive_style_has_no_active_indicator(self):
        """Inactive _SceneRow must have transparent border-left (no active indicator)."""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("s1", "Scene 01", status="draft")
        row.set_active(False)
        style = row.styleSheet()
        self.assertIn("border-left: 2px solid transparent", style)

    def test_no_right_border(self):
        """_SceneRow must NOT have a right border (removes extra right edge line)."""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("s1", "Scene 01", status="draft")
        row.set_active(True)
        style = row.styleSheet()
        self.assertIn("border-right: none", style)

    def test_active_has_one_border_only(self):
        """Active row stylesheet should declare border-right: none, border-top: none, border-bottom: none."""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("s1", "Scene 01", status="draft")
        row.set_active(True)
        style = row.styleSheet()
        self.assertIn("border-right: none", style)
        self.assertIn("border-top: none", style)
        self.assertIn("border-bottom: none", style)


# ===========================================================================
# 5-6. Status indicator
# ===========================================================================

class TestStatusIndicator(unittest.TestCase):
    """P3.11: Status indicator must be present, larger (16px), and use icons."""

    def test_status_indicator_present(self):
        """_SceneRow with status must have a badge widget."""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("s1", "Scene 01", status="draft")
        self.assertIsNotNone(row._badge, "Badge should be present when status is set")

    def test_status_indicator_16px(self):
        """Status indicator must be a real icon badge, visibly larger
        than the old 10px dot. (P3.44.4: raised 16px → 20px — the
        user-requested scale-up for ALL SCENES row action icons; the
        P3.11 intent "a clear Material icon, not a dot" is preserved.)"""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("s1", "Scene 01", status="draft")
        self.assertEqual(row._badge.width(), 20)
        self.assertEqual(row._badge.height(), 20)

    def test_status_indicator_changes_with_status(self):
        """Status indicator must change according to Scene.status."""
        from ui.panels.project_scene_sidebar import _SceneRow
        statuses = ["draft", "ready", "generating", "complete", "error"]
        for status in statuses:
            row = _SceneRow("s1", "Scene 01", status=status)
            self.assertIsNotNone(row._badge, "Badge should exist for status: {0}".format(status))
            self.assertEqual(row._badge.toolTip(), status.upper())

    def test_status_uses_icon_mapping(self):
        """_SceneRow must use _STATUS_ICONS mapping with icon names."""
        from ui.panels.project_scene_sidebar import _SceneRow
        self.assertTrue(hasattr(_SceneRow, '_STATUS_ICONS'))
        icons = _SceneRow._STATUS_ICONS
        self.assertIn("draft", icons)
        self.assertIn("ready", icons)
        self.assertIn("generating", icons)
        self.assertIn("complete", icons)
        self.assertIn("error", icons)
        # Each entry should be (icon_name, color)
        for status, (icon_name, color) in icons.items():
            self.assertIsInstance(icon_name, str)
            self.assertTrue(icon_name)  # non-empty
            self.assertIsInstance(color, str)
            self.assertTrue(color.startswith("#"))

    def test_no_status_no_badge(self):
        """_SceneRow without status should have no badge."""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("s1", "Scene 01", status="")
        self.assertIsNone(row._badge)

    def test_badge_has_no_border(self):
        """Badge must NOT have a border (prevents extra right edge line)."""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("s1", "Scene 01", status="draft")
        badge_style = row._badge.styleSheet()
        self.assertIn("border: none", badge_style,
                      "Badge must have border: none to prevent right edge line")


# ===========================================================================
# 7. Long names don't clip status indicator
# ===========================================================================

class TestLongNamesNoClip(unittest.TestCase):
    """P3.11: Long Scene names must not clip the status indicator."""

    def test_elide_text_accounts_for_badge(self):
        """_elide_text must subtract badge width from available text width."""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("s1", "Very Long Scene Name That Exceeds Available Width", status="draft")
        row.resize(200, 32)  # narrow width to force eliding
        # Manually trigger elide (offscreen mode may not fire resizeEvent)
        row._elide_text()
        # The label text should be elided (shorter than full name)
        full_name = "Very Long Scene Name That Exceeds Available Width"
        self.assertTrue(row._label.text().endswith("…") or
                        len(row._label.text()) < len(full_name),
                        "Text should be elided: {0}".format(row._label.text()))
        # Badge should still exist (not clipped)
        self.assertIsNotNone(row._badge)

    def test_badge_remains_right_aligned(self):
        """Badge layout should place it after the label (right-aligned via stretch)."""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("s1", "Short", status="draft")
        # In offscreen mode, geometry may not be computed. Instead verify
        # the layout structure: label has stretch=1, badge has stretch=0.
        # This means the label takes all available space and badge stays right.
        layout = row.layout()
        # Find the label and badge items
        label_item = None
        badge_item = None
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.widget() is row._label:
                label_item = item
            elif item.widget() is row._badge:
                badge_item = item
        self.assertIsNotNone(label_item)
        self.assertIsNotNone(badge_item)
        # Label should have stretch factor 1 (takes available space)
        # Badge should have stretch factor 0 (stays at natural size, right-aligned)
        self.assertEqual(layout.stretch(layout.indexOf(label_item)), 1)
        self.assertEqual(layout.stretch(layout.indexOf(badge_item)), 0)


# ===========================================================================
# 8. Scene identity still uses project_id + scene_id internally
# ===========================================================================

class TestSceneIdentityPreserved(unittest.TestCase):
    """P3.11: Scene identity must still use project_id + scene_id internally."""

    def test_scene_row_stores_scene_id(self):
        """_SceneRow must store the scene_id internally (for clicks)."""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("scene_abc_123", "Scene 01", status="draft")
        self.assertEqual(row._scene_id, "scene_abc_123")

    def test_scene_row_click_emits_scene_id(self):
        """Clicking a _SceneRow must emit the internal scene_id."""
        from ui.panels.project_scene_sidebar import _SceneRow
        row = _SceneRow("scene_xyz_789", "Scene 01", status="draft")
        received = []
        row.clicked_signal.connect(lambda sid: received.append(sid))
        # Simulate a click by calling mousePressEvent directly
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QEvent
        press_event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(10, 10),
                                   QPointF(10, 10), Qt.MouseButton.LeftButton,
                                   Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        row.mousePressEvent(press_event)
        self.assertEqual(received, ["scene_xyz_789"])

    def test_recents_manager_uses_project_id(self):
        """RecentsManager must still use project_id + entity_id for identity."""
        from engine.recents_manager import RecentsManager, RecentEntry
        sm = None
        rm = RecentsManager(sm)
        rm.add_recent_scene("proj_123", "scene_456", "Scene 01")
        scenes = rm.get_recent_scenes()
        self.assertEqual(len(scenes), 1)
        self.assertEqual(scenes[0].project_id, "proj_123")
        self.assertEqual(scenes[0].entity_id, "scene_456")


if __name__ == "__main__":
    unittest.main(verbosity=2)
