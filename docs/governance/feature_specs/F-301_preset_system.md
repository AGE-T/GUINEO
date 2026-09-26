# Feature Spec: Preset System (F-301..F-308)

## Summary

A Preset captures the voice + emotion + style + prosody + generation
parameters of a single configuration so the user can re-apply it with
one click.  Presets are persisted as YAML (primary) with a JSON
fallback for environments without PyYAML.

## Feature IDs

| ID    | Name                |
|-------|---------------------|
| F-301 | Save preset         |
| F-302 | Apply preset        |
| F-303 | Delete preset       |
| F-304 | Rename preset       |
| F-305 | Import preset (file)|
| F-306 | Export preset (file)|
| F-307 | YAML persistence    |
| F-308 | JSON fallback       |

## User Stories

- As a creator, I want to save my favourite voice + emotion + speed
  combination so I can recall it instantly next session.
- As a creator, I want to share presets with collaborators by
  exporting them to a file.
- As a creator, I want to rename or delete presets without losing the
  underlying voice profile.

## Data Model

```yaml
name: "Default Audiobook"
voice_id: "narrator_female"
emotion: "Calm"
style: "Narration"
speed: "Slow"
pitch: "Normal"
delivery: "Expressive Low"
parameters:
    temperature: 1.3
    top_p: 0.95
    top_k: 300
    max_new_tokens: 2048
    seed: null
    append_silence: 0.5
    normalize_output: false
    auto_play: true
    output_format: "wav"
```

## API

`engine.preset_manager.PresetManager`:

| Method                       | Description                                |
|------------------------------|--------------------------------------------|
| `load() -> Dict[str, Preset]`| Load every preset (cached).                |
| `refresh() -> Dict[str, Preset]` | Force reload.                          |
| `save(preset) -> str`        | Persist a preset (returns relative path).  |
| `list() -> List[Preset]`     | Sorted list of all presets.                |
| `get(name) -> Optional[Preset]` | Look up by name.                       |
| `delete(name) -> bool`       | Remove a preset from disk.                 |
| `rename(old, new) -> Preset` | Rename (raises on duplicate).              |
| `validate(preset) -> List[str]` | Validation warnings.                    |
| `validate_name(name)`        | Raise ValueError if invalid.               |

## Persistence Layout

Presets live in `<app_root>/presets/` (the project root `presets/`
directory). Each preset is one file:

- `<safe_name>.yaml` (preferred), or
- `<safe_name>.json` (fallback).

`<safe_name>` is produced by replacing every character outside
`[A-Za-z0-9_\-]` with `_`.

The exact runtime path is constructed in `MainWindow._connect_engine`
as `os.path.join(APP_ROOT, "presets")` and passed to `PresetManager`.
`PresetManager.directory` exposes the absolute path — the
`PresetManagerDialog` displays it in its header so the user can find
their preset files on disk without guessing.

## UI Integration

- The `PresetManagerDialog` (reached from the ProjectSceneSidebar
  "Presets" library nav item) lists presets and emits signals:
  - `apply_preset_requested(name)`
  - `save_preset_requested()`
  - `delete_preset_requested(name)`
  - `rename_preset_requested(name)`
  - `export_preset_requested(name)`
  - `import_preset_requested()`
- Each preset row displays the preset name on the first line and the
  human-readable **voice name** on the second line. The internal
  `voice_id` is NEVER shown as the primary label — it is an opaque
  engine identifier. If the referenced voice no longer exists, the row
  shows "(voice missing)" instead of the voice name. The `voice_id` is
  preserved in the row tooltip for debugging.
- The MainWindow wires the dialog's signals to the same preset handlers
  used by the legacy sidebar tab (`_on_apply_preset`,
  `_on_save_current_as_preset`, `_on_delete_preset`,
  `_on_rename_preset`, `_on_export_preset`, `_on_import_preset`).
  Each signal is connected **exactly once** — no duplicates.
- The File menu's Import Preset / Export Preset items call
  `_on_import_preset` and `_on_export_preset`.

## Voice Display Policy

- **Internal identifier**: `voice_id` (e.g. `captain_4c6e8c96`) —
  persisted in the preset file, passed to `Engine.generate()`, used as
  the `VoiceProfile.id` lookup key. Never shown to the user as a
  primary label.
