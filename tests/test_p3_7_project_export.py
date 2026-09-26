"""
SpeechStudio — P3.7 Project Export Tests
=========================================

Tests for the Project Export feature:
  1. Empty Project export
  2. Single Scene export
  3. Multi Scene export
  4. Character export
  5. Scene → Character relationship preservation
  6. Generated AudioAsset export
  7. Include Audio ON
  8. Include Audio OFF
  9. Referenced Voice Asset export
  10. Missing audio warning
  11. Existing destination safety (overwrite + unique suffix)
  12. Relative path generation
  13. Export does not modify original Project
  14. Exported Project can be re-opened (round-trip)
  15. Exported Scene state matches original
  16. Exported Characters match original
  17. Exported AudioAsset metadata matches original
  18. Large multi-Scene Project export

Run:
    python3 -m pytest tests/test_p3_7_project_export.py -v
"""

from __future__ import annotations
import os
import sys
import json
import shutil
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ===========================================================================
# Test fixture helpers
# ===========================================================================

def _make_project(name="TestProject", num_scenes=2, num_chars=2,
                   with_audio=False, app_root=None):
    """Build a test Project with scenes, characters, and optional audio."""
    from engine.models import Project, Scene, Character, AudioAsset
    project = Project(name=name)
    for i in range(num_scenes):
        scene = Scene(name="Scene_{0:02d}".format(i + 1), status="draft")
        scene.text = "Text for scene {0}".format(i + 1)
        scene.emotion = "awe"
        scene.speed = "Normal"
        scene.project_id = project.id
        if with_audio and app_root:
            # Create a real audio file
            audio_dir = os.path.join(app_root, "outputs")
            os.makedirs(audio_dir, exist_ok=True)
            wav_name = "test_{0}_{1}.wav".format(project.id, scene.id)
            wav_path = os.path.join(audio_dir, wav_name)
            with open(wav_path, "wb") as f:
                f.write(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00")
            asset = {
                "id": "asset_{0}_{1}".format(scene.id, i),
                "scene_id": scene.id,
                "output_path": os.path.relpath(wav_path, app_root).replace(os.sep, "/"),
                "duration": 2.5,
                "speaker": None,
                "character_id": None,
                "voice_profile_id": None,
                "generated_at": "2024-01-01T12:00:00",
            }
            scene.audio_assets.append(asset)
        project.add_scene(scene)
    for i in range(num_chars):
        char = Character(name="Character_{0:02d}".format(i + 1),
                          role="protagonist" if i == 0 else "supporting")
        project.add_character(char)
        # Link char to first scene
        if project.scenes:
            project.scenes[0].character_ids.append(char.id)
    return project


# ===========================================================================
# Tests 1-3: Empty, Single, Multi Scene export
# ===========================================================================

class TestBasicExport(unittest.TestCase):
    """Tests for basic Project export structure."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.app_root = os.path.join(self.tmpdir, "app")
        os.makedirs(self.app_root, exist_ok=True)
        self.dest = os.path.join(self.tmpdir, "exports")
        os.makedirs(self.dest, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_project_export(self):
        """1. Empty Project (no scenes, no chars) exports valid structure."""
        from engine.project_exporter import ProjectExporter
        from engine.models import Project
        project = Project(name="Empty")
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=False,
                                  include_voice_assets=False)
        self.assertTrue(result.success, result.error)
        self.assertTrue(os.path.isfile(os.path.join(result.export_dir, "project.json")))
        self.assertTrue(os.path.isdir(os.path.join(result.export_dir, "scenes")))
        self.assertTrue(os.path.isdir(os.path.join(result.export_dir, "characters")))
        self.assertEqual(result.scenes_exported, 0)
        self.assertEqual(result.characters_exported, 0)

    def test_single_scene_export(self):
        """2. Single Scene exports with scene.json."""
        from engine.project_exporter import ProjectExporter
        project = _make_project(name="Single", num_scenes=1, num_chars=0)
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=False,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        self.assertEqual(result.scenes_exported, 1)
        # Check scene.json exists
        scenes_dir = os.path.join(result.export_dir, "scenes")
        scene_dirs = [d for d in os.listdir(scenes_dir) if os.path.isdir(os.path.join(scenes_dir, d))]
        self.assertEqual(len(scene_dirs), 1)
        scene_json = os.path.join(scenes_dir, scene_dirs[0], "scene.json")
        self.assertTrue(os.path.isfile(scene_json))

    def test_multi_scene_export(self):
        """3. Multi Scene export — each scene gets its own folder."""
        from engine.project_exporter import ProjectExporter
        project = _make_project(name="Multi", num_scenes=5, num_chars=0)
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=False,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        self.assertEqual(result.scenes_exported, 5)
        scenes_dir = os.path.join(result.export_dir, "scenes")
        scene_dirs = [d for d in os.listdir(scenes_dir) if os.path.isdir(os.path.join(scenes_dir, d))]
        self.assertEqual(len(scene_dirs), 5)


# ===========================================================================
# Tests 4-5: Character export + relationships
# ===========================================================================

class TestCharacterExport(unittest.TestCase):
    """Tests for Character export and relationship preservation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.app_root = os.path.join(self.tmpdir, "app")
        os.makedirs(self.app_root, exist_ok=True)
        self.dest = os.path.join(self.tmpdir, "exports")
        os.makedirs(self.dest, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_character_export(self):
        """4. Characters are exported to characters/<name>/character.json."""
        from engine.project_exporter import ProjectExporter
        project = _make_project(name="CharTest", num_scenes=1, num_chars=3)
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=False,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        self.assertEqual(result.characters_exported, 3)
        chars_dir = os.path.join(result.export_dir, "characters")
        char_dirs = [d for d in os.listdir(chars_dir) if os.path.isdir(os.path.join(chars_dir, d))]
        self.assertEqual(len(char_dirs), 3)
        # Each should have character.json
        for cd in char_dirs:
            self.assertTrue(os.path.isfile(
                os.path.join(chars_dir, cd, "character.json")))

    def test_scene_character_relationship_preserved(self):
        """5. Scene.character_ids survive export and match original."""
        from engine.project_exporter import ProjectExporter
        project = _make_project(name="RelTest", num_scenes=2, num_chars=2)
        # Record original relationships
        original_scene_char_ids = {s.id: list(s.character_ids) for s in project.scenes}
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=False,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        # Read project.json and verify relationships
        with open(os.path.join(result.export_dir, "project.json"), "r") as f:
            data = json.load(f)
        for scene_data in data["scenes"]:
            orig_ids = original_scene_char_ids.get(scene_data["id"], [])
            self.assertEqual(scene_data["character_ids"], orig_ids)


# ===========================================================================
# Tests 6-8: AudioAsset export + include_audio toggle
# ===========================================================================

class TestAudioExport(unittest.TestCase):
    """Tests for generated audio packaging."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.app_root = os.path.join(self.tmpdir, "app")
        os.makedirs(self.app_root, exist_ok=True)
        self.dest = os.path.join(self.tmpdir, "exports")
        os.makedirs(self.dest, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_generated_audio_exported(self):
        """6. Generated AudioAsset is copied to scene's audio/ folder."""
        from engine.project_exporter import ProjectExporter
        project = _make_project(name="AudioTest", num_scenes=2, num_chars=0,
                                 with_audio=True, app_root=self.app_root)
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=True,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        self.assertEqual(result.audio_files_copied, 2)
        # Verify audio files exist in scene dirs
        scenes_dir = os.path.join(result.export_dir, "scenes")
        for sd in os.listdir(scenes_dir):
            audio_dir = os.path.join(scenes_dir, sd, "audio")
            self.assertTrue(os.path.isdir(audio_dir))
            files = os.listdir(audio_dir)
            self.assertGreaterEqual(len(files), 1)

    def test_include_audio_on(self):
        """7. include_audio=True copies audio files."""
        from engine.project_exporter import ProjectExporter
        project = _make_project(name="AudioOn", num_scenes=1, num_chars=0,
                                 with_audio=True, app_root=self.app_root)
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=True,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        self.assertGreater(result.audio_files_copied, 0)

    def test_include_audio_off(self):
        """8. include_audio=False does NOT copy audio files."""
        from engine.project_exporter import ProjectExporter
        project = _make_project(name="AudioOff", num_scenes=1, num_chars=0,
                                 with_audio=True, app_root=self.app_root)
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=False,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        self.assertEqual(result.audio_files_copied, 0)
        # No audio/ folders should exist
        scenes_dir = os.path.join(result.export_dir, "scenes")
        for sd in os.listdir(scenes_dir):
            audio_dir = os.path.join(scenes_dir, sd, "audio")
            self.assertFalse(os.path.exists(audio_dir),
                              "audio/ dir should not exist when include_audio=False")


# ===========================================================================
# Test 9: Voice reference asset export
# ===========================================================================

class TestVoiceAssetExport(unittest.TestCase):
    """Tests for referenced voice asset packaging."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.app_root = os.path.join(self.tmpdir, "app")
        os.makedirs(self.app_root, exist_ok=True)
        # Create a voice profile directory with reference audio
        voices_dir = os.path.join(self.app_root, "voices", "vp_test123")
        os.makedirs(voices_dir, exist_ok=True)
        ref_wav = os.path.join(voices_dir, "reference.wav")
        with open(ref_wav, "wb") as f:
            f.write(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00")
        self.dest = os.path.join(self.tmpdir, "exports")
        os.makedirs(self.dest, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_voice_asset_exported(self):
        """9. Referenced voice assets are copied to references/voices/."""
        from engine.project_exporter import ProjectExporter
        from engine.models import Project, Scene, Character, VoiceProfile

        project = Project(name="VoiceTest")
        scene = Scene(name="Scene 1")
        project.add_scene(scene)
        char = Character(name="Narrator", voice_profile_id="vp_test123")
        project.add_character(char)
        scene.character_ids.append(char.id)

        # Voice lookup returns a VoiceProfile
        def voice_lookup(vid):
            if vid == "vp_test123":
                return VoiceProfile(
                    id="vp_test123",
                    name="Test Voice",
                    reference_audio_path="voices/vp_test123/reference.wav",
                    reference_transcript="hello world",
                    sample_rate=24000,
                    duration=1.5,
                )
            return None

        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=False,
                                  include_voice_assets=True,
                                  voice_lookup=voice_lookup)
        self.assertTrue(result.success)
        self.assertEqual(result.voice_assets_copied, 1)
        # Verify references/voices/ exists
        refs_dir = os.path.join(result.export_dir, "references", "voices")
        self.assertTrue(os.path.isdir(refs_dir))
        vp_dirs = os.listdir(refs_dir)
        self.assertEqual(len(vp_dirs), 1)
        vp_dir = os.path.join(refs_dir, vp_dirs[0])
        self.assertTrue(os.path.isfile(os.path.join(vp_dir, "voice.json")))
        self.assertTrue(os.path.isfile(os.path.join(vp_dir, "reference.wav")))
        self.assertTrue(os.path.isfile(os.path.join(vp_dir, "transcript.txt")))


# ===========================================================================
# Test 10: Missing audio warning
# ===========================================================================

class TestMissingAudioWarning(unittest.TestCase):
    """Tests that missing audio files produce warnings, not failures."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.app_root = os.path.join(self.tmpdir, "app")
        os.makedirs(self.app_root, exist_ok=True)
        self.dest = os.path.join(self.tmpdir, "exports")
        os.makedirs(self.dest, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_missing_audio_produces_warning(self):
        """10. Missing audio file produces a warning, export still succeeds."""
        from engine.project_exporter import ProjectExporter
        from engine.models import Project, Scene
        project = Project(name="MissingAudio")
        scene = Scene(name="Scene 1")
        # Add an audio asset pointing to a non-existent file
        scene.audio_assets.append({
            "id": "asset1",
            "scene_id": scene.id,
            "output_path": "outputs/nonexistent.wav",
            "duration": 1.0,
        })
        project.add_scene(scene)
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=True,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        self.assertEqual(result.audio_files_copied, 0)
        self.assertGreaterEqual(len(result.warnings), 1)
        self.assertTrue(any("Missing audio" in w or "nonexistent" in w
                            for w in result.warnings),
                        "Warnings should mention the missing audio")


# ===========================================================================
# Test 11: Existing destination safety
# ===========================================================================

class TestDestinationSafety(unittest.TestCase):
    """Tests for overwrite safety."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.app_root = os.path.join(self.tmpdir, "app")
        os.makedirs(self.app_root, exist_ok=True)
        self.dest = os.path.join(self.tmpdir, "exports")
        os.makedirs(self.dest, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_unique_suffix_when_not_overwriting(self):
        """11a. When overwrite=False and dest exists, a unique _2 suffix is used."""
        from engine.project_exporter import ProjectExporter
        project = _make_project(name="MyProj", num_scenes=1, num_chars=0)
        exporter = ProjectExporter(self.app_root)
        # First export
        r1 = exporter.export(project, self.dest, include_audio=False,
                              include_voice_assets=False, overwrite=False)
        self.assertTrue(r1.success)
        self.assertTrue(os.path.basename(r1.export_dir) == "MyProj")
        # Second export — should get MyProj_2
        r2 = exporter.export(project, self.dest, include_audio=False,
                              include_voice_assets=False, overwrite=False)
        self.assertTrue(r2.success)
        self.assertTrue(os.path.basename(r2.export_dir) == "MyProj_2")

    def test_overwrite_removes_existing(self):
        """11b. When overwrite=True, existing dir is replaced."""
        from engine.project_exporter import ProjectExporter
        project = _make_project(name="Overwrite", num_scenes=1, num_chars=0)
        exporter = ProjectExporter(self.app_root)
        # First export
        r1 = exporter.export(project, self.dest, include_audio=False,
                              include_voice_assets=False, overwrite=False)
        self.assertTrue(r1.success)
        # Create a marker file that should be removed on overwrite
        marker = os.path.join(r1.export_dir, "marker.txt")
        with open(marker, "w") as f:
            f.write("should be deleted")
        # Second export with overwrite=True
        r2 = exporter.export(project, self.dest, include_audio=False,
                              include_voice_assets=False, overwrite=True)
        self.assertTrue(r2.success)
        self.assertEqual(r2.export_dir, r1.export_dir)
        self.assertFalse(os.path.exists(marker),
                          "Overwrite should have removed the old directory")


# ===========================================================================
# Test 12: Relative paths
# ===========================================================================

class TestRelativePaths(unittest.TestCase):
    """Tests that exported paths are relative."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.app_root = os.path.join(self.tmpdir, "app")
        os.makedirs(self.app_root, exist_ok=True)
        self.dest = os.path.join(self.tmpdir, "exports")
        os.makedirs(self.dest, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_audio_paths_are_relative(self):
        """12. Exported audio paths are relative to the export root."""
        from engine.project_exporter import ProjectExporter
        project = _make_project(name="RelPath", num_scenes=1, num_chars=0,
                                 with_audio=True, app_root=self.app_root)
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=True,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        with open(os.path.join(result.export_dir, "project.json"), "r") as f:
            data = json.load(f)
        for scene in data["scenes"]:
            for asset in scene.get("audio_assets", []):
                path = asset.get("output_path", "")
                self.assertFalse(os.path.isabs(path),
                                  "Path should be relative: {0}".format(path))
                self.assertTrue(path.startswith("scenes/"),
                                "Path should start with scenes/: {0}".format(path))


# ===========================================================================
# Test 13: Export does not modify original Project
# ===========================================================================

class TestOriginalUnchanged(unittest.TestCase):
    """Tests that the original Project is not modified by export."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.app_root = os.path.join(self.tmpdir, "app")
        os.makedirs(self.app_root, exist_ok=True)
        self.dest = os.path.join(self.tmpdir, "exports")
        os.makedirs(self.dest, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_original_project_unchanged(self):
        """13. Export must not modify the original Project's audio paths."""
        from engine.project_exporter import ProjectExporter
        project = _make_project(name="OrigTest", num_scenes=1, num_chars=0,
                                 with_audio=True, app_root=self.app_root)
        # Record original audio paths
        original_paths = [a["output_path"] for s in project.scenes
                          for a in s.audio_assets]
        exporter = ProjectExporter(self.app_root)
        exporter.export(project, self.dest, include_audio=True,
                         include_voice_assets=False)
        # Verify original paths are unchanged
        after_paths = [a["output_path"] for s in project.scenes
                       for a in s.audio_assets]
        self.assertEqual(original_paths, after_paths,
                          "Original Project audio paths must not change")


# ===========================================================================
# Tests 14-17: Round-trip import + state match
# ===========================================================================

class TestRoundTripImport(unittest.TestCase):
    """Tests that an exported Project can be re-opened via ProjectManager."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.app_root = os.path.join(self.tmpdir, "app")
        os.makedirs(self.app_root, exist_ok=True)
        self.dest = os.path.join(self.tmpdir, "exports")
        os.makedirs(self.dest, exist_ok=True)
        # Native projects dir for the ProjectManager
        self.projects_dir = os.path.join(self.app_root, "projects")
        os.makedirs(self.projects_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_exported_project_can_be_reopened(self):
        """14. An exported project can be imported via ProjectManager.import_project_dir."""
        from engine.project_exporter import ProjectExporter
        from engine.project_manager import ProjectManager
        project = _make_project(name="RoundTrip", num_scenes=3, num_chars=2)
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=False,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        # Import the exported project
        pm = ProjectManager(self.projects_dir)
        imported = pm.import_project_dir(result.export_dir)
        self.assertIsNotNone(imported, "Import should succeed")
        self.assertEqual(imported.name, "RoundTrip")
        self.assertEqual(len(imported.scenes), 3)
        self.assertEqual(len(imported.characters), 2)

    def test_exported_scene_state_matches_original(self):
        """15. Exported Scene state matches the original.

        P3.28 SUPERSESSION: this test previously asserted that an
        imported Scene keeps a hand-set `status = "complete"` verbatim.
        P3.28 §5 / design record Rec 24 correct that: the Scene status
        is DERIVED from audio-asset coverage and recomputed on project
        save/load — a scene with ZERO generated audio derives to
        NOT_GENERATED ("complete" may honestly downgrade; that is the
        corrected semantics, not data loss). The remaining scene state
        (text, emotion) must still match the original exactly.
        """
        from engine.project_exporter import ProjectExporter
        from engine.project_manager import ProjectManager
        from engine.audio_provenance import SCENE_NOT_GENERATED
        project = _make_project(name="SceneMatch", num_scenes=2, num_chars=1)
        # Set some specific scene state
        project.scenes[0].text = "Custom text"
        project.scenes[0].emotion = "fear"
        project.scenes[0].status = "complete"  # hand-set, no audio assets
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=False,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        pm = ProjectManager(self.projects_dir)
        imported = pm.import_project_dir(result.export_dir)
        # Find the matching scene by name
        orig_scene = project.scenes[0]
        imp_scene = next(s for s in imported.scenes if s.name == orig_scene.name)
        self.assertEqual(imp_scene.text, "Custom text")
        self.assertEqual(imp_scene.emotion, "fear")
        # P3.28: zero-coverage scenes derive to NOT_GENERATED on import
        # (the save-time recompute is the honest corrected value).
        self.assertEqual(imp_scene.status, SCENE_NOT_GENERATED)

    def test_exported_characters_match_original(self):
        """16. Exported Characters match the original."""
        from engine.project_exporter import ProjectExporter
        from engine.project_manager import ProjectManager
        project = _make_project(name="CharMatch", num_scenes=1, num_chars=2)
        project.characters[0].name = "Engineer"
        project.characters[0].role = "protagonist"
        project.characters[0].voice_profile_id = "vp_abc"
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=False,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        pm = ProjectManager(self.projects_dir)
        imported = pm.import_project_dir(result.export_dir)
        # Find Engineer
        eng = next(c for c in imported.characters if c.name == "Engineer")
        self.assertEqual(eng.role, "protagonist")
        self.assertEqual(eng.voice_profile_id, "vp_abc")

    def test_exported_audio_metadata_matches_original(self):
        """17. Exported AudioAsset metadata matches the original."""
        from engine.project_exporter import ProjectExporter
        from engine.project_manager import ProjectManager
        project = _make_project(name="AudioMeta", num_scenes=1, num_chars=0,
                                 with_audio=True, app_root=self.app_root)
        orig_asset = project.scenes[0].audio_assets[0]
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=True,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        pm = ProjectManager(self.projects_dir)
        imported = pm.import_project_dir(result.export_dir)
        imp_asset = imported.scenes[0].audio_assets[0]
        self.assertEqual(imp_asset["id"], orig_asset["id"])
        self.assertEqual(imp_asset["duration"], orig_asset["duration"])
        self.assertEqual(imp_asset["scene_id"], orig_asset["scene_id"])
        # The output_path will be different (relative to export vs app_root)
        # but it should still be a valid path
        self.assertTrue(imp_asset["output_path"])


# ===========================================================================
# Test 18: Large multi-Scene Project
# ===========================================================================

class TestLargeProject(unittest.TestCase):
    """Tests for large projects with many scenes/characters."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.app_root = os.path.join(self.tmpdir, "app")
        os.makedirs(self.app_root, exist_ok=True)
        self.dest = os.path.join(self.tmpdir, "exports")
        os.makedirs(self.dest, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_large_multi_scene_project_export(self):
        """18. Large Project (10 scenes, 5 chars, audio) exports correctly."""
        from engine.project_exporter import ProjectExporter
        project = _make_project(name="LargeProject", num_scenes=10, num_chars=5,
                                 with_audio=True, app_root=self.app_root)
        exporter = ProjectExporter(self.app_root)
        result = exporter.export(project, self.dest, include_audio=True,
                                  include_voice_assets=False)
        self.assertTrue(result.success)
        self.assertEqual(result.scenes_exported, 10)
        self.assertEqual(result.characters_exported, 5)
        self.assertEqual(result.audio_files_copied, 10)
        self.assertEqual(len(result.warnings), 0)


# ===========================================================================
# Menu/UI wiring tests (AST-based, no PySide6 needed)
# ===========================================================================

class TestExportMenuWiring(unittest.TestCase):
    """Tests that the Export Project action is wired in the menu bar."""

    def test_menu_bar_has_export_project(self):
        """File menu must have an export_project action."""
        import ast
        path = os.path.join(_ROOT, "ui", "panels", "menu_bar.py")
        with open(path) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "MenuBar":
                for n in node.body:
                    if isinstance(n, ast.FunctionDef) and n.name == "_build_file_menu":
                        src = ast.unparse(n)
                        self.assertTrue(
                            '"export_project"' in src or "'export_project'" in src)
                        self.assertIn("Export Project", src)
                        return
        self.fail("MenuBar._build_file_menu not found")

    def test_main_window_connects_export_project(self):
        """MainWindow must connect the export_project action."""
        path = os.path.join(_ROOT, "ui", "main_window.py")
        with open(path) as f:
            src = f.read()
        self.assertIn('connect("export_project"', src)
        self.assertIn("_on_export_project", src)

    def test_top_nav_file_menu_has_export_project(self):
        """TopNav File menu must include export_project."""
        import ast
        path = os.path.join(_ROOT, "ui", "panels", "top_navigation.py")
        with open(path) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "TopNavigation":
                for n in node.body:
                    if isinstance(n, ast.FunctionDef) and n.name == "_setup_menus":
                        src = ast.unparse(n)
                        self.assertTrue(
                            '"export_project"' in src or "'export_project'" in src)
                        return
        self.fail("TopNavigation._setup_menus not found")


# ===========================================================================
# ProjectExporter API tests
# ===========================================================================

class TestProjectExporterAPI(unittest.TestCase):
    """Tests for the ProjectExporter class API."""

    def test_export_result_summary(self):
        """ExportResult.summary() produces a readable string."""
        from engine.project_exporter import ExportResult
        r = ExportResult(success=True, scenes_exported=3, characters_exported=2,
                          audio_files_copied=5, voice_assets_copied=1, warnings=[])
        summary = r.summary()
        self.assertIn("Scenes exported: 3", summary)
        self.assertIn("Characters exported: 2", summary)
        self.assertIn("Audio files copied: 5", summary)
        self.assertIn("Referenced voice assets copied: 1", summary)
        self.assertIn("Warnings: 0", summary)

    def test_exporter_rejects_none_project(self):
        """Exporter should reject None project with an error."""
        from engine.project_exporter import ProjectExporter
        exporter = ProjectExporter("/tmp")
        result = exporter.export(None, "/tmp", include_audio=False,
                                  include_voice_assets=False)
        self.assertFalse(result.success)
        self.assertIn("No project", result.error)

    def test_safe_dirname(self):
        """_safe_dirname converts unsafe names."""
        from engine.project_exporter import _safe_dirname
        self.assertEqual(_safe_dirname("Eden"), "Eden")
        self.assertEqual(_safe_dirname("My Project/Two"), "My_Project_Two")
        self.assertEqual(_safe_dirname(""), "untitled")
        self.assertEqual(_safe_dirname("  spaces  "), "spaces")


if __name__ == "__main__":
    unittest.main(verbosity=2)
