"""
SpeechStudio — P3.25 Model Load Dialog white/blank window regression tests
===========================================================================

USER-REPORTED DEFECT (post-P3.25): the "Loading Model - SpeechStudio"
dialog appeared as a WHITE, CONTENT-FREE window during model loading
(only the OS-drawn title bar was visible).

ROOT CAUSE (reproduced with audit_probes_p325/probe_paint_timing.py):
  1. MainWindow submitted the engine load job BEFORE showing the dialog.
     The worker thread immediately began ``import torch`` — on Windows
     the C-extension init holds the GIL in long stretches, so the GUI
     thread's event loop could not process the dialog's expose/paint
     events for many seconds. A mapped-but-unpainted top-level window is
     filled by the OS with the default WHITE background brush.
  2. The app-wide QSS styles QDialog backgrounds as TRANSPARENT (the
     main window's dark look comes from ambient background layers — a
     separate top-level dialog has no such backdrop), and the dialog set
     no explicit background of its own.

FIX (runtime contract proven by these tests):
  * ModelLoadDialog.start() paints the dialog SYNCHRONOUSLY
    (processEvents(ExcludeUserInputEvents) + repaint()) while the GUI
    thread still owns the GIL.
  * MainWindow shows + paints the dialog BEFORE submitting the load job
    (both _load_model and _load_model_and_generate), and closes it again
    if the submission itself raises.
  * The dialog carries an explicit dark background (AssembleDialog
    pattern) instead of relying on the transparent QDialog QSS.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_25_model_load_dialog.py -v
"""

from __future__ import annotations
import os
import sys
import time
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch, PropertyMock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage

_app = QApplication.instance() or QApplication([])

from ui.theme import apply_theme, DEFAULT_THEME


class _PaintSpyDialogMixin:
    """Counts real paintEvent deliveries on the dialog body."""

    def __init__(self, *args, **kwargs):
        self.paint_count = 0
        super().__init__(*args, **kwargs)

    def paintEvent(self, ev):
        self.paint_count += 1
        super().paintEvent(ev)


class _LoadDialogHarness(unittest.TestCase):
    """Real Engine + real MainWindow in an isolated tmpdir."""

    @classmethod
    def setUpClass(cls):
        from engine.engine import Engine
        from ui.main_window import MainWindow
        cls.tmpdir = tempfile.mkdtemp(prefix="ss_p325_mld_")
        cls.engine = Engine(app_root=cls.tmpdir)
        # Force the "model not loaded" state so _load_model() runs its
        # dialog path (the harness default elsewhere patches it True).
        cls._loaded_patcher = patch.object(
            type(cls.engine._model), "is_loaded",
            new_callable=PropertyMock, return_value=False)
        cls._loaded_patcher.start()
        cls.win = MainWindow(cls.engine)

    @classmethod
    def tearDownClass(cls):
        try:
            if getattr(cls.win, "_model_load_dialog", None) is not None:
                cls.win._model_load_dialog.stop()
                cls.win._model_load_dialog = None
        except Exception:
            pass
        try:
            cls.win.close()
        except Exception:
            pass
        cls._loaded_patcher.stop()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _cleanup_dialog(self):
        dlg = getattr(self.win, "_model_load_dialog", None)
        if dlg is not None:
            dlg.stop()
            self.win._model_load_dialog = None


