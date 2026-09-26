# ADR 003: Current Prompt Architecture

- **Status**: Accepted (CURRENT)
- **Date**: 2025-01
- **Decision Maker**: SpeechStudio engineering
- **Supersedes**: ADR 002 Decision 3 (Canonical Prompt Pipeline) and
  Decision 3e (Read Tokens gate)
- **Related**: `docs/adr/ADR_002_Recent_Architectural_Decisions.md`
  (historical), `spec/05_Prompt_System.md`,
  `spec/Higgs_Audio_V3_Reference.md`

## Purpose

This ADR documents the **current** prompt architecture for
SpeechStudio. It supersedes the prompt-related decisions in ADR 002,
which documented a `SemanticTokenState` / Read Tokens model that has
since been reconsidered.

The goal is to provide a single, authoritative reference for how
prompts are constructed in the current codebase, what the user
ownership model is, and how the application relates to the underlying
HIGGS V3 model semantics.

---

## 1. Two Ownership Modes

SpeechStudio has exactly two prompt ownership modes. There is no
hybrid "Plain Text + manually embedded tokens + managed UI state"
mode — that model was considered and rejected (see ADR 002 Decision 3
for the historical context).

### MANAGED MODE

**SpeechStudio owns the prompt state.**

The user interacts with friendly UI controls (emotion buttons, style
dropdown, speed/pitch/delivery sliders, SFX insertions, pause
markers). SpeechStudio compiles these into a valid HIGGS V3 prompt
via the canonical compiler.

- The editor text is **plain narration text** (no `<|...|>` tokens).
- SFX and pause markers may appear inline as `{sfx:Laughter:Haha}`
  or `{pause}` — these are **application markers**, not HIGGS tokens.
  The compiler converts them to proper HIGGS tokens at build time.
- Narration Blocks may partition the text into segments with
  per-segment overrides.
- The user never needs to know HIGGS token syntax.

### RAW MODE

**The user owns the prompt.**

The user writes literal HIGGS tokens directly in the editor
(`<|emotion:fear|><|style:whispering|>Hello world.`). SpeechStudio
treats this text as sacred — it is sent to the model **as-is**.

- SpeechStudio does **not** silently rewrite, fix, merge, import, or
  normalize manual Higgs tokens.
- SpeechStudio does **not** prepend global emotion/style/prosody
  tokens.
- SpeechStudio does **not** require a "Read Tokens" step (that model
  is documented in ADR 002 as historical and is not the current UX).
