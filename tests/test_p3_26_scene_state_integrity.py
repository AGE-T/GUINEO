"""
SpeechStudio P3.26 — Scene Switching + Narration Block State Integrity
=======================================================================
Runtime regression tests (task Part 19). REAL QApplication + REAL
MainWindow + NarrationEditor + Project + Scene + Character + RightPanel —
no AST-only checks. Every test class boots its OWN isolated environment
(own Project, Scenes, Characters, blocks, engine root, settings scope,
temp files cleaned in tearDownClass) per task Part 23.

Defect register (all reproduced BEFORE the fix in
ss/audit_probes_p326/probe_repro.py + probe_emotion_scope.py):

  P3.26-D1  Scene switch did not restore the per-Scene editor mode
            (widget-global mode leaked Scene B's BLOCKS into Scene A's
            PLAIN) and left Scene B's blocks painted as extra selections
            in the editor when Scene A had no blocks. Root causes:
            (a) editor mode never persisted/restored per Scene;
            (b) the no-blocks restore path never re-rendered the editor
                block data (stale block visuals + gutter);
            (c) set_text during the switch fed the text swap into
                NarrationBlockManager.on_text_changed which OFFSET-SHIFTED
                the previous Scene's live block objects;
            (d) _selected_block_id + control-panel block scope never
                explicitly reset (cleared only via cursor-signal side
                effects).
  P3.26-D2  B1 could not be selected on the first click (B2 had to be
            clicked first). Root cause: block selection was driven ONLY
            by cursorPositionChanged; the gutter click calls
            setTextCursor(block.start), which emits NO signal when the
            cursor already sits at that offset (e.g. 0 right after a
            Scene load / auto-detect), so the selection never ran.
  P3.26-D3  Advanced global Emotion (Enthusiasm) did not propagate.
            Root cause: the control panel's _active_block_id became a
            STALE phantom scope (mode switch to Plain, Re-detect block-id
            replacement, block delete, Scene switch) and semantic control
            changes were routed into _update_block_override — either
            written into a hidden block override or silently dropped,
            never touching the global / preview / request.

Fixes under test (see OPEN_BUGS.md P3.26 register):
  - engine/models.py: Scene.editor_mode + Scene.selected_block_id
    (additive, backward-compatible, deterministic defaults).
  - ui/panels/narration_editor.py: begin/end_scene_load atomic load
    window; set_scene_blocks (full model+visual reset incl. zero blocks);
    set_editor_mode / editor_mode_name; block_click_requested signal
    (gutter + text clicks drive selection explicitly); leaving BLOCKS
    mode clears the selection; _post_analyze/delete/merge/create emit
    block_selected so the scope always follows reality.
  - ui/main_window.py: _load_scene_state restores text+blocks+mode+
    selection atomically and resets the block scope FIRST; per-Scene
    mode+selection persisted in _save_current_scene_state;
    _block_scope_valid_id() validates the scope (mode + existence)
    before any semantic change is routed into an override.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_26_scene_state_integrity.py -v
"""

from __future__ import annotations
import os
import sys
import time
import shutil
import tempfile
import unittest
from unittest.mock import patch, PropertyMock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

_QAPP = QApplication.instance() or QApplication([])

# Modal dialogs auto-answer Yes (never block the offscreen event loop).
for _n in ("warning", "critical", "information", "question", "about"):
    def _mk(nm):
        def _w(*a, **k):
            return QMessageBox.StandardButton.Yes
        return _w
    setattr(QMessageBox, _n, staticmethod(_mk(_n)))

# Fake GPU layer ONLY (no torch in this sandbox) — everything above the
# model call is REAL production code.
import types
import numpy as np

_fake_torch = types.ModuleType("torch")


class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_fake_torch.no_grad = lambda: _NoGrad()
_fake_torch.manual_seed = lambda s: None
_fake_torch.from_numpy = lambda arr: arr
_fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)
sys.modules.setdefault("torch", _fake_torch)


def process(ms=25):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _QAPP.processEvents()
        time.sleep(0.004)


LONG_A = (
    "The old observatory stood silent above the fog line.\n\n"
    "Its dome had not turned in thirty years, yet every night the caretaker\n"
    "climbed the spiral stair to dust the great lens.\n\n"
    "The villagers believed the place held a secret, and they were right,\n"
    "though none of them could have guessed what it was.\n\n"
    "On the last evening of autumn, a light appeared inside the dome."
)

SHORT_B = (
    "Engineer: the hull is breached.\n\n"
    "Lady: then we seal it now.\n\n"
    "Narrator: the ship groaned."
)


