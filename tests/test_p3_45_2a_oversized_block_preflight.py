"""
GUINEO (SpeechStudio) — P3.45.2A runtime tests
================================================

OVERSIZED BLOCK PREFLIGHT — the single-output ceiling contract.

Locks the contract mandated by docs/design/P3_45_2A_OVERSIZED_BLOCK_
PREFLIGHT.md:

    effective max_new_tokens / HIGGS_FRAME_RATE (25 fps)   = ceiling
    default 4096 tokens                                   = 163.84 s

    preflight_generation_size(text, max_new_tokens)  →  structured verdict
        SAFE      estimate < ceiling           → no UI noise, path unchanged
        WARNING   estimate == ceiling exactly  → informed, no modal
        BLOCKED   estimate > ceiling           → ONE confirmation before
                                                  anything is submitted or
                                                  mutated; the user decides

The estimate basis is the project's EXISTING ~15 chars/sec heuristic
(P3.45.2B territory — NOT redesigned here). The boundary is decided by
EXACT rational arithmetic (chars*25 vs tokens*15) — no float equality.

Protected paths proven here (through the REAL application code):
    1. single Generate           (MainWindow._start_generation)
    2. Generate Long preview      (LongNarrationDialog markers + confirm)
    3. long single sentence       (NarrationSplitter uncapped-sentence
                                   bypass — now preflighted as a part)
    4. speaker-turn               (NarrationSplitter $SPEAKER bypass —
                                   now preflighted as a part)
    5. Batch selective start      (MainWindow._on_generate_selected)
    6. Batch manual start         (BatchGenerationDialog._on_start)
    7. per-row regeneration       (BatchGenerationDialog._on_regen —
                                   verdict folded into the EXISTING
                                   confirmation, no second modal)

NEVER done anywhere (pinned by tests):
    - auto-splitting / rewriting / re-chunking user text
    - mutating blocks, offsets, slot ids, queue order, parameters,
      versions, filenames or assets
    - duplicating the limit math outside engine/output_guard.py

Truncation metadata (documented limitation, pinned): the model API
returns only a waveform — no finish_reason / token_count / frame_count
exists anywhere in the chain, so post-generation truncation detection
beyond the P3.27B R1/R2/R3 guards is NOT faked here. The preflight's
maximum_output_seconds and detect_output_anomaly's token_ceiling_s are
proven to be the SAME arithmetic (one source of truth).

Modal interception: QMessageBox.question is replaced by a recorder that
returns the decision under test and captures (title, text) — the tests
assert the MESSAGE CONTENT (truthful numbers), not just that a dialog
method was called.

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_45_2a_oversized_block_preflight.py -v
"""
from __future__ import annotations

import copy
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

# Fake torch (house pattern: test_p3_28 / test_p3_35 / test_p3_44_4).
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
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)

# Real application theme (house pattern since P3.34).
from ui.theme import apply_theme, DEFAULT_THEME  # noqa: E402
apply_theme(APP, DEFAULT_THEME)

from engine.output_guard import (  # noqa: E402
    preflight_generation_size, preflight_display_message,
    preflight_summary_line, detect_output_anomaly,
    PREFLIGHT_SAFE, PREFLIGHT_WARNING, PREFLIGHT_BLOCKED,
    HIGGS_FRAME_RATE, ESTIMATED_CHARS_PER_SECOND,
)

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


# Boundary arithmetic reference (exact integers):
#   blocked  <=> chars * 25 >  tokens * 15
#   warning  <=> chars * 25 == tokens * 15
#   safe     <=> chars * 25 <  tokens * 15
# Default 4096 tokens: 4096*15 = 61440; 61440/25 = 2457.6 -> the tightest
# integer pair around the default boundary is 2457 (safe) / 2458 (blocked).
# Non-default 4100 tokens: 4100*15 = 61500 = 2460*25 -> EXACT warning pair.
SAFE_CHARS_DEFAULT = 2457
BLOCKED_CHARS_DEFAULT = 2458
WARN_CHARS_4100 = 2460


