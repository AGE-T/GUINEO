# SpeechStudio Friendly UX Parameter Audit (v2 — Corrected)

- **Status**: ANALYSIS ONLY — No code changes
- **Date**: 2025-01
- **Scope**: Full parameter inventory + Friendly abstraction layer analysis
- **Source of truth**: Current project ZIP + HIGGS V3 official references + original Friendly screenshot (as UX reference, NOT technical spec)

> **CORRECTION FROM v1**: The original Friendly screenshot is a VISUAL
> AND UX REFERENCE. It is NOT a literal HIGGS parameter specification.
> Friendly controls do NOT map 1:1 to HIGGS tokens. Friendly is an
> ABSTRACTION LAYER: USER INTENT → FRIENDLY CONTROL → one or more
> existing Advanced/semantic values → HIGGS V3 compilation.
>
> This audit treats every Friendly label as a high-level user-facing
> concept that must be analysed for its best underlying existing state.
> No HIGGS tokens are invented to match the Friendly UI.

---

## 0. ARCHITECTURE MODEL

```
USER INTENT (what the user wants to achieve)
    ↓
FRIENDLY CONTROL (high-level UI abstraction)
    ↓
ADVANCED STATE (one or more existing engine parameters)
    ↓
HIGGS V3 COMPILATION (CanonicalPromptCompiler → PromptBuilder)
    ↓
MODEL INPUT (final prompt string)
```

**Key principle**: A Friendly control may map to ONE OR MORE Advanced
parameters. It does NOT require a single corresponding HIGGS token.
"Whisper" can map to `style=Whispering`. "Tense" can map to a
combination of emotion + prosody. "Calm" can map to emotion + speed +
delivery.

---

## 1. COMPLETE PARAMETER INVENTORY

### 1.1 HIGGS-Native Controls (prompt tokens)

These are the ONLY valid HIGGS V3 tokens. The official catalogue
(`spec/Higgs_Audio_V3_Reference.md`) defines:

| Category | Valid HIGGS V3 tokens | Count | Notes |
|----------|----------------------|-------|-------|
| Emotion | elation, amusement, enthusiasm, determination, pride, contentment, affection, relief, contemplation, confusion, surprise, awe, longing, arousal, anger, fear, disgust, bitterness, sadness, shame, helplessness | 21 | |
| Style | singing, whispering, shouting | 3 | |
| Speed | speed_very_slow, speed_slow, speed_fast, speed_very_fast | 4 | **NO speed_normal** — "Normal" = no token |
| Pitch | pitch_low, pitch_high | 2 | **NO pitch_normal** — "Normal" = no token |
| Delivery | expressive_high, expressive_low | 2 | **NO expressive_normal** — "Normal" = no token |
| Pause | pause, long_pause | 2 | Inline |
| SFX | cough, laughter, crying, screaming, burping, humming, sigh, sniff, sneeze | 9 | Inline |

**Total: 43 valid HIGGS V3 tokens.** No other tokens may be emitted.

### 1.2 SpeechStudio Application Controls (semantic state)

These are application-level state variables that the CanonicalPromptCompiler
resolves into HIGGS tokens:

| # | Name | Internal Field | Type | Range | Default | Block-Scoped? | Global? | Emits HIGGS Token? |
|---|------|----------------|------|-------|---------|----------------|---------|-------------------|
| 1 | Emotion | `MainWindow._emotion` / `PromptBlock.emotion` | Optional[str] | 21 names | None | YES | YES | YES (one `<\|emotion:tag\|>`) |
| 2 | Style | `MainWindow._style` / `PromptBlock.style` | Optional[str] | 3 names | None | YES | YES | YES (one `<\|style:tag\|>`) |
| 3 | Speed | `MainWindow._speed` / `PromptBlock.speed` | str | 5 names (incl. Normal) | "Normal" | YES | YES | YES (skipped if Normal) |
| 4 | Pitch | `MainWindow._pitch` / `PromptBlock.pitch` | str | 3 names (incl. Normal) | "Normal" | YES | YES | YES (skipped if Normal) |
| 5 | Delivery | `MainWindow._delivery` / `PromptBlock.delivery` | str | 3 names (incl. Normal) | "Normal" | YES | YES | YES (skipped if Normal) |
| 6 | SFX | `PromptBlock.sfx_insertions` | List[SfxInsertion] | 9 types | Empty | YES | NO | YES (inline `<\|sfx:tag\|>onomatopoeia`) |
| 7 | Pause | `PromptBlock.pause_insertions` | List[PauseInsertion] | 2 types | Empty | YES | NO | YES (inline `<\|prosody:pause\|>`) |

### 1.3 Generation Sampling Controls (model request)

| # | Name | Internal Field | Type | Range | Default | Received by model? |
|---|------|----------------|------|-------|---------|-------------------|
| 8 | Temperature | `GenerationParameters.temperature` | float | 0.1–2.0 | 1.3 | YES (passed directly) |
| 9 | Top P | `GenerationParameters.top_p` | float | 0.1–1.0 | 0.95 | YES |
| 10 | Top K | `GenerationParameters.top_k` | int | 1–500 | 300 | YES |
| 11 | Max New Tokens | `GenerationParameters.max_new_tokens` | int | 128–8192 | 4096 | YES |
| 12 | Seed | `GenerationParameters.seed` | Optional[int] | Any int | None | NO (torch.manual_seed only) |
| 13 | Append Silence | `GenerationParameters.append_silence` | float | 0.0–5.0 | 0.5 | NO (post-generation) |
| 14 | Normalize | `GenerationParameters.normalize_output` | bool | true/false | False | NO (post-generation) |
| 15 | Auto Play | `GenerationParameters.auto_play` | bool | true/false | True | NO (UI behavior) |
| 16 | Output Format | `GenerationParameters.output_format` | str | "wav" only | "wav" | NO |

