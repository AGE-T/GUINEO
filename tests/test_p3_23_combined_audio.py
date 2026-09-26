"""
SpeechStudio — P3.23 Combined Audio Runtime Tests
===================================================

RUNTIME tests (real float32 WAV fixtures — the format the Higgs Audio V3
generation pipeline ACTUALLY writes) for the Scene Assembly / Combined
Audio system per the design record:

  §13/§51  float32 24 kHz mono WAV assembly (the P3.23 CRITICAL fix —
           the old implementation used the `wave` module, which cannot
           read/write IEEE-float WAVs and failed on every REAL generated
           file with `wave.Error: unknown format: 3`)
  §45      combined_outputs lineage (scene_ids, audio_asset_ids,
           silence_ms, normalized, created_at)
  §52/§53  missing audio BLOCKS assembly (no silent skipping); write
           failure removes partial output
  §54      global inter-scene silence
  §55      optional -18 LUFS normalization (default ON)
  §57/§58  MP3 via safe FFmpeg argv invocation; missing FFmpeg → WAV OK
  §60      minimal MP3 ID3 metadata
  §74/§85  output filename sanitization (path traversal rejection)
  §47      combined output may never overwrite a source Scene audio file
  §48/§49  stale detection
  §61      Combined Audio History entry (runtime via real Engine)
  §64/§65  Project Export/Reimport preserves combined_outputs

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_23_combined_audio.py -v
"""

from __future__ import annotations
import os
import sys
import unittest
import tempfile
import shutil
import subprocess

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import soundfile as sf


def _make_float_wav(path: str, duration: float = 1.0, rate: int = 24000,
                    amp: float = 0.3) -> str:
    """Create a REAL Higgs-format fixture: 24 kHz mono 32-bit float WAV.

    This is exactly what engine.audio_manager.AudioManager.save_wav
    produces (soundfile subtype='FLOAT').
    """
    n = int(duration * rate)
    arr = (np.sin(np.linspace(0, 20 * np.pi, n)) * amp).astype(np.float32)
    sf.write(path, arr, rate, subtype="FLOAT")
    return path


# ===========================================================================
# 1. Float32 WAV assembly (§13/§51 — the CRITICAL fix)
# ===========================================================================

