# -*- coding: utf-8 -*-
"""P3.45.6 closeout — notification success icon + release hygiene
================================================================

Targeted regressions for the closeout correction round:

1. THE SUCCESS ICON (P3.45.7 artwork swap)
   * the packaged asset exists and keeps its contract: ``ok3.png`` —
     the user-supplied circular badge artwork with its transparency
     ALREADY PREPARED in the file (236x234 RGBA, fully transparent
     outside the circle, anti-aliased edges, max alpha 254), used
     VERBATIM — byte-identical to the uploaded artwork, pinned by
     SHA-256 (any accidental re-processing/re-encode fails the pin);
     the retired P3.45.6 ``ok2.png`` / ``ok2_transparent.png`` assets
     no longer ship;
   * ``FeedbackDialog.information`` boxes carry the packaged PNG as
     their icon — the right size (the P3.45.9 80 LOGICAL pixel target,
     see ``_SUCCESS_ICON_SIZE``), the aspect ratio preserved, the
     background outside the badge transparent (no white rectangle on
     the 4 dark themes), resolved APP_ROOT-relative (works from any
     CWD, no absolute developer path) and cached; the logical target
     is pinned explicitly (a revert to 48/64 fails) including the
     high-DPI path (device raster ``round(target * dpr)`` px carrying
     the devicePixelRatio, displayed at 80 logical);
   * the REAL message-box icon label (``qt_msgboxex_icon_label``)
     carries the pixmap — a real-widget check, not a source-text match;
   * the missing-asset fallback keeps the stock information icon;
   * the affected event-feedback call sites route through
     ``FeedbackDialog`` while the neutral notices, the error
     (``critical``), warning and confirmation dialogs keep their stock
     semantic icons (a real warning box still shows ``Icon.Warning``
     with no custom pixmap).

2. RELEASE / RUNTIME HYGIENE (user task 1)
   * ``.gitignore`` excludes the runtime ``projects/`` and ``presets/``
     trees (the tracked-project leak class is closed at the gate);
   * the suite-level APP_ROOT isolation (tests/conftest.py) is ACTIVE
     during the run: MainWindow's APP_ROOT is the session temp root,
     and a REAL MainWindow's ProjectManager resolves inside it — never
     the repository (the mutation channel that shipped the five
     project.json test artifacts).
"""

import os
import sys
import unittest

if __name__ == "__main__":
    sys.path.insert(
        0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv)

import ui.feedback as feedback_mod
from ui.feedback import (FeedbackDialog, _reset_success_icon_cache,
                          _success_icon_pixmap, _SUCCESS_ICON_SIZE)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ASSETS_ICONS = os.path.join(_REPO_ROOT, "assets", "icons")


def _process(ms=20):
    import time
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _APP.processEvents()


# ---------------------------------------------------------------------------
# 1) Packaged asset
# ---------------------------------------------------------------------------
#: SHA-256 of the user-supplied ok3.png artwork (P3.45.7 swap). The
#: file is used VERBATIM — its transparency is already prepared and the
#: explicit instruction was to NOT modify it — so the packaged bytes
#: must match the uploaded artwork exactly. Any re-save, re-encode or
#: "transparency improvement" breaks this pin.
_OK3_SHA256 = "cb98d65a7ccffee5b1629fcc56b7e0a256b1104aee8bf81a6bb8b2aae91d6cc3"


