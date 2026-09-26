"""
SpeechStudio — P3.37 GUINEO Branding + Documentation validation tests
======================================================================

Validation for the GUINEO rebrand round:

  A. DOCUMENTATION (file-level, §54 of the brief; revised in the
     P3.44.5 docs round: README.md is now British-English only and the
     Hungarian documentation lives in the separate README_HU.md):
     - README.md exists (EN | British English) and README_HU.md exists
       (HU | Magyar); the two describe the same current functionality
       and cross-link each other.
     - GUINEO is the visible product identity; obsolete user-facing
       SpeechStudio branding is gone from user docs (historical /
       technical mentions — SpeechStudio.py, SpeechStudio_clean.zip,
       "formerly known as" — are the deliberate exceptions).
     - LICENSE_HIGGS_TTS_3.txt exists and is the VERBATIM official
       Boson Higgs TTS 3 Research and Non-Commercial License (byte
       equal to the archived upstream copy in research/).
     - README links to the Higgs licence; THIRD_PARTY_LICENSES.md
       carries Boson AI + bundled-component attribution.
     - The support section appears at the END of BOTH language
       sections with the supplied Ko-fi links preserved verbatim.
     - Logo references are valid; no broken internal anchor links;
       no broken relative file links.
     - README_START_HERE.txt is rebranded AND current (5 themes,
       Merge Completed Parts, Combine Scene — no stale claims).
     - British English spelling in the EN section; no fake release
       version (v1.0.0 + per-launch build number, as implemented).

  B. BRANDING RUNTIME (real QApplication / real widgets):
     - APP_NAME == "GUINEO" everywhere it is user-visible
       (engine.version, ui.main_window, debug info, startup header).
     - A real MainWindow's windowTitle() is "GUINEO"; the top
       navigation brand label reads "GUINEO"; the first-run demo
       text mentions GUINEO.
     - The application icon asset exists and decodes.

  C. SAFETY OF THE REBRAND:
     - The model-load ANIMATION is untouched (SHA-256 pinned).
     - Internal SpeechStudio identifiers are NOT globally renamed
       (entry-point file name, project/scene format strings, QSS
       objectNames, log namespaces).
     - launch/update scripts show GUINEO to users but keep the
       technical SpeechStudio_clean.zip / SpeechStudio.py contract.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest \\
        tests/test_p3_37_branding_documentation.py -v
"""
import os
import re
import sys
import types
import hashlib
import unittest
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Fake torch (house pattern: test_p3_27b / test_p3_28 / test_p3_35).
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

README_PATH = os.path.join(_ROOT, "README.md")
README_HU_PATH = os.path.join(_ROOT, "README_HU.md")
START_HERE_PATH = os.path.join(_ROOT, "README_START_HERE.txt")
HIGGS_LICENSE_PATH = os.path.join(_ROOT, "LICENSE_HIGGS_TTS_3.txt")
THIRD_PARTY_PATH = os.path.join(_ROOT, "THIRD_PARTY_LICENSES.md")
LOGO_PATH = os.path.join(_ROOT, "assets", "brand", "guineo_logo.png")
ANIMATION_PATH = os.path.join(_ROOT, "assets", "animations", "model_load.webp")

# SHA-256 of the shipped model-load animation (P3.32/P3.33 asset) — the
# rebrand must leave this file byte-identical ("Model-loading animation
# remains untouched").
_ANIMATION_SHA256 = "b09f7b721b4c20853f2fa2fa8edb60164478fda273a0a878708b3f46220f0e07"

KOFI_URL = "https://ko-fi.com/thomashoysgameaudio"
KOFI_BADGE_URL = ("https://camo.githubusercontent.com/12ddacd4b1ffd5473ce1023840877"
                  "61260706d10e6ee9d4c4d4bcf84c9608dcc/68747470733a2f2f696d672e7368"
                  "69656c64732e696f2f62616467652f537570706f72745f6f6e5f4b6f2d2d6669"
                  "2d4631363036313f7374796c653d666f722d7468652d6261646765266c6f67"
                  "6f3d6b6f2d6669266c6f676f436f6c6f723d7768697465")
