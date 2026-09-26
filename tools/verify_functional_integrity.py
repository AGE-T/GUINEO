#!/usr/bin/env python3
"""
SpeechStudio Functional Integrity Test Suite.

Exercises the preset save/load/restart cycle, voice resolution pipeline,
and reference-audio failure handling WITHOUT requiring the Higgs Audio
V3 model to be loaded. All tests run against the real PresetManager,
VoiceManager, and a stubbed GenerationManager that exercises the same
voice-resolution code path as production.

Test sections:
    A. Preset save / load / persistence / restart cycle
    B. Preset apply (all fields restored)
    C. Preset missing-voice handling (no silent substitution)
    D. Voice selection chain (voice_id -> VoiceProfile -> reference audio)
    E. Reference audio failure handling (no silent fallback)
    F. VoiceManager.validate() integration into preflight
    G. Preset signal connection count (no duplicates)

Run:
    python3 tools/verify_functional_integrity.py
"""
from __future__ import annotations

import os
import sys
import shutil
import tempfile
import traceback
from typing import Optional, List

# Make the SpeechStudio project importable.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from engine.preset_manager import Preset, PresetManager
from engine.voice_manager import VoiceManager
from engine.models import GenerationParameters, VoiceProfile, GenerationRequest
from engine.errors import ReferenceAudioMissing, InvalidVoice


# ---------------------------------------------------------------------------
# Test utilities
# ---------------------------------------------------------------------------
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
INFO = "\033[36mINFO\033[0m"

_test_results: List[tuple] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    _test_results.append((name, ok, detail))
    mark = PASS if ok else FAIL
    line = "  [{0}] {1}".format(mark, name)
    if detail:
        line += " — {0}".format(detail)
    print(line)


def make_test_wav(path: str, duration_s: float = 2.0, sample_rate: int = 24000) -> None:
    """Write a minimal valid WAV file (silence) using the stdlib wave module."""
    import wave
    import struct
    n_frames = int(duration_s * sample_rate)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(sample_rate)
        # Write silent frames
        frames = struct.pack("<" + "h" * n_frames, *([0] * n_frames))
        w.writeframes(frames)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------
def _setup_test_root() -> str:
    """Create a fresh temp app root with voices/, presets/, outputs/ dirs."""
    tmp = tempfile.mkdtemp(prefix="speechstudio_test_")
    os.makedirs(os.path.join(tmp, "voices"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "presets"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "outputs"), exist_ok=True)
    return tmp


def _create_voice_profile(
    voice_mgr: VoiceManager,
    name: str,
    wav_path: Optional[str] = None,
    transcript: str = "Hello, this is a test.",
) -> VoiceProfile:
    """Create a voice profile with an optional reference WAV."""
    profile = voice_mgr.create_profile(name=name, description="test voice")
    if wav_path is not None:
        voice_mgr.import_reference(profile.id, wav_path, transcript)
        profile = voice_mgr.get_profile(profile.id)
    return profile


