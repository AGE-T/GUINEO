"""GUINEO P3.44.4 — BATCH EXECUTION RUN SEMANTICS
(runtime tests — the §12–§16 validation matrix).

Every test runs the REAL MainWindow + Engine + BatchManager +
BatchGenerationDialog offscreen (only the model boundary is faked; the
classes under test are never mocked).

The corrected state model under validation (spec §5):

  Global execution state (drives the controls):
    Idle / Running / Paused / Stopping / Finished
      — derived from BatchManager._running / _paused / _stop_requested.

  Per-job state (drives each row):
    Pending / Generating / Completed / Failed / Skipped
      — the actual state of THAT job.

  Execution run (P3.44.4 §2): the EXPLICIT set of job objects that
  participate in the current run (BatchManager.start(only=...);
  None = whole queue). Table membership is NOT run membership:

    * a job that was never selected stays PENDING while another job
      generates (§1) and after the run finishes;
    * Stop flips only the jobs semantically affected by the run (§6/§14);
    * when the run's last job completes, the manager deterministically
      reaches IDLE — Pause/Stop disable, no generating indicator, no
      stale flags (§3/§4/§10);
    * a late/obsolete callback or timer can never re-enable running,
      restore an old active job, start a PENDING job or mark another
      job Stopped (§11);
    * a second generation after a finished one starts from a CLEAN
      manager state (§16).

Documented STOP semantics (§14, encoded below):
    A (in flight, member)  -> Stopped (or Completed when the native call
                              already produced audio — the P3.30(d)
                              restore rule; a stop never destroys work)
    B, C (members, pending) -> Stopped
    D (table, never run)    -> Pending — UNTOUCHED

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_44_4_batch_execution_run.py -v
"""
import gc
import math
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from concurrent.futures import Future
from unittest.mock import patch, PropertyMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np

# Fake torch (house pattern).
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

from PySide6.QtWidgets import QApplication, QCheckBox

_app = QApplication.instance() or QApplication([])

from engine.engine import Engine
from engine.models import Project, Scene, GenerationResult
from engine.project_manager import ProjectManager
from engine.batch_manager import BatchManager, BatchJob, JobStatus
from ui.main_window import MainWindow
from ui.panels.batch_generation import AnimatedHourglass

SAMPLE_RATE = 24000


class FakeHiggsModel:
    """Model-boundary fake (P3.27B/P3.28 house pattern)."""
    calls = []
    speech_s = 0.25
    delay_s = 0.0

    def generate_speech(self, text, tokenizer, *, reference_audio=None,
                        reference_sample_rate=None, reference_codes=None,
                        reference_text=None, max_new_tokens=2048,
                        temperature=1.0, top_p=None, top_k=None):
        FakeHiggsModel.calls.append(dict(text=text))
        if FakeHiggsModel.delay_s:
            time.sleep(FakeHiggsModel.delay_s)
        n = int(FakeHiggsModel.speech_s * SAMPLE_RATE)
        t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
        return (0.2 * np.sin(2 * math.pi * 220 * t)).astype(np.float32)


def _make_part(text, speaker=None, block_id=None, est=8.0):
    from engine.narration_splitter import SplitPart
    return SplitPart(
        text=text, prompt=text, speaker=speaker or "",
        source_block_id=block_id, part_of_block=1,
        total_parts_in_block=1, block_label="",
        estimated_duration=est, char_count=len(text))


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def gen_pill_rows(dlg):
    """Rows whose VISIBLE status pill shows the generating hourglass."""
    rows = []
    for i in range(dlg._table.rowCount()):
        w = dlg._table.cellWidget(i, dlg._col_status)
        if w is not None and w.findChildren(AnimatedHourglass):
            rows.append(i)
    return rows


def pill_texts(dlg):
    from PySide6.QtWidgets import QLabel
    out = []
    for i in range(dlg._table.rowCount()):
        w = dlg._table.cellWidget(i, dlg._col_status)
        text = ""
        if w is not None:
            for lbl in w.findChildren(QLabel):
                if lbl.text():
                    text = lbl.text()
                    break
        out.append(text)
    return out


