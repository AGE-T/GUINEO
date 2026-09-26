"""
SpeechStudio Engine - Worker Threads
=====================================

AI Development Rules, section 11:
    "Long running operations shall never execute on the UI thread.
     Speech generation executes on a worker thread.
     Audio playback executes independently.
     The interface must remain responsive."

Engine Specification, section 16:
    "Generation executes on a worker thread.
     Audio playback executes independently.
     The UI thread must never wait for generation."

System Specification, section 18:
    "UI Thread: User interaction only.
     Generation Thread: Speech synthesis.
     Playback Thread: Audio playback.
     Background Tasks: Bootstrap operations, file indexing, history updates."

This module provides a simple thread-pool abstraction. The Engine uses it
to run generation on a worker thread. It uses Python's standard
concurrent.futures so the engine has no Qt dependency (the UI layer in
Phase 3 bridges to Qt signals).
"""

from __future__ import annotations
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Optional

from engine.logger import get_logger
from engine.events import EventBus, EventType, Event

logger = get_logger("workers")


class WorkerPool:
    """A small thread pool for engine background tasks.

    The pool uses a single dedicated generation worker (so only one
    generation runs at a time, matching the single-model-instance rule)
    plus a separate small pool for other background tasks (history writes,
    file indexing, etc.).
    """

    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        # Single-thread executor for generation (only one at a time).
        self._gen_executor = ThreadPoolExecutor(max_workers=1,
                                                thread_name_prefix="speechstudio-gen")
        # Two-thread executor for background tasks.
        self._bg_executor = ThreadPoolExecutor(max_workers=2,
                                                thread_name_prefix="speechstudio-bg")
        self._current_generation: Optional[Future] = None
        self._gen_lock = threading.Lock()
        # P3.30(d): cooperative cancel flag. Future.cancel() is a NO-OP
        # once the callable has STARTED running — the engine's generation
        # pipeline polls this Event at every safe checkpoint (before the
        # model call, etc.) so a STOP actually stops queued work instead
        # of silently running every remaining part to completion.
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def submit_generation(self, func: Callable, *args, **kwargs) -> Future:
        """Submit a generation task. Only one generation runs at a time.

        If a generation is already running, the returned Future will fail
        with a RuntimeError when accessed.

        Returns a Future that the UI can poll or attach a callback to.
        """
        with self._gen_lock:
            if self._current_generation is not None and \
               not self._current_generation.done():
                raise RuntimeError("A generation is already running.")

            # P3.30(d): a fresh submission clears any stale cancel flag —
            # a previous STOP must not cancel the NEXT generation.
            self._cancel_event.clear()
            future = self._gen_executor.submit(func, *args, **kwargs)
            self._current_generation = future
            self._event_bus.emit(Event(EventType.GENERATION_STARTED))
            return future

    def cancel_generation(self) -> bool:
        """Attempt to cancel the current generation.

        P3.30(d) two-layer cancellation:
          1. ``Future.cancel()`` — works when the callable has NOT yet
             started (queued on the worker).
          2. The cooperative ``_cancel_event`` — checked by the engine's
             generation pipeline at every safe checkpoint (entry, before
             the model call). Once inside the native ``generate_speech``
             call there is no interruption hook (verified against the
             model documentation: no stopping criteria / streamer API),
             so that call runs out and its audio is kept.

        Returns True when no generation is running; otherwise the
        Future.cancel() verdict (True = cancelled before start, and the
        cooperative flag is ALWAYS set for the running pipeline).
        """
        self._cancel_event.set()
        with self._gen_lock:
            if self._current_generation is None:
                return True
            return self._current_generation.cancel()

    def cancel_requested(self) -> bool:
        """P3.30(d): is a cooperative cancel pending for the current
        generation pipeline? Safe to poll from the worker thread."""
        return self._cancel_event.is_set()

    @property
    def is_generating(self) -> bool:
        with self._gen_lock:
            return (self._current_generation is not None and
                    not self._current_generation.done())

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------
    def submit_background(self, func: Callable, *args, **kwargs) -> Future:
        """Submit a background task (history write, file indexing, etc.)."""
        return self._bg_executor.submit(func, *args, **kwargs)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        """Shut down the pool. Call during application shutdown."""
        logger.info("Shutting down worker pool ...")
        self._gen_executor.shutdown(wait=False)
        self._bg_executor.shutdown(wait=False)
