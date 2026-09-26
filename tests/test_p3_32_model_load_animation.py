"""
SpeechStudio — P3.32 Model Load Dialog animation tests
=======================================================

Feature (user request: "a modell betöltése részhez egy kis animáció"):
an optional hero animation in ModelLoadDialog.

Runtime contract proven by these tests:

  * The dialog plays ``assets/animations/model_load.webp`` (preferred) or
    ``model_load.gif`` (fallback) — exactly the two animated formats this
    Qt build's QMovie supports (``QMovie.supportedFormats()`` returned
    ``['gif', 'webp']`` on the pinned PySide6 6.11.2 — probed, not
    assumed). No new dependency is introduced.
  * NO usable asset => the dialog is EXACTLY the pre-P3.32 layout:
    400x220, indeterminate progress bar visible, no movie. The P3.25 /
    P3.29 regression contracts are untouched.
  * Usable asset => centered animation label above the title, progress
    bar hidden, dialog 400x380, aspect ratio preserved (8x4 fixture
    scales to 140x70 inside the 140x140 box).
  * P3.33 CENTERING: the hero label sits in the dialog's horizontal
    CENTER (layout-item AlignHCenter). Before the fix the fixed-size
    label was inserted without an alignment and Qt parked it at the
    layout cell origin — the LEFT content margin, ~100px off-center
    (user report: "nem rossz, csak nem középen van").
  * P3.33 LOOP-FOREVER: assets exported with a FINITE loop count
    (NETSCAPE loop=1 => QMovie.loopCount() == 1; the shipped user
    export is loopCount() == 0) play their passes, emit ``finished``
    and QMovie then freezes on the last frame. A loading indicator
    must never go static: the dialog restarts the movie on
    ``finished`` while it is visible, and never restarts it once
    closed. Infinite-loop assets (loopCount -1) never emit finished.
  * The animation starts in start() and its current frame is part of
    the synchronously painted dialog — the P3.25 white-window invariant
    holds WITH the animation: before the caller can submit the load job
    (and the ``import torch`` GIL starvation begins), the dialog is
    already painted with live animation content.
  * Corruption never breaks the dialog: garbage .webp and/or garbage
    .gif files are skipped with a warning and the classic layout wins.

The animated GIF fixture is generated IN-PROCESS: a hand-packed GIF89a
with two frames (no Pillow/ffmpeg dependency — deterministic on the
Windows production target too). The byte layout was validated against
Qt's QMovie (frameCount == 2, both frames jumpable) before these tests
were written. NOTE: QLabel.pixmap() is NOT asserted anywhere — with
setMovie() the label never updates its ``pixmap`` property (it paints
the movie's current frame internally), so the visible proof is done
via QWidget.grab() pixel analysis instead.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_32_model_load_animation.py -v
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

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QMovie
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import ui.panels.model_load_dialog as mld
from ui.panels.model_load_dialog import ModelLoadDialog


# ---------------------------------------------------------------------------
# In-process animated GIF fixture (hand-packed GIF89a, 2 frames)
# ---------------------------------------------------------------------------
def _tiny_animated_gif(width: int = 8, height: int = 4,
                        colors=(((0, 0, 0), (255, 255, 255)))) -> bytes:
    """Pack a minimal 2-frame looping animated GIF.

    Layout: small frames, 2-entry global color table, NETSCAPE2.0
    infinite loop extension, 100 ms per frame. Frame 0 is solid
    ``colors[0]``, frame 1 is solid ``colors[1]``.

    The LZW stream uses the classic "uncompressed GIF" trick: a CLEAR
    code before every literal so the code width never grows past the
    initial 3 bits (min code size 2 => clear=4, EOI=5). This keeps the
    packer ~10 lines and produces a stream every GIF decoder (including
    Qt's qgifhandler) accepts.
    """
    W, H = width, height
    header = b"GIF89a"
    # Logical screen descriptor: GCT flag set, 2-entry GCT.
    lsd = bytes([W & 0xFF, W >> 8, H & 0xFF, H >> 8, 0x80, 0, 0])
    gct = bytes(colors[0]) + bytes(colors[1])
    # NETSCAPE2.0 loop-forever extension.
    netscape = b"\x21\xFF\x0B" + b"NETSCAPE2.0" + b"\x03\x01\x00\x00\x00"

    def frame(pixel: int) -> bytes:
        # Graphic control extension: disposal=1 (do not dispose),
        # 100 ms delay, no transparency. (8 bytes incl. terminator.)
        gce = bytes([0x21, 0xF9, 0x04, 0x04, 0x0A, 0x00, 0x00, 0x00])
        # Image descriptor: full frame at 0,0, no local color table.
        desc = bytes([0x2C, 0, 0, 0, 0,
                      W & 0xFF, W >> 8, H & 0xFF, H >> 8, 0x00])
        codes = []
        for _ in range(W * H):
            codes += [4, pixel]                     # CLEAR, literal
        codes += [5]                                # EOI
        out = bytearray()
        cur = 0
        nbits = 0
        for c in codes:                             # LSB-first packing
            cur |= (c & 0x7) << nbits
            nbits += 3
            while nbits >= 8:
                out.append(cur & 0xFF)
                cur >>= 8
                nbits -= 8
        if nbits:
            out.append(cur & 0xFF)
        data = bytes(out)
        blocks = bytearray([0x02])                  # LZW min code size
        i = 0
        while i < len(data):
            chunk = data[i:i + 255]
            blocks.append(len(chunk))
            blocks += chunk
            i += 255
        blocks.append(0x00)                         # sub-block terminator
        return gce + desc + bytes(blocks)

    return header + lsd + gct + netscape + frame(0) + frame(1) + b"\x3B"


_GARBAGE = b"this is not an animated image file, just garbage bytes 0123456789"

# NETSCAPE2.0 loop-extension bytes inside _tiny_animated_gif(): the two
# bytes after the sub-block id 0x01 are the loop count — 0x0000 loops
# forever (QMovie.loopCount() == -1); patching them to 0x0001 makes a
# FINITE movie (QMovie.loopCount() == 1: one extra pass, then finished
# and QMovie freezes — probed before writing these tests).
_NETSCAPE_INFINITE = b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"
_NETSCAPE_FINITE_1 = b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x01\x00\x00"


def _finite_loop_gif(**kwargs) -> bytes:
    """The suite's 2-frame GIF, but with a FINITE loop count (1).

    2 frames x 100 ms => one pass is ~200 ms, finished fires at
    ~380 ms — fast enough for a deterministic real-time test.
    """
    blob = _tiny_animated_gif(**kwargs)
    patched = blob.replace(_NETSCAPE_INFINITE, _NETSCAPE_FINITE_1)
    assert patched != blob, "NETSCAPE loop patch failed"
    return patched


class _PaintSpyMixin:
    """Counts real paintEvent deliveries on the dialog body."""

    def __init__(self, *args, **kwargs):
        self.paint_count = 0
        super().__init__(*args, **kwargs)

    def paintEvent(self, ev):
        self.paint_count += 1
        super().paintEvent(ev)


class _DialogTestBase(unittest.TestCase):
    """Base: fresh tmpdir animation dir per test, dialog cleanup."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="ss_p332_")
        self._orig_dir = mld.ANIMATIONS_DIR

    def tearDown(self):
        mld.ANIMATIONS_DIR = self._orig_dir
        dlg = getattr(self, "_dlg", None)
        if dlg is not None:
            try:
                dlg.stop()
            except Exception:
                pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, name: str, payload: bytes) -> str:
        path = os.path.join(self._tmp, name)
        with open(path, "wb") as f:
            f.write(payload)
        return path

    def _make_dialog(self) -> ModelLoadDialog:
        self._dlg = ModelLoadDialog(None)
        return self._dlg

    def _anim_dir(self) -> str:
        return self._tmp