class _IsolatedHarness:
    """Every test class gets its OWN MainWindow + engine + temp root
    (task Part 23: no shared mutable Project/Scene/Character state)."""

    @classmethod
    def _boot(cls):
        from engine.engine import Engine
        from engine.project_manager import ProjectManager
        from engine.recents_manager import RecentsManager
        from ui.main_window import MainWindow
        cls.tmpdir = tempfile.mkdtemp(prefix="ss_p326_%s_" % cls.__name__)
        cls.engine = Engine(app_root=cls.tmpdir)
        cls._patchers = [
            patch.object(type(cls.engine._model), "is_loaded",
                         new_callable=PropertyMock, return_value=True),
            patch.object(type(cls.engine._model), "device",
                         new_callable=PropertyMock, return_value="cpu"),
        ]
        for p in cls._patchers:
            p.start()
        cls.win = MainWindow(cls.engine)
        cls.win._project_manager = ProjectManager(
            os.path.join(cls.tmpdir, "projects"))
        cls.win._recents_manager = RecentsManager()
        cls.win._refresh_sidebar()
        process()

    @classmethod
    def _shutdown(cls):
        for p in getattr(cls, "_patchers", []):
            try:
                p.stop()
            except Exception:
                pass
        try:
            cls.win.close()
        except Exception:
            pass
        process()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # ---- helpers -----------------------------------------------------
    def scene_by_name(self, name):
        for s in self.win._active_project.scenes:
            if s.name == name:
                return s
        raise AssertionError("scene %r not found" % name)

    def reset_semantics(self):
        """Isolation: clear the shared emotion/style selection + globals
        (the Advanced buttons are checkable toggles — a click on an
        already-checked button TOGGLES IT OFF)."""
        self.win._control_panel.set_emotion(None)
        self.win._emotion = None

    def make_scene(self, name, text, activate=True):
        from engine.models import Scene
        proj = self.win._active_project
        order = max((s.sort_order for s in proj.scenes), default=-1) + 1
        scene = Scene(name=name, project_id=proj.id, text=text,
                      sort_order=order)
        proj.scenes.append(scene)
        if activate:
            self.win._save_current_scene_state()
            self.win._active_scene = scene
            self.win._load_scene_state(scene)
        process()
        return scene

    def make_characters(self, *names):
        proj = self.win._active_project
        for name in names:
            self.win._on_character_create_quick()
            process()
            created = proj.characters[-1]
            self.win._on_character_rename_requested(created.id, name)
            process()
        return {c.name: c for c in proj.characters}

    def enter_blocks_mode(self):
        self.win._editor._blocks_btn.setChecked(True)
        process()

    def enter_plain_mode(self):
        self.win._editor._plain_btn.setChecked(True)
        process()

    def gutter_click(self, block_index):
        """REAL QTest click on block N's (1-based) gutter header."""
        ed = self.win._editor._editor
        text = ed.toPlainText()
        blocks = ed._blocks
        self.assertTrue(1 <= block_index <= len(blocks))
        block = blocks[block_index - 1]
        start = max(0, min(block.start_offset, len(text)))
        end = max(start, min(block.end_offset, len(text)))
        cur = QTextCursor(ed.document())
        cur.setPosition(start)
        y_top = ed.cursorRect(cur).top()
        gw = ed._block_gutter_widget
        QTest.mouseClick(gw, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier,
                         QPointF(gw.width() / 2.0, y_top + 8).toPoint())
        process()

    def click_emotion(self, name):
        btn = self.win._control_panel._advanced._emotion_buttons._buttons.get(
            name)
        self.assertIsNotNone(btn, "emotion button %r missing" % name)
        btn.click()
        process()
        return btn


# ===========================================================================
# 1-2. Scene A -> B -> A restoration (Plain Text + Narration Blocks)
# ===========================================================================

