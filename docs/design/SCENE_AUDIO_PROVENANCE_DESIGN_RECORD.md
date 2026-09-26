# SpeechStudio — Scene Audio Provenance + Batch Generation State + Export
## Extended Design Decision Record (P3.27 — DESIGN ONLY, NOT IMPLEMENTED)

Status:      DESIGN PROPOSAL — awaiting acceptance. No code has been changed.
Extends:     CHARACTER SYSTEM (P3.22) + SCENE ASSEMBLY (P3.23) + COMBINED AUDIO (P3.23)
Baseline:    SpeechStudio P3.26 (686/686 tests, ZIP SHA-256 c3a3f66d…0cbd0)
Task type:   Design + architecture decision pass. Implementation is a separate,
             phased follow-up (phase plan in §7).
Audit basis: Every fact in this record was verified against the actual P3.26
             source tree (line numbers cited). Read-only audits were logged as
             AUDIT-A (combined audio + assembly), AUDIT-B (history + project
             export + scene persistence), AUDIT-C (batch window + Generate Long
             + scene status) in /home/z/my-project/worklog.md.

---

## 0. THE PRODUCT RULE (GOVERNS EVERYTHING BELOW)

The system NEVER attempts to determine whether the user changed text, prompt,
tokens, or semantics. There is no text diffing, no token tracking, no prompt
hashing, no automatic invalidation. The system only ever records three facts:

  1. Has this generation slot ever been generated successfully? (yes/no)
  2. What audio versions exist for it? (append-only list)
  3. Which output is explicitly selected, where explicit selection is required?

The user decides when to regenerate. Everything below is subordinate to this.

---

## 1. FILENAME / NAMING AUDIT (TASK §2–§3)

### 1.1 Inventory of every output-naming site

| # | Producer | Template | Location | Collision behaviour |
|---|----------|----------|----------|---------------------|
| 1 | Single Generate (auto name) | `YYYYmmdd_HHMMSS.wav` (+`_ms`, +`_ms_N` on collision) | engine/audio_manager.py:89–107 | Safe (P3.25 SS-M05 fix: ms + counter dedup) |
| 2 | Generate Long part (multi-speaker) | `{Proj}_{Scene}_Part_{NN}_{Speaker}_{sceneid8}.wav` | ui/main_window.py:2991–2993 | **NONE — silent overwrite** |
| 3 | Generate Long part (single-speaker) | `{Proj}_{Scene}_Part_{NN}_{sceneid8}.wav` | ui/main_window.py:2994–2995 | **NONE — silent overwrite** |
| 4 | Manual batch job | user-typed name, or auto (timestamp) | ui/panels/batch_generation.py:94–97, 188–189 | none for explicit names |
| 5 | Dialogue line part | `output_filename` from `.scene.json` | engine/scene_persistence.py:149, ui/main_window.py:5140 | none |
| 6 | Dialogue scene full mix | `outputs/{slug}/{slug}_full.wav` | ui/main_window.py:3172–3176 | **fixed name — overwritten every Concatenate** |
| 7 | Dialogue stems | `outputs/{slug}/stems/{speaker}.wav` | ui/main_window.py:3178–3194 | **fixed name — overwritten** |
| 8 | Single-speaker long full mix | `outputs/long_narration_full.wav` | ui/main_window.py:3200–3209 | **fixed name — overwritten** |
| 9 | Project Combined Audio | `outputs/combined/{Proj}_combined_{YYYYmmdd_HHMMSS}.wav` | ui/panels/assemble_dialog.py:320–323 | timestamp-differs ⇒ new file; same second + user confirms ⇒ overwrite, and a second `combined_outputs` entry is appended pointing at the same path (assemble_dialog.py:625–646, 711) |
| 10 | History entry file | `settings/history/{entry_id}.json`, id = timestamp + ms + counter | engine/history_manager.py:226–240 | safe (counter dedup) |
| 11 | Project Export audio copy | `scenes/<safe>/audio/{NNN}_{orig}.wav`, `combined_outputs/{NNN}_{name}.wav` | engine/project_exporter.py:343, 477 | positional prefix (order-based, not identity-based) |

`sceneid8` = first 8 chars of the Scene UUID (main_window.py:2987). Names are
sanitised by `_normalize_filename` (main_window.py:2970–2979) and, for combined
outputs, `sanitize_output_filename` (combined_audio.py:325–347).

### 1.2 Answers to the §3 audit questions

**What already exists?**
Project name, Scene name, part index (`Part_01`), speaker (multi-speaker only),
scene-id disambiguator, and — only on the Project-Combined and auto-timestamp
paths — a timestamp. Combined outputs are identifiable by the `_combined_`
infix; the legacy long-mix uses `_full`.

