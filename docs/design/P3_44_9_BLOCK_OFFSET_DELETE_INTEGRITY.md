# P3.44.9 — Block Offset / Delete Integrity

**Phase**: P3.44.9 (IMPLEMENTATION + REGRESSION)
**Status**: COMPLETE
**Predecessors**: P3.44.5 (batch lifecycle/provenance), P3.44.6 (slot_id
identity), P3.44.7 (Re-detect semantic preservation), P3.44.8 (sentence
splitter integrity) — all COMPLETE and preserved.
**Files changed**: `engine/narration_block_manager.py`,
`ui/panels/narration_editor.py`,
`tests/test_p3_44_9_block_offset_delete_integrity.py` (new).

---

## 1. Root cause

The block offset engine — `NarrationBlockManager.on_text_changed` —
classified every block with exactly two branches:

```python
if old_change_start <= block.start_offset:      # (1) shift BOTH ends
    block.start_offset += delta
    block.end_offset += delta
elif old_change_start < block.end_offset:       # (2) adjust end only
    block.end_offset += delta
```

Branch (1) is correct ONLY when the edit lies entirely BEFORE the
block (`change_end <= block.start`). Because its condition merely
required `change_start <= block.start`, it also captured every block
that OVERLAPS the change region — including a block that starts
exactly AT the change start. For a deletion (delta < 0) such a block
was shifted LEFT, into text that belongs to preceding blocks.

Branch (2) computed `end += delta` even when the edit extended PAST
the block's end, then clamped (`end = max(start, end)`), silently
discarding surviving characters and leaving zero-length rows.

The engine is DIFF-based (longest common prefix, then longest common
suffix). For the proven reproduction the diff localises the deletion
exactly, so the block classification above is the sole defect.

## 2. The exact boundary condition (proven reproduction)

```
old text:      "A"*53 + "B"*46 + "C"*30        (129 chars)
blocks:        A=[0:53)  B=[53:99)  C=[99:129)
edit:          delete exactly [53:99)  (starts exactly at B.start)
diff:          cs=53, ce=99, delta=-46
```

OLD engine, block B `[53:99)`: branch (1) fires (`53 <= 53`) →
`[53-46 : 99-46]` = **`[7:53]`** — the exact reported corruption. B now
overlaps A (`[0:53)` vs `[7:53)`), owns 46 characters of A's text,
survives as a row, and — through `_split_with_blocks`
(`text[b.start:b.end]`) — generates A's text a second time. The same
branch mis-classifies every block starting INSIDE a replaced region
(`cs < s < ce`), and clamping turns the "edit extends past block end"
case into a phantom `[10:10)` that loses the surviving head `[10:20)`.

Sibling defect, same engine: `on_multi_replace` (Replace All) adjusted
only `end_offset` for a match overlapping a block — a match covering a
block's start left the start unmapped (lost surviving characters), and
a match exactly covering a whole block left a `[53:53)` phantom.

## 3. The authoritative offset transformation rule (one rule, both paths)

`NarrationBlockManager._apply_edit_to_blocks(blocks, cs, ce, inserted_len)`
is the SINGLE rule used by `on_text_changed` AND (sequentially, with a
running shift) by `on_multi_replace`. One contiguous edit replaces old
text `[cs, ce)` with `inserted_len` characters
(`delta = inserted_len - (ce - cs)`). For each block `[s, e)`:

| # | condition | new range |
|---|-----------|-----------|
| 1 | `e <= cs` (edit at/after block end) | untouched |
| 2 | `ce <= s` (edit entirely before block) | `[s+delta, e+delta]` |
| 3a | `s <= cs < e` and `e <= ce` | `[s, cs+L)` — head intact, owns the replacement |
| 3b | `s <= cs < e` and `e > ce` | `[s, cs+L+(e-ce))` — head intact, owns replacement + surviving tail |
| 4a | `cs < s < ce` and `e <= ce` | REMOVED (nothing survives, nothing owned) |
| 4b | `cs < s < ce` and `e > ce` | `[cs+L, cs+L+(e-ce))` — surviving tail only |
| 5 | whole-text replacement (`cs==0 and ce==len(old)`) | every block REMOVED |
| 6 | final range owns zero characters (`start >= end`) | REMOVED — no phantom rows |

Worked examples (block `[30:60)`, `S` = 100 unique chars):

- delete `[25:30)` → rule 2 → `[25:55)` (edit ends exactly at block
  start: pure shift)
- delete `[30:35)` → rule 3b → `[30:55)` (edit starts exactly AT the
  block start: start FIXED, never shifted into preceding text)
- delete `[35:55)` → rule 3b → `[30:40)` (strictly inside)
- delete `[55:60)` → rule 3a → `[30,55)` (ends exactly at block end)
- delete `[60:65)` → rule 1 → untouched (starts exactly at block end)
- delete `[20:30)` → rule 4b → `[20:50)` (covers the start, tail
  survives at the replacement point)
- delete `[20:80)` → rule 4a → REMOVED (fully covered)
- insert `"xy"` at 30 → rule 2 (`ce==cs<=s`) → `[32:62)` (insertion
  at the block start is OUTSIDE the block)
