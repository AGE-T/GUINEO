# P3.45.5 — Verification and Test Hygiene

Baseline: `main @ 418b9ed` (P3.45.4 build commit; origin identical,
clean tree). Every finding was re-verified at that HEAD by the
Post-P3.45 roadmap review (code evidence + runtime reproduction where
a runtime signal exists) before this round started. This round is
strictly maintenance: it changes NO product behaviour and reopens NO
completed P3.44/P3.45 functionality.

---

## 1. CONTEXT AND VERIFIED FINDINGS

The Post-P3.45 audit adjudicated the three known test/verification
problems and the architecture-gate state:

| # | Finding | Verdict at `418b9ed` | Evidence |
|---|---------|----------------------|----------|
| A | `verify_architecture.py` whitelist vacuous | **PROVEN** | `"engine"` inside its own `UI_ALLOWED_ENGINE_IMPORTS` + the `mod.startswith(w + ".")` match ⇒ the facade rule can NEVER fire (synthetic proof: `engine.anything_at_all` passes); the manifest claimed a facade-only UI while 24 distinct engine modules were really imported (11 never whitelisted); scanning was top-level-only, so lazy imports bypassed BOTH layer rules (`engine/batch_manager.py`'s lazy `QTimer` and `engine/version.py`'s lazy `PySide6` version probe were invisible) |
| B | `verify_integration.py` stop expectation pre-P3.44.4 | **PROVEN** | Stops a NEVER-STARTED manager and expects SKIPPED — the documented §14 contract leaves never-run jobs PENDING (idle stop = no-op); the tool has exactly one commit (root `8d14815`) and was red from the repository's first commit |
| C | SS-3 README test asserts deleted header | **PROVEN, deterministic** | `# EN | British English` deleted by the owner's deliberate README rewrite `cd5e122`; zero "British" occurrences remain; 32/33 with 1 permanent red |
| D | P3.44.4 regen-stop flake | **PROVEN flaky** (3/30 at this baseline; historically 2/8, 3/4) | Root cause 1: the blind `_process(400)` sleep could elapse while the previous generation's future was still in flight → busy-retry chain → FAILED. Root cause 2 (discovered during this round's stabilisation, see §6): Stop landing in the submit-to-start window cancels the queued future → `_on_job_done`'s exception path renders the job FAILED (contract says Stopped) |
| E | False docstrings + stale register entries | **PROVEN** | `main_window.py` and `audio_provenance.py` claimed re-detect materialises `expected_audio_slots`; the sole production writer is Generate Long (`grep`/AST: exactly one call site, `main_window.py`); BUG-001's closure conditions were long met (P3.44.2 runtime suites verify model-input prompt equality) yet the entry said FIXED-PENDING-VERIFICATION; P3.45-GATE said "implementation not started" with four slices complete |

## 2. IMPLEMENTATION (five fixes, no product behaviour change)

### 2.1 `tools/verify_architecture.py` — the gate gets teeth

* The facade root import (`from engine import Engine` / `import
  engine`) is allowed ONLY as the exact name `engine`; every
  `engine.<module>` import must be explicitly listed. Names imported
  via `from engine import NAME` are resolved against the real
  `engine/` package contents (a submodule counts as `engine.NAME`; a
  facade re-export counts as the root) — for absolute AND
  relative-resolved imports (the `from ...engine import X` escape is
  closed).
* Two-tier whitelist: `UI_ALLOWED_ENGINE_IMPORTS` (16 by-design
  modules — data classes, display lookups, managers owned by their
  dialogs) + `UI_BASELINE_ENGINE_IMPORTS` (the 11 direct-import
  modules that already existed: audio_provenance, character_colors,
  combined_audio, duration_estimation, narration_splitter,
  output_guard, project_exporter, project_manager, prompt_state,
  recents_manager, scene_persistence). Anything else — `engine.engine`,
  `model_manager`, `generation_manager`, `workers`, `block_detector`,
  `sentence_boundaries`, any future module — is a violation.
* FULL-AST import scanning (function/class-level lazy imports
  included); relative imports resolved to absolute paths.
