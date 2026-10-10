# P3.45.6 Closeout Correction — Release Cleanup, Combine Scene Regressions, Notification Success Icon

Round id: `P3.45.6-CLOSEOUT` · Base: `main @ 5ed1a91` (the P3.45.6
addendum release) · Scope: closeout only — no new product phase, no
force push, existing P3.45.6 functional fixes preserved verbatim.

This record documents the three corrections requested by the owner
after the P3.45.6 addendum: (1) release/project-data cleanup, (2)
targeted Combine Scene regression reproduction, (3) the notification
success-icon replacement with the user-supplied `ok2.png` artwork.

---

## §1 Release and project-data cleanup

### §1.1 Classification of every tracked `projects/*/project.json`

Diffing `ae809ae` (P3.45.5) → `5ed1a91` (P3.45.6) over `projects/`
shows exactly five files, all inserted, none ever modified after
their adding commit:

| File | Added in | Classification (proven) | Evidence |
|---|---|---|---|
| `projects/6bf099ce-6aa/` | `a2027fe` | **Test fixture** | `Project(name="Golden")` + `Character(name="Anna Char")` + `Scene(name="GoldenScene")` are created by `tests/test_p3_43_voice_unification.py::TestGoldenPath::test_full_golden_path` (lines 1354–1378) over a REAL MainWindow; the character-apply flow calls `self._project_manager.save_project(project)` (one of the 13 save call sites, mw:5284–5516 family) — and the harness MainWindow's module-level `APP_ROOT` was the REPOSITORY root (see §1.2), so the project was persisted into the repo's runtime `projects/` tree. Three different UUIDs = three separate runs. |
| `projects/7e4aa44e-19f/` | `a2027fe` | **Test fixture** | same as above (second run) |
| `projects/85055f18-b87/` | `a2027fe` | **Test fixture** | same as above (third run) |
| `projects/e2315687-5f0/` "Default" | `5e7d1d8` (Phase 5) | **Runtime-created project data** | file `created_at 2026-10-09T12:28:23`, committed in the Phase 5 commit dated `12:30:29`; the scene carries the first-run demo text because the in-memory default scene captured the demo-text editor state; saved through the same real-APP_ROOT ProjectManager channel during Phase 5 dev runs. |
| `projects/e660e1b8-70d/` "Default" | `a2027fe` (Phase 3 refinement) | **Runtime-created + test-mutated data** | scene names "01 Persisted Name" / "Inactive Renamed" / "03 Renamed Charlie" / "04 Delta" / "05 Renamed Echo" are dev-time versions of startup-restoration test fixtures; the file is the "tracked shipped Default project mutated by tests" defect recorded in OPEN_BUGS at `a2027fe`. |

**None of the five is intentionally shipped demo/seed data.** The
intended first-run experience is CODE-generated: `_connect_engine`
inserts the Hungarian demo text into the Plain Text editor on the
very first run (mw:678–707) and the in-memory phantom "Default"
project — no `project.json` is needed on disk. `grep` proves no
product code references any of the five UUIDs (only OPEN_BUGS and
this document mention them). The shipped-project regression tests
(`TestShippedDefaultProjectRestore`) seed their own "Default"
project inside their temporary APP_ROOT and are functionally
independent of the tracked files.

**Action**: `git rm -r projects/` (all five files, index and disk).
The five files are runtime/test residue in the development sandbox
clone only; no user data existed in them (the user's real machine
data is untouched by definition — these files were born inside the
P3.45.6 dev sessions).

### §1.2 Root cause of the leak — MainWindow APP_ROOT decoupling

The MainWindow-driving harnesses boot `MainWindow(engine)` where the
ENGINE received a temporary `app_root` — but MainWindow's OWN
module-level `APP_ROOT` (mw:58) stayed the repository root. Settings
already derive from the engine root (mw:143–151, the P3.25 SS-H04
fix), so the remaining real-APP_ROOT channels were:

* `mw:712` `presets_dir = APP_ROOT/presets` (PresetManager),
* `mw:717` `projects_dir = APP_ROOT/projects` (ProjectManager — the
  tracked-file mutation + new `projects/<uuid>/` writer),
* `mw:4953/4961` manual-concat outputs + relative path resolution,
* `mw:96` logo read (read-only).

Every character/scene save flow (13 `save_project` call sites)
therefore wrote real runtime data into the repository tree, and the
phase commits `git add`-ed a dirty tree → five runtime files became
tracked → the `git archive` release convention shipped them in BOTH
P3.45.6 builds.