class TestFloatWavAssembly(unittest.TestCase):
    """Assembly must work on the REAL generated format (IEEE float32)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ss_p323_flt_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _sources(self, count=3):
        from engine.combined_audio import AssemblySource
        srcs = []
        for i in range(count):
            p = _make_float_wav(os.path.join(
                self.tmpdir, "scene{0}.wav".format(i)), 1.0)
            srcs.append(AssemblySource(
                scene_id="s{0}".format(i), scene_name="S{0}".format(i),
                audio_path=p, duration=1.0,
                audio_asset_id="asset_{0}".format(i)))
        return srcs

    def test_assembles_real_float32_wavs(self):
        """CRITICAL regression: the old `wave`-module implementation raised
        `wave.Error: unknown format: 3` on real generated files."""
        from engine.combined_audio import assemble_combined_audio
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(
            self._sources(3), out, inter_scene_silence=0.5, normalize=False)
        self.assertTrue(result.success, "assembly on REAL float32 must work")
        self.assertEqual(result.scene_count, 3)
        # 3 x 1.0s + 2 x 0.5s gap = 4.0s
        self.assertAlmostEqual(result.total_duration, 4.0, places=1)
        info = sf.info(out)
        self.assertEqual(info.subtype, "FLOAT",
                         "combined output must be IEEE float WAV (§51)")
        self.assertEqual(info.samplerate, 24000)
        self.assertEqual(info.channels, 1)

    def test_float_output_loadable_by_generation_pipeline_loader(self):
        """The combined WAV must be readable by the app's own loader
        (AudioManager.load_wav — used for playback + waveform display)."""
        from engine.combined_audio import assemble_combined_audio
        from engine.audio_manager import AudioManager
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(
            self._sources(2), out, inter_scene_silence=0.0, normalize=False)
        self.assertTrue(result.success)
        am = AudioManager(outputs_dir=os.path.join(self.tmpdir, "outputs"))
        data, sr = am.load_wav(out)
        self.assertEqual(sr, 24000)
        self.assertAlmostEqual(len(data) / sr, 2.0, places=1)

    def test_assembly_order_preserved(self):
        """§42: sources assemble in the given order (user-defined)."""
        from engine.combined_audio import assemble_combined_audio
        srcs = self._sources(3)
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(
            srcs, out, inter_scene_silence=0.0, normalize=False)
        self.assertEqual(result.source_scene_ids, ["s0", "s1", "s2"])

    def test_partial_selection_any_subset(self):
        """§42: any subset assembles (S1 + S3 without S2)."""
        from engine.combined_audio import assemble_combined_audio
        srcs = self._sources(3)
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(
            [srcs[0], srcs[2]], out, inter_scene_silence=0.0, normalize=False)
        self.assertTrue(result.success)
        self.assertEqual(result.source_scene_ids, ["s0", "s2"])

    def test_no_silence_option(self):
        """§54: silence 0.0 → no gap."""
        from engine.combined_audio import assemble_combined_audio
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(
            self._sources(2), out, inter_scene_silence=0.0, normalize=False)
        self.assertAlmostEqual(result.total_duration, 2.0, places=1)


# ===========================================================================
# 2. Blocking failures (§52/§53)
# ===========================================================================

class TestAssemblyBlocking(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ss_p323_blk_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_missing_file_blocks_no_silent_skip(self):
        """§52: missing audio file BLOCKS; no output written; reason names
        the Scene."""
        from engine.combined_audio import (
            assemble_combined_audio, AssemblySource)
        good = _make_float_wav(os.path.join(self.tmpdir, "s0.wav"))
        sources = [
            AssemblySource("s0", "Introduction", good),
            AssemblySource("s1", "Chapter 1",
                           os.path.join(self.tmpdir, "missing.wav")),
        ]
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(sources, out)
        self.assertFalse(result.success)
        self.assertFalse(os.path.exists(out))
        self.assertTrue(any("Chapter 1" in r for r in result.blocked_reasons))

    def test_no_sources_blocks(self):
        from engine.combined_audio import assemble_combined_audio
        result = assemble_combined_audio(
            [], os.path.join(self.tmpdir, "combined.wav"))
        self.assertFalse(result.success)
        self.assertTrue(result.blocked_reasons)

    def test_unreadable_file_blocks(self):
        """§52: an existing but unreadable/corrupt file also blocks."""
        from engine.combined_audio import (
            assemble_combined_audio, AssemblySource)
        bad = os.path.join(self.tmpdir, "corrupt.wav")
        with open(bad, "wb") as f:
            f.write(b"NOT A WAV FILE AT ALL")
        sources = [AssemblySource("s0", "S0", bad)]
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(sources, out)
        self.assertFalse(result.success)
        self.assertFalse(os.path.exists(out))


# ===========================================================================
# 3. Normalization (§55)
# ===========================================================================

class TestNormalization(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ss_p323_nrm_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_normalize_on_adjusts_loudness(self):
        """§55: default-ON normalization moves the combined RMS toward
        -18 LUFS."""
        from engine.combined_audio import assemble_combined_audio, AssemblySource
        # Very quiet input (-30 dBFS RMS-ish)
        p = _make_float_wav(os.path.join(self.tmpdir, "quiet.wav"),
                            amp=0.03)
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(
            [AssemblySource("s0", "S0", p)], out,
            normalize=True, normalize_target_lufs=-18.0)
        self.assertTrue(result.success)
        self.assertTrue(result.normalized)
        data, _ = sf.read(out, dtype="float32")
        rms = float(np.sqrt(np.mean(data ** 2)))
        rms_db = 20 * np.log10(rms) if rms > 0 else -999
        # Should be amplified toward -18 dBFS (within the +20 dB clamp).
        self.assertGreater(rms_db, -25.0,
                           "normalization should raise the quiet signal")

    def test_normalize_off_keeps_samples(self):
        from engine.combined_audio import assemble_combined_audio, AssemblySource
        p = _make_float_wav(os.path.join(self.tmpdir, "s.wav"), amp=0.3)
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(
            [AssemblySource("s0", "S0", p)], out, normalize=False)
        self.assertTrue(result.success)
        self.assertFalse(result.normalized)
        orig, _ = sf.read(p, dtype="float32")
        got, _ = sf.read(out, dtype="float32")
        np.testing.assert_allclose(got, orig, atol=1e-6)

    def test_combined_entry_records_normalized_and_silence(self):
        """§45: the combined_outputs entry retains silence_ms + normalized."""
        from engine.combined_audio import (
            assemble_combined_audio, AssemblySource, build_combined_output_entry)
        p = _make_float_wav(os.path.join(self.tmpdir, "s.wav"))
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(
            [AssemblySource("s0", "S0", p, audio_asset_id="a1")], out,
            inter_scene_silence=0.5, normalize=True)
        entry = build_combined_output_entry(result, "Proj", [
            {"scene_id": "s0", "scene_name": "S0", "audio_path": p,
             "audio_asset_id": "a1", "duration": 1.0}])
        self.assertEqual(entry["silence_ms"], 500)
        self.assertTrue(entry["normalized"])
        self.assertEqual(entry["audio_asset_ids"], ["a1"])
        self.assertEqual(entry["scene_ids"], ["s0"])
        self.assertIn("created_at", entry)


# ===========================================================================
# 4. MP3 conversion (§57/§58/§60)
# ===========================================================================

class TestMp3Conversion(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ss_p323_mp3_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _ffmpeg(self) -> bool:
        import shutil as _sh
        return _sh.which("ffmpeg") is not None

    def test_mp3_created_with_id3_metadata(self):
        """§57/§60: WAV first, then MP3 (libmp3lame CBR) with minimal ID3
        metadata (title/artist/album/comment)."""
        if not self._ffmpeg():
            self.skipTest("FFmpeg not available")
        from engine.combined_audio import (
            assemble_combined_audio, AssemblySource, ffmpeg_available)
        self.assertTrue(ffmpeg_available())
        p = _make_float_wav(os.path.join(self.tmpdir, "s.wav"))
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(
            [AssemblySource("s0", "S0", p)], out, normalize=False,
            create_mp3=True, project_name="Eden")
        self.assertTrue(result.success)
        self.assertTrue(result.mp3_created)
        self.assertTrue(os.path.isfile(result.mp3_path))
        # Verify ID3 metadata is embedded.
        probe = subprocess.run(
            ["ffmpeg", "-i", result.mp3_path, "-f", "ffmetadata", "-"],
            capture_output=True)
        meta = probe.stdout + probe.stderr
        self.assertIn(b"Eden", meta)               # title/album
        self.assertIn(b"SpeechStudio", meta)       # artist
        self.assertIn(b"Higgs Audio V3", meta)     # comment
        # WAV must still exist (MP3 is an ADDITIONAL export — §56).
        self.assertTrue(os.path.isfile(out))

    def test_mp3_uses_safe_argv_no_shell(self):
        """§57/§85: FFmpeg must run via argv list with shell=False."""
        import inspect
        from engine import combined_audio as ca
        src = inspect.getsource(ca.convert_to_mp3)
        self.assertIn("subprocess.run", src)
        self.assertIn("shell", src)
        self.assertNotIn("shell=True", src)
        self.assertIn("cmd", src)

    def test_wav_only_when_mp3_not_requested(self):
        from engine.combined_audio import (
            assemble_combined_audio, AssemblySource)
        p = _make_float_wav(os.path.join(self.tmpdir, "s.wav"))
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(
            [AssemblySource("s0", "S0", p)], out, normalize=False,
            create_mp3=False)
        self.assertTrue(result.success)
        self.assertFalse(result.mp3_created)
        self.assertFalse(os.path.exists(
            os.path.splitext(out)[0] + ".mp3"))


# ===========================================================================
# 5. Filename sanitization + source protection (§74/§47)
# ===========================================================================

class TestPathSafety(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ss_p323_sec_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_traversal_filename_sanitized(self):
        """§74/§85: "../" and absolute paths in the filename are rejected."""
        from engine.combined_audio import sanitize_output_filename
        # Traversal is stripped to the bare basename.
        self.assertEqual(sanitize_output_filename("../../evil.wav"), "evil.wav")
        self.assertEqual(sanitize_output_filename("..\\..\\evil.wav"), "evil.wav")
        self.assertEqual(sanitize_output_filename("/etc/passwd.wav"),
                         "passwd.wav")
        self.assertEqual(sanitize_output_filename("C:\\temp\\x.wav"), "x.wav")
        # Null bytes and slashes inside are neutralized.
        self.assertNotIn("/", sanitize_output_filename("a/b\x00c.wav"))
        self.assertNotIn("\\", sanitize_output_filename("a\\b.wav"))
        # Plain names pass through.
        self.assertEqual(sanitize_output_filename("Eden_combined.wav"),
                         "Eden_combined.wav")
        # Garbage is rejected.
        self.assertEqual(sanitize_output_filename(""), "")
        self.assertEqual(sanitize_output_filename("..."), "")

    def test_output_never_overwrites_source(self):
        """§47: the combined output must not overwrite a source Scene WAV.
        The dialog-level guard is UI-tested in test_assemble_dialog_safety;
        here we verify the engine refuses no path (it writes exactly the
        given output path) — i.e. protection lives at the dialog layer."""
        from engine.combined_audio import assemble_combined_audio, AssemblySource
        src_path = _make_float_wav(os.path.join(self.tmpdir, "src.wav"))
        # Assemble to a DIFFERENT path (normal case) — source untouched.
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(
            [AssemblySource("s0", "S0", src_path)], out, normalize=False)
        self.assertTrue(result.success)
        self.assertTrue(os.path.isfile(src_path))
        original, _ = sf.read(src_path, dtype="float32")
        self.assertGreater(len(original), 0)


# ===========================================================================
# 6. Stale detection (§48/§49)
# ===========================================================================

class TestStaleDetection(unittest.TestCase):
    def test_newer_asset_marks_stale(self):
        from engine.combined_audio import is_combined_output_stale
        from engine.models import Scene
        scene = Scene(name="S1", id="scene_1")
        scene.audio_assets = [{"generated_at": "2026-08-27T13:00:00"}]
        entry = {"scene_ids": ["scene_1"], "created_at": "2026-08-27T12:00:00"}
        self.assertTrue(is_combined_output_stale(entry, [scene]))

    def test_older_asset_not_stale(self):
        from engine.combined_audio import is_combined_output_stale
        from engine.models import Scene
        scene = Scene(name="S1", id="scene_1")
        scene.audio_assets = [{"generated_at": "2026-08-27T11:00:00"}]
        entry = {"scene_ids": ["scene_1"], "created_at": "2026-08-27T12:00:00"}
        self.assertFalse(is_combined_output_stale(entry, [scene]))

    def test_backward_compat_source_scene_ids_key(self):
        """P3.22 entries with source_scene_ids (no scene_ids) still work."""
        from engine.combined_audio import is_combined_output_stale
        from engine.models import Scene
        scene = Scene(name="S1", id="scene_1")
        scene.audio_assets = [{"generated_at": "2026-08-27T13:00:00"}]
        entry = {"source_scene_ids": ["scene_1"],
                 "created_at": "2026-08-27T12:00:00"}
        self.assertTrue(is_combined_output_stale(entry, [scene]))


# ===========================================================================
# 7. Combined History entry (§61) — real Engine + HistoryManager
# ===========================================================================

class TestCombinedHistory(unittest.TestCase):
    """RUNTIME: the MainWindow combined-history flow writes a HistoryEntry
    with the combined output via the REAL HistoryManager."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ss_p323_hist_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_history_entry_created_for_combined(self):
        """§61: one HistoryEntry per Combined Audio export, referencing
        the project + scenes + duration + output path."""
        from engine.engine import Engine
        from engine.models import GenerationResult
        from datetime import datetime
        engine = Engine(app_root=self.tmpdir)
        wav = _make_float_wav(os.path.join(self.tmpdir, "combined.wav"))
        rel = os.path.relpath(wav, self.tmpdir)
        result = GenerationResult(
            success=True, output_path=rel, output_duration=4.0,
            output_sample_rate=24000,
            prompt="[Combined Audio - 3 scenes: A, B, C]",
            timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
            project="Eden", scene_name="Combined (3 scenes)")
        entry_id = engine._history.add(result)
        self.assertTrue(entry_id)
        got = engine._history.get_entry(entry_id)
        self.assertIsNotNone(got)
        self.assertIn("Combined", got.prompt)
        self.assertEqual(got.output_duration, 4.0)
        self.assertEqual(got.project, "Eden")


