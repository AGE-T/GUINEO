# P3.44.7 — Re-detect Semantic Preservation

**Phase:** P3.44.7 (Re-detect semantic preservation) — IMPLEMENTATION + REGRESSION.
**Baseline:** P3.44.6 (Generation Structure Identity — `slot_id` authoritative join) intact.
**Scope discipline:** NOT a Batch Generation redesign; NOT a sentence-splitter repair
(P3.44.8); NOT a block-offset/delete repair (P3.44.9). Minimal, proven-defect-only changes.

---

## 1. Root causes found (verified in the CURRENT source, not assumed)

The P3.44.5 research audit (Issue E) proved four independent semantic-loss
mechanisms; all four were re-verified against the source before this phase:

1. **Character-only assignment was invisible as manual state.**
   `PromptBlock.has_overrides()` covered only emotion/style/speed/pitch/
   delivery, and `_on_character_changed` (narration_editor) did NOT set
   `manually_edited`. A Character-only block therefore had
   `has_manual_edits == False`, so `_on_analyze_again` took the DIRECT
   rebuild branch (`rebuild_everything`) — every Character destroyed with
   NO dialog and NO `lost_character_id` warning.

2. **Substitution edits silently lost semantics on the Save path.**
   `preserve_overrides` matched by stripped-equality (pass 1) or
   containment ≥ 80 % (pass 2), and the lost-Character donation (pass 3)
   accepted candidates by containment only. A plain substitution edit
   ("mat" → "rug") breaks containment ENTIRELY: no transfer, no match,
   and no lost warning — the Character (and emotion/style/label) vanished
   without a trace.

3. **Splits transferred semantic state to NO new block.**
   A balanced split (one old block → two ≈50 % children) scores below the
   80 % threshold for both children, so neither child matched, and
   block-wide semantics reached at most one `lost_character_id` warning.
   Emotion/style/label/SFX/pause were silently lost for both children.

4. **SFX/pause insertions were never transferred at all.**
   `_transfer_overrides` and `rebuild_automatic_only` copied overrides,
   label, lock and Character — but NOT `sfx_insertions` /
   `pause_insertions`. Every Re-detect path (Save, Apply, Discard, direct)
   dropped the span-local SFX/pause metadata even on EXACT text matches.
   (In-text `{sfx:…}`/`{pause}` markers live in the editor text itself and
   were never affected.)

## 2. Semantic fields covered (inventory derived from the current model)

| Field | User-authored? | Semantic? | Survives Re-detect? | Rule |
|---|---|---|---|---|
| text | yes | source content | NO — rebuilt | new analysis owns it |
| block id | system | identity | replaced (by design) | see §5 |
| emotion/style/speed/pitch/delivery | yes | YES | YES where unambiguous | block-wide transfer |
| character_id | yes | YES (MANDATORY) | YES where unambiguous | block-wide transfer + lost warning |
| lost_character_id | system | warning state | recomputed | cleared on successful transfer |
| sfx_insertions | yes (via saved projects) | YES (span-local) | YES | offset-mapped transfer (§6) |
| pause_insertions | yes (via saved projects) | YES (span-local) | YES | offset-mapped transfer (§6) |
| label | yes | YES | YES | block-wide transfer |
| locked | yes | control state | YES on matched blocks | transfers; Apply path protects locked |
| manually_edited | system flag | control state | recomputed | set on transfer recipients |
| status/progress/duration | system | runtime display | NO | owned by the generation pipeline |

`has_overrides()` now returns True for ANY user-authored semantic state
(five overrides + character_id + label + sfx/pause presence), so the
direct-rebuild branch fires ONLY for a genuinely pristine structure.
`lost_character_id` is deliberately excluded (a warning, not content).

## 3. Matching / transfer rules (all deterministic)

`preserve_overrides` (the Save path) now runs four passes in order:

1. **EXACT** — stripped text equality, one-to-one (unchanged).
2. **SIMILARITY** — best one-to-one score ≥ 80 % among unmatched blocks.
   Score = max(containment ratio, difflib SequenceMatcher ratio) — the
   difflib term is the P3.44.7 addition that catches substitution edits.
3. **SPLIT / union coverage** (new) — the first run of consecutive
   unmatched new blocks whose whitespace-normalised concatenation EQUALS
   an unmatched old block's whitespace-normalised text. Block-wide
   semantics transfer to EVERY child; span-local insertions allocate to
   the child whose mapped region contains them (sequential region search
   — a repeated child text maps to the NEXT unused occurrence, so an
   insertion can never double-allocate).
4. **LOST-CHARACTER** (P3.23 §25, extended) — still-unmatched old blocks
   with a Character donate `lost_character_id` to the most related new
   block. Donation accepts the legacy containment rule verbatim (any
   overlap > 0) OR a difflib ratio ≥ 0.5.

`rebuild_automatic_only` (the Apply path) gained the same split pass and
the same lost-Character donation for locked blocks whose text vanished —
the three user-facing Re-detect paths (Save / Apply / direct-with-dialog)
now offer equivalent preservation guarantees. `rebuild_everything`
(Discard, after an explicit confirmation that now names Characters and
SFX/pause insertions) remains the intentional destruction path.

