# P3.45.6 Addendum — Batch Queue Removal vs Scene Combine Integrity

Investigation round: P3.45.6 ADDITIONAL REQUIRED INVESTIGATION
(added after the five ordered phases were completed and pushed).
Baseline: `main @ 3fcd0d2` (P3.45.6 final doc commits) — implementation
continues on this tree.

---

## §0 Observed evidence (the reported state)

* The Batch Generation UI displays **15 visible rows**.
* The coverage indicator reads **PARTIAL (15/22)**.
* **Combine Scene** rejects the operation: *"Block 16 through Block 22
  have no generated audio"* (one blocker bullet per slot, labels
  `'Block 16'` … `'Block 22'`).

## §1 The complete state chain (proven from code at `3fcd0d2`)

| # | Link | Where | Truth |
|---|---|---|---|
| 1 | **Scene blocks (authoritative membership)** | `NarrationBlockManager._blocks` (`PromptBlock` objects, stable ids per P3.44.9; exact-position feed per P3.45.6 Phase 2) | Live model, updated on every text edit; deletion fully covered by the P3.44.9 transformer (`_apply_edit_to_blocks` — fully-replaced blocks are REMOVED, zero-range blocks are not blocks). |
| 2 | **Text-range updates on deletion** | Same transformer; SFX/pause insertions ride the same mapping and die with their block | Slots carry **no text ranges** (`materialize_expected_slots` records id/label/part context only), so no edit path can transform them — by design (§3). |
| 3 | **Batch queue rows** | `BatchManager._jobs`; one row per `BatchJob` (`_rebuild_table`) | Created wholesale by Generate Long (`bm.clear_all()` + one job per splitter part, `main_window.py:3557-3699`); **Remove = `remove_job(idx)` deletes the job object ONLY** (`batch_manager.py:426-429`; the manager holds no Scene reference). |
| 4 | **Slots + block→slot identity** | `Scene.expected_audio_slots`, sole production writer Generate Long (`main_window.py:3566-3569`, wholesale replace); `slot_id = "{block_id}:{part_of_block}"` / `"plain:{part_index}"` (`audio_provenance.py:120-128`) | A frozen, *recorded* structure snapshot — NOT derived from the live blocks between structure events (P3.44.6 §"preserved": text edits, split/merge, re-detect, queue edits/load never rewrite it). |
| 5 | **Generated audio provenance** | `Scene.audio_assets`, append-only; ONE writer `register_generation_result`; identity-first join `slot_of_asset` (exact `slot_id` match, never positional for modern assets) | Never pruned. An asset whose slot vanished is orphaned-but-preserved (coverage/Combine correctly stop counting it; rows render "Unlinked part"). |
| 6 | **Combine eligibility + output selection** | `resolved_slot_sources(scene)` enumerates **exactly `expected_audio_slots`** in slot order; uncovered slot → fail-closed blocker; per-slot audio = explicit selection else latest version | Queue rows are IRRELEVANT to Combine: the 15 visible rows and the 22-slot structure are two disjoint inputs. |

## §2 Root cause — the exact state transition that creates the mismatch

The queue and the recorded slot structure are **decoupled structures
that only ever change together at the Generate Long structure event**
(`bm.clear_all()` + `scene.expected_audio_slots = slots`, atomic in
`_start_long_narration`). Every other mutation changes exactly ONE side:

* **Queue-only**: Remove / Edit-swap / Duplicate / Load Queue / Clear —
  none touches blocks, slots, assets, or coverage.
* **Structure-side**: text/block deletion, Re-detect (id replacement) —
  none touches the queue or the recorded slots (documented model:
  OPEN_BUGS P3.45.1/P3.45.3 residuals; P3.44.6 design §"preserved").

For the reported state the consistent history is: Generate Long
materialised **22 slots + 22 jobs**; 15 parts were generated (15 assets);
the 7 un-generated rows were **removed from the queue** (or the queue was
otherwise replaced) while the 22-slot structure and its 15 assets
persisted. Result: 15 rows / coverage 15/22 / Combine demanding the audio
of 7 slots whose regeneration path **no longer exists in the queue**.
Two aggravating defects made this a trap rather than an honest state:

1. **The Combine remedy was unreachable/misleading.** "Generate the
   missing parts first" — impossible when the rows were removed; and when
   the blocks were *deleted from the text* (the sibling state), Combine
   demanded audio for content that no longer exists, with the same
   misleading remedy. Nothing distinguished *legitimate missing audio*
   (block still in the current text) from *stale structure references*
   (block deleted / re-detected).
