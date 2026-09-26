"""
SpeechStudio Build Versioning System.
Single source of truth for version and build information.
"""
from __future__ import annotations
import os
import json
import platform
import subprocess
from typing import Optional

# GUINEO is the user-facing product name (P3.37 rebrand). The internal
# project/technical identifiers (SpeechStudio.py, package paths, log
# namespaces, file-format strings) deliberately keep their historical
# names — only USER-VISIBLE branding changed.
APP_NAME = "GUINEO"
APP_VERSION = "1.0.0"

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BUILD_FILE = os.path.join(_APP_ROOT, "settings", "build.json")

_BUILD_NUMBER: Optional[int] = None
_BUILD_DATE: Optional[str] = None
_GIT_COMMIT: Optional[str] = None
_GIT_BRANCH: Optional[str] = None
_GIT_DIRTY: bool = False


def _detect_git() -> None:
    global _GIT_COMMIT, _GIT_BRANCH, _GIT_DIRTY
    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3, cwd=_APP_ROOT)
        if result.returncode == 0:
            _GIT_COMMIT = result.stdout.strip()
        result = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3, cwd=_APP_ROOT)
        if result.returncode == 0:
            _GIT_BRANCH = result.stdout.strip()
        result = subprocess.run(["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=3, cwd=_APP_ROOT)
        _GIT_DIRTY = bool(result.stdout.strip())
    except Exception:
        pass


def _load_build_number() -> int:
    global _BUILD_NUMBER, _BUILD_DATE
    try:
        with open(_BUILD_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            _BUILD_NUMBER = data.get("build_number", 0)
            _BUILD_DATE = data.get("build_date", "")
    except Exception:
        _BUILD_NUMBER = 0
        _BUILD_DATE = ""
    return _BUILD_NUMBER


def _save_build_number(num: int, date_str: str) -> None:
    os.makedirs(os.path.dirname(_BUILD_FILE), exist_ok=True)
    try:
        with open(_BUILD_FILE, "w", encoding="utf-8") as f:
            json.dump({"build_number": num, "build_date": date_str}, f)
    except Exception:
        pass


def increment_build() -> None:
    global _BUILD_NUMBER, _BUILD_DATE
    from datetime import datetime
    _load_build_number()
    _BUILD_NUMBER = (_BUILD_NUMBER or 0) + 1
    _BUILD_DATE = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_build_number(_BUILD_NUMBER, _BUILD_DATE)
    _detect_git()


def get_build_number() -> int:
    if _BUILD_NUMBER is None:
        _load_build_number()
    return _BUILD_NUMBER or 1

def get_build_date() -> str:
    return _BUILD_DATE or "unknown"

def get_git_commit() -> str:
    return _GIT_COMMIT or "n/a"

def get_git_branch() -> str:
    return _GIT_BRANCH or "n/a"

def get_git_dirty() -> bool:
    return _GIT_DIRTY

def get_version_string() -> str:
    return "v{0} Build {1}".format(APP_VERSION, get_build_number())

def get_full_version_string() -> str:
    s = get_version_string()
    if _GIT_COMMIT:
        s += " ({0})".format(_GIT_COMMIT)
        if _GIT_DIRTY:
            s += " [dirty]"
    return s

def get_python_version() -> str:
    return "{0}.{1}.{2}".format(*platform.python_version_tuple())

def get_platform_string() -> str:
    return platform.platform()

def get_torch_version() -> str:
    try:
        import torch; return torch.__version__
    except Exception:
        return "not installed"

def get_transformers_version() -> str:
    try:
        import transformers; return transformers.__version__
    except Exception:
        return "not installed"

def get_qt_version() -> str:
    try:
        import PySide6; return PySide6.__version__
    except Exception:
        return "not installed"

def get_cuda_version() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.version.cuda or "available"
        return "unavailable"
    except Exception:
        return "unknown"

def get_gpu_name() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
        return "none"
    except Exception:
        return "unknown"

def get_model_name() -> str:
    return "multimodalart/higgs-audio-v3-tts-4b-transformers"

def get_model_precision() -> str:
    try:
        settings_path = os.path.join(_APP_ROOT, "settings", "settings.json")
        with open(settings_path, "r") as f:
            data = json.load(f)
        return data.get("performance", {}).get("model_precision", "bfloat16")
    except Exception:
        return "bfloat16"

def get_debug_info() -> str:
    lines = [
        "{0} {1}".format(APP_NAME, get_version_string()),
        "Build Date: {0}".format(get_build_date()),
        "Git: {0} ({1}){2}".format(get_git_commit(), get_git_branch(),
            " [dirty]" if get_git_dirty() else ""),
        "",
        "OS: {0}".format(get_platform_string()),
        "Python: {0}".format(get_python_version()),
        "PyTorch: {0}".format(get_torch_version()),
        "Transformers: {0}".format(get_transformers_version()),
        "Qt: {0}".format(get_qt_version()),
        "CUDA: {0}".format(get_cuda_version()),
        "GPU: {0}".format(get_gpu_name()),
        "",
        "Model: {0}".format(get_model_name()),
        "Precision: {0}".format(get_model_precision()),
        "App Root: {0}".format(_APP_ROOT),
    ]
    return "\n".join(lines)

def get_startup_log_header() -> str:
    return "{0} {1} | Python {2} | {3} | GPU: {4} | Model: {5} | Precision: {6}".format(
        APP_NAME, get_full_version_string(), get_python_version(),
        get_platform_string(), get_gpu_name(), get_model_name(), get_model_precision())
