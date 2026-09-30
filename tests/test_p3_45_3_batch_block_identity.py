"""GUINEO P3.45.3 — BATCH BLOCK IDENTITY regression tests.

Spec: the Batch UI must make the existing Block → Part → Batch Job
relationship visible, deterministic and persistent — as a PROJECTION of
the existing model (slot_id / expected_audio_slots / part_version), NOT
a new identity system.

Coverage matrix (task §19):

  Identity      1-5   block/part exposure, slot mapping, deterministic order
  Regeneration  6-7   structural Part preserved, versions associated
  Duplication   8-10  P3.44.6 manual independence survives the new UI
  Queue order   11-12 reordering never becomes structural identity
  Save/load     13-16 presentation + version + duplicate survive reload
  Re-detect     17-19 behaviour unchanged, no false mapping, no stale id
  Generation    20-22 correct row, selective-only, failure keeps mapping
  UI            23-26 identity/part/state/version visible + geometry

Real-widget integration (task §20): TestRealGenerationIdentity drives
the PRODUCTION path — _start_long_narration (slot materialisation +
BatchJob creation) → real BatchGenerationDialog → real engine run
(model boundary faked) → register_generation_result → the rendered
Block/Part chips — never only pure object tests.

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_45_3_batch_block_identity.py -v
"""
from __future__ import annotations

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

# ---------------------------------------------------------------------------
# Fake torch (house pattern: test_p3_28 / test_p3_44_5 / test_p3_44_6)
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

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QLabel, QPushButton,
)
from unittest.mock import patch, PropertyMock

_app = QApplication.instance() or QApplication([])

from engine.batch_manager import BatchJob, BatchManager, JobStatus
from engine.models import GenerationParameters, Project, Scene
from engine.audio_provenance import (
    assets_for_slot, block_slot_id, coverage_counts, materialize_expected_slots,
    plain_slot_id, register_generation_result, resolved_asset_for_slot,
    resolved_slot_sources, slot_identity_summary, slot_versions,
)
from engine.narration_splitter import SplitPart

SAMPLE_RATE = 24000


def make_speech(seconds: float, freq: float = 220.0) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (0.2 * np.sin(2 * math.pi * freq * t)).astype(np.float32)


class FakeHiggsModel:
    """Model-boundary fake (P3.27B house pattern)."""

    calls = []
    speech_s = 1.0

    def generate_speech(self, text, tokenizer, *, reference_audio=None,
                        reference_sample_rate=None, reference_codes=None,
                        reference_text=None, max_new_tokens=2048,
                        temperature=1.0, top_p=None, top_k=None):
        FakeHiggsModel.calls.append(dict(text=text))
        return make_speech(FakeHiggsModel.speech_s)


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


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _make_part(text="A test sentence for the part.", speaker=None,
               block_id=None, part_of_block=1, total_in_block=1,
               block_label="", character_id=None, est=8.0):
    return SplitPart(
        text=text, prompt=text, speaker=speaker or "",
        source_block_id=block_id, part_of_block=part_of_block,
        total_parts_in_block=total_in_block, block_label=block_label,
        character_id=character_id, estimated_duration=est,
        char_count=len(text))


def _scene_with(parts, project=None, scene_id="sc1abcd", name="Scene 03"):
    """Project + Scene with the expected-slot structure materialised."""
    project = project or Project(name="Eden", id="proj1")
    scene = Scene(id=scene_id, name=name, project_id=project.id)
    project.add_scene(scene)
    scene.expected_audio_slots = materialize_expected_slots(parts)
    scene.audio_assets = []
    scene.combined_outputs = []
    return project, scene


