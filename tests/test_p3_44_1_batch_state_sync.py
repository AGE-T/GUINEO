"""GUINEO P3.44.1 — BATCH UI STATE SYNCHRONISATION AND UNSAVED SCENE DATA
PRESERVATION (runtime tests).

Spec §12 coverage — every test runs the REAL MainWindow + Engine +
BatchManager + BatchGenerationDialog offscreen (only the model boundary
is faked; the classes under test are never mocked):

Selection
  All / None / individual toggles / refresh during a run / after
  generation — the VISIBLE QCheckBox state must always equal the
  authoritative ``_check_states``.

Single job lifecycle
  PENDING -> GENERATING -> COMPLETED -> IDLE with Pause/Stop/selection/
  summary/pill states correct WITHOUT any user intervention.

Multiple sequential jobs
  The generating indicator follows the AUTHORITATIVE active job
  (``manager.current_index``), never the first incomplete row, and no
  row stays generating after the batch ends.

Pause and Resume / Stop
  Controls always correspond to the manager state; the
  stop-while-paused-between-jobs DEADLOCK (running stuck True forever)
  is regression-pinned; start() works afterwards.

In place widget identity
  State-only updates never rebuild the table (same row/checkbox/action
  widget objects).

Cross-contamination
  A generation result is never attached to a job that did not run
  (identity guard); a replaced queue does not auto-run without start().

Project switching with unsaved Scene
  New project + substantial text + Narration Blocks, NOT saved, batch
  running, switch away and back — everything survives; repeated
  A->B->A->B->A and A->B->C->A; an existing saved Scene modified
  without saving keeps the modification; the DISK file is not silently
  rewritten; the batch uses the CURRENT editor text (not disk).

Thread safety
  All manager-driven UI updates execute on the UI thread (Qt signal
  marshalling preserved).

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_44_1_batch_state_sync.py -v
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

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel

_app = QApplication.instance() or QApplication([])

from unittest.mock import patch, PropertyMock

from engine.engine import Engine
from engine.models import Project, Scene, Character
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


def _make_part(text, speaker=None, block_id=None, est=8.0,
               character_id=None):
    from engine.narration_splitter import SplitPart
    return SplitPart(
        text=text, prompt=text, speaker=speaker or "",
        source_block_id=block_id, part_of_block=1,
        total_parts_in_block=1, block_label="",
        character_id=character_id, estimated_duration=est,
        char_count=len(text))


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def visible_checks(dlg):
    """The VISIBLE checkbox check state per row."""
    out = []
    for i in range(dlg._table.rowCount()):
        w = dlg._table.cellWidget(i, dlg._col_check)
        cb = getattr(w, "_checkbox", None) if w else None
        out.append(cb.isChecked() if isinstance(cb, QCheckBox) else None)
    return out


def internal_checks(dlg, n=None):
    # P3.45.4 (documented update): _check_states is JOB-KEYED (slot id for
    # structural jobs, object identity for manual rows) — derive each
    # row's state through the job at that row, exactly as the production
    # checkbox sync does. The visible/internal equality contract this
    # suite pins is unchanged.
    n = n if n is not None else dlg._table.rowCount()
    return [bool(dlg._check_states.get(
        dlg._check_key(dlg._manager.jobs[i]), False)) for i in range(n)]


def job_key(dlg, row):
    """P3.45.4 (documented update): the check-state key of the job at
    ``row`` — replaces the pre-P3.45.4 raw row-index writes."""
    return dlg._check_key(dlg._manager.jobs[row])


def gen_pill_rows(dlg):
    """Rows whose VISIBLE status pill shows the generating hourglass."""
    rows = []
    for i in range(dlg._table.rowCount()):
        w = dlg._table.cellWidget(i, dlg._col_status)
        if w is not None and w.findChildren(AnimatedHourglass):
            rows.append(i)
    return rows


def pill_texts(dlg):
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
# Harness — real MainWindow + Engine + REAL ProjectManager (on-disk
# projects under a tmp dir); model boundary faked only.
# ===========================================================================
class _Harness(unittest.TestCase):

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="ss_p3441_")
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

        # Project A (active) + B + C, all persisted on disk.
        self.pa = Project(name="Alpha", id="projA")
        self.scene_a = Scene(id="scA00001", name="01 Scene",
                              project_id="projA")
        self.char_a = Character(name="Guide", id="chA00001")
        self.scene_a.character_ids = [self.char_a.id]
        self.pa.add_scene(self.scene_a)
        self.pa.add_character(self.char_a)
        self.pm.save_project(self.pa)

        self.pb = Project(name="Beta", id="projB")
        self.scene_b = Scene(id="scB00001", name="Scene B",
                              project_id="projB")
        self.pb.add_scene(self.scene_b)
        self.pm.save_project(self.pb)

        self.pc = Project(name="Gamma", id="projC")
        self.scene_c = Scene(id="scC00001", name="Scene C",
                              project_id="projC")
        self.pc.add_scene(self.scene_c)
        self.pm.save_project(self.pc)

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
                # P3.44.1 (suite memory, root cause): unittest keeps every
                # TestCase INSTANCE (and its attributes) alive until the
                # END of the whole suite run — a closed-but-referenced
                # MainWindow keeps its entire C++ widget tree, engine,
                # project set and batch-dialog heap resident (~40 MB per
                # test). 23 such tests ≈ +0.9 GB at full-suite scale,
                # which OOM-killed the whole run at ~85% progress on the
                # 4 GB host (runtime-verified: anon-rss 2.0→2.98 GB in
                # ~35 s while this file's tests ran). close() only HIDES
                # a top-level widget; the references are what keep it
                # allocated. deleteLater + event-loop flush lets the C++
                # side actually free; dropping the attributes releases
                # the Python-side wrappers.
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
        # Release the per-test heap: these attributes are the only
        # remaining strong references once the test body is done.
        self.win = None
        self.engine = None
        self.pm = None
        self.pa = None
        self.pb = None
        self.pc = None
        self.scene_a = None
        self.scene_b = None
        self.scene_c = None
        self.char_a = None
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

    def start_long(self, parts, review=True):
        self.win._start_long_narration(parts, None, self.params(), {},
                                       review_mode=review)

    def generate(self, texts, review=True):
        """Queue a scene batch (one job per text) via the production path."""
        parts = [_make_part(t) for t in texts]
        self.start_long(parts, review=review)
        return self.win._batch_dialog

    def wait_batch(self, timeout=30):
        bm = self.win._batch_manager
        t0 = time.time()
        while bm.is_running and time.time() - t0 < timeout:
            _process(20)
        _process(250)

    def dialog(self):
        return self.win._batch_dialog

    def sample_consistent(self, dlg, bm):
        """Read (pill GEN rows, jobs statuses, current_index) with a
        confirmation pass that eliminates read-interleave artifacts:
        a mismatch is only reported when it persists across two
        immediately consecutive samples (a truly stuck pill persists;
        a transition mid-read is gone on the second sample)."""
        def _read():
            pills = gen_pill_rows(dlg)
            jobs = bm.jobs
            statuses = [j.status for j in jobs]
            ci = bm.current_index
            return pills, statuses, ci
        first = _read()
        if first[0] == [i for i, s in enumerate(first[1])
                        if s == JobStatus.GENERATING]:
            return first, True
        _process(60)
        second = _read()
        ok = second[0] == [i for i, s in enumerate(second[1])
                           if s == JobStatus.GENERATING]
        return second, ok


BIG_TEXT = (
    "The signal repeats across the ridge, patient and low.\n"
    "Nobody in the camp wants to name it aloud, because naming a thing\n"
    "gives it permission to be real, and this one has not yet earned\n"
    "that permission. Marta counts the seconds between pulses the way\n"
    "her mother taught her: not for superstition, but for arithmetic.\n"
    "If the interval shrinks, the source is approaching. If it holds,\n"
    "it is only the mountain breathing, and the mountain has breathed\n"
    "for longer than any of us have kept records."
)


# ===========================================================================
# §12 Selection — the visible checkbox IS the selection state
# ===========================================================================
class TestSelectionSync(_Harness):

    def test_all_and_none_sync_visible_checkboxes(self):
        """D1: All/None change _check_states; the VISIBLE QCheckBoxes
        must follow (previously the in-place refresh only touched the
        ENABLE state — internal True, visible False)."""
        dlg = self.generate(["Part one text.", "Part two text.",
                             "Part three text."])
        self.assertEqual(internal_checks(dlg), [True, True, True])
        self.assertEqual(visible_checks(dlg), [True, True, True])

        dlg._on_select_none()
        self.assertEqual(internal_checks(dlg), [False, False, False])
        self.assertEqual(visible_checks(dlg), [False, False, False],
                         "visible checkbox state must equal the internal "
                         "selection state after None")

        dlg._on_select_all()
        self.assertEqual(internal_checks(dlg), [True, True, True])
        self.assertEqual(visible_checks(dlg), [True, True, True],
                         "visible checkbox state must equal the internal "
                         "selection state after All")

    def test_individual_toggle_and_refresh_sync(self):
        dlg = self.generate(["Alpha part.", "Beta part.", "Gamma part."])
        # User unchecks row 1 (via the checkbox widget itself).
        w = dlg._table.cellWidget(1, dlg._col_check)
        w._checkbox.click()
        self.assertEqual(internal_checks(dlg), [True, False, True])
        self.assertEqual(visible_checks(dlg), [True, False, True])
        # A structure-unchanged refresh must keep the visible states.
        dlg._refresh_table()
        self.assertEqual(visible_checks(dlg), [True, False, True])
        # Programmatic state change + refresh -> visible follows.
        dlg._check_states[job_key(dlg, 2)] = False
        dlg._refresh_table()
        self.assertEqual(visible_checks(dlg), [True, False, False])

    def test_selection_survives_generation(self):
        """After a full run the selection is intact and re-enabled."""
        dlg = self.generate(["First part.", "Second part.", "Third part."])
        dlg._on_select_none()
        dlg._check_states[job_key(dlg, 0)] = True
        dlg._refresh_table()
        self.assertEqual(visible_checks(dlg), [True, False, False])
        dlg._on_start()  # generate only row 0
        self.wait_batch()
        self.assertEqual(self.win._batch_manager.is_running, False)
        self.assertEqual(visible_checks(dlg), [True, False, False])
        cb = dlg._table.cellWidget(0, dlg._col_check)._checkbox
        self.assertTrue(cb.isEnabled(), "checkboxes re-enable when idle")

    def test_refresh_during_run_preserves_selection(self):
        dlg = self.generate(["Part one.", "Part two.", "Part three.",
                             "Part four."])
        dlg._on_select_none()
        dlg._check_states[job_key(dlg, 2)] = True
        dlg._refresh_table()
        dlg._on_start()  # only row 2 generates
        # refresh the table mid-run the way refresh_scene_context does
        dlg._refresh_table()
        self.assertEqual(visible_checks(dlg), [False, False, True, False])
        self.wait_batch()
        self.assertEqual(visible_checks(dlg), [False, False, True, False])


# ===========================================================================
# §12 Single job lifecycle — PENDING -> GENERATING -> COMPLETED -> IDLE
# ===========================================================================
class TestSingleJobLifecycle(_Harness):

    def test_single_job_returns_to_idle_without_user_action(self):
        """D2: after ONE selected job completes, the dialog must reach
        the correct idle state by itself (Pause/Stop disabled, Stop text
        reset, checkboxes enabled, summary finished, no stale running)."""
        dlg = self.generate(["A single selected part."])
        bm = self.win._batch_manager
        dlg._on_start()

        saw_generating = False
        t0 = time.time()
        while bm.is_running and time.time() - t0 < 15:
            if JobStatus.GENERATING in [j.status for j in bm.jobs]:
                saw_generating = True
            _process(20)
        self.assertTrue(saw_generating, "job must pass through GENERATING")
        self.wait_batch()

        self.assertEqual([j.status for j in bm.jobs],
                         [JobStatus.COMPLETED])
        self.assertFalse(bm.is_running, "manager must be idle")
        self.assertFalse(dlg._pause_btn.isEnabled(),
                         "Pause disabled at idle")
        self.assertFalse(dlg._stop_btn.isEnabled(), "Stop disabled at idle")
        self.assertNotIn("Stopping", dlg._stop_btn.text())
        self.assertIn("Finished", dlg._state_label.text())
        self.assertIn("1/1", dlg._progress_text.text())
        cb = dlg._table.cellWidget(0, dlg._col_check)._checkbox
        self.assertTrue(cb.isEnabled())
        for btn in (dlg._add_btn, dlg._edit_btn, dlg._remove_btn):
            self.assertTrue(btn.isEnabled(),
                            "table actions re-enable when idle")


# ===========================================================================
# §12 Multiple sequential jobs — the indicator follows the REAL job
# ===========================================================================
class TestMultiJobIndicator(_Harness):

    def test_indicator_follows_authoritative_current_index(self):
        """D3: at every sample, exactly the manager.current_index row (or
        no row) shows the generating pill; the pill set always equals the
        jobs actually in GENERATING state; nothing is left at the end."""
        # Give each model call real duration so the GENERATING windows
        # are observable (the fake model call is otherwise ~instant).
        FakeHiggsModel.delay_s = 0.35
        try:
            dlg = self.generate(["Part {0} of the indicator run.".format(i)
                                 for i in range(1, 5)])
            bm = self.win._batch_manager
            dlg._on_start()

            mismatches = 0
            saw = set()
            t0 = time.time()
            while bm.is_running and time.time() - t0 < 30:
                (pills, statuses, ci), ok = self.sample_consistent(dlg, bm)
                if not ok:
                    mismatches += 1
                if ci is not None:
                    saw.add(ci)
                    if not ok or pills != [ci]:
                        self.fail(
                            "generating indicator does not follow the real "
                            "job: pills={0} current_index={1}".format(pills, ci))
                _process(30)
            self.wait_batch()

            self.assertEqual(mismatches, 0,
                             "the visible pill set must always equal the set "
                             "of GENERATING jobs")
            self.assertEqual(saw, {0, 1, 2, 3},
                             "every job must have been the active one")
            self.assertEqual(gen_pill_rows(dlg), [],
                             "no row may show generating after the batch ends")
            self.assertEqual(pill_texts(dlg), ["Done"] * 4)
            self.assertEqual([j.status for j in bm.jobs],
                             [JobStatus.COMPLETED] * 4)
        finally:
            FakeHiggsModel.delay_s = 0.0

    def test_indicator_moves_off_completed_row(self):
        """The exact user scenario: row1 done, row2 generating; when row2
        completes and row3 starts, row2's pill must NOT stay generating
        (stuck-on-first-incomplete-row defect)."""
        FakeHiggsModel.delay_s = 0.35
        try:
            dlg = self.generate(["Row one.", "Row two.", "Row three.",
                                 "Row four."])
            bm = self.win._batch_manager
            dlg._on_start()
            deadline = time.time() + 25
            observed = False
            while time.time() < deadline:
                _process(30)
                if bm.current_index == 2:
                    (pills, _st, _ci), ok = self.sample_consistent(dlg, bm)
                    if ok:
                        observed = True
                        self.assertEqual(pills, [2],
                                         "while job 3 generates, ONLY row 3 "
                                         "shows the indicator (row 2 must have "
                                         "cleared)")
                        break
            self.assertTrue(observed, "job 3 never became the active job")
            self.wait_batch()
        finally:
            FakeHiggsModel.delay_s = 0.0


# ===========================================================================
# §12 Pause / Resume / Stop — controls == manager state, no deadlock
# ===========================================================================
class TestPauseResumeStop(_Harness):

    def test_pause_resume_completion(self):
        FakeHiggsModel.delay_s = 0.9
        dlg = self.generate(["Pause part one.", "Pause part two."])
        bm = self.win._batch_manager
        dlg._on_start()
        _process(150)

        dlg._on_pause()
        self.assertTrue(bm.is_paused)
        self.assertFalse(dlg._pause_btn.isEnabled())
        self.assertTrue(dlg._start_btn.isEnabled(),
                        "Start/Resume available while paused")

        dlg._on_pause() if False else None
        # resume via the manager path (the Start button emits the same
        # resume semantics for a paused manual queue; scene mode uses
        # the manager directly)
        bm.resume()
        _process(100)
        self.assertFalse(bm.is_paused)
        self.wait_batch(timeout=25)
        self.assertFalse(bm.is_running)
        self.assertEqual([j.status for j in bm.jobs],
                         [JobStatus.COMPLETED] * 2)
        self.assertFalse(dlg._pause_btn.isEnabled())
        FakeHiggsModel.delay_s = 0.0

    def test_stop_while_running_settles(self):
        FakeHiggsModel.delay_s = 0.7
        dlg = self.generate(["Stop part one.", "Stop part two.",
                             "Stop part three."])
        bm = self.win._batch_manager
        dlg._on_start()
        _process(200)
        dlg._on_stop()
        deadline = time.time() + 15
        while bm.is_running and time.time() < deadline:
            _process(100)
        self.assertFalse(bm.is_running, "batch must settle after Stop")
        self.assertEqual(dlg._stop_btn.text(), "Stop")
        self.assertFalse(dlg._stop_btn.isEnabled())
        statuses = [j.status for j in bm.jobs]
        for s in statuses:
            self.assertIn(s, (JobStatus.COMPLETED, JobStatus.SKIPPED))
        FakeHiggsModel.delay_s = 0.0

    def test_stop_while_paused_between_jobs_settles_immediately(self):
        """D4 deadlock regression: Pause during a run, let the in-flight
        job finish (paused BETWEEN jobs), press Stop — the batch must go
        idle IMMEDIATELY (previously _running stayed True forever and
        every later start() silently failed)."""
        FakeHiggsModel.delay_s = 0.4
        dlg = self.generate(["Deadlock part one.", "Deadlock part two.",
                             "Deadlock part three."])
        bm = self.win._batch_manager
        dlg._on_start()
        _process(120)
        dlg._on_pause()
        # let the in-flight job run out while paused
        deadline = time.time() + 10
        while bm.current_index is not None and time.time() < deadline:
            _process(50)
        _process(400)  # past the (patched) inter-job delay
        self.assertIsNone(bm.current_index)
        self.assertTrue(bm.is_running, "paused between jobs: still running")

        dlg._on_stop()
        self.assertFalse(bm.is_running,
                         "Stop while paused-between-jobs must finish the "
                         "batch AT ONCE (the old deadlock left it stuck "
                         "running forever)")
        self.assertEqual(dlg._stop_btn.text(), "Stop")
        FakeHiggsModel.delay_s = 0.0

    def test_start_works_after_deadlock_stop(self):
        """After the deadlock recovery, a new batch must start (the old
        stuck _running made start() return False silently)."""
        FakeHiggsModel.delay_s = 0.4
        dlg = self.generate(["Recovery part one.", "Recovery part two."])
        bm = self.win._batch_manager
        dlg._on_start()
        _process(120)
        dlg._on_pause()
        deadline = time.time() + 10
        while bm.current_index is not None and time.time() < deadline:
            _process(50)
        _process(400)
        dlg._on_stop()
        self.assertFalse(bm.is_running)

        # user selects everything again and starts a fresh run. The
        # engine's disk guard refuses to overwrite the previous run's
        # files, so — exactly like a production regeneration — fresh
        # output filenames are allocated before restarting.
        dlg._on_select_all()
        for i, job in enumerate(bm.jobs):
            job.status = JobStatus.PENDING
            job.error = None
            job.output_filename = "restart_{0}_v2.wav".format(i)
            job.output_path = None
        started = bm.start()
        self.assertTrue(started, "start() must succeed after the recovery")
        self.wait_batch(timeout=25)
        self.assertEqual([j.status for j in bm.jobs],
                         [JobStatus.COMPLETED] * 2)
        FakeHiggsModel.delay_s = 0.0


# ===========================================================================
# §12 In-place widget identity — normal updates do NOT rebuild the table
# ===========================================================================
class TestInPlaceWidgetIdentity(_Harness):

    def test_state_only_update_keeps_widget_objects(self):
        dlg = self.generate(["Identity part one.", "Identity part two."])
        # complete a run so a job has a non-trivial state
        dlg._on_select_none()
        dlg._check_states[job_key(dlg, 0)] = True
        dlg._refresh_table()
        dlg._on_start()
        self.wait_batch()

        check0 = dlg._table.cellWidget(0, dlg._col_check)._checkbox
        act0 = dlg._row_refs[0]["actions"]
        act1 = dlg._row_refs[1]["actions"]

        rebuild_calls = []
        orig_rebuild = dlg._rebuild_table
        dlg._rebuild_table = lambda jobs: (
            rebuild_calls.append(1), orig_rebuild(jobs))[1]

        # a structure-unchanged manager update (status/summary only)
        dlg._check_states[job_key(dlg, 1)] = True
        dlg._refresh_table()

        self.assertEqual(rebuild_calls, [],
                         "a state-only refresh must NOT rebuild the table")
        self.assertIs(dlg._table.cellWidget(0, dlg._col_check)._checkbox,
                      check0, "checkbox widget identity preserved")
        self.assertIs(dlg._row_refs[0]["actions"], act0)
        self.assertIs(dlg._row_refs[1]["actions"], act1)
        dlg._rebuild_table = orig_rebuild

    def test_refresh_scene_context_does_not_rebuild(self):
        dlg = self.generate(["Coverage part one.", "Coverage part two."])
        rebuild_calls = []
        orig_rebuild = dlg._rebuild_table
        dlg._rebuild_table = lambda jobs: (
            rebuild_calls.append(1), orig_rebuild(jobs))[1]
        dlg.refresh_scene_context()
        self.assertEqual(rebuild_calls, [])
        dlg._rebuild_table = orig_rebuild


# ===========================================================================
# Cross-contamination — results belong to the job that was submitted
# ===========================================================================
class TestCrossContamination(_Harness):

    def test_manual_result_not_attached_to_replaced_queue(self):
        """A manual batch in flight when the user starts a scene batch:
        the old result is DROPPED (identity guard) and every scene job
        that shows COMPLETED actually RAN (model calls)."""
        FakeHiggsModel.delay_s = 0.6
        bm = self.win._batch_manager
        bm.add_job(BatchJob(name="Manual one", prompt="manual prompt text",
                            project="Alpha"))
        bm.start()
        _process(150)
        # user starts the scene batch while the manual job is in flight
        self.generate(["Scene part one.", "Scene part two.",
                       "Scene part three."], review=False)
        self.wait_batch(timeout=30)

        scene_jobs = [j for j in bm.jobs if j.slot_id]
        ran = [c["text"] for c in FakeHiggsModel.calls
               if "Scene part" in c["text"]]
        completed = [j for j in scene_jobs
                     if j.status == JobStatus.COMPLETED]
        self.assertEqual(len(ran), 3,
                         "all three scene jobs must actually run")
        self.assertEqual(len(completed), len(ran),
                         "completed scene jobs == actually-run scene jobs")
        for j in scene_jobs:
            self.assertIn("Scene part", j.prompt)
            self.assertNotIn("manual", (j.prompt or "").lower())
            if j.status == JobStatus.COMPLETED:
                self.assertTrue(j.output_path)
                self.assertIn("Part_00", j.output_path,
                              "each completed job owns ITS versioned "
                              "output (not the manual job's file)")
        FakeHiggsModel.delay_s = 0.0

    def test_replaced_queue_without_start_never_auto_runs(self):
        """Review-mode hand-over: queue replaced while a job is in
        flight, start() NOT called — the new PENDING jobs must stay
        PENDING (nothing auto-runs)."""
        FakeHiggsModel.delay_s = 0.5
        bm = self.win._batch_manager
        bm.add_job(BatchJob(name="Manual one", prompt="manual prompt text",
                            project="Alpha"))
        bm.start()
        _process(120)
        self.generate(["Review part one.", "Review part two."],
                      review=True)   # no start()
        self.wait_batch(timeout=20)
        self.assertFalse(bm.is_running,
                         "the old batch must finish when its job resolves")
        statuses = [j.status for j in bm.jobs]
        self.assertEqual(statuses, [JobStatus.PENDING] * 2,
                         "review-mode jobs must never auto-run")
        FakeHiggsModel.delay_s = 0.0


# ===========================================================================
# §12 Project switching with unsaved Scene content
# ===========================================================================
class TestProjectSwitchingUnsaved(_Harness):

    def _edit_scene_a_unsaved(self):
        """Type substantial text + 2 narration blocks into scene A
        WITHOUT saving (the user's reproduction)."""
        from engine.narration_blocks import PromptBlock
        blocks = [
            PromptBlock(start_offset=0, end_offset=29,
                        emotion="calm", label="Opening"),
            PromptBlock(start_offset=30, end_offset=len(BIG_TEXT),
                        emotion="tense", label="Ridge"),
        ]
        self.win._editor.begin_scene_load()
        try:
            self.win._editor.set_text(BIG_TEXT)
            self.win._editor.set_scene_blocks(blocks, BIG_TEXT)
        finally:
            self.win._editor.end_scene_load()
        self.win._editor.set_editor_mode("blocks")

    def test_unsaved_scene_survives_switch_during_batch(self):
        """THE §6 reproduction: new project, scene, substantial text,
        narration blocks, NOT saved; batch running; switch to project B
        and back — everything must be intact."""
        self._edit_scene_a_unsaved()
        self.generate([BIG_TEXT[:80]])
        dlg = self.dialog()
        dlg._on_start()

        # switch away and back while the generation runs
        self.win._load_native_project(self.pm.get_project("projB"))
        _process(150)
        self.win._load_native_project(self.pm.get_project("projA"))
        self.wait_batch()

        # editor state
        self.assertEqual(self.win._editor.get_text(), BIG_TEXT,
                         "unsaved plain text must survive the switch")
        scene_a = self.win._active_scene
        self.assertIs(scene_a, self.scene_a,
                      "switching back must return the SAME live Scene "
                      "instance (the authoritative editing state)")
        blocks = scene_a.narration_blocks
        self.assertEqual(len(blocks), 2,
                         "unsaved narration blocks must survive")
        self.assertEqual(blocks[0].get("emotion"), "calm")
        self.assertEqual(blocks[1].get("label"), "Ridge")
        self.assertEqual(scene_a.editor_mode, "blocks")
        self.assertEqual(scene_a.character_ids, [self.char_a.id],
                         "character associations survive")
        # generated audio landed on the OWNING scene — not B's
        self.assertEqual(len(self.scene_a.audio_assets), 1,
                         "the batch asset must register on scene A")
        self.assertEqual(len(self.scene_b.audio_assets), 0,
                         "scene B must never receive A's asset")

    def test_existing_scene_modified_without_save(self):
        """A SAVED scene, modified without saving: the modification
        survives the switch AND the disk file is not silently rewritten."""
        self.scene_a.text = "Old saved text on disk."
        self.pm.save_project(self.pa)
        disk_path = os.path.join(self.tmp, "projects", "projA",
                                 "project.json")
        with open(disk_path, "r", encoding="utf-8") as fh:
            import json
            disk_before = json.load(fh)

        self.win._editor.set_text("Brand new unsaved modification.")
        self.generate(["Part for the saved-scene run."])
        self.dialog()._on_start()
        self.win._load_native_project(self.pm.get_project("projB"))
        _process(120)
        self.win._load_native_project(self.pm.get_project("projA"))
        self.wait_batch()

        self.assertEqual(self.win._editor.get_text(),
                         "Brand new unsaved modification.",
                         "the unsaved modification must survive")
        with open(disk_path, "r", encoding="utf-8") as fh:
            import json
            disk_after = json.load(fh)
        scene_disk = [s for s in disk_after.get("scenes", [])
                      if s.get("id") == self.scene_a.id][0]
        self.assertEqual(scene_disk.get("text"), "Old saved text on disk.",
                         "no silent disk save (live state vs persisted "
                         "state stay distinct)")

    def test_repeated_switching_a_b_a_b_a(self):
        self._edit_scene_a_unsaved()
        self.generate(["Part for repeated switching."])
        self.dialog()._on_start()
        for pid in ("projB", "projA", "projB", "projA"):
            self.win._load_native_project(self.pm.get_project(pid))
            _process(100)
        self.wait_batch()
        self.assertEqual(self.win._editor.get_text(), BIG_TEXT)
        self.assertEqual(len(self.win._active_scene.narration_blocks), 2)
        self.assertEqual(len(self.scene_a.audio_assets), 1)

    def test_switching_a_b_c_a(self):
        self._edit_scene_a_unsaved()
        self.generate(["Part for abc switching."])
        self.dialog()._on_start()
        for pid in ("projB", "projC", "projA"):
            self.win._load_native_project(self.pm.get_project(pid))
            _process(100)
        self.wait_batch()
        self.assertEqual(self.win._editor.get_text(), BIG_TEXT)
        self.assertEqual(len(self.win._active_scene.narration_blocks), 2)
        self.assertEqual(len(self.scene_a.audio_assets), 1)
        self.assertEqual(len(self.scene_c.audio_assets), 0)

    def test_batch_uses_current_editor_text_not_disk(self):
        """§8: disk holds old text; the editor holds new text; the batch
        must run on the NEW text."""
        self.scene_a.text = "The old disk text."
        self.pm.save_project(self.pa)
        self.win._editor.set_text("The new live editor text.")
        self.generate(["The new live editor text."], review=False)
        self.wait_batch()
        prompts = [c["text"] for c in FakeHiggsModel.calls]
        self.assertIn("The new live editor text.", prompts)
        self.assertNotIn("The old disk text.", prompts,
                         "the batch must never silently use the older "
                         "disk version of the Scene")


# ===========================================================================
# §5 Thread safety — UI updates only on the UI thread
# ===========================================================================
class TestThreadSafety(_Harness):

    def test_manager_updates_run_on_ui_thread(self):
        dlg = self.generate(["Thread part one.", "Thread part two.",
                             "Thread part three."])
        bm = self.win._batch_manager
        threads = []
        orig = dlg._on_manager_changed

        def _record():
            threads.append(QThread.currentThread())
            orig()

        dlg._on_manager_changed = _record
        dlg._on_start()
        self.wait_batch()
        self.assertGreater(len(threads), 3,
                           "manager change notifications must have arrived")
        main = self.app.thread()
        for th in threads:
            self.assertIs(th, main,
                          "every manager-driven UI update must run on the "
                          "UI thread (worker threads never touch widgets)")
        self.assertEqual(bm.marshal_to_ui, dlg._marshaler.marshal,
                         "the signal-based _UiMarshaler stays installed "
                         "while the dialog is open")
        dlg._on_manager_changed = orig


# ===========================================================================
# §4 Manager callbacks — multiple listeners, no overwrite
# ===========================================================================
class TestManagerCallbacks(_Harness):

    def test_multiple_listeners_all_notified(self):
        bm = self.win._batch_manager
        hits = {"a": 0, "b": 0}

        def listener_a(_bm):
            hits["a"] += 1

        def listener_b(_bm):
            hits["b"] += 1
            raise RuntimeError("one failing listener must not block "
                               "the others")

        bm.add_on_changed(listener_a)
        bm.add_on_changed(listener_b)
        bm._emit_changed()
        self.assertEqual(hits, {"a": 1, "b": 1})

        bm.remove_on_changed(listener_a)
        bm._emit_changed()
        self.assertEqual(hits, {"a": 1, "b": 2})

        bm.remove_on_changed(listener_b)
        bm._emit_changed()
        self.assertEqual(hits, {"a": 1, "b": 2})

    def test_start_double_returns_false_only_when_not_orphaned(self):
        bm = self.win._batch_manager
        bm.add_job(BatchJob(name="j1", prompt="x"))
        self.assertTrue(bm.start())
        try:
            # genuine double start (the in-flight job IS still queued)
            self.assertFalse(bm.start())
        finally:
            bm.stop()
        _ = self.wait_batch if False else None
        deadline = time.time() + 5
        while bm.is_running and time.time() < deadline:
            _process(20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
