"""
SpeechStudio Engine - Data Models
=================================

Defines the data structures exchanged between engine modules and returned
to the UI. Every class is a plain data container with no business logic
(single responsibility: "hold data").

Key types:
    - GenerationParameters: sampling settings (temperature, top_p, ...)
    - GenerationRequest:    everything needed to start a generation
    - GenerationResult:     everything returned to the UI after generation
    - VoiceProfile:         a voice profile's metadata + reference data
    - PromptData:           the output of the PromptBuilder
    - ModelStatus:          snapshot of the model/tokenizer/CUDA state

Engine Specification, section 22 (Result Object):
    "Every generation returns: Success, Generated audio path, Output duration,
     Generation duration, Realtime factor, Sampling settings, Voice profile,
     Warnings, Errors. This object is the only output returned to the UI."

Generation Engine Specification, section 13 (Generation Metadata):
    "Each generation shall return: Generation Time, Output Duration,
     Realtime Factor, Output Sample Rate, Output File, Generation Parameters,
     Voice Profile, Timestamp."
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime
import uuid


# ---------------------------------------------------------------------------
# Generation parameters
# ---------------------------------------------------------------------------
# Generation Engine Specification, section 8 + Higgs Audio V3 Reference section 7.
@dataclass
class GenerationParameters:
    """Sampling and output parameters for a single generation.

    Only officially supported parameters are exposed (Engine Specification
    section 9: "Only officially supported parameters shall be exposed").
    """

    temperature: float = 1.3          # range 0.1 - 2.0, recommended 1.2-1.4
    top_p: float = 0.95               # range 0.1 - 1.0
    top_k: int = 300                  # range 1 - 500, recommended 300
    max_new_tokens: int = 4096        # range 128 - 8192, 4096 ≈ 30s audio
    seed: Optional[int] = None        # None = random
    append_silence: float = 0.5       # seconds, range 0.0 - 5.0, recommended 0.5
    normalize_output: bool = False
    auto_play: bool = True
    output_format: str = "wav"        # currently only "wav" supported
    # Application-level SFX policy (NOT a HIGGS token). When False, SFX
    # markers ({sfx:Name:Onomatopoeia}) in the text are replaced with
    # just the onomatopoeia text — no <|sfx:tag|> token is emitted.
    # When True (default), markers are converted to tokens normally.
    allow_sfx: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "max_new_tokens": self.max_new_tokens,
            "seed": self.seed,
            "append_silence": self.append_silence,
            "normalize_output": self.normalize_output,
            "auto_play": self.auto_play,
            "output_format": self.output_format,
            "allow_sfx": self.allow_sfx,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GenerationParameters":
        # P3.25 (audit SS-M18): the fallback literals previously diverged
        # from the dataclass defaults (temperature 1.0 vs 1.3, top_k 50 vs
        # 300, max_new_tokens 2048 vs 4096, append_silence 1.0 vs 0.5) —
        # old scenes/history/presets with missing keys silently restored
        # DIFFERENT values than a fresh object (Engine Spec §11
        # reproducibility violation). The dataclass defaults are now the
        # single source of truth.
        defaults = cls()
        if not isinstance(data, dict):
            data = {}
        return cls(
            temperature=data.get("temperature", defaults.temperature),
            top_p=data.get("top_p", defaults.top_p),
            top_k=data.get("top_k", defaults.top_k),
            max_new_tokens=data.get("max_new_tokens", defaults.max_new_tokens),
            seed=data.get("seed"),
            append_silence=data.get("append_silence", defaults.append_silence),
            normalize_output=data.get("normalize_output", defaults.normalize_output),
            auto_play=data.get("auto_play", defaults.auto_play),
            output_format=data.get("output_format", defaults.output_format),
            allow_sfx=data.get("allow_sfx", defaults.allow_sfx),
        )

    def validate(self) -> List[str]:
        """Return a list of validation warning strings (empty if valid)."""
        warnings: List[str] = []
        if not 0.1 <= self.temperature <= 2.0:
            warnings.append("temperature must be between 0.1 and 2.0")
        if not 0.1 <= self.top_p <= 1.0:
            warnings.append("top_p must be between 0.1 and 1.0")
        if not 1 <= self.top_k <= 500:
            warnings.append("top_k must be between 1 and 500")
        if not 128 <= self.max_new_tokens <= 8192:
            warnings.append("max_new_tokens must be between 128 and 8192")
        if not 0.0 <= self.append_silence <= 5.0:
            warnings.append("append_silence must be between 0.0 and 5.0")
        if self.output_format != "wav":
            warnings.append("only 'wav' output_format is currently supported")
        return warnings


# ---------------------------------------------------------------------------
# Voice profile
# ---------------------------------------------------------------------------
# System Specification section 11, Voice System spec.
@dataclass
class VoiceProfile:
    """A voice profile used for voice cloning.

    Each profile contains reference audio, an optional transcript, and
    metadata. Profiles are independent (System Specification section 11).
    """

    id: str                           # unique identifier (filename-safe)
    name: str                         # display name
    reference_audio_path: str = ""    # relative path to WAV file
    reference_transcript: str = ""    # text transcript of the reference
    sample_rate: int = 0             # detected from the reference WAV
    channels: int = 0                # detected from the reference WAV
    duration: float = 0.0            # seconds, detected from the reference WAV
    description: str = ""
    tags: List[str] = field(default_factory=list)
    preview_image: str = ""          # optional relative path
    # P3.23 (voice import API drift fix): speaker metadata collected by
    # the Voice Import dialog. Previously the UI called
    # Engine.set_voice_speaker_metadata() which did not exist — the call
    # raised AttributeError and the whole import reported "Failed to
    # import voice" whenever gender/age/mood was filled in.
    gender: str = ""                  # "" | "male" | "female" | ... (free text)
    age_range: str = ""               # e.g. "young", "middle", "senior"
    mood: str = ""                    # e.g. "warm", "bright", "dark"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "reference_audio_path": self.reference_audio_path,
            "reference_transcript": self.reference_transcript,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "duration": self.duration,
            "description": self.description,
            "tags": list(self.tags),
            "preview_image": self.preview_image,
            "gender": self.gender,
            "age_range": self.age_range,
            "mood": self.mood,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VoiceProfile":
        return cls(
            id=data["id"],
            name=data["name"],
            reference_audio_path=data.get("reference_audio_path", ""),
            reference_transcript=data.get("reference_transcript", ""),
            sample_rate=data.get("sample_rate", 0),
            channels=data.get("channels", 0),
            duration=data.get("duration", 0.0),
            description=data.get("description", ""),
            tags=list(data.get("tags", [])),
            preview_image=data.get("preview_image", ""),
            gender=data.get("gender", ""),
            age_range=data.get("age_range", ""),
            mood=data.get("mood", ""),
        )

    @property
    def has_reference(self) -> bool:
        """True if the profile has reference audio suitable for cloning."""
        return bool(self.reference_audio_path) and self.sample_rate > 0


# ---------------------------------------------------------------------------
# Prompt data
# ---------------------------------------------------------------------------
# Prompt System section 11: PromptBuilder outputs.
@dataclass
class PromptData:
    """The output of the PromptBuilder.

    Attributes:
        final_prompt: the exact string sent to the model.
        token_preview: human-readable token list (for the preview panel).
        warnings: validation warnings (non-blocking).
        plain_text: the original user text without tokens.
    """
    final_prompt: str
    plain_text: str
    token_preview: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """True if the prompt has content and no blocking issues.

        Note: warnings are non-blocking. Blocking issues raise InvalidPrompt
        before a PromptData is ever returned.
        """
        return bool(self.final_prompt.strip())


# ---------------------------------------------------------------------------
# Generation request
# ---------------------------------------------------------------------------
# The input to Engine.generate(). Everything the Engine needs to produce
# speech in one object.
@dataclass
class GenerationRequest:
    """Everything the Engine needs to start a generation.

    The UI constructs this and passes it to Engine.generate().
    """

    text: str                           # the plain user text
    emotion: Optional[str] = None       # emotion name (e.g. "Awe")
    style: Optional[str] = None         # style name (e.g. "Whispering")
    speed: Optional[str] = None         # speed name (e.g. "Slow")
    pitch: Optional[str] = None         # pitch name (e.g. "Low")
    delivery: Optional[str] = None      # delivery name (e.g. "Expressive High")
    sfx_insertions: List[Dict[str, str]] = field(default_factory=list)
    # sfx_insertions: list of {"sfx": "Laughter", "onomatopoeia": "Haha", "position": "before"|<text>}
    pause_insertions: List[Dict[str, Any]] = field(default_factory=list)
    # pause_insertions: list of {"type": "pause"|"long_pause", "after_text": <text>}
    voice_id: Optional[str] = None      # voice profile id (None = no cloning)
    parameters: GenerationParameters = field(default_factory=GenerationParameters)
    # Optional explicit output filename (used by the batch manager and
    # "save-as" workflows).  None = auto-generated timestamp filename.
    output_filename: Optional[str] = None
    # Project name for history grouping (e.g. "My Book", "Podcast Ep3").
    project: str = "Default"
    # P2.6: Scene/Character context for history linkage.
    scene_id: Optional[str] = None
    scene_name: Optional[str] = None
    character_id: Optional[str] = None
    speaker: Optional[str] = None
    # P3.27B: Long Generation part provenance + expected duration (the
    # splitter's estimate for the part's text). Used by the output guard
    # to detect pathological runaway generations conservatively, and by
    # History to record which part/version a generation belongs to.
    part_index: Optional[int] = None
    part_version: Optional[int] = None
    expected_duration: Optional[float] = None
    # P3.28: provenance passthrough — the source Narration Block and the
    # scene-scoped generation run id ("{scene8}-r{NNN}"). Routed through
    # Engine → GenerationResult → AudioAsset/HistoryEntry so every
    # generated file answers "which block / which run created me?".
    block_id: Optional[str] = None
    generation_run: Optional[str] = None


# ---------------------------------------------------------------------------
# Generation result
# ---------------------------------------------------------------------------
# Engine Specification section 22, Generation Engine Spec section 13.
@dataclass
class GenerationResult:
    """The only object returned to the UI after a generation.

    Success or failure, this object is always returned (Engine Specification
    section 22: "This object is the only output returned to the UI").
    """

    success: bool
    output_path: str = ""               # relative path to the WAV file
    output_duration: float = 0.0        # seconds of generated audio
    output_sample_rate: int = 0         # Hz (24000 for Higgs Audio V3)
    generation_time: float = 0.0        # seconds the model took
    realtime_factor: float = 0.0        # generation_time / output_duration
    parameters: Optional[GenerationParameters] = None
    voice_profile: Optional[VoiceProfile] = None
    prompt: str = ""                    # the final prompt that was sent
    timestamp: str = ""                 # ISO-format timestamp
    warnings: List[str] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)
    gpu_name: str = ""
    vram_usage_mb: float = 0.0          # if available
    project: str = "Default"            # project name for history grouping
    # P2.6: Scene/Character context for history linkage.
    scene_id: Optional[str] = None
    scene_name: Optional[str] = None
    character_id: Optional[str] = None
    speaker: Optional[str] = None
    # P3.27B: part provenance passthrough (set by Engine from the request)
    # and the output-guard analysis of the raw model waveform. The guard
    # NEVER discards audio — it only flags (see engine/output_guard.py).
    part_index: Optional[int] = None
    part_version: Optional[int] = None
    output_anomaly: Optional[Dict[str, Any]] = None
    # P3.28: provenance passthrough (block + generation run), set by
    # Engine from the request. Consumed by the single registration
    # writer (engine/audio_provenance.register_generation_result).
    block_id: Optional[str] = None
    generation_run: Optional[str] = None
    # P3.30(d): True when the pipeline was stopped by a cooperative
    # cancel at a safe checkpoint (before the model call). A cancelled
    # result never has output audio; the BatchManager renders it as a
    # user stop (SKIPPED), not an error.
    cancelled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "output_path": self.output_path,
            "output_duration": self.output_duration,
            "output_sample_rate": self.output_sample_rate,
            "generation_time": self.generation_time,
            "realtime_factor": self.realtime_factor,
            "parameters": self.parameters.to_dict() if self.parameters else None,
            "voice_profile": self.voice_profile.to_dict() if self.voice_profile else None,
            "prompt": self.prompt,
            "timestamp": self.timestamp,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "gpu_name": self.gpu_name,
            "vram_usage_mb": self.vram_usage_mb,
        }


# ---------------------------------------------------------------------------
# Model status
# ---------------------------------------------------------------------------
@dataclass
class ModelStatus:
    """Snapshot of the model / tokenizer / CUDA state.

    Returned by ModelManager.status() and displayed in the UI status bar.
    """
    model_loaded: bool = False
    tokenizer_loaded: bool = False
    cuda_available: bool = False
    gpu_name: str = ""
    gpu_count: int = 0
    vram_total_mb: float = 0.0
    vram_used_mb: float = 0.0
    model_id: str = ""
    dtype: str = ""
    loading: bool = False
    load_progress: float = 0.0          # 0.0 to 1.0
    # P3.29: human-readable load phase ("Loading tokenizer...",
    # "Loading model weights...", "Moving model to CUDA..."). Published by
    # ModelManager at each load-phase transition so the ModelLoadDialog
    # can show WHAT the loader is doing (additive; empty when idle).
    phase: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_loaded": self.model_loaded,
            "tokenizer_loaded": self.tokenizer_loaded,
            "cuda_available": self.cuda_available,
            "gpu_name": self.gpu_name,
            "gpu_count": self.gpu_count,
            "vram_total_mb": self.vram_total_mb,
            "vram_used_mb": self.vram_used_mb,
            "model_id": self.model_id,
            "dtype": self.dtype,
            "loading": self.loading,
            "load_progress": self.load_progress,
            "phase": self.phase,
        }


# ---------------------------------------------------------------------------
# Project entity (P1.2 — Phase 1 authoritative data model)
# ---------------------------------------------------------------------------
@dataclass
class Character:
    """A narrative character within a Project.

    A Character is a narrative role (e.g. "Engineer", "Captain") that
    references a VoiceProfile for voice cloning. The Character is separate
    from the VoiceProfile — the same Character can be re-assigned to a
    different VoiceProfile without changing its identity.

    Attributes:
        id: unique identifier (filename-safe).
        name: display name (e.g. "Engineer").
        description: optional human-readable description.
        voice_profile_id: the VoiceProfile ID this character uses, or None.
        tags: optional list of string tags.
        role: optional role description (e.g. "protagonist", "narrator").
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = ""
    description: str = ""
    voice_profile_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    role: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "voice_profile_id": self.voice_profile_id,
            "tags": list(self.tags),
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Character":
        import uuid as _uuid
        return cls(
            id=data.get("id", str(_uuid.uuid4())[:12]),
            name=data.get("name", ""),
            description=data.get("description", ""),
            voice_profile_id=data.get("voice_profile_id"),
            tags=list(data.get("tags", [])),
            role=data.get("role", ""),
        )


