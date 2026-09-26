"""
SpeechStudio — P3.35 runtime tests
==================================

ONE BatchGenerationDialog, TWO valid workflows, ONE live instance.

Covers the implementation brief end-to-end:

  §2  Shared wiring helper — the menu path (previously ZERO signal
      connections → dead Play / Stop-playback / Regen / Merge buttons)
      now wires every dialog signal through _wire_batch_dialog.
  §3/§14 Single live instance — same-mode reopen focuses; a mode
      switch closes cleanly; never two dialogs on the shared manager.
  §4/§15 Callback safety — set on open, released on close, no theft,
      visible dialog receives live updates, History registered once.
  §5/§6 Titles + honest menu tooltip ("Batch Queue" vs
      "Batch Generation — <Scene>"; "manual batch queue (independent
      of Scenes)").
  §7/§12 Manual Batch Queue regression — every visible button is
      functional at runtime (Add/Edit/Duplicate/Remove/Up/Down/
      Save/Load/Clear/Start/Pause/Stop/Play/Stop-playback/Regen/
      Merge Completed Parts — the last one as a REAL end-to-end merge).
  §8/§13 Scene mode regression — presentation + completion-callback
      scoping unchanged (deep behaviour is covered by the existing
      P3.27B/P3.28/P3.30/P3.31 suites, which run unchanged).
  §9  Concatenate semantics — hidden in scene mode, honest manual merge.
  §22/§32 Window geometry persistence — resize → close → reopen →
      restart (settings.json on disk) → restored, both modes.
  §23-§28/§33 Column configuration persistence — widths + order +
      visibility per mode, native QHeaderView state, schema-migration
      and corrupt-blob safety, mode isolation.
  §29/§30 "None" (and "All") button vertical clipping — metrics-aware
      height under the real application theme.
  §18 Governance — batch_generation action + _on_open_batch_generation
      handler + honest tooltip.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \\
        tests/test_p3_35_batch_two_workflows.py -v
"""
import os
import sys
import json
import time
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch, PropertyMock, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Fake torch (house pattern: test_p3_27b / test_p3_28 / test_p3_30).
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

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QPushButton, QDialog, QHeaderView,
)

from engine.models import Project, Scene  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)

# P3.34 house pattern: apply the REAL application theme ONCE at module
# level — the "None" clipping test must measure metrics under the real
# global button QSS (padding 7px + 1px border), not the offscreen default.
from ui.theme import apply_theme, DEFAULT_THEME  # noqa: E402
from ui.feedback import FeedbackDialog  # noqa: E402
apply_theme(APP, DEFAULT_THEME)


# ---------------------------------------------------------------------------
# Model-boundary fake (house pattern)
# ---------------------------------------------------------------------------
def make_speech(seconds=1.0, sr=24000):
    import numpy as np
    return (np.random.uniform(-0.5, 0.5, int(sr * seconds))
            .astype("float32"))


class FakeHiggsModel:
    calls = []
    speech_s = 0.2
    gate = None          # optional (threading.Event,) block-until-set
    on_enter = None

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


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _wait_batch(bm, timeout=12.0):
    t0 = time.time()
    while bm.is_running and time.time() - t0 < timeout:
        _process(30)
    _process(150)


def _column_index(table, title):
    for i in range(table.columnCount()):
        item = table.horizontalHeaderItem(i)
        if item is not None and item.text() == title:
            return i
    return None


class _Harness(unittest.TestCase):
    """Real Engine + real MainWindow + real BatchManager on a tmp root."""

    def setUp(self):
        from engine.engine import Engine
        from ui.main_window import MainWindow
        self.tmp = tempfile.mkdtemp(prefix="ss_p335_")
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
        self.project = Project(name="Eden", id="proj1")
        self.scene = Scene(id="sc1abcd", name="Scene 03", project_id="proj1")
        self.project.add_scene(self.scene)
        self.win._active_project = self.project
        self.win._active_scene = self.scene
        self.win._current_project = "Eden"
        FakeHiggsModel.calls = []
        FakeHiggsModel.gate = None
        FakeHiggsModel.on_enter = None

    def tearDown(self):
        dlg = getattr(self.win, "_batch_dialog", None)
        if dlg is not None:
            try:
                dlg.close()
            except Exception:
                pass
        try:
            self.win.close()
        except Exception:
            pass
        for p in self.patchers:
            p.stop()
        _process(50)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- convenience -------------------------------------------------------
    def open_manual(self):
        self.win._on_open_batch_generation()
        return self.win._batch_dialog

    def open_scene(self):
        self.win._present_batch_dialog(
            scene=self.scene, scene_id=self.scene.id)
        return self.win._batch_dialog

    def restart_batch_dialog(self):
        """Simulate 'application restart' for the batch dialog: close the
        live instance, destroy it, drop the reference — the next open
        builds a FRESH dialog that must re-read settings.json.

        (A mere close does NOT drop the reference: the single-instance
        policy intentionally re-focuses the still-alive closed dialog —
        the user's state is never lost on a close/reopen cycle.)
        """
        dlg = getattr(self.win, "_batch_dialog", None)
        if dlg is not None:
            try:
                dlg.close()
            except Exception:
                pass
            dlg.deleteLater()
        self.win._batch_dialog = None
        _process(80)

    def settings_file(self):
        return os.path.join(self.tmp, "settings", "settings.json")

    def read_settings(self):
        path = self.settings_file()
        if not os.path.isfile(path):
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def live_batch_dialogs(self):
        from ui.panels.batch_generation import BatchGenerationDialog
        return [w for w in self.win.findChildren(BatchGenerationDialog)
                if not w.isHidden() or w.isVisible()]


