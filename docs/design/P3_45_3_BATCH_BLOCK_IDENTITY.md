# P3.45.3 — Batch Block Identity

Task: "Batch Block Identity — code level audit, minimal implementation,
regression validation and fresh Git build." Baseline: main @ `5684dc6`
(P3.45.2B COMPLETE). This document records the identity audit, the
architectural decision, the presentation-only implementation and the
validation. The companion focused suite:
`tests/test_p3_45_3_batch_block_identity.py` (40 tests).

---

## 1. CODE-LEVEL AUDIT (verified at HEAD, not from prior reports)

The audit read the working tree at `5684dc6` end to end:
`engine/batch_manager.py` (complete), `ui/panels/batch_generation.py`
(complete), `engine/audio_provenance.py` (slot identity, registration,
resolution), `ui/main_window.py` (Generate Long job creation 3430-3693,
`_on_batch_regen` 3882-3968, `_on_generate_selected` 3974-4166, Combine
4291-4456, Concatenate 4588-4800), plus two parallel read-only agent
audits covering the Block model / naming / persistence / re-detect
surface and the registration / Combine / Concatenate / versioning
surface.

### 1.1 The identity that already existed (nothing was missing in the data)

| stage | identity carried | where |
|---|---|---|
| Block | uuid4[:12] id + optional user label | `engine/narration_blocks.py:57,70` |
| Structure event | slot dicts `{slot_id, block_id, block_label, part_index, part_of_block, speaker, character_id}` | `materialize_expected_slots` (`engine/audio_provenance.py:130`) |
| SplitPart | `source_block_id`, `part_of_block`, `block_label`, `speaker`, `character_id` | `engine/narration_splitter.py:63-78` |
| BatchJob | `slot_id`, `part_index`, `part_version`, `source_block_id`, `generation_run`, scene/character/speaker context | `engine/batch_manager.py:127-148` |
| GenerationRequest | all of the above forwarded | `to_request()` (`engine/batch_manager.py:227-266`, P3.44.6) |
| GenerationResult | all preserved through the engine facade | `engine/engine.py:322-353` |
| AudioAsset | stamped by the ONE writer | `register_generation_result` (`engine/audio_provenance.py:961-1033`) |
| Selection | `Scene.selected_block_audio` {slot_id → asset_id}, resolved selection→latest | `resolved_asset_for_slot` (:314-327) |
| Scene Combine | per-slot resolved sources in SLOT order | `resolved_slot_sources` (:697-740) |

**Identity is passed end to end and discarded nowhere structural.**
Queue YAML round-trips every identity field; project JSON round-trips
blocks by id with no positional reconstruction.

### 1.2 What the user could NOT see (the actual UX gap)

The Batch table's Name column showed `_display_name(job)` — the output
filename (e.g. `Eden_S03_Long_v01_Part_009_Engineer_ab12cd34.wav`) —
falling back to `job.name` ("Part 9", "Engineer: 9"). No Batch surface
rendered *which Block* a row belongs to, *which Part of that Block* it
is, or *how many Parts the Block has*. The only block-derived visible
name anywhere was the splitter's `block_label` in the Generate Long
preview headers and in Combine blocker messages — never in the Batch
window. `source_block_id` was designed for "Block→Part mapping in the
BatchGenerationDialog" (`narration_splitter.py:63-66`) but **no UI code
consumed it**.

Naming inconsistency (verified): the editor gutter renders positional
`B{N}` (`narration_editor.py:291-301`); the splitter records
`block_label = label or "Block {N}"` (positional at split time); user
labels exist in the properties panel. Three namespaces, none of them
visible in Batch.

### 1.3 Queue order vs structural order (where the conflation lived)

- The "#" column renders **queue position**; `move_job` reorders the
  queue; regen/duplicate change queue membership. For a fresh queue,
  queue position == global part_index — the conflation was invisible.
- Structural order lives in `expected_audio_slots` (Combine order,
  coverage order, slot numbering).
- Manual Concatenate uses QUEUE order (its documented concept);
  Scene Combine uses SLOT order — intentionally different, but the
  Batch UI gave the user no way to tell which row was structurally
  which after any reorder.
- The P3.44.1/P3.44.4 identity guard and execution-run scoping already
  operate on job OBJECT identity, never on position — the engine side
  was already correct; only the presentation conflated them.

### 1.4 Re-detect (deliberate, preserved)

Re-detect replaces **every** Block id by design
(`rebuild_everything`/`rebuild_automatic_only`/`preserve_overrides` all
install fresh detector blocks; `_transfer_overrides` explicitly does
not transfer ids). It never re-materialises `expected_audio_slots`
(the only production caller of `materialize_expected_slots` is Generate
Long — the docstrings claiming "and on re-detect" are stale; the
residual is documented in OPEN_BUGS 2531-2535 and deferred as a
P3.45.2+ structure decision). Until the next Generate Long, slots and
assets keep the OLD block ids; after it, old-id assets join no slot
(identity-first join, `slot_of_asset`), and a restored old queue row is
orphaned — which the new UI must present **honestly** as unlinked,
never as a fabricated mapping.

