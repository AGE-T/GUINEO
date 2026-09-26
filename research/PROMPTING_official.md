# Writing target text for Higgs Audio v3 TTS — control tags

How to embed control tags in the `input` text to steer emotion, prosody, style, and sound effects.
For where this fits in the overall workflow, see [AGENTS.md](./AGENTS.md).

## Format rule (read first)

Every tag is `<|category:tag|>`. There are **two placements**:

- **Sentence-level** — emotion, style, and prosody's `speed_* / pitch_* / expressive_*`.
  Put at the **start of the sentence**; it colors the whole sentence.
- **Inline** — sound effects (`sfx`) and prosody's `pause / long_pause`.
  Insert **at the exact position** in the sentence where the effect should occur.

**`sfx` gotcha:** format is `<|sfx:tag|>onomatopoeia, then the line` — the tag comes **first**,
immediately followed by the onomatopoeia with **no space** between them.

## Examples

Sentence-level:
```
<|emotion:elation|>Welcome aboard, we are absolutely thrilled to have you here!
<|style:whispering|>Come closer, I have a little secret to share.
<|prosody:speed_slow|>Take your time, there's really no need to rush.
```

Inline sfx (tag first, onomatopoeia attached, no space):
```
<|sfx:cough|>Ahem, welcome everyone, let's get started.
<|sfx:laughter|>Haha, so glad you could make it!
```

Inline pause (between phrases):
```
Hello there <|prosody:pause|> and welcome to the show.
```

Stacking tags (sentence-level emotion + inline sfx in one line):
```
<|emotion:elation|><|sfx:laughter|>Haha, welcome, welcome, we're so happy you're here!
<|sfx:sigh|>Haah, what a day — but welcome, please make yourself at home.
```

## Tips

- You can stack tags in one sentence (e.g. a leading emotion tag plus an inline sfx).
- `speed_very_slow` only slows the model to roughly ~5s; for slower delivery, insert
  `<|prosody:long_pause|>` between phrases instead.
- Only the tags below are recognized — anything else degrades output or gets read literally.

## Full tag catalog (43)

### Emotion (21) — sentence-level
`affection`, `amusement`, `anger`, `arousal`, `awe`, `bitterness`, `confusion`, `contemplation`,
`contentment`, `determination`, `disgust`, `elation`, `enthusiasm`, `fear`, `helplessness`,
`longing`, `pride`, `relief`, `sadness`, `shame`, `surprise`

Syntax: `<|emotion:elation|>`

### Prosody (10)
- Sentence-level: `speed_very_slow`, `speed_slow`, `speed_fast`, `speed_very_fast`,
  `pitch_low`, `pitch_high`, `expressive_high`, `expressive_low`
- Inline: `pause`, `long_pause`

Syntax: `<|prosody:speed_slow|>`, `<|prosody:pause|>`

### Style (3) — sentence-level
`singing`, `shouting`, `whispering`

Syntax: `<|style:whispering|>`

### Sound effects (9) — inline
`cough`, `laughter`, `crying`, `screaming`, `burping`, `humming`, `sigh`, `sniff`, `sneeze`

Syntax: `<|sfx:cough|>Ahem, ...` (tag first, onomatopoeia attached, no space)
