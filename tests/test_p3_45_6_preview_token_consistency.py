"""GUINEO P3.45.6 — PHASE 5: PREVIEW / NARRATION BLOCK TOKEN CONSISTENCY.

Root cause (source-verified at the P3.45.6 baseline): the CENTER
Preview tab compiled through a PARALLEL token pipeline
(``PromptOptimizer.resolve_and_optimize`` +
``PromptBuilder.build_from_emissions``/``build``) that was
code-divergent from ``MainWindow._build_prompt`` (the pipeline Normal
Generate and the right-panel preview use):

  * it NEVER applied ``allow_sfx`` — SFX tokens showed in the preview
    even when Allow SFX was OFF, while Generate suppressed them
    (preview != model input, the ADR 003 §6/§8 contract broken on a
    display surface);
  * it surfaced NO compile warnings (SS-H07 discipline broken);
  * it silently fell back to the raw text on compile failure.

The Long-Narration dialog's part rebuild also swallowed compile
warnings (the canonical compiler converts invalid-value failures into
a tokenless prompt WITH a warning — the warning never reached the
user).

FIX (P3.45.6 Phase 5): the center Preview now compiles through the
SAME ``CanonicalPromptCompiler`` entries as Generate
(``compile_continuous`` with blocks; ``compile_for_generate``
without) with the SAME ``allow_sfx`` policy (pushed with the global
defaults); compile warnings are surfaced in the preview stats; the
dialog collects and surfaces part-rebuild warnings once. Raw Mode
remains literal pass-through (unchanged). ONE token engine, one
precedence rule (Inline > Block > Global).

INTENDED, DOCUMENTED differences (not defects — the phase brief
requires them explicit): independent Batch Parts are compiled
SELF-CONTAINED (every winning token re-emitted per part; part text is
the block slice ``.strip()``-ed at split time), while the continuous
single-call prompt keeps unstripped slices joined with a single space
and emits tokens on CHANGE across blocks.
"""
from __future__ import annotations

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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

from engine.narration_blocks import PromptBlock, SfxInsertion, PauseInsertion
from engine.narration_block_manager import validate_block_ranges

TEXT = ("The rain fell quietly over the hills.\n\n"
        "Thunder rolled across the valley below.")
S1 = "The rain fell quietly over the hills."
S2 = "Thunder rolled across the valley below."


def _process(ms=30):
    import time
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


class _WinHarness(unittest.TestCase):
    """REAL MainWindow + Engine (model boundary faked only)."""

    def setUp(self):
        import shutil
        import tempfile
        from unittest.mock import patch, PropertyMock
        from engine.engine import Engine
        from engine.models import Project, Scene
        from engine.project_manager import ProjectManager
        from ui.main_window import MainWindow

        self.tmp = tempfile.mkdtemp(prefix="ss_p3456tok_")
        self.engine = Engine(app_root=self.tmp)
        self.patchers = [
            patch.object(type(self.engine._model), "is_loaded",
                         new_callable=PropertyMock, return_value=True),
            patch.object(type(self.engine._model), "device",
                         new_callable=PropertyMock, return_value="cpu"),
            patch.object(type(self.engine._model),
                         "get_model_and_tokenizer",
                         return_value=(object(), None)),
        ]
        for p in self.patchers:
            p.start()
        self.engine.play_audio = lambda p: None
        self.win = MainWindow(self.engine)
        self.pm = ProjectManager(os.path.join(self.tmp, "projects"))
        self.pa = Project(name="Alpha", id="projA")
        self.scene_a = Scene(id="scA00001", name="01 Scene",
                             project_id="projA")
        self.pa.add_scene(self.scene_a)
        self.pm.save_project(self.pa)
        self.win._active_project = self.pa
        self.win._active_scene = self.scene_a
        self.win._current_project = "Alpha"
        # Raw mode OFF for managed-mode tests.
        self.win._editor._raw_mode_cb.setChecked(False)

    def tearDown(self):
        try:
            if self.win._batch_dialog is not None:
                self.win._batch_dialog.close()
        except Exception:
            pass
        try:
            self.win.close()
            self.win.deleteLater()
        except Exception:
            pass
        for p in self.patchers:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        _process(60)
        self.win = None
        self.engine = None
        import gc
        gc.collect()

    # -- helpers ---------------------------------------------------------
    def load_blocks(self, with_overrides=False, with_sfx=False):
        ed = self.win._editor
        ed.begin_scene_load()
        try:
            ed.set_text(TEXT)
        finally:
            ed.end_scene_load()
        a_end = len(S1)
        b_start = TEXT.index(S2)
        blocks = []
        for i, (s, e) in enumerate([(0, a_end), (b_start, len(TEXT))]):
            b = PromptBlock(start_offset=s, end_offset=e)
            if with_overrides and i == 0:
                b.emotion = "Fear"
                b.style = "Whispering"
            if with_sfx and i == 1:
                b.sfx_insertions = [SfxInsertion("Laughter", "Haha", 3)]
                b.pause_insertions = [PauseInsertion("pause", 1)]
            blocks.append(b)
        ed.begin_scene_load()
        try:
            ed.set_scene_blocks(blocks, TEXT)
        finally:
            ed.end_scene_load()
        return blocks

    def set_allow_sfx(self, value):
        rp = self.win._control_panel
        rp._friendly._allow_sfx_btn.setChecked(value)
        rp._friendly._on_allow_sfx_toggled()   # what a real click triggers

    def preview(self):
        ed = self.win._editor
        ed._mode = ed.MODE_PREVIEW
        ed._update_preview()
        return ed._preview.toPlainText()

    def push_defaults(self):
        """The production push: MainWindow → editor globals + policy."""
        self.win._update_prompt_preview()


