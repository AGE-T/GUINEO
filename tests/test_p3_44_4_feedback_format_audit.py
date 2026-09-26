"""GUINEO P3.44.4 — FEEDBACKDIALOG FORMAT REGRESSION AUDIT
(P3.44.3 regression follow-up, runtime + source-level tests).

The reported regression: MainWindow._on_save_project() used positional
indexes {1}/{2}/{3} in the details string while supplying only THREE
format arguments (indexes 0/1/2) — ``.format()`` raised
"Replacement index 3 out of range for positional args tuple" INSIDE the
try block, so the user saw "Failed to save project: ..." even though
the save itself had already succeeded.

Why the P3.44.3 suite missed it: its Layer-3 migration guard checked
SOURCE PATTERNS and modeled the intended content in a hand-built dialog
(_make_project_saved_box) — it never EXECUTED the real call site, so an
invalid format index between the migration and this audit was invisible.

This file closes that gap three ways:

Layer A (systematic source audit): an AST scan of EVERY
FeedbackDialog.information() call site under ``ui/`` — every
string-literal ``.format()`` expression is validated for positional
index validity (max index < argument count, no auto/manual mixing).
This is the P3.44.4 TASK §2 audit: it would have caught the Project
Save defect and catches any future drift.

Layer B (the regression, executed for real): the REAL Project Save path
runs with FeedbackDialog.information and QMessageBox.critical recorded
— proving the save succeeds, no formatting exception is raised, the
expected summary lands, and the Scenes/Characters counts and Location
are correct (TASK §6).

Layer C (equivalent coverage, TASK §7): the other migrated structured
feedback paths executed for real — scene save, batch queue save, batch
queue load, voice profile export (menu path), preset export.

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_44_4_feedback_format_audit.py -v
"""
import ast
import gc
import glob
import os
import shutil
import string
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Fake torch (house pattern — keeps engine imports light).
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

from PySide6.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication([])

from engine.engine import Engine
from engine.models import Project, Scene, Character, GenerationParameters
from engine.project_manager import ProjectManager
from engine.batch_manager import BatchJob
from engine.preset_manager import Preset
from ui.main_window import MainWindow
from ui.feedback import FeedbackDialog


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


# ===========================================================================
# Layer A — systematic AST audit of every FeedbackDialog format call
# ===========================================================================
class _FeedbackFormatAuditor(ast.NodeVisitor):
    """Collect (file, line, format-string, n_positional_args) for every
    ``"<literal>".format(...)`` expression passed to a
    ``FeedbackDialog.information(...)`` call.

    Adjacent string literals ("a" "b") are folded into ONE Constant by
    the parser (verified at P3.44.4 audit time), so a format call
    attached to the concatenation is validated against the FULL string.
    """

    def __init__(self, path):
        self.path = path
        self.sites = []

    def visit_Call(self, node):
        # Detect FeedbackDialog.information(...)
        func = node.func
        is_info = (isinstance(func, ast.Attribute)
                   and func.attr == "information"
                   and isinstance(func.value, ast.Name)
                   and func.value.id == "FeedbackDialog")
        if is_info:
            # summary is args[2], details is args[3] (parent, title, ...).
            for arg in node.args[2:4]:
                if (isinstance(arg, ast.Call)
                        and isinstance(arg.func, ast.Attribute)
                        and arg.func.attr == "format"
                        and isinstance(arg.func.value, ast.Constant)
                        and isinstance(arg.func.value.value, str)):
                    self.sites.append((
                        self.path, node.lineno,
                        arg.func.value.value,
                        len([a for a in arg.args
                             if not isinstance(a, ast.keyword)])))
        self.generic_visit(node)


def _iter_feedback_format_sites():
    root = os.path.join(_ROOT, "ui")
    for path in sorted(glob.glob(os.path.join(root, "**", "*.py"),
                                 recursive=True)):
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError):
            continue
        auditor = _FeedbackFormatAuditor(path)
        auditor.visit(tree)
        for site in auditor.sites:
            yield site


