"""
SpeechStudio Engine package.

The Engine is the execution layer of SpeechStudio (Engine Specification,
section 1). It hides all Hugging Face and Transformers details from the UI.
The UI communicates only with the Engine.

Public API:
    from engine import Engine, EventType, GenerationRequest, GenerationParameters

Engine modules (single responsibility each):
    - ModelManager       owns the single model and tokenizer instance
    - PromptBuilder      the only component allowed to assemble Higgs prompts
    - VoiceManager       owns reference audio and transcripts
    - GenerationManager  owns generation requests and results
    - AudioManager       owns playback buffers and saved audio
    - HistoryManager     stores reproducible generation history
    - SettingsManager    persists application settings as JSON
    - Logger             structured logging for every subsystem

Architectural rules (AI Development Rules, sections 4/5/8/9):
    - The UI never communicates directly with Transformers.
    - The Engine never manipulates UI widgets.
    - Exactly one model instance and one tokenizer instance exist.
    - Only PromptBuilder may construct Higgs prompt tokens.
"""

from engine.engine import Engine
from engine.events import EventBus, EventType, Event
from engine.models import (
    GenerationRequest,
    GenerationResult,
    GenerationParameters,
    VoiceProfile,
    ModelStatus,
    PromptData,
)
from engine.errors import (
    EngineError,
    ModelNotLoaded,
    CudaUnavailable,
    InvalidPrompt,
    InvalidVoice,
    GenerationFailed,
    ValidationFailed,
)

__all__ = [
    # Facade
    "Engine",
    # Events
    "EventBus",
    "EventType",
    "Event",
    # Data models
    "GenerationRequest",
    "GenerationResult",
    "GenerationParameters",
    "VoiceProfile",
    "ModelStatus",
    "PromptData",
    # Errors
    "EngineError",
    "ModelNotLoaded",
    "CudaUnavailable",
    "InvalidPrompt",
    "InvalidVoice",
    "GenerationFailed",
    "ValidationFailed",
]