class TestSceneSwitchModeRestoration(unittest.TestCase, _IsolatedHarness):
    """Task §19.1 / §19.2 + Part 6: the per-Scene editor mode must
    survive A -> B -> A in BOTH directions."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_01_plain_scene_restores_plain_after_blocks_scene(self):
        win = self.win
        # Scene A: long text, PLAIN (default)
        a = self.make_scene("SceneA", LONG_A)
        self.enter_plain_mode()
        win._save_current_scene_state()
        # Scene B: short text + BLOCKS + characters
        chars = self.make_characters("Engineer", "Lady", "Narrator")
        b = self.make_scene("SceneB", SHORT_B)
        self.enter_blocks_mode()
        blocks = win._editor.block_manager.blocks
        self.assertGreaterEqual(len(blocks), 2)
        for blk, (cname, _) in zip(blocks, list(chars.items())[:3]):
            blk.character_id = chars[cname].id
        win._sync_scene_characters()
        win._save_current_scene_state()

        # ---- B -> A ----
        win._switch_to_scene(a.id)
        process()
        ed = win._editor
        self.assertEqual(ed._mode, ed.MODE_PLAIN,
                         "Scene A must return in PLAIN mode")
        self.assertEqual(ed.get_text(), LONG_A)
        self.assertFalse(ed._editor._blocks,
                         "no stale inner-editor block data on Scene A")
        self.assertFalse(ed.block_manager.has_blocks,
                         "Scene A has no blocks")
        self.assertIsNone(ed._selected_block_id)
        self.assertTrue(ed._properties_panel.isHidden())
        self.assertEqual(ed._editor.extraSelections(), [],
                         "no stale block backgrounds painted")
        self.assertIsNone(win._control_panel._active_block_id,
                          "block scope must not leak from B to A")

    def test_02_blocks_scene_restores_blocks_after_plain_scene(self):
        win = self.win
        chars = self.make_characters("Engineer", "Lady", "Narrator")
        a = self.scene_by_name("SceneA")
        b = self.scene_by_name("SceneB")

        # A -> B (B must come back in BLOCKS)
        win._switch_to_scene(b.id)
        process()
        self.assertEqual(win._editor._mode, win._editor.MODE_BLOCKS,
                         "Scene B must return in BLOCKS mode")
        blocks = win._editor.block_manager.blocks
        self.assertGreaterEqual(len(blocks), 2)
        assigned = [b.character_id for b in blocks if b.character_id]
        self.assertGreaterEqual(len(assigned), 2,
                                "B's Character assignments restored")
        # gutter visible, properties panel visible
        self.assertFalse(win._editor._properties_panel.isHidden())
        self.assertFalse(win._editor._analyze_btn.isHidden())

        # B -> A (A must come back in PLAIN)
        win._switch_to_scene(a.id)
        process()
        self.assertEqual(win._editor._mode, win._editor.MODE_PLAIN)
        # A -> B again — still exact
        win._switch_to_scene(b.id)
        process()
        self.assertEqual(win._editor._mode, win._editor.MODE_BLOCKS)
        self.assertGreaterEqual(len(win._editor.block_manager.blocks), 2)

    def test_03_reverse_direction_blocks_to_plain(self):
        """Part 6 reverse: A=BLOCKS, B=PLAIN -> A returns BLOCKS."""
        win = self.win
        c = self.make_scene("SceneC", "One paragraph is enough.\n\n"
                            "Two paragraphs for detection.")
        self.enter_blocks_mode()
        self.assertGreaterEqual(len(win._editor.block_manager.blocks), 1)
        win._save_current_scene_state()
        d = self.make_scene("SceneD", "A plain single paragraph.")
        self.enter_plain_mode()
        win._save_current_scene_state()

        win._switch_to_scene(c.id)
        process()
        self.assertEqual(win._editor._mode, win._editor.MODE_BLOCKS,
                         "Scene C returns in BLOCKS")
        win._switch_to_scene(d.id)
        process()
        self.assertEqual(win._editor._mode, win._editor.MODE_PLAIN,
                         "Scene D returns in PLAIN")


# ===========================================================================
# 3. Scene Character isolation (UI + model)
# ===========================================================================

class TestSceneCharacterIsolation(unittest.TestCase, _IsolatedHarness):
    """Task §19.3 + Part 7: no Character rows/IDs may leak from B to A."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_no_character_leak_between_scenes(self):
        win = self.win
        a = self.make_scene("SceneA", LONG_A)   # no characters
        chars = self.make_characters("Engineer", "Lady", "Narrator")

        b = self.make_scene("SceneB", SHORT_B)
        self.enter_blocks_mode()
        blocks = win._editor.block_manager.blocks
        for blk, (cname, _) in zip(blocks, list(chars.items())[:3]):
            blk.character_id = chars[cname].id
        win._sync_scene_characters()
        win._save_current_scene_state()
        self.assertTrue(b.character_ids,
                        "Scene B model carries character ids")

        # B -> A
        win._switch_to_scene(a.id)
        process()
        # UI: no block → no character widgets in the properties panel path
        self.assertEqual(win._editor._mode, win._editor.MODE_PLAIN)
        self.assertIsNone(win._editor._selected_block_id)
        # Model: Scene A has NO character ids
        b_ids = set(b.character_ids)
        leaked = [cid for cid in a.character_ids if cid in b_ids]
        self.assertEqual(leaked, [],
                         "Scene B Character ids leaked into Scene A model")
        self.assertEqual(a.character_ids, [],
                         "Scene A (no Characters) must have empty "
                         "character_ids, got %r" % a.character_ids)

    def test_character_gutter_names_follow_active_scene(self):
        """The editor's character mirror must not paint B's characters on
        A's blocks (A has none). Self-contained scenes."""
        win = self.win
        chars = self.make_characters("Solo")
        self.make_scene("GutterA", "Gutter scene A text.")
        b = self.make_scene("GutterB", "Gutter B one.\n\nGutter B two.")
        self.enter_blocks_mode()
        blocks = win._editor.block_manager.blocks
        self.assertGreaterEqual(len(blocks), 1)
        blocks[0].character_id = chars["Solo"].id
        win._sync_scene_characters()
        win._save_current_scene_state()

        a = self.scene_by_name("GutterA")
        win._switch_to_scene(a.id)
        process()
        # A is plain with no blocks: gutter hidden, no character strips
        self.assertFalse(win._editor._editor._blocks)
        self.assertTrue(win._editor._editor._block_gutter_widget.isHidden()
                        or not win._editor._editor._block_gutter_widget
                        .isVisibleTo(win._editor._editor))


