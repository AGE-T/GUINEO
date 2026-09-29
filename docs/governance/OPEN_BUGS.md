# SpeechStudio Open Bugs / Documentation Backlog

This file tracks known issues that have NOT yet been fixed. Each
entry is removed from this file only after the fix is implemented
AND independently verified.

**Status legend:**
- **OPEN** — not yet fixed
- **FIXED-PENDING-VERIFICATION** — fix implemented, awaiting
  independent verification of exact preview/model equality
- **CLOSED** — fixed and independently verified

---

## BUG-001: Advanced Preview emits invalid `expressive_normal` token

- **Status**: FIXED-PENDING-VERIFICATION
- **Severity**: Critical (prompt correctness)
- **Discovered**: Post-FINAL-CORRECTION audit
- **ADR reference**: ADR 003 §7
- **Fix applied**: `engine/prompt_state.py` `compile_continuous()`
  now skips emission of any prosody token when the effective value
  is `"Normal"` or `None` — matching `PromptBuilder.build()` behavior.

### Description

The Advanced Preview (`CanonicalPromptCompiler.compile_continuous()`)
and the center Preview (`CanonicalPromptCompiler.compile_for_generate()`
→ `PromptBuilder.build()`) did not produce identical token sequences.

`compile_continuous()` could emit:

```
<|prosody:expressive_normal|>
```

while the center Preview correctly produced NO token for "Normal"
delivery. The same issue applied to `speed_normal` and `pitch_normal`.

### Root Cause

1. `engine/higgs_tokens.py` defines placeholder
   `ProsodyDef("Normal", "expressive_normal", ...)` entries. These
   are application-level "no-op" markers — NOT valid HIGGS V3 tokens.

2. The official HIGGS V3 reference defines only:
   - Speed: `speed_very_slow`, `speed_slow`, `speed_fast`, `speed_very_fast`
   - Pitch: `pitch_low`, `pitch_high`
   - Delivery: `expressive_high`, `expressive_low`
   
   There is NO `*_normal` token in HIGGS V3.

3. `PromptBuilder.build()` correctly checked `!= "Normal"` before
   emitting. `compile_continuous()` did NOT — it emitted a token
   whenever the value changed, including to "Normal".

### Fix Applied

`compile_continuous()` (in `engine/prompt_state.py`) now skips
emission for any prosody value that is `"Normal"` or `None`. The
`prev` tracker is reset to `None` when a "Normal" value is
encountered, so that a subsequent non-Normal value correctly emits
(e.g. Normal→High→Normal emits `expressive_high` once for the
middle block, nothing for the first and last).

### Verification Performed

The following tests were run and PASS:

1. **Required test** — 3 blocks with Delivery Normal/Expressive
   High/Normal:
   - Block 1 (Normal) → no expressive token ✓
   - Block 2 (Expressive High) → `<|prosody:expressive_high|>` ✓
   - Block 3 (Normal) → no `expressive_normal` token ✓

2. **Expanded test** — Speed Normal, Pitch Normal, Delivery Normal
   individually and combined:
   - None produces a HIGGS token ✓
   - No `*_normal` token in any output ✓

3. **Preview Hard Contract** — Center Preview vs Advanced Preview
   for 10 different configurations (emotion, style, speed, pitch,
   delivery, all-Normal, all-non-Normal):
   - All 10 produce IDENTICAL prompt strings (exact match) ✓
   - No `*_normal` tokens in any path ✓

### Why Status is FIXED-PENDING-VERIFICATION (not CLOSED)

Per the user's directive: "Until exact preview/model equality is
independently demonstrated, BUG-001 remains OPEN."

The fix is implemented and the automated tests pass, but the status
remains FIXED-PENDING-VERIFICATION until an independent audit
confirms that:
1. The exact final prompt string is identical across Center Preview,
   Advanced Preview, and the actual model input.
2. No `*_normal` token reaches the model in any code path.
3. The fix survives a real generation test (not just unit tests).

An independent reviewer should re-run the tests above and additionally
verify the model input path end-to-end before marking this CLOSED.

### Related Files

- `engine/prompt_state.py` — `compile_continuous()` (fixed)
- `engine/prompt_builder.py` — `build()` (reference for correct skip behavior)
- `engine/higgs_tokens.py` — token definitions (see BUG-002)
- `ui/panels/long_narration_dialog.py` — `_on_generate()` (refactored
  to use `CanonicalPromptCompiler` instead of direct `PromptBuilder`;
  removed the `<|` hybrid shortcut)

---

## BUG-002: `expressive_normal` / `speed_normal` / `pitch_normal` are not valid HIGGS V3 tokens

- **Status**: OPEN (partially mitigated by BUG-001 fix)
- **Severity**: Medium (documentation / catalog accuracy)
- **Related**: BUG-001

### Description

`engine/higgs_tokens.py` defines three "Normal" prosody tokens that
do NOT exist in the official HIGGS V3 reference:

| Application token       | Tag               | HIGGS V3? |
|-------------------------|-------------------|-----------|
| `ProsodyDef("Normal", "speed_normal", ...)` | `speed_normal` | ❌ NOT in reference |
| `ProsodyDef("Normal", "pitch_normal", ...)` | `pitch_normal` | ❌ NOT in reference |
| `ProsodyDef("Normal", "expressive_normal", ...)` | `expressive_normal` | ❌ NOT in reference |

### Current Mitigation

BUG-001's fix ensures these tags are never emitted by
`compile_continuous()`. `PromptBuilder.build()` already skipped them.
So no `*_normal` token reaches the model.

### Remaining Work

The placeholders still exist in `higgs_tokens.py` and are referenced
in the in-app Token Guide (`ui/panels/token_guide.py`). They should
be either:
1. Marked as `application_only=True` (non-emittable), OR
2. Removed from the token catalogue and represented differently in
   the UI (e.g. a "Default" option that maps to `None`).

The Token Guide must be corrected to not list them as valid HIGGS V3
tokens.

### Verification

After the fix:
1. `grep -rn "expressive_normal\|speed_normal\|pitch_normal" engine/`
   must return no matches in token-emitting code paths.
2. The Token Guide must not list these as valid HIGGS V3 tokens.
3. All verification scripts must pass.

---

## BUG-003: Prompt Pipeline Contract is not a standalone document

- **Status**: OPEN
- **Severity**: Low (documentation gap)
- **ADR reference**: ADR 003 §10

### Description

The Prompt Pipeline Contract is referenced in the codebase
(`engine/prompt_state.py`, `ui/main_window.py`,
`ui/panels/narration_editor.py`) as the authoritative specification
for the prompt pipeline. However, the contract is currently embedded
in code comments and docstrings — there is no standalone document.

### Required Fix

Extract the contract into a standalone document:
`docs/governance/PROMPT_PIPELINE_CONTRACT.md`.

The document must explicitly distinguish:
- **Application semantics** (SpeechStudio's scope rules, precedence,
  ownership model) — these are SpeechStudio decisions.
- **HIGGS V3 model semantics** (token syntax, placement rules,
  token catalogue) — these are defined by the model, not by
  SpeechStudio.

### Verification

After the fix:
1. `docs/governance/PROMPT_PIPELINE_CONTRACT.md` exists.
2. The contract is referenced from ADR 003 and the governance README.
3. The contract clearly distinguishes application semantics from
   HIGGS V3 model semantics.

---

## BUG-004: SemanticTokenState flattens sentence-scoped tokens to one global value

- **Status**: OPEN (architectural limitation — not a regression)
- **Severity**: Low (UI workflow removed — no current user impact)
- **ADR reference**: ADR 003 §3, §8
- **Do not refactor yet** — this is documented for future planning.

### Description

`SemanticTokenState` (in `engine/prompt_state.py`) flattens all
parsed tokens to a SINGLE value per category:

```python
@dataclass
class SemanticTokenState:
    emotion: Optional[str] = None    # ONE emotion, not per-sentence
    style: Optional[str] = None      # ONE style, not per-sentence
    speed: Optional[str] = None
    pitch: Optional[str] = None
    delivery: Optional[str] = None
    ...
```

Meanwhile, `ParsedToken` already tracks `sentence_identity` and
`block_identity`:

```python
@dataclass
class ParsedToken:
    category: str
    tag: str
    offset: int
    raw: str
    placement: str = "sentence_level"
    sentence_identity: int = 0
    block_identity: Optional[str] = None
    source: str = "manual_import"
```

So the parser knows WHICH sentence/block a token belongs to, but the
state object discards that information and keeps only the last value
of each category.

### Impact

- Multiple sentence-scoped token states CANNOT coexist correctly
  inside the same document when using inline `<|...|>` tokens (the
  SemanticTokenState path).
- Example: if the user writes
  `<|emotion:fear|>Sentence one. <|emotion:awe|>Sentence two.`
  the state will have `emotion = "Awe"` (the last value wins) — the
  fear emotion for sentence one is lost.

### Current Mitigation — UI Workflow Removed

The Read Tokens UI workflow (which was the only consumer of
`SemanticTokenState`) has been **REMOVED** from the application:

- The "Read Tokens" button has been removed from the NarrationEditor
  mode bar.
- The `read_tokens_requested` signal has been removed.
- The `_on_read_tokens()` handler has been removed from MainWindow.
- The `self._token_state` instance variable has been removed from
  MainWindow. `token_state=None` is now always passed to the
  compiler.
- All "PROMPT OUT OF SYNC" stale-state checks have been removed.
- All "imported token state" UI text has been removed.

The `SemanticTokenState` and `SemanticTokenParser` classes still
exist in `engine/prompt_state.py` for engine-level API compatibility,
but they are no longer used by any UI workflow.

### No Current User Impact

This limitation does NOT affect any current user workflow:

1. **Managed Mode with Narration Blocks** — per-block overrides are
   stored on each `PromptBlock` object (NOT in `SemanticTokenState`).
   The continuous compiler (`compile_continuous`) resolves each block
   independently. Multiple block-scoped emotions/styles/prosody values
   DO coexist correctly. **Verified by regression test (see below).**

2. **Raw Mode** — the user writes literal tokens in the text. No
   parsing into `SemanticTokenState` occurs. Each token stays at its
   exact position in the text. Multiple sentence-scoped tokens
   coexist correctly by definition.

3. **Managed Mode without blocks** — only global defaults apply. There
   is only one emotion/style/prosody value per generation. No
   coexistence issue.

### Regression Test Performed

The following test verifies that Narration Blocks (the primary
multi-scope workflow) preserves per-block token states correctly:

1. **Block 1 Emotion=Fear, Block 2 Emotion=Awe**:
   - Result: `<|emotion:fear|>Sentence one. <|emotion:awe|>Sentence two.`
   - Both emotions present at correct boundaries ✓

2. **Block 1 Style=Whispering, Block 2 Style=Singing**:
   - Result: `<|style:whispering|>Sentence one. <|style:singing|>Sentence two.`
   - Both styles present at correct boundaries ✓

3. **Block 1 Pitch=Low, Block 2 Pitch=High**:
   - Result: `<|prosody:pitch_low|>Sentence one. <|prosody:pitch_high|>Sentence two.`
   - Both pitches present at correct boundaries ✓

4. **Combined (Fear+Whispering+Low / Awe+Singing+High)**:
   - All 6 tokens present, block boundaries respected ✓

The SemanticTokenState flattening was also tested and confirmed:
parsing `<|emotion:fear|>Sentence one. <|emotion:awe|>Sentence two.`
produces `state.emotion = "Awe"` (last value wins). This is the
documented limitation — but since the UI workflow that used this
path has been removed, there is no user impact.

### Future Refactor (NOT now)

If the Read Tokens workflow is ever reintroduced (which is explicitly
NOT the current UX per ADR 003 §8), `SemanticTokenState` should be
refactored to store per-sentence (or per-block) state instead of a
single flattened value. The `ParsedToken.sentence_identity` and
`block_identity` fields are already in place to support this.

Until then, this is documented as a known architectural limitation
with NO current user impact.

---

## P3.23 DEFECT FIX REGISTER (from the independent security/reliability audit)

The independent audit (SpeechStudio_AUDIT_REPORT.md, verdict: NOT RELEASE
READY) found 54 defects in the pre-P3.21 build. P3.23 re-verified each
headline finding against the P3.22/P3.23 build and fixed the following.
Each fix is covered by runtime tests in `tests/test_p3_23_regression_fixes.py`.

### AUDIT-THR-1 (was CRITICAL): thread affinity — HISTORY_UPDATED
- **Status**: CLOSED (P3.23)
- Engine emits HISTORY_UPDATED on the worker thread; the MainWindow handler
  touched widgets directly. The handler now emits `_history_changed_signal`
  (a Qt signal) — Qt queues cross-thread emissions onto the UI thread.

### AUDIT-UX-1/2: F11 and Esc completely dead ("Ambiguous shortcut overload")
- **Status**: CLOSED (P3.23)
- Both a hidden-menubar QAction AND a standalone QShortcut were registered
  for F11 and Esc — Qt disabled both. The duplicate QShortcuts were removed;
  the QAction shortcuts were promoted to ApplicationShortcut context.
  Runtime test: QTest F11 keyClick now toggles fullscreen (previously dead).

### AUDIT-UX-3: Settings dialog opened twice
- **Status**: CLOSED (P3.23)
- Duplicate `connect("settings", ...)` registration removed (was two slots
  → two dialogs). Runtime test counts exactly one dialog instantiation.

### AUDIT-DATA-1: Settings split brain (cross-instance overwrite)
- **Status**: MITIGATED (P3.23)
- SettingsManager now merges-on-write: it re-reads the disk file and applies
  only its own dirty keys, so a second instance's save no longer reverts
  the first instance's keys (the audit's proven theme-revert scenario).
  RESIDUAL (known, documented): values READ by an instance stay cached
  until that instance next saves; a single shared SettingsManager remains
  the recommended future architecture.

### AUDIT-SEC-1/2: history delete + project delete path traversal
- **Status**: CLOSED (P3.23)
- HistoryManager.delete validates the entry ID (single safe component) and
  containment within the history dir; the audio delete is containment-
  checked against the app root. ProjectManager.delete_project validates the
  project ID and containment within the projects dir (no arbitrary rmtree).

### AUDIT-API-1: Voice import API drift (AttributeError)
- **Status**: CLOSED (P3.23)
- Engine.import_voice_avatar / Engine.set_voice_speaker_metadata now exist
  and are functional (VoiceManager.import_avatar copies the image into the
  profile + records preview_image; set_speaker_metadata persists
  gender/age_range/mood on VoiceProfile). Voice import no longer fails
  whenever an avatar or speaker metadata is provided.

### Still open from the audit (NOT fixed in P3.23 — reported, not ignored):
- **AUDIT-ARCH-1** (LOW/MEDIUM): startup writes settings/build.json/logs
  into the source tree via module-relative APP_ROOT (architectural;
  requires an approved app-data redirection decision).
- **AUDIT-UX-4** (MEDIUM): "Font Status..." unreachable in TopNav Help
  menu (native menubar hidden).
- **AUDIT-UX-5** (MEDIUM): TopNav Export button opens Voice Library export
  (tooltip says "Export Audio").
- **AUDIT-THEME-1** (MEDIUM): Token Guide colors frozen at import.
- Various LOW stale-doc/dead-code items (legacy sidebar, modern_control_panel,
  feature registry F-703, README theme count).

### P3.23 CRITICAL (found + fixed during the Character/Assembly stage):
- **P3.23-CRIT-1**: Combined Audio assembly used Python's `wave` module,
  which CANNOT read the float32 (IEEE format 3) WAV files the Higgs
  pipeline actually writes — assembly failed on every REAL generated file
  (`wave.Error: unknown format: 3`). Existing tests passed only because
  they created PCM16 fixtures with the same `wave` module (fixture
  blindness). Rewritten with soundfile (float32-safe); combined output is
  24 kHz mono float WAV. Runtime tests use REAL float32 fixtures.
- **P3.23-HIGH-1**: Re-detect / Split / Merge silently DROPPED Character
  assignments (explicit design-record §25 violation). Fixed with
  similarity-based transfer + lost_character_id recovery (never silent).
- **P3.23-HIGH-2**: Multi-speaker ($SPEAKER) path ignored block Characters
  — the speaker map could override an explicit Character assignment
  (design-record §19 violation). Fixed: character_id propagates from the
  overlapping block to every SplitPart; the speaker map is only consulted
  when the Character resolves no voice.

---

## P3.24 UX CORRECTION REGISTER (Character + Scene UX)

Three concrete UX problems from the P3.24 correction task, each root-caused
and fixed. Runtime coverage: `tests/test_p3_24_character_scene_ux.py` (57
tests) + E2E golden path (20 checks).

### P3.24-UX-1 (HIGH): Scene dropdown "New Scene" did nothing
- **Status**: CLOSED (P3.24)
- Root cause: `ProjectSceneBar.new_scene_requested` was emitted by the
  menu action but NEVER connected in MainWindow — the signal had zero
  receivers (every other bar signal was connected; this one was missed).
- Fix: connected to the single authoritative `_on_new_scene` handler; the
  handler was also converted to QUICK CREATE (no modal name prompt —
  unique "Untitled Scene" default, next sort_order, immediate activation,
  clean editor state via `_load_scene_state`, silent persistence).
- Also fixed within: the old handler only called `clear_text()`, leaving
  the previous Scene's Narration Blocks in the editor.

