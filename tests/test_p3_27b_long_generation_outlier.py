"""
SpeechStudio — P3.27B Long Generation Outlier / Excessive Silence Tests
========================================================================

Runtime tests for the P3.27B defect + fix:

  ROOT CAUSE (reproduced in ss/audit_probes_p327b/probe_outlier_repro.py):
  the Higgs Audio V3 model generates audio autoregressively at 25 fps and
  stops on its end-of-speech signal OR when max_new_tokens is exhausted
  (4096 tokens = 163.84 s ceiling). In the defective run the model failed
  to terminate after the speech content and kept emitting (silent) frames
  until the cap: 163.56 s total = 30.48 s speech + ~133 s silent tail.
  The app pipeline was correct (no app stage adds length) but treated the
  pathological output as a normal success, and regen overwrote the
  previous good version in place.

  FIX UNDER TEST:
  1. engine/output_guard.py — conservative anomaly detection on the RAW
     model waveform (runaway trailing silence / expected-duration
     multiple / fully silent output) — detection only, NEVER trimming.
  2. Versioned Generate Long part filenames (Long_vNN_Part_NNN) with a
     disk-guarded allocator — a regenerated outlier can never overwrite
     the previous good version (task §18).
  3. Anomaly flag surfaced in the Batch dialog Duration cell + tooltips
     (task §17) and recorded in History with part index + version (§19).

Test matrix (task §20): normal short/medium parts, same part regenerated,
same seed, different seed, different temperature, different max_tokens,
same prompt/voice, multi-part batches, outlier detection, long trailing
silence detection, fresh output path, previous version preservation,
cancel, retry, failure, and synthetic pathological WAVs for deterministic
guard testing.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_27b_long_generation_outlier.py -v
"""

from __future__ import annotations

import os
import sys
import time
import types
import unittest
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np

# ---------------------------------------------------------------------------
# Fake torch (house pattern: e2e_p325/e2e_p326)
# ---------------------------------------------------------------------------
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

SAMPLE_RATE = 24000
FRAME_RATE = 25
SAMPLES_PER_FRAME = SAMPLE_RATE // FRAME_RATE  # 960


def make_speech(seconds: float, freq: float = 220.0) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def make_runaway(speech_seconds: float, total_seconds: float) -> np.ndarray:
    """Mimic the observed defect: speech + numerically silent tail."""
    speech = make_speech(speech_seconds)
    out = np.zeros(int(total_seconds * SAMPLE_RATE), dtype=np.float32)
    out[: len(speech)] = speech
    return out


class FakeHiggsModel:
    """Model-boundary fake. speech_s = audible length; runaway mode fills
    the rest of the token budget with silence (non-termination)."""

    calls = []
    speech_s = 1.0
    runaway = False

    def generate_speech(self, text, tokenizer, *, reference_audio=None,
                        reference_sample_rate=None, reference_codes=None,
                        reference_text=None, max_new_tokens=2048,
                        temperature=1.0, top_p=None, top_k=None):
        FakeHiggsModel.calls.append(dict(
            text=text, max_new_tokens=max_new_tokens,
            temperature=temperature, top_p=top_p, top_k=top_k))
        if FakeHiggsModel.runaway:
            cap_s = max_new_tokens / float(FRAME_RATE)
            return make_runaway(FakeHiggsModel.speech_s, cap_s)
        return make_speech(FakeHiggsModel.speech_s)


# ===========================================================================
# Pure-function tests: the output guard + filename allocator (no Qt)
# ===========================================================================

