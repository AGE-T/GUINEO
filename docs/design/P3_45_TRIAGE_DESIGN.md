# P3.45 — Triage / Design Gate Report

**Phase**: P3.45 (Block → Part UX / Oversized Block Preflight /
Duration Estimation / Rendering & UX Hardening)
**Status**: TRIAGE COMPLETE — DESIGN GATE. No implementation performed
in this run (per the P3.45 execution rule: reconnaissance →
architecture map → dependency map → first slice definition; STOP
before implementation).
**Baseline**: `eadbfff` (P3.44.9.1 complete), branch `main`, clean
tree, in sync with origin. Baseline validation as reported: focused
P3.44.9.1 19/19; full battery 1563 passed + 1 known pre-existing
SS-3 failure; launch smoke 11/11; verify_compile 147/147;
verify_architecture PASS; verify_functional_integrity 80/80.
**Method**: full-repository code trace (engine + ui, no
filename-based assumptions) by four parallel read-only exploration
passes (engine generation flow; editor/main-window UX;
provenance/combine; batch UI) plus direct verification of every
load-bearing claim against the current tree. All references below
are `file:line` at `eadbfff`.
**Pre-existing analysis reused** (evidence, not assumption):
`docs/audits/P3_44_5_RESEARCH_AUDIT.md` §5.1/§5.7/§5.8/§6/§9/§12
(Block ≠ Part visibility gap; oversized-block UX gap; /15 estimate
inflation; dead code inventory) — every reused claim was
re-verified at current HEAD before inclusion.

---

## 1. CURRENT ARCHITECTURE

### 1.1 Logical structure layer (frozen)

- `Scene.text` + `Scene.narration_blocks` — serialised `PromptBlock`
  dicts (`id`, `start_offset`/`end_offset`, five override values,
  `label`, `locked`, `manually_edited`, `character_id`,
  `lost_character_id`, SFX/pause insertions;
  `engine/narration_blocks.py:38-96`, `models.py:505-512`). The
  editor document is the source of truth; blocks own offsets, never
  text.
- Detection: `HeuristicBlockDetector.detect`
  (`engine/block_detector.py:61-96`), boundary score ≥ 50,
  delegating sentence scanning to the ONE authoritative
  `engine/sentence_boundaries.py` (P3.44.8).
- Offset integrity: `NarrationBlockManager._apply_edit_to_blocks`
  (P3.44.9) — FROZEN, not to be touched by P3.45.

### 1.2 Generation structure layer (slot materialisation)

- There is exactly ONE production structure event: **Generate Long
  confirm**. `NarrationSplitter.split()` produces sentence-grouped
  parts (`TARGET_SENTENCES=3`, `MAX_CHARS=400`, `MIN_CHARS=50`;
  `engine/narration_splitter.py:85-87`); a block ≤ 400 chars yields
  exactly ONE part (`:547-548`); each part is stamped
  `part_of_block`/`total_parts_in_block` (`:594-595`),
  `source_block_id` (`:591`) and `estimated_duration =
  len(text)/15.0` (`:573`; same formula in the speaker/raw/plain
  branches `:421/:504/:642`).
- `LongNarrationDialog` shows the parts (editable plain text, header
  `"Part N | speaker/block_label | ~est s | tokens | chars"`,
  `ui/panels/long_narration_dialog.py:394-406`; dialog header shows
  total parts + estimated total `:93-106`).
- On confirm, `MainWindow._start_long_narration`
  (`ui/main_window.py:3355-3612`) calls
  `materialize_expected_slots(parts)`
  (`engine/audio_provenance.py:130-158`) which REPLACES
  `scene.expected_audio_slots` wholesale (`main_window.py:3410-3413`
  — the only production write site, confirmed also by
  `docs/design/P3_44_6_GENERATION_STRUCTURE_IDENTITY.md`), allocates
  a `generation_run` (`"{scene8}-r{NNN}"`,
  `audio_provenance.py:474-495`), and creates one `BatchJob` per part
  with `slot_id` (`"{block_id}:{part_of_block}"` /
  `"plain:{part_index}"`, `audio_provenance.py:120-127`), GLOBAL
  `part_index` (queue position, `main_window.py:3496`),
  `part_version` (asset-aware `next_slot_version`,
  `audio_provenance.py:502-524`), `source_block_id`,
  `expected_duration` (`main_window.py:3536`).