### P3.24-UX-2 (HIGH): CharacterManagementDialog NameError on every mutation
- **Status**: CLOSED (P3.24)
- Root cause: `character_dialog.py` used `logger` in
  add/delete/apply handlers without importing it — every Character
  add/delete/update through the dialog raised NameError AFTER mutating
  the Project (state changed, error surfaced).
- Fix: `from engine.logger import logger` import added.

### P3.24-UX-3 (MEDIUM): CHARACTERS panel unusable; Assemble Audio hidden
- **Status**: CLOSED (P3.24)
- The CHARACTERS context was a read-only list whose only creation path
  was the secondary modal dialog; the "+" affordance was an icon-only
  28x28 tool button that read as a dot. "Assemble Audio" was buried in
  the Scene dropdown.
- Fix: the CHARACTERS panel is now a real management surface —
  `_CharacterRow` rows (canonical colour dot + inline rename + Voice
  Profile dropdown + safe delete), "+ Add Character" CTA at the panel
  top, actionable empty state, no-project disabled state, and a proper
  "+ Add" pill in the header. Assemble Audio moved to a prominent
  bottom-of-sidebar action; the Scene dropdown duplicate was removed
  (File → Assemble Scenes... remains as the standard menu path).
- VLM-verified: no clipping/overlap/misalignment in the rendered panel.


## P3.25 AUDIT DEFECT REMEDIATION REGISTER (second independent audit)

Source: SpeechStudio_AUDIT_REPORT.md (54-defect register; verdict at the
time: NOT RELEASE READY). P3.25 re-verified EVERY claim independently
against the P3.24 build (reproduction probes: ss/audit_probes_p325/),
fixed the confirmed defects, and added runtime regression tests
(tests/test_p3_25_audit_remediation.py, 60 tests). Rule applied: no fix
without independent reproduction first; no fix without a runtime test
after. Test-type discipline maintained (runtime vs AST vs E2E labelled).

### CRITICAL

### SS-C01: cross-thread UI mutation on HISTORY_UPDATED
- **Status**: VERIFIED FIXED (P3.23 fix re-verified at runtime in P3.25)
- Root cause: the handler subscribed directly to the EventBus and
  touched widgets from the generation worker thread.
- Fix (P3.23, held): handler only emits `_history_changed_signal`; Qt
  queues cross-thread emissions onto the GUI thread.
- Test: `TestSSC01ThreadAffinity` — HISTORY_UPDATED published on a real
  worker thread; refresh asserted to run on MainThread.

### HIGH — fixed in P3.25

### SS-H01: Scene voice None-state leak + destruction
- **Status**: CLOSED (P3.25)
- Root cause: truthy guard `if scene.voice_profile_id:` in
  `_load_scene_state` skipped the reset; the unconditional save then
  persisted the stale selection into the None-voice Scene.
- Fix: UNCONDITIONAL `set_selected_voice_id(scene.voice_profile_id)`
  (None → "(No voice)") + voice-info cleared to "No voice" when None.
- Test: `TestSSH01SceneVoiceNone` (3 tests) + A→B→A matrix in
  `TestSceneStateIntegrity`.

### SS-H02: block override clobbered the scene-global value
- **Status**: CLOSED (P3.25)
- Root cause: `_on_emotion_changed` (and style/speed/pitch/delivery)
  wrote BOTH the global field and the block override.
- Fix: with a block selected, the handler writes ONLY the block
  override; the global is written exclusively when no block is selected
  (matches the documented design in `_on_block_selected`).
- Test: `TestSSH02BlockOverrideScope` (3 tests incl. all five controls).

### SS-H03: Scene.parameters None contamination
- **Status**: CLOSED (P3.25)
- Root cause: truthy guard `if scene.parameters:` — same anti-pattern
  as SS-H01 on the parameters path.
- Fix: UNCONDITIONAL `set_parameters(scene.parameters or
  GenerationParameters())` — None resets the panel to factory defaults.
- Test: `TestSSH03SceneParamsNone` (2 tests).