Two further repo-write channels existed alongside:

* `engine/logger.py` hardwires `<repo>/logs` (the `engine` package
  `__init__` → `engine.events` module-level `get_logger` creates the
  process-wide Logger singleton against the repo dir BEFORE any
  patch could apply);
* `SettingsManager.instance(<repo>/settings)` auto-saves defaults
  when the file is missing (`Engine()` default-root constructions —
  `test_p3_45_4` pins that path — and the two dialog-level
  `.instance(_root/settings)` calls in `long_narration_dialog.py`
  and `batch_generation.py`).

### §1.3 The fix

1. **`.gitignore`**: `projects/` and `presets/` are runtime user
   data and are now ignored outright — the leak class is closed at
   the gate (an accidental `git add` can no longer track them).
2. **`tests/conftest.py`** — suite-level runtime-path isolation
   (session-scoped, best-effort, never raises):
   * `ui.main_window.APP_ROOT` → a session temp root; a symlinked
     `assets/` directory keeps the read-only brand/logo asset
     resolution (the `test_p3_37` window-icon pin);
   * `engine.logger.Logger.__init__` default dir → session tmp/logs
     (the singleton is reset and rebuilt through the patched init,
     which strips the repo file handlers from the shared
     `speechstudio.engine` root logger; the import-time artifact
     directory is removed);
   * `SettingsManager.instance` — ONLY the repository settings dir
     is redirected to session tmp/settings; every explicit tmp-dir
     call passes through unchanged;
   * `pytest_unconfigure` restores all three and removes the
     session root. Per-test APP_ROOT patches (the P3.45.6 Phase 3
     harness pattern) keep working unchanged.
3. **`tests/test_p3_45_4_rendering_ux_hardening.py`** —
   `test_behavior_unchanged` pins ONLY the `Engine()` default-root
   auto-detection, so the three directory-creating managers
   (VoiceManager / AudioManager / HistoryManager) are stubbed for
   that one construction; the pin itself is unchanged.
4. **Current residue removed** (settings/, logs/, outputs/, voices/
   — all gitignored or untracked runtime artifacts of previous
   sessions).

**Verified result**: full focused sweeps (587 tests) and the full
battery leave the repository untouched — no `settings/`, `logs/`,
`outputs/`, `voices/`, `projects/`, `presets/` directories appear;
`git status --porcelain` shows only the intended changes. The
post-battery check is repeated after the final full run (§5).

### §1.4 What ships in the release now

Exactly the Git tree minus `releases/`: NO `projects/` content, no
settings, no logs, no caches. The first-run experience is unchanged
(code-generated demo text + phantom project, immune per the Phase 3
first-run gate — `TestShippedDefaultProjectRestore` still pins both
the first-run and second-run behaviour with a seeded on-disk
project).

---

## §2 Combine Scene — targeted reproduction (user scenarios A–D)

The addendum round (`4494c91`) already established the root cause
and the fix; this closeout adds the two deterministic regressions
the owner's list makes explicit (C: multi-block simultaneous
deletion; D: real disk save → reload) and re-verifies the mapping:

| Owner scenario | Regression tests (file: class) |
|---|---|
| **A** — batch row removed, source block retained; Remove semantics documented | `TestRemoveSemantics` (4 tests: the job-only confirmation fires exactly for uncovered structural rows, declined keeps the job, covered/manual removals stay silent, re-added jobs check only new jobs) + `TestScreenshotGoldenPath` steps 3–5 (the reported 15/22 state: 7 removals each state the contract; header PARTIAL + "{N} expected part(s) missing"; Combine fail-closed naming Blocks 16–22). **Remove removes the generation JOB only** — pinned by `remove_job` (batch_manager.py:426–429, no Scene reference), documented in the addendum design record §3 and stated at the decision point (batch_generation.py:3203+). |
| **B** — source text + Scene block deleted, batch regenerated, Combine must not demand audio for non-existent blocks | `TestScenarioBStaleAfterDeletion` (deleted block → STALE classification with the Generate Long remedy; Re-add refuses) + `TestScenarioCRegenerateAndCombine::test_after_generate_long_missing_list_matches_current_structure` (regeneration over the current text; slots == current; Combine succeeds with the current slots). |
| **C** — MULTIPLE blocks deleted simultaneously → queue holds only current blocks' items; Combine validates the same authoritative structure | **NEW** `test_multi_block_deletion_then_regenerate_combine_uses_current_structure`: one editor selection spanning Blocks 2+3 (single multi-block deletion through the P3.44.9 transformer) → regenerate → queue jobs carry exactly `["blk01:1", "blk04:1"]` slot ids / `["blk01", "blk04"]` source blocks; Combine names ONLY the current uncovered Block 4 (never the deleted Blocks 2/3, no stale marker); after generating the missing current part the historical assets for the deleted blocks are preserved (append-only: 4 assets) and Combine succeeds with exactly `["blk01:1", "blk04:1"]`. |
| **D** — save and reload: same contract; deleted blocks don't reappear; valid audio not lost; frozen lineage preserved | `TestScenarioELoadRoundtrip::test_roundtrip_preserves_state_and_classifies_honestly` (model-level JSON round-trip) + **NEW** `test_real_disk_save_reload_preserves_contract`: a REAL `ProjectManager.save_project` → fresh-manager `get_project` disk round-trip; the deleted Block 3 does NOT reappear; the stale slot reference survives truthfully; the valid audio assets keep slot_id / part_version / generation_run lineage and their files; Combine on the reloaded scene classifies honestly (stale named + remedy, nothing mutated); after Generate Long over the reloaded text the valid audio joins and Combine succeeds with both sources and the lineage untouched (2 assets, no duplicates, no deletions). |

