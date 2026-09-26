"""
SpeechStudio Engine - Logger
============================

AI Development Rules, section 13:
    "Every major subsystem shall write structured log messages.
     Logs shall include: Timestamp, Component, Severity, Message,
     Optional stack trace"

Engine Specification, section 18:
    "The Engine logs: generation requests, generation duration, sampling
     parameters, voice profile, errors, warnings, performance data.
     Logs are written to logs/"

System Specification, section 15:
    "Logs are stored inside logs/
     Log files include: Application, Bootstrap, Generation, Errors, Performance
     Every exception shall be logged."

The Logger wraps Python's standard logging module and provides:
    - per-component loggers (so every log line identifies its source)
    - structured formatting with timestamp, component, severity, message
    - optional stack-trace capture for exceptions
    - separate log files per subsystem
"""

from __future__ import annotations
import os
import logging
import traceback
from typing import Optional


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Resolved relative to the SpeechStudio application root.
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGS_DIR = os.path.join(_APP_ROOT, "logs")

# Log file names (System Specification section 15).
LOG_FILES = {
    "application": "application.log",
    "bootstrap": "bootstrap.log",
    "generation": "generation.log",
    "errors": "errors.log",
    "performance": "performance.log",
    "engine": "engine.log",
}

# Severity levels ordered for the _severity_name helper.
_LEVEL_NAMES = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------
class StructuredFormatter(logging.Formatter):
    """Formatter that produces structured log lines.

    Format:  YYYY-MM-DD HH:MM:SS [SEVERITY] component: message
    With optional stack trace appended on ERROR/CRITICAL.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Inject a clean component name from the logger name.
        component = record.name.replace("speechstudio.", "")
        if not component:
            component = "root"
        severity = _LEVEL_NAMES.get(record.levelno, str(record.levelno))

        header = "{ts} [{sev}] {comp}: {msg}".format(
            ts=self.formatTime(record, datefmt="%Y-%m-%d %H:%M:%S"),
            sev=severity,
            comp=component,
            msg=record.getMessage(),
        )

        if record.exc_info and record.exc_info[1] is not None:
            # Append a stack trace for exception logs.
            tb = "".join(traceback.format_exception(*record.exc_info))
            return header + "\n" + tb.rstrip()
        return header


# ---------------------------------------------------------------------------
# Logger singleton
# ---------------------------------------------------------------------------
class Logger:
    """Centralised logging facility for every engine subsystem.

    Provides per-component child loggers that all write to the engine log
    file (and the errors file for WARNING+). Using a single class keeps the
    logging configuration in one place and matches the AI Development Rules
    requirement that "every major subsystem shall write structured log
    messages".
    """

    _instance: Optional["Logger"] = None

    def __init__(self, logs_dir: str = _LOGS_DIR):
        self._logs_dir = logs_dir
        os.makedirs(logs_dir, exist_ok=True)
        self._configure()

    @classmethod
    def get(cls) -> "Logger":
        """Return the singleton Logger instance, creating it on first use."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _configure(self) -> None:
        """Create file handlers for every subsystem log file."""
        self._root = logging.getLogger("speechstudio.engine")
        self._root.setLevel(logging.DEBUG)
        # Remove any pre-existing handlers so re-init is idempotent.
        for handler in list(self._root.handlers):
            self._root.removeHandler(handler)

        formatter = StructuredFormatter()

        # Main engine log - captures everything DEBUG and above.
        engine_path = os.path.join(self._logs_dir, LOG_FILES["engine"])
        engine_handler = logging.FileHandler(engine_path, mode="a", encoding="utf-8")
        engine_handler.setLevel(logging.DEBUG)
        engine_handler.setFormatter(formatter)
        self._root.addHandler(engine_handler)

        # Errors log - captures WARNING and above only.
        errors_path = os.path.join(self._logs_dir, LOG_FILES["errors"])
        errors_handler = logging.FileHandler(errors_path, mode="a", encoding="utf-8")
        errors_handler.setLevel(logging.WARNING)
        errors_handler.setFormatter(formatter)
        self._root.addHandler(errors_handler)

        # Generation log - captures generation-related messages.
        # Uses a filter so only the generation component writes here.
        gen_path = os.path.join(self._logs_dir, LOG_FILES["generation"])
        gen_handler = logging.FileHandler(gen_path, mode="a", encoding="utf-8")
        gen_handler.setLevel(logging.DEBUG)
        gen_handler.setFormatter(formatter)
        gen_handler.addFilter(_ComponentFilter("generation"))
        self._root.addHandler(gen_handler)

        # Performance log - captures performance-related messages.
        perf_path = os.path.join(self._logs_dir, LOG_FILES["performance"])
        perf_handler = logging.FileHandler(perf_path, mode="a", encoding="utf-8")
        perf_handler.setLevel(logging.DEBUG)
        perf_handler.setFormatter(formatter)
        perf_handler.addFilter(_ComponentFilter("performance"))
        self._root.addHandler(perf_handler)

    def component(self, name: str) -> logging.Logger:
        """Return a child logger for a specific engine component.

        Args:
            name: short component name, e.g. "model_manager", "generation".
        """
        return self._root.getChild(name)

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------
    def debug(self, component: str, message: str) -> None:
        self.component(component).debug(message)

    def info(self, component: str, message: str) -> None:
        self.component(component).info(message)

    def warning(self, component: str, message: str) -> None:
        self.component(component).warning(message)

    def error(self, component: str, message: str,
              exc_info: bool = False) -> None:
        self.component(component).error(message, exc_info=exc_info)

    def exception(self, component: str, message: str) -> None:
        """Log an error with a full stack trace (use inside except blocks)."""
        self.component(component).error(message, exc_info=True)


class _ComponentFilter(logging.Filter):
    """Filter that only passes records whose component name matches."""

    def __init__(self, component: str):
        super().__init__()
        self._component = component

    def filter(self, record: logging.LogRecord) -> bool:
        # The component is the last segment of the logger name after
        # "speechstudio.engine.".
        name = record.name
        parts = name.split(".")
        return parts[-1] == self._component if parts else False


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------
def get_logger(component: str) -> logging.Logger:
    """Return a configured logger for the given engine component.

    This is the primary entry point used by every engine module:

        from engine.logger import get_logger
        logger = get_logger("model_manager")
        logger.info("Model loaded successfully")
    """
    return Logger.get().component(component)
