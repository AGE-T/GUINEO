# SpeechStudio — Scene Audio Composition: Design Review
## P3.28 FOLLOW-UP — DESIGN DISCUSSION ONLY

Status:      DESIGN REVIEW — returned for discussion. NO code, tests, or ZIP
             were modified anywhere in this pass.
Build:       P3.30 current (817/817 tests; P3.28 provenance implemented,
             P3.30 UX/stale-audit corrections applied)
Inputs:      (a) SCENE_AUDIO_PROVENANCE_DESIGN_RECORD.md (P3.27, accepted),
             (b) the actual implementation in the current workspace,
             (c) the 22-question design brief (P3.28 follow-up).
Audit basis: every claim below was verified against the real source:
             engine/audio_provenance.py (868 lines), engine/models.py
             (Scene fields), ui/main_window.py (_on_combine_scene_requested,
             _set_scene_output_selection), ui/panels/batch_generation.py
             (versions popover, combined section), ui/panels/assemble_dialog.py
             (resolution consumption), engine/project_exporter.py (v2).

Question map: brief §1→S4, §2→S4, §3→S5, §4→S8, §5→S10, §6→S5/S10,
§7→S6, §8→S9, §9→S7, §10→S7, §11→S7, §12→S7, §13→S5, §14→S11,
§15→S12, §16→S12, §17→S7, §18→S7, §19→S16/S19, §20→S4, §21→S1/S19.

---

## HEADLINE VERDICT

**The conceptual architecture proposed in brief §21 is — almost verbatim —
what P3.28 already built.** The implemented model is:

    AudioAsset                    → immutable generated version (append-only)
    Scene.selected_block_audio    → {slot_id → asset_id} per-slot selection
    current composition           → DERIVED: resolved_asset_for_slot per slot
                                    (explicit selection, else latest version)
    Scene.combined_outputs        → immutable snapshots with per-slot lineage
    Scene.selected_output         → explicit Scene-level output selection
    resolve_scene_output()        → the ONE resolution Project Assembly consumes