# ===========================================================================
# Harness — real MainWindow + Engine + ProjectManager (on-disk project
# under a tmp dir); model boundary faked only.
# ===========================================================================
class _Harness(unittest.TestCase):

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="ss_p3444_")
        self.engine = Engine(app_root=self.tmp)
        self.patchers = [
            patch.object(type(self.engine._model), "is_loaded",
                         new_callable=PropertyMock, return_value=True),
            patch.object(type(self.engine._model), "device",
                         new_callable=PropertyMock, return_value="cpu"),
            patch.object(type(self.engine._model),
                         "get_model_and_tokenizer",
                         return_value=(FakeHiggsModel(), None)),
            # Fast pacing for tests: the inter-job delay is a pacing
            # constant, not state-machine logic.
            patch.object(BatchManager, "INTER_JOB_DELAY_MS", 60),
        ]
        for p in self.patchers:
            p.start()
        self.engine.play_audio = lambda p: None
        self.win = MainWindow(self.engine)
        self.pm = ProjectManager(os.path.join(self.tmp, "projects"))
        self.win._project_manager = self.pm

        self.pa = Project(name="Alpha", id="projA")
        self.scene_a = Scene(id="scA00001", name="01 Scene",
                             project_id="projA")
        self.pa.add_scene(self.scene_a)
        self.pm.save_project(self.pa)
        self.win._active_project = self.pa
        self.win._active_scene = self.scene_a
        self.win._current_project = "Alpha"
        FakeHiggsModel.calls = []
        FakeHiggsModel.speech_s = 0.25
        FakeHiggsModel.delay_s = 0.0

    def tearDown(self):
        FakeHiggsModel.delay_s = 0.0
        try:
            if getattr(self, "win", None) is not None:
                try:
                    if self.win._batch_dialog is not None:
                        self.win._batch_dialog.close()
                except Exception:
                    pass
                try:
                    self.win.close()
                except Exception:
                    pass
                try:
                    self.win.deleteLater()
                except Exception:
                    pass
        except Exception:
            pass
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        try:
            _process(60)  # let deleteLater events execute
        except Exception:
            pass
        # Release the per-test heap (house pattern from P3.44.1).
        self.win = None
        self.engine = None
        self.pm = None
        self.pa = None
        self.scene_a = None
        self.tmp = None
        self.patchers = None
        gc.collect()

    # -- helpers ---------------------------------------------------------
    def params(self):
        from engine.models import GenerationParameters
        return GenerationParameters(
            temperature=0.95, top_p=0.95, top_k=300, max_new_tokens=4096,
            seed=None, append_silence=0.3, normalize_output=False,
            auto_play=False)

    def generate(self, texts, review=True):
        """Queue a scene batch (one job per text) via the production path."""
        parts = [_make_part(t) for t in texts]
        self.win._start_long_narration(parts, None, self.params(), {},
                                       review_mode=review)
        return self.win._batch_dialog

    def select_only(self, dlg, indices):
        """Set the authoritative checkbox state to EXACTLY ``indices``."""
        dlg._check_states = {i: (i in set(indices))
                             for i in range(dlg._table.rowCount())}

    def wait_batch(self, timeout=30):
        bm = self.win._batch_manager
        t0 = time.time()
        while bm.is_running and time.time() - t0 < timeout:
            _process(20)
        _process(250)

    def dialog(self):
        return self.win._batch_dialog

    def wait_status(self, job_idx, status, timeout=15):
        bm = self.win._batch_manager
        t0 = time.time()
        while bm.jobs[job_idx].status != status and time.time() - t0 < timeout:
            _process(20)
        return bm.jobs[job_idx].status == status


