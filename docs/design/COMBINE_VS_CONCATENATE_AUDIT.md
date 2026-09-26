# SpeechStudio — UI Audit: "Combine Scene" vs "Concatenate"
## ADDITIONAL UI AUDIT — DESIGN DISCUSSION ONLY (no code / tests / ZIP touched)

Status:   AUDIT REPORT — awaiting agreement before any change.
Build:    P3.30 current. Every fact below was verified against the real
          source; line numbers cited.
Files:    ui/panels/batch_generation.py (buttons + input collection),
          ui/main_window.py (both handlers), engine/history_manager.py
          (entry typing), engine/audio_provenance.py (Combine machinery).

---

## 1. WHAT EACH BUTTON ACTUALLY CALLS (verified)

**[ Combine Scene ]** — batch_generation.py:883–900, SCENE MODE ONLY
(`if self._scene is not None:`).
→ signal `combine_scene_requested` → MainWindow.
`_on_combine_scene_requested` (main_window.py:3516–3660).

**[ Concatenate ]** — batch_generation.py:902–915, created
**UNCONDITIONALLY — visible in BOTH scene mode and manual batch mode**.
→ `_on_concatenate` (batch_generation.py:2061–2098, collects inputs) →
signal `concatenate_requested(paths)` → MainWindow.
`_on_concatenate_requested` (main_window.py:3779–4004).

Both end in the same two low-level AudioManager primitives
(`concatenate_with_silence` / `concatenate`). **The primitives are shared;
the semantics are not.** "Same underlying operation" is NOT true — see §2.

---

## 2. EXACT SEMANTIC DIFFERENCE

### Combine Scene:
Creates a **versioned, lineage-tracked Scene-level Combined Output** from
the Scene's **provenance-resolved composition** (per-slot: explicit
selection, else latest version; ALL expected slots, in slot order). The
result is registered in `Scene.combined_outputs`, is the only default
input to Project Assembly, and participates in stale detection.

### Concatenate:
A **legacy, queue-scoped merge utility**. It merges **the current batch
queue's COMPLETED job files in queue order** — whatever the queue happens
to contain — into a **fixed-name, unversioned file** that is registered
NOWHERE except History. It has **three different behaviours**, selected
implicitly:

| Trigger | Path | Output |
|---|---|---|
| Any completed job has a `speaker` | "dialogue" path (3845–3892) | `outputs/{project-slug}/{project-slug}_full.wav` (+ per-line copies into `lines/`, per-speaker `stems/{speaker}.wav` for Unreal) — **overwritten in place every time** |
| Single-speaker, silence > 0 (default 300 ms) | 3898–3903 | `outputs/long_narration_full.wav` — **overwritten in place** |
| Single-speaker, silence == 0 | 3904–3907 | `outputs/long_narration_full.wav` via 100 ms crossfade — **overwritten in place** |

**Verdict: NOT "duplicate functionality" as a whole — but the
single-speaker path IS a strictly-worse duplicate of Combine Scene, and in
scene mode the button as a whole is a provenance bypass.**

---

## 3. INPUT DIFFERENCE (verified)

| | Combine Scene | Concatenate |
|---|---|---|
| Source of inputs | `Scene.expected_audio_slots` → `resolved_slot_sources()` (audio_provenance.py:573–616) | `self._manager.jobs` filtered to `COMPLETED`, in queue order (batch_generation.py:2079–2090) |
| Respects per-slot version selection (`selected_block_audio`) | **YES** (via `resolved_asset_for_slot`) | **NO — never consulted** |
| Scope | The **whole Scene** (every expected slot) | **The current queue only** — a review-mode subset merges just that subset; slots not in the queue are silently absent |
| Uncovered/missing handling | **BLOCKS** with named blockers (no silent skipping) | Missing file → abort with a warning; but a *slot absent from the queue* is silently omitted (≤1 file → info dialog) |
| Minimum to operate | ≥1 expected slot | ≥2 COMPLETED jobs in the queue (1745–1749) |

Same data? **Only coincidentally** — when the queue happens to be a full,
latest-run, no-curated-versions mirror of the Scene. In every other case
(the exact cases the provenance model exists for) they consume different
data.

## 4. OUTPUT DIFFERENCE (verified)

