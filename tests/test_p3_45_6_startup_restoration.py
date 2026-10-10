"""GUINEO P3.45.6 — PHASE 3: STARTUP PROJECT / SCENE RESTORATION.

Regression suite for the reported startup defect: on launch the
breadcrumb could display ``PROJECTS > GUINEO > Leírás`` while the left
panel lists stayed EMPTY until the user clicked the Projects/Scenes
navigation buttons.

ROOT CAUSE (source-verified at the P3.45.6 baseline):
``MainWindow._connect_engine`` restored only the DISPLAY names from
settings ("current_project" / "current_scene") into the breadcrumb,
then constructed a PHANTOM in-memory default Project (a NEW uuid, one
scene holding the demo/empty editor text) — the REAL persisted project
was never loaded from disk, and NOTHING populated the sidebar's
project/scene context lists at startup (the lists are built by
``_update_recents()``, which only ran on navigation clicks and later
mutations).

FIX (P3.45.6 Phase 3): ``_restore_last_active_project()`` runs during
``_connect_engine`` AFTER ProjectManager/RecentsManager initialisation
— it resolves the saved selection (legacy NAME schema, stable
Recents-entity-id preference for duplicates and stale-name recovery),
loads the REAL project through the EXISTING ``_load_native_project``
path (first scene + editor + sidebar + breadcrumb + characters +
recents wiring), then activates the SAVED scene through the EXISTING
``_switch_to_scene`` path. First run / deleted project keeps the
original honest in-memory fallback (nothing persisted over existing
data), and the context list renders even then.

Every test builds a REAL MainWindow (real sidebar/breadcrumb widgets)
over a temporary APP_ROOT with pre-seeded settings.json and on-disk
projects — the construction itself runs the startup path under test.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Fake torch (house pattern).
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

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import ui.main_window as mw_mod
from engine.engine import Engine
from engine.models import Project, Scene
from engine.project_manager import ProjectManager

SCENE_TEXT = ("A leírás jelenete. Ez a szöveg a mentett Leírás jelenethez "
              "tartozik, és a restaurálás után pontosan ezt kell látni.")
OTHER_TEXT = "Ez a bevezető jelenet szövege."


def _write_settings(app_root, application=None):
    """Seed settings.json exactly as a previous session would have it."""
    settings_dir = os.path.join(app_root, "settings")
    os.makedirs(settings_dir, exist_ok=True)
    data = {"application": application or {}}
    with open(os.path.join(settings_dir, "settings.json"), "w",
              encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def _blocks_payload():
    from engine.narration_blocks import PromptBlock
    b = PromptBlock(start_offset=0, end_offset=len(SCENE_TEXT))
    b.label = "Narration block"
    b.character_id = "char-anna"
    return [b.to_dict()]


class _StartupHarness(unittest.TestCase):
    """REAL MainWindow constructed over a temporary APP_ROOT.

    The construction itself exercises the startup path under test —
    no navigation click is ever simulated unless a test explicitly
    does so.
    """

    def setUp(self):
        from unittest.mock import patch, PropertyMock
        self.tmp = tempfile.mkdtemp(prefix="ss_p3456start_")
        os.makedirs(os.path.join(self.tmp, "projects"), exist_ok=True)
        # Patch APP_ROOT so settings/projects/presets resolve inside tmp.
        self._app_root_patch = patch.object(mw_mod, "APP_ROOT", self.tmp)
        self._app_root_patch.start()

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
        self.engine.play_audio = lambda p: None

    def _make_project(self, name, scenes_spec, project_id=None):
        """Persist a REAL project to disk (id stable across restarts)."""
        pm = ProjectManager(os.path.join(self.tmp, "projects"))
        project = Project(name=name, id=project_id)
        for scene_name, text, blocks in scenes_spec:
            s = Scene(name=scene_name, project_id=project.id,
                      text=text)
            if blocks is not None:
                s.narration_blocks = blocks
            project.add_scene(s)
        pm.save_project(project)
        return project

    def _construct(self):
        self.win = mw_mod.MainWindow(self.engine)

    def tearDown(self):
        if getattr(self, "win", None) is not None:
            try:
                self.win.close()
                self.win.deleteLater()
            except Exception:
                pass
        for p in self.patchers:
            p.stop()
        self._app_root_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        try:
            end = __import__("time").time() + 0.05
            while __import__("time").time() < end:
                QApplication.processEvents()
        except Exception:
            pass
        self.win = None
        self.engine = None
        import gc
        gc.collect()


class TestStartupRestoration(_StartupHarness):

    def test_startup_restores_saved_project_and_scene(self):
        """THE regression: launch with a saved GUINEO/Leírás selection.

        The real project loads (NOT a phantom), the SAVED scene (not
        the first) is active, the editor text and Blocks restore, the
        breadcrumb agrees with the actual model objects, and the
        left-panel Projects list is populated WITHOUT any click.
        """
        saved = self._make_project("GUINEO", [
            ("01 Intro", OTHER_TEXT, []),
            ("Leírás", SCENE_TEXT, _blocks_payload()),
        ])
        _write_settings(self.tmp, {
            "current_project": "GUINEO",
            "current_scene": "Leírás",
            "first_run_completed": True,
        })
        self._construct()
        win = self.win

        # Real project (stable id), not a phantom.
        self.assertIsNotNone(win._active_project)
        self.assertEqual(win._active_project.id, saved.id)
        self.assertEqual(win._active_project.name, "GUINEO")
        # The SAVED scene is active (not the first).
        self.assertEqual(win._active_scene.name, "Leírás")
        self.assertEqual(win._current_scene, "Leírás")
        # Editor text + Blocks restored through the existing load path.
        self.assertEqual(win._editor.get_text(), SCENE_TEXT)
        blocks = win._editor.block_manager.blocks
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].label, "Narration block")
        self.assertEqual(blocks[0].character_id, "char-anna")
        # Breadcrumb agrees with the ACTUAL objects.
        bar = win._project_scene_bar
        self.assertEqual(bar._project_label.text(), "GUINEO")
        self.assertEqual(bar._scene_label.text(), "Leírás")
        # Left-panel Projects list populated WITHOUT a navigation click.
        sb = win._sidebar
        self.assertIsNotNone(sb._context_entries)
        ids = [e.get("id") for e in sb._context_entries]
        self.assertIn(saved.id, ids)
        # The selected sidebar row matches the active object.
        self.assertEqual(sb._context_active_id, saved.id)
        # The Scenes list (sidebar scene rows) is populated and the
        # active scene row is the SAVED scene.
        scene_ids = [s["id"] for s in sb._scenes]
        self.assertIn(win._active_scene.id, scene_ids)
        self.assertEqual(sb._active_scene_id, win._active_scene.id)

    def test_startup_scene_list_populated_immediately(self):
        """The Scenes context list derives from the ACTIVE project the
        moment the context is requested — populated data at startup."""
        saved = self._make_project("GUINEO", [
            ("01 Intro", OTHER_TEXT, []),
            ("Leírás", SCENE_TEXT, []),
        ])
        _write_settings(self.tmp, {
            "current_project": "GUINEO",
            "current_scene": "Leírás",
            "first_run_completed": True,
        })
        self._construct()

        # No click: simulate the context switch the nav button would
        # perform — the data must already be complete.
        entries = self.win._build_context_list_entries("Scenes")
        names = [e["name"] for e in entries]
        self.assertEqual(names, ["01 Intro", "Leírás"])
        entries_p = self.win._build_context_list_entries("Projects")
        self.assertEqual([e["name"] for e in entries_p], ["GUINEO"])
        self.assertEqual(self.win._active_project.id, saved.id)

    def test_saved_scene_missing_falls_back_to_first(self):
        saved = self._make_project("GUINEO", [
            ("01 Intro", OTHER_TEXT, []),
            ("Leírás", SCENE_TEXT, []),
        ])
        _write_settings(self.tmp, {
            "current_project": "GUINEO",
            "current_scene": "Nincs ilyen jelenet",
            "first_run_completed": True,
        })
        self._construct()

        self.assertEqual(self.win._active_project.id, saved.id)
        self.assertEqual(self.win._active_scene.name, "01 Intro")
        self.assertEqual(self.win._editor.get_text(), OTHER_TEXT)

    def test_first_run_no_projects_keeps_default(self):
        """First run: no settings file, no projects on disk."""
        self._construct()
        win = self.win

        self.assertIsNotNone(win._active_project)
        self.assertIsNotNone(win._active_scene)
        # In-memory default: NOT an on-disk project.
        pm = ProjectManager(os.path.join(self.tmp, "projects"))
        self.assertEqual(pm.list_projects(), [])
        # The context list is RENDERED (empty is legitimate; unrendered
        # is the defect).
        self.assertEqual(win._sidebar._context_entries, [])

    def test_missing_project_falls_back_honestly(self):
        """Deleted project: no phantom is persisted over existing data
        and the fallback does not pretend restoration succeeded."""
        self._make_project("Real", [("S", "text", [])])
        _write_settings(self.tmp, {
            "current_project": "Ghost",   # deleted since last session
            "current_scene": "Leírás",
            "first_run_completed": True,
        })
        projects_before = sorted(os.listdir(
            os.path.join(self.tmp, "projects")))

        self._construct()

        pm = ProjectManager(os.path.join(self.tmp, "projects"))
        names = [p.name for p in pm.list_projects()]
        self.assertEqual(names, ["Real"])   # nothing new persisted
        self.assertEqual(sorted(os.listdir(
            os.path.join(self.tmp, "projects"))), projects_before)
        # Honest fallback: in-memory default project named "Ghost".
        self.assertEqual(self.win._active_project.name, "Ghost")

    def test_stale_name_recovers_via_recents_id(self):
        """The saved NAME no longer matches (renamed?), but the stable
        Recents entity_id still resolves the real project."""
        saved = self._make_project("Renamed Later", [
            ("Leírás", SCENE_TEXT, []),
        ])
        _write_settings(self.tmp, {
            "current_project": "Old Name",
            "current_scene": "Leírás",
            "first_run_completed": True,
            "recents": {
                "projects": [{"entity_id": saved.id,
                              "project_id": "",
                              "display_name": "Old Name"}],
                "scenes": [],
            },
        })
        self._construct()

        self.assertEqual(self.win._active_project.id, saved.id)
        self.assertEqual(self.win._active_scene.name, "Leírás")
        self.assertEqual(self.win._editor.get_text(), SCENE_TEXT)

    def test_duplicate_project_names_resolved_by_stable_id(self):
        """Two projects share the display name: the Recents entity_id
        (the stable identifier) disambiguates — the display name is
        never treated as a unique Project ID."""
        first = self._make_project("Dup", [("A", "first text", [])])
        second = self._make_project("Dup", [("B", "second text", [])])
        _write_settings(self.tmp, {
            "current_project": "Dup",
            "current_scene": "B",
            "first_run_completed": True,
            "recents": {
                "projects": [{"entity_id": second.id,
                              "project_id": "",
                              "display_name": "Dup"}],
                "scenes": [],
            },
        })
        self._construct()

        self.assertEqual(self.win._active_project.id, second.id)
        self.assertNotEqual(self.win._active_project.id, first.id)
        self.assertEqual(self.win._active_scene.name, "B")
        self.assertEqual(self.win._editor.get_text(), "second text")

    def test_legacy_settings_without_recents(self):
        """Legacy settings store ONLY names (no recents section at
        all): pure-name resolution restores the project + scene."""
        saved = self._make_project("GUINEO", [
            ("01 Intro", OTHER_TEXT, []),
            ("Leírás", SCENE_TEXT, []),
        ])
        _write_settings(self.tmp, {
            "current_project": "GUINEO",
            "current_scene": "Leírás",
            "first_run_completed": True,
        })
        self._construct()

        self.assertEqual(self.win._active_project.id, saved.id)
        self.assertEqual(self.win._active_scene.name, "Leírás")
        self.assertEqual(self.win._editor.get_text(), SCENE_TEXT)

    def test_persisted_selection_agrees_after_restore(self):
        """After restoration the persisted settings agree with the
        ACTUAL active objects (the next launch restores the same)."""
        saved = self._make_project("GUINEO", [
            ("01 Intro", OTHER_TEXT, []),
            ("Leírás", SCENE_TEXT, []),
        ])
        _write_settings(self.tmp, {
            "current_project": "GUINEO",
            "current_scene": "Leírás",
            "first_run_completed": True,
        })
        self._construct()

        sm = self.win._settings_manager
        self.assertEqual(sm.get("application", "current_project"), "GUINEO")
        self.assertEqual(sm.get("application", "current_scene"), "Leírás")

    def test_recents_recorded_at_startup(self):
        """The restored project/scene enter the Recents MRU through the
        existing load path — the Recents panel is populated too."""
        saved = self._make_project("GUINEO", [
            ("Leírás", SCENE_TEXT, []),
        ])
        _write_settings(self.tmp, {
            "current_project": "GUINEO",
            "current_scene": "Leírás",
            "first_run_completed": True,
        })
        self._construct()

        recents = self.win._recents_manager.get_recent_projects()
        self.assertTrue(any(r.entity_id == saved.id for r in recents))


class TestShippedDefaultProjectRestore(_StartupHarness):
    """A shipped-style first-run layout: a "Default" project on disk whose
    scene holds the first-run demo text (seeded into the temporary
    APP_ROOT by the test itself).

    P3.45.6 Phase 3: a genuinely FRESH install (first run not yet
    completed — the merged schema default "Default" was never chosen
    by anyone) keeps the ORIGINAL first-run experience exactly: the
    demo text arrives through the first-run insertion path, the
    in-memory default project stays, and NO restoration fires. This
    also keeps the first run immune to the state of any such project
    file on disk (the battery-order hazard: before the P3.45.6
    closeout, suite runs mutated the then-tracked
    projects/Default/project.json — the hygiene defect recorded and
    CLOSED in OPEN_BUGS; the tracked file itself was removed and the
    suite now runs over an isolated session APP_ROOT, see
    tests/conftest.py)."""

    def test_first_run_untouched_with_shipped_default_on_disk(self):
        from engine.models import Scene as _Scene
        demo = "Ez egy rövid GUINEO teszt. Ez a mintaszöveg..."
        pm_dir = os.path.join(self.tmp, "projects")
        project = Project(name="Default", id="dflt0001")
        scene = _Scene(name="Untitled", project_id="dflt0001", text=demo)
        project.add_scene(scene)
        ProjectManager(pm_dir).save_project(project)
        # NO settings file at all — a genuinely fresh install (the
        # Engine init creates defaults with first_run_completed=False).

        self._construct()
        win = self.win

        # NO restoration: the in-memory default project (a NEW uuid),
        # not the shipped one.
        self.assertIsNotNone(win._active_project)
        self.assertNotEqual(win._active_project.id, "dflt0001")
        # The first-run demo text is present through the ORIGINAL
        # insertion path.
        self.assertIn("GUINEO teszt", win._editor.get_text())
        # The left-panel Projects list renders the REAL on-disk state
        # (the shipped Default project is listed — clickable), while NO
        # listed row is marked active (the phantom default is not an
        # on-disk project, so the highlight matches nothing visible).
        ids = [e.get("id") for e in win._sidebar._context_entries]
        self.assertIn("dflt0001", ids)
        self.assertNotIn(win._sidebar._context_active_id, ids)

    def test_second_run_restores_shipped_default(self):
        """After the first run completed with the (unchanged) persisted
        "Default" selection, the shipped Default project restores —
        populated lists, breadcrumb == actual objects."""
        from engine.models import Scene as _Scene
        demo = "Ez egy rövid GUINEO teszt. Ez a mintaszöveg..."
        pm_dir = os.path.join(self.tmp, "projects")
        project = Project(name="Default", id="dflt0001")
        scene = _Scene(name="Untitled", project_id="dflt0001", text=demo)
        project.add_scene(scene)
        ProjectManager(pm_dir).save_project(project)
        _write_settings(self.tmp, {
            "current_project": "Default",
            "current_scene": "Untitled",
            "first_run_completed": True,
        })

        self._construct()
        win = self.win

        self.assertEqual(win._active_project.id, "dflt0001")
        self.assertEqual(win._active_scene.name, "Untitled")
        self.assertEqual(win._editor.get_text(), demo)
        self.assertEqual(win._sidebar._context_entries[0]["id"], "dflt0001")


if __name__ == "__main__":
    unittest.main(verbosity=2)
