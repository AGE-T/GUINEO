# ---------------------------------------------------------------------------
# P3.40 — Batch Generation table row rendering audit (runtime tests)
# ---------------------------------------------------------------------------
# Root causes fixed (runtime-verified before the fix):
#   F1  Bright purple full-height 2px vertical line at EVERY column boundary
#       of the selected row: the APP-level stylesheet
#       (ui.theme.generate_qss) rule
#       ``QTableWidget::item:selected { border-left: 2px solid #d0bcff }``
#       cascaded into the batch table because the dialog's own stylesheet
#       never reset border-left (widget stylesheets only override the
#       properties they declare).
#   F2  Actions column clipping: ``QTableWidget::item { padding: 6px }``
#       insets every setCellWidget() into the item CONTENT rect — the
#       action widget got 48 - 6 - 6 - 1 = 35px for a 4+36+4 = 44px
#       fixed button band: buttons overflowed 5px past the widget bottom
#       (clipped), top-anchored (asymmetric clearance).
#
# These tests pin the POST-fix contract with real QApplication, the real
# Modern Dark theme (app stylesheet + QPalette applied exactly like
# MainWindow does) and production widgets:
#   1. selected row styling        — no accent vertical line at boundaries
#   2. column boundary rendering   — uniform single selection fill
#   3. Actions widget geometry     — full item content rect (47px)
#   4. button geometry             — 36x36, fully inside, equal spacing
#   5. icon geometry               — 32px, glyph not clipped
#   6. vertical centering          — symmetric clearance + explicit align
#   7. selected state              — one coherent treatment only
#   8. disabled state              — running batch keeps geometry
#   9. long filename               — Actions independent of Name width
#  10. resized Actions column      — default/large keep buttons visible
#  11. state matrix                — every JobStatus, current cell in every
#                                    column, keyboard nav, focus lost
#  12. fullscreen                  — no regression in fullscreen geometry
#  13. DPI-aware geometry          — subprocess probe at 100/125/150%
#  14. column persistence          — P3.35 save/restore intact
# ---------------------------------------------------------------------------
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if not os.environ.get("LD_LIBRARY_PATH"):
    os.environ["LD_LIBRARY_PATH"] = "/tmp/gllibs/usr/lib/x86_64-linux-gnu"

APP_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), ".."))

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
    QApplication, QPushButton,
)
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)

# The REAL app-level theme — exactly what MainWindow applies. Without this
# the app-QSS cascade (root cause F1) is not exercised and the test would
# pass vacuously.
from ui.theme import apply_theme  # noqa: E402
apply_theme(APP, "modern-dark")

from engine.models import Project, Scene  # noqa: E402
from engine.batch_manager import BatchManager, BatchJob, JobStatus  # noqa: E402
from engine.narration_splitter import SplitPart  # noqa: E402
from engine.audio_provenance import materialize_expected_slots  # noqa: E402
from ui.panels.batch_generation import BatchGenerationDialog  # noqa: E402

ACCENT_RGB = (208, 188, 255)   # Modern Dark accent #d0bcff (line F1)
FILL_HUE_PURPLE = True         # dialog fill rgba(139, 92, 246, 35)


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _part(text="A test sentence.", block_id="b1", label="B1"):
    return SplitPart(
        text=text, prompt=text, speaker="",
        source_block_id=block_id, part_of_block=1,
        total_parts_in_block=1, block_label=label,
        character_id=None, estimated_duration=8.0, char_count=len(text))


