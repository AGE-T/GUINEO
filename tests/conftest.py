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
import os
import shutil
import sys
import tempfile

_tests_since_gc = 0

# ---------------------------------------------------------------------------
# P3.45.6 closeout — suite-level APP_ROOT / runtime-path isolation
# ---------------------------------------------------------------------------
# ROOT CAUSE (proven during the P3.45.6 closeout investigation, see
# docs/design/P3_45_6_CLOSEOUT_RELEASE_CLEANUP_COMBINE_NOTIFICATIONS.md):
# the MainWindow-driving harnesses boot ``MainWindow(engine)`` where the
# ENGINE got a temporary ``app_root`` — but MainWindow's OWN module-level
# ``APP_ROOT`` stayed the repository root. MainWindow then resolved
# ``projects/`` (mw:717 ProjectManager), ``presets/`` (mw:712) and the
# manual-concat ``outputs/`` (mw:4953) against the REPOSITORY, so every
# character/scene save flow (13 ``save_project`` call sites) wrote real
# runtime data into the repo tree. This is exactly how the five tracked
# ``projects/*/project.json`` test artifacts (3x "Golden" fixtures, the
# runtime-created "Default" pair) leaked into the P3.45.6 commits and
# shipped inside both P3.45.6 release zips.
#
# Two further repo-write channels existed alongside it:
#   * ``engine.logger`` hardwires ``<repo>/logs`` (Logger.__init__ mkdirs
#     and opens file handlers) — every Engine construction touched it;
#   * ``SettingsManager.instance(<repo>/settings)`` auto-saves defaults
#     when the file is missing (Engine() default-root constructions and
#     the two dialog-level ``.instance(_root/settings)`` calls).
#
# FIX (this hook, session-scoped): redirect, for the WHOLE pytest session,
#   1. ``ui.main_window.APP_ROOT``          -> <session_tmp>            (projects/presets/outputs; a symlinked ``assets`` keeps the read-only brand/logo asset resolution — the P3.37 window-icon pin),
#   2. ``engine.logger.Logger.__init__``    -> <session_tmp>/logs       (only the DEFAULT repo dir is redirected; explicit dirs pass through),
#   3. ``SettingsManager.instance``         -> <session_tmp>/settings   (only the REPO settings dir is redirected; every tmp-dir call passes through unchanged).
# Per-test patches of ``ui.main_window.APP_ROOT`` (the P3.45.6 Phase 3
# harness pattern in test_p3_45_6_startup_restoration.py and the p3_35
# sites) keep working: mock records the session value as the original and
# restores it on exit.
# The hook must never raise (same house rule as the memory hook below);
# on any import failure the session simply runs unisolated, exactly like
# every pre-closeout run did.

_SESSION_ROOT = None          # type: str | None
_RESTORE = []                 # [(callable), ...] undo steps, LIFO


def pytest_configure(config):
    global _SESSION_ROOT
    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)

        _SESSION_ROOT = tempfile.mkdtemp(prefix="ss_test_approot_")

        # Read-only app assets (brand logo etc.) must stay resolvable from
        # the redirected APP_ROOT — symlink the real assets directory.
        try:
            os.symlink(os.path.join(repo_root, "assets"),
                       os.path.join(_SESSION_ROOT, "assets"))
        except (OSError, NotImplementedError):
            shutil.copytree(os.path.join(repo_root, "assets"),
                            os.path.join(_SESSION_ROOT, "assets"))

        # 2) Engine logger default-dir redirect (default arg is bound at
        #    def time, so redirect None/unspecified and the repo default).
        #    NOTE ON ORDER: importing ``engine.logger`` first executes the
        #    ``engine`` package __init__, whose ``engine.events`` import
        #    already creates the process-wide Logger singleton against the
        #    REPO logs dir. The patch therefore also RESETS the singleton
        #    (the patched __init__ re-runs Logger._configure, which strips
        #    the pre-existing repo file handlers from the shared
        #    ``speechstudio.engine`` root logger) and removes the empty
        #    logs directory the import itself created.
        import engine.logger as _elog
        _repo_logs = _elog._LOGS_DIR
        _orig_logger_init = _elog.Logger.__init__

        def _logger_init(self, logs_dir=None, *a, **k):
            if logs_dir is None or logs_dir == _repo_logs:
                logs_dir = os.path.join(_SESSION_ROOT, "logs")
            return _orig_logger_init(self, logs_dir, *a, **k)

        _RESTORE.append(lambda: setattr(_elog.Logger, "__init__",
                                        _orig_logger_init))
        _elog.Logger.__init__ = _logger_init
        _elog.Logger._instance = None
        try:
            _elog.Logger.get()  # rebuild the singleton against session tmp
        except Exception:
            pass
        if os.path.isdir(_repo_logs):
            shutil.rmtree(_repo_logs, ignore_errors=True)

        # 1) MainWindow APP_ROOT redirect.
        import ui.main_window as _mw
        _orig_app_root = _mw.APP_ROOT
        _RESTORE.append(lambda: setattr(_mw, "APP_ROOT", _orig_app_root))
        _mw.APP_ROOT = _SESSION_ROOT

        # 3) SettingsManager.instance repo-path redirect.
        import engine.settings_manager as _esm
        _repo_settings = os.path.join(repo_root, "settings")
        _orig_instance = _esm.SettingsManager.instance.__func__

        def _instance(cls, settings_dir):
            try:
                if os.path.abspath(settings_dir) == _repo_settings:
                    settings_dir = os.path.join(_SESSION_ROOT, "settings")
            except (TypeError, ValueError):
                pass
            return _orig_instance(cls, settings_dir)

        _RESTORE.append(lambda: setattr(_esm.SettingsManager, "instance",
                                        classmethod(_orig_instance)))
        _esm.SettingsManager.instance = classmethod(_instance)
    except Exception:
        # Isolation is best-effort: never break the suite startup.
        pass


def pytest_unconfigure(config):
    global _SESSION_ROOT
    for undo in reversed(_RESTORE):
        try:
            undo()
        except Exception:
            pass
    del _RESTORE[:]
    if _SESSION_ROOT is not None:
        shutil.rmtree(_SESSION_ROOT, ignore_errors=True)
        _SESSION_ROOT = None


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
