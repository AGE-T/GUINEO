# -*- coding: utf-8 -*-
"""P3.45.6 addendum — Batch Queue Removal vs Scene Combine Integrity.

Deterministic regressions for the reported state:

    Batch Generation UI: 15 visible rows, coverage PARTIAL (15/22),
    Combine Scene rejects — "Block 16 through Block 22 have no generated
    audio".

Proven root cause (design record
docs/design/P3_45_6_BATCH_QUEUE_REMOVAL_COMBINE_INTEGRITY.md §2): the
queue (BatchManager._jobs) and the Scene's expected-slot structure are
decoupled structures that only change together at the Generate Long
structure event. Remove deletes the generation JOB only (queue
workspace) while the recorded structure still requires the audio —
correct per the P3.44.6/P3.45.3 contract, but nothing surfaced the gap,
nothing could repair the queue without wiping it, and Combine's remedy
message could not distinguish genuinely missing audio from stale
structure references (deleted blocks).

This suite pins:
  * the Remove contract (job-only; slots/blocks/assets untouched);
  * missing_expected_parts — the exact slots with neither audio nor a
    queue row (the coverage gap the header now surfaces);
  * resolved_slot_sources classification — legitimate missing audio vs
    stale structure reference (read-only, back-compat default);
  * Re-add Missing — the same range gate + the same splitter + the same
    job builder as Generate Long, matched by EXACT slot identity, never
    associating a part with a slot it does not match (scenario D);
  * scenarios A–E of the task, including the full screenshot-state
    golden path (real MainWindow, real editor, real batch generation
    with the faked model boundary, real Combine outputs).
"""

import os
import sys
import json
import time
import types
import shutil
import tempfile
import unittest

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

SAMPLE_RATE = 24000


def make_speech(seconds: float, freq: float = 220.0) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class FakeHiggsModel:
    """Model-boundary fake (P3.27B house pattern)."""

    calls = []
    speech_s = 0.5

    def generate_speech(self, text, tokenizer, *, reference_audio=None,
                        reference_sample_rate=None, reference_codes=None,
                        reference_text=None, max_new_tokens=2048,
                        temperature=1.0, top_p=None, top_k=None):
        FakeHiggsModel.calls.append(dict(text=text))
        return make_speech(FakeHiggsModel.speech_s)


def _process(ms=30):
    from PySide6.QtWidgets import QApplication
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _check_dict(dlg, flags):
    """P3.45.4 (documented update): _check_states is JOB-KEYED (slot id
    for structural jobs, object identity for manual rows)."""
    return {dlg._check_key(job): bool(v)
            for job, v in zip(dlg._manager.jobs, flags)}


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


STALE_MARKER = "no longer exists in the current text"
LEGACY_MISSING = "has no generated audio yet"


# ===========================================================================
# 1) PURE ENGINE — missing_expected_parts
# ===========================================================================
class _PureFactory(unittest.TestCase):
    """Shared scene/slot/asset fixtures (no Qt)."""

    SCENE_ID = "ab12cd34ef56"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p346brq_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_scene(self, slots=None, assets=None):
        from engine.models import Scene
        s = Scene(id=self.SCENE_ID, name="Scene 03")
        if slots is not None:
            s.expected_audio_slots = list(slots)
        if assets is not None:
            s.audio_assets = list(assets)
        return s

    def slots_for(self, n):
        """n block slots 'blkNN:1' with labels 'Block N' (the exact
        recorded shape Generate Long materialises)."""
        from engine.audio_provenance import materialize_expected_slots
        return materialize_expected_slots([
            _make_part(block_id="blk{0:02d}".format(i),
                       block_label="Block {0}".format(i))
            for i in range(1, n + 1)])

    def wav(self, name):
        import soundfile as sf
        path = os.path.join(self.tmp, "outputs", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sf.write(path, make_speech(0.5), SAMPLE_RATE)
        return os.path.relpath(path, self.tmp)

    def asset(self, slot_id, part_index, version=1, block_id=None,
              file=None):
        return {
            "id": "a{0}v{1}".format(part_index, version),
            "scene_id": self.SCENE_ID,
            "output_path": file or self.wav(
                "p{0:03d}_v{1:02d}.wav".format(part_index, version)),
            "duration": 10.0,
            "speaker": None, "character_id": None,
            "voice_profile_id": None,
            "generated_at": "20260101_120000",
            "block_id": block_id,
            "part_index": part_index,
            "part_version": version,
            "generation_run": "{0}-r001".format(self.SCENE_ID[:8]),
            "slot_id": slot_id,
        }


class TestMissingExpectedParts(_PureFactory):
    """The exact slots with neither audio nor a queue row."""

    def test_definition_uncovered_and_rowless_only(self):
        from engine.audio_provenance import missing_expected_parts
        scene = self.make_scene(slots=self.slots_for(22))
        # 15 covered (assets), 15 with rows → the 7 of Blocks 16..22 are
        # exactly the reported missing set, in structure order.
        covered = [self.asset("blk{0:02d}:1".format(i), i)
                   for i in range(1, 16)]
        scene.audio_assets = covered
        job_ids = {"blk{0:02d}:1".format(i) for i in range(1, 16)}
        missing = missing_expected_parts(scene, job_ids)
        self.assertEqual([s["slot_id"] for s in missing],
                         ["blk{0:02d}:1".format(i) for i in range(16, 23)])
        self.assertEqual([s["block_label"] for s in missing],
                         ["Block {0}".format(i) for i in range(16, 23)])

    def test_covered_slot_without_row_is_not_missing(self):
        from engine.audio_provenance import missing_expected_parts
        scene = self.make_scene(slots=self.slots_for(3))
        scene.audio_assets = [self.asset("blk03:1", 3)]
        # No rows at all — but slot 3 has audio → only 1-2 are missing.
        missing = missing_expected_parts(scene, set())
        self.assertEqual([s["slot_id"] for s in missing],
                         ["blk01:1", "blk02:1"])

    def test_uncovered_slot_with_row_is_not_missing(self):
        from engine.audio_provenance import missing_expected_parts
        scene = self.make_scene(slots=self.slots_for(3))
        missing = missing_expected_parts(scene, {"blk01:1", "blk02:1"})
        self.assertEqual([s["slot_id"] for s in missing], ["blk03:1"])

    def test_legacy_scene_without_slots_returns_empty(self):
        from engine.audio_provenance import missing_expected_parts
        scene = self.make_scene()
        scene.audio_assets = [self.asset("x", 1)]
        self.assertEqual(missing_expected_parts(scene, set()), [])

    def test_none_and_nonstring_job_ids_ignored(self):
        from engine.audio_provenance import missing_expected_parts
        scene = self.make_scene(slots=self.slots_for(2))
        missing = missing_expected_parts(scene, [None, 42, "", "blk01:1"])
        self.assertEqual([s["slot_id"] for s in missing], ["blk02:1"])


# ===========================================================================
# 2) PURE ENGINE — resolved_slot_sources classification (real wav files)
# ===========================================================================
class TestResolvedSlotSourcesClassification(_PureFactory):
    """Legitimate missing audio vs stale structure reference."""

    def _scene_covered_1_2(self):
        scene = self.make_scene(slots=self.slots_for(3))
        scene.audio_assets = [
            self.asset("blk01:1", 1, block_id="blk01"),
            self.asset("blk02:1", 2, block_id="blk02"),
        ]
        return scene

    def test_legitimate_missing_message_unchanged(self):
        from engine.audio_provenance import resolved_slot_sources
        scene = self._scene_covered_1_2()
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_block_ids={"blk01", "blk02", "blk03"})
        self.assertEqual(len(sources), 2)
        self.assertEqual(blockers, ["'Block 3' " + LEGACY_MISSING])

    def test_stale_reference_message_with_remedy(self):
        from engine.audio_provenance import resolved_slot_sources
        scene = self._scene_covered_1_2()
        # Block 3 deleted from the current text (ids 1-2 survive — the
        # P3.44.9 deletion contract).
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_block_ids={"blk01", "blk02"})
        self.assertEqual(len(sources), 2)
        self.assertEqual(len(blockers), 1)
        self.assertIn("'Block 3'", blockers[0])
        self.assertIn(STALE_MARKER, blockers[0])
        self.assertIn("Generate Long", blockers[0])
        self.assertNotIn(LEGACY_MISSING, blockers[0])

    def test_default_none_is_byte_identical_legacy(self):
        from engine.audio_provenance import resolved_slot_sources
        scene = self._scene_covered_1_2()
        # Back-compat: the default call (every existing caller/test)
        # keeps the exact legacy message even for a deleted block.
        sources, blockers = resolved_slot_sources(scene, self.tmp)
        self.assertEqual(blockers, ["'Block 3' " + LEGACY_MISSING])

    def test_covered_slot_with_replaced_ids_blocked_without_content_evidence(self):
        """P3.45.8 CONTRACT CHANGE (supersedes the P3.45.6-ADD
        "covered slots are never classified" rule — owner-mandated
        narration-integrity closure): with ONLY structure evidence (an
        id set, no current decomposition), a covered slot whose Block
        provably no longer exists is BLOCKED — an existing audio file
        alone is no longer proof that the audio belongs to the current
        narration (the post-Re-detect orphaned-audio class)."""
        from engine.audio_provenance import resolved_slot_sources
        scene = self.make_scene(slots=self.slots_for(3))
        scene.audio_assets = [
            self.asset("blk01:1", 1, block_id="blk01"),
            self.asset("blk02:1", 2, block_id="blk02"),
            self.asset("blk03:1", 3, block_id="blk03"),
        ]
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_block_ids={"new01", "new02", "new03"})
        self.assertEqual(sources, [])
        self.assertEqual(len(blockers), 3)
        self.assertTrue(all(
            "still has audio, but its Block no longer exists" in b
            for b in blockers), blockers)
        self.assertTrue(all("Generate Long" in b for b in blockers))

    def test_plain_slot_unclassifiable(self):
        from engine.audio_provenance import (
            resolved_slot_sources, materialize_expected_slots)
        scene = self.make_scene(
            slots=materialize_expected_slots([
                _make_part(), _make_part()]))
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_block_ids=set())
        self.assertEqual(sources, [])
        self.assertEqual(blockers,
                         ["'Part 1' " + LEGACY_MISSING,
                          "'Part 2' " + LEGACY_MISSING])

    def test_empty_id_set_means_empty_structure(self):
        from engine.audio_provenance import resolved_slot_sources
        # All blocks deleted AND nothing generated yet: an empty set is
        # POSITIVE evidence of an empty structure — every block slot
        # classifies as a stale reference (never "missing audio").
        scene = self.make_scene(slots=self.slots_for(3))
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_block_ids=set())
        self.assertEqual(sources, [])
        self.assertEqual(len(blockers), 3)
        self.assertTrue(all(STALE_MARKER in b for b in blockers))

    def test_sources_order_and_fields_unchanged_by_ids(self):
        from engine.audio_provenance import resolved_slot_sources
        scene = self._scene_covered_1_2()
        with_ids, _ = resolved_slot_sources(
            scene, self.tmp, current_block_ids={"blk01", "blk02"})
        without_ids, _ = resolved_slot_sources(scene, self.tmp)
        self.assertEqual(with_ids, without_ids,
                         "classification must not change resolved sources")