### 1.4 Voice / Speaker Controls

| # | Name | Internal Field | Type | Notes |
|---|------|----------------|------|-------|
| 17 | Voice ID | `MainWindow._voice_id` / `BatchJob.voice_id` | Optional[str] | Primary voice selector |
| 18 | Reference Audio | `VoiceProfile.reference_audio_path` | str | Managed via Voice Library |
| 19 | Reference Transcript | `VoiceProfile.reference_transcript` | str | Managed via Voice Library |
| 20 | Speaker | `BatchJob.speaker` | Optional[str] | Dialogue only |

### 1.5 Display-Only Elements (from original screenshot — NOT parameters)

| # | Name | In screenshot? | Engine parameter? | Assessment |
|---|------|---------------|-------------------|------------|
| D1 | Speaker metadata "Male, 40s, Calm" | YES | NO | Display-only — would need new VoiceProfile fields |
| D2 | Estimated time footer | YES | NO | Display-only — UI calculation |
| D3 | Inline Controls Help | YES | N/A | Display-only — documentation text |

### 1.6 Unsupported Legacy Reference Elements (NOT in HIGGS V3)

| # | Name | In screenshot? | Engine parameter? | Assessment |
|---|------|---------------|-------------------|------------|
| U1 | Stability (0.75) | YES | **NO** — HIGGS V3 has no stability parameter | ElevenLabs concept. NOT implementable. **REMOVE.** |
| U2 | Similarity (0.80) | YES | **NO** — HIGGS V3 has no similarity parameter | ElevenLabs concept. NOT implementable. **REMOVE.** |
| U3 | Strength (70%) | YES | **NO** — HIGGS V3 has no strength parameter | UI-only placeholder in current code. **REMOVE** or map to Delivery (see §9). |
| U4 | Continuous Speed slider (1.00x) | YES | NO (HIGGS supports 4 discrete tokens) | Would need quantization. See §9. |

---

## 2. THE MAPPING LAYER: USER INTENT → FRIENDLY CONTROL → ADVANCED STATE

This is the core of the Friendly abstraction. Each Friendly control
maps to one or more existing Advanced parameters. The mapping must be:
- **Explainable**: the user can understand why selecting "X" changes
  parameters A, B, C.
- **Stable**: the same Friendly selection always produces the same
  Advanced state.
- **Reversible**: if Advanced parameters are changed manually, the
  Friendly control can detect whether the combination still matches
  a Friendly preset (and if not, show "Custom").

### 2.1 Friendly Emotion/Character Controls

| Friendly Control | User Intent | HIGGS Emotion? | HIGGS Style? | Speed? | Pitch? | Delivery? | Confidence | Risk | Rationale |
|-----------------|-------------|----------------|--------------|--------|--------|-----------|------------|------|-----------|
| **Neutral** | No emotion override — neutral narration | None (no token) | None | Normal | Normal | Normal | HIGH | LOW | Default state. No tokens emitted. |
| **Happy** | Joyful, positive | Elation | — | — | — | — | HIGH | LOW | Direct 1:1 mapping. Elation = joyful excitement. |
| **Sad** | Sorrowful, melancholic | Sadness | — | — | — | — | HIGH | LOW | Direct 1:1 mapping. |
| **Angry** | Frustrated, furious | Anger | — | — | — | — | HIGH | LOW | Direct 1:1 mapping. |
| **Calm** | Peaceful, relaxed, steady | Contentment | — | Slow (candidate) | — | Expressive Low (candidate) | MEDIUM | MEDIUM | See §5 below — needs prosody analysis. |
| **Tense** | Anxious, suspenseful, on edge | **UNDECIDED** — see §3 | — | — | — | — | LOW | HIGH | No single HIGGS emotion matches "tense" well. Needs multi-param analysis. |
| **Whisper** | Quiet, intimate, hushed | None | **Whispering** | — | — | — | HIGH | LOW | Direct mapping to Style. See §4. |
| **Excited** | Energetic, eager, enthusiastic | Enthusiasm | — | Fast (candidate) | High (candidate) | Expressive High (candidate) | MEDIUM | MEDIUM | See §6 below — needs prosody analysis. |

### 2.2 Friendly Generation Controls

| Friendly Control | User Intent | Underlying Parameter | Confidence | Risk | Rationale |
|-----------------|-------------|---------------------|------------|------|-----------|
| **Creativity** (candidate) | How wild/predictable the output is | Temperature (+ possibly Top K, Top P) | MEDIUM | MEDIUM | See §12 — grouping analysis needed. |
| **Seed** | Reproducibility | Seed | HIGH | LOW | Direct 1:1 mapping. |

---

## 3. TENSE — DETAILED ANALYSIS