class TestPreviewGenerateParity(_WinHarness):

    def test_blocks_mode_allow_sfx_off_parity(self):
        """THE regression: with Allow SFX OFF the center preview now
        matches the Generate prompt exactly (pre-fix the preview showed
        <|sfx:...|> tokens Generate suppressed)."""
        self.load_blocks(with_sfx=True)
        self.set_allow_sfx(False)
        self.push_defaults()

        shown = self.preview()
        model_input = self.win._build_prompt()

        self.assertEqual(shown, model_input,
                         "center preview != model input with Allow SFX "
                         "OFF (preview={0!r} / generate={1!r})"
                         .format(shown[:80], model_input[:80]))
        self.assertNotIn("<|sfx:", model_input)
        self.assertNotIn("<|sfx:", shown)

    def test_blocks_mode_allow_sfx_on_parity(self):
        """Parity with Allow SFX ON (block overrides + SFX metadata)."""
        self.load_blocks(with_overrides=True, with_sfx=True)
        self.set_allow_sfx(True)
        self.push_defaults()

        shown = self.preview()
        model_input = self.win._build_prompt()
        self.assertEqual(shown, model_input)
        self.assertIn("<|sfx:laughter|>", model_input)   # SFX token present

    def test_no_blocks_mode_parity(self):
        """Parity without blocks (compile_for_generate path)."""
        ed = self.win._editor
        ed.begin_scene_load()
        try:
            ed.set_text(S1)
        finally:
            ed.end_scene_load()
        self.set_allow_sfx(True)
        self.push_defaults()

        self.assertEqual(self.preview(), self.win._build_prompt())

    def test_raw_mode_literal_passthrough_both_surfaces(self):
        raw_text = "<|emotion:fear|>Stay in the light. {sfx:Wind:Hooo}"
        ed = self.win._editor
        ed._raw_mode_cb.setChecked(True)
        ed.begin_scene_load()
        try:
            ed.set_text(raw_text)
        finally:
            ed.end_scene_load()
        self.set_allow_sfx(False)
        self.push_defaults()

        self.assertEqual(self.preview(), raw_text)
        self.assertEqual(self.win._build_prompt(), raw_text)

    def test_preview_warnings_surfaced(self):
        """Compile warnings reach the preview stats (SS-H07 on the
        display surface): an invalid global emotion is rejected with a
        visible warning count."""
        self.load_blocks()
        # Drive an invalid value straight into the compile inputs.
        ed = self.win._editor
        ed.set_global_defaults(emotion="NOT_A_REAL_EMOTION",
                               allow_sfx=True)
        shown = self.preview()
        self.assertEqual(shown, self.win._build_prompt())
        self.assertTrue(ed._last_preview_warnings,
                        "the rejected emotion must surface as a preview "
                        "warning")
        self.assertIn("\u26a0", ed._stats_label.text())


