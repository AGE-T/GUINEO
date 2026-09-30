"""
GUINEO (SpeechStudio) — P3.45.2B runtime tests
================================================

DURATION ESTIMATION + SPLITTER CONSISTENCY — the canonical-estimator
and product-target contract.

Locks the contract mandated by docs/design/P3_45_2B_DURATION_ESTIMATION_
SPLITTER_CONSISTENCY.md:

    ONE canonical estimator (engine/duration_estimation):
        ESTIMATED_CHARS_PER_SECOND = 15.0   (the historical heuristic,
                                             UNCALIBRATED — single owner)
        estimate_speech_seconds(text)       (one meaning everywhere: an
                                             ESTIMATE of speech seconds,
                                             never a measurement, never
                                             a limit)

    ONE text basis per site (the text the site actually owns):
        splitter  → the plain PART text
        Preview   → the editor document's plain text (NOT the compiled
                    prompt — prepended control tokens are not speech)
        preflight → the caller-supplied request basis (reported in the
                    result; engine/output_guard imports the canonical
                    constant — no second constant exists)

    PRODUCT TARGET (not a model limit, not a runtime limit):
        parts of approximately 20-25 s of estimated speech
        = TARGET_PART_CHARS 300 (20 s) close threshold with lookahead
          merge, MAX_CHARS 400 (~26.7 s) hard cap unchanged
        - any text <= 400 chars → exactly ONE part
        - a single sentence > 400 chars → ONE honest oversized part
          (NO sub-sentence mechanism exists — no character slicing)
        - a long $SPEAKER turn → sentence-grouped into several parts,
          all replicating speaker/character/effective state

    STALENESS (proven stale paths, fixed):
        - Long-dialog part edit  → estimate recomputed (live header +
          emitted SplitPart → BatchJob.expected_duration)
        - Batch workspace prompt edit → expected_duration recomputed
          from the edited prompt (the R2 guard's expectation)

NEVER done anywhere (pinned by tests):
    - recalibrating the /15 heuristic (no measurement corpus exists)
    - character-slicing a sentence to hit the duration target
    - mutating block text/identity, slot_id, generation lineage
    - inventing a model hard limit at 20-25 s (it is a PRODUCT target;
      the real ceiling stays max_new_tokens/25 in engine/output_guard)

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_45_2b_duration_splitter_consistency.py -v
"""
from __future__ import annotations

import math
import os
import sys
import time
import types
import unittest
import tempfile
import shutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Fake torch (house pattern: test_p3_28 / test_p3_35 / test_p3_44_4 / 2A).
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

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)

# Real application theme (house pattern since P3.34).
from ui.theme import apply_theme, DEFAULT_THEME  # noqa: E402
apply_theme(APP, DEFAULT_THEME)

from engine.duration_estimation import (  # noqa: E402
    ESTIMATED_CHARS_PER_SECOND,
    estimate_speech_seconds,
)
from engine.output_guard import (  # noqa: E402
    preflight_generation_size,
    PREFLIGHT_SAFE, PREFLIGHT_WARNING, PREFLIGHT_BLOCKED,
)
from engine.narration_splitter import NarrationSplitter  # noqa: E402

SAMPLE_RATE = 24000


def make_speech(seconds: float, freq: float = 220.0) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (0.2 * np.sin(2 * math.pi * freq * t)).astype(np.float32)


class FakeHiggsModel:
    """Model-boundary fake (P3.27B/P3.28 house pattern)."""

    calls = []
    speech_s = 0.25
    delay_s = 0.0

    def generate_speech(self, text, tokenizer, *, reference_audio=None,
                        reference_sample_rate=None, reference_codes=None,
                        reference_text=None, max_new_tokens=2048,
                        temperature=1.0, top_p=None, top_k=None):
        FakeHiggsModel.calls.append(dict(text=text, max_new_tokens=max_new_tokens))
        if FakeHiggsModel.delay_s:
            time.sleep(FakeHiggsModel.delay_s)
        return make_speech(FakeHiggsModel.speech_s)


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


