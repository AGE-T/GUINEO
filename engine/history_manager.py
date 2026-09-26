"""
SpeechStudio Engine - History Manager
======================================

Engine Specification, section 11:
    "HistoryManager stores: Generated prompt, Generation parameters,
     Voice profile, Output file, Timestamp, Generation duration,
     Realtime factor.
     History entries must be reproducible."

Generation Engine Specification, section 14:
    "After successful generation: Save audio, Save metadata, Register
     history entry, Return playback handle."

Prompt System, section 21:
    "Every generated prompt must be stored together with its settings."

System Specification, section 12:
    "Generated files are written to outputs/.
     Each generation stores: Generated audio, Metadata, Generation parameters,
     Timestamp, Prompt information."

Single responsibility: append, list, load, delete, and export history
entries. Each entry is a JSON file that can fully reproduce a generation.
"""

from __future__ import annotations
import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from engine.logger import get_logger
from engine.models import GenerationResult, GenerationParameters, VoiceProfile

logger = get_logger("history_manager")


class HistoryEntry:
    """A single reproducible history entry.

    Stored as a JSON file in settings/history/. Each file is
    self-contained: it contains the prompt, parameters, voice profile, and
    output path needed to reproduce the generation exactly.

    P3.25 (audit SS-H10): a history file is UNTRUSTED input. The entry is
    validated defensively — a malformed entry (missing id, wrong types,
    corrupt nested voice_profile / duration) degrades gracefully instead
    of crashing the History view dialog.
    """

    def __init__(self, data):
        # P3.25: tolerate non-dict payloads without crashing.
        self._data = data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def id(self) -> str:
        value = self._data.get("id", "")
        return value if isinstance(value, str) else ""

    @property
    def timestamp(self) -> str:
        value = self._data.get("timestamp", "")
        return value if isinstance(value, str) else ""

    @property
    def output_path(self) -> str:
        value = self._data.get("output_path", "")
        return value if isinstance(value, str) else ""

    @property
    def prompt(self) -> str:
        value = self._data.get("prompt", "")
        return value if isinstance(value, str) else ""

    @property
    def generation_time(self) -> float:
        return _as_float(self._data.get("generation_time", 0.0))

    @property
    def output_duration(self) -> float:
        return _as_float(self._data.get("output_duration", 0.0))

    @property
    def output_sample_rate(self) -> int:
        return int(_as_float(self._data.get("output_sample_rate", 24000)))

    @property
    def realtime_factor(self) -> float:
        return _as_float(self._data.get("realtime_factor", 0.0))

    @property
    def parameters(self) -> GenerationParameters:
        data = self._data.get("parameters", {})
        if not isinstance(data, dict):
            data = {}
        return GenerationParameters.from_dict(data)

    @property
    def voice_profile(self) -> Optional[VoiceProfile]:
        vp = self._data.get("voice_profile")
        if not isinstance(vp, dict):
            return None
        # P3.25: a voice_profile missing id/name (or with wrong types)
        # previously raised KeyError/TypeError straight into the History
        # dialog — degrade to "no voice" instead.
        if not isinstance(vp.get("id"), str) or \
                not isinstance(vp.get("name"), str):
            return None
        try:
            return VoiceProfile.from_dict(vp)
        except Exception:
            return None

    @property
    def voice_name(self) -> str:
        vp = self.voice_profile
        return vp.name if vp else "No voice"

    @property
    def project(self) -> str:
        value = self._data.get("project", "Default")
        return value if isinstance(value, str) else "Default"

    # P1.5: Scene and Character linkage (nullable for backward compat)
    @property
    def scene_id(self) -> Optional[str]:
        value = self._data.get("scene_id")
        return value if isinstance(value, str) else None

    @property
    def scene_name(self) -> Optional[str]:
        value = self._data.get("scene_name")
        return value if isinstance(value, str) else None

    @property
    def character_id(self) -> Optional[str]:
        value = self._data.get("character_id")
        return value if isinstance(value, str) else None

    @property
    def speaker(self) -> Optional[str]:
        value = self._data.get("speaker")
        return value if isinstance(value, str) else None

    # P3.27B: Long Generation part provenance (nullable for backward
    # compatibility — entries written before P3.27B have no part info).
    @property
    def part_index(self) -> Optional[int]:
        value = self._data.get("part_index")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def part_version(self) -> Optional[int]:
        value = self._data.get("part_version")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def output_anomaly(self) -> Optional[Dict[str, Any]]:
        value = self._data.get("output_anomaly")
        return value if isinstance(value, dict) else None

    # P3.28 (§20 / Rec 18): entry type + provenance. The type field is
    # additive; legacy entries (written before P3.28) have no stored type
    # and are backfilled on read by payload-shape heuristics — never
    # rewritten on disk.
    @property
    def type(self) -> Optional[str]:
        value = self._data.get("type")
        if isinstance(value, str) and value in (
                "part", "scene_combined", "project_combined", "concat",
                "generation"):
            return value
        # Legacy backfill (read-only heuristic; design record Rec 18).
        prompt = self.prompt or ""
        if prompt.startswith("[Combined Audio"):
            return "project_combined"
        if prompt.startswith("[Concatenated"):
            return "concat"
        if prompt.startswith("[Scene Combined"):
            return "scene_combined"
        if self.part_index is not None:
            return "part"
        return None  # legacy single generation (pre-P3.28)

    @property
    def block_id(self) -> Optional[str]:
        value = self._data.get("block_id")
        return value if isinstance(value, str) else None

    @property
    def generation_run(self) -> Optional[str]:
        value = self._data.get("generation_run")
        return value if isinstance(value, str) else None

    # P3.28: combined-output lineage (scene_combined / project_combined /
    # concat entries carry a summary of their sources; tolerant reader).
    @property
    def source_ids(self) -> List[str]:
        value = self._data.get("source_ids")
        if not isinstance(value, list):
            return []
        return [str(v) for v in value if isinstance(v, (str, int))]

    @property
    def data(self) -> Dict[str, Any]:
        return dict(self._data)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)


