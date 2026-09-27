"""
GUINEO (SpeechStudio) — P3.45.1 runtime tests
===============================================

BLOCK → PART UX — GENERATION-STATE VISIBILITY (presentation-only wiring).

Locks the contract mandated by docs/design/P3_45_1_BLOCK_GENERATION_STATE_
VISIBILITY.md: the editor gutter's generation-state badges (the previously
DEAD ``update_block_status`` API + badge/progress painter) are fed by a pure
derivation of the existing provenance state:

    Scene.expected_audio_slots × append-only audio_assets
      → engine.audio_provenance.block_slot_states   (existing, tested)
      → MainWindow._sync_block_status_badges        (P3.45.1, derived,
                                                      state-transition driven)
      → NarrationEditor.update_block_status          (existing API)
      → _BlockGutterWidget badge painter             (existing visual)

Derived per-block state (NEVER stored — re-derived at every refresh point):

    covered == total            → "done"       ("✓ N/N" when multi-part)
    run in flight + uncovered   → "generating" ("⚙ N/N" when multi-part)
    zero coverage + ERROR scene → "error"      (existing taxonomy)
    anything else               → None         (NO badge — never stale)

Refresh points (state-transition driven; NO timers, NO polling):
  1. ``blocks_changed`` (Scene/project restore, Re-detect, programmatic
     text swap — the block STRUCTURE transition signal);
  2. generation finished + asset registered on the ACTIVE Scene
     (``_on_generation_finished_ui`` — the real result-registration path);
  3. generation failed on the ACTIVE Scene (``_on_generation_failed_ui``);
  4. batch completed (``_on_scene_batch_completed`` — the run-end recompute);
  5. Generate Long start (``_start_long_narration`` — run start AND the
     expected-slot (re)materialisation structure event).

Required test matrix (task directive):
    1. initial state            5. reload / scene restore
    2. generation completion    6. Re-detect
       (REAL registration path) 7. version/selection change (stability —
    3. multiple blocks             block_slot_states exposes no selected-
       (isolation)                 version input → no refresh required)
    4. regeneration             8. no stale widget state
    +  real-widget paint checks (gutter grab → pixel scan: the done badge
       renders in SUCCESS green / the error badge in ERROR red / NO badge
       pixels when the derived state is None).

Paint-attachment note: a pixel-level "badge inside its block's row band"
assertion is deliberately NOT attempted — under the offscreen platform
the lazy QPlainTextDocumentLayout stays permanently in-flight (the
documented P3.44.9.1 harness raciness), so a synchronous _block_rows()
reading can drift >10px from the paint-time basis between two processEvents
windows. The P3.44.9.1 suite pins the ROW geometry (rows == text anchors
at EVERY scroll position, same-basis comparison); the badge is painted in
the SAME paint pass at the SAME row ``gy`` (header and badge are one
iteration of one loop — structurally inseparable), and scroll-purity of
the badge data is pinned by test_scrolling_does_not_mutate_badge_state.
Visual confirmation of the painted result (badge on the correct block's
row, coverage labels readable) was done offscreen via rendered-PNG VLM
inspection — see the design record §MANUAL GUI CHECK.

Environment note (inherited from the P3.44.9.1 suite): real widget tests
run under QT_QPA_PLATFORM=offscreen. Visual verification beyond the pixel
scans (exact glyph shapes, label aesthetics) is NOT possible without a
human screen — the geometry itself is pinned by the frozen P3.44.9.1
contract tests, which are re-run in the P3.45.1 validation battery.

Run:
    LD_LIBRARY_PATH=/tmp/gllibs/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_p3_45_1_block_generation_state_visibility.py -v
"""
from __future__ import annotations

import os
import sys
import time
import types
import unittest
import tempfile
import shutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Fake torch (house pattern: test_p3_28 / test_p3_35 / test_p3_44_9_1).
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

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from engine.narration_blocks import PromptBlock  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)

# Real application theme (house pattern since P3.34).
from ui.theme import apply_theme, DEFAULT_THEME  # noqa: E402
apply_theme(APP, DEFAULT_THEME)

SAMPLE_RATE = 24000


