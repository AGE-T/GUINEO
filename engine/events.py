"""
SpeechStudio Engine - Event System
===================================

Engine Specification, section 15:
    "The Engine shall publish events.
     GenerationStarted, GenerationFinished, GenerationFailed,
     PlaybackStarted, PlaybackFinished, VoiceChanged,
     ModelLoaded, ModelUnloaded, HistoryUpdated.
     UI components subscribe to these events."

The event system uses a simple observer pattern. The Engine (and its
managers) publish events; subscribers (typically the UI in Phase 3) register
callbacks. The system is Qt-free so the Engine has no UI dependency
(Engine Constraints, section 23: "shall never Access UI widgets").

Threading note:
    Events are emitted on the thread that triggers them (usually the
    generation worker thread). Subscribers that touch the UI MUST marshal
    back to the UI thread (e.g. via Qt signals in Phase 3).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List
from enum import Enum

from engine.logger import get_logger

logger = get_logger("events")


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------
class EventType(str, Enum):
    """Every event the Engine can publish (Engine Specification section 15)."""

    # Generation lifecycle
    GENERATION_STARTED = "GenerationStarted"
    GENERATION_FINISHED = "GenerationFinished"
    GENERATION_FAILED = "GenerationFailed"

    # Playback lifecycle
    PLAYBACK_STARTED = "PlaybackStarted"
    PLAYBACK_FINISHED = "PlaybackFinished"
    PLAYBACK_PAUSED = "PlaybackPaused"
    PLAYBACK_RESUMED = "PlaybackResumed"
    PLAYBACK_SEEKED = "PlaybackSeeked"
    PLAYBACK_POSITION_CHANGED = "PlaybackPositionChanged"

    # Voice lifecycle
    VOICE_CHANGED = "VoiceChanged"

    # Model lifecycle
    MODEL_LOADED = "ModelLoaded"
    MODEL_UNLOADED = "ModelUnloaded"

    # History lifecycle
    HISTORY_UPDATED = "HistoryUpdated"

    # Engine lifecycle (used in Phase 2 for model loading status)
    ENGINE_INITIALIZED = "EngineInitialized"
    ENGINE_STATUS_CHANGED = "EngineStatusChanged"


@dataclass
class Event:
    """A single event published by the Engine.

    Attributes:
        type: the EventType enum member.
        data: arbitrary payload (e.g. the result object, the voice id).
    """
    type: EventType
    data: Any = None


# ---------------------------------------------------------------------------
# Event bus
# ---------------------------------------------------------------------------
# Type alias for subscriber callbacks: callable(event: Event) -> None
EventCallback = Callable[[Event], None]


class EventBus:
    """A lightweight publish/subscribe event bus.

    The Engine owns one instance. Managers and the UI register callbacks
    by event type. Emission is synchronous - callbacks run on the emitter's
    thread.

    Error isolation: if a callback raises, the exception is logged and the
    bus continues dispatching to remaining subscribers (AI Development Rules
    section 12: "The application must not terminate unexpectedly").
    """

    def __init__(self) -> None:
        self._subscribers: Dict[EventType, List[EventCallback]] = {}

    def subscribe(self, event_type: EventType, callback: EventCallback) -> None:
        """Register a callback for a specific event type."""
        self._subscribers.setdefault(event_type, []).append(callback)
        logger.debug("Subscribed to %s (total: %d)",
                     event_type.value,
                     len(self._subscribers[event_type]))

    def unsubscribe(self, event_type: EventType, callback: EventCallback) -> None:
        """Remove a previously registered callback."""
        subs = self._subscribers.get(event_type)
        if subs and callback in subs:
            subs.remove(callback)

    def emit(self, event: Event) -> None:
        """Dispatch an event to all registered subscribers.

        Exceptions in individual callbacks are logged and swallowed so a
        faulty subscriber cannot crash the engine.
        """
        subs = self._subscribers.get(event.type, [])
        logger.debug("Emitting %s to %d subscriber(s)",
                     event.type.value, len(subs))
        for callback in subs:
            try:
                callback(event)
            except Exception as exc:  # noqa: BLE001 - must not crash the bus
                logger.exception(
                    "Subscriber {0} raised {1}: {2}".format(
                        getattr(callback, "__name__", repr(callback)),
                        type(exc).__name__,
                        exc,
                    ),
                )

    def clear(self) -> None:
        """Remove every subscriber (used on shutdown)."""
        self._subscribers.clear()
