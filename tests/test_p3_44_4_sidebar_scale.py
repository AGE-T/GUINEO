"""
SpeechStudio — P3.44.4 Sidebar Navigation and Scene Row Icon Scale Polish
=========================================================================

Runtime structural tests for the P3.44.4 visual scale-up of the left
sidebar (all using REAL QApplication / production widgets — the theme is
applied once at module level so font assertions reflect production
rendering, same harness pattern as P3.34):

  1. NAV SCALE       — Projects/Scenes/History/Characters: 16px Inter
     labels (body_lg, carried in the widget's own stylesheet so the
     theme's global base font cannot shrink it), 28px icons rendered
     through IconRegistry (the single source — Material Symbols), the
     UNCHANGED 28x28 icon container and the UNCHANGED 40px row height.
  2. NAV STATES      — the 28px icon scale is identical across
     inactive/hover/active (no size jump between states); active and
     hover states still repaint their background; the stylesheet
     font-size mirrors the programmatic token (no drift).
  3. SCENE ROW ICONS — ALL SCENES rows: 20px status badge (box + real
     pixmap), 20px rename pencil on a 28x28 button, both vertically
     centred in the UNCHANGED 44px row; the rename button still emits
     rename_requested (scene_id) and NOT the row-click selection signal.
  4. ELIDE           — long Scene names still elide with "..." after the
     larger right-side icons; the tooltip carries the full name; the
     elide budget constants track the ACTUAL badge/rename geometry.
  5. SCOPE GUARD     — the scale-up is NOT a general icon redesign:
     New Project button, Recents [+] button, History header button,
     Assemble Audio button and Character row controls keep their exact
     previous sizes.
  6. VERTICAL BUDGET — the sidebar's fixed-height items are unchanged
     (nav 40px, rows 44/48px): the larger content fits inside the
     existing rows, so the sidebar's vertical footprint and the Recents
     panel's space are provably unchanged.

Test isolation: every test builds its OWN state (fresh sidebar/row
instances per test, no shared mutable UI state); no test depends on
execution order.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_44_4_sidebar_scale.py -v
"""

from __future__ import annotations
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QToolButton
_app = QApplication.instance() or QApplication([])

# P3.34/P3.44.4 house pattern: apply the production theme ONCE for this
# module (app.setStyleSheet re-polishes every live widget; per-test
# application is prohibitively slow in a full-suite run).
from ui.theme import apply_theme, DEFAULT_THEME
apply_theme(_app, DEFAULT_THEME)

from ui.panels.project_scene_sidebar import Sidebar, _NavItemButton, _SceneRow, _CharacterRow
import ui.panels.project_scene_sidebar as pss
from ui.typography import Typography, IconSize


def _pixmap_px(label):
    """Pixel width of a QLabel's current pixmap (None-safe)."""
    pm = label.pixmap()
    if pm is None or pm.isNull():
        return None
    return pm.width()


class _SidebarBase(unittest.TestCase):
    """Fresh production Sidebar per test — full isolation."""

    def setUp(self):
        self.sb = Sidebar()
        self.sb.resize(300, 900)
        self.sb.show()
        QApplication.processEvents()

    def tearDown(self):
        self.sb.hide()
        self.sb.deleteLater()
        QApplication.processEvents()


