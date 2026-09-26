"""
SpeechStudio — P3.44.6 Generation Structure Identity regression tests
======================================================================

The proven defect class (design round, ss/p3446_probe_identity.py):
the authoritative structural identity — the ``slot_id`` that the slot
side AND the BatchJob side already carry ("{block_id}:{part_of_block}"
/ "plain:{part_index}") — was DROPPED on the way to the asset
(``BatchJob.to_request()`` did not forward it; the ONE writer
``register_generation_result`` did not stamp it). Slot membership was
therefore re-derived from the GLOBAL ``part_index`` (queue position),
which is not a stable property of a part:

  FALSE POSITIVE  — changed part decomposition: an old asset of block B
      part 1 (global position 3) was accepted as coverage for a NEW,
      differently-bounded part that merely happened to sit at position 3
      (same block, same position, different part_of_block);
  FALSE NEGATIVE  — reorder / within-block growth: a genuinely valid
      asset of an unchanged slot became unreachable purely because its
      global position shifted (valid audio silently "lost", coverage
      under-reported).

Fix under test (the proven minimal mechanism — directive §1):
``slot_id`` is preserved through the whole modern chain

    expected slot → BatchJob → GenerationRequest → GenerationResult
    → register_generation_result → AudioAsset → slot_of_asset

and ``slot_of_asset`` joins IDENTITY-FIRST: a modern asset belongs to
the expected slot with EXACTLY its recorded slot_id (wherever that slot
now sits); a modern mismatch NEVER downgrades to positional matching.
Legacy assets (no ``slot_id`` — everything recorded before P3.44.6)
keep the documented P3.44.5 positional + block-aware join VERBATIM.

Part versioning is NOT structure identity: same slot + changed
generation settings (temperature / top_k / top_p / seed / prompt) = a
new generation_run / part_version of the SAME slot.

duplicate_job semantics (directive §5, explicitly validated): a
duplicate is an INDEPENDENT manual queue row — it deliberately strips
structural provenance (part/slot/run), so its registered asset joins NO
expected slot and can never masquerade as the original slot.

Detector methodology (P3.44.5 house pattern): tests marked
"fails on old code" fail on the pre-P3.44.6 tree and pass after the
fix; CONTROL tests (valid matching / legacy scope) must stay green on
BOTH (behaviour that must not change).

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_44_6_slot_identity.py -v
"""

from __future__ import annotations

import os
import sys
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
# Fake torch (house pattern: test_p3_28 / test_p3_44_5)
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