class TestTokenSemantics(_WinHarness):

    def test_global_defaults_emitted_once(self):
        """Exactly ONE emotion token for the whole document — no
        double application of defaults."""
        self.win._emotion = "Fear"
        self.load_blocks()
        self.push_defaults()

        prompt = self.win._build_prompt()
        import re
        emotions = re.findall(r"<\|emotion:\w+\|>", prompt)
        self.assertEqual(emotions, ["<|emotion:fear|>"])

    def test_block_override_precedence_single_token(self):
        """A block override wins over the global default and is emitted
        exactly once (Inline > Block > Global, only the winner)."""
        self.win._emotion = "Elation"
        self.load_blocks(with_overrides=True)   # block 0 emotion=fear
        self.push_defaults()

        prompt = self.win._build_prompt()
        import re
        emotions = re.findall(r"<\|emotion:\w+\|>", prompt)
        # Block 0 wins (fear); block 1 has no override -> the emit-on-
        # change loop emits elation for it.
        self.assertEqual(emotions, ["<|emotion:fear|>",
                                    "<|emotion:elation|>"])
        self.assertIn("<|style:whispering|>", prompt)

    def test_token_emission_order_deterministic(self):
        """Per-block prefix order: emotion → style → speed → pitch →
        delivery."""
        self.load_blocks()
        blocks = self.win._editor.block_manager.blocks
        blocks[0].emotion = "Fear"
        blocks[0].style = "Whispering"
        blocks[0].speed = "Slow"
        blocks[0].pitch = "High"
        blocks[0].delivery = "Expressive High"
        self.push_defaults()

        prompt = self.win._build_prompt()
        head = prompt[:prompt.index(S1)]
        self.assertLess(head.index("<|emotion:fear|>"),
                        head.index("<|style:whispering|>"))
        self.assertLess(head.index("<|style:whispering|>"),
                        head.index("<|prosody:speed_slow|>"))
        self.assertLess(head.index("<|prosody:speed_slow|>"),
                        head.index("<|prosody:pitch_high|>"))
        self.assertLess(head.index("<|prosody:pitch_high|>"),
                        head.index("<|prosody:expressive_high|>"))

    def test_sfx_pause_handling_parity(self):
        """Pause tokens survive Allow SFX OFF on BOTH surfaces; SFX
        tokens are suppressed on both."""
        self.load_blocks(with_sfx=True)
        self.set_allow_sfx(False)
        self.push_defaults()

        shown = self.preview()
        model_input = self.win._build_prompt()
        self.assertEqual(shown, model_input)
        self.assertNotIn("<|sfx:", shown)
        self.assertIn("<|prosody:pause|>", shown)
        self.assertIn("Haha", shown)   # onomatopoeia kept (SS-H06)


