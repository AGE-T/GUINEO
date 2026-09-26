"""
SpeechStudio Engine - Output Guard (P3.27B)
============================================

Runtime safety detection for pathological generation outputs, plus the
Generate Long part-filename version allocator.

WHY THIS MODULE EXISTS (P3.27B defect):
    The Higgs Audio V3 model generates audio autoregressively at 25 fps
    (8 codebooks, 40 ms per frame; research/bosonai_higgs-tts-v3-4b_README.md
    "Audio tokens ... 8 codebooks at 25 fps", "Frame rate: 25 fps").
    Generation stops when the model emits its end-of-speech signal — OR,
    failing that, when the max_new_tokens budget is exhausted:

        4096 tokens / 25 fps = 163.84 s hard ceiling

    Observed defect (user report, reproduced in
    ss/audit_probes_p327b/probe_outlier_repro.py): a regenerated Part 09
    produced 163.56 s of audio (99.83 % of the 4096-token ceiling) — the
    speech content was intact (~30.48 s) and the remaining ~133 s was a
    numerically silent tail. The model finished the speech but failed to
    terminate, continuing to emit (silent) frames until the token budget
    ran out. Prompt, settings, voice and model arguments were IDENTICAL
    between the good (30.48 s) and bad (163.56 s) runs — the ONLY
    difference was the raw waveform returned by the model.

    The application pipeline behaved correctly (append_silence adds only
    0.5 s; normalisation is gain-only; save_wav writes a fresh file), so
    the fix is NOT trimming and NOT a max_tokens reduction (a legitimate
    120 s part needs 3000 tokens and must keep working). The fix is:

      1. DETECT the pathology conservatively (this module);
      2. FLAG it visibly in the Batch Generation dialog and History;
      3. VERSION Generate Long part filenames so a bad regeneration can
         never overwrite the previous good version (disk-guarded
         allocation; the single versioning mechanism also planned by the
         P3.27 design record, Rec 2/6 — not a second mechanism).

DESIGN RULES (P3.27B task §16/§21):
    - NEVER trim, clip, or discard audio. Detection only; the user decides
      (keep / regenerate / inspect).
    - No fixed hard duration limit ("anything above 30 s is wrong" is
      wrong): a legitimate long output has speech throughout and is never
      flagged. The runaway signature is a LONG TRAILING SILENCE, optionally
      cross-checked against the expected duration estimated from the text.
    - All thresholds are module constants so they can be tuned in one place.

This module is pure (numpy only, no Qt, no engine state) and fully
unit-testable.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Model constants (research/bosonai_higgs-tts-v3-4b_README.md)
# ---------------------------------------------------------------------------
HIGGS_FRAME_RATE = 25            # audio frames per second
HIGGS_SAMPLE_RATE = 24000        # mono output sample rate
HIGGS_SAMPLES_PER_FRAME = HIGGS_SAMPLE_RATE // HIGGS_FRAME_RATE   # 960

# ---------------------------------------------------------------------------
# Anomaly detector thresholds (conservative by design)
# ---------------------------------------------------------------------------
# R1 — runaway trailing silence: the tail must be BOTH long in absolute
# terms AND dominate the file. A legitimate part with 3 s of dramatic
# final pause, or a 150 s part of continuous speech, is never flagged.
ANOMALY_MIN_TAIL_SILENCE_S = 10.0
ANOMALY_TAIL_RATIO = 0.5         # tail >= 50 % of total duration

# R2 — expected-duration multiple: only when the caller supplied an
# expected duration (estimated from the part's text by the splitter).
# Requires BOTH a large multiple AND a large absolute margin so that a
# 12 s expected -> 40 s actual (slow dramatic reading) is NOT flagged,
# while 12 s expected -> 163 s actual IS.
ANOMALY_EXPECTED_MULTIPLE = 3.0
ANOMALY_EXPECTED_MARGIN_S = 20.0

# R3 — completely silent output (no audible speech at all).
ANOMALY_SILENT_MIN_TOTAL_S = 5.0

# Frame-level "speech present" threshold for the trailing-silence scan.
FRAME_RMS_THRESHOLD_DBFS = -50.0


# ---------------------------------------------------------------------------
# Waveform analysis
# ---------------------------------------------------------------------------
def analyze_waveform(samples, sample_rate: int) -> Dict[str, Any]:
    """Measure the speech region and the trailing silence of a waveform.

    Pure numeric analysis on the raw model output (P3.27B task §6/§7):
    40 ms frames (matching the codec frame rate), a frame counts as
    audible when its RMS exceeds FRAME_RMS_THRESHOLD_DBFS. Natural
    pauses, breathing and dramatic timing are far above this threshold
    and are never counted as silence.

    Returns dict with: total_s, speech_s, trailing_silence_s,
    tail_rms_dbfs, tail_samples, sample_rate.
    """
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32)
    total_s = len(arr) / float(sample_rate) if sample_rate > 0 else 0.0
    frame_len = max(1, int(round(
        HIGGS_SAMPLES_PER_FRAME * (sample_rate / float(HIGGS_SAMPLE_RATE)))))
    n_frames = len(arr) // frame_len
    if n_frames == 0:
        return {
            "total_s": round(total_s, 2), "speech_s": 0.0,
            "trailing_silence_s": round(total_s, 2),
            "tail_rms_dbfs": None, "tail_samples": int(len(arr)),
            "sample_rate": int(sample_rate),
        }
    frames = arr[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    threshold = 10.0 ** (FRAME_RMS_THRESHOLD_DBFS / 20.0)
    loud_mask = rms > threshold
    if np.any(loud_mask):
        last_loud_frame = int(np.max(np.nonzero(loud_mask)[0]))
        speech_s = (last_loud_frame + 1) * frame_len / float(sample_rate)
    else:
        speech_s = 0.0
    tail_start = int(round(speech_s * sample_rate))
    tail = arr[tail_start:] if tail_start < len(arr) else arr[:0]
    tail_rms = float(np.sqrt(np.mean(tail ** 2))) if len(tail) > 0 else 0.0
    tail_rms_dbfs = 20.0 * float(np.log10(tail_rms)) if tail_rms > 0 else None
    return {
        "total_s": round(total_s, 2),
        "speech_s": round(speech_s, 2),
        "trailing_silence_s": round(total_s - speech_s, 2),
        "tail_rms_dbfs": (round(tail_rms_dbfs, 1)
                          if tail_rms_dbfs is not None else None),
        "tail_samples": int(len(tail)),
        "sample_rate": int(sample_rate),
    }


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------
def detect_output_anomaly(analysis: Dict[str, Any],
                          expected_duration_s: Optional[float] = None,
                          max_new_tokens: Optional[int] = None
                          ) -> Optional[Dict[str, Any]]:
    """Decide whether a generation output is pathological.

    Conservative by construction (P3.27B §16/§21):
      R1 runaway trailing silence — tail >= 10 s AND tail >= 50 % of total
      R2 expected-duration multiple — total >= 3x expected AND
         total >= expected + 20 s (only when expected was supplied)
      R3 silent output — no audible speech at all in an output >= 5 s

    Returns None when the output looks normal, otherwise a structured
    dict: {detected: True, reasons: [str, ...], expected_duration_s,
    max_new_tokens, token_ceiling_s?, analysis: {...}}. Callers must
    NEVER discard the audio based on this — flag only.
    """
    reasons: List[str] = []
    total = float(analysis.get("total_s", 0.0) or 0.0)
    tail = float(analysis.get("trailing_silence_s", 0.0) or 0.0)
    speech = float(analysis.get("speech_s", 0.0) or 0.0)

    # R1 — the runaway signature (catches the observed 163.56 s defect:
    # 30.48 s speech + 133.08 s silent tail).
    if (tail >= ANOMALY_MIN_TAIL_SILENCE_S
            and total > 0 and tail >= ANOMALY_TAIL_RATIO * total):
        reasons.append(
            "long trailing silence: {0:.1f}s silent tail after {1:.1f}s "
            "of speech ({2:.0f}% of the file)".format(
                tail, speech, 100.0 * tail / total if total else 0.0))

    # R2 — duration far beyond the text-based estimate.
    if (expected_duration_s is not None and expected_duration_s > 0
            and total >= ANOMALY_EXPECTED_MULTIPLE * expected_duration_s
            and total >= expected_duration_s + ANOMALY_EXPECTED_MARGIN_S):
        reasons.append(
            "duration far exceeds expected: {0:.1f}s generated for an "
            "estimated {1:.1f}s of text".format(total, expected_duration_s))

    # R3 — nothing audible at all.
    if speech <= 0.0 and total >= ANOMALY_SILENT_MIN_TOTAL_S:
        reasons.append(
            "silent output: no audible speech detected in {0:.1f}s".format(
                total))

    if not reasons:
        return None

    out: Dict[str, Any] = {
        "detected": True,
        "reasons": reasons,
        "expected_duration_s": (round(float(expected_duration_s), 2)
                                if expected_duration_s is not None else None),
        "max_new_tokens": int(max_new_tokens) if max_new_tokens else None,
        "analysis": analysis,
    }
    if max_new_tokens:
        out["token_ceiling_s"] = round(
            max_new_tokens / float(HIGGS_FRAME_RATE), 2)
    return out


def anomaly_warning_text(anomaly: Optional[Dict[str, Any]]) -> str:
    """Human-readable one-line warning for result.warnings / UI tooltips."""
    if not anomaly:
        return ""
    parts = "; ".join(anomaly.get("reasons", [])) or "unusual output"
    return "Unusually long output detected: {0}. " \
           "The file was kept — you can keep, inspect, or regenerate it." \
        .format(parts)


# ---------------------------------------------------------------------------
# Generate Long part filename versioning (P3.27B §18; P3.27 design Rec 2)
# ---------------------------------------------------------------------------
def normalize_name_component(name: str) -> str:
    """Windows-safe filename component (same rules as the existing
    _normalize_filename in main_window._start_long_narration)."""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "").strip())
    safe = re.sub(r"_+", "_", safe)
    safe = safe.strip("_.")
    return safe or "untitled"


def build_part_filename(project: str, scene: str, part_index: int,
                        version: int, speaker: Optional[str] = None,
                        scene_id: Optional[str] = None) -> str:
    """Build a versioned Generate Long part filename.

    Convention (extends the existing Project/Scene/Part convention with a
    version slot — the SAME mechanism planned by the P3.27 design record,
    so no second versioning convention is introduced):

        {Proj}_{Scene}_Long_v01_Part_001[_{Speaker}][_{scene8}].wav
        e.g. Eden_S03_Long_v01_Part_009_Engineer_ab12cd34.wav
             Eden_S03_Long_v02_Part_009_Engineer_ab12cd34.wav  (regen)

    The version travels with the PART: a selective regeneration produces
    exactly one new version for that part while all other parts keep
    their existing files.
    """
    proj = normalize_name_component(project or "Project")
    scn = normalize_name_component(scene or "Scene")
    base = "{0}_{1}_Long_v{2:02d}_Part_{3:03d}".format(
        proj, scn, max(1, int(version)), max(1, int(part_index)))
    if speaker:
        base += "_{0}".format(normalize_name_component(speaker))
    if scene_id:
        base += "_{0}".format(str(scene_id)[:8])
    return base + ".wav"


def next_free_part_filename(outputs_dir: str, project: str, scene: str,
                            part_index: int,
                            speaker: Optional[str] = None,
                            scene_id: Optional[str] = None,
                            start_version: int = 1
                            ) -> Tuple[str, int]:
    """Allocate the next free versioned part filename (disk-guarded).

    Starts at ``start_version`` and increments while a file with that
    name already exists in ``outputs_dir``. Deterministic and monotonic:
    a failed generation (which never writes a file) consumes no version,
    and a successful regeneration can never overwrite the previous
    version's file.
    """
    version = max(1, int(start_version))
    while True:
        filename = build_part_filename(project, scene, part_index, version,
                                       speaker=speaker, scene_id=scene_id)
        if not os.path.exists(os.path.join(outputs_dir, filename)):
            return filename, version
        version += 1


def extract_version_hint(filename: str) -> Optional[int]:
    """Best-effort parse of the Long_vNN slot from an existing filename
    (used when regenerating a job whose filename already carries a
    version). Returns None when the name has no version slot."""
    m = re.search(r"_Long_v(\d+)_Part_", filename or "")
    return int(m.group(1)) if m else None
