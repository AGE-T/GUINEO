# SpeechStudio Governance Documentation

> **Note:** the user-facing product name is **GUINEO** (since the P3.37
> rebrand). This governance layer is an **internal technical** archive and
> keeps the historical SpeechStudio terminology. For the user guide see
> [`../../README.md`](../../README.md).

This directory contains the **governance layer** for the SpeechStudio
project: the rules, manifests, and registries that keep the codebase
aligned with its intended architecture.

## Document Hierarchy

The authority of project documents, in descending order:

1. **Current accepted ADR** (currently ADR 003)
2. **Current Prompt Pipeline Contract** (referenced in code; see ADR 003 §10)
3. **Current feature specifications** (`feature_specs/`)
4. **Current code** (the actual implementation)
5. **Historical ADRs** (ADR 001, ADR 002 — for context only)
6. **Historical build reports** (`../build_reports/` — represent the
   state of a specific build, not the current release)

A historical build report must **never** override a later
architectural decision.

## Contents

| File                          | Purpose                                              |
|-------------------------------|------------------------------------------------------|
| `architecture_manifest.yaml`  | Single source of truth for the layered architecture. |
| `feature_registry.yaml`       | Authoritative list of features + statuses.           |
| `capability_matrix.yaml`      | Feature -> capability cross-reference.               |
| `DEVELOPMENT_RULES.md`        | Mandatory rules for every contributor.               |
| `OPEN_BUGS.md`                | Known unfixed issues (must be resolved before release). |
| `feature_specs/`              | One spec file per feature (F-xxx).                   |
| `../adr/`                     | Architecture Decision Records.                       |
| `../build_reports/`           | Per-build verification reports (historical).         |

## Quick Start

1. **Before contributing**, read `DEVELOPMENT_RULES.md`.
2. **Before adding a feature**, add an entry to `feature_registry.yaml`
   and write a spec in `feature_specs/`.
3. **Before changing the architecture**, write an ADR in `../adr/`.
4. **Before releasing**, run every `tools/verify_*.py` script and save
   the output to `../build_reports/`.

## Architecture Decision Records

| ADR                                             | Status   | Topic                                                      |
|-------------------------------------------------|----------|------------------------------------------------------------|
| `../adr/ADR_001_Preset_Architecture.md`         | Accepted | Preset system (engine-owned, YAML, signal-driven UI)       |
| `../adr/ADR_002_Recent_Architectural_Decisions.md` | **Historical** | Tasks 20–FINAL-CORRECTION decisions (partially superseded by ADR 003) |
| `../adr/ADR_003_Current_Prompt_Architecture.md` | **Current** | Current prompt architecture: Managed Mode vs Raw Mode ownership, CanonicalPromptCompiler, HIGGS V3 placement rules, known bugs |

> **Important**: ADR 003 is the **current architectural source of
> truth** for prompt pipeline decisions. ADR 002 is preserved for
> historical context but its prompt pipeline decisions (Read Tokens /
> SemanticTokenState) have been superseded.

## Verification Scripts

> **⚠️ Do not claim "all verification scripts pass" unless ALL of the
> following scripts actually pass.** Run each one and verify the exit
> code is 0. A single failing script means the build is NOT
> release-ready, regardless of how many other scripts pass.

| Script                          | What it checks                                    | Current Status |
|---------------------------------|---------------------------------------------------|----------------|
| `tools/verify_compile.py`       | Every Python file compiles.                       | PASS           |
| `tools/verify_architecture.py`  | Layer dependency rules are respected.             | PASS           |
| `tools/verify_feature_gate.py`  | Experimental features are properly gated.         | PASS           |
| `tools/verify_functional_integrity.py` | 80 functional integrity checks.             | PASS           |
| `tools/verify_integration.py`   | End-to-end smoke test (preset round-trip + batch manager + pipeline hash logging). | PASS |

> **Note**: `tools/verify_transport_gl.py` exists but requires
> PySide6 with OpenGL — it is not run in headless environments.

## Known Open Bugs

These are documented issues that have NOT yet been fully resolved.
See `OPEN_BUGS.md` for full details.

1. **BUG-001: Advanced Preview emits invalid `expressive_normal` token**
   — **Status: FIXED-PENDING-VERIFICATION**. The fix is implemented
   (`compile_continuous()` now skips `*_normal` emissions) and
   automated tests pass (Preview Hard Contract verified for 10
   configurations). Remains FIXED-PENDING-VERIFICATION until an
   independent audit confirms exact preview/model equality
   end-to-end. Per user directive: "Until exact preview/model
   equality is independently demonstrated, BUG-001 remains OPEN."

2. **BUG-002: `expressive_normal` / `speed_normal` / `pitch_normal`
   are not valid HIGGS V3 tokens** — **Status: OPEN** (partially
   mitigated by BUG-001 fix — no `*_normal` token reaches the model,
   but the placeholders still exist in `higgs_tokens.py` and the
   Token Guide).

3. **BUG-003: Prompt Pipeline Contract is not a standalone document**
   — **Status: OPEN** (Low severity).

> **Do not mark the build release-ready merely because the five
> verification scripts pass.** BUG-001 must be independently
> verified as CLOSED before release.

## Relationship to the Spec

The `spec/` directory holds the original product specifications
(system, engine, UI, prompt, generation).  This `docs/governance/`
directory is the **engineering** counterpart: it describes how the
codebase is organised to deliver on those specs.

> **Note on HIGGS V3 semantics**: The `spec/` directory contains the
> official HIGGS V3 token catalogue and placement rules
> (`spec/Higgs_Audio_V3_Reference.md`). Application-level concepts
> like "Inline > Block > Global precedence" are SpeechStudio
> application semantics, NOT HIGGS V3 model rules. HIGGS V3 defines
> token syntax and placement only.
