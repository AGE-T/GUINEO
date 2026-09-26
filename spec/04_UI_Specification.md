04_UI.md
SpeechStudio User Interface Specification
Version: 1.0
Status: Approved
1. Purpose
This document defines the complete graphical user interface of SpeechStudio.
The UI is the only interface between the user and the Higgs Audio V3 TTS engine.
Users must never edit Python files, configuration files or prompt tokens during normal operation.
The interface must expose every officially supported Higgs Audio V3 capability.
2. Design Goals
The interface shall be
Modern
Clean
Fast
Responsive
Dark Theme by default
Optimized for mouse and keyboard
Suitable for both beginners and advanced users
3. Main Window Layout
The application consists of five primary regions.
1



+-------------------------------------------------------------+
| Menu Bar |
+-------------------------------------------------------------+
| Toolbar |
+---------+---------------------------+------------------------+
| | | |
| | | |
| Sidebar | Prompt Editor | Control Panel |
| | | |
| | | |
+---------+---------------------------+------------------------+
| Waveform / Audio Player |
+-------------------------------------------------------------+
| Status Bar |
+-------------------------------------------------------------+
4. Menu Bar
File
New Project
Open Project
Save Project
Import Voice
Export Voice
Import Preset
Export Preset
Exit
Generation
Generate
Stop
Replay
Clear
2



Tools
Benchmark
Voice Library
History
Settings
Developer
View
Theme
Reset Layout
Fullscreen
Help
Documentation
About
5. Toolbar
Buttons
Generate
Stop
Replay
Open Output Folder
Voice Library
History
Settings
Benchmark
Developer Mode
3



Toolbar icons only.
Tooltip required.
6. Left Sidebar
Contains
Voice Library
History
Presets
Projects
Output Browser
Collapsible.
Resizable.
7. Center Panel
Large Prompt Editor.
Features
Plain text editing
Undo
Redo
Copy
Paste
Drag and Drop
Line numbers optional
Syntax highlighting in Developer Mode
Supports large prompts.
4



8. Right Control Panel
Scrollable.
Contains every Higgs configuration.
Sections
Voice
Emotion
Style
Prosody
Sound Effects
Generation
Reference Audio
Prompt Preview
9. Voice Section
Controls
Voice Profile selector
Import Voice
Export Voice
Create Voice
Delete Voice
Reference Audio
Reference Transcript
Voice Information
Display
5



Voice name
Reference duration
Sample rate
Channels
Status
10. Emotion Section
Contains every official Higgs emotion.
21 Toggle Buttons.
Exactly one selected in normal mode.
Supported
Elation
Amusement
Enthusiasm
Determination
Pride
Contentment
Affection
Relief
Contemplation
Confusion
Surprise
Awe
Longing
Arousal
6



Anger
Fear
Disgust
Bitterness
Sadness
Shame
Helplessness
Each button displays
Name
Tooltip
Example usage
Generated token
11. Style Section
Three buttons.
Singing
Shouting
Whispering
Exactly one active.
12. Prosody Section
Speed
Normal
Very Slow
Slow
7



Fast
Very Fast
Pitch
Normal
Low
High
Delivery
Normal
Expressive High
Expressive Low
Pause controls
Insert Pause
Insert Long Pause
Pause buttons insert inline tokens at cursor position.
13. Sound Effects
Official Higgs SFX only.
Cough
Laughter
Crying
Screaming
Burping
Humming
Sigh
Sniff
8



Sneeze
The UI automatically inserts
Token
Suggested onomatopoeia
Correct spacing
User never types SFX tokens manually.
14. Generation Section
Parameters
Temperature
Top P
Top K
Max New Tokens
Seed
Append Silence
Output Format
Auto Play
Normalize Audio
Each parameter contains
Current value
Default value
Tooltip
Reset button
9



15. Reference Audio
Reference WAV
Reference Transcript
Trim Reference
Normalize
Refresh
Display
Duration
Sample Rate
Channels
File Name
Validation Status
16. Prompt Preview
Read only.
Displays
Final prompt
Inserted Higgs tokens
Reference information
Warnings
Copy button
Developer Mode may allow editing.
10



