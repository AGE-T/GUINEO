"""
SpeechStudio Engine - Combined Audio Assembly
==============================================

Assembles multiple Scene audio outputs into a single combined audio file.

This is the core of the "Scene Chain" feature: the user selects a set of
Scenes (in a user-defined order), and this module concatenates their
generated WAV files into one continuous audio file.

P3.23 REWRITE — float32 WAV support (CRITICAL fix):
    The Higgs Audio V3 generation pipeline writes **32-bit IEEE float WAV**
    files (format tag 3) via soundfile (`subtype='FLOAT'`). Python's built-in
    `wave` module CANNOT read or write IEEE-float WAVs
    (`wave.Error: unknown format: 3`). The previous implementation used the
    `wave` module and therefore failed on every REAL generated file — it only
    worked on PCM16 test fixtures. This rewrite loads audio via
    soundfile (primary, all subtypes incl. FLOAT) with a wave-module fallback
    for PCM, and writes the combined output as 32-bit float WAV (24 kHz mono),
    matching the generation pipeline format exactly.

Design decisions (locked — SPEECHSTUDIO CHARACTERS + SCENE ASSEMBLY
FULL DECISION RECORD §50–§59):
    - WAV is the primary lossless assembly format (24 kHz, mono, float32).
    - No silent skipping: if a selected Scene's audio file is missing,
      the assembly is BLOCKED with a clear error naming the Scene (§52/§53).
    - Disk/write failure: abort and remove any partial output (§53).
    - MP3 is an export conversion only (WAV first, then optional FFmpeg
      conversion; `subprocess.run(argv_list, shell=False)`, never shell=True).
    - Optional LUFS normalization applied to the combined result (§55).
    - Inter-scene silence is one global value for the whole assembly (§54).

Data flow:
    1. AssemblyDialog collects selected scenes + output settings
    2. MainWindow calls assemble_combined_audio()
    3. CombinedAudioAssembler.assemble() concatenates WAVs (float32-safe)
    4. Optionally normalize_to_lufs() on the combined result
    5. Optionally convert_to_mp3() if FFmpeg available + user requested
    6. CombinedOutput dict added to Project.combined_outputs (§45 lineage:
       scene_ids, audio_asset_ids, output_path, format, duration,
       silence_ms, normalized, created_at)
    7. History entry created by MainWindow (type="combined")

No new entity class — CombinedOutput is a plain dict stored in
Project.combined_outputs (per the locked architecture decision §45/§46).
"""

from __future__ import annotations
import os
import time
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from engine.logger import get_logger

logger = get_logger("combined_audio")

# ---------------------------------------------------------------------------
# WAV I/O — float32-safe (P3.23 critical fix)
# ---------------------------------------------------------------------------

HIGGS_SAMPLE_RATE = 24000


