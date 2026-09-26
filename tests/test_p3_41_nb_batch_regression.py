"""
GUINEO (SpeechStudio) — P3.41 runtime tests
===========================================

Narration Blocks adjacent-block visual integrity + Long Generation
routing regression.

ISSUE 1 — Narration Blocks collapse when the blank line between blocks
    is removed:
    Root cause (runtime-proven): the block gutter painted each block's
    header directly at the RAW document y range (QTextCursor/cursorRect
    of the block offsets) with no minimum row height and no stacking
    constraint. Removing the separator blank line(s) makes two blocks'
    offsets legitimately map to the SAME document line (the offsets
    themselves stay correct — the block manager delta-tracks them), so
    both headers were painted at the same y and collapsed into each
    other ("B1 · ANNA" over "B2 · MÁRK").
    Fix: the gutter now paints ONE ROW per block — anchored at the
    document position, at least MIN_ROW_HEIGHT tall, monotonically
    stacked (never overlapping). Painting and click hit-testing share
    the same _block_rows() layout.

ISSUE 2 — Long Generation opening an obsolete / differently configured
    Batch window:
    Root cause (runtime-proven): the P3.35 single-live-instance policy
    compared ONLY the mode (scene vs manual). A scene dialog left open
    from Scene A was focused and REUSED when the user switched to Scene
    B and ran Long Generation — the window kept Scene A's title /
    coverage / combined outputs / completion callback while running
    Scene B's jobs.
    Fix: the focus match now includes the SCENE IDENTITY — a dialog
    bound to a different Scene is cleanly superseded (running batch
    preserved) and rebuilt for the Scene being generated.

Also guards the P3.39 token integrity contract through the Long
Generation path (fresh paste → no implicit tokens; explicit override →
token present and consistent per part).

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \\
        tests/test_p3_41_nb_batch_regression.py -v
"""
import os
import sys
import time
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch, PropertyMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Fake torch (house pattern: test_p3_35 / test_p3_27b / test_p3_28).
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
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QPushButton, QCheckBox, QComboBox, QMessageBox,
)
from PySide6.QtCore import Qt, QTimer, QPoint  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from engine.models import Project, Scene, Character  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)

# Real application theme (house pattern since P3.34).
from ui.theme import apply_theme, DEFAULT_THEME  # noqa: E402
apply_theme(APP, DEFAULT_THEME)


# ---------------------------------------------------------------------------
# Model-boundary fake (house pattern from P3.35)
# ---------------------------------------------------------------------------
def make_speech(seconds=1.0, sr=24000):
    return (np.random.uniform(-0.5, 0.5, int(sr * seconds))
            .astype("float32"))


class FakeHiggsModel:
    calls = []

    def generate_speech(self, text, tokenizer, *, reference_audio=None,
                        reference_sample_rate=None, reference_codes=None,
                        reference_text=None, max_new_tokens=2048,
                        temperature=1.0, top_p=None, top_k=None):
        FakeHiggsModel.calls.append(dict(text=text))
        return make_speech(0.2)


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


