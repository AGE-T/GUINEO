# P3.44.5 — Stabilisation / Integrity Phase 1 — Implementation Notes

Status: implemented. Scope: exactly three objectives — (A) Batch dialog
lifecycle / stale-listener fix, (B) block-aware `slot_of_asset` join,
(C) permanent regression tests for both. No other behaviour changed.

Regression tests: `tests/test_p3_44_5_stabilisation_integrity.py`
(29 tests; 17 of them fail on the pre-P3.44.5 code — 8 lifecycle, 9
provenance — and all 29 pass after the fix).

---

## 1. Why the Batch listener must be re-established after dialog reuse

`BatchGenerationDialog.closeEvent` releases the dialog's manager
listener (`_release_manager`: `remove_on_changed` + marshaler handed
back). The dialog OBJECT survives close (Qt non-destructive close) and
MainWindow keeps the reference, because the scene-aware single-instance
policy (`_focus_existing_batch_dialog`) intentionally re-focuses the
still-alive closed dialog for the same scene+mode instead of rebuilding
it (the user's window state is never lost on a close/reopen cycle).

Before P3.44.5 that reuse path called only `show()/raise_()` — nothing
re-registered the listener. The reopened dialog was then deaf to every
`BatchManager` event: row status pills stayed at their pre-close state,
the summary froze, Start stayed disabled while Pause/Stop stayed
enabled, and the UI recovered only when the user pressed Pause/Stop
(those handlers force a local refresh). BatchManager itself kept
progressing correctly the whole time — the defect was purely
lifecycle/listener ownership, not the manager state machine.

**Fix point:** `showEvent` now funnels every presentation of the dialog
(fresh construction AND reuse-after-close) through `_connect_manager`
plus a full `_on_manager_changed` re-sync. The re-sync is required for
the "batch finished while the dialog was closed" case, where no future
event will ever arrive.

## 2. Why listener registration must be idempotent

`showEvent` fires on every show. Re-registering unconditionally would
(a) accumulate duplicate logical listeners (a fresh closure each time —
`add_on_changed` dedupes by identity, so new objects are NOT deduped)
making one manager event fan out into N duplicated UI updates, and (b)
corrupt the marshaler hand-over bookkeeping (`_prev_marshal` could
adopt our own marshaler as the "previous owner" to later restore).

Idempotency rules in `_connect_manager`:

* NO-OP while OUR exact listener is already registered — checked
  against the manager's ACTUAL `on_changed_listeners` list, not a
  local "connected" flag (a second bookkeeping flag would be a second
  source of truth that can go stale).
* The listener and marshaler callback objects are created ONCE per
  dialog and REUSED on every reconnect, so the manager's identity-based
  dedupe guarantees exactly one logical listener across any number of
  close/reopen cycles.
* `_prev_marshal` is only re-snapshotted when the current marshaler is
  not our own.
* The `destroyed` safety handler is connected once (objects live for
  the whole dialog lifetime) — reconnect cycles cannot accumulate
  destroy handlers.

A newer marshal owner is never clobbered by an already-subscribed
dialog: events still reach our listener through the manager's listener
list regardless of who owns `marshal_to_ui`.

## 3. Why `part_index` alone is insufficient for block-derived matching

`slot_of_asset` joined an asset to a slot by GLOBAL `part_index`
position only. `part_index` is the asset's global position in the
structure THAT EXISTED AT GENERATION TIME; the slot's stable identity is
`{block_id}:{part_of_block}`. After a structural edit (blocks rebuilt
with new stable block ids), old assets whose global positions happened
to line up with new slots were accepted as coverage: derived coverage
reported the new structure as covered and Combine could assemble old
audio under the new structure (reproduced end-to-end in the audit and
now by the regression tests).

**Block-aware rule** (for scenes WITH an expected slot structure):

* block-derived slot ⟹ asset must carry the SAME `block_id`
  (both sides carry identity → identity must agree);
* plain slot ⟹ plain asset only (positional identity is the whole
  part identity in the plain model — a block-aware asset on the other
  side is evidence of a different structure);
* mismatch on either side, or a position that no longer exists
  (structure shrank) ⟹ `None` — the asset belongs to no current slot.
  A block-aware asset never silently downgrades to the weaker legacy
  positional synthesis when the stronger comparison fails.

Valid matching is unchanged: same block_id + same part_index still
qualifies (per-slot precision: a block whose identity survives an edit
keeps its coverage; changed blocks lose theirs).

## 4. How legacy block-less assets are treated

The exact existing legacy representation was inspected first:

* Pre-P3.28 full-scene assets carry NO `block_id`, NO `part_index`, NO
  `part_version` — `slot_of_asset` returns None for them exactly as
  before (they are not slot assets; `is_complete_scene_asset` governs
  them elsewhere).
* Scenes that never materialised an expected slot structure keep the
  documented degraded synthesis path VERBATIM: a block asset maps to
  `{block_id}:{part_index}`, a plain asset to `plain:{part_index}`;
  such scenes use legacy coverage anyway (`coverage_counts` does not
  consult slot matching). `scene=None` behaves identically.
* A plain (block-less) PART asset against a plain slot still matches
  positionally — that is the plain model's identity, not a downgrade.

A modern block-aware asset therefore cannot masquerade through the
legacy path: the synthesis is reachable only when the scene has NO slot
structure, and a slotted scene's mismatches return `None` outright.

## 5. Intentionally deferred (next phases — unchanged in P3.44.5)

* GenerationPlan / source_structure_fingerprint / stale-gate /
  rebuild-keep-cancel UX / snapshot subsystem.
* Re-detect semantic preservation (Character/SFX/pause/block-id
  preservation during Re-detect).
* Sentence splitting (abbreviations, decimals, `GLM 5.2`, `U.S.A.`,
  `test.hu`).
* Block offset/delete corruption; oversized-block preflight; duration
  estimation.
* Block ≠ Part UI redesign; rendering/OpenGL architecture;
  Concatenate redesign; full provenance refactor.

Known residual (documented, accepted for this phase): a block whose
identity survives a structural edit but whose PART DECOMPOSITION
changed can still be matched positionally (same block, same position,
different part text). The current model stores no `part_of_block` on
assets, so a stronger same-block join is not derivable without the
deferred plan/fingerprint layer. The proven false-match class
(different block ids) is fully rejected.
