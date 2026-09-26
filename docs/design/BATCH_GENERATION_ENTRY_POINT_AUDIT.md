# SpeechStudio — Batch Generation Duplicate UI Audit

> **STATUS: IMPLEMENTED (P3.35).** The recommendations below were
> accepted and implemented — see `DEVELOPMENT_LOG.txt` (P3.35 entry) and
> `tests/test_p3_35_batch_two_workflows.py`. One dialog, two workflows,
> one live instance, shared wiring helper, honest titles/tooltip,
> "Merge Completed Parts" in the manual queue, geometry + per-mode
> column persistence, and the clipped "None" button fix.

**AUDIT AND DESIGN DECISION ONLY — no code, tests, menus, or dialogs were
modified.** Produced as the answer to the user's 18-section audit brief.

Probe evidence: `/home/z/audit_probes/probe_batch_entry_points.py`
(read-only, offscreen; reproduces both construction paths exactly as
`main_window.py` builds them).

---

## §1 EVERY BATCH GENERATION IMPLEMENTATION (verified by search)

| Class / module | Location | Role |
|---|---|---|
| `BatchGenerationDialog` | `ui/panels/batch_generation.py:352` (2,111 lines) | The ONLY batch window. Non-modal. **Two modes** (see §3). |
| `JobEditDialog` | `ui/panels/batch_generation.py:78` | Modal editor for ONE job (Name/Prompt/Voice/params/seed). Helper of the above, not an entry point of its own. |
| `BatchManager` / `BatchJob` / `BatchSummary` | `engine/batch_manager.py:300/89/266` | The ONE engine. Qt-free. Created **once** in `MainWindow._connect_engine` (`ui/main_window.py:718-724`) — a singleton both windows drive. |
| `_BatchUpdateContext` | `control_panel.py:1190`, `modern_control_panel.py:1872` | UNRELATED — "batch UI refresh suppression" context manager, not generation. |
| Legacy dialog classes | — | **NONE FOUND.** Every `QDialog` in `batch_generation.py` is listed above; no other file defines a batch window. |

**Number of dialog implementations: 1.** There is no second, legacy class.

## §2 ENTRY POINT MAP (verified from source)

| # | Entry point | UI location | Handler | Class + construction | Mode | Signals wired by handler |
|---|---|---|---|---|---|---|
| E1 | "▶ Generate Long" button; Generation-menu "Generate Long Narration" `Ctrl+Shift+Return`; top-nav **Edit** menu item (same QAction) | Editor mode bar (`narration_editor.py:774`), classic Generation menu (`menu_bar.py:71`), top-nav Edit (`top_navigation.py:336`) | `_on_generate_long_narration` → `LongNarrationDialog` (parts preview, speaker→voice map, "Review before generating" checkbox) → `_start_long_narration` (`main_window.py:2832/2937`) | `BatchGenerationDialog(bm, voices, self, scene=…, project=…, app_root=…, review_mode=…)` (`main_window.py:3143-3146`) | **SCENE MODE** | **9** (`concatenate`, `combine_scene`, `use_version`, `select_output`, `play`, `stop_playback`, `regen`, `generate_selected`, `export_audio`) + `set_on_batch_completed` (`3148-3180`) |
| E2 | "Batch Generation..." action — **ONE QAction** (`menu_bar.py:97`, classic **Tools** menu) reused verbatim by the top-nav **Project** menu (`top_navigation.py:358`); connected at `main_window.py:389` | Tools menu + top-nav Project menu (the "top menu" the user reported) | `_on_open_batch_generation` (`main_window.py:2819-2830`) | `BatchGenerationDialog(self._batch_manager, voices, self)` — bare, no kwargs | **MANUAL MODE** (legacy P3.18 layout) | **0** — nothing is connected |
| E3 | "Load Dialogue Scene..." | File menu (both bars) | `_on_load_scene` (`main_window.py:5963-6046`) | `BatchGenerationDialog(bm, voices, self)` — bare kwargs | **MANUAL MODE** (scene jobs from `.scene.json`) | **4** (`concatenate`, `play`, `stop_playback`, `regen` — `6025-6033`) |

User-facing entry points: **4 buttons/menu items → 3 handlers → 2 construction
shapes → 1 class.**

## §3 ARE THERE ACTUALLY TWO WINDOWS? — Answer: **C + D**

