04_REFERENCE.md
SpeechStudio Higgs Audio V3 Reference
Version: 1.0
Status: Approved
1. Purpose
This document serves as the complete reference for every Higgs Audio V3 capability supported by
SpeechStudio.
It defines every officially supported control token, generation parameter and verified behaviour used by
the application.
This document is the primary reference for both developers and AI coding assistants.
2. Supported Model
Current backend
multimodalart/higgs-audio-v3-tts-4b-transformers
Original model
BosonAI Higgs Audio V3 TTS 4B
Execution
Transformers
CUDA
3. Emotion Tokens
SpeechStudio supports every official Higgs emotion.
Emotion Token Description
Elation <\|emotion:elation\|> Joy
1



Emotion Token Description
Amusement <\|emotion:amusement\|> Playful
Enthusiasm <\|emotion:enthusiasm\|> Excitement
Determination <\|emotion:determination\|> Firm
Pride <\|emotion:pride\|> Confident
Contentment <\|emotion:contentment\|> Calm satisfaction
Affection <\|emotion:affection\|> Warm
Relief <\|emotion:relief\|> Relief
Contemplation <\|emotion:contemplation\|> Reflective
Confusion <\|emotion:confusion\|> Confused
Surprise <\|emotion:surprise\|> Surprise
Awe <\|emotion:awe\|> Wonder
Longing <\|emotion:longing\|> Yearning
Arousal <\|emotion:arousal\|> Heightened intensity
Anger <\|emotion:anger\|> Anger
Fear <\|emotion:fear\|> Fear
Disgust <\|emotion:disgust\|> Disgust
Bitterness <\|emotion:bitterness\|> Bitterness
Sadness <\|emotion:sadness\|> Sadness
Shame <\|emotion:shame\|> Shame
Helplessness <\|emotion:helplessness\|> Helpless
4. Style Tokens
Style Token
Singing <\|style:singing\|>
Whispering <\|style:whispering\|>
Shouting <\|style:shouting\|>
2



5. Prosody Tokens
Speed
Token Effect
speed_very_slow ~0.65×
speed_slow ~0.85×
speed_fast ~1.2×
speed_very_fast ~1.4×
Pitch
Token Effect
pitch_low Lower pitch
pitch_high Higher pitch
Delivery
Token Effect
expressive_high More expressive
expressive_low Flatter delivery
Pauses
Token Effect
pause Short pause
long_pause Long pause
6. Sound Effects
Effect Token Suggested Text
Cough <\|sfx:cough\|> Ahem
Laughter <\|sfx:laughter\|> Haha
Crying <\|sfx:crying\|> Sob
Screaming <\|sfx:screaming\|> Ahh
3



Effect Token Suggested Text
Burping <\|sfx:burping\|> Burp
Humming <\|sfx:humming\|> Hmm
Sigh <\|sfx:sigh\|> Ahh
Sniff <\|sfx:sniff\|> Sff
Sneeze <\|sfx:sneeze\|> Achoo
7. Generation Parameters
Parameter Type Default UI Range
temperature float 1.0 0.1–2.0
top_p float 0.95 0.1–1.0
top_k integer 50 1–500
max_new_tokens integer 2048 128–4096
seed integer Random Any
append_silence float 1.0 s 0–5 s
8. Voice Cloning
Supported
Reference WAV
Reference Transcript
Reference Sample Rate
Reference Audio Validation
Voice Profiles
9. Prompt Rules
Sentence level tokens
Emotion
4



Style
Pitch
Speed
Delivery
Inline tokens
Pause
Long Pause
Sound Effects
10. Verified SpeechStudio Findings
This section contains experimentally verified behaviour.
Examples
Approximately 15 seconds of speech is consistently reliable.
Appending one second of silence improves natural sentence endings.
Emotion transitions produce more stable output than combining multiple emotions within a single
sentence.
Voice cloning quality improves with clean reference recordings.
11. GUI Mapping
Every supported Higgs feature is mapped to a dedicated UI control.
Emotion
Emotion Panel
Style
Style Panel
Prosody
5



Prosody Panel
Sound Effects
SFX Panel
Generation Parameters
Generation Panel
Reference Voice
Voice Panel
Prompt Preview
Prompt Preview Panel
12. Prompt Examples
Short narrator
Podcast
Movie trailer
Conversation
Horror
Documentary
Fast speech
Whisper
Singing
Voice clone
These examples are maintained as living examples and updated when verified improvements are found.
13. Official References
BosonAI Higgs Audio
6



PROMPTING.md
Hugging Face Model Card
Transformers Port
SpeechStudio Deep Research
SpeechStudio Verified Experiments
7
