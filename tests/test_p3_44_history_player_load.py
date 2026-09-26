"""GUINEO P3.44-A — HISTORY ENTRY → BOTTOM WAVEFORM PLAYER (runtime).

User request: clicking a generated item in the left-panel History list
must LOAD that audio into the bottom WaveformPlayer so it can be
listened to from anywhere — not only inside the History screen itself.

Covers:
  * History context list click → transport loads (path/duration/peaks)
  * The P3.34 selection contract is KEPT (select + MRU, no dialog)
  * Missing file → previous track preserved + status message (no modal)
  * Entry without an output path → informative status message only
  * Re-clicking the CURRENT entry does not reset the transport
  * Absolute paths and engine-app-root-relative paths both resolve
  * The History screen's own Play button lands on the SAME current
    track (resolved against the ENGINE's app root, never the CWD)

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python -m pytest \
        tests/test_p3_44_history_player_load.py -v
"""
import math
import os
import struct
import sys
import tempfile
import shutil
import types
import unittest
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Fake torch/transformers (house pattern — not installed in the test env).
for _mod in ("torch", "transformers"):
    try:
        __import__(_mod)
    except ImportError:
        sys.modules.setdefault(_mod, types.ModuleType(_mod))

# P3.44.4 (test-env hygiene): enrich the bare stub so a LATER-imported
# module driving real generations is not poisoned (see the note in
# test_p3_43_voice_unification.py — runtime-verified interference).
_torch_stub = sys.modules.get("torch")
if _torch_stub is not None and not hasattr(_torch_stub, "no_grad"):
    class _StubNoGrad:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    _torch_stub.no_grad = lambda: _StubNoGrad()
    _torch_stub.manual_seed = lambda _s: None
    _torch_stub.from_numpy = lambda arr: arr
    _torch_stub.cuda = types.SimpleNamespace(is_available=lambda: False)

from PySide6.QtWidgets import QApplication
from unittest.mock import patch, PropertyMock

_app = QApplication.instance() or QApplication([])

from engine.engine import Engine
from engine.history_manager import HistoryEntry
from ui.main_window import MainWindow


def _make_wav(path, seconds=1.5, rate=16000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(seconds * rate)
        frames = bytearray()
        for i in range(n):
            v = int(12000 * math.sin(2 * math.pi * 440 * i / rate))
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))