def _format_violations(sites):
    """Validate every (file, line, fmt, n_args) site. Returns a list of
    human-readable violation strings."""
    violations = []
    for path, line, fmt, n_args in sites:
        positional = []
        auto = 0
        try:
            for _literal, field_name, _spec, _conv in \
                    string.Formatter().parse(fmt):
                if field_name is None:
                    continue
                if field_name == "":
                    auto += 1
                elif field_name.isdigit():
                    positional.append(int(field_name))
                # named fields ({name}) are validated by keywords — skip
        except ValueError as exc:
            violations.append("{0}:{1}: unparseable format string "
                              "{2!r} ({3})".format(path, line, fmt, exc))
            continue
        if auto and positional:
            violations.append(
                "{0}:{1}: mixes automatic {{}} and manual {{{{n}}}} "
                "numbering in {2!r}".format(path, line, fmt))
        if positional and max(positional) >= n_args:
            violations.append(
                "{0}:{1}: positional index {2} out of range for {3} "
                "argument(s) in {4!r}".format(
                    path, line, max(positional), n_args, fmt))
        if auto > n_args:
            violations.append(
                "{0}:{1}: {2} automatic field(s) exceed {3} argument(s) "
                "in {4!r}".format(path, line, auto, n_args, fmt))
    return violations


class TestFeedbackFormatSourceAudit(unittest.TestCase):
    """TASK §2: inspect EVERY migrated FeedbackDialog.information()
    .format() expression for invalid positional indexes."""

    def test_every_call_site_is_discovered(self):
        """The audit must actually SEE the migrated sites (a scanner that
        finds nothing proves nothing)."""
        sites = list(_iter_feedback_format_sites())
        files = {os.path.relpath(path, _ROOT) for path, _, _, _ in sites}
        self.assertIn("ui/main_window.py", files)
        self.assertIn("ui/panels/batch_generation.py", files)
        self.assertIn("ui/panels/voice_library.py", files)
        # The six migrated P3.44.3 sites + the tested ones.
        self.assertGreaterEqual(len(sites), 6,
                                "expected at least the 6 migrated call "
                                "sites, found {0}".format(len(sites)))

    def test_no_format_index_violations_anywhere(self):
        """Every FeedbackDialog.information() .format() expression in the
        codebase has valid positional indexes. (The pre-fix Project Save
        site — {1}/{2}/{3} with 3 args — is exactly what this catches.)"""
        sites = list(_iter_feedback_format_sites())
        violations = _format_violations(sites)
        self.assertEqual(violations, [],
                         "invalid positional format indexes found:\n"
                         + "\n".join(violations))

    def test_buggy_pattern_is_gone_from_save_project(self):
        """The exact regression pattern ({3} with 3 args) is absent."""
        src = open(os.path.join(_ROOT, "ui", "main_window.py"),
                   encoding="utf-8").read()
        self.assertNotIn("projects/{3}/", src)
        self.assertIn("projects/{2}/", src)