class ModalRecorder:
    """QMessageBox.question stand-in: records (title, text), answers."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, parent, title, text, *args, **kwargs):
        self.calls.append((title, text))
        return self.answer


def _make_part(text="A test sentence for the part.", speaker=None,
               block_id=None, est=8.0):
    from engine.narration_splitter import SplitPart
    return SplitPart(
        text=text, prompt=text, speaker=speaker or "",
        source_block_id=block_id, part_of_block=1,
        total_parts_in_block=1, block_label="",
        estimated_duration=est, char_count=len(text))


# Deterministic long-text builder: n sentences of exactly known length,
# single spaces between them (so source reconstruction is exact).
def _sentence(i):
    # "Sentence 3 talks about the calm sea and the weather." — a stable
    # ~57-char sentence shape for grouping math.
    return "Sentence {0} talks about the calm sea and the weather.".format(i)


def _long_text(n):
    return " ".join(_sentence(i) for i in range(1, n + 1))


# ===========================================================================
# 1) ESTIMATION — one canonical meaning, one constant, one basis per site
# ===========================================================================
class TestCanonicalEstimator(unittest.TestCase):
    """The single estimator module: semantics, identity, consumption."""

    def test_canonical_formula_is_the_historical_heuristic(self):
        # len/15 — UNCALIBRATED (P3.45.2B explicitly does not change it).
        self.assertEqual(ESTIMATED_CHARS_PER_SECOND, 15.0)
        self.assertEqual(estimate_speech_seconds("A" * 300), 20.0)
        self.assertEqual(estimate_speech_seconds(""), 0.0)
        self.assertEqual(estimate_speech_seconds(None), 0.0)

    def test_output_guard_constant_is_the_canonical_object(self):
        # No second constant: output_guard imports the canonical object.
        import engine.output_guard as og
        self.assertIs(og.ESTIMATED_CHARS_PER_SECOND,
                      ESTIMATED_CHARS_PER_SECOND)

    def test_preflight_consumes_the_canonical_estimate(self):
        text = "B" * 731
        verdict = preflight_generation_size(text)
        self.assertEqual(
            verdict["estimated_request_seconds"],
            round(estimate_speech_seconds(text), 2))
        self.assertEqual(verdict["chars_per_second"],
                         ESTIMATED_CHARS_PER_SECOND)
        self.assertEqual(verdict["basis_chars"], len(text))

    def test_splitter_estimates_are_canonical(self):
        # Every split path's SplitPart.estimated_duration is the canonical
        # estimate of that part's plain text (all four paths).
        s = NarrationSplitter()
        plain = _long_text(30)
        for p in s.split(text=plain):
            self.assertEqual(p.estimated_duration,
                             estimate_speech_seconds(p.text))
        raw = "<|emotion:awe|> Raw sentence one. Raw sentence two. " * 8
        for p in s.split(text=raw, raw_mode=True):
            self.assertEqual(p.estimated_duration,
                             estimate_speech_seconds(p.text))
        dial = ("$CAPTAIN:\n" + _long_text(12) + "\n\n"
                "$ENGINEER:\nFine. Roger that.")
        for p in s.split(text=dial, detect_speakers=True):
            self.assertEqual(p.estimated_duration,
                             estimate_speech_seconds(p.text))

    def test_preflight_and_splitter_agree_per_part(self):
        # For the same input semantics (the part's plain text), the
        # preflight's estimate IS the splitter's estimate.
        s = NarrationSplitter()
        for p in s.split(text=_long_text(25)):
            v = preflight_generation_size(p.text)
            self.assertEqual(v["estimated_request_seconds"],
                             round(p.estimated_duration, 2))
            self.assertEqual(v["state"], PREFLIGHT_SAFE)

    def test_target_derived_from_canonical_rate(self):
        # The splitter's char target is derived from the canonical rate
        # (seconds x chars/sec), never an independent char constant.
        s = NarrationSplitter()
        self.assertEqual(s.TARGET_PART_SECONDS, 20.0)
        self.assertEqual(s.TARGET_PART_CHARS,
                         int(20.0 * ESTIMATED_CHARS_PER_SECOND))
        self.assertEqual(s.TARGET_PART_CHARS, 300)
        self.assertEqual(s.MAX_CHARS, 400)  # hard cap unchanged (P3.44 era)

    def test_p3452a_boundary_values_unchanged(self):
        # The 2A ceiling boundary is byte-identical with the canonical
        # constant (no drift introduced by the import unification).
        self.assertEqual(preflight_generation_size("X" * 2457)["state"],
                         PREFLIGHT_SAFE)
        self.assertEqual(preflight_generation_size("X" * 2458)["state"],
                         PREFLIGHT_BLOCKED)
        self.assertEqual(
            preflight_generation_size("X" * 2460, 4100)["state"],
            PREFLIGHT_WARNING)


# ===========================================================================
# 2) SPLITTING — the 20-25 s product target with semantic boundaries
# ===========================================================================
class TestDurationTargetedGrouping(unittest.TestCase):
    """The grouping retarget: 300-char close + lookahead merge."""

    def setUp(self):
        self.splitter = NarrationSplitter()

    def test_text_below_cap_is_exactly_one_part(self):
        # Any text <= MAX_CHARS stays WHOLE (lookahead merge subsumes the
        # historical 3-sentence close): 6 sentences / 350 chars → 1 part.
        text = ". ".join(["This is a padded sentence number %d" % i
                          for i in range(6)]) + "."
        self.assertLessEqual(len(text), 400)
        parts = self.splitter.split(text=text)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].text, text)

    def test_long_text_parts_target_20_to_25_seconds(self):
        # 30 x ~57-char sentences (~1720 chars): every non-tail part is
        # in the 20-26.7 s product window (>= 300 chars, <= 400 chars).
        text = _long_text(30)
        parts = self.splitter.split(text=text)
        self.assertGreater(len(parts), 1)
        non_tail = parts[:-1]
        for p in non_tail:
            self.assertGreaterEqual(p.estimated_duration, 20.0,
                                    "part below the 20 s floor: %.1fs"
                                    % p.estimated_duration)
            self.assertLessEqual(p.estimated_duration, 400 / 15.0 + 1e-9,
                                 "part above the hard cap estimate")
        # All parts are exact source slices, sentence-final.
        for p in parts:
            self.assertIn(p.text, text)
            self.assertTrue(p.text.rstrip().endswith((".", "!", "?", "…")))

    def test_lookahead_merges_small_remainder(self):
        # 350 chars of 150/150/50: closing at 300 would leave a silly
        # 50-char part; the lookahead keeps the turn-sized text WHOLE.
        text = ("X" * 150 + ". " + "Y" * 150 + ". " + "Z" * 50 + ".")
        self.assertLessEqual(len(text), 400)
        parts = self.splitter.split(text=text)
        self.assertEqual(len(parts), 1)

    def test_exact_source_text_preserved(self):
        # Reconstruction invariant: joining the parts (boundary
        # separators dropped — the historical split-point semantics)
        # equals the source with whitespace collapsed ONLY across those
        # boundaries; every part is an exact slice; intra-part double
        # spaces and newlines are byte-preserved.
        text = ("First sentence here.  Double space kept inside.\n"
                "Newline sentence follows. ") * 6
        parts = self.splitter.split(text=text)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertIn(p.text, text)
        joined = "".join(p.text for p in parts)
        self.assertEqual("".join(joined.split()), "".join(text.split()))
        # Intra-part separator preservation: any part containing both
        # sentences of one repetition keeps the exact double space.
        for p in parts:
            if "Double space" in p.text and "First sentence" in p.text:
                self.assertIn("here.  Double", p.text)

    def test_sentence_boundaries_and_decimals_intact(self):
        text = ("A GLM 5.2-t használtam, mert gyors. Az érték 3.14 lett "
                "belőle. Látogasd meg a test.hu oldalt. U.S.A. is known "
                "for this too. Az ára 12.50 volt. ") * 3
        parts = self.splitter.split(text=text)
        self.assertGreater(len(parts), 1)
        joined = "\n".join(p.text for p in parts)
        for needle in ["5.2", "3.14", "test.hu", "U.S.A.", "12.50"]:
            self.assertIn(needle, joined)
        for p in parts:
            self.assertTrue(p.text.rstrip().endswith(".", ))

    def test_long_single_sentence_one_oversized_part_no_slicing(self):
        # NO sub-sentence mechanism exists: a >400-char sentence becomes
        # ONE honest oversized part — never character-sliced.
        long_sentence = "word " * 120  # 600 chars, no internal boundary
        text = long_sentence + " Done."
        parts = self.splitter.split(text=text)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].text, text.strip())
        self.assertEqual(parts[0].estimated_duration,
                         estimate_speech_seconds(parts[0].text))
        # The oversized part is ABOVE the 20-25 s product target...
        self.assertGreater(parts[0].estimated_duration, 25.0)
        # ...but the product target is NOT a limit: the preflight
        # (which guards the REAL ceiling, 163.84 s at default budget)
        # still classifies it SAFE — no invented 30 s-style hard limit.
        v = preflight_generation_size(parts[0].text)
        self.assertEqual(v["state"], PREFLIGHT_SAFE)
        # A sentence above the REAL ceiling stays ONE part too (the
        # documented 2A bypass: honestly surfaced as BLOCKED).
        huge = " ".join("word{0}".format(i) for i in range(600)) + "."
        parts2 = self.splitter.split(text=huge)
        self.assertEqual(len(parts2), 1)
        self.assertEqual(
            preflight_generation_size(parts2[0].text)["state"],
            PREFLIGHT_BLOCKED)

    def test_block_fast_path_one_part(self):
        # The frozen 1-block = 1-part mapping: a block <= 400 chars is
        # exactly one part; a longer block is sentence-grouped.
        from engine.narration_blocks import PromptBlock
        t_small = "s" * 393 + ". "
        b_small = PromptBlock(id="bS", start_offset=0,
                              end_offset=len(t_small))
        parts = self.splitter.split(text=t_small, blocks=[b_small])
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].source_block_id, "bS")
        self.assertEqual(parts[0].part_of_block, 1)
        # Long block: multi-part with block identity preserved.
        t_big = _long_text(30)
        b_big = PromptBlock(id="bB", start_offset=0, end_offset=len(t_big))
        parts = self.splitter.split(text=t_big, blocks=[b_big])
        self.assertGreater(len(parts), 1)
        ids = {p.source_block_id for p in parts}
        self.assertEqual(ids, {"bB"})
        self.assertEqual([p.part_of_block for p in parts],
                         list(range(1, len(parts) + 1)))
        for p in parts:
            self.assertEqual(p.total_parts_in_block, len(parts))

    def test_no_structural_corruption_block_path(self):
        # Blocks → parts: source_block_id/block_index mapping and the
        # part texts are untouched by the duration retarget.
        from engine.narration_blocks import PromptBlock
        text = ("Első blokk GLM 5.2-ről szól. Rövid. " * 3) + \
               "Második blokk 3.14-ről. " + "Harmadik blokk vége."
        b1_end = len("Első blokk GLM 5.2-ről szól. Rövid. " * 3)
        b2_end = b1_end + len("Második blokk 3.14-ről. ")
        blocks = [PromptBlock(start_offset=0, end_offset=b1_end),
                  PromptBlock(start_offset=b1_end, end_offset=b2_end),
                  PromptBlock(start_offset=b2_end, end_offset=len(text))]
        parts = self.splitter.split(text=text, blocks=blocks)
        self.assertEqual(len(parts), 3)
        self.assertEqual([p.source_block_id for p in parts],
                         [b.id for b in blocks])
        self.assertEqual([p.block_index for p in parts], [0, 1, 2])
        for p in parts:
            self.assertIn(p.text, text)


# ===========================================================================
# 3) SPEAKER TURNS — safe semantic splitting
# ===========================================================================
class TestSpeakerTurnSplitting(unittest.TestCase):

    def setUp(self):
        self.splitter = NarrationSplitter()

    def test_long_turn_split_into_target_parts_same_speaker(self):
        # A multi-sentence turn of ~860 chars → several parts, ALL with
        # the same speaker, in source order, targeting the product
        # window (the turn text is the same shape as plain text).
        turn_text = _long_text(15)                    # ~860 chars
        text = "Intro line.\n\n$CAPTAIN:\n{0}\n\n$ENGINEER:\nFine.".format(
            turn_text)
        parts = self.splitter.split(text=text, detect_speakers=True)
        captain = [p for p in parts if p.speaker == "CAPTAIN"]
        self.assertGreater(len(captain), 1)
        for p in captain:
            self.assertEqual(p.speaker, "CAPTAIN")
            self.assertEqual(p.block_label, "CAPTAIN")
            # The $SPEAKER declaration line is NEVER part of the text.
            self.assertNotIn("$CAPTAIN", p.text)
        # Part ordering: the captain parts appear in source order and
        # reconstruct the turn text exactly (separators preserved inside
        # parts, dropped at part boundaries).
        joined = "".join(p.text for p in captain)
        self.assertEqual("".join(joined.split()),
                         "".join(turn_text.split()))
        # Non-tail parts target the 20-25 s window.
        for p in captain[:-1]:
            self.assertGreaterEqual(p.estimated_duration, 20.0)
            self.assertLessEqual(p.estimated_duration, 400 / 15.0 + 1e-9)
        # Turn-internal numbering is metadata-consistent.
        self.assertEqual([p.part_of_block for p in captain],
                         list(range(1, len(captain) + 1)))
        for p in captain:
            self.assertEqual(p.total_parts_in_block, len(captain))
        # The small neighbour turn is untouched.
        engineer = [p for p in parts if p.speaker == "ENGINEER"]
        self.assertEqual(len(engineer), 1)
        self.assertEqual(engineer[0].text, "Fine.")

    def test_short_turn_stays_one_part(self):
        text = ("$CAPTAIN:\nWe have to leave now.\n\n"
                "$ENGINEER:\nThe engine is not ready.\n\n")
        parts = self.splitter.split(text=text, detect_speakers=True)
        self.assertEqual(len(parts), 2)
        self.assertEqual([p.speaker for p in parts],
                         ["CAPTAIN", "ENGINEER"])
        self.assertEqual([p.part_of_block for p in parts], [1, 1])
        self.assertEqual([p.total_parts_in_block for p in parts], [1, 1])

    def test_turn_oversized_single_sentence_remains_one_part(self):
        # A turn consisting of ONE unpunctuated giant sentence has no
        # semantic split point → ONE oversized part (the documented 2A
        # bypass case — now honestly surfaced by the preflight).
        giant = " ".join("said{0}".format(i) for i in range(800))
        text = "Intro.\n\n$CAPTAIN:\n{0}\n\n$ENGINEER:\nFine.".format(giant)
        parts = self.splitter.split(text=text, detect_speakers=True)
        captain = [p for p in parts if p.speaker == "CAPTAIN"]
        self.assertEqual(len(captain), 1)
        self.assertEqual(
            preflight_generation_size(captain[0].text)["state"],
            PREFLIGHT_BLOCKED)

    def test_turn_state_replicated_on_all_parts(self):
        # Effective semantic state + character propagate to EVERY part
        # of a split turn (each part is an independent generation call).
        from engine.narration_blocks import PromptBlock
        turn_text = _long_text(15)
        text = "Intro.\n\n$CAPTAIN:\n{0}".format(turn_text)
        block = PromptBlock(id="bC", start_offset=0, end_offset=len(text),
                            character_id="char_captain", emotion="Fear",
                            style="Whispering")
        parts = self.splitter.split(text=text, blocks=[block],
                                    detect_speakers=True,
                                    global_emotion="Elation")
        captain = [p for p in parts if p.speaker == "CAPTAIN"]
        self.assertGreater(len(captain), 1)
        for p in captain:
            self.assertEqual(p.character_id, "char_captain")
            self.assertEqual(p.eff_emotion, "Fear")      # block override
            self.assertEqual(p.eff_style, "Whispering")  # block override
            # Every part carries the compiled prompt with its tokens
            # (independent self-contained generation call).
            self.assertTrue(p.prompt)
            self.assertIn("fear", p.prompt.lower())

    def test_turn_slot_identity_is_plain(self):
        # Speaker parts are PLAIN slots (slot_id = plain:{part_index});
        # splitting a turn never creates block-slot or new identity.
        from engine.audio_provenance import materialize_expected_slots
        turn_text = _long_text(15)
        text = "Intro.\n\n$CAPTAIN:\n{0}\n\n$ENGINEER:\nFine.".format(
            turn_text)
        parts = self.splitter.split(text=text, detect_speakers=True)
        slots = materialize_expected_slots(parts)
        self.assertEqual(len(slots), len(parts))
        for i, slot in enumerate(slots, start=1):
            self.assertEqual(slot["slot_id"], "plain:{0}".format(i))
            self.assertIsNone(slot["block_id"])
        captain_idx = [i for i, p in enumerate(parts, start=1)
                       if p.speaker == "CAPTAIN"]
        self.assertGreater(len(captain_idx), 1)
        for i in captain_idx:
            self.assertEqual(slots[i - 1]["speaker"], "CAPTAIN")


# ===========================================================================
# 4) STALENESS — the proven stale paths, fixed
# ===========================================================================
class _P345BHarness(unittest.TestCase):
    """Real MainWindow + Engine with the model boundary faked (2A pattern)."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p3452b_")
        from engine.engine import Engine
        from ui.main_window import MainWindow
        from engine.project_manager import ProjectManager
        from engine.models import Project, Scene
        from engine.batch_manager import BatchManager
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
            patch.object(BatchManager, "INTER_JOB_DELAY_MS", 60),
        ]
        for p in self.patchers:
            p.start()
        self.engine.play_audio = lambda p: None
        self.win = MainWindow(self.engine)
        self.pm = ProjectManager(os.path.join(self.tmp, "projects"))
        self.win._project_manager = self.pm
        self.pa = Project(name="Alpha", id="projA")
        self.scene = Scene(id="scA00001", name="01 Scene",
                           project_id="projA")
        self.pa.add_scene(self.scene)
        self.pm.save_project(self.pa)
        self.win._active_project = self.pa
        self.win._active_scene = self.scene
        self.win._current_project = "Alpha"
        FakeHiggsModel.calls = []
        FakeHiggsModel.speech_s = 0.25
        FakeHiggsModel.delay_s = 0.0

    def tearDown(self):
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
        except Exception:
            pass
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        try:
            _process(60)
        except Exception:
            pass
        self.win = None
        self.engine = None
        self.pm = None
        self.pa = None
        self.scene = None
        self.tmp = None
        self.patchers = None
        import gc
        gc.collect()

    def params(self, max_new_tokens=4096):
        from engine.models import GenerationParameters
        return GenerationParameters(
            temperature=0.95, top_p=0.95, top_k=300,
            max_new_tokens=max_new_tokens, seed=None, append_silence=0.3,
            normalize_output=False, auto_play=False)

    def wait_batch(self, timeout=60):
        bm = self.win._batch_manager
        t0 = time.time()
        while bm.is_running and time.time() - t0 < timeout:
            _process(20)
        _process(250)