### 1.3 Execution layer

- `BatchManager` queue: `PENDING/GENERATING/COMPLETED/FAILED/
  SKIPPED` (`engine/batch_manager.py:60-65`), single-flight guard,
  run-scoped `start(only=[...])`, cooperative stop; no text-size or
  part-count logic exists in the manager at all.
- `BatchJob.to_request()` forwards the full provenance
  (`batch_manager.py:262-265`) → `Engine.generate()`. **The engine
  NEVER splits text**: `request.text` is used verbatim as the final
  prompt (`engine/engine.py:252-266`); `Engine._validate` checks only
  empty prompt/params/voice (`engine.py:415-477`) — no length check.
  Single worker (`engine/workers.py:51-52`).
- Output guard (P3.27B): anomaly DETECTION only — R1 runaway
  trailing silence, R2 expected-duration multiple (3.0× AND +20 s —
  only when `expected_duration` was supplied), R3 silent output
  (`engine/output_guard.py:145-204`); records
  `token_ceiling_s = max_new_tokens / 25`
  (`output_guard.py:201-203`).
- Single (non-Long) **Generate** compiles the WHOLE scene into ONE
  request with `expected_duration=None` and creates no slots
  (`main_window.py:2362-2447`); in Narration Blocks mode the plain
  Generate capability is disabled entirely
  (`narration_editor.py:2222-2244`).

### 1.4 Result layer (frozen identity model)

- `register_generation_result` is the ONE writer: appends the asset
  dict (with `block_id`, `part_index`, `part_version`,
  `generation_run`, `slot_id`) to the owning `scene.audio_assets`
  (append-only; an asset exists ⟺ its generation succeeded) and
  recomputes the derived scene status
  (`engine/audio_provenance.py:961-1033`).
- Versioning: monotonic max+1 with disk guard; selection semantics:
  explicit `scene.selected_block_audio[slot_id]` wins, dangling →
  latest, empty → latest (`resolved_asset_for_slot`,
  `audio_provenance.py:314-327`).
- Identity join: `slot_of_asset` — modern assets join by recorded
  `slot_id` only, never positionally (`audio_provenance.py:174-277`).

### 1.5 Consumption layer

- **Playback**: batch rows resolve audio via
  `resolved_asset_for_slot` (selection-aware;
  `batch_generation.py:2065-2094`); the bottom transport plays
  whatever was last loaded into it — selecting a block in the editor
  changes nothing audio-related.
- **Scene Combine**: `resolved_slot_sources` — every expected slot,
  in slot order, one RESOLVED version each; uncovered/missing slots
  BLOCK with a modal (`audio_provenance.py:697-740`,
  `main_window.py:4095-4102`); versioned output appended to
  `scene.combined_outputs` with full per-slot lineage; clears
  explicit scene output selection (R3) (`main_window.py:4070-4235`).
- **Manual Concatenate** (manual queue mode only): the literal
  `output_path` list of COMPLETED jobs in QUEUE order — no slot
  resolution, no versioning, fixed filename overwritten in place
  (`outputs/long_narration_full.wav`), no scene entry, plain history
  entry (`main_window.py:4367-4592`;
  `docs/design/COMBINE_VS_CONCATENATE_AUDIT.md`).
- **Project Assembly**: multi-scene, each scene represented by the
  `resolve_scene_output` chain (`engine/combined_audio.py:350-532`,
  `audio_provenance.py:783-944`).

### 1.6 UI presentation surfaces

- **Editor gutter** (`ui/panels/narration_editor.py`): `B{N}` label
  + character name/colour + override pills + a per-block status badge
  and progress-bar painter — fed by `update_block_status()`
  (`:578-598`) which has **ZERO callers** (dead API; verified by
  repo-wide grep excluding tests — only self-references at
  `:2186-2198` exist).