def _load_audio(path: str) -> Tuple[Any, int]:
    """Load a WAV file and return (float32 mono samples, sample_rate).

    Primary: soundfile — handles ALL WAV subtypes including 32-bit IEEE
    float (the REAL Higgs Audio V3 output format) and any PCM width.
    Fallback: Python `wave` module (PCM only) — used when soundfile is
    unavailable. Mirrors engine.audio_manager.AudioManager.load_wav.
    """
    import numpy as np

    try:
        import soundfile as sf
        data, sample_rate = sf.read(path, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        return np.ascontiguousarray(data, dtype=np.float32), int(sample_rate)
    except ImportError:
        pass

    # Fallback: wave module (PCM integer only)
    import wave as _wave
    with _wave.open(path, "rb") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if n_channels > 1 and sampwidth > 0:
        # De-interleave mono-average
        import array
        if sampwidth == 2:
            arr = array.array("h")
            arr.frombytes(frames)
            vals = np.asarray(arr, dtype=np.float32).reshape(-1, n_channels)
            data = vals.mean(axis=1) / 32768.0
            return data, int(sample_rate)
        raise IOError(
            "soundfile unavailable and WAV is not 16-bit PCM: {0}".format(path))
    if sampwidth == 2:
        import array
        arr = array.array("h")
        arr.frombytes(frames)
        return np.asarray(arr, dtype=np.float32) / 32768.0, int(sample_rate)
    raise IOError(
        "soundfile unavailable and WAV sample width unsupported "
        "(sampwidth={0}): {1}".format(sampwidth, path))


def _save_audio(path: str, samples: Any, sample_rate: int) -> None:
    """Write float32 mono samples to a 32-bit float WAV file.

    Primary: soundfile (writes IEEE float WAV directly — same subtype as
    the generation pipeline). Fallback: wave module + header patch to
    format code 3 (mirrors engine.audio_manager.AudioManager._write_wav).
    """
    import numpy as np
    arr = np.ascontiguousarray(np.asarray(samples, dtype=np.float32).flatten())

    try:
        import soundfile as sf
        sf.write(path, arr, sample_rate, subtype="FLOAT")
        return
    except ImportError:
        pass

    # Fallback: wave module writes PCM; patch the header to IEEE float.
    import wave as _wave
    import struct
    with _wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(4)
        wf.setframerate(sample_rate)
        wf.writeframes(arr.tobytes())
    # Patch RIFF format tag: 1 (PCM) → 3 (IEEE float).
    # Layout: "RIFF" u32le "WAVE" "fmt " u32le <fmt-chunk>
    #         fmt chunk: u16le format u16le channels u32le rate u32le
    #                    byterate u16le blockalign u16le sampwidth
    with open(path, "r+b") as f:
        f.seek(20)                       # start of fmt chunk body
        fmt = struct.unpack("<H", f.read(2))[0]
        if fmt == 1:
            f.seek(20)
            f.write(struct.pack("<H", 3))
            # byte rate = rate * channels * sampwidth
            f.seek(28)
            f.write(struct.pack("<I", sample_rate * 1 * 4))
            # block align = channels * sampwidth
            f.seek(32)
            f.write(struct.pack("<H", 4))


def _normalize_to_lufs(samples: Any, target_lufs: float = -18.0) -> Any:
    """Normalize samples toward a target loudness (RMS-based LUFS approx).

    Same algorithm as engine.audio_manager.AudioManager.normalize_to_lufs:
    a pure-RMS approximation of loudness normalization (sufficient for
    speech). If the RMS is -inf (digital silence), returns unchanged.
    """
    import numpy as np
    arr = np.asarray(samples, dtype=np.float32)
    if len(arr) == 0:
        return arr
    rms = float(np.sqrt(np.mean(arr ** 2)))
    if rms <= 0.0:
        return arr
    rms_db = 20.0 * np.log10(rms)
    gain_db = target_lufs - rms_db
    # Safety clamp: never amplify by more than +20 dB or attenuate by
    # more than -30 dB in a single normalization pass.
    gain_db = max(-30.0, min(20.0, gain_db))
    gain = float(10.0 ** (gain_db / 20.0))
    normalized = arr * gain
    # Soft-limit to prevent clipping after gain.
    peak = float(np.max(np.abs(normalized))) if len(normalized) else 0.0
    if peak > 1.0:
        normalized = normalized / peak * 0.999
    return normalized.astype(np.float32)


def _detect_duration(wav_path: str) -> float:
    """Read the duration of a WAV file in seconds (float32-safe)."""
    try:
        data, rate = _load_audio(wav_path)
        return len(data) / float(rate) if rate > 0 else 0.0
    except Exception as exc:
        logger.warning("Could not read duration of %s: %s", wav_path, exc)
        return 0.0


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AssemblySource:
    """One source audio file for assembly.

    Attributes:
        scene_id:       the Scene ID this audio belongs to.
        scene_name:     human-readable scene name (for logging/history).
        audio_path:     absolute path to the WAV file.
        duration:       audio duration in seconds (0.0 if unknown).
        audio_asset_id: the AudioAsset ID (lineage, design record §45).
    """
    scene_id: str
    scene_name: str
    audio_path: str
    duration: float = 0.0
    audio_asset_id: str = ""


@dataclass
class AssemblyResult:
    """Result of an assembly operation.

    Attributes:
        success:          True if the WAV was assembled.
        output_path:      path to the combined WAV file.
        mp3_path:         path to the MP3 file ("" if not created).
        mp3_created:      True if MP3 conversion succeeded.
        total_duration:   total duration in seconds.
        scene_count:      number of scenes included.
        source_scene_ids: list of scene IDs included (in order).
        audio_asset_ids:  list of source AudioAsset IDs (lineage, §45).
        normalized:       True if LUFS normalization was applied.
        silence_ms:       inter-scene silence actually used (ms).
        errors:           list of error/warning strings.
        blocked_reasons:  fatal problems that BLOCKED the assembly (§52).
    """
    success: bool = False
    output_path: str = ""
    mp3_path: str = ""
    mp3_created: bool = False
    total_duration: float = 0.0
    scene_count: int = 0
    source_scene_ids: List[str] = field(default_factory=list)
    audio_asset_ids: List[str] = field(default_factory=list)
    normalized: bool = False
    silence_ms: int = 0
    errors: List[str] = field(default_factory=list)
    blocked_reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# FFmpeg / MP3
# ---------------------------------------------------------------------------

def ffmpeg_available() -> bool:
    """Check if FFmpeg is available on the system PATH."""
    return shutil.which("ffmpeg") is not None


def convert_to_mp3(
    wav_path: str,
    mp3_path: str,
    bitrate: str = "192k",
    title: str = "",
    artist: str = "",
    album: str = "",
    comment: str = "",
) -> Tuple[bool, str]:
    """Convert a WAV file to MP3 using FFmpeg (libmp3lame CBR).

    Security: the command is an argv LIST executed without any shell —
    no shell string construction, no shell invocation (design record
    §57/§85).

    Args:
        wav_path:  source WAV file path.
        mp3_path:  destination MP3 file path.
        bitrate:   MP3 bitrate (e.g. "128k", "192k", "320k").
        title/artist/album/comment: minimal ID3 metadata (design record §60).

    Returns:
        (success, message): True if conversion succeeded, else False + error msg.
    """
    if not os.path.isfile(wav_path):
        return False, f"Source WAV not found: {wav_path}"
    if not ffmpeg_available():
        return False, "FFmpeg not found on system PATH. Install FFmpeg to enable MP3 export."
    try:
        cmd = [
            "ffmpeg", "-y",          # overwrite output
            "-i", wav_path,
            "-codec:a", "libmp3lame",
            "-b:a", bitrate,         # CBR by default (design record §57)
        ]
        # Minimal ID3 metadata (design record §60 — suggested values).
        if title:
            cmd += ["-metadata", "title={0}".format(title)]
        if artist:
            cmd += ["-metadata", "artist={0}".format(artist)]
        if album:
            cmd += ["-metadata", "album={0}".format(album)]
        if comment:
            cmd += ["-metadata", "comment={0}".format(comment)]
        cmd.append(mp3_path)
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=300,  # 5 min max
        )
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace")[-500:]
            return False, f"FFmpeg failed (code {result.returncode}): {err}"
        if not os.path.isfile(mp3_path):
            return False, "FFmpeg completed but MP3 file not created."
        logger.info("MP3 conversion OK: %s → %s", wav_path, mp3_path)
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "FFmpeg conversion timed out (>5 min)."
    except Exception as exc:
        return False, f"FFmpeg error: {exc}"


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def sanitize_output_filename(filename: str) -> str:
    """Sanitize a user-supplied output filename.

    Treats the filename as UNTRUSTED input (design record §74/§85):
    - Strips any directory components (blocks "../" path traversal and
      absolute-path injection).
    - Removes path separators and null bytes.
    - Keeps only filesystem-portable characters.
    Returns a safe bare filename (no directories). May return "" if the
    input contains no usable characters.
    """
    if not filename:
        return ""
    # Drop any directory components the user may have typed.
    name = os.path.basename(filename.replace("\\", "/"))
    # Remove path separators / nulls that survived (defence in depth).
    for ch in ("/", "\\", "\x00"):
        name = name.replace(ch, "_")
    name = name.strip()
    # Reject names that are only dots / empty.
    if not name or set(name) <= {".", " "}:
        return ""
    return name


