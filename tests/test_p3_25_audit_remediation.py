"""
SpeechStudio — P3.25 Audit Defect Remediation + Stability Hardening
====================================================================

Runtime regression tests for the SECOND INDEPENDENT AUDIT defect register
(SpeechStudio_AUDIT_REPORT.md). Every fixed CRITICAL/HIGH defect and the
MEDIUM defects fixed in P3.25 has a RUNTIME test here — no AST-only claims
for runtime behaviour.

Defect coverage map (test class → audit ID):
  TestSSC01ThreadAffinity          SS-C01  (verified fix: GUI-thread refresh)
  TestSSH01SceneVoiceNone          SS-H01  (None-voice scene stays None)
  TestSSH02BlockOverrideScope      SS-H02  (block edits don't clobber global)
  TestSSH03SceneParamsNone         SS-H03  (None params reset to defaults)
  TestSSH04SettingsArchitecture    SS-H04  (single owner, coherent, atomic)
  TestSSH05RawModePreview          SS-H05  (preview == model input)
  TestSSH06AllowSfxEndToEnd        SS-H06  (+M01/M02: propagation)
  TestSSH07InvalidValues           SS-H07  (reject + surfaced warning)
  TestSSH08PathTraversal           SS-H08  (import sanitize + export contain)
  TestSSH09CorruptSettings         SS-H09  (+L14: no crash, quarantine)
  TestSSH10HistoryRobustness       SS-H10  (per-entry tolerance)
  TestSSH11VoiceImport             SS-H11  (verified-fixed re-check)
  TestSSH12H13Shortcuts            SS-H12/H13 (F11 + Esc priority chain)
  TestSSH14SettingsDialog          SS-H14  (verified-fixed re-check)
  TestSSH15SceneRestoreFaults      SS-H15  (no silent skips)
  TestSceneStateIntegrity          task §5 (A→B→A matrix)
  TestSSMDefects                   SS-M03/M05/M06/M07/M08/M09/M10/M14/M18

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_25_audit_remediation.py -v
"""

from __future__ import annotations
import os
import sys
import json
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch, PropertyMock, MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest

_app = QApplication.instance() or QApplication([])


def _make_wav(path, secs=1):
    import wave as wavemod
    with wavemod.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 24000 * secs)
    return path


# ===========================================================================
# Shared harness — real Engine + real MainWindow in an isolated tmpdir
# ===========================================================================

class _MWHarness:
    """Mixin: real MainWindow with isolated persistence."""

    @classmethod
    def _boot(cls):
        from engine.engine import Engine
        from ui.main_window import MainWindow
        from engine.project_manager import ProjectManager
        from engine.recents_manager import RecentsManager
        cls.tmpdir = tempfile.mkdtemp(prefix="ss_p325_")
        cls.engine = Engine(app_root=cls.tmpdir)
        cls._patcher = patch.object(
            type(cls.engine._model), "is_loaded",
            new_callable=PropertyMock, return_value=True)
        cls._patcher.start()
        cls.win = MainWindow(cls.engine)
        cls._orig_project = cls.win._active_project
        cls._orig_scene = cls.win._active_scene
        cls.projects_dir = os.path.join(cls.tmpdir, "projects")
        cls.win._project_manager = ProjectManager(cls.projects_dir)
        cls.win._recents_manager = RecentsManager()
        cls.win._refresh_sidebar()

    @classmethod
    def _shutdown(cls):
        try:
            cls._patcher.stop()
        except Exception:
            pass
        try:
            cls.win.close()
        except Exception:
            pass
        shutil.rmtree(cls.tmpdir, ignore_errors=True)


# ===========================================================================
# SS-C01 — cross-thread UI mutation (verified fix + runtime proof)
# ===========================================================================

class TestSSC01ThreadAffinity(unittest.TestCase, _MWHarness):
    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_history_updated_from_worker_runs_refresh_on_gui_thread(self):
        """RUNTIME (task §3): publish HISTORY_UPDATED on a NON-GUI thread
        (exactly what the generation worker does) — the sidebar refresh
        must execute on the GUI thread via the queued Qt signal."""
        from engine.events import Event, EventType
        threads_seen = []
        orig = self.win._refresh_sidebar

        def spy():
            threads_seen.append(threading.current_thread().name)
            try:
                orig()
            except Exception:
                pass

        self.win._refresh_sidebar = spy
        try:
            t = threading.Thread(
                target=lambda: self.engine._event_bus.emit(
                    Event(EventType.HISTORY_UPDATED)),
                name="probe-worker")
            t.start()
            t.join()
            for _ in range(20):
                _app.processEvents()
        finally:
            self.win._refresh_sidebar = orig
        # P3.29 (documented supersession): the original assertion was
        # ``assertEqual(threads_seen, ["MainThread"])`` — an EXACT
        # single-call count. That races MainWindow's startup
        # ``QTimer.singleShot(100, self._refresh_sidebar)``
        # (main_window.py): when >=100 ms elapses between window
        # construction (setUpClass) and this test's processEvents loop
        # (machine-load dependent — verified on BOTH the pre-P3.29 and
        # post-P3.29 code: sleep 0.15 -> ['MainThread', 'MainThread']),
        # the pending startup refresh ALSO fires inside the loop, on the
        # GUI thread — a perfectly legal second call. The invariant THIS
        # test exists for (its name and docstring) is THREAD AFFINITY:
        # every observed refresh ran on the GUI thread, and the test's
        # own HISTORY_UPDATED emission was delivered at least once.
        self.assertTrue(threads_seen, "refresh never ran")
        self.assertEqual(
            set(threads_seen), {"MainThread"},
            "sidebar refresh ran off the GUI thread: %s" % threads_seen)

    def test_handler_does_not_touch_widgets_directly(self):
        """The handler only emits a Qt signal (source-level invariant,
        kept from P3.23 — complements the runtime proof above)."""
        import inspect
        from ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._on_history_changed)
        self.assertIn("_history_changed_signal.emit()", src)
        self.assertNotIn("_refresh_sidebar()", src)


# ===========================================================================
# SS-H01 — Scene voice None leak + None-state destruction
# ===========================================================================

class TestSSH01SceneVoiceNone(unittest.TestCase, _MWHarness):
    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def _setup_scenes(self):
        from engine.models import Scene
        ref = _make_wav(os.path.join(self.tmpdir, "ref.wav"))
        vp = self.engine.create_voice("VoiceA", ref)
        self.win._control_panel.populate_voices(self.engine.list_voices())
        proj = self.win._active_project
        a = Scene(name="A", project_id=proj.id)
        b = Scene(name="B", project_id=proj.id)
        a.voice_profile_id = vp.id
        b.voice_profile_id = None
        proj.scenes.extend([a, b])
        return vp, a, b

    def test_none_voice_scene_shows_no_voice(self):
        vp, a, b = self._setup_scenes()
        self.win._switch_to_scene(a.id)
        self.assertEqual(self.win._control_panel.get_selected_voice_id(), vp.id)
        self.win._switch_to_scene(b.id)
        self.assertIsNone(self.win._control_panel.get_selected_voice_id(),
                          "Scene B (voice=None) must select (No voice), not "
                          "inherit Scene A's voice")

    def test_none_voice_state_survives_save(self):
        vp, a, b = self._setup_scenes()
        self.win._switch_to_scene(a.id)
        self.win._switch_to_scene(b.id)
        self.win._save_current_scene_state()
        self.assertIsNone(b.voice_profile_id,
                          "Saving while Scene B is active must not write "
                          "Scene A's voice into B")

    def test_round_trip_a_b_a_preserves_both(self):
        vp, a, b = self._setup_scenes()
        self.win._switch_to_scene(a.id)
        self.win._switch_to_scene(b.id)
        self.win._switch_to_scene(a.id)
        self.assertEqual(self.win._control_panel.get_selected_voice_id(),
                         vp.id, "Scene A's voice must survive A→B→A")
        self.assertEqual(a.voice_profile_id, vp.id)


