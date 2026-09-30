"""SpeechStudio Engine — Duration Estimation (P3.45.2B).

THE canonical speech-duration estimate for the whole application.

Before P3.45.2B the same semantic quantity — "how many seconds of
audio is this text expected to need?" — was computed independently at
six sites with TWO different text bases:

  * the four ``NarrationSplitter`` split sites
    (``len(part_text) / 15.0`` — PLAIN text, no prepended tokens);
  * the editor Preview stats label
    (``len(final_prompt) / 15.0`` — the TOKENIZED prompt, counting
    ``<|emotion:...|>`` control tokens as speech: +25 % for a single
    Fear-override block, up to +102 % for token-heavy state — measured
    in the P3.45 triage);
  * ``engine/output_guard.preflight_generation_size`` (its own named
    constant ``ESTIMATED_CHARS_PER_SECOND = 15.0``, caller's text).

P3.45.2B makes the heuristic and its ONE semantic explicit:

  * ``ESTIMATED_CHARS_PER_SECOND`` — the single rate constant.  This is
    the project's existing ~15 chars/sec heuristic, UNCALIBRATED (no
    measurement corpus exists; recalibration is explicitly out of
    scope for P3.45.2B).  ``engine/output_guard`` imports this same
    object — there is no second constant anywhere.
  * ``estimate_speech_seconds(text)`` — the single function.  The
    semantic: an ESTIMATE (never a measurement, never a limit) of the
    speech time of the given text at the canonical rate.

Text-basis contract: every caller estimates the text it actually owns
at that site — the splitter estimates the plain PART text (the spoken
content, inline markers included, prepended control tokens excluded);
the Preview stats estimate the editor document's plain text; the
preflight estimates the caller-supplied request basis and REPORTS the
basis in its result so an estimate can never be presented as a
measurement.  Two intentionally different quantities (the estimate vs
the hard output ceiling in ``output_guard``) are NOT merged — they
live in separate modules with explicit names.

Product target (P3.45.2B): parts of approximately 20–25 seconds.
This is a PRODUCT TARGET — not a model hard limit, not a runtime
limit, not a truncation guarantee.  ``NarrationSplitter`` derives its
grouping target from this module's rate (``TARGET_PART_SECONDS`` ×
``ESTIMATED_CHARS_PER_SECOND``) so the splitter's char arithmetic and
the estimator can never drift apart again.

This module is dependency-free (no Qt, no engine state, no disk) and
pure (no mutation of any input).
"""
from __future__ import annotations

from typing import Optional

__all__ = [
    "ESTIMATED_CHARS_PER_SECOND",
    "estimate_speech_seconds",
]

# The house heuristic: characters of speech per second of audio.
# Single source of truth since P3.45.2B — engine/output_guard and every
# other consumer import THIS object (no duplicates anywhere).
#
# This is an APPROXIMATION, not a measurement: empirical project
# experience puts stable single-call generation around ~30 s with
# roughly ±10 s of prompt-complexity variation.  The heuristic is kept
# at the historical value of 15.0 — P3.45.2B explicitly does NOT
# recalibrate it (no measurement corpus exists for a new rate).
ESTIMATED_CHARS_PER_SECOND = 15.0


def estimate_speech_seconds(text: Optional[str]) -> float:
    """Canonical speech-duration ESTIMATE for ``text`` (seconds).

    Semantics (one meaning, everywhere):
      * INPUT  — the plain text the caller owns at its site (part text,
        document text, request basis).  Whatever string is given is
        measured as-is (``None``/empty → 0.0).
      * OUTPUT — an estimated number of seconds of audio at the
        canonical ~15 chars/sec heuristic rate.

    This is an ESTIMATE — never a measurement of generated audio and
    never a hard output limit (the engine ceiling lives in
    ``engine/output_guard`` as ``max_new_tokens / 25 fps``).

    Pure function: no state, no caching, no mutation.
    """
    if not text:
        return 0.0
    return len(text) / ESTIMATED_CHARS_PER_SECOND