- **Batch table** (`ui/panels/batch_generation.py`): one row per
  part/job; `#` column = global queue position; "Generated" cell
  shows `✓ Generated · N versions ▾ · vNN selected`; version
  popover with per-version duration/play/use; NO block identity
  column anywhere.
- **Preview dialog**: flat part list, block_label only as a repeated
  text fragment.
- **Sidebar**: scene-level derived status badges only.
- **Status bar**: booleans (Generating…/Ready) and per-generation
  timing; no block/part/slot information.

---

## 2. BLOCK → PART UX GAPS

All gaps below are proven from code; each lists where the
information EXISTS and where it is (or is not) PRESENTED.

| # | Model knows (file:line) | UI shows |
|---|---|---|
| G1 | Per-block generated state is derivable: `block_slot_states` returns per-block `covered/total/version_count` (`audio_provenance.py:350-389`; consumed today only by tests) | **Nothing.** The gutter badge painter + `update_block_status` API exist but are dead code; `PromptBlock.status/progress/duration` are runtime-only, unpainted, unrestored (`narration_blocks.py:91-96`, `:125-149`) |
| G2 | Parts-per-block: `SplitPart.part_of_block/total_parts_in_block` stamped since P3.15 (`narration_splitter.py:594-595`) | **Nothing renders it.** Grep across `ui/`: zero display uses. The audit's "19 blocks → 31 parts" phenomenon is data-present, UI-invisible (`P3_44_5_RESEARCH_AUDIT.md` §5.1) |
| G3 | Batch rows carry `slot_id`/`source_block_id` (`batch_manager.py:143-148`, persisted `:170-181`) | Rows show only the global `#` position and the output filename; no block label, no "part X of Y of block B" |
| G4 | Block naming | Three divergent default namespaces for the same object: editor gutter `B{N}` (position-derived, `narration_editor.py:290`), preview dialog `Block {N}` (splitter default, `narration_splitter.py:557`), user label (when set) |
| G5 | Selected version per slot: `Scene.selected_block_audio` written only from the Batch version popover (`main_window.py:3947-3967`) | Visible ONLY inside the Batch dialog; the editor's block properties panel has no audio section at all (`narration_editor.py:996-1167`); selecting a block in the editor has no audio consequence |
| G6 | Missing/failed slots: combine blockers (`audio_provenance.py:719-725`), coverage PARTIAL/ERROR | Surfaced only as a Combine-time modal and a scene-level sidebar badge; no persistent per-block "not generated" marker in the editing surface |
| G7 | Single-Generate assets sit OUTSIDE the slot structure (`slot_id=None`, `audio_provenance.py:1005-1009`) | No user indication that a plain Generate result will not count toward Combine coverage (first feedback: "This Scene has no generation structure yet" at Combine time, `main_window.py:4103-4109`) |
| G8 | Manual Concatenate is un-versioned, queue-ordered, lineage-free, invisible to Assembly (`main_window.py:4367-4592`) | No warning of any of these properties |
| G9 | During a batch run the scene model changes per job | The main-window waveform shows the LAST FINISHED PART; the editor shows nothing per job; project file is not persisted until Combine/Assembly/Save |
| G10 | `HistoryEntry` carries `part_index/part_version/block_id/generation_run` (`history_manager.py:269-360`) | History UI renders none of them (no part/version/block columns); `slot_id` is not recorded at all |

**Classification of the candidate repairs** (required before
implementation): G1, G2, G3, G5, G6, G9 are **presentation-only**
(consume existing derived state; no data-flow change); G4 is
presentation-only (label normalisation); G7, G8 are presentation +
small messaging/flow additions; G10 is a data-flow addition
(recording `slot_id` in history) — none requires a structural or
architectural change. The existing identity model
(`slot_id` / `generation_run` / `part_version` / asset lineage) is
sufficient for every gap — **no new identity system is proposed**,
and none is needed.

---

## 3. OVERSIZED BLOCK

### 3.1 Current limits (all verified at HEAD)

