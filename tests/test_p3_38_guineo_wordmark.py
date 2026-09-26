"""
SpeechStudio — P3.38 GUINEO wordmark integration tests
=======================================================

The official GUINEO logo files supplied by the user
(GUINEO_LOGO_TEXT_nobg_320_107P.png / GUINEO_LOGO_TEXT_nobg_720_240P.png —
white wordmark + swoosh on a transparent background) are integrated into
the application brand slot — the top-left corner where the former
SpeechStudio brand (graphic_eq icon + text label) lived.

Covers:
  1. ASSETS — both official files ship byte-untouched (SHA-256 pinned);
     the derived display wordmark is trimmed, transparent and white.
  2. BRAND SLOT — a real TopNavigation shows the wordmark pixmap with
     the documented logical height, 2x render DPR (HiDPI crispness),
     tooltip + accessible name.
  3. VISIBILITY — pixel proof: the wordmark renders as light ink on the
     dark bar surface in ALL FIVE themes (the top navigation keeps its
     fixed dark surface in every theme, so the white logo is always the
     correct variant).
  4. FALLBACK — a missing/undecodable asset falls back to the text
     brand "GUINEO"; the bar is never left empty.
  5. NO REGRESSION — the app icon (square emblem) and the model-load
     animation remain untouched; no SpeechStudio label remains.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest \\
        tests/test_p3_38_guineo_wordmark.py -v
"""
import os
import sys
import types
import hashlib
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

from PySide6.QtWidgets import QApplication, QLabel, QMainWindow  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRAND_DIR = os.path.join(_ROOT, "assets", "brand")

OFFICIAL_320 = os.path.join(_BRAND_DIR, "guineo_logo_text_320.png")
OFFICIAL_720 = os.path.join(_BRAND_DIR, "guineo_logo_text_720.png")
WORDMARK = os.path.join(_BRAND_DIR, "guineo_wordmark.png")
EMBLEM = os.path.join(_BRAND_DIR, "guineo_logo.png")
ANIMATION = os.path.join(_ROOT, "assets", "animations", "model_load.webp")

# SHA-256 of the user-supplied official files (byte-untouched in-repo).
_SHA_OFFICIAL_320 = "936c4102a8be0fb40ca7506ab11fc932b47f3fc9c3f803c4caeff5959962927c"
_SHA_OFFICIAL_720 = "8a0fa9d0189905e3bf486f6d8759c9d5efd87c34f45bb8103d7b7ae807d833cc"
_SHA_EMBLEM = "4bf0aef5e584798a1e17db670338d4ddc41673639fcf6a49a9a0b869bb2774fb"
_SHA_ANIMATION = "b09f7b721b4c20853f2fa2fa8edb60164478fda273a0a878708b3f46220f0e07"


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class TestOfficialLogoAssets(unittest.TestCase):

    def test_official_files_ship_untouched(self):
        self.assertTrue(os.path.isfile(OFFICIAL_320))
        self.assertTrue(os.path.isfile(OFFICIAL_720))
        self.assertEqual(_sha256(OFFICIAL_320), _SHA_OFFICIAL_320)
        self.assertEqual(_sha256(OFFICIAL_720), _SHA_OFFICIAL_720)

    def test_official_dimensions(self):
        from PySide6.QtGui import QImage
        for path, w, h in ((OFFICIAL_320, 320, 107), (OFFICIAL_720, 720, 240)):
            img = QImage(path)
            self.assertFalse(img.isNull(), path)
            self.assertEqual((img.width(), img.height()), (w, h), path)

    def test_display_wordmark_is_trimmed_white_on_transparent(self):
        """The derived display asset keeps the official white ink and a
        transparent background, with only a thin padding margin."""
        from PIL import Image
        import numpy as np
        img = Image.open(WORDMARK).convert("RGBA")
        a = np.array(img)
        alpha = a[..., 3]
        # Mostly transparent, with real content.
        self.assertLess((alpha > 8).mean(), 0.5, "wordmark must be transparent")
        self.assertGreater((alpha > 8).mean(), 0.1, "wordmark must have content")
        # The ink is white (matches the official asset; a small
        # fraction of anti-aliased edge pixels may be slightly darker).
        opaque = a[alpha > 200]
        self.assertGreater(len(opaque), 100)
        self.assertTrue((opaque[:, :3].mean(axis=0) > 245).all(),
                        "the wordmark ink must stay white")
        frac_soft = (opaque[:, :3] < 230).any(axis=1).mean()
        self.assertLess(frac_soft, 0.02,
                        "only anti-aliased edges may be below white")
        # Trimmed: content starts within the 2px padding.
        ys, xs = np.where(alpha > 8)
        self.assertLessEqual(xs.min(), 2)
        self.assertLessEqual(ys.min(), 2)
        # Same aspect as the official 720 content.
        aspect = img.width / img.height
        self.assertAlmostEqual(aspect, 666 / 176, delta=0.05)


