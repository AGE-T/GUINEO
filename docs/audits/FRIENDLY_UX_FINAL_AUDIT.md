# SpeechStudio Friendly UX — Final Parameter & Interaction Audit

- **Status**: FINAL DESIGN AUDIT — READ ONLY, no code changes
- **Date**: 2025-01
- **Purpose**: Implementation specification for the next Friendly UI development round
- **Sources**: Current project ZIP + HIGGS V3 references + original Friendly screenshot (UX reference) + existing engine behavior

> **This is strictly an analysis task.** No Python, QSS, UI, PromptBuilder,
> CanonicalPromptCompiler, NarrationSplitter, HIGGS token definitions,
> or VoiceProfile models were modified.

---

## 1. CORE DESIGN PHILOSOPHY

**Friendly IS**: a high-level user-intent layer over the existing
Advanced/application state.

**Friendly is NOT**: a smaller Advanced panel, a second prompt compiler,
a separate semantic state system, or a direct copy of HIGGS tokens.

### Architecture:

```
USER INTENT
→ FRIENDLY CONTROL (high-level abstraction)
→ one or more existing Advanced / application values
→ existing semantic state (MainWindow._emotion, etc.)
→ canonical compiler or generation request
→ HIGGS V3 / model
```

**Goal**: FEWER TECHNICAL DECISIONS, WITHOUT LOSING UNDERLYING POWER.

A Friendly control MAY intentionally modify multiple underlying parameters.
This is a core requirement, not an edge case.

---

## 2. OWNERSHIP MODEL — ONE SOURCE OF TRUTH

There is **one underlying source of truth**: the MainWindow state
variables (`self._emotion`, `self._style`, `self._speed`,
`self._pitch`, `self._delivery`, `self._voice_id`) and the
`GenerationParameters` dataclass.

- Friendly and Advanced are **two views over the same state**.
- Friendly MUST NOT maintain an independent hidden copy.
- Advanced MUST NOT maintain a second independent state.

### Two-way synchronisation:

| Direction | Rule |
|-----------|------|
| Friendly → Advanced | Setting a Friendly control writes to the underlying state variables |
| Advanced → Friendly | Friendly controls read the underlying state and show active/highlighted if the state matches |
| Mixed state | If Advanced state doesn't match any Friendly concept, Friendly shows "Custom" |

---

## 3. ORIGINAL REFERENCE — ELEMENT-BY-ELEMENT DECISION

| Reference Element | Decision | Why |
|------------------|----------|-----|
| Selected Speaker | **KEEP** | Primary voice selector — matches engine Voice ID |
| Speaker metadata (Male, 40s, Calm) | **DISPLAY ONLY** | VoiceProfile currently has NO such fields. If added, they are display-only metadata, not engine parameters |
| Emotion & Style (8 grid) | **KEEP + SIMPLIFY** | 8 emotions is the right count; labels need mapping analysis (§7) |
| Custom Style dropdown | **KEEP** | Compact way to expose 3 HIGGS styles + None |
| Strength slider | **REMOVE** | No engine parameter exists. UI-only placeholder is misleading |
| Advanced Controls (Stability, Similarity, Speed) | **REMOVE Stability/Similarity** (not in HIGGS V3); **SIMPLIFY Speed** (snapping slider or 5 discrete) | Stability/Similarity are ElevenLabs concepts |
| Allow SFX toggle | **KEEP** | Useful application-level policy control (new `allow_sfx` param) |
| Inline Controls Help | **REPLACE** | Use actual current syntax (`{sfx:...}`, `{pause}`, `<\|style:whispering\|>`), not screenshot's `[...]` syntax |
| Generate Selected Line | **KEEP** | Matches main CTA style |
| Estimated time footer | **DISPLAY ONLY** | UI calculation (text_length / 15 * RTF) |
| Temperature / Top K / Top P | **MERGE** into "AI Freedom" (conditional) | See §18 — grouping with lookup table + "Custom" state |
| Seed | **KEEP** | Reproducibility — fundamentally different from sampling |

---

## 4. COMPLETE CURRENT PARAMETER INVENTORY

### 4.1 HIGGS-Native Token Parameters (prompt tokens)

| # | Name | Internal Field | UI Location | Type | Range | Default | Token? | Scope | Affects Prompt? | Persisted? | Interacts? |
|---|------|----------------|-------------|------|-------|---------|--------|-------|-----------------|------------|------------|
| 1 | Emotion | `MainWindow._emotion` / `PromptBlock.emotion` | Advanced: EmotionButtons | Optional[str] | 21 names | None | YES `<\|emotion:tag\|>` | Block + Global | YES | YES (presets) | NO (single) |
| 2 | Style | `MainWindow._style` / `PromptBlock.style` | Advanced: StyleButtons | Optional[str] | 3 names | None | YES `<\|style:tag\|>` | Block + Global | YES | YES | NO |
| 3 | Speed | `MainWindow._speed` / `PromptBlock.speed` | Advanced: ProsodySection | str | 5 names | "Normal" | YES (if ≠Normal) | Block + Global | YES | YES | YES (prosody trio) |
| 4 | Pitch | `MainWindow._pitch` / `PromptBlock.pitch` | Advanced: ProsodySection | str | 3 names | "Normal" | YES (if ≠Normal) | Block + Global | YES | YES | YES |
| 5 | Delivery | `MainWindow._delivery` / `PromptBlock.delivery` | Advanced: ProsodySection | str | 3 names | "Normal" | YES (if ≠Normal) | Block + Global | YES | YES | YES |
| 6 | SFX | `PromptBlock.sfx_insertions` | Advanced: SfxSection | List[SfxInsertion] | 9 types | Empty | YES (inline) | Block-scoped | YES | NO | NO |
| 7 | Pause | `PromptBlock.pause_insertions` | Advanced: ProsodySection | List[PauseInsertion] | 2 types | Empty | YES (inline) | Block-scoped | YES | NO | NO |

### 4.2 Generation / Sampling Parameters (model request)

| # | Name | Internal Field | UI Location | Type | Range | Default | Model Receives? | Scope | Persisted? | Interacts? |
|---|------|----------------|-------------|------|-------|---------|-----------------|-------|------------|------------|
| 8 | Temperature | `GenerationParameters.temperature` | Advanced: GenerationSection | float | 0.1–2.0 | 1.3 | YES | Global + per-BatchJob | YES (settings.json) | YES (sampling trio) |
| 9 | Top P | `GenerationParameters.top_p` | Advanced: GenerationSection | float | 0.1–1.0 | 0.95 | YES | Global + per-BatchJob | YES | YES |
| 10 | Top K | `GenerationParameters.top_k` | Advanced: GenerationSection | int | 1–500 | 300 | YES | Global + per-BatchJob | YES | YES |
| 11 | Max New Tokens | `GenerationParameters.max_new_tokens` | Advanced: GenerationSection | int | 128–8192 | 4096 | YES | Global + per-BatchJob | YES | NO |
| 12 | Seed | `GenerationParameters.seed` | Advanced: GenerationSection | Optional[int] | Any int | None | NO (torch.manual_seed) | Global + per-BatchJob | YES | NO |
| 13 | Append Silence | `GenerationParameters.append_silence` | Advanced: GenerationSection | float | 0.0–5.0 | 0.5 | NO (post-gen) | Global + per-BatchJob | YES | NO |
| 14 | Normalize | `GenerationParameters.normalize_output` | Advanced: GenerationSection | bool | true/false | False | NO (post-gen) | Global + per-BatchJob | YES | NO |
| 15 | Auto Play | `GenerationParameters.auto_play` | Advanced: GenerationSection | bool | true/false | True | NO (UI) | Global (forced False for batch) | YES | NO |
| 16 | Output Format | `GenerationParameters.output_format` | (not in UI) | str | "wav" only | "wav" | NO | Global + per-BatchJob | YES | NO |

