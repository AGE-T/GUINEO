# SpeechStudio Development Governance Document

**Status:** Active  
**Date:** 2025-01-15  
**Purpose:** Establish a regression-safe development workflow for all future SpeechStudio development  

---

## Phase 1: Development Process Audit

### The Problem

During recent development iterations, completed features have repeatedly disappeared from the codebase without warning. The project still compiles, no syntax errors occur, no runtime exceptions fire — but previously implemented functionality silently vanishes. Recovery builds restore some missing functionality while accidentally removing other completed work.

### Root Cause Analysis

**1. No version control safety net.**  
The project has no `.git` directory. Every file change is a destructive overwrite. There is no commit history, no diff, no rollback capability. If a file is regenerated from an older state (by the AI assistant, by a tool, or by accident), the newer content is permanently lost.

**2. No build verification between sprints.**  
Features are implemented in sequence without verifying that previous features still exist after each change. A regression introduced in Sprint N is not discovered until Sprint N+3, making it impossible to know which change caused the loss.

**3. No feature inventory.**  
There is no authoritative list of what features exist. Without an inventory, there is nothing to check against. A missing feature is invisible because nobody remembers it was supposed to be there.

**4. Full-file regeneration risk.**  
When the AI assistant writes a complete file (using `Write` rather than `Edit`), it replaces the entire file content. If the assistant's context does not include the most recent state of that file, the regeneration produces an older version — silently deleting newer work.

**5. No mandatory regression checklist.**  
Each sprint ends with a "regression validation" that checks for the *new* feature's presence, but does not systematically verify *every existing feature* is still present. The validation is additive, not exhaustive.

**6. Context window resets.**  
When the conversation context is reset or summarized, the AI assistant loses awareness of files created in previous sessions. It may regenerate files from the summarized state, omitting features that were added after the summary's cutoff.

### Why Existing Code Can Be Unintentionally Replaced

| Mechanism | How it causes regression |
|-----------|------------------------|
| `Write` tool overwrites entire file | If the assistant's context is stale, the new content omits recent additions |
| No git history | No way to diff or recover the previous state |
| No pre-write checksum | No verification that the file being overwritten matches the assistant's mental model |
| Large files regenerated wholesale | More surface area for accidental omission |
| No "must not remove" assertions | No automated check that existing features survive a write |

### Current Practices That Allow Regressions

1. **Trusting the context window** — The assistant assumes its in-memory representation of a file is current. It may not be.
2. **Full-file writes instead of targeted edits** — Using `Write` when `Edit` would suffice increases the blast radius.
3. **No post-write verification** — After writing a file, there is no check that all previously-present functions/classes still exist.
4. **No feature registry** — There is no machine-readable list of features that must be preserved.
5. **No build report** — There is no structured record of what changed, what was added, and what was removed in each build.

### Verification Steps Currently Missing

1. **Pre-write state capture** — Before overwriting a file, the assistant does not record what was there.
2. **Post-write diff check** — After writing, there is no comparison to verify only intended changes were made.
3. **Feature presence check** — After a build, there is no automated verification that every registered feature still exists.
4. **Cross-file reference check** — No verification that imports and references to newly created modules still resolve.
5. **Build report generation** — No structured output documenting what the build changed.

### Architectural Weaknesses That Increase Regression Risk

1. **Monolithic `main_window.py`** (~1270 lines) — Any change to MainWindow risks touching unrelated handlers.
2. **No module-level isolation** — Features are spread across multiple files with no clear ownership boundaries.
3. **Signal-based coupling** — Qt signals create implicit dependencies that are invisible in import graphs.
4. **No test suite** — There are no automated tests to catch regressions; all verification is manual or pattern-matching.

---

## Phase 2: Feature Inventory

The following inventory reflects the **current actual state of the codebase as observed on disk** (not the intended state from the conversation history).

### How to Read This Inventory

- **Implemented** — Feature exists in the codebase and is functional.
- **Partially Implemented** — Feature exists but is incomplete or has known gaps.
- **Planned** — Feature is discussed in specs/ADRs but does not exist in code.
- **Deprecated** — Feature exists but is scheduled for removal.
- **Removed** — Feature previously existed but has been deleted from the codebase.
- **Experimental** — Feature exists but is not stable.

### Current Feature Registry (as observed on disk)