def _as_float(value, fallback: float = 0.0) -> float:
    """Coerce a JSON value to float with a safe fallback (SS-H10)."""
    if isinstance(value, bool):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _is_within(base: str, target: str) -> bool:
    """Containment check: True if `target` resolves inside `base`.

    P3.23 (design record §85: all delete paths require containment
    checks). Both paths are made absolute + normalized (resolving "..")
    before comparison, so traversal sequences cannot escape the base.
    """
    base_abs = os.path.abspath(base)
    target_abs = os.path.abspath(target)
    return os.path.commonpath([base_abs, target_abs]) == base_abs


def _safe_component(value: str) -> str:
    """Validate that `value` is a single safe path component.

    Rejects empty values, separators, absolute paths, ".."/"." traversal
    and null bytes. Returns the value if safe, else "".
    """
    if not value or not isinstance(value, str):
        return ""
    if value in (".", ".."):
        return ""
    if "/" in value or "\\" in value or "\x00" in value:
        return ""
    if os.path.isabs(value):
        return ""
    return value


class HistoryManager:
    """Stores and retrieves reproducible generation history.

    Each generation produces one JSON file in the history directory. The
    file is named by timestamp so entries sort chronologically.
    """

    def __init__(self, history_dir: str):
        self._history_dir = history_dir
        os.makedirs(history_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def add(self, result: GenerationResult,
            entry_type: Optional[str] = None,
            extra: Optional[Dict[str, Any]] = None) -> str:
        """Register a generation result and return the entry id.

        Args:
            result: the GenerationResult returned by the Engine.
            entry_type: P3.28 entry type ("part" | "scene_combined" |
                "project_combined" | "concat"); None infers "part" for
                results carrying part provenance (legacy entries stay
                untyped and are backfilled on read).
            extra: P3.28 optional lineage fields merged into the entry
                (e.g. source asset ids, scene ids, format) — used by the
                Scene Combine / Project Assembly paths.

        Returns:
            The unique id of the new history entry (timestamp-based).

        P3.25 (audit SS-M05): the entry id previously used second-
        resolution timestamps as BOTH the filename and the id — two
        generations completing within the same second silently
        overwrote each other's entry (and, via identical output names,
        their audio). The id now carries a millisecond suffix and is
        de-duplicated against existing files.
        """
        timestamp = result.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        entry_id = timestamp
        # P3.25 (SS-M05): guarantee uniqueness — append milliseconds (and,
        # if a collision STILL exists, a counter) so a same-second entry
        # never overwrites an existing one.
        if os.path.exists(os.path.join(self._history_dir, entry_id + ".json")):
            base = datetime.now()
            entry_id = "{0}_{1}".format(
                timestamp, base.strftime("%f")[:3])
            counter = 1
            while os.path.exists(
                    os.path.join(self._history_dir, entry_id + ".json")):
                entry_id = "{0}_{1}_{2}".format(
                    timestamp, base.strftime("%f")[:3], counter)
                counter += 1
        entry_data: Dict[str, Any] = {
            "id": entry_id,
            "timestamp": timestamp,
            "output_path": result.output_path,
            "prompt": result.prompt,
            "generation_time": result.generation_time,
            "output_duration": result.output_duration,
            "output_sample_rate": result.output_sample_rate,
            "realtime_factor": result.realtime_factor,
            "parameters": result.parameters.to_dict() if result.parameters else {},
            "voice_profile": result.voice_profile.to_dict() if result.voice_profile else None,
            "gpu_name": result.gpu_name,
            "vram_usage_mb": result.vram_usage_mb,
            "warnings": list(result.warnings),
            "project": result.project if hasattr(result, 'project') else "Default",
            # P1.5: scene_id and character_id for History → Scene/Character
            # linkage. These are nullable for backward compatibility — old
            # history entries won't have them, and single-prompt generations
            # may not have a scene context.
            "scene_id": getattr(result, 'scene_id', None),
            "scene_name": getattr(result, 'scene_name', None),
            "character_id": getattr(result, 'character_id', None),
            "speaker": getattr(result, 'speaker', None),
            # P3.27B: part provenance + output-guard verdict. A
            # pathological generation stays fully traceable (task §19):
            # part index, generation version, anomaly reasons and the
            # measured speech/silence breakdown are all recorded.
            "part_index": getattr(result, 'part_index', None),
            "part_version": getattr(result, 'part_version', None),
            "output_anomaly": getattr(result, 'output_anomaly', None),
            # P3.28 (§20 / Rec 18): entry type + block/run provenance.
            "type": (entry_type
                      or ("part" if getattr(result, 'part_index', None)
                          is not None else None)),
            "block_id": getattr(result, 'block_id', None),
            "generation_run": getattr(result, 'generation_run', None),
        }
        # P3.28: merge caller-supplied lineage fields (never overwrite
        # the core fields above).
        if isinstance(extra, dict):
            for key, value in extra.items():
                if key not in entry_data:
                    entry_data[key] = value
        file_path = os.path.join(self._history_dir, entry_id + ".json")
        try:
            with open(file_path, "w", encoding="utf-8") as handle:
                json.dump(entry_data, handle, indent=2, ensure_ascii=False)
            logger.info("History entry saved: %s", file_path)
        except OSError as exc:
            logger.error("Failed to save history entry: %s", exc)
            raise
        return entry_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def list_entries(self) -> List[HistoryEntry]:
        """Return all history entries sorted newest-first.

        P3.25 (audit SS-H10): per-entry tolerance — a corrupt file
        (invalid JSON, wrong encoding, wrong root type) is skipped with a
        warning while every OTHER entry stays readable. Previously ONE
        unreadable file raised out of list_entries() and poisoned the
        entire history.
        """
        entries: List[HistoryEntry] = []
        if not os.path.isdir(self._history_dir):
            return entries
        for fname in sorted(os.listdir(self._history_dir), reverse=True):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._history_dir, fname)
            try:
                with open(path, "r", encoding="utf-8",
                          errors="replace") as handle:
                    data = json.load(handle)
                if not isinstance(data, dict):
                    raise ValueError("entry root is not a JSON object")
                entries.append(HistoryEntry(data))
            except Exception as exc:  # SS-H10: broad per-entry catch
                logger.warning("history_manager: skipping corrupt entry "
                               "%s: %s (%s)", fname, exc,
                               type(exc).__name__)
        return entries

    def get_entry(self, entry_id: str) -> Optional[HistoryEntry]:
        """Load a specific entry by id, or None if not found/corrupt."""
        path = os.path.join(self._history_dir, entry_id + ".json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8",
                      errors="replace") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return None
            return HistoryEntry(data)
        except Exception as exc:  # SS-H10: broad — never propagate
            logger.error("Failed to load entry %s: %s (%s)", entry_id, exc,
                         type(exc).__name__)
            return None

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    def delete(self, entry_id: str, delete_audio: bool = False) -> bool:
        """Delete a history entry. Optionally also delete the audio file.

        P3.23 (design record §85 — containment checks): entry_id is
        untrusted input. A crafted ID containing "../" (or an absolute
        path) previously allowed deleting an arbitrary .json file anywhere
        on disk, and its recorded output_path allowed deleting an
        arbitrary audio file. Both paths are now containment-checked:
        the entry JSON must resolve inside the history directory, and the
        audio file must resolve inside the app root.

        Returns True if the entry file was deleted.
        """
        # P3.23: reject traversal / absolute / malformed entry IDs.
        if not _safe_component(entry_id):
            logger.warning("Rejected unsafe history entry id: %r", entry_id)
            return False
        path = os.path.join(self._history_dir, entry_id + ".json")
        # Defence in depth: the resolved path must stay inside the
        # history directory.
        if not _is_within(self._history_dir, path):
            logger.warning("Rejected history delete outside history dir: %r",
                           entry_id)
            return False
        # M1 FIX: Read the entry BEFORE deleting the JSON file, so we can
        # use its output_path to delete the audio file afterwards.
        # Previously, get_entry was called AFTER the JSON was deleted,
        # so it always returned None and the audio was never deleted.
        entry = self.get_entry(entry_id) if delete_audio else None
        deleted = False
        if os.path.isfile(path):
            try:
                os.remove(path)
                deleted = True
                logger.info("History entry deleted: %s", entry_id)
            except OSError as exc:
                logger.error("Failed to delete entry %s: %s",
                             entry_id, exc)

        if delete_audio:
            if entry and entry.output_path:
                audio_path = entry.output_path
                from pathlib import Path
                # M1 FIX: history_dir is <app_root>/settings/history/,
                # so the app root is TWO levels up (history/ → settings/
                # → <app_root>/).
                app_root = Path(self._history_dir).parent.parent
                # P3.23: only delete audio INSIDE the app root. Absolute
                # paths and traversal sequences are rejected outright
                # (previously an absolute output_path was followed as-is).
                candidate = Path(audio_path)
                if candidate.is_absolute():
                    abs_audio = candidate.resolve()
                else:
                    abs_audio = (app_root / audio_path).resolve()
                try:
                    abs_audio.relative_to(app_root.resolve())
                except ValueError:
                    logger.warning(
                        "Refused to delete audio outside app root: %s",
                        audio_path)
                    return deleted
                if abs_audio.is_file():
                    try:
                        abs_audio.unlink()
                        logger.info("Audio file deleted: %s", audio_path)
                    except OSError as exc:
                        logger.error(
                                     "Failed to delete audio %s: %s",
                                     audio_path, exc)
        return deleted

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_entry(self, entry_id: str, export_path: str) -> bool:
        """Copy a history entry JSON to an external path."""
        entry = self.get_entry(entry_id)
        if entry is None:
            return False
        try:
            with open(export_path, "w", encoding="utf-8") as handle:
                json.dump(entry.to_dict(), handle, indent=2, ensure_ascii=False)
            logger.info("History entry exported: %s -> %s", entry_id, export_path)
            return True
        except OSError as exc:
            logger.error( "Failed to export entry %s: %s",
                         entry_id, exc)
            return False
