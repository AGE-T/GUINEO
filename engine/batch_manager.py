"""
SpeechStudio Engine - Batch Manager
====================================

Manages queueing, sequencing, and tracking of multiple generation jobs.

Key responsibilities:
    - Hold an ordered list of BatchJob objects (the queue).
    - Drive the engine through them one at a time with a small delay
      between jobs.
    - Recover from transient "already running" errors by retrying after
      a short timeout.
    - Marshal status updates back to the UI thread via a single
      ``marshal_to_ui`` callable (the UI side sets it to
      ``QTimer.singleShot(0, fn)``).
    - Persist the queue to YAML and reload it.

The BatchManager is engine-agnostic: it accepts a callable
``submit_fn(request) -> Future`` and a callable
``cancel_fn() -> bool`` rather than importing the Engine directly.  This
keeps the dependency arrow one-way (engine -> batch_manager) and makes
testing trivial.

AI Development Rules section 11:
    Long-running operations never execute on the UI thread.  All callbacks
    fired by the BatchManager are marshalled through ``marshal_to_ui`` so
    the UI never touches batch state from a worker thread.
"""

from __future__ import annotations
import enum
import threading
import time
import os
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from engine.models import GenerationRequest, GenerationParameters, GenerationResult
from engine.logger import get_logger

logger = get_logger("batch")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ts() -> str:
    """Return a timestamp + thread-name prefix used in batch log messages."""
    return "{0} [{1}]".format(
        time.strftime("%Y-%m-%d %H:%M:%S"),
        threading.current_thread().name,
    )


