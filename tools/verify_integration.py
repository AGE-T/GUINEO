#!/usr/bin/env python3
"""
SpeechStudio Integration Verification
=====================================

Runs a tiny end-to-end smoke test that exercises:

    1. PresetManager save / list / apply (via Preset dataclass).
    2. BatchManager queue + stop semantics (the documented §14
       contract: stopping an ACTIVE execution run — in-flight member
       and pending members become SKIPPED, never-run non-members stay
       PENDING — plus the documented idle-stop no-op) + duration source.
    3. Pipeline SHA-256 logging markers (static check).

This script does NOT require PySide6 or the actual Higgs model; it
uses fake collaborators for the engine.

Exit code 0 = all checks pass; 1 = at least one failure.

Usage:
    python3 tools/verify_integration.py [--root PATH]
"""

from __future__ import annotations
import argparse
import os
import sys
import tempfile
from concurrent.futures import Future
from typing import List


def _ensure_root_on_path(root: str) -> None:
    if root not in sys.path:
        sys.path.insert(0, root)


def _check_preset_round_trip(root: str) -> List[str]:
    """Save a preset, reload, verify all fields survive."""
    _ensure_root_on_path(root)
    try:
        from engine.preset_manager import Preset, PresetManager
        from engine.models import GenerationParameters
    except ImportError as exc:
        return ["cannot import preset_manager: {0}".format(exc)]

    violations: List[str] = []
    with tempfile.TemporaryDirectory() as d:
        pm = PresetManager(d)
        original = Preset(
            name="Test",
            voice_id="v1",
            emotion="Awe",
            style="Narration",
            speed="Slow",
            pitch="Low",
            delivery="Expressive High",
            parameters=GenerationParameters(
                temperature=1.3, top_p=0.95, top_k=300,
                max_new_tokens=2048, seed=42,
                append_silence=0.5, normalize_output=True,
                auto_play=False, output_format="wav"),
        )
        try:
            pm.save(original)
        except Exception as exc:
            return ["PresetManager.save raised: {0}".format(exc)]

        # Re-instantiate to force a disk reload
        pm2 = PresetManager(d)
        presets = pm2.list()
        if len(presets) != 1:
            violations.append(
                "expected 1 preset, got {0}".format(len(presets)))
            return violations
        loaded = presets[0]
        for attr in ("name", "voice_id", "emotion", "style", "speed",
                     "pitch", "delivery"):
            if getattr(loaded, attr) != getattr(original, attr):
                violations.append(
                    "field '{0}' mismatch: {1} != {2}".format(
                        attr, getattr(loaded, attr),
                        getattr(original, attr)))
        if loaded.parameters.seed != 42:
            violations.append(
                "seed mismatch: {0} != 42".format(loaded.parameters.seed))
        if not loaded.parameters.normalize_output:
            violations.append("normalize_output did not survive round-trip")

    return violations