def _scene_dialog(tmp, statuses=(JobStatus.COMPLETED, JobStatus.PENDING),
                  long_name=False):
    from engine.settings_manager import SettingsManager
    project = Project(name="Eden", id="proj1")
    scene = Scene(id="sc1abcd", name="Scene 03", project_id="proj1")
    project.add_scene(scene)
    scene.expected_audio_slots = materialize_expected_slots(
        [_part("Line one."), _part("Line two.", block_id="b2", label="B2")])
    scene.audio_assets = []
    scene.combined_outputs = []
    bm = BatchManager(submit_fn=lambda req: None)
    for i, st in enumerate(statuses):
        j = BatchJob(name="Part {0:02d}".format(i + 1),
                     prompt="Line {0}.".format(i + 1),
                     slot_id="b{0}:1".format(i + 1))
        j.status = st
        if st == JobStatus.COMPLETED:
            j.output_path = "outputs/x{0}.wav".format(i)
            j.output_duration = 5.0
        if long_name:
            j.name = "v" * 300
            j.output_filename = "f" * 300
        bm.add_job(j)
    # Hermetic: inject an isolated SettingsManager (the _settings()
    # fallback would otherwise write column/geometry state into the
    # PROJECT settings folder and leak between test runs).
    sm = SettingsManager(os.path.join(tmp, "settings"))
    dlg = BatchGenerationDialog(bm, [], parent=None, scene=scene,
                                project=project, app_root=tmp,
                                settings_manager=sm)
    dlg.resize(1100, 680)
    dlg.layout().activate()
    dlg.show()
    dlg.activateWindow()
    _process(150)
    return dlg


def _manual_dialog(tmp):
    from engine.settings_manager import SettingsManager
    bm = BatchManager(submit_fn=lambda req: None)
    j0 = BatchJob(name="Voice over take one", prompt="The quick brown fox.")
    j0.status = JobStatus.COMPLETED
    j0.output_path = "outputs/a.wav"
    j0.output_duration = 3.2
    bm.add_job(j0)
    bm.add_job(BatchJob(name="Voice over take two", prompt="Jumps over."))
    sm = SettingsManager(os.path.join(tmp, "settings"))
    dlg = BatchGenerationDialog(bm, [], parent=None, app_root=tmp,
                                settings_manager=sm)
    dlg.resize(1100, 680)
    dlg.layout().activate()
    dlg.show()
    dlg.activateWindow()
    _process(150)
    return dlg


def _select_row(dlg, row=0, col=None):
    t = dlg._table
    if col is None:
        col = dlg._col_name
    QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton,
                     pos=t.visualRect(t.model().index(row, col)).center())
    _process(80)


def _grab_array(t):
    pm = t.grab()
    dpr = pm.devicePixelRatio()
    img = pm.toImage().convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(h, w, 4)
    return arr.copy(), dpr


def _row_band(t, row=0):
    """(y0, y1, row_height, viewport_origin) in TABLE coordinates."""
    vp = t.viewport()
    org = vp.mapTo(t, QPoint(0, 0))
    y0 = org.y() + t.rowViewportPosition(row)
    return y0, y0 + t.rowHeight(row), t.rowHeight(row), org


def _is_accentish(px):
    r, g, b = int(px[0]), int(px[1]), int(px[2])
    return (abs(r - ACCENT_RGB[0]) < 40
            and abs(g - ACCENT_RGB[1]) < 40
            and abs(b - ACCENT_RGB[2]) < 30)


def _accent_boundary_pixel_count(t, row=0):
    """Bright-accent pixels within +-3px of any column boundary in the
    row band (device-pixel aware). Zero is the post-fix contract."""
    arr, dpr = _grab_array(t)
    y0, _y1, rh, org = _row_band(t, row)
    hdr = t.horizontalHeader()
    total = 0
    for c in range(t.columnCount()):
        li = hdr.logicalIndex(c)
        bx = int((org.x() + hdr.sectionViewportPosition(li)) * dpr)
        for x in range(max(0, int(bx - 3 * dpr)),
                       min(arr.shape[1], int(bx + 4 * dpr))):
            band = arr[int(y0 * dpr):int((y0 + rh) * dpr), x, :3]
            hits = sum(1 for yy in range(band.shape[0])
                       if _is_accentish(band[yy]))
            if hits >= rh * dpr * 0.5:
                total += 1
    return total