So this review is NOT about structure. The structure is correct, minimal, and
already satisfies §19's "keep it small" constraint exactly. What the brief
surfaces is **three semantic refinements**, all of which I endorse and which
require **zero data-model changes**:

  R1  STALE must be measured against the **current composition**
      (the resolved per-slot selection), not against "the latest version
      that exists". — fixes a real false-positive class (§4 of the brief).
  R2  STALE must also cover **structural change** (the expected slot set
      no longer matches the snapshot's lineage) — a snapshot that misses a
      newly added block must not silently pass as fresh.
  R3  Pressing **Combine must visibly become the Scene's resolved output**
      (today an older explicit selection silently survives a Combine).

Everything else in the brief: **confirmed as implemented and correct**.

---

## S1. ASSESSMENT OF THE EXISTING P3.28 DESIGN

The P3.27 design record's core decisions — one authoritative append stream
(`Scene.audio_assets`), four provenance fields, derived completeness, two
selection maps, snapshot combined outputs with lineage, one resolution
function, one registration writer — were implemented faithfully and then
corrected twice by P3.30 (friendly stale copy with Cancel/Use Anyway;
all-stale Scenes require review instead of silently falling back to one
part's WAV).

Measured against the brief's 22 questions, the model answers 19 of them the
way the brief is leaning. The three misalignments are R1–R3 above. None of
them is structural; all three live in one pure function, one handler, and
reason strings.

## S2. WHAT IS CORRECT (keep, no discussion needed)

Verified in code, each with the brief paragraph it satisfies:

1. **Versions ≠ composition ≠ combined file** (brief §3): versions are
   `audio_assets` entries; the composition is the derived map
   `resolved_asset_for_slot()` per expected slot; combined files are
   immutable `combined_outputs` entries with per-slot lineage. Three
   distinct concepts, one storage mechanism. ✔
2. **"Latest" is a default, never a rule** (§2): an explicit per-slot
   selection always wins; only an empty selection map resolves to
   latest (`resolved_asset_for_slot`, audio_provenance.py:236–249). ✔
3. **Nothing is auto-deleted** (§10): both lists are append-only; there is
   no delete path anywhere in the model. ✔
4. **Regeneration never auto-combines** (§11): regen appends one asset,
   recomputes coverage, and stops. Combine is a separate button. ✔
5. **Partial regeneration semantics** (§12): untouched slots keep their
   assets; the regenerating slot allocates the next version; coverage is
   unaffected; older combined outputs simply become stale. ✔
6. **Completeness independent of output selection/staleness** (§13):
   `coverage_counts()` is a pure function of expected slots × assets.
   A COMPLETE Scene with a STALE combined output is a normal, representable
   state (and vice versa). Scene.status never encodes stale/custom. ✔
7. **Missing audio safety** (§14): Combine is BLOCKED with named blockers;
   resolution flags missing; there is no silent per-slot version fallback
   (a selected asset whose file is missing resolves to MISSING/review,
   not to the older version). ✔
8. **Naming** (§17): `{Proj}_{Scene}_Long_vNN_Part_NNN(_Speaker)_{scene8}.wav`
   and `{Proj}_{Scene}_Combined_vNN(_scene8).wav` — version belongs to the
   slot output, combined version to the snapshot. Filenames are for humans;
   identity is model ids, never parsed from names. ✔
9. **Run vs version separation** (§18): `generation_run`
   (`{scene8}-rNNN`, monotonic per scene) answers "which execution";
   `part_version` answers "which iteration of this slot". Both ride the
   asset; runs are reconstructible by grouping — no registry. ✔
10. **Batch window economy** (§16): one row per block, lazy
    "· N versions ▾" popover with per-version "Use" / "✓ (in use)" /
    "Latest version (default)" clear, plus a rest-state "· v02 selected"
    chip when a slot is curated. 50 assets render as 4 rows + 4 popovers. ✔
11. **Multiple combined outputs** (§15): newest-first flat list, per-row
    duration/STALE badge/reason, Play/Export, "Use as Scene output";
    the resolved output is printed above the list. Inspection without
    overwhelm. ✔
12. **Persistence/export** (§7 "survives Save/Restart/Reopen/Export/
    Reimport"): all four Scene fields round-trip through project.json
    (`Scene.to_dict/from_dict`, models.py:524–580) and export v2
    (`project_exporter.py`, EXPORT_VERSION = 2, scene combined copied,
    selections included). Selections are asset/entry **ids**, so they
    survive reimport path rewriting. ✔

## S3. WHAT SHOULD CHANGE (the three refinements)

### R1 — STALE = "does not represent the current composition" (brief §4)

Today `is_scene_combined_stale` compares each lineage source's recorded
`part_version` against **`latest_asset_for_slot`** — the newest version
that *exists*. That is not the composition. Counter-example, straight from
the brief's own logic:

    B1: v01, v02, v03 exist. User explicitly selects v02 (valid, §7).
    Combine → Combined v01 (lineage B1 v02).
    Today: stale, because v03 exists ("B1 has a newer version: v03").
    Correct: NOT stale — the composition IS B1 v02; the snapshot
    represents the current composition exactly.

The fix is definitional, not structural: compare each recorded source's
**asset identity against `resolved_asset_for_slot()`** (selection, else
latest). Asset-id equality is the strict "represents" relation (version
equality is equivalent within a slot under monotonic allocation; asset-id
is the audio's identity and also survives future oddities).

Consequences (all desirable):
- No-selection case is unchanged: resolved = latest, so "regenerated B3
  → v02; Combined v01 has B3 v01 → STALE" behaves exactly as today (§4's
  example and the P3.30(f) copy "B3 has a newer version: v02" stay valid).
- Deliberately curated older versions stop producing noise badges, and
  Rule 2 ("latest NON-stale combined") stops skipping a snapshot that is
  in fact an exact representation of the curated composition.
- The reverse case becomes honestly stale: user combines B3 v02, then
  LATER explicitly selects B3 v01 → composition moved below the snapshot
  → STALE (new reason string, see S8).

### R2 — Structural staleness (slot-set mismatch)

Today staleness iterates the snapshot's recorded sources only. If a new
block/part is added to the Scene afterwards (a structure event: Generate
Long preview / re-detect), the old snapshot's lineage doesn't mention the
new slot, and the snapshot passes as fresh. Project Assembly would then
silently use a Scene mix that is missing the new content — the exact
silent-omission class P3.30(g) was raised against.

Fix: stale also when `set(recorded source slot_ids) ≠ set(expected slot
ids)`, reason: "Scene structure has changed since this Combined output
(N parts expected, M in snapshot)". Symmetric: removed slots also flag
(a snapshot containing audio for a block that no longer exists no longer
represents the composition). Cost: one set comparison. UX cost is real
but honest: such a Scene moves to requires_review at resolution time —
which is correct, because the honest alternatives are re-combine (one
click) or explicit Use Anyway. To keep that from ever feeling like a dead
end, see the assembly remedy recommendation in S10.

### R3 — Combine must visibly take over the resolved output (brief §9)

Today `_on_combine_scene_requested` appends the new snapshot and does NOT
touch `Scene.selected_output`. With no explicit selection the fresh
snapshot wins Rule 2 anyway (it cannot be stale at birth — it was built
FROM the current composition). **But if the user previously made an
explicit selection (e.g. Use-Anyway'd Combined v01, or picked a single
asset), a fresh Combine changes nothing the book will consume.** The
button appears to misbehave — same family as the "furcsán viselkedik"
report.

Recommendation: **Combine clears `selected_output` (sets None) and the
fresh snapshot wins by the default rule**, with a status toast
"Scene output → Combined v02". Rationale for *clearing* rather than
*pinning* the new entry explicitly:
- Clearing keeps the P3.30(g) safety net: a LATER regeneration makes the
  snapshot stale → Rule 2 skips it → requires_review (never silent stale
  consumption). Pinning it explicitly would let stale audio flow into the
  book with only a badge — that would partially undo P3.30(g).
- Pressing Combine is the user's newest statement of intent and should
  supersede an older curation; anyone who combined "just to listen" while
  deliberately keeping an older output re-selects it with one Choose…
  (documented, rare, visible in the toast).
- Verification of brief §9's exact question: "should Combine update
  Scene.selected_output?" — Answer: it must UPDATE THE RESOLVED OUTPUT;
  the safest mechanism is clearing the override, not writing a new one.

## S4. AUTHORITATIVE STATE DEFINITION (brief §20 — one deterministic answer each)

| # | Question | Authoritative answer (after R1–R3) |
|---|----------|-------------------------------------|
| 1 | Current **Block** audio | `resolved_asset_for_slot(scene, slot)`: the asset whose id is `selected_block_audio[slot]` if that entry exists in the slot's assets, else the slot's highest `part_version` asset. Never "whatever generated last across slots". |
| 2 | Current **Scene composition** | The derived map `{slot → current Block audio}` over the Scene's `expected_audio_slots`, in slot order. It is a FUNCTION of (assets, selection map, expected slots) — never stored, never persisted, recomputed on demand. |
| 3 | **Scene Combined output** | An immutable entry in `Scene.combined_outputs`: one evaluation of the composition function, frozen, with full per-slot lineage (slot_id, asset_id, part_version, paths, durations, silence, run). Append-only, monotonic version, never a source for another combine. |
| 4 | **Stale** combined output | Its recorded lineage no longer represents the current composition: (a) any source's recorded asset ≠ that slot's currently resolved asset, (b) slot-set mismatch vs expected slots, or (c) a referenced file missing from disk. Stale ≠ bad ≠ unusable — it means "an older snapshot", remains selectable, and is honoured with a warning when explicitly chosen. |
| 5 | **Selected Scene output** | `Scene.selected_output = {kind, id}` when set and resolvable → resolution returns it, marked CUSTOM, stale/missing flagged but honoured. Cleared by Combine (R3). When None, the deterministic default chain (below) applies. |
| 6 | **Project Assembly source** | `resolve_scene_output(scene)` — the single choke point. Chain: explicit selection → latest NON-stale combined → latest valid full-scene asset (legacy) → **requires_review** (assembly shows the reason and stops for that Scene; never a silent fallback). |

Determinism note: for any persisted state, every one of these answers is a
pure function of project.json + disk. Nothing depends on wall-clock,
window state, or evaluation order.

## S5. SCENE COMPOSITION MODEL (brief §3, §6, §13)

Endorsed as-is, and already the implementation: the composition is the
derived per-slot resolution (S4 #2). The brief's pipeline

    BLOCK VERSION SELECTION → SCENE COMPOSITION → OPTIONAL COMBINE →
    PROJECT ASSEMBLY

maps one-to-one onto `selected_block_audio` → `resolved_slot_sources()` →
`combined_outputs` → `resolve_scene_output()`. Two boundaries make this
model safe:

- **Composition → Combine**: Combine consumes the composition, blocks on
  uncovered/missing slots (no silent skipping), records lineage.
- **Combine → Assembly**: Assembly consumes exactly ONE resolved entity
  per Scene and records its id in the project-combined lineage. It never
  sees blocks, versions, or the composition itself.

Completeness (§13) is orthogonal: it is defined ONLY over expected slots ×
assets. CUSTOM/STALE/MISSING/REVIEW are output-level markers, never Scene
states. A Scene can be COMPLETE while its snapshot is STALE — normal after
a regeneration — and that must never downgrade coverage (verified: it
doesn't, and tests pin it).

## S6. VERSION SELECTION RULES (brief §7, §10)

- Representation: `Scene.selected_block_audio: {slot_id → asset_id}` —
  the smallest possible: one dict. No new entity, no per-version records,
  no state machine.
- Default: key absent ⇒ latest version. Present ⇒ that exact asset.
- Dangling selection (asset id not found, e.g. hand-edited JSON, or a
  future manual delete): falls back to latest — "a dangling selection
  never destroys resolution" (implemented, keep).
- Selections are **never** moved by regeneration: regenerating B3 to v04
  does not change a standing v02 selection (§7: "may deliberately want an
  older version"). The Batch row shows "· v02 selected" so the curation is
  visible at rest.
- Clearing: "Latest version (default)" in the versions popover (emits the
  selection signal with an empty id) — implemented.
- Survival: Save/Restart/Reopen via project.json; Export/Reimport via
  export v2 (ids, not paths — immune to path rewriting). Implemented;
  add one regression test asserting the full round-trip (S17).
- Identity rule: selections reference model ids only. Filenames are never
  parsed for identity (keeps §17 safe and makes renaming harmless).

## S7. COMBINED OUTPUT SEMANTICS + RUN/VERSION/NAMING (brief §9–§12, §17, §18)

- Snapshot contract (§9): pressing Combine on composition
  B1v02/B2v01/B3v02/B4v01/B5v04 produces `Combined vNN` whose lineage is
  EXACTLY that tuple (implemented: `resolved_slot_sources` +
  `build_scene_combined_entry`). The snapshot never mutates afterwards.
- R3 (above) governs the selected_output question.
- Regeneration (§11): appends an asset only; the composition changes; the
  previous snapshot becomes stale (R1 semantics); the user chooses when to
  re-combine. Endorsed — auto-combine was correctly rejected by the
  original design (cost, surprise, combined-list flooding, and it would
  violate "the user alone decides").
- Partial regeneration (§12): confirmed exactly as the brief states;
  already pinned by tests ("partial regen does not degrade COMPLETE").
- Naming (§17): confirmed correct; version belongs to the slot output,
  combined version to the composition snapshot; combined filenames get
  scene-id disambiguators, project-combined keeps its timestamp suffix.
- Run vs version (§18): confirmed; keep them separate — run groups parts
  in History/Batch UI, version orders a slot's audio. Neither is ever
  used as the other.

## S8. STALE SEMANTICS — FINAL DEFINITION (brief §4)

**STALE ⟺ "this Combined output no longer represents the current Scene
composition."** Never "bad", never "unusable". Concretically (with R1+R2):

    stale(entry) =
        set(source.slot_id for entry.sources) ≠ set(expected slot_ids)
        OR ∃ source: resolved_asset_for_slot(scene, source.slot_id).id
                     ≠ source.asset_id
        OR ∃ referenced file missing from disk

Reason strings (keep the P3.30(f) approved wording; extend minimally):
- newer-version case (unchanged): "B1 has a newer version: v02"
- curated-older case (new): "B3's selected version (v01) differs from
  this Combined output (v02)"
- structural case (new): "Scene structure has changed since this Combined
  output (6 parts expected, 5 in snapshot)"
- missing case (unchanged): "source file missing: <name>"

Behaviour contract (all already implemented, unchanged by R1–R3):
- A stale snapshot is never the DEFAULT resolution (Rule 2 skips it).
- A stale snapshot is ALWAYS explicitly selectable, with the friendly
  Cancel / Use Anyway dialog (P3.30(f)) — after which it resolves as
  CUSTOM with a visible STALE marker.
- Staleness is NEVER persisted — always recomputed from the model; old
  projects adopt improved semantics automatically, with zero migration.

## S9. CUSTOM SEMANTICS (brief §8)

Answer: **CUSTOM means exactly one thing — "an explicit Scene-level output
selection is in force" (`selected_output` resolves).** It therefore covers
both "user explicitly selected an older Combined output" AND "user
explicitly selected a single asset". What it deliberately does NOT cover:

- Per-slot version curation (`selected_block_audio`) is **composition
  curation**, not output curation. It feeds Combine and the version list;
  it does not change what Assembly consumes by itself, so branding the
  Scene CUSTOM for it would be misleading. It is surfaced where it acts:
  the "· v02 selected" chip and the Combine source list.
- Three orthogonal dimensions, three orthogonal markers (unchanged Rec 7):
  coverage (COMPLETE/PARTIAL/…), curation (CUSTOM, output-level only),
  freshness (STALE/MISSING per output). No mixing.

## S10. PROJECT ASSEMBLY BEHAVIOUR (brief §5, §6 — the most important part)

Of the offered models:
- **A ("latest valid non-stale combined")** — necessary but insufficient
  alone: it has no answer for legacy Scenes (no combined outputs) and no
  answer for explicit user intent.
- **B ("auto-assemble the current composition")** — REJECT. It re-combines
  implicitly at assembly time: two assemblies minutes apart can differ;
  the project-combined lineage would point at per-slot assets that later
  change under it (provenance rot); the Combine step the user owns becomes
  decorative; silence/normalization decisions get applied where the user
  cannot preview them. It is the "always fresh" temptation that destroys
  the snapshot contract.
- **C ("force explicit re-combine")** — correct as a LAST RESORT state, not
  as a default (legacy Scenes would nag).

**Recommended (and implemented): A′ + C as fallback.** The chain in S4 #6.
For a long audiobook this is the least surprising and safest: the default
is always a real, inspectable, immutable FILE the user created (never a
virtual mix); staleness is surfaced, never silently consumed; and the
failsafe is an explicit review state. One UX upgrade to ship with R1/R2:
the requires_review state must always offer the one-click remedy —
recommended assembly-row actions **[Combine Scene Now] [Choose Output…]**
(Combine currently lives only in the Batch window; a review dead-end with
just "use Choose…" text invites the "stuck" feeling again). Small, purely
additive to the assembly row.

## S11. MISSING ASSET BEHAVIOUR (brief §14)

Confirmed and endorsed, with the exact implemented guarantees:
- Composition references B3 v02 whose file is missing → Combine BLOCKS
  with "'B3': audio file missing (path)"; nothing is skipped silently.
- Resolution never falls back to B3 v01 for a missing v02: the resolved
  entity is flagged MISSING; if it was explicitly selected → requires_review
  with "The selected … file is missing"; explicit re-selection of v01 by
  the USER is the only path back. (The one tolerated fallback — dangling
  *id*, not missing *file* — is the hand-edited-JSON tolerance in S6.)
- Rule 3's backward scan over assets (legacy scenes, no combined outputs)
  picks the newest asset whose file EXISTS. This is pre-P3.28 behaviour,
  kept deliberately for legacy scope only; slot-level resolution never
  does this. Document, don't change.
- Assembly records the resolved entity id in lineage, so a later missing
  file is diagnosable from the project-combined entry.

## S12. BATCH UI + COMBINED LIST RECOMMENDATION (brief §15, §16)

Keep exactly the implemented shape (it already meets the "not
overwhelming" bar):
- One row per block (grouped): checkbox · label/speaker ·
  "✓ Generated · N versions ▾" (popover: vNN · duration · filename,
  "✓ (in use)", "Latest version (default)") · optional "· vNN selected"
  chip when curated. The brief's "B3 [v02 selected]" is this chip.
- Combined section: resolved-output headline line, then newest-first rows
  "Combined vNN · duration · [STALE reason] · Use as Scene output ·
  Play · Export". Lineage detail on demand (tooltip/details), never
  expanded by default.
- Under R1/R2 the STALE reasons get more precise; no layout change.

## S13. PERSISTENCE RECOMMENDATION

No change. project.json holds all state (assets with provenance, slots,
combined entries with lineage, both selection maps); history JSONs hold
typed entries; everything is ids. Staleness/completeness are derived at
read. R1–R3 persist NOTHING new (R3 only writes `selected_output = None`
at Combine time — an existing field).

## S14. EXPORT / REIMPORT RECOMMENDATION

No change to shape: EXPORT_VERSION stays 2 (R1–R3 add no fields; derived
semantics travel for free). Verify in tests: after reimport,
`selected_block_audio` + `selected_output` still point at ids that exist
in the reimported scene (path rewriting doesn't touch ids), and combined
lineage resolves. If a future export ever breaks id references, the
dangling-tolerance rules (S6, S11) degrade safely.

## S15. MIGRATION IMPACT

**Zero for R1–R3.** All three are pure-function or handler-behaviour
changes; no stored field changes shape; no conversion pass. Effects on
existing projects:
- Snapshots judged stale ONLY because a newer version existed while the
  user curated an older one: badge disappears (correct).
- Snapshots from before a structure change: badge/review appears
  (honest upgrade of information, no data touched).
- Combined-after-explicit-selection workflows: resolved output follows the
  new snapshot (the intended fix).
One required re-verification: the P3.30(g) audit tests
(all-stale → requires_review) must be re-run under the new staleness
predicate — expected to still pass, since a genuinely outdated composition
still mismatches its lineage.

## S16. REQUIRED MODEL CHANGES (the punchline: none to the data model)

    Scene / AudioAsset / BatchJob / HistoryEntry shapes: UNCHANGED
    (exactly the P3.28 field set — still satisfies brief §19's "smallest
    possible architecture").

Implementation touch points for the accepted refinements (for the
follow-up task, NOT done now):
1. `engine/audio_provenance.py :: is_scene_combined_stale` — switch the
   per-source comparison from `latest_asset_for_slot` version-max to
   `resolved_asset_for_slot` asset-id equality; add slot-set comparison
   vs `expected_audio_slots`; add the two new reason strings.
2. `ui/main_window.py :: _on_combine_scene_requested` — set
   `scene.selected_output = None` before refresh + toast
   "Scene output → Combined vNN".
3. `ui/panels/assemble_dialog.py` — review-state remedy actions
   ([Combine Scene Now] reusing the existing combine handler /
   [Choose Output…] opening the existing picker). Optional but
   recommended.
4. No DB, no registry, no new entities, no auto-anything (§19 reaffirmed).

## S17. REQUIRED TESTS (follow-up phase; none written now)

New/updated, all in the established runtime style:
1. Curated-older selection + combine → snapshot NOT stale (R1 core).
2. Combine from B3v02, then explicitly select B3v01 → STALE with the
   curated-older reason string.
3. No-selection default: regen → newer-version stale reason unchanged
   (guards the P3.30(f) copy).
4. Added slot after combine → structural stale reason; Scene PARTIAL +
   review at resolution; re-combine clears it.
5. Removed slot after combine → structural stale.
6. Combine clears an explicit older selection; resolved output = new
   snapshot; toast fired (R3).
7. Combine with a single-asset (CUSTOM) selection → also cleared to the
   snapshot (R3 edge).
8. P3.30(g) regression: all-snapshots-stale → requires_review, no
   single-part fallback — re-run under the new predicate.
9. Selection round-trip: save → reload → export v2 → reimport →
   `selected_block_audio`/`selected_output` still resolve (S6/S14).
10. Dangling per-slot selection falls back to latest (exists — keep).
11. New reason strings match the approved wording templates (S8).
12. E2E: §3-of-brief flow — combine v01 → regen B3 → v02 → v01 STALE +
    Scene COMPLETE → assembly review → Use Anyway honoured → re-combine
    v02 → assembly consumes v02, lineage ids exact; save/restart between
    stages.

## S18. RISKS

| Risk | Severity | Mitigation |
|------|----------|------------|
| R2 makes more Scenes surface requires_review (structure changes) | Medium | One-click remedies in the review state (S10); reasons always name the fix; the alternative is silent content omission in the book — strictly worse. |
| R1 softens "newer version exists" signal (badge no longer fires for curated slots) | Low | "N versions ▾" + curated chip still show version wealth; optional tooltip "newer version available" in the popover (UI-only). |
| R3 surprise: Combine overrides a deliberate older output selection | Low | Toast announces the switch; reverting is one Choose…; not clearing is worse (button visibly does nothing). |
| Combine-time clear + later regen → review state users must learn | Low | This is P3.30(g)'s intended safety semantics; consistent story: "the book never silently uses outdated audio". |
| Test churn in stale-related suites | Low | All changes are in one pure function; reason-string assertions localized. |

## S19. FINAL RECOMMENDED ARCHITECTURE (for acceptance)

The current P3.28 model, refined by R1–R3 — i.e. exactly the brief's §21
target, with stale and combine semantics sharpened to match the brief's
own definitions:

    AudioAsset                  immutable, append-only, provenance-tagged
                                (block_id, part_index, part_version,
                                 generation_run)
    per-slot current audio      resolved_asset_for_slot: selection → latest
    Scene composition           DERIVED map over expected slots (never stored)
    Combine                     user-invoked snapshot; blocks on gaps/missing;
                                lineage-exact; R3: becomes resolved output
    combined outputs            append-only, inspectable, selectable
    STALE                       snapshot ≠ current composition
                                (asset-id mismatch ∨ slot-set mismatch ∨
                                 missing file); friendly Cancel/Use-Anyway
    CUSTOM                      explicit scene-level output selection in force
    completeness                slots × assets only (never freshness/selection)
    Project Assembly            resolve_scene_output only:
                                selection → latest fresh snapshot → legacy
                                asset → REQUIRES REVIEW (with one-click
                                remedy); lineage records the resolved id

Not built (reaffirmed): text diffing, prompt hashes, token tracking,
automatic invalidation/regeneration/combining, database, registries,
new Character/Voice models. The user alone decides when to regenerate and
when to combine; the system's whole job is to remember, derive, and never
surprise.

---

*Design review ends. Nothing was implemented. Awaiting agreement on R1, R2,
R3 (and the optional assembly remedy actions) before any implementation
phase begins.*
