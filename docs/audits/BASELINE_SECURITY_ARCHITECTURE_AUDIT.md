> **Archived provenance note.** This is the independent adversarial
security / reliability / architecture audit of the pre-remediation
baseline (the SpeechStudio-era `SpeechStudio_AUDIT_FINAL.zip` tree, 231
files) that opened the remediation engagement. It is preserved verbatim
for provenance: its verdict describes THAT historical state, not the
current application. The remediation rounds that followed are documented
in `DEVELOPMENT_LOG.txt` (P3.30 onwards), and the behaviours they fixed
are regression-locked by the test suite in `tests/`.

---

# SpeechStudio — Independent Third-Party Security, Reliability & Architecture Audit Report

| | |
|---|---|
| **Audit target** | `SpeechStudio_AUDIT_FINAL.zip` (2,096,337 bytes, SHA-256 `3d0085c1626bc4755a0118e2af79291489e0cb4e0ac61471a51e99861cec4102`) |
| **Audited source** | 101 Python files, 231 total files, extracted to a read-only working copy |
| **Audit type** | Adversarial, read-only, independent (auditor had zero involvement in development) |
| **Method** | Source review + static analysis + **runtime probes** (real `Engine` + real `MainWindow` instantiated offscreen, ~35 probe scripts) + existing test suite execution + import-graph analysis |
| **Handoff document** | `SpeechStudio_AUDIT_HANDOFF.md` (treated as background claims, **not** as authority — every claim independently re-verified) |
| **Verdict** | **NOT RELEASE READY** |

---

## 1. Vezetői összefoglaló (Executive Summary — Hungarian)

A SpeechStudio átment egy független, adversarial (hiba-kereső) auditon. A kézbesítő dokumentum (handoff) által leírt alapszintű működés **tényleg működik**: a 382 teszt tényleg lefut PASS-ra, a 5 hivatalos verifikációs szkript tényleg zöld, a projekt/jelenet perzisztencia körútja mezőre pontos, a prompt-háromszög (Preview == Advanced Preview == generált prompt) managed módban tart, és a hang-rangsor (Scene → kontrollpanel) a dokumentált módon viselkedik.

Ugyanakkor az audit **1 CRITICAL, 15 HIGH, ~20 MEDIUM és ~18 LOW** súlyosságú, többnyire **futásidejű bizonyítékkal alátámasztott** defektust tárt fel, amelyek közül több **adatvesztést, összeomlást, biztonsági rést és funkcionális visszaesést** okoz a normál felhasználói útvonalon:

- **CRITICAL**: minden sikeres generálás után a worker-szálból közvetlenül módosítja a Qt widgeteket a history-frissítés (nemdefiniált viselkedés / véletlenszerű crash kockázat).
- **Adatsértési hibák**: egy `voice_profile_id=None` jelenet "nincs hang" állapota megsemmisül és átöröklődik egy másik jelenet hangja; a blokk-szintű emotion/style felülírás tirálisálja a jelenet-globális beállítást; a `Scene.parameters=None` ugyanígy szennyeződik; a beállításfájl három különálló írója egymást írja felül csndben.
- **Biztonság**: rosszindulatú export fájl elolvas/töröl tetszőleges fájlokat az alkalmazás gyökérén kívül (path traversal); egy hibás settings.json vagy history JSON az alkalmazást indíthatatlanná teszi / a teljes history-t megmérgezi.
- **Funkcionális regressziók**: az **Esc és F11 gyorsbillentyűk halottak** (ambiguus regisztráció), a Settings menü **két párbeszédablakot** nyit, a hangimport avatar/metaadattal **mindig hibát jelez** (nemlétező engine-metódusok hívása), az "Allow SFX" kapcsoló végponttól végpontig **no-op**.
- **Proveniencia**: a ZIP SHA-256 hash-e **nem egyezik** a handoff-ban dokumentálttal.

A handoff "382 teszt mind zöld" állítása igaz — de a teljes tesztcsomag **2,14 másodperc** alatt fut le (átlag ~5,6 ms/teszt), ami jelzi, hogy túlnyomórészt sekély strukturális (forrásszöveg-ellenőrző) tesztekről van szó, amelyek éppen a fenti, integrációs szintű hibákat nem tudják elkapni. **Az ajánlás: NOT RELEASE READY** — a fenti hibák javítása után újraauditálás szükséges.

---

## 2. Audit Scope, Method & Ground Rules