| | Combine Scene | Concatenate |
|---|---|---|
| Filename | `{Proj}_{Scene}_Combined_vNN_{scene8}.wav` (Rec 2) | Fixed: `long_narration_full.wav` / `{project}_full.wav` + stems |
| Versioned | **YES** — monotonic max+1, disk-guarded (`next_scene_combined_version`) | **NO — silent in-place overwrite every press** (defect D-6, never fixed on this path) |
| Output dir | `outputs/` | `outputs/` or `outputs/{project-slug}/` (+ `lines/`, `stems/`) |
| Becomes AudioAsset | No (by design — snapshots are not slot assets) | No |
| Becomes Scene Combined Output | **YES** — appended to `Scene.combined_outputs` with full per-slot lineage | **NO** |
| History | YES — typed `scene_combined` with lineage ids + run (3628–3636) | YES — but **UNTYPED** (handler calls `add(result)` without `entry_type` at 3989; `type` lands as None because the fabricated result has no `part_index` — Rec 18 mandated `"concat"`, this is a defect) |
| Provenance preserved | Scene id, block ids, part indexes, **asset ids**, part versions, speakers, character ids, run, paths, durations | Prompt-prefix text only ("[Concatenated Long Narration – N parts]"; job names + first 80 chars of prompts). No ids, no versions, no run |
| Stale detection | **YES** (lineage-based) | **NO — no model record exists to compare** |
| Scene-level output | **YES** | **NO** |
| Suitable for Project Assembly | **YES** (default resolution chain) | **NO — invisible**: `resolve_scene_output` reads only `selected_output`, `combined_outputs`, `audio_assets`; a concatenated file is none of these |
| Modifies Scene state | Appends combined entry; recomputes nothing; saves project | **Nothing** (waveform preview + History only) |
| Project saved after | YES (3654–3656) | NO |

## 5–10. BEHAVIOURAL TESTS (traced through code, §9–§10 of the brief)

**Stale test** — Combined v01 exists, regenerate B3 (→ v02):
- **Combine Scene** → allocates `Combined_v02` with exact lineage
  (B3 v02); v01 becomes STALE ("B3 has a newer version: v02") and is kept
  on disk. Fully stale-aware.
- **Concatenate** → overwrites `long_narration_full.wav` with the queue's
  completed files. No stale concept, no version, **the previous merge is
  destroyed** (the D-1/D-6 data-loss class). Project Assembly cannot
  validate or even see the result.

**Versioning test** — Combine, Combine again, Concatenate:
- Combine → `Combined_v01`; Combine again → `Combined_v02` (old kept).
- Concatenate → same fixed filename each time; **overwrites**; nothing
  preserved. Only Combine uses the provenance version model.

**Which fits the approved architecture** (block-version selection →
composition → Scene-level output → Assembly): **Combine Scene, alone.**
Concatenate sits outside the pipeline at every stage: input is not the
composition, output is not a Scene-level output, result is not consumable
by Assembly.

---

## REQUIRED FINAL TABLE

| Action | Input | Output | Provenance | Versioned | History | Project Assembly |
|---|---|---|---|---|---|---|
| **Combine Scene** | ALL expected Scene slots, provenance-resolved (selection → latest), slot order; blocks on gaps/missing | `{Proj}_{Scene}_Combined_vNN_{scene8}.wav` in `outputs/`, registered in `Scene.combined_outputs` | Full per-slot lineage: scene, block, part index, asset id, part version, speaker, character, run, paths, durations | YES (monotonic, disk-guarded, never overwrites, old kept) | YES, typed `scene_combined` + lineage ids | YES — default source (resolve_scene_output) |
| **Concatenate** | COMPLETED jobs of the CURRENT batch queue, in queue order (≥2); ignores `selected_block_audio`; queue-subset merges silently partial | Fixed name, overwritten in place: `outputs/long_narration_full.wav` OR dialogue: `outputs/{proj-slug}/{proj-slug}_full.wav` + `lines/` + per-speaker `stems/` | NONE (prompt-text summary only; untyped history entry) | NO (silent overwrite; previous merge destroyed) | YES, untyped (defect; Rec 18 said `"concat"`) | NO — invisible to resolution |

---

## CONCLUSIONS

### 1. Actual functional difference
Combine Scene = the P3.28 provenance snapshot (composition in, versioned
Scene-level output out, Assembly-compatible). Concatenate = a legacy,
queue-scoped file-merge utility with an implicitly-selected dialogue/stems
sub-mode, registered nowhere but History, invisible to Assembly.

### 2. Whether both are required
**Partially.** Three sub-cases:
- **Scene mode, single-speaker: Concatenate is NOT required.** It is a
  strictly-worse duplicate of Combine Scene (no version, no lineage, no
  registration, silent overwrite, invisible to Assembly). This is exactly
  the case the P3.27 design record Rec 13 ordered REPLACED by Scene
  Combine (defect D-6) — P3.28 added the replacement but did not retire
  the legacy button.
- **Manual batch mode (no Scene): Concatenate IS required** — Combine
  Scene does not exist there (no scene ⇒ no composition), and merging a
  manual job queue is a legitimate need.