class TestAnimationFallback(_DialogTestBase):
    """No usable asset => byte-for-byte the classic layout.

    P3.33: the repo now SHIPS the user's animation asset, so the
    no-asset state is produced by pointing ANIMATIONS_DIR at a temp
    dir (empty / corrupt) — the classic-layout contract itself is
    unchanged and still fully covered below."""

    def test_repo_ships_playable_animation(self):
        """P3.33 premise flip: the repo SHIPS the user's exported
        animation (assets/animations/model_load.webp — the user
        attached it for exactly this purpose). The shipped asset must
        be found and must be PLAYABLE, and an unpatched dialog must
        upgrade to the animated layout. If this fails, the shipped
        asset is missing or corrupt — fix the asset, not the test.
        (The classic-layout fallback is covered by the two tests
        below via patched empty/corrupt dirs.)"""
        found = mld.find_animation_file()
        self.assertIsNotNone(
            found, "the repo no longer ships an animation asset — "
                   "assets/animations/model_load.webp is gone")
        self.assertTrue(found.endswith("model_load.webp"))

        dlg = self._make_dialog()   # unpatched: real repo animations dir
        try:
            self.assertIsNotNone(dlg._movie,
                                 "shipped animation did not activate")
            self.assertTrue(dlg._movie.isValid())
            self.assertGreater(dlg._movie.frameCount(), 1)
            self.assertIsNotNone(dlg._anim_label)
            self.assertTrue(dlg._progress.isHidden())
            self.assertEqual((dlg.width(), dlg.height()),
                             mld.DIALOG_SIZE_WITH_ANIMATION)
        finally:
            dlg.stop()

    def test_empty_animations_dir_keeps_classic_layout(self):
        with patch.object(mld, "ANIMATIONS_DIR", self._anim_dir()):
            dlg = ModelLoadDialog(None)
            self._dlg = dlg
        self.assertIsNone(dlg._movie)
        self.assertFalse(dlg._progress.isHidden())
        self.assertEqual((dlg.width(), dlg.height()), (400, 220))

    def test_corrupt_candidates_fall_back_to_classic_layout(self):
        """Garbage .webp AND garbage .gif => skip both, classic layout."""
        self._write("model_load.webp", _GARBAGE)
        self._write("model_load.gif", _GARBAGE)
        with patch.object(mld, "ANIMATIONS_DIR", self._anim_dir()):
            dlg = ModelLoadDialog(None)
            self._dlg = dlg
        self.assertIsNone(dlg._movie)
        self.assertIsNone(dlg._anim_label)
        self.assertFalse(dlg._progress.isHidden())
        self.assertEqual((dlg.width(), dlg.height()), (400, 220))


