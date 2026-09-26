# ADR 002: Recent Architectural Decisions (Tasks 20–FINAL-CORRECTION)

- **Status**: HISTORICAL — Partially Superseded by ADR 003
- **Date**: 2025-01 (consolidated)
- **Decision Maker**: SpeechStudio engineering
- **Supersedes**: None (extends ADR 001)
- **Superseded by**: `ADR_003_Current_Prompt_Architecture.md` (prompt
  architecture decisions only — specifically Decision 3 "Canonical
  Prompt Pipeline" and the Read Tokens / SemanticTokenState model)
- **Related**: `docs/governance/architecture_manifest.yaml`,
  `docs/governance/feature_registry.yaml`

> **⚠️ HISTORICAL DOCUMENT NOTICE**
>
> This ADR records architectural decisions made during the Tasks 20–
> FINAL-CORRECTION development cycle. It is preserved for historical
> context and to explain *why* the code reached its current state.
>
> **Parts of this ADR have been superseded by ADR 003.** Specifically:
>
> - **Decision 3 (Canonical Prompt Pipeline)** documented the
>   `SemanticTokenState` / Read Tokens architecture as the intended
>   model for managing manual Higgs tokens in the editor. This model
>   was subsequently **reconsidered and replaced** by the simpler
>   Managed Mode / Raw Mode ownership split documented in ADR 003.
>   The `SemanticTokenState` and `CanonicalPromptCompiler` classes
>   still exist in the code, but the Read Tokens gate is no longer
>   the intended user workflow for Raw Mode.
>
> - **Decision 3e (Read Tokens gate)** described a future feature
>   where manual tokens would not be active until the user pressed
>   "Read Tokens". This is **not the current intended UX**. See
>   ADR 003 for the current Raw Mode ownership model.
>
> The other decisions in this ADR (safe update mechanism, multi-
> speaker dialogue system, narration block offset integrity, long
> narration UI/CTA consistency) remain current and are not
> superseded.
>
> **Do not treat this ADR as the current architectural source of
> truth for prompt pipeline decisions.** Refer to ADR 003 instead.

## Context

Over a sustained development cycle (Tasks 20 through FINAL-CORRECTION)
SpeechStudio evolved from a single-speaker TTS tool into a multi-speaker
narration studio with a canonical prompt pipeline, narration blocks,
and a long-narration batch system. Many of these changes were
implemented incrementally and only documented in the worklog.

This ADR consolidates the architectural decisions made during that
period into a single reference. Each decision is recorded with its
context, the chosen solution, the rejected alternatives, and the
consequences. This ensures future maintainers understand *why* the
code is structured the way it is — not just *what* it does.

The decisions are grouped into five themes:

1. Safe update mechanism (data-loss prevention)
2. Multi-speaker dialogue system
3. Canonical prompt pipeline
4. Narration Block offset integrity
5. Long Narration UI and CTA consistency

---

## Decision 1: Safe Update Mechanism (Task 20)

### Context

The `SpeechStudio_clean.zip` distribution contained empty `.gitkeep`
placeholder files in `settings/`, `voices/`, `outputs/`, `presets/`,
`models/`, `logs/`, `temp/`, `.cache/`. When users deleted their
existing folder and extracted fresh (the most common Windows pattern),
**all personal data was wiped** — including the 8GB Higgs model,
imported voice profiles, generation history, and presets.

### Decision

Created `update.bat` — a Windows batch script that safely updates
**only the Python source code** from the zip, while preserving all
personal data.

**How it works:**
1. Verifies `SpeechStudio_clean.zip` is in the same folder and that
   `SpeechStudio.py` exists (sanity check).
2. Backs up the current source code (`engine/`, `ui/`, `tools/`,
   `spec/`, `docs/`, `research/`, top-level `.py`/`.txt`/`.bat` files)
   to a timestamped `_backup_source_YYYYMMDD_HHMM\` folder.
3. Uses PowerShell's `System.IO.Compression.ZipFile` to selectively
   extract **only** `.py`, `.txt`, `.bat`, `.md`, `.yaml`, `.yml` files.
4. Explicitly **skips** any file in: `settings/`, `voices/`, `outputs/`,
   `presets/`, `models/`, `logs/`, `temp/`, `.cache/`, `__pycache__/`.
5. Reports extracted vs. skipped file counts and shows the backup
   location.

The zip itself was also updated to exclude `settings/history/*`
(previously only `settings.json` was excluded).

### Alternatives Considered

- **A full installer (NSIS/Inno Setup).** Rejected — too heavy for a
  Python desktop app; the user base is small and technical.
- **An auto-updater that downloads patches.** Rejected — requires a
  hosting backend and network reliability; the current zip-based
  distribution is simpler.
- **Git-based updates.** Rejected — most users are non-developers who
  don't have Git installed.

### Consequences

- **Positive**: Users can update safely without losing data. The
  script is a single double-click — no command line needed.
- **Positive**: The backup folder provides a rollback path if a new
  version is broken.
- **Negative**: The script is Windows-only. macOS/Linux users must
  update manually (extract source files only).
- **Negative**: The zip must be maintained carefully — any personal
  data that accidentally lands in the zip will overwrite the user's
  data on update.

---

## Decision 2: Multi-Speaker Dialogue System (Tasks 26–29)

### Context

Users wanted to generate multi-speaker dialogue (e.g. audiobook
chapters with CAPTAIN and ENGINEER characters). The initial Phase 1
prototype (Task 26) used a `SPEAKER:` syntax that caused false
positives on normal text like `Note:` or `15:30`. Phase 2 (Task 28)
refined this into a production-ready system.

### Decision

**2a. The `$SPEAKER:` declaration syntax.**

A speaker declaration is a line that is **exactly** `$SPEAKER:`
(dollar sign, uppercase identifier, colon, optional trailing
whitespace, nothing else):

```
$CAPTAIN:
First sentence.
Second sentence.

$ENGINEER:
Third sentence.
```

The `$` prefix makes detection **deterministic** — no false positives
on normal prose. The declaration line itself is NOT included in the
TTS prompt; only the text below it is sent to the model.

Regex: `^\$([A-Z][A-Z0-9_]{2,}):\s*$` (MULTILINE).

**2b. UI-based speaker→voice mapping (no settings.json editing).**

The `LongNarrationDialog` detects all speakers from the split parts
and renders a `QComboBox` per speaker, populated from
`VoiceManager.list_voices()`. The user assigns a voice to each speaker
via dropdown. The mapping is saved to `settings.json` automatically on
Generate, so it persists across dialog reopens.

The `speaker_voice_map` key was **removed** from
`DEFAULT_SETTINGS.application` — the mapping is purely UI-driven now.

**2c. Dedicated `BatchJob.speaker` field.**

Added `speaker: Optional[str] = None` to `BatchJob`. This is the
authoritative source for dialogue detection — replacing the old `":"
in job.name` heuristic. Used by the concatenate handler to group jobs
by speaker for stems export.

**2d. Silence-based concatenation for dialogue.**

Multi-speaker dialogue uses `AudioManager.concatenate_with_silence()`
(300ms base, randomized 50%–150% per boundary, with fade in/out).
Single-speaker narration uses the original crossfade (100ms) —
backward compatible.

The randomization is seeded from the file path hash, so the result
is reproducible for the same input files.

**2e. Scene persistence (`.scene.json`).**

Added `engine/scene_persistence.py` with `DialogueScene` and
`DialogueLine` dataclasses. A scene captures the full dialogue state:
speakers, text, voice IDs, emotions, output paths, durations, and
generation status. Save/Load via the File menu.

**2f. Stems export.**

When concatenating a multi-speaker dialogue, the handler creates a
directory structure:

```
outputs/<project>/
  lines/         # individual line WAVs
  stems/         # per-speaker concatenated WAVs (captain.wav, engineer.wav)
  dialogue_full.wav  # full dialogue in queue order
```

### Alternatives Considered

- **A separate Dialogue tab/editor.** Rejected — would duplicate the
  Narration editor's infrastructure and fragment the UX. The
  `$SPEAKER:` syntax integrates cleanly into the existing text editor.
- **Per-line emotion/style dropdowns.** Rejected for Phase 2 —
  complexity vs. value. Users who need per-line emotion can use Raw
  Mode with inline Higgs tokens. A Phase 3 UI may revisit this.
- **Storing the speaker_voice_map in settings.json only.** Rejected —
  non-technical users couldn't edit it. The UI dropdown is required.

### Consequences

- **Positive**: Deterministic speaker detection — no false positives.
- **Positive**: No settings.json editing required for voice mapping.
- **Positive**: Stems export enables downstream mixing in DAWs/Unreal.
- **Positive**: Scene persistence supports long-form audiobook
  production (save progress, resume later).
- **Negative**: The `$` prefix is unusual — users must learn the
  syntax. The Token Guide (Help menu) documents it.
- **Negative**: Per-line emotion/style requires Raw Mode (no dropdown
  UI yet).

---

## Decision 3: Canonical Prompt Pipeline (Tasks 25, 33, PROMPT-PIPELINE-CONTRACT-AUDIT)

### Context

The original prompt construction was scattered: `_build_prompt()` in
`MainWindow` used a simple `if '<|' in text` heuristic, the
`NarrationSplitter` called `PromptBuilder.build()` independently per
part, and `PromptOptimizer` had its own emission logic. This led to
**duplicate/conflicting tokens** when Raw Mode text contained Higgs
tokens and the splitter prepended global tokens on top.

### Decision

**3a. `CanonicalPromptCompiler` is the single compilation entry point.**

Added `engine/prompt_state.py` with `CanonicalPromptCompiler` — the
ONE class allowed to construct Higgs token strings. Three entry
points, all using the SAME precedence resolver:

| Method                          | Used by                          |
|---------------------------------|----------------------------------|
| `compile_for_generate()`        | Preview + Generate (single call) |
| `compile_for_batch_part()`      | Generate Long / batch splitting  |
| `compile_continuous()`          | Continuous generation (blocks)   |

**Precedence (per property, per scope):**
1. Inline token (from `SemanticTokenState`, imported via Read Tokens)
2. Narration Block override
3. Global Right Panel default

Only the winning value is emitted — no duplicates.

**3b. `SemanticTokenParser` + `SemanticTokenState`.**

`SemanticTokenParser.parse(text)` properly identifies token
categories, resolves tags to human-readable names, and detects
conflicts (e.g. `pitch_low` + `pitch_high` in the same scope).

`SemanticTokenState` tracks: parsed tokens, resolved effective values
(emotion/style/speed/pitch/delivery), SFX/pause presence, stale
tracking (text changed after Read Tokens), and conflict warnings.

**3c. Raw Mode bypasses the compiler entirely.**

When `raw_mode=True`, `NarrationSplitter._split_raw()` splits at
sentence boundaries but uses each part's text **as-is** for the
prompt — no `PromptBuilder`, no global tokens prepended. This
preserves the user's literal Higgs tokens exactly.

**3d. Token detection in `_build_prompt()`.**

If the text contains `<|` and `|>` (Higgs token syntax) and Raw Mode
is OFF, the `PromptBuilder` is NOT called — the text is used as-is.
This prevents duplicate tokens when the user wrote tokens manually
but forgot to enable Raw Mode.

Decision tree:
1. Raw Mode ON → text as-is
2. Narration Blocks present → block-aware compiler
3. Text contains Higgs tokens → text as-is (no compiler)
4. Otherwise → compiler with global settings

**3e. Read Tokens gate (documented, not yet implemented).**

The contract specifies that manual tokens in editor text are NOT
active until the user presses "Read Tokens". This is a future feature
— currently, tokens in the text are always active (case 3 above).
The `SemanticTokenState.imported` and `.stale` fields are in place
for when the gate is added.

### Alternatives Considered

- **Let each caller build its own prompt.** Rejected — led to the
  duplicate-token bug. One canonical compiler is required.
- **Merge inline tokens with UI state automatically.** Rejected for
  now — the conflict detection is complex and the Read Tokens gate
  gives the user explicit control. Documented as "Required future
  behaviour" in the contract.

### Consequences

- **Positive**: No duplicate/conflicting tokens in any path.
- **Positive**: One precedence rule — Inline > Block > Global —
  everywhere (Preview, Generate, Generate Long, continuous).
- **Positive**: Raw Mode is truly raw — user tokens are sacred.
- **Negative**: The Read Tokens gate is not yet implemented — tokens
  in text are always active. This is a known limitation.
- **Negative**: `compile_continuous()` uses a separate emission
  optimizer (change-only emission) that differs from
  `compile_for_batch_part()`. This is intentional (continuous vs.
  independent parts) but adds cognitive complexity.

---

## Decision 4: Narration Block Offset Integrity (Tasks 36, FINAL-CORRECTION)

### Context

Narration Blocks reference text by character offsets into the editor
document. Two bugs corrupted these offsets:

1. **4 blocks → 2 parts** (Task 36): `_split_with_blocks()` called
   `_group_sentences()` on every block, merging short blocks into
   fewer parts.

2. **Find and Replace All offset corruption** (FINAL-CORRECTION):
   `on_text_changed()` assumed a single contiguous edit (common
   prefix/suffix). Replace All produces **multiple disjoint edits** —
   the entire span from first to last match was treated as one
   modification, corrupting offsets for blocks in between.

### Decision

**4a. One-block-one-part mapping (Task 36).**

`_split_with_blocks()` now checks block text length:
- If `len(block_text) <= MAX_CHARS` (400): the block becomes exactly
  ONE part — no `_group_sentences()` call.
- If `len(block_text) > MAX_CHARS`: the block is split into sentence
  groups (to stay within the model's generation limit).

This preserves the user's mental model: 4 blocks = 4 parts.

**4b. `NarrationBlockManager.on_multi_replace()` (FINAL-CORRECTION).**

Added a dedicated method for Find and Replace All that handles each
match independently:

```
on_multi_replace(old_text, new_text, matches, replacement)
```

Algorithm:
1. Sort matches by position (ascending).
2. Process blocks in ascending start-offset order.
3. For each block:
   - (a) Consume matches ending ≤ block's original start. Their deltas
     accumulate into `cumulative_delta`.
   - (b) Shift the block by `cumulative_delta`.
   - (c) Consume matches overlapping the block's original range. Each
     adds its delta to `end_offset` and to `cumulative_delta`.
4. Update `_last_text = new_text` so the subsequent `textChanged`
   signal is a no-op in `on_text_changed()`.

`_on_replace_all()` in `narration_editor.py` now computes match
positions before replacement, calls `on_multi_replace()`, then
`setPlainText()`.

**4c. Re Detect — three modes with one-to-one matching.**

- **Preserve Overrides**: two-pass matching (exact matches first,
  then fuzzy substring ≥80%). Enforces **one-to-one** matching via a
  `used_old` set — prevents an old override from being assigned to
  multiple new blocks.
- **Rebuild Automatic Only**: preserves locked blocks (one-to-one
  matching on locked blocks only). Automatic blocks are recalculated.
- **Rebuild Everything**: intentionally discards all overrides.

### Alternatives Considered

- **Force Re Detect after every Replace All.** Rejected — the
  requirement explicitly states "do not solve the problem by simply
  forcing Re Detect after every Replace All." The block metadata
  must remain valid after text replacement itself.
- **Store block text instead of offsets.** Rejected — the editor is
  the single source of truth for text. Storing duplicated text would
  create sync issues.
- **Fuzzy matching with score < 80%.** Rejected — too risky. A 70%
  match could assign an override to an unrelated block. 80% is the
  minimum confidence.

### Consequences

- **Positive**: Find and Replace All never corrupts block offsets.
  Multiple replacements at different positions remain correct.
- **Positive**: Block identity, ordering, overrides, locked state,
  and manual state are all preserved through Replace All.
- **Positive**: 1-block = 1-part mapping matches user expectations.
- **Positive**: Preserve Overrides never assigns an old override to
  an unrelated new block.
- **Negative**: `on_multi_replace()` is more complex than
  `on_text_changed()` — but the complexity is isolated and tested.

---

## Decision 5: Long Narration UI and CTA Consistency (FINAL-CORRECTION)

### Context

The Long Narration dialog had two visual inconsistencies:
1. The part header showed only `Part 1 | Block 1 | ~8s | 134 chars` —
   no indication of the effective token state. The user couldn't see
   what would actually be generated.
2. The Generate button used `setProperty("accent", True)` which
   rendered as a small dark-purple button — visually inconsistent
   with the main SpeechStudio Generate CTA (orange gradient).

### Decision

**5a. Effective token display in part headers.**

Added `_build_token_display_html(part)` to `LongNarrationDialog`.
The part header now shows:

```
Part 1 | Block 1 | ~8s | FEAR WHISPERING SLOW HIGH | 134 chars
```

- Values come from the **same `eff_*` fields** on `SplitPart` that
  are used by `CanonicalPromptCompiler.compile_for_batch_part()` to
  build the actual `BatchJob.prompt`.
- **Display == effective state == compilation state.**
- Human-readable values only (e.g. `FEAR`, `WHISPERING`, `SLOW`,
  `HIGH`) — NO raw Higgs syntax (`<|emotion:fear|>`).
- Category-based color coding:
  - Emotion: `#f87171` (warm red/coral)
  - Style: `#c084fc` (purple)
  - Speed: `#14b8a6` (teal)
  - Pitch: `#fbbf24` (amber/gold)
  - Delivery: `#4ade80` (green)
- Compact: colored uppercase text spans, no pills/borders.
- "Normal" prosody values are skipped (default, adds noise).
- Display ONLY — does not modify `part.text`, `part.prompt`, or
  semantic state.

**5b. Generate button matches the main CTA.**

Replaced `setProperty("accent", True)` with the **identical orange
CTA style** from `toolbar.Toolbar._apply_generate_style`:

| Property      | Value                                      |
|---------------|--------------------------------------------|
| Background    | `qlineargradient(135deg, #F97316, #EA580C)` |
| Text color    | `#FFFFFF`                                  |
| Border radius | `8px`                                      |
| Padding       | `8px 20px`                                 |
| Font          | Bold, 13px                                 |
| Hover         | `opacity: 0.9`                             |
| Pressed       | `opacity: 0.8`                             |
| Disabled      | `bg_surface` + `text_disabled` + border    |

Added `_cta_button_style()` static method that returns the identical
stylesheet. The style is duplicated (rather than imported from
`toolbar.py`) to avoid coupling the dialog to the toolbar module —
but the values are identical and must be kept in sync.

Only the visual presentation changed — `BatchJob` creation, prompt
compilation, generation parameters, part editing, voice selection,
concatenation, and generation flow are **unchanged**.

### Alternatives Considered

- **Display raw HIGGS tokens in the header.** Rejected — the
  requirement explicitly forbids raw syntax like `<|emotion:fear|>`.
  Human-readable values only.
- **Import the CTA style from toolbar.py.** Rejected — would couple
  the dialog to the toolbar module. Duplication with a comment to
  keep in sync is cleaner.
- **Show all prosody values including "Normal".** Rejected — adds
  noise. "Normal" is the default; only non-default values are shown.

### Consequences

- **Positive**: The user can immediately see what will be generated
  for each part — no surprises after clicking Generate.
- **Positive**: The token display is derived from the canonical
  effective state, not a second prompt representation.
- **Positive**: The Long Narration Generate button looks like the
  same action as the main Generate — visual consistency.
- **Negative**: The CTA style is duplicated in two files. If the main
  CTA style changes, `_cta_button_style()` must be updated to match.
  A comment documents this requirement.

---

## Cross-Cutting Concerns

### Theme-Aware Palette (Task 30)

The legacy `Palette` class in `ui/theme.py` had hardcoded Dark theme
colors. `apply_theme()` now **mutates `Palette` in place** to mirror
the active `ThemePalette`. This means panels that use
`from ui.theme import Palette` work for any theme — but they're using
the legacy flat namespace, not the structured `Colors`/`Surfaces`/
`Elevation` tokens.

The `Toolbar._apply_generate_style()` method uses inline
`setStyleSheet` with `Palette.ACCENT` etc. — and `refresh_theme()` is
called after `apply_theme()` to re-apply the style with new colors.

### Crash Logging (Task 34)

`SpeechStudio.py` installs `sys.excepthook` and
`threading.excepthook` at startup. Unhandled exceptions on any thread
are written to `logs/crash.log` with a timestamp and full traceback.
This is separate from `application.log` — easy to find and send for
debugging.

### Concatenate Settings (Tasks 34, 35)

Added `concatenate_silence_ms` (default 300) and
`concatenate_randomize` (default True) to
`DEFAULT_SETTINGS.audio`. Both single-speaker and multi-speaker
concatenation paths read these settings. If `silence_ms == 0`, the
single-speaker path falls back to the original crossfade behavior.

---

## Verification

All decisions were verified with:

- `tools/verify_compile.py` — 75 files compile.
- `tools/verify_architecture.py` — no layer violations.
- `tools/verify_functional_integrity.py` — 80/80 checks pass.
- `tools/verify_feature_gate.py` — all feature gates respected.
- Targeted unit tests for each decision (offset integrity, speaker
  detection, token display, CTA style match).
- End-to-end test: Replace All → Re Detect → Preserve Overrides →
  Generate Long — all block boundaries and overrides correct.

---

## References

- `docs/adr/ADR_001_Preset_Architecture.md` — preset system
- `docs/governance/architecture_manifest.yaml` — layered architecture
- `docs/governance/feature_registry.yaml` — feature statuses
- `engine/narration_block_manager.py` — `on_multi_replace()`,
  `preserve_overrides()`, `rebuild_automatic_only()`
- `engine/narration_splitter.py` — `_split_with_speakers()`,
  `_split_with_blocks()`, `_split_raw()`
- `engine/prompt_state.py` — `CanonicalPromptCompiler`,
  `SemanticTokenParser`, `SemanticTokenState`
- `engine/scene_persistence.py` — `DialogueScene`, `DialogueLine`
- `engine/audio_manager.py` — `concatenate_with_silence()`
- `ui/panels/long_narration_dialog.py` — token display, CTA style
- `ui/panels/narration_editor.py` — `_on_replace_all()`
- `ui/panels/toolbar.py` — `_apply_generate_style()`, `refresh_theme()`
- `update.bat` — safe source-only update script
- `worklog.md` — Tasks 20 through FINAL-CORRECTION (detailed logs)
