"""
SpeechStudio Engine - Semantic Token State
==========================================

This module implements the semantic token parsing, state management,
and canonical prompt compilation described in the Prompt Pipeline Contract.

Architecture:
    SemanticTokenParser  — parses <|...|> tokens from text into structured state
    SemanticTokenState   — holds the imported token state + stale tracking
    CanonicalPromptCompiler — compiles final prompt using Inline > Block > Global precedence

Key principles:
    - Manual tokens in editor text are NOT active until Read Tokens is pressed
    - After Read Tokens, tokens become semantic state
    - If text changes after Read Tokens, state becomes STALE
    - Precedence: Inline token > Block override > Global default
    - Conflicts are detected and surfaced as warnings
    - Raw Mode bypasses all of this — editor text is literal
"""

from __future__ import annotations
import re
import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

from engine.higgs_tokens import (
    EMOTION_BY_TAG, STYLE_BY_NAME, PROSODY_BY_NAME,
    SFX_BY_TAG, SFX_BY_NAME, PAUSE_OPTIONS, PROSODY_TOKENS,
)

# Build PROSODY_BY_TAG locally (not exported by higgs_tokens)
PROSODY_BY_TAG = {p.tag: p for p in PROSODY_TOKENS}
from engine.models import PromptData
from engine.logger import get_logger

logger = get_logger("prompt_state")


# ---------------------------------------------------------------------------
# Token regex — matches <|category:tag|>
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"<\|(\w+):(\w+)\|>")


# ---------------------------------------------------------------------------
# Parsed token — one token found in the text
# ---------------------------------------------------------------------------
@dataclass
class ParsedToken:
    """A single Higgs token parsed from editor text."""
    category: str   # "emotion", "style", "prosody", "sfx"
    tag: str        # the tag value, e.g. "fear", "whispering", "pitch_high"
    offset: int     # character offset in the original text
    raw: str        # the original <|...|> string
    # Placement type (Section 7/8 of the spec):
    # "sentence_level" — emotion, style, prosody (speed/pitch/delivery)
    #   These belong at the sentence boundary.
    # "inline_positional" — SFX, pause
    #   These must remain at their exact position in the text.
    placement: str = "sentence_level"
    # Scope information (Section 4/14):
    # sentence_identity: which sentence this token belongs to (0-based index)
    sentence_identity: int = 0
    # block_identity: which Narration Block this token falls within (None if no blocks)
    block_identity: Optional[str] = None
    # source: where this token came from
    source: str = "manual_import"

    @property
    def prosody_subcategory(self) -> Optional[str]:
        """For prosody tokens, return the sub-category (speed/pitch/delivery/pause)."""
        if self.category != "prosody":
            return None
        if self.tag.startswith("speed_"):
            return "speed"
        if self.tag.startswith("pitch_"):
            return "pitch"
        if self.tag.startswith("expressive_"):
            return "delivery"
        if self.tag in ("pause", "long_pause"):
            return "pause"
        return None

    @property
    def human_value(self) -> str:
        """Return the human-readable value (e.g. 'Fear' for emotion:fear)."""
        if self.category == "emotion":
            emo = EMOTION_BY_TAG.get(self.tag)
            return emo.name if emo else self.tag
        if self.category == "style":
            from engine.higgs_tokens import STYLE_BY_NAME
            for s in STYLE_BY_NAME.values():
                if s.tag == self.tag:
                    return s.name
            return self.tag
        if self.category == "prosody":
            pd = PROSODY_BY_TAG.get(self.tag)
            return pd.name if pd else self.tag
        if self.category == "sfx":
            sfx = SFX_BY_TAG.get(self.tag)
            return sfx.name if sfx else self.tag
        return self.tag