## 2. ARCHITECTURAL DECISION — CASE A

Existing data contains everything required:

- `BatchJob.slot_id` (both sides of the join) ✓
- `Scene.expected_audio_slots` with `block_id`, `block_label`,
  `part_of_block`, `part_index`, `speaker` ✓
- block position derivable (distinct block_id order in the structure —
  equals the editor gutter's `B{N}` whenever the structure is current,
  because the splitter emits parts in block order) ✓
- parts-per-block derivable (count of slots sharing the block_id) ✓
- user label vs auto label distinguishable (auto form is
  `Block {N}`) ✓

Therefore: **presentation and mapping change only.** No new identity
system, no new source of truth, no propagation path to add, no
architecture change. The Batch UI becomes a projection of the existing
model.

## 3. IMPLEMENTATION (3 surfaces, all projection)

### 3.1 `engine/audio_provenance.py` — `slot_identity_summary`

One pure derivation function (the same discipline as
`resolved_asset_for_slot` — one resolver everywhere): given
`(scene, slot_id)` it returns
`{slot_id, block_id, block_number, block_label, user_label,
part_of_block, parts_in_block, part_index, speaker, character_id}`,
or `None` when the slot is not in the current structure (the caller
must present the unlink honestly). Plain/speaker slots carry
`block_number=None`, `parts_in_block=0` (turn totals are NOT derivable
without positional inference — never performed). `_AUTO_BLOCK_LABEL_RE`
(`Block \d+\Z`) separates user labels from the splitter's positional
auto form so display code never prints "B4 · Block 4".

### 3.2 `ui/panels/batch_generation.py` — the `_NameCell` two-line row identity

Scene-mode Name column (header now **"Part / File"**):

```
B4 · Intro · Part 2/3                    ← identity chip (muted, 10px)
Eden_S03_Long_v01_Part_009.wav           ← display name (unchanged)
```

- block part: `B{n}[ · user label][ · Part x/y]` (fraction only when
  the block has >1 expected slot — a single-Part block is just `B{n}`)
- plain/speaker part: `Part {part_index}[ · speaker]` — the global
  structural part number (survives reordering, unlike "#")
- `Unlinked part` — slot_id not in the current structure (muted, with
  an explanatory tooltip; never a fabricated block mapping)
- `Manual job` — no slot provenance (P3.44.6 duplicates, "+ Add" rows)
- Manual Batch Queue window: **unchanged** — plain item cell, original
  "File Name" header, no identity layer (zero regression surface)

The cell stores NOTHING: `update_state(job)` recomputes both lines from
`_identity_chip(job)` → `slot_identity_summary` on every refresh
(rebuild path and the in-place P3.44 §7/§8 path both call it), so
structural identity never derives from queue position. The container is
transparent (no background styling) so the delegate-painted
row/selection background shows through — the P3.40 row pixel contracts
(selection-fill sampling at row_top+3, no accent at column boundaries)
are preserved and re-verified. `mousePressEvent` keeps the item-cell
interaction contract: clicking the wide Name column selects the row.

The "#" column gains a tooltip in scene mode: queue position
(execution order) vs the structural identity in Part / File — making
the queue-vs-structure distinction explicit where it is visible.

Row height stays 48px; the two lines fit inside the existing 47px
content box (P3.40 geometry contract re-asserted by tests).

### 3.3 `JobEditDialog` — read-only identity note