- **A. Two classes?** NO — one class (`BatchGenerationDialog`).
- **B. Same class, two construction paths?** YES — three, in fact (§2).
- **C. Same class configured differently?** YES — the `scene=None` ctor
  kwarg is the mode switch (`batch_generation.py:412-414, 675`):
  - **Scene mode** (`scene is not None`) — 7-column table
    (`#, ✓, File Name, Generated+versions▾, Status, Duration, Actions`),
    coverage header + All/None, SCENE COMBINED OUTPUTS section, Combine
    Scene, Export Audio, GENERATE CHECKED, review mode.
  - **Manual mode** (`scene is None`) — the *self-described* "LEGACY manual
    batch window … original P3.18 five-column layout … kept EXACTLY"
    (`batch_generation.py:412-414, 669-674`): `#, File Name, Status,
    Duration, Actions`, START BATCH, no provenance UI.
- **D. Legacy wrapper?** No wrapper — but the **menu handler is an
  unwired construction** of the same class (see §4 defect).

Probe (both built exactly as the handlers build them, same job in each):

| Feature | E1 Long (scene mode) | E2 Menu (manual mode) |
|---|---|---|
| Window title | "Batch Generation" | "Batch Generation" (**identical — root of the confusion**) |
| Table | 7 columns | 5 columns |
| Coverage header / All-None | ✔ | ✘ |
| Checkbox (selective generation) column | ✔ | ✘ |
| Generated-state + versions ▾ column | ✔ | ✘ |
| SCENE COMBINED OUTPUTS section | ✔ | ✘ |
| Combine Scene button | ✔ | ✘ |
| Export Audio… button | ✔ | ✘ |
| Primary button | GENERATE CHECKED (N) | START BATCH |
| Review mode (jobs stay pending) | ✔ | ✘ |
| Add/Edit/Duplicate/Remove/Up/Down | ✔ | ✔ |
| Save Queue…/Load Queue…/Clear | ✔ | ✔ |
| Concatenate button | ✔ (present, legacy) | ✔ (present) |
| Pause / Stop | ✔ | ✔ |
| Per-row Play / Stop-playback / Regen | ✔ | ✔ (buttons exist) |
| Anomaly display (P3.27B "163.8s ⚠" + tooltip) | ✔ | ✔ (job-driven) |

## §4 FEATURE COMPARISON — exact functional differences

Everything in §3's table, PLUS the decisive wiring difference (source-verified
connect counts; E2's handler contains **zero** `.connect(` calls):

| Control | E1 Long path | E2 Menu path | E3 Load-scene path |
|---|---|---|---|
| START / GENERATE CHECKED | works (manual `manager.start()` / signal → `_on_generate_selected`) | **works** (internal `manager.start()`) | works |
| Pause / Stop batch | works (internal) | **works** (internal) | works |
| Add/Edit/Dup/Remove/Reorder/Save/Load/Clear | works (internal) | **works** (internal) | works |
| Per-row **Play** | works (`_on_batch_play`) | **DEAD BUTTON** (0 receivers) | works |
| Per-row **Stop playback** | works | **DEAD BUTTON** | works |
| Per-row **Regen** | works (`_on_batch_regen`, versioned) | **DEAD** — confirmation dialog appears, "Yes" → *nothing happens* | works |
| **Concatenate** | works (`_on_concatenate_requested`) | **DEAD** after its validation messageboxes | works |
| Combine Scene / versions / output selection / Export Audio / selective generation | works (9 wires) | not shown (scene-only) | not shown |

Additional interference defects (probe-confirmed "C" scenario):

1. **Callback theft on the shared manager.** `BatchManager.set_on_changed`
   (`batch_manager.py:924-925`) and `marshal_to_ui` are SINGLE slots. Each
   dialog's `_connect_manager` overwrites both. Opening the menu window
   while a Long Generation window is open **steals live updates from the
   still-visible scene window** (probe: both flags `True`). The scene window
   freezes visually mid-generation.
2. **`_batch_dialog` single reference** (`main_window.py:2828/3143/6024`):
   both windows can coexist (Qt-parented), only the newest refreshes.
3. **Close-event coupling:** either window's `closeEvent` stops a running
   batch (`batch_generation.py:2108-2109`) — closing the redundant window
   kills the other one's generation.
4. Because both windows share ONE `BatchManager`, after running Long
   Generation and closing its window, **E2 shows the same scene jobs** in
   the crippled manual layout — exactly the user's "second, older-looking
   window with fewer features".

## §5 AUTHORITATIVE BATCH WORKFLOW

The accepted architecture (P3.27/P3.28 design records) is:

```
Long Generation → Batch Generation (scene mode) → generation slots
  → persistent AudioAssets → per-slot versions → Scene completeness
  → provenance → History
```