**User intent**: "Tense" means anxious, suspenseful, on edge, under
pressure. Think: thriller narration, tense dialogue, countdown.

### Candidate mappings:

| Candidate | HIGGS param(s) | Rationale | Confidence | Risk |
|-----------|----------------|-----------|------------|------|
| **Fear only** | `<\|emotion:fear\|>` | Fear = "anxiety, dread, apprehension" — closest single emotion | MEDIUM | Fear may be too strong (horror) vs. "tense" (suspense) |
| **Arousal only** | `<\|emotion:arousal\|>` | Arousal = "heightened intensity, alertness" — matches tension | LOW | Ambiguous name — may imply romantic arousal; poorly documented |
| **Determination only** | `<\|emotion:determination\|>` | Determination = "firm, resolute conviction" — tense focus | LOW | More positive/active than "tense" implies |
| **Fear + Slow + Expressive High** | emotion:fear + speed_slow + expressive_high | Slow pacing + high expressiveness = suspenseful tension | MEDIUM | Multi-param coupling — harder to reverse |
| **Fear + Low pitch** | emotion:fear + pitch_low | Low pitch = darker, more ominous | MEDIUM | May be too dark |
| **No mapping** | — | Mark as UNDECIDED | — | Honest about uncertainty |

### Evidence from HIGGS reference:

- `fear`: "Anxiety, dread, apprehension. Use for horror stories, scary
  scenes, warnings, danger situations, suspense."
  → The description explicitly mentions "suspense" — this is the
  closest match for "tense."

- `arousal`: "Heightened emotional intensity and alertness. Use for
  intense scenes, suspense, climax moments, thrilling content."
  → Also mentions "suspense" — but the name is ambiguous.

### Recommendation:

**Tense → UNDECIDED (lean toward Fear + optional prosody)**

The safest single-emotion mapping is **Fear** (its description
explicitly includes "suspense"). However, "tense" may benefit from
prosody coupling (Slow speed for suspense, Expressive High delivery
for intensity).

**Before implementing**, the user should:
1. Test `emotion=fear` alone and assess if it sounds "tense" or "scared."
2. Test `emotion=fear + speed=slow + delivery=expressive_high` and
   assess if the combination better matches "tense."
3. If neither is satisfactory, consider keeping Tense as UNDECIDED
   and not including it in the initial Friendly release.

**Do NOT arbitrarily map Tense → Fear without user testing.**

---

## 4. WHISPER — RESOLVED

**User intent**: Quiet, intimate, hushed speech.

### Mapping:

```
Friendly: Whisper
→ Style = Whispering
→ HIGGS token: <|style:whispering|>
```

**Confidence**: HIGH
**Risk**: LOW

### Implementation notes:

- Whisper is a **Friendly abstraction** that maps to the HIGGS Style
  `whispering`.
- It is NOT an emotion. Do not emit `<|emotion:whisper|>` (invalid token).
- In the Friendly emotion grid, "Whisper" is visually presented
  alongside emotions (matching the reference screenshot), but
  functionally it sets `style = "Whispering"` and clears the emotion
  selection (or leaves emotion as-is, depending on UX decision).
- If the user selects "Whisper" then "Happy", the Whisper style is
  cleared (mutually exclusive, since the grid is single-selection).
  A tooltip should explain: "Whisper is a voice style. Selecting an
  emotion will replace it."

### Reversibility:

If Advanced sets `style = "Whispering"`, the Friendly grid should
highlight "Whisper". If Advanced sets any other style or clears style,
"Whisper" is unhighlighted. Fully reversible.

---

## 5. CALM — DETAILED ANALYSIS

**User intent**: Peaceful, relaxed, steady, unhurried.

### Candidate mappings:

| Candidate | HIGGS param(s) | Rationale | Confidence | Risk |
|-----------|----------------|-----------|------------|------|
| **Contentment only** | `<\|emotion:contentment\|>` | Contentment = "calm satisfaction, peaceful ease" — direct match | HIGH | LOW — may lack prosody character |
| **Contentment + Slow** | emotion:contentment + speed_slow | Slow pacing reinforces calmness | MEDIUM | MEDIUM — 2-param coupling |
| **Contentment + Slow + Expressive Low** | emotion:contentment + speed_slow + expressive_low | Slow + flat delivery = very calm | MEDIUM | MEDIUM — 3-param coupling, harder to reverse |
| **Contentment + Low pitch** | emotion:contentment + pitch_low | Lower pitch = more relaxed | LOW | MEDIUM |

### Evidence from HIGGS reference:

- `contentment`: "Calm satisfaction and peaceful ease. Use for
  relaxation guides, meditation, nature narration, slow-paced content."
  → The description explicitly mentions "calm" and "slow-paced."

### Recommendation:

**Calm → Contentment (emotion only)** — **initial release**

Start with the direct 1:1 mapping (emotion=Contentment, no prosody
override). The HIGGS description already implies calm pacing.

**Future enhancement** (if user testing shows Contentment alone is
not "calm" enough):
```
Calm → emotion=Contentment + speed=Slow + delivery=Expressive Low
```

**Reversibility concern**: If Calm is a 3-param preset, then Advanced
state `emotion=Contentment, speed=Normal, delivery=Normal` would NOT
match "Calm" — the Friendly control would show "Custom." This is
acceptable as long as the user understands the preset is a starting
point, not a lock.