- Splitter: `MAX_CHARS=400` per part, `TARGET_SENTENCES=3`,
  `MIN_CHARS=50` (`narration_splitter.py:85-87`). Block ≤ 400 chars
  → exactly one part (`:547-548`); longer blocks decompose into
  sentence groups. **Short blocks are never merged** (one block ≥ one
  part).
- **Bypass 1 — single oversized sentence**: a sentence > 400 chars
  becomes its own part with NO upper cap (`:727-735`).
- **Bypass 2 — speaker turns**: `_split_with_speakers` creates ONE
  part per `$SPEAKER:` turn with NO size check at all — `MAX_CHARS`
  is never referenced in that path (verified by grep: the constant
  appears only at `:86/:526/:547/:698/:713/:728/:742/:743`); a
  5,000-char turn becomes one giant job.
- No max-parts-per-block and no total-parts cap exist anywhere.
- The engine performs no splitting and no length validation
  (§1.3); `BatchManager` has no size logic.

### 3.2 The actual technical constraint

The Higgs Audio V3 model generates autoregressively at 25 fps and
stops at the `max_new_tokens` budget:
`max_new_tokens / 25` seconds of audio ceiling
(`output_guard.py:8-15`, `:61-63`). With the default
`max_new_tokens=4096` (`models.py:50`) that is **163.84 s** per
generation; the observed P3.27B defect (163.56 s = 99.83 % of the
ceiling) confirms the mechanism. The parameter is user-configurable
(128–8192, `models.py:50`), so the ceiling ranges 5.12 s–327.68 s —
**any preflight must read the actual request parameters, not a
constant**.

Two documented consequences:

1. **Silent mid-speech truncation risk**: a part whose audio would
   exceed the ceiling stops at the budget. The guard CANNOT flag
   this case: R2 requires `total ≥ 3× expected` (truncated output is
   BELOW expectation), and R1 only catches the silent-tail
   signature, not a mid-speech cut. At the splitter's own 15
   chars/s heuristic, 163.84 s ≈ 2,457 chars — every bypass-1/2 part
   above that size is a silent-truncation candidate.
2. **Stale comment**: `models.py:50` says "4096 ≈ 30s audio",
   contradicting the module-documented 25 fps math
   (`output_guard.py:15`). The comment must not be used as a
   constraint basis and should be corrected when the preflight lands.

### 3.3 Where the user stands today

- **Preview time** (Generate Long): the dialog DOES show "N parts"
  and per-part `~est` — but flat, without per-block grouping, and
  WITHOUT any warning when a part's estimate approaches or exceeds
  the token ceiling. `estimated_duration` is not recomputed after
  the user edits a part's text in the dialog
  (`long_narration_dialog.py:491-522` updates `prompt`/`char_count`
  only) — the stale pre-edit estimate flows into `BatchJob.
  expected_duration` and thence into the guard's R2 comparison.
- **Single Generate path**: the whole scene in one request,
  `expected_duration=None` — the LEAST protected path (no guard
  input at all).
- **Batch**: no preflight whatsoever; oversized blocks expand into
  N jobs silently (only visible as the row count in the preview).

### 3.4 Safe preflight insertion points

1. A **pure classification function** over the `SplitPart` list +
   the request's `max_new_tokens`: per part →
   `within-range / likely-multi-part(source block) / cannot-safely-
   process-as-one-unit (estimate ≥ ceiling with explicit margin)`.
   Pure, deterministic, unit-testable, no block mutation.
2. **Preview dialog surfacing**: per-part header badge + per-block
   grouping line ("Block B7 → 4 parts"), plus a warning row for
   ceiling-exceeding parts. Presentation-only.
3. Optionally the same classification in `_start_long_narration`
   (and later the single-Generate path) as a confirmation gate.

**Product semantics preserved**: no automatic splitting, no silent
rewriting of user content — the splitter already performs the
decomposition; the preflight only COMMUNICATES it (and flags the
cases the decomposition cannot fix: uncapped sentences and speaker
turns).

---

## 4. DURATION ESTIMATION

