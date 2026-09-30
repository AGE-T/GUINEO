"""GUINEO P3.45.4 — RENDERING / UX HARDENING regression tests.

Spec: harden the rendering/UX implementation against the concrete,
runtime-PROVEN defects of the P3.45 triage (D-R1 app_root duplication,
D-R2 _check_states index keying, D-R3 redundant toPlainText in paint
paths, D-R4 no-op click full viewport repaint, D-R5 flash-timer viewport
repaints, D-R6 hardcoded block stripes) — while the frozen contracts
(P3.44.9/9.1 geometry, P3.45.1 badges, P3.45.3 identity chips) stay
byte-identical.

Coverage matrix (task TEST STRATEGY):

  App root       1-2   single definition + unchanged behaviour
  Check states   3-12  correct job, reorder, delete, duplicate, Load
                       Queue, project reload, failed job, edit, All/None
  Paint          13-15 redundant toPlainText removed; geometry identity;
                       rendered content unchanged
  Click repaint  16-18 no-op click paints nothing; real change repaints
  Flash          19-21 visible flash kept (gutter animates), timer stops,
                       viewport scope correct (0 paints)
  Theme          22-25 live tokens, theme switch, selection/override
                       contrast intact
  Batch identity 26-28 chips intact, reorder correct, duplicate
                       structurally independent
  Editor safety  29-32 P3.44.9.1 scroll/geometry, P3.45.1 status badge,
                       real mouse click selection

Real-widget integration: TestEditorPaintPaths / TestClickRepaint /
TestFlashBehaviour drive a REAL NarrationEditor (real auto-detect, real
paint events counted via event filters); the Batch tests drive REAL
BatchGenerationDialogs through the REAL handlers (move/remove/duplicate/
load-queue/edit) — never only pure object tests.

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_45_4_rendering_ux_hardening.py -v
"""
from __future__ import annotations

import ast
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

# ---------------------------------------------------------------------------
# Fake torch (house pattern: test_p3_28 / test_p3_44_5 / test_p3_45_3)
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

from PySide6.QtCore import QEvent, QObject, QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox  # noqa: E402

APP = QApplication.instance() or QApplication([])

# Real application theme (house pattern since P3.34).
from ui.theme import apply_theme, DEFAULT_THEME, Palette  # noqa: E402

apply_theme(APP, DEFAULT_THEME)

from engine.batch_manager import BatchJob, BatchManager, JobStatus  # noqa: E402
from engine.models import Project, Scene  # noqa: E402
from engine.audio_provenance import materialize_expected_slots  # noqa: E402
from engine.narration_splitter import SplitPart  # noqa: E402
from engine.settings_manager import SettingsManager  # noqa: E402

EDITOR_PY = os.path.join(_ROOT, "ui", "panels", "narration_editor.py")
ENGINE_PY = os.path.join(_ROOT, "engine", "engine.py")
BATCH_PY = os.path.join(_ROOT, "ui", "panels", "batch_generation.py")


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _make_part(text, block_id=None, speaker=None, part_of_block=1,
               total_in_block=1, block_label=""):
    return SplitPart(
        text=text, prompt=text, speaker=speaker or "",
        source_block_id=block_id, part_of_block=part_of_block,
        total_parts_in_block=total_in_block, block_label=block_label,
        character_id=None, estimated_duration=8.0,
        char_count=len(text))


def _scene_with(parts):
    project = Project(name="Eden", id="proj1")
    scene = Scene(id="sc1abcd", name="Scene 03", project_id="proj1")
    project.add_scene(scene)
    scene.expected_audio_slots = materialize_expected_slots(parts)
    scene.audio_assets = []
    scene.combined_outputs = []
    return project, scene


