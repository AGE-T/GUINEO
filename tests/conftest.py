"""Suite-level test-instance hygiene (P3.44.1).

ROOT CAUSE (runtime-verified): ``unittest`` keeps every ``TestCase``
INSTANCE — and with it every attribute its ``setUp`` installed — alive
until the END of the whole suite run (the loader builds the complete
suite up front and holds the instances). The house test pattern creates
a REAL MainWindow + Engine (+ batch dialog, project set, harness state)
per test, and the harness ``tearDown``s only ``close()`` the windows:
close HIDES a top-level widget; the retained instance attributes keep
the entire C++ widget tree, the engine and the dialog heap resident.

Measured effect on this suite: ~40 MB retained PER widget-driving test
→ the full run's resident set climbed past ~2.9 GB at ~85% progress
(the point where the heaviest late-alphabet files run) and the kernel
OOM-killed pytest on the 4 GB host — TWICE, mid-run, with no failure
output (the first P3.44.1 full-suite attempt died exactly this way and
the final summary line was never produced).

FIX (this hook): after each test's own ``tearDown`` has run, drop the
instance's PUBLIC attributes entirely. unittest semantics make this
safe by construction: each test method runs on a FRESH instance, so
instance attributes never communicate anything across tests; class
level state (``setUpClass``/``tearDownClass``, e.g. the shared-window
pattern in test_p3_44_history_player_load.py) lives on the class and is
untouched. QWidget-valued attributes are closed and scheduled for C++
deletion (``deleteLater``) first, and the deferred deletions are
flushed through the existing QApplication so the memory is reclaimed
immediately rather than one test later.

The hook never raises: a hygiene pass must not be able to fail a test.

COST NOTE: a full ``gc.collect()`` after EVERY test measured ~97 ms/test
on this suite (growing heap) — ~2 minutes of pure overhead at 1191
tests, enough to push a single full-suite run past the environment's
time ceiling (runtime-verified: the run was SIGTERMed by the very
timeout installed to bound it). The C++ bulk (the actual ~40 MB/test)
is freed by ``deleteLater`` + ``processEvents`` — NOT by gc; gc only
reclaims cyclic PYTHON garbage. Cycles are therefore collected every
20th test (60 forced collections ≈ seconds) while Python's own
allocation-threshold collections continue to run in between.
"""
import gc

_tests_since_gc = 0


def pytest_runtest_teardown(item, nextitem):
    """Release the finished test's instance state (see module docstring)."""
    inst = getattr(item, "instance", None)
    if inst is None or not hasattr(inst, "_testMethodName"):
        return  # not a unittest TestCase instance
    try:
        names = [n for n in vars(inst) if not n.startswith("_")]
        app = None
        for n in names:
            try:
                obj = getattr(inst, n)
                is_widget = (callable(getattr(obj, "isWidgetType", None))
                             and obj.isWidgetType())
                if is_widget:
                    try:
                        obj.close()
                    except Exception:
                        pass
                    try:
                        obj.deleteLater()
                    except Exception:
                        pass
                    if app is None:
                        try:
                            from PySide6.QtWidgets import QApplication
                            app = QApplication.instance()
                        except Exception:
                            app = False
            except Exception:
                pass
            try:
                delattr(inst, n)
            except Exception:
                pass
        if app:
            try:
                app.processEvents()  # flush the deferred deletions now
            except Exception:
                pass
        global _tests_since_gc
        _tests_since_gc += 1
        if _tests_since_gc >= 20:
            _tests_since_gc = 0
            gc.collect()
    except Exception:
        pass