# ===========================================================================
# Identity (model level — the derivation is pure, no Qt)
# ===========================================================================
class TestSlotIdentitySummary(unittest.TestCase):
    """engine.audio_provenance.slot_identity_summary — the ONE derivation
    the Batch UI projects (never queue position)."""

    def _summary(self, parts):
        _p, scene = _scene_with(parts)
        return scene, [slot_identity_summary(scene, s["slot_id"])
                       for s in scene.expected_audio_slots]

    def test_one_block_one_part(self):
        _s, infos = self._summary([_make_part(block_id="b1")])
        self.assertEqual(len(infos), 1)
        info = infos[0]
        self.assertEqual(info["block_id"], "b1")
        self.assertEqual(info["block_number"], 1)
        self.assertEqual(info["part_of_block"], 1)
        self.assertEqual(info["parts_in_block"], 1)
        self.assertEqual(info["user_label"], "")
        self.assertEqual(info["part_index"], 1)

    def test_two_part_block_exposes_both_parts(self):
        _s, infos = self._summary([
            _make_part("One.", block_id="b1", part_of_block=1, total_in_block=2),
            _make_part("Two.", block_id="b1", part_of_block=2, total_in_block=2),
        ])
        self.assertEqual([i["part_of_block"] for i in infos], [1, 2])
        self.assertEqual([i["parts_in_block"] for i in infos], [2, 2])
        self.assertEqual([i["block_number"] for i in infos], [1, 1])
        self.assertEqual([i["slot_id"] for i in infos],
                         ["b1:1", "b1:2"])
        self.assertEqual([i["part_index"] for i in infos], [1, 2])

    def test_three_part_block_numbering(self):
        _s, infos = self._summary([
            _make_part("A.", block_id="b1", part_of_block=1, total_in_block=3),
            _make_part("B.", block_id="b1", part_of_block=2, total_in_block=3),
            _make_part("C.", block_id="b1", part_of_block=3, total_in_block=3),
        ])
        self.assertEqual([i["part_of_block"] for i in infos], [1, 2, 3])
        self.assertEqual(infos[0]["parts_in_block"], 3)

    def test_each_part_maps_to_expected_slot_id(self):
        parts = [
            _make_part("A.", block_id="b1", part_of_block=1),
            _make_part("B.", block_id="b1", part_of_block=2),
            _make_part("C.", block_id="b2", part_of_block=1),
            _make_part("D.", speaker="CAPTAIN"),
        ]
        _p, scene = _scene_with(parts)
        for slot in scene.expected_audio_slots:
            info = slot_identity_summary(scene, slot["slot_id"])
            self.assertIsNotNone(info, slot["slot_id"])
            self.assertEqual(info["slot_id"], slot["slot_id"])
            self.assertEqual(info["block_id"], slot["block_id"])
            self.assertEqual(info["part_index"], slot["part_index"])
            if slot["block_id"]:
                self.assertEqual(
                    slot["slot_id"],
                    block_slot_id(slot["block_id"], slot["part_of_block"]))
            else:
                self.assertEqual(
                    slot["slot_id"],
                    plain_slot_id(slot["part_index"]))

    def test_two_blocks_independent_numbering(self):
        _s, infos = self._summary([
            _make_part("A.", block_id="bx", part_of_block=1, total_in_block=2),
            _make_part("B.", block_id="bx", part_of_block=2, total_in_block=2),
            _make_part("C.", block_id="by", part_of_block=1),
        ])
        self.assertEqual([i["block_number"] for i in infos], [1, 1, 2])
        self.assertEqual([i["parts_in_block"] for i in infos], [2, 2, 1])

    def test_ordering_deterministic(self):
        parts = [_make_part("A.", block_id="b1"),
                 _make_part("B.", block_id="b2"),
                 _make_part("C.", block_id="b1", part_of_block=2)]
        _p, scene = _scene_with(parts)
        first = [slot_identity_summary(scene, s["slot_id"])
                 for s in scene.expected_audio_slots]
        for _ in range(3):
            again = [slot_identity_summary(scene, s["slot_id"])
                     for s in scene.expected_audio_slots]
            self.assertEqual(again, first)

    def test_user_label_vs_auto_block_label(self):
        _p, scene = _scene_with([
            _make_part("A.", block_id="b1", block_label="Intro"),
            _make_part("B.", block_id="b2", block_label="Block 2"),
        ])
        labelled = slot_identity_summary(scene, "b1:1")
        auto = slot_identity_summary(scene, "b2:1")
        self.assertEqual(labelled["user_label"], "Intro")
        self.assertEqual(labelled["block_label"], "Intro")
        # The splitter's auto "Block {N}" form is positional metadata, NOT
        # a user label — display layers must not repeat it beside "B{N}".
        self.assertEqual(auto["user_label"], "")
        self.assertEqual(auto["block_label"], "Block 2")

    def test_plain_and_speaker_slots_have_no_block_identity(self):
        _p, scene = _scene_with([
            _make_part("Narration."),
            _make_part("[CAPTAIN] Aye.", speaker="CAPTAIN"),
        ])
        plain = slot_identity_summary(scene, "plain:1")
        speaker = slot_identity_summary(scene, "plain:2")
        for info in (plain, speaker):
            self.assertIsNone(info["block_id"])
            self.assertIsNone(info["block_number"])
            self.assertEqual(info["parts_in_block"], 0)
        self.assertEqual(plain["part_index"], 1)
        self.assertEqual(speaker["speaker"], "CAPTAIN")
        self.assertEqual(speaker["part_index"], 2)

    def test_unknown_slot_returns_none(self):
        _p, scene = _scene_with([_make_part(block_id="b1")])
        self.assertIsNone(slot_identity_summary(scene, "b1:2"))
        self.assertIsNone(slot_identity_summary(scene, "plain:9"))
        self.assertIsNone(slot_identity_summary(scene, "zzz:1"))

    def test_invalid_slot_id_returns_none(self):
        _p, scene = _scene_with([_make_part(block_id="b1")])
        self.assertIsNone(slot_identity_summary(scene, ""))
        self.assertIsNone(slot_identity_summary(scene, None))
        self.assertIsNone(slot_identity_summary(None, "b1:1"))

    def test_materialize_round_trip_against_real_splitter_output(self):
        # Integration with the REAL builder: block labels, part numbering
        # and slot ids survive the materialise → summary round trip.
        parts = [
            _make_part("A.", block_id="blk01", part_of_block=1,
                       total_in_block=2, block_label="Intro"),
            _make_part("B.", block_id="blk01", part_of_block=2,
                       total_in_block=2, block_label="Intro"),
            _make_part("C.", block_id="blk02", block_label="Block 2"),
        ]
        _p, scene = _scene_with(parts)
        self.assertEqual(
            slot_identity_summary(scene, "blk01:1")["user_label"], "Intro")
        self.assertEqual(
            slot_identity_summary(scene, "blk02:1")["block_number"], 2)
        self.assertEqual(
            slot_identity_summary(scene, "blk01:2")["part_of_block"], 2)