### 4.1 Current estimator

One formula, five call sites, **two different text bases**:

| Site | Base | Purpose |
|---|---|---|
| `narration_splitter.py:421/504/573/642` | **plain text** `len(text)/15.0` | `SplitPart.estimated_duration` → preview display + `BatchJob.expected_duration` |
| `narration_editor.py:2093` | **tokenised prompt** `len(final_prompt)/15.0` | Preview tab stats label "Est. duration: ~Xs" |

### 4.2 Known inaccuracies (evidence)

- **Base inconsistency (proven, +102 %)**: the editor Preview counts
  Higgs-token-inflated prompt characters while the splitter counts
  plain text — for the SAME text the two surfaces disagree by the
  measured token-inflation factor (+102 %, `P3_44_5_RESEARCH_AUDIT.
  md` §5.8: "prompt characters counted, +102 % measured").
- **Stale after edit**: preview-dialog edits do not refresh
  `estimated_duration` (§3.3), so the guard compares against a
  pre-edit estimate.
- **Uncalibrated constant**: 15 chars/s has never been calibrated
  against real generations; the audit records that no real corpus
  existed at measurement time. The guard's thresholds (3.0× AND
  +20 s) are deliberately insensitive to this uncertainty.
- **Measured durations exclude post-processing**: `output_duration`
  is measured on the raw model waveform BEFORE `append_silence`
  (0.5 s) and normalisation (`generation_manager.py:244-247`) — the
  Batch Duration column therefore shows 0.5 s less than the file.
  `assemble_dialog.py:842-850` labels a MEASURED resolved-duration
  total as "Live total estimated duration" — a mislabel.

### 4.3 Is improvement justified?

- **Consistency/labeling: YES** — two disagreeing estimates for
  identical text is a defect of information, not of precision; the
  stale-after-edit case feeds a safety comparator (the guard); the
  assemble label is factually wrong. These are the smallest
  evidence-backed fixes: unify the Preview base to plain text (or
  relabel it as prompt-length-derived), recompute on edit, fix the
  assemble label.
- **Precision (a better formula): NOT YET JUSTIFIED** — no corpus of
  real generations exists to prove where the current estimate is
  wrong, by how much, or for which input types; the only functional
  consumer (guard R2) is insensitive to ±50 % error by design. Per
  the P3.45 rule, the estimator is NOT replaced on the imagination
  of a smarter formula. A calibration corpus can accumulate
  passively (the system already records measured durations) and a
  recalibration can be proposed later WITH data.
- Whatever lands, the number must remain clearly an estimate.

---

## 5. RENDERING / UX HARDENING

### 5.1 Proven defects (code evidence, present at HEAD)

- **D-R1 — duplicated `app_root` property**: `engine/engine.py:490-
  492` AND `:785-786` — the second definition shadows the first
  (identical bodies today; latent divergence). Flagged in the
  P3.44.5 audit §6, still present (verified).
- **D-R2 — `_check_states` keying defect**: index-keyed
  implementation vs slot-keyed docstring
  (`batch_generation.py:779-781` vs `:2154-2156`); `_on_load_queue`
  (`:2840-2854`) replaces the job list WITHOUT re-deriving the
  checkmarks — stale index→checked mappings can attach to the wrong
  jobs (audit L-3, still present). Touches the P3.44.4 checked-regen
  flow → needs its own analysis slice.
- **D-R3 — redundant full-document copies per paint**:
  `toPlainText()` runs in `paintEvent` (`narration_editor.py:187` —
  its result is never even used in the paint body; `ln_width`
  at `:188` is a dead local), again in `_block_rows` (`:134`), in
  `mousePressEvent` (`:435`), and per cursor move in
  `_highlight_current_line` (`:692`) — measurable cost on large
  documents, pure waste in two of the four sites.
- **D-R4 — full viewport repaint per no-op click**:
  `_on_block_click_requested` re-runs `_render_block_visuals()` on
  EVERY click even when the selection did not change
  (`narration_editor.py:1813-1824`) → `viewport().update()`.