class _Harness(unittest.TestCase):
    """Real Engine + real MainWindow + real NarrationEditor on a tmp root."""

    def setUp(self):
        from engine.engine import Engine
        from ui.main_window import MainWindow
        self.tmp = tempfile.mkdtemp(prefix="ss_p341_")
        self.engine = Engine(app_root=self.tmp)
        # Real voice profiles so the Long Narration speaker mapping can
        # be assigned in the modal dialog (routing tests).
        self.engine.create_voice("Anna Voice")
        self.engine.create_voice("Mark Voice")
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
        self.project = Project(name="Eden", id="proj1")
        self.scene = Scene(id="sc1abcd", name="Scene 03",
                           project_id="proj1")
        self.scene2 = Scene(id="sc2efgh", name="Scene 07",
                            project_id="proj1")
        self.project.add_scene(self.scene)
        self.project.add_scene(self.scene2)
        self.anna = Character(id="char-anna", name="ANNA")
        self.mark = Character(id="char-mark", name="MARK")
        self.project.characters.extend([self.anna, self.mark])
        self.win._active_project = self.project
        self.win._active_scene = self.scene
        self.win._current_project = "Eden"
        self.win._update_editor_characters()
        FakeHiggsModel.calls = []
        self.ed = self.win._editor

    def tearDown(self):
        dlg = getattr(self.win, "_batch_dialog", None)
        if dlg is not None:
            try:
                dlg.close()
            except Exception:
                pass
        try:
            self.win.close()
        except Exception:
            pass
        for p in self.patchers:
            p.stop()
        _process(50)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- editor helpers ----------------------------------------------------
    def setup_blocks(self, text="Sentence A.\n\nSentence B."):
        """Paste text in Plain Text, switch to Narration Blocks (the
        auto-detect runs), mirror the editor, return the blocks."""
        self.ed.clear_text()
        self.ed._plain_btn.setChecked(True)
        self.ed._editor.setPlainText(text)
        self.ed._blocks_btn.setChecked(True)
        self.ed._editor.setFocus()
        return self.ed._block_manager.blocks

    def rows(self):
        return self.ed._editor._block_gutter_widget._block_rows()

    def assert_rows_disjoint(self, rows=None):
        """P3.41 core guarantee: painted gutter rows never overlap and
        every row can hold its own header line."""
        rows = [r for r in (rows if rows is not None else self.rows())
                if r is not None]
        self.assertTrue(rows, "no rows painted")
        for r in rows:
            self.assertGreaterEqual(r[2] - r[1], 18)
        for a, b in zip(rows, rows[1:]):
            self.assertGreaterEqual(b[1], a[2] + 1,
                                     "gutter rows overlap: %r vs %r" % (a, b))

    # -- modal Generate Long driver (house pattern from the P3.41 audit) --
    def drive_generate_long(self, review=True):
        """Drive the REAL entry path: editor 'Generate Long' button ->
        _on_generate_long_narration -> modal LongNarrationDialog (exec)
        -> assign speakers + review checkbox -> click Generate ->
        _start_long_narration -> _present_batch_dialog."""
        def click_generate():
            from ui.panels.long_narration_dialog import LongNarrationDialog
            candidates = [w for w in QApplication.topLevelWidgets()
                          if isinstance(w, LongNarrationDialog)
                          and w.isVisible()]
            for w in candidates:
                for combo in w.findChildren(QComboBox):
                    if combo.count() > 1 and combo.currentIndex() == 0:
                        combo.setCurrentIndex(1)
                if review:
                    for cb in w.findChildren(QCheckBox):
                        if cb.text().startswith("Review"):
                            cb.setChecked(True)
                for b in w.findChildren(QPushButton):
                    if b.text() == "Generate":
                        QTest.mouseClick(b, Qt.MouseButton.LeftButton)
                        return

        def close_strays():
            for w in QApplication.topLevelWidgets():
                if isinstance(w, QMessageBox):
                    w.reject()

        QTimer.singleShot(200, click_generate)
        QTimer.singleShot(2500, close_strays)
        QTimer.singleShot(4000, close_strays)
        QTest.mouseClick(self.ed._generate_long_btn,
                         Qt.MouseButton.LeftButton)
        for _ in range(60):
            _process(30)
            if getattr(self.win, "_batch_dialog", None) is not None:
                break
        _process(80)
        return getattr(self.win, "_batch_dialog", None)

    def batch_info(self):
        dlg = getattr(self.win, "_batch_dialog", None)
        if dlg is None:
            return None
        jobs = dlg._manager.jobs
        return dict(
            scene_mode=dlg.is_scene_mode,
            title=dlg.windowTitle(),
            cols=dlg._table.columnCount(),
            dialog_scene_id=(dlg.scene.id if dlg.scene is not None else None),
            job_scene_ids=sorted({j.scene_id for j in jobs}),
            coverage=getattr(dlg, "_coverage_label", None) is not None,
            combine=hasattr(dlg, "_combine_btn"),
            export=hasattr(dlg, "_export_btn"),
            jobs=jobs,
        )