KOFI_BUTTON_URL = ("https://camo.githubusercontent.com/201ef269611db7eb6b5d08e9f756a"
                   "b8980df3014b64492770bdf13a6ed924641/68747470733a2f2f6b6f2d6669"
                   "2e636f6d2f696d672f676974687562627574746f6e5f736d2e737667")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _github_slug(heading):
    """Approximate the GitHub heading anchor algorithm."""
    s = heading.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)  # strip punctuation
    s = re.sub(r"\s", "-", s)
    return s


# ---------------------------------------------------------------------------
# A. Documentation validation
# ---------------------------------------------------------------------------
class TestDocumentation(unittest.TestCase):

    def test_readme_exists(self):
        self.assertTrue(os.path.isfile(README_PATH), "README.md must exist")

    def test_readme_hu_exists_and_has_hungarian_section(self):
        self.assertTrue(os.path.isfile(README_HU_PATH),
                        "README_HU.md must exist (Hungarian user guide)")
        text = _read(README_HU_PATH)
        self.assertIn("# HU | Magyar", text)
        self.assertIn('<a id="magyar"></a>', text)

    def test_readme_has_british_english_section(self):
        text = _read(README_PATH)
        self.assertIn("# EN | British English", text)
        self.assertIn('<a id="english"></a>', text)

    def test_readme_cross_links_language_files_and_repository(self):
        """P3.44.5 docs round: the two language files cross-link each
        other and both point at the canonical GitHub repository."""
        en, hu = _read(README_PATH), _read(README_HU_PATH)
        self.assertIn("](README_HU.md)", en,
                      "README.md must link the Hungarian documentation")
        self.assertIn("](README.md)", hu,
                      "README_HU.md must link the English documentation")
        for text in (en, hu):
            self.assertIn("https://github.com/AGE-T/GUINEO", text,
                          "both files reference the GitHub repository")

    def test_both_language_sections_describe_same_functionality(self):
        """The two versions describe the SAME current application."""
        hu, en = _read(README_HU_PATH), _read(README_PATH)
        # Core concepts that must appear in BOTH language sections.
        for concept in (
            "Combine Scene", "Batch Queue", "STALE", "REVIEW REQUIRED",
            "Higgs TTS 3", "Boson AI", "Ko-fi", "Ctrl+Enter",
            "v01", "Voice Profile", "Narration Blocks", "Project Assembly",
            "Batch Audio Export", "Ctrl+Shift+A",
        ):
            self.assertIn(concept, hu, "missing from HU section: %r" % concept)
            self.assertIn(concept, en, "missing from EN section: %r" % concept)
        # The same audio-format facts in both.
        self.assertIn("24 000 Hz", hu)
        self.assertIn("24,000 Hz", en)

    def test_guineo_is_the_visible_product_identity(self):
        combined = _read(README_PATH) + _read(README_HU_PATH)
        self.assertGreaterEqual(combined.count("GUINEO"), 30,
                                "GUINEO must dominate the README branding")

    def test_no_stale_user_facing_speechstudio_branding_in_readme(self):
        """SpeechStudio may appear ONLY as file names or the historical
        'formerly known as' note — never as the current product name."""
        for path in (README_PATH, README_HU_PATH):
            text = _read(path)
            stripped = text
            for allowed in (
                "SpeechStudio_clean.zip",          # update process contract
                "SpeechStudio.py",                 # entry-point file name
                "development name SpeechStudio",   # EN historical note
                "development name *SpeechStudio*", # EN historical note (emphasis)
                "fejlesztői nevén SpeechStudio",   # HU historical note
                "fejlesztői nevén *SpeechStudio*", # HU historical note (emphasis)
            ):
                stripped = stripped.replace(allowed, "")
            self.assertNotIn("SpeechStudio", stripped,
                             "stale user-facing SpeechStudio branding in %s"
                             % os.path.basename(path))

    def test_readme_links_to_higgs_license(self):
        for path in (README_PATH, README_HU_PATH):
            text = _read(path)
            self.assertIn("[`LICENSE_HIGGS_TTS_3.txt`](LICENSE_HIGGS_TTS_3.txt)", text)
            self.assertIn("[`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md)", text)

    def test_support_section_at_end_of_hungarian_section(self):
        hu = _read(README_HU_PATH)
        support_pos = hu.rindex("## ☕ Támogatás / Support")
        # No heading of any level may follow the support heading within
        # the HU section ("keep it at the end").
        rest = hu[support_pos + len("## ☕ Támogatás / Support"):]
        self.assertIsNone(re.search(r"^#{1,6}\s", rest, re.MULTILINE),
                         "documentation placed after the HU support section")
        # And it must come after the last technical section.
        self.assertGreater(support_pos, hu.rindex("## Hibaelhárítás"))

    def test_support_section_at_end_of_english_section(self):
        text = _read(README_PATH)
        support_pos = text.rindex("## ☕ Support")
        rest = text[support_pos + len("## ☕ Support"):]
        self.assertIsNone(re.search(r"^#{1,6}\s", rest, re.MULTILINE),
                         "documentation placed after the EN support section")
        # The EN support section follows the last technical section …
        self.assertGreater(support_pos, text.rindex("## Troubleshooting"))
        # … and the document ends with the Ko-fi URL.
        self.assertTrue(text.rstrip().endswith(KOFI_URL))

    def test_kofi_links_preserved(self):
        """All supplied Ko-fi links are preserved verbatim, in BOTH
        language files."""
        for url in (KOFI_URL, KOFI_BADGE_URL, KOFI_BUTTON_URL):
            for path in (README_PATH, README_HU_PATH):
                self.assertIn(url, _read(path),
                              "Ko-fi link missing from %s: %s"
                              % (os.path.basename(path), url))

    def test_logo_reference_and_asset_valid(self):
        for path in (README_PATH, README_HU_PATH):
            self.assertIn('src="assets/brand/guineo_logo.png"', _read(path))
        self.assertTrue(os.path.isfile(LOGO_PATH))
        from PySide6.QtGui import QImage
        img = QImage(LOGO_PATH)
        self.assertFalse(img.isNull(), "logo must decode")
        self.assertEqual(img.width(), 140)
        self.assertEqual(img.height(), 140)

    def test_no_broken_internal_anchor_links(self):
        for path in (README_PATH, README_HU_PATH):
            text = _read(path)
            explicit = set(re.findall(r'<a id="([^"]+)"></a>', text))
            heading_slugs = set()
            for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE):
                heading_slugs.add(_github_slug(m.group(1)))
            links = set(re.findall(r"\]\(#([^)]+)\)", text))
            self.assertTrue(links)
            broken = sorted(l for l in links
                            if l not in explicit and l not in heading_slugs)
            self.assertEqual(broken, [],
                             "broken internal anchor links in %s"
                             % os.path.basename(path))

    def test_no_broken_relative_file_links(self):
        for path in (README_PATH, README_HU_PATH):
            text = _read(path)
            rel = set(re.findall(r"\]\((?!#|https?://|mailto:)([^)]+)\)", text))
            broken = sorted(r for r in rel
                            if not os.path.isfile(os.path.join(_ROOT, r)))
            self.assertEqual(broken, [],
                             "broken relative file links in %s"
                             % os.path.basename(path))

    def test_higgs_license_file_is_verbatim_official(self):
        self.assertTrue(os.path.isfile(HIGGS_LICENSE_PATH))
        text = _read(HIGGS_LICENSE_PATH)
        self.assertTrue(text.startswith(
            "BOSON HIGGS TTS 3 RESEARCH AND NON-COMMERCIAL LICENSE AGREEMENT"),
            "official license title")
        self.assertIn("Last Updated: July 8, 2026", text)
        self.assertIn("Boson AI USA, Inc.", text)
        self.assertIn("II-A. CREATOR USE GRANT", text)
        self.assertIn("contact@boson.ai", text)
        # Byte-identical to the archived upstream copy in research/.
        archived = os.path.join(_ROOT, "research",
                                "LICENSE_bosonai_higgs-tts-3-4b_UPSTREAM.txt")
        self.assertEqual(_read_bytes(HIGGS_LICENSE_PATH),
                         _read_bytes(archived),
                         "LICENSE_HIGGS_TTS_3.txt must be the verbatim "
                         "official text")

    def test_third_party_licenses_attribution(self):
        self.assertTrue(os.path.isfile(THIRD_PARTY_PATH))
        text = _read(THIRD_PARTY_PATH)
        for needle in (
            "Boson AI", "Higgs TTS 3",
            "Boson Higgs TTS 3 is licensed under the Boson Higgs TTS 3 "
            "Research and Non-Commercial License",
            "PySide6", "PyTorch", "Transformers", "NumPy",
            "Inter", "Libre Franklin", "JetBrains Mono",
            "FFmpeg is not distributed with",
            "https://huggingface.co/bosonai/higgs-tts-3-4b",
            "https://boson.ai",
        ):
            self.assertIn(needle, text)

    def test_readme_start_here_rebranded_and_current(self):
        self.assertTrue(os.path.isfile(START_HERE_PATH))
        text = _read(START_HERE_PATH)
        self.assertIn("GUINEO - Quick Start Guide", text)
        self.assertIn("README.md", text)  # points at the full guide
        self.assertIn("README_HU.md", text)  # and at the Hungarian guide
        # Current facts (the old file claimed 4 themes / Dark default /
        # "Concatenate" / auto-concatenation advice).
        self.assertIn("Themes (5)", text)
        self.assertIn("Modern Dark (default)", text)
        self.assertIn("Merge Completed Parts", text)
        self.assertIn("Combine Scene", text)
        self.assertNotIn("Themes (4)", text)
        # The obsolete list was "Dark (default), Light, Synthwave, Retro
        # Console" — the current list names Modern Dark first.
        self.assertNotIn("- Dark (default), Light, Synthwave", text)
        self.assertNotIn("100ms crossfade", text)
        self.assertNotIn("auto-concatenation", text)
        # The update contract keeps its technical file names.
        self.assertIn("SpeechStudio_clean.zip", text)
        self.assertIn("SpeechStudio.py", text)
        self.assertIn(KOFI_URL, text)

    def test_british_english_spelling_in_en_section(self):
        en = _read(README_PATH)  # the file is English-only since P3.44.5
        for british in ("licence", "normalisation", "organises",
                        "recognised"):
            self.assertIn(british, en,
                          "British spelling missing: %r" % british)
        for american in (r"\bcolor\b", r"\bbehavior\b", r"\borganization\b",
                         r"\bnormalization\b", r"\borganized\b",
                         r"\blicense agreement\b"):
            self.assertIsNone(re.search(american, en),
                              "American spelling found: %r" % american)

    def test_version_documented_without_fake_release(self):
        text = _read(README_PATH)
        self.assertIn("v1.0.0", text)
        self.assertIn("build", text.lower())
        # The build number is the real per-launch mechanism.
        self.assertIn("settings\\build.json", text)

    def test_readme_documents_required_topics(self):
        """§29 coverage spot-check: every mandated topic is present in
        both language files."""
        hu, en = _read(README_HU_PATH), _read(README_PATH)
        topics_hu = ("Plain Text", "Narration Blocks", "Preview", "FRIENDLY",
                     "ADVANCED", "STALE", "REVIEW REQUIRED", "Project Assembly",
                     "Batch Audio Export", "Hangprofilok", "Szereplők",
                     "Előzmények", "Recents", "Teljes képernyő", "Anomáliadetektálás",
                     "Hardverkövetelmények", "Hibaelhárítás", "Billentyűparancsok",
                     "Menük", "licencelés", "102 nyelv")
        topics_en = ("Plain Text", "Narration Blocks", "Preview", "FRIENDLY",
                     "ADVANCED", "STALE", "REVIEW REQUIRED", "Project Assembly",
                     "Batch Audio Export", "Voice Profiles", "Characters",
                     "History", "Recents", "Fullscreen", "Anomaly detection",
                     "Hardware requirements", "Troubleshooting", "Keyboard shortcuts",
                     "Menus", "Licensing", "102 languages")
        for t in topics_hu:
            self.assertIn(t, hu, "HU file missing topic: %r" % t)
        for t in topics_en:
            self.assertIn(t, en, "EN file missing topic: %r" % t)

    def test_current_phase_documented_in_both_files(self):
        """P3.44.5 docs round: both files document the current phase,
        its stabilisation scope and the release history."""
        en, hu = _read(README_PATH), _read(README_HU_PATH)
        self.assertIn("What's new in P3.44.5", en)
        self.assertIn("Újdonságok a P3.44.5-ben", hu)
        for text in (en, hu):
            self.assertIn("tests/test_p3_44_5_stabilisation_integrity.py", text)
            self.assertIn("P3.44.5", text)
        # The release-history sections exist in both files.
        self.assertIn("## Release history", en)
        self.assertIn("## Kiadástörténet", hu)
        # The developer/repository sections exist in both files.
        self.assertIn("## For developers", en)
        self.assertIn("## Fejlesztőknek", hu)


