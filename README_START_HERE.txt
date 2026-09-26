================================================================
  GUINEO - Quick Start Guide
================================================================

WHAT THIS IS:
  GUINEO (formerly developed under the name SpeechStudio) is a
  portable Windows desktop application for local speech synthesis
  using the Higgs TTS 3 (Higgs Audio V3) model from Boson AI.

  The application manages its own virtual environment, dependencies,
  and model downloads automatically. You never need to open a terminal,
  edit Python files, or activate a virtual environment manually.

  All processing is local. No cloud, no telemetry.

  The complete user guide is README.md (British English) in this
  folder; the Hungarian guide is README_HU.md. Third-party licences:
  THIRD_PARTY_LICENSES.md and LICENSE_HIGGS_TTS_3.txt.

  Source code and all version ZIPs: https://github.com/AGE-T/GUINEO

REQUIREMENTS:
  - Windows 10/11 (64-bit)
  - Python 3.11 (exactly) - install from https://www.python.org/
    Make sure "Add Python to PATH" is checked during installation.
  - NVIDIA GPU with CUDA support (for speech generation, ~10 GB VRAM)
  - ~12 GB free disk space (for the model download)
  - Internet connection (for the first run only)

HOW TO START:
  1. Extract this folder to any location on your computer
     (e.g. C:\GUINEO or D:\GUINEO)
  2. Double-click launch.bat
  3. Wait for the first-run setup to complete:
     - Creates a virtual environment (venv\)
     - Installs PyTorch, Transformers, PySide6, and other dependencies
     - Downloads the Higgs TTS 3 model (~9 GB, one-time)
  4. The GUINEO window will open automatically

FIRST RUN TAKES TIME:
  The first run downloads ~9 GB of model files. This can take
  10-60 minutes depending on your internet connection. The download
  is resumable - if it is interrupted, just run launch.bat again.

SUBSEQUENT RUNS:
  After the first run, launch.bat starts instantly because everything
  is cached locally. No internet connection is needed.

HOW TO UPDATE (IMPORTANT):
  To update GUINEO to a new version WITHOUT losing your data:

  1. Download the new version ZIP and rename it to SpeechStudio_clean.zip
  2. Place it in the GUINEO folder (next to SpeechStudio.py)
  3. Close the GUINEO application if it is running
  4. Double-click update.bat

  The update script:
  - Backs up your current source code (to _backup_source_*)
  - Extracts ONLY the .py/.txt/.bat/.md/.yaml source files
  - PRESERVES your personal data:
      * settings\settings.json  (theme, project, volume, window layout)
      * settings\history\       (history entries)
      * voices\                 (voice profiles)
      * outputs\                (generated audio)
      * presets\                (saved presets)
      * models\                 (the ~9 GB Higgs model)
  - NEVER delete the whole folder and re-extract! Use update.bat.

KEYBOARD SHORTCUTS:
  Ctrl+Enter          Generate speech
  Ctrl+Shift+Enter    Generate Long Narration (splits text, batch)
  Esc                 Stop generation / leave fullscreen / close search
  Space               Play / Pause playback (when editor not focused)
  Ctrl+N              New Project
  Ctrl+Shift+N        New Scene
  Ctrl+O              Open Project
  Ctrl+S              Save Project
  Ctrl+Shift+E        Export Project
  Ctrl+Shift+A        Assemble Scenes
  Ctrl+L              Voice Library
  Ctrl+H              History
  F11                 Fullscreen
  Ctrl+Q              Exit

FEATURES:
  Generation:
  - Single generation with full Higgs token support
  - Long Narration: splits text into safe parts, batch-generates
    each part, then Combine Scene produces one versioned output
  - Batch Generation window (Scene mode): coverage tracking, per-part
    version selection, regeneration, Combine Scene, Export Audio
  - Batch Queue (manual mode): queue, edit, reorder, save/load
    queues, Merge Completed Parts
  - Scenes keep full audio provenance: block versions -> current
    composition -> immutable Combined outputs (v01, v02, ...)
  - STALE detection tells you when a Combined output no longer
    matches the current composition (the file itself is fine)
  - Project Assembly stitches finished Scenes into one WAV
    (optional MP3 with FFmpeg); a single Part is never silently
    used as a whole Scene output (REVIEW REQUIRED instead)

  Projects / Scenes / Characters:
  - Projects group scenes, characters and audio with provenance
  - Scene ordering (Move Up / Move Down), per-scene voice settings
  - Characters with assigned Voice Profiles and stable colour
    identity; block-level Character assignment

  Playback (waveform player):
  - Click or drag on the waveform to seek
  - Pause / Resume / Stop transport controls
  - Volume slider (persisted across restarts)
  - Real-time playhead tracking

  History:
  - Every generation is stored as a reproducible JSON entry
  - Combined and assembled audio also gets a history entry
  - Browse, replay, reuse settings, export, delete

  Themes (5):
  - Modern Dark (default), Dark, Light, Synthwave, Retro Console
  - Switch via the View menu

  Supported Languages:
  - 102 languages (85 production-quality, 17 usable)
  - See Help > Supported Languages for the full list
  - Hungarian is in Tier 1 (production quality)

  Voices:
  - Zero-shot voice cloning from a reference audio clip (WAV)
  - Import / export voice profiles

TROUBLESHOOTING:
  - If launch.bat says Python is not found:
    Install Python 3.11 from https://www.python.org/downloads/release/python-3119/
    Make sure "Add Python to PATH" is checked.

  - If the download fails:
    Check logs\bootstrap.log for details. The download uses regular
    HTTPS (not the hf-xet protocol) and is resumable. Re-run launch.bat.

  - If you see "Not enough disk space":
    Free up at least 12 GB and run launch.bat again.

  - If the model loads but CUDA is unavailable:
    Install the latest NVIDIA drivers from https://www.nvidia.com/drivers

  - If the Generate button does nothing:
    Wait for the model to finish loading (check the status bar or
    logs\application.log for "Model loaded").

  - STALE scene output: not an error. Use Combine Scene for a fresh
    snapshot, or Choose Output to deliberately select the old one.

PROJECT STRUCTURE:
  launch.bat          <- Double-click this to start
  update.bat          <- Use this to update from a new zip (preserves data)
  bootstrap.py        <- Environment setup (runs automatically)
  SpeechStudio.py     <- Application entry point
  requirements.txt    <- Python dependencies
  README.md           <- Full user guide (British English)
  README_HU.md        <- Teljes magyar felhasználói útmutató
  engine\             <- Backend (model, prompt builder, audio, batch, etc.)
  ui\                 <- Frontend (PySide6 window, panels, theme)
  spec\               <- Project specifications
  research\           <- Reference documentation
  tools\              <- Verification scripts
  .cache\             <- Hugging Face cache (created on first run)
  venv\               <- Virtual environment (created on first run)
  logs\               <- Application logs
  outputs\            <- Generated WAV files
  voices\             <- Voice profiles
  settings\           <- settings.json + history\ entries
  presets\            <- Saved presets
  models\             <- The Higgs TTS 3 model weights

================================================================
  Support the developer
================================================================
  If GUINEO is useful to you, consider supporting continued
  development:

  Ko-fi: https://ko-fi.com/thomashoysgameaudio

================================================================