* Engine "no Qt" rule: same full-AST scanning, with exactly two
  per-file, per-module documented exceptions — `engine/version.py` →
  `PySide6` (the `get_qt_version()` reporting probe; no Qt machinery)
  and `engine/batch_manager.py` → `PySide6.QtCore` (the known QTimer
  debt; its documented future task is a UI-injected scheduler).
* Transparency: every run PRINTS the baseline-debt imports (module ×
  files) and the two engine-side exceptions — the debt cannot hide;
  it just does not fail the gate. The D-R10 facade refactor was NOT
  started (explicitly out of scope).

### 2.2 `architecture_manifest.yaml` §2 — honest reconciliation

The dependency-rules section now records what IS (the two-tier
enforced whitelist, the 11-module documented baseline debt, the two
engine-side Qt exceptions) instead of claiming a facade-only
architecture the code never had. Existing architectural debt is
distinguished from the rules enforced against future changes:
removing a tier-2 entry requires an explicit manifest decision.

### 2.3 `tools/verify_integration.py` — the documented §14 contract

The old never-started-then-stop scenario is replaced by coverage that
preserves its intent (stop cancels queued work) and adds the idle
behaviour:

* **Active selective run** (deterministic, no Qt event loop needed):
  three jobs, `start(only=[0, 1])` with a never-completing submit
  future (the job stays in flight exactly like a real generation);
  after the ACTIVE state is asserted (jA GENERATING), `stop()` →
  jA (in-flight member) SKIPPED, jB (pending member) SKIPPED,
  jC (never-run non-member) PENDING untouched — §14 A/B/D verbatim.
* **Idle stop**: a never-started manager's `stop()` changes nothing —
  jobs stay PENDING, manager not running (the §10 no-op).
* The unchanged duration-source check keeps its coverage.

### 2.4 SS-3 — `tests/test_p3_37_branding_documentation.py`

`test_readme_has_british_english_section` now verifies the CURRENT
structure: the `english` anchor (the cross-link/internal-anchor
target), the `# GUINEO` product title, and that README.md contains no
second language section (the Hungarian guide lives in README_HU.md).
The obsolete literal-header assertion is gone; British spelling stays
pinned by the dedicated spelling test; all other assertions untouched.
Module result: 33/33.

### 2.5 Stop-timing stabilisation — `tests/test_p3_44_4_batch_execution_run.py`

Test-side only; production stop semantics untouched:

* `wait_engine_idle(timeout=15)`: bounded condition barrier on
  `engine.is_generating` — the EXACT precondition `submit_generation`
  checks — replacing the blind `_process(400)` before the regen.
* `wait_generation_inflight(calls_before, timeout=15)`: bounded
  barrier on the fake model call receiving the request, called BEFORE
  `dlg._on_stop()` — pinning the stop to the §14 A-case the test
  documents ("Stop while the generation is in flight") and closing the
  submit-to-start race window (see §6).
* The second `_process(400)` (event-queue DRAIN after an injected
  stale callback in a different test) is intentionally kept: no
  engine-state condition exists to wait on, and it was not the
  identified flake's synchronisation.

### 2.6 Documentation and register corrections

* `ui/main_window.py` (`_start_long_narration` P3.28 docstring) and
  `engine/audio_provenance.py` (`materialize_expected_slots`
  docstring): slots are materialised ONLY by the Generate Long
  structure event — never on a keystroke and never on re-detect
  (re-detect replaces block ids and leaves the recorded structure
  unchanged until the next Generate Long — the documented
  "Unlinked part" detachment).
* `docs/governance/OPEN_BUGS.md`: BUG-001 → CLOSED (resolution note
  documents the three closure conditions now independently verified);
  P3.45-GATE → COMPLETED (P3.45.1–P3.45.4 all delivered); SS-3/SS-4
  → CLOSED by this round; new SS-7 finding recorded (see §6); no
  historical evidence deleted.

## 3. TESTS