# ===========================================================================
# §1/§12 — single selected job: unselected rows untouched, manager idle
# ===========================================================================
class TestSingleSelectedJob(_Harness):

    def test_selected_completes_unselected_remain_pending(self):
        """THE §1 reproduction: 4 rows, select ONLY Row 2 and generate.

        Expected (new semantics): Row 2 Generating -> Completed; Rows
        1/3/4 remain PENDING — never "Stopped". Previously the second
        pass in MainWindow._on_generate_selected flipped every unchecked
        PENDING job to SKIPPED, rendering unrelated rows "Stopped"."""
        dlg = self.generate(["Row one text.", "Row two text.",
                             "Row three text.", "Row four text."])
        bm = self.win._batch_manager
        self.select_only(dlg, [1])
        dlg._on_start()

        # While the selected job runs, the unselected rows stay PENDING.
        self.wait_status(1, JobStatus.GENERATING)
        self.assertEqual(bm.jobs[1].status, JobStatus.GENERATING)
        for i in (0, 2, 3):
            self.assertEqual(bm.jobs[i].status, JobStatus.PENDING,
                             "unselected row {0} must stay Pending while "
                             "another row generates".format(i + 1))
        self.wait_batch()

        statuses = [j.status for j in bm.jobs]
        self.assertEqual(statuses, [JobStatus.PENDING, JobStatus.COMPLETED,
                                    JobStatus.PENDING, JobStatus.PENDING])
        # §12 assertions: no active generation, clean manager state.
        self.assertNotIn(JobStatus.GENERATING, statuses)
        self.assertFalse(bm.is_running)
        self.assertFalse(bm.is_paused)
        self.assertFalse(bm.stop_requested)
        self.assertIsNone(bm.current_index)
        self.assertIsNone(bm._current_job)
        self.assertIsNone(bm._run_jobs)
        # Controls: Pause/Stop disabled (driven by the global state).
        self.assertFalse(dlg._pause_btn.isEnabled(),
                         "Pause must disable once the run is idle")
        self.assertFalse(dlg._stop_btn.isEnabled(),
                         "Stop must disable once the run is idle")
        self.assertNotIn("Stopping", dlg._stop_btn.text())
        # No generating indicator; unselected pills show Queued (the
        # PENDING pill label — never "Stopped").
        self.assertEqual(gen_pill_rows(dlg), [])
        pills = pill_texts(dlg)
        for i in (0, 2, 3):
            self.assertNotIn("Stopped", pills[i],
                             "row {0} must not render Stopped".format(i + 1))
            self.assertIn("Queued", pills[i])
        self.assertIn("Done", pills[1])
        # Summary: not Running/Paused (§12 "Finished or equivalent").
        self.assertNotIn("Running", dlg._state_label.text())
        self.assertNotIn("Paused", dlg._state_label.text())
        # The table remains interactive.
        cb = dlg._table.cellWidget(0, dlg._col_check)._checkbox
        self.assertTrue(cb.isEnabled())
        for btn in (dlg._add_btn, dlg._edit_btn, dlg._remove_btn):
            self.assertTrue(btn.isEnabled())

    def test_unselected_rows_keep_pending_after_repeat_runs(self):
        """Two selective runs in a row: the never-selected rows survive
        BOTH runs untouched (still Pending, never Stopped)."""
        dlg = self.generate(["A text.", "B text.", "C text.", "D text."])
        bm = self.win._batch_manager
        self.select_only(dlg, [1])
        dlg._on_start()
        self.wait_batch()
        self.select_only(dlg, [2])
        dlg._on_start()
        self.wait_batch()
        statuses = [j.status for j in bm.jobs]
        self.assertEqual(statuses, [JobStatus.PENDING, JobStatus.COMPLETED,
                                    JobStatus.COMPLETED, JobStatus.PENDING])
        self.assertFalse(bm.is_running)
        pills = pill_texts(dlg)
        self.assertNotIn("Stopped", "".join(pills))


