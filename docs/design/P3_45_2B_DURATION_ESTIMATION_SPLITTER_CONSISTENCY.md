# P3.45.2B — Duration Estimation + Splitter Consistency

Task: "Duration Estimation + Splitter Consistency — code-level audit,
controlled implementation, regression validation and fresh Git build."
Baseline: main @ `5a6e5f3` (P3.45.2A COMPLETE). This document records
the audit, the canonical semantics, the implementation and the
validation. The companion focused suite:
`tests/test_p3_45_2b_duration_splitter_consistency.py` (33 tests).

---

## 1. CODE-LEVEL AUDIT (verified at HEAD, not from prior reports)

The full audit was performed on the working tree at `5a6e5f3`
(splitter, scanner, guard, editor, dialog, batch, provenance read in
full; two parallel read-only agent audits covered every UI duration
surface and every staleness path; runtime probes measured the actual
behaviour). Key findings:

### 1.1 Duration producers (the estimate, pre-change)

| site | basis | formula |
|---|---|---|
| `engine/narration_splitter.py:421/504/573/642` (4 split paths) | plain part text | `len(text)/15.0` |
| `ui/panels/narration_editor.py:2109` (Preview stats) | **TOKENIZED prompt** (prepended control tokens counted as speech) | `len(prompt)/15.0` |
| `engine/output_guard.py` (preflight) | caller's text (prompt for single-Generate/batch; part text for the long dialog) | `chars / ESTIMATED_CHARS_PER_SECOND` (own constant `15.0`) |

Six sites, two text bases, two constants (one literal ×5, one named).
Measured divergence for one Fear-override block: 80 vs 64 chars =
**+25 %**; the P3.45 triage measured up to **+102 %** for token-heavy
state. `SplitPart.char_count` is prompt-based while
`estimated_duration` on the same object is text-based — mixed bases in
one dialog header line.

### 1.2 Splitter behaviour (pre-change, runtime-probed)

- Grouping closed at `TARGET_SENTENCES=3` sentences (≥50 chars):
  typical parts were **~13 s** — BELOW the 20–25 s product target.
- `MAX_CHARS=400` cap: groups ≤ 400 chars (≈26.7 s) — unchanged.
- Single sentence > 400 chars → own part, **uncapped** (probe: 606
  chars = 40.4 s).
- **Speaker turn → ONE part regardless of size** (probe: 1099 chars =
  73.3 s) — the documented P3.45.2A bypass.
- Block ≤ 400 chars → exactly one part (the frozen 1-block = 1-part
  mapping).
- No sub-sentence splitting mechanism exists anywhere
  (`engine/sentence_boundaries.py` produces sentence-level units only).

### 1.3 Stale paths (actual, not theoretical)

1. Long-dialog part edit: `p.estimated_duration` was never recomputed
   (`long_narration_dialog.py:591` updated `char_count` only) → the
   stale value flowed into `BatchJob.expected_duration`
   (`main_window.py:3611`) → the R2 output guard compared measured
   audio against a pre-edit estimate.
2. Batch workspace prompt edit (`JobEditDialog.apply_to_job`):
   `expected_duration` untouched — stale for the guard and for queue
   YAML save/load.
3. Regeneration preserves `expected_duration` (fine once (2) is fixed).
4. Non-stale by construction: Preview stats (live), all five 2A
   preflight sites (fresh text), batch Duration column (measured),
   badges (derived), project load (no estimate persisted).
5. `duplicate_job` strips `expected_duration` **deliberately**
   (documented P3.44.6 manual/structural split) — NOT touched.

### 1.4 Actual-vs-estimated surfaces

All measured surfaces (batch Duration column, version popovers,
history, transport, assemble rows/totals) show MEASURED values; the
estimate surfaces are the Preview stats line, the long-dialog headers
and the preflight texts. One mislabel found: the assemble dialog §41
comment/docstring said "estimated" over a sum of MEASURED durations
(user-visible string was neutral) — comment-only fix.