# ===========================================================================
# Harness — real MainWindow + Engine + ProjectManager
# ===========================================================================
class _MWHarness(unittest.TestCase):

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="ss_p3444fb_")
        self.engine = Engine(app_root=self.tmp)
        self.win = MainWindow(self.engine)
        self.pm = ProjectManager(os.path.join(self.tmp, "projects"))
        self.win._project_manager = self.pm

        self.project = Project(name="Méhek_doku", id="6140eafc-e0f")
        self.s1 = Scene(id="sc000001", name="01 Scene", project_id="projA")
        self.s2 = Scene(id="sc000002", name="02 Scene", project_id="projA")
        self.project.add_scene(self.s1)
        self.project.add_scene(self.s2)
        for name in ("Guide", "Captain", "Mate"):
            self.project.add_character(Character(name=name))
        self.pm.save_project(self.project)
        self.win._active_project = self.project
        self.win._active_scene = self.s1
        self.win._current_project = "Méhek_doku"
        self.win._current_scene = "01 Scene"

        # Record structured feedback + critical errors instead of exec().
        self.feedback_calls = []
        self.critical_calls = []

        def fake_information(parent, title, summary, details=None):
            self.feedback_calls.append(
                {"parent": parent, "title": title, "summary": summary,
                 "details": details})
            return QMessageBox.StandardButton.Ok

        def fake_critical(*args, **kwargs):
            self.critical_calls.append((args, kwargs))
            return QMessageBox.StandardButton.Ok

        self._patches = [
            patch.object(FeedbackDialog, "information",
                         staticmethod(fake_information)),
            patch.object(QMessageBox, "critical", staticmethod(fake_critical)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        try:
            if getattr(self, "win", None) is not None:
                try:
                    if self.win._batch_dialog is not None:
                        self.win._batch_dialog.close()
                except Exception:
                    pass
                try:
                    self.win.close()
                except Exception:
                    pass
                self.win.deleteLater()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)
        _process(60)
        self.win = None
        self.engine = None
        self.pm = None
        self.project = None
        self.tmp = None
        gc.collect()

    def assert_no_critical(self, context):
        self.assertEqual(self.critical_calls, [],
                         "{0} must not raise/show any error dialog "
                         "(got: {1!r})".format(
                             context,
                             [a[0][2] if len(a[0]) > 2 else a[0]
                              for a in self.critical_calls]))


# ===========================================================================
# Layer B — the Project Save regression, executed for real
# ===========================================================================
class TestProjectSaveFeedbackPath(_MWHarness):

    def test_save_project_succeeds_and_feedback_is_correct(self):
        """TASK §6: execute the REAL Project Save feedback path.

        * the project save SUCCEEDS (project.json on disk)
        * no formatting exception is raised (no critical dialog)
        * FeedbackDialog receives the expected summary
        * the Scenes count is correct (2)
        * the Characters count is correct (3)
        * the Location is correct (projects/{id}/)

        Pre-fix, this exact path raised "Replacement index 3 out of
        range for positional args tuple" and showed "Failed to save
        project: …" AFTER a successful save."""
        self.win._on_save_project()

        self.assert_no_critical("Project Save")
        self.assertEqual(len(self.feedback_calls), 1,
                         "exactly one structured feedback dialog expected")
        call = self.feedback_calls[0]
        self.assertEqual(call["title"], "GUINEO")
        self.assertIn("Project saved: Méhek_doku", call["summary"])
        details = call["details"] or ""
        self.assertIn("Scenes: 2", details)
        self.assertIn("Characters: 3", details)
        self.assertIn("Location: projects/6140eafc-e0f/", details)
        # The save itself really happened.
        saved = os.path.join(self.pm.directory, self.project.id,
                             "project.json")
        self.assertTrue(os.path.isfile(saved),
                        "the project file must exist after save")

    def test_save_project_counts_follow_the_project(self):
        """The counts are LIVE project facts, not constants: a project
        with 1 scene / 1 character renders 1/1."""
        self.project.scenes = [self.s1]
        self.project.characters = [self.project.characters[0]]
        self.win._on_save_project()
        self.assert_no_critical("Project Save (minimal project)")
        details = self.feedback_calls[0]["details"] or ""
        self.assertIn("Scenes: 1", details)
        self.assertIn("Characters: 1", details)


# ===========================================================================
# Layer C — equivalent coverage for the other migrated paths
# ===========================================================================
class TestSceneSaveFeedbackPath(_MWHarness):

    def test_save_scene_reports_lines_speakers_and_location(self):
        from engine.batch_manager import JobStatus  # noqa: F401
        bm = self.win._batch_manager
        bm.add_job(BatchJob(name="l1", prompt="line one",
                            speaker="CAPTAIN", project="Méhek_doku"))
        bm.add_job(BatchJob(name="l2", prompt="line two",
                            speaker="MATE", project="Méhek_doku"))
        path = os.path.join(self.tmp, "scene1")  # .scene.json appended
        final = path + ".scene.json"
        with patch("ui.main_window.QFileDialog.getSaveFileName",
                   return_value=(path, "")):
            self.win._on_save_scene()
        self.assert_no_critical("Scene Save")
        self.assertEqual(len(self.feedback_calls), 1)
        call = self.feedback_calls[0]
        self.assertEqual(call["summary"], "Scene saved")
        details = call["details"] or ""
        self.assertIn("Location: {0}".format(final), details)
        self.assertIn("2 lines, 2 speakers.", details)
        self.assertTrue(os.path.isfile(final))


class TestBatchQueueFeedbackPath(_MWHarness):

    def test_save_and_load_queue_report_locations_and_counts(self):
        from engine.batch_manager import BatchJob as BJ
        self.win._on_open_batch_generation()
        dlg = self.win._batch_dialog
        bm = self.win._batch_manager
        bm.add_job(BJ(name="q1", prompt="prompt one", project="Alpha"))
        bm.add_job(BJ(name="q2", prompt="prompt two", project="Alpha"))
        qpath = os.path.join(self.tmp, "batch_queue.yaml")

        with patch("ui.panels.batch_generation.QFileDialog.getSaveFileName",
                   return_value=(qpath, "")):
            dlg._on_save_queue()
        self.assert_no_critical("Queue Save")
        self.assertEqual(len(self.feedback_calls), 1)
        call = self.feedback_calls[0]
        self.assertEqual(call["summary"], "Batch queue saved")
        self.assertIn("Location: {0}".format(qpath),
                      call["details"] or "")
        self.assertTrue(os.path.isfile(qpath))

        with patch("ui.panels.batch_generation.QFileDialog.getOpenFileName",
                   return_value=(qpath, "")):
            dlg._on_load_queue()
        self.assert_no_critical("Queue Load")
        self.assertEqual(len(self.feedback_calls), 2)
        call = self.feedback_calls[1]
        self.assertEqual(call["summary"], "Batch queue loaded")
        self.assertIn("2 jobs loaded from:", call["details"] or "")
        self.assertIn(qpath, call["details"] or "")
        self.assertEqual(len(bm.jobs), 2)


class TestVoiceExportFeedbackPath(_MWHarness):

    def test_export_voice_profile_reports_name_and_location(self):
        import types as _types
        voice = _types.SimpleNamespace(name="Narrator")
        exported = os.path.join(self.tmp, "export", "voice-1")
        with patch.object(self.win._control_panel, "get_selected_voice_id",
                          return_value="voice-1"), \
             patch.object(self.win._engine, "get_voice",
                          return_value=voice), \
             patch.object(self.win._engine, "export_voice_profile",
                          return_value=exported), \
             patch("ui.main_window.QFileDialog.getExistingDirectory",
                   return_value=os.path.join(self.tmp, "export")):
            self.win._on_export_voice_menu()
        self.assert_no_critical("Voice Export")
        self.assertEqual(len(self.feedback_calls), 1)
        call = self.feedback_calls[0]
        self.assertEqual(call["summary"],
                         "Voice profile 'Narrator' exported")
        details = call["details"] or ""
        self.assertIn("Location: {0}".format(exported), details)
        self.assertIn("reference WAV", details)


class TestPresetExportFeedbackPath(_MWHarness):

    def test_export_preset_reports_location(self):
        preset = Preset(name="Audit Preset",
                        parameters=GenerationParameters())
        self.win._preset_manager.save(preset)
        ppath = os.path.join(self.tmp, "preset.yaml")
        with patch("ui.main_window.QFileDialog.getSaveFileName",
                   return_value=(ppath, "")):
            self.win._on_export_preset(name="Audit Preset")
        self.assert_no_critical("Preset Export")
        self.assertEqual(len(self.feedback_calls), 1)
        call = self.feedback_calls[0]
        self.assertEqual(call["summary"], "Preset exported")
        self.assertIn("Location: {0}".format(ppath),
                      call["details"] or "")
        self.assertTrue(os.path.isfile(ppath))


if __name__ == "__main__":
    unittest.main()