# ===========================================================================
# 1) App root — single authoritative definition (D-R1)
# ===========================================================================
class TestAppRoot(unittest.TestCase):

    def test_single_property_definition(self):
        """D-R1: exactly ONE app_root property in engine.py (the
        previously shadowed first definition was removed)."""
        tree = ast.parse(open(ENGINE_PY, encoding="utf-8").read())
        defs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "Engine":
                for item in node.body:
                    if (isinstance(item, (ast.FunctionDef, ))
                            and item.name == "app_root"):
                        defs.append(item)
                    if isinstance(item, ast.AsyncFunctionDef) \
                            and item.name == "app_root":
                        defs.append(item)
        self.assertEqual(
            len(defs), 1,
            "engine.app_root must have exactly one definition "
            "(P3.45.4 removed the shadowed duplicate)")

    def test_behavior_unchanged(self):
        """The authoritative property keeps the P3.43 behaviour."""
        from engine.engine import Engine
        with tempfile.TemporaryDirectory(prefix="ss_p3454_root_") as tmp:
            eng = Engine(app_root=tmp)
            self.assertEqual(eng.app_root, tmp)
        default_root = _ROOT
        eng2 = Engine()
        self.assertEqual(eng2.app_root, default_root)
        # read-only: no setter exists
        with self.assertRaises(AttributeError):
            eng2.app_root = "/elsewhere"


