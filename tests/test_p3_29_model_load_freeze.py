"""
SpeechStudio — P3.29 Model Load Dialog FREEZE regression tests
===============================================================

USER-REPORTED DEFECT (post-P3.28, Windows screenshot):
    "Valamiért ez a kis ablak megfagy, majd ha betöltődött a model,
     eltűnik." — the "Loading Model - SpeechStudio" dialog froze for the
    WHOLE model load (Windows title bar: "(Not Responding)"), the
    elapsed timer never ticked, the status stayed "Initializing...", and
    the dialog disappeared the moment the load finished.

ROOT CAUSE (reproduced with audit_probes_p329/probe_model_load_freeze.py
against the unfixed build: 0 watchdog ticks in a 3.4 s simulated load,
max single processEvents() block 3.39 s = the whole load):
  1. ModelManager.load() held ONE RLock for the ENTIRE load (import
     torch + from_pretrained + .to(device)) and emitted
     ENGINE_STATUS_CHANGED right after ``_loading = True``.
  2. MainWindow marshals that event to the GUI thread, where
     _refresh_model_status() calls ModelManager.status() ->
     ``with self._lock:`` -> the GUI thread BLOCKED until the load
     finished. No timer ticks, no paints, no input => "(Not Responding)".
  3. Secondary hazard: status() itself did ``import torch`` — when the
     worker was mid-import, the per-module import lock blocked the GUI
     thread for the whole import.

FIX (runtime contract proven by these tests):
  * ModelManager lock scopes split: ``_state_lock`` (RLock, microseconds
    — status()/is_loaded/device/get_model_and_tokenizer) vs ``_load_lock``
    (serializes whole load()/unload()/warmup() — never taken by readers).
  * status() NEVER imports torch: only an already-imported torch
    (sys.modules) is queried, fully exception-guarded.
  * load() publishes human-readable phases on ModelStatus.phase
    ("Preparing...", "Loading tokenizer...", "Loading model weights...",
    "Moving model to CUDA/CPU...").
  * ModelLoadDialog polls status_fn (non-blocking) and shows the phase.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_29_model_load_freeze.py -v
"""

from __future__ import annotations
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

_app = QApplication.instance() or QApplication([])

from ui.theme import apply_theme, DEFAULT_THEME
apply_theme(_app, DEFAULT_THEME)


# ---------------------------------------------------------------------------
# Fake torch / transformers machinery (installed in sys.modules during the
# tests that exercise the REAL ModelManager.load() code path)
# ---------------------------------------------------------------------------
class _FakeCuda:
    @staticmethod
    def is_available():
        return False


def _make_fake_torch():
    mod = types.ModuleType("torch")
    mod.cuda = _FakeCuda
    mod.bfloat16 = "fake-bfloat16"
    mod.float16 = "fake-float16"
    mod.float32 = "fake-float32"
    return mod


class _FakeModel:
    def to(self, device):
        return self

    def eval(self):
        return self

    def generate_speech(self, *a, **k):
        return None


class _FakeTransformers:
    """Records calls; from_pretrained sleeps to simulate a slow load."""

    def __init__(self, tokenizer_delay=0.2, weights_delay=1.2,
                 to_device_delay=0.3, fail_weights=False):
        self.tokenizer_delay = tokenizer_delay
        self.weights_delay = weights_delay
        self.to_device_delay = to_device_delay
        self.fail_weights = fail_weights
        self.tokenizer_calls = 0
        self.model_calls = 0
        self.state = {}

        tf = types.ModuleType("transformers")
        outer = self

        class _AutoTok:
            @staticmethod
            def from_pretrained(path, **kw):
                outer.tokenizer_calls += 1
                time.sleep(outer.tokenizer_delay)
                return types.SimpleNamespace()

        class _AutoModel:
            @staticmethod
            def from_pretrained(path, **kw):
                outer.model_calls += 1
                if outer.fail_weights:
                    raise RuntimeError("simulated corrupt weights")
                time.sleep(outer.weights_delay)
                outer.state["weights_done"] = time.time()

                class _SlowTo:
                    def to(self, device):
                        time.sleep(outer.to_device_delay)
                        return _FakeModel()

                    def eval(self):
                        return self

                return _SlowTo()

        tf.AutoModelForCausalLM = _AutoModel
        tf.AutoTokenizer = _AutoTok
        self.module = tf