class TestAnimationActive(_DialogTestBase):
    """Usable asset => hero animation replaces the busy bar."""

    def test_valid_gif_activates_hero_animation(self):
        self._write("model_load.gif", _tiny_animated_gif())
        with patch.object(mld, "ANIMATIONS_DIR", self._anim_dir()):
            dlg = ModelLoadDialog(None)
            self._dlg = dlg
        self.assertIsNotNone(dlg._movie)
        self.assertTrue(dlg._movie.isValid())
        self.assertEqual(dlg._movie.frameCount(), 2)
        self.assertIsNotNone(dlg._anim_label)
        self.assertFalse(dlg._anim_label.isHidden())
        self.assertTrue(dlg._progress.isHidden(),
                        "busy bar must be hidden when the animation is "
                        "the activity indicator")
        self.assertEqual((dlg.width(), dlg.height()),
                         mld.DIALOG_SIZE_WITH_ANIMATION)

    def test_webp_path_is_preferred_when_both_exist(self):
        """Existence-based preference: with both files present the .webp
        candidate is offered first (playability decided separately)."""
        self._write("model_load.webp", _GARBAGE)
        self._write("model_load.gif", _tiny_animated_gif())
        with patch.object(mld, "ANIMATIONS_DIR", self._anim_dir()):
            found = mld.find_animation_file()
        self.assertIsNotNone(found)
        self.assertTrue(found.endswith("model_load.webp"))

    def test_unplayable_webp_falls_through_to_gif(self):
        """Garbage .webp + valid .gif => the GIF plays (validity-driven
        fallback chain, not existence-only)."""
        self._write("model_load.webp", _GARBAGE)
        self._write("model_load.gif", _tiny_animated_gif())
        with patch.object(mld, "ANIMATIONS_DIR", self._anim_dir()):
            dlg = ModelLoadDialog(None)
            self._dlg = dlg
        self.assertIsNotNone(dlg._movie)
        self.assertTrue(dlg._movie.isValid())
        self.assertEqual(dlg._movie.frameCount(), 2)

    def test_scaled_size_preserves_aspect_ratio(self):
        """An 8x4 fixture scales to 140x70 inside the 140x140 box
        (aspect preserved, no distortion)."""
        self._write("model_load.gif", _tiny_animated_gif(width=8, height=4))
        with patch.object(mld, "ANIMATIONS_DIR", self._anim_dir()):
            dlg = ModelLoadDialog(None)
            self._dlg = dlg
        self.assertEqual(dlg._movie.scaledSize(), QSize(140, 70))
        self.assertEqual(dlg._anim_label.size(), QSize(140, 140))

    def test_animation_label_is_horizontally_centered(self):
        """P3.33 regression (user report: the animation is not in the
        center): the hero label must sit in the dialog's horizontal
        CENTER — center of label geometry == center of the dialog,
        within 1 px. Before the fix the fixed-size label was inserted
        into the QVBoxLayout WITHOUT a layout-item alignment, so Qt
        parked it at the cell origin: the LEFT content margin (center
        x = 94 on the 400 px dialog instead of 200 — proven by pixel
        analysis of the user's screenshot). The QLabel's own alignment
        only centers the movie pixmap INSIDE the label; it does not
        move the label itself."""
        self._write("model_load.gif", _tiny_animated_gif())
        with patch.object(mld, "ANIMATIONS_DIR", self._anim_dir()):
            dlg = ModelLoadDialog(None)
            self._dlg = dlg
            dlg.start()      # show + processEvents => layout computed
        try:
            g = dlg._anim_label.geometry()
            center_x = g.left() + g.width() / 2.0
            dialog_center = dlg.width() / 2.0
            self.assertLessEqual(
                abs(center_x - dialog_center), 1.0,
                "animation label center x=%.1f but dialog center x=%.1f "
                "— hero animation is off-center (P3.33 regression)"
                % (center_x, dialog_center))
            # The label must not be at the left content margin either
            # (the pre-fix position: left == 24).
            self.assertGreater(
                g.left(), mld.ANIMATION_MAX_SIZE.width() // 2,
                "animation label still hugs the left margin")
        finally:
            dlg.stop()

    def test_finite_loop_asset_restarts_forever(self):
        """P3.33 LOOP-FOREVER (end-to-end, real time): a FINITE-loop
        asset (NETSCAPE loop=1 => QMovie.loopCount() == 1) fires
        ``finished`` after its last pass — without the fix QMovie then
        freezes on the final frame. A loading indicator must never go
        static: the dialog restarts the movie, so it is RUNNING again
        after ``finished``. (The shipped user export is exactly this
        class of asset: loopCount() == 0, plays once, then froze.)"""
        self._write("model_load.gif", _finite_loop_gif())
        with patch.object(mld, "ANIMATIONS_DIR", self._anim_dir()):
            dlg = ModelLoadDialog(None)
            self._dlg = dlg
            self.assertEqual(
                dlg._movie.loopCount(), 1,
                "fixture must be a finite-loop movie (NETSCAPE loop=1)")
            finished = []
            dlg._movie.finished.connect(lambda: finished.append(True))
            dlg.start()
        try:
            # 2 frames x 100 ms x 2 passes => finished at ~380 ms;
            # bounded wait so a broken build fails instead of hanging.
            deadline = time.monotonic() + 3.0
            while not finished and time.monotonic() < deadline:
                QApplication.processEvents()
            self.assertTrue(
                finished,
                "finite-loop fixture never emitted finished — the test "
                "fixture itself is broken")
            # Direct connection: the dialog's restart slot ran BEFORE
            # the counter append above, so the movie must already be
            # running again; allow a short grace spin anyway.
            grace = time.monotonic() + 0.5
            while (dlg._movie.state() != QMovie.MovieState.Running
                    and time.monotonic() < grace):
                QApplication.processEvents()
            self.assertEqual(
                dlg._movie.state(), QMovie.MovieState.Running,
                "movie froze after its finite loop — the loading "
                "animation would go static (P3.33 regression)")
        finally:
            dlg.stop()

    def test_finished_restarts_movie_but_not_on_closed_dialog(self):
        """P3.33 LOOP-FOREVER (deterministic wiring): emitting
        ``finished`` restarts a visible movie (even one that already
        stopped), but NEVER restarts a movie whose dialog has been
        closed by stop() — no zombie movies on dead dialogs."""
        self._write("model_load.gif", _finite_loop_gif())
        try:
            with patch.object(mld, "ANIMATIONS_DIR", self._anim_dir()):
                dlg = ModelLoadDialog(None)
                self._dlg = dlg
                dlg.show()
                QApplication.processEvents()

                # stopped movie + finished (the real end-of-pass state)
                dlg._movie.start()
                dlg._movie.stop()
                self.assertEqual(dlg._movie.state(),
                                 QMovie.MovieState.NotRunning)
                dlg._movie.finished.emit()
                QApplication.processEvents()
                self.assertEqual(
                    dlg._movie.state(), QMovie.MovieState.Running,
                    "finished did not restart the movie on a visible "
                    "dialog")

                # closed dialog: stop() accepts (hides) the dialog — a
                # late/queued finished must NOT resurrect the movie.
                dlg.stop()
                self.assertFalse(dlg.isVisible())
                dlg._movie.finished.emit()
                QApplication.processEvents()
                self.assertEqual(
                    dlg._movie.state(), QMovie.MovieState.NotRunning,
                    "movie restarted on a CLOSED dialog — zombie movie")
        finally:
            try:
                dlg.stop()
            except Exception:
                pass

    def test_start_paints_current_frame_synchronously(self):
        """P3.25 invariant WITH the animation: after start() returns —
        with ZERO test-side event-loop processing — the dialog is
        visible, painted (>=1 paintEvent) and the movie's CURRENT frame
        is VISIBLY rendered in the label area. Proof is pixel-based
        (grab()): QLabel paints the movie's current frame internally —
        label.pixmap() stays null with setMovie() and cannot be
        asserted. (start() flushes events itself, so the movie may
        already have ticked past frame 0 — the contract is "the
        animation is live on screen", not "frame specifically 0".)
        The movie is Running; stop() stops it."""
        red, green = (255, 0, 0), (0, 255, 0)
        self._write("model_load.gif", _tiny_animated_gif(colors=(red, green)))

        class _Spy(_PaintSpyMixin, ModelLoadDialog):
            pass

        with patch.object(mld, "ANIMATIONS_DIR", self._anim_dir()):
            dlg = _Spy(None)
            self._dlg = dlg
            dlg.start()
        try:
            self.assertTrue(dlg.isVisible())
            self.assertGreaterEqual(
                dlg.paint_count, 1,
                "dialog not painted before the load job could be "
                "submitted — the white-window regression")
            pm = dlg._movie.currentPixmap()
            self.assertFalse(pm.isNull(),
                             "animation frame must be decoded "
                             "synchronously in start()")
            self.assertEqual((pm.width(), pm.height()), (140, 70))

            # Visible proof: grab() renders synchronously — the CURRENT
            # frame's color must dominate the label area (the 140x70
            # movie covers ~half of the 140x140 label box; at 2px
            # sampling that is ~2450 samples, threshold 1200).
            frame = dlg._movie.currentFrameNumber()
            expected = red if frame == 0 else green
            exp_bucket = tuple(v // 32 * 32 for v in expected)
            img = dlg.grab().toImage().convertToFormat(
                QImage.Format.Format_RGB32)
            g = dlg._anim_label.geometry()
            hits = 0
            for y in range(g.top(), g.bottom(), 2):
                for x in range(g.left(), g.right(), 2):
                    c = img.pixelColor(x, y)
                    if (c.red() // 32 * 32, c.green() // 32 * 32,
                            c.blue() // 32 * 32) == exp_bucket:
                        hits += 1
            self.assertGreater(
                hits, 1200,
                "current animation frame (frame %d, color %s) is not "
                "visibly rendered after start() (%d/%d sampled pixels)"
                % (frame, expected, hits,
                   (g.width() // 2) * (g.height() // 2)))
            self.assertEqual(dlg._movie.state(),
                             QMovie.MovieState.Running)
        finally:
            dlg.stop()
        self.assertEqual(dlg._movie.state(), QMovie.MovieState.NotRunning)

    def test_dialog_body_renders_dark_with_animation(self):
        """The dialog body must render DARK with the animation active —
        never a white/uniform body (P3.25 class of regression)."""
        from ui.theme import apply_theme, DEFAULT_THEME
        apply_theme(_app, DEFAULT_THEME)
        self._write("model_load.gif", _tiny_animated_gif())
        with patch.object(mld, "ANIMATIONS_DIR", self._anim_dir()):
            dlg = ModelLoadDialog(None)
            self._dlg = dlg
            dlg.start()
            try:
                pix = dlg.grab()
                img = pix.toImage().convertToFormat(
                    QImage.Format.Format_RGB32)
                w, h = img.width(), img.height()
                total = white = 0
                for y in range(0, h, 4):
                    for x in range(0, w, 4):
                        c = img.pixelColor(x, y)
                        if (c.red() > 230 and c.green() > 230
                                and c.blue() > 230):
                            white += 1
                        total += 1
                self.assertLess(
                    100.0 * white / total, 10.0,
                    "dialog body is %.1f%% white with the animation"
                    % (100.0 * white / total))
            finally:
                dlg.stop()


class _MainWindowHarness(unittest.TestCase):
    """Real Engine + real MainWindow in an isolated tmpdir (P3.25
    harness pattern) to prove the animated dialog on the real load path."""

    @classmethod
    def setUpClass(cls):
        from engine.engine import Engine
        from ui.main_window import MainWindow
        cls.tmpdir = tempfile.mkdtemp(prefix="ss_p332_win_")
        cls.animdir = tempfile.mkdtemp(prefix="ss_p332_anim_")
        with open(os.path.join(cls.animdir, "model_load.gif"), "wb") as f:
            f.write(_tiny_animated_gif())
        cls.engine = Engine(app_root=cls.tmpdir)
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
        shutil.rmtree(cls.animdir, ignore_errors=True)


class TestMainWindowLoadPath(_MainWindowHarness):

    def test_load_model_shows_animated_dialog(self):
        """RUNTIME: MainWindow._load_model creates the dialog with the
        animation ACTIVE when the asset is present — the movie is valid
        and the classic busy bar is hidden."""
        seen = {}

        def fake_load(use_cuda=True, callback=None):
            dlg = self.win._model_load_dialog
            seen["movie"] = getattr(dlg, "_movie", "MISSING")
            seen["progress_hidden"] = getattr(
                dlg, "_progress", None) is not None and \
                dlg._progress.isHidden()
            return None

        with patch.object(self.engine, "load_model",
                          side_effect=fake_load), \
                patch.object(mld, "ANIMATIONS_DIR", self.animdir):
            self.win._load_model()
        try:
            self.assertNotEqual(seen.get("movie"), "MISSING",
                                "load path did not create the dialog")
            self.assertIsNotNone(seen.get("movie"),
                                 "dialog created without the animation "
                                 "although the asset was present")
            self.assertTrue(seen["movie"].isValid())
            self.assertTrue(seen.get("progress_hidden"))
        finally:
            dlg = self.win._model_load_dialog
            if dlg is not None:
                dlg.stop()
                self.win._model_load_dialog = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
