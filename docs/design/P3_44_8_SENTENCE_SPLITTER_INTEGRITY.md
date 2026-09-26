# P3.44.8 — Sentence Splitter Integrity
## Design Record (IMPLEMENTATION + REGRESSION PHASE)

Phase goal: fix the proven sentence-splitting regression and establish ONE
consistent, text-preserving, marker-safe sentence-boundary behaviour across
the whole generation pipeline — without introducing an NLP architecture.

Status: **COMPLETE** (implementation, regression matrix, old-code proof,
full battery, launch smoke — see §9).

---

## 1. The regression

Proven against the P3.44.7 tree (commit `b5d3c10`) by direct runtime probe
BEFORE any change:

| Input | Pre-P3.44.8 sentences | Correct |
|---|---|---|
| `A GLM 5.2-t használtam.` | `A GLM 5.` / `2-t használtam.` | 1 sentence |
| `Az érték 3.14.` | `Az érték 3.` / `14.` | 1 sentence |
| `Ez a v1.2 verzió.` | `Ez a v1.` / `2 verzió.` | 1 sentence |
| `A test.hu domain.` | `A test.` / `hu domain.` | 1 sentence |
| `U.S.A. is known for this.` | `U.` / `S.` / `A.` / `is known for this.` | 1 sentence |
| `Az ára 12.50 volt.` | `Az ára 12.` / `50 volt.` | 1 sentence |

A second defect in the same path: `_group_sentences()` joined sentence
fragments with `" ".join(...)`, so a text split mid-number was
reconstructed with an INSERTED space and the whitespace-normalised part
text went to the model:

```
original:  A GLM 5.2-t használtam.
generated: A GLM 5. 2-t használtam.     (space injected inside 5.2)
```

Newlines and multiple spaces between sentences were silently normalised
to single spaces in the same join.

A third defect (divergence): `PromptBuilder._detect_conflicts()` split
the COMPILED prompt with a third, independent rule
(`re.split(r'(?<=[.!?])\s+', prompt)`) that (a) detached an inline SFX
token compiled from `. {sfx:...}` into the NEXT sentence (conflict
attribution divergence — the `RA-UX` audit finding) and (b) ignored `…`
boundaries.

## 2. Historical cause (git evidence)

Commit **`9acefbe`** ("f395f4e8-9069-4316-8652-f16ed0a6b34a", P3.44.2,
2026-09-05) rewrote `NarrationSplitter._split_sentences` to gain SFX/pause
marker shielding+attachment. The diff replaced:

```python
# Split at sentence-ending punctuation followed by whitespace
raw_parts = self.SENTENCE_END_RE.split(text)   # (?<=[.!?…])\s+
sentences = [s.strip() for s in raw_parts if s.strip()]
```

with a bare punctuation-run scan:

```python
for m in self._PUNCT_RUN_RE.finditer(text):   # [.!?…]+
    ...  # marker shielding + attachment rules
    cuts.append(end)                           # NO whitespace/context check
```

The rewrite achieved the marker rules but **lost the requirement that
punctuation be followed by whitespace**; every `.` glued to a digit or
letter became a "sentence boundary". `HeuristicBlockDetector._split_sentences`
had used bare `[.!?…]+` (without the whitespace requirement) since before
P3.44.2 — the same defect class, pre-existing there (P3.44.2 only added its
marker rules). The `" ".join` reconstruction predates P3.44.2 but only
became a text-mutation bug in combination with the broken boundary rule.

The P3.44.5 audit logged this as **RA-C** (deferred by scope discipline);
P3.44.8 is its scheduled fix.

## 3. Sentence-splitting implementations found (splitter map)

| # | Implementation | Input | Output | Boundary rule (pre-fix) | Whitespace | Marker behaviour | Mutates text? |
|---|---|---|---|---|---|---|---|
| 1 | `engine/narration_splitter.py` `NarrationSplitter._split_sentences` (+ `_group_sentences`) | block/plain/raw text | sentence list → grouped part texts (SplitPart.text) | `[.!?…]+` run scan, marker shield/attach, **no context check** | segments stripped; groups joined `" "` | shield + attach (P3.44.2) | **YES** — `" ".join` injected spaces; ws normalised |
| 2 | `engine/block_detector.py` `HeuristicBlockDetector._split_sentences` | full editor text (Re-detect / block boundaries) | sentence dicts `{text, start, end}` | `[.!?…]+` run scan, marker shield/attach, **no context check** | end consumes trailing `" \t\r"`; `\n` left in `between` gap | shield + attach (P3.44.2) | no (offsets into source) |
| 3 | `engine/prompt_builder.py` `PromptBuilder._detect_conflicts` | COMPILED final prompt (markers already tokens) | sentence list for conflict attribution | `(?<=[.!?])\s+` (no `…`, **no marker awareness**) | split consumes the `\s+` | none — `<|sfx:...|>` after `. ` lands in the NEXT sentence | no (informational only) |

