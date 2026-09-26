#!/usr/bin/env python3
"""
SpeechStudio - Application Entry Point
======================================

This module is the graphical application entry point. It is launched by
bootstrap.py after the runtime environment is ready.

Phase 1 responsibilities:
    - Create the PySide6 QApplication.
    - Apply the dark theme.
    - Show the main window.
    - Keep the application running until the user closes the window.

Speech generation is NOT part of Phase 1. The main window at this stage is a
functional shell that proves the environment is correctly configured. The
full UI panels (Sprint 03) and the speech engine (Sprint 02/06) are built in
later phases.

Portability: all paths are relative to the application root. No user profile,
AppData or Registry access is performed.
"""

import os
import sys
import logging
import faulthandler
import traceback

# Enable faulthandler to catch native segfaults and write the stack trace
# to stderr AND to a crash dump file. This is critical for diagnosing
# crashes that don't produce a Python traceback (e.g. QOpenGLWidget crashes).
_crash_dump_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "crash_dump.txt")
os.makedirs(os.path.dirname(_crash_dump_path), exist_ok=True)
try:
    _crash_file = open(_crash_dump_path, "w", encoding="utf-8")
    faulthandler.enable(_crash_file, all_threads=True)
except Exception:
    faulthandler.enable()  # fallback to stderr only

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(APP_ROOT, "logs")

