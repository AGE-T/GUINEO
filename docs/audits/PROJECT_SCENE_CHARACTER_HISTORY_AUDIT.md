# SpeechStudio — Project / Scene / Character / History Architecture Audit

- **Status**: AUDIT ONLY — No code changes
- **Date**: 2025-01
- **Purpose**: Pre-implementation audit against the approved architectural direction

---

## 1. Current Project Implementation

**Status: STRING LABEL ONLY — NO ENTITY**

| Aspect | Current State |
|--------|--------------|
| Data model | `str` field `"Default"` on `MainWindow`, `GenerationRequest`, `GenerationResult` |
| Persistence | `settings["application"]["current_project"]` — a single string |
| Project file (`.sproj`) | JSON with: `project` (name), `text` (editor content), `voice_id`, `emotion/style/speed/pitch/delivery`, `parameters`, `saved_at` |
| What `.sproj` does NOT contain | Scene list, character roster, speaker_voice_map, batch jobs, Narration Blocks, generated audio references |
| Project directory | Does NOT exist — no `projects/<name>/` folder structure |
| New Project action | Updates in-memory label only; does NOT create any file or directory |
| Open Project action | Loads `.sproj` (single-prompt state); does NOT restore scenes, characters, or speaker mappings |

**Verdict: "Project" is a string label, not a first-class entity. The `.sproj` format stores single-prompt editor state, not project structure.**

---

## 2. Current Scene Implementation

**Status: PARTIALLY EXISTS — DIALOGUE-ONLY, NO PER-SCENE EDITOR STATE**

| Aspect | Current State |
|--------|--------------|
| Data model | `DialogueScene` dataclass in `engine/scene_persistence.py` |
| `DialogueLine` fields | `speaker`, `text`, `voice_id`, `emotion`, `style`, `speed`, `pitch`, `delivery`, `output_path`, `output_duration`, `generation_status`, `output_filename` |
| Scene file (`.scene.json`) | JSON format `"speechstudio-scene"`, version 1 |
| What `.scene.json` does NOT contain | Narration Blocks, block overrides, generation parameters |
| Scene creation | In-memory only — appends to `sidebar._scenes`; does NOT save to disk |
| Scene selection | Changes label only; does NOT load scene data; editor content NOT restored |
| Scene save | Builds `DialogueScene` from `BatchJob` list — only works with batch jobs |
| Scene list | In-memory `List[dict]` on `Sidebar._scenes` — never persisted as a collection |

**Critical gap: Scene switching does NOT restore editor content. Selecting a different scene just changes the label.**

---

## 3. Current Character Implementation

**Status: DOES NOT EXIST**

| Aspect | Current State |
|--------|--------------|
| Character dataclass | ❌ None |
| Character storage | ❌ None |
| Character UI | Sidebar nav item "Characters" — **`characters_clicked` signal NEVER connected** |
| Closest concept | `BatchJob.speaker` (bare string) + `speaker_voice_map` (flat dict) |
| `speaker_voice_map` | Global in settings.json — NOT per-project |

---

## 4. Current History Implementation

**Status: EXISTS BUT INCOMPLETE — NO SCENE/CHARACTER LINKAGE**

| Aspect | Current State |
|--------|--------------|
| Storage | One JSON file per entry in `settings/history/` |
| Fields | `id`, `timestamp`, `output_path`, `prompt`, `parameters`, `voice_profile`, `project` (str) |
| Missing | ❌ `scene_id`, ❌ `character`/`speaker` |
| History → Scene link | ❌ Permanently lost on save |
| History UI | Legacy sidebar History tab crashes (`_focus_history` calls non-existent `_tabs`) |

---

## 5. Current Recents Implementation

**Status: DOES NOT EXIST AS A CONCEPT**

- "Recent Scenes" is hardcoded, not context-aware, not MRU, no max items.

---

## 6. Existing Legacy Systems

| System | Status |
|--------|--------|
| `ui/panels/sidebar.py` | DEAD CODE — imported but never instantiated |
| `_focus_history()` | **LATENT BUG** — calls `self._sidebar._tabs` which doesn't exist on `ProjectSceneSidebar` |
| `ProjectSceneBar.new_scene_requested` | DEAD SIGNAL — declared but never emitted |
| 3 primary-nav signals | UNWIRED — `projects_clicked`, `scenes_clicked`, `characters_clicked` never connected |
| `current_scene` in DEFAULT_SETTINGS | MISSING — written at runtime, not in defaults |

---

## 7. Duplicate / Conflicting Concepts

| Conflict | Details |
|----------|---------|
| `.sproj` vs `.scene.json` | Two disconnected formats — opening one doesn't affect the other |
| `current_project` string vs Project entity | String label mistaken for a Project |
| `speaker_voice_map` vs Character entity | Flat dict is de facto Character→Voice, but no metadata |
| Legacy vs new sidebar | Legacy imported but dead; new sidebar missing wired signals |

---

## 8-17. See Full Audit Document

The complete audit with proposed data model, navigation model, migration plan, backwards compatibility, export structure, files to change, files unchanged, risks, and implementation order is in the full document.

**Key findings:**
1. Project is a string, not an entity
2. Scene switching doesn't restore editor content
3. Character doesn't exist
4. History loses scene/character linkage
5. `_focus_history` is a latent crash bug
6. Three primary-nav signals are unwired
7. `speaker_voice_map` is global, not per-project