# ===========================================================================
# §13 — multiple selected jobs: only selected execute
# ===========================================================================
class TestMultipleSelectedJobs(_Harness):

    def test_abc_run_d_stays_pending_throughout(self):
        """A, B, C checked: they run sequentially; D (unchecked) remains
        PENDING AT EVERY SAMPLE — never Stopped, never STARTED — and the
        batch returns to idle after C (§13)."""
        dlg = self.generate(["A part text.", "B part text.",
                             "C part text.", "D part text."])
        bm = self.win._batch_manager
        self.select_only(dlg, [0, 1, 2])
        dlg._on_start()

        while bm.is_running:
            # D must never be scheduled NOR marked Stopped while the
            # run progresses through A, B and C.
            self.assertEqual(bm.jobs[3].status, JobStatus.PENDING,
                             "D was not part of the execution run — it "
                             "must stay Pending (got {0})".format(
                                 bm.jobs[3].status))
            self.assertIsNone(bm.jobs[3].started_at,
                              "D must never be started by a run it does "
                              "not belong to")
            _process(20)
        _process(250)

        # A, B, C each actually ran (started_at is the authoritative
        # bookkeeping — the fast fake model transitions between samples).
        for i in (0, 1, 2):
            self.assertIsNotNone(bm.jobs[i].started_at,
                                 "selected job {0} must have run".format(i))
            self.assertEqual(bm.jobs[i].status, JobStatus.COMPLETED)
        self.assertIsNone(bm.jobs[3].started_at,
                         "the never-selected job must never start")
        statuses = [j.status for j in bm.jobs]
        self.assertEqual(statuses, [JobStatus.COMPLETED] * 3
                         + [JobStatus.PENDING])
        self.assertFalse(bm.is_running)
        self.assertFalse(bm.is_paused)
        self.assertFalse(bm.stop_requested)
        self.assertFalse(dlg._pause_btn.isEnabled())
        self.assertFalse(dlg._stop_btn.isEnabled())
        self.assertEqual(gen_pill_rows(dlg), [])