- **D-R5 — flash animation repaints the text viewport**:
  `_on_flash_tick` (`:658-664`) repaints gutter AND viewport every
  30 ms although only the gutter paints the flash overlay.
- **D-R6 — hardcoded colours bypass the theme system**: block
  background stripes `"#252840"`/`"#222538"` (`:693-696`) while all
  other surfaces use `Palette` tokens.
- **D-R7 — combined-rows churn**: `_refresh_combined_section`
  destroys and recreates every combined-output row on every manager
  change (`batch_generation.py:2356-2361`), in contrast to the
  table's signature-gated in-place updates.
- **D-R8 — dead code**: `ui/panels/modern_control_panel.py` (1,971
  lines, zero production importers — confirmed again at HEAD);
  unused `PromptOptimizer`/`PromptBuilder` imports in the splitter
  (audit §6).
- **D-R9 — Generate-button mid-batch flicker** (audit L-2): per-job
  started/finished events toggle the Generate buttons; capability
  re-derivation on each job finish (`main_window.py:2603`) does not
  consult `bm.is_running` — still present by code structure; to be
  behaviour-verified in a later slice.
- **D-R10 — private-attribute reach-ins**: `main_window.py` calls
  `engine._audio.concatenate*` / `engine._history.add` /
  `engine._event_bus.emit` directly (`:4131-4141`, `:4200-4210`,
  `:4459-4501`, `:4577-4580`) — architecture-rule violation, stable
  today, hygiene item only.

### 5.2 Documented but NOT runtime-provable here

The three-layer transparency stack + GL-composited window text
softening (contrast 229→219 measured offscreen; Windows ClearType
loss inferred from external Qt documentation —
`P3_44_5_RESEARCH_AUDIT.md` §12, `docs/design/
FONT_RENDERING_METRICS_AUDIT.md`). This is a cross-platform
rendering architecture concern requiring its own decision — NOT a
P3.45 default work item.

### 5.3 Cosmetic preferences (explicitly NOT defects)

Label-namespace differences beyond the functional G4 issue, absence
of a toast system, Advanced-tab Generate-button placement, waveform
showing the last part during a batch run. These may inform UX
slices but are not hardening targets.

### 5.4 Frozen surface

P3.44.9.1 gutter/scroll synchronisation: no evidence of regression
found during this trace; it is NOT reopened. Any P3.45 slice that
touches `narration_editor.py` must keep the geometry path
(`_block_rows`, the `updateRequest` repaint wiring) untouched and
re-run the P3.44.9.1 suite.

---

## 6. DEPENDENCIES

- **Slice 1 (gutter generated-state feed)** depends on nothing else;
  it consumes `block_slot_states` (existing, tested) through the
  existing `update_block_status` API at existing push-refresh
  points. It must respect the pull-based invalidation constraint
  (§5 of the P3.44.9.1 record: the gutter repaints only on explicit
  triggers — the refresh points must be enumerated, no timers).
- **Slice 2 (oversized preflight)** is independent of Slice 1; it
  lives in the splitter-classification + preview-dialog layer. It
  shares the preview dialog with the estimate-consistency fix
  (recompute-on-edit), which argues for landing them together or
  back-to-back.
- **Slice 3 (duration consistency)** — preview base unification
  (`narration_editor.py` stats label) is independent; assemble label
  fix independent.
- **Slice 4 (batch-table block identity)** depends on a presentation
  decision informed by Slices 1–2 (which block naming to surface —
  G4 normalisation) and touches the batch table rendering contract
  (P3.40 signature-gated updates must be respected when adding a
  column).
- **Slice 5 (rendering hygiene)** must be kept in SEPARATE commits
  from behaviour slices (dead code removal, D-R1, D-R3/D-R4/D-R5
  reductions) — mixing hygiene with behaviour would blur the
  regression attribution.
- **Later/escalated items** (manual-concatenate semantics,
  single-Generate slot messaging, history `slot_id`, `_check_states`
  re-keying, transparency stack) each depend on product/architecture
  decisions, not on each other.

---

