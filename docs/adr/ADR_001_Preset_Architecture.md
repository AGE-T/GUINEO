# ADR 001: Preset Architecture

- **Status**: Accepted
- **Date**: 2025-01-01
- **Decision Maker**: SpeechStudio engineering

## Context

SpeechStudio needs a way for users to save and recall the combination
of voice + emotion + style + prosody + generation parameters that
produces a satisfying narration.  Without presets, users have to
re-enter six to ten settings every session, which is error-prone and
frustrating.

The questions this ADR answers:

1. **Where does preset state live?** Engine or UI?
2. **What format?** YAML, JSON, TOML, SQLite?
3. **What does the API look like?**
4. **How does the UI consume presets?**

## Decision

1. **Preset state lives in the Engine layer.**  A new
   `engine.preset_manager.PresetManager` class owns persistence and
   validation.  The UI never reads or writes preset files directly.
2. **YAML is the primary format; JSON is a fallback.**  PyYAML may not
   be installed on every machine, so the manager ships with a tiny
   built-in YAML emitter/parser that covers the preset schema.  If
   PyYAML is available it is preferred.
3. **The API is small and synchronous.**  `load`, `save`, `list`,
   `get`, `delete`, `rename`, `validate`, `validate_name`.  All
   methods are blocking (preset files are tiny).
4. **The UI consumes presets via signals.**  The sidebar's Presets tab
   emits `apply_preset_requested(name)` etc.; the MainWindow wires
   those signals to `PresetManager` calls and refreshes the sidebar
   after every mutation.
5. **Applying a preset uses `ControlPanel.batch_update()`.**  This
   batches the setter calls so the prompt preview is rebuilt only
   once.
6. **The control panel shows an inheritance banner** indicating the
   preset name that is currently applied.

## Consequences

- **Positive**: One source of truth for preset state.  The UI is a
  thin shell.  Presets can be edited offline (just open the YAML).
- **Positive**: No hard dependency on PyYAML.
- **Positive**: Presets are portable across machines (YAML is human-
  readable and git-friendly).
- **Negative**: The built-in YAML emitter is minimal; complex schemas
  (nested lists, anchors) would require PyYAML.  This is acceptable
  because the preset schema is intentionally simple.
- **Negative**: The manager caches the loaded presets in memory; if a
  preset file is edited externally the cache is stale until
  `refresh()` is called.  The UI calls `refresh()` after every
  mutation, so this is rarely a problem.

## Alternatives Considered

- **SQLite**.  Rejected because presets are small, few in number, and
  benefit from being human-readable.
- **A single JSON file with all presets**.  Rejected because a
  corruption would lose every preset; one-file-per-preset is more
  resilient.
- **Putting presets in the UI layer**.  Rejected because it would
  violate the layered architecture (UI layer would own persistence,
  which is the Engine's job).

## References

- `docs/governance/architecture_manifest.yaml` (layered architecture)
- `engine/preset_manager.py` (implementation)
- `docs/governance/feature_specs/F-301_preset_system.md` (feature spec)