# ---------------------------------------------------------------------------
# Semantic token state — the imported token configuration
# ---------------------------------------------------------------------------
@dataclass
class SemanticTokenState:
    """Holds the semantic token state imported via Read Tokens.

    Before Read Tokens: this is empty/default.
    After Read Tokens: this holds the parsed tokens from the editor text.
    After text change: stale=True (tokens may no longer match the text).
    """
    # Parsed tokens from the last Read Tokens import
    tokens: List[ParsedToken] = field(default_factory=list)

    # Resolved effective values (per scope: global or inline)
    emotion: Optional[str] = None
    style: Optional[str] = None
    speed: Optional[str] = None
    pitch: Optional[str] = None
    delivery: Optional[str] = None

    # SFX and pause markers are in the text itself (as {sfx:...} / {pause})
    # — they don't need to be in semantic state because PromptBuilder
    # processes them from the text. But we track if any were found.
    has_sfx: bool = False
    has_pause: bool = False

    # Stale tracking
    imported: bool = False       # True after Read Tokens succeeds
    stale: bool = False          # True if text changed after import
    imported_text_hash: str = "" # SHA-256 of text at import time

    # Conflicts detected during parsing
    conflicts: List[str] = field(default_factory=list)

    def reset(self):
        """Clear all imported state (e.g. when entering Raw Mode)."""
        self.tokens.clear()
        self.emotion = None
        self.style = None
        self.speed = None
        self.pitch = None
        self.delivery = None
        self.has_sfx = False
        self.has_pause = False
        self.imported = False
        self.stale = False
        self.imported_text_hash = ""
        self.conflicts.clear()

    def check_stale(self, current_text: str) -> bool:
        """Check if the current text differs from the imported text."""
        if not self.imported:
            return False
        current_hash = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
        if current_hash != self.imported_text_hash:
            self.stale = True
            return True
        self.stale = False
        return False


# ---------------------------------------------------------------------------
# Semantic token parser
# ---------------------------------------------------------------------------
class SemanticTokenParser:
    """Parses <|...|> tokens from editor text into SemanticTokenState.

    This is NOT a simple substring check. It properly identifies token
    categories, resolves tags to human-readable names, and detects
    conflicts (e.g. pitch_low + pitch_high in the same scope).
    """

    @staticmethod
    def parse(text: str) -> Tuple[List[ParsedToken], List[str]]:
        """Parse all Higgs tokens from the text.

        Returns (tokens, conflicts) where:
            tokens: list of ParsedToken in order of appearance
            conflicts: list of conflict warning strings

        Conflict detection:
            - Multiple emotions in the same text → warning
            - Multiple styles → warning
            - Conflicting pitch (low + high) → warning
            - Conflicting speed (slow + fast) → warning
            - Conflicting delivery (expressive_low + expressive_high) → warning
        """
        tokens: List[ParsedToken] = []
        conflicts: List[str] = []

        for match in _TOKEN_RE.finditer(text):
            category = match.group(1)
            tag = match.group(2)
            offset = match.start()
            raw = match.group(0)

            # Validate the token is known
            if category == "emotion" and tag not in EMOTION_BY_TAG:
                conflicts.append(f"Unknown emotion token: {raw}")
                continue
            if category == "style":
                known = any(s.tag == tag for s in STYLE_BY_NAME.values())
                if not known:
                    conflicts.append(f"Unknown style token: {raw}")
                    continue
            if category == "prosody" and tag not in PROSODY_BY_TAG:
                conflicts.append(f"Unknown prosody token: {raw}")
                continue
            if category == "sfx" and tag not in SFX_BY_TAG:
                conflicts.append(f"Unknown SFX token: {raw}")
                continue

            # Determine placement type (Section 7/8):
            # SFX and pause are inline_positional — they must stay at their
            # exact position in the text. Everything else is sentence_level.
            is_inline = (category == "sfx" or
                        (category == "prosody" and tag in ("pause", "long_pause")))

            tokens.append(ParsedToken(
                category=category,
                tag=tag,
                offset=offset,
                raw=raw,
                placement="inline_positional" if is_inline else "sentence_level",
            ))

        # Detect conflicts
        emotions = [t for t in tokens if t.category == "emotion"]
        styles = [t for t in tokens if t.category == "style"]
        speeds = [t for t in tokens if t.prosody_subcategory == "speed"]
        pitches = [t for t in tokens if t.prosody_subcategory == "pitch"]
        deliveries = [t for t in tokens if t.prosody_subcategory == "delivery"]

        if len(emotions) > 1:
            names = ", ".join(t.human_value for t in emotions)
            conflicts.append(f"Multiple emotion tokens in text: {names}. Only the last one will be used.")

        if len(styles) > 1:
            names = ", ".join(t.human_value for t in styles)
            conflicts.append(f"Multiple style tokens in text: {names}. Only the last one will be used.")

        if len(set(t.tag for t in speeds)) > 1:
            tags = ", ".join(t.tag for t in speeds)
            conflicts.append(f"Conflicting speed tokens: {tags}. Only the last one will be used.")

        if len(set(t.tag for t in pitches)) > 1:
            tags = ", ".join(t.tag for t in pitches)
            conflicts.append(f"Conflicting pitch tokens: {tags}. Only the last one will be used.")

        if len(set(t.tag for t in deliveries)) > 1:
            tags = ", ".join(t.tag for t in deliveries)
            conflicts.append(f"Conflicting delivery tokens: {tags}. Only the last one will be used.")

        return tokens, conflicts

    @staticmethod
    def resolve_state(text: str) -> SemanticTokenState:
        """Parse text and return a fully resolved SemanticTokenState.

        This is the main entry point for the Read Tokens action.
        """
        tokens, conflicts = SemanticTokenParser.parse(text)
        state = SemanticTokenState()
        state.tokens = tokens
        state.conflicts = conflicts
        state.imported = True
        state.imported_text_hash = hashlib.sha256(
            text.encode("utf-8")).hexdigest()

        # Resolve effective values — last token of each category wins
        # (inline tokens override everything else at the global scope)
        for token in tokens:
            if token.category == "emotion":
                state.emotion = token.human_value
            elif token.category == "style":
                state.style = token.human_value
            elif token.category == "prosody":
                sub = token.prosody_subcategory
                if sub == "speed":
                    state.speed = token.human_value
                elif sub == "pitch":
                    state.pitch = token.human_value
                elif sub == "delivery":
                    state.delivery = token.human_value
                elif sub == "pause":
                    state.has_pause = True
            elif token.category == "sfx":
                state.has_sfx = True

        logger.info(
            "SemanticTokenParser: parsed %d tokens, emotion=%s, style=%s, "
            "speed=%s, pitch=%s, delivery=%s, conflicts=%d",
            len(tokens), state.emotion, state.style,
            state.speed, state.pitch, state.delivery, len(conflicts),
        )
        return state


