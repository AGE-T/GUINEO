# P3.45.2A — Oversized Block Preflight (single-output ceiling)

Task: "Oversized Block Preflight — evidence-first implementation, minimal
change, no estimator redesign." Baseline: main @ `2e2cb46` (P3.45.1
COMPLETE). P3.45.2 is split into A (this) and B (Duration Estimation
Consistency — NOT implemented here, explicitly forbidden).

The companion focused suite: `tests/test_p3_45_2a_oversized_block_preflight.py`
(42 tests). This document is the contract they lock.

---

## 1. CURRENT STATE VERIFIED (reconnaissance at current HEAD)

Every path below was traced on the working tree at the implementation
commit, before and after the change:

1. **Single Generate**: `MainWindow._on_generate()` →
   `_start_generation(text)` → `final_prompt = _build_prompt()` →
   `GenerationRequest(text=final_prompt, parameters=
   control_panel.get_parameters())` → `Engine.generate()` →
   `GenerationManager.generate()` → `model.generate_speech(text, …,
   max_new_tokens=params.max_new_tokens)` → waveform → `GenerationResult`.
2. **Generate Long**: `_on_generate_long_narration()` →
   `NarrationSplitter.split()` → `LongNarrationDialog(parts, …,
   generation_parameters=params)` (preview + per-part edit) →
   `generate_requested` → Batch queue → per-part `GenerationRequest`s.
3. **Sentence splitting** (`engine/narration_splitter.py
   _group_sentence_units`): a single sentence longer than
   `MAX_CHARS = 400` becomes **its own part and is never cut** —
   the verified bypass A: an oversized sentence reached the engine with
   no size check.
4. **Speaker turns** (`_split_with_speakers`): each `$SPEAKER:` turn is
   **one part, never re-chunked** — the verified bypass B: a giant turn
   reached the engine with the same absence of checks.
5. **Batch**: `BatchJob.prompt` / `BatchJob.parameters` are the editable
   workspace truth; `to_request()` builds `GenerationRequest(text=
   self.prompt, parameters=self.parameters)` — the effective values.
6. **Regeneration**: `BatchGenerationDialog._on_regen(idx)` → existing
   confirmation modal → `regen_requested` → versioned part regen.
7. **`max_new_tokens` origin**: `GenerationParameters` dataclass default
   4096 (`engine/models.py`); per-request from the control panel spinbox
   (range 128–8192, user-editable); per-job in the Batch workspace
   (editable); preset YAML may carry 2048. `model_manager` warmup uses
   128 but that is not a user path. **No model-specific limit API
   exists** — limitation documented in §9.
8. **Existing duration guard** (`engine/output_guard.py
   detect_output_anomaly`, P3.27B): POST-generation, conservative,
   flag-only. R1 runaway trailing silence, R2 expected-duration multiple
   (needs `expected_duration` supplied), R3 fully silent output. R2 only
   fires on outputs LONGER than expected — a TRUNCATED output (shorter
   than the text needs) is invisible to it. Confirmed the triage's
   truncation blind spot.
9. **Truncation metadata**: `generate_speech()` returns only a waveform
   tensor; `GenerationResult` has no token count, frame count, finish
   reason or completion status. **No explicit truncation metadata exists
   anywhere in the chain** — see §3.

Request-flow diagram (normal block and oversized block, after the change):

```
user text
  → editor blocks / plain text
  → [single Generate]  _build_prompt() ──► PREFLIGHT(final_prompt,
  │                                              request.parameters)   ┐
  → [Generate Long]    NarrationSplitter.split()                       │
  │                      ├─ normal sentence groups ──┐                 │
  │                      ├─ oversized single sentence (own part) ──┤   │
  │                      └─ $SPEAKER turn (own part) ────────────┘   │
  → LongNarrationDialog  per-part PREFLIGHT(part.text, params)  ──────┤
  │   (header markers + limit note; Generate click re-checks        │  the ONE
  │    the EDITED text)                                             │  source:
  → Batch queue          BatchJob(prompt, parameters)                │  engine/
  │                      manual start / selective start / regen ────┤  output_
  → GenerationRequest    (text, parameters.max_new_tokens)           │  guard.py
  → Engine.generate()    GenerationManager → generate_speech(        │
  │                          max_new_tokens=effective)               │
  → waveform             (STOP at token budget = mid-speech cut,     │
  │                       or EOS; no metadata distinguishes them)    │
  → GenerationResult     → post-generation R1/R2/R3 guards (P3.27B,
                          unchanged) → AudioAsset (append-only)
```