# ===========================================================================
# §7/§12 — Manual Batch Queue regression (every button functional)
# ===========================================================================
class TestManualQueueRegression(_Harness):

    def test_menu_open_batch_queue(self):
        """Menu path opens the MANUAL queue: title, layout, merge button."""
        dlg = self.open_manual()
        self.assertIsNotNone(dlg)
        dlg.show()
        self.assertEqual(dlg.windowTitle(), "Batch Queue")
        self.assertFalse(dlg.is_scene_mode)
        self.assertEqual(dlg._table.columnCount(), 5)
        self.assertEqual(dlg._start_btn.text(), "START BATCH")
        self.assertEqual(dlg._concat_btn.text(), "Merge Completed Parts")
        self.assertIn("not linked to a Scene", dlg._concat_btn.toolTip())
        self.assertIn("does not affect Project Assembly",
                      dlg._concat_btn.toolTip())
        self.assertFalse(hasattr(dlg, "_coverage_label"))

    def test_add_edit_duplicate_remove(self):
        from ui.panels.batch_generation import JobEditDialog
        dlg = self.open_manual()

        def fake_exec_add(self_dlg):
            self_dlg._name.setText("Added Job")
            self_dlg._prompt.setText("hello world")
            return QDialog.DialogCode.Accepted

        def fake_exec_edit(self_dlg):
            self_dlg._name.setText("Edited Name")
            return QDialog.DialogCode.Accepted

        with patch.object(JobEditDialog, "exec", fake_exec_add):
            dlg._on_add()
        self.assertEqual(len(dlg._manager.jobs), 1)
        self.assertEqual(dlg._manager.jobs[0].name, "Added Job")
        self.assertEqual(dlg._table.rowCount(), 1)

        dlg._table.selectRow(0)
        with patch.object(JobEditDialog, "exec", fake_exec_edit):
            dlg._on_edit()
        self.assertEqual(dlg._manager.jobs[0].name, "Edited Name")

        dlg._table.selectRow(0)
        dlg._on_duplicate()
        self.assertEqual(len(dlg._manager.jobs), 2)
        self.assertEqual(dlg._manager.jobs[1].name, "Edited Name (copy)")

        dlg._table.selectRow(0)
        dlg._on_remove()
        self.assertEqual(len(dlg._manager.jobs), 1)

    def test_move_up_down(self):
        from engine.batch_manager import BatchJob
        dlg = self.open_manual()
        dlg._manager.add_job(BatchJob(name="A", prompt="a"))
        dlg._manager.add_job(BatchJob(name="B", prompt="b"))
        dlg._refresh_table()
        dlg._table.selectRow(1)
        dlg._on_move_up()
        self.assertEqual([j.name for j in dlg._manager.jobs], ["B", "A"])
        dlg._table.selectRow(0)
        dlg._on_move_down()
        self.assertEqual([j.name for j in dlg._manager.jobs], ["A", "B"])

    def test_clear_queue(self):
        from engine.batch_manager import BatchJob
        from PySide6.QtWidgets import QMessageBox
        dlg = self.open_manual()
        dlg._manager.add_job(BatchJob(name="A", prompt="a"))
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes):
            dlg._on_clear()
        self.assertEqual(len(dlg._manager.jobs), 0)

    def test_save_and_load_queue(self):
        """Save Queue… / Load Queue… — the governance-locked YAML format.

        P3.44.3: the queue save/load confirmations migrated to the
        structured FeedbackDialog (same information, summary/details
        hierarchy) — the modal-suppression patch follows the new seam.
        """
        from engine.batch_manager import BatchJob
        from PySide6.QtWidgets import QMessageBox
        dlg = self.open_manual()
        dlg._manager.add_job(BatchJob(name="Persisted", prompt="px"))
        qpath = os.path.join(self.tmp, "queue", "batch_queue.yaml")
        os.makedirs(os.path.dirname(qpath), exist_ok=True)
        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName",
                   return_value=(qpath, "")), \
             patch.object(FeedbackDialog, "information",
                          return_value=QMessageBox.StandardButton.Ok):
            dlg._on_save_queue()
        self.assertTrue(os.path.isfile(qpath))
        with open(qpath, "r", encoding="utf-8") as fh:
            raw = fh.read()
        self.assertIn("Persisted", raw)

        dlg._manager.clear_all()
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName",
                   return_value=(qpath, "")), \
             patch.object(FeedbackDialog, "information",
                          return_value=QMessageBox.StandardButton.Ok):
            dlg._on_load_queue()
        self.assertEqual(len(dlg._manager.jobs), 1)
        self.assertEqual(dlg._manager.jobs[0].name, "Persisted")

    def test_start_runs_real_generation(self):
        """START BATCH drives the REAL engine pipeline to completion."""
        from engine.batch_manager import BatchJob, JobStatus
        dlg = self.open_manual()
        dlg._manager.add_job(BatchJob(name="m1", prompt="first manual job"))
        dlg._manager.add_job(BatchJob(name="m2", prompt="second manual job"))
        dlg._refresh_table()
        self.assertTrue(dlg._manager.start())
        _wait_batch(dlg._manager)
        for job in dlg._manager.jobs:
            self.assertEqual(job.status, JobStatus.COMPLETED, job.error)
            self.assertTrue(job.output_path)
            full = job.output_path
            if not os.path.isabs(full):
                full = os.path.join(self.tmp, full)
            self.assertTrue(os.path.isfile(full), full)
        self.assertEqual(dlg._state_label.text().strip(), "State    Finished")

    def test_pause_and_stop_buttons(self):
        """Pause + Stop actually drive the manager (house pattern from
        test_p3_30: _running faked to avoid a worker thread).

        P3.44.1 (deterministic idle): with a job genuinely IN FLIGHT
        (``_current_index`` set) Stop shows the transitional
        "Stopping…" state while the model call runs out. With NOTHING
        in flight (paused between jobs — the old deadlock window),
        Stop now finishes the batch IMMEDIATELY instead of leaving
        ``_running`` stuck True forever."""
        from engine.batch_manager import BatchJob, JobStatus
        dlg = self.open_manual()
        dlg._manager.add_job(BatchJob(name="j1", prompt="x"))
        dlg._manager.add_job(BatchJob(name="j2", prompt="y"))
        dlg._refresh_table()
        bm = dlg._manager
        bm._running = True
        bm._paused = False
        bm._stop_requested = False
        dlg._update_buttons()
        self.assertTrue(dlg._pause_btn.isEnabled())
        dlg._on_pause()
        self.assertTrue(bm.is_paused)
        # In-flight case: job 1 is generating -> Stop is transitional.
        bm._current_index = 0
        bm.jobs[0].status = JobStatus.GENERATING
        dlg._update_buttons()
        dlg._on_stop()
        self.assertTrue(bm.stop_requested)
        self.assertIn("Stopping", dlg._stop_btn.text())
        self.assertFalse(dlg._stop_btn.isEnabled())
        # Nothing-in-flight case (paused between jobs): the P3.44.1
        # deadlock window — Stop must settle the batch to idle AT ONCE.
        bm2 = dlg._manager
        bm2._running = True
        bm2._paused = True
        bm2._stop_requested = False
        bm2._current_index = None
        bm2._current_job = None
        dlg._on_stop()
        self.assertFalse(bm2.is_running,
                         "stop while paused-between-jobs must finish the "
                         "batch (previously stuck 'running' forever)")
        self.assertEqual(dlg._stop_btn.text(), "Stop")
        self.assertFalse(dlg._stop_btn.isEnabled())

    def test_play_stop_playback_wired(self):
        """The formerly-dead per-row Play / Stop-playback buttons reach
        the MainWindow handlers (mocked BEFORE the menu open so the
        shared wiring helper connects the mock — proof of wiring).

        P3.44 (§5): Play enablement now validates that the output file
        EXISTS before enabling playback — the fake path became a real
        tiny WAV so the wiring proof still runs against a playable row
        (the disabled-on-missing-file state has its own P3.44 tests)."""
        import wave as _wave
        from engine.batch_manager import BatchJob, JobStatus
        play_mock = MagicMock()
        stop_mock = MagicMock()
        self.win._on_batch_play = play_mock
        self.win._on_batch_stop_playback = stop_mock
        dlg = self.open_manual()
        job = None
        dlg._manager.add_job(BatchJob(name="w", prompt="wire me"))
        fake_abs = os.path.join(self.tmp, "outputs", "fake.wav")
        os.makedirs(os.path.dirname(fake_abs), exist_ok=True)
        with _wave.open(fake_abs, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 1600)
        for j in dlg._manager.jobs:
            j.status = JobStatus.COMPLETED
            j.output_path = "outputs/fake.wav"
            job = j
        dlg._refresh_table()
        dlg._update_buttons()
        act = dlg._table.cellWidget(0, dlg._col_act)
        buttons = act.findChildren(QPushButton)
        self.assertTrue(len(buttons) >= 3)
        self.assertTrue(buttons[0].isEnabled(),
                        "Play must be enabled for an existing output file")
        buttons[0].click()                     # Play
        play_mock.assert_called_once_with(fake_abs)
        buttons[1].click()                     # Stop playback
        stop_mock.assert_called_once()

    def test_regen_wired(self):
        """The formerly-dead Regen button emits regen_requested (Yes on
        the confirmation dialog)."""
        from engine.batch_manager import BatchJob, JobStatus
        from PySide6.QtWidgets import QMessageBox
        regen_mock = MagicMock()
        self.win._on_batch_regen = regen_mock
        dlg = self.open_manual()
        dlg._manager.add_job(BatchJob(name="r", prompt="regen me"))
        dlg._manager.jobs[0].status = JobStatus.COMPLETED
        dlg._refresh_table()
        dlg._update_buttons()
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes):
            dlg._on_regen(0)
        regen_mock.assert_called_once_with(0)

    def test_merge_completed_parts_real_end_to_end(self):
        """Merge Completed Parts → REAL concatenation through the REAL
        MainWindow handler (single-speaker path) → combined WAV on disk."""
        from engine.batch_manager import BatchJob, JobStatus
        dlg = self.open_manual()
        dlg._manager.add_job(BatchJob(name="p1", prompt="part one"))
        dlg._manager.add_job(BatchJob(name="p2", prompt="part two"))
        dlg._manager.start()
        _wait_batch(dlg._manager)
        self.assertTrue(all(j.status == JobStatus.COMPLETED
                            for j in dlg._manager.jobs))
        dlg._update_buttons()
        self.assertTrue(dlg._concat_btn.isEnabled())
        with patch("ui.main_window.APP_ROOT", self.tmp):
            dlg._on_concatenate()          # emits concatenate_requested…
            _process(300)                  # …handler runs synchronously;
        combined = os.path.join(self.tmp, "outputs", "long_narration_full.wav")
        self.assertTrue(os.path.isfile(combined), combined)
        self.assertGreater(os.path.getsize(combined), 1024)