2. **The queue showed no trace of the missing parts.** 15 rows looked
   like a complete workspace; nothing surfaced that 7 expected parts had
   neither audio nor a row, and the only recovery (a fresh Generate Long)
   wipes the whole queue workspace.

## §3 The intended contract, established from code/tests/docs

* **Remove = a queue-workspace operation.** It removes the generation
  JOB only. It does NOT remove the Scene block, does NOT invalidate the
  expected slot, does NOT delete or unlink generated audio. Evidence:
  `remove_job` body (`batch_manager.py:426-429`, no Scene reference);
  P3.44.6 design record §"preserved: queue edits/reorder/load"; P3.45.3
  design §5 ("Queue position is an execution/workspace concern …
  Structural order comes from `expected_audio_slots`"); pinned by
  `test_p3_45_3_batch_block_identity.py` (queue YAML round-trips,
  duplicate-strips-identity) and the P3.45.1 sync-purity tests.
* **Combine validates the recorded structure, fail-closed, never
  silently skipping** (P3.28 §13/§52; pinned by
  `test_p3_28 TestSceneCombine`, `test_p3_44_6 TestCombineResolution`).
  This is CORRECT and is preserved untouched by this round.
* **Slots change ONLY at structure events** (Generate Long). This round
  does NOT introduce a second slot writer, does NOT reconcile/mutate
  slots on load, deletion, or queue edits (that would be exactly the
  "blindly deleting historical slot data" the task forbids, and it would
  false-positive after Re-detect, which legitimately replaces block ids
  while assets still join the recorded slots).

## §4 The fix — three additive, minimal pieces

### F1 — truthful blocker classification (engine, read-only)

`resolved_slot_sources(scene, app_root, current_block_ids=None)`:
for an **uncovered** slot whose `block_id` is truthy and NOT in
`current_block_ids`, the blocker becomes the stale-structure message
with the correct remedy; every other case keeps the exact legacy message
("has no generated audio yet"). `current_block_ids=None` (the default)
keeps byte-identical legacy behavior for every existing caller/test.

`MainWindow._combine_scene` derives the id set:
* active scene → the **live editor block model** (positive evidence,
  even when empty — all blocks deleted means all block slots are stale);
* non-active scene (by-id combine) → the persisted
  `scene.narration_blocks` ids; an empty persisted list → `None`
  (no trustworthy snapshot → legacy messages).

Covered slots are NEVER classified (an asset resolves → source, no
blocker), so the currently-working flow *re-detect + full coverage →
Combine succeeds* is structurally unaffected.

### F2 — missing-parts surface + Re-add Missing (workspace repair)

Engine helper `missing_expected_parts(scene, job_slot_ids)`: the
expected slots that have **neither a joined asset nor a queue row** —
exactly the slots whose regeneration path no longer exists in the queue
while Combine still requires them. Pure derivation.

Batch dialog (scene mode): the coverage header appends
`— {N} expected part(s) missing from the queue` when N > 0, and a new
**[Re-add Missing]** button (header row, next to All/None) emits
`readd_missing_requested` → `MainWindow._on_readd_missing_parts`.

`_on_readd_missing_parts` repairs **the queue only**:
1. same guards as the other structure-level actions (idle);
2. the **same `validate_block_ranges` fail-closed gate** as Generate Long
   (P3.45.6 Phase 2) — no part from invalid ranges;
3. the **same `NarrationSplitter.split` pass** over the CURRENT text +
   CURRENT blocks with CURRENT settings (single authoritative engine —
   no second splitter, no second prompt compiler);
4. matches current parts to missing slots by **EXACT slot identity**
   (`block_slot_id(block_id, part_of_block)` — the same derivation
   `materialize_expected_slots` uses);
5. builds each job through the **same construction Generate Long uses**
   (factored verbatim into `_build_long_part_job`: voice precedence
   Character → speaker map → default; asset-aware `next_slot_version`
   naming; full provenance slot_id / source_block_id / part_index (the
   RECORDED structure position) / part_version / generation_run (one
   fresh run for the re-add activation) / character_id / speaker);
6. **appends** via `bm.add_job` — never clears the queue, never touches
   slots/assets/versions/selections;
7. reports honestly: unmatched slots (block deleted → stale; part
   decomposition changed; **plain slots**, which have positional identity
   only and can never be PROVEN to be the same text) are listed with the
   Generate Long remedy, never forced into a job.

Because re-add only ever targets slots with **no other job and no
asset**, there is no identity ambiguity — this is deliberately distinct
from Duplicate, which strips slot identity precisely because a duplicate
would generate a second audio for a slot that already has a
regeneration path (P3.44.6).

### F3 — Remove semantics made explicit at the decision point

`_on_remove` shows a confirmation **only** when the removed job is
structural (its `slot_id` is in the Scene's expected structure) AND its
slot is uncovered — the exact case where removal removes the last
regeneration path while Combine still requires the audio. The dialog
states the contract verbatim: removes the generation JOB only; the
Block, the slot and any generated audio are NOT removed; Combine will
report the part as missing; Re-add Missing restores the row later.
Covered-slot removal and manual-job removal stay silent (no Combine
consequence).

## §5 Frozen contracts honoured

`slot_id` derivation and identity-first join, `generation_run` scoping,
`part_version` allocation (asset-aware `next_slot_version`), append-only
`AudioAsset` lineage, P3.44.9 range transformer + P3.45.6 exact feed and
`validate_block_ranges` gate, P3.44.9.1 gutter geometry, P3.45.1 status
semantics, P3.45.2A preflight (applies automatically to re-added jobs at
GENERATE CHECKED), P3.45.2B duration splitting (the same splitter pass),
P3.45.3 batch identity (job-keyed check states; re-added jobs are keyed
by slot like every structural job), P3.45.5 stop contract, **Scene
Combine source order** (unchanged — classification only affects blocker
message text), Manual Concatenate queue order. No second slot writer, no
second splitter engine, no second token engine, no load-time mutation.

## §6 Regression scenarios (task lettering → tests)

* **A** Remove a row while the source block exists → Combine reports the
  genuinely missing audio (fail-closed, no silent omission) — and the
  missing surface + Re-add restore the path.
* **B** Delete a block from the text → no stale slot remains a
  *required output*: Combine classifies it as a stale structure reference
  with the Generate Long remedy (not "missing audio"); Re-add refuses it;
  the documented data model's snapshot preservation is respected (no
  mutation) while the state is clearly signalled.
* **C** Delete several blocks, regenerate (Generate Long), combine →
  the missing-block list matches the current authoritative structure;
  surviving assets still join (ids stable through deletion — no loss).
* **D** Delete and recreate text containing repeated phrases → obsolete
  identities cannot associate with the new text (exact-identity re-add
  matching + identity-first join; re-typed identical text under a NEW
  block id never matches the old slot).
* **E** Load a project with stale block/slot references → state is
  preserved intact (no destructive reconciliation), valid audio and
  provenance survive the round-trip, and consumption points classify
  honestly (coverage header + Combine blockers + Unlinked-part chips).
* **Screenshot state** reproduced deterministically at engine level
  (22 slots / 15 assets / 15 job rows → PARTIAL (15/22) + 7 blockers
  naming Block 16..22) and at real-widget level (real MainWindow +
  real batch dialog: 15 table rows, header text, missing count, Re-add
  enabled), then driven through Re-add → generate → Combine success.

## §7 Documented decisions and residuals

* **No load-time slot reconciliation.** "Reconciled safely" (scenario E)
  is interpreted as: nothing is lost or mutated at load; staleness is
  diagnosed at consumption time (F1/F2 surfaces) with the documented
  remedy. A write-time reconciliation would need to distinguish
  block-deletion from re-detect id replacement — undecidable without
  re-splitting (slots carry no text), and wrong-guessing destroys valid
  coverage. The task explicitly forbids blind deletion; a user-confirmed
  destructive cleanup is NOT added (Generate Long is the sanctioned
  structure event and is lossless for surviving ids).
* **Plain slots are not re-addable.** Positional identity cannot prove
  the current part is the same text after any edit (scenario D risk);
  the honest report sends them to Generate Long.
* **Active-scene + empty live block model** (all blocks deleted) → all
  block slots classify as stale (positive empty-structure evidence).
  Non-active scenes with an empty persisted block list fall back to
  legacy messages (no trustworthy snapshot).
* **Part-decomposition drift** (block survives, duration split changed)
  → the affected sub-slot is reported as not re-addable; the remedy is
  Generate Long. Deliberate: re-adding a sub-part with drifted text under
  the old `part_of_block` identity would mislabel provenance.
* The pre-existing P3.45.3 residual (Re-detect replaces ids without
  re-materialising slots — "Unlinked part" chips) remains OPEN and
  untouched: this round does not change slot writing.

## §8 Test file

`tests/test_p3_45_6_batch_removal_combine_integrity.py` — engine-level
pure tests (missing-parts definition, classification matrix, order
stability, back-compat default), real-MainWindow combine-classification
tests, real-dialog Remove-semantics tests, and the end-to-end
screenshot-state → Re-add → generate → Combine golden path with a faked
model boundary (house pattern) and REAL wav outputs.