- insert `"xy"` at 45 → rule 3b → `[30:62)` (inside: the block grows)
- insert `"xy"` at 60 → rule 1 → untouched (at the block end:
  outside)

Determinism notes: the mapping is monotone (identity before `cs`,
constant on the replaced span, `+delta` after `ce`), so non-overlapping
inputs stay non-overlapping and list order is preserved. Rule 3 is the
"typed inside the block" rule: the block containing the edit start owns
the replacement text; blocks that started inside the replaced region do
not. The diff engine (maximal prefix, then maximal suffix) can localise
ambiguous repetitive-text edits differently than the user's cursor
history — every such localisation is a VALID (prefix, suffix) split and
the rule is correct for whichever split the diff picks (regression
tests pin the documented outcomes).

Rule 5 rationale: a whole-text replacement (or a programmatic
`set_text` swap — see §6) means no character of the old document
survives; no block can keep ownership. Scene loads never reach this
path (P3.26 `_loading_scene` guard).

Rule 6 rationale: a block that owns zero source characters cannot
render, generate, or persist — it is not a block. This also heals
LEGACY phantom ranges (loaded from pre-P3.44.9 saves) on the first
text edit.

**Span-local SFX/pause insertion offsets** (block-relative) ride the
SAME absolute mapping in `_remap_insertions`: before the edit →
unchanged; at/after the edit end → `+delta`; inside the replaced region
→ deterministic clamp to the replacement start (a user-authored SFX is
never silently dropped by a keystroke); finally clamped into
`[0, new_len]`. Blocks that die take their insertions with them.

## 4. Block invariants (documented model)

Per block: `id` (uuid, stable), `[start_offset, end_offset)` half-open
range into the editor text (the single source of truth — blocks never
store text), semantic metadata (five overrides, `character_id`,
`lost_character_id`, `label`, SFX/pause insertions with block-relative
offsets), `locked`, `manually_edited`; persistence via
`Scene.narration_blocks` (`PromptBlock.to_dict/from_dict`).

Enforced invariants after every text change (asserted by
`assert_structure_valid` over the whole battery):

1. `0 <= start < end <= len(text)` — ranges valid AND non-empty
   (zero-length = removed).
2. List order == ascending `start_offset`; starts never decrease.
3. Non-overlap: `block_i.end <= block_(i+1).start` (touching allowed).
4. Text ownership: a block's resolved text IS `text[start:end]`
   exactly (raw, unstripped) — no block can own another block's
   characters.
5. No phantom rows: deleted/emptied blocks leave the model (and the
   save file — via the round-trip) entirely.
6. Gaps between blocks are LEGAL and unowned (plain text outside
   blocks is expected; full coverage is NOT required). Whitespace
   between blocks belongs to neither block.
7. Whitespace-only blocks are legal while non-empty (the splitter
   skips them for parts); only ZERO-character blocks are removed.
8. Save/load fidelity: a valid structure before save is byte-identical
   after `to_dict → from_dict` and the `Scene` round-trip.

## 5. Delete Block button (§11 — confirmed NOT the root cause)

`_do_delete` → `NarrationBlockManager.delete_block` removes the row
from the model; the TEXT is untouched (offsets of every other block are
unchange by definition); the editor clears the selection, emits
`blocks_changed`, clears the MainWindow block scope (`block_selected
("")`) and re-renders. No structural defect found in the button path
(regression-locked: `test_real_editor_delete_block_button`). The
reported "block delete corruption" was always the offset engine above.

## 6. Insertion / edit / programmatic swap behaviour

- Typed edits (insert/delete/substitution, single or repeated) go
  through `on_text_changed` → the rule table in §3.
- Replace All goes through `on_multi_replace` → the SAME rule applied
  per match, sequentially with a running shift.
- `set_text` (programmatic whole-document swap; history-reuse and
  first-run demo paths) now RESETS the block model
  (`_block_manager.clear()`), and — when Narration Blocks mode is
  active — auto-detects fresh blocks for the new document (the same
  contract as entering blocks mode with no blocks). Previously the swap
  was fed into the offset engine, which clamped the previous document's
  blocks into garbage/phantom ranges riding onto unrelated text.
  Scene restores are unaffected: they run inside the P3.26 load guard
  and replace the whole model via `set_scene_blocks`.

## 7. Save / load / rehydration

`to_dict → from_dict` and the `Scene` round-trip preserve the corrected
structure exactly (ids, offsets, ordering, overrides, Character,
label, locked, insertions). Because rule 6 eliminates zero-length
blocks on every text change, no new phantom can be persisted; legacy
phantoms loaded from old saves are healed on the first edit (documented
residual: a legacy phantom is round-tripped as-is until an edit or a
Delete Block removes it — the fix prevents NEW ones).

## 8. Generation integrity proof (§13 / §14)

After the boundary delete of the middle block, `NarrationSplitter.split`
receives exactly the owning ranges: parts are `A_TXT` and `C_TXT`,
`source_block_id` set are exactly the surviving block ids, the deleted
id appears in no part, and no part text duplicates another. The same
holds after `to_dict → from_dict` rehydration and after the full
`Scene` save/load round-trip (regression-locked:
`TestGenerationConsequence`, 5 tests). The old engine generated B's
range as A's text (duplicated generation) — that class is dead.