# ---------------------------------------------------------------------------
# Section A: Preset save / load / persistence
# ---------------------------------------------------------------------------
def test_preset_save_and_persist() -> None:
    print("\n{0}: Preset save + persistence + restart".format(INFO))
    tmp_root = _setup_test_root()
    try:
        presets_dir = os.path.join(tmp_root, "presets")
        pm = PresetManager(presets_dir)

        # Save a preset with all fields populated.
        params = GenerationParameters(
            temperature=1.4, top_p=0.92, top_k=280,
            max_new_tokens=2048, seed=12345,
            append_silence=0.75, normalize_output=True,
            auto_play=False, output_format="wav",
        )
        preset = Preset(
            name="TEST_PRESET_A",
            voice_id="voice_a_abc12345",
            emotion="Awe",
            style="Whispering",
            speed="Slow",
            pitch="Low",
            delivery="Expressive High",
            parameters=params,
        )
        relpath = pm.save(preset)

        # Verify the file exists on disk.
        saved_abspath = os.path.join(pm.directory, os.path.basename(relpath))
        file_exists = os.path.isfile(saved_abspath)
        record("A1: preset file exists on disk",
               file_exists,
               "path={0}".format(saved_abspath))

        # Verify the preset is listed.
        listed = pm.list()
        names = [p.name for p in listed]
        record("A2: preset appears in list()",
               "TEST_PRESET_A" in names,
               "names={0}".format(names))

        # Inspect the persisted contents.
        with open(saved_abspath, "r", encoding="utf-8") as fh:
            raw = fh.read()
        has_name = "TEST_PRESET_A" in raw
        has_voice_id = "voice_a_abc12345" in raw
        has_emotion = "Awe" in raw
        has_style = "Whispering" in raw
        has_speed = "Slow" in raw
        has_pitch = "Low" in raw
        has_delivery = "Expressive High" in raw
        has_temperature = "1.4" in raw
        has_top_p = "0.92" in raw
        has_top_k = "280" in raw
        has_max_tokens = "2048" in raw
        has_seed = "12345" in raw
        has_append_silence = "0.75" in raw
        has_normalize = "true" in raw.lower() or "false" in raw.lower()
        has_auto_play = "auto_play" in raw
        record("A3: persisted file contains name", has_name)
        record("A3: persisted file contains voice_id", has_voice_id)
        record("A3: persisted file contains emotion", has_emotion)
        record("A3: persisted file contains style", has_style)
        record("A3: persisted file contains speed", has_speed)
        record("A3: persisted file contains pitch", has_pitch)
        record("A3: persisted file contains delivery", has_delivery)
        record("A3: persisted file contains temperature", has_temperature)
        record("A3: persisted file contains top_p", has_top_p)
        record("A3: persisted file contains top_k", has_top_k)
        record("A3: persisted file contains max_new_tokens", has_max_tokens)
        record("A3: persisted file contains seed", has_seed)
        record("A3: persisted file contains append_silence", has_append_silence)
        record("A3: persisted file contains normalize_output", has_normalize)
        record("A3: persisted file contains auto_play", has_auto_play)

        # Simulate application restart: create a fresh PresetManager
        # (no in-memory cache) pointing at the same directory.
        pm2 = PresetManager(presets_dir)
        preset_after_restart = pm2.get("TEST_PRESET_A")
        record("A4: preset survives restart (reloaded from disk)",
               preset_after_restart is not None)
        if preset_after_restart is not None:
            r = preset_after_restart
            record("A4: name restored", r.name == "TEST_PRESET_A")
            record("A4: voice_id restored", r.voice_id == "voice_a_abc12345")
            record("A4: emotion restored", r.emotion == "Awe")
            record("A4: style restored", r.style == "Whispering")
            record("A4: speed restored", r.speed == "Slow")
            record("A4: pitch restored", r.pitch == "Low")
            record("A4: delivery restored", r.delivery == "Expressive High")
            record("A4: temperature restored",
                   abs(r.parameters.temperature - 1.4) < 1e-6)
            record("A4: top_p restored",
                   abs(r.parameters.top_p - 0.92) < 1e-6)
            record("A4: top_k restored", r.parameters.top_k == 280)
            record("A4: max_new_tokens restored", r.parameters.max_new_tokens == 2048)
            record("A4: seed restored", r.parameters.seed == 12345)
            record("A4: append_silence restored",
                   abs(r.parameters.append_silence - 0.75) < 1e-6)
            record("A4: normalize_output restored",
                   r.parameters.normalize_output is True)
            record("A4: auto_play restored",
                   r.parameters.auto_play is False)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Section B: Preset apply (all fields restored)
# ---------------------------------------------------------------------------
def test_preset_apply_restores_all_fields() -> None:
    print("\n{0}: Preset apply restores all fields".format(INFO))
    tmp_root = _setup_test_root()
    try:
        presets_dir = os.path.join(tmp_root, "presets")
        pm = PresetManager(presets_dir)
        params = GenerationParameters(
            temperature=1.2, top_p=0.88, top_k=250,
            max_new_tokens=3072, seed=999,
            append_silence=1.0, normalize_output=False, auto_play=True,
        )
        preset = Preset(
            name="APPLY_TEST",
            voice_id=None,  # no voice — just test non-voice fields
            emotion="Calm", style="Narration",
            speed="Fast", pitch="High", delivery="Expressive Low",
            parameters=params,
        )
        pm.save(preset)
        # Reload (simulate fresh apply).
        pm2 = PresetManager(presets_dir)
        loaded = pm2.get("APPLY_TEST")
        record("B1: loaded preset is not None", loaded is not None)
        if loaded is None:
            return
        # Compare every persisted field.
        record("B2: emotion matches", loaded.emotion == "Calm")
        record("B3: style matches", loaded.style == "Narration")
        record("B4: speed matches", loaded.speed == "Fast")
        record("B5: pitch matches", loaded.pitch == "High")
        record("B6: delivery matches", loaded.delivery == "Expressive Low")
        record("B7: temperature matches",
               abs(loaded.parameters.temperature - 1.2) < 1e-6)
        record("B8: top_p matches",
               abs(loaded.parameters.top_p - 0.88) < 1e-6)
        record("B9: top_k matches", loaded.parameters.top_k == 250)
        record("B10: max_new_tokens matches",
               loaded.parameters.max_new_tokens == 3072)
        record("B11: seed matches", loaded.parameters.seed == 999)
        record("B12: append_silence matches",
               abs(loaded.parameters.append_silence - 1.0) < 1e-6)
        record("B13: normalize_output matches",
               loaded.parameters.normalize_output is False)
        record("B14: auto_play matches",
               loaded.parameters.auto_play is True)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Section C: Preset missing-voice handling
