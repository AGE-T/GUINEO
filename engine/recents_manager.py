"""
SpeechStudio Engine - Recents Manager
=====================================

P3.4: Context-aware MRU (Most Recently Used) lists for Projects,
Scenes, Characters, and History entries.

Stores only lightweight identifiers (IDs + display names), NOT full
entity copies. Persisted to settings.json under "application.recents".

Maximum 5 items per list (configurable).
"""

from __future__ import annotations
import os
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from engine.logger import get_logger

logger = get_logger("recents_manager")

MAX_RECENTS = 5


@dataclass
class RecentEntry:
    """A single recent item reference (lightweight, no full entity)."""
    entity_id: str = ""           # project_id, scene_id, character_id, or history_entry_id
    project_id: str = ""          # for scenes/characters: which project they belong to
    display_name: str = ""        # human-readable name for display
    subtitle: str = ""            # optional subtitle (e.g. voice name for characters)
    timestamp: str = ""           # when this was added to recents (for sorting)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "project_id": self.project_id,
            "display_name": self.display_name,
            "subtitle": self.subtitle,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecentEntry":
        return cls(
            entity_id=data.get("entity_id", ""),
            project_id=data.get("project_id", ""),
            display_name=data.get("display_name", ""),
            subtitle=data.get("subtitle", ""),
            timestamp=data.get("timestamp", ""),
        )


