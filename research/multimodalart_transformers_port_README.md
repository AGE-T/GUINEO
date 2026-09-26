---
license: other
license_name: bosonai-higgs-audio-v3
license_link: LICENSE
language:
- en
pipeline_tag: text-to-speech
tags:
- text-to-speech
- tts
- voice-cloning
- higgs-audio
- qwen3
- custom_code
base_model:
- bosonai/higgs-audio-v3-tts-4b
library_name: transformers
---

# Higgs Audio v3 TTS (4B) — transformers `trust_remote_code` port

A `trust_remote_code` packaging of [`bosonai/higgs-audio-v3-tts-4b`](https://huggingface.co/bosonai/higgs-audio-v3-tts-4b)
that loads with plain 🤗 transformers (no SGLang). The weights are the original
checkpoint, copied unchanged; only a small `modeling_*.py` / `configuration_*.py`
pair and an `auto_map` were added.

The model is a standard **Qwen3-4B** backbone plus a fused multi-codebook audio
embedding/head. Reference-audio encoding and waveform decoding use the
transformers-native [`bosonai/higgs-audio-v2-tokenizer`](https://huggingface.co/bosonai/higgs-audio-v2-tokenizer)
(`higgs_audio_v2_tokenizer`), loaded automatically on first use.

Requires `transformers >= 5.5`.

## Usage

```python
import torch
import torchaudio
from transformers import AutoModelForCausalLM, AutoTokenizer

repo = "multimodalart/higgs-audio-v3-tts-4b-transformers"
tokenizer = AutoTokenizer.from_pretrained(repo)
model = AutoModelForCausalLM.from_pretrained(
    repo, trust_remote_code=True, dtype=torch.bfloat16
).to("cuda").eval()

# Zero-shot TTS
wav = model.generate_speech("Hello, this is Higgs Audio running on transformers.", tokenizer)
torchaudio.save("out.wav", wav.unsqueeze(0), model.config.sample_rate)

# Voice cloning from a reference clip (+ optional transcript)
ref, sr = torchaudio.load("reference.wav")
wav = model.generate_speech(
    "Now speaking in the cloned voice.",
    tokenizer,
    reference_audio=ref,
    reference_sample_rate=sr,
    reference_text="optional transcript of the reference clip",
    temperature=0.7,
    top_p=0.95,
)
torchaudio.save("clone.wav", wav.unsqueeze(0), model.config.sample_rate)
```

`generate_speech` returns a mono 24 kHz waveform as a CPU float32 tensor `[L]`.

## Notes

- Generation uses Higgs' delay pattern across 8 codebooks (vocab 1026, incl.
  BOC/EOC specials); de-delay + decode are handled internally.
- The codec runs in fp32 (decode is unstable in bf16); the LM backbone runs in
  the dtype you load it in (bf16 recommended).
- License: research/non-commercial, inherited from the upstream checkpoint — see
  [`LICENSE`](LICENSE).