# ===========================================================================
# SS-H02 — block-scoped overrides must not clobber the scene global
# ===========================================================================

class TestSSH02BlockOverrideScope(unittest.TestCase, _MWHarness):
    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def _select_block(self):
        # P3.26 UPDATE (documented per task Part 24): the old helper forged
        # the block scope from a plain-mode editor (a phantom state that
        # cannot occur through the real UI and IS the P3.26 "global
        # emotion does nothing" defect). Since P3.26 the scope is valid
        # only in Narration Blocks mode — the test now selects a REAL
        # PromptBlock through the real mode path, which is the approved
        # block-scoped-edit architecture (SS-H02) under the corrected
        # contract.
        from engine.narration_blocks import PromptBlock
        blk = PromptBlock(id="b1", start_offset=0,
                          end_offset=max(1, len(self.win._editor.get_text())))
        self.win._editor.block_manager._blocks = [blk]
        self.win._editor._selected_block_id = "b1"
        self.win._editor._plain_btn.setChecked(False)
        self.win._editor._blocks_btn.setChecked(True)  # real BLOCKS mode
        self.win._control_panel._active_block_id = "b1"
        return blk

    def test_emotion_edit_with_block_leaves_global_untouched(self):
        self.win._emotion = "Awe"
        blk = self._select_block()
        self.win._on_emotion_changed("Sad")
        self.assertEqual(self.win._emotion, "Awe",
                         "global emotion was clobbered by a block edit")
        self.assertEqual(blk.emotion, "Sad",
                         "the block override must be written")

    def test_all_five_overrides_leave_globals_untouched(self):
        cases = [
            ("emotion", "Awe", "Sad", "_on_emotion_changed"),
            ("style", "Whispering", "Singing", "_on_style_changed"),
            ("speed", "Normal", "Slow", "_on_speed_changed"),
            ("pitch", "Normal", "Low", "_on_pitch_changed"),
            ("delivery", "Normal", "Expressive High", "_on_delivery_changed"),
        ]
        for prop, before, after, handler in cases:
            setattr(self.win, "_" + prop if prop != "delivery" else "_delivery", before)
            self.win._emotion = self.win._emotion  # no-op
            blk = self._select_block()
            getattr(self.win, handler)(after)
            global_after = getattr(self.win, "_" + prop)
            self.assertEqual(global_after, before,
                             "global %s clobbered (%r != %r)"
                             % (prop, global_after, before))
            self.assertEqual(getattr(blk, prop), after,
                             "block %s override not written" % prop)

    def test_global_written_when_no_block_selected(self):
        self.win._control_panel._active_block_id = None
        self.win._editor.block_manager._blocks = []
        self.win._emotion = "Awe"
        self.win._on_emotion_changed("Sad")
        self.assertEqual(self.win._emotion, "Sad",
                         "without a selected block the global MUST change")


# ===========================================================================
# SS-H03 — Scene.parameters None contamination
# ===========================================================================

class TestSSH03SceneParamsNone(unittest.TestCase, _MWHarness):
    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_none_params_reset_panel_to_defaults(self):
        from engine.models import Scene, GenerationParameters
        proj = self.win._active_project
        a = Scene(name="A", project_id=proj.id)
        a.parameters = GenerationParameters(temperature=0.777)
        b = Scene(name="B", project_id=proj.id)
        b.parameters = None
        proj.scenes.extend([a, b])
        self.win._switch_to_scene(a.id)
        self.assertAlmostEqual(
            self.win._control_panel.get_parameters().temperature, 0.777,
            places=1)
        self.win._switch_to_scene(b.id)
        got = self.win._control_panel.get_parameters().temperature
        self.assertGreater(abs(got - 0.777), 0.01,
                           "Scene B (params=None) inherited A's temperature "
                           "(got %r)" % got)
        self.assertAlmostEqual(got, GenerationParameters().temperature,
                               delta=0.01,
                               msg="Scene B must reset to factory defaults")

    def test_none_params_not_contaminated_on_save(self):
        from engine.models import Scene, GenerationParameters
        proj = self.win._active_project
        a = Scene(name="A2", project_id=proj.id)
        a.parameters = GenerationParameters(temperature=0.777)
        b = Scene(name="B2", project_id=proj.id)
        b.parameters = None
        proj.scenes.extend([a, b])
        self.win._switch_to_scene(a.id)
        self.win._switch_to_scene(b.id)
        self.win._save_current_scene_state()
        self.assertIsNotNone(b.parameters,
                             "params should now hold the panel's defaults")
        self.assertGreater(abs(b.parameters.temperature - 0.777), 0.01,
                           "A's temperature was persisted into B")


# ===========================================================================
# SS-H04 — settings architecture: single owner, coherent, atomic
# ===========================================================================