@dataclass
class Scene:
    """A single scene within a Project.

    A Scene is an independently editable, independently generatable content
    unit. It contains the editor text, Narration Block state, character
    references, generation settings, and generated audio references.

    Attributes:
        id: unique identifier (filename-safe).
        name: display name (e.g. "Scene 01").
        project_id: the Project ID this scene belongs to.
        text: the editor text content (plain text, no HIGGS tokens).
        narration_blocks: serialised Narration Block state (list of dicts).
        character_ids: list of Character IDs used in this scene.
        emotion/style/speed/pitch/delivery: global defaults for this scene.
        parameters: generation parameters for this scene.
        status: generation status (draft/ready/generating/complete/error).
        audio_assets: list of generated audio asset references.
        voice_profile_id: P3.13 — Scene-scoped Voice Profile selection.
            When set, this is the authoritative voice for Generate/Generate
            Long from this Scene. Survives Save/Restart/Reopen. Independent
            per Scene — switching Scenes does NOT overwrite this.
        created_at: ISO timestamp.
        modified_at: ISO timestamp.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = "Untitled"
    project_id: str = ""
    text: str = ""
    narration_blocks: List[Dict[str, Any]] = field(default_factory=list)
    character_ids: List[str] = field(default_factory=list)
    emotion: Optional[str] = None
    style: Optional[str] = None
    speed: str = "Normal"
    pitch: str = "Normal"
    delivery: str = "Normal"
    parameters: Optional[GenerationParameters] = None
    status: str = "draft"
    audio_assets: List[Dict[str, Any]] = field(default_factory=list)
    # P3.13: Scene-scoped Voice Profile. None = no scene-specific voice
    # (use the global/control-panel voice). When set, this is authoritative.
    voice_profile_id: Optional[str] = None
    # P3.21: User-defined Scene ordering within the Project.
    # Independent from name, creation time, filesystem order.
    # Default 0 — backward compatible (existing scenes get 0, displayed in insertion order).
    sort_order: int = 0
    # P3.26: Scene-scoped editor mode. The editor mode (Plain Text /
    # Narration Blocks / Preview) is part of the Scene's state contract —
    # switching Scenes must restore each Scene's own mode instead of
    # globally retaining the last one. Values: "plain" | "blocks" |
    # "preview". Default "plain" — backward compatible: existing
    # projects (saved before P3.26) open in Plain Text.
    editor_mode: str = "plain"
    # P3.26: the selected Narration Block (Scene state contract Part 4).
    # None = no selection. Restored only when the id still exists in the
    # restored blocks and the Scene is in Narration Blocks mode.
    selected_block_id: Optional[str] = None
    # -----------------------------------------------------------------
    # P3.28 Scene Audio Provenance (design record §3 / Rec 23 — additive):
    # -----------------------------------------------------------------
    # Expected generation slots — materialised ONLY by explicit structure
    # events (Generate Long preview / Re-detect), never on a keystroke.
    # Each slot: {slot_id, block_id, block_label, part_index,
    #             part_of_block, speaker, character_id}.
    expected_audio_slots: List[Dict[str, Any]] = field(default_factory=list)
    # Scene Combined outputs — append-only, versioned, with per-slot
    # lineage (same entry shape philosophy as Project.combined_outputs).
    combined_outputs: List[Dict[str, Any]] = field(default_factory=list)
    # Explicit per-slot version selection: {slot_id → asset_id}.
    # Empty ⇒ deterministic default (latest version per slot).
    selected_block_audio: Dict[str, str] = field(default_factory=dict)
    # Explicit Scene-level output selection:
    # {kind: "scene_combined"|"asset", id} | None. None ⇒ the
    # deterministic resolve_scene_output() chain.
    selected_output: Optional[Dict[str, Any]] = None
    created_at: str = ""
    modified_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "project_id": self.project_id,
            "text": self.text,
            "narration_blocks": list(self.narration_blocks),
            "character_ids": list(self.character_ids),
            "emotion": self.emotion,
            "style": self.style,
            "speed": self.speed,
            "pitch": self.pitch,
            "delivery": self.delivery,
            "parameters": self.parameters.to_dict() if self.parameters else None,
            "status": self.status,
            "audio_assets": list(self.audio_assets),
            "voice_profile_id": self.voice_profile_id,
            "sort_order": self.sort_order,
            "editor_mode": self.editor_mode,
            "selected_block_id": self.selected_block_id,
            # P3.28 provenance fields (additive — legacy JSON without
            # them imports with the tolerant defaults below).
            "expected_audio_slots": list(self.expected_audio_slots),
            "combined_outputs": list(self.combined_outputs),
            "selected_block_audio": dict(self.selected_block_audio),
            "selected_output": dict(self.selected_output)
            if self.selected_output else None,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Scene":
        import uuid as _uuid
        params_data = data.get("parameters")
        params = GenerationParameters.from_dict(params_data) if params_data else None
        return cls(
            id=data.get("id", str(_uuid.uuid4())[:12]),
            name=data.get("name", "Untitled"),
            project_id=data.get("project_id", ""),
            text=data.get("text", ""),
            narration_blocks=list(data.get("narration_blocks", [])),
            character_ids=list(data.get("character_ids", [])),
            emotion=data.get("emotion"),
            style=data.get("style"),
            speed=data.get("speed", "Normal"),
            pitch=data.get("pitch", "Normal"),
            delivery=data.get("delivery", "Normal"),
            parameters=params,
            status=data.get("status", "draft"),
            # P3.28 §26: tolerant audio_assets reader (a malformed value
            # previously became a list of CHARACTERS via list(str)).
            audio_assets=_as_dict_list(data.get("audio_assets")),
            voice_profile_id=data.get("voice_profile_id"),
            sort_order=data.get("sort_order", 0),
            # P3.26: per-Scene editor mode. Unknown/missing values (older
            # project files) deterministically restore to "plain" — one
            # policy, never garbage.
            editor_mode=(data.get("editor_mode")
                         if data.get("editor_mode") in ("plain", "blocks",
                                                        "preview")
                         else "plain"),
            selected_block_id=data.get("selected_block_id"),
            # P3.28 provenance fields — tolerant readers (P3.28 §26 JSON
            # robustness): wrong types degrade to defaults, never crash.
            expected_audio_slots=_as_dict_list(
                data.get("expected_audio_slots")),
            combined_outputs=_as_dict_list(data.get("combined_outputs")),
            selected_block_audio=_as_str_dict(
                data.get("selected_block_audio")),
            selected_output=(data.get("selected_output")
                             if isinstance(data.get("selected_output"), dict)
                             else None),
            created_at=data.get("created_at", ""),
            modified_at=data.get("modified_at", ""),
        )


def _as_dict_list(value: Any) -> List[Dict[str, Any]]:
    """Tolerant list-of-dicts reader (P3.28 §26 JSON robustness)."""
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]


def _as_str_dict(value: Any) -> Dict[str, str]:
    """Tolerant {str: str} reader (P3.28 §26 JSON robustness)."""
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()
            if isinstance(v, str)}


@dataclass
class Project:
    """A first-class Project entity — container for Scenes and Characters.

    A Project represents a complete work (e.g. a book, an audiobook, a
    podcast episode). It contains multiple Scenes and a roster of
    Characters.

    Attributes:
        id: unique identifier (filename-safe).
        name: display name (e.g. "Eden").
        description: optional human-readable description.
        characters: list of Character entities in this project.
        scenes: list of Scene entities in this project.
        speaker_voice_map: per-project speaker→voice_id mapping (legacy compat).
        created_at: ISO timestamp.
        modified_at: ISO timestamp.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = "Default"
    description: str = ""
    characters: List[Character] = field(default_factory=list)
    scenes: List[Scene] = field(default_factory=list)
    speaker_voice_map: Dict[str, str] = field(default_factory=dict)
    # P3.22 Scene Chain: combined audio outputs assembled from scene audio.
    # Each entry is a dict from combined_audio.build_combined_output_entry().
    # No new entity class — plain dicts per the locked architecture decision.
    combined_outputs: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    modified_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "characters": [c.to_dict() for c in self.characters],
            "scenes": [s.to_dict() for s in self.scenes],
            "speaker_voice_map": dict(self.speaker_voice_map),
            "combined_outputs": list(self.combined_outputs),
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Project":
        import uuid as _uuid
        characters = [Character.from_dict(c) for c in data.get("characters", [])]
        scenes = [Scene.from_dict(s) for s in data.get("scenes", [])]
        return cls(
            id=data.get("id", str(_uuid.uuid4())[:12]),
            name=data.get("name", "Default"),
            description=data.get("description", ""),
            characters=characters,
            scenes=scenes,
            speaker_voice_map=dict(data.get("speaker_voice_map", {})),
            combined_outputs=list(data.get("combined_outputs", [])),
            created_at=data.get("created_at", ""),
            modified_at=data.get("modified_at", ""),
        )

    def add_scene(self, scene: Scene) -> None:
        """Add a Scene to this project."""
        scene.project_id = self.id
        self.scenes.append(scene)

    def add_character(self, character: Character) -> None:
        """Add a Character to this project."""
        self.characters.append(character)

    def get_scene(self, scene_id: str) -> Optional[Scene]:
        for s in self.scenes:
            if s.id == scene_id:
                return s
        return None

    def get_character(self, character_id: str) -> Optional[Character]:
        for c in self.characters:
            if c.id == character_id:
                return c
        return None