### SS-H04: settings.json split-brain (3 instances + raw 4th writer)
- **Status**: CLOSED (P3.25)
- Root cause: Engine/MainWindow/entry point held private
  SettingsManager instances with divergent caches; the batch dialog
  wrote settings.json via a raw read-modify-write; writes were
  non-atomic; MainWindow's settings dir was hardwired to the module
  path (different file than the Engine's whenever app_root differed).
- Fix: (a) `SettingsManager.instance()` — ONE shared owner per file per
  process, used by Engine, MainWindow and the entry point; (b)
  MainWindow derives its settings dir from the ENGINE's app root; (c)
  reload-on-read via (inode, mtime_ns, size) — stale reads eliminated
  including same-timestamp rewrites (atomic replace changes the inode);
  (d) atomic tmp+os.replace writes; (e) batch dialog writes through the
  shared manager (raw writer removed).
- Test: `TestSSH04SettingsArchitecture` (6 tests incl. real Engine+
  MainWindow sharing one object and a full restart cycle).

### SS-H05: Raw Mode preview differed from model input
- **Status**: CLOSED (P3.25)
- Root cause: `_update_preview` never checked `_raw_mode` — the Preview
  tab showed the compiled prompt (with injected tokens) while the model
  received the literal raw text.
- Fix: preview renders the literal editor text verbatim when Raw Mode
  is active (ADR 003 §6/§8: preview == model input).
- Test: `TestSSH05RawModePreview` (2 tests).

### SS-H06 (+SS-M01/M02): Allow SFX no-op end-to-end
- **Status**: CLOSED (P3.25)
- Root cause: GenerationSection.get_parameters() rebuilt
  GenerationParameters WITHOUT allow_sfx (always default True), so the
  Friendly toggle never reached the prompt compiler; the toggle also
  never emitted parameters_changed (no sync), and the advanced→friendly
  sync reset it; Generate Long dropped the flag in job_params and the
  dialog part-rebuild.
- Fix: GenerationSection owns and round-trips allow_sfx; the toggle
  emits parameters_changed (friendly→advanced sync chain); job_params
  forwards base_params.allow_sfx; LongNarrationDialog accepts and
  applies allow_sfx on part rebuild.
- Test: `TestSSH06AllowSfxEndToEnd` (6 tests: panel params, prompt
  content ON/OFF, tab-switch survival, job params, dialog rebuild).

### SS-H07: invalid semantic values — garbage token OR silent fallback
- **Status**: CLOSED (P3.25)
- Root cause: two divergent policies — block overrides fell through to
  `value.lower()` in `_resolve_emission_tag` (literal garbage token to
  the model) while the global path swallowed InvalidPrompt into a
  tokenless prompt; the UI discarded the compiler's warnings.
- Fix: ONE deterministic policy everywhere — unknown values are
  REJECTED (no token emitted) and a warning is recorded; the
  MainWindow surfaces compile warnings (Preview panel + status bar)
  via `_last_prompt_warnings`.
- Test: `TestSSH07InvalidValues` (4 tests incl. invalid matrix and
  runtime warning surfacing through the real MainWindow).

### SS-H08 (+SS-M08): path traversal / import-export exfiltration
- **Status**: CLOSED (P3.25)
- Root cause: exporter joined model-supplied output_paths without
  containment (absolute paths discarded app_root via os.path.join);
  import kept traversal paths verbatim; hostile names crashed the
  exporter (null bytes, 300-char names, Windows reserved names).
- Fix: `_contained_source_path()` containment (rejects absolute,
  traversal, dot-segments, backslash, null-byte paths; realpath must
  stay inside app_root) applied to ALL export copy sites (scene audio,
  voice references, combined WAV+MP3); import SANITIZES escaping paths
  (stripped + logged); `_safe_dirname` hardened (null bytes, 80-char
  cap, Windows reserved names); export dialog catches unexpected
  exceptions.
- Test: `TestSSH08PathTraversal` (4 tests: crafted import → re-export
  exfiltration attempt, containment matrix, direct in-memory export,
  history-delete containment re-check) + M08 hostile-names test.

### SS-H09 (+SS-L14): corrupt settings.json bricked startup
- **Status**: CLOSED (P3.25)
- Root cause: `_load` caught only JSONDecodeError/OSError — wrong root
  type (AttributeError in _deep_merge) and UnicodeDecodeError crashed
  Engine construction; `get()` raised on non-dict sections.
- Fix: broad per-load catch + isinstance(dict) validation + corrupt
  file QUARANTINED (renamed .corrupt-<ts>, app self-heals on next
  save); `get()` returns the default for non-dict sections.
- Test: `TestSSH09CorruptSettings` (4 tests incl. real Engine boot with
  a corrupt file).

### SS-H10: one corrupt history file poisoned all history
- **Status**: CLOSED (P3.25)
- Root cause: narrow excepts in list/get entry; HistoryEntry was a raw
  dict wrapper — KeyError/TypeError reached the History dialog.
- Fix: broad per-entry catch (+ dict validation) in list_entries/
  get_entry; HistoryEntry properties validate types and coerce
  (output_duration via _as_float, voice_profile degrades to None);
  real HistoryViewDialog construction test with malformed entries.
- Test: `TestSSH10HistoryRobustness` (3 tests).

### SS-H11: voice import avatar/metadata AttributeError
- **Status**: VERIFIED FIXED (P3.23 fix re-verified at runtime in P3.25)
- Test: `TestSSH11VoiceImport` (avatar + metadata + duplicate import +
  re-import idempotence).

### SS-H12/SS-H13: dead F11 / dead Esc
- **Status**: SS-H12 VERIFIED FIXED (F11 both directions, QTest).
  SS-H13 CLOSED (P3.25): the P3.23 fix had kept Esc as an
  ApplicationShortcut QAction — that intercepted EVERY Esc app-wide, so
  Esc still could not exit fullscreen or close the search bar.
- Fix: Esc removed from the Stop QAction; ONE authoritative handler in
  MainWindow.keyPressEvent with an explicit priority chain:
  stop generation > exit fullscreen > close search bar.
- Test: `TestSSH12H13Shortcuts` (5 tests: F11 both ways, Esc exits
  fullscreen, Esc closes search, Esc stops generation, no ambiguous
  registrations).

### SS-H14: Settings dialog opened twice
- **Status**: VERIFIED FIXED (re-verified incl. rapid triple trigger:
  exactly 3 dialogs for 3 triggers).
- Test: `TestSSH14SettingsDialog`.

### SS-H15: silent except-cluster in scene save/restore
- **Status**: CLOSED (P3.25)
- Root cause: five `except: pass` blocks around the scene state sync —
  one failing setter skipped the remaining restores (partial-restore
  leak) and block-persistence failures vanished silently.
- Fix: per-field narrow handlers with logged warnings (block-state
  save failures at ERROR level); restores no longer skip.
- Test: `TestSSH15SceneRestoreFaults` (fault injection both directions).

### MEDIUM — fixed in P3.25

| ID | Root cause → Fix | Test |
|---|---|---|
| SS-M01 | advanced→friendly sync reset the SFX toggle → toggle participates in the sync chain | TestSSH06 (tab-switch survival) |
| SS-M02 | job_params + dialog rebuild dropped allow_sfx → forwarded | TestSSH06 (2 tests) |
| SS-M03 | `if params.seed is not None` left stale seed → else-clear | test_m03 |
| SS-M05 | second-resolution ids collided → millisecond suffix + de-dup (history AND audio filenames) | test_m05 (2 tests) |
| SS-M06 | corrupt voice profile crashed listing → broad per-profile catch | test_m06 |
| SS-M07 | import_project_dir crashed on malformed project.json → wrapped + rejected | test_m07 |
| SS-M08 | hostile names crashed export → sanitizer hardening + dialog catch | test_m08 |
| SS-M09 | scene deletion not persisted → save_project after delete (failure surfaces a warning) | test_m09 |
| SS-M10 | CWD-relative isfile in History view → _resolve_history_path against app root | test_m10 |
| SS-M11 | Font Status unreachable → added to TopNav Help menu | (menu structure; runtime menu walks remain in P3.23 probes) |
| SS-M12 | "Export Audio" mislabel → "Export Voice" | (label) |
| SS-M13 | Token Guide colors frozen at import → HTML built at dialog open | (dialog opens with current Palette) |
| SS-M14 | QTimer.singleShot(0) from worker threads never fired → `_model_status_signal` marshalling | test_m14 (GUI-thread proof) |
| SS-M18 | from_dict defaults diverged from dataclass → single default source | test_m18 |
| SS-M20 | stale verify_transport_gl.py (180/183 red) → RETIRED with honest stub | tool exit 0, no claims |

### Bonus defect found and fixed during P3.25 (E2E)

- **PATH-1**: `GenerationManager._load_reference_audio` resolved
  reference paths against the MODULE directory, not the Engine's app
  root — with Engine(app_root=<dir>) a valid imported reference was
  reported "missing from disk" and blocked generation. Fixed to resolve
  via the AudioManager's app root. Test:
  `test_generation_reference_audio_resolves_against_engine_app_root`.
- **DATA-1** (audit §6.4 bonus): the Engine failure path stored the
  WORKER THREAD NAME as the result timestamp (flowed into history
  ids/recents). Fixed to a real timestamp.
- **SS-H04-extra**: MainWindow hardwired its settings dir to the module
  path — the Engine and MainWindow held DIFFERENT settings files
  whenever app_root differed (audit probe_c3 scenario). Fixed: settings
  dir derived from the Engine's app root (verified: same object).

### Still open (documented, not fixed in P3.25)

- **AUDIT-ARCH-1** (MEDIUM, architectural): startup writes
  settings/build.json/logs via module-relative APP_ROOT in several
  UI/engine components (documented since P3.23; requires an approved
  app-data redirection decision). Partially mitigated: settings,
  reference-audio resolution and history paths now follow the ENGINE's
  app root consistently.
- **SS-M15** (MEDIUM, architectural): engine/batch_manager.py lazily
  imports PySide6 (Qt-free rule breach) for retry delays; requires a
  scheduling abstraction injection.
- **SS-M16/SS-M17** (MEDIUM, feature-adjacent): Character
  voice_profile_id is not used for multi-speaker pre-selection;
  speaker_voice_map is a global (cross-project) setting. Both are
  integration gaps, not regressions; deferred pending a product
  decision (no feature creep in a stabilization pass).
- **SS-M19** (MEDIUM): threading invariants (GENERATION_STARTED on the
  submitter thread; BatchManager.marshal_to_ui default) are documented
  in code comments but not enforced.
- **SS-L02…SS-L18** (LOW): stale docs / dead code / cosmetic items —
  unchanged (LOW priority per the remediation order).
- Dead code (~7 files, ~5,400 lines: sidebar.py, modern_control_panel.py,
  components.py, prompt_editor.py, ambient_*, transport_gl.py) — removal
  is a separate, reviewable change (not bundled into defect fixes).

---

## P3.25 ADDENDUM: Model Load Dialog white/blank window (user-reported)

Reported after the P3.25 build on the target Windows machine: the
"Loading Model - SpeechStudio" dialog appeared as a **white,
content-free window** (only the OS-drawn title bar was visible) during
model loading.

### MLD-1 (HIGH, user-reported) — loading dialog never painted

- **Root cause (runtime-reproduced)**: `MainWindow._load_model` /
  `_load_model_and_generate` submitted the engine load job BEFORE
  showing the dialog. The worker thread immediately began `import
  torch`, which holds the GIL in long C-extension stretches on Windows,
  so the GUI thread's event loop could not process the freshly-shown
  dialog's expose/paint events for many seconds. A mapped-but-unpainted
  top-level window is filled by the OS with the default WHITE window
  brush. Reproduced headlessly with
  `ss/audit_probes_p325/probe_paint_timing.py`: OLD flow = first paint
  **1.99 s after show()**; fixed flow = **0.00 s**.
- **Fix**: `ModelLoadDialog.start()` now paints synchronously
  (`processEvents(ExcludeUserInputEvents)` + `repaint()` — the standard
  splash-screen pattern) while the GUI thread still owns the GIL, and
  MainWindow shows+paints the dialog BEFORE submitting the load job
  (submission failure closes the dialog again and re-raises).
- **Tests**: `tests/test_p3_25_model_load_dialog.py` — dialog painted
  before submission (both load paths), painted with ZERO event-loop
  processing under an active GIL-hogging worker, submission-failure
  closes the dialog, dark render with content, explicit background.

### MLD-2 (MEDIUM) — dialog relied on the transparent QDialog QSS

- **Root cause**: the app-wide QSS styles `QDialog { background-color:
  transparent; }` (the main window's dark look comes from ambient
  background layers — a separate top-level dialog has no such
  backdrop); the dialog set no background of its own, leaving the body
  to platform-dependent fill behaviour.
- **Fix**: explicit dark surface on the dialog (the visually verified
  AssembleDialog pattern: `background-color: Palette.BG_BASE`).

### MLD-3 (MEDIUM) — Cancel button label invisible

- **Root cause**: `setFixedSize(400, 180)` was too short for the
  content under the theme QSS paddings — the layout squeezed the Cancel
  button to a 15 px height, and the 7 px vertical QSS padding left zero
  pixels for the label (verified: button QRect(163,141,74,15), 0 text
  pixels in the render).
- **Fix**: `setFixedSize(400, 220)` — every item now lays out at its
  size hint (button 74×32, "Cancel" renders; VLM-verified full dialog
  render: title, status, progress bar, elapsed timer, Cancel all
  visible on the dark surface).

Status: all three **FIXED**; 656/656 tests, 5/5 verification scripts,
E2E golden path 30/30.

---

## P3.26 register — Scene switching + Narration Block state integrity
(user-reported defects; all REPRODUCED before fixing — see
`ss/audit_probes_p326/probe_repro.py` + `probe_emotion_scope.py`)

### P3.26-D1 (CRITICAL, user workflow Part 3) — Scene switch state leak

- **Symptom**: A (long text, Plain) -> B (short text, 3 Characters,
  Blocks) -> A: A returned in BLOCKS mode with B's block visuals painted
  over A's text and B's block/Character elements below the text;
  Re-detect was required to repair the display.
- **Root causes** (4, all confirmed at runtime):
  1. The editor mode (Plain/Blocks/Preview) was widget-global — never
     persisted or restored per Scene.
  2. The no-blocks restore path (`block_manager.clear()`) never
     re-rendered the editor: the previous Scene's blocks stayed in the
     inner editor as extraSelections + gutter data.
  3. `set_text` during the switch fed the text swap into
     `NarrationBlockManager.on_text_changed` while the PREVIOUS Scene's
     blocks were still loaded — they were offset-shifted as if the user
     had edited the text.
  4. `_selected_block_id` (editor) and `_active_block_id` (control
     panel) were never explicitly reset — clearing happened only via
     cursor-signal side effects (non-deterministic).
- **Fix**: `Scene.editor_mode` + `Scene.selected_block_id` (additive,
  deterministic defaults); atomic Scene restore
  (`begin_scene_load`/`set_scene_blocks`/`set_editor_mode` — text swap
  never offset-shifts, zero-blocks case fully re-renders); scope reset
  FIRST in `_load_scene_state`; mode + selection restore through the
  real `_set_mode`/`block_selected` paths; fresh Scenes inherit the
  current mode (creation), existing Scenes always restore their own.
- **Tests**: `tests/test_p3_26_scene_state_integrity.py` (29 runtime
  tests: mode restoration both directions, Character isolation UI+model,
  block/character widget cleanup, Re-detect-not-required, persistence
  round-trip, contamination matrix); E2E `ss/e2e/e2e_p326.py` 34/34.

### P3.26-D2 (HIGH, user workflow Part 9) — B1 not selectable on first click

- **Symptom**: in Narration Blocks, B1 could not be selected on the
  first click; B2 had to be selected first, then B1 worked.
- **Root cause**: block selection was driven ONLY by
  `cursorPositionChanged`. The gutter click calls
  `setTextCursor(block.start_offset)`; when the cursor already sits at
  that offset (the default state after a Scene load / `setPlainText` /
  auto-detect — offset 0), Qt emits NO signal, so the selection handler
  never ran. The same applies to text-area clicks that don't move the
  cursor.
- **Fix**: `block_click_requested` signal — both the gutter click and a
  new `mousePressEvent` override on `BlockAwarePlainTextEdit` emit the
  clicked offset; `NarrationEditor._on_block_click_requested` drives the
  selection explicitly (idempotent — re-clicking the selected block
  still refreshes the properties panel, so every click has visible
  feedback).
- **Tests**: runtime QTest gutter clicks (first click, B1/B2/B3/B1
  sequence, text-area click, immediately after Scene switch).

### P3.26-D3 (HIGH, user workflow Part 11) — Advanced global Emotion dead

- **Symptom**: setting a global emotion (Enthusiasm) in Advanced did
  nothing — no effective state, no token in the preview; the same in
  Plain Text mode.
- **Root cause**: the control panel's `_active_block_id` (block scope)
  went STALE via four paths — switching to Plain/Preview mode, Re-detect
  (all block ids replaced), deleting the selected block, and Scene
  switches (non-deterministic cursor-signal clearing). Semantic control
  changes were routed into `_update_block_override`: with a live-but-
  hidden block (Plain mode) the value was written into the hidden block
  override while the user believed they set the global; with a dead id
  the change was SILENTLY DROPPED (no state, no preview token, no
  warning).
- **Fix**: `_block_scope_valid_id()` validates the scope before any
  semantic change (must be BLOCKS mode + live block id; stale scopes are
  cleared and the change lands on the GLOBAL); the editor now also emits
  `block_selected` on every selection-changing path (mode switch,
  Re-detect via `_post_analyze`, delete, merge, create) so the scope
  always follows reality.
- **Tests**: the four stale-scope scenarios + six-emotion matrix +
  preview + real-GenerationRequest propagation (captured from
  `_start_generation`).

### P3.26-UX1 / UX2 (UX fine tune, Parts 1-2) — sidebar

- **UX1**: the Recents header action is now an icon-only 28×28
  QToolButton (22 px clearly visible plus icon; hover: accent border +
  lifted background; pressed: deeper background; context-aware
  dispatcher + per-context tooltips unchanged). Supersedes the P3.24
  "+ Add" pill (icon+text was redundant).
- **UX2**: the shared Recents list (Recent Projects / Scenes /
  Characters / Audio) is a visually distinct small surface — #202024
  background vs. the #1A1A1A sidebar, 8 px rounded corners, 1px #323236
  border, 6 px internal padding. Data model and row widgets unchanged.

Status: all three functional defects **FIXED**; both UX refinements
implemented. 686/686 tests, 5/5 verification scripts, E2Es: P3.26
34/34, P3.25 30/30, P3.24 20/20. VLM-verified renders of all four
Recents contexts + the full Narration Blocks editor (no clipping,
overlap, or duplicate elements).

---

## P3.27B register — Long Generation outlier / excessive trailing silence
(user-reported runtime defect; REPRODUCED before fixing — see
`ss/audit_probes_p327b/probe_outlier_repro.py` for the pre-fix record and
`probe_outlier_fixed.py` for the post-fix verification)

### P3.27B-D1 (CRITICAL, user report §1) — Regenerated part becomes a 163.56 s outlier with a ~133 s silent tail

- **Symptom**: regenerating one part of a Long Generation produced a
  163.56 s WAV (baseline for the same Part 09 / same prompt: 30.48 s,
  743,520 samples). The speech content was intact; after it, a very
  long near-silent tail, then EOF. Not the Append Silence feature
  (0.5 s), not an inter-part gap.
- **Root cause** (model-side non-termination, proven at runtime):
  Higgs Audio V3 generates audio autoregressively at 25 fps (40 ms
  per frame; research/bosonai_higgs-tts-v3-4b_README.md: "8 codebooks
  at 25 fps", "Frame rate: 25 fps (40 ms / frame)"). Generation stops
  on the model's end-of-speech signal OR at max_new_tokens. In the bad
  run the model finished the speech but failed to terminate and kept
  emitting (silent) frames until the 4096-token budget was exhausted:
  4096 / 25 = 163.84 s ceiling; observed 163.56 s = 4089 frames =
  99.83 % of the ceiling (good run: 30.48 s = 762 tokens). Pre-fix
  probe proved prompt hash, temperature/top_p/top_k/max_tokens, voice
  and ALL model kwargs were IDENTICAL between the good and bad runs —
  the first difference is the raw waveform returned by the model
  (731,520 vs 3,925,440 samples). The silence exists in the RAW model
  output (measured before append_silence / normalisation); the app
  pipeline was correct (append_silence adds exactly 0.5 s;
  normalisation is gain-only; save_wav writes a fresh truncating
  file). App-side failures were consequential: no detection (the
  pathological output was saved as a normal success with no warning)
  and regen-overwrite-in-place destroyed the previous good version.
- **Fix** (detection + preservation — NO trimming, NO blind
  max_tokens reduction, per task §21):
  1. `engine/output_guard.py` (new pure module): conservative anomaly
     detector on the RAW waveform — R1 runaway trailing silence
     (>= 10 s AND >= 50 % of the file), R2 expected-duration multiple
     (>= 3x the splitter's text estimate AND >= expected + 20 s),
     R3 fully silent output. Nothing is ever trimmed or discarded;
     natural pauses / breathing / dramatic timing are far above the
     -50 dBFS frame threshold and never flagged.
  2. `GenerationManager.generate` runs the guard between the model
     return and post-processing; the verdict rides the
     GenerationResult (`output_anomaly` + a human-readable warning).
     A guard failure can never fail the generation.
  3. Versioned Generate Long part filenames
     (`<Proj>_<Scene>_Long_vNN_Part_NNN[_<speaker>][_<scene8>].wav`,
     the same single mechanism planned by the P3.27 design record
     Rec 2/6 — not a second convention) with a disk-guarded allocator:
     every regeneration allocates the next free version, so a bad
     regen can never overwrite the previous good file. Manual batch
     jobs keep the legacy overwrite contract.
  4. Batch dialog: Duration cell shows `163.84s ⚠` + tooltip with the
     measured speech/silence breakdown and an explicit "the audio was
     kept — keep / inspect / regenerate" statement (task §17).
  5. History: entries now carry part_index, part_version,
     output_anomaly + the warning (task §19) — a pathological
     generation stays fully traceable.
- **Tests**: `tests/test_p3_27b_long_generation_outlier.py` (30 tests:
  synthetic pathological WAVs, false-positive matrix incl. legitimate
  120 s speech + 3 s dramatic pause, token-budget math, version
  allocation/disk-guard, regen isolation, fresh-output, failure/cancel/
  retry, dialog display, history traceability); E2E
  `ss/e2e/e2e_p327b.py` 22/22 through the real MainWindow path.

### P3.27B-E1 (MEDIUM, discovered during regression) — Order-dependent test hang: TestSSH06 poisons TestSSH12H13Shortcuts

- **Status**: OPEN (pre-existing on the frozen P3.26 baseline — verified
  via git-stash: `pytest tests/test_p3_25_audit_remediation.py` hangs at
  the same point WITHOUT any P3.27B change; NOT a P3.27B regression).
- **Symptom**: running the whole P3.25 test file in one process hangs
  after ~40 tests, at `TestSSH12H13Shortcuts::test_esc_closes_search_bar`
  (the class passes in isolation and after every other class).
- **Root cause**: `TestSSH06AllowSfxEndToEnd::test_generate_long_job_params_forward_allow_sfx`
  calls the REAL `MainWindow._start_long_narration`, which creates the
  batch dialog and starts a real engine generation with no model
  loaded; the resulting async GENERATION_FAILED marshals to the UI
  thread and opens a modal `QMessageBox.critical` that nothing answers
  in offscreen mode, deadlocking subsequent UI-event tests.
- **Workaround (no test modified/skipped)**: run the suite with
  `--deselect tests/test_p3_25_audit_remediation.py::TestSSH12H13Shortcuts`
  and execute that class in a separate pytest invocation.
- **P3.29 observation (2025-08-28)**: running the FILE ALONE with that
  deselect can still hang at `TestSSH14SettingsDialog` (~test 41 of 55)
  — the same SSH06 modal poisoning, different victim when the file runs
  without the rest of the suite (verified IDENTICAL on the stashed
  pre-P3.29 code, so NOT a P3.29 regression; the full-suite invocation
  with the deselect completes cleanly — that is the regression-gated
  invocation). Proper fix unchanged (stub SSH06's batch submission).
- **Proper fix (future pass)**: SSH06 should stub the batch submission
  (e.g. stub `BatchManager.start` or the submit_fn) instead of running
  the real generation pipeline.

## P3.28 register — Scene Audio Provenance + Batch Generation State + Export

### P3.28 defects fixed (design record D-1…D-8 + P3.27B carry-forward)

- **D-1 (Generate Long re-run silently destroyed previous audio)**:
  CLOSED — per-slot monotonic version allocation (asset-aware max+1 +
  disk guard, engine/audio_provenance.next_slot_version) + save_wav
  no-silent-overwrite guard (engine/audio_manager.py).
- **D-2 (Scene status flap: complete on first part / error on any
  failure / never generating on batch path)**: CLOSED — the coverage
  state is DERIVED (engine/audio_provenance.scene_coverage_state) and
  recomputed on batch start (GENERATING), job completion, batch
  completion, scene load and project save.
- **D-3 (results appended to _active_scene unconditionally)**: CLOSED —
  the ONE WRITER engine/audio_provenance.register_generation_result
  resolves the Scene by result.scene_id.
- **D-4 (ISO vs compact timestamp lexicographic compare — always
  stale)**: CLOSED — parse_any_timestamp compares parsed datetimes;
  Scene combined staleness is version-lineage based (no timestamps).
- **D-5 (SplitPart.source_block_id dropped at BatchJob build)**: CLOSED
  — BatchJob carries source_block_id + slot_id; the asset is tagged.
- **D-6 (fixed-name unversioned long-mix concat, no lineage)**: CLOSED
  for the Scene path — Combine Scene writes versioned, lineage-tracked
  Scene.combined_outputs entries. The legacy Concatenate button
  (dialogue/stems workflow) intentionally keeps the old behaviour per
  the design record ("out of scope and untouched").
- **D-7 (doc/code drift)**: PARTIALLY CLOSED — long dialog silence text
  and P3.27B docstrings corrected; the regen docstring updated in
  P3.27B. Remaining drift items are cosmetic.
- **D-8 (save_wav explicit filename path escape)**: CLOSED — basename +
  separator strip + realpath containment check.

### P3.28 known behaviours / residual items

- **P3.28-R1 (LOW, accepted)**: the per-part failure modal
  (QMessageBox.critical on every failed batch part) is the pre-existing
  UX; the design record Rec 9 recommends collapsing it into the failed
  rows + a single end-of-batch notice. Deferred (behaviour change beyond
  this stage's mandate).
- **P3.28-R2 (LOW, accepted)**: `Scene.status` values "not_generated" /
  "partial" are new; older sidebar consumers that string-match "draft" /
  "complete" still work (the badge map is additive; unknown values
  render no badge).
- **P3.28-R3 (INFO)**: a full-scene single-Generate asset (no
  part_index) does not count toward slot coverage once a scene has
  materialised slots (correct per the design: it remains available
  through resolution rule 3 "Latest audio").
- **P3.27B-E1** remains OPEN (pre-existing, unchanged — see above).

## P3.29 register — "Loading Model" dialog freeze (GUI-thread block during model load)

### P3.29 defects fixed

- **P3.29-D1 (frozen "Loading Model" dialog / "(Not Responding)" for the
  whole model load, user-reported post-P3.28 with a Windows screenshot)**:
  CLOSED — ModelManager.load() held ONE RLock for the ENTIRE load while
  emitting ENGINE_STATUS_CHANGED at load start; the GUI thread's
  _refresh_model_status() blocked on that lock inside status() for the
  whole load (no timer ticks, no paints, no input; the dialog vanished
  the moment the load finished). Fix: split lock scopes —
  ``_state_lock`` (RLock, microsecond state snapshot for
  status()/is_loaded/device/get_model_and_tokenizer) vs ``_load_lock``
  (serializes whole load()/unload()/warmup(); never taken by readers).
  Reproduced by ss/audit_probes_p329/probe_model_load_freeze.py on the
  unfixed build (0 watchdog ticks in a 3.4 s simulated load, max single
  processEvents() block 3.39 s); post-fix 7/7 with the GUI alive for the
  whole load.
- **P3.29-D2 (status() imported torch on the GUI thread)**: CLOSED —
  while the worker was mid-``import torch``, the per-module import lock
  would block the GUI thread's ``status()`` for the whole import.
  status() now only queries an ALREADY-imported torch (sys.modules),
  fully exception-guarded (a partially-initialised torch during the
  worker's import is an expected transient). unload() received the same
  guard.
- **P3.29-D3 (no load feedback: status stuck at "Initializing...",
  pre-existing)**: CLOSED — load phases are now published on the
  additive ``ModelStatus.phase`` ("Preparing...", "Loading
  tokenizer...", "Loading model weights...", "Moving model to
  CUDA/CPU...") and the ModelLoadDialog polls them (status_fn, 500 ms,
  non-blocking by D-1's guarantee). The elapsed timer + phase text make
  the dialog visibly alive during 10-60 s loads.
- **P3.29-D4 (stale deferred dialog-close killed the NEXT load's
  dialog, pre-existing — found by ss/e2e/e2e_p329.py S7)**: CLOSED —
  after cancelling a load (dialog closed, background load continues),
  finishing a NEW load, then starting ANOTHER one, the cancelled load's
  deferred ``QTimer.singleShot(0)`` close ran inside the new dialog's
  ``start()`` event pump and closed the NEW dialog (load continued
  invisibly; the generate-after-load chain never fired). The deferred
  close is now IDENTITY-GUARDED: it closes only the dialog instance it
  was scheduled for (``current is not dlg`` => stale, logged, ignored).

### P3.29 known behaviours / residual items

- **P3.29-R1 (INFO, inherent)**: Windows DLL loads during
  ``import torch`` hold the GIL in long C stretches — the GUI thread may
  still exhibit short stutters during those stretches. In-process this
  cannot be fully eliminated (CPython + torch); the P3.25
  synchronous-first-paint + the P3.29 alive event loop together keep the
  dialog content visible and the app responsive around them.
- **P3.29-R2 (documented test supersession)**:
  test_p3_25_audit_remediation.TestSSC01ThreadAffinity asserted an
  EXACT single refresh call, racing MainWindow's startup
  ``QTimer.singleShot(100, _refresh_sidebar)`` (>=100 ms between
  construction and the test's processEvents window =>
  ['MainThread','MainThread'] — verified IDENTICAL on the pre-P3.29
  code via git-stash, so a pre-existing flaky assertion, not a
  regression). Superseded to the test's actual invariant: every
  observed refresh ran on the GUI thread (set equality).
- **P3.27B-E1** remains OPEN; the register entry now also documents
  that the FILE-ALONE invocation can hang at TestSSH14 (~test 41/55)
  with the same SSH06 modal poisoning (pre-existing — verified
  identical on stashed pre-P3.29 code; the full-suite invocation with
  the documented deselect completes cleanly).

---

## P3.30 — USER BUG-BATCH REGISTER (7 reported items)

Date: 2026 (P3.30 hibajavítási szakasz). All seven user-reported items
reproduced (ss/audit_probes_p330/probe_repro_all.py, 15/15 defect
demonstrations on the pre-fix build) and CLOSED with runtime regression
tests (tests/test_p3_30_ux_batch_fixes.py, 20 tests) + E2E
(ss/e2e/e2e_p330.py, 15/15).

- **P3.30-B1 (a — Plain Text mode showed narration blocks)**: CLOSED —
  two causes, both fixed in narration_editor.py. (1) A scene restore
  repopulated the editor's VISUAL block mirror
  (``set_scene_blocks`` → ``_render_block_visuals`` fed the gutter
  unconditionally) while the mode stayed Plain; (2) the per-Scene mode
  restore drove only the radio buttons — a NO-OP when the target radio
  was already checked (no toggled signal → ``_set_mode`` never ran →
  nothing cleared the visuals). Fix: the editor block mirror is fed
  ONLY in Narration Blocks mode (cleared otherwise), and
  ``set_editor_mode`` calls ``_set_mode`` directly (idempotent) before
  syncing the radios.
- **P3.30-B2 (b — Export Audio menu opened frameless at screen
  top-left)**: CLOSED — ``QMenu.exec()`` with no position opens at the
  cursor, which repeatedly resolved to the screen origin on Windows.
  New ``_popup_menu_above(menu, btn)`` helper: menu anchored ABOVE the
  EXPORT AUDIO button (sidebar has ample space), falling back below at
  the screen edge, horizontally clamped.
- **P3.30-B3 (c — same misplacement for the audio version-selection
  menu)**: CLOSED — the lazy "· N versions ▾" popover and the assembly
  "Choose…" picker use the same anchored helper (button captured in the
  click closure).
- **P3.30-B4 (d — STOP ineffective in BATCH GENERATION; generation
  could get stuck)**: CLOSED — root causes found by audit (model docs
  reviewed per the user's request):
  (1) ``WorkerPool.cancel_generation()`` relied on
  ``Future.cancel()`` — a NO-OP once the callable started running, so a
  stop never prevented ANY remaining work. (2) Two stuck-forever paths:
  ``_on_job_done`` early-returned when ``_current_index`` was None
  (batch left "running" eternally), and ``to_request()``/non-RuntimeError
  submit exceptions escaped ``_process_next`` (job stuck GENERATING).
  Fix: cooperative cancel Event (checked at pipeline entry + immediately
  BEFORE the model call; a NEW submission clears it — no stale-cancel
  leak), both stuck paths contained (orphan completion finishes the
  batch; ANY submit failure marks FAILED and the queue continues), the
  GENERATING row flips to SKIPPED instantly on Stop (restored to
  COMPLETED if the in-flight call finishes with audio — a stop never
  destroys completed work), a cancelled result renders as SKIPPED (user
  stop, not a red error), and the Stop button shows "Stopping…" while
  the queue runs out.
  MODEL-DOC ANSWER (research/*.md reviewed): Higgs Audio V3
  ``generate_speech()`` (original + transformers port) exposes NO
  stopping criteria, NO streamer, NO cancellation parameter — it is one
  blocking native call. Mid-call interruption is therefore impossible
  in-process; the checkpoint BEFORE the call is the earliest and
  cheapest cancel point (verified in tests: model never invoked).
- **P3.30-B5 (e — new buttons too small / unreadable)**: CLOSED —
  "Use as output" and assembly "Choose…" 22px fixed → 28px min height
  with 4/10px padding; the "· N versions ▾" popover button got 3/8px
  padding + 12px font (was zero-padding); "× Clear explicit selection"
  got padding + explicit font size.
- **P3.30-B6 (f — friendlier stale warning copy)**: CLOSED — new
  ``stale_audio_use_anyway_dialog`` with the user-approved copy
  verbatim ("This Scene Audio is out of date." / per-block "B1 has a
  newer version: v02" / "This Combined Audio was created from older
  versions." / "Do you want to use the older audio anyway?" with
  **Cancel** (default) and **Use Anyway** buttons). Used at BOTH stale
  sites (Batch "Use as output" + Assembly "Choose…"). Reason wording in
  ``is_scene_combined_stale`` changed to "B1 has a newer version: v02"
  (colon form).
- **P3.30-B7 (g — stale/resolution behavior audited: "furcsán
  viselkedik")**: CLOSED — audit found the P3.28 §15 "correction" had a
  HOLE: when a Scene's combined outputs existed but were ALL stale,
  automatic resolution fell through Rule 2 to Rule 3 — whose
  ``audio_assets[-1]`` for block Scenes is ONE PART's WAV — so Project
  Assembly would silently consume a single part as the WHOLE Scene's
  audio (worse than the stale audio it was avoiding, and itself a
  silent assembly). Fix: such Scenes now REQUIRE REVIEW (re-combine in
  one click, or explicitly choose the stale output via the friendly
  Use-Anyway dialog). Rule 3 remains exactly as designed for LEGACY
  scenes with no combined outputs. Documented supersession:
  test_p3_28 test_stale_combined_skipped_falls_to_asset →
  test_stale_combined_all_stale_requires_review (same file, combine
  E2E assertion updated to requires_review).

### P3.30 known behaviours / residual items

- **P3.30-R1 (INFO, inherent)**: a STOP that lands while the model is
  inside ``generate_speech()`` waits for that call to finish (its audio
  is kept and the row restored to COMPLETED). This is the documented
  model-API limitation; the "Stopping…" button state communicates it.
- **P3.30-R2 (design note)**: a deliberately selected OLDER per-slot
  version that feeds a Combine still yields a combined output flagged
  stale ("…created from older versions") — kept BY DESIGN: newer
  versions genuinely exist, the badge + Use-Anyway dialog tell the
  truth, and the user's approved copy describes exactly this case.

---

## P3.31 — SCENE OUTPUT RESOLUTION SAFETY REGISTER (accepted R1/R2/R3 + remedy)

Date: 2026 (P3.28 follow-up implementation phase). Implements the ACCEPTED
design review (docs/design/SCENE_AUDIO_COMPOSITION_DESIGN_REVIEW.md) plus
the user's final safety correction. No data-model change, no new entities,
nothing automatic.

- **P3.31-R1 (stale was measured against "latest existing version")**:
  CLOSED — `is_scene_combined_stale` now compares each lineage source's
  recorded asset against the slot's currently RESOLVED asset
  (`resolved_asset_for_slot`: explicit selection, else latest). A
  deliberately curated OLDER version IS the composition — its snapshot
  is NOT stale. New curated-older reason: "B3's selected version (v01)
  differs from this Combined output (v02)". Strict identity = asset id;
  tolerant version-equality fallback only for hand-written/legacy
  lineage that recorded no asset_id. Supersedes residual note P3.30-R2
  (curated-older combine no longer flags stale — that behaviour is what
  the accepted review S8/S18-Risk2 predicted and the user accepted).
- **P3.31-R2 (no structural staleness)**: CLOSED — stale also when the
  snapshot's recorded slot set ≠ the Scene's current expected slot set
  ("Scene structure has changed since this Combined output (N parts
  expected, M in snapshot)"). A snapshot missing a newly added block's
  audio can never silently pass as the complete Scene.
- **P3.31-R3 (Combine did not take over the resolved output)**: CLOSED —
  a successful Combine CLEARS `Scene.selected_output`; the fresh
  snapshot (born fresh: built from the current composition and slot
  set) wins the default rule. Status toast announces
  "Scene output → Combined vNN". Keeping an older selection made
  Combine appear to do nothing to what Assembly consumes.
- **P3.31-SAFETY (single Part could resolve as the Scene output)**:
  CLOSED (user-mandated correction) — automatic resolution chain is
  now: (1) explicit selection → (2) latest NON-STALE COMPLETE combined
  → (3) REVIEW REQUIRED. The legacy asset rule applies ONLY to
  PROVABLY complete full-scene assets (`is_complete_scene_asset`: no
  slot provenance AND no `_Part_`/`_Long_v` filename marker) and ONLY
  when the Scene has no combined outputs at all. A single Part
  AudioAsset is NEVER the automatic Scene output — no exceptions, not
  even a one-slot Scene, not even via legacy part-file naming. NEVER a
  fallback from a failed Combined resolution to an arbitrary Part.
  Review reason names the remedy ("Combine the Scene, or choose an
  output explicitly"). Explicit user selection of a Part asset remains
  allowed (CUSTOM) — the rule constrains AUTOMATIC resolution only.
- **P3.31-REMEDY (review state was a dead end)**: CLOSED — REVIEW
  REQUIRED rows in the Assemble dialog now offer [Combine Scene Now]
  (routed by scene id through the SAME combine handler — works for
  non-active Scenes; guarded while generation is running) and
  [Choose Output…] (relabeled from "Choose..."; picker option renamed
  honestly to "Latest full-scene audio (legacy)" and offered only when
  a provable full-scene asset exists). After a remedy combine the
  dialog rows rebuild automatically. Stale outputs remain explicitly
  selectable through the existing friendly Cancel / Use Anyway
  workflow (unchanged, P3.30(f) copy).
- **P3.31-ENV (sandbox environment reverted again)**: INFO — the Python
  env lost PySide6/sounddevice mid-phase (same class as the P3.30
  restore event); reinstalled PySide6 6.11.2 + sounddevice into
  /home/z/.venv and rebuilt the /tmp/gllibs GL runtime per the
  documented recipe. Workspace files were NOT affected.

Tests: tests/test_p3_31_scene_output_safety.py (17 runtime tests:
R1 ×4, R2 ×4, SAFETY ×3, R3 GUI ×3, REMEDY GUI ×3); honest fixture
updates in test_p3_28 (legacy-asset fallback test now uses a provable
full-scene asset + new test_part_asset_never_resolves_automatically;
"Choose..." label finders in test_p3_30 updated to "Choose Output…").
Full suite 835/835; verify scripts 5/5 PASS; launch smoke OK;
E2E P3.30 regression 15/15 PASS.

## P3.33 — Model load animation: centering + finite-loop freeze (user
## reported), asset now shipped

User report: "Nem rossz, csak nem középen van" (not bad, just not in
the center) + the user attached their own exported animation
(model_load.webp). Both defects were proven by pixel/runtime probes
before fixing, and both are covered by new regression tests.

- **P3.33-CENTERING (hero animation was left-aligned, not centered)**:
  CLOSED — pixel analysis of the user's screenshot: the 140x140 hero
  label sat at the LEFT content margin (center x = 94.5 on the 400px
  dialog; the title below it was centered). Root cause: the fixed-size
  QLabel was inserted into the QVBoxLayout WITHOUT a layout-item
  alignment, and Qt parks such a widget at the layout cell origin; the
  QLabel's own alignment only centers the movie pixmap INSIDE the
  label. Fix: `insertWidget(0, label, 0, AlignHCenter)`. After the fix
  the label geometry is x=130..270 — center x = 200.0 = the dialog
  center (offscreen grab, off by −0.5px). Regression test:
  label center == dialog center within 1px AND label not at the left
  margin.
- **P3.33-LOOP (finite-loop asset would freeze the loading indicator)**:
  CLOSED — found while probing the user's asset BEFORE wiring it in:
  QMovie.loopCount() == 0 (plays exactly once). Runtime proof: finished
  at ~4.8s, then the movie froze on frame 87 for the rest of a 30+s
  model load. A loading indicator must never go static: `finished` now
  restarts the movie while the dialog is visible (infinite-loop assets
  never emit finished and are unaffected; a closed dialog never
  restarts its movie — no zombies). Tests: real-time end-to-end
  (NETSCAPE loop=1 fixture: finished fires, movie Running again) +
  deterministic emit-based wiring (restart on visible, NOT on closed).
- **P3.33-ASSET (animation was invisible until the user exported one)**:
  CLOSED — the user's export (256x256, 88 frames, ~24fps, opaque white
  card, ~270KB) now SHIPS in assets/animations/. The P3.32
  "repo ships no asset" test premise flipped honestly: the new
  test_repo_ships_playable_animation proves the shipped file is found
  and playable; the classic-layout fallback contract stays covered via
  patched empty/corrupt dirs; the P3.25 dark-body regression pins the
  no-asset state (its original intent) because the shipped white card
  legitimately covers ~13% of the animated dialog.

Tests: tests/test_p3_32_model_load_animation.py grown 10 → 13 runtime
tests (−1 honestly rewritten premise, +4 new: centering, finite-loop
restart, deterministic restart wiring incl. zombie guard, shipped-asset
playability). Full suite 848/848; verify 5/5 (121 files compile,
functional 80/80); launch smoke PASS (real MainWindow _load_model path:
animation active, running, centered — label center x=200 = dialog
center x=200); E2E P3.30 regression 15/15 PASS.

## P3.34 — UI finetune: Recents panel, Scene pencil, volume handle,
## History list behaviour, typography

User-requested UI refinement task (submitted under the label "P3.32 UI
FINETUNE"; registered as P3.34 — P3.32/P3.33 are taken by the model-load
animation work). No architectural changes: Project/Scene/Character/
AudioAsset model, Scene Audio Provenance, Combine/Concatenate semantics,
RecentsManager MRU and the generation pipeline are untouched.

- **P3.34-PANEL (Recents surface scrolled with the content)**: CLOSED —
  the P3.26 panel styling lived on the SCROLLING content widget, so the
  border/radius scrolled away with overflowing rows. New structure:
  STATIC outer QFrame (lifted bg + border + radius + padding) wrapping
  a transparent QScrollArea; only the rows scroll (pixel-verified: panel
  geometry unchanged while scrolling). Same shared widget renders all
  four Recents contexts.
- **P3.34-HEIGHT (Recents panel "unnecessarily short")**: CLOSED — TWO
  root causes. (1) The sidebar's fixed-height items exceeded the
  vertical layout budget already at the default 1440x900 window, so Qt
  crushed the panel below its minimum (pre-existing since P3.6). Fixed
  by trimming fixed overhead: nav rows 48→40px, nav text 16→14px
  (the user-requested size), nav icons 24→20px (requested 18-20px
  band), layout spacing 16→10, context-scroll minimum 120→100.
  (2) Qt caches QScrollArea::sizeHint from the first computation — for
  the post-construction-populated Recents the hint stayed at the
  empty-boot value so the layout NEVER grew the panel; and
  widgetResizable kept the content widget at viewport size so
  fixed-height rows overflowed INVISIBLY without a scrollbar (this also
  clipped the ALL-SCENES context list — pre-existing). Fixed with a
  live-sizeHint scroll-area subclass + deterministic minimumHeight
  computed from the rows' fixed heights + adjustSize() after every
  populate (scrollbar range) + a child deferred re-sync timer. Result:
  five 48px two-line rows fit without scrolling (max 248px); longer
  lists scroll INSIDE the static panel; short windows squeeze
  proportionally as before.
- **P3.34-PENCIL (status badge masquerading as a broken pencil)**:
  CLOSED — the "pencil" the user saw was the STATUS badge: draft /
  not_generated rendered the "edit" (pencil) glyph with a
  NOT_GENERATED tooltip. No-audio states now render the neutral
  audio_file glyph (status tooltips stay on the status indicator
  only), and every ALL SCENES row carries a REAL pencil button
  (tooltip exactly "Rename Scene") wired to the ONE authoritative
  rename workflow (_rename_scene_entity — the parameterized core of
  the existing ProjectSceneBar path; no second rename system).
  Rename updates the Scene entity, marks unsaved state, refreshes ALL
  SCENES / RECENTS / ProjectSceneBar, persists through Project save,
  and preserves scene order, generation status and scene identity.
- **P3.34-VOLUME (handle clipped at the bottom)**: CLOSED — QSlider's
  vertical size policy is FIXED: the layout gave the slider its 15px
  size-hint while the styled handle needs 17px (subControlRect proof:
  handle at y=-1..16 in a 15px widget — clipped top AND bottom).
  Fix: explicit 28px slider height; 7px groove and 17px handle center
  with equal ~5.5px clearance; handle size/style unchanged. Verified
  at 0/50/100 and inside the real transport island.
- **P3.34-HISTORY (entry click opened the full modal dialog)**: CLOSED
  (user decision, supersedes the P3.5 contract) — clicking a History
  entry now behaves like every other context list: it SELECTS the
  entry (authoritative _last_viewed_history_id; no new state) and does
  NOT open the HistoryViewDialog. The full view opens explicitly via
  the NEW compact ALL HISTORY header icon (tooltip "Open Generation
  History", visible in the History context only, enabled when empty —
  the view has a proper empty state) which reuses the EXISTING
  _focus_history handler / HistoryViewDialog (no second History
  window). Opening the view does not change the selected entry.
  Recent Audio MRU updates on click, same as the other contexts.
- **P3.34-TYPO (top nav text "visually too small")**: CLOSED — the
  theme's global 13px base QSS font silently overrode the programmatic
  nav fonts (production reality: 13px, not the 15px token). The
  NavButton stylesheet now carries font-size: 14px (the requested
  size) — production top nav is genuinely 14px now. Recents/context
  row labels: 14px primary / 12px secondary (new metadata_md token,
  same Inter family) carried in the widgets' own stylesheets for the
  same override reason. Status/badge scale unchanged (no global font
  inflation). The + button was already icon-only (P3.26) — kept.
- **P3.34-STRUCT (documented, pre-existing)**: INFO — at 1280x720 the
  sidebar's fixed-height content structurally exceeds the available
  vertical budget (the app minimum is 1100x700; 1024x768 cannot occur):
  the Recents panel squeezes to ~30-35px but stays present with working
  internal scrolling. 1440x900 reaches the 120px+ band; larger windows
  show all five rows without scrolling.

Tests: tests/test_p3_34_ui_finetune.py (50 runtime tests: recents
button/panel/scroll/heights/typography, pencil + rename ×10 incl.
persistence, volume geometry ×2, history sidebar + runtime + empty,
responsive ×3 resolutions + fullscreen + five-fit + long-list scroll).
Honest supersessions: P3.5 history-click contract (dialog → selection;
the reversal is the user's explicit decision), P3.6 recents structural
pin (scroll inside the panel layout, stretch-0 contract unchanged).
Full suite 898/898; verify 5/5 (122 files compile, functional 80/80);
E2E P3.30 15/15; launch smoke PASS; visual inspection (offscreen grabs
+ pixel analysis) confirmed every acceptance item.

---

## P3.39 TOKEN-INTEGRITY REGISTER (token enters the chain without selection)

User report (HU): a token (<|emotion:affection|>) appeared in the
prompt chain although NO token was selected in the right panel
(status bar showed `TOKENS: <|emotion:affection|>` /
`SOURCES: emotion=Affection (Global)`); it could not be removed,
only overridden. Fresh projects with pasted text must receive NO
tokens unless the user explicitly sets one.

Audit method: real Engine + real MainWindow offscreen probes with
stack-trace instrumentation on every emotion_changed emission
(/home/z/audit_probes/probe_p39_tokens.py, probe_p39_desync.py,
probe_p39_postfix.py) — every finding below is RUNTIME-PROVEN, no
AST-only claims.

### P3.39-F1 (CRITICAL): programmatic setters emitted user-intent signals

- **Status**: CLOSED (P3.39)
- EmotionButtons.set_selected (ui/panels/control_panel.py) and
  StyleButtons.set_selected emitted emotion_changed / style_changed.
  Every PROGRAMMATIC display update (scene load, block-selection
  display, preset apply, Friendly<->Advanced sync, RightPanel
  wrappers) therefore masqueraded as a USER click and re-entered
  MainWindow._on_emotion_changed — writing the global, or (worse,
  with a block scope active) a hidden Narration-Block OVERRIDE with
  zero user action. Runtime proof: `rp.set_emotion("Affection")`
  with the auto-selected block scope wrote block[0].emotion while
  the global stayed None; scene load fired a spurious
  emotion_changed('Affection').
- **Fix**: both setters are now SIGNAL-SILENT (Qt convention). The
  only emission source is a real user click (_on_clicked). Verified:
  programmatic set with an active scope writes NOTHING (block
  overrides and globals untouched; emission log empty).

### P3.39-F2 (CRITICAL): the ghost emotion — unremovable, invisible token

- **Status**: CLOSED (P3.39)
- Reproduction (runtime): set the global emotion (e.g. Affection,
  no block selected) → select a Narration Block → click the Friendly
  "Neutral" pill. The change is scope-routed into the BLOCK override
  (P3.25 contract — correct), but the panel widgets kept the RAW
  widget value (None) as their display. Result: the GLOBAL
  "Affection" survived invisibly — the right panel showed NO token
  selected while every block still emitted <|emotion:affection|>
  (inherited) — the exact reported screenshot. The token was
  unremovable through the visible UI: the Affection button appeared
  unchecked (clicking it once re-SELECTED it — "only override"),
  and Neutral kept writing the block override.
- **Fix**: MainWindow._refresh_panel_from_state() re-asserts the
  panel display from the AUTHORITATIVE state after every
  scope-routed write AND after block selection changes (single
  honest-display path, signal-silent): block scope active → the
  block's EFFECTIVE value (override or inherited global); no scope
  → the global. The panel now always displays what the prompt chain
  will actually emit. The ghost is VISIBLE, and removal is
  discoverable: deselect the block → the global shows → click the
  checked button → toggle off → token gone (runtime-verified).

### P3.39-F3 (by design, documented): hidden block-scope auto-selection

- **Status**: INFO (behaviour kept, made honest)
- _post_analyze auto-selects block[0] after block detection (mode
  switch / Re-detect), which activates the block scope for the NEXT
  emotion click. The selection IS visible (properties panel title,
  gutter accent bar, source indicator), and with F1+F2 fixed the
  display is always truthful — a click visibly writes the block
  override (source indicator "Block Override"). P3.25's block-scope
  contract is preserved (regression-tested).

### Verified guarantees (runtime-proven, tests/test_p3_39_token_integrity.py)

1. Fresh project + pasted text: NO tokens in the prompt until the
   user explicitly sets one (verbatim user requirement).
2. Paste / auto-detect / tab switches / programmatic setters /
   scene load: ZERO emotion/style emissions — no state writes
   without a user click.
3. Scene load never fabricates block overrides (emission-free).
4. The ghost scenario now displays the effective emotion; the token
   is removable (toggle click / Friendly Neutral without scope).
5. P3.25 block-scope contract intact (block edit ≠ global edit).
6. Scene save/load round-trips semantics without side writes.

Full suite 995/995 (976 existing + 19 new); verify 5/5
(126 files compile, functional 80/80); launch smoke 11/11.

---

## P3.40 BATCH-TABLE ROW-RENDERING REGISTER (duplicate purple column dividers + clipped action icons)

User report (EN): the selected row in the Batch Generation table shows
TWO overlapping purple vertical lines at column boundaries — one bright,
full row height; one darker, shorter, roughly text/content height. The
Actions column icons are vertically misaligned with the bottom portion
slightly clipped and the buttons sitting too low. Audit-only brief: no
UI redesign, no provenance/generation changes, root cause first.

Audit method: real QApplication + real Modern Dark theme (app-level QSS
+ QPalette applied exactly as MainWindow does) + production
BatchGenerationDialog rendered offscreen; numpy pixel analysis of every
column boundary in every interaction state; full geometry chain
measured (row → cell rect → cellWidget → layout → button →
contentsRect → icon); QSS bisection (string surgery) to attribute each
line to its exact rule. All findings runtime-proven.

### P3.40-F1 (visual): bright purple full-height line at EVERY column boundary

- **Status**: CLOSED (P3.40)
- The app-level stylesheet rule
  `QTableWidget::item:selected { border-left: 2px solid {accent} }`
  (ui/theme.py, Modern Dark accent #d0bcff) cascaded into the batch
  table: a widget stylesheet only overrides the properties it declares,
  and the dialog's own QSS never reset `border-left`. Every selected
  cell painted a 2px bright purple left border — measured at every
  boundary (e.g. x=[41,42], h=46, #D0BCFF); disappeared when the app
  rule was stripped (bisection). The app-level rule remains INTENDED
  for other tables (history etc.) — the batch dialog now neutralizes
  it locally.

### P3.40-F2 (visual): second darker, shorter indicator at the same boundary

- **Status**: CLOSED (P3.40)
- The dialog's own per-cell selected box
  (`::item:selected { background: rgba(139,92,246,35) }`, measured
  composite #4B4066, plus its per-cell border-bottom + padding box
  geometry) lands on the same x-position as the inherited border-left;
  on the user's Windows/native-style + DPI pipeline the two per-cell
  boxes from two different stylesheets read as two overlapping
  indicators (at 100% offscreen Fusion the fill tiles seamlessly, so
  only one line is visible there). Consolidation removes the entire
  per-cell border stack — one uniform row fill remains, so every
  possible source of the second edge is gone regardless of which
  sub-pixel path drew it.

### P3.40-F3 (geometry): Actions column clipping / bottom-heavy buttons

- **Status**: CLOSED (P3.40)
- `QTableWidget::item { padding: 6px }` insets every setCellWidget()
  into the item CONTENT rect: the action widget got 48-6-6-1 = 35px
  for a fixed 4+36+4 = 44px button band — buttons overflowed 5px past
  the widget bottom (clipped by QWidget child clipping; the regen
  button's bottom 5 rows were missing from the grab; clearance 4/-5),
  and the top-anchored band sat ~4px below the row's centre.
- **Fix** (no size reductions, no behaviour changes): `::item` vertical
  padding 0 (content height 47px, band fits with ≈1.5px slack per
  side); explicit `AlignVCenter` on the actions/generated layouts;
  button QSS padding 2px→1px so the 32px icon fits the 36px button's
  contents rect exactly; derived + documented 124px minimum Actions
  column width (3×36 + 2×4 + 2×4 + 2×6).

### Verified guarantees (runtime-proven, tests/test_p3_40_batch_row_rendering.py, 26 tests + DPI probe)

1. Zero accent-coloured vertical runs at any column boundary in the
   selected row (scene + manual modes; click, hover, current cell in
   every column, keyboard navigation, focus lost, fullscreen).
2. Exactly one selection treatment: the uniform translucent purple
   row fill (same composite colour in every column, ±15/255).
3. Action widget = full item content rect (47px); all three buttons
   36×36 fully inside; symmetric clearance (5/6); equal 4px spacing;
   icons 32×32 unclipped; regen bottom rows painted.
4. Geometry holds for every JobStatus, disabled/running state, a
   300-character filename, a resized (220px) Actions column and at
   DPI 100/125/150% (subprocess probe, devicePixelRatio-aware pixels).
5. P3.35 column persistence (saveState/restoreState) intact
   (regression-tested: saved 200px width restored verbatim).
6. Play/Stop/Regenerate behaviour, selection behaviour, version
   selection, generation state, provenance, batch manager, history —
   all untouched; the app-level border-left rule keeps its intended
   role in OTHER tables.

Full suite 1021/1021 (995 existing + 26 new); verify 5/5
(128 files compile, functional 80/80); launch smoke 11/11;
visual audit PASS on all seven §20 confirmations.

---

## P3.41 REGISTER — NARRATION BLOCKS ADJACENCY + LONG GENERATION ROUTING

### F1 (CLOSED) — Narration Blocks labels collapse when the separator blank line is removed
- Symptom: B1/B2 assigned (ANNA/MÁRK); removing the blank line between
  the sentences painted both gutter labels at the same y — garbled,
  collapsed row; re-insert + Re-detect restored it.
- Root cause (runtime-proven): the gutter painted each block header at
  its RAW document y range (cursorRect of the block offsets) with NO
  minimum row height and no stacking constraint. After the edit both
  blocks' (still-correct) offsets map to the SAME document line, so
  both headers rendered coincident (16px overlap, pixel- and
  VLM-verified). The block STATE was never corrupted — ids, offsets,
  character assignments all preserved by the manager's delta tracking.
- Fix: `_BlockGutterWidget._block_rows()` — ONE row per block,
  anchored at the document position, min height MIN_ROW_HEIGHT = 18
  (derived from the header geometry: 1px separator + 2px inset + 16px
  label band − 1px), monotonically stacked (never overlapping).
  Painting AND click hit-testing share the layout. Blocks that don't
  collide keep row_top == document y (no arbitrary spacing).
- Guarded by: tests/test_p3_41_nb_batch_regression.py (merged-line,
  adjacent-line, multi-blank-line, edits before/between/around blocks,
  scroll, stacked-row click selection, render pixel bands, anchor
  fidelity, id/character preservation, reinsert+re-detect cycle).

### F2 (CLOSED) — Long Generation reuses an obsolete Batch window bound to the previous Scene
- Symptom: with a Batch dialog left open from Scene A, switching to
  Scene B and starting Long Generation focused the OLD dialog: title /
  coverage / combined outputs / completion callback still Scene A's
  while Scene B's jobs ran. On completion the WRONG Scene's coverage
  recomputed and the new Scene stayed stuck GENERATING.
- Root cause (runtime-proven): `_focus_existing_batch_dialog` compared
  ONLY the mode (scene vs manual). Same mode → focus & reuse,
  regardless of which Scene the dialog was bound to. Overrides never
  changed the mode (routing matrix A–F/H/K all resolve to Scene mode);
  the trigger was an open dialog + scene switch — hence "sometimes".
- Fix: the focus match now includes the SCENE IDENTITY
  (want_scene_id). Same mode AND same scene → focus (single instance
  preserved); different mode OR different scene → clean supersede via
  close_without_stopping_batch (running batch preserved) and rebuild
  through the ONE P3.35 wiring path for the Scene being generated.
- Guarded by: routing tests A–F + §2K acceptance, scene-switch rebind
  (title/scene/jobs/callback-scene-id), same-scene single instance,
  manual hand-over, running-batch survival, manual queue preserved,
  all-entries-one-handler.

### Token/override regression guard
- The routing fix touches no token plumbing; tests prove: fresh paste
  → no implicit tokens in Batch job prompts; explicit override → token
  present exactly in that block's part; per-part character/style
  effective-state consistency (P3.39 guarantees intact).

Full suite 1049/1049 (1021 existing + 28 new); verify 5/5
(129 files compile, functional 80/80); launch smoke 11/11;
visual audit: two legible, separated block rows after the edit; the
Batch window follows the active Scene after a switch.

## P3.43 VOICE-PROFILE-UNIFICATION REGISTER (fragmented voice workflow)

- **Status**: CLOSED (implemented + runtime-verified — 1135/1135 suite,
  verify 5/5, launch smoke 11/11, VLM visual audit)
- **Discovered**: P3.43 audit round (36 runtime findings: 2 BUG, 4 LIMIT,
  11 FIND, 19 OK)
- **Spec**: one VoiceProfile model, one VoiceManager, ONE management
  screen, ONE editor (Create/Import/Edit), lightweight selectors
  elsewhere; Engine facade complete; VOICE_CHANGED authoritative.

### Closed findings

- **F1 CLOSED — avatar path base mismatch**: preview_image was stored as a
  bare profile-dir-relative filename no consumer could resolve; imported
  avatars were never displayed. NOW: app-root-relative convention
  (voices/<id>/avatar.png) everywhere + deterministic legacy-value
  migration on load + multi-candidate resolver in AvatarLabel.
- **F2 CLOSED — Qt enum crash**: `Qt.AspectRatioMode.KeepAspectRatioByExpansion`
  does not exist in PySide6 6.11.2 → avatar rendering would AttributeError.
  NOW: `KeepAspectRatioByExpanding`; rendering runtime-tested.
- **F3 CLOSED — metadata cannot be cleared**: empty strings were silently
  ignored. NOW: None = unchanged, "" = explicit clear (json-level test).
- **F4 CLOSED — non-atomic import + BMP filter mismatch**: the import
  dialog offered *.bmp while the engine rejected .bmp AFTER creating the
  profile. NOW: pre-flight validation of everything (WAV readable, avatar
  extension), staged replacement (.incoming/.bak with rollback), full
  transaction with directory rollback, dialog filter aligned to the
  engine whitelist.
- **F5 CLOSED — dependency-blind delete**: NOW: Characters + Scenes are
  scanned before deletion, shown in the confirmation, and their
  assignments are CLEARED after a confirmed delete (project + scene
  persist paths) — no silent dangling ids.
- **F6 CLOSED — dead Tools→Voice Library / Ctrl+L action**: the QAction
  existed since P3.6 with NO handler. NOW: connected → unified manager.
- **F7 CLOSED — File→Export Voice opened the whole library as a picker**:
  NOW: real export of the currently selected profile through the facade
  (with an explicit pointer to Voice Profiles when nothing is selected).
- **F8 CLOSED — two competing import flows + name-only create**:
  VoiceLibraryDialog's chained QInputDialog flow and MainWindow's
  QInputDialog create are retired — Create/Import both route into the
  single editor; the manager offers no QInputDialog paths (source-pinned
  test).
- **F9 CLOSED — no reimport / non-portable exports**: NOW:
  Engine.import_voice_profile_dir rebuilds a profile from an exported
  folder using the files actually present (stored paths are NOT trusted),
  preferring the original id when free; full round-trip verified
  (name/metadata/transcript/audio/avatar).
- **F10 CLOSED — Character Management latent crashes (pre-existing)**:
  `from engine.logger import logger` (nonexistent name) and
  `QFormLayout.setStyleSheet` (nonexistent method) — the dialog could
  never be constructed in the real app (tests only AST-parsed it).
  Both fixed; the dialog is now runtime-driven in the P3.43 suite.
- **J CLOSED — VOICE_CHANGED gaps**: transcript edits and private-path
  mutations emitted nothing. NOW: every facade mutation emits
  VOICE_CHANGED; the UI no longer reaches into engine._voices at all
  (source-pinned test); the manager refreshes live.

### Kept-by-design (documented)

- The classic VoiceSection combo (Advanced tab) remains the lightweight
  scene-level selector; the Friendly "Change" opens the NEW lightweight
  picker whose explicit "Manage Voice Profiles…" opens the manager (§15/§27).
- Dead modules `ui/panels/sidebar.py` + `ui/panels/modern_control_panel.py`
  are NOT deleted this round (P3.43 §32 conservative cleanup; they are
  unreachable from any live entry point and remain pinned by legacy
  source-scanning tests).

### Verified guarantees

1. ONE VoiceProfile data model, ONE VoiceManager persistence (no second
   registry; source audit clean).
2. ONE management screen ("Voice Profiles") + ONE editor (3 modes, one
   form architecture); no competing dialogs remain (routing + source tests).
3. The Engine facade exposes every voice operation; the UI uses no
   private voice state.
4. Every mutation emits VOICE_CHANGED; selectors refresh without restart
   (rename/delete/create propagation runtime-tested).
5. Edit keeps the profile id stable; metadata can be set AND cleared;
   reference replacement preserves the transcript and validates first.
6. Import is transactional — no partial profile, no orphaned files, no
   misleading success state.
7. Export → delete → reimport round-trips everything (same id when free).
8. Delete is dependency-aware — referenced profiles cannot be silently
   deleted; confirmed deletes clear Character/Scene references.
9. Keyboard navigation updates the detail pane exactly like mouse
   selection.
10. The golden path (25 steps incl. generate + regenerate) passes on a
    real MainWindow without restart.

---

## P3.44 REGISTER — HISTORY→PLAYER INTEGRATION + SCENE-BATCH PLAYBACK INTEGRITY

**Round**: P3.44 · **Status**: CLOSED · **Severity**: P0/P1 (batch), P2 (history auditioning)

### User-reported symptoms

1. *(A)* Clicking a generated item in the left History panel only
   highlighted it — the audio could only be auditioned inside the
   History screen, never from the main window.
2. *(B)* Scene-mode Batch: previously generated versions (v01, v02,
   v03) stay visible after a table refresh or a Scene reopen, but rows
   "lose focus" and Play is disabled or plays nothing; the same
   reproduces after closing and reopening the Scene in Batch mode.

### Root causes (runtime-verified)

- **F1 (A) — click did not load the transport**: the History entry
  click selected + refreshed MRU (P3.34 contract) and stopped there.
- **F2 (A) — relative path reached the WaveformPlayer**:
  HistoryViewDialog passed `entry.output_path` (app-root-relative) to
  the player whose peak loader resolves against the process CWD —
  a trackless transport (P3.25 bug class).
- **F3 (A) — split resolution bases**: the dialog's existence
  pre-check resolved against the module-derived app root while
  playback resolved against the engine's — a "file missing" modal for
  playable entries whenever the roots differed.
- **F4 (B) — Play bound to queue-execution state**:
  `job.status == COMPLETED and bool(job.output_path)` gated Play. A
  reopened Scene has fresh PENDING jobs (output_path None) while ALL
  prior versions live in Scene audio_assets provenance → visible but
  unplayable (the P0 defect).
- **F5 (B) — destructive full-table refresh**: `_refresh_table`
  destroyed and rebuilt every row widget on EVERY manager change
  (each job status update) → focus loss, selection loss, the "row
  loses focus" symptom.
- **F6 (B) — version menu had no preview**: one action per version,
  choosing = Use (selection only); no way to listen to a version
  before selecting it.

### Fixes (all runtime-tested)

- **A CLOSED**: `MainWindow._load_history_entry_into_player` loads the
  entry's audio into the bottom transport (engine-app-root-resolved
  absolute path, duration/sr from the authoritative entry, real peaks,
  no auto-play, non-blocking missing-file handling); the P3.34
  no-dialog selection contract is intact; `HistoryViewDialog` uses ONE
  resolution base (`_resolve_path`) for pre-check, Play, Reveal and the
  status column, and Play lands on the same current track as the
  sidebar click.
- **B CLOSED (F4)**: Scene-mode Play resolves through
  `resolved_asset_for_slot` (explicit selection, else latest) at CLICK
  time; Manual mode keeps the BatchJob.output_path path (§9 — two
  independent paths); Play enablement is derived from the existence of
  the authoritative asset file (§5/§11), independent of job status.
- **B CLOSED (F5)**: `_refresh_table` is now a dispatcher — unchanged
  structure signature (job object identities) → targeted in-place
  updates (widget identity preserved; only changed cells touched);
  structural change → full rebuild WITH current-row/scroll/checkbox
  preservation and focus returned to the table.
- **B CLOSED (F6)**: every version menu row has a read-only [Play]
  preview (plays exactly that version, menu stays open) and a [Use]
  selection action (provenance mutation only, closes the menu); missing
  files show a clear "⚠ file missing" unavailable state with no silent
  fallback.

### Architectural rule enforced (§13)

BatchJob.output_path stays queue-execution state — playback resolution
NEVER copies the AudioAsset path into it (runtime-asserted after every
play interaction: still None).

### Regression protection

- New: `tests/test_p3_44_history_player_load.py` (9 tests),
  `tests/test_p3_44_batch_playback_integrity.py` (23 tests incl. the
  full §14 reopen-and-play golden path on a real MainWindow).
- Intentionally updated: 1 P3.35 wiring test (fake path → real WAV per
  the §5 file-existence contract; wiring proof preserved).
- Full suite 1167/1167; verify 5/5; launch smoke 11/11.

## P3.44.1 REGISTER — BATCH UI STATE SYNCHRONISATION + UNSAVED SCENE PRESERVATION

**Round**: P3.44.1 · **Status**: CLOSED · **Severity**: P1 (state integrity,
data loss), P2 (visual desync)

### User-reported symptoms

1. After All/None, the visible row checkboxes diverge from the actual
   selection (the number of generation jobs changes correctly — the
   internal state is right, the visible checkboxes are not).
2. After a single-job batch completes, the dialog does not return to idle
   by itself; controls stay in the running state.
3. The "generating" indicator does not follow the actually-active row.
4. Stop while paused between jobs leaves the batch "running" forever;
   later Start is a silent no-op.
5. Results of an in-flight generation can land on a row that never ran
   after the queue is replaced mid-run.
6. Switching projects away and back during a batch discards unsaved
   Scene text/blocks; results register on the wrong project's Scene.

### Root causes (runtime-verified)

- **F1 — in-place refresh ignored CHECK state**: `_update_rows_in_place`
  synced only the checkbox ENABLE state; All/None mutates `_check_states`
  and refreshes through the same-structure in-place path → visible boxes
  stale.
- **F2 — single change-callback slot**: the manager's `set_on_changed`
  held ONE listener; a second observer replaced the first dialog's
  callback — the first dialog then received no manager updates at all
  (stale running UI after completion).
- **F3 — index-based active row**: `current_index` (and completion
  routing) keyed on queue INDEX; queue mutation mid-flight redirected
  the completion to whatever job occupied the old index; a stale
  inter-job timer could double-schedule (two rows generating).
- **F4 — non-deterministic idle**: `stop()` with nothing in flight set
  flags but never finished the batch → `_running` stuck True forever;
  later `start()` silently returned False.
- **F5 — cross-contamination on queue replacement**: an orphaned
  in-flight call's result was attached to the job at the OLD index (a
  job that never ran showed Done with the wrong output).
- **F6 — disk-reread project instances + active-scene result routing**:
  `get_project`/`list_projects` re-read `project.json` on every call —
  an A→B→A switch constructed a FRESH project, stranding unsaved
  in-memory edits; batch-triggered Scene operations (asset registration,
  recomputes, regen versioning) routed to the ACTIVE project/scene, not
  the owning one.

### Fixes (all runtime-tested)

- **F1 CLOSED**: `_update_rows_in_place` synchronises the visible check
  state with `_check_states` (signals blocked during the programmatic
  sync).
- **F2 CLOSED**: multi-listener manager (`add_on_changed` /
  `remove_on_changed`; legacy `set_on_changed` kept); the dialog
  registers/releases its own listener — coexisting observers never
  overwrite each other (two-dialog test).
- **F3 CLOSED**: `_current_job` identity bookkeeping; `current_index`
  resolves by identity (mutation-immune; orphan → no active row);
  `_process_next` single-flight guard; retry-revert emits a change.
- **F4 CLOSED**: deterministic idle — `stop()` with nothing in flight
  finishes the batch immediately (deadlock regression-pinned); idle stop
  is harmless; a `False` from `start()` is always surfaced (informing
  dialog, never a silent no-op).
- **F5 CLOSED**: identity guard in `_on_job_done` — results apply to the
  submitted OBJECT; an orphaned in-flight result is DROPPED, never
  attached to a row that did not run; queue hand-over: `start()` accepts
  a replaced queue (old call runs out, its result dropped, new PENDING
  jobs then run); a replaced queue never auto-runs without `start()`.
- **F6 CLOSED**: ProjectManager session instance cache (live instance is
  the editing state; delete evicts; `reload_project` escape hatch);
  `find_scene_owner` + `MainWindow._scene_owner` route every
  batch-triggered Scene operation to the OWNING project's live Scene.

### Additional hardening (same round)

- Batch job failures surface in the Batch table (FAILED pill, error
  tooltip, summary, log) — never as a stack of blocking modals (one per
  failed job); single user-initiated generations keep the modal.

### Suite-infrastructure defect (found by this round's verification)

- **F7 — full-suite OOM kill at ~85%** (twice, kernel-verified
  anon-rss 2.0→2.98 GB): unittest holds every TestCase instance — and
  its setUp attributes — until the END of the run; harness `tearDown`s
  only `close()`d windows (a hide, not a free) → ~40 MB retained per
  widget-driving test. The P3.44.1 file (inserted at ~85% suite
  position) pushed the resident set past the 4 GB host limit and the
  kernel killed pytest mid-run with no failure output — the first
  full-suite-with-new-file attempt died exactly this way. **CLOSED**:
  `tests/conftest.py` `pytest_runtest_teardown` hygiene hook
  (widget close+deleteLater+event-flush, drop all public instance
  attributes — safe by unittest construction, class state untouched,
  forced gc every 20 tests after the per-test gc measured 97 ms/test);
  the same discipline in the two P3.44 files' `tearDown`s.

### Regression protection

- New: `tests/test_p3_44_1_batch_state_sync.py` (23 tests — selection
  sync, single-job idle return, indicator fidelity, pause/resume/stop
  incl. the deadlock pin and restart-after-deadlock, widget identity,
  cross-contamination guard, unsaved-Scene survival across A→B→A /
  A→B→C→A / modified-existing, editor-text-not-disk, UI-thread
  marshalling, multi-listener).
- Updated: `test_p3_35_batch_two_workflows.py` (Stop state machine pin;
  listener-lifecycle tests on the list API; +1 new two-dialog test).
- New suite infra: `tests/conftest.py` (instance-state hygiene).
- Full suite 1191/1191 in a single run (8:28); chunked cross-check
  749/749 + 442/442; launch smoke 11/11.

---

## P3.44.2 audit round — SFX pipeline, sentence splitting, pause-resume

Discovered by dedicated runtime reproduction (not by test failure):
the reported SFX symptoms were reproduced end-to-end with the real
MainWindow + Engine + Batch pipeline offscreen before any fix.

### SFX-1: Preview and Batch interpreted block SFX/pause metadata differently

- **Status**: **CLOSED** (P3.44.2)
- **Severity**: High (semantic prompt divergence; user-visible SFX
  loss in Batch)
- **Root cause (runtime-proven, M1/M5 probes)**:
  `CanonicalPromptCompiler.compile_continuous` (Preview) converted
  `PromptBlock.sfx_insertions` / `pause_insertions` into prompt tokens
  via `PromptBuilder._process_block_inline`; the Batch pipeline
  (`NarrationSplitter._split_with_blocks` →
  `compile_for_batch_part(group_text, ...)`) never received the
  metadata — it only converted in-text `{sfx:...}` markers. Blocks
  carrying the structured representation lost their SFX/pause in every
  Batch part (and the Long dialog showed no marker).
- **Fix (one authoritative conversion)**: new
  `PromptBuilder.materialize_inline_markers()` is the single
  metadata→marker conversion; `_process_block_inline` (Preview) and
  `NarrationSplitter.split` (Batch, via
  `_materialize_block_metadata` — caller blocks never mutated,
  span-shifted copies, zero-cost fast path, raw mode exempt) both call
  it. A double-insertion guard makes the illegal
  metadata-plus-in-text-marker state materialize exactly once.

### SFX-2: sentence splitting vs SFX markers adjacent to punctuation

- **Status**: **CLOSED** (P3.44.2)
- **Severity**: High (wrong part boundaries; >400-char glued
  "sentences"; markers cut in half by block boundaries)
- **Root cause (runtime-proven)**:
  `NarrationSplitter.SENTENCE_END_RE = (?<=[.!?…])\s+` required
  whitespace after the punctuation — `???{sfx:...}` (no whitespace)
  was NOT a boundary, gluing two sentences into one oversized part;
  `HeuristicBlockDetector._split_sentences` used `[.!?…]+` with NO
  whitespace requirement and NO marker shielding — it cut at `???`
  (inconsistent with the splitter) and could cut INSIDE a marker whose
  onomatopoeia contains punctuation (`{sfx:Laughter:Ha!ha}`), leaving
  broken marker text in the part that the model read literally.
- **Fix**: both splitters now implement the same two rules:
  (1) SHIELDING — punctuation inside an inline marker never ends a
  sentence (a marker is never cut in half); (2) ATTACHMENT — a marker
  chain following sentence punctuation (with or without whitespace)
  belongs to the sentence it annotates: the sentence ends after the
  marker chain. Marker-less text behaves byte-identically to before.

### SFX-3: Batch Start while paused was a modal dead-end (no resume path)

- **Status**: **CLOSED** (P3.44.2)
- **Severity**: High (a paused batch could NEVER be continued from the
  UI)
- **Root cause (runtime-proven — the validation probe hung on the
  modal)**: while paused, the Pause button disables itself and the
  Start/GENERATE CHECKED button is the only enabled run control, but
  `BatchManager.start()` returned False for a paused run (no
  hand-over) so the manual-mode dialog blocked on an "Already
  Running" modal; the scene-mode `_on_generate_selected` blocked on
  the same modal; and `manager.resume()` had no UI caller at all.
- **Fix**: `start()` accepts a paused (non-stopping) run as a RESUME
  (delegates to `resume()` semantics under the same RLock);
  `_on_generate_selected` resumes a paused run instead of showing the
  modal. A plain double-start (not paused) is still refused.

### Regression protection (P3.44.2)

- New: `tests/test_p3_44_2_sfx_pipeline.py` (28 tests — cases A–J,
  Preview==Batch equivalence incl. block metadata at the UI level,
  Long-dialog SFX visibility, allow_sfx on both pipelines, splitter
  shielding/attachment, caller-block immutability, raw exemption,
  long-text no-loss, pause-resume on both start paths).
- `p3442_final_validation.py`: 19/19 runtime checks (reported SFX
  cases + P3.44.1 state revalidation in one run).
- P3.44.1 harness re-run: 21/22 with the single intermittent sample
  being the documented `marshal_to_ui` queue-drain window (three
  consecutive clean runs 0 mismatches; authoritative double-read
  tests 23/23).

## P3.44.3 audit round — feedback dialogs, final state revalidation

### FD-1: message boxes had no defined background (theme gap)

- **Status**: **CLOSED** (P3.44.3)
- **Severity**: Low (visual; every QMessageBox in the app rendered
  without the theme surface — the generic `QDialog { background:
  transparent }` QSS rule left them compositing over black/undefined
  on Linux, with dark-theme text on an effectively undefined surface)
- **Root cause (runtime-proven by pixel sampling of a rendered box)**:
  `ui/theme.py::generate_qss()` had no QMessageBox section at all;
  boxes inherited the transparent QDialog background.
- **Fix**: central "Message boxes" QSS section in `generate_qss()`
  (zero call-site changes, ~200 boxes styled at once, all 5 themes):
  `bg_surface` surface + 1px border + 12px radius (Radii.md medium
  container), `#qt_msgbox_label` 15px/600 title hierarchy,
  `#qt_msgbox_informativelabel` secondary colour + vertical rhythm,
  icon column padding, `#qt_msgbox_detail` recessed surface, button
  min-width 96px / 8px-20px padding. Semantic icons, buttons and
  keyboard behaviour untouched.

### FD-2: structured confirmations were one-blob text; locations not copyable

- **Status**: **CLOSED** (P3.44.3)
- **Severity**: Low (UX)
- **Root cause**: positive confirmations ("Project saved … Scenes …
  Characters … Location …") were plain `QMessageBox.information`
  hand-formatted `\n\n` blobs: summary and technical detail rendered
  identically; file locations were plain non-selectable label text.
- **Fix**: new central `ui/feedback.py::FeedbackDialog(QMessageBox)`
  — summary → main text (title level), technical details →
  informative text (secondary colour, word-wrapped,
  `TextSelectableByMouse` so paths can be copied). Interaction model
  bit-identical to QMessageBox (verified: Enter→default button result,
  Escape→same result as plain QMessageBox, modal, parented). Seven
  structured call sites migrated (project saved, scene saved, preset
  exported, voice profile exported ×2, batch queue saved/loaded);
  warnings/errors/questions remain plain QMessageBox and only receive
  the central QSS polish.

### FD-3: modal-suppression test seams broke after the symbol change

- **Status**: **CLOSED** (P3.44.3)
- **Severity**: Medium (test infrastructure — the affected suites
  HUNG on a real modal, not failed)
- **Root cause (runtime-proven with faulthandler stack dump)**: the
  migrated call sites changed the call symbol
  (`QMessageBox.information` → `FeedbackDialog.information`), so
  existing modal-suppression patches (`patch.object(QMessageBox,
  "information")` in test_p3_35, `_FakeMB` module patches in
  test_p3_43) no longer intercepted the dialog —
  `test_save_and_load_queue` blocked forever inside `exec()`
  (stack: ui/feedback.py:129 ← batch_generation._on_save_queue ←
  the test), stalling the full regression run at 59%.
- **Fix**: the patches follow the new seam (`patch.object(
  FeedbackDialog, "information", return_value=Ok)` and module-level
  `FeedbackDialog` → `_FakeMB`). Production behaviour unchanged;
  no test-only production code.

### Regression protection (P3.44.3)

- New: `tests/test_p3_44_3_feedback_dialogs.py` (17 structural tests —
  no pixel-fragile assertions): QSS section present in all themes,
  surface painted (background colour sample), title hierarchy font,
  plain variants (warning/critical/question) themed + icons + buttons,
  FeedbackDialog title/summary/details/fields presence, OK button +
  default, selectable details, long path wraps and is never truncated,
  modal+parent, exec accept returns Ok, Enter→default, Escape parity
  with plain QMessageBox, migration source guard.
- `ss/p3443_final_validation.py`: 9/9 runtime checks (spec §18 SFX
  lines end-to-end X1–X5 + dialog checks D1–D3).
- Full regression: 1236/1236 (1219 previous + 17 new); launch smoke
  11/11; P3.44.1 (23/23) + P3.44.2 (28/28) + P3.44.2 validation probe
  (19/19) all re-passed on the final build.

## P3.44.4 — Sidebar navigation and scene row icon scale (visual only)

Scope: left-sidebar primary navigation (Projects / Scenes / History /
Characters) and ALL SCENES row action icons. Constraint frame from the
spec: no row-height increase (nav 40px, scene rows 44/48px), no
redesign, no behaviour change, IconRegistry stays the single icon
source, existing Typography system only.

### SB-1: primary navigation visually undersized inside its rows

- **Status**: **CLOSED** (P3.44.4)
- **Severity**: Low (visual proportion, user-reported with screenshot)
- **Root cause**: `_NavItemButton` rendered 20px icons in a 28×28
  container inside 40px rows, with a 14px label — the Material
  Symbols viewBox (~80% fill) made the drawn glyph ~16px, leaving the
  row mostly empty; the label was also small relative to the sidebar
  width. The four icon render sites (initial + active/hover/inactive)
  and three stylesheet font rules were independently hard-coded, so
  any per-state drift would make the icon visibly jump.
- **Fix**: 16px label via the existing `Typography.body_lg()` Inter
  token (mirrored in the widget stylesheet so the theme base font
  cannot shrink it); 28px icons through IconRegistry; container
  28×28 and row height 40px unchanged. New class constants
  `_ICON_PX`/`_LABEL_PX` lock all seven sites to one scale.

### SB-2: ALL SCENES row action icons too small to target

- **Status**: **CLOSED** (P3.44.4)
- **Severity**: Low (usability of secondary actions)
- **Root cause**: `_SceneRow`'s status badge (16px icon/box) and
  rename pencil (16px icon on a 24×24 button) were proportionally tiny
  against the 44px row and the now-larger sidebar language; the
  elide budget in `_elide_text` also hard-coded the old right-side
  widths (22/30).
- **Fix**: badge 20px (box + rendered pixmap), pencil 20px on a
  28×28 button, row heights unchanged; the elide budget now derives
  from the same class constants as the rendered geometry
  (`_STATUS_ICON_PX + 6`, `_RENAME_BTN_PX + 6`), so long names elide
  correctly with the larger icons (tooltip carries the full name).

### Verification notes (P3.44.4)

- VLM-reported "row 1 ghosting" was objectively DISPROVEN: the widget
  tree holds exactly one label per row (clean geometry) and a
  pixel-diff of the MainWindow-rendered row-1 label against an
  identically-configured fresh row is 0/13344 (0.0000%). The VLM
  misread the low-contrast dark-theme render (contradictory "ghost
  text" across two passes).
- The sidebar's vertical footprint is provably unchanged: fresh-boot
  Sidebar `sizeHint()` is 644px both before and after P3.44.4 (220 and
  300px widths) — every modified widget has a fixed height, so the
  larger content fits inside the existing rows.

### Regression protection (P3.44.4)

- New: `tests/test_p3_44_4_sidebar_scale.py` (35 structural tests:
  labels/icons/sizes/containers/row heights/centring/state scale
  consistency/active+hover repaint/colour language/rename-signal
  behaviour/elision incl. budget-vs-geometry/scope guard on untouched
  controls/vertical budget).
- Intentional in-line updates of 3 pinned legacy assertions
  (P3.34 nav typography, P3.34 badge box, P3.11 badge box) for the
  user-requested scale change.
- Full regression: 1271/1271 (1236 previous + 35 new); focused
  sidebar battery 366/366; launch smoke 11/11; visual probe 12/12.


## P3.44.4 (round 2) — Batch execution-run semantics + P3.44.3 feedback regression

### BS-1: Unselected rows displayed "Stopped" after selective generation

- **Status**: **CLOSED** (P3.44.4 round 2)
- **Severity**: High (false state shown for jobs that never ran)
- **Reproduction**: Batch table A/B/C/D, check only B, GENERATE —
  A/C/D ended up rendered "Stopped".
- **Root cause**: TWO sites conflated table membership with execution
  membership. (1) `MainWindow._on_generate_selected`'s second pass
  flipped every UNCHECKED PENDING job to SKIPPED (the old P3.28
  design). (2) `BatchManager.stop()` flipped EVERY PENDING job
  table-wide. There was no explicit definition of which jobs belong
  to the current execution run.
- **Fix**: the execution run is now explicit — `BatchManager.start(
  only=...)` captures the run's job OBJECTS (`_run_jobs`; None = whole
  queue, the unchanged manual/full-batch semantics). `_process_next`
  only schedules run members; `stop()` only flips members; the
  unchecked rows are never touched by a run they were never part of.

### BS-2: Pause/Stop remained enabled after a single selected job completed

- **Status**: **CLOSED** (P3.44.4 round 2)
- **Severity**: High (controls stuck active with no active execution)
- **Root cause**: (1) `_process_next` checked the paused gate BEFORE
  checking whether any runnable member job remained — a pause pressed
  while the run's last job was in flight left the batch
  "running+paused" forever with nothing left to run. (2)
  `_finish_batch_locked` cleared only `_running`/`_current_index`,
  leaving `_paused`, `_stop_requested`, `_current_job`,
  `_restart_after_current` set after a finish (stale flags kept
  "Stopping…"/paused presentations alive and violated clean restart).
- **Fix**: gate order is now single-flight → not-running →
  stop-requested → member scan → finish-when-exhausted (EVEN while
  paused: an exhausted run is a finished run) → paused gate → submit;
  `_finish_batch_locked` clears ALL transient execution state before
  the final change event, so the final state is observable by the
  dialog before the final refresh. Pause/Resume/Start now emit change
  events (listener-based marshalling, unchanged architecture).

### BS-3: Obsolete callbacks/timers could start jobs after the batch finished

- **Status**: **CLOSED** (P3.44.4 round 2)
- **Severity**: Medium (state corruption after completion)
- **Root cause**: `_process_next` did not check `_running` — any late
  completion callback, leftover inter-job timer or marshalled race
  could silently launch a PENDING job of a run that no longer existed
  (masked pre-P3.44.4 because unselected jobs were SKIPPED).
- **Fix**: the `not _running → return` gate (P3.44.4 §11); a stale
  `_on_job_done` after completion now has no effect on any job state,
  the idle flag, or the controls.

### BS-4: Project Save showed "Failed to save project" after a successful save (P3.44.3 regression)

- **Status**: **CLOSED** (P3.44.4 round 2)
- **Severity**: High (user-facing failure message on success)
- **Root cause**: `MainWindow._on_save_project`'s migrated
  `FeedbackDialog.information` details string used positional indexes
  {1}/{2}/{3} with only THREE format arguments — `.format()` raised
  "Replacement index 3 out of range for positional args tuple" inside
  the try block, routing a SUCCEEDED save into the failure dialog.
- **Fix**: indexes corrected to {0}/{1}/{2} (exactly the form given in
  the task); an AST-based audit test now validates EVERY
  `FeedbackDialog.information()` `.format()` expression in `ui/`
  (would have caught this and catches future drift); the REAL Project
  Save path is executed in tests (no exception, correct
  Scenes/Characters/Location), plus equivalent coverage for scene
  save, queue save/load, voice export and preset export.

### BS-5: Cross-file bare torch stub poisoned the shared pytest process

- **Status**: **CLOSED** (P3.44.4 round 2 — test environment only)
- **Severity**: Medium (order-dependent suite failures, no production
  impact)
- **Root cause**: `test_p3_43_voice_unification.py` and
  `test_p3_44_history_player_load.py` install a BARE
  `types.ModuleType("torch")` (no `no_grad`); when collected before
  generation-driving files, those files' richer
  `sys.modules.setdefault("torch", fake)` becomes a no-op and every
  generation fails with "module 'torch' has no attribute 'no_grad'"
  (runtime-verified: p3_43 + p3_44_1 in one process = 11 failures,
  on the pre-change tree too — pre-existing, previously masked by the
  chunk composition).
- **Fix**: both stubs are enriched with the standard house-fake
  no-ops (no_grad/manual_seed/from_numpy/cuda) so any import order
  works.

### Verification notes (P3.44.4 round 2)

- Defect-first discipline: both new defect classes were reproduced
  RED first (temporary reintroduction of the old flip and of the
  format-index bug → the new tests fail; restored → green).
- Full regression: 1292/1292 (1271 previous + 21 new; chunked
  683+81+138+114+242+34); launch smoke 11/11 (real entry path);
  focused batteries: new batch file 12/12, new audit file 9/9,
  P3.44.1 22/22, P3.44.2/3/playback 133/133, P3.28 81/81.

---

## P3.44.5 RESEARCH-AUDIT REGISTER (2026-09)

Source: `docs/audits/P3_44_5_RESEARCH_AUDIT.md` (read-only diagnostic
round; every finding was runtime-reproduced before this register was
written).

**CLOSED in P3.44.5:**

- **RA-B+D — Batch dialog stale listener after close→reopen.** CLOSED
  (fixed + regression-locked: 8 lifecycle tests in
  `tests/test_p3_44_5_stabilisation_integrity.py` fail on the pre-fix
  code). Root cause: `closeEvent` released the manager listener; the
  scene-aware reuse path re-showed the still-alive dialog without
  re-registration, leaving it deaf to batch events (frozen pills, stuck
  Start/Pause/Stop button states). Fix: `showEvent` funnels every
  presentation through the idempotent `_connect_manager` plus a full
  state re-sync. BatchManager semantics unchanged.
- **RA-§11/§15 — Provenance identity mixing (positional join).** CLOSED
  (fixed + regression-locked: 9 provenance tests fail on the pre-fix
  code). Root cause: `slot_of_asset` matched assets to expected slots by
  global `part_index` only, so old audio could cover a structurally
  different plan and Combine could assemble stale audio. Fix:
  block-aware join (`block_id` agreement for block-derived slots; kind
  agreement for plain slots; mismatches return `None`; no silent legacy
  downgrade). Legacy block-less scope unchanged.

**OPEN (deferred by P3.44.5 scope discipline — not yet scheduled):**

- **RA-C — Sentence splitter abbreviation/decimal fragmentation**
  (P3.44.2 regression): "GLM 5.2", "U.S.A.", "v1.2", "3.14", "test.hu"
  all fragment in both splitter implementations;
  `_group_sentences` `" ".join` additionally mutates the text (injects
  spaces into the model input).
  **P3.44.8 RESOLUTION: CLOSED.** The boundary rules moved to ONE
  authoritative scanner (`engine/sentence_boundaries.py`) used by
  `NarrationSplitter`, `HeuristicBlockDetector` AND
  `PromptBuilder._detect_conflicts`: punctuation glued to a digit/letter
  is never a boundary (decimals/versions/domains/initials), a single
  ASCII `.` before a lowercase continuation is an abbreviation
  continuation, and sentence grouping joins with ORIGINAL separators
  (span-based exact-text preservation — the `" ".join` mutation is
  gone). Old-code proof: 2/15 defect detectors passed on the P3.44.7
  tree, 15/15 after the fix (controls 4/4 both). Deferred edges (title
  abbreviations, uppercase continuation, sentence-initial lowercase)
  are documented in
  `docs/design/P3_44_8_SENTENCE_SPLITTER_INTEGRITY.md` §10.
- **RA-A2 — `on_text_changed` offset corruption on block-start
  deletions** (the real root of the reported "block delete" symptom):
  the boundary bug shifts block ranges (overlap, wrong text ownership,
  duplicate/fragment parts generated downstream).
  **P3.44.9 RESOLUTION: CLOSED.** `on_text_changed` and
  `on_multi_replace` now transform every edit through the ONE
  authoritative rule `_apply_edit_to_blocks` (edit at/after end →
  untouched; edit before → shift; edit starting inside/at the block →
  head intact, owns the replacement; block starting inside the
  replaced region → surviving tail only or REMOVED; whole-text swap →
  all removed; zero-character range → not a block). Old-code proof:
  54 defect detectors red / 33 controls green → 76/76 green (see
  `docs/design/P3_44_9_BLOCK_OFFSET_DELETE_INTEGRITY.md`).
- **RA-A3 — clamped empty orphan blocks** (`[53:53]`-style invisible
  blocks) survive save/reload.
  **P3.44.9 RESOLUTION: CLOSED.** Zero-character blocks cannot exist
  after any text change (removed with their rows and insertions);
  legacy phantoms are healed on the first edit; save/load round-trips
  are fidelity-exact and phantom-free for structures produced by the
  fixed engine.
- **RA-A4 — positional auto-labelling illusion** (renumbered "Block N"
  labels after deletions create the impression an identifier survived).
  **P3.44.9 RESOLUTION: CLOSED (documented, not a defect).** The B{N}
  gutter label is POSITIONAL by design; block ids (uuids) are the
  stable identity and remain stable through ordinary edits (locked by
  regression). The illusion is resolved by the documentation + the
  structural guarantee that a deleted block leaves NO row at all —
  there is nothing left to mislabel.
- **RA-E — Re-detect semantics**: character-only assignment takes the
  direct rebuild branch and silently destroys Characters;
  `preserve_overrides` loses characters on plain edits without the
  lost-warning; SFX/pause insertions are lost on every path; all block
  ids are replaced on every Re-detect.
  **P3.44.7 RESOLUTION:** the first three mechanisms are FIXED —
  `has_overrides()` now recognises ANY user-authored semantic state
  (Character/label/SFX/pause included), the Character combo marks the
  block manually edited, the similarity match gained a difflib term so
  substitution edits transfer (or record `lost_character_id` when
  ambiguous), a union-coverage SPLIT pass transfers block-wide
  semantics to every child with span-local offset mapping, and SFX/pause
  insertions transfer on every preservation path (see
  `docs/design/P3_44_7_REDETECT_SEMANTIC_PRESERVATION.md`). The fourth
  item (block ids replaced on Re-detect) is BY DESIGN and now explicitly
  safe: semantics migrate through the matching passes, and after the
  next Generate Long a modern asset stamped with an OLD `slot_id` joins
  nothing (P3.44.6 identity-first join — regression-locked). Deferred
  finding recorded: MANUAL split/merge still do not re-allocate SFX/pause
  insertions (user operations, not Re-detect paths).
- **RA-PLAN — GenerationPlan / structure-fingerprint stale-gate**
  (design gap): expected slots carry no source structure fingerprint,
  so a same-block asset whose part decomposition changed can still match
  positionally (documented residual in
  `docs/design/P3_44_5_STABILISATION_NOTES.md` §5).
  **P3.44.6 RESOLUTION:** the residual is CLOSED for modern assets —
  the authoritative structural identity is the existing `slot_id`, now
  stamped on every AudioAsset at generation time and joined
  identity-first (same-block/different-part no longer matches; reorder
  and growth keep genuinely valid membership — see
  `docs/design/P3_44_6_GENERATION_STRUCTURE_IDENTITY.md`). Legacy
  (pre-P3.44.6) assets keep the P3.44.5 positional join by documented
  compatibility necessity (they carry no recorded identity). The
  fingerprint/plan-gate ARCHITECTURE itself was evaluated in the
  P3.44.6 design round and REJECTED as unnecessary (structure identity
  needed one passthrough field, not a fingerprint engine); text
  staleness within a surviving slot remains deliberately undetected
  per the P3.28 §0 product rule (the user alone decides when to
  regenerate). Not scheduled for implementation; effectively subsumed
  by the slot-identity model.
- **RA-UX — Block ≠ Part UI invisibility; duration estimate inflation
  (prompt characters counted, +102% measured); three divergent splitter
  implementations (the `PromptBuilder` conflict detector detaches SFX
  markers from their sentence).**
  **P3.44.8 PARTIAL RESOLUTION:** the third finding is CLOSED — all
  three sentence implementations delegate to the one authoritative
  scanner and the compiled-prompt split keeps SFX/pause tokens attached
  to their sentence (regression-locked,
  `docs/design/P3_44_8_SENTENCE_SPLITTER_INTEGRITY.md` §8). Block ≠
  Part UX and the duration-estimate inflation remain deferred to
  P3.45.

Each OPEN item is evidence-backed in the audit report. Of the report's
recommended fix order, the first two items (join discipline, dialog
re-wiring) were delivered by P3.44.5; the join-discipline item was
COMPLETED by P3.44.6 (identity-first slot_id join — the
fingerprint/plan-gate step is resolved by it, see RA-PLAN above);
Re-detect semantics was COMPLETED by P3.44.7 (see RA-E above);
splitter rules was COMPLETED by P3.44.8 (see RA-C above);
offset boundary fixes were COMPLETED by P3.44.9 (see RA-A2/A3/A4
above); the visual gutter/scroll synchronisation follow-up was
COMPLETED by P3.44.9.1 (see SS-5 below); the remaining order is:
estimate/duplication cleanup (P3.45).

## P3.44.8 round — sentence splitter integrity

### SS-1: punctuation-run scan split decimals/versions/domains/initials

- **Status**: **CLOSED** (P3.44.8; audit item RA-C)
- **Severity**: High (wrong part boundaries; text mutation reaching the
  model input)
- **Root cause (git-proven, commit 9acefbe / P3.44.2)**: the marker
  shielding+attachment rewrite replaced
  `SENTENCE_END_RE.split` (`(?<=[.!?…])\s+`) with a bare `[.!?…]+`
  run scan, losing the whitespace requirement — every `.` glued to a
  digit/letter ("GLM 5.2", "3.14", "v1.2", "test.hu", "U.S.A.") became
  a "sentence boundary"; `HeuristicBlockDetector` had the same
  context-free scan since before P3.44.2; `_group_sentences`'s
  `" ".join` then INSERTED a space inside the split token
  ("A GLM 5.2-t használtam." → "A GLM 5. 2-t használtam.") and
  normalised newlines/multi-spaces.
- **Fix**: `engine/sentence_boundaries.py` — the ONE authoritative
  boundary contract (SHIELDING / ATTACHMENT / CONTEXT / ABBREVIATION
  rules) + span-based exact-text units; all three implementations
  delegate; grouping joins with original separators. 62 permanent
  regression tests; old-code proof 2/15 → 15/15; full battery
  1469/1469; launch smoke 11/11. Deferred edges documented in the
  design doc §10 (title abbreviations, uppercase continuation,
  sentence-initial lowercase, space-containing onomatopoeia in the
  compiled representation).

## P3.44.9 round — block offset / delete integrity

### SS-2: block-start deletion shifted ranges into preceding text

- **Status**: **CLOSED** (P3.44.9; audit items RA-A2 + RA-A3)
- **Severity**: Critical (structural corruption: overlap, wrong text
  ownership, duplicated generation text, phantom blocks surviving
  save/load)
- **Root cause**: `NarrationBlockManager.on_text_changed` classified
  blocks with `change_start <= block.start` → shift BOTH ends — correct
  only for edits entirely before the block. A deletion starting exactly
  at a block boundary shifted the block into preceding text
  (`[53:99] → [7:53]`); blocks starting inside a replaced region were
  shifted the same way; an edit extending past a block's end was
  clamped, discarding surviving characters and leaving `[x:x)` phantom
  rows. `on_multi_replace` (Replace All) had the sibling defect
  (match covering a block start left the start unmapped; match exactly
  covering a block left a phantom).
- **Fix**: ONE authoritative rule `_apply_edit_to_blocks` shared by
  `on_text_changed` and `on_multi_replace` (untouched / shift /
  head-intact-owns-replacement / surviving-tail-only / removed /
  whole-text-swap / zero-character-block-does-not-exist) + insertion
  offsets ride the same mapping + `set_text` resets the block model
  (programmatic swap contract). 76 permanent regression tests + 35
  subtests; old-code proof 54 red / 33 controls → 76/76; full battery
  1575 passed; launch smoke 11/11. Design record:
  `docs/design/P3_44_9_BLOCK_OFFSET_DELETE_INTEGRITY.md`.

### SS-3 (pre-existing, recorded): P3.37 README documentation test mismatch

- **Status**: **OPEN** (not a P3.44.9 defect — evidence: identical
  failure with the P3.44.9 changes stashed AND with `README.md` at its
  last commit `cd5e122`)
- **Severity**: Low (test-only): `test_p3_37_branding_documentation.py
  ::test_readme_has_british_english_section` expects the header
  `# EN | British English`, which no longer exists in the README
  structure rewritten by commit `cd5e122` ("Update README.md").
- **Disposition**: deferred — fixing the README header or the test is
  documentation work outside the P3.44.9 offset scope.

### SS-4 (pre-existing, recorded): flaky regen-stop timing test

- **Status**: **OPEN** (flaky; not a P3.44.9 defect — evidence: fails
  3 of 4 runs on the pristine tree, identical signature)
- **Severity**: Low (test-only): `test_p3_44_4_batch_execution_run.py
  ::test_regen_run_stop_leaves_others_pending` intermittently marks the
  regen job FAILED under suite load; the test itself documents the
  still-busy-engine retry chain.
- **Disposition**: deferred — needs a deterministic engine-busy barrier
  in the harness (test infrastructure, outside the phase scope).

## P3.44.9.1 round — block gutter / text scroll synchronisation

### SS-5: block gutter labels desynchronised from the scrolling text viewport

- **Status**: **CLOSED** (P3.44.9.1)
- **Severity**: Medium (visual only — no block data was ever affected)
- **Symptom**: with 30+ blocks, scrolling left the gutter labels
  frozen near the top (B1..B12 painted at the scroll=0 rows while the
  visible text was far below); any forced repaint at depth produced a
  stacking cascade (each label one MIN_ROW_HEIGHT below the previous
  ROW instead of at its own block's text; measured 25 labels in an
  11-block band, B15 170 px below its own text in the real-MainWindow
  probe).
- **Root cause** (three visual-layer components, evidence-first
  probes `ss/p34491_*.py`): (1) `QPlainTextEdit.updateRequest` was
  connected ONLY to the LineNumberArea — the block gutter never
  repainted on scroll (frozen image); (2) the P3.41 clamp
  `max(y_top, prev_bottom + 1)` chained unconditionally — blocks
  scrolled off above the viewport were dragged back INTO the band;
  (3) the same clamp chained across paragraphs for sentence-pair
  blocks sharing a wrapped line. Case A — completely independent of
  the P3.44.9 offset engine (scrolling fires no text-change path;
  ids/offsets/owned text byte-identical across scroll cycles, pinned
  by permanent control tests).
- **Fix**: `ui/panels/narration_editor.py` only — (a)
  `_update_line_number_area` override repaints the gutter on the SAME
  updateRequest path (no timers/polling; construction-order guard for
  the signals emitted during base-class construction); (b)
  `_block_rows()` stacks only on genuine degenerate adjacency
  (block text starting on the same/adjacent TEXT line as the previous
  block's text end) — separated blocks anchor at their OWN text start
  in viewport coordinates; (c) a monotonic trim pass keeps rows
  strictly disjoint against non-monotonic lazy-layout estimates.
  19 permanent regression tests (incl. two real Engine+MainWindow+
  auto-detect full-stack tests); old-code proof 14 red / 5 controls
  (top alignment, scroll data purity, edit identity, P3.41 adjacency,
  P3.41 click consistency) → 19/19. Design record:
  `docs/design/P3_44_9_1_BLOCK_GUTTER_SCROLL_SYNC.md`.

### SS-6 (environment note, recorded): GL-shim path affects DPI probe tests

- **Status**: **OPEN** (environment, not an application defect)
- **Severity**: Low (test-only): `tests/p340_dpi_probe.py` hard-codes
  the GL-shim library path `/tmp/gllibs/usr/lib/x86_64-linux-gnu`.
  When the shim is extracted to a different location the 3 DPI tests
  fail in the sandbox harness with an identical signature on the
  pristine tree; aligning the shim path restores 26/26 green. A
  windowed desktop run does not need the shim at all.
- **Disposition**: deferred — the hard-coded path mirrors the
  documented sandbox recipe (re-verified in the P3.44.9.1
  environment rebuild); making the probe search the standard
  `LD_LIBRARY_PATH` is test-infrastructure polish for a later phase.

## P3.45 round — triage / design gate (no implementation)

### P3.45-GATE — Block → Part UX / oversized preflight / duration estimation / rendering hardening: scoped and sequenced

- **Status**: **OPEN** (design gate passed; implementation not started)
- **Scope**: full-repository reconnaissance at `eadbfff` (four
  read-only exploration passes + direct re-verification of every
  load-bearing claim, including the re-verification of the reused
  P3.44.5 audit findings). Design record:
  `docs/design/P3_45_TRIAGE_DESIGN.md`.
- **Outcome**:
  - The deferred RA-UX components (Block ≠ Part UI invisibility;
    duration estimate inflation) now have a concrete, evidence-based
    plan — see the design record §2/§3/§4/§7.
  - The editor gutter's per-block generation-state API
    (`update_block_status` + badge painter) is DEAD CODE (zero
    callers) while the data source (`block_slot_states`) exists and
    is tested — wiring them is the recommended first slice
    (P3.45.1), presentation-only.
  - The real oversized-part constraint is the `max_new_tokens/25fps`
    output ceiling (default 4096 → 163.84 s; the P3.27B observed
    defect sat at 99.83 % of it). Two silent bypasses exist (uncapped
    single sentences > 400 chars; `$SPEAKER` turns with no size
    check); truncation at the ceiling CANNOT be flagged by the
    output guard. Preflight = pure classification + preview
    surfacing; no auto-splitting (P3.45.2).
  - Duration estimation: one formula, two text bases (plain vs
    tokenized prompt — the measured +102 % inconsistency); the
    estimate is not recomputed after preview edits; the assemble
    dialog mislabels measured totals as "estimated". Only
    consistency fixes are justified; the constant is NOT
    recalibrated without a measured corpus (P3.45.3).
  - Rendering hardening: proven defects separated from cosmetic
    preferences — duplicated `engine.app_root` property,
    index-keyed `_check_states` (audit L-3, still present),
    redundant `toPlainText()` copies per paint, no-op-click full
    viewport repaints, flash-timer viewport repaints, hardcoded
    block-stripe colours, dead `modern_control_panel.py`. The
    transparency-stack/GL text question stays behind an escalation
    gate (§12 of the design record).
- **Explicitly frozen / escalated** (see design record §12): the
  P3.44.9 offset engine, the P3.44.9.1 gutter geometry/scroll paths,
  the slot_id/generation_run/part_version identity model, the
  append-only asset doctrine, Batch workspace semantics; no
  GenerationPlan/fingerprint/STALE revival; no estimator
  recalibration without data; Manual Concatenate semantics, history
  `slot_id` recording and `_check_states` re-keying each require
  their own analysis before any change.
- **Next**: P3.45.1 implementation decision (block generation state
  in the editor gutter — smallest, both endpoints already exist and
  are tested).

## P3.45.1 round — editor gutter generation-state visibility (presentation-only wiring)

### P3.45.1 — Block generation-state badges were dead code (CLOSED)

- **Status**: **CLOSED** by P3.45.1 (commit on `main`, see
  DEVELOPMENT_LOG.txt P3.45.1). The P3.45-GATE headline finding —
  `update_block_status` + badge painter dead (zero callers) while
  `block_slot_states` exists and is tested — is now wired:
  `MainWindow._sync_block_status_badges` derives per-block state
  (done / generating / error / none, with multi-part coverage
  "✓ N/N" / "⚙ N/N") at five state-transition points (blocks_changed,
  generation finished, generation failed, batch completed, Generate
  Long start). No new identity system, no polling, no geometry
  changes; P3.44.9/.9.1 suites re-verified green (95 + 35 subtests).
  Full battery: 1583 passed + 1 (SS-3, pre-existing) + 35 subtests =
  the 1563 baseline + exactly the 20 new tests.
- **Residuals (recorded, NOT defects of this slice; each needs its
  own decision before any change)**:
  1. "generating" is scene-level: review mode shows generating badges
     before any job runs (same semantics as the scene's own sidebar
     badge since P3.28). Per-job badge granularity = job-level events
     at MainWindow (deferred with P3.45.2+).
  2. A regenerating covered block keeps "done" during the regen —
     the append-only doctrine keeps the previous version valid; the
     Batch window shows the actual job progress.
  3. Re-detect clears all badges while the slot structure keeps the
     OLD block ids until the next Generate Long (the pre-existing
     P3.45-GATE finding; badge honestly presents the detach; slot
     re-materialisation on Re-detect is a P3.45.2+ structure
     decision).
  4. Partial idle blocks (some parts covered, no run in flight) show
     NO badge — the existing vocabulary has no honest partial
     representation; nothing is claimed, the Batch window holds the
     detail. A dedicated partial presentation would extend the
     taxonomy (deferred).
- **Environment note (recorded)**: pixel-level scroll-attachment
  measurement is not achievable offscreen (the lazy
  QPlainTextDocumentLayout stays in-flight — the documented P3.44.9.1
  harness raciness); attachment is guaranteed structurally (badge
  painted in the same pass at the same row `gy`), pinned by the
  P3.44.9.1 suite and the new scroll-purity test, and confirmed
  visually via rendered-PNG VLM inspection (4 renders, exact expected
  states read back).

No backlog items were removed in this round.

## P3.45.2A round — oversized block preflight (single-output ceiling)

### P3.45.2A — Oversized generation requests bypassed every size check; truncation at the token ceiling is invisible (CLOSED for the UI generation paths)

- **Status**: **CLOSED** by P3.45.2A (commit on `main`, see
  DEVELOPMENT_LOG.txt P3.45.2A). The P3.45-GATE finding — "the real
  oversized-part constraint is the `max_new_tokens/25fps` output
  ceiling (default 4096 → 163.84 s); two silent bypasses exist; the
  output guard cannot flag truncation at the ceiling" — now has a
  BEFORE-generation classification layer:
  `engine.output_guard.preflight_generation_size(text,
  max_new_tokens)` (pure function; the ONE source of the ceiling
  arithmetic, alongside the unchanged P3.27B post-generation guards).
  SAFE / WARNING (exactly at the ceiling — no invented percentage
  band) / BLOCKED (estimate above the ceiling), decided by exact
  rational arithmetic (`chars*25` vs `tokens*15` — no float
  equality), on the ACTUAL request basis (compiled prompt / part
  text / job prompt) with the EFFECTIVE per-request token budget.
  BLOCKED raises ONE truthful confirmation (default No) at: single
  Generate, Generate Long preview (per-part ⚠ markers + Generate-click
  re-check of the EDITED text — closes both splitter bypasses),
  Batch manual start, Batch selective start (flagged jobs reported;
  neighbours never corrupted), and the existing per-row regen
  confirmation. Declining aborts before ANY mutation (scene status,
  submission, queue, versions). NO auto-split, NO text/block/job
  mutation anywhere (pinned by byte-identity tests). The misleading
  "4096 ≈ 30s / 15s" comments are corrected in the three touched
  places only. Full battery at the implementation commit: focused
  42/42 (twice); frozen P3.45.1 + P3.44.9 + P3.44.9.1 + P3.27B +
  P3.28 suites 210 passed + 35 subtests; full suite 1530 passed +
  1 (SS-3, pre-existing) + 35 subtests; verify_compile 149/149;
  architecture PASS; functional integrity 80/80; launch smoke 11/11.
- **Residuals (recorded, NOT defects of this slice; each needs its
  own decision before any change)**:
  1. Direct `Engine.generate()` callers with no UI call site
     (scripts, future automation) are not preflighted — the preflight
     lives at the presentation/generation-entry layer where the user
     decision point exists.
  2. The estimate basis is the existing ~15 chars/sec heuristic
     (consumed as-is, P3.45.2B territory): it can misclassify in
     both directions; every message discloses which number is the
     estimate and which is the real limit.
  3. Post-generation truncation remains undetectable — the model API
     returns only a waveform (no token/frame count, no finish
     reason anywhere in the chain); P3.45.2A deliberately does not
     fabricate an inference. If a future engine exposes metadata,
     consume it explicitly (documented in the design record §7/§11).
  4. WARNING has no softer band (e.g. "90 % of ceiling"): a
     percentage threshold would be invented without data — remains
     a later UX/data decision.
  5. No model-specific limit API exists today; future backends with
     different frame rates / per-model ceilings must be consumed
     through the result's `effective_*` fields.
- **Environment note (recorded)**: `test_p3_43_voice_unification.py`
  and `test_p3_44_history_player_load.py` fail COLLECTION in the
  current venv (`torch.__spec__ is None` after the house stub
  install) — proven identical at baseline `2e2cb46` via `git stash`
  (environment drift: torch is no longer importable; both files were
  green in the P3.45.1-era environment). NOT a P3.45.2A regression;
  restoring a torch-bearing venv (or enriching the two stubs with
  `__spec__`) is an environment task, not a product change.

No backlog items were removed in this round.