class TestBrandSlot(unittest.TestCase):

    def _make_nav(self):
        from ui.panels.menu_bar import MenuBar
        from ui.panels.toolbar import Toolbar
        from ui.panels.top_navigation import TopNavigation
        host = QMainWindow()
        nav = TopNavigation(MenuBar(host), Toolbar(host))
        nav.resize(1200, 56)
        return host, nav

    def test_brand_slot_shows_wordmark(self):
        from ui.panels.top_navigation import (
            BRAND_LOGO_HEIGHT, BRAND_LOGO_RENDER_DPR,
        )
        host, nav = self._make_nav()
        try:
            brand = nav.brand_logo_label()
            pm = brand.pixmap()
            self.assertFalse(pm.isNull(), "wordmark pixmap must be set")
            # Logical display size: 26px high, wide enough for the wordmark.
            # (QLabel.pixmap() reports the LOGICAL size in this Qt version;
            # the size hint carries the same logical geometry.)
            hint = brand.sizeHint()
            self.assertEqual(hint.height(), BRAND_LOGO_HEIGHT)
            self.assertGreater(hint.width(), 60)
            self.assertLess(hint.width(), 160)
            self.assertEqual(pm.height(), BRAND_LOGO_HEIGHT)
            # The pixmap handed to the label is rendered at 2x PHYSICAL
            # pixels and tagged with the 2.0 device pixel ratio, so the
            # wordmark stays crisp on HiDPI displays.
            captured = {}
            orig_set_pixmap = QLabel.setPixmap

            def _spy(lbl_self, pixmap):
                captured["pm"] = pixmap
                orig_set_pixmap(lbl_self, pixmap)

            with unittest.mock.patch.object(QLabel, "setPixmap", _spy):
                nav._apply_brand_logo()
            hi = captured["pm"]
            self.assertEqual(hi.devicePixelRatio(), BRAND_LOGO_RENDER_DPR)
            self.assertEqual(hi.height(),
                             int(round(BRAND_LOGO_HEIGHT * BRAND_LOGO_RENDER_DPR)))
            # Accessibility + tooltip.
            self.assertEqual(brand.accessibleName(), "GUINEO")
            self.assertEqual(brand.toolTip(), "GUINEO")
        finally:
            nav.deleteLater()
            host.deleteLater()

    def test_wordmark_visible_on_bar_in_every_theme(self):
        """Pixel proof: light logo ink renders on the dark bar surface
        under ALL five themes (the top nav keeps its fixed dark
        background, so the white wordmark is always correct)."""
        from ui.theme import THEMES, apply_theme
        for theme in THEMES:
            apply_theme(APP, theme)
            host, nav = self._make_nav()
            try:
                img = nav.grab().toImage()
                # Bar surface is dark in every theme — sampled on the
                # top strip (y=3), safely above the centred content.
                for x in (300, 600, 900):
                    self.assertLess(img.pixelColor(x, 3).lightness(), 60,
                                    "bar surface must stay dark (%s @%d)"
                                    % (theme, x))
                # … and the brand area carries light logo ink.
                light = 0
                for y in range(10, 46):
                    for x in range(24, 150):
                        if img.pixelColor(x, y).lightness() > 150:
                            light += 1
                self.assertGreater(
                    light, 300,
                    "wordmark ink not visible in theme %s (%d px)" % (theme, light))
            finally:
                nav.deleteLater()
                host.deleteLater()

    def test_no_speechstudio_or_icon_label_in_brand_slot(self):
        host, nav = self._make_nav()
        try:
            texts = [l.text() for l in nav.findChildren(QLabel)
                     if l.text().strip()]
            self.assertNotIn("SpeechStudio", texts)
            # The brand slot is the pixmap label — no fallback text set.
            self.assertEqual(nav.brand_logo_label().text(), "")
        finally:
            nav.deleteLater()
            host.deleteLater()

    def test_missing_asset_falls_back_to_text_brand(self):
        """With the wordmark asset unavailable the brand slot falls back
        to the text brand — never an empty bar."""
        from ui.panels.top_navigation import TopNavigation
        host, nav = self._make_nav()
        try:
            import ui.panels.top_navigation as tn
            with unittest.mock.patch.object(tn, "BRAND_LOGO_PATH",
                                            "/nonexistent/logo.png"):
                nav._apply_brand_logo()
            brand = nav.brand_logo_label()
            self.assertEqual(brand.text(), "GUINEO")
            self.assertTrue(brand.pixmap().isNull())
        finally:
            nav.deleteLater()
            host.deleteLater()


import unittest.mock  # noqa: E402  (used by the fallback + DPR tests)


class TestNoRegression(unittest.TestCase):

    def test_app_icon_emblem_untouched(self):
        self.assertTrue(os.path.isfile(EMBLEM))
        self.assertEqual(_sha256(EMBLEM), _SHA_EMBLEM)

    def test_model_load_animation_untouched(self):
        self.assertEqual(_sha256(ANIMATION), _SHA_ANIMATION)

    def test_source_pinned_wordmark_path(self):
        src = open(os.path.join(_ROOT, "ui", "panels", "top_navigation.py"),
                   encoding="utf-8").read()
        self.assertIn("guineo_wordmark.png", src)
        self.assertIn("BRAND_LOGO_HEIGHT = 26", src)
        self.assertIn("BRAND_LOGO_RENDER_DPR = 2.0", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