- **Sources of truth**: the ZIP content and actual runtime behavior. The handoff document and its PASS conclusions were treated as *claims to verify*, never as evidence.
- **Read-only compliance**: zero source files modified (verified by all four audit agents; only runtime artifacts — `__pycache__`, logs, and a rewritten `settings/settings.json` caused by the app's own module-relative APP_ROOT design on every MainWindow instantiation — changed during probing; disclosure in §12).
- **Runtime instrumentation**: probe scripts under `/tmp/audit_probes/` (~35 scripts) instantiate the **real** `Engine` and `MainWindow` offscreen (`QT_QPA_PLATFORM=offscreen`) and monkeypatch **at runtime only** (e.g. capture `engine.generate` calls, stub dialogs) to observe what actually crosses component boundaries.
- **Reproduction environment**: Python 3.12.14, PySide6 6.11.2, pytest 9.0.2, Linux headless.
- All "runtime-proven" statements below were observed executing real production classes, not mocks of them.

---

## 3. Handoff Document — Claim-by-Claim Verification

| # | Handoff claim (§) | Independent verdict | Evidence |
|---|---|---|---|
| 1 | "382 tests, all pass" (§4, §12) | **TRUE** | Full suite executed: `382 passed in 2.14s` |
| 2 | "5/5 verification scripts pass" (§12) | **TRUE, with caveat** | The 5 listed scripts pass. A 6th script in `tools/` — `verify_transport_gl.py` — **FAILS (180/183, exit 1)**: it asserts the pre-rewrite transport architecture. Misleading green. |
| 3 | `ui/settings_dialog.py` path (§14) | **FALSE** | Actual location: `ui/panels/settings_dialog.py`. (Task-brief's path correction confirmed.) |
| 4 | ZIP SHA-256 `b7575c08…`, 2,096,146 bytes (§12) | **FALSE** | Actual: `3d0085c1626bc4755a0118e2af79291489e0cb4e0ac61471a51e99861cec4102`, 2,096,337 bytes. **The delivered artifact is not byte-identical to the attested one** (file counts match: 101 py / 14 test files). Provenance discrepancy. |
| 5 | Voice precedence: "Scene VP (control panel) > Character VP (reference only)" (§10) | **TRUE for single-speaker Generate — and incomplete** | Runtime-confirmed: `GenerationRequest.voice_id == control panel selection`, Character VP never reaches the request. **Undocumented defects found**: (a) a Scene with `voice_profile_id=None` inherits the *previous* scene's voice (stale leak) and its None state is destroyed on save; (b) in multi-speaker, Character VP is never consulted for pre-selection. See §5. |
| 6 | Defect fixes C1, C2, C5, H1, M1, L1 (§5) | **TRUE** (C1, C2, C5, M1 runtime-verified; H1 via real dialog smoke; no counter-evidence for L1) | C1: None emotion/style → no token, no crash. C2: speaker/scene/character survive `BatchJob.to_request()`. C5: character_id/speaker set in `_start_generation`. M1: read-before-delete + correct app root. |
| 7 | False positives C3/C4 (§6) | **C4 TRUE; C3 REFUTED at integration level** | Seed clearing works (C4). But "allow_sfx works" (C3) is only true for `FriendlyView.get_parameters()` **in isolation** — through the actual `RightPanel.get_parameters()` path the value is dropped and SFX stays ON end-to-end (defect SS-H06). The test `test_audit_defect_fixes.py` exercised the isolated method only. |
| 8 | Removed: Developer Mode, Library sidebar, Block toolbar, "Show Higgs tokens", Keep-model-loaded, Collapse-sidebar, dup auto-play (§7) | **TRUE** (all runtime-verified absent) | Leftovers are inert/documented (empty Developer settings tab, docstring mentions, stale registry entry F-703 — LOW defects SS-L04…). Block toolbar removal lost **no function** (all 8 operations relocated & reachable). |
| 9 | "Runtime tests ~260 / AST ~100 / unit ~22" (§4) | **PARTIAL / optimistic** | Test-type census: only 5 files use `ast.parse` explicitly, 8 instantiate QApplication — the suite is dominated by source-string/structural assertions; measured suite speed (2.14 s / 382 tests ≈ 5.6 ms avg) is inconsistent with deep runtime coverage. See §9. |
| 10 | Fullscreen fix (§9 "manual") | **Code-level TRUE; KEY IS DEAD** | Geometry save/restore verified; no StaysOnTopHint; but **F11 keypress does nothing** (ambiguous shortcut overload — SS-H12). Real-display flicker: MANUAL VERIFICATION REQUIRED. |
| 11 | Top menus implemented via QToolButton+InstantPopup (§9) | **TRUE structurally; behavior defects found** | All 33 actions connected (dumpObjectInfo audit). But: Esc dead (SS-H13), Settings opens twice (SS-H14), Font Status unreachable (SS-M11), Export button mislabeled (SS-M12). Real-display popup clicks: MANUAL. |
| 12 | Settings inventory "Final" all WORKING (§8) | **PARTIAL** | Tabs/controls exist and persist per-instance; but the split-brain multi-instance architecture silently reverts settings written through the other instance (SS-H04). "Normalize output volume → −18 LUFS" and auto-play paths not re-verified individually (no counter-evidence). |

---

## 4. Test & Verification Suite Quality Audit

**Suite run**: `382 passed in 2.14s` — handoff number reproduced exactly.

**Type census (by file)**:

| Measure | Value |
|---|---|
| Test functions | 382 (in 14 files) |
| Files explicitly using `ast.parse` | 5 |
| Files instantiating `QApplication` | 8 |
| Files importing production code | 13 / 14 |
| Suite wall time | 2.14 s (~5.6 ms/test avg) |

**Key structural weaknesses (why 382 green ≠ safe)**:

1. **Isolation-level assertions hide integration-level defects.** Example: C3 "allow_sfx works" is tested on `FriendlyView.get_parameters()` alone; through `RightPanel` the value is dropped (SS-H06) — the exact class of bug the suite was believed to exclude.
2. **Per-instance persistence is tested; cross-instance coherence never is.** `test_p3_16_settings_persistence.py` validates one SettingsManager round-trip, while production runs **three** instances (plus a raw-JSON 4th writer) over the same file (SS-H04).
3. **No negative/hostile-input tests** for JSON robustness (SS-H09/H10), path traversal (SS-H08), hostile filenames (SS-M08), or nonexistent-API calls (SS-H11).
4. **No thread-affinity tests** — the CRITICAL cross-thread UI mutation (SS-C01) is structurally invisible to the suite.
5. **No keyboard-shortcut tests** — both Esc and F11 are dead in the shipped build (SS-H12/H13) despite README advertising them.
6. **`verify_architecture.py` blind spots**: top-level-import scanning only; the two lazy `PySide6` imports inside `engine/batch_manager.py` (architecture breach, SS-M15) pass undetected. `verify_transport_gl.py` is stale and fails (asserts pre-rewrite architecture).

---

## 5. VOICE PRECEDENCE — Special Report (highest-priority mandate)

The mandate: determine, at runtime, which value actually reaches the generation request and the model call — for **single-speaker**, **multi-speaker**, and **character-based speaker resolution** — across Plain Text, Narration Blocks, Generate Long, and the Friendly/Advanced panels. No precedence direction was assumed; both were tested.

### 5.1 Single-speaker (Plain Text / Narration Blocks → Generate)

**Authoritative voice = `Scene.voice_profile_id` via the control panel dropdown. CONFIRMED at runtime.**

- Probe S1: Scene VP = `SceneVoice_AAA`, selected Character VP = `CharVoice_BBB` → captured `GenerationRequest.voice_id == SceneVoice_AAA`. The Character's VP **never** reaches `voice_id`; only `character_id` + `speaker` (character name) ride along for context (`ui/main_window.py:1851-1863`, comment explicitly documents "we do NOT override voice_id — the dropdown wins").
- Scene switching restores the dropdown from `Scene.voice_profile_id` (`_load_scene_state`, main_window.py:3311-3320) and `_save_current_scene_state` persists the dropdown back into the Scene (3274-3277).
- **HOWEVER — runtime-proven defects in this path**:
  - **SS-H01 (stale leak)**: `_load_scene_state` restores the dropdown only `if scene.voice_profile_id:` — loading a Scene with `voice_profile_id=None` leaves the *previous* scene's voice selected. Probe S2: after loading Scene A (VP_A) then Scene B (VP=None), Generate in Scene B sent **VP_A** to the engine. The scene silently generates with the wrong voice.
  - **SS-H01 (None-state destruction)**: Probe S3: A→B switch + `_save_current_scene_state` wrote `VP_A` into Scene B — Scene B's "no voice" state is permanently destroyed on save. A scene can never durably hold "no voice" once any sibling scene has one.
- Handoff §10's precedence statement is therefore *correct but incomplete*: it documents the winner but not the leak/contamination failure mode of the None case.

### 5.2 Multi-speaker / Generate Long

**Precedence actually implemented: `speaker_voice_map` (explicit user mapping in the Long Narration dialog) > control panel default (= Scene VP). Character.voice_profile_id is never consulted.**

- Probe S5: with map `{CAPTAIN→CharVoice, NARRATOR→SceneVoice}`, `BatchJob.voice_id` per part correctly followed the map; parts without mapping fall back to the control panel default. `BatchJob.to_request()` forwards voice/speaker/scene/character (C2 fix confirmed).
- Probe S4 (pre-selection in the dialog): with a Character named `CAPTAIN` holding `voice_profile_id=CharVoice` **and** `$CAPTAIN:` lines in the text, the speaker→voice combo pre-selects the **control panel default voice** (or the globally saved map), **not** the Character's assigned voice. The user must manually assign each speaker on every project unless the global saved map happens to match (SS-M16).
- **SS-M17 (cross-project leak)**: the speaker→voice map persists in `application.speaker_voice_map` — a **global** setting. A mapping created in Project 1 pre-selects (and, on Generate, applies) in Project 2 for identically-named speakers, with no project scoping.
- **Batch `character_id` is uniform, not per-part**: `_start_long_narration` stamps `character_id=self._selected_character_id` onto *every* job regardless of the part's speaker (main_window.py:2648) — with no character selected it is `None` even when a same-named Character exists (probe S5: `character_id: null` for the CAPTAIN part).

### 5.3 Character-based speaker resolution

**Does not exist.** Selecting a Character (sidebar or Character dialog):
- sets `_selected_character_id` for sidebar highlighting and attaches `character_id`/`speaker` to the *next single-prompt* request as context only;
- **never** changes the control panel voice dropdown, **never** influences multi-speaker mapping, **never** overrides `Scene.voice_profile_id`.

The `Character.voice_profile_id` field is therefore a **data-model reference only** (surfaced as subtitle text in the Characters context list and carried into export/reimport), with no runtime voice-selection effect in any generation workflow. If the product intent is "character-driven casting", that feature is absent (SS-M16). If the intent is "Scene-only authority", the field's UI prominence (subtitle, dialog) overstates its effect — either way the contract is unclear to users.

### 5.4 Voice precedence verdict table

| Scenario | Winner at runtime | Character VP used? | Defects |
|---|---|---|---|
| Single-speaker Generate (Scene VP set) | Scene VP | No (context only) | none |
| Single-speaker Generate (Scene VP = None) | **Previous scene's VP (stale)** | No | SS-H01 |
| Scene A→B→A switch | Restored per scene | — | None-voice state destroyed (SS-H01) |
| Multi-speaker, speaker mapped | speaker_voice_map | Only if user manually mapped it | SS-M17 global map leak |
| Multi-speaker, speaker unmapped | Control panel default (= Scene VP) | No | SS-M16 no auto-preselect |
| Character selected + Generate | Control panel dropdown | No | by-design, but see §5.3 |

---

## 6. Defect Register

Severity scale: CRITICAL (crash/data corruption/security breach on normal path) / HIGH (feature broken, data loss in realistic scenarios, security issue with preconditions) / MEDIUM (broken edge case, reliability, misleading UX) / LOW (cosmetic, docs, latent).

Counts: **1 CRITICAL · 15 HIGH · 20 MEDIUM · 18 LOW = 54 defects** (LOW items compacted in §6.5).

### 6.1 CRITICAL

---

#### SS-C01 — Cross-thread Qt widget mutation after every successful generation

| Field | Value |
|---|---|
| **Severity** | CRITICAL |
| **Category** | Concurrency / crash risk |
| **Affected feature** | Generation completion → sidebar/history refresh |
| **Affected file/component** | `engine/engine.py:287` (emit) + `ui/main_window.py:2129-2130` (`_on_history_changed` → `_refresh_sidebar`) |
| **Observed** | Runtime probe P3: the `HISTORY_UPDATED` handler executed on the generation worker thread (`probe-gen_0`) and directly rebuilt sidebar widgets (`update_voices()`, `populate_voices()`, …). Qt widget access from a non-GUI thread is undefined behavior. |
| **Expected** | All UI mutations marshalled to the UI thread (as done one line later for `GENERATION_FINISHED` via `_generation_finished_signal`). |
| **Exact reproduction** | Instantiate MainWindow + Engine; complete any generation; observe handler thread (`threading.current_thread().name` inside a probe-subscribed handler). Every successful generation triggers it. |
| **Root cause** | Accidental omission: `_on_history_changed` subscribes directly to the EventBus while the sibling `GENERATION_FINISHED` path correctly marshals. |
| **Impact** | Intermittent hard crashes / corrupted widget state after generation; timing-dependent, worst on slower machines or large history — the classic "works in testing, crashes in the field" class. |
| **Existing test coverage** | None (thread affinity untested). |
| **Missing test coverage** | Thread-affinity assertion on all EventBus handlers (handler must run on `QApplication.thread()`). |
| **Recommended remediation** | Route `HISTORY_UPDATED` through a marshalling signal like `_generation_finished_signal`; add a debug-mode thread-affinity assert for all `_on_*` handlers. |

---

### 6.2 HIGH

---

#### SS-H01 — Scene voice: stale cross-scene leak + destruction of the "no voice" state

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Data integrity / voice precedence |
| **Affected feature** | Scene switching; Scene voice selection (P3.13); Generate |
| **Affected file/component** | `ui/main_window.py:3311-3320` (`_load_scene_state`, `if scene.voice_profile_id:` guard), 3274-3277 (save) |
| **Observed** | Runtime: (a) Scene B with `voice_profile_id=None` loaded after Scene A (VP_A) → dropdown keeps VP_A → Generate in B sends **VP_A**; (b) A→B switch + save writes VP_A into B — B's None state permanently destroyed. |
| **Expected** | A None-voice scene selects "(No voice)" and persists None. |
| **Exact reproduction** | Build project with Scene A (VP set) + Scene B (VP=None); switch A→B; Generate in B (wrong voice); switch away and back (B.voice_profile_id now VP set). Probe `probe_voice2.py` S2/S3. |
| **Root cause** | Truthy-guard skips `set_selected_voice_id(None)` reset; unconditional dropdown save then contaminates the scene entity. |
| **Impact** | Wrong-voice audio generated silently; user's explicit "no voice" choice irreversibly overwritten — data corruption in the core scene model. |
| **Existing test coverage** | `test_p3_13_scene_voice_persistence.py` tests persistence of **set** values only, never the None case. |
| **Missing test coverage** | None→set→None round-trip; cross-scene contamination matrix. |
| **Recommended remediation** | Unconditional `set_selected_voice_id(scene.voice_profile_id)` (the setter already accepts None → index 0). |

---

#### SS-H02 — Block-scoped emotion/style/prosody edits clobber the scene-global value

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Data integrity / duplicated state sync |
| **Affected feature** | Narration Blocks overrides; Scene global emotion/style/speed/pitch/delivery |
| **Affected file/component** | `ui/main_window.py:1024-1057` (`_on_emotion_changed` etc.); documented-but-dead signal `right_panel.py:72` `block_override_changed` (never emitted anywhere) |
| **Observed** | Runtime probe P2: global emotion 'Sad' → select block → set emotion 'Awe' → `mw._emotion == 'Awe'` (global overwritten!) **and** block override 'Awe' → deselect → panel shows 'Awe' as the new global; `_save_current_scene_state` persists the clobbered global. |
| **Expected** | Docstrings (main_window.py:981, right_panel.py:424) promise block-scoped edits write **only** the block override, leaving the global untouched. |
| **Exact reproduction** | Set a global emotion different from a block's; edit emotion with a block selected; deselect. |
| **Root cause** | Handlers write both targets unconditionally; the intended architecture (emit `block_override_changed` instead) was never wired. |
| **Impact** | Scene-level defaults silently destroyed by routine block editing; downstream blocks inheriting the global change behavior; persisted. |
| **Existing test coverage** | None (no test edits overrides with a block selected and asserts global immutability). |
| **Missing test coverage** | Global-unchanged invariant test around block override edits. |
| **Recommended remediation** | In `_on_*_changed`, branch on `_active_block_id`: write override only (or emit `block_override_changed` as documented); keep global writes for deselected state. |

---

#### SS-H03 — Scene.parameters=None leak & contamination (params analog of SS-H01)

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Data integrity / duplicated state sync |
| **Affected feature** | Scene switching; generation parameters persistence |
| **Affected file/component** | `ui/main_window.py:3304` (`if scene.parameters:` guard) + 3262-3265 (save, swallowed on exception) |
| **Observed** | Runtime: Scene A saved with temperature 0.777 → switch to Scene B (`parameters=None`) → panel returns A's params (0.78) → saving B persists them into B. |
| **Expected** | None parameters reset the panel to defaults. |
| **Exact reproduction** | Two scenes, one with custom params, one fresh; switch; read `control_panel.get_parameters()`. |
| **Root cause** | Same truthy-guard anti-pattern as SS-H01, on the parameters path. |
| **Impact** | Scene generation silently runs with another scene's temperature/top_p/seed; contamination persisted. |
| **Existing test coverage** | None for the None case. |
| **Missing test coverage** | Params None round-trip. |
| **Recommended remediation** | Unconditional `set_parameters(scene.parameters or GenerationParameters())`. |

---

#### SS-H04 — settings.json split-brain: 3 SettingsManager instances + raw-JSON 4th writer silently revert each other

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Settings persistence / architecture |
| **Affected feature** | Settings dialog, View→Theme, window geometry, batch dialog geometry, startup reader |
| **Affected file/component** | `SpeechStudio.py:244`, `engine/engine.py:100`, `ui/main_window.py:117` (three instances); `ui/panels/batch_generation.py:379-404` (raw read-modify-write); `engine/settings_manager.py:119-127` (non-atomic whole-file rewrite, cache loaded once at construction) |
| **Observed** | Runtime probe_c4 (same file, production classes): `sm1.set("theme","light")` → disk=light; `sm2.set("other_key","X")` → disk **theme reverted to dark** (sm2 rewrote the whole file from its stale construction-time cache). Probe_c3: MainWindow's manager uses a **different file** than the Engine's whenever `Engine(app_root=…)` differs from install dir (`main_window.py:56` hardwires APP_ROOT). Settings dialog shows stale theme after a View-menu switch. |
| **Expected** | One owner of settings.json; writes never revert other keys; atomic. |
| **Exact reproduction** | Construct two SettingsManagers on the same file; set disjoint keys alternately; read the file after each. |
| **Root cause** | P3.16 consolidated usages **inside** MainWindow only; Engine and the entry point kept private instances; whole-file non-locked rewrites from divergent caches. |
| **Impact** | User settings (theme, paths, defaults) silently revert — the exact symptom class P3.16 claimed to fix; also torn/corrupt file risk on crash (recovery → defaults). |
| **Existing test coverage** | `test_p3_16_settings_persistence.py` — single-instance round-trip only. |
| **Missing test coverage** | Cross-instance coherence; atomicity/crash-mid-write. |
| **Recommended remediation** | Single shared SettingsManager (injected into Engine and MainWindow); reload-before-write or shared cache; atomic tmp-file+rename. |

---

#### SS-H05 — Center "Preview" ignores Raw Mode: preview ≠ what the model receives

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Prompt hard contract / UI truthfulness |
| **Affected feature** | Raw Mode; Preview tab |
| **Affected file/component** | `ui/panels/narration_editor.py:1441-1464` (`_update_preview` never checks `_raw_mode`) |
| **Observed** | Runtime: raw text `<\|emotion:fear\|>Stay in the light…` with global Elation/Whispering → center preview showed `<\|emotion:elation\|><\|style:whispering\|><\|emotion:fear\|>Stay in the light…` while `request.text` was the literal raw text. |
| **Expected** | ADR 003 §6/§8: preview == model input; Raw Mode = user owns the prompt. |
| **Exact reproduction** | Enable Raw Mode; type token-laden text; set any global emotion; compare Preview tab vs generated request. |
| **Root cause** | Preview path uses PromptOptimizer+PromptBuilder unconditionally; only `_build_prompt` branches on raw mode. |
| **Impact** | User previews (and trusts) a prompt that is NOT what will be sent — direct violation of the app's core promise; fabricated duplicate-emotion conflicts shown as if real. |
| **Existing test coverage** | 3-way equality tests cover managed mode only. |
| **Missing test coverage** | Raw-mode preview equality. |
| **Recommended remediation** | Branch `_update_preview` on `is_raw_mode()`; render raw text verbatim. |

---

#### SS-H06 — "Allow SFX" toggle is a no-op end-to-end

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Functional no-op / parameter propagation |
| **Affected feature** | SFX tokens (laughter etc.) suppression toggle |
| **Affected file/component** | `ui/panels/right_panel.py:351` (delegation) + `ui/panels/control_panel.py:604-619` (GenerationSection drops allow_sfx) + `right_panel.py:333-341` (sync overwrites FriendlyView's value) |
| **Observed** | Runtime: toggle OFF (button shows "OFF") → `mw._control_panel.get_parameters().allow_sfx == True`; generated prompt still contains `<\|sfx:laughter\|>`. |
| **Expected** | allow_sfx=False strips SFX tokens from the compiled prompt. |
| **Exact reproduction** | Toggle Allow SFX off; enter text with an SFX block; Generate; inspect request/prompt. |
| **Root cause** | `RightPanel.get_parameters()` delegates to AdvancedView→GenerationSection which never reads/stores the flag; FriendlyView's `_allow_sfx` is overwritten by the sync path (→ SS-M01). |
| **Impact** | The one control users have over SFX injection does nothing; prompts sent to the model silently differ from user intent. This **refutes** handoff §6's C3 "false positive" at the integration level. |
| **Existing test coverage** | `test_audit_defect_fixes.py` tests `FriendlyView.get_parameters()` in isolation — the exact seam where the value is lost is untested. |
| **Missing test coverage** | End-to-end `RightPanel.get_parameters().allow_sfx` + prompt-content test. |
| **Recommended remediation** | Persist allow_sfx in GenerationSection; include in its `get_parameters()`/`set_parameters()`; stop overwriting it in `_sync_advanced_to_friendly`. |

---

#### SS-H07 — Invalid semantic values: invalid token reaches the model OR silent tokenless fallback (two divergent behaviors)

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Prompt hard contract / validation |
| **Affected feature** | Emotion/style selection via presets, saved scenes, block overrides |
| **Affected file/component** | `engine/prompt_builder.py:486-496` (fallback emission), `engine/prompt_state.py:379-386` (swallow → plain text), `ui/main_window.py:886` (warnings discarded, literal `[]`) |
| **Observed** | Runtime: same invalid value `emotion="Bogus"` — via block override → literal `<\|emotion:bogus\|>` token emitted into `GenerationRequest.text`; via global/preset/scene-load → compile silently falls back to tokenless prompt, generation proceeds, zero user-visible warning. |
| **Expected** | One deterministic outcome: reject with a surfaced error (or normalize with a warning shown). |
| **Exact reproduction** | Preset/scene JSON with invalid emotion → Generate; vs block override dict with invalid emotion → Generate; compare prompts. |
| **Root cause** | Two different invalid-handling strategies coexist (value-fallback emission vs compile-catch fallback); the UI drops the compiler's warnings list. |
| **Impact** | Model receives garbage tokens (undefined model behavior) or loses ALL emotion/style direction — neither visible to the user. |
| **Existing test coverage** | None for invalid values. |
| **Missing test coverage** | Invalid-value matrix (block vs global vs preset vs scene). |
| **Recommended remediation** | Validate at UI ingestion boundaries (preset apply, scene load, block editor); propagate compiler warnings to the status bar. |

---

#### SS-H08 — Path traversal: arbitrary file read/exfiltration via crafted project import → re-export

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Security / path traversal |
| **Affected feature** | Project export/import (portable directory format) |
| **Affected file/component** | `engine/project_exporter.py:259` (unvalidated join), `engine/project_manager.py:289-362` (`import_project_dir` keeps paths verbatim) |
| **Observed** | Runtime probe: malicious `project.json` with `"audio_assets":[{"output_path":"../../../audit_probes/b1b_SECRET.txt"}]` → import → re-export(include_audio=True) **copied the secret file from outside app_root into the export bundle**. Absolute paths also escape (`os.path.join(app_root, "/abs")` discards app_root). |
| **Expected** | Reject/normalize any path escaping app_root at import and at export-copy time. |
| **Exact reproduction** | Hand-craft exported dir with traversal output_path; File→Import Project; File→Export Project (include audio). |
| **Root cause** | Model-supplied relative path joined without containment check; import performs zero sanitization. |
| **Impact** | An attacker who supplies a "project" (shared/collaboration scenario) can exfiltrate arbitrary user files into a bundle the user then shares back. |
| **Existing test coverage** | Export round-trip tests use benign paths only. |
| **Missing test coverage** | Traversal/absolute-path/unc-path import rejection tests. |
| **Recommended remediation** | `os.path.realpath` containment assertion (path must stay under app_root) on import and before every export copy. |

---

#### SS-H09 — Engine startup crash on malformed settings.json

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Robustness / availability |
| **Affected feature** | Application startup |
| **Affected file/component** | `engine/settings_manager.py:109-117` (excepts only JSONDecodeError/OSError) |
| **Observed** | Runtime, real Engine: top-level LIST/STRING/INT → AttributeError in `_deep_merge`; UTF-16 bytes → UnicodeDecodeError; deep nesting → RecursionError. All three crash `Engine(app_root=…)` — app cannot start, no self-heal. |
| **Expected** | Any unparseable file → defaults + warning (spec: settings must never block startup). |
| **Exact reproduction** | Write `"[]"` (or UTF-16 content) into settings/settings.json; launch. |
| **Root cause** | Narrow except; no `isinstance(data, dict)` check; default encoding open. |
| **Impact** | One corrupted byte in a user-writable file bricks the application with an unhelpful traceback. |
| **Existing test coverage** | Only valid + JSONDecodeError cases. |
| **Missing test coverage** | Malformed-type matrix on every loader. |
| **Recommended remediation** | `except Exception` + type validation + backup-and-quarantine of the corrupt file. |

---

#### SS-H10 — One corrupt history file poisons the entire history; entry-schema crashes reach dialogs

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Robustness |
| **Affected feature** | History list, History view dialog, sidebar |
| **Affected file/component** | `engine/history_manager.py:199-219` (narrow excepts), `engine/models.py:149` (`data["id"]` KeyError), `ui/panels/history_view.py:254-255` (TypeError on string duration) |
| **Observed** | Runtime: a single UTF-16 history JSON → `list_entries()` raises → **all** history unreadable; real `HistoryViewDialog` constructor crashes on `voice_profile` missing `id` (KeyError) / `output_duration` as string (TypeError). |
| **Expected** | Per-entry tolerance: skip bad entries, keep the rest. |
| **Exact reproduction** | Drop one corrupt entry file into settings/history/; open History. |
| **Root cause** | HistoryEntry is a raw-dict wrapper without validation; except clauses miss UnicodeDecodeError/TypeError/KeyError/AttributeError. |
| **Impact** | Total history loss (UX-wise) from one bad file; crash paths reachable from the History dialog. |
| **Existing test coverage** | Valid entries only. |
| **Missing test coverage** | Corrupt-entry isolation tests. |
| **Recommended remediation** | Broad catch per entry + schema validation + quarantine counter. |

---

#### SS-H11 — Voice import with avatar or speaker metadata ALWAYS reports failure (nonexistent Engine API)

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Functional / API drift |
| **Affected feature** | Voice profile import dialog |
| **Affected file/component** | `ui/main_window.py:2986, 2989` calling `engine.import_voice_avatar(...)` / `engine.set_voice_speaker_metadata(...)` — **neither exists anywhere in engine/** (verified by grep + real Engine AttributeError) |
| **Observed** | Runtime probe with real Engine following the exact `_on_import_voice` sequence: profile + reference + transcript created on disk, then AttributeError → caught → user sees "Failed to import voice: …" **although the voice was created**; retrying duplicates profiles. |
| **Expected** | Import with avatar/gender/age/mood completes and persists metadata. |
| **Exact reproduction** | Import a voice with any avatar image or gender/age/mood filled. |
| **Root cause** | UI/engine API drift; no integration test for the dialog flow. |
| **Impact** | Core voice workflow appears broken; silent partial state; duplicate-profile pollution. |
| **Existing test coverage** | None for the import dialog flow. |
| **Missing test coverage** | End-to-end voice import incl. avatar/metadata. |
| **Recommended remediation** | Implement the two Engine methods or remove the calls + persist metadata via VoiceManager; make the handler transactional (cleanup on failure). |

---

#### SS-H12 — F11 fullscreen shortcut is DEAD (ambiguous shortcut overload)

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Keyboard shortcuts / regression |
| **Affected feature** | Fullscreen toggle via keyboard |
| **Affected file/component** | `ui/main_window.py:375-377` (P3.10 QShortcut) + `ui/panels/menu_bar.py:113` (QAction shortcut) |
| **Observed** | Runtime `QTest.keyClick(F11)` → "Ambiguous shortcut overload: F11"; window state unchanged. Menu item itself still works. |
| **Expected** | F11 toggles fullscreen (README_START_HERE.txt:72 advertises it). |
| **Exact reproduction** | Send F11 to the main window (probe: probe_c_feature_audit). Platform-independent ambiguity. |
| **Root cause** | Two Window-sccontext registrations of the same key → Qt disables both. |
| **Impact** | Documented primary shortcut dead on every platform. |
| **Existing test coverage** | test_p3_10 asserts the QShortcut exists — the collision itself untested. |
| **Missing test coverage** | keyClick → windowState assertion. |
| **Recommended remediation** | Single registration (drop the QAction shortcut or the QShortcut). |

---

#### SS-H13 — Esc is DEAD: Stop-generation, exit-fullscreen, close-search all unreachable

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Keyboard shortcuts / regression |
| **Affected feature** | Esc behaviors (×3) |
| **Affected file/component** | `menu_bar.py:70` (Stop QAction Esc) + `main_window.py:379-382` (QShortcut Esc) + `main_window.py:4410` (keyPressEvent) + `narration_editor.py:1840-1844` (search bar close) |
| **Observed** | Runtime: Esc keyClick → "Ambiguous shortcut overload: Esc"; fullscreen not exited; open search bar not closed. |
| **Expected** | README: "Esc → Stop generation"; plus exit-fullscreen and close-search behaviors. |
| **Exact reproduction** | Enter fullscreen → press Esc; or open Ctrl+F bar → press Esc. |
| **Root cause** | Same double-registration as SS-H12 (three claimants). |
| **Impact** | Cannot stop generation by keyboard; cannot leave fullscreen by keyboard; search bar sticks. |
| **Existing test coverage** | None at key level. |
| **Missing test coverage** | Esc keyClick matrix. |
| **Recommended remediation** | One authoritative Esc handler with explicit priority (stop > fullscreen > search), single shortcut registration. |

---

#### SS-H14 — Settings menu opens the dialog TWICE

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Menus / copy-paste defect |
| **Affected feature** | Settings dialog |
| **Affected file/component** | `ui/main_window.py:336` **and** `:345` — `self._menu_bar.connect("settings", self._on_open_settings)` duplicated |
| **Observed** | Runtime: one trigger → 2 SettingsDialog instances created (probe counter); user closes one modal, the second appears. |
| **Expected** | One dialog. |
| **Exact reproduction** | Trigger Settings from the menu once (probe verified instance count). |
| **Root cause** | Duplicated connect line. |
| **Impact** | Every Settings open shows stacked dialogs — immediate, user-visible on the primary path. |
| **Existing test coverage** | None (connection count untested). |
| **Missing test coverage** | Exactly-once dialog instantiation test. |
| **Recommended remediation** | Delete one line. |

---

#### SS-H15 — Dangerous silent `except: pass` cluster in the scene save/restore chain

| Field | Value |
|---|---|
| **Severity** | HIGH |
| **Category** | Error handling / data loss enabler |
| **Affected feature** | Scene state persistence & switching |
| **Affected file/component** | `ui/main_window.py:3262-3265` (params), `:3267-3270` (narration blocks), `:3274-3277` (voice), `:3298-3307` (restore block, all five setters in one try), `:3311-3320` (voice restore) |
| **Observed** | Static + runtime: five swallow-paths around the exact state that the voice/params bugs (SS-H01/H03) corrupt; a raise in the first setter skips the remaining restores while MainWindow fields are already mutated (partial restore). |
| **Expected** | State mutations either succeed fully or surface failure; block-state save failures must not be silent (blocks are user work). |
| **Exact reproduction** | Force one setter to raise (probe); observe the rest skipped silently. |
| **Root cause** | Defensive-try anti-pattern around state sync. |
| **Impact** | Silent narration-block loss on scene switch; masks/enables the H01/H03 corruption. |
| **Existing test coverage** | None for failure paths. |
| **Missing test coverage** | Fault-injection in save/restore. |
| **Recommended remediation** | Narrow, logged handling per field; fail loudly on block-state persistence. |

---

### 6.3 MEDIUM (compact 13-field entries)

**SS-M01 — Allow-SFX=OFF silently reset to ON by tab switch / advanced edits.**
`right_panel.py:309-331` (`_sync_advanced_to_friendly` pushes advanced params — allow_sfx always True per SS-H06 — back into FriendlyView). Runtime: `friendly._allow_sfx` False→True after `_switch_tab(0)`. Expected: user's toggle preserved. Repro: toggle off, switch Friendly↔Advanced. Root cause: sync direction overwrites. Impact: masks SS-H06, erodes trust. Coverage: none. Missing: toggle-survives-tab-switch test. Remediation: exclude allow_sfx from sync until SS-H06 fixed.

**SS-M02 — Generate Long: editing a part re-injects SFX tokens (allow_sfx not forwarded).**
`long_narration_dialog.py:462-469` (rebuild) + `main_window.py:2583-2593` (job_params copy omits allow_sfx). Runtime: part prompt with SFX stripped before edit → `<\|sfx:laughter\|>` present after edit; job params always True. Expected: user's suppression survives edit/rebuild. Root cause: field omitted in two copy paths. Impact: prompt contract broken for SFX-suppressed long narrations. Remediation: forward `base_params.allow_sfx` (and part state) into rebuild + job_params.

**SS-M03 — Stale seed: `set_parameters(seed=None)` never clears the field.**
`control_panel.py:601-602` (`if params.seed is not None:` — no else-clear). Runtime: seed 42 → seed None → `get_parameters().seed == 42`. Impact: seedless history regeneration silently reuses previous seed (reproducibility accident); scene save persists contaminated seed (main_window.py:2209/3263/3305/3694). Remediation: else-clear branch.

**SS-M04 — Arbitrary file DELETE via history entry traversal (engine API level).**
`history_manager.py:245-268`: `../..` output_path + `delete(delete_audio=True)` deletes outside app_root (probe: victim file removed). Mitigation: current UI passes `delete_audio=False` (history_view.py:375); reachable via `engine.delete_history_entry`. Remediation: containment check before unlink.

**SS-M05 — Same-second generation collisions silently overwrite audio + history.**
`generation_manager.py:98` (second-resolution timestamp is BOTH filename and history id). Probe: two adds same second → 1 file survives. Impact: two quick generations lose the first output. Remediation: uuid/millis suffix.

**SS-M06 — VoiceManager._load_profile TypeErrors crash voice listing.**
`voice_manager.py:245-254`: top-level LIST/STRING, tags:int, UTF-16 → TypeError/UnicodeDecodeError escaping narrow except → `list_voices()` raises at 7 unprotected call sites; sidebar silently blanks (caught in _refresh_sidebar). Remediation: broad except per profile.

**SS-M07 — `import_project_dir` crashes on malformed project.json.**
`project_manager.py:330`: `Project.from_dict` outside try → `"scenes":"hello"` → AttributeError (contrast: get_project/list_projects are graceful). Remediation: move inside try.

**SS-M08 — Exporter crashes on hostile names through the real dialog.**
`project_exporter.py:194-200/221/245`: null-byte project name → uncaught ValueError; 300-char scene name → uncaught OSError(ENAMETOOLONG) — both propagate out of `ExportProjectDialog._on_export` (export_project_dialog.py:178-196, no try). Remediation: length/null-byte sanitization + dialog-level catch.

**SS-M09 — Scene deletion is not persisted (resurrects after restart).**
`main_window.py:3150-3185`: in-memory removal only; closeEvent saves layout only. Probe: delete scene → close → reload → scene back. Project deletion IS durable. Remediation: save_project after delete.

**SS-M10 — CWD-dependent file checks in HistoryViewDialog.**
`history_view.py:260/289/340` use relative `os.path.isfile` vs playback resolving against app_root → false "File missing" + Play blocked when launched from another directory (launch.bat / `python SpeechStudio.py` elsewhere). Remediation: resolve once against app_root.

**SS-M11 — "Font Status…" unreachable (hidden native menu only).**
Exists only in the hidden native Help menubar; TopNavigation Help menu omits it (probe enumeration). Handler connected. Remediation: add to TopNav Help.

**SS-M12 — Top-nav Export button mislabeled.**
Tooltip "Export Audio" but triggers voice-profile export (VoiceLibraryDialog) — `top_navigation.py:248` + `main_window.py:399/3438`. Remediation: relabel or re-target.

**SS-M13 — Token Guide colors frozen at import (theme-blind).**
`token_guide.py:423-437`: GUIDE_HTML formatted once at module import with current Palette; theme switches never refresh (probe: light accent persisted after dark switch). Remediation: build HTML at dialog open.

**SS-M14 — `QTimer.singleShot(0, …)` from worker threads never fires (3 handlers, masked).**
`main_window.py:1991/2000/2003` for MODEL_LOADED/MODEL_UNLOADED/ENGINE_STATUS_CHANGED (emitted from bg-executor: model_manager.py:140/209). Probe: callback never ran despite event loop. Masked by a redundant `_on_model_load_done` path — dead code path hiding the defect. Remediation: marshal via signals.

**SS-M15 — Engine layer imports PySide6 (Qt-free rule breach, invisible to verify_architecture).**
`engine/batch_manager.py:592-594, 680-681` lazy `QTimer.singleShot` for retry/inter-job delays (+ `version.py:123` guarded probe). Works only under the undocumented invariant that `_process_next` runs on the Qt thread. Remediation: inject a scheduling abstraction.

**SS-M16 — Character.voice_profile_id never used for multi-speaker pre-selection (integration gap).**
§5.2: speaker combos preselect saved-global map or control-panel default; a same-named Character's VP is ignored; `character_id` on BatchJobs is uniform (`_selected_character_id`), not per-part. Impact: "character casting" advertised by the data model doesn't exist; manual remapping every time. Remediation: seed speaker map from matching Character VPs; per-part character resolution.

**SS-M17 — speaker_voice_map is a GLOBAL setting (cross-project leak).**
`main_window.py:2544` persists under `application.speaker_voice_map`; pre-selected and applied in every project's Generate Long. Remediation: scope per project (project.json) or namespace by project.

**SS-M18 — GenerationParameters.from_dict defaults diverge from dataclass defaults.**
`models.py:77-89` vs 47-60: temperature 1.0 vs 1.3, top_k 50 vs 300, max_new_tokens 2048 vs 4096, append_silence 1.0 vs 0.5. Old scenes/history/presets with missing keys silently restore different values (Engine Spec §11 reproducibility violated). Remediation: single default source.

**SS-M19 — THR-3/THR-4: undocumented threading invariants.**
`_on_generation_started` mutates UI directly (safe only because GENERATION_STARTED fires on submitter thread — workers.py:77); `BatchManager.marshal_to_ui` defaults to synchronous `fn()` (safe only because MainWindow injects `_UiMarshaler`; `tools/verify_integration.py` uses the dangerous default). Remediation: document + enforce.

**SS-M20 — verify_transport_gl.py stale: 180/183, exit 1 (asserts pre-rewrite architecture); verify_architecture.py blind to lazy imports & attribute-level breaches.**
"Green" tooling overstates health. Remediation: update or retire stale tool; extend scanner to nested imports.

### 6.4 Architecture findings (rated)

| Finding | Severity | Evidence |
|---|---|---|
| **Dead code: 5,394 lines (13.0% of production LOC) in ui/**: `sidebar.py` (554 ln, DEAD — name-collides with live `ProjectSceneSidebar`'s `Sidebar` class), `modern_control_panel.py` (1,971 ln, DEAD, contains outdated Strength/Stability/Similarity sliders), `components.py` (1,617 ln, transitively dead), `prompt_editor.py`, `ambient_background.py`, `ambient_renderer.py`, `ambient_rhi.py` | HIGH (maintenance/risk) | import graph (ast) + runtime `sys.modules` probe: none loaded |
| `transport_gl.py` 1,576 ln DEPRECATED (pre-rewrite GPU transport) → total ~6,970 dead+deprecated lines (~16.8%) | HIGH | waveform_player docstring; sole importer = stale verify tool |
| `control_panel.py` 30% dead weight: `ControlPanel` class (339 ln) imported-but-never-instantiated; only its 7 section classes are live; `main_window.py:41` masks the swap via `RightPanel as ControlPanel` masquerade | MEDIUM | import graph |
| God object: MainWindow 4,495 lines / 141 methods (97 `_on_*`) / ~18 responsibility clusters / 21 engine-private attribute breaches (`_voices`, `_audio`, `_settings`, `_history`) | MEDIUM (structural) | AST stats |
| `engine/prompt_state.py:41-308` LEGACY (Read Tokens workflow, always `token_state=None`) | LOW | call-site scan |
| 13 dead `settings_dir` + `SettingsManager` import fossils in main_window.py (construct-per-call refactor remnants) | LOW | AST |
| Bonus data bug: `engine.py:299` stores the **thread name** as failure-result `timestamp` (flows into recents IDs / AudioAsset.generated_at) | LOW-MEDIUM | source + flow trace |

### 6.5 LOW (compacted — full detail in worklog)

| ID | Finding | Location |
|---|---|---|
| SS-L01 | Fresh-install sidebar shows wrong-context placeholder "No scenes yet" under RECENT PROJECTS; ALL PROJECTS empty despite active in-memory project (no `_update_recents` at startup) | project_scene_sidebar.py:581; main_window.py:1270+ |
| SS-L02 | README says "Themes (4)" — registry has 5; same doc advertises dead Esc/F11 | README_START_HERE.txt:101 |
| SS-L03 | Stale feature-registry entry F-703 "Developer mode" (removed) | docs/governance/feature_registry.yaml:114 |
| SS-L04 | Theme gallery mock documents 4 themes (modern-dark missing) | docs/theme_gallery_mock.html |
| SS-L05 | Developer-Mode leftovers: docstrings (top_navigation.py:24/305, menu_bar.py:8), inert `developer_mode` key (settings_manager.py:41, settings_dialog.py:428), empty Developer tab (settings_dialog.py:276-294), shipped settings.json, test assertions | various |
| SS-L06 | Windows reserved names (CON/NUL/AUX/COM1) pass `_safe_dirname` → export dir creation fails on Windows target | project_exporter.py:91-103 |
| SS-L07 | Duplicate path sanitizer (`_safe_dirname` vs `_safe_name`) — drift risk | project_exporter.py vs export_project_dialog.py:208 |
| SS-L08 | Dual scene source-of-truth: project.json embeds scenes AND `save_scene` writes `scenes/<id>.json`; nothing syncs them (dead code in app) | project_manager.py |
| SS-L09 | No orphan cleanup: delete_project leaves outputs/*.wav + orphan history entries (dead "Open Scene" links) | engine/project_manager.py |
| SS-L10 | Token Guide 100% Hungarian vs English UI (possibly intentional) | token_guide.py |
| SS-L11 | Three different panel-width fallback defaults (280/400, 280/360, 400/400) + stale "not saved" comment vs code that saves | settings_manager.py:64; settings_dialog.py:370; main_window.py:266/4323 |
| SS-L12 | Raw-mode Generate Long not byte-identical (whitespace collapse; `$SPEAKER:` lines stripped per-line) — no token injection though | narration_splitter.py:392-423/221-371 |
| SS-L13 | Recents: scene entries with empty project_id dedup by entity_id only (edge) | recents_manager.py:132-141 |
| SS-L14 | SettingsManager.get crashes if section value is non-dict (`{"window":"big"}`) | settings_manager.py:141-144 |
| SS-L15 | Misleading comment: higgs_tokens.py:134 claims "excluding Normal" while option lists include it (all emission paths correctly guard — latent only) | higgs_tokens.py:118-137 |
| SS-L16 | Stale docstring: prompt_state.py:14-16 still describes removed Read Tokens gate as active model | prompt_state.py |
| SS-L17 | `_show_menu()` unreachable; font_loader docstring references nonexistent "Developer menu" | top_navigation.py:377-401; font_loader.py:21/252 |
| SS-L18 | ZIP provenance: delivered artifact hash ≠ handoff-attested hash (§3 item 4) — release-process finding | handoff §12 vs actual file |

---

## 7. Audit Matrix — Feature Areas

Legend: PASS = runtime-verified working · PARTIAL = works with listed defects · FAIL = broken · M-ONLY = manual/real-display verification impossible offscreen (not counted against the code)

| Area | Verdict | Notes / defects |
|---|---|---|
| Project CRUD + persistence | **PASS** | Round-trip perfect (3 scenes, 2 chars, per-scene VP incl. None, blocks, params, Hungarian names); rename consistent; double-save stable |
| Scene CRUD | **PARTIAL** | Delete not persisted (SS-M09); switch-state bugs SS-H01/H03/H15 |
| Character management | **PARTIAL** | Import-with-metadata always fails (SS-H11); VP integration gap (SS-M16) |
| Voice profiles | **PARTIAL** | Creation/reference import OK + safe slugs; metadata broken (SS-H11); listing crashable (SS-M06) |
| Friendly panel | **PARTIAL** | Allow SFX no-op (SS-H06) + silent reset (SS-M01) |
| Advanced panel | **PASS** | 3-way prompt equality in managed mode (runtime) |
| Raw Mode | **PARTIAL** | Single Generate pure (runtime); center preview lies (SS-H05); GL whitespace (SS-L12) |
| Narration Blocks | **PARTIAL** | Toolbar removal complete, no function lost (all 8 ops reachable); block-override clobbers global (SS-H02) |
| Preview | **PARTIAL** | Managed PASS; Raw FAIL (SS-H05) |
| Generate (single) | **PASS** | Full parameter propagation (10/10 fields), seed handling at panel level OK; invalid values caveat (SS-H07) |
| Generate Long | **PARTIAL** | Chain Part→BatchJob→request holds; SFX re-injection (SS-M02); uniform character_id (SS-M16) |
| Multi-speaker | **PARTIAL** | Map resolution works; no Character-based pre-selection; global map leak (SS-M17) |
| History | **PARTIAL** | Missing audio graceful (runtime); poison-file fragility (SS-H10); collisions (SS-M05); CWD paths (SS-M10) |
| Recents | **PASS** | MRU/cap-5/dedup/same-name/same-id/restart all runtime-verified |
| Context lists / sidebar | **PASS** | Exactly PROJECTS/SCENES/HISTORY/CHARACTERS, no Library, no QTabWidget; active states from authoritative state; startup placeholder (SS-L01) |
| Themes | **PASS** | 5 themes incl. modern-dark; single registry consumed by View menu AND Settings; all apply live; Token Guide freeze (SS-M13) |
| Settings dialog | **PARTIAL** | Per-tab persistence OK in isolation; split-brain (SS-H04); opens twice (SS-H14) |
| Help / Token Guide | **PASS** | No stale user-visible terms (Read Tokens/Prompt Out Of Sync/Strength/Stability/Similarity); `*_normal` correctly documented as non-tokens; LOW doc drift items |
| Save / Open / restart | **PASS** | See project persistence; sidebar widths theme-adjacent LOWs |
| Import / Export | **PARTIAL** | Normal flows + round-trip PASS; hostile names crash dialog (SS-M08); traversal exfiltration (SS-H08); import crash (SS-M07) |
| Top menus | **PARTIAL** | All 33 actions connected; Esc/F11 dead (SS-H12/H13); Font Status unreachable (SS-M11); real-display popups M-ONLY |
| Fullscreen | **PARTIAL** | Geometry/flags verified; **F11 key dead**; flicker M-ONLY |
| Developer Mode removal | **PASS** | UI-level removal confirmed (inert leftovers = LOW) |
| Security | **FAIL** | SS-H08 (+SS-M04/M06/M07/M08) |
| Concurrency | **FAIL** | SS-C01 (+SS-M14/M19) |
| Architecture | **PARTIAL** | Clean layering (no engine→ui top-level, no cycles) BUT 13-17% dead code, settings split-brain, Qt-in-engine breach |
| Test suite | **PARTIAL** | 382 pass as claimed; coverage class systematically misses integration/thread/negative paths (§4) |

---

## 8. UI Reference Cross-Check (against `speechstudio_ui_reference.png`)

Runtime sidebar/mode-walk of the real MainWindow matches the reference for: nav items, RECENT/ALL sections, Friendly/Advanced + VOICE/GENERATION subtabs, block properties panel (Emotion/Style/Speed/Pitch/Delivery override dropdowns with "Inherited:" labels, Label+Lock, Split/Merge/Delete), TOKENS/SOURCES metadata, transport + status bar semantics.

**Divergence requiring clarification (cannot be resolved offscreen)**: the reference shows center-editor granularity toggles **"Paragraph / Sentence / Word / Phoneme"** — no such toggles exist anywhere in `ui/` (rg-verified) nor in `spec/`; the implementation has "Plain Text / Narration Blocks / Preview" mode radios + Raw Mode + Re-detect + Generate Long. Either the reference depicts an unbuilt design or a removed feature. → **MANUAL VERIFICATION REQUIRED / design clarification**.

---

## 9. What Was Verified Working (positive assurance)

- **Prompt contract (managed mode)**: Center preview == Advanced preview == request text — exact string equality across plain text, single-block, and multi-block full-override scenarios; token order stable (emotion→style→speed→pitch→delivery); no duplicated/lost/`*_normal` tokens; C1 None-handling confirmed.
- **Single canonical compiler**: `compile_for_generate` == `compile_continuous` == `compile_for_batch_part` for identical state; the dialog-edit rebuild preserves block overrides; `BatchJob.prompt == part.prompt == to_request().text`.
- **Raw purity** for the generation path itself (single + long, with and without speakers): no token injection even with emotion selected.
- **Parameter propagation**: all 10 GenerationParameters fields panel→request; seed empty/invalid → None.
- **Persistence round-trip**: perfect field-level fidelity (probe_b4), rename propagation, double-save stability, id-keyed project dirs.
- **Name-based directory escape BLOCKED** for project/scene/character names in export (5 hostile names contained); voice import destinations uuid-slug safe; no zip surface (zip-slip N/A).
- **RecentsManager**: full contract (MRU, cap 5, dedup, same-name different-id, restart persistence, stale removal).
- **History missing-audio UX**: graceful status + warnings, no crash/hang.
- **Themes**: single registry, dual-consumer consistency, all 5 apply live.
- **Blocks toolbar removal**: complete with zero function loss (all operations relocated: properties panel + context menu + mode bar + Ctrl+F).
- **Menus**: all 33 actions connected; no unconnected/duplicate labels.
- **Engine/UI layering**: zero top-level engine→ui imports; no circular imports (SCC-proven).

---

## 10. MANUAL VERIFICATION REQUIRED (cannot be proven offscreen)

1. Real-display top-menu popup click behavior (structure + connections verified offscreen).
2. Fullscreen enter/exit **flicker** (anti-flicker code verified; the F11 *key* defect is platform-independent — SS-H12).
3. Advanced/Friendly visual parity vs reference PNG.
4. Paragraph/Sentence/Word/Phoneme granularity toggles present in reference but absent in build (§8) — needs product clarification.
5. Real audio device playback path (probes substituted file-level checks).

---

## 11. Recommended Remediation Order

**Phase 1 — stop the bleeding (pre-release blockers)**:
1. SS-C01 marshal HISTORY_UPDATED (1-line signal route).
2. SS-H14 delete duplicate settings connect (1 line).
3. SS-H12/H13 single Esc/F11 registration.
4. SS-H01/H03 unconditional resets for voice + parameters on scene load (2 lines) + SS-H15 narrow the silent excepts.
5. SS-H04 consolidate SettingsManager (single instance + atomic write).
6. SS-H08 path containment on import/export (+SS-M04).

**Phase 2 — correctness**:
7. SS-H02 block-override vs global separation (emit the documented `block_override_changed`).
8. SS-H05 raw-mode preview branch.
9. SS-H06/M01/M02 allow_sfx end-to-end (store + sync + rebuild + job_params).
10. SS-H11 implement/remove phantom Engine APIs; transactional import.
11. SS-H07 invalid-value validation + surfaced warnings.
12. SS-H09/H10/M06/M07 broad-but-per-entry robustness in all JSON loaders.

**Phase 3 — hardening & hygiene**:
13. SS-M05 unique ids; SS-M09 persist scene delete; SS-M10 path resolution; SS-M14 dead QTimer paths; SS-M16/M17 speaker-map character seeding + project scoping; SS-M18 default unification; SS-M11/M12/M13 UX fixes; dead-code purge (~7 files, 5,394 lines) + retire/update stale verify tools; LOW docs batch.

**Phase 4 — tests**: thread-affinity, cross-instance settings coherence, keyClick shortcuts, hostile-input matrix, end-to-end dialog flows (voice import, export), raw-mode preview equality, None-state round-trips.

---

## 12. Final Release Assessment

### Verdict: **NOT RELEASE READY**

**Rationale**:

1. **One CRITICAL, runtime-proven** defect on the golden path (every successful generation triggers cross-thread UI mutation — SS-C01).
2. **Fifteen HIGH defects**, most runtime-proven, including: silent data corruption in the core scene model (SS-H01/H02/H03/H15), settings self-reversion (SS-H04), a security exfiltration vector (SS-H08), startup brick on one corrupt file (SS-H09), a core workflow that always reports failure (SS-H11), and two advertised keyboard shortcuts dead (SS-H12/H13) plus a double-opening modal on the primary settings path (SS-H14).
3. **The verification story is weaker than it appears**: 382 green tests (true) are dominated by shallow structural assertions (2.14 s total); the handoff's own "false positive" C3 call is refuted at the integration level (SS-H06); one shipped verify tool fails; the delivered ZIP's hash does not match the attested hash (SS-L18).
4. **What works is genuinely solid** (persistence, prompt contract in managed mode, export round-trip for benign data, recents, theme system, layering) — with Phase 1 fixes (≈1 week of focused work) this could plausibly reach **RELEASE CANDIDATE**; the full Phase 1–3 set is a reasonable bar for a subsequent re-audit.

**Recommended path**: fix Phase 1 → re-run this audit's probe suite (scripts preserved under `/tmp/audit_probes/`, ~35 scripts, all runnable read-only against a new build) → re-assess.

---

## Appendix A — Environment & Artifact Disclosure

- Audit executed on Python 3.12.14 / PySide6 6.11.2 / pytest 9.0.2, Linux, offscreen Qt (`QT_QPA_PLATFORM=offscreen`, libEGL shim).
- Read-only compliance: no audited source file was modified. Runtime side-effect (by the app's own design — module-relative APP_ROOT): `settings/settings.json` rewritten (theme left at shipped "dark"; `first_run_completed=true`), `logs/` appended, `__pycache__` regenerated.
- Probe inventory: `probe_voice*.py` (voice precedence S1–S5), `probe_a_*.py` (prompt contract), `probe_b1…b8*` (security/persistence/recents/export dialog), `probe_c*` (feature/menus/themes/shortcuts/settings split-brain), `probe_d_*` + `import_graph.py` (architecture), `mw_stats.py` (god-object stats).
- Shared audit work log: `/home/z/my-project/worklog.md` (per-task entries from all audit agents, including full defect detail beyond this report's compacted LOW entries).
