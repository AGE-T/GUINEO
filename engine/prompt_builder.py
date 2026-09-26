"""
SpeechStudio Engine - Prompt Builder
=====================================

AI Development Rules, section 9:
    "Only PromptBuilder may create Higgs prompt tokens.
     The UI must never concatenate token strings.
     The Engine must never manually edit prompt syntax."

Prompt System, section 11:
    "The Prompt Builder must be the only component that assembles final
     Higgs tokens. The Prompt Builder receives: selected emotion, selected
     style, selected prosody, selected sound effect, plain text, reference
     voice data, generation settings.
     The Prompt Builder outputs: final prompt string, token preview,
     validation warnings."

Prompt System, section 8 (Token Placement Rules):
    Emotion            -> sentence start only
    Style              -> sentence start only
    Prosody (sentence) -> sentence start only
    Prosody (inline)   -> inserted at exact position
    Sound effects      -> inserted immediately before the onomatopoeia

Prompt System, section 10 (Conflict Handling):
    - Multiple emotions in one sentence block  -> warn
    - Multiple styles in one sentence block     -> warn
    - Conflicting pitch values                  -> warn
    - Conflicting speed values                  -> warn
    - Conflicting delivery values               -> warn
    - Duplicate SFX tags in same inline position-> warn

Inline Markers:
    The UI inserts human-readable markers into the editor text. These are
    NOT Higgs tokens - they are placeholders that only the PromptBuilder
    can convert to real tokens. This keeps the UI token-syntax-free while
    letting the user see where effects are placed.

    Marker format:
        {sfx:Laughter:Haha}    -> <|sfx:laughter|>Haha
        {pause}                -> <|prosody:pause|>
        {long_pause}           -> <|prosody:long_pause|>

Single responsibility: assemble and validate Higgs prompts. Nothing else.
"""

from __future__ import annotations
import re
from typing import List, Optional

from engine.narration_blocks import (
    EffectiveBlockState, TokenEmission, SfxInsertion, PauseInsertion,
)

from engine.logger import get_logger
from engine.errors import InvalidPrompt
from engine.higgs_tokens import (
    make_token, EMOTION_BY_NAME, EMOTION_BY_TAG, STYLE_BY_NAME,
    PROSODY_BY_NAME, SPEED_OPTIONS, PITCH_OPTIONS, DELIVERY_OPTIONS,
    PAUSE_OPTIONS, SFX_BY_NAME,
)
from engine.models import PromptData

logger = get_logger("prompt_builder")


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------
# Token regex (for validation of the final prompt - never for construction)
_TOKEN_RE = re.compile(r"<\|(\w+):(\w+)\|>")

# Inline marker regex (for parsing markers the UI inserts into the text)
# {sfx:Laughter:Haha}  ->  group(1)="Laughter", group(2)="Haha"
_SFX_MARKER_RE = re.compile(r"\{sfx:([^:}]+):([^}]+)\}")
# {pause} or {long_pause}
_PAUSE_MARKER_RE = re.compile(r"\{(pause|long_pause)\}")
# Any inline marker (SFX or pause) — used for span computation/shielding.
_INLINE_MARKER_RE = re.compile(
    r"\{sfx:[^:}]+:[^}]+\}|\{pause\}|\{long_pause\}")