**The scene-mode construction (E1) is the authoritative implementation.**
It is the only path that materialises `expected_audio_slots`, allocates a
generation run, allocates per-slot versions (`next_slot_version`), carries
`slot_id/source_block_id/character_id/generation_run` on every `BatchJob`
(`main_window.py:3083-3124`), recompute coverage, Combine Scene, and
Assembly-compatible output resolution.

## §6 PROJECT MENU ENTRY — why it exists

- It is the **original** entry point: feature registry F-401..F-410 list
  "Gate: Tools menu" (`feature_registry.yaml:73-85`); the capability matrix
  documents `batch | Tools -> Batch Generation -> add jobs -> Start
  (manual)` (`capability_matrix.yaml:70`). It predates the P3.23–P3.28
  Long Generation upgrades.
- P3.6 deliberately **re-exposed** the same QAction in the top-nav Project
  menu (asserted by `tests/test_p3_6_sidebar_redesign.py:534-540`), and
  `tools/verify_feature_gate.py:94-106` **requires** the action and the
  `_on_open_batch_generation` handler to exist (governance-protected).
- It does **not** instantiate a legacy class; it opens the same dialog in
  manual mode against the shared manager.
- **Legitimate purpose independent of Long Generation: YES** — the manual
  batch queue (arbitrary jobs: + Add / Edit / Duplicate / Save/Load queue
  YAML / START BATCH / Concatenate). It does NOT create a manual batch
  *instead of* Scene Long Generation unless the user builds one.

## §7 INTENDED DISTINCTION — CONFIRMED

The two entry points are **intended** to be:

- **Long Generation (E1) = Scene-based generation** — slots, versions,
  provenance, completeness, Assembly.
- **Batch Generation menu (E2) = Manual Batch Queue** — arbitrary jobs,
  queue persistence, plain merge.

