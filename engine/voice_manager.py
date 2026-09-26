"""
SpeechStudio Engine - Voice Manager
====================================

Engine Specification, section 7:
    "VoiceManager is responsible for: Loading voice profiles, Loading
     reference audio, Loading reference transcript, Voice validation,
     Voice import, Voice export, Voice metadata.
     VoiceManager never performs speech generation."

AI Development Rules, section 10:
    "VoiceManager owns: Reference audio, Reference transcript, Voice metadata.
     No other module shall modify voice files directly."

System Specification, section 11:
    "Voice profiles reside inside voices/.
     Each profile contains: Reference audio, Reference transcript, Metadata,
     Optional preview image.
     Each profile exists independently."

Single responsibility: manage voice profile files and reference audio.
Never loads the model, never generates speech, never builds prompts.
"""

from __future__ import annotations
import os
import json
import re
import uuid
import wave
from typing import Dict, List, Optional

from engine.logger import get_logger
from engine.errors import InvalidVoice, ReferenceAudioMissing
from engine.models import VoiceProfile

logger = get_logger("voice_manager")

# P3.43: profile ids are also filesystem directory names. This pattern is
# the single authority for what a SAFE profile id is (matches what
# _make_id can produce). Anything else (path separators, '..', absolute
# paths, unicode tricks) is rejected before it is ever joined into a
# filesystem path — imported/stored ids are untrusted input.
_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def is_safe_profile_id(profile_id: str) -> bool:
    """True if profile_id is a filename-safe id (no path components)."""
    return bool(profile_id) and _PROFILE_ID_RE.match(profile_id) is not None


