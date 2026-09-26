"""
SpeechStudio Engine - Block Detector.

Heuristic, local, deterministic detection of Narration Block boundaries.

This is a pure function: text in, block boundaries out. No state, no side
effects. A future AI Director can replace this class without changing any
other component — the interface is just detect(text) -> List[PromptBlock].
"""

from __future__ import annotations
import re
from typing import List

from engine.narration_blocks import PromptBlock
from engine.logger import get_logger

logger = get_logger("block_detector")


# P3.44.2 — inline SFX/pause markers ({sfx:Name:Onom} / {pause} /
# {long_pause}). Punctuation inside these markers belongs to the
# onomatopoeia and must never create a sentence boundary; a marker that
# follows sentence punctuation belongs to the sentence it annotates.
# Kept in sync with engine.prompt_builder._INLINE_MARKER_RE.
_INLINE_MARKER_RE = re.compile(
    r"\{sfx:[^:}]+:[^}]+\}|\{pause\}|\{long_pause\}")


_NEW_BLOCK_MARKERS = [
    "but", "however", "yet", "on the other hand", "in contrast",
    "nevertheless", "nonetheless", "still", "though",
    "then", "next", "afterwards", "meanwhile", "finally",
    "subsequently", "later",
    "in conclusion", "to summarize", "overall", "in summary",
    "to conclude", "ultimately",
    "let's talk about", "moving on", "now", "so",
    "first", "second", "third", "lastly",
]

_CONTINUATION_MARKERS = [
    "it", "this", "that", "he", "she", "they", "we", "which",
    "and", "also", "plus", "additionally", "moreover", "furthermore",
]


class BlockDetector:
    """Base class for block detection. Subclass to replace the algorithm."""

    def detect(self, text: str) -> List[PromptBlock]:
        raise NotImplementedError


class HeuristicBlockDetector(BlockDetector):
    """Local, deterministic, heuristic-based block detector."""

    def detect(self, text: str) -> List[PromptBlock]:
        if not text or not text.strip():
            return []

        sentences = self._split_sentences(text)
        if len(sentences) <= 1:
            return [PromptBlock(start_offset=0, end_offset=len(text))]

        blocks: List[PromptBlock] = []
        current_start = sentences[0]["start"]

        for i in range(1, len(sentences)):
            prev = sentences[i - 1]
            curr = sentences[i]

            between = text[prev["end"]:curr["start"]]
            if "\n\n" in between:
                score = 100
            else:
                score = self._boundary_score(prev, curr, between)

            if score >= 50:
                blocks.append(PromptBlock(
                    start_offset=current_start,
                    end_offset=prev["end"],
                ))
                current_start = curr["start"]

        blocks.append(PromptBlock(
            start_offset=current_start,
            end_offset=len(text),
        ))

        logger.info("Detected %d narration blocks from %d sentences",
                    len(blocks), len(sentences))
        return blocks

    def _split_sentences(self, text: str) -> List[dict]:
        """Split text into sentences with character offsets.

        Newlines (including paragraph breaks \n\n) are left in the gap
        between sentences so the detector can find them.

        P3.44.2 — SFX/pause marker awareness (consistent with
        NarrationSplitter._split_sentences so detection and batch
        splitting agree on sentence semantics):
          1. SHIELDING: punctuation inside an inline marker (e.g. the "!"
             in ``{sfx:Laughter:Ha!ha}``) never ends a sentence — a
             marker can never be cut in half by a block boundary.
          2. ATTACHMENT: a marker that follows sentence punctuation (with
             or without whitespace) belongs to the sentence it annotates
             — the sentence END lands AFTER the marker chain, so the
             marker never starts the next block orphaned from its
             sentence.
        """
        sentences = []
        pattern = re.compile(r"[.!?…]+")
        # Inline marker spans ({sfx:Name:Onom} / {pause} / {long_pause}).
        marker_spans = [(m.start(), m.end())
                        for m in _INLINE_MARKER_RE.finditer(text)]

        def _marker_at(pos: int):
            for s, e in marker_spans:
                if s == pos:
                    return e
            return None

        pos = 0
        for match in pattern.finditer(text):
            # Rule 1 — punctuation inside a marker is shielded.
            if any(s <= match.start() < e for s, e in marker_spans):
                continue
            end = match.end()
            # Rule 2 — attach a following marker chain (optional
            # whitespace between punctuation and markers).
            while end < len(text):
                j = end
                while j < len(text) and text[j] in " \t\r":
                    j += 1
                mk_end = _marker_at(j) if j < len(text) else None
                if mk_end is None:
                    break
                end = mk_end
            while end < len(text) and text[end] in " \t\r":
                end += 1

            sentence_text = text[pos:end].strip()
            if sentence_text:
                sentences.append({
                    "text": sentence_text,
                    "start": pos,
                    "end": end,
                })
            # Skip newlines to find the start of the next sentence,
            # but DON'T include them in this sentence's end offset.
            next_pos = end
            while next_pos < len(text) and text[next_pos] in "\n":
                next_pos += 1
            pos = next_pos

        if pos < len(text):
            remaining = text[pos:].strip()
            if remaining:
                sentences.append({
                    "text": remaining,
                    "start": pos,
                    "end": len(text),
                })

        return sentences

    def _boundary_score(self, prev: dict, curr: dict, between: str) -> int:
        score = 0

        if "\n" in between:
            score += 30

        curr_words = curr["text"].lower().split()[:3]
        curr_start = " ".join(curr_words)

        for marker in _NEW_BLOCK_MARKERS:
            if curr_start.startswith(marker):
                if marker in ("but", "however", "yet", "on the other hand",
                              "in contrast", "nevertheless"):
                    score += 80
                elif marker in ("in conclusion", "to summarize", "overall",
                                "in summary", "to conclude", "ultimately"):
                    score += 75
                elif marker in ("then", "next", "afterwards", "meanwhile",
                                "finally", "subsequently", "later"):
                    score += 70
                else:
                    score += 65
                break

        for marker in _CONTINUATION_MARKERS:
            if curr_start.startswith(marker):
                if marker in ("it", "this", "that", "he", "she", "they",
                              "we", "which"):
                    score -= 30
                else:
                    score -= 20
                break

        prev_len = len(prev["text"])
        curr_len = len(curr["text"])
        if prev_len > 200 and curr_len < 50:
            score += 20

        if prev["text"].rstrip().endswith("?") and not curr["text"].rstrip().endswith("?"):
            score += 40

        return score