class TestSSH04SettingsArchitecture(unittest.TestCase):
    def test_independent_instances_cross_coherent(self):
        """Two directly-constructed instances (as tests do) never serve
        stale values: after sm2 writes, sm1 READS the new value."""
        from engine.settings_manager import SettingsManager
        tmp = tempfile.mkdtemp(prefix="ss_h04a_")
        try:
            sm1 = SettingsManager(tmp)
            sm2 = SettingsManager(tmp)
            sm1.set("application", "theme", "light")
            sm2.set("window", "width", 999)
            self.assertEqual(sm1.get("window", "width"), 999,
                             "stale read: sm1 did not see sm2's write")
            self.assertEqual(sm2.get("application", "theme"), "light",
                             "write revert: sm2 clobbered sm1's theme")
            disk = json.load(open(os.path.join(tmp, "settings.json")))
            self.assertEqual(disk["window"]["width"], 999)
            self.assertEqual(disk["application"]["theme"], "light")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_instance_registry_returns_shared_object(self):
        """ONE authoritative instance per settings dir per process."""
        from engine.settings_manager import SettingsManager
        tmp = tempfile.mkdtemp(prefix="ss_h04b_")
        try:
            a = SettingsManager.instance(tmp)
            b = SettingsManager.instance(tmp)
            self.assertIs(a, b)
            self.assertIs(SettingsManager.instance(tmp), a)
        finally:
            # Clear the registry entry so other tests are not affected.
            SettingsManager._instances.pop(os.path.abspath(tmp), None)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_engine_and_mainwindow_share_one_manager(self):
        """RUNTIME: the real Engine and the real MainWindow hold the SAME
        SettingsManager object (the old split-brain architecture had three
        private instances)."""
        from engine.engine import Engine
        from ui.main_window import MainWindow
        tmp = tempfile.mkdtemp(prefix="ss_h04c_")
        patcher = None
        win = None
        try:
            eng = Engine(app_root=tmp)
            patcher = patch.object(
                type(eng._model), "is_loaded",
                new_callable=PropertyMock, return_value=True)
            patcher.start()
            win = MainWindow(eng)
            self.assertIs(eng._settings, win._settings_manager,
                          "Engine and MainWindow must share ONE "
                          "SettingsManager instance")
        finally:
            if patcher:
                patcher.stop()
            if win:
                win.close()
            from engine.settings_manager import SettingsManager
            SettingsManager._instances.pop(
                os.path.join(os.path.abspath(tmp), "settings"), None)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_writes_are_atomic_no_tmp_leftovers(self):
        from engine.settings_manager import SettingsManager
        tmp = tempfile.mkdtemp(prefix="ss_h04d_")
        try:
            sm = SettingsManager(tmp)
            for i in range(5):
                sm.set("application", "counter", i)
            leftovers = [f for f in os.listdir(tmp)
                         if f.startswith("settings.json.tmp-")]
            self.assertEqual(leftovers, [],
                             "atomic write left temp files behind")
            # File is valid JSON after repeated atomic writes.
            data = json.load(open(os.path.join(tmp, "settings.json")))
            self.assertEqual(data["application"]["counter"], 4)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_batch_dialog_writes_through_settings_manager(self):
        """SS-H04: the batch dialog must not be a raw fourth writer."""
        src = open(os.path.join(_ROOT, "ui", "panels",
                                "batch_generation.py")).read()
        self.assertNotIn(
            'open(settings_path, "w"', src,
            "raw independent settings writer still present")

    def test_theme_survives_restart_cycle(self):
        """Theme + geometry coherence across a full 'restart' (two
        sequential Engine instances on the same app root)."""
        from engine.engine import Engine
        tmp = tempfile.mkdtemp(prefix="ss_h04e_")
        try:
            e1 = Engine(app_root=tmp)
            e1._settings.set("application", "theme", "synthwave")
            e1._settings.set("window", "width", 1234)
            # "Restart": a fresh Engine reads the same file.
            e2 = Engine(app_root=tmp)
            self.assertEqual(e2._settings.get("application", "theme"),
                             "synthwave")
            self.assertEqual(e2._settings.get("window", "width"), 1234)
        finally:
            from engine.settings_manager import SettingsManager
            SettingsManager._instances.pop(
                os.path.join(os.path.abspath(tmp), "settings"), None)
            shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# SS-H05 — Raw Mode preview == model input
# ===========================================================================

class TestSSH05RawModePreview(unittest.TestCase, _MWHarness):
    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_preview_shows_literal_raw_text(self):
        raw_text = "<|emotion:fear|>Stay in the light."
        self.win._editor.set_text(raw_text)
        self.win._editor._raw_mode_cb.setChecked(True)
        self.win._editor.set_global_defaults(emotion="Elation",
                                              style="Whispering")
        self.win._editor._mode = self.win._editor.MODE_PREVIEW
        self.win._editor._update_preview()
        shown = self.win._editor._preview.toPlainText()
        self.assertEqual(shown, raw_text,
                         "Raw Mode preview must equal the literal editor "
                         "text (no injected tokens)")
        self.assertEqual(self.win._build_prompt(), raw_text,
                         "model input must equal the literal editor text")
        self.assertEqual(shown, self.win._build_prompt(),
                         "preview != model input in Raw Mode")

    def test_no_injection_with_tokens_in_text(self):
        self.win._editor.set_text("<|style:singing|>La la {sfx:Laughter:Haha}")
        self.win._editor._raw_mode_cb.setChecked(True)
        self.win._editor._mode = self.win._editor.MODE_PREVIEW
        self.win._editor._update_preview()
        shown = self.win._editor._preview.toPlainText()
        self.assertIn("<|style:singing|>", shown)
        self.assertIn("{sfx:Laughter:Haha}", shown,
                      "Raw Mode preview must not convert markers")


# ===========================================================================
# SS-H06 + M01 + M02 — Allow SFX end-to-end
# ===========================================================================

class TestSSH06AllowSfxEndToEnd(unittest.TestCase, _MWHarness):
    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def _toggle_off(self):
        rp = self.win._control_panel
        rp._friendly._allow_sfx_btn.setChecked(False)
        rp._friendly._on_allow_sfx_toggled()  # what a real click triggers

    def test_rightpanel_params_reflect_toggle(self):
        self._toggle_off()
        self.assertFalse(self.win._control_panel.get_parameters().allow_sfx,
                         "RightPanel.get_parameters() dropped the OFF "
                         "toggle (the SS-H06 seam)")

    def test_sfx_token_suppressed_in_prompt(self):
        self.win._editor._raw_mode_cb.setChecked(False)
        self.win._editor.set_text("He laughed {sfx:Laughter:Haha} and left.")
        self._toggle_off()
        prompt = self.win._build_prompt()
        self.assertNotIn("<|sfx:", prompt,
                         "SFX token reached the prompt with Allow SFX OFF")

    def test_sfx_token_present_when_on(self):
        # Explicitly ensure ON — this test must not depend on the state
        # left by test_sfx_token_suppressed_in_prompt (no order deps).
        rp = self.win._control_panel
        rp._friendly._allow_sfx_btn.setChecked(True)
        rp._friendly._on_allow_sfx_toggled()
        self.win._editor._raw_mode_cb.setChecked(False)
        self.win._editor.set_text("He laughed {sfx:Laughter:Haha} and left.")
        prompt = self.win._build_prompt()
        self.assertIn("<|sfx:laughter|>", prompt)

    def test_toggle_survives_tab_switch(self):
        """SS-M01: the advanced→friendly sync must not reset the toggle."""
        rp = self.win._control_panel
        self._toggle_off()
        rp._sync_advanced_to_friendly()
        self.assertFalse(rp._friendly._allow_sfx,
                         "toggle was reset by the tab sync (SS-M01)")

    def test_generate_long_job_params_forward_allow_sfx(self):
        """SS-M02: BatchJob parameters carry the panel's allow_sfx."""
        from engine.models import GenerationParameters
        base = GenerationParameters(allow_sfx=False)
        # Capture the BatchJob parameters by stubbing the batch manager.
        captured = []
        bm = self.win._batch_manager
        orig_add = bm.add_job

        def spy_add(job, *a, **k):
            captured.append(job)
            return orig_add(job, *a, **k)

        bm.add_job = spy_add
        try:
            part = type("P", (), {"speaker": None, "character_id": None,
                                  "prompt": "x", "text": "x"})()
            self.win._start_long_narration([part], None, base, {})
        except Exception:
            pass  # engine may refuse without a model; params captured first
        finally:
            bm.add_job = orig_add
        self.assertTrue(captured, "no BatchJob was created")
        self.assertFalse(captured[0].parameters.allow_sfx,
                          "Generate Long dropped allow_sfx=False (SS-M02)")

    def test_long_dialog_rebuild_respects_allow_sfx(self):
        """SS-M02: editing a part in the Long Narration dialog must not
        resurrect suppressed SFX tokens."""
        from engine.narration_splitter import SplitPart
        from ui.panels.long_narration_dialog import LongNarrationDialog
        part = SplitPart(text="He laughed {sfx:Laughter:Haha}",
                         prompt="He laughed {sfx:Laughter:Haha}")
        dlg = LongNarrationDialog([part], "", [], None, None,
                                  allow_sfx=False, parent=self.win)
        dlg._editors[0].setPlainText("He laughed {sfx:Laughter:Haha}")
        dlg._on_generate()
        self.assertNotIn("<|sfx:", part.prompt,
                         "the dialog rebuild re-injected an SFX token")


