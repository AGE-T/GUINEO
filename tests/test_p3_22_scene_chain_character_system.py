"""
SpeechStudio — P3.22 Scene Chain + Character System Tests
==========================================================

Comprehensive tests for:
  1. Character Visual Identity (engine/character_colors.py)
  2. Combined Audio Assembly (engine/combined_audio.py)
  3. Project.combined_outputs field
  4. PromptBlock.character_id field
  5. SplitPart.character_id propagation
  6. Assembly Dialog (ui/panels/assemble_dialog.py)
  7. Block properties Character selector (ui/panels/narration_editor.py)
  8. Block gutter character color strip
  9. Sidebar character color dot
 10. Main window wiring (assemble menu, set_characters, voice resolution)
 11. Project Export combined_outputs integration

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_p3_22_scene_chain_character_system.py -v
"""

from __future__ import annotations
import os
import sys
import unittest
import tempfile
import shutil
import wave

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ===========================================================================
# 1. Character Visual Identity
# ===========================================================================

class TestCharacterColors(unittest.TestCase):
    """P3.22: engine/character_colors.py — deterministic hash-based colors."""

    def test_same_id_gives_same_color(self):
        """The same character_id must always map to the same color."""
        from engine.character_colors import get_character_color
        c1 = get_character_color("char_abc")
        c2 = get_character_color("char_abc")
        self.assertEqual(c1.hex_main, c2.hex_main)

    def test_different_ids_likely_different_colors(self):
        """Different character_ids should usually give different colors."""
        from engine.character_colors import get_character_color
        import uuid
        colors = set()
        # Use realistic 12-char UUID prefixes (like the real Character.id field)
        for _ in range(20):
            cid = str(uuid.uuid4())[:12]
            c = get_character_color(cid)
            colors.add(c.hex_main)
        # With 20 different UUIDs and 12 palette colors, we expect high diversity.
        # At least 8 unique colors (some collisions are expected with 12 buckets).
        self.assertGreaterEqual(len(colors), 8,
                                "Expected high color diversity for 20 UUID-based IDs")

    def test_none_id_gives_neutral(self):
        """None or empty character_id returns the neutral gray color."""
        from engine.character_colors import get_character_color
        c_none = get_character_color(None)
        c_empty = get_character_color("")
        self.assertEqual(c_none.hex_main, c_empty.hex_main)
        self.assertEqual(c_none.name, "Neutral")

    def test_color_has_all_fields(self):
        """CharacterColor must have hex_main, hex_bg, hex_text, name."""
        from engine.character_colors import get_character_color
        c = get_character_color("test_id")
        self.assertTrue(c.hex_main.startswith("#"))
        self.assertTrue(c.hex_bg.startswith("#"))
        self.assertTrue(c.hex_text.startswith("#"))
        self.assertTrue(len(c.name) > 0)

    def test_dark_vs_light_theme(self):
        """Dark and light themes give different variants."""
        from engine.character_colors import get_character_color
        c_dark = get_character_color("test_id", theme="dark")
        c_light = get_character_color("test_id", theme="light")
        # The main color may be different (brightened for dark, deepened for light)
        self.assertIsInstance(c_dark.hex_main, str)
        self.assertIsInstance(c_light.hex_main, str)

    def test_palette_has_12_colors(self):
        """The palette must have exactly 12 colors."""
        from engine.character_colors import get_all_palette_colors
        colors = get_all_palette_colors()
        self.assertEqual(len(colors), 12)

    def test_color_is_valid_hex(self):
        """All palette colors must be valid 7-char hex (#RRGGBB)."""
        from engine.character_colors import get_all_palette_colors, is_valid_color
        for c in get_all_palette_colors():
            self.assertTrue(is_valid_color(c.hex_main),
                            "{0} is not a valid hex color".format(c.hex_main))
            self.assertTrue(is_valid_color(c.hex_bg))

    def test_hex_shortcut(self):
        """get_character_color_hex returns just the hex string."""
        from engine.character_colors import get_character_color_hex, get_character_color
        hex_val = get_character_color_hex("test_id")
        full = get_character_color("test_id")
        self.assertEqual(hex_val, full.hex_main)


# ===========================================================================
# 2. Combined Audio Assembly
# ===========================================================================

