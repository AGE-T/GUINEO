"""
SpeechStudio Engine - Project Manager
=====================================

Manages Project entities: create, load, save, list, delete.

A Project is stored as a directory structure:

    projects/
    └── <project_id>/
        ├── project.json       (Project metadata, characters, scenes)
        ├── scenes/
        │   └── <scene_id>.json
        └── characters/
            └── <character_id>.json

The project.json file contains the full Project.to_dict() including
embedded Scene and Character data. Individual scene/character files
are an optimisation for large projects (not yet implemented — Phase 2+).

Migration:
    Old .sproj files can be imported via import_sproj(path).
    Old .scene.json files can be imported via import_scene_json(path, project_id).
"""

from __future__ import annotations
import os
import json
import time
from typing import List, Optional, Dict, Any

from engine.models import Project, Scene, Character, GenerationParameters
from engine.logger import get_logger

logger = get_logger("project_manager")


class ProjectManager:
    """Manages Project entities on disk.

    Each project is stored as a directory under ``projects/`` containing
    a ``project.json`` file with the full project state.

    P3.44.1 §7/§9 (session instance cache): ``get_project``/
    ``list_projects`` return the SAME in-memory Project object for a
    given project id within a manager's lifetime. Previously every call
    re-read ``project.json`` from disk — switching projects AWAY and
    BACK constructed a FRESH Project, so every unsaved in-memory Scene
    edit (plain text, Narration Blocks, block metadata, character
    associations) was silently DISCARDED (the runtime-verified data
    loss: the old object holding the unsaved state became unreachable
    and the new object was built from the last SAVED file). The live
    object IS the authoritative editing state; the disk file is the
    persisted snapshot. A ``reload_project`` escape hatch is provided
    for callers that explicitly need the disk truth.
    """

    PROJECT_FORMAT = "speechstudio-project-v2"
    FORMAT_VERSION = 2

    def __init__(self, projects_dir: str):
        self._dir = projects_dir
        os.makedirs(self._dir, exist_ok=True)
        # P3.44.1: project id -> the LIVE Project instance for this
        # session. Populated by get/list/save; evicted by delete.
        self._cache: Dict[str, Project] = {}

    @property
    def directory(self) -> str:
        return self._dir

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def _load_from_disk(self, project_id: str) -> Optional[Project]:
        """Read one project.json (no cache involvement)."""
        project_path = os.path.join(self._dir, project_id, "project.json")
        if not os.path.isfile(project_path):
            return None
        try:
            with open(project_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Project.from_dict(data)
        except Exception as exc:
            logger.error("Failed to load project %s: %s", project_id, exc)
            return None

    def _cached(self, project: Project) -> Project:
        """Register/refresh ``project`` as the LIVE instance for its id."""
        if project is not None and project.id:
            self._cache[project.id] = project
        return project

    def list_projects(self) -> List[Project]:
        """Return all projects, sorted by modified_at (newest first).

        P3.44.1: returns the CACHED live instances when a project is
        already loaded in this session (unsaved in-memory edits survive
        project switches); only genuinely new ids are read from disk.
        """
        projects = []
        if not os.path.isdir(self._dir):
            return projects
        for name in sorted(os.listdir(self._dir)):
            project_path = os.path.join(self._dir, name, "project.json")
            if not os.path.isfile(project_path):
                continue
            cached = self._cache.get(name)
            if cached is not None:
                projects.append(cached)
                continue
            try:
                with open(project_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                project = Project.from_dict(data)
                self._cached(project)
                projects.append(project)
            except Exception as exc:
                logger.warning("Failed to load project %s: %s", name, exc)
        projects.sort(key=lambda p: p.modified_at or p.created_at or "",
                      reverse=True)
        return projects

    def get_project(self, project_id: str) -> Optional[Project]:
        """Load a single project by id.

        P3.44.1: returns the LIVE session instance when cached —
        switching projects away and back must not destroy unsaved
        in-memory Scene state (the previous re-read-from-disk behaviour
        did exactly that). Use ``reload_project`` for the disk truth.
        """
        cached = self._cache.get(project_id)
        if cached is not None:
            return cached
        return self._cached(self._load_from_disk(project_id))

    def reload_project(self, project_id: str) -> Optional[Project]:
        """Explicitly re-read a project from DISK, replacing the cached
        live instance (P3.44.1 escape hatch for callers that need the
        persisted truth, e.g. after external file changes)."""
        self._cache.pop(project_id, None)
        return self._cached(self._load_from_disk(project_id))

    def find_scene_owner(self, scene_id: str) -> Optional[Project]:
        """P3.44.1 §6: the project that OWNS ``scene_id`` (live instances).

        A batch may run for a Scene of project A while the user has
        already switched to project B — generation results, batch
        completion recomputes and regen versioning must route to A's
        scene, never to B's (the runtime-verified defect: the result's
        fallback registered A's audio on B's ACTIVE scene). Scans the
        cached instances plus any project on disk (loading + caching
        them as a side effect, so the owner it returns IS the instance
        the user gets when switching to that project).
        """
        if not scene_id:
            return None
        for p in self.list_projects():
            if any(s.id == scene_id for s in p.scenes):
                return p
        return None

    def _sanitize_scene_audio_paths(self, project: Project) -> None:
        """P3.25 (audit SS-H08): strip unsafe audio asset paths at import.

        Model-supplied ``output_path`` values that are absolute or escape
        the projects directory's app root context are set to "" (the
        asset METADATA is preserved, the dangerous path is not). Without
        this, a crafted exported project could exfiltrate arbitrary files
        through a re-export (see ProjectExporter containment) or point
        playback/deletion outside the intended directory.
        """
        import os as _os

        def _is_safe_rel(path) -> bool:
            if not isinstance(path, str) or not path:
                return True  # empty is safe (handled as "no path")
            if "\x00" in path:
                return False
            # Model paths are always forward-slash relative paths; a
            # backslash is illegitimate AND is a separator on Windows
            # ("..\evil" must be rejected platform-independently).
            if "\\" in path:
                return False
            if _os.path.isabs(path):
                return False
            # Reject traversal: any '..' path component.
            parts = _os.path.normpath(path).split(_os.sep)
            return ".." not in parts

        for scene in project.scenes:
            assets = getattr(scene, "audio_assets", None)
            if not isinstance(assets, list):
                scene.audio_assets = []
                continue
            for asset in assets:
                if isinstance(asset, dict) and \
                        not _is_safe_rel(asset.get("output_path")):
                    logger.warning(
                        "P3.7 import: stripped unsafe audio output_path "
                        "%r from scene '%s' (path escapes the app root).",
                        asset.get("output_path"), scene.name)
                    asset["output_path"] = ""
            # P3.28: the Scene-level combined outputs carry the same class
            # of path (plus their per-slot lineage output_paths — those are
            # informational lineage records, but sanitize them too).
            scene_combined = getattr(scene, "combined_outputs", None)
            if isinstance(scene_combined, list):
                for entry in scene_combined:
                    if isinstance(entry, dict):
                        if not _is_safe_rel(entry.get("output_path")):
                            logger.warning(
                                "P3.28 import: stripped unsafe scene "
                                "combined output path %r.",
                                entry.get("output_path"))
                            entry["output_path"] = ""
                        for source in (entry.get("sources") or []):
                            if (isinstance(source, dict)
                                    and not _is_safe_rel(
                                        source.get("output_path"))):
                                source["output_path"] = ""
        # Combined outputs carry the same class of path.
        combined = getattr(project, "combined_outputs", None)
        if isinstance(combined, list):
            for entry in combined:
                if isinstance(entry, dict) and \
                        not _is_safe_rel(entry.get("output_path")):
                    logger.warning(
                        "P3.7 import: stripped unsafe combined output path "
                        "%r.", entry.get("output_path"))
                    entry["output_path"] = ""
                if isinstance(entry, dict) and \
                        not _is_safe_rel(entry.get("mp3_path")):
                    entry["mp3_path"] = ""

    def _restore_combined_audio(self, project: Project,
                                 export_dir: str) -> None:
        """P3.28 (Rec 19 + §21): restore export-bundled audio into outputs/.

        Scene audio_assets, Scene-level combined_outputs, and project-level
        combined_outputs whose output_path resolves INSIDE the export
        directory are copied back to <app_root>/outputs/ and the model
        paths rewritten to the canonical outputs-relative form — so an
        imported project's coverage, versions, resolution, playback and
        staleness all work immediately. Missing/unresolvable files keep
        their metadata (the entry shows as missing — never a crash).
        """
        import shutil as _shutil

        app_root = os.path.dirname(os.path.abspath(self._dir))
        outputs_dir = os.path.join(app_root, "outputs")
        try:
            os.makedirs(outputs_dir, exist_ok=True)
        except OSError as exc:
            logger.warning("P3.28 import: cannot create outputs dir: %s", exc)
            return

        def _restore(rel_path):
            if (not isinstance(rel_path, str) or not rel_path
                    or os.path.isabs(rel_path) or "\\" in rel_path):
                return None
            src = os.path.realpath(os.path.join(export_dir, rel_path))
            try:
                if os.path.commonpath([os.path.realpath(export_dir), src]) \
                        != os.path.realpath(export_dir):
                    return None
            except ValueError:
                return None
            if not os.path.isfile(src):
                return None
            base = os.path.basename(rel_path.replace("\\", "/"))
            dst = os.path.join(outputs_dir, base)
            counter = 2
            while os.path.exists(dst):
                root, ext = os.path.splitext(base)
                dst = os.path.join(
                    outputs_dir, "{0}_{1}{2}".format(root, counter, ext))
                counter += 1
            try:
                _shutil.copy2(src, dst)
            except OSError as exc:
                logger.warning("P3.28 import: combined audio copy failed "
                               "(%s): %s", rel_path, exc)
                return None
            return os.path.relpath(dst, app_root).replace(os.sep, "/")

        # Scene audio assets (P3.28 §21: the import must restore the
        # relationships — asset audio is bundled at scenes/<n>/audio/).
        for scene in project.scenes:
            for asset in (getattr(scene, "audio_assets", None) or []):
                if not isinstance(asset, dict):
                    continue
                new_rel = _restore(asset.get("output_path", ""))
                if new_rel:
                    asset["output_path"] = new_rel
        # Scene-level combined outputs.
        for scene in project.scenes:
            entries = getattr(scene, "combined_outputs", None)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                new_rel = _restore(entry.get("output_path", ""))
                if new_rel:
                    entry["output_path"] = new_rel
        entries = getattr(project, "combined_outputs", None)
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                new_rel = _restore(entry.get("output_path", ""))
                if new_rel:
                    entry["output_path"] = new_rel
                mp3_rel = entry.get("mp3_path", "")
                if isinstance(mp3_rel, str) and mp3_rel:
                    src = os.path.join(export_dir, mp3_rel)
                    if os.path.isfile(src):
                        base = os.path.basename(mp3_rel.replace("\\", "/"))
                        dst = os.path.join(outputs_dir, base)
                        if not os.path.exists(dst):
                            try:
                                _shutil.copy2(src, dst)
                                entry["mp3_path"] = os.path.relpath(
                                    dst, app_root).replace(os.sep, "/")
                            except OSError:
                                pass

    def save_project(self, project: Project) -> str:
        """Save a project to disk. Returns the project directory path.

        P3.28 (§5 / Rec 7): every save recomputes each Scene's DERIVED
        coverage state — the persisted project.json always carries the
        honest COMPLETE/PARTIAL/ERROR/NOT_GENERATED value (a persisted
        GENERATING from a crash honestly downgrades to the derived
        state; legacy "complete" values may honestly downgrade to
        "partial" — corrected semantics, not data loss).

        P3.44.1: the saved object becomes/refreshes the LIVE session
        instance for its id (so a subsequent get_project returns the
        very object the user keeps editing — identity, not a disk
        round-trip).
        """
        if not project.id:
            import uuid
            project.id = str(uuid.uuid4())[:12]

        # P3.44.1: register as the live instance FIRST (the save itself
        # does not replace the object — it persists its current state).
        self._cached(project)

        # P3.28 §5: recompute coverage on project save (pure derivation
        # from the authoritative audio_assets stream — never stored).
        try:
            from engine.audio_provenance import recompute_all_scene_status
            recompute_all_scene_status(project)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("P3.28: coverage recompute on save failed: %s",
                           exc)

        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not project.created_at:
            project.created_at = now
        project.modified_at = now

        # P3.21: Auto-assign sequential sort_order if all scenes have 0
        # (backward compat: old projects get ordered by insertion on first save)
        if project.scenes and all(s.sort_order == 0 for s in project.scenes):
            for i, scene in enumerate(project.scenes):
                scene.sort_order = i

        project_dir = os.path.join(self._dir, project.id)
        os.makedirs(project_dir, exist_ok=True)
        os.makedirs(os.path.join(project_dir, "scenes"), exist_ok=True)
        os.makedirs(os.path.join(project_dir, "characters"), exist_ok=True)

        data = {
            "format": self.PROJECT_FORMAT,
            "version": self.FORMAT_VERSION,
            **project.to_dict(),
        }

        project_path = os.path.join(project_dir, "project.json")
        with open(project_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info("Saved project: %s (%s) with %d scenes, %d characters",
                    project.name, project.id,
                    len(project.scenes), len(project.characters))
        return project_dir

    def delete_project(self, project_id: str) -> bool:
        """Delete a project directory. Returns True if deleted.

        P3.23 (design record §85 — containment checks): project_id is
        untrusted input (it originates from imported project JSON and UI
        selection). A crafted ID containing "../" or an absolute path
        previously allowed rmtree of an ARBITRARY directory on disk.
        The ID must be a single safe path component and the resolved
        directory must remain inside the projects directory.
        """
        import shutil
        # Reject traversal / absolute / malformed project IDs.
        if not project_id or not isinstance(project_id, str):
            return False
        if project_id in (".", ".."):
            return False
        if ("/" in project_id or "\\" in project_id
                or "\x00" in project_id or os.path.isabs(project_id)):
            logger.warning("Rejected unsafe project id for delete: %r",
                           project_id)
            return False
        project_dir = os.path.join(self._dir, project_id)
        # Defence in depth: resolved path must stay inside projects dir.
        base_abs = os.path.abspath(self._dir)
        target_abs = os.path.abspath(project_dir)
        try:
            if os.path.commonpath([base_abs, target_abs]) != base_abs:
                logger.warning(
                    "Rejected project delete outside projects dir: %r",
                    project_id)
                return False
        except ValueError:
            return False
        if not os.path.isdir(project_dir):
            return False
        try:
            shutil.rmtree(project_dir)
            # P3.44.1: evict the live instance — a deleted project must
            # never resurrect from the session cache.
            self._cache.pop(project_id, None)
            logger.info("Deleted project: %s", project_id)
            return True
        except Exception as exc:
            logger.error("Failed to delete project %s: %s", project_id, exc)
            return False

    # ------------------------------------------------------------------
    # Scene operations
    # ------------------------------------------------------------------
    def save_scene(self, project_id: str, scene: Scene) -> str:
        """Save a scene within a project. Returns the scene file path."""
        if not scene.id:
            import uuid
            scene.id = str(uuid.uuid4())[:12]
        scene.project_id = project_id

        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not scene.created_at:
            scene.created_at = now
        scene.modified_at = now

        scene_dir = os.path.join(self._dir, project_id, "scenes")
        os.makedirs(scene_dir, exist_ok=True)
        scene_path = os.path.join(scene_dir, f"{scene.id}.json")

        with open(scene_path, "w", encoding="utf-8") as f:
            json.dump(scene.to_dict(), f, indent=2, ensure_ascii=False)

        logger.info("Saved scene: %s (%s) in project %s",
                    scene.name, scene.id, project_id)
        return scene_path

    def load_scene(self, project_id: str, scene_id: str) -> Optional[Scene]:
        """Load a scene from a project."""
        scene_path = os.path.join(self._dir, project_id, "scenes", f"{scene_id}.json")
        if not os.path.isfile(scene_path):
            return None
        try:
            with open(scene_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Scene.from_dict(data)
        except Exception as exc:
            logger.error("Failed to load scene %s: %s", scene_id, exc)
            return None

    # ------------------------------------------------------------------
    # Migration / Import
    # ------------------------------------------------------------------
    def import_sproj(self, sproj_path: str) -> Optional[Project]:
        """Import a legacy .sproj file as a new Project.

        Creates a Project with a single Scene containing the editor text
        and generation settings from the .sproj file.

        Args:
            sproj_path: path to the .sproj file.

        Returns:
            The created Project, or None on failure.
        """
        try:
            with open(sproj_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.error("Failed to read .sproj file: %s", exc)
            return None

        # Extract fields from legacy .sproj format
        project_name = data.get("project", "Imported Project")
        text = data.get("text", "")
        voice_id = data.get("voice_id")
        emotion = data.get("emotion")
        style = data.get("style")
        speed = data.get("speed", "Normal")
        pitch = data.get("pitch", "Normal")
        delivery = data.get("delivery", "Normal")
        params_data = data.get("parameters", {})
        params = GenerationParameters.from_dict(params_data) if params_data else None

        # Create a Scene from the .sproj content
        scene = Scene(
            name="Scene 01",
            text=text,
            emotion=emotion,
            style=style,
            speed=speed,
            pitch=pitch,
            delivery=delivery,
            parameters=params,
            status="draft",
        )

        # Create the Project
        project = Project(name=project_name)
        project.add_scene(scene)

        # If there's a voice_id, create a Character for it
        if voice_id:
            character = Character(
                name="Narrator",
                voice_profile_id=voice_id,
                role="narrator",
            )
            project.add_character(character)
            scene.character_ids.append(character.id)

        # Save the new project
        self.save_project(project)
        logger.info("Imported .sproj as project: %s (%s)", project.name, project.id)
        return project

    def import_scene_json(self, scene_path: str, project_id: str) -> Optional[Scene]:
        """Import a legacy .scene.json file into an existing project.

        Args:
            scene_path: path to the .scene.json file.
            project_id: the project to import into.

        Returns:
            The created Scene, or None on failure.
        """
        try:
            with open(scene_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.error("Failed to read .scene.json file: %s", exc)
            return None

        # Create a Scene from the dialogue scene
        scene_name = data.get("name", "Imported Scene")
        lines = data.get("lines", [])

        # Reconstruct text from dialogue lines
        text_parts = []
        for line in lines:
            speaker = line.get("speaker", "")
            line_text = line.get("text", "")
            if speaker:
                text_parts.append(f"${speaker}:\n{line_text}")
            else:
                text_parts.append(line_text)
        text = "\n\n".join(text_parts)

        scene = Scene(
            name=scene_name,
            project_id=project_id,
            text=text,
            status="draft",
        )

        # Save the scene
        self.save_scene(project_id, scene)
        logger.info("Imported .scene.json as scene: %s (%s)", scene.name, scene.id)
        return scene

    # ------------------------------------------------------------------
    # P3.7: Exported Project Import
    # ------------------------------------------------------------------
    def import_project_dir(self, export_dir: str,
                            new_id: bool = True) -> Optional[Project]:
        """Import an exported Project directory (P3.7 export structure).

        Reads the <export_dir>/project.json + scenes/*/scene.json +
        characters/*/character.json structure produced by ProjectExporter,
        reconstructs the authoritative Project, and saves it as a new
        native project under projects/.

        This is NOT a second import architecture — it reuses the same
        Project.from_dict() / Project.to_dict() model and the same
        save_project() persistence path as native projects.

        Audio assets referenced in scene.json keep their relative paths
        (pointing into the export's scenes/<name>/audio/ folder). These
        paths are preserved as-is so the exported audio is accessible
        relative to the project location. If the user wants the audio
        copied into outputs/, that is a separate concern.

        Args:
            export_dir: path to the exported project root (contains
                        project.json, scenes/, characters/).
            new_id: if True (default), generate a new project ID so the
                    imported project doesn't collide with the original.
                    If False, keep the original ID (use with care).

        Returns:
            The imported Project, or None on failure.
        """
        project_json_path = os.path.join(export_dir, "project.json")
        if not os.path.isfile(project_json_path):
            logger.error("P3.7 import: project.json not found in %s", export_dir)
            return None
        try:
            with open(project_json_path, "r", encoding="utf-8",
                      errors="replace") as f:
                project_data = json.load(f)
            if not isinstance(project_data, dict):
                logger.error("P3.7 import: project.json root is not a JSON "
                             "object.")
                return None
        except Exception as exc:
            logger.error("P3.7 import: failed to read project.json: %s", exc)
            return None

        # P3.25 (audit SS-H07 import robustness): a structurally invalid
        # project.json (e.g. {"scenes": "hello"}) previously crashed with
        # AttributeError straight out of the import path. Rejected
        # gracefully now.
        try:
            project = Project.from_dict(project_data)
        except Exception as exc:
            logger.error("P3.7 import: malformed project data "
                         "(%s: %s) — import rejected.", type(exc).__name__,
                         exc)
            return None

        # P3.25 (audit SS-H08): SANITIZE untrusted audio asset paths at
        # import. A crafted export could carry traversal / absolute
        # output_paths (e.g. "../../secrets.txt"); kept verbatim they
        # escape the app root on re-export (arbitrary file exfiltration)
        # and on any delete/playback that resolves them. Escaping paths
        # are STRIPPED from the imported model (asset metadata kept).
        self._sanitize_scene_audio_paths(project)

        # Optionally regenerate the ID to avoid collisions with the original
        if new_id:
            import uuid
            project.id = str(uuid.uuid4())[:12]
            # Re-link scenes to the new project ID
            for scene in project.scenes:
                scene.project_id = project.id

        # The project_data from the export may contain scenes with audio
        # paths relative to the export dir. When we save_project() the
        # native project.json is written — the scene audio_assets keep
        # their relative paths (they point into the export's audio/ dir).
        # This is acceptable: the metadata is preserved, and the audio
        # files are co-located with the scene data.

        # If the export also wrote individual scene.json files, we prefer
        # those (they are the per-scene export). The project.json's scenes
        # list is the authoritative source — individual scene.json files
        # are redundant but kept for human readability.
        scenes_export_dir = os.path.join(export_dir, "scenes")
        if os.path.isdir(scenes_export_dir):
            # The project.json already contains the full scenes data
            # (ProjectExporter writes both). We use the project.json data.
            pass

        # Save as a new native project
        # P3.28 (Rec 19): restore combined audio — the export carries
        # WAV copies under combined_outputs/ (scene-level) whose paths in
        # the model are export-relative. Copy them back into outputs/ and
        # rewrite the paths so playback/assembly/staleness all work in
        # the imported native project (previously a documented gap —
        # import left export-relative paths that pointed nowhere).
        self._restore_combined_audio(project, export_dir)
        self.save_project(project)
        logger.info("P3.7 import: imported project '%s' (id=%s, %d scenes, %d characters)",
                    project.name, project.id,
                    len(project.scenes), len(project.characters))
        return project