# ===========================================================================
# SS-H07 — invalid semantic values: ONE policy (reject + surfaced warning)
# ===========================================================================

class TestSSH07InvalidValues(unittest.TestCase):
    def test_block_invalid_emotion_no_garbage_token(self):
        from engine.prompt_state import CanonicalPromptCompiler
        from engine.narration_blocks import PromptBlock
        blk = PromptBlock(start_offset=0, end_offset=11)
        blk.emotion = "Bogus"
        pd = CanonicalPromptCompiler.compile_continuous(
            text="Hello world", blocks=[blk])
        self.assertNotIn("<|emotion:bogus|>", pd.final_prompt,
                         "garbage token reached the compiled prompt")
        self.assertNotIn("<|emotion:", pd.final_prompt)
        self.assertTrue(pd.warnings,
                        "the rejection must produce a warning")

    def test_global_invalid_emotion_no_token_with_warning(self):
        from engine.prompt_state import CanonicalPromptCompiler
        pd = CanonicalPromptCompiler.compile_for_generate(
            text="Hello world", global_emotion="Bogus")
        self.assertNotIn("<|emotion:", pd.final_prompt)
        self.assertTrue(pd.warnings,
                        "silent tokenless fallback is not allowed — a "
                        "warning must be returned")

    def test_invalid_matrix_same_policy_everywhere(self):
        from engine.prompt_state import CanonicalPromptCompiler
        from engine.narration_blocks import PromptBlock
        for value in ("Bogus", "", "N/A", "emotion:fear", 12345):
            # Block path
            blk = PromptBlock(start_offset=0, end_offset=5)
            blk.emotion = value if isinstance(value, str) else value
            pd_block = CanonicalPromptCompiler.compile_continuous(
                text="Hello", blocks=[blk])
            self.assertNotIn("<|emotion:", pd_block.final_prompt,
                             "block path emitted a token for %r" % value)
            # Global path
            pd_global = CanonicalPromptCompiler.compile_for_generate(
                text="Hello", global_emotion=value)
            self.assertNotIn("<|emotion:", pd_global.final_prompt,
                             "global path emitted a token for %r" % value)

    def test_mainwindow_surfaces_compile_warnings(self):
        """RUNTIME: the MainWindow records and forwards compiler warnings
        (previously discarded with a literal [])."""
        from engine.engine import Engine
        from ui.main_window import MainWindow
        tmp = tempfile.mkdtemp(prefix="ss_h07_")
        patcher = None
        win = None
        try:
            eng = Engine(app_root=tmp)
            patcher = patch.object(
                type(eng._model), "is_loaded",
                new_callable=PropertyMock, return_value=True)
            patcher.start()
            win = MainWindow(eng)
            win._emotion = "Bogus"
            win._editor.set_text("Hello world")
            win._editor._raw_mode_cb.setChecked(False)
            win._update_prompt_preview()
            self.assertTrue(getattr(win, "_last_prompt_warnings", []),
                            "compiler warning was not collected")
        finally:
            if patcher:
                patcher.stop()
            if win:
                win.close()
            shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# SS-H08 — path traversal / import-export exfiltration
# ===========================================================================

class TestSSH08PathTraversal(unittest.TestCase):
    def _craft(self, base, payload_a, payload_b=None):
        exp = os.path.join(base, "evil_export")
        os.makedirs(os.path.join(exp, "scenes"), exist_ok=True)
        scenes = [{
            "id": "s1", "name": "S1", "project_id": "evil", "text": "x",
            "status": "draft", "sort_order": 1,
            "audio_assets": [{"id": "a1", "output_path": payload_a}],
        }]
        if payload_b:
            scenes.append({
                "id": "s2", "name": "S2", "project_id": "evil", "text": "y",
                "status": "draft", "sort_order": 2,
                "audio_assets": [{"id": "a2", "output_path": payload_b}],
            })
        proj = {"id": "evil", "name": "Evil", "status": "draft",
                "characters": [], "scenes": scenes}
        with open(os.path.join(exp, "project.json"), "w") as f:
            json.dump(proj, f)
        return exp

    def test_import_then_reexport_cannot_exfiltrate(self):
        """RUNTIME: the exact audit scenario — crafted project with a
        traversal output_path → import → re-export(include_audio) must
        NOT copy the secret into the bundle."""
        from engine.project_manager import ProjectManager
        from engine.project_exporter import ProjectExporter
        base = tempfile.mkdtemp(prefix="ss_h08a_")
        try:
            app_root = os.path.join(base, "app")
            os.makedirs(app_root)
            secret_dir = os.path.join(base, "outside")
            os.makedirs(secret_dir)
            secret = os.path.join(secret_dir, "SECRET.bin")
            with open(secret, "wb") as f:
                f.write(b"CLASSIFIED-EXFIL-" + os.urandom(8))
            exp = self._craft(base, "../outside/SECRET.bin",
                              os.path.join(base, "outside", "SECRET.bin"))
            pm = ProjectManager(os.path.join(app_root, "projects"))
            imported = pm.import_project_dir(exp)
            self.assertIsNotNone(imported)
            # The import SANITIZED both payloads.
            self.assertEqual(
                imported.scenes[0].audio_assets[0]["output_path"], "")
            self.assertEqual(
                imported.scenes[1].audio_assets[0]["output_path"], "")
            pe = ProjectExporter(app_root)
            res = pe.export(imported, dest_parent=os.path.join(base, "out"),
                            include_audio=True, overwrite=True)
            leaked = []
            for root, _d, files in os.walk(os.path.join(base, "out")):
                for fn in files:
                    try:
                        with open(os.path.join(root, fn), "rb") as f:
                            if f.read(4) == b"CLAS":
                                leaked.append(fn)
                    except Exception:
                        pass
            self.assertEqual(leaked, [], "secret exfiltrated: %s" % leaked)
            self.assertEqual(res.audio_files_copied, 0)
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_containment_matrix(self):
        from engine.project_exporter import _contained_source_path
        base = tempfile.mkdtemp(prefix="ss_h08b_")
        try:
            app_root = os.path.join(base, "app")
            os.makedirs(os.path.join(app_root, "outputs"))
            legit = os.path.join(app_root, "outputs", "ok.wav")
            open(legit, "wb").write(b"RIFF")
            self.assertIsNone(_contained_source_path(app_root, "../x.wav"))
            self.assertIsNone(_contained_source_path(
                app_root, "outputs/../../x.wav"))
            self.assertIsNone(_contained_source_path(
                app_root, "/etc/hostname"))
            self.assertIsNone(_contained_source_path(
                app_root, "..\\outside\\SECRET.bin"))
            self.assertIsNone(_contained_source_path(app_root, "out\x00.wav"))
            self.assertIsNone(_contained_source_path(app_root, ""))
            self.assertEqual(_contained_source_path(app_root, "outputs/ok.wav"),
                             os.path.realpath(legit))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_exporter_rejects_unsafe_paths_directly(self):
        """Defence in depth: even WITHOUT import sanitization, the export
        copy itself refuses escaping paths (a project built in memory)."""
        from engine.project_manager import ProjectManager
        from engine.project_exporter import ProjectExporter
        from engine.models import Project, Scene
        base = tempfile.mkdtemp(prefix="ss_h08c_")
        try:
            app_root = os.path.join(base, "app")
            os.makedirs(app_root)
            secret = os.path.join(base, "SECRET.bin")
            with open(secret, "wb") as f:
                f.write(b"CLASSIFIED-DIRECT")
            p = Project(id="x", name="X")
            s = Scene(id="s1", name="S", project_id="x", text="t")
            s.audio_assets = [{"id": "a1", "output_path": "../SECRET.bin"}]
            p.scenes.append(s)
            pe = ProjectExporter(app_root)
            res = pe.export(p, dest_parent=base, include_audio=True,
                            overwrite=True)
            self.assertEqual(res.audio_files_copied, 0)
            leaked = []
            for root, _d, files in os.walk(res.export_dir):
                for fn in files:
                    try:
                        with open(os.path.join(root, fn), "rb") as f:
                            if f.read(4) == b"CLAS":
                                leaked.append(fn)
                    except Exception:
                        pass
            self.assertEqual(leaked, [])
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_history_delete_containment_still_holds(self):
        """SS-M04 (verified-fixed re-check): traversal entry ids and
        out-of-root audio paths are refused by HistoryManager.delete."""
        from engine.history_manager import HistoryManager
        base = tempfile.mkdtemp(prefix="ss_h08d_")
        try:
            # REAL layout: <app_root>/settings/history — the manager
            # derives the app root as TWO levels above the history dir.
            hm = HistoryManager(os.path.join(base, "app", "settings",
                                             "history"))
            # The victim lives OUTSIDE the app root (base/app).
            outside = os.path.join(base, "outside")
            os.makedirs(outside)
            victim = os.path.join(outside, "victim.txt")
            with open(victim, "w") as f:
                f.write("v")
            self.assertFalse(hm.delete("../../victim", delete_audio=True))
            self.assertTrue(os.path.exists(victim),
                            "traversal id deleted a file outside the "
                            "history dir")
            # An absolute out-of-root output_path must not delete audio.
            with open(os.path.join(base, "app", "settings", "history",
                                   "e1.json"), "w") as f:
                json.dump({"id": "e1", "output_path": victim}, f)
            hm.delete("e1", delete_audio=True)
            self.assertTrue(os.path.exists(victim),
                            "absolute out-of-root output_path was deleted")
        finally:
            shutil.rmtree(base, ignore_errors=True)


