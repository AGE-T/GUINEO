REFERENCES.md
SpeechStudio Reference Documentation
Version: 1.0
Status: Approved
1. Purpose
This document lists every official and approved reference used during the development of
SpeechStudio.
Only sources listed in this document may be considered authoritative.
If two sources conflict, priority is determined by the order defined below.
2. Source Priority
Priority 1
Official BosonAI documentation
Priority 2
Official Hugging Face model documentation
Priority 3
Official BosonAI GitHub repository
Priority 4
Official implementation examples
Priority 5
SpeechStudio verified experiments
Priority 6
SpeechStudio Deep Research documents
1



Information from lower priority sources must never override higher priority sources without
verification.
3. Official Model
Model
BosonAI Higgs Audio V3 TTS 4B
Repository
https://huggingface.co/bosonai/higgs-tts-v3-4b
Purpose
Primary speech synthesis model.
Status
Official.
4. Transformers Implementation
Model
multimodalart Higgs Audio V3 Transformers Port
Repository
https://huggingface.co/multimodalart/higgs-audio-v3-tts-4b-transformers
Purpose
Current backend used by SpeechStudio.
Status
Approved implementation.
5. Official Prompt Documentation
Document
2



PROMPTING.md
Repository
https://huggingface.co/bosonai/higgs-tts-v3-4b/blob/main/PROMPTING.md
Purpose
Official prompt syntax.
Emotion tokens.
Style tokens.
Prosody tokens.
Sound effect tokens.
Prompt construction.
6. Official GitHub Repository
Repository
https://github.com/boson-ai/higgs-audio
Purpose
Reference implementation.
Examples.
Model loading.
Backend architecture.
7. SGLang Cookbook
Document
Higgs Audio Cookbook
Repository
https://sgl-project.github.io/sglang-omni/cookbook/higgs_tts.html
3



Purpose
Reference implementation.
Performance notes.
Prompt examples.
API usage.
Only implementation details applicable to the local Transformers backend shall be adopted.
8. vLLM Omni Documentation
Document
BosonAI Higgs Audio V3 TTS
Repository
https://github.com/vllm-project/vllm-omni/blob/main/recipes/BosonAI/Higgs-Audio-V3-TTS.md
Purpose
Generation pipeline reference.
Configuration reference.
Performance reference.
Only implementation concepts applicable to SpeechStudio shall be used.
9. Benchmark Reference
Document
benchmark_tts_seedtts.py
Repository
https://github.com/sgl-project/sglang-omni/blob/main/benchmarks/eval/benchmark_tts_seedtts.py
Purpose
Benchmark methodology.
4



Performance comparison.
Generation workflow reference.
10. SpeechStudio Research
Internal documents.
Approved as project references.
Research documents include
Higgs prompting research
Generation parameter research
Voice cloning research
Performance research
UI research
These documents are stored in
research/
11. Verified Experiments
SpeechStudio maintains its own verified test results.
Only reproducible experiments are considered valid references.
Examples
Emotion tests
Style tests
Prosody tests
Sound effect tests
Voice cloning tests
Generation parameter tests
5



Silence padding tests
Performance measurements
These documents are stored in
research/tests/
12. Generation Parameters
Official parameter definitions are taken from
Official Hugging Face documentation
Verified implementation
SpeechStudio experiments
Only parameters verified through these sources shall appear in the UI.
13. Prompt Tokens
Prompt syntax is derived from
PROMPTING.md
SpeechStudio verification
Every supported token used by SpeechStudio must exist in the official documentation or be
experimentally verified.
14. Voice Cloning
Reference voice recommendations are based on
Official documentation
SpeechStudio experiments
Reference audio validation rules
Reference transcript handling
6



Reference sample rate
Reference duration
15. Performance
Performance recommendations are based on
Official documentation
CUDA behaviour
SpeechStudio benchmarks
Measured generation times
Measured VRAM usage
Measured realtime factor
16. Internal Specifications
The following project documents are normative.
00_PROJECT.md
01_REQUIREMENTS.md
02_BOOTSTRAP.md
03_ARCHITECTURE.md
04_UI.md
05_PROMPT_SYSTEM.md
06_GENERATION_ENGINE.md
07_VOICE_SYSTEM.md
08_SETTINGS.md
09_STORAGE.md
10_AI_DEVELOPMENT_RULES.md
7



11_TESTING.md
These documents define SpeechStudio behaviour.
17. External Libraries
SpeechStudio currently depends on
Python
PyTorch
Transformers
Hugging Face Hub
SoundFile
NumPy
Future dependencies require explicit approval.
18. Validation Policy
Information added to SpeechStudio documentation must satisfy at least one of the following
Official BosonAI documentation
Official Hugging Face documentation
Official repository implementation
Verified SpeechStudio experiment
Verified Deep Research finding
Information failing these requirements shall not be treated as specification.
19. Documentation Update Policy
Whenever an official Higgs release changes behaviour
The relevant SpeechStudio specification shall be reviewed.
8



The change shall be verified experimentally before implementation.
Documentation must be updated before source code.
20. Project Philosophy
SpeechStudio documentation is implementation driven.
Specifications are based on verified behaviour.
Assumptions are not specifications.
Every implementation decision should be traceable to an approved reference or a reproducible
experiment.
9
