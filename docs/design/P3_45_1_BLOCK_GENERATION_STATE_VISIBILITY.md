# P3.45.1 — Block → Part UX: Generation-State Visibility

**Task**: presentation-only wiring of the editor gutter's per-block
generation-state badges to the existing, tested provenance derivation.
**Baseline**: `main` @ `5f247db` (P3.45 triage gate complete; P3.44.9 and
P3.44.9.1 complete and frozen).
**Scope gate**: this slice ONLY. No P3.45.2 (oversized preflight), no
P3.45.3 (estimator consistency), no P3.45.4 (batch block identity), no
P3.45.5 (rendering hygiene).

---

## 1. CURRENT STATE VERIFIED (before implementation)

Every triage finding was re-verified at HEAD `5f247db`, branch `main`,
clean tree:

1. **`block_slot_states`** (`engine/audio_provenance.py:350-389`) —
   the DERIVED per-block state (never stored; P3.28 §4/Rec 5): one
   entry per block in expected-slot order with
   `{block_id, block_label, slots, covered, total, generated,
   version_count}`. A block is `generated` iff EVERY currently
   expected slot has ≥ 1 successful AudioAsset. Production consumers
   before this slice: NONE (only tests).
2. **`update_block_status`** — `narration_editor.py:578` (inner
   `BlockAwarePlainTextEdit`) and `:2186` (outer `NarrationEditor`
   delegate): sets `PromptBlock.status/progress/duration` and
   repaints the gutter. **ZERO production callers** (dead feedback
   path — the P3.45 triage's headline finding).
3. **Badge/progress painter** (`narration_editor.py:310-345`): paints
   "✓ Done" (SUCCESS) / "⚙ Gen..." (ACCENT) / "⏳ Queued" (DISABLED) /
   "⚠ Error" (ERROR) at the top-right of each gutter row, plus a
   progress bar when `status == "generating"` and `progress > 0`.
   Unknown statuses fall back to a TEXT_SECONDARY rendering.
4. **Completion paths**: single generation finishes →
   `_on_generation_finished_ui` (`main_window.py:2597`) →
   `register_generation_result` (THE ONE writer, `audio_provenance.
   py:961`) → sidebar refresh; every batch part flows through the
   same handler. Failure → `_on_generation_failed_ui` (`:2681`,
   scene-status recompute with failure hint). Batch end →
   `_finish_batch` (`batch_manager.py:1212`) → the
   `set_on_batch_completed` callback → `_on_scene_batch_completed`
   (`main_window.py:3614`, final coverage recompute).
5. **Batch start** (`_start_long_narration`, `main_window.py:3355`):
   materialises `expected_audio_slots` (the ONLY production
   materialisation site — `main_window.py:3410`; Re-detect does NOT
   touch slots) and sets the Scene to `SCENE_GENERATING` (`:3550`).
   Selective generation (`:3923`) and per-part regen (`:3771`) also
   set GENERATING for their runs.
6. **Scene/project load**: every path routes through
   `_load_native_project` → `_load_scene_state` (`:5328`) →
   `set_scene_blocks` (restores the whole block model, emits
   `blocks_changed`) → mode restore. `blocks_changed` was already
   consumed by `_update_prompt_preview` and `_sync_scene_characters`
   (`main_window.py:506-510`).
7. **Re-detect** (`narration_editor.py:_on_analyze_again` → rebuild
   branches → `_post_analyze`): every branch replaces block objects
   with FRESH ids (`_transfer_overrides` deliberately does not
   transfer id); emits `blocks_changed`.
8. **Per-keystroke edits** (P3.44.9 contract): `on_text_changed` →
   `_apply_edit_to_blocks` preserves the block OBJECTS — badge data
   survives ordinary editing; `blocks_changed` is NOT emitted on
   plain keystrokes.
9. **Version selection** (`_on_use_version_requested`, `:3947`):
   mutates `Scene.selected_block_audio`; `block_slot_states`
   (covered/total/version_count) is selection-independent.

**Where the UI went stale today**: it never went stale — it was never
populated. `PromptBlock.status` is only ever `None` (`from_dict` does
not restore it; no production caller ever set it), so the editor
showed ZERO generation state. The data and the visual both existed;
only the wiring was missing.

## 2. ROOT CAUSE

The P3.15-era badge API (`update_block_status` + painter) was built
for a MainWindow-driven generation loop that was never connected:
when P3.28 moved all generation-state bookkeeping to the derived
provenance model (`block_slot_states`, scene coverage states, the ONE
asset writer), no producer was wired to push that derived state into
the editor. The editor became a stateless bystander: the user had to
open the Batch window to learn anything about which block has audio.

## 3. IMPLEMENTATION (what was wired, which APIs reused)

The desired chain, exactly as tasked:

```
provenance state (slots × append-only assets)
  → block_slot_states            (existing, tested — reused verbatim)
  → MainWindow._sync_block_status_badges   (NEW: pure derivation)
  → NarrationEditor.update_block_status    (existing API — now called)
  → _BlockGutterWidget badge painter       (existing visual — now fed)
```

**`ui/main_window.py`** (+107 lines):
- `_sync_block_status_badges()` — derives per-block badge state from
  `block_slot_states(active_scene)` + the Scene's own derived status
  (`SCENE_GENERATING` / `SCENE_ERROR`, normalised through the
  existing `normalize_legacy_status`), and pushes EVERY block through
  the existing `editor.update_block_status` API (status `None` clears
  — a full deterministic re-write, no residue).
- One new signal connection: `blocks_changed → sync` (the block
  STRUCTURE transition signal).
- Four state-transition call sites: generation finished with an asset
  registered on the ACTIVE scene; generation failed on the ACTIVE
  scene; scene batch completed (run-end recompute); Generate Long
  start (run start + slot (re)materialisation).

**`ui/panels/narration_editor.py`** (+58 lines):
- The OUTER `update_block_status` now writes the state on the
  AUTHORITATIVE block model (`NarrationBlockManager` — the same
  PromptBlock instances the gutter mirror is fed from) BEFORE
  delegating to the inner editor. This closes a real ordering hazard:
  `blocks_changed` fires BEFORE `_render_block_visuals` re-feeds the
  visual mirror on scene restores and Re-detect, so a status pushed
  through the mirror alone would land on discarded objects and be
  lost. The inner API + gutter repaint are unchanged.
- `reset_all_block_statuses` (outer) clears the model too (same
  reasoning).
- Painter: multi-part blocks present their slot coverage — "✓ 2/2"
  (done) / "⚙ 1/2" (generating) — derived from the pushed
  `parts_done/parts_total`. Same font, same draw rectangle, same
  culling: **the P3.44.9.1 geometry is untouched; only the label text
  varies.** Single-part blocks keep the classic labels; error/queued
  keep the existing taxonomy.

**`engine/narration_blocks.py`** (+16 lines):
- `PromptBlock.parts_done` / `parts_total` runtime display fields —
  the SAME contract as `status/progress/duration`: display-only,
  excluded from `has_overrides`, never persisted (`to_dict`/
  `from_dict` unchanged), never a second source of truth (a pushed
  presentation copy, re-derived on every sync).

## 4. REFRESH POINTS (and why exactly these)

| # | Point | Why |
|---|-------|-----|
| 1 | `blocks_changed` | Block STRUCTURE transitions: scene/project restore (`set_scene_blocks`), Re-detect, programmatic text swap — the block identity set changed, so the badge set must be re-derived. |
| 2 | generation finished (active scene) | An asset was appended to the active Scene's stream — coverage changed; a fully covered block flips to "done" immediately (mid-run). |
| 3 | generation failed (active scene) | The failure recompute may move a zero-coverage scene to ERROR — blocks then show the existing error badge. |
| 4 | scene batch completed | The final coverage recompute cleared GENERATING — badges settle to their truthful end state. |
| 5 | Generate Long start | The run started AND the expected-slot structure was (re)materialised — badges appear ("generating" for uncovered, "done" held for already-covered blocks whose previous versions stay valid). |

**Deliberately NOT wired** (documented, per "only wire what the
architecture requires"):
- `_on_use_version_requested` — `block_slot_states` exposes no
  selected-version input (covered/total/version_count are
  selection-independent); there is no badge change to reflect. Tested
  for stability instead (Test 7).
- Per-keystroke — block objects survive edits (P3.44.9); slots are
  never touched by edits (§0 product rule).
- Per-job progress — MainWindow receives no job-level progress
  events; the progress bar stays absent (`progress = 0.0`), the
  generating badge itself is the indication. No polling, no timers.

## 5. TESTS (new focused suite)

`tests/test_p3_45_1_block_generation_state_visibility.py` — 20 tests
through the REAL MainWindow + Engine + BatchManager pipeline (fake
model boundary, house pattern), editor restored via the REAL
`_load_scene_state` path:

- **Test 1** initial state: no slots / zero-coverage slots / block
  without slots → no badge, never a stale state (3 tests, incl. the
  ERROR-scene zero-coverage representation and no-slot isolation).
- **Test 2** real generation completion: full Generate Long run
  (real registration path) → done badges incl. the multi-part
  "2/2"; review-mode start → deterministic "generating" observation
  with coverage payload.
- **Test 3** isolation: only b1's part generated → b2/b3 keep no
  badge (and no stale generating after settle).
- **Test 4** regeneration: per-part regen as a new version → badge
  stays "done" (append-only validity), slot has 2 versions.
- **Test 5** reload: persistence round-trip + real scene restore
  reconstructs badges from the model; switching to an ungenerated
  scene clears them (no widget-state carryover).
- **Test 6** re-detect: real re-detect path → new ids claim nothing;
  slots/assets untouched (state follows the SURVIVING slots).
- **Test 7** version selection: badge stays truthful; no refresh
  required (documented architectural fact).
- **Test 8** no stale state: re-materialisation (b2: 2→3 parts) →
  stale "done" replaced by "generating 2/3", then settled to no
  badge; keystroke edits do not destroy badge data.
- **Paint checks** (real gutter grabs → pixel scans): done badge
  renders green; error badge renders red; NO badge pixels when
  ungenerated; badge disappears after state invalidation.
- **Frozen-contract pins**: sync never mutates block structure/
  offsets/slots/assets; `to_dict` carries no badge fields; scrolling
  never mutates badge state (Case-A purity).

## 6. VALIDATION (all run at the implementation commit)

| Battery | Result |
|---|---|
| Focused P3.45.1 suite | **20/20** (run twice — stable) |
| P3.28 + P3.44.5 + P3.44.6 provenance suites | **134 passed** |
| P3.44.9 + P3.44.9.1 frozen suites | **95 passed + 35 subtests** |
| Full battery (12 foreground chunks) | **1583 passed + 1 failed (SS-3, pre-existing, OPEN in the register — fails identically on pristine) + 35 subtests** = the documented 1563 baseline + exactly the 20 new tests |
| `verify_compile` | **148/148 files PASS** (147 + the new test file) |
| `verify_architecture` | **PASS** |
| `verify_functional_integrity` | **80/80** |
| Launch smoke (real entry path) | **11/11** |

## 7. P3.44.9 / P3.44.9.1 REGRESSION CHECK

- The P3.44.9 suite (`test_p3_44_9_block_offset_delete_integrity.py`)
  and the P3.44.9.1 suite
  (`test_p3_44_9_1_block_gutter_scroll_sync.py`, including the
  every-scroll-position alignment sweep and the five permanent
  control tests) re-ran GREEN inside the full battery AND standalone:
  **95 passed + 35 subtests**.
- No line of `_block_rows()`, scroll-coordinate calculations,
  `updateRequest`/scroll synchronisation, block offset calculations
  or block range semantics was touched. The painter change is
  label-text only (same QFont, same QRect, same culling).
- Additional pins in the new suite: the sync never mutates block
  identity/offsets; scrolling never mutates badge data.

## 8. SCOPE CHECK

- No new identity system; `slot_id` untouched; no fingerprints, no
  `GenerationPlan`, no `STALE`, no session locking, no hidden
  sub-sessions.
- No new source of truth: `parts_done/parts_total` are a pushed
  presentation copy with the exact `status/progress/duration`
  contract (runtime display state; never persisted, never semantic).
- No timers, no polling, no animation loops — one signal connection
  + four state-transition call sites.
- No structural block-range changes; no Batch redesign; no Scene
  Combine change; no Manual Concatenate change; no sentence
  splitting change; no duration estimation change; no oversized
  preflight; no unrelated rendering refactor; no P3.45.2+ work.

## 9. REMAINING RISKS (evidence-based only)

1. **Mid-batch badge granularity**: "generating" is scene-level
   (SCENE_GENERATING) — during a run, ALL uncovered slotted blocks
   show the generating badge even if their specific job is still
   PENDING (review mode shows it before any job runs, mirroring the
   scene's own sidebar badge, which has had the same semantics since
   P3.28). Per-job badge granularity would require job-level events
   at MainWindow — deliberately out of this slice's scope.
2. **Regenerating a covered block** keeps "done" during the regen
   (the previous version remains valid — P3.28's append-only
   doctrine; the Batch window shows the actual job progress). This
   is the truthful "currently valid state", as tasked.
3. **Re-detect clears all badges** (new block ids cannot claim old
   slots) while the slot structure stays keyed to the old ids until
   the next Generate Long — the pre-existing P3.45 triage finding;
   the badge honestly presents that detach. Fixing it means
   re-materialising slots on re-detect (a P3.45.2+ structure
   decision, NOT taken here).
4. **Partial idle blocks show no badge** (some parts covered, no run
   in flight): the existing badge vocabulary has no honest
   "partially generated, nothing queued" representation; showing
   nothing claims nothing (the Batch window holds the detail). A
   dedicated partial presentation would extend the taxonomy —
   deliberately not invented in this slice.
5. **Scene status lifecycle is inherited as-is**: if a future defect
   leaves a scene stuck in GENERATING (a pre-existing
   register-level concern), badges would faithfully show
   "generating". The badge derives from the same scene state the
   sidebar already shows.

## 10. FILES / COMMIT

- `engine/narration_blocks.py` — `parts_done`/`parts_total` runtime
  display fields + `has_overrides` exclusion note.
- `ui/panels/narration_editor.py` — outer `update_block_status`
  model-first write + parts payload; outer `reset_all_block_statuses`
  model clear; painter multi-part coverage labels.
- `ui/main_window.py` — `_sync_block_status_badges()` + the
  `blocks_changed` connection + four state-transition call sites.
- `tests/test_p3_45_1_block_generation_state_visibility.py` — NEW,
  20 focused tests.
- `docs/design/P3_45_1_BLOCK_GENERATION_STATE_VISIBILITY.md` — this
  record; `DEVELOPMENT_LOG.txt` P3.45.1 entry; `OPEN_BUGS.md`
  P3.45.1 round.
- Commit: see §10 of the development log / the repository history
  (single commit on `main`, pushed).

---

## MANUAL GUI CHECK (offscreen visual verification)

Human screen inspection is not possible in this environment (stated
explicitly). Instead, the REAL MainWindow pipeline was rendered
offscreen (`ss/audit_p3451_visual2.py` — real Generate Long +
selective generation + scene restore) and the gutter grabs were
verified by vision-model inspection of the PNGs:

| Render | Expected | VLM read back |
|---|---|---|
| A: mixed, run in flight | B1 none · B2 ✓ Done · B3 ✓ 2/2 · B4 ⚙ 0/2 · B5 ⚙ Gen... | **exactly as expected** |
| B: settled partial | B2 ✓ Done · B3 ✓ 2/2 · B4/B5 none | **exactly as expected** |
| C: ERROR scene | B1/B2 ⚠ Error (red) · B3-B5 none | **exactly as expected** |
| D: scrolled | badges travel with their blocks | rows re-anchor per the frozen P3.44.9.1 geometry; badge data pure under scroll (test-pinned) |

A pixel-delta scroll measurement was attempted and is NOT achievable
here: under the offscreen platform the lazy
`QPlainTextDocumentLayout` stays permanently in-flight (the
documented P3.44.9.1 harness raciness) — the paint-time row basis and
any synchronous `_block_rows()` reading can drift >10px apart between
event-loop windows. The attachment is instead guaranteed structurally
(the badge is painted in the SAME paint pass at the SAME row `gy` as
its block's header — one loop iteration, inseparable), pinned by the
frozen P3.44.9.1 suite (rows == text anchors at every scroll
position) and by the new scroll-purity test.

**STOP after P3.45.1. P3.45.2 is NOT started.**
