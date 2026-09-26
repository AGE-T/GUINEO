"""
SpeechStudio Engine - Facade
=============================

Engine Specification, section 21:
    "The UI never communicates directly with Transformers.
     The UI interacts only with Engine services.
     Typical workflow: Generate Button -> Engine.generate() -> Result Object
     -> UI Update."

This module is the single entry point. The UI (Phase 3) imports only
`from engine import Engine` and calls its methods. Every internal manager
is hidden behind the facade.

Engine Specification, section 14 (Generation Pipeline):
    Receive Request -> Validate -> Build Prompt -> Prepare Parameters ->
    Call generate_speech() -> Receive Audio -> Append Silence ->
    Normalize (optional) -> Save WAV -> Store History -> Return Result

Engine Specification, section 13 (Validation Pipeline):
    Before generation the Engine validates: Model loaded, Tokenizer loaded,
    Prompt valid, Voice profile valid, Reference audio valid, Generation
    parameters valid, Output directory writable.

Engine Specification, section 23 (Engine Constraints):
    "The Engine shall never: Access UI widgets, Show dialogs, Modify UI
     state, Write outside the project directory, Load multiple model
     instances, Bypass PromptBuilder."
"""

from __future__ import annotations
import os
import threading
from datetime import datetime
from typing import Callable, Optional, List

from engine.logger import get_logger, Logger
from engine.events import EventBus, EventType, Event
from engine.errors import (
    EngineError, ValidationFailed, ModelNotLoaded, CudaUnavailable,
    InvalidPrompt, InvalidVoice,
)
from engine.models import (
    GenerationRequest, GenerationResult, GenerationParameters,
    VoiceProfile, ModelStatus, PromptData,
)
from engine.settings_manager import SettingsManager
from engine.history_manager import HistoryManager
from engine.prompt_builder import PromptBuilder
from engine.voice_manager import VoiceManager
from engine.audio_manager import AudioManager
from engine.model_manager import ModelManager
from engine.generation_manager import GenerationManager
from engine.workers import WorkerPool

from concurrent.futures import Future

logger = get_logger("engine")