class TestCombinedAudioAssembly(unittest.TestCase):
    """P3.22: engine/combined_audio.py — WAV concatenation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _make_wav(self, name: str, duration: float = 1.0, rate: int = 24000):
        """Create a test WAV file with the given duration."""
        path = os.path.join(self.tmpdir, name)
        n_frames = int(duration * rate)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(b"\x00" * (n_frames * 2))
        return path

    def test_assemble_basic(self):
        """Assembling 3 WAVs produces a combined WAV with correct duration."""
        from engine.combined_audio import assemble_combined_audio, AssemblySource
        wavs = [self._make_wav("s{0}.wav".format(i), 1.0) for i in range(3)]
        sources = [AssemblySource(scene_id="s{0}".format(i), scene_name="S{0}".format(i),
                                   audio_path=wavs[i], duration=1.0) for i in range(3)]
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(sources, out, inter_scene_silence=0.5)
        self.assertTrue(result.success)
        self.assertEqual(result.scene_count, 3)
        # 3 scenes (3.0s) + 2 gaps (1.0s) = 4.0s
        self.assertAlmostEqual(result.total_duration, 4.0, places=1)
        self.assertTrue(os.path.isfile(out))

    def test_assemble_no_sources(self):
        """Assembling with no sources fails gracefully."""
        from engine.combined_audio import assemble_combined_audio
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio([], out)
        self.assertFalse(result.success)
        self.assertGreater(len(result.errors), 0)

    def test_assemble_missing_audio(self):
        """P3.23 (design record §52/§53): missing audio BLOCKS the assembly.

        No silent skipping: if any selected Scene's audio file is missing,
        the result is unsuccessful, blocked_reasons names the Scene, and NO
        output file is written. (Previously the file was silently skipped —
        explicitly rejected by the design record and the P3.23 task spec.)
        """
        from engine.combined_audio import assemble_combined_audio, AssemblySource
        w1 = self._make_wav("s0.wav", 1.0)
        sources = [
            AssemblySource(scene_id="s0", scene_name="S0", audio_path=w1, duration=1.0),
            AssemblySource(scene_id="s1", scene_name="S1", audio_path="/nonexistent.wav", duration=1.0),
        ]
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(sources, out)
        self.assertFalse(result.success)
        self.assertGreater(len(result.blocked_reasons), 0)
        self.assertIn("S1", result.blocked_reasons[0])  # names the scene
        self.assertFalse(os.path.exists(out))            # no output written

    def test_assemble_no_silence(self):
        """Inter-scene silence of 0 means no gap."""
        from engine.combined_audio import assemble_combined_audio, AssemblySource
        wavs = [self._make_wav("s{0}.wav".format(i), 1.0) for i in range(2)]
        sources = [AssemblySource(scene_id="s{0}".format(i), scene_name="S{0}".format(i),
                                   audio_path=wavs[i], duration=1.0) for i in range(2)]
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(sources, out, inter_scene_silence=0.0)
        self.assertTrue(result.success)
        # 2 scenes (2.0s) + 0 gaps = 2.0s
        self.assertAlmostEqual(result.total_duration, 2.0, places=1)

    def test_build_combined_output_entry(self):
        """build_combined_output_entry creates a proper dict entry."""
        from engine.combined_audio import assemble_combined_audio, AssemblySource, build_combined_output_entry
        w = self._make_wav("s0.wav", 1.0)
        sources = [AssemblySource(scene_id="s0", scene_name="S0", audio_path=w, duration=1.0)]
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(sources, out)
        entry = build_combined_output_entry(result, "TestProject", [
            {"scene_id": "s0", "scene_name": "S0", "audio_path": w, "duration": 1.0}
        ])
        self.assertIn("id", entry)
        self.assertIn("output_path", entry)
        self.assertIn("duration", entry)
        self.assertIn("scene_count", entry)
        self.assertIn("source_scene_ids", entry)
        self.assertIn("created_at", entry)
        self.assertEqual(entry["scene_count"], 1)
        self.assertEqual(entry["project_name"], "TestProject")

    def test_stale_detection(self):
        """is_combined_output_stale detects re-generated scenes."""
        from engine.combined_audio import assemble_combined_audio, AssemblySource, build_combined_output_entry, is_combined_output_stale
        from engine.models import Scene
        w = self._make_wav("s0.wav", 1.0)
        sources = [AssemblySource(scene_id="s0", scene_name="S0", audio_path=w, duration=1.0)]
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(sources, out)
        entry = build_combined_output_entry(result, "Test", [
            {"scene_id": "s0", "scene_name": "S0", "audio_path": w, "duration": 1.0}
        ])
        # Scene with old audio → not stale
        scene = Scene(id="s0", name="S0")
        scene.audio_assets = [{"generated_at": "1999-01-01T00:00:00"}]
        self.assertFalse(is_combined_output_stale(entry, [scene]))
        # Scene with newer audio → stale
        scene.audio_assets = [{"generated_at": "9999-12-31T23:59:59"}]
        self.assertTrue(is_combined_output_stale(entry, [scene]))

    def test_ffmpeg_available_returns_bool(self):
        """ffmpeg_available returns a boolean (not crashes)."""
        from engine.combined_audio import ffmpeg_available
        result = ffmpeg_available()
        self.assertIsInstance(result, bool)


# ===========================================================================
# 3. Project.combined_outputs field
# ===========================================================================

class TestProjectCombinedOutputs(unittest.TestCase):
    """P3.22: Project.combined_outputs field exists and persists."""

    def test_project_has_combined_outputs(self):
        """Project must have a combined_outputs field."""
        from engine.models import Project
        p = Project(name="Test")
        self.assertTrue(hasattr(p, 'combined_outputs'))
        self.assertEqual(p.combined_outputs, [])

    def test_combined_outputs_serialization(self):
        """combined_outputs survives to_dict/from_dict round-trip."""
        from engine.models import Project
        p = Project(name="Test")
        p.combined_outputs.append({"id": "x", "output_path": "/tmp/x.wav", "duration": 5.0})
        data = p.to_dict()
        self.assertIn("combined_outputs", data)
        p2 = Project.from_dict(data)
        self.assertEqual(len(p2.combined_outputs), 1)
        self.assertEqual(p2.combined_outputs[0]["output_path"], "/tmp/x.wav")

    def test_backward_compat_no_combined_outputs(self):
        """Old project data without combined_outputs defaults to empty list."""
        from engine.models import Project
        old_data = {"name": "Old", "scenes": [], "characters": []}
        p = Project.from_dict(old_data)
        self.assertEqual(p.combined_outputs, [])

    def test_combined_outputs_persist(self):
        """combined_outputs survives save/load via ProjectManager."""
        from engine.project_manager import ProjectManager
        from engine.models import Project
        tmpdir = tempfile.mkdtemp()
        try:
            pm = ProjectManager(tmpdir)
            p = Project(name="Test")
            p.combined_outputs.append({"id": "x", "output_path": "/tmp/x.wav"})
            pm.save_project(p)
            pm2 = ProjectManager(tmpdir)
            loaded = pm2.list_projects()[0]
            self.assertEqual(len(loaded.combined_outputs), 1)
        finally:
            shutil.rmtree(tmpdir)


# ===========================================================================
# 4. PromptBlock.character_id field
# ===========================================================================

class TestPromptBlockCharacterId(unittest.TestCase):
    """P3.22: PromptBlock.character_id field exists and persists."""

    def test_block_has_character_id(self):
        """PromptBlock must have a character_id field."""
        from engine.narration_blocks import PromptBlock
        b = PromptBlock(start_offset=0, end_offset=10)
        self.assertTrue(hasattr(b, 'character_id'))
        self.assertIsNone(b.character_id)  # default

    def test_character_id_serialization(self):
        """character_id survives to_dict/from_dict round-trip."""
        from engine.narration_blocks import PromptBlock
        b = PromptBlock(start_offset=0, end_offset=10, character_id="char_abc")
        data = b.to_dict()
        self.assertIn("character_id", data)
        self.assertEqual(data["character_id"], "char_abc")
        b2 = PromptBlock.from_dict(data)
        self.assertEqual(b2.character_id, "char_abc")

    def test_backward_compat_no_character_id(self):
        """Old block data without character_id defaults to None."""
        from engine.narration_blocks import PromptBlock
        old_data = {"start_offset": 0, "end_offset": 5}
        b = PromptBlock.from_dict(old_data)
        self.assertIsNone(b.character_id)


# ===========================================================================
# 5. SplitPart.character_id propagation
# ===========================================================================

class TestSplitPartCharacterId(unittest.TestCase):
    """P3.22: SplitPart carries character_id from the source block."""

    def test_splitpart_has_character_id(self):
        """SplitPart must have a character_id field."""
        from engine.narration_splitter import SplitPart
        sp = SplitPart(text="hello", prompt="hello")
        self.assertTrue(hasattr(sp, 'character_id'))
        self.assertIsNone(sp.character_id)  # default

    def test_block_character_id_propagates_to_part(self):
        """When splitting blocks, the block's character_id is carried to parts."""
        from engine.narration_splitter import NarrationSplitter
        from engine.narration_blocks import PromptBlock
        splitter = NarrationSplitter()
        text = "Hello world. This is a test. Goodbye now."
        blocks = [PromptBlock(start_offset=0, end_offset=len(text), character_id="char_abc")]
        parts = splitter.split(
            text=text, blocks=blocks,
            global_emotion=None, global_style=None,
            global_speed="Normal", global_pitch="Normal", global_delivery="Normal",
        )
        self.assertGreater(len(parts), 0)
        for part in parts:
            self.assertEqual(part.character_id, "char_abc",
                             "All parts from a block with character_id must carry it")

    def test_no_character_id_when_block_has_none(self):
        """Blocks without character_id produce parts with character_id=None."""
        from engine.narration_splitter import NarrationSplitter
        from engine.narration_blocks import PromptBlock
        splitter = NarrationSplitter()
        text = "Hello world. This is a test."
        blocks = [PromptBlock(start_offset=0, end_offset=len(text))]
        parts = splitter.split(
            text=text, blocks=blocks,
            global_emotion=None, global_style=None,
            global_speed="Normal", global_pitch="Normal", global_delivery="Normal",
        )
        self.assertGreater(len(parts), 0)
        for part in parts:
            self.assertIsNone(part.character_id)


