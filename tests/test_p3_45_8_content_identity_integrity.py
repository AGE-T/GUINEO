# -*- coding: utf-8 -*-
"""P3.45.8 — narration content-identity integrity (Re-add + Combine)
=====================================================================

Deterministic regressions for the owner-mandated narration-integrity
closure. The two association points where wrong-text audio could
previously be attached silently are now gated by a fail-closed content
identity (``content_sha``: SHA-256 over the exact generation prompt +
speaker + character — see ``engine/audio_provenance.part_content_sha``):

TASK 1 — Re-add Missing (E1..E8):
  E1  same block_id + part number, CHANGED part text     -> repair REFUSED
  E2  same block_id + part number, changed boundaries    -> repair REFUSED
      (recorded decomposition no longer matches the current split)
  E3  sentence insertion shifting later boundaries       -> the affected
      slot REFUSED, unaffected positions still repairable
  E4  unchanged text + unchanged decomposition           -> repair STILL
      SUCCEEDS (jobs appended, queue never wiped)
  E5  missing legacy fingerprint / incomplete provenance -> repair REFUSED
      (legacy slots cannot be auto-matched; remedy named)
  E6  speaker / character identity change                -> repair REFUSED
  E7  duplicate identical texts                          -> every position
      proves ITS OWN content (position + fingerprint): no cross-
      association, no ambiguity at the Re-add gate
  E8  aborted / partial repair                           -> assets, slots,
      selections and existing queue rows untouched

TASK 2 — Re-detect / Combine stale-audio integrity (D1..D9):
  D1  existing audio whose Block no longer exists        -> BLOCKED
  D2  changed text with the old audio still present      -> BLOCKED
  D3  unchanged slot with valid provenance               -> still combines
  D4  uniquely provable block-ID replacement             -> reconciled
      (Re-detect over unchanged text with proven assets)
  D5  ambiguous repeated text                            -> BLOCKED
  D6  missing / old provenance fields (legacy project)   -> BLOCKED
  D7  mixture of valid / stale / missing slots           -> exact per-slot
      blockers, fail-closed
  D8  aborting Combine never mutates audio / project data
  D9  successful Combine after repair / regeneration

Plus the engine-level tier matrix for ``resolved_slot_sources`` and the
identity primitive itself. Everything drives the REAL application logic
(MainWindow, editor, splitter, batch manager, combine) with the model
boundary faked — the same house pattern as test_p3_28 / test_p3_45_6.

Legacy compatibility is honestly scoped: a legacy slot/asset (no
``content_sha``) is REFUSED at both gates (E5/D6) — the documented
fail-closed rule for unprovable content; the remedy (Generate Long /
regenerate the affected parts) is always named, never automatic.
"""

import os
import sys
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
# Fake torch (house pattern)
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


CHANGED_MARKER = "no longer matches the recorded generation content"
LEGACY_MARKER = "content-identity provenance"
STALE_AUDIO_MARKER = "generated for a previous version"
ORPHANED_MARKER = "recorded content no longer exists"
AMBIGUOUS_MARKER = "cannot be matched unambiguously"
TIER1_ORPHAN_MARKER = "still has audio, but its Block no longer exists"


# ===========================================================================
# 1) The identity primitive (pure)
# ===========================================================================
class TestContentIdentityPrimitive(unittest.TestCase):

    def test_deterministic_and_versioned(self):
        from engine.audio_provenance import (
            part_content_sha, CONTENT_IDENTITY_VERSION)
        a = part_content_sha("Hello.", "CAPTAIN", "ch1")
        b = part_content_sha("Hello.", "CAPTAIN", "ch1")
        self.assertEqual(a, b)
        self.assertEqual(CONTENT_IDENTITY_VERSION, 1)
        self.assertIsInstance(a, str)
        self.assertEqual(len(a), 64)

    def test_exact_bytes_no_normalisation(self):
        from engine.audio_provenance import part_content_sha
        # Whitespace / case differences are DIFFERENT content — never
        # folded together (no lossy normalisation).
        self.assertNotEqual(part_content_sha("Hello."), part_content_sha(" Hello."))
        self.assertNotEqual(part_content_sha("Hello."), part_content_sha("hello."))
        self.assertNotEqual(part_content_sha("Hello."), part_content_sha("Hello.."))

    def test_speaker_and_character_participate(self):
        from engine.audio_provenance import part_content_sha
        base = part_content_sha("Hello.")
        self.assertNotEqual(base, part_content_sha("Hello.", "CAPTAIN"))
        self.assertNotEqual(part_content_sha("Hello.", "A"),
                            part_content_sha("Hello.", "B"))
        self.assertNotEqual(part_content_sha("Hello.", None, "ch1"),
                            part_content_sha("Hello.", None, "ch2"))

    def test_none_canonicalised_and_none_prompt(self):
        from engine.audio_provenance import part_content_sha
        self.assertEqual(part_content_sha("x", None, None),
                         part_content_sha("x", "", ""))
        self.assertIsNone(part_content_sha(None))

    def test_materialize_and_register_stamp_the_same_fingerprint(self):
        """The two writers of the identity chain agree: the SLOT's
        fingerprint (from the splitter part) equals the ASSET's
        fingerprint stamped by the ONE registration writer from the
        RESULT's prompt (the exact generation input)."""
        from engine.audio_provenance import (
            materialize_expected_slots, register_generation_result)
        part = _make_part(text="The exact part text.", speaker="CAPTAIN",
                          character_id="ch1", block_id="blk01")
        slots = materialize_expected_slots([part])
        result = types.SimpleNamespace(
            scene_id="sc1", output_path="", output_duration=1.0,
            speaker="CAPTAIN", character_id="ch1", voice_profile=None,
            timestamp="t", block_id="blk01", part_index=1, part_version=1,
            generation_run="r1", slot_id="blk01:1",
            prompt="The exact part text.")
        project = types.SimpleNamespace(
            get_scene=lambda sid: types.SimpleNamespace(
                id="sc1", name="S", audio_assets=[], status="",
                expected_audio_slots=[]))
        asset = register_generation_result(project, result)
        self.assertEqual(slots[0]["content_sha"], asset["content_sha"])