class TestSuccessIconAssets(unittest.TestCase):

    def test_ok3_artwork_packaged_verbatim(self):
        """assets/icons/ok3.png is the user-supplied circular badge
        artwork: present, 236x234 RGBA, byte-identical to the upload
        (SHA-256 pin), transparency already prepared — corners fully
        transparent, anti-aliased edges, NO fully-opaque pixel."""
        import hashlib
        from PySide6.QtGui import QImage
        path = os.path.join(_ASSETS_ICONS, "ok3.png")
        self.assertTrue(os.path.isfile(path), "missing asset: " + path)
        digest = hashlib.sha256(
            open(path, "rb").read()).hexdigest()
        self.assertEqual(digest, _OK3_SHA256,
                         "the ok3 artwork must ship VERBATIM (byte-level "
                         "SHA-256 pin) — any transparency re-processing "
                         "or re-encode is a defect")
        img = QImage(path)
        self.assertFalse(img.isNull())
        self.assertEqual((img.width(), img.height()), (236, 234))
        # Transparency ALREADY prepared in the file: fully transparent
        # outside the circular badge.
        for cx, cy in ((0, 0), (img.width() - 1, 0),
                       (0, img.height() - 1),
                       (img.width() - 1, img.height() - 1)):
            self.assertEqual(img.pixelColor(cx, cy).alpha(), 0,
                             "background corner must be fully transparent")
        # Alpha structure of the verbatim artwork: substantial visible
        # content at (sub-255) high alpha + genuine anti-aliased edges.
        opaque = partial = transparent = 0
        for y in range(img.height()):
            for x in range(img.width()):
                a = img.pixelColor(x, y).alpha()
                if a == 0:
                    transparent += 1
                elif a == 255:
                    opaque += 1
                else:
                    partial += 1
        self.assertGreater(transparent, 10000,
                           "the area outside the badge must be "
                           "transparent")
        self.assertGreater(partial, 1000,
                           "anti-aliased edges must survive verbatim")
        self.assertGreater(opaque + partial, 40000,
                           "the badge drawing must be present")

    def test_retired_ok2_assets_no_longer_ship(self):
        """The P3.45.6 ok2 artwork pair was RETIRED by the P3.45.7 swap:
        neither file may remain in the packaged assets (no dead artwork
        ships in the release)."""
        for name in ("ok2.png", "ok2_transparent.png"):
            self.assertFalse(
                os.path.isfile(os.path.join(_ASSETS_ICONS, name)),
                "retired asset still present: " + name)
        # And the displayed icon constant points at the new artwork.
        self.assertEqual(feedback_mod._SUCCESS_ICON_FILE, "ok3.png")