- **User-facing display**: `voice.name` (e.g. "Captain") — resolved at
  list-rendering time via the voice lookup map
  (`MainWindow._refresh_presets` builds `{voice_id: voice_name}` from
  `Engine.list_voices()` and pushes it into the sidebar/dialog).
- If `preset.voice_id` is set but the voice profile is missing, the
  display shows "(voice missing)" and `MainWindow._on_apply_preset`
  shows a clear warning dialog. The application NEVER silently
  substitutes another voice.

## Behaviour Notes

- Applying a preset uses `ControlPanel.batch_update()` so the prompt
  preview is rebuilt only once after all setters complete.
- The control panel's inheritance banner is set to
  `("preset", source=name)` after a preset is applied.
- Deleting a preset is a destructive action: the user is asked to
  confirm.
- Renaming a preset deletes the old file and saves under the new
  name; the in-memory cache is updated atomically.
- **Missing-voice policy**: If `preset.voice_id` is set but the voice
  profile no longer exists, `MainWindow._on_apply_preset` shows a
  clear warning dialog and leaves the voice combo at "(No voice)". The
  application NEVER silently picks another voice. All other preset
  settings (emotion, style, prosody, parameters) are still applied.
- **Preset save logging**: After a successful save, the engine log
  records: preset name, voice_id, resolved voice_name, emotion, style,
  speed, pitch, delivery, and the absolute file path on disk.
- **Preset apply logging**: After a successful apply, the engine log
  records: preset name, voice_id, resolved voice_name, and reference
  audio path.

## Test Plan

Automated tests live in `tools/verify_functional_integrity.py` and
exercise the full preset lifecycle without requiring the Higgs model
to be loaded. Run with:

```
python3 tools/verify_functional_integrity.py
```

### Preset save + persistence + restart (Section A)
1. Create a PresetManager in a temp directory.
2. Save a preset with all 14 fields populated.
3. Verify the preset file exists on disk.
4. Verify the file contents include: name, voice_id, emotion, style,
   speed, pitch, delivery, temperature, top_p, top_k, max_new_tokens,
   seed, append_silence, auto_play, normalize_output.
5. Create a FRESH PresetManager (simulating application restart).
6. Verify the preset is loaded from disk.
7. Verify every field is restored to the exact saved value.

### Preset apply (Section B)
1. Save a preset with known values.
2. Reload it via a fresh PresetManager.
3. Verify every persisted field matches the original.

### Missing-voice handling (Section C)
1. Create a voice profile with a reference WAV.
2. Save a preset referencing that voice.
3. Delete the voice profile.
4. Reload the preset — its `voice_id` is still set but the voice is
   gone.
5. Verify `VoiceManager.get_profile(preset.voice_id)` returns None.
6. Verify NO silent substitution to another voice occurs.

### Voice selection chain (Section D)
1. Create Voice A with a reference WAV at path P_A.
2. Create Voice B with a DIFFERENT reference WAV at path P_B.
3. Verify Voice A and Voice B have different ids.
4. Verify Voice A and Voice B have different reference paths.
5. Verify `VoiceManager.get_profile(A)` resolves to Voice A (not B).
6. Verify `VoiceManager.get_profile(B)` resolves to Voice B (not A).
7. Verify `VoiceManager.validate()` returns no blocking warnings for
   either voice.

### Reference audio failure handling (Section E)
1. Create a voice profile with a reference WAV.
2. Delete the reference WAV file from disk (keep the profile).
3. Verify `VoiceManager.validate()` reports the missing file.
4. Verify `GenerationManager._load_reference_audio()` RAISES
   `ReferenceAudioMissing` (does NOT return `(None, 0)`).
5. Verify the empty-path case also raises `ReferenceAudioMissing`.

### Manual UI test (requires running the application)
1. Start the application.
2. Open Preset Manager (Library → Presets).
3. Save a preset with a known voice + emotion + style + parameters.
4. Note the storage path shown in the dialog header.
5. Verify the preset file exists at that path.
6. Close the application.
7. Restart the application.
8. Open Preset Manager — verify the preset is listed.
9. Change all settings to different values.
10. Apply the preset — verify all settings are restored.
11. Delete the voice profile referenced by the preset (via Voice Library).
12. Open Preset Manager — verify the preset row shows "(voice missing)".
13. Apply the preset — verify a warning dialog appears and the voice
    combo is left at "(No voice)" (no silent substitution).