---

## 6. EXCITED — DETAILED ANALYSIS

**User intent**: Energetic, eager, enthusiastic, lively.

### Candidate mappings:

| Candidate | HIGGS param(s) | Rationale | Confidence | Risk |
|-----------|----------------|-----------|------------|------|
| **Enthusiasm only** | `<\|emotion:enthusiasm\|>` | Enthusiasm = "energetic excitement, eagerness" — direct match | HIGH | LOW — may lack energy without prosody |
| **Enthusiasm + Fast** | emotion:enthusiasm + speed_fast | Fast pacing reinforces excitement | MEDIUM | MEDIUM — 2-param coupling |
| **Enthusiasm + Fast + High + Expressive High** | emotion:enthusiasm + speed_fast + pitch_high + expressive_high | Full energy package | MEDIUM | HIGH — 4-param coupling, hard to reverse |
| **Enthusiasm + High pitch** | emotion:enthusiasm + pitch_high | Higher pitch = more excited | LOW | MEDIUM |

### Evidence from HIGGS reference:

- `enthusiasm`: "Energetic excitement and eagerness. Use for
  motivational speeches, product launches, sports commentary, calls
  to action."
  → The description implies high energy but doesn't mandate specific
  prosody.

### Recommendation:

**Excited → Enthusiasm (emotion only)** — **initial release**

Start with the direct 1:1 mapping. The HIGGS Enthusiasm emotion
already implies energy.

**Future enhancement** (if user testing shows Enthusiasm alone is
not "excited" enough):
```
Excited → emotion=Enthusiasm + speed=Fast + pitch=High + delivery=Expressive High
```

**Reversibility concern**: Same as Calm — a multi-param preset creates
a "Custom" state when any single param is changed.

---

## 7. HIGGS V3 MODEL TRUTH

### Official V3 catalogue (from `spec/Higgs_Audio_V3_Reference.md`):

- **21 emotions** (elation, amusement, enthusiasm, determination, pride,
  contentment, affection, relief, contemplation, confusion, surprise,
  awe, longing, arousal, anger, fear, disgust, bitterness, sadness,
  shame, helplessness)
- **3 styles** (singing, whispering, shouting)
- **4 non-normal speed values** (speed_very_slow, speed_slow,
  speed_fast, speed_very_fast)
- **2 pitch values** (pitch_low, pitch_high)
- **2 expressive values** (expressive_high, expressive_low)
- **2 pauses** (pause, long_pause)
- **9 SFX** (cough, laughter, crying, screaming, burping, humming,
  sigh, sniff, sneeze)

### What does NOT exist in HIGGS V3:

- `expressive_normal` — **invalid** (Normal = no token)
- `speed_normal` — **invalid** (Normal = no token)
- `pitch_normal` — **invalid** (Normal = no token)
- `emotion:tense` — **invalid** (not in the 21 emotions)
- `emotion:whisper` — **invalid** (Whisper is a style, not an emotion)
- `emotion:calm` — **invalid** (not in the 21 emotions; closest is contentment)
- `style:normal` — **invalid** (Normal = no style token)

### Application-level "Normal" semantics:

In SpeechStudio, "Normal" is an application state that means:
**NO HIGGS TOKEN EMITTED for that category.**

This is enforced by:
- `PromptBuilder.build()` — checks `!= "Normal"` before emitting
- `CanonicalPromptCompiler.compile_continuous()` — skips "Normal" in
  emission optimizer (BUG-001 fix)

**Do not invent model tokens to match the Friendly UI.** Friendly
labels like "Tense," "Whisper," "Calm" are user-intent abstractions,
not HIGGS token names.

---

## 8. SPEED — DISCRETE VS CONTINUOUS

### Original reference:
Shows Speed as a continuous slider (value "1.00x").

### HIGGS V3 truth:
Supports only 4 discrete speed tokens (speed_very_slow, speed_slow,
speed_fast, speed_very_fast) plus "Normal" (no token).

### Options:

| Option | UX | Implementation | Reversible? | Risk |
|--------|-----|----------------|-------------|------|
| **A: Discrete 5-state** | 5 buttons (V.Slow / Slow / Normal / Fast / V.Fast) | Direct 1:1 to HIGGS tokens | YES | LOW |
| **B: Continuous snapping slider** | Slider that snaps to 5 positions | Slider value quantized to nearest token | YES | MEDIUM |
| **C: Continuous slider (no snap)** | Free-range slider | Would need to emit non-standard speed values | NO | HIGH (invalid tokens) |

### Recommendation:

**Option B: Continuous snapping slider** — matches the original
reference's visual style while respecting HIGGS V3's discrete tokens.

**Implementation**:
- Slider range: 0.0–1.0 (visual)
- Snap positions: 0.1 (V.Slow), 0.3 (Slow), 0.5 (Normal), 0.7 (Fast), 0.9 (V.Fast)
- Display: "0.65x", "0.85x", "1.0x", "1.2x", "1.4x"
- Underlying value: the HIGGS token name (speed_very_slow, etc.)

**Reversibility**: The slider snaps to discrete values, so Advanced
state always maps to a slider position. Fully reversible.

**Alternative**: If snapping is too complex, use **Option A** (5
discrete buttons) — simpler, same underlying mapping.

---