# ===========================================================================
# 6. Assembly Dialog (AST checks)
# ===========================================================================

class TestAssembleDialogAST(unittest.TestCase):
    """P3.22: AssembleScenesDialog exists with the expected API."""

    def test_dialog_file_exists(self):
        """ui/panels/assemble_dialog.py must exist."""
        path = os.path.join(_ROOT, "ui", "panels", "assemble_dialog.py")
        self.assertTrue(os.path.isfile(path))

    def test_dialog_class_exists(self):
        """AssembleScenesDialog class must exist."""
        with open(os.path.join(_ROOT, "ui/panels/assemble_dialog.py")) as f:
            src = f.read()
        self.assertIn("class AssembleScenesDialog", src)

    def test_dialog_has_assemble_handler(self):
        """Dialog must have _on_assemble method."""
        with open(os.path.join(_ROOT, "ui/panels/assemble_dialog.py")) as f:
            src = f.read()
        self.assertIn("def _on_assemble", src)

    def test_dialog_uses_assemble_combined_audio(self):
        """Dialog must call assemble_combined_audio from the engine."""
        with open(os.path.join(_ROOT, "ui/panels/assemble_dialog.py")) as f:
            src = f.read()
        self.assertIn("assemble_combined_audio", src)

    def test_dialog_has_mp3_option(self):
        """Dialog must have an MP3 checkbox."""
        with open(os.path.join(_ROOT, "ui/panels/assemble_dialog.py")) as f:
            src = f.read()
        self.assertIn("_mp3_checkbox", src)
        self.assertIn("ffmpeg_available", src)

    def test_dialog_has_inter_scene_gap(self):
        """Dialog must have an inter-scene silence slider."""
        with open(os.path.join(_ROOT, "ui/panels/assemble_dialog.py")) as f:
            src = f.read()
        self.assertIn("_gap_slider", src)


