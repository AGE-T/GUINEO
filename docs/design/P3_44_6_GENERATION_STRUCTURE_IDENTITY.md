# P3.44.6 — Generation Structure Integrity: Design + Proof Record

**Phase:** P3.44.6 (DESIGN → PROOF → **IMPLEMENTED** — see the
Implementation Record at the end of this file)
**Baseline:** P3.44.5-D (commit `5354fc9`, clean tree, 284-file deliverable
`GUINEO_P3.44.5_current.zip`)
**Scope discipline:** the design + proof round was investigation-only
(production changes: NONE, per the roadmap-correction stop condition);
the implementation round then applied exactly the proven minimal
mechanism. Every claim below is derived from the current code and, where
marked **[PROVEN]**, verified at runtime against the real functions
(`ss/p3446_probe_identity.py`, pure Python, no mocks of the join under
test).

---

## A. Why the old roadmap interpretation was too broad

The earlier interpretation — "P3.44.6 = implement fingerprint +
`source_structure_fingerprint` + STALE + GenerationPlan/Rebuild workflow" —
assumed Batch Generation is a disposable queue over an immutable source.
The code shows it is not. `BatchManager` + `BatchGenerationDialog` form a
persistent **generation workspace**: editable prompts
(`JobEditDialog` → `job.prompt`), per-row editing, queue ordering
(`move_job`), Start/Stop/Pause, per-part playback, per-part regeneration
as a NEW VERSION of the SAME slot (`_on_batch_regen`,
`_on_generate_selected`), multiple result versions with explicit
selection (`Scene.selected_block_audio`), Scene Combine, Manual
Concatenate, and queue save/load to YAML (`save_to_file`/`load_from_file`
persist the full `BatchJob` including `slot_id`).

Against that workspace model, a structure **fingerprint + STALE gate**
cannot "simply be implemented", for three concrete reasons:

