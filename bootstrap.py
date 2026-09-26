#!/usr/bin/env python3
"""
SpeechStudio Bootstrap
======================

Responsible for preparing the runtime environment before the graphical
application starts.

Startup sequence (System Specification, section 4):

    launch.bat
        |
        v
    Bootstrap
        |
        v
    Environment Validation
        |
        v
    Virtual Environment
        |
        v
    Dependency Validation
        |
        v
    Folder Validation
        |
        v
    Hugging Face Cache Validation
        |
        v
    Model Validation
        |
        v
    [Model Warmup  ->  implemented in Phase 2]
        |
        v
    SpeechStudio UI

Bootstrap responsibilities (System Specification, section 7):
    - Create virtual environment if missing
    - Install missing dependencies
    - Create required folders
    - Configure Hugging Face cache
    - Verify CUDA
    - Verify Python version
    - Download tokenizer if required
    - Download model if required
    - Validate downloaded files
    - Start SpeechStudio

Bootstrap shall never generate speech.

PHASE 1 BOUNDARY (Decision 4):
    Bootstrap downloads and validates the model and tokenizer FILES only.
    It does NOT load the model into memory, does NOT instantiate the
    tokenizer, and does NOT call generate_speech(). Model loading is the
    responsibility of the Engine (ModelManager) and begins in Phase 2
    (Sprint 02 - Core Engine). The only `import torch` calls in Phase 1 are
    for environment detection (version, cuda.is_available,
    get_device_name) - never for model instantiation.

Portability rules (System Specification, section 5, AI Development Rules
section 6/7):
    - Every path is relative to the application root.
    - No absolute paths, no AppData, no Registry, no user profile.
    - The Hugging Face cache lives inside .cache/huggingface/.
    - The virtual environment lives inside venv/.

This module uses ONLY the Python standard library so that it can run on a
bare system Python before the virtual environment exists. Hugging Face Hub
is imported lazily, only after the virtual environment has been created and
the script has been re-launched inside it.
"""

import os
import sys
import subprocess
import platform
import time
import logging
import traceback

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------
# Every path is resolved relative to this file so that the application is
# fully portable. No absolute paths are ever used.

# P3.37 rebrand: user-facing product name (internal identifiers unchanged)
APP_NAME = "GUINEO"
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(APP_ROOT, "venv")
CACHE_DIR = os.path.join(APP_ROOT, ".cache", "huggingface")
HUB_CACHE_DIR = os.path.join(CACHE_DIR, "hub")
LOGS_DIR = os.path.join(APP_ROOT, "logs")
MODELS_DIR = os.path.join(APP_ROOT, "models")
REQUIREMENTS_FILE = os.path.join(APP_ROOT, "requirements.txt")
APP_ENTRY = os.path.join(APP_ROOT, "SpeechStudio.py")

# The model is downloaded to a local directory (not the HF cache) to avoid
# Windows symlink issues. The HF cache uses symlinks to deduplicate blobs,
# which requires Developer Mode or admin privileges on Windows. Using
# local_dir downloads files as regular copies - works everywhere.
MODEL_LOCAL_DIR = os.path.join(MODELS_DIR, "higgs-audio-v3")

# Model identifier (Generation Engine Specification, section 5;
# REFERENCES.md, section 4).
# Current backend: Transformers port of BosonAI Higgs Audio V3 TTS 4B.
MODEL_ID = "multimodalart/higgs-audio-v3-tts-4b-transformers"
MODEL_REVISION = "main"

# Python version policy (AI Development Rules section 3, System Specification
# section 21). Python 3.11 is the required runtime. The bootstrap refuses to
# proceed on any other version so that the application always runs in the
# tested, approved environment.
REQUIRED_PYTHON = (3, 11)

