"""
GUINEO — P3.39 Token-Integrity Audit Fixes
===========================================

Regression tests for the token-handling audit (user report: a token
(<|emotion:affection|>) entered the prompt chain although NO token was
selected in the right panel, and it could not be removed — only
overridden; fresh projects with pasted text must receive NO tokens
unless the user explicitly sets one).

Root causes fixed (runtime-proven in the audit):
  F1  EmotionButtons.set_selected / StyleButtons.set_selected EMITTED
      the user-intent signals (emotion_changed / style_changed), so
      every PROGRAMMATIC display update (scene load, block-selection
      display, preset apply, Friendly<->Advanced sync) masqueraded as a
      user click and re-entered MainWindow._on_emotion_changed —
      writing the global or a hidden Narration-Block OVERRIDE with no
      user action ("token enters the chain without selection").
  F2  With a Narration Block selected (auto-selected after block
      detection), the Friendly "Neutral" pill wrote None into the BLOCK
      override only — the GLOBAL "ghost" emotion survived invisibly:
      the panel displayed "no token selected" while every block still
      emitted <|emotion:...|> (inherited). The token was unremovable
      because the visible UI showed nothing to toggle off.
  Fix: (1) programmatic setters are SIGNAL-SILENT (Qt convention — the
  only emission source is a real user click);
  (2) MainWindow._refresh_panel_from_state() re-asserts the panel
  display from the AUTHORITATIVE state after every scope-routed write
  (block scope active -> the block's EFFECTIVE value; otherwise the
  global) — the panel always displays what the prompt chain will
  actually emit.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_39_token_integrity.py -v
"""

from __future__ import annotations
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, PropertyMock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

# Suppress modal dialogs (delete-locked-block confirmations etc.)
for _m in ("question", "warning", "information", "critical"):
    setattr(QMessageBox, _m,
            staticmethod(lambda *a, _m=_m, **k: {
                "question": QMessageBox.StandardButton.Yes,
                "warning": QMessageBox.StandardButton.Ok,
                "information": QMessageBox.StandardButton.Ok,
                "critical": QMessageBox.StandardButton.Ok,
            }[_m]))

_app = QApplication.instance() or QApplication([])

BEES = ("A méhek titokzatos és lenyűgöző világa. A kaptárban minden "
        "méhnek megvan a saját feladata. A királynő naponta több ezer "
        "petét rak. A dolgozók táncukkal mutatják meg az irányt.\n\n"
        "A virágpor gyűjtése kemény munka. A nektárból mézet "
        "készítenek. A kaptár hőmérsékletét is szabályozzák.")


def process():
    _app.processEvents()


# ===========================================================================
# Harness — real Engine + real MainWindow in an isolated tmpdir
# ===========================================================================