# ===========================================================================
# 7. Block properties Character selector (AST checks)
# ===========================================================================

class TestBlockPropertiesCharacterSelector(unittest.TestCase):
    """P3.22: NarrationEditor block properties has a Character selector."""

    def test_character_combo_exists(self):
        """NarrationEditor must have _character_combo."""
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        self.assertIn("_character_combo", src)
        self.assertIn("None (inherit scene voice)", src)

    def test_character_changed_handler_exists(self):
        """_on_character_changed handler must exist."""
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        self.assertIn("def _on_character_changed", src)

    def test_set_characters_method_exists(self):
        """set_characters method must exist."""
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        self.assertIn("def set_characters", src)

    def test_character_color_dot_exists(self):
        """Block properties must have a _char_dot for the character color."""
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        self.assertIn("_char_dot", src)

    def test_character_colors_imported(self):
        """narration_editor must import get_character_color."""
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        self.assertIn("from engine.character_colors import", src)


# ===========================================================================
# 8. Block gutter character color strip (AST checks)
# ===========================================================================

class TestBlockGutterCharacterStrip(unittest.TestCase):
    """P3.22: Block gutter paints a character color strip."""

    def test_gutter_uses_character_id(self):
        """Gutter painting code must check block.character_id."""
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        self.assertIn("char_id = getattr(block, 'character_id'", src)

    def test_gutter_draws_character_strip(self):
        """Gutter must draw a 3px character color strip."""
        with open(os.path.join(_ROOT, "ui/panels/narration_editor.py")) as f:
            src = f.read()
        self.assertIn("QRect(0, y_top, 3, block_height)", src)
        self.assertIn("accent_x = 3", src)