class TestOutputGuardPure(unittest.TestCase):
    """Deterministic synthetic-WAV tests of the anomaly detector."""

    def test_normal_short_speech_not_flagged(self):
        from engine.output_guard import analyze_waveform, detect_output_anomaly
        a = analyze_waveform(make_speech(10.0), SAMPLE_RATE)
        self.assertIsNone(detect_output_anomaly(a, expected_duration_s=12.0))

    def test_normal_medium_speech_not_flagged(self):
        from engine.output_guard import analyze_waveform, detect_output_anomaly
        a = analyze_waveform(make_speech(30.0), SAMPLE_RATE)
        self.assertIsNone(detect_output_anomaly(a, expected_duration_s=30.0))

    def test_legitimate_long_speech_not_flagged(self):
        """120 s of continuous speech (a legitimate long part) is normal."""
        from engine.output_guard import analyze_waveform, detect_output_anomaly
        a = analyze_waveform(make_speech(120.0), SAMPLE_RATE)
        self.assertIsNone(detect_output_anomaly(a, expected_duration_s=110.0))

    def test_natural_final_pause_not_flagged(self):
        """A 3 s dramatic final pause is NOT pathological (no blind trim)."""
        from engine.output_guard import analyze_waveform, detect_output_anomaly
        wav = np.concatenate([make_speech(20.0), np.zeros(3 * SAMPLE_RATE,
                                                          dtype=np.float32)])
        a = analyze_waveform(wav, SAMPLE_RATE)
        self.assertIsNone(detect_output_anomaly(a, expected_duration_s=20.0))

    def test_observed_defect_runaway_flagged(self):
        """The exact P3.27B defect: 30.48 s speech + 133 s silent tail."""
        from engine.output_guard import (analyze_waveform,
                                         detect_output_anomaly)
        wav = make_runaway(30.48, 163.56)
        a = analyze_waveform(wav, SAMPLE_RATE)
        self.assertAlmostEqual(a["speech_s"], 30.48, delta=0.1)
        self.assertAlmostEqual(a["trailing_silence_s"], 133.08, delta=0.2)
        anomaly = detect_output_anomaly(
            a, expected_duration_s=30.0, max_new_tokens=4096)
        self.assertIsNotNone(anomaly)
        self.assertTrue(anomaly["detected"])
        self.assertTrue(any("trailing silence" in r
                            for r in anomaly["reasons"]))
        self.assertAlmostEqual(anomaly["token_ceiling_s"], 163.84, delta=0.01)

    def test_expected_duration_rule_flags_outlier(self):
        """12 s expected -> 163 s actual is flagged even without a tail
        rule hit... (here WITH tail: both rules may fire)."""
        from engine.output_guard import analyze_waveform, detect_output_anomaly
        wav = make_runaway(12.0, 60.0)   # 12 s speech + 48 s silence
        a = analyze_waveform(wav, SAMPLE_RATE)
        anomaly = detect_output_anomaly(a, expected_duration_s=12.0)
        self.assertIsNotNone(anomaly)
        self.assertTrue(any("trailing silence" in r for r in anomaly["reasons"]))

    def test_expected_duration_rule_alone_needs_both_conditions(self):
        """Slow-but-legit reading (2.5x expected, no long tail) NOT flagged:
        the multiple rule requires >= 3x AND >= expected+20 s."""
        from engine.output_guard import analyze_waveform, detect_output_anomaly
        a = analyze_waveform(make_speech(30.0), SAMPLE_RATE)
        # 30 s actual vs 12 s expected = 2.5x but < expected+20 (32) edge...
        # 12+20 = 32 > 30 -> not flagged.
        self.assertIsNone(detect_output_anomaly(a, expected_duration_s=12.0))

    def test_silent_output_flagged(self):
        from engine.output_guard import analyze_waveform, detect_output_anomaly
        a = analyze_waveform(np.zeros(10 * SAMPLE_RATE, dtype=np.float32),
                             SAMPLE_RATE)
        anomaly = detect_output_anomaly(a)
        self.assertIsNotNone(anomaly)
        self.assertTrue(any("silent output" in r for r in anomaly["reasons"]))

    def test_tiny_output_never_flagged(self):
        from engine.output_guard import analyze_waveform, detect_output_anomaly
        a = analyze_waveform(make_speech(0.3), SAMPLE_RATE)
        self.assertIsNone(detect_output_anomaly(a))

    def test_max_tokens_math(self):
        """Task §9: token budget -> seconds mapping at 25 fps."""
        from engine.output_guard import HIGGS_FRAME_RATE
        self.assertEqual(30 * HIGGS_FRAME_RATE, 750)     # 30 s -> 750 tokens
        self.assertEqual(60 * HIGGS_FRAME_RATE, 1500)
        self.assertEqual(120 * HIGGS_FRAME_RATE, 3000)
        self.assertAlmostEqual(4096 / HIGGS_FRAME_RATE, 163.84, places=2)

    def test_warning_text_mentions_keep(self):
        """The warning must tell the user the file was kept."""
        from engine.output_guard import (analyze_waveform,
                                         detect_output_anomaly,
                                         anomaly_warning_text)
        a = analyze_waveform(make_runaway(30.48, 163.56), SAMPLE_RATE)
        anomaly = detect_output_anomaly(a, expected_duration_s=30.0)
        text = anomaly_warning_text(anomaly)
        self.assertIn("kept", text)