def assemble_combined_audio(
    sources: List[AssemblySource],
    output_path: str,
    inter_scene_silence: float = 0.5,
    create_mp3: bool = False,
    mp3_bitrate: str = "192k",
    normalize: bool = True,
    normalize_target_lufs: float = -18.0,
    project_name: str = "",
) -> AssemblyResult:
    """Concatenate multiple WAV files into a single combined WAV.

    Float32-safe: loads via soundfile (handles the REAL Higgs output
    format — 24 kHz mono 32-bit IEEE float) and writes the same format.

    Design record §52/§53 — failure behaviour:
        - No silent skipping. If ANY selected source file is missing or
          unreadable, the assembly is BLOCKED: the result is unsuccessful,
          `blocked_reasons` names the offending Scene(s), and NO output
          file is written.
        - On write failure the partial output file is removed.

    Args:
        sources:             ordered list of AssemblySource (scene audio).
        output_path:         destination WAV path (will be created/overwritten).
        inter_scene_silence: seconds of silence between scenes (0.0 = none).
        create_mp3:          if True, also convert to MP3 (requires FFmpeg).
        mp3_bitrate:         MP3 bitrate if create_mp3 is True.
        normalize:           if True, normalize the combined result to the
                             target loudness (design §55: default ON).
        normalize_target_lufs: target loudness in LUFS (default -18).
        project_name:        used for MP3 ID3 metadata.

    Returns:
        AssemblyResult with success status + metadata.
    """
    import numpy as np

    result = AssemblyResult()

    # --- Validate sources: BLOCK on missing files (design §52) ---
    if not sources:
        result.blocked_reasons.append("No scenes selected for assembly.")
        result.errors.append("No scenes selected for assembly.")
        return result

    for src in sources:
        if not src.audio_path or not os.path.isfile(src.audio_path):
            reason = (
                "Scene '{0}': audio file not found ({1}). "
                "Generate this scene first, or deselect it.".format(
                    src.scene_name or src.scene_id,
                    src.audio_path or "(no path)")
            )
            result.blocked_reasons.append(reason)
            result.errors.append(reason)

    if result.blocked_reasons:
        # Blocked — no output written, no silent skip.
        logger.warning(
            "Assembly blocked: %d missing source(s).", len(result.blocked_reasons))
        return result

    # --- Ensure output directory exists ---
    out_dir = os.path.dirname(output_path)
    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            result.blocked_reasons.append(
                "Cannot create output folder: {0}".format(exc))
            result.errors.append("Cannot create output folder: {0}".format(exc))
            return result

    # --- Load and concatenate (float32-safe) ---
    try:
        import numpy as np

        loaded: List[Any] = []
        sample_rate = HIGGS_SAMPLE_RATE
        for i, src in enumerate(sources):
            try:
                data, sr = _load_audio(src.audio_path)
            except Exception as exc:
                # Unreadable file also BLOCKS (design §52: "Missing file:
                # block assembly").
                reason = ("Scene '{0}': audio file could not be read — {1}".format(
                    src.scene_name or src.scene_id, exc))
                result.blocked_reasons.append(reason)
                result.errors.append(reason)
                continue
            if i == 0:
                sample_rate = sr
            elif sr != sample_rate:
                result.errors.append(
                    "Scene '{0}': sample rate differs ({1} vs {2}) — "
                    "kept source rate (best effort).".format(
                        src.scene_name or src.scene_id, sr, sample_rate))
            if len(data) > 0:
                loaded.append(data)

        if result.blocked_reasons:
            # Remove any partial output if one was created earlier.
            _remove_quietly(output_path)
            return result

        if not loaded:
            result.blocked_reasons.append(
                "No readable audio in the selected scenes.")
            result.errors.append("No readable audio in the selected scenes.")
            return result

        silence_samples = int(inter_scene_silence * sample_rate)
        silence = np.zeros(silence_samples, dtype=np.float32) \
            if silence_samples > 0 else None

        pieces: List[Any] = []
        for i, data in enumerate(loaded):
            if i > 0 and silence is not None:
                pieces.append(silence)
            pieces.append(data)
        combined = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]

        # --- Optional normalization on the combined result (§55) ---
        if normalize and len(combined) > 0:
            combined = _normalize_to_lufs(combined, normalize_target_lufs)
            result.normalized = True

        total_frames = len(combined)
        result.total_duration = total_frames / float(sample_rate) \
            if sample_rate > 0 else 0.0

        # --- Write combined WAV (float32) ---
        try:
            _save_audio(output_path, combined, sample_rate)
        except Exception as exc:
            # Write failure → abort + remove partial output (§53).
            _remove_quietly(output_path)
            result.blocked_reasons.append(
                "Failed to write output file: {0}".format(exc))
            result.errors.append(
                "Failed to write output file: {0}".format(exc))
            logger.error("Combined audio write failed: %s", exc)
            return result

        result.output_path = output_path
        result.scene_count = len(sources)
        result.source_scene_ids = [s.scene_id for s in sources]
        result.audio_asset_ids = [s.audio_asset_id for s in sources
                                  if s.audio_asset_id]
        result.silence_ms = int(inter_scene_silence * 1000)
        result.success = True
        logger.info(
            "Combined audio assembled: %s (%d scenes, %.2fs, normalized=%s)",
            output_path, result.scene_count, result.total_duration,
            result.normalized)

    except Exception as exc:
        _remove_quietly(output_path)
        result.blocked_reasons.append("Assembly failed: {0}".format(exc))
        result.errors.append("Assembly failed: {0}".format(exc))
        logger.error("Combined audio assembly failed: %s", exc)
        return result

    # --- Optional MP3 conversion (WAV first, then convert — §56) ---
    if create_mp3 and result.success:
        mp3_path = os.path.splitext(output_path)[0] + ".mp3"
        ok, msg = convert_to_mp3(
            output_path, mp3_path, mp3_bitrate,
            title=project_name,
            artist="SpeechStudio",
            album=project_name,
            comment="generated with Higgs Audio V3",
        )
        if ok:
            result.mp3_path = mp3_path
            result.mp3_created = True
        else:
            # Design §58: missing FFmpeg → WAV still succeeds + warning.
            result.errors.append("MP3 conversion skipped: {0}".format(msg))
            logger.warning("MP3 conversion failed: %s", msg)

    return result