17. Audio Player
Waveform
Play
Pause
Stop
Seek
Volume
Zoom
Playback Speed
Displays
Length
Sample Rate
Output Format
18. Status Bar
Displays
Model
CUDA
GPU Name
VRAM Usage
Generation Time
Generation Status
Current Voice
11



19. History Window
Each entry stores
Timestamp
Voice
Prompt
Generation Parameters
Output File
Duration
Replay
Reuse
Delete
Export
20. Preset Window
Stores
Voice
Emotion
Style
Prosody
Generation Parameters
Prompt Template
User can
Create
Rename
12



Duplicate
Delete
Import
Export
21. Settings Window
General
Generation
Paths
Audio
Performance
Developer
Appearance
No Python editing required.
22. Benchmark Window
Compare
Temperature
Top P
Top K
Emotion
Prosody
Voice
Displays
Generation Time
13



Output Duration
Realtime Factor
23. Developer Mode
Displays
Raw Prompt
Token List
GPU Memory
Generation Log
Prompt Validation
Timing
No additional functionality.
Only additional information.
24. Validation
Before generation
Prompt not empty
Voice valid
Reference valid
Generation parameters valid
Model loaded
CUDA available
Output path valid
Errors shown inside UI.
No terminal required.
14



25. Responsive Behaviour
Panels resizable.
Sidebar collapsible.
Right panel collapsible.
Layout automatically restored at startup.
26. Theme
Dark theme default.
Light theme optional.
Consistent spacing.
Rounded controls.
Native Windows behavior.
27. Keyboard Shortcuts
Ctrl N
New Project
Ctrl O
Open
Ctrl S
Save
Ctrl Enter
Generate
Esc
Stop
15



Space
Play Pause
Ctrl L
Voice Library
Ctrl H
History
Ctrl P
Prompt Preview
28. UI Principles
The interface must expose every officially supported Higgs Audio V3 feature.
The interface must not expose unsupported or experimental options.
Every visible control must correspond to either
an official Higgs prompt token
or
an officially supported generation parameter.
No placeholder controls.
No future functionality.
No hidden experimental features.
The UI is a graphical representation of the Higgs Audio V3 capabilities.
16

29. V6 Horizontal Transport (Authoritative Visual Reference)

The bottom transport (WaveformPlayer) follows the validated V6 standalone
reference test (gpu_transport_horizontal_waveform_v12_test_v6.py). The V6
file is the AUTHORITATIVE visual specification for transport geometry,
sizes, glow, and colors. The implementation in ui/panels/waveform_player.py
reproduces those values exactly while keeping the existing playback engine.

29.1 V6 Reference Values

Reference canvas:        1280 x 300 px
Island:
  Center:                (640, 150)
  Half-size:             (624, 116) -> total 1248 x 232 px
  Corner radius:         18 px
  Body color:            rgb(0.082, 0.074, 0.092)  ->  #151318
  Border:                rgb(0.16, 0.13, 0.19) x 0.75 alpha  ->  #292130

