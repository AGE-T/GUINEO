"""
SpeechStudio — P3.44.5 Stabilisation / Integrity Phase 1 regression tests
==========================================================================

Two proven defects, each captured as a deterministic reproduction that
FAILS on the pre-P3.44.5 code and PASSES after the fix:

ISSUE B/D — Batch dialog lifecycle / stale-listener (§2–§7, §16, §17)
    closeEvent releases the dialog's manager listener, but the dialog
    OBJECT survives close and the scene-aware single-instance policy
    (MainWindow._focus_existing_batch_dialog) later re-shows the SAME
    object without re-registering it. The reopened dialog then receives
    NO manager events: stale row pills, frozen summary, Start disabled
    while Pause/Stop stay enabled — recovering only when the user
    presses Pause/Stop (their handlers force a local refresh).
    Fix under test: BatchGenerationDialog.showEvent funnels every
    presentation through the now-idempotent _connect_manager plus a
    full _on_manager_changed re-sync from manager truth.

PROVENANCE — slot_of_asset positional identity mixing (§7–§10, §18)
    The asset→slot join matched ``part_index`` POSITIONALLY against the
    Scene's expected slots and ignored ``block_id``. After a structural
    edit (blocks rebuilt with new stable identities), old assets whose
    part positions lined up were accepted as coverage for the NEW
    structure — derived coverage reported it as covered and Combine
    could assemble old audio under the new structure.
    Fix under test: for a slotted scene the join now requires part
    identity AGREEMENT (block-derived slot ⟺ same block_id on the
    asset; plain slot ⟺ plain asset); the legacy scope (scenes without
    an expected slot structure) keeps the documented synthesis path.

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_44_5_stabilisation_integrity.py -v
"""

from __future__ import annotations

import os
import sys
import time
import types
import unittest
import tempfile
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np

# ---------------------------------------------------------------------------
# Fake torch (house pattern: test_p3_28 / test_p3_27b)
# ---------------------------------------------------------------------------
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

SAMPLE_RATE = 24000


def make_speech(seconds: float, freq: float = 220.0) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class FakeHiggsModel:
    """Model-boundary fake (P3.27B house pattern).

    ``delay_s`` simulates model latency ON THE WORKER THREAD so that
    mid-run UI states (e.g. the GENERATING status pill) are observable
    by polling tests — the default 0.0 keeps the instant behaviour the
    older suites were written against.
    """

    calls = []
    speech_s = 1.0
    runaway = False
    fail_next = 0
    delay_s = 0.0

    def generate_speech(self, text, tokenizer, *, reference_audio=None,
                        reference_sample_rate=None, reference_codes=None,
                        reference_text=None, max_new_tokens=2048,
                        temperature=1.0, top_p=None, top_k=None):
        FakeHiggsModel.calls.append(dict(text=text))
        if FakeHiggsModel.delay_s > 0:
            time.sleep(FakeHiggsModel.delay_s)
        if FakeHiggsModel.fail_next > 0:
            FakeHiggsModel.fail_next -= 1
            raise RuntimeError("simulated model failure")
        return make_speech(FakeHiggsModel.speech_s)


def _process(ms=30):
    from PySide6.QtWidgets import QApplication
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


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


# ---------------------------------------------------------------------------
# Shared assertion helpers
# ---------------------------------------------------------------------------
_PILL_TEXTS = {"Done", "Gen", "Queued", "Error", "Stopped"}


def listener_count(bm, dlg) -> int:
    """How many of the manager's registered listeners are THIS dialog's."""
    cb = getattr(dlg, "_on_changed_cb", None)
    if cb is None:
        return 0
    return sum(1 for c in bm.on_changed_listeners if c is cb)


def pill_text(dlg, row: int) -> str:
    """The visible status-pill text of a table row."""
    from PySide6.QtWidgets import QLabel
    cell = dlg._table.cellWidget(row, dlg._col_status)
    if cell is None:
        return ""
    for label in cell.findChildren(QLabel):
        if label.text() in _PILL_TEXTS:
            return label.text()
    return ""


def rendered_status(dlg, row: int):
    """The dialog's OWN rendered-state tracking for a row (what the UI
    last painted — stays stale when manager events stop arriving)."""
    refs = dlg._row_refs[row] if row < len(dlg._row_refs) else None
    return refs.get("status") if refs else None


def button_state(dlg):
    """(start_enabled, pause_enabled, stop_enabled)."""
    return (dlg._start_btn.isEnabled(),
            dlg._pause_btn.isEnabled(),
            dlg._stop_btn.isEnabled())