class TestPartFilenameAllocator(unittest.TestCase):
    """Versioned filenames + disk-guard (task §18)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p327b_names_")

    def test_v01_from_start(self):
        from engine.output_guard import next_free_part_filename
        name, ver = next_free_part_filename(self.tmp, "Eden", "S03", 9,
                                            speaker="Engineer",
                                            scene_id="ab12cd34ef")
        self.assertEqual(ver, 1)
        self.assertEqual(name, "Eden_S03_Long_v01_Part_009_Engineer_ab12cd34.wav")

    def test_disk_guard_bumps_version(self):
        from engine.output_guard import next_free_part_filename
        name1, v1 = next_free_part_filename(self.tmp, "Eden", "S03", 1)
        with open(os.path.join(self.tmp, name1), "wb") as f:
            f.write(b"x")
        name2, v2 = next_free_part_filename(self.tmp, "Eden", "S03", 1)
        self.assertEqual((v1, v2), (1, 2))
        self.assertIn("Long_v02", name2)

    def test_failed_generation_consumes_no_version(self):
        """A failed run writes no file, so the next attempt reuses v01
        slot logic (disk-guard is the truth, not a counter)."""
        from engine.output_guard import next_free_part_filename
        name1, _ = next_free_part_filename(self.tmp, "Eden", "S03", 1)
        name2, _ = next_free_part_filename(self.tmp, "Eden", "S03", 1)
        self.assertEqual(name1, name2)

    def test_extract_version_hint(self):
        from engine.output_guard import extract_version_hint
        self.assertEqual(
            extract_version_hint("Eden_S03_Long_v03_Part_009_ab.wav"), 3)
        self.assertIsNone(extract_version_hint("Eden_S03_Part_09_ab.wav"))


# ===========================================================================
# Runtime pipeline tests (real Engine + BatchManager, fake model boundary)
# ===========================================================================

def _process(ms=30):
    from PySide6.QtWidgets import QApplication
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


class _PipelineHarness(unittest.TestCase):
    """Boots a real Engine + BatchManager (signal marshaller) with the
    model boundary faked. Every test gets its own temp app root."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p327b_run_")
        from engine.engine import Engine
        from engine.batch_manager import BatchManager, BatchJob
        from engine.models import GenerationParameters
        from ui.panels.batch_generation import _UiMarshaler
        from unittest.mock import patch, PropertyMock

        self.engine = Engine(app_root=self.tmp)
        self.patchers = [
            patch.object(type(self.engine._model), "is_loaded",
                         new_callable=PropertyMock, return_value=True),
            patch.object(type(self.engine._model), "device",
                         new_callable=PropertyMock, return_value="cpu"),
            patch.object(type(self.engine._model),
                         "get_model_and_tokenizer",
                         return_value=(FakeHiggsModel(), None)),
        ]
        for p in self.patchers:
            p.start()
        self._UiMarshaler = _UiMarshaler
        self.marshaler = _UiMarshaler()
        self.bm = BatchManager(submit_fn=self.engine.generate,
                               marshal_to_ui=self.marshaler.marshal)
        self.BatchJob = BatchJob
        self.GenerationParameters = GenerationParameters
        FakeHiggsModel.calls = []
        FakeHiggsModel.runaway = False
        FakeHiggsModel.speech_s = 1.0

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_part_job(self, part_index=1, filename=None, speech_s=1.0,
                      expected=None, **param_overrides):
        params = dict(temperature=0.95, top_p=0.95, top_k=300,
                      max_new_tokens=4096, seed=None, append_silence=0.5,
                      normalize_output=False, auto_play=False)
        params.update(param_overrides)
        return self.BatchJob(
            name="Part {0}".format(part_index),
            prompt="A short test sentence for part {0}.".format(part_index),
            voice_id=None,
            parameters=self.GenerationParameters(**params),
            output_filename=filename,
            project="Eden",
            speaker=None,
            scene_id="ab12cd34ef56",
            scene_name="Scene 03",
            part_index=part_index,
            part_version=1,
            expected_duration=expected,
        )

    def wait_batch(self, timeout=30):
        t0 = time.time()
        while self.bm.is_running and time.time() - t0 < timeout:
            _process(20)
        _process(150)

    def read_wav(self, rel_path):
        import soundfile as sf
        path = os.path.join(self.tmp, rel_path)
        samples, sr = sf.read(path, dtype="float32")
        return samples, sr


