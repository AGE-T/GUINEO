# Friendly Prosody — Final UX Decision Pass

- **Status**: READ ONLY — No code changes, no implementation
- **Date**: 2025-01
- **Scope**: Determine whether Pace / Pitch / Delivery should remain three
  direct Friendly controls or be replaced by high-level user-intent
  abstractions.
- **Goal**: Reduce the number of **technical decisions** the user must
  make (NOT the number of widgets).

---

## 1. THE QUESTION

Should Friendly expose:

**Option A** — Three direct prosody controls:
- Pace (Speed: V.Slow / Slow / Normal / Fast / V.Fast)
- Pitch (Low / Normal / High)
- Delivery (Expressive Low / Normal / Expressive High)

**Option B** — High-level intent abstractions:
- Calm / Energetic / Soft / Strong / Dramatic / Natural / etc.

**Option C** — Hybrid: a few high-level presets + individual controls

---

## 2. CURRENT PROSODY PARAMETER ANALYSIS

### 2.1 Perceptual independence

| Parameter | What it controls | Perceptually independent? | Technical decision required? |
|-----------|-----------------|---------------------------|------------------------------|
| **Speed** | How fast the speech is | YES — pacing is immediately obvious | LOW — "Slow" vs "Fast" is intuitive |
| **Pitch** | Voice register (higher/lower) | YES — pitch is a distinct dimension | MEDIUM — "Low" vs "High" requires some understanding |
| **Delivery** | Expressiveness (flat vs. animated) | YES — expressiveness is distinct from speed/pitch | HIGH — "Expressive Low" vs "Expressive High" is technical jargon |

### 2.2 User comprehension assessment

| Control | Label | User understands? | Technical jargon? |
|---------|-------|-------------------|-------------------|
| Pace | "Pace" / "Slow" / "Fast" | YES — universally understood | NO |
| Pitch | "Pitch" / "Low" / "High" | MOSTLY — musicians understand; some users confuse with volume | LOW |
| Delivery | "Delivery" / "Expressive Low" / "Expressive High" | NO — "Expressive Low" is meaningless to non-technical users | HIGH |

### 2.3 Key finding

**Delivery is the most technical and least intuitive of the three.**
"Expressive Low" and "Expressive High" are jargon — the user doesn't
know whether "High" means "more expressive" or "higher pitch."

Speed and Pitch are more intuitive but still require the user to
understand three independent dimensions and how they interact.

---

## 3. HIGH-LEVEL INTENT CANDIDATES

### Evaluation criteria:

For each candidate, I assess:
- **Underlying mapping**: exact Speed / Pitch / Delivery values
- **Confidence**: how well the mapping matches the user's intent
- **Risk**: coupling complexity, reversibility, mixed-state confusion
- **Evidence**: support from HIGGS descriptions, presets, or user testing
- **Distinctness**: whether it's meaningfully different from other candidates

---

### 3.1 Natural

