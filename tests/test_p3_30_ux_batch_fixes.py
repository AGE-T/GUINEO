"""
SpeechStudio — P3.30 UX/BATCH regression tests
==============================================

One runtime test class per user-reported defect (all fixes P3.30):

  (a) Plain Text mode must show NO narration-block visuals — a scene
      switch into a scene saved 'plain' (with blocks) previously left
      the block gutter painted because the mode radio no-op'd.
  (b/c) Export Audio / version-selection / Choose… menus are ANCHORED
      above their button (previously QMenu.exec() with no position —
      frameless top-left popup on Windows).
  (d1) Cooperative cancellation: a generation cancelled at a safe
      checkpoint (before the model call) returns cancelled=True, the
      model is never called, and the NEXT submission is unaffected
      (the stale-cancel leak).
  (d2) An orphan job completion (no _current_index) FINISHES the batch
      instead of leaving it stuck "running" forever.
  (d3) A submit_fn/to_request failure of ANY exception type marks the
      job FAILED and the queue CONTINUES (both stuck-forever paths).
  (d4) Batch stop semantics: pending+generating flip to SKIPPED at
      once; a job that was already inside the model call and finishes
      with audio is RESTORED to COMPLETED; a cooperatively cancelled
      result renders as SKIPPED (user stop), never FAILED; the Stop
      button shows "Stopping…" while the batch runs out.
  (e) The P3.28 buttons are readable (28px min height / 12px padding).
  (f) The stale-audio confirmation uses the user-approved copy with
      Cancel / Use Anyway buttons.
  (g) All-combined-stale Scenes require review — never a silent
      single-part fallback (covered further in test_p3_28 updates).

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_30_ux_batch_fixes.py -v
"""
import os
import sys
import time
import shutil
import tempfile
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Fake torch (house pattern: test_p3_27b / test_p3_28) — the model
# boundary is faked anyway; the engine only needs torch.no_grad.
fake_torch = types.ModuleType("torch")


class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


fake_torch.no_grad = lambda: _NoGrad()
fake_torch.manual_seed = lambda s: None
fake_torch.from_numpy = lambda arr: arr
fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)
sys.modules.setdefault("torch", fake_torch)

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from engine.models import (  # noqa: E402
    Project, Scene, GenerationParameters, GenerationResult,
)

APP = QApplication.instance() or QApplication(sys.argv)


def _wait_batch(bm, timeout=8.0):
    t0 = time.time()
    while bm.is_running and time.time() - t0 < timeout:
        _process(30)
    _process(200)


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


# ---------------------------------------------------------------------------
# Model-boundary fake (P3.27B house pattern)
# ---------------------------------------------------------------------------
def make_speech(seconds=1.0, sr=24000):
    import numpy as np
    return (np.random.uniform(-0.5, 0.5, int(sr * seconds))
            .astype("float32"))


class FakeHiggsModel:
    calls = []
    speech_s = 1.0
    gate = None          # optional (threading.Event,) block-until-set
    on_enter = None      # optional callable when the model call starts

    def generate_speech(self, text, tokenizer, *, reference_audio=None,
                        reference_sample_rate=None, reference_codes=None,
                        reference_text=None, max_new_tokens=2048,
                        temperature=1.0, top_p=None, top_k=None):
        FakeHiggsModel.calls.append(dict(text=text))
        if FakeHiggsModel.on_enter is not None:
            FakeHiggsModel.on_enter()
        if FakeHiggsModel.gate is not None:
            FakeHiggsModel.gate.wait(10.0)
        return make_speech(FakeHiggsModel.speech_s)


def _make_part(text="A test sentence for the part.", speaker=None,
               block_id=None, part_of_block=1, block_label="",
               character_id=None, est=8.0):
    from engine.narration_splitter import SplitPart
    return SplitPart(
        text=text, prompt=text, speaker=speaker or "",
        source_block_id=block_id, part_of_block=part_of_block,
        total_parts_in_block=1, block_label=block_label,
        character_id=character_id, estimated_duration=est,
        char_count=len(text))