# ===========================================================================
# 2) _check_states — job-level identity + lifecycle (D-R2)
# ===========================================================================
class TestCheckStatesLifecycle(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p3454_chk_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _process(40)

    def _dialog(self, jobs=None, parts=None):
        from ui.panels.batch_generation import BatchGenerationDialog
        if parts is None:
            parts = [_make_part("A. {0}".format(i), "b%d" % i)
                     for i in range(3)]
        project, scene = _scene_with(parts)
        bm = BatchManager(submit_fn=lambda req: None)
        for j in (jobs if jobs is not None else
                  [BatchJob(name="Part {0}".format(i + 1),
                            prompt="Text {0}.".format(i + 1),
                            slot_id="b{0}:1".format(i)) for i in range(3)]):
            bm.add_job(j)
        sm = SettingsManager(os.path.join(self.tmp, "settings"))
        dlg = BatchGenerationDialog(bm, [], parent=None, scene=scene,
                                    project=project, app_root=self.tmp,
                                    settings_manager=sm)
        dlg.resize(1100, 680)
        dlg.layout().activate()
        dlg.show()
        _process(60)
        self.addCleanup(dlg.close)
        return dlg, bm

    def _visible(self, dlg):
        out = []
        for i, refs in enumerate(dlg._row_refs):
            cb = refs.get("check")
            out.append(None if cb is None else bool(cb.isChecked()))
        return out

    def _choose(self, dlg, flags):
        """User toggles: the authoritative state per current job."""
        for job, v in zip(dlg._manager.jobs, flags):
            dlg._check_states[dlg._check_key(job)] = bool(v)
        dlg._refresh_table()
        _process(30)

    def _click_checkbox(self, dlg, row):
        w = dlg._table.cellWidget(row, dlg._col_check)
        cb = getattr(w, "_checkbox", None)
        self.assertIsInstance(cb, QCheckBox)
        cb.click()
        _process(20)

    # -- 2: state belongs to the correct job -------------------------------
    def test_state_belongs_to_correct_job(self):
        dlg, bm = self._dialog()
        self.assertEqual(self._visible(dlg), [True, True, True])
        # user unchecks row 1 through the REAL widget
        self._click_checkbox(dlg, 1)
        self.assertEqual(self._visible(dlg), [True, False, True])
        self.assertEqual(dlg._checked_indices(), [0, 2])
        # a refresh keeps the visible states (P3.44 §7)
        dlg._refresh_table()
        _process(30)
        self.assertEqual(self._visible(dlg), [True, False, True])

    # -- 3: reorder does not move state to another row ----------------------
    def test_reorder_checkmark_follows_the_job(self):
        dlg, bm = self._dialog()
        self._choose(dlg, [True, False, False])
        # exactly _on_move_down's manager call + refresh
        bm.move_job(0, 1)
        dlg._refresh_table()
        _process(30)
        self.assertEqual(self._visible(dlg), [False, True, False])
        self.assertEqual(dlg._checked_indices(), [1])
        # the structural identity is untouched by the reorder (P3.45.3)
        self.assertEqual([j.slot_id for j in bm.jobs],
                         ["b1:1", "b0:1", "b2:1"])

    def test_reorder_up_checkmark_follows_the_job(self):
        dlg, bm = self._dialog()
        self._choose(dlg, [False, False, True])
        bm.move_job(2, 0)
        dlg._refresh_table()
        _process(30)
        self.assertEqual(self._visible(dlg), [True, False, False])
        self.assertEqual(dlg._checked_indices(), [0])

    # -- 4: duplicate does not inherit transient state ----------------------
    def test_duplicate_is_state_independent(self):
        dlg, bm = self._dialog()
        self._choose(dlg, [False, False, True])
        bm.duplicate_job(0)          # exactly _on_duplicate's calls
        dlg._refresh_table()
        _process(30)
        # clone (row 1) unchecked; the checked j2 (now row 3) keeps its
        # checkmark — nothing migrated.
        self.assertEqual(self._visible(dlg), [False, False, False, True])
        self.assertEqual(dlg._checked_indices(), [3])
        # P3.44.6: the duplicate is structurally independent
        self.assertIsNone(bm.jobs[1].slot_id)

    # -- 5: Load Queue clears/restores transient state ----------------------
    def test_load_queue_rederives_documented_defaults(self):
        dlg, bm = self._dialog()
        qpath = os.path.join(self.tmp, "q.yaml")
        bm.save_to_file(qpath)
        # user unchecked everything
        self._choose(dlg, [False, False, False])
        # load the same queue through the REAL handler
        from PySide6.QtWidgets import QFileDialog
        from unittest.mock import patch
        with patch.object(QFileDialog, "getOpenFileName",
                          staticmethod(lambda *a, **k: (qpath, ""))):
            with patch("ui.panels.batch_generation.FeedbackDialog."
                       "information"):
                dlg._on_load_queue()
        _process(60)
        # P3.28 §11 defaults for the loaded (not-generated) jobs
        self.assertEqual(self._visible(dlg), [True, True, True])
        self.assertEqual(dlg._checked_indices(), [0, 1, 2])

    def test_load_queue_leaves_no_stale_cross_queue_mappings(self):
        dlg, bm = self._dialog()
        other = BatchManager(submit_fn=lambda req: None)
        other.add_job(BatchJob(name="X", prompt="x."))
        other.add_job(BatchJob(name="Y", prompt="y."))
        opath = os.path.join(self.tmp, "q2.yaml")
        other.save_to_file(opath)
        # user checked only row 0 of the OLD queue
        self._choose(dlg, [True, False, False])
        from PySide6.QtWidgets import QFileDialog
        from unittest.mock import patch
        with patch.object(QFileDialog, "getOpenFileName",
                          staticmethod(lambda *a, **k: (opath, ""))):
            with patch("ui.panels.batch_generation.FeedbackDialog."
                       "information"):
                dlg._on_load_queue()
        _process(60)
        # the new queue's jobs get the documented defaults (checked),
        # never the old queue's row-0 toggle
        self.assertEqual(self._visible(dlg), [True, True])
        self.assertEqual(dlg._checked_indices(), [0, 1])
        # dict invariant: exactly the current jobs' keys, always in range
        self.assertEqual(set(dlg._check_states),
                         {dlg._check_key(j) for j in bm.jobs})

    # -- 6: project reload (fresh dialog) remains correct -------------------
    def test_fresh_dialog_derives_fresh_defaults(self):
        dlg, bm = self._dialog()
        self._choose(dlg, [False, True, False])
        dlg.close()
        _process(30)
        # a NEW dialog over the same manager/scene (the P3.35 single
        # live-instance reopen path) starts from the documented defaults
        from ui.panels.batch_generation import BatchGenerationDialog
        sm = SettingsManager(os.path.join(self.tmp, "settings"))
        dlg2 = BatchGenerationDialog(
            bm, [], parent=None, scene=dlg._scene, project=dlg._project,
            app_root=self.tmp, settings_manager=sm)
        dlg2.show()
        _process(60)
        self.addCleanup(dlg2.close)
        # transient check states are per-dialog: the new dialog re-derives
        # the documented P3.28 §11 defaults, never the previous dialog's
        # user toggles
        self.assertEqual(self._visible(dlg2), [True, True, True])

    # -- 7: failed job remains associated correctly -------------------------
    def test_failed_job_keeps_its_state(self):
        dlg, bm = self._dialog()
        self._choose(dlg, [False, True, False])
        bm.jobs[1].status = JobStatus.FAILED     # in-place failure
        bm.jobs[1].error = "boom"
        dlg._refresh_table()
        _process(30)
        self.assertEqual(self._visible(dlg), [False, True, False])
        self.assertEqual(dlg._checked_indices(), [1])

    # -- extra pins: edit / All / None / dict hygiene -----------------------
    def test_edit_keeps_manual_job_state(self):
        dlg, bm = self._dialog()
        # a manual job (no slot) at row 0
        bm._jobs.insert(0, BatchJob(name="Manual", prompt="m."))
        dlg._refresh_table()
        _process(30)
        self._choose(dlg, [True, False, False, False])
        from engine.batch_manager import BatchJob as _BJ
        from unittest.mock import patch
        original = bm.jobs[0]
        clone = _BJ.from_dict(original.to_dict())
        with patch.object(dlg, "_selected_row", lambda: 0):
            with patch("ui.panels.batch_generation.JobEditDialog") as JED:
                JED.DialogCode.Accepted = 1
                inst = JED.return_value
                inst.exec.return_value = 1
                dlg._on_edit()
        _process(40)
        # the replaced object carries the user's check state over
        self.assertEqual(self._visible(dlg), [True, False, False, False])
        self.assertEqual(dlg._checked_indices(), [0])
        self.assertEqual(bm.jobs[0].prompt, original.prompt)

    def test_all_and_none(self):
        dlg, bm = self._dialog()
        dlg._on_select_none()
        _process(30)
        self.assertEqual(self._visible(dlg), [False, False, False])
        self.assertEqual(dlg._checked_indices(), [])
        dlg._on_select_all()
        _process(30)
        self.assertEqual(self._visible(dlg), [True, True, True])
        self.assertEqual(dlg._checked_indices(), [0, 1, 2])

    def test_dict_describes_exactly_current_jobs(self):
        """After ANY structural mutation + refresh the dict holds ONLY
        keys of current jobs (prune removes unreachable leftovers; a
        brand-new job has no entry until toggled — default unchecked)."""
        dlg, bm = self._dialog()
        self._choose(dlg, [True, False, True])
        bm.remove_job(0)
        dlg._refresh_table()
        _process(30)
        bm.duplicate_job(0)
        dlg._refresh_table()
        _process(30)
        current_keys = {dlg._check_key(j) for j in bm.jobs}
        self.assertTrue(set(dlg._check_states) <= current_keys,
                        "no unreachable check-state keys may survive")
        self.assertTrue(all(0 <= i < len(bm.jobs)
                            for i in dlg._checked_indices()))
        self.assertEqual(bm.jobs[1].slot_id, None,
                         "the duplicate is a manual job (P3.44.6)")
        self.assertNotIn(dlg._check_key(bm.jobs[1]), dlg._check_states,
                         "a fresh duplicate carries no transient state")

    def test_manual_mode_has_no_check_states(self):
        from ui.panels.batch_generation import BatchGenerationDialog
        bm = BatchManager(submit_fn=lambda req: None)
        bm.add_job(BatchJob(name="Voice over", prompt="x."))
        sm = SettingsManager(os.path.join(self.tmp, "settings"))
        dlg = BatchGenerationDialog(bm, [], parent=None,
                                    app_root=self.tmp, settings_manager=sm)
        dlg.show()
        _process(50)
        self.addCleanup(dlg.close)
        # the manual Batch Queue has NO selective-generation column at all
        self.assertEqual(dlg._check_states, {})
        self.assertEqual(dlg._table.rowCount(), 1)
        headers = [dlg._table.horizontalHeaderItem(c).text()
                   for c in range(dlg._table.columnCount())]
        self.assertNotIn("", headers)      # no checkbox column header
        self.assertIn("File Name", headers)


# ===========================================================================
# 3) Editor paint paths — redundancy removed, rendering unchanged (D-R3)
# ===========================================================================
class TestEditorPaintPaths(unittest.TestCase):
    """Real NarrationEditor + real paint events (event-filter counters)."""

    def _make_editor(self):
        from ui.panels.narration_editor import NarrationEditor
        ed = NarrationEditor()
        ed.resize(900, 700)
        ed.show()
        ed.clear_text()
        text = ("Alpha block sentence one.\nAlpha block sentence two.\n\n"
                "Beta block sentence.\n\n"
                "Gamma block sentence one.\nGamma block sentence two.\n")
        ed._editor.setPlainText(text)
        ed._blocks_btn.setChecked(True)     # blocks mode + real auto-detect
        _process(120)
        ed._editor._flash_timer.stop()      # deterministic paints only
        ed._editor.clearFocus()
        _process(40)
        self.addCleanup(ed.close)
        self.addCleanup(ed.deleteLater)
        return ed

    def test_no_to_plain_text_in_paint_paths(self):
        """D-R3: the gutter paintEvent/_block_rows/mousePressEvent and
        _highlight_current_line no longer call toPlainText (the live
        characterCount identity replaced the full-document copies)."""
        src = open(EDITOR_PY, encoding="utf-8").read()
        tree = ast.parse(src)
        names = {"paintEvent", "_block_rows", "mousePressEvent",
                 "_highlight_current_line"}
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in names:
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Attribute)
                            and sub.func.attr == "toPlainText"):
                        offenders.append(node.name)
        self.assertEqual(
            offenders, [],
            "toPlainText() must not run in paint/hit-test/highlight "
            "paths (D-R3): found in {0}".format(offenders))

    def test_block_rows_geometry_identical_to_text_length_reference(self):
        """The O(1) document length produces the SAME row layout basis as
        the old len(toPlainText()) clamp (the exact identity, in the real
        widget, with real auto-detected blocks) and the row invariants
        hold (one full header line minimum, strictly ordered rows)."""
        ed = self._make_editor()
        editor = ed._editor
        self.assertEqual(len(editor.toPlainText()),
                         editor.document().characterCount() - 1)
        rows = editor._block_gutter_widget._block_rows()
        self.assertTrue(rows)
        self.assertTrue(all(r is not None for r in rows))
        min_row_h = editor._block_gutter_widget.MIN_ROW_HEIGHT
        prev_top = None
        for block, top, bottom in rows:
            self.assertGreaterEqual(bottom - top, min_row_h,
                                    "a row always fits one header line")
            if prev_top is not None:
                self.assertGreaterEqual(top, prev_top,
                                        "rows are ordered by document "
                                        "position (monotonic anchors)")
            prev_top = top

    def test_scrolling_repaints_gutter_and_viewport(self):
        """P3.44.9.1 wiring intact: a REAL scroll drives both paints."""
        from ui.panels.narration_editor import NarrationEditor
        # a LONG document so the scrollbar is genuinely scrollable
        text = "\n\n".join("Sentence {0} of the long document.".format(i)
                           for i in range(120))
        ed = NarrationEditor()
        ed.resize(900, 700)
        ed.show()
        ed.clear_text()
        ed._editor.setPlainText(text)
        ed._blocks_btn.setChecked(True)     # real auto-detect (many blocks)
        _process(200)
        ed._editor._flash_timer.stop()
        ed._editor.clearFocus()
        _process(40)
        self.addCleanup(ed.close)
        self.addCleanup(ed.deleteLater)
        editor = ed._editor
        vbar = editor.verticalScrollBar()
        self.assertGreater(vbar.maximum(), 0,
                           "the long document must be scrollable")
        counts = {"vp": 0, "gw": 0}

        class _Counter(QObject):
            def __init__(self, key, parent):
                super().__init__(parent)      # owned: never GC'd mid-test
                self.key = key

            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Type.Paint:
                    counts[self.key] += 1
                return False

        fvp = _Counter("vp", editor)
        fgw = _Counter("gw", editor)
        editor.viewport().installEventFilter(fvp)
        editor._block_gutter_widget.installEventFilter(fgw)
        _process(30)
        counts["vp"] = counts["gw"] = 0
        vbar.setValue(min(120, vbar.maximum()))
        _process(80)
        self.assertGreater(counts["gw"], 0,
                           "the block gutter must repaint on scroll "
                           "(P3.44.9.1 updateRequest wiring)")
        self.assertGreater(counts["vp"], 0)
        vbar.setValue(0)
        _process(30)

    def test_status_badge_painting_intact(self):
        """P3.45.1: update_block_status still paints the badge state."""
        ed = self._make_editor()
        editor = ed._editor
        blocks = ed._block_manager.blocks
        editor.update_block_status(blocks[0].id, "done",
                                   1.0, 12.5)
        _process(40)
        self.assertEqual(getattr(blocks[0], "status", None), "done")
        self.assertEqual(getattr(blocks[0], "duration", None), 12.5)
        # generating + parts coverage label derivation still resolves
        editor.update_block_status(blocks[1].id, "generating", 0.5)
        _process(40)
        self.assertEqual(getattr(blocks[1], "status", None), "generating")