# ===========================================================================
# §8/§13 — Scene mode regression (presentation + scoping)
# ===========================================================================
class TestSceneModeRegression(_Harness):

    def test_scene_dialog_presentation(self):
        dlg = self.open_scene()
        dlg.show()
        self.assertTrue(dlg.is_scene_mode)
        self.assertEqual(dlg.windowTitle(),
                         "Batch Generation \u2014 Scene 03")
        self.assertEqual(dlg._table.columnCount(), 7)
        self.assertIn("GENERATE CHECKED", dlg._start_btn.text())
        self.assertTrue(hasattr(dlg, "_coverage_label"))
        self.assertIn("Scene 03", dlg._coverage_label.text())
        self.assertIn("coverage", dlg._coverage_label.text())
        self.assertTrue(hasattr(dlg, "_combine_btn"))
        self.assertTrue(hasattr(dlg, "_export_btn"))
        # §9: Concatenate is NOT a Scene-level operation — hidden here.
        self.assertFalse(hasattr(dlg, "_concat_btn"))

    def test_scene_completion_callback_scoped(self):
        bm = self.win._batch_manager
        self.open_scene()
        self.assertIsNotNone(bm._on_batch_completed)
        self.open_manual()
        # Manual queue clears the stale scene-completion callback.
        self.assertIsNone(bm._on_batch_completed)

    def test_scene_features_present(self):
        """Version popover button + Use-as-output rows + review checkbox
        UI all remain (deep behaviour is covered by P3.28/P3.30 suites)."""
        from engine.audio_provenance import materialize_expected_slots
        from engine.batch_manager import BatchJob
        self.scene.expected_audio_slots = materialize_expected_slots([])
        dlg = self.open_scene()
        dlg._manager.add_job(BatchJob(
            name="B1", prompt="x", slot_id="b1:1",
            scene_id=self.scene.id, part_index=1))
        dlg._refresh_table()
        self.assertIsNotNone(_column_index(dlg._table, "Generated"))
        self.assertIsNotNone(_column_index(dlg._table, ""))  # checkbox col


