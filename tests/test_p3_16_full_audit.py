"""
SpeechStudio — P3.16 Full Settings Audit Tests
================================================

Comprehensive tests for the full settings audit:
  - No duplicate Theme control
  - No duplicate Developer Mode control
  - No broken "Show Higgs tokens" control
  - Sidebar/Control Panel width applied from settings
  - All settings persist correctly
  - Each setting has a clear runtime path

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_16_full_audit.py -v
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
# 1. No duplicate settings
# ===========================================================================

class TestNoDuplicateSettings(unittest.TestCase):
    """P3.16: No setting should be duplicated across tabs."""

    def test_theme_only_in_appearance_tab(self):
        """Theme must appear ONLY in Appearance tab, not General."""
        with open(os.path.join(_ROOT, "ui/panels/settings_dialog.py")) as f:
            src = f.read()
        # Find the General tab build method
        general_start = src.find("def _build_general_tab")
        general_end = src.find("def _build_generation_tab")
        general_src = src[general_start:general_end]
        # General tab must NOT have a Theme combo
        self.assertNotIn("Theme:", general_src,
                         "General tab must not have Theme (it's in Appearance)")

    def test_developer_mode_only_in_developer_tab(self):
        """Developer Mode must appear ONLY in Developer tab, not General."""
        with open(os.path.join(_ROOT, "ui/panels/settings_dialog.py")) as f:
            src = f.read()
        general_start = src.find("def _build_general_tab")
        general_end = src.find("def _build_generation_tab")
        general_src = src[general_start:general_end]
        # Check code lines only (not comments)
        code_lines = [line for line in general_src.split('\n')
                      if not line.strip().startswith('#')]
        code_src = '\n'.join(code_lines)
        self.assertNotIn("Developer Mode", code_src,
                         "General tab must not have Developer Mode control (it's in Developer tab)")

    def test_no_show_tokens_control(self):
        """'Show Higgs tokens' must be removed (was broken)."""
        with open(os.path.join(_ROOT, "ui/panels/settings_dialog.py")) as f:
            src = f.read()
        # Check code lines only (comments mention it for documentation)
        code_lines = [line for line in src.split('\n')
                      if not line.strip().startswith('#')]
        code_src = '\n'.join(code_lines)
        self.assertNotIn("self._show_tokens", code_src,
                         "Show Higgs tokens widget must be removed from code")

    def test_theme_in_appearance_tab(self):
        """Theme must be in Appearance tab."""
        with open(os.path.join(_ROOT, "ui/panels/settings_dialog.py")) as f:
            src = f.read()
        appearance_start = src.find("def _build_appearance_tab")
        appearance_end = src.find("def _load_values")
        appearance_src = src[appearance_start:appearance_end]
        self.assertIn("Application Theme:", appearance_src)


# ===========================================================================
# 2. Sidebar/Control Panel width applied from settings
# ===========================================================================

class TestPanelWidthsFromSettings(unittest.TestCase):
    """P3.16: Sidebar + Control Panel widths must be loaded from settings."""

    def test_sidebar_width_loaded_from_settings(self):
        """MainWindow must load sidebar_width from settings, not hardcode 400."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn('self._settings_manager.get("window", "sidebar_width"', src)
        # Must NOT have the old hardcoded SIDE_PANEL_WIDTH = 400
        self.assertNotIn("SIDE_PANEL_WIDTH = 400", src,
                         "Sidebar width must come from settings, not hardcoded")

    def test_control_panel_width_loaded_from_settings(self):
        """MainWindow must load control_panel_width from settings."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn('self._settings_manager.get("window", "control_panel_width"', src)

    def test_control_panel_uses_CONTROL_PANEL_WIDTH(self):
        """Control panel must use CONTROL_PANEL_WIDTH, not SIDE_PANEL_WIDTH."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("self._control_panel.setFixedWidth(CONTROL_PANEL_WIDTH)", src)


# ===========================================================================
# 3. Settings dialog loads/saves correctly
# ===========================================================================