# ===========================================================================
# 4) Click repaint scope (D-R4)
# ===========================================================================
class TestClickRepaint(unittest.TestCase):

    def _make_editor(self):
        from ui.panels.narration_editor import NarrationEditor
        ed = NarrationEditor()
        ed.resize(900, 700)
        ed.show()
        ed.clear_text()
        text = ("Alpha block sentence one.\nAlpha block sentence two.\n\n"
                "Beta block sentence.\n\n"
                "Gamma block sentence one.\nGamma block sentence two.\n")
        ed._editor.setPlainText(text)
        ed._blocks_btn.setChecked(True)
        _process(120)
        ed._editor._flash_timer.stop()
        ed._editor.clearFocus()
        _process(40)
        self.addCleanup(ed.close)
        self.addCleanup(ed.deleteLater)
        return ed

    def _counters(self, ed):
        """Paint counters as OWNED children of the editor (never GC'd
        mid-test — a filter QObject without an owner is destroyed as
        soon as the Python reference dies and silently stops counting)."""
        counts = {"vp": 0, "gw": 0}
        editor = ed._editor

        class _Counter(QObject):
            def __init__(self, key, parent):
                super().__init__(parent)
                self.key = key

            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Type.Paint:
                    counts[self.key] += 1
                return False

        fvp = _Counter("vp", editor)
        fgw = _Counter("gw", editor)
        editor.viewport().installEventFilter(fvp)
        editor._block_gutter_widget.installEventFilter(fgw)
        return counts

    def test_noop_click_repaints_nothing(self):
        """D-R4: re-clicking the ALREADY-selected block schedules zero
        viewport repaints (was: 1 full viewport paint + gutter paint)."""
        ed = self._make_editor()
        counts = self._counters(ed)
        blocks = ed._block_manager.blocks
        off = blocks[1].start_offset
        # real selection change first
        ed._on_block_click_requested(off)
        _process(40)
        self.assertGreaterEqual(counts["vp"], 1)
        # settle and measure the NO-OP re-click
        _process(30)
        counts["vp"] = counts["gw"] = 0
        ed._editor.clearFocus()
        _process(30)
        ed._on_block_click_requested(off)      # same block, same offset
        _process(60)
        self.assertEqual(counts["vp"], 0,
                         "a no-op click must not repaint the text viewport")
        # the documented visible feedback is preserved: the properties
        # panel was still refreshed (idempotent selection)
        self.assertEqual(ed._selected_block_id, blocks[1].id)

    def test_real_selection_change_still_repaints(self):
        """A REAL selection change keeps the full re-render (contract)."""
        ed = self._make_editor()
        counts = self._counters(ed)
        blocks = ed._block_manager.blocks
        _process(30)
        counts["vp"] = counts["gw"] = 0
        ed._on_block_click_requested(blocks[2].start_offset)
        _process(40)
        self.assertGreaterEqual(counts["vp"], 1)
        self.assertGreaterEqual(counts["gw"], 1)
        self.assertEqual(ed._selected_block_id, blocks[2].id)

    def test_real_mouse_click_selects_block(self):
        """A REAL mouse click inside the text area drives the P3.26 path:
        mousePressEvent → block_click_requested → selection."""
        ed = self._make_editor()
        editor = ed._editor
        blocks = ed._block_manager.blocks
        editor.clearFocus()
        _process(30)
        # a viewport position inside the SECOND block's first line
        from PySide6.QtGui import QTextCursor
        cur = QTextCursor(editor.document())
        cur.setPosition(blocks[1].start_offset)
        rect = editor.cursorRect(cur)
        QTest.mouseClick(editor.viewport(),
                         Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier,
                         QPoint(rect.center().x(), rect.center().y()))
        _process(60)
        self.assertEqual(ed._selected_block_id, blocks[1].id,
                         "a real text-area click must select the block "
                         "under the cursor (P3.26)")