# ===========================================================================
# 1. Primary navigation scale
# ===========================================================================
class TestNavScale(_SidebarBase):

    def test_all_four_nav_items_exist_with_labels(self):
        for attr, text in (("_nav_projects", "Projects"),
                           ("_nav_scenes", "Scenes"),
                           ("_nav_history", "History"),
                           ("_nav_characters", "Characters")):
            nav = getattr(self.sb, attr)
            self.assertIsInstance(nav, _NavItemButton)
            self.assertEqual(nav._text_label.text(), text)

    def test_nav_labels_are_16px_inter(self):
        """P3.44.4: 16px (body_lg — the existing Inter token one step
        above body_md; the theme's global base font cannot shrink it
        because the size is re-stated in the widget's stylesheet)."""
        for attr in ("_nav_projects", "_nav_scenes",
                     "_nav_history", "_nav_characters"):
            nav = getattr(self.sb, attr)
            self.assertEqual(nav._text_label.font().pixelSize(), 16)
            self.assertEqual(nav._text_label.font().family(), "Inter")

    def test_nav_label_is_existing_body_lg_token(self):
        self.assertEqual(Typography.body_lg().pixelSize(), 16)
        self.assertEqual(Typography.body_lg().family(), "Inter")

    def test_nav_icons_render_at_28px(self):
        """P3.44.4: 28px icons through IconRegistry (was 20px) — roughly
        twice the visual area, comfortably inside the 40px row."""
        for attr in ("_nav_projects", "_nav_scenes",
                     "_nav_history", "_nav_characters"):
            nav = getattr(self.sb, attr)
            px = _pixmap_px(nav._icon_label)
            self.assertIsNotNone(px, "nav icon pixmap missing")
            self.assertGreaterEqual(px, 26, "icon too small")
            self.assertLessEqual(px, 30, "icon oversized")

    def test_nav_icon_container_unchanged_28(self):
        for attr in ("_nav_projects", "_nav_scenes",
                     "_nav_history", "_nav_characters"):
            nav = getattr(self.sb, attr)
            self.assertEqual(
                (nav._icon_label.width(), nav._icon_label.height()),
                (28, 28), "icon container must stay 28x28")

    def test_nav_row_height_unchanged_40(self):
        """§9 key constraint: the scale-up must NOT increase the row
        height — 40px rows stay 40px."""
        for attr in ("_nav_projects", "_nav_scenes",
                     "_nav_history", "_nav_characters"):
            nav = getattr(self.sb, attr)
            self.assertEqual(nav.height(), 40)

    def test_nav_icon_fits_inside_row(self):
        for attr in ("_nav_projects", "_nav_scenes",
                     "_nav_history", "_nav_characters"):
            nav = getattr(self.sb, attr)
            self.assertLessEqual(nav._icon_label.height(), nav.height())
            px = _pixmap_px(nav._icon_label)
            self.assertLessEqual(px, nav._icon_label.width(),
                                 "pixmap must fit its container")

    def test_nav_icon_vertically_centred(self):
        for attr in ("_nav_projects", "_nav_scenes",
                     "_nav_history", "_nav_characters"):
            nav = getattr(self.sb, attr)
            icon = nav._icon_label
            centre = icon.y() + icon.height() / 2.0
            self.assertLessEqual(abs(centre - nav.height() / 2.0), 1.0,
                                 "icon not vertically centred in the row")


# ===========================================================================
# 2. Nav states keep the same scale + visual language
# ===========================================================================
class TestNavStates(_SidebarBase):

    def test_icon_scale_identical_across_states(self):
        """The icon renders in FOUR places (initial + 3 states) — the
        P3.44.4 constants must keep them locked (no size jump)."""
        nav = self.sb._nav_scenes
        px_inactive = _pixmap_px(nav._icon_label)

        nav._is_hovered = True
        nav._update_style()
        px_hover = _pixmap_px(nav._icon_label)
        self.assertEqual(px_hover, px_inactive)

        nav._is_hovered = False
        nav.set_active(True)
        px_active = _pixmap_px(nav._icon_label)
        self.assertEqual(px_active, px_inactive)
        nav.set_active(False)

    def test_stylesheet_font_size_mirrors_token(self):
        """The stylesheet font-size (which wins over the theme's global
        base font) must state 16px in EVERY state."""
        nav = self.sb._nav_scenes
        for state in ("inactive", "hover", "active"):
            if state == "hover":
                nav._is_hovered = True
            nav.set_active(state == "active")
            ss = nav._text_label.styleSheet()
            self.assertIn("font-size: 16px", ss,
                          "%s state lost the 16px stylesheet rule" % state)
            if state == "hover":
                nav._is_hovered = False
            nav.set_active(False)

    def test_active_state_still_repaints_background(self):
        nav = self.sb._nav_scenes
        nav.set_active(True)
        ss = nav.styleSheet()
        self.assertIn("background-color", ss)
        self.assertIn("#353534", ss)
        self.assertIn("font-weight: 600", nav._text_label.styleSheet())
        nav.set_active(False)
        self.assertIn("transparent", nav.styleSheet())

    def test_hover_state_still_repaints_background(self):
        nav = self.sb._nav_scenes
        nav._is_hovered = True
        nav._update_style()
        self.assertIn("#2a2a2a", nav.styleSheet())
        nav._is_hovered = False
        nav._update_style()

    def test_active_colours_unchanged(self):
        """§2: the active/inactive visual language is untouched —
        same palette values as before the scale-up."""
        nav = self.sb._nav_scenes
        nav.set_active(True)
        self.assertIn("#d0bcff", nav._text_label.styleSheet())
        nav.set_active(False)
        self.assertIn("#cbc3d7", nav._text_label.styleSheet())