class RecentsManager:
    """Manages context-aware MRU lists.

    Four separate lists:
    - recent_projects: List[RecentEntry] (entity_id = project_id)
    - recent_scenes: List[RecentEntry] (entity_id = scene_id, project_id = project_id)
    - recent_characters: List[RecentEntry] (entity_id = character_id, project_id = project_id)
    - recent_history: List[RecentEntry] (entity_id = history_entry_id)

    All lists use MRU ordering (newest first) and are capped at MAX_RECENTS.
    """

    def __init__(self, settings_manager=None):
        """Initialize with optional SettingsManager for persistence.

        Args:
            settings_manager: SettingsManager instance. If None, recents
                are in-memory only (not persisted).
        """
        self._sm = settings_manager
        self._recent_projects: List[RecentEntry] = []
        self._recent_scenes: List[RecentEntry] = []
        self._recent_characters: List[RecentEntry] = []
        self._recent_history: List[RecentEntry] = []
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load(self) -> None:
        """Load recents from settings.json."""
        if self._sm is None:
            return
        try:
            data = self._sm.get("application", "recents", {})
            if data:
                self._recent_projects = [RecentEntry.from_dict(d) for d in data.get("projects", [])]
                self._recent_scenes = [RecentEntry.from_dict(d) for d in data.get("scenes", [])]
                self._recent_characters = [RecentEntry.from_dict(d) for d in data.get("characters", [])]
                self._recent_history = [RecentEntry.from_dict(d) for d in data.get("history", [])]
            logger.info("Recents loaded: %d projects, %d scenes, %d characters, %d history",
                        len(self._recent_projects), len(self._recent_scenes),
                        len(self._recent_characters), len(self._recent_history))
        except Exception as exc:
            logger.warning("Failed to load recents: %s", exc)

    def _save(self) -> None:
        """Save recents to settings.json."""
        if self._sm is None:
            return
        try:
            data = {
                "projects": [r.to_dict() for r in self._recent_projects],
                "scenes": [r.to_dict() for r in self._recent_scenes],
                "characters": [r.to_dict() for r in self._recent_characters],
                "history": [r.to_dict() for r in self._recent_history],
            }
            self._sm.set("application", "recents", data)
        except Exception as exc:
            logger.warning("Failed to save recents: %s", exc)

    # ------------------------------------------------------------------
    # MRU update helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _add_mru(entries: List[RecentEntry], entry: RecentEntry,
                 max_items: int = MAX_RECENTS) -> List[RecentEntry]:
        """Add entry to MRU list. Move to top if exists. Cap at max_items.

        Identity for deduplication:
        - Projects: entity_id (project_id)
        - Scenes: project_id + entity_id (scene_id)
        - Characters: project_id + entity_id (character_id)
        - History: entity_id (history_entry_id)
        """
        # Check for existing entry
        for i, existing in enumerate(entries):
            if entry.project_id:
                # Scene/Character: use project_id + entity_id as identity
                if existing.project_id == entry.project_id and existing.entity_id == entry.entity_id:
                    entries.pop(i)
                    break
            else:
                # Project/History: use entity_id only
                if existing.entity_id == entry.entity_id:
                    entries.pop(i)
                    break
        # Prepend (newest first)
        entries.insert(0, entry)
        # Cap at max
        if len(entries) > max_items:
            entries = entries[:max_items]
        return entries

    # ------------------------------------------------------------------
    # Public API — add recents
    # ------------------------------------------------------------------
    def add_recent_project(self, project_id: str, name: str) -> None:
        """Mark a Project as recently used."""
        import time
        entry = RecentEntry(
            entity_id=project_id,
            display_name=name,
            timestamp=time.strftime("%Y%m%d_%H%M%S"),
        )
        self._recent_projects = self._add_mru(self._recent_projects, entry)
        self._save()
        logger.info("Recent project added: %s (%s)", name, project_id)

    def add_recent_scene(self, project_id: str, scene_id: str, scene_name: str) -> None:
        """Mark a Scene as recently used."""
        import time
        entry = RecentEntry(
            entity_id=scene_id,
            project_id=project_id,
            display_name=scene_name,
            timestamp=time.strftime("%Y%m%d_%H%M%S"),
        )
        self._recent_scenes = self._add_mru(self._recent_scenes, entry)
        self._save()
        logger.info("Recent scene added: %s (%s/%s)", scene_name, project_id, scene_id)

    def add_recent_character(self, project_id: str, character_id: str,
                              character_name: str, subtitle: str = "") -> None:
        """Mark a Character as recently used."""
        import time
        entry = RecentEntry(
            entity_id=character_id,
            project_id=project_id,
            display_name=character_name,
            subtitle=subtitle,
            timestamp=time.strftime("%Y%m%d_%H%M%S"),
        )
        self._recent_characters = self._add_mru(self._recent_characters, entry)
        self._save()
        logger.info("Recent character added: %s (%s/%s)", character_name, project_id, character_id)

    def add_recent_history(self, entry_id: str, display_name: str,
                            subtitle: str = "") -> None:
        """Mark a History entry as recently used."""
        import time
        entry = RecentEntry(
            entity_id=entry_id,
            display_name=display_name,
            subtitle=subtitle,
            timestamp=time.strftime("%Y%m%d_%H%M%S"),
        )
        self._recent_history = self._add_mru(self._recent_history, entry)
        self._save()

    # ------------------------------------------------------------------
    # Public API — get recents
    # ------------------------------------------------------------------
    def get_recent_projects(self) -> List[RecentEntry]:
        return list(self._recent_projects)

    def get_recent_scenes(self) -> List[RecentEntry]:
        return list(self._recent_scenes)

    def get_recent_characters(self) -> List[RecentEntry]:
        return list(self._recent_characters)

    def get_recent_history(self) -> List[RecentEntry]:
        return list(self._recent_history)

    # ------------------------------------------------------------------
    # Public API — stale cleanup
    # ------------------------------------------------------------------
    def remove_stale_project(self, project_id: str) -> None:
        """Remove a project and all its scenes/characters from recents."""
        self._recent_projects = [r for r in self._recent_projects if r.entity_id != project_id]
        self._recent_scenes = [r for r in self._recent_scenes if r.project_id != project_id]
        self._recent_characters = [r for r in self._recent_characters if r.project_id != project_id]
        self._save()

    def remove_stale_scene(self, project_id: str, scene_id: str) -> None:
        """Remove a scene from recents."""
        self._recent_scenes = [
            r for r in self._recent_scenes
            if not (r.project_id == project_id and r.entity_id == scene_id)
        ]
        self._save()

    def remove_stale_character(self, project_id: str, character_id: str) -> None:
        """Remove a character from recents."""
        self._recent_characters = [
            r for r in self._recent_characters
            if not (r.project_id == project_id and r.entity_id == character_id)
        ]
        self._save()

    def remove_stale_history(self, entry_id: str) -> None:
        """Remove a history entry from recents."""
        self._recent_history = [r for r in self._recent_history if r.entity_id != entry_id]
        self._save()