class TestPreviewStatsAgreeWithSplitter(_P345BHarness):
    """The Preview 'Est. duration' is the canonical estimate of the
    document's plain text — the same quantity/basis as the splitter's
    part estimates (previously the TOKENIZED prompt: +25 % for a single
    Fear block)."""

    def _enter_preview_mode(self):
        from ui.panels.narration_editor import NarrationEditor
        self.win._editor._set_mode(NarrationEditor.MODE_PREVIEW)

    def _stats_text(self):
        return self.win._editor._stats_label.text()

    def test_preview_stats_use_plain_text_basis(self):
        text = ("Hello there. General Kenobi! "
                "<|emotion:awe|> You are a bold one.")
        self.win._editor.set_text(text)
        self._enter_preview_mode()
        # Global emotion Fear: the compiled prompt gains a PREPENDED
        # token (<|emotion:fear|>) that is NOT speech.
        self.win._editor.set_global_defaults(emotion="Fear")
        stats = self._stats_text()
        # The estimate equals the canonical estimate of the PLAIN text.
        self.assertIn("~{0:.0f}s".format(estimate_speech_seconds(text)),
                      stats)
        # The prompt length still describes the prompt (its own labelled
        # quantity) — tokens included there, but never in the estimate.
        from engine.prompt_builder import PromptBuilder
        prompt = PromptBuilder().build(
            text, emotion="Fear").final_prompt
        self.assertIn("Prompt length: {0} chars".format(len(prompt)),
                      stats)
        self.assertGreater(len(prompt), len(text))

    def test_preview_stats_and_splitter_agree_for_same_text(self):
        # The same document text: the stats estimate == the sum of the
        # splitter part estimates for that text (same quantity, same
        # basis — modulo dropped boundary separators).
        text = _long_text(30)
        self.win._editor.set_text(text)
        self._enter_preview_mode()
        self.win._editor.set_global_defaults(emotion="Fear")
        stats_est = estimate_speech_seconds(text)
        parts = NarrationSplitter().split(text=text)
        parts_est = sum(p.estimated_duration for p in parts)
        self.assertAlmostEqual(stats_est, parts_est, delta=3.0)

    def test_text_change_recalculates_estimate(self):
        self._enter_preview_mode()
        self.win._editor.set_text(_long_text(4))
        before = self._stats_text()
        self.assertIn("~{0:.0f}s".format(
            estimate_speech_seconds(_long_text(4))), before)
        self.win._editor.set_text(_long_text(8))
        after = self._stats_text()
        self.assertIn("~{0:.0f}s".format(
            estimate_speech_seconds(_long_text(8))), after)