class TestModelLoadDialogPaint(_LoadDialogHarness):
    """The white/blank loading window regression."""

    def test_dialog_painted_before_load_submitted(self):
        """RUNTIME: when Engine.load_model is invoked, the dialog must
        already be visible AND painted (>=1 paintEvent) — the load worker
        (import torch) must never be able to starve the first paint."""
        import ui.panels.model_load_dialog as mld_mod

        class _Spy(_PaintSpyDialogMixin, mld_mod.ModelLoadDialog):
            pass

        seen = {}

        def fake_load(use_cuda=True, callback=None):
            dlg = self.win._model_load_dialog
            seen["visible"] = dlg is not None and dlg.isVisible()
            seen["painted"] = getattr(dlg, "paint_count", 0)
            seen["dialog"] = dlg
            return None

        with patch.object(self.engine, "load_model", side_effect=fake_load), \
                patch.object(mld_mod, "ModelLoadDialog", _Spy):
            self.win._load_model()
        try:
            self.assertTrue(seen.get("visible"),
                            "dialog not visible when the load job was "
                            "submitted")
            self.assertGreaterEqual(seen.get("painted", 0), 1,
                                    "dialog had received NO paint event "
                                    "when the load job was submitted — "
                                    "the white-window regression")
        finally:
            self._cleanup_dialog()

    def test_dialog_painted_without_any_event_loop(self):
        """RUNTIME (the actual white-window mechanism): a GIL-hogging
        load worker (import torch) cannot prevent the first paint —
        after _load_model() returns, with ZERO event-loop processing,
        the dialog must already be painted."""
        import ui.panels.model_load_dialog as mld_mod

        class _Spy(_PaintSpyDialogMixin, mld_mod.ModelLoadDialog):
            pass

        def _hold_gil(seconds: float) -> None:
            try:
                sys.setswitchinterval(seconds + 10.0)
                t0 = time.time()
                while time.time() - t0 < seconds:
                    pass
            finally:
                sys.setswitchinterval(0.005)

        def fake_load(use_cuda=True, callback=None):
            t = threading.Thread(target=_hold_gil, args=(1.2,),
                                 daemon=True)
            t.start()
            seen["worker"] = t
            return None

        seen = {}
        with patch.object(self.engine, "load_model", side_effect=fake_load), \
                patch.object(mld_mod, "ModelLoadDialog", _Spy):
            self.win._load_model()
            # NO processEvents() here — the paint must have happened
            # synchronously inside dialog.start().
            dlg = self.win._model_load_dialog
            try:
                self.assertIsNotNone(dlg)
                self.assertTrue(dlg.isVisible())
                self.assertGreaterEqual(dlg.paint_count, 1,
                                        "dialog not painted before the "
                                        "GIL-hogging worker started")
            finally:
                self._cleanup_dialog()
            worker = seen.get("worker")
            if worker is not None:
                worker.join(timeout=10)

    def test_generate_path_painted_before_load_submitted(self):
        """RUNTIME: _load_model_and_generate keeps the same invariant."""
        import ui.panels.model_load_dialog as mld_mod

        class _Spy(_PaintSpyDialogMixin, mld_mod.ModelLoadDialog):
            pass

        seen = {}

        def fake_load(use_cuda=True, callback=None):
            dlg = self.win._model_load_dialog
            seen["visible"] = dlg is not None and dlg.isVisible()
            seen["painted"] = getattr(dlg, "paint_count", 0)
            return None

        with patch.object(self.engine, "load_model", side_effect=fake_load), \
                patch.object(mld_mod, "ModelLoadDialog", _Spy):
            self.win._load_model_and_generate("hello world")
        try:
            self.assertTrue(seen.get("visible"))
            self.assertGreaterEqual(seen.get("painted", 0), 1)
        finally:
            self._cleanup_dialog()

    def test_submission_failure_closes_dialog(self):
        """If the load submission itself raises, the dialog must not be
        left on screen (no stuck modal) and the error must propagate."""
        import ui.panels.model_load_dialog as mld_mod

        def boom(use_cuda=True, callback=None):
            raise RuntimeError("worker pool broken")

        with patch.object(self.engine, "load_model", side_effect=boom):
            with self.assertRaises(RuntimeError):
                self.win._load_model()
        self.assertIsNone(self.win._model_load_dialog,
                          "dialog left dangling after submission failure")
        dlg = self.win._model_load_dialog
        self.assertIsNone(dlg)

    def test_dialog_body_renders_dark_with_content(self):
        """The dialog body must render DARK with visible content under
        the real app theme — never a white/uniform body.

        P3.33 note: the repo now SHIPS the user's animation asset
        (assets/animations/model_load.webp — an opaque white card that
        legitimately covers ~13% of the 400x380 animated dialog). This
        P3.25 regression is about the CLASSIC layout, so it pins the
        no-asset state via an empty animations dir; the animated
        layout's own dark-body contract lives in
        test_p3_32_model_load_animation.py."""
        import ui.panels.model_load_dialog as mld_mod
        from ui.panels.model_load_dialog import ModelLoadDialog

        apply_theme(_app, DEFAULT_THEME)
        empty_dir = tempfile.mkdtemp(prefix="ss_p325_noanim_")
        try:
            with patch.object(mld_mod, "ANIMATIONS_DIR", empty_dir):
                dlg = ModelLoadDialog(None)
                try:
                    dlg.start()
                    pix = dlg.grab()
                    img = pix.toImage().convertToFormat(
                        QImage.Format.Format_RGB32)
                    w, h = img.width(), img.height()
                    total = white = 0
                    colors = set()
                    for y in range(0, h, 4):
                        for x in range(0, w, 4):
                            c = img.pixelColor(x, y)
                            r, g, b = c.red(), c.green(), c.blue()
                            colors.add((r, g, b))
                            if r > 230 and g > 230 and b > 230:
                                white += 1
                            total += 1
                    white_ratio = 100.0 * white / total
                    self.assertLess(white_ratio, 10.0,
                                    "dialog body is %.1f%% white — the "
                                    "blank white window regression"
                                    % white_ratio)
                    self.assertGreater(len(colors), 5,
                                       "dialog render has no visible "
                                       "content (%d distinct colors)"
                                       % len(colors))
                finally:
                    dlg.stop()
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

    def test_dialog_has_explicit_background(self):
        """The dialog must not rely on the app QSS' transparent QDialog
        background — it carries its own dark surface (AssembleDialog
        pattern)."""
        from ui.panels.model_load_dialog import ModelLoadDialog
        from ui.theme import Palette
        dlg = ModelLoadDialog(None)
        try:
            self.assertIn("background-color",
                          dlg.styleSheet(),
                          "dialog has no explicit background stylesheet")
            self.assertIn(Palette.BG_BASE.lower(), dlg.styleSheet().lower())
        finally:
            dlg.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
