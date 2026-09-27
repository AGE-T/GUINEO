"""
SpeechStudio Engine - Narration Block Data Model.

Defines the core data structures for the Narration Block system:
- PromptBlock: offset-based reference into the editor text + override settings
- EffectiveBlockState: resolved values (override or global default)
- TokenEmission: a planned token insertion (output of the PromptOptimizer)
- NarrationTemplate: reusable block settings without text

Design principles:
- The editor is the single source of truth for text. Blocks store offsets,
  not duplicated text.
- Override properties use None to mean "inherit the global default".
- The data model is engine-independent (no Higgs-specific syntax here).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import uuid


@dataclass
class SfxInsertion:
    """An inline SFX insertion within a block, stored by offset."""
    sfx_name: str
    onomatopoeia: str
    offset: int  # character offset within the block's text span


@dataclass
class PauseInsertion:
    """An inline pause insertion within a block, stored by offset."""
    pause_type: str  # "pause" or "long_pause"
    offset: int      # character offset within the block's text span


@dataclass
class PromptBlock:
    """A Narration Block.

    References text by character offsets into the editor document.
    The editor is the single source of truth — blocks never store text.

    Attributes:
        id: unique identifier.
        start_offset: character offset where the block starts (inclusive).
        end_offset: character offset where the block ends (exclusive).
        emotion/style/speed/pitch/delivery: override values.
            None means "inherit the global default".
        sfx_insertions: inline SFX markers within this block.
        pause_insertions: inline pause markers within this block.
        label: optional user-visible label.
        locked: if True, Analyze Again preserves this block.
        manually_edited: True if the user split/merged/created this block.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    start_offset: int = 0
    end_offset: int = 0

    emotion: Optional[str] = None
    style: Optional[str] = None
    speed: Optional[str] = None
    pitch: Optional[str] = None
    delivery: Optional[str] = None

    sfx_insertions: List[SfxInsertion] = field(default_factory=list)
    pause_insertions: List[PauseInsertion] = field(default_factory=list)

    label: Optional[str] = None
    locked: bool = False
    manually_edited: bool = False

    # P3.22 Character System: the Character this block is spoken by.
    # None = inherit the scene/control-panel voice (the default).
    # When set, block.character_id → character.voice_profile_id is the
    # authoritative voice for this block (Block Character > Scene > Dropdown).
    # Preserved across Analyze Again if the block is locked or manually_edited.
    character_id: Optional[str] = None

    # P3.23 (design record §11/§25): temporary recovery reference used when
    # Re-detect could not reliably match this block to its previous identity
    # and the previous block HAD a Character assigned. The Character
    # assignment must NOT silently disappear: character_id is cleared (so no
    # unverified voice is used) and lost_character_id retains the previous
    # Character so the UI can show a warning ("B1 ⚠") and offer re-assignment
    # in the properties panel. Set to None when the Character is re-assigned
    # or the warning is explicitly dismissed.
    lost_character_id: Optional[str] = None

    # Generation status tracking (HTML reference: Done/Generating/Pending/Error)
    # These are set by MainWindow during generation and read by the block gutter
    # to display status badges and progress bars.
    status: Optional[str] = None  # None | "pending" | "generating" | "done" | "error"
    progress: float = 0.0  # 0.0 - 1.0 (only meaningful when status == "generating")
    duration: float = 0.0  # seconds of generated audio (when status == "done")
    # P3.45.1: multi-part coverage payload for the gutter badge, derived
    # from engine.audio_provenance.block_slot_states at the refresh points
    # (covered / total expected slots of this block). Display-only runtime
    # state — the SAME contract as status/progress/duration above: never
    # persisted (to_dict/from_dict), never semantic (has_overrides), and
    # never a second source of truth (the Scene's slot structure and asset
    # stream remain the only provenance data; these fields are a pushed
    # presentation copy, re-derived from that data on every sync).
    parts_done: int = 0    # covered slots (badge "✓ N/N" / "⚙ N/N" when parts_total > 1)
    parts_total: int = 0   # expected slots of this block (0/1 = single-part badge)

    def has_overrides(self) -> bool:
        """True when this block carries ANY user-authored semantic state.

        P3.44.7 — the previous definition (emotion/style/speed/pitch/
        delivery only) made a Character-only assignment invisible to
        ``NarrationBlockManager.has_manual_edits``, so Re-detect took the
        DIRECT rebuild branch and silently destroyed the assignment.
        The definition now covers every semantic field a user can
        author on a block: the five override values, the Character
        assignment, the label, and SFX/pause insertions.

        Deliberately EXCLUDED:
          - ``lost_character_id`` — a warning about an already-lost
            Character, not semantic content;
          - ``locked`` / ``manually_edited`` — control state, tracked
            separately by ``has_manual_edits``;
          - ``status`` / ``progress`` / ``duration`` / ``parts_done`` /
            ``parts_total`` — runtime display state owned by the
            generation pipeline (P3.45.1: ``parts_*`` are a pushed
            presentation copy of the derived slot coverage).
        """
        return (any(v is not None for v in
                    (self.emotion, self.style, self.speed,
                     self.pitch, self.delivery))
                or self.character_id is not None
                or self.label is not None
                or bool(self.sfx_insertions)
                or bool(self.pause_insertions))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "emotion": self.emotion,
            "style": self.style,
            "speed": self.speed,
            "pitch": self.pitch,
            "delivery": self.delivery,
            "sfx_insertions": [
                {"sfx_name": s.sfx_name, "onomatopoeia": s.onomatopoeia,
                 "offset": s.offset}
                for s in self.sfx_insertions
            ],
            "pause_insertions": [
                {"pause_type": p.pause_type, "offset": p.offset}
                for p in self.pause_insertions
            ],
            "label": self.label,
            "locked": self.locked,
            "manually_edited": self.manually_edited,
            "character_id": self.character_id,
            "lost_character_id": self.lost_character_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PromptBlock":
        sfx = [SfxInsertion(
            s["sfx_name"], s["onomatopoeia"], s["offset"]
        ) for s in data.get("sfx_insertions", [])]
        pauses = [PauseInsertion(
            p["pause_type"], p["offset"]
        ) for p in data.get("pause_insertions", [])]
        return cls(
            id=data.get("id", str(uuid.uuid4())[:12]),
            start_offset=data.get("start_offset", 0),
            end_offset=data.get("end_offset", 0),
            emotion=data.get("emotion"),
            style=data.get("style"),
            speed=data.get("speed"),
            pitch=data.get("pitch"),
            delivery=data.get("delivery"),
            sfx_insertions=sfx,
            pause_insertions=pauses,
            label=data.get("label"),
            locked=data.get("locked", False),
            manually_edited=data.get("manually_edited", False),
            character_id=data.get("character_id"),
            lost_character_id=data.get("lost_character_id"),
        )