# ===========================================================================
# 2) Engine tier matrix for resolved_slot_sources (pure)
# ===========================================================================
class _EngineFactory(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p3458e_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wav(self, name):
        import soundfile as sf
        path = os.path.join(self.tmp, "outputs", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sf.write(path, make_speech(0.5), SAMPLE_RATE)
        return os.path.relpath(path, self.tmp)

    def slot(self, block_id, sha, label=None, part_index=1):
        return {"slot_id": "{0}:1".format(block_id), "block_id": block_id,
                "block_label": label or "Block {0}".format(block_id[-2:]),
                "part_index": part_index, "part_of_block": 1,
                "speaker": "", "character_id": None, "content_sha": sha}

    def asset(self, slot_id, sha=None, part_index=1):
        a = {"id": "a{0}".format(part_index), "scene_id": "sc1",
             "output_path": self.wav("a{0}.wav".format(part_index)),
             "duration": 1.0, "speaker": None, "character_id": None,
             "voice_profile_id": None, "generated_at": "t",
             "block_id": slot_id.split(":")[0], "part_index": part_index,
             "part_version": 1, "generation_run": "r1", "slot_id": slot_id}
        if sha is not None:
            a["content_sha"] = sha
        return a

    def scene(self, slots, assets):
        from engine.models import Scene
        s = Scene(id="sc1abcd", name="S")
        s.expected_audio_slots = list(slots)
        s.audio_assets = list(assets)
        return s

    def cur(self, slot_id, sha, block_id):
        return {"slot_id": slot_id, "block_id": block_id,
                "part_of_block": 1, "content_sha": sha}


class TestResolvedSlotSourcesTiers(_EngineFactory):

    def test_proven_chain_is_a_source(self):
        from engine.audio_provenance import resolved_slot_sources
        sha = "s1"
        scene = self.scene([self.slot("blk01", sha)],
                           [self.asset("blk01:1", sha)])
        parts = [self.cur("blk01:1", sha, "blk01")]
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_parts=parts)
        self.assertEqual(blockers, [])
        self.assertEqual([s["slot_id"] for s in sources], ["blk01:1"])

    def test_changed_text_blocked(self):
        from engine.audio_provenance import resolved_slot_sources
        scene = self.scene([self.slot("blk01", "old")],
                           [self.asset("blk01:1", "old")])
        parts = [self.cur("blk01:1", "new", "blk01")]
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_parts=parts)
        self.assertEqual(sources, [])
        self.assertEqual(len(blockers), 1)
        self.assertIn(CHANGED_MARKER, blockers[0])

    def test_stale_audio_blocked(self):
        """slot == current, but the RESOLVED asset was generated for a
        previous content (e.g. an explicitly selected older version)."""
        from engine.audio_provenance import resolved_slot_sources
        scene = self.scene([self.slot("blk01", "cur")],
                           [self.asset("blk01:1", "old")])
        parts = [self.cur("blk01:1", "cur", "blk01")]
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_parts=parts)
        self.assertEqual(sources, [])
        self.assertEqual(len(blockers), 1)
        self.assertIn(STALE_AUDIO_MARKER, blockers[0])

    def test_legacy_slot_blocked_with_remedy(self):
        from engine.audio_provenance import resolved_slot_sources
        slot = self.slot("blk01", "irrelevant")
        slot.pop("content_sha", None)
        scene = self.scene([slot], [self.asset("blk01:1", "sha")])
        parts = [self.cur("blk01:1", "sha", "blk01")]
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_parts=parts)
        self.assertEqual(sources, [])
        self.assertIn(LEGACY_MARKER, blockers[0])
        self.assertIn("Generate Long", blockers[0])

    def test_unique_reconciliation_is_a_source(self):
        from engine.audio_provenance import resolved_slot_sources
        sha = "s1"
        scene = self.scene([self.slot("blk01", sha)],
                           [self.asset("blk01:1", sha)])
        # The block id was REPLACED (re-detect) but the content survived
        # unchanged and uniquely.
        parts = [self.cur("new01:1", sha, "new01")]
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_parts=parts)
        self.assertEqual(blockers, [])
        self.assertEqual([s["slot_id"] for s in sources], ["blk01:1"])

    def test_ambiguous_reconciliation_blocked(self):
        from engine.audio_provenance import resolved_slot_sources
        sha = "s1"
        scene = self.scene([self.slot("blk01", sha)],
                           [self.asset("blk01:1", sha)])
        parts = [self.cur("new01:1", sha, "new01"),
                 self.cur("new02:1", sha, "new02")]
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_parts=parts)
        self.assertEqual(sources, [])
        self.assertEqual(len(blockers), 1)
        self.assertIn(AMBIGUOUS_MARKER, blockers[0])

    def test_orphaned_content_blocked(self):
        from engine.audio_provenance import resolved_slot_sources
        scene = self.scene([self.slot("blk01", "gone")],
                           [self.asset("blk01:1", "gone")])
        parts = [self.cur("new01:1", "other", "new01")]
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_parts=parts)
        self.assertEqual(sources, [])
        self.assertIn(ORPHANED_MARKER, blockers[0])

    def test_empty_parts_is_positive_empty(self):
        from engine.audio_provenance import resolved_slot_sources
        sha = "s1"
        scene = self.scene([self.slot("blk01", sha)],
                           [self.asset("blk01:1", sha)])
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_parts=[])
        self.assertEqual(sources, [])
        self.assertIn(ORPHANED_MARKER, blockers[0])

    def test_tier1_orphaned_block_blocked(self):
        from engine.audio_provenance import resolved_slot_sources
        sha = "s1"
        scene = self.scene([self.slot("blk01", sha)],
                           [self.asset("blk01:1", sha)])
        sources, blockers = resolved_slot_sources(
            scene, self.tmp, current_block_ids={"new01"})
        self.assertEqual(sources, [])
        self.assertIn(TIER1_ORPHAN_MARKER, blockers[0])

    def test_default_none_is_byte_identical_legacy(self):
        from engine.audio_provenance import resolved_slot_sources
        sha = "s1"
        scene = self.scene([self.slot("blk01", "other")],
                           [self.asset("blk01:1", sha)])
        sources, blockers = resolved_slot_sources(scene, self.tmp)
        self.assertEqual(blockers, [])
        self.assertEqual(len(sources), 1)