## 9. STRENGTH / STABILITY / SIMILARITY — UNSUPPORTED

### Assessment:

| Control | In screenshot? | HIGGS V3 parameter? | ElevenLabs concept? | Recommendation |
|---------|---------------|---------------------|---------------------|----------------|
| Strength | YES | NO | YES (controls voice clone strength) | **REMOVE** — no engine mapping |
| Stability | YES | NO | YES (controls voice consistency) | **REMOVE** — no engine mapping |
| Similarity | YES | NO | YES (controls reference similarity) | **REMOVE** — no engine mapping |

### Rationale:

These three controls come from ElevenLabs-style TTS engines. The HIGGS
V3 model does NOT have stability, similarity, or strength parameters.
The generation parameters are: temperature, top_p, top_k, max_new_tokens,
seed.

**Do NOT implement these as functional controls.** They would be
UI-only placeholders with no effect — misleading to the user.

### Possible future mappings (NOT for initial release):

If the user insists on keeping a "Strength" concept:
- Strength → Delivery (Low↔High): "Strong" = Expressive High, "Weak" = Expressive Low
- Stability → Temperature (inverted): "High stability" = low temperature
- Similarity → (no mapping — voice similarity is controlled by reference audio)

**Do NOT implement these mappings without user testing.** They are
approximate and may not produce the expected behavior.

---

## 10. ALLOW SFX — APPLICATION POLICY CONTROL

### Original reference:
Shows an "Allow SFX" toggle switch (ON position).

### Current engine behavior:

The PromptBuilder converts `{sfx:Laughter:Haha}` markers in the text
to `<|sfx:laughter|>Haha` tokens. There is NO global on/off switch —
if the marker is in the text, the token is emitted.

### Proposed behavior:

```
Allow SFX ON  → SFX markers in text are converted to HIGGS tokens (current behavior)
Allow SFX OFF → SFX markers in text are STRIPPED (no tokens emitted)
```

### Implementation requirement:

- New field: `GenerationParameters.allow_sfx: bool = True`
- PromptBuilder checks `allow_sfx` before converting `{sfx:...}` markers
- If `allow_sfx = False`, the `_SFX_MARKER_RE.sub()` call is skipped
  (markers remain as literal text or are stripped)

### Assessment:

- **Type**: Application-level policy control (NOT a HIGGS token)
- **Scope**: Global (applies to the entire generation)
- **Reversible**: YES — toggle on/off, markers are preserved in text
- **Risk**: LOW

### Recommendation:

**ADD** the Allow SFX toggle. It is a useful application-level control
with clear semantics. The toggle does NOT modify the text — it only
controls whether the PromptBuilder converts SFX markers to tokens.

**Open question**: When Allow SFX is OFF, should the `{sfx:...}`
markers be:
(a) Left as literal text (user sees `{sfx:Laughter:Haha}` in output)? — confusing
(b) Stripped from the text entirely? — cleaner but modifies user text
(c) Replaced with just the onomatopoeia (`Haha`)? — best UX

**Recommendation**: Option (c) — replace `{sfx:Laughter:Haha}` with
just `Haha` (the onomatopoeia) when Allow SFX is OFF. This preserves
the spoken content without the SFX token.

---

## 11. INLINE CONTROLS HELP — USE ACTUAL HIGGS V3 SYNTAX

### Original reference shows:
```
[sfx: rain] eso hangja
[pause: 1.5s] rövid szünet
[whisper] suttogas [/whisper]
```

### Problem:
This syntax does NOT match the current implementation. The current
PromptBuilder uses:
- `{sfx:Laughter:Haha}` (curly braces, name:onomatopoeia)
- `{pause}` / `{long_pause}` (curly braces, no duration)
- `<|style:whispering|>` (HIGGS token, no bracketed inline switching)

### Recommendation:

**Use the ACTUAL current HIGGS V3 syntax** for any help text. Do NOT
copy the screenshot's older syntax.

### Proposed Inline Controls Help content:

```
INLINE CONTROLS HELP

SFX (insert in text):
  {sfx:Laughter:Haha}    → <|sfx:laughter|>Haha
  {sfx:Cough:Ahem}       → <|sfx:cough|>Ahem

Pause (insert in text):
  {pause}                → <|prosody:pause|> (short, ~400-700ms)
  {long_pause}           → <|prosody:long_pause|> (long, ~700-1500ms)

HIGGS tokens (Raw Mode):
  <|emotion:fear|>       → sets emotion for the sentence
  <|style:whispering|>   → sets style for the sentence
  <|prosody:speed_slow|> → sets speed for the sentence
  <|prosody:pitch_high|> → sets pitch for the sentence
  <|prosody:expressive_high|> → sets delivery for the sentence
```

### Notes:

- The `[whisper]...[/whisper]` inline style switching from the
  screenshot is **NOT currently supported**. The current Style setting
  applies to the entire generation. Implementing inline style switching
  would require PromptBuilder changes (separate feature request).
- The `[pause: 1.5s]` duration syntax is **NOT supported**. HIGGS V3
  has only `pause` and `long_pause` — no custom durations.

---

## 12. GENERATION PARAMETERS — TEMPERATURE / TOP K / TOP P / SEED

### Current wiring (verified from generation_manager.py):