## 2. THE DURATION PIPELINE (source → estimate → split → part → generation → actual)

```
editor document (user text; blocks = spans with overrides)
  → _materialize_block_metadata (split-time; block sfx/pause metadata
    → in-text markers on COPIES; raw mode exempt)
  → sentence_boundaries.split_sentence_units  [THE scanner: exact
    slices, marker shielding/attachment, NO sub-sentence API]
  → NarrationSplitter._group_sentence_units   [duration-target
    grouping (P3.45.2B); 400-char hard cap; oversized sentence → own
    part; speaker turn sentence-grouped (P3.45.2B)]
  → SplitPart{ text=plain exact, prompt=compiled,
               estimated_duration=canonical(text) (P3.45.2B),
               char_count=len(prompt) }
  → LongNarrationDialog [headers: canonical estimate + chars on ONE
    basis, LIVE refresh on edit (P3.45.2B); Generate recomputes the
    estimate from the edited text (P3.45.2B)]
  → BatchJob{prompt (editable), expected_duration (= part estimate,
    recomputed on workspace prompt edit — P3.45.2B)}
  → GenerationRequest → Engine → model.generate_speech (25 fps token
    budget; no truncation metadata)
  → GenerationResult.output_duration (MEASURED, pre-append-silence)
  → AudioAsset/history/combined durations (MEASURED)
Guards: 2A preflight (pre-gen ceiling) + P3.27B R1/R2/R3 (post-gen).
```

Sources of truth per transition: the document string (editor) → the
materialized text (splitter) → the exact slices (scanner) → the joined
groups (splitter) → the SplitPart fields (dialog) → the job workspace
(batch) → the request (engine) → the waveform (model) → the measured
durations (results). The canonical ESTIMATE is
`engine/duration_estimation.estimate_speech_seconds` (P3.45.2B).

## 3. THE CANONICAL ESTIMATOR

`engine/duration_estimation.py` (new, dependency-free, pure):

- `ESTIMATED_CHARS_PER_SECOND = 15.0` — the ONE constant. The
  historical heuristic, **uncalibrated** (no measurement corpus; the
  P3.45.2B task explicitly forbids recalibration without evidence).
  `engine/output_guard` imports this object (its local name is an
  alias; identity pinned by tests) — no second constant exists.
- `estimate_speech_seconds(text)` — the ONE function. Semantics: an
  ESTIMATE of speech seconds at the canonical rate; never a
  measurement, never a limit. `None`/empty → 0.0.

Intentionally NOT merged: the hard output ceiling
(`output_guard`: `max_new_tokens / 25 fps`) and every measured
duration (results) are different quantities in separate modules with
explicit names.

Text-basis contract (one basis per site, each estimating the text the
site owns): splitter → plain part text; Preview stats → the document's
plain text (basis FIXED this round — was the tokenized prompt);
preflight → the caller-supplied request basis, reported in the result.

## 4. THE 20–25 SECOND PRODUCT TARGET

With the canonical rate: 20 s ≈ 300 chars, 25 s ≈ 375 chars
(directional). The splitter expresses the target in SECONDS and
derives chars through the canonical rate — never a bare char constant:

```python
TARGET_PART_SECONDS = 20.0                       # window lower bound
TARGET_PART_CHARS  = int(20.0 * 15.0) = 300      # close threshold
MAX_CHARS          = 400                         # hard cap (unchanged)
```

Grouping rule (P3.45.2B): accumulate sentences; CLOSE a group when
its exact joined length ≥ 300 **and** at least 300 chars of speech
remain after it (lookahead); the hard cap (400) flushes a group when
the next sentence would not fit. Consequences:

- Typical parts: 300–400 chars = **20–26.7 s** (mean ≈ 23 s) —
  "approximately 20 to 25 seconds". The overshoot past 25 s is
  bounded by one sentence and the unchanged 400-char cap.
