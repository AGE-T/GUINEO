"""
SpeechStudio Engine - Project Exporter
======================================

P3.7: Exports a complete Project into a portable, self-contained directory
structure that can be reopened through ProjectManager.

Directory structure produced:

    <ProjectName>/
    ├── project.json              (Project metadata, scenes index, characters)
    ├── scenes/
    │   ├── <SceneName>/
    │   │   ├── scene.json        (full Scene state incl. AudioAsset metadata)
    │   │   └── audio/
    │   │       ├── 001.wav       (copied if include_audio=True)
    │   │       └── 002.wav
    │   └── <SceneName>/
    │       ├── scene.json
    │       └── audio/
    ├── characters/
    │   └── <CharacterName>/
    │       └── character.json
    └── references/
        └── voices/
            └── <VoiceProfileId>/
                ├── voice.json    (VoiceProfile metadata — NOT the full object)
                ├── reference.wav (copied if include_voice_assets=True)
                └── transcript.txt

Design principles:
  - Export is a READ + COPY operation. The original Project is NEVER modified.
  - All paths inside the exported project.json / scene.json are RELATIVE
    to the export root (portability).
  - Missing audio files produce WARNINGS, not failures (unless the structure
    itself is invalid).
  - No second import architecture — ProjectManager.import_project_dir() reads
    the exported structure back into the authoritative model.
  - VoiceProfile objects are NOT duplicated into Character export. Only the
    voice_profile_id reference is preserved. Voice reference assets are copied
    to references/voices/ only when include_voice_assets=True AND the voice is
    actually referenced by a Character in the Project.
"""

from __future__ import annotations
import os
import json
import shutil
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Callable

from engine.models import Project, Scene, Character, VoiceProfile
from engine.logger import get_logger