# ===========================================================================
# 4-5. Block widget / Character widget cleanup
# ===========================================================================

class TestBlockWidgetCleanup(unittest.TestCase, _IsolatedHarness):
    """Task §19.4 / §19.5: the inner editor's block widgets/data and the
    properties-panel Character widgets must be fully rebuilt per Scene."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_block_model_and_visuals_fully_replaced_per_scene(self):
        win = self.win
        a = self.make_scene("CleanA", "Alpha text one.\n\nAlpha text two.")
        self.enter_blocks_mode()
        blocks_a = list(win._editor.block_manager.blocks)
        self.assertGreaterEqual(len(blocks_a), 1)
        win._save_current_scene_state()

        b = self.make_scene("CleanB", "Beta text one.\n\nBeta text two.")
        self.enter_blocks_mode()
        process()
        blocks_b = win._editor.block_manager.blocks
        self.assertGreaterEqual(len(blocks_b), 1)
        win._save_current_scene_state()
        self.assertFalse({x.id for x in blocks_a} & {x.id for x in blocks_b},
                         "block IDs must be per-Scene instances")

        # A -> B -> A: the editor's inner block list must be exactly A's
        win._switch_to_scene(a.id)
        process()
        inner_ids = {b.id for b in win._editor._editor._blocks}
        model_ids = {b.id for b in win._editor.block_manager.blocks}
        self.assertEqual(inner_ids, model_ids,
                         "inner editor block data != manager model")
        self.assertEqual(model_ids, {x.id for x in blocks_a},
                         "Scene A's original blocks must be restored "
                         "(same ids, exact as stored)")

    def test_zero_block_scene_clears_widgets_and_extraselections(self):
        win = self.win
        a = self.make_scene("SceneA", "Only text, never entered blocks.")
        win._save_current_scene_state()
        b = self.make_scene("SceneB", "Beta one.\n\nBeta two.")
        self.enter_blocks_mode()
        self.assertGreaterEqual(len(win._editor.block_manager.blocks), 1)
        win._save_current_scene_state()

        win._switch_to_scene(a.id)
        process()
        self.assertEqual(win._editor._editor._blocks, [],
                         "stale block widgets remain in inner editor")
        self.assertEqual(win._editor._editor.extraSelections(), [],
                         "stale block backgrounds remain painted")


# ===========================================================================
# 6. Re-detect NOT required after Scene switch
# ===========================================================================

class TestRedetectNotRequired(unittest.TestCase, _IsolatedHarness):
    """Task §19.6 + Part 8: the Scene must already be correct WITHOUT
    pressing Re-detect."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_scene_correct_without_redetect(self):
        win = self.win
        a = self.make_scene("SceneA", "Alpha one.\n\nAlpha two.")
        self.enter_blocks_mode()
        win._editor.block_manager.blocks[0].emotion = "Awe"
        win._editor.block_manager.blocks[0].character_id = None
        win._save_current_scene_state()
        ids_before = [b.id for b in win._editor.block_manager.blocks]
        overrides_before = [(b.emotion, b.style) for b in
                            win._editor.block_manager.blocks]

        b = self.make_scene("SceneB", "Beta one.\n\nBeta two.")
        process()
        self.enter_plain_mode()
        win._save_current_scene_state()

        # Switch back — NO Re-detect pressed anywhere.
        win._switch_to_scene(a.id)
        process()
        blocks = win._editor.block_manager.blocks
        self.assertEqual([x.id for x in blocks], ids_before,
                         "blocks must be exact without Re-detect")
        self.assertEqual([(x.emotion, x.style) for x in blocks],
                         overrides_before,
                         "overrides must be exact without Re-detect")
        self.assertEqual(win._editor._mode, win._editor.MODE_BLOCKS)


# ===========================================================================
# 7-8. B1 first-click + B1/B2/B3 selection sequence
# ===========================================================================

