"""SpeechStudio Engine — Sentence Boundaries (P3.44.8).

THE authoritative sentence-boundary scanner for the whole application.
Before P3.44.8 there were three independent sentence-boundary
implementations that could diverge:

  1. ``NarrationSplitter._split_sentences`` (batch part production)
  2. ``HeuristicBlockDetector._split_sentences`` (Re-detect / block
     boundaries — feeds narration_block_manager)
  3. ``PromptBuilder._detect_conflicts`` (conflict warnings on the
     COMPILED prompt)

P3.44.2 (commit 9acefbe) had replaced the splitter's
``SENTENCE_END_RE.split`` — ``(?<=[.!?…])\\s+`` — with a bare
punctuation-run scan (``[.!?…]+``) to gain SFX/pause marker
shielding+attachment, and in doing so LOST the requirement that
sentence-ending punctuation be followed by whitespace.  Every ``.``
glued to a digit or letter became a "sentence boundary":

    "GLM 5.2"  ->  "GLM 5." / "2"
    "3.14"     ->  "3." / "14"
    "v1.2"     ->  "v1." / "2"
    "test.hu"  ->  "test." / "hu"
    "U.S.A."   ->  "U." / "S." / "A."

and ``_group_sentences`` then joined the fragments with
``" ".join(...)``, INSERTING a space inside the number:

    "A GLM 5.2-t használtam."  ->  "A GLM 5. 2-t használtam."

This module restores the whitespace requirement, adds the minimal
context rules the regression matrix needs, and makes exact-text
preservation structural (span-based, never reconstructed with invented
separators).  All three implementations now delegate here.

===========================================================================
THE BOUNDARY CONTRACT (deterministic, local, no external NLP)
===========================================================================

A punctuation RUN (one or more of ``. ! ? …``) ends a sentence iff ALL
of the following hold:

  1. SHIELDING — no character of the run lies inside an inline marker
     span (``{sfx:Name:Onom}`` / ``{pause}`` / ``{long_pause}``, or the
     compiled-prompt equivalents — see TOKEN_SPAN_RE).  Punctuation
     inside an onomatopoeia belongs to the sound effect.

  2. ATTACHMENT — a marker chain that follows the run — directly
     (``???{sfx:Laughter:Haha}``) or across whitespace
     (``. {sfx:...}``), possibly repeated — belongs to the sentence
     being ended: the boundary lands AFTER the chain.  A run with an
     attached marker chain is ALWAYS a boundary (P3.44.2 semantics,
     kept unchanged).  A marker is never orphaned into the next
     sentence and never cut in half.

  3. CONTEXT — if NO marker chain is attached, the run is a boundary
     only when the character immediately after the run is whitespace or
     the end of the text.  A period glued to a digit or letter
     (``3.14``, ``v1.2``, ``GLM 5.2``, ``test.hu``, ``U.S.A.``,
     ``192.168.1.1``, ``config.yaml``) is part of the token it sits in
     and never ends a sentence.

  4. ABBREVIATION CONTINUATION — a run consisting of exactly ONE ASCII
     ``.`` followed by whitespace and then a LOWERCASE letter does not
     end a sentence (``U.S.A. is known...``, ``stb. ez...``).  This is
     the smallest deterministic abbreviation rule that satisfies the
     regression matrix; no abbreviation dictionary is introduced.
     Runs that contain ``!``, ``?`` or ``…`` (``?!"``, ``...``,
     ``…``) keep their boundary semantics — ellipsis and multi-dot
     runs are boundaries when rule 3 says so, matching the intended
     semantics both before and during P3.44.2.

EXACT-TEXT PRESERVATION (structural):

  * ``split_sentence_units`` returns ``(sentence, separator)`` pairs
    where both strings are EXACT slices of the source.  The separator
    is the original whitespace run between two sentences — never
    normalised.  ``"".join(text + sep for text, sep in units)`` equals
    ``source.strip()`` byte-for-byte.

  * ``split_sentences`` returns the exact sentence texts.

This module is dependency-free (``re`` only) and every scan is a
single linear pass over compiled-regex matches.
"""
from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

__all__ = [
    "INLINE_MARKER_RE",
    "TOKEN_SPAN_RE",
    "find_sentence_ends",
    "split_sentence_units",
    "split_sentences",
    "split_tokenized_sentences",
]

# ---------------------------------------------------------------------------
# Marker spans
# ---------------------------------------------------------------------------
# Inline SFX/pause markers in the EDITABLE text representation.
# Single source of truth (engine.prompt_builder._INLINE_MARKER_RE,
# engine.narration_splitter.NarrationSplitter._INLINE_MARKER_RE and
# engine.block_detector._INLINE_MARKER_RE alias this object — the
# historical copies are kept as aliases for compatibility).
INLINE_MARKER_RE = re.compile(
    r"\{sfx:[^:}]+:[^}]+\}|\{pause\}|\{long_pause\}")

# Inline SFX/pause spans in the COMPILED prompt representation
# (PromptBuilder output: ``{sfx:Laughter:Haha}`` -> ``<|sfx:laughter|>Haha``).
# The token plus its GLUED onomatopoeia (``\S*``) forms one span so that
# (a) punctuation inside the onomatopoeia stays shielded, and (b) the
# attachment rule keeps the token with the sentence it annotates.
# Onomatopoeia containing spaces cannot be recovered exactly in the
# compiled representation — documented edge case (design doc §10).
TOKEN_SPAN_RE = re.compile(
    r"<\|sfx:\w+\|>\S*|<\|prosody:(?:long_)?pause\|>")

# A punctuation run: one or more sentence-ending punctuation marks.
_PUNCT_RUN_RE = re.compile(r"[.!?…]+")

