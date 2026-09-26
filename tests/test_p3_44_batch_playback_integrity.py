"""GUINEO P3.44-B — BATCH PLAYBACK AND TABLE STATE INTEGRITY (runtime).

Spec: the Scene-mode Batch dialog must keep ALREADY GENERATED audio
playable after a table refresh or a Scene reopen, without destroying
row widgets/focus on every manager update.

Covers (§12 A–R):
  A/B  two versions generated for a slot
  C/D  close Batch → reopen the same Scene in Batch mode
  E    both versions visible ("2 versions")
  F    both versions playable (PENDING job + existing assets)
  G/H  select v01 → Play plays v01
  I/J  select v02 → Play plays v02
  K    version-menu Play plays the EXACT selected version
  L    version-menu Use changes selection but does NOT play
  M/N  refresh keeps the current row selected
  O    playback still works after refresh
  P    missing asset → clear unavailable state (no fallback, no crash)
  Q    Manual Batch playback still works (BatchJob.output_path path)
  R    Scene mode and Manual mode stay independent

Plus:
  §1/§7/§8  refresh never destroys row widgets on same-structure
             updates (widget IDENTITY assertions) and preserves
             selection/scroll/checkboxes across structural rebuilds
  §3/§10    one authoritative resolver (resolved_asset_for_slot)
             drives Play, tooltips and the version badge
  §13       BatchJob.output_path is NEVER a hidden mirror of provenance
  §14       the full generate→refresh→play→select→close→reopen→…
             golden path on a REAL MainWindow

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_44_batch_playback_integrity.py -v
"""
import gc
import math
import os
import shutil
import struct
import sys
import tempfile
import time
import types
import unittest
import wave

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

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidgetAction
from unittest.mock import patch, PropertyMock

_app = QApplication.instance() or QApplication([])

from engine.engine import Engine
from engine.models import Project, Scene
from engine.batch_manager import BatchJob, JobStatus
from engine.audio_provenance import (
    register_generation_result, resolved_asset_for_slot, slot_versions,
)
from ui.main_window import MainWindow
from ui.panels.batch_generation import (
    BatchGenerationDialog, _RowActions, _VersionMenuRow,
)

SAMPLE_RATE = 24000


def make_speech(seconds: float, freq: float = 220.0) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (0.2 * np.sin(2 * math.pi * freq * t)).astype(np.float32)


class FakeHiggsModel:
    """Model-boundary fake (P3.27B/P3.28 house pattern)."""

    calls = []
    speech_s = 1.0

    def generate_speech(self, text, tokenizer, *, reference_audio=None,
                        reference_sample_rate=None, reference_codes=None,
                        reference_text=None, max_new_tokens=2048,
                        temperature=1.0, top_p=None, top_k=None):
        FakeHiggsModel.calls.append(dict(text=text))
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


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _write_wav(path, seconds=1.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        n = int(seconds * SAMPLE_RATE)
        frames = bytearray()
        for i in range(n):
            v = int(9000 * math.sin(2 * math.pi * 330 * i / SAMPLE_RATE))
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))


