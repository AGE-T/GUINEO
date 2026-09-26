"""GUINEO P3.44.2 — SFX PIPELINE INTEGRITY, PREVIEW/BATCH EQUIVALENCE AND
SENTENCE-SPLITTING CORRECTNESS (runtime tests).

Spec §15 coverage — cases A through J plus the §10/§14 invariants:

Engine-level (pure pipeline, real classes, no mocks):
  A  Plain text containing an SFX marker — Preview prompt == Batch part
     prompt, token present, complete text preserved.
  B  Narration Block containing an SFX insertion (sfx_insertions metadata,
     marker NOT in text) — the batch pipeline materializes the metadata via
     the SAME authoritative conversion as the Preview (no silent drop).
  C  SFX immediately after punctuation ("Ez zseniális.{sfx:Sigh:Ahhj}").
  D  SFX immediately after multiple punctuation ("???{sfx:Laughter:Haha}")
     — a sentence boundary IS recognized after the marker chain; the
     marker belongs to the sentence it annotates.
  E  SFX followed by normal text (mid-sentence marker stays in place).
  F  Multiple SFX markers in one block — all preserved, in order.
  G  SFX combined with Pause metadata — both materialized.
  H  Several Narration Blocks where only one contains SFX — the SFX stays
     scoped to its own part; other parts' prompts unchanged.
  +  Punctuation INSIDE an onomatopoeia ({sfx:Laughter:Ha!ha}) never cuts
     a marker in half (splitter AND detector).
  +  The double representation guard (block metadata + in-text marker for
     the same SFX) materializes exactly once.
  +  The splitter never mutates the caller's PromptBlock objects.
  +  Raw mode is exempt from metadata materialization (user owns text).
  +  Long multi-sentence text: no user text is lost across parts (the
     "complete sentence must be generated" invariant) and `???{sfx:...}`
     no longer glues two sentences into one oversized part.

UI-level (REAL MainWindow + Engine + BatchManager offscreen; only the
model boundary is faked — house pattern):
  I  Batch generation from a Scene containing both Plain Text and
     Narration Blocks with SFX — every model call carries the SFX token
     and the full text.
  J  Switch project while Batch generation is running — SFX data (text
     markers AND block metadata) survives the switch; jobs still complete
     with the correct prompts.
  §14 LongNarrationDialog part editors show the SFX marker (the batch
     representation matches what will be generated).

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_44_2_sfx_pipeline.py -v
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

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from unittest.mock import patch, PropertyMock

from engine.engine import Engine
from engine.models import Project, Scene, GenerationParameters
from engine.project_manager import ProjectManager
from engine.narration_splitter import NarrationSplitter
from engine.narration_blocks import PromptBlock, SfxInsertion, PauseInsertion
from engine.block_detector import HeuristicBlockDetector
from engine.prompt_state import CanonicalPromptCompiler
from engine.batch_manager import BatchManager, JobStatus
from ui.main_window import MainWindow

SAMPLE_RATE = 24000

G_E, G_S = "Elation", "Whispering"  # visible global tokens


class FakeHiggsModel:
    """Model-boundary fake (house pattern)."""
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


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _norm(text):
    """Whitespace-insensitive comparison key (order-preserving)."""
    return " ".join(text.split())


# ===========================================================================
# Engine-level: the pure SFX pipeline (cases A–H + splitting invariants)
# ===========================================================================
class SfxPipelineEngineTests(unittest.TestCase):
    """Preview (compile_for_generate / compile_continuous) vs Batch
    (NarrationSplitter → compile_for_batch_part) semantic equivalence."""

    def setUp(self):
        self.splitter = NarrationSplitter()

    def _preview_plain(self, text):
        return CanonicalPromptCompiler.compile_for_generate(
            text=text, global_emotion=G_E, global_style=G_S,
            global_speed="Normal", global_pitch="Normal",
            global_delivery="Normal", allow_sfx=True).final_prompt

    def _preview_blocks(self, text, blocks):
        return CanonicalPromptCompiler.compile_continuous(
            text=text, blocks=blocks, global_emotion=G_E,
            global_style=G_S, global_speed="Normal",
            global_pitch="Normal", global_delivery="Normal",
            allow_sfx=True).final_prompt

    def _batch(self, text, blocks=None, raw=False):
        return self.splitter.split(
            text=text, blocks=blocks, global_emotion=G_E,
            global_style=G_S, global_speed="Normal", global_pitch="Normal",
            global_delivery="Normal", raw_mode=raw, allow_sfx=True)

    def _assert_all_text_preserved(self, text, parts):
        """§16 no-data-loss: every non-whitespace character of the user
        text (excluding marker syntax) survives across the parts."""
        joined = _norm(" ".join(p.text for p in parts))
        expected = _norm(text)
        self.assertEqual(joined, expected,
                         "Batch part texts lost or altered user text:\n"
                         "  joined : %r\n  expected: %r" % (joined, expected))

    # -- Case A ----------------------------------------------------------
    def test_case_A_plain_text_sfx_marker(self):
        text = ("Ez zseniális, már a reggelimet is elkészíti helyettem "
                "{sfx:Sigh:Ahhj}.")
        preview = self._preview_plain(text)
        self.assertIn("<|sfx:sigh|>Ahhj", preview)
        parts = self._batch(text)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].prompt, preview,
                         "Preview and Batch prompts differ for Case A")
        self.assertIn("{sfx:Sigh:Ahhj}", parts[0].text,
                      "SFX marker not visible in the batch representation")
        self._assert_all_text_preserved(text, parts)

    # -- Case B ----------------------------------------------------------
    def test_case_B_block_sfx_insertion_metadata(self):
        text = "Ez a blokk tiszta szövege. Ez a második mondat."
        block = PromptBlock(
            start_offset=0, end_offset=len(text),
            sfx_insertions=[SfxInsertion("Sigh", "Ahhj", 26)])
        preview = self._preview_blocks(text, [block])
        self.assertIn("<|sfx:sigh|>Ahhj", preview,
                      "Preview lost the SFX metadata token")
        parts = self._batch(text, [block])
        self.assertEqual(parts[0].prompt, preview,
                         "Batch part prompt diverges from the Preview for "
                         "block SFX metadata (the P3.44.2 root cause)")
        self.assertIn("{sfx:Sigh:Ahhj}", parts[0].text,
                      "Materialized SFX marker not visible in the batch "
                      "representation (Case B)")

    def test_case_B_block_pause_insertion_metadata(self):
        text = "Első mondat. Második mondat. Harmadik mondat."
        block = PromptBlock(
            start_offset=0, end_offset=len(text),
            pause_insertions=[PauseInsertion("pause", 13)])
        preview = self._preview_blocks(text, [block])
        self.assertIn("<|prosody:pause|>", preview)
        parts = self._batch(text, [block])
        self.assertEqual(parts[0].prompt, preview,
                         "Batch part prompt diverges from the Preview for "
                         "pause metadata")
        self.assertIn("{pause}", parts[0].text)

    # -- Case C ----------------------------------------------------------
    def test_case_C_sfx_immediately_after_punctuation(self):
        text = "Ez zseniális.{sfx:Sigh:Ahhj}"
        preview = self._preview_plain(text)
        self.assertIn("zseniális.<|sfx:sigh|>Ahhj", preview)
        parts = self._batch(text)
        self.assertEqual(parts[0].prompt, preview)
        self._assert_all_text_preserved(text, parts)

    # -- Case D ----------------------------------------------------------
    def test_case_D_sfx_after_multiple_punctuation(self):
        text = ("Ez zseniális, már a reggelimet is elkészíti "
                "helyette???{sfx:Laughter:Haha} - Plain textben látom!")
        preview = self._preview_plain(text)
        self.assertIn("???<|sfx:laughter|>Haha", preview)
        parts = self._batch(text)
        # The marker chain ends the sentence; the dash-fragment is the
        # next sentence (grouped together here — short text).
        self.assertEqual(parts[0].prompt, preview,
                         "Preview and Batch differ for ???{sfx:...}")
        self._assert_all_text_preserved(text, parts)

    def test_case_D_sentence_boundary_recognized_without_whitespace(self):
        # Long text forces sentence grouping; ???{sfx:...} must END the
        # sentence (previously the two sentences GLUED into one).
        text = (
            "Ez egy elég hosszú mondat, ami önmagában is betölti a "
            "rész hossz követelményeit, mert szándékosan hosszú. "
            "Harmadik mondat következik most???{sfx:Laughter:Haha} "
            "És ez a mondat a nevetés után jön, külön mondatként. "
            "Negyedik mondat a szöveg végén, hogy több rész keletkezzen. "
            "Ötödik mondat zárja a sort rendesen."
        )
        sentences = self.splitter._split_sentences(text)
        self.assertTrue(
            any(s.endswith("{sfx:Laughter:Haha}") for s in sentences),
            "Punctuation-adjacent SFX must terminate its sentence, "
            "got: %r" % (sentences,))
        self.assertFalse(
            any("{sfx:Laughter:Haha} És" in s for s in sentences),
            "Two sentences were glued into one by an adjacent SFX marker")
        parts = self._batch(text)
        self._assert_all_text_preserved(text, parts)

    # -- Case E ----------------------------------------------------------
    def test_case_E_sfx_followed_by_normal_text(self):
        text = "Valami történt {sfx:Sigh:Ahhj} és megy tovább a történet."
        preview = self._preview_plain(text)
        self.assertIn("<|sfx:sigh|>Ahhj és megy tovább", preview)
        parts = self._batch(text)
        self.assertEqual(parts[0].prompt, preview)
        # A mid-sentence marker must NOT create a sentence boundary.
        sentences = self.splitter._split_sentences(text)
        self.assertEqual(len(sentences), 1)
        self._assert_all_text_preserved(text, parts)

    # -- Case F ----------------------------------------------------------
    def test_case_F_multiple_sfx_markers_in_one_block(self):
        text = ("Mondat előtte. {sfx:Laughter:Haha} Utána egy mondat. "
                "{sfx:Sigh:Ahhj} Vége.")
        preview = self._preview_plain(text)
        self.assertIn("<|sfx:laughter|>Haha", preview)
        self.assertIn("<|sfx:sigh|>Ahhj", preview)
        parts = self._batch(text)
        self.assertEqual(parts[0].prompt, preview)
        self.assertEqual(parts[0].prompt.count("<|sfx:"), 2)
        self._assert_all_text_preserved(text, parts)

    # -- Case G ----------------------------------------------------------
    def test_case_G_sfx_combined_with_pause_metadata(self):
        text = "Első mondat ezt mondja. Második mondat jön. Harmadik."
        block = PromptBlock(
            start_offset=0, end_offset=len(text),
            sfx_insertions=[SfxInsertion("Sigh", "Ahhj", 20)],
            pause_insertions=[PauseInsertion("pause", 34)])
        preview = self._preview_blocks(text, [block])
        self.assertIn("<|sfx:sigh|>Ahhj", preview)
        self.assertIn("<|prosody:pause|>", preview)
        parts = self._batch(text, [block])
        self.assertEqual(parts[0].prompt, preview,
                         "SFX+Pause metadata diverged between Preview and "
                         "Batch")
        self.assertIn("{sfx:Sigh:Ahhj}", parts[0].text)
        self.assertIn("{pause}", parts[0].text)

    # -- Case H ----------------------------------------------------------
    def test_case_H_blocks_one_with_sfx(self):
        text = ("Első blokk szövege hosszú mondatokkal. Még egy mondat "
                "itt.\n\nMásodik blokk {sfx:Laughter:Haha} a nevetéssel.\n\n"
                "Harmadik blokk sima szöveg marad.")
        det = HeuristicBlockDetector()
        blocks = det.detect(text)
        self.assertGreaterEqual(len(blocks), 2)
        parts = self._batch(text, blocks)
        with_sfx = [p for p in parts if "<|sfx:laughter|>Haha" in p.prompt]
        without_sfx = [p for p in parts if "<|sfx:" not in p.prompt]
        self.assertEqual(len(with_sfx), 1,
                         "SFX leaked into more parts than its own block: %r"
                         % ([p.text for p in parts],))
        self.assertGreater(len(without_sfx), 0)
        # The SFX part must carry the marker in its visible text too.
        self.assertIn("{sfx:Laughter:Haha}", with_sfx[0].text)
        self._assert_all_text_preserved(text, parts)

    # -- Shielding -------------------------------------------------------
    def test_punctuation_inside_onomatopoeia_never_cuts_marker(self):
        text = ("Valami vicces történt {sfx:Laughter:Ha!ha} és megy "
                "tovább. Még egy mondat. Megint egy mondat. "
                "Negyedik mondat. Ötödik mondat zárja.")
        sentences = self.splitter._split_sentences(text)
        for s in sentences:
            # A marker was cut in half iff a "{sfx:" opener has no
            # complete "{sfx:Name:Onom}" match covering it.
            openers = s.count("{sfx:") + s.count("{pause}")
            complete = len(self.splitter._INLINE_MARKER_RE.findall(s))
            self.assertEqual(openers, complete,
                             "The splitter cut a marker in half: %r" % s)
        # Detector: no block boundary may cut the marker.
        blocks = HeuristicBlockDetector().detect(text)
        for b in blocks:
            block_text = text[b.start_offset:b.end_offset]
            openers = block_text.count("{sfx:") + block_text.count("{pause}")
            complete = len(self.splitter._INLINE_MARKER_RE.findall(
                block_text))
            self.assertEqual(openers, complete,
                             "A block boundary cut a marker in half: %r"
                             % block_text)
        parts = self._batch(text, blocks)
        joined = " ".join(p.prompt for p in parts)
        self.assertEqual(joined.count("<|sfx:laughter|>H"), 1)
        self._assert_all_text_preserved(text, parts)

    def test_detector_boundary_keeps_marker_with_its_sentence(self):
        # Newline after the punctuation+marker → boundary score 100 →
        # the block split must NOT orphan the marker into the next block.
        text = ("Első mondat véget ér???{sfx:Laughter:Haha}\n\n"
                "Második blokk mondata következik most.")
        blocks = HeuristicBlockDetector().detect(text)
        self.assertGreaterEqual(len(blocks), 2)
        first = text[blocks[0].start_offset:blocks[0].end_offset]
        self.assertIn("{sfx:Laughter:Haha}", first,
                      "The detector orphaned an SFX marker into the next "
                      "block (its sentence ends before the marker)")
        second = text[blocks[1].start_offset:blocks[1].end_offset]
        self.assertNotIn("{sfx:", second)

    # -- Double representation guard -------------------------------------
    def test_duplicate_metadata_and_marker_materialize_once(self):
        text = "Ez a szöveg {sfx:Sigh:Ahhj} dupla. Második."
        block = PromptBlock(
            start_offset=0, end_offset=len(text),
            sfx_insertions=[SfxInsertion("Sigh", "Ahhj", 12)])
        preview = self._preview_blocks(text, [block])
        self.assertEqual(preview.count("<|sfx:sigh|>"), 1,
                         "Double representation materialized twice "
                         "(Preview): %r" % preview)
        parts = self._batch(text, [block])
        self.assertEqual(parts[0].prompt.count("<|sfx:sigh|>"), 1,
                         "Double representation materialized twice (Batch)")

    # -- Caller safety ---------------------------------------------------
    def test_splitter_never_mutates_caller_blocks(self):
        text = "Első blokk szövege. Második mondat. Harmadik mondat."
        block = PromptBlock(
            start_offset=0, end_offset=len(text),
            sfx_insertions=[SfxInsertion("Sigh", "Ahhj", 22)])
        orig_start, orig_end = block.start_offset, block.end_offset
        orig_sfx = list(block.sfx_insertions)
        parts = self._batch(text, [block])
        self.assertGreater(len(parts), 0)
        self.assertEqual((block.start_offset, block.end_offset),
                         (orig_start, orig_end),
                         "The splitter mutated the caller's block span")
        self.assertEqual(block.sfx_insertions, orig_sfx,
                         "The splitter mutated the caller's block metadata")

    def test_raw_mode_exempts_metadata_materialization(self):
        text = "Raw prompt <|emotion:fear|>Stay in the light."
        block = PromptBlock(
            start_offset=0, end_offset=len(text),
            sfx_insertions=[SfxInsertion("Sigh", "Ahhj", 11)])
        parts = self._batch(text, [block], raw=True)
        for p in parts:
            self.assertNotIn("{sfx:", p.text,
                             "Raw mode must not materialize metadata")
            self.assertNotIn("{sfx:", p.prompt)

    def test_no_metadata_fast_path_identity(self):
        text = "Sima szöveg, semmilyen metaadat. Második mondat."
        block = PromptBlock(start_offset=0, end_offset=len(text))
        parts = self._batch(text, [block])
        self.assertEqual(parts[0].text, text.strip())

    def test_long_text_no_sentence_glued_or_lost(self):
        long_text = (
            "Ez egy hosszú bevezető mondat, ami elég hosszú, hogy külön "
            "generálási részt kapjon, mert jócskán meghaladja a hatvan "
            "karaktert és még több szót tartalmaz. "
            "A második mondat szintén hosszú, hogy csoportosításra "
            "kerüljön, és legyen benne elég szó. "
            "Harmadik mondat következik most???{sfx:Laughter:Haha} "
            "És ez a mondat a nevetés után jön, még mindig külön mondat. "
            "Negyedik mondat, hogy a teljes szöveg bizonyosan meghaladja "
            "a négyszáz karakteres limitet. "
            "Ötödik mondat a végére, hogy legyen mit csoportosítani. "
            "Hatodik mondat {sfx:Sigh:Ahhj} ami a sóhajt követi. "
            "Hetedik mondat zárja a szöveget."
        )
        parts = self._batch(long_text)
        self.assertGreater(len(parts), 1, "Expected a multi-part split")
        self._assert_all_text_preserved(long_text, parts)
        # The SFX-annotated sentence stays attached to its own sentence.
        sfx_part = next(p for p in parts if "{sfx:Laughter:Haha}" in p.text)
        self.assertIn("most???{sfx:Laughter:Haha}", sfx_part.text)
        # No part may contain the glued pair (marker + following
        # sentence as ONE sentence) — that was the P3.44.2 defect.
        self.assertEqual(
            len(self.splitter._split_sentences(long_text)),
            len([s for s in self.splitter._split_sentences(long_text)
                 if s]),
            "sanity")
        self.assertTrue(
            any(s == ("Harmadik mondat következik most???"
                      "{sfx:Laughter:Haha}")
                for s in self.splitter._split_sentences(long_text)),
            "The ???{sfx:...} sentence must END at its marker, not glue "
            "the following sentence")


# ===========================================================================
# UI-level: real MainWindow + Engine + BatchManager (model faked only)
# ===========================================================================
class _MWHarness(unittest.TestCase):

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="ss_p3442_")
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
        self.scene_a = Scene(id="scA00001", name="01 Scene",
                              project_id="projA")
        self.pa.add_scene(self.scene_a)
        self.pm.save_project(self.pa)

        self.pb = Project(name="Beta", id="projB")
        self.scene_b = Scene(id="scB00001", name="Scene B",
                              project_id="projB")
        self.pb.add_scene(self.scene_b)
        self.pm.save_project(self.pb)

        self.win._active_project = self.pa
        self.win._active_scene = self.scene_a
        self.win._current_project = "Alpha"
        self.win._emotion = G_E
        self.win._style = G_S
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
            _process(60)
        except Exception:
            pass
        self.win = None
        self.engine = None
        self.pm = None
        self.pa = None
        self.pb = None
        self.scene_a = None
        self.scene_b = None
        self.tmp = None
        self.patchers = None
        gc.collect()

    # -- helpers ---------------------------------------------------------
    def params(self):
        return GenerationParameters(
            temperature=0.95, top_p=0.95, top_k=300, max_new_tokens=4096,
            seed=None, append_silence=0.3, normalize_output=False,
            auto_play=False)

    def wait_batch(self, timeout=30):
        bm = self.win._batch_manager
        t0 = time.time()
        while bm.is_running and time.time() - t0 < timeout:
            _process(20)
        _process(250)

    def split_like_generate_long(self, text, blocks=None):
        """Mirror MainWindow._on_generate_long_narration's split call."""
        splitter = NarrationSplitter()
        return splitter.split(
            text=text, blocks=blocks if blocks else None,
            global_emotion=self.win._emotion,
            global_style=self.win._style,
            global_speed=self.win._speed, global_pitch=self.win._pitch,
            global_delivery=self.win._delivery,
            base_parameters=self.params(),
            raw_mode=self.win._editor.is_raw_mode(),
            detect_speakers=True,
            allow_sfx=True)