## 2. ROOT CAUSE / CURRENT GAP

Before P3.45.2A NOTHING compared the requested text against the
single-output ceiling. Consequences:

- A request needing more audio than `max_new_tokens / 25 fps` produces
  a mid-speech cut at the token budget, silently — the user sees a
  "completed" asset that is truncated.
- The P3.27B post-generation guard cannot catch it: R1/R2/R3 all fire on
  outputs LONGER than expectations; a truncated output is shorter.
- The two splitter bypasses (oversized single sentence; giant speaker
  turn) reached the engine with no check at any layer.

## 3. TECHNICAL LIMIT (exact, and where the values originate)

The single-output ceiling of ONE generation call:

```
ceiling_seconds = effective_max_new_tokens / HIGGS_FRAME_RATE
                 = 4096 / 25 = 163.84 s   (default configuration)
```

- `HIGGS_FRAME_RATE = 25` — `engine/output_guard.py` (research:
  bosonai_higgs-tts-v3-4b README, "8 codebooks at 25 fps"); the same
  constant the P3.27B guard uses.
- `effective_max_new_tokens` — the value actually sent to
  `generate_speech()`: `request.parameters.max_new_tokens`. When a
  caller has no parameters object, the `GenerationParameters()` dataclass
  default (4096) is resolved inside the preflight — the dataclass stays
  the single configured source; no second constant exists.
- The model generates audio autoregressively at 25 frames/sec and stops
  at the token budget OR end-of-speech, whichever comes first. Text that
  needs more audio than the budget cannot be produced by one call.
- The preflight is the PRE-generation counterpart of the post-generation
  guard; both share `HIGGS_FRAME_RATE` and the token-ceiling arithmetic
  (`test_single_source_with_the_post_generation_guard` pins them equal).

The previous code comment "4096 ≈ 30s audio" was WRONG; corrected ONLY in
the three places this task touches (`engine/models.py` field comment,
control-panel tooltip, token guide row — the old tooltip even claimed
"4096 ≈ 15s"). No other files were cleaned.

## 4. PREFLIGHT CONTRACT (SAFE / WARNING / BLOCKED)

`engine/output_guard.preflight_generation_size(text, max_new_tokens)` —
pure function (no Qt, no engine state, no disk), returns a structured
dict, never a UI string alone:

| field | meaning |
|---|---|
| `state` | `"safe"` \| `"warning"` \| `"blocked"` |
| `reasons` | human-readable list (empty when safe) |
| `effective_max_new_tokens` | the value actually used |
| `effective_fps` | 25 (`HIGGS_FRAME_RATE`) |
| `maximum_output_seconds` | tokens / 25, 2 dp |
| `estimated_request_seconds` | chars / 15, 2 dp, `None` without a basis |
| `input_basis` / `basis_chars` | `"text_length"` + char count, or `"none"` |
| `chars_per_second` | 15.0 — the EXISTING heuristic (see below) |
| `parts_required` | ceil(estimated / ceiling) — informational only |
| `display_message` | single-source UI text ("" when safe) |

**Estimate basis**: the project's EXISTING ~15 chars/sec heuristic — the
same figure `NarrationSplitter` uses for `SplitPart.estimated_duration`
at its four split sites. This is CONSUMPTION of the existing heuristic
(P3.45.2B owns its recalibration — untouched here). The basis is reported
in the result so no caller can present an estimate as a measurement.

**Boundary semantics** (deterministic, NO floating-point equality):

```
blocked  ⇔  chars * 25 >  tokens * 15     (exact integers)
warning  ⇔  chars * 25 == tokens * 15     (exact integers)
safe     ⇔  chars * 25 <  tokens * 15
```

compared through `fractions.Fraction` as the exact reals `chars/15` vs
`tokens/25`. Tightest default-budget integer pair: 2457 chars safe /
2458 chars blocked (4096 tokens). Exact warning pair: 2460 chars at
4100 tokens (both exactly 164.0 s).

**Why WARNING = exactly-at-ceiling and nothing else**: the task forbids
inventing arbitrary percentage thresholds. "Exactly at the ceiling" is
the only boundary derivable without new data. A softer warning band
(a "90 % of ceiling" style) remains a later UX/data decision (§9).

**No text basis** (`text=None`): verdict is SAFE with a reason —
evidence-first: never block without a basis.