class TestSettingsDialogLoadSave(unittest.TestCase):
    """P3.16: Settings dialog must load and save all controls correctly."""

    def setUp(self):
        from engine.settings_manager import SettingsManager
        self.tmpdir = tempfile.mkdtemp()
        self.sm = SettingsManager(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_dialog_loads_check_for_model(self):
        """Settings dialog must load check_for_model_on_startup."""
        from ui.panels.settings_dialog import SettingsDialog
        self.sm.set("application", "check_for_model_on_startup", False)
        dlg = SettingsDialog(self.sm, None)
        self.assertFalse(dlg._check_model.isChecked())

    def test_dialog_saves_check_for_model(self):
        """Settings dialog must save check_for_model_on_startup."""
        from ui.panels.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.sm, None)
        dlg._check_model.setChecked(False)
        dlg._on_save()
        self.assertFalse(self.sm.get("application", "check_for_model_on_startup"))

    def test_dialog_loads_theme_from_appearance(self):
        """Theme must load into the Appearance tab's combo, not General."""
        from ui.panels.settings_dialog import SettingsDialog
        self.sm.set("application", "theme", "light")
        dlg = SettingsDialog(self.sm, None)
        self.assertEqual(dlg._app_theme.currentText(), "light")

    def test_dialog_saves_theme_from_appearance(self):
        """Theme must save from the Appearance tab's combo."""
        from ui.panels.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.sm, None)
        dlg._app_theme.setCurrentText("light")
        dlg._on_save()
        self.assertEqual(self.sm.get("application", "theme"), "light")

    def test_dialog_loads_sidebar_width(self):
        """Sidebar width must load into the Appearance tab's spinbox."""
        from ui.panels.settings_dialog import SettingsDialog
        self.sm.set("window", "sidebar_width", 350)
        dlg = SettingsDialog(self.sm, None)
        self.assertEqual(dlg._sidebar_width.value(), 350)

    def test_dialog_saves_sidebar_width(self):
        """Sidebar width must save from the Appearance tab's spinbox."""
        from ui.panels.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.sm, None)
        dlg._sidebar_width.setValue(350)
        dlg._on_save()
        self.assertEqual(self.sm.get("window", "sidebar_width"), 350)

    def test_dialog_loads_control_panel_width(self):
        """Control panel width must load."""
        from ui.panels.settings_dialog import SettingsDialog
        self.sm.set("window", "control_panel_width", 450)
        dlg = SettingsDialog(self.sm, None)
        self.assertEqual(dlg._control_width.value(), 450)

    def test_dialog_saves_control_panel_width(self):
        """Control panel width must save."""
        from ui.panels.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.sm, None)
        dlg._control_width.setValue(450)
        dlg._on_save()
        self.assertEqual(self.sm.get("window", "control_panel_width"), 450)

    def test_dialog_loads_concatenate_silence(self):
        """Dialogue Silence must load."""
        from ui.panels.settings_dialog import SettingsDialog
        self.sm.set("audio", "concatenate_silence_ms", 500)
        dlg = SettingsDialog(self.sm, None)
        self.assertEqual(dlg._concat_silence.value(), 500)

    def test_dialog_saves_concatenate_silence(self):
        """Dialogue Silence must save."""
        from ui.panels.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.sm, None)
        dlg._concat_silence.setValue(500)
        dlg._on_save()
        self.assertEqual(self.sm.get("audio", "concatenate_silence_ms"), 500)

    def test_dialog_loads_concatenate_randomize(self):
        """Dialogue Silence Randomization must load."""
        from ui.panels.settings_dialog import SettingsDialog
        self.sm.set("audio", "concatenate_randomize", False)
        dlg = SettingsDialog(self.sm, None)
        self.assertFalse(dlg._concat_randomize.isChecked())

    def test_dialog_saves_concatenate_randomize(self):
        """Dialogue Silence Randomization must save."""
        from ui.panels.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.sm, None)
        dlg._concat_randomize.setChecked(False)
        dlg._on_save()
        self.assertFalse(self.sm.get("audio", "concatenate_randomize"))


# ===========================================================================
# 4. Full persistence round-trip through the dialog
# ===========================================================================