# ===========================================================================
# §14 — Stop: only execution-relevant jobs are affected
# ===========================================================================
class TestStopSemantics(_Harness):

    def test_stop_selective_run_leaves_non_member_untouched(self):
        """A, B, C selected; Stop while A is generating:

        A   (in flight, member) -> Stopped (or Completed when the native
                                    call already produced audio — the
                                    P3.30(d) restore rule)
        B, C (members, pending)  -> Stopped
        D   (never in the run)   -> Pending — UNTOUCHED
        """
        FakeHiggsModel.delay_s = 0.4
        dlg = self.generate(["Stop A.", "Stop B.", "Stop C.", "Stop D."])
        bm = self.win._batch_manager
        self.select_only(dlg, [0, 1, 2])
        dlg._on_start()
        self.wait_status(0, JobStatus.GENERATING)
        self.assertEqual(bm.jobs[3].status, JobStatus.PENDING)

        dlg._on_stop()
        deadline = time.time() + 15
        while bm.is_running and time.time() < deadline:
            _process(50)

        self.assertFalse(bm.is_running, "the stopped run must settle")
        statuses = [j.status for j in bm.jobs]
        # A: stopped (the fake model call is cancelled cooperatively; if
        # audio landed anyway the P3.30(d) rule restores Completed).
        self.assertIn(statuses[0], (JobStatus.SKIPPED, JobStatus.COMPLETED))
        # B, C: members that never ran -> Stopped.
        self.assertEqual(statuses[1], JobStatus.SKIPPED)
        self.assertEqual(statuses[2], JobStatus.SKIPPED)
        # D: never part of the run -> untouched Pending.
        self.assertEqual(statuses[3], JobStatus.PENDING,
                         "D was not part of the execution run; a Stop of "
                         "that run must not mark it Stopped")
        # Clean manager state after the stop (§10).
        self.assertFalse(bm.stop_requested)
        self.assertFalse(bm.is_paused)
        self.assertIsNone(bm.current_index)
        self.assertFalse(dlg._stop_btn.isEnabled())
        self.assertNotIn("Stopping", dlg._stop_btn.text())
        FakeHiggsModel.delay_s = 0.0

    def test_stop_whole_queue_run_flips_all_pending(self):
        """§6: the established Stop behaviour for a GENUINE multi-job
        (whole-queue) execution is unchanged — every PENDING member
        flips to Stopped."""
        FakeHiggsModel.delay_s = 0.4
        self.win._on_open_batch_generation()
        dlg = self.win._batch_dialog
        bm = self.win._batch_manager
        for n in ("w1", "w2", "w3", "w4"):
            bm.add_job(BatchJob(name=n, prompt="prompt " + n,
                                project="Alpha"))
        dlg._refresh_table()
        bm.start()
        self.wait_status(0, JobStatus.GENERATING)

        dlg._on_stop()
        deadline = time.time() + 15
        while bm.is_running and time.time() < deadline:
            _process(50)

        self.assertFalse(bm.is_running)
        statuses = [j.status for j in bm.jobs]
        self.assertIn(statuses[0], (JobStatus.SKIPPED, JobStatus.COMPLETED))
        for i in (1, 2, 3):
            self.assertEqual(statuses[i], JobStatus.SKIPPED,
                             "whole-queue run: pending members must keep "
                             "flipping to Stopped")
        FakeHiggsModel.delay_s = 0.0

    def test_regen_run_stop_leaves_others_pending(self):
        """The regen execution run is exactly the one reset job: Stop of
        a regen run never touches the other PENDING rows."""
        FakeHiggsModel.delay_s = 0.4
        dlg = self.generate(["R one.", "R two.", "R three."])
        bm = self.win._batch_manager
        self.select_only(dlg, [1])
        dlg._on_start()
        self.wait_batch()
        self.assertEqual(bm.jobs[1].status, JobStatus.COMPLETED)
        # Let the worker fully finalize (audio save + history/asset
        # registration) before the regen start — under suite load a
        # still-busy engine turns the regen submit into a retry chain.
        _process(400)

        # Regenerate row 2 (the MainWindow production path).
        self.win._on_batch_regen(1)
        self.wait_status(1, JobStatus.GENERATING)
        self.assertEqual(bm.jobs[0].status, JobStatus.PENDING)
        self.assertEqual(bm.jobs[2].status, JobStatus.PENDING)

        dlg._on_stop()
        deadline = time.time() + 15
        while bm.is_running and time.time() < deadline:
            _process(50)
        statuses = [j.status for j in bm.jobs]
        self.assertIn(statuses[1], (JobStatus.SKIPPED, JobStatus.COMPLETED))
        self.assertEqual(statuses[0], JobStatus.PENDING,
                         "not part of the regen run — untouched")
        self.assertEqual(statuses[2], JobStatus.PENDING,
                         "not part of the regen run — untouched")
        self.assertFalse(bm.is_running)
        FakeHiggsModel.delay_s = 0.0


