"""
SpeechStudio — P3.45.5 Verification & Test Hygiene regression suite
====================================================================

Pins the P3.45.5 maintenance round (verification tooling + test
reliability; NO product behaviour change):

  1. ``tools/verify_architecture.py`` has teeth:
     - the real tree passes (the gate),
     - explicitly permitted imports pass — tier-1 (by-design), tier-2
       (documented baseline debt) and the facade root,
     - an arbitrary NEW ``engine.*`` import is rejected — top-level,
       lazy (function-level), via the relative-import facade shortcut
       and via ``from engine import <submodule>``,
     - the engine "no Qt" rule rejects lazy Qt imports and permits
       ONLY the two per-file documented exceptions (module-scoped).
  2. ``tools/verify_integration.py`` encodes the documented §14 stop
     contract (active-run stop: in-flight member and pending members →
     SKIPPED, never-run non-member → PENDING untouched; idle stop: a
     documented no-op) — unit-level against the REAL BatchManager,
     plus the tool itself exits 0.
  3. SS-3: the P3.37 branding test validates the CURRENT README
     structure (anchor + product title + English-only document), not
     the obsolete literal header; the whole module is green.
  4. The P3.44.4 regen-stop test synchronises on REAL conditions — a
     bounded engine-idle barrier before the regen submit (the exact
     ``submit_generation`` precondition) and a bounded in-flight
     barrier before the stop (the model call received the request) —
     with no blind fixed sleep as the primary synchronisation.
  5. The corrected docstrings match actual slot-materialisation
     behaviour: re-detect is NOT a slot writer; the sole production
     call site of ``materialize_expected_slots`` is the Generate Long
     structure event in ``ui/main_window.py``.
  6. The governance register reflects the verified states (BUG-001
     CLOSED with its resolution evidence; P3.45-GATE COMPLETED).
"""

from __future__ import annotations

