"""
SpeechStudio — P3.28 Scene Audio Provenance + Batch Generation State + Export
==============================================================================

Runtime tests for the P3.28 implementation of the approved design record
(docs/design/SCENE_AUDIO_PROVENANCE_DESIGN_RECORD.md) as mandated by the
P3.28 directive.

THE PRODUCT RULE under test (design record §0): the system records ONLY
  1. has this generation slot ever been generated successfully?
  2. what audio versions exist for it (append-only)?
  3. which output is explicitly selected where selection is required?
There is NO text diffing, NO token tracking, NO prompt hashing, NO
automatic invalidation — tests verify nothing of the sort was introduced.

Test matrix (P3.28 §27, 25 categories):
  1. expected slot derivation            14. review mode
  2. generated block derivation          15. batch default selection
  3. complete/partial/error states       16. partial regeneration
  4. generation run allocation           17. batch export
  5. version allocation                  18. filename security
  6. failed version handling             19. history provenance
  7. no overwrite                        20. export/reimport
  8. regenerated parts                   21. scene routing during generation
  9. scene internal combined output      22. anomaly detection (P3.27B kept)
 10. scene lineage                       23. anomaly persistence
 11. stale combined detection            24. pathological silence
 12. scene output resolution             25. P3.27B versioning regression
 13. project assembly source resolution
Plus: §26 JSON robustness + §25 concurrency (scene switch mid-batch).

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_28_audio_provenance.py -v
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
# Fake torch (house pattern: test_p3_27b / e2e_p325 / e2e_p326)
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


def make_runaway(speech_seconds: float, total_seconds: float) -> np.ndarray:
    speech = make_speech(speech_seconds)
    out = np.zeros(int(total_seconds * SAMPLE_RATE), dtype=np.float32)
    out[: len(speech)] = speech
    return out


class FakeHiggsModel:
    """Model-boundary fake (P3.27B house pattern)."""

    calls = []
    speech_s = 1.0
    runaway = False
    fail_next = 0

    def generate_speech(self, text, tokenizer, *, reference_audio=None,
                        reference_sample_rate=None, reference_codes=None,
                        reference_text=None, max_new_tokens=2048,
                        temperature=1.0, top_p=None, top_k=None):
        FakeHiggsModel.calls.append(dict(text=text))
        if FakeHiggsModel.fail_next > 0:
            FakeHiggsModel.fail_next -= 1
            raise RuntimeError("simulated model failure")
        if FakeHiggsModel.runaway:
            cap_s = max_new_tokens / 25.0
            return make_runaway(FakeHiggsModel.speech_s, cap_s)
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


# ===========================================================================
# 1) PURE-FUNCTION TESTS — engine/audio_provenance.py (no Qt)
# ===========================================================================
class TestSlotDerivation(unittest.TestCase):
    """§27-1: expected slot derivation (block/plain/speaker splits)."""

    def test_block_parts_get_block_slot_ids(self):
        from engine.audio_provenance import (
            materialize_expected_slots, block_slot_id)
        parts = [
            _make_part(block_id="blk1", part_of_block=1),
            _make_part(block_id="blk1", part_of_block=2),
            _make_part(block_id="blk2", part_of_block=1),
        ]
        slots = materialize_expected_slots(parts)
        self.assertEqual(len(slots), 3)
        self.assertEqual(slots[0]["slot_id"], block_slot_id("blk1", 1))
        self.assertEqual(slots[1]["slot_id"], block_slot_id("blk1", 2))
        self.assertEqual(slots[2]["slot_id"], block_slot_id("blk2", 1))
        self.assertEqual([s["part_index"] for s in slots], [1, 2, 3])

    def test_plain_parts_get_plain_slot_ids(self):
        from engine.audio_provenance import (
            materialize_expected_slots, plain_slot_id)
        parts = [_make_part(), _make_part(), _make_part()]
        slots = materialize_expected_slots(parts)
        self.assertEqual([s["slot_id"] for s in slots],
                         [plain_slot_id(1), plain_slot_id(2), plain_slot_id(3)])
        self.assertTrue(all(s["block_id"] is None for s in slots))

    def test_slot_structure_carries_context(self):
        from engine.audio_provenance import materialize_expected_slots
        parts = [_make_part(speaker="CAPTAIN", character_id="ch1")]
        slots = materialize_expected_slots(parts)
        self.assertEqual(slots[0]["speaker"], "CAPTAIN")
        self.assertEqual(slots[0]["character_id"], "ch1")


class _SceneFactory(unittest.TestCase):
    """Shared helpers for scene-level pure tests."""

    def make_scene(self, slots=None, assets=None, combined=None,
                   selected_block_audio=None, selected_output=None):
        from engine.models import Scene
        s = Scene(id="ab12cd34ef56", name="Scene 03")
        if slots:
            s.expected_audio_slots = list(slots)
        if assets:
            s.audio_assets = list(assets)
        if combined:
            s.combined_outputs = list(combined)
        if selected_block_audio:
            s.selected_block_audio = dict(selected_block_audio)
        if selected_output:
            s.selected_output = dict(selected_output)
        return s

    def asset(self, slot_part_index, version, path="outputs/x.wav",
              block_id=None, run="ab12cd34-r001", duration=10.0):
        return {
            "id": "a{0}v{1}".format(slot_part_index, version),
            "scene_id": "ab12cd34ef56",
            "output_path": path.format(i=slot_part_index, v=version),
            "duration": duration,
            "speaker": None,
            "character_id": None,
            "voice_profile_id": None,
            "generated_at": "20260101_120000",
            "block_id": block_id,
            "part_index": slot_part_index,
            "part_version": version,
            "generation_run": run,
        }


class TestBlockGeneratedState(_SceneFactory):
    """§27-2: block generated state is DERIVED, never stored."""

    def test_b1_b2_b4_generated_b3_not(self):
        # 4 blocks, one slot each; B3 has no asset.
        slots = [
            {"slot_id": "b1:1", "block_id": "b1", "block_label": "B1",
             "part_index": 1, "part_of_block": 1, "speaker": "",
             "character_id": None},
            {"slot_id": "b2:1", "block_id": "b2", "block_label": "B2",
             "part_index": 2, "part_of_block": 1, "speaker": "",
             "character_id": None},
            {"slot_id": "b3:1", "block_id": "b3", "block_label": "B3",
             "part_index": 3, "part_of_block": 1, "speaker": "",
             "character_id": None},
            {"slot_id": "b4:1", "block_id": "b4", "block_label": "B4",
             "part_index": 4, "part_of_block": 1, "speaker": "",
             "character_id": None},
        ]
        assets = [self.asset(1, 1, block_id="b1"),
                  self.asset(2, 1, block_id="b2"),
                  self.asset(2, 2, block_id="b2"),
                  self.asset(4, 1, block_id="b4")]
        scene = self.make_scene(slots=slots, assets=assets)
        from engine.audio_provenance import block_slot_states
        states = {b["block_id"]: b for b in block_slot_states(scene)}
        self.assertTrue(states["b1"]["generated"])
        self.assertTrue(states["b2"]["generated"])
        self.assertFalse(states["b3"]["generated"])
        self.assertTrue(states["b4"]["generated"])
        self.assertEqual(states["b2"]["version_count"], 2)

    def test_multi_slot_block_not_generated_until_all_covered(self):
        slots = [
            {"slot_id": "b1:1", "block_id": "b1", "part_index": 1,
             "part_of_block": 1, "block_label": "B1", "speaker": "",
             "character_id": None},
            {"slot_id": "b1:2", "block_id": "b1", "part_index": 2,
             "part_of_block": 2, "block_label": "B1", "speaker": "",
             "character_id": None},
        ]
        scene = self.make_scene(slots=slots,
                                assets=[self.asset(1, 1, block_id="b1")])
        from engine.audio_provenance import block_slot_states
        states = {b["block_id"]: b for b in block_slot_states(scene)}
        self.assertFalse(states["b1"]["generated"])  # 1 of 2 slots covered

    def test_no_stored_generated_state_field(self):
        from engine.models import Scene
        s = Scene()
        self.assertFalse(hasattr(s, "generated_blocks"),
                         "Scene.generated_blocks is FORBIDDEN (second truth)")


class TestSceneCompleteness(_SceneFactory):
    """§27-3: complete/partial/error/not-generated/generating states."""

    def _scene_with_coverage(self, covered, total):
        slots = [{"slot_id": "s{0}".format(i),
                  "block_id": "b{0}".format(i), "part_index": i + 1,
                  "part_of_block": 1, "block_label": "B{0}".format(i),
                  "speaker": "", "character_id": None}
                 for i in range(total)]
        assets = [self.asset(i + 1, 1, block_id="b{0}".format(i))
                  for i in range(covered)]
        return self.make_scene(slots=slots, assets=assets)

    def test_states_matrix(self):
        from engine.audio_provenance import (
            scene_coverage_state, SCENE_NOT_GENERATED, SCENE_PARTIAL,
            SCENE_COMPLETE, SCENE_ERROR, SCENE_GENERATING,
        )
        self.assertEqual(scene_coverage_state(self._scene_with_coverage(0, 3)),
                         SCENE_NOT_GENERATED)
        self.assertEqual(scene_coverage_state(self._scene_with_coverage(0, 3),
                                              last_run_failed=True),
                         SCENE_ERROR)
        self.assertEqual(scene_coverage_state(self._scene_with_coverage(1, 3)),
                         SCENE_PARTIAL)
        self.assertEqual(scene_coverage_state(self._scene_with_coverage(3, 3)),
                         SCENE_COMPLETE)
        # GENERATING wins over everything while a run is in flight.
        self.assertEqual(scene_coverage_state(self._scene_with_coverage(0, 3),
                                              generating=True),
                         SCENE_GENERATING)
        self.assertEqual(scene_coverage_state(self._scene_with_coverage(3, 3),
                                              generating=True),
                         SCENE_GENERATING)

    def test_failed_regen_of_covered_slot_does_not_degrade_complete(self):
        scene = self._scene_with_coverage(3, 3)
        from engine.audio_provenance import (
            scene_coverage_state, recompute_scene_status)
        # A failed RE-generation: coverage untouched (all assets remain).
        state = scene_coverage_state(scene, last_run_failed=True)
        self.assertEqual(state, "complete",
                         "a failed regen must NOT degrade a COMPLETE scene")

    def test_legacy_scene_semantics(self):
        # No expected_audio_slots ever materialised → ≥1 asset = covered.
        scene = self.make_scene(assets=[self.asset(1, 1)])
        from engine.audio_provenance import coverage_counts
        self.assertEqual(coverage_counts(scene), (1, 1))
        empty = self.make_scene()
        self.assertEqual(coverage_counts(empty), (0, 1))

    def test_full_scene_asset_does_not_cover_slots(self):
        # A full-scene asset (single Generate) is NOT slot-mapped: with
        # materialised slots, coverage stays 0/2 → NOT_GENERATED (the
        # asset remains available through the resolution chain's rule 3).
        scene = self._scene_with_coverage(0, 2)
        scene.audio_assets.append({
            "id": "full1", "scene_id": scene.id, "output_path": "outputs/f.wav",
            "duration": 5.0, "generated_at": "20260101_120000",
            "block_id": None, "part_index": None, "part_version": None,
            "generation_run": None})
        from engine.audio_provenance import scene_coverage_state
        self.assertEqual(scene_coverage_state(scene), "not_generated")


class TestGenerationRunAllocation(_SceneFactory):
    """§27-4: generation run allocation — metadata, no registry."""

    def test_run_id_format_and_monotonic_sequence(self):
        scene = self.make_scene(assets=[
            self.asset(1, 1, run="ab12cd34-r001"),
            self.asset(2, 1, run="ab12cd34-r002"),
        ])
        from engine.audio_provenance import allocate_generation_run
        self.assertEqual(allocate_generation_run(scene), "ab12cd34-r003")

    def test_run_counts_combined_outputs(self):
        scene = self.make_scene(assets=[
            self.asset(1, 1, run="ab12cd34-r005")],
            combined=[{"id": "c1", "version": 1,
                       "generation_run": "ab12cd34-r007"}])
        from engine.audio_provenance import allocate_generation_run
        self.assertEqual(allocate_generation_run(scene), "ab12cd34-r008")

    def test_first_run_is_r001(self):
        from engine.audio_provenance import allocate_generation_run
        self.assertEqual(allocate_generation_run(self.make_scene()),
                         "ab12cd34-r001")


class TestVersionAllocation(_SceneFactory):
    """§27-5/6/7: version allocation, failed versions, no overwrite."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p328_ver_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_monotonic_max_plus_one(self):
        scene = self.make_scene(assets=[
            self.asset(1, 1), self.asset(1, 3)])
        from engine.audio_provenance import next_slot_version
        filename, version = next_slot_version(
            scene, "plain:1", self.tmp, project="Eden", scene_name="S03",
            part_index=1)
        self.assertEqual(version, 4)  # max(1,3)+1
        self.assertIn("_v04_", filename)

    def test_disk_guard_bumps_on_collision(self):
        # v02 filename already on disk → allocator bumps to v03 even
        # though the asset-aware start is v02.
        from engine.output_guard import build_part_filename
        existing = build_part_filename("Eden", "S03", 1, 2)
        with open(os.path.join(self.tmp, existing), "wb") as f:
            f.write(b"x")
        scene = self.make_scene(assets=[self.asset(1, 1)])
        from engine.audio_provenance import next_slot_version
        filename, version = next_slot_version(
            scene, "plain:1", self.tmp, project="Eden", scene_name="S03",
            part_index=1)
        self.assertEqual(version, 3)
        self.assertIn("_v03_", filename)

    def test_failed_generation_consumes_no_version(self):
        # A failed generation appends NO asset → the next allocation for
        # the slot returns the SAME next version (nothing consumed).
        scene = self.make_scene(assets=[self.asset(1, 1)])
        from engine.audio_provenance import next_slot_version
        _, v1 = next_slot_version(scene, "plain:1", self.tmp,
                                  project="Eden", scene_name="S03",
                                  part_index=1)
        # (simulated failed generation: nothing appended, no file written)
        _, v2 = next_slot_version(scene, "plain:1", self.tmp,
                                  project="Eden", scene_name="S03",
                                  part_index=1)
        self.assertEqual(v1, v2)


