# ---------------------------------------------------------------------------
# P3.40 DPI probe — spawned as a subprocess with QT_SCALE_FACTOR set BEFORE
# QApplication creation (scale cannot change in-process). Prints a JSON
# verdict; the test parses it.
# ---------------------------------------------------------------------------
import json
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if not os.environ.get("LD_LIBRARY_PATH"):
    os.environ["LD_LIBRARY_PATH"] = "/tmp/gllibs/usr/lib/x86_64-linux-gnu"

APP_ROOT = sys.argv[1]
SCALE = sys.argv[2]
sys.path.insert(0, APP_ROOT)

fake_torch = types.ModuleType("torch")


class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


fake_torch.no_grad = lambda: _NoGrad()
fake_torch.manual_seed = lambda s: None
fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)
sys.modules.setdefault("torch", fake_torch)

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)
from ui.theme import apply_theme  # noqa: E402
apply_theme(APP, "modern-dark")

from engine.models import Project, Scene  # noqa: E402
from engine.batch_manager import BatchManager, BatchJob, JobStatus  # noqa: E402
from engine.narration_splitter import SplitPart  # noqa: E402
from engine.audio_provenance import materialize_expected_slots  # noqa: E402
from ui.panels.batch_generation import BatchGenerationDialog  # noqa: E402


def _process(ms=60):
    import time
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def build():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="p340_dpi_")
    project = Project(name="Eden", id="proj1")
    scene = Scene(id="sc1abcd", name="Scene 03", project_id="proj1")
    project.add_scene(scene)
    scene.expected_audio_slots = materialize_expected_slots([
        SplitPart(text="Line one for the audit.", prompt="Line one.",
                  speaker="", source_block_id="b1", part_of_block=1,
                  total_parts_in_block=1, block_label="B1",
                  character_id=None, estimated_duration=8.0,
                  char_count=15),
    ])
    scene.audio_assets = []
    scene.combined_outputs = []
    bm = BatchManager(submit_fn=lambda req: None)
    j0 = BatchJob(name="Part 01", prompt="Line one.", slot_id="b1:1")
    j0.status = JobStatus.COMPLETED
    j0.output_path = "outputs/x.wav"
    j0.output_duration = 5.0
    bm.add_job(j0)
    from engine.settings_manager import SettingsManager
    sm = SettingsManager(os.path.join(tmp, "settings"))
    dlg = BatchGenerationDialog(bm, [], parent=None, scene=scene,
                                project=project, app_root=tmp,
                                settings_manager=sm)
    dlg.resize(1100, 680)
    dlg.layout().activate()
    return dlg


def np_grab(t):
    pm = t.grab()
    dpr = pm.devicePixelRatio()
    img = pm.toImage().convertToFormat(
        __import__("PySide6.QtGui",
                   fromlist=["QImage"]).QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(h, w, 4)
    return arr.copy(), dpr


def accent_runs_at_boundaries(t, row=0):
    """Return count of bright-purple vertical runs (>= 60% row height)
    within +-3 px of any column boundary in the selected row band."""
    arr, dpr = np_grab(t)
    from PySide6.QtCore import QPoint
    vp = t.viewport()
    org = vp.mapTo(t, QPoint(0, 0))
    hdr = t.horizontalHeader()
    y0 = org.y() + t.rowViewportPosition(row) + 1
    y1 = y0 + t.rowHeight(row) - 1
    runs = 0
    rh = t.rowHeight(row)
    for c in range(t.columnCount()):
        li = hdr.logicalIndex(c)
        bx = (org.x() + hdr.sectionViewportPosition(li)) * dpr
        for x in range(max(0, int(bx - 3 * dpr)),
                       min(arr.shape[1], int(bx + 4 * dpr))):
            colpx = arr[int(y0 * dpr):int(y1 * dpr), x, :3].astype(int)
            # bright accent #d0bcff or saturated purple with high value
            r, g, b = colpx[:, 0], colpx[:, 1], colpx[:, 2]
            purple = (b > r - 10) & (b > 120) & (r > 120) & (b > g + 25)
            if purple.sum() >= rh * dpr * 0.6:
                runs += 1
    return runs


def main():
    dlg = build()
    dlg.show()
    dlg.activateWindow()
    _process(200)
    t = dlg._table
    QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton,
                     pos=t.visualRect(t.model().index(0, 2)).center())
    _process(120)

    result = {"scale": SCALE,
              "device_pixel_ratio": APP.devicePixelRatio(),
              "row_height": t.rowHeight(0),
              "accent_boundary_runs": accent_runs_at_boundaries(t, 0)}

    aw = t.cellWidget(0, dlg._col_act)
    btns = aw.findChildren(QPushButton)
    result["action_widget_height"] = aw.height()
    result["button_heights"] = [b.height() for b in btns]
    result["buttons_inside"] = all(
        b.y() >= 0 and b.y() + b.height() <= aw.height() for b in btns)
    tops = [b.y() for b in btns]
    bots = [b.y() + b.height() for b in btns]
    result["clearance_top"] = min(tops)
    result["clearance_bottom"] = aw.height() - max(bots)
    result["centered"] = abs(result["clearance_top"]
                             - result["clearance_bottom"]) <= 2

    # glyph clipping: play glyph bbox fully inside its button
    arr, dpr = np_grab(t)
    from PySide6.QtCore import QPoint
    vp = t.viewport()
    org = vp.mapTo(t, QPoint(0, 0))
    b = btns[0]
    gx = int((org.x() + aw.x() + b.x()) * dpr)
    gy = int((org.y() + aw.y() + b.y()) * dpr)
    bw = int(b.width() * dpr)
    bh = int(b.height() * dpr)
    sub = arr[gy:gy + bh, gx:gx + bw, :3]
    light = (sub[:, :, 0] > 100) & (sub[:, :, 1] > 100) & (sub[:, :, 2] > 100)
    ys, xs = np.nonzero(light)
    result["play_glyph_found"] = bool(len(xs))
    if len(xs):
        result["play_glyph_clipped"] = bool(
            ys.min() <= 0 or ys.max() >= bh - 1
            or xs.min() <= 0 or xs.max() >= bw - 1)

    # regen button fully painted (accent bg fills the whole rect)
    br = btns[2]
    gx = int((org.x() + aw.x() + br.x()) * dpr)
    gy = int((org.y() + aw.y() + br.y()) * dpr)
    bw = int(br.width() * dpr)
    bh = int(br.height() * dpr)
    sub = arr[gy:gy + bh, gx:gx + bw, :3]
    is_accent = ((np.abs(sub[:, :, 0].astype(int) - 208) < 45)
                 & (np.abs(sub[:, :, 1].astype(int) - 188) < 45)
                 & (np.abs(sub[:, :, 2].astype(int) - 255) < 30))
    # sample rows: top edge+2, middle, bottom edge-2 (device px)
    rows_ok = [bool(is_accent[2].mean() > 0.4),
               bool(is_accent[bh // 2].mean() > 0.4),
               bool(is_accent[bh - 3].mean() > 0.4)]
    result["regen_fully_painted"] = all(rows_ok)

    dlg.close()
    print("P340_DPI_JSON" + json.dumps(result))


if __name__ == "__main__":
    main()