1. **It conflicts with the locked product rule** (P3.28 design record §0,
   encoded in `engine/audio_provenance.py`'s module docstring): the
   system NEVER decides whether text/prompt/semantics changed; there is
   no text diffing, no prompt hashing, no automatic invalidation. A
   STALE gate that fires on structure change and demands
   Rebuild/Keep/Cancel re-introduces exactly the automatic-invalidation
   semantics that were deliberately rejected.
2. **It conflates two identities the current model correctly separates.**
   Structure identity ("which slot does this result belong to?") and
   generation-settings identity ("how was this attempt produced?") are
   distinct by design: same structure + changed Top-K/seed/prompt is a
   legitimate NEW VERSION of the SAME slot (`next_slot_version`,
   `allocate_generation_run` per activation — §7 of the directive). A
   monolithic fingerprint over the whole structure would invalidate
   valid version chains (violating §16 of the directive: "identity
   safety, not blanket invalidation").
3. **It presumes the missing thing is an architecture.** The
   investigation shows the structure itself is ALREADY materialised,
   identity-addressed and persisted (`Scene.expected_audio_slots`, slot
   identity `"{block_id}:{part_of_block}"` / `"plain:{part_index}"`).
   What is missing is ONE field on the result side: the asset never
   records which slot it was generated for, so membership is re-derived
   from an unstable coordinate (global queue position). That is a join
   defect, not an architecture gap.

Session locking / sub-sessions were already rejected upstream (directive
§2) and are not revisited here: the Session remains an editable
workspace; nothing in the proposed mechanism requires fragmenting it.

---

## B. Current Batch architecture (as traced)

```
Blocks (editor = source of truth; PromptBlock ids + offsets)
   │  on_text_changed / split_block / merge_with_above / re-detect
   │  → block ids SURVIVE text edits; split keeps the first half's id;
   │    merge keeps the upper block's id; Re-detect REPLACES ALL ids
   ▼
[Generate Long activation]  ui/main_window.py:_start_long_narration
   ├─ bm.clear_all()                       ← the workspace queue is wiped
   ├─ scene.expected_audio_slots =
   │      materialize_expected_slots(parts) ← THE structure event (the
   │      ONLY production write site, mw:3410-3413; parts = the EDITED
   │      parts from LongNarrationDialog, not the raw splitter output)
   ├─ generation_run = allocate_generation_run(scene)   "{scene8}-r{NNN}"
   ├─ per part: BatchJob(slot_id, part_index=GLOBAL i+1,
   │        part_version=next_slot_version(scene, slot_id, …),
   │        source_block_id, generation_run, prompt, voice, params)
   └─ scene → GENERATING; dialog re-presented (P3.44.5 reuse policy)
   ▼
Batch workspace (BatchManager, thread-safe; BatchJob = editable row)
   │  prompt edits, param edits, queue reorder, duplicate, regen,
   │  review-mode selection — none of these touch the Scene structure
   ▼
Generation (per job: to_request() → Engine → GenerationResult)
   │  request carries block_id / part_index / part_version /
   │  generation_run — but NOT slot_id (dropped in to_request!)
   ▼
THE ONE WRITER  register_generation_result (audio_provenance.py:926)
   ├─ appends AudioAsset{block_id, part_index, part_version,
   │      generation_run}  ← NO slot_id, NO part_of_block
   └─ recomputes derived scene status
   ▼
DERIVED membership: slot_of_asset(asset, scene)  (the JOIN)
   │  current rule: asset.part_index (GLOBAL position) selects the
   │  candidate slot, then block_id must agree (P3.44.5 §8)
   ▼
assets_for_slot → coverage / versions / next_slot_version /
resolved_asset_for_slot (selection else latest) →
   ├─ Scene Combine (resolved_slot_sources, slot order; lineage snapshot
   │   in combined_outputs.sources; staleness R2 = slot-set equality,
   │   R1 = resolved-asset identity)
   └─ resolve_scene_output → Project Assembly (Assemble dialog /
       export) — consumes ONLY the resolved Scene output, never Batch
       internals (directive §11 satisfied today)

Manual Concatenate is a separate queue-order workflow over
`job.output_path` — it never touches slots, versions or the join
(directive §10: operationally unchanged by anything proposed here).
```

Stage ownership / identity summary:

| Stage | Owner | Persistent form | Identity | Mutable | Immutable |
|---|---|---|---|---|---|
| Blocks | NarrationBlockManager + editor | `Scene.narration_blocks` (ids + offsets) | `PromptBlock.id` (uuid) | text, offsets, overrides, ids (via ops above) | — |
| SplitParts | NarrationSplitter (transient) | none | `source_block_id` + `part_of_block` (+ global order) | — | whole run |
| expected_audio_slots | Scene | `project.json` | `slot_id` = `{block_id}:{part_of_block}` / `plain:{part_index}` | replaced wholesale at each Generate Long | between replacements |
| BatchJob | BatchManager | queue YAML (`slot_id` persisted) | queue position + carried snapshot fields | prompt, params, status, order, version on regen | slot_id/source_block_id (unless job duplicated) |
| generation_run | metadata only (no registry) | asset/history/combined lineage | `{scene8}-r{NNN}` (max+1) | — | allocated per activation |
| AudioAsset | Scene (append-only) | `project.json` | asset `id`; **slot membership DERIVED by the join** | never mutated | the whole entry |
| part_version | derived allocation | asset + filename `_Long_vNN_` | per-slot monotonic max+1, disk-guarded | — | allocated per generation |
| combined_outputs | Scene | `project.json` | entry `id` + `version`; per-slot lineage in `sources` | append-only; `selected_output` may be cleared by fresh Combine (R3) | entries |

---

## C. Current identity model (exact fields)

- **Slot side** (`expected_audio_slots` entry):
  `slot_id`, `block_id`, `block_label`, `part_index` (global),
  `part_of_block`, `speaker`, `character_id`.
- **Job side** (`BatchJob`): `slot_id`, `source_block_id`,
  `part_index` (global), `part_version`, `generation_run` (+ prompt,
  params, voice, scene/character context).
- **Result side** (`AudioAsset`): `block_id`, `part_index` (global),
  `part_version`, `generation_run`. **No `slot_id`, no `part_of_block`.**
- **Run**: `generation_run` string on job/asset/history/combined entries;
  reconstructible by grouping (no registry).
- **Version**: `part_version` int; filename slot `_Long_vNN_` +
  `Part_{NNN}` (global position); disk-guard prevents overwrite.
- **Selection**: `Scene.selected_block_audio{slot_id → asset_id}`;
  `Scene.selected_output{kind,id}`.
- **Lineage snapshot**: `combined_outputs[].sources[]` with `slot_id`,
  `asset_id`, `part_version` — the ONLY place a historical structure is
  recorded.

**The gap:** the join key that the BatchJob docstring itself calls "the
join key between the batch queue and the Scene's
expected_audio_slots / audio_assets" (`slot_id`) is dropped on the way to
the asset (`to_request()` does not forward it;
`register_generation_result` does not stamp it). The join therefore
re-derives membership from `part_index` — a GLOBAL queue position that is
not a stable property of the part.

---

## D. Mutation matrix (derived from code; runs/versions "on activation")

| Operation | Source structure changes? | Structure identity (slots) changes? | New generation_run? | New part_version? | New result? |
|---|---|---|---|---|---|
| Blocks text edit | deferred — only at next Generate Long (splitter not run on keystroke) | NO (slots untouched; ids survive `on_text_changed`) | no | no | no |
| Block split | deferred (original id kept on first half, NEW id on second half) | NO now; replaced at next GL | no | no | no |
| Block merge | deferred (upper id kept, removed id dies) | NO now; replaced at next GL | no | no | no |
| Block reorder | deferred (ids survive, offsets/order change) | NO now; replaced at next GL (slot ORDER changes; per-block slot_ids stable) | no | no | no |
| Re-detect | YES, immediately — ALL block ids replaced (`_transfer_overrides` never transfers id) | NO — `expected_audio_slots` keeps the OLD block ids (decoupled snapshot; docstring claims otherwise — see §J) | no | no | no |
| Batch prompt edit | NO | NO | on regen: YES | on regen: YES | on regen: YES (same slot) |
| Top-K change | NO (generation setting) | NO | on regen: YES | on regen: YES | on regen: YES (same slot) |
| Top-P change | NO | NO | same as above | same | same |
| temperature change | NO | NO | same | same | same |
| seed change | NO | NO | same | same | same |
| per-part regenerate | NO | NO | YES (`allocate_generation_run`) | YES (`next_slot_version`, asset-aware) | YES (append; same slot) |
| Batch duplicate | NO | NO | when run: run=None on the clone | clone has none | YES, provenance-less asset (duplicate_job strips part/slot fields; joins nothing) |
| queue reorder | NO | NO | no | no | no (affects Manual Concatenate order only; Combine uses slot order) |
| version selection | NO | NO | no | no | no (changes RESOLUTION only; staleness R1 reacts) |
| Combine | NO | NO | YES (combine event allocates a run id) | combined output version (max+1) | YES (combined_outputs entry + history, not an AudioAsset) |
| Generate Long re-run | YES (splitter re-run on current blocks) | YES — wholesale replacement at mw:3413 (+ `bm.clear_all()`) | YES | per-slot asset-aware restart | YES (per generated part) |

---

## E. Smallest viable solution — choice with evidence

**Chosen: C — a minimal generation_structure identifier.** Concretely:
the **`slot_id` that already exists** on the slot side and on the
`BatchJob` side is carried through `to_request()` → `GenerationRequest` →
`GenerationResult` → `register_generation_result()` → `AudioAsset`, and
`slot_of_asset` joins **identity-first**:

1. asset carries `slot_id` → membership = the current slot with that
   exact id (slot identity = block_id + part_of_block); slot gone →
   `None` (the part no longer exists in the current structure);
2. asset without `slot_id` (all pre-P3.44.6 assets) → the P3.44.5
   positional+block join, **verbatim** (documented compatibility path).

**Why not the others (all [PROVEN] by probe):**

- **A — existing fields + stricter invariant: insufficient.** The needed
  invariant is "membership is a function of the part's own coordinates",
  but the asset's only coordinate besides `block_id` is the global
  `part_index`, which changes under upstream edits. Probes 2/3 show the
  same slot (`blkA:1` / `blkZ:1`) losing its genuinely valid audio purely
  by position shift — no invariant over the existing fields can restore
  it, and no invariant can detect the decomposition swap of Probe 1 Q2.
- **B — structure revision counter: sufficient but too blunt.** A
  revision field bumped at each materialisation would reject ALL assets
  from prior structures — including genuinely reusable ones (reorder,
  growth). That is precisely the blanket invalidation §16 forbids, and it
  destroys valid version lineage.
- **D — GenerationPlan/snapshot: unnecessary.** The structure is already
  materialised, identity-addressed and persisted; historical structures
  already exist as lineage snapshots in `combined_outputs.sources`. D
  adds architecture without adding discriminative power over C for the
  proven problem. (Deferred with Batch V2 — §I.)

**Evidence (runtime, real functions — `ss/p3446_probe_identity.py`):**

| Scenario | Current join | Identity-first join |
|---|---|---|
| §15 S1→S2: old A2 (part vanished) | `None` ✓ (P3.44.5) | `None` ✓ |
| §15 S1→S2: old B1 vs new B:2 | **joins `blkB:2` — FALSE POSITIVE** (old audio counts as the new part's coverage; Combine resolves it) | joins `blkB:1` — its TRUE slot; `blkB:2` correctly uncovered |
| Reorder: same slot, shifted position | `None` — coverage 0/2, valid audio unreachable | slot covered; valid reuse restored |
| Within-block growth: unchanged slot shifted | `None` — false negative | slot covered |
| Legacy asset (no slot_id) | positional join | identical result (fallback verbatim) |

The §15 test case resolves exactly as demanded: no old result is ever
attributed to a *differently-bounded* part; a result that genuinely
belongs to the same slot coordinates survives; text staleness within a
surviving slot remains the user's call (P3.28 §0 product rule — the
system's proof standard is structural identity, not text equivalence).

---

## F. Exact lifecycle of the chosen identity (proposed mechanism)

- **created:** `slot_id` at `materialize_expected_slots` (Generate Long
  activation; also derived per part by the splitter's
  `source_block_id`+`part_of_block`). Asset-side: stamped by
  `register_generation_result` from the result passthrough.
- **copied:** slot → `BatchJob` (mw:3541) → `to_request()` → engine →
  `GenerationResult` → asset. (Today the chain stops at the request:
  `to_request` forwards block_id/run/part fields but drops `slot_id`.)
- **preserved:** block text edits, split/merge (per id-survival rules),
  re-detect (stale-but-persisted snapshot), queue edits/reorder/load,
  prompt/parameter edits, regeneration (new version, same slot id),
  version selection, Combine, save/load. Never rewritten anywhere.
- **replaced:** wholesale at the next Generate Long activation
  (`scene.expected_audio_slots = slots`, mw:3413, with `bm.clear_all()`).
  Continuity of an individual `slot_id` across replacement = its
  `block_id` survived AND its `part_of_block` position survived.
- **persisted:** `project.json` via `Scene.to_dict` — slots, assets
  (additive `slot_id` key in the plain dicts), `selected_block_audio`,
  `combined_outputs` lineage; queue YAML already persists
  `job.slot_id`. Tolerant readers everywhere (`_as_dict_list`,
  `from_dict` defaults) — legacy files import unchanged.
- **consumed:** `slot_of_asset` (identity-first) → `assets_for_slot` →
  coverage / versions / `next_slot_version` /
  `resolved_asset_for_slot` → Combine sources → staleness R1/R2 →
  `resolve_scene_output` → Project Assembly; batch dialog coverage
  pills, versions menu, combine-button enablement.

**Persistence test (§18, reasoned against the round-trip code):** every
consumed surface reads persisted state only (`project.json` via
`Scene.from_dict` tolerant readers; queue YAML via `BatchJob.from_dict`
which already restores `slot_id`). The added asset key is a plain dict
entry — it survives save/close/reopen/reload scene/reopen Batch without
any new infrastructure. The join itself is a pure function over that
persisted state; nothing memory-only is involved.

---

## G. Batch compatibility proof (capability by capability)

- **prompt edit:** orthogonal to identity; regeneration = same slot, new
  version/run. `JobEditDialog` round-trips jobs via
  `to_dict/from_dict`, which preserves `slot_id`. ✓
- **parameter changes (Top-K/Top-P/temperature/seed):** generation
  settings — never touch structure; new attempt = new version of the
  same slot. The identity-first join keeps this exact semantics. ✓
- **per-part regeneration:** `next_slot_version(scene, job.slot_id)` —
  identity join makes the version lineage position-independent (a
  shifted-but-valid slot keeps its version history instead of silently
  restarting at v01). ✓
- **playback:** plays `job.output_path` / resolved assets — no join
  dependency. ✓
- **versioning / version selection:** `assets_for_slot` +
  `selected_block_audio[slot_id]` — selection is already keyed by
  `slot_id`; identity join only changes WHICH assets qualify (dead-slot
  audio excluded, genuinely-owning audio included). Valid selections are
  never invalidated: a selection on a slot that still exists keeps
  resolving to the same asset. ✓
- **ordering:** queue order (Manual Concatenate) vs slot order (Combine)
  — both untouched. ✓
- **Combine:** consumes `resolved_slot_sources` over the same
  `expected_audio_slots`; staleness R2 (slot-set equality) unchanged;
  R1 unchanged. The join change alters membership exactly as intended
  (no old audio under a differently-bounded part). ✓
- **Manual Concatenate:** queue-order over `job.output_path`; zero slot
  involvement — **operationally unchanged**. ✓
- **save/load:** additive asset key; queue YAML already complete. ✓
- **existing Scene behaviour / Project Assembly:** consume
  `resolve_scene_output` downstream of the join; no interface changes. ✓
- **legacy scope:** assets without `slot_id` keep the documented
  P3.44.5 positional join verbatim (probe-verified identical). ✓

---

## H. Production changes

**Production changes: NONE (this round).**

The mechanism is designed, proven by runtime probe against the real
functions, and specified to patch-shape precision, but NOT implemented —
per the directive's stop condition ("the next implementation decision
will be made after reviewing this report"). The verified-no-regression
baseline of this round: working tree clean at `5354fc9`; P3.44.5
regression 29/29 OK, P3.28 provenance 65/65 OK, P3.44 playback + P3.31
scene-output 40/40 OK (§19 duty discharged).

**Proposed implementation shape (for the next decision), ≈5 files,
purely additive ≈30–40 lines + docs:**
`engine/models.py` (+`slot_id` on `GenerationRequest`/`GenerationResult`,
asset dict key), `engine/batch_manager.py` (`to_request` forwards
`slot_id`; persistence already complete), `engine/engine.py` (request →
result passthrough, mirroring `block_id`),
`engine/audio_provenance.py` (`register_generation_result` stamps
`asset["slot_id"]`; `slot_of_asset` gains the identity-first branch with
the verbatim legacy fallback), plus a new regression test file encoding
the probe scenarios as fail-on-old / pass-on-new detectors (the
P3.44.5 methodology), with the full provenance/batch battery as the
no-regression gate. No schema migration (plain dict keys, tolerant
readers). No BatchManager, Combine, persistence or Session changes.

---

## I. Deferred architecture

**Batch Generation Architecture V2 remains deferred.** Nothing in this
investigation proves the current architecture cannot support a stable
minimal solution — the opposite: the structure, slot identity, run
identity, version identity, selection and lineage layers all exist and
persist; the single missing link is one passthrough field. V2 (deeper
provenance, version lineage modelling, Batch/Blocks boundaries) stays a
separate future project. No refactor is smuggled into P3.44.6.

---

## J. Additional code-observed facts (recorded, NOT fixed this round)

1. **Doc/code mismatch:** `models.py:492` and `main_window.py:3370`
   claim slots are materialised "on Generate Long preview / Re-detect";
   in code there is exactly ONE production write site (Generate Long
   activation, mw:3410-3413). Re-detect does NOT materialise slots —
   after Re-detect the Scene carries a structure snapshot with dead
   block ids until the next Generate Long (coverage/Combine continue to
   resolve against the old snapshot consistently).
2. **Legacy synthesis asymmetry:** `slot_of_asset`'s legacy branch
   synthesises `{block_id}:{part_index}` using the GLOBAL position as
   `part_of_block` — inconsistent with the materialised convention
   (`{block_id}:{part_of_block}`). Reachable only for `scene=None` /
   unslotted scenes (documented legacy scope); harmless, recorded.
3. **`duplicate_job` strips part/slot provenance** (P3.23-era behaviour):
   a duplicated Generate Long row becomes a manual job; its asset
   carries no slot provenance and joins nothing. Documented behaviour,
   outside P3.44.6 scope.
4. **History entries** carry `block_id`/`part_index`/`part_version`/
   `generation_run` but not `slot_id` — same chain gap as the asset;
   would be closed by the same one-line forward in `to_request` +
   history's add() passthrough (optional, part of the proposed patch).
5. **Queue hand-over:** Generate Long re-run while a batch is in flight
   is handled by the P3.44.1 identity guard (orphaned in-flight result
   dropped) — the structure replacement point is safe today.

---

## Verification appendix (this round)

- Baseline: `git status` clean at `5354fc9`; no production file modified.
- Probe: `ss/p3446_probe_identity.py` (4 probes, real functions:
  `materialize_expected_slots`, `register_generation_result`,
  `slot_of_asset`, `assets_for_slot`, `is_slot_covered`,
  `coverage_counts`, `resolved_asset_for_slot`, `resolved_slot_sources`).
- Suites: `tests.test_p3_44_5_stabilisation_integrity` 29/29 OK;
  `tests.test_p3_28_audio_provenance` 65/65 OK;
  `tests.test_p3_44_batch_playback_integrity` +
  `tests.test_p3_31_scene_output_safety` 40/40 OK
  (offscreen Qt, PySide6 6.11.2).

**Stop condition answer:** the smallest stable mechanism that prevents
an old generation structure from being confused with a newly materialised
structure, while preserving the Batch as a fully editable generation
workspace, is **a single lightweight structure identifier: the existing
`slot_id`, carried through the request/result/asset chain and used as the
identity-first join key** — with the P3.44.5 positional join preserved
verbatim as the legacy fallback.

---

# IMPLEMENTATION RECORD (P3.44.6 implementation phase)

The design was implemented exactly as proven — the existing `slot_id`
preserved through the modern generation/result/asset path and made the
authoritative join key. No GenerationPlan, no fingerprint, no STALE
engine, no Session lock, no sub-session, no Batch/Combine/provenance
architecture change, no BatchManager rewrite. `duplicate_job` semantics
were investigated and validated (below).

## Final identity model (the authoritative statement)

1. **`slot_id` is THE authoritative modern structural identity**
   ("{block_id}:{part_of_block}" / "plain:{part_index}"). It is stamped
   onto the AudioAsset at generation time by the ONE writer
   (`register_generation_result`), from the request → result passthrough
   originating in `BatchJob.to_request()` — membership is never re-derived
   on the asset side.
2. **`part_index` remains global positional METADATA** — filenames,
   Batch display, and the documented legacy join still use it. Its meaning
   is unchanged; it is no longer the primary modern join key.
3. **`generation_run` represents a generation attempt/run**, never
   structure identity — a fresh run id is allocated per activation
   (batch start / regen / selective generation / combine), same slot.
4. **`part_version` represents result/version lineage**, never structure
   identity — same slot + new settings (temperature / top_k / top_p /
   seed / prompt) = a new version of the SAME slot.
5. **Modern asset matching is slot_id-first** (`slot_of_asset`): a modern
   asset belongs to the expected slot with EXACTLY its recorded slot_id,
   wherever that slot now sits (position-independent). A modern identity
   mismatch NEVER falls back to positional matching — that fallback was
   the proven defect class (false positives on changed decomposition,
   false negatives on reorder/growth).
6. **Legacy assets (no `slot_id` — everything recorded before P3.44.6)
   retain their documented fallback**: the P3.44.5 positional +
   block-aware join, preserved verbatim, including the `scene=None` /
   unslotted-scene synthesis scope. A modern asset in that scope returns
   its recorded identity verbatim (self-describing — never a positional
   synthesis of an identity it already carries).
7. **Batch prompt/settings edits do not change structure identity** —
   the job's `slot_id` survives the edit round-trip (`to_dict/from_dict`)
   and the queue YAML; a regenerated row is a new attempt/version of the
   same slot. The Batch remains a fully editable generation workspace.
8. **Manual Concatenate remains queue-order based** over
   `job.output_path` — zero slot involvement, operationally unchanged.
9. **Batch Generation Architecture V2 remains deferred** — nothing in
   this phase refactors the Batch engine, the Combine engine, the Session
   model or the persistence schema (the asset `slot_id` is a plain dict
   key that round-trips through the existing tolerant readers).

## `duplicate_job` conclusion (directive §5)

Duplication means **"create a new manual Batch job with the same
prompt/settings/context"** — NOT "another generation of the same
structural slot" (Case B, not Case A). The existing stripping of
structural provenance (part_index / part_version / source_block_id /
generation_run / slot_id) is therefore CORRECT and is now documented in
the method docstring as a deliberate decision: the duplicate's request
carries no slot identity, its registered asset joins NO expected slot
(coverage / versioning / Combine untouched), and it can never masquerade
as the original slot — while scene/speaker/character context is preserved
for History lineage (P3.23 §22). Copying `slot_id` blindly would make the
duplicate's asset silently join the original slot while sharing its
output filename — a version-lineage collision. Regression-tested in
`tests/test_p3_44_6_slot_identity.py` (TestBatchWorkspaceCompatibility:
`test_14*`).

## Exact production delta

- `engine/models.py` — additive `slot_id: Optional[str] = None` on
  `GenerationRequest`, `GenerationResult` and `AudioAsset` (+ its
  `to_dict`). No existing field renamed or redefined.
- `engine/batch_manager.py` — `to_request()` forwards `slot_id`
  (queue YAML persistence already existed); `duplicate_job` docstring
  records the explicit Case B decision (no behavioural change).
- `engine/engine.py` — the request → result passthrough forwards
  `slot_id` (mirroring `block_id`).
- `engine/audio_provenance.py` — `register_generation_result` stamps
  `asset["slot_id"]`; `slot_of_asset` gains the identity-first branch
  with the P3.44.5 legacy branch preserved verbatim;
  `is_complete_scene_asset` includes `slot_id` in its Part-proof
  (defensive airtight invariant — an asset carrying a slot identity is
  definitionally a Part).
- `tests/test_p3_44_6_slot_identity.py` — new permanent regression
  suite (40 tests) encoding the §18 matrix with fail-on-old /
  pass-on-new detectors and both-green controls.

## Verification (implementation round)

- New suite: **40/40** on the new code; **17 failed / 23 passed** on the
  old code via `git stash` (the 17 are exactly the identity-defect
  detectors; the 23 both-green are the valid-match / legacy / persistence
  controls).
- Full battery: **1363/1363** green (all suites except the documented
  known-bad tree-only `test_p3_30_ux_fixes.py`, excluded per the P3.44.5
  precedent — identical failures on pristine baseline).
- Launch smoke (real entry path): **11/11**.
- P3.44.5 stabilisation suite: 29/29 (non-regression confirmed); P3.28
  provenance: 65/65.