### 4.3 Voice / Speaker Parameters

| # | Name | Internal Field | UI Location | Type | Scope | Persisted? |
|---|------|----------------|-------------|------|-------|------------|
| 17 | Voice ID | `MainWindow._voice_id` / `BatchJob.voice_id` | Advanced: VoiceSection | Optional[str] | Global + per-Speaker + per-BatchJob | YES (presets) |
| 18 | Reference Audio | `VoiceProfile.reference_audio_path` | Voice Library | str (path) | Per-Voice | YES (profile.json) |
| 19 | Reference Transcript | `VoiceProfile.reference_transcript` | Voice Library | str | Per-Voice | YES |
| 20 | Speaker | `BatchJob.speaker` / `SplitPart.speaker` | Long Narration dialog | Optional[str] | Per-BatchJob (dialogue) | YES (scene.json) |
| 21 | Voice Name | `VoiceProfile.name` | Advanced: VoiceSection | str (display) | Per-Voice | YES |
| 22 | Sample Rate | `VoiceProfile.sample_rate` | Advanced: VoiceSection | int (display) | Per-Voice | YES |
| 23 | Duration | `VoiceProfile.duration` | Advanced: VoiceSection | float (display) | Per-Voice | YES |

### 4.4 Application / UX Parameters (non-model)

| # | Name | Internal Field | Type | Scope | Assessment |
|---|------|----------------|------|-------|------------|
| 24 | Allow SFX (proposed) | `GenerationParameters.allow_sfx` (new) | bool | Global | Application policy — controls whether SFX markers are compiled |
| 25 | Estimated Time (proposed) | (UI calculation) | str (display) | Per-generation | Display-only — no engine effect |
| 26 | Inline Controls Help | (display text) | str | N/A | Display-only — documentation |

### 4.5 Unsupported / Legacy (in screenshot, NOT in engine)

| # | Name | In screenshot? | HIGGS V3? | Decision |
|---|------|---------------|-----------|----------|
| U1 | Stability | YES | NO | REMOVE |
| U2 | Similarity | YES | NO | REMOVE |
| U3 | Strength | YES | NO | REMOVE |
| U4 | Continuous Speed (no snap) | YES | NO (discrete only) | REPLACE with snapping slider |

### 4.6 Parameter Count Reconciliation

| Category | Count |
|----------|-------|
| HIGGS token parameters | 7 |
| Generation sampling parameters | 9 |
| Voice/Speaker parameters | 7 |
| Application/UX parameters | 3 (1 existing + 2 proposed) |
| Unsupported/legacy | 4 |
| **Total distinct parameters** | **30** |

---

## 5. SEMANTIC CATEGORIES