logger = get_logger("project_exporter")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ExportResult:
    """The result of an export operation."""
    success: bool = False
    export_dir: str = ""
    scenes_exported: int = 0
    characters_exported: int = 0
    audio_files_copied: int = 0
    voice_assets_copied: int = 0
    warnings: List[str] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        """Human-readable summary for the export report dialog."""
        lines = [
            "Scenes exported: {0}".format(self.scenes_exported),
            "Characters exported: {0}".format(self.characters_exported),
            "Audio files copied: {0}".format(self.audio_files_copied),
            "Referenced voice assets copied: {0}".format(self.voice_assets_copied),
            "Warnings: {0}".format(len(self.warnings)),
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_dirname(name: str, fallback: str = "untitled") -> str:
    """Convert a name to a filesystem-safe directory name.

    Replaces any character that is not alphanumeric, dash, or underscore
    with an underscore. Preserves Unicode letters/digits.

    P3.25 (audit SS-M08): hostile names are contained — null bytes are
    stripped (they raise ValueError in the filesystem layer), length is
    capped at 80 characters (ENAMETOOLONG / ENOENT on long names), and
    Windows reserved device names (CON, NUL, AUX, COM1-9, LPT1-9) are
    rejected so exports also work on the Windows target.
    """
    if not name or not name.strip():
        return fallback
    # Strip null bytes (ValueError in os functions) and control chars.
    safe = name.replace("\x00", "")
    # Replace path separators and other unsafe chars
    safe = re.sub(r'[\\/:*?"<>|\s]+', '_', safe.strip())
    # Remove leading/trailing underscores and dots (hidden/relative dirs)
    safe = safe.strip('_.')
    # Cap the length (filesystem limits; Windows MAX_PATH with parents).
    if len(safe) > 80:
        safe = safe[:80].rstrip('_.')
    if not safe:
        return fallback
    # Windows reserved device names would fail dir creation on Windows.
    if safe.upper() in _WINDOWS_RESERVED:
        safe = "{0}_dir".format(safe)
    return safe


# Windows reserved device names (SS-M08).
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def _contained_source_path(app_root: str, rel_path: str) -> Optional[str]:
    """Resolve a MODEL-SUPPLIED relative path safely inside app_root.

    P3.25 (audit SS-H08): project.json contents are untrusted. A crafted
    ``output_path`` (``../secrets``, ``../../..``, or an absolute path)
    previously escaped the app root on BOTH the import path (kept
    verbatim) and the export-copy path (``os.path.join(app_root, abs)``
    discards app_root entirely) — re-exporting an imported "project"
    exfiltrated arbitrary files into the bundle.

    Returns the absolute contained path, or None when the path escapes
    app_root (absolute path, traversal, symlink escape). Callers must
    treat None as REJECT (skip + warning) — never follow the path.
    """
    if not rel_path or not isinstance(rel_path, str):
        return None
    if "\x00" in rel_path:
        return None
    # The model layer always stores FORWARD-SLASH relative paths (the
    # exporter normalises with os.sep → "/"). A backslash is therefore
    # always illegitimate — and on Windows it is a path separator, so a
    # "..\\evil" payload must be rejected platform-independently instead
    # of being treated as a weird-but-contained Linux filename.
    if "\\" in rel_path:
        return None
    # Absolute paths are rejected outright (os.path.join would discard
    # app_root). The model layer stores paths RELATIVE to app_root.
    if os.path.isabs(rel_path):
        return None
    app_abs = os.path.realpath(app_root)
    candidate = os.path.realpath(os.path.join(app_abs, rel_path))
    try:
        if os.path.commonpath([app_abs, candidate]) != app_abs:
            return None
    except ValueError:
        return None
    return candidate


def _unique_dir(parent: str, base: str) -> str:
    """Return a unique directory path under parent.

    If <parent>/<base> exists, appends _2, _3, ... until unique.
    """
    candidate = os.path.join(parent, base)
    if not os.path.exists(candidate):
        return candidate
    i = 2
    while True:
        candidate = os.path.join(parent, "{0}_{1}".format(base, i))
        if not os.path.exists(candidate):
            return candidate
        i += 1


# ---------------------------------------------------------------------------
# ProjectExporter
# ---------------------------------------------------------------------------

class ProjectExporter:
    """Exports a Project to a portable directory structure.

    Usage:
        exporter = ProjectExporter(app_root="/path/to/SpeechStudio")
        result = exporter.export(
            project=my_project,
            dest_parent="/path/to/Exports",
            include_audio=True,
            include_voice_assets=True,
            voice_lookup=my_engine.get_voice,  # callable(voice_id) -> VoiceProfile|None
        )
        if result.success:
            print("Exported to", result.export_dir)
    """

    EXPORT_FORMAT = "speechstudio-project-export-v1"
    # P3.28 (design record Rec 19): bumped to 2 — the export now carries
    # the Scene Audio Provenance fields (per-asset block/part/version/run
    # tags, expected_audio_slots, per-Scene combined_outputs, selections).
    # The reader is tolerant: v1 exports still import (they simply lack
    # provenance).
    EXPORT_VERSION = 2

    def __init__(self, app_root: str):
        """Initialize the exporter.

        Args:
            app_root: the SpeechStudio application root directory. Used to
                      resolve relative audio/voice paths from the model.
        """
        self._app_root = os.path.abspath(app_root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def export(self, project: Project, dest_parent: str,
               include_audio: bool = True,
               include_voice_assets: bool = True,
               voice_lookup: Optional[Callable[[str], Optional[VoiceProfile]]] = None,
               overwrite: bool = False) -> ExportResult:
        """Export a Project to a portable directory.

        Args:
            project: the authoritative Project to export (NOT modified).
            dest_parent: the parent directory where <ProjectName>/ will be
                         created.
            include_audio: if True, copy generated audio files into each
                           scene's audio/ subfolder.
            include_voice_assets: if True, copy referenced voice reference
                                  audio + transcripts into references/voices/.
            voice_lookup: callable(voice_id) -> VoiceProfile|None. Required
                          if include_voice_assets=True.
            overwrite: if True and the target dir exists, remove it first.
                       If False and it exists, a unique _2/_3 suffix is used.

        Returns:
            ExportResult with success status, counts, and warnings.
        """
        result = ExportResult()
        if project is None:
            result.error = "No project provided."
            return result

        # Determine the export directory name
        project_dir_name = _safe_dirname(project.name, fallback="project")
        if overwrite:
            export_dir = os.path.join(dest_parent, project_dir_name)
            if os.path.exists(export_dir):
                shutil.rmtree(export_dir)
        else:
            export_dir = _unique_dir(dest_parent, project_dir_name)

        try:
            os.makedirs(dest_parent, exist_ok=True)
            os.makedirs(export_dir, exist_ok=True)
        except OSError as exc:
            result.error = "Cannot create export directory: {0}".format(exc)
            logger.error("Export failed: %s", result.error)
            return result

        result.export_dir = export_dir
        logger.info("P3.7: Exporting project '%s' to %s", project.name, export_dir)

        # Snapshot the project so we NEVER modify the original. We work on
        # a deep-ish copy via to_dict/from_dict round-trip. This guarantees
        # the original Project object is untouched even if we rewrite
        # audio paths in the exported data.
        project_data = project.to_dict()

        # --- Export Characters ---
        chars_dir = os.path.join(export_dir, "characters")
        os.makedirs(chars_dir, exist_ok=True)
        # Build a set of voice_profile_ids referenced by characters
        referenced_voice_ids = set()
        for char_data in project_data.get("characters", []):
            char_name = _safe_dirname(char_data.get("name", "character"),
                                       fallback="character")
            # Ensure unique character dir name
            char_dir = _unique_dir(chars_dir, char_name)
            os.makedirs(char_dir, exist_ok=True)
            char_path = os.path.join(char_dir, "character.json")
            try:
                with open(char_path, "w", encoding="utf-8") as f:
                    json.dump(char_data, f, indent=2, ensure_ascii=False)
                result.characters_exported += 1
            except OSError as exc:
                result.warnings.append(
                    "Failed to export character '{0}': {1}".format(
                        char_data.get("name", "?"), exc))
            # Track voice references
            vpid = char_data.get("voice_profile_id")
            if vpid:
                referenced_voice_ids.add(vpid)

        # --- Export Scenes ---
        scenes_dir = os.path.join(export_dir, "scenes")
        os.makedirs(scenes_dir, exist_ok=True)
        # P3.28 (Rec 19): export the Scene-level combined outputs (WAV
        # files, copy-only) into combined_outputs/ with a scene-position
        # prefix; per-slot lineage + versioning flow through Scene.to_dict
        # into scene.json automatically (no structural format change —
        # purely additive fields).
        scene_combined_dir = os.path.join(export_dir, "combined_outputs")
        for scene_pos, scene_data in enumerate(project_data.get("scenes", [])):
            scene_combined = [
                e for e in (scene_data.get("combined_outputs") or [])
                if isinstance(e, dict)]
            if include_audio and scene_combined:
                os.makedirs(scene_combined_dir, exist_ok=True)
                exported_scene_combined = []
                for idx, entry in enumerate(scene_combined):
                    rel = entry.get("output_path", "")
                    new_entry = dict(entry)
                    if rel:
                        src_abs = _contained_source_path(self._app_root, rel)
                        if src_abs is None:
                            result.warnings.append(
                                "Scene combined output path escapes the "
                                "application root ('{0}') — skipped for "
                                "security.".format(rel))
                        elif not os.path.isfile(src_abs):
                            result.warnings.append(
                                "Scene combined WAV not found: {0} — "
                                "skipped.".format(rel))
                        else:
                            safe_name = "{0:03d}_{1:02d}_{2}".format(
                                scene_pos + 1, idx + 1,
                                os.path.basename(rel))
                            dest_wav = os.path.join(scene_combined_dir,
                                                    safe_name)
                            try:
                                shutil.copy2(src_abs, dest_wav)
                                result.audio_files_copied += 1
                                new_entry["output_path"] = os.path.relpath(
                                    dest_wav, export_dir).replace(os.sep, "/")
                            except OSError as exc:
                                result.warnings.append(
                                    "Failed to copy scene combined WAV "
                                    "'{0}': {1}".format(rel, exc))
                    exported_scene_combined.append(new_entry)
                scene_data["combined_outputs"] = exported_scene_combined
        # We rebuild the scenes list in project_data with updated audio paths
        exported_scenes_data = []
        for scene_data in project_data.get("scenes", []):
            scene_name = _safe_dirname(scene_data.get("name", "scene"),
                                        fallback="scene")
            scene_dir = _unique_dir(scenes_dir, scene_name)
            os.makedirs(scene_dir, exist_ok=True)
            # Export audio assets if requested
            audio_dir = os.path.join(scene_dir, "audio")
            exported_assets = []
            if include_audio and scene_data.get("audio_assets"):
                os.makedirs(audio_dir, exist_ok=True)
                for idx, asset in enumerate(scene_data["audio_assets"]):
                    src_rel = asset.get("output_path", "")
                    if not src_rel:
                        result.warnings.append(
                            "AudioAsset {0} in scene '{1}' has no output_path — skipped.".format(
                                asset.get("id", "?"), scene_data.get("name", "?")))
                        exported_assets.append(asset)
                        continue
                    # P3.25 (audit SS-H08): model-supplied paths are
                    # UNTRUSTED. Resolve with containment — an escaping
                    # path (traversal / absolute) is REJECTED, never
                    # copied (prevents import → re-export exfiltration).
                    src_abs = _contained_source_path(self._app_root, src_rel)
                    if src_abs is None:
                        result.warnings.append(
                            "AudioAsset {0} in scene '{1}' has an unsafe "
                            "output_path ('{2}') — skipped for security.".format(
                                asset.get("id", "?"),
                                scene_data.get("name", "?"), src_rel))
                        exported_assets.append(asset)
                        continue
                    if not os.path.isfile(src_abs):
                        result.warnings.append(
                            "Missing audio file for scene '{0}': {1}".format(
                                scene_data.get("name", "?"), src_rel))
                        exported_assets.append(asset)
                        continue
                    # Build a safe filename: 001_<original_name>.wav
                    original_name = os.path.basename(src_rel)
                    safe_name = "{0:03d}_{1}".format(idx + 1, original_name)
                    dest_audio = os.path.join(audio_dir, safe_name)
                    try:
                        shutil.copy2(src_abs, dest_audio)
                        result.audio_files_copied += 1
                    except OSError as exc:
                        result.warnings.append(
                            "Failed to copy audio '{0}': {1}".format(src_rel, exc))
                        exported_assets.append(asset)
                        continue
                    # Update the asset's output_path to a RELATIVE path
                    # within the export structure.
                    new_rel = os.path.relpath(dest_audio, export_dir)
                    new_asset = dict(asset)
                    new_asset["output_path"] = new_rel.replace(os.sep, "/")
                    exported_assets.append(new_asset)
            else:
                # Not including audio — keep metadata, but rewrite paths to
                # be relative to the export root so they remain meaningful
                # (they'll point to non-existent files, but the metadata is
                # preserved as required by the spec).
                exported_assets = list(scene_data.get("audio_assets", []))
            # Write scene.json with updated audio_assets
            scene_export_data = dict(scene_data)
            scene_export_data["audio_assets"] = exported_assets
            scene_path = os.path.join(scene_dir, "scene.json")
            try:
                with open(scene_path, "w", encoding="utf-8") as f:
                    json.dump(scene_export_data, f, indent=2, ensure_ascii=False)
                result.scenes_exported += 1
            except OSError as exc:
                result.warnings.append(
                    "Failed to export scene '{0}': {1}".format(
                        scene_data.get("name", "?"), exc))
            exported_scenes_data.append(scene_export_data)

        # --- Export Voice References (optional) ---
        if include_voice_assets and referenced_voice_ids and voice_lookup is not None:
            refs_dir = os.path.join(export_dir, "references", "voices")
            os.makedirs(refs_dir, exist_ok=True)
            for vpid in referenced_voice_ids:
                vp = voice_lookup(vpid)
                if vp is None:
                    result.warnings.append(
                        "Referenced voice profile '{0}' not found — skipped.".format(vpid))
                    continue
                vp_dir = _unique_dir(refs_dir, _safe_dirname(vpid, fallback="voice"))
                os.makedirs(vp_dir, exist_ok=True)
                # Write voice metadata (NOT the full VoiceProfile — just a
                # reference snapshot so the export is self-documenting).
                vp_meta = {
                    "id": vp.id,
                    "name": vp.name,
                    "reference_audio_path": vp.reference_audio_path,
                    "reference_transcript": vp.reference_transcript,
                    "sample_rate": vp.sample_rate,
                    "duration": vp.duration,
                    "description": vp.description,
                    "tags": list(vp.tags),
                }
                vp_meta_path = os.path.join(vp_dir, "voice.json")
                try:
                    with open(vp_meta_path, "w", encoding="utf-8") as f:
                        json.dump(vp_meta, f, indent=2, ensure_ascii=False)
                except OSError as exc:
                    result.warnings.append(
                        "Failed to write voice metadata for '{0}': {1}".format(vpid, exc))
                    continue
                # Copy reference audio if it exists
                if vp.reference_audio_path:
                    # P3.25 (SS-H08): containment-checked resolution.
                    ref_src = _contained_source_path(
                        self._app_root, vp.reference_audio_path)
                    if ref_src is None:
                        result.warnings.append(
                            "Voice reference audio path for '{0}' escapes "
                            "the application root — skipped for security.".format(vpid))
                    elif os.path.isfile(ref_src):
                        ref_dest = os.path.join(vp_dir, "reference.wav")
                        try:
                            shutil.copy2(ref_src, ref_dest)
                            result.voice_assets_copied += 1
                        except OSError as exc:
                            result.warnings.append(
                                "Failed to copy voice reference audio '{0}': {1}".format(
                                    vpid, exc))
                    else:
                        result.warnings.append(
                            "Voice reference audio not found for '{0}': {1}".format(
                                vpid, vp.reference_audio_path))
                # Copy transcript if it exists
                if vp.reference_transcript:
                    try:
                        transcript_path = os.path.join(vp_dir, "transcript.txt")
                        with open(transcript_path, "w", encoding="utf-8") as f:
                            f.write(vp.reference_transcript)
                    except OSError as exc:
                        result.warnings.append(
                            "Failed to write transcript for voice '{0}': {1}".format(
                                vpid, exc))
        elif include_voice_assets and referenced_voice_ids and voice_lookup is None:
            result.warnings.append(
                "Voice assets requested but no voice_lookup provided — skipped.")

        # --- P3.22: Export Combined Outputs (Scene Chain) ---
        # Copy combined audio files (WAV + MP3) into the export's
        # combined_outputs/ subfolder. Update the paths in project_data
        # to be relative to the export root.
        combined_data = project_data.get("combined_outputs", [])
        if combined_data and include_audio:
            combined_dir = os.path.join(export_dir, "combined_outputs")
            os.makedirs(combined_dir, exist_ok=True)
            exported_combined = []
            for idx, entry in enumerate(combined_data):
                wav_rel = entry.get("output_path", "")
                if not wav_rel:
                    exported_combined.append(entry)
                    continue
                # P3.25 (SS-H08): containment-checked resolution — the
                # old code explicitly FOLLOWED absolute paths.
                wav_abs = _contained_source_path(self._app_root, wav_rel)
                if wav_abs is None:
                    result.warnings.append(
                        "Combined output path escapes the application root "
                        "('{0}') — skipped for security.".format(wav_rel))
                    exported_combined.append(entry)
                    continue
                if not os.path.isfile(wav_abs):
                    result.warnings.append(
                        "Combined output WAV not found: {0} — skipped.".format(wav_rel))
                    exported_combined.append(entry)
                    continue
                # Copy WAV
                wav_name = os.path.basename(wav_rel)
                safe_name = "{0:03d}_{1}".format(idx + 1, wav_name)
                dest_wav = os.path.join(combined_dir, safe_name)
                try:
                    shutil.copy2(wav_abs, dest_wav)
                    result.audio_files_copied += 1
                except OSError as exc:
                    result.warnings.append(
                        "Failed to copy combined WAV '{0}': {1}".format(wav_rel, exc))
                    exported_combined.append(entry)
                    continue
                new_entry = dict(entry)
                new_entry["output_path"] = os.path.relpath(dest_wav, export_dir).replace(os.sep, "/")
                # Copy MP3 if it exists
                mp3_rel = entry.get("mp3_path", "")
                if mp3_rel:
                    # P3.25 (SS-H08): containment-checked resolution.
                    mp3_abs = _contained_source_path(self._app_root, mp3_rel)
                    if mp3_abs is not None and os.path.isfile(mp3_abs):
                        mp3_name = os.path.splitext(safe_name)[0] + ".mp3"
                        dest_mp3 = os.path.join(combined_dir, mp3_name)
                        try:
                            shutil.copy2(mp3_abs, dest_mp3)
                            new_entry["mp3_path"] = os.path.relpath(dest_mp3, export_dir).replace(os.sep, "/")
                        except OSError as exc:
                            result.warnings.append(
                                "Failed to copy combined MP3 '{0}': {1}".format(mp3_rel, exc))
                            new_entry["mp3_path"] = ""
                    else:
                        new_entry["mp3_path"] = ""
                exported_combined.append(new_entry)
            project_data["combined_outputs"] = exported_combined
        elif combined_data and not include_audio:
            # Keep metadata but don't copy files (paths point to originals)
            pass

        # --- Write project.json ---
        # Update the scenes list in project_data with the exported (path-rewritten) scenes.
        project_data["scenes"] = exported_scenes_data
        # Add export format markers
        project_data["export_format"] = self.EXPORT_FORMAT
        project_data["export_version"] = self.EXPORT_VERSION
        project_data["exported_at"] = self._now_iso()
        project_path = os.path.join(export_dir, "project.json")
        try:
            with open(project_path, "w", encoding="utf-8") as f:
                json.dump(project_data, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            result.error = "Failed to write project.json: {0}".format(exc)
            result.warnings.append(result.error)
            logger.error("Export failed: %s", result.error)
            # Don't mark as success
            result.success = False
            return result

        result.success = True
        logger.info("P3.7: Export complete — %d scenes, %d characters, %d audio, %d voice assets, %d warnings",
                    result.scenes_exported, result.characters_exported,
                    result.audio_files_copied, result.voice_assets_copied,
                    len(result.warnings))
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _now_iso() -> str:
        import time
        return time.strftime("%Y-%m-%dT%H:%M:%S")
