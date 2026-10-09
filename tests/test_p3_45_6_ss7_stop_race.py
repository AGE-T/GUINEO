"""GUINEO P3.45.6 — PHASE 1 / SS-7: PRE-START CANCELLATION RACE.

Regression suite for the stop/cancellation race PROVEN during the
P3.45.5 stabilisation work (3/30 reproductions; documented as
OPEN_BUGS SS-7) and fixed in P3.45.6 Phase 1:

    1. BatchManager._process_next marks a job GENERATING and submits it;
    2. the worker task is QUEUED but has not started executing yet;
    3. the user presses Stop: stop() flips the job SKIPPED (P3.30(d))
       and calls cancel_fn() -> WorkerPool.cancel_generation() ->
       Future.cancel(), which SUCCEEDS because the task never started;
    4. the future completes CANCELLED; _on_job_done's ``future.result()``
       raises concurrent.futures.CancelledError — an Exception subclass
       with an EMPTY str() — so the generic ``except Exception`` branch
       classified the user stop as FAILED with an empty error.

The fix classifies a pre-start CANCELLED future through the AUTHORITATIVE
stop semantics (stop() flips the run's GENERATING members to SKIPPED and
sets _stop_requested under the SAME lock, BEFORE calling cancel_fn):

    * cancelled future + stop signal  -> SKIPPED "Stopped by user."
      (the documented §14/P3.30(d) user-stop contract);
    * cancelled future, NO stop signal -> FAILED with an explicit
      message (an external cancellation — engine shutdown / a foreign
      cancel — is NEVER hidden as a user stop and NEVER a silent
      empty-error FAILED row);
    * a genuine worker exception       -> FAILED with the real message;
    * a cooperative in-flight cancel   -> SKIPPED (unchanged);
    * a stop racing a COMPLETING worker -> COMPLETED restore (unchanged).

Determinism rules (no blind fixed sleeps):
    * unit scenarios drive REAL ``concurrent.futures.Future`` objects
      whose completion the test controls exactly;
    * the real-executor scenario holds the single worker thread with an
      Event-gated blocker so the generation task is provably QUEUED
      (pre-start) when Stop lands — the exact vulnerable state;
    * every wait is a bounded condition loop.

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_45_6_ss7_stop_race.py -v
"""
import os
import sys
import time
import types
import threading
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Fake torch (house pattern — importing the engine package must not
# require a real torch installation).
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

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from engine.models import GenerationResult
from engine.batch_manager import BatchManager, BatchJob, JobStatus


def _process(ms=30):
    """Pump the Qt event loop so marshalled/timer follow-ups land."""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _wait_for(cond, timeout=10.0, what="condition"):
    """Bounded condition wait — never a blind sleep."""
    t0 = time.time()
    while not cond():
        if time.time() - t0 > timeout:
            raise AssertionError(
                "timeout after {0}s waiting for: {1}".format(timeout, what))
        time.sleep(0.005)


def _job(name, prompt=None):
    return BatchJob(name=name, prompt=prompt or name)


def _ok_result(path="out.wav"):
    return GenerationResult(success=True, output_path=path,
                            output_duration=1.0, generation_time=0.5)


class _ScriptedPool:
    """Emulates WorkerPool's two-layer cancel at the Future level.

    ``submit`` hands out REAL ``concurrent.futures.Future`` objects (the
    manager attaches its callback); ``cancel`` reproduces
    ``WorkerPool.cancel_generation()``: it calls ``Future.cancel()`` on
    the current generation future and returns its verdict — True when
    the task had not started (the SS-7 pre-start window).
    """

    def __init__(self):
        self.future = None
        self.requests = []
        self.submitted = threading.Event()
        # Emulates the executor's RUNNING state: once the task has
        # started, ``Future.cancel()`` returns False without cancelling
        # (the in-flight cooperative path takes over instead).
        self.cancel_succeeds = True

    def submit(self, request):
        self.requests.append(request)
        self.future = Future()
        self.submitted.set()
        return self.future

    def cancel(self):
        if self.future is None:
            return True
        if self.cancel_succeeds:
            return self.future.cancel()
        return False  # task already RUNNING — cooperative cancel only


class _MgrHarness(unittest.TestCase):
    """BatchManager driven directly through a scripted pool."""

    def setUp(self):
        # Fast pacing: the inter-job delay is a pacing constant, not
        # state-machine logic (house pattern).
        self._pacer = patch.object(BatchManager, "INTER_JOB_DELAY_MS", 20)
        self._pacer.start()
        self.pool = _ScriptedPool()
        self.mgr = BatchManager(
            submit_fn=self.pool.submit, cancel_fn=self.pool.cancel)

    def tearDown(self):
        self._pacer.stop()
        self.pool = None
        self.mgr = None

    def add_jobs(self, n):
        for i in range(n):
            self.mgr.add_job(_job("J{0}".format(i + 1)))

    def start_and_reach_submit(self, only=None):
        """start() and deterministically wait for the submit to happen."""
        self.assertTrue(self.mgr.start(only=only))
        _wait_for(lambda: self.pool.future is not None,
                  what="submit_fn to be called by _process_next")
        # The exact vulnerable SS-7 state: the job is GENERATING and the
        # future is still PENDING (cancellable — worker not started).
        self.assertEqual(self.mgr.jobs[0].status, JobStatus.GENERATING)
        self.assertFalse(self.pool.future.done())
        self.assertFalse(self.pool.future.cancelled())
        return self.pool.future


# ===========================================================================
# The seven contract scenarios (unit level, real Future machinery)
# ===========================================================================
class TestSS7PreStartCancelRace(_MgrHarness):

    def test_pre_start_cancel_renders_user_stop_not_failed(self):
        """THE regression: Stop in the submit-to-start window.

        The future completes CANCELLED (task never started) while the
        manager's stop is in effect — the job must end SKIPPED with the
        user-stop message, never a red FAILED row with an empty error.
        """
        self.add_jobs(2)
        future = self.start_and_reach_submit()

        # Stop lands in the pre-start window (deterministic: the
        # scripted future is still PENDING, so cancel() succeeds).
        self.mgr.stop()
        self.assertTrue(future.cancelled())

        job0, job1 = self.mgr.jobs
        self.assertIs(job0.status, JobStatus.SKIPPED,
                      "pre-start cancel must render as the user-stop "
                      "contract, got {0!r} (error={1!r})"
                      .format(job0.status, job0.error))
        self.assertEqual(job0.error, "Stopped by user.")
        self.assertIs(job1.status, JobStatus.SKIPPED)  # pending member

        # The batch deterministically finishes (bounded, event-driven).
        _wait_for(lambda: not self.mgr.is_running,
                  what="batch to finish after pre-start stop")
        _process(80)
        self.assertFalse(self.mgr.is_running)
        self.assertIs(job0.status, JobStatus.SKIPPED)

    def test_external_cancel_without_stop_stays_failed_with_message(self):
        """A cancelled future with NO stop signal is not blanket-SKIPPED.

        Proves the fix does not globally translate every CancelledError
        to SKIPPED: a foreign cancellation (engine shutdown / a cancel
        from outside this run's stop) stays a visible non-success with
        an explicit message — never a silent empty-error FAILED row and
        never a hidden user-stop.
        """
        self.add_jobs(1)
        future = self.start_and_reach_submit()

        # External cancel — no manager stop: the job is still GENERATING
        # and _stop_requested is False when the callback classifies.
        self.assertTrue(future.cancel())

        job0 = self.mgr.jobs[0]
        self.assertIs(job0.status, JobStatus.FAILED)
        self.assertEqual(job0.error,
                         "Generation was cancelled before it started.")
        self.assertNotEqual(job0.error, "")  # never the empty-error row
        _process(80)  # drain the inter-job timer follow-ups

    def test_inflight_cooperative_cancel_renders_user_stop(self):
        """Started task + cooperative cancel: unchanged P3.30(d) path."""
        self.add_jobs(1)
        future = self.start_and_reach_submit()

        # The worker started and the pipeline hit a safe checkpoint:
        # it returns a cancelled GenerationResult (result-based cancel).
        future.set_result(GenerationResult(success=False, cancelled=True))

        job0 = self.mgr.jobs[0]
        self.assertIs(job0.status, JobStatus.SKIPPED)
        self.assertEqual(job0.error, "Stopped by user.")
        _process(80)  # drain the inter-job timer follow-ups
        """A real worker exception must never be hidden by the fix."""
        self.add_jobs(1)
        future = self.start_and_reach_submit()

        future.set_exception(RuntimeError("model exploded"))

        job0 = self.mgr.jobs[0]
        self.assertIs(job0.status, JobStatus.FAILED)
        self.assertIn("model exploded", job0.error)

    def test_idle_stop_is_noop(self):
        """Stop on an idle manager changes nothing (P3.44.1 contract)."""
        self.add_jobs(2)
        self.mgr.stop()
        for j in self.mgr.jobs:
            self.assertIs(j.status, JobStatus.PENDING)
        self.assertFalse(self.mgr.is_running)
        self.assertFalse(self.mgr.stop_requested)

    def test_stop_selective_run_member_and_nonmember(self):
        """§14: A (pre-start in flight) + B (pending member) -> SKIPPED,
        D (table, never in the run) -> PENDING."""
        self.add_jobs(3)
        future = self.start_and_reach_submit(only=[0, 1])

        self.mgr.stop()
        self.assertTrue(future.cancelled())

        a, b, d = self.mgr.jobs
        self.assertIs(a.status, JobStatus.SKIPPED)
        self.assertEqual(a.error, "Stopped by user.")
        self.assertIs(b.status, JobStatus.SKIPPED)
        self.assertIs(d.status, JobStatus.PENDING)

    def test_callback_idempotency_pre_start_cancel(self):
        """A duplicated done-callback must not corrupt the stopped state."""
        self.add_jobs(1)
        future = self.start_and_reach_submit()
        self.mgr.stop()
        job0 = self.mgr.jobs[0]
        self.assertIs(job0.status, JobStatus.SKIPPED)

        # Late/duplicate delivery of the same cancelled future.
        self.mgr._on_job_done(future)
        self.mgr._on_job_done(future)

        self.assertIs(job0.status, JobStatus.SKIPPED)
        self.assertNotEqual(job0.error, "")
        self.assertFalse(self.mgr.is_running)
        _process(80)  # drain the inter-job timer follow-ups

    def test_worker_completion_racing_stop_restores_completed(self):
        """Stop lands after submit; the worker finishes with audio anyway.

        P3.30(d) restore rule — a stop never destroys completed work. The
        task has STARTED (Future.cancel() returns False — the executor's
        RUNNING state), completes with a successful result after the
        stop flipped the job SKIPPED.
        """
        self.add_jobs(1)
        future = self.start_and_reach_submit()

        # The task has started: cancel can no longer succeed.
        self.pool.cancel_succeeds = False
        self.mgr.stop()
        self.assertFalse(future.cancelled())

        future.set_result(_ok_result("kept.wav"))

        job0 = self.mgr.jobs[0]
        self.assertIs(job0.status, JobStatus.COMPLETED)
        self.assertEqual(job0.output_path, "kept.wav")
        self.assertIsNone(job0.error)
        _process(80)
        self.assertFalse(self.mgr.is_running)


# ===========================================================================
# Real-executor integration: the exact vulnerable state through a REAL
# ThreadPoolExecutor — the generation task is provably QUEUED (pre-start)
# when Stop lands, then the real Future.cancel() → real CancelledError
# path runs end-to-end.
# ===========================================================================
class TestRealExecutorPreStartCancel(unittest.TestCase):

    def setUp(self):
        self._pacer = patch.object(BatchManager, "INTER_JOB_DELAY_MS", 20)
        self._pacer.start()

    def tearDown(self):
        self._pacer.stop()

    def _run_scenario(self):
        """One full pre-start-stop cycle on a real single-worker pool.

        Returns (job_final_status, job_error, worker_started_flag).
        """
        executor = ThreadPoolExecutor(max_workers=1,
                                      thread_name_prefix="ss7-test")
        blocker = threading.Event()      # holds the ONLY worker thread
        started = threading.Event()      # set iff the gen task RAN
        submitted = threading.Event()
        state = {"future": None}

        def blocker_task():
            blocker.wait(timeout=30)
            return None

        def gen_task(_request):
            started.set()
            return _ok_result("never.wav")

        def submit_fn(request):
            fut = executor.submit(gen_task, request)
            state["future"] = fut
            submitted.set()
            return fut

        def cancel_fn():
            fut = state["future"]
            if fut is None:
                return True
            return fut.cancel()

        # The blocker occupies the single worker: the generation task
        # submitted after it is QUEUED and cannot start (deterministic
        # pre-start state — the SS-7 window, widened deterministically).
        executor.submit(blocker_task)

        mgr = BatchManager(submit_fn=submit_fn, cancel_fn=cancel_fn)
        mgr.add_job(_job("R1"))
        self.assertTrue(mgr.start())
        _wait_for(submitted.is_set, what="real executor submit")

        # Provable pre-start: the task cannot run while the blocker
        # holds the worker.
        self.assertFalse(started.is_set())
        self.assertFalse(state["future"].done())

        # STOP in the window.
        mgr.stop()

        # Release the blocker and drain the pool.
        blocker.set()
        executor.shutdown(wait=True)

        _wait_for(lambda: not mgr.is_running,
                  what="batch finish after real pre-start cancel")
        _process(80)

        job = mgr.jobs[0]
        return job.status, job.error, started.is_set()

    def test_real_queued_future_cancel_stop_semantics(self):
        status, error, worker_started = self._run_scenario()
        self.assertIs(
            status, JobStatus.SKIPPED,
            "real-executor pre-start cancel must render as the user "
            "stop, got {0!r} (error={1!r})".format(status, error))
        self.assertEqual(error, "Stopped by user.")
        self.assertFalse(worker_started,
                         "the cancelled task must never have started")

    def test_race_repeated_under_cpu_contention(self):
        """20 repetitions while two CPU burners compete for the GIL/cores.

        Stability proof for the race fix under load (the original 3/30
        flake surfaced only under suite load). Bounded by construction:
        each iteration is event-driven, never a blind sleep.
        """
        stop_burn = threading.Event()

        def burn():
            x = 0
            while not stop_burn.is_set():
                x = (x * 1103515245 + 12345) & 0x7FFFFFFF

        burners = [threading.Thread(target=burn, daemon=True)
                   for _ in range(2)]
        for t in burners:
            t.start()
        try:
            for i in range(20):
                status, error, worker_started = self._run_scenario()
                self.assertIs(
                    status, JobStatus.SKIPPED,
                    "iteration {0}: pre-start stop must be SKIPPED, got "
                    "{1!r} (error={2!r})".format(i, status, error))
                self.assertEqual(error, "Stopped by user.")
                self.assertFalse(worker_started)
        finally:
            stop_burn.set()
            for t in burners:
                t.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