# --- PyTorch CUDA wheel index ---------------------------------------------
# torch is intentionally absent from requirements.txt. The bootstrap installs
# the CUDA-enabled build separately so the correct CUDA runtime is bundled on
# Windows (Decision 3: use the official PyTorch CUDA wheel index when CUDA is
# available).
#
# PyTorch publishes one wheel index per CUDA variant. We map the driver's
# maximum supported CUDA version (reported by nvidia-smi) to the highest
# compatible PyTorch CUDA wheel index. The list is ordered newest-first so the
# first match wins.
#
# Source: https://pytorch.org/get-started/locally/
# A wheel tagged cuXYZ runs on any driver that supports CUDA X.YZ or newer.
PYTORCH_CUDA_INDICES = [
    # (driver CUDA version threshold, PyTorch wheel index name, human label)
    (12, 8, "cu128", "CUDA 12.8"),
    (12, 6, "cu126", "CUDA 12.6"),
    (12, 4, "cu124", "CUDA 12.4"),
    (12, 1, "cu121", "CUDA 12.1"),
    (11, 8, "cu118", "CUDA 11.8"),
]
PYTORCH_INDEX_URL_BASE = "https://download.pytorch.org/whl/"
# Minimum torch version that ships the Higgs Audio V3 compatible CUDA kernels
# and supports Python 3.11 on Windows. 2.7+ is verified against the cu128
# index. We let pip pick the newest compatible patch release.
TORCH_MIN_VERSION = "2.7.0"

# Folders that must exist inside the project directory
# (System Specification, section 6).
REQUIRED_FOLDERS = [
    ".cache/huggingface",
    "logs",
    "models",
    "outputs",
    "presets",
    "settings",
    "temp",
    "voices",
    "engine",
    "ui",
    "spec",
    "research",
]

# Files that must be present once the model and tokenizer have been
# downloaded (verified against the model repository file listing).
TOKENIZER_FILES = [
    "tokenizer.json",
    "tokenizer_config.json",
]
MODEL_CONFIG_FILES = [
    "config.json",
    "model.safetensors.index.json",
    "configuration_higgs_multimodal_qwen3.py",
    "modeling_higgs_multimodal_qwen3.py",
    "chat_template.jinja",
]
# Tokenizer + config + custom code are small and downloaded first.
SMALL_FILE_PATTERNS = [
    "tokenizer*",
    "config.json",
    "model.safetensors.index.json",
    "configuration_*.py",
    "modeling_*.py",
    "chat_template.jinja",
    "LICENSE",
    "README.md",
]
# The large model weight file(s) are downloaded separately.
# "model*.safetensors" matches:
#   - model.safetensors            (single-file models, like Higgs Audio V3)
#   - model-00001-of-00003.safetensors  (sharded models)
# It does NOT match model.safetensors.index.json (the index is a small file
# downloaded together with the tokenizer/config above).
LARGE_FILE_PATTERNS = [
    "model*.safetensors",
]