def _check_batch_manager(root: str) -> List[str]:
    """Verify retry mechanism + stop semantics + duration source."""
    _ensure_root_on_path(root)
    try:
        from engine.batch_manager import (
            BatchManager, BatchJob, JobStatus,
        )
        from engine.models import GenerationResult, GenerationParameters
    except ImportError as exc:
        return ["cannot import batch_manager: {0}".format(exc)]

    violations: List[str] = []

    # --- to_request includes output_filename ---
    job = BatchJob(name="j1", prompt="hello",
                   output_filename="out.wav",
                   parameters=GenerationParameters())
    req = job.to_request()
    if req.output_filename != "out.wav":
        violations.append(
            "to_request() lost output_filename: {0}".format(
                req.output_filename))
    if req.text != "hello":
        violations.append(
            "to_request() lost prompt text: {0}".format(req.text))

    # --- §14 stop contract: stopping an ACTIVE execution run ---
    # P3.45.5 correction: the old scenario called stop() on a manager
    # that had NEVER STARTED and expected SKIPPED — a pre-P3.44.4
    # expectation contradicting the documented contract (an idle stop
    # is a no-op; never-run jobs stay PENDING; the tool has been red
    # since the repository's first commit because of it). The intended
    # coverage — "stop cancels queued work" — is preserved by
    # establishing the ACTIVE state first, and the idle behaviour is
    # now covered explicitly below.
    calls = {"n": 0}

    def pending_submit(request):
        calls["n"] += 1
        f: Future = Future()
        # Never completes: the job stays in flight exactly like a real
        # generation inside generate_speech() (deterministic, no Qt
        # timer and no event loop needed).
        return f

    mgr = BatchManager(
        submit_fn=pending_submit,
        cancel_fn=lambda: True,
        marshal_to_ui=lambda fn: fn(),
    )
    mgr.add_job(BatchJob(name="jA", prompt="hello"))   # in-flight member
    mgr.add_job(BatchJob(name="jB", prompt="world"))   # pending member
    mgr.add_job(BatchJob(name="jC", prompt="idle"))    # never-run non-member
    mgr.start(only=[0, 1])
    if mgr.jobs[0].status != JobStatus.GENERATING:
        violations.append(
            "after start(only=[0,1]), job jA status={0} (expected "
            "GENERATING — the run must be active before stop())".format(
                mgr.jobs[0].status))
    if mgr.jobs[2].status != JobStatus.PENDING:
        violations.append(
            "job jC must stay PENDING (never part of the run)")

    mgr.stop()
    stopped = [j.status for j in mgr.jobs]
    # §14: A (in flight, member) -> Stopped; B (member, pending) ->
    # Stopped; C (never ran, not a member) -> Pending UNTOUCHED.
    if stopped[0] != JobStatus.SKIPPED:
        violations.append(
            "after stop(), in-flight member jA status={0} (expected "
            "SKIPPED)".format(stopped[0]))
    if stopped[1] != JobStatus.SKIPPED:
        violations.append(
            "after stop(), pending member jB status={0} (expected "
            "SKIPPED)".format(stopped[1]))
    if stopped[2] != JobStatus.PENDING:
        violations.append(
            "after stop(), never-run job jC status={0} (expected "
            "PENDING — a stop of a run must not touch non-members)"
            .format(stopped[2]))

    # --- documented idle-stop behaviour: a no-op ---
    # stop() on a manager that was NEVER started changes nothing: the
    # jobs stay PENDING and a stale stop flag is cleared (§10).
    mgr_idle = BatchManager(
        submit_fn=pending_submit,
        cancel_fn=lambda: True,
        marshal_to_ui=lambda fn: fn(),
    )
    mgr_idle.add_job(BatchJob(name="j1", prompt="hello"))
    mgr_idle.add_job(BatchJob(name="j2", prompt="world"))
    mgr_idle.stop()
    for j in mgr_idle.jobs:
        if j.status != JobStatus.PENDING:
            violations.append(
                "idle stop(): job {0} status={1} (expected PENDING — "
                "stop on a never-started manager is a documented no-op)"
                .format(j.name, j.status))
            break
    if mgr_idle.is_running:
        violations.append("idle stop(): manager must not be running")

    # --- output_duration comes from result.output_duration (audio length) ---
    # We can't easily run start() without Qt's QTimer for the inter-job
    # delay, but we can call _on_job_done directly with a constructed
    # Future to verify the field assignment logic.
    #
    # NOTE: The current BatchManager API uses _on_job_done(future), which
    # calls future.result() internally. The old API was _do_job_done(job,
    # result) — that method no longer exists. This test was updated to
    # match the current API.
    mgr2 = BatchManager(
        submit_fn=pending_submit,
        cancel_fn=lambda: True,
        marshal_to_ui=lambda fn: fn(),
    )
    test_job = BatchJob(name="j1", prompt="hello")
    mgr2.add_job(test_job)
    # Simulate the manager having "started" the job.
    with mgr2._lock:  # noqa: SLF001
        mgr2._current_index = 0  # noqa: SLF001
        test_job.status = JobStatus.GENERATING
    fake_result = GenerationResult(
        success=True,
        output_path="outputs/y.wav",
        output_duration=12.34,   # audio length
        generation_time=3.45,    # wall clock - must NOT be used
    )
    # Construct a Future that has already completed with fake_result,
    # then call _on_job_done(future) — matching the real callback path.
    fake_future: Future = Future()
    fake_future.set_result(fake_result)
    mgr2._on_job_done(fake_future)  # noqa: SLF001
    if test_job.output_duration != 12.34:
        violations.append(
            "output_duration should be 12.34 (audio length), got {0}".format(
                test_job.output_duration))
    if test_job.generation_time != 3.45:
        violations.append(
            "generation_time should be 3.45 (wall clock), got {0}".format(
                test_job.generation_time))

    return violations


def _check_pipeline_markers(root: str) -> List[str]:
    """Static check: each pipeline stage logs SHA-256."""
    violations: List[str] = []
    expected = [
        ("ui/main_window.py", "Stage 1"),
        ("ui/main_window.py", "Stage 2"),
        ("engine/engine.py", "Stage 3"),
        ("engine/generation_manager.py", "Stage 4/5"),
    ]
    for rel, marker in expected:
        path = os.path.join(root, rel)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            violations.append("{0}: cannot read".format(rel))
            continue
        if marker not in src:
            violations.append(
                "{0}: missing SHA-256 marker '{1}'".format(rel, marker))
        if "sha256" not in src.lower():
            violations.append(
                "{0}: no sha256() call found".format(rel))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None)
    args = parser.parse_args()

    if args.root is None:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(here)
    else:
        root = os.path.abspath(args.root)

    print("Running integration smoke tests for:", root)

    all_violations: List[str] = []
    all_violations.extend(_check_preset_round_trip(root))
    all_violations.extend(_check_batch_manager(root))
    all_violations.extend(_check_pipeline_markers(root))

    if all_violations:
        print("\nFAILED: {0} violation(s):\n".format(len(all_violations)))
        for v in all_violations:
            print("  - {0}".format(v))
        return 1

    print("PASS: all integration smoke tests succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