# ===========================================================================
# §3/§14 — Dual entry: NO simultaneous dialogs
# ===========================================================================
class TestSingleLiveInstance(_Harness):

    def test_menu_open_twice_focuses_same_dialog(self):
        first = self.open_manual()
        first.show()
        _process()
        self.win._on_open_batch_generation()
        second = self.win._batch_dialog
        self.assertIs(first, second)
        self.assertTrue(first.isVisible())
        self.assertEqual(len(self.live_batch_dialogs()), 1)

    def test_scene_open_closes_manual(self):
        manual = self.open_manual()
        manual.show()
        _process()
        scene = self.open_scene()
        self.assertIsNot(manual, scene)
        self.assertFalse(manual.isVisible())
        self.assertTrue(scene.isVisible())
        self.assertTrue(scene.is_scene_mode)
        _process(80)  # let deleteLater settle
        self.assertEqual(len(self.live_batch_dialogs()), 1)

    def test_manual_reopen_after_scene(self):
        scene = self.open_scene()
        scene.show()
        _process()
        manual = self.open_manual()
        self.assertIsNot(scene, manual)
        self.assertFalse(scene.isVisible())
        self.assertTrue(manual.isVisible())
        self.assertEqual(manual.windowTitle(), "Batch Queue")
        _process(80)
        self.assertEqual(len(self.live_batch_dialogs()), 1)

    def test_running_batch_survives_mode_switch(self):
        """§11: closing via a mode switch must NOT stop a running batch."""
        manual = self.open_manual()
        bm = manual._manager
        bm._running = True
        bm._stop_requested = False
        self.open_scene()
        self.assertFalse(manual.isVisible())
        self.assertTrue(bm.is_running)
        self.assertFalse(bm.stop_requested)

    def test_user_close_stops_running_batch(self):
        """The USER closing the (single) dialog still stops the batch.

        P3.44.4 §10: the deterministic-idle finish inside stop() now
        clears ALL transient execution state — after the close, the
        manager is not only idle (running=False) but CLEAN (no lingering
        stop_requested/pause flags). The pre-P3.44.4 behaviour kept
        ``stop_requested=True`` while idle."""
        from engine.batch_manager import BatchJob, JobStatus
        dlg = self.open_manual()
        dlg._manager.add_job(BatchJob(name="j1", prompt="x"))
        bm = dlg._manager
        bm._running = True
        dlg.close()
        self.assertFalse(bm.is_running,
                         "nothing in flight: close-stop settles to idle "
                         "AT ONCE (deterministic finish)")
        self.assertFalse(bm.stop_requested,
                         "P3.44.4 §10: a finished run leaves no stale "
                         "stop flag behind")
        self.assertEqual(bm.jobs[0].status, JobStatus.SKIPPED)
        # Idempotent: an explicit finish after the deterministic one is
        # a harmless no-op (the P3.30 house pattern).
        bm._finish_batch()
        self.assertFalse(bm.is_running)