# ===========================================================================
# 5) Flash behaviour (D-R5)
# ===========================================================================
class TestFlashBehaviour(unittest.TestCase):

    def test_flash_animates_gutter_without_viewport_repaints(self):
        from ui.panels.narration_editor import NarrationEditor
        ed = NarrationEditor()
        ed.resize(900, 700)
        ed.show()
        ed.clear_text()
        ed._editor.setPlainText("One block sentence.\n\nAnother one.\n")
        ed._blocks_btn.setChecked(True)
        _process(120)
        self.addCleanup(ed.close)
        self.addCleanup(ed.deleteLater)
        editor = ed._editor
        blocks = ed._block_manager.blocks
        counts = {"vp": 0, "gw": 0}

        class _Counter(QObject):
            def __init__(self, key):
                super().__init__()
                self.key = key

            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Type.Paint:
                    counts[self.key] += 1
                return False

        fvp = _Counter("vp")
        fgw = _Counter("gw")
        editor.viewport().installEventFilter(fvp)
        editor._block_gutter_widget.installEventFilter(fgw)
        editor.clearFocus()
        _process(30)
        counts["vp"] = counts["gw"] = 0
        editor.flash_blocks([b.id for b in blocks])
        # let the whole ~0.9s animation run
        _process(1000)
        # the visible flash still animates: the GUTTER repaints per tick
        self.assertGreaterEqual(counts["gw"], 10,
                                 "the gutter flash must keep animating")
        # the text viewport is NOT invalidated by the flash (was ~30
        # full viewport paints per flash pre-fix)
        self.assertLessEqual(counts["vp"], 2,
                             "flash ticks must not repaint the text "
                             "viewport (allowing only settle paints)")
        # the timer drives the fade and stops itself at the end
        self.assertFalse(editor._flash_timer.isActive())
        self.assertEqual(editor._flash_ids, set())
        self.assertEqual(editor._flash_alpha, 0.0)