class Engine:
    """The single entry point for all UI operations.

    The UI creates one Engine instance at startup and calls its methods.
    Every method returns plain data (no Qt types) so the Engine is UI-
    agnostic.

    Lifecycle:
        engine = Engine()         # initialise (does NOT load the model)
        engine.load_model()       # load model on a worker thread
        result = engine.generate(request)  # generate speech
        engine.shutdown()         # release everything
    """

    def __init__(self, app_root: Optional[str] = None):
        """Initialise the Engine and all its managers.

        Args:
            app_root: path to the SpeechStudio project root. If None,
                      auto-detected from this file's location.
        """
        if app_root is None:
            app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._app_root = app_root

        # --- Paths (all relative to app_root) ---
        settings_dir = os.path.join(app_root, "settings")
        history_dir = os.path.join(settings_dir, "history")
        voices_dir = os.path.join(app_root, "voices")
        outputs_dir = os.path.join(app_root, "outputs")
        # The model is loaded from a local directory (populated by bootstrap)
        # to avoid Windows symlink issues with the HF cache.
        model_path = os.path.join(app_root, "models", "higgs-audio-v3")

        # --- Core infrastructure ---
        Logger()  # initialise the logging singleton
        self._event_bus = EventBus()
        self._worker_pool = WorkerPool(self._event_bus)

        # --- Managers (single responsibility each) ---
        # P3.25 (audit SS-H04): the SHARED settings owner — Engine,
        # MainWindow and the entry point must never hold divergent
        # SettingsManager caches over the same file.
        self._settings = SettingsManager.instance(settings_dir)
        self._history = HistoryManager(history_dir)
        self._prompt_builder = PromptBuilder()
        self._voices = VoiceManager(voices_dir)
        self._audio = AudioManager(outputs_dir)
        self._model = ModelManager(self._event_bus, model_path,
                                   settings_manager=self._settings)
        self._generation = GenerationManager(self._model, self._audio)

        logger.info("Engine initialised. App root: %s", app_root)
        self._event_bus.emit(Event(EventType.ENGINE_INITIALIZED))

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------
    def load_model(self, use_cuda: bool = True,
                   callback: Optional[Callable[[ModelStatus], None]] = None) -> Future:
        """Load the model and tokenizer on a worker thread.

        Returns a Future that resolves to a ModelStatus when loading is
        complete (or raises ModelLoadFailed).

        Args:
            use_cuda: prefer CUDA if available.
            callback: optional callback invoked with the final ModelStatus.
        """
        def _do_load():
            try:
                status = self._model.load(use_cuda=use_cuda)
                if callback:
                    callback(status)
                return status
            except Exception:
                if callback:
                    callback(self._model.status())
                raise

        return self._worker_pool.submit_background(_do_load)

    def warmup_model(self) -> None:
        """Run a warmup generation. Call after load_model completes."""
        if self._model.is_loaded:
            self._model.warmup()

    def get_model_status(self) -> ModelStatus:
        """Return the current model/tokenizer/CUDA status."""
        return self._model.status()

    # ------------------------------------------------------------------
    # Generation (the main API)
    # ------------------------------------------------------------------
    def generate(self, request: GenerationRequest) -> Future:
        """Submit a generation request. Returns a Future<GenerationResult>.

        The UI should attach a callback to the future:

            future = engine.generate(request)
            future.add_done_callback(on_done)

        The generation runs on a worker thread so the UI stays responsive.
        Only one generation can run at a time.
        """
        def _do_generate():
            return self._execute_generation(request)

        return self._worker_pool.submit_generation(_do_generate)

    def cancel_generation(self) -> bool:
        """Attempt to cancel the current generation."""
        return self._worker_pool.cancel_generation()

    @property
    def is_generating(self) -> bool:
        return self._worker_pool.is_generating

    def _execute_generation(self, request: GenerationRequest) -> GenerationResult:
        """Run the full generation pipeline on the worker thread.

        Pipeline (Engine Specification section 14):
          Validate -> Build Prompt -> Generate -> Post-process -> Save ->
          Store History -> Return Result.

        P3.30(d) cancellation checkpoints: the pipeline polls the worker
        pool's cooperative cancel event at every SAFE boundary (pipeline
        entry and immediately BEFORE the model call). A cancel at those
        points returns a failed result with ``cancelled=True`` — no file
        is written, no history is stored. Inside the native
        ``generate_speech()`` call there is no interruption hook (model
        documentation exposes no stopping criteria / streamer API), so a
        cancel that arrives mid-call lets that call finish and KEEPS its
        audio (a stop must never destroy completed work).
        """
        def _cancelled() -> bool:
            try:
                return self._worker_pool.cancel_requested()
            except Exception:
                return False

        def _cancelled_result() -> GenerationResult:
            return GenerationResult(
                success=False,
                parameters=request.parameters,
                prompt=request.text,
                timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
                cancelled=True,
                errors=[{
                    "type": "Cancelled",
                    "message": "Generation cancelled by user.",
                    "suggested_action": "",
                }],
            )

        try:
            # P3.30(d): checkpoint — cancel before any work (queued case).
            if _cancelled():
                logger.info("Generation cancelled before pipeline start")
                result = _cancelled_result()
                self._event_bus.emit(Event(EventType.GENERATION_FAILED,
                                           result))
                return result
            # --- VOICE VERIFY log ---
            # Emitted BEFORE Engine.generate() does anything, so the
            # user-visible voice selection is recorded in the engine log
            # even if the request fails to validate.
            if request.voice_id:
                verify_voice = self._voices.get_profile(request.voice_id)
                verify_name = (
                    verify_voice.name if verify_voice is not None
                    else "(profile not found)"
                )
                verify_ref = (
                    verify_voice.reference_audio_path
                    if verify_voice is not None else "(none)"
                )
                logger.info(
                    "VOICE VERIFY: selected_voice_id=%s "
                    "selected_voice_name=%s reference_audio=%s",
                    request.voice_id, verify_name, verify_ref,
                )
            else:
                logger.info(
                    "VOICE VERIFY: selected_voice_id=(none) "
                    "selected_voice_name=(none) reference_audio=(none)",
                )

            # --- Step 1: Validate ---
            self._validate(request)

            # --- Step 2: Build prompt ---
            # The UI always builds the final prompt via the PromptBuilder
            # (or the NarrationEditor's block-aware prompt builder, which
            # itself calls PromptBuilder).  The request.text is therefore
            # the final, fully-tokenised prompt.  We use it as-is - no
            # heuristic detection of "<|" is performed (the previous
            # "<| in text" heuristic was removed because it bypassed the
            # PromptBuilder contract for inputs that merely contained
            # literal angle-bracket characters).
            prompt_data = PromptData(
                final_prompt=request.text,
                plain_text=request.text,
                token_preview=[],
                warnings=[],
            )
            # --- Stage 3: Pipeline verification (SHA-256) ---
            try:
                import hashlib
                prompt_hash = hashlib.sha256(
                    request.text.encode("utf-8")).hexdigest()
                logger.info(
                    "PIPELINE VERIFY [Stage 3 - Engine._execute_generation] "
                    "prompt_sha256=%s len=%d",
                    prompt_hash, len(request.text))
            except Exception:
                pass

            logger.info("Using pre-built prompt (%d chars)", len(request.text))

            # --- Step 3: Resolve voice profile ---
            voice_profile = None
            if request.voice_id:
                voice_profile = self._voices.get_profile(request.voice_id)
                if voice_profile is None:
                    # VOICE RESOLVE log — voice_id supplied but no profile
                    # found on disk. This is a hard failure: we never
                    # silently substitute another voice.
                    logger.error(
                        "VOICE RESOLVE: requested_voice_id=%s NOT FOUND "
                        "(profile directory missing or deleted)",
                        request.voice_id,
                    )
                    raise InvalidVoice(
                        "Voice profile not found: {0}".format(request.voice_id),
                        suggested_action=(
                            "The voice profile may have been deleted. "
                            "Select a different voice in the control panel, "
                            "or recreate this voice profile in the Voice "
                            "Library."
                        ),
                    )
                # VOICE RESOLVE log — profile resolved successfully.
                logger.info(
                    "VOICE RESOLVE: requested_voice_id=%s resolved_voice_id=%s "
                    "resolved_voice_name=%s reference_audio=%s",
                    request.voice_id, voice_profile.id, voice_profile.name,
                    voice_profile.reference_audio_path or "(none)",
                )

            # --- Step 4: Generate ---
            # P3.30(d): checkpoint — the LAST safe boundary before the
            # uninterruptible native model call. A cancel here avoids the
            # whole model run (the expensive part) instead of burning the
            # GPU on audio the user no longer wants.
            if _cancelled():
                logger.info("Generation cancelled before model call")
                result = _cancelled_result()
                self._event_bus.emit(Event(EventType.GENERATION_FAILED,
                                           result))
                return result
            result = self._generation.generate(request, prompt_data, voice_profile)

            # P2.6: Pass scene/character context from request to result
            # so HistoryEntry can carry it.
            if hasattr(request, 'scene_id'):
                result.scene_id = request.scene_id
            if hasattr(request, 'scene_name'):
                result.scene_name = request.scene_name
            if hasattr(request, 'character_id'):
                result.character_id = request.character_id
            if hasattr(request, 'speaker'):
                result.speaker = request.speaker
            # P3.27B: pass Long Generation part provenance through to the
            # result so HistoryEntry records part index + generation version.
            if getattr(request, 'part_index', None) is not None:
                result.part_index = request.part_index
            if getattr(request, 'part_version', None) is not None:
                result.part_version = request.part_version
            # P3.28: pass block + generation-run provenance through to the
            # result (consumed by the single registration writer in
            # engine/audio_provenance.register_generation_result).
            if getattr(request, 'block_id', None) is not None:
                result.block_id = request.block_id
            if getattr(request, 'generation_run', None) is not None:
                result.generation_run = request.generation_run
            # P3.44.6: pass the authoritative modern structural slot
            # identity through to the result (same consumer as above —
            # register_generation_result stamps it onto the AudioAsset
            # so slot membership never has to be re-derived from the
            # global part_index).
            if getattr(request, 'slot_id', None) is not None:
                result.slot_id = request.slot_id

            # --- Step 5: Store history ---
            if result.success:
                self._history.add(result)
                self._event_bus.emit(Event(EventType.HISTORY_UPDATED))
                self._event_bus.emit(Event(EventType.GENERATION_FINISHED, result))

            return result

        except EngineError as exc:
            # Structured engine error - package into a failed result.
            logger.error("Generation failed: %s", exc)
            # P3.25 (audit data bug): the failure path previously stored
            # the WORKER THREAD NAME as the result timestamp — it flowed
            # into history ids / recents / AudioAsset.generated_at.
            failed_result = GenerationResult(
                success=False,
                parameters=request.parameters,
                prompt=request.text,
                timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
                errors=[exc.to_dict()],
            )
            self._event_bus.emit(Event(EventType.GENERATION_FAILED, failed_result))
            return failed_result

        except Exception as exc:
            # Unexpected error - wrap it.
            logger.exception("Unexpected error during generation: %s", exc)
            failed_result = GenerationResult(
                success=False,
                parameters=request.parameters,
                prompt=request.text,
                errors=[{
                    "type": "UnexpectedError",
                    "message": str(exc),
                    "suggested_action": "Check logs/engine.log for details.",
                }],
            )
            self._event_bus.emit(Event(EventType.GENERATION_FAILED, failed_result))
            return failed_result

    # ------------------------------------------------------------------
    # Validation pipeline
    # ------------------------------------------------------------------
    # Engine Specification section 13.
    def _validate(self, request: GenerationRequest) -> None:
        """Run the pre-generation validation pipeline.

        Raises ValidationFailed with a list of issues if any check fails.

        Voice validation policy:
            - If request.voice_id is set, the voice profile MUST exist
              (no silent substitution).
            - If the profile exists, VoiceManager.validate() is consulted
              to verify reference audio integrity (file exists, sample
              rate detected). Missing/corrupt reference audio BLOCKS
              generation — the user gets a clear error rather than audio
              generated with the wrong voice.
            - Non-blocking warnings (e.g. "no transcript", "audio is
              >30s long") do NOT block generation; they are advisory only.
        """
        issues: List[str] = []

        # Model loaded.
        if not self._model.is_loaded:
            issues.append("Model is not loaded.")

        # CUDA available (unless CPU mode).
        status = self._model.status()
        if not status.cuda_available and self._model.device != "cpu":
            issues.append("CUDA is not available.")

        # Prompt not empty.
        if not request.text or not request.text.strip():
            issues.append("Prompt text is empty.")

        # Generation parameters valid.
        param_warnings = request.parameters.validate()
        issues.extend(param_warnings)

        # Voice profile valid (if specified).
        if request.voice_id:
            voice = self._voices.get_profile(request.voice_id)
            if voice is None:
                issues.append(
                    "Voice profile not found: {0}".format(request.voice_id),
                )
            else:
                # Reuse the existing VoiceManager.validate() — never
                # duplicate the validation logic.
                voice_warnings = self._voices.validate(request.voice_id)
                # Split into blocking vs advisory:
                #   - BLOCKING: missing reference audio, undetected sample rate
                #   - ADVISORY: missing transcript, audio too long
                for w in voice_warnings:
                    low = w.lower()
                    if (
                        "missing" in low and "audio" in low
                    ) or (
                        "no reference audio" in low
                    ) or (
                        "sample rate not detected" in low
                    ) or (
                        "not found" in low
                    ):
                        issues.append(
                            "Voice '{0}': {1}".format(voice.name, w),
                        )
                    # else: advisory warnings are intentionally ignored
                    # here — they don't block generation.

        # Output directory writable.
        outputs_dir = os.path.join(self._app_root, "outputs")
        if not os.path.isdir(outputs_dir):
            try:
                os.makedirs(outputs_dir, exist_ok=True)
            except OSError:
                issues.append("Output directory cannot be created: outputs/")

        if issues:
            raise ValidationFailed(
                "Validation failed: " + "; ".join(issues),
                "Resolve the issues listed in the UI and try again.",
            )

    # ------------------------------------------------------------------
    # Voice management
    # ------------------------------------------------------------------
    # P3.43 (Voice Profile Unification): this facade is the COMPLETE public
    # voice API. The UI never reaches into engine._voices — every profile
    # operation is exposed here, and every MUTATION emits VOICE_CHANGED so
    # all selectors / lists / dependent UI refresh from the authoritative
    # state (P3.43 §5/§6). Read-only operations (export, validate, resolve)
    # emit nothing.

    @property
    def app_root(self) -> str:
        """The application root directory (P3.43: public read-only)."""
        return self._app_root

    def list_voices(self) -> List[VoiceProfile]:
        return self._voices.list_profiles()

    def get_voice(self, voice_id: str) -> Optional[VoiceProfile]:
        return self._voices.get_profile(voice_id)

    def create_voice(self, name: str, description: str = "",
                     tags: Optional[List[str]] = None) -> VoiceProfile:
        voice = self._voices.create_profile(name, description, tags)
        self._event_bus.emit(Event(EventType.VOICE_CHANGED, voice.id))
        return voice

    def delete_voice(self, voice_id: str) -> bool:
        result = self._voices.delete_profile(voice_id)
        self._event_bus.emit(Event(EventType.VOICE_CHANGED))
        return result

    def import_voice_reference(self, voice_id: str, wav_path: str,
                               transcript: str = "") -> VoiceProfile:
        voice = self._voices.import_reference(voice_id, wav_path, transcript)
        self._event_bus.emit(Event(EventType.VOICE_CHANGED, voice.id))
        return voice

    def import_voice_profile(self, name: str, wav_path: str = "",
                             transcript: str = "", avatar_path: str = "",
                             gender: Optional[str] = None,
                             age_range: Optional[str] = None,
                             mood: Optional[str] = None,
                             description: str = "",
                             tags: Optional[List[str]] = None) -> VoiceProfile:
        """Transactional full import (P3.43 §9): create + reference audio +
        transcript + avatar + speaker metadata in ONE operation. On any
        failure the whole profile is rolled back — no partial state."""
        voice = self._voices.import_full(
            name, wav_path=wav_path, transcript=transcript,
            avatar_path=avatar_path, gender=gender, age_range=age_range,
            mood=mood, description=description, tags=tags)
        self._event_bus.emit(Event(EventType.VOICE_CHANGED, voice.id))
        return voice

    def import_voice_profile_dir(self, source_dir: str) -> VoiceProfile:
        """Re-import a previously exported voice profile directory.

        Restores name, metadata, transcript, reference audio and avatar
        from the actual files present in the folder (stored paths are not
        trusted — exports are portable). P3.43 §10 round-trip support."""
        voice = self._voices.import_profile_dir(source_dir)
        self._event_bus.emit(Event(EventType.VOICE_CHANGED, voice.id))
        return voice

    def rename_voice(self, voice_id: str, new_name: str) -> VoiceProfile:
        """Rename a voice profile (id and reference audio are kept)."""
        voice = self._voices.rename_profile(voice_id, new_name)
        self._event_bus.emit(Event(EventType.VOICE_CHANGED, voice.id))
        return voice

    def update_voice_transcript(self, voice_id: str,
                                transcript: str) -> VoiceProfile:
        """Set / clear the reference transcript of a voice profile."""
        voice = self._voices.set_transcript(voice_id, transcript)
        self._event_bus.emit(Event(EventType.VOICE_CHANGED, voice.id))
        return voice

    def update_voice_details(self, voice_id: str,
                             description: Optional[str] = None,
                             tags: Optional[List[str]] = None) -> VoiceProfile:
        """Set / clear the description and tags of a voice profile."""
        voice = self._voices.update_details(voice_id, description, tags)
        self._event_bus.emit(Event(EventType.VOICE_CHANGED, voice.id))
        return voice

    # P3.23 (voice import API drift fix): the Voice Import dialog calls
    # these two methods. They previously did not exist — the calls raised
    # AttributeError and the whole import reported "Failed to import
    # voice" whenever an avatar or gender/age/mood was provided.
    def import_voice_avatar(self, voice_id: str, avatar_path: str) -> VoiceProfile:
        """Import/replace the avatar of a voice profile."""
        voice = self._voices.import_avatar(voice_id, avatar_path)
        self._event_bus.emit(Event(EventType.VOICE_CHANGED, voice.id))
        return voice

    def remove_voice_avatar(self, voice_id: str) -> VoiceProfile:
        """Remove the avatar of a voice profile (P3.43 §8)."""
        voice = self._voices.remove_avatar(voice_id)
        self._event_bus.emit(Event(EventType.VOICE_CHANGED, voice.id))
        return voice

    def set_voice_speaker_metadata(self, voice_id: str,
                                   gender: Optional[str] = None,
                                   age_range: Optional[str] = None,
                                   mood: Optional[str] = None) -> VoiceProfile:
        """Set the speaker metadata (gender / age range / mood).

        P3.43 §7 semantics: each argument is applied explicitly — None
        keeps the current value, an EMPTY string CLEARS the field (the
        user explicitly emptied it), non-empty text sets it.
        """
        voice = self._voices.set_speaker_metadata(
            voice_id, gender, age_range, mood)
        self._event_bus.emit(Event(EventType.VOICE_CHANGED, voice.id))
        return voice

    def export_voice_profile(self, voice_id: str, export_dir: str) -> str:
        """Export a voice profile folder (metadata + reference WAV +
        transcript + avatar). Returns the exported directory path.
        Read-only — emits no change event."""
        return self._voices.export_profile(voice_id, export_dir)

    def validate_voice_profile(self, voice_id: str) -> List[str]:
        """Return validation warnings for a voice profile (empty = valid).
        Read-only — emits no change event."""
        return self._voices.validate(voice_id)

    def voice_reference_path(self, voice_id: str) -> str:
        """Absolute path of the profile's reference audio ("" when none).

        The single authoritative resolver for the reference WAV — the
        management screen's Play action uses this instead of joining
        paths itself (P3.43 §25)."""
        voice = self.get_voice(voice_id)
        if voice is None or not voice.reference_audio_path:
            return ""
        return self._voices.resolve_asset(voice.reference_audio_path)

    def resolve_voice_asset(self, relative_path: str) -> str:
        """Resolve an app-root-relative profile asset path safely.

        Guaranteed to stay inside the application root (stored relative
        paths are untrusted input, P3.43 §30)."""
        return self._voices.resolve_asset(relative_path)

    # ------------------------------------------------------------------
    # Prompt preview (without generating)
    # ------------------------------------------------------------------
    def preview_prompt(self, request: GenerationRequest) -> PromptData:
        """Build and return the prompt without generating speech.

        Used by the Prompt Preview panel.
        """
        return self._prompt_builder.build(
            text=request.text,
            emotion=request.emotion,
            style=request.style,
            speed=request.speed,
            pitch=request.pitch,
            delivery=request.delivery,
            sfx_insertions=request.sfx_insertions,
            pause_insertions=request.pause_insertions,
        )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def get_settings(self) -> dict:
        return self._settings.all_settings

    def get_generation_defaults(self) -> GenerationParameters:
        return self._settings.get_generation_parameters()

    def set_generation_defaults(self, params: GenerationParameters) -> None:
        self._settings.set_generation_parameters(params)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def get_history(self):
        return self._history.list_entries()

    def delete_history_entry(self, entry_id: str, delete_audio: bool = False) -> bool:
        result = self._history.delete(entry_id, delete_audio)
        self._event_bus.emit(Event(EventType.HISTORY_UPDATED))
        return result

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def subscribe(self, event_type: EventType, callback: Callable[[Event], None]) -> None:
        """Subscribe to an engine event (Phase 3 UI will use this)."""
        self._event_bus.subscribe(event_type, callback)

    def unsubscribe(self, event_type: EventType, callback: Callable[[Event], None]) -> None:
        self._event_bus.unsubscribe(event_type, callback)

    # ------------------------------------------------------------------
    # Audio playback
    # ------------------------------------------------------------------
    def play_audio(self, path: str) -> None:
        """Play an audio file from the outputs directory."""
        try:
            samples, sr = self._audio.load_wav(path)
            self._audio.play(samples, sr)
            self._event_bus.emit(Event(EventType.PLAYBACK_STARTED, path))
        except Exception as exc:
            logger.error("Playback failed: {0}".format(exc))

    def play_audio_from_position(self, path: str,
                                 start_position_sec: float) -> None:
        """Play an audio file starting from a specific position (in seconds).

        Used by the waveform-seek UI when the user clicks on the waveform
        while playback is stopped.
        """
        try:
            samples, sr = self._audio.load_wav(path)
            self._audio.play(samples, sr,
                             start_position_sec=start_position_sec)
            self._event_bus.emit(Event(EventType.PLAYBACK_STARTED, path))
        except Exception as exc:
            logger.error("Playback (from position) failed: {0}".format(exc))

    def stop_playback(self) -> None:
        self._audio.stop_playback()
        self._event_bus.emit(Event(EventType.PLAYBACK_FINISHED))

    def pause_playback(self) -> None:
        """Pause playback (position is retained)."""
        self._audio.pause_playback()
        self._event_bus.emit(Event(EventType.PLAYBACK_PAUSED))

    def resume_playback(self) -> None:
        """Resume playback after pause."""
        self._audio.resume_playback()
        self._event_bus.emit(Event(EventType.PLAYBACK_RESUMED))

    def seek_audio(self, position_sec: float) -> None:
        """Seek to a position in the current playback (in seconds)."""
        self._audio.seek(position_sec)
        self._event_bus.emit(Event(EventType.PLAYBACK_SEEKED, position_sec))

    def get_playback_position(self) -> float:
        """Return the current playback position in seconds (0.0 if idle)."""
        return self._audio.get_playback_position()

    def get_playback_duration(self) -> float:
        """Return the total duration of the current playback in seconds."""
        return self._audio.get_playback_duration()

    def get_playback_state(self) -> str:
        """Return the current playback state.

        One of: "stopped", "playing", "paused".
        """
        return self._audio.get_playback_state()

    def set_playback_position_callback(self, callback) -> None:
        """Register a callback for playback position updates.

        The callback receives (position_sec, duration_sec) and is invoked
        from the audio thread (~20 Hz). Subscribers that touch the UI must
        marshal back to the UI thread.
        """
        self._audio.set_position_callback(callback)

    def set_playback_finished_callback(self, callback) -> None:
        """Register a callback invoked when playback finishes naturally."""
        self._audio.set_finished_callback(callback)

    def set_playback_volume(self, volume: float) -> None:
        """Set the master playback volume (linear gain 0.0 - 1.0).

        Affects the current playback (live) and all future playbacks.
        """
        self._audio.set_volume(volume)

    def get_playback_volume(self) -> float:
        """Return the current master playback volume (0.0 - 1.0)."""
        return self._audio.get_volume()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    # System Specification section 19.
    def shutdown(self) -> None:
        """Release all resources. Call during application shutdown."""
        logger.info("Engine shutting down ...")
        # Stop playback.
        self._audio.stop_playback()
        # Cancel any running generation.
        self._worker_pool.cancel_generation()
        # Unload the model.
        self._model.unload()
        # Shut down the worker pool.
        self._worker_pool.shutdown()
        # Clear event subscribers.
        self._event_bus.clear()
        logger.info("Engine shutdown complete.")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def app_root(self) -> str:
        return self._app_root

    @property
    def is_model_loaded(self) -> bool:
        return self._model.is_loaded