- The only transformation Raw Mode performs is **structural splitting**
  for Generate Long (splitting at sentence boundaries so each part
  fits within the model's generation limit). Each part's text is the
  literal editor text for that span — no tokens are added, removed, or
  modified.

---

## 2. Canonical Prompt Compiler

In Managed Mode, a single class is responsible for constructing
HIGGS V3 token strings:

**`engine/prompt_state.py::CanonicalPromptCompiler`**

No other component may construct `<|category:tag|>` strings directly
(except `PromptBuilder`, which the compiler delegates to).

### Entry Points

| Method                        | Used by                          | Scope            |
|-------------------------------|----------------------------------|------------------|
| `compile_for_generate()`      | Preview + Generate (single call) | Global / Inline  |
| `compile_for_batch_part()`    | Generate Long / batch splitting  | Block / Global   |
| `compile_continuous()`        | Continuous generation (blocks)   | Multi-block      |

All three use the **same** precedence resolver (see §3 below).

### Raw Mode Bypass

When Raw Mode is ON, the compiler is **not called**. The
`NarrationSplitter._split_raw()` method handles splitting and uses
each part's text as the prompt directly.

---

## 3. Application Scope Precedence (NOT a HIGGS Rule)

> **IMPORTANT**: The precedence rules below are **SpeechStudio
> application semantics**, NOT HIGGS V3 model semantics.
>
> HIGGS V3 defines token **syntax** (`<|category:tag|>`) and
> **placement rules** (sentence-level vs inline). It does NOT define
> a scope hierarchy for "global default vs block override vs inline
> token." That hierarchy is a SpeechStudio application concern — it
> determines which UI-controlled value wins when multiple sources
> could contribute to the same token category.
>
> See `spec/Higgs_Audio_V3_Reference.md` for the model's actual
> token catalogue and placement rules.

### Managed Mode Precedence (per property, per scope)

```
1. Narration Block override   (highest — per-block UI setting)
2. Global Right Panel default  (lowest — applies when no block override)
```

The "Inline token" layer from ADR 002 (which required Read Tokens to
import manual `<|...|>` tokens into semantic state) is **not part of
the current Managed Mode**. If the editor text contains literal
`<|...|>` tokens while in Managed Mode, the current behavior is to
pass the text through as-is (no compiler prepend) — but this is a
fallback, not an intended workflow. Users who write tokens manually
should use Raw Mode.

### Block Override Scope

A Narration Block override applies only to the text within that
block's character range. Blocks do not affect text outside their
range. The compiler resolves each block independently.

---

## 4. Managed Mode Sub-Modes

Managed Mode has two editor presentations:

### Friendly (default)

The user sees a plain text editor with optional Narration Block
gutter annotations. Emotion/style/prosody are set via the Right
Panel. This is the primary user-facing mode.

### Advanced

The user sees the same text editor but with additional tooling
(block properties panel, SFX/pause insertion tools, per-block
override dropdowns). The Advanced Preview shows the compiled HIGGS
prompt. The underlying compiler is the same — only the UI surface
differs.

> **⚠️ BUG-001: Advanced Preview Multiple Representations — FIXED-PENDING-VERIFICATION**
>
> The Advanced Preview and the center Preview previously did not
> produce identical token output. The Advanced Preview
> (`compile_continuous()`) could emit `<|prosody:expressive_normal|>`
> for "Normal" delivery, while the center Preview
> (`compile_for_generate()` → `PromptBuilder.build()`) correctly
> skipped "Normal" delivery.
>
> **Fix applied**: `compile_continuous()` now skips emission of any
> prosody token when the effective value is `"Normal"` or `None`,
> matching `PromptBuilder.build()`. The `prev` tracker resets to
> `None` on "Normal" so subsequent non-Normal values correctly emit.
>
> **Verification performed**:
> - Required test (3 blocks: Normal/Expressive High/Normal) PASS
> - Expanded test (Speed/Pitch/Delivery Normal) PASS — no tokens emitted
> - Preview Hard Contract (10 configurations, exact string match) PASS
>
> **Status**: FIXED-PENDING-VERIFICATION. The fix is implemented and
> automated tests pass, but remains in this state until an independent
> audit confirms exact preview/model equality end-to-end. See
> `docs/governance/OPEN_BUGS.md` BUG-001.
>
> Preview must ultimately equal the actual model input. Until
> BUG-001 is independently verified as CLOSED, do not rely solely
> on the Advanced Preview as the authoritative prompt representation.

---

## 5. Narration Blocks

Narration Blocks partition the editor text into segments with
optional per-segment overrides. A block references text by character
offsets — the editor is the single source of truth for text; blocks
store only metadata.

### Block Properties

Each block can override: emotion, style, speed, pitch, delivery,
label, locked, and manually_edited. `None` means "inherit the global
default."

### Block Operations

- **Auto-detect**: `HeuristicBlockDetector` splits text at paragraph
  boundaries and transition markers.
- **Split / Merge / Delete**: user-initiated block structure edits.
- **Lock**: locked blocks are preserved by `rebuild_automatic_only()`.
- **Re Detect** has three modes:
  - **Rebuild Everything**: discards all overrides (intentional).
  - **Rebuild Automatic Only**: preserves locked blocks.
  - **Preserve Overrides**: one-to-one text matching (exact first,
    then fuzzy ≥80%) transfers overrides to matching new blocks.

### Find and Replace Block Integrity

`NarrationBlockManager.on_multi_replace()` handles Find and Replace
All by processing each match independently with cumulative delta
tracking. This preserves block identity, ordering, overrides, locked
state, and manual state through multi-replacement operations. See
ADR 002 Decision 4 for details (this decision is NOT superseded).

---

## 6. Generate Paths

### Generate (single call)

```
Managed Mode:
  editor text + Right Panel state + Block overrides
  → CanonicalPromptCompiler.compile_for_generate()
  → PromptBuilder.build()
  → single HIGGS prompt → model

Raw Mode:
  editor text (literal)
  → model (no compiler, no transformation)
```

### Generate Long (batch)

```
Managed Mode:
  editor text + Blocks + Right Panel state
  → NarrationSplitter.split()
  → per-part: CanonicalPromptCompiler.compile_for_batch_part()
  → each BatchJob.prompt is self-contained (all required tokens)
  → batch generation

Raw Mode:
  editor text (literal)
  → NarrationSplitter._split_raw() (structural split only)
  → each part's text == part's prompt (literal, no tokens added)
  → optional explicit per-part override (future)
  → batch generation
```

### Multi-Speaker

The `$SPEAKER:` declaration syntax partitions text by speaker. Each
speaker's text becomes a separate `SplitPart` with `.speaker` set.
Per-speaker voice assignment is done via the `LongNarrationDialog`
UI (not settings.json). See ADR 002 Decision 2 (not superseded).

### Preview Hard Contract

**Preview MUST equal the actual model input.** The same compiler
path must be used for both. If the Preview shows one token sequence
and the Generate path produces a different one, that is a bug (see
BUG-001 in §4).

---

## 7. HIGGS V3 Token Catalogue — Known Discrepancy

The official HIGGS V3 reference
(`spec/Higgs_Audio_V3_Reference.md`, `research/PROMPTING_official.md`)
defines the following **delivery** (expressiveness) tokens:

| Token                       | Tag               | Effect            |
|-----------------------------|-------------------|-------------------|
| `<\|prosody:expressive_high\|>`  | `expressive_high`  | More expressive   |
| `<\|prosody:expressive_low\|>`   | `expressive_low`   | Flatter delivery  |

There is **NO** `expressive_normal` token in the official reference.
The same applies to speed (`speed_normal` does not exist) and pitch
(`pitch_normal` does not exist).

However, `engine/higgs_tokens.py` defines:

```python
ProsodyDef("Normal", "expressive_normal", "Normal delivery", "delivery")
ProsodyDef("Normal", "speed_normal", "Normal speed", "speed")
ProsodyDef("Normal", "pitch_normal", "Normal pitch", "pitch")
```

These are **application-level placeholders** used to represent "no
override" in the UI. They are NOT valid HIGGS V3 tokens.

**Current state (post-BUG-001 fix):**

- `PromptBuilder.build()` correctly skips emitting a token when the
  value is `"Normal"` (checked `!= "Normal"` before emitting).
- `CanonicalPromptCompiler.compile_continuous()` now ALSO skips
  emission for `"Normal"` values (BUG-001 fix applied). The `prev`
  tracker resets to `None` on "Normal" so subsequent non-Normal
  values correctly emit.
- No `*_normal` token reaches the model in any code path.

> **⚠️ BUG-001: FIXED-PENDING-VERIFICATION**
>
> The invalid `expressive_normal` / `speed_normal` / `pitch_normal`
> token emission from `compile_continuous()` has been fixed. The fix
> skips emission for any `"Normal"` or `None` prosody value, matching
> `PromptBuilder.build()`.
>
> **Verification performed**:
> - Required test (3 blocks: Normal/Expressive High/Normal) — no
>   `expressive_normal` emitted, `expressive_high` emitted once ✓
> - Expanded test (Speed/Pitch/Delivery Normal) — no tokens emitted ✓
> - Preview Hard Contract (10 configurations) — Center Preview ==
>   Advanced Preview (exact string match) ✓
>
> **Status**: FIXED-PENDING-VERIFICATION until an independent audit
> confirms exact preview/model equality end-to-end. See
> `docs/governance/OPEN_BUGS.md` BUG-001.
>
> **BUG-002 (still OPEN)**: The `*_normal` placeholders still exist
> in `higgs_tokens.py` and are referenced in the Token Guide. They
> should be marked as non-emittable or removed. See
> `docs/governance/OPEN_BUGS.md` BUG-002.

---

## 8. Raw Mode Ownership Model — Explicit Rules

The following operations are **FORBIDDEN** in Raw Mode:

1. **No silent rewriting.** The editor text must not be modified by
   SpeechStudio. If the text contains invalid tokens, that is the
   user's responsibility.
2. **No token fixing/merging.** If the text contains duplicate or
   conflicting tokens, they are passed through as-is.
3. **No token import/normalization.** There is no "Read Tokens" step
   that parses `<|...|>` tokens into semantic state. The text is
   literal.
4. **No global token prepend.** The Right Panel's emotion/style/
   prosody settings are NOT prepended to the prompt. They are ignored.
5. **No block override application.** Narration Block overrides are
   NOT applied to the prompt in Raw Mode. (Blocks may still be used
   for structural splitting, but their override values are not
   compiled into tokens.)

The **only** transformation Raw Mode performs is structural splitting
for Generate Long: the text is split at sentence boundaries so each
part fits within the model's generation limit. Each part's prompt is
the literal text of that span.

### Future: Explicit Per-Part Override in Raw Mode

A future enhancement may allow the user to **explicitly** attach an
override to a specific part in the Long Narration dialog (e.g.
"Part 3 should use Fear emotion"). This would be an **explicit user
action**, not an automatic import. This is not yet implemented.

---

## 9. Document Hierarchy

The authority of project documents, in descending order:

1. **Current accepted ADR** (this document, ADR 003)
2. **Current Prompt Pipeline Contract** (referenced in code; see §10)
3. **Current feature specifications** (`docs/governance/feature_specs/`)
4. **Current code** (the actual implementation)
5. **Historical ADRs** (ADR 002, ADR 001 — for context only)
6. **Historical build reports** (`docs/build_reports/` — represent
   the state of a specific build, not the current release)

A historical build report must **never** override a later
architectural decision.

---

## 10. Prompt Pipeline Contract

The Prompt Pipeline Contract is referenced in the codebase
(`engine/prompt_state.py`, `ui/main_window.py`,
`ui/panels/narration_editor.py`) as the authoritative specification
for the prompt pipeline. The contract is currently embedded in code
comments and docstrings rather than a standalone document.

Key contract points (as currently implemented):

1. **Raw Mode bypasses the compiler.** Literal text → model.
2. **Managed Mode uses `CanonicalPromptCompiler`.** One compiler,
   three entry points, same precedence resolver.
3. **Each BatchJob part is self-contained.** Every part gets a
   complete prompt with all required tokens — no reliance on
   previous parts' state.
4. **Multi-speaker preserves block overrides.** Each speaker's text
   gets the correct block-scoped tokens.
5. **Long Narration edit safety.** The dialog shows `part.text`
   (plain text) and rebuilds `part.prompt` from the effective state
   on Generate — it never overwrites the user's plain text with
   tokenized text.
6. **Preview == Generate.** Both call the same compiler path.
7. **Generate and Generate Long share the compiler.** No separate
   prompt construction logic for batch parts.
8. **SFX and pause markers are in the text.** `{sfx:Laughter:Haha}`
   and `{pause}` are converted to HIGGS tokens by `PromptBuilder`.

> **Note**: The contract should be extracted into a standalone
> document (`docs/governance/PROMPT_PIPELINE_CONTRACT.md`) in a
> future documentation pass. Until then, this ADR serves as the
> authoritative reference.

---

## Alternatives Considered

### Hybrid Mode (Plain Text + Manual Tokens + Managed UI State)

**Rejected.** This was the model documented in ADR 002 Decision 3.
It required a "Read Tokens" gate to import manual `<|...|>` tokens
into semantic state, then merge them with UI state using Inline >
Block > Global precedence. The complexity was high, the conflict
detection was non-trivial, and the user mental model was confusing
("are my manual tokens active or not?").

The current Managed/Raw split is simpler: in Managed Mode,
SpeechStudio owns the tokens (the user never writes them); in Raw
Mode, the user owns the tokens (SpeechStudio never touches them).
There is no ambiguous middle ground.

### Automatic Token Import in Raw Mode

**Rejected.** Automatically parsing `<|...|>` tokens from the editor
text and merging them with UI state would violate the Raw Mode
ownership contract. The user wrote those tokens intentionally —
SpeechStudio must not second-guess them.

### Per-Part Override UI in Raw Mode Generate Long

**Deferred.** This is a future enhancement (see §8). It would allow
the user to explicitly attach an override to a specific part without
writing tokens. This is NOT an automatic import — it is an explicit
user action in the Long Narration dialog.

---

## Consequences

- **Positive**: Clear ownership model — no ambiguity about who owns
  the prompt in each mode.
- **Positive**: Raw Mode is truly raw — user tokens are sacred.
- **Positive**: Managed Mode uses a single canonical compiler — no
  duplicate prompt representations.
- **Positive**: The Read Tokens UI workflow has been REMOVED. There is
  no longer an obsolete "import inline tokens into semantic state"
  path that could confuse users. The `SemanticTokenState` /
  `CanonicalPromptCompiler` engine classes still accept a
  `token_state` parameter for API compatibility, but MainWindow
  always passes `None` — there is no import path.
- **Negative**: The `expressive_normal` token discrepancy (§7) was an
  open bug but has been FIXED (BUG-001 status:
  FIXED-PENDING-VERIFICATION). The `*_normal` placeholders still
  exist in `higgs_tokens.py` (BUG-002, OPEN) but are never emitted.

---

## Verification

- `tools/verify_compile.py` — PASS (75 files)
- `tools/verify_architecture.py` — PASS
- `tools/verify_functional_integrity.py` — 80/80 PASS
- `tools/verify_feature_gate.py` — PASS
- `tools/verify_integration.py` — status: see current build report

> **Note**: The governance documentation must not claim ALL
> verification scripts pass until `verify_integration.py` actually
> passes. See the current build report for the latest status.

---

## References

- `engine/prompt_state.py` — `CanonicalPromptCompiler`,
  `SemanticTokenParser`, `SemanticTokenState`
- `engine/prompt_builder.py` — `PromptBuilder.build()`,
  `PromptBuilder.build_from_emissions()`
- `engine/narration_splitter.py` — `_split_raw()`,
  `_split_with_blocks()`, `_split_with_speakers()`
- `engine/narration_block_manager.py` — `on_multi_replace()`,
  `preserve_overrides()`
- `engine/higgs_tokens.py` — token catalogue (note the
  `expressive_normal` discrepancy in §7)
- `ui/main_window.py` — `_build_prompt()`, `_on_generate()`,
  `_on_generate_long_narration()`
- `ui/panels/narration_editor.py` — Raw Mode toggle, block editor
- `ui/panels/long_narration_dialog.py` — Long Narration dialog
- `spec/Higgs_Audio_V3_Reference.md` — official HIGGS V3 token
  catalogue and placement rules
- `spec/05_Prompt_System.md` — original prompt system spec
- `docs/adr/ADR_002_Recent_Architectural_Decisions.md` — historical
  decisions (partially superseded)
