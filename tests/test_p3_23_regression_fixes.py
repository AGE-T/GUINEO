"""
SpeechStudio — P3.23 Regression Fix Tests
==========================================

RUNTIME tests for the P3.23 fixes of previously-audited defects that the
task spec §19 required to be addressed (not silently ignored):

  1. F11/Esc "Ambiguous shortcut overload" (dead keys) — fixed by removing
     the duplicate QShortcut registrations (single QAction registration).
  2. Settings dialog opening TWICE (duplicate menu connect).
  3. Settings split brain — merge-on-write: a second SettingsManager
     instance saving its own key no longer reverts the first instance's
     key (the audit's proven data-loss scenario).
  4. History delete path traversal (arbitrary .json delete + arbitrary
     audio delete via crafted entry IDs / output paths).
  5. Project delete path traversal (arbitrary rmtree via crafted project ID).
  6. Voice import API drift — Engine.import_voice_avatar and
     Engine.set_voice_speaker_metadata now exist and are functional
     (previously AttributeError → "Failed to import voice").
  7. HISTORY_UPDATED thread affinity — routed through a Qt signal.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_23_regression_fixes.py -v
"""

from __future__ import annotations
import os
import sys
import unittest
import tempfile
import shutil
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication([])

QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.question = staticmethod(
    lambda *a, **k: QMessageBox.StandardButton.No)


# ===========================================================================
# 1. F11 / Esc shortcut ambiguity (runtime)
# ===========================================================================

class TestShortcutFix(unittest.TestCase):
    """F11 and Esc each registered EXACTLY ONCE → no ambiguous overload."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="ss_p323_sc_")
        from engine.engine import Engine
        cls.engine = Engine(app_root=cls.tmpdir)
        from ui.main_window import MainWindow
        cls.win = MainWindow(cls.engine)
        cls.win.show()  # shortcuts deliver only to shown windows
        QApplication.processEvents()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.win.close()
        except Exception:
            pass
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_f11_single_registration_toggles_fullscreen(self):
        """RUNTIME: pressing F11 toggles fullscreen (the audit proved both
        F11 AND Esc were dead — 'Ambiguous shortcut overload')."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.assertFalse(self.win.isFullScreen())
        QTest.keyClick(self.win, Qt.Key.Key_F11)
        QApplication.processEvents()
        self.assertTrue(self.win.isFullScreen(),
                        "F11 must toggle INTO fullscreen (was dead pre-fix)")
        QTest.keyClick(self.win, Qt.Key.Key_F11)
        QApplication.processEvents()
        self.assertFalse(self.win.isFullScreen(),
                         "F11 must toggle OUT of fullscreen")

    def test_no_ambiguous_shortcut_overload_f11(self):
        """There must be exactly ONE object claiming the F11 shortcut."""
        from PySide6.QtGui import QShortcut
        from PySide6.QtCore import Qt
        f11_shortcuts = [s for s in self.win.findChildren(QShortcut)
                         if s.key().toString().contains("F11", Qt.CaseSensitivity.CaseInsensitive)]
        self.assertEqual(len(f11_shortcuts), 0,
                         "standalone QShortcut(F11) duplicates must not exist")
        # The menubar QAction remains the single registration.
        act = self.win._menu_bar.get_action("fullscreen")
        self.assertIsNotNone(act)
        self.assertEqual(act.shortcut().toString(), "F11")

    def test_stop_action_has_no_ambiguous_esc_shortcut(self):
        """P3.25 (audit SS-H13): Esc is handled by ONE authoritative
        handler (MainWindow.keyPressEvent priority chain: stop >
        fullscreen > search bar). The Stop QAction must NOT carry an
        Esc shortcut — an ApplicationShortcut QAction intercepted every
        Esc press app-wide and made Esc-exit-fullscreen /
        Esc-close-search unreachable."""
        act = self.win._menu_bar.get_action("stop")
        self.assertIsNotNone(act)
        self.assertEqual(
            act.shortcut().toString(), "",
            "Stop QAction must not register a global Esc shortcut "
            "(P3.25 SS-H13)")


# ===========================================================================
# 2. Settings opens ONCE (duplicate connect removed)
# ===========================================================================