# ---------------------------------------------------------------------------
# Job status enum
# ---------------------------------------------------------------------------
class JobStatus(enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# BatchJob
# ---------------------------------------------------------------------------
def _opt_str(value: Any) -> Optional[str]:
    """Tolerant Optional[str] coercion (P3.28 §26 JSON robustness)."""
    return value if isinstance(value, str) else None


def _opt_int(value: Any) -> Optional[int]:
    """Tolerant Optional[int] coercion (P3.28 §26 JSON robustness)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


@dataclass
class BatchJob:
    """A single queued generation.

    Attributes:
        name: human-readable label.
        prompt: the full prompt text (may include Higgs tokens).
        voice_id: voice profile id, or None.
        output_filename: desired output filename, or None (= auto).
        parameters: full GenerationParameters dataclass.
        status: current JobStatus.
        error: human-readable error message, or None.
        output_path: relative path of the saved WAV (set on success).
        output_duration: duration of the saved audio (seconds).
        generation_time: wall-clock time the model took (seconds).
        started_at, finished_at: epoch seconds (or None).
    """

    name: str = ""
    prompt: str = ""
    voice_id: Optional[str] = None
    output_filename: Optional[str] = None
    parameters: GenerationParameters = field(default_factory=GenerationParameters)
    project: str = "Default"
    status: JobStatus = JobStatus.PENDING
    error: Optional[str] = None
    output_path: Optional[str] = None
    output_duration: float = 0.0
    generation_time: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    # Multi-speaker dialogue: the speaker label (e.g. "CAPTAIN") this job
    # belongs to. Empty/None for single-speaker narration. This is the
    # authoritative source for dialogue detection (not a string heuristic).
    speaker: Optional[str] = None
    # C2 FIX: Scene/Character context fields so they survive the
    # BatchJob → GenerationRequest → Engine → Result → AudioAsset/History
    # chain. Previously these were dropped in to_request(), causing every
    # Generate Long HistoryEntry to have scene_id=None, character_id=None.
    scene_id: Optional[str] = None
    scene_name: Optional[str] = None
    character_id: Optional[str] = None
    # P3.27B: Long Generation part provenance + expected duration (from
    # the splitter's SplitPart.estimated_duration) + the output-guard
    # verdict captured from the GenerationResult on completion. part_index
    # also marks a job as a Generate Long part for versioned regeneration
    # (manual batch jobs keep the legacy overwrite-in-place behaviour).
    part_index: Optional[int] = None
    part_version: Optional[int] = None
    expected_duration: Optional[float] = None
    anomaly: Optional[Dict[str, Any]] = None
    # P3.28: provenance — the source Narration Block (from
    # SplitPart.source_block_id; closes defect D-5: the splitter
    # populated it but BatchJob creation dropped it) and the scene-scoped
    # generation run id ("{scene8}-r{NNN}", allocated at batch start).
    source_block_id: Optional[str] = None
    generation_run: Optional[str] = None
    # P3.28: the slot id this job generates ("{block_id}:{part_of_block}"
    # or "plain:{part_index}") — the join key between the batch queue and
    # the Scene's expected_audio_slots / audio_assets.
    slot_id: Optional[str] = None

    # ------------------------------------------------------------------
    # (De)serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "prompt": self.prompt,
            "voice_id": self.voice_id,
            "output_filename": self.output_filename,
            "parameters": self.parameters.to_dict(),
            "status": self.status.value,
            "error": self.error,
            "output_path": self.output_path,
            "output_duration": self.output_duration,
            "generation_time": self.generation_time,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "speaker": self.speaker,
            "project": self.project,
            # C2 FIX: persist scene/character context
            "scene_id": self.scene_id,
            "scene_name": self.scene_name,
            "character_id": self.character_id,
            # P3.27B: part provenance + output-guard verdict
            "part_index": self.part_index,
            "part_version": self.part_version,
            "expected_duration": self.expected_duration,
            "anomaly": self.anomaly,
            # P3.28: provenance (block + run + slot)
            "source_block_id": self.source_block_id,
            "generation_run": self.generation_run,
            "slot_id": self.slot_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BatchJob":
        params_data = data.get("parameters", {})
        if isinstance(params_data, GenerationParameters):
            params = params_data
        else:
            params = GenerationParameters.from_dict(params_data or {})
        try:
            status = JobStatus(data.get("status", "pending"))
        except ValueError:
            status = JobStatus.PENDING
        return cls(
            name=str(data.get("name", "")),
            prompt=str(data.get("prompt", "")),
            voice_id=data.get("voice_id"),
            output_filename=data.get("output_filename"),
            parameters=params,
            status=status,
            error=data.get("error"),
            output_path=data.get("output_path"),
            output_duration=float(data.get("output_duration", 0.0) or 0.0),
            generation_time=float(data.get("generation_time", 0.0) or 0.0),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            speaker=data.get("speaker"),
            project=str(data.get("project", "Default")),
            # C2 FIX: restore scene/character context
            scene_id=data.get("scene_id"),
            scene_name=data.get("scene_name"),
            character_id=data.get("character_id"),
            # P3.27B: restore part provenance + anomaly (tolerant defaults)
            part_index=_opt_int(data.get("part_index")),
            part_version=_opt_int(data.get("part_version")),
            expected_duration=data.get("expected_duration")
            if isinstance(data.get("expected_duration"), (int, float)) else None,
            anomaly=data.get("anomaly") if isinstance(
                data.get("anomaly"), dict) else None,
            # P3.28: restore provenance (tolerant defaults — P3.28 §26)
            source_block_id=_opt_str(data.get("source_block_id")),
            generation_run=_opt_str(data.get("generation_run")),
            slot_id=_opt_str(data.get("slot_id")),
        )

    def to_request(self) -> GenerationRequest:
        """Build a fresh GenerationRequest from this job's settings.

        The ``status``/``output_*`` fields are NOT included: a fresh
        GenerationRequest is purely an input to the Engine.

        C2 FIX: now forwards speaker, scene_id, scene_name, character_id
        so they survive into the GenerationResult → AudioAsset / HistoryEntry.

        P3.27B: also forwards part_index/part_version/expected_duration so
        the output guard can compare the generated duration against the
        text-based estimate and History can record part provenance.

        P3.28: also forwards block_id/generation_run (provenance
        passthrough — design record Rec 23) so the single registration
        writer can tag the AudioAsset with its block and run.
        """
        return GenerationRequest(
            text=self.prompt,
            voice_id=self.voice_id,
            parameters=self.parameters,
            output_filename=self.output_filename,
            project=self.project,
            speaker=self.speaker,
            scene_id=self.scene_id,
            scene_name=self.scene_name,
            character_id=self.character_id,
            part_index=self.part_index,
            part_version=self.part_version,
            expected_duration=self.expected_duration,
            block_id=self.source_block_id,
            generation_run=self.generation_run,
        )


# ---------------------------------------------------------------------------
# BatchSummary
# ---------------------------------------------------------------------------
@dataclass
class BatchSummary:
    """Aggregate statistics for a finished (or stopped) batch run."""

    total: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    total_audio_seconds: float = 0.0
    total_wall_seconds: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    @property
    def is_finished(self) -> bool:
        return (self.completed + self.failed + self.skipped) >= self.total \
            and self.total > 0

    @property
    def duration_seconds(self) -> float:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return 0.0

    @property
    def realtime_factor(self) -> float:
        if self.total_audio_seconds <= 0:
            return 0.0
        return (self.duration_seconds / self.total_audio_seconds
                if self.duration_seconds > 0 else 0.0)


# ---------------------------------------------------------------------------
# BatchManager
# ---------------------------------------------------------------------------
class BatchManager:
    """Coordinates sequential execution of multiple generation jobs.

    The manager is fully thread-safe: the UI thread reads ``jobs`` and
    calls ``start``/``pause``/``stop``; the engine worker fires future
    callbacks from its own thread.  All UI-facing callbacks are
    marshalled through ``marshal_to_ui`` so the UI never touches batch
    state directly from a worker thread.
    """

    # Inter-job delay (milliseconds) - prevents back-to-back GPU stress.
    INTER_JOB_DELAY_MS = 500
    # Retry delay when the engine reports "already running".
    RETRY_DELAY_MS = 300
    # Maximum retry attempts before a job is marked FAILED.
    MAX_RETRIES = 10

    def __init__(
        self,
        submit_fn: Callable[[GenerationRequest], Any],
        cancel_fn: Optional[Callable[[], bool]] = None,
        marshal_to_ui: Optional[Callable[[Callable[[], None]], None]] = None,
    ):
        self._submit_fn = submit_fn
        self._cancel_fn = cancel_fn
        self.marshal_to_ui = marshal_to_ui or (lambda fn: fn())

        self._jobs: List[BatchJob] = []
        self._lock = threading.RLock()
        self._running = False
        self._paused = False
        self._stop_requested = False
        # P3.44.4 (execution run definition): the exact job OBJECTS that
        # belong to the CURRENT execution run. ``None`` = the whole queue
        # (the established full-batch semantics: every PENDING job runs,
        # Stop flips every PENDING job). A list = a SELECTIVE run (Scene
        # "GENERATE CHECKED", per-row regen): only member jobs are
        # scheduled by ``_process_next`` and only member jobs are flipped
        # SKIPPED by ``stop()`` — a job that merely sits in the table
        # (PENDING, never selected) is NOT part of the run and must never
        # change state because another job was generated or stopped.
        # Transient execution state only: never persisted, cleared by
        # ``_finish_batch_locked`` (this is the explicit run definition —
        # not a second copy of the queue; the job objects are shared).
        self._run_jobs: Optional[List[BatchJob]] = None
        self._current_index: Optional[int] = None
        # P3.44.1: the in-flight job OBJECT (identity bookkeeping). The
        # index alone breaks when the queue is mutated mid-flight
        # (clear_all/remove while a generation runs): the completion
        # landed on whatever job NOW occupies the old index — a job that
        # never ran showed "Done" with the wrong output (runtime-
        # verified cross-contamination). ``_current_index`` is kept for
        # the legacy/test paths; ``current_index`` resolves by identity
        # first.
        self._current_job: Optional[BatchJob] = None
        # P3.44.1: queue hand-over flag. ``start()`` called while the
        # in-flight job is orphaned (queue replaced) accepts the start
        # and sets this; when the old call resolves, its result is
        # DROPPED and the NEW queue's PENDING jobs proceed.
        self._restart_after_current = False
        self._summary: BatchSummary = BatchSummary()
        self._retry_counts: Dict[int, int] = {}
        # P3.44.1: MULTIPLE change listeners (the old single callback
        # slot meant a second Batch dialog silently stole the first
        # dialog's updates — the architectural concern in the spec §4).
        self._on_changed_listeners: List[Callable[["BatchManager"], None]] = []

    # ------------------------------------------------------------------
    # Properties (read-only snapshots for the UI)
    # ------------------------------------------------------------------
    @property
    def jobs(self) -> List[BatchJob]:
        with self._lock:
            return list(self._jobs)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def current_index(self) -> Optional[int]:
        """The AUTHORITATIVE active job row (P3.44.1 §3).

        Resolved by identity from ``_current_job``: the actual job the
        manager submitted — immune to queue reordering/replacement. An
        orphaned in-flight job (removed from the queue) reports None
        (no active row). Falls back to the legacy index field for the
        test/legacy paths that never set ``_current_job``.
        """
        with self._lock:
            job = self._current_job
            if job is not None:
                for i, j in enumerate(self._jobs):
                    if j is job:
                        return i
                return None  # orphaned in-flight job — no active row
            return self._current_index

    @property
    def summary(self) -> BatchSummary:
        with self._lock:
            return self._summary

    # ------------------------------------------------------------------
    # Queue manipulation (called from the UI thread)
    # ------------------------------------------------------------------
    def add_job(self, job: BatchJob) -> int:
        """Append ``job`` to the queue.  Returns its new index."""
        with self._lock:
            self._jobs.append(job)
            return len(self._jobs) - 1

    def remove_job(self, index: int) -> None:
        with self._lock:
            if 0 <= index < len(self._jobs):
                del self._jobs[index]

    def move_job(self, src: int, dst: int) -> None:
        with self._lock:
            if 0 <= src < len(self._jobs) and 0 <= dst < len(self._jobs):
                job = self._jobs.pop(src)
                self._jobs.insert(dst, job)

    def duplicate_job(self, index: int) -> Optional[int]:
        with self._lock:
            if 0 <= index < len(self._jobs):
                src = self._jobs[index]
                clone = BatchJob(
                    name="{0} (copy)".format(src.name),
                    prompt=src.prompt,
                    voice_id=src.voice_id,
                    output_filename=src.output_filename,
                    parameters=GenerationParameters.from_dict(
                        src.parameters.to_dict()),
                    # P3.23: preserve the scene/speaker/character context so
                    # the duplicated part keeps the same History lineage as
                    # the original (design record §22).
                    project=src.project,
                    speaker=src.speaker,
                    scene_id=src.scene_id,
                    scene_name=src.scene_name,
                    character_id=src.character_id,
                )
                self._jobs.insert(index + 1, clone)
                return index + 1
            return None

    def clear_finished(self) -> None:
        """Remove all COMPLETED/FAILED/SKIPPED jobs."""
        with self._lock:
            self._jobs = [
                j for j in self._jobs
                if j.status in (JobStatus.PENDING, JobStatus.GENERATING)
            ]

    def clear_all(self) -> None:
        with self._lock:
            self._jobs.clear()
            self._retry_counts.clear()

    def regen_job(self, index: int) -> bool:
        """Reset a single job to PENDING so it will be re-processed.

        CRITICAL for concatenation order: this method preserves the job's
        position in the queue and its ``output_filename``. The regenerated
        audio overwrites the same file, so when the batch finishes and
        concatenation runs, the part ordering is unchanged.

        Use :meth:`start` with ``reset_failed=False`` afterwards to process
        ONLY the reset job(s) without touching other FAILED/SKIPPED jobs.

        Args:
            index: the job's position in the queue.

        Returns:
            True if the job was reset, False if the index is invalid, the
            job is currently GENERATING, or already PENDING.
        """
        with self._lock:
            if index < 0 or index >= len(self._jobs):
                return False
            job = self._jobs[index]
            if job.status == JobStatus.GENERATING:
                return False  # cannot regen a job that's currently running
            if job.status == JobStatus.PENDING:
                return False  # already pending, nothing to do
            # Reset to PENDING — but KEEP output_filename and position!
            job.status = JobStatus.PENDING
            job.error = None
            job.output_path = None
            job.output_duration = 0.0
            job.generation_time = 0.0
            job.started_at = None
            job.finished_at = None
            # P3.27B: also clear the previous run's output-guard verdict so
            # the regenerated part starts from a clean state (the verdict is
            # re-captured from the new GenerationResult on completion).
            job.anomaly = None
            logger.info("regen_job: reset job %d '%s' to PENDING "
                        "(output_filename preserved: %s)",
                        index, job.name, job.output_filename)
            return True

    # ------------------------------------------------------------------
    # YAML persistence
    # ------------------------------------------------------------------
    def save_to_file(self, path: str) -> str:
        """Persist the job queue to ``path`` as YAML (or JSON fallback).

        Returns the absolute path that was written.
        """
        with self._lock:
            payload = {
                "schema_version": 1,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "jobs": [j.to_dict() for j in self._jobs],
            }
        try:
            import yaml  # type: ignore
            text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
            ext = ".yaml"
        except ImportError:
            text = json.dumps(payload, indent=2, ensure_ascii=False)
            ext = ""

        if not path.endswith((".yaml", ".yml", ".json")):
            path = path + ext
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def load_from_file(self, path: str, replace: bool = True) -> int:
        """Load jobs from ``path``.  Returns the number of jobs loaded.

        ``status`` is always reset to PENDING when loading (a saved
        COMPLETED job is not re-executed by ``start`` unless the user
        resets it).
        """
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()

        if path.endswith(".json"):
            import json as _json
            data = _json.loads(text)
        else:
            try:
                import yaml  # type: ignore
                data = yaml.safe_load(text)
            except ImportError:
                import json as _json
                data = _json.loads(text)

        jobs_data = data.get("jobs", []) if isinstance(data, dict) else []
        jobs: List[BatchJob] = []
        for jd in jobs_data:
            if not isinstance(jd, dict):
                continue
            job = BatchJob.from_dict(jd)
            # Reset transient state on load.
            job.status = JobStatus.PENDING
            job.error = None
            job.output_path = None
            job.output_duration = 0.0
            job.generation_time = 0.0
            job.started_at = None
            job.finished_at = None
            jobs.append(job)

        with self._lock:
            if replace:
                self._jobs = jobs
            else:
                self._jobs.extend(jobs)
            self._retry_counts.clear()
        return len(jobs)

    # ------------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------------
    def start(self, reset_failed: bool = True, only=None) -> bool:
        """Begin processing the queue.  Returns False if already running.

        Args:
            reset_failed: if True (default), reset all SKIPPED/FAILED jobs
                back to PENDING so a full re-start is possible. Set to False
                when regenerating specific jobs via :meth:`regen_job` so only
                the explicitly-reset jobs are re-processed.
            only: P3.44.4 — optional iterable of queue INDICES defining the
                EXECUTION RUN (Scene selective generation / per-row regen).
                ``None`` (default) = the whole queue is the run: every
                PENDING job executes and Stop flips every PENDING job (the
                established manual/full-batch semantics, unchanged). With
                ``only``, jobs NOT in the list keep their state untouched:
                they are never scheduled and never flipped SKIPPED by a
                Stop of this run.

        P3.44.1 (queue hand-over): when the queue was REPLACED while a
        generation is still in flight (the previous job object is no
        longer in ``jobs`` — e.g. a manual batch was running when Long
        Generation cleared the queue), the start is ACCEPTED: the old
        in-flight call runs out (its result is DROPPED — identity guard
        in ``_on_job_done``), then the new queue's PENDING jobs run.
        The old behaviour returned False here, leaving the new jobs
        pending forever (or, before the identity guard, contaminating
        the wrong row with the old result).

        P3.44.2 (pause resume): START while the run is PAUSED is a
        RESUME (the Start button is the only enabled control while
        paused — see the runtime note in the body).
        """
        with self._lock:
            run_members = self._resolve_run_members_locked(only)
            if self._running:
                # P3.44.2 — START while PAUSED is a RESUME request.
                # The Start button is the only resume control the dialog
                # offers (the Pause button disables itself while paused
                # and manager.resume() has no other UI caller), so this
                # must accept the start and clear the pause. Runtime-
                # proven defect this closes: start() previously returned
                # False here, _on_start showed a blocking "Already
                # Running" modal, and a PAUSED batch could never be
                # resumed from the UI at all.
                if self._paused and not self._stop_requested:
                    self.resume()  # same RLock — same resume semantics
                    logger.info("Batch START while paused -> resumed")
                    return True
                in_flight = self._current_job
                handover = (in_flight is not None
                            and not any(j is in_flight for j in self._jobs))
                if not handover:
                    return False
                # Queue hand-over: accept the start. The old call's
                # completion routes through _on_job_done's identity
                # guard: result dropped, then _post_job_ui continues
                # with the NEW queue.
                self._restart_after_current = True
                self._stop_requested = False
                self._paused = False
                self._run_jobs = run_members
                if reset_failed:
                    for j in self._jobs:
                        if j.status in (JobStatus.SKIPPED, JobStatus.FAILED):
                            j.status = JobStatus.PENDING
                            j.error = None
                self._summary = BatchSummary(
                    total=sum(1 for j in self._jobs),
                    started_at=time.time(),
                )
                pending_count = sum(
                    1 for j in self._jobs if j.status == JobStatus.PENDING)
                logger.info(
                    "Batch START (hand-over): %d jobs (%d pending) — old "
                    "in-flight call runs out first", len(self._jobs),
                    pending_count)
                return True
            # Reset SKIPPED/FAILED back to PENDING so a re-start is possible.
            if reset_failed:
                for j in self._jobs:
                    if j.status in (JobStatus.SKIPPED, JobStatus.FAILED):
                        j.status = JobStatus.PENDING
                        j.error = None
            self._running = True
            self._paused = False
            self._stop_requested = False
            self._restart_after_current = False
            # P3.44.4: capture the EXECUTION RUN (job objects; None =
            # whole queue). Everything the run does — scheduling and
            # Stop — is scoped to these jobs from this point on.
            self._run_jobs = run_members
            self._summary = BatchSummary(
                total=sum(1 for j in self._jobs),
                started_at=time.time(),
            )
            pending_count = sum(1 for j in self._jobs
                                if j.status == JobStatus.PENDING)
            logger.info("Batch START: %d jobs (%d pending, run=%s)",
                        len(self._jobs), pending_count,
                        "whole queue" if run_members is None
                        else "{0} selected".format(len(run_members)))
        # P3.44.4 §9 (notification timing): START is a meaningful global
        # transition — emit it so every listener observes running=True
        # immediately (not only when the first job starts).
        self.marshal_to_ui(self._emit_changed)
        # Kick off the first job from the calling (UI) thread.  Subsequent
        # jobs are scheduled via marshal_to_ui.
        self._process_next()
        return True

    def _resolve_run_members_locked(self, only
                                     ) -> Optional[List[BatchJob]]:
        """P3.44.4: resolve the ``only`` indices into job OBJECTS.

        MUST be called under ``self._lock`` (the queue is read). Returns
        ``None`` when ``only`` is None (whole-queue run). Invalid indices
        are dropped; the caller guarantees at least one checked job in
        the UI paths, and an empty explicit run simply finishes
        immediately (an empty execution run is a no-op run, not an
        error). The returned list holds the job objects themselves —
        identity (``is``) is the membership key, never equality.
        """
        if only is None:
            return None
        members: List[BatchJob] = []
        seen: set = set()
        for raw in only:
            try:
                idx = int(raw)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(self._jobs) and idx not in seen:
                seen.add(idx)
                members.append(self._jobs[idx])
        return members

    def pause(self) -> None:
        with self._lock:
            self._paused = True
        # P3.44.4 §9 (notification timing): PAUSE is a meaningful global
        # transition — emit it so controls reflect the paused state even
        # if the caller does not refresh manually.
        self.marshal_to_ui(self._emit_changed)

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            if self._running and self._current_job is None \
                    and self._current_index is None:
                # Restart the queue if we paused between jobs.
                self.marshal_to_ui(self._process_next)
        # P3.44.4 §9: RESUME is a meaningful global transition — emit it.
        self.marshal_to_ui(self._emit_changed)

    def stop(self) -> None:
        """Stop the CURRENT EXECUTION RUN and return the batch to idle.

        P3.30(d) semantics (aligned with the verified model docs):
          - PENDING jobs flip to SKIPPED instantly.
          - The GENERATING job flips to SKIPPED immediately too (the user
            sees the stop); the engine's cooperative cancel aborts it at
            the next safe checkpoint (before the model call). If it was
            already INSIDE the native generate_speech() call — which has
            no interruption hook — the call runs out, and _on_job_done
            RESTORES the job to COMPLETED so completed audio is never
            destroyed by a stop.
          - The next job will not be scheduled.

        P3.44.4 (execution-run scoping): only jobs that BELONG to the
        current run are flipped — every PENDING/GENERATING member of
        ``_run_jobs`` (or, for a whole-queue run, every job in the
        table). A job that merely sits in the table PENDING (never
        selected into this run) is NOT stopped: its state must not
        change merely because another job's run was stopped. The
        in-flight job is by construction a run member.

        P3.44.1 (deterministic idle — the "stuck running" deadlock):
          when NOTHING is in flight (no ``_current_job``/``_current_index``
          — e.g. Stop pressed while the batch is PAUSED between jobs, the
          deadlock reproduction), the batch can never "run out" later, so
          it finishes NOW. Previously ``_running`` stayed True forever:
          the dialog kept showing Pause/Stop active, checkboxes stayed
          disabled, and every later ``start()`` silently returned False.
        """
        with self._lock:
            if not self._running:
                # Idle: nothing to stop. Keep the call harmless (a stale
                # stop flag must not leak into the next run).
                self._stop_requested = False
                self._paused = False
                self._restart_after_current = False
                return
            self._stop_requested = True
            self._restart_after_current = False
            self._paused = False
            run_members = self._run_jobs
            for j in self._jobs:
                if j.status in (JobStatus.PENDING, JobStatus.GENERATING):
                    if run_members is not None and not any(
                            j is m for m in run_members):
                        # P3.44.4: table membership is NOT run membership.
                        # This job never entered the current execution run
                        # — a Stop of that run must not touch it.
                        continue
                    if j.status == JobStatus.GENERATING:
                        # Keep bookkeeping: _on_job_done still needs the
                        # job; the SKIPPED status may be restored to
                        # COMPLETED if the in-flight call finishes with
                        # audio despite the stop.
                        j.status = JobStatus.SKIPPED
                        j.error = "Stopped: generation ended by user."
                    else:
                        j.status = JobStatus.SKIPPED
                        j.error = "Skipped: batch stopped by user."
            if self._cancel_fn is not None:
                try:
                    self._cancel_fn()
                except Exception:
                    pass
            # Update summary immediately so the UI reflects the stop.
            self._recompute_summary_locked()
            in_flight = (self._current_job is not None
                         or self._current_index is not None)
        self.marshal_to_ui(self._emit_changed)
        if not in_flight:
            # Nothing is generating — finish NOW (deterministic idle).
            self._finish_batch()

    # ------------------------------------------------------------------
    # Internal: processing loop
    # ------------------------------------------------------------------
    @property
    def stop_requested(self) -> bool:
        """P3.30(d): has a stop been requested for the running batch?
        (The UI shows a "Stopping…" state while the in-flight model call
        runs out.)"""
        with self._lock:
            return bool(self._stop_requested)

    def _process_next(self) -> None:
        """Find the next runnable job of the CURRENT EXECUTION RUN and
        submit it.  Called on the UI thread (via marshal_to_ui) so it is
        safe to touch Qt-free engine state.

        P3.44.1 (single-flight guarantee): returns IMMEDIATELY when a
        job is already in flight. The queue advances ONLY from the
        in-flight job's completion (_on_job_done -> _post_job_ui ->
        _after_job). Previously a stale inter-job timer (e.g. left over
        from a paused/finished batch, or raced by a hand-over restart)
        could call _process_next while a generation was already
        running: it double-marked the NEXT job GENERATING, its submit
        then hit "already running", and the retry path corrupted the
        in-flight bookkeeping — two rows generating, the real
        completion orphaned, the batch finishing with jobs stuck in
        GENERATING (runtime-traced).

        P3.44.4 (gate ordering + run scoping):
          1. ``not _running`` -> return. An OBSOLETE invocation (a stale
             completion callback, a leftover inter-job timer, a marshalled
             race after the batch already finished) must NEVER start a
             job — previously it could silently launch a PENDING job of a
             run that no longer exists.
          2. The runnable scan is scoped to the EXECUTION RUN
             (``_run_jobs``; None = whole queue). A PENDING job that was
             never selected is NOT runnable by this run.
          3. When the run set is exhausted the batch FINISHES — even if
             PAUSED. A pause pressed while the last member job was in
             flight used to leave the batch "running+paused" forever
             with nothing left to run (Pause/Start/Stop stuck active,
             §3/§4 of the P3.44.4 reproduction). An exhausted run is a
             finished run.
          4. The paused gate now sits BETWEEN the exhaustion check and
             the submit: pause halts the queue only while member jobs
             actually remain.
        """
        logger.info("_process_next: entry (thread=%s)", threading.current_thread().name)
        with self._lock:
            if self._current_job is not None or self._current_index is not None:
                logger.info("_process_next: a job is already in flight "
                            "-> return (no double-scheduling)")
                return
            if not self._running:
                # P3.44.4 §11: an obsolete invocation after the batch
                # finished (stale callback / leftover timer) must have
                # NO effect on the new idle state.
                logger.info("_process_next: not running -> return "
                            "(obsolete invocation ignored)")
                return
            if self._stop_requested:
                logger.info("_process_next: stop_requested -> finish_batch")
                self._finish_batch()
                return

            run_members = self._run_jobs
            next_idx: Optional[int] = None
            for i, j in enumerate(self._jobs):
                if j.status != JobStatus.PENDING:
                    continue
                if run_members is not None and not any(
                        j is m for m in run_members):
                    # P3.44.4: not a member of the current execution run
                    # — never scheduled by this run.
                    continue
                next_idx = i
                break

            if next_idx is None:
                # The execution run is exhausted (no member PENDING job
                # remains). Finish NOW — even while PAUSED: a pause can
                # only halt a run that still has work (see docstring 3).
                logger.info("_process_next: no runnable member jobs -> "
                            "finish_batch (paused=%s)", self._paused)
                self._finish_batch()
                return

            if self._paused:
                logger.info("_process_next: paused (members remain) -> return")
                return

            job = self._jobs[next_idx]
            job.status = JobStatus.GENERATING
            job.started_at = time.time()
            job.error = None
            self._current_index = next_idx
            self._current_job = job
            logger.info("_process_next: starting job %d '%s'", next_idx, job.name)
            self.marshal_to_ui(self._emit_changed)

        # Build the request outside the lock (the Engine may take a
        # moment to accept it).
        # P3.30(d): a malformed job (to_request failure) or ANY submit
        # exception — not only RuntimeError — is now contained: the job
        # is marked FAILED and the queue CONTINUES. Previously a
        # non-RuntimeError escaped _process_next entirely, leaving the
        # job stuck GENERATING and the batch stuck "running" forever
        # (the "generation gets stuck" defect).
        try:
            request = job.to_request()
        except Exception as exc:
            logger.exception("to_request failed for job %d: %s",
                             next_idx, exc)
            with self._lock:
                job.status = JobStatus.FAILED
                job.error = "Could not build the generation request: " \
                            "{0}".format(exc)
                job.finished_at = time.time()
                # P3.44.1: clear the in-flight bookkeeping ONLY when
                # THIS job is the in-flight one (a concurrent
                # _process_next can no longer race thanks to the
                # single-flight guard, but keep the clearing precise).
                if self._current_job is None or self._current_job is job:
                    self._current_index = None
                    self._current_job = None
                self._recompute_summary_locked()
            self._post_job_ui()
            return

        try:
            future = self._submit_fn(request)
            logger.info("_process_next: submit_fn returned future for job %d", next_idx)
            # The Engine returns a Future; attach our completion callback.
            future.add_done_callback(self._on_job_done)
        except RuntimeError as exc:
            # Engine reports "already running" - retry after a short delay.
            if "already running" in str(exc).lower():
                retries = self._retry_counts.get(next_idx, 0)
                logger.warning("_process_next: RuntimeError 'already running' for job %d (retry %d/%d)",
                               next_idx, retries + 1, self.MAX_RETRIES)
                if retries < self.MAX_RETRIES:
                    self._retry_counts[next_idx] = retries + 1
                    # Revert the job to PENDING so a future _process_next
                    # pass picks it up again.
                    with self._lock:
                        job.status = JobStatus.PENDING
                        job.started_at = None
                        # P3.44.1: clear the in-flight bookkeeping ONLY
                        # when THIS job is the in-flight one — reverting
                        # a job that never submitted must never orphan a
                        # DIFFERENT generation that is actually running
                        # (the traced corruption: two rows generating,
                        # the real completion orphaned).
                        if self._current_job is None \
                                or self._current_job is job:
                            self._current_index = None
                            self._current_job = None
                    # P3.44.1: emit the revert — otherwise the row keeps
                    # showing the GENERATING pill (stale hourglass) while
                    # nothing is generating during the retry delay.
                    self.marshal_to_ui(self._emit_changed)
                    # Schedule a retry on the UI thread.
                    try:
                        # Use Qt's QTimer if available, else fall back to
                        # marshal_to_ui (which on the UI side can be
                        # QTimer.singleShot).
                        from PySide6.QtCore import QTimer
                        QTimer.singleShot(self.RETRY_DELAY_MS,
                                          self._process_next)
                    except ImportError:
                        self.marshal_to_ui(self._process_next)
                    return
                else:
                    # Too many retries - mark as FAILED.
                    with self._lock:
                        job.status = JobStatus.FAILED
                        job.error = "Engine was busy after {0} retries.".format(
                            self.MAX_RETRIES)
                        job.finished_at = time.time()
                        if self._current_job is None \
                                or self._current_job is job:
                            self._current_index = None
                            self._current_job = None
                        self._recompute_summary_locked()
                    self._post_job_ui()
                    return
            else:
                # Some other RuntimeError - treat as failure.
                with self._lock:
                    job.status = JobStatus.FAILED
                    job.error = str(exc)
                    job.finished_at = time.time()
                    if self._current_job is None \
                            or self._current_job is job:
                        self._current_index = None
                        self._current_job = None
                    self._recompute_summary_locked()
                self._post_job_ui()
                return
        except Exception as exc:
            # P3.30(d): ANY other submit failure (validation, plumbing,
            # unexpected types) — contain it exactly like a RuntimeError:
            # mark FAILED, keep the queue alive. This closes the second
            # stuck-forever path.
            logger.exception("_process_next: submit_fn raised for job %d",
                             next_idx)
            with self._lock:
                job.status = JobStatus.FAILED
                job.error = str(exc)
                job.finished_at = time.time()
                if self._current_job is None or self._current_job is job:
                    self._current_index = None
                    self._current_job = None
                self._recompute_summary_locked()
            self._post_job_ui()
            return

    def _on_job_done(self, future) -> None:
        """Future-done callback (runs on the worker thread).

        We process the result directly here (only touching batch state
        protected by self._lock) and then marshal ONLY the UI update
        + next-job scheduling to the UI thread.

        P3.44.1 (identity guard): the result is applied to the job
        OBJECT that was submitted (``_current_job``). When that object
        is no longer in the queue (the queue was cleared/replaced while
        the call ran — e.g. a manual batch in flight when Long
        Generation rebuilt the queue), the result has NO home: it is
        DROPPED, never attached to whatever job now occupies the old
        index (the runtime-verified cross-contamination: a job that
        never ran showed "Done" with the wrong output). Then the batch
        either continues into the replaced queue (queue hand-over —
        ``start()`` accepted it) or finishes.
        """
        logger.info("_on_job_done: called on thread=%s", threading.current_thread().name)
        # Capture the result before marshalling.
        result: Optional[GenerationResult] = None
        error: Optional[str] = None
        try:
            result = future.result()
            logger.info("_on_job_done: future.result() success=%s",
                        result.success if result else "None")
        except Exception as exc:
            error = str(exc)
            logger.error("_on_job_done: future.result() raised: %s", exc)

        # Find the job and update its state directly (thread-safe via _lock).
        with self._lock:
            # P3.44.1 identity guard: prefer the submitted OBJECT.
            in_flight = self._current_job
            if in_flight is not None and not any(
                    j is in_flight for j in self._jobs):
                # Orphaned in-flight call: its job was removed from the
                # queue mid-flight. The result cannot be attached
                # anywhere — drop it.
                logger.info(
                    "_on_job_done: in-flight job '%s' no longer queued — "
                    "result dropped (queue was replaced)",
                    in_flight.name)
                restart = self._restart_after_current
                self._restart_after_current = False
                self._current_index = None
                self._current_job = None
                if restart:
                    # Queue hand-over (start() accepted on the replaced
                    # queue): keep the batch running; the new queue's
                    # PENDING jobs proceed via the normal pacing.
                    self.marshal_to_ui(self._post_job_ui)
                    return
                # No hand-over: the batch ends here (a replaced queue
                # must never silently auto-run its PENDING jobs — e.g.
                # review mode). _finish_batch marshals the change + the
                # batch-completed callback onto the UI thread.
                self._finish_batch_locked()
                self.marshal_to_ui(self._finish_batch)
                return
            idx = self._current_index
            if in_flight is not None:
                # Resolve the CURRENT index of the submitted object (the
                # queue may have been re-ordered around it).
                idx = next((i for i, j in enumerate(self._jobs)
                            if j is in_flight), idx)
                job = in_flight
            elif idx is None or idx >= len(self._jobs):
                # P3.30(d): an orphan completion (the job's index is
                # gone — e.g. a stop/regen race) must still END the
                # batch. Previously this returned with _running left
                # True forever — the queue looked stuck and nothing
                # (not even Stop) recovered it.
                logger.warning("_on_job_done: _current_index is None — "
                               "finishing batch")
                self._finish_batch_locked()
                self.marshal_to_ui(self._post_job_ui)
                return
            else:
                job = self._jobs[idx]
            job.finished_at = time.time()
            was_stopped = job.status == JobStatus.SKIPPED
            result_cancelled = bool(getattr(result, "cancelled", False)) \
                if result is not None else False
            if error is not None:
                job.status = JobStatus.FAILED
                job.error = error
            elif result is None or not result.success:
                if result_cancelled or was_stopped:
                    # P3.30(d): a cooperative cancel renders as a USER
                    # STOP (SKIPPED), never as a red FAILED row.
                    job.status = JobStatus.SKIPPED
                    job.error = "Stopped by user."
                else:
                    job.status = JobStatus.FAILED
                    if result is not None and result.errors:
                        job.error = "; ".join(
                            e.get("message", str(e)) for e in result.errors)
                    else:
                        job.error = "Generation failed."
            elif was_stopped:
                # P3.30(d): the model call was already inside the native
                # generate_speech() when the user pressed Stop; it ran
                # out and produced audio. A stop never destroys
                # completed work — restore COMPLETED.
                job.status = JobStatus.COMPLETED
                job.error = None
                job.output_path = result.output_path
                job.output_duration = result.output_duration
                job.generation_time = result.generation_time
                job.anomaly = getattr(result, "output_anomaly", None)
                logger.info("_on_job_done: stopped job finished with "
                            "audio anyway -> restored to COMPLETED")
            else:
                job.status = JobStatus.COMPLETED
                job.output_path = result.output_path
                job.output_duration = result.output_duration
                job.generation_time = result.generation_time
                # P3.27B: capture the output-guard verdict (None = normal)
                # so the Batch dialog can flag pathological outputs.
                job.anomaly = getattr(result, "output_anomaly", None)
                job.part_version = getattr(result, "part_version", None) \
                    if getattr(result, "part_version", None) is not None \
                    else job.part_version
            self._current_index = None
            self._current_job = None
            self._recompute_summary_locked()
            logger.info("_on_job_done: job '%s' -> %s", job.name, job.status.value)

        # Marshal the UI update + next-job scheduling to the UI thread.
        # This is the ONLY part that needs to run on the UI thread.
        logger.info("_on_job_done: marshalling UI update + _after_job to UI thread")
        self.marshal_to_ui(self._post_job_ui)

    def _post_job_ui(self) -> None:
        """Run on the UI thread after a job completes: update UI + schedule next."""
        self._emit_changed()
        self._after_job()

    def _after_job(self) -> None:
        """Wait a short delay (INTER_JOB_DELAY_MS) then process the next job."""
        logger.info("_after_job: scheduling _process_next in %dms", self.INTER_JOB_DELAY_MS)
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(self.INTER_JOB_DELAY_MS, self._process_next)
        except ImportError:
            # Without Qt we can't schedule a delay - process immediately.
            self.marshal_to_ui(self._process_next)

    def _finish_batch(self) -> None:
        """Mark the batch as finished and emit a final changed event."""
        logger.info("_finish_batch: called")
        with self._lock:
            summary_snapshot = self._finish_batch_locked()
        self.marshal_to_ui(self._emit_changed)
        # Fire the batch-completed callback (if registered) with a snapshot
        # of the final summary. Used by the Long Narration flow to trigger
        # concatenation of all parts into one WAV.
        cb = self._on_batch_completed
        if cb is not None:
            try:
                cb(summary_snapshot)
            except Exception as exc:
                logger.warning("on_batch_completed callback raised: %s", exc)

    def _finish_batch_locked(self):
        """P3.30(d): the lock-holding core of _finish_batch. Returns the
        final summary snapshot. Callers MUST hold self._lock.

        P3.44.4 §10 (complete transient-state clearing): a finished run
        must leave NO execution state behind — not ``_running`` alone.
        Previously ``_paused``, ``_stop_requested``, ``_restart_after_current``
        and ``_current_job`` SURVIVED a finish: a stopped run kept
        ``stop_requested=True`` while idle (the dialog's "Stopping…"
        presentation never reset cleanly), a paused run kept
        ``_paused=True`` while idle (a later summary rendered "Paused"
        for an idle queue), and a stale ``_current_job`` made the
        single-flight guard swallow a later ``start()`` (accepted, then
        nothing ever ran — Pause/Stop stuck enabled forever, the §3/§4
        reproduction). Every transient field is cleared HERE, before
        the final change event is marshalled to the UI, so the final
        state is observable by the dialog BEFORE the final refresh.
        """
        self._running = False
        self._paused = False
        self._stop_requested = False
        self._restart_after_current = False
        self._current_index = None
        self._current_job = None
        # P3.44.4: the execution run definition is transient — a finished
        # run owns no jobs (the next start() re-derives its own members).
        self._run_jobs = None
        self._summary.finished_at = time.time()
        self._recompute_summary_locked()
        logger.info("_finish_batch: completed=%d failed=%d skipped=%d",
                    self._summary.completed, self._summary.failed,
                    self._summary.skipped)
        return self._summary

    # ------------------------------------------------------------------
    # Summary + events
    # ------------------------------------------------------------------
    def _recompute_summary_locked(self) -> None:
        completed = sum(1 for j in self._jobs if j.status == JobStatus.COMPLETED)
        failed = sum(1 for j in self._jobs if j.status == JobStatus.FAILED)
        skipped = sum(1 for j in self._jobs if j.status == JobStatus.SKIPPED)
        total_audio = sum(j.output_duration for j in self._jobs
                          if j.status == JobStatus.COMPLETED)
        total_wall = sum(j.generation_time for j in self._jobs
                         if j.status == JobStatus.COMPLETED)
        started = self._summary.started_at
        self._summary = BatchSummary(
            total=len(self._jobs),
            completed=completed,
            failed=failed,
            skipped=skipped,
            total_audio_seconds=total_audio,
            total_wall_seconds=total_wall,
            started_at=started,
            finished_at=time.time(),
        )

    def _emit_changed(self) -> None:
        """Notify listeners that the queue state changed.

        P3.44.1: the manager supports MULTIPLE listeners
        (``add_on_changed``/``remove_on_changed``). The old
        single-callback slot meant a second Batch dialog silently
        replaced the first dialog's callback — the first dialog then
        stopped receiving ANY manager updates (stale UI, "dead" table).
        Each listener is isolated: one raising never blocks the others.
        """
        for cb in list(self._on_changed_listeners):
            if cb is None:
                continue
            try:
                cb(self)
            except Exception:
                pass

    def add_on_changed(self, cb: Optional[Callable[["BatchManager"], None]]) -> None:
        """Register an ADDITIONAL change listener (P3.44.1 §4).

        Multiple dialogs (and MainWindow itself) can observe the same
        manager simultaneously; removing one listener never affects the
        others. Duplicate registration of the same callable is a no-op.
        """
        if cb is None:
            return
        with self._lock:
            if not any(existing is cb for existing in self._on_changed_listeners):
                self._on_changed_listeners.append(cb)

    def remove_on_changed(self, cb: Optional[Callable[["BatchManager"], None]]) -> None:
        """Unregister a change listener (identity-based removal)."""
        if cb is None:
            return
        with self._lock:
            self._on_changed_listeners = [
                existing for existing in self._on_changed_listeners
                if existing is not cb]

    def set_on_changed(self, cb: Optional[Callable[["BatchManager"], None]]) -> None:
        """Legacy single-slot registration — REPLACES the listener list.

        Retained for backwards compatibility (the pre-P3.44.1 API).
        New callers should prefer ``add_on_changed``/``remove_on_changed``
        so coexisting observers never overwrite each other.
        """
        with self._lock:
            self._on_changed_listeners = [cb] if cb is not None else []

    @property
    def on_changed_listeners(self) -> List[Callable[["BatchManager"], None]]:
        """Snapshot of the registered change listeners (tests/inspection)."""
        with self._lock:
            return list(self._on_changed_listeners)

    # Optional one-shot callback fired when the ENTIRE batch finishes
    # (all jobs processed). Used by the Long Narration flow to concatenate
    # all parts into a single WAV after generation completes.
    _on_batch_completed: Optional[Callable[["BatchSummary"], None]] = None

    def set_on_batch_completed(
        self, cb: Optional[Callable[["BatchSummary"], None]]) -> None:
        """Register a callback fired once when the whole batch finishes.

        The callback receives a :class:`BatchSummary` snapshot.
        """
        self._on_batch_completed = cb