# ---------------------------------------------------------------------------
# Local Hugging Face cache configuration
# ---------------------------------------------------------------------------
# This MUST happen before huggingface_hub is imported anywhere.
# Per System Specification section 13 the cache resides inside
# .cache/huggingface/ and the application shall never rely on the default
# user cache location. We set the variables unconditionally so that even if
# the host has HF_HOME set, our local cache wins.
os.environ["HF_HOME"] = CACHE_DIR
os.environ["HF_HUB_CACHE"] = HUB_CACHE_DIR
# Disable Hugging Face telemetry for offline portability and privacy.
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
# Do not inject HF token lookup; SpeechStudio only uses public models.
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
# CRITICAL: Disable the hf-xet download backend.
# huggingface_hub >= 0.27 ships with hf-xet, a proprietary download protocol
# that uses content-addressable storage instead of plain HTTPS. It is known
# to fail on many Windows configurations, behind corporate firewalls, and
# with certain DNS/proxy setups. Forcing regular HTTPS download makes the
# download work reliably everywhere. The trade-off (no deduplication) is
# irrelevant for SpeechStudio which downloads exactly one model.
os.environ["HF_HUB_DISABLE_XET"] = "1"
# Suppress the symlink warning on Windows. We use local_dir (not cache_dir)
# for downloads, so symlinks are not created. This warning is harmless but
# confusing to users.
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logger():
    """Create a logger that writes to logs/bootstrap.log and to stdout.

    Uses only the standard library so it works on a bare system Python.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_file = os.path.join(LOGS_DIR, "bootstrap.log")

    logger = logging.getLogger("speechstudio.bootstrap")
    logger.setLevel(logging.DEBUG)
    # Avoid duplicate handlers if bootstrap re-enters (it should not, but be safe).
    if not logger.handlers:
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                               datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(
            logging.Formatter("[%(levelname)s] %(message)s")
        )
        logger.addHandler(console_handler)
    return logger


def log_banner(logger):
    """Write a clearly delimited header so each bootstrap run is traceable."""
    logger.info("=" * 60)
    logger.info("%s Bootstrap starting", APP_NAME)
    logger.info("Application root : %s", APP_ROOT)
    logger.info("Platform         : %s", platform.platform())
    logger.info("Python executable: %s", sys.executable)
    logger.info("Python version   : %s", platform.python_version())
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Stage 1: Environment validation
# ---------------------------------------------------------------------------
def verify_python_version(logger):
    """Verify that the Python interpreter is exactly the required version.

    Python 3.11 is the approved runtime (AI Development Rules section 3,
    System Specification section 21). The bootstrap refuses to proceed on
    any other version so the application always runs in the tested
    environment.
    """
    current = sys.version_info[:2]
    if current != REQUIRED_PYTHON:
        raise RuntimeError(
            "GUINEO requires Python {0}.{1} exactly, but the current "
            "interpreter is Python {2}.{3}.\n"
            "Please install Python {0}.{1} from https://www.python.org/ and "
            "make sure it is the first 'python' or 'py' on your PATH, then "
            "run launch.bat again.".format(
                REQUIRED_PYTHON[0], REQUIRED_PYTHON[1],
                current[0], current[1],
            )
        )
    logger.info("Python version OK (%s.%s).", current[0], current[1])


# ---------------------------------------------------------------------------
# Stage 2: Virtual environment
# ---------------------------------------------------------------------------
def is_windows():
    return platform.system() == "Windows"


def get_venv_python():
    """Return the path to the Python interpreter inside the project venv."""
    if is_windows():
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def venv_exists():
    """Return True if the virtual environment looks complete."""
    return os.path.isfile(get_venv_python())


def running_in_venv():
    """Return True if the current interpreter is the venv interpreter."""
    return os.path.normcase(os.path.abspath(sys.executable)) == \
           os.path.normcase(os.path.abspath(get_venv_python()))


def ensure_virtualenv(logger):
    """Create the project local virtual environment if it is missing."""
    if venv_exists():
        logger.info("Virtual environment found: %s", VENV_DIR)
        return
    logger.info("Creating virtual environment at: %s", VENV_DIR)
    start = time.time()
    subprocess.check_call(
        [sys.executable, "-m", "venv", "--clear", VENV_DIR]
    )
    elapsed = time.time() - start
    logger.info("Virtual environment created in %.1f s.", elapsed)


# ---------------------------------------------------------------------------
# Stage 3: Dependency validation
# ---------------------------------------------------------------------------
def _detect_driver_cuda_version(logger):
    """Return the maximum CUDA version supported by the installed NVIDIA driver.

    Uses nvidia-smi (shipped with every NVIDIA driver). Returns None if no
    NVIDIA GPU or driver is present. The returned tuple is (major, minor), for
    example (12, 8) for a driver that supports CUDA 12.8.

    The CUDA version is parsed from the nvidia-smi header line which looks like:
        NVIDIA-SMI 545.84                  Driver Version: 545.84   CUDA Version: 12.3
    """
    nvidia_smi = "nvidia-smi"
    try:
        # Run nvidia-smi without query flags - the header contains the CUDA version.
        proc = subprocess.run(
            [nvidia_smi],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None

        output = proc.stdout

        # Parse the driver version from the header.
        import re
        driver_match = re.search(r"Driver Version:\s*(\d+\.\d+)", output)
        if driver_match:
            driver_version = driver_match.group(1)
            logger.info("NVIDIA driver version: %s", driver_version)

        # Parse the CUDA version from the header.
        cuda_match = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", output)
        if cuda_match:
            major = int(cuda_match.group(1))
            minor = int(cuda_match.group(2))
            logger.info("Driver supports CUDA %s.%s", major, minor)
            return (major, minor)

        # If we can't parse the CUDA version, fall back to the driver version
        # mapping. This shouldn't happen with a working nvidia-smi, but it's
        # a safety net.
        if driver_match:
            driver_major = int(driver_version.split(".")[0])
            # Approximate mapping: driver 570+ = CUDA 12.8, 545+ = CUDA 12.3
            if driver_major >= 570:
                return (12, 8)
            elif driver_major >= 545:
                return (12, 3)
            elif driver_major >= 535:
                return (12, 2)
            else:
                return (11, 8)

        return None

    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("nvidia-smi query failed: %s", exc)
        return None


def _select_pytorch_cuda_index(driver_cuda, logger):
    """Pick the highest PyTorch CUDA wheel index the driver can run.

    PyTorch cuXYZ wheels run on any driver that supports CUDA X.YZ or newer, so
    we walk the list newest-first and return the first whose threshold the
    driver meets. If the driver is too old for every index, we fall back to the
    oldest supported one (cu118) with a warning - the user can still try it.
    """
    if driver_cuda is None:
        logger.info("No NVIDIA driver detected; torch will be installed from PyPI (CPU build).")
        return None  # CPU build

    for major, minor, index_name, label in PYTORCH_CUDA_INDICES:
        # Driver supports this CUDA version if driver >= threshold.
        if (driver_cuda[0], driver_cuda[1]) >= (major, minor) or \
           driver_cuda[0] > major:
            logger.info("Driver supports %s; selecting PyTorch %s wheels.", label, index_name)
            return index_name

    # Driver is older than cu118. Warn and use cu118 as the safest fallback.
    logger.warning(
        "NVIDIA driver supports CUDA %s.%s, which is older than the newest "
        "PyTorch CUDA wheel. Falling back to cu118; upgrade the driver for "
        "better performance if possible.",
        driver_cuda[0], driver_cuda[1],
    )
    return "cu118"


def _is_torch_installed():
    """Return True if torch is importable inside the venv."""
    venv_python = get_venv_python()
    try:
        result = subprocess.run(
            [venv_python, "-c", "import torch; print(torch.__version__)"],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _torch_has_cuda():
    """Return True if the installed torch has CUDA support."""
    venv_python = get_venv_python()
    try:
        result = subprocess.run(
            [venv_python, "-c",
             "import torch; print('CUDA' if torch.cuda.is_available() else 'CPU')"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip() == "CUDA"
    except Exception:  # noqa: BLE001
        pass
    return False


def ensure_torch(logger):
    """Install the CUDA-enabled PyTorch build into the venv.

    Decision 3: on Windows, when an NVIDIA GPU is present, install torch from
    the official PyTorch CUDA wheel index so the correct CUDA runtime is
    bundled. When no NVIDIA GPU is present, install the CPU build from PyPI
    (the application will still start; speech generation is blocked until a
    CUDA device is available - Generation Engine Specification section 17).

    If torch is already installed but without CUDA AND an NVIDIA driver is
    detected, the CPU version is uninstalled and replaced with the CUDA build.
    This handles the case where a previous run installed CPU torch because
    the driver detection failed.

    torch is intentionally absent from requirements.txt because pip cannot
    express "use a different index URL for one package". This function handles
    it explicitly.
    """
    venv_python = get_venv_python()
    driver_cuda = _detect_driver_cuda_version(logger)
    index_name = _select_pytorch_cuda_index(driver_cuda, logger)

    # Check if torch is already installed.
    if _is_torch_installed():
        if index_name is None:
            # No NVIDIA driver - CPU torch is fine.
            logger.info("PyTorch (CPU) already installed; no NVIDIA driver detected.")
            return

        # NVIDIA driver detected. Check if the installed torch has CUDA.
        if _torch_has_cuda():
            logger.info("PyTorch with CUDA already installed.")
            return

        # Torch is installed but WITHOUT CUDA, and we have an NVIDIA driver.
        # This happens when a previous run failed to detect the driver.
        # Reinstall with the CUDA build.
        logger.info("PyTorch is installed but without CUDA support.")
        logger.info("An NVIDIA driver was detected (CUDA %s.%s). Reinstalling with CUDA...",
                    driver_cuda[0], driver_cuda[1])
        try:
            subprocess.check_call(
                [venv_python, "-m", "pip", "uninstall", "-y", "torch", "torchaudio"],
                stdout=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            pass  # uninstall failure is non-fatal; the install will overwrite
        # Fall through to install the CUDA version.
    elif index_name is None:
        # No torch installed, no NVIDIA driver - install CPU build.
        logger.info("Installing PyTorch + torchaudio (CPU build) from PyPI...")
        cmd = [venv_python, "-m", "pip", "install",
               "torch>={}".format(TORCH_MIN_VERSION),
               "torchaudio"]
        start = time.time()
        subprocess.check_call(cmd)
        elapsed = time.time() - start
        logger.info("PyTorch + torchaudio installed in %.1f s.", elapsed)
        return

    # Install the CUDA build of torch AND torchaudio (same index).
    # torchaudio is required by the Higgs Audio V3 model's custom code
    # (modeling_higgs_multimodal_qwen3.py:250 does `import torchaudio`
    # inside _encode_reference). Without it, the model cannot be loaded.
    index_url = PYTORCH_INDEX_URL_BASE + index_name
    logger.info("Installing PyTorch + torchaudio from %s ...", index_url)
    cmd = [venv_python, "-m", "pip", "install",
           "torch>={}".format(TORCH_MIN_VERSION),
           "torchaudio",
           "--index-url", index_url]

    start = time.time()
    subprocess.check_call(cmd)
    elapsed = time.time() - start
    logger.info("PyTorch + torchaudio (CUDA %s) installed in %.1f s.", index_name, elapsed)


def ensure_dependencies(logger):
    """Install missing dependencies from requirements.txt into the venv.

    pip skips already-satisfied packages, so only missing packages are
    actually installed (System Specification section 14).

    Note: torch is installed separately by ensure_torch() because it needs a
    custom CUDA wheel index on Windows. requirements.txt deliberately omits
    torch.
    """
    if not os.path.isfile(REQUIREMENTS_FILE):
        raise RuntimeError("requirements.txt not found: " + REQUIREMENTS_FILE)

    venv_python = get_venv_python()

    # Upgrade pip quietly so that dependency resolution is reliable.
    logger.info("Upgrading pip inside the virtual environment...")
    subprocess.check_call(
        [venv_python, "-m", "pip", "install", "--upgrade", "pip"],
        stdout=subprocess.DEVNULL,
    )

    # Install the CUDA-enabled PyTorch build first (Decision 3).
    ensure_torch(logger)

    logger.info("Validating dependencies against requirements.txt...")
    start = time.time()
    subprocess.check_call(
        [venv_python, "-m", "pip", "install", "-r", REQUIREMENTS_FILE]
    )
    elapsed = time.time() - start
    logger.info("Dependencies ready (%.1f s).", elapsed)


# ---------------------------------------------------------------------------
# Stage 4: Folder validation
# ---------------------------------------------------------------------------
def ensure_folders(logger):
    """Create every required project folder if it does not exist."""
    logger.info("Validating project folder structure...")
    for folder in REQUIRED_FOLDERS:
        path = os.path.join(APP_ROOT, *folder.split("/"))
        os.makedirs(path, exist_ok=True)
        # Keep empty folders present in the project tree.
        gitkeep = os.path.join(path, ".gitkeep")
        if not os.listdir(path) and not os.path.exists(gitkeep):
            open(gitkeep, "w").close()
    logger.info("Folder structure ready (%d folders).", len(REQUIRED_FOLDERS))


# ---------------------------------------------------------------------------
# Re-launch inside the virtual environment
# ---------------------------------------------------------------------------
def relaunch_in_venv(logger):
    """Re-run bootstrap.py using the venv Python.

    The environment (including the local HF cache configuration) is inherited
    so that huggingface_hub sees HF_HOME pointing at the project cache.
    """
    venv_python = get_venv_python()
    logger.info("Re-launching bootstrap inside the virtual environment...")
    env = dict(os.environ)
    # Mark that this is a re-entry, for clarity in logs.
    env["SPEECHSTUDIO_BOOTSTRAP_STAGE"] = "2"
    result = subprocess.call([venv_python, os.path.abspath(__file__)], env=env)
    if result != 0:
        raise RuntimeError("Bootstrap failed inside the virtual environment.")
    logger.info("Bootstrap stage 2 completed successfully.")


# ---------------------------------------------------------------------------
# Stage 5: Hugging Face cache validation
# ---------------------------------------------------------------------------
def verify_hf_cache(logger):
    """Confirm that the local Hugging Face cache is configured."""
    hf_home = os.environ.get("HF_HOME", "")
    hub_cache = os.environ.get("HF_HUB_CACHE", "")
    if not hf_home or not hub_cache:
        raise RuntimeError("Hugging Face cache environment is not configured.")
    if not hf_home.startswith(APP_ROOT):
        raise RuntimeError(
            "HF_HOME is outside the project directory: " + hf_home
        )
    os.makedirs(hub_cache, exist_ok=True)
    logger.info("Hugging Face cache: %s", hf_home)
    logger.info("Hub cache directory: %s", hub_cache)


# ---------------------------------------------------------------------------
# Stage 6: CUDA verification
# ---------------------------------------------------------------------------
def verify_cuda(logger):
    """Detect CUDA availability and log the result.

    CUDA is preferred (Generation Engine Specification section 17). If it is
    unavailable the application still starts; generation is simply blocked
    until a CUDA device is present (handled in Phase 6).
    """
    try:
        import torch  # noqa: WPS433 - lazy import, only available in venv
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("PyTorch could not be imported: %s", exc)
        logger.warning("Skipping CUDA verification.")
        return False

    if not torch.cuda.is_available():
        logger.warning("CUDA is NOT available on this machine.")
        logger.warning("GPU acceleration is disabled. A CUDA GPU is required for speech generation.")
        return False

    try:
        gpu_name = torch.cuda.get_device_name(0)
        device_count = torch.cuda.device_count()
        logger.info("CUDA available. GPU: %s (devices: %d).", gpu_name, device_count)
        return True
    except Exception as exc:
        logger.warning("CUDA reported available but querying the device failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Stage 7: Model and tokenizer validation / download
# ---------------------------------------------------------------------------
def _is_file_cached(filename):
    """Return True if a specific file is present in the local model directory.

    We use local_dir (not the HF cache) for downloads to avoid Windows
    symlink issues. Files are stored as regular files in MODEL_LOCAL_DIR.
    """
    return os.path.isfile(os.path.join(MODEL_LOCAL_DIR, filename))


def _weight_shard_filenames():
    """Return the list of weight shard filenames declared by the index.

    Reads model.safetensors.index.json from MODEL_LOCAL_DIR (it must already
    be downloaded as part of the tokenizer/config bundle). Falls back to
    ["model.safetensors"] if no index is present (non-sharded models without
    an index).
    """
    import json  # standard library

    index_path = os.path.join(MODEL_LOCAL_DIR, "model.safetensors.index.json")
    if not os.path.isfile(index_path):
        # No index; assume a single non-sharded weight file.
        return ["model.safetensors"]

    try:
        with open(index_path, "r", encoding="utf-8") as handle:
            index = json.load(handle)
        shards = sorted(set(index.get("weight_map", {}).values()))
        return shards if shards else ["model.safetensors"]
    except Exception:
        return ["model.safetensors"]


def _is_tokenizer_cached():
    """Return True if every tokenizer and config file is cached."""
    required = TOKENIZER_FILES + MODEL_CONFIG_FILES
    return all(_is_file_cached(f) for f in required)


def _is_model_cached():
    """Return True if every model weight shard is cached."""
    shards = _weight_shard_filenames()
    return all(_is_file_cached(s) for s in shards)


def _get_file_size(filename):
    """Return the size of a file in the model repo, in bytes.

    Returns 0 if the size cannot be determined. Uses the HuggingFace API
    to fetch file metadata.
    """
    from huggingface_hub import HfApi  # noqa: WPS433
    try:
        api = HfApi()
        info = api.get_paths_info(
            repo_id=MODEL_ID,
            paths=[filename],
            revision=MODEL_REVISION,
            repo_type="model",
        )
        if info and hasattr(info[0], "size"):
            return info[0].size
    except Exception:
        pass
    return 0


def _format_size(num_bytes):
    """Format a byte count as a human-readable string."""
    if num_bytes < 1024:
        return "{0} B".format(num_bytes)
    elif num_bytes < 1024 * 1024:
        return "{0:.1f} KB".format(num_bytes / 1024)
    elif num_bytes < 1024 * 1024 * 1024:
        return "{0:.1f} MB".format(num_bytes / (1024 * 1024))
    else:
        return "{0:.2f} GB".format(num_bytes / (1024 * 1024 * 1024))


def _download_single_file(filename, logger, max_retries=5):
    """Download a single file with retry logic.

    Uses hf_hub_download with local_dir, which downloads files as regular
    copies (no symlinks). This works on Windows without Developer Mode.

    The download is resumable: if it is interrupted, re-running launch.bat
    will resume from where it left off (the .incomplete file is kept).

    Args:
        filename: the file name in the model repo.
        logger: the bootstrap logger.
        max_retries: number of retry attempts on failure.

    Returns:
        The local path to the downloaded file.
    """
    from huggingface_hub import hf_hub_download  # noqa: WPS433

    # Get the file size for progress information.
    size = _get_file_size(filename)
    size_str = _format_size(size) if size > 0 else "unknown size"

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                logger.info("Retry %d/%d for %s ...", attempt, max_retries, filename)
            else:
                logger.info("Downloading %s (%s) ...", filename, size_str)

            path = hf_hub_download(
                repo_id=MODEL_ID,
                filename=filename,
                revision=MODEL_REVISION,
                local_dir=MODEL_LOCAL_DIR,
            )
            logger.info("  %s downloaded successfully.", filename)
            return path

        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("  Attempt %d failed: %s", attempt, exc)
            if attempt < max_retries:
                logger.info("  Waiting 5 seconds before retry...")
                time.sleep(5)

    raise RuntimeError(
        "Failed to download {0} after {1} attempts. Last error: {2}".format(
            filename, max_retries, last_error
        )
    )


def _download_files(allow_patterns, logger, label):
    """Download the given file patterns from the model repository.

    Uses snapshot_download with local_dir, which downloads files as regular
    copies (no symlinks). This works on Windows without Developer Mode.
    """
    from huggingface_hub import snapshot_download  # noqa: WPS433

    logger.info("Downloading %s from %s ...", label, MODEL_ID)
    start = time.time()
    path = snapshot_download(
        repo_id=MODEL_ID,
        revision=MODEL_REVISION,
        allow_patterns=allow_patterns,
        local_dir=MODEL_LOCAL_DIR,
        # Limit parallel workers to avoid overwhelming slow connections.
        max_workers=4,
    )
    elapsed = time.time() - start
    logger.info("%s downloaded in %.1f s -> %s", label, elapsed, path)
    return path


def ensure_tokenizer(logger):
    """Download tokenizer and small config files if missing."""
    if _is_tokenizer_cached():
        logger.info("Tokenizer and config files already present.")
        return
    _download_files(SMALL_FILE_PATTERNS, logger, "tokenizer and config files")


def ensure_model_weights(logger):
    """Download the large model weight file(s) if missing.

    This is the heaviest part of the bootstrap (several gigabytes). It only
    runs once; subsequent launches detect the cached weights and skip this
    step entirely.

    The download is resumable: if it is interrupted (network drop, power
    outage, etc.), re-running launch.bat will resume from where it left off.
    """
    shards = _weight_shard_filenames()
    if _is_model_cached():
        logger.info("Model weights already present.")
        return

    # Report what we're about to download so the user knows the scale.
    total_size = 0
    for shard in shards:
        size = _get_file_size(shard)
        total_size += size
        logger.info("  Model file: %s (%s)", shard, _format_size(size))

    logger.info("Total download size: %s", _format_size(total_size))

    # Check available disk space before starting.
    try:
        import shutil
        free_bytes = shutil.disk_usage(MODELS_DIR).free
        free_str = _format_size(free_bytes)
        # Need total_size + 10% overhead for temporary files.
        needed = int(total_size * 1.1)
        if free_bytes < total_size:
            logger.error("ERROR: Not enough disk space for the model download.")
            logger.error("  Need: %s (plus overhead)", _format_size(needed))
            logger.error("  Available: %s", free_str)
            logger.error("  Free up disk space and run launch.bat again.")
            raise RuntimeError(
                "Not enough disk space. Need {0}, have {1}.".format(
                    _format_size(needed), free_str
                )
            )
        elif free_bytes < needed:
            logger.warning("WARNING: Disk space is tight.")
            logger.warning("  Need: %s (with overhead), have: %s",
                           _format_size(needed), free_str)
            logger.warning("  The download may fail. Consider freeing more space.")
        else:
            logger.info("Available disk space: %s", free_str)
    except RuntimeError:
        raise
    except Exception as exc:
        logger.warning("Could not check disk space: %s", exc)

    logger.info("This is a large download. It may take 10-60 minutes depending")
    logger.info("on your internet connection. The download is resumable - if it")
    logger.info("is interrupted, just run launch.bat again to continue.")
    logger.info("")

    # Download each shard file individually with retry logic.
    for shard in shards:
        if _is_file_cached(shard):
            logger.info("  %s already downloaded, skipping.", shard)
            continue
        _download_single_file(shard, logger, max_retries=5)


def validate_downloaded_files(logger):
    """Verify that every required file is present in the local model directory."""
    model_path = MODEL_LOCAL_DIR
    logger.info("Validating downloaded files at: %s", model_path)

    # Tokenizer and config files.
    required = TOKENIZER_FILES + MODEL_CONFIG_FILES
    missing = [
        fname for fname in required
        if not os.path.isfile(os.path.join(model_path, fname))
    ]
    if missing:
        raise RuntimeError(
            "Model validation failed. Missing files: " + ", ".join(missing)
        )

    # Weight shard files referenced by the index.
    shard_files = _weight_shard_filenames()
    missing_shards = [
        shard for shard in shard_files
        if not os.path.isfile(os.path.join(model_path, shard))
    ]
    if missing_shards:
        raise RuntimeError(
            "Model validation failed. Missing weight shards: " + ", ".join(missing_shards)
        )

    logger.info("All required model and tokenizer files are present (%d weight shards).",
                len(shard_files))


def ensure_model_and_tokenizer(logger):
    """Detect, download and validate the model and tokenizer."""
    ensure_tokenizer(logger)
    ensure_model_weights(logger)
    validate_downloaded_files(logger)


# ---------------------------------------------------------------------------
# Stage 8: Start the application
# ---------------------------------------------------------------------------
def start_application(logger):
    """Launch SpeechStudio.py using the venv Python.

    The GUI runs as an independent process so that bootstrap can exit cleanly
    once the environment is ready.
    """
    if not os.path.isfile(APP_ENTRY):
        raise RuntimeError("Application entry point not found: " + APP_ENTRY)

    logger.info("Starting %s ...", APP_NAME)
    env = dict(os.environ)
    # Detach the GUI so it survives the bootstrap process exiting.
    kwargs = dict(env=env)
    if not is_windows():
        kwargs["start_new_session"] = True
    subprocess.Popen([get_venv_python(), APP_ENTRY], **kwargs)
    logger.info("%s launched.", APP_NAME)


# ---------------------------------------------------------------------------
# Venv dependency sanity check (stage 2 only)
# ---------------------------------------------------------------------------
def _verify_venv_dependencies(logger):
    """Verify that the key packages are importable inside the venv.

    Dependencies are installed in stage 1 (system Python). This check does
    NOT reinstall them; it only confirms they are present. If a package is
    missing (for example because bootstrap was launched directly inside the
    venv without a prior stage-1 run), a clear error is raised.
    """
    required = ["huggingface_hub"]
    missing = []
    for package in required:
        try:
            __import__(package)
        except Exception:  # noqa: BLE001
            missing.append(package)

    if missing:
        raise RuntimeError(
            "Required package(s) not importable inside the virtual environment: "
            + ", ".join(missing)
            + ". Run launch.bat so that bootstrap can install "
            "dependencies from requirements.txt."
        )
    logger.info("Virtual environment dependencies verified.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main():
    logger = setup_logger()
    log_banner(logger)

    try:
        if not running_in_venv():
            # -----------------------------------------------------------
            # Stage 1 (system Python): prepare the runtime environment.
            # These stages run exactly once, in the system interpreter,
            # before the script re-launches itself inside the venv.
            # -----------------------------------------------------------
            # --- Stage 1: Environment validation ---
            verify_python_version(logger)

            # --- Stage 2: Virtual environment ---
            ensure_virtualenv(logger)

            # --- Stage 3: Dependency validation ---
            ensure_dependencies(logger)

            # --- Stage 4: Folder validation ---
            ensure_folders(logger)

            # --- Re-launch inside the venv for Hugging Face operations ---
            relaunch_in_venv(logger)
            return 0

        # ---------------------------------------------------------------
        # Stage 2 (venv Python): Hugging Face operations and app launch.
        # We are now running inside the project virtual environment.
        # Dependency installation is NOT repeated here; it was completed
        # in stage 1. We only verify that the key packages are importable
        # so that a direct venv invocation without a prior stage-1 run
        # produces a clear error instead of a confusing import crash.
        # ---------------------------------------------------------------
        logger.info("Running inside project virtual environment.")
        logger.info("Continuing bootstrap stage 2 (Hugging Face operations).")

        _verify_venv_dependencies(logger)

        # --- Stage 5: Hugging Face cache validation ---
        verify_hf_cache(logger)

        # --- Stage 6: CUDA verification ---
        verify_cuda(logger)

        # --- Stage 7: Model and tokenizer validation / download ---
        ensure_model_and_tokenizer(logger)

        # --- Stage 8: Start the application ---
        start_application(logger)

        logger.info("Bootstrap complete.")
        return 0

    except subprocess.CalledProcessError as exc:
        logger.error("A subprocess failed (exit code %s).", exc.returncode)
        logger.error("Command: %s", " ".join(exc.cmd) if exc.cmd else "(unknown)")
        logger.debug(traceback.format_exc())
        return 1
    except Exception as exc:  # noqa: BLE001 - bootstrap must never crash silently
        logger.error("Bootstrap failed: %s", exc)
        logger.debug(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