# ===========================================================================
# 3) REAL MainWindow harness
# ===========================================================================
class _MWHarness(unittest.TestCase):

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p3458mw_")
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

    def make_blocks_and_text(self, n, ids=None, paras=None):
        from engine.narration_blocks import PromptBlock
        if paras is None:
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

    def proven_slots(self, blocks, paras, labels=None):
        """Slots materialised from the REAL block texts — fingerprints
        match the current decomposition (the proven-identity path)."""
        from engine.audio_provenance import materialize_expected_slots
        return materialize_expected_slots([
            _make_part(text=para, block_id=b.id,
                       block_label=(labels[i] if labels
                                    else "Block {0}".format(i + 1)))
            for i, (b, para) in enumerate(zip(blocks, paras))])

    def direct_asset(self, slot_id, part_index, content_sha=None):
        import soundfile as sf
        path = os.path.join(self.tmp, "outputs",
                            "direct_p{0:03d}.wav".format(part_index))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sf.write(path, make_speech(0.5), SAMPLE_RATE)
        asset = {
            "id": "direct-{0}".format(slot_id),
            "scene_id": self.scene.id,
            "output_path": os.path.relpath(path, self.tmp),
            "duration": 10.0, "speaker": None, "character_id": None,
            "voice_profile_id": None, "generated_at": "20260101_120000",
            "block_id": slot_id.split(":")[0], "part_index": part_index,
            "part_version": 1,
            "generation_run": "{0}-r001".format(self.scene.id[:8]),
            "slot_id": slot_id,
        }
        if content_sha is not None:
            asset["content_sha"] = content_sha
        return asset

    def proven_assets(self, blocks, paras):
        from engine.audio_provenance import part_content_sha
        return [self.direct_asset("{0}:1".format(b.id), i + 1,
                                  content_sha=part_content_sha(para))
                for i, (b, para) in enumerate(zip(blocks, paras))]

    def load_editor(self, text, blocks, character_ids=None):
        from engine.narration_blocks import PromptBlock as _PB
        self.scene.text = text
        dicts = []
        for i, b in enumerate(blocks):
            d = b.to_dict()
            if character_ids and character_ids[i] is not None:
                d["character_id"] = character_ids[i]
            dicts.append(d)
        self.scene.narration_blocks = dicts
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

    def combine_warning(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "warning", return_value=None) as warn:
            ok = self.win._combine_scene(self.scene)
        msg = warn.call_args[0][2] if warn.called else ""
        return ok, msg

    def readd_with_warning(self):
        """Run [Re-add Missing] capturing the QMessageBox.warning text
        (the refused-slot report) and the status-bar message."""
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "warning", return_value=None) as warn:
            self.win._on_readd_missing_parts()
        msg = warn.call_args[0][2] if warn.called else ""
        return msg

    def insert_into_block(self, block, extra):
        """Insert text INSIDE the block's range (id survives, text
        changes) through the REAL editor."""
        from PySide6.QtGui import QTextCursor
        ed = self.win._editor._editor
        cur = ed.textCursor()
        cur.setPosition(block.end_offset - 1)
        cur.insertText(extra)
        _process(30)

    def delete_block_text(self, block):
        from PySide6.QtGui import QTextCursor
        ed = self.win._editor._editor
        cur = ed.textCursor()
        cur.setPosition(block.start_offset)
        cur.setPosition(block.end_offset,
                        QTextCursor.MoveMode.KeepAnchor)
        ed.setTextCursor(cur)
        cur.removeSelectedText()
        _process(30)