# Whitespace skipped between a punctuation run and an attached marker
# chain (mirrors the historical P3.44.2 attachment behaviour).
_ATTACH_WS = " \t\r"


def _marker_at(pos: int, marker_spans: Sequence[Tuple[int, int]]) -> Optional[int]:
    """Return the end offset of the marker span starting at ``pos``."""
    for s, e in marker_spans:
        if s == pos:
            return e
    return None


# ---------------------------------------------------------------------------
# The authoritative scanner
# ---------------------------------------------------------------------------
def find_sentence_ends(text: str,
                       marker_spans: Optional[Sequence[Tuple[int, int]]] = None,
                       ) -> List[int]:
    """Return the sentence END offsets (exclusive) in ascending order.

    Implements the boundary contract documented in the module docstring.
    Offsets are positions in ``text`` as given (no stripping is done
    here — callers that need stripped semantics use the wrappers).

    Args:
        text: the text to scan (editable or compiled representation).
        marker_spans: precomputed inline-marker spans as
            ``(start, end)`` tuples.  When ``None`` they are computed
            from ``INLINE_MARKER_RE`` (the editable-text markers).  Pass
            spans from ``TOKEN_SPAN_RE`` for compiled prompts.
    """
    if not text:
        return []
    if marker_spans is None:
        marker_spans = [(m.start(), m.end())
                        for m in INLINE_MARKER_RE.finditer(text)]

    ends: List[int] = []
    for m in _PUNCT_RUN_RE.finditer(text):
        run_start, run_end = m.start(), m.end()
        run = m.group()

        # Rule 1 — SHIELDING: punctuation inside a marker never ends a
        # sentence.
        if any(s <= run_start < e for s, e in marker_spans):
            continue

        # Rule 2 — ATTACHMENT: a marker chain after the run (directly or
        # across whitespace) belongs to the sentence being ended; the
        # boundary lands after the chain.
        end = run_end
        attached = False
        while end < len(text):
            j = end
            while j < len(text) and text[j] in _ATTACH_WS:
                j += 1
            mk_end = _marker_at(j, marker_spans) if j < len(text) else None
            if mk_end is None:
                break
            end = mk_end
            attached = True
        if attached:
            ends.append(end)
            continue

        # Rule 3 — CONTEXT: without an attached marker chain the run
        # must be followed by whitespace or end-of-text.
        if run_end >= len(text) or text[run_end].isspace():
            # Rule 4 — ABBREVIATION CONTINUATION: a single ASCII "."
            # followed by whitespace + lowercase letter continues the
            # sentence ("U.S.A. is known...", "stb. ez...").
            if run == "." and run_end < len(text):
                k = run_end
                while k < len(text) and text[k].isspace():
                    k += 1
                if k < len(text) and text[k].islower():
                    continue
            ends.append(run_end)
    return ends


def _units_from_ends(text: str, ends: Sequence[int]) -> List[Tuple[str, str]]:
    """Build exact ``(sentence, separator)`` units from boundary ends.

    Shared by the editable-text and compiled-prompt splitters so the
    reconstruction invariant holds identically for both.
    """
    units: List[Tuple[str, str]] = []
    pos = 0            # start of the current sentence's span (may point at ws)
    prev_end = 0       # end of the previous sentence (punct/marker end)
    for end in ends:
        # The sentence's text starts at the first non-whitespace
        # character of its span; everything before that (from the
        # previous sentence's end) is the separator.
        start = pos
        while start < end and text[start].isspace():
            start += 1
        if start < end:
            if units:
                units[-1] = (units[-1][0], text[prev_end:start])
            units.append((text[start:end], ""))
            prev_end = end
        pos = end
    # Trailing sentence (no boundary punctuation after the last cut).
    start = pos
    while start < len(text) and text[start].isspace():
        start += 1
    if start < len(text):
        if units:
            units[-1] = (units[-1][0], text[prev_end:start])
        units.append((text[start:].rstrip(), ""))
    return units


def split_sentence_units(text: str) -> List[Tuple[str, str]]:
    """Split ``text`` into exact ``(sentence, separator)`` pairs.

    * ``sentence`` — an exact slice of ``text`` with no leading or
      trailing whitespace (a sentence ends at punctuation or an
      attached marker chain; it starts at the first non-whitespace
      character after the previous sentence).
    * ``separator`` — the EXACT whitespace run between this sentence
      and the next one in the source (``""`` for the final unit).

    Reconstruction invariant::

        "".join(t + sep for t, sep in split_sentence_units(text))
            == text.strip()

    Leading/trailing whitespace of the whole input is dropped (the
    historical whole-text ``strip()`` semantics); every character
    between the first and the last sentence is preserved byte-exactly.
    """
    if not text:
        return []
    ends = find_sentence_ends(text)
    return _units_from_ends(text, ends)


def split_sentences(text: str) -> List[str]:
    """Return the exact sentence texts of ``text`` (no separators)."""
    return [t for t, _ in split_sentence_units(text)]


def split_tokenized_sentences(prompt: str) -> List[str]:
    """Return the sentences of a COMPILED prompt.

    Same boundary contract, with inline SFX/pause spans taken from
    ``TOKEN_SPAN_RE`` so that ``<|sfx:laughter|>Haha`` /
    ``<|prosody:pause|>`` behave exactly like ``{sfx:Laughter:Haha}`` /
    ``{pause}`` did in the editable text: punctuation inside them is
    shielded and a token after sentence punctuation is attached to the
    sentence it annotates (never detached into the next one).
    """
    if not prompt:
        return []
    marker_spans = [(m.start(), m.end())
                    for m in TOKEN_SPAN_RE.finditer(prompt)]
    ends = find_sentence_ends(prompt, marker_spans)
    return [t for t, _ in _units_from_ends(prompt, ends)]
