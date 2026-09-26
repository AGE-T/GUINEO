"""
SpeechStudio Engine - Generation Manager
=========================================

Engine Specification, section 8:
    "GenerationManager is responsible for: Preparing generation request,
     Calling generate_speech(), Receiving generated waveform, Returning
     generation metadata.
     The GenerationManager owns generation parameters."

Generation Engine Specification, section 3 (Generation Pipeline):
    "UI -> Prompt Builder -> Validation -> Generation Engine ->
     generate_speech() -> Audio Buffer -> Optional Silence Padding ->
     Save Output -> Audio Player -> History"

Generation Engine Specification, section 8 (Generation Parameters):
    temperature, top_p, top_k, max_new_tokens, seed, reference_audio,
    reference_sample_rate, reference_text, append_silence, normalize_output,
    output_format.

Memory Ownership (Engine Specification section 20):
    "GenerationManager owns: Generation request, Generation result."

Single responsibility: execute a generation request against the loaded
model and return metadata. Never builds prompts (that's PromptBuilder),
never manages voices (that's VoiceManager), never writes audio files
(that's AudioManager - GenerationManager calls AudioManager to save).
"""

from __future__ import annotations
import os
import time
from typing import Optional

from engine.logger import get_logger
from engine.errors import (
    GenerationFailed, CudaUnavailable, ModelNotLoaded, InvalidVoice,
    ReferenceAudioMissing,
)
from engine.models import (
    GenerationRequest, GenerationResult, GenerationParameters,
    VoiceProfile, PromptData, ModelStatus,
)
from engine.model_manager import ModelManager
from engine.audio_manager import AudioManager

logger = get_logger("generation")