class _FakeMLModules:
    """Context manager: install fake torch+transformers, restore after."""

    def __init__(self, fake_tf):
        self._fake_tf = fake_tf

    def __enter__(self):
        self._saved = {k: sys.modules.get(k)
                       for k in ("torch", "transformers")}
        sys.modules["torch"] = _make_fake_torch()
        sys.modules["transformers"] = self._fake_tf.module
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        return False


class _TorchImportRecorder:
    """Meta-path hook that records (and refuses) any 'torch' import."""

    def __init__(self):
        self.attempts = []

    def find_module(self, fullname, path=None):
        if fullname == "torch" or fullname.startswith("torch."):
            self.attempts.append(fullname)
            return self
        return None

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "torch" or fullname.startswith("torch."):
            self.attempts.append(fullname)
            raise ImportError("torch import blocked by P3.29 test")
        return None


def _make_model_dir(tmpdir):
    model_dir = os.path.join(tmpdir, "models", "higgs-audio-v3")
    os.makedirs(model_dir, exist_ok=True)
    with open(os.path.join(model_dir, "config.json"), "w") as f:
        f.write('{"model_type": "fake"}')
    return model_dir


# ---------------------------------------------------------------------------
# Pure ModelManager tests
# ---------------------------------------------------------------------------
class TestModelManagerNonBlockingStatus(unittest.TestCase):
    """status() / is_loaded must never block while load() is running."""

    def _manager(self, tmpdir):
        from engine.model_manager import ModelManager
        from engine.events import EventBus
        return ModelManager(EventBus(), _make_model_dir(tmpdir))

    def test_status_non_blocking_during_load(self):
        """THE core regression: while the worker is inside a slow
        from_pretrained (pre-fix: holding the lock for the whole load),
        status() on another thread must return within milliseconds."""
        tmpdir = tempfile.mkdtemp(prefix="ss_p329_a_")
        try:
            mgr = self._manager(tmpdir)
            fake = _FakeTransformers(weights_delay=1.5)
            with _FakeMLModules(fake):
                t = threading.Thread(target=lambda: mgr.load(use_cuda=False),
                                     daemon=True)
                t.start()
                time.sleep(0.25)          # worker is inside the fake load
                t0 = time.time()
                st = mgr.status()
                dt = time.time() - t0
                self.assertTrue(st.loading,
                                "status() should report loading=True")
                self.assertFalse(st.model_loaded)
                t.join(timeout=10)
            self.assertLess(
                dt, 0.30,
                "status() blocked %.2fs during an in-progress load — the "
                "frozen Loading-Model-dialog regression (pre-fix: blocks "
                "for the WHOLE load)" % dt)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_is_loaded_and_device_non_blocking_during_load(self):
        tmpdir = tempfile.mkdtemp(prefix="ss_p329_b_")
        try:
            mgr = self._manager(tmpdir)
            fake = _FakeTransformers(weights_delay=1.2)
            with _FakeMLModules(fake):
                t = threading.Thread(target=lambda: mgr.load(use_cuda=False),
                                     daemon=True)
                t.start()
                time.sleep(0.2)
                t0 = time.time()
                loaded = mgr.is_loaded
                dev = mgr.device
                dt = time.time() - t0
                t.join(timeout=10)
            self.assertFalse(loaded)
            self.assertEqual(dev, "cpu")
            self.assertLess(dt, 0.30,
                            "is_loaded/device blocked %.2fs during load" % dt)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_status_never_imports_torch(self):
        """status() must never import torch: on the GUI thread, during the
        worker's ``import torch`` the per-module import lock would block
        the GUI thread for the whole import."""
        tmpdir = tempfile.mkdtemp(prefix="ss_p329_c_")
        saved = {k: sys.modules.get(k) for k in ("torch", "transformers")}
        sys.modules.pop("torch", None)
        sys.modules.pop("transformers", None)
        hook = _TorchImportRecorder()
        sys.meta_path.insert(0, hook)
        try:
            mgr = self._manager(tmpdir)
            st = mgr.status()
            self.assertEqual(hook.attempts, [],
                             "status() imported torch: %s — the GUI-thread "
                             "import-lock hazard" % hook.attempts)
            self.assertFalse(st.cuda_available)
        finally:
            sys.meta_path.remove(hook)
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_status_tolerates_partially_initialized_torch(self):
        """During the worker's ``import torch`` sys.modules already
        contains a PARTIALLY-initialised module (no cuda attr yet /
        cuda calls raise). status() must degrade to cuda_available=False
        and never raise."""
        tmpdir = tempfile.mkdtemp(prefix="ss_p329_d_")
        saved = sys.modules.get("torch")
        broken = types.ModuleType("torch")     # no cuda attribute at all

        class _ExplodingCuda:
            @staticmethod
            def is_available():
                raise RuntimeError("CUDA init in progress")

        try:
            mgr = self._manager(tmpdir)
            sys.modules["torch"] = broken
            st = mgr.status()
            self.assertFalse(st.cuda_available)

            broken.cuda = _ExplodingCuda()
            st = mgr.status()
            self.assertFalse(st.cuda_available,
                             "status() must swallow CUDA query failures")
        finally:
            if saved is None:
                sys.modules.pop("torch", None)
            else:
                sys.modules["torch"] = saved
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_unload_never_imports_torch(self):
        tmpdir = tempfile.mkdtemp(prefix="ss_p329_e_")
        saved = {k: sys.modules.get(k) for k in ("torch", "transformers")}
        sys.modules.pop("torch", None)
        sys.modules.pop("transformers", None)
        hook = _TorchImportRecorder()
        sys.meta_path.insert(0, hook)
        try:
            mgr = self._manager(tmpdir)
            # simulate a loaded state (the real body must run, not the
            # early return)
            mgr._model = _FakeModel()
            mgr._tokenizer = types.SimpleNamespace()
            mgr._device = "cpu"
            mgr.unload()
            self.assertEqual(hook.attempts, [],
                             "unload() imported torch: %s" % hook.attempts)
            self.assertIsNone(mgr._model)
            self.assertIsNone(mgr._tokenizer)
        finally:
            sys.meta_path.remove(hook)
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestLoadPhasesAndEvents(unittest.TestCase):
    """Phase publication + load lifecycle semantics."""

    def _manager_with_events(self, tmpdir):
        from engine.model_manager import ModelManager
        from engine.events import EventBus, EventType
        bus = EventBus()
        events = []
        mgr = ModelManager(bus, _make_model_dir(tmpdir))
        for et in (EventType.ENGINE_STATUS_CHANGED, EventType.MODEL_LOADED,
                   EventType.MODEL_UNLOADED):
            bus.subscribe(et, events.append)
        return mgr, bus, events

    def test_phase_sequence_and_events(self):
        tmpdir = tempfile.mkdtemp(prefix="ss_p329_f_")
        try:
            mgr, bus, events = self._manager_with_events(tmpdir)
            fake = _FakeTransformers(tokenizer_delay=0.05,
                                     weights_delay=0.1,
                                     to_device_delay=0.05)
            with _FakeMLModules(fake):
                st = mgr.load(use_cuda=False)

            from engine.events import EventType
            status_events = [e for e in events
                             if e.type == EventType.ENGINE_STATUS_CHANGED]
            phases = [e.data.phase for e in status_events]
            self.assertIn("Preparing...", phases)
            self.assertIn("Loading tokenizer...", phases)
            self.assertIn("Loading model weights...", phases)
            self.assertTrue(any(p.startswith("Moving model to")
                                for p in phases),
                            "no 'Moving model to ...' phase in %s" % phases)

            loaded = [e for e in events if e.type == EventType.MODEL_LOADED]
            self.assertEqual(len(loaded), 1)
            # final status: loaded, not loading, phase cleared
            self.assertTrue(st.model_loaded)
            self.assertFalse(st.loading)
            self.assertEqual(st.phase, "")
            self.assertEqual(fake.tokenizer_calls, 1)
            self.assertEqual(fake.model_calls, 1)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_failure_resets_state_and_emits_no_model_loaded(self):
        tmpdir = tempfile.mkdtemp(prefix="ss_p329_g_")
        try:
            mgr, bus, events = self._manager_with_events(tmpdir)
            fake = _FakeTransformers(fail_weights=True)
            from engine.errors import ModelLoadFailed
            with _FakeMLModules(fake):
                with self.assertRaises(ModelLoadFailed):
                    mgr.load(use_cuda=False)
            from engine.events import EventType
            self.assertFalse(
                any(e.type == EventType.MODEL_LOADED for e in events))
            st = mgr.status()
            self.assertFalse(st.model_loaded)
            self.assertFalse(st.loading)
            self.assertEqual(st.phase, "")
            self.assertFalse(mgr.is_loaded)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_already_loaded_short_circuit(self):
        tmpdir = tempfile.mkdtemp(prefix="ss_p329_h_")
        try:
            mgr, bus, events = self._manager_with_events(tmpdir)
            fake = _FakeTransformers(tokenizer_delay=0.05,
                                     weights_delay=0.05,
                                     to_device_delay=0.05)
            with _FakeMLModules(fake):
                mgr.load(use_cuda=False)
                mgr.load(use_cuda=False)      # second call: no-op
            self.assertEqual(fake.model_calls, 1,
                             "second load() re-ran from_pretrained")
            self.assertEqual(fake.tokenizer_calls, 1)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Dialog-level tests