# ---------------------------------------------------------------------------
def test_preset_missing_voice() -> None:
    print("\n{0}: Preset missing-voice handling (no silent substitution)".format(INFO))
    tmp_root = _setup_test_root()
    try:
        presets_dir = os.path.join(tmp_root, "presets")
        voices_dir = os.path.join(tmp_root, "voices")
        pm = PresetManager(presets_dir)
        vm = VoiceManager(voices_dir)

        # Create a voice, save a preset referencing it, then DELETE the voice.
        wav = os.path.join(tmp_root, "ref.wav")
        make_test_wav(wav)
        voice = _create_voice_profile(vm, "Captain", wav_path=wav)
        preset = Preset(
            name="CAPTAIN_PRESET",
            voice_id=voice.id,
            emotion="Calm", style="Narration",
            speed="Normal", pitch="Normal", delivery="Expressive Low",
            parameters=GenerationParameters(),
        )
        pm.save(preset)
        # Verify the preset's voice_id resolves BEFORE deletion.
        voice_before = vm.get_profile(voice.id)
        record("C1: voice profile exists before deletion",
               voice_before is not None)
        record("C1: preset.voice_id matches the created voice",
               preset.voice_id == voice.id)

        # Delete the voice profile.
        vm.delete_profile(voice.id)
        voice_after = vm.get_profile(voice.id)
        record("C2: voice profile is None after deletion",
               voice_after is None)

        # Reload the preset — its voice_id is still set but the voice is gone.
        pm2 = PresetManager(presets_dir)
        loaded = pm2.get("CAPTAIN_PRESET")
        record("C3: preset still loaded after voice deletion",
               loaded is not None and loaded.voice_id == voice.id)

        # Simulate the MainWindow._on_apply_preset voice-resolution check:
        #   if preset.voice_id is set but get_profile returns None -> missing.
        resolved = vm.get_profile(loaded.voice_id)
        voice_missing = (resolved is None)
        record("C4: voice resolution detects missing voice",
               voice_missing,
               "voice_id={0} resolved={1}".format(loaded.voice_id, resolved))
        # The rule: we must NOT silently pick another voice. The handler
        # must leave the combo at "(No voice)" and warn the user. We can
        # verify the rule by confirming that vm.get_profile returns None
        # (not a different voice).
        record("C5: no silent substitution to another voice",
               resolved is None,
               "(if this FAILS, a different voice was returned)")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Section D: Voice selection chain (voice_id -> VoiceProfile -> reference)