No other sentence-boundary logic exists in the codebase (verified by
searching `[.!?…]` patterns, `sentence` references, `_group_sentences`,
`_detect_conflicts`, `_INLINE_MARKER_RE`; `prompt_state.py` conflict logic
is text-wide, not sentence-level; `long_narration_dialog.py` consumes
SplitParts without its own boundary logic; `_split_with_speakers` is
line-based, not punctuation-based).

## 4. The authoritative boundary contract

New module **`engine/sentence_boundaries.py`** — the ONE scanner all three
implementations delegate to (`find_sentence_ends` / `split_sentence_units`
/ `split_sentences` / `split_tokenized_sentences`).

A punctuation RUN (`[.!?…]+`) ends a sentence iff:

1. **SHIELDING** — no character of the run lies inside an inline marker
   span (`{sfx:Name:Onom}` / `{pause}` / `{long_pause}`, or
   `<|sfx:tag|>Onom` / `<|prosody:pause|>` spans in the compiled
   representation). (P3.44.2 rule, unchanged.)
2. **ATTACHMENT** — a marker chain after the run — directly
   (`???{sfx:Laughter:Haha}`) or across whitespace (`. {sfx:...}`),
   possibly repeated — belongs to the sentence being ended; the boundary
   lands AFTER the chain. A run with an attached chain is ALWAYS a
   boundary. (P3.44.2 rule, unchanged — markers are never orphaned and
   never cut in half.)
3. **CONTEXT** — if no marker chain is attached, the run is a boundary
   ONLY when whitespace or end-of-text follows. This restores the
   pre-P3.44.2 `(?<=[.!?…])\s+` requirement that `9acefbe` lost.
4. **ABBREVIATION CONTINUATION** — a run of exactly ONE ASCII `.`
   followed by whitespace and then a lowercase letter does NOT end the
   sentence (`U.S.A. is known...`, `stb. ez...`). Runs containing `!`,
   `?` or `…` (including `...` and `?!`) keep boundary semantics —
   ellipsis remains a boundary when rule 3 says so (the intended
   semantics both before and during P3.44.2).

Genuine boundaries are unchanged: `Ez egy mondat. Ez a következő.`,
`!`, `?`, `…`, `???` runs, EOF — verified against the old-rule witness
and the full P3.44.2 marker suite.

## 5. Decimal / version / abbreviation / domain rules

All fall out of rule 3 + rule 4 (no token dictionary, no NLP):

- **Decimals** `3.14`, `12.50`: `.` glued between digits → no boundary.
  A decimal at sentence end (`Az érték 3.14.`) keeps the final `.` as a
  boundary (EOF).
- **Versions** `GLM 5.2`, `v1.2`, `5.2-t`: same digit/letter glue rule.
- **Domains / dotted identifiers** `test.hu`, `docs.python.org`,
  `192.168.1.1`, `config.yaml`: the `.` is followed by a letter/digit,
  never whitespace → no boundary.
- **Multi-initial abbreviations** `U.S.A.`: the internal dots are glued
  to uppercase letters (rule 3); the final `.` before a lowercase word
  is an abbreviation continuation (rule 4) → the initials AND the
  continuation stay in ONE sentence. `U.S.A.-t` (glued hyphen) is whole.
- **Title abbreviations** `Mr. Smith`, `Dr. Kovács`: `.` + whitespace +
  UPPERCASE word IS a boundary — exactly the pre-P3.44.2 and P3.44.2
  behaviour. No abbreviation dictionary introduced (smallest rule set
  mandated by the phase). See §10.

## 6. Exact-text preservation rule

Sentence splitting must preserve the exact source characters:

- Sentences are **exact source slices** (spans), never reconstructions.
- `split_sentence_units` returns `(sentence, separator)` pairs where the
  separator is the ORIGINAL whitespace run between two sentences.
- `NarrationSplitter._group_sentence_units` joins grouped sentences with
  their original separators (`sentence_i + sep_i + sentence_{i+1}`),
  replacing the old `" ".join`. A separator is consumed only when the
  sentence after it joins the same part; at a part boundary the separator
  is the split point (dropped, as before).
- Structural invariant (regression-locked):
  `"".join(t + sep for t, sep in split_sentence_units(text)) ==
  text.strip()`.
- Regression proof: `"A GLM 5.2-t használtam."` remains
  byte/character-identical in the generated sentence text AND in
  `SplitPart.text` (plain and raw modes). Multiple spaces (`"A.  B."`)
  and newline separators (`"A.\nB."`, `"A.\n\nB."`) survive grouping.

## 7. Inline marker preservation rule

> An inline marker belonging to a sentence remains part of that sentence
> during sentence splitting and grouping.

- Markers inside a sentence never split it (`{sfx:Sigh:Ahh}` mid-sentence
  stays in place; `{sfx:Laughter:Ha!ha}` punctuation is shielded).
- A marker chain after punctuation attaches to the sentence being ended
  (`Ez zseniális.{sfx:Sigh:Ahhj}` / `???{sfx:Laughter:Haha}` /
  `. {sfx:Sigh:Ahhj}`) — the marker never becomes an independent
  fragment and never migrates to the next sentence.