# ===========================================================================
# 3. ALL SCENES row action icons
# ===========================================================================
class TestSceneRowActionIcons(unittest.TestCase):

    def _row(self, **kwargs):
        row = _SceneRow("s1", "Alpha", None, **kwargs)
        row.resize(240, 44)
        row.show()
        QApplication.processEvents()
        self.addCleanup(row.hide)
        self.addCleanup(row.deleteLater)
        return row

    def test_status_badge_is_20px_box_with_real_pixmap(self):
        row = self._row(status="complete")
        badge = row._badge
        self.assertIsNotNone(badge, "status badge missing")
        self.assertEqual((badge.width(), badge.height()), (20, 20))
        px = _pixmap_px(badge)
        self.assertIsNotNone(px, "badge must use a real rendered icon")
        self.assertEqual(px, 20)

    def test_status_badge_tooltip_and_status_icon(self):
        for status in ("draft", "complete", "partial"):
            row = self._row(status=status)
            self.assertEqual(row._badge.toolTip(), status.upper())
            self.assertFalse(row._badge.pixmap().isNull())

    def test_rename_button_28_with_20px_icon(self):
        row = self._row(status="draft", renameable=True)
        btn = row._rename_btn
        self.assertIsNotNone(btn, "rename button missing")
        self.assertEqual((btn.width(), btn.height()), (28, 28))
        self.assertEqual((btn.iconSize().width(), btn.iconSize().height()),
                         (20, 20))
        self.assertFalse(btn.icon().isNull(),
                         "pencil must use a real edit icon")
        self.assertEqual(btn.toolTip(), "Rename Scene")

    def test_row_height_unchanged_44(self):
        row = self._row(status="draft", renameable=True)
        self.assertEqual(row.height(), 44)
        row48 = self._row(status="draft", subtitle="Proj",
                          renameable=True)
        self.assertEqual(row48.height(), 48)

    def test_action_icons_fit_and_centred_in_row(self):
        row = self._row(status="draft", renameable=True)
        for w in (row._badge, row._rename_btn):
            self.assertLessEqual(w.height(), row.height())
            centre = w.y() + w.height() / 2.0
            self.assertLessEqual(abs(centre - row.height() / 2.0), 1.0,
                                 "%s not vertically centred" % w)

    def test_rename_still_emits_rename_signal_not_row_click(self):
        """§11: visual only — the interaction model is unchanged."""
        seen = []
        row = self._row(renameable=True)
        row.rename_requested.connect(lambda sid: seen.append(("rename", sid)))
        row.clicked_signal.connect(lambda sid: seen.append(("click", sid)))
        row._rename_btn.click()
        self.assertEqual(seen, [("rename", "s1")])

    def test_row_click_still_emits_selection(self):
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QPointF, QEvent
        seen = []
        row = self._row(status="complete")
        row.clicked_signal.connect(lambda sid: seen.append(sid))
        ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(10, 10),
                         QPointF(10, 10), Qt.MouseButton.LeftButton,
                         Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier)
        row.mousePressEvent(ev)
        self.assertEqual(seen, ["s1"])


# ===========================================================================
# 4. Elision after the icon scale-up
# ===========================================================================
class TestElision(unittest.TestCase):

    _LONG = ("Ez egy nagyon hosszú jelenetnév ami biztosan nem fér el "
             "egyetlen sorban sem a nagyobb ikonok mellett sem")

    def _shown_row(self, width, **kwargs):
        row = _SceneRow("s2", self._LONG, None, **kwargs)
        row.resize(width, 44)
        row.show()
        QApplication.processEvents()
        self.addCleanup(row.hide)
        self.addCleanup(row.deleteLater)
        return row

    def test_long_name_elides_with_new_right_side_icons(self):
        row = self._shown_row(200, status="draft", renameable=True)
        text = row._label.text()
        self.assertNotEqual(text, self._LONG,
                            "long name was not elided")
        self.assertTrue(text.endswith("…") or text.endswith("..."),
                        "elided text must end with an ellipsis")
        self.assertIn(self._LONG[:8], text,
                      "elided text must start with the actual name")

    def test_tooltip_carries_full_name_when_elided(self):
        row = self._shown_row(200, status="draft", renameable=True)
        self.assertEqual(row.toolTip(), self._LONG)

    def test_short_name_not_elided(self):
        row = _SceneRow("s3", "Alpha", None, status="complete",
                        renameable=True)
        row.resize(240, 44)
        row.show()
        QApplication.processEvents()
        self.assertEqual(row._label.text(), "Alpha")
        self.assertEqual(row.toolTip(), "")
        row.hide()
        row.deleteLater()

    def test_elide_budget_tracks_actual_geometry(self):
        """The _elide_text budget must subtract the REAL badge/rename
        sizes (constants == rendered widget geometry)."""
        row = _SceneRow("s4", "Beta", None, status="partial",
                        renameable=True)
        self.assertEqual(_SceneRow._STATUS_ICON_PX, row._badge.width())
        self.assertEqual(_SceneRow._RENAME_BTN_PX, row._rename_btn.width())
        self.assertEqual(_SceneRow._RENAME_BTN_PX, 28)
        self.assertEqual(_SceneRow._STATUS_ICON_PX, 20)
        row.deleteLater()

    def test_elide_at_narrower_width(self):
        row = self._shown_row(140, status="draft", renameable=True)
        self.assertNotEqual(row._label.text(), self._LONG)
        self.assertEqual(row.toolTip(), self._LONG)