class SfxBatchRuntimeTests(_MWHarness):
    """Cases I and J + §14 — through the REAL MainWindow batch path."""

    def _run_batch_and_collect_calls(self, parts):
        FakeHiggsModel.calls = []
        self.win._start_long_narration(parts, None, self.params(), {},
                                       review_mode=False)
        self.wait_batch()
        return list(FakeHiggsModel.calls)

    # -- Case I ----------------------------------------------------------
    def test_case_I_scene_plain_text_plus_blocks_with_sfx(self):
        # Scene containing Plain Text (with an SFX marker) AND Narration
        # Blocks (one with an in-text marker, one clean).
        text = ("Bevezető plain szöveg {sfx:Sigh:Ahhj} a jelenethez. "
                "Még egy plain mondat következik most.\n\n"
                "Blokk egy szövege hosszú, külön generálásra. "
                "{sfx:Laughter:Haha} A nevetés a blokkban van.\n\n"
                "Blokk kettő teljesen tiszta marad.")
        self.win._editor.set_text(text)
        _process(80)
        blocks = HeuristicBlockDetector().detect(text)
        self.assertGreaterEqual(len(blocks), 2)

        parts = self.split_like_generate_long(text, blocks)
        # Every part prompt must contain its expected SFX state.
        sfx_parts = [p for p in parts if "<|sfx:" in p.prompt]
        self.assertEqual(len(sfx_parts), 2,
                         "Expected exactly 2 SFX-bearing parts, got %r"
                         % [p.text for p in parts])
        joined = _norm(" ".join(p.text for p in parts))
        self.assertEqual(joined, _norm(text),
                         "Part texts lost or altered user text")

        calls = self._run_batch_and_collect_calls(parts)
        self.assertEqual(len(calls), len(parts))
        called = " ".join(c["text"] for c in calls)
        self.assertIn("<|sfx:sigh|>Ahhj", called)
        self.assertIn("<|sfx:laughter|>Haha", called)
        # No user text lost in the MODEL INPUT either.
        self.assertIn("Bevezető plain szöveg", called)
        self.assertIn("Blokk kettő teljesen tiszta marad", called)

    def test_case_I_block_metadata_sfx_reaches_model(self):
        # Case B at the UI level: the block's SFX metadata (marker NOT in
        # the editor text) must reach the model through the real batch.
        text = "Ez a blokk tiszta szövege. Ez a második mondat."
        block = PromptBlock(
            start_offset=0, end_offset=len(text),
            sfx_insertions=[SfxInsertion("Sigh", "Ahhj", 26)])
        parts = self.split_like_generate_long(text, [block])
        self.assertIn("{sfx:Sigh:Ahhj}", parts[0].text,
                      "Metadata SFX invisible in the batch representation")
        calls = self._run_batch_and_collect_calls(parts)
        self.assertEqual(len(calls), 1)
        self.assertIn("<|sfx:sigh|>Ahhj", calls[0]["text"],
                      "SFX metadata never reached the model (P3.44.2 "
                      "root cause regression)")

    # -- §14: Long dialog shows the marker --------------------------------
    def test_long_dialog_shows_sfx_marker_in_part_editors(self):
        from ui.panels.long_narration_dialog import LongNarrationDialog
        text = ("Ez zseniális, már a reggelimet is elkészíti helyette???"
                "{sfx:Laughter:Haha} - Plain textben látom!")
        parts = self.split_like_generate_long(text)
        dlg = LongNarrationDialog(parts, "Voice", [], None, {},
                                  allow_sfx=True, parent=self.win)
        try:
            editor_texts = [e.toPlainText() for e in dlg._editors]
            self.assertTrue(
                any("{sfx:Laughter:Haha}" in t for t in editor_texts),
                "LongNarrationDialog part editors do not show the SFX "
                "marker: %r" % editor_texts)
            # The prompt the dialog would submit carries the token.
            dlg._on_generate()  # rebuilds prompts; emits but no receiver
        finally:
            dlg.close()
            dlg.deleteLater()
            _process(50)

    # -- Case J ----------------------------------------------------------
    def test_case_J_project_switch_during_batch_keeps_sfx(self):
        # Scene A: text WITH an SFX marker + saved narration_blocks that
        # carry sfx metadata. Start a batch, switch A→B→A mid-run.
        text_a = ("Alpha jelenet szövege {sfx:Sigh:Ahhj} itt. "
                  "Még egy mondat az Alpha jelenetben.")
        # Metadata offset must NOT overlap the in-text marker (a block
        # carrying BOTH the marker and metadata for the same SFX is the
        # guarded duplicate representation — see the engine tests).
        block_a = PromptBlock(
            start_offset=0, end_offset=len(text_a),
            sfx_insertions=[SfxInsertion("Laughter", "Haha", 46)])
        self.scene_a.text = text_a
        self.scene_a.narration_blocks = [block_a.to_dict()]
        self.scene_a.editor_mode = "blocks"
        self.pm.save_project(self.pa)

        self.win._load_scene_state(self.scene_a)
        _process(80)
        parts = self.split_like_generate_long(
            self.win._editor.get_text(),
            self.win._editor.block_manager.blocks or None)
        self.assertIn("{sfx:Sigh:Ahhj}", " ".join(p.text for p in parts))

        FakeHiggsModel.calls = []
        FakeHiggsModel.delay_s = 0.4  # keep the batch running mid-switch
        self.win._start_long_narration(parts, None, self.params(), {},
                                       review_mode=False)
        _process(200)
        self.assertTrue(self.win._batch_manager.is_running)

        # Switch to project B, then back to A (P3.44.1 scene-owner path).
        self.win._project_manager._cache = {}  # force fresh load from disk
        self.win._active_project = self.pb
        self.win._active_scene = self.scene_b
        self.win._current_project = "Beta"
        _process(100)
        self.win._active_project = self.pa
        self.win._active_scene = self.scene_a
        self.win._current_project = "Alpha"
        _process(100)

        FakeHiggsModel.delay_s = 0.0
        self.wait_batch()
        self.assertFalse(self.win._batch_manager.is_running)
        # All jobs completed with the SFX intact.
        called = " ".join(c["text"] for c in FakeHiggsModel.calls)
        self.assertIn("<|sfx:sigh|>Ahhj", called)
        self.assertIn("<|sfx:laughter|>Haha", called,
                      "Block SFX metadata lost across the project switch")
        # The persisted scene still carries its SFX data (disk truth).
        loaded = self.pm.reload_project("projA")
        scene = loaded.get_scene(self.scene_a.id)
        self.assertIn("{sfx:Sigh:Ahhj}", scene.text)
        self.assertTrue(scene.narration_blocks[0]["sfx_insertions"],
                        "Narration block SFX metadata lost on disk")