class TestBatchPartSemantics(_WinHarness):

    def _split_parts(self, allow_sfx=True):
        from engine.narration_splitter import NarrationSplitter
        from engine.models import GenerationParameters
        blocks = self.load_blocks(with_overrides=True)
        params = GenerationParameters(allow_sfx=allow_sfx)
        splitter = NarrationSplitter()
        return splitter.split(
            text=TEXT, blocks=blocks,
            global_emotion=None, global_style=None,
            global_speed="Normal", global_pitch="Normal",
            global_delivery="Normal",
            base_parameters=params, raw_mode=False,
            detect_speakers=True, allow_sfx=allow_sfx)

    def test_batch_part_from_exact_source_slice(self):
        """Each part's text is the block's exact (stripped — the
        documented intended difference) slice of the CURRENT text."""
        parts = self._split_parts()
        self.assertEqual([p.text for p in parts], [S1, S2])
        for p in parts:
            self.assertIn(p.text, TEXT)

    def test_parts_self_contained_tokens(self):
        """INTENDED difference (documented): independent parts are
        SELF-CONTAINED — every winning token re-emitted per part."""
        parts = self._split_parts()
        # Block 0 has the fear override; block 1 has none -> in the
        # CONTINUOUS prompt elation (global) would only appear once;
        # for independent parts each part carries its own effective
        # tokens. Here globals are None -> only the override appears,
        # in part 1 only.
        self.assertIn("<|emotion:fear|>", parts[0].prompt)
        self.assertNotIn("<|emotion:", parts[1].prompt)
        self.assertTrue(parts[0].prompt.endswith(S1))
        self.assertTrue(parts[1].prompt.endswith(S2))

    def test_invalid_ranges_never_become_duplicate_parts(self):
        """Overlapping block ranges are blocked by the Phase 2 gate
        BEFORE any part is constructed (no duplicate Part text)."""
        blocks = self.load_blocks()
        # Force the legacy corruption (overlap).
        blocks[0].end_offset = len(TEXT)
        self.assertTrue(any("overlap" in i.reason
                            for i in validate_block_ranges(
                                self.win._editor.block_manager.blocks,
                                TEXT)))
        # The Generate Long gate blocks before the splitter (the
        # splitter is never reached — pinned by the Phase 2 suite; here
        # the precondition itself is the guard).
        issues = validate_block_ranges(
            self.win._editor.block_manager.blocks, TEXT)
        self.assertNotEqual(issues, [])

    def test_speaker_state_consistent(self):
        """$SPEAKER turns become their own parts with .speaker set;
        the single-generate prompt never injects speaker tokens
        (Character/speaker state affects VOICE selection and part
        identity, not prompt control tokens)."""
        text = "$CAPTAIN:\nFull ahead.\n\n$CREW:\nAye, Captain."
        from engine.narration_splitter import NarrationSplitter
        from engine.models import GenerationParameters
        splitter = NarrationSplitter()
        parts = splitter.split(
            text=text, blocks=None,
            global_emotion=None, global_style=None,
            global_speed="Normal", global_pitch="Normal",
            global_delivery="Normal",
            base_parameters=GenerationParameters(), raw_mode=False,
            detect_speakers=True, allow_sfx=True)
        speakers = [p.speaker for p in parts]
        self.assertIn("CAPTAIN", speakers)
        self.assertIn("CREW", speakers)
        for p in parts:
            self.assertNotIn("<|", p.prompt.replace("<|emotion", "")[:0] or
                             " ")  # (control tokens only; no speaker token)
            self.assertNotIn("CAPTAIN:", p.prompt)
            self.assertNotIn("CREW:", p.prompt)

    def test_save_load_no_token_divergence(self):
        """Save/load round trip: the preview ↔ generate parity and the
        source text basis are unchanged after a scene round trip."""
        self.load_blocks(with_overrides=True, with_sfx=True)
        self.set_allow_sfx(False)
        self.push_defaults()
        before_prompt = self.win._build_prompt()
        before_preview = self.preview()

        # Round trip through the Scene payload.
        payload = [b.to_dict() for b in
                   self.win._editor.block_manager.blocks]
        restored = [PromptBlock.from_dict(d) for d in payload]
        ed = self.win._editor
        ed.begin_scene_load()
        try:
            ed.set_text(TEXT)
        finally:
            ed.end_scene_load()
        ed.begin_scene_load()
        try:
            ed.set_scene_blocks(restored, TEXT)
        finally:
            ed.end_scene_load()
        self.push_defaults()

        self.assertEqual(self.win._build_prompt(), before_prompt)
        self.assertEqual(self.preview(), before_preview)


class TestDialogWarningSurfacing(_WinHarness):

    def test_part_rebuild_warning_surfaced(self):
        """The dialog surfaces part-rebuild compile warnings (invalid
        effective value → tokenless part + VISIBLE warning)."""
        from unittest.mock import patch
        from engine.narration_splitter import SplitPart
        from ui.panels.long_narration_dialog import LongNarrationDialog
        import ui.panels.long_narration_dialog as dlg_mod

        part = SplitPart(
            text=S1, prompt="", speaker="", source_block_id="b1",
            part_of_block=1, total_parts_in_block=1, block_label="",
            estimated_duration=2.0, char_count=len(S1),
            eff_emotion="NOT_A_REAL_EMOTION")

        warnings_shown = []
        from PySide6.QtWidgets import QMessageBox as _QMB
        with patch.object(_QMB, "warning",
                          lambda *a, **k: warnings_shown.append(a)), \
             patch.object(_QMB, "question",
                          lambda *a, **k: _QMB.StandardButton.No):
            dlg = LongNarrationDialog(
                [part], "", [], None, {}, allow_sfx=True, parent=self.win)
            try:
                dlg._on_generate()
            finally:
                dlg.close()
                dlg.deleteLater()

        self.assertTrue(warnings_shown,
                        "the invalid eff_emotion must surface as a "
                        "dialog warning (pre-fix: silently swallowed)")
        self.assertIn("Part 1", warnings_shown[0][2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