# ===========================================================================
# SS-H09 — corrupt settings startup handling
# ===========================================================================

class TestSSH09CorruptSettings(unittest.TestCase):
    def _sm(self, content, mode="w"):
        tmp = tempfile.mkdtemp(prefix="ss_h09_")
        path = os.path.join(tmp, "settings.json")
        with open(path, mode) as f:
            f.write(content)
        return tmp, path

    def test_malformed_matrix_never_crashes(self):
        from engine.settings_manager import SettingsManager
        cases = [
            ("[]", "w"), ("'string'", "w"), ("42", "w"), ("null", "w"),
            ("{broken", "w"),
            ('{"application": "theme"}'.encode("utf-16"), "wb"),
        ]
        for content, mode in cases:
            tmp, _path = self._sm(content, mode)
            try:
                sm = SettingsManager(tmp)  # must not raise
                self.assertIsInstance(sm.get("application", "theme"),
                                      (str, type(None)))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def test_corrupt_file_quarantined_and_self_heals(self):
        from engine.settings_manager import SettingsManager
        tmp, path = self._sm("[]")
        try:
            sm = SettingsManager(tmp)
            self.assertEqual(sm.get("application", "theme"),
                             "modern-dark")  # defaults
            quarantined = [f for f in os.listdir(tmp)
                           if f.startswith("settings.json.corrupt-")]
            self.assertTrue(quarantined,
                            "corrupt file was not quarantined")
            self.assertFalse(os.path.exists(path) and
                             open(path).read() == "[]")
            # Self-heal: a save writes a clean file.
            sm.set("application", "theme", "light")
            data = json.load(open(path))
            self.assertEqual(data["application"]["theme"], "light")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_engine_boots_with_corrupt_settings(self):
        """RUNTIME: the real Engine constructs (app never bricks)."""
        from engine.engine import Engine
        tmp, _ = self._sm("[]")
        try:
            eng = Engine(app_root=tmp)  # must not raise
            self.assertIsNotNone(eng)
        finally:
            from engine.settings_manager import SettingsManager
            SettingsManager._instances.pop(
                os.path.join(os.path.abspath(tmp), "settings"), None)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_non_dict_section_get_returns_default(self):
        """SS-L14: {"window": "big"} must not raise in get()."""
        from engine.settings_manager import SettingsManager
        tmp, _ = self._sm(json.dumps({"window": "big"}))
        try:
            sm = SettingsManager(tmp)
            self.assertEqual(sm.get("window", "width", 1024), 1024)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# SS-H10 — history corruption robustness
# ===========================================================================

class TestSSH10HistoryRobustness(unittest.TestCase):
    def test_utf16_entry_does_not_poison_list(self):
        from engine.history_manager import HistoryManager
        base = tempfile.mkdtemp(prefix="ss_h10a_")
        try:
            hdir = os.path.join(base, "history")
            os.makedirs(hdir)
            with open(os.path.join(hdir, "20250101_000000.json"), "w") as f:
                json.dump({"id": "20250101_000000", "prompt": "ok"}, f)
            with open(os.path.join(hdir, "20250101_000001.json"), "wb") as f:
                f.write('{"id": "x"}'.encode("utf-16"))
            with open(os.path.join(hdir, "20250101_000002.json"), "w") as f:
                f.write("[]")  # wrong root type
            entries = HistoryManager(hdir).list_entries()
            self.assertEqual(len(entries), 1,
                             "valid entry was lost to corrupt siblings")
            self.assertEqual(entries[0].id, "20250101_000000")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_malformed_entry_properties_degrade_gracefully(self):
        from engine.history_manager import HistoryEntry
        # voice_profile missing id → None (was KeyError into dialogs)
        e = HistoryEntry({"voice_profile": {"name": "x"}})
        self.assertIsNone(e.voice_profile)
        self.assertEqual(e.voice_name, "No voice")
        # string duration → coerced float (was TypeError in the dialog)
        e2 = HistoryEntry({"output_duration": "12.5"})
        self.assertAlmostEqual(e2.output_duration, 12.5)
        e3 = HistoryEntry({"output_duration": "corrupt"})
        self.assertEqual(e3.output_duration, 0.0)
        # non-dict payload tolerated
        e4 = HistoryEntry(["not", "a", "dict"])
        self.assertEqual(e4.id, "")
        self.assertIsNone(e4.scene_id)

    def test_history_view_dialog_constructs_with_malformed_entries(self):
        """RUNTIME: the real HistoryViewDialog constructor tolerates a
        malformed entry (previously KeyError/TypeError)."""
        from engine.history_manager import HistoryEntry
        from ui.panels.history_view import HistoryViewDialog
        entries = [
            HistoryEntry({"id": "ok1", "prompt": "p", "output_path": "",
                          "voice_profile": {"name": "no-id"},
                          "output_duration": "corrupt"}),
        ]
        dlg = HistoryViewDialog(entries, project=None, parent=None)
        dlg.close()