import ast
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_architecture_p3455",
        os.path.join(REPO, "tools", "verify_architecture.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_VA = _load_verifier()


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Architecture verifier
# ---------------------------------------------------------------------------
class TestArchitectureVerifier(unittest.TestCase):
    """The corrected verifier: permits the documented baseline, rejects
    everything else — including lazy and disguised imports."""

    def _make_tree(self, files):
        root = tempfile.mkdtemp(prefix="ss_p3455_arch_")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for rel, content in files.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        return root

    # -- the actual gate --------------------------------------------------
    def test_real_tree_passes_both_layers(self):
        debt_usage = {}
        known_debt = []
        self.assertEqual(_VA._check_ui_layer(REPO, debt_usage), [])
        self.assertEqual(_VA._check_engine_layer(REPO, known_debt), [])
        # The transparency data must still be REPORTED (the documented
        # baseline debt cannot silently disappear from the output).
        self.assertTrue(debt_usage, "baseline-debt usage must be reported")
        self.assertIn("engine.audio_provenance", debt_usage)
        self.assertEqual(
            len(known_debt), 2,
            "exactly the two documented engine-side Qt exceptions "
            "(version.py PySide6 + batch_manager.py PySide6.QtCore)")

    # -- explicitly permitted imports pass --------------------------------
    def test_tier1_by_design_import_passes(self):
        root = self._make_tree({
            "ui/panel_a.py": "from engine.models import VoiceProfile\n",
        })
        self.assertEqual(_VA._check_ui_layer(root), [])

    def test_tier2_baseline_debt_import_passes_and_is_reported(self):
        root = self._make_tree({
            "ui/panel_b.py": "from engine.output_guard import preflight\n",
        })
        debt_usage = {}
        self.assertEqual(_VA._check_ui_layer(root, debt_usage), [])
        self.assertEqual(debt_usage.get("engine.output_guard"),
                         {"ui/panel_b.py"})

    def test_facade_root_import_passes(self):
        # "Engine" is a facade re-export (a class), NOT an engine
        # submodule -> allowed as the exact facade root import.
        root = self._make_tree({
            "engine/__init__.py": "from engine.engine import Engine\n",
            "engine/engine.py": "class Engine(object):\n    pass\n",
            "ui/panel_c.py": "from engine import Engine\n",
        })
        self.assertEqual(_VA._check_ui_layer(root), [])

    def test_lazy_baseline_debt_import_passes(self):
        # Full-AST scanning must not over-flag: a function-level import
        # of a documented baseline module is still permitted.
        root = self._make_tree({
            "ui/panel_d.py": (
                "def later():\n"
                "    from engine.duration_estimation import "
                "estimate_duration\n"
                "    return estimate_duration\n"),
        })
        debt_usage = {}
        self.assertEqual(_VA._check_ui_layer(root, debt_usage), [])
        self.assertIn("engine.duration_estimation", debt_usage)

    # -- arbitrary NEW imports are rejected -------------------------------
    def test_arbitrary_new_engine_import_rejected(self):
        root = self._make_tree({
            "ui/panel_e.py": "from engine.brand_new_module import thing\n",
        })
        violations = _VA._check_ui_layer(root)
        self.assertEqual(len(violations), 1)
        self.assertIn("engine.brand_new_module", violations[0])

    def test_lazy_function_level_import_rejected(self):
        root = self._make_tree({
            "ui/panel_f.py": (
                "def sneaky():\n"
                "    import engine.workers\n"
                "    return engine.workers\n"),
        })
        violations = _VA._check_ui_layer(root)
        self.assertEqual(len(violations), 1)
        self.assertIn("engine.workers", violations[0])

    def test_from_engine_import_submodule_rejected(self):
        # "from engine import workers" resolves the NAME against the
        # real engine package: a submodule import, not a facade use.
        root = self._make_tree({
            "engine/__init__.py": "",
            "engine/workers.py": "",
            "ui/panel_g.py": "from engine import workers\n",
        })
        violations = _VA._check_ui_layer(root)
        self.assertEqual(len(violations), 1)
        self.assertIn("engine.workers", violations[0])

    def test_relative_facade_shortcut_rejected(self):
        # A relative import that escapes to the top-level engine
        # package cannot smuggle a submodule past the facade rule.
        root = self._make_tree({
            "engine/__init__.py": "",
            "engine/workers.py": "",
            "ui/panels/deep.py": (
                "def x():\n"
                "    from ...engine import workers\n"
                "    return workers\n"),
        })
        violations = _VA._check_ui_layer(root)
        self.assertEqual(len(violations), 1)
        self.assertIn("engine.workers", violations[0])
        self.assertIn("ui/panels/deep.py", violations[0])

    def test_plain_submodule_import_rejected(self):
        root = self._make_tree({
            "ui/panel_h.py": "import engine.model_manager\n",
        })
        violations = _VA._check_ui_layer(root)
        self.assertEqual(len(violations), 1)
        self.assertIn("engine.model_manager", violations[0])

    def test_ml_import_still_rejected(self):
        root = self._make_tree({
            "ui/panel_i.py": "import torch\n",
        })
        violations = _VA._check_ui_layer(root)
        self.assertEqual(len(violations), 1)
        self.assertIn("torch", violations[0])

    # -- engine layer: no Qt, two documented exceptions --------------------
    def test_engine_layer_rejects_lazy_qt(self):
        root = self._make_tree({
            "engine/new_module.py": (
                "def f():\n"
                "    from PySide6.QtWidgets import QWidget\n"
                "    return QWidget\n"),
        })
        violations = _VA._check_engine_layer(root)
        self.assertEqual(len(violations), 1)
        self.assertIn("engine/new_module.py", violations[0])
        self.assertIn("PySide6.QtWidgets", violations[0])

    def test_engine_layer_documented_exceptions_permitted(self):
        root = self._make_tree({
            "engine/version.py": (
                "def get_qt_version():\n"
                "    try:\n"
                "        import PySide6\n"
                "        return PySide6.__version__\n"
                "    except Exception:\n"
                "        return 'not installed'\n"),
            "engine/batch_manager.py": (
                "def pace():\n"
                "    try:\n"
                "        from PySide6.QtCore import QTimer\n"
                "        QTimer.singleShot(500, _next)\n"
                "    except ImportError:\n"
                "        pass\n"
                "def _next():\n"
                "    pass\n"),
        })
        known_debt = []
        self.assertEqual(_VA._check_engine_layer(root, known_debt), [])
        notes = " ".join(known_debt)
        self.assertIn("engine/version.py", notes)
        self.assertIn("engine/batch_manager.py", notes)

    def test_engine_exception_is_module_scoped(self):
        # The batch_manager exception is for PySide6.QtCore ONLY: any
        # other Qt module in that file is still a violation.
        root = self._make_tree({
            "engine/batch_manager.py": (
                "def f():\n"
                "    from PySide6.QtWidgets import QWidget\n"
                "    return QWidget\n"),
        })
        violations = _VA._check_engine_layer(root)
        self.assertEqual(len(violations), 1)
        self.assertIn("PySide6.QtWidgets", violations[0])

    def test_engine_layer_rejects_ui_import(self):
        root = self._make_tree({
            "engine/leaky.py": "from ui.main_window import MainWindow\n",
        })
        violations = _VA._check_engine_layer(root)
        self.assertEqual(len(violations), 1)
        self.assertIn("ui", violations[0])


# ---------------------------------------------------------------------------
# 2. Integration verifier: the documented §14 stop contract
# ---------------------------------------------------------------------------
class TestStopContract(unittest.TestCase):
    """Unit-level: the REAL BatchManager under the §14 contract the
    corrected integration tool encodes. Deterministic — no Qt event
    loop needed (the never-completing future keeps the job in flight
    exactly like a real in-flight generation)."""

    def _mgr(self, submit_fn=None):
        from concurrent.futures import Future
        from engine.batch_manager import BatchManager
        if submit_fn is None:
            def submit_fn(request):
                return Future()  # never completes -> stays in flight
        return BatchManager(submit_fn=submit_fn,
                            cancel_fn=lambda: True,
                            marshal_to_ui=lambda fn: fn())

    def test_active_run_stop_contract(self):
        """§14: A (in flight, member) → Stopped/SKIPPED; B (member,
        pending) → Stopped/SKIPPED; C (never ran, not a member) →
        Pending UNTOUCHED."""
        from engine.batch_manager import BatchJob, JobStatus
        mgr = self._mgr()
        mgr.add_job(BatchJob(name="jA", prompt="hello"))
        mgr.add_job(BatchJob(name="jB", prompt="world"))
        mgr.add_job(BatchJob(name="jC", prompt="idle"))
        self.assertTrue(mgr.start(only=[0, 1]))
        # The active state must be established BEFORE the stop (the
        # old tool skipped this and asserted a phantom contract).
        self.assertEqual(mgr.jobs[0].status, JobStatus.GENERATING)
        self.assertEqual(mgr.jobs[2].status, JobStatus.PENDING)

        mgr.stop()
        stopped = [j.status for j in mgr.jobs]
        self.assertEqual(stopped[0], JobStatus.SKIPPED)
        self.assertEqual(stopped[1], JobStatus.SKIPPED)
        self.assertEqual(stopped[2], JobStatus.PENDING,
                         "a stop of a run must not touch non-members")

    def test_whole_queue_stop_flips_pending_members(self):
        """§6: a whole-queue run's Stop flips every PENDING member."""
        from engine.batch_manager import BatchJob, JobStatus
        mgr = self._mgr()
        for n in ("w1", "w2", "w3"):
            mgr.add_job(BatchJob(name=n, prompt="p " + n))
        self.assertTrue(mgr.start())
        self.assertEqual(mgr.jobs[0].status, JobStatus.GENERATING)
        mgr.stop()
        self.assertEqual([j.status for j in mgr.jobs],
                         [JobStatus.SKIPPED] * 3)

    def test_idle_stop_is_documented_noop(self):
        """stop() on a manager that was NEVER started: jobs stay
        PENDING (the §10 idle no-op) — the pre-P3.44.4 expectation
        (SKIPPED) was the tool's bug, not the product's contract."""
        from engine.batch_manager import BatchJob, JobStatus
        mgr = self._mgr()
        mgr.add_job(BatchJob(name="j1", prompt="hello"))
        mgr.add_job(BatchJob(name="j2", prompt="world"))
        mgr.stop()
        self.assertEqual([j.status for j in mgr.jobs],
                         [JobStatus.PENDING, JobStatus.PENDING])
        self.assertFalse(mgr.is_running)
        self.assertFalse(mgr.stop_requested)


class TestIntegrationTool(unittest.TestCase):
    """The corrected tool itself: exit 0 with the §14 scenarios."""

    def test_tool_exit_zero_and_pass(self):
        result = subprocess.run(
            [sys.executable,
             os.path.join(REPO, "tools", "verify_integration.py"),
             "--root", REPO],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
        self.assertEqual(
            result.returncode, 0,
            "verify_integration must exit 0; output:\n{0}\nstderr:\n{1}"
            .format(result.stdout, result.stderr))
        self.assertIn("PASS: all integration smoke tests succeeded",
                      result.stdout)


# ---------------------------------------------------------------------------
# 3. SS-3: the P3.37 README test validates the CURRENT structure
# ---------------------------------------------------------------------------
class TestSS3ReadmeContract(unittest.TestCase):

    def test_readme_current_structure_contract(self):
        text = _read(os.path.join(REPO, "README.md"))
        self.assertIn('<a id="english"></a>', text)
        self.assertIn("# GUINEO", text)
        self.assertNotIn("# HU |", text)

    def test_fixed_module_no_longer_asserts_obsolete_header(self):
        src = _read(os.path.join(
            REPO, "tests", "test_p3_37_branding_documentation.py"))
        self.assertNotIn('"# EN | British English"', src,
                         "the obsolete literal-header assertion must be "
                         "gone (the header was deleted by the deliberate "
                         "README rewrite cd5e122)")
        self.assertIn('id="english"', src)

    def test_fixed_module_green(self):
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             os.path.join(REPO, "tests",
                         "test_p3_37_branding_documentation.py"), "-q"],
            capture_output=True, text=True, timeout=180,
            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
        self.assertEqual(
            result.returncode, 0,
            "the P3.37 branding module must be fully green (SS-3 "
            "closed); output:\n{0}".format(result.stdout))
        self.assertIn("passed", result.stdout)


# ---------------------------------------------------------------------------
# 4. Stop-timing stabilisation: a REAL condition, not a blind sleep
# ---------------------------------------------------------------------------
class TestStopTimingStabilisation(unittest.TestCase):
    """Source-contract (house AST style): the regen-stop test
    synchronises through the bounded engine-idle barrier."""

    _FILE = os.path.join(REPO, "tests",
                         "test_p3_44_4_batch_execution_run.py")

    @classmethod
    def _tree(cls):
        return ast.parse(_read(cls._FILE))

    def _method_source(self, tree, name):
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(_read(self._FILE), node)
        self.fail("method {0} not found".format(name))

    def test_barrier_helper_is_condition_based(self):
        src = self._method_source(self._tree(), "wait_engine_idle")
        self.assertIsNotNone(src)
        self.assertIn("is_generating", src,
                      "the barrier must poll the REAL engine busy state")
        self.assertIn("timeout", src,
                      "the barrier must be bounded (clear failure)")

    def test_regen_stop_test_uses_barrier_not_blind_sleep(self):
        src = self._method_source(
            self._tree(), "test_regen_run_stop_leaves_others_pending")
        self.assertIn("self.wait_engine_idle()", src)
        self.assertIn("self.wait_generation_inflight(", src)
        self.assertNotIn("_process(400)", src,
                         "the blind 400 ms sleep must be gone from the "
                         "regen-stop synchronisation")

    def test_inflight_barrier_is_condition_based(self):
        src = self._method_source(self._tree(), "wait_generation_inflight")
        self.assertIn("FakeHiggsModel.calls", src,
                      "the in-flight barrier must wait for the REAL "
                      "model-call state, not a timer")
        self.assertIn("timeout", src,
                      "the in-flight barrier must be bounded")

    def test_blind_sleep_removed_from_file(self):
        src = _read(self._FILE)
        self.assertNotIn("_process(400)  # let the worker fully finalize",
                         src)


# ---------------------------------------------------------------------------
# 5. Documentation corrections match actual slot materialisation
# ---------------------------------------------------------------------------
class TestSlotMaterialisationDocs(unittest.TestCase):

    def test_main_window_docstring_corrected(self):
        src = _read(os.path.join(REPO, "ui", "main_window.py"))
        self.assertNotIn("(only here and on re-detect",
                         src,
                         "the false claim that re-detect materialises "
                         "slots must be gone")
        self.assertIn("never on a keystroke and never on", src)
        self.assertIn("re-detect", src)

    def test_audio_provenance_docstring_corrected(self):
        src = _read(os.path.join(REPO, "engine", "audio_provenance.py"))
        self.assertNotIn("(Generate Long preview / Re-detect)",
                         src,
                         "the false parenthetical must be gone")
        self.assertIn("never on Re-detect", src)

    def test_sole_production_call_site_is_generate_long(self):
        """The docstring claim is pinned to reality: exactly ONE
        production call of materialize_expected_slots exists, in the
        Generate Long structure event (ui/main_window.py)."""
        call_sites = []
        for layer in ("ui", "engine"):
            base = os.path.join(REPO, layer)
            for dirpath, dirnames, filenames in os.walk(base):
                if "__pycache__" in dirpath:
                    continue
                for fname in filenames:
                    if not fname.endswith(".py"):
                        continue
                    path = os.path.join(dirpath, fname)
                    try:
                        tree = ast.parse(_read(path))
                    except SyntaxError:
                        continue
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Call):
                            func = node.func
                            name = getattr(func, "id", None) \
                                or getattr(func, "attr", None)
                            if name == "materialize_expected_slots":
                                call_sites.append(
                                    os.path.relpath(path, REPO))
        self.assertEqual(
            call_sites, ["ui" + os.sep + "main_window.py"],
            "materialize_expected_slots must have exactly one "
            "production call site — the Generate Long structure event "
            "(got {0})".format(call_sites))


# ---------------------------------------------------------------------------
# 6. Governance register reflects the verified states
# ---------------------------------------------------------------------------
class TestGovernanceRegister(unittest.TestCase):

    def test_bug_001_register_closed(self):
        text = _read(os.path.join(REPO, "docs", "governance",
                                  "OPEN_BUGS.md"))
        self.assertIn(
            "**Status**: **CLOSED** (P3.45.5", text)
        self.assertIn("### Resolution (P3.45.5 register update",
                      text)

    def test_p345_gate_register_completed(self):
        text = _read(os.path.join(REPO, "docs", "governance",
                                  "OPEN_BUGS.md"))
        self.assertIn(
            "**Status**: **COMPLETED** (P3.45.5 register update", text)


if __name__ == "__main__":
    unittest.main()