class TestSceneCombinedOutputs(_SceneFactory):
    """§27-9/10/11: scene internal combination, lineage, staleness."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p328_comb_")
        # Real files so staleness/existence checks are exercised (paths
        # are resolved against the tmp app root).
        os.makedirs(os.path.join(self.tmp, "outputs"), exist_ok=True)
        import soundfile as sf
        for name in ("f_v01.wav", "f_v02.wav", "c1.wav", "c2.wav"):
            sf.write(os.path.join(self.tmp, "outputs", name),
                     make_speech(1.0), SAMPLE_RATE, subtype="FLOAT")
        from engine.output_guard import build_part_filename
        self.files = []
        for v in (1, 2):
            name = build_part_filename("Eden", "S03", 1, v)
            path = os.path.join(self.tmp, name)
            sf.write(path, make_speech(1.0), SAMPLE_RATE, subtype="FLOAT")
            self.files.append(path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _covered_scene(self):
        slots = [{"slot_id": "plain:1", "block_id": None,
                  "part_index": 1, "part_of_block": 1, "block_label": "B1",
                  "speaker": "", "character_id": None}]
        assets = [
            self.asset(1, 1, path="outputs/f_v01.wav"),
            self.asset(1, 2, path="outputs/f_v02.wav"),
        ]
        return self.make_scene(slots=slots, assets=assets)

    def test_combined_filename_convention(self):
        from engine.audio_provenance import build_scene_combined_filename
        self.assertEqual(
            build_scene_combined_filename("Eden", "Scene 03", 1,
                                          scene_id="ab12cd34ef56"),
            "Eden_Scene_03_Combined_v01_ab12cd34.wav")

    def test_combined_version_monotonic_disk_guarded(self):
        from engine.audio_provenance import next_scene_combined_version
        scene = self.make_scene(combined=[{"id": "c1", "version": 1}])
        # v01 filename exists on disk → bumped to v02.
        from engine.audio_provenance import build_scene_combined_filename
        existing = build_scene_combined_filename("Eden", "S03", 1,
                                                 scene_id="ab12cd34ef56")
        with open(os.path.join(self.tmp, existing), "wb") as f:
            f.write(b"x")
        filename, version = next_scene_combined_version(
            scene, self.tmp, project="Eden", scene_name="S03",
            scene_id="ab12cd34ef56")
        self.assertEqual(version, 2)
        self.assertIn("Combined_v02", filename)

    def test_stale_detection_by_version_lineage(self):
        scene = self._covered_scene()
        entry = {"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                 "sources": [{"slot_id": "plain:1", "part_version": 1,
                              "output_path": "outputs/f_v01.wav"}]}
        from engine.audio_provenance import is_scene_combined_stale
        # Current max version for the slot is v02 > recorded v01 → stale.
        stale, reasons = is_scene_combined_stale(scene, entry, self.tmp)
        self.assertTrue(stale)
        self.assertTrue(any("newer version" in r for r in reasons))

    def test_fresh_combined_not_stale(self):
        scene = self._covered_scene()
        entry = {"id": "c2", "version": 2, "output_path": "outputs/c2.wav",
                 "sources": [{"slot_id": "plain:1", "part_version": 2,
                              "output_path": "outputs/f_v02.wav"}]}
        from engine.audio_provenance import is_scene_combined_stale
        stale, reasons = is_scene_combined_stale(scene, entry, self.tmp)
        self.assertFalse(stale, reasons)

    def test_missing_source_file_stale(self):
        scene = self._covered_scene()
        # The slot's CURRENT asset file (what a re-combine would use) is
        # missing from disk → the combined output is stale.
        os.remove(os.path.join(self.tmp, "outputs", "f_v02.wav"))
        entry = {"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                 "sources": [{"slot_id": "plain:1", "part_version": 2,
                              "output_path": "outputs/f_v02.wav"}]}
        from engine.audio_provenance import is_scene_combined_stale
        stale, reasons = is_scene_combined_stale(scene, entry, self.tmp)
        self.assertTrue(stale)
        self.assertTrue(any("missing" in r for r in reasons))

    def test_unresolvable_slot_with_missing_recorded_file_stale(self):
        scene = self._covered_scene()
        # The slot no longer resolves (structure changed) AND the
        # recorded lineage file is gone → stale.
        scene.expected_audio_slots = []
        entry = {"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                 "sources": [{"slot_id": "plain:1", "part_version": 1,
                              "output_path": "outputs/MISSING.wav"}]}
        from engine.audio_provenance import is_scene_combined_stale
        stale, _ = is_scene_combined_stale(scene, entry, self.tmp)
        self.assertTrue(stale)

    def test_lineage_fields_recorded(self):
        from engine.audio_provenance import build_scene_combined_entry
        sources = [{"slot_id": "plain:1", "block_id": None,
                    "block_label": "B1", "part_index": 1,
                    "asset_id": "a1v1", "part_version": 1,
                    "speaker": None, "character_id": None,
                    "output_path": "outputs/f_v01.wav", "duration": 10.0}]
        entry = build_scene_combined_entry(
            self._covered_scene(), version=1, output_path="outputs/c1.wav",
            duration=10.0, sources=sources, silence_ms=300,
            generation_run="ab12cd34-r002", project_name="Eden")
        for key in ("slot_id", "block_id", "part_index", "asset_id",
                    "part_version", "speaker", "character_id",
                    "output_path", "duration"):
            self.assertIn(key, entry["sources"][0],
                          "combined lineage missing {0}".format(key))
        self.assertEqual(entry["generation_run"], "ab12cd34-r002")


class TestSceneOutputResolution(_SceneFactory):
    """§27-12/13: scene output resolution incl. the §15 DESIGN CORRECTION."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p328_res_")
        self.real_files = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _file(self, rel):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import soundfile as sf
        sf.write(path, make_speech(1.0), SAMPLE_RATE, subtype="FLOAT")
        self.real_files.append(path)
        return path

    def test_chain_latest_asset_fallback(self):
        """LEGACY fallback (accepted follow-up safety rule): a PROVABLE
        full-scene asset (no slot provenance, no part-file naming — e.g.
        a single full-scene Generate) still resolves when the Scene has
        no combined outputs. A PART asset NEVER does."""
        legacy_asset = {
            "id": "legacy1", "scene_id": "ab12cd34ef56",
            "output_path": "outputs/20260101_120000.wav",
            "duration": 10.0, "speaker": None, "character_id": None,
            "voice_profile_id": None, "generated_at": "20260101_120000",
            "block_id": None, "part_index": None, "part_version": None,
            "generation_run": None,
        }
        scene = self.make_scene(assets=[legacy_asset])
        self._file("outputs/20260101_120000.wav")
        from engine.audio_provenance import resolve_scene_output
        res = resolve_scene_output(scene, self.tmp)
        self.assertEqual(res["kind"], "asset")
        self.assertEqual(res["label"], "Latest audio")
        self.assertFalse(res["requires_review"])

    def test_part_asset_never_resolves_automatically(self):
        """Accepted safety rule: a single Part AudioAsset is NEVER the
        Scene's complete output — not even when it is the only asset,
        and not via legacy part-file naming either."""
        from engine.audio_provenance import (
            resolve_scene_output, is_complete_scene_asset)
        # Post-P3.28 Part (slot provenance recorded).
        scene = self.make_scene(
            assets=[self.asset(1, 2, path="outputs/p1v2.wav")])
        self._file("outputs/p1v2.wav")
        res = resolve_scene_output(scene, self.tmp)
        self.assertTrue(res["requires_review"],
                        "a Part must never auto-resolve as the Scene output")
        self.assertIsNone(res["kind"])
        # Pre-P3.28 legacy part file (no provenance, _Part_ naming).
        legacy_part = {
            "id": "lp1", "scene_id": "ab12cd34ef56",
            "output_path": "outputs/Eden_S03_Part_01_ab12cd34.wav",
            "duration": 10.0, "speaker": None, "character_id": None,
            "voice_profile_id": None, "generated_at": "20250101_120000",
            "block_id": None, "part_index": None, "part_version": None,
            "generation_run": None,
        }
        self.assertFalse(is_complete_scene_asset(legacy_part),
                         "legacy _Part_ naming must fail the proof")
        scene2 = self.make_scene(assets=[legacy_part])
        self._file("outputs/Eden_S03_Part_01_ab12cd34.wav")
        res2 = resolve_scene_output(scene2, self.tmp)
        self.assertTrue(res2["requires_review"])

    def test_latest_non_stale_combined_preferred(self):
        slots = [{"slot_id": "plain:1", "block_id": None, "part_index": 1,
                  "part_of_block": 1, "block_label": "", "speaker": "",
                  "character_id": None}]
        assets = [self.asset(1, 1, path="outputs/p1.wav")]
        combined = [{"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                     "duration": 5.0,
                     "sources": [{"slot_id": "plain:1", "part_version": 1,
                                  "output_path": "outputs/p1.wav"}]}]
        scene = self.make_scene(slots=slots, assets=assets, combined=combined)
        self._file("outputs/p1.wav")
        self._file("outputs/c1.wav")
        from engine.audio_provenance import resolve_scene_output
        res = resolve_scene_output(scene, self.tmp)
        self.assertEqual(res["kind"], "scene_combined")
        self.assertEqual(res["label"], "Combined v01")

    def test_stale_combined_all_stale_requires_review(self):
        """P3.28 §15 CORRECTION, superseded by the P3.30(g) AUDIT:
        automatic resolution must NEVER use a stale combined output —
        and (P3.30(g)) must NEVER silently fall through to a single
        PART asset either: for block Scenes audio_assets[-1] is ONE
        PART's WAV, which the assembly would consume as the WHOLE
        Scene's audio without a word. A Scene whose combined outputs
        all exist but are ALL stale requires REVIEW: re-combine (one
        click) or explicitly choose the stale output (Use Anyway)."""
        slots = [{"slot_id": "plain:1", "block_id": None, "part_index": 1,
                  "part_of_block": 1, "block_label": "", "speaker": "",
                  "character_id": None}]
        assets = [self.asset(1, 1, path="outputs/p1v1.wav"),
                  self.asset(1, 2, path="outputs/p1v2.wav")]
        combined = [{"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                     "duration": 5.0,
                     "sources": [{"slot_id": "plain:1", "part_version": 1,
                                  "output_path": "outputs/p1v1.wav"}]}]
        scene = self.make_scene(slots=slots, assets=assets, combined=combined)
        self._file("outputs/p1v1.wav")
        self._file("outputs/p1v2.wav")
        self._file("outputs/c1.wav")
        from engine.audio_provenance import resolve_scene_output
        res = resolve_scene_output(scene, self.tmp)
        self.assertTrue(res["requires_review"],
                        "all-stale combined must require review — never a "
                        "silent single-part fallback")
        self.assertIsNone(res["kind"], "nothing is auto-selected")
        self.assertIn("stale", res["reason"])

    def test_explicit_stale_selection_honoured_but_flagged(self):
        slots = [{"slot_id": "plain:1", "block_id": None, "part_index": 1,
                  "part_of_block": 1, "block_label": "", "speaker": "",
                  "character_id": None}]
        assets = [self.asset(1, 1, path="outputs/p1v1.wav"),
                  self.asset(1, 2, path="outputs/p1v2.wav")]
        combined = [{"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                     "duration": 5.0,
                     "sources": [{"slot_id": "plain:1", "part_version": 1,
                                  "output_path": "outputs/p1v1.wav"}]}]
        scene = self.make_scene(
            slots=slots, assets=assets, combined=combined,
            selected_output={"kind": "scene_combined", "id": "c1"})
        self._file("outputs/p1v1.wav")
        self._file("outputs/p1v2.wav")
        self._file("outputs/c1.wav")
        from engine.audio_provenance import resolve_scene_output
        res = resolve_scene_output(scene, self.tmp)
        self.assertTrue(res["custom"])
        self.assertTrue(res["stale"],
                        "explicit stale selection is honoured but FLAGGED")

    def test_all_stale_requires_review(self):
        """§15: if all candidates are stale/missing → review required;
        never silently assemble stale audio."""
        slots = [{"slot_id": "plain:1", "block_id": None, "part_index": 1,
                  "part_of_block": 1, "block_label": "", "speaker": "",
                  "character_id": None}]
        assets = [self.asset(1, 1, path="outputs/MISSING_ASSET.wav")]
        combined = [{"id": "c1", "version": 1,
                     "output_path": "outputs/MISSING_COMBINED.wav",
                     "duration": 5.0,
                     "sources": [{"slot_id": "plain:1", "part_version": 1,
                                  "output_path": "outputs/MISSING.wav"}]}]
        scene = self.make_scene(slots=slots, assets=assets, combined=combined)
        from engine.audio_provenance import resolve_scene_output
        res = resolve_scene_output(scene, self.tmp)
        self.assertTrue(res["requires_review"])

    def test_explicit_selection_missing_file_requires_review(self):
        scene = self.make_scene(
            selected_output={"kind": "asset", "id": "a1v1"},
            assets=[self.asset(1, 1, path="outputs/MISSING.wav")])
        from engine.audio_provenance import resolve_scene_output
        res = resolve_scene_output(scene, self.tmp)
        self.assertTrue(res["requires_review"])