# ===========================================================================
# §4/§15 — Shared manager lifecycle: no callback theft, one listener
# ===========================================================================
class TestSharedManagerLifecycle(_Harness):

    def test_listener_set_on_open_released_on_close(self):
        bm = self.win._batch_manager
        # P3.44.1: the manager keeps a LISTENER LIST (multi-observer).
        self.assertEqual(bm.on_changed_listeners, [])
        orig_marshal = bm.marshal_to_ui  # MainWindow's own marshaler
        dlg = self.open_manual()
        self.assertIn(dlg._on_changed_cb, bm.on_changed_listeners)
        self.assertEqual(bm.marshal_to_ui, dlg._marshaler.marshal)
        dlg.close()
        self.assertNotIn(dlg._on_changed_cb, bm.on_changed_listeners)
        self.assertEqual(bm.on_changed_listeners, [])
        # Restored to the PRE-dialog owner (MainWindow's marshaler) —
        # never left pointing at the closed dialog, never cleared to a
        # value that would break a dialog-less manager.
        self.assertEqual(bm.marshal_to_ui, orig_marshal)
        self.assertNotEqual(bm.marshal_to_ui, dlg._marshaler.marshal)

    def test_reopen_rewires_no_theft(self):
        bm = self.win._batch_manager
        a = self.open_manual()
        a.close()
        self.restart_batch_dialog()
        b = self.open_manual()
        self.assertIsNot(a, b)
        self.assertIn(b._on_changed_cb, bm.on_changed_listeners)
        self.assertNotIn(a._on_changed_cb, bm.on_changed_listeners)
        b.close()
        self.assertEqual(bm.on_changed_listeners, [])

    def test_two_dialogs_both_receive_updates(self):
        """P3.44.1 §4: the single-callback slot previously meant the
        second dialog REPLACED the first's callback — the first dialog
        went silent. The listener list keeps BOTH live."""
        from engine.batch_manager import BatchJob
        a = self.open_manual()
        a.show()
        b = self.open_manual()   # a second dialog on the SAME manager
        b.show()
        self.assertIn(a._on_changed_cb, self.win._batch_manager.on_changed_listeners)
        self.assertIn(b._on_changed_cb, self.win._batch_manager.on_changed_listeners)
        a._manager.add_job(BatchJob(name="live", prompt="x"))
        self.win._batch_manager._emit_changed()
        _process(100)
        self.assertEqual(a._table.rowCount(), 1,
                         "the FIRST dialog must still receive updates")
        self.assertEqual(b._table.rowCount(), 1)
        b.close()
        a.close()

    def test_visible_dialog_receives_live_updates(self):
        from engine.batch_manager import BatchJob
        dlg = self.open_manual()
        dlg.show()
        self.assertEqual(dlg._table.rowCount(), 0)
        dlg._manager.add_job(BatchJob(name="live", prompt="x"))
        dlg._manager._emit_changed()
        _process(100)
        self.assertEqual(dlg._table.rowCount(), 1)

    def test_history_registered_exactly_once(self):
        """One real generation → exactly ONE history entry for it."""
        from engine.batch_manager import BatchJob, JobStatus
        history_dir = os.path.join(self.tmp, "settings", "history")
        dlg = self.open_manual()
        dlg._manager.add_job(BatchJob(name="once", prompt="exactly once"))
        dlg._manager.start()
        _wait_batch(dlg._manager)
        self.assertEqual(dlg._manager.jobs[0].status, JobStatus.COMPLETED)
        entries = [f for f in os.listdir(history_dir)
                   if f.endswith(".json")] if os.path.isdir(history_dir) else []
        self.assertEqual(len(entries), 1)

    def test_double_construction_never_happens_via_handlers(self):
        """Even mixed rapid handler use yields exactly ONE live dialog."""
        self.open_manual()
        _process(30)
        self.open_scene()
        _process(30)
        self.win._on_open_batch_generation()
        _process(80)
        self.assertEqual(len(self.live_batch_dialogs()), 1)


