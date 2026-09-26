# Feature Spec: Batch Generation (F-401..F-410)

## Summary

Batch Generation lets the user queue multiple generation jobs and run
them sequentially.  The BatchManager paces jobs (250 ms between
generations), retries on "already running" errors (200 ms delay), and
marshals all callbacks to the UI thread via `QTimer.singleShot(0, fn)`.

## Feature IDs

| ID    | Name                          |
|-------|-------------------------------|
| F-401 | Job queue                     |
| F-402 | Add/edit/remove jobs          |
| F-403 | Move/duplicate jobs           |
| F-404 | Save/load queue (YAML)        |
| F-405 | Start/pause/stop              |
| F-406 | Retry on busy                 |
| F-407 | Inter-job delay 250ms         |
| F-408 | Per-job seed                  |
| F-409 | Per-job output name           |
| F-410 | Job duration = audio length   |

## Data Model

`engine.batch_manager.BatchJob`:

| Field             | Type                  | Notes                              |
|-------------------|-----------------------|------------------------------------|
| name              | str                   | Human-readable label.              |
| prompt            | str                   | May include Higgs tokens.          |
| voice_id          | Optional[str]         | None = no cloning.                 |
| output_filename   | Optional[str]         | None = auto timestamp.             |
| parameters        | GenerationParameters  | Full sampling config.              |
| status            | JobStatus             | PENDING/GENERATING/COMPLETED/...   |
| error             | Optional[str]         |                                    |
| output_path       | Optional[str]         | Relative path of saved WAV.        |
| output_duration   | float                 | Audio length in seconds.           |
| generation_time   | float                 | Wall-clock time in seconds.        |
| started_at        | Optional[float]       | Epoch seconds.                     |
| finished_at       | Optional[float]       | Epoch seconds.                     |

`engine.batch_manager.BatchSummary` aggregates the totals.

## Architecture

```
+--------------------+        submit_fn(req)        +------------+
| BatchGenerationUI  |  -->  BatchManager          | Engine     |
|  (Qt table + btns) |  <--  marshal_to_ui(fn)      | (worker)   |
+--------------------+        on_changed(mgr)       +------------+
```

The BatchManager is Qt-free.  All callbacks are scheduled onto the UI
thread by the `marshal_to_ui` callable, which the dialog sets to
`lambda fn: QTimer.singleShot(0, fn)`.

## Retry Mechanism

When `submit_fn` raises `RuntimeError("...already running...")`:

1. The job is reverted to `PENDING`.
2. After `RETRY_DELAY_MS` (200 ms), `_process_next` is re-scheduled.
3. After `MAX_RETRIES` (5) attempts, the job is marked `FAILED`.

## Stop Behaviour

`BatchManager.stop()` immediately:

1. Sets `_stop_requested = True`.
2. Marks every `PENDING` job as `SKIPPED` with the error string
   `"Skipped: batch stopped by user."`.
3. Calls `cancel_fn()` (= `engine.cancel_generation()`).
4. Recomputes the summary and emits a changed event.

The dialog's `_on_stop` handler ALSO refreshes the table immediately
(rather than waiting for the marshalled callback) so the user sees the
SKIPPED rows the instant they click Stop.

## Job Duration

`BatchJob.output_duration` is set from `GenerationResult.output_duration`
(audio length), NOT `generation_time` (wall clock).  This matters
because a job that produces 30 s of audio in 5 s of wall-clock time
should contribute 30 s to the batch's "total audio seconds", not 5 s.

## Per-Job Seed

`JobEditDialog` has a `QLineEdit` for the seed:

- Empty string -> `parameters.seed = None` (random).
- Integer string -> `parameters.seed = int(text)` (reproducible).

## YAML Persistence

`save_to_file(path)` writes a single document with a `schema_version`
and a `jobs` list.  `load_from_file(path, replace=True)` resets every
job's transient state (status, output_*, timestamps) to PENDING.

## Test Plan

1. Add 3 jobs, Start, verify all 3 complete in order.
2. Add a job while a generation is running, verify the manager
   retries with 200 ms delay rather than failing immediately.
3. Click Stop mid-batch, verify the remaining PENDING jobs become
   SKIPPED instantly.
4. Set a per-job seed, run twice, verify identical output hashes.
5. Save the queue, restart the app, load the queue, verify all jobs
   reappear with PENDING status.