def materialize_inline_markers(text, sfx_insertions=(), pause_insertions=()):
    """Materialize block SFX/pause METADATA into literal inline markers.

    P3.44.2 — THE single authoritative conversion from the structured
    representation (``PromptBlock.sfx_insertions`` / ``pause_insertions``,
    offset-based) to the editable in-text marker representation
    (``{sfx:Name:Onom}`` / ``{pause}``).  Both the Preview pipeline
    (``PromptBuilder._process_block_inline`` via compile_continuous) and
    the Batch pipeline (``NarrationSplitter.split``) call THIS function,
    so the two pipelines can never diverge in how metadata becomes text.

    Semantics (unchanged from the original preview implementation):
      - insertions are applied in reverse offset order;
      - each marker is inserted as ``" " + marker + " "`` (padding);
      - an insertion whose offset already points at (or sits inside) an
        EXISTING in-text marker is SKIPPED — the same SFX must never be
        materialized twice (double-insertion guard).

    Args:
        text: the block (or full) text WITHOUT the markers.
        sfx_insertions: iterable of SfxInsertion (offsets relative to text).
        pause_insertions: iterable of PauseInsertion (offsets relative to text).

    Returns:
        (new_text, materialized_count)
    """
    if not sfx_insertions and not pause_insertions:
        return text, 0

    def _covered_by_marker(offset: int, txt: str) -> bool:
        # An in-text marker that covers the insertion offset (or that the
        # offset points directly before/after) makes this insertion
        # redundant — the same SFX must never be materialized twice.
        for m in _INLINE_MARKER_RE.finditer(txt):
            if m.start() - 1 <= offset <= m.end():
                return True
        # Offsets beyond the (un-materialized) text are corrupt — ignore.
        if offset < 0 or offset > len(txt):
            return True
        return False

    events = []
    for ins in sfx_insertions:
        events.append((ins.offset,
                       PromptBuilder.make_sfx_marker(ins.sfx_name,
                                                      ins.onomatopoeia)))
    for ins in pause_insertions:
        events.append((ins.offset, PromptBuilder.make_pause_marker(
            ins.pause_type)))
    # Reverse offset order so earlier insertions never invalidate later
    # offsets (identical to the original preview implementation).
    events.sort(key=lambda ev: ev[0], reverse=True)

    count = 0
    for offset, marker in events:
        # Guard is re-evaluated against the CURRENT text so an insertion
        # placed by a previous event shields overlapping offsets too.
        if _covered_by_marker(offset, text):
            continue
        text = text[:offset] + " " + marker + " " + text[offset:]
        count += 1
    return text, count