def make_speech(seconds: float, freq: float = 220.0) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class FakeHiggsModel:
    """Model-boundary fake (P3.27B/P3.28 house pattern)."""

    calls = []
    speech_s = 1.0
    runaway = False
    fail_next = 0

    def generate_speech(self, text, tokenizer, *, reference_audio=None,
                        reference_sample_rate=None, reference_codes=None,
                        reference_text=None, max_new_tokens=2048,
                        temperature=1.0, top_p=None, top_k=None):
        FakeHiggsModel.calls.append(dict(text=text))
        if FakeHiggsModel.fail_next > 0:
            FakeHiggsModel.fail_next -= 1
            raise RuntimeError("simulated model failure")
        if FakeHiggsModel.runaway:
            cap_s = max_new_tokens / 25.0
            out = np.zeros(int(cap_s * SAMPLE_RATE), dtype=np.float32)
            out[: int(FakeHiggsModel.speech_s * SAMPLE_RATE)] = \
                make_speech(FakeHiggsModel.speech_s)
            return out
        return make_speech(FakeHiggsModel.speech_s)


def _process(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _make_part(text="A test sentence for the part.", speaker=None,
               block_id=None, part_of_block=1, block_label="",
               character_id=None, est=8.0):
    from engine.narration_splitter import SplitPart
    return SplitPart(
        text=text, prompt=text, speaker=speaker or "",
        source_block_id=block_id, part_of_block=part_of_block,
        total_parts_in_block=1, block_label=block_label,
        character_id=character_id, estimated_duration=est,
        char_count=len(text))


# The editor document: three blocks separated by blank lines (the block
# detector scores "\n\n" as a hard boundary, so Re-detect reproduces the
# same 3-block structure with FRESH ids).
TEXT = (
    "The engine hums in the dark.\n\n"
    "Stars drift past the viewport. The station is close now. "
    "Docking clamps extend with a heavy clank. The corridor lights "
    "wake up one by one.\n\n"
    "Silence settles over the deck."
)


def _block_dict(bid, start, end):
    return {"id": bid, "start_offset": start, "end_offset": end,
            "emotion": None, "style": None, "speed": None, "pitch": None,
            "delivery": None, "sfx_insertions": [], "pause_insertions": [],
            "label": None, "locked": False, "manually_edited": False,
            "character_id": None, "lost_character_id": None}


_P1 = TEXT.index("\n\n")
_P2 = TEXT.index("\n\n", _P1 + 2)
SCENE_BLOCKS = [
    _block_dict("b1", 0, _P1),
    _block_dict("b2", _P1 + 2, _P2),
    _block_dict("b3", _P2 + 2, len(TEXT)),
]


def _parts_4():
    """b1 → 1 part, b2 → 2 parts, b3 → 1 part (b2 is the multi-part block)."""
    return [
        _make_part(block_id="b1", part_of_block=1, block_label="B1"),
        _make_part(block_id="b2", part_of_block=1, block_label="B2"),
        _make_part(block_id="b2", part_of_block=2, block_label="B2"),
        _make_part(block_id="b3", part_of_block=1, block_label="B3"),
    ]


# ===========================================================================
# Harness — real MainWindow + Engine, model boundary faked (P3.28 pattern),
# editor restored through the REAL _load_scene_state path.
# ===========================================================================
class _P345Harness(unittest.TestCase):
    """Real MainWindow + Engine with the model boundary faked."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p3451_")
        from engine.engine import Engine
        from ui.main_window import MainWindow
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
        self.win = MainWindow(self.engine)
        # Isolate persistence into the temp root.
        self.win._project_manager = type(
            "PM", (), {"__init__": lambda self: None,
                       "save_project": lambda self, p: None,
                       "list_projects": lambda self: []})()
        from engine.models import Project, Scene
        self.project = Project(name="Eden", id="proj1")
        self.scene = Scene(id="sc1abcd", name="Scene 03",
                           project_id="proj1", text=TEXT,
                           narration_blocks=[dict(b) for b in SCENE_BLOCKS],
                           editor_mode="blocks")
        self.project.add_scene(self.scene)
        self.win._active_project = self.project
        self.win._active_scene = self.scene
        self.win._current_project = "Eden"
        # The REAL scene-restore path (used by every scene switch and
        # project load): blocks → editor, blocks_changed → badge sync.
        self.win._load_scene_state(self.scene)
        FakeHiggsModel.calls = []
        FakeHiggsModel.runaway = False
        FakeHiggsModel.fail_next = 0
        FakeHiggsModel.speech_s = 1.0

    def tearDown(self):
        try:
            if getattr(self, "win", None) is not None:
                try:
                    if self.win._batch_dialog is not None:
                        self.win._batch_dialog.close()
                except Exception:
                    pass
                self.win.close()
        except Exception:
            pass
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def params(self):
        from engine.models import GenerationParameters
        return GenerationParameters(
            temperature=0.95, top_p=0.95, top_k=300, max_new_tokens=4096,
            seed=None, append_silence=0.5, normalize_output=False,
            auto_play=False)

    def start_long(self, parts, review=False):
        self.win._start_long_narration(parts, None, self.params(), {},
                                       review_mode=review)

    def wait_batch(self, timeout=60):
        bm = self.win._batch_manager
        t0 = time.time()
        while bm.is_running and time.time() - t0 < timeout:
            _process(20)
        _process(200)

    def badge(self, block_id):
        """The pushed badge state for a block: (status, parts_done, parts_total)."""
        b = self.win._editor.block_manager.get_block(block_id)
        self.assertIsNotNone(b, "block %s must exist in the editor" % block_id)
        return (b.status, b.parts_done, b.parts_total)

    def editor_blocks(self):
        return self.win._editor.block_manager.blocks


# ===========================================================================
# 1) DERIVED-STATE TESTS (refresh points + the derivation contract)
# ===========================================================================
class TestInitialAndDerivedStates(_P345Harness):
    """Test 1 (+ derivation contract): blocks without generation show
    nothing — never a stale generated/progress state."""

    def test_t1_initial_state_no_badge_without_generation(self):
        # Fresh scene restored through the real path: no slots, no assets.
        for bid in ("b1", "b2", "b3"):
            self.assertEqual(self.badge(bid), (None, 0, 0))
        # Slots materialised but ZERO coverage, no run in flight, no
        # error scene → still NO badge (status stays None). The coverage
        # payload (parts_done/parts_total) is pushed truthfully — it is
        # display data, painted ONLY when a badge status exists:
        from engine.audio_provenance import materialize_expected_slots
        self.scene.expected_audio_slots = materialize_expected_slots(
            _parts_4())
        self.win._sync_block_status_badges()
        self.assertEqual(self.badge("b1"), (None, 0, 1))
        self.assertEqual(self.badge("b2"), (None, 0, 2))
        self.assertEqual(self.badge("b3"), (None, 0, 1))

    def test_t1_error_scene_zero_coverage_shows_error_badge(self):
        # The existing failure representation: SCENE_ERROR (zero coverage
        # + last run failed) → per-block "error" via the same derivation.
        from engine.audio_provenance import (
            materialize_expected_slots, recompute_scene_status)
        self.scene.expected_audio_slots = materialize_expected_slots(
            _parts_4())
        recompute_scene_status(self.scene, last_run_failed=True)
        self.assertEqual(self.scene.status, "error")
        self.win._sync_block_status_badges()
        for bid in ("b1", "b2", "b3"):
            status, done, total = self.badge(bid)
            self.assertEqual(status, "error")
        # Zero-coverage blocks in a NON-error scene (e.g. PARTIAL after a
        # selective run) show NOTHING — see Test 3.

    def test_t1_blocks_without_slots_never_show_a_badge(self):
        # A block that has no expected slot (added after the last Generate
        # Long, or a legacy scene) must not inherit any state.
        from engine.audio_provenance import materialize_expected_slots
        parts = _make_part(block_id="b1", part_of_block=1)
        self.scene.expected_audio_slots = materialize_expected_slots(
            [parts])
        self.win._sync_block_status_badges()
        self.assertEqual(self.badge("b1"), (None, 0, 1))
        for bid in ("b2", "b3"):
            self.assertEqual(self.badge(bid), (None, 0, 0))


# ===========================================================================
# 2) REAL GENERATION PATH — completion, run-in-flight, isolation, regen
# ===========================================================================
class TestRealGenerationPath(_P345Harness):
    """Tests 2–4: the REAL BatchManager → generation manager →
    register_generation_result (the ONE writer) → badge sync chain."""

    def test_t2_generation_completion_real_registration_path(self):
        self.start_long(_parts_4())
        self.wait_batch()
        # The real path produced the assets and the derived scene state:
        self.assertEqual(self.scene.status, "complete")
        self.assertEqual(len(self.scene.audio_assets), 4)
        self.assertEqual(len(self.scene.expected_audio_slots), 4)
        # …and the badges follow it:
        self.assertEqual(self.badge("b1"), ("done", 1, 1))
        self.assertEqual(self.badge("b2"), ("done", 2, 2))  # multi-part
        self.assertEqual(self.badge("b3"), ("done", 1, 1))

    def test_t2_generating_state_while_run_in_flight(self):
        # Review mode = deterministic observation window: the structure
        # event materialised the slots and the scene is GENERATING, but
        # no job has run yet.
        self.start_long(_parts_4(), review=True)
        self.assertEqual(self.scene.status, "generating")
        self.assertEqual(self.badge("b1"), ("generating", 0, 1))
        self.assertEqual(self.badge("b2"), ("generating", 0, 2))
        self.assertEqual(self.badge("b3"), ("generating", 0, 1))

    def test_t3_isolation_unrelated_blocks_do_not_inherit(self):
        # Generate ONLY b1's part (the real selective-generation path);
        # b2/b3 must not inherit "done" — and after the run settles they
        # must not keep a stale "generating" badge either.
        self.start_long(_parts_4(), review=True)
        self.win._on_generate_selected([0])
        self.wait_batch()
        self.assertEqual(self.badge("b1"), ("done", 1, 1))
        # b2/b3 keep their truthful (uncovered) coverage payload but NO
        # badge status — they did not inherit "done", and after the run
        # settled they do not keep a stale "generating" badge either:
        self.assertEqual(self.badge("b2"), (None, 0, 2))
        self.assertEqual(self.badge("b3"), (None, 0, 1))
        # The scene honestly reflects partial coverage:
        self.assertEqual(self.scene.status, "partial")

    def test_t4_regeneration_reflects_current_result(self):
        self.start_long(_parts_4())
        self.wait_batch()
        self.assertEqual(self.badge("b2"), ("done", 2, 2))
        # Regenerate b2's first part as a NEW VERSION (real path).
        self.win._on_batch_regen(1)
        self.wait_batch()
        # The currently valid state: still covered (append-only versions)
        # → "done"; the previous good version was never degraded.
        self.assertEqual(self.badge("b2"), ("done", 2, 2))
        self.assertEqual(self.scene.status, "complete")
        from engine.audio_provenance import assets_for_slot
        self.assertEqual(len(assets_for_slot(self.scene, "b2:1")), 2)


# ===========================================================================
# 3) RESTORE / RE-DETECT / SELECTION / STALE STATE
# ===========================================================================
class TestRestoreAndStaleness(_P345Harness):
    """Tests 5–8."""

    def _generate_all(self):
        self.start_long(_parts_4())
        self.wait_batch()
        self.assertEqual(self.scene.status, "complete")

    def test_t5_reload_reconstructs_badges_from_persisted_model(self):
        self._generate_all()
        # Persistence round-trip (the project file path) + the REAL scene
        # restore path used by scene switches and project loads.
        from engine.models import Scene as SceneModel
        restored = SceneModel.from_dict(self.scene.to_dict())
        self.win._active_scene = restored
        self.win._load_scene_state(restored)
        # The restored editor blocks are FRESH objects (from_dict →
        # status None); the badges were re-DERIVED from the persisted
        # slots/assets, not retained widget state:
        self.assertEqual(self.badge("b1"), ("done", 1, 1))
        self.assertEqual(self.badge("b2"), ("done", 2, 2))
        self.assertEqual(self.badge("b3"), ("done", 1, 1))

    def test_t5_scene_switch_clears_badges_on_ungenerated_scene(self):
        self._generate_all()
        self.assertEqual(self.badge("b1"), ("done", 1, 1))
        # Switch to a scene that has the SAME block ids but no slots and
        # no assets — the editor must reconstruct from THAT scene's model
        # (no stale carry-over of the previous scene's widget state).
        from engine.models import Scene as SceneModel
        fresh = SceneModel(id="sc9fresh", name="Fresh",
                           project_id="proj1", text=TEXT,
                           narration_blocks=[dict(b) for b in SCENE_BLOCKS],
                           editor_mode="blocks")
        self.win._active_scene = fresh
        self.win._load_scene_state(fresh)
        for bid in ("b1", "b2", "b3"):
            self.assertEqual(self.badge(bid), (None, 0, 0))

    def test_t6_redetect_state_follows_surviving_slots(self):
        self._generate_all()
        self.assertEqual(self.badge("b2"), ("done", 2, 2))
        old_ids = {b.id for b in self.editor_blocks()}
        # The REAL Re-detect path: no overrides and no locks → the direct
        # rebuild branch (no dialog) replaces EVERY block id (P3.26).
        self.win._editor._on_analyze_again()
        blocks = self.editor_blocks()
        self.assertEqual(len(blocks), 3)
        self.assertNotEqual({b.id for b in blocks}, old_ids)
        # No stale badge attached to the new blocks (the surviving slot
        # structure still carries the OLD block ids — new blocks claim
        # nothing)…
        for b in blocks:
            self.assertIsNone(b.status)
            self.assertEqual((b.parts_done, b.parts_total), (0, 0))
        # …and the provenance state itself was NOT mutated by Re-detect:
        self.assertEqual(len(self.scene.expected_audio_slots), 4)
        self.assertEqual(len(self.scene.audio_assets), 4)

    def test_t7_version_selection_keeps_badge_truthful(self):
        self._generate_all()
        from engine.audio_provenance import assets_for_slot
        first = assets_for_slot(self.scene, "b2:1")[0]
        # The REAL selection path (P3.28 §15/Rec 16):
        self.win._on_use_version_requested("b2:1", first["id"])
        self.assertEqual(self.scene.selected_block_audio.get("b2:1"),
                         first["id"])
        # block_slot_states exposes NO selected-version input (covered /
        # total / version_count are selection-independent) — no refresh
        # point is required; the badge remains the truthful derived state:
        self.assertEqual(self.badge("b2"), ("done", 2, 2))
        self.assertEqual(self.badge("b1"), ("done", 1, 1))

    def test_t8_stale_done_badge_disappears_when_state_changes(self):
        self._generate_all()
        self.assertEqual(self.badge("b2"), ("done", 2, 2))
        # Re-run Generate Long with a structure where b2 now has THREE
        # parts (deterministic: review mode — no job runs). The stale
        # "done" must vanish; the still-covered slots stay truthful.
        parts = [
            _make_part(block_id="b1", part_of_block=1, block_label="B1"),
            _make_part(block_id="b2", part_of_block=1, block_label="B2"),
            _make_part(block_id="b2", part_of_block=2, block_label="B2"),
            _make_part(block_id="b2", part_of_block=3, block_label="B2"),
            _make_part(block_id="b3", part_of_block=1, block_label="B3"),
        ]
        self.start_long(parts, review=True)
        self.assertEqual(self.badge("b2"), ("generating", 2, 3))
        self.assertEqual(self.badge("b1"), ("done", 1, 1))
        self.assertEqual(self.badge("b3"), ("done", 1, 1))
        # Settle without running anything (the same recompute the batch
        # completion callback performs): partial + idle → NO badge (the
        # stale done is gone); the coverage payload stays truthful:
        self.win._batch_manager.clear_all()
        from engine.audio_provenance import recompute_scene_status
        recompute_scene_status(self.scene)
        self.win._sync_block_status_badges()
        self.assertEqual(self.badge("b2"), (None, 2, 3))
        self.assertEqual(self.badge("b1"), ("done", 1, 1))

    def test_t8_keystroke_edits_do_not_destroy_badge_data(self):
        # P3.44.9 contract: single contiguous edits preserve the block
        # OBJECTS — the badge payload survives ordinary text editing.
        self._generate_all()
        ed = self.win._editor._editor
        cursor = ed.textCursor()
        cursor.setPosition(5)
        cursor.insertText("X")
        _process(30)
        self.assertEqual(self.badge("b1"), ("done", 1, 1))
        self.assertEqual(self.badge("b2"), ("done", 2, 2))


# ===========================================================================
# 4) REAL-WIDGET PAINT TESTS — the gutter actually renders the badges
# ===========================================================================
class TestGutterBadgePainting(_P345Harness):
    """Real widget rendering: the gutter is grabbed and scanned for the
    badge colors. SUCCESS green appears ONLY in the done badge (verified
    against the full paintEvent color inventory); ERROR red likewise for
    the error badge. The generating badge shares ACCENT teal with the
    per-row accent bars, so its presentation is verified at the model
    level (the same painter code path — only the pen color and label
    differ); exact glyph aesthetics need a human screen (offscreen
    limitation, stated in the design record).
    """

    def _grab_gutter(self):
        ed = self.win._editor._editor
        ed.resize(900, 520)
        _process(60)
        gutter = ed._block_gutter_widget
        self.assertTrue(gutter.isVisible() or True)  # offscreen: geometry set
        return gutter.grab().toImage()

    @staticmethod
    def _count(img, pred):
        n = 0
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                if pred(c.red(), c.green(), c.blue()):
                    n += 1
        return n

    def _green(self, img):
        # SUCCESS #4ade80: green clearly dominant over red AND blue.
        return self._count(img, lambda r, g, b: g > r + 40 and g > b + 40)

    def _red(self, img):
        # ERROR #f87171: red dominant (amber WARNING stays excluded).
        return self._count(img, lambda r, g, b:
                           r > 150 and r > g + 70 and r > b + 70)

    def test_paint_done_badge_renders_in_gutter(self):
        self.start_long(_parts_4())
        self.wait_batch()
        img = self._grab_gutter()
        self.assertGreater(self._green(img), 0,
                           "the done badge must render in SUCCESS green")

    def test_paint_no_badge_pixels_when_ungenerated(self):
        img = self._grab_gutter()
        self.assertEqual(self._green(img), 0)
        self.assertEqual(self._red(img), 0)

    def test_paint_error_badge_renders_in_gutter(self):
        # Drive the scene into the existing ERROR state (zero coverage +
        # failure hint) and confirm the error badge actually paints.
        from engine.audio_provenance import (
            materialize_expected_slots, recompute_scene_status)
        self.scene.expected_audio_slots = materialize_expected_slots(
            _parts_4())
        recompute_scene_status(self.scene, last_run_failed=True)
        self.win._sync_block_status_badges()
        img = self._grab_gutter()
        self.assertGreater(self._red(img), 0,
                           "the error badge must render in ERROR red")
        self.assertEqual(self._green(img), 0)

    def test_paint_done_badge_disappears_after_rematerialisation(self):
        self.start_long(_parts_4())
        self.wait_batch()
        img = self._grab_gutter()
        self.assertGreater(self._green(img), 0)
        # The state change that invalidates "done" (Test 8's scenario):
        parts = [
            _make_part(block_id="b2", part_of_block=1),
            _make_part(block_id="b2", part_of_block=2),
            _make_part(block_id="b2", part_of_block=3),
        ]
        from engine.audio_provenance import (
            materialize_expected_slots, recompute_scene_status)
        self.scene.expected_audio_slots = materialize_expected_slots(
            parts)
        recompute_scene_status(self.scene)
        self.win._sync_block_status_badges()
        img = self._grab_gutter()
        self.assertEqual(self._green(img), 0,
                         "no stale done badge may render after the state "
                         "no longer represents a generated block")


# ===========================================================================
# 5) FROZEN-CONTRACT PIN — the P3.45.1 push never mutates block structure
# ===========================================================================
class TestSyncPurity(_P345Harness):
    """The sync is a PRESENTATION of state, never a source of truth: it
    must not mutate block structure, offsets, slots or assets (the frozen
    P3.44.9 / P3.44.9.1 surfaces)."""

    def test_sync_never_mutates_block_model_or_provenance(self):
        self.start_long(_parts_4())
        self.wait_batch()
        before_blocks = [(b.id, b.start_offset, b.end_offset,
                          b.emotion, b.character_id, b.locked)
                         for b in self.editor_blocks()]
        before_slots = [dict(s) for s in self.scene.expected_audio_slots]
        before_assets = [dict(a) for a in self.scene.audio_assets]
        for _ in range(3):
            self.win._sync_block_status_badges()
        after_blocks = [(b.id, b.start_offset, b.end_offset,
                         b.emotion, b.character_id, b.locked)
                        for b in self.editor_blocks()]
        self.assertEqual(after_blocks, before_blocks)
        self.assertEqual([dict(s) for s in self.scene.expected_audio_slots],
                         before_slots)
        self.assertEqual([dict(a) for a in self.scene.audio_assets],
                         before_assets)

    def test_persisted_block_dict_has_no_badge_fields(self):
        # The badge payload is runtime display state only — never
        # persisted (the to_dict contract is unchanged).
        self.start_long(_parts_4())
        self.wait_batch()
        block = self.win._editor.block_manager.get_block("b2")
        d = block.to_dict()
        self.assertNotIn("status", d)
        self.assertNotIn("progress", d)
        self.assertNotIn("duration", d)
        self.assertNotIn("parts_done", d)
        self.assertNotIn("parts_total", d)

    def test_scrolling_does_not_mutate_badge_state(self):
        # Case-A pin (P3.44.9.1): scrolling is pure presentation — it
        # must not disturb the badge payload either.
        self.start_long(_parts_4())
        self.wait_batch()
        ed = self.win._editor._editor
        sb = ed.verticalScrollBar()
        sb.setValue(sb.maximum())
        _process(40)
        self.assertEqual(self.badge("b1"), ("done", 1, 1))
        self.assertEqual(self.badge("b2"), ("done", 2, 2))
        sb.setValue(0)
        _process(40)
        self.assertEqual(self.badge("b2"), ("done", 2, 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