class TestRegisterGenerationResult(_SceneFactory):
    """§27-21 (scene routing, D-3) + the ONE WRITER contract."""

    def test_routes_by_scene_id_not_active_scene(self):
        from engine.models import Project
        scene_a = self.make_scene()
        scene_a.id = "sceneAAAA"
        scene_b = self.make_scene()
        scene_b.id = "sceneBBBB"
        project = Project(name="P")
        project.add_scene(scene_a)
        project.add_scene(scene_b)

        class FakeResult:
            success = True
            scene_id = "sceneBBBB"       # belongs to B...
            output_path = "outputs/x.wav"
            output_duration = 3.0
            speaker = None
            character_id = None
            voice_profile = None
            timestamp = "20260101_120000"
            block_id = None
            part_index = None
            part_version = None
            generation_run = None

        from engine.audio_provenance import register_generation_result
        asset = register_generation_result(
            project, FakeResult(), scene_fallback=scene_a)  # ...A is active
        self.assertIsNotNone(asset)
        self.assertEqual(len(scene_b.audio_assets), 1)
        self.assertEqual(len(scene_a.audio_assets), 0,
                         "D-3: the asset must land on the result's scene, "
                         "not whichever scene is active")

    def test_provenance_tags_attached(self):
        from engine.models import Project
        scene = self.make_scene()
        project = Project(name="P")
        project.add_scene(scene)

        class FakeResult:
            success = True
            scene_id = scene.id
            output_path = "outputs/x.wav"
            output_duration = 3.0
            speaker = "CAPTAIN"
            character_id = "ch1"
            voice_profile = None
            timestamp = "20260101_120000"
            block_id = "blk9"
            part_index = 2
            part_version = 3
            generation_run = "ab12cd34-r004"

        from engine.audio_provenance import register_generation_result
        asset = register_generation_result(project, FakeResult())
        self.assertEqual(asset["block_id"], "blk9")
        self.assertEqual(asset["part_index"], 2)
        self.assertEqual(asset["part_version"], 3)
        self.assertEqual(asset["generation_run"], "ab12cd34-r004")
        self.assertEqual(asset["speaker"], "CAPTAIN")

    def test_no_scene_context_returns_none(self):
        from engine.models import Project

        class FakeResult:
            scene_id = None
            output_path = "outputs/x.wav"
            output_duration = 1.0
            speaker = None
            character_id = None
            voice_profile = None
            timestamp = "20260101_120000"
            block_id = None
            part_index = None
            part_version = None
            generation_run = None

        from engine.audio_provenance import register_generation_result
        self.assertIsNone(
            register_generation_result(Project(), FakeResult(), None))