class TestFullDialogPersistenceRoundTrip(unittest.TestCase):
    """P3.16: Change a setting via dialog, restart (new SettingsManager), verify."""

    def setUp(self):
        from engine.settings_manager import SettingsManager
        self.tmpdir = tempfile.mkdtemp()
        self.sm = SettingsManager(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _round_trip(self, set_callback, verify_key, verify_value):
        """Helper: set via dialog, save, reload, verify."""
        from ui.panels.settings_dialog import SettingsDialog
        from engine.settings_manager import SettingsManager
        dlg = SettingsDialog(self.sm, None)
        set_callback(dlg)
        dlg._on_save()
        # Simulate restart
        sm2 = SettingsManager(self.tmpdir)
        self.assertEqual(sm2.get(*verify_key), verify_value)

    def test_theme_round_trip(self):
        self._round_trip(
            lambda dlg: dlg._app_theme.setCurrentText("light"),
            ("application", "theme"), "light")

    def test_check_model_round_trip(self):
        self._round_trip(
            lambda dlg: dlg._check_model.setChecked(False),
            ("application", "check_for_model_on_startup"), False)

    def test_sidebar_width_round_trip(self):
        self._round_trip(
            lambda dlg: dlg._sidebar_width.setValue(350),
            ("window", "sidebar_width"), 350)

    def test_control_panel_width_round_trip(self):
        self._round_trip(
            lambda dlg: dlg._control_width.setValue(450),
            ("window", "control_panel_width"), 450)

    def test_concat_silence_round_trip(self):
        self._round_trip(
            lambda dlg: dlg._concat_silence.setValue(750),
            ("audio", "concatenate_silence_ms"), 750)

    def test_concat_randomize_round_trip(self):
        self._round_trip(
            lambda dlg: dlg._concat_randomize.setChecked(False),
            ("audio", "concatenate_randomize"), False)

    def test_volume_round_trip(self):
        self._round_trip(
            lambda dlg: dlg._volume.setValue(25),
            ("audio", "volume"), 0.25)

    def test_auto_play_round_trip(self):
        self._round_trip(
            lambda dlg: dlg._auto_play.setChecked(False),
            ("generation", "auto_play"), False)  # Note: saved as GenerationParameters

    def test_normalize_round_trip(self):
        self._round_trip(
            lambda dlg: dlg._normalize.setChecked(True),
            ("generation", "normalize_output"), True)

    def test_temperature_round_trip(self):
        self._round_trip(
            lambda dlg: dlg._temperature.setValue(1.8),
            ("generation", "temperature"), 1.8)

    def test_precision_round_trip(self):
        self._round_trip(
            lambda dlg: dlg._precision_fp16.setChecked(True),
            ("performance", "model_precision"), "float16")


    def test_developer_mode_removed(self):
        """P3.16B: Developer Mode checkbox must be removed (was dead)."""
        with open(os.path.join(_ROOT, 'ui/panels/settings_dialog.py')) as f:
            src = f.read()
        code_lines = [l for l in src.split('\n') if not l.strip().startswith('#')]
        code_src = '\n'.join(code_lines)
        self.assertNotIn('self._dev_startup', code_src,
                         'Developer Mode checkbox must be removed')

    def test_keep_model_removed(self):
        """P3.16B: Keep model loaded checkbox must be removed (was dead)."""
        with open(os.path.join(_ROOT, 'ui/panels/settings_dialog.py')) as f:
            src = f.read()
        code_lines = [l for l in src.split('\n') if not l.strip().startswith('#')]
        code_src = '\n'.join(code_lines)
        self.assertNotIn('self._keep_model', code_src,
                         'Keep model loaded must be removed')

    def test_sidebar_collapsed_removed(self):
        """P3.16B: Collapse sidebar checkbox must be removed (was dead)."""
        with open(os.path.join(_ROOT, 'ui/panels/settings_dialog.py')) as f:
            src = f.read()
        code_lines = [l for l in src.split('\n') if not l.strip().startswith('#')]
        code_src = '\n'.join(code_lines)
        self.assertNotIn('self._sidebar_collapsed', code_src,
                         'Collapse sidebar must be removed')

    def test_audio_auto_play_removed(self):
        """P3.16B: Audio auto-play duplicate must be removed."""
        with open(os.path.join(_ROOT, 'ui/panels/settings_dialog.py')) as f:
            src = f.read()
        code_lines = [l for l in src.split('\n') if not l.strip().startswith('#')]
        code_src = '\n'.join(code_lines)
        self.assertNotIn('self._audio_auto_play', code_src,
                         'Audio auto-play duplicate must be removed')

class TestRuntimeEffects(unittest.TestCase):
    """P3.16: Verify which settings have actual runtime effects."""

    def test_concatenate_silence_used_in_main_window(self):
        """Dialogue Silence must be used in MainWindow for concatenation."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("concatenate_silence_ms", src)

    def test_concatenate_randomize_used_in_main_window(self):
        """Dialogue Silence Randomization must be used in MainWindow."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("concatenate_randomize", src)

    def test_playback_volume_used_in_main_window(self):
        """Playback volume must be applied to the waveform player."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("playback_volume", src)

    def test_generation_params_used_in_control_panel(self):
        """Generation parameters must be loaded into the control panel."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("get_generation_defaults", src)
        self.assertIn("set_parameters", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
