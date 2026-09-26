"""
SpeechStudio Engine - Narration Block Manager.

Manages the list of PromptBlock objects, including:
- Offset tracking (adjusts when the editor text changes)
- Split / merge / delete operations
- Lock / unlock
- Re-detect (delegates to BlockDetector)

The editor is the single source of truth for text. This manager only
stores block metadata (offsets + overrides + labels).
"""

from __future__ import annotations
from typing import List, Optional, Tuple
import uuid

from engine.narration_blocks import PromptBlock, NarrationTemplate
from engine.block_detector import BlockDetector, HeuristicBlockDetector
from engine.logger import get_logger

logger = get_logger("narration_block_manager")


class NarrationBlockManager:
    """Manages the list of Narration Blocks."""

    def __init__(self, detector: Optional[BlockDetector] = None):
        self._blocks: List[PromptBlock] = []
        self._detector: BlockDetector = detector or HeuristicBlockDetector()
        self._last_text: str = ""

    @property
    def blocks(self) -> List[PromptBlock]:
        return list(self._blocks)

    @property
    def has_blocks(self) -> bool:
        return len(self._blocks) > 0

    @property
    def has_manual_edits(self) -> bool:
        return any(b.manually_edited or b.has_overrides() or b.locked
                   for b in self._blocks)

    @property
    def has_locked_blocks(self) -> bool:
        return any(b.locked for b in self._blocks)

    def get_block(self, block_id: str) -> Optional[PromptBlock]:
        for b in self._blocks:
            if b.id == block_id:
                return b
        return None

    def get_block_at_offset(self, offset: int) -> Optional[PromptBlock]:
        for b in self._blocks:
            if b.start_offset <= offset < b.end_offset:
                return b
        return None

    def get_block_text(self, block: PromptBlock, full_text: str) -> str:
        return full_text[block.start_offset:block.end_offset]

    def auto_detect(self, text: str) -> None:
        self._blocks = self._detector.detect(text)
        self._last_text = text
        logger.info("Auto-detected %d blocks", len(self._blocks))

    def rebuild_everything(self, text: str) -> None:
        self._blocks = self._detector.detect(text)
        self._last_text = text

    def rebuild_automatic_only(self, text: str) -> None:
        """Rebuild automatic blocks while preserving locked/protected blocks.

        Locked blocks remain protected — their overrides, labels, locked
        state AND character assignment are preserved if their text still
        exists in the new text (design record §25: "Locked block: preserve
        Character").
        Automatic (non-locked) blocks are recalculated from scratch.

        Manual or locked state is never destroyed for locked blocks.
        """
        if not self._blocks:
            self.auto_detect(text)
            return
        # Old blocks' offsets are relative to self._last_text (the text they
        # were detected from). Use self._last_text — NOT the `text` parameter
        # — to extract the old block text for comparison.
        locked_blocks = [(b, self.get_block_text(b, self._last_text))
                         for b in self._blocks if b.locked]
        new_blocks = self._detector.detect(text)
        used_old = set()  # track which old blocks have been matched (1:1)
        for new_block in new_blocks:
            new_text = text[new_block.start_offset:new_block.end_offset]
            for i, (old_block, old_text) in enumerate(locked_blocks):
                if i in used_old:
                    continue
                if old_text.strip() and new_text.strip() == old_text.strip():
                    new_block.emotion = old_block.emotion
                    new_block.style = old_block.style
                    new_block.speed = old_block.speed
                    new_block.pitch = old_block.pitch
                    new_block.delivery = old_block.delivery
                    new_block.label = old_block.label
                    new_block.locked = True
                    new_block.manually_edited = True
                    # P3.23 (design §25): locked block → Character preserved.
                    new_block.character_id = old_block.character_id
                    new_block.lost_character_id = None
                    used_old.add(i)
                    break
        self._blocks = new_blocks
        self._last_text = text

    def preserve_overrides(self, text: str) -> None:
        """Re-detect blocks while preserving overrides for matching blocks.

        For each new block, find the best-matching old block (by text
        similarity). If the match score is >= 80%, the old block's overrides
        are transferred to the new block.

        One-to-one matching is enforced: each old block can only be matched
        to one new block. This prevents an old override from being assigned
        to multiple unrelated new blocks.

        Matching rules (in priority order):
        1. Exact text match (score = 100) — always matches.
        2. Substring match (one is contained in the other) — score = ratio
           of the shorter to the longer text. Must be >= 80%.
        3. No match — new block gets defaults.

        Deleted blocks (old blocks with no match in new blocks) disappear.
        New blocks (no match in old blocks) get defaults.
        Changed blocks inherit overrides only if the change is small enough
        to score >= 80%.

        P3.23 (design record §25) — Character preservation through Re-detect:
            - Matched (exact or >= 80% similarity) → Character transfers.
            - NOT matched but the old block HAD a Character → the new block
              keeps NO character_id (no unverified voice is used) but records
              lost_character_id so the UI can warn ("B1 ⚠") and offer
              re-assignment. The Character never silently disappears.
        """
        if not self._blocks:
            self.auto_detect(text)
            return
        # Old blocks' offsets are relative to self._last_text (the text they
        # were detected from). Use self._last_text to extract old block text.
        old_blocks = [(b, self.get_block_text(b, self._last_text))
                      for b in self._blocks]
        new_blocks = self._detector.detect(text)
        # Track which old blocks have been consumed (one-to-one matching).
        # This prevents an old block's overrides from being assigned to
        # multiple new blocks.
        used_old = set()
        # First pass: exact matches (highest confidence, 1:1).
        for new_block in new_blocks:
            new_text = text[new_block.start_offset:new_block.end_offset].strip()
            if not new_text:
                continue
            for i, (old_block, old_text) in enumerate(old_blocks):
                if i in used_old:
                    continue
                old_text_stripped = old_text.strip()
                if not old_text_stripped:
                    continue
                if new_text == old_text_stripped:
                    self._transfer_overrides(old_block, new_block)
                    used_old.add(i)
                    break
        # Second pass: fuzzy substring matches for remaining new blocks.
        # Only unmatched new blocks and unmatched old blocks participate.
        for new_block in new_blocks:
            # Skip if already matched in the first pass.
            if new_block.manually_edited:
                continue
            new_text = text[new_block.start_offset:new_block.end_offset].strip()
            if not new_text:
                continue
            best_match_idx = -1
            best_score = 0
            for i, (old_block, old_text) in enumerate(old_blocks):
                if i in used_old:
                    continue
                old_text_stripped = old_text.strip()
                if not old_text_stripped:
                    continue
                # Substring check — one must contain the other.
                if new_text in old_text_stripped or old_text_stripped in new_text:
                    score = min(len(new_text), len(old_text_stripped)) / \
                            max(len(new_text), len(old_text_stripped)) * 100
                    if score > best_score:
                        best_match_idx = i
                        best_score = score
            if best_match_idx >= 0 and best_score >= 80:
                old_block = old_blocks[best_match_idx][0]
                self._transfer_overrides(old_block, new_block)
                used_old.add(best_match_idx)
        # P3.23 third pass (design §25 — no silent Character loss):
        # any old block that HAD a Character but was NOT matched to a new
        # block donates its Character as lost_character_id to the new block
        # with the highest text overlap (best-effort recovery), so the user
        # is warned and can re-assign instead of the assignment vanishing.
        unmatched_old = [i for i in range(len(old_blocks))
                         if i not in used_old
                         and old_blocks[i][0].character_id]
        if unmatched_old:
            for i in unmatched_old:
                old_block, old_text = old_blocks[i]
                old_text_stripped = old_text.strip()
                if not old_text_stripped:
                    continue
                # Candidate new blocks: not carrying a character and not
                # already carrying a lost_character_id.
                best_nb = None
                best_overlap = 0.0
                for nb in new_blocks:
                    if nb.character_id or nb.lost_character_id:
                        continue
                    nb_text = text[nb.start_offset:nb.end_offset].strip()
                    if not nb_text:
                        continue
                    if nb_text in old_text_stripped or old_text_stripped in nb_text:
                        overlap = min(len(nb_text), len(old_text_stripped)) / \
                                  max(len(nb_text), len(old_text_stripped))
                        if overlap > best_overlap:
                            best_overlap = overlap
                            best_nb = nb
                if best_nb is not None and best_overlap > 0:
                    best_nb.lost_character_id = old_block.character_id
                    best_nb.manually_edited = True
                    logger.info(
                        "Re-detect: character '%s' not reliably matched — "
                        "recorded as lost_character_id (warning + re-assign).",
                        old_block.character_id)
        self._blocks = new_blocks
        self._last_text = text

    @staticmethod
    def _transfer_overrides(old_block: PromptBlock,
                            new_block: PromptBlock) -> None:
        """Transfer override values, label, locked, manual state AND the
        Character assignment from the old block to the new block. Does NOT
        transfer offsets (the new block keeps its own detected offsets) or
        id (the new block keeps its own identity).

        P3.23 (design record §25): the Character assignment is an explicit
        semantic user decision — it transfers with a successful similarity
        match, and any stale lost_character_id warning is cleared.
        """
        new_block.emotion = old_block.emotion
        new_block.style = old_block.style
        new_block.speed = old_block.speed
        new_block.pitch = old_block.pitch
        new_block.delivery = old_block.delivery
        new_block.label = old_block.label
        new_block.locked = old_block.locked
        new_block.manually_edited = True
        new_block.character_id = old_block.character_id
        new_block.lost_character_id = None

    def split_block(self, block_id: str, split_offset: int) -> bool:
        block = self.get_block(block_id)
        if block is None:
            return False
        if split_offset <= block.start_offset or split_offset >= block.end_offset:
            return False
        new_block = PromptBlock(
            start_offset=split_offset,
            end_offset=block.end_offset,
            emotion=block.emotion, style=block.style,
            speed=block.speed, pitch=block.pitch, delivery=block.delivery,
            label=block.label, locked=block.locked, manually_edited=True,
            # P3.23 (design §25): Split preserves the Character on BOTH
            # resulting blocks — the text still belongs to the same speaker.
            character_id=block.character_id,
        )
        block.end_offset = split_offset
        block.manually_edited = True
        idx = self._blocks.index(block)
        self._blocks.insert(idx + 1, new_block)
        return True

    def merge_with_above(self, block_id: str) -> bool:
        block = self.get_block(block_id)
        if block is None:
            return False
        idx = self._blocks.index(block)
        if idx == 0:
            return False
        above = self._blocks[idx - 1]
        # P3.23 (design §25): Merge preserves the Character. The surviving
        # block keeps its own Character; if it has none, it inherits the
        # removed block's Character (the merged text is still spoken by
        # that speaker). If BOTH have (different) Characters, the survivor
        # keeps its own and the removed block's assignment is recorded as
        # lost_character_id so nothing silently disappears.
        if not above.character_id and block.character_id:
            above.character_id = block.character_id
        elif (above.character_id and block.character_id
              and above.character_id != block.character_id):
            above.lost_character_id = block.character_id
        above.end_offset = block.end_offset
        above.manually_edited = True
        self._blocks.remove(block)
        return True

    def delete_block(self, block_id: str) -> bool:
        block = self.get_block(block_id)
        if block is None:
            return False
        self._blocks.remove(block)
        return True

    def toggle_lock(self, block_id: str) -> bool:
        block = self.get_block(block_id)
        if block is None:
            return False
        block.locked = not block.locked
        block.manually_edited = True
        return True

    def set_override(self, block_id: str, property_name: str,
                     value: Optional[str]) -> bool:
        block = self.get_block(block_id)
        if block is None:
            return False
        if hasattr(block, property_name):
            setattr(block, property_name, value)
            block.manually_edited = True
            return True
        return False

    def set_label(self, block_id: str, label: Optional[str]) -> bool:
        block = self.get_block(block_id)
        if block is None:
            return False
        block.label = label
        block.manually_edited = True
        return True

    def on_text_changed(self, new_text: str) -> None:
        if not self._blocks:
            self._last_text = new_text
            return
        old_text = self._last_text
        if new_text == old_text:
            return
        common_prefix = 0
        min_len = min(len(old_text), len(new_text))
        while common_prefix < min_len and old_text[common_prefix] == new_text[common_prefix]:
            common_prefix += 1
        common_suffix = 0
        while (common_suffix < min_len - common_prefix and
               old_text[len(old_text) - 1 - common_suffix] ==
               new_text[len(new_text) - 1 - common_suffix]):
            common_suffix += 1
        old_change_start = common_prefix
        old_change_end = len(old_text) - common_suffix
        new_change_start = common_prefix
        new_change_end = len(new_text) - common_suffix
        delta = (new_change_end - new_change_start) - (old_change_end - old_change_start)
        if delta == 0:
            for block in self._blocks:
                if old_change_start <= block.start_offset < old_change_end:
                    block.manually_edited = True
            self._last_text = new_text
            return
        for block in self._blocks:
            if old_change_start <= block.start_offset:
                block.start_offset += delta
                block.end_offset += delta
            elif old_change_start < block.end_offset:
                block.end_offset += delta
                block.manually_edited = True
            if block.start_offset < 0:
                block.start_offset = 0
            if block.end_offset < block.start_offset:
                block.end_offset = block.start_offset
        self._last_text = new_text

    def on_multi_replace(
        self,
        old_text: str,
        new_text: str,
        matches: List[Tuple[int, int]],
        replacement: str,
    ) -> None:
        """Handle Find and Replace All with multiple disjoint replacements.

        Each match is a ``(start, end)`` position in ``old_text``. All matches
        are replaced with the same ``replacement`` string. This adjusts block
        offsets correctly for each independent replacement — it does NOT treat
        the span from the first match to the last match as one contiguous edit
        (which was the root cause of the offset corruption bug).

        The algorithm:
        1. Sort matches by position (ascending).
        2. Process blocks in order of their original start offset.
        3. For each block:
           a. Consume all matches that end at or before the block's original
              start offset. Their length deltas are accumulated into
              ``cumulative_delta``.
           b. Apply ``cumulative_delta`` to both ``start_offset`` and
              ``end_offset`` (shifts the block to its new position).
           c. Consume all matches that overlap with the block's original
              range. For each overlapping match, add its length delta to
              ``end_offset`` and to ``cumulative_delta``. Mark the block as
              ``manually_edited``.
        4. Update ``_last_text`` to ``new_text`` so the subsequent
           ``textChanged`` signal from ``setPlainText`` is a no-op in
           ``on_text_changed``.

        This preserves:
        - Block identity (the same PromptBlock objects remain in the list)
        - Block ordering (blocks are processed in ascending offset order)
        - Block override values (emotion/style/speed/pitch/delivery untouched)
        - Locked state (``locked`` flag untouched)
        - Manual state (``manually_edited`` is only set True, never cleared)
        - Character assignment (design record §34: Character belongs to
          block identity, not text position — untouched by Replace/Replace
          All; the same block objects survive the replacement)

        Args:
            old_text: the full text before replacement.
            new_text: the full text after replacement.
            matches: list of ``(start, end)`` positions in ``old_text``.
                     Each position is a character offset; ``end`` is exclusive.
            replacement: the replacement string (same for all matches).
        """
        if not self._blocks:
            self._last_text = new_text
            return

        if not matches:
            self._last_text = new_text
            return

        # Sort matches by start position (ascending).
        sorted_matches = sorted(matches)
        replacement_len = len(replacement)

        # Snapshot original offsets — we modify blocks in place, so we need
        # the original values to test match overlap correctly.
        original_offsets = [(b, b.start_offset, b.end_offset) for b in self._blocks]
        # Process blocks in ascending start-offset order. We iterate over the
        # snapshot (not the live list) so reordering inside the list (if any)
        # doesn't affect the loop. The blocks themselves are modified in place.
        ordered = sorted(original_offsets, key=lambda t: t[1])

        cumulative_delta = 0
        match_idx = 0
        n_matches = len(sorted_matches)

        for block, orig_start, orig_end in ordered:
            # (a) Consume matches that end at or before this block's original
            #     start. Their entire length delta applies to everything after.
            while (match_idx < n_matches
                   and sorted_matches[match_idx][1] <= orig_start):
                m_start, m_end = sorted_matches[match_idx]
                cumulative_delta += replacement_len - (m_end - m_start)
                match_idx += 1

            # (b) Shift the block by the cumulative delta so far.
            block.start_offset = orig_start + cumulative_delta
            block.end_offset = orig_end + cumulative_delta

            # (c) Consume matches that overlap with the block's original range.
            #     A match overlaps if its start < block's original end AND
            #     its end > block's original start. Since we already consumed
            #     matches ending <= orig_start in step (a), any remaining match
            #     with start < orig_end overlaps this block.
            while match_idx < n_matches:
                m_start, m_end = sorted_matches[match_idx]
                if m_start >= orig_end:
                    # Match is entirely after this block — stop.
                    break
                # Match overlaps with this block.
                delta = replacement_len - (m_end - m_start)
                block.end_offset += delta
                cumulative_delta += delta
                block.manually_edited = True
                match_idx += 1

            # Clamp to valid range.
            if block.start_offset < 0:
                block.start_offset = 0
            if block.end_offset < block.start_offset:
                block.end_offset = block.start_offset

        # Update _last_text so the textChanged signal triggered by
        # setPlainText(new_text) is a no-op in on_text_changed().
        self._last_text = new_text
        logger.info(
            "on_multi_replace: %d matches, %d blocks adjusted",
            len(sorted_matches), len(self._blocks),
        )

    def to_dict(self) -> List[dict]:
        return [b.to_dict() for b in self._blocks]

    def from_dict(self, data: List[dict]) -> None:
        self._blocks = [PromptBlock.from_dict(d) for d in data]

    def clear(self) -> None:
        self._blocks.clear()
        self._last_text = ""