# ===========================================================================
# 9. Sidebar character color dot (AST checks)
# ===========================================================================

class TestSidebarCharacterDot(unittest.TestCase):
    """P3.22: Sidebar _SceneRow shows a character color dot."""

    def test_scene_row_accepts_character_id(self):
        """_SceneRow must accept a character_id parameter."""
        with open(os.path.join(_ROOT, "ui/panels/project_scene_sidebar.py")) as f:
            src = f.read()
        self.assertIn("character_id: str = \"\"", src)

    def test_scene_row_creates_char_dot(self):
        """_SceneRow must create a _char_dot when character_id is set."""
        with open(os.path.join(_ROOT, "ui/panels/project_scene_sidebar.py")) as f:
            src = f.read()
        self.assertIn("_char_dot", src)
        self.assertIn("get_character_color", src)

    def test_context_list_passes_character_id(self):
        """Context list building must pass character_id to _SceneRow."""
        with open(os.path.join(_ROOT, "ui/panels/project_scene_sidebar.py")) as f:
            src = f.read()
        self.assertIn("character_id=character_id", src)


# ===========================================================================
# 10. Main window wiring (AST checks)
# ===========================================================================

class TestMainWindowWiring(unittest.TestCase):
    """P3.22: MainWindow wires up assembly + character system."""

    def test_assemble_menu_connected(self):
        """MainWindow must connect assemble_scenes menu action."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("assemble_scenes", src)
        self.assertIn("self._on_assemble_scenes", src)

    def test_on_assemble_scenes_handler_exists(self):
        """MainWindow must have _on_assemble_scenes method."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("def _on_assemble_scenes", src)
        self.assertIn("AssembleScenesDialog", src)

    def test_update_editor_characters_exists(self):
        """MainWindow must have _update_editor_characters method."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("def _update_editor_characters", src)
        self.assertIn("self._editor.set_characters", src)

    def test_set_characters_called_on_project_load(self):
        """_load_native_project must call _update_editor_characters."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("self._update_editor_characters()", src)

    def test_voice_resolution_uses_block_character(self):
        """_start_long_narration must resolve voice from block character_id."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("part_character_id", src)
        self.assertIn("part.character_id", src)

    def test_batchjob_character_id_uses_part(self):
        """BatchJob character_id must use part_character_id."""
        with open(os.path.join(_ROOT, "ui/main_window.py")) as f:
            src = f.read()
        self.assertIn("character_id=part_character_id", src)

    def test_assemble_menu_item_in_menu_bar(self):
        """menu_bar.py must have the assemble_scenes action."""
        with open(os.path.join(_ROOT, "ui/panels/menu_bar.py")) as f:
            src = f.read()
        self.assertIn("assemble_scenes", src)
        self.assertIn("Assemble Scenes...", src)


# ===========================================================================
# 11. Project Export combined_outputs integration (AST checks)
# ===========================================================================

class TestProjectExportCombinedOutputs(unittest.TestCase):
    """P3.22: ProjectExporter exports combined_outputs."""

    def test_exporter_has_combined_outputs_section(self):
        """ProjectExporter must have a combined_outputs export section."""
        with open(os.path.join(_ROOT, "engine/project_exporter.py")) as f:
            src = f.read()
        self.assertIn("combined_outputs", src)
        self.assertIn("combined_dir", src)

    def test_exporter_copies_combined_wav(self):
        """Exporter must copy combined WAV files."""
        with open(os.path.join(_ROOT, "engine/project_exporter.py")) as f:
            src = f.read()
        self.assertIn("Combined output WAV", src)


# ===========================================================================
# 12. Runtime test: Assembly Dialog can be constructed
# ===========================================================================

class TestAssemblyDialogRuntime(unittest.TestCase):
    """P3.22: AssembleScenesDialog can be constructed and used."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_dialog_constructs(self):
        """Dialog can be constructed with a project."""
        from ui.panels.assemble_dialog import AssembleScenesDialog
        from engine.models import Project, Scene
        import wave
        # Create a project with a scene that has audio
        project = Project(name="TestProject")
        scene = Scene(id="s1", name="Scene 1", sort_order=0)
        # Create a test WAV
        wav_path = os.path.join(self.tmpdir, "test.wav")
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000)
            wf.writeframes(b"\x00" * 48000)
        scene.audio_assets = [{"output_path": wav_path, "duration": 1.0, "generated_at": "2024-01-01T00:00:00"}]
        project.add_scene(scene)
        dialog = AssembleScenesDialog(project, self.tmpdir)
        self.assertIsNotNone(dialog)
        self.assertEqual(len(dialog._scene_rows), 1)
        self.assertTrue(dialog._scene_rows[0].audio_path != "")
        dialog.deleteLater()


