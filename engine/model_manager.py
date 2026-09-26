"""
SpeechStudio Engine - Model Manager
====================================

Engine Specification, section 5:
    "ModelManager responsibilities: Load tokenizer, Load model, Unload model,
     Warmup model, Report model status, Report CUDA information.
     The ModelManager owns the only model instance."

Generation Engine Specification, section 4 (Model Lifecycle):
    "The model shall be loaded once during application startup.
     The model shall remain in GPU memory until the application exits.
     The model shall never be reloaded between generations.
     The tokenizer shall be loaded once."

AI Development Rules, section 8:
    "Exactly one model instance exists. Exactly one tokenizer exists.
     The model is loaded once. The tokenizer is loaded once.
     The model remains resident until application shutdown."

Generation Engine Specification, section 2:
    "The Generation Engine is the only component allowed to communicate
     with the Transformers model."

Memory Ownership (Engine Specification section 20):
    "ModelManager owns: Model, Tokenizer."

Single responsibility: own and manage the model + tokenizer lifecycle.
Never builds prompts, never manages voices, never writes audio files.
"""

from __future__ import annotations
import os
import sys
import threading
from typing import Optional, Tuple

from engine.logger import get_logger
from engine.errors import (
    ModelNotLoaded, ModelLoadFailed, CudaUnavailable, TokenizerMissing,
)
from engine.models import ModelStatus
from engine.events import EventBus, EventType, Event

logger = get_logger("model_manager")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Generation Engine Specification section 5: supported backend.
MODEL_ID = "multimodalart/higgs-audio-v3-tts-4b-transformers"
DEFAULT_MODEL_DTYPE = "bfloat16"  # bf16 recommended per the Transformers port README
HIGGS_SAMPLE_RATE = 24000  # Output is mono 24 kHz (verified from model card).

# Maps the settings string to the torch dtype object.
_DTYPE_MAP = {
    "bfloat16": "torch.bfloat16",
    "float16": "torch.float16",
    "float32": "torch.float32",
}