**Three concepts kept distinct** (per the task):
1. text duration estimate — the /15 heuristic, consumed as-is;
2. engine maximum output duration — the hard technical constraint
   (this preflight's subject);
3. actual generated audio duration — post-generation evidence; it can
   never replace preflight (and `output_duration` stays a measurement
   on the result, untouched).

**Truncation detection**: no explicit metadata exists in the chain (§1.9);
P3.45.2A deliberately does NOT fabricate a fragile duration-based
inference. The preflight prevents the PREDICTABLE overflow; the P3.27B
guards keep their role. Pinned by
`test_generation_result_has_no_fabricated_truncation_metadata`.

**NO AUTO-SPLIT**: the preflight is a classification layer, never a
mutation layer. It never rewrites text, never inserts blocks, never
changes sentence boundaries, slot_ids, Scene or Batch structure.
`parts_required` is reported as INFORMATION ("would need ≥ N parts") —
the splitting decision stays with the user.

## 5. IMPLEMENTATION (files changed, single source of truth)

| file | change |
|---|---|
| `engine/output_guard.py` | + `preflight_generation_size()`, `preflight_display_message()`, `preflight_summary_line()`, constants `PREFLIGHT_SAFE/WARNING/BLOCKED`, `ESTIMATED_CHARS_PER_SECOND = 15.0`; module docstring updated |
| `ui/main_window.py` | single Generate: preflight on `final_prompt` + `request.parameters.max_new_tokens` — BLOCKED → ONE confirmation, decline aborts BEFORE any state mutation; WARNING → status-bar note. Generate Long: passes `generation_parameters=params` to the dialog. Batch selective start (`_on_generate_selected`): preflights the CHECKED jobs (current prompt + current parameters), one confirmation listing affected jobs, decline = nothing started |
| `ui/panels/long_narration_dialog.py` | optional `generation_parameters` ctor arg (None → default, backwards compatible); per-part header markers (⚠ … exceeds / exactly at the ~Ns limit) + limit note in `_build_ui` (split-time text basis); Generate click re-checks the EDITED text (one confirmation listing affected parts; decline keeps the dialog open, emits nothing) |
| `ui/panels/batch_generation.py` | manual start (`_on_start`): preflights every runnable job (PENDING/SKIPPED/FAILED — the `start(reset_failed=True)` run set), one confirmation, decline = nothing started; `_on_regen`: the verdict is folded into the EXISTING confirmation (no second modal) |
| `engine/models.py` | comment fix only (the misleading "≈ 30s" note → real 25 fps ceiling, pointing at output_guard) |
| `ui/panels/control_panel.py` | tooltip fix only (2048≈82s / 4096≈164s / 8192≈328s at 25 fps) |
| `ui/panels/token_guide.py` | one row's duration claim corrected |

The hard-limit arithmetic exists ONLY in `engine/output_guard.py`. Every
UI call site supplies the actual basis (compiled prompt / part text /
job prompt) + the effective parameters and calls the one function. No
class was introduced — the existing guard's dict-result pattern
(`detect_output_anomaly`) is reused for the preflight result.

All call sites follow the P3.27B defensive posture: a preflight failure
(import/exception) can never block a normal generation (`try/except`,
log-only). BLOCKED is a user decision point ("Generate anyway?" with
the default on No), never a silent hard failure.

## 6. PROTECTED PATHS

| path | protection | test |
|---|---|---|
| single Generate | BLOCKED → confirmation; decline → nothing submitted, no scene-status mutation | `TestSingleGeneratePath` (4) |
| Generate Long preview | per-part markers + limit note before commit | `TestLongNarrationDialogPreflight` (9) |
| oversized single sentence (bypass A) | becomes a part → dialog preflight (marker + Generate-click confirm); splitter behaviour itself unchanged | `TestSplitterBypassPaths` (2) |
| speaker turn (bypass B) | same as A; neighbouring turns unaffected | `TestSplitterBypassPaths` (2) |
| Batch selective start | flagged jobs reported in one confirmation; decline = byte-identical workspace; unchecked blocked jobs do not affect the run | `TestBatchSelectiveStart` (4) |
| Batch manual start | runnable jobs preflighted; decline = no start | `TestBatchManualStart` (3) |
| per-row regeneration | verdict folded into the existing confirmation | `TestRegenerationPreflight` (4) |
| normal generation | unchanged path, zero UI noise (SAFE) | `test_safe_prompt_generates_normally_no_modal`, `test_normal_generation_full_pipeline_regression` |

## 7. REMAINING BYPASSES (proven, evidence-based)

- **Engine-side generation with no UI call site**: anything that calls
  `Engine.generate()` directly (scripts, future automation) is not
  preflighted. The preflight lives at the presentation/generation-entry
  layer because that is where the user decision point exists.
- **The estimate is heuristic**: the /15 basis can both over- and
  under-estimate real speech duration (P3.45.2B territory). A request
  classified SAFE can still truncate if the real reading is much slower
  than 15 chars/sec; a request classified BLOCKED may actually fit.
  This is inherent to a character-based estimate and is stated in every
  user-facing message ("The estimate is heuristic (character-based);
  the maximum is the real technical limit.").
- **Post-generation truncation remains undetectable** (§3): no metadata
  exists; the R1/R2/R3 guards keep their original role only.

## 8. TESTS

`tests/test_p3_45_2a_oversized_block_preflight.py` — 42 tests, real
MainWindow + Engine + splitter + dialog + batch through the actual
application paths (model boundary faked, house pattern). Covers the 12
mandated categories: below/at/above hard limit (exact-integer boundary
pairs), oversized single sentence, speaker turn, different effective
`max_new_tokens` (2048/4100/4096/8192 incl. the control-panel spinbox
path), no-mutation (byte-identical jobs/slots/scene status/blocks),
normal generation regression, Batch regression (safe + blocked +
neighbour integrity), existing guard regression (R1/R2/R3 suite green),
truncation boundary semantics (no fabricated metadata — pinned), legacy
configuration (dialog ctor without parameters; preset-default budget
path), plus truthful-message content assertions through a
`QMessageBox.question` recorder (message text, not just call counts).

## 9. VALIDATION (all run at the implementation commit)

| validation | result |
|---|---|
| focused P3.45.2A suite | 42/42 (twice — stable) |
| P3.45.1 + P3.44.9 + P3.44.9.1 + P3.27B guard + P3.28 provenance focused | 210 passed + 35 subtests |
| full suite (6 foreground chunks, house pattern) | 1530 passed + 1 failed (SS-3, the documented pre-registered P3.37 README test — fails identically on the pristine baseline) + 35 subtests |
| environment note | `test_p3_43_voice_unification.py` + `test_p3_44_history_player_load.py` fail COLLECTION in this venv (`torch.__spec__ is None` after the house stub install): **proven identical at baseline `2e2cb46` via `git stash`** — environment drift (torch no longer importable), NOT a P3.45.2A regression; both files were green in the P3.45.1-era environment |
| `tools/verify_compile.py` | 149/149 PASS |
| `tools/verify_architecture.py` | PASS |
| `tools/verify_functional_integrity.py` | 80/80 PASS |
| launch smoke (real entry path, offscreen) | 11/11 PASS (`ss-audit/p3452a_launch_smoke.py`, uncommitted probe) |

## 10. SCOPE CHECK

No duration-estimator redesign; no `/15` formula change (consumed
as-is); no preview estimator rewrite; no Assemble duration semantics
change; no auto-splitting; no block mutation; no new identity system;
no slot_id changes; no GenerationPlan; no fingerprint; no STALE; no
session locking; no Batch architecture rewrite; no Scene Combine
rewrite; no Manual Concatenate rewrite; no P3.44.9 changes; no
P3.44.9.1 geometry changes; no timers; no polling; no unrelated
rendering refactor. P3.45.2B (Duration Estimation Consistency) was NOT
started.

## 11. REMAINING RISKS (evidence-based only)

1. The WARNING band is exactly-at-ceiling only — a softer warning
   threshold (e.g. 90 % of ceiling) is a later UX/data decision,
   deliberately not invented here.
2. No model-specific limit API exists; if a future backend exposes a
   different frame rate or a per-model token ceiling, the preflight
   must consume it through `effective_*` fields (the contract already
   reports them; today they resolve from the single house source).
3. Long-dialog WARNING parts raise no modal (header markers only) —
   documented residual: the marker is visible before commit and the
   exactly-at-ceiling case has zero margin by definition, but a modal
   would be noise for a state the numbers already describe.
4. The heuristic estimate can misclassify in both directions (§7);
   the user-facing message always discloses which number is which.

## 12. FILES / COMMIT

Modified: `engine/output_guard.py`, `engine/models.py`,
`ui/main_window.py`, `ui/panels/long_narration_dialog.py`,
`ui/panels/batch_generation.py`, `ui/panels/control_panel.py`,
`ui/panels/token_guide.py`.
New: `tests/test_p3_45_2a_oversized_block_preflight.py`, this document.
Commit: see DEVELOPMENT_LOG.txt P3.45.2A entry (single implementation
commit on main, pushed to origin/main).