### VOICE CHARACTER (prompt tokens)
Emotion (#1), Style (#2), Speed (#3), Pitch (#4), Delivery (#5), SFX (#6), Pause (#7)

### GENERATION / SAMPLING (model request)
Temperature (#8), Top P (#9), Top K (#10), Max New Tokens (#11), Seed (#12), Append Silence (#13), Normalize (#14), Auto Play (#15), Output Format (#16)

### VOICE / SPEAKER
Voice ID (#17), Reference Audio (#18), Reference Transcript (#19), Speaker (#20), Voice Name (#21), Sample Rate (#22), Duration (#23)

### APPLICATION / UX
Allow SFX (#24, proposed), Estimated Time (#25, proposed), Inline Help (#26)

### UNSUPPORTED / LEGACY
Stability (U1), Similarity (U2), Strength (U3), Continuous Speed (U4)

---

## 6. HIGGS VS APPLICATION SEMANTICS

| Layer | Defines | Does NOT define |
|-------|---------|-----------------|
| **HIGGS V3** | Token syntax (`<\|category:tag\|>`), token categories (emotion/style/prosody/sfx/pause), token placement (sentence-level vs inline), token semantics | Friendly concepts, application state, scope, presets, ownership, grouping, defaults, activation, UI representation |
| **SpeechStudio Application** | Friendly concepts, application state, scope, presets, ownership, grouping, defaults, activation, UI representation | Token syntax, token categories, token placement, token semantics |

**A Friendly label does NOT need to correspond directly to a HIGGS token.**
Example: Friendly "Whisper" → Style=Whispering → `<|style:whispering|>`.
This is valid because Friendly is an abstraction layer.

**Do NOT invent HIGGS tokens to support Friendly terminology.**

---

## 7. EMOTION AUDIT

### Original reference 8 emotions:

| # | Reference Label | Intended User Meaning | HIGGS Emotion? | HIGGS Style? | Prosody? | Multi-param? | Confidence | Risk |
|---|----------------|----------------------|----------------|--------------|----------|--------------|------------|------|
| 1 | Neutral | No emotion override — neutral narration | None (no token) | None | Normal/Normal/Normal | YES (preset of defaults) | HIGH | LOW |
| 2 | Happy | Joyful, positive | Elation | — | — | NO (direct 1:1) | HIGH | LOW |
| 3 | Sad | Sorrowful, melancholic | Sadness | — | — | NO | HIGH | LOW |
| 4 | Angry | Frustrated, furious | Anger | — | — | NO | HIGH | LOW |
| 5 | Calm | Peaceful, relaxed, steady | Contentment | — | (candidate: Slow + Expressive Low) | MAYBE (see §10) | HIGH for emotion-only; MEDIUM for prosody | LOW / MEDIUM |
| 6 | Tense | Anxious, suspenseful, on edge | **UNDECIDED** (see §10) | — | (candidate: Slow + Expressive High) | MAYBE | LOW | HIGH |
| 7 | Whisper | Quiet, intimate, hushed | None | **Whispering** | — | YES (cross-category) | HIGH | LOW |
| 8 | Excited | Energetic, eager, enthusiastic | Enthusiasm | — | (candidate: Fast + High + Expressive High) | MAYBE (see §10) | HIGH for emotion-only; MEDIUM for prosody | LOW / MEDIUM |

---

## 8. EMOTION SIMPLIFICATION — FINAL RECOMMENDED SET

### Primary Friendly Emotion Set (8 controls):

| # | Friendly Label | Full Name (display) | Underlying Mapping |
|---|---------------|-------------------|-------------------|
| 1 | Neutral | Neutral | Emotion=None, Style=None (defaults) |
| 2 | Happy | Happy | Emotion=Elation |
| 3 | Sad | Sad | Emotion=Sadness |
| 4 | Angry | Angry | Emotion=Anger |
| 5 | Calm | Calm | Emotion=Contentment (initial; future: +prosody) |
| 6 | Tense | Tense | UNDECIDED — see §10 |
| 7 | Whisper | Whisper | Style=Whispering |
| 8 | Excited | Excited | Emotion=Enthusiasm (initial; future: +prosody) |

**Rules:**
- Full human-readable names. NO abbreviations (not "Amuse", "Enthus").
- The complete 21-emotion HIGGS set remains accessible via "More Emotions."
- Near-synonyms (Amusement≈Happy, Pride≈Happy, Affection≈Calm) go under "More."

---

## 9. USER INTENT MAPPING

### USER INTENT → FRIENDLY CONTROL → UNDERLYING ADVANCED STATE

| Friendly Control | Emotion | Style | Speed | Pitch | Delivery | Confidence | Evidence |
|-----------------|---------|-------|-------|-------|----------|------------|----------|
| **Neutral** | None | None | Normal | Normal | Normal | HIGH | Default state — no tokens |
| **Happy** | Elation | — | — | — | — | HIGH | HIGGS: "joyful excitement" |
| **Sad** | Sadness | — | — | — | — | HIGH | HIGGS: "sorrow, grief" |
| **Angry** | Anger | — | — | — | — | HIGH | HIGGS: "frustration, fury" |
| **Calm** | Contentment | — | (Normal) | (Normal) | (Normal) | HIGH (initial) | HIGGS: "calm satisfaction, peaceful ease" |
| **Tense** | UNDECIDED | — | — | — | — | LOW | See §10 |
| **Whisper** | None | Whispering | — | — | — | HIGH | HIGGS style, not emotion |
| **Excited** | Enthusiasm | — | (Normal) | (Normal) | (Normal) | HIGH (initial) | HIGGS: "energetic excitement, eagerness" |

### Future multi-parameter presets (Phase 2, after user testing):

| Friendly Control | Emotion | Style | Speed | Pitch | Delivery | Confidence |
|-----------------|---------|-------|-------|-------|----------|------------|
| **Calm (enhanced)** | Contentment | — | Slow | — | Expressive Low | MEDIUM |
| **Excited (enhanced)** | Enthusiasm | — | Fast | High | Expressive High | MEDIUM |
| **Tense (if resolved)** | Fear | — | Slow | — | Expressive High | LOW |

---

## 10. CALM / EXCITED / TENSE / OTHER HIGH-LEVEL STATES

### Calm

| Aspect | Value |
|--------|-------|
| Underlying (initial) | Emotion=Contentment |
| Underlying (enhanced, future) | Emotion=Contentment + Speed=Slow + Delivery=Expressive Low |
| Confidence | HIGH (initial), MEDIUM (enhanced) |
| Risk | LOW (initial), MEDIUM (enhanced — 3-param coupling) |
| Evidence | HIGGS: "calm satisfaction, peaceful ease, slow-paced content" — description already implies calm pacing |
| Recommendation | Start with emotion-only. Add prosody preset only after user testing confirms Contentment alone is insufficient. |

### Excited

| Aspect | Value |
|--------|-------|
| Underlying (initial) | Emotion=Enthusiasm |
| Underlying (enhanced, future) | Emotion=Enthusiasm + Speed=Fast + Pitch=High + Delivery=Expressive High |
| Confidence | HIGH (initial), MEDIUM (enhanced) |
| Risk | LOW (initial), HIGH (enhanced — 4-param coupling) |
| Evidence | HIGGS: "energetic excitement, eagerness" — implies high energy |
| Recommendation | Start with emotion-only. Add prosody preset only after user testing. |

### Tense

| Aspect | Value |
|--------|-------|
| Underlying | UNDECIDED |
| Candidates | (a) Fear only, (b) Fear + Slow + Expressive High, (c) Arousal only, (d) Omit |
| Confidence | LOW |
| Risk | HIGH |
| Evidence | HIGGS Fear: "anxiety, dread, apprehension, suspense" — mentions "suspense" (closest to "tense"). HIGGS Arousal: "heightened intensity, alertness, suspense" — also mentions "suspense" but name is ambiguous. |
| Recommendation | **UNDECIDED.** Do NOT blindly map to Fear. User must test: (1) Fear alone, (2) Fear+Slow+Expressive High, (3) Arousal alone. If none satisfactory, omit Tense from initial release. |

### Other candidates considered:

| Candidate | Assessment | Recommendation |
|-----------|------------|----------------|
| Energetic | Subset of Excited (Enthusiasm + Fast) | Merge into Excited (enhanced) |
| Dramatic | Fear + Slow + Expressive High (same as Tense candidate) | Merge into Tense (if resolved) |
| Soft | Contentment + Expressive Low | Subset of Calm (enhanced) |
| Strong | Anger + Expressive High | Too niche; omit |
| Natural | All defaults | Same as Neutral |

---

## 11. WHISPER

| Aspect | Value |
|--------|-------|
| Friendly label | Whisper |
| Underlying mapping | Style = Whispering |
| HIGGS token emitted | `<\|style:whispering\|>` |
| Additional prosody | NONE (initial) — Whispering alone is sufficient |
| Confidence | HIGH |
| Risk | LOW |
| Reversibility | FULLY reversible — Advanced Style=Whispering → Friendly Whisper active |

### Whisper + Emotion coexistence:

**Open question**: Can the user select Whisper AND an emotion (e.g. Happy)?

**Option A**: Mutually exclusive (grid is single-selection)
- Selecting Whisper clears emotion; selecting emotion clears Whisper
- Simpler UX, matches reference screenshot (single grid)

**Option B**: Coexist (Whisper is a toggle, emotions are separate)
- User can have Whisper + Happy simultaneously
- More flexible, but deviates from the single-grid reference

**Recommendation**: **Option A** (mutually exclusive) for initial release.
Matches the reference screenshot. A tooltip explains: "Whisper is a voice
style. Selecting an emotion will replace it."

---

## 12. STYLE

### Current 3 HIGGS styles:
- Singing (`<|style:singing|>`)
- Whispering (`<|style:whispering|>`)
- Shouting (`<|style:shouting|>`)

### Friendly representation:

| Style | Friendly location | Rationale |
|-------|------------------|-----------|
| Whispering | Emotion grid (as "Whisper" button) | Matches reference; most common style |
| Singing | Custom Style dropdown | Niche; less frequently used |
| Shouting | Custom Style dropdown | Niche; often combined with Anger |

### Recommendation:
- **Whisper** → emotion grid (cross-category abstraction, §11)
- **Custom Style dropdown** → exposes all 3 styles + None (for Singing and Shouting)
- The dropdown reflects the current Style state (two-way sync)
- If the user selects "Whisper" in the grid, the dropdown updates to "Whispering"
- If the user selects "Shouting" in the dropdown, the grid deactivates "Whisper"

---

## 13. PROSODY AUDIT

### Current prosody parameters:
- Speed: 5 values (V.Slow, Slow, Normal, Fast, V.Fast)
- Pitch: 3 values (Low, Normal, High)
- Delivery: 3 values (Expressive Low, Normal, Expressive High)

### High-level prosody grouping analysis:

| Candidate | Speed | Pitch | Delivery | Confidence | Evidence | Recommendation |
|-----------|-------|-------|----------|------------|----------|----------------|
| Calm | Slow | — | Expressive Low | MEDIUM | Conceptual match | UNDECIDED — start as emotion-only, add prosody in Phase 2 |
| Energetic | Fast | High | Expressive High | MEDIUM | Conceptual match | UNDECIDED — merge into Excited (enhanced) |
| Dramatic | Slow | — | Expressive High | LOW | Conceptual match | UNDECIDED — merge into Tense (if resolved) |
| Soft | — | Low | Expressive Low | LOW | Conceptual match | UNDECIDED — too niche |
| Strong | — | — | Expressive High | LOW | Conceptual match | UNDECIDED — too niche |

### Recommendation:
**Do NOT group prosody into high-level presets in initial release.**
Expose Speed, Pitch, Delivery individually (as the reference screenshot
does — Pace/Pitch/Delivery rows). Insufficient preset data validates
combinations.

If Phase 2 user testing confirms combinations, add a "Tone" preset row
above the individual controls with "Custom" as the mixed-state label.

---

## 14. SPEED UX

| Option | UX | Implementation | Reversible? | Risk | Recommendation |
|--------|-----|----------------|-------------|------|----------------|
| A: 5 discrete buttons | V.Slow / Slow / Normal / Fast / V.Fast | Direct 1:1 to HIGGS tokens | YES | LOW | Acceptable fallback |
| B: Snapping slider | Visual continuous, snaps to 5 positions | Slider quantized to nearest token | YES | MEDIUM | **PREFERRED** — matches reference visual |
| C: Continuous (no snap) | Free-range | Would emit invalid tokens | NO | HIGH | REJECTED |

### Recommendation: Option B (snapping slider)

**Implementation**:
- Slider range: 0.0–1.0 (visual)
- Snap positions: 0.1 (V.Slow ~0.65x), 0.3 (Slow ~0.85x), 0.5 (Normal 1.0x), 0.7 (Fast ~1.2x), 0.9 (V.Fast ~1.4x)
- Display: "0.65x", "0.85x", "1.0x", "1.2x", "1.4x"
- Underlying: HIGGS token name (speed_very_slow, etc.) or "Normal" (no token)

**Normal = NO TOKEN EMITTED.** Do NOT create `<|prosody:speed_normal|>`.

---

## 15. STRENGTH

| Aspect | Value |
|--------|-------|
| In screenshot? | YES (slider, 70%) |
| HIGGS V3 parameter? | NO |
| Engine parameter? | NO |
| Current implementation? | UI-only placeholder in modern_control_panel.py (`self._strength = 75`), emits `parameters_changed` but has NO effect |
| Multi-param mapping candidate? | Strength → Delivery (Low↔High) — but arbitrary |
| Confidence | LOW |
| Risk | HIGH (misleading if no effect) |

### Recommendation: **REMOVE**

No engine parameter exists. A UI-only slider that visually changes but
has no generation effect is misleading. If the user insists on a
"Strength" concept, map it to Delivery ONLY with explicit user approval.

---

## 16. STABILITY / SIMILARITY

| Aspect | Stability | Similarity |
|--------|-----------|------------|
| In screenshot? | YES (0.75) | YES (0.80) |
| HIGGS V3 parameter? | NO | NO |
| Engine parameter? | NO | NO |
| Origin | ElevenLabs concept | ElevenLabs concept |

### Recommendation: **REMOVE both**

These are NOT HIGGS V3 parameters. They come from ElevenLabs-style TTS
engines. The HIGGS V3 generation parameters are: temperature, top_p,
top_k, max_new_tokens, seed. Do NOT incorrectly associate ElevenLabs
terminology with HIGGS V3.

---

## 17. GENERATION TAB AUDIT

| Parameter | Friendly? | Advanced? | Decision | Rationale |
|-----------|-----------|-----------|----------|-----------|
| Temperature | YES (as AI Freedom) | YES | MERGE into AI Freedom | Primary sampling param; users don't understand "temperature" |
| Top P | HIDDEN (if grouped) / ADVANCED | YES | CONDITIONAL | Participates in AI Freedom if grouping approved |
| Top K | HIDDEN (if grouped) / ADVANCED | YES | CONDITIONAL | Participates in AI Freedom if grouping approved |
| Max New Tokens | ADVANCED ONLY | YES | ADVANCED ONLY | Technical; only needed for very long/short clips |
| Seed | YES (direct) | YES | KEEP DIRECT | Reproducibility — simple concept, fundamentally different |
| Append Silence | ADVANCED ONLY | YES | ADVANCED ONLY | Post-generation; rarely changed |
| Normalize | ADVANCED ONLY | YES | ADVANCED ONLY | Post-generation; export-only |
| Auto Play | HIDDEN | YES | HIDDEN | UI behavior, not a generation param |
| Output Format | HIDDEN | NO | HIDDEN | Only "wav" supported |

---

## 18. AI FREEDOM / CREATIVITY CONCEPT

### Investigation: Can Temperature + Top K + Top P form "AI Freedom"?

All three control sampling "freedom" / "variation." The user-facing
question is: **"How much freedom should the model have?"**

### Evidence from current project:

| Source | Temperature | Top P | Top K | Notes |
|--------|-------------|-------|-------|-------|
| DEFAULT_SETTINGS (GenerationParameters defaults) | 1.3 | 0.95 | 300 | Current production defaults |
| HIGGS V3 reference | 1.0 | 0.95 | 50 | Official defaults (conservative) |
| Token Guide recommendation | 1.2–1.4 | 0.95 | 300 | SpeechStudio recommended range |

### Proposed AI Freedom lookup table (5 levels):

| AI Freedom Level | Temperature | Top P | Top K | Rationale |
|-----------------|-------------|-------|-------|-----------|
| **Strict** | 0.5 | 0.80 | 50 | Very conservative, predictable |
| **Focused** | 0.8 | 0.90 | 100 | Mild variation |
| **Balanced** | 1.0 | 0.95 | 200 | HIGGS reference default |
| **Expressive** | 1.3 | 0.95 | 300 | SpeechStudio default (recommended) |
| **Wild** | 1.6 | 1.0 | 500 | Maximum variation |
| **Custom** | (whatever) | (whatever) | (whatever) | When Advanced state doesn't match any level |

### Confidence: MEDIUM
### Risk: MEDIUM (hides useful combinations)
### Reversibility: YES — "Custom" state when combination doesn't match

### Recommendation:

**CONDITIONAL MERGE** — implement AI Freedom ONLY if:
1. The mapping uses the lookup table above (5 discrete levels + Custom)
2. The Friendly control shows "Custom" when Advanced state doesn't match
3. Advanced tab remains fully accessible for manual fine-tuning
4. Tooltip explains: "AI Freedom sets Temperature, Top K, and Top P together"

If the user does NOT want grouping: expose Temperature as a direct
"Creativity" slider (0.1–2.0), leave Top K and Top P in Advanced.

---

## 19. AI LEASH / CONTROL CONCEPT

### Investigation: Can a SECOND high-level dimension be useful?

The question: can the user independently control:
- HOW MUCH THE MODEL EXPLORES (AI Freedom / Creativity)
- HOW TIGHTLY THE MODEL IS CONSTRAINED (AI Leash / Control)

### Current parameter analysis:

| Parameter | Controls exploration? | Controls constraint? |
|-----------|----------------------|---------------------|
| Temperature | YES (higher = more exploration) | YES (lower = more constraint) |
| Top K | YES (higher = more candidates) | YES (lower = fewer candidates) |
| Top P | YES (higher = larger nucleus) | YES (lower = smaller nucleus) |

**Finding**: Temperature, Top K, and Top P all control the SAME
dimension (exploration vs. constraint). They are NOT independent axes.

### Conclusion:

**There is NO predictable second dimension.** A "AI Leash" concept
would be a fake second axis with no real engine support.

### Recommendation: **DO NOT implement AI Leash / Control**

One "AI Freedom" control is sufficient. A second dimension would
mislead the user into thinking they can independently control
exploration and constraint, which the engine does not support.

---

## 20. SEED

| Aspect | Value |
|--------|-------|
| Friendly? | YES (direct) |
| Model receives? | NO (torch.manual_seed only) |
| Concept | Reproducibility — "same seed + same voice = same output" |
| Default | None (random) |
| Persisted? | YES (settings.json, presets) |

### Recommendation: **KEEP as direct control, do NOT merge with AI Freedom**

Seed is fundamentally different — it controls reproducibility, not
sampling style. Merging it with AI Freedom would be misleading.

### Friendly representation:
- Text input (integer or empty = random)
- Label: "Seed" with tooltip: "Same seed + same voice = same output. Leave empty for random."

### Default behavior: Random (empty).

---

## 21. TOP K / TOP P

### Analysis: Can they participate in AI Freedom?

YES — both Top K and Top P control sampling constraint, same as
Temperature. They fit naturally into the AI Freedom lookup table (§18).

### If AI Freedom is NOT approved:
- Top K → ADVANCED ONLY
- Top P → ADVANCED ONLY
- Rationale: niche parameters; Temperature alone covers most use cases

### If AI Freedom IS approved:
- Top K and Top P are HIDDEN in Friendly (grouped into AI Freedom)
- They remain accessible in Advanced for fine-tuning

---

## 22. ALLOW SFX

### Current engine behavior (verified from prompt_builder.py):

The PromptBuilder converts `{sfx:Laughter:Haha}` markers in the text
to `<|sfx:laughter|>Haha` tokens via `_SFX_MARKER_RE.sub()`. There is
NO global on/off switch — if the marker is in the text, the token is
emitted.

### Proposed behavior:

| Allow SFX State | Engine Behavior |
|----------------|-----------------|
| ON | SFX markers converted to HIGGS tokens (current behavior) |
| OFF | SFX markers replaced with onomatopoeia only (e.g. `{sfx:Laughter:Haha}` → `Haha`) — no token emitted |

### Implementation:
- New field: `GenerationParameters.allow_sfx: bool = True`
- PromptBuilder checks `allow_sfx` before converting markers
- If OFF: `_SFX_MARKER_RE.sub()` replaces `{sfx:Name:Onomatopoeia}` with just `Onomatopoeia`

### Classification:
- **Type**: Application-level policy control (NOT a HIGGS token)
- **Scope**: Global (applies to entire generation)
- **Belongs in**: Friendly (GENERATION tab) — simple toggle

### Confidence: HIGH
### Risk: LOW

---

## 23. SFX / PAUSE

### Assessment:

SFX and Pause are **positional/contextual** concepts — they are
inserted at specific cursor positions in the text, not set as global
state.

### Recommendation:

| Control | Friendly location | Rationale |
|---------|------------------|-----------|
| SFX buttons | GENERATION tab (3×3 grid) | Quick insert at cursor; matches reference |
| Pause buttons | GENERATION tab | Quick insert at cursor |

These are **action buttons** (insert at cursor), not state controls.
They do not have an "active" visual state — they perform an action
when clicked.

---

## 24. INLINE CONTROLS HELP

### Current actual HIGGS / SpeechStudio syntax:

| Feature | Actual syntax | Screenshot syntax | Match? |
|---------|--------------|-------------------|--------|
| SFX | `{sfx:Laughter:Haha}` | `[sfx: rain]` | NO |
| Pause | `{pause}` / `{long_pause}` | `[pause: 1.5s]` | NO |
| Whisper | `<\|style:whispering\|>` (sentence-level) | `[whisper]...[/whisper]` (inline) | NO |

### Recommendation:

**Use ACTUAL current syntax.** Do NOT copy the screenshot's older syntax.

### Proposed Inline Controls Help content:

```
INLINE CONTROLS

SFX (insert in text):
  {sfx:Laughter:Haha}    → <|sfx:laughter|>Haha
  {sfx:Cough:Ahem}       → <|sfx:cough|>Ahem

Pause (insert in text):
  {pause}                → short pause (~400-700ms)
  {long_pause}           → long pause (~700-1500ms)

HIGGS tokens (Raw Mode):
  <|emotion:fear|>       → emotion for the sentence
  <|style:whispering|>   → style for the sentence
  <|prosody:speed_slow|> → speed for the sentence
```

### Notes:
- `[whisper]...[/whisper]` inline style switching is NOT supported
- `[pause: 1.5s]` custom duration is NOT supported (only pause/long_pause)

---

## 25. SPEAKER

### Friendly "Selected Speaker" card:

| Element | Source | Engine parameter? |
|---------|--------|-------------------|
| Avatar | VoiceProfile.preview_image (if exists) | NO (display) |
| Name | VoiceProfile.name | YES (display) |
| Metadata | (see §26) | NO (display) |
| Change Voice button | Opens Voice Library | NO (navigation) |

### Recommendation:
- Friendly shows the **currently selected VoiceProfile** (read-only display)
- "Change Voice" opens the Voice Library dialog (existing system)
- Friendly does NOT create a second profile system

---

## 26. SPEAKER METADATA

### Current VoiceProfile fields:

| Field | Exists? | Type |
|-------|---------|------|
| id | YES | str |
| name | YES | str |
| reference_audio_path | YES | str |
| reference_transcript | YES | str |
| sample_rate | YES | int |
| channels | YES | int |
| duration | YES | float |
| description | YES | str |
| tags | YES | List[str] |
| preview_image | YES | str |

### Screenshot shows: "Male, 40s, Calm"

These metadata fields (gender, age_range, character) do NOT exist in
VoiceProfile. The closest existing field is `description` (free text)
and `tags` (list of strings).

### Recommendation:

**Do NOT invent new data models merely to match the screenshot.**

Options:
- **Option A**: Use the existing `description` field to display free-text metadata
- **Option B**: Use `tags` to display structured metadata (e.g. ["Male", "40s", "Calm"])
- **Option C**: Do not display metadata (only name + duration)

**Recommendation**: **Option B** — use `tags` for structured metadata.
The user can add tags like "Male", "40s", "Calm" when creating a voice
profile. Friendly displays them as metadata text. No new data model needed.

---

## 27. FRIENDLY ACTIVATION MODEL

### Core rule:

**A Friendly control is active/highlighted ONLY when the CURRENT
EFFECTIVE STATE matches that Friendly concept.**

The control does NOT light simply because the user clicked it once.
It lights because the effective semantic state matches.

### Examples:

| Effective State | Friendly Control | Active? |
|----------------|-----------------|---------|
| Emotion=Elation | Happy | YES |
| Emotion=None | Happy | NO |
| Style=Whispering | Whisper | YES |
| Style=None | Whisper | NO |
| Speed=Fast, Pitch=High, Delivery=Expressive High | Excited (enhanced) | YES |
| Speed=Fast, Pitch=Low, Delivery=Normal | Excited (enhanced) | NO → Custom |
| Temperature=1.3, Top P=0.95, Top K=300 | AI Freedom=Expressive | YES |
| Temperature=0.5, Top P=0.80, Top K=50 | AI Freedom=Strict | YES |
| Temperature=0.7, Top P=0.95, Top K=300 | AI Freedom | NO → Custom |

---

## 28. ADVANCED → FRIENDLY SYNCHRONISATION

### Rule:

When Advanced changes the state, Friendly reflects it.

### Algorithm:

```
For each Friendly control:
    Read the current effective state (MainWindow._emotion, etc.)
    Compare against the Friendly control's mapping
    If match → activate (highlight)
    If no match → deactivate (or show "Custom" for grouped controls)
```

### Examples:

| Advanced State | Friendly Result |
|---------------|-----------------|
| Emotion=Fear | (No direct Friendly emotion for Fear) → "More Emotions" or inactive |
| Style=Whispering | Whisper active |
| Speed=Fast, Pitch=High, Delivery=Expressive High | Excited active (if enhanced mapping) |
| Speed=Fast, Pitch=Low | Custom (no preset matches) |

---

## 29. FRIENDLY → ADVANCED SYNCHRONISATION

### Rule:

When Friendly is activated, set the underlying parameters required by
that Friendly mapping. All changes use the existing semantic state
variables.

### Example:

```
User clicks "Whisper":
    MainWindow._style = "Whispering"
    MainWindow._emotion = None  (clear emotion if mutually exclusive)
    → ControlPanel.set_style("Whispering")
    → ControlPanel.set_emotion(None)
    → _update_prompt_preview()
```

No separate Friendly state. The change goes directly to the MainWindow
variables, which are the single source of truth.

---

## 30. FRIENDLY DEACTIVATION — OWNERSHIP TRACKING

### Core rule:

When a Friendly control is deactivated, ONLY the parameters that the
Friendly control ACTUALLY CHANGED are restored. Pre-existing user-owned
values are preserved.

### Ownership tracking mechanism:

For each parameter, track:
- `current_value`: the current value
- `friendly_owner`: which Friendly control owns this parameter (None if user-owned)
- `previous_value`: the value before the Friendly control took ownership

### Example 1: Friendly changes the value

```
Initial: Pitch = Normal (user-owned, no Friendly owner)

User activates "Excited" (enhanced):
    Pitch → High
    Pitch.friendly_owner = "Excited"
    Pitch.previous_value = Normal

User deactivates "Excited":
    Pitch → previous_value = Normal
    Pitch.friendly_owner = None
```

### Example 2: Friendly does NOT change the value

```
Initial: Pitch = High (user-owned, no Friendly owner)

User activates "Excited" (enhanced):
    Pitch is already High — Friendly does NOT change it
    Pitch.friendly_owner = None (Friendly didn't take ownership)
    Pitch.previous_value = (not set)

User deactivates "Excited":
    Pitch remains High (Friendly didn't own it)
```

### Rule:

**Friendly only restores parameters it actually changed.**
Parameters that were already at the target value are NOT owned by
Friendly and are NOT restored on deactivation.

---

## 31. GROUPED CONTROL OWNERSHIP

The ownership mechanism (§30) applies to ANY multi-parameter Friendly
control: Excited (enhanced), Calm (enhanced), AI Freedom, etc.

### AI Freedom example:

```
Initial: Temperature=1.3, Top P=0.95, Top K=300 (user-owned)

User selects AI Freedom = "Expressive":
    Temperature → 1.3 (already 1.3 — NOT owned)
    Top P → 0.95 (already 0.95 — NOT owned)
    Top K → 300 (already 300 — NOT owned)
    (All three already match — Friendly owns NONE)

User changes Advanced: Top K → 500

User selects AI Freedom = "Expressive" again:
    Temperature → 1.3 (already 1.3 — NOT owned)
    Top P → 0.95 (already 0.95 — NOT owned)
    Top K → 300 (was 500 — CHANGED, owned by AI Freedom)
    Top K.previous_value = 500

User deactivates AI Freedom (selects "Custom"):
    Top K → 500 (restored)
    Temperature, Top P unchanged (not owned)
```

---

## 32. MIXED STATE

### Definition:

When the Advanced state does not match any Friendly preset, the
Friendly control shows "Custom" (not the preset name, not active).

### Examples:

| Advanced State | Friendly Result |
|---------------|-----------------|
| Speed=Fast, Pitch=High, Delivery=Expressive High | Excited (enhanced) = active |
| Speed=Fast, Pitch=Low, Delivery=Normal | Excited (enhanced) = **Custom** |
| Temperature=0.7, Top P=0.95, Top K=300 | AI Freedom = **Custom** |

### Visual representation:

- Active preset: highlighted with accent color
- Custom: no highlight, label shows "Custom" or the control is inactive
- The individual parameter values (Speed, Pitch, etc.) are always visible below

### Rule:

**Friendly must NOT falsely remain highlighted as fully active when
the state doesn't match.** It must show "Custom" or deactivate.

---

## 33. TOKEN VISUAL STATE

### Rule:

If an actual managed token is active, the corresponding Friendly
control is active. If the token disappears, the Friendly control
becomes inactive.

### Applies to:
- Emotion → Happy/Sad/Angry/Calm/Tense/Excited
- Style → Whisper (and Custom Style dropdown)
- Speed → Pace slider
- Pitch → Pitch control
- Delivery → Delivery control

### Example:

```
Effective state: Emotion=Elation
→ Friendly "Happy" is active (highlighted)

User clears emotion (sets to None):
→ Effective state: Emotion=None
→ Friendly "Happy" is NOT active
→ Friendly "Neutral" is active (matches the default state)
```

The visual activation state comes from **semantic effective state**,
NOT from an independent Friendly highlight state.

---

## 34. FRIENDLY AND PREVIEW

### Hard contract:

The following MUST agree:
1. Friendly active controls
2. Effective semantic state
3. Preview (compiled prompt)
4. Actual generation prompt

### Example:

```
Friendly: "Whisper" active
→ Effective state: Style=Whispering
→ Preview: <|style:whispering|>Hello world.
→ Generate prompt: <|style:whispering|>Hello world.
```

If any of these disagree, it is a bug.

### Rule:

If Friendly says a value is active, the effective compiled prompt MUST
reflect the corresponding state. If the token or state is removed,
Friendly MUST stop showing it as active.

---

## 35. GENERATION HARD CONTRACT

### Rule:

Friendly Generation values MUST equal the actual values sent to the model.

```
Friendly: AI Freedom = "Expressive"
→ Temperature = 1.3, Top P = 0.95, Top K = 300
→ BatchJob.parameters.temperature = 1.3
→ BatchJob.parameters.top_p = 0.95
→ BatchJob.parameters.top_k = 300
→ Model receives: temperature=1.3, top_p=0.95, top_k=300
```

Never display a Friendly setting that does not correspond to actual
execution.

---

## 36. ADVANCED VALUES AND FRIENDLY DEFAULTING

### Rule (same as §30):

When Friendly activation changes multiple parameters, the original
values are stored. When the Friendly control is removed, ONLY
parameters actually owned by the Friendly control are restored.

Do NOT overwrite unrelated Advanced changes.

---

## 37. MORE EMOTIONS UX

### Options:

| Option | Pros | Cons |
|--------|------|------|
| Inline expansion | No popup; stays in context | Takes vertical space |
| Popup dialog | Compact; doesn't clutter | Modal; feels separate |
| Scrolling extension | Smooth; no modal | Can be disorienting |

### Recommendation: **Inline expansion**

The "More emotions" button expands the grid from 8 to 21 (adds 13
emotions below the primary 8). This stays in context, doesn't require
a modal, and is visually consistent with the primary grid.

**Implementation**:
- 4×2 primary grid (8 emotions)
- "More emotions" button below
- Click → grid expands to 4×6 (24 slots, 21 filled + 3 empty or a 4×5 + 1)
- "Fewer emotions" button collapses back to 4×2

---

## 38. FRIENDLY GENERATION CONTROL VISUAL MODEL

### Recommended visual abstractions:

| Control | Visual | Rationale |
|---------|--------|-----------|
| AI Freedom | 5-segment segmented control (Strict / Focused / Balanced / Expressive / Wild) + "Custom" | Discrete levels match the lookup table; "Custom" for mixed state |
| Seed | Text input + "Random" button | Simple; matches Advanced |
| Allow SFX | Toggle switch | Binary; matches reference |

**Do NOT use technical Advanced widgets** (QDoubleSpinBox for
temperature, QSpinBox for top_k). Friendly controls should explain
intent, not implementation.

---

## 39. COMPLETE ADVANCED → FRIENDLY MAPPING

| Advanced Parameter | Current Meaning | Friendly Control | Mapping Type | Ownership | Confidence | Risk | Reversible |
|--------------------|-----------------|-----------------|--------------|-----------|------------|------|------------|
| Emotion (#1) | 21 emotion tokens | 8 primary + More | DIRECT (8) + GROUPED (13) | Friendly owns Emotion when activated | HIGH | LOW | YES |
| Style (#2) | 3 style tokens | Whisper (grid) + Custom Style dropdown | DIRECT (cross-category for Whisper) | Friendly owns Style when Whisper activated | HIGH | LOW | YES |
| Speed (#3) | 5 speed values | Snapping slider | DIRECT | Friendly owns Speed if part of a preset | HIGH | MEDIUM | YES |
| Pitch (#4) | 3 pitch values | Segmented control | DIRECT | Friendly owns Pitch if part of a preset | HIGH | LOW | YES |
| Delivery (#5) | 3 delivery values | Segmented control | DIRECT | Friendly owns Delivery if part of a preset | HIGH | LOW | YES |
| SFX (#6) | 9 SFX types | SFX grid (action) | DIRECT (action, not state) | N/A (insertion action) | HIGH | LOW | N/A |
| Pause (#7) | 2 pause types | Pause buttons (action) | DIRECT (action) | N/A | HIGH | LOW | N/A |
| Temperature (#8) | Sampling randomness | AI Freedom (grouped) | GROUPED (conditional) | Friendly owns if AI Freedom changed it | MEDIUM | MEDIUM | YES (with Custom) |
| Top P (#9) | Nucleus sampling | AI Freedom (grouped) or ADVANCED | GROUPED / ADVANCED | Same as Temperature | MEDIUM | MEDIUM | YES |
| Top K (#10) | Token limit | AI Freedom (grouped) or ADVANCED | GROUPED / ADVANCED | Same as Temperature | MEDIUM | MEDIUM | YES |
| Max New Tokens (#11) | Max audio length | — | ADVANCED ONLY | N/A | HIGH | LOW | N/A |
| Seed (#12) | Reproducibility | Seed input | DIRECT | Friendly owns Seed when set | HIGH | LOW | YES |
| Append Silence (#13) | Post-gen padding | — | ADVANCED ONLY | N/A | HIGH | LOW | N/A |
| Normalize (#14) | Loudness | — | ADVANCED ONLY | N/A | HIGH | LOW | N/A |
| Auto Play (#15) | UI behavior | — | HIDDEN | N/A | HIGH | LOW | N/A |
| Output Format (#16) | File format | — | HIDDEN | N/A | HIGH | LOW | N/A |
| Voice ID (#17) | Voice profile | Selected Speaker card | DIRECT | Friendly owns Voice ID when selected | HIGH | LOW | YES |
| Allow SFX (#24, new) | SFX policy | Toggle switch | DIRECT | Friendly owns allow_sfx | HIGH | LOW | YES |

---

## 40. COMPLETE FRIENDLY CONTROL TABLE

| Friendly Label | Purpose | Underlying Params | Default | Allowed States | Activation Condition | Deactivation Behavior | Ownership | Advanced Interaction | Mixed State | Preview Expectation | Model Effect | Confidence |
|----------------|---------|-------------------|---------|----------------|----------------------|----------------------|-----------|---------------------|-------------|---------------------|--------------|------------|
| **Neutral** | No character override | Emotion=None, Style=None, Speed=Normal, Pitch=Normal, Delivery=Normal | Active by default | All defaults | Effective state = all defaults | N/A (always available) | Owns nothing (clears others) | Clearing any param → may activate Neutral | N/A | No character tokens in prompt | No character tokens | HIGH |
| **Happy** | Joyful emotion | Emotion=Elation | Inactive | Emotion=Elation or None | Emotion=Elation | Clears Emotion → None | Owns Emotion | Advanced Emotion=Elation → Happy active | If Emotion≠Elation → not active | `<\|emotion:elation\|>` in prompt | Emotion token emitted | HIGH |
| **Sad** | Sorrowful emotion | Emotion=Sadness | Inactive | Emotion=Sadness or None | Emotion=Sadness | Clears Emotion | Owns Emotion | Same as Happy | Same | `<\|emotion:sadness\|>` | Emotion token | HIGH |
| **Angry** | Angry emotion | Emotion=Anger | Inactive | Emotion=Anger or None | Emotion=Anger | Clears Emotion | Owns Emotion | Same | Same | `<\|emotion:anger\|>` | Emotion token | HIGH |
| **Calm** | Peaceful emotion | Emotion=Contentment | Inactive | Emotion=Contentment or None | Emotion=Contentment | Clears Emotion | Owns Emotion | Same | Same | `<\|emotion:contentment\|>` | Emotion token | HIGH |
| **Tense** | Suspenseful (UNDECIDED) | UNDECIDED | Inactive | UNDECIDED | UNDECIDED | UNDECIDED | UNDECIDED | UNDECIDED | UNDECIDED | UNDECIDED | UNDECIDED | LOW |
| **Whisper** | Quiet style | Style=Whispering | Inactive | Style=Whispering or None | Style=Whispering | Clears Style | Owns Style | Advanced Style=Whispering → Whisper active | If Style≠Whispering → not active | `<\|style:whispering\|>` | Style token | HIGH |
| **Excited** | Energetic emotion | Emotion=Enthusiasm | Inactive | Emotion=Enthusiasm or None | Emotion=Enthusiasm | Clears Emotion | Owns Emotion | Same as Happy | Same | `<\|emotion:enthusiasm\|>` | Emotion token | HIGH |
| **Custom Style** | Style selector | Style (3 values + None) | None | None/Singing/Whispering/Shouting | Style matches selection | Sets to None | Owns Style | Direct 1:1 | N/A (direct) | `<\|style:tag\|>` if not None | Style token | HIGH |
| **Pace (Speed)** | Pacing control | Speed (5 values) | Normal | V.Slow/Slow/Normal/Fast/V.Fast | Speed matches slider position | Sets to Normal | Owns Speed if part of preset | Direct 1:1 | N/A | `<\|prosody:speed_*\|>` if not Normal | Speed token | HIGH |
| **Pitch** | Pitch control | Pitch (3 values) | Normal | Low/Normal/High | Pitch matches selection | Sets to Normal | Owns Pitch if part of preset | Direct 1:1 | N/A | `<\|prosody:pitch_*\|>` if not Normal | Pitch token | HIGH |
| **Delivery** | Expressiveness | Delivery (3 values) | Normal | Low/Normal/High | Delivery matches selection | Sets to Normal | Owns Delivery if part of preset | Direct 1:1 | N/A | `<\|prosody:expressive_*\|>` if not Normal | Delivery token | HIGH |
| **AI Freedom** | Sampling freedom | Temp+TopP+TopK | Expressive | Strict/Focused/Balanced/Expressive/Wild/Custom | Values match lookup table | Restores owned params | Owns changed params | Advanced changes → check match → Custom if no match | Custom if no match | N/A (model params) | Temp/TopP/TopK sent to model | MEDIUM |
| **Seed** | Reproducibility | Seed | Random | Any int or Random | Seed matches input | Clears to None | Owns Seed when set | Direct 1:1 | N/A | N/A | torch.manual_seed | HIGH |
| **Allow SFX** | SFX policy | allow_sfx (new) | ON | ON/OFF | Toggle state | N/A | Owns allow_sfx | Direct 1:1 | N/A | SFX markers processed or stripped | SFX tokens emitted or suppressed | HIGH |
| **Selected Speaker** | Voice selection | Voice ID | None | Any voice profile | Voice ID matches | Clears to None | Owns Voice ID | Direct 1:1 | N/A | N/A | Reference audio used | HIGH |

---

## 41. PARAMETER REDUCTION

### NOT optimized for fewest controls. Optimized for:
- Clarity
- Predictability
- Discoverability
- Low cognitive load
- Two-way synchronisation
- Preservation of full power
- Consistent relation with Advanced

### Result:

| Category | Advanced controls | Friendly controls | Change |
|----------|------------------|-------------------|--------|
| Voice character | 7 | 8 (6 emotions + Whisper + Custom Style) | +1 (Whisper split from Style) |
| Prosody | 3 | 3 (Pace, Pitch, Delivery) | 0 |
| Generation | 8 | 3 (AI Freedom, Seed, Allow SFX) | -5 |
| Voice/Speaker | 4 | 1 (Selected Speaker) | -3 |
| SFX/Pause | 2 | 2 | 0 |
| **Total** | **24** | **17** | **-7** |

**Goal achieved**: FEWER TECHNICAL DECISIONS (user doesn't need to
understand Temperature/TopK/TopP), NOT FEWER CAPABILITIES (all 24
parameters remain accessible via Advanced).

---

## 42. FINAL PROPOSED FRIENDLY STRUCTURE

### FRIENDLY VOICE tab:

```
VOICE
├── Selected Speaker (card with avatar + name + tags)
├── Emotion & Style
│   ├── 4×2 emotion grid (Neutral/Happy/Sad/Angry/Calm/Tense/Whisper/Excited)
│   ├── "More emotions" expand button
│   └── Custom Style dropdown (None/Singing/Whispering/Shouting)
├── Prosody
│   ├── Pace (snapping slider: V.Slow/Slow/Normal/Fast/V.Fast)
│   ├── Pitch (segmented: Low/Normal/High)
│   └── Delivery (segmented: Low/Normal/High)
└── [Generate CTA at bottom]
```

### FRIENDLY GENERATION tab:

```
GENERATION
├── AI Freedom (segmented: Strict/Focused/Balanced/Expressive/Wild/Custom)
├── Seed (text input + "Random" button)
├── Allow SFX (toggle switch)
├── Sound Effects (3×3 grid of 9 SFX buttons — insert at cursor)
├── Pause (2 buttons: Insert Pause / Insert Long Pause)
├── Inline Controls Help (collapsible documentation)
└── [Generate CTA at bottom]
```

### Elements NOT in Friendly (Advanced only):
- Top P (if not grouped into AI Freedom)
- Top K (if not grouped into AI Freedom)
- Max New Tokens
- Append Silence
- Normalize Output
- Auto Play
- Output Format
- Reference Audio / Transcript (via Voice Library)
- Block overrides (via Narration Blocks editor)

### Elements REMOVED:
- Strength
- Stability
- Similarity

### Display-only:
- Speaker metadata (from VoiceProfile.tags)
- Estimated time (UI calculation)

---

## 43. REQUIRED DECISION REPORT

### 1. Complete current parameter inventory:
30 distinct parameters (7 HIGGS token + 9 generation + 7 voice/speaker + 3 application/UX + 4 unsupported/legacy). See §4.

### 2. Original reference inventory:
Selected Speaker, Emotion & Style (8), Custom Style, Strength, Advanced Controls (Stability/Similarity/Speed), Allow SFX, Inline Controls Help, Generate CTA, Estimated time, Temperature/TopK/TopP/Seed. See §3.

### 3. Final recommended Friendly emotion set:
8 primary: Neutral, Happy, Sad, Angry, Calm, Tense (UNDECIDED), Whisper, Excited. See §8.

### 4. Final recommended Friendly character abstractions:
Whisper → Style=Whispering (HIGH confidence). Calm/Excited → emotion-only initially, prosody presets in Phase 2. Tense → UNDECIDED. See §9-10.

### 5. Final recommended Friendly generation abstractions:
AI Freedom (grouped Temp+TopP+TopK, conditional). Seed (direct). Allow SFX (direct toggle). See §17-22.

### 6. Exact mappings:
See §9 (USER INTENT → FRIENDLY CONTROL → UNDERLYING STATE) and §18 (AI Freedom lookup table).

### 7. Ownership rules:
Friendly owns ONLY parameters it actually changed. Previous values stored and restored on deactivation. See §30-31, §36.

### 8. Activation rules:
Friendly control active ONLY when effective state matches the concept. See §27, §33.

### 9. Advanced → Friendly synchronisation:
Friendly reads effective state, compares to mapping, activates/deactivates or shows "Custom". See §28.

### 10. Friendly → Advanced synchronisation:
Friendly writes to MainWindow state variables (single source of truth). See §29.

### 11. Mixed state rules:
"Custom" when Advanced state doesn't match any Friendly preset. See §32.

### 12. More Emotions UX:
Inline expansion (4×2 → 4×6 grid). See §37.

### 13. Advanced-only parameters:
Top P, Top K (if not grouped), Max New Tokens, Append Silence, Normalize, Auto Play, Output Format, Reference Audio/Transcript. See §17.

### 14. Parameters to remove:
Strength, Stability, Similarity. See §15-16.

### 15. Display-only parameters:
Speaker metadata (from tags), Estimated time, Inline Controls Help. See §25-26.

### 16. UNDECIDED parameters:
Tense (emotion mapping), Calm/Excited (prosody enhancement), AI Freedom (grouping approval). See §10, §18.

### 17. Risks:
- Tense mapping uncertainty (LOW confidence)
- AI Freedom grouping hides useful combinations (MEDIUM risk)
- Multi-param presets create "Custom" confusion (MEDIUM risk)
- Ownership tracking complexity (MEDIUM risk)

### 18. Recommended implementation order:
1. Remove Strength/Stability/Similarity
2. Wire modern_control_panel.py into runtime
3. Implement 8-emotion grid + More expansion
4. Implement Whisper (Style=Whispering cross-category)
5. Resolve Tense (user testing)
6. Add VoiceProfile tags for speaker metadata
7. Implement Speed snapping slider
8. Add Allow SFX toggle
9. Implement AI Freedom (conditional on approval)
10. Add Seed, SFX, Pause, Inline Help
11. Implement ownership tracking
12. Verify two-way sync + Preview Hard Contract

---

## 44. NO CODE CHANGES

This is strictly an analysis task. No modifications were made to:
- Python files
- QSS
- UI
- PromptBuilder
- CanonicalPromptCompiler
- NarrationSplitter
- HIGGS token definitions
- VoiceProfile models

The audit output becomes the implementation specification.

---

## 45. FINAL SUCCESS CRITERION

The final Friendly architecture should allow a normal user to make
meaningful voice and generation decisions without needing to understand:

- Temperature
- Top K
- Top P
- Pitch
- Speed
- Delivery
- HIGGS token syntax

At the same time:
- Advanced retains full technical control
- Friendly remains fully synchronized with Advanced
- Friendly activation reflects actual semantic state
- Friendly deactivation restores only Friendly-owned changes
- Preview reflects the effective state
- The actual generation request reflects the same state
- There is only one underlying source of truth