# ---------------------------------------------------------------------------
class TestDialogPhasePolling(unittest.TestCase):

    def test_dialog_poll_updates_status_text(self):
        from ui.panels.model_load_dialog import ModelLoadDialog

        current = {"phase": ""}

        def status_fn():
            return types.SimpleNamespace(phase=current["phase"])

        dlg = ModelLoadDialog(None, status_fn=status_fn)
        try:
            dlg.start()
            dlg._poll_status()
            self.assertEqual(dlg._status.text(), "Initializing...")
            current["phase"] = "Loading model weights..."
            dlg._poll_status()
            self.assertEqual(dlg._status.text(), "Loading model weights...")
            current["phase"] = "Moving model to CUDA..."
            dlg._poll_status()
            self.assertEqual(dlg._status.text(), "Moving model to CUDA...")
        finally:
            dlg.stop()

    def test_dialog_empty_phase_keeps_previous_text(self):
        from ui.panels.model_load_dialog import ModelLoadDialog

        def status_fn():
            return types.SimpleNamespace(phase="")

        dlg = ModelLoadDialog(None, status_fn=status_fn)
        try:
            dlg.start()
            dlg.set_status("Loading model weights...")
            dlg._poll_status()
            self.assertEqual(dlg._status.text(), "Loading model weights...",
                             "empty phase must not clobber the status text")
        finally:
            dlg.stop()

    def test_dialog_status_fn_exception_swallowed(self):
        from ui.panels.model_load_dialog import ModelLoadDialog

        def boom():
            raise RuntimeError("status query failed")

        dlg = ModelLoadDialog(None, status_fn=boom)
        try:
            dlg.start()
            dlg._poll_status()      # must not raise
            self.assertEqual(dlg._status.text(), "Initializing...")
        finally:
            dlg.stop()

    def test_dialog_without_status_fn_still_ticks(self):
        from ui.panels.model_load_dialog import ModelLoadDialog
        dlg = ModelLoadDialog(None)
        try:
            dlg.start()
            dlg._poll_status()
            self.assertEqual(dlg._timer_label.text(), "Elapsed: 00:00")
        finally:
            dlg.stop()