class TestSettingsSingleConnect(unittest.TestCase):
    def test_settings_connected_once(self):
        """RUNTIME: triggering the settings action creates ONE dialog.

        The audit showed two registrations → two dialogs. We count
        SettingsDialog instantiations by replacing the dialog class in
        its module namespace with an instrumented fake.
        """
        from unittest.mock import patch
        from PySide6.QtCore import QObject, Signal
        import ui.panels.settings_dialog as sd
        from ui.main_window import MainWindow
        from engine.engine import Engine
        tmpdir = tempfile.mkdtemp(prefix="ss_p323_st_")

        created = []

        class FakeSettingsDialog(QObject):
            settings_changed = Signal()

            def __init__(self, settings, parent=None):
                super().__init__(parent)
                created.append(self)

            def exec(self):
                return 0

            def raise_(self):
                pass

            def activateWindow(self):
                pass

        try:
            engine = Engine(app_root=tmpdir)
            win = MainWindow(engine)
            with patch.object(sd, "SettingsDialog", FakeSettingsDialog):
                win._on_open_settings()
            self.assertEqual(len(created), 1,
                             "Settings action must open the dialog exactly "
                             "once (was 2 pre-fix)")
            win.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 3. Settings merge-on-write (split brain data loss)
# ===========================================================================