def _remove_quietly(path: str) -> None:
    """Remove a file if it exists, never raise (partial-output cleanup)."""
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as exc:
        logger.warning("Could not remove partial output %s: %s", path, exc)


def build_combined_output_entry(
    result: AssemblyResult,
    project_name: str,
    source_scenes: List[Dict[str, Any]],
    version: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a combined_outputs entry dict for Project.combined_outputs.

    Design record §45 — lineage fields:
        id, scene_ids, audio_asset_ids, output_path, format, duration,
        silence_ms, normalized, created_at.

    P3.28 (§18/Rec 14): optional monotonic ``version`` slot (max+1 over
    Project.combined_outputs — same rule as the filename allocator).

    `source_scene_ids` and `scene_count` are retained alongside `scene_ids`
    for backward compatibility with earlier P3.22 readers.
    """
    import uuid
    entry = {
        "id": str(uuid.uuid4())[:12],
        "output_path": result.output_path,
        "mp3_path": result.mp3_path if result.mp3_created else "",
        "format": "wav+mp3" if result.mp3_created else "wav",
        "duration": result.total_duration,
        "scene_count": result.scene_count,
        # Design record §45 lineage (authoritative names):
        "scene_ids": list(result.source_scene_ids),
        "audio_asset_ids": list(result.audio_asset_ids),
        "silence_ms": result.silence_ms,
        "normalized": result.normalized,
        # Backward-compatible alias (P3.22 readers):
        "source_scene_ids": list(result.source_scene_ids),
        "source_scenes": [
            {"scene_id": s.get("scene_id", ""),
             "scene_name": s.get("scene_name", ""),
             "audio_path": s.get("audio_path", ""),
             "audio_asset_id": s.get("audio_asset_id", ""),
             "resolved_kind": s.get("resolved_kind", ""),
             "resolved_id": s.get("resolved_id", ""),
             "duration": s.get("duration", 0.0)}
            for s in source_scenes
        ],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "project_name": project_name,
    }
    if version is not None:
        try:
            entry["version"] = int(version)
        except (TypeError, ValueError):
            pass
    return entry


def is_combined_output_stale(
    combined_entry: Dict[str, Any],
    project_scenes: List[Any],
    app_root: str = "",
) -> bool:
    """Check if a combined output is stale (source scenes were re-generated).

    A combined output is stale if any of its source scenes has audio_assets
    that are NEWER than the combined output's created_at timestamp
    (design record §48/§49/§67).

    P3.28 (defect D-4 fix): ``created_at`` is an ISO timestamp
    ("%Y-%m-%dT%H:%M:%S", written by build_combined_output_entry) while
    ``generated_at`` on assets is a COMPACT timestamp
    ("%Y%m%d_%H%M%S", written by GenerationManager). The previous
    lexicographic comparison made EVERY non-empty asset look newer than
    EVERY combined output. Both sides are now parsed to datetime before
    comparison (engine.audio_provenance.parse_any_timestamp).

    P3.28 (Rec 15 extension): the output is ALSO stale when a source
    Scene's RESOLVED output has changed identity — the resolved entity
    (combined entry id / asset id) no longer matches the lineage record.

    Args:
        combined_entry:  the dict from Project.combined_outputs.
        project_scenes:  list of Scene objects (the project's scenes).
        app_root:        application root (for file existence checks).

    Returns:
        True if stale (re-assembly recommended), False if up-to-date.
    """
    from engine.audio_provenance import parse_any_timestamp

    created_at = parse_any_timestamp(combined_entry.get("created_at", ""))
    if created_at is None:
        return True  # unknown creation time → assume stale

    source_ids = set(
        combined_entry.get("scene_ids") or
        combined_entry.get("source_scene_ids") or [])
    for scene in project_scenes:
        if scene.id not in source_ids:
            continue
        # Check if any audio asset is newer than the combined output
        # (D-4 fix: parse BOTH timestamp formats before comparing).
        for asset in (scene.audio_assets or []):
            asset_time = parse_any_timestamp(asset.get("generated_at", ""))
            if asset_time is not None and asset_time > created_at:
                return True
        # Rec 15 extension: identity-based staleness — if the entry
        # recorded WHICH asset/combined id represented each scene, the
        # output is stale once the scene's resolved output changes.
        recorded = (combined_entry.get("source_scenes") or [])
        if app_root and recorded:
            try:
                from engine.audio_provenance import resolve_scene_output
                resolution = resolve_scene_output(scene, app_root)
                if resolution.get("kind") and not resolution.get(
                        "requires_review"):
                    resolved_id = resolution.get("id", "")
                    for rec in recorded:
                        if (isinstance(rec, dict)
                                and rec.get("scene_id") == scene.id
                                and rec.get("resolved_id")):
                            if resolved_id and \
                                    resolved_id != rec.get("resolved_id"):
                                return True
                            break
            except Exception:  # pragma: no cover - defensive
                pass
    # Missing output file = stale (must be re-assembled).
    if app_root:
        rel = combined_entry.get("output_path", "")
        if rel:
            full = rel if os.path.isabs(rel) else os.path.join(app_root, rel)
            if not os.path.isfile(full):
                return True
    return False