# ===========================================================================
# §22/§32 — Window geometry persistence (SettingsManager, both modes)
# ===========================================================================
class TestGeometryPersistence(_Harness):

    def test_resize_close_reopen_restored(self):
        dlg = self.open_manual()
        dlg.show()
        dlg.resize(1234, 567)
        dlg.close()
        self.assertEqual(
            self.read_settings().get("window", {})
            .get("batch_dialog", {}).get("width"), 1234)
        self.restart_batch_dialog()
        dlg2 = self.open_manual()
        self.assertEqual((dlg2.width(), dlg2.height()), (1234, 567))

    def test_geometry_survives_restart(self):
        """The size is on DISK in settings.json — an application restart
        (fresh process) restores it."""
        dlg = self.open_manual()
        dlg.resize(1010, 505)
        dlg.close()
        data = self.read_settings()
        self.assertEqual(data["window"]["batch_dialog"]["width"], 1010)
        self.assertEqual(data["window"]["batch_dialog"]["height"], 505)
        # "Restart": read through a FRESH SettingsManager (new instance
        # directory cache) as a new process would.
        from engine.settings_manager import SettingsManager
        sm = SettingsManager(os.path.join(self.tmp, "settings", ""))
        self.assertEqual(
            sm.get("window", "batch_dialog", {}).get("height"), 505)

    def test_both_modes_share_geometry_documented(self):
        """Both modes intentionally share window/batch_dialog — the size
        chosen in one workflow is restored for the other (documented)."""
        dlg = self.open_manual()
        dlg.resize(1180, 640)
        dlg.close()
        self.restart_batch_dialog()
        scene = self.open_scene()
        self.assertEqual((scene.width(), scene.height()), (1180, 640))


