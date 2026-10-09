# P3.45.6 — Final Stability and Narration Integrity Closure (Design Record)

Baseline: `main @ ae809ae` (P3.45.5 build commit; `origin/main` equal,
clean tree). Programme: FIVE ordered implementation phases — SS-7 stop
race, Narration Block range integrity, startup Project/Scene
restoration, gutter status layout, Preview/token consistency — each
with focused regressions, frozen-contract revalidation, per-phase
commit; ONE fresh release at the end (`git archive` convention,
separate build commit, push).

Evidence classes used throughout (mandatory separation):
source-inspection verified / deterministic automated test / real Qt
widget reproduction / real application flow / manually verified by
the user / not verifiable in this environment (data or hardware
absent). The user's actual `GUINEO / Leírás` project file was NOT
supplied — no claim is made that the user's saved project data was
repaired or verified; a final manual checklist is preserved for it.

---

## Phase 1 — SS-7: Stop / Cancellation Race

### 1.1 Root cause (source + runtime verified)

Failure sequence (PROVEN by the P3.45.5 logs, 3/30 reproductions;
re-verified by source inspection at `ae809ae`):

1. `BatchManager._process_next` marks the job `GENERATING` and calls
   `submit_fn` → `WorkerPool.submit_generation` →
   `ThreadPoolExecutor.submit` — the task is QUEUED, not started.
2. The user presses Stop. `BatchManager.stop()` flips this run's
   GENERATING members to `SKIPPED` and sets `_stop_requested` under
   the manager lock, then calls `cancel_fn` →
   `WorkerPool.cancel_generation()` → `Future.cancel()` — which
   SUCCEEDS because the task never started.
3. The future completes CANCELLED; the done callback
   `BatchManager._on_job_done` runs (synchronously on the cancelling
   thread; the manager RLock is reentrant so the nested acquisition
   is safe).
4. `future.result()` raises `concurrent.futures.CancelledError`.
   Runtime-verified class facts (Python 3.12.14):
   `concurrent.futures.CancelledError` is its OWN class
   (MRO CancelledError → Error → Exception), NOT
   `asyncio.CancelledError`, and its `str()` is EMPTY.
5. The generic `except Exception` handler set `error = ""` — and
   `if error is not None` is TRUE for the empty string, so the job
   was classified `FAILED` with an empty error BEFORE the
   stop-semantics branch (`result_cancelled or was_stopped`) could
   run. That branch only handles the result-based cooperative cancel
   (`GenerationResult(cancelled=True)`), not the pre-start CANCELLED
   future state.

Authoritative distinguishing information (identified per the phase
brief): the future's CANCELLED **state** (the callable never ran — no
pipeline stage, no model call: it can never be a genuine model or
runtime failure) combined with the manager's own stop signals read
UNDER THE LOCK: `job.status == SKIPPED` (stop() flipped this run's
member before calling cancel_fn) or `_stop_requested`. A cancellation
without either signal is a FOREIGN cancel (engine shutdown /
single-generation Stop / anything outside this run's stop) — not a
user stop of this run.

### 1.2 Fix (smallest safe production change)

`engine/batch_manager.py::_on_job_done` only:

- `concurrent.futures.CancelledError` is caught EXPLICITLY before the
  generic handler; `pre_start_cancel = future.cancelled()` trusts only
  the CANCELLED future STATE (a CancelledError raised by the callable
  itself — future FINISHED with exception — keeps the generic path).
- Classification, under the lock:
  - cancelled future + (`was_stopped` or `_stop_requested`) →
    `SKIPPED`, "Stopped by user." (the documented §14/P3.30(d)
    user-stop contract);
  - cancelled future, NO stop signal → `FAILED` with the explicit
    message "Generation was cancelled before it started." (visible
    non-success; never blanket-translated to SKIPPED, never a silent
    empty-error row);
  - genuine worker exception → FAILED with the real message
    (unchanged);
  - cooperative in-flight cancel → SKIPPED (unchanged);
  - stop racing a completing worker → COMPLETED restore (unchanged).

NOT changed: `stop()` selection semantics, run scoping, retry
semantics, queue progression, `regen_job`, generation versioning,
Asset lineage, `slot_id`/`generation_run`/`part_version`.

### 1.3 Regression tests (`tests/test_p3_45_6_ss7_stop_race.py`)

Deterministic by construction — no blind fixed sleeps; every wait is
a bounded condition loop; the vulnerable state is CONSTRUCTED, not
hoped for:

- Unit level drives REAL `concurrent.futures.Future` objects through
  a scripted pool that reproduces `WorkerPool.cancel_generation()`
  semantics (including the already-started case where `cancel()`
  returns False);
- Real-executor level uses a REAL single-worker `ThreadPoolExecutor`
  with an Event-gated blocker occupying the worker, so the generation
  task is PROVABLY queued (pre-start) when Stop lands — the exact
  SS-7 window, widened deterministically; the full real
  cancel → CANCELLED state → CancelledError → callback chain runs.

Scenarios (the phase brief's seven): pre-start stop → SKIPPED
"Stopped by user."; external cancel without stop → FAILED with
explicit message (no blanket SKIPPED); in-flight cooperative cancel →
SKIPPED; genuine generation error → FAILED; idle Stop → no-op;
selective run §14 (A pre-start + B pending member → SKIPPED,
D non-member → PENDING); callback idempotency (duplicate cancelled
delivery cannot corrupt the stopped state); worker completion racing
Stop → COMPLETED restore.

### 1.4 Results

- Regression proof: both key tests FAIL on the pre-fix tree
  (`git stash` of the production change; verified) and PASS with the
  fix.
- Focused suite: 9/9, run TWICE (3.86 s / 3.88 s).
- Repetition under contention: 20-iteration real-executor loop with
  two CPU-burner threads — all SKIPPED, task never started.
- Affected historical suites: P3.44.4 + P3.44.1 + P3.44.5 + P3.30 +
  P3.35 + P3.41 = 150 passed; frozen stop/batch contracts P3.45.5 +
  P3.45.3 + P3.44.6 + P3.28 = 176 passed.
- `verify_compile` on the changed file: green (final battery runs at
  programme end).

### 1.5 Residual risk

- A FOREIGN cancellation of the batch's future now renders
  FAILED-with-explicit-message rather than FAILED-empty; the toolbar
  single-generation Stop during a batch run (main_window.py:2516)
  falls in this class — documented behaviour, no batch stop requested
  (the batch queue continues per the existing run semantics). No
  evidence this path is reachable while the batch dialog owns the
  stop surface; recorded, not changed (minimal-change discipline).