# ---------------------------------------------------------------------------
# B. Branding runtime (real widgets)
# ---------------------------------------------------------------------------
class TestBrandingRuntime(unittest.TestCase):

    def test_app_name_is_guineo_everywhere(self):
        from engine import version
        from ui.main_window import APP_NAME as MW_APP_NAME
        self.assertEqual(version.APP_NAME, "GUINEO")
        self.assertEqual(MW_APP_NAME, "GUINEO")
        self.assertTrue(version.get_debug_info().startswith("GUINEO"))
        self.assertIn("GUINEO", version.get_startup_log_header())

    def test_entry_point_application_names(self):
        src = _read(os.path.join(_ROOT, "SpeechStudio.py"))
        self.assertIn('app.setApplicationName("GUINEO")', src)
        self.assertIn('app.setApplicationDisplayName("GUINEO")', src)
        self.assertIn('app.setOrganizationName("GUINEO")', src)
        # The window icon is wired to the GUINEO logo asset.
        self.assertIn('guineo_logo.png', src)

    def test_main_window_title_and_icon(self):
        """Real Engine + real MainWindow: the visible window title is
        GUINEO and the window carries an icon."""
        from unittest.mock import patch, PropertyMock
        from engine.engine import Engine
        from ui.main_window import MainWindow

        tmp = tempfile.mkdtemp(prefix="ss_p337_")
        engine = Engine(app_root=tmp)
        fake_model = types.SimpleNamespace()
        patchers = [
            patch.object(type(engine._model), "is_loaded",
                         new_callable=PropertyMock, return_value=True),
            patch.object(type(engine._model), "device",
                         new_callable=PropertyMock, return_value="cpu"),
        ]
        for p in patchers:
            p.start()
        try:
            win = MainWindow(engine)
            self.assertEqual(win.windowTitle(), "GUINEO")
            self.assertFalse(win.windowIcon().isNull(),
                             "MainWindow must carry the GUINEO icon")
        finally:
            for p in patchers:
                p.stop()
            win.close()
            win.deleteLater()
            engine.shutdown()

    def test_top_navigation_brand_label(self):
        """Real MenuBar + Toolbar + TopNavigation: the brand slot shows
        the official GUINEO wordmark (P3.38) — a pixmap label with the
        right accessible name — and no SpeechStudio label remains."""
        from ui.panels.menu_bar import MenuBar
        from ui.panels.toolbar import Toolbar
        from ui.panels.top_navigation import TopNavigation

        host = QMainWindow()
        menubar = MenuBar(host)
        toolbar = Toolbar(host)
        nav = TopNavigation(menubar, toolbar)
        labels = [l.text() for l in nav.findChildren(QLabel)]
        self.assertNotIn("SpeechStudio", labels)
        # P3.38: the brand is the official wordmark image.
        brand = nav.brand_logo_label()
        self.assertFalse(brand.pixmap().isNull(),
                         "the brand slot must show the GUINEO wordmark")
        self.assertEqual(brand.accessibleName(), "GUINEO")
        self.assertEqual(brand.toolTip(), "GUINEO")
        nav.deleteLater()
        host.deleteLater()

    def test_first_run_demo_text_mentions_guineo(self):
        """Fresh settings → the inserted Hungarian demo text says GUINEO."""
        from unittest.mock import patch, PropertyMock
        from engine.engine import Engine
        from ui.main_window import MainWindow

        tmp = tempfile.mkdtemp(prefix="ss_p337b_")
        engine = Engine(app_root=tmp)
        patchers = [
            patch.object(type(engine._model), "is_loaded",
                         new_callable=PropertyMock, return_value=True),
            patch.object(type(engine._model), "device",
                         new_callable=PropertyMock, return_value="cpu"),
        ]
        for p in patchers:
            p.start()
        try:
            win = MainWindow(engine)
            text = win._editor.get_text()
            self.assertIn("GUINEO teszt", text)
            self.assertNotIn("SpeechStudio teszt", text)
        finally:
            for p in patchers:
                p.stop()
            win.close()
            win.deleteLater()
            engine.shutdown()

    def test_user_facing_dialog_filters_guineo(self):
        src = _read(os.path.join(_ROOT, "ui", "main_window.py"))
        self.assertIn('"GUINEO Scene (*.scene.json);;All Files (*)"', src)
        self.assertIn('"GUINEO Legacy Project (*.sproj);;All Files (*)"', src)
        self.assertIn('"GUINEO Project (*.sproj);;All Files (*)"', src)
        self.assertNotIn('"SpeechStudio Scene', src)
        self.assertNotIn('"SpeechStudio Legacy Project', src)
        self.assertNotIn('"SpeechStudio Project', src)

    def test_about_dialog_attribution(self):
        """The About dialog carries the licence attribution required by
        the Boson licence (Section IV(a)(iii)) and points at the licence
        files."""
        src = _read(os.path.join(_ROOT, "ui", "main_window.py"))
        self.assertIn("Built with Higgs TTS 3 licensed from Boson AI USA, Inc.",
                      src)
        self.assertIn("THIRD_PARTY_LICENSES.md", src)
        self.assertIn("GUINEO Documentation", src)

    def test_token_guide_guineo(self):
        src = _read(os.path.join(_ROOT, "ui", "panels", "token_guide.py"))
        self.assertNotIn("SpeechStudio verified experiments", src)
        self.assertIn("GUINEO verified experiments", src)

    def test_model_load_animation_untouched(self):
        self.assertTrue(os.path.isfile(ANIMATION_PATH))
        digest = hashlib.sha256(_read_bytes(ANIMATION_PATH)).hexdigest()
        self.assertEqual(digest, _ANIMATION_SHA256,
                         "the model-load animation must remain untouched")