# ===========================================================================
# §23-§28/§33 — Column configuration persistence (per mode)
# ===========================================================================
class TestColumnPersistence(_Harness):

    def _configure_manual(self, width=333):
        dlg = self.open_manual()
        dlg.show()
        header = dlg._table.horizontalHeader()
        status = _column_index(dlg._table, "Status")
        header.setSectionResizeMode(
            status, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(status, width)
        dlg.close()
        return width

    def test_column_widths_persist(self):
        self._configure_manual(333)
        self.restart_batch_dialog()
        dlg = self.open_manual()
        status = _column_index(dlg._table, "Status")
        self.assertEqual(dlg._table.horizontalHeader().sectionSize(status),
                         333)
        # …and it is on disk (restart survival) under the manual key.
        data = self.read_settings()["window"]["batch_queue_columns"]
        self.assertEqual(data["widths"]["Status"], 333)

    def test_columns_are_user_resizable_now(self):
        """The 'lost' column configuration: columns were Fixed (could not
        be resized by the user at all) — now the data columns Interactive."""
        dlg = self.open_manual()
        header = dlg._table.horizontalHeader()
        for title in ("Status", "Duration", "Actions"):
            i = _column_index(dlg._table, title)
            self.assertEqual(
                header.sectionResizeMode(i),
                QHeaderView.ResizeMode.Interactive, title)
        self.assertTrue(header.sectionsMovable())
        dlg2 = self.open_scene()
        h2 = dlg2._table.horizontalHeader()
        for title in ("Generated", "Status", "Duration", "Actions"):
            i = _column_index(dlg2._table, title)
            self.assertEqual(
                h2.sectionResizeMode(i),
                QHeaderView.ResizeMode.Interactive, title)

    def test_order_and_visibility_persist(self):
        dlg = self.open_manual()
        dlg.show()
        header = dlg._table.horizontalHeader()
        # Hide "Duration", move "Actions" to the front.
        dur = _column_index(dlg._table, "Duration")
        act = _column_index(dlg._table, "Actions")
        header.setSectionHidden(dur, True)
        header.moveSection(act, 0)
        dlg.close()
        self.restart_batch_dialog()
        dlg2 = self.open_manual()
        h2 = dlg2._table.horizontalHeader()
        dur2 = _column_index(dlg2._table, "Duration")
        self.assertTrue(h2.isSectionHidden(dur2))
        # visualIndex(0) is the FIRST visible slot; "Actions" is logical
        # but its VISUAL position must be the first non-hidden section.
        act2 = _column_index(dlg2._table, "Actions")
        visible = [h2.logicalIndex(v)
                   for v in range(h2.count()) if not h2.isSectionHidden(
                       h2.logicalIndex(v))]
        self.assertEqual(visible[0], act2)

    def test_mode_isolation(self):
        """Manual config is never corrupted by a Scene-mode dialog (and
        vice versa) — separate keys, separate schemas."""
        self._configure_manual(333)
        self.restart_batch_dialog()
        scene = self.open_scene()
        scene.show()
        h = scene._table.horizontalHeader()
        gen = _column_index(scene._table, "Generated")
        h.resizeSection(gen, 250)
        scene.close()
        self.restart_batch_dialog()
        manual = self.open_manual()
        status = _column_index(manual._table, "Status")
        self.assertEqual(
            manual._table.horizontalHeader().sectionSize(status), 333)
        keys = set(self.read_settings()["window"].keys())
        self.assertIn("batch_queue_columns", keys)
        self.assertIn("batch_generation_scene_columns", keys)
        self.restart_batch_dialog()
        scene2 = self.open_scene()
        gen2 = _column_index(scene2._table, "Generated")
        self.assertEqual(
            scene2._table.horizontalHeader().sectionSize(gen2), 250)

    def test_schema_migration_no_crash(self):
        """Stored state from an OLDER schema (obsolete + missing titles)
        never crashes; matching widths are re-applied by title, new
        columns keep their defaults (§27)."""
        sm_dir = os.path.join(self.tmp, "settings")
        dlg = self.open_manual()  # create once so settings exist
        dlg.close()
        self.restart_batch_dialog()
        from engine.settings_manager import SettingsManager
        sm = SettingsManager.instance(sm_dir)
        sm.set("window", "batch_queue_columns", {
            "version": 1,
            "columns": ["#", "File Name", "Old Column", "Status",
                        "Duration", "Actions"],
            "state": "AAAA",  # deliberately not a valid header state
            "widths": {"Status": 275, "Old Column": 999},
        })
        dlg2 = self.open_manual()  # must not raise
        status = _column_index(dlg2._table, "Status")
        self.assertEqual(
            dlg2._table.horizontalHeader().sectionSize(status), 275)

    def test_corrupt_state_ignored(self):
        dlg = self.open_manual()
        dlg.close()
        self.restart_batch_dialog()
        from engine.settings_manager import SettingsManager
        sm = SettingsManager.instance(os.path.join(self.tmp, "settings"))
        sm.set("window", "batch_queue_columns", "garbage-not-a-dict")
        dlg2 = self.open_manual()  # must not raise; defaults apply
        self.assertEqual(dlg2._table.columnCount(), 5)


# ===========================================================================
# §29/§30 — "None" button clipping (real theme metrics)
# ===========================================================================
class TestNoneButtonGeometry(_Harness):

    def test_none_and_all_fully_visible(self):
        dlg = self.open_scene()
        dlg.show()
        _process(50)
        for btn, label in ((dlg._sel_none_btn, "None"),
                           (dlg._sel_all_btn, "All")):
            hint = btn.sizeHint().height()
            self.assertGreaterEqual(
                btn.height(), hint,
                "{0}: widget {1}px < needed {2}px".format(
                    label, btn.height(), hint))
            fm = btn.fontMetrics()
            # QSS: 7px padding top/bottom + 1px borders.
            self.assertGreaterEqual(btn.height(), fm.height() + 16)
            text_rect = fm.boundingRect(
                btn.rect().adjusted(16, 9, -16, -9),
                0x0084, label)  # AlignCenter
            self.assertTrue(text_rect.isValid(), label)

    def test_neighbouring_buttons_not_clipped(self):
        """§30: same root-cause audit for adjacent controls — the 'Use as
        output' buttons (28px min) fit their 12px font + 4px padding."""
        dlg = self.open_scene()
        dlg.show()
        from engine.audio_provenance import (
            materialize_expected_slots, register_generation_result,
            build_scene_combined_entry,
        )
        from engine.models import GenerationResult
        scene = self.scene
        scene.expected_audio_slots = materialize_expected_slots([])
        result = GenerationResult(
            success=True, output_path="outputs/x.wav", output_duration=5.0,
            timestamp="20260101_000000")
        result.scene_id = scene.id
        result.part_index = 1
        result.part_version = 1
        register_generation_result(self.project, result,
                                   scene_fallback=scene)
        scene.combined_outputs.append(build_scene_combined_entry(
            scene, version=1, output_path="outputs/c1.wav", duration=5.0,
            sources=[], silence_ms=300, generation_run="r1",
            project_name="Eden"))
        dlg._refresh_combined_section()
        dlg._refresh_table()
        dlg._update_buttons()
        found = 0
        for btn in dlg.findChildren(QPushButton):
            if btn.text().startswith(("Use as output", "\u2713 selected")):
                found += 1
                fm = btn.fontMetrics()
                self.assertGreaterEqual(
                    btn.height(), fm.height() + 8 + 2,
                    "clipped: {0!r} h={1} fm={2}".format(
                        btn.text(), btn.height(), fm.height()))
        self.assertGreaterEqual(found, 1)


# ===========================================================================
# §18/§10 — Governance + honest menu presentation
# ===========================================================================
class TestGovernanceAndMenu(_Harness):

    def test_batch_generation_action_tooltip(self):
        action = self.win._menu_bar._actions.get("batch_generation")
        self.assertIsNotNone(action)
        self.assertEqual(action.text(), "Batch Generation...")
        self.assertEqual(
            action.toolTip(),
            "Open the manual batch queue (independent of Scenes)")

    def test_governance_identifiers_intact(self):
        self.assertTrue(hasattr(self.win, "_on_open_batch_generation"))
        self.assertTrue(callable(self.win._on_open_batch_generation))
        # Shared wiring helper used by every construction path.
        self.assertTrue(callable(getattr(self.win, "_wire_batch_dialog")))

    def test_wire_helper_connects_all_signals(self):
        """The helper wires ALL nine dialog signals — the formerly dead
        menu path now has every handler attached. Every MainWindow slot
        is replaced by a mock BEFORE the open (wiring-time attribute
        lookup binds the mock), so emitting each signal proves the
        connection without invoking real handlers."""
        mocks = {}
        for name in ("_on_concatenate_requested", "_on_combine_scene_requested",
                     "_on_use_version_requested", "_on_select_output_requested",
                     "_on_batch_play", "_on_batch_stop_playback",
                     "_on_batch_regen", "_on_generate_selected",
                     "_on_batch_export_audio"):
            m = MagicMock()
            setattr(self.win, name, m)
            mocks[name] = m
        dlg = self.open_manual()
        dlg.concatenate_requested.emit(["a", "b"])
        dlg.combine_scene_requested.emit()
        dlg.use_version_requested.emit("s", "a")
        dlg.select_output_requested.emit("k", "i")
        dlg.play_requested.emit("outputs/x.wav")
        dlg.stop_playback_requested.emit()
        dlg.regen_requested.emit(3)
        dlg.generate_selected_requested.emit([0, 2])
        dlg.export_audio_requested.emit("parts", [])
        for name, m in mocks.items():
            self.assertTrue(m.called, name)
        mocks["_on_concatenate_requested"].assert_called_once_with(
            ["a", "b"])
        mocks["_on_batch_play"].assert_called_once_with("outputs/x.wav")
        mocks["_on_batch_regen"].assert_called_once_with(3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