@dataclass
class EffectiveBlockState:
    """The resolved state for a block after inheriting global defaults."""
    block_index: int
    text: str
    emotion: Optional[str] = None
    style: Optional[str] = None
    speed: Optional[str] = None
    pitch: Optional[str] = None
    delivery: Optional[str] = None
    sfx_insertions: List[SfxInsertion] = field(default_factory=list)
    pause_insertions: List[PauseInsertion] = field(default_factory=list)


@dataclass
class TokenEmission:
    """A planned token insertion, output by the PromptOptimizer."""
    block_index: int
    category: str   # "emotion", "style", "prosody"
    value: str      # the tag value (e.g. "awe", "whispering", "speed_slow")


@dataclass
class NarrationTemplate:
    """A reusable Narration Block template (settings without text)."""
    name: str
    emotion: Optional[str] = None
    style: Optional[str] = None
    speed: Optional[str] = None
    pitch: Optional[str] = None
    delivery: Optional[str] = None
    label: str = ""
    description: str = ""
    icon: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "emotion": self.emotion,
            "style": self.style,
            "speed": self.speed,
            "pitch": self.pitch,
            "delivery": self.delivery,
            "label": self.label,
            "description": self.description,
            "icon": self.icon,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NarrationTemplate":
        return cls(
            name=data["name"],
            emotion=data.get("emotion"),
            style=data.get("style"),
            speed=data.get("speed"),
            pitch=data.get("pitch"),
            delivery=data.get("delivery"),
            label=data.get("label", ""),
            description=data.get("description", ""),
            icon=data.get("icon", ""),
        )


BUILTIN_TEMPLATES: List[NarrationTemplate] = [
    NarrationTemplate("YouTube Intro", emotion="Enthusiasm",
                      label="Introduction", description="Energetic opening for videos",
                      icon="🎬"),
    NarrationTemplate("Product Description", emotion="Determination",
                      label="Description", description="Clear, confident product overview",
                      icon="📦"),
    NarrationTemplate("Comparison", emotion="Contemplation",
                      label="Comparison", description="Thoughtful comparison of options",
                      icon="⚖️"),
    NarrationTemplate("Pros", emotion="Pride",
                      label="Pros", description="Positive points with confidence",
                      icon="✅"),
    NarrationTemplate("Cons", emotion="Bitterness",
                      label="Cons", description="Negative points with restraint",
                      icon="❌"),
    NarrationTemplate("Call to Action", emotion="Determination", style="Shouting",
                      label="Call to Action", description="Urgent, motivating close",
                      icon="📢"),
    NarrationTemplate("Podcast Intro", emotion="Contentment",
                      label="Introduction", description="Warm, welcoming podcast opening",
                      icon="🎙️"),
    NarrationTemplate("Story Opening", emotion="Awe",
                      label="Story", description="Wonder and intrigue",
                      icon="📖"),
    NarrationTemplate("Dramatic Reveal", emotion="Surprise",
                      label="Surprise", description="Unexpected revelation",
                      icon="😲"),
    NarrationTemplate("Ending", emotion="Relief",
                      label="Conclusion", description="Satisfied, wrapping up",
                      icon="🏁"),
    NarrationTemplate("Question", emotion="Confusion",
                      label="Question", description="Puzzled, thought-provoking",
                      icon="❓"),
    NarrationTemplate("Whisper Secret", emotion="Affection", style="Whispering",
                      label="Secret", description="Intimate, confidential",
                      icon="🤫"),
]