| Feature | Status | Evidence |
|---------|--------|----------|
| **Core UI** | Implemented | `MainWindow` with 5-region layout exists |
| **Menu Bar** | Implemented | `menu_bar.py` with File/Generation/Tools/View/Help menus |
| **Toolbar** | Implemented | `toolbar.py` with generate/stop/replay/etc. |
| **Sidebar** | Implemented | `sidebar.py` with Voices/History/Presets/Outputs tabs |
| **Control Panel** | Implemented | `control_panel.py` with Voice/Emotion/Style/Prosody/SFX/Generation/Reference/Preview sections |
| **Narration Editor** | Implemented | `narration_editor.py` with 3 modes (Plain/Blocks/Preview) |
| **Narration Blocks** | Implemented | `narration_blocks.py`, `narration_block_manager.py`, `block_detector.py` |
| **Block Gutter Rendering** | Implemented | `BlockGutter` class with line-number-style painting |
| **Block Properties Panel** | Implemented | Override checkboxes, status labels, split/merge/delete |
| **Override Removal (3-state)** | Implemented | Unchecking clears to None, status shows "Inherited" |
| **Narration Templates** | Partially Implemented | `NarrationTemplate` + `BUILTIN_TEMPLATES` defined, but no UI to apply them |
| **Prompt Builder** | Implemented | `prompt_builder.py` with `build()` and `build_from_emissions()` |
| **Prompt Optimizer** | Implemented | `prompt_optimizer.py` with `resolve_and_optimize()` and `optimize_emissions()` |
| **Prompt Preview** | Implemented | `PromptPreviewSection` in control panel |
| **Prompt Pipeline (unified)** | **NOT Implemented** | `_build_prompt()` method does not exist; preview and generation use different paths |
| **Engine (no PromptBuilder)** | **NOT Implemented** | Engine still has `"<\|"` heuristic in `_execute_generation` |
| **Pipeline Verification Logging** | **NOT Implemented** | No SHA-256 hash logging at pipeline stages |
| **Voice Library** | Implemented | `voice_manager.py` + `voice_library.py` dialog |
| **Voice Cloning** | Implemented | `generation_manager.py` loads reference audio |
| **History** | Implemented | `history_manager.py` + sidebar History tab |
| **Playback** | Implemented | `audio_manager.py` play/stop methods |
| **Waveform Player** | Implemented | `waveform_player.py` with play/pause/stop/seek/volume |
| **Settings Dialog** | Implemented | `settings_dialog.py` with 7 tabs including Model Precision |
| **Model Precision** | Implemented | bf16/fp16/fp32 radio buttons in settings, read by `model_manager.py` |
| **Version System** | Implemented | `version.py` with auto-increment build number |
| **Benchmark Dialog** | Implemented | `benchmark_dialog.py` |
| **Output Normalization** | Implemented | LUFS-based normalization in `audio_manager.py` |
| **Audio Analysis** | Implemented | `analyze_audio()` in `audio_manager.py` |
| **32-bit Float WAV** | Implemented | `save_wav()` with soundfile + wave fallback |
| **Show Prompt Blocks Toggle** | Implemented | View menu checkable action |
| **Inheritance Mode Banner** | **NOT Implemented** | `set_inheritance_mode()` does not exist in current code |
| **ControlPanel Setters** | **NOT Implemented** | No `set_emotion()`, `set_speed()`, etc. on ControlPanel |
| **ControlPanel batch_update()** | **NOT Implemented** | No transaction context manager |
| **Advanced Prompt Editor** | **NOT Implemented** | `advanced_prompt_editor.py` does not exist |
| **Raw Prompt Mode** | **NOT Implemented** | No raw mode bypass in prompt building |
| **Batch Generation** | **NOT Implemented** | `batch_manager.py` does not exist |
| **Batch Sequence Mode** | **NOT Implemented** | `batch_generation.py` does not exist |
| **Batch YAML Import/Export** | **NOT Implemented** | No `save_to_file()` / `load_from_file()` |
| **Batch Freeze Fix (marshal_to_ui)** | **NOT Implemented** | `marshal_to_ui` field does not exist |
| **Preset System** | Implemented | `engine/preset_manager.py` exists; `MainWindow._on_save_current_as_preset` / `_on_apply_preset` / `_on_delete_preset` / `_on_rename_preset` / `_on_import_preset` / `_on_export_preset` wired to File menu + PresetManagerDialog. Storage: `<app_root>/presets/`. |
| **Preset UI (sidebar/dialog)** | Implemented | Legacy `sidebar.py PresetsTab` + new `preset_manager_dialog.py` (reached via ProjectSceneSidebar → Library → Presets). Each row shows the human-readable voice NAME (not voice_id). |
| **ADR_001 (Preset Architecture)** | Implemented | `docs/adr/ADR_001_Preset_Architecture.md` exists. |
| **Project Manifest** | Planned | Discussed in ADR discussion, not implemented |

### Critical Observation

