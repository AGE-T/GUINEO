"""
SpeechStudio Engine - Narration Splitter.

Splits long text into generation-safe parts at sentence boundaries.
Never cuts inside a sentence. Respects Narration Block boundaries.

Rules:
- Target: ~3-4 sentences per part (up to ~400 chars)
- Always cut at sentence end (. ! ? …)
- Never cut inside a sentence
- If Narration Blocks exist, each block starts a new part boundary
- Short sentences are accumulated until the target is reached
- Every part gets append_silence=0.5 (post-generation silence, not model-generated)
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List, Optional

from engine.narration_blocks import PromptBlock
from engine.models import GenerationParameters
from engine.prompt_optimizer import PromptOptimizer
from engine.prompt_builder import PromptBuilder
from engine.sentence_boundaries import (
    INLINE_MARKER_RE as _SHARED_INLINE_MARKER_RE,
    split_sentence_units as _split_units,
    split_sentences as _split_sentence_texts,
)
from engine.logger import get_logger

logger = get_logger("narration_splitter")


@dataclass
class SplitPart:
    """One generation-safe part of a long narration."""
    text: str                        # plain text (no tokens)
    prompt: str                      # full prompt with Higgs tokens
    block_index: Optional[int] = None  # which Narration Block this came from
    block_label: str = ""            # human-readable block label
    estimated_duration: float = 0.0  # estimated seconds of audio
    char_count: int = 0              # character count of prompt
    speaker: str = ""                # detected speaker label (e.g. "CAPTAIN"), "" if none
    # P3.15: The source Block's unique ID. Used for Block→Part mapping in the
    # BatchGenerationDialog so the user can see which Block each Part came from.
    # When a Block is split into multiple Parts, all Parts share the same
    # source_block_id. None for plain-text (no blocks) parts.
    source_block_id: Optional[str] = None
    # P3.22 Character System: the Character ID from the source block.
    # When set, this part should use the character's voice_profile_id
    # (Block Character > Scene Voice > Control Panel dropdown).
    # None for plain-text parts or blocks without a character assignment.
    character_id: Optional[str] = None
    # P3.15: Part number within its source Block (1-based). If Block 5 produces
    # 2 Parts, they are part_of_block=1 and part_of_block=2.
    part_of_block: int = 1
    # P3.15: Total Parts produced by this source Block. If Block 5 produces
    # 2 Parts, both have total_parts_in_block=2.
    total_parts_in_block: int = 1
    # Effective generation state resolved from block overrides + global defaults.
    # These are set by _split_with_speakers and _split_with_blocks so that
    # LongNarrationDialog can rebuild the prompt if the user edits the text.
    eff_emotion: Optional[str] = None
    eff_style: Optional[str] = None
    eff_speed: Optional[str] = None
    eff_pitch: Optional[str] = None
    eff_delivery: Optional[str] = None
    # Raw Mode flag: when True, this part came from _split_raw() and the
    # text is the literal Higgs prompt (user-owned). The LongNarrationDialog
    # must NOT rebuild the prompt for raw parts — the edited text is used
    # as-is (ADR 003 §8: Raw Mode ownership model).
    is_raw: bool = False


class NarrationSplitter:
    """Splits long text into generation-safe parts.

    Never cuts inside a sentence. Respects Narration Block boundaries.
    Can detect speaker labels (e.g. "CAPTAIN: text") for multi-speaker
    dialogue support.
    """

    # Target ~3-4 sentences per part, max ~400 chars
    TARGET_SENTENCES = 3
    MAX_CHARS = 400
    MIN_CHARS = 50  # don't create parts shorter than this unless at end

    # P3.44.2/P3.44.8: any inline marker (SFX / pause). Aliased to the
    # single authoritative regex in engine.sentence_boundaries (kept as
    # a class attribute for backward compatibility with callers/tests
    # that reference NarrationSplitter._INLINE_MARKER_RE). Used to
    # shield marker contents from punctuation detection and to attach a
    # marker that directly follows sentence punctuation to the sentence
    # it annotates (no whitespace required — e.g. "???{sfx:Laughter:Haha}").
    _INLINE_MARKER_RE = _SHARED_INLINE_MARKER_RE

    # NOTE (P3.44.8): the sentence-boundary rules themselves (including
    # SENTENCE_END_RE / _PUNCT_RUN_RE, retired here) moved to
    # engine.sentence_boundaries — the ONE authoritative scanner shared
    # by NarrationSplitter, BlockDetector and PromptBuilder.

    # Speaker declaration: a line that is EXACTLY "$SPEAKER:" (dollar sign,
    # uppercase identifier, colon, optional trailing whitespace, nothing else).
    # The $ prefix makes this deterministic — no false positives on normal
    # text like "Note:", "PS:", "15:30".
    # Speaker name: uppercase letters, digits, underscores. Min 3 chars.
    # Examples (valid):    "$CAPTAIN:", "$ENGINEER:", "$SCIENTIST_01:"
    # Examples (invalid):  "CAPTAIN:", "$CAPTAIN: text", "Note:", "$AB:"
    SPEAKER_DECL_RE = re.compile(r'^\$([A-Z][A-Z0-9_]{2,}):\s*$', re.MULTILINE)

    def split(
        self,
        text: str,
        blocks: Optional[List[PromptBlock]] = None,
        global_emotion: Optional[str] = None,
        global_style: Optional[str] = None,
        global_speed: str = "Normal",
        global_pitch: str = "Normal",
        global_delivery: str = "Normal",
        base_parameters: Optional[GenerationParameters] = None,
        raw_mode: bool = False,
        detect_speakers: bool = False,
        speaker_voice_map: Optional[dict] = None,
        allow_sfx: bool = True,
    ) -> List[SplitPart]:
        """Split text into generation-safe parts.

        Args:
            text: the full text to split.
            blocks: optional Narration Blocks (if present, block boundaries
                    are respected and per-block overrides are applied).
            global_*: global defaults for emotion/style/prosody. Ignored
                      when ``raw_mode`` is True.
            base_parameters: base generation parameters (copied, with
                            append_silence forced to 0.5).
            raw_mode: if True, the text is treated as literal Higgs prompt
                      (the user already wrote tokens). The PromptBuilder is
                      NOT called — each part's text is used as-is for the
                      prompt.
            detect_speakers: if True, detect ``SPEAKER: text`` lines and
                             produce one SplitPart per speaker line, with
                             the speaker name stored in SplitPart.speaker.
                             This takes precedence over blocks and raw_mode
                             (a dialogue is split by speaker turns, each
                             line becomes its own part).
            speaker_voice_map: optional dict mapping speaker labels to
                                voice profile IDs. When detect_speakers is
                                True AND this map is non-empty, only speaker
                                labels that exist in the map are recognized
                                as speakers (prevents false positives on
                                normal text with colons). If the map is
                                empty or None, all SPEAKER: lines are
                                recognized (backward compat with Phase 1).

        Returns:
            List of SplitPart, each safe to generate as a standalone batch job.

        Raises:
            ValueError: if detect_speakers is True, a speaker_voice_map is
                        provided, and the text contains a speaker label that
                        is NOT in the map (unknown speaker — the caller
                        should surface this as an error to the user).
        """
        if base_parameters is None:
            base_parameters = GenerationParameters()

        # Force 0.5s silence on every part
        params = GenerationParameters(
            temperature=base_parameters.temperature,
            top_p=base_parameters.top_p,
            top_k=base_parameters.top_k,
            max_new_tokens=base_parameters.max_new_tokens,
            seed=base_parameters.seed,
            append_silence=0.5,  # always 0.5s post-generation silence
            normalize_output=base_parameters.normalize_output,
            auto_play=False,     # never auto-play individual parts
            output_format=base_parameters.output_format,
        )

        # P3.44.2 — ONE authoritative metadata materialization for the whole
        # Batch pipeline: block sfx_insertions/pause_insertions (the legacy
        # structured representation) become literal in-text markers in the
        # text AND in block-span-adjusted COPIES, before any branch runs.
        # This is the same conversion the Preview pipeline applies
        # (PromptBuilder.materialize_inline_markers via compile_continuous),
        # so Preview and Batch receive identical semantic SFX/pause input.
        # The caller's PromptBlock objects are never mutated (copies only),
        # texts without metadata take the zero-cost fast path, and RAW mode
        # is exempt (the user owns the literal text — ADR 003 §8).
        if blocks and not raw_mode:
            text, blocks = self._materialize_block_metadata(text, blocks)

        # Multi-speaker dialogue detection takes precedence — each speaker
        # line becomes its own part with the speaker name attached.
        if detect_speakers and self._has_speaker_labels(text, speaker_voice_map):
            # Validate: if a speaker_voice_map is provided, every recognized
            # speaker must be in the map (unknown speaker = error).
            if speaker_voice_map:
                found_speakers = self._find_speakers(text, speaker_voice_map)
                unknown = [s for s in found_speakers if s not in speaker_voice_map]
                if unknown:
                    raise ValueError(
                        "Unknown speaker(s): {0}. Please assign a voice "
                        "profile to these speakers.".format(
                            ", ".join(unknown)))
            return self._split_with_speakers(
                text, blocks, global_emotion, global_style,
                global_speed, global_pitch, global_delivery, params, raw_mode,
                speaker_voice_map or {}, allow_sfx)

        # In raw mode, skip the PromptBuilder entirely.
        if raw_mode:
            return self._split_raw(text, params)

        if blocks:
            return self._split_with_blocks(
                text, blocks, global_emotion, global_style,
                global_speed, global_pitch, global_delivery, params, allow_sfx)
        else:
            return self._split_plain(
                text, global_emotion, global_style,
                global_speed, global_pitch, global_delivery, params, allow_sfx)

    # ------------------------------------------------------------------
    # Metadata materialization (P3.44.2)
    # ------------------------------------------------------------------
    def _materialize_block_metadata(self, text, blocks):
        """Materialize block SFX/pause metadata into in-text markers.

        Returns ``(materialized_text, shifted_block_copies)`` — the input
        ``blocks`` list and its PromptBlock objects are NEVER mutated.
        Per-block materialization goes through
        ``PromptBuilder.materialize_inline_markers`` (the single
        authoritative conversion shared with the Preview pipeline), so a
        block's materialized text is byte-identical to what
        ``compile_continuous`` compiles for the Preview.

        Fast path: when no block carries sfx/pause insertions, the inputs
        are returned unchanged (zero allocations).
        """
        if not any(b.sfx_insertions or b.pause_insertions for b in blocks):
            return text, blocks

        import dataclasses
        from engine.prompt_builder import materialize_inline_markers

        # Materialize each block's own span (insertion offsets are
        # block-relative), stitching the untouched inter-block text.
        ordered = sorted(blocks, key=lambda b: b.start_offset)
        pieces = []
        new_blocks = []
        cursor = 0
        new_start = 0
        for b in ordered:
            prefix = text[cursor:b.start_offset]
            block_text = text[b.start_offset:b.end_offset]
            mat, _ = materialize_inline_markers(
                block_text, b.sfx_insertions, b.pause_insertions)
            new_start += len(prefix)
            pieces.append(prefix)
            pieces.append(mat)
            new_blocks.append(dataclasses.replace(
                b, start_offset=new_start,
                end_offset=new_start + len(mat)))
            new_start += len(mat)
            cursor = b.end_offset
        pieces.append(text[cursor:])
        new_text = "".join(pieces)

        # Preserve the caller's block ORDER in the returned list.
        by_id = {id(b): nb for b, nb in zip(ordered, new_blocks)}
        return new_text, [by_id.get(id(b), b) for b in blocks]

    # ------------------------------------------------------------------
    # Speaker detection helpers
    # ------------------------------------------------------------------
    def _has_speaker_labels(self, text: str,
                            speaker_voice_map: Optional[dict] = None) -> bool:
        """Return True if the text contains at least one valid $SPEAKER: line.

        If ``speaker_voice_map`` is provided and non-empty, only speaker
        declarations whose speaker exists in the map are considered valid.
        If the map is empty/None, any $SPEAKER: line is recognized.
        """
        if speaker_voice_map:
            for m in self.SPEAKER_DECL_RE.finditer(text):
                if m.group(1) in speaker_voice_map:
                    return True
            return False
        return bool(self.SPEAKER_DECL_RE.search(text))

    def _find_speakers(self, text: str,
                       speaker_voice_map: Optional[dict] = None) -> List[str]:
        """Return the list of distinct speaker labels found in the text."""
        speakers = []
        seen = set()
        for m in self.SPEAKER_DECL_RE.finditer(text):
            spk = m.group(1)
            if spk not in seen:
                seen.add(spk)
                speakers.append(spk)
        return speakers

    def _split_with_speakers(
        self, text, blocks, g_emotion, g_style, g_speed, g_pitch, g_delivery,
        params, raw_mode: bool, speaker_voice_map: Optional[dict] = None,
        allow_sfx: bool = True,
    ) -> List[SplitPart]:
        """Split multi-speaker dialogue using the $SPEAKER: syntax.

        Format:
            $CAPTAIN:
            First sentence.
            Second sentence.

            $ENGINEER:
            Third sentence.

        The speaker declaration is a line that is EXACTLY "$SPEAKER:" (no
        other text on that line). All following lines (until the next
        speaker declaration or end of text) belong to that speaker.

        The speaker declaration line is NOT included in the TTS prompt —
        only the text below it is sent to the model.

        CRITICAL: This method resolves Narration Block overrides for each
        speaker's text. If a block overlaps with the speaker's text range,
        the block's emotion/style/prosody override the global defaults.
        This ensures per-block tokens (e.g. <|emotion:fear|>) are present
        in each independent BatchJob prompt.

        Each part gets a COMPLETE, self-contained prompt — all required
        Higgs tokens are prepended. This is essential because each part
        becomes an independent model generation call.
        """
        # builder no longer needed — canonical compiler is used directly
        parts: List[SplitPart] = []

        # Build a list of (start_offset, end_offset, block) for quick lookup.
        # This allows us to find which block(s) overlap with each speaker's
        # text range.
        block_ranges = []
        if blocks:
            for block in blocks:
                block_ranges.append((block.start_offset, block.end_offset, block))

        lines = text.splitlines()
        current_speaker = ""
        current_text_lines: List[str] = []
        # Track the character offset of the current speaker's text start
        # so we can find overlapping blocks.
        current_offset = 0
        speaker_text_start = 0

        def _find_block_for_range(text_start: int, text_end: int):
            """Find the block that overlaps with the given text range.

            Returns the block's override values, or None if no block
            overlaps (use global defaults).
            """
            for b_start, b_end, block in block_ranges:
                # Check if the block overlaps with the text range
                if b_start < text_end and b_end > text_start:
                    return block
            return None

        def _flush():
            nonlocal current_speaker, current_text_lines, speaker_text_start
            if not current_text_lines:
                current_speaker = ""
                current_text_lines = []
                return
            line_text = "\n".join(current_text_lines).strip()
            if not line_text:
                current_text_lines = []
                return

            # Find the character range of this speaker's text in the
            # original text. We use speaker_text_start and the current
            # offset to determine the range.
            text_end = current_offset

            # Resolve effective emotion/style/prosody:
            # 1. Check if a Narration Block overlaps with this text range
            # 2. If yes, use the block's override (if set)
            # 3. Otherwise, use the global default
            block = _find_block_for_range(speaker_text_start, text_end)
            eff_emotion = g_emotion
            eff_style = g_style
            eff_speed = g_speed
            eff_pitch = g_pitch
            eff_delivery = g_delivery
            if block is not None:
                if block.emotion is not None:
                    eff_emotion = block.emotion
                if block.style is not None:
                    eff_style = block.style
                if block.speed is not None:
                    eff_speed = block.speed
                if block.pitch is not None:
                    eff_pitch = block.pitch
                if block.delivery is not None:
                    eff_delivery = block.delivery

            # Build the prompt for this speaker's text.
            if raw_mode:
                prompt = line_text
            else:
                from engine.prompt_state import CanonicalPromptCompiler
                prompt_data = CanonicalPromptCompiler.compile_for_batch_part(
                    text=line_text,
                    global_emotion=eff_emotion,
                    global_style=eff_style,
                    global_speed=eff_speed,
                    global_pitch=eff_pitch,
                    global_delivery=eff_delivery,
                    allow_sfx=allow_sfx,
                )
                prompt = prompt_data.final_prompt
            est_duration = len(line_text) / 15.0
            # P3.23 (design record §23): if the overlapping Narration Block
            # carries a Character, the Character is authoritative for this
            # part — it propagates to SplitPart.character_id so voice
            # resolution (Block Character > speaker_voice_map > Scene >
            # Dropdown) uses the Character's Voice Profile. $SPEAKER remains
            # as speaker context only.
            block_character_id = getattr(block, "character_id", None) \
                if block is not None else None
            parts.append(SplitPart(
                text=line_text,
                prompt=prompt,
                block_index=None,
                block_label=current_speaker,
                estimated_duration=est_duration,
                char_count=len(prompt),
                speaker=current_speaker,
                character_id=block_character_id,
                is_raw=raw_mode,  # mark as raw if raw_mode was requested
            ))
            # Store the effective values on the part for logging/debugging
            parts[-1].eff_emotion = eff_emotion
            parts[-1].eff_style = eff_style
            parts[-1].eff_speed = eff_speed
            parts[-1].eff_pitch = eff_pitch
            parts[-1].eff_delivery = eff_delivery
            current_text_lines = []

        for line in lines:
            stripped = line.strip()
            m = self.SPEAKER_DECL_RE.match(stripped)
            if m:
                _flush()
                current_speaker = m.group(1).strip()
                speaker_text_start = current_offset + len(line) + 1  # +1 for \n
            else:
                if not stripped:
                    if current_text_lines:
                        current_text_lines.append("")
                else:
                    current_text_lines.append(stripped)
            current_offset += len(line) + 1  # +1 for \n
        _flush()

        logger.info("Split dialogue into %d parts (%d speakers)",
                    len(parts),
                    len({p.speaker for p in parts if p.speaker}))
        # Log each part's effective state for debugging
        for i, p in enumerate(parts):
            logger.info(
                "  Part %d: speaker=%s, emotion=%s, style=%s, speed=%s, pitch=%s, delivery=%s, prompt=%s",
                i, p.speaker,
                getattr(p, 'eff_emotion', '?'),
                getattr(p, 'eff_style', '?'),
                getattr(p, 'eff_speed', '?'),
                getattr(p, 'eff_pitch', '?'),
                getattr(p, 'eff_delivery', '?'),
                p.prompt[:80],
            )
        return parts

    # ------------------------------------------------------------------
    # Split raw text (user already wrote Higgs tokens)
    # ------------------------------------------------------------------
    def _split_raw(
        self, text: str, params: GenerationParameters
    ) -> List[SplitPart]:
        """Split raw prompt text at sentence boundaries.

        The text is treated as the literal Higgs prompt — no PromptBuilder
        is called, no global tokens are prepended. Each part's text IS
        the prompt.

        Each part is marked ``is_raw=True`` so the LongNarrationDialog
        knows NOT to rebuild the prompt via the canonical compiler
        (ADR 003 §8: Raw Mode ownership model — user owns the prompt).
        """
        parts: List[SplitPart] = []

        units = self._split_sentence_units(text)
        groups = self._group_sentence_units(units)

        for group_text in groups:
            est_duration = len(group_text) / 15.0
            parts.append(SplitPart(
                text=group_text,
                prompt=group_text,  # raw: text == prompt
                block_index=None,
                block_label="",
                estimated_duration=est_duration,
                char_count=len(group_text),
                is_raw=True,  # mark as raw so dialog doesn't rebuild
            ))

        logger.info("Split raw text into %d parts (no PromptBuilder)", len(parts))
        return parts

    # ------------------------------------------------------------------
    # Split with Narration Blocks
    # ------------------------------------------------------------------
    def _split_with_blocks(
        self, text, blocks, g_emotion, g_style, g_speed, g_pitch, g_delivery, params,
        allow_sfx: bool = True,
    ) -> List[SplitPart]:
        """Split respecting block boundaries. Each block produces at least
        one part (its own). If a block is very long (>MAX_CHARS), its text
        is further split into sentence groups. Short blocks are NEVER merged
        together — each block is a separate generation part."""
        optimizer = PromptOptimizer()
        # builder no longer needed — canonical compiler is used directly
        parts: List[SplitPart] = []

        for block_idx, block in enumerate(blocks):
            block_text = text[block.start_offset:block.end_offset].strip()
            if not block_text:
                continue

            # Get effective values (override or inherited)
            emotion = block.emotion if block.emotion is not None else g_emotion
            style = block.style if block.style is not None else g_style
            speed = block.speed if block.speed is not None else g_speed
            pitch = block.pitch if block.pitch is not None else g_pitch
            delivery = block.delivery if block.delivery is not None else g_delivery

            # If the block text is short enough, make it ONE part directly.
            # This preserves the 1-block = 1-part mapping the user expects.
            if len(block_text) <= self.MAX_CHARS:
                groups = [block_text]
            else:
                # Block is too long — split into sentence groups.
                units = self._split_sentence_units(block_text)
                groups = self._group_sentence_units(units)

            # P3.15: Track how many Parts this Block produces for the
            # part_of_block / total_parts_in_block display.
            total_parts_for_this_block = len(groups)
            block_label = block.label or "Block {0}".format(block_idx + 1)

            for part_idx, group_text in enumerate(groups):
                # Build prompt for this part using the block's settings
                from engine.prompt_state import CanonicalPromptCompiler
                prompt_data = CanonicalPromptCompiler.compile_for_batch_part(
                    text=group_text,
                    global_emotion=emotion,
                    global_style=style,
                    global_speed=speed,
                    global_pitch=pitch,
                    global_delivery=delivery,
                    allow_sfx=allow_sfx,
                )
                prompt = prompt_data.final_prompt

                est_duration = len(group_text) / 15.0  # ~15 chars/sec estimate
                # P3.23 (design record §23): if the block carries a Character
                # and no $SPEAKER label exists, the speaker field is populated
                # with the Character name for context and History ("If
                # Character exists without $SPEAKER: speaker is populated
                # with Character name"). Voice resolution stays Character-
                # driven via character_id; the name here is display context.
                part_speaker = ""
                if block.character_id and not getattr(block, "_speaker_label", ""):
                    part_speaker = getattr(block, "character_name", "") or ""
                parts.append(SplitPart(
                    text=group_text,
                    prompt=prompt,
                    block_index=block_idx,
                    block_label=block_label,
                    estimated_duration=est_duration,
                    char_count=len(prompt),
                    # P3.15: Block→Part identity fields
                    source_block_id=block.id,
                    # P3.22: carry the block's character_id for voice resolution
                    character_id=getattr(block, 'character_id', None),
                    part_of_block=part_idx + 1,
                    total_parts_in_block=total_parts_for_this_block,
                    speaker=part_speaker,  # Character name (context) or ""
                    eff_emotion=emotion,
                    eff_style=style,
                    eff_speed=speed,
                    eff_pitch=pitch,
                    eff_delivery=delivery,
                ))

        logger.info("Split %d blocks into %d parts", len(blocks), len(parts))
        for i, p in enumerate(parts):
            logger.info(
                "  Part %d: block=%s, emotion=%s, style=%s, speed=%s, pitch=%s, delivery=%s, prompt=%s",
                i, p.block_label,
                p.eff_emotion, p.eff_style, p.eff_speed,
                p.eff_pitch, p.eff_delivery,
                p.prompt[:80],
            )
        return parts

    # ------------------------------------------------------------------
    # Split plain text (no blocks)
    # ------------------------------------------------------------------
    def _split_plain(
        self, text, g_emotion, g_style, g_speed, g_pitch, g_delivery, params,
        allow_sfx: bool = True,
    ) -> List[SplitPart]:
        """Split plain text at sentence boundaries."""
        # builder no longer needed — canonical compiler is used directly
        parts: List[SplitPart] = []

        units = self._split_sentence_units(text)
        groups = self._group_sentence_units(units)

        for group_text in groups:
            from engine.prompt_state import CanonicalPromptCompiler
            prompt_data = CanonicalPromptCompiler.compile_for_batch_part(
                text=group_text,
                global_emotion=g_emotion,
                global_style=g_style,
                global_speed=g_speed,
                global_pitch=g_pitch,
                global_delivery=g_delivery,
                allow_sfx=allow_sfx,
            )
            prompt = prompt_data.final_prompt

            est_duration = len(group_text) / 15.0
            parts.append(SplitPart(
                text=group_text,
                prompt=prompt,
                block_index=None,
                block_label="",
                estimated_duration=est_duration,
                char_count=len(prompt),
            ))

        logger.info("Split text into %d parts", len(parts))
        return parts

    # ------------------------------------------------------------------
    # Sentence splitting
    # ------------------------------------------------------------------
    def _split_sentence_units(self, text: str) -> List[tuple]:
        """Split ``text`` into exact ``(sentence, separator)`` units.

        Delegates to the single authoritative boundary scanner
        (engine.sentence_boundaries — P3.44.8).  The separator is the
        ORIGINAL whitespace run between two sentences, so grouping can
        reconstruct part text byte-exactly (never " ".join).
        """
        return _split_units(text)

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences — EXACT source slices.

        Never cuts inside a sentence (a period glued to a digit/letter —
        "3.14", "v1.2", "test.hu", "U.S.A." — is not a boundary) and
        never mutates the text (each sentence is a slice, not a
        reconstruction).

        P3.44.2 — SFX/pause marker awareness (unchanged, now enforced by
        the shared scanner):
          1. SHIELDING: punctuation INSIDE an inline marker (e.g. the "!" in
             ``{sfx:Laughter:Ha!ha}``) is part of the onomatopoeia, never a
             sentence boundary — a marker is never cut in half.
          2. ATTACHMENT: an inline marker that follows sentence punctuation
             — with or without whitespace (``???{sfx:Laughter:Haha}`` and
             ``. {sfx:...}``) — belongs to the sentence it annotates: the
             sentence ends AFTER the marker chain. Without this rule a
             punctuation-adjacent marker glued two sentences into one
             oversized "sentence" (wrong part grouping), and a
             whitespace-separated marker detached to the NEXT sentence
             (SFX placement moved relative to the Preview prompt, which
             keeps it right after the punctuation).
        """
        return _split_sentence_texts(text)

    # ------------------------------------------------------------------
    # Group sentences into parts
    # ------------------------------------------------------------------
    def _group_sentence_units(self, units: List[tuple]) -> List[str]:
        """Group ``(sentence, separator)`` units into parts of
        ~TARGET_SENTENCES or ~MAX_CHARS.

        P3.44.8 — EXACT-TEXT PRESERVATION: sentences joined into one
        part are joined with their ORIGINAL separators
        (``sentence_i + sep_i + sentence_{i+1}``), never
        ``" ".join(...)``.  The old join INSERTED a space inside
        tokens the broken boundary rule had split ("A GLM 5.2-t
        használtam." -> "A GLM 5. 2-t használtam.") and silently
        normalised newlines/multiple spaces to single spaces.  A
        separator is only consumed when the sentence AFTER it joins the
        same part; at a part boundary the separator is the split point
        and is dropped (as before).

        Rules (unchanged):
        - Accumulate sentences until target reached
        - If a single sentence exceeds MAX_CHARS, it gets its own part
        - Never cut inside a sentence
        - Don't create parts shorter than MIN_CHARS unless it's the last
        """
        if not units:
            return []

        groups: List[str] = []
        current: List[tuple] = []
        current_chars = 0   # length of the exact joined text so far

        for sentence, sep in units:
            sent_chars = len(sentence)

            # If this single sentence is very long, give it its own part
            if sent_chars > self.MAX_CHARS:
                # First flush current group
                if current:
                    groups.append(self._join_units(current))
                    current = []
                    current_chars = 0
                groups.append(sentence)
                continue

            # Separator that would join this sentence to the current
            # group (the ORIGINAL whitespace — length-aware accounting
            # replaces the old "+1 for space" approximation).
            join_sep_len = len(current[-1][1]) if current else 0

            # Check if adding this sentence would exceed MAX_CHARS
            if current and current_chars + join_sep_len + sent_chars > self.MAX_CHARS:
                # Flush current group
                groups.append(self._join_units(current))
                current = []
                current_chars = 0
                join_sep_len = 0

            current.append((sentence, sep))
            current_chars += join_sep_len + sent_chars

            # Check if we've reached the target sentence count
            if len(current) >= self.TARGET_SENTENCES and current_chars >= self.MIN_CHARS:
                groups.append(self._join_units(current))
                current = []
                current_chars = 0

        # Flush remaining
        if current:
            groups.append(self._join_units(current))

        return groups

    @staticmethod
    def _join_units(units: List[tuple]) -> str:
        """Join units with their ORIGINAL separators (exact text)."""
        if not units:
            return ""
        out = []
        for i, (sentence, sep) in enumerate(units):
            out.append(sentence)
            if i < len(units) - 1:
                out.append(sep)
        return "".join(out)