class ModelManager:
    """Owns the single model and tokenizer instance.

    Enforces the single-instance rule: the model is loaded once and stays
    resident until unload() is called at shutdown. All Transformers imports
    are lazy so the engine can be imported before torch is installed.

    The model is loaded from a local directory (models/higgs-audio-v3/)
    that was populated by bootstrap.py. This avoids Windows symlink issues
    with the HuggingFace cache system.

    Thread safety: load() and unload() are guarded by a lock so concurrent
    calls from the UI thread and worker threads cannot corrupt state.

    P3.29 FIX (frozen "Loading Model" dialog, user report post-P3.28):
        The previous implementation held ONE RLock (``_lock``) for the
        ENTIRE load — ``import torch`` + ``from_pretrained`` + ``.to()``
        (10-60 s). ``load()`` emits ENGINE_STATUS_CHANGED right after
        ``_loading = True``; MainWindow marshals that event to the GUI
        thread, where ``_refresh_model_status()`` calls ``status()`` ->
        ``with self._lock:`` — so the GUI thread BLOCKED on the lock for
        the whole load: no timer ticks, no paints, no input. Windows
        marked the dialog "(Not Responding)" and it vanished the moment
        the load finished (exactly the reported symptom).

        The fix splits the lock scopes:
          * ``_state_lock`` (RLock) — guards ``_model`` / ``_tokenizer`` /
            ``_device`` / ``_loading`` / ``_phase``. Held only for
            microseconds; ``status()`` / ``is_loaded`` can never block.
          * ``_load_lock`` (Lock) — serializes whole load()/unload()/
            warmup() calls (the single-load invariant). Held for the
            entire load but NEVER acquired by status readers.

        Additionally ``status()`` NEVER imports torch any more: during
        the worker's ``import torch`` the per-module import lock would
        block any other thread importing torch (the GUI thread called
        ``status()`` -> ``import torch`` mid-import) for the whole
        import. Only an ALREADY-imported torch (sys.modules) is queried,
        and the CUDA query block is fully exception-guarded.
    """

    def __init__(self, event_bus: EventBus, model_path: str,
                 settings_manager=None):
        """Initialise the ModelManager.

        Args:
            event_bus: the engine event bus for status notifications.
            model_path: path to the local directory containing the model
                        files (populated by bootstrap.py).
            settings_manager: optional SettingsManager for model_precision.
        """
        self._event_bus = event_bus
        self._model_path = model_path
        self._settings = settings_manager
        # P3.29: two lock scopes — see the class docstring.
        self._state_lock = threading.RLock()
        self._load_lock = threading.Lock()

        # The single model and tokenizer instances (None until loaded).
        self._model = None
        self._tokenizer = None
        self._device = None       # "cuda" or "cpu"
        self._loading = False
        # P3.29: human-readable load phase ("Loading tokenizer..." etc.),
        # published on ModelStatus.phase so the load dialog can show it.
        self._phase = ""

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load(self, use_cuda: bool = True) -> ModelStatus:
        """Load the model and tokenizer into memory.

        This is a potentially long operation (10-60 seconds depending on
        hardware). It should be called on a worker thread, not the UI
        thread (AI Development Rules section 11).

        P3.29: the long work runs OUTSIDE the state lock (see the class
        docstring) so ``status()`` on the GUI thread stays non-blocking;
        whole-load operations are serialized by ``_load_lock``.

        Args:
            use_cuda: if True, prefer CUDA; fall back to CPU if unavailable.

        Returns:
            The current ModelStatus after loading.
        """
        with self._load_lock:
            with self._state_lock:
                if self._model is not None and self._tokenizer is not None:
                    logger.info("Model already loaded; skipping.")
                    return self.status()
                if self._loading:
                    # Unreachable while _load_lock serializes loads; kept
                    # as defense against future re-entrancy.
                    raise ModelLoadFailed(
                        "A model load is already in progress.",
                        "Wait for the current model load to finish.",
                    )

            # Verify the model path exists (immutable path — no lock).
            if not os.path.isdir(self._model_path):
                with self._state_lock:
                    self._loading = False
                raise ModelLoadFailed(
                    "Model directory not found: {0}. Run launch.bat to download the model.".format(
                        self._model_path),
                    "Run launch.bat so bootstrap can download the model.",
                )

            config_file = os.path.join(self._model_path, "config.json")
            if not os.path.isfile(config_file):
                with self._state_lock:
                    self._loading = False
                raise ModelLoadFailed(
                    "config.json not found in {0}. The model download may be incomplete.".format(
                        self._model_path),
                    "Re-run launch.bat to complete the model download.",
                )

            with self._state_lock:
                self._loading = True
                self._phase = "Preparing..."
            self._emit_status_changed()

            try:
                # --- Lazy imports ---
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer

                # --- CUDA check ---
                if use_cuda and torch.cuda.is_available():
                    device = "cuda"
                    gpu_name = torch.cuda.get_device_name(0)
                    logger.info("CUDA available. GPU: %s", gpu_name)
                elif use_cuda:
                    logger.warning("CUDA requested but unavailable; falling back to CPU.")
                    device = "cpu"
                else:
                    device = "cpu"
                with self._state_lock:
                    self._device = device

                # --- Load tokenizer ---
                # Load from the local directory (populated by bootstrap).
                self._set_phase("Loading tokenizer...")
                logger.info("Loading tokenizer from %s ...", self._model_path)
                tokenizer = AutoTokenizer.from_pretrained(
                    self._model_path,
                    trust_remote_code=True,
                )
                with self._state_lock:
                    self._tokenizer = tokenizer
                logger.info("Tokenizer loaded.")

                # --- Load model ---
                # Per the Transformers port README:
                #   model = AutoModelForCausalLM.from_pretrained(
                #       repo, trust_remote_code=True, dtype=torch.bfloat16
                #   ).to("cuda").eval()
                #
                # Note: transformers 5.x accepts both 'dtype' and 'torch_dtype'.
                # We try 'torch_dtype' first (more widely compatible) and fall
                # back to 'dtype'.
                precision_str = self._get_precision_setting()
                dtype = self._resolve_dtype(torch, precision_str)

                self._set_phase("Loading model weights...")
                logger.info("Loading model from %s (precision=%s, device=%s) ...",
                            self._model_path, precision_str, device)

                try:
                    logger.info("Calling from_pretrained(dtype=%s) ...", precision_str)
                    model = AutoModelForCausalLM.from_pretrained(
                        self._model_path,
                        trust_remote_code=True,
                        dtype=dtype,
                    )
                    logger.info("from_pretrained returned successfully.")
                except TypeError:
                    logger.info("dtype not accepted; trying torch_dtype parameter...")
                    model = AutoModelForCausalLM.from_pretrained(
                        self._model_path,
                        trust_remote_code=True,
                        torch_dtype=dtype,
                    )
                    logger.info("from_pretrained (torch_dtype) returned successfully.")
                except Exception as exc:
                    logger.exception("from_pretrained FAILED: %s", exc)
                    raise

                self._set_phase("Moving model to {0}...".format(
                    device.upper() if device else "device"))
                model = model.to(device)
                model.eval()
                with self._state_lock:
                    self._model = model
                    self._loading = False
                    self._phase = ""
                logger.info("Model loaded (dtype=%s) and moved to %s.",
                            precision_str, device)

                if self._event_bus is not None:
                    self._event_bus.emit(Event(EventType.MODEL_LOADED,
                                               self.status()))
                return self.status()

            except Exception as exc:
                with self._state_lock:
                    self._model = None
                    self._tokenizer = None
                    self._loading = False
                    self._phase = ""
                logger.exception("Model load failed: %s", exc)
                # Clean up partial state (done above under the state lock).
                raise ModelLoadFailed(
                    "Failed to load the model: {0}".format(exc),
                ) from exc

    def warmup(self) -> None:
        """Run a minimal generation to warm up the model (CUDA kernels).

        Per System Specification section 4 startup sequence: "Model Warmup".
        This should be called after load() and before the first real
        generation. It is optional but recommended for consistent timing.

        P3.29: the (potentially long) generate_speech call runs OUTSIDE the
        state lock — only serialized against load()/unload() via
        ``_load_lock`` so ``status()`` readers can never block on it.
        """
        with self._load_lock:
            with self._state_lock:
                if self._model is None or self._tokenizer is None:
                    raise ModelNotLoaded("Cannot warmup: model is not loaded.")

            logger.info("Warming up model with a short test generation ...")
            try:
                model, tokenizer = self.get_model_and_tokenizer()
                # Run a very short generation to initialise CUDA kernels.
                _ = model.generate_speech(
                    "Warmup.",
                    tokenizer,
                    max_new_tokens=128,
                )
                logger.info("Model warmup complete.")
            except ModelNotLoaded:
                raise
            except Exception as exc:
                logger.warning("model_manager: Warmup failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Unloading
    # ------------------------------------------------------------------
    def unload(self) -> None:
        """Release the model and tokenizer from memory.

        Called during application shutdown (System Specification section 19).
        """
        with self._load_lock:
            with self._state_lock:
                if self._model is None and self._tokenizer is None:
                    return
                logger.info("Unloading model and tokenizer ...")
                self._model = None
                self._tokenizer = None
                self._device = None
                self._phase = ""

            # Attempt to free GPU memory. P3.29: only use an
            # ALREADY-imported torch (never import it here — shutdown may
            # race a load in another thread).
            try:
                torch = sys.modules.get("torch")
                if torch is not None and torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

            if self._event_bus is not None:
                self._event_bus.emit(Event(EventType.MODEL_UNLOADED))
            logger.info("Model and tokenizer unloaded.")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def status(self) -> ModelStatus:
        """Return a snapshot of the current model/tokenizer/CUDA state.

        P3.29 FIX: this method is called from the GUI thread (status bar
        refresh + the load dialog's poll timer) WHILE the worker thread
        is inside ``import torch`` / ``from_pretrained``. Two hazards were
        removed:
          1. It used to run under the SAME lock ``load()`` held for the
             whole load → the GUI thread blocked for 10-60 s (the frozen
             "Loading Model" dialog / "(Not Responding)"). Now only the
             microsecond state snapshot takes ``_state_lock``.
          2. It used to ``import torch`` — if the worker was mid-import,
             the per-module import lock blocked the GUI thread for the
             whole import. Now only an ALREADY-imported torch
             (``sys.modules``) is queried, fully exception-guarded (a
             partially-initialised module during the worker's import is
             an expected transient state).
        """
        with self._state_lock:
            precision = self._get_precision_setting()
            device = self._device
            status = ModelStatus(
                model_loaded=self._model is not None,
                tokenizer_loaded=self._tokenizer is not None,
                loading=self._loading,
                model_id=MODEL_ID,
                dtype=precision,
                phase=self._phase,
            )

        # NEVER import torch here (see docstring).
        torch = sys.modules.get("torch")
        if torch is not None:
            try:
                status.cuda_available = bool(torch.cuda.is_available())
                if status.cuda_available:
                    status.gpu_name = torch.cuda.get_device_name(0)
                    status.gpu_count = torch.cuda.device_count()
                    props = torch.cuda.get_device_properties(0)
                    status.vram_total_mb = props.total_memory / (1024 * 1024)
                    if device == "cuda":
                        status.vram_used_mb = (
                            torch.cuda.memory_allocated(0) / (1024 * 1024)
                        )
            except Exception:
                # Partially-initialised torch (worker mid-import) or CUDA
                # query failure — report CUDA unavailable, never raise.
                status.cuda_available = False
        return status

    # ------------------------------------------------------------------
    # Load-phase publication (P3.29)
    # ------------------------------------------------------------------
    def _set_phase(self, text: str) -> None:
        """Update the human-readable load phase and notify subscribers.

        Called on the load worker thread at each phase transition; the
        emitted status carries ``phase`` so UI surfaces (the load dialog)
        can show what the loader is currently doing.
        """
        with self._state_lock:
            self._phase = text
        self._emit_status_changed()

    def _emit_status_changed(self) -> None:
        if self._event_bus is not None:
            self._event_bus.emit(Event(EventType.ENGINE_STATUS_CHANGED,
                                       self.status()))

    # ------------------------------------------------------------------
    # Access (read-only - used by GenerationManager)
    # ------------------------------------------------------------------
    def get_model_and_tokenizer(self) -> Tuple:
        """Return (model, tokenizer), raising if not loaded.

        Only the GenerationManager should call this - it is the only other
        component that needs to touch the model, and it only calls
        generate_speech(), never modifying model state.
        """
        with self._state_lock:
            if self._model is None or self._tokenizer is None:
                raise ModelNotLoaded(
                    "The model is not loaded yet.",
                    "Wait for the model to finish loading, then try again.",
                )
            return self._model, self._tokenizer

    @property
    def is_loaded(self) -> bool:
        with self._state_lock:
            return self._model is not None and self._tokenizer is not None

    @property
    def device(self) -> Optional[str]:
        with self._state_lock:
            return self._device

    @property
    def sample_rate(self) -> int:
        """Return the model's output sample rate (24 kHz for Higgs V3)."""
        return HIGGS_SAMPLE_RATE

    # ------------------------------------------------------------------
    # Precision helpers
    # ------------------------------------------------------------------
    def _get_precision_setting(self) -> str:
        """Read the model_precision setting from SettingsManager."""
        if self._settings is None:
            return DEFAULT_MODEL_DTYPE
        try:
            precision = self._settings.get("performance", "model_precision",
                                           DEFAULT_MODEL_DTYPE)
            if precision not in _DTYPE_MAP:
                logger.warning("Unknown model_precision '%s', using default '%s'",
                               precision, DEFAULT_MODEL_DTYPE)
                return DEFAULT_MODEL_DTYPE
            return precision
        except Exception:
            return DEFAULT_MODEL_DTYPE

    @staticmethod
    def _resolve_dtype(torch_module, precision_str: str):
        """Map a precision string to the corresponding torch dtype object."""
        if precision_str == "float16":
            return torch_module.float16
        elif precision_str == "float32":
            return torch_module.float32
        else:
            return torch_module.bfloat16