class GenerationManager:
    """Executes generation requests and returns metadata.

    The GenerationManager is the only component (besides ModelManager) that
    touches the model, and it only calls generate_speech(). It never
    modifies model state.

    Pipeline (Generation Engine Specification section 3):
      1. Receive validated request + prompt data
      2. Prepare generation parameters
      3. Call model.generate_speech()
      4. Receive generated waveform
      5. Append silence (optional)
      6. Normalise (optional)
      7. Save WAV (via AudioManager)
      8. Return metadata
    """

    def __init__(self, model_manager: ModelManager, audio_manager: AudioManager):
        self._model_manager = model_manager
        self._audio_manager = audio_manager

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def generate(
        self,
        request: GenerationRequest,
        prompt_data: PromptData,
        voice_profile: Optional[VoiceProfile] = None,
    ) -> GenerationResult:
        """Execute a single generation.

        Args:
            request: the validated GenerationRequest from the UI.
            prompt_data: the assembled prompt from PromptBuilder.
            voice_profile: the selected voice profile (None = no cloning).

        Returns:
            A GenerationResult (always; success or failure).

        Raises:
            ModelNotLoaded, CudaUnavailable, GenerationFailed:
                These are caught by the Engine facade and packaged into the
                result. They propagate here so the facade can distinguish
                error types for logging.
        """
        start_time = time.time()
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        params = request.parameters

        # --- Pre-flight checks ---
        if not self._model_manager.is_loaded:
            raise ModelNotLoaded(
                "Cannot generate: the model is not loaded.",
            )

        status = self._model_manager.status()
        if not status.cuda_available and self._model_manager.device != "cpu":
            raise CudaUnavailable(
                "CUDA is not available and CPU mode is not enabled.",
            )

        # --- Get model + tokenizer ---
        model, tokenizer = self._model_manager.get_model_and_tokenizer()

        # --- Stage 4/5: Pipeline verification (SHA-256) before generate_speech ---
        try:
            import hashlib
            prompt_hash = hashlib.sha256(
                prompt_data.final_prompt.encode("utf-8")).hexdigest()
            logger.info(
                "PIPELINE VERIFY [Stage 4/5 - GenerationManager.generate] "
                "prompt_sha256=%s len=%d voice=%s temp=%.2f top_p=%.2f "
                "top_k=%d max_tokens=%d",
                prompt_hash, len(prompt_data.final_prompt),
                voice_profile.name if voice_profile else "(none)",
                params.temperature, params.top_p,
                params.top_k, params.max_new_tokens)
        except Exception:
            pass

        # --- Build the generate_speech() call kwargs ---
        # The model's generate_speech() signature is:
        #   generate_speech(text, tokenizer, *, reference_audio=None,
        #     reference_sample_rate=None, reference_codes=None,
        #     reference_text=None, max_new_tokens=2048,
        #     temperature=1.0, top_p=None, top_k=None)
        # Note: 'text' NOT 'prompt'; 'seed' is NOT supported.
        gen_kwargs = {
            "text": prompt_data.final_prompt,
            "tokenizer": tokenizer,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "top_k": params.top_k,
            "max_new_tokens": params.max_new_tokens,
        }

        # Note: the model does not accept a 'seed' parameter.
        # If seed is set, we set it via torch.manual_seed before generation.
        if params.seed is not None:
            try:
                import torch
                torch.manual_seed(params.seed)
                logger.info("Set torch seed to %d", params.seed)
            except Exception:
                pass

        # Voice cloning (Generation Engine Specification section 7).
        #
        # CRITICAL CORRECTNESS RULE:
        #   If a voice_profile was supplied AND that profile claims to have
        #   a reference audio, then the reference audio MUST be loaded
        #   successfully. If it cannot be loaded, generation MUST FAIL
        #   loudly — we never silently fall back to "no voice cloning"
        #   because that would produce audio with the wrong voice character
        #   and the user would have no way to tell.
        #
        # The Engine facade already validates the voice profile before
        # calling generate(); if we reach this point with a voice_profile
        # that has_reference==True but the file is missing/corrupt, that
        # is a runtime integrity failure and must raise ReferenceAudioMissing.
        if voice_profile is not None and voice_profile.has_reference:
            # VOICE GENERATION log — emitted immediately before
            # model.generate_speech() so the on-disk reference path is
            # recorded in the engine log alongside the voice identity.
            logger.info(
                "VOICE GENERATION: voice_id=%s voice_name=%s "
                "reference_audio=%s reference_sample_rate=%d",
                voice_profile.id,
                voice_profile.name,
                voice_profile.reference_audio_path,
                voice_profile.sample_rate,
            )

            ref_audio, ref_sr = self._load_reference_audio(voice_profile)
            # _load_reference_audio now RAISES ReferenceAudioMissing on any
            # failure (missing path, missing file, decode error). If we reach
            # here, ref_audio is a valid tensor.
            gen_kwargs["reference_audio"] = ref_audio
            gen_kwargs["reference_sample_rate"] = ref_sr
            if voice_profile.reference_transcript:
                gen_kwargs["reference_text"] = voice_profile.reference_transcript
            logger.info("Using voice clone: %s (%s)",
                        voice_profile.name, voice_profile.id)
        elif voice_profile is not None and not voice_profile.has_reference:
            # A voice profile was selected but it has no usable reference
            # audio. This should already have been caught by Engine._validate,
            # but we defend in depth: never silently generate without the
            # requested voice.
            raise ReferenceAudioMissing(
                "Voice '{0}' has no reference audio attached. "
                "Import a reference WAV into this voice profile before "
                "generating with it.".format(voice_profile.name),
                suggested_action=(
                    "Open the Voice Library, select '{0}', and use "
                    "'Import reference audio' to attach a WAV file."
                ).format(voice_profile.name),
            )

        # --- Call generate_speech() ---
        logger.info("Starting generation: %d chars, temp=%.2f, top_p=%.2f, "
                    "top_k=%d, max_tokens=%d",
                    len(prompt_data.final_prompt),
                    params.temperature, params.top_p,
                    params.top_k, params.max_new_tokens)

        try:
            import torch
            import numpy as np

            with torch.no_grad():
                wav_tensor = model.generate_speech(**gen_kwargs)

            # generate_speech returns a mono 24kHz CPU float32 tensor [L].
            if hasattr(wav_tensor, "cpu"):
                wav_tensor = wav_tensor.cpu()
            if hasattr(wav_tensor, "numpy"):
                waveform = wav_tensor.numpy().astype(np.float32)
            else:
                waveform = np.asarray(wav_tensor, dtype=np.float32)

            # Ensure 1-D.
            if waveform.ndim > 1:
                waveform = waveform.flatten()

        except Exception as exc:
            logger.exception("generate_speech() failed: %s", exc)
            raise GenerationFailed(
                "The model raised an error during generation: {0}".format(exc),
            ) from exc

        generation_time = time.time() - start_time

        # --- Post-processing (Generation Engine Specification section 9) ---
        output_duration = self._audio_manager.get_duration(
            waveform, self._model_manager.sample_rate
        )

        # --- P3.27B: Output guard (pathological output detection) ----------
        # Analysed on the RAW model waveform BEFORE append_silence /
        # normalisation, so the measured trailing silence is exactly what
        # the model produced (task §7: "inspect the raw generated audio
        # before any application post-processing"). The guard NEVER
        # modifies or discards the audio — it only detects and flags; the
        # user decides (keep / regenerate / inspect). A detection failure
        # must never fail the generation itself.
        output_anomaly = None
        try:
            from engine.output_guard import (
                analyze_waveform, detect_output_anomaly,
                anomaly_warning_text,
            )
            analysis = analyze_waveform(
                waveform, self._model_manager.sample_rate)
            output_anomaly = detect_output_anomaly(
                analysis,
                expected_duration_s=getattr(request, "expected_duration", None),
                max_new_tokens=params.max_new_tokens,
            )
            if output_anomaly is not None:
                logger.warning(
                    "OUTPUT GUARD: anomalous generation detected "
                    "(duration=%.1fs speech=%.1fs tail=%.1fs): %s",
                    output_duration, analysis.get("speech_s", 0.0),
                    analysis.get("trailing_silence_s", 0.0),
                    "; ".join(output_anomaly.get("reasons", [])),
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("OUTPUT GUARD: analysis skipped (%s)", exc)

        if params.append_silence > 0:
            waveform = self._audio_manager.append_silence(
                waveform, params.append_silence,
                self._model_manager.sample_rate,
            )

        if params.normalize_output:
            # Use LUFS-based normalization for professional speech loudness
            waveform = self._audio_manager.normalize_to_lufs(waveform, target_lufs=-18.0)

        # --- Save ---
        output_path = self._audio_manager.save_wav(
            waveform, self._model_manager.sample_rate,
            filename=request.output_filename,
        )

        # --- Compute metadata ---
        realtime_factor = (
            generation_time / output_duration if output_duration > 0 else 0.0
        )

        final_status = self._model_manager.status()

        # P3.27B: attach the output-guard verdict + human-readable warning.
        result_warnings = list(prompt_data.warnings)
        if output_anomaly is not None:
            try:
                from engine.output_guard import anomaly_warning_text
                result_warnings.append(anomaly_warning_text(output_anomaly))
            except Exception:  # pragma: no cover - defensive
                pass

        result = GenerationResult(
            success=True,
            output_path=output_path,
            output_duration=output_duration,
            output_sample_rate=self._model_manager.sample_rate,
            generation_time=generation_time,
            realtime_factor=realtime_factor,
            parameters=params,
            voice_profile=voice_profile,
            prompt=prompt_data.final_prompt,
            timestamp=timestamp,
            warnings=result_warnings,
            gpu_name=final_status.gpu_name,
            vram_usage_mb=final_status.vram_used_mb,
            project=getattr(request, 'project', 'Default'),
            part_index=getattr(request, 'part_index', None),
            part_version=getattr(request, 'part_version', None),
            output_anomaly=output_anomaly,
            # P3.28: provenance passthrough (block + generation run) —
            # consumed by engine/audio_provenance.register_generation_result.
            block_id=getattr(request, 'block_id', None),
            generation_run=getattr(request, 'generation_run', None),
        )

        logger.info("Generation complete: %.2fs audio in %.2fs (RTF=%.2f)",
                    output_duration, generation_time, realtime_factor)
        return result

    # ------------------------------------------------------------------
    # Reference audio loading
    # ------------------------------------------------------------------
    def _load_reference_audio(self, voice_profile: VoiceProfile):
        """Load the reference audio tensor for voice cloning.

        Returns ``(reference_audio_tensor, sample_rate)``.

        Raises:
            ReferenceAudioMissing: if the reference audio path is empty,
                the file does not exist, or the file cannot be decoded.

        This method NEVER returns (None, 0). A failed reference audio
        load is a hard error: the caller (generate()) must propagate it
        so that the generation result is a clear failure rather than
        silent generation with the wrong voice character.
        """
        ref_path = voice_profile.reference_audio_path
        if not ref_path:
            raise ReferenceAudioMissing(
                "Voice '{0}' has no reference audio path set in its "
                "profile.".format(voice_profile.name),
                suggested_action=(
                    "Open the Voice Library, select '{0}', and import a "
                    "reference WAV file."
                ).format(voice_profile.name),
            )

        # P3.25 (path-consistency fix): resolve the reference audio against
        # the AUDIO MANAGER's app root (derived from the engine's app root
        # — outputs_dir's parent), NOT the module-relative directory. With
        # a non-default Engine(app_root=...) the module-relative join
        # pointed at the wrong tree and a valid reference looked "missing".
        # (Defensive: tools may construct GenerationManager without an
        # audio manager — fall back to the module-relative root then.)
        audio_mgr = getattr(self, "_audio_manager", None)
        if audio_mgr is not None and hasattr(audio_mgr, "_get_app_root"):
            app_root = audio_mgr._get_app_root()
        else:
            app_root = os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))
        full_path = os.path.join(app_root, ref_path)
        if not os.path.isfile(full_path):
            logger.error(
                "VOICE RESOLVE: reference audio file MISSING for voice_id=%s "
                "voice_name=%s expected_path=%s",
                voice_profile.id, voice_profile.name, full_path,
            )
            raise ReferenceAudioMissing(
                "Voice '{0}' reference audio file is missing from disk: "
                "{1}".format(voice_profile.name, ref_path),
                suggested_action=(
                    "Re-import the reference WAV for voice '{0}', or pick a "
                    "different voice profile."
                ).format(voice_profile.name),
            )

        logger.info(
            "VOICE RESOLVE: requested_voice_id=%s resolved_voice_id=%s "
            "resolved_voice_name=%s reference_audio=%s",
            voice_profile.id, voice_profile.id, voice_profile.name, ref_path,
        )

        # Preferred path: torchaudio (matches the Higgs Audio V3 model card).
        try:
            import torch
            import torchaudio
            ref, sr = torchaudio.load(full_path)
            # The model expects a 1-D tensor (mono). Downmix stereo if needed.
            if ref.dim() > 1 and ref.shape[0] > 1:
                ref = ref.mean(dim=0)
            else:
                ref = ref.squeeze(0)
            logger.info(
                "VOICE RESOLVE: loaded reference audio via torchaudio: "
                "sr=%d shape=%s", sr, tuple(ref.shape),
            )
            return ref, sr
        except ImportError:
            # torchaudio not available — fall back to AudioManager.
            logger.info(
                "VOICE RESOLVE: torchaudio unavailable, falling back to "
                "AudioManager for reference audio load.",
            )
            try:
                samples, sr = self._audio_manager.load_wav(full_path)
                import torch
                ref = torch.from_numpy(samples)
                logger.info(
                    "VOICE RESOLVE: loaded reference audio via AudioManager: "
                    "sr=%d shape=%s", sr, tuple(ref.shape),
                )
                return ref, sr
            except Exception as exc:
                logger.error(
                    "VOICE RESOLVE: AudioManager failed to load reference "
                    "audio for voice_id=%s voice_name=%s path=%s: %s",
                    voice_profile.id, voice_profile.name, full_path, exc,
                )
                raise ReferenceAudioMissing(
                    "Voice '{0}' reference audio could not be decoded: "
                    "{1}".format(voice_profile.name, exc),
                    suggested_action=(
                        "Re-import the reference WAV for voice '{0}'. The "
                        "file may be corrupt or in an unsupported format."
                    ).format(voice_profile.name),
                ) from exc
        except Exception as exc:
            # torchaudio.load() raised (corrupt file, unsupported codec, etc).
            logger.error(
                "VOICE RESOLVE: torchaudio failed to load reference audio "
                "for voice_id=%s voice_name=%s path=%s: %s",
                voice_profile.id, voice_profile.name, full_path, exc,
            )
            raise ReferenceAudioMissing(
                "Voice '{0}' reference audio could not be decoded: "
                "{1}".format(voice_profile.name, exc),
                suggested_action=(
                    "Re-import the reference WAV for voice '{0}'. The file "
                    "may be corrupt or in an unsupported format."
                ).format(voice_profile.name),
            ) from exc