# ===========================================================================
# 1) PURE LIFECYCLE / STATE — real BatchManager + real dialog, no engine
# ===========================================================================
class TestDialogLifecyclePure(unittest.TestCase):
    """Issue B/D core mechanics without any generation engine.

    These test the LISTENER lifecycle only: construction subscribes
    exactly once, close releases, re-show re-subscribes exactly once
    with the SAME callback object, and repeated cycles never multiply
    callbacks. On the pre-P3.44.5 code the re-show test fails: the
    listener stays released after closeEvent and the dialog is deaf.
    """

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p3445_pure_")
        from engine.batch_manager import BatchManager
        from engine.settings_manager import SettingsManager
        from ui.panels.batch_generation import BatchGenerationDialog
        self.bm = BatchManager(submit_fn=lambda req: None)
        # Isolate the dialog's settings channel into the tmp root (the
        # no-settings_manager fallback would touch the REPO's settings
        # folder — house rule: tests never persist outside their tmp).
        self.sm = SettingsManager.instance(
            os.path.join(self.tmp, "settings"))
        # The PRE-dialog marshal owner — what closeEvent must restore.
        self.pre_dialog_marshal = self.bm.marshal_to_ui
        self.dlg = BatchGenerationDialog(self.bm, [], None,
                                         app_root=self.tmp,
                                         settings_manager=self.sm)
        self.dlg.show()
        _process(30)

    def tearDown(self):
        try:
            self.dlg.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_construction_subscribes_exactly_once(self):
        self.assertEqual(listener_count(self.bm, self.dlg), 1,
                         "a freshly constructed + shown dialog must be "
                         "subscribed exactly once")
        self.assertIn(self.dlg._on_changed_cb,
                      self.bm.on_changed_listeners)
        self.assertEqual(self.bm.marshal_to_ui, self.dlg._marshaler.marshal)

    def test_close_releases_listener_and_marshal(self):
        self.dlg.close()
        _process(30)
        self.assertEqual(listener_count(self.bm, self.dlg), 0,
                         "closeEvent must release the manager listener")
        self.assertNotIn(self.dlg._on_changed_cb,
                         self.bm.on_changed_listeners)
        self.assertEqual(self.bm.marshal_to_ui, self.pre_dialog_marshal,
                         "the marshaler must be handed back on close")

    def test_reclose_is_idempotent(self):
        self.dlg.close()
        self.dlg.close()
        _process(30)
        self.assertEqual(listener_count(self.bm, self.dlg), 0)

    def test_reshow_after_close_reconnects_exactly_once(self):
        """THE stale-listener regression: the audited reuse path.

        closeEvent releases the listener; the single-instance policy
        later re-shows the SAME still-alive object via dlg.show().
        Before P3.44.5 nothing re-registered the listener — the dialog
        stayed deaf to every later manager event.
        """
        cb_before = self.dlg._on_changed_cb
        self.dlg.close()
        _process(30)
        self.assertEqual(listener_count(self.bm, self.dlg), 0)
        self.dlg.show()          # the _focus_existing_batch_dialog path
        _process(30)
        self.assertEqual(listener_count(self.bm, self.dlg), 1,
                         "re-show must re-establish exactly ONE listener")
        self.assertIs(self.dlg._on_changed_cb, cb_before,
                      "reconnection must REUSE the same callback object "
                      "(identity-dedupe → no duplicated UI updates)")
        self.assertEqual(self.bm.marshal_to_ui,
                         self.dlg._marshaler.marshal,
                         "the marshaler must be re-installed on reuse")

    def test_repeated_close_open_cycles_never_multiply_callbacks(self):
        """§16 Case 5: 5 close/open/focus cycles → still one listener,
        and one manager event still produces exactly one UI update."""
        for _ in range(5):
            self.dlg.close()
            _process(10)
            self.dlg.show()
            _process(10)
        self.assertEqual(listener_count(self.bm, self.dlg), 1)
        hits = {"n": 0}
        orig = self.dlg._on_manager_changed

        def _count():
            hits["n"] += 1
            orig()

        self.dlg._on_manager_changed = _count
        self.bm._emit_changed()
        _process(50)
        self.dlg._on_manager_changed = orig
        self.assertEqual(hits["n"], 1,
                         "one logical manager event must produce exactly "
                         "one logical UI update — never N duplicates")

    def test_extra_show_while_connected_is_noop(self):
        """Showing an already-subscribed dialog must not duplicate."""
        self.dlg.show()
        _process(20)
        self.dlg.show()
        _process(20)
        self.assertEqual(listener_count(self.bm, self.dlg), 1)

    def test_reshow_resyncs_presentation_from_manager_truth(self):
        """Everything that changed while the dialog was closed (no
        events were delivered) must be reflected on re-show WITHOUT
        any manual Pause/Stop/refresh interaction."""
        from engine.batch_manager import BatchJob, JobStatus
        for i in range(3):
            self.bm.add_job(BatchJob(name="Part {0}".format(i + 1),
                                     prompt="x {0}".format(i)))
        _process(30)
        self.dlg.close()
        # The queue mutates while the dialog is closed: jobs land and
        # one completes (manager truth moves on, dialog hears nothing).
        self.bm.jobs[1].status = JobStatus.COMPLETED
        self.bm.jobs[1].output_path = "outputs/done.wav"
        self.dlg.show()
        _process(30)
        self.assertEqual(self.dlg._table.rowCount(), 3,
                         "rows added while closed must appear on re-show")
        self.assertIs(rendered_status(self.dlg, 1), JobStatus.COMPLETED,
                      "the completed job must render Completed after "
                      "re-show (re-sync from manager truth)")
        self.assertEqual(pill_text(self.dlg, 1), "Done")
        self.assertIs(rendered_status(self.dlg, 0), JobStatus.PENDING)
        self.assertEqual(button_state(self.dlg),
                         (True, False, False),
                         "idle manager → Start enabled, Pause/Stop "
                         "disabled — without any user interaction")