Multiple features that were implemented during this conversation session **do not exist on disk**. This confirms the regression problem is real and active. The codebase appears to have been reset to a state from before the pipeline verification sprint, losing:

- Unified `_build_prompt()` pipeline
- Engine heuristic removal
- Pipeline verification logging
- ControlPanel setter API + batch_update()
- Inheritance mode banner
- Advanced Prompt Editor + Raw Prompt Mode
- Batch Generation (all components)
- Preset System (all components)
- ADR_001 document

---

## Phase 3: Impact Analysis Protocol

**Before modifying any source file, the following dependency report must be produced.**

### Required Pre-Implementation Report

For every file to be modified:

```
FILE: <path>
REASON FOR CHANGE: <why this file must change>
DEPENDENT SYSTEMS: <which existing systems import or reference this file>
REGRESSION RISKS:
  - <risk 1>
  - <risk 2>
UNRELATED FUNCTIONALITY THAT COULD BE AFFECTED:
  - <functionality 1>
  - <functionality 2>
```

### Rules

1. **No implementation begins until this report is complete and reviewed.**
2. **If a file has more than 5 dependent systems, the change requires explicit confirmation before proceeding.**
3. **If a change touches a "stable system" (one marked Implemented in the Feature Registry), a regression check must be performed immediately after the change.**
4. **Full-file rewrites (`Write` tool) are prohibited for files larger than 200 lines. Use targeted `Edit` operations instead.**

---

## Phase 4: Implementation Rules

### Order of Operations

Every feature implementation must follow this sequence:

1. **Identify affected files** — List every file that will be created or modified.
2. **Identify affected systems** — List every feature from the Feature Registry that depends on these files.
3. **Produce the Impact Analysis** (Phase 3).
4. **Implement only the required modifications** — Do not rewrite unrelated code.
5. **Do not replace existing implementations unless explicitly required.**
6. **Avoid touching stable systems.** If a stable system must be modified, document why.
7. **If a larger refactoring becomes necessary, STOP and ask for confirmation before continuing.**

### Prohibited Actions

- **No full-file regeneration of existing files** unless the file is being created for the first time.
- **No deleting functions or classes** that are still referenced elsewhere.
- **No renaming** without updating all references in the same change.
- **No removing imports** without verifying they are unused.
- **No "cleanup" edits** that are not directly related to the current feature.

### Required Actions

- **Read before write** — Always `Read` a file immediately before `Edit`ing it, to ensure the latest on-disk state is in context.
- **Prefer `Edit` over `Write`** — Targeted edits have smaller blast radius.
- **Verify after write** — After writing, verify the file compiles and key functions still exist.

---

## Phase 5: Regression Verification Protocol

### Mandatory Post-Implementation Check

After every implementation, perform a complete regression review comparing the Previous Build to the Current Build.

### Required Report

```
REGRESSION REPORT
=================

NEW FEATURES:
  - <feature 1>
  - <feature 2>

MODIFIED FEATURES:
  - <feature>: <what changed>

REMOVED FEATURES:
  - <feature> (if any — STOP if unexpected)

UNEXPECTED CHANGES:
  - <change> (if any — STOP if found)

KNOWN LIMITATIONS:
  - <limitation>
```

### Feature-by-Feature Check

Every feature in the Feature Registry must receive one of the following results:

| Result | Meaning |
|--------|---------|
| **PASS** | Feature exists and is unchanged from previous build |
| **CHANGED** | Feature exists but was intentionally modified — documented and expected |
| **REGRESSION** | Feature is missing or broken — **STOP immediately** |
| **NOT TESTED** | Feature could not be verified in this build |

### Stop Conditions

If **ANY** feature marked "Implemented" in the Feature Registry receives a result of **REGRESSION**, implementation must **stop immediately**:

1. Do not continue adding features.
2. Investigate the regression — determine exactly when it was introduced.
3. Restore the missing functionality before continuing.
4. Re-run the regression check to confirm the restoration.

---

## Phase 6: Feature Registry

### Format

The Feature Registry is a YAML file at `docs/governance/feature_registry.yaml`. It is the authoritative inventory of the project.

```yaml
# SpeechStudio Feature Registry
# This file is the authoritative inventory of all features.
# No completed feature should silently disappear without being detected.

version: 1
last_updated: "2025-01-15"

features:
  core_ui:
    status: implemented
    files: [ui/main_window.py]
  
  narration_blocks:
    status: implemented
    files: [engine/narration_blocks.py, engine/narration_block_manager.py, 
            engine/block_detector.py, ui/panels/narration_editor.py]
  
  advanced_prompt_editor:
    status: not_implemented
    files: []
  
  # ... (full registry below)
```