# ---------------------------------------------------------------------------
# C. Rebrand safety: internal identifiers + update contract
# ---------------------------------------------------------------------------
class TestRebrandSafety(unittest.TestCase):

    def test_internal_identifiers_not_globally_renamed(self):
        # Entry point keeps its historical file name.
        self.assertTrue(os.path.isfile(os.path.join(_ROOT, "SpeechStudio.py")))
        # Project / scene file-format identifiers unchanged.
        pm = _read(os.path.join(_ROOT, "engine", "project_manager.py"))
        self.assertIn("speechstudio-project-v2", pm)
        sp = _read(os.path.join(_ROOT, "engine", "scene_persistence.py"))
        self.assertIn('"speechstudio-scene"', sp)
        # QSS objectNames unchanged (global stylesheet contract).
        comp = _read(os.path.join(_ROOT, "ui", "components.py"))
        self.assertIn("SpeechStudioIconButton", comp)
        # Log namespaces unchanged.
        mw = _read(os.path.join(_ROOT, "ui", "main_window.py"))
        self.assertIn('logging.getLogger("speechstudio.ui.main_window")', mw)

    def test_launcher_scripts_show_guineo(self):
        bat = _read(os.path.join(_ROOT, "launch.bat"))
        self.assertIn("[GUINEO]", bat)
        self.assertNotIn("[SpeechStudio]", bat)
        sh = _read(os.path.join(_ROOT, "launch.sh"))
        self.assertIn("[GUINEO]", sh)
        self.assertNotIn("[SpeechStudio]", sh)
        boot = _read(os.path.join(_ROOT, "bootstrap.py"))
        self.assertIn('APP_NAME = "GUINEO"', boot)
        self.assertIn("GUINEO requires Python", boot)

    def test_update_script_keeps_technical_contract(self):
        upd = _read(os.path.join(_ROOT, "update.bat"))
        # User-facing header is GUINEO …
        self.assertIn("GUINEO Safe Update", upd)
        self.assertIn("You can now launch GUINEO with launch.bat", upd)
        # … but the technical contract (zip name, entry point, path strip
        # rule, preserved-folder list) is untouched.
        self.assertIn('SpeechStudio_clean.zip', upd)
        self.assertIn("SpeechStudio.py", upd)
        self.assertIn("'^SpeechStudio/'", upd)
        for folder in ("settings/", "voices/", "outputs/", "presets/", "models/"):
            self.assertIn(folder, upd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
