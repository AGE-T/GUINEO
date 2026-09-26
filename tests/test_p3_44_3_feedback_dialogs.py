"""GUINEO P3.44.3 — FEEDBACK AND CONFIRMATION DIALOG POLISH
(structural UI tests — no pixel-fragile assertions).

Spec §11–§19 coverage for the visual round:

Layer 1 — the central QMessageBox QSS section in ``ui/theme.py``:
  * every message box paints a proper theme surface (the generic
    "QDialog { background: transparent }" rule previously left them
    without a defined background);
  * title/details typography hierarchy (``#qt_msgbox_label`` 15px
    semibold, informative label secondary);
  * uniform, comfortable button metrics (min-width, padding).

Layer 2 — ``ui/feedback.py`` ``FeedbackDialog``:
  * summary/details content hierarchy (same information as the old
    "one blob" messages — counts, locations — but semantically split);
  * technical details (locations/paths) are word-wrapped AND
    mouse-selectable (copyable);
  * long paths are NEVER truncated — the full text stays in the label
    and the dialog width stays bounded;
  * interaction model is bit-identical to QMessageBox: modal, parented,
    standard buttons, Enter → default button, Escape → reject.

Layer 3 — migration guard for the six structured call sites that now
use FeedbackDialog (project saved, scene saved, preset exported, voice
profile exported, batch queue saved/loaded):
  * source-level guard (call sites still use FeedbackDialog.information,
    the old "one blob" formatting is gone);
  * content-preservation: the migrated "Project saved" formatting still
    carries every original field (name, Scenes, Characters, Location).

Plain QMessageBox call sites (warnings, errors, questions, batch
confirmations, …) are intentionally untouched — they only receive the
central QSS polish. Their rendering sanity (icon + buttons + themed
background) is verified here as well.

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_44_3_feedback_dialogs.py -v
"""
import os
import sys

import pytest

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QLabel, QMessageBox, QPushButton,
)

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from ui.theme import THEMES, DEFAULT_THEME, generate_qss, apply_theme  # noqa: E402
from ui.feedback import FeedbackDialog  # noqa: E402


# ----------------------------------------------------------------------
# QApplication singleton (house pattern)
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.fixture()
def themed_app(qapp):
    """Fresh QApplication with the default theme applied (as at launch)."""
    apply_theme(qapp, DEFAULT_THEME)
    return qapp


def _informative_label(box):
    for label in box.findChildren(QLabel):
        if label.objectName() == "qt_msgbox_informativelabel":
            return label
    return None


def _title_label(box):
    for label in box.findChildren(QLabel):
        if label.objectName() == "qt_msgbox_label":
            return label
    return None