Play / Pause:
  Center:                (120, 150)
  Outer diameter:        192 x 192 px  (radius 96)
  Core radius:           80 px         (color rgb(0.055, 0.050, 0.065)  ->  #0E0D11)
  Track ring radius:     88 px         (R = 88)
  Track width:           3 px          (color rgb(0.165, 0.165, 0.165)  ->  #2A2A2A)
  Active track width:    4 px          (primary color)
  Icon texture size:     128 x 128 px

Stop:
  Center:                (315, 150)
  Half-size:             (40, 40)  ->  80 x 80 px
  Corner radius:         12 px
  Body color:            rgb(0.1098, 0.1059, 0.1059)  ->  #1C1B1B

Waveform container:
  Center:                (850, 150)
  Half-size:             (390, 96)  ->  780 x 192 px
  Corner radius:         10 px
  Body color:            rgb(0.065, 0.060, 0.072)  ->  #111012
  Inactive bar:          rgb(0.28, 0.24, 0.34)  ->  #483D57
  Active bar:            primary  ->  #D0BCFF
  Bar max half-height:   34 px

Layout gaps (left to right):
  Play left edge:        24 px from island left
  Play -> Stop gap:      59 px
  Stop -> Wave gap:      105 px  (intentional breathing room)
  Wave right edge:       1240 px  (24 px from island right edge 1264)

Colors (preserved via QColor.fromRgbF, exact float match):
  V6_PURPLE_GLOW         (0.6588235, 0.3333333, 0.9686275)  ->  #A855F7
  V6_PRIMARY              (0.8156863, 0.7372549, 1.0)         ->  #D0BCFF
  V6_PLAY_CORE            (0.055, 0.050, 0.065)               ->  #0E0D11
  V6_PLAY_TRACK           (0.165, 0.165, 0.165)               ->  #2A2A2A
  V6_STOP_BODY            (0.1098, 0.1059, 0.1059)            ->  #1C1B1B
  V6_ISLAND_BODY          (0.082, 0.074, 0.092)                ->  #151318
  V6_ISLAND_BORDER        (0.16, 0.13, 0.19)                  ->  #292130
  V6_WAVE_BODY            (0.065, 0.060, 0.072)                ->  #111012
  V6_WAVE_BORDER          (0.12, 0.10, 0.15)                   ->  #1F1A26
  V6_WAVE_INACTIVE        (0.28, 0.24, 0.34)                  ->  #483D57
  V6_WAVE_CENTER_LINE     (0.22, 0.18, 0.28)                  ->  #382E47

29.2 Glow Formulas (reproduced pixel-accurately via numpy)

Play/Pause glow:
  base       = exp(-max(d - 94, 0) / 15) * 0.22
  playing    = exp(-max(d - 94, 0) / 25) * 0.42
             + exp(-max(d - 94, 0) / 45) * 0.22
  glow       = mix(base, playing, playing_factor)
  glow      *= (1 + 0.18 * playHover)
  color     += purple * glow

Stop glow:
  base       = exp(-max(d, 0) / 15) * 0.40
  glow      *= (1 + 0.30 * stopHover)
  if pressed && hover:
      glow  += exp(-max(d, 0) / 25) * 0.70
  color     += purple * glow

The rounded-rectangle SDF (used for Stop glow) reproduces V6 roundBox():
  q = abs(p) - halfSize + vec2(radius)
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius

29.3 Icon SVG Paths (Material Symbols)

  play_arrow:    M8 5v14l11-7z
  pause:         M6 19h4V5H6v14zm8-14v14h4V5h-4z
  stop:          M6 6h12v12H6z

29.4 Preserved Application Functionality

The transport keeps the existing playback engine completely intact.

Signals (unchanged):
  play_requested        emitted when Play/Pause clicked in stopped or paused state
  pause_requested       emitted when Play/Pause clicked in playing state
  stop_requested        emitted when Stop clicked
  seek_requested(float) emitted on waveform click or drag (position in seconds)
  volume_changed(float) emitted on volume slider change (0.0 to 1.0)

Methods (unchanged signatures):
  set_audio_info(duration, sample_rate, output_path)
  set_play_position(position_sec)
  set_playback_state(state)  # "stopped" | "playing" | "paused"
  set_volume(volume)          # 0.0 to 1.0, blocks signals
  get_volume() -> float
  clear()
  current_path  (property)

Engine components preserved:
  - WAV loading via WaveformCanvas.load_wav() (numpy peak extraction)
  - Click-to-seek and drag-to-scrub on the waveform
  - Hover indicator (subtle dashed line)
  - Mono downmix for multi-channel WAVs
  - Peak recomputation on canvas resize

29.5 Implementation Module

  ui/panels/waveform_player.py
    - V6 reference values as module-level constants (V6_ prefix)
    - TransportPlayButton(QWidget)   192x192, custom paint with glow + ring
    - TransportStopButton(QWidget)   80x80, custom paint with glow + body
    - WaveformCanvas(QWidget)        192 tall, V6 dark container + real WAV data
    - WaveformPlayer(QWidget)        the V6 transport island itself

29.6 Authoritative Reference Document

For the complete extracted value table, glow formula derivations,
implementation architecture, and validation checklist, see:
  docs/V6_TRANSPORT_REFERENCE.md