class PromptBuilder:
    """Assembles validated Higgs Audio V3 prompts from user selections.

    This is the ONLY class in the entire application that calls make_token()
    or constructs Higgs token syntax. No other module may import
    make_token or build token strings.

    The builder converts inline markers ({sfx:...}, {pause}, {long_pause})
    in the editor text into proper Higgs tokens, prepends sentence-level
    tokens (emotion, style, prosody), and returns the final prompt.
    """

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------
    def build(
        self,
        text: str,
        emotion: Optional[str] = None,
        style: Optional[str] = None,
        speed: Optional[str] = None,
        pitch: Optional[str] = None,
        delivery: Optional[str] = None,
        sfx_insertions: Optional[List[dict]] = None,
        pause_insertions: Optional[List[dict]] = None,
        allow_sfx: bool = True,
    ) -> PromptData:
        """Construct a validated Higgs prompt from user selections.

        The text may contain inline markers:
            {sfx:Laughter:Haha}  ->  <|sfx:laughter|>Haha
            {pause}              ->  <|prosody:pause|>
            {long_pause}         ->  <|prosody:long_pause|>

        These markers are converted to proper Higgs tokens. The UI never
        writes token syntax directly.

        Args:
            text: the user text (may contain inline markers).
            emotion: emotion name (e.g. "Awe"), or None.
            style: style name (e.g. "Whispering"), or None.
            speed: speed option name (e.g. "Slow"), or None.
            pitch: pitch option name (e.g. "Low"), or None.
            delivery: delivery option name (e.g. "Expressive High"), or None.
            sfx_insertions: (legacy, optional) list of SFX insertion dicts.
            pause_insertions: (legacy, optional) list of pause insertion dicts.
            allow_sfx: when False, {sfx:Name:Onomatopoeia} markers are
                replaced with just the onomatopoeia text (no SFX token
                emitted). When True (default), markers are converted
                to tokens normally.

        Returns:
            PromptData with the final prompt, preview, and warnings.

        Raises:
            InvalidPrompt: if the prompt is empty or contains blocking errors.
        """
        warnings: List[str] = []
        token_preview: List[str] = []

        # --- Validate text ---
        if not text or not text.strip():
            raise InvalidPrompt("The prompt text is empty.",
                                "Enter some text to synthesise.")

        # --- Validate and collect sentence-level tokens ---
        sentence_tokens: List[str] = []

        if emotion is not None:
            emo = EMOTION_BY_NAME.get(emotion)
            if emo is None:
                raise InvalidPrompt(
                    "Unknown emotion: {0}".format(emotion),
                    "Select one of the supported emotions from the panel.",
                )
            token = make_token("emotion", emo.tag)
            sentence_tokens.append(token)
            token_preview.append("Emotion: {0} -> {1}".format(emotion, token))

        if style is not None:
            st = STYLE_BY_NAME.get(style)
            if st is None:
                raise InvalidPrompt(
                    "Unknown style: {0}".format(style),
                    "Select one of: Singing, Whispering, Shouting.",
                )
            token = make_token("style", st.tag)
            sentence_tokens.append(token)
            token_preview.append("Style: {0} -> {1}".format(style, token))

        # Prosody sentence-level tokens: speed, pitch, delivery.
        if speed is not None and speed != "Normal":
            spd = PROSODY_BY_NAME.get(speed)
            if spd is None or spd.category != "speed":
                raise InvalidPrompt(
                    "Unknown speed option: {0}".format(speed),
                    "Select a valid speed option.",
                )
            token = make_token("prosody", spd.tag)
            sentence_tokens.append(token)
            token_preview.append("Speed: {0} -> {1}".format(speed, token))

        if pitch is not None and pitch != "Normal":
            ptc = PROSODY_BY_NAME.get(pitch)
            if ptc is None or ptc.category != "pitch":
                raise InvalidPrompt(
                    "Unknown pitch option: {0}".format(pitch),
                    "Select a valid pitch option.",
                )
            token = make_token("prosody", ptc.tag)
            sentence_tokens.append(token)
            token_preview.append("Pitch: {0} -> {1}".format(pitch, token))

        if delivery is not None and delivery != "Normal":
            dlv = PROSODY_BY_NAME.get(delivery)
            if dlv is None or dlv.category != "delivery":
                raise InvalidPrompt(
                    "Unknown delivery option: {0}".format(delivery),
                    "Select a valid delivery option.",
                )
            token = make_token("prosody", dlv.tag)
            sentence_tokens.append(token)
            token_preview.append("Delivery: {0} -> {1}".format(delivery, token))

        # --- Convert inline markers to Higgs tokens ---
        final_text = text

        # SFX markers: {sfx:Laughter:Haha} -> <|sfx:laughter|>Haha
        # When allow_sfx=False, replace {sfx:Name:Onom} with just Onom
        # (no SFX token emitted — the onomatopoeia remains as plain text).
        sfx_count = 0

        def _replace_sfx_marker(match):
            nonlocal sfx_count
            sfx_name = match.group(1).strip()
            onomatopoeia = match.group(2).strip()

            sfx = SFX_BY_NAME.get(sfx_name)
            if sfx is None:
                warnings.append(
                    "Unknown sound effect in marker: {0}".format(sfx_name)
                )
                return ""  # remove unknown marker

            if not allow_sfx:
                # SFX suppressed — keep only the onomatopoeia text.
                # No token emitted, no count increment.
                return onomatopoeia

            token = make_token("sfx", sfx.tag)
            sfx_block = token + onomatopoeia
            sfx_count += 1
            token_preview.append(
                "SFX: {0} -> {1}".format(sfx_name, sfx_block)
            )
            return sfx_block

        final_text = _SFX_MARKER_RE.sub(_replace_sfx_marker, final_text)

        # Pause markers: {pause} -> <|prosody:pause|>, {long_pause} -> <|prosody:long_pause|>
        pause_count = 0

        def _replace_pause_marker(match):
            nonlocal pause_count
            ptag = match.group(1)

            # Find the pause prosody definition by tag
            pd = None
            for p in PAUSE_OPTIONS:
                if p.tag == ptag:
                    pd = p
                    break

            if pd is None:
                warnings.append(
                    "Unknown pause type in marker: {0}".format(ptag)
                )
                return ""

            token = make_token("prosody", pd.tag)
            pause_count += 1
            token_preview.append(
                "Pause: {0} -> {1}".format(pd.name, token)
            )
            return token

        final_text = _PAUSE_MARKER_RE.sub(_replace_pause_marker, final_text)

        # --- Check for leftover markers (malformed) ---
        leftover_markers = re.findall(r"\{[^}]+\}", final_text)
        for marker in leftover_markers:
            warnings.append("Unrecognised marker in text: {0}".format(marker))

        # --- Assemble the final prompt ---
        # Sentence-level tokens go at the start (no space between them),
        # followed immediately by the text with inline tokens.
        prefix = "".join(sentence_tokens)
        final_prompt = (prefix + final_text) if prefix else final_text

        # --- Conflict detection (Prompt System section 10) ---
        conflict_warnings = self._detect_conflicts(final_prompt)
        warnings.extend(conflict_warnings)

        # --- Post-build validation ---
        post_warnings = self._validate_final_prompt(final_prompt)
        warnings.extend(post_warnings)

        # Remove duplicate warnings while preserving order
        seen = set()
        unique_warnings = []
        for w in warnings:
            if w not in seen:
                seen.add(w)
                unique_warnings.append(w)

        logger.info("Prompt built: %d sentence tokens, %d SFX, %d pauses, %d warnings",
                    len(sentence_tokens), sfx_count, pause_count, len(unique_warnings))
        if unique_warnings:
            for w in unique_warnings:
                logger.warning("prompt_builder: %s", w)

        return PromptData(
            final_prompt=final_prompt,
            plain_text=text,
            token_preview=token_preview,
            warnings=unique_warnings,
        )

    # ------------------------------------------------------------------
    # Conflict detection (Prompt System section 10)
    # ------------------------------------------------------------------
    def _detect_conflicts(self, prompt: str) -> List[str]:
        """Detect conflicting tokens in the final prompt.

        Checks for:
        - Multiple emotion tokens in one sentence
        - Multiple style tokens in one sentence
        - Conflicting pitch values
        - Conflicting speed values
        - Conflicting delivery values
        - Duplicate SFX at the same position
        """
        warnings: List[str] = []

        # Split into sentences (rough split on . ! ? followed by space or end)
        sentences = re.split(r'(?<=[.!?])\s+', prompt)

        for i, sentence in enumerate(sentences):
            # Find all tokens in this sentence
            emotions = []
            styles = []
            speeds = []
            pitches = []
            deliveries = []
            sfxs = []

            for match in _TOKEN_RE.finditer(sentence):
                category, tag = match.group(1), match.group(2)
                if category == "emotion":
                    emotions.append(tag)
                elif category == "style":
                    styles.append(tag)
                elif category == "prosody":
                    # Determine sub-category from tag
                    if tag.startswith("speed_"):
                        speeds.append(tag)
                    elif tag.startswith("pitch_"):
                        pitches.append(tag)
                    elif tag.startswith("expressive_"):
                        deliveries.append(tag)
                elif category == "sfx":
                    sfxs.append(tag)

            # Multiple emotions in one sentence
            if len(emotions) > 1:
                warnings.append(
                    "Sentence {0}: multiple emotions ({1}). Only one emotion per sentence is recommended.".format(
                        i + 1, ", ".join(emotions))
                )

            # Multiple styles in one sentence
            if len(styles) > 1:
                warnings.append(
                    "Sentence {0}: multiple styles ({1}). Only one style per sentence is recommended.".format(
                        i + 1, ", ".join(styles))
                )

            # Conflicting speed values
            if len(set(speeds)) > 1:
                warnings.append(
                    "Sentence {0}: conflicting speed values ({1}).".format(
                        i + 1, ", ".join(speeds))
                )

            # Conflicting pitch values
            if len(set(pitches)) > 1:
                warnings.append(
                    "Sentence {0}: conflicting pitch values ({1}).".format(
                        i + 1, ", ".join(pitches))
                )

            # Conflicting delivery values
            if len(set(deliveries)) > 1:
                warnings.append(
                    "Sentence {0}: conflicting delivery values ({1}).".format(
                        i + 1, ", ".join(deliveries))
                )

        return warnings

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate_final_prompt(self, prompt: str) -> List[str]:
        """Check the assembled prompt for issues. Returns warnings only."""
        warnings: List[str] = []

        # Check for unknown token tags.
        for match in _TOKEN_RE.finditer(prompt):
            category, tag = match.group(1), match.group(2)
            if not _is_known_token(category, tag):
                warnings.append(
                    "Unknown token in prompt: <{0}:{1}>".format(category, tag)
                )

        # Check prompt length
        if len(prompt) > 5000:
            warnings.append(
                "Prompt is very long ({0} chars). Generation may be slow or truncated.".format(
                    len(prompt))
            )

        return warnings

    # ------------------------------------------------------------------
    # Preview parsing (for the Prompt Preview panel)
    # ------------------------------------------------------------------
    def parse_preview(self, prompt: str) -> List[str]:
        """Parse a final prompt string into a human-readable token list.

        Used by the Prompt Preview panel to show what tokens are present.
        This does NOT construct tokens - it only reads existing ones.
        """
        preview: List[str] = []
        for match in _TOKEN_RE.finditer(prompt):
            category, tag = match.group(1), match.group(2)
            label = _token_label(category, tag)
            preview.append("{0}: <{1}:{2}>".format(label, category, tag))
        return preview

    # ------------------------------------------------------------------
    # Marker helpers (used by the UI)
    # ------------------------------------------------------------------
    @staticmethod
    def make_sfx_marker(sfx_name: str, onomatopoeia: str) -> str:
        """Return the inline marker string for an SFX insertion.

        The UI calls this when the user clicks an SFX button. The returned
        marker is inserted into the editor text. Only the PromptBuilder
        converts it to a real Higgs token.
        """
        return "{{sfx:{0}:{1}}}".format(sfx_name, onomatopoeia)

    @staticmethod
    def make_pause_marker(pause_type: str) -> str:
        """Return the inline marker string for a pause insertion.

        pause_type: "pause" or "long_pause"
        """
        return "{{{0}}}".format(pause_type)

    @staticmethod
    def strip_markers(text: str) -> str:
        """Remove all inline markers from text (for plain-text display)."""
        text = _SFX_MARKER_RE.sub("", text)
        text = _PAUSE_MARKER_RE.sub("", text)
        return text

    # ------------------------------------------------------------------
    # Build from Optimized Emissions (Narration Block system)
    # ------------------------------------------------------------------
    def build_from_emissions(
        self,
        states: List[EffectiveBlockState],
        emissions: List[TokenEmission],
    ) -> str:
        """Build the final Higgs prompt from an optimized emission plan.

        P3.25 (audit SS-H07): emission values are validated against the
        HIGGS V3 catalogue. An UNKNOWN value (e.g. a corrupted block
        override) is REJECTED — no token is emitted for it — and a
        warning is recorded in ``self.last_warnings`` (reset on every
        build call) so callers can surface it to the user. Previously an
        unknown value fell through to ``value.lower()`` and produced a
        GARBAGE token (``<|emotion:bogus|>``) that reached the model.
        """
        self.last_warnings: List[str] = []
        if not states:
            return ""
        emissions_by_block = {}
        for em in emissions:
            emissions_by_block.setdefault(em.block_index, []).append(em)
        parts = []
        for state in states:
            prefix = ""
            for em in emissions_by_block.get(state.block_index, []):
                tag = self._resolve_emission_tag(em)
                if tag:
                    prefix += make_token(em.category, tag)
            block_text = self._process_block_inline(state)
            parts.append(prefix + block_text)
        final_prompt = " ".join(parts)
        logger.info("Built prompt from %d blocks, %d emissions: %d chars",
                    len(states), len(emissions), len(final_prompt))
        return final_prompt

    def _resolve_emission_tag(self, emission: TokenEmission) -> str:
        """Return the HIGGS tag for an emission value, or "" when unknown.

        P3.25 (SS-H07): unknown values emit NO token (reject) and record a
        warning — one deterministic policy for every ingestion path
        (block override, global, preset, scene data).
        """
        if emission.category == "emotion":
            emo = EMOTION_BY_NAME.get(emission.value)
            if emo is None:
                self._warn_invalid("emotion", emission.value)
                return ""
            return emo.tag
        if emission.category == "style":
            st = STYLE_BY_NAME.get(emission.value)
            if st is None:
                self._warn_invalid("style", emission.value)
                return ""
            return st.tag
        if emission.category == "prosody":
            pd = PROSODY_BY_NAME.get(emission.value)
            if pd is None:
                self._warn_invalid("prosody", emission.value)
                return ""
            return pd.tag
        self._warn_invalid(emission.category, emission.value)
        return ""

    def _warn_invalid(self, category: str, value) -> None:
        """Record a warning for an invalid semantic value (SS-H07)."""
        msg = ("Invalid {0} value '{1}' — no {0} token was emitted. "
               "Select a supported value from the panel.".format(
                   category, value))
        if not hasattr(self, "last_warnings"):
            self.last_warnings = []
        if msg not in self.last_warnings:
            self.last_warnings.append(msg)
        logger.warning("PromptBuilder: %s", msg)

    def _process_block_inline(self, state: EffectiveBlockState) -> str:
        # P3.44.2: metadata materialization now goes through the SINGLE
        # authoritative helper (materialize_inline_markers) — the same
        # conversion the Batch pipeline uses, so Preview and Batch can
        # never interpret block SFX/pause metadata differently. The
        # double-insertion guard also protects the previously-corrupting
        # case of a block carrying BOTH metadata and in-text markers.
        text, _ = materialize_inline_markers(
            state.text, state.sfx_insertions, state.pause_insertions)
        text = _SFX_MARKER_RE.sub(self._convert_sfx_marker, text)
        text = _PAUSE_MARKER_RE.sub(self._convert_pause_marker, text)
        return text

    def _convert_sfx_marker(self, match) -> str:
        sfx_name = match.group(1).strip()
        onomatopoeia = match.group(2).strip()
        sfx = SFX_BY_NAME.get(sfx_name)
        if sfx is None:
            return ""
        token = make_token("sfx", sfx.tag)
        return token + onomatopoeia

    def _convert_pause_marker(self, match) -> str:
        ptag = match.group(1)
        pd = None
        for p in PAUSE_OPTIONS:
            if p.tag == ptag:
                pd = p
                break
        if pd is None:
            return ""
        token = make_token("prosody", pd.tag)
        return token