| Parameter | Passed to model? | Via | Default | Recommended range |
|-----------|-----------------|-----|---------|-------------------|
| Temperature | YES | `generate()` kwargs | 1.3 | 1.0–1.4 (HIGGS reference) |
| Top P | YES | `generate()` kwargs | 0.95 | 0.9–1.0 |
| Top K | YES | `generate()` kwargs | 300 | 50–300 |
| Max New Tokens | YES | `generate()` kwargs | 4096 | 2048–8192 |
| Seed | NO | `torch.manual_seed()` | None | Any int |

### Grouping analysis: Can Temperature + Top K + Top P form "Creativity"?

All three control sampling "randomness" / "variation." However:

- **Temperature** scales the probability distribution before sampling.
  Higher = more random.
- **Top K** limits to the K most likely tokens. Higher = more candidates.
- **Top P** limits to the nucleus (cumulative probability). Higher = more candidates.

They interact **non-linearly**. A "Creativity" slider would need a
formula that maps a single 0–100 value to three parameters.

### Candidate "Creativity" mappings:

| Creativity level | Temperature | Top K | Top P | Rationale |
|-----------------|-------------|-------|-------|-----------|
| 0 (Deterministic) | 0.3 | 50 | 0.5 | Very conservative |
| 25 (Conservative) | 0.7 | 100 | 0.8 | Mild variation |
| 50 (Balanced) | 1.0 | 200 | 0.9 | Default-ish |
| 75 (Expressive) | 1.3 | 300 | 0.95 | HIGGS recommended |
| 100 (Wild) | 1.6 | 500 | 1.0 | Maximum variation |

### Risk assessment:

| Risk | Description | Mitigation |
|------|-------------|------------|
| **Hides useful combinations** | User may want low temp + high top_k (controlled diversity) | Expose Advanced tab for fine-tuning |
| **Non-linear interaction** | The three params don't scale linearly | Use a lookup table, not a formula |
| **Not reversible** | If Advanced sets temp=0.5, top_k=500, what is "Creativity"? | Show "Custom" when combination doesn't match a preset |

### Reversibility:

If Creativity is a 5-level preset (0/25/50/75/100), then:
- Advanced state matching a preset → Friendly shows that level
- Advanced state NOT matching any preset → Friendly shows "Custom"

This is **reversible** because the Friendly control can detect whether
the current Advanced combination matches a known preset.

### Recommendation:

**Creativity = GROUPED (Temperature + Top K + Top P)** — but only if:

1. The mapping uses a **lookup table** (5 discrete levels), not a formula.
2. The Friendly control shows "Custom" when Advanced state doesn't match.
3. Advanced tab remains fully accessible for manual fine-tuning.
4. The user is informed (tooltip) that Creativity sets 3 parameters.

**If the user does not want grouping**: expose Temperature as a direct
"Creativity" slider (0.1–2.0), leave Top K and Top P in Advanced.

### Seed:

**Seed = DIRECT** (no grouping). Seed controls reproducibility, not
sampling style. It is a fundamentally different parameter.

---

## 13. FINAL AUDIT — 6 CATEGORIES

### Category 1: HIGGS-Native Controls (prompt tokens)

These are the only valid HIGGS V3 tokens. Friendly controls map TO these,
but these are not exposed directly in Friendly.

| Token category | Count | Examples |
|---------------|-------|----------|
| Emotion | 21 | elation, fear, anger, sadness, contentment, enthusiasm, ... |
| Style | 3 | singing, whispering, shouting |
| Speed | 4 | speed_very_slow, speed_slow, speed_fast, speed_very_fast |
| Pitch | 2 | pitch_low, pitch_high |
| Delivery | 2 | expressive_high, expressive_low |
| Pause | 2 | pause, long_pause |
| SFX | 9 | cough, laughter, crying, ... |

### Category 2: SpeechStudio Application Controls (semantic state)

These are the application-level state variables that the compiler
resolves into HIGGS tokens. These are what Advanced exposes.

| Control | Type | Emits token? |
|---------|------|--------------|
| Emotion | Optional[str] (21 names) | YES |
| Style | Optional[str] (3 names) | YES |
| Speed | str (5 names, Normal=no token) | YES (if not Normal) |
| Pitch | str (3 names, Normal=no token) | YES (if not Normal) |
| Delivery | str (3 names, Normal=no token) | YES (if not Normal) |
| SFX insertions | List[SfxInsertion] | YES (inline) |
| Pause insertions | List[PauseInsertion] | YES (inline) |
| Allow SFX (proposed) | bool | NO (policy control) |

### Category 3: Friendly User-Intent Abstractions

These are the high-level Friendly controls. Each maps to one or more
Category 2 controls.