| Aspect | Value |
|--------|-------|
| User intent | Default narration — no prosody modification |
| Speed | Normal (no token) |
| Pitch | Normal (no token) |
| Delivery | Normal (no token) |
| Confidence | **HIGH** |
| Risk | LOW |
| Evidence | This is the default state — all three at Normal means no prosody tokens emitted. Equivalent to "Neutral" for prosody. |
| Distinctness | YES — the baseline; every other preset deviates from this |
| Recommendation | **KEEP as the default state** (not a separate button — it's what you get when nothing else is selected) |

---

### 3.2 Calm

| Aspect | Value |
|--------|-------|
| User intent | Peaceful, relaxed, unhurried, soothing |
| Speed | Slow |
| Pitch | Normal (or Low) |
| Delivery | Expressive Low |
| Confidence | **MEDIUM** |
| Risk | MEDIUM — 3-parameter coupling |
| Evidence | HIGGS Contentment: "calm satisfaction, peaceful ease, slow-paced content." The description explicitly mentions "slow-paced" — supports Speed=Slow. "Peaceful ease" supports Delivery=Expressive Low (flatter, less animated). Pitch is ambiguous — could be Normal or Low. |
| Distinctness | YES — clearly different from Energetic/Dramatic |
| Underlying uncertainty | Pitch = Normal or Low? Without user testing, this is a guess. |
| Recommendation | **VIABLE** — but needs user testing to confirm Pitch choice |

---

### 3.3 Energetic

| Aspect | Value |
|--------|-------|
| User intent | Lively, high-energy, upbeat, enthusiastic |
| Speed | Fast |
| Pitch | High |
| Delivery | Expressive High |
| Confidence | **MEDIUM** |
| Risk | MEDIUM — 3-parameter coupling |
| Evidence | HIGGS Enthusiasm: "energetic excitement, eagerness." "Energetic" implies fast pacing, higher pitch, and more expressiveness. All three prosody dimensions move in the "up" direction. |
| Distinctness | YES — clearly the opposite of Calm |
| Underlying uncertainty | None significant — all three values are clearly "energetic" |
| Recommendation | **VIABLE** — strongest candidate for a high-level preset |

---

### 3.4 Soft

| Aspect | Value |
|--------|-------|
| User intent | Gentle, quiet, tender, subdued |
| Speed | Normal (or Slow) |
| Pitch | Normal (or Low) |
| Delivery | Expressive Low |
| Confidence | **LOW** |
| Risk | HIGH — overlaps heavily with Calm |
| Evidence | "Soft" is primarily a volume/timbre concept, but HIGGS has no volume parameter. The closest prosody approximation is Delivery=Expressive Low (flatter). Speed and Pitch are ambiguous. |
| Distinctness | **NO** — "Soft" vs "Calm" is a near-synonym. Both imply Slow + Expressive Low. The user cannot distinguish them meaningfully. |
| Recommendation | **REJECT** — too similar to Calm. Would confuse users ("is Soft different from Calm?"). |

---

### 3.5 Strong

| Aspect | Value |
|--------|-------|
| User intent | Powerful, forceful, commanding, intense |
| Speed | Normal (or Fast) |
| Pitch | Normal (or Low) |
| Delivery | Expressive High |
| Confidence | **LOW** |
| Risk | HIGH — ambiguous; overlaps with Angry emotion and Energetic |
| Evidence | "Strong" is primarily a volume/force concept. HIGGS has no volume parameter. Delivery=Expressive High adds expressiveness, but "strong" doesn't necessarily mean "expressive." Speed and Pitch are unclear. |
| Distinctness | **NO** — "Strong" overlaps with Energetic (Fast+High+Expressive High) and with the Angry emotion. The user won't know what makes "Strong" different from "Energetic." |
| Recommendation | **REJECT** — too ambiguous, overlaps with Energetic and Angry. |

---

### 3.6 Dramatic

| Aspect | Value |
|--------|-------|
| User intent | Theatrical, intense, suspenseful, weighty |
| Speed | Slow |
| Pitch | Normal (or Low) |
| Delivery | Expressive High |
| Confidence | **LOW** |
| Risk | HIGH — unusual combination (Slow + Expressive High is counterintuitive) |
| Evidence | "Dramatic" in narration often means slow pacing with heightened expressiveness — think of a dramatic movie trailer narration. Speed=Slow + Delivery=Expressive High captures this. Pitch is ambiguous. |
| Distinctness | YES — Slow + Expressive High is a unique combination not covered by Calm (Slow + Expressive Low) or Energetic (Fast + Expressive High). |
| Underlying uncertainty | Is "Dramatic" actually Slow + Expressive High? Or is it just an emotion (Fear/Awe) with Normal prosody? Without user testing, this is speculative. |
| Recommendation | **UNDECIDED** — conceptually distinct but unverified. Needs user testing. |

---

### 3.7 Additional candidates considered

| Candidate | Assessment | Recommendation |
|-----------|------------|----------------|
| **Whispered** | Already covered by Whisper (Style=Whispering). Prosody doesn't need a separate "Whispered" preset. | REJECT (redundant) |
| **Aggressive** | Overlaps with Angry emotion + Shouting style. Prosody preset would be Fast + Expressive High, same as Energetic. | REJECT (redundant) |
| **Sad** | Already covered by Sad emotion. Prosody preset would be Slow + Low + Expressive Low, but this overlaps with Calm. | REJECT (redundant) |
| **Epic** | Slow + Normal + Expressive High — same as Dramatic. | REJECT (redundant) |
| **Playful** | Fast + High + Expressive High — same as Energetic. | REJECT (redundant) |

---

## 4. DISTINCTNESS MATRIX

|  | Natural | Calm | Energetic | Dramatic |
|--|---------|------|-----------|----------|
| **Natural** | — | Different (Slow+Low vs Normal) | Different (Fast+High vs Normal) | Different (Slow+Expressive High vs Normal) |
| **Calm** | Different | — | **Opposite** | Different (Expressive Low vs High) |
| **Energetic** | Different | **Opposite** | — | Different (Fast vs Slow) |
| **Dramatic** | Different | Different | Different | — |

**Finding**: Only 3 candidates are sufficiently distinct:
- **Natural** (all Normal)
- **Calm** (Slow + Expressive Low)
- **Energetic** (Fast + High + Expressive High)
- **Dramatic** (Slow + Expressive High) — borderline, needs verification

Soft, Strong, and all other candidates are either redundant or too ambiguous.

---

## 5. PROSODY PRESET COVERAGE ANALYSIS

If we use high-level presets, how much of the prosody space do they cover?

| Preset | Speed | Pitch | Delivery | Coverage |
|--------|-------|-------|----------|----------|
| Natural | Normal | Normal | Normal | 1 of 15 combinations |
| Calm | Slow | Normal | Expressive Low | 1 of 15 |
| Energetic | Fast | High | Expressive High | 1 of 15 |
| Dramatic | Slow | Normal | Expressive High | 1 of 15 |

**Total**: 4 of 15 possible prosody combinations (27%).

**Problem**: The remaining 73% of prosody space would require the user
to either:
(a) Use Advanced to set individual prosody values, OR
(b) Select "Custom" in Friendly and accept no preset label.

This means **high-level presets cover less than a third of the prosody
space**. The user would frequently fall into "Custom" territory.

---

## 6. REVERSIBILITY AND MIXED-STATE ANALYSIS

### If prosody presets are implemented:

| Scenario | Friendly shows | Problem |
|----------|---------------|---------|
| User selects "Calm" | Calm active | None |
| User changes Pitch to High in Advanced | **Custom** | User doesn't understand why Calm deactivated |
| User selects "Energetic" then changes Speed to Slow | **Custom** | Mixed state confusion |
| User wants Speed=Fast + Delivery=Expressive Low (unusual) | **Custom** | No preset matches — user has no Friendly label |

### If direct controls are kept:

| Scenario | Friendly shows | Problem |
|----------|---------------|---------|
| User sets Speed=Fast | Pace=Fast | None — clear |
| User sets Pitch=High | Pitch=High | None — clear |
| User sets Delivery=Expressive High | Delivery=High | None — clear |
| Any combination | Each control shows its value | No mixed state — always clear |

**Finding**: Direct controls NEVER produce a mixed state. Every
combination is representable. Presets introduce mixed-state confusion.

---

## 7. THE COGNITIVE LOAD QUESTION

### How many technical decisions does each option require?

**Option A — Three direct controls:**
- User makes 3 decisions: Pace, Pitch, Delivery
- Each decision is simple (pick from 3-5 options)
- Each decision is independent (no interaction to understand)
- **Total technical decisions: 3**

**Option B — High-level presets only:**
- User makes 1 decision: select a preset (Natural/Calm/Energetic/Dramatic)
- But: 4 presets cover only 27% of the space
- If the user wants anything else, they must understand the underlying
  prosody parameters ANYWAY (to set them in Advanced)
- **Total technical decisions: 1 (if preset matches) or 3+ (if it doesn't)**

**Option C — Hybrid (presets + individual controls):**
- User can start with a preset (1 decision) then fine-tune
- But: fine-tuning may break the preset → "Custom" confusion
- **Total technical decisions: 1-4 (variable)**

### Key insight:

**Presets don't reduce technical decisions — they defer them.**
If the preset matches the user's intent, it's 1 decision. If it
doesn't, the user must make the same 3 decisions PLUS understand
why the preset didn't work.

Direct controls are **honest**: 3 decisions, always clear, no hidden
complexity.

---

## 8. THE "DELIVERY" JARGON PROBLEM

The strongest argument for presets is that "Delivery" / "Expressive Low"
/ "Expressive High" is technical jargon. But this can be solved WITHOUT
presets:

### Proposed relabeling:

| Current (technical) | Proposed (user-friendly) | Rationale |
|--------------------|--------------------------|-----------|
| Delivery | **Expression** | "Expression" is more intuitive than "Delivery" |
| Expressive Low | **Subtle** | "Subtle" = less expressive, flatter — clear |
| Normal | **Natural** | "Natural" = default expressiveness |
| Expressive High | **Animated** | "Animated" = more expressive, lively — clear |

### Result:

```
PACE:     V.Slow  Slow  Normal  Fast  V.Fast
PITCH:    Low  Normal  High
EXPRESSION: Subtle  Natural  Animated
```

This eliminates the jargon problem without introducing preset coupling
or mixed-state confusion. The user makes 3 simple, independent decisions
with intuitive labels.

---

## 9. RECOMMENDATION

### Option A (Modified): Three direct controls with improved labels

```
PROSODY
├── Pace (snapping slider): V.Slow / Slow / Normal / Fast / V.Fast
├── Pitch (segmented): Low / Normal / High
└── Expression (segmented): Subtle / Natural / Animated
```

### Why NOT presets:

1. **Coverage**: 4 presets cover only 27% of the prosody space.
2. **Mixed state**: Presets introduce "Custom" confusion when the user
   changes any single parameter.
3. **Deferred complexity**: Presets don't eliminate technical decisions —
   they defer them to when the preset doesn't match.
4. **Reversibility**: Direct controls are always clear; presets create
   ambiguity about what changed.
5. **Redundancy**: Most high-level candidates (Soft, Strong, Aggressive,
   Sad, Epic, Playful) are either redundant with each other or
   redundant with emotion controls.

### Why improved labels:

1. **"Expression"** is more intuitive than "Delivery"
2. **"Subtle / Natural / Animated"** eliminates the "Expressive Low/High"
   jargon
3. **No coupling**: each control remains independent
4. **No mixed state**: every combination is representable
5. **Full coverage**: 100% of the prosody space is accessible

### Why NOT hybrid (presets + individual controls):

1. **Complexity**: adds a preset row that may conflict with individual
   controls
2. **Mixed state**: changing an individual control after selecting a
   preset creates "Custom" — confusing
3. **Limited benefit**: the 3 viable presets (Natural/Calm/Energetic)
   are already achievable with 1-2 clicks on the direct controls

---

## 10. FINAL PROSODY STRUCTURE

```
PROSODY
├── PACE
│   └── [snapping slider: 0.65x / 0.85x / 1.0x / 1.2x / 1.4x]
│       underlying: speed_very_slow / speed_slow / Normal / speed_fast / speed_very_fast
│
├── PITCH
│   └── [segmented: Low / Normal / High]
│       underlying: pitch_low / Normal / pitch_high
│
└── EXPRESSION
    └── [segmented: Subtle / Natural / Animated]
        underlying: expressive_low / Normal / expressive_high
```

### Properties:

| Property | Value |
|----------|-------|
| Number of controls | 3 |
| Technical decisions | 3 (each simple, independent) |
| Coverage | 100% of prosody space (5 × 3 × 3 = 45 combinations) |
| Mixed state | NEVER (every combination is representable) |
| Reversibility | FULL (each control reflects its underlying value) |
| Jargon | ELIMINATED (Subtle/Natural/Animated instead of Expressive Low/High) |
| Cognitive load | LOW (3 independent decisions, intuitive labels) |

---

## 11. COMPARISON SUMMARY

| Criterion | Direct (improved labels) | High-level presets | Hybrid |
|-----------|-------------------------|--------------------|----|
| Technical decisions | 3 (always) | 1 (if match) or 3+ (if not) | 1-4 (variable) |
| Coverage | 100% | ~27% | 100% (but with mixed state) |
| Mixed state | Never | Frequent | Frequent |
| Reversibility | Full | Complex (ownership tracking) | Complex |
| Jargon | Eliminated (Subtle/Natural/Animated) | Hidden behind preset names | Partially hidden |
| Cognitive load | Low (3 simple choices) | Low if preset matches, high if not | Variable |
| Implementation complexity | Low | Medium (ownership, mixed state) | High (both systems) |

---

## 12. DECISION

**KEEP three direct prosody controls with improved labels.**

```
PACE:        V.Slow / Slow / Normal / Fast / V.Fast
PITCH:       Low / Normal / High
EXPRESSION:  Subtle / Natural / Animated
```

### Rationale:

1. **Direct controls are honest**: 3 decisions, always clear, no hidden
   complexity.
2. **Presets defer complexity**: they don't eliminate technical decisions,
   they just postpone them to when the preset doesn't match.
3. **Presets cover only 27%**: the user would frequently hit "Custom"
   and have to understand prosody anyway.
4. **The jargon problem is solvable with relabeling**: "Expression"
   with "Subtle/Natural/Animated" is as intuitive as any preset name.
5. **No mixed state**: direct controls never produce the "Custom"
   confusion that presets create.
6. **Full coverage**: 100% of the prosody space is accessible without
   falling back to Advanced.

### What this does NOT do:

- Does NOT reduce the number of widgets (still 3 controls)
- Does NOT hide prosody behind abstract concepts

### What this DOES do:

- **Reduces technical decisions**: "Subtle/Natural/Animated" requires
  no understanding of "expressiveness" as a concept
- **Eliminates jargon**: no "Expressive Low/High" confusion
- **Maintains full control**: every prosody combination is accessible
- **Avoids mixed-state complexity**: no "Custom" label needed

---

## 13. EXCEPTION — EMOTION-COUPLED PRESETS (FUTURE)

The only case where prosody presets make sense is when they are
**coupled with emotions** (Phase 2, after user testing):

| Emotion | Prosody enhancement | Confidence |
|---------|--------------------|------------|
| Calm (Contentment) | Pace=Slow, Expression=Subtle | MEDIUM |
| Excited (Enthusiasm) | Pace=Fast, Pitch=High, Expression=Animated | MEDIUM |
| Tense (Fear?) | Pace=Slow, Expression=Animated | LOW |

In this model, the prosody is NOT a separate preset — it's part of the
**emotion preset**. The user selects "Calm" and gets Contentment +
Slow + Subtle as a package. The individual prosody controls still
exist below for fine-tuning.

But this is a **Phase 2 enhancement** that requires user testing to
validate the mappings. For the initial release, **three direct controls
with improved labels** is the safest, most honest, and most flexible
approach.

---

## 14. FINAL ANSWER

**Pace, Pitch, and Expression should remain three direct Friendly controls.**

- Do NOT replace them with high-level intent abstractions.
- Do NOT add a prosody preset row.
- DO relabel "Delivery" → "Expression" and "Expressive Low/High" →
  "Subtle/Animated" to eliminate jargon.
- DO keep the three controls independent (no coupling, no mixed state).
- DO consider emotion-coupled prosody presets in Phase 2 (after user
  testing validates the mappings).

**The goal is fewer technical decisions, not fewer widgets. Three
intuitive, independent controls (Pace / Pitch / Expression) achieve
this goal better than abstract presets that cover only 27% of the
space and introduce mixed-state confusion.**

---

## 15. NO CODE CHANGES

This is strictly an analysis task. No modifications were made to any
files. This document is a UX decision record that informs the
implementation specification.