# ---------------------------------------------------------------------------
# Audio asset reference (generated audio belonging to a Scene)
# ---------------------------------------------------------------------------
@dataclass
class AudioAsset:
    """A reference to a generated audio file belonging to a Scene.

    This is NOT a copy of the audio — it references the canonical output
    file in the outputs/ directory.

    Attributes:
        id: unique identifier.
        scene_id: the Scene ID this audio belongs to.
        output_path: relative path to the WAV file.
        duration: audio duration in seconds.
        speaker: optional speaker label (for multi-speaker scenes).
        character_id: optional Character ID.
        voice_profile_id: optional VoiceProfile ID used for generation.
        generated_at: ISO timestamp.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    scene_id: str = ""
    output_path: str = ""
    duration: float = 0.0
    speaker: Optional[str] = None
    character_id: Optional[str] = None
    voice_profile_id: Optional[str] = None
    generated_at: str = ""
    # P3.28 provenance (design record Rec 23 — additive; None for legacy
    # full-scene assets): the source Narration Block, the Long Generation
    # part index, the per-slot monotonic version, and the scene-scoped
    # generation run id. Identity NEVER comes from parsing filenames.
    block_id: Optional[str] = None
    part_index: Optional[int] = None
    part_version: Optional[int] = None
    generation_run: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "scene_id": self.scene_id,
            "output_path": self.output_path,
            "duration": self.duration,
            "speaker": self.speaker,
            "character_id": self.character_id,
            "voice_profile_id": self.voice_profile_id,
            "generated_at": self.generated_at,
            "block_id": self.block_id,
            "part_index": self.part_index,
            "part_version": self.part_version,
            "generation_run": self.generation_run,
        }