# ===========================================================================
# Fixtures (pure functions — no Qt)
# ===========================================================================
class _IdentityFactory(unittest.TestCase):
    """Shared fixtures for the join / resolution tests."""

    SCENE_ID = "ab12cd34ef56"

    def make_scene(self, slots=None, assets=None):
        from engine.models import Scene
        s = Scene(id=self.SCENE_ID, name="Scene 03")
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

    def modern_asset(self, slot_id, part_index, version, block_id,
                     path=None):
        """A P3.44.6 asset: stamped with the authoritative slot identity."""
        return {
            "id": "m{0}v{1}".format(part_index, version),
            "scene_id": self.SCENE_ID,
            "output_path": path or "outputs/p{0}_v{1:02d}.wav".format(
                part_index, version),
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

    def legacy_asset(self, part_index, version, block_id, path=None):
        """A pre-P3.44.6 asset: NO slot identity (documented legacy)."""
        return {
            "id": "l{0}v{1}".format(part_index, version),
            "scene_id": self.SCENE_ID,
            "output_path": path or "outputs/q{0}_v{1:02d}.wav".format(
                part_index, version),
            "duration": 10.0,
            "speaker": None, "character_id": None,
            "voice_profile_id": None,
            "generated_at": "20260101_120000",
            "block_id": block_id,
            "part_index": part_index,
            "part_version": version,
            "generation_run": "{0}-r001".format(self.SCENE_ID[:8]),
        }

    def structure(self, parts):
        """Materialise expected slots from SplitPart stand-ins."""
        from engine.audio_provenance import materialize_expected_slots
        return materialize_expected_slots(parts)


# ===========================================================================
# §18 Slot identity 1–6 (+ 22) — the identity-first join matrix
# ===========================================================================
class TestIdentityFirstJoin(_IdentityFactory):
    """The modern join: slot_id is THE structural identity.

    Tests 2, 4, 5, 6 and 22 fail on the pre-P3.44.6 code (the asset
    slot_id was ignored / never stamped); 1 and 3 pass on both where the
    positional coincidence agrees — the identity rule must not lose
    valid matching.
    """

    # --- 1: same slot_id → valid match -------------------------------
    def test_01_same_slot_id_valid_match(self):
        """§18/1: a modern asset joins the slot with its exact slot_id,
        and coverage/versions follow (CONTROL for the valid direction)."""
        from engine.audio_provenance import (
            slot_of_asset, is_slot_covered, assets_for_slot)
        scene = self.make_scene(slots=[
            self.slot("blkA:1", "blkA", 1),
            self.slot("blkA:2", "blkA", 2),
        ])
        a = self.modern_asset("blkA:2", 2, 1, "blkA")
        scene.audio_assets = [a]
        self.assertEqual(slot_of_asset(a, scene), "blkA:2")
        self.assertTrue(is_slot_covered(scene, "blkA:2"))
        self.assertEqual(assets_for_slot(scene, "blkA:2"), [a])

    # --- 2: different slot_id + same part_index → invalid ------------
    def test_02_different_slot_id_same_part_index_invalid(self):
        """§18/2 (fails on old code): the OLD structure's slot blkA:2
        asset (position 2) vs the NEW structure where position 2 is a
        DIFFERENT slot — identity mismatch must reject even though the
        position lines up AND the block is the same (see test_04 for the
        full decomposition case; here a renamed/second block occupies
        the position)."""
        from engine.audio_provenance import slot_of_asset
        scene = self.make_scene(slots=[
            self.slot("blkA:1", "blkA", 1),
            self.slot("blkX:1", "blkX", 2),
        ])
        old = self.modern_asset("blkA:2", 2, 1, "blkA")
        self.assertIsNone(
            slot_of_asset(old, scene),
            "a modern asset whose slot_id matches no expected slot must "
            "join nothing — never the slot that merely sits at the same "
            "global position")

    # --- 3: different slot_id + same block_id → invalid if slot differs
    def test_03_different_slot_id_same_block_id_invalid(self):
        """§18/3: same block, different part_of_block ⇒ different slot.
        Directive §13's exact shape: current slot BlockB:2, old asset
        slot_id=BlockA:2 → MUST NOT match; and old BlockB:1 audio must
        not match BlockB:2 either."""
        from engine.audio_provenance import slot_of_asset
        scene = self.make_scene(slots=[
            self.slot("blkB:1", "blkB", 1, part_of_block=1),
            self.slot("blkB:2", "blkB", 2, part_of_block=2),
        ])
        other_block = self.modern_asset("blkA:2", 2, 1, "blkA")
        self.assertIsNone(slot_of_asset(other_block, scene),
                          "BlockA:2 asset must not match BlockB:2")
        same_block_other_part = self.modern_asset("blkB:1", 2, 1, "blkB")
        self.assertEqual(
            slot_of_asset(same_block_other_part, scene), "blkB:1",
            "same block + different part belongs to ITS OWN slot, not "
            "the slot at the asset's old global position")

    # --- 4: changed part decomposition → no false positive -----------
    def test_04_changed_decomposition_no_false_positive(self):
        """§18/4 + directive §15 S1→S2 residual (FAILS ON OLD CODE):

        S1: Block A: [A1, A2], Block B: [B1]
            slots: blkA:1(pos1) blkA:2(pos2) blkB:1(pos3)
        S2: Block A: [A1],     Block B: [B1, B2]
            slots: blkA:1(pos1) blkB:1(pos2) blkB:2(pos3)

        The old B1 asset (slot blkB:1, global position 3, block blkB)
        was FALSE-POSITIVELY joined to the new slot blkB:2 (same block,
        same position, DIFFERENT part_of_block) by the positional join.
        Identity-first: it joins its TRUE slot blkB:1; blkB:2 stays
        uncovered; Combine blocks on it instead of assembling old audio
        under the new structure."""
        import soundfile as sf
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp(prefix="ss_p3446_s1s2_")
        try:
            def wav(name):
                path = os.path.join(tmp, name)
                sf.write(path, make_speech(0.5), SAMPLE_RATE)
                return os.path.relpath(path, tmp)

            from engine.audio_provenance import (
                slot_of_asset, is_slot_covered, coverage_counts,
                assets_for_slot, resolved_slot_sources)
            s2 = self.structure([
                _make_part(block_id="blkA", part_of_block=1,
                           block_label="A"),
                _make_part(block_id="blkB", part_of_block=1,
                           block_label="B"),
                _make_part(block_id="blkB", part_of_block=2,
                           block_label="B"),
            ])
            old_assets = [
                self.modern_asset("blkA:1", 1, 1, "blkA",
                                  path=wav("old_a1.wav")),
                self.modern_asset("blkA:2", 2, 1, "blkA",
                                  path=wav("old_a2.wav")),
                self.modern_asset("blkB:1", 3, 1, "blkB",
                                  path=wav("old_b1.wav")),
            ]
            scene = self.make_scene(slots=s2, assets=old_assets)
            # The old B1 joins ITS OWN slot — not the new differently-
            # bounded part that inherited its position.
            self.assertEqual(slot_of_asset(old_assets[2], scene),
                             "blkB:1")
            self.assertFalse(is_slot_covered(scene, "blkB:2"),
                             "old B1 audio must not cover the NEW part "
                             "B2")
            # Old A2 (part vanished from A) joins nothing.
            self.assertIsNone(slot_of_asset(old_assets[1], scene))
            self.assertEqual(coverage_counts(scene), (2, 3))
            # Combine: the new part must be a BLOCKER, never silently
            # assembled from old audio.
            sources, blockers = resolved_slot_sources(scene, tmp)
            slot_ids = [s["slot_id"] for s in sources]
            self.assertIn("blkA:1", slot_ids)
            self.assertIn("blkB:1", slot_ids)
            self.assertNotIn("blkB:2", slot_ids)
            self.assertEqual(len(blockers), 1,
                             "exactly the uncovered new slot blocks")
            self.assertIn("no generated audio", blockers[0])
            self.assertEqual(assets_for_slot(scene, "blkB:2"), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # --- 5: reorder does not lose valid same-slot results ------------
    def test_05_reorder_keeps_valid_results(self):
        """§18/5 (FAILS ON OLD CODE): block order swapped — the
        unchanged slot blkA:1 shifts from global position 1 to 2. The
        positional join lost its genuinely valid audio (coverage 1/2 →
        0/2 for that slot); identity-first keeps it covered."""
        from engine.audio_provenance import (
            is_slot_covered, coverage_counts, latest_asset_for_slot)
        old = self.structure([
            _make_part(block_id="blkA", part_of_block=1, block_label="A"),
            _make_part(block_id="blkB", part_of_block=1, block_label="B"),
        ])
        new = self.structure([
            _make_part(block_id="blkB", part_of_block=1, block_label="B"),
            _make_part(block_id="blkA", part_of_block=1, block_label="A"),
        ])
        a1 = self.modern_asset("blkA:1", 1, 1, "blkA")
        b1 = self.modern_asset("blkB:1", 2, 1, "blkB")
        scene = self.make_scene(slots=new, assets=[a1, b1])
        self.assertTrue(is_slot_covered(scene, "blkA:1"),
                        "a genuinely valid same-slot asset must survive "
                        "a reorder (old code: false negative)")
        self.assertTrue(is_slot_covered(scene, "blkB:1"))
        self.assertEqual(coverage_counts(scene), (2, 2))
        self.assertIs(latest_asset_for_slot(scene, "blkA:1"), a1)
        self.assertIs(latest_asset_for_slot(scene, "blkB:1"), b1)
        _ = old  # (structure derivation documented above)

    # --- 6: growth does not create positional false matches ---------
    def test_06_growth_no_positional_false_match_and_no_loss(self):
        """§18/6 (FAILS ON OLD CODE for the loss half): an earlier block
        grows a second part, shifting the unchanged blkZ:1 from position
        2 to 3. Old code lost its valid audio (position 2 became the new
        blkA:2 slot); identity-first keeps it. The NEW slot blkA:2 must
        NOT be covered by the shifted asset (no positional false match
        in either direction)."""
        from engine.audio_provenance import (
            is_slot_covered, coverage_counts, slot_of_asset)
        new = self.structure([
            _make_part(block_id="blkA", part_of_block=1, block_label="A"),
            _make_part(block_id="blkA", part_of_block=2, block_label="A"),
            _make_part(block_id="blkZ", part_of_block=1, block_label="Z"),
        ])
        z1 = self.modern_asset("blkZ:1", 2, 1, "blkZ")
        scene = self.make_scene(slots=new, assets=[z1])
        self.assertEqual(slot_of_asset(z1, scene), "blkZ:1")
        self.assertTrue(is_slot_covered(scene, "blkZ:1"),
                        "the unchanged slot keeps its audio after growth")
        self.assertFalse(is_slot_covered(scene, "blkA:1"))
        self.assertFalse(is_slot_covered(scene, "blkA:2"),
                         "the new slot must not inherit the shifted "
                         "asset positionally")
        self.assertEqual(coverage_counts(scene), (1, 3))

    # --- 22: modern mismatch never falls into the legacy fallback ----
    def test_22_modern_mismatch_no_legacy_positional_fallback(self):
        """§18/22 (FAILS ON OLD CODE): a MODERN asset (slot_id present)
        whose identity matches no current slot must NOT be downgraded
        to the legacy positional join. Sharpest shape: the asset's
        block_id MATCHES the slot sitting at its global position — only
        the part_of_block differs (nb2:1 audio vs the nb2:2 slot). Old
        code accepted it positionally (same block, same position);
        identity-first rejects it: the asset IS slot nb2:1, which no
        longer exists in this structure."""
        from engine.audio_provenance import (
            slot_of_asset, is_slot_covered, assets_for_slot)
        scene = self.make_scene(slots=[
            self.slot("nb1:1", "nb1", 1),
            self.slot("nb2:2", "nb2", 2, part_of_block=2),
        ])
        old = self.modern_asset("nb2:1", 2, 1, "nb2")
        self.assertIsNone(
            slot_of_asset(old, scene),
            "modern asset + identity mismatch ⇒ None; the P3.44.5 "
            "positional+block fallback is reserved for assets with NO "
            "recorded slot identity")
        self.assertFalse(is_slot_covered(scene, "nb2:2"))
        self.assertEqual(assets_for_slot(scene, "nb2:2"), [])
        # The same guard for a genuinely foreign block:
        other_block = self.modern_asset("ob2:1", 2, 1, "ob2")
        self.assertIsNone(slot_of_asset(other_block, scene))

    def test_modern_asset_identity_follows_slot_not_position(self):
        """Position independence, directly: the SAME asset dict joined
        against two structures that place its slot at different global
        positions resolves to the same slot_id in both."""
        from engine.audio_provenance import slot_of_asset
        a = self.modern_asset("blkB:2", 4, 1, "blkB")
        front = self.make_scene(slots=[
            self.slot("blkB:2", "blkB", 1, part_of_block=2),
        ])
        back = self.make_scene(slots=[
            self.slot("blkA:1", "blkA", 1),
            self.slot("blkB:1", "blkB", 2, part_of_block=1),
            self.slot("blkB:2", "blkB", 3, part_of_block=2),
        ])
        self.assertEqual(slot_of_asset(a, front), "blkB:2")
        self.assertEqual(slot_of_asset(a, back), "blkB:2")

    def test_modern_asset_vanished_slot_is_none(self):
        """A modern asset whose slot disappeared entirely (structure
        shrank / part merged away) belongs to no current slot."""
        from engine.audio_provenance import slot_of_asset
        scene = self.make_scene(slots=[self.slot("blkA:1", "blkA", 1)])
        old = self.modern_asset("blkA:3", 3, 1, "blkA")
        self.assertIsNone(slot_of_asset(old, scene))

    def test_modern_self_describing_identity_without_scene(self):
        """Legacy scope call (scene=None) on a modern asset returns the
        recorded slot_id verbatim — never a positional synthesis of an
        identity the asset already carries."""
        from engine.audio_provenance import slot_of_asset
        a = self.modern_asset("blkB:2", 7, 1, "blkB")
        self.assertEqual(slot_of_asset(a, None), "blkB:2")

    def test_plain_modern_asset_joins_by_identity(self):
        """Plain (speaker-split) modern assets join their plain slot by
        identity wherever it sits — the plain positional model's known
        fragility is closed the same way."""
        from engine.audio_provenance import slot_of_asset, is_slot_covered
        scene = self.make_scene(slots=[
            self.slot("plain:1", None, 1),
            self.slot("plain:2", None, 2),
        ])
        p2 = self.modern_asset("plain:2", 2, 1, None)
        scene.audio_assets = [p2]
        self.assertEqual(slot_of_asset(p2, scene), "plain:2")
        self.assertTrue(is_slot_covered(scene, "plain:2"))
        self.assertFalse(is_slot_covered(scene, "plain:1"))
        # The asset of a REMOVED plain slot (re-split changed the count)
        # joins nothing even though a plain slot still sits at its
        # position — it is a different decomposition.
        p_old = self.modern_asset("plain:3", 3, 1, None)
        self.assertIsNone(slot_of_asset(p_old, scene))


# ===========================================================================
# §18 Legacy 21 — the documented compatibility path (both-green controls)
# ===========================================================================
class TestLegacyCompatibility(_IdentityFactory):
    """Legacy assets (no slot_id) keep the P3.44.5 join VERBATIM."""

    def test_21_legacy_valid_match_retained(self):
        """§18/21: a legitimate legacy asset (same block, same position)
        still resolves through the P3.44.5 positional + block-aware
        path — identical behaviour to the P3.44.5 suite's VALID case."""
        from engine.audio_provenance import (
            slot_of_asset, is_slot_covered, latest_asset_for_slot)
        scene = self.make_scene(slots=[
            self.slot("b1:1", "b1", 1),
            self.slot("b2:1", "b2", 2),
        ])
        l1 = self.legacy_asset(1, 1, "b1")
        l2 = self.legacy_asset(2, 2, "b2")
        scene.audio_assets = [l1, l2]
        self.assertEqual(slot_of_asset(l1, scene), "b1:1")
        self.assertEqual(slot_of_asset(l2, scene), "b2:1")
        self.assertTrue(is_slot_covered(scene, "b2:1"))
        self.assertIs(latest_asset_for_slot(scene, "b2:1"), l2)

    def test_legacy_block_mismatch_still_rejected(self):
        """P3.44.5 §8 INVALID case unchanged: legacy asset whose
        block_id differs from the slot's block at the same position."""
        from engine.audio_provenance import slot_of_asset
        scene = self.make_scene(slots=[
            self.slot("nb1:1", "nb1", 1),
            self.slot("nb2:1", "nb2", 2),
        ])
        old = self.legacy_asset(2, 1, "ob2")
        self.assertIsNone(slot_of_asset(old, scene))

    def test_legacy_shrunk_position_still_none(self):
        from engine.audio_provenance import slot_of_asset
        scene = self.make_scene(slots=[self.slot("nb1:1", "nb1", 1)])
        old = self.legacy_asset(3, 1, "ob3")
        self.assertIsNone(slot_of_asset(old, scene))

    def test_legacy_kind_mismatch_still_rejected(self):
        from engine.audio_provenance import slot_of_asset
        scene = self.make_scene(slots=[self.slot("plain:1", None, 1)])
        block_asset = self.legacy_asset(1, 1, "ob1")
        self.assertIsNone(slot_of_asset(block_asset, scene))
        scene2 = self.make_scene(slots=[self.slot("nb1:1", "nb1", 1)])
        plain_asset = self.legacy_asset(1, 1, None)
        self.assertIsNone(slot_of_asset(plain_asset, scene2))

    def test_legacy_scope_synthesis_unchanged(self):
        """scene=None / unslotted scenes: the documented legacy
        synthesis (block → {block_id}:{part_index}, plain →
        plain:{part_index}) is preserved verbatim."""
        from engine.audio_provenance import slot_of_asset
        block_asset = self.legacy_asset(2, 1, "ob2")
        plain_asset = self.legacy_asset(3, 1, None)
        self.assertEqual(slot_of_asset(block_asset, None), "ob2:2")
        self.assertEqual(slot_of_asset(plain_asset, None), "plain:3")
        unslotted = self.make_scene(slots=[], assets=None)
        self.assertEqual(slot_of_asset(block_asset, unslotted), "ob2:2")
        self.assertEqual(slot_of_asset(plain_asset, unslotted), "plain:3")

    def test_legacy_full_scene_asset_still_not_a_slot(self):
        from engine.audio_provenance import slot_of_asset
        full_scene = {
            "id": "fs1", "scene_id": self.SCENE_ID,
            "output_path": "outputs/full.wav", "duration": 10.0,
        }
        scene = self.make_scene(slots=[self.slot("b1:1", "b1", 1)])
        self.assertIsNone(slot_of_asset(full_scene, scene))
        self.assertIsNone(slot_of_asset(full_scene, None))

    def test_mixed_stream_legacy_and_modern(self):
        """A realistic migration-mixed stream: legacy assets resolve via
        the P3.44.5 path, modern assets via identity — in the same
        Scene, without interference."""
        from engine.audio_provenance import (
            slot_of_asset, coverage_counts, assets_for_slot)
        scene = self.make_scene(slots=[
            self.slot("b1:1", "b1", 1),
            self.slot("b2:1", "b2", 2),
            self.slot("b2:2", "b2", 3, part_of_block=2),
        ])
        legacy_ok = self.legacy_asset(1, 1, "b1")        # valid legacy
        legacy_moved = self.legacy_asset(3, 1, "b1")     # stale legacy
        modern_ok = self.modern_asset("b2:2", 3, 1, "b2")
        scene.audio_assets = [legacy_ok, legacy_moved, modern_ok]
        self.assertEqual(slot_of_asset(legacy_ok, scene), "b1:1")
        # Stale legacy at position 3: P3.44.5 rule rejects (block b1 ≠ b2)
        self.assertIsNone(slot_of_asset(legacy_moved, scene))
        self.assertEqual(slot_of_asset(modern_ok, scene), "b2:2")
        self.assertEqual(coverage_counts(scene), (2, 3))
        self.assertEqual(len(assets_for_slot(scene, "b2:2")), 1)


# ===========================================================================
# §18 Propagation 7 — the REAL chain (model-boundary fake only)
# ===========================================================================
class TestSlotIdPropagationRealChain(unittest.TestCase):
    """slot_id survives: expected slot → BatchJob → to_request →
    GenerationRequest → REAL Engine._execute_generation →
    GenerationResult → register_generation_result → AudioAsset →
    slot_of_asset. The engine pipeline, the ONE writer and the join are
    the REAL production functions; only the model call is faked
    (FakeHiggsModel, P3.27B house pattern).

    FAILS ON OLD CODE at every propagation assertion (the request
    dropped slot_id; the writer never stamped it).
    """

    def setUp(self):
        from unittest.mock import patch, PropertyMock
        from engine.engine import Engine
        from engine.models import Project, Scene
        self.tmp = tempfile.mkdtemp(prefix="ss_p3446_prop_")
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
        self.project = Project(name="Eden", id="proj1")
        self.scene = Scene(id="ab12cd34ef56", name="Scene 03",
                           project_id="proj1")
        self.project.add_scene(self.scene)
        FakeHiggsModel.calls = []

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _structure(self):
        from engine.audio_provenance import materialize_expected_slots
        parts = [
            _make_part(block_id="blkA", part_of_block=1, block_label="A"),
            _make_part(block_id="blkA", part_of_block=2, block_label="A"),
            _make_part(block_id="blkB", part_of_block=1, block_label="B"),
        ]
        self.scene.expected_audio_slots = materialize_expected_slots(parts)
        return self.scene.expected_audio_slots

    def _job(self, slot, part_index, version, run, **param_overrides):
        from engine.batch_manager import BatchJob
        from engine.models import GenerationParameters
        params = dict(temperature=0.95, top_p=0.95, top_k=300,
                      max_new_tokens=4096, seed=None, append_silence=0.5,
                      normalize_output=False, auto_play=False)
        params.update(param_overrides)
        return BatchJob(
            name="Part {0}".format(part_index),
            prompt="A short test sentence for part {0}.".format(part_index),
            voice_id=None,
            parameters=GenerationParameters(**params),
            output_filename=None,
            project="Eden",
            speaker=None,
            scene_id=self.scene.id,
            scene_name="Scene 03",
            part_index=part_index,
            part_version=version,
            source_block_id=slot["block_id"],
            generation_run=run,
            slot_id=slot["slot_id"],
        )

    def test_07_full_chain_expected_slot_to_join(self):
        """§18/7: every stage of the identity path carries the SAME
        authoritative slot_id — including through the REAL engine
        pipeline — and the registered asset joins its slot by identity."""
        from engine.audio_provenance import (
            register_generation_result, slot_of_asset, is_slot_covered)
        slots = self._structure()
        job = self._job(slots[1], part_index=2, version=1,
                        run="ab12cd34-r001")

        # expected slot → BatchJob
        self.assertEqual(job.slot_id, "blkA:2")
        # BatchJob → GenerationRequest
        request = job.to_request()
        self.assertEqual(request.slot_id, "blkA:2")
        self.assertEqual(request.block_id, "blkA")
        # GenerationRequest → GenerationResult (REAL engine pipeline)
        future = self.engine.generate(request)
        result = future.result(timeout=60)
        self.assertTrue(result.success, result.errors)
        self.assertEqual(result.slot_id, "blkA:2",
                         "the engine passthrough must carry the slot "
                         "identity onto the result")
        self.assertEqual(result.block_id, "blkA")
        # Result → AudioAsset (the ONE writer)
        asset = register_generation_result(self.project, result)
        self.assertIsNotNone(asset)
        self.assertEqual(asset["slot_id"], "blkA:2",
                         "the ONE writer must stamp the authoritative "
                         "slot identity onto the asset")
        # AudioAsset → slot_of_asset (the join)
        self.assertEqual(slot_of_asset(asset, self.scene), "blkA:2")
        self.assertTrue(is_slot_covered(self.scene, "blkA:2"))

    def test_chain_output_is_a_real_playable_file(self):
        """§18/13 (playback substrate): the propagated chain produces a
        REAL WAV on disk at the asset/job output path — the data source
        for Batch row playback, History→WavePlayer and resolved-asset
        playback. Behaviour unchanged by the identity propagation."""
        import soundfile as sf
        from engine.audio_provenance import (
            register_generation_result, resolved_asset_for_slot)
        slots = self._structure()
        job = self._job(slots[0], part_index=1, version=1,
                        run="ab12cd34-r001")
        request = job.to_request()
        result = self.engine.generate(request).result(timeout=60)
        self.assertTrue(result.success, result.errors)
        asset = register_generation_result(self.project, result)
        abs_path = os.path.join(self.tmp, asset["output_path"])
        self.assertTrue(os.path.isfile(abs_path),
                        "the registered asset must point at a real file")
        samples, sr = sf.read(abs_path, dtype="float32")
        self.assertEqual(sr, SAMPLE_RATE)
        self.assertGreater(len(samples), 0)
        # The batch row's playback source (job.output_path set on
        # completion by the manager) is the same file.
        resolved = resolved_asset_for_slot(self.scene, "blkA:1")
        self.assertEqual(resolved["id"], asset["id"])


# ===========================================================================
# §18 Versioning 8–10 — settings/version ≠ structure identity
# ===========================================================================
class TestVersioningAndSettings(_IdentityFactory):
    """slot_id is the STRUCTURAL identity; generation settings and part
    versions are layered on top of it, never replacing it."""

    def _register(self, scene, slot, part_index, version, run,
                  temperature=0.95):
        from types import SimpleNamespace
        from engine.models import GenerationParameters
        from engine.audio_provenance import register_generation_result
        from engine.models import Project
        project = Project(name="Eden", id="proj1")
        project.add_scene(scene)
        result = SimpleNamespace(
            scene_id=scene.id,
            output_path="outputs/p{0}_v{1:02d}.wav".format(
                part_index, version),
            output_duration=2.0, speaker=None, character_id=None,
            voice_profile=None, timestamp="20260101_120000",
            block_id=slot["block_id"], part_index=part_index,
            part_version=version, generation_run=run,
            slot_id=slot["slot_id"],
            parameters=GenerationParameters(temperature=temperature),
        )
        return register_generation_result(project, result)

    def test_08_new_settings_same_structural_identity(self):
        """§18/8: same slot_id + different generation settings
        (temperature / top_k / top_p / seed / prompt) → the SAME
        structural identity; new attempts are new versions/runs of the
        SAME slot, never a new slot."""
        from engine.audio_provenance import (
            coverage_counts, assets_for_slot, slot_of_asset)
        slots = self.structure([
            _make_part(block_id="blkA", part_of_block=1, block_label="A"),
            _make_part(block_id="blkB", part_of_block=1, block_label="B"),
        ])
        scene = self.make_scene(slots=slots)
        # v1 @ temperature 0.95, v2 @ temperature 1.4, v3 @ temperature
        # 0.7 + different run — all the SAME structural slot blkA:1.
        self._register(scene, slots[0], 1, 1, "ab12cd34-r001",
                       temperature=0.95)
        self._register(scene, slots[0], 1, 2, "ab12cd34-r002",
                       temperature=1.4)
        self._register(scene, slots[0], 1, 3, "ab12cd34-r003",
                       temperature=0.7)
        self.assertEqual(coverage_counts(scene), (1, 2))
        assets = assets_for_slot(scene, "blkA:1")
        self.assertEqual(len(assets), 3)
        for asset in assets:
            self.assertEqual(slot_of_asset(asset, scene), "blkA:1")
            self.assertEqual(asset["slot_id"], "blkA:1")
        self.assertEqual([a["part_version"] for a in assets], [1, 2, 3])
        self.assertEqual([a["generation_run"] for a in assets],
                         ["ab12cd34-r001", "ab12cd34-r002",
                          "ab12cd34-r003"])

    def test_09_new_part_version_valid_lineage(self):
        """§18/9: same slot_id + new part_version = valid version
        lineage; next_slot_version allocates max+1 over the slot's OWN
        assets (identity-scoped, position-independent)."""
        from engine.audio_provenance import (
            next_slot_version, assets_for_slot, slot_versions)
        slots = self.structure([
            _make_part(block_id="blkA", part_of_block=1, block_label="A"),
            _make_part(block_id="blkB", part_of_block=1, block_label="B"),
        ])
        scene = self.make_scene(slots=slots)
        self._register(scene, slots[0], 1, 1, "ab12cd34-r001")
        self._register(scene, slots[0], 1, 2, "ab12cd34-r002")
        # The allocator sees only the slot's OWN assets:
        _filename, version = next_slot_version(
            scene, "blkA:1", self.tmp_out() if hasattr(self, "tmp_out")
            else "/tmp", project="Eden", scene_name="Scene 03",
            part_index=1)
        self.assertEqual(version, 3)
        lineage = slot_versions(scene, "blkA:1")
        self.assertEqual([v["part_version"] for v in lineage], [1, 2])

    def test_10_multiple_versions_separately_selectable(self):
        """§18/10: multiple versions of the same slot remain separately
        playable/selectable — explicit selection wins, clearing it
        returns to latest, and each version keeps its own file."""
        from engine.audio_provenance import (
            resolved_asset_for_slot, latest_asset_for_slot,
            slot_versions)
        slots = self.structure([
            _make_part(block_id="blkA", part_of_block=1, block_label="A"),
        ])
        scene = self.make_scene(slots=slots)
        v1 = self._register(scene, slots[0], 1, 1, "ab12cd34-r001")
        v2 = self._register(scene, slots[0], 1, 2, "ab12cd34-r002")
        v3 = self._register(scene, slots[0], 1, 3, "ab12cd34-r003")
        self.assertEqual(
            [a["output_path"] for a in (v1, v2, v3)],
            ["outputs/p1_v01.wav", "outputs/p1_v02.wav",
             "outputs/p1_v03.wav"])
        # Default: latest.
        self.assertIs(latest_asset_for_slot(scene, "blkA:1"), v3)
        self.assertIs(resolved_asset_for_slot(scene, "blkA:1"), v3)
        # Explicit selection of the middle version:
        scene.selected_block_audio["blkA:1"] = v2["id"]
        self.assertIs(resolved_asset_for_slot(scene, "blkA:1"), v2)
        # Selection survives in the version list (all three remain).
        self.assertEqual(
            len(slot_versions(scene, "blkA:1")), 3)
        # Clearing the selection returns to latest (nothing destroyed).
        scene.selected_block_audio.pop("blkA:1", None)
        self.assertIs(resolved_asset_for_slot(scene, "blkA:1"), v3)

    def test_regeneration_after_position_shift_keeps_lineage(self):
        """Directive §14: after a reorder, the shifted-but-valid slot
        keeps its version history (identity-scoped allocation) instead
        of silently restarting at v01 positionally."""
        from engine.audio_provenance import next_slot_version
        old = self.structure([
            _make_part(block_id="blkA", part_of_block=1, block_label="A"),
            _make_part(block_id="blkB", part_of_block=1, block_label="B"),
        ])
        scene = self.make_scene(slots=old)
        self._register(scene, old[0], 1, 1, "ab12cd34-r001")
        self._register(scene, old[0], 1, 2, "ab12cd34-r002")
        # Structure reordered: blkA:1 now sits at global position 2.
        new = self.structure([
            _make_part(block_id="blkB", part_of_block=1, block_label="B"),
            _make_part(block_id="blkA", part_of_block=1, block_label="A"),
        ])
        scene.expected_audio_slots = new
        _f, version = next_slot_version(
            scene, "blkA:1", "/tmp", project="Eden",
            scene_name="Scene 03", part_index=2)
        self.assertEqual(
            version, 3,
            "the slot's OWN version lineage (2 assets ⇒ v03) must "
            "survive the position shift")


# ===========================================================================
# §18 Batch 11–14 — the workspace model preserved
# ===========================================================================
class TestBatchWorkspaceCompatibility(unittest.TestCase):
    """Prompt edit / per-part regeneration / duplicate semantics —
    the Batch remains a fully editable generation workspace; queue
    identity never replaces structure identity."""

    def setUp(self):
        from engine.batch_manager import BatchManager, BatchJob
        self.BatchJob = BatchJob
        self.bm = BatchManager(submit_fn=lambda r: None,
                               marshal_to_ui=lambda fn: fn())

    def _part_job(self, slot_id="blkA:2", part_index=2, version=1,
                  run="ab12cd34-r001", block_id="blkA", prompt=None):
        return self.BatchJob(
            name="Part {0}".format(part_index),
            prompt=prompt or "A short test sentence for part {0}.".format(
                part_index),
            voice_id=None, project="Eden", speaker=None,
            scene_id="ab12cd34ef56", scene_name="Scene 03",
            part_index=part_index, part_version=version,
            source_block_id=block_id, generation_run=run,
            slot_id=slot_id)

    def test_11_prompt_edit_preserves_structure_identity(self):
        """§18/11: the Batch edit flow (JobEditDialog's dict round-trip
        — batch_generation._on_edit) preserves the job's slot identity
        while the prompt text changes; the regenerated request still
        carries the SAME slot_id (same slot, new attempt)."""
        job = self._part_job(prompt="Original prompt text.")
        clone = self.BatchJob.from_dict(job.to_dict())
        clone.prompt = "Edited prompt text with different content."
        request = clone.to_request()
        self.assertEqual(request.slot_id, "blkA:2",
                         "structure identity survives a prompt edit")
        self.assertNotEqual(request.text, job.prompt)
        # Queue YAML round-trip (save_to_file/load_from_file contract):
        # slot_id is persisted with the queue.
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        try:
            self.bm.add_job(clone)
            self.bm.save_to_file(path)
            self.bm.clear_all()
            self.bm.load_from_file(path)
            loaded = self.bm.jobs[0]
            self.assertEqual(loaded.slot_id, "blkA:2")
            self.assertEqual(loaded.prompt,
                             "Edited prompt text with different content.")
            self.assertEqual(loaded.to_request().slot_id, "blkA:2")
        finally:
            os.unlink(path)

    def test_12_per_part_regeneration_preserves_identity(self):
        """§18/12: per-part regeneration semantics — the SAME slot_id,
        a NEW part_version allocated over the slot's own lineage, and a
        fresh generation_run (a new ATTEMPT, never a new structure)."""
        from engine.audio_provenance import (
            materialize_expected_slots, next_slot_version,
            allocate_generation_run)
        from engine.models import Project, Scene
        from types import SimpleNamespace
        from engine.audio_provenance import register_generation_result
        project = Project(name="Eden", id="proj1")
        scene = Scene(id="ab12cd34ef56", name="Scene 03",
                      project_id="proj1")
        project.add_scene(scene)
        slots = materialize_expected_slots([
            _make_part(block_id="blkA", part_of_block=2, block_label="A"),
        ])
        scene.expected_audio_slots = slots
        job = self._part_job()
        # First generation (v1):
        result = SimpleNamespace(
            scene_id=scene.id, output_path="outputs/p2_v01.wav",
            output_duration=2.0, speaker=None, character_id=None,
            voice_profile=None, timestamp="20260101_120000",
            block_id="blkA", part_index=2, part_version=1,
            generation_run="ab12cd34-r001", slot_id=job.slot_id)
        register_generation_result(project, result)
        # Regeneration (the _on_batch_regen contract): new version from
        # the slot's own assets + a fresh run id:
        _f, version = next_slot_version(
            scene, job.slot_id, "/tmp", project="Eden",
            scene_name="Scene 03", part_index=2)
        run = allocate_generation_run(scene)
        self.assertEqual(version, 2)
        self.assertEqual(run, "ab12cd34-r002")
        job.part_version = version
        job.generation_run = run
        request = job.to_request()
        self.assertEqual(request.slot_id, "blkA:2",
                         "regeneration keeps the structural identity")
        self.assertEqual(request.part_version, 2)
        self.assertEqual(request.generation_run, "ab12cd34-r002")
        # Second registration appends as v2 of the SAME slot:
        result2 = SimpleNamespace(
            scene_id=scene.id, output_path="outputs/p2_v02.wav",
            output_duration=2.1, speaker=None, character_id=None,
            voice_profile=None, timestamp="20260101_120100",
            block_id="blkA", part_index=2, part_version=2,
            generation_run=run, slot_id=job.slot_id)
        register_generation_result(project, result2)
        from engine.audio_provenance import assets_for_slot
        assets = assets_for_slot(scene, "blkA:2")
        self.assertEqual([a["part_version"] for a in assets], [1, 2])
        self.assertEqual(len(scene.audio_assets), 2,
                         "append-only: the old version remains "
                         "accessible")

    def test_14_duplicate_is_independent_manual_job(self):
        """§18/14 + directive §5 (Case B, explicit): a duplicated job
        is an INDEPENDENT manual queue row. It keeps prompt/voice/
        scene/character context but strips ALL structural provenance —
        its generated asset joins NO expected slot and can never
        masquerade as the original Scene slot."""
        from engine.audio_provenance import (
            materialize_expected_slots, register_generation_result,
            is_slot_covered, coverage_counts)
        from engine.models import Project, Scene
        from types import SimpleNamespace
        project = Project(name="Eden", id="proj1")
        scene = Scene(id="ab12cd34ef56", name="Scene 03",
                      project_id="proj1")
        project.add_scene(scene)
        scene.expected_audio_slots = materialize_expected_slots([
            _make_part(block_id="blkA", part_of_block=2, block_label="A"),
        ])
        # Original structural part job:
        job = self._part_job()
        self.bm.add_job(job)
        new_idx = self.bm.duplicate_job(0)
        self.assertEqual(new_idx, 1)
        clone = self.bm.jobs[1]
        # Context preserved (P3.23 §22 — History lineage):
        self.assertEqual(clone.prompt, job.prompt)
        self.assertEqual(clone.scene_id, job.scene_id)
        self.assertEqual(clone.scene_name, job.scene_name)
        self.assertEqual(clone.character_id, job.character_id)
        self.assertEqual(clone.speaker, job.speaker)
        self.assertEqual(clone.voice_id, job.voice_id)
        # Structural provenance STRIPPED (the Case B decision):
        for field in ("slot_id", "part_index", "part_version",
                      "source_block_id", "generation_run",
                      "expected_duration"):
            self.assertIsNone(
                getattr(clone, field),
                "duplicate must not inherit structural provenance: "
                "{0}".format(field))
        # The clone's request carries no slot identity:
        request = clone.to_request()
        self.assertIsNone(request.slot_id)
        self.assertIsNone(request.part_index)
        self.assertIsNone(request.block_id)
        # The clone's registered asset joins NO slot (coverage of the
        # original slot is unaffected by the duplicate's generation):
        result = SimpleNamespace(
            scene_id=scene.id, output_path="outputs/dup.wav",
            output_duration=2.0, speaker=None, character_id=None,
            voice_profile=None, timestamp="20260101_120200",
            block_id=None, part_index=None, part_version=None,
            generation_run=None, slot_id=None)
        asset = register_generation_result(project, result)
        self.assertIsNone(asset["slot_id"])
        self.assertFalse(is_slot_covered(scene, "blkA:2"),
                         "the duplicate's audio must not cover the "
                         "original slot")
        self.assertEqual(coverage_counts(scene), (0, 1))
        # And the ORIGINAL job still carries its identity:
        self.assertEqual(self.bm.jobs[0].slot_id, "blkA:2")

    def test_14b_duplicate_of_manual_job_stays_slot_free(self):
        """A duplicate of a MANUAL job (never had provenance) is a
        faithful manual copy — no structural identity appears."""
        manual = self.BatchJob(name="New Job", prompt="free text",
                               project="Eden")
        self.bm.add_job(manual)
        self.bm.duplicate_job(0)
        clone = self.bm.jobs[1]
        self.assertIsNone(clone.slot_id)
        self.assertIsNone(clone.to_request().slot_id)

    def test_14c_duplicate_queue_roundtrip_keeps_split(self):
        """Queue persistence keeps the manual/structural split: the
        original's slot_id round-trips; the duplicate's absence of
        slot_id round-trips too."""
        import tempfile, os
        job = self._part_job()
        self.bm.add_job(job)
        self.bm.duplicate_job(0)
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        try:
            self.bm.save_to_file(path)
            self.bm.clear_all()
            self.bm.load_from_file(path)
            self.assertEqual(self.bm.jobs[0].slot_id, "blkA:2")
            self.assertIsNone(self.bm.jobs[1].slot_id)
        finally:
            os.unlink(path)


# ===========================================================================
# §18 Combine 15–18 — resolution through the identity join (real files)
# ===========================================================================
class TestCombineResolution(_IdentityFactory):
    """resolved_slot_sources (the Scene Combine input contract) with
    REAL audio files on disk."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p3446_comb_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wav(self, name):
        import soundfile as sf
        path = os.path.join(self.tmp, name)
        sf.write(path, make_speech(0.5), SAMPLE_RATE)
        return path

    def _scene_with_files(self, slots, asset_specs):
        """asset_specs: list of dicts for modern_asset() with a real
        file under 'file'."""
        from engine.models import Scene
        scene = Scene(id=self.SCENE_ID, name="Scene 03")
        scene.expected_audio_slots = list(slots)
        assets = []
        for spec in asset_specs:
            wav = self._wav(spec["file"])
            rel = os.path.relpath(wav, self.tmp)
            a = self.modern_asset(spec["slot_id"], spec["part_index"],
                                  spec["version"], spec["block_id"],
                                  path=rel)
            assets.append(a)
        scene.audio_assets = assets
        return scene

    def test_15_valid_same_slot_asset_resolves(self):
        """§18/15: a valid modern same-slot asset resolves as a Combine
        source (in expected-slot order, with the right file)."""
        from engine.audio_provenance import resolved_slot_sources
        slots = [
            self.slot("blkA:1", "blkA", 1),
            self.slot("blkB:1", "blkB", 2),
        ]
        scene = self._scene_with_files(slots, [
            dict(file="a1.wav", slot_id="blkA:1", part_index=1,
                 version=1, block_id="blkA"),
            dict(file="b1.wav", slot_id="blkB:1", part_index=2,
                 version=1, block_id="blkB"),
        ])
        sources, blockers = resolved_slot_sources(scene, self.tmp)
        self.assertEqual(blockers, [])
        self.assertEqual([s["slot_id"] for s in sources],
                         ["blkA:1", "blkB:1"])
        self.assertTrue(sources[0]["output_path"].endswith("a1.wav"))
        self.assertTrue(sources[1]["output_path"].endswith("b1.wav"))

    def test_16_wrong_slot_asset_does_not_resolve(self):
        """§18/16: an asset whose slot_id belongs to a DIFFERENT slot
        (directive §13 Invalid: current slot BlockB:2, asset
        slot_id=BlockA:2) does not resolve — the slot stays a blocker."""
        from engine.audio_provenance import resolved_slot_sources
        slots = [
            self.slot("blkB:1", "blkB", 1),
            self.slot("blkB:2", "blkB", 2),
        ]
        scene = self._scene_with_files(slots, [
            dict(file="old_a2.wav", slot_id="blkA:2", part_index=2,
                 version=1, block_id="blkA"),
        ])
        sources, blockers = resolved_slot_sources(scene, self.tmp)
        self.assertEqual(sources, [])
        self.assertEqual(len(blockers), 2,
                         "both slots must block — nothing resolves")

    def test_17_changed_decomposition_does_not_resolve_old_audio(self):
        """§18/17: old BlockB:1/BlockB:2 audio vs the NEW decomposition
        BlockB:1/2/3 — old assets resolve ONLY against their own
        identities; the genuinely new part never receives old audio."""
        from engine.audio_provenance import (
            resolved_slot_sources, coverage_counts)
        new_slots = [
            self.slot("blkB:1", "blkB", 1, part_of_block=1),
            self.slot("blkB:2", "blkB", 2, part_of_block=2),
            self.slot("blkB:3", "blkB", 3, part_of_block=3),
        ]
        scene = self._scene_with_files(new_slots, [
            dict(file="old_b1.wav", slot_id="blkB:1", part_index=1,
                 version=1, block_id="blkB"),
            dict(file="old_b2.wav", slot_id="blkB:2", part_index=2,
                 version=1, block_id="blkB"),
        ])
        sources, blockers = resolved_slot_sources(scene, self.tmp)
        self.assertEqual([s["slot_id"] for s in sources],
                         ["blkB:1", "blkB:2"])
        self.assertEqual(len(blockers), 1,
                         "the genuinely new third part must block")
        self.assertIn("no generated audio", blockers[0])
        self.assertEqual(coverage_counts(scene), (2, 3))

    def test_18_selected_version_resolves_correctly(self):
        """§18/18: explicit per-slot version selection resolves the
        chosen asset in the Combine source (and falls back to latest
        when the selection points at a missing asset)."""
        from engine.audio_provenance import resolved_slot_sources
        slots = [self.slot("blkA:1", "blkA", 1)]
        scene = self._scene_with_files(slots, [
            dict(file="v1.wav", slot_id="blkA:1", part_index=1,
                 version=1, block_id="blkA"),
            dict(file="v2.wav", slot_id="blkA:1", part_index=1,
                 version=2, block_id="blkA"),
        ])
        # Default: latest (v2).
        sources, _ = resolved_slot_sources(scene, self.tmp)
        self.assertTrue(sources[0]["output_path"].endswith("v2.wav"))
        # Explicit selection of v1:
        v1 = scene.audio_assets[0]
        scene.selected_block_audio["blkA:1"] = v1["id"]
        sources, _ = resolved_slot_sources(scene, self.tmp)
        self.assertTrue(sources[0]["output_path"].endswith("v1.wav"))
        self.assertEqual(sources[0]["part_version"], 1)
        # Dangling selection falls back to latest (never destroyed):
        scene.selected_block_audio["blkA:1"] = "missing-asset"
        sources, _ = resolved_slot_sources(scene, self.tmp)
        self.assertTrue(sources[0]["output_path"].endswith("v2.wav"))


# ===========================================================================
# §18 Persistence 19–20 — save → close → reopen → reload
# ===========================================================================
class TestPersistence(_IdentityFactory):
    """The identity survives Scene.to_dict/from_dict (project.json)
    and BatchJob.to_dict/from_dict (queue YAML) — the plain-dict asset
    key round-trips through the tolerant readers with no schema
    change; resolution behaviour is identical after reload."""

    def test_19_save_load_preserves_slot_identity(self):
        """§18/19: Scene round-trip: the asset's slot_id, the expected
        slots, and the per-slot selection all survive."""
        from engine.models import Scene
        slots = self.structure([
            _make_part(block_id="blkA", part_of_block=1, block_label="A"),
            _make_part(block_id="blkB", part_of_block=1, block_label="B"),
        ])
        scene = self.make_scene(slots=slots)
        a1 = self.modern_asset("blkA:1", 1, 1, "blkA")
        a2 = self.modern_asset("blkB:1", 2, 1, "blkB")
        scene.audio_assets = [a1, a2]
        scene.selected_block_audio["blkB:1"] = a2["id"]
        reloaded = Scene.from_dict(scene.to_dict())
        # Asset identity preserved:
        self.assertEqual(reloaded.audio_assets[0]["slot_id"], "blkA:1")
        self.assertEqual(reloaded.audio_assets[1]["slot_id"], "blkB:1")
        # Expected-slot structure preserved:
        self.assertEqual(
            [s["slot_id"] for s in reloaded.expected_audio_slots],
            ["blkA:1", "blkB:1"])
        # Selection map preserved:
        self.assertEqual(reloaded.selected_block_audio.get("blkB:1"),
                         a2["id"])

    def test_20_reopen_preserves_resolution_behaviour(self):
        """§18/20: after save → close → reopen (from_dict), coverage,
        per-slot membership, latest/selected resolution and the
        identity-first join behave EXACTLY as before the round-trip —
        including the §15 decomposition guard and the reorder reuse."""
        from engine.models import Scene
        from engine.audio_provenance import (
            coverage_counts, is_slot_covered, resolved_asset_for_slot,
            slot_of_asset)
        # Structure with a reorder relative to the assets' positions:
        slots = self.structure([
            _make_part(block_id="blkB", part_of_block=1, block_label="B"),
            _make_part(block_id="blkA", part_of_block=1, block_label="A"),
        ])
        scene = self.make_scene(slots=slots)
        a1 = self.modern_asset("blkA:1", 1, 1, "blkA")  # pos 1 originally
        b1 = self.modern_asset("blkB:1", 2, 1, "blkB")
        scene.audio_assets = [a1, b1]
        scene.selected_block_audio["blkA:1"] = a1["id"]
        reloaded = Scene.from_dict(scene.to_dict())
        self.assertEqual(coverage_counts(reloaded), (2, 2))
        self.assertTrue(is_slot_covered(reloaded, "blkA:1"))
        self.assertTrue(is_slot_covered(reloaded, "blkB:1"))
        self.assertEqual(slot_of_asset(a1, reloaded), "blkA:1")
        self.assertEqual(slot_of_asset(b1, reloaded), "blkB:1")
        self.assertIs(resolved_asset_for_slot(reloaded, "blkA:1"),
                      reloaded.audio_assets[0])
        # The decomposition guard survives reload: an asset of a
        # vanished slot still joins nothing.
        old_part = self.modern_asset("blkA:2", 2, 1, "blkA")
        reloaded.audio_assets.append(old_part)
        self.assertIsNone(slot_of_asset(old_part, reloaded))

    def test_batchjob_roundtrip_preserves_slot_identity(self):
        """Queue YAML contract: BatchJob.to_dict/from_dict round-trips
        slot_id (and the whole provenance set) — the reopened Batch
        (same scene, same structure) still addresses the same slots."""
        from engine.batch_manager import BatchJob
        job = BatchJob(
            name="Part 2", prompt="text", project="Eden",
            scene_id="ab12cd34ef56", scene_name="Scene 03",
            part_index=2, part_version=1, source_block_id="blkA",
            generation_run="ab12cd34-r001", slot_id="blkA:2")
        clone = BatchJob.from_dict(job.to_dict())
        self.assertEqual(clone.slot_id, "blkA:2")
        self.assertEqual(clone.source_block_id, "blkA")
        self.assertEqual(clone.part_index, 2)
        self.assertEqual(clone.part_version, 1)
        self.assertEqual(clone.generation_run, "ab12cd34-r001")
        self.assertEqual(clone.to_request().slot_id, "blkA:2")

    def test_legacy_scene_without_slot_id_reloads_unchanged(self):
        """Legacy project files (assets recorded before P3.44.6, no
        slot_id key) reload with their P3.44.5 behaviour intact —
        tolerant readers, no migration required."""
        from engine.models import Scene
        from engine.audio_provenance import (
            slot_of_asset, is_slot_covered)
        slots = [
            self.slot("b1:1", "b1", 1),
            self.slot("b2:1", "b2", 2),
        ]
        scene = self.make_scene(slots=slots)
        l1 = self.legacy_asset(1, 1, "b1")
        l2 = self.legacy_asset(2, 1, "b2")
        scene.audio_assets = [l1, l2]
        reloaded = Scene.from_dict(scene.to_dict())
        self.assertEqual(slot_of_asset(reloaded.audio_assets[0],
                                       reloaded), "b1:1")
        self.assertTrue(is_slot_covered(reloaded, "b2:1"))
        self.assertNotIn("slot_id", reloaded.audio_assets[0])


# ===========================================================================
# Complete-scene proof — the defensive invariant
# ===========================================================================
class TestCompleteSceneAssetProof(_IdentityFactory):
    """is_complete_scene_asset: an asset carrying ANY slot provenance —
    including the new slot_id — is a Part, never the whole Scene."""

    def test_modern_slot_asset_is_never_complete_scene(self):
        from engine.audio_provenance import is_complete_scene_asset
        a = self.modern_asset("blkA:1", 1, 1, "blkA",
                              path="outputs/scene.wav")
        self.assertFalse(is_complete_scene_asset(a))

    def test_slot_id_alone_marks_a_part(self):
        """Defensive airtight case: even an asset carrying ONLY the
        slot identity (no positional fields) is a Part — it can never
        masquerade as the complete Scene output."""
        from engine.audio_provenance import is_complete_scene_asset
        a = {"id": "x", "scene_id": self.SCENE_ID,
             "output_path": "outputs/scene.wav", "duration": 1.0,
             "slot_id": "blkA:1"}
        self.assertFalse(is_complete_scene_asset(a))

    def test_full_scene_asset_still_provably_complete(self):
        from engine.audio_provenance import is_complete_scene_asset
        a = {"id": "fs", "scene_id": self.SCENE_ID,
             "output_path": "outputs/full_scene.wav", "duration": 1.0}
        self.assertTrue(is_complete_scene_asset(a))


if __name__ == "__main__":
    unittest.main(verbosity=2)