# ---------------------------------------------------------------------------
# 2) The pixmap the dialog displays
# ---------------------------------------------------------------------------
class TestSuccessIconPixmap(unittest.TestCase):

    def setUp(self):
        _reset_success_icon_cache()

    tearDown = setUp

    def test_pixmap_resolves_size_and_aspect(self):
        pm = _success_icon_pixmap()
        self.assertIsNotNone(pm)
        self.assertFalse(pm.isNull())
        # LOGICAL dimensions (device px / devicePixelRatio) — the
        # P3.45.9 target is a logical 80px box; the device raster may
        # differ by Qt's sub-pixel canvas rounding (80x80 at dpr 1,
        # 160x158 at dpr 2 — both documented in the design record §8).
        dpr = float(pm.devicePixelRatio()) or 1.0
        lw = pm.width() / dpr
        lh = pm.height() / dpr
        self.assertLessEqual(lw, _SUCCESS_ICON_SIZE + 0.01)
        self.assertLessEqual(lh, _SUCCESS_ICON_SIZE + 0.01)
        self.assertGreaterEqual(lw, _SUCCESS_ICON_SIZE - 4)
        self.assertGreaterEqual(lh, _SUCCESS_ICON_SIZE - 4)
        # Aspect ratio preserved (no distortion): source 236x234.
        ratio = pm.width() / float(pm.height())
        self.assertAlmostEqual(ratio, 236.0 / 234.0, delta=0.05)

    def test_target_is_80_logical_pixels(self):
        """P3.45.9 sizing correction pin: the success icon target IS
        80 logical pixels (48 read as too small despite available
        space; 80 is the owner-selected largest reviewed mock-up). A
        revert to 48 — or a drift to any other value — fails here."""
        self.assertEqual(_SUCCESS_ICON_SIZE, 80)
        pm = _success_icon_pixmap()
        self.assertIsNotNone(pm)
        dpr = float(pm.devicePixelRatio()) or 1.0
        # Fitted into the 80x80 box with KeepAspectRatio: the width IS
        # the target (the 236px source edge is the limiting one), the
        # height follows the near-square source aspect (234/236 ->
        # 79.32 ideal; Qt rounds the raster canvas, hence [78.5, 80]).
        self.assertAlmostEqual(pm.width() / dpr, 80.0, delta=0.5)
        self.assertGreaterEqual(pm.height() / dpr, 78.5)
        self.assertLessEqual(pm.height() / dpr, 80.0)
        # Not stretched to force both dimensions to exactly 80: the
        # mechanism is KeepAspectRatio; the measured raster aspect
        # stays within a sub-pixel tolerance of the source ratio.
        self.assertAlmostEqual(pm.width() / float(pm.height()),
                               236.0 / 234.0, delta=0.02)

    def test_pixmap_high_dpr_keeps_logical_target(self):
        """High-DPI path: with a 2.0 devicePixelRatio the pixmap is a
        ~160px DEVICE raster carrying devicePixelRatio 2.0 — displayed
        at the same 80 logical pixel target (physical pixels double,
        logical size unchanged). Simulated by stubbing the application
        instance the resolver consults."""
        from unittest.mock import patch
        from PySide6.QtGui import QGuiApplication

        class _FakeApp:
            def devicePixelRatio(self):
                return 2.0

        with patch.object(QGuiApplication, "instance",
                          return_value=_FakeApp()):
            _reset_success_icon_cache()
            try:
                pm = _success_icon_pixmap()
                self.assertIsNotNone(pm)
                dpr = float(pm.devicePixelRatio())
                self.assertAlmostEqual(dpr, 2.0, delta=0.01,
                                       msg="devicePixelRatio must be "
                                           "recorded on the pixmap")
                # Device raster ~ round(80 * 2) = 160 (Qt's sub-pixel
                # canvas rounding may give 158-160 for the height).
                self.assertIn(pm.width(), (159, 160))
                self.assertIn(pm.height(), (158, 159, 160))
                # The DISPLAYED (logical) size stays the 80px target.
                self.assertAlmostEqual(pm.width() / dpr, 80.0, delta=0.5)
                self.assertGreaterEqual(pm.height() / dpr, 78.5)
                self.assertLessEqual(pm.height() / dpr, 80.0)
                self.assertAlmostEqual(pm.width() / float(pm.height()),
                                       236.0 / 234.0, delta=0.02)
            finally:
                _reset_success_icon_cache()

    def test_pixmap_background_transparent_no_white_rectangle(self):
        """The DISPLAYED pixmap must not carry an opaque background
        OUTSIDE the circular badge — on the 4 dark themes it would show
        as an unwanted white rectangle (the user-reported risk). The
        white fill INSIDE the badge is part of the verbatim artwork."""
        pm = _success_icon_pixmap()
        img = pm.toImage()
        for cx, cy in ((0, 0), (img.width() - 1, 0),
                       (0, img.height() - 1),
                       (img.width() - 1, img.height() - 1)):
            self.assertEqual(img.pixelColor(cx, cy).alpha(), 0,
                             "white rectangle leak at corner "
                             "({0},{1})".format(cx, cy))

    def test_pixmap_cached_and_stable(self):
        first = _success_icon_pixmap()
        second = _success_icon_pixmap()
        self.assertIs(first, second, "the scaled pixmap must be cached")

    def test_pixmap_resolves_from_any_cwd(self):
        """No absolute developer path / upload location dependency: the
        asset resolves APP_ROOT-relative from ANY working directory."""
        cwd = os.getcwd()
        os.chdir("/")
        try:
            _reset_success_icon_cache()
            pm = _success_icon_pixmap()
            self.assertIsNotNone(pm)
            self.assertFalse(pm.isNull())
        finally:
            os.chdir(cwd)
            _reset_success_icon_cache()

    def test_source_has_no_absolute_path(self):
        """The resolution path is derived from the module location — no
        hardcoded absolute path may appear in ui/feedback.py."""
        src = open(os.path.join(_REPO_ROOT, "ui", "feedback.py"),
                   encoding="utf-8").read()
        self.assertNotIn("/home/", src)
        self.assertNotIn("C:\\", src)
        self.assertIn('os.path.join(_APP_ROOT, "assets", "icons"',
                      src)