class TestLongDialogLiveEstimates(_P345BHarness):
    """The dialog's per-part and aggregate estimates refresh live and
    the emitted SplitPart carries the canonical estimate of the EDITED
    text (previously frozen at split time → stale
    BatchJob.expected_duration)."""

    def _dialog(self, parts):
        from ui.panels.long_narration_dialog import LongNarrationDialog
        return LongNarrationDialog(
            parts, "", [], None, {}, allow_sfx=True, parent=None,
            generation_parameters=self.params())

    def _header_texts(self, dlg):
        return [lbl.text() for lbl in dlg.findChildren(QLabel)]

    def test_split_time_header_shows_canonical_estimate(self):
        text = _long_text(4)  # one part
        parts = NarrationSplitter().split(text=text)
        dlg = self._dialog(parts)
        joined = "\n".join(self._header_texts(dlg))
        self.assertIn("~{0:.0f}s".format(
            estimate_speech_seconds(parts[0].text)), joined)
        # chars on the SAME plain-text basis as the estimate.
        self.assertIn("{0} chars".format(len(parts[0].text)), joined)
        dlg.close()

    def test_edit_updates_part_header_live(self):
        text = _long_text(4)
        parts = NarrationSplitter().split(text=text)
        dlg = self._dialog(parts)
        new_text = _long_text(12)
        dlg._editors[0].setPlainText(new_text)
        _process(30)
        header = dlg._part_headers[0].text()
        self.assertIn("~{0:.0f}s".format(estimate_speech_seconds(new_text)),
                      header)
        self.assertIn("{0} chars".format(len(new_text)), header)
        # The part object itself is NOT mutated mid-session (the emit
        # path recomputes it — see the generate test below).
        self.assertNotEqual(parts[0].estimated_duration,
                            estimate_speech_seconds(new_text))
        dlg.close()

    def test_edit_updates_aggregate_header_live(self):
        p1 = _make_part(_long_text(4))
        p2 = _make_part(_long_text(4))
        dlg = self._dialog([p1, p2])
        total_before = estimate_speech_seconds(p1.text) + \
            estimate_speech_seconds(p2.text)
        self.assertIn("~{0:.0f}s".format(total_before),
                      dlg._total_header.text())
        new_text = _long_text(12)
        dlg._editors[1].setPlainText(new_text)
        _process(30)
        total_after = estimate_speech_seconds(p1.text) + \
            estimate_speech_seconds(new_text)
        self.assertIn("~{0:.0f}s".format(total_after),
                      dlg._total_header.text())
        dlg.close()

    def test_edit_reclassifies_oversized_marker_and_limit_note(self):
        # A blocked part edited down to safe: the ⚠ marker disappears
        # from the header and the limit note hides — the numbers the
        # user sees are truthful after the edit. (isHidden() is used —
        # the dialog is never shown in the harness, so isVisible()
        # would be False even for a shown label.)
        parts = [_make_part("huge " * 700)]        # ~3500 chars → blocked
        dlg = self._dialog(parts)
        joined_before = "\n".join(self._header_texts(dlg))
        self.assertIn("exceeds the ~164s single-output limit",
                      joined_before)
        self.assertFalse(dlg._limit_note.isHidden())
        dlg._editors[0].setPlainText(_long_text(4))  # safe now
        _process(30)
        header = dlg._part_headers[0].text()
        self.assertNotIn("exceeds", header)
        self.assertTrue(dlg._limit_note.isHidden())
        dlg.close()

    def test_generate_emits_canonical_estimate_of_edited_text(self):
        # THE staleness fix: the emitted SplitPart.estimated_duration
        # (→ BatchJob.expected_duration → output guard R2) is computed
        # from the EDITED text, not the split-time text.
        parts = [NarrationSplitter().split(text=_long_text(4))[0]]
        dlg = self._dialog(parts)
        new_text = _long_text(10)
        dlg._editors[0].setPlainText(new_text)
        emitted = []
        dlg.generate_requested.connect(
            lambda p, m: emitted.append((p, m)))
        dlg._on_generate()
        self.assertEqual(len(emitted), 1)
        part = emitted[0][0][0]
        self.assertEqual(part.text, new_text)
        self.assertEqual(part.estimated_duration,
                         estimate_speech_seconds(new_text))
        # The prompt is rebuilt and char_count tracks it (existing 2A
        # semantics preserved).
        self.assertGreaterEqual(part.char_count, len(part.prompt))
        dlg.close()

    def test_generate_long_end_to_end_expected_duration(self):
        # Full path: splitter → dialog → edit → Generate →
        # _start_long_narration → BatchJob.expected_duration is the
        # canonical estimate of the edited text.
        from engine.narration_splitter import NarrationSplitter as NS
        parts = NS().split(text=_long_text(4))
        new_text = _long_text(10)
        parts[0].text = new_text                     # simulate the emit
        parts[0].prompt = new_text
        parts[0].estimated_duration = estimate_speech_seconds(new_text)
        parts[0].char_count = len(new_text)
        self.win._start_long_narration(parts, None, self.params(), {},
                                       review_mode=True)
        _process(80)
        job = self.win._batch_manager.jobs[0]
        self.assertAlmostEqual(job.expected_duration,
                               estimate_speech_seconds(new_text),
                               places=6)
        # The request forwards the same expectation (guard R2 basis).
        req = job.to_request()
        self.assertAlmostEqual(req.expected_duration,
                               estimate_speech_seconds(new_text),
                               places=6)