**What is missing?**
- Any generation-run or version marker on Generate Long parts and on Scene-
  level mixes (`_full` files are unversioned and un-tracked).
- Any version marker on combined outputs (`_combined_` has a timestamp but no
  v01/v02; the list of entries has no version ordering).
- Block identity anywhere in a filename.
- Any linkage from a filename back to a block, part, or run.

**Are versions deterministic?**
No. No naming path today encodes a version at all. The only determinism is the
auto-timestamp path's collision counter, which is wall-clock dependent.

**Can two generations overwrite each other?**
Yes, in four ways:
  1. Re-running Generate Long on the same Scene reproduces byte-identical part
     filenames (no timestamp, no version) and `save_wav` writes explicit
     filenames with NO existence check (audio_manager.py:109–111 vs. the auto
     path's guarded branch at 89–107). The previous run's audio is silently
     destroyed. **This is the root defect behind task §2.**
  2. Single-job Regen deliberately overwrites in place (batch_generation.py
     1307–1318, "The output file will be overwritten in place.").
  3. Concatenate overwrites the fixed-name `_full.wav` / stems every time.
  4. Project Combined with the same-second timestamp + user "Overwrite".

**Can the user tell which file belongs to which generation run?**
Not for Generate Long parts (no run id, no timestamp, no version). Partially
for Project Combined (timestamp only, no version). Outside SpeechStudio, a
folder of `Eden_S03_Part_01_ab12cd34.wav` files gives no ordering information
at all.

**Can the user tell which file is a part and which is combined?**
Mostly, by suffix convention — but there are three conventions (`_Part_NN`,
`_combined_`, `_full`) and none of them says what a `_full` file contains, or
which parts/versions it was built from (no lineage outside the project JSON).

### 1.3 Filename audit verdict

The current convention has the right instincts (Project, Scene, part number,
speaker, scene-id disambiguator) but is missing exactly one concept:
**version**. Therefore: **do NOT invent a second naming convention. EXTEND the
existing one with a version slot and a combined marker.** See Recommendation 2.

---

## 2. DEFECTS DISCOVERED DURING THE AUDIT (TO BE FIXED BY THIS DESIGN)

These were found while auditing; each is folded into a phase in §7. None were
known to the user when the task was written (except D-1's symptom).

| ID | Defect | Evidence | Fixed by |
|----|--------|----------|----------|
| D-1 | Generate Long re-run silently destroys the previous run's audio (no version, no collision check on explicit filenames) | audio_manager.py:109–111 + main_window.py:2991–2995 | Rec 2, 6; PH1 |
| D-2 | Scene status flips to "complete" when the FIRST part of a long batch succeeds (rest still queued); flips to "error" on any single part failure; never set to "generating" by Generate Long | main_window.py:2384, 2424, 2150; no "generating" write on the batch path | Rec 7–10; PH1–2 |
| D-3 | Generation results are appended to `self._active_scene` unconditionally — `result.scene_id` is never consulted. Switching Scenes mid-batch attaches parts to the WRONG Scene (provenance corruption) | main_window.py:2363–2383 | Rec 4, 20; PH1 |
| D-4 | `is_combined_output_stale` compares an ISO `created_at` against a `%Y%m%d_%H%M%S` `generated_at` lexicographically → always True for any non-empty `generated_at` (masked by ISO-format test fixtures) | combined_audio.py:581, 614–617 vs. generation_manager.py:98 | Rec 15; PH5 |
| D-5 | `SplitPart.source_block_id` is populated by the splitter (narration_splitter.py:44, 512) then DROPPED when BatchJobs are built; the splitter comment claims the Batch window maps Block→Part — it does not | main_window.py:2996–3014; batch_manager.py:92–115 | Rec 5; PH1 |
| D-6 | The long-mix "Concatenate" writes fixed, unversioned filenames with no lineage, records only a History entry, and adds nothing to `scene.audio_assets` / `combined_outputs` | main_window.py:3172–3209, 3236–3291 | Rec 13–14; PH5 |
| D-7 | Doc/code drift: claimed History "type: combined" is never written (main_window.py:4942–4943); Regen tooltip promises auto-re-concat that is not wired (batch_generation.py:1293); dialog says 500 ms default silence, code default is 300 ms (long_narration_dialog.py:105 vs. main_window.py:3136) | — | Rec 18; PH9 |
| D-8 | `save_wav` joins `filename` into the outputs dir without basename/normalisation — an absolute or `../`-bearing explicit filename escapes the outputs directory (os.path.join semantics) | audio_manager.py:109 | Rec 21; PH1 |

---

## 3. CORE MODEL DECISIONS

### 3.1 The generation slot (the unit the system remembers)

Define the **slot** — the smallest unit Generate Long actually produces:

  slot_id = `{block_id}:{part_of_block}`   for block-derived parts
  slot_id = `plain:{part_index}`           for plain-text / speaker-split parts

A slot is *expected* when the current generation structure (the splitter run at
batch-preview time) contains it. Slots materialise only when the user builds a
batch preview (Generate Long) or re-detects blocks — a user-initiated structure
event, never a keystroke (§0 product rule: no change tracking).

The expected slot list is persisted on the Scene as
`expected_audio_slots: [{slot_id, block_id, block_label, part_index, speaker,
character_id}]`. Before the first materialisation, a Scene has no slot
structure and falls back to legacy coverage semantics (§3.4).

### 3.2 Block generated state = derived, not stored

"Has this block ever been generated?" is **not a stored field**. It is derived:

    block X is generated  ⟺  every slot currently expected for X
                              has ≥ 1 successful AudioAsset

AudioAsset presence in `Scene.audio_assets` already means success (assets are
only appended on success — main_window.py:2341). Deriving from the single
authoritative append-stream means there is no second truth to drift. This is
the smallest mechanism compatible with the current architecture (task §5).

### 3.3 Versions = AudioAsset fields + monotonic allocation

Every successful generation of a slot appends an AudioAsset carrying:

    block_id, part_index, part_version (1-based, per slot), generation_run

Next version for a slot = `1 + max(part_version of that slot's assets)`; if the
candidate output filename already exists on disk, keep bumping until free.
Monotonic, deterministic, no stored counters, never overwrites, survives asset
deletion without ever reusing a live file's name.

Generation **run identity**: each batch start allocates a scene-scoped run
sequence (`1 + max(run seq among scene assets)`, same disk-guard idea), id =
`{scene_id[:8]}-r{NNN}`. The run groups parts in History/Batch window. It is
metadata — the filename carries the per-part version (Rec 2), which is what the
user's own examples encode (`Long_v01_Part_001` → later `Long_v02_Part_001`;
and after selective regen: mixed versions, exactly as task §22 describes).

### 3.4 Scene completeness = pure function of (expected slots × assets)

    GENERATING   a run is in flight for this Scene
    COMPLETE     every expected slot has ≥ 1 successful asset
    PARTIAL      some slots covered, some not (failed slots show per-slot ✗)
    ERROR        zero slots covered AND the last run had ≥ 1 failure
    NOT_GENERATED otherwise

Derived — never eagerly mutated (contrast with today's flap, D-2). Legacy
scenes (no `expected_audio_slots` ever materialised): covered ⟺ ≥ 1 asset
exists (preserves today's practical meaning; per-slot tracking starts with the
first new-model batch preview).

### 3.5 Two selection maps — the only "current/selected" state

    Scene.selected_block_audio : {slot_id → asset_id}   (empty ⇒ latest per slot)
    Scene.selected_output      : {kind: "scene_combined"|"asset", id} | None

Selections are user actions, never automatic. `None` ⇒ the deterministic
default resolution (Rec 16). An explicit selection that differs from the
default is what "CUSTOM" means (Rec 7). Nothing is ever deleted by selection.

---

## 4. THE 25 REQUIRED RECOMMENDATIONS

### Rec 1 — Batch export UX (task §1, §25)
One "Export Audio…" toolbar button in the Batch Generation window (enabled when
≥ 1 completed part or combined output exists). Opens a small dialog:
destination folder (existing-dir picker, validated), scope radio:
**(a) selected rows, (b) all generated parts, (c) scene combined outputs,
(d) everything in this batch**, then Copy. Files keep their provenance
filenames (Rec 2) — export is a plain file copy, no renaming, no second naming
scheme, no metadata editing, no reordering. Collision at destination:
auto-suffix ` (2)`, `(3)`… — never silent overwrite; a summary lists what was
exported. No new export architecture: this is ~150 lines reusing
`_safe_dirname`-style validation (Rec 21).

### Rec 2 — Filename convention (task §2)
Extend the existing convention with one version slot; do not replace it:

    Parts (block or plain, Long generation):
      {Proj}_{Scene}_Long_v{part_version:02d}_Part_{part_index:03d}{_Speaker}_{sceneid8}.wav
      e.g. Eden_S03_Long_v01_Part_001.wav
           Eden_S03_Long_v01_Part_002_Engineer.wav
           Eden_S03_Long_v02_Part_002_Engineer.wav   (after regenerating Part 02)

    Scene combined output:
      {Proj}_{Scene}_Combined_v{version:02d}_{sceneid8}.wav
      e.g. Eden_S03_Combined_v01_ab12cd34.wav

    Project combined output (unchanged shape, add version):
      {Proj}_combined_v{version:02d}_{YYYYmmdd_HHMMSS}.wav

    Single Generate (auto): unchanged `YYYYmmdd_HHMMSS.wav` (already
    collision-safe) — no change; it is a full-scene asset, not a batch part.

Rationale: matches the user's own example almost verbatim while preserving the
existing Project/Scene/speaker/scene-id components; version travels with the
PART (not the run) so selective regeneration produces exactly the mixed-version
lineage task §22 expects. Filenames remain understandable outside SpeechStudio.
Lineage/identity live in the model (Rec 20) — filenames never ARE the identity.

### Rec 3 — Existing filename audit
Delivered as §1 above. Verdict: extend, don't replace; and fix overwrite
behaviour (D-1, D-8) as part of the same phase.

### Rec 4 — Generation run identity
A run = one activation of the batch pipeline for a Scene (Generate Long, review
Generate, or multi-part Regen). Identity: scene-scoped monotonic sequence,
`{scene_id[:8]}-r{NNN}`, allocated at batch start with the same disk-guarded
max+1 rule; carried on every AudioAsset, HistoryEntry and (for combined) in
lineage. No new entity, no run registry — runs are reconstructible by grouping
assets on `generation_run`. Displayed in the Batch window header
("Run r003") and in History group tooltips.

### Rec 5 — Block generated state
Derived, per §3.2: presence of ≥ 1 successful AudioAsset whose `block_id`
matches, covering all currently-expected slots of the block. Persisted with the
Scene implicitly (assets live in `Scene.audio_assets`, which is already saved
in project.json). No `Scene.generated_blocks` duplicate — a second list would
be a second truth. UI asks the derivation; the model never stores it.

### Rec 6 — Block versioning
Per-slot version field on AudioAsset, allocated at job submit time
(monotonic max+1 + disk guard, §3.3). Old versions are never deleted, never
overwritten (this changes today's regen-overwrites-in-place behaviour —
intentional: it is the data-loss path D-1/D-6). The Batch window shows
"B2 ✓ Generated · 3 versions" and versions on demand (Rec 12, 22).

### Rec 7 — Scene completeness
Per §3.4: five derived states; the write path sets only GENERATING (batch
start, routed by scene_id) and recomputes on every job completion / batch end
/ scene load / project save. `Scene.status` remains the persisted field (same
key, new value set) so the sidebar badge keeps working; legacy values
recompute on load (§8 migration).

**COMPLETE vs PARTIAL vs CUSTOM** (task §11): COMPLETE/PARTIAL are coverage
facts (does every expected slot have audio?). CUSTOM is not a coverage state at
all — it marks that the Scene's *resolved output* (Rec 16) is a user-curated
selection rather than the deterministic default. STALE and MISSING are
per-output markers (combined output older than its sources; asset file absent
on disk), shown as badges, never as Scene states. Keeping these three
dimensions orthogonal (coverage / curation / freshness) is what makes the model
small.

### Rec 8 — Partial generation
User checks a subset of parts in the Batch window → Generate. Untouched parts
keep their assets and versions (nothing is invalidated — §0). Selected parts
allocate the next version each. Scene coverage recomputes afterwards; a Scene
that was COMPLETE stays COMPLETE (regenerating B2 adds v02; B1's v01 still
counts — exactly task §15/§16).

### Rec 9 — Failed generation
Failure is per-slot: the job is FAILED (BatchJob.error), no asset is appended
(existing behaviour — assets only exist on success), the slot shows ✗ with the
error in tooltip, the Scene recomputes to PARTIAL (some covered) or ERROR (none
covered + last run had failures). A failed *re*generation of an already-covered
slot does not degrade coverage — the previous version remains valid, so a
COMPLETE scene stays COMPLETE with a failure notice. One modal error per batch
end (not per part — today's per-part modal spam, main_window.py:2431–2437,
collapses into the window's failed rows + a single end-of-batch toast).

### Rec 10 — Cancelled generation
`BatchManager.stop()` semantics are already correct (completed jobs stay
COMPLETED, pending become SKIPPED — batch_manager.py:513–534). Add: recompute
scene coverage at batch end → PARTIAL unless all slots were already covered
(then COMPLETE). No state is destroyed by cancelling; nothing else to do.

### Rec 11 — Long Generation review checkbox (task §13)
A `QCheckBox` in the NarrationEditor mode bar, immediately left of
"▶ Generate Long" (narration_editor.py:773–804): label **"Review before
generating"**, tooltip "Open the Batch Generation window to review parts and
choose what to generate — generation starts only when you press Generate
there." Persisted in settings.json (`long_generation.review_before_generate`,
default OFF = current behaviour, zero surprise for existing users).
- Unchecked: Long dialog → Generate ⇒ batch window opens and auto-starts
  (today's path, main_window.py:3029–3040).
- Checked: Long dialog → Generate ⇒ batch window opens in REVIEW MODE:
  jobs stay PENDING, per-part checkboxes visible, big [Generate] button.
Both paths call the same `_start_long_narration` → BatchManager pipeline — the
ONLY difference is whether `bm.start()` is called automatically. No second
pipeline (task §13's hard rule).

### Rec 12 — Batch window selection behaviour (task §14)
Default checkbox state: **only not-generated slots are checked**;
already-generated slots are unchecked (one click each, or "Select all", to
include them). Rationale: regenerating costs minutes of GPU per part; existing
audio is never invalid (§0), so pre-checking it invites accidental mass
re-spend — the opposite failure (missing a part) is visible and trivially
fixed with one more click. This default also makes task §34's flow zero-click:
B3 is the only unchecked-generated… i.e. the only not-generated slot, so it is
pre-checked and [Generate] does exactly what the user wants. The alternative
(check all) is rejected: it makes the destructive-by-cost option the default.
Window layout (review mode):

    SCENE 03 — Run r004 — coverage: PARTIAL (3/4)
    [✓] B1  Engineer   ✓ Generated · 1 version     12.4s
    [ ] B2  Lady       ✓ Generated · 3 versions ▾   9.1s
    [✓] B3  Engineer   — Not generated               —
    [ ] B4  Lady       ✓ Generated · 1 version      10.8s
    ── Scene combined outputs ─────────────────────
    v01 · 32.3s · STALE (B2 has newer v03)          [Play][Export]
                                   [Cancel]  [Generate 1 part]

### Rec 13 — Scene internal combination (task §22)
Replace the fixed-name Concatenate output (D-6) with a **Scene Combined
output**: one click of "Combine" in the Batch window concatenates the resolved
audio of every expected slot (selected version per slot, else latest — §3.5)
in slot order, with the existing per-part trailing silence, into
`Scene.combined_outputs` — a new per-Scene append-only list using the SAME
entry shape as `Project.combined_outputs` (combined_audio.py:559–583), extended
with per-slot lineage:

    {id, version, output_path, duration, slot_count, silence_ms,
     sources: [{slot_id, block_id, part_index, asset_id, part_version,
                speaker, character_id, output_path, duration}],
     created_at, generation_run}

Older combined outputs remain on disk and in the list; nothing is rebuilt
automatically. The dialogue/stems workflow (DialogueScene) is out of scope and
untouched.

### Rec 14 — Combined output versioning
Scene combined: `v{NN}` = `1 + max(version in scene.combined_outputs)`,
disk-guarded — deterministic, never overwrites (Rec 2 filename). Project
combined: same rule over `Project.combined_outputs` (replaces the
timestamp-only name; timestamp kept as suffix for human ordering).

### Rec 15 — Combined stale detection
For Scene combined outputs: **version-lineage staleness** — stale ⟺ any source
slot's current max `part_version` > that source's recorded `part_version`, OR
the recorded asset file is missing. No timestamps involved → immune to D-4.
For Project combined outputs: keep the existing
`is_combined_output_stale` logic but FIX the timestamp comparison (parse both
`%Y-%m-%dT%H:%M:%S` and `%Y%m%d_%H%M%S` to datetime before comparing — D-4;
today it is effectively always-True) and extend it to flag when a source
Scene's resolved output has changed identity (asset/combined id mismatch vs.
the lineage record). Display: "STALE — B2 has a newer version (v03)". Never
auto-rebuild: the badge + tooltip tell the user; rebuilding is one click.

### Rec 16 — Scene-level source resolution (task §21, §10)
One pure function, `resolve_scene_output(scene)`:

    1. explicit Scene.selected_output (kind+id) if valid → label "Selected",
       CUSTOM marker
    2. else latest Scene combined output (max version) → label "Combined vNN",
       STALE/MISSING badge as applicable
    3. else latest full-scene asset (legacy fallback: audio_assets[-1] —
       exactly today's rule, assemble_dialog.py:466) → label "Latest audio"

Rule 3 is the backward-compatible default, so every existing project resolves
exactly as it does today. Rule 2 is the natural default once scene-combine
exists (a combined file is by definition the scene's own latest assembled
output). Per-slot version selection (`selected_block_audio`) feeds Combine
(Rec 13) and the version list; it does not change scene resolution by itself.
Selection UI: per-version "Use" in the Batch window; "Choose…" per scene row in
the Assembly dialog offering: latest combined / any combined version / latest
single asset. The user never faces 50 raw files.

### Rec 17 — Project-level assembly (task §21)
`AssembleDialog._get_scene_audio` switches from `audio_assets[-1]` to
`resolve_scene_output(scene)`; the row label shows which resolution was used
("Combined v02" / "Latest audio" / "Selected · CUSTOM") + STALE/MISSING badges
+ the same "Choose…" picker. `AssemblySource`/lineage records the resolved
entity (combined entry id or asset id) so `Project.combined_outputs` lineage
stays exact. Everything else in the assembly path (ordering, silence, ffmpeg,
entry building) is unchanged — this is a source-resolution swap only.

### Rec 18 — History (task §27)
Additive `type` field on every new entry: `"part" | "scene_combined" |
"project_combined" | "concat"` (+ the four provenance fields for parts:
block_id, part_index, part_version, generation_run; lineage summary ids for
combined types). Legacy entries: type backfilled on read by the existing
payload-shape heuristics (prompt prefixes "[Combined Audio", "[Concatenated"),
never rewritten on disk. This closes D-7's never-written "type: combined"
docstring promise and makes the three relationships traceable: part → run →
scene; scene_combined → source assets; project_combined → resolved scene
outputs.

### Rec 19 — Project Export (task §26)
No structural change to the export format: `Scene.to_dict()` already carries
`audio_assets`; new asset fields flow automatically; additively include
`expected_audio_slots`, `combined_outputs` (scene-level, copied like project
combined — WAV/MP3 into `combined_outputs/` with the same 001_ positional
prefix), `selected_block_audio`, `selected_output`. Audio stays FILES (copy2),
never base64. Bump `EXPORT_VERSION` to 2 (reader already tolerant; v1 imports
keep working, they just lack provenance). Import restores everything through
the existing `Project.from_dict` + `_sanitize_scene_audio_paths` (SS-H08)
path — one addition: scene combined WAVs are copied back to outputs/ (today's
import leaves export-relative paths; documented behaviour, now fixed for both
levels). History remains app-level and NOT project-exported (it is a user
workspace journal, not project data — unchanged decision, now documented).

### Rec 20 — Persistence (task §8, §30)
Everything persists inside the two existing JSON files — project.json
(Scene fields + asset dicts + combined entries) and settings/history/*.json
(entry fields). Nothing new: no DB, no tracking tables, no per-run files.
Identity is model-based (asset ids, slot ids, versions) — filenames are
never parsed for identity (task §20). The single registration point
(`register_generation_result`, see §6) is the ONLY writer of new assets —
which also fixes D-3 by routing on `result.scene_id`.

### Rec 21 — Security (task §29)
Reuse + three additions: (1) `save_wav` applies `os.path.basename` +
traversal-strip to explicit filenames (closes D-8; the outputs dir becomes a
hard boundary, matching the auto-name path); (2) the Batch Export validates the
destination (must exist, be a dir, be writable; per-file dest =
basename-sanitised provenance name; resolve+containment check so a crafted
name cannot escape the chosen folder; never delete anything at the
destination); (3) version/part integers are format-cast (`int()`), never
interpolated raw. Combined/ffmpeg path already safe (argv list, shell=False,
`sanitize_output_filename`, `-y` only after an explicit user confirm at a
timestamp-differentiated path — now version-differentiated too). Names from
project.json (importable ⇒ untrusted) flow only through the existing
sanitisers, which stay mandatory on every filename build.

### Rec 22 — Performance (task §28)
Batch window: ONE row per slot/block (grouped), versions behind a lazy
"versions ▾" popover — never a widget per audio file; version lists are
in-memory dicts already loaded with the Scene. Coverage/state derivations are
O(slots × assets) on scene events only (not per-keystroke, not per-timer).
History view unchanged. A 50-asset scene renders as 4 rows + 4 popovers.
Assembly dialog unchanged (already one row per scene).

### Rec 23 — Minimum data model changes (task §30)
Exactly these additive changes; NO new entities, NO changes to
Character/VoiceProfile/PromptBlock/Project shapes:

    AudioAsset   + block_id: Opt[str], part_index: Opt[int],
                   part_version: Opt[int], generation_run: Opt[str]
    Scene        + expected_audio_slots: List[dict]   (default [])
                 + combined_outputs: List[dict]        (default [])
                 + selected_block_audio: Dict[str,str] (default {})
                 + selected_output: Opt[dict]          (default None)
                 ~ status: same field, derived value set
    BatchJob     + source_block_id: Opt[str], part_index: Opt[int]
    GenerationRequest / GenerationResult
                 + block_id, part_index, part_version, generation_run
    HistoryEntry + type, block_id, part_index, part_version, generation_run
    PromptBlock  — unchanged (block state is derived; Rec 5)
    Project      — unchanged (existing combined_outputs field reused)
    NEW module   engine/audio_provenance.py — pure functions only:
                 slot/coverage/state derivation, version+run allocation,
                 filename builders, resolve_scene_output, lineage builders,
                 stale checks, timestamp parsing (D-4 fix). ~300 lines, no UI,
                 fully unit-testable.

### Rec 24 — Migration strategy (task §31-24)
No renames, no rewrites, no one-time conversion job. Every new field has a
default; every reader is tolerant (existing pattern from P3.26's
editor_mode). On load: legacy assets (no part_version) are "legacy scope" —
they neither map to slots nor vanish; a Scene with no materialised
`expected_audio_slots` uses legacy coverage (≥1 asset ⇒ covered), preserving
today's observed behaviour exactly. Per-slot tracking begins with the first
new-model batch preview. Old `status` strings recompute on first load
("complete" may honestly downgrade to "partial" — that is the corrected
semantics, not data loss). Existing files on disk are never touched; the next
generation of each slot allocates v01-or-higher with disk-guard, so an old
unversioned `Eden_S03_Part_01_ab12cd34.wav` and a new
`…_Long_v01_Part_001_ab12cd34.wav` coexist without collision.

### Rec 25 — Required tests (task §31-25)
Runtime tests (real QApplication/MainWindow/Engine, engine.generate
monkeypatched at the harness boundary — the established house pattern), one
new file `tests/test_p3_27_audio_provenance.py` + pure-function tests for
`engine/audio_provenance.py`:

  1.  Slot derivation: blocks/plain/speaker splits → expected slots persist.
  2.  Block generated state: B1✓B2✓B3—B4✓ from assets; derived, not stored.
  3.  Coverage states: NOT_GENERATED/GENERATING/PARTIAL/COMPLETE/ERROR matrix.
  4.  Complete-after-partial: generate B3 → COMPLETE (§34).
  5.  Partial regen does not degrade COMPLETE; B2 regen → v02, B1 keeps v01.
  6.  Failed regen of covered slot: coverage unchanged; ✗ visible.
  7.  Failed first run: ERROR; mixed fail/success: PARTIAL with ✗.
  8.  Cancel mid-batch: completed stay, skipped stay uncovered → PARTIAL/COMPLETE.
  9.  Version monotonicity: v01→v02→v03; failed version number skipped; no reuse.
  10. Filename convention incl. speaker + sanitisation + version bump on
      disk-collision (two same-slot generations same second).
  11. No-overwrite regression: re-running Generate Long does NOT touch the
      previous run's files (D-1 closed).
  12. Run identity: assets grouped by generation_run; run seq increments.
  13. Scene routing: switch Scene mid-batch → assets/status land on the
      correct Scene by scene_id (D-3 closed).
  14. Review mode: checkbox ON → no auto-start; Generate starts only on click;
      checkbox OFF → auto-start (today's behaviour).
  15. Batch window defaults: only not-generated slots checked (§34 zero-click).
  16. Scene combined: build v01 → regen B2 → v01 STALE with "B2 has newer
      version" reason; v02 build; v01 file still on disk.
  17. resolve_scene_output: default chain (selected → latest combined →
      latest asset) + CUSTOM marker + MISSING badge.
  18. Project assembly consumes resolved output; label/badge correct; lineage
      records combined/asset ids.
  19. History type/provenance fields; legacy entries backfilled on read.
  20. Project export/import round-trip: assets, versions, scene combined,
      selections, slots survive; EXPORT_VERSION 2; v1 import still loads.
  21. Timestamp parse fix: is_combined_output_stale false for fresh legacy
      entries (D-4 closed).
  22. Security: traversal filename rejected by save_wav; batch export cannot
      escape destination; no silent overwrite at destination.
  23. Legacy scene load: no slots materialised → legacy coverage preserved.
  24. Plain-mode full asset coverage; blocks-mode multi-part block coverage.
  25. E2E (task §34 flow): scene PARTIAL → review → select B3 → generate →
      COMPLETE → regen B2 (v02) → still COMPLETE → combine v02 → assembly
      consumes Combined v02 — with save/restart between stages.

---

## 5. WHAT DELIBERATELY DOES NOT CHANGE

Character system, Voice Precedence (Block Character > Scene Voice > UI Voice),
Scene Assembly ordering/silence/ffmpeg path, History view UX, Project Export
format shape, Recents, settings, Raw Mode, SFX chain, the LongNarrationDialog
preview step, the single-Generate path (beyond asset field passthrough), and
the dialogue/stems workflow. No redesign of any accepted P3.22–P3.26 surface.

## 6. THE ONE NEW WRITER (provenance integrity rule)

All generation results — single, long, review, regen — flow through ONE
registration function (in engine/audio_provenance.py, called from
`_on_generation_finished_ui`):

    register_generation_result(project, result) → Asset dict
      - resolves the Scene by result.scene_id (never _active_scene)  [D-3]
      - allocates/attaches part_version + generation_run when present
      - appends the asset dict to that Scene
      - recomputes that Scene's coverage state
      - returns the asset for UI refresh

Everything else (History, BatchJob status, sidebar) consumes events as today.

## 7. PHASE PLAN (maps 1:1 to task §32)

| Phase | Deliverable | Closes |
|-------|-------------|--------|
| PH1 | audio_provenance module + additive model fields + single registration writer (scene_id routing) + versioned filenames + save_wav guards | D-1, D-3, D-5, D-8; Rec 2,4,5,6 |
| PH2 | Batch window persistent state display (grouped rows, ✓/—, version counts, coverage header) + derived scene badges | D-2; Rec 7–10 |
| PH3 | Review checkbox + review-mode gating (same pipeline) | Rec 11, 12 |
| PH4 | Version UI (version popover, per-slot "Use") + History type/provenance fields | Rec 6, 18 |
| PH5 | Scene internal combination + lineage + versioning + stale detection (+ timestamp fix) | D-4, D-6; Rec 13–15 |
| PH6 | Scene-level output selection (selected_output, CUSTOM) | Rec 16 |
| PH7 | Project Assembly integration (resolve_scene_output + badges + picker) | Rec 17 |
| PH8 | Batch export | Rec 1 |
| PH9 | Project Export/Import + History integration + doc drift cleanup | D-7; Rec 19 |
| PH10 | Stale/missing polish, full regression, ZIP, report | Rec 25 |

Each phase ends green on the full existing suite (686 tests) plus its own new
runtime tests; no phase breaks the previous one's contract.

## 8. ANSWER TO THE FINAL QUESTION (task §35)

> "What is the smallest robust data and UX architecture that allows
> SpeechStudio to remember which blocks have ever been generated, preserve
> multiple audio versions, correctly determine Scene completeness, allow
> selective re-generation, allow direct Batch export, support Scene-level
> combined audio, and finally provide one unambiguous Scene output to the
> Project audiobook Assembly without tracking text or prompt changes?"

**The append-only AudioAsset stream, provenance-tagged.** Keep exactly one
authoritative record — the existing `Scene.audio_assets` append stream (an
asset exists ⟺ that generation succeeded) — and give each new asset four
additive tags: `block_id`, `part_index`, `part_version`, `generation_run`.
Then derive every question from it with pure functions:

- *remembered generation* = a slot has ≥ 1 asset (nothing stored separately);
- *versions* = filter the stream by slot, ordered by part_version
  (monotonic max+1 allocation, disk-guarded → never overwrite, never delete);
- *completeness* = expected slots × covered slots, recomputed on events —
  starting a generation is never completion; a failed or cancelled run simply
  leaves its slots uncovered;
- *selective re-generation* = checkboxes in the batch window; unchecked parts
  are untouched by construction, because the model never invalidates anything;
- *batch export* = copy the already-correctly-named files
  (`…_Long_v02_Part_003….wav`), collision-guarded;
- *scene-level combined audio* = one new small append-only list on the Scene
  (`combined_outputs` with per-slot lineage), versioned the same way, stale =
  lineage version < current version (ids, not timestamps);
- *one unambiguous scene output* = `resolve_scene_output()` — explicit user
  selection, else latest scene-combined, else latest asset (today's rule) —
  which is the ONLY thing Project Assembly ever consumes.

Four asset fields, four Scene fields, two BatchJob/request passthrough fields,
one HistoryEntry field, one pure-function module, one registration writer, one
selection rule. No new entities, no database, no tracking of any kind — the
user alone decides when to regenerate, and the system can always answer
"generated before? which versions? which output is in use?" from the model.

---

*Design record ends. Implementation begins only after acceptance of this
proposal; the phase plan above is the implementation contract.*