# ===========================================================================
# 1) UNIT CONTRACT — input → effective parameters → preflight result
# ===========================================================================
class TestPreflightUnitContract(unittest.TestCase):
    """The pure classification function (no Qt, no engine state)."""

    def test_below_hard_limit_is_safe(self):
        r = preflight_generation_size("A" * 400, 4096)
        self.assertEqual(r["state"], PREFLIGHT_SAFE)
        self.assertEqual(r["effective_max_new_tokens"], 4096)
        self.assertEqual(r["effective_fps"], 25)
        self.assertEqual(r["maximum_output_seconds"], 163.84)
        self.assertEqual(r["estimated_request_seconds"], 26.67)
        self.assertEqual(r["input_basis"], "text_length")
        self.assertEqual(r["basis_chars"], 400)
        self.assertEqual(r["chars_per_second"], 15.0)
        self.assertEqual(r["parts_required"], 1)
        self.assertEqual(r["reasons"], [])
        self.assertEqual(r["display_message"], "")

    def test_safe_at_tightest_default_boundary(self):
        # 2457*25 = 61425 < 61440 — the last safe integer at 4096.
        r = preflight_generation_size("A" * SAFE_CHARS_DEFAULT, 4096)
        self.assertEqual(r["state"], PREFLIGHT_SAFE)

    def test_blocked_at_tightest_default_boundary(self):
        # 2458*25 = 61450 > 61440 — the first blocked integer at 4096.
        r = preflight_generation_size("A" * BLOCKED_CHARS_DEFAULT, 4096)
        self.assertEqual(r["state"], PREFLIGHT_BLOCKED)
        self.assertEqual(r["parts_required"], 2)

    def test_exact_boundary_is_warning_deterministic(self):
        # 2460*25 == 4100*15 == 61500 — the exact rational boundary.
        r = preflight_generation_size("A" * WARN_CHARS_4100, 4100)
        self.assertEqual(r["state"], PREFLIGHT_WARNING)
        self.assertEqual(r["maximum_output_seconds"], 164.0)
        self.assertEqual(r["estimated_request_seconds"], 164.0)
        # One char below / above the exact boundary is deterministic.
        self.assertEqual(
            preflight_generation_size("A" * 2459, 4100)["state"],
            PREFLIGHT_SAFE)
        self.assertEqual(
            preflight_generation_size("A" * 2461, 4100)["state"],
            PREFLIGHT_BLOCKED)

    def test_above_hard_limit_message_is_truthful(self):
        r = preflight_generation_size("B" * 3000, 4096)
        self.assertEqual(r["state"], PREFLIGHT_BLOCKED)
        self.assertEqual(r["estimated_request_seconds"], 200.0)
        self.assertEqual(r["parts_required"], 2)
        msg = r["display_message"]
        self.assertIn("~200s", msg)
        self.assertIn("3000 chars", msg)
        self.assertIn("~164s", msg)
        self.assertIn("4096 tokens", msg)
        self.assertIn("25 frames/sec", msg)
        self.assertIn("at least 2 parts", msg)
        self.assertIn("heuristic", msg)  # no certainty claimed
        line = preflight_summary_line(r, "CAPTAIN: 2")
        self.assertIn("~200s", line)
        self.assertIn("~164s", line)
        self.assertIn("2 parts", line)
        # The warning-state message says "exactly at the limit".
        w = preflight_generation_size("A" * WARN_CHARS_4100, 4100)
        self.assertIn("exactly at the generation limit",
                      preflight_display_message(w, "This block"))

    def test_effective_max_new_tokens_changes_the_ceiling(self):
        # 3000 chars = 200s: blocked at 4096, safe at 8192 (327.68s).
        r8192 = preflight_generation_size("B" * 3000, 8192)
        self.assertEqual(r8192["state"], PREFLIGHT_SAFE)
        self.assertEqual(r8192["maximum_output_seconds"], 327.68)
        # 1500 chars = 100s: safe at 4096, blocked at 2048 (81.92s).
        self.assertEqual(
            preflight_generation_size("C" * 1500, 2048)["state"],
            PREFLIGHT_BLOCKED)
        self.assertEqual(
            preflight_generation_size("C" * 1500, 2048)["maximum_output_seconds"],
            81.92)
        # Legacy minimum budget: 128 tokens -> 5.12s ceiling.
        self.assertEqual(
            preflight_generation_size("C" * 1500, 128)["maximum_output_seconds"],
            5.12)

    def test_none_tokens_resolves_configured_default(self):
        from engine.models import GenerationParameters
        r = preflight_generation_size("B" * 3000, None)
        self.assertEqual(r["effective_max_new_tokens"],
                         GenerationParameters().max_new_tokens)
        self.assertEqual(r["state"], PREFLIGHT_BLOCKED)

    def test_no_text_basis_is_safe_with_reason(self):
        r = preflight_generation_size(None, 4096)
        self.assertEqual(r["state"], PREFLIGHT_SAFE)
        self.assertEqual(r["input_basis"], "none")
        self.assertIsNone(r["basis_chars"])
        self.assertIsNone(r["estimated_request_seconds"])
        self.assertTrue(r["reasons"])  # evidence-first, never silent
        self.assertEqual(r["display_message"], "")

    def test_parts_required_arithmetic(self):
        # 6000 chars = 400s; 400/163.84 = 2.441 -> 3 parts.
        self.assertEqual(
            preflight_generation_size("E" * 6000, 4096)["parts_required"], 3)
        # Exactly at the boundary needs exactly 1 part (no margin).
        self.assertEqual(
            preflight_generation_size("A" * WARN_CHARS_4100, 4100)
            ["parts_required"], 1)

    def test_deterministic_repeated_calls(self):
        a = preflight_generation_size("D" * 2500, 4096)
        b = preflight_generation_size("D" * 2500, 4096)
        self.assertEqual(a, b)

    def test_single_source_with_the_post_generation_guard(self):
        # The preflight ceiling and detect_output_anomaly's token_ceiling_s
        # are the SAME arithmetic (one source of truth in output_guard).
        # Use a waveform analysis that actually TRIGGERS the R1 runaway
        # signature so the anomaly dict (and its token_ceiling_s) exists.
        analysis = {"total_s": 163.56, "speech_s": 30.48,
                    "trailing_silence_s": 133.08, "tail_rms_dbfs": -80.0,
                    "tail_samples": 1, "sample_rate": 24000}
        anomaly = detect_output_anomaly(
            analysis, expected_duration_s=20.0, max_new_tokens=4096)
        self.assertIsNotNone(anomaly)  # R1 + R2 fire on the P3.27B defect
        self.assertEqual(anomaly["token_ceiling_s"], 163.84)
        self.assertEqual(
            anomaly["token_ceiling_s"],
            preflight_generation_size("x", 4096)["maximum_output_seconds"])
        # And the raw constants:
        self.assertEqual(4096 / HIGGS_FRAME_RATE, 163.84)
        self.assertEqual(ESTIMATED_CHARS_PER_SECOND, 15.0)