def _make_project_saved_box(parent=None):
    """The EXACT content structure the migrated call site produces."""
    box = FeedbackDialog(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle("GUINEO")
    box.set_structured_text(
        "Project saved: {0}".format("Méhek_doku"),
        "Scenes: {0}\nCharacters: {1}\n\n"
        "Location: projects/{2}/".format(3, 2, "6140eafc-e0f"))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.setDefaultButton(QMessageBox.StandardButton.Ok)
    return box


# ======================================================================
# Layer 1 — central QSS section (ui/theme.py)
# ======================================================================
class TestCentralStylesheet:
    def test_qss_contains_message_box_section(self):
        qss = generate_qss(THEMES[DEFAULT_THEME])
        assert "QMessageBox {" in qss
        assert "#qt_msgbox_label" in qss
        assert "#qt_msgbox_informativelabel" in qss

    def test_qss_message_box_section_theme_safe(self):
        """The section must format cleanly for every shipped theme."""
        for name, palette in THEMES.items():
            qss = generate_qss(palette)
            assert "QMessageBox {" in qss, name

    def test_message_box_paints_theme_surface(self, themed_app):
        """The old bug: boxes painted transparent/black. Now bg_surface."""
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("test")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.show()
        themed_app.processEvents()
        pix = box.grab()
        assert not pix.isNull()
        img = pix.toImage()
        # Sample an interior point (middle of the left edge, inside the
        # border+radius) — must be the theme surface, not black/undefined.
        c = img.pixelColor(6, img.height() // 2)
        surface = THEMES[DEFAULT_THEME].bg_surface  # #1A1A1A
        assert abs(c.red() - int(surface[1:3], 16)) <= 1
        assert abs(c.green() - int(surface[3:5], 16)) <= 1
        assert abs(c.blue() - int(surface[5:7], 16)) <= 1
        box.close()

    def test_title_label_takes_hierarchy_font(self, themed_app):
        box = QMessageBox()
        box.setText("Title line")
        box.setInformativeText("detail line")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.show()
        themed_app.processEvents()
        title = _title_label(box)
        assert title is not None
        assert title.font().bold()
        assert title.font().pixelSize() >= 15  # above 13px body
        box.close()

    def test_plain_variants_render(self, themed_app):
        """warnings/errors/questions: icon + buttons exist, themed bg."""
        for icon, buttons in (
            (QMessageBox.Icon.Warning, QMessageBox.StandardButton.Ok),
            (QMessageBox.Icon.Critical, QMessageBox.StandardButton.Ok),
            (QMessageBox.Icon.Question,
             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No),
        ):
            box = QMessageBox()
            box.setIcon(icon)
            box.setText("message text")
            box.setStandardButtons(buttons)
            box.show()
            themed_app.processEvents()
            icon_labels = [l for l in box.findChildren(QLabel)
                           if l.objectName() == "qt_msgboxex_icon_label"]
            assert icon_labels, "semantic icon missing"
            btns = box.findChildren(QPushButton)
            assert btns
            for b in btns:
                assert b.minimumWidth() >= 96  # comfortable uniform size
            box.close()


# ======================================================================
# Layer 2 — FeedbackDialog structure and behaviour
# ======================================================================
class TestFeedbackDialogStructure:
    def test_dialog_exists_with_title_and_summary(self, themed_app):
        box = _make_project_saved_box()
        box.show()
        themed_app.processEvents()
        assert box.windowTitle() == "GUINEO"
        title = _title_label(box)
        assert title is not None
        assert "Project saved: Méhek_doku" in title.text()
        box.close()

    def test_information_remains_present(self, themed_app):
        """Every field of the original message survives (§12)."""
        box = _make_project_saved_box()
        box.show()
        themed_app.processEvents()
        combined = box.text() + "\n" + box.informativeText()
        for token in ("Scenes:", "3", "Characters:", "2",
                      "Location:", "projects/6140eafc-e0f/"):
            assert token in combined, token
        box.close()

    def test_ok_button_exists_with_correct_text(self, themed_app):
        box = _make_project_saved_box()
        box.show()
        themed_app.processEvents()
        buttons = [b for b in box.findChildren(QPushButton)
                   if b.text().replace("&", "") == "OK"]
        assert buttons
        assert buttons[0] is box.defaultButton()
        box.close()

    def test_details_selectable(self, themed_app):
        """Locations must be selectable for copying (§11/§12)."""
        box = _make_project_saved_box()
        details = _informative_label(box)
        assert details is not None
        assert (details.textInteractionFlags()
                & Qt.TextInteractionFlag.TextSelectableByMouse)

    def test_long_location_wraps_not_truncated(self, themed_app):
        long_path = "projects/" + "6140eafc/" + "x" * 160 + \
                    "/deeply/nested/audio_references/output.wav"
        box = FeedbackDialog()
        box.set_structured_text("Project saved",
                                "Location: {0}".format(long_path))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.show()
        themed_app.processEvents()
        details = _informative_label(box)
        assert details is not None
        # Full text preserved — never truncated for appearance (§12).
        assert long_path in details.text()
        assert details.wordWrap()          # sensible wrapping
        assert box.size().width() <= 800   # bounded, not screen-wide
        box.close()


class TestFeedbackDialogBehaviour:
    def test_modal_and_parented(self, themed_app):
        parent = QLabel("owner")
        box = FeedbackDialog(parent)
        assert box.parent() is parent
        assert box.isModal()

    def test_information_accept_returns_ok(self, themed_app):
        """Accept path: the default button fires, result == Ok."""
        results = {}

        def click_ok():
            # Click the first VISIBLE dialog with a real default button —
            # leaked/unconfigured FeedbackDialog widgets from other tests
            # must never swallow this (a None.click() inside the event
            # loop would hang exec() forever).
            for w in themed_app.topLevelWidgets():
                if (isinstance(w, FeedbackDialog) and w.isVisible()
                        and w.defaultButton() is not None):
                    w.defaultButton().click()
                    return

        QTimer.singleShot(0, click_ok)
        results["r"] = FeedbackDialog.information(
            None, "GUINEO", "Project saved: X", "Scenes: 1")
        assert results["r"] == QMessageBox.StandardButton.Ok

    def test_enter_key_triggers_default(self, themed_app):
        box = _make_project_saved_box()
        box.show()
        themed_app.processEvents()

        captured = []
        box.accepted.connect(lambda: captured.append(True))
        QTest.keyClick(box, Qt.Key.Key_Return)
        themed_app.processEvents()
        assert captured == [True]
        assert box.result() == int(QMessageBox.StandardButton.Ok)

    def test_escape_key_parity_with_plain_qmessagebox(self, themed_app):
        """Escape behaviour must stay IDENTICAL to plain QMessageBox."""
        outcomes = {}
        for cls in (QMessageBox, FeedbackDialog):
            box = cls()
            box.setText("message")
            if isinstance(box, FeedbackDialog):
                box.set_structured_text("message", "details")
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.setDefaultButton(QMessageBox.StandardButton.Ok)
            box.show()
            themed_app.processEvents()
            QTest.keyClick(box, Qt.Key.Key_Escape)
            themed_app.processEvents()
            outcomes[cls.__name__] = box.result()
            box.close()
        assert outcomes["QMessageBox"] == outcomes["FeedbackDialog"]

    def test_window_closing_via_close(self, themed_app):
        box = _make_project_saved_box()
        box.show()
        themed_app.processEvents()
        box.close()
        assert not box.isVisible()


# ======================================================================
# Layer 3 — migration guard (the six structured call sites)
# ======================================================================
class TestMigrationGuard:
    SITES = {
        "ui/main_window.py": ("FeedbackDialog.information",
                              "Project saved: {0}\\n\\n"),
        "ui/panels/batch_generation.py": ("FeedbackDialog.information",
                                          "Batch queue saved to:\\n"),
        "ui/panels/voice_library.py": ("FeedbackDialog.information",
                                       "exported to:\\n"),
    }

    def test_call_sites_use_feedback_dialog(self):
        for path, (needle, gone) in self.SITES.items():
            src = open(os.path.join(APP_ROOT, path), encoding="utf-8").read()
            assert needle in src, path
            assert gone not in src, \
                "{0}: old one-blob formatting still present".format(path)

    def test_theme_and_feedback_modules_importable(self, themed_app):
        import ui.feedback  # noqa: F401
        import ui.theme  # noqa: F401