| Friendly Control | Underlying Category 2 state | Mapping type | Confidence |
|-----------------|---------------------------|--------------|------------|
| Neutral | Emotion=None, Style=None, Speed=Normal, Pitch=Normal, Delivery=Normal | PRESET (all defaults) | HIGH |
| Happy | Emotion=Elation | DIRECT (1:1) | HIGH |
| Sad | Emotion=Sadness | DIRECT (1:1) | HIGH |
| Angry | Emotion=Anger | DIRECT (1:1) | HIGH |
| Calm | Emotion=Contentment | DIRECT (1:1, future: PRESET) | HIGH (initial) |
| Tense | UNDECIDED — see §3 | — | LOW |
| Whisper | Style=Whispering | DIRECT (1:1, cross-category) | HIGH |
| Excited | Emotion=Enthusiasm | DIRECT (1:1, future: PRESET) | HIGH (initial) |
| Selected Speaker | Voice ID | DIRECT (1:1) | HIGH |
| Custom Style dropdown | Style (all 3) | DIRECT (1:1) | HIGH |
| Speed (snapping slider) | Speed (5 values) | DIRECT (1:1) | HIGH |
| Pitch | Pitch (3 values) | DIRECT (1:1) | HIGH |
| Delivery | Delivery (3 values) | DIRECT (1:1) | HIGH |
| Creativity (candidate) | Temperature + Top K + Top P | GROUPED (lookup table) | MEDIUM |
| Seed | Seed | DIRECT (1:1) | HIGH |
| Sound Effects | SFX insertions | DIRECT (action) | HIGH |
| Pause | Pause insertions | DIRECT (action) | HIGH |
| Allow SFX | allow_sfx (proposed) | DIRECT (1:1) | HIGH |

### Category 4: Generation Sampling Controls

| Control | Friendly? | Advanced? | Notes |
|---------|-----------|-----------|-------|
| Temperature | YES (as Creativity or direct) | YES | Primary sampling param |
| Top P | HIDDEN (if Creativity groups it) / ADVANCED | YES | Niche |
| Top K | HIDDEN (if Creativity groups it) / ADVANCED | YES | Niche |
| Max New Tokens | ADVANCED ONLY | YES | Technical |
| Seed | YES (direct) | YES | Reproducibility |
| Append Silence | ADVANCED ONLY | YES | Post-generation |
| Normalize | ADVANCED ONLY | YES | Post-generation |
| Auto Play | HIDDEN | YES | UI behavior |
| Output Format | HIDDEN | NO | Only "wav" |

### Category 5: Display-Only Elements

| Element | Engine parameter? | Recommendation |
|---------|-------------------|----------------|
| Speaker metadata ("Male, 40s, Calm") | NO | ADD (new VoiceProfile fields, display-only) |
| Estimated time footer | NO | ADD (UI calculation: text_length / 15 * RTF) |
| Inline Controls Help | N/A | ADD (document actual HIGGS V3 syntax) |

### Category 6: Unsupported Legacy Reference Elements

| Element | In screenshot? | HIGGS V3? | Recommendation |
|---------|---------------|-----------|----------------|
| Stability | YES | NO | **REMOVE** — ElevenLabs concept, not in HIGGS |
| Similarity | YES | NO | **REMOVE** — ElevenLabs concept, not in HIGGS |
| Strength | YES | NO | **REMOVE** — no engine mapping (or map to Delivery with user approval) |
| Continuous Speed (no snap) | YES | NO | **REPLACE** with discrete 5-state or snapping slider |
| `[sfx: rain]` syntax | YES | NO | **REPLACE** with actual `{sfx:Laughter:Haha}` syntax |
| `[pause: 1.5s]` syntax | YES | NO | **REPLACE** with actual `{pause}` / `{long_pause}` syntax |
| `[whisper]...[/whisper]` syntax | YES | NO | **DO NOT IMPLEMENT** (inline style switching not supported) |

---

## 14. FINAL DECISION TABLE

| Friendly Control | Underlying Parameters | Mapping Type | Confidence | Risk | Recommendation |
|-----------------|----------------------|--------------|------------|------|----------------|
| Selected Speaker | Voice ID | DIRECT | HIGH | LOW | **KEEP** |
| Speaker metadata | (new VoiceProfile fields) | DISPLAY | HIGH | LOW | **ADD** |
| Neutral | Emotion=None (defaults) | PRESET | HIGH | LOW | **KEEP** |
| Happy | Emotion=Elation | DIRECT | HIGH | LOW | **KEEP** |
| Sad | Emotion=Sadness | DIRECT | HIGH | LOW | **KEEP** |
| Angry | Emotion=Anger | DIRECT | HIGH | LOW | **KEEP** |
| Calm | Emotion=Contentment | DIRECT | HIGH | LOW | **KEEP** (future: add prosody) |
| Tense | UNDECIDED | — | LOW | HIGH | **UNDECIDED** — needs user testing |
| Whisper | Style=Whispering | DIRECT (cross-cat) | HIGH | LOW | **KEEP** |
| Excited | Emotion=Enthusiasm | DIRECT | HIGH | LOW | **KEEP** (future: add prosody) |
| "More emotions" | Emotion (remaining 13) | DIRECT | HIGH | LOW | **KEEP** |
| Custom Style | Style (3 values) | DIRECT | HIGH | LOW | **KEEP** |
| Speed (snapping) | Speed (5 values) | DIRECT | HIGH | MEDIUM | **KEEP** (snapping slider or 5 buttons) |
| Pitch | Pitch (3 values) | DIRECT | HIGH | LOW | **KEEP** |
| Delivery | Delivery (3 values) | DIRECT | HIGH | LOW | **KEEP** |
| Creativity | Temp + Top K + Top P | GROUPED | MEDIUM | MEDIUM | **CONDITIONAL** — only with lookup table + "Custom" state |
| Seed | Seed | DIRECT | HIGH | LOW | **KEEP** |
| Sound Effects | SFX insertions | DIRECT | HIGH | LOW | **KEEP** |
| Pause | Pause insertions | DIRECT | HIGH | LOW | **KEEP** |
| Allow SFX | allow_sfx (new) | DIRECT | HIGH | LOW | **ADD** |
| Inline Controls Help | (display) | DISPLAY | HIGH | LOW | **ADD** (actual syntax) |
| Estimated Time | (display) | DISPLAY | HIGH | LOW | **ADD** |
| Strength | (none) | — | — | HIGH | **REMOVE** (or map to Delivery) |
| Stability | (none) | — | — | HIGH | **REMOVE** (not in HIGGS) |
| Similarity | (none) | — | — | HIGH | **REMOVE** (not in HIGGS) |
| Top P | (if not grouped) | — | — | — | **ADVANCED ONLY** |
| Top K | (if not grouped) | — | — | — | **ADVANCED ONLY** |
| Max New Tokens | — | — | — | — | **ADVANCED ONLY** |
| Append Silence | — | — | — | — | **ADVANCED ONLY** |
| Normalize | — | — | — | — | **ADVANCED ONLY** |
| Auto Play | — | — | — | — | **HIDDEN** |
| Output Format | — | — | — | — | **HIDDEN** |