class TestB1FirstClick(unittest.TestCase, _IsolatedHarness):
    """Task §19.7 / §19.8 + Parts 9/10: B1 selectable on the FIRST click;
    selection sequence works in every context."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def _fresh_blocks_state(self):
        win = self.win
        win._editor.set_text(
            "First paragraph sets the scene.\n\n"
            "Second paragraph develops conflict.\n\n"
            "Third paragraph resolves.\n\n"
            "Fourth paragraph closes.")
        self.enter_blocks_mode()
        blocks = win._editor.block_manager.blocks
        self.assertEqual(len(blocks), 4)
        # Simulate the post-scene-load state: cursor at 0, no selection.
        cur = win._editor._editor.textCursor()
        cur.setPosition(0)
        win._editor._editor.setTextCursor(cur)
        win._editor._selected_block_id = None
        win._editor._update_properties_panel()
        win._editor._render_block_visuals()
        process()
        return blocks

    def test_b1_selectable_on_first_click(self):
        blocks = self._fresh_blocks_state()
        self.assertEqual(self.win._editor._editor.textCursor().position(), 0,
                         "precondition: cursor already at B1 start")
        self.gutter_click(1)
        self.assertEqual(self.win._editor._selected_block_id,
                         blocks[0].id,
                         "B1 MUST be selectable on the FIRST click")

    def test_b1_b2_b3_b1_sequence(self):
        blocks = self._fresh_blocks_state()
        for idx in (1, 2, 3, 1):
            self.gutter_click(idx)
            self.assertEqual(self.win._editor._selected_block_id,
                             blocks[idx - 1].id,
                             "click on B%d must select it" % idx)

    def test_text_area_click_selects_first_block(self):
        blocks = self._fresh_blocks_state()
        ed = self.win._editor._editor
        rect = ed.cursorRect(QTextCursor(ed.document()))
        QTest.mouseClick(ed.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, rect.center())
        process()
        self.assertEqual(self.win._editor._selected_block_id, blocks[0].id,
                         "text-area click must select B1 immediately")

    def test_selection_after_scene_switch_then_first_click(self):
        """Part 10: selection works immediately after a Scene switch."""
        win = self.win
        self._fresh_blocks_state()
        win._save_current_scene_state()
        b = self.make_scene("SceneB", "Other scene text.\n\nMore text.")
        process()
        win._switch_to_scene(win._active_project.scenes[0].id)
        process()
        blocks = win._editor.block_manager.blocks
        self.assertGreaterEqual(len(blocks), 1)
        self.gutter_click(1)
        self.assertEqual(win._editor._selected_block_id, blocks[0].id)


# ===========================================================================
# 9-12. Global emotion: Advanced, Plain Text, preview, GenerationRequest
# ===========================================================================

class TestGlobalEmotionPropagation(unittest.TestCase, _IsolatedHarness):
    """Task §19.9-§19.12 + Parts 11/12/14: Enthusiasm must reach the
    semantic state, the preview, and the actual GenerationRequest."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def _token(self, name):
        from engine.higgs_tokens import EMOTION_BY_NAME
        return "<|emotion:%s|>" % EMOTION_BY_NAME[name].tag

    def test_09_global_enthusiasm_in_advanced(self):
        win = self.win
        self.reset_semantics()
        win._editor.set_text("The quick brown fox jumps over the lazy dog.")
        process()
        self.click_emotion("Enthusiasm")
        self.assertEqual(win._emotion, "Enthusiasm")
        self.assertEqual(win._control_panel.get_emotion(), "Enthusiasm")
        self.assertIn(self._token("Enthusiasm"), win._build_prompt())

    def test_10_global_enthusiasm_in_plain_text_mode(self):
        """Part 14: Plain Text mode — no hidden dependency on blocks."""
        win = self.win
        self.reset_semantics()
        self.enter_plain_mode()
        win._editor.set_text("Plain mode enthusiasm test.")
        process()
        self.click_emotion("Enthusiasm")
        self.assertEqual(win._emotion, "Enthusiasm")
        preview = win._build_prompt()
        self.assertIn(self._token("Enthusiasm"), preview)
        # The generation request carries the same token
        captured = self._capture_request()
        self.assertIsNotNone(captured)
        self.assertIn(self._token("Enthusiasm"), captured.text)
        self.assertEqual(captured.emotion, "Enthusiasm")

    def test_11_preview_panel_propagation(self):
        win = self.win
        self.reset_semantics()
        win._editor.set_text("Preview propagation sentence.")
        process()
        self.click_emotion("Enthusiasm")
        # The Right Panel's PROMPT PREVIEW section text:
        preview_widget = (win._control_panel._advanced._preview._preview)
        txt = preview_widget.toPlainText()
        self.assertIn(self._token("Enthusiasm"), txt,
                      "Right Panel preview must show the token")

    def test_12_generation_request_propagation(self):
        win = self.win
        self.reset_semantics()
        win._editor.set_text("Request propagation sentence.")
        process()
        self.click_emotion("Enthusiasm")
        captured = self._capture_request()
        self.assertIsNotNone(captured)
        self.assertIn(self._token("Enthusiasm"), captured.text)
        self.assertEqual(captured.emotion, "Enthusiasm")

    def test_all_six_emotions_propagate(self):
        """Part 12: the full six-emotion matrix."""
        win = self.win
        self.reset_semantics()
        win._editor.set_text("Six emotion matrix sentence.")
        process()
        for name in ("Elation", "Enthusiasm", "Fear", "Sadness",
                     "Anger", "Contentment"):
            self.reset_semantics()
            self.click_emotion(name)
            self.assertEqual(win._emotion, name,
                             "%s: global state" % name)
            self.assertIn(self._token(name), win._build_prompt(),
                          "%s: compiled token" % name)
            self.assertEqual(win._control_panel.get_emotion(), name,
                             "%s: panel selection" % name)

    def _capture_request(self):
        """Drive the REAL _start_generation and capture the
        GenerationRequest handed to the engine."""
        win = self.win
        captured = {}

        class _FakeFuture:
            def add_done_callback(self, cb):
                pass

        def fake_generate(request):
            captured["req"] = request
            return _FakeFuture()

        orig = self.engine.generate
        self.engine.generate = fake_generate
        try:
            win._start_generation(win._editor.get_text())
            process()
        finally:
            self.engine.generate = orig
        return captured.get("req")