# ---------------------------------------------------------------------------
# Canonical prompt compiler
# ---------------------------------------------------------------------------
class CanonicalPromptCompiler:
    """Compiles the final prompt using Inline > Block > Global precedence.

    This is the SINGLE canonical compiler used by Preview, Generate,
    and Generate Long. No other component may construct Higgs token strings.

    Precedence (per property, per scope):
        1. Inline token (from Read Tokens semantic state)
        2. Narration Block override
        3. Global Right Panel default

    Only the winning value is emitted — no duplicates.
    """

    @staticmethod
    def compile(
        text: str,
        global_emotion: Optional[str] = None,
        global_style: Optional[str] = None,
        global_speed: Optional[str] = None,
        global_pitch: Optional[str] = None,
        global_delivery: Optional[str] = None,
        block_emotion: Optional[str] = None,
        block_style: Optional[str] = None,
        block_speed: Optional[str] = None,
        block_pitch: Optional[str] = None,
        block_delivery: Optional[str] = None,
        inline_emotion: Optional[str] = None,
        inline_style: Optional[str] = None,
        inline_speed: Optional[str] = None,
        inline_pitch: Optional[str] = None,
        inline_delivery: Optional[str] = None,
        allow_sfx: bool = True,
    ) -> PromptData:
        """Compile a canonical prompt with full precedence resolution.

        Args:
            text: the plain text (may contain {sfx:...} and {pause} markers)
            global_*: Right Panel default values
            block_*: Narration Block override values (None = inherit global)
            inline_*: Imported inline token values (from Read Tokens)
            allow_sfx: when False, SFX markers are stripped (onomatopoeia kept)

        Returns:
            PromptData with the final prompt, token preview, and warnings.
        """
        from engine.prompt_builder import PromptBuilder

        # Resolve effective values: Inline > Block > Global
        eff_emotion = inline_emotion or block_emotion or global_emotion
        eff_style = inline_style or block_style or global_style
        eff_speed = inline_speed or block_speed or global_speed
        eff_pitch = inline_pitch or block_pitch or global_pitch
        eff_delivery = inline_delivery or block_delivery or global_delivery

        # Use the existing PromptBuilder to build the final prompt.
        # PromptBuilder handles SFX markers ({sfx:...}) and pause markers
        # ({pause}) in the text, converting them to proper Higgs tokens.
        # It also handles conflict detection at the sentence level.
        builder = PromptBuilder()
        try:
            prompt_data = builder.build(
                text=text,
                emotion=eff_emotion,
                style=eff_style,
                speed=eff_speed,
                pitch=eff_pitch,
                delivery=eff_delivery,
                allow_sfx=allow_sfx,
            )
        except Exception as exc:
            logger.warning("CanonicalPromptCompiler: build failed: %s", exc)
            prompt_data = PromptData(
                final_prompt=text,
                plain_text=text,
                token_preview=[],
                warnings=[f"Prompt compilation failed: {exc}"],
            )

        return prompt_data

    @staticmethod
    def compile_for_generate(
        text: str,
        global_emotion: Optional[str] = None,
        global_style: Optional[str] = None,
        global_speed: Optional[str] = None,
        global_pitch: Optional[str] = None,
        global_delivery: Optional[str] = None,
        token_state: Optional[SemanticTokenState] = None,
        allow_sfx: bool = True,
    ) -> PromptData:
        """Compile the canonical prompt for a Generate (single call).

        This is the entry point for Preview and Generate.
        It resolves Inline (from token_state) > Global defaults.
        No block scope (blocks are handled by the editor's get_final_prompt).
        """
        inline_emotion = None
        inline_style = None
        inline_speed = None
        inline_pitch = None
        inline_delivery = None

        if token_state and token_state.imported and not token_state.stale:
            inline_emotion = token_state.emotion
            inline_style = token_state.style
            inline_speed = token_state.speed
            inline_pitch = token_state.pitch
            inline_delivery = token_state.delivery

        return CanonicalPromptCompiler.compile(
            text=text,
            global_emotion=global_emotion,
            global_style=global_style,
            global_speed=global_speed,
            global_pitch=global_pitch,
            global_delivery=global_delivery,
            inline_emotion=inline_emotion,
            inline_style=inline_style,
            inline_speed=inline_speed,
            inline_pitch=inline_pitch,
            inline_delivery=inline_delivery,
            allow_sfx=allow_sfx,
        )

    @staticmethod
    def compile_for_batch_part(
        text: str,
        global_emotion: Optional[str] = None,
        global_style: Optional[str] = None,
        global_speed: Optional[str] = None,
        global_pitch: Optional[str] = None,
        global_delivery: Optional[str] = None,
        block_emotion: Optional[str] = None,
        block_style: Optional[str] = None,
        block_speed: Optional[str] = None,
        block_pitch: Optional[str] = None,
        block_delivery: Optional[str] = None,
        token_state: Optional[SemanticTokenState] = None,
        allow_sfx: bool = True,
    ) -> PromptData:
        """Compile a self-contained prompt for one BatchJob part.

        This is the entry point for Generate Long / batch splitting.
        Each part gets a COMPLETE prompt with all required tokens.

        Uses the SAME Inline > Block > Global precedence as compile_for_generate.
        There must NOT be two different precedence rules (Section 1/13).
        """
        # Extract inline tokens from token_state (same as compile_for_generate)
        inline_emotion = None
        inline_style = None
        inline_speed = None
        inline_pitch = None
        inline_delivery = None

        if token_state and token_state.imported and not token_state.stale:
            inline_emotion = token_state.emotion
            inline_style = token_state.style
            inline_speed = token_state.speed
            inline_pitch = token_state.pitch
            inline_delivery = token_state.delivery

        # Call the SAME compile() method — one resolver, one precedence rule
        return CanonicalPromptCompiler.compile(
            text=text,
            global_emotion=global_emotion,
            global_style=global_style,
            global_speed=global_speed,
            global_pitch=global_pitch,
            global_delivery=global_delivery,
            block_emotion=block_emotion,
            block_style=block_style,
            block_speed=block_speed,
            block_pitch=block_pitch,
            block_delivery=block_delivery,
            inline_emotion=inline_emotion,
            inline_style=inline_style,
            inline_speed=inline_speed,
            inline_pitch=inline_pitch,
            inline_delivery=inline_delivery,
            allow_sfx=allow_sfx,
        )

    @staticmethod
    def compile_continuous(
        text: str,
        blocks: list,
        global_emotion: Optional[str] = None,
        global_style: Optional[str] = None,
        global_speed: Optional[str] = None,
        global_pitch: Optional[str] = None,
        global_delivery: Optional[str] = None,
        token_state: Optional[SemanticTokenState] = None,
        allow_sfx: bool = True,
    ) -> PromptData:
        """Compile a continuous prompt containing multiple Narration Blocks.

        This is the SINGLE canonical entry point for Narration Blocks
        (Section 27). It replaces the old editor.get_final_prompt() path
        which used a separate PromptOptimizer + PromptBuilder.

        For continuous generation (one model call), sentence-level state
        may persist. Repeated unchanged tokens are optimized — a token is
        only emitted when the effective value changes from the previous
        block (Section 23).

        Precedence: Inline > Block override > Global default (same rule).

        Args:
            allow_sfx: when False, SFX markers in block text are replaced
                with just the onomatopoeia (no SFX token emitted).
        """
        from engine.prompt_builder import PromptBuilder
        from engine.narration_blocks import EffectiveBlockState, TokenEmission

        # Extract inline tokens
        inline_emotion = None
        inline_style = None
        inline_speed = None
        inline_pitch = None
        inline_delivery = None

        if token_state and token_state.imported and not token_state.stale:
            inline_emotion = token_state.emotion
            inline_style = token_state.style
            inline_speed = token_state.speed
            inline_pitch = token_state.pitch
            inline_delivery = token_state.delivery

        if not blocks:
            # No blocks — single continuous text
            return CanonicalPromptCompiler.compile(
                text=text,
                global_emotion=global_emotion,
                global_style=global_style,
                global_speed=global_speed,
                global_pitch=global_pitch,
                global_delivery=global_delivery,
                inline_emotion=inline_emotion,
                inline_style=inline_style,
                inline_speed=inline_speed,
                inline_pitch=inline_pitch,
                inline_delivery=inline_delivery,
                allow_sfx=allow_sfx,
            )

        # Resolve effective state per block: Inline > Block > Global
        states: list = []
        for i, block in enumerate(blocks):
            block_text = text[block.start_offset:block.end_offset]
            # Apply precedence: Inline > Block override > Global default
            eff_emotion = inline_emotion or block.emotion or global_emotion
            eff_style = inline_style or block.style or global_style
            eff_speed = inline_speed or block.speed or global_speed
            eff_pitch = inline_pitch or block.pitch or global_pitch
            eff_delivery = inline_delivery or block.delivery or global_delivery

            states.append(EffectiveBlockState(
                block_index=i, text=block_text,
                emotion=eff_emotion, style=eff_style,
                speed=eff_speed, pitch=eff_pitch,
                delivery=eff_delivery,
                sfx_insertions=list(block.sfx_insertions),
                pause_insertions=list(block.pause_insertions),
            ))

        # Optimize emissions: only emit tokens when the value CHANGES
        # from the previous block (continuous prompt optimization).
        #
        # CRITICAL: "Normal" is an application-level placeholder meaning
        # "no override." It is NOT a valid HIGGS V3 token. The official
        # HIGGS V3 reference defines only:
        #   Speed:    speed_very_slow, speed_slow, speed_fast, speed_very_fast
        #   Pitch:    pitch_low, pitch_high
        #   Delivery: expressive_high, expressive_low
        # There is NO speed_normal, pitch_normal, or expressive_normal token.
        # Therefore, "Normal" values must NEVER produce an emission — they
        # mean "no token for this category." This matches the behavior of
        # PromptBuilder.build(), which skips "Normal" prosody values.
        #
        # This is the Preview Hard Contract (ADR 003 §6): the Advanced
        # Preview (this method) and the center Preview (compile_for_generate
        # → PromptBuilder.build) must produce IDENTICAL token sequences for
        # the same input. If PromptBuilder skips "Normal", so must we.
        emissions: list = []
        prev = {"emotion": None, "style": None, "speed": None,
                "pitch": None, "delivery": None}

        for state in states:
            # Emotion and Style have no "Normal" placeholder — they are
            # either set (a value) or unset (None). None means NO TOKEN
            # (same as "Normal" for prosody). Only emit when the value is
            # non-None AND different from the previous EMITTED value.
            # C1 FIX: previously, a None→Fear or Fear→None transition would
            # emit TokenEmission(value=None), which crashed
            # _resolve_emission_tag() calling None.lower().
            if (state.emotion is not None
                    and state.emotion != prev["emotion"]):
                emissions.append(TokenEmission(
                    block_index=state.block_index,
                    category="emotion", value=state.emotion))
                prev["emotion"] = state.emotion
            elif state.emotion is None:
                # None means "no token" — reset prev so a subsequent
                # non-None value WILL be emitted.
                prev["emotion"] = None
            if (state.style is not None
                    and state.style != prev["style"]):
                emissions.append(TokenEmission(
                    block_index=state.block_index,
                    category="style", value=state.style))
                prev["style"] = state.style
            elif state.style is None:
                prev["style"] = None
            # Speed / Pitch / Delivery: "Normal" means NO TOKEN.
            # Only emit when the value is non-None AND non-"Normal" AND
            # different from the previous emitted value.
            # Note: we compare against prev (the last EMITTED value, not
            # the last state value) so that Normal→High→Normal correctly
            # emits expressive_high for the middle block and nothing for
            # the last block.
            if (state.speed is not None
                    and state.speed != "Normal"
                    and state.speed != prev["speed"]):
                emissions.append(TokenEmission(
                    block_index=state.block_index,
                    category="prosody", value=state.speed))
                prev["speed"] = state.speed
            elif state.speed == "Normal" or state.speed is None:
                # "Normal" / None means "no token" — reset prev so that
                # a subsequent non-Normal value WILL be emitted.
                prev["speed"] = None
            if (state.pitch is not None
                    and state.pitch != "Normal"
                    and state.pitch != prev["pitch"]):
                emissions.append(TokenEmission(
                    block_index=state.block_index,
                    category="prosody", value=state.pitch))
                prev["pitch"] = state.pitch
            elif state.pitch == "Normal" or state.pitch is None:
                prev["pitch"] = None
            if (state.delivery is not None
                    and state.delivery != "Normal"
                    and state.delivery != prev["delivery"]):
                emissions.append(TokenEmission(
                    block_index=state.block_index,
                    category="prosody", value=state.delivery))
                prev["delivery"] = state.delivery
            elif state.delivery == "Normal" or state.delivery is None:
                prev["delivery"] = None

        # Build the final continuous prompt using PromptBuilder.build_from_emissions
        builder = PromptBuilder()
        try:
            final_prompt = builder.build_from_emissions(states, emissions)
            # If allow_sfx=False, strip SFX tokens from the compiled prompt.
            # build_from_emissions converts {sfx:Name:Onom} to <|sfx:tag|>Onom
            # via _convert_sfx_marker. We need to replace <|sfx:tag|> with
            # nothing (just keep the onomatopoeia text that follows).
            if not allow_sfx:
                import re as _re
                final_prompt = _re.sub(r'<\|sfx:\w+\|>', '', final_prompt)
        except Exception as exc:
            logger.warning("CanonicalPromptCompiler.compile_continuous: failed: %s", exc)
            raise  # Re-raise — do NOT silently fall back to plain text (Issue 4)

        # P3.25 (audit SS-H07): surface value-validation warnings from the
        # builder (invalid block overrides are REJECTED, never emitted as
        # garbage tokens — the warning tells the user why).
        builder_warnings = list(getattr(builder, "last_warnings", []))
        return PromptData(
            final_prompt=final_prompt,
            plain_text=text,
            token_preview=[],
            warnings=builder_warnings,
        )