class SfxPreviewBatchEquivalenceTests(_MWHarness):
    """§10 invariant at the UI level: same Scene content + same SFX
    metadata + same global panel state = equivalent semantic prompt."""

    def test_preview_equals_batch_semantics_plain(self):
        text = ("Ez zseniális, már a reggelimet is elkészíti helyette???"
                "{sfx:Laughter:Haha} - Plain textben látom!")
        self.win._editor.set_text(text)
        _process(80)
        preview = self.win._build_prompt()
        self.assertIn("<|sfx:laughter|>Haha", preview)
        parts = self.split_like_generate_long(text)
        self.assertEqual(parts[0].prompt, preview,
                         "Preview != Batch for the same Scene content")

    def test_preview_equals_batch_semantics_blocks_metadata(self):
        text = "Ez a blokk tiszta szövege. Ez a második mondat."
        block = PromptBlock(
            start_offset=0, end_offset=len(text),
            sfx_insertions=[SfxInsertion("Sigh", "Ahhj", 26)])
        self.win._editor.set_text(text)
        _process(80)
        # Put the metadata block into the editor's block manager.
        self.win._editor.block_manager._blocks = [block]
        _process(80)
        preview = self.win._build_prompt()
        self.assertIn("<|sfx:sigh|>Ahhj", preview)
        parts = self.split_like_generate_long(
            text, self.win._editor.block_manager.blocks)
        self.assertEqual(parts[0].prompt, preview,
                         "Preview != Batch for block SFX metadata "
                         "(P3.44.2 root cause)")

    def test_allow_sfx_false_strips_tokens_in_batch_too(self):
        # The Allow-SFX policy must hold on the batch side as well
        # (P3.25 SS-M02 discipline extended to the metadata fix).
        text = "Első mondat. {sfx:Sigh:Ahhj} Második mondat itt."
        splitter = NarrationSplitter()
        parts = splitter.split(
            text=text, blocks=None, global_emotion=G_E, global_style=G_S,
            base_parameters=self.params(), allow_sfx=False)
        self.assertNotIn("<|sfx:", parts[0].prompt)
        self.assertNotIn("<|sfx:", parts[0].text.replace("{sfx:", ""))
        # allow_sfx=False keeps only the onomatopoeia text.
        self.assertIn("Ahhj", parts[0].prompt)

    def test_allow_sfx_false_with_block_metadata(self):
        text = "Első mondat. Második mondat. Harmadik mondat."
        block = PromptBlock(
            start_offset=0, end_offset=len(text),
            sfx_insertions=[SfxInsertion("Sigh", "Ahhj", 13)])
        splitter = NarrationSplitter()
        parts = splitter.split(
            text=text, blocks=[block], global_emotion=G_E,
            global_style=G_S, base_parameters=self.params(),
            allow_sfx=False)
        self.assertNotIn("<|sfx:", parts[0].prompt,
                         "allow_sfx=False must suppress metadata SFX tokens")


