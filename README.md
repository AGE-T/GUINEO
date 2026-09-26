# GUINEO

<p align="center">
  <img src="assets/brand/guineo_logo.png" alt="GUINEO logo" width="180"/>
</p>

**GUINEO** is a portable desktop application for local speech synthesis, built
on the Higgs TTS 3 (Higgs Audio V3) model from Boson AI. All processing
happens on your machine — no cloud, no telemetry.

[🇭🇺 Magyar dokumentáció](README_HU.md) ·
[GitHub repository](https://github.com/AGE-T/GUINEO)

---

<a id="english"></a>

# EN | British English

<a id="whats-new"></a>
## What's new in P3.44.5

P3.44.5 is a **stabilisation and integrity release**: two independently
reproduced defects are fixed at their exact ownership points, and both are
locked in by a new permanent regression suite. No other behaviour changed.

- **Batch window lifecycle (audit Issues B and D).** A Batch Generation
  window that was closed and later re-opened (same scene, same mode) used to
  stop receiving updates from the running batch: row states froze, Start
  stayed disabled and Pause/Stop stayed enabled after completion, and the
  window recovered only if you pressed Pause or Stop. Every presentation of
  the window now re-establishes the manager listener **idempotently**
  (exactly one listener, never duplicates) and re-synchronises the whole
  table from the manager's actual state — so a re-opened window always shows
  the truth, including a batch that finished while the window was closed.
  The BatchManager state machine itself was correct throughout and is
  unchanged.
- **Block-aware audio provenance matching.** Generated audio is now matched
  to a scene's expected slots by **block identity** (`block_id`), not merely
  by global part position. Previously, after a structural edit, an old audio
  file whose position happened to line up with the new structure was
  silently accepted as coverage — and Combine Scene could assemble old audio
  under the new text. Old audio can no longer masquerade as coverage for a
  structurally different generation plan.
- **29 new regression tests**
  (`tests/test_p3_44_5_stabilisation_integrity.py`) capture both defects:
  17 of them fail on the pre-P3.44.5 code and all 29 pass after the fix.

Implementation record: `docs/design/P3_44_5_STABILISATION_NOTES.md`;
evidence base: `docs/audits/P3_44_5_RESEARCH_AUDIT.md`.

## Contents

1. [What's new in P3.44.5](#whats-new)
2. [What is GUINEO?](#what-is-guineo)
3. [Installation](#installation)
4. [Updating](#updating)
5. [Hardware requirements](#hardware-requirements)
6. [Getting started — a quick tour](#getting-started)
7. [The workspace at a glance](#the-workspace-at-a-glance)
8. [Projects](#projects)
9. [Scenes](#scenes)
10. [Characters](#characters)
11. [Voice Profiles](#voice-profiles)
12. [Narration Blocks and editor modes](#narration-blocks)
13. [Control panel — Friendly and Advanced mode](#control-panel)
14. [Generating speech](#generating-speech)
15. [Long Generation](#long-generation)
16. [Batch Generation — two workflows, one system](#batch-generation-en)
17. [Versions and the Scene audio composition](#audio-composition)
18. [STALE — the outdated output](#stale-en)
19. [Project Assembly](#project-assembly)
20. [Exporting](#exporting)
21. [History](#history)
22. [Recents and Context Lists](#recents-en)
23. [Themes, fullscreen, volume](#themes)
24. [The model and supported languages](#the-model-en)
25. [Generation anomaly detection](#anomaly-detection)
26. [Error handling and path safety](#error-handling)
27. [Persistence — what survives a restart?](#persistence)
28. [Keyboard shortcuts](#keyboard-shortcuts)
29. [Menus](#menus)
30. [Settings](#settings)
31. [Version and build information](#version-info)
32. [Release history](#release-history)
33. [Higgs TTS 3 Licensing](#higgs-licensing)
34. [Troubleshooting](#troubleshooting)
35. [For developers — repository and tests](#for-developers)

<a id="what-is-guineo"></a>
## What is GUINEO?

GUINEO is a **portable, local-first desktop application for speech
synthesis** on Windows. It is built on the **Higgs TTS 3** (also known as
Higgs Audio V3) open-weight 4-billion-parameter audio language model from
Boson AI, running on your own NVIDIA GPU. The application:

- organises your text into **Projects, Scenes and Narration Blocks**,
- manages **Characters**, each with its own voice and colour identity,
- performs **zero-shot voice cloning** from short reference clips
  (Voice Profiles),
- automatically splits and **batch-generates long texts**,
- keeps every scene's audio in a **versioned, fully traceable provenance
  system**, and
- **assembles finished scenes** into a single combined audio file.

All processing is local: no cloud, no telemetry — generated files stay on
your disk. The application downloads the model weights (~9 GB) once on
first run and works offline afterwards.

<a id="installation"></a>
## Installation

**Requirements:** Windows 10/11 (64-bit), Python 3.11 (exactly this
version), an NVIDIA GPU with CUDA support, about 12 GB of free disk space,
and an internet connection for the first run.

1. Download the latest release ZIP (e.g. `releases/GUINEO_P3.44.5_current.zip`
   from the [GitHub repository](https://github.com/AGE-T/GUINEO)) and extract
   the GUINEO folder to any location (e.g. `C:\GUINEO`).
2. Install **Python 3.11** from
   [python.org](https://www.python.org/downloads/release/python-3119/) —
   tick “Add Python to PATH” during installation.
3. Double-click **`launch.bat`**.

The first run performs the entire setup automatically:

- it creates a private virtual environment (`venv\`),
- installs PyTorch, Transformers, PySide6 and the remaining dependencies,
- downloads the Higgs TTS 3 model (~9 GB, one-time), and
- starts the GUINEO window.

**The first download takes time** (10–60 minutes depending on your
connection) but is **resumable** — if it is interrupted, simply run
`launch.bat` again. Every subsequent start is fast and needs no internet.

If `launch.bat` reports that Python was not found, install version 3.11
from the link above and make sure “Add Python to PATH” is ticked.

<a id="updating"></a>
## Updating

To update to a new version **without losing your data**:

1. Download the new release ZIP from the repository's `releases/` folder on
   [GitHub](https://github.com/AGE-T/GUINEO) and rename it to
   **`SpeechStudio_clean.zip`**.
2. Place it in the GUINEO folder (next to `SpeechStudio.py`).
3. Close GUINEO if it is running.
4. Run **`update.bat`**.

The script backs up your current source code, then replaces **only the
source files**. All personal data is preserved: `settings\`, `voices\`,
`outputs\`, `presets\`, `models\`.

**Never delete the whole folder and re-extract** — always use `update.bat`.

<a id="hardware-requirements"></a>
## Hardware requirements

| Level | Requirement |
|---|---|
| **Minimum** | Windows 10/11 64-bit, Python 3.11, a CUDA-capable NVIDIA GPU, ~12 GB free disk space. A CPU-only fallback exists, but generation is impractically slow there. |
| **Recommended** | A modern NVIDIA RTX GPU with ~10 GB of free VRAM (the model uses roughly 10 GB of VRAM at bfloat16 precision). Development and testing were done on a GeForce RTX 5070. |
| **Large / long generation** | Long texts are split into many short parts, so the VRAM requirement **does not grow** with text length — the practical limits are time and disk space. The status bar and History show the measured real-time factor (RTF). |

<a id="getting-started"></a>
## Getting started

A complete round trip in five minutes:

1. **Launch** `launch.bat`. On first start the application offers to load
   the model (about 10–30 seconds, ~10 GB of VRAM). If you skip it, it
   loads automatically when you press **Generate**.
2. **Type something** in the editor (a short Hungarian sample text is
   pre-loaded on the very first run).
3. **Pick a voice**: right panel → **VOICE** tab → *Change Voice* → choose
   a voice, or import a reference clip (see [Voice Profiles](#voice-profiles)).
4. Press **Ctrl+Enter** (or click the orange **GENERATE** button).
5. The finished audio appears in the **waveform player** at the bottom —
   click or drag on the waveform to seek; adjust loudness with the volume
   slider.

The generated file lands in `outputs\` (24,000 Hz mono WAV). Every
generation is recorded in **History** (Ctrl+H), from which the full
settings can be restored later.

<a id="the-workspace-at-a-glance"></a>
## The workspace at a glance

- **Left sidebar**: Projects / Scenes / History / Characters navigation,
  a RECENTS panel, context lists (ALL PROJECTS, ALL SCENES, ALL
  CHARACTERS, ALL HISTORY), and a pinned **Assemble Audio** button at the
  bottom.
- **Centre**: the text editor (Plain Text / Narration Blocks / Preview
  modes) with the waveform transport underneath.
- **Right panel**: **FRIENDLY** and **ADVANCED** tabs — voice, emotion,
  style, prosody, sound effects and generation parameters.
- **Top**: the official **GUINEO logo** (white wordmark) in the left
  corner, navigation menus (File, Edit, View, Project, Help),
  undo/redo, save, export and the GENERATE button.
- **Bottom**: the status bar — model state, CUDA/GPU, generation state,
  active voice, measured generation time (RTF) and the version.

<a id="projects"></a>
## Projects

A **Project** is the top-level container: scenes, characters, blocks,
audio and history travel together. `Ctrl+N` creates a new one, `Ctrl+O`
opens, `Ctrl+S` saves. Projects live in the `projects\` folder
(`project.json`) and are saved automatically on scene switches and exit.
The Projects dropdown renames and deletes projects; the sidebar switches
between them quickly. The **Recents** list keeps the last five.

<a id="scenes"></a>
## Scenes

A **Scene** is a self-contained text and audio unit inside a project —
for example a chapter, a dialogue, or one scene of a screenplay.

- **Create**: File → New Scene (`Ctrl+Shift+N`), or the Scene dropdown /
  sidebar buttons.
- **Rename**: the pencil icon next to every scene row, or the Scene menu →
  Rename Scene.
- **Order**: Scene menu → **Move Up / Move Down** (the order persists).
- **Editing**: click a scene — its text, blocks, characters and voice
  settings are all stored per scene.
- **Status** (derived from coverage, never hand-set):
  - **NOT GENERATED** — no audio yet,
  - **GENERATING** — generation is running,
  - **PARTIAL** — some blocks have audio,
  - **COMPLETE** — every expected block has audio,
  - **ERROR** — the last run failed (previously generated audio stays
    intact).

A scene's audio is generated in the **Batch Generation — Scene** window
(see below), where you can select versions, regenerate parts, and
**Combine Scene**. The scene's status and resolved output update
automatically.

**Saving / loading to a file**: File → Save Dialogue Scene / Load Dialogue
Scene (`*.scene.json`) — saves a dialogue scene to a standalone file and
loads it back into the Batch Queue window.

<a id="characters"></a>
## Characters

A **Character** is a voice personality within a project:

- **Name** — appears in the sidebar, next to blocks, in batch rows and in
  History.
- **Voice Profile** — the voice assigned to the character (may be empty).
  Assign it in Character Management or the sidebar's CHARACTERS context.
- **Colour identity** — GUINEO automatically assigns every character a
  **stable colour** (a 12-colour palette derived from the character's id),
  identical everywhere: sidebar dot, block markers, batch rows.
- **Block-level assignment** — an individual narration block can carry its
  own character in the block properties panel. Blocks without a character
  inherit the scene's global voice.

**Voice resolution** during generation:

1. A plain **Generate** uses the voice selected in the scene/control panel.
2. For **per-block generation (Generate Long)** the chain is:
   ① the block's **character's** voice → ② the speaker mapping
   (multi-speaker text) → ③ the default voice from the panel.

Lines of the form `$SPEAKER:` (an ALL-CAPS name) are recognised, and each
speaker is generated with its own reference audio.

<a id="voice-profiles"></a>
## Voice Profiles

A voice profile is a **zero-shot voice-cloning reference**: a short
(5–15 seconds, WAV) sample plus an optional transcript.

- **Voice Profiles**: `Ctrl+L` or Project → Voice Profiles — manage
  (create/import/edit/export/delete) voice profiles.
- **Import**: File → Import Voice — choose a WAV file; the profile is
  stored in `voices\` (reference + metadata).
- **Export**: File → Export Voice — move a profile to another machine.
- References longer than 30 seconds trigger a warning (5–15 s is optimal).
- Assign profiles to characters, or directly to the scene.

<a id="narration-blocks"></a>
## Narration Blocks

The central editor has three modes (switched with the radio buttons at
the top):

- **Plain Text** — a simple text area; long generation splits it
  automatically.
- **Narration Blocks** — the text can be broken into **blocks**
  (paragraph- and sentence-level heuristics). Each block can have its own
  character, overridden emotion/style, lock flag; blocks can be merged,
  split, deleted; **Re-detect** rebuilds them (locked and manually edited
  blocks are preserved). After generation, blocks carry done/generating
  badges and per-block versions. Twelve built-in templates are available
  (YouTube Intro, Product Description, and so on).
- **Preview** — a read-only, syntax-highlighted view of the final prompt
  with tokens.

With **Raw Mode** you own the prompt yourself: tokens are sent literally
(with a confirmation dialog).

<a id="control-panel"></a>
## Control panel

The right panel works in two tabs:

**FRIENDLY** — simplified control on two sub-tabs:

- **VOICE**: the selected voice, a change button; **EMOTION & STYLE**
  chips (8 primary emotions and more: Happy, Sad, Anger, Calm, Fear,
  Whisper, Joy…); **PROSODY**: Pace, Pitch (low/normal/high) and
  Expression (subtle/natural/animated) segmented controls.
- **GENERATION**: the **AI Freedom** control with five levels
  (Strict → Wild: pre-calibrated temperature/top_p/top_k combinations),
  a seed field, the **ALLOW SFX** toggle, and sound-effect/pause insert
  buttons.

**ADVANCED** — full expert control: the complete 21-emotion grid, styles
(Singing / Whispering / Shouting), prosody (five speed levels, pitch,
delivery), inline pauses, sound effects, generation parameters
(temperature, top_p, top_k, max_new_tokens, seed, appended silence,
normalisation, auto-play), reference audio and a live **prompt preview**.

**The token system** (the Token Guide in the Help menu provides a full
description, in Hungarian): the controls insert `<|category:tag|>` tokens
into the prompt (for example `<|emotion:fear|>`, `<|prosody:speed_slow|>`,
`<|sfx:laughter|>Haha`). Sentence-start tokens apply to the whole
sentence; inline tokens apply at the exact position; SFX tokens carry an
onomatopoeia.

<a id="generating-speech"></a>
## Generating speech

- **Ctrl+Enter / the GENERATE button**: generates the current text in one
  call (the model loads automatically if needed).
- **Esc**: stop a running generation (finished files are kept), leave
  fullscreen, or close the search bar.
- **Replay** (Edit menu): replay the most recent audio.
- Output: **24,000 Hz mono, 32-bit float WAV** in `outputs\`, with an
  automatic timestamped name (suffixed on collision).
- The status bar shows the measured time and the real-time factor (RTF).

<a id="long-generation"></a>
## Long Generation

**Generate Long** (`Ctrl+Shift+Return`, or the editor button) is designed
for long texts:

1. The text is split into **safe parts** (a target of roughly 400
   characters at sentence boundaries), `$SPEAKER:` lines are recognised,
   and a duration estimate is shown.
2. In the **Long Narration Generator** dialog you can review the parts,
   map speakers to voices, and enable **Review before generating**
   (off by default; when enabled, the batch window opens in review mode
   and only starts once you press **GENERATE CHECKED**).
3. The parts run in sequence in the **Batch Generation — Scene** window
   (with 0.5 s of trailing silence). Closing the batch window stops the
   running batch, so do not close it accidentally while generating.
4. When the run finishes, the scene's status updates automatically
   (COMPLETE / PARTIAL / ERROR). Use **Combine Scene** to produce a
   single listenable output from the parts (see
   [Audio composition](#audio-composition)).

<a id="batch-generation-en"></a>
## Batch Generation

GUINEO has **one Batch Generation system** — one window, one batch
manager (BatchManager), one generation backend — that exposes **two valid
workflows** depending on where you open it from:

| | **Batch Generation — Scene mode** | **Batch Queue (manual mode)** |
|---|---|---|
| **Entry point** | Generate Long (`Ctrl+Shift+Return`), or File → Load Dialogue Scene | Project → Batch Generation… (or the classic Tools menu) |
| **Purpose** | Covering a scene's blocks, version management, combining | Queuing arbitrary audio jobs, independent of Scenes |
| **Title** | “Batch Generation — <Scene name>” | “Batch Queue” |
| **Table** | 7 columns: number, checkbox, file name, Generated (versions), status, duration, actions | 5 columns: number, file name, status, duration, actions |
| **Header** | Coverage (e.g. “coverage: PARTIAL (3/4)”) and a run id | — |
| **Actions** | All/None selection, **GENERATE CHECKED (N)** (generates exactly the checked rows), per-row Play / Stop playback / **Regen** (new version), **Combine Scene**, **Export Audio…**, a version picker (“N versions ▾”) | **+ Add / Edit / Duplicate / Remove / Up / Down**, **Save Queue… / Load Queue…** (YAML) / Clear, **START BATCH**, per-row Play / Stop playback / Regen, **Merge Completed Parts** |
| **Scene outputs** | SCENE COMBINED OUTPUTS section: resolved output, versions, STALE badge, Use as output / Clear | none (the output is not linked to a Scene) |

**Shared behaviour:**

- **Only one batch window is live at a time.** Opening the same mode again
  simply focuses the existing window; requesting the other mode closes the
  old window cleanly (a running batch keeps running) and opens the new
  mode.
- **Review Before Generating** (in the long narration dialog): jobs stay
  PENDING until you press the generate button.
- **Pause**: the batch stops picking up new jobs; the current job
  finishes. (The current build has no Resume button — use **Stop** to end
  the batch: pending jobs become SKIPPED, and Restart resets them to
  PENDING.)
- **Stop**: waiting jobs become SKIPPED immediately; the running job is
  cancelled at the next safe checkpoint; audio already written to disk is
  preserved.
- **Regen**: in scene mode it creates a **new versioned** file after
  confirmation (the previous version is kept); in manual mode it
  overwrites the file in place.
- **Merge Completed Parts** (manual mode only): merges the completed jobs
  in queue order into one WAV (300 ms of silence between parts by
  default — optionally randomised; a crossfade applies only at 0 ms
  silence). This output is **not linked to a Scene** and does not affect
  Project Assembly.

<a id="audio-composition"></a>
## Audio composition

GUINEO's provenance system records exactly **which block versions** make
up a scene:

1. **Block versions**: every generation produces a new version
   (`v01`, `v02`, `v03`…). If you explicitly select an older one, that
   choice stands — a newer generation alone never overrides it. Without
   an explicit selection the **latest valid version** is used.
2. **Scene composition**: the scene's current composition is a **derived
   mapping** — which version belongs to each block right now. For
   example:

   ```
   B1 → v02    B2 → v01    B3 → v04    B4 → v01
   ```

3. **Combine Scene**: produces an **immutable snapshot** of the
   composition — a Combined output, also versioned (`Combined v01`,
   `v02`…) — recording exactly which block version each slot used. Older
   combined outputs remain available and selectable.

   ```
   Scene composition:           Combine Scene
   B1 → v02                     ───────────▶   Combined v01
   B2 → v01                                     (immutable snapshot)
   B3 → v04
   B4 → v01
   ```

The SCENE COMBINED OUTPUTS section of the Batch Generation — Scene window
shows the resolved output, the version list (with durations), the STALE
badge, and **Use as output** / clear-explicit-selection. The dialog's
geometry and column configuration (widths, order, visibility) are
remembered and restored after a restart.

<a id="stale-en"></a>
## STALE

The **STALE** badge means that a combined output **no longer reflects the
scene's current composition**. It does **not** mean the file is corrupt,
broken or unusable — it is intact, playable and selectable.

Example:

```
Combined v01 contains:     The scene's CURRENT composition:
  B1 → v02                   B1 → v02
  B2 → v01                   B2 → v01
  B3 → v01                   B3 → v02   ← a new version was generated
```

Because B3 now has a newer version, Combined v01 is **STALE**.

A combined output also becomes STALE when a **new block** is added to the
scene, a block is **removed**, or a source **file is missing** from disk.

**Explicit stale selection**: an outdated output can deliberately be
chosen as the scene's output — GUINEO warns and asks for a “Use Anyway”
confirmation. **Combine Scene** always produces a fresh snapshot and
takes over as the output.

<a id="project-assembly"></a>
## Project Assembly

**Assemble Audio** (bottom of the sidebar, or `Ctrl+Shift+A`) stitches
the finished audio of several scenes into one file:

1. The dialog lists **one row per scene** with the resolved output
   (“Combined v02”, “Latest audio”, “Selected · CUSTOM”) and STALE /
   MISSING badges. Rows can be reordered (Move Up / Down); each row's
   output can be overridden with **Choose Output…**.
2. Settings: output file name, folder (default `outputs\combined`), the
   **gap between scenes** (0–3 s, default 0.5 s), **normalisation to
   −18 LUFS** (on by default), and an optional **MP3 copy** (requires
   FFmpeg — see [Exporting](#exporting)).
3. Output: WAV (+ optionally MP3); the assembly is recorded in History
   and in the project's `combined_outputs` lineage.

**Safety rule**: the system will **never silently use a single part
(block) audio as a whole scene's output**. If no valid complete scene
output exists, the row shows **REVIEW REQUIRED** — with the named
remedies: **Combine Scene Now** or **Choose Output**. If a source file is
missing, the assembly stops with an error naming the scene — nothing is
silently skipped.

<a id="exporting"></a>
## Exporting

**Batch Audio Export** (Batch Generation — Scene window,
**Export Audio…**) — four precisely defined scopes:

| Scope | What it copies |
|---|---|
| **Selected rows (N)** | the outputs of the checked rows |
| **All generated parts** | the outputs of every job that is a scene part |
| **Scene combined outputs** | **all** combined outputs of the scene (every version) |
| **Everything in this batch** | the parts **and** the combined outputs together |

The export is a **copy without transcoding** (WAV, provenance file
names); collisions get an automatic “(2)” suffix; missing files are
listed in the summary dialog.

**Project Export** (File → Export Project…, `Ctrl+Shift+E`) creates a
**folder** (not a ZIP) at the destination you choose:

```
<ProjectName>/
├─ project.json              ← scene index, characters, combined lineage
├─ scenes/<Scene>/
│   ├─ scene.json            ← text, blocks, versions, provenance
│   └─ audio/001.wav …       ← the scene's audio (optional: “Include
│                               Generated Audio”, on by default)
├─ characters/<Character>/character.json
├─ references/voices/<Profile>/   ← reference audio + transcripts
│                                 (optional: “Include Referenced Voice
│                                  Assets”, on by default)
└─ combined_outputs/         ← combined WAVs (+ project-level MP3)
```

All internal paths are **relative**. Model files are **not** part of the
export. Limitation: the current build has **no user-facing command to
reimport** an exported project directory — File → Open Project opens the
native `projects\` directory and the legacy `*.sproj` format.

<a id="history"></a>
## History

**Ctrl+H** or Project → History. Every generation — single, long,
combined, assembled — creates a **reproducible JSON entry** in
`settings\history\`: the full prompt, parameters, voice profile, measured
timings, GPU, warnings, scene/character/block context and the output
path. Clicking an entry selects it (the Recent Audio list refreshes); the
ALL HISTORY header icon opens the detailed view, from which settings can
be restored and audio played or exported. Entries can be deleted
(deletion is containment-checked within the history directory).

<a id="recents-en"></a>
## Recents

- **The RECENTS panel** (left sidebar): the last 5 projects / scenes /
  characters / history entries, depending on the context. A “+” button
  creates new items.
- **Context Lists**: ALL PROJECTS / ALL SCENES / ALL CHARACTERS / ALL
  HISTORY — a quick overview of everything. Every ALL SCENES row has a
  pencil icon for instant renaming.

<a id="themes"></a>
## Themes

- **Five themes**: Modern Dark (the default), Dark, Light, Synthwave and
  Retro Console — switchable from the View → Theme menu, persisted
  immediately.
- **Fullscreen**: **F11** toggles; Esc also leaves fullscreen.
- **Volume**: the transport slider (0–100); the value **persists** across
  restarts.
- **Reset Layout** (View menu): restores the default layout.
- Window geometry and the batch window's size and column configuration
  are remembered as well.

<a id="the-model-en"></a>
## The model

- **Model**: Higgs TTS 3 (Higgs Audio V3) by Boson AI. GUINEO loads the
  community Transformers port
  `multimodalart/higgs-audio-v3-tts-4b-transformers` (the original:
  `bosonai/higgs-tts-3-4b`).
- **Loading**: at startup the application offers to load it (“Load the
  model now?…”); otherwise Generate loads it automatically. While loading,
  a dialog with the **GUINEO animation** runs (phase messages and an
  elapsed timer) and can be cancelled.
- **Precision**: bfloat16 (recommended) / float16 (experimental) /
  float32 — set in Settings; takes effect after the model reloads.
- **Languages**: 102 languages — 85 at production quality (Tier 1,
  including **Hungarian**) and 17 usable (Tier 2). The full list: Help →
  Supported Languages.
- **Local processing**: after the first download everything runs locally;
  there is no telemetry and no cloud.

<a id="anomaly-detection"></a>
## Anomaly detection

During long generation GUINEO **conservatively detects** pathological
outputs (the P3.27B protection):

- a runaway **trailing silence** (tail ≥ 10 s **and** ≥ 50% of the total
  length),
- an output far **longer than expected** (≥ 3× the estimate **and** ≥
  estimate + 20 s),
- a **silent** output (no audible speech in ≥ 5 s).

**Important**: detection is advisory — the file is **never trimmed or
deleted**. Affected rows show a ⚠ marker next to the duration in the
batch window (with a detailed breakdown), History records the anomaly,
and the audio can be **regenerated** at any time (as a new version; the
old one is kept). The model cannot guarantee the absence of excessive
trailing silence — this protection mitigates, but does not eliminate,
the effect.

<a id="error-handling"></a>
## Error handling

Every error surfaces with a **readable message** (dialog plus status
bar), with a suggested action where possible: for example “the model is
not loaded — press Generate”, “not enough VRAM (~10 GB required)”,
“reference audio missing”. Errors are logged to `logs\`
(`application.log`, `engine.log`, `crash.log`, `bootstrap.log`). A failure
never crashes the application, and a failed regeneration never degrades
existing audio.

**Path safety**: output file names are always contained inside
`outputs\`; imported projects are stripped of escaping
(relative/absolute/traversal) paths; history deletion is
containment-checked.

<a id="persistence"></a>
## Persistence

| What | Where | Survives restart? |
|---|---|---|
| Theme, volume, window and batch-window geometry, columns | `settings\settings.json` | ✅ |
| Build counter | `settings\build.json` | ✅ (+1 on every launch) |
| History | `settings\history\*.json` | ✅ |
| Projects, scenes, characters, versions, provenance | `projects\<id>\project.json` | ✅ |
| Voice profiles | `voices\` | ✅ |
| Generated audio | `outputs\` | ✅ |
| Presets | `presets\` | ✅ |
| Model weights (~9 GB) | `models\higgs-audio-v3\` | ✅ (never re-downloaded) |
| Logs | `logs\` | ✅ |

After a restart everything above returns; the model must be loaded into
VRAM again (about 10–30 s). The `update.bat` update path always preserves
these folders.

<a id="keyboard-shortcuts"></a>
## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Enter` | Generate speech |
| `Ctrl+Shift+Enter` | Generate Long Narration |
| `Esc` | Stop generation / leave fullscreen / close the search bar |
| `Space` | Play / pause (when the editor is not focused) |
| `Ctrl+N` / `Ctrl+Shift+N` | New project / new scene |
| `Ctrl+O` / `Ctrl+S` | Open / save project |
| `Ctrl+Shift+E` | Export project |
| `Ctrl+Shift+A` | Assemble scenes |
| `Ctrl+L` | Voice Profiles |
| `Ctrl+H` | History |
| `F11` | Fullscreen |
| `Ctrl+Q` | Exit |

<a id="menus"></a>
## Menus

- **File**: New Project, New Scene, Open Project, Save Project, Export
  Project…, Save / Load Dialogue Scene, Import / Export Voice, Import /
  Export Preset, Exit
  *(Assembly is not a menu item: the pinned **Assemble Audio** button at
  the bottom of the sidebar — or `Ctrl+Shift+A` — opens it; see
  [Project Assembly](#project-assembly).)*
- **Edit**: Generate, Generate Long Narration, Stop, Replay, Clear
- **View**: Theme (five themes), Reset Layout, Fullscreen, Show Prompt
  Blocks
- **Project**: Batch Generation…, Voice Profiles, History, Preset
  Manager…, Open Output Folder
- **Help**: Documentation, Token Guide…, Supported Languages…, Copy Debug
  Information, Font Status…, About, ─, Settings, Benchmark

The right side of the top bar holds undo/redo, save, export and the
GENERATE button.

<a id="settings"></a>
## Settings

Help → Settings:

- **General**: auto-play after generation, output normalisation, output
  format, randomised silence when concatenating.
- **Performance**: model precision (bfloat16 / float16 / float32) —
  affects memory use and speed; takes effect the next time the model
  loads.
- **Application Theme**: theme selection.

<a id="version-info"></a>
## Version info

GUINEO is a development build: the version is `v1.0.0` and the **build
number increments on every launch** (`settings\build.json`), shown in the
status bar and the Help → About dialog. About also displays the full
environment (Python, PyTorch, Transformers, Qt, CUDA, GPU, model,
precision) and offers a **Copy Debug Info** button.

The current development phase is **P3.44.5** (Stabilisation / Integrity
Phase 1) — see [Release history](#release-history).

<a id="release-history"></a>
## Release history

A concise summary of the recent development phases. The complete, dated
history is [`DEVELOPMENT_LOG.txt`](DEVELOPMENT_LOG.txt) (in Hungarian), and
every delivered version ZIP is preserved in the repository's `releases/`
folder on [GitHub](https://github.com/AGE-T/GUINEO).

| Phase | Focus |
|---|---|
| **P3.44.5** (current) | Stabilisation / integrity: batch-window lifecycle fix (Issues B+D), block-aware provenance join, 29-test regression suite |
| P3.44.4 | Batch execution-run semantics; feedback-format regression suite |
| P3.44.3 | Feedback dialog fixes |
| P3.44.2 | SFX pipeline fixes |
| P3.44.1 | Batch state synchronisation fixes |
| P3.44 | Batch playback integrity; history player load; sidebar scale |
| P3.43 | Voice unification (library, picker, import, editor) |
| P3.41 | Block deletion and editor interaction fixes |
| P3.40 | Batch row rendering; DPI and visual audit fixes |
| P3.39 | Token integrity (no phantom token emission) |
| P3.38 | GUINEO wordmark and brand assets |
| P3.37 | GUINEO rebrand; complete bilingual documentation |
| ≤ P3.36 | Earlier phases — see the development log |

<a id="higgs-licensing"></a>
## Higgs TTS 3 Licensing

GUINEO uses **Higgs TTS 3 from Boson AI**. The model is provided under
the **Boson Higgs TTS 3 Research and Non-Commercial License** — this is
**not an open source licence**:

- **Research and non-commercial use** is free of charge.
- The current licence includes a **Creator Use Grant**: digital creators
  may create, publish and **monetise creative content** (podcasts,
  videos, audiobooks, social-media posts and similar) on channels they
  own or control, provided they **credit Boson AI's Higgs Audio** — for
  example, “This audio was created with Boson AI's Higgs Audio —
  https://www.boson.ai/higgs-audio” in the description or in the audio
  itself.
- **Commercial use** — production deployment, hosted API use, embedding
  the model in a product or application, redistribution or reselling the
  model — **requires the appropriate commercial licence**:
  <https://boson.ai> · contact@boson.ai.
- Being able to download the model weights does **not** grant any
  commercial redistribution right.
- The licence also prohibits, among other things: voice cloning or
  impersonation of real people without explicit, verifiable consent;
  deceptive or fraudulent use; and using outputs to train non-Boson
  generative models.

The complete official licence text ships with the project:
[`LICENSE_HIGGS_TTS_3.txt`](LICENSE_HIGGS_TTS_3.txt). Third-party licences
and attribution: [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

<a id="troubleshooting"></a>
## Troubleshooting

| Symptom | What to do |
|---|---|
| **Model not found / not downloaded** | Run `launch.bat` — the bootstrap detects the missing model and downloads it (resumable). Details: `logs\bootstrap.log`. The model lives in `models\higgs-audio-v3\`. |
| **Hugging Face / download errors** | GUINEO uses only a public model and never needs an authentication token. Behind a firewall or proxy, check HTTPS connectivity; the download resumes from `.incomplete` files. |
| **Missing voice profile** | Open the Voice Library (`Ctrl+L`) and check that the profile exists and that its reference WAV is really inside `voices\`. Re-import it if needed (File → Import Voice). |
| **A generated file is missing** | The History entry shows the path (`outputs\…`). If the file was deleted manually, the scene output becomes MISSING — regenerate the part, or choose another version/output. |
| **STALE scene output** | Not an error: the combined output no longer reflects the current composition. Remedies: **Combine Scene** (a fresh snapshot) or **Choose Output** (a deliberate selection, confirmed with “Use Anyway”). |
| **Generation anomaly (⚠)** | The file was kept and merely flagged. Listen to it; if it is wrong, regenerate the row — a new version is created and the old one is kept. |
| **GPU / CUDA issues** | Install the latest NVIDIA driver. The model uses ~10 GB of VRAM — close other VRAM-heavy applications. Without CUDA the app falls back to the CPU (slow). The status bar shows the CUDA state. |
| **OpenGL / display issues** | The animated background falls back automatically through GPU → static CPU → plain dark; the application remains usable. Updating the graphics driver usually helps. |
| **Fullscreen** | Enter with `F11`; leave with `F11` or `Esc`. |
| **No sound / playback silent** | Playback uses the system audio device; check the Windows volume and the default device. If the playback library is unavailable, GUINEO still writes the files — it simply cannot play them. |
| **Export errors** | MP3 creation requires **FFmpeg** (install it and put it on PATH); without it, a WAV is produced with a warning. Ensure a few GB of free space on the target drive. |
| **Generate does nothing** | Wait for the model to finish loading (status bar / `logs\application.log`: “Model loaded”), then try again. |
| **White/empty model-load window on first run** | A known historical defect, fixed in the current build; if you ever see it, update your graphics driver and send `logs\crash.log` to the developer. |

Further logs: `logs\application.log`, `logs\engine.log`,
`logs\bootstrap.log`, `logs\crash.log`.
<a id="for-developers"></a>
## For developers — repository and tests

The complete source code lives on GitHub:
<https://github.com/AGE-T/GUINEO>. The repository contains the same tree
that ships in the runnable ZIPs (see the `releases/` folder), so the
application can be built and studied directly from a checkout. The supported
end-user setup path is `launch.bat` / `bootstrap.py` (Windows), which creates
the virtual environment, installs the dependencies and downloads the model —
see [Installation](#installation).

**Running the test suite.** The tests are pure offscreen runs — no GPU, no
model download:

```
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
```

Dependencies: PySide6, NumPy, SoundFile, sounddevice (`torch` is stubbed by
the house test pattern). On headless Linux, the EGL/GL runtime libraries
must be present for the Qt import.

**Verification scripts** (`tools/`): `verify_compile.py`,
`verify_architecture.py`, `verify_integration.py`,
`verify_functional_integrity.py`, `verify_feature_gate.py`,
`verify_transport_gl.py`.

**Layout**: `engine/` — backend (model manager, prompt builder, audio,
batch, provenance, persistence); `ui/` — PySide6 frontend; `tests/` —
pytest regression suites; `tools/` — verification scripts; `docs/` — design
records, audits and governance; `spec/` — the original specifications;
`research/` — upstream reference documentation; `assets/` — fonts, icons,
brand; `presets/` — saved presets.

**Governance**: development rules in
[`docs/governance/DEVELOPMENT_RULES.md`](docs/governance/DEVELOPMENT_RULES.md);
the known-issues register in
[`docs/governance/OPEN_BUGS.md`](docs/governance/OPEN_BUGS.md); the full
phase history in [`DEVELOPMENT_LOG.txt`](DEVELOPMENT_LOG.txt).

---

## ☕ Support


If you find this project helpful and want to support my work, feel free to buy
me a coffee!

[Support me on Ko-fi](https://ko-fi.com/thomashoysgameaudio)
([image](https://camo.githubusercontent.com/12ddacd4b1ffd5473ce102384087761260706d10e6ee9d4c4d4bcf84c9608dcc/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f537570706f72745f6f6e5f4b6f2d2d66692d4631363036313f7374796c653d666f722d7468652d6261646765266c6f676f3d6b6f2d6669266c6f676f436f6c6f723d7768697465))

Or click the button below:

[Ko-fi Support](https://ko-fi.com/thomashoysgameaudio)
([image](https://camo.githubusercontent.com/201ef269611db7eb6b5d08e9f756ab8980df3014b64492770bdf13a6ed924641/68747470733a2f2f6b6f2d66692e636f6d2f696d672f676974687562627574746f6e5f736d2e737667))

https://ko-fi.com/thomashoysgameaudio