# ===========================================================================
# 13. Block emotion must not modify the global
# ===========================================================================

class TestGlobalVsBlockEmotion(unittest.TestCase, _IsolatedHarness):
    """Task §19.13 + Part 13: Block Emotion is a block override; the
    Scene-global stays untouched; deselect returns the global."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_block_emotion_does_not_touch_global(self):
        win = self.win
        self.reset_semantics()
        win._editor.set_text("Global sad story.\n\nSecond sad paragraph.")
        process()
        self.enter_plain_mode()
        self.click_emotion("Sadness")
        self.assertEqual(win._emotion, "Sadness")

        self.enter_blocks_mode()
        blocks = win._editor.block_manager.blocks
        self.assertGreaterEqual(len(blocks), 2)
        # Real selection path: select B1 via the click-driven mechanism
        win._editor._on_block_click_requested(blocks[0].start_offset)
        self.assertEqual(win._control_panel._active_block_id, blocks[0].id)

        self.click_emotion("Awe")
        self.assertEqual(blocks[0].emotion, "Awe",
                         "B1 override must be written")
        self.assertEqual(win._emotion, "Sadness",
                         "GLOBAL must still be Sadness after block edit")

        # Deselect: the Advanced panel must show the global again.
        win._editor._selected_block_id = None
        win._editor.block_selected.emit("")
        process()
        self.assertEqual(win._control_panel.get_emotion(), "Sadness",
                         "after deselect the panel shows the global")


# ===========================================================================
# 14-15. Scene emotion persistence + isolation
# ===========================================================================

class TestSceneEmotionIsolation(unittest.TestCase, _IsolatedHarness):
    """Task §19.14 / §19.15 + Part 15: A(Enthusiasm) <-> B(Sadness) with
    zero leakage."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_emotion_persists_and_isolates_across_switches(self):
        win = self.win
        self.reset_semantics()
        a = self.make_scene("EmoA", "Scene A text.")
        self.enter_plain_mode()
        self.click_emotion("Enthusiasm")
        win._save_current_scene_state()
        self.assertEqual(a.emotion, "Enthusiasm")

        b = self.make_scene("EmoB", "Scene B text.")
        self.enter_plain_mode()
        self.reset_semantics()
        self.click_emotion("Sadness")
        win._save_current_scene_state()
        self.assertEqual(b.emotion, "Sadness")

        # A -> B -> A -> B: no leakage in any direction
        win._switch_to_scene(a.id); process()
        self.assertEqual(win._emotion, "Enthusiasm",
                         "A must restore Enthusiasm")
        win._switch_to_scene(b.id); process()
        self.assertEqual(win._emotion, "Sadness", "B must restore Sadness")
        win._switch_to_scene(a.id); process()
        self.assertEqual(win._emotion, "Enthusiasm",
                         "A must restore Enthusiasm again")

    def test_none_emotion_restores_none(self):
        """Part 17: a Scene with emotion=None must NOT inherit the
        previous Scene's emotion."""
        win = self.win
        self.reset_semantics()
        a = self.make_scene("NoneA", "With emotion.")
        self.enter_plain_mode()
        self.click_emotion("Fear")
        win._save_current_scene_state()
        self.assertEqual(a.emotion, "Fear")

        b = self.make_scene("NoneB", "Without emotion.")
        process()
        # Loading fresh B restores its stored emotion (None) — the panel
        # and the global must both be None with NO click required.
        self.assertIsNone(win._emotion,
                          "fresh Scene B (None) must not inherit A's Fear")
        self.assertIsNone(win._control_panel.get_emotion())
        win._save_current_scene_state()
        self.assertIsNone(b.emotion)

        win._switch_to_scene(a.id); process()
        self.assertEqual(win._emotion, "Fear")
        win._switch_to_scene(b.id); process()
        self.assertIsNone(win._emotion,
                          "Scene B (None) inherited A's Fear")


# ===========================================================================
# 16. Scene voice isolation
# ===========================================================================