# ===========================================================================
# 6) Theme / palette (D-R6)
# ===========================================================================
class TestThemeStripes(unittest.TestCase):

    def _editor_with_blocks(self):
        from ui.panels.narration_editor import NarrationEditor
        ed = NarrationEditor()
        ed.resize(900, 700)
        ed.show()
        ed.clear_text()
        ed._editor.setPlainText("First block text.\n\nSecond block text.\n")
        ed._blocks_btn.setChecked(True)
        _process(120)
        ed._editor._flash_timer.stop()
        _process(40)
        self.addCleanup(ed.close)
        self.addCleanup(ed.deleteLater)
        return ed

    def _stripe_colors(self, ed):
        """The alpha-45 extra-selection backgrounds (block stripes)."""
        from PySide6.QtGui import QColor
        found = []
        for s in ed._editor.extraSelections():
            bg = s.format.background().color()
            if bg.alpha() == 45:
                found.append(bg.name())
        self.assertTrue(found, "block stripes must exist")
        return found, QColor(Palette.BG_SURFACE), QColor(Palette.BG_SURFACE_ALT)

    def test_stripes_derive_from_live_palette_tokens(self):
        ed = self._editor_with_blocks()
        stripes, surf, surf_alt = self._stripe_colors(ed)
        for c in stripes:
            self.assertIn(c, (surf.name(), surf_alt.name()),
                          "stripes must use the theme surface tokens")

    def test_no_hardcoded_stripe_hex_in_editor(self):
        """D-R6: no QColor("#252840") / QColor("#222538") CONSTRUCTOR
        remains in the editor source (comments documenting the fix are
        fine — scan the AST for actual calls)."""
        tree = ast.parse(open(EDITOR_PY, encoding="utf-8").read())
        offenders = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "QColor"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in ("#252840", "#222538")):
                offenders.append(node.args[0].value)
        self.assertEqual(
            offenders, [],
            "the dark-only stripe literals must not be constructed "
            "anywhere in the editor")

    def test_stripes_follow_a_theme_switch(self):
        ed = self._editor_with_blocks()
        stripes_md, _, _ = self._stripe_colors(ed)
        apply_theme(APP, "light")
        ed.refresh_theme()          # the MainWindow theme-switch call
        _process(40)
        stripes_light, _, _ = self._stripe_colors(ed)
        self.assertNotEqual(set(stripes_light), set(stripes_md),
                            "light-mode stripes must differ from "
                            "modern-dark stripes")
        # restore the house theme for the other tests
        apply_theme(APP, DEFAULT_THEME)
        ed.refresh_theme()
        _process(40)

    def test_selection_and_override_contrast_unchanged(self):
        """The P3.15/P3.28 selection + override stripe semantics keep
        their exact Palette colors and alphas (frozen visual contract)."""
        ed = self._editor_with_blocks()
        from PySide6.QtGui import QColor
        editor = ed._editor
        blocks = ed._block_manager.blocks
        # select the first block, override the second
        ed._selected_block_id = blocks[0].id
        blocks[1].emotion = "Awe"
        editor._highlight_current_line()
        _process(30)
        saw_selected = saw_override = False
        for s in editor.extraSelections():
            bg = s.format.background().color()
            if bg.alpha() == 35 and bg.name() == QColor(Palette.ACCENT).name():
                saw_selected = True
            if bg.alpha() == 22 and bg.name() == QColor(Palette.WARNING).name():
                saw_override = True
        self.assertTrue(saw_selected, "selected block keeps ACCENT @ 35")
        self.assertTrue(saw_override, "override block keeps WARNING @ 22")