class SfxBatchStateRevalidationTests(_MWHarness):
    """P3.44.2-discovered Batch state defect + Pause contract pins.

    Runtime proof: while a batch is PAUSED the Pause button disables
    itself and the Start button is the ONLY enabled run control — but
    both start paths refused while paused (manual mode: ``start()``
    returned False and ``_on_start`` blocked on an "Already Running"
    modal; scene mode: ``_on_generate_selected`` blocked on the same
    modal), and ``manager.resume()`` had NO UI caller — so a paused
    batch could NEVER be resumed from the UI.
    """

    @staticmethod
    def _parts(prefix, n):
        from engine.narration_splitter import SplitPart
        return [
            SplitPart(text="%s part %d." % (prefix, i),
                      prompt="%s part %d." % (prefix, i), speaker="",
                      estimated_duration=3.0, char_count=14)
            for i in range(1, n + 1)
        ]

    def test_manager_start_while_paused_resumes(self):
        FakeHiggsModel.delay_s = 0.6
        try:
            self.win._start_long_narration(self._parts("Resume", 3),
                                           None, self.params(), {},
                                           review_mode=False)
            dlg = self.win._batch_dialog
            bm = self.win._batch_manager
            _process(150)
            dlg._on_pause()
            _process(200)
            self.assertTrue(bm.is_paused)
            self.assertFalse(dlg._pause_btn.isEnabled())
            self.assertTrue(dlg._start_btn.isEnabled(),
                            "Start is the resume control while paused")

            # The P3.44.2 fix: start() ACCEPTS while paused (previously
            # returned False → the dialog blocked on a modal, which is
            # exactly how the defect was runtime-proven offscreen).
            ok = bm.start()
            self.assertTrue(ok, "start() while paused must resume")
            self.assertFalse(bm.is_paused)
            self.assertTrue(bm.is_running)
            FakeHiggsModel.delay_s = 0.0
            self.wait_batch(timeout=25)
            self.assertFalse(bm.is_running)
            self.assertEqual([j.status for j in bm.jobs],
                             [JobStatus.COMPLETED] * 3)
        finally:
            FakeHiggsModel.delay_s = 0.0

    def test_scene_generate_checked_while_paused_resumes(self):
        FakeHiggsModel.delay_s = 0.6
        try:
            self.win._start_long_narration(self._parts("Scene", 3),
                                           None, self.params(), {},
                                           review_mode=False)
            dlg = self.win._batch_dialog
            bm = self.win._batch_manager
            _process(150)
            dlg._on_pause()
            _process(200)
            self.assertTrue(bm.is_paused)
            # The scene-mode Start button is "GENERATE CHECKED" — with
            # all rows checked it must RESUME the paused run (not block
            # on the already-running modal).
            dlg._check_states.update({i: True for i in range(3)})
            dlg._on_start()
            _process(200)
            self.assertFalse(bm.is_paused,
                             "GENERATE CHECKED while paused must resume")
            self.assertTrue(bm.is_running)
            FakeHiggsModel.delay_s = 0.0
            self.wait_batch(timeout=25)
            self.assertEqual([j.status for j in bm.jobs],
                             [JobStatus.COMPLETED] * 3)
        finally:
            FakeHiggsModel.delay_s = 0.0

    def test_start_while_running_not_paused_still_refuses(self):
        FakeHiggsModel.delay_s = 0.8
        try:
            self.win._start_long_narration(self._parts("Refuse", 2),
                                           None, self.params(), {},
                                           review_mode=False)
            bm = self.win._batch_manager
            _process(120)
            self.assertTrue(bm.is_running)
            self.assertFalse(bm.is_paused)
            # A plain double-start (not paused) is still refused.
            ok = bm.start()
            self.assertFalse(ok, "double-start must still return False")
            FakeHiggsModel.delay_s = 0.0
            self.wait_batch(timeout=25)
        finally:
            FakeHiggsModel.delay_s = 0.0


if __name__ == "__main__":
    unittest.main()