class TestSceneVoiceIsolation(unittest.TestCase, _IsolatedHarness):
    """Task §19.16: each Scene restores its OWN voice (incl. None)."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_voice_isolated_and_none_preserved(self):
        import wave as wavemod
        win = self.win
        self.reset_semantics()
        ref = os.path.join(self.tmpdir, "ref.wav")
        with wavemod.open(ref, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
            w.writeframes(b"\x00\x00" * 24000)
        voice = self.engine.create_voice("IsolatedVoice")
        self.engine.import_voice_reference(voice.id, ref)
        voice = self.engine.get_voice(voice.id)
        win._control_panel.populate_voices(self.engine.list_voices())

        a = self.make_scene("VoiceA", "Voice scene A.")
        win._control_panel.set_selected_voice_id(voice.id)
        win._save_current_scene_state()
        self.assertEqual(a.voice_profile_id, voice.id)

        b = self.make_scene("VoiceB", "Voice scene B.")
        win._control_panel.set_selected_voice_id(None)
        win._save_current_scene_state()
        self.assertIsNone(b.voice_profile_id)

        win._switch_to_scene(a.id); process()
        self.assertEqual(win._control_panel.get_selected_voice_id(),
                         voice.id, "A restores its voice")
        win._switch_to_scene(b.id); process()
        self.assertIsNone(win._control_panel.get_selected_voice_id(),
                          "B restores None (no A leak)")
        win._switch_to_scene(a.id); process()
        self.assertEqual(win._control_panel.get_selected_voice_id(),
                         voice.id)


# ===========================================================================
# 17. Scene generation parameter isolation
# ===========================================================================

class TestSceneParamsIsolation(unittest.TestCase, _IsolatedHarness):
    """Task §19.17: generation parameters are Scene-scoped (incl. None ->
    defaults, never the previous Scene's values)."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_parameters_isolated(self):
        from engine.models import GenerationParameters
        win = self.win
        self.reset_semantics()
        a = self.make_scene("ParamA", "Params scene A.")
        custom = GenerationParameters(temperature=0.75, top_k=12)
        win._control_panel.set_parameters(custom)
        win._save_current_scene_state()
        self.assertAlmostEqual(a.parameters.temperature, 0.75, delta=0.005)

        b = self.make_scene("ParamB", "Params scene B.")
        process()
        win._control_panel.set_parameters(GenerationParameters())
        win._save_current_scene_state()

        win._switch_to_scene(a.id); process()
        got_a = win._control_panel.get_parameters()
        self.assertAlmostEqual(got_a.temperature, 0.75, delta=0.005)
        self.assertEqual(got_a.top_k, 12)

        win._switch_to_scene(b.id); process()
        got_b = win._control_panel.get_parameters()
        defaults = GenerationParameters()
        self.assertGreater(abs(got_b.temperature - 0.75), 0.01,
                           "Scene B inherited A's temperature")
        self.assertAlmostEqual(got_b.temperature, defaults.temperature,
                               delta=0.01)
        self.assertEqual(got_b.top_k, defaults.top_k)


# ===========================================================================
# Additional P3.26 contract: stale block scope must never swallow a
# semantic control change (Part 11 root cause)
# ===========================================================================

class TestStaleBlockScopeCleared(unittest.TestCase, _IsolatedHarness):
    """The four stale-scope paths from the user's defect report."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def _token(self, name):
        from engine.higgs_tokens import EMOTION_BY_NAME
        return "<|emotion:%s|>" % EMOTION_BY_NAME[name].tag

    def _setup_blocks(self):
        win = self.win
        win._editor.set_text("Alpha paragraph.\n\nBeta paragraph.\n\n"
                             "Gamma paragraph.")
        process()
        self.enter_blocks_mode()
        return list(win._editor.block_manager.blocks)

    def test_plain_mode_with_leftover_scope_writes_global(self):
        win = self.win
        self.reset_semantics()
        blocks = self._setup_blocks()
        win._editor._on_block_click_requested(blocks[1].start_offset)
        self.assertEqual(win._control_panel._active_block_id, blocks[1].id)
        self.enter_plain_mode()
        process()
        # scope cleared by the mode switch:
        self.assertIsNone(win._control_panel._active_block_id)
        self.click_emotion("Enthusiasm")
        self.assertEqual(win._emotion, "Enthusiasm")
        self.assertIn(self._token("Enthusiasm"), win._build_prompt())

    def test_redetect_replaces_scope_selection(self):
        win = self.win
        self.reset_semantics()
        blocks = self._setup_blocks()
        win._editor._on_block_click_requested(blocks[1].start_offset)
        self.assertEqual(win._control_panel._active_block_id, blocks[1].id)
        win._editor._on_analyze_again()   # no manual edits -> rebuild
        process()
        new_blocks = win._editor.block_manager.blocks
        # scope must follow the NEW selection (never the dead id):
        self.assertIn(win._control_panel._active_block_id,
                      {b.id for b in new_blocks},
                      "scope must reference a live block after Re-detect")
        # And a click now writes the visible selection's override (or the
        # global when nothing is selected) — never a silent drop:
        self.click_emotion("Enthusiasm")
        scope = win._control_panel._active_block_id
        if scope:
            sel = win._editor.block_manager.get_block(scope)
            self.assertEqual(sel.emotion, "Enthusiasm")
        else:
            self.assertEqual(win._emotion, "Enthusiasm")

    def test_delete_selected_block_clears_scope(self):
        win = self.win
        self.reset_semantics()
        blocks = self._setup_blocks()
        win._editor._on_block_click_requested(blocks[1].start_offset)
        self.assertEqual(win._control_panel._active_block_id, blocks[1].id)
        win._editor._selected_block_id = blocks[1].id
        win._editor._on_delete_block()
        process()
        self.assertIsNone(win._control_panel._active_block_id,
                          "delete must clear the scope")
        self.click_emotion("Enthusiasm")
        self.assertEqual(win._emotion, "Enthusiasm")

    def test_scene_switch_clears_scope(self):
        win = self.win
        self.reset_semantics()
        blocks = self._setup_blocks()
        win._editor._on_block_click_requested(blocks[1].start_offset)
        self.assertEqual(win._control_panel._active_block_id, blocks[1].id)
        win._save_current_scene_state()
        b = self.make_scene("ScopeB", "New scene.")
        process()
        self.assertIsNone(win._control_panel._active_block_id,
                          "scene switch must clear the scope")
        self.click_emotion("Enthusiasm")
        self.assertEqual(win._emotion, "Enthusiasm")


# ===========================================================================
# Scene save/restore round-trip (persistence of mode + selection)
# ===========================================================================

class TestSceneStatePersistence(unittest.TestCase, _IsolatedHarness):
    """Part 4 contract persists across Save -> reload (Project round-trip)."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_editor_mode_and_selection_survive_roundtrip(self):
        from engine.models import Scene
        win = self.win
        a = self.make_scene("PersistA", "Persist me.\n\nSecond paragraph.")
        self.enter_blocks_mode()
        blocks = win._editor.block_manager.blocks
        self.assertGreaterEqual(len(blocks), 1)
        win._editor._on_block_click_requested(blocks[-1].start_offset)
        win._save_current_scene_state()

        # Round-trip through the serializer
        data = a.to_dict()
        self.assertEqual(data["editor_mode"], "blocks")
        self.assertEqual(data["selected_block_id"], blocks[-1].id)
        restored = Scene.from_dict(data)
        self.assertEqual(restored.editor_mode, "blocks")
        self.assertEqual(restored.selected_block_id, blocks[-1].id)

        b_data = {**data, "editor_mode": "plain", "selected_block_id": None}
        b_restored = Scene.from_dict(b_data)
        self.assertEqual(b_restored.editor_mode, "plain")
        self.assertIsNone(b_restored.selected_block_id)

    def test_unknown_mode_value_restores_plain(self):
        from engine.models import Scene
        data = {"name": "X", "text": "t"}
        data["editor_mode"] = "bogus"
        self.assertEqual(Scene.from_dict(data).editor_mode, "plain")
        data["editor_mode"] = "blocks"
        self.assertEqual(Scene.from_dict(data).editor_mode, "blocks")


# ===========================================================================
# Part 18: no global editor contamination after a switch (matrix)
# ===========================================================================

class TestNoGlobalEditorContamination(unittest.TestCase, _IsolatedHarness):
    """After switching Scenes NOTHING from the previous Scene may survive
    in editor text / block widgets / selection / semantic state."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_full_contamination_matrix(self):
        win = self.win
        self.reset_semantics()
        chars = self.make_characters("Engineer", "Lady")
        a = self.make_scene("ContA", "Scene A long text.\n\nMore A text.")
        self.enter_plain_mode()
        self.click_emotion("Enthusiasm")
        win._save_current_scene_state()

        b = self.make_scene("ContB", "Scene B text.\n\nB second.")
        self.enter_blocks_mode()
        blocks = win._editor.block_manager.blocks
        blocks[0].character_id = chars["Engineer"].id
        blocks[0].emotion = "Awe"
        win._editor._on_block_click_requested(blocks[0].start_offset)
        win._save_current_scene_state()

        win._switch_to_scene(a.id)
        process()
        ed = win._editor
        # editor text = A's text exactly
        self.assertEqual(ed.get_text(), "Scene A long text.\n\nMore A text.")
        # no stale block widgets / selection / scope
        self.assertEqual(ed._editor._blocks, [])
        self.assertIsNone(ed._selected_block_id)
        self.assertIsNone(win._control_panel._active_block_id)
        # semantic state = A's
        self.assertEqual(win._emotion, "Enthusiasm")
        # preview reflects A's state only
        from engine.higgs_tokens import EMOTION_BY_NAME
        pv = win._build_prompt()
        self.assertIn("<|emotion:%s|>" % EMOTION_BY_NAME["Enthusiasm"].tag,
                      pv)
        self.assertNotIn("<|emotion:awe|>", pv,
                         "Scene B's block override leaked into A's preview")


if __name__ == "__main__":
    unittest.main(verbosity=2)
