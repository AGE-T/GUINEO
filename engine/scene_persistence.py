"""
SpeechStudio Engine - Scene Persistence
=======================================

Saves and loads multi-speaker narration scenes as .scene.json files.

A scene captures the full state of a multi-speaker narration:
- scene name
- speaker → voice mapping
- dialogue lines (speaker, text, parameters, status, output path)

This allows the user to save a dialogue project, close the app, and
resume later — the batch jobs, their status, and generated audio paths
are all preserved.

Scene files are JSON, versioned, and human-readable.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from engine.logger import get_logger
from engine.models import GenerationParameters

logger = get_logger("scene_persistence")


SCENE_FORMAT_VERSION = 1


@dataclass
class DialogueLine:
    """One line in a multi-speaker scene."""
    speaker: str = ""
    text: str = ""
    voice_id: Optional[str] = None
    emotion: Optional[str] = None
    style: Optional[str] = None
    speed: str = "Normal"
    pitch: str = "Normal"
    delivery: str = "Normal"
    # Generation state:
    output_path: Optional[str] = None
    output_duration: float = 0.0
    generation_status: str = "pending"  # pending/completed/failed
    output_filename: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DialogueLine":
        return cls(
            speaker=str(data.get("speaker", "")),
            text=str(data.get("text", "")),
            voice_id=data.get("voice_id"),
            emotion=data.get("emotion"),
            style=data.get("style"),
            speed=str(data.get("speed", "Normal")),
            pitch=str(data.get("pitch", "Normal")),
            delivery=str(data.get("delivery", "Normal")),
            output_path=data.get("output_path"),
            output_duration=float(data.get("output_duration", 0.0) or 0.0),
            generation_status=str(data.get("generation_status", "pending")),
            output_filename=data.get("output_filename"),
        )


@dataclass
class DialogueScene:
    """A complete multi-speaker narration scene."""
    name: str = "Untitled"
    project: str = "Default"
    lines: List[DialogueLine] = field(default_factory=list)
    default_pause_ms: int = 500
    created_at: str = ""
    modified_at: str = ""
    combined_audio_path: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": "speechstudio-scene",
            "version": SCENE_FORMAT_VERSION,
            "name": self.name,
            "project": self.project,
            "lines": [line.to_dict() for line in self.lines],
            "default_pause_ms": self.default_pause_ms,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "combined_audio_path": self.combined_audio_path,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DialogueScene":
        lines_data = data.get("lines", [])
        lines = [DialogueLine.from_dict(ld) for ld in lines_data]
        return cls(
            name=str(data.get("name", "Untitled")),
            project=str(data.get("project", "Default")),
            lines=lines,
            default_pause_ms=int(data.get("default_pause_ms", 500)),
            created_at=str(data.get("created_at", "")),
            modified_at=str(data.get("modified_at", "")),
            combined_audio_path=data.get("combined_audio_path"),
            notes=str(data.get("notes", "")),
        )

    def save(self, path: str) -> str:
        """Save the scene to a .scene.json file. Returns the path."""
        self.modified_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.created_at:
            self.created_at = self.modified_at
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
        logger.info("Scene saved: %s (%d lines)", path, len(self.lines))
        return path

    @classmethod
    def load(cls, path: str) -> "DialogueScene":
        """Load a scene from a .scene.json file."""
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        scene = cls.from_dict(data)
        logger.info("Scene loaded: %s (%d lines)", path, len(scene.lines))
        return scene


def scene_from_batch_jobs(jobs, scene_name: str = "",
                          project: str = "Default") -> DialogueScene:
    """Build a DialogueScene from a list of BatchJob objects.

    Used when the user wants to save the current batch as a scene.
    """
    lines = []
    for job in jobs:
        line = DialogueLine(
            speaker=job.speaker or "",
            text=job.prompt,
            voice_id=job.voice_id,
            output_path=job.output_path,
            output_duration=job.output_duration,
            generation_status=job.status.value if hasattr(job.status, 'value') else str(job.status),
            output_filename=job.output_filename,
        )
        lines.append(line)
    return DialogueScene(
        name=scene_name or "Untitled",
        project=project,
        lines=lines,
    )