# ===========================================================================
# 3) REAL MAINWINDOW HARNESS
# ===========================================================================
class _MWHarness(unittest.TestCase):
    """Real MainWindow + Engine with the model boundary faked (the
    test_p3_28 house pattern)."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p346mw_")
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
        # Isolate persistence into the temp root (house pattern; the
        # recents builder also needs list_projects — provide it).
        self.win._project_manager = type(
            "PM", (), {"__init__": lambda self: None,
                       "save_project": lambda self, p: None,
                       "list_projects": lambda self: []})()
        from engine.models import Project, Scene
        self.project = Project(name="Eden", id="proj1")
        self.scene = Scene(id="sc1abcd", name="Scene 03",
                           project_id="proj1")
        self.project.add_scene(self.scene)
        self.win._active_project = self.project
        self.win._active_scene = self.scene
        self.win._current_project = "Eden"
        FakeHiggsModel.calls = []
        FakeHiggsModel.speech_s = 0.5

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
        _process(50)

    # -- fixtures ----------------------------------------------------------

    def params(self):
        from engine.models import GenerationParameters
        return GenerationParameters(
            temperature=0.95, top_p=0.95, top_k=300, max_new_tokens=4096,
            seed=None, append_silence=0.5, normalize_output=False,
            auto_play=False)

    def make_blocks_and_text(self, n, ids=None):
        """n one-paragraph blocks over '\n'-joined text (each paragraph
        < MAX_CHARS → exactly one part per block)."""
        from engine.narration_blocks import PromptBlock
        paras = ["Narration block {0} carries its own deterministic "
                 "sentence for the splitter.".format(i)
                 for i in range(1, n + 1)]
        text = "\n".join(paras)
        blocks = []
        pos = 0
        for i, para in enumerate(paras):
            blocks.append(PromptBlock(
                id=(ids[i] if ids else "blk{0:02d}".format(i + 1)),
                start_offset=pos, end_offset=pos + len(para)))
            pos += len(para) + 1
        return text, blocks, paras

    def parts_for_blocks(self, blocks, paras, labels=None):
        return [_make_part(text=para, block_id=b.id,
                           block_label=(labels[i] if labels
                                        else "Block {0}".format(i + 1)))
                for i, (b, para) in enumerate(zip(blocks, paras))]

    def slots_for_blocks(self, blocks, labels=None, paras=None):
        """Materialise slots for the blocks. When ``paras`` is given the
        parts carry the REAL block texts (so the stamped ``content_sha``
        matches the current decomposition — the proven-identity path);
        without it the parts keep the helper's default text (slots whose
        fingerprints deliberately do NOT match the editor text — the
        changed-content class)."""
        from engine.audio_provenance import materialize_expected_slots
        return materialize_expected_slots([
            _make_part(text=(paras[i] if paras else
                             "A test sentence for the part."),
                       block_id=b.id,
                       block_label=(labels[i] if labels
                                    else "Block {0}".format(i + 1)))
            for i, b in enumerate(blocks)])

    def direct_asset(self, slot_id, part_index, version=1, content_sha=None):
        import soundfile as sf
        path = os.path.join(self.tmp, "outputs",
                            "direct_p{0:03d}_v{1:02d}.wav".format(
                                part_index, version))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sf.write(path, make_speech(0.5), SAMPLE_RATE)
        asset = {
            "id": "direct-{0}".format(slot_id),
            "scene_id": self.scene.id,
            "output_path": os.path.relpath(path, self.tmp),
            "duration": 10.0,
            "speaker": None, "character_id": None,
            "voice_profile_id": None,
            "generated_at": "20260101_120000",
            "block_id": slot_id.split(":")[0],
            "part_index": part_index,
            "part_version": version,
            "generation_run": "{0}-r001".format(self.scene.id[:8]),
            "slot_id": slot_id,
        }
        if content_sha is not None:
            # P3.45.8: proven-identity asset (as if generated on the
            # current content by a P3.45.8+ build).
            asset["content_sha"] = content_sha
        return asset

    def load_editor(self, text, blocks):
        """Production-faithful editor restore (the _load_scene_state
        path — scene.text + scene.narration_blocks)."""
        from engine.narration_blocks import PromptBlock as _PB
        self.scene.text = text
        self.scene.narration_blocks = [b.to_dict() for b in blocks]
        self.win._load_scene_state(self.scene)
        self.win._active_scene = self.scene

    def start_long(self, parts, review=False):
        self.win._start_long_narration(parts, None, self.params(), {},
                                       review_mode=review)

    def wait_batch(self, timeout=60):
        bm = self.win._batch_manager
        t0 = time.time()
        while bm.is_running and time.time() - t0 < timeout:
            _process(20)
        _process(200)

    def dialog(self):
        return self.win._batch_dialog

    def combine_warning_for(self, scene):
        """Run Combine for the GIVEN scene (the real _combine_scene
        return path) capturing the warning message text (if any)."""
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "warning", return_value=None) as warn:
            ok = self.win._combine_scene(scene)
        msg = warn.call_args[0][2] if warn.called else ""
        return ok, msg

    def combine_warning(self):
        """Run Combine (the real _combine_scene return path) capturing
        the warning message text (if any)."""
        return self.combine_warning_for(self.scene)


# ===========================================================================
# 4) Combine classification wiring (real MainWindow)
# ===========================================================================
class TestCombineClassificationWiring(_MWHarness):

    def test_active_scene_live_blocks_classify_stale(self):
        from engine.audio_provenance import part_content_sha
        text, blocks, paras = self.make_blocks_and_text(3)
        self.scene.expected_audio_slots = self.slots_for_blocks(
            blocks, paras=paras)
        self.scene.audio_assets = [
            self.direct_asset("blk01:1", 1,
                              content_sha=part_content_sha(paras[0])),
            self.direct_asset("blk02:1", 2,
                              content_sha=part_content_sha(paras[1])),
        ]
        self.load_editor(text, blocks)
        # REAL deletion of Block 3's text through the editor (the
        # P3.44.9 transformer removes the block, ids of 1-2 survive).
        from PySide6.QtGui import QTextCursor
        ed = self.win._editor._editor
        cur = ed.textCursor()
        cur.setPosition(blocks[2].start_offset)
        cur.setPosition(blocks[2].end_offset,
                        QTextCursor.MoveMode.KeepAnchor)
        ed.setTextCursor(cur)
        cur.removeSelectedText()
        _process(30)
        live_ids = {b.id for b in self.win._editor.block_manager.blocks}
        self.assertEqual(live_ids, {"blk01", "blk02"})
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        self.assertIn("'Block 3'", msg)
        self.assertIn(STALE_MARKER, msg)
        self.assertIn("Generate Long", msg)
        self.assertNotIn(LEGACY_MISSING, msg)
        # The recorded structure + assets are never mutated by Combine.
        self.assertEqual(len(self.scene.expected_audio_slots), 3)
        self.assertEqual(len(self.scene.audio_assets), 2)

    def test_non_active_scene_uses_persisted_snapshot(self):
        from engine.models import Scene
        # Target scene is NOT the active one (the by-id combine path).
        text, blocks, _ = self.make_blocks_and_text(3)
        other = Scene(id="sc2efgh", name="Scene 04", project_id="proj1")
        other.text = "\n".join(
            p for p in text.split("\n")[:2])
        other.narration_blocks = [b.to_dict() for b in blocks[:2]]
        other.expected_audio_slots = self.slots_for_blocks(blocks)
        other.audio_assets = [
            self.direct_asset("blk01:1", 1),
            self.direct_asset("blk02:1", 2),
        ]
        self.project.add_scene(other)
        # The ACTIVE scene is a different one with its own editor state.
        atext, ablocks, _ = self.make_blocks_and_text(1, ids=["act01"])
        self.load_editor(atext, ablocks)
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "warning", return_value=None) as warn:
            ok = self.win._on_combine_scene_by_id(other.id)
        msg = warn.call_args[0][2] if warn.called else ""
        self.assertFalse(ok)
        self.assertIn("'Block 3'", msg)
        self.assertIn(STALE_MARKER, msg)
        # Empty persisted snapshot → no evidence → legacy message only.
        other2 = Scene(id="sc3ijkl", name="Scene 05", project_id="proj1")
        other2.text = "text"
        other2.expected_audio_slots = self.slots_for_blocks(blocks)
        other2.audio_assets = [
            self.direct_asset("blk01:1", 1),
            self.direct_asset("blk02:1", 2),
        ]
        self.project.add_scene(other2)
        with patch.object(QMessageBox, "warning", return_value=None) as warn:
            ok = self.win._on_combine_scene_by_id(other2.id)
        msg = warn.call_args[0][2] if warn.called else ""
        self.assertFalse(ok)
        self.assertIn("'Block 3'", msg)
        self.assertIn(LEGACY_MISSING, msg)
        self.assertNotIn(STALE_MARKER, msg)

    def test_redetect_full_coverage_combines_when_content_proven(self):
        """P3.45.8 (D4 — uniquely provable block-ID replacement): after
        Re-detect every block id is replaced while the text is
        UNCHANGED; when both the slots and the assets carry matching
        content fingerprints, the reconciliation path proves the audio
        IS the current content and Combine still succeeds (the honest
        successor of the pre-P3.45.8 "covered slots never classified"
        behaviour — now EARNED by proof, not assumed)."""
        from engine.narration_blocks import PromptBlock
        from engine.audio_provenance import part_content_sha
        text, blocks, paras = self.make_blocks_and_text(3)
        self.scene.expected_audio_slots = self.slots_for_blocks(
            blocks, paras=paras)
        self.scene.audio_assets = [
            self.direct_asset("blk01:1", 1,
                              content_sha=part_content_sha(paras[0])),
            self.direct_asset("blk02:1", 2,
                              content_sha=part_content_sha(paras[1])),
            self.direct_asset("blk03:1", 3,
                              content_sha=part_content_sha(paras[2])),
        ]
        self.load_editor(text, blocks)
        # Simulate post-Re-detect: every block id replaced, same text.
        new_blocks = [
            PromptBlock(id="new{0:02d}".format(i + 1),
                        start_offset=b.start_offset,
                        end_offset=b.end_offset)
            for i, b in enumerate(blocks)]
        self.win._editor.begin_scene_load()
        try:
            self.win._editor.set_scene_blocks(new_blocks, text)
        finally:
            self.win._editor.end_scene_load()
        ok, msg = self.combine_warning()
        self.assertTrue(ok, "full coverage with PROVEN content must "
                            "combine after re-detect: " + msg)
        self.assertEqual(len(self.scene.combined_outputs), 1)
        self.assertEqual(self.scene.combined_outputs[0]["slot_count"], 3)

    def test_redetect_full_coverage_blocked_for_unproven_legacy_audio(self):
        """P3.45.8 fail-closed companion: the same re-detect state but
        with LEGACY assets (no content fingerprint) is BLOCKED — the
        audio cannot be proven to belong to the current narration — and
        NOTHING is mutated by the failed combine."""
        from engine.narration_blocks import PromptBlock
        text, blocks, paras = self.make_blocks_and_text(3)
        self.scene.expected_audio_slots = self.slots_for_blocks(
            blocks, paras=paras)
        self.scene.audio_assets = [
            self.direct_asset("blk01:1", 1),
            self.direct_asset("blk02:1", 2),
            self.direct_asset("blk03:1", 3),
        ]
        self.load_editor(text, blocks)
        new_blocks = [
            PromptBlock(id="new{0:02d}".format(i + 1),
                        start_offset=b.start_offset,
                        end_offset=b.end_offset)
            for i, b in enumerate(blocks)]
        self.win._editor.begin_scene_load()
        try:
            self.win._editor.set_scene_blocks(new_blocks, text)
        finally:
            self.win._editor.end_scene_load()
        slots_before = list(self.scene.expected_audio_slots)
        assets_before = list(self.scene.audio_assets)
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        self.assertEqual(len(msg.split("•")) - 1, 3,
                         "every unproven covered slot is named: " + msg)
        self.assertIn("cannot be proven", msg)
        self.assertIn("Generate Long", msg)
        # Fail-closed leaves everything untouched.
        self.assertEqual(self.scene.expected_audio_slots, slots_before)
        self.assertEqual(self.scene.audio_assets, assets_before)
        self.assertEqual(len(self.scene.combined_outputs), 0)


# ===========================================================================
# 5) Remove semantics (the dialog-level contract, scenario A)
# ===========================================================================
class TestRemoveSemantics(_MWHarness):

    def _scene_dialog(self, job_slots=("blk01:1",), asset_slot=None,
                      manual=False):
        """Scene-mode dialog over a 2-block scene; jobs created for the
        given slots (full provenance like a Generate Long queue row)."""
        text, blocks, paras = self.make_blocks_and_text(2)
        self.scene.expected_audio_slots = self.slots_for_blocks(blocks)
        if asset_slot:
            self.scene.audio_assets = [self.direct_asset(asset_slot, 1)]
        self.load_editor(text, blocks)
        from engine.batch_manager import BatchJob
        self.win._batch_manager.clear_all()
        for i, sid in enumerate(job_slots):
            self.win._batch_manager.add_job(BatchJob(
                name="Part {0}".format(i + 1), prompt="x",
                part_index=i + 1, part_version=1, slot_id=sid,
                source_block_id=sid.split(":")[0],
                scene_id=self.scene.id, scene_name=self.scene.name))
        if manual:
            self.win._batch_manager.add_job(
                BatchJob(name="Manual", prompt="m"))
        dlg = self.win._present_batch_dialog(scene=self.scene,
                                              scene_id=self.scene.id)
        dlg._refresh_table()
        return dlg

    def test_remove_uncovered_structural_confirms_then_updates_header(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        # Both slots have rows; blk02 is covered by an asset, blk01 is
        # not — removing row 0 removes the last regeneration path for
        # an UNCOVERED expected slot → the contract must be stated.
        dlg = self._scene_dialog(job_slots=("blk01:1", "blk02:1"),
                                  asset_slot="blk02:1")
        slots_before = list(self.scene.expected_audio_slots)
        dlg._table.selectRow(0)
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes) as q:
            dlg._on_remove()
        self.assertTrue(q.called,
                        "uncovered structural removal must state the "
                        "job-only contract before acting")
        self.assertEqual(len(dlg._manager.jobs), 1)
        # The contract: job ONLY — structure/assets untouched.
        self.assertEqual(self.scene.expected_audio_slots, slots_before)
        self.assertEqual(len(self.scene.audio_assets), 1)
        header = dlg._coverage_label.text()
        self.assertIn("1 expected part(s) missing from the queue", header)
        self.assertIn("PARTIAL (1/2)", header)
        self.assertTrue(dlg._readd_btn.isEnabled())
        self.assertEqual(dlg._readd_btn.text(), "Re-add Missing (1)")

    def test_remove_confirmation_declined_keeps_job(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        dlg = self._scene_dialog(job_slots=("blk01:1", "blk02:1"),
                                  asset_slot="blk02:1")
        dlg._table.selectRow(0)
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.No):
            dlg._on_remove()
        self.assertEqual(len(dlg._manager.jobs), 2)
        self.assertNotIn("missing from the queue",
                         dlg._coverage_label.text())

    def test_remove_covered_structural_is_silent(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        # blk01 is covered by an asset — its row's removal leaves audio
        # (no Combine consequence) and blk02 keeps its row: silent.
        dlg = self._scene_dialog(job_slots=("blk01:1", "blk02:1"),
                                  asset_slot="blk01:1")
        dlg._table.selectRow(0)
        with patch.object(QMessageBox, "question",
                          return_value=None) as q:
            dlg._on_remove()
        self.assertFalse(q.called, "covered removal has no Combine "
                                   "consequence — stays silent")
        self.assertEqual(len(dlg._manager.jobs), 1)
        # Audio exists → not missing even without the row.
        self.assertNotIn("missing from the queue",
                         dlg._coverage_label.text())
        self.assertFalse(dlg._readd_btn.isEnabled())

    def test_remove_manual_job_is_silent(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        dlg = self._scene_dialog(job_slots=("blk01:1", "blk02:1"),
                                  manual=True)
        dlg._table.selectRow(2)
        with patch.object(QMessageBox, "question",
                          return_value=None) as q:
            dlg._on_remove()
        self.assertFalse(q.called)
        self.assertEqual(len(dlg._manager.jobs), 2)

    def test_on_readded_jobs_checks_only_new_jobs(self):
        from engine.batch_manager import BatchJob
        dlg = self._scene_dialog(job_slots=("blk01:1",))
        # User unchecked the existing row; a re-added job (added by the
        # MainWindow handler first, then registered) must not reset that
        # choice (P3.45.4 job-keyed states — only the NEW key is set).
        dlg._check_states = _check_dict(dlg, [False])
        new = BatchJob(name="Part 2", prompt="y", part_index=2,
                       slot_id="blk02:1", source_block_id="blk02")
        dlg._manager.add_job(new)
        dlg.on_readded_jobs([new])
        self.assertEqual(dlg._checked_indices(), [1])
        self.assertEqual(len(dlg._manager.jobs), 2)


# ===========================================================================
# 6) Re-add Missing guards (real MainWindow)
# ===========================================================================
class TestReaddMissing(_MWHarness):

    def test_running_guard_blocks_readd(self):
        from unittest.mock import patch, PropertyMock
        from PySide6.QtWidgets import QMessageBox
        text, blocks, paras = self.make_blocks_and_text(2)
        self.scene.expected_audio_slots = self.slots_for_blocks(blocks)
        self.load_editor(text, blocks)
        bm = self.win._batch_manager
        with patch.object(type(bm), "is_running",
                          new_callable=PropertyMock, return_value=True):
            with patch.object(QMessageBox, "information",
                              return_value=None) as info:
                self.win._on_readd_missing_parts()
        self.assertTrue(info.called)
        self.assertEqual(len(bm.jobs), 0)

    def test_range_gate_blocks_readd(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        from engine.narration_blocks import PromptBlock
        text, blocks, paras = self.make_blocks_and_text(2)
        # Corrupt the second block's range (out of bounds) — the same
        # fail-closed precondition as Generate Long (P3.45.6 Phase 2).
        blocks[1].end_offset = len(text) + 50
        self.scene.expected_audio_slots = self.slots_for_blocks(blocks)
        self.load_editor(text, blocks)
        with patch.object(QMessageBox, "warning", return_value=None) as warn:
            self.win._on_readd_missing_parts()
        self.assertTrue(warn.called)
        self.assertIn("Re-add stopped", warn.call_args[0][2])
        self.assertIn("range", warn.call_args[0][2])
        self.assertEqual(len(self.win._batch_manager.jobs), 0,
                         "no job may be created from invalid ranges")

    def test_plain_slots_reported_not_forced(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        from engine.audio_provenance import materialize_expected_slots
        # A plain-text scene (no blocks): slots are positional — they can
        # never be PROVEN to be the same text, so re-add must refuse.
        text = "First plain paragraph.\nSecond plain paragraph."
        self.scene.text = text
        self.scene.narration_blocks = []
        self.win._load_scene_state(self.scene)
        self.scene.expected_audio_slots = materialize_expected_slots(
            [_make_part(), _make_part()])
        with patch.object(QMessageBox, "warning", return_value=None) as warn:
            self.win._on_readd_missing_parts()
        self.assertTrue(warn.called)
        self.assertIn("could NOT be re-added", warn.call_args[0][2])
        self.assertIn("Generate Long", warn.call_args[0][2])
        self.assertEqual(len(self.win._batch_manager.jobs), 0,
                         "positional identity must never be forced into a "
                         "job (scenario D protection)")


# ===========================================================================
# 7) Scenario B — block deleted from the text
# ===========================================================================
class TestScenarioBStaleAfterDeletion(_MWHarness):

    def test_deleted_block_classified_stale_and_readd_refuses(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtGui import QTextCursor
        text, blocks, paras = self.make_blocks_and_text(3)
        self.scene.expected_audio_slots = self.slots_for_blocks(blocks)
        self.scene.audio_assets = [
            self.direct_asset("blk01:1", 1),
            self.direct_asset("blk02:1", 2),
        ]
        self.load_editor(text, blocks)
        # REAL deletion of Block 3 through the editor.
        ed = self.win._editor._editor
        cur = ed.textCursor()
        cur.setPosition(blocks[2].start_offset)
        cur.setPosition(blocks[2].end_offset,
                        QTextCursor.MoveMode.KeepAnchor)
        ed.setTextCursor(cur)
        cur.removeSelectedText()
        _process(30)
        slots_before = list(self.scene.expected_audio_slots)
        # Combine distinguishes the stale reference from missing audio.
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        self.assertIn("'Block 3'", msg)
        self.assertIn(STALE_MARKER, msg)
        self.assertNotIn(LEGACY_MISSING, msg)
        # Re-add refuses the deleted block's slot (honest report).
        with patch.object(QMessageBox, "warning", return_value=None) as warn:
            self.win._on_readd_missing_parts()
        self.assertTrue(warn.called)
        self.assertIn("could NOT be re-added", warn.call_args[0][2])
        self.assertIn("Block 3", warn.call_args[0][2])
        self.assertEqual(len(self.win._batch_manager.jobs), 0)
        # The documented snapshot model is respected — no mutation.
        self.assertEqual(self.scene.expected_audio_slots, slots_before)
        self.assertEqual(len(self.scene.audio_assets), 2)


# ===========================================================================
# 8) Scenario C — delete, regenerate the queue, combine
# ===========================================================================
class TestScenarioCRegenerateAndCombine(_MWHarness):

    def test_after_generate_long_missing_list_matches_current_structure(self):
        from PySide6.QtGui import QTextCursor
        from engine.audio_provenance import coverage_counts, part_content_sha
        text, blocks, paras = self.make_blocks_and_text(3)
        self.scene.expected_audio_slots = self.slots_for_blocks(
            blocks, paras=paras)
        self.scene.audio_assets = [
            self.direct_asset("blk01:1", 1,
                              content_sha=part_content_sha(paras[0])),
            self.direct_asset("blk02:1", 2,
                              content_sha=part_content_sha(paras[1])),
        ]
        self.load_editor(text, blocks)
        # Delete Block 3 (real editor deletion).
        ed = self.win._editor._editor
        cur = ed.textCursor()
        cur.setPosition(blocks[2].start_offset)
        cur.setPosition(blocks[2].end_offset,
                        QTextCursor.MoveMode.KeepAnchor)
        ed.setTextCursor(cur)
        cur.removeSelectedText()
        _process(30)
        surviving = self.win._editor.block_manager.blocks
        self.assertEqual([b.id for b in surviving], ["blk01", "blk02"])
        # Regenerate the Batch queue = the Generate Long structure event
        # over the CURRENT text (review mode: jobs queue without running).
        self.start_long(
            self.parts_for_blocks(surviving, paras[:2]), review=True)
        self.assertEqual(
            [s["slot_id"] for s in self.scene.expected_audio_slots],
            ["blk01:1", "blk02:1"],
            "the recorded structure must match the current text")
        # The surviving ids' audio STILL joins — nothing was lost.
        self.assertEqual(coverage_counts(self.scene), (2, 2))
        dlg = self.dialog()
        self.assertEqual(len(dlg._manager.jobs), 2)
        self.assertNotIn("missing from the queue",
                         dlg._coverage_label.text())
        # Combine now succeeds with exactly the current structure.
        ok, msg = self.combine_warning()
        self.assertTrue(ok, msg)
        entry = self.scene.combined_outputs[0]
        self.assertEqual(entry["slot_count"], 2)
        self.assertEqual([s["slot_id"] for s in entry["sources"]],
                         ["blk01:1", "blk02:1"])

    def test_multi_block_deletion_then_regenerate_combine_uses_current_structure(self):
        """P3.45.6 closeout — user scenario C: MULTIPLE blocks deleted in
        ONE editor selection, then the Batch queue is regenerated.

        The queue must contain generation items for ONLY the current
        blocks, and the Combine validation must use exactly the same
        authoritative Scene structure the regeneration was built from:
        the deleted blocks are never demanded again, while their
        historical audio stays preserved (append-only lineage)."""
        from PySide6.QtGui import QTextCursor
        from engine.audio_provenance import (
            coverage_counts, part_content_sha)
        text, blocks, paras = self.make_blocks_and_text(4)
        self.scene.expected_audio_slots = self.slots_for_blocks(
            blocks, paras=paras)
        self.scene.audio_assets = [
            self.direct_asset("blk01:1", 1,
                              content_sha=part_content_sha(paras[0])),
            self.direct_asset("blk02:1", 2,
                              content_sha=part_content_sha(paras[1])),
            self.direct_asset("blk03:1", 3,
                              content_sha=part_content_sha(paras[2])),
        ]
        self.load_editor(text, blocks)
        self.assertEqual(coverage_counts(self.scene), (3, 4))
        # ONE selection spanning Blocks 2 AND 3 — a single multi-block
        # deletion through the P3.44.9 offset transformer.
        ed = self.win._editor._editor
        cur = ed.textCursor()
        cur.setPosition(blocks[1].start_offset)
        cur.setPosition(blocks[2].end_offset,
                        QTextCursor.MoveMode.KeepAnchor)
        ed.setTextCursor(cur)
        cur.removeSelectedText()
        _process(30)
        surviving = self.win._editor.block_manager.blocks
        self.assertEqual([b.id for b in surviving], ["blk01", "blk04"])
        # Regenerate the Batch queue over the CURRENT text (review mode:
        # jobs queue up without running). Explicit labels keep the
        # deleted blocks' labels ("Block 2"/"Block 3") reserved — they
        # must never appear in any later Combine message.
        self.start_long(
            self.parts_for_blocks(surviving, [paras[0], paras[3]],
                                  labels=["Block 1", "Block 4"]),
            review=True)
        self.assertEqual(
            [s["slot_id"] for s in self.scene.expected_audio_slots],
            ["blk01:1", "blk04:1"],
            "the recorded structure must match the current text")
        dlg = self.dialog()
        self.assertEqual(
            [j.slot_id for j in dlg._manager.jobs],
            ["blk01:1", "blk04:1"],
            "the queue holds generation items for the CURRENT blocks only")
        self.assertEqual(
            [j.source_block_id for j in dlg._manager.jobs],
            ["blk01", "blk04"])
        self.assertNotIn("missing from the queue",
                         dlg._coverage_label.text())
        # Combine on the current structure: the ONLY uncovered slot is
        # Block 4 (still in the text — legitimate missing audio); the
        # deleted Blocks 2/3 are NEVER demanded despite their orphaned
        # historical audio.
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        self.assertIn("'Block 4'", msg)
        self.assertNotIn("'Block 2'", msg)
        self.assertNotIn("'Block 3'", msg)
        self.assertNotIn(STALE_MARKER, msg)
        # Generate the missing current part; the historical assets for
        # the deleted blocks are preserved (append-only), the new asset
        # joins, and Combine succeeds with exactly the current slots.
        dlg._check_states = _check_dict(dlg, [False, True])
        dlg._on_start()
        self.wait_batch()
        self.assertEqual(len(self.scene.audio_assets), 4,
                         "append-only lineage: the deleted blocks' audio "
                         "history is never deleted to silence a warning")
        self.assertEqual(coverage_counts(self.scene), (2, 2))
        ok2, msg2 = self.combine_warning()
        self.assertTrue(ok2, msg2)
        entry = self.scene.combined_outputs[0]
        self.assertEqual(entry["slot_count"], 2)
        self.assertEqual([s["slot_id"] for s in entry["sources"]],
                         ["blk01:1", "blk04:1"])


# ===========================================================================
# 9) Scenario D — delete and recreate text with repeated phrases
# ===========================================================================
class TestScenarioDRepeatedPhrase(_MWHarness):

    def test_recreated_identical_text_never_associates_with_old_slot(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        from engine.narration_blocks import PromptBlock
        from engine.audio_provenance import coverage_counts
        phrase = "The same repeated phrase appears in both blocks."
        text = phrase + "\n" + phrase
        bA = PromptBlock(id="blkA", start_offset=0, end_offset=len(phrase))
        bB = PromptBlock(id="blkB", start_offset=len(phrase) + 1,
                         end_offset=len(phrase) + 1 + len(phrase))
        self.scene.expected_audio_slots = self.slots_for_blocks(
            [bA, bB], labels=["Block 1", "Block 2"])
        self.scene.audio_assets = [self.direct_asset("blkA:1", 1)]
        self.load_editor(text, [bA, bB])
        self.assertEqual(coverage_counts(self.scene), (1, 2))
        # The user deletes Block 2 and RETYPES the identical phrase:
        # re-detect creates a NEW block with a NEW id for the same text.
        from PySide6.QtGui import QTextCursor
        ed = self.win._editor._editor
        cur = ed.textCursor()
        cur.setPosition(bB.start_offset)
        cur.setPosition(bB.end_offset, QTextCursor.MoveMode.KeepAnchor)
        ed.setTextCursor(cur)
        cur.removeSelectedText()
        _process(30)
        # ... the retype (position len(phrase)+1 == the newline slot):
        cur = ed.textCursor()
        cur.setPosition(len(phrase) + 1)
        cur.insertText(phrase)
        _process(30)
        self.assertEqual(self.win._editor.get_text(), text,
                         "the recreated text is identical to the original")
        bNEW = PromptBlock(id="blkNEW",
                           start_offset=len(phrase) + 1,
                           end_offset=len(phrase) + 1 + len(phrase))
        self.win._editor.begin_scene_load()
        try:
            self.win._editor.set_scene_blocks(
                [bA, bNEW], self.win._editor.get_text())
        finally:
            self.win._editor.end_scene_load()
        # The old slot blkB:1 is stale + uncovered + rowless → missing.
        from engine.audio_provenance import missing_expected_parts
        missing = missing_expected_parts(self.scene, set())
        self.assertEqual([s["slot_id"] for s in missing], ["blkB:1"])
        # Re-add must REFUSE: an identical-text part exists, but its
        # identity is blkNEW — never associated with slot blkB:1.
        with patch.object(QMessageBox, "warning", return_value=None) as warn:
            self.win._on_readd_missing_parts()
        self.assertTrue(warn.called)
        self.assertIn("could NOT be re-added", warn.call_args[0][2])
        self.assertIn("Block 2", warn.call_args[0][2])
        self.assertEqual(
            [getattr(j, "slot_id", None)
             for j in self.win._batch_manager.jobs],
            [], "no job may be created for the obsolete identity")
        # Combine classifies the old slot as a stale reference.
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        self.assertIn("'Block 2'", msg)
        self.assertIn(STALE_MARKER, msg)
        # After a Generate Long re-materialisation the structure is
        # honest again: the new part is GENUINELY missing (its block
        # exists), the old asset still covers only blkA:1.
        self.start_long(
            self.parts_for_blocks([bA, bNEW], [phrase, phrase],
                                  labels=["Block 1", "Block 2"]),
            review=True)
        self.assertEqual(
            [s["slot_id"] for s in self.scene.expected_audio_slots],
            ["blkA:1", "blkNEW:1"])
        self.assertEqual(coverage_counts(self.scene), (1, 2))
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        self.assertIn("'Block 2'", msg)
        self.assertIn(LEGACY_MISSING, msg)
        self.assertNotIn(STALE_MARKER, msg)
        # The identical-text old asset never covers the new identity.
        self.assertEqual(
            [a["slot_id"] for a in self.scene.audio_assets], ["blkA:1"])


# ===========================================================================
# 10) Scenario E — load with stale references (round-trip safety)
# ===========================================================================
class TestScenarioELoadRoundtrip(_MWHarness):

    def test_roundtrip_preserves_state_and_classifies_honestly(self):
        from engine.models import Project
        from engine.audio_provenance import coverage_counts
        text, blocks, paras = self.make_blocks_and_text(3)
        # A post-deletion save: 2 blocks persisted, 3 slots recorded,
        # 2 assets — the stale reference lives in the project file.
        self.scene.text = "\n".join(paras[:2])
        self.scene.narration_blocks = [b.to_dict() for b in blocks[:2]]
        self.scene.expected_audio_slots = self.slots_for_blocks(blocks)
        self.scene.audio_assets = [
            self.direct_asset("blk01:1", 1),
            self.direct_asset("blk02:1", 2),
        ]
        self.project.name = "Eden"
        # Model-level round-trip (json, the on-disk shape).
        data = json.loads(json.dumps(self.project.to_dict()))
        loaded = Project.from_dict(data)
        lscene = loaded.scenes[0]
        self.assertEqual(
            [s["slot_id"] for s in lscene.expected_audio_slots],
            ["blk01:1", "blk02:1", "blk03:1"])
        self.assertEqual(
            [a["slot_id"] for a in lscene.audio_assets],
            ["blk01:1", "blk02:1"])
        self.assertEqual(
            [a["part_version"] for a in lscene.audio_assets], [1, 1])
        self.assertEqual(
            [b["id"] for b in lscene.narration_blocks],
            ["blk01", "blk02"])
        # NO data loss: valid audio still resolves after the load.
        self.assertEqual(coverage_counts(lscene), (2, 3))
        # MW-level: loading the round-tripped scene classifies the stale
        # slot honestly (fail-closed, remedy stated, nothing mutated).
        self.project.scenes[0] = lscene
        self.win._active_project = self.project
        self.win._active_scene = lscene
        self.win._load_scene_state(lscene)
        slots_before = list(lscene.expected_audio_slots)
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        self.assertIn("'Block 3'", msg)
        self.assertIn(STALE_MARKER, msg)
        self.assertEqual(lscene.expected_audio_slots, slots_before)
        self.assertEqual(len(lscene.audio_assets), 2)
        self.assertTrue(all(os.path.isfile(
            os.path.join(self.tmp, a["output_path"]))
            for a in lscene.audio_assets),
            "valid generated audio must survive the load untouched")

    def test_real_disk_save_reload_preserves_contract(self):
        """P3.45.6 closeout — user scenario D: a REAL ProjectManager disk
        save → reload round-trip keeps the exact contract.

        The deleted block does NOT reappear; the valid audio and its
        slot_id / generation_run / part_version append-only lineage are
        NOT lost; Combine classifies the reloaded state honestly (the
        stale reference is named with the correct remedy, nothing is
        mutated); and after regenerating the structure over the reloaded
        text the valid audio still joins and Combine succeeds."""
        from engine.project_manager import ProjectManager
        from engine.audio_provenance import (
            coverage_counts, part_content_sha)
        text, blocks, paras = self.make_blocks_and_text(3)
        # A post-deletion save: 2 blocks persisted, 3 slots recorded,
        # 2 assets — the stale reference lives in the project file.
        # The assets carry content fingerprints (P3.45.8+ generation) so
        # the round-trip must preserve them (identity survives disk).
        self.scene.text = "\n".join(paras[:2])
        self.scene.narration_blocks = [b.to_dict() for b in blocks[:2]]
        self.scene.expected_audio_slots = self.slots_for_blocks(
            blocks, paras=paras)
        self.scene.audio_assets = [
            self.direct_asset("blk01:1", 1,
                              content_sha=part_content_sha(paras[0])),
            self.direct_asset("blk02:1", 2,
                              content_sha=part_content_sha(paras[1])),
        ]
        runs_before = {a["slot_id"]: a["generation_run"]
                       for a in self.scene.audio_assets}
        # REAL disk round-trip through the authoritative persistence
        # (a FRESH manager instance reads the file back from disk).
        pm_dir = os.path.join(self.tmp, "projects")
        ProjectManager(pm_dir).save_project(self.project)
        loaded = ProjectManager(pm_dir).get_project(self.project.id)
        self.assertIsNotNone(loaded)
        lscene = loaded.scenes[0]
        # The deleted block does NOT reappear; the stale slot reference
        # survives truthfully (the load never rewrites history).
        self.assertEqual([b["id"] for b in lscene.narration_blocks],
                         ["blk01", "blk02"])
        self.assertEqual(
            [s["slot_id"] for s in lscene.expected_audio_slots],
            ["blk01:1", "blk02:1", "blk03:1"])
        # Valid audio is NOT lost; the frozen lineage contracts survive.
        self.assertEqual(
            [a["slot_id"] for a in lscene.audio_assets],
            ["blk01:1", "blk02:1"])
        self.assertEqual(
            [a["part_version"] for a in lscene.audio_assets], [1, 1])
        self.assertEqual(
            {a["slot_id"]: a["generation_run"]
             for a in lscene.audio_assets},
            runs_before)
        self.assertTrue(all(os.path.isfile(
            os.path.join(self.tmp, a["output_path"]))
            for a in lscene.audio_assets))
        self.assertEqual(coverage_counts(lscene), (2, 3))
        # P3.45.8: the content fingerprints survive the disk round-trip
        # (slots AND assets — identity is persisted, not re-derived).
        self.assertTrue(all(s.get("content_sha") for s
                            in lscene.expected_audio_slots))
        self.assertTrue(all(a.get("content_sha") for a
                            in lscene.audio_assets))
        # MW-level: the reloaded scene classifies honestly (fail-closed,
        # stale reference named with the remedy, nothing mutated, the
        # valid audio files intact).
        self.win._active_project = loaded
        self.win._active_scene = lscene
        self.win._load_scene_state(lscene)
        slots_before = list(lscene.expected_audio_slots)
        ok, msg = self.combine_warning_for(lscene)
        self.assertFalse(ok)
        self.assertIn("'Block 3'", msg)
        self.assertIn(STALE_MARKER, msg)
        self.assertEqual(lscene.expected_audio_slots, slots_before)
        self.assertEqual(len(lscene.audio_assets), 2)
        # Repair on the RELOADED project: Generate Long over the current
        # text rebuilds the structure; the valid audio still joins and
        # Combine succeeds — no data loss, no resurrected blocks, no
        # duplicated lineage.
        surviving = self.win._editor.block_manager.blocks
        self.assertEqual([b.id for b in surviving], ["blk01", "blk02"])
        self.start_long(self.parts_for_blocks(surviving, paras[:2]),
                        review=True)
        self.assertEqual(
            [s["slot_id"] for s in lscene.expected_audio_slots],
            ["blk01:1", "blk02:1"])
        self.assertEqual(coverage_counts(lscene), (2, 2))
        ok2, msg2 = self.combine_warning_for(lscene)
        self.assertTrue(ok2, msg2)
        entry = lscene.combined_outputs[0]
        self.assertEqual([s["slot_id"] for s in entry["sources"]],
                         ["blk01:1", "blk02:1"])
        self.assertEqual(len(lscene.audio_assets), 2,
                         "the valid audio lineage is untouched by the "
                         "regeneration — no duplicate, no deletion")


# ===========================================================================
# 11) THE GOLDEN PATH — screenshot state → Re-add → generate → Combine
# ===========================================================================
class TestScreenshotGoldenPath(_MWHarness):
    """Reproduces the EXACT reported evidence through real flows, then
    drives the recovery: Re-add Missing → GENERATE CHECKED → Combine.
    """

    def test_screenshot_state_readd_generate_combine_end_to_end(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        from engine.audio_provenance import coverage_counts
        from engine.batch_manager import JobStatus

        # --- 1. A 22-block scene, loaded through the real path --------
        text, blocks, paras = self.make_blocks_and_text(22)
        self.load_editor(text, blocks)

        # --- 2. Generate Long (structure event; review mode → PENDING)
        self.start_long(self.parts_for_blocks(blocks, paras), review=True)
        dlg = self.dialog()
        self.assertEqual(len(dlg._manager.jobs), 22)
        self.assertEqual(len(self.scene.expected_audio_slots), 22)

        # --- 3. Generate only the first 15 (selective run, real audio)
        dlg._check_states = _check_dict(dlg, [True] * 15 + [False] * 7)
        dlg._on_start()
        self.wait_batch()
        self.assertEqual(coverage_counts(self.scene), (15, 22))
        self.assertEqual(
            [j.status for j in dlg._manager.jobs[:15]],
            [JobStatus.COMPLETED] * 15)

        # --- 4. Remove the 7 un-generated rows (the reported action) --
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes) as q:
            for _ in range(7):
                dlg._table.selectRow(15)
                dlg._on_remove()
        self.assertEqual(q.call_count, 7,
                         "every uncovered structural removal states the "
                         "job-only contract")
        self.assertEqual(len(dlg._manager.jobs), 15)

        # --- 5. THE SCREENSHOT STATE -----------------------------------
        header = dlg._coverage_label.text()
        self.assertIn("PARTIAL (15/22)", header)
        self.assertIn("7 expected part(s) missing from the queue", header)
        self.assertEqual(dlg._table.rowCount(), 15)
        self.assertTrue(dlg._readd_btn.isEnabled())
        self.assertEqual(dlg._readd_btn.text(), "Re-add Missing (7)")

        # --- 6. Combine fails closed: GENUINELY missing audio (the
        #        blocks still exist — scenario A, no silent omission) ---
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        for i in range(16, 23):
            self.assertIn("Block {0}".format(i), msg)
        self.assertIn("'Block 16' " + LEGACY_MISSING, msg)
        self.assertNotIn(STALE_MARKER, msg)
        self.assertEqual(len(self.scene.combined_outputs), 0)
        # Nothing was silently omitted: the 7 slots are still required.
        from engine.audio_provenance import missing_expected_parts
        self.assertEqual(
            len(missing_expected_parts(
                self.scene, {j.slot_id for j in dlg._manager.jobs})), 7)

        # --- 7. Re-add Missing repairs the queue workspace ------------
        slots_before = [s["slot_id"] for s in self.scene.expected_audio_slots]
        assets_before = list(self.scene.audio_assets)
        first15 = list(dlg._manager.jobs)
        outputs_before = [j.output_filename for j in first15]
        self.win._on_readd_missing_parts()
        self.assertEqual(len(dlg._manager.jobs), 22)
        readded = dlg._manager.jobs[15:]
        self.assertEqual([j.slot_id for j in readded],
                         ["blk{0:02d}:1".format(i) for i in range(16, 23)])
        self.assertEqual([j.part_index for j in readded],
                         list(range(16, 23)),
                         "the RECORDED structure position is the identity")
        self.assertEqual([j.part_version for j in readded], [1] * 7)
        self.assertEqual([j.source_block_id for j in readded],
                         ["blk{0:02d}".format(i) for i in range(16, 23)])
        runs = {j.generation_run for j in readded}
        self.assertEqual(len(runs), 1, "one fresh run for the activation")
        self.assertTrue(runs.pop().startswith("sc1abcd-r"))
        # Prompts are re-derived from the CURRENT block text.
        for i, job in enumerate(readded):
            self.assertIn(paras[15 + i], job.prompt)
        # The workspace was APPENDED, never wiped: the 15 completed jobs
        # keep their statuses, outputs and positions.
        self.assertEqual(dlg._manager.jobs[:15], first15)
        self.assertEqual(
            [j.status for j in dlg._manager.jobs[:15]],
            [JobStatus.COMPLETED] * 15)
        self.assertEqual([j.output_filename for j in dlg._manager.jobs[:15]],
                         outputs_before)
        # Re-added rows are checked (P3.28 §11: not-generated → checked)
        # — the generation path; the user's prior choices elsewhere in
        # the queue are never reset by the repair.
        for j in readded:
            self.assertTrue(dlg._check_states[dlg._check_key(j)],
                            "re-added job {0} must be checked".format(
                                j.slot_id))
        self.assertIn(15, dlg._checked_indices())
        self.assertIn(21, dlg._checked_indices())
        # The recorded structure, assets and versions are untouched.
        self.assertEqual(
            [s["slot_id"] for s in self.scene.expected_audio_slots],
            slots_before)
        self.assertEqual(self.scene.audio_assets, assets_before)
        # The header no longer reports missing parts.
        self.assertNotIn("missing from the queue",
                         dlg._coverage_label.text())
        self.assertFalse(dlg._readd_btn.isEnabled())

        # --- 8. Generate the re-added 7 (GENERATE CHECKED) ------------
        dlg._check_states = _check_dict(dlg, [False] * 15 + [True] * 7)
        dlg._on_start()
        self.wait_batch()
        self.assertEqual(coverage_counts(self.scene), (22, 22))
        self.assertEqual(len(self.scene.audio_assets), 22)

        # --- 9. Combine SUCCEEDS with the full structure --------------
        ok, msg = self.combine_warning()
        self.assertTrue(ok, msg)
        entry = self.scene.combined_outputs[0]
        self.assertEqual(entry["slot_count"], 22)
        self.assertEqual([s["slot_id"] for s in entry["sources"]],
                         ["blk{0:02d}:1".format(i) for i in range(1, 23)],
                         "Combine output order == expected slot order")
        self.assertTrue(os.path.isfile(os.path.join(
            self.tmp, entry["output_path"])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