class TestSettingsMergeOnWrite(unittest.TestCase):
    """The audit's smoking gun: instance A writes theme, instance B writes
    max_tokens → A's theme got REVERTED by B's save. Merge-on-write must
    preserve BOTH."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ss_p323_sm_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_second_instance_does_not_revert_first(self):
        from engine.settings_manager import SettingsManager
        sm1 = SettingsManager(self.tmpdir)
        sm1.set("application", "theme", "light")
        # A NEW instance (like Engine's) starts later with the same file.
        sm2 = SettingsManager(self.tmpdir)
        sm2.set("generation", "temperature", 0.42)
        # sm1's write must survive sm2's save.
        with open(os.path.join(self.tmpdir, "settings.json")) as f:
            disk = json.load(f)
        self.assertEqual(disk["application"]["theme"], "light",
                         "theme must NOT be reverted by the second "
                         "instance's save (audit data-loss scenario)")
        self.assertEqual(disk["generation"]["temperature"], 0.42)

    def test_cross_instance_alternating_writes(self):
        from engine.settings_manager import SettingsManager
        sm_a = SettingsManager(self.tmpdir)
        sm_b = SettingsManager(self.tmpdir)
        for i in range(3):
            sm_a.set("application", "playback_volume", 0.5 + i * 0.1)
            sm_b.set("audio", "volume", 0.6 + i * 0.1)
        with open(os.path.join(self.tmpdir, "settings.json")) as f:
            disk = json.load(f)
        self.assertAlmostEqual(disk["application"]["playback_volume"], 0.7)
        self.assertAlmostEqual(disk["audio"]["volume"], 0.8)

    def test_set_section_merge_preserves_other_sections(self):
        from engine.settings_manager import SettingsManager
        sm_a = SettingsManager(self.tmpdir)
        sm_a.set("application", "theme", "synthwave")
        sm_b = SettingsManager(self.tmpdir)
        sm_b.set_section("window", {"width": 999, "height": 777,
                                    "sidebar_width": 300,
                                    "control_panel_width": 300,
                                    "sidebar_collapsed": False,
                                    "control_panel_collapsed": False})
        with open(os.path.join(self.tmpdir, "settings.json")) as f:
            disk = json.load(f)
        self.assertEqual(disk["application"]["theme"], "synthwave")
        self.assertEqual(disk["window"]["width"], 999)

    def test_missing_file_creates_defaults(self):
        from engine.settings_manager import SettingsManager
        sm = SettingsManager(self.tmpdir)
        with open(os.path.join(self.tmpdir, "settings.json")) as f:
            disk = json.load(f)
        self.assertIn("application", disk)
        self.assertIn("generation", disk)


# ===========================================================================
# 4. History delete path traversal
# ===========================================================================

class TestHistoryDeleteTraversal(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ss_p323_ht_")
        # Simulated app root: <tmp>/app; history at <app>/settings/history.
        self.app_root = os.path.join(self.tmpdir, "app")
        self.hist_dir = os.path.join(self.app_root, "settings", "history")
        os.makedirs(self.hist_dir)
        # A sensitive file OUTSIDE the history dir that must survive.
        self.secret = os.path.join(self.tmpdir, "secret.json")
        with open(self.secret, "w") as f:
            f.write("{}")
        from engine.history_manager import HistoryManager
        self.hm = HistoryManager(history_dir=self.hist_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _add_entry(self, entry_id, output_path=""):
        from engine.models import GenerationResult
        result = GenerationResult(
            success=True, output_path=output_path,
            prompt="x", timestamp=entry_id)
        return self.hm.add(result)

    def test_traversal_entry_id_rejected(self):
        """A crafted entry_id containing ../ must not delete outside files."""
        self._add_entry("20260827_120000")
        ok = self.hm.delete("../../secret", delete_audio=False)
        self.assertFalse(ok)
        self.assertTrue(os.path.isfile(self.secret))

    def test_absolute_entry_id_rejected(self):
        ok = self.hm.delete(os.path.join(self.tmpdir, "secret"), delete_audio=False)
        self.assertFalse(ok)
        self.assertTrue(os.path.isfile(self.secret))

    def test_normal_delete_still_works(self):
        entry_id = self._add_entry("20260827_130000")
        ok = self.hm.delete(entry_id, delete_audio=False)
        self.assertTrue(ok)

    def test_audio_delete_outside_app_root_rejected(self):
        """A crafted output_path pointing outside the app root must be
        refused (no arbitrary audio deletion)."""
        entry_id = self._add_entry(
            "20260827_140000", output_path="../../outside.wav")
        # Create the target file that the traversal path points to.
        target = os.path.join(self.tmpdir, "outside.wav")
        with open(target, "wb") as f:
            f.write(b"WAV")
        ok = self.hm.delete(entry_id, delete_audio=True)
        # Entry JSON deletion OK, but the outside file must SURVIVE.
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(target),
                        "audio outside the app root must not be deleted")

    def test_audio_delete_inside_app_root_works(self):
        audio_rel = os.path.join("outputs", "in_root.wav")
        audio_abs = os.path.join(self.app_root, audio_rel)
        os.makedirs(os.path.dirname(audio_abs), exist_ok=True)
        with open(audio_abs, "wb") as f:
            f.write(b"WAV")
        entry_id = self._add_entry("20260827_150000",
                                   output_path=audio_rel.replace(os.sep, "/"))
        ok = self.hm.delete(entry_id, delete_audio=True)
        self.assertTrue(ok)
        self.assertFalse(os.path.isfile(audio_abs))


# ===========================================================================
# 5. Project delete path traversal
# ===========================================================================

class TestProjectDeleteTraversal(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ss_p323_pt_")
        self.projects_dir = os.path.join(self.tmpdir, "projects")
        os.makedirs(self.projects_dir)
        # A directory outside the projects dir that must survive.
        self.outside = os.path.join(self.tmpdir, "precious")
        os.makedirs(self.outside)
        with open(os.path.join(self.outside, "data.txt"), "w") as f:
            f.write("important")
        from engine.project_manager import ProjectManager
        self.pm = ProjectManager(projects_dir=self.projects_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_dotdot_project_id_rejected(self):
        """Crafted project_id '../precious' must not rmtree outside."""
        self.assertFalse(self.pm.delete_project("../precious"))
        self.assertTrue(os.path.isdir(self.outside))
        self.assertTrue(os.path.isfile(
            os.path.join(self.outside, "data.txt")))

    def test_absolute_project_id_rejected(self):
        self.assertFalse(self.pm.delete_project(self.outside))
        self.assertTrue(os.path.isdir(self.outside))

    def test_separator_ids_rejected(self):
        self.assertFalse(self.pm.delete_project("a/b"))
        self.assertFalse(self.pm.delete_project("a\\b"))
        self.assertTrue(os.path.isdir(self.outside))

    def test_normal_delete_works(self):
        from engine.models import Project
        proj = Project(name="Real", id="real_proj")
        self.pm.save_project(proj)
        self.assertTrue(os.path.isdir(
            os.path.join(self.projects_dir, "real_proj")))
        self.assertTrue(self.pm.delete_project("real_proj"))
        self.assertFalse(os.path.isdir(
            os.path.join(self.projects_dir, "real_proj")))


# ===========================================================================
# 6. Voice import API (previously AttributeError)
# ===========================================================================

class TestVoiceImportApi(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ss_p323_vi_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_engine_has_both_methods(self):
        from engine.engine import Engine
        engine = Engine(app_root=self.tmpdir)
        self.assertTrue(callable(getattr(engine, "import_voice_avatar", None)),
                        "Engine.import_voice_avatar must exist (was the "
                        "source of 'Failed to import voice' on avatar)")
        self.assertTrue(callable(
            getattr(engine, "set_voice_speaker_metadata", None)))

    def test_import_voice_avatar_runtime(self):
        """RUNTIME: avatar import copies the image + records preview_image."""
        from engine.engine import Engine
        engine = Engine(app_root=self.tmpdir)
        voice = engine.create_voice("Male Deep")
        # Create a small PNG (1x1).
        png = os.path.join(self.tmpdir, "avatar.png")
        with open(png, "wb") as f:
            f.write(bytes.fromhex(
                "89504e470d0a1a0a0000000d494844520000000100000001"
                "08060000001f15c4890000000d4944415478da63f8ffff3f"
                "0300050001ff9f4b950000000049454e44ae426082"))
        result = engine.import_voice_avatar(voice.id, png)
        # P3.43 (Voice Profile Unification §8): preview_image is stored
        # APP-ROOT-RELATIVE ("voices/<id>/avatar.png") — the same
        # convention as reference_audio_path. The old bare "avatar.png"
        # value could never be resolved by any consumer (the avatar path
        # base mismatch). The file location itself is unchanged.
        self.assertEqual(
            result.preview_image,
            os.path.relpath(
                os.path.join(self.tmpdir, "voices", voice.id, "avatar.png"),
                self.tmpdir))
        self.assertTrue(os.path.isfile(os.path.join(
            self.tmpdir, "voices", voice.id, "avatar.png")))

    def test_set_voice_speaker_metadata_runtime(self):
        from engine.engine import Engine
        engine = Engine(app_root=self.tmpdir)
        voice = engine.create_voice("Female Soft")
        result = engine.set_voice_speaker_metadata(
            voice.id, gender="female", age_range="young", mood="warm")
        self.assertEqual(result.gender, "female")
        self.assertEqual(result.age_range, "young")
        self.assertEqual(result.mood, "warm")
        # Persists through a fresh profile load.
        got = engine.get_voice(voice.id)
        self.assertEqual(got.gender, "female")
        self.assertEqual(got.mood, "warm")

    def test_avatar_rejects_non_image(self):
        from engine.engine import Engine
        engine = Engine(app_root=self.tmpdir)
        voice = engine.create_voice("X")
        bad = os.path.join(self.tmpdir, "evil.sh")
        with open(bad, "w") as f:
            f.write("#!/bin/sh\n")
        with self.assertRaises(Exception):
            engine.import_voice_avatar(voice.id, bad)


# ===========================================================================
# 7. HISTORY_UPDATED thread affinity
# ===========================================================================

class TestHistoryUpdatedThreadAffinity(unittest.TestCase):
    def test_handler_emits_signal_not_widget_touch(self):
        """The engine-event handler must route through the Qt signal (the
        engine emits HISTORY_UPDATED on the worker thread)."""
        import inspect
        from ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._on_history_changed)
        self.assertIn("_history_changed_signal.emit()", src)
        self.assertNotIn("_refresh_sidebar()", src)

    def test_signal_connected_to_refresh(self):
        from engine.engine import Engine
        from ui.main_window import MainWindow
        tmpdir = tempfile.mkdtemp(prefix="ss_p323_hu_")
        try:
            engine = Engine(app_root=tmpdir)
            win = MainWindow(engine)
            # PySide6 exposes connected slot count differently; verify the
            # signal is connected by emitting it and checking the sidebar
            # refresh path does not crash, plus inspect source wiring.
            import inspect
            mw_src = inspect.getsource(
                type(win).__init__)
            self.assertIn("_history_changed_signal.connect", mw_src)
            # Emitting the signal must run the connected slot safely.
            win._history_changed_signal.emit()
            QApplication.processEvents()
            self.assertTrue(True)  # reached without error
            win.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