# ---------------------------------------------------------------------------
# Full MainWindow runtime — the golden regression (the user's scenario)
# ---------------------------------------------------------------------------
class TestMainWindowLoadAlive(unittest.TestCase):
    """Real Engine + real MainWindow; a slow fake model load; the GUI
    event loop must stay ALIVE for the whole load (pre-fix: one blocked
    processEvents() covering the entire load => "(Not Responding)")."""

    @classmethod
    def setUpClass(cls):
        import logging
        logging.disable(logging.WARNING)
        from engine.engine import Engine
        from ui.main_window import MainWindow

        cls.tmpdir = tempfile.mkdtemp(prefix="ss_p329_rt_")
        _make_model_dir(cls.tmpdir)
        cls.fake_tf = _FakeTransformers(tokenizer_delay=0.3,
                                        weights_delay=1.2,
                                        to_device_delay=0.3)
        cls._ml = _FakeMLModules(cls.fake_tf)
        cls._ml.__enter__()
        # Fresh Engine in a fresh tmpdir: is_loaded is naturally False
        # (no class-level is_loaded patch here — the load must be able to
        # flip it to True for the final assertions).
        cls.engine = Engine(app_root=cls.tmpdir)
        cls.win = MainWindow(cls.engine)
        # Neutralise the startup "load model?" offer (a modal would
        # corrupt the timing loop).
        cls.win._offer_model_load = lambda: None

    @classmethod
    def tearDownClass(cls):
        import logging
        try:
            d = getattr(cls.win, "_model_load_dialog", None)
            if d is not None:
                d.stop()
                cls.win._model_load_dialog = None
            cls.win.close()
        except Exception:
            pass
        cls._ml.__exit__(None, None, None)
        logging.disable(logging.NOTSET)
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_gui_alive_for_whole_load(self):
        """THE user scenario: during the simulated load the watchdog
        QTimer must keep firing (healthy event loop), the dialog elapsed
        timer must advance, load phases must become visible, and the
        dialog must close with the model loaded at the end."""
        win = self.win
        ticks = []
        watchdog = QTimer(win)
        watchdog.setInterval(50)
        watchdog.timeout.connect(lambda: ticks.append(time.time()))
        watchdog.start()

        t_start = time.time()
        win._load_model()
        dlg = win._model_load_dialog
        self.assertIsNotNone(dlg)
        self.assertIsNotNone(dlg._status_fn,
                             "MainWindow must wire the dialog's phase poll")

        max_block = 0.0
        phase_texts = set()
        last_elapsed = None
        elapsed_changes = 0
        deadline = t_start + 15.0
        while time.time() < deadline:
            if win._model_load_dialog is None:
                break
            p0 = time.time()
            _app.processEvents()
            max_block = max(max_block, time.time() - p0)
            if win._model_load_dialog is None:
                break
            phase_texts.add(dlg._status.text())
            txt = dlg._timer_label.text()
            if txt != last_elapsed:
                elapsed_changes += 1
                last_elapsed = txt
            time.sleep(0.01)
        watchdog.stop()

        t_worker_end = self.fake_tf.state.get("weights_done",
                                              time.time()) + 0.3 + 0.5
        ticks_during_load = [t for t in ticks if t <= t_worker_end]

        # 1. The dialog closed and the model is loaded ("eltűnik" — only
        #    AFTER the load, never before).
        self.assertIsNone(win._model_load_dialog,
                          "dialog did not close after the load")
        self.assertTrue(self.engine._model.is_loaded)

        # 2. The GUI thread stayed alive for the whole load: the watchdog
        #    (50 ms) fired at a healthy rate while the worker was loading
        #    (pre-fix: 0 ticks — one blocked call for the entire load).
        load_window = t_worker_end - t_start
        healthy = int(load_window / 0.05)
        self.assertGreaterEqual(
            len(ticks_during_load), max(3, int(healthy * 0.5)),
            "only %d watchdog ticks in a %.2fs load (healthy ~%d) — the "
            "frozen Loading-Model-dialog regression"
            % (len(ticks_during_load), load_window, healthy))

        # 3. No single event-loop block over 0.5s (pre-fix: ~the whole
        #    load; Windows shows "(Not Responding)" after ~5s).
        self.assertLess(max_block, 0.5,
                        "a single processEvents() call blocked %.2fs"
                        % max_block)

        # 4. The dialog elapsed timer advanced DURING the load (the
        #    screenshot symptom: the timer never ticked).
        self.assertGreaterEqual(elapsed_changes, 2,
                                "dialog elapsed timer never advanced during "
                                "the load")

        # 5. Load phases became visible (screenshot symptom: stuck at
        #    "Initializing...").
        self.assertTrue(
            phase_texts & {"Loading tokenizer...", "Loading model weights...",
                           "Moving model to CPU..."},
            "no load phase ever visible in %s" % sorted(phase_texts))

    def test_stale_deferred_close_spares_newer_dialog(self):
        """P3.29 (found by ss/e2e/e2e_p329.py S7): a deferred close
        scheduled for load #1 (whose dialog the user CANCELLED — the
        background load continues) must NOT kill a NEWER load's dialog
        when it fires. Pre-fix sequence: the stale singleShot(0) close
        ran inside the new dialog's start() event pump and closed the
        NEW dialog — the load continued invisibly and the
        generate-after-load chain never fired."""
        from ui.panels.model_load_dialog import ModelLoadDialog
        from engine.models import ModelStatus
        win = self.win

        dlg1 = ModelLoadDialog(win, status_fn=lambda: None)
        win._model_load_dialog = dlg1
        dlg1.start()
        # Load #1's worker completes -> the close is scheduled FOR dlg1.
        win._close_model_load_dialog(ModelStatus(model_loaded=True))
        # ... but the user had meanwhile cancelled dlg1 and started
        # load #2 (a NEW dialog is current):
        dlg2 = ModelLoadDialog(win, status_fn=lambda: None)
        win._model_load_dialog = dlg2
        dlg2.start()      # its internal event pump delivers the stale close
        _app.processEvents()
        _app.processEvents()
        try:
            self.assertIs(win._model_load_dialog, dlg2,
                          "a stale deferred close killed the NEWER load's "
                          "dialog")
            self.assertTrue(dlg2.isVisible(),
                            "the newer load's dialog was hidden by a stale "
                            "close")
        finally:
            dlg2.stop()
            win._model_load_dialog = None
            dlg1.stop()

    def test_matching_deferred_close_still_works(self):
        """The identity guard must not break the NORMAL close: a close
        scheduled for the CURRENT dialog still closes it."""
        from ui.panels.model_load_dialog import ModelLoadDialog
        from engine.models import ModelStatus
        win = self.win

        dlg = ModelLoadDialog(win, status_fn=lambda: None)
        win._model_load_dialog = dlg
        dlg.start()
        win._close_model_load_dialog(ModelStatus(model_loaded=True))
        _app.processEvents()
        _app.processEvents()
        try:
            self.assertIsNone(win._model_load_dialog,
                              "the matching close did not close the dialog")
        finally:
            d = getattr(win, "_model_load_dialog", None)
            if d is not None:
                d.stop()
                win._model_load_dialog = None
            dlg.stop()

    def test_generate_path_wires_status_fn_too(self):
        """_load_model_and_generate uses the same phase-poll wiring."""
        win = self.win
        seen = {}

        def fake_load(use_cuda=True, callback=None):
            dlg = win._model_load_dialog
            seen["status_fn"] = getattr(dlg, "_status_fn", None)
            return None

        with patch.object(self.engine, "load_model", side_effect=fake_load):
            win._load_model_and_generate("hello")
        try:
            # bound methods: compare by == (same __self__ + __func__),
            # not by identity (each access creates a new bound object)
            self.assertEqual(seen.get("status_fn"),
                             self.engine.get_model_status)
            self.assertIsNotNone(seen.get("status_fn"))
        finally:
            d = getattr(win, "_model_load_dialog", None)
            if d is not None:
                d.stop()
                win._model_load_dialog = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