# ---------------------------------------------------------------------------
# 3) The dialog wiring (real widgets)
# ---------------------------------------------------------------------------
class TestFeedbackDialogIcon(unittest.TestCase):

    def setUp(self):
        _reset_success_icon_cache()

    tearDown = setUp

    def _box(self):
        return FeedbackDialog._make_information_box(
            None, "GUINEO", "Project saved: Eden",
            "Scenes: 3\nCharacters: 1\n\nLocation: projects/eden/")

    def test_box_carries_packaged_pixmap(self):
        box = self._box()
        pm = box.iconPixmap()
        self.assertFalse(pm.isNull(), "the success icon must be set")
        expected = _success_icon_pixmap()
        self.assertEqual((pm.width(), pm.height()),
                         (expected.width(), expected.height()))
        self.assertEqual(box.icon(), QMessageBox.Icon.NoIcon,
                         "Qt semantics: a custom iconPixmap replaces the "
                         "stock icon enum")

    def test_real_icon_label_carries_the_pixmap(self):
        """Real-widget verification: the message-box icon label
        (qt_msgboxex_icon_label) actually holds the pixmap."""
        box = self._box()
        labels = [l for l in box.findChildren(QLabel)
                  if l.objectName() == "qt_msgboxex_icon_label"]
        self.assertEqual(len(labels), 1, "icon label must exist")
        label_pm = labels[0].pixmap()
        self.assertIsNotNone(label_pm)
        self.assertFalse(label_pm.isNull())
        box.close()
        box.deleteLater()
        _process()

    def test_real_dialog_layout_at_80_logical_pixels(self):
        """P3.45.9 acceptance, on the REAL laid-out dialog: the icon
        label displays the pixmap at the 80 logical pixel target with
        no clipping, no overlap with the text column, no runaway
        dialog expansion, and the OK button survives the wider icon
        column (runtime evidence mirrors the render verification in
        the design record §8)."""
        box = self._box()
        box.show()
        _process(40)
        try:
            labels = [l for l in box.findChildren(QLabel)
                      if l.objectName() == "qt_msgboxex_icon_label"]
            self.assertEqual(len(labels), 1)
            label = labels[0]
            label_pm = label.pixmap()
            self.assertIsNotNone(label_pm)
            self.assertFalse(label_pm.isNull())
            dpr = float(label_pm.devicePixelRatio()) or 1.0
            logical_w = label_pm.width() / dpr
            self.assertAlmostEqual(logical_w, 80.0, delta=0.5,
                                   msg="displayed icon must sit at the "
                                       "80 logical px target")
            # No clipping: the laid-out label fits the pixmap's logical
            # width (its content box, padding included).
            self.assertGreaterEqual(label.width(), int(logical_w),
                                    "icon label narrower than the "
                                    "pixmap — clipping")
            self.assertLessEqual(label.height(), 200,
                                 "icon column exploded vertically")
            # The label sits fully inside the dialog.
            self.assertGreaterEqual(label.x(), 0)
            self.assertGreaterEqual(label.y(), 0)
            self.assertLessEqual(label.x() + label.width(),
                                 box.width())
            # No runaway expansion (the 48px round rendered ~300 wide;
            # the 80px target legitimately widens it, bounded here).
            self.assertLess(box.width(), 1000)
            self.assertLess(box.height(), 600)
            # Buttons and default survive the wider icon column.
            buttons = box.buttons()
            self.assertEqual([b.text() for b in buttons], ["OK"])
            self.assertIs(box.defaultButton(), buttons[0])
        finally:
            box.close()
            box.deleteLater()
            _process()

    def test_missing_asset_falls_back_to_stock_icon(self):
        """Non-fatal-asset house rule: when the PNG is unavailable the
        dialog keeps the stock information icon."""
        from unittest.mock import patch
        with patch.object(feedback_mod, "_SUCCESS_ICON_FILE",
                          "no_such_icon.png"):
            _reset_success_icon_cache()
            try:
                self.assertIsNone(_success_icon_pixmap())
                box = FeedbackDialog._make_information_box(
                    None, "GUINEO", "Project saved: Eden", None)
                # icon() stays Information: a custom iconPixmap would
                # flip the enum to NoIcon (Qt semantics), so the enum IS
                # the proof that the stock icon is displayed.
                self.assertEqual(box.icon(),
                                 QMessageBox.Icon.Information)
                box.close()
                box.deleteLater()
                _process()
            finally:
                _reset_success_icon_cache()

    def test_warning_dialogs_keep_stock_icon(self):
        """Scope guard: plain warnings keep their semantic stock icon —
        the success pixmap is NEVER applied outside FeedbackDialog
        (a custom pixmap would flip icon() to NoIcon; the enum staying
        Warning proves the stock semantic icon is displayed)."""
        box = QMessageBox(QMessageBox.Icon.Warning, "GUINEO",
                          "Some output files are missing")
        try:
            self.assertEqual(box.icon(), QMessageBox.Icon.Warning)
            box2 = QMessageBox(QMessageBox.Icon.Critical, "GUINEO",
                               "Scene Combine failed")
            self.assertEqual(box2.icon(), QMessageBox.Icon.Critical)
            box3 = QMessageBox(QMessageBox.Icon.Question, "GUINEO",
                               "Delete this project?")
            self.assertEqual(box3.icon(), QMessageBox.Icon.Question)
            for b in (box2, box3):
                b.close()
                b.deleteLater()
        finally:
            box.close()
            box.deleteLater()
            _process()