# ===========================================================================
# 4) TASK 1 — Re-add Missing content identity (E1..E8)
# ===========================================================================
class TestReaddContentIdentity(_MWHarness):

    def _queue_without(self, blocks, paras, removed):
        """A scene whose queue rows for `removed` blocks were deleted
        (Remove = job-only), so those slots are missing from the queue."""
        text, blocks, paras = self.make_blocks_and_text(
            len(blocks), paras=paras) if False else (None, blocks, paras)
        self.scene.expected_audio_slots = self.proven_slots(blocks, paras)
        self.load_editor("\n".join(paras), blocks)
        from engine.batch_manager import BatchJob
        self.win._batch_manager.clear_all()
        for i, b in enumerate(blocks):
            if i in removed:
                continue
            self.win._batch_manager.add_job(BatchJob(
                name="Part {0}".format(i + 1), prompt=paras[i],
                part_index=i + 1, part_version=1,
                slot_id="{0}:1".format(b.id),
                source_block_id=b.id, scene_id=self.scene.id,
                scene_name=self.scene.name))
        self.win._present_batch_dialog(scene=self.scene,
                                       scene_id=self.scene.id)
        self.dialog()._refresh_table()
        return blocks, paras

    def test_e1_changed_part_text_refuses_repair(self):
        """E1: same block_id + part number, CHANGED text -> NO job, the
        refusal names the content difference, nothing is mutated."""
        text, blocks, paras = self.make_blocks_and_text(2)
        self._queue_without(blocks, paras, removed={0})
        # Edit Block 1's text (id survives, content changes).
        self.insert_into_block(blocks[0], " An inserted sentence.")
        slots_before = list(self.scene.expected_audio_slots)
        assets_before = list(self.scene.audio_assets)
        bm = self.win._batch_manager
        rows_before = list(bm.jobs)
        msg = self.readd_with_warning()
        self.assertIn("Re-added 0 of 1", msg)
        self.assertIn("differs from the recorded generation content", msg)
        self.assertIn("Generate Long", msg)
        # The refused slot got NO job; nothing else changed.
        self.assertEqual([j.slot_id for j in bm.jobs],
                         [j.slot_id for j in rows_before])
        self.assertEqual(self.scene.expected_audio_slots, slots_before)
        self.assertEqual(self.scene.audio_assets, assets_before)

    def test_e2_changed_decomposition_refuses_repair(self):
        """E2: the recorded decomposition no longer matches the current
        split: a recorded TWO-part block whose current text produces a
        different part at the same position -> refused; the part that no
        longer exists -> refused."""
        from engine.audio_provenance import materialize_expected_slots
        # A recorded structure where blk01 decomposed into 2 parts.
        p1 = _make_part(text="First recorded piece.", block_id="blk01",
                        part_of_block=1, block_label="Block 1")
        p2 = _make_part(text="Second recorded piece.", block_id="blk01",
                        part_of_block=2, block_label="Block 1")
        p3 = _make_part(text="Unaffected later block.", block_id="blk02",
                        part_of_block=1, block_label="Block 2")
        self.scene.expected_audio_slots = materialize_expected_slots(
            [p1, p2, p3])
        # The current editor text: block 1 holds DIFFERENT content for
        # part 1 and part 2's text is gone (boundary shift), block 2
        # unchanged.
        from engine.narration_blocks import PromptBlock
        cur_text = ("Changed first content.\nUnaffected later block.")
        blocks = [PromptBlock(id="blk01", start_offset=0,
                              end_offset=len("Changed first content.")),
                  PromptBlock(id="blk02",
                              start_offset=len("Changed first content.") + 1,
                              end_offset=len(cur_text))]
        self.load_editor(cur_text, blocks)
        from engine.batch_manager import BatchJob
        self.win._batch_manager.clear_all()
        self.win._batch_manager.add_job(BatchJob(
            name="Part 1", prompt="x", part_index=3, part_version=1,
            slot_id="blk02:1", source_block_id="blk02",
            scene_id=self.scene.id, scene_name=self.scene.name))
        self.win._present_batch_dialog(scene=self.scene,
                                       scene_id=self.scene.id)
        msg = self.readd_with_warning()
        self.assertIn("Re-added 0 of 2", msg)
        # blk01:1 — same slot identity, different content.
        self.assertIn("differs from the recorded generation content", msg)
        # blk01:2 — the current text no longer produces this part.
        self.assertIn("no longer produces this part", msg)

    def test_e3_sentence_insertion_shifts_only_the_affected_slot(self):
        """E3: a sentence inserted into Block 2 refuses Block 2's repair
        while the UNCHANGED Block 3 stays legitimately repairable."""
        text, blocks, paras = self.make_blocks_and_text(3)
        self._queue_without(blocks, paras, removed={1, 2})
        self.insert_into_block(blocks[1], " A shifted boundary sentence.")
        bm = self.win._batch_manager
        msg = self.readd_with_warning()
        self.assertIn("Re-added 1 of 2", msg)
        # Block 3 (unchanged) got its job; Block 2 (changed) refused.
        self.assertEqual(
            sorted(j.slot_id for j in bm.jobs
                   if j.slot_id == "blk03:1"),
            ["blk03:1"])
        self.assertIn("differs from the recorded generation content", msg)

    def test_e4_unchanged_text_still_repairs(self):
        """E4: unchanged text + unchanged decomposition -> the repair
        still succeeds (jobs appended with full provenance, the queue
        never wiped)."""
        text, blocks, paras = self.make_blocks_and_text(3)
        self._queue_without(blocks, paras, removed={1, 2})
        bm = self.win._batch_manager
        kept = [j.slot_id for j in bm.jobs]
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "warning", return_value=None) as w:
            self.win._on_readd_missing_parts()
            self.assertFalse(w.called, "no refusal expected: " + (
                w.call_args[0][2] if w.called else ""))
        self.assertEqual(
            [j.slot_id for j in bm.jobs],
            kept + ["blk02:1", "blk03:1"])
        # Full provenance on the re-added rows.
        for j in bm.jobs:
            if j.slot_id in ("blk02:1", "blk03:1"):
                self.assertIsNotNone(j.part_index)
                self.assertIsNotNone(j.part_version)
                self.assertIsNotNone(j.generation_run)
                self.assertEqual(j.source_block_id,
                                 j.slot_id.split(":")[0])

    def test_e5_legacy_slot_refuses_repair(self):
        """E5: a slot recorded before content provenance (no
        content_sha) can never be auto-matched — refused with the
        legacy reason and the Generate Long remedy."""
        from engine.audio_provenance import materialize_expected_slots
        text, blocks, paras = self.make_blocks_and_text(1)
        slots = materialize_expected_slots(
            [_make_part(text=paras[0], block_id="blk01")])
        for s in slots:
            s.pop("content_sha", None)   # the legacy persisted shape
        self.scene.expected_audio_slots = slots
        self.load_editor(text, blocks)
        self.win._batch_manager.clear_all()
        msg = self.readd_with_warning()
        self.assertIn("Re-added 0 of 1", msg)
        self.assertIn("legacy structure without content identity", msg)
        self.assertIn("Generate Long", msg)

    def test_e6_character_identity_change_refuses_repair(self):
        """E6: the block's CHARACTER changed (same text) — the recorded
        fingerprint includes character identity, so reuse is refused."""
        from engine.audio_provenance import part_content_sha
        text, blocks, paras = self.make_blocks_and_text(1)
        # Recorded: the part was generated WITHOUT a character.
        self.scene.expected_audio_slots = self.proven_slots(blocks, paras)
        self.load_editor(text, blocks, character_ids=["ch_new"])
        bm = self.win._batch_manager
        self.win._batch_manager.clear_all()
        msg = self.readd_with_warning()
        self.assertIn("Re-added 0 of 1", msg)
        self.assertIn("differs from the recorded generation content", msg)

    def test_e7_duplicate_texts_repair_their_own_positions(self):
        """E7: two blocks with IDENTICAL text: each missing slot proves
        ITS OWN content at its own position — both repair correctly,
        with no cross-association (position + fingerprint)."""
        paras = ["Identical deterministic sentence for the block.",
                 "Identical deterministic sentence for the block."]
        text, blocks, _ = self.make_blocks_and_text(2, paras=paras)
        self._queue_without(blocks, paras, removed={0, 1})
        bm = self.win._batch_manager
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "warning", return_value=None) as w:
            self.win._on_readd_missing_parts()
            self.assertFalse(w.called)
        self.assertEqual(
            sorted(j.slot_id for j in bm.jobs),
            ["blk01:1", "blk02:1"])
        # Each job's prompt is the (identical) text — but each is pinned
        # to its own slot identity.
        for j in bm.jobs:
            self.assertEqual(j.prompt, paras[0])

    def test_e8_partial_refusal_leaves_state_untouched(self):
        """E8: a PARTIAL repair (one slot repairable, one refused)
        appends only the provable job and touches nothing else —
        assets, slots, selections and existing rows are byte-identical."""
        text, blocks, paras = self.make_blocks_and_text(2)
        self._queue_without(blocks, paras, removed={0, 1})
        self.insert_into_block(blocks[0], " A changed sentence.")
        bm = self.win._batch_manager
        slots_before = list(self.scene.expected_audio_slots)
        assets_before = list(self.scene.audio_assets)
        sel_before = dict(self.scene.selected_block_audio)
        rows_before = list(bm.jobs)
        msg = self.readd_with_warning()
        self.assertIn("Re-added 1 of 2", msg)
        self.assertEqual(
            [j.slot_id for j in bm.jobs],
            [j.slot_id for j in rows_before] + ["blk02:1"])
        self.assertEqual(self.scene.expected_audio_slots, slots_before)
        self.assertEqual(self.scene.audio_assets, assets_before)
        self.assertEqual(self.scene.selected_block_audio, sel_before)