def _row_fill_samples(t, row=0):
    """Sample the selection fill colour in every column of the row.

    Sampled at y = row_top + 3 — above the cell-widget content (buttons
    start at y=5, pill/text at y>=12) so the pixel is guaranteed to be
    the delegate-painted item background, never a child widget's glyph
    (e.g. the accent-coloured "N versions" button text).
    """
    arr, dpr = _grab_array(t)
    y0, _y1, rh, org = _row_band(t, row)
    ysample = int((y0 + 3) * dpr)
    hdr = t.horizontalHeader()
    out = []
    for c in range(t.columnCount()):
        li = hdr.logicalIndex(c)
        x0 = int((org.x() + hdr.sectionViewportPosition(li)) * dpr)
        w = int(hdr.sectionSize(li) * dpr)
        for dx in (8 * dpr, w / 2.0, (w - 8 * dpr)):
            x = int(x0 + dx)
            if 0 <= ysample < arr.shape[0] and 0 <= x < arr.shape[1]:
                out.append(tuple(int(v) for v in arr[ysample, x, :3]))
    return out


def _is_purple_fill(px):
    r, g, b = int(px[0]), int(px[1]), int(px[2])
    return (b > g + 8) and (r > g + 3) and 40 < r < 110 and b < 130


def _buttons(dlg, row=0):
    aw = dlg._table.cellWidget(row, dlg._col_act)
    btns = aw.findChildren(QPushButton)
    # stable order: play, stop, regen (creation order)
    return aw, btns