class TestTimestampParseFix(unittest.TestCase):
    """D-4: both timestamp formats parse; is_combined_output_stale fixed."""

    def test_parse_both_formats(self):
        from engine.audio_provenance import parse_any_timestamp
        self.assertIsNotNone(parse_any_timestamp("2026-01-01T12:00:00"))
        self.assertIsNotNone(parse_any_timestamp("20260101_120000"))
        self.assertIsNone(parse_any_timestamp(""))
        self.assertIsNone(parse_any_timestamp(None))
        self.assertIsNone(parse_any_timestamp("garbage"))

    def test_legacy_fresh_combined_not_stale(self):
        """The D-4 regression: ISO created_at vs compact generated_at."""
        from engine.combined_audio import is_combined_output_stale
        entry = {"created_at": "2026-01-01T12:00:00", "scene_ids": ["s1"]}
        scene = type("S", (), {"id": "s1", "audio_assets": [
            {"generated_at": "20250101_120000"}]})()
        # Asset generated BEFORE the combined output (2025 < 2026) → NOT
        # stale. The old lexicographic compare said "2025..." > "2026-.."
        # is False... actually "20250101" < "2026-01" lexicographically is
        # True for < — the bug triggered when asset was NEWER, e.g.:
        scene.audio_assets[0]["generated_at"] = "20260102_090000"
        self.assertTrue(is_combined_output_stale(entry, [scene]))
        scene.audio_assets[0]["generated_at"] = "20251231_235959"
        self.assertFalse(is_combined_output_stale(entry, [scene]))


