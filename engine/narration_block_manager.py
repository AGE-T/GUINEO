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
import difflib
import uuid

from engine.narration_blocks import (
    PromptBlock, SfxInsertion, PauseInsertion, NarrationTemplate,
)
from engine.block_detector import BlockDetector, HeuristicBlockDetector
from engine.logger import get_logger

logger = get_logger("narration_block_manager")


def _norm_ws(s: str) -> str:
    """Whitespace-normalised text (P3.44.7 split-coverage comparison)."""
    return " ".join(s.split())


def _relatedness(old_text: str, new_text: str) -> float:
    """Deterministic relatedness score in [0.0, 1.0] between two texts.

    P3.44.7 — the historical containment ratio (one text inside the
    other, length ratio) is kept as the primary signal; a difflib
    SequenceMatcher ratio is added so plain substitution edits ("sat"
    → "stood"), which break containment ENTIRELY, still measure as
    related. Pure function of the two texts — fully deterministic.
    """
    if not old_text or not new_text:
        return 0.0
    containment = 0.0
    if new_text in old_text or old_text in new_text:
        containment = min(len(new_text), len(old_text)) / \
                      max(len(new_text), len(old_text))
    ratio = difflib.SequenceMatcher(
        None, old_text, new_text, autojunk=False).ratio()
    return max(containment, ratio)


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
        state, Character assignment AND SFX/pause insertions are preserved
        if their text still exists in the new text (design record §25:
        "Locked block: preserve Character"; P3.44.7 extends the same
        guarantee to the span-local SFX/pause metadata).
        Automatic (non-locked) blocks are recalculated from scratch.

        P3.44.7 — two preservation upgrades (path-equivalence with
        ``preserve_overrides``, see docs/design/
        P3_44_7_REDETECT_SEMANTIC_PRESERVATION.md):
          1. A locked block whose text was SPLIT into consecutive new
             blocks (whitespace-normalised coverage) transfers its
             semantics to every child — the text still exists, in pieces.
          2. A locked block whose text no longer exists anywhere donates
             its Character as ``lost_character_id`` to the most related
             new block — the Character never silently disappears.

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
                    # P3.23 (design §25): locked block → Character preserved
                    # (plus overrides/label/lock and — P3.44.7 — the SFX/pause
                    # insertions with deterministic offset mapping).
                    self._transfer_overrides(old_block, new_block,
                                             old_text, new_text)
                    used_old.add(i)
                    break
        # P3.44.7 split pass: a locked block split into consecutive new
        # blocks keeps its semantics on every child. Returns the indices
        # (into locked_blocks) it additionally consumed.
        split_consumed = self._apply_split_pass(
            locked_blocks, used_old, new_blocks, text)
        consumed = used_old | split_consumed
        # P3.44.7 lost-Character donation for locked blocks whose text is
        # gone entirely (same rule as preserve_overrides pass 3).
        self._donate_lost_characters(
            [locked_blocks[i] for i in range(len(locked_blocks))
             if i not in consumed], new_blocks, text)
        self._blocks = new_blocks
        self._last_text = text

    def preserve_overrides(self, text: str) -> None:
        """Re-detect blocks while preserving semantic state for matching blocks.

        Matching passes (in order; all deterministic):

        1. EXACT — stripped text equality, one-to-one. Always transfers.
        2. SIMILARITY — best one-to-one score among unmatched blocks,
           threshold >= 80%. The score is ``_relatedness`` (P3.44.7):
           the historical containment ratio OR a difflib ratio, whichever
           is larger — so plain substitution edits ("sat" → "stood"),
           which break containment entirely, still match.
        3. SPLIT / union coverage (P3.44.7) — when consecutive unmatched
           new blocks' whitespace-normalised concatenation EQUALS one
           unmatched old block's text, the old block was genuinely split:
           block-wide semantics transfer to EVERY child and span-local
           SFX/pause insertions allocate to the child whose mapped span
           contains them.
        4. LOST-CHARACTER — any still-unmatched old block that HAD a
           Character donates ``lost_character_id`` to the most related
           new block (warning + re-assignment; never silent loss).

        Transfer semantics per field class:
        - block-wide (emotion/style/speed/pitch/delivery, label, locked,
          Character): transfer on passes 1-2; on a split, EVERY child.
        - span-local (sfx/pause insertions, offsets relative to the block
          text): transfer with deterministic offset mapping; an insertion
          whose anchor text no longer exists is DROPPED (logged) — it is
          never guessed onto unrelated text.

        Deleted blocks (old blocks with no match) disappear. New blocks
        (no match) get defaults. Changed blocks inherit only when the
        relatedness score is sufficient (unambiguous mapping).

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
        # multiple unrelated new blocks.
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
                    self._transfer_overrides(
                        old_block, new_block, old_text,
                        text[new_block.start_offset:new_block.end_offset])
                    used_old.add(i)
                    break
        # Second pass: similarity matches for remaining new blocks.
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
                # P3.44.7: containment ratio OR difflib ratio — whichever
                # is larger (substitution edits break containment).
                score = _relatedness(old_text_stripped, new_text) * 100
                if score > best_score:
                    best_match_idx = i
                    best_score = score
            if best_match_idx >= 0 and best_score >= 80:
                old_block, old_text = old_blocks[best_match_idx]
                self._transfer_overrides(
                    old_block, new_block, old_text,
                    text[new_block.start_offset:new_block.end_offset])
                used_old.add(best_match_idx)
        # P3.44.7 pass 2.5 — SPLIT / union coverage: one old block whose
        # text is the concatenation of consecutive unmatched new blocks.
        split_consumed = self._apply_split_pass(
            old_blocks, used_old, new_blocks, text)
        used_old |= split_consumed
        # P3.23 third pass (design §25 — no silent Character loss), with
        # the P3.44.7 difflib extension for substitution edits:
        # any old block that HAD a Character but was NOT matched to a new
        # block donates its Character as lost_character_id to the new block
        # with the highest relatedness (best-effort recovery), so the user
        # is warned and can re-assign instead of the assignment vanishing.
        self._donate_lost_characters(
            [old_blocks[i] for i in range(len(old_blocks))
             if i not in used_old], new_blocks, text)
        self._blocks = new_blocks
        self._last_text = text

    @staticmethod
    def _transfer_overrides(old_block: PromptBlock,
                            new_block: PromptBlock,
                            old_text: Optional[str] = None,
                            new_text: Optional[str] = None) -> None:
        """Transfer override values, label, locked, manual state AND the
        Character assignment from the old block to the new block. Does NOT
        transfer offsets (the new block keeps its own detected offsets) or
        id (the new block keeps its own identity).

        P3.23 (design record §25): the Character assignment is an explicit
        semantic user decision — it transfers with a successful similarity
        match, and any stale lost_character_id warning is cleared.

        P3.44.7 — SFX/pause insertions are span-local semantic metadata:
        they transfer with a deterministic offset mapping when the texts
        are known (``old_text``/``new_text`` are the RAW block texts). An
        insertion whose anchor position cannot be mapped is dropped
        (logged) — never guessed onto unrelated text. Without texts the
        insertions copy verbatim (only valid for identical text; the
        materializer's range guard drops corrupt offsets defensively).
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
        if old_text is not None and new_text is not None:
            NarrationBlockManager._map_insertions(
                old_block, new_block, old_text, new_text)
        else:
            new_block.sfx_insertions = list(old_block.sfx_insertions)
            new_block.pause_insertions = list(old_block.pause_insertions)

    @staticmethod
    def _map_offset(old_text: str, new_text: str, offset: int) -> Optional[int]:
        """Map a block-relative insertion offset old text → new text.

        Deterministic rules, in order:
        1. identical texts           → identity;
        2. new text inside old text  → shift by the substring position
                                       (split/shrink: an offset OUTSIDE
                                       the new span returns None — it
                                       belongs to a sibling block);
        3. old text inside new text  → shift by the substring position
                                       (growth/merge);
        4. otherwise                 → difflib matching blocks: an offset
                                       inside a matching block maps
                                       through it; an offset in an EDITED
                                       region returns None (the anchor
                                       text no longer exists).
        """
        if old_text == new_text:
            return offset
        if new_text and new_text in old_text:
            pos = old_text.find(new_text)
            if pos <= offset < pos + len(new_text):
                return offset - pos
            return None
        if old_text and old_text in new_text:
            return offset + new_text.find(old_text)
        for a, b, size in difflib.SequenceMatcher(
                None, old_text, new_text, autojunk=False).get_matching_blocks():
            if size and a <= offset < a + size:
                return b + (offset - a)
        return None

    @staticmethod
    def _map_insertions(old_block: PromptBlock, new_block: PromptBlock,
                        old_text: str, new_text: str) -> None:
        """Transfer sfx/pause insertions with deterministic offset mapping.

        Unmappable anchors (the user edited away the very text at the
        insertion point) are dropped and logged — the explicit outcome
        for ambiguity mandated by the P3.44.7 product rule.
        """
        mapped_sfx = []
        for ins in old_block.sfx_insertions:
            off = NarrationBlockManager._map_offset(old_text, new_text, ins.offset)
            if off is None:
                logger.info(
                    "Re-detect: SFX '%s' at offset %d is not mappable to "
                    "the matched block text — dropped deterministically.",
                    ins.sfx_name, ins.offset)
                continue
            mapped_sfx.append(SfxInsertion(ins.sfx_name, ins.onomatopoeia, off))
        mapped_pauses = []
        for ins in old_block.pause_insertions:
            off = NarrationBlockManager._map_offset(old_text, new_text, ins.offset)
            if off is None:
                logger.info(
                    "Re-detect: %s at offset %d is not mappable to the "
                    "matched block text — dropped deterministically.",
                    ins.pause_type, ins.offset)
                continue
            mapped_pauses.append(PauseInsertion(ins.pause_type, off))
        new_block.sfx_insertions = mapped_sfx
        new_block.pause_insertions = mapped_pauses

    def _apply_split_pass(self, old_pairs, used_old, new_blocks, text) -> set:
        """P3.44.7 SPLIT / union-coverage pass.

        For every UNCONSUMED old block that carries semantic state, look
        for the first run of consecutive UNMATCHED new blocks whose
        whitespace-normalised concatenation equals the old block's
        whitespace-normalised text — i.e. the detector split the old
        block. On a hit:
        - block-wide semantics (overrides, label, locked, Character)
          transfer to EVERY child;
        - span-local SFX/pause insertions allocate to the child whose
          mapped region within the old text contains the offset
          (sequential search — a repeated child text maps to the NEXT
          unused occurrence, so insertions can never double-allocate);
        - the old block is consumed.

        Returns the set of consumed indices into ``old_pairs``.
        """
        consumed = set()
        if not new_blocks:
            return consumed
        new_raw = [text[nb.start_offset:nb.end_offset] for nb in new_blocks]
        for i, (old_block, old_text) in enumerate(old_pairs):
            if i in used_old or i in consumed:
                continue
            if not (old_block.character_id or old_block.has_overrides()
                    or old_block.locked):
                continue
            old_norm = _norm_ws(old_text)
            if not old_norm:
                continue
            run = self._find_covering_run(new_blocks, new_raw, old_norm)
            if run is None:
                continue
            self._apply_split_transfer(
                old_block, old_text, new_blocks, new_raw, run)
            consumed.add(i)
            logger.info(
                "Re-detect split: old block '%s' covered by new blocks "
                "[%d..%d] — semantics transferred to every child.",
                old_block.id, run[0], run[1])
        return consumed

    @staticmethod
    def _find_covering_run(new_blocks, new_raw, old_norm):
        """First run of consecutive unmatched new blocks covering old_norm.

        Greedy prefix scan: extend a run while its whitespace-normalised
        concatenation is a prefix of ``old_norm``; return ``(i, j)`` on
        exact equality. ``new_block.manually_edited`` marks new blocks
        already matched by passes 1-2 (they can never be split children).
        Returns None when no run covers the old text.
        """
        n = len(new_blocks)
        for i in range(n):
            if new_blocks[i].manually_edited:
                continue
            first = _norm_ws(new_raw[i])
            if not first:
                continue
            parts = [first]
            for j in range(i, n):
                if new_blocks[j].manually_edited:
                    break
                if j > i:
                    ext = _norm_ws(new_raw[j])
                    if not ext:
                        break
                    parts.append(ext)
                joined = " ".join(parts)
                if joined == old_norm:
                    return (i, j)
                if not old_norm.startswith(joined):
                    break
        return None

    @staticmethod
    def _apply_split_transfer(old_block, old_text, new_blocks, new_raw, run):
        """Transfer one split old block's semantics to its children."""
        i, j = run
        children = new_blocks[i:j + 1]
        # Each child's region within the OLD raw text (sequential search;
        # difflib matching blocks as whitespace-reflow fallback).
        regions = []
        cursor = 0
        for k in range(i, j + 1):
            raw = new_raw[k]
            region = None
            if raw:
                p = old_text.find(raw, cursor)
                if p >= 0:
                    region = (p, p + len(raw))
                    cursor = region[1]
            if region is None and raw:
                mb = [m for m in difflib.SequenceMatcher(
                    None, old_text, raw, autojunk=False).get_matching_blocks()
                    if m.size > 0]
                if mb:
                    lo, hi = mb[0].a, mb[-1].a + mb[-1].size
                    if hi > lo:
                        region = (lo, hi)
                        cursor = hi
            regions.append(region)
        # Block-wide semantics → EVERY child (the text still belongs to
        # the same speaker/semantic context).
        for child in children:
            child.emotion = old_block.emotion
            child.style = old_block.style
            child.speed = old_block.speed
            child.pitch = old_block.pitch
            child.delivery = old_block.delivery
            child.label = old_block.label
            child.locked = old_block.locked
            child.manually_edited = True
            child.character_id = old_block.character_id
            child.lost_character_id = None
            child.sfx_insertions = []
            child.pause_insertions = []
        # Span-local insertions → the FIRST child region containing them.
        for ins in old_block.sfx_insertions:
            for idx, region in enumerate(regions):
                if region and region[0] <= ins.offset < region[1]:
                    children[idx].sfx_insertions.append(SfxInsertion(
                        ins.sfx_name, ins.onomatopoeia, ins.offset - region[0]))
                    break
            else:
                logger.info(
                    "Re-detect split: SFX '%s' at offset %d falls in no "
                    "child span — dropped deterministically.",
                    ins.sfx_name, ins.offset)
        for ins in old_block.pause_insertions:
            for idx, region in enumerate(regions):
                if region and region[0] <= ins.offset < region[1]:
                    children[idx].pause_insertions.append(PauseInsertion(
                        ins.pause_type, ins.offset - region[0]))
                    break
            else:
                logger.info(
                    "Re-detect split: %s at offset %d falls in no child "
                    "span — dropped deterministically.",
                    ins.pause_type, ins.offset)

    @staticmethod
    def _donate_lost_characters(unmatched_pairs, new_blocks, text) -> None:
        """P3.23 §25 / P3.44.7 — no silent Character loss.

        Every unmatched old block that HAD a Character donates it as
        ``lost_character_id`` to the most related new block that carries
        neither a Character nor a lost warning. Relatedness = containment
        ratio (the legacy P3.23 rule, any overlap > 0) OR a difflib ratio
        >= 0.5 (the P3.44.7 extension for substitution edits). A block
        with NO related new block (the text is gone entirely) records
        nothing — there is nothing plausible to warn on.
        """
        for old_block, old_text in unmatched_pairs:
            if not old_block.character_id:
                continue
            old_text_stripped = old_text.strip()
            if not old_text_stripped:
                continue
            best_nb = None
            best_score = 0.0
            for nb in new_blocks:
                if nb.character_id or nb.lost_character_id:
                    continue
                nb_text = text[nb.start_offset:nb.end_offset].strip()
                if not nb_text:
                    continue
                containment = 0.0
                if nb_text in old_text_stripped or old_text_stripped in nb_text:
                    containment = min(len(nb_text), len(old_text_stripped)) / \
                                  max(len(nb_text), len(old_text_stripped))
                ratio = difflib.SequenceMatcher(
                    None, old_text_stripped, nb_text, autojunk=False).ratio()
                score = max(containment, ratio)
                # Legacy containment donation (any overlap) kept verbatim;
                # difflib alone must reach 0.5 to count.
                if (containment > 0 or score >= 0.5) and score > best_score:
                    best_score = score
                    best_nb = nb
            if best_nb is not None and best_score > 0:
                best_nb.lost_character_id = old_block.character_id
                best_nb.manually_edited = True
                logger.info(
                    "Re-detect: character '%s' not reliably matched — "
                    "recorded as lost_character_id (warning + re-assign).",
                    old_block.character_id)

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

    # ------------------------------------------------------------------
    # P3.44.9 — the single authoritative block-offset transformation
    # ------------------------------------------------------------------
    @staticmethod
    def _apply_edit_to_blocks(blocks, cs: int, ce: int, inserted_len: int):
        """Apply ONE contiguous text edit to a list of blocks (P3.44.9).

        The edit replaces old text ``[cs, ce)`` with ``inserted_len``
        characters (``delta = inserted_len - (ce - cs)``). For every
        block ``[s, e)`` (old coordinates):

          1. ``e <= cs``   -> untouched (edit at/after the block end).
          2. ``ce <= s``   -> ``[s+delta, e+delta]`` (edit before block).
          3. ``s <= cs < e`` -> head intact, the block owns the
             replacement (the edit was typed inside it):
               ``e <= ce`` : ``[s, cs+inserted_len)``
               ``e >  ce`` : ``[s, cs+inserted_len+(e-ce))``
          4. ``cs < s < ce`` -> head replaced:
               ``e <= ce`` : REMOVED (nothing survives, nothing owned)
               ``e >  ce`` : ``[cs+inserted_len, cs+inserted_len+(e-ce))``

        Blocks whose final range owns zero characters (including
        pre-existing phantom ranges) are removed: a block that owns no
        source characters is not a block. Span-local SFX/pause
        insertion offsets ride the same absolute coordinate mapping.

        Returns the surviving block list (same objects, updated ranges).
        Purely deterministic — no context beyond (cs, ce, inserted_len).
        """
        delta = inserted_len - (ce - cs)
        kept = []
        for block in blocks:
            s, e = block.start_offset, block.end_offset
            if e <= cs:
                # Edit at/after the block end: the block is untouched.
                kept.append(block)
                continue
            if ce <= s:
                # Edit entirely before the block: shift by delta.
                block.start_offset = s + delta
                block.end_offset = e + delta
                kept.append(block)
                continue
            # Overlapping edit — the block's text content changed.
            repl_end = cs + inserted_len
            if s <= cs:
                # Head intact; the block owns the replacement text.
                new_s = s
                new_e = repl_end if e <= ce else repl_end + (e - ce)
            else:
                # cs < s: the block starts inside the replaced region.
                if e <= ce:
                    logger.info(
                        "Block '%s' [%d:%d] fully replaced by edit "
                        "[%d:%d) — removed (no surviving text).",
                        block.id, s, e, cs, ce)
                    continue
                new_s = repl_end
                new_e = repl_end + (e - ce)
            block.manually_edited = True
            NarrationBlockManager._remap_insertions(
                block, s, new_s, new_e, cs, ce, delta)
            block.start_offset = new_s
            block.end_offset = new_e
            kept.append(block)
        # A block that owns zero characters does not exist (P3.44.9:
        # this also heals legacy phantom ranges on the first edit).
        return [b for b in kept if b.start_offset < b.end_offset]

    @staticmethod
    def _remap_insertions(block: PromptBlock, old_start: int,
                          new_start: int, new_end: int,
                          cs: int, ce: int, delta: int) -> None:
        """Propagate block-relative SFX/pause offsets through one edit.

        The insertion offset rides the SAME absolute coordinate mapping
        as the block ranges: positions before the edit stay, positions
        at/after the edit end shift by delta, and an anchor INSIDE the
        replaced region deterministically clamps to the replacement
        start (the user's SFX is never silently dropped by a keystroke;
        the materializer's range guard keeps it valid).
        """
        new_len = new_end - new_start

        def _map_abs(x: int) -> int:
            if x < cs:
                return x
            if x >= ce:
                return x + delta
            return cs

        def _clamp(o: int) -> int:
            return max(0, min(o, new_len))

        if block.sfx_insertions:
            block.sfx_insertions = [
                SfxInsertion(i.sfx_name, i.onomatopoeia,
                             _clamp(_map_abs(old_start + i.offset) - new_start))
                for i in block.sfx_insertions]
        if block.pause_insertions:
            block.pause_insertions = [
                PauseInsertion(p.pause_type,
                               _clamp(_map_abs(old_start + p.offset) - new_start))
                for p in block.pause_insertions]

    def on_text_changed(self, new_text: str) -> None:
        """Track block offsets through a single contiguous text edit.

        P3.44.9 — the previous implementation classified blocks with
        ``old_change_start <= block.start_offset`` -> shift BOTH ends,
        which is only correct when the edit lies entirely BEFORE the
        block. A deletion starting exactly at a block boundary shifted
        the block INTO preceding text (the proven [53:99] -> [7:53]
        corruption: overlap, wrong text ownership, duplicated
        generation text, phantom blocks after save/load). The edit is
        now transformed through ``_apply_edit_to_blocks`` — the single
        authoritative rule shared with ``on_multi_replace``.
        """
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
        new_change_end = len(new_text) - common_suffix
        inserted_len = new_change_end - old_change_start
        if old_change_start == 0 and old_change_end == len(old_text):
            # Whole-text replacement: nothing of the old document
            # survives, so no block can keep ownership (set_text /
            # history-reuse / clear paths). Scene loads are exempt —
            # the P3.26 _loading_scene guard never reaches this method.
            self._blocks = []
            self._last_text = new_text
            return
        self._blocks = self._apply_edit_to_blocks(
            self._blocks, old_change_start, old_change_end, inserted_len)
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
        are replaced with the same ``replacement`` string.

        P3.44.9 — every match is now transformed through the SINGLE
        authoritative rule ``_apply_edit_to_blocks`` (the same rule as
        ``on_text_changed``), applied sequentially in ascending match
        order with a running coordinate shift. The previous per-block
        implementation adjusted only ``end_offset`` for matches
        overlapping a block, so a match COVERING a block's start left
        the start unmapped (lost surviving characters) and a match
        exactly covering a whole block left a zero-length phantom.

        This preserves:
        - Block identity (the same PromptBlock objects remain in the list)
        - Block ordering (the sequential rule is monotone)
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

        replacement_len = len(replacement)
        # Sequential application: after each match, the following text is
        # shifted by the accumulated delta of all matches processed so far,
        # so match k occupies [m_start+shift, m_end+shift) in the CURRENT
        # (partially transformed) coordinate system.
        shift = 0
        for m_start, m_end in sorted(matches):
            self._blocks = self._apply_edit_to_blocks(
                self._blocks, m_start + shift, m_end + shift,
                replacement_len)
            shift += replacement_len - (m_end - m_start)

        # Update _last_text so the textChanged signal triggered by
        # setPlainText(new_text) is a no-op in on_text_changed().
        self._last_text = new_text
        logger.info(
            "on_multi_replace: %d matches, %d blocks adjusted",
            len(matches), len(self._blocks),
        )

    def to_dict(self) -> List[dict]:
        return [b.to_dict() for b in self._blocks]

    def from_dict(self, data: List[dict]) -> None:
        self._blocks = [PromptBlock.from_dict(d) for d in data]

    def clear(self) -> None:
        self._blocks.clear()
        self._last_text = ""