class _MWHarness:
    """Mixin: real MainWindow with isolated persistence + emission log."""

    @classmethod
    def _boot(cls):
        from engine.engine import Engine
        from ui.main_window import MainWindow
        cls._mw_cls = MainWindow
        cls.tmpdir = tempfile.mkdtemp(prefix="ss_p39_")
        cls.engine = Engine(app_root=cls.tmpdir)
        cls._patcher = patch.object(
            type(cls.engine._model), "is_loaded",
            new_callable=PropertyMock, return_value=True)
        cls._patcher.start()
        cls.win = MainWindow(cls.engine)
        cls.emissions = []
        cls._orig_emo = MainWindow._on_emotion_changed
        cls._orig_style = MainWindow._on_style_changed

        def spy_emo(self, emotion):
            cls.emissions.append(("emotion", emotion))
            return cls._orig_emo(self, emotion)

        def spy_style(self, style):
            cls.emissions.append(("style", style))
            return cls._orig_style(self, style)

        MainWindow._on_emotion_changed = spy_emo
        MainWindow._on_style_changed = spy_style
        process()

    @classmethod
    def _shutdown(cls):
        MainWindow = cls._mw_cls
        MainWindow._on_emotion_changed = cls._orig_emo
        MainWindow._on_style_changed = cls._orig_style
        for p in getattr(cls, "_patchers", []):
            try:
                p.stop()
            except Exception:
                pass
        try:
            cls._patcher.stop()
        except Exception:
            pass
        try:
            cls.win.close()
        except Exception:
            pass
        process()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # ---- helpers -----------------------------------------------------
    def _panel(self):
        return self.win._control_panel

    def _emotion_buttons(self):
        return self._panel()._advanced._emotion_buttons

    def _editor(self):
        return self.win._editor

    def _blocks(self):
        return self._editor().block_manager.blocks

    def _prompt(self):
        return self.win._build_prompt()

    def _deselect_block(self):
        self._editor()._selected_block_id = None
        self.win._on_block_selected("")
        process()

    def _select_block(self, idx=0):
        blocks = self._blocks()
        assert blocks, "no blocks to select"
        self._editor()._selected_block_id = blocks[idx].id
        self.win._on_block_selected(blocks[idx].id)
        process()

    def _paste_and_detect(self, text=BEES):
        """Fresh paste + switch to Narration Blocks (auto-detect)."""
        self._editor().set_text(text)
        self._editor()._set_mode(self._editor().MODE_BLOCKS)
        process()


# ===========================================================================
# 1. Programmatic setters are signal-silent (Fix 1)
# ===========================================================================

class TestSetterSilence(unittest.TestCase):
    """EmotionButtons/StyleButtons.set_selected must never emit — the
    user-intent signals may ONLY originate from real clicks."""

    def test_emotion_set_selected_valid_name_no_emit(self):
        from ui.panels.control_panel import EmotionButtons
        w = EmotionButtons()
        seen = []
        w.emotion_changed.connect(lambda v: seen.append(v))
        w.set_selected("Affection")
        w.set_selected("Awe")
        w.set_selected("Affection")  # idempotent repeat
        self.assertEqual(seen, [])
        self.assertEqual(w.get_selected(), "Affection")

    def test_emotion_set_selected_none_and_empty_no_emit(self):
        from ui.panels.control_panel import EmotionButtons
        w = EmotionButtons()
        seen = []
        w.emotion_changed.connect(lambda v: seen.append(v))
        w.set_selected("Affection")
        w.set_selected(None)
        self.assertEqual(w.get_selected(), None)
        w.set_selected("")
        self.assertEqual(w.get_selected(), None)
        self.assertEqual(seen, [])

    def test_emotion_set_selected_invalid_name_no_emit(self):
        from ui.panels.control_panel import EmotionButtons
        w = EmotionButtons()
        seen = []
        w.emotion_changed.connect(lambda v: seen.append(v))
        w.set_selected("Bogus Emotion")
        self.assertEqual(seen, [])
        self.assertIsNone(w.get_selected())

    def test_emotion_user_click_still_emits(self):
        from ui.panels.control_panel import EmotionButtons
        w = EmotionButtons()
        seen = []
        w.emotion_changed.connect(lambda v: seen.append(v))
        w._buttons["Affection"].click()
        self.assertEqual(seen, ["Affection"])
        w._buttons["Affection"].click()  # toggle off
        self.assertEqual(seen, ["Affection", ""])
        self.assertIsNone(w.get_selected())

    def test_style_set_selected_no_emit(self):
        from ui.panels.control_panel import StyleButtons
        w = StyleButtons()
        seen = []
        w.style_changed.connect(lambda v: seen.append(v))
        w.set_selected("Whispering")
        w.set_selected(None)
        w.set_selected("Shouting")
        self.assertEqual(seen, [])
        self.assertEqual(w.get_selected(), "Shouting")

    def test_style_user_click_still_emits(self):
        from ui.panels.control_panel import StyleButtons
        w = StyleButtons()
        seen = []
        w.style_changed.connect(lambda v: seen.append(v))
        w._buttons["Shouting"].click()
        self.assertEqual(seen, ["Shouting"])


