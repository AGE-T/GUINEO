06_GENERATION_ENGINE.md
SpeechStudio Generation Engine Specification
Version: 1.0
Status: Approved
1. Purpose
The Generation Engine is responsible for converting a validated prompt into synthesized speech using
the local Higgs Audio V3 TTS model.
It is the only component allowed to communicate with the Transformers model.
2. Responsibilities
The Generation Engine shall
load the model
keep the model resident in memory
receive validated generation requests
invoke generate_speech()
receive generated audio
optionally append silence
save the audio
return generation metadata
The engine must never contain UI code.
1



3. Generation Pipeline
UI
↓
Prompt Builder
↓
Validation
↓
Generation Engine
↓
generate_speech()
↓
Audio Buffer
↓
Optional Silence Padding
↓
Save Output
↓
Audio Player
↓
History
4. Model Lifecycle
The model shall be loaded once during application startup.
The model shall remain in GPU memory until the application exits.
2



The model shall never be reloaded between generations.
The tokenizer shall be loaded once.
5. Supported Backend
Current backend
Transformers
Current model
multimodalart/higgs-audio-v3-tts-4b-transformers
Execution device
CUDA
Supported precision
FP16
BF16
6. Required Inputs
Prompt
Tokenizer
Loaded Model
Reference Audio (optional)
Reference Transcript (optional)
Generation Parameters
Output Path
7. Reference Voice
If voice cloning is enabled, the engine shall provide
3



reference_audio
reference_sample_rate
reference_text
to the model.
If voice cloning is disabled, these values shall not be supplied.
8. Generation Parameters
SpeechStudio shall expose the following parameters.
Temperature
Type
Float
Purpose
Sampling randomness
Recommended default
1.0
Supported UI range
0.10
to
2.00
Top P
Type
Float
Purpose
Nucleus sampling
4



Recommended default
0.95
Supported range
0.10
to
1.00
Top K
Type
Integer
Purpose
Limits candidate token selection
Recommended default
50
Supported UI range
1
to
500
Max New Tokens
Type
Integer
Purpose
Maximum generated token count
Recommended default
5



2048
Minimum
128
Maximum
4096
Seed
Type
Integer
Purpose
Optional deterministic generation
Default
Random
Append Silence
Type
Float
Purpose
Adds silence to the end of generated audio.
Recommended default
1.0 second
Range
0.0
to
5.0 seconds
6



Auto Play
Type
Boolean
Purpose
Automatically plays generated audio.
Normalize Output
Type
Boolean
Purpose
Normalize output volume before saving.
Output Format
Current
WAV
Future formats are outside the current project scope.
9. Audio Post Processing
The engine may optionally append silence after generation.
Purpose
Prevent abrupt audio ending.
Implementation
Append zero samples.
Duration configurable.
7



10. Output Naming
Default naming
yyyyMMdd_HHmmss.wav
Example
20260625_184512.wav
11. Output Directory
Default
outputs/
The engine shall create the directory if missing.
12. Error Handling
Possible failures
Model not loaded
CUDA unavailable
Reference audio missing
Reference transcript invalid
Generation failure
Output write failure
Every failure shall return a structured error.
No silent failures.
13. Generation Metadata
Each generation shall return
8



Generation Time
Output Duration
Realtime Factor
Output Sample Rate
Output File
Generation Parameters
Voice Profile
Timestamp
14. History Integration
After successful generation
Save audio
Save metadata
Register history entry
Return playback handle
15. Performance
The engine shall minimise unnecessary allocations.
Reference audio should be reused while the same voice profile remains active.
The tokenizer shall never be recreated during normal operation.
16. Memory Management
The engine shall
reuse the loaded model
reuse the tokenizer
9



release temporary tensors
avoid duplicate audio buffers where possible
17. CUDA Requirements
Preferred device
CUDA
If CUDA is unavailable
Generation shall be prevented unless CPU mode is explicitly enabled.
The application shall display the reason.
18. Logging
Each generation shall create a log entry containing
Start Time
End Time
Generation Duration
Generation Parameters
Voice Profile
Output File
Warnings
Errors
19. Benchmark Data
The engine shall expose
Generation Time
Output Length
10



Realtime Factor
GPU Name
VRAM Usage if available
These values are displayed in Benchmark mode.
20. Engine API
The UI communicates only with the Generation Engine.
The Generation Engine communicates only with the backend adapter.
The UI shall never directly call Transformers.
21. Configuration Ownership
The Generation Engine owns
generation parameters
sampling configuration
output writing
silence padding
metadata creation
The Prompt Builder owns prompt construction.
The Voice System owns reference voice management.
22. Threading
Generation shall execute on a worker thread.
The UI thread must remain responsive.
Audio playback shall not block generation.
11



23. Validation Requirements
Generation starts only if
Model loaded
Prompt valid
Voice valid
Reference audio valid if present
Output directory writable
Generation parameters valid
24. Supported Current Parameters
The engine currently supports
temperature
top_p
top_k
max_new_tokens
reference_audio
reference_sample_rate
reference_text
Future parameters shall only be added after confirmation from official Higgs documentation or verified
implementation.
25. Success Criteria
The Generation Engine is successful if
the model is loaded once,
generation starts without Python scripting,
12



speech is produced,
metadata is stored,
history is updated,
and the UI remains responsive throughout the process.
13