# ---------------------------------------------------------------------------
# Token knowledge helpers (validation only)
# ---------------------------------------------------------------------------
def _is_known_token(category: str, tag: str) -> bool:
    """Check whether a token is officially supported."""
    if category == "emotion":
        return tag in {e.tag for e in EMOTION_BY_NAME.values()}
    if category == "style":
        return tag in {s.tag for s in STYLE_BY_NAME.values()}
    if category == "prosody":
        return tag in {p.tag for p in PROSODY_BY_NAME.values()}
    if category == "sfx":
        return tag in {s.tag for s in SFX_BY_NAME.values()}
    return False


def _token_label(category: str, tag: str) -> str:
    """Return a human-readable label for a token."""
    if category == "emotion":
        emo = EMOTION_BY_TAG.get(tag)
        return emo.name if emo else "Emotion"
    if category == "style":
        st = next((s for s in STYLE_BY_NAME.values() if s.tag == tag), None)
        return st.name if st else "Style"
    if category == "prosody":
        pd = next((p for p in PROSODY_BY_NAME.values() if p.tag == tag), None)
        if pd:
            return "{0} ({1})".format(pd.name, pd.category.capitalize())
        return "Prosody"
    if category == "sfx":
        sfx = next((s for s in SFX_BY_NAME.values() if s.tag == tag), None)
        return sfx.name if sfx else "Sound Effect"
    return category.capitalize()