# ===========================================================================
# 2) SPLITTER BYPASS PATHS — the oversized request reaches the preflight
#    as a real SplitPart (input → effective parameters → result)
# ===========================================================================
class TestSplitterBypassPaths(unittest.TestCase):
    """The two known silent-bypass generators now produce parts that the
    preflight classifies — the bypass itself is documented as unchanged
    (P3.45.2A never re-chunks), the PROTECTION is the classification."""

    def test_oversized_single_sentence_part_is_blocked(self):
        from engine.narration_splitter import NarrationSplitter
        long_sentence = " ".join(
            "word{0}".format(i) for i in range(600)) + "."   # ~4200 chars
        parts = NarrationSplitter().split(text=long_sentence)
        self.assertEqual(len(parts), 1)  # the uncapped-sentence bypass
        verdict = preflight_generation_size(parts[0].text, 4096)
        self.assertEqual(verdict["state"], PREFLIGHT_BLOCKED)
        self.assertGreater(verdict["estimated_request_seconds"],
                           verdict["maximum_output_seconds"])

    def test_oversized_sentence_inside_block_is_blocked(self):
        from engine.narration_splitter import NarrationSplitter
        from engine.narration_blocks import PromptBlock
        long_sentence = " ".join(
            "term{0}".format(i) for i in range(700)) + "."
        block = PromptBlock(id="b1", start_offset=0,
                            end_offset=len(long_sentence))
        parts = NarrationSplitter().split(
            text=long_sentence, blocks=[block])
        self.assertEqual(len(parts), 1)
        self.assertEqual(
            preflight_generation_size(parts[0].text, 4096)["state"],
            PREFLIGHT_BLOCKED)

    def test_speaker_turn_part_is_blocked(self):
        from engine.narration_splitter import NarrationSplitter
        turn = " ".join("said{0}".format(i) for i in range(800))
        text = "Intro sentence.\n\n$CAPTAIN:\n{0}\n\n$ENGINEER:\nFine."
        parts = NarrationSplitter().split(
            text=text.format(turn), detect_speakers=True)
        # One part per speaker turn; the CAPTAIN turn is the giant one.
        captain = [p for p in parts if p.speaker == "CAPTAIN"]
        self.assertEqual(len(captain), 1)  # the uncapped speaker bypass
        verdict = preflight_generation_size(captain[0].text, 4096)
        self.assertEqual(verdict["state"], PREFLIGHT_BLOCKED)
        # The neighbouring small turn stays safe (no neighbour corruption).
        engineer = [p for p in parts if p.speaker == "ENGINEER"]
        self.assertEqual(
            preflight_generation_size(engineer[0].text, 4096)["state"],
            PREFLIGHT_SAFE)

    def test_normal_split_parts_stay_safe(self):
        from engine.narration_splitter import NarrationSplitter
        text = ("One short sentence. Two short sentence. "
                "Three short sentence ends here.")
        parts = NarrationSplitter().split(text=text)
        self.assertTrue(parts)
        for p in parts:
            self.assertEqual(
                preflight_generation_size(p.text, 4096)["state"],
                PREFLIGHT_SAFE)