- Any text ≤ 400 chars → **exactly ONE part** (the lookahead merges
  small remainders — no silly short tails mid-text; this also subsumes
  the historical MIN_CHARS rule).
- A single sentence > 400 chars → ONE oversized part, never cut (no
  sub-sentence mechanism exists; text integrity beats the target).
- This is a **PRODUCT TARGET**, not a model hard limit, not a runtime
  limit, not a truncation guarantee. The real ceiling stays
  `max_new_tokens/25` in `engine/output_guard` (unchanged, 2A frozen:
  2457 safe / 2458 blocked / 2460@4100 warning — byte-identical).

The `/15` heuristic was NOT recalibrated. `MAX_CHARS=400` was not
"replaced by 300/375" — it remains the hard cap; 300 is the derived
close threshold.

## 5. LONG SINGLE SENTENCE

Audited: no semantically safe subdivision mechanism exists
(`sentence_boundaries` = sentence-level units only; no comma/clause
API anywhere). Therefore: one oversized part, no character slicing,
the canonical estimate on the whole sentence, honestly surfaced by the
2A preflight (above-target parts ≤ 2457 chars are SAFE vs the ceiling;
above-ceiling parts are BLOCKED with the existing confirmation). Known
limitation, documented — a future sub-sentence mechanism would be a
separate, explicitly designed task.

## 6. SPEAKER TURNS

A turn's text under a `$SPEAKER:` declaration is ordinary narration;
the declaration itself is a TURN boundary living OUTSIDE the part text.
Splitting the text at sentence boundaries therefore preserves speaker
identity, marker position, source text and part ordering **by
construction**. Implementation (`_split_with_speakers._flush`): the
turn text is sentence-grouped with the SAME scanner + grouping as
plain narration; every group becomes a SplitPart replicating the
turn's speaker / `character_id` / effective semantic state; each part
gets its own complete compiled prompt (independent generation call).
`part_of_block`/`total_parts_in_block` carry turn-internal numbering
(display metadata only). Slot identity is unaffected: speaker parts
are PLAIN slots (`slot_id = plain:{part_index}` — `materialize_expected_slots`);
no new identity system, no slot_id semantics change, no lineage change.
A turn consisting of a single oversized sentence remains ONE oversized
part (same fallback as §5). Per-TURN block-override resolution is
unchanged (a turn spanning multiple blocks resolves against the first
overlapping block — pre-existing documented limitation).

## 7. STALENESS FIXES (every stale path fixed)

| path | fix |
|---|---|
| Long-dialog part edit | live header refresh (`textChanged` → estimate + chars + re-classified 2A marker + aggregate total + limit-note visibility) AND `p.estimated_duration = estimate_speech_seconds(text)` at Generate → `BatchJob.expected_duration` fresh |
| Batch prompt edit | `JobEditDialog.apply_to_job` recomputes `expected_duration = estimate_speech_seconds(prompt)` (request-text basis of the workspace) |
| Regen after edit | preserved value is now the edited-time value (correct by construction) |
| Queue YAML round-trip | persists prompt + matching estimate (consistency pinned by test) |
| Editor text edit | Preview stats were already live; now on the canonical basis |

## 8. RELATIONSHIP TO P3.45.2A

The preflight consumes the canonical constant (imported — identity
pinned) and therefore agrees with the splitter wherever the input
semantics are the same (per part: `estimated_request_seconds ==
round(SplitPart.estimated_duration, 2)`). The 2A boundary arithmetic,
states, messages, call sites and text bases per site are UNCHANGED
(the long-dialog re-check stays on the edited part text — 2A's
documented choice; the token-inflation blind window at the exact
ceiling edge is a documented residual, magnitude ≈ the prepended-token
char overhead, only reachable by above-ceiling-sized parts). The
20–25 s target is compatible with the preflight: normal 2B parts
(300–400 chars) are deep SAFE; oversized single sentences keep their
2A markers/confirmations. The 2A "speaker bypass" test case (an
unpunctuated giant turn) still yields ONE part — the 2A suite is green
unmodified (42/42).