# ===========================================================================
# SS-H11 — voice import (verified-fixed re-check)
# ===========================================================================

class TestSSH11VoiceImport(unittest.TestCase):
    def test_avatar_metadata_duplicate_import(self):
        from engine.engine import Engine
        tmp = tempfile.mkdtemp(prefix="ss_h11_")
        try:
            eng = Engine(app_root=tmp)
            ref = _make_wav(os.path.join(tmp, "ref.wav"))
            vp = eng.create_voice("ImportProbe", ref)
            self.assertIsNotNone(vp)
            avatar = os.path.join(tmp, "avatar.png")
            with open(avatar, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
            eng.import_voice_avatar(vp.id, avatar)  # no AttributeError
            eng.set_voice_speaker_metadata(vp.id, gender="female",
                                            age_range="young", mood="warm")
            v = eng.get_voice(vp.id)
            self.assertEqual(v.gender, "female")
            self.assertEqual(v.age_range, "young")
            self.assertEqual(v.mood, "warm")
            self.assertTrue(v.preview_image,
                            "avatar was not recorded on the profile")
            # Duplicate import: same name again creates a NEW profile
            # (no half-created/duplicated broken state, ids differ).
            dup = eng.create_voice("ImportProbe", ref)
            self.assertNotEqual(dup.id, vp.id)
            # Re-import of the SAME id's avatar is idempotent on metadata.
            eng.set_voice_speaker_metadata(vp.id, gender="female",
                                            age_range="young", mood="warm")
            self.assertEqual(eng.get_voice(vp.id).gender, "female")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# SS-H12 / SS-H13 — keyboard shortcuts
# ===========================================================================

class TestSSH12H13Shortcuts(unittest.TestCase, _MWHarness):
    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_f11_toggles_fullscreen_both_ways(self):
        self.win.show()
        for _ in range(5):
            _app.processEvents()
        QTest.keyClick(self.win, Qt.Key.Key_F11)
        for _ in range(5):
            _app.processEvents()
        self.assertTrue(self.win.windowState()
                        & Qt.WindowState.WindowFullScreen)
        QTest.keyClick(self.win, Qt.Key.Key_F11)
        for _ in range(5):
            _app.processEvents()
        self.assertFalse(self.win.windowState()
                         & Qt.WindowState.WindowFullScreen)

    def test_esc_exits_fullscreen(self):
        self.win.show()
        for _ in range(5):
            _app.processEvents()
        QTest.keyClick(self.win, Qt.Key.Key_F11)
        for _ in range(5):
            _app.processEvents()
        self.assertTrue(self.win.windowState()
                        & Qt.WindowState.WindowFullScreen)
        QTest.keyClick(self.win, Qt.Key.Key_Escape)
        for _ in range(5):
            _app.processEvents()
        self.assertFalse(self.win.windowState()
                         & Qt.WindowState.WindowFullScreen,
                         "Esc must exit fullscreen (was dead)")

    def test_esc_closes_search_bar(self):
        self.win.show()
        for _ in range(5):
            _app.processEvents()
        bar = self.win._editor._search_bar
        if not bar.isVisible():
            self.win._editor._on_toggle_search()
        self.assertTrue(bar.isVisible())
        QTest.keyClick(self.win, Qt.Key.Key_Escape)
        for _ in range(5):
            _app.processEvents()
        self.assertFalse(bar.isVisible(), "Esc must close the search bar")

    def test_esc_stops_generation_when_generating(self):
        stopped = {"called": False}
        orig = self.win._on_stop
        self.win._on_stop = lambda: stopped.__setitem__("called", True)
        try:
            with patch.object(type(self.engine), "is_generating",
                              new_callable=PropertyMock,
                              return_value=True):
                QTest.keyClick(self.win, Qt.Key.Key_Escape)
                for _ in range(5):
                    _app.processEvents()
        finally:
            self.win._on_stop = orig
        self.assertTrue(stopped["called"],
                        "Esc must stop a running generation (top priority)")

    def test_no_ambiguous_esc_or_f11_registration(self):
        """Exactly ONE claimant per key: no QAction shortcut for Esc, the
        F11 QAction remains the single F11 registration."""
        act_stop = self.win._menu_bar.get_action("stop")
        self.assertEqual(act_stop.shortcut().toString(), "")
        act_fs = self.win._menu_bar.get_action("fullscreen")
        self.assertEqual(act_fs.shortcut().toString(), "F11")
        from PySide6.QtGui import QShortcut
        dupes = [s for s in self.win.findChildren(QShortcut)
                 if "Esc" in s.key().toString() or "F11" in s.key().toString()]
        self.assertEqual(dupes, [])


# ===========================================================================
# SS-H14 — settings dialog single instance (verified-fixed re-check)
# ===========================================================================

class TestSSH14SettingsDialog(unittest.TestCase):
    def test_one_dialog_per_trigger_and_rapid_double(self):
        import ui.panels.settings_dialog as sd
        from engine.engine import Engine
        from ui.main_window import MainWindow
        tmp = tempfile.mkdtemp(prefix="ss_h14_")
        patcher = None
        win = None
        try:
            eng = Engine(app_root=tmp)
            patcher = patch.object(
                type(eng._model), "is_loaded",
                new_callable=PropertyMock, return_value=True)
            patcher.start()
            win = MainWindow(eng)
            count = {"n": 0}

            class _Fake:
                def __init__(self, *a, **k):
                    count["n"] += 1
                    self.settings_changed = MagicMock()
                    self.settings_changed.connect = MagicMock()

                def exec(self):
                    return 0

            with patch.object(sd, "SettingsDialog", _Fake):
                win._menu_bar.get_action("settings").trigger()
                _app.processEvents()
                self.assertEqual(count["n"], 1)
                # Rapid double trigger (double click on the menu).
                win._menu_bar.get_action("settings").trigger()
                win._menu_bar.get_action("settings").trigger()
                _app.processEvents()
                self.assertEqual(count["n"], 3,
                                 "%d dialogs for 3 triggers" % count["n"])
        finally:
            if patcher:
                patcher.stop()
            if win:
                win.close()
            shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# SS-H15 — scene restore fault isolation
# ===========================================================================

class TestSSH15SceneRestoreFaults(unittest.TestCase, _MWHarness):
    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_one_failing_setter_does_not_skip_remaining_restores(self):
        from engine.models import Scene
        s = Scene(name="X", project_id=self.win._active_project.id)
        s.text = "text"
        s.emotion = "Awe"
        s.voice_profile_id = None
        self.win._active_project.scenes.append(s)
        orig_emotion = self.win._control_panel.set_emotion
        orig_voice = self.win._control_panel.set_selected_voice_id
        called = {"voice": False}

        def boom(*a, **k):
            raise RuntimeError("injected failure")

        def spy_voice(v):
            called["voice"] = True

        self.win._control_panel.set_emotion = boom
        self.win._control_panel.set_selected_voice_id = spy_voice
        try:
            self.win._load_scene_state(s)
        finally:
            self.win._control_panel.set_emotion = orig_emotion
            self.win._control_panel.set_selected_voice_id = orig_voice
        self.assertTrue(called["voice"],
                        "a failing set_emotion silently skipped the voice "
                        "restore (partial-restore state leak)")

    def test_block_persistence_failure_is_logged_not_silent(self):
        """The save path logs (ERROR) instead of `except: pass` — a raise
        in the block serialization must not vanish silently."""
        import logging
        s = self.win._active_scene
        orig = self.win._editor.block_manager.blocks

        class _Bad:
            def to_dict(self):
                raise RuntimeError("cannot serialize")

        self.win._editor.block_manager._blocks = [_Bad()]
        try:
            with self.assertLogs("speechstudio.ui.main_window",
                                 level="ERROR"):
                self.win._save_current_scene_state()
        finally:
            self.win._editor.block_manager._blocks = orig


# ===========================================================================
# Task §5 — Scene state integrity matrix (A → B → A)
# ===========================================================================

class TestSceneStateIntegrity(unittest.TestCase, _MWHarness):
    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_full_ab_matrix_no_leakage(self):
        from engine.models import Scene, GenerationParameters
        ref = _make_wav(os.path.join(self.tmpdir, "ref2.wav"))
        vp = self.engine.create_voice("MatrixVoice", ref)
        self.win._control_panel.populate_voices(self.engine.list_voices())
        proj = self.win._active_project
        a = Scene(name="A", project_id=proj.id)
        b = Scene(name="B", project_id=proj.id)
        a.voice_profile_id = vp.id
        a.emotion = "Awe"
        a.style = "Whispering"
        a.speed = "Slow"
        a.pitch = "Low"
        a.delivery = "Expressive High"
        a.parameters = GenerationParameters(temperature=1.555, top_p=0.7)
        a.text = "Scene A text"
        # B: no voice, no params, default semantics
        b.voice_profile_id = None
        b.parameters = None
        b.text = "Scene B text"
        proj.scenes.extend([a, b])

        # A → B
        self.win._switch_to_scene(a.id)
        self.win._switch_to_scene(b.id)
        self.assertIsNone(self.win._control_panel.get_selected_voice_id(),
                          "B inherited A's voice")
        self.assertAlmostEqual(
            self.win._control_panel.get_parameters().temperature,
            GenerationParameters().temperature, places=2,
            msg="B inherited A's temperature")
        self.assertIsNone(self.win._emotion, "B inherited A's emotion")
        self.assertEqual(self.win._editor.get_text(), "Scene B text")

        # B → A: A remains unchanged
        self.win._switch_to_scene(a.id)
        self.assertEqual(self.win._control_panel.get_selected_voice_id(),
                         vp.id)
        self.assertAlmostEqual(
            self.win._control_panel.get_parameters().temperature, 1.555,
            places=2)
        self.assertEqual(self.win._emotion, "Awe")
        self.assertEqual(self.win._style, "Whispering")
        self.assertEqual(self.win._speed, "Slow")
        self.assertEqual(self.win._pitch, "Low")
        self.assertEqual(self.win._delivery, "Expressive High")
        self.assertEqual(self.win._editor.get_text(), "Scene A text")

        # Entities not silently cross-persisted. Saving while B was active
        # legitimately persists B's OWN panel state (defaults) — what must
        # never happen is A's values landing in B.
        self.assertIsNone(b.voice_profile_id,
                          "B's None-voice state was destroyed")
        if b.parameters is not None:
            self.assertGreater(
                abs(b.parameters.temperature - 1.555), 0.01,
                "A's temperature was persisted into B")
        self.assertEqual(a.voice_profile_id, vp.id)


# ===========================================================================
# MEDIUM defects fixed in P3.25
# ===========================================================================

class TestSSMDefects(unittest.TestCase):

    # SS-M03 -----------------------------------------------------------
    def test_m03_seed_none_clears_field(self):
        from engine.models import GenerationParameters
        from ui.panels.control_panel import GenerationSection
        gs = GenerationSection()
        gs.set_parameters(GenerationParameters(seed=42))
        self.assertEqual(gs.get_parameters().seed, 42)
        gs.set_parameters(GenerationParameters(seed=None))
        self.assertIsNone(gs.get_parameters().seed,
                          "stale seed survived set_parameters(seed=None)")

    # SS-M05 -----------------------------------------------------------
    def test_m05_same_second_history_entries_both_survive(self):
        from engine.history_manager import HistoryManager
        from engine.models import GenerationResult, GenerationParameters
        base = tempfile.mkdtemp(prefix="ss_m05_")
        try:
            hm = HistoryManager(os.path.join(base, "history"))

            def mk(ts):
                return GenerationResult(
                    success=True, parameters=GenerationParameters(),
                    prompt="p", output_path="/nowhere.wav", timestamp=ts)
            hm.add(mk("20250101_120000"))
            hm.add(mk("20250101_120000"))
            self.assertEqual(len(hm.list_entries()), 2,
                             "same-second entries collided")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_m05_same_second_audio_files_both_survive(self):
        from engine.audio_manager import AudioManager
        base = tempfile.mkdtemp(prefix="ss_m05b_")
        try:
            am = AudioManager(os.path.join(base, "outputs"))
            am._get_app_root = lambda: base
            p1 = am.save_wav([0.0, 0.1], 24000, filename=None)
            p2 = am.save_wav([0.0, 0.1], 24000, filename=None)
            self.assertNotEqual(p1, p2,
                                "same-second auto filenames collided")
            self.assertTrue(os.path.isfile(os.path.join(base, p1)))
            self.assertTrue(os.path.isfile(os.path.join(base, p2)))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    # SS-M06 -----------------------------------------------------------
    def test_m06_corrupt_voice_profile_tolerated(self):
        from engine.voice_manager import VoiceManager
        base = tempfile.mkdtemp(prefix="ss_m06_")
        try:
            vdir = os.path.join(base, "voices")
            # Correct structure: each profile is a DIRECTORY with
            # profile.json inside.
            os.makedirs(os.path.join(vdir, "v1"))
            with open(os.path.join(vdir, "v1", "profile.json"), "w") as f:
                json.dump({"id": "v1", "name": "V1"}, f)
            os.makedirs(os.path.join(vdir, "v2"))
            with open(os.path.join(vdir, "v2", "profile.json"), "wb") as f:
                f.write('{"id": "v2"}'.encode("utf-16"))
            os.makedirs(os.path.join(vdir, "v3"))
            with open(os.path.join(vdir, "v3", "profile.json"), "w") as f:
                f.write("[]")
            vm = VoiceManager(vdir)
            profiles = vm.list_profiles()  # must not raise
            self.assertEqual(len(profiles), 1,
                             "valid profile lost to corrupt siblings")
            self.assertEqual(profiles[0].id, "v1")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    # SS-M07 -----------------------------------------------------------
    def test_m07_malformed_project_json_rejected_gracefully(self):
        from engine.project_manager import ProjectManager
        base = tempfile.mkdtemp(prefix="ss_m07_")
        try:
            exp = os.path.join(base, "exp")
            os.makedirs(exp)
            with open(os.path.join(exp, "project.json"), "w") as f:
                json.dump({"id": "x", "name": "X", "scenes": "hello"}, f)
            pm = ProjectManager(os.path.join(base, "projects"))
            result = pm.import_project_dir(exp)  # must not raise
            self.assertIsNone(result)
        finally:
            shutil.rmtree(base, ignore_errors=True)

    # SS-M08 -----------------------------------------------------------
    def test_m08_hostile_names_export_does_not_crash(self):
        from engine.project_exporter import ProjectExporter
        from engine.models import Project, Scene
        base = tempfile.mkdtemp(prefix="ss_m08_")
        try:
            pe = ProjectExporter(base)
            p = Project(id="x", name="Evil")
            p.scenes.append(Scene(id="s1", name="S" * 300,
                                  project_id="x", text="t"))
            res = pe.export(p, dest_parent=base, include_audio=False,
                            overwrite=True)
            self.assertIsNotNone(res)
            self.assertTrue(os.path.isdir(res.export_dir))
            # Null byte in the project name is stripped, not fatal.
            p2 = Project(id="y", name="Ev\x00il2")
            p2.scenes.append(Scene(id="s2", name="Ok", project_id="y",
                                   text="t"))
            res2 = pe.export(p2, dest_parent=base, include_audio=False,
                             overwrite=True)
            self.assertIsNotNone(res2)
        finally:
            shutil.rmtree(base, ignore_errors=True)

    # SS-M09 -----------------------------------------------------------
    def test_m09_scene_deletion_is_durable(self):
        from engine.engine import Engine
        from engine.models import Scene
        from ui.main_window import MainWindow
        from PySide6.QtWidgets import QMessageBox
        tmp = tempfile.mkdtemp(prefix="ss_m09_")
        patcher = None
        win = None
        try:
            eng = Engine(app_root=tmp)
            patcher = patch.object(
                type(eng._model), "is_loaded",
                new_callable=PropertyMock, return_value=True)
            patcher.start()
            win = MainWindow(eng)
            proj = win._active_project
            # Persist the project once so the scene exists on disk.
            win._project_manager.save_project(proj)
            s = Scene(name="Doomed", project_id=proj.id)
            proj.scenes.append(s)
            win._switch_to_scene(s.id)
            pid = proj.id
            with patch.object(QMessageBox, "question",
                              return_value=QMessageBox.StandardButton.Yes):
                win._on_delete_scene()
            reloaded = win._project_manager.get_project(pid)
            self.assertFalse(any(sc.id == s.id for sc in reloaded.scenes),
                             "deleted scene resurrected from disk")
        finally:
            if patcher:
                patcher.stop()
            if win:
                win.close()
            shutil.rmtree(tmp, ignore_errors=True)

    # SS-M10 -----------------------------------------------------------
    def test_m10_history_paths_resolve_against_app_root(self):
        from ui.panels.history_view import _resolve_history_path
        resolved = _resolve_history_path("outputs/x.wav")
        # _ROOT IS the application root (…/SpeechStudio). The resolved
        # path must be anchored there, NOT at the current working dir.
        self.assertTrue(resolved.startswith(_ROOT + os.sep),
                        "path not resolved against app root: %r" % resolved)
        self.assertTrue(resolved.endswith("outputs/x.wav"))
        # Absolute paths pass through unchanged.
        self.assertEqual(_resolve_history_path("/abs/x.wav"), "/abs/x.wav")
        # RUNTIME: a real file inside APP_ROOT is found from ANY cwd.
        marker = os.path.join(_ROOT, "engine", "__init__.py")
        rel = os.path.relpath(marker, _ROOT)
        cwd = os.getcwd()
        os.chdir("/")
        try:
            self.assertTrue(
                os.path.isfile(_resolve_history_path(rel)),
                "file inside app root not found from a different CWD")
        finally:
            os.chdir(cwd)

    # SS-M14 -----------------------------------------------------------
    def test_m14_model_events_reach_gui_thread(self):
        """RUNTIME: MODEL_LOADED published on a worker thread refreshes
        the model status ON THE GUI THREAD (the previous
        QTimer.singleShot(0) path never fired)."""
        from engine.engine import Engine
        from engine.events import Event, EventType
        from ui.main_window import MainWindow
        tmp = tempfile.mkdtemp(prefix="ss_m14_")
        patcher = None
        win = None
        try:
            eng = Engine(app_root=tmp)
            patcher = patch.object(
                type(eng._model), "is_loaded",
                new_callable=PropertyMock, return_value=True)
            patcher.start()
            win = MainWindow(eng)
            threads_seen = []
            orig = win._refresh_model_status

            def spy():
                threads_seen.append(threading.current_thread().name)
                try:
                    orig()
                except Exception:
                    pass

            win._refresh_model_status = spy
            t = threading.Thread(
                target=lambda: eng._event_bus.emit(
                    Event(EventType.MODEL_LOADED)),
                name="model-worker")
            t.start()
            t.join()
            for _ in range(20):
                _app.processEvents()
            self.assertEqual(threads_seen, ["MainThread"],
                             "model status refresh did not run on the GUI "
                             "thread: %s" % threads_seen)
        finally:
            if patcher:
                patcher.stop()
            if win:
                win.close()
            shutil.rmtree(tmp, ignore_errors=True)

    # SS-M18 -----------------------------------------------------------
    def test_m18_from_dict_defaults_match_dataclass(self):
        from engine.models import GenerationParameters
        d = GenerationParameters.from_dict({})
        ref = GenerationParameters()
        self.assertEqual(d.temperature, ref.temperature)
        self.assertEqual(d.top_p, ref.top_p)
        self.assertEqual(d.top_k, ref.top_k)
        self.assertEqual(d.max_new_tokens, ref.max_new_tokens)
        self.assertEqual(d.append_silence, ref.append_silence)
        self.assertEqual(d.allow_sfx, ref.allow_sfx)

    # E2E-found path consistency ----------------------------------------
    def test_generation_reference_audio_resolves_against_engine_app_root(self):
        """E2E-found defect: GenerationManager._load_reference_audio
        resolved the reference path against the MODULE directory instead
        of the Engine's app root — with Engine(app_root=<isolated dir>)
        a VALID imported reference looked 'missing from disk' and blocked
        generation. Resolution must follow the engine's app root."""
        from engine.engine import Engine
        tmp = tempfile.mkdtemp(prefix="ss_pathfix_")
        try:
            eng = Engine(app_root=tmp)
            ref = _make_wav(os.path.join(tmp, "ref.wav"))
            v = eng.create_voice("PathVoice")
            eng.import_voice_reference(v.id, ref)
            v = eng.get_voice(v.id)
            # The engine-level validation must pass with the isolated
            # app root (previously: 'missing from disk').
            warnings = eng._voices.validate(v.id)
            self.assertFalse(
                any("missing" in w.lower() for w in warnings),
                "valid reference reported missing: %s" % warnings)
            # And the generation-manager-side resolution finds the file.
            gm = eng._generation
            resolved = os.path.join(
                gm._audio_manager._get_app_root(), v.reference_audio_path)
            self.assertTrue(os.path.isfile(resolved),
                            "generation path resolution missed the file: %s"
                            % resolved)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