Evidence: the code comments themselves (`batch_generation.py:412-414`
"None = legacy manual batch window — all P3.28 provenance UI is hidden and
behaviour is unchanged"; `669-674` "LEGACY manual window … kept EXACTLY"),
the F-401 spec, and the accepted `COMBINE_VS_CONCATENATE_AUDIT.md`
conclusion ("Manual batch mode (no Scene): … merging a manual job queue is
a legitimate need — keep").

They already share the same underlying engine (§8). **They must NOT be
merged blindly.**

## §8 SHARED PIPELINE — one source of truth (verified)

- **ONE BatchManager** singleton (`main_window.py:718-724`), submit_fn =
  `engine.generate` — identical for all entry points.
- **ONE History registration path** — engine-level `self._history.add(result)`
  (`engine/engine.py:348-350`), dialog-agnostic.
- **ONE AudioAsset/provenance chain** — context rides
  `BatchJob → to_request() → GenerationRequest → Result → AudioAsset/History`
  (C2 fix, `batch_manager.py:122-147`); nothing is duplicated per dialog.
- Duplicated state: **NONE.** The only per-dialog state is view state
  (`_check_states`, geometry key `window/batch_dialog`).

## §9–10 RECOMMENDED FINAL ARCHITECTURE / UI (for agreement — NOT implemented)

Keep the two workflows, make them honest (per §9: "the user must
immediately understand why there are two"):

1. **Fix E2's wiring** (the real defect): connect the four manual-mode
   signals exactly like the in-repo template `_on_load_scene`
   (`main_window.py:6025-6033`): `play_requested`, `stop_playback_requested`,
   `regen_requested`, `concatenate_requested`. Best shape: extract ONE
   shared `_wire_batch_dialog(dlg)` helper used by all three construction
   sites so the wiring can never drift again (scene-only signals connected
   unconditionally are harmless — their buttons don't exist in manual mode).
2. **Name the distinction** (suggested): scene window title
   `"Batch Generation — {Scene name}"`; manual window `"Batch Queue"`
   (or keep "Batch Generation" + a mode subtitle). Tooltip on the menu
   action: "Open the manual batch queue (independent of Scenes)".
3. **Single live instance**: if a batch dialog is already open, focus it
   instead of constructing a second (eliminates callback theft + close-stop
   interference + the "two different windows" confusion). If the open
   window's mode differs from the requested one, close-then-reopen.
4. **Do NOT** remove the menu action: it is the documented F-401 gate, the
   P3.6 test and `verify_feature_gate.py` depend on it, and the manual
   queue is a real use case.
5. Ride-along (already accepted in COMBINE_VS_CONCATENATE_AUDIT.md,
   still pending implementation): relabel manual-mode Concatenate to
   "Merge Completed Parts" with an honest tooltip.

## §11 LEGACY CODE AUDIT

| Item | Status |
|---|---|
| Unused Batch classes | **none** — one dialog + one editor + one manager |
| Dead imports / old handlers / old menu actions | **none found** — every action traced to a live handler |
| "Legacy" code that exists | The manual-mode *layout branch* inside the one dialog (deliberate, documented, test-pinned by P3.18-era tests) and the legacy Concatenate merge path (audited separately; keep in manual mode per accepted design) |
| Compatibility code | `verify_feature_gate.py` + `test_p3_6` pin the menu action; E3 load-scene depends on the same manual mode |

## §12 UX VERDICT

The current state violates the "no two visually similar windows with
different capabilities without explanation" rule **only in presentation**:
identical titles ("Batch Generation") + the unwired E2 buttons make the
manual queue *look* like a broken, older twin. The mode split itself is
sound; the fix is naming + wiring + single-instance, not consolidation.

## §13 USER'S REPORTED WORKFLOW — reproduced (probe §A/§B/§C)

A. Long Generation → 7-col scene window, full feature set, 9 wires.
B. Close it (queue keeps the scene jobs — shared manager).
C. Project-menu "Batch Generation..." → **same title**, 5-col layout, same
jobs shown without versions/coverage, Concatenate + row Play/Stop/Regen
present but dead; if opened while the scene window is still up, it also
freezes that window's live updates.
Differences documented in §3/§4 tables (title/layout/buttons/version/
export/character/scene/combine/stop/history/state).

## §15 REQUIRED FINAL ANSWERS

1. **Implementations:** 1 dialog class (+1 modal job editor) + 1 engine.
2. **User-facing entry points:** 4 (Generate Long button; Generation-menu /
   top-nav-Edit "Generate Long Narration"; Tools-menu "Batch Generation…";
   top-nav-Project "Batch Generation…" — the latter is the SAME QAction as
   Tools; plus File-menu "Load Dialogue Scene…" as a 5th, queue-loading one).
3. **Class per entry:** `BatchGenerationDialog` for all (E1 scene mode,
   E2/E3 manual mode).
4. **Exact functional differences:** §3/§4 tables — mode-gated features +
   E2's four dead controls + shared-manager interference.
5. **Authoritative:** the **Long Generation → scene-mode** construction (E1).
6. **Is the Project/Tools menu entry legacy?** It is the *original* manual
   entry, governance-pinned, and serves a distinct purpose — **not dead
   legacy**, but currently **mis-wired**.
7. **Two distinct use cases?** YES — Scene Long Generation vs Manual Batch
   Queue (documented intent).
8. **Same generation pipeline?** YES — one BatchManager, one engine
   submit path, one History/AudioAsset registration chain.
9. **Recommended architecture:** keep one engine + one dialog class, two
   explicit modes; fix E2 wiring via a shared wiring helper; single
   live instance.
10. **Recommended UI:** distinct titles ("Batch Generation — Scene X" /
    "Batch Queue"), honest tooltips, manual-mode Concatenate relabel
    (already-accepted ride-along).
11. **Files removable:** none.
12. **Files that must remain:** `ui/panels/batch_generation.py`,
    `engine/batch_manager.py`, `ui/panels/menu_bar.py`,
    `ui/panels/top_navigation.py`, `ui/main_window.py`,
    `ui/panels/long_narration_dialog.py`, `ui/panels/narration_editor.py`.
13. **Migration/compat:** `verify_feature_gate.py:94-106` and
    `test_p3_6_sidebar_redesign.py:534-540` pin the action name
    `"batch_generation"` + handler `_on_open_batch_generation` — keep both
    identifiers when rewiring; shared geometry setting key
    `window/batch_dialog` is fine; queue YAML format unchanged.
14. **Required regression tests (when implementing):** E2 wires the four
    manual signals; manual-mode Play/Stop/Regen/Concatenate actually invoke
    handlers; dual-open (scene window keeps refreshing / single-instance
    focus); E3 load-scene unchanged; scene-mode P3.28 suite green;
    `verify_feature_gate.py` green.

## §18 FINAL VERDICT

**C. SAME ENGINE, TWO VALID ENTRY POINTS — unify presentation.**

(Verdict B's "two valid workflows" is the product truth, but since there is
only ONE implementation, the correct technical verdict is C: the two
windows are the same class + same engine exposed through differently-wired
constructions. Do not merge dialogs; do not remove the menu action; fix the
missing wiring, differentiate the presentation, and enforce a single live
instance. No implementation performed — awaiting agreement.)