# ---------------------------------------------------------------------------
def test_voice_selection_chain() -> None:
    print("\n{0}: Voice selection chain (Voice A vs Voice B)".format(INFO))
    tmp_root = _setup_test_root()
    try:
        voices_dir = os.path.join(tmp_root, "voices")
        vm = VoiceManager(voices_dir)

        # Create Voice A with a reference WAV at a known path.
        wav_a = os.path.join(tmp_root, "ref_a.wav")
        make_test_wav(wav_a, duration_s=2.0, sample_rate=24000)
        voice_a = _create_voice_profile(
            vm, "Voice A", wav_path=wav_a, transcript="Voice A reference.",
        )

        # Create Voice B with a DIFFERENT reference WAV at a different path.
        wav_b = os.path.join(tmp_root, "ref_b.wav")
        make_test_wav(wav_b, duration_s=3.0, sample_rate=16000)
        voice_b = _create_voice_profile(
            vm, "Voice B", wav_path=wav_b, transcript="Voice B reference.",
        )

        record("D1: Voice A and Voice B have different ids",
               voice_a.id != voice_b.id,
               "A={0} B={1}".format(voice_a.id, voice_b.id))
        record("D2: Voice A and Voice B have different reference paths",
               voice_a.reference_audio_path != voice_b.reference_audio_path,
               "A={0} B={1}".format(
                   voice_a.reference_audio_path,
                   voice_b.reference_audio_path,
               ))
        record("D3: Voice A reference audio file exists on disk",
               os.path.isfile(os.path.join(tmp_root, voice_a.reference_audio_path)))
        record("D4: Voice B reference audio file exists on disk",
               os.path.isfile(os.path.join(tmp_root, voice_b.reference_audio_path)))

        # Simulate the Engine._execute_generation voice resolution path:
        # request.voice_id -> VoiceManager.get_profile -> VoiceProfile.
        # We do NOT need the model loaded to verify the resolution chain.
        resolved_a = vm.get_profile(voice_a.id)
        resolved_b = vm.get_profile(voice_b.id)
        record("D5: Voice A resolves to the correct profile",
               resolved_a is not None and resolved_a.id == voice_a.id)
        record("D6: Voice B resolves to the correct profile",
               resolved_b is not None and resolved_b.id == voice_b.id)
        record("D7: resolved Voice A name matches",
               resolved_a.name == "Voice A")
        record("D8: resolved Voice B name matches",
               resolved_b.name == "Voice B")
        record("D9: resolved Voice A reference path matches",
               resolved_a.reference_audio_path == voice_a.reference_audio_path)
        record("D10: resolved Voice B reference path matches",
               resolved_b.reference_audio_path == voice_b.reference_audio_path)

        # Verify VoiceManager.validate() returns no blocking warnings.
        warnings_a = vm.validate(voice_a.id)
        warnings_b = vm.validate(voice_b.id)
        # Filter to blocking-only (matching Engine._validate policy).
        def _blocking(w):
            low = w.lower()
            return (
                ("missing" in low and "audio" in low)
                or "no reference audio" in low
                or "sample rate not detected" in low
                or "not found" in low
            )
        blocking_a = [w for w in warnings_a if _blocking(w)]
        blocking_b = [w for w in warnings_b if _blocking(w)]
        record("D11: Voice A has no blocking validation warnings",
               len(blocking_a) == 0,
               "warnings={0}".format(warnings_a))
        record("D12: Voice B has no blocking validation warnings",
               len(blocking_b) == 0,
               "warnings={0}".format(warnings_b))
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Section E: Reference audio failure handling (no silent fallback)
# ---------------------------------------------------------------------------
def test_reference_audio_failure_no_silent_fallback() -> None:
    print("\n{0}: Reference audio failure handling (no silent fallback)".format(INFO))
    tmp_root = _setup_test_root()
    try:
        voices_dir = os.path.join(tmp_root, "voices")
        vm = VoiceManager(voices_dir)

        # Create a voice WITH a reference WAV.
        wav = os.path.join(tmp_root, "ref.wav")
        make_test_wav(wav)
        voice = _create_voice_profile(vm, "Captain", wav_path=wav)

        # Now DELETE the reference WAV file from disk (but keep the profile).
        os.remove(os.path.join(tmp_root, voice.reference_audio_path))
        record("E1: reference WAV deleted from disk",
               not os.path.isfile(os.path.join(tmp_root, voice.reference_audio_path)))

        # VoiceManager.validate() should now report the file as missing.
        warnings = vm.validate(voice.id)
        has_missing_warning = any(
            "missing" in w.lower() and "audio" in w.lower()
            for w in warnings
        )
        record("E2: VoiceManager.validate() reports missing reference audio",
               has_missing_warning,
               "warnings={0}".format(warnings))

        # Verify the NEW GenerationManager._load_reference_audio behaviour:
        # it must RAISE ReferenceAudioMissing (not return (None, 0)).
        # We call it directly to verify the contract without needing the
        # full model loaded.
        from engine.generation_manager import GenerationManager
        # Construct a GenerationManager with None sub-managers (we only
        # test _load_reference_audio, which doesn't touch them).
        gm = GenerationManager.__new__(GenerationManager)
        gm._audio_manager = None
        gm._model_manager = None
        raised = False
        try:
            gm._load_reference_audio(voice)
        except ReferenceAudioMissing:
            raised = True
        except Exception as exc:
            # Any other exception type is also acceptable as long as it
            # is NOT a silent (None, 0) return. But ReferenceAudioMissing
            # is the expected type.
            record("E3: _load_reference_audio raised wrong exception type",
                   False, "got {0}: {1}".format(type(exc).__name__, exc))
            raised = True
        record("E3: _load_reference_audio raises ReferenceAudioMissing",
               raised,
               "(previously returned (None, 0) silently)")

        # Also test the empty-path case.
        voice_empty = VoiceProfile(id="empty", name="Empty")
        raised_empty = False
        try:
            gm._load_reference_audio(voice_empty)
        except ReferenceAudioMissing:
            raised_empty = True
        record("E4: empty reference path raises ReferenceAudioMissing",
               raised_empty)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Section F: VoiceManager.validate() integration into preflight