# ===========================================================================
# 2) MAIN-WINDOW REUSE POLICY — §16 Cases 1–5 (bootstrap/UI level)
# ===========================================================================
class _ReuseHarness(unittest.TestCase):
    """Real MainWindow with the model boundary faked (house pattern),
    no generation started — these tests exercise the reuse POLICY."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p3445_reuse_")
        from engine.engine import Engine
        from ui.main_window import MainWindow
        from unittest.mock import patch, PropertyMock
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
                       "save_project": lambda self, p: None})()
        from engine.models import Project, Scene
        self.project = Project(name="Eden", id="proj1")
        self.scene_a = Scene(id="sc1aaaa", name="Scene A",
                             project_id="proj1")
        self.scene_b = Scene(id="sc2bbbb", name="Scene B",
                             project_id="proj1")
        self.project.add_scene(self.scene_a)
        self.project.add_scene(self.scene_b)
        self.win._active_project = self.project
        self.win._active_scene = self.scene_a
        self.win._current_project = "Eden"
        FakeHiggsModel.calls = []
        FakeHiggsModel.speech_s = 1.0
        FakeHiggsModel.fail_next = 0
        FakeHiggsModel.delay_s = 0.0

    def tearDown(self):
        try:
            if getattr(self, "win", None) is not None:
                try:
                    if self.win._batch_dialog is not None:
                        self.win._batch_dialog.close()
                except Exception:
                    pass
                self.win.close()
        except Exception:
            pass
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def params(self):
        from engine.models import GenerationParameters
        return GenerationParameters(
            temperature=0.95, top_p=0.95, top_k=300, max_new_tokens=4096,
            seed=None, append_silence=0.5, normalize_output=False,
            auto_play=False)

    def start_long(self, parts, scene, review=True):
        self.win._active_scene = scene
        self.win._start_long_narration(parts, None, self.params(), {},
                                       review_mode=review)
        return self.win._batch_dialog

    def open_manual(self):
        self.win._on_open_batch_generation()
        return self.win._batch_dialog


class TestReusePolicy(_ReuseHarness):
    """§16: scene/mode isolation stays intact AND the listener works on
    every reuse path."""

    def _assert_live_update_flows(self, dlg):
        """A live manager event must reach the reused dialog (§16: the
        listener works — not merely 'does not raise')."""
        from engine.batch_manager import BatchJob
        bm = self.win._batch_manager
        self.assertEqual(listener_count(bm, dlg), 1)
        rows_before = dlg._table.rowCount()
        bm.add_job(BatchJob(name="live", prompt="x"))
        bm._emit_changed()
        _process(50)
        self.assertEqual(dlg._table.rowCount(), rows_before + 1,
                         "the reused dialog must still receive live "
                         "manager updates")

    def test_case1_scene_a_same_mode_reuse_listener_works(self):
        parts = [_make_part(block_id="a1"), _make_part(block_id="a2")]
        first = self.start_long(parts, self.scene_a)
        second = self.start_long(parts, self.scene_a)
        self.assertIs(first, second,
                      "same scene + same mode must REUSE the dialog")
        self.assertIs(second.scene, self.scene_a)
        self._assert_live_update_flows(second)

    def test_case2_scene_a_close_reopen_listener_works(self):
        parts = [_make_part(block_id="a1"), _make_part(block_id="a2")]
        first = self.start_long(parts, self.scene_a)
        cb = first._on_changed_cb
        first.close()
        _process(30)
        bm = self.win._batch_manager
        self.assertEqual(listener_count(bm, first), 0)
        second = self.start_long(parts, self.scene_a)
        self.assertIs(second, first,
                      "a closed dialog for the SAME scene is re-focused, "
                      "not replaced (single-instance policy)")
        self.assertEqual(listener_count(bm, second), 1,
                         "the re-focused dialog must be re-subscribed")
        self.assertIs(second._on_changed_cb, cb)
        self._assert_live_update_flows(second)

    def test_case3_scene_b_does_not_reuse_scene_a_dialog(self):
        parts_a = [_make_part(block_id="a1")]
        dlg_a = self.start_long(parts_a, self.scene_a)
        dlg_a.close()
        _process(30)
        parts_b = [_make_part(block_id="b1")]
        dlg_b = self.start_long(parts_b, self.scene_b)
        self.assertIsNot(dlg_b, dlg_a,
                         "Scene B must never reuse Scene A's dialog")
        self.assertIs(dlg_b.scene, self.scene_b)
        bm = self.win._batch_manager
        self.assertEqual(listener_count(bm, dlg_b), 1)
        self.assertEqual(listener_count(bm, dlg_a), 0,
                         "the superseded Scene A dialog must stay "
                         "unsubscribed (scene/mode isolation)")

    def test_case4_manual_close_reopen_listener_works(self):
        first = self.open_manual()
        cb = first._on_changed_cb
        first.close()
        _process(30)
        second = self.open_manual()
        self.assertIs(second, first,
                      "manual mode re-focuses the same closed dialog")
        bm = self.win._batch_manager
        self.assertEqual(listener_count(bm, second), 1)
        self.assertIs(second._on_changed_cb, cb)
        self._assert_live_update_flows(second)

    def test_case5_repeated_focus_cycles_do_not_multiply_callbacks(self):
        parts = [_make_part(block_id="a1")]
        dlg = self.start_long(parts, self.scene_a)
        bm = self.win._batch_manager
        for _ in range(4):
            dlg.close()
            _process(10)
            dlg = self.start_long(parts, self.scene_a)
        self.assertEqual(listener_count(bm, dlg), 1,
                         "4 close/reopen cycles → exactly one listener")
        hits = {"n": 0}
        orig = dlg._on_manager_changed

        def _count():
            hits["n"] += 1
            orig()

        dlg._on_manager_changed = _count
        bm._emit_changed()
        _process(50)
        dlg._on_manager_changed = orig
        self.assertEqual(hits["n"], 1,
                         "one manager event → one UI update after cycles")


# ===========================================================================
# 3) RUNTIME INTEGRATION — the audited §6/§17 reproduction, scene mode
# ===========================================================================
class _RuntimeHarness(_ReuseHarness):
    """Adds the runtime generation battery (FakeHiggsModel)."""

    def wait_batch(self, timeout=60):
        bm = self.win._batch_manager
        t0 = time.time()
        while bm.is_running and time.time() - t0 < timeout:
            _process(20)
        _process(200)


class TestClosedReopenGeneration(_RuntimeHarness):
    """§6 minimum scenario, END to END with the model boundary faked.

    batch → select subset → generate → CLOSE → reopen/focus the SAME
    dialog → start another generation → observe transitions → terminal
    state. On the pre-P3.44.5 code the reopened dialog is deaf: rows
    never flip during run 2 and the terminal button state never
    arrives without a manual Pause/Stop click.
    """

    PARTS = [_make_part(block_id="blk{0}".format(i),
                        text="Sentence number {0} for the part.".format(i))
             for i in range(1, 5)]

    def test_close_reopen_generate_terminal_state(self):
        from engine.batch_manager import JobStatus
        bm = self.win._batch_manager
        # Simulate real model latency so the GENERATING pill is
        # observable while the reused dialog is being polled.
        FakeHiggsModel.delay_s = 0.35

        # 1-2. Create a batch (4 jobs) + open the dialog (review mode:
        # jobs PENDING, the user chooses what to generate).
        dlg = self.start_long(self.PARTS, self.scene_a, review=True)
        self.assertEqual(dlg._table.rowCount(), 4)
        cb_identity = dlg._on_changed_cb

        # 3-4. Select a subset (rows 0 and 1) and generate it.
        # P3.45.4 (documented update): job-keyed check states.
        dlg._check_states = {dlg._check_key(j): v for j, v in zip(
            dlg._manager.jobs, [True, True, False, False])}
        dlg._on_start()
        self.wait_batch()
        self.assertEqual(
            [j.status for j in bm.jobs],
            [JobStatus.COMPLETED, JobStatus.COMPLETED,
             JobStatus.PENDING, JobStatus.PENDING])

        # 5-6. Close the dialog, then reopen/focus the SAME dialog for
        # the same scene/mode (a fresh Generate Long activation reuses
        # the still-alive closed dialog).
        dlg.close()
        _process(50)
        self.assertEqual(listener_count(bm, dlg), 0)
        reopened = self.start_long(self.PARTS, self.scene_a, review=True)
        self.assertIs(reopened, dlg,
                      "the closed dialog must be reused, not replaced")
        self.assertEqual(listener_count(bm, reopened), 1,
                         "the reused dialog must be subscribed exactly "
                         "once BEFORE further manager activity")

        # 7. Start another valid generation — this time everything.
        # P3.45.4 (documented update): job-keyed check states.
        reopened._check_states = {reopened._check_key(j): True
                                  for j in reopened._manager.jobs}
        reopened._on_start()
        self.assertTrue(bm.is_running)

        # 8. Observe job state transitions DURING the generation: the
        # reused dialog must paint at least one GENERATING row live.
        saw_generating = False
        t0 = time.time()
        while bm.is_running and time.time() - t0 < 60:
            _process(20)
            if any(refs.get("status") is JobStatus.GENERATING
                   for refs in reopened._row_refs):
                saw_generating = True
        self.wait_batch()
        self.assertTrue(saw_generating,
                        "the reopened dialog must show live GENERATING "
                        "transitions (it was deaf before P3.44.5)")

        # 9-10. Every visible row reaches the correct terminal state.
        self.assertFalse(bm.is_running)
        for i in range(4):
            self.assertEqual(bm.jobs[i].status, JobStatus.COMPLETED)
            self.assertIs(rendered_status(reopened, i),
                          JobStatus.COMPLETED,
                          "row {0} must RENDER Completed".format(i))
            self.assertEqual(pill_text(reopened, i), "Done")

        # 11. Button capability at completion — the §17 symptom is
        # impossible: no Pause, no Stop, no refresh was ever clicked.
        self.assertEqual(button_state(reopened), (True, False, False),
                         "terminal state: Start enabled, Pause disabled, "
                         "Stop disabled — without any manual recovery")

        # 12. No duplicate callbacks/listeners were created.
        self.assertEqual(listener_count(bm, reopened), 1)
        self.assertIs(reopened._on_changed_cb, cb_identity,
                      "the SAME callback object must serve every cycle")
        hits = {"n": 0}
        orig = reopened._on_manager_changed

        def _count():
            hits["n"] += 1
            orig()

        reopened._on_manager_changed = _count
        bm._emit_changed()
        _process(50)
        reopened._on_manager_changed = orig
        self.assertEqual(hits["n"], 1)

    def test_control_never_closed_identical_terminal_state(self):
        """§5: the result must be IDENTICAL when the dialog was opened
        once and never closed (control group for the test above)."""
        from engine.batch_manager import JobStatus
        bm = self.win._batch_manager
        FakeHiggsModel.delay_s = 0.35
        dlg = self.start_long(self.PARTS, self.scene_a, review=True)
        # P3.45.4 (documented update): job-keyed check states.
        dlg._check_states = {dlg._check_key(j): True
                             for j in dlg._manager.jobs}
        dlg._on_start()
        self.wait_batch()
        self.assertFalse(bm.is_running)
        for i in range(4):
            self.assertIs(rendered_status(dlg, i), JobStatus.COMPLETED)
            self.assertEqual(pill_text(dlg, i), "Done")
        self.assertEqual(button_state(dlg), (True, False, False))
        self.assertEqual(listener_count(bm, dlg), 1)


class TestFinishedWhileClosed(_RuntimeHarness):
    """§17 head-on: a batch that FINISHES while the dialog is closed
    (the preserve-batch hand-over path). The OLD behaviour — reopened
    window still showing Running pills with Start disabled and
    Pause/Stop enabled until a manual click — must be impossible."""

    def test_reopen_after_finish_shows_terminal_state(self):
        from engine.batch_manager import BatchJob, JobStatus
        bm = self.win._batch_manager
        dlg = self.open_manual()
        for i in range(3):
            bm.add_job(BatchJob(name="Part {0}".format(i + 1),
                                prompt="Manual part {0}.".format(i),
                                project="Eden"))
        _process(30)
        dlg._on_start()
        self.assertTrue(bm.is_running)
        # The hand-over close: listener released, batch KEEPS running.
        dlg.close_without_stopping_batch()
        _process(30)
        self.assertEqual(listener_count(bm, dlg), 0)
        # The batch finishes while the dialog is closed and deaf.
        self.wait_batch()
        self.assertFalse(bm.is_running)
        for j in bm.jobs:
            self.assertIs(j.status, JobStatus.COMPLETED)
        # Reopen the SAME queue (manual mode focuses the same dialog).
        reopened = self.open_manual()
        self.assertIs(reopened, dlg)
        _process(50)
        # §17 symptom check — WITHOUT clicking Pause/Stop/anything:
        self.assertEqual(button_state(reopened), (True, False, False),
                         "reopened-after-finish dialog must show the "
                         "terminal capability state immediately")
        for i in range(3):
            self.assertIs(rendered_status(reopened, i),
                          JobStatus.COMPLETED,
                          "row {0} must render Completed on reopen".format(i))
            self.assertEqual(pill_text(reopened, i), "Done")
        self.assertEqual(listener_count(bm, reopened), 1)


# ===========================================================================
# 4) PROVENANCE — block-aware join (pure functions, no Qt)
# ===========================================================================
class _ProvenanceFactory(unittest.TestCase):
    """Shared fixtures for the provenance tests."""

    def make_scene(self, slots=None, assets=None):
        from engine.models import Scene
        s = Scene(id="ab12cd34ef56", name="Scene 03")
        if slots is not None:
            s.expected_audio_slots = list(slots)
        if assets is not None:
            s.audio_assets = list(assets)
        return s

    def slot(self, slot_id, block_id, part_index, label="B",
             part_of_block=1):
        return {"slot_id": slot_id, "block_id": block_id,
                "block_label": label, "part_index": part_index,
                "part_of_block": part_of_block, "speaker": "",
                "character_id": None}

    def asset(self, part_index, version, block_id, path=None):
        return {
            "id": "a{0}v{1}".format(part_index, version),
            "scene_id": "ab12cd34ef56",
            "output_path": path or "outputs/p{0}_v{1:02d}.wav".format(
                part_index, version),
            "duration": 10.0,
            "speaker": None, "character_id": None,
            "voice_profile_id": None,
            "generated_at": "20260101_120000",
            "block_id": block_id,
            "part_index": part_index,
            "part_version": version,
            "generation_run": "ab12cd34-r001",
        }


class TestBlockAwareJoin(_ProvenanceFactory):
    """§18 matrix: valid / invalid / legacy / no-accidental-downgrade."""

    def test_invalid_same_part_index_different_block_id(self):
        """§10 INVALID: same part_index + different block_id MUST NOT
        match. (The pre-P3.44.5 positional join returned the slot.)"""
        from engine.audio_provenance import (
            slot_of_asset, is_slot_covered, assets_for_slot)
        scene = self.make_scene(slots=[
            self.slot("nb1:1", "nb1", 1),
            self.slot("nb2:1", "nb2", 2),
            self.slot("nb3:1", "nb3", 3),
        ])
        old_asset = self.asset(2, 1, block_id="ob2")
        self.assertIsNone(
            slot_of_asset(old_asset, scene),
            "an old-block asset must not join the NEW structure just "
            "because part_index lines up")
        self.assertFalse(is_slot_covered(scene, "nb2:1"))
        self.assertEqual(assets_for_slot(scene, "nb2:1"), [])

    def test_valid_same_part_index_same_block_id(self):
        """§10 VALID: same block_id + same part_index still qualifies."""
        from engine.audio_provenance import (
            slot_of_asset, is_slot_covered, latest_asset_for_slot)
        scene = self.make_scene(slots=[
            self.slot("b1:1", "b1", 1),
            self.slot("b2:1", "b2", 2),
        ])
        a1 = self.asset(2, 1, block_id="b2")
        a2 = self.asset(2, 2, block_id="b2")
        scene.audio_assets = [a1, a2]
        self.assertEqual(slot_of_asset(a1, scene), "b2:1")
        self.assertTrue(is_slot_covered(scene, "b2:1"))
        self.assertIs(latest_asset_for_slot(scene, "b2:1"), a2,
                      "version ordering within a VALID slot is intact")

    def test_no_accidental_downgrade_to_positional(self):
        """§18 no-downgrade: a modern block-aware asset whose stronger
        comparison failed must NOT silently fall back to the weaker
        legacy synthesis ("{old_block}:{part_index}")."""
        from engine.audio_provenance import slot_of_asset
        scene = self.make_scene(slots=[
            self.slot("nb1:1", "nb1", 1),
            self.slot("nb2:1", "nb2", 2),
        ])
        old_asset = self.asset(1, 1, block_id="ob1")
        self.assertIsNone(
            slot_of_asset(old_asset, scene),
            "must be None — never the synthesized 'ob1:1' positional id")

    def test_shrunk_structure_position_missing_is_none(self):
        """An asset whose position no longer exists (structure shrank)
        belongs to no current slot — not re-anchored by synthesis."""
        from engine.audio_provenance import slot_of_asset
        scene = self.make_scene(slots=[self.slot("nb1:1", "nb1", 1)])
        old_asset = self.asset(3, 1, block_id="ob3")
        self.assertIsNone(slot_of_asset(old_asset, scene))

    def test_kind_mismatch_block_asset_vs_plain_slot(self):
        from engine.audio_provenance import slot_of_asset
        scene = self.make_scene(slots=[self.slot("plain:1", None, 1)])
        block_asset = self.asset(1, 1, block_id="ob1")
        self.assertIsNone(
            slot_of_asset(block_asset, scene),
            "a block-derived asset cannot cover a plain slot: the "
            "structures differ in part-identity kind")

    def test_kind_mismatch_plain_asset_vs_block_slot(self):
        from engine.audio_provenance import slot_of_asset, is_slot_covered
        scene = self.make_scene(slots=[self.slot("nb1:1", "nb1", 1)])
        plain_asset = self.asset(1, 1, block_id=None)
        self.assertIsNone(
            slot_of_asset(plain_asset, scene),
            "a plain asset cannot cover a block-derived slot")
        self.assertFalse(is_slot_covered(scene, "nb1:1"))

    def test_plain_asset_vs_plain_slot_positional_match_retained(self):
        """Plain slots keep their positional identity model — a plain
        asset still covers the plain slot at its position."""
        from engine.audio_provenance import slot_of_asset, is_slot_covered
        scene = self.make_scene(slots=[
            self.slot("plain:1", None, 1),
            self.slot("plain:2", None, 2),
        ])
        plain_asset = self.asset(2, 1, block_id=None)
        scene.audio_assets = [plain_asset]
        self.assertEqual(slot_of_asset(plain_asset, scene), "plain:2")
        self.assertTrue(is_slot_covered(scene, "plain:2"))
        self.assertFalse(is_slot_covered(scene, "plain:1"))

    def test_legacy_scope_synthesis_preserved(self):
        """§9 LEGACY: scenes WITHOUT an expected slot structure keep the
        documented compatibility path verbatim (block → "{block}:{n}",
        plain → "plain:{n}"); scene=None behaves the same."""
        from engine.audio_provenance import slot_of_asset
        legacy_scene = self.make_scene(slots=[])  # never materialised
        block_asset = self.asset(2, 1, block_id="ob2")
        plain_asset = self.asset(3, 1, block_id=None)
        self.assertEqual(slot_of_asset(block_asset, legacy_scene), "ob2:2")
        self.assertEqual(slot_of_asset(plain_asset, legacy_scene),
                         "plain:3")
        self.assertEqual(slot_of_asset(block_asset, None), "ob2:2")
        self.assertEqual(slot_of_asset(plain_asset, None), "plain:3")

    def test_full_scene_asset_still_not_a_slot(self):
        from engine.audio_provenance import slot_of_asset
        scene = self.make_scene(slots=[self.slot("b1:1", "b1", 1)])
        full_scene_asset = {
            "id": "full1", "output_path": "outputs/full.wav",
            "duration": 30.0, "block_id": None,
            "part_index": None, "part_version": None,
        }
        self.assertIsNone(slot_of_asset(full_scene_asset, scene))


class TestProvenanceIdentityMixing(_ProvenanceFactory):
    """§7/§10 scene-level reproduction: OLD audio + NEW structure.

    Generation A covers o1/o2/o3; the blocks are structurally edited so
    the rebuild produces DIFFERENT stable block identities; the OLD
    assets are still present (append-only stream). The derived
    coverage must NOT report the new structure as covered, and Combine
    must refuse to assemble old audio under the new structure.
    """

    OLD_SLOTS = None  # set per-test via _old_structure()

    def _old_structure(self):
        return [self.slot("ob1:1", "ob1", 1, "A"),
                self.slot("ob2:1", "ob2", 2, "B"),
                self.slot("ob3:1", "ob3", 3, "C")]

    def _new_structure(self):
        # The structural edit: blocks rebuilt → new stable identities.
        return [self.slot("nb1:1", "nb1", 1, "A'"),
                self.slot("nb2:1", "nb2", 2, "B'"),
                self.slot("nb3:1", "nb3", 3, "C'")]

    def _old_assets(self):
        return [self.asset(1, 1, block_id="ob1"),
                self.asset(2, 1, block_id="ob2"),
                self.asset(3, 1, block_id="ob3")]

    def test_old_assets_do_not_cover_new_structure(self):
        from engine.audio_provenance import (
            coverage_counts, scene_coverage_state, is_slot_covered,
            SCENE_NOT_GENERATED, block_slot_states)
        scene = self.make_scene(slots=self._new_structure(),
                                assets=self._old_assets())
        self.assertEqual(coverage_counts(scene), (0, 3),
                         "three OLD assets whose part_index values line "
                         "up with the three NEW slots must NOT count as "
                         "coverage (pre-P3.44.5: (3, 3))")
        self.assertEqual(scene_coverage_state(scene), SCENE_NOT_GENERATED)
        for s in scene.expected_audio_slots:
            self.assertFalse(is_slot_covered(scene, s["slot_id"]),
                             "slot {0} must be uncovered".format(
                                 s["slot_id"]))
        states = {b["block_id"]: b for b in block_slot_states(scene)}
        self.assertFalse(states["nb1"]["generated"])
        self.assertFalse(states["nb2"]["generated"])
        self.assertFalse(states["nb3"]["generated"])

    def test_combine_blockers_after_structural_change(self):
        """Combine must refuse — never silently assemble old audio under
        the new structure."""
        import tempfile
        root = tempfile.mkdtemp(prefix="ss_p3445_prov_")
        try:
            for a in self._old_assets():
                path = os.path.join(root, a["output_path"])
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(b"RIFF")
            scene = self.make_scene(slots=self._new_structure(),
                                    assets=self._old_assets())
            from engine.audio_provenance import resolved_slot_sources
            sources, blockers = resolved_slot_sources(scene, root)
            self.assertEqual(sources, [],
                             "no old asset may resolve as a source for "
                             "the new structure")
            self.assertEqual(len(blockers), 3,
                             "every new slot must be named as a blocker")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_partial_overlap_is_per_slot_precise(self):
        """A block whose identity SURVIVES the edit keeps its coverage;
        only the changed blocks lose theirs — the guard is per-slot
        identity, not a blanket rejection."""
        from engine.audio_provenance import coverage_counts
        new_slots = [self.slot("ob1:1", "ob1", 1, "A"),   # survived
                     self.slot("nb2:1", "nb2", 2, "B'"),  # changed
                     self.slot("nb3:1", "nb3", 3, "C'")]  # changed
        scene = self.make_scene(slots=new_slots,
                                assets=self._old_assets())
        self.assertEqual(coverage_counts(scene), (1, 3),
                         "the surviving block identity keeps its asset; "
                         "the two changed blocks do not")

    def test_valid_structure_still_fully_covered(self):
        """Control: the SAME structure + SAME assets → full coverage
        (nothing about valid matching changed)."""
        from engine.audio_provenance import (
            coverage_counts, scene_coverage_state, SCENE_COMPLETE)
        scene = self.make_scene(slots=self._old_structure(),
                                assets=self._old_assets())
        self.assertEqual(coverage_counts(scene), (3, 3))
        self.assertEqual(scene_coverage_state(scene), SCENE_COMPLETE)


class TestProvenanceIdentityMixingEndToEnd(unittest.TestCase):
    """§10 through the REAL writer + REAL slot materialisation.

    Generation A registers assets via register_generation_result; the
    structural edit rebuilds the expected slots via
    materialize_expected_slots with DIFFERENT block identities; BEFORE
    any new audio the provenance layer must report the new slots
    uncovered; after ONE new registration exactly that slot is covered.
    No provenance is hand-reused to make anything pass.
    """

    def setUp(self):
        from engine.models import Project, Scene
        self.project = Project(name="Eden", id="proj1")
        self.scene = Scene(id="sc1abcd", name="Scene 03",
                           project_id="proj1")
        self.project.add_scene(self.scene)

    def _register(self, block_id, part_index, version, run):
        from types import SimpleNamespace
        from engine.audio_provenance import register_generation_result
        result = SimpleNamespace(
            scene_id=self.scene.id,
            output_path="outputs/p{0}_v{1:02d}.wav".format(
                part_index, version),
            output_duration=2.0, speaker=None, character_id=None,
            voice_profile=None, timestamp="20260101_120000",
            block_id=block_id, part_index=part_index,
            part_version=version, generation_run=run,
        )
        return register_generation_result(self.project, result)

    def test_generation_a_structural_edit_generation_b(self):
        from engine.audio_provenance import (
            materialize_expected_slots, coverage_counts,
            scene_coverage_state, is_slot_covered, resolved_slot_sources,
            SCENE_COMPLETE, SCENE_NOT_GENERATED, SCENE_PARTIAL,
        )

        # --- Generation A: blocks A/B/C, audio for all three. -------
        old_parts = [_make_part(block_id="ob{0}".format(i),
                                block_label="ABC"[i - 1])
                     for i in (1, 2, 3)]
        self.scene.expected_audio_slots = materialize_expected_slots(
            old_parts)
        for i in (1, 2, 3):
            self._register("ob{0}".format(i), i, 1, "sc1abcd-r001")
        self.assertEqual(coverage_counts(self.scene), (3, 3))
        self.assertEqual(scene_coverage_state(self.scene), SCENE_COMPLETE)

        # --- Structural edit: blocks rebuilt with NEW identities. ---
        new_parts = [_make_part(block_id="nb{0}".format(i),
                                block_label="ABC"[i - 1])
                     for i in (1, 2, 3)]
        self.scene.expected_audio_slots = materialize_expected_slots(
            new_parts)

        # --- Generation B, BEFORE any new audio: old assets must not
        #     be accepted as matches merely because part_index lines up.
        self.assertEqual(coverage_counts(self.scene), (0, 3),
                         "old audio must not cover the new structure "
                         "(pre-P3.44.5 this was (3, 3))")
        self.assertEqual(scene_coverage_state(self.scene),
                         SCENE_NOT_GENERATED)
        for s in self.scene.expected_audio_slots:
            self.assertFalse(is_slot_covered(self.scene, s["slot_id"]))
        sources, blockers = resolved_slot_sources(self.scene, "/nonexistent")
        self.assertEqual(sources, [])
        self.assertEqual(len(blockers), 3)

        # --- One new registration: exactly that slot becomes covered.
        self._register("nb2", 2, 1, "sc1abcd-r002")
        self.assertEqual(coverage_counts(self.scene), (1, 3))
        self.assertEqual(scene_coverage_state(self.scene), SCENE_PARTIAL)
        self.assertTrue(is_slot_covered(self.scene, "nb2:1"))
        # The old assets are still in the append-only stream (history
        # is never destroyed) — they simply map to no current slot.
        self.assertEqual(len(self.scene.audio_assets), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