---

## 15. REVERSIBILITY + MIXED STATE HANDLING

### For DIRECT (1:1) mappings:

Fully reversible. Advanced state determines Friendly state, and vice versa.

### For GROUPED mappings (Creativity, future Calm/Excited presets):

| Situation | Friendly shows | Advanced shows |
|-----------|---------------|----------------|
| User selects Friendly preset | Preset name | Underlying values set by preset |
| User changes one Advanced param | **"Custom"** | The changed value |
| User selects Friendly preset again | Preset name | Underlying values reset to preset |

**"Custom" is the mixed-state label.** It means the current Advanced
combination does not match any Friendly preset.

### Rule:

There is ONE source of truth: the Advanced state variables
(`MainWindow._emotion`, `._style`, `._speed`, etc.).

- Friendly controls WRITE to these variables (Friendly → Advanced).
- Friendly controls READ from these variables (Advanced → Friendly).
- When reading, if the combination doesn't match a preset, show "Custom."

---

## 16. RECOMMENDED IMPLEMENTATION ORDER

1. **Resolve Tense** (§3) — user testing needed; mark UNDECIDED for now
2. **Remove Strength/Stability/Similarity** (§9) — no engine mapping
3. **Wire modern_control_panel.py** into runtime
4. **Implement Friendly emotion grid** (8 controls: Neutral/Happy/Sad/Angry/Calm/Tense/Whisper/Excited)
   - Whisper maps to Style=Whispering (not emotion)
   - Tense marked UNDECIDED (use Fear as placeholder, or omit)
5. **Add VoiceProfile metadata** (gender, age_range, character) for speaker card
6. **Implement Speed as snapping slider** (§8) or keep 5 discrete buttons
7. **Add Allow SFX toggle** (§10) — new allow_sfx parameter
8. **Add Creativity control** (§12) — only if lookup table + "Custom" state is acceptable
9. **Add Seed** to GENERATION tab
10. **Add Inline Controls Help** (§11) — actual HIGGS V3 syntax
11. **Add Estimated Time footer** (display-only)
12. **Verify two-way sync** (Friendly ↔ Advanced)
13. (Future) Add prosody presets for Calm/Excited if user testing validates

---

## 17. AMBIGUITIES REQUIRING USER DECISION

1. **Tense mapping** — Fear? Arousal? Fear+prosody? Or omit? (§3)
2. **Creativity grouping** — group Temp+TopK+TopP or expose Temperature only? (§12)
3. **Speed control type** — snapping slider or 5 discrete buttons? (§8)
4. **Allow SFX OFF behavior** — strip markers, keep literal, or replace with onomatopoeia? (§10)
5. **Whisper + Emotion coexistence** — can user select Whisper AND Happy? Or mutually exclusive? (§4)
6. **Calm/Excited prosody** — start with emotion-only, or include prosody presets from day 1? (§5, §6)
7. **Speaker metadata fields** — what fields to add to VoiceProfile? (gender, age, character, language?)
8. **"More emotions" UX** — inline expand or popup dialog?

---

## CONCLUSION

The Friendly UX is an **abstraction layer** over the existing Advanced
parameters. It does NOT require new HIGGS tokens. Each Friendly control
maps to one or more existing Application Controls (Category 2), which
the CanonicalPromptCompiler resolves into valid HIGGS V3 tokens.

**Key corrections from v1:**
- "Whisper" maps to Style=Whispering (not an emotion token)
- "Tense" is UNDECIDED (not blindly mapped to Fear)
- "Calm" starts as Emotion=Contentment (future: add prosody)
- "Excited" starts as Emotion=Enthusiasm (future: add prosody)
- Stability/Similarity/Strength are REMOVED (not in HIGGS V3)
- Inline help uses actual `{sfx:...}` / `{pause}` syntax (not screenshot's `[...]` syntax)
- Speed can be a snapping slider (not continuous, not discarded)
- Creativity can group Temp+TopK+TopP (with lookup table + "Custom" state)

**No code was modified. No UI was changed. No compiler was touched.**
This audit is the implementation specification for the next Friendly
UI development round.
