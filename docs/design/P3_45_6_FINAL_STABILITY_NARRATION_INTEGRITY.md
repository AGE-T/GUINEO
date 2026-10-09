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

---

## Phase 2 — Narration Block Range Integrity

### 2.1 Root cause (deterministically reproduced)

The reported symptoms (a second Part containing a trailing substring of
the first sentence; text beginning/ending mid-word; stale fragments
after large deletions; blocks behaving strangely after line removals)
trace to the EDIT RECONSTRUCTION, not to the splitter: the source of
Batch Part text is `text[block.start_offset:block.end_offset]`
(`engine/narration_splitter.py:621` + the metadata pre-step 310-311)
over the LIVE editor snapshot — with **zero validation** of the ranges
anywhere in the generation path. Two proven producers corrupt the
ranges:

**Producer A — ambiguous prefix/suffix attribution.**
`NarrationBlockManager.on_text_changed` infers the changed segment by
common-prefix/common-suffix comparison (no Qt position signal was used
anywhere in the codebase; `contentsChange` had zero usages).

*Family A1 (wrong-block symmetry, reproduced):* deleting the FIRST of
two ADJACENT identical passages — the prefix matches through the first
copy, so the diff attributes the deletion to the SECOND: the DELETED
copy's block survives untouched (aliased onto the surviving text, with
its semantics) while the SURVIVING copy's block is REMOVED with all
its metadata (label/Character/overrides).
*Family A2 (multi-edit coalescing, reproduced):* two disjoint
deletions delivered through ONE textChanged emission (undo of a
compound command, programmatic multi-edit) coalesce into a single
spanning edit: the middle block — whose text survived both deletions —
ends up owning nearly the whole document (its Part then contains other
blocks' sentences), and the last block collapses to a one-character
trailing fragment `"t"` — exactly "a second Part containing a trailing
substring of that sentence".

**Producer B — legacy loaded ranges.** `PromptBlock.from_dict` restores
offsets verbatim and `set_scene_blocks` installs them with NO
validation against the live document (source-proven) — corrupt/stale
persisted ranges reach generation silently, including overlaps that
duplicate source text into two Parts.

### 2.2 Investigation B proof (the exact-position API)

`QTextDocument.contentsChange(position, charsRemoved, charsAdded)` is
the exact-position API. Real-widget probe (PySide6 6.11.2, offscreen)
verified for every required edit type: typed edits; interactive
selection deletion; paste; paste-over-selection (ONE combined
emission); replacement; multi-line insert/delete; programmatic
`setPlainText` (TWO emissions: clear-all `(0, oldLen, 0)` then
insert-all `(0, 0, newLen)`); undo/redo of typed edits (exact reverse
deltas at the exact original positions); repeated-text later-occurrence
deletion (exact position 8, NOT the ambiguous earliest). Ordering:
`contentsChange` fires strictly BEFORE `textChanged`. Reading the
document inside the handler yields the post-change text.
`setPlainText` also CLEARS the undo history (so undoing a Replace All
is structurally impossible — its historical mis-diff is unreachable).

### 2.3 Fix (minimal, same engine)

1. **`NarrationBlockManager.on_document_edit(position, removed, added,
   new_text)`** — the exact-position feed transforming through the
   SAME P3.44.9 authoritative `_apply_edit_to_blocks` (no second edit
   engine). Whole-document swaps (setPlainText's clear-all +
   insert-all pair; interactive select-all delete) are HELD and
   resolved at COMMAND level by the `textChanged` → `on_text_changed`
   resolver — preserving the frozen semantics exactly (drop when
   nothing survives; coarse transform for substitutions — the P3.44.7
   contract, regression-caught and fixed during this phase; pre-applied
   Replace All no-ops on the equality guard). `on_text_changed` is
   UNCHANGED (byte-identical contract body + resolver flag) and
   remains the fallback for direct callers.
2. **`NarrationEditor._on_contents_change`** — wires
   `document().contentsChange` with the P3.26 `_loading_scene` guard
   and a `_suppress_contents_change` flag around the pre-applied
   Replace All swap. After the exact feed runs, the subsequent
   textChanged diff no-ops on `_last_text` equality.
3. **Generation precondition (fail closed).** `validate_block_ranges`
   (pure, deterministic; per-issue diagnostics: block identity, label,
   offsets, current text length, exact slice, reason) + the Generate
   Long gate in `_on_generate_long_narration` BEFORE the splitter:
   in-bounds, non-empty, ordered, non-overlapping ranges or the
   generation stops with the exact diagnostics + the existing recovery
   surface (Re-detect Blocks — P3.44.7-proven semantic preservation).
   No Part/slot/job is constructed from invalid ranges; nothing is
   silently clamped; gaps remain legal (the product permits unowned
   text).

### 2.4 Regression tests (`tests/test_p3_45_6_block_range_integrity.py`)

31 tests, run TWICE green: both defect families characterised on the
old path + fixed on the exact path; the 13 brief scenarios (trailing
substring; mid-word split; later-occurrence deletion; large
multi-line deletion; repeated prefix/suffix replacement; save/load
after transforming edits; legacy invalid ranges blocked; previous
assets with deleted source text; Generate-after-deletion uses only
current slices; invalid ranges block before slots/jobs; semantics
preservation through the exact path; undo/redo round-trip; multi-replace
+ document reset) + real-widget editor tests (real cursor selections,
paste-over-selection, scene-load guard, Replace All pre-applied
intact) + gate tests on the real MainWindow/Engine (splitter patched
to prove it never runs on invalid ranges; warning captures; slots
untouched).

### 2.5 Results

- Focused 31/31 twice; battery re-run 40/40 (both P3.45.6 suites).
- Frozen contracts: P3.44.7 + P3.44.9 + P3.44.9.1 + P3.44.8 = 201
  passed + 35 subtests (including the P3.44.7 substitution-edit
  contract that the first-cut design regressed — caught by the suite
  and fixed by the hold-then-resolve design before commit).
- Wide affected: P3.44.4, P3.26, P3.15, P3.22, P3.23, P3.45.1,
  P3.45.2A, P3.45.2B, P3.45.4 = 258 passed.
- `verify_compile` 155/155 on the changed tree.

### 2.6 Residual risks

- The command-level resolver keeps the frozen prefix/suffix semantics
  for whole-document swaps (programmatic setPlainText / select-all
  delete). For a substitution swap with repeated text the coarse
  attribution remains theoretically ambiguous — bounded by the
  generation gate + re-detect recovery, and unreachable for the
  reported flows (typed edits are now exact).
- `QTextDocument.undo()` of block-model operations (split/merge/delete)
  is not a text edit and was never undoable — unchanged, documented.
- The user's actual `GUINEO / Leírás` project file was NOT supplied:
  the corruptions are reproduced on synthetic documents built from the
  reported patterns; the real project's ranges can only be validated
  by the user via the final manual checklist (or by the new gate
  itself, which will now surface any invalid range the project may
  still carry).

---

## Phase 3 — Project / Scene Restoration on Startup

### 3.1 Root cause (source-verified)

`MainWindow._connect_engine` restored only the DISPLAY names from
settings (`current_project` / `current_scene`) into the breadcrumb,
then constructed a PHANTOM in-memory default Project (new uuid, one
scene holding the demo/empty editor text) — the REAL persisted project
was never loaded from disk. The sidebar's project/scene context lists
are built by `_update_recents()`, which ran only on navigation clicks
and later mutations — never at startup. Result: the breadcrumb showed
`PROJECTS > GUINEO > Leírás` while the left panel lists were empty
until the user clicked Projects/Scenes, and the active model objects
were a phantom project + a default scene (not the persisted scene's
text/Blocks).

### 3.2 Fix (reuse of the existing load paths; no second persistence system)

`_restore_last_active_project()` runs during `_connect_engine` after
ProjectManager/RecentsManager initialisation:

- resolves the saved Project — legacy NAME schema primary (the
  persisted settings store names); duplicate display names and stale
  names are disambiguated/recovered through the stable Recents
  entity_id (a real on-disk project id, most-recent first); a display
  name is never treated as a unique id;
- loads the REAL project through the EXISTING `_load_native_project`
  path (first scene + editor text/Blocks/settings + sidebar scene rows
  + breadcrumb + Character selector + Recents MRU +
  `_update_recents()` — which populates the left-panel context list
  and highlights the active row);
- activates the SAVED scene through the EXISTING `_switch_to_scene`
  path (name match, Recents-id disambiguation, first-scene fallback);
- re-persists the selection so the stored state agrees with the actual
  active objects;
- returns False on first run / deleted project (no valid saved
  selection) — the ORIGINAL honest in-memory fallback then applies
  (unchanged), and even then the context list renders (empty is a
  legitimate state; unrendered was the defect). Nothing is persisted
  over existing data; no phantom reaches disk.

### 3.3 Regression tests (`tests/test_p3_45_6_startup_restoration.py`)

10 tests, run TWICE green — REAL MainWindow construction over a
temporary APP_ROOT with pre-seeded settings.json and on-disk projects:
saved project + scene restored (stable id, saved scene not first,
editor text + Block state + label/Character restored, breadcrumb ==
actual objects, Projects list populated with the active row
highlighted WITHOUT any click, sidebar scene rows populated with the
saved scene active); scene-list data complete at startup; saved-scene
missing → first scene; first run (no projects) → honest default with a
RENDERED (empty) list; deleted project → honest fallback with zero
disk mutation; stale name → Recents-id recovery; duplicate names →
stable-id disambiguation; legacy name-only settings; persisted
selection agrees after restore; Recents MRU recorded at startup.

### 3.4 Results

- Focused 10/10 twice; affected suites: P3.17 + P3.21 + P3.24 + P3.26
  + P3.16 + P3.13 + P3.9 = 202 passed; MainWindow-heavy P3.44.4 +
  P3.45.4 + Phase 2 suite = 71 passed; `verify_compile` 157/157.

### 3.5 Residual risks

- The persisted schema remains NAME-based (compatibility preserved);
  the stable-id recovery depends on Recents entries existing. A
  renamed project with cleared Recents falls back to the honest
  default — documented, not silently mis-restored.
- The user's actual `GUINEO / Leírás` project was not supplied; the
  restoration is proven on synthetic on-disk projects with the same
  shape (see the final manual checklist).