# ===========================================================================
# Regeneration / duplication / queue order / save-load (model level)
# ===========================================================================
class TestBatchIdentityModel(unittest.TestCase):
    """The identity invariants the UI projects (engine-level)."""

    def _job(self, slot_id="b1:2", part_index=2, version=1, block="b1"):
        return BatchJob(
            name="Part 2", prompt="Text.", project="Eden",
            scene_id="sc1abcd", scene_name="Scene 03",
            part_index=part_index, part_version=version,
            source_block_id=block, generation_run="sc1abcd-r001",
            slot_id=slot_id,
            output_filename="Eden_S03_Long_v01_Part_002.wav")

    def test_regen_preserves_structural_part(self):
        bm = BatchManager(submit_fn=lambda req: None)
        job = self._job()
        job.status = JobStatus.COMPLETED
        bm.add_job(job)
        self.assertTrue(bm.regen_job(0))
        self.assertEqual(job.slot_id, "b1:2")
        self.assertEqual(job.part_index, 2)
        self.assertEqual(job.source_block_id, "b1")
        self.assertEqual(job.status, JobStatus.PENDING)

    def test_duplicate_strips_structural_provenance(self):
        # P3.44.6 CONTROL: duplication means a new MANUAL job, never a
        # second generation of the same structural slot.
        bm = BatchManager(submit_fn=lambda req: None)
        bm.add_job(self._job())
        idx = bm.duplicate_job(0)
        self.assertIsNotNone(idx)
        clone = bm.jobs[idx]
        self.assertIsNone(clone.slot_id)
        self.assertIsNone(clone.part_index)
        self.assertIsNone(clone.part_version)
        self.assertIsNone(clone.expected_duration)
        self.assertIsNone(clone.source_block_id)
        self.assertIsNone(clone.generation_run)
        # Scene/speaker context IS preserved (History lineage, P3.23 §22).
        self.assertEqual(clone.scene_id, "sc1abcd")
        self.assertIn("(copy)", clone.name)

    def test_duplicate_request_joins_no_slot(self):
        bm = BatchManager(submit_fn=lambda req: None)
        bm.add_job(self._job())
        bm.duplicate_job(0)
        request = bm.jobs[1].to_request()
        self.assertIsNone(request.slot_id)
        self.assertIsNone(request.block_id)

    def test_to_request_forwards_full_identity(self):
        bm = BatchManager(submit_fn=lambda req: None)
        bm.add_job(self._job())
        request = bm.jobs[0].to_request()
        self.assertEqual(request.slot_id, "b1:2")
        self.assertEqual(request.block_id, "b1")
        self.assertEqual(request.part_index, 2)
        self.assertEqual(request.part_version, 1)
        self.assertEqual(request.generation_run, "sc1abcd-r001")

    def test_move_job_preserves_identity(self):
        bm = BatchManager(submit_fn=lambda req: None)
        bm.add_job(self._job(slot_id="b1:1", part_index=1))
        bm.add_job(self._job(slot_id="b2:1", part_index=2,
                              block="b2"))
        bm.move_job(1, 0)
        self.assertEqual([j.slot_id for j in bm.jobs], ["b2:1", "b1:1"])
        # The job OBJECT keeps its own identity — only order changed.
        self.assertEqual(bm.jobs[0].source_block_id, "b2")
        self.assertEqual(bm.jobs[0].slot_id, "b2:1")
        self.assertEqual(bm.jobs[1].source_block_id, "b1")

    def test_queue_yaml_round_trip_preserves_identity(self):
        bm = BatchManager(submit_fn=lambda req: None)
        bm.add_job(self._job())
        bm.add_job(self._plain_job())
        tmp = tempfile.mkdtemp(prefix="ss_p3453_yaml_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = bm.save_to_file(os.path.join(tmp, "queue.yaml"))
        bm2 = BatchManager(submit_fn=lambda req: None)
        bm2.load_from_file(path, replace=True)
        jobs = bm2.jobs
        self.assertEqual(jobs[0].slot_id, "b1:2")
        self.assertEqual(jobs[0].source_block_id, "b1")
        self.assertEqual(jobs[0].part_index, 2)
        self.assertEqual(jobs[0].part_version, 1)
        self.assertEqual(jobs[0].generation_run, "sc1abcd-r001")
        self.assertEqual(jobs[1].slot_id, "plain:5")
        # A duplicated (slot-less) job survives the round trip too.
        bm3 = BatchManager(submit_fn=lambda req: None)
        bm3.add_job(self._job())
        bm3.duplicate_job(0)
        p3 = bm3.save_to_file(os.path.join(tmp, "q3.yaml"))
        bm4 = BatchManager(submit_fn=lambda req: None)
        bm4.load_from_file(p3, replace=True)
        self.assertIsNone(bm4.jobs[1].slot_id)

    def _plain_job(self):
        return BatchJob(
            name="Part 5", prompt="Plain.", project="Eden",
            scene_id="sc1abcd", part_index=5, part_version=3,
            slot_id="plain:5")

    def test_scene_dict_round_trip_preserves_structure(self):
        parts = [
            _make_part("A.", block_id="b1", part_of_block=1, total_in_block=2,
                       block_label="Intro"),
            _make_part("B.", block_id="b1", part_of_block=2, total_in_block=2,
                       block_label="Intro"),
        ]
        project, scene = _scene_with(parts)
        data = scene.to_dict()
        restored = Scene.from_dict(data)
        self.assertEqual(
            [s["slot_id"] for s in restored.expected_audio_slots],
            ["b1:1", "b1:2"])
        info = slot_identity_summary(restored, "b1:2")
        self.assertEqual(info["user_label"], "Intro")
        self.assertEqual(info["part_of_block"], 2)
        self.assertEqual(info["parts_in_block"], 2)


# ===========================================================================
# UI — real Batch widgets (direct construction, P3.40 house pattern)
# ===========================================================================
class TestBatchIdentityWidgets(unittest.TestCase):
    """The Batch table as a projection of the model (scene mode)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p3453_ui_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _process(40)

    def _dialog(self, parts, jobs=None):
        from engine.settings_manager import SettingsManager
        from ui.panels.batch_generation import BatchGenerationDialog
        project, sc = _scene_with(parts)
        bm = BatchManager(submit_fn=lambda req: None)
        for j in (jobs if jobs is not None else
                  [BatchJob(name="Part {0}".format(i + 1),
                            prompt="Text {0}.".format(i + 1),
                            slot_id=s["slot_id"])
                   for i, s in enumerate(sc.expected_audio_slots)]):
            bm.add_job(j)
        sm = SettingsManager(os.path.join(self.tmp, "settings"))
        dlg = BatchGenerationDialog(bm, [], parent=None, scene=sc,
                                    project=project, app_root=self.tmp,
                                    settings_manager=sm)
        dlg.resize(1100, 680)
        dlg.layout().activate()
        dlg.show()
        _process(80)
        return dlg

    def _name_cell(self, dlg, row):
        from ui.panels.batch_generation import _NameCell
        cell = dlg._table.cellWidget(row, dlg._col_name)
        self.assertIsInstance(cell, _NameCell)
        return cell

    # -- 23: block identity visible ------------------------------------
    def test_block_identity_visible_in_name_cell(self):
        dlg = self._dialog([
            _make_part("A.", block_id="b1"),
            _make_part("B.", block_id="b2", block_label="Intro"),
        ])
        self.addCleanup(dlg.close)
        self.assertEqual(self._name_cell(dlg, 0).identity_text(), "B1")
        self.assertEqual(self._name_cell(dlg, 1).identity_text(),
                         "B2 · Intro")

    # -- 24: part identity where deterministically available -----------
    def test_multi_part_block_shows_part_fraction(self):
        dlg = self._dialog([
            _make_part("A.", block_id="b1", part_of_block=1, total_in_block=2),
            _make_part("B.", block_id="b1", part_of_block=2, total_in_block=2),
            _make_part("C.", block_id="b2"),
        ])
        self.addCleanup(dlg.close)
        self.assertEqual(self._name_cell(dlg, 0).identity_text(),
                         "B1 · Part 1/2")
        self.assertEqual(self._name_cell(dlg, 1).identity_text(),
                         "B1 · Part 2/2")
        # A single-Part Block carries no fraction (the row IS the block).
        self.assertEqual(self._name_cell(dlg, 2).identity_text(), "B2")

    def test_plain_part_identity(self):
        dlg = self._dialog([
            _make_part("Narration."),
            _make_part("[CAPTAIN] Aye.", speaker="CAPTAIN"),
        ])
        self.addCleanup(dlg.close)
        self.assertEqual(self._name_cell(dlg, 0).identity_text(), "Part 1")
        self.assertEqual(self._name_cell(dlg, 1).identity_text(),
                         "Part 2 · CAPTAIN")

    def test_manual_window_has_no_identity_layer(self):
        # The legacy manual Batch Queue is untouched: plain item cell,
        # no chip, original header.
        from engine.settings_manager import SettingsManager
        from ui.panels.batch_generation import BatchGenerationDialog
        bm = BatchManager(submit_fn=lambda req: None)
        bm.add_job(BatchJob(name="Voice over", prompt="The quick brown fox."))
        sm = SettingsManager(os.path.join(self.tmp, "settings"))
        dlg = BatchGenerationDialog(bm, [], parent=None, app_root=self.tmp,
                                    settings_manager=sm)
        dlg.show()
        _process(50)
        self.addCleanup(dlg.close)
        self.assertIsNone(dlg._table.cellWidget(0, dlg._col_name))
        self.assertIsNotNone(dlg._table.item(0, dlg._col_name))
        self.assertEqual(
            dlg._table.horizontalHeaderItem(dlg._col_name).text(),
            "File Name")
        self.assertEqual(dlg._table.item(0, dlg._col_name).text(),
                         "Voice over")

    def test_manual_job_in_scene_mode_is_marked(self):
        # Flow D presentation: a duplicated / manually added row in the
        # scene window is visibly NOT a structural Part.
        parts = [_make_part("A.", block_id="b1")]
        dlg = self._dialog(parts, jobs=[
            BatchJob(name="Part 1", prompt="Text.", slot_id="b1:1"),
            BatchJob(name="Part 1 (copy)", prompt="Text."),
        ])
        self.addCleanup(dlg.close)
        self.assertEqual(self._name_cell(dlg, 0).identity_text(), "B1")
        self.assertEqual(self._name_cell(dlg, 1).identity_text(),
                         "Manual job")
        self.assertIn("P3.44.6", self._name_cell(dlg, 1).toolTip())

    def test_unlinked_job_is_marked_honestly(self):
        # A job whose slot is not in the CURRENT structure (e.g. a queue
        # from an older structure) — never a fabricated block mapping.
        parts = [_make_part("A.", block_id="newblk")]
        old_job = BatchJob(name="Old part", prompt="Old.",
                           slot_id="oldblk:1", part_index=1)
        dlg = self._dialog(parts, jobs=[old_job])
        self.addCleanup(dlg.close)
        cell = self._name_cell(dlg, 0)
        self.assertEqual(cell.identity_text(), "Unlinked part")
        self.assertNotIn("B1", cell.identity_text())

    def test_header_labels_and_queue_tooltip(self):
        dlg = self._dialog([_make_part("A.", block_id="b1")])
        self.addCleanup(dlg.close)
        self.assertEqual(
            dlg._table.horizontalHeaderItem(dlg._col_name).text(),
            "Part / File")
        tip = dlg._table.item(0, dlg._col_num).toolTip()
        self.assertIn("Queue position", tip)

    # -- 26: geometry unchanged ------------------------------------------
    def test_row_height_and_cell_geometry_unchanged(self):
        dlg = self._dialog([
            _make_part("A.", block_id="b1", part_of_block=1, total_in_block=2),
            _make_part("B.", block_id="b1", part_of_block=2, total_in_block=2),
        ])
        self.addCleanup(dlg.close)
        for row in range(2):
            self.assertEqual(dlg._table.rowHeight(row), 48)
            cell = self._name_cell(dlg, row)
            # Same cell geometry contract as the Actions band (P3.40):
            # full 47px content height, anchored at the row's top.
            self.assertEqual(cell.height(), 47)
            self.assertEqual(cell.y(), row * 48)
            labels = cell.findChildren(QLabel)
            self.assertEqual(len(labels), 2)
            # Glyph boxes stay below the row-level pixel-contract sample
            # line (row_top+3): the identity label starts at >= 4px.
            self.assertGreaterEqual(labels[0].y(), 4)

    def test_name_cell_click_selects_row(self):
        dlg = self._dialog([
            _make_part("A.", block_id="b1"),
            _make_part("B.", block_id="b2"),
        ])
        self.addCleanup(dlg.close)
        cell = self._name_cell(dlg, 1)
        QTest.mouseClick(cell, Qt.MouseButton.LeftButton)
        _process(40)
        self.assertEqual(
            [r.row() for r in dlg._table.selectionModel().selectedRows()],
            [1])

    def test_in_place_update_changes_name_without_rebuild(self):
        dlg = self._dialog([_make_part("A.", block_id="b1")])
        self.addCleanup(dlg.close)
        job = dlg._manager.jobs[0]
        cell_before = self._name_cell(dlg, 0)
        self.assertEqual(cell_before.name_text(), "Part 1")
        # A completion lands (output_path appears) — same widget object,
        # updated text (P3.44 §7/§8: no widget destruction).
        job.status = JobStatus.COMPLETED
        job.output_path = "outputs/Eden_S03_Long_v01_Part_001.wav"
        dlg._refresh_table()
        cell_after = self._name_cell(dlg, 0)
        self.assertIs(cell_after, cell_before)
        self.assertEqual(cell_after.name_text(),
                         "Eden_S03_Long_v01_Part_001.wav")
        self.assertEqual(cell_after.identity_text(), "B1")

    def test_skipped_row_name_deemphasised(self):
        parts = [_make_part("A.", block_id="b1")]
        job = BatchJob(name="Part 1", prompt="Text.", slot_id="b1:1")
        job.status = JobStatus.SKIPPED
        dlg = self._dialog(parts, jobs=[job])
        self.addCleanup(dlg.close)
        cell = self._name_cell(dlg, 0)
        from ui.theme import Palette
        self.assertIn(Palette.TEXT_DISABLED.lower().lstrip("#"),
                      cell._name_label.styleSheet().lower())

    def test_job_edit_dialog_identity_note(self):
        from ui.panels.batch_generation import JobEditDialog
        dlg = self._dialog([_make_part("A.", block_id="b1")])
        self.addCleanup(dlg.close)
        structural = dlg._manager.jobs[0]
        manual = BatchJob(name="Part 1 (copy)", prompt="Text.")
        self.assertEqual(dlg._identity_note(structural),
                         "Scene Part: B1")
        self.assertIn("Manual Batch job", dlg._identity_note(manual))
        # The editor renders the note (read-only) — a duplicate can never
        # be mistaken for the original structural Part while editing.
        edit = JobEditDialog(structural, [], parent=None,
                             identity_note=dlg._identity_note(structural))
        notes = [l for l in edit.findChildren(QLabel)
                 if l.text() == "Scene Part: B1"]
        self.assertEqual(len(notes), 1)
        edit2 = JobEditDialog(manual, [], parent=None, identity_note="")
        self.assertFalse([l for l in edit2.findChildren(QLabel)
                          if l.text().startswith("Scene Part:")])

    # -- 25: state association -------------------------------------------
    def test_status_pill_associated_with_row(self):
        parts = [_make_part("A.", block_id="b1")]
        jobs = [BatchJob(name="Part 1", prompt="Text.", slot_id="b1:1"),
                BatchJob(name="Part 2", prompt="Text.", slot_id="b2:1")]
        jobs[0].status = JobStatus.COMPLETED
        dlg = self._dialog([_make_part("A.", block_id="b1"),
                            _make_part("B.", block_id="b2")], jobs=jobs)
        self.addCleanup(dlg.close)
        for row, expected in ((0, "Done"), (1, "Queued")):
            cell = dlg._table.cellWidget(row, dlg._col_status)
            texts = [l.text() for l in cell.findChildren(QLabel)]
            self.assertIn(expected, texts)


# ===========================================================================
# Real generation flow — real MainWindow + Engine (production path)
# ===========================================================================
class _MWHarness(unittest.TestCase):
    """Real MainWindow + Engine; the model boundary is faked; the batch
    pipeline (slot materialisation → BatchJob → engine run → asset
    registration → rendered rows) is the PRODUCTION code under test."""

    def setUp(self):
        from engine.engine import Engine
        from ui.main_window import MainWindow
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p3453_")
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
                try:
                    self.win.deleteLater()
                except Exception:
                    pass
        except Exception:
            pass
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        _process(60)
        for name in ("win", "engine", "project", "scene", "dlg", "job",
                     "played", "patchers", "tmp"):
            if hasattr(self, name):
                setattr(self, name, None)
        gc.collect()

    def params(self):
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

    def chip(self, row):
        cell = self.dialog()._table.cellWidget(
            row, self.dialog()._col_name)
        return cell.identity_text()

    def PARTS(self):
        # Block b1 → 2 parts; block b2 → 1 part (labelled "Conclusion").
        return [
            _make_part("First part of block one.", block_id="blk1",
                       part_of_block=1, total_in_block=2, block_label="Intro"),
            _make_part("Second part of block one.", block_id="blk1",
                       part_of_block=2, total_in_block=2, block_label="Intro"),
            _make_part("Whole second block.", block_id="blk2",
                       block_label="Conclusion"),
        ]


class TestRealGenerationIdentity(_MWHarness):
    """§20 golden path: creation → real UI → identity mapping →
    generation/registration → displayed Block/Part relationship."""

    def test_real_generation_flow_displays_block_part(self):
        # Flows A + B: one Block → one Part; one Block → two Parts.
        self.start_long(self.PARTS())
        self.wait_batch()
        dlg = self.dialog()
        bm = self.win._batch_manager
        # Correct rows, correct terminal state, correct identity fields.
        self.assertEqual([j.status for j in bm.jobs],
                         [JobStatus.COMPLETED] * 3)
        self.assertEqual([j.slot_id for j in bm.jobs],
                         ["blk1:1", "blk1:2", "blk2:1"])
        # Rendered Block/Part relationship (the P3.45.3 projection).
        self.assertEqual(self.chip(0), "B1 · Intro · Part 1/2")
        self.assertEqual(self.chip(1), "B1 · Intro · Part 2/2")
        self.assertEqual(self.chip(2), "B2 · Conclusion")
        # Assets registered with the preserved identity (one writer).
        self.assertEqual(len(self.scene.audio_assets), 3)
        self.assertEqual([a["slot_id"] for a in self.scene.audio_assets],
                         ["blk1:1", "blk1:2", "blk2:1"])
        self.assertEqual([a["part_version"] for a in self.scene.audio_assets],
                         [1, 1, 1])
        self.assertEqual(coverage_counts(self.scene), (3, 3))
        # The Generated cell shows the derived state (✓ + 1 version).
        for row in range(3):
            cell = dlg._table.cellWidget(row, dlg._col_gen)
            texts = [l.text() for l in cell.findChildren(QLabel)]
            self.assertIn("✓ Generated", texts, texts)

    def test_regen_preserves_part_and_creates_new_version(self):
        # Flow C: regeneration is a NEW VERSION of the SAME Part.
        self.start_long(self.PARTS())
        self.wait_batch()
        job = self.win._batch_manager.jobs[1]
        slot = job.slot_id
        self.assertEqual(slot, "blk1:2")
        self.win._on_batch_regen(1)
        self.wait_batch()
        job_after = self.win._batch_manager.jobs[1]
        self.assertIs(job_after, job)
        self.assertEqual(job_after.slot_id, slot)
        self.assertEqual(job_after.part_index, 2)
        self.assertEqual(job_after.status, JobStatus.COMPLETED)
        self.assertEqual(job_after.part_version, 2)
        # Two versions on the SAME slot; latest resolves to v02.
        versions = slot_versions(self.scene, slot)
        self.assertEqual([v["part_version"] for v in versions], [1, 2])
        resolved = resolved_asset_for_slot(self.scene, slot)
        self.assertEqual(resolved["part_version"], 2)
        # The chip is unchanged — same structural Part.
        self.assertEqual(self.chip(1), "B1 · Intro · Part 2/2")

    def test_duplicate_is_manual_and_joins_no_slot(self):
        # Flow D: the duplicate stays semantically distinct — generating
        # it never masquerades as the original structural Part.
        self.start_long(self.PARTS())
        self.wait_batch()
        bm = self.win._batch_manager
        assets_before = list(self.scene.audio_assets)
        idx = bm.duplicate_job(0)
        self.assertIsNotNone(idx)
        clone = bm.jobs[idx]
        self.assertIsNone(clone.slot_id)
        # The UI's duplicate handler refreshes the table explicitly —
        # mirror that before asserting the rendered chip.
        self.dialog()._refresh_table()
        _process(40)
        self.assertEqual(self.chip(idx), "Manual job")
        # The duplicate shares the output FILENAME with its source; the
        # user gives it its own before running it (Edit Batch Job) —
        # exactly the manual workflow the P3.44.6 contract describes.
        clone.output_filename = "Eden_Manual_take_001.wav"
        # Run the whole queue: only the PENDING clone executes.
        self.assertTrue(bm.start())
        self.wait_batch()
        self.assertEqual(clone.status, JobStatus.COMPLETED)
        # One new asset — with NO slot binding; the original slot's
        # version history is untouched.
        new_assets = [a for a in self.scene.audio_assets
                      if a not in assets_before]
        self.assertEqual(len(new_assets), 1)
        self.assertIsNone(new_assets[0]["slot_id"])
        self.assertEqual(
            len(assets_for_slot(self.scene, "blk1:1")), 1)
        self.dialog()._refresh_table()
        _process(40)
        self.assertEqual(self.chip(idx), "Manual job")

    def test_duplicate_filename_collision_is_guarded(self):
        # The duplicate COPIES the output filename; generating it as-is
        # must NEVER overwrite the original Part's file — the output
        # guard refuses (P3.27B/P3.44.6 protection), the row FAILS, and
        # the original slot's audio + version history are untouched.
        self.start_long(self.PARTS())
        self.wait_batch()
        bm = self.win._batch_manager
        original = bm.jobs[0]
        original_path = original.output_path
        assets_before = list(self.scene.audio_assets)
        idx = bm.duplicate_job(0)
        clone = bm.jobs[idx]
        self.assertEqual(clone.output_filename,
                        original.output_filename)
        self.assertTrue(bm.start())
        self.wait_batch()
        self.assertEqual(clone.status, JobStatus.FAILED)
        self.assertIn("already exists", clone.error or "")
        # Nothing was overwritten, no asset was appended, and the
        # duplicate still shows the honest manual identity.
        self.assertEqual(self.scene.audio_assets, assets_before)
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, original_path)))
        self.assertEqual(
            len(assets_for_slot(self.scene, "blk1:1")), 1)
        self.dialog()._refresh_table()
        _process(40)
        self.assertEqual(self.chip(idx), "Manual job")

    def test_reorder_queue_keeps_structural_identity(self):
        # Flow F: queue order changes; structural identity does not.
        self.start_long(self.PARTS(), review=True)
        dlg = self.dialog()
        bm = self.win._batch_manager
        before_slots = [j.slot_id for j in bm.jobs]
        before_structure = [s["slot_id"]
                            for s in self.scene.expected_audio_slots]
        self.assertEqual(before_slots, before_structure)
        self.assertEqual(self.chip(0), "B1 · Intro · Part 1/2")
        bm.move_job(2, 0)
        dlg._refresh_table()
        _process(40)
        # Queue position (#) changed; the chips travelled WITH the jobs.
        self.assertEqual([j.slot_id for j in bm.jobs],
                         [before_slots[2], before_slots[0], before_slots[1]])
        self.assertEqual(self.chip(0), "B2 · Conclusion")
        self.assertEqual(self.chip(1), "B1 · Intro · Part 1/2")
        # The Scene's structural order is untouched by the queue reorder.
        self.assertEqual([s["slot_id"] for s
                          in self.scene.expected_audio_slots],
                         before_structure)

    def test_generate_selected_affects_only_checked(self):
        # Flow G: selective run — unchecked jobs keep state AND identity.
        self.start_long(self.PARTS(), review=True)
        dlg = self.dialog()
        bm = self.win._batch_manager
        dlg._check_states = {0: True, 1: False, 2: False}
        self.win._on_generate_selected([0])
        self.wait_batch()
        self.assertEqual(bm.jobs[0].status, JobStatus.COMPLETED)
        self.assertEqual([j.status for j in bm.jobs[1:]],
                         [JobStatus.PENDING, JobStatus.PENDING])
        # Unselected jobs keep their Block identity untouched.
        self.assertEqual([j.slot_id for j in bm.jobs],
                         ["blk1:1", "blk1:2", "blk2:1"])
        self.assertEqual(self.chip(1), "B1 · Intro · Part 2/2")
        self.assertEqual(self.chip(2), "B2 · Conclusion")
        # Only the selected slot has an asset.
        self.assertEqual(
            [a["slot_id"] for a in self.scene.audio_assets], ["blk1:1"])

    def test_failed_generation_keeps_structural_mapping(self):
        # Flow H: failure must not destroy (or move) the mapping.
        class _FailingModel:
            def generate_speech(self, *a, **k):
                raise RuntimeError("synthetic model failure")

        self.patchers.append(
            patch.object(type(self.engine._model),
                         "get_model_and_tokenizer",
                         return_value=(_FailingModel(), None)))
        self.patchers[-1].start()
        self.start_long(self.PARTS())
        self.wait_batch()
        bm = self.win._batch_manager
        self.assertEqual([j.status for j in bm.jobs],
                         [JobStatus.FAILED] * 3)
        self.assertEqual([j.slot_id for j in bm.jobs],
                         ["blk1:1", "blk1:2", "blk2:1"])
        # No asset registered; the rows still show their Block/Part.
        self.assertEqual(self.scene.audio_assets, [])
        self.assertEqual(self.chip(0), "B1 · Intro · Part 1/2")
        self.assertEqual(self.chip(2), "B2 · Conclusion")

    def test_save_load_preserves_presentation(self):
        # Flow E: Scene dict + queue YAML round-trips keep the identity
        # presentation, the multi-Part relationship, the version state
        # and the duplicate semantics.
        self.start_long(self.PARTS())
        self.wait_batch()
        bm = self.win._batch_manager
        # Add a duplicate row (manual) — inserted right after its source.
        bm.duplicate_job(0)
        queue_path = bm.save_to_file(
            os.path.join(self.tmp, "queue.yaml"))
        scene_data = self.scene.to_dict()
        project_data = self.project.to_dict()

        # Reload the Scene through the model round trip.
        restored_scene = Scene.from_dict(scene_data)
        restored_project = Project.from_dict(project_data)
        # Reload the queue through the manager round trip.
        bm2 = BatchManager(submit_fn=lambda req: None)
        bm2.load_from_file(queue_path, replace=True)
        self.assertEqual([j.slot_id for j in bm2.jobs],
                         ["blk1:1", None, "blk1:2", "blk2:1"])
        # The multi-Part block still derives its relationship.
        info = slot_identity_summary(restored_scene, "blk1:2")
        self.assertEqual(info["part_of_block"], 2)
        self.assertEqual(info["parts_in_block"], 2)
        self.assertEqual(info["user_label"], "Intro")
        # Version state survived (v01 assets on every slot).
        for slot_id in ("blk1:1", "blk1:2", "blk2:1"):
            self.assertEqual(
                [v["part_version"]
                 for v in slot_versions(restored_scene, slot_id)], [1])
        # A re-presented dialog on the restored state shows the same chips.
        self.win._active_scene = restored_scene
        self.win._active_project = restored_project
        self.win._present_batch_dialog(
            scene=restored_scene, scene_id=restored_scene.id,
            review_mode=True)
        dlg = self.dialog()
        # Replace the queue with the restored jobs for presentation.
        for j in list(bm.jobs):
            bm.remove_job(0)
        for j in bm2.jobs:
            bm.add_job(j)
        dlg._refresh_table()
        _process(60)
        self.assertEqual(self.chip(0), "B1 · Intro · Part 1/2")
        self.assertEqual(self.chip(1), "Manual job")
        self.assertEqual(self.chip(2), "B1 · Intro · Part 2/2")
        self.assertEqual(self.chip(3), "B2 · Conclusion")

    def test_redetect_replaces_ids_and_no_false_mapping(self):
        # Flow I: re-detection behaviour is UNCHANGED (ids replaced by
        # design); after a re-materialisation the old job shows the
        # honest "Unlinked part" state — never a fabricated mapping —
        # and the new structure carries only the new ids.
        bm = self.win._batch_manager
        # Establish blocks + a structure event.
        self.start_long(self.PARTS(), review=True)
        old_slots = [s["slot_id"]
                     for s in self.scene.expected_audio_slots]
        self.assertEqual(old_slots, ["blk1:1", "blk1:2", "blk2:1"])
        # Re-detect (engine path — the editor dispatches here): block
        # ids are replaced wholesale BY DESIGN (P3.26/P3.44.7 semantics).
        editor_bm = self.win._editor.block_manager
        editor_bm.rebuild_everything("Fresh text for re detection.")
        new_ids = [b.id for b in editor_bm.blocks]
        self.assertTrue(new_ids)
        self.assertNotIn("blk1", new_ids)
        self.assertNotIn("blk2", new_ids)
        # The slots keep the OLD structure until the next structure
        # event (documented residual — NOT reconciled here).
        self.assertEqual([s["slot_id"]
                          for s in self.scene.expected_audio_slots],
                         old_slots)
        # The next Generate Long re-materialises with the NEW block ids:
        # the OLD queue rows (e.g. a restored old queue) must never be
        # re-mapped to the new blocks positionally.
        new_parts = [
            _make_part("New first.", block_id=new_ids[0],
                       part_of_block=1, total_in_block=2),
            _make_part("New second.", block_id=new_ids[0],
                       part_of_block=2, total_in_block=2),
        ]
        self.start_long(new_parts)
        self.wait_batch()
        new_slots = [s["slot_id"]
                     for s in self.scene.expected_audio_slots]
        self.assertEqual(new_slots,
                         ["{0}:1".format(new_ids[0]),
                          "{0}:2".format(new_ids[0])])
        # No stale block id remains visible in the structure.
        for slot in self.scene.expected_audio_slots:
            self.assertIn(slot["block_id"], new_ids)
        # A restored OLD queue job presents as Unlinked — honest.
        old_job = BatchJob(name="Old part", prompt="Old.",
                           slot_id="blk1:1", part_index=1,
                           part_version=1, scene_id=self.scene.id)
        bm.add_job(old_job)
        self.dialog()._refresh_table()
        _process(40)
        self.assertEqual(self.chip(2), "Unlinked part")
        self.assertEqual(
            slot_identity_summary(self.scene, "blk1:1"), None)


if __name__ == "__main__":
    unittest.main()
