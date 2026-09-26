"""
SpeechStudio P3.31 — Scene Output Resolution Safety (runtime tests)
===================================================================

Implements the ACCEPTED follow-up design decisions (P3.28 follow-up):

  R1  STALE is based on the RESOLVED asset identity per slot
      (``resolved_asset_for_slot`` — explicit selection, else latest).
      A deliberately curated OLDER version IS the composition; a newer
      version merely EXISTING is not staleness.

  R2  STALE also covers STRUCTURAL change: the snapshot's recorded slot
      set must equal the Scene's current expected slot set.

  R3  Combine clears ``Scene.selected_output`` — the fresh snapshot
      becomes the resolved output via the default rule.

  SAFETY  Automatic resolution NEVER treats a single Part AudioAsset as
      the complete Scene output. Chain: explicit selection → latest
      non-stale COMPLETE combined → REVIEW REQUIRED (legacy provable
      full-scene assets only when NO combined outputs exist).

  REMEDY  REVIEW REQUIRED rows offer [Combine Scene Now] +
      [Choose Output…]; Combine-by-id works for non-active Scenes.

No data-model changes; no new entities; nothing automatic (the user
alone decides when to regenerate or combine).
"""

import os
import sys
import shutil
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_RATE = 24000


def make_speech(seconds: float, freq: float = 220.0) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _process(ms=30):
    from PySide6.QtWidgets import QApplication
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