# ===========================================================================
# §3/§4/§15 — Pause and Resume
# ===========================================================================
class TestPauseResume(_Harness):

    def test_pause_resume_multi_job(self):
        """§15: pause while A is active; B/C stay Pending; resume lets
        B and C run; after C the manager is idle and the controls
        disable."""
        FakeHiggsModel.delay_s = 0.4
        dlg = self.generate(["Pause A.", "Pause B.", "Pause C."])
        bm = self.win._batch_manager
        self.select_only(dlg, [0, 1, 2])
        dlg._on_start()
        self.wait_status(0, JobStatus.GENERATING)

        dlg._on_pause()
        self.assertTrue(bm.is_paused)
        # Controls reflect the paused state.
        self.assertFalse(dlg._pause_btn.isEnabled())
        self.assertTrue(dlg._start_btn.isEnabled())

        # Let the in-flight job run out while paused (established
        # representation: the model call cannot be interrupted).
        deadline = time.time() + 15
        while bm.jobs[0].status == JobStatus.GENERATING \
                and time.time() < deadline:
            _process(50)
        _process(300)  # past the (patched) inter-job delay

        self.assertEqual(bm.jobs[0].status, JobStatus.COMPLETED)
        # B and C remain Pending; the run is paused WITH work remaining.
        self.assertEqual(bm.jobs[1].status, JobStatus.PENDING)
        self.assertEqual(bm.jobs[2].status, JobStatus.PENDING)
        self.assertTrue(bm.is_paused)
        self.assertTrue(bm.is_running)

        # Resume via the GENERATE/START button (P3.44.2 semantics).
        dlg._on_start()
        self.assertFalse(bm.is_paused)
        self.wait_batch()

        statuses = [j.status for j in bm.jobs]
        self.assertEqual(statuses, [JobStatus.COMPLETED] * 3)
        self.assertFalse(bm.is_running)
        self.assertFalse(bm.is_paused)
        self.assertFalse(dlg._pause_btn.isEnabled())
        self.assertFalse(dlg._stop_btn.isEnabled())
        self.assertEqual(gen_pill_rows(dlg), [])
        FakeHiggsModel.delay_s = 0.0

    def test_pause_single_job_finishes_when_exhausted(self):
        """§3/§4 hardening: pause while the ONLY member job is in
        flight; when it completes the run is EXHAUSTED — the batch must
        finish by itself (previously it stayed "running+paused" forever
        with nothing left to run, keeping Pause/Start/Stop stuck)."""
        FakeHiggsModel.delay_s = 0.4
        dlg = self.generate(["Solo one.", "Solo two.", "Solo three."])
        bm = self.win._batch_manager
        self.select_only(dlg, [0])
        dlg._on_start()
        self.wait_status(0, JobStatus.GENERATING)

        dlg._on_pause()
        self.assertTrue(bm.is_paused)

        # The single member job completes while paused.
        deadline = time.time() + 15
        while bm.jobs[0].status == JobStatus.GENERATING \
                and time.time() < deadline:
            _process(50)
        _process(600)  # past the (patched) inter-job delay

        # The exhausted run finished by itself — NO user intervention.
        self.assertEqual(bm.jobs[0].status, JobStatus.COMPLETED)
        for i in (1, 2):
            self.assertEqual(bm.jobs[i].status, JobStatus.PENDING)
        self.assertFalse(bm.is_running,
                         "an exhausted run is a finished run — even while "
                         "paused (the §3 stuck-controls defect)")
        self.assertFalse(bm.is_paused)
        self.assertFalse(dlg._pause_btn.isEnabled())
        self.assertFalse(dlg._stop_btn.isEnabled())
        self.assertEqual(gen_pill_rows(dlg), [])
        FakeHiggsModel.delay_s = 0.0


# ===========================================================================
# §16 — restart after completion
# ===========================================================================
class TestRestartAfterCompletion(_Harness):

    def test_second_generation_starts_from_clean_state(self):
        """After a single selective run finishes, the manager state is
        CLEAN (no running/paused/stop_requested/stale current job) and a
        second, differently-selected generation starts normally."""
        dlg = self.generate(["First sel.", "Second sel.",
                             "Third sel.", "Fourth sel."])
        bm = self.win._batch_manager
        self.select_only(dlg, [1])
        dlg._on_start()
        self.wait_batch()
        self.assertEqual(bm.jobs[1].status, JobStatus.COMPLETED)

        # The previous Finished state leaves NOTHING behind (§16).
        self.assertFalse(bm.is_running)
        self.assertFalse(bm.is_paused)
        self.assertFalse(bm.stop_requested)
        self.assertIsNone(bm.current_index)
        self.assertIsNone(bm._current_job)

        # Change the selection and start another generation.
        self.select_only(dlg, [2])
        started = dlg._on_start()
        self.wait_batch()
        statuses = [j.status for j in bm.jobs]
        self.assertEqual(statuses, [JobStatus.PENDING, JobStatus.COMPLETED,
                                    JobStatus.COMPLETED, JobStatus.PENDING])
        self.assertFalse(bm.is_running)
        self.assertFalse(bm.is_paused)
        self.assertFalse(bm.stop_requested)
        # The second generation actually ran (a fresh model call for it).
        self.assertGreaterEqual(len(FakeHiggsModel.calls), 2)