# ===========================================================================
# (a) Plain Text mode shows no block visuals
# ===========================================================================
class TestPlainTextModeNoBlockVisuals(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch, PropertyMock
        from engine.engine import Engine
        from ui.main_window import MainWindow
        self.tmp = tempfile.mkdtemp(prefix="ss_p330_a_")
        self.engine = Engine(app_root=self.tmp)
        self.patchers = [
            patch.object(type(self.engine._model), "is_loaded",
                         new_callable=PropertyMock, return_value=True),
            patch.object(type(self.engine._model), "device",
                         new_callable=PropertyMock, return_value="cpu"),
            patch.object(type(self.engine._model),
                         "get_model_and_tokenizer",
                         return_value=(FakeHiggsModel(), None)),
        ]
        for p in self.patchers:
            p.start()
        self.win = MainWindow(self.engine)
        self.win._project_manager = type(
            "PM", (), {"__init__": lambda self: None,
                       "save_project": lambda self, p: None,
                       "list_projects": lambda self: []})()
        self.project = Project(name="P", id="p1")
        self.scene_a = Scene(id="sa", name="A", project_id="p1")
        self.scene_a.editor_mode = "plain"
        self.scene_a.text = "Scene A text."
        # Scene B is saved PLAIN but HAS blocks — the defect trigger.
        self.scene_b = Scene(id="sb", name="B", project_id="p1")
        self.scene_b.editor_mode = "plain"
        self.scene_b.text = "B1 text here.\n\nB2 text here."
        self.scene_b.narration_blocks = [
            {"id": "b1", "label": "B1", "start_offset": 0,
             "end_offset": 14, "locked": False},
            {"id": "b2", "label": "B2", "start_offset": 16,
             "end_offset": 30, "locked": False},
        ]
        self.project.add_scene(self.scene_a)
        self.project.add_scene(self.scene_b)
        self.win._active_project = self.project
        self.win._active_scene = self.scene_a
        self.win._current_project = "P"

    def tearDown(self):
        try:
            self.win.close()
        except Exception:
            pass
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plain_scene_switch_leaves_no_block_visuals(self):
        self.win._load_scene_state(self.scene_a)
        _process(50)
        editor = self.win._editor
        self.assertEqual(editor._mode, editor.MODE_PLAIN)
        self.win._switch_to_scene("sb")
        _process(100)
        self.assertEqual(editor._mode, editor.MODE_PLAIN,
                         "target scene was saved plain")
        self.assertEqual(len(editor._editor._blocks), 0,
                         "no block data may live on the editor in plain "
                         "mode (the gutter would paint it)")
        # And the BLOCK MODEL is intact — switching to blocks renders them.
        editor.set_editor_mode("blocks")
        _process(50)
        self.assertEqual(editor._mode, editor.MODE_BLOCKS)
        self.assertEqual(len(editor._editor._blocks), 2,
                         "blocks mode still renders the restored blocks")

    def test_set_editor_mode_idempotent_same_mode(self):
        """Calling set_editor_mode with the CURRENT mode must re-apply the
        mode contract (P3.30(a): the radio-button no-op hole)."""
        editor = self.win._editor
        editor.set_editor_mode("blocks")
        _process(30)
        editor._selected_block_id = "b1"
        editor.set_editor_mode("plain")
        editor.set_editor_mode("plain")   # the previously no-op case
        _process(30)
        self.assertEqual(editor._mode, editor.MODE_PLAIN)
        self.assertEqual(len(editor._editor._blocks), 0)
        self.assertIsNone(editor._selected_block_id,
                          "leaving blocks mode ends the block selection")


# ===========================================================================
# (b/c) Anchored menus
# ===========================================================================
class TestAnchoredMenus(unittest.TestCase):
    """The three previously-unanchored QMenu.exec() sites now route through
    _popup_menu_above(menu, btn) with the correct anchor button."""

    def test_export_menu_anchored_above_export_button(self):
        import ui.panels.batch_generation as bg
        from unittest.mock import patch
        captured = {}

        def fake_popup(menu, btn):
            captured["menu"] = menu
            captured["btn"] = btn
            return None

        with patch.object(bg, "_popup_menu_above", side_effect=fake_popup):
            dlg = _make_scene_dialog(self)
            try:
                dlg._on_export_audio()
                self.assertIs(captured.get("btn"), dlg._export_btn,
                              "Export Audio menu must anchor to the "
                              "EXPORT AUDIO button")
                actions = [a.text() for a in
                           captured["menu"].actions()]
                self.assertTrue(any("Selected rows" in t for t in actions))
            finally:
                dlg.close()

    def test_versions_menu_anchored_above_versions_button(self):
        import ui.panels.batch_generation as bg
        from unittest.mock import patch
        captured = {}

        def fake_popup(menu, btn):
            captured["menu"] = menu
            captured["btn"] = btn
            return None

        with patch.object(bg, "_popup_menu_above", side_effect=fake_popup):
            dlg = _make_scene_dialog(self)
            try:
                # Grow a second version for slot 1 so the ▾ button exists.
                scene = dlg._scene
                scene.audio_assets.append(dict(
                    scene.audio_assets[0], id="a1v2", part_version=2,
                    output_path="outputs/x_v02.wav"))
                dlg.refresh_scene_context()
                dlg._show_versions_menu("b1:1", dlg)
                # Without an explicit anchor the dialog itself is used —
                # the REAL click path always passes the versions button.
                self.assertIn("btn", captured)
                # With the button: anchored to it.
                versions_btn = _find_button_startswith(dlg, "\u00b7 ")
                if versions_btn is not None:
                    captured.clear()
                    dlg._show_versions_menu("b1:1", versions_btn)
                    self.assertIs(captured.get("btn"), versions_btn)
            finally:
                dlg.close()

    def test_choose_output_menu_anchored(self):
        import ui.panels.assemble_dialog as ad
        from unittest.mock import patch
        from ui.panels.assemble_dialog import _SceneCheckRow
        captured = {}

        def fake_popup(menu, btn):
            captured["menu"] = menu
            captured["btn"] = btn
            return None

        with patch.object(ad, "_popup_menu_above", side_effect=fake_popup):
            scene = _scene_with_combined(self)
            row = _SceneCheckRow(scene, "/tmp/c1.wav", 5.0, False,
                                 "Combined v01")
            self.addCleanup(row.deleteLater)
            choose_btn = None
            for btn in row.findChildren(QPushButton):
                if btn.text() == "Choose Output\u2026":
                    choose_btn = btn
                    break
            self.assertIsNotNone(choose_btn)
            row._choose_output(scene, choose_btn)
            self.assertIs(captured.get("btn"), choose_btn,
                          "the Choose Output… menu anchors to its button")
            self.assertIn("btn", captured)


def _find_button_startswith(dlg, prefix):
    for btn in dlg.findChildren(QPushButton):
        if btn.text().startswith(prefix):
            return btn
    return None


def _make_scene_dialog(test):
    """Build a REAL BatchGenerationDialog in scene mode with one
    completed part (fresh combined output present)."""
    from unittest.mock import patch, PropertyMock
    from engine.engine import Engine
    from ui.main_window import MainWindow
    from engine.models import Project, Scene

    tmp = tempfile.mkdtemp(prefix="ss_p330_dlg_")
    engine = Engine(app_root=tmp)
    patchers = [
        patch.object(type(engine._model), "is_loaded",
                     new_callable=PropertyMock, return_value=True),
        patch.object(type(engine._model), "device",
                     new_callable=PropertyMock, return_value="cpu"),
        patch.object(type(engine._model), "get_model_and_tokenizer",
                     return_value=(FakeHiggsModel(), None)),
    ]
    for p in patchers:
        p.start()
    win = MainWindow(engine)
    win._project_manager = type(
        "PM", (), {"__init__": lambda self: None,
                   "save_project": lambda self, p: None,
                   "list_projects": lambda self: []})()
    project = Project(name="Eden", id="proj1")
    scene = Scene(id="sc1abcd", name="Scene 03", project_id="proj1")
    project.add_scene(scene)
    win._active_project = project
    win._active_scene = scene
    win._current_project = "Eden"
    FakeHiggsModel.calls = []
    FakeHiggsModel.speech_s = 0.3

    from engine.audio_provenance import (
        materialize_expected_slots, register_generation_result,
        build_scene_combined_entry,
    )
    scene.expected_audio_slots = materialize_expected_slots(
        [_make_part(block_id="b1", block_label="B1")])
    result = GenerationResult(
        success=True, output_path="outputs/x.wav", output_duration=5.0,
        timestamp="20260101_000000")
    result.scene_id = scene.id
    result.block_id = "b1"
    result.part_index = 1
    result.part_version = 1
    asset = register_generation_result(project, result, scene_fallback=scene)
    scene.combined_outputs.append(build_scene_combined_entry(
        scene, version=1, output_path="outputs/c1.wav", duration=5.0,
        sources=[{"slot_id": "b1:1", "block_id": "b1", "block_label": "B1",
                  "part_index": 1, "asset_id": asset["id"],
                  "part_version": 1, "speaker": "", "character_id": None,
                  "output_path": "outputs/x.wav", "duration": 5.0}],
        silence_ms=300, generation_run="r1", project_name="Eden"))
    os.makedirs(os.path.join(tmp, "outputs"), exist_ok=True)
    for n in ("x.wav", "c1.wav"):
        with open(os.path.join(tmp, "outputs", n), "wb") as f:
            f.write(b"\0" * 64)

    from ui.panels.batch_generation import BatchGenerationDialog
    dlg = BatchGenerationDialog(win._batch_manager, [], parent=win,
                                scene=scene, app_root=tmp)
    test.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    test.addCleanup(dlg.close)
    test.addCleanup(win.close)
    for p in patchers:
        test.addCleanup(p.stop)
    return dlg


def _scene_with_combined(test):
    scene = Scene(id="sc1abcd", name="S", project_id="proj1")
    scene.expected_audio_slots = [{
        "slot_id": "b1:1", "block_id": "b1", "part_index": 1,
        "part_of_block": 1, "block_label": "B1", "speaker": "",
        "character_id": None}]
    scene.audio_assets = [{
        "id": "a1", "slot_id": "b1:1", "block_id": "b1", "part_index": 1,
        "part_version": 1, "output_path": "outputs/x.wav",
        "duration": 5.0}]
    scene.combined_outputs = [{
        "id": "c1", "version": 1, "output_path": "outputs/c1.wav",
        "duration": 5.0,
        "sources": [{"slot_id": "b1:1", "part_version": 1,
                     "output_path": "outputs/x.wav"}]}]
    return scene


# ===========================================================================
# (d) Cancellation + stuck-batch fixes
# ===========================================================================
class TestCooperativeCancellation(unittest.TestCase):
    """d1: engine checkpoints + stale-cancel reset."""

    def setUp(self):
        from unittest.mock import patch, PropertyMock
        from engine.engine import Engine
        self.tmp = tempfile.mkdtemp(prefix="ss_p330_d1_")
        self.engine = Engine(app_root=self.tmp)
        self.patchers = [
            patch.object(type(self.engine._model), "is_loaded",
                         new_callable=PropertyMock, return_value=True),
            patch.object(type(self.engine._model), "device",
                         new_callable=PropertyMock, return_value="cpu"),
            patch.object(type(self.engine._model),
                         "get_model_and_tokenizer",
                         return_value=(FakeHiggsModel(), None)),
        ]
        for p in self.patchers:
            p.start()
        FakeHiggsModel.calls = []
        FakeHiggsModel.speech_s = 0.3
        FakeHiggsModel.gate = None
        FakeHiggsModel.on_enter = None

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _request(self):
        from engine.models import GenerationRequest
        return GenerationRequest(
            text="Hello world test.", parameters=GenerationParameters())

    def test_cancelled_before_pipeline_start(self):
        # A pending stop (cancel flag set) cancels the generation at the
        # pipeline ENTRY checkpoint when the worker picks it up. Called
        # synchronously (no submit): submit clears the flag — the flag is
        # set by a cancel that lands AFTER submit, which is exactly the
        # real-world race this checkpoint covers.
        self.engine._worker_pool._cancel_event.set()
        res = self.engine._execute_generation(self._request())
        self.assertFalse(res.success)
        self.assertTrue(getattr(res, "cancelled", False))
        self.assertEqual(FakeHiggsModel.calls, [],
                         "the model must never be called")

    def test_cancelled_before_model_call(self):
        # A cancel that lands BEFORE the model call cancels at the Step-4
        # checkpoint (the model is never invoked). Deterministic: the
        # validator is wrapped to set the cancel flag mid-pipeline (after
        # validation, before Step 4).
        orig_validate = self.engine._validate

        def hook(req):
            self.engine._worker_pool._cancel_event.set()
            return orig_validate(req)

        import unittest.mock as _mock
        with _mock.patch.object(self.engine, "_validate", side_effect=hook):
            res = self.engine._execute_generation(self._request())
        self.assertFalse(res.success)
        self.assertTrue(getattr(res, "cancelled", False))
        self.assertEqual(FakeHiggsModel.calls, [],
                         "the model call must be skipped by the "
                         "pre-model checkpoint")

    def test_cancel_inside_model_call_keeps_audio(self):
        # Cancel that arrives while the model call is BLOCKED: the native
        # call runs out (documented limitation) and the audio is KEPT.
        import threading
        gate = threading.Event()
        FakeHiggsModel.gate = gate
        fut = self.engine.generate(self._request())
        _wait_model_entered(FakeHiggsModel)
        self.engine.cancel_generation()
        gate.set()                       # the native call "runs out"
        res = fut.result(timeout=10)
        self.assertTrue(res.success,
                        "a cancel that arrives inside the model call "
                        "keeps the completed audio (documented "
                        "limitation + P3.30(d) decision)")
        FakeHiggsModel.gate = None

    def test_stale_cancel_does_not_leak_into_next_submission(self):
        self.engine.cancel_generation()
        _process(20)
        fut = self.engine.generate(self._request())
        res = fut.result(timeout=10)
        self.assertTrue(res.success,
                        "submit clears the stale cancel flag")
        self.assertFalse(getattr(res, "cancelled", False))
        self.assertEqual(len(FakeHiggsModel.calls), 1)


def _wait_model_entered(fake, timeout=5.0):
    t0 = time.time()
    while not fake.calls:
        if time.time() - t0 > timeout:
            raise AssertionError("model was never called")
        time.sleep(0.01)


class TestBatchManagerStopAndStuckPaths(unittest.TestCase):
    """d2/d3/d4 at the pure BatchManager level."""

    def _bm(self, submit_fn):
        from engine.batch_manager import BatchManager
        return BatchManager(submit_fn=submit_fn,
                            cancel_fn=lambda: True,
                            marshal_to_ui=lambda fn: fn())

    def test_orphan_completion_finishes_batch(self):
        from concurrent.futures import Future
        from engine.batch_manager import BatchJob
        bm = self._bm(lambda req: Future())
        bm.add_job(BatchJob(name="j1", prompt="x"))
        bm._running = True
        bm._current_index = None
        f = Future()
        f.set_result(None)
        bm._on_job_done(f)
        self.assertFalse(bm.is_running,
                         "an orphan completion must END the batch "
                         "(previously stuck 'running' forever)")

    def test_submit_valueerror_marks_failed_and_continues(self):
        from concurrent.futures import Future
        from engine.batch_manager import BatchJob, JobStatus
        calls = {"n": 0}

        def submit(req):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("plumbing broke")
            f = Future()
            f.set_result(GenerationResult(
                success=True, output_path="outputs/ok.wav",
                output_duration=1.0))
            return f

        bm = self._bm(submit)
        bm.add_job(BatchJob(name="bad", prompt="x"))
        bm.add_job(BatchJob(name="good", prompt="y"))
        bm.start()
        _wait_batch(bm)
        self.assertEqual(bm.jobs[0].status, JobStatus.FAILED)
        self.assertIn("plumbing broke", bm.jobs[0].error)
        self.assertEqual(bm.jobs[1].status, JobStatus.COMPLETED)

    def test_to_request_failure_marks_failed(self):
        from engine.batch_manager import BatchJob, JobStatus
        bm = self._bm(lambda req: Future())
        bad = BatchJob(name="bad", prompt="x")
        bad.output_filename = 12345          # to_request will choke
        bm.add_job(bad)
        # P3.44.4: drive the REAL production path (start()) — a direct
        # _process_next() call is now correctly rejected as an obsolete
        # invocation when no execution run is active (the manager's
        # running gate). start() calls _process_next synchronously, so
        # the containment under test still happens inside _process_next.
        try:
            bm.start()
        except Exception as exc:
            self.fail("start/_process_next must contain to_request "
                      "failures, got {0!r}".format(exc))
        self.assertEqual(bad.status, JobStatus.FAILED)

    def test_stop_marks_generating_skipped_and_restores_on_success(self):
        from concurrent.futures import Future
        from engine.batch_manager import BatchManager, BatchJob, JobStatus

        class HOLD:
            pass

        bm = BatchManager(submit_fn=lambda req: Future(),
                          cancel_fn=lambda: True,
                          marshal_to_ui=lambda fn: fn())
        job = BatchJob(name="j1", prompt="x")
        bm.add_job(job)
        bm._running = True
        bm._current_index = 0
        job.status = JobStatus.GENERATING
        bm.stop()
        self.assertEqual(job.status, JobStatus.SKIPPED,
                         "the generating row flips to SKIPPED at once")
        self.assertTrue(bm.stop_requested)
        # The in-flight call ran out and produced audio -> RESTORED.
        f = Future()
        f.set_result(GenerationResult(
            success=True, output_path="outputs/kept.wav",
            output_duration=2.0))
        bm._on_job_done(f)
        self.assertEqual(job.status, JobStatus.COMPLETED,
                         "a stop never destroys completed audio")
        _wait_batch(bm)
        self.assertFalse(bm.is_running)

    def test_cancelled_result_renders_as_skipped_not_failed(self):
        from concurrent.futures import Future
        from engine.batch_manager import BatchJob, JobStatus
        bm = self._bm(lambda req: Future())
        job = BatchJob(name="j1", prompt="x")
        bm.add_job(job)
        bm._running = True
        bm._current_index = 0
        job.status = JobStatus.GENERATING
        f = Future()
        f.set_result(GenerationResult(
            success=False, cancelled=True,
            errors=[{"type": "Cancelled",
                     "message": "Generation cancelled by user."}]))
        bm._on_job_done(f)
        self.assertEqual(job.status, JobStatus.SKIPPED,
                         "a cooperative cancel is a USER STOP, not an "
                         "error row")
        self.assertIn("Stopped", job.error)


class TestBatchDialogStopUI(unittest.TestCase):
    """d4 UI: Stop → 'Stopping…' → 'Stop' when the batch ends."""

    def test_stop_button_states(self):
        from engine.batch_manager import BatchManager, BatchJob, JobStatus
        from ui.panels.batch_generation import BatchGenerationDialog
        bm = BatchManager(submit_fn=lambda req: None,
                          cancel_fn=lambda: True,
                          marshal_to_ui=lambda fn: fn())
        bm.add_job(BatchJob(name="j1", prompt="x"))
        dlg = BatchGenerationDialog(bm, [], parent=None)
        self.addCleanup(dlg.close)
        self.assertEqual(dlg._stop_btn.text(), "Stop")
        bm._running = True
        bm._current_index = 0
        bm.jobs[0].status = JobStatus.GENERATING
        dlg._on_stop()
        self.assertIn("Stopping", dlg._stop_btn.text())
        self.assertFalse(dlg._stop_btn.isEnabled())
        # Batch ends -> button resets.
        bm._finish_batch()
        dlg._update_buttons()
        self.assertEqual(dlg._stop_btn.text(), "Stop")


# ===========================================================================
# (e) Readable button sizes
# ===========================================================================
class TestButtonSizes(unittest.TestCase):
    def test_batch_dialog_p328_buttons_readable(self):
        dlg = _make_scene_dialog(self)
        dlg.resize(900, 600)
        # A scene-mode row with slot provenance → the versions ▾ button.
        from engine.batch_manager import BatchJob
        from engine.batch_manager import JobStatus
        job = BatchJob(name="B1", prompt="x", slot_id="b1:1",
                       scene_id=dlg._scene.id, part_index=1)
        dlg._manager.add_job(job)
        job.status = JobStatus.COMPLETED
        dlg._refresh_table()
        use_btn = _find_button_startswith(dlg, "Use as output")
        if use_btn is None:
            use_btn = _find_button_startswith(dlg, "\u2713 selected")
        self.assertIsNotNone(use_btn, "Use-as-output button must exist")
        self.assertGreaterEqual(use_btn.minimumHeight(), 26,
                                "28px target: readable click target")
        versions_btn = _find_button_startswith(dlg, "\u00b7 ")
        self.assertIsNotNone(versions_btn,
                             "versions popover button must exist for a "
                             "slot with versions")
        versions_btn.ensurePolished()
        self.assertGreaterEqual(versions_btn.sizeHint().height(), 20,
                                "padded popover button, not zero-padding "
                                "text")

    def test_assemble_choose_button_readable(self):
        from ui.panels.assemble_dialog import _SceneCheckRow
        scene = _scene_with_combined(self)
        row = _SceneCheckRow(scene, "/tmp/c1.wav", 5.0, False, "Combined v01")
        self.addCleanup(row.deleteLater)
        choose_btn = None
        for btn in row.findChildren(QPushButton):
            if btn.text() == "Choose Output\u2026":
                choose_btn = btn
                break
        self.assertIsNotNone(choose_btn)
        self.assertGreaterEqual(choose_btn.minimumHeight(), 26)


# ===========================================================================
# (f) Friendly stale-audio warning
# ===========================================================================
class TestStaleWarningCopy(unittest.TestCase):
    def test_use_anyway_dialog_copy_and_buttons(self):
        """The dialog body + buttons follow the user-approved copy."""
        from ui.panels.assemble_dialog import stale_audio_use_anyway_dialog
        from PySide6.QtWidgets import QMessageBox
        from unittest.mock import patch

        reasons = ["B1 has a newer version: v02",
                   "B2 has a newer version: v02",
                   "source file missing: gone.wav"]
        seen = {}

        def cap_exec(self):
            seen["text"] = self.text()
            seen["buttons"] = [b.text() for b in self.buttons()]
            seen["default"] = self.defaultButton().text()
            return None

        with patch.object(QMessageBox, "exec", cap_exec):
            stale_audio_use_anyway_dialog(None, reasons)
        self.assertIn("This Scene Audio is out of date.", seen["text"])
        self.assertIn("B1 has a newer version: v02", seen["text"])
        self.assertIn("B2 has a newer version: v02", seen["text"])
        self.assertIn("source file missing: gone.wav", seen["text"])
        # Version lines come BEFORE the missing-file lines.
        self.assertLess(seen["text"].index("B1 has a newer version"),
                        seen["text"].index("source file missing"))
        self.assertIn("This Combined Audio was created from older "
                      "versions.", seen["text"])
        self.assertIn("Do you want to use the older audio anyway?",
                      seen["text"])
        self.assertIn("Cancel", seen["buttons"])
        self.assertIn("Use Anyway", seen["buttons"])
        self.assertEqual(seen["default"], "Cancel",
                         "Cancel is the safe default")


class TestMainWindowStaleSelectionUsesFriendlyDialog(unittest.TestCase):
    def test_select_output_stale_shows_use_anyway(self):
        from unittest.mock import patch, PropertyMock
        from engine.engine import Engine
        from ui.main_window import MainWindow
        import ui.panels.assemble_dialog as ad

        tmp = tempfile.mkdtemp(prefix="ss_p330_f_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        engine = Engine(app_root=tmp)
        patchers = [
            patch.object(type(engine._model), "is_loaded",
                         new_callable=PropertyMock, return_value=True),
            patch.object(type(engine._model), "device",
                         new_callable=PropertyMock, return_value="cpu"),
            patch.object(type(engine._model),
                         "get_model_and_tokenizer",
                         return_value=(FakeHiggsModel(), None)),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        win = MainWindow(engine)
        self.addCleanup(win.close)
        win._project_manager = type(
            "PM", (), {"__init__": lambda self: None,
                       "save_project": lambda self, p: None,
                       "list_projects": lambda self: []})()
        project = Project(name="P", id="p1")
        scene = _scene_with_combined(self)
        project.add_scene(scene)
        win._active_project = project
        win._active_scene = scene
        win._current_project = "P"
        os.makedirs(os.path.join(tmp, "outputs"), exist_ok=True)
        for n in ("x.wav", "c1.wav", "xv2.wav"):
            with open(os.path.join(tmp, "outputs", n), "wb") as f:
                f.write(b"\0" * 64)
        scene.audio_assets.append({
            "id": "a1v2", "slot_id": "b1:1", "block_id": "b1",
            "part_index": 1, "part_version": 2,
            "output_path": "outputs/xv2.wav", "duration": 5.0})
        win._batch_dialog = None

        calls = {"n": 0}

        def fake_dialog(parent, reasons, title=""):
            calls["n"] += 1
            calls["reasons"] = list(reasons)
            return calls["n"] == 1   # first: Use Anyway; (2nd not needed)

        with patch.object(ad, "stale_audio_use_anyway_dialog",
                          side_effect=fake_dialog):
            from engine.audio_provenance import KIND_SCENE_COMBINED
            win._on_select_output_requested(KIND_SCENE_COMBINED, "c1")
        self.assertEqual(calls["n"], 1,
                         "the friendly Use-Anyway dialog must be used")
        self.assertTrue(any("newer version" in r for r in calls["reasons"]))
        self.assertEqual(scene.selected_output,
                         {"kind": "scene_combined", "id": "c1"})

        # Cancel keeps the selection unchanged (still explicit from the
        # first call — clear it, then cancel).
        scene.selected_output = None
        calls["n"] = 0

        def fake_cancel(parent, reasons, title=""):
            calls["n"] += 1
            return False

        with patch.object(ad, "stale_audio_use_anyway_dialog",
                          side_effect=fake_cancel):
            win._on_select_output_requested(KIND_SCENE_COMBINED, "c1")
        self.assertIsNone(scene.selected_output,
                          "Cancel must not select the stale output")


# ===========================================================================
# (g) all-stale Scene requires review (assembly row gating)
# ===========================================================================
class TestAllStaleRequiresReviewInAssembly(unittest.TestCase):
    def test_assemble_row_review_required(self):
        tmp = tempfile.mkdtemp(prefix="ss_p330_g_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.makedirs(os.path.join(tmp, "outputs"), exist_ok=True)
        for n in ("x.wav", "c1.wav", "xv2.wav"):
            with open(os.path.join(tmp, "outputs", n), "wb") as f:
                f.write(b"\0" * 64)
        from engine.audio_provenance import resolve_scene_output
        from ui.panels.assemble_dialog import _SceneCheckRow
        scene = _scene_with_combined(self)
        scene.audio_assets.append({
            "id": "a1v2", "slot_id": "b1:1", "block_id": "b1",
            "part_index": 1, "part_version": 2,
            "output_path": "outputs/xv2.wav", "duration": 5.0})
        res = resolve_scene_output(scene, tmp)
        self.assertTrue(res["requires_review"])
        row = _SceneCheckRow(scene, "", 0.0, False, "",
                             review_reason=res["reason"])
        self.addCleanup(row.deleteLater)
        self.assertFalse(row._checkbox.isEnabled(),
                         "a REVIEW REQUIRED row must be unchecked + "
                         "disabled — stale audio is never assembled "
                         "silently")


if __name__ == "__main__":
    unittest.main()