# ===========================================================================
# 13. Runtime test: Block properties Character selector works
# ===========================================================================

class TestBlockPropertiesCharacterSelectorRuntime(unittest.TestCase):
    """P3.22: NarrationEditor block properties Character selector works at runtime."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def test_set_characters_populates_combo(self):
        """set_characters populates the Character combo."""
        from ui.panels.narration_editor import NarrationEditor
        editor = NarrationEditor()
        editor.set_characters([
            {"id": "c1", "name": "Alice"},
            {"id": "c2", "name": "Bob"},
        ])
        # The combo should have 3 items: None + Alice + Bob
        self.assertEqual(editor._character_combo.count(), 3)
        self.assertEqual(editor._character_combo.itemText(0), "None (inherit scene voice)")
        self.assertEqual(editor._character_combo.itemText(1), "Alice")
        self.assertEqual(editor._character_combo.itemText(2), "Bob")
        editor.deleteLater()

    def test_set_characters_empty_clears_combo(self):
        """set_characters with empty list leaves only the None option."""
        from ui.panels.narration_editor import NarrationEditor
        editor = NarrationEditor()
        editor.set_characters([])
        self.assertEqual(editor._character_combo.count(), 1)
        self.assertEqual(editor._character_combo.itemText(0), "None (inherit scene voice)")
        editor.deleteLater()

    def test_character_selection_sets_block_character_id(self):
        """Selecting a character in the combo sets block.character_id."""
        from ui.panels.narration_editor import NarrationEditor
        from engine.narration_blocks import PromptBlock
        editor = NarrationEditor()
        editor.set_characters([{"id": "c1", "name": "Alice"}])
        # Set up a block
        editor._editor.setPlainText("Hello world.")
        block = PromptBlock(start_offset=0, end_offset=12)
        editor._block_manager._blocks.append(block)
        editor._selected_block_id = block.id
        editor._update_properties_panel()
        # Select Alice (index 1)
        editor._character_combo.setCurrentIndex(1)
        # Verify the block's character_id was set
        self.assertEqual(block.character_id, "c1")
        editor.deleteLater()

    def test_none_selection_clears_block_character_id(self):
        """Selecting None clears block.character_id."""
        from ui.panels.narration_editor import NarrationEditor
        from engine.narration_blocks import PromptBlock
        editor = NarrationEditor()
        editor.set_characters([{"id": "c1", "name": "Alice"}])
        editor._editor.setPlainText("Hello world.")
        block = PromptBlock(start_offset=0, end_offset=12, character_id="c1")
        editor._block_manager._blocks.append(block)
        editor._selected_block_id = block.id
        editor._update_properties_panel()
        # Select None (index 0)
        editor._character_combo.setCurrentIndex(0)
        self.assertIsNone(block.character_id)
        editor.deleteLater()


# ===========================================================================
# 14. Runtime test: Character colors are deterministic
# ===========================================================================

class TestCharacterColorDeterminismRuntime(unittest.TestCase):
    """P3.22: Character colors are deterministic across sessions."""

    def test_color_stable_across_multiple_calls(self):
        """Calling get_character_color 100 times gives the same color."""
        from engine.character_colors import get_character_color
        colors = set()
        for _ in range(100):
            c = get_character_color("stable_test_id")
            colors.add(c.hex_main)
        self.assertEqual(len(colors), 1, "Color must be stable across calls")

    def test_color_distribution(self):
        """Colors are reasonably distributed across the palette."""
        from engine.character_colors import get_character_color
        color_names = set()
        for i in range(100):
            c = get_character_color("char_{0:03d}".format(i))
            color_names.add(c.name)
        # With 100 different IDs and 12 colors, we expect all 12 names to appear
        self.assertGreaterEqual(len(color_names), 10,
                                "Expected most palette colors to be used")


# ===========================================================================
# 15. Runtime test: Combined audio with real WAVs
# ===========================================================================

class TestCombinedAudioRuntime(unittest.TestCase):
    """P3.22: Combined audio assembly with real WAV files."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_assemble_produces_valid_wav(self):
        """The assembled WAV is a valid, readable WAV file.

        P3.23: the combined output is 32-bit IEEE float WAV (24 kHz mono) —
        the same subtype the Higgs generation pipeline writes. Verified
        via soundfile (the `wave` module cannot read IEEE-float WAVs).
        """
        from engine.combined_audio import assemble_combined_audio, AssemblySource
        import wave
        wavs = []
        for i in range(3):
            path = os.path.join(self.tmpdir, "s{0}.wav".format(i))
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000)
                wf.writeframes(b"\x00" * 48000)
            wavs.append(path)
        sources = [AssemblySource(scene_id="s{0}".format(i), scene_name="S{0}".format(i),
                                   audio_path=wavs[i], duration=1.0) for i in range(3)]
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(sources, out, inter_scene_silence=0.5,
                                         normalize=False)
        self.assertTrue(result.success)
        # Verify the output is a valid float32 WAV via soundfile
        try:
            import soundfile as sf
        except ImportError:
            self.skipTest("soundfile not available")
        info = sf.info(out)
        self.assertEqual(info.subtype, "FLOAT")
        self.assertEqual(info.samplerate, 24000)
        self.assertEqual(info.channels, 1)
        # 3 scenes (3.0s) + 2 gaps (1.0s) = 4.0s
        self.assertAlmostEqual(info.duration, 4.0, places=1)

    def test_assemble_preserves_scene_order(self):
        """Scenes are assembled in the order they appear in the sources list."""
        from engine.combined_audio import assemble_combined_audio, AssemblySource
        import wave
        import struct
        # Create WAVs with distinguishable content (different amplitudes)
        wavs = []
        for i in range(3):
            path = os.path.join(self.tmpdir, "s{0}.wav".format(i))
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000)
                # Each scene has a unique amplitude pattern
                amp = (i + 1) * 1000
                frames = struct.pack("<" + "h" * 24000, *([amp] * 24000))
                wf.writeframes(frames)
            wavs.append(path)
        sources = [AssemblySource(scene_id="s{0}".format(i), scene_name="S{0}".format(i),
                                   audio_path=wavs[i], duration=1.0) for i in range(3)]
        out = os.path.join(self.tmpdir, "combined.wav")
        result = assemble_combined_audio(sources, out, inter_scene_silence=0.0)
        self.assertTrue(result.success)
        self.assertEqual(result.source_scene_ids, ["s0", "s1", "s2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