# ===========================================================================
# 1-2-7: selection rendering (the purple-divider fix)
# ===========================================================================
class TestSelectionRendering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p340_sel_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _close(self, dlg):
        dlg.close()
        _process(50)

    def test_no_accent_line_at_boundaries_scene_mode(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        _select_row(dlg, 0)
        t = dlg._table
        self.assertEqual([r.row() for r in t.selectionModel()
                          .selectedRows()], [0])
        self.assertEqual(_accent_boundary_pixel_count(t, 0), 0)

    def test_no_accent_line_at_boundaries_manual_mode(self):
        dlg = _manual_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        _select_row(dlg, 0)
        self.assertEqual(_accent_boundary_pixel_count(dlg._table, 0), 0)

    def test_selection_is_one_uniform_fill(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        _select_row(dlg, 0)
        samples = _row_fill_samples(dlg._table, 0)
        # every column's middle pixel is the SAME translucent purple fill
        for px in samples:
            self.assertTrue(_is_purple_fill(px), px)
        base = samples[0]
        for px in samples[1:]:
            self.assertLessEqual(max(abs(a - b) for a, b in
                                      zip(px, base)), 15, (base, px))

    def test_unselected_row_has_no_purple(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        _select_row(dlg, 1)
        samples = _row_fill_samples(dlg._table, 0)
        for px in samples:
            self.assertFalse(_is_purple_fill(px), px)
        for px in _row_fill_samples(dlg._table, 1):
            self.assertTrue(_is_purple_fill(px), px)

    def test_current_cell_in_every_column_no_artifact(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        _select_row(dlg, 0)
        t = dlg._table
        for col in range(t.columnCount()):
            t.setCurrentIndex(t.model().index(0, col))
            _process(40)
            self.assertEqual(_accent_boundary_pixel_count(t, 0), 0,
                             "current cell col {0}".format(col))

    def test_keyboard_navigation_no_artifact(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        t = dlg._table
        t.setCurrentIndex(t.model().index(0, 0))
        QTest.keyClick(t, Qt.Key.Key_Right)
        QTest.keyClick(t, Qt.Key.Key_Right)
        QTest.keyClick(t, Qt.Key.Key_Down)
        _process(80)
        self.assertEqual(_accent_boundary_pixel_count(t, 0), 0)
        self.assertEqual(_accent_boundary_pixel_count(t, 1), 0)

    def test_focus_lost_selection_stable(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        _select_row(dlg, 0)
        t = dlg._table
        t.clearFocus()
        _process(80)
        self.assertEqual(_accent_boundary_pixel_count(t, 0), 0)
        for px in _row_fill_samples(t, 0):
            self.assertTrue(_is_purple_fill(px), px)


# ===========================================================================
# 3-4-5-6-8: Actions geometry (the clipping fix)
# ===========================================================================
class TestActionsGeometry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p340_geo_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _close(self, dlg):
        dlg.close()
        _process(50)

    def test_action_widget_fills_item_content(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        t = dlg._table
        aw = t.cellWidget(0, dlg._col_act)
        # 48px row - 1px ::item border-bottom = 47px content rect
        self.assertEqual(aw.height(), 47)
        self.assertEqual(aw.y(), 0)

    def test_buttons_fully_inside_widget_and_centered(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        aw, btns = _buttons(dlg, 0)
        self.assertEqual(len(btns), 3)
        for b in btns:
            self.assertEqual((b.width(), b.height()), (36, 36))
            self.assertGreaterEqual(b.y(), 0)
            self.assertLessEqual(b.y() + b.height(), aw.height())
        tops = [b.y() for b in btns]
        bots = [b.y() + b.height() for b in btns]
        self.assertLessEqual(abs(min(tops) - (aw.height() - max(bots))), 2)
        # equal spacing between the three buttons
        self.assertEqual(btns[1].x() - (btns[0].x() + btns[0].width()), 4)
        self.assertEqual(btns[2].x() - (btns[1].x() + btns[1].width()), 4)
        # same vertical line for all three
        self.assertEqual(len(set(tops)), 1)

    def test_icon_size_not_reduced(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        _aw, btns = _buttons(dlg, 0)
        for b in btns:
            self.assertEqual(b.iconSize().width(), 32)
            self.assertEqual(b.iconSize().height(), 32)

    def test_regen_button_fully_painted(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        _select_row(dlg, 0)
        t = dlg._table
        aw, btns = _buttons(dlg, 0)
        br = btns[2]
        arr, dpr = _grab_array(t)
        y0, _y1, _rh, org = _row_band(t, 0)
        gx = int((org.x() + aw.x() + br.x()) * dpr)
        gy = int((org.y() + aw.y() + br.y()) * dpr)
        bh = int(br.height() * dpr)
        sub = arr[gy:gy + bh, gx:gx + int(br.width() * dpr), :3]
        is_accent = ((np.abs(sub[:, :, 0].astype(int) - ACCENT_RGB[0]) < 45)
                     & (np.abs(sub[:, :, 1].astype(int) - ACCENT_RGB[1]) < 45)
                     & (np.abs(sub[:, :, 2].astype(int) - ACCENT_RGB[2]) < 30))
        # the accent background reaches the BOTTOM edge rows of the button
        self.assertGreater(is_accent[2].mean(), 0.4)
        self.assertGreater(is_accent[bh // 2].mean(), 0.4)
        self.assertGreater(is_accent[bh - 3].mean(), 0.4)

    def test_play_glyph_not_clipped(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        _select_row(dlg, 0)
        t = dlg._table
        aw, btns = _buttons(dlg, 0)
        b = btns[0]
        arr, dpr = _grab_array(t)
        y0, _y1, _rh, org = _row_band(t, 0)
        gx = int((org.x() + aw.x() + b.x()) * dpr)
        gy = int((org.y() + aw.y() + b.y()) * dpr)
        bw, bh = int(b.width() * dpr), int(b.height() * dpr)
        sub = arr[gy:gy + bh, gx:gx + bw, :3]
        light = ((sub[:, :, 0] > 100) & (sub[:, :, 1] > 100)
                 & (sub[:, :, 2] > 100))
        ys, xs = np.nonzero(light)
        self.assertGreater(len(xs), 0, "play glyph not rendered")
        self.assertGreater(ys.min(), 0)
        self.assertLess(ys.max(), bh - 1)
        self.assertGreater(xs.min(), 0)
        self.assertLess(xs.max(), bw - 1)

    def test_disabled_running_state_keeps_geometry(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        dlg._manager._running = True
        dlg._refresh_table()
        _process(60)
        aw, btns = _buttons(dlg, 0)
        for b in btns:
            self.assertEqual((b.width(), b.height()), (36, 36))
            self.assertLessEqual(b.y() + b.height(), aw.height())
        self.assertFalse(btns[0].isEnabled())
        self.assertEqual(_accent_boundary_pixel_count(dlg._table, 0), 0)

    def test_all_job_statuses_geometry(self):
        statuses = [JobStatus.COMPLETED, JobStatus.FAILED,
                    JobStatus.GENERATING, JobStatus.PENDING,
                    JobStatus.SKIPPED]
        dlg = _scene_dialog(self.tmp, statuses=statuses)
        self.addCleanup(self._close, dlg)
        for row in range(len(statuses)):
            aw, btns = _buttons(dlg, row)
            for b in btns:
                self.assertEqual((b.width(), b.height()), (36, 36))
                self.assertLessEqual(b.y() + b.height(), aw.height())
            self.assertEqual(aw.height(), 47)


# ===========================================================================
# 9-10: long filename + column resize
# ===========================================================================
class TestColumnsAndFilenames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p340_col_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _close(self, dlg):
        dlg.close()
        _process(50)

    def test_long_filename_leaves_actions_intact(self):
        dlg = _scene_dialog(self.tmp, long_name=True)
        self.addCleanup(self._close, dlg)
        _select_row(dlg, 0)
        t = dlg._table
        hdr = t.horizontalHeader()
        act_w = hdr.sectionSize(dlg._col_act)
        aw, btns = _buttons(dlg, 0)
        for b in btns:
            self.assertLessEqual(b.y() + b.height(), aw.height())
        self.assertGreaterEqual(act_w, 124)
        self.assertEqual(_accent_boundary_pixel_count(t, 0), 0)

    def test_resized_actions_column_large(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        t = dlg._table
        hdr = t.horizontalHeader()
        hdr.resizeSection(dlg._col_act, 220)
        _process(60)
        _select_row(dlg, 0)
        aw, btns = _buttons(dlg, 0)
        for b in btns:
            self.assertLessEqual(b.y() + b.height(), aw.height())
        self.assertEqual(btns[0].y(), btns[1].y(), btns[2].y())
        self.assertEqual(_accent_boundary_pixel_count(t, 0), 0)

    def test_default_actions_width_above_derived_minimum(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        hdr = dlg._table.horizontalHeader()
        # derived minimum: 3*36 + 2*4 spacing + 2*4 margins + 2*6 padding
        self.assertGreaterEqual(hdr.sectionSize(dlg._col_act), 124)

    def test_column_persistence_intact(self):
        # P3.35 save/restore must keep working after the QSS change.
        dlg = _scene_dialog(self.tmp)
        hdr = dlg._table.horizontalHeader()
        hdr.resizeSection(dlg._col_act, 200)
        dlg._save_column_state()
        dlg.close()
        _process(60)
        dlg2 = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg2)
        hdr2 = dlg2._table.horizontalHeader()
        self.assertEqual(hdr2.sectionSize(dlg2._col_act), 200)
        # restored geometry is still valid
        aw, btns = _buttons(dlg2, 0)
        for b in btns:
            self.assertLessEqual(b.y() + b.height(), aw.height())


# ===========================================================================
# 11-12: fullscreen + manual-mode geometry
# ===========================================================================
class TestFullscreenAndManualMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p340_fs_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _close(self, dlg):
        dlg.close()
        _process(50)

    def test_fullscreen_keeps_geometry_and_selection(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        dlg.showFullScreen()
        _process(150)
        _select_row(dlg, 0)
        t = dlg._table
        self.assertEqual(_accent_boundary_pixel_count(t, 0), 0)
        aw, btns = _buttons(dlg, 0)
        for b in btns:
            self.assertLessEqual(b.y() + b.height(), aw.height())
        for px in _row_fill_samples(t, 0):
            self.assertTrue(_is_purple_fill(px), px)
        dlg.showNormal()
        _process(80)

    def test_manual_mode_geometry(self):
        dlg = _manual_dialog(self.tmp)
        self.addCleanup(self._close, dlg)
        _select_row(dlg, 0)
        aw, btns = _buttons(dlg, 0)
        self.assertEqual(aw.height(), 47)
        for b in btns:
            self.assertEqual((b.width(), b.height()), (36, 36))
            self.assertLessEqual(b.y() + b.height(), aw.height())


# ===========================================================================
# 13: DPI-aware geometry (subprocess probe at 100/125/150%)
# ===========================================================================
class TestDpiGeometry(unittest.TestCase):
    def _probe(self, scale):
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["QT_SCALE_FACTOR"] = scale
        env["LD_LIBRARY_PATH"] = "/tmp/gllibs/usr/lib/x86_64-linux-gnu"
        probe = os.path.join(os.path.dirname(__file__), "p340_dpi_probe.py")
        proc = subprocess.run(
            [sys.executable, probe, APP_ROOT, scale],
            capture_output=True, text=True, env=env, timeout=180)
        for line in proc.stdout.splitlines():
            if line.startswith("P340_DPI_JSON"):
                return json.loads(line[len("P340_DPI_JSON"):])
        self.fail("probe produced no verdict: {0} / {1}".format(
            proc.stdout[-800:], proc.stderr[-800:]))

    def test_dpi_100(self):
        self._assert_verdict(self._probe("1.0"), 1.0)

    def test_dpi_125(self):
        self._assert_verdict(self._probe("1.25"), 1.25)

    def test_dpi_150(self):
        self._assert_verdict(self._probe("1.5"), 1.5)

    def _assert_verdict(self, res, scale):
        self.assertEqual(res["scale"], str(scale))
        self.assertEqual(res["accent_boundary_runs"], 0, res)
        self.assertEqual(res["action_widget_height"], 47, res)
        self.assertTrue(res["buttons_inside"], res)
        self.assertTrue(res["centered"], res)
        self.assertEqual(res["button_heights"], [36, 36, 36], res)
        self.assertTrue(res["play_glyph_found"], res)
        self.assertFalse(res["play_glyph_clipped"], res)
        self.assertTrue(res["regen_fully_painted"], res)


# ===========================================================================
# 14: stylesheet / layout regression contract
# ===========================================================================
class TestQssContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p340_qss_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dialog_qss_resets_per_cell_borders(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(dlg.close)
        qss = dlg._table.styleSheet()
        self.assertIn("padding: 0px 6px", qss)
        self.assertIn("QTableWidget::item:focus", qss)
        # :selected must explicitly reset the border (incl. the inherited
        # app-level border-left) — declared AFTER :focus so the current
        # cell still gets the fill + row separator.
        self.assertLess(qss.index("QTableWidget::item:focus"),
                        qss.index("QTableWidget::item:selected"))
        sel = qss.split("QTableWidget::item:selected", 1)[1][:200]
        self.assertIn("border: none", sel)
        self.assertIn("background: rgba(139, 92, 246, 35)", sel)

    def test_action_layout_explicit_vcenter(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(dlg.close)
        aw = dlg._table.cellWidget(0, dlg._col_act)
        self.assertTrue(aw.layout().alignment()
                        & Qt.Alignment.AlignVCenter)

    def test_button_padding_fits_icon_exactly(self):
        dlg = _scene_dialog(self.tmp)
        self.addCleanup(dlg.close)
        _aw, btns = _buttons(dlg, 0)
        for b in btns:
            qss = b.styleSheet()
            self.assertIn("padding: 1px", qss)
        # 32 icon + 2*1 border + 2*1 padding == 36 button
        self.assertEqual(32 + 2 + 2, btns[0].height())


if __name__ == "__main__":
    unittest.main(verbosity=2)
