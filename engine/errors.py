"""
SpeechStudio Engine - Structured Error Types
============================================

Engine Specification, section 17:
    "Structured errors only."
    Examples: ModelNotLoaded, TokenizerMissing, InvalidVoice, InvalidPrompt,
    GenerationFailed, CudaUnavailable, OutputWriteFailed
    "Errors contain: Type, Message, Suggested action"

AI Development Rules, section 12:
    "The AI shall never suppress exceptions.
     Every exception shall be: logged, reported, handled gracefully.
     The application must not terminate unexpectedly."

Every engine error inherits from EngineError and carries:
    - error_type: a stable string identifier (used in logs and UI)
    - message: a human-readable description
    - suggested_action: what the user should do to resolve it

These errors are raised by engine modules and caught by the Engine facade,
which converts them into the Result object returned to the UI.
"""

from __future__ import annotations
from typing import Optional


class EngineError(Exception):
    """Base class for every structured SpeechStudio engine error.

    Attributes:
        error_type: Stable string identifier (e.g. "ModelNotLoaded").
        message: Human-readable description of what went wrong.
        suggested_action: What the user should do to resolve it.
    """

    error_type: str = "EngineError"
    suggested_action: str = "Check the logs for more detail."

    def __init__(self, message: str, suggested_action: Optional[str] = None):
        super().__init__(message)
        self.message = message
        if suggested_action is not None:
            self.suggested_action = suggested_action

    def to_dict(self) -> dict:
        """Serialise the error for the Result object and history."""
        return {
            "type": self.error_type,
            "message": self.message,
            "suggested_action": self.suggested_action,
        }

    def __str__(self) -> str:
        return "[{0}] {1}".format(self.error_type, self.message)


# ---------------------------------------------------------------------------
# Model / tokenizer errors
# ---------------------------------------------------------------------------
class ModelNotLoaded(EngineError):
    """Raised when a generation is requested but the model is not loaded."""

    error_type = "ModelNotLoaded"
    suggested_action = "Wait for the model to finish loading, then try again."


class TokenizerMissing(EngineError):
    """Raised when the tokenizer cannot be loaded or is absent."""

    error_type = "TokenizerMissing"
    suggested_action = (
        "Run launch.bat so that bootstrap can download the tokenizer. "
        "If it is already downloaded, check logs/bootstrap.log for errors."
    )


class ModelLoadFailed(EngineError):
    """Raised when the model fails to load (CUDA OOM, corrupt weights, etc.)."""

    error_type = "ModelLoadFailed"
    suggested_action = (
        "Ensure you have a CUDA-compatible GPU with enough VRAM (~10 GB). "
        "Check logs/engine.log for the underlying error."
    )


# ---------------------------------------------------------------------------
# CUDA errors
# ---------------------------------------------------------------------------
class CudaUnavailable(EngineError):
    """Raised when generation is requested but no CUDA device is available."""

    error_type = "CudaUnavailable"
    suggested_action = (
        "Install an NVIDIA GPU with current drivers, or enable CPU mode in "
        "Settings (not recommended for production use)."
    )


# ---------------------------------------------------------------------------
# Prompt errors
# ---------------------------------------------------------------------------
class InvalidPrompt(EngineError):
    """Raised when prompt validation fails (empty, bad tokens, conflicts)."""

    error_type = "InvalidPrompt"
    suggested_action = "Check the Prompt Preview panel for warnings and fix them."


# ---------------------------------------------------------------------------
# Voice errors
# ---------------------------------------------------------------------------
class InvalidVoice(EngineError):
    """Raised when a voice profile is missing, incomplete, or corrupt."""

    error_type = "InvalidVoice"
    suggested_action = "Select a valid voice profile or create a new one."


class ReferenceAudioMissing(EngineError):
    """Raised when voice cloning is requested but reference audio is absent."""

    error_type = "ReferenceAudioMissing"
    suggested_action = "Import a reference WAV file into the selected voice profile."


class ReferenceTranscriptInvalid(EngineError):
    """Raised when the reference transcript is empty or invalid."""

    error_type = "ReferenceTranscriptInvalid"
    suggested_action = "Provide a transcript that matches the reference audio."


# ---------------------------------------------------------------------------
# Generation errors
# ---------------------------------------------------------------------------
class GenerationFailed(EngineError):
    """Raised when generate_speech() raises an exception or returns no audio."""

    error_type = "GenerationFailed"
    suggested_action = (
        "Check logs/engine.log for the underlying error. Try reducing "
        "max_new_tokens or simplifying the prompt."
    )


class ValidationFailed(EngineError):
    """Raised when the pre-generation validation pipeline rejects a request."""

    error_type = "ValidationFailed"
    suggested_action = "Resolve the validation issues listed in the UI and try again."


# ---------------------------------------------------------------------------
# I/O errors
# ---------------------------------------------------------------------------
class OutputWriteFailed(EngineError):
    """Raised when the output WAV file cannot be written."""

    error_type = "OutputWriteFailed"
    suggested_action = "Check that the outputs/ folder is writable and not full."