# ===========================================================================
# Harness — real MainWindow + Engine, model boundary faked (P3.28 pattern)
# ===========================================================================
class _MWHarness(unittest.TestCase):
    """Real MainWindow + Engine; dialogs opened through the production
    _present_batch_dialog / start_long paths (real wiring, isolated
    tmp app root, shared settings manager)."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p344b_")
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
        # Recorder instead of real playback (no audio device offscreen).
        self.played = []
        self.engine.play_audio = lambda p: self.played.append(p)
        self.win = MainWindow(self.engine)
        self.win._project_manager = type(
            "PM", (), {"__init__": lambda self: None,
                       "save_project": lambda self, p: None})()
        self.project = Project(name="Eden", id="proj1")
        self.scene = Scene(id="sc1abcd", name="Scene 03",
                           project_id="proj1")
        self.project.add_scene(self.scene)
        self.win._active_project = self.project
        self.win._active_scene = self.scene
        self.win._current_project = "Eden"
        FakeHiggsModel.calls = []
        FakeHiggsModel.speech_s = 1.0

    def tearDown(self):
        try:
            if getattr(self, "win", None) is not None:
                try:
                    if self.win._batch_dialog is not None:
                        self.win._batch_dialog.close()
                except Exception:
                    pass
                self.win.close()
                # P3.44.1 (suite memory, same root cause as the P3.44.1
                # file): unittest keeps every TestCase instance alive
                # until the END of the suite — a closed-but-referenced
                # MainWindow retains its whole C++ widget tree, engine
                # and batch-dialog heap (~40 MB per test; 23 tests here
                # ≈ +0.9 GB, which pushed the full suite into an OOM
                # kill at ~85% on the 4 GB host). close() only hides a
                # top-level widget; the REFERENCES keep it allocated.
                # deleteLater + an event-loop flush actually frees the
                # C++ tree (the dialog is parented to the window, so it
                # cascades); dropping the attributes releases the
                # Python wrappers.
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
            _process(60)  # run the deferred deletions
        except Exception:
            pass
        # Drop the remaining strong references (base + the attributes
        # the per-class setUps install) so the per-test heap is actually
        # reclaimed instead of accumulating for the rest of the suite.
        for name in ("win", "engine", "project", "scene", "dlg", "job",
                     "v01", "v02", "part", "played", "patchers", "tmp"):
            if hasattr(self, name):
                setattr(self, name, None)
        gc.collect()

    def params(self):
        from engine.models import GenerationParameters
        return GenerationParameters(
            temperature=0.95, top_p=0.95, top_k=300, max_new_tokens=4096,
            seed=None, append_silence=0.5, normalize_output=False,
            auto_play=False)

    def start_long(self, parts, review=False):
        self.win._start_long_narration(parts, None, self.params(), {},
                                       review_mode=review)

    def wait_batch(self, timeout=30):
        bm = self.win._batch_manager
        t0 = time.time()
        while bm.is_running and time.time() - t0 < timeout:
            _process(20)
        _process(200)

    def dialog(self):
        return self.win._batch_dialog

    def close_batch(self):
        """Close the live batch dialog the way the user does."""
        self.win._close_batch_dialog_if_open()
        _process(50)

    # -- provenance helpers --------------------------------------------
    def register_version(self, slot_id, version, rel_path,
                         duration=2.0, block_id="b1", part_index=1):
        """Register a successful generation via the SINGLE WRITER
        (register_generation_result) — a real file must exist."""
        from types import SimpleNamespace
        abs_path = os.path.join(self.tmp, rel_path)
        _write_wav(abs_path, duration)
        result = SimpleNamespace(
            scene_id=self.scene.id, output_path=rel_path,
            output_duration=duration, speaker=None, character_id=None,
            voice_profile=None, timestamp="20260101_120000",
            block_id=block_id, part_index=part_index,
            part_version=version, generation_run="sc1abcd-r001",
        )
        return register_generation_result(self.project, result,
                                          scene_fallback=self.scene)

    def add_pending_job(self, slot_id, part_index=1, name="Part 1",
                        part_version=3):
        bm = self.win._batch_manager
        job = BatchJob(
            name=name, prompt="p344 part", voice_id=None,
            output_filename="PScene_v{0:02d}_Part_{1:03d}.wav".format(
                part_version, part_index),
            project="Eden", scene_id=self.scene.id,
            scene_name=self.scene.name, part_index=part_index,
            part_version=part_version, slot_id=slot_id,
        )
        bm.add_job(job)
        return job

    def open_scene_dialog(self, jobs_added=True):
        """Open the scene-mode Batch dialog (review semantics: nothing
        auto-starts — the 'reopened Scene' state)."""
        if jobs_added and not self.win._batch_manager.jobs:
            self.add_pending_job("b1:1")
        self.win._present_batch_dialog(
            scene=self.scene, scene_id=self.scene.id, review_mode=True)
        _process(50)
        return self.dialog()

    def slot(self):
        return "b1:1"


# ===========================================================================
# §2/§3/§6/§11/§13 — Scene-mode authoritative playback resolution
# ===========================================================================
class TestSceneModePlaybackResolution(_MWHarness):

    def setUp(self):
        super().setUp()
        self.scene.expected_audio_slots = [{
            "slot_id": "b1:1", "block_id": "b1", "block_label": "B1",
            "part_index": 1, "part_of_block": 1, "speaker": "",
            "character_id": None,
        }]
        self.v01 = self.register_version("b1:1", 1, "outputs/p1_v01.wav")
        self.v02 = self.register_version("b1:1", 2, "outputs/p1_v02.wav")
        self.job = self.add_pending_job("b1:1")
        self.dlg = self.open_scene_dialog()

    def _row_actions(self, row=0):
        act = self.dlg._table.cellWidget(row, self.dlg._col_act)
        self.assertIsInstance(act, _RowActions)
        return act

    def _click_play(self, row=0):
        self._row_actions(row).play_button().click()
        _process(20)

    def test_pending_job_with_existing_versions_is_playable(self):
        """§6.4-6.7: REOPENED state — fresh PENDING job (output_path
        None) yet Play is ENABLED because the Scene holds assets."""
        job = self.win._batch_manager.jobs[0]
        self.assertEqual(job.status, JobStatus.PENDING)
        self.assertIsNone(job.output_path)
        btn = self._row_actions().play_button()
        self.assertTrue(btn.isEnabled(),
                        "Play must be enabled for existing Scene audio "
                        "even when the job is PENDING (§11)")

    def test_play_plays_resolved_latest_version(self):
        self._click_play()
        expected = os.path.join(self.tmp, "outputs/p1_v02.wav")
        self.assertEqual(self.win._waveform.current_path, expected)
        self.assertEqual(self.played, [expected])

    def test_play_respects_explicit_selection(self):
        """§3: v01 explicitly selected → Play MUST play v01 (never the
        latest silently)."""
        self.scene.selected_block_audio[self.slot()] = self.v01["id"]
        self.dlg.refresh_scene_context()
        self._click_play()
        self.assertEqual(
            self.win._waveform.current_path,
            os.path.join(self.tmp, "outputs/p1_v01.wav"))

    def test_selection_change_without_refresh_is_honoured_at_click(self):
        """§3 click-time resolution: Use then Play works immediately."""
        self.scene.selected_block_audio[self.slot()] = self.v01["id"]
        # NOTE: no refresh here — the click resolves the CURRENT state.
        self._click_play()
        self.assertEqual(
            self.win._waveform.current_path,
            os.path.join(self.tmp, "outputs/p1_v01.wav"))

    def test_no_asset_slot_not_playable(self):
        job2 = self.add_pending_job("b2:1", part_index=2, name="Part 2")
        self.scene.expected_audio_slots.append({
            "slot_id": "b2:1", "block_id": "b2", "block_label": "B2",
            "part_index": 2, "part_of_block": 1, "speaker": "",
            "character_id": None,
        })
        self.dlg._refresh_table()
        act = self.dlg._table.cellWidget(1, self.dlg._col_act)
        self.assertFalse(act.play_button().isEnabled(),
                         "a slot with no assets must not be playable")

    def test_job_output_path_never_mirrors_provenance(self):
        """§13: architectural rule — playback resolution NEVER copies
        the asset path into BatchJob.output_path."""
        self._click_play()
        self._click_play()
        job = self.win._batch_manager.jobs[0]
        self.assertIsNone(job.output_path,
                          "BatchJob.output_path must stay queue-execution "
                          "state (no hidden provenance mirror)")

    def test_play_tooltip_names_the_version(self):
        self.scene.selected_block_audio[self.slot()] = self.v01["id"]
        self.dlg.refresh_scene_context()
        tip = self._row_actions().play_button().toolTip()
        self.assertIn("v01", tip)
        self.assertIn("selected", tip)


# ===========================================================================
# §4/§5 — version menu: every version playable; missing file state
# ===========================================================================
class TestVersionMenuPlayback(_MWHarness):

    def setUp(self):
        super().setUp()
        self.scene.expected_audio_slots = [{
            "slot_id": "b1:1", "block_id": "b1", "block_label": "B1",
            "part_index": 1, "part_of_block": 1, "speaker": "",
            "character_id": None,
        }]
        self.v01 = self.register_version("b1:1", 1, "outputs/p1_v01.wav")
        self.v02 = self.register_version("b1:1", 2, "outputs/p1_v02.wav")
        self.add_pending_job("b1:1")
        self.dlg = self.open_scene_dialog()

    def _menu_rows(self):
        menu = self.dlg._build_versions_menu(self.slot())
        rows = []
        for act in menu.actions():
            if isinstance(act, QWidgetAction):
                w = act.defaultWidget()
                if isinstance(w, _VersionMenuRow):
                    rows.append(w)
        return rows

    def _row_buttons(self, row):
        buttons = row.findChildren(QPushButton)
        # [Play, Use] per the row layout.
        self.assertGreaterEqual(len(buttons), 2)
        return buttons[0], buttons[-1]

    def test_menu_lists_every_version_with_play_and_use(self):
        rows = self._menu_rows()
        self.assertEqual(len(rows), 2)
        for row in rows:
            play, use = self._row_buttons(row)
            self.assertTrue(use.isEnabled())

    def test_menu_play_plays_the_exact_version(self):
        """§6.11: Play from the version menu plays THAT version."""
        v01_row, v02_row = self._menu_rows()
        # v01 row (rows ordered by part_version).
        play01, _ = self._row_buttons(v01_row)
        play01.click()
        self.assertEqual(
            self.win._waveform.current_path,
            os.path.join(self.tmp, "outputs/p1_v01.wav"))
        play02, _ = self._row_buttons(v02_row)
        play02.click()
        self.assertEqual(
            self.win._waveform.current_path,
            os.path.join(self.tmp, "outputs/p1_v02.wav"))
        # Preview must not have changed the Scene's selection.
        self.assertEqual(self.scene.selected_block_audio, {})

    def test_menu_use_changes_selection_without_playing(self):
        """§6.12/§12 L: Use = selection mutation only — never plays."""
        v01_row, _ = self._menu_rows()
        _, use01 = self._row_buttons(v01_row)
        use01.click()
        _process(20)
        self.assertEqual(self.scene.selected_block_audio.get(self.slot()),
                         self.v01["id"])
        self.assertEqual(self.played, [],
                         "Use must NOT trigger playback")
        # The authoritative resolver now returns v01 (§10 chain).
        resolved = resolved_asset_for_slot(self.scene, self.slot())
        self.assertEqual(resolved["id"], self.v01["id"])
        # ... and the refreshed row Play follows the same resolver.
        act = self.dlg._table.cellWidget(0, self.dlg._col_act)
        act.play_button().click()
        self.assertEqual(
            self.win._waveform.current_path,
            os.path.join(self.tmp, "outputs/p1_v01.wav"))

    def test_menu_marks_current_version_in_use(self):
        self.scene.selected_block_audio[self.slot()] = self.v01["id"]
        rows = self._menu_rows()
        labels = rows[0].findChildren(QLabel)
        self.assertTrue(any("in use" in l.text() for l in labels))

    def test_missing_file_version_clear_unavailable_state(self):
        """§5/§12 P: a version whose file is gone stays listed, Play is
        disabled with an explanation, no crash, no silent fallback."""
        v03 = self.register_version("b1:1", 3, "outputs/p1_v03.wav")
        os.remove(os.path.join(self.tmp, "outputs/p1_v03.wav"))
        rows = self._menu_rows()
        self.assertEqual(len(rows), 3)
        v03_row = rows[2]
        play03, _ = self._row_buttons(v03_row)
        self.assertFalse(play03.isEnabled())
        self.assertIn("missing", play03.toolTip())
        labels = v03_row.findChildren(QLabel)
        self.assertTrue(any("file missing" in l.text() for l in labels))
        # The other versions remain playable.
        play01, _ = self._row_buttons(rows[0])
        play01.click()
        self.assertEqual(
            self.win._waveform.current_path,
            os.path.join(self.tmp, "outputs/p1_v01.wav"))

    def test_missing_selected_version_row_play_disabled_no_fallback(self):
        """§5: when the SELECTED version's file is missing the row Play
        is DISABLED — it must not silently play another version."""
        os.remove(os.path.join(self.tmp, "outputs/p1_v02.wav"))
        self.scene.selected_block_audio[self.slot()] = self.v02["id"]
        self.dlg.refresh_scene_context()
        act = self.dlg._table.cellWidget(0, self.dlg._col_act)
        self.assertFalse(act.play_button().isEnabled())
        self.assertIn("missing", act.play_button().toolTip())
        # v01's file still exists — but Play must not fall back to it.
        self.assertEqual(self.played, [])


# ===========================================================================
# §1/§7/§8 — refresh integrity: no widget destruction, state preservation
# ===========================================================================
class TestRefreshIntegrity(_MWHarness):

    def setUp(self):
        super().setUp()
        self.scene.expected_audio_slots = [{
            "slot_id": "b1:1", "block_id": "b1", "block_label": "B1",
            "part_index": 1, "part_of_block": 1, "speaker": "",
            "character_id": None,
        }]
        self.v01 = self.register_version("b1:1", 1, "outputs/p1_v01.wav")
        self.job = self.add_pending_job("b1:1")
        self.dlg = self.open_scene_dialog()

    def _row_actions(self, row=0):
        return self.dlg._table.cellWidget(row, self.dlg._col_act)

    def test_status_update_keeps_widget_identity_and_selection(self):
        """§1/§7/§12 M: a job status change is a targeted update — the
        row widgets are NOT destroyed (identity), the current row stays
        selected, the checkbox stays checked."""
        self.dlg._table.setCurrentCell(0, 0)
        cb = self.dlg._table.cellWidget(0, self.dlg._col_check)._checkbox
        cb.setChecked(True)
        actions_before = self._row_actions()
        # The manager mutates the SAME job object (house reality).
        self.job.status = JobStatus.COMPLETED
        self.job.output_path = "outputs/p1_v01.wav"
        self.job.output_duration = 2.0
        self.dlg._on_manager_changed()
        _process(20)
        self.assertIs(self._row_actions(), actions_before,
                      "same-structure refresh must not rebuild rows")
        self.assertEqual(self.dlg._table.currentRow(), 0)
        self.assertTrue(cb.isChecked())
        self.assertTrue(cb.isEnabled())

    def test_focus_survives_in_place_refresh(self):
        """§7: keyboard focus on a row control is NOT lost when a
        manager update arrives (the P0 'row loses focus' symptom)."""
        btn = self._row_actions().play_button()
        btn.setFocus()
        _process(20)
        self.assertIs(QApplication.focusWidget(), btn)
        self.job.status = JobStatus.COMPLETED
        self.dlg._on_manager_changed()
        _process(20)
        self.assertIs(QApplication.focusWidget(), btn,
                      "focus must stay on the SAME widget instance")

    def test_playback_works_after_refresh(self):
        """§12 O: playback still works after a refresh."""
        self.job.status = JobStatus.COMPLETED
        self.dlg._on_manager_changed()
        self._row_actions().play_button().click()
        self.assertEqual(
            self.win._waveform.current_path,
            os.path.join(self.tmp, "outputs/p1_v01.wav"))

    def test_structural_rebuild_preserves_selection_scroll_checks(self):
        """§7: a REAL structural change (job added) rebuilds the table
        but restores current row, scroll position and checkbox states."""
        for i in range(2, 30):
            self.scene.expected_audio_slots.append({
                "slot_id": "b{0}:1".format(i), "block_id": "b{0}".format(i),
                "block_label": "B{0}".format(i), "part_index": i,
                "part_of_block": 1, "speaker": "", "character_id": None,
            })
            self.add_pending_job("b{0}:1".format(i), part_index=i,
                                 name="Part {0}".format(i))
        self.dlg._refresh_table()
        _process(20)
        # User state: row 5 selected+checked, scrolled down.
        self.dlg._table.setCurrentCell(5, 0)
        cb = self.dlg._table.cellWidget(5, self.dlg._col_check)._checkbox
        cb.setChecked(True)
        vbar = self.dlg._table.verticalScrollBar()
        _process(100)  # let the layout compute the scrollbar range
        vbar.setValue(min(400, vbar.maximum()))
        _process(50)
        prev_scroll = vbar.value()
        self.assertGreater(prev_scroll, 0, "scrollbar must be scrollable")
        # Structural change: another job joins the queue.
        self.add_pending_job("b1:1", part_index=99, name="Late",
                             part_version=9)
        self.dlg._refresh_table()
        _process(20)
        self.assertEqual(self.dlg._table.currentRow(), 5)
        self.assertTrue(self.dlg._check_states.get(5, False),
                        "checkbox state must survive the rebuild")
        self.assertEqual(vbar.value(), min(prev_scroll, vbar.maximum()),
                         "scroll position must be restored")

    def test_generated_cell_rebuilt_only_on_version_change(self):
        """§8: the Generated cell is replaced ONLY when the slot's
        derived version state changes — and the action band identity
        stays intact either way."""
        actions_before = self._row_actions()
        gen_before = self.dlg._table.cellWidget(0, self.dlg._col_gen)
        # Same-structure refresh with no version change: nothing rebuilt.
        self.dlg._refresh_table()
        self.assertIs(self.dlg._table.cellWidget(0, self.dlg._col_gen),
                      gen_before)
        # A new version lands → gen cell rebuilt, actions NOT.
        self.register_version("b1:1", 2, "outputs/p1_v02.wav")
        self.dlg._on_manager_changed()
        _process(20)
        self.assertIsNot(self.dlg._table.cellWidget(0, self.dlg._col_gen),
                         gen_before)
        self.assertIs(self._row_actions(), actions_before)


# ===========================================================================
# §9/§12 Q/R — Manual Batch Queue independence
# ===========================================================================
class TestManualModeIndependence(_MWHarness):

    def setUp(self):
        super().setUp()
        # Scene assets exist — they must NEVER leak into manual mode.
        self.scene.expected_audio_slots = [{
            "slot_id": "b1:1", "block_id": "b1", "block_label": "B1",
            "part_index": 1, "part_of_block": 1, "speaker": "",
            "character_id": None,
        }]
        self.register_version("b1:1", 1, "outputs/p1_v01.wav")
        self.win._on_open_batch_generation()
        _process(50)
        self.dlg = self.dialog()

    def _add_manual_job(self, status=JobStatus.COMPLETED,
                        output_path="outputs/manual1.wav", make_file=True):
        if make_file and output_path:
            _write_wav(os.path.join(self.tmp, output_path), 1.0)
        job = BatchJob(name="m", prompt="manual job")
        job.status = status
        job.output_path = output_path
        self.win._batch_manager.add_job(job)
        self.dlg._refresh_table()
        return job

    def _row_actions(self, row=0):
        return self.dlg._table.cellWidget(row, self.dlg._col_act)

    def test_manual_completed_job_playable_via_job_path(self):
        """§12 Q: manual playback still works (BatchJob.output_path)."""
        job = self._add_manual_job()
        btn = self._row_actions().play_button()
        self.assertTrue(btn.isEnabled())
        btn.click()
        self.assertEqual(self.win._waveform.current_path,
                         os.path.join(self.tmp, "outputs/manual1.wav"))
        self.assertEqual(self.played,
                         [os.path.join(self.tmp, "outputs/manual1.wav")])

    def test_manual_pending_not_playable(self):
        self._add_manual_job(status=JobStatus.PENDING,
                             output_path=None, make_file=False)
        self.assertFalse(self._row_actions().play_button().isEnabled())

    def test_manual_missing_file_not_playable(self):
        self._add_manual_job(make_file=False)
        self.assertFalse(self._row_actions().play_button().isEnabled())

    def test_scene_assets_do_not_leak_into_manual_mode(self):
        """§12 R: the Scene has a valid v01 asset; a manual PENDING job
        with no output stays UNPLAYABLE (independent resolution)."""
        job = self._add_manual_job(status=JobStatus.PENDING,
                                   output_path=None, make_file=False)
        self.assertIsNone(self.dlg._scene)
        self.assertFalse(self._row_actions().play_button().isEnabled())
        # The scene-mode dialog for the same manager state IS playable
        # (fresh scene queue — the manual job leaves the shared queue).
        self.win._close_batch_dialog_if_open()
        self.win._batch_manager.clear_all()
        self.add_pending_job("b1:1")
        self.win._present_batch_dialog(
            scene=self.scene, scene_id=self.scene.id, review_mode=True)
        _process(50)
        scene_dlg = self.dialog()
        act = scene_dlg._table.cellWidget(0, scene_dlg._col_act)
        self.assertTrue(act.play_button().isEnabled())


# ===========================================================================
# §6/§14 — the complete reopen-and-play golden path (real MainWindow)
# ===========================================================================
class TestReopenGoldenPath(_MWHarness):
    """§14: the exact user workflow —
    generate, refresh, play, select version, play, close Batch, reopen
    Scene, play old version, switch version, play new version, generate
    another version, refresh, play again."""

    def setUp(self):
        super().setUp()
        self.scene.expected_audio_slots = [{
            "slot_id": "b1:1", "block_id": "b1", "block_label": "B1",
            "part_index": 1, "part_of_block": 1, "speaker": "",
            "character_id": None,
        }]
        self.part = _make_part(block_id="b1", block_label="B1")

    def test_full_golden_path(self):
        # --- 1. Generate v01 (real pipeline: splitter→jobs→fake model). --
        self.start_long([self.part])
        self.wait_batch()
        self.assertEqual(len(self.scene.audio_assets), 1)
        v01 = self.scene.audio_assets[0]
        self.assertEqual(v01["part_version"], 1)

        # --- 2. Refresh (manager updates already flowed; force one). ----
        self.dialog()._on_manager_changed()
        _process(20)

        # --- 3. Play → v01. ---------------------------------------------
        act = self.dialog()._table.cellWidget(
            0, self.dialog()._col_act)
        self.assertTrue(act.play_button().isEnabled())
        act.play_button().click()
        v01_abs = os.path.join(self.tmp, v01["output_path"])
        self.assertEqual(self.win._waveform.current_path, v01_abs)

        # --- 4. Generate a second version (second run). ------------------
        self.start_long([self.part])
        self.wait_batch()
        self.assertEqual(len(self.scene.audio_assets), 2)
        v02 = self.scene.audio_assets[1]
        self.assertEqual(v02["part_version"], 2)

        # --- 5. Play → v02 (latest). --------------------------------------
        act = self.dialog()._table.cellWidget(
            0, self.dialog()._col_act)
        act.play_button().click()
        self.assertEqual(self.win._waveform.current_path,
                         os.path.join(self.tmp, v02["output_path"]))

        # --- 6. Select v01 via the version menu (Use). --------------------
        menu = self.dialog()._build_versions_menu("b1:1")
        rows = [a.defaultWidget() for a in menu.actions()
                if isinstance(a, QWidgetAction)
                and isinstance(a.defaultWidget(), _VersionMenuRow)]
        self.assertEqual(len(rows), 2)
        _, use01 = rows[0].findChildren(QPushButton)[0], \
            rows[0].findChildren(QPushButton)[-1]
        use01.click()
        _process(20)
        self.assertEqual(
            self.scene.selected_block_audio.get("b1:1"), v01["id"])

        # --- 7. Play → v01 (the explicitly selected version). -------------
        act = self.dialog()._table.cellWidget(
            0, self.dialog()._col_act)
        act.play_button().click()
        self.assertEqual(self.win._waveform.current_path, v01_abs)

        # --- 8. Version-menu Play v02 → exact v02, selection unchanged. ---
        menu = self.dialog()._build_versions_menu("b1:1")
        rows = [a.defaultWidget() for a in menu.actions()
                if isinstance(a, QWidgetAction)
                and isinstance(a.defaultWidget(), _VersionMenuRow)]
        play02 = rows[1].findChildren(QPushButton)[0]
        play02.click()
        self.assertEqual(self.win._waveform.current_path,
                         os.path.join(self.tmp, v02["output_path"]))
        self.assertEqual(
            self.scene.selected_block_audio.get("b1:1"), v01["id"],
            "preview must not change the selection")

        # --- 9. Close Batch; reopen the same Scene. ------------------------
        self.close_batch()
        self.assertIsNone(self.win._batch_dialog)
        self.start_long([self.part], review=True)
        _process(50)
        dlg = self.dialog()
        self.assertIsNotNone(dlg)
        # Reopened state: fresh PENDING jobs (queue execution state).
        jobs = self.win._batch_manager.jobs
        self.assertTrue(all(j.status == JobStatus.PENDING for j in jobs))
        self.assertTrue(all(j.output_path is None for j in jobs))
        # Both existing versions visible.
        versions = slot_versions(self.scene, "b1:1")
        self.assertEqual(len(versions), 2)
        gen = dlg._table.cellWidget(0, dlg._col_gen)
        texts = [l.text() for l in gen.findChildren(QLabel)] + \
                [b.text() for b in gen.findChildren(QPushButton)]
        self.assertTrue(any("2 versions" in t for t in texts),
                        "the Generated cell must show both versions: {0}".format(
                            texts))
        # v01 playable (selection persisted through reopen).
        act = dlg._table.cellWidget(0, dlg._col_act)
        self.assertTrue(act.play_button().isEnabled())
        act.play_button().click()
        self.assertEqual(self.win._waveform.current_path, v01_abs)
        # v02 playable: switch version → play.
        menu = dlg._build_versions_menu("b1:1")
        rows = [a.defaultWidget() for a in menu.actions()
                if isinstance(a, QWidgetAction)
                and isinstance(a.defaultWidget(), _VersionMenuRow)]
        use02 = rows[1].findChildren(QPushButton)[-1]
        use02.click()
        _process(20)
        act = dlg._table.cellWidget(0, dlg._col_act)
        act.play_button().click()
        self.assertEqual(self.win._waveform.current_path,
                         os.path.join(self.tmp, v02["output_path"]))

        # --- 10. Refresh; row selection + playback intact (§12 M/N/O). ----
        dlg._table.setCurrentCell(0, 0)
        self.win._batch_manager.jobs[0].status = JobStatus.COMPLETED
        dlg._on_manager_changed()
        _process(20)
        self.assertEqual(dlg._table.currentRow(), 0)
        act = dlg._table.cellWidget(0, dlg._col_act)
        act.play_button().click()
        self.assertEqual(self.win._waveform.current_path,
                         os.path.join(self.tmp, v02["output_path"]))

        # --- 11. Generate another version (v03); refresh; play again. -----
        self.close_batch()
        self.start_long([self.part])
        self.wait_batch()
        self.assertEqual(len(self.scene.audio_assets), 3)
        v03 = self.scene.audio_assets[2]
        # The explicit v02 selection still wins (§3).
        dlg = self.dialog()
        act = dlg._table.cellWidget(0, dlg._col_act)
        act.play_button().click()
        self.assertEqual(self.win._waveform.current_path,
                         os.path.join(self.tmp, v02["output_path"]))
        # Switch to Latest → play → v03.
        self.scene.selected_block_audio.pop("b1:1", None)
        dlg.refresh_scene_context()
        act = dlg._table.cellWidget(0, dlg._col_act)
        act.play_button().click()
        self.assertEqual(self.win._waveform.current_path,
                         os.path.join(self.tmp, v03["output_path"]))

        # --- 12. BatchJob.output_path was never a provenance mirror. ------
        for j in self.win._batch_manager.jobs:
            if j.status == JobStatus.PENDING:
                self.assertIsNone(j.output_path)


if __name__ == "__main__":
    unittest.main()