# ===========================================================================
# 7) P3.45.3 Batch identity safety (spot checks; full suite re-run separately)
# ===========================================================================
class TestBatchIdentitySafety(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p3454_id_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _process(40)

    def _dialog(self, parts):
        from ui.panels.batch_generation import BatchGenerationDialog
        project, scene = _scene_with(parts)
        bm = BatchManager(submit_fn=lambda req: None)
        for i, s in enumerate(scene.expected_audio_slots):
            bm.add_job(BatchJob(name="Part {0}".format(i + 1),
                                prompt="Text {0}.".format(i + 1),
                                slot_id=s["slot_id"]))
        sm = SettingsManager(os.path.join(self.tmp, "settings"))
        dlg = BatchGenerationDialog(bm, [], parent=None, scene=scene,
                                    project=project, app_root=self.tmp,
                                    settings_manager=sm)
        dlg.resize(1100, 680)
        dlg.layout().activate()
        dlg.show()
        _process(60)
        self.addCleanup(dlg.close)
        return dlg, bm

    def test_identity_chips_render_after_reorder(self):
        """P3.45.3: the Block·Part chips still travel with the JOBS (and
        the check state now travels the same way)."""
        dlg, bm = self._dialog([
            _make_part("A.", block_id="b1", part_of_block=1,
                       total_in_block=2, block_label="Intro"),
            _make_part("B.", block_id="b1", part_of_block=2,
                       total_in_block=2, block_label="Intro"),
            _make_part("C.", block_id="b2"),
        ])
        from ui.panels.batch_generation import _NameCell
        cell0 = dlg._table.cellWidget(0, dlg._col_name)
        self.assertIsInstance(cell0, _NameCell)
        self.assertEqual(cell0.identity_text(), "B1 · Intro · Part 1/2")
        bm.move_job(0, 2)
        dlg._refresh_table()
        _process(30)
        cell_now = dlg._table.cellWidget(2, dlg._col_name)
        self.assertEqual(cell_now.identity_text(),
                         "B1 · Intro · Part 1/2",
                         "the identity chip travels with the job object")

    def test_manual_window_unchanged(self):
        from ui.panels.batch_generation import BatchGenerationDialog
        bm = BatchManager(submit_fn=lambda req: None)
        bm.add_job(BatchJob(name="Voice over", prompt="x."))
        sm = SettingsManager(os.path.join(self.tmp, "settings"))
        dlg = BatchGenerationDialog(bm, [], parent=None,
                                    app_root=self.tmp, settings_manager=sm)
        dlg.show()
        _process(50)
        self.addCleanup(dlg.close)
        self.assertIsNone(dlg._table.cellWidget(0, dlg._col_name))
        self.assertIsNotNone(dlg._table.item(0, dlg._col_name))


if __name__ == "__main__":
    unittest.main(verbosity=2)