- **Dialogue/stems (Unreal export): the FEATURE is required, the button
  is the wrong home.** It is triggered implicitly by "any completed job
  has a speaker" — in a scene-mode multi-speaker batch, pressing
  "Concatenate" silently takes the dialogue path (different directory,
  different naming, stems) without the user choosing it. The design record
  kept this workflow frozen/out-of-scope, which is why the path survived.

### 3. Whether either is legacy
**Concatenate is legacy** (P3.23 behaviour, self-labelled in its own
comment, batch_generation.py:902–905; preserved for the dialogue workflow
and manual batch). Its tooltip is ALSO stale doc drift: it promises
"100 ms crossfade" and "outputs/long_narration_full.wav" — the handler
actually uses settings-driven silence (default 300 ms, randomized) and, in
dialogue mode, a different path entirely. The `_on_concatenate` docstring
claim "regen preserves the output filename" is likewise obsolete under
P3.28 versioned regeneration (regen now allocates a NEW versioned file;
the queue-order logic still holds, the filename claim does not).

### 4. Are the current labels misleading?
**Yes, doubly:**
- Nothing tells the user that one button is provenance-correct and the
  other bypasses provenance. In scene mode both sit side by side with no
  visual hierarchy — the user cannot know that only one feeds the
  audiobook Assembly.
- "Concatenate" also silently switches to a different operation (dialogue
  stems) based on job metadata, so even its own meaning is unstable.

### 5. Recommended final UX (NOT implemented — for agreement)
1. **Scene mode: remove [Concatenate] entirely** (hide when
   `self._scene is not None`). Single-speaker scene merges are fully
   covered by Combine Scene; multi-speaker scene merges likewise (slots
   carry speakers; silence-based concatenation already handles speaker
   gaps). This completes Rec 13/D-6 as designed and eliminates the
   provenance bypass exactly where the provenance model lives.
2. **Manual batch mode: keep it, relabel "Merge Completed Parts"** with
   an honest tooltip: "Merge the completed jobs of this queue, in queue
   order, into one WAV. Not linked to any Scene — does not affect
   Project Assembly." (fixed-name overwrite acceptable there? — see 6a.)
3. **Dialogue/stems: extract to an explicit action** ("Export Dialogue
   Stems…") instead of implicit speaker-detection — at minimum while it
   remains, its trigger must be visible. (If the UE workflow is in active
   use, this is the one piece worth a small dedicated follow-up; if not,
   it stays manual-mode-only legacy until then.)
4. Tooltip/type fixes ride along (5→6 below).

### 6. Implementation defects found (all confirmed in code)
- **(a) D-6 still live on the Concatenate path**: fixed-name silent
  in-place overwrite destroys the previous merge (single-speaker AND
  dialogue `_full.wav` + stems). Design record marked this "Fixed by Rec
  13/PH5" — PH5 built the replacement but never retired the legacy writer.
- **(b) Provenance bypass in scene mode**: ignores `selected_block_audio`
  and `expected_audio_slots`; a review-mode subset merges partially with
  no warning that slots are missing.
- **(c) History entry untyped**: handler omits
  `entry_type="concat"` (Rec 18 violation); entry also carries no
  scene_id despite usually running inside a scene context.
- **(d) Tooltip + docstring drift**: crossfade/`long_narration_full.wav`
  claims vs settings-driven silence and the dialogue path; "regen
  preserves the output filename" is false under P3.28.
- **(e) Dialogue path names the directory after the PROJECT**
  (`self._current_project or "dialogue"`, main_window.py:3848) — for a
  multi-scene project every scene's dialogue export collides in one
  project-named folder; two scenes overwrite each other's `_full.wav`.
- **(f) In scene mode, [Combine Scene] and [Concatenate] have no visual
  relationship or hierarchy** — the ambiguity this audit was opened for.

### 7. Duplicate functionality?
**DUPLICATE FUNCTIONALITY — scoped verdict:**
- Scene mode, single-speaker: **YES — duplicate; remove Concatenate.**
- Scene mode, multi-speaker: **overlapping-but-divergent** (stems feature
  hidden inside) — still remove from scene mode; relocate stems export if
  needed.
- Manual batch mode: **NO — keep (relabeled).**

---

## PROJECT ASSEMBLY VERDICT (brief §8)

Assembly should accept **only Combine Scene outputs** (plus explicit
single-asset selection and the legacy latest-asset fallback, per the
approved resolution chain). The Concatenate output must REMAIN
non-consumable — wiring it in would legitimize a provenance-free,
overwriting, queue-scoped writer as an Assembly source. It is a
preview/scratch tool, not a Scene output.

---

*Audit ends. No labels, code, or tests were changed. Awaiting agreement on
recommendation 5 (1)–(3) + the defect list before any implementation
phase.*