## 9. Manual split/merge SFX/pause investigation (§15 — conclusion)

**Separate issue, NOT the offset-engine defect.** Evidence:
`split_block` and `merge_with_above` compute block RANGES correctly
(`[s,split)+[split,e)`; `above.end = block.end` — characterisation
tests green on BOTH the pre-fix and post-fix trees), they simply do not
implement span-local insertion redistribution at all (the first child
keeps the insertions unshifted; the merged-away block's insertions are
dropped). The offset defect lived in `on_text_changed` /
`on_multi_replace` — different functions, different mechanism. The
redistribution gap remains the P3.44.7 deferred finding (user
operations, not Re-detect paths); NOT fixed here, NOT silently
absorbed.

## 10. Tests

`tests/test_p3_44_9_block_offset_delete_integrity.py` — 76 tests +
35 subtests:

- §5 the proven `[53:99]→[7:53]` reproduction (5 tests: no shift into
  preceding text, no overlap, no phantom, no wrong ownership, realistic
  3-block document);
- §6 deletion matrix A–I (10 tests) + whole-document delete;
- §7 insertion matrix (9 tests: inside / at start / at end / before /
  between / after last / multiline / punctuation / inline marker);
- §8 multi-operation sequences (4 tests: insert-delete-insert-delete,
  edit-edit-delete-edit, repeated boundary deletes, delete-all);
- §9 + §22 structural invariants + deterministic position sweeps
  (delete table 12 cases, insert table 8 cases, full-scenario
  invariant battery, legacy-phantom healing);
- §10 identity (4 tests: stable ids, deleted id gone, neighbour ids,
  positional vs stable numbering);
- §19 clamping (3 tests: surviving head kept, fully-covered removed,
  no negative/inverted ranges over 5 delete shapes);
- §20 boundary conditions (6 tests: cs==s, ce==e, cs==s∧ce==e,
  equal-length substitutions inside/covering start, whole-text swap);
- Replace-All matrix (5 tests);
- §16 + §17 metadata & marker propagation (8 tests);
- §12 save/load round-trips (3 tests);
- §13 + §14 generation consequence incl. rehydration (5 tests);
- §15 manual split/merge characterisation controls (4 tests, green on
  both trees — the separation evidence);
- real-editor Qt-signal paths (5 tests: boundary delete through the
  real `textChanged` pipeline, Delete Block button, typed sequence,
  marker survival, programmatic swap semantics);
- §18 reorder-model documentation test (no reorder feature exists;
  list order IS document order).

## 11. Old-code / new-code proof (exact counts)

Final test file, production changes stashed (`git stash push --
engine/narration_block_manager.py ui/panels/narration_editor.py`):

- OLD code: **54 failed / 33 passed** (the 54 = defect detectors; the
  33 = controls whose expectations coincide with the old behaviour)
- NEW code: **76 passed** (+35 subtests)

Regression preservation: P3.44.5/6/7/8 suites 175/175 with the new
suite 251/251; block/editor/save-load suites (P3.15×2, P3.26, P3.41,
P3.44.2, audit) 237/237; full battery 1575 passed (4 chunks) with two
PRE-EXISTING non-phase failures proven identical on the pristine tree:
the P3.37 `test_readme_has_british_english_section` documentation test
(broken by the earlier `cd5e122` "Update README.md" commit — README
header structure changed) and the timing-flaky
`test_p3_44_4_batch_execution_run.py::test_regen_run_stop_leaves_
others_pending` (fails 3-of-4 runs on the pristine tree too; the test
itself documents the still-busy-engine retry chain under suite load).
`verify_compile` 146/146, `verify_architecture` PASS,
`verify_functional_integrity` 80/80, launch smoke 11/11.

## 12. Known residual risks & deferred work

- The diff-based engine localises ambiguous repetitive-text edits to a
  VALID but possibly different (prefix, suffix) split than the user's
  cursor history; text ownership is always exact for the chosen split,
  and the regression table pins the documented outcomes. Cursor-true
  tracking would require QTextCursor-level edit hooks — out of scope.
- Text-level UNDO restores the text but cannot resurrect blocks removed
  by rule 4a/6 (Qt's undo stack owns text only; block structure is
  app-level state). Deterministic; documented; re-detect restores.
- Legacy phantom blocks from pre-P3.44.9 saves persist until the first
  text edit or an explicit Delete Block (rule 6 heals on edit; save
  filtering was deliberately not added — persistence stays
  fidelity-exact).
- Manual split/merge SFX/pause redistribution — separate deferred
  issue (see §9; P3.44.7 finding).
- Pre-existing (not this phase): P3.37 README documentation test
  mismatch (introduced by commit `cd5e122`); flaky
  `test_regen_run_stop_leaves_others_pending` timing under load.
- Roadmap unchanged: P3.45 (Block → Part UX, oversized-block preflight,
  duration estimation, rendering/UX hardening) and Batch Generation
  Architecture V2 remain DEFERRED. No P3.45 work was pulled in.