class TestJSONRobustness(unittest.TestCase):
    """§26: malformed provenance data must never crash the model."""

    def test_scene_from_dict_malformed_fields(self):
        from engine.models import Scene
        s = Scene.from_dict({
            "id": "x", "name": "S",
            "expected_audio_slots": "not-a-list",
            "combined_outputs": [1, 2, {"id": "ok"}],
            "selected_block_audio": {"a": 1, "b": None, 3: "ok"},
            "selected_output": ["not", "a", "dict"],
            "audio_assets": "garbage",
            "status": 42,
        })
        self.assertEqual(s.expected_audio_slots, [])
        self.assertEqual(s.combined_outputs, [{"id": "ok"}])
        self.assertEqual(s.selected_block_audio, {"3": "ok"})
        self.assertIsNone(s.selected_output)
        self.assertEqual(s.audio_assets, [])

    def test_scene_roundtrip_preserves_provenance(self):
        from engine.models import Scene
        s = Scene.from_dict({
            "id": "x", "name": "S",
            "expected_audio_slots": [{"slot_id": "plain:1"}],
            "combined_outputs": [{"id": "c1", "version": 1}],
            "selected_block_audio": {"plain:1": "asset9"},
            "selected_output": {"kind": "scene_combined", "id": "c1"},
        })
        data = s.to_dict()
        s2 = Scene.from_dict(data)
        self.assertEqual(s2.expected_audio_slots, s.expected_audio_slots)
        self.assertEqual(s2.combined_outputs, s.combined_outputs)
        self.assertEqual(s2.selected_block_audio, s.selected_block_audio)
        self.assertEqual(s2.selected_output, s.selected_output)

    def test_history_entry_malformed_provenance(self):
        from engine.history_manager import HistoryEntry
        entry = HistoryEntry({
            "type": 42, "block_id": 99, "generation_run": [],
            "part_index": "x", "part_version": True, "source_ids": "no"})
        self.assertIsNone(entry.type)
        self.assertIsNone(entry.block_id)
        self.assertIsNone(entry.generation_run)
        self.assertIsNone(entry.part_index)
        self.assertIsNone(entry.part_version)
        self.assertEqual(entry.source_ids, [])

    def test_batchjob_from_dict_malformed_provenance(self):
        from engine.batch_manager import BatchJob
        job = BatchJob.from_dict({
            "source_block_id": 5, "generation_run": [], "slot_id": {},
            "part_index": "x"})
        self.assertIsNone(job.source_block_id)
        self.assertIsNone(job.generation_run)
        self.assertIsNone(job.slot_id)


# ===========================================================================
# 2) PIPELINE TESTS — Engine + BatchManager with the fake model (no
#    MainWindow); provenance passthrough + the ONE WRITER.
# ===========================================================================
class _PipelineHarness(unittest.TestCase):
    """Real Engine + BatchManager with the model boundary faked."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p328_pipe_")
        from engine.engine import Engine
        from engine.batch_manager import BatchManager
        from ui.panels.batch_generation import _UiMarshaler
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
        self.marshaler = _UiMarshaler()
        self.bm = BatchManager(submit_fn=self.engine.generate,
                               marshal_to_ui=self.marshaler.marshal)
        FakeHiggsModel.calls = []
        FakeHiggsModel.runaway = False
        FakeHiggsModel.fail_next = 0
        FakeHiggsModel.speech_s = 1.0

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait_batch(self, timeout=30):
        t0 = time.time()
        while self.bm.is_running and time.time() - t0 < timeout:
            _process(20)
        _process(150)

    def make_part_job(self, part_index=1, filename=None, block_id=None,
                      slot_id=None, run=None, version=1, scene_id="sc1"):
        from engine.batch_manager import BatchJob
        from engine.models import GenerationParameters
        return BatchJob(
            name="Part {0}".format(part_index),
            prompt="A short test sentence for part {0}.".format(part_index),
            voice_id=None,
            parameters=GenerationParameters(
                temperature=0.95, top_p=0.95, top_k=300, max_new_tokens=4096,
                seed=None, append_silence=0.5, normalize_output=False,
                auto_play=False),
            output_filename=filename,
            project="Eden", speaker=None,
            scene_id=scene_id, scene_name="Scene 03",
            part_index=part_index, part_version=version,
            source_block_id=block_id, generation_run=run, slot_id=slot_id)


class TestProvenancePassthrough(_PipelineHarness):
    """Request → Engine → Result provenance passthrough (§2)."""

    def test_block_and_run_reach_result_and_history(self):
        job = self.make_part_job(
            part_index=2, block_id="blk7", slot_id="blk7:1",
            run="sc1abcd-r003", version=1)
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        self.assertEqual(job.status.value, "completed")
        entries = self.engine._history.list_entries()
        self.assertTrue(entries)
        entry = entries[0]
        self.assertEqual(entry.block_id, "blk7")
        self.assertEqual(entry.generation_run, "sc1abcd-r003")
        self.assertEqual(entry.part_index, 2)
        self.assertEqual(entry.type, "part")

    def test_history_type_backfill_for_legacy_entries(self):
        # Legacy concat-style entries get their type backfilled on read.
        from engine.models import GenerationResult
        self.engine._history.add(GenerationResult(
            success=True, output_path="outputs/x.wav",
            prompt="[Concatenated Long Narration - 3 parts]\n  x",
            timestamp="20260101_120000"))
        entries = self.engine._history.list_entries()
        self.assertEqual(entries[0].type, "concat")

    def test_anomaly_persists_through_result_history_and_job(self):
        """§27-23 + §27-24: the P3.27B outlier flows through provenance."""
        FakeHiggsModel.runaway = True
        FakeHiggsModel.speech_s = 2.0
        job = self.make_part_job(
            part_index=1, block_id="blk1", slot_id="blk1:1",
            run="sc1abcd-r001", version=1)
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        self.assertIsNotNone(job.anomaly, "outlier must be flagged")
        self.assertTrue(job.anomaly.get("detected"))
        entries = self.engine._history.list_entries()
        self.assertIsNotNone(entries[0].output_anomaly)
        # The file was KEPT (never trimmed/discarded).
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, job.output_path)))


class TestFilenameSecurity(unittest.TestCase):
    """§27-18: filename safety (containment + no silent overwrite)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p328_sec_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _audio(self):
        from engine.audio_manager import AudioManager
        return AudioManager(os.path.join(self.tmp, "outputs"))

    def test_traversal_contained_never_escapes(self):
        """§19: ../ absolute paths, UNC and drive forms can NEVER escape
        the outputs directory. Design record Rec 21: basename +
        traversal-strip (the sanitized file lands INSIDE outputs)."""
        audio = self._audio()
        outputs_abs = os.path.realpath(os.path.join(self.tmp, "outputs"))
        # Distinct basenames: the no-overwrite guard would otherwise
        # (correctly) reject the second identical save.
        cases = ("../evil1.wav", "..\\evil2.wav", "/abs/path3.wav",
                 "C:\\evil4.wav", "\\\\server\\share5.wav",
                 "sub/../x6.wav")
        for bad in cases:
            rel = audio.save_wav(make_speech(0.1), filename=bad)
            full = os.path.realpath(os.path.join(self.tmp, rel))
            try:
                self.assertEqual(
                    os.path.commonpath([outputs_abs, full]), outputs_abs,
                    "{0!r} escaped the outputs dir!".format(bad))
            except ValueError:
                self.fail("{0!r} escaped the outputs dir!".format(bad))
        # Pure relative forms with no usable basename are rejected.
        from engine.errors import OutputWriteFailed
        for bad in ("..", ".", " ", ""):
            with self.assertRaises(OutputWriteFailed, msg=repr(bad)):
                audio.save_wav(make_speech(0.1), filename=bad)
        # Nothing landed OUTSIDE outputs/.
        for root, dirs, files in os.walk(self.tmp):
            if os.path.realpath(root) == outputs_abs:
                continue
            for f in files:
                if f.endswith(".wav"):
                    self.fail("wav escaped outputs/: {0}".format(
                        os.path.join(root, f)))

    def test_separator_stripped_to_basename(self):
        audio = self._audio()
        rel = audio.save_wav(make_speech(0.1), filename="sub/x.wav")
        # The directory component is DROPPED (basename): the file lands
        # directly inside outputs/ — never in a subdirectory or outside.
        full = os.path.join(self.tmp, rel)
        self.assertTrue(os.path.isfile(full))
        self.assertEqual(os.path.basename(rel), "x.wav")
        self.assertEqual(os.path.dirname(rel), "outputs")

    def test_existing_file_never_silently_overwritten(self):
        audio = self._audio()
        rel = audio.save_wav(make_speech(0.1), filename="part_v01.wav")
        full = os.path.join(self.tmp, rel)
        before = open(full, "rb").read()
        from engine.errors import OutputWriteFailed
        with self.assertRaises(OutputWriteFailed):
            audio.save_wav(make_speech(0.5), filename="part_v01.wav")
        self.assertEqual(open(full, "rb").read(), before,
                         "the previous file must be byte-identical")

    def test_auto_name_still_deduplicates(self):
        audio = self._audio()
        rel1 = audio.save_wav(make_speech(0.1))
        rel2 = audio.save_wav(make_speech(0.1))
        self.assertNotEqual(rel1, rel2)