# ===========================================================================
# Shared pure-test helpers
# ===========================================================================
class _PureBase(unittest.TestCase):
    """Scene factory + real WAV fixtures (paths resolve under self.tmp)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p331_pure_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _file(self, rel):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import soundfile as sf
        sf.write(path, make_speech(1.0), SAMPLE_RATE, subtype="FLOAT")
        return path

    def make_scene(self, **kw):
        from engine.models import Scene
        s = Scene(id="ab12cd34ef56", name="Scene 03")
        for key, value in kw.items():
            setattr(s, key, value)
        return s

    def asset(self, part_index, version, path, block_id=None):
        self._file(path)
        return {
            "id": "a{0}v{1}".format(part_index, version),
            "scene_id": "ab12cd34ef56",
            "output_path": path,
            "duration": 10.0,
            "speaker": None, "character_id": None,
            "voice_profile_id": None,
            "generated_at": "20260101_120000",
            "block_id": block_id,
            "part_index": part_index,
            "part_version": version,
            "generation_run": "ab12cd34-r001",
        }

    def slot(self, slot_id, block_id, label, part_index):
        return {"slot_id": slot_id, "block_id": block_id,
                "block_label": label, "part_index": part_index,
                "part_of_block": 1, "speaker": "", "character_id": None}

    def source(self, slot_id, asset, label):
        return {"slot_id": slot_id, "block_id": asset.get("block_id"),
                "block_label": label,
                "part_index": asset.get("part_index"),
                "asset_id": asset["id"],
                "part_version": asset["part_version"],
                "speaker": None, "character_id": None,
                "output_path": asset["output_path"],
                "duration": asset.get("duration", 10.0)}


# ===========================================================================
# R1 — stale by RESOLVED asset identity
# ===========================================================================
class TestR1ResolvedIdentity(_PureBase):

    def test_curated_older_selection_is_not_stale(self):
        """B1 has v01/v02/v03; the user selected v02; the snapshot was
        built from v02. The snapshot represents the CURRENT composition
        — it must NOT be flagged stale just because v03 exists."""
        from engine.audio_provenance import is_scene_combined_stale
        assets = [self.asset(1, 1, "outputs/b1_v01.wav", block_id="b1"),
                  self.asset(1, 2, "outputs/b1_v02.wav", block_id="b1"),
                  self.asset(1, 3, "outputs/b1_v03.wav", block_id="b1")]
        scene = self.make_scene(
            expected_audio_slots=[self.slot("b1:1", "b1", "B1", 1)],
            audio_assets=assets,
            selected_block_audio={"b1:1": "a1v2"})
        entry = {"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                 "sources": [self.source("b1:1", assets[1], "B1")]}
        stale, reasons = is_scene_combined_stale(scene, entry, self.tmp)
        self.assertFalse(stale, reasons)

    def test_selecting_older_after_combine_is_stale(self):
        """Combined was built from B3 v02; the user LATER selects v01 —
        the composition moved below the snapshot → STALE with the
        curated-older reason."""
        from engine.audio_provenance import is_scene_combined_stale
        assets = [self.asset(1, 1, "outputs/b3_v01.wav", block_id="b3"),
                  self.asset(1, 2, "outputs/b3_v02.wav", block_id="b3")]
        scene = self.make_scene(
            expected_audio_slots=[self.slot("b3:1", "b3", "B3", 1)],
            audio_assets=assets,
            selected_block_audio={"b3:1": "a1v1"})
        entry = {"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                 "sources": [self.source("b3:1", assets[1], "B3")]}
        stale, reasons = is_scene_combined_stale(scene, entry, self.tmp)
        self.assertTrue(stale)
        self.assertTrue(any(
            "selected version (v01) differs from this Combined output "
            "(v02)" in r for r in reasons), reasons)

    def test_default_composition_newer_version_reason_unchanged(self):
        """No explicit selection: regenerating B1 to v02 keeps the
        approved P3.30(f) copy (\"B1 has a newer version: v02\")."""
        from engine.audio_provenance import is_scene_combined_stale
        assets = [self.asset(1, 1, "outputs/b1_v01.wav", block_id="b1"),
                  self.asset(1, 2, "outputs/b1_v02.wav", block_id="b1")]
        scene = self.make_scene(
            expected_audio_slots=[self.slot("b1:1", "b1", "B1", 1)],
            audio_assets=assets)
        entry = {"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                 "sources": [self.source("b1:1", assets[0], "B1")]}
        stale, reasons = is_scene_combined_stale(scene, entry, self.tmp)
        self.assertTrue(stale)
        self.assertIn("B1 has a newer version: v02", reasons)

    def test_strict_identity_same_version_different_asset(self):
        """Equal versions but a different asset id (defensive branch):
        stale with the generic differs reason."""
        from engine.audio_provenance import is_scene_combined_stale
        assets = [self.asset(1, 2, "outputs/b1_v02.wav", block_id="b1")]
        other = dict(assets[0], id="OTHER_ID")
        scene = self.make_scene(
            expected_audio_slots=[self.slot("b1:1", "b1", "B1", 1)],
            audio_assets=assets,
            selected_block_audio={"b1:1": "OTHER_ID_OR_ANY"})
        # lineage records a DIFFERENT asset id than the resolved one
        entry = {"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                 "sources": [self.source("b1:1", other, "B1")]}
        stale, reasons = is_scene_combined_stale(scene, entry, self.tmp)
        self.assertTrue(stale)
        self.assertTrue(any("selected audio differs" in r
                            for r in reasons), reasons)


# ===========================================================================
# R2 — structural staleness (expected slot set vs lineage)
# ===========================================================================
class TestR2StructuralStaleness(_PureBase):

    def _scene_two_slots(self):
        assets = [self.asset(1, 1, "outputs/b1_v01.wav", block_id="b1"),
                  self.asset(2, 1, "outputs/b2_v01.wav", block_id="b2")]
        scene = self.make_scene(
            expected_audio_slots=[self.slot("b1:1", "b1", "B1", 1),
                                  self.slot("b2:1", "b2", "B2", 2)],
            audio_assets=assets)
        return scene, assets

    def test_added_slot_after_combine_is_stale(self):
        from engine.audio_provenance import is_scene_combined_stale
        scene, assets = self._scene_two_slots()
        entry = {"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                 "sources": [self.source("b1:1", assets[0], "B1")]}
        stale, reasons = is_scene_combined_stale(scene, entry, self.tmp)
        self.assertTrue(stale)
        self.assertTrue(any(
            "Scene structure has changed since this Combined output "
            "(2 parts expected, 1 in snapshot)" in r for r in reasons),
            reasons)

    def test_removed_slot_after_combine_is_stale(self):
        from engine.audio_provenance import is_scene_combined_stale
        scene, assets = self._scene_two_slots()
        # Snapshot contains BOTH slots; the Scene now expects only B1.
        scene.expected_audio_slots = [self.slot("b1:1", "b1", "B1", 1)]
        entry = {"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                 "sources": [self.source("b1:1", assets[0], "B1"),
                             self.source("b2:1", assets[1], "B2")]}
        stale, reasons = is_scene_combined_stale(scene, entry, self.tmp)
        self.assertTrue(stale)
        self.assertTrue(any("structure has changed" in r for r in reasons))

    def test_matching_slot_set_no_structural_reason(self):
        from engine.audio_provenance import is_scene_combined_stale
        scene, assets = self._scene_two_slots()
        entry = {"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                 "sources": [self.source("b1:1", assets[0], "B1"),
                             self.source("b2:1", assets[1], "B2")]}
        stale, reasons = is_scene_combined_stale(scene, entry, self.tmp)
        self.assertFalse(stale, reasons)

    def test_structural_stale_scene_requires_review(self):
        """A structurally-stale-only Scene (combined exists, identity
        matches, slot set differs) must REQUIRE REVIEW — never a silent
        asset fallback."""
        from engine.audio_provenance import resolve_scene_output
        scene, assets = self._scene_two_slots()
        entry = {"id": "c1", "version": 1, "output_path": "outputs/c1.wav",
                 "duration": 20.0,
                 "sources": [self.source("b1:1", assets[0], "B1")]}
        scene.combined_outputs = [entry]
        self._file("outputs/c1.wav")
        res = resolve_scene_output(scene, self.tmp)
        self.assertTrue(res["requires_review"])
        self.assertIsNone(res["kind"])
        self.assertIn("stale", res["reason"])


# ===========================================================================
# SAFETY — a Part is NEVER the automatic Scene output
# ===========================================================================
class TestPartNeverResolves(_PureBase):

    def test_parts_only_scene_review_reason_mentions_remedy(self):
        from engine.audio_provenance import resolve_scene_output
        assets = [self.asset(1, 1, "outputs/p1.wav"),
                  self.asset(2, 1, "outputs/p2.wav")]
        scene = self.make_scene(
            expected_audio_slots=[
                self.slot("plain:1", None, "Plain narration", 1),
                self.slot("plain:2", None, "Plain narration", 2)],
            audio_assets=assets)
        res = resolve_scene_output(scene, self.tmp)
        self.assertTrue(res["requires_review"])
        self.assertIsNone(res["kind"])
        self.assertIn("Combine the Scene", res["reason"])

    def test_single_slot_scene_part_still_never_resolves(self):
        """No exceptions — not even a one-Part Scene."""
        from engine.audio_provenance import resolve_scene_output
        assets = [self.asset(1, 1, "outputs/only.wav")]
        scene = self.make_scene(
            expected_audio_slots=[self.slot("plain:1", None, "P", 1)],
            audio_assets=assets)
        res = resolve_scene_output(scene, self.tmp)
        self.assertTrue(res["requires_review"])

    def test_explicit_part_selection_still_honoured(self):
        """The SAFETY rule constrains AUTOMATIC resolution only. An
        EXPLICIT user selection of a Part asset resolves (CUSTOM)."""
        from engine.audio_provenance import resolve_scene_output
        assets = [self.asset(1, 1, "outputs/p1.wav")]
        scene = self.make_scene(
            expected_audio_slots=[self.slot("plain:1", None, "P", 1)],
            audio_assets=assets,
            selected_output={"kind": "asset", "id": "a1v1"})
        res = resolve_scene_output(scene, self.tmp)
        self.assertFalse(res["requires_review"])
        self.assertTrue(res["custom"])
        self.assertEqual(res["label"], "Selected \u00b7 CUSTOM")


# ===========================================================================
# R3 + REMEDY — GUI tests (real MainWindow + real audio combine)
# ===========================================================================
class _MWHarness(unittest.TestCase):
    """Real MainWindow + Engine (model boundary irrelevant: no
    generation runs — the combine path only uses AudioManager)."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.tmp = tempfile.mkdtemp(prefix="ss_p331_mw_")
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
                         return_value=(object(), None)),
        ]
        for p in self.patchers:
            p.start()
        self.win = MainWindow(self.engine)
        self.win._project_manager = type(
            "PM", (), {"__init__": lambda self: None,
                       "save_project": lambda self, p: None})()
        from engine.models import Project, Scene
        self.project = Project(name="Eden", id="proj1")
        self.scene_other = Scene(id="other scene", name="Other",
                                 project_id="proj1")
        self.scene = Scene(id="sc1abcd", name="Scene 03",
                           project_id="proj1")
        self.project.add_scene(self.scene_other)
        self.project.add_scene(self.scene)
        self.win._active_project = self.project
        self.win._active_scene = self.scene
        self.win._current_project = "Eden"

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

    # --- fixture: a fully-covered Scene with real WAVs ---------------
    def cover_scene(self, n_blocks=2):
        import soundfile as sf
        os.makedirs(os.path.join(self.tmp, "outputs"), exist_ok=True)
        slots, assets = [], []
        for i in range(1, n_blocks + 1):
            bid = "b{0}".format(i)
            slots.append({"slot_id": "{0}:1".format(bid),
                          "block_id": bid, "block_label": "B{0}".format(i),
                          "part_index": i, "part_of_block": 1,
                          "speaker": "", "character_id": None})
            rel = "outputs/B{0}_v01.wav".format(i)
            sf.write(os.path.join(self.tmp, rel), make_speech(1.0),
                     SAMPLE_RATE, subtype="FLOAT")
            assets.append({
                "id": "asset_b{0}_v1".format(i),
                "scene_id": self.scene.id, "output_path": rel,
                "duration": 1.0, "speaker": None, "character_id": None,
                "voice_profile_id": None, "generated_at": "20260101_120000",
                "block_id": bid, "part_index": i, "part_version": 1,
                "generation_run": "sc1abcd-r001"})
        self.scene.expected_audio_slots = slots
        self.scene.audio_assets = assets

    def stale_combined(self):
        """A combined output whose lineage points at v01 assets while a
        newer v02 exists for B1 (stale by R1)."""
        self.scene.audio_assets.append({
            "id": "asset_b1_v2", "scene_id": self.scene.id,
            "output_path": "outputs/B1_v02.wav", "duration": 1.0,
            "speaker": None, "character_id": None, "voice_profile_id": None,
            "generated_at": "20260101_120100", "block_id": "b1",
            "part_index": 1, "part_version": 2,
            "generation_run": "sc1abcd-r002"})
        import soundfile as sf
        sf.write(os.path.join(self.tmp, "outputs", "B1_v02.wav"),
                 make_speech(1.0), SAMPLE_RATE, subtype="FLOAT")
        sources = [{
            "slot_id": "b1:1", "block_id": "b1", "block_label": "B1",
            "part_index": 1, "asset_id": "asset_b1_v1", "part_version": 1,
            "speaker": None, "character_id": None,
            "output_path": "outputs/B1_v01.wav", "duration": 1.0},
            {"slot_id": "b2:1", "block_id": "b2", "block_label": "B2",
             "part_index": 2, "asset_id": "asset_b2_v1", "part_version": 1,
             "speaker": None, "character_id": None,
             "output_path": "outputs/B2_v01.wav", "duration": 1.0}]
        entry = {"id": "comb1", "version": 1,
                 "output_path": "outputs/Eden_Scene_03_Combined_v01.wav",
                 "duration": 2.0, "slot_count": 2, "silence_ms": 300,
                 "sources": sources, "created_at": "2026-01-01T12:00:00",
                 "generation_run": "sc1abcd-r001", "project_name": "Eden"}
        self.scene.combined_outputs = [entry]
        import soundfile as sf
        sf.write(os.path.join(self.tmp, "outputs",
                              "Eden_Scene_03_Combined_v01.wav"),
                 make_speech(2.0), SAMPLE_RATE, subtype="FLOAT")
        return entry


class TestR3CombineTakesOver(_MWHarness):

    def test_combine_clears_explicit_combined_selection(self):
        from engine.audio_provenance import resolve_scene_output
        self.cover_scene()
        self.stale_combined()
        self.scene.selected_output = {"kind": "scene_combined",
                                      "id": "comb1"}
        self.assertTrue(self.win._combine_scene(self.scene))
        self.assertIsNone(
            self.scene.selected_output,
            "R3: Combine must clear the older explicit selection")
        self.assertEqual(len(self.scene.combined_outputs), 2)
        res = resolve_scene_output(self.scene, self.tmp)
        self.assertEqual(res["label"], "Combined v02")
        self.assertFalse(res["stale"])
        self.assertFalse(res["custom"])

    def test_combine_clears_explicit_asset_selection(self):
        from engine.audio_provenance import resolve_scene_output
        self.cover_scene()
        self.stale_combined()
        self.scene.selected_output = {"kind": "asset",
                                      "id": "asset_b1_v1"}
        self.assertTrue(self.win._combine_scene(self.scene))
        self.assertIsNone(self.scene.selected_output)
        res = resolve_scene_output(self.scene, self.tmp)
        self.assertEqual(res["label"], "Combined v02")

    def test_combine_status_message_announces_takeover(self):
        from PySide6.QtWidgets import QStatusBar
        self.cover_scene()
        self.stale_combined()
        self.scene.selected_output = {"kind": "scene_combined",
                                      "id": "comb1"}
        self.win._combine_scene(self.scene)
        msg = QStatusBar.currentMessage(self.win._status_bar)
        self.assertIn("Scene output \u2192 Combined v02", msg)


class TestReviewRemedyActions(_MWHarness):

    def _review_dialog(self):
        from ui.panels.assemble_dialog import AssembleScenesDialog
        dlg = AssembleScenesDialog(self.project, self.tmp, None)
        self.addCleanup(dlg.deleteLater)
        # The production wiring (MainWindow._on_assemble_scenes):
        dlg.combine_scene_now_requested.connect(
            self.win._on_combine_scene_by_id)
        return dlg

    def _row_for(self, dlg, scene):
        for row in dlg._scene_rows:
            if row.scene.id == scene.id:
                return row
        return None

    def test_review_row_shows_remedy_buttons(self):
        self.cover_scene()
        self.stale_combined()
        dlg = self._review_dialog()
        row = self._row_for(dlg, self.scene)
        self.assertIsNotNone(row)
        self.assertFalse(row._checkbox.isEnabled(),
                         "REVIEW REQUIRED row is unchecked + disabled")
        texts = [b.text() for b in row.findChildren(
            __import__("PySide6.QtWidgets", fromlist=["QPushButton"])
            .QPushButton)]
        self.assertIn("Combine Scene Now", texts)
        self.assertIn("Choose Output\u2026", texts)

    def test_combine_scene_now_fixes_non_active_scene_end_to_end(self):
        """Clicking [Combine Scene Now] on a REVIEW REQUIRED row of a
        NON-ACTIVE Scene combines it (by id) and rebuilds the dialog —
        the row then resolves to the fresh Combined output."""
        self.cover_scene()
        self.stale_combined()
        # Make a DIFFERENT scene active (the remedy must work by id).
        self.win._active_scene = self.scene_other
        dlg = self._review_dialog()
        row = self._row_for(dlg, self.scene)
        self.assertIsNotNone(row)
        self.assertFalse(row._checkbox.isEnabled())
        combine_btn = None
        from PySide6.QtWidgets import QPushButton
        for btn in row.findChildren(QPushButton):
            if btn.text() == "Combine Scene Now":
                combine_btn = btn
                break
        self.assertIsNotNone(combine_btn)
        combine_btn.click()
        _process(100)
        # The combine happened on the RIGHT scene…
        self.assertEqual(len(self.scene.combined_outputs), 2,
                         "Combine-by-id must target the review Scene")
        self.assertEqual(len(self.scene_other.combined_outputs), 0)
        # …and the dialog rows were rebuilt: the row now resolves.
        row2 = self._row_for(dlg, self.scene)
        self.assertIsNotNone(row2)
        self.assertNotEqual(row2, row, "rows were rebuilt after combine")
        self.assertTrue(row2._checkbox.isEnabled(),
                        "the row resolves after the remedy")
        self.assertIn("Combined v02", row2.asset_label)

    def test_remedy_blocked_while_generation_running(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        self.cover_scene()
        self.stale_combined()
        # Fake a running batch manager.
        self.win._batch_manager = type(
            "BM", (), {"__init__": lambda self: None,
                       "is_running": True})()
        try:
            with patch.object(QMessageBox, "warning",
                              return_value=None) as warn:
                self.win._on_combine_scene_by_id(self.scene.id)
                self.assertTrue(warn.called)
            self.assertEqual(len(self.scene.combined_outputs), 1,
                             "no combine may run while generation is "
                             "in flight")
        finally:
            self.win._batch_manager = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