class _HistoryHarness(unittest.TestCase):
    """Real Engine + real MainWindow in an isolated tmpdir (P3.34 pattern)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ss_p344a_")
        cls.engine = Engine(app_root=cls.tmp)
        cls._patcher = patch.object(
            type(cls.engine._model), "is_loaded",
            new_callable=PropertyMock, return_value=True)
        cls._patcher.start()
        cls.win = MainWindow(cls.engine)
        cls.win.resize(1440, 900)
        cls.win.show()
        QApplication.processEvents()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.win.close()
        except Exception:
            pass
        cls._patcher.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _add_entry(cls, entry_id: str, output_path: str,
                   duration: float = 1.5, sample_rate: int = 16000,
                   timestamp: str = "20260829_120000") -> None:
        """Write a real history JSON entry (the HistoryManager's own
        storage format — no production API bypassed)."""
        path = os.path.join(cls.engine._history._history_dir,
                            entry_id + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "id": entry_id,
                "timestamp": timestamp,
                "prompt": "p344 test prompt",
                "output_path": output_path,
                "output_duration": duration,
                "output_sample_rate": sample_rate,
                "project": "Proj",
                "scene_name": "Scene 1",
                "speaker": "Narrator",
            }, f)

    def _click(self, entry_id: str) -> None:
        """The sidebar History row click path (real signal chain)."""
        self.win._sidebar.recent_item_clicked.emit("History", entry_id)
        QApplication.processEvents()


class TestHistoryClickLoadsPlayer(_HistoryHarness):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_wav(os.path.join(cls.tmp, "outputs", "h1.wav"), 1.5, 16000)
        _make_wav(os.path.join(cls.tmp, "outputs", "h2.wav"), 2.5, 24000)
        cls._add_entry("p344h1", "outputs/h1.wav", 1.5, 16000)
        cls._add_entry("p344h2", "outputs/h2.wav", 2.5, 24000)

    def test_click_loads_audio_into_bottom_waveplayer(self):
        wf = self.win._waveform
        before = wf.current_path
        self._click("p344h1")
        expected = os.path.join(self.tmp, "outputs", "h1.wav")
        self.assertEqual(wf.current_path, expected)
        self.assertNotEqual(wf.current_path, before,
                            "the click must have loaded a new track")
        # Duration from the authoritative entry metadata.
        self.assertAlmostEqual(wf._duration, 1.5, delta=0.35)
        # Peaks actually loaded from the real file (waveform visible).
        self.assertTrue(len(wf._island.waveform._peaks) > 0)

    def test_click_second_entry_swaps_track(self):
        self._click("p344h1")
        self._click("p344h2")
        self.assertEqual(
            self.win._waveform.current_path,
            os.path.join(self.tmp, "outputs", "h2.wav"))

    def test_click_keeps_selection_and_mru_without_dialog(self):
        """P3.34 contract preserved: click selects + refreshes MRU and
        NEVER opens the HistoryViewDialog."""
        with patch("ui.panels.history_view.HistoryViewDialog") as dlg_cls:
            self._click("p344h2")
        dlg_cls.assert_not_called()
        self.assertEqual(self.win._last_viewed_history_id, "p344h2")
        recents = self.win._recents_manager.get_recent_history()
        self.assertTrue(any(r.entity_id == "p344h2" for r in recents))

    def test_reclick_current_entry_does_not_reset_transport(self):
        wf = self.win._waveform
        self._click("p344h1")
        calls = []
        orig = wf.set_audio_info

        def _spy(duration, sr, path):
            calls.append(path)
            orig(duration, sr, path)

        wf.set_audio_info = _spy
        try:
            # Same entry again → no destructive reload.
            self._click("p344h1")
            self.assertEqual(calls, [])
            # Different entry → loads.
            self._click("p344h2")
            self.assertEqual(
                calls, [os.path.join(self.tmp, "outputs", "h2.wav")])
        finally:
            wf.set_audio_info = orig


class TestHistoryClickMissingFile(_HistoryHarness):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_wav(os.path.join(cls.tmp, "outputs", "good.wav"))
        cls._add_entry("good", "outputs/good.wav", 1.5, 16000)
        cls._add_entry("gone", "outputs/gone.wav", 1.5, 16000)  # no file
        cls._add_entry("nopath", "", 1.5, 16000)

    def test_missing_file_keeps_previous_track_and_message(self):
        self._click("good")
        before = self.win._waveform.current_path
        self._click("gone")
        # The transport keeps its current track (no destructive clear).
        self.assertEqual(self.win._waveform.current_path, before)
        # A clear non-blocking explanation (no modal dialog).
        self.assertIn("not found", self.win._status_bar.currentMessage())

    def test_entry_without_output_path_message_only(self):
        self._click("nopath")
        self.assertIn("no audio file",
                      self.win._status_bar.currentMessage())

    def test_absolute_output_path_entry_loads(self):
        abs_wav = os.path.join(self.tmp, "outputs", "abs.wav")
        _make_wav(abs_wav, 1.0, 16000)
        self._add_entry("abswav", abs_wav, 1.0, 16000)
        self._click("abswav")
        self.assertEqual(self.win._waveform.current_path, abs_wav)


class TestHistoryViewDialogPlay(_HistoryHarness):
    """The History screen's own Play must land on the same transport
    (P3.44: resolved against the ENGINE's app root, never the CWD —
    the old code passed the relative string to the WaveformPlayer whose
    peak loader resolves against the process CWD)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_wav(os.path.join(cls.tmp, "outputs", "d1.wav"), 1.2, 16000)
        cls._add_entry("dial1", "outputs/d1.wav", 1.2, 16000)

    def _open_dialog(self):
        from ui.panels.history_view import HistoryViewDialog
        entries = self.win._engine.get_history()
        dlg = HistoryViewDialog(entries, self.win._active_project,
                                self.win)
        dlg.show()
        QApplication.processEvents()
        return dlg

    def test_play_resolves_path_and_drives_transport(self):
        dlg = self._open_dialog()
        # Select the row for dial1.
        for row in range(dlg._table.rowCount()):
            dlg._table.setCurrentCell(row, 0)
            if dlg._get_selected_entry().id == "dial1":
                break
        played = []
        self.win._engine.play_audio = lambda p: played.append(p)
        try:
            dlg._on_play()
        finally:
            dlg.close()
        expected = os.path.join(self.tmp, "outputs", "d1.wav")
        self.assertEqual(played, [expected])
        self.assertEqual(self.win._waveform.current_path, expected)
        self.assertEqual(self.win._waveform._playback_state, "playing")

    def test_replay_same_entry_keeps_waveform(self):
        dlg = self._open_dialog()
        for row in range(dlg._table.rowCount()):
            dlg._table.setCurrentCell(row, 0)
            if dlg._get_selected_entry().id == "dial1":
                break
        self.win._engine.play_audio = lambda p: None
        calls = []
        orig = self.win._waveform.set_audio_info

        def _spy(d, sr, p):
            calls.append(p)
            orig(d, sr, p)

        self.win._waveform.set_audio_info = _spy
        try:
            dlg._on_play()
            first = self.win._waveform.current_path
            dlg._on_play()
        finally:
            self.win._waveform.set_audio_info = orig
            dlg.close()
        # Second Play replays without resetting the waveform.
        self.assertEqual(calls, [])
        self.assertEqual(self.win._waveform.current_path, first)


if __name__ == "__main__":
    unittest.main()
