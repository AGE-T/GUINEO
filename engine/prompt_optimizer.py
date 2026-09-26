"""
SpeechStudio Engine - Prompt Optimizer.

Takes a list of Narration Blocks (with overrides + global defaults) and
produces an optimized token emission plan that minimizes the number of
Higgs tokens while preserving identical behavior.

This is a pure function: states in, emission plan out. No side effects.

Architecture:
    Narration Blocks + Global Defaults
        |
        v
    PromptOptimizer (this module)
        |
        v
    TokenEmission plan
        |
        v
    PromptBuilder (converts to Higgs syntax)
"""

from __future__ import annotations
from typing import List, Optional

from engine.narration_blocks import (
    PromptBlock, EffectiveBlockState, TokenEmission,
)
from engine.logger import get_logger

logger = get_logger("prompt_optimizer")


class PromptOptimizer:
    """Optimizes a sequence of block states to minimize emitted tokens."""

    def resolve_and_optimize(
        self,
        blocks: List[PromptBlock],
        text: str,
        global_emotion: Optional[str] = None,
        global_style: Optional[str] = None,
        global_speed: Optional[str] = None,
        global_pitch: Optional[str] = None,
        global_delivery: Optional[str] = None,
    ) -> List[EffectiveBlockState]:
        """Resolve inherited values and produce effective states."""
        if not blocks:
            return [EffectiveBlockState(
                block_index=0, text=text,
                emotion=global_emotion, style=global_style,
                speed=global_speed, pitch=global_pitch,
                delivery=global_delivery,
            )]

        states: List[EffectiveBlockState] = []

        for i, block in enumerate(blocks):
            block_text = text[block.start_offset:block.end_offset]
            emotion = block.emotion if block.emotion is not None else global_emotion
            style = block.style if block.style is not None else global_style
            speed = block.speed if block.speed is not None else global_speed
            pitch = block.pitch if block.pitch is not None else global_pitch
            delivery = block.delivery if block.delivery is not None else global_delivery

            states.append(EffectiveBlockState(
                block_index=i, text=block_text,
                emotion=emotion, style=style, speed=speed,
                pitch=pitch, delivery=delivery,
                sfx_insertions=list(block.sfx_insertions),
                pause_insertions=list(block.pause_insertions),
            ))

        return states

    def optimize_emissions(
        self,
        states: List[EffectiveBlockState],
    ) -> List[TokenEmission]:
        """Produce the minimal set of token emissions.

        Only emit a token when the effective value CHANGES from the
        previous block.
        """
        emissions: List[TokenEmission] = []
        prev_emotion: Optional[str] = None
        prev_style: Optional[str] = None
        prev_speed: Optional[str] = None
        prev_pitch: Optional[str] = None
        prev_delivery: Optional[str] = None

        for state in states:
            if state.emotion != prev_emotion:
                if state.emotion and state.emotion != "Normal":
                    emissions.append(TokenEmission(
                        state.block_index, "emotion", state.emotion))
                prev_emotion = state.emotion

            if state.style != prev_style:
                if state.style:
                    emissions.append(TokenEmission(
                        state.block_index, "style", state.style))
                prev_style = state.style

            if state.speed != prev_speed:
                if state.speed and state.speed != "Normal":
                    emissions.append(TokenEmission(
                        state.block_index, "prosody", state.speed))
                prev_speed = state.speed

            if state.pitch != prev_pitch:
                if state.pitch and state.pitch != "Normal":
                    emissions.append(TokenEmission(
                        state.block_index, "prosody", state.pitch))
                prev_pitch = state.pitch

            if state.delivery != prev_delivery:
                if state.delivery and state.delivery != "Normal":
                    emissions.append(TokenEmission(
                        state.block_index, "prosody", state.delivery))
                prev_delivery = state.delivery

        logger.info("Optimized: %d blocks -> %d token emissions",
                    len(states), len(emissions))
        return emissions