- In the COMPILED prompt the same holds for tokens:
  `<|sfx:tag|>Onom` / `<|prosody:pause|>` spans shield and attach
  identically (`TOKEN_SPAN_RE`), so `_detect_conflicts` no longer
  detaches an SFX token from its sentence.

## 8. Cross-implementation consistency

All three implementations now delegate to `engine.sentence_boundaries`:

| Implementation | Delegation |
|---|---|
| `NarrationSplitter._split_sentences` / `_split_sentence_units` | `sentence_boundaries.split_sentences` / `split_sentence_units` |
| `HeuristicBlockDetector._split_sentences` | `sentence_boundaries.find_sentence_ends` (+ its historical span mechanics: trailing `" \t\r"` into sentence end, `\n` left in the `between` gap for `_boundary_score`) |
| `PromptBuilder._detect_conflicts` | `sentence_boundaries.split_tokenized_sentences` (marker-equivalent spans on the compiled prompt) |

`_INLINE_MARKER_RE` in `prompt_builder` / `narration_splitter` /
`block_detector` is now an alias of the single
`sentence_boundaries.INLINE_MARKER_RE` object (the historical
"kept in sync" copies can no longer drift). Regression-locked: the
splitter, the detector and the conflict splitter agree on the boundary
count for the whole invariant corpus, and the SFX/pause token stays with
its sentence. The P3.44.2 SFX suite (28 tests) is green unchanged.

## 9. Tests

New permanent suite: `tests/test_p3_44_8_sentence_splitter_integrity.py`
— **62 tests**:

- §9 matrix: core boundaries (1–4), decimals (5–7), versions (8–10),
  domains (11–12), abbreviations (13–15), whitespace/exact text (16–20),
  inline markers (21–25), multi-sentence paragraphs (26–30).
- §10 cross-implementation agreement (splitter vs detector vs conflict
  splitter; SFX/pause token attachment; onomatopoeia shielding in the
  compiled representation).
- §11 source-span tests (no boundary inside `5.2` / `test.hu` /
  `U.S.A.` / `192.168.1.1`; detector span/text consistency).
- §13 invariants over a 26-text battery: no character insertion, no
  silent deletion, no reordering, marker conservation, boundary
  conservation, reconstruction roundtrip.
- §12 old-rule divergence witness: a characterisation of the
  pre-P3.44.8 punctuation-run rule proves the old rule fragments the
  defect corpus and mutates `"A GLM 5.2-t használtam."` →
  `"A GLM 5. 2-t használtam."`, while genuine-boundary controls agree
  between old and new.
- Pipeline surfaces: `split()` plain/raw exactness, part texts as exact
  source slices, part-count impact on decimal-heavy text
  (documented intended consequence), block→part identity
  (`source_block_id`, `block_index`) intact, parts end at sentence
  boundaries, oversize sentence → own part.

Old-code proof (git-extracted `b5d3c10` modules, one-off harness):
**OLD 2/15 defect detectors passed (controls 4/4); NEW 15/15 (controls
4/4)**. Decimal-heavy text part count: OLD 7 → NEW 4.

Regression battery: P3.44.8 62/62; P3.44.2 SFX 28/28 + P3.15 28/28;
P3.44.5/.6/.7 + batch suites 171/171; Scene output / Combine /
provenance / batch workflow suites 216/216; **full battery 1469/1469**
(excluding the documented pre-existing-broken `test_p3_30_ux_fixes.py`
— P3.44.5/6/7 precedent; its failures are identical on the pristine
P3.44.7 tree, proven by stash). Launch smoke (real entry path): 11/11.

## 10. Explicitly deferred edge cases

- **Title abbreviations** (`Mr. Smith`, `Dr. Kovács`, `stb. Ez...` with
  an uppercase continuation): still split at the period — identical to
  the pre-P3.44.2 baseline. A curated abbreviation dictionary was
  explicitly out of scope ("do not invent a large abbreviation
  dictionary"). Deferred to backlog; the lowercase-continuation rule
  (contract rule 4) covers the audit-proven `U.S.A. is...` case.
- **Sentence-initial lowercase after a period** (`Ez történt. és innen
  folytatódik.`): treated as an abbreviation continuation (one
  sentence) — the safe direction (no fragment starts lowercase), but it
  can grow a sentence. Non-standard orthography; documented residual.
- **Onomatopoeia containing spaces in the compiled representation**:
  `{sfx:Laughter:Ha! ha}` is shielded exactly in the editable text, but
  after compilation (`<|sfx:laughter|>Ha! ha`) the space-delimited tail
  is not part of the token span. Single-word onomatopoeia (the normal
  case) is fully covered.
- **Abbreviation-final period before an uppercase word** (`Az U.S.A.
  Elnöke...`): splits after `U.S.A.` — matches the pre-P3.44.2 baseline;
  the audit case used the lowercase continuation (`U.S.A. is known...`).
- **P3.44.9 (block offset/delete integrity) and P3.45 (UX/estimation)
  remain deferred**; the manual split/merge SFX/pause re-allocation
  finding from P3.44.7 remains deferred (it is a user-operation path,
  not the sentence splitter — independently inspected, not the same
  defect).