### Rules

1. **Every feature must be listed** — if it's not in the registry, it doesn't exist.
2. **Status must be kept current** — update the registry after every build.
3. **The registry is checked at the start of every build** — to establish the baseline.
4. **The registry is checked at the end of every build** — to detect regressions.

---

## Phase 7: Build Report

### Mandatory Report Format

Every completed build must end with a structured report:

```
BUILD REPORT
============

Build Number:     <N>
Version:          <X.Y.Z>
Date:             <YYYY-MM-DD>

Modified Files:
  - <file 1> (created | modified | deleted)
  - <file 2> (created | modified | deleted)

New Features:
  - <feature 1>
  - <feature 2>

Fixed Bugs:
  - <bug 1>

Regression Status:
  - <feature A>: PASS
  - <feature B>: CHANGED (<what changed>)
  - <feature C>: REGRESSION (<what broke>) — STOP if found

Known Issues:
  - <issue 1>

Remaining Technical Debt:
  - <debt 1>

Architectural Changes:
  - <change 1>

Open Questions:
  - <question 1>

Implementation Confidence: HIGH | MEDIUM | LOW
  (Reason: <why this confidence level>)
```

### Rules

1. **No build is considered complete without this report.**
2. **Implementation Confidence below HIGH requires explanation.**
3. **Any REGRESSION result blocks the build from being accepted.**

---

## Phase 8: Recovery Rules

### If an existing feature disappears during implementation:

1. **Stop immediately.** Do not continue implementing additional features.
2. **Determine exactly when the regression was introduced** — identify the specific file write that caused the loss.
3. **Restore the missing functionality before continuing.**
4. **Do not create recovery builds that may overwrite newer work.** Recovery must be surgical — restore only the missing feature, do not regenerate the entire file.
5. **Always recover from the most recent verified implementation state.** If the most recent state is unknown, recover from the Feature Registry's last-known-good description.
6. **After recovery, re-run the full regression check** to confirm no other features were lost.

### Recovery Prohibition

- **Never use a full-file `Write` to recover.** Always use targeted `Edit` operations to restore only the missing code.
- **Never recover from conversation context alone.** Always verify against the on-disk state first (`Read` the file before recovering).
- **Never assume a file's content.** Always read it before modifying.

---

## Phase 9: Incremental Development Rules

### Milestone-Based Development

Large features must be divided into small, independently verifiable milestones.

1. **Each milestone must compile.**
2. **Each milestone must pass regression verification.**
3. **Each milestone must be accepted before the next one begins.**
4. **Avoid implementing multiple unrelated systems within the same build.**

### Milestone Format

```
MILESTONE: <name>
PARENT FEATURE: <feature name>
FILES AFFECTED:
  - <file 1>
  - <file 2>
CHANGES:
  - <change 1>
  - <change 2>
VERIFICATION:
  - Compiles: YES/NO
  - Regression check: PASS/FAIL
  - Feature present: YES/NO
ACCEPTANCE: PENDING | ACCEPTED | REJECTED
```

### Rules

- **One milestone per build.** Do not bundle unrelated changes.
- **If a milestone fails regression, the build is blocked** until the regression is resolved.
- **Milestones must be small enough to verify manually** — if a milestone touches more than 5 files, it should be split further.

---

## Implementation: Feature Registry File

The following file must be created at `docs/governance/feature_registry.yaml` and maintained as the authoritative inventory:

(A separate file will be created for this — see below.)

---

## Acceptance Criteria

This governance document is accepted when:

1. The Feature Registry file exists at `docs/governance/feature_registry.yaml`.
2. The registry accurately reflects the current on-disk state of the codebase.
3. The user has reviewed and approved the governance workflow.
4. All future builds follow the Implementation Rules (Phase 4) and Regression Verification (Phase 5).
5. All future builds produce a Build Report (Phase 7).

---

## Acknowledgment of Current State

**As of this writing, the codebase has lost significant completed work.** The following features were implemented during this conversation session but **do not exist on disk**:

- Unified `_build_prompt()` pipeline
- Engine `"<|"` heuristic removal
- Pipeline verification logging (SHA-256)
- ControlPanel setter API + `batch_update()` context manager
- Inheritance mode banner
- Advanced Prompt Editor + Raw Prompt Mode
- Batch Generation (manager, UI, YAML save/load, freeze fix)
- Preset System (manager, UI, sidebar integration)
- ADR_001 document

**Before any new development begins, these features must be restored.** The Feature Registry will track the restoration progress.

---

## Revision History

| Date | Version | Description |
|------|---------|-------------|
| 2025-01-15 | 1.0 | Initial governance document created after discovery of major regression |