New focused suite `tests/test_p3_45_5_verification_hygiene.py`
(31 tests): architecture verifier (real tree passes with the
transparency data; tier-1/tier-2/facade-root/lazy-baseline imports
pass; arbitrary new engine imports rejected top-level, lazy,
relative-escaped, facade-name-smuggled and plain; ML imports rejected;
engine lazy-Qt rejected; the two exceptions permitted and
module-scoped; engine→ui rejected); §14 stop contract (active
selective run, whole-queue run, idle no-op — real BatchManager);
the integration tool exits 0; SS-3 (current structure contract,
obsolete assertion gone, module green via subprocess); stop-timing
source contracts (both barriers condition-based and bounded; no blind
sleep in the regen test); slot-materialisation documentation
(docstrings corrected; exactly ONE production call site —
AST-scanned); governance register statuses.

Focused: 31/31 TWICE. Stop-test stability (the previously flaky test):
30/30 isolated; 15/15 under 2-CPU burner contention; 5× whole-file
12/12; 175-test multi-file in-battery-position chunk — all green.

## 4. WHAT WAS NOT DONE (scope gate)

* No product behaviour change anywhere: `engine/batch_manager.py`,
  `engine/workers.py`, `engine/engine.py`, all UI panels — untouched
  (the only production-file changes are the two DOCSTRINGS).
* The SS-7 pre-start cancel race was NOT fixed (a product change in
  the frozen P3.44.4 stop zone → its own future task; documented in
  OPEN_BUGS with evidence and fix direction).
* The D-R10 facade refactor was NOT started; the two engine-side Qt
  exceptions are DOCUMENTED debt, not fixes.
* No frozen contract touched: P3.44.9, P3.44.9.1, P3.45.1, P3.45.2A,
  P3.45.2B, P3.45.3, P3.45.4, slot_id, generation_run, part_version,
  append-only lineage, Batch workspace, Scene Combine, Manual
  Concatenate — all re-validated green this round (see the release
  validation battery).

## 5. FROZEN-CONTRACT SAFETY

The full P3.44/P3.45 regression battery was re-run at the
implementation commit (see DEVELOPMENT_LOG P3.45.5): P3.45.4 28,
P3.45.3 40, P3.45.2B 33, P3.45.2A 42, P3.45.1 20, P3.44.9 76+35
subtests, P3.44.9.1 19, P3.44.4 12 (×6 stability), P3.44.1 23 — plus
the full suite — all green, with the previously-documented failures
(SS-3, the flake, verify_integration) now closed by this round.

## 6. NEW FINDING — SS-7 (documented, deliberately unfixed)

While stabilising the regen-stop test, the reproduction logs exposed a
genuine product race (user-reachable): a Stop that lands between the
job's GENERATING marking and the worker's task pickup causes
`Future.cancel()` to succeed on the still-queued future;
`BatchManager._on_job_done` then takes the exception path
(`CancelledError`, empty message) and marks the job FAILED —
violating §14/P3.30(d) ("a cooperative cancel renders as a USER STOP,
never as a red FAILED row"). Evidence: 3/30 reproductions, full log
captures (no pipeline stage ever ran). Fix direction (three lines in
`_on_job_done`'s exception path: check `future.cancelled()` /
`was_stopped` → SKIPPED). NOT fixed here: this round changes no
product behaviour; the finding is recorded as OPEN_BUGS SS-7 for its
own evidence-first task.

## 7. REMAINING RISKS / DEFERRED

1. **SS-7** (§6) — OPEN, evidence-backed, awaiting its own task.
2. The two other stop tests still use `wait_status(GENERATING)` alone
   (they can theoretically sample the SS-7 window; bounded by the SS-7
   fix).
3. The tier-2 baseline debt and the two engine-side Qt exceptions are
   DOCUMENTED, not resolved (D-R10-family decisions remain future
   work).
4. P3.27B-E1 (SSH06 modal poisoning) and SS-6 (GL-shim hard-coded
   path) remain OPEN environment/test-infrastructure items, unchanged.
5. The `QObject::startTimer` stderr warning from verify_integration's
   unchanged duration check is pre-existing and cosmetic (exit 0).