Optional `identity_note` parameter renders one muted line at the top:
`Scene Part: B4 · Part 2/3` for structural jobs; the honest
manual-job contract for slot-less rows in scene mode ("Manual Batch
job — not bound to a Block Part of this Scene. Generating it never
joins a Scene slot."); nothing in the manual window. A duplicated row
can therefore never be mistaken for the original structural Part while
being edited.

## 4. BLOCK → PART UX (the required flows)

| Flow | Presentation |
|---|---|
| A one Block / one Part / one Job | `B2 · Conclusion` |
| B one Block / multiple Parts | `B1 · Intro · Part 1/2`, `B1 · Intro · Part 2/2` — relationship derived from the structure, never stored |
| C regenerate | same slot, same chip; new `part_version` (asset-aware max+1, new run); version state in the existing Generated cell ("· 2 versions", "· vNN selected") |
| D duplicate | `Manual job` chip + `… (copy)` name + the JobEditDialog note; its generation joins NO slot (P3.44.6, pinned by tests); the copied filename is guarded — a collision FAILS the row and never overwrites the original's audio |
| E save/reload | queue YAML + project JSON round-trips re-derive identical chips |
| F reorder | "#" changes; chips travel with the job objects; `expected_audio_slots` untouched; Combine order unchanged |
| G generate selected | only checked rows run (P3.44.4 execution run); unchecked rows keep state AND identity |
| H failed job | FAILED row keeps slot_id + chip; no asset registered |
| I re-detect | ids replaced by design (unchanged); until the next Generate Long the recorded structure still backs the chips; after re-materialisation an old queue row shows `Unlinked part` — honest, no fabrication |

## 5. QUEUE ORDER vs STRUCTURAL ORDER

Queue position is an execution/workspace concern ("#" column, Manual
Concatenate order, P3.44.4 run membership). Structural order comes from
`expected_audio_slots` (Combine, coverage, `B{n}`/`Part x/y`
derivation). The implementation keeps them distinct: the chip is
derived per JOB OBJECT (slot_id-keyed) and never from the row index —
`move_job` changes "#" only. The "#" tooltip states the distinction.

## 6. WHAT WAS **NOT** DONE (scope gate)

No new identity system; no new source of truth; no slot_id /
generation_run / part_version redesign; no GenerationPlan /
fingerprint / STALE / session locking; no Batch architecture rewrite;
no Scene Combine rewrite; no Manual Concatenate rewrite; no P3.44.9 /
P3.44.9.1 / P3.45.1 / P3.45.2A / P3.45.2B changes; no timers; no
polling; no automatic structural reconciliation after re-detect; no
mutation of user text. The UI is a projection of the existing model —
`slot_identity_summary` derives, stores nothing, and is the single
derivation source for all new presentation.

## 7. REMAINING ARCHITECTURAL LIMITATIONS (evidence-based)

- Re-detect does not re-materialise slots (pre-existing, documented
  OPEN_BUGS 2531-2535): between a re-detect and the next Generate Long
  the chips describe the RECORDED structure; live gutter numbering may
  differ. Reconciliation is a P3.45.2+ structure decision, not taken
  here (the task forbids inventing reconciliation logic).
- Speaker-turn part totals are not derivable from the structure
  (consecutive-speaker grouping would be positional inference): speaker
  parts show the global `Part {n}`, not a turn fraction.
- Block labels shown are as recorded at split time (a label edited
  after the split appears after the next structure event) — consistent
  with the §0 product rule that slots change only on structure events.
- `B{n}` is positional within the structure, matching the gutter while
  the structure is current; it is NOT the block uuid (which stays
  internal, per the no-raw-identifier display rule).
- The pre-existing environment failures (SS-3, the P3.44.4 stop-timing
  flake, `verify_integration` stop semantics) are unchanged — see
  OPEN_BUGS.

## 8. TESTS

`tests/test_p3_45_3_batch_block_identity.py` — 40 tests:

- **Identity (12)**: slot_identity_summary for 1/2/3-part blocks, two
  blocks, deterministic ordering, user vs auto label, plain/speaker,
  unknown slot → None, invalid input, materialise round-trip; BatchJob
  identity invariants (regen preserves part; to_request forwards all).
- **Duplication (3)**: P3.44.6 stripping control; request joins no
  slot; manual chip + collision guard (failed duplicate never
  overwrites the original).
- **Queue order (1 + widget)**: move preserves identity; reorder
  changes "#"/chips travel; structure untouched (real MainWindow).
- **Save/load (3)**: queue YAML, Scene dict, and the full Flow E
  presentation round-trip incl. version state and duplicate semantics.
- **Re-detect (1)**: ids replaced (control), no false mapping after
  re-materialisation, no stale id in the structure.
- **Generation (4 real MainWindow + Engine runs)**: the §20 golden
  path (creation → real dialog → real run → registered assets →
  rendered chips), regen new-version, duplicate-joins-no-slot,
  selective run, failed run.
- **UI (16)**: chips per kind, header labels, "#" tooltip, manual
  window unchanged, row-height/geometry contracts, click-to-select,
  in-place update without widget rebuild, skipped de-emphasis,
  JobEditDialog note, status pill association.

## 9. VALIDATION

Focused 40/40 **twice** (stable). Regression battery: P3.45.2B (33),
P3.45.2A (42), P3.45.1 (20), P3.44.9 + P3.44.9.1 + P3.28 provenance +
P3.23 Combine + P3.31 (206 + 35 subtests), P3.44.1/4/5/6 (104),
playback/two-workflows/nb/outlier/ux-batch (139), project save/load
family (180) — all green. Full suite in 6 chunks: **1697 passed + 2
documented pre-existing failures** (SS-3 P3.37 README;
P3.44.4 stop-timing flake — passes isolated, proven flaky on the
pristine baseline in P3.45.2B). verify_compile 152/152;
verify_architecture PASS; verify_functional_integrity 80/80;
verify_integration: the pre-existing stop-semantics violation,
identical to the pristine baseline (OPEN_BUGS). Launch smoke 12/12 on
the real entry path (`ss/p3453_launch_smoke.py`, not committed per
house convention).

## 10. FILES

- `engine/audio_provenance.py` — `slot_identity_summary` +
  `_AUTO_BLOCK_LABEL_RE` (additive, pure).
- `ui/panels/batch_generation.py` — `_NameCell` class,
  `_identity_chip`/`_identity_note` methods, scene-mode Name cell
  wiring (rebuild + in-place paths), "Part / File" header, "#" tooltip,
  `JobEditDialog` identity note parameter.
- `tests/test_p3_45_3_batch_block_identity.py` — new focused suite.
- `docs/design/P3_45_3_BATCH_BLOCK_IDENTITY.md` — this document;
  `DEVELOPMENT_LOG.txt` + `docs/governance/OPEN_BUGS.md` entries.