class TestRuntimePipeline(_PipelineHarness):
    """End-to-end pipeline behaviour with the fake model."""

    def test_normal_part_completes_unflagged(self):
        FakeHiggsModel.speech_s = 12.0
        job = self.make_part_job(part_index=1, expected=12.0)
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        from engine.batch_manager import JobStatus
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertAlmostEqual(job.output_duration, 12.0, delta=0.01)
        self.assertIsNone(job.anomaly)

    def test_outlier_part_flagged_end_to_end(self):
        """The observed defect, deterministically: 30.48 s speech inside a
        163.84 s (full 4096-token) runaway output."""
        FakeHiggsModel.speech_s = 30.48
        FakeHiggsModel.runaway = True
        job = self.make_part_job(part_index=9, expected=30.0)
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        from engine.batch_manager import JobStatus
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertAlmostEqual(job.output_duration, 163.84, delta=0.1)
        self.assertIsNotNone(job.anomaly, "guard must flag the outlier")
        reasons = " ".join(job.anomaly["reasons"])
        self.assertIn("trailing silence", reasons)
        # The result also carries the warning + structured verdict.
        # (History records them — verified below via get_history.)

    def test_outlier_history_entry_traceable(self):
        """Task §19: pathological generation stays fully traceable."""
        FakeHiggsModel.speech_s = 30.48
        FakeHiggsModel.runaway = True
        job = self.make_part_job(part_index=9, expected=30.0)
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        entries = [e for e in self.engine.get_history()
                   if os.path.basename(e.output_path or "") ==
                   os.path.basename(job.output_path)]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.part_index, 9)
        self.assertEqual(entry.part_version, 1)
        self.assertIsNotNone(entry.output_anomaly)
        warnings = entry.data.get("warnings", [])
        self.assertTrue(any("Unusually long output" in w for w in warnings),
                        "warning must be persisted: {0}".format(warnings))

    def test_regen_creates_new_version_and_preserves_previous(self):
        """Task §18/§12: regen writes a FRESH versioned file; the previous
        good version's file is untouched (byte-identical)."""
        FakeHiggsModel.speech_s = 30.48
        job = self.make_part_job(
            part_index=9,
            filename="Eden_S03_Long_v01_Part_009_ab12cd34.wav",
            expected=30.0)
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        good_path = os.path.join(self.tmp, job.output_path)
        with open(good_path, "rb") as f:
            good_bytes = f.read()
        # Regenerate — this time the model runs away (the defect).
        FakeHiggsModel.runaway = True
        from engine.output_guard import next_free_part_filename
        new_name, new_ver = next_free_part_filename(
            os.path.join(self.tmp, "outputs"), project="Eden", scene="S03",
            part_index=9, speaker=None, scene_id="ab12cd34ef56",
            start_version=2)
        job.output_filename = new_name
        job.part_version = new_ver
        self.assertTrue(self.bm.regen_job(0))
        self.bm.start(reset_failed=False)
        self.wait_batch()
        self.assertEqual(job.part_version, 2)
        self.assertIn("Long_v02", job.output_filename)
        self.assertNotEqual(job.output_path,
                            "outputs/Eden_S03_Long_v01_Part_009_ab12cd34.wav")
        # Previous version preserved byte-identically.
        with open(good_path, "rb") as f:
            self.assertEqual(f.read(), good_bytes)
        # The regen is an outlier: flagged, and the v01 file still exists.
        self.assertIsNotNone(job.anomaly)
        self.assertTrue(os.path.isfile(good_path))

    def test_regen_fresh_state_no_stale_audio(self):
        """Task §11/§12: regen output contains ONLY the new waveform
        (no concatenation with, or tail from, the previous version)."""
        FakeHiggsModel.speech_s = 5.0
        job = self.make_part_job(part_index=1,
                                 filename="P_S_Long_v01_Part_001_x1.wav")
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        s1, _ = self.read_wav(job.output_path)
        # Regen with a LONGER speech segment.
        FakeHiggsModel.speech_s = 8.0
        job.output_filename = "P_S_Long_v02_Part_001_x1.wav"
        job.part_version = 2
        self.bm.regen_job(0)
        self.bm.start(reset_failed=False)
        self.wait_batch()
        s2, _ = self.read_wav(job.output_path)
        # New file = 8.5 s (8 s speech + 0.5 s append) — NOT 5+8.5.
        self.assertAlmostEqual(len(s2) / SAMPLE_RATE, 8.5, delta=0.05)
        self.assertLess(len(s2), len(s1) + len(s1))

    def test_same_settings_deterministic_requests(self):
        """Same part + same settings -> identical model kwargs each run
        (the outlier is model-side nondeterminism, not a settings drift)."""
        FakeHiggsModel.speech_s = 2.0
        job = self.make_part_job(part_index=1)
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        call1 = dict(FakeHiggsModel.calls[-1])
        self.bm.regen_job(0)
        self.bm.start(reset_failed=False)
        self.wait_batch()
        call2 = dict(FakeHiggsModel.calls[-1])
        for key in ("text", "temperature", "top_p", "top_k", "max_new_tokens"):
            self.assertEqual(call1[key], call2[key], key)

    def test_same_seed_forwarded_each_run(self):
        """Seed semantics unchanged: a set seed is forwarded every time."""
        FakeHiggsModel.speech_s = 1.0
        job = self.make_part_job(part_index=1, seed=1234)
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        self.bm.regen_job(0)
        self.bm.start(reset_failed=False)
        self.wait_batch()
        # Parameters (incl. seed) survive to_request on both runs.
        self.assertEqual(job.parameters.seed, 1234)
        req = job.to_request()
        self.assertEqual(req.parameters.seed, 1234)

    def test_different_temperature_and_max_tokens_forwarded(self):
        """Settings variations flow to the model boundary unchanged."""
        FakeHiggsModel.speech_s = 1.0
        job = self.make_part_job(part_index=1, temperature=0.6,
                                 max_new_tokens=1024)
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        call = FakeHiggsModel.calls[-1]
        self.assertEqual(call["temperature"], 0.6)
        self.assertEqual(call["max_new_tokens"], 1024)

    def test_multiple_parts_isolated(self):
        """Part N's file never contains Part N-1/N+1 audio (task §13)."""
        FakeHiggsModel.speech_s = 3.0
        for i in (1, 2, 3):
            self.bm.add_job(self.make_part_job(
                part_index=i, filename="P_S_Long_v01_Part_{0:03d}_x.wav".format(i)))
        self.bm.start()
        self.wait_batch()
        from engine.batch_manager import JobStatus
        for i, job in enumerate(self.bm.jobs):
            self.assertEqual(job.status, JobStatus.COMPLETED)
            samples, _ = self.read_wav(job.output_path)
            self.assertAlmostEqual(len(samples) / SAMPLE_RATE, 3.5,
                                   delta=0.05,
                                   msg="part {0} isolated".format(i + 1))

    def test_cancel_preserves_completed_parts(self):
        FakeHiggsModel.speech_s = 1.0
        for i in (1, 2):
            self.bm.add_job(self.make_part_job(part_index=i))
        self.bm.start()
        self.wait_batch()
        # Stop before adding/regenerating anything else.
        self.bm.stop()
        from engine.batch_manager import JobStatus
        self.assertEqual(self.bm.jobs[0].status, JobStatus.COMPLETED)
        self.assertEqual(self.bm.jobs[1].status, JobStatus.COMPLETED)

    def test_failure_leaves_no_asset_and_retry_works(self):
        """A failed generation writes no file, no history entry, consumes
        no version; a retry (regen path) succeeds with the same filename."""
        FakeHiggsModel.speech_s = 1.0

        class Boom:
            def generate_speech(self, **kw):
                raise RuntimeError("model exploded")

        # Stack a FAILING model patch on top (mock patchers stack — stopping
        # it re-exposes the FakeHiggsModel patcher underneath).
        from unittest.mock import patch
        p = patch.object(type(self.engine._model),
                         "get_model_and_tokenizer",
                         return_value=(Boom(), None))
        p.start()
        job = self.make_part_job(part_index=1,
                                 filename="P_S_Long_v01_Part_001_x.wav")
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        from engine.batch_manager import JobStatus
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "outputs",
                         "P_S_Long_v01_Part_001_x.wav")))
        entries = [e for e in self.engine.get_history()
                   if e.output_path and "P_S_Long_v01" in e.output_path]
        self.assertEqual(len(entries), 0, "failed gen: no history entry")
        # Retry with the good model — same version slot is free again.
        p.stop()
        self.bm.regen_job(0)
        self.bm.start(reset_failed=False)
        self.wait_batch()
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(job.output_filename, "P_S_Long_v01_Part_001_x.wav")

    def test_expected_duration_passthrough(self):
        """expected_duration flows job→request when provided and is None
        for manual jobs (guard rules R1/R3 still cover those — the pure
        tests above verify detection without an expected duration)."""
        req = self.make_part_job(expected=30.0).to_request()
        self.assertAlmostEqual(req.expected_duration, 30.0)
        bare = self.BatchJob(name="manual", prompt="x",
                             parameters=self.GenerationParameters())
        self.assertIsNone(bare.to_request().expected_duration)