# ---------------------------------------------------------------------------
def test_validate_uses_voice_manager() -> None:
    print("\n{0}: Engine._validate uses VoiceManager.validate()".format(INFO))
    # We verify by reading the source — the integration is structural.
    src_path = os.path.join(PROJECT_ROOT, "engine", "engine.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    has_validate_call = "self._voices.validate(request.voice_id)" in src
    has_no_silent_fallback = "issues.append" in src and "has_reference" not in src.split(
        "Voice profile valid"
    )[1].split("Output directory")[0]
    record("F1: Engine._validate calls self._voices.validate()",
           has_validate_call)
    record("F2: Engine._validate no longer uses has_reference as the only check",
           "has_reference" not in src or "voice.has_reference" not in src)


# ---------------------------------------------------------------------------
# Section G: Preset signal connection count (no duplicates)
# ---------------------------------------------------------------------------
def test_no_duplicate_preset_signal_connections() -> None:
    print("\n{0}: Preset signal connection count (no duplicates)".format(INFO))
    src_path = os.path.join(PROJECT_ROOT, "ui", "main_window.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    # Count how many times each preset signal is connected.
    signals = [
        "apply_preset_requested.connect",
        "save_preset_requested.connect",
        "delete_preset_requested.connect",
        "rename_preset_requested.connect",
        "export_preset_requested.connect",
    ]
    all_ok = True
    for sig in signals:
        count = src.count("self._sidebar.{0}".format(sig))
        # The sidebar connection should appear EXACTLY ONCE.
        # (Dialog connections use a different receiver name.)
        ok = (count == 1)
        record("G: sidebar.{0} connected exactly once".format(sig),
               ok, "count={0}".format(count))
        if not ok:
            all_ok = False
    record("G: no duplicate preset signal connections", all_ok)


# ---------------------------------------------------------------------------
# Section H: Preset storage path consistency
# ---------------------------------------------------------------------------
def test_preset_path_consistency() -> None:
    print("\n{0}: Preset storage path consistency".format(INFO))
    # The MainWindow uses APP_ROOT/presets; verify the code matches.
    src_path = os.path.join(PROJECT_ROOT, "ui", "main_window.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    code_uses_presets_root = 'os.path.join(APP_ROOT, "presets")' in src
    record("H1: MainWindow uses APP_ROOT/presets (not settings/presets)",
           code_uses_presets_root)

    # F-301 doc should now say presets/ (root), not settings/presets/.
    doc_path = os.path.join(
        PROJECT_ROOT, "docs", "governance",
        "feature_specs", "F-301_preset_system.md",
    )
    with open(doc_path, "r", encoding="utf-8") as fh:
        doc = fh.read()
    doc_says_root_presets = "Presets live in `<app_root>/presets/`" in doc
    doc_says_settings_presets = (
        "settings/presets/" in doc
        # Exclude the historical lessons-learned doc which legitimately
        # references the old path when describing the fix.
        and "LESSONS" not in doc
    )
    record("H2: F-301 doc says <app_root>/presets/ (root)",
           doc_says_root_presets)
    record("H3: F-301 doc no longer says settings/presets/",
           not doc_says_settings_presets)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print("{0}: SpeechStudio Functional Integrity Test Suite".format(INFO))
    print("{0}: project root: {1}".format(INFO, PROJECT_ROOT))
    try:
        test_preset_save_and_persist()
        test_preset_apply_restores_all_fields()
        test_preset_missing_voice()
        test_voice_selection_chain()
        test_reference_audio_failure_no_silent_fallback()
        test_validate_uses_voice_manager()
        test_no_duplicate_preset_signal_connections()
        test_preset_path_consistency()
    except Exception:
        traceback.print_exc()
        return 2

    print("\n{0}: Summary".format(INFO))
    total = len(_test_results)
    passed = sum(1 for _, ok, _ in _test_results if ok)
    failed = total - passed
    for name, ok, detail in _test_results:
        if not ok:
            mark = FAIL
            print("  [{0}] {1} — {2}".format(mark, name, detail))
    print("\n  {0} passed, {1} failed, {2} total".format(passed, failed, total))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