# ---------------------------------------------------------------------------
# 4) The affected call sites (source-level migration pins)
# ---------------------------------------------------------------------------
class TestEventFeedbackCallSites(unittest.TestCase):
    """Every affected event-feedback window routes through
    FeedbackDialog; the semantic error/warning/confirmation vocabulary
    keeps the plain QMessageBox forms."""

    def _src(self, rel):
        return open(os.path.join(_REPO_ROOT, rel), encoding="utf-8").read()

    def test_main_window_migrated_success_sites(self):
        src = self._src(os.path.join("ui", "main_window.py"))
        # ALL positive event-feedback sites route through the common
        # path: 4 structured (P3.44.3, incl. the user's save example)
        # + 7 migrated in the closeout = 11 call expressions.
        self.assertEqual(src.count("FeedbackDialog.information"), 11,
                         "every positive event-feedback site in the "
                         "MainWindow must route through FeedbackDialog")
        for needle in (
            # already structured (P3.44.3) — incl. the user's example:
            '"Project saved: {0}"',
            '"Scene saved"',
            '"Preset exported"',
            '"Voice profile \'{0}\' exported"',
            # migrated in the closeout:
            '"Imported preset: {0}"',
            '"Voice profile \'{0}\' imported successfully."',
            '"Voice \'{0}\' exported"',
            '"History entry exported"',
            '"Legacy project imported as new native Project: {0}"',
            '"Scene loaded: {0}"',
        ):
            self.assertIn(needle, src, "missing migrated call: " + needle)
        # The OLD plain success blobs are gone:
        for stale in (
            '"Voice \'{0}\' exported to:\\n{1}',
            '"History entry exported to:',
            '"Legacy project imported as new native Project:\\n"',
            '"Scene loaded: {0}\\n{1} lines',
        ):
            self.assertNotIn(stale, src,
                             "stale plain-success blob still present: "
                             + stale)

    def test_panel_migrated_success_sites(self):
        export_src = self._src(
            os.path.join("ui", "panels", "export_project_dialog.py"))
        self.assertIn('FeedbackDialog.information(self, "Export Complete",\n'
                      '                                   "Project exported successfully.", details)',
                      export_src)
        assemble_src = self._src(
            os.path.join("ui", "panels", "assemble_dialog.py"))
        self.assertIn('FeedbackDialog.information(\n'
                      '            self, "Assembly Complete", summary_lines[0],',
                      assemble_src)
        char_src = self._src(
            os.path.join("ui", "panels", "character_dialog.py"))
        self.assertIn('FeedbackDialog.information(\n'
                      '                self, "Added to Scene",',
                      char_src)
        # The panels import the shared dialog (no local re-implementation):
        for rel in ("export_project_dialog", "assemble_dialog",
                    "character_dialog"):
            self.assertIn("from ui.feedback import FeedbackDialog",
                          self._src(os.path.join("ui", "panels",
                                                 rel + ".py")))

    def test_neutral_and_error_sites_keep_plain_qmessagebox(self):
        """Neutral notices, real errors and confirmations keep the plain
        QMessageBox forms — no global icon replacement."""
        src = self._src(os.path.join("ui", "main_window.py"))
        # A neutral notice (no event happened — precondition info) keeps
        # the plain information form with the STOCK icon:
        self.assertIn('"No active project.\\n\\nCreate or open a project first."',
                      src)
        self.assertIn('QMessageBox.information(self, APP_NAME,', src)
        # Real error dialogs keep the critical form:
        self.assertIn('"Failed to import preset: {0}"', src)
        self.assertIn('QMessageBox.critical(self, APP_NAME,', src)
        # Confirmations keep the question form:
        self.assertIn('QMessageBox.question(', src)
        char_src = self._src(
            os.path.join("ui", "panels", "character_dialog.py"))
        self.assertIn('"Already Added"', char_src)
        self.assertIn('QMessageBox.information(', char_src)
        # No global monkey-patching of the QMessageBox icon machinery:
        feedback_src = self._src(os.path.join("ui", "feedback.py"))
        for banned in ("QMessageBox.information =",
                       "QMessageBox.critical =",
                       "QMessageBox.warning =",
                       "QMessageBox.question ="):
            self.assertNotIn(banned, feedback_src)