# ===========================================================================
# 5) TASK 2 — Combine stale-audio integrity (D1..D9)
# ===========================================================================
class TestCombineStaleAudioIntegrity(_MWHarness):

    def test_d1_audio_of_deleted_block_blocked(self):
        """D1: an existing audio asset whose Block no longer exists
        (deleted after Re-detect) is BLOCKED, not silently used."""
        text, blocks, paras = self.make_blocks_and_text(2)
        self.scene.expected_audio_slots = self.proven_slots(blocks, paras)
        self.scene.audio_assets = self.proven_assets(blocks, paras)
        self.load_editor(text, blocks)
        self.delete_block_text(blocks[1])
        slots_before = list(self.scene.expected_audio_slots)
        assets_before = list(self.scene.audio_assets)
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        self.assertIn("'Block 2'", msg)
        self.assertIn(ORPHANED_MARKER, msg)
        self.assertIn("Generate Long", msg)
        # D8 (partial): nothing mutated by the failed combine.
        self.assertEqual(self.scene.expected_audio_slots, slots_before)
        self.assertEqual(self.scene.audio_assets, assets_before)
        self.assertEqual(len(self.scene.combined_outputs), 0)
        self.assertTrue(all(os.path.isfile(
            os.path.join(self.tmp, a["output_path"]))
            for a in self.scene.audio_assets))

    def test_d2_changed_text_with_old_audio_blocked(self):
        """D2: the text changed but the old audio asset is still
        present -> BLOCKED with the changed-content reason; the asset
        survives untouched."""
        text, blocks, paras = self.make_blocks_and_text(1)
        self.scene.expected_audio_slots = self.proven_slots(blocks, paras)
        self.scene.audio_assets = self.proven_assets(blocks, paras)
        self.load_editor(text, blocks)
        self.insert_into_block(blocks[0], " The text changed here.")
        assets_before = list(self.scene.audio_assets)
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        self.assertIn("'Block 1'", msg)
        self.assertIn(CHANGED_MARKER, msg)
        self.assertIn("Regenerate", msg)
        self.assertEqual(self.scene.audio_assets, assets_before)

    def test_d3_unchanged_valid_slot_still_combines(self):
        """D3: unchanged text + valid provenance -> Combine succeeds
        (the honest no-regression for fully valid narration)."""
        text, blocks, paras = self.make_blocks_and_text(2)
        self.scene.expected_audio_slots = self.proven_slots(blocks, paras)
        self.scene.audio_assets = self.proven_assets(blocks, paras)
        self.load_editor(text, blocks)
        ok, msg = self.combine_warning()
        self.assertTrue(ok, msg)
        entry = self.scene.combined_outputs[0]
        self.assertEqual(entry["slot_count"], 2)
        self.assertEqual([s["slot_id"] for s in entry["sources"]],
                         ["blk01:1", "blk02:1"])

    def test_d4_unique_block_id_replacement_reconciles(self):
        """D4: Re-detect replaced every block id over UNCHANGED text
        with PROVEN assets -> the unique content match reconciles and
        Combine succeeds."""
        from engine.narration_blocks import PromptBlock
        text, blocks, paras = self.make_blocks_and_text(2)
        self.scene.expected_audio_slots = self.proven_slots(blocks, paras)
        self.scene.audio_assets = self.proven_assets(blocks, paras)
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
        ok, msg = self.combine_warning()
        self.assertTrue(ok, msg)
        self.assertEqual(self.scene.combined_outputs[0]["slot_count"], 2)

    def test_d5_ambiguous_repeated_text_blocked(self):
        """D5: the recorded content now appears MORE THAN ONCE in the
        current structure (block ids replaced) -> the audio cannot be
        matched unambiguously -> BLOCKED for that slot only."""
        from engine.narration_blocks import PromptBlock
        paras = ["Ambiguous deterministic sentence repeated twice.",
                 "Ambiguous deterministic sentence repeated twice."]
        text, blocks, _ = self.make_blocks_and_text(2, paras=paras)
        self.scene.expected_audio_slots = self.proven_slots(blocks, paras)
        self.scene.audio_assets = self.proven_assets(blocks, paras)
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
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        self.assertIn(AMBIGUOUS_MARKER, msg)
        self.assertIn("more than once", msg)

    def test_d6_legacy_project_blocked_with_remedy(self):
        """D6: a project recorded before content provenance (slots AND
        assets without fingerprints) -> every covered slot is BLOCKED
        with the legacy reason and the Generate Long remedy; nothing is
        deleted."""
        text, blocks, paras = self.make_blocks_and_text(2)
        from engine.audio_provenance import materialize_expected_slots
        legacy_slots = materialize_expected_slots([
            _make_part(text=para, block_id=b.id)
            for b, para in zip(blocks, paras)])
        for s in legacy_slots:
            s.pop("content_sha", None)
        self.scene.expected_audio_slots = legacy_slots
        self.scene.audio_assets = [
            self.direct_asset("blk01:1", 1),
            self.direct_asset("blk02:1", 2),
        ]
        self.load_editor(text, blocks)
        assets_before = list(self.scene.audio_assets)
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        self.assertEqual(len(msg.split("•")) - 1, 2, msg)
        self.assertIn(LEGACY_MARKER, msg)
        self.assertIn("Generate Long", msg)
        self.assertEqual(self.scene.audio_assets, assets_before)
        self.assertEqual(len(self.scene.combined_outputs), 0)

    def test_d7_d8_mixture_blocked_with_exact_reasons_nothing_mutated(self):
        """D7 + D8: valid / changed-text / deleted-block / missing-audio
        slots in ONE scene -> exactly the right blocker per slot, the
        combine aborts BEFORE assembly and mutates nothing."""
        text, blocks, paras = self.make_blocks_and_text(4)
        self.scene.expected_audio_slots = self.proven_slots(blocks, paras)
        # Block 1: valid. Block 2: changed text. Block 3: deleted block.
        # Block 4: never generated (legitimately missing audio).
        self.scene.audio_assets = [
            self.proven_assets(blocks, paras)[0],
            self.proven_assets(blocks, paras)[1],
            self.proven_assets(blocks, paras)[2],
        ]
        self.load_editor(text, blocks)
        self.insert_into_block(blocks[1], " Changed sentence.")
        # Delete Block 3 through the LIVE block model (the insertion
        # above shifted every later offset).
        live_blk03 = next(
            b for b in self.win._editor.block_manager.blocks
            if b.id == "blk03")
        self.delete_block_text(live_blk03)
        live_ids = {b.id for b in self.win._editor.block_manager.blocks}
        self.assertEqual(live_ids, {"blk01", "blk02", "blk04"})
        slots_before = list(self.scene.expected_audio_slots)
        assets_before = list(self.scene.audio_assets)
        files_before = [a["output_path"] for a in self.scene.audio_assets]
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        self.assertIn(CHANGED_MARKER, msg)       # Block 2
        self.assertIn(ORPHANED_MARKER, msg)      # Block 3
        self.assertIn("has no generated audio yet", msg)  # Block 4
        self.assertNotIn("'Block 1'", msg)       # the valid slot passes
        # D8: no mutation, no assembly.
        self.assertEqual(self.scene.expected_audio_slots, slots_before)
        self.assertEqual(self.scene.audio_assets, assets_before)
        self.assertEqual([a["output_path"] for a in self.scene.audio_assets],
                         files_before)
        self.assertEqual(len(self.scene.combined_outputs), 0)
        self.assertTrue(all(os.path.isfile(
            os.path.join(self.tmp, p)) for p in files_before))

    def test_d9_combine_succeeds_after_repair(self):
        """D9: after the user repairs the invalid slots (Generate Long
        over the current text + regenerating), Combine succeeds with
        exactly the current structure."""
        text, blocks, paras = self.make_blocks_and_text(2)
        self.scene.expected_audio_slots = self.proven_slots(blocks, paras)
        self.scene.audio_assets = self.proven_assets(blocks, paras)
        self.load_editor(text, blocks)
        # The text changes -> combine blocked (stale audio)...
        self.insert_into_block(blocks[0], " A post-generation edit.")
        ok, msg = self.combine_warning()
        self.assertFalse(ok)
        # ...the user repairs: Generate Long over the CURRENT text
        # (review mode: no destructive hidden regeneration), then
        # GENERATE CHECKED produces fresh audio for the current content.
        surviving = self.win._editor.block_manager.blocks
        cur_paras = [self.win._editor.get_text()[
            b.start_offset:b.end_offset] for b in surviving]
        parts = [_make_part(text=p, block_id=b.id,
                            block_label="Block {0}".format(i + 1))
                 for i, (b, p) in enumerate(zip(surviving, cur_paras))]
        self.start_long(parts, review=True)
        dlg = self.dialog()
        dlg._check_states = _check_dict(dlg, [True, True])
        dlg._on_start()
        self.wait_batch()
        # The historical (stale) asset is PRESERVED append-only; the
        # fresh asset for the current content is the resolved one.
        self.assertGreaterEqual(len(self.scene.audio_assets), 3)
        ok2, msg2 = self.combine_warning()
        self.assertTrue(ok2, msg2)
        entry = self.scene.combined_outputs[0]
        self.assertEqual(entry["slot_count"], 2)
        self.assertEqual([s["slot_id"] for s in entry["sources"]],
                         ["blk01:1", "blk02:1"])

    def test_selected_older_version_of_changed_text_blocked(self):
        """Explicit version selection cannot smuggle older-content
        audio into a changed narration: the resolved asset's
        fingerprint must match the CURRENT content (fail closed even
        for deliberate selections)."""
        text, blocks, paras = self.make_blocks_and_text(1)
        parts = [_make_part(text=paras[0], block_id=blocks[0].id,
                            block_label="Block 1")]
        self.load_editor(text, blocks)
        self.start_long(parts)          # v01 for the original text
        self.wait_batch()
        v01 = [a for a in self.scene.audio_assets
               if a["part_version"] == 1][0]
        # The text changes; the structure is re-materialised; the part
        # is regenerated as v02 for the NEW content.
        self.insert_into_block(blocks[0], " A post-generation edit.")
        surviving = self.win._editor.block_manager.blocks
        cur_text = self.win._editor.get_text()
        cur_paras = [cur_text[b.start_offset:b.end_offset]
                     for b in surviving]
        parts2 = [_make_part(text=p, block_id=b.id,
                             block_label="Block 1")
                  for b, p in zip(surviving, cur_paras)]
        self.start_long(parts2, review=True)
        dlg = self.dialog()
        dlg._check_states = _check_dict(dlg, [True])
        dlg._on_start()
        self.wait_batch()
        # Combine with the LATEST resolution succeeds...
        ok, msg = self.combine_warning()
        self.assertTrue(ok, msg)
        # ...but explicitly selecting the v01 (old-content) audio is
        # refused by the identity gate.
        self.win._on_use_version_requested("blk01:1", v01["id"])
        ok2, msg2 = self.combine_warning()
        self.assertFalse(ok2)
        self.assertIn(STALE_AUDIO_MARKER, msg2)
        # The selection itself is untouched (user data preserved).
        self.assertEqual(self.scene.selected_block_audio,
                         {"blk01:1": v01["id"]})