## 7. RECOMMENDED EXECUTION ORDER

The order below follows code dependency and regression risk, not
generic priority:

1. **P3.45.1 — block generation state in the editor gutter** (G1/G6
   subset). Both endpoints exist and are tested; the change is
   presentation-only on the primary editing surface; it exercises
   the exact repaint path P3.44.9.1 just stabilised, which is also
   the main risk — mitigated by re-running the frozen suites.
   Lowest-risk first slice also establishes the refresh-point
   enumeration needed by every later editor-state slice.
2. **P3.45.2 — oversized-part preflight classification + preview
   surfacing** (§3.4). Pure function + dialog labels; closes the
   silent-truncation information gap; uses the REAL constraint
   (`max_new_tokens/25`), not an invented limit. Includes
   recompute-estimate-on-edit (same dialog) and the `models.py:50`
   comment correction.
3. **P3.45.3 — duration-estimate consistency** (preview base
   unification, assemble label). May partially land with P3.45.2;
   no formula change.
4. **P3.45.4 — batch-table block identity** (G3/G4): display-only
   column/group info from already-persisted job fields; batch
   rendering contract respected.
5. **P3.45.5 — rendering hygiene** (D-R1, D-R3/4/5 reductions,
   D-R8 dead code, D-R6 theme tokens): separate commits, no
   behaviour mixing.
6. Escalation-gated (see §12): manual-concatenate semantics,
  single-Generate structure messaging, history `slot_id`,
  `_check_states` re-keying, transparency stack, estimator
  recalibration (needs measured corpus).

---

## 8. FIRST IMPLEMENTATION SLICE

**P3.45.1 — "Block generation state in the editor gutter"**
(proposed; NOT implemented in this run).

- **Proven problem**: the editing surface where blocks live shows
  zero generated-audio state while the entire pipeline for it
  exists: `block_slot_states` derives per-block
  `covered/total/version_count` (tested), `update_block_status`
  feeds a painted badge + progress bar in the gutter (dead — zero
  callers), and `reset_all_block_statuses` exists for structure
  resets.
- **Change shape**: wire the derived per-block state into the
  existing badge API at the existing push-refresh moments — batch
  job finished / batch completed / scene load-restore / project
  load / re-detect (ids replaced → reset) / Generate Long
  materialisation / version-selection change. Presentation-only:
  no model writes, no new identity, no timers/polling, no
  geometry changes.
- **Out of scope**: batch-table changes, playback linkage,
  estimator changes, any P3.44.9/.9.1 geometry or scroll-path
  change.
- **Invariants to hold**: block dicts untouched (offsets/ids —
  P3.44.9 controls), gutter geometry untouched (P3.44.9.1
  controls), append-only asset doctrine untouched, derived-state
  purity (badges recomputed from the model, never persisted).

---

## 9. FILES / COMPONENTS

| File | Why involved |
|---|---|
| `engine/audio_provenance.py` | `block_slot_states` data source (read-only); `materialize_expected_slots`; ceiling-adjacent helpers for preflight context |
| `ui/panels/narration_editor.py` | `update_block_status` consumer + gutter painter (Slice 1); Preview stats estimate base (Slice 3); CARE: P3.44.9.1 geometry lives here |
| `ui/main_window.py` | refresh trigger points; `_start_long_narration`; `_combine_scene`/concatenate paths (later slices); `_on_generation_finished_ui` |
| `engine/narration_splitter.py` | limits, estimates, `part_of_block` stamping (Slices 2/3) |
| `ui/panels/long_narration_dialog.py` | preview surfacing, recompute-on-edit (Slice 2) |
| `engine/models.py` | `max_new_tokens` stale comment (Slice 2) |
| `ui/panels/batch_generation.py` | block-identity column (Slice 4); combined-rows churn + `_check_states` (hygiene/escalation) |
| `engine/engine.py` | duplicated `app_root` property (Slice 5 hygiene) |
| `engine/output_guard.py` | read-only reference for the ceiling math (Slice 2) |
| Later: `engine/history_manager.py`, `ui/panels/assemble_dialog.py`, `ui/panels/history_view.py` | history `slot_id`, assemble label, history columns |