# ===========================================================================
# ISSUE 1 — Narration Blocks adjacent-block visual integrity
# ===========================================================================
class TestAdjacentBlockGeometry(_Harness):

    def test_two_blocks_detected_with_characters(self):
        """Setup sanity: B1/B2 detected, rows anchored at the document."""
        blocks = self.setup_blocks()
        self.assertEqual(len(blocks), 2)
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        rows = [r for r in self.rows() if r is not None]
        self.assertEqual(len(rows), 2)
        self.assert_rows_disjoint(rows)

    def test_remove_blank_line_no_visual_overlap(self):
        """THE reported defect: remove the blank line(s) between two
        assigned blocks — the painted rows must never collapse into
        each other (labels stay fully readable, stacked in order)."""
        blocks = self.setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        editor = self.ed._editor
        # Select BOTH newlines between the sentences and delete (one
        # user action — the sentences land on one text line).
        c = QTextCursor(editor.document())
        c.setPosition(11)
        c.setPosition(13, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(c)
        QTest.keyClick(editor, Qt.Key.Key_Delete)
        _process(50)
        # Authoritative state: offsets still valid, characters intact.
        self.assertEqual(editor.toPlainText(), "Sentence A.Sentence B.")
        rows = [r for r in self.rows() if r is not None]
        self.assertEqual(len(rows), 2,
                         "both blocks must still be painted")
        self.assert_rows_disjoint(rows)
        # The two rows must occupy DIFFERENT vertical bands (the labels
        # are stacked, not coincident).
        self.assertGreaterEqual(rows[1][1], rows[0][2] + 1)

    def test_one_newline_removed_rows_disjoint(self):
        """Adjacent lines variant: remove ONE newline — rows disjoint."""
        blocks = self.setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        editor = self.ed._editor
        c = QTextCursor(editor.document())
        c.setPosition(12)
        editor.setTextCursor(c)
        QTest.keyClick(editor, Qt.Key.Key_Backspace)
        _process(50)
        self.assertEqual(editor.toPlainText(), "Sentence A.\nSentence B.")
        rows = [r for r in self.rows() if r is not None]
        self.assertEqual(len(rows), 2)
        self.assert_rows_disjoint(rows)

    def test_character_assignments_preserved_after_removal(self):
        """§1C: removing the blank line must NOT reassign characters."""
        blocks = self.setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        editor = self.ed._editor
        c = QTextCursor(editor.document())
        c.setPosition(11)
        c.setPosition(13, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(c)
        QTest.keyClick(editor, Qt.Key.Key_Delete)
        _process(30)
        self.assertEqual(self.ed._block_manager.blocks[0].character_id,
                         "char-anna")
        self.assertEqual(self.ed._block_manager.blocks[1].character_id,
                         "char-mark")

    def test_block_ids_preserved_through_edit(self):
        """§1A: the block identities survive the text edit (no rebuild,
        no re-detect — the same PromptBlock objects remain)."""
        blocks = self.setup_blocks()
        ids = [b.id for b in blocks]
        editor = self.ed._editor
        c = QTextCursor(editor.document())
        c.setPosition(11)
        c.setPosition(13, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(c)
        QTest.keyClick(editor, Qt.Key.Key_Delete)
        _process(30)
        self.assertEqual([b.id for b in self.ed._block_manager.blocks], ids)

    def test_reinsert_and_redetect_restores_layout(self):
        """§1C/§1E: blank line re-inserted + Re-detect restores separate
        rows AND the character assignments (no side-effect reassign)."""
        blocks = self.setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        editor = self.ed._editor
        c = QTextCursor(editor.document())
        c.setPosition(11)
        c.setPosition(13, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(c)
        QTest.keyClick(editor, Qt.Key.Key_Delete)
        _process(30)
        # re-insert the blank line with ENTER (AFTER the period —
        # offset 11 — restoring the original "Sentence A.\n\nSentence B.")
        c = QTextCursor(editor.document())
        c.setPosition(11)
        editor.setTextCursor(c)
        QTest.keyClick(editor, Qt.Key.Key_Return)
        QTest.keyClick(editor, Qt.Key.Key_Return)
        _process(30)
        # Re-detect with the manager's override-preserving path (the
        # 'Save' option of the Re-detect dialog).
        self.ed._block_manager.preserve_overrides(editor.toPlainText())
        self.ed._post_analyze()
        _process(30)
        mgr = self.ed._block_manager
        self.assertEqual(len(mgr.blocks), 2)
        self.assert_rows_disjoint()
        found = {}
        for b in mgr.blocks:
            text = editor.toPlainText()[b.start_offset:b.end_offset]
            if "Sentence A" in text:
                found["A"] = b.character_id
            if "Sentence B" in text:
                found["B"] = b.character_id
        self.assertEqual(found.get("A"), "char-anna")
        self.assertEqual(found.get("B"), "char-mark")

    def test_multiple_blank_lines_added_and_removed(self):
        """§1E: add several blank lines, type between blocks, remove
        them again — rows always disjoint, assignments intact."""
        blocks = self.setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        editor = self.ed._editor
        c = QTextCursor(editor.document())
        c.setPosition(11)
        editor.setTextCursor(c)
        for _ in range(3):
            QTest.keyClick(editor, Qt.Key.Key_Return)
        _process(30)
        self.assert_rows_disjoint()
        QTest.keyClicks(editor, "mid")
        _process(30)
        self.assert_rows_disjoint()
        # Remove the typed text and the blank lines again.
        c = QTextCursor(editor.document())
        c.setPosition(11)
        c.setPosition(17, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(c)
        QTest.keyClick(editor, Qt.Key.Key_Delete)
        _process(30)
        self.assert_rows_disjoint()
        self.assertEqual(self.ed._block_manager.blocks[0].character_id,
                         "char-anna")
        self.assertEqual(self.ed._block_manager.blocks[1].character_id,
                         "char-mark")

    def test_edit_text_before_b1(self):
        """§1E: typing BEFORE the first block keeps geometry coherent."""
        blocks = self.setup_blocks()
        blocks[0].character_id = "char-anna"
        editor = self.ed._editor
        c = QTextCursor(editor.document())
        c.setPosition(0)
        editor.setTextCursor(c)
        QTest.keyClicks(editor, "XX ")
        _process(30)
        self.assertEqual(self.ed._block_manager.blocks[0].start_offset, 3)
        self.assert_rows_disjoint()

    def test_delete_text_around_block_boundaries(self):
        """§1E: deleting at the block boundary (end of B1) stacks the
        rows without corruption."""
        blocks = self.setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        editor = self.ed._editor
        # Delete the period at the end of B1 (offset 10) — B1 shrinks,
        # B2 shifts left, both stay on their own lines.
        c = QTextCursor(editor.document())
        c.setPosition(11)
        editor.setTextCursor(c)
        QTest.keyClick(editor, Qt.Key.Key_Backspace)
        _process(30)
        self.assert_rows_disjoint()
        # Now remove the separator newlines entirely.
        c = QTextCursor(editor.document())
        c.setPosition(10)
        c.setPosition(12, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(c)
        QTest.keyClick(editor, Qt.Key.Key_Delete)
        _process(30)
        self.assert_rows_disjoint()

    def test_scroll_while_blocks_adjacent(self):
        """§1E: scrolling with adjacent blocks keeps the row layout
        coherent (rows are viewport-relative and culled correctly)."""
        # Long text: 40 lines before the two-block tail (the filler
        # prefix itself becomes the first block; we work with the LAST
        # two blocks — Sentence A and Sentence B).
        prefix = "\n".join("Filler line {0}.".format(i)
                           for i in range(1, 41)) + "\n\n"
        blocks = self.setup_blocks(prefix + "Sentence A.\n\nSentence B.")
        self.assertGreaterEqual(len(blocks), 2)
        last = blocks[-1]      # "Sentence B."
        second_last = blocks[-2]  # "Sentence A."
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        editor = self.ed._editor
        # Merge the two sentences onto one line.
        text = editor.toPlainText()
        idx = text.index("Sentence A.\n\nSentence B.")
        c = QTextCursor(editor.document())
        c.setPosition(idx + 11)
        c.setPosition(idx + 13, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(c)
        QTest.keyClick(editor, Qt.Key.Key_Delete)
        _process(30)
        for value in (0, 100, 300, 600):
            editor.verticalScrollBar().setValue(value)
            _process(30)
            self.assert_rows_disjoint()

    def test_gutter_click_selects_stacked_block(self):
        """Hit-testing must use the SAME row layout as painting: with
        two stacked rows, clicking the SECOND row selects B2 (not B1 —
        both map to the same raw document y range)."""
        blocks = self.setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        editor = self.ed._editor
        c = QTextCursor(editor.document())
        c.setPosition(11)
        c.setPosition(13, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(c)
        QTest.keyClick(editor, Qt.Key.Key_Delete)
        _process(30)
        rows = [r for r in self.rows() if r is not None]
        self.assertEqual(len(rows), 2)
        self.assertGreaterEqual(rows[1][1], rows[0][2] + 1)
        gutter = editor._block_gutter_widget
        # Real click into the middle of B2's stacked row.
        click_y = (rows[1][1] + rows[1][2]) // 2
        QTest.mouseClick(gutter, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(20, click_y))
        _process(50)
        self.assertEqual(self.ed._selected_block_id, blocks[1].id)
        # And the first row selects B1.
        click_y = (rows[0][1] + rows[0][2]) // 2
        QTest.mouseClick(gutter, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(20, click_y))
        _process(50)
        self.assertEqual(self.ed._selected_block_id, blocks[0].id)

    def test_normal_blocks_row_anchored_at_document(self):
        """No arbitrary spacing: when blocks do NOT collide, each row's
        top equals the block's real document y position."""
        self.setup_blocks()
        editor = self.ed._editor
        text = editor.toPlainText()
        for r in self.rows():
            if r is None:
                continue
            block, row_top, _ = r
            cur = QTextCursor(editor.document())
            cur.setPosition(max(0, min(block.start_offset, len(text))))
            y_top = editor.cursorRect(cur).top()
            self.assertEqual(row_top, y_top)

    def test_gutter_renders_both_rows_after_collapse(self):
        """Paint smoke: the collapsed gutter renders without error and
        produces two visually distinct label rows (pixel rows with
        text-like content in the gutter band)."""
        blocks = self.setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        editor = self.ed._editor
        c = QTextCursor(editor.document())
        c.setPosition(11)
        c.setPosition(13, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(c)
        QTest.keyClick(editor, Qt.Key.Key_Delete)
        _process(30)
        self.win.resize(1000, 700)
        self.win.show()
        _process(80)
        gutter = editor._block_gutter_widget
        img = gutter.grab().toImage()
        self.assertFalse(img.isNull())
        # Two label bands must exist at DIFFERENT y positions (with the
        # pre-P3.41 code both labels painted at the SAME y and garbled
        # each other — a single glyph band).
        def _bright(y):
            return sum(1 for x in range(img.width())
                       if img.pixelColor(x, y).lightness() > 120)
        text_rows = [y for y in range(img.height()) if _bright(y) > 20]
        bands = []
        for y in text_rows:
            if bands and y - bands[-1][-1] <= 1:
                bands[-1].append(y)
            else:
                bands.append([y])
        self.assertGreaterEqual(
            len(bands), 2,
            "expected two separated label bands, got: %r" % (bands,))
        # The two bands must not touch (each label fully inside its own
        # row) — the gap is at least the row margin.
        for a, b in zip(bands, bands[1:]):
            self.assertGreaterEqual(b[0], a[-1] + 2)
        self.win.hide()


# ===========================================================================
# ISSUE 2 — Long Generation routing (one authoritative Scene workflow)
# ===========================================================================
class TestLongGenerationRouting(_Harness):

    def _assert_scene_dialog(self, info, scene):
        self.assertIsNotNone(info, "no batch dialog was opened")
        self.assertTrue(info["scene_mode"])
        self.assertEqual(info["title"],
                         "Batch Generation \u2014 {0}".format(scene.name))
        self.assertEqual(info["cols"], 7)
        self.assertEqual(info["dialog_scene_id"], scene.id)
        self.assertEqual(info["job_scene_ids"], [scene.id])
        self.assertTrue(info["coverage"])
        self.assertTrue(info["combine"], "Combine Scene must be present")
        self.assertTrue(info["export"], "Export Audio must be present")

    def test_plain_text_generate_long_scene_mode(self):
        """§2G-A: New Scene -> Plain Text -> Generate Long -> Scene mode."""
        self.ed.clear_text()
        self.ed._editor.setPlainText("Sentence A.\n\nSentence B.")
        self.drive_generate_long()
        self._assert_scene_dialog(self.batch_info(), self.scene)

    def test_nb_generate_long_scene_mode(self):
        """§2G-B: Narration Blocks (no overrides) -> Scene mode."""
        self.setup_blocks()
        self.drive_generate_long()
        self._assert_scene_dialog(self.batch_info(), self.scene)

    def test_nb_character_generate_long_scene_mode(self):
        """§2G-C: Character assignment must NOT change the mode."""
        blocks = self.setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[1].character_id = "char-mark"
        self.ed._update_properties_panel()
        self.drive_generate_long()
        self._assert_scene_dialog(self.batch_info(), self.scene)

    def test_nb_emotion_override_scene_mode(self):
        """§2G-D: Emotion override must NOT change the mode."""
        blocks = self.setup_blocks()
        blocks[0].emotion = "Elation"
        self.drive_generate_long()
        self._assert_scene_dialog(self.batch_info(), self.scene)

    def test_nb_style_override_scene_mode(self):
        """§2L: Style override must NOT change the mode."""
        blocks = self.setup_blocks()
        blocks[1].style = "Whispering"
        self.drive_generate_long()
        self._assert_scene_dialog(self.batch_info(), self.scene)

    def test_nb_multiple_overrides_scene_mode(self):
        """§2K critical acceptance: B1 -> ANNA + Emotion, B2 -> MARK +
        Style -> ONE Scene-aware window with the full Scene feature set."""
        blocks = self.setup_blocks()
        blocks[0].character_id = "char-anna"
        blocks[0].emotion = "Elation"
        blocks[1].character_id = "char-mark"
        blocks[1].style = "Whispering"
        self.ed._update_properties_panel()
        self.ed._render_block_visuals()
        self.drive_generate_long(review=True)
        info = self.batch_info()
        self._assert_scene_dialog(info, self.scene)
        dlg = self.win._batch_dialog
        self.assertIn(self.scene.name, dlg._coverage_label.text())
        self.assertIn("coverage", dlg._coverage_label.text())
        self.assertTrue(dlg._review_mode,
                        "Review Before Generate must reach the dialog")

    def test_switch_scene_rebuilds_dialog_for_new_scene(self):
        """THE issue-2 repro: Scene A dialog left open -> switch to
        Scene B -> Generate Long -> the window REBINDS to Scene B (the
        old dialog is superseded, never reused with obsolete context)."""
        self.setup_blocks()
        self.drive_generate_long()
        first = self.win._batch_dialog
        self._assert_scene_dialog(self.batch_info(), self.scene)
        # Switch to Scene B through the real scene-state path.
        self.win._active_scene = self.scene2
        self.win._current_scene = self.scene2.name
        self.win._load_scene_state(self.scene2)
        self.ed._editor.setPlainText("Scene B content.\n\nSecond part.")
        self.ed._blocks_btn.setChecked(True)
        self.drive_generate_long()
        second = self.win._batch_dialog
        self.assertIsNot(second, first,
                         "the Scene A dialog must not be reused for Scene B")
        self._assert_scene_dialog(self.batch_info(), self.scene2)
        # The completion callback follows the NEW scene (P3.28 §5) —
        # the scene id rides as the lambda's default argument.
        bm = self.win._batch_manager
        cb = bm._on_batch_completed
        self.assertIsNotNone(cb)
        bound = cb.__defaults__[0] if cb.__defaults__ else None
        self.assertEqual(bound, self.scene2.id)

    def test_same_scene_reopen_focuses_single_instance(self):
        """P3.35 §3 preserved: same mode + same scene -> the existing
        dialog is focused, never duplicated."""
        self.setup_blocks()
        self.drive_generate_long()
        first = self.win._batch_dialog
        first.show()
        _process(80)
        self.setup_blocks("Sentence A.\n\nSentence B.")
        self.drive_generate_long()
        second = self.win._batch_dialog
        self.assertIs(first, second)
        from ui.panels.batch_generation import BatchGenerationDialog
        live = [w for w in self.win.findChildren(BatchGenerationDialog)]
        self.assertEqual(len(live), 1)

    def test_manual_open_then_long_generation_replaces(self):
        """Manual queue open -> Long Generation -> replaced by the Scene
        dialog (mode hand-over, single instance)."""
        self.win._on_open_batch_generation()
        manual = self.win._batch_dialog
        self.assertFalse(manual.is_scene_mode)
        self.assertEqual(manual.windowTitle(), "Batch Queue")
        self.setup_blocks()
        self.drive_generate_long()
        dlg = self.win._batch_dialog
        self.assertIsNot(dlg, manual)
        self._assert_scene_dialog(self.batch_info(), self.scene)

    def test_manual_menu_stays_intentionally_manual(self):
        """§2I: Tools -> Batch Generation remains the Manual Batch Queue."""
        self.win._on_open_batch_generation()
        info = self.batch_info()
        self.assertFalse(info["scene_mode"])
        self.assertEqual(info["title"], "Batch Queue")
        self.assertEqual(info["cols"], 5)
        self.assertFalse(info["coverage"])

    def test_running_batch_survives_scene_handover(self):
        """P3.35 §11 preserved: superseding a dialog (scene hand-over)
        must NOT stop a running batch."""
        self.setup_blocks()
        self.drive_generate_long()
        bm = self.win._batch_manager
        bm._running = True
        bm._stop_requested = False
        self.win._active_scene = self.scene2
        self.win._current_scene = self.scene2.name
        self.win._load_scene_state(self.scene2)
        self.ed._editor.setPlainText("Scene B content.\n\nSecond part.")
        self.ed._blocks_btn.setChecked(True)
        self.drive_generate_long()
        self.assertTrue(bm.is_running)
        self.assertFalse(bm.stop_requested)

    def test_all_long_entries_share_one_handler(self):
        """§2B: every Long Generation entry (editor button signal, menu
        action, top navigation Edit menu) routes to the ONE handler —
        proven by driving them with a fake preview dialog (the handler
        imports LongNarrationDialog at call time, so the patch applies
        to every entry)."""
        self.ed.clear_text()
        self.ed._editor.setPlainText("Some text to generate.")
        from ui.panels import long_narration_dialog as lnd_module
        constructed = []

        class FakeDialog:
            def __init__(self, *a, **kw):
                constructed.append(a)

            def exec(self):
                return 0  # Rejected — no generation started

        action = self.win._menu_bar.get_action("generate_long")
        self.assertIsNotNone(action)
        with patch.object(lnd_module, "LongNarrationDialog", FakeDialog):
            # 1. Menu action (Ctrl+Shift+Return)
            action.trigger()
            _process(30)
            # 2. Editor 'Generate Long' button signal
            self.ed.generate_long_requested.emit()
            _process(30)
        self.assertEqual(len(constructed), 2,
                         "both entries must reach "
                         "_on_generate_long_narration")
        # 3. Top navigation reuses the menu's action object.
        from PySide6.QtWidgets import QMenu
        from ui.panels.top_navigation import TopNavigation
        found = False
        for nav in self.win.findChildren(TopNavigation):
            for menu in nav.findChildren(QMenu):
                if any(act is action for act in menu.actions()):
                    found = True
        self.assertTrue(found,
                        "top navigation must reuse the menu action")


# ===========================================================================
# P3.39 token-integrity guard through the Long Generation pipeline
# ===========================================================================
class TestTokenRegressionLongGeneration(_Harness):

    def _start_with_parts(self):
        """Replicate _on_generate_long_narration's splitter call exactly,
        then run the REAL _start_long_narration (review mode — jobs stay
        pending; prompts are inspectable on the jobs)."""
        from engine.narration_splitter import NarrationSplitter
        params = self.win._control_panel.get_parameters()
        parts = NarrationSplitter().split(
            text=self.ed.get_text(),
            blocks=self.ed.block_manager.blocks or None,
            global_emotion=self.win._emotion,
            global_style=self.win._style,
            global_speed=self.win._speed,
            global_pitch=self.win._pitch,
            global_delivery=self.win._delivery,
            base_parameters=params,
            raw_mode=self.ed.is_raw_mode(),
            detect_speakers=True,
            allow_sfx=params.allow_sfx,
        )
        self.win._start_long_narration(parts, None, params, {},
                                       review_mode=True)
        _process(80)
        return self.win._batch_manager.jobs

    def test_fresh_pasted_text_no_implicit_tokens(self):
        """P3.39 guarantee through Generate Long: a fresh Scene + pasted
        text receives NO tokens unless the user sets one explicitly."""
        self.ed.clear_text()
        self.ed._editor.setPlainText("First sentence here.\n\nSecond "
                                     "sentence follows.")
        self.ed._blocks_btn.setChecked(True)
        jobs = self._start_with_parts()
        self.assertTrue(jobs)
        for job in jobs:
            self.assertNotIn("<|emotion:", job.prompt)
            self.assertNotIn("<|style:", job.prompt)
            self.assertNotIn("<|prosody:", job.prompt)

    def test_explicit_block_override_token_reaches_batch(self):
        """An explicit block override appears in exactly that block's
        part prompt (and only there)."""
        blocks = self.setup_blocks("First sentence here.\n\nSecond "
                                   "sentence follows.")
        blocks[0].emotion = "Elation"
        jobs = self._start_with_parts()
        self.assertTrue(jobs)
        with_emotion = [j for j in jobs if "<|emotion:elation|>" in j.prompt]
        without = [j for j in jobs if "<|emotion:" not in j.prompt]
        self.assertTrue(with_emotion, "override token missing")
        self.assertTrue(without, "token leaked into other parts")

    def test_character_and_overrides_consistent_per_part(self):
        """Block context (Character + Style) is carried per part with
        the correct effective token state."""
        blocks = self.setup_blocks("First sentence here.\n\nSecond "
                                   "sentence follows.")
        blocks[0].character_id = "char-anna"
        blocks[0].emotion = "Elation"
        blocks[1].character_id = "char-mark"
        blocks[1].style = "Whispering"
        jobs = self._start_with_parts()
        self.assertTrue(jobs)
        # Part provenance: each job traces back to its source block.
        by_block = {j.source_block_id: j for j in jobs}
        self.assertIn(blocks[0].id, by_block)
        self.assertIn(blocks[1].id, by_block)
        self.assertIn("<|emotion:elation|>", by_block[blocks[0].id].prompt)
        self.assertNotIn("<|emotion:", by_block[blocks[1].id].prompt)
        self.assertIn("<|style:whispering|>", by_block[blocks[1].id].prompt)
        self.assertNotIn("<|style:", by_block[blocks[0].id].prompt)
        self.assertEqual(by_block[blocks[0].id].character_id, "char-anna")
        self.assertEqual(by_block[blocks[1].id].character_id, "char-mark")


if __name__ == "__main__":
    unittest.main(verbosity=2)