class TestBatchWorkspaceStaleness(_P345BHarness):
    """Batch prompt edit recomputes expected_duration (the R2 guard's
    expectation tracked the ORIGINAL split-time text forever before)."""

    def _queued_job(self):
        parts = [_make_part("Short original text. " * 3, est=6.0)]
        self.win._start_long_narration(parts, None, self.params(), {},
                                       review_mode=True)
        _process(80)
        return self.win._batch_manager.jobs[0]

    def test_prompt_edit_recomputes_expected_duration(self):
        job = self._queued_job()
        self.assertEqual(job.expected_duration, 6.0)
        # The workspace edit dialog writes the new prompt...
        from ui.panels.batch_generation import JobEditDialog
        dlg = JobEditDialog(job, [], parent=None)
        new_prompt = "Edited and much longer prompt text. " * 10
        dlg._prompt.setText(new_prompt)
        dlg.apply_to_job()
        self.assertEqual(job.prompt, new_prompt)
        # ...and the expectation is the canonical estimate OF THE NEW
        # PROMPT (the request-text basis of this workspace).
        self.assertEqual(job.expected_duration,
                         estimate_speech_seconds(new_prompt))
        # The request (guard R2 basis) sees the fresh value.
        self.assertEqual(job.to_request().expected_duration,
                         estimate_speech_seconds(new_prompt))

    def test_queue_yaml_round_trip_keeps_consistency(self):
        # Save/load does not create semantic divergence: the persisted
        # expected_duration matches the persisted prompt.
        job = self._queued_job()
        from ui.panels.batch_generation import JobEditDialog
        dlg = JobEditDialog(job, [], parent=None)
        new_prompt = "Round trip prompt. " * 8
        dlg._prompt.setText(new_prompt)
        dlg.apply_to_job()
        data = job.to_dict()
        self.assertEqual(data["expected_duration"],
                         estimate_speech_seconds(data["prompt"]))
        from engine.batch_manager import BatchJob
        restored = BatchJob.from_dict(data)
        self.assertEqual(restored.expected_duration,
                         estimate_speech_seconds(restored.prompt))