# ===========================================================================
# §11 — stale callbacks / obsolete invocations cannot corrupt the state
# ===========================================================================
class TestStaleCallbackProtection(_Harness):

    def _run_single(self):
        dlg = self.generate(["Stale one.", "Stale two.",
                             "Stale three.", "Stale four."])
        self.select_only(dlg, [1])
        dlg._on_start()
        self.wait_batch()
        return dlg

    def test_late_completion_callback_has_no_effect(self):
        """A completion callback firing AFTER the batch finished must
        not: re-enable running, restore an active job, mark another job
        Stopped, or attach its result anywhere."""
        dlg = self._run_single()
        bm = self.win._batch_manager
        before = [j.status for j in bm.jobs]
        self.assertEqual(before, [JobStatus.PENDING, JobStatus.COMPLETED,
                                  JobStatus.PENDING, JobStatus.PENDING])

        f = Future()
        f.set_result(GenerationResult(
            success=True, output_path="outputs/stale.wav",
            output_duration=1.0))
        bm._on_job_done(f)
        _process(400)  # let any marshalled/timer follow-ups land

        after = [j.status for j in bm.jobs]
        self.assertEqual(after, before,
                         "a stale callback must not change any job state")
        self.assertNotIn(JobStatus.GENERATING, after)
        self.assertFalse(bm.is_running)
        self.assertFalse(bm.stop_requested)
        self.assertIsNone(bm.current_index)
        self.assertIsNone(bm._current_job)
        self.assertFalse(dlg._pause_btn.isEnabled())
        self.assertFalse(dlg._stop_btn.isEnabled())
        # No row was marked Stopped by the stale callback.
        self.assertNotIn("Stopped", "".join(pill_texts(dlg)))

    def test_obsolete_process_next_never_starts_jobs(self):
        """A direct (obsolete) _process_next invocation while the batch
        is idle must NOT start a PENDING job — even though unselected
        PENDING jobs now remain in the table (the §11 requirement)."""
        dlg = self._run_single()
        bm = self.win._batch_manager

        bm._process_next()
        _process(300)

        statuses = [j.status for j in bm.jobs]
        self.assertEqual(statuses, [JobStatus.PENDING, JobStatus.COMPLETED,
                                    JobStatus.PENDING, JobStatus.PENDING])
        self.assertFalse(bm.is_running)
        self.assertIsNone(bm.current_index)
        self.assertEqual(gen_pill_rows(dlg), [])
        self.assertFalse(dlg._pause_btn.isEnabled())
        self.assertFalse(dlg._stop_btn.isEnabled())

    def test_stale_callback_then_new_run_still_works(self):
        """The idle state survives a stale callback AND a subsequent
        real run starts normally (§11 + §16 combined)."""
        dlg = self._run_single()
        bm = self.win._batch_manager

        f = Future()
        f.set_result(GenerationResult(
            success=True, output_path="outputs/stale2.wav",
            output_duration=1.0))
        bm._on_job_done(f)
        _process(300)

        self.select_only(dlg, [3])
        dlg._on_start()
        self.wait_batch()
        statuses = [j.status for j in bm.jobs]
        self.assertEqual(statuses, [JobStatus.PENDING, JobStatus.COMPLETED,
                                    JobStatus.PENDING, JobStatus.COMPLETED])
        self.assertFalse(bm.is_running)
        self.assertFalse(bm.is_paused)


if __name__ == "__main__":
    unittest.main()