# ===========================================================================
# Harness — real MainWindow + Engine, model boundary faked (P3.44.4 pattern)
# ===========================================================================
class _P345AHarness(unittest.TestCase):
    """Real MainWindow + Engine with the model boundary faked."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p3452a_")
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

    # -- helpers ---------------------------------------------------------
    def params(self, max_new_tokens=4096):
        from engine.models import GenerationParameters
        return GenerationParameters(
            temperature=0.95, top_p=0.95, top_k=300,
            max_new_tokens=max_new_tokens, seed=None, append_silence=0.3,
            normalize_output=False, auto_play=False)

    def queue_scene_batch(self, texts, review=True, max_new_tokens=4096):
        """Queue a scene batch (one job per text) via the production path."""
        parts = [_make_part(t) for t in texts]
        self.win._start_long_narration(parts, None,
                                       self.params(max_new_tokens), {},
                                       review_mode=review)
        return self.win._batch_dialog

    def wait_batch(self, timeout=60):
        bm = self.win._batch_manager
        t0 = time.time()
        while bm.is_running and time.time() - t0 < timeout:
            _process(20)
        _process(250)

    def snapshot(self):
        """Full no-mutation snapshot of the batch workspace + scene."""
        bm = self.win._batch_manager
        return {
            "jobs": [j.to_dict() for j in bm.jobs],
            "slots": copy.deepcopy(self.scene.expected_audio_slots),
            "scene_status": self.scene.status,
        }


# ===========================================================================
# 3) SINGLE GENERATE PATH (real MainWindow pipeline + modal recorder)
# ===========================================================================
class TestSingleGeneratePath(_P345AHarness):

    def test_safe_prompt_generates_normally_no_modal(self):
        from unittest.mock import patch
        text = "A calm sentence that fits easily within one output."
        self.win._editor.set_text(text)
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        with patch.object(QMessageBox, "question", rec):
            self.win._on_generate()
        self.wait_batch()
        # No confirmation was needed; the generation ran unchanged.
        self.assertEqual(len(rec.calls), 0)
        self.assertEqual(len(FakeHiggsModel.calls), 1)
        self.assertIn("calm sentence", FakeHiggsModel.calls[0]["text"])

    def test_oversized_prompt_blocked_declined_nothing_submitted(self):
        from unittest.mock import patch
        from engine.batch_manager import JobStatus  # noqa: F401
        text = "oversized " * 350            # ~3500 chars -> ~233s
        self.win._editor.set_text(text)
        status_before = self.scene.status
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        with patch.object(QMessageBox, "question", rec):
            self.win._on_generate()
        _process(200)
        # ONE truthful confirmation was shown ...
        self.assertEqual(len(rec.calls), 1)
        title, msg = rec.calls[0]
        self.assertIn("exceed", msg.lower())
        self.assertIn("~233s", msg)
        self.assertIn("~164s", msg)
        self.assertIn("4096 tokens", msg)
        self.assertIn("25 frames/sec", msg)
        self.assertIn("heuristic", msg)
        # ... and declining submitted NOTHING: no model call, no scene
        # status mutation (the abort happens before any state change).
        self.assertEqual(FakeHiggsModel.calls, [])
        self.assertEqual(self.scene.status, status_before)
        self.assertFalse(self.engine.is_generating)

    def test_oversized_prompt_acknowledged_proceeds_unchanged(self):
        from unittest.mock import patch
        text = "acknowledged " * 320           # ~4160 chars
        self.win._editor.set_text(text)
        rec = ModalRecorder(QMessageBox.StandardButton.Yes)
        with patch.object(QMessageBox, "question", rec):
            self.win._on_generate()
        self.wait_batch()
        # The user override path: exactly one confirmation, then the
        # generation ran with the prompt UNCHANGED (never rewritten).
        self.assertEqual(len(rec.calls), 1)
        self.assertEqual(len(FakeHiggsModel.calls), 1)
        self.assertIn("acknowledged ", FakeHiggsModel.calls[0]["text"])
        self.assertTrue(FakeHiggsModel.calls[0]["text"].startswith("<|")
                        or "acknowledged" in FakeHiggsModel.calls[0]["text"])

    def test_effective_budget_from_control_panel_is_used(self):
        from unittest.mock import patch
        # 8192 tokens raises the ceiling to 327.68s: a ~233s prompt is
        # then SAFE — no modal at all (the effective value, not a
        # constant, drives the classification). RightPanel delegates to
        # AdvancedView → GenerationSection, whose _max_tokens spinbox is
        # the REAL user-facing budget control — this is the actual
        # effective-parameter path (no test-only shortcut).
        spin = self.win._control_panel._advanced._generation._max_tokens
        spin.setValue(8192)
        self.assertEqual(
            self.win._control_panel.get_parameters().max_new_tokens, 8192)
        text = "oversized " * 350
        self.win._editor.set_text(text)
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        with patch.object(QMessageBox, "question", rec):
            self.win._on_generate()
        self.wait_batch()
        self.assertEqual(len(rec.calls), 0)
        self.assertEqual(len(FakeHiggsModel.calls), 1)
        self.assertEqual(FakeHiggsModel.calls[0]["max_new_tokens"], 8192)


# ===========================================================================
# 4) GENERATE LONG DIALOG (real splitter → real dialog → Generate click)
# ===========================================================================
class TestLongNarrationDialogPreflight(_P345AHarness):

    def _dialog(self, parts, params=None):
        from ui.panels.long_narration_dialog import LongNarrationDialog
        dlg = LongNarrationDialog(
            parts, "", [], None, {}, allow_sfx=True, parent=None,
            generation_parameters=params if params is not None
            else self.params())
        return dlg

    def _collect_header_texts(self, dlg):
        # All visible QLabel texts of the dialog (the per-part headers
        # inside the scroll-area frames AND the top-level limit note).
        from PySide6.QtWidgets import QLabel
        return [lbl.text() for lbl in dlg.findChildren(QLabel)]

    def test_oversized_part_header_marker_and_limit_note(self):
        parts = [
            _make_part("Short safe part one. " * 2),
            _make_part("huge " * 700),            # ~3500 chars -> blocked
        ]
        dlg = self._dialog(parts)
        headers = self._collect_header_texts(dlg)
        joined = "\n".join(headers)
        self.assertIn("⚠ ~233s exceeds the ~164s single-output limit", joined)
        self.assertIn("Single-output limit: ~164s (max_new_tokens=4096)",
                      joined)

    def test_safe_parts_have_no_marker_and_no_limit_note(self):
        parts = [_make_part("Small part one."), _make_part("Small part two.")]
        dlg = self._dialog(parts)
        joined = "\n".join(self._collect_header_texts(dlg))
        self.assertNotIn("⚠", joined)
        self.assertNotIn("Single-output limit", joined)

    def test_warning_part_marker_exact_boundary(self):
        parts = [_make_part("A" * WARN_CHARS_4100)]
        dlg = self._dialog(parts, self.params(max_new_tokens=4100))
        joined = "\n".join(self._collect_header_texts(dlg))
        self.assertIn("exactly at the ~164s single-output limit", joined)

    def test_non_default_budget_changes_dialog_classification(self):
        # 1500 chars = 100s: blocked at 2048-token budget, safe at 4096.
        parts = [_make_part("B" * 1500)]
        dlg2048 = self._dialog(parts, self.params(max_new_tokens=2048))
        self.assertIn("exceeds the ~82s single-output limit",
                      "\n".join(self._collect_header_texts(dlg2048)))
        dlg4096 = self._dialog(parts, self.params(max_new_tokens=4096))
        self.assertNotIn("⚠",
                         "\n".join(self._collect_header_texts(dlg4096)))

    def test_generate_click_declined_no_emit_dialog_stays_open(self):
        from unittest.mock import patch
        parts = [_make_part("Safe part one."), _make_part("huge " * 700)]
        dlg = self._dialog(parts)
        emitted = []
        dlg.generate_requested.connect(
            lambda p, m: emitted.append((p, m)))
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        with patch.object(QMessageBox, "question", rec):
            dlg._on_generate()
        # One confirmation listing the affected part; declining emits
        # NOTHING (dialog stays open — the user can edit and retry).
        self.assertEqual(len(rec.calls), 1)
        self.assertIn("1 of 2 parts exceed", rec.calls[0][1])
        self.assertIn("~233s", rec.calls[0][1])
        self.assertEqual(emitted, [])
        self.assertFalse(dlg.result() in (QMessageBox.StandardButton.Yes,))

    def test_generate_click_acknowledged_emits_unchanged_parts(self):
        from unittest.mock import patch
        big = "huge " * 700
        parts = [_make_part("Safe part one."), _make_part(big)]
        dlg = self._dialog(parts)
        emitted = []
        dlg.generate_requested.connect(
            lambda p, m: emitted.append((p, m)))
        rec = ModalRecorder(QMessageBox.StandardButton.Yes)
        with patch.object(QMessageBox, "question", rec):
            dlg._on_generate()
        # Acknowledged: BOTH parts emitted, the oversized one UNCHANGED —
        # NO auto-split, NO rewrite (the no-auto-split contract).
        self.assertEqual(len(emitted), 1)
        emitted_parts = emitted[0][0]
        self.assertEqual(len(emitted_parts), 2)
        self.assertEqual(emitted_parts[1].text.strip(), big.strip())

    def test_generate_click_uses_edited_text_as_basis(self):
        from unittest.mock import patch
        # The header marker reflects split-time text; an EDIT that makes
        # a safe part oversized is caught at Generate time (the basis is
        # the text that will actually be sent).
        parts = [_make_part("Small editable part.")]
        dlg = self._dialog(parts)
        dlg._editors[0].setPlainText("edited " * 500)   # ~3500 chars
        emitted = []
        dlg.generate_requested.connect(
            lambda p, m: emitted.append((p, m)))
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        with patch.object(QMessageBox, "question", rec):
            dlg._on_generate()
        self.assertEqual(len(rec.calls), 1)
        self.assertIn("1 of 1 parts exceed", rec.calls[0][1])
        self.assertEqual(emitted, [])

    def test_generate_click_safe_parts_no_modal(self):
        from unittest.mock import patch
        parts = [_make_part("Small part one."), _make_part("Small part two.")]
        dlg = self._dialog(parts)
        emitted = []
        dlg.generate_requested.connect(
            lambda p, m: emitted.append((p, m)))
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        with patch.object(QMessageBox, "question", rec):
            dlg._on_generate()
        self.assertEqual(len(rec.calls), 0)
        self.assertEqual(len(emitted), 1)
        dlg.close()

    def test_legacy_constructor_without_parameters_uses_default(self):
        # Backwards compatibility: older callers (and the frozen suites)
        # construct the dialog without generation_parameters — the
        # verdicts then resolve the configured default (4096).
        from ui.panels.long_narration_dialog import LongNarrationDialog
        dlg = LongNarrationDialog([_make_part("huge " * 700)],
                                  "", [], None, {})
        self.assertEqual(len(dlg._preflight_verdicts), 1)
        self.assertEqual(dlg._preflight_verdicts[0]["state"],
                         PREFLIGHT_BLOCKED)
        self.assertEqual(
            dlg._preflight_verdicts[0]["effective_max_new_tokens"], 4096)
        dlg.close()


# ===========================================================================
# 5) BATCH — selective start (scene mode), manual start, regeneration
# ===========================================================================
class TestBatchSelectiveStart(_P345AHarness):

    def test_blocked_checked_job_declined_leaves_everything_untouched(self):
        from unittest.mock import patch
        from engine.batch_manager import JobStatus
        dlg = self.queue_scene_batch(
            ["Safe one.", "huge " * 700, "Safe three."])
        bm = self.win._batch_manager
        # P3.45.4 (documented update): job-keyed check states.
        dlg._check_states = {dlg._check_key(j): True
                             for j in bm.jobs}
        before = self.snapshot()
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        with patch.object(QMessageBox, "question", rec):
            self.win._on_generate_selected([0, 1, 2])
        # ONE confirmation reports the affected job before start ...
        self.assertEqual(len(rec.calls), 1)
        self.assertIn("1 of 3 selected parts exceed", rec.calls[0][1])
        self.assertIn("~233s", rec.calls[0][1])
        # ... and declining starts NOTHING: byte-identical workspace.
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(bm.is_running)
        for j in bm.jobs:
            self.assertEqual(j.status, JobStatus.PENDING)
        self.assertEqual(FakeHiggsModel.calls, [])

    def test_blocked_checked_job_acknowledged_runs_normally(self):
        from unittest.mock import patch
        from engine.batch_manager import JobStatus
        dlg = self.queue_scene_batch(
            ["Safe one.", "huge " * 700, "Safe three."])
        bm = self.win._batch_manager
        # P3.45.4 (documented update): job-keyed check states.
        dlg._check_states = {dlg._check_key(j): True
                             for j in bm.jobs}
        rec = ModalRecorder(QMessageBox.StandardButton.Yes)
        with patch.object(QMessageBox, "question", rec):
            self.win._on_generate_selected([0, 1, 2])
        self.wait_batch()
        # Regression: the whole run completed — blocked + safe jobs and
        # their neighbours all intact (nothing corrupted by the preflight).
        self.assertEqual(len(rec.calls), 1)
        self.assertEqual(len(FakeHiggsModel.calls), 3)
        statuses = [j.status for j in bm.jobs]
        self.assertEqual(statuses, [JobStatus.COMPLETED] * 3)

    def test_safe_batch_no_modal_full_regression(self):
        from unittest.mock import patch
        dlg = self.queue_scene_batch(["A short.", "B short.", "C short."])
        bm = self.win._batch_manager
        # P3.45.4 (documented update): job-keyed check states.
        dlg._check_states = {dlg._check_key(j): True
                             for j in bm.jobs}
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        with patch.object(QMessageBox, "question", rec):
            self.win._on_generate_selected([0, 1, 2])
        self.wait_batch()
        self.assertEqual(len(rec.calls), 0)   # no UI noise for safe runs
        self.assertEqual(len(FakeHiggsModel.calls), 3)
        self.assertFalse(bm.is_running)

    def test_unchecked_blocked_job_does_not_block_the_run(self):
        from unittest.mock import patch
        dlg = self.queue_scene_batch(
            ["Safe one.", "huge " * 700, "Safe three."])
        bm = self.win._batch_manager
        # Only SAFE jobs are selected: the blocked one is not part of the
        # execution run — no confirmation, no delay, normal run.
        # P3.45.4 (documented update): job-keyed check states.
        dlg._check_states = {dlg._check_key(j): v for j, v in zip(
            bm.jobs, [True, False, True])}
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        with patch.object(QMessageBox, "question", rec):
            self.win._on_generate_selected([0, 2])
        self.wait_batch()
        self.assertEqual(len(rec.calls), 0)
        self.assertEqual(len(FakeHiggsModel.calls), 2)
        self.assertFalse(bm.is_running)


class TestBatchManualStart(_P345AHarness):

    def test_manual_queue_blocked_job_declined_no_start(self):
        from unittest.mock import patch
        from engine.batch_manager import BatchJob, JobStatus
        self.win._on_open_batch_generation()
        dlg = self.win._batch_dialog
        bm = self.win._batch_manager
        bm.add_job(BatchJob(name="small", prompt="Small manual prompt.",
                            project="Alpha"))
        bm.add_job(BatchJob(name="giant", prompt="huge " * 700,
                            project="Alpha"))
        dlg._refresh_table()
        before = self.snapshot()
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        with patch.object(QMessageBox, "question", rec):
            dlg._on_start()
        self.assertEqual(len(rec.calls), 1)
        self.assertIn("1 of 2 jobs exceed", rec.calls[0][1])
        self.assertIn("~233s", rec.calls[0][1])
        # Declined: nothing started, nothing mutated.
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(bm.is_running)
        self.assertEqual(FakeHiggsModel.calls, [])
        for j in bm.jobs:
            self.assertEqual(j.status, JobStatus.PENDING)

    def test_manual_queue_blocked_job_acknowledged_runs(self):
        from unittest.mock import patch
        from engine.batch_manager import BatchJob, JobStatus
        self.win._on_open_batch_generation()
        dlg = self.win._batch_dialog
        bm = self.win._batch_manager
        bm.add_job(BatchJob(name="small", prompt="Small manual prompt.",
                            project="Alpha"))
        bm.add_job(BatchJob(name="giant", prompt="huge " * 700,
                            project="Alpha"))
        dlg._refresh_table()
        rec = ModalRecorder(QMessageBox.StandardButton.Yes)
        with patch.object(QMessageBox, "question", rec):
            dlg._on_start()
        self.wait_batch()
        self.assertEqual(len(rec.calls), 1)
        self.assertEqual(len(FakeHiggsModel.calls), 2)
        self.assertEqual([j.status for j in bm.jobs],
                         [JobStatus.COMPLETED] * 2)

    def test_manual_queue_safe_no_modal(self):
        from unittest.mock import patch
        from engine.batch_manager import BatchJob
        self.win._on_open_batch_generation()
        dlg = self.win._batch_dialog
        bm = self.win._batch_manager
        bm.add_job(BatchJob(name="one", prompt="First safe prompt.",
                            project="Alpha"))
        dlg._refresh_table()
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        with patch.object(QMessageBox, "question", rec):
            dlg._on_start()
        self.wait_batch()
        self.assertEqual(len(rec.calls), 0)
        self.assertEqual(len(FakeHiggsModel.calls), 1)


class TestRegenerationPreflight(_P345AHarness):

    def test_regen_confirm_contains_preflight_verdict(self):
        from unittest.mock import patch
        dlg = self.queue_scene_batch(["Safe one.", "huge " * 700])
        bm = self.win._batch_manager
        # The regen confirmation is the EXISTING modal — the verdict is
        # folded into its text (no second modal, frozen flow preserved).
        rec = ModalRecorder(QMessageBox.StandardButton.Cancel)
        with patch.object(QMessageBox, "question", rec):
            dlg._on_regen(1)
        self.assertEqual(len(rec.calls), 1)
        msg = rec.calls[0][1]
        self.assertIn("Regenerate ONLY this part", msg)
        self.assertIn("PREFLIGHT (P3.45.2A)", msg)
        self.assertIn("may exceed the generation limit", msg)
        self.assertIn("~233s", msg)
        self.assertIn("~164s", msg)

    def test_regen_confirm_declined_no_regen_requested(self):
        from unittest.mock import patch
        dlg = self.queue_scene_batch(["Safe one.", "huge " * 700])
        requested = []
        dlg.regen_requested.connect(lambda idx: requested.append(idx))
        rec = ModalRecorder(QMessageBox.StandardButton.Cancel)
        with patch.object(QMessageBox, "question", rec):
            dlg._on_regen(1)
        self.assertEqual(requested, [])
        self.assertEqual(FakeHiggsModel.calls, [])

    def test_regen_confirm_acknowledged_emits_regen(self):
        from unittest.mock import patch
        from engine.batch_manager import JobStatus
        dlg = self.queue_scene_batch(["Safe one.", "huge " * 700])
        bm = self.win._batch_manager
        # Complete the oversized job first (the production regen flow
        # regenerates a COMPLETED part — regen_job refuses PENDING rows,
        # which would raise the un-patched "cannot be regenerated"
        # warning modal and hang the offscreen run).
        # P3.45.4 (documented update): job-keyed check states.
        dlg._check_states = {dlg._check_key(j): v for j, v in zip(
            bm.jobs, [False, True])}
        rec_run = ModalRecorder(QMessageBox.StandardButton.Yes)
        with patch.object(QMessageBox, "question", rec_run):
            self.win._on_generate_selected([1])
        self.wait_batch()
        self.assertEqual(bm.jobs[1].status, JobStatus.COMPLETED)
        requested = []
        dlg.regen_requested.connect(lambda idx: requested.append(idx))
        rec = ModalRecorder(QMessageBox.StandardButton.Yes)
        with patch.object(QMessageBox, "question", rec):
            dlg._on_regen(1)
        self.assertEqual(requested, [1])
        # The production handler ran the regen (new version, completes).
        self.wait_batch()
        self.assertEqual(bm.jobs[1].status, JobStatus.COMPLETED)
        dlg.close()

    def test_safe_regen_confirm_has_no_preflight_noise(self):
        from unittest.mock import patch
        dlg = self.queue_scene_batch(["Safe one.", "Safe two."])
        rec = ModalRecorder(QMessageBox.StandardButton.Cancel)
        with patch.object(QMessageBox, "question", rec):
            dlg._on_regen(1)
        self.assertEqual(len(rec.calls), 1)
        self.assertNotIn("PREFLIGHT", rec.calls[0][1])


# ===========================================================================
# 6) NO-MUTATION + truncation-boundary semantics + normal-path regression
# ===========================================================================
class TestNoMutationAndTruncationSemantics(_P345AHarness):

    def test_declined_selective_run_never_touches_blocks_or_slots(self):
        from unittest.mock import patch
        from engine.narration_blocks import PromptBlock
        # A real scene with blocks, an oversized part queued, declined.
        text = ("First block of narration.\n\n"
                "Second block with more text.\n\n"
                "Third block.")
        self.scene.text = text
        p1 = text.index("\n\n")
        p2 = text.index("\n\n", p1 + 2)
        self.scene.narration_blocks = [
            {"id": "b1", "start_offset": 0, "end_offset": p1,
             "emotion": None, "style": None, "speed": None, "pitch": None,
             "delivery": None, "sfx_insertions": [], "pause_insertions": [],
             "label": None, "locked": False, "manually_edited": False,
             "character_id": None, "lost_character_id": None},
            {"id": "b2", "start_offset": p1 + 2, "end_offset": p2,
             "emotion": None, "style": None, "speed": None, "pitch": None,
             "delivery": None, "sfx_insertions": [], "pause_insertions": [],
             "label": None, "locked": False, "manually_edited": False,
             "character_id": None, "lost_character_id": None},
        ]
        self.win._load_scene_state(self.scene)
        blocks_before = [
            (b.id, b.start_offset, b.end_offset, b.text
             if hasattr(b, "text") else "")
            for b in self.win._editor.block_manager.blocks]
        dlg = self.queue_scene_batch(
            ["First block of narration.", "huge " * 700])
        bm = self.win._batch_manager
        # P3.45.4 (documented update): job-keyed check states.
        dlg._check_states = {dlg._check_key(j): True
                             for j in bm.jobs}
        before = self.snapshot()
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        with patch.object(QMessageBox, "question", rec):
            self.win._on_generate_selected([0, 1])
        # The editor blocks are byte-identical (no re-chunking, no
        # offset changes, no structural split — P3.44.9 frozen surface).
        blocks_after = [
            (b.id, b.start_offset, b.end_offset, b.text
             if hasattr(b, "text") else "")
            for b in self.win._editor.block_manager.blocks]
        self.assertEqual(blocks_after, blocks_before)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(FakeHiggsModel.calls, [])

    def test_generation_result_has_no_fabricated_truncation_metadata(self):
        # Documented limitation (pinned): the model API returns only a
        # waveform; the chain exposes NO finish_reason / token_count /
        # frame_count. P3.45.2A deliberately does NOT fake a post-hoc
        # truncation detector — the preflight PREVENTS the predictable
        # overflow instead, and the P3.27B guards keep their role.
        from engine.models import GenerationResult
        for field_name in ("finish_reason", "truncated", "token_count",
                           "frame_count", "completion_reason"):
            self.assertFalse(
                hasattr(GenerationResult, field_name),
                "GenerationResult must not grow a fabricated '%s' field"
                % field_name)
        # The one derivable ceiling is the preflight/guard arithmetic:
        self.assertEqual(
            preflight_generation_size("x", 4096)["maximum_output_seconds"],
            4096 / 25.0)

    def test_normal_generation_full_pipeline_regression(self):
        # A normal request continues through the EXISTING generation path
        # unchanged: model called, result registered in History with the
        # saved output file.
        text = "Normal narration that generates without any preflight."
        self.win._editor.set_text(text)
        rec = ModalRecorder(QMessageBox.StandardButton.No)
        from unittest.mock import patch
        with patch.object(QMessageBox, "question", rec):
            self.win._on_generate()
        self.wait_batch()
        self.assertEqual(len(rec.calls), 0)
        self.assertEqual(len(FakeHiggsModel.calls), 1)
        # The finished-generation path ran end-to-end: history has the
        # entry with a real output file.
        _process(400)
        entries = self.engine._history.list_entries()
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].output_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