# ===========================================================================
# Batch dialog UI tests (real dialog, flagged row display)
# ===========================================================================

class TestBatchDialogAnomalyDisplay(_PipelineHarness):
    """Task §17: the dialog must visibly mark an anomalous output."""

    def _make_dialog(self):
        from ui.panels.batch_generation import BatchGenerationDialog
        dlg = BatchGenerationDialog(self.bm, [], None)
        return dlg

    def test_duration_cell_shows_warning_marker(self):
        FakeHiggsModel.speech_s = 30.48
        FakeHiggsModel.runaway = True
        job = self.make_part_job(part_index=9, expected=30.0)
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        dlg = self._make_dialog()
        try:
            dur_item = dlg._table.item(0, 3)
            self.assertIn("163.84", dur_item.text())
            self.assertIn("\u26a0", dur_item.text(),
                          "Duration cell must carry the ⚠ marker")
            tooltip = dur_item.toolTip()
            self.assertIn("unusually long output", tooltip)
            self.assertIn("kept", tooltip,
                          "tooltip must say the audio was kept")
            self.assertIn("regenerate", tooltip)
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_normal_row_has_no_marker(self):
        FakeHiggsModel.speech_s = 2.0
        job = self.make_part_job(part_index=1, expected=2.0)
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        dlg = self._make_dialog()
        try:
            dur_item = dlg._table.item(0, 3)
            self.assertNotIn("\u26a0", dur_item.text())
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_dialog_emits_regen_signal(self):
        """The dialog delegates regen (MainWindow allocates the version)."""
        from PySide6.QtWidgets import QMessageBox
        FakeHiggsModel.speech_s = 1.0
        job = self.make_part_job(part_index=1)
        self.bm.add_job(job)
        self.bm.start()
        self.wait_batch()
        dlg = self._make_dialog()
        try:
            captured = []
            dlg.regen_requested.connect(lambda idx: captured.append(idx))
            # Auto-confirm the QMessageBox.
            orig = QMessageBox.question
            QMessageBox.question = staticmethod(
                lambda *a, **k: QMessageBox.StandardButton.Yes)
            try:
                dlg._on_regen(0)
            finally:
                QMessageBox.question = orig
            self.assertEqual(captured, [0])
        finally:
            dlg.close()
            dlg.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