Frozen contracts preserved throughout: `slot_id`, `generation_run`,
`part_version`, append-only AudioAsset lineage, the P3.44.9 range
transformer, Scene Combine order. No fix-by-ignoring, no historical
audio deletion (the multi-block test explicitly asserts the orphaned
assets survive).

**Root cause (restated, proven in the addendum design record §2)**:
the queue and the recorded slot structure are decoupled structures
that only change together at the Generate Long structure event;
Remove is queue-workspace-only. The defect was in the surrounding
semantics — nothing surfaced rowless uncovered slots, and Combine's
blocker could not distinguish genuinely missing audio from stale
structure references. The addendum's three additive pieces
(truthful `resolved_slot_sources` classification, the
missing-expected-parts header + Re-add Missing repair, the
job-only-contract confirmation) closed it; this round adds the
owner-requested A–D determinism proofs on top.

---

## §3 Notification success icon (ok2.png)

### §3.1 Affected windows — inventory

167 static QMessageBox sites + 3 constructor sites + 7
`FeedbackDialog.information` sites exist in the UI. The positive
EVENT-FEEDBACK windows (the affected set) are:

* already structured (P3.44.3): Project saved (mw:6971 — the
  owner's example), Scene saved (mw:7217), Preset exported
  (mw:3167), Voice profile exported (mw:6206 + voice_library:588),
  Batch queue saved / loaded (batch_generation:3287/3309);
* migrated to the common path in THIS round (10 legacy plain
  `QMessageBox.information` success sites): Imported preset
  (mw:3114), batch audio export summary (mw:4886), Voice profile
  imported (mw:5182), Voice exported (mw:6142), History entry
  exported (mw:6504), Legacy project imported (mw:6927), Scene
  loaded (mw:7295), Export Complete (export_project_dialog:215),
  Assembly Complete (assemble_dialog:1055), Added to Scene
  (character_dialog:319).

Deliberately UNCHANGED (stock icons kept): all 38 `critical`, all
51 `warning`, all 25 `question` sites; all ~23 NEUTRAL
`.information` notices ("No active project", "Already Added",
"batch already running", "no longer available", About/Documentation
etc.) — no global icon replacement, no monkey-patching (source
pins in `test_p3_45_6_closeout_notification_icon.py`).

### §3.2 The assets

* `assets/icons/ok2.png` — the user-supplied artwork VERBATIM
  (140×145, fully opaque; proven by the asset test's exact-dimension
  + uniform-alpha-255 checks).
* `assets/icons/ok2_transparent.png` — the background-transparency
  prepared variant actually displayed. Generation method: border
  flood-fill over pixels with luminance ≥ 60 identifies the
  BACKGROUND region only (the solid ~22-luminance glyph outline
  blocks the flood; the INTERNAL white elements — sunglass lenses,
  snout, nose highlight, finger gaps — are enclosed and never
  reached); flood-region pixels are re-encoded as coverage
  (RGB := the glyph's solid fill colour (22,22,20), alpha :=
  255−luminance, pure background → alpha 0); every non-flood pixel
  keeps its ORIGINAL opaque RGBA. Programmatic verification: corners
  alpha 0; 2311 enclosed white pixels preserved opaque; 12008
  drawing pixels byte-identical; 868 partial-alpha anti-aliased edge
  pixels; 36.6% fully transparent.

The drawing itself is NOT redrawn or altered — the original
`ok2.png` ships untouched as the reference copy, and the displayed
variant differs only in background alpha (exactly the sanctioned
"background-transparency prepared variant" of the task).

### §3.3 The integration — one common path

`ui/feedback.py::FeedbackDialog.information` (the P3.44.3 common
path for structured event feedback) now sets the icon pixmap in its
box-construction step (`_make_information_box`, split from
`information()` so the box is inspectable without the modal loop —
identical construction for both entry points):

* resolution is APP_ROOT-relative (`ui/feedback.py`'s own module
  root → `assets/icons/ok2_transparent.png`) — no absolute
  developer path, no upload-location dependency (pinned by the
  any-CWD test and the source scan);
* scaled into the standard 48px message-box icon box with the aspect
  ratio preserved (140:145 → 46×48 — no distortion, no clipping,
  matches the stock Fusion icon geometry so the icon column and
  text layout are unchanged), device-pixel-ratio aware, cached;
* missing/unloadable asset → silent fallback to the stock
  `Icon.Information` (the non-fatal-asset house rule; pinned by the
  fallback test).

### §3.4 Visual verification (offscreen, honestly reported)

* Composite checks (46×48 on #1A1A1A and #FFFFFF): VLM-verified —
  no white rectangle on dark, no halo, no distortion/clipping; the
  opaque original on dark was VLM-confirmed as the "stark white
  box" the owner warned about (the negative example).
* REAL dialog render: a genuine `FeedbackDialog` ("Project saved:
  Eden") with the default modern-dark theme was rendered offscreen
  (`box.grab()`, 297×190) and VLM-inspected: the mascot icon is
  visible at an appropriate standard size, transparent background
  (dark surface shows through), not clipped/distorted, text layout
  intact, no other defects.
* Honest residual: on the 4 dark themes the near-black drawing has
  inherently LOW CONTRAST (VLM noted it in the standalone large
  composite; in the real 46×48 dialog render it remained clearly
  recognizable). Per the task constraints the drawing was NOT
  recoloured or redrawn; on the light theme it is fully readable.
  A theme-aware light-on-dark variant remains a documented future
  option if the owner wants higher dark-theme contrast.
* The offscreen platform cannot validate the final on-screen
  appearance on the owner's Windows machine (platform style
  differences); the Windows-side visual confirmation stays with the
  owner's manual checklist.

### §3.5 Packaging

Both PNGs are version-controlled under `assets/icons/` (the
existing versioned asset directory), resolved at runtime relative to
the application root — they are inside the `git archive` file set
and therefore inside the release ZIP (content-checked at build
time, §5).

---

## §4 Regression tests added this round

`tests/test_p3_45_6_closeout_notification_icon.py` (17 tests):
asset contracts (original verbatim + transparent variant with
preserved drawing/internal whites), pixmap resolution/size/aspect/
cache/any-CWD/no-absolute-path, real-widget icon wiring (the
`qt_msgboxex_icon_label` carries the pixmap), missing-asset
fallback, warning/critical/question stock icons unchanged, the 11
MainWindow call-site count + message pins + stale-blob absence,
panel migrations + shared imports, neutral/error/question plain
forms kept, no QMessageBox monkey-patching, `.gitignore` runtime
entries, live conftest isolation (redirect active + a REAL
MainWindow's ProjectManager resolves inside the session root).

`tests/test_p3_45_6_batch_removal_combine_integrity.py` (+2 → 30
tests): owner scenario C (multi-block simultaneous deletion) and
scenario D (real ProjectManager disk save → reload), as mapped in
§2.

`tests/test_p3_28_audio_provenance.py`: the three batch-audio-export
patches updated from `PySide6.QtWidgets.QMessageBox.information` to
`ui.feedback.FeedbackDialog.information` (the flow now routes
through the common path).

`tests/test_p3_45_4_rendering_ux_hardening.py`: default-root
`Engine()` construction stubs the three mkdir'ing managers (the
app_root auto-detection pin itself is unchanged).

`tests/test_p3_45_6_startup_restoration.py`: the shipped-project
class docstring updated to the cleaned reality (the tests seed
their own project; behaviour unchanged).

---

## §5 Verification and release

* Focused sweeps: 151 + 436 = 587 tests green; the extended combine
  suite 30/30 twice; the icon suite 17/17 twice.
* Full battery, all validators, launch smoke: see the final report
  and DEVELOPMENT_LOG (evidence graded per the house convention).
* Post-battery pollution check: `git status --porcelain` clean
  except intended changes; no runtime directory reappears.
* Fresh build `releases/GUINEO_P3.45.6_current.zip` from the cleaned
  Git tree via the `git archive` convention (convention re-verified
  against the previous build first); ZIP content checked by NAME
  (not just count): no `projects/`, no settings/logs/caches, both
  `ok2` assets present; build in its own commit; keep-last-5; push;
  local == origin/main; clean tree; committed blob == on-disk.

## §6 Decisions and residuals

* No product behaviour changed except: the success icon on the
  affected event-feedback dialogs (user-requested) and the
  summary/details split of the 10 migrated legacy success messages
  (identical information, the P3.44.3 two-level presentation; the
  P3.44.3 suite and the format audit remain green).
* Dark-theme icon contrast: documented residual (§3.4) — not acted
  on (would require altering the drawing, which the task forbids).
* The launch smoke (real entry path, outside pytest) still writes
  real runtime settings/logs by design; it is run with a probe that
  cleans its own artifacts afterwards (documented in the final
  report).

## §7 P3.45.7 addendum — artwork swap: ok2.png → ok3.png (verbatim)

Owner instruction after the P3.45.6 final audit: replace the success
artwork on the confirmation/positive-feedback windows with the newly
supplied `ok3.png`, and — unlike the ok2 round — do NOT perform ANY
transparency processing on it.

### §7.1 The new asset (facts, verified programmatically + VLM)

`assets/icons/ok3.png`, 236×234 RGBA, byte-identical to the uploaded
artwork (SHA-256 pinned in the test suite:
`cb98d65a…91d6cc3`):

* the artwork is a circular monochrome badge — the GUINEO mascot
  (aviator sunglasses, thumbs-up) inside a thick black circular
  border with a WHITE fill inside the badge;
* the transparency is ALREADY PREPARED in the file: the four corners
  and the whole region outside the circle are fully transparent
  (13 089 alpha-0 pixels; VLM confirms the dark background shows
  through cleanly outside the circle);
* anti-aliased edges are genuine partial alpha (41 135 partial-alpha
  pixels); there is NO fully-opaque pixel — max alpha is 254 (a
  property of the supplied file, kept verbatim);
* the white fill INSIDE the badge is part of the artwork design, not
  a background artifact — on dark themes the icon reads as a circular
  sticker badge (VLM-verified on a real modern-dark dialog render:
  icon visible, proper size, no distortion, no white rectangle
  outside the circle, layout intact).

### §7.2 What changed (3 files) and what did NOT

* `assets/icons/ok3.png` ADDED verbatim; `assets/icons/ok2.png` and
  `assets/icons/ok2_transparent.png` REMOVED (retired — no dead
  artwork ships).
* `ui/feedback.py`: `_SUCCESS_ICON_FILE = "ok3.png"` + docstrings
  updated (the module docstring now records the P3.45.7 swap and the
  no-processing rule). The RESOLUTION PATH IS UNCHANGED:
  APP_ROOT-relative, 48px box, aspect preserved (236/234), DPR-aware,
  cached, stock-icon fallback. No call-site changes anywhere — the
  17 positive event-feedback windows pick the new artwork through the
  ONE common path automatically; errors/warnings/questions/neutral
  notices keep stock icons (scope guards untouched).
* `tests/test_p3_45_6_closeout_notification_icon.py`: the asset
  contract tests rewritten for ok3 — byte-level SHA-256 verbatim pin
  (any re-encode or "transparency improvement" fails the suite),
  236×234 dimension pin, corner-alpha-0, partial-alpha edge presence,
  badge-content presence, ok2-retirement pin,
  `_SUCCESS_ICON_FILE == "ok3.png"` pin; the pixmap aspect pin
  updated to 236/234; the no-white-rectangle pixmap check reworded
  (the guard is about the area OUTSIDE the badge). Still 17 tests.

### §7.3 Residuals

* The dark-theme contrast question of the ok2 round is RESOLVED BY
  THE ARTWORK ITSELF: the ok3 badge carries its own white fill, so
  the drawing reads on dark themes (VLM-verified on modern-dark).
* Windows-side visual confirmation remains on the owner's manual
  checklist (unchanged house residual).
* The ok2 transparency-preparation pipeline is retired with the
  asset; nothing in the product references it anymore.