class VoiceManager:
    """Manages voice profiles and their reference audio.

    Each voice profile is stored as a directory under voices/ containing:
      - profile.json   (metadata)
      - reference.wav  (reference audio, optional during creation)
      - transcript.txt (reference transcript, optional)
    """

    def __init__(self, voices_dir: str):
        self._voices_dir = voices_dir
        os.makedirs(voices_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Profile CRUD
    # ------------------------------------------------------------------
    def list_profiles(self) -> List[VoiceProfile]:
        """Return all voice profiles found in the voices directory."""
        profiles: List[VoiceProfile] = []
        if not os.path.isdir(self._voices_dir):
            return profiles
        for name in sorted(os.listdir(self._voices_dir)):
            profile_dir = os.path.join(self._voices_dir, name)
            if not os.path.isdir(profile_dir):
                continue
            profile = self._load_profile(name)
            if profile is not None:
                profiles.append(profile)
        return profiles

    def get_profile(self, profile_id: str) -> Optional[VoiceProfile]:
        """Load a single profile by id, or None if not found."""
        # P3.43: ids arriving from stored project/scene files are untrusted
        # — refuse anything that could escape the voices/ directory before
        # it reaches a path join. (get_profile keeps its "unknown id →
        # None" contract for well-formed ids.)
        if not is_safe_profile_id(profile_id):
            raise InvalidVoice(
                "Unsafe voice profile id: {0!r}".format(profile_id))
        return self._load_profile(profile_id)

    def create_profile(self, name: str, description: str = "",
                       tags: Optional[List[str]] = None) -> VoiceProfile:
        """Create a new empty voice profile and return it.

        The profile has no reference audio until import_reference is called.
        """
        profile_id = self._make_id(name)
        profile_dir = os.path.join(self._voices_dir, profile_id)
        os.makedirs(profile_dir, exist_ok=True)

        profile = VoiceProfile(
            id=profile_id,
            name=name,
            description=description,
            tags=list(tags) if tags else [],
        )
        self._save_profile(profile)
        logger.info("Voice profile created: %s (%s)", name, profile_id)
        return profile

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a voice profile and all its files."""
        if not is_safe_profile_id(profile_id):
            raise InvalidVoice(
                "Unsafe voice profile id: {0!r}".format(profile_id))
        profile_dir = os.path.join(self._voices_dir, profile_id)
        if not os.path.isdir(profile_dir):
            return False
        try:
            import shutil
            shutil.rmtree(profile_dir)
            logger.info("Voice profile deleted: %s", profile_id)
            return True
        except OSError as exc:
            logger.error("Failed to delete profile %s: %s",
                         profile_id, exc)
            return False

    def rename_profile(self, profile_id: str, new_name: str) -> VoiceProfile:
        """Rename a voice profile (keeps the same id and reference audio)."""
        profile = self.get_profile(profile_id)
        if profile is None:
            raise InvalidVoice("Voice profile not found: {0}".format(profile_id))
        new_name = str(new_name).strip()
        if not new_name:
            raise InvalidVoice("Voice profile name cannot be empty.")
        profile.name = new_name
        self._save_profile(profile)
        logger.info("Voice profile renamed: %s -> %s", profile_id, new_name)
        return profile

    # ------------------------------------------------------------------
    # Reference audio
    # ------------------------------------------------------------------
    def import_reference(self, profile_id: str, source_wav_path: str,
                         transcript: str = "") -> VoiceProfile:
        """Import (or replace) the reference WAV of a voice profile.

        The source WAV is VALIDATED BEFORE the profile is touched (P3.43
        §9: validation before destructive replacement). The existing
        reference.wav is preserved until the new copy has been verified:
        the new data lands at reference.wav.incoming, its metadata is
        read there, and only then is it swapped in atomically. If the
        swap fails the previous reference.wav is restored.

        An empty ``transcript`` leaves the existing transcript untouched
        (so a pure reference replacement never destroys the transcript).
        """
        profile = self.get_profile(profile_id)
        if profile is None:
            raise InvalidVoice("Voice profile not found: {0}".format(profile_id))

        if not os.path.isfile(source_wav_path):
            raise ReferenceAudioMissing(
                "Reference WAV file not found: {0}".format(source_wav_path),
            )

        profile_dir = os.path.join(self._voices_dir, profile_id)
        dest_wav = os.path.join(profile_dir, "reference.wav")
        incoming = dest_wav + ".incoming"
        backup = None
        try:
            # 1. Copy to a staging name — the current reference stays intact.
            import shutil
            shutil.copy2(source_wav_path, incoming)
            # 2. Validate the STAGED copy before replacing anything.
            sample_rate, channels, duration = self._read_wav_metadata(incoming)
            if sample_rate <= 0 or channels <= 0:
                raise InvalidVoice(
                    "The selected file is not a readable WAV audio file.")
            # 3. Swap in atomically (os.replace on the same directory).
            if os.path.isfile(dest_wav):
                backup = dest_wav + ".bak"
                os.replace(dest_wav, backup)
            os.replace(incoming, dest_wav)
        except InvalidVoice:
            self._remove_quiet(incoming)
            raise
        except OSError as exc:
            # Roll back to the previous reference if the swap failed.
            self._remove_quiet(incoming)
            if backup is not None and os.path.isfile(backup) \
                    and not os.path.isfile(dest_wav):
                os.replace(backup, dest_wav)
            raise InvalidVoice(
                "Failed to copy reference audio: {0}".format(exc)) from exc
        finally:
            if backup is not None:
                self._remove_quiet(backup)

        profile.reference_audio_path = os.path.relpath(dest_wav, self._get_app_root())
        profile.sample_rate = sample_rate
        profile.channels = channels
        profile.duration = duration

        if transcript:
            profile.reference_transcript = transcript
            self._save_transcript(profile_id, transcript)

        self._save_profile(profile)
        logger.info("Reference audio imported for %s: %d Hz, %d ch, %.1f s",
                    profile_id, sample_rate, channels, duration)
        return profile

    @staticmethod
    def _remove_quiet(path: str) -> None:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            logger.warning("Could not remove staging file: %s", path)

    def set_transcript(self, profile_id: str, transcript: str) -> VoiceProfile:
        """Set or update the reference transcript for a profile."""
        profile = self.get_profile(profile_id)
        if profile is None:
            raise InvalidVoice("Voice profile not found: {0}".format(profile_id))
        profile.reference_transcript = transcript
        self._save_transcript(profile_id, transcript)
        self._save_profile(profile)
        logger.info("Transcript updated for %s", profile_id)
        return profile

    # ------------------------------------------------------------------
    # P3.23 + P3.43: Avatar + speaker metadata + profile details
    # ------------------------------------------------------------------
    # P3.43 AVATAR PATH CONVENTION (single authority):
    #   preview_image is stored RELATIVE TO THE APPLICATION ROOT, exactly
    #   like reference_audio_path ("voices/<id>/avatar.png"). The previous
    #   storage wrote a bare "avatar.png" (profile-dir-relative) which no
    #   consumer could resolve — _load_profile now migrates those legacy
    #   values deterministically on load, so existing profiles keep
    #   working and get re-persisted in the unified convention.
    AVATAR_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

    def import_avatar(self, profile_id: str, source_image_path: str) -> VoiceProfile:
        """Copy an avatar/preview image into the profile and record it.

        Previously the UI called Engine.import_voice_avatar() which did
        not exist — the import failed with AttributeError whenever an
        avatar was selected. The image is copied into the profile
        directory and referenced by the app-root-relative path stored in
        VoiceProfile.preview_image (P3.43 convention — see the note
        above).

        Only image extensions are accepted (.png/.jpg/.jpeg/.webp/.gif —
        the exact set the reference WAV/image filters and the importer
        agree on, P3.43 §24); the copy never overwrites the reference
        audio. The file is copied to a staging name and validated
        (non-empty) before it replaces the current avatar (P3.43 §9).
        """
        profile = self.get_profile(profile_id)
        if profile is None:
            raise InvalidVoice("Voice profile not found: {0}".format(profile_id))
        if not os.path.isfile(source_image_path):
            raise InvalidVoice("Avatar image not found: {0}".format(
                source_image_path))
        ext = os.path.splitext(source_image_path)[1].lower()
        if ext not in self.AVATAR_EXTENSIONS:
            raise InvalidVoice(
                "Unsupported avatar image type: {0}".format(ext))
        import shutil
        profile_dir = os.path.join(self._voices_dir, profile_id)
        os.makedirs(profile_dir, exist_ok=True)
        # Remove a previous avatar with a DIFFERENT extension so only one
        # avatar file ever lives in the profile directory.
        old_rel = profile.preview_image or ""
        if old_rel:
            old_name = os.path.basename(old_rel)
            if old_name and old_name != "avatar{0}".format(ext):
                self._remove_quiet(os.path.join(profile_dir, old_name))
        dest = os.path.join(profile_dir, "avatar{0}".format(ext))
        incoming = dest + ".incoming"
        try:
            shutil.copyfile(source_image_path, incoming)
            if os.path.getsize(incoming) <= 0:
                raise InvalidVoice("The avatar image file is empty.")
            os.replace(incoming, dest)
        except OSError as exc:
            self._remove_quiet(incoming)
            raise InvalidVoice(
                "Failed to copy avatar image: {0}".format(exc)) from exc
        profile.preview_image = os.path.relpath(dest, self._get_app_root())
        self._save_profile(profile)
        logger.info("Avatar imported for %s -> %s", profile_id, dest)
        return profile

    def remove_avatar(self, profile_id: str) -> VoiceProfile:
        """Remove the avatar image from a profile (P3.43 §8)."""
        profile = self.get_profile(profile_id)
        if profile is None:
            raise InvalidVoice("Voice profile not found: {0}".format(profile_id))
        old_rel = profile.preview_image or ""
        if old_rel:
            # Only ever delete a file INSIDE this profile's own directory.
            name = os.path.basename(old_rel)
            if name.startswith("avatar"):
                self._remove_quiet(
                    os.path.join(self._voices_dir, profile_id, name))
        profile.preview_image = ""
        self._save_profile(profile)
        logger.info("Avatar removed for %s", profile_id)
        return profile

    def set_speaker_metadata(self, profile_id: str, gender: Optional[str] = None,
                             age_range: Optional[str] = None,
                             mood: Optional[str] = None) -> VoiceProfile:
        """Set the speaker metadata (gender / age range / mood) on a profile.

        P3.43 §7 semantics — each argument is applied EXPLICITLY:
          * None  → leave the current value unchanged (argument omitted)
          * ""    → CLEAR the value (user explicitly emptied the field)
          * text  → set the value
        The previous implementation silently ignored empty strings, which
        made it impossible to clear a metadata field after it was set.
        """
        profile = self.get_profile(profile_id)
        if profile is None:
            raise InvalidVoice("Voice profile not found: {0}".format(profile_id))
        if gender is not None:
            profile.gender = str(gender)
        if age_range is not None:
            profile.age_range = str(age_range)
        if mood is not None:
            profile.mood = str(mood)
        self._save_profile(profile)
        logger.info("Speaker metadata updated for %s (gender=%r, age=%r, mood=%r)",
                     profile_id, profile.gender, profile.age_range, profile.mood)
        return profile

    def update_details(self, profile_id: str,
                       description: Optional[str] = None,
                       tags: Optional[List[str]] = None) -> VoiceProfile:
        """Update profile description / tags (P3.43 single editor fields).

        Same None-unchanged semantics as set_speaker_metadata: an empty
        string clears the description, an empty list clears the tags.
        """
        profile = self.get_profile(profile_id)
        if profile is None:
            raise InvalidVoice("Voice profile not found: {0}".format(profile_id))
        if description is not None:
            profile.description = str(description)
        if tags is not None:
            profile.tags = [str(t).strip() for t in tags if str(t).strip()]
        self._save_profile(profile)
        return profile

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_profile(self, profile_id: str, export_dir: str) -> str:
        """Copy a voice profile to an external directory.

        Returns the path to the exported profile directory.
        """
        profile = self.get_profile(profile_id)
        if profile is None:
            raise InvalidVoice("Voice profile not found: {0}".format(profile_id))

        profile_src = os.path.join(self._voices_dir, profile_id)
        export_path = os.path.join(export_dir, profile_id)
        os.makedirs(export_path, exist_ok=True)

        try:
            import shutil
            for fname in os.listdir(profile_src):
                src = os.path.join(profile_src, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(export_path, fname))
            logger.info("Voice profile exported: %s -> %s", profile_id, export_path)
            return export_path
        except OSError as exc:
            raise InvalidVoice(
                "Failed to export voice profile: {0}".format(exc),
            ) from exc

    # ------------------------------------------------------------------
    # P3.43: transactional full import + profile directory re-import
    # ------------------------------------------------------------------
    def import_full(self, name: str, wav_path: str = "",
                    transcript: str = "", avatar_path: str = "",
                    gender: Optional[str] = None,
                    age_range: Optional[str] = None,
                    mood: Optional[str] = None,
                    description: str = "",
                    tags: Optional[List[str]] = None) -> VoiceProfile:
        """Perform a COMPLETE voice profile import as ONE transaction.

        This is the single authoritative import operation (P3.43 §9/
        §25): create + reference audio + transcript + avatar + speaker
        metadata + details, with full validation BEFORE anything is
        written and a complete rollback (the freshly created profile
        directory is removed) if ANY step fails. On failure no partial
        profile, no orphaned files, no profile in the list remain.

        ``wav_path`` may be empty (CREATE mode: the profile exists without
        reference audio; it can be added later via import_reference).
        """
        name = str(name).strip()
        if not name:
            raise InvalidVoice("Voice profile name cannot be empty.")
        # --- Pre-flight validation (nothing created yet) ---
        if wav_path:
            if not os.path.isfile(wav_path):
                raise ReferenceAudioMissing(
                    "Reference WAV file not found: {0}".format(wav_path))
            sr, ch, _dur = self._read_wav_metadata(wav_path)
            if sr <= 0 or ch <= 0:
                raise InvalidVoice(
                    "The selected file is not a readable WAV audio file.")
        if avatar_path:
            if not os.path.isfile(avatar_path):
                raise InvalidVoice(
                    "Avatar image not found: {0}".format(avatar_path))
            ext = os.path.splitext(avatar_path)[1].lower()
            if ext not in self.AVATAR_EXTENSIONS:
                raise InvalidVoice(
                    "Unsupported avatar image type: {0}".format(ext))

        # --- Create + populate, rolling back on any failure ---
        profile = self.create_profile(name, description, tags)
        try:
            if wav_path:
                profile = self.import_reference(
                    profile.id, wav_path, transcript)
            elif transcript:
                profile = self.set_transcript(profile.id, transcript)
            if avatar_path:
                profile = self.import_avatar(profile.id, avatar_path)
            if gender is not None or age_range is not None or mood is not None:
                profile = self.set_speaker_metadata(
                    profile.id, gender, age_range, mood)
            return profile
        except Exception as exc:
            # Roll back the whole transaction — no partial profile, no
            # orphaned directory, no misleading success state.
            logger.error("Transactional voice import failed for %r — "
                         "rolling back: %s", name, exc)
            self.delete_profile(profile.id)
            raise

    def import_profile_dir(self, source_dir: str) -> VoiceProfile:
        """Import a previously EXPORTED voice profile directory (P3.43 §10).

        The directory must contain a ``profile.json``; the assets are
        restored from the files ACTUALLY PRESENT in the directory
        (reference.wav / transcript.txt / avatar.<ext>) — stored path
        values from the JSON are deliberately NOT trusted, which makes
        exports portable regardless of the relative paths they were
        written with (the old round-trip killer).

        Security (P3.43 §30): the profile is rebuilt under a SAFE id
        (the exported id when it is safe and still free, otherwise a
        freshly generated one); nothing from the JSON is executed; only
        whitelisted filenames are copied; the new id can never escape
        voices/.
        """
        if not os.path.isdir(source_dir):
            raise InvalidVoice(
                "Voice profile folder not found: {0}".format(source_dir))
        meta_path = os.path.join(source_dir, "profile.json")
        if not os.path.isfile(meta_path):
            raise InvalidVoice(
                "Not a voice profile folder (profile.json is missing).")
        try:
            with open(meta_path, "r", encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
        except Exception as exc:
            raise InvalidVoice(
                "Could not read the profile metadata: {0}".format(exc)) from exc
        if not isinstance(data, dict) or not str(data.get("name", "")).strip():
            raise InvalidVoice(
                "The profile metadata does not contain a valid name.")

        name = str(data["name"]).strip()
        # Rebuild the id: prefer the exported id when it is safe and free
        # (dangling references may even resolve again); otherwise mint a
        # fresh unique id. NEVER trust a path-like id.
        wanted_id = data.get("id") or ""
        if not is_safe_profile_id(str(wanted_id)) or \
                os.path.isdir(os.path.join(self._voices_dir, str(wanted_id))):
            wanted_id = None

        # Locate the actual assets in the source directory.
        wav_path = ""
        if os.path.isfile(os.path.join(source_dir, "reference.wav")):
            wav_path = os.path.join(source_dir, "reference.wav")
        avatar_path = ""
        for ext in self.AVATAR_EXTENSIONS:
            candidate = os.path.join(source_dir, "avatar{0}".format(ext))
            if os.path.isfile(candidate):
                avatar_path = candidate
                break
        transcript = str(data.get("reference_transcript", "") or "")
        if not transcript and os.path.isfile(
                os.path.join(source_dir, "transcript.txt")):
            try:
                with open(os.path.join(source_dir, "transcript.txt"),
                          "r", encoding="utf-8", errors="replace") as fh:
                    transcript = fh.read()
            except OSError as exc:
                logger.warning("Could not read transcript.txt: %s", exc)

        gender = str(data.get("gender", "") or "")
        age_range = str(data.get("age_range", "") or "")
        mood = str(data.get("mood", "") or "")
        description = str(data.get("description", "") or "")
        tags: List[str] = []
        raw_tags = data.get("tags", [])
        if isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]

        if wanted_id is not None:
            # Import under the original id: build the profile with the
            # requested id, then populate (same transactional rollback).
            profile_dir = os.path.join(self._voices_dir, wanted_id)
            os.makedirs(profile_dir, exist_ok=True)
            profile = VoiceProfile(
                id=wanted_id, name=name, description=description,
                tags=list(tags))
            try:
                self._save_profile(profile)
                if wav_path:
                    profile = self.import_reference(
                        profile.id, wav_path, transcript)
                elif transcript:
                    profile = self.set_transcript(profile.id, transcript)
                if avatar_path:
                    profile = self.import_avatar(profile.id, avatar_path)
                profile = self.set_speaker_metadata(
                    profile.id, gender, age_range, mood)
                profile = self.update_details(profile.id, description, tags)
                return profile
            except Exception as exc:
                logger.error("Profile directory import failed for %r — "
                             "rolling back: %s", source_dir, exc)
                self.delete_profile(profile.id)
                raise

        return self.import_full(
            name, wav_path=wav_path, transcript=transcript,
            avatar_path=avatar_path, gender=gender, age_range=age_range,
            mood=mood, description=description, tags=tags)

    # ------------------------------------------------------------------
    # P3.43: asset path resolution (single authority for the UI)
    # ------------------------------------------------------------------
    def resolve_asset(self, relative_path: str) -> str:
        """Resolve an app-root-relative profile asset path to absolute.

        Used by the UI (via the Engine facade) to locate a profile's
        reference audio / avatar without knowing the storage layout. The
        normalised result is guaranteed to stay inside the application
        root — stored relative paths are untrusted input (P3.43 §30).
        """
        if not relative_path:
            return ""
        root = os.path.abspath(self._get_app_root())
        candidate = os.path.normpath(os.path.join(root, relative_path))
        if candidate != root and candidate.startswith(root + os.sep):
            return candidate
        return ""

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self, profile_id: str) -> List[str]:
        """Return a list of validation warnings for a voice profile.

        An empty list means the profile is valid for voice cloning.
        """
        warnings: List[str] = []
        profile = self.get_profile(profile_id)
        if profile is None:
            return ["Voice profile not found."]

        if not profile.reference_audio_path:
            warnings.append("No reference audio file.")
        else:
            full_path = os.path.join(self._get_app_root(),
                                     profile.reference_audio_path)
            if not os.path.isfile(full_path):
                warnings.append("Reference audio file is missing from disk.")
            elif profile.sample_rate <= 0:
                warnings.append("Reference audio sample rate not detected.")

        if not profile.reference_transcript.strip():
            warnings.append("No reference transcript (optional but recommended).")

        if profile.duration > 0 and profile.duration > 30.0:
            warnings.append(
                "Reference audio is {0:.1f}s long; 5-15s is optimal."
                .format(profile.duration)
            )
        return warnings

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _load_profile(self, profile_id: str) -> Optional[VoiceProfile]:
        profile_dir = os.path.join(self._voices_dir, profile_id)
        meta_path = os.path.join(profile_dir, "profile.json")
        if not os.path.isfile(meta_path):
            return None
        try:
            with open(meta_path, "r", encoding="utf-8",
                      errors="replace") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                logger.error("Profile %s metadata is not a JSON object.",
                             profile_id)
                return None
            # Ensure paths are relative.
            data["id"] = profile_id
            profile = VoiceProfile.from_dict(data)
            # P3.43 avatar path migration (deterministic, data-compatible):
            # profiles written before P3.43 store preview_image as a bare
            # filename ("avatar.png" — profile-dir-relative) while every
            # consumer resolves app-root-relative paths. Normalise the
            # legacy value on load when the file actually exists in the
            # profile directory; the next save persists the unified
            # convention. Absolute preview paths are left untouched.
            preview = profile.preview_image or ""
            if preview and not os.path.isabs(preview) and \
                    os.sep not in preview and not preview.startswith("voices"):
                candidate = os.path.join(profile_dir, preview)
                if os.path.isfile(candidate):
                    profile.preview_image = os.path.relpath(
                        candidate, self._get_app_root())
                else:
                    # Legacy value points at nothing — clear it so the UI
                    # shows a clean "no avatar" state instead of a path
                    # that can never resolve.
                    profile.preview_image = ""
            return profile
        except Exception as exc:
            # P3.25 (audit SS-M06): broad per-profile catch — a single
            # corrupt profile.json (wrong encoding, wrong types) must
            # never crash list_profiles()/get_profile() and blank the
            # whole voice library.
            logger.error("Failed to load profile %s: %s (%s)",
                         profile_id, exc, type(exc).__name__)
            return None

    def _save_profile(self, profile: VoiceProfile) -> None:
        profile_dir = os.path.join(self._voices_dir, profile.id)
        os.makedirs(profile_dir, exist_ok=True)
        meta_path = os.path.join(profile_dir, "profile.json")
        try:
            with open(meta_path, "w", encoding="utf-8") as handle:
                json.dump(profile.to_dict(), handle, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.error("Failed to save profile %s: %s",
                         profile.id, exc)
            raise

    def _save_transcript(self, profile_id: str, transcript: str) -> None:
        path = os.path.join(self._voices_dir, profile_id, "transcript.txt")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(transcript)
        except OSError as exc:
            logger.error("Failed to save transcript: %s", exc)

    @staticmethod
    def _make_id(name: str) -> str:
        """Generate a filename-safe profile id from a display name."""
        import re
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", name.strip().lower())
        slug = slug.strip("_") or "voice"
        # Append a short unique suffix to avoid collisions.
        return "{0}_{1}".format(slug, uuid.uuid4().hex[:8])

    def _get_app_root(self) -> str:
        """Return the application root.

        self._voices_dir is <app_root>/voices, so the app root is one
        directory level up.
        """
        return os.path.dirname(os.path.abspath(self._voices_dir))

    @staticmethod
    def _read_wav_metadata(wav_path: str):
        """Read sample rate, channels, and duration from a WAV file.

        Uses the standard wave module so no external dependency is needed.
        Returns (sample_rate, channels, duration_seconds).
        """
        try:
            with wave.open(wav_path, "rb") as wav:
                sample_rate = wav.getframerate()
                channels = wav.getnchannels()
                frames = wav.getnframes()
                duration = frames / float(sample_rate) if sample_rate > 0 else 0.0
                return sample_rate, channels, duration
        except Exception as exc:
            logger.warning("voice_manager: could not read WAV metadata: %s", exc)
            return 0, 0, 0.0
