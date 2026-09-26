"""
SpeechStudio Engine - Higgs Audio V3 Token Reference
=====================================================

This module defines every officially supported Higgs Audio V3 control token.

Sources (REFERENCES.md priority order):
    1. Official PROMPTING.md  (research/PROMPTING_official.md)
    2. SpeechStudio Higgs Audio V3 Reference (spec/Higgs_Audio_V3_Reference.md)
    3. SpeechStudio Prompt System (spec/05_Prompt_System.md)

Token format: <|category:tag|>

Placement rules (PROMPTING.md, Prompt System section 8):
    - Sentence-level tokens (emotion, style, prosody speed/pitch/delivery):
      placed at the START of the sentence.
    - Inline tokens (SFX, prosody pause/long_pause):
      inserted at the EXACT position where the effect occurs.
    - SFX gotcha: <|sfx:tag|>onomatopoeia  - no space between tag and word.

ONLY the PromptBuilder is allowed to import and use these definitions
(AI Development Rules section 9, Prompt System section 11). No other module
shall construct Higgs token strings.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple


# ---------------------------------------------------------------------------
# Token format helper
# ---------------------------------------------------------------------------
TOKEN_FORMAT = "<|{category}:{tag}|>"


def make_token(category: str, tag: str) -> str:
    """Construct a single Higgs control token.

    Only PromptBuilder should call this.
    """
    return TOKEN_FORMAT.format(category=category, tag=tag)


# ---------------------------------------------------------------------------
# Emotion tokens (21 emotions)
# ---------------------------------------------------------------------------
# Source: spec/Higgs_Audio_V3_Reference.md section 3, PROMPTING.md
@dataclass(frozen=True)
class EmotionDef:
    name: str        # human-readable label (UI display)
    tag: str         # token tag value (e.g. "elation")
    description: str # short description for tooltip

EMOTIONS: List[EmotionDef] = [
    EmotionDef("Elation",       "elation",       "Joyful excitement and delight. Use for happy announcements, celebrations, good news, winning moments."),
    EmotionDef("Amusement",     "amusement",     "Lighthearted, playful fun. Use for jokes, funny stories, entertaining content, children's material."),
    EmotionDef("Enthusiasm",    "enthusiasm",    "Energetic excitement and eagerness. Use for motivational speeches, product launches, sports commentary, calls to action."),
    EmotionDef("Determination", "determination", "Firm, resolute conviction. Use for goal-setting, challenges, military/sports commands, persuasive arguments."),
    EmotionDef("Pride",         "pride",         "Confident self-assurance and dignity. Use for achievements, accomplishments, testimonials, award speeches."),
    EmotionDef("Contentment",   "contentment",   "Calm satisfaction and peaceful ease. Use for relaxation guides, meditation, nature narration, slow-paced content."),
    EmotionDef("Affection",     "affection",     "Warm, tender, caring tone. Use for love declarations, family messages, romantic content, greetings to loved ones."),
    EmotionDef("Relief",        "relief",        "Release of tension, exhaling after stress. Use for 'finally done', resolved conflicts, 'glad that's over' moments."),
    EmotionDef("Contemplation", "contemplation", "Thoughtful, reflective, inward. Use for philosophy, poetry, memories, documentaries, meditation prompts."),
    EmotionDef("Confusion",     "confusion",     "Puzzled, uncertain, doubting. Use for unexpected situations, mysteries, 'I don't understand' dialogue, plot twists."),
    EmotionDef("Surprise",      "surprise",      "Sudden astonishment, unexpected discovery. Use for reveals, shocking news, gifts, 'wow' moments, plot twists."),
    EmotionDef("Awe",           "awe",           "Wonder and reverence before something grand. Use for nature scenes, epic moments, space/cosmos, majestic descriptions."),
    EmotionDef("Longing",       "longing",       "Yearning, wistful desire for something distant. Use for nostalgia, missing someone, distant memories, romantic longing."),
    EmotionDef("Arousal",       "arousal",       "Heightened emotional intensity and alertness. Use for intense scenes, suspense, climax moments, thrilling content."),
    EmotionDef("Anger",         "anger",         "Frustration, irritation, righteous fury. Use for arguments, complaints, confrontation, injustice, strong disagreement."),
    EmotionDef("Fear",          "fear",          "Anxiety, dread, apprehension. Use for horror stories, scary scenes, warnings, danger situations, suspense."),
    EmotionDef("Disgust",       "disgust",       "Strong aversion and revulsion. Use for unpleasant descriptions, rejection, 'that's gross' reactions, criticism."),
    EmotionDef("Bitterness",    "bitterness",    "Resentful, cynical, sour outlook. Use for betrayal, disappointment, cynical commentary, regret, grievances."),
    EmotionDef("Sadness",       "sadness",       "Sorrow, grief, melancholy. Use for loss, farewells, tragic stories, emotional scenes, condolences, empathy."),
    EmotionDef("Shame",         "shame",         "Embarrassment, guilt, humiliation. Use for confessions, mistakes, apologies, awkward moments, regretful admissions."),
    EmotionDef("Helplessness",  "helplessness",  "Powerless, trapped, unable to act. Use for desperate situations, being overwhelmed, giving up, tragic endings."),
]

EMOTION_NAMES: List[str] = [e.name for e in EMOTIONS]
EMOTION_TAGS: List[str] = [e.tag for e in EMOTIONS]
EMOTION_BY_NAME: Dict[str, EmotionDef] = {e.name: e for e in EMOTIONS}
EMOTION_BY_TAG: Dict[str, EmotionDef] = {e.tag: e for e in EMOTIONS}


# ---------------------------------------------------------------------------
# Style tokens (3 styles)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StyleDef:
    name: str
    tag: str

STYLES: List[StyleDef] = [
    StyleDef("Singing",    "singing"),
    StyleDef("Whispering", "whispering"),
    StyleDef("Shouting",   "shouting"),
]

STYLE_NAMES: List[str] = [s.name for s in STYLES]
STYLE_BY_NAME: Dict[str, StyleDef] = {s.name: s for s in STYLES}


# ---------------------------------------------------------------------------
# Prosody tokens
# ---------------------------------------------------------------------------
# Speed (sentence-level)
@dataclass(frozen=True)
class ProsodyDef:
    name: str
    tag: str
    effect: str
    category: str   # "speed" | "pitch" | "delivery" | "pause"

PROSODY_TOKENS: List[ProsodyDef] = [
    # Speed
    ProsodyDef("Very Slow",      "speed_very_slow",  "~0.65x speed",  "speed"),
    ProsodyDef("Slow",           "speed_slow",       "~0.85x speed",  "speed"),
    ProsodyDef("Normal",         "speed_normal",     "Normal speed",  "speed"),
    ProsodyDef("Fast",           "speed_fast",       "~1.2x speed",   "speed"),
    ProsodyDef("Very Fast",      "speed_very_fast",  "~1.4x speed",   "speed"),
    # Pitch
    ProsodyDef("Low",            "pitch_low",        "Lower pitch",   "pitch"),
    ProsodyDef("Normal",         "pitch_normal",     "Normal pitch",  "pitch"),
    ProsodyDef("High",           "pitch_high",       "Higher pitch",  "pitch"),
    # Delivery
    ProsodyDef("Expressive High","expressive_high",  "More expressive",  "delivery"),
    ProsodyDef("Normal",         "expressive_normal","Normal delivery",  "delivery"),
    ProsodyDef("Expressive Low", "expressive_low",   "Flatter delivery", "delivery"),
    # Pauses (inline)
    ProsodyDef("Pause",          "pause",            "Short pause (~400-700 ms)",   "pause"),
    ProsodyDef("Long Pause",     "long_pause",       "Long pause (~700-1500 ms, ~2x short)",    "pause"),
]

# Speed options (excluding "Normal" which is the default/no-op)
SPEED_OPTIONS: List[ProsodyDef] = [p for p in PROSODY_TOKENS if p.category == "speed"]
PITCH_OPTIONS: List[ProsodyDef] = [p for p in PROSODY_TOKENS if p.category == "pitch"]
DELIVERY_OPTIONS: List[ProsodyDef] = [p for p in PROSODY_TOKENS if p.category == "delivery"]
PAUSE_OPTIONS: List[ProsodyDef] = [p for p in PROSODY_TOKENS if p.category == "pause"]

PROSODY_BY_NAME: Dict[str, ProsodyDef] = {p.name: p for p in PROSODY_TOKENS}


# ---------------------------------------------------------------------------
# Sound effect tokens (9 SFX)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SfxDef:
    name: str
    tag: str
    onomatopoeia: str   # suggested sound word (no space between token and this)

SFX_TOKENS: List[SfxDef] = [
    SfxDef("Cough",     "cough",     "Ahem"),
    SfxDef("Laughter",  "laughter",  "Haha"),
    SfxDef("Crying",    "crying",    "Sob"),
    SfxDef("Screaming", "screaming", "Ahh"),
    SfxDef("Burping",   "burping",   "Burp"),
    SfxDef("Humming",   "humming",   "Hmm"),
    SfxDef("Sigh",      "sigh",      "Ahh"),
    SfxDef("Sniff",     "sniff",     "Sff"),
    SfxDef("Sneeze",    "sneeze",    "Achoo"),
]

SFX_BY_NAME: Dict[str, SfxDef] = {s.name: s for s in SFX_TOKENS}
SFX_BY_TAG: Dict[str, SfxDef] = {s.tag: s for s in SFX_TOKENS}


# ---------------------------------------------------------------------------
# Sentence-level vs inline classification
# ---------------------------------------------------------------------------
# Used by PromptBuilder to enforce placement rules.
SENTENCE_LEVEL_CATEGORIES = {"emotion", "style", "speed", "pitch", "delivery"}
INLINE_CATEGORIES = {"sfx", "pause"}


def is_sentence_level(category: str) -> bool:
    """Return True if tokens in this category go at the sentence start."""
    return category in SENTENCE_LEVEL_CATEGORIES


def is_inline(category: str) -> bool:
    """Return True if tokens in this category are inserted inline."""
    return category in INLINE_CATEGORIES