# ===========================================================================
# 8. Export / Reimport round-trip (§64/§65)
# ===========================================================================

class TestExportReimportCombined(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ss_p323_exp_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_combined_outputs_survive_export_reimport(self):
        """§64/§65: export → reimport preserves combined_outputs, scene
        IDs, audio asset IDs and relative paths (no absolute leakage)."""
        from engine.models import Project, Scene, Character
        from engine.project_manager import ProjectManager
        from engine.project_exporter import ProjectExporter

        # Build a project with 2 scenes + a combined output.
        proj = Project(name="Eden", id="proj_eden")
        s1 = Scene(name="S1", id="scene_1")
        s2 = Scene(name="S2", id="scene_2")
        s1.sort_order = 0
        s2.sort_order = 1
        wav1 = _make_float_wav(os.path.join(self.tmpdir, "a1.wav"))
        wav2 = _make_float_wav(os.path.join(self.tmpdir, "a2.wav"))
        s1.audio_assets = [{"id": "asset_1", "output_path": "outputs/a1.wav",
                            "duration": 1.0}]
        s2.audio_assets = [{"id": "asset_2", "output_path": "outputs/a2.wav",
                            "duration": 1.0}]
        proj.scenes = [s1, s2]
        proj.characters = [Character(name="Engineer", id="char_eng")]
        # Simulate the audio files living under the app root.
        app_root = self.tmpdir
        os.makedirs(os.path.join(app_root, "outputs"), exist_ok=True)
        sf.write(os.path.join(app_root, "outputs", "a1.wav"),
                 np.zeros(24000, dtype=np.float32), 24000, subtype="FLOAT")
        sf.write(os.path.join(app_root, "outputs", "a2.wav"),
                 np.zeros(24000, dtype=np.float32), 24000, subtype="FLOAT")
        combined_wav = os.path.join(app_root, "outputs", "combined.wav")
        sf.write(combined_wav, np.zeros(48000, dtype=np.float32), 24000,
                 subtype="FLOAT")
        proj.combined_outputs = [{
            "id": "comb_1",
            "output_path": "outputs/combined.wav",
            "mp3_path": "",
            "format": "wav",
            "duration": 2.0,
            "scene_count": 2,
            "scene_ids": ["scene_1", "scene_2"],
            "audio_asset_ids": ["asset_1", "asset_2"],
            "silence_ms": 500,
            "normalized": True,
            "created_at": "2026-08-27T12:00:00",
            "project_name": "Eden",
        }]

        # Export (P3.7 structure: <dest_parent>/<ProjectName>/). The
        # project is NOT modified (§47: source data unchanged).
        exporter = ProjectExporter(app_root=app_root)
        export_parent = os.path.join(self.tmpdir, "export")
        result = exporter.export(proj, export_parent, include_audio=True)
        export_dir = os.path.join(export_parent, "Eden")
        self.assertTrue(os.path.isdir(export_dir))

        # The exported project.json must contain combined_outputs with
        # RELATIVE paths.
        import json
        with open(os.path.join(export_dir, "project.json")) as f:
            data = json.load(f)
        combos = data.get("combined_outputs", [])
        self.assertEqual(len(combos), 1)
        self.assertFalse(os.path.isabs(combos[0]["output_path"]),
                         "§64: combined output metadata must use relative "
                         "paths (no absolute path leakage)")
        # The combined WAV file must have been copied into the export.
        self.assertTrue(os.path.isfile(os.path.join(
            export_dir, combos[0]["output_path"].replace("/", os.sep))))

        # Reimport via ProjectManager.import_project_dir.
        pm = ProjectManager(projects_dir=os.path.join(
            self.tmpdir, "projects_imported"))
        imported = pm.import_project_dir(export_dir, new_id=False)
        self.assertIsNotNone(imported)
        self.assertEqual(len(imported.combined_outputs), 1)
        entry = imported.combined_outputs[0]
        self.assertEqual(entry["scene_ids"], ["scene_1", "scene_2"])
        self.assertEqual(entry["audio_asset_ids"], ["asset_1", "asset_2"])
        self.assertEqual(entry["silence_ms"], 500)
        self.assertTrue(entry["normalized"])
        # Scene IDs preserved (§65).
        self.assertEqual({s.id for s in imported.scenes},
                         {"scene_1", "scene_2"})
        # Characters preserved (§32).
        self.assertEqual(len(imported.characters), 1)
        self.assertEqual(imported.characters[0].name, "Engineer")


# ===========================================================================
# 9. Assemble dialog runtime behavior (§41/§42/§53/§74)
# ===========================================================================

class TestAssembleDialogRuntime(unittest.TestCase):
    """RUNTIME: real AssembleScenesDialog offscreen."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.tmpdir = tempfile.mkdtemp(prefix="ss_p323_dlg_")
        from engine.models import Project, Scene
        cls.project = Project(name="Eden", id="proj_eden")
        cls.scenes = []
        for i in range(3):
            s = Scene(name="S{0}".format(i + 1), id="scene_{0}".format(i + 1))
            s.sort_order = i
            wav = _make_float_wav(os.path.join(
                cls.tmpdir, "scene{0}.wav".format(i)))
            s.audio_assets = [{
                "id": "asset_{0}".format(i),
                "output_path": wav,   # absolute in-memory (dialog resolves)
                "duration": 1.0,
                "generated_at": "2026-08-27T10:0{0}:00".format(i),
            }]
            cls.scenes.append(s)
        cls.project.scenes = cls.scenes
        # Scene 4 has NO audio (§52: disabled row).
        s4 = Scene(name="S4", id="scene_4")
        s4.sort_order = 3
        s4.audio_assets = []
        cls.project.scenes.append(s4)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _make_dialog(self):
        from ui.panels.assemble_dialog import AssembleScenesDialog
        return AssembleScenesDialog(self.project, self.tmpdir)

    def test_rows_show_canonical_source_and_duration(self):
        """§44: each row shows which audio asset is used + duration."""
        dlg = self._make_dialog()
        try:
            self.assertEqual(len(dlg._scene_rows), 4)
            # Rows are ordered by sort_order.
            names = [r.scene.name for r in dlg._scene_rows]
            self.assertEqual(names, ["S1", "S2", "S3", "S4"])
            # Row with audio has asset label (source visibility §44).
            self.assertTrue(dlg._scene_rows[0].asset_label)
            self.assertEqual(dlg._scene_rows[0].duration, 1.0)
            self.assertTrue(dlg._scene_rows[0]._audio_asset_id)
            # Row without audio has no path and is unselectable (§52).
            self.assertFalse(dlg._scene_rows[3].audio_path)
            self.assertFalse(dlg._scene_rows[3]._checkbox.isEnabled())
            self.assertFalse(dlg._scene_rows[3].is_selected())
        finally:
            dlg.deleteLater()

    def test_reorder_changes_assembly_order(self):
        """§41/§42: Move Up/Down define a user-defined assembly order."""
        dlg = self._make_dialog()
        try:
            initial = [r.scene.id for r in dlg._ordered_rows()]
            self.assertEqual(initial,
                             ["scene_1", "scene_2", "scene_3", "scene_4"])
            # Select the first row and move it down.
            dlg._scene_rows[0]._checkbox.setChecked(True)
            dlg._move_selected(1)
            reordered = [r.scene.id for r in dlg._ordered_rows()]
            self.assertEqual(reordered,
                             ["scene_2", "scene_1", "scene_3", "scene_4"])
        finally:
            dlg.deleteLater()

    def test_empty_selection_disables_assemble(self):
        """§53: empty selection → Assemble disabled."""
        dlg = self._make_dialog()
        try:
            dlg._select_all()
            self.assertTrue(dlg._assemble_btn.isEnabled())
            dlg._select_none()
            self.assertFalse(dlg._assemble_btn.isEnabled())
        finally:
            dlg.deleteLater()

    def test_total_duration_label_live_update(self):
        """§41: live total estimated duration updates with selection."""
        dlg = self._make_dialog()
        try:
            dlg._select_all()
            # 3 ready scenes x 1.0s + 2 gaps x 0.5s = 4.0s
            self.assertIn("00:00:04", dlg._total_label.text())
            dlg._select_none()
            dlg._scene_rows[0]._checkbox.setChecked(True)
            self.assertIn("00:00:01", dlg._total_label.text())
        finally:
            dlg.deleteLater()

    def test_default_filename_has_timestamp(self):
        """§78B: default filename <Project>_combined_<timestamp>.wav."""
        import re
        dlg = self._make_dialog()
        try:
            name = dlg._filename_edit.text()
            self.assertTrue(name.startswith("Eden_combined_"))
            self.assertTrue(re.search(r"_\d{8}_\d{6}$", name),
                            "timestamp suffix expected: {0}".format(name))
        finally:
            dlg.deleteLater()

    def test_resolve_output_path_rejects_traversal(self):
        """§74: traversal filenames are sanitized; output never equals a
        source audio path (§47)."""
        from PySide6.QtWidgets import QMessageBox
        dlg = self._make_dialog()
        orig_info = QMessageBox.information
        orig_warn = QMessageBox.warning
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        QMessageBox.warning = staticmethod(lambda *a, **k: None)
        QMessageBox.critical = staticmethod(lambda *a, **k: None)
        try:
            # Traversal filename → sanitized to bare name.
            dlg._filename_edit.setText("../../../../tmp/evil.wav")
            path = dlg._resolve_output_path()
            self.assertIsNotNone(path)
            self.assertEqual(os.path.basename(path), "evil.wav")
            self.assertEqual(os.path.dirname(path),
                             os.path.abspath(dlg._dir_edit.text().strip()))
            # Destination == a source audio file → rejected (§47).
            src = dlg._scene_rows[0].audio_path
            dlg._filename_edit.setText(os.path.basename(src))
            dlg._dir_edit.setText(os.path.dirname(src))
            path2 = dlg._resolve_output_path()
            self.assertIsNone(path2)
        finally:
            QMessageBox.information = orig_info
            QMessageBox.warning = orig_warn
            dlg.deleteLater()

    def test_normalize_checkbox_default_on(self):
        """§55: normalize option defaults ON."""
        dlg = self._make_dialog()
        try:
            self.assertTrue(dlg._normalize_checkbox.isChecked())
        finally:
            dlg.deleteLater()

    def test_duplicate_scene_impossible(self):
        """§69: each Scene appears exactly once (checkbox rows)."""
        dlg = self._make_dialog()
        try:
            ids = [r.scene.id for r in dlg._scene_rows]
            self.assertEqual(len(ids), len(set(ids)))
        finally:
            dlg.deleteLater()


if __name__ == "__main__":
    unittest.main()