One-to-one discipline is unchanged for passes 1–2; the split pass is the
only one-to-many donation and only under exact union coverage.

## 4. Ambiguity handling (explicit, never a silent guess)

- **Unambiguous** (exact, ≥ 80 % similarity, exact union coverage):
  transfer.
- **Ambiguous but related** (containment < 80 %, or difflib in
  [0.5, 0.8)): NO transfer — `lost_character_id` warning on the most
  related new block; the user re-assigns.
- **Unrelated** (no containment, difflib < 0.5): nothing transfers and
  nothing is warned on — there is no plausible home for the semantics;
  this is the documented deterministic outcome for deleted content.
- **Span-local anchors in edited regions**: an SFX/pause offset that
  cannot be mapped (the anchor text no longer exists) is DROPPED and
  logged — it is never guessed onto unrelated text. Mappable anchors
  always transfer.

## 5. ID handling (P3.44.6 interaction)

Re-detect replaces block objects and therefore block ids — UNCHANGED,
and correct: the ids index the analysis structure, not the semantics.
What matters (verified, regression-locked):

- semantic state migrates explicitly through the matching passes above;
- Re-detect does NOT touch `scene.expected_audio_slots` (the production
  materialisation point remains Generate Long — `_start_long_narration`;
  the "on re-detect" phrase in an old comment there refers to the next
  Generate Long run, not to a Re-detect-side materialisation);
- after Re-detect, new block ids → new `slot_id`s at the next Generate
  Long → a MODERN asset stamped with an OLD `slot_id` joins nothing
  (identity-first, no positional fallback — P3.44.6); legacy assets keep
  the documented P3.44.5 positional+block-aware join verbatim;
- no second identity system was introduced.

## 6. SFX / pause handling

SFX and pause are span-local semantic metadata (`SfxInsertion` /
`PauseInsertion`, offsets relative to the block's raw text span). The
UI-authored representation is the in-text `{sfx:Name:Onom}` / `{pause}` /
`{long_pause}` marker (inserted at the cursor), which lives in the text
and survives Re-detect natively; the structured metadata path (saved
projects) is what this phase preserves. Transfer mapping
(`_map_offset`, deterministic):

1. identical texts → identity;
2. new text inside old text → shift by substring position (offsets
   outside the new span belong to a sibling block);
3. old text inside new text → shift by substring position (growth);
4. otherwise → difflib matching blocks: an offset inside a matching
   block maps through it; an offset in an edited region returns None
   (dropped, logged).

The end-to-end guarantee is locked by a test that materialises the
transferred insertion through `materialize_inline_markers` and asserts
the marker lands on the same anchor text.

## 7. Test results

New permanent suite: `tests/test_p3_44_7_redetect_semantic_preservation.py`
— 44 tests covering the §18 matrix (Character 1–6, semantic metadata 7–9,
split 10–12, SFX 13–15, pause 16–17, paths 18–22, invariants 23–26) and
the §19 absence-of-false-preservation cases (unrelated blocks never
inherit Character/SFX/pause; repeated children never double-allocate;
one old block donates to exactly one new block in passes 1–2; semantic
conservation: every old Character is active somewhere or lost-marked).

- **Old-code proof** (git stash of the three production files): the new
  detectors fail on the OLD code — 24 failed / 20 passed; the 20 green
  are exactly the controls (exact-match preservation, ambiguity warning,
  unrelated guards, structural validity, slot identity, direct-rebuild
  UX). On the NEW code: 44/44.
- **Full regression battery:** 1407/1407 green in 5 chunks (246 targeted
  P3.44.7+provenance+identity + 345 P3.35–44 + 368 early P3.5–21 +
  428 middle P3.22–34+audit + 20 P3.30 batch). The documented known-bad
  tree-only `test_p3_30_ux_fixes.py` is excluded per the P3.44.5/6
  precedent (its 2 failures were re-verified to be identical on the
  pre-P3.44.7 code).
- **Launch smoke** (real entry path): 11/11.

## 8. Deferred issues (documented, not absorbed)

- Sentence-splitter integrity (GLM 5.2 / U.S.A. / decimals / domains /
  abbreviation handling) — **P3.44.8**, untouched.
- `on_text_changed` boundary corruption / delete offsets / overlapping
  block offsets / orphan cleanup — **P3.44.9**, untouched.
- **Manual `split_block` / `merge_with_above` do not re-allocate SFX /
  pause insertions** (a manual split leaves second-half insertions on
  the first block with stale offsets; the materialiser's range guard
  drops them). Manual split/merge are user operations, not Re-detect
  paths — recorded here as a deferred finding for a later polish phase.
- Block → Part UX / oversized preflight / duration estimation /
  rendering hardening — **P3.45**.
- Batch Generation Architecture V2 — deferred, untouched.

**Files changed:** `engine/narration_blocks.py` (has_overrides definition),
`engine/narration_block_manager.py` (preservation engine: relatedness,
offset mapping, split pass, lost donation, path equivalence),
`ui/panels/narration_editor.py` (character combo marks manual state;
accurate Discard warning), `tests/test_p3_44_7_redetect_semantic_
preservation.py` (new, 44 tests).