# ===========================================================================
# 3) MAIN-WINDOW RUNTIME TESTS — the full pipeline through the real
#    MainWindow (Generate Long, review mode, selective regen, combine,
#    export). engine.generate is exercised via the real BatchManager
#    submit chain with the model boundary faked (house pattern).
# ===========================================================================
class _MWHarness(unittest.TestCase):
    """Real MainWindow + Engine with the model boundary faked."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p328_mw_")
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
        # Isolate persistence into the temp root.
        self.win._project_manager = type(
            "PM", (), {"__init__": lambda self: None,
                       "save_project": lambda self, p: None})()
        # A dedicated project + scene.
        from engine.models import Project, Scene
        self.project = Project(name="Eden", id="proj1")
        self.scene = Scene(id="sc1abcd", name="Scene 03",
                           project_id="proj1")
        self.project.add_scene(self.scene)
        self.win._active_project = self.project
        self.win._active_scene = self.scene
        self.win._current_project = "Eden"
        FakeHiggsModel.calls = []
        FakeHiggsModel.runaway = False
        FakeHiggsModel.fail_next = 0
        FakeHiggsModel.speech_s = 1.0

    def tearDown(self):
        try:
            if getattr(self, "win", None) is not None:
                # Close any batch dialog without stopping real playback.
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


class TestGenerateLongProvenance(_MWHarness):
    """§27-1..7 end-to-end: slots, runs, versions, coverage, no overwrite."""

    def test_slots_runs_versions_and_coverage(self):
        parts = [_make_part(block_id="b1", block_label="B1"),
                 _make_part(block_id="b2", block_label="B2"),
                 _make_part(block_id="b3", block_label="B3"),
                 _make_part(block_id="b4", block_label="B4")]
        self.start_long(parts)
        self.wait_batch()
        scene = self.scene
        # Slots materialised exactly once (structure event).
        self.assertEqual(len(scene.expected_audio_slots), 4)
        self.assertEqual([s["slot_id"] for s in scene.expected_audio_slots],
                         ["b1:1", "b2:1", "b3:1", "b4:1"])
        # 4 assets, all tagged with block/part/version/run provenance.
        self.assertEqual(len(scene.audio_assets), 4)
        runs = {a["generation_run"] for a in scene.audio_assets}
        self.assertEqual(len(runs), 1, "one run per batch activation")
        self.assertTrue(runs.pop().startswith("sc1abcd-r"))
        self.assertEqual([a["part_version"] for a in scene.audio_assets],
                         [1, 1, 1, 1])
        self.assertEqual([a["block_id"] for a in scene.audio_assets],
                         ["b1", "b2", "b3", "b4"])
        # Derived coverage: all 4 slots covered → COMPLETE.
        self.assertEqual(scene.status, "complete")
        # Versioned filenames on disk.
        for asset in scene.audio_assets:
            self.assertRegex(os.path.basename(asset["output_path"]),
                             r"_Long_v01_Part_\d{3}_sc1abcd\.wav")

    def test_batch_start_is_generating_never_complete(self):
        """D-2 regression: starting a generation is NEVER completion."""
        from engine.audio_provenance import SCENE_GENERATING
        parts = [_make_part(), _make_part()]
        FakeHiggsModel.speech_s = 3.0  # slow parts → time to observe
        self.start_long(parts)
        try:
            _process(50)
            self.assertEqual(self.scene.status, SCENE_GENERATING)
        finally:
            self.wait_batch()

    def test_no_overwrite_on_rerun(self):
        """D-1 regression: re-running Generate Long never overwrites."""
        parts = [_make_part()]
        self.start_long(parts)
        self.wait_batch()
        v01 = os.path.join(self.tmp, self.scene.audio_assets[0]["output_path"])
        v01_bytes = open(v01, "rb").read()
        # Re-run the same structure: the covered slot allocates v02.
        self.win._batch_manager.stop()
        self.win._batch_dialog = None
        self.start_long([_make_part()])
        # Auto mode generates everything → the part becomes v02.
        self.wait_batch()
        self.assertEqual(len(self.scene.audio_assets), 2)
        self.assertEqual(self.scene.audio_assets[1]["part_version"], 2)
        self.assertEqual(open(v01, "rb").read(), v01_bytes,
                         "the previous run's file must be untouched")
        self.assertNotEqual(self.scene.audio_assets[1]["output_path"],
                            self.scene.audio_assets[0]["output_path"])

    def test_review_mode_no_autostart(self):
        """§27-14: review ON → jobs stay PENDING until Generate pressed."""
        parts = [_make_part(), _make_part()]
        self.start_long(parts, review=True)
        bm = self.win._batch_manager
        self.assertFalse(bm.is_running)
        self.assertEqual(len(bm.jobs), 2)
        self.assertTrue(all(j.status.value == "pending" for j in bm.jobs))
        # The dialog is in scene mode with checkboxes visible.
        dlg = self.dialog()
        self.assertIsNotNone(dlg)
        self.assertIsNotNone(dlg._scene)
        # Pressing Generate (the primary button path) starts the pipeline.
        dlg._on_start()
        self.wait_batch()
        self.assertEqual(len(self.scene.audio_assets), 2)
        self.assertEqual(self.scene.status, "complete")

    def test_review_mode_off_autostarts(self):
        parts = [_make_part()]
        self.start_long(parts, review=False)
        self.assertTrue(self.win._batch_manager.is_running)
        self.wait_batch()
        self.assertEqual(len(self.scene.audio_assets), 1)

    def test_default_selection_not_generated_checked(self):
        """§27-15: only not-generated slots are pre-checked."""
        # First run: cover parts 1 and 2 (part 3 unchecked via selection).
        parts = [_make_part(block_id="b1"), _make_part(block_id="b2"),
                 _make_part(block_id="b3")]
        self.start_long(parts, review=True)
        dlg = self.dialog()
        # Fresh scene: nothing generated → ALL checked by default.
        self.assertEqual(dlg._check_states, {0: True, 1: True, 2: True})
        # Generate only the first two.
        dlg._check_states = {0: True, 1: True, 2: False}
        dlg._on_start()
        self.wait_batch()
        # Re-open in review mode: covered slots UNCHECKED, uncovered CHECKED.
        self.win._batch_dialog = None
        self.start_long(parts, review=True)
        dlg = self.dialog()
        self.assertEqual(dlg._check_states, {0: False, 1: False, 2: True})


class TestPartialRegeneration(_MWHarness):
    """§27-16: partial regeneration with selective checkboxes."""

    def test_b2_b4_regen_to_v02_others_keep_v01(self):
        parts = [_make_part(block_id="b1", block_label="B1"),
                 _make_part(block_id="b2", block_label="B2"),
                 _make_part(block_id="b3", block_label="B3"),
                 _make_part(block_id="b4", block_label="B4")]
        self.start_long(parts)
        self.wait_batch()
        v01_files = {
            a["part_index"]: os.path.join(self.tmp, a["output_path"])
            for a in self.scene.audio_assets}
        v01_bytes = {k: open(v, "rb").read()
                     for k, v in v01_files.items()}
        # Check B2 + B4 for regeneration.
        dlg = self.dialog()
        dlg._check_states = {0: False, 1: True, 2: False, 3: True}
        dlg._on_start()  # GENERATE CHECKED → _on_generate_selected
        self.wait_batch()
        # B2 + B4 now have v02; B1 + B3 keep exactly one v01 asset each.
        by_slot = {}
        for a in self.scene.audio_assets:
            by_slot.setdefault(a["part_index"], []).append(a["part_version"])
        self.assertEqual(by_slot[1], [1])
        self.assertEqual(by_slot[2], [1, 2])
        self.assertEqual(by_slot[3], [1])
        self.assertEqual(by_slot[4], [1, 2])
        # Untouched v01 files are byte-identical.
        for k, data in v01_bytes.items():
            self.assertEqual(open(v01_files[k], "rb").read(), data)
        # The Scene stays COMPLETE (nothing was invalidated).
        self.assertEqual(self.scene.status, "complete")
        # The regen ran under a NEW generation run id.
        runs = {a["generation_run"] for a in self.scene.audio_assets}
        self.assertEqual(len(runs), 2,
                         "selective regen is a new run activation")


class TestSceneRoutingDuringGeneration(_MWHarness):
    """§27-21: switching Scenes mid-batch never moves the output (D-3)."""

    def test_switch_mid_batch_routes_by_scene_id(self):
        from engine.models import Scene
        other = Scene(id="sc2efgh", name="Scene 04", project_id="proj1")
        self.project.add_scene(other)
        parts = [_make_part(), _make_part()]
        FakeHiggsModel.speech_s = 2.0
        self.start_long(parts)
        # Switch the ACTIVE scene mid-batch — the jobs still carry sc1abcd.
        self.win._active_scene = other
        self.wait_batch()
        self.assertEqual(len(self.scene.audio_assets), 2,
                         "assets must land on the generating scene")
        self.assertEqual(len(other.audio_assets), 0,
                         "D-3: the active-at-finish scene must get nothing")
        self.assertEqual(self.scene.status, "complete")


class TestSceneCombine(_MWHarness):
    """§27-9..11: Scene internal combination, lineage, staleness, v02."""

    def test_combine_v01_stale_v02_and_lineage(self):
        parts = [_make_part(block_id="b1", block_label="B1"),
                 _make_part(block_id="b2", block_label="B2")]
        self.start_long(parts)
        self.wait_batch()
        # Combine → v01.
        self.win._on_combine_scene_requested()
        self.assertEqual(len(self.scene.combined_outputs), 1)
        entry = self.scene.combined_outputs[0]
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["slot_count"], 2)
        self.assertIn("Combined_v01", entry["output_path"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, entry["output_path"])))
        # Lineage: one source per slot with full provenance.
        self.assertEqual(len(entry["sources"]), 2)
        self.assertEqual(entry["sources"][0]["slot_id"], "b1:1")
        self.assertEqual(entry["sources"][1]["slot_id"], "b2:1")
        # Fresh: not stale.
        from engine.audio_provenance import (
            is_scene_combined_stale, resolve_scene_output)
        stale, _ = is_scene_combined_stale(
            self.scene, entry, self.win._engine_app_root())
        self.assertFalse(stale)
        # Resolution consumes the combined output.
        res = resolve_scene_output(self.scene, self.tmp)
        self.assertEqual(res["kind"], "scene_combined")
        self.assertEqual(res["label"], "Combined v01")

        # Regenerate B2 → v02 → the combined v01 becomes STALE.
        dlg = self.dialog()
        dlg._check_states = {0: False, 1: True}
        dlg._on_start()
        self.wait_batch()
        stale, reasons = is_scene_combined_stale(
            self.scene, entry, self.win._engine_app_root())
        self.assertTrue(stale)
        self.assertTrue(any("newer version" in r for r in reasons))
        # Automatic resolution: all combined outputs are now stale ->
        # REVIEW REQUIRED (P3.30(g): never silently fall through to a
        # single part asset as the Scene's audio).
        res = resolve_scene_output(self.scene, self.tmp)
        self.assertTrue(res["requires_review"])
        self.assertIsNone(res["kind"])

        # Combine again → v02; v01 file still on disk; both entries exist.
        self.win._on_combine_scene_requested()
        self.assertEqual(len(self.scene.combined_outputs), 2)
        v02 = self.scene.combined_outputs[1]
        self.assertEqual(v02["version"], 2)
        self.assertIn("Combined_v02", v02["output_path"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, entry["output_path"])))
        # v02 sources record the CURRENT versions (B2 = v02).
        b2_source = [s for s in v02["sources"]
                     if s["slot_id"] == "b2:1"][0]
        self.assertEqual(b2_source["part_version"], 2)
        # Resolution now prefers the fresh v02.
        res = resolve_scene_output(self.scene, self.tmp)
        self.assertEqual(res["label"], "Combined v02")

    def test_combine_blocked_when_slot_uncovered(self):
        parts = [_make_part(block_id="b1"), _make_part(block_id="b2")]
        self.start_long(parts, review=True)
        dlg = self.dialog()
        dlg._check_states = {0: True, 1: False}  # only part 1 generated
        dlg._on_start()
        self.wait_batch()
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "warning", return_value=None) as warn:
            self.win._on_combine_scene_requested()
            self.assertTrue(warn.called,
                            "uncovered slot must BLOCK with a warning")
        self.assertEqual(len(self.scene.combined_outputs), 0)

    def test_selected_block_audio_feeds_combine(self):
        parts = [_make_part(block_id="b1")]
        self.start_long(parts)
        self.wait_batch()
        # Regenerate as v02, then explicitly select v01.
        dlg = self.dialog()
        dlg._check_states = {0: True}
        dlg._on_start()
        self.wait_batch()
        v01 = [a for a in self.scene.audio_assets
               if a["part_version"] == 1][0]
        self.win._on_use_version_requested("b1:1", v01["id"])
        self.assertEqual(self.scene.selected_block_audio, {"b1:1": v01["id"]})
        self.win._on_combine_scene_requested()
        entry = self.scene.combined_outputs[0]
        self.assertEqual(entry["sources"][0]["part_version"], 1,
                         "Combine must use the explicitly selected version")


class TestBatchExport(_MWHarness):
    """§27-17: batch export — copy-only, provenance names, no overwrite."""

    def test_export_copies_with_collision_guard(self):
        parts = [_make_part(block_id="b1"), _make_part(block_id="b2")]
        self.start_long(parts)
        self.wait_batch()
        dest = os.path.join(self.tmp, "export_dest")
        os.makedirs(dest, exist_ok=True)
        from unittest.mock import patch
        rel_files = [a["output_path"] for a in self.scene.audio_assets]
        with patch("PySide6.QtWidgets.QFileDialog.getExistingDirectory",
                   return_value=dest), \
             patch("PySide6.QtWidgets.QMessageBox.information",
                   return_value=None):
            self.win._on_batch_export_audio("parts", [])
        copied = sorted(os.listdir(dest))
        self.assertEqual(len(copied), 2)
        for rel in rel_files:
            self.assertIn(os.path.basename(rel), copied)
        # Export again → collisions get " (2)" suffixes, no overwrite.
        with patch("PySide6.QtWidgets.QFileDialog.getExistingDirectory",
                   return_value=dest), \
             patch("PySide6.QtWidgets.QMessageBox.information",
                   return_value=None):
            self.win._on_batch_export_audio("parts", [])
        self.assertEqual(len(os.listdir(dest)), 4)

    def test_export_selected_scope_uses_checked_rows(self):
        parts = [_make_part(block_id="b1"), _make_part(block_id="b2")]
        self.start_long(parts)
        self.wait_batch()
        dest = os.path.join(self.tmp, "export_sel")
        os.makedirs(dest, exist_ok=True)
        from unittest.mock import patch
        with patch("PySide6.QtWidgets.QFileDialog.getExistingDirectory",
                   return_value=dest), \
             patch("PySide6.QtWidgets.QMessageBox.information",
                   return_value=None):
            self.win._on_batch_export_audio("selected", [0])
        self.assertEqual(len(os.listdir(dest)), 1)


class TestProjectExportReimport(_MWHarness):
    """§27-20: export/reimport round-trip preserves provenance."""

    def test_roundtrip_provenance_and_combined(self):
        parts = [_make_part(block_id="b1")]
        self.start_long(parts)
        self.wait_batch()
        self.win._on_combine_scene_requested()
        # Export (v2).
        from engine.project_exporter import ProjectExporter
        dest = os.path.join(self.tmp, "exports")
        os.makedirs(dest, exist_ok=True)
        exporter = ProjectExporter(self.tmp)
        result = exporter.export(self.project, dest, include_audio=True,
                                 include_voice_assets=False)
        self.assertTrue(result.success)
        with open(os.path.join(result.export_dir, "project.json"),
                  encoding="utf-8") as f:
            import json
            data = json.load(f)
        self.assertEqual(data.get("export_version"), 2)
        scene_data = data["scenes"][0]
        self.assertEqual(len(scene_data["expected_audio_slots"]), 1)
        self.assertEqual(len(scene_data["combined_outputs"]), 1)
        self.assertTrue(scene_data["audio_assets"][0]["part_version"], 1)
        # Reimport into a fresh manager rooted at the same tmp app root.
        from engine.project_manager import ProjectManager
        pm = ProjectManager(os.path.join(self.tmp, "projects2"))
        imported = pm.import_project_dir(result.export_dir)
        self.assertIsNotNone(imported)
        scene = imported.scenes[0]
        self.assertEqual(len(scene.expected_audio_slots), 1)
        self.assertEqual(len(scene.audio_assets), 1)
        self.assertEqual(scene.audio_assets[0]["part_version"], 1)
        self.assertEqual(scene.audio_assets[0]["generation_run"],
                         self.scene.audio_assets[0]["generation_run"])
        # Scene combined WAV copied back into outputs/ and resolvable.
        self.assertEqual(len(scene.combined_outputs), 1)
        entry = scene.combined_outputs[0]
        restored = os.path.join(self.tmp, entry["output_path"])
        self.assertTrue(os.path.isfile(restored),
                        "the scene combined WAV must be restored to outputs/")
        from engine.audio_provenance import resolve_scene_output
        res = resolve_scene_output(scene, self.tmp)
        self.assertEqual(res["kind"], "scene_combined")


class TestHistoryProvenance(_MWHarness):
    """§27-19: history provenance for parts + combined outputs."""

    def test_part_entries_carry_full_provenance(self):
        parts = [_make_part(block_id="b7")]
        self.start_long(parts)
        self.wait_batch()
        entries = self.engine._history.list_entries()
        part_entries = [e for e in entries if e.type == "part"]
        self.assertEqual(len(part_entries), 1)
        entry = part_entries[0]
        self.assertEqual(entry.block_id, "b7")
        self.assertEqual(entry.part_index, 1)
        self.assertEqual(entry.part_version, 1)
        self.assertTrue(entry.generation_run.startswith("sc1abcd-r"))

    def test_scene_combined_history_entry(self):
        parts = [_make_part(block_id="b1")]
        self.start_long(parts)
        self.wait_batch()
        self.win._on_combine_scene_requested()
        entries = self.engine._history.list_entries()
        combined = [e for e in entries if e.type == "scene_combined"]
        self.assertEqual(len(combined), 1)
        self.assertTrue(combined[0].source_ids)


class TestConcurrencyAudit(_MWHarness):
    """§25: generation while switching Scene / regen while combined exists."""

    def test_regen_while_combined_exists_preserves_v01(self):
        parts = [_make_part(block_id="b1")]
        self.start_long(parts)
        self.wait_batch()
        self.win._on_combine_scene_requested()
        combined_v01_path = os.path.join(
            self.tmp, self.scene.combined_outputs[0]["output_path"])
        combined_bytes = open(combined_v01_path, "rb").read()
        # Regenerate the part while the combined output exists.
        dlg = self.dialog()
        dlg._check_states = {0: True}
        dlg._on_start()
        self.wait_batch()
        # The combined v01 file is untouched (append-only outputs).
        self.assertEqual(open(combined_v01_path, "rb").read(), combined_bytes)
        self.assertEqual(len(self.scene.combined_outputs), 1)

    def test_covered_scene_stays_complete_after_failed_regen(self):
        parts = [_make_part(block_id="b1")]
        self.start_long(parts)
        self.wait_batch()
        self.assertEqual(self.scene.status, "complete")
        # Force a failure on the regen. (The per-part failure modal is
        # the pre-existing UX — patched out so the offscreen run does
        # not block on it.)
        FakeHiggsModel.fail_next = 1
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        dlg = self.dialog()
        dlg._check_states = {0: True}
        with patch.object(QMessageBox, "critical", return_value=None):
            dlg._on_start()
            self.wait_batch()
        self.assertEqual(self.scene.status, "complete",
                         "a failed regen must NOT degrade a COMPLETE scene")
        # The v01 asset + file survive.
        self.assertEqual(len(self.scene.audio_assets), 1)


class TestP327BVersioningRegression(_MWHarness):
    """§27-25: P3.27B outlier protection retained through provenance."""

    def test_runaway_regenerates_as_new_version_with_warning(self):
        parts = [_make_part(block_id="b1")]
        self.start_long(parts)
        self.wait_batch()
        v01_path = os.path.join(self.tmp,
                                self.scene.audio_assets[0]["output_path"])
        v01_bytes = open(v01_path, "rb").read()
        # Pathological regeneration: model non-termination to the cap.
        FakeHiggsModel.runaway = True
        FakeHiggsModel.speech_s = 2.0
        dlg = self.dialog()
        dlg._check_states = {0: True}
        dlg._on_start()
        self.wait_batch()
        # New asset = v02 with its OWN filename; v01 untouched.
        self.assertEqual(len(self.scene.audio_assets), 2)
        v02 = self.scene.audio_assets[1]
        self.assertEqual(v02["part_version"], 2)
        self.assertNotEqual(v02["output_path"],
                            self.scene.audio_assets[0]["output_path"])
        self.assertEqual(open(v01_path, "rb").read(), v01_bytes)
        # The outlier is flagged and KEPT.
        jobs = self.win._batch_manager.jobs
        self.assertIsNotNone(jobs[0].anomaly)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp,
                                                    v02["output_path"])))
        # History retains the warning.
        entries = [e for e in self.engine._history.list_entries()
                   if e.part_version == 2]
        self.assertTrue(entries and entries[0].output_anomaly)


if __name__ == "__main__":
    unittest.main(verbosity=2)