## 9. TESTS

New focused suite (33 tests, real application paths, house harness):
canonical estimator + constant identity + preflight consumption;
grouping target/lookahead/cap/oversized-fallback; exact-text and
boundary integrity; speaker-turn splitting (identity, ordering,
reconstruction, slots, state replication); Preview-stats basis;
dialog live refresh + emit recompute + end-to-end
`expected_duration`; batch edit recompute + YAML round-trip;
no-mutation invariants. Two existing P3.44.8 assertions that pinned
the retired sentence-count targeting were updated to the new product
semantics with justification comments (their PURPOSE — decimal
boundary integrity and exact-slice preservation — is unchanged and
amplified).

## 10. VALIDATION

Focused 33/33 **twice** (stable). Full battery in 6 foreground chunks:
1657 passed + 2 failures, BOTH proven pre-existing: SS-3 (the
preregistered P3.37 README test, fails identically on the pristine
tree) and `test_p3_44_4_batch_execution_run.py::
TestStopSemantics::test_regen_run_stop_leaves_others_pending`
(timing flake — proven failing 2/8 runs on the pristine `5a6e5f3`
tree via `git stash`, zero P3.45.2B files touched in that path).
`verify_compile` 151/151 PASS; `verify_architecture` PASS;
`verify_functional_integrity` 80/80 PASS; `verify_integration` FAILED
with a stop-semantics violation **proven identical on the pristine
tree** (pre-existing; newly observed because the 2A round did not run
this tool — recorded in OPEN_BUGS); launch smoke 11/11 on the real
entry path.

## 11. REMAINING RISKS (evidence-based only)

1. The `/15` heuristic can err in both directions (±10 s empirically)
   — every message discloses which number is an estimate.
2. The long-dialog Generate re-check basis (plain part text) can miss
   a token-inflated prompt exactly at the ceiling edge (≈1–6 s window)
   — only reachable by ~2400+-char parts, i.e. the pathological
   oversized cases.
3. Block SFX/pause metadata is materialised at split time: part
   estimates count marker chars, the Preview stats measure the editor
   text as seen (documented divergence, marker chars only).
4. Speaker turns spanning multiple blocks resolve overrides against
   the first overlapping block (pre-existing, unchanged).
5. End-of-text tail parts can be shorter than the target (the
   historical "unless at end" exemption; bounded by the cap).
6. `verify_integration` stop-semantics violation — pre-existing,
   proven at baseline, recorded in OPEN_BUGS.

## 12. FILES

- `engine/duration_estimation.py` (NEW — the canonical estimator)
- `engine/narration_splitter.py` (canonical estimates ×4; grouping
  retarget; speaker-turn splitting; docstrings)
- `engine/output_guard.py` (canonical constant import/alias)
- `ui/panels/narration_editor.py` (Preview stats basis fix)
- `ui/panels/long_narration_dialog.py` (live refresh + emit recompute)
- `ui/panels/batch_generation.py` (JobEditDialog recompute)
- `ui/panels/assemble_dialog.py` (§41 comment/docstring wording only)
- `tests/test_p3_45_2b_duration_splitter_consistency.py` (NEW)
- `tests/test_p3_44_8_sentence_splitter_integrity.py` (2 assertions
  updated to the new product semantics)
- Docs: this file, `DEVELOPMENT_LOG.txt`, `docs/governance/OPEN_BUGS.md`

FROZEN SURFACES untouched: slot_id / generation_run / part_version /
append-only Asset lineage / Batch editable workspace / Scene Combine /
Manual Concatenate / P3.44.9 block ranges / P3.44.9.1 gutter geometry /
P3.45.1 block status system / P3.45.2A preflight architecture. No
GenerationPlan, no fingerprinting, no STALE state, no session locking,
no timers/polling, no parallel engines. P3.45.3 NOT started.