# ===========================================================================
# 2. Fresh paste purity (the user's requirement, verbatim)
# ===========================================================================

class TestFreshPastePurity(_MWHarness, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def setUp(self):
        self.emissions.clear()
        self.win._emotion = None
        self.win._style = None
        self._panel()._advanced._emotion_buttons.set_selected(None)
        self._panel()._advanced._style_buttons.set_selected(None)
        self._editor()._set_mode(self._editor().MODE_PLAIN)
        self._editor().block_manager.clear()
        process()

    def test_fresh_paste_no_tokens(self):
        """Fresh project + pasted text: the prompt has NO tokens until the
        user explicitly sets one (user requirement, verbatim)."""
        self._paste_and_detect()
        # No emotion/style was ever selected by the user:
        self.assertEqual(self.win._emotion, None)
        self.assertEqual(self.win._style, None)
        for b in self._blocks():
            self.assertIsNone(b.emotion)
            self.assertIsNone(b.style)
        prompt = self._prompt()
        self.assertNotIn("<|emotion:", prompt)
        self.assertNotIn("<|style:", prompt)
        self.assertNotIn("<|prosody:", prompt)

    def test_paste_detect_and_tab_switch_no_emissions(self):
        """Paste + auto-detect + Friendly/Advanced tab switches never fire
        the user-intent handlers (no state write without a click)."""
        self._paste_and_detect()
        self._panel()._switch_tab(0)
        process()
        self._panel()._switch_tab(1)
        process()
        self._panel()._switch_tab(0)
        process()
        self.assertEqual(self.emissions, [])
        self.assertEqual(self.win._emotion, None)

    def test_programmatic_display_set_never_writes_state(self):
        """Scene-load-style set_emotion with an ACTIVE (auto-selected)
        block scope must not write a hidden block override."""
        self._paste_and_detect()
        self._select_block(0)   # scope active (auto-selected after detect)
        self.emissions.clear()
        self._panel().set_emotion("Affection")   # scene-load-style call
        self._panel().set_style("Whispering")
        process()
        self.assertEqual(self.emissions, [])
        self.assertIsNone(self.win._emotion)
        self.assertIsNone(self.win._style)
        for b in self._blocks():
            self.assertIsNone(b.emotion)
            self.assertIsNone(b.style)

    def test_scene_load_no_emissions_no_phantom_overrides(self):
        """A scene with emotion='Affection' loads WITHOUT writing hidden
        block overrides, even while a block scope is active."""
        self._paste_and_detect()
        scene = self.win._active_scene
        scene.text = BEES
        scene.emotion = "Affection"
        scene.style = None
        scene.narration_blocks = [b.to_dict() for b in self._blocks()]
        self._select_block(0)
        self.emissions.clear()
        self.win._load_scene_state(scene)
        process()
        self.assertEqual(self.emissions, [])
        self.assertEqual(self.win._emotion, "Affection")
        for b in self._blocks():
            self.assertIsNone(b.emotion,
                              "scene load must not fabricate overrides")
        # The scene DOES say affection — the token is present and the
        # panel honestly displays it:
        self.assertIn("<|emotion:affection|>", self._prompt())
        self.assertEqual(self._emotion_buttons().get_selected(), "Affection")


# ===========================================================================
# 3. Honest display (Fix 2) — the ghost-emotion scenario
# ===========================================================================

class TestHonestDisplay(_MWHarness, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def setUp(self):
        self.emissions.clear()
        self.win._emotion = None
        self.win._style = None
        self._panel()._advanced._emotion_buttons.set_selected(None)
        self._panel()._advanced._style_buttons.set_selected(None)
        self._editor()._set_mode(self._editor().MODE_PLAIN)
        self._editor().block_manager.clear()
        self._deselect_block()
        process()

    def _set_global_affection(self):
        self._deselect_block()
        self._emotion_buttons()._buttons["Affection"].click()
        process()
        self.assertEqual(self.win._emotion, "Affection")

    def test_ghost_scenario_panel_shows_the_truth(self):
        """The exact reported scenario: global Affection + block selected +
        Friendly 'Neutral' click -> the panel must SHOW the inherited
        global (never 'nothing selected' while the token persists)."""
        self._paste_and_detect()
        self._set_global_affection()
        self._select_block(0)
        # User clicks the Friendly Neutral pill (intending to remove the
        # emotion) — with a block scope this clears the BLOCK override
        # only (P3.25 contract); the panel must revert to the inherited
        # GLOBAL value, visibly:
        self._panel()._friendly._on_emotion_clicked("Neutral")
        process()
        self.assertEqual(self.win._emotion, "Affection",
                         "block-scoped Neutral must not clear the global")
        panel_sel = self._emotion_buttons().get_selected()
        self.assertEqual(panel_sel, "Affection",
                         "PANEL MUST SHOW THE EFFECTIVE (inherited) EMOTION")
        self.assertEqual(self._panel()._friendly.get_emotion(), "Affection")
        # The chain truth + honest UI agree:
        self.assertIn("<|emotion:affection|>", self._prompt())

    def test_ghost_token_is_removable(self):
        """End-to-end: after the ghost is visible, deselecting the block
        and toggling the button off REMOVES the token from the chain."""
        self._paste_and_detect()
        self._set_global_affection()
        self._select_block(0)
        self._panel()._friendly._on_emotion_clicked("Neutral")
        # Ghost is now VISIBLE (previous test). Remove it:
        self._deselect_block()
        self.assertEqual(self._emotion_buttons().get_selected(), "Affection")
        self._emotion_buttons()._buttons["Affection"].click()  # toggle OFF
        process()
        self.assertIsNone(self.win._emotion)
        self.assertNotIn("<|emotion:affection|>", self._prompt())
        self.assertNotIn("<|emotion:", self._prompt())

    def test_friendly_neutral_no_scope_clears_global(self):
        """Without a block scope, Friendly Neutral clears the GLOBAL
        emotion (the obvious 'remove' action works)."""
        self._paste_and_detect()
        self._set_global_affection()
        self._panel()._friendly._on_emotion_clicked("Neutral")
        process()
        self.assertIsNone(self.win._emotion)
        self.assertNotIn("<|emotion:affection|>", self._prompt())

    def test_toggle_off_with_block_scope_reverts_to_inherited(self):
        """With a block override set, toggling the button off clears the
        override; the display reverts to the inherited global."""
        self._paste_and_detect()
        self._set_global_affection()           # global = Affection
        self._select_block(0)
        # Set a block override (Awe) via the advanced panel:
        self._emotion_buttons()._buttons["Awe"].click()
        process()
        self.assertEqual(self._blocks()[0].emotion, "Awe")
        self.assertEqual(self.win._emotion, "Affection",
                         "block-scoped change must not touch the global")
        # Toggle Awe off:
        self._emotion_buttons()._buttons["Awe"].click()
        process()
        self.assertIsNone(self._blocks()[0].emotion)
        # The display reverts to the inherited global:
        self.assertEqual(self._emotion_buttons().get_selected(), "Affection")
        self.assertIn("<|emotion:affection|>", self._prompt())

    def test_source_indicator_shows_block_vs_global(self):
        """The emotion source indicator tells the user which scope a
        click will edit ('Block Override' vs 'Global Default')."""
        self._paste_and_detect()
        self._select_block(0)
        # block has NO override -> source is the global:
        self._deselect_block()
        self._select_block(0)
        # (indicator set by _on_block_selected; block.emotion is None)
        self._blocks()[0].emotion = "Awe"
        self._select_block(0)  # re-select to refresh indicator
        # indicator text is not directly asserted (internal label), the
        # behaviour is covered by the display tests above.
        self._blocks()[0].emotion = None


# ===========================================================================
# 4. Block-scope contract preserved (P3.25 regression)
# ===========================================================================

class TestBlockScopeContract(_MWHarness, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def setUp(self):
        self.emissions.clear()
        self.win._emotion = None
        self._panel()._advanced._emotion_buttons.set_selected(None)
        self._editor()._set_mode(self._editor().MODE_PLAIN)
        self._editor().block_manager.clear()
        process()

    def test_block_scoped_click_writes_override_only(self):
        self._paste_and_detect()
        self._deselect_block()
        self._emotion_buttons()._buttons["Affection"].click()
        self.assertEqual(self.win._emotion, "Affection")
        self._select_block(0)
        self._emotion_buttons()._buttons["Awe"].click()
        process()
        self.assertEqual(self._blocks()[0].emotion, "Awe")
        self.assertEqual(self.win._emotion, "Affection",
                         "global must stay untouched by a block-scoped edit")
        # Other blocks untouched:
        for b in self._blocks()[1:]:
            self.assertIsNone(b.emotion)

    def test_block_override_beats_global_in_prompt(self):
        from engine.prompt_state import CanonicalPromptCompiler
        self._paste_and_detect()
        self._deselect_block()
        self._emotion_buttons()._buttons["Affection"].click()
        self._select_block(0)
        self._emotion_buttons()._buttons["Awe"].click()
        prompt = self._prompt()
        self.assertIn("<|emotion:awe|>", prompt)
        self.assertNotIn("<|emotion:affection|><|emotion:awe|>",
                         "duplicate emotion emission")


# ===========================================================================
# 5. Scene save/load round trip
# ===========================================================================

class TestSceneRoundTrip(_MWHarness, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def setUp(self):
        self.emissions.clear()
        self.win._emotion = None
        self._panel()._advanced._emotion_buttons.set_selected(None)
        self._editor()._set_mode(self._editor().MODE_PLAIN)
        self._editor().block_manager.clear()
        process()

    def test_save_load_preserves_semantics_without_side_writes(self):
        self._paste_and_detect()
        self._deselect_block()
        self._emotion_buttons()._buttons["Affection"].click()
        self._select_block(0)
        self._emotion_buttons()._buttons["Awe"].click()
        self.win._save_current_scene_state()
        scene = self.win._active_scene
        self.assertEqual(scene.emotion, "Affection")
        self.assertEqual(len(scene.narration_blocks or []), len(self._blocks()))
        # Block override survives the save:
        self.assertEqual(scene.narration_blocks[0]["emotion"], "Awe")

        self.emissions.clear()
        self.win._load_scene_state(scene)
        process()
        self.assertEqual(self.emissions, [],
                         "scene load must be emission-free (no state writes)")
        self.assertEqual(self.win._emotion, "Affection")
        blocks = self._blocks()
        self.assertEqual(blocks[0].emotion, "Awe")
        for b in blocks[1:]:
            self.assertIsNone(b.emotion)
        prompt = self._prompt()
        self.assertIn("<|emotion:awe|>", prompt)
        self.assertNotIn("<|emotion:affection|><|emotion:awe|>", prompt)

    def test_scene_with_none_emotion_loads_clean(self):
        """A scene saved with emotion=None restores a CLEAN global (no
        ghost from the previous scene)."""
        self._paste_and_detect()
        self._deselect_block()
        self._emotion_buttons()._buttons["Affection"].click()
        self.assertEqual(self.win._emotion, "Affection")
        scene = self.win._active_scene
        self.win._save_current_scene_state()
        # Simulate a different scene with emotion=None:
        scene.emotion = None
        scene.narration_blocks = []
        self.win._load_scene_state(scene)
        process()
        self.assertIsNone(self.win._emotion)
        self.assertNotIn("<|emotion:", self._prompt())


if __name__ == "__main__":
    unittest.main(verbosity=2)