# ===========================================================================
# 6) The current-identity derivation itself
# ===========================================================================
class TestCurrentIdentityDerivation(_MWHarness):

    def test_active_scene_identities_match_materialization(self):
        """The validation pass and the Generate Long materialisation
        agree on every fingerprint (the SAME authoritative splitter)."""
        text, blocks, paras = self.make_blocks_and_text(3)
        self.load_editor(text, blocks)
        ids = self.win._combine_current_identities(self.scene)
        self.assertIsNotNone(ids)
        self.assertEqual(len(ids), 3)
        from engine.audio_provenance import part_content_sha
        for entry, (b, para) in zip(ids, zip(blocks, paras)):
            self.assertEqual(entry["slot_id"], "{0}:1".format(b.id))
            self.assertEqual(entry["block_id"], b.id)
            self.assertEqual(entry["content_sha"],
                             part_content_sha(para))

    def test_non_active_scene_returns_none(self):
        from engine.models import Scene
        other = Scene(id="sc2efgh", name="Other", project_id="proj1")
        self.assertIsNone(self.win._combine_current_identities(other))

    def test_blank_editor_is_positive_empty(self):
        text, blocks, paras = self.make_blocks_and_text(2)
        self.load_editor(text, blocks)
        ed = self.win._editor._editor
        ed.clear()
        _process(30)
        ids = self.win._combine_current_identities(self.scene)
        self.assertEqual(ids, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