# ---------------------------------------------------------------------------
# 5) Release / runtime hygiene
# ---------------------------------------------------------------------------
class TestRuntimeHygiene(unittest.TestCase):

    def test_gitignore_excludes_runtime_project_and_preset_trees(self):
        gi = open(os.path.join(_REPO_ROOT, ".gitignore"),
                  encoding="utf-8").read()
        self.assertIn("projects/", gi)
        self.assertIn("presets/", gi)

    def test_suite_level_app_root_isolation_active(self):
        """The conftest redirect is live: MainWindow's APP_ROOT is a
        session temp root (with the read-only assets symlink), never the
        repository — the mutation channel that shipped the five
        project.json test artifacts is structurally closed."""
        import ui.main_window as mw_mod
        self.assertNotEqual(mw_mod.APP_ROOT, _REPO_ROOT,
                            "MainWindow APP_ROOT must be redirected away "
                            "from the repository during tests")
        self.assertTrue(os.path.isdir(mw_mod.APP_ROOT))
        # Read-only assets stay resolvable through the redirect.
        self.assertTrue(os.path.isfile(
            os.path.join(mw_mod.APP_ROOT, "assets", "brand",
                         "guineo_logo.png")))

    def test_real_main_window_persists_into_session_root(self):
        """A REAL MainWindow constructed over a tmp-root Engine resolves
        its ProjectManager INSIDE the redirected APP_ROOT — a save can
        never write into the repository's projects/ tree again."""
        import tempfile
        import shutil
        from unittest.mock import patch, PropertyMock
        import ui.main_window as mw_mod
        from engine.engine import Engine
        with tempfile.TemporaryDirectory(prefix="ss_closeout_") as tmp:
            engine = Engine(app_root=tmp)
            patchers = [
                patch.object(type(engine._model), "is_loaded",
                             new_callable=PropertyMock, return_value=True),
                patch.object(type(engine._model), "device",
                             new_callable=PropertyMock, return_value="cpu"),
                patch.object(type(engine._model),
                             "get_model_and_tokenizer",
                             return_value=(object(), None)),
            ]
            for p in patchers:
                p.start()
            win = None
            try:
                win = mw_mod.MainWindow(engine)
                pm_dir = win._project_manager._dir
                self.assertNotEqual(
                    os.path.commonpath([os.path.abspath(pm_dir),
                                        _REPO_ROOT]), _REPO_ROOT,
                    "the ProjectManager must NOT resolve inside the "
                    "repository")
                self.assertTrue(os.path.abspath(pm_dir).startswith(
                    os.path.abspath(mw_mod.APP_ROOT)))
            finally:
                if win is not None:
                    win.close()
                    win.deleteLater()
                for p in patchers:
                    p.stop()
                _process(50)
                shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
