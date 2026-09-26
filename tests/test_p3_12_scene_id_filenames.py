"""
SpeechStudio — P3.12/P3.17/P3.27B Scene ID + Human-Friendly Audio Filename Tests
==========================================================================

Tests that every generated audio filename:
  1. Contains the Scene ID (compact suffix for uniqueness)
  2. Contains the Project name (human-readable)
  3. Contains the Scene name (human-readable)
  4. Contains the Part number (zero-padded)
  5. Multi-speaker filenames include the speaker
  6. Same-name Scenes produce distinct filenames
  7. Names are normalized for Windows filesystem compatibility
  8. (P3.27B) Generate Long parts carry a VERSION slot (Long_vNN) so a
     regenerated part never overwrites the previous good version

P3.27B UPDATE (task §18): the P3.12/P3.17 convention
    <Project>_<Scene>_Part_NN[_<speaker>][_<scene8>].wav
was explicitly superseded by the versioned convention
    <Project>_<Scene>_Long_vNN_Part_NNN[_<speaker>][_<scene8>].wav
(the same single mechanism planned by the P3.27 design record, Rec 2/6).
The tests below encode the new contract.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_12_scene_id_filenames.py -v
"""

from __future__ import annotations
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class TestSceneIdInFilenames(unittest.TestCase):
    """P3.12/P3.17/P3.27B: Generated audio filenames must contain Scene ID
    + human-readable names + a version slot."""

    def test_filename_format_contains_project_scene_part(self):
        """The filename builder must include Project_Scene_Long_vNN_Part."""
        # The production builder lives in engine/output_guard.py
        # (P3.27B); main_window calls it via next_free_part_filename.
        from engine.output_guard import build_part_filename
        name = build_part_filename("Eden", "Scene 03", 9, 1,
                                   speaker="Engineer",
                                   scene_id="ab12cd34ef56")
        self.assertEqual(name, "Eden_Scene_03_Long_v01_Part_009_Engineer_ab12cd34.wav")
        self.assertIn("Long_v01", name, "Version slot required (P3.27B)")
        self.assertIn("Part_009", name, "Part must be zero-padded NNN")
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("next_free_part_filename", src,
                      "MainWindow must allocate via the disk-guarded builder")
        # Old unversioned format must be gone
        self.assertNotIn('"Part_{2:02d}"', src,
                         "Old unversioned format must be removed")

    def test_filename_normalization_exists(self):
        """Filename normalization must exist for Windows compatibility."""
        with open(os.path.join(_ROOT, "engine/output_guard.py")) as f:
            src = f.read()
        self.assertIn("normalize_name_component", src)
        self.assertIn(r'[\\/:*?"<>|\s]+', src,
                      "Must replace Windows-invalid characters")

    def test_scene_id_suffix_for_uniqueness(self):
        """Filename must include a compact scene_id suffix for uniqueness."""
        from engine.output_guard import build_part_filename
        name = build_part_filename("Eden", "S03", 1, 1, scene_id="abc123de")
        self.assertIn("abc123de", name,
                      "Must include compact scene_id suffix")

    def test_scene_a_part1_contains_scene_a_id(self):
        """Scene A → Part 1 filename must contain Scene A's ID (compact)."""
        from engine.output_guard import build_part_filename
        scene_a_id = "abc123de"
        filename = build_part_filename("Eden", "Ciklonok", 1, 1,
                                       scene_id=scene_a_id)
        self.assertIn(scene_a_id, filename)
        self.assertIn("Eden", filename)
        self.assertIn("Ciklonok", filename)
        self.assertIn("Part_001", filename)

    def test_scene_b_part1_contains_scene_b_id(self):
        """Scene B → Part 1 filename must contain Scene B's ID."""
        from engine.output_guard import build_part_filename
        scene_b_id = "xyz789gh"
        filename = build_part_filename("Eden", "Vihar", 1, 1,
                                       scene_id=scene_b_id)
        self.assertIn(scene_b_id, filename)
        self.assertIn("Part_001", filename)

    def test_same_name_scenes_produce_distinct_filenames(self):
        """Two Scenes with identical names but different IDs produce distinct filenames."""
        from engine.output_guard import build_part_filename
        filename1 = build_part_filename("Eden", "Scene_01", 1, 1,
                                        scene_id="id_1111")
        filename2 = build_part_filename("Eden", "Scene_01", 1, 1,
                                        scene_id="id_2222")
        self.assertNotEqual(filename1, filename2,
                            "Same-name scenes must produce distinct filenames")

    def test_multi_speaker_filename_contains_speaker(self):
        """Multi-speaker filenames must include the speaker name."""
        from engine.output_guard import build_part_filename
        filename = build_part_filename(
            "Eden", "Ciklonok", 1, 1, speaker="Engineer",
            scene_id="abc123de")
        self.assertIn("Eden", filename)
        self.assertIn("Ciklonok", filename)
        self.assertIn("Part_001", filename)
        self.assertIn("Engineer", filename)
        self.assertIn("abc123de", filename)

    def test_versioned_regenerations_never_collide(self):
        """P3.27B §18: successive versions of the same part must differ."""
        from engine.output_guard import build_part_filename
        v1 = build_part_filename("Eden", "S03", 9, 1, scene_id="ab12cd34")
        v2 = build_part_filename("Eden", "S03", 9, 2, scene_id="ab12cd34")
        self.assertNotEqual(v1, v2)
        self.assertIn("Long_v01", v1)
        self.assertIn("Long_v02", v2)

    def test_no_scene_fallback(self):
        """When no active scene, filename should still work."""
        from engine.output_guard import build_part_filename
        filename = build_part_filename("Project", "Scene", 1, 1)
        self.assertEqual(filename, "Project_Scene_Long_v01_Part_001.wav")

    def test_filename_collision_handling_still_works(self):
        """Filename collision handling (unique suffix) must still work."""
        from engine.project_exporter import _unique_dir
        import tempfile, os, shutil
        tmpdir = tempfile.mkdtemp()
        try:
            base = "Eden_Ciklonok_Part_01_abc123de"
            os.makedirs(os.path.join(tmpdir, base))
            result = _unique_dir(tmpdir, base)
            self.assertTrue(result.endswith("_2"),
                            "Collision should produce _2 suffix: {0}".format(result))
        finally:
            shutil.rmtree(tmpdir)

    def test_audioasset_output_path_consistency(self):
        """AudioAsset.output_path must use the same filename format."""
        from engine.models import AudioAsset
        asset = AudioAsset(
            scene_id="scene_test",
            output_path="outputs/Eden_Ciklonok_Long_v01_Part_001_abc123de.wav",
            duration=2.0,
        )
        self.assertIn("Eden", asset.output_path)
        self.assertIn("Ciklonok", asset.output_path)
        self.assertIn("Part_001", asset.output_path)

    def test_history_entry_output_path_consistency(self):
        """HistoryEntry.output_path must use the same filename format."""
        from engine.history_manager import HistoryManager
        from engine.models import GenerationResult
        import tempfile, shutil
        tmpdir = tempfile.mkdtemp()
        try:
            hm = HistoryManager(tmpdir)
            r = GenerationResult(
                success=True,
                output_path="outputs/Eden_Ciklonok_Long_v01_Part_001_abc123de.wav",
                prompt="test",
                generation_time=1.0,
                output_duration=2.0,
            )
            r.timestamp = "20240101_120000"
            r.project = "Eden"
            entry_id = hm.add(r)
            entry = hm.get_entry(entry_id)
            self.assertIn("Eden", entry.output_path)
            self.assertIn("Part_001", entry.output_path)
        finally:
            shutil.rmtree(tmpdir)

    def test_windows_unsafe_chars_normalized(self):
        """Windows-unsafe characters must be normalized in filenames."""
        import re
        def _normalize(name):
            safe = re.sub(r'[\\/:*?"<>|\s]+', '_', name.strip())
            safe = re.sub(r'_+', '_', safe)
            safe = safe.strip('_.')
            return safe or "untitled"

        # Test various unsafe names
        self.assertEqual(_normalize("My Book: Part 1"), "My_Book_Part_1")
        self.assertEqual(_normalize("Chapter 03 / Storm"), "Chapter_03_Storm")
        self.assertEqual(_normalize('Test"Quote'), "Test_Quote")
        self.assertEqual(_normalize("  spaces  "), "spaces")
        self.assertEqual(_normalize(""), "untitled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