class TestNoMutationInvariants(_P345BHarness):
    """The splitter retarget never mutates the caller's text/blocks."""

    def test_split_does_not_mutate_input_text_or_blocks(self):
        from engine.narration_blocks import PromptBlock
        text = _long_text(30)
        blocks = [PromptBlock(id="bK", start_offset=0, end_offset=len(text),
                              emotion="Fear")]
        text_copy, blocks_copy = text, list(blocks)
        parts = NarrationSplitter().split(text=text, blocks=blocks,
                                          global_emotion="Elation")
        self.assertGreater(len(parts), 1)
        self.assertEqual(text, text_copy)
        self.assertEqual(blocks, blocks_copy)
        self.assertEqual(blocks[0].start_offset, 0)
        self.assertEqual(blocks[0].end_offset, len(text))

    def test_speaker_turn_split_preserves_dollar_speaker_syntax(self):
        # The $SPEAKER: declaration lines survive in the SOURCE; every
        # part text excludes them; nothing is rewritten in the source.
        turn_text = _long_text(15)
        text = "Intro line.\n\n$CAPTAIN:\n{0}\n\n$ENGINEER:\nFine.".format(
            turn_text)
        parts = NarrationSplitter().split(text=text, detect_speakers=True)
        for p in parts:
            self.assertNotIn("$", p.text)
        # Source untouched (split operates on its own slices).
        self.assertIn("$CAPTAIN:", text)
        self.assertIn("$ENGINEER:", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
