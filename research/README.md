# SpeechStudio - Research & References

> **Note:** the user-facing product name is **GUINEO** (since the P3.37
> rebrand). This folder is an **internal technical** archive and keeps the
> historical SpeechStudio terminology. For the user guide see
> [`../README.md`](../README.md); for third-party licences see
> [`../THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md).

This directory holds every external reference used by SpeechStudio, plus a
summary of the verified findings that influence the implementation.

Authoritative source priority is defined in `../spec/REFERENCES.md`:

1. Official BosonAI documentation
2. Official Hugging Face model documentation
3. Official BosonAI GitHub repository
4. Official implementation examples
5. SpeechStudio verified experiments
6. SpeechStudio Deep Research documents

---

## Files in this directory

| File | Source | Purpose |
|------|--------|---------|
| `PROMPTING_official.md` | https://huggingface.co/bosonai/higgs-tts-v3-4b/raw/main/PROMPTING.md | Official prompt control-tag syntax |
| `bosonai_higgs-tts-v3-4b_README.md` | https://huggingface.co/bosonai/higgs-tts-v3-4b | Official model card |
| `multimodalart_transformers_port_README.md` | https://huggingface.co/multimodalart/higgs-audio-v3-tts-4b-transformers | The Transformers port used by SpeechStudio |
| `boson-ai_higgs-audio_github_README.md` | https://github.com/boson-ai/higgs-audio | Reference implementation repo |
| `vllm-omni_Higgs-Audio-V3-TTS_recipe.md` | https://github.com/vllm-project/vllm-omni | Serving recipe / performance reference |

---

## Verified findings (influence the implementation)

### 1. Model backend

SpeechStudio uses the **Transformers** port, not SGLang, not vLLM:

- Repo id: `multimodalart/higgs-audio-v3-tts-4b-transformers`
- Original: `bosonai/higgs-tts-v3-4b` (weights copied unchanged)
- Loading: `AutoModelForCausalLM.from_pretrained(repo, trust_remote_code=True, dtype=torch.bfloat16)`
- Tokenizer: `AutoTokenizer.from_pretrained(repo)`
- The model is a **Qwen3-4B** backbone with a fused multi-codebook audio
  embedding/head (8 codebooks x 1026 vocab, MusicGen-style delay pattern).
- Audio codec: `bosonai/higgs-audio-v2-tokenizer` (`higgs_audio_v2_tokenizer`),
  loaded automatically on first use.

### 2. Dependency requirements

- **transformers >= 5.5** (explicitly stated by the Transformers port README).
  The bootstrap therefore pins `transformers>=5.5.0` in `requirements.txt`.
- PyTorch with CUDA (bf16 recommended for the LM backbone).
- The codec decodes in fp32 (decode is unstable in bf16); the LM runs in the
  dtype the model is loaded in.

### 3. Generation API (verified signature)

```python
# Zero-shot TTS
wav = model.generate_speech(prompt_text, tokenizer)

# Voice cloning
ref, sr = torchaudio.load("reference.wav")
wav = model.generate_speech(
    prompt_text,
    tokenizer,
    reference_audio=ref,
    reference_sample_rate=sr,
    reference_text="optional transcript",
    temperature=0.7,
    top_p=0.95,
)
```

- Returns a mono **24 kHz** waveform as a CPU `float32` tensor of shape `[L]`.
- Confirmed generation parameters: `temperature`, `top_p`, plus the
  reference-voice trio (`reference_audio`, `reference_sample_rate`,
  `reference_text`).
- `top_k` and `max_new_tokens` are inherited from the underlying Qwen3 LM
  and are exposed by SpeechStudio per the Generation Engine Specification.

### 4. Prompt control tags (official PROMPTING.md)

Format: `<|category:tag|>`

Two placements:
- **Sentence-level** - emotion, style, and prosody's `speed_* / pitch_* / expressive_*`.
  Placed at the **start of the sentence**.
- **Inline** - sound effects (`sfx`) and prosody's `pause / long_pause`.
  Inserted at the **exact position** where the effect should occur.

sfx gotcha: `<|sfx:tag|>onomatopoeia, then the line` - the tag comes first,
immediately followed by the onomatopoeia with **no space** between them.

This matches `../spec/05_Prompt_System.md` exactly.

### 5. Architecture notes

- Delay pattern across 8 codebooks; de-delay and decode handled internally by
  the custom modelling code.
- The model remains resident in GPU memory; the tokenizer is loaded once.
- Both align with the Engine Specification's single-instance model lifecycle.

### 6. Performance reference (vLLM recipe, for context only)

SpeechStudio does NOT use vLLM. The recipe is kept only as a performance
reference point:
- Target hardware: 1x H100 80GB
- Output: 24 kHz mono
- Languages: 100+
- The local Transformers backend will be slower than a dedicated serving
  engine but matches the portability and "no extra framework" goals of the
  specification.