---

## 10. RISKS (evidence-based only)

- **R-1**: Slice 1 edits `narration_editor.py`, the file carrying
  the frozen P3.44.9.1 geometry — a careless change could regress
  scroll sync. Mitigation: only call the existing public
  `update_block_status`/`refresh_blocks` path; never touch
  `_block_rows`/paint geometry; P3.44.9.1 (19) + P3.44.9 (76+35)
  suites re-run mandatory.
- **R-2**: missed refresh points ⇒ stale badges (the pull-based
  gutter invalidation constraint). Mitigation: enumerate and test
  each trigger; badges are derived, so the worst case is a stale
  display until the next event — no data corruption is possible.
- **R-3**: preflight false alarms from mapping the 15 chars/s
  estimate onto the token ceiling. Mitigation: explicit margin,
  "likely" wording, no blocking behaviour.
- **R-4**: `main_window.py` is 6,859 lines — every slice there has
  a wide blast radius; focused per-slice regression only.
- **R-5**: known pre-existing flakes (SS-3 README test, SS-4 regen
  timing test) and the SS-6 DPI-shim environment note must not be
  attributed to P3.45 changes — baseline-comparison discipline as
  in P3.44.9/.9.1.

---

## 11. TEST PLAN (for the first slice, P3.45.1)

1. New focused suite `tests/test_p3_45_1_block_generation_state.py`
   (project style: real `QApplication`, real widgets, real
   registration path — no mock writes):
   - badge state transitions driven through the REAL
     `register_generation_result` for: not-generated → covered
     (multi-part block partial/complete) → new version
     (`version_count`) → failed regen of a covered block (stays
     done);
   - scene save/load restores badges from persisted model state;
   - re-detect resets badges (block ids replaced);
   - Generate Long materialisation resets then re-feeds;
   - version-selection change updates the affected block only.
2. Controls (unchanged-behaviour proofs):
   - P3.44.9 suite: block ids/offsets byte-identical after badge
     updates;
   - P3.44.9.1 suite: gutter geometry/scroll untouched;
   - P3.44.5/6 provenance + slot suites: no writes to
     assets/slots/selections;
   - P3.28 audio-provenance suite (slot semantics).
3. Full battery + `verify_compile` + `verify_architecture` +
   `verify_functional_integrity` + launch smoke.
4. Real-widget offscreen GUI validation of badge visibility through
   the real event-bus finish path; windowed human visual
   verification is not possible in this sandbox — stated
   limitation, geometry assertions computed from real Qt widget
   geometry instead (same approach as P3.44.9.1).

---

## 12. STOP / ESCALATION

**Explicitly NOT to be changed in P3.45 without a broader
architectural decision:**

1. The P3.44.9 offset engine and the P3.44.9.1 gutter geometry /
   scroll-sync paths (frozen; reopen only on direct regression
   evidence).
2. The identity model: `slot_id` / `generation_run` /
   `part_version` / append-only `Scene.audio_assets` / single-writer
   registration (P3.44.6 contracts).
3. Batch as an editable generation workspace (P3.44.5).
4. No session locking, hidden sub-sessions, automatic session
   merging, fingerprint systems, `GenerationPlan`, or `STALE`
   states — P3.44.6 evaluated and rejected them; nothing in this
   triage resurrects a need for them.
5. The 15 chars/s constant: not recalibrated without a measured
   corpus (§4.3).
6. Manual Concatenate overwrite semantics: product decision
   (COMBINE_VS_CONCATENATE_AUDIT) — a WARNING may be a small slice;
   changing semantics is not.
7. The transparency-stack / GL text composition question
   (§5.2): separate rendering-architecture phase.
8. `_check_states` re-keying by slot: touches the P3.44.4
   checked-regen execution flow — needs its own analysis before any
   change.

**This run is the P3.45 triage/design gate. No implementation was
performed; the four areas were mapped, dependencies established,
and the first slice defined. Progression to P3.45.1 implementation
is the next run's decision.**