# Configure the local Hugging Face cache here too, so that any future import
# of transformers / huggingface_hub inside the application process uses the
# project local cache (System Specification section 13).
CACHE_DIR = os.path.join(APP_ROOT, ".cache", "huggingface")
os.environ.setdefault("HF_HOME", CACHE_DIR)
os.environ.setdefault("HF_HUB_CACHE", os.path.join(CACHE_DIR, "hub"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# Disable the hf-xet download backend (same as bootstrap.py).
# See bootstrap.py for the full rationale.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
# Suppress the symlink warning on Windows (we use local_dir, not cache_dir).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


# ---------------------------------------------------------------------------
# Application logging (Phase 1 minimal logger)
# ---------------------------------------------------------------------------
def setup_application_logger():
    """Configure the root logger to write to logs/application.log and stdout."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_file = os.path.join(LOGS_DIR, "application.log")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Remove existing handlers so re-init does not duplicate output.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("[%(levelname)s] %(message)s")
    )
    root.addHandler(console_handler)

    return logging.getLogger("speechstudio")


# ---------------------------------------------------------------------------
# Crash logger — writes unhandled exceptions to logs/crash.log
# ---------------------------------------------------------------------------
def _install_crash_handler():
    """Install a global exception handler that logs crashes to logs/crash.log.

    This catches unhandled exceptions in the Python layer (including those
    swallowed by PySide6 signal handlers) and writes a full traceback to
    a dedicated crash log file. The log includes the timestamp, the
    exception type, the full traceback, and the application version.
    """
    import traceback
    import os
    from datetime import datetime

    crash_log = os.path.join(LOGS_DIR, "crash.log")

    def _excepthook(exc_type, exc_value, exc_tb):
        """Called on unhandled Python exceptions."""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 60 + "\n")
                f.write("CRASH at {0}\n".format(timestamp))
                f.write("=" * 60 + "\n")
                f.write(tb_text)
                f.write("\n")
            # Also log to the main logger.
            logging.getLogger("speechstudio").critical(
                "UNHANDLED EXCEPTION: %s: %s\n%s",
                exc_type.__name__, exc_value, tb_text)
        except Exception:
            pass  # don't let the crash handler itself crash
        # Call the original excepthook to preserve default behavior.
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    # Also hook into PySide6 signal exceptions via threading.
    import threading
    def _threading_excepthook(args):
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            tb_text = "".join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback))
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 60 + "\n")
                f.write("THREAD CRASH at {0} (thread: {1})\n".format(
                    timestamp, args.thread.name))
                f.write("=" * 60 + "\n")
                f.write(tb_text)
                f.write("\n")
            logging.getLogger("speechstudio").critical(
                "THREAD EXCEPTION in %s: %s: %s\n%s",
                args.thread.name, args.exc_type.__name__, args.exc_value, tb_text)
        except Exception:
            pass
    threading.excepthook = _threading_excepthook


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    logger = setup_application_logger()

    # Install the crash handler BEFORE anything else, so we catch
    # crashes during initialization too.
    _install_crash_handler()
    logger.info("Crash handler installed — logs/crash.log")

    # Increment build number and detect Git metadata (once per startup)
    from engine.version import (
        increment_build, get_startup_log_header, get_full_version_string,
        get_debug_info, APP_NAME,
    )
    increment_build()

    logger.info("=" * 60)
    logger.info(get_startup_log_header())
    logger.info(get_full_version_string())
    logger.info("Application root: %s", APP_ROOT)
    logger.info("=" * 60)
    logger.debug(get_debug_info())

    # Import PySide6 lazily so that a missing dependency produces a clear
    # error message rather than an import crash at module load time.
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QSurfaceFormat
    except Exception as exc:
        logger.error("PySide6 could not be imported: %s", exc)
        logger.error("Make sure bootstrap.py completed successfully.")
        return 1

    # Set the default OpenGL surface format BEFORE QApplication is created.
    # This is required by the GPU ambient renderer (QOpenGLWidget). The
    # format requests an OpenGL 4.6 Compatibility Profile context, which
    # is the format proven to work in the standalone reference implementation
    # (ambient_gpu_v5_refined.py) on the target machine (RTX 5070).
    #
    # CRITICAL: must be set before QApplication is created so all
    # QOpenGLWidget instances pick up this format.
    try:
        fmt = QSurfaceFormat()
        fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        fmt.setVersion(4, 6)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
        fmt.setAlphaBufferSize(8)  # required for transparent QOpenGLWidget
        fmt.setSwapInterval(1)  # VSync on
        QSurfaceFormat.setDefaultFormat(fmt)
        logger.info("OpenGL surface format set: 4.6 CompatibilityProfile (alpha=8)")
    except Exception as exc:
        logger.warning("Could not set default OpenGL surface format: %s", exc)

    # The application uses a single QApplication instance.
    # High-DPI scaling is handled automatically by Qt 6.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    # P3.37 rebrand: the user-facing product identity is GUINEO. Internal
    # technical identifiers (file names, package paths, log namespaces)
    # deliberately keep their historical SpeechStudio names.
    app.setApplicationName("GUINEO")
    app.setApplicationDisplayName("GUINEO")
    app.setOrganizationName("GUINEO")

    # Application icon (GUINEO logo). Applied at the QApplication level so
    # every window and dialog inherits it. Missing asset is non-fatal.
    try:
        from PySide6.QtGui import QIcon
        _logo_path = os.path.join(APP_ROOT, "assets", "brand", "guineo_logo.png")
        if os.path.isfile(_logo_path):
            app.setWindowIcon(QIcon(_logo_path))
            logger.info("Application icon set: %s", _logo_path)
        else:
            logger.warning("Application icon not found (non-fatal): %s", _logo_path)
    except Exception as exc:
        logger.warning("Could not set application icon (non-fatal): %s", exc)

    # --- Load the HTML design system fonts (Phase B) ---
    # Loads Inter, Libre Franklin, and JetBrains Mono TTFs from
    # assets/fonts/. These are the authoritative font families specified
    # in DESIGN.md. If the files are missing, Qt falls back to the
    # families specified in ui.design_tokens.Typography (which include
    # "Segoe UI", "DejaVu Sans", etc.).
    try:
        from ui.font_loader import load_fonts
        _font_status = load_fonts()
        for family, weights in _font_status.items():
            logger.info("Font loaded: %s (weights: %s)", family, weights)
    except Exception as exc:
        logger.warning("Font loading failed (non-fatal): %s", exc)

    # Apply the saved theme before any widget is created.
    from ui.theme import apply_theme, THEMES, DEFAULT_THEME
    # Read theme from settings
    from engine.settings_manager import SettingsManager
    import os as _os
    _settings_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "settings")
    # P3.25 (audit SS-H04): use the SHARED settings owner — the entry
    # point, Engine and MainWindow previously held three private
    # instances whose divergent caches silently reverted each other.
    _sm = SettingsManager.instance(_settings_dir)
    _theme_name = _sm.get("application", "theme", DEFAULT_THEME)
    _palette = apply_theme(app, _theme_name)
    logger.info("Theme applied: %s", _theme_name)

    # --- Initialise the Engine (Sprint 02: Core Engine) ---
    # The Engine is the single entry point for all backend operations.
    # The UI connects to it via the engine parameter.
    from engine import Engine
    engine = Engine()
    logger.info("SpeechStudio engine initialised (model not yet loaded).")

    # Create and show the main window, passing the engine.
    from ui.main_window import MainWindow
    try:
        logger.info("Creating MainWindow...")
        window = MainWindow(engine=engine)
        logger.info("MainWindow created successfully. About to call show()...")
    except Exception as exc:
        logger.error("Failed to create MainWindow: %s", exc, exc_info=True)
        traceback.print_exc()
        raise

    # CRITICAL: The show() call can trigger a native crash (segfault) on
    # Windows if the GPU ambient renderer (QOpenGLWidget) conflicts with
    # the widget hierarchy. We log before and after, and flush immediately,
    # so the log shows exactly where the crash happens.
    logger.info("Calling window.show()...")
    # Flush all log handlers so the log file is written even if show() crashes
    for handler in logging.getLogger().handlers:
        handler.flush()
    try:
        window.show()
        logger.info("window.show() completed successfully.")
    except Exception as exc:
        logger.error("Failed to show main window: %s", exc, exc_info=True)
        traceback.print_exc()
        raise

    logger.info("SpeechStudio main window shown. Entering Qt event loop.")

    # Run the Qt event loop until the user closes the application.
    exit_code = app.exec()

    logger.info("SpeechStudio exited with code %s.", exit_code)

    # --- Engine shutdown (System Specification section 19) ---
    # Stop playback, cancel generation, unload model, flush logs.
    engine.shutdown()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