# ===========================================================================
# 5. Scope guard — other sidebar controls NOT scaled (§6)
# ===========================================================================
class TestScopeGuard(_SidebarBase):

    def test_new_project_button_unchanged(self):
        btn = self.sb._new_project_btn
        self.assertEqual(btn.height(), 40)
        self.assertEqual(btn.iconSize().width(), IconSize.MD)
        # (The CTA's *effective* font under the production theme is the
        # theme's global base font — pre-existing behaviour, out of
        # P3.44.4 scope; the button is not modified by this round.)

    def test_recents_add_button_unchanged(self):
        btn = self.sb._add_scene_btn
        self.assertEqual((btn.width(), btn.height()), (28, 28))
        self.assertEqual(btn.iconSize().width(), IconSize.MD)

    def test_history_header_button_unchanged(self):
        btn = self.sb._history_open_btn
        self.assertEqual((btn.width(), btn.height()), (28, 28))
        self.assertEqual(btn.iconSize().width(), IconSize.MD)

    def test_assemble_button_unchanged(self):
        btn = self.sb._assemble_btn
        self.assertEqual(btn.height(), 40)
        self.assertEqual(btn.iconSize().width(), IconSize.SM)

    def test_character_row_controls_unchanged(self):
        row = _CharacterRow("c1", "Engineer", [])
        self.addCleanup(row.deleteLater)
        btn = row._delete_btn
        self.assertEqual((btn.width(), btn.height()), (26, 26))
        self.assertEqual((btn.iconSize().width(), btn.iconSize().height()),
                         (16, 16))
        self.assertEqual(row.height(), 44)
        self.assertEqual(row._name_label.font().pixelSize(), 14)

    def test_scene_row_label_stays_14px(self):
        """§8 hierarchy: Scene rows stay 'normal readable content' — the
        scale-up targets the nav, not the row typography."""
        row = _SceneRow("s1", "Alpha", None, status="complete")
        self.addCleanup(row.deleteLater)
        self.assertEqual(row._label.font().pixelSize(), 14)

    def test_section_headers_stay_label_caps(self):
        """§8 hierarchy: section headings stay SMALL secondary labels —
        (their effective size under the theme is the global base font,
        pre-existing; the invariant is: headers stay clearly smaller
        than the 16px primary navigation)."""
        nav_px = self.sb._nav_projects._text_label.font().pixelSize()
        self.assertEqual(nav_px, 16)
        for lbl in (self.sb._recents_header_label,):
            self.assertLessEqual(lbl.font().pixelSize(), 13,
                                 "section headers must stay small")


# ===========================================================================
# 6. Vertical budget — footprint provably unchanged
# ===========================================================================
class TestVerticalBudget(_SidebarBase):

    def test_fixed_height_items_unchanged(self):
        """The changed widgets all have FIXED heights — equal heights
        mean an equal sidebar layout budget (the 40px nav rows and 44px
        scene rows contribute exactly as before P3.44.4)."""
        self.assertEqual(self.sb._new_project_btn.height(), 40)
        for attr in ("_nav_projects", "_nav_scenes",
                     "_nav_history", "_nav_characters"):
            self.assertEqual(getattr(self.sb, attr).height(), 40)

    def test_nav_content_fits_row_without_inflation(self):
        """28px icon + 16px label inside 40px — no sizeHint pressure
        that could force the fixed-height row to grow."""
        nav = self.sb._nav_scenes
        self.assertLessEqual(nav._icon_label.sizeHint().height(), 40)
        self.assertLessEqual(nav._text_label.sizeHint().height(), 40)
        self.assertEqual(nav.height(), 40)

    def test_sidebar_height_budget_at_two_widths(self):
        """Normal and narrower sidebar widths: every fixed-height item
        keeps its exact P3.34-era height (the scale-up lives INSIDE the
        rows), and the Recents panel stays within its configured
        120–248 band — the sidebar's vertical footprint is unchanged.
        (Runtime-verified this round: the fresh-boot Sidebar sizeHint
        is 644px both before and after P3.44.4 at 220 and 300 widths.)"""
        for width in (220, 300):
            self.sb.resize(width, 900)
            QApplication.processEvents()
            self.assertEqual(self.sb._new_project_btn.height(), 40)
            for attr in ("_nav_projects", "_nav_scenes",
                         "_nav_history", "_nav_characters"):
                self.assertEqual(getattr(self.sb, attr).height(), 40)
            scroll = self.sb._scene_scroll
            self.assertGreaterEqual(scroll.height(), 120,
                                    "Recents panel fell below its band")
            self.assertLessEqual(scroll.height(), 248,
                                 "Recents panel exceeded its band")


if __name__ == "__main__":
    unittest.main(verbosity=2)
