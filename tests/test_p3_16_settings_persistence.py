"""
SpeechStudio — P3.16 Settings Persistence Audit Tests
======================================================

Tests that the Settings system persists correctly across restarts.

Root cause of the original bug: MainWindow created 13 separate
SettingsManager instances, each with its own in-memory copy of settings.
When one saved, the others' stale copies would overwrite it on their next
save. The fix: a SINGLE persistent self._settings_manager in MainWindow.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_16_settings_persistence.py -v
"""

from __future__ import annotations
import os
import sys
import json
import tempfile
import shutil
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ===========================================================================
# 1. SettingsManager single-instance persistence
# ===========================================================================

class TestSettingsManagerPersistence(unittest.TestCase):
    """P3.16: SettingsManager must persist settings correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.settings_dir = os.path.join(self.tmpdir, "settings")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_save_and_reload(self):
        """Settings saved by one SettingsManager must be loadable by a new one."""
        from engine.settings_manager import SettingsManager
        sm1 = SettingsManager(self.settings_dir)
        sm1.set("application", "theme", "light")
        sm1.set("application", "playback_volume", 0.5)

        # Create a NEW SettingsManager (simulates restart)
        sm2 = SettingsManager(self.settings_dir)
        self.assertEqual(sm2.get("application", "theme"), "light")
        self.assertEqual(sm2.get("application", "playback_volume"), 0.5)

    def test_defaults_dont_overwrite_saved(self):
        """DEFAULT_SETTINGS must NOT overwrite saved values on load."""
        from engine.settings_manager import SettingsManager
        sm1 = SettingsManager(self.settings_dir)
        sm1.set("generation", "temperature", 1.8)

        # Reload — the saved temperature must survive
        sm2 = SettingsManager(self.settings_dir)
        self.assertEqual(sm2.get("generation", "temperature"), 1.8)

    def test_deep_merge_preserves_user_keys(self):
        """_deep_merge must preserve user keys while adding new defaults."""
        from engine.settings_manager import SettingsManager, _deep_merge, DEFAULT_SETTINGS
        user_settings = {
            "application": {"theme": "light", "custom_key": "custom_value"},
            "generation": {"temperature": 1.5},
        }
        merged = _deep_merge(DEFAULT_SETTINGS, user_settings)
        # User value preserved
        self.assertEqual(merged["application"]["theme"], "light")
        self.assertEqual(merged["application"]["custom_key"], "custom_value")
        self.assertEqual(merged["generation"]["temperature"], 1.5)
        # Default keys also present
        self.assertIn("developer_mode", merged["application"])
        self.assertIn("top_p", merged["generation"])

    def test_corrupt_json_falls_back_to_defaults(self):
        """Corrupt settings.json must not crash — falls back to defaults."""
        from engine.settings_manager import SettingsManager
        os.makedirs(self.settings_dir)
        with open(os.path.join(self.settings_dir, "settings.json"), "w") as f:
            f.write("{ invalid json !!!")
        sm = SettingsManager(self.settings_dir)
        # Must have defaults
        self.assertEqual(sm.get("application", "theme"), "modern-dark")

    def test_empty_json_falls_back_to_defaults(self):
        """Empty settings.json must not crash."""
        from engine.settings_manager import SettingsManager
        os.makedirs(self.settings_dir)
        with open(os.path.join(self.settings_dir, "settings.json"), "w") as f:
            f.write("")
        sm = SettingsManager(self.settings_dir)
        self.assertEqual(sm.get("application", "theme"), "modern-dark")

    def test_missing_file_uses_defaults(self):
        """Missing settings.json must use defaults and create the file."""
        from engine.settings_manager import SettingsManager
        sm = SettingsManager(self.settings_dir)
        self.assertEqual(sm.get("application", "theme"), "modern-dark")
        # File should be created
        self.assertTrue(os.path.isfile(os.path.join(self.settings_dir, "settings.json")))

    def test_unknown_keys_dont_crash(self):
        """Unknown keys in settings.json must not crash the app."""
        from engine.settings_manager import SettingsManager
        os.makedirs(self.settings_dir)
        with open(os.path.join(self.settings_dir, "settings.json"), "w") as f:
            json.dump({"unknown_section": {"unknown_key": 42}}, f)
        sm = SettingsManager(self.settings_dir)
        # Must still have defaults
        self.assertEqual(sm.get("application", "theme"), "modern-dark")
        # Unknown key preserved (not deleted)
        self.assertEqual(sm.get("unknown_section", "unknown_key"), 42)


# ===========================================================================
# 2. MainWindow uses single SettingsManager
# ===========================================================================

class TestMainWindowSingleSettingsManager(unittest.TestCase):
    """P3.16: MainWindow must use a SINGLE persistent SettingsManager."""

    def test_main_window_has_settings_manager_attribute(self):
        """MainWindow must have self._settings_manager.

        P3.25 (audit SS-H04): MainWindow now obtains the SHARED instance
        via SettingsManager.instance() — one authoritative owner per file.
        """
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn(
            "self._settings_manager = SettingsManager.instance(settings_dir)",
            src)

    def test_no_local_settings_manager_instances(self):
        """MainWindow must NOT create local SettingsManager instances (except __init__)."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # Count occurrences of direct construction — should be ZERO
        # (the shared instance is obtained via SettingsManager.instance).
        count = src.count("SettingsManager(settings_dir)")
        self.assertEqual(count, 0,
                         "Expected 0 direct SettingsManager(settings_dir) "
                         "constructions (use SettingsManager.instance), "
                         "found {0}".format(count))

    def test_local_variables_use_self_settings_manager(self):
        """Local _sm/sm variables must reference self._settings_manager."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        # The old pattern: _sm = SettingsManager(settings_dir)
        # The new pattern: _sm = self._settings_manager
        self.assertNotIn("_sm = SettingsManager(settings_dir)", src,
                         "Local _sm = SettingsManager(settings_dir) must be replaced")
        self.assertNotIn("sm = SettingsManager(settings_dir)", src,
                         "Local sm = SettingsManager(settings_dir) must be replaced")
        self.assertIn("_sm = self._settings_manager", src,
                      "Local _sm must reference self._settings_manager")
        self.assertIn("sm = self._settings_manager", src,
                      "Local sm must reference self._settings_manager")


# ===========================================================================
# 3. Settings persistence round-trip (simulating restart)
# ===========================================================================

class TestSettingsRoundTrip(unittest.TestCase):
    """P3.16: Settings must survive a full save → restart → reload cycle."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.settings_dir = os.path.join(self.tmpdir, "settings")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_theme_persists(self):
        """Theme must persist across restart."""
        from engine.settings_manager import SettingsManager
        sm1 = SettingsManager(self.settings_dir)
        sm1.set("application", "theme", "light")
        sm2 = SettingsManager(self.settings_dir)
        self.assertEqual(sm2.get("application", "theme"), "light")

    def test_generation_params_persist(self):
        """Generation parameters must persist across restart."""
        from engine.settings_manager import SettingsManager
        from engine.models import GenerationParameters
        sm1 = SettingsManager(self.settings_dir)
        params = GenerationParameters(temperature=1.8, top_p=0.8, seed=42)
        sm1.set_generation_parameters(params)
        sm2 = SettingsManager(self.settings_dir)
        loaded = sm2.get_generation_parameters()
        self.assertEqual(loaded.temperature, 1.8)
        self.assertEqual(loaded.top_p, 0.8)
        self.assertEqual(loaded.seed, 42)

    def test_playback_volume_persists(self):
        """Playback volume must persist across restart."""
        from engine.settings_manager import SettingsManager
        sm1 = SettingsManager(self.settings_dir)
        sm1.set("application", "playback_volume", 0.25)
        sm2 = SettingsManager(self.settings_dir)
        self.assertEqual(sm2.get("application", "playback_volume"), 0.25)

    def test_developer_mode_persists(self):
        """Developer mode must persist across restart."""
        from engine.settings_manager import SettingsManager
        sm1 = SettingsManager(self.settings_dir)
        sm1.set("application", "developer_mode", True)
        sm2 = SettingsManager(self.settings_dir)
        self.assertTrue(sm2.get("application", "developer_mode"))

    def test_check_for_model_persists(self):
        """Check for model on startup must persist."""
        from engine.settings_manager import SettingsManager
        sm1 = SettingsManager(self.settings_dir)
        sm1.set("application", "check_for_model_on_startup", False)
        sm2 = SettingsManager(self.settings_dir)
        self.assertFalse(sm2.get("application", "check_for_model_on_startup"))

    def test_audio_settings_persist(self):
        """Audio settings (volume, auto_play, silence) must persist."""
        from engine.settings_manager import SettingsManager
        sm1 = SettingsManager(self.settings_dir)
        sm1.set("audio", "volume", 0.7)
        sm1.set("audio", "auto_play", False)
        sm1.set("audio", "concatenate_silence_ms", 500)
        sm2 = SettingsManager(self.settings_dir)
        self.assertEqual(sm2.get("audio", "volume"), 0.7)
        self.assertFalse(sm2.get("audio", "auto_play"))
        self.assertEqual(sm2.get("audio", "concatenate_silence_ms"), 500)

    def test_window_settings_persist(self):
        """Window settings (width, height, sidebar) must persist."""
        from engine.settings_manager import SettingsManager
        sm1 = SettingsManager(self.settings_dir)
        sm1.set("window", "width", 1920)
        sm1.set("window", "height", 1080)
        sm1.set("window", "sidebar_width", 320)
        sm2 = SettingsManager(self.settings_dir)
        self.assertEqual(sm2.get("window", "width"), 1920)
        self.assertEqual(sm2.get("window", "height"), 1080)
        self.assertEqual(sm2.get("window", "sidebar_width"), 320)

    def test_performance_settings_persist(self):
        """Performance settings must persist."""
        from engine.settings_manager import SettingsManager
        sm1 = SettingsManager(self.settings_dir)
        sm1.set("performance", "keep_model_loaded", False)
        sm1.set("performance", "model_precision", "float16")
        sm2 = SettingsManager(self.settings_dir)
        self.assertFalse(sm2.get("performance", "keep_model_loaded"))
        self.assertEqual(sm2.get("performance", "model_precision"), "float16")

    def test_multiple_saves_dont_lose_data(self):
        """Multiple saves must not lose earlier values (the original bug)."""
        from engine.settings_manager import SettingsManager
        # The original bug: multiple SettingsManager instances with stale
        # in-memory copies would overwrite each other. With a single
        # instance, this can't happen.
        sm = SettingsManager(self.settings_dir)
        sm.set("application", "theme", "light")
        sm.set("application", "developer_mode", True)
        sm.set("generation", "temperature", 1.5)
        sm.set("audio", "volume", 0.5)
        # Reload — ALL values must survive
        sm2 = SettingsManager(self.settings_dir)
        self.assertEqual(sm2.get("application", "theme"), "light")
        self.assertTrue(sm2.get("application", "developer_mode"))
        self.assertEqual(sm2.get("generation", "temperature"), 1.5)
        self.assertEqual(sm2.get("audio", "volume"), 0.5)


# ===========================================================================
# 4. Settings dialog wiring
# ===========================================================================

class TestSettingsDialogWiring(unittest.TestCase):
    """P3.16: Settings dialog must save to SettingsManager."""

    def test_settings_dialog_exists(self):
        """settings_dialog.py must exist."""
        self.assertTrue(os.path.isfile(os.path.join(_ROOT, "ui/panels/settings_dialog.py")))

    def test_settings_dialog_uses_settings_manager(self):
        """Settings dialog must use SettingsManager for persistence."""
        with open(os.path.join(_ROOT, "ui/panels/settings_dialog.py")) as f:
            src = f.read()
        self.assertIn("SettingsManager", src)


# ===========================================================================
# 5. RecentsManager uses the same SettingsManager
# ===========================================================================

class TestRecentsManagerUsesSharedSettings(unittest.TestCase):
    """P3.16: RecentsManager must use the same shared SettingsManager."""

    def test_recents_manager_receives_settings_manager(self):
        """MainWindow must pass self._settings_manager to RecentsManager."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("RecentsManager(_sm)", src)
        # _sm is now self._settings_manager


if __name__ == "__main__":
    unittest.main(verbosity=2)
