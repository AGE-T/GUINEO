"""
SpeechStudio Engine - Settings Manager
=======================================

Engine Specification, section 12:
    "SettingsManager stores: Application settings, Generation defaults,
     Voice defaults, Window layout, Audio preferences.
     Settings are persisted as JSON."

System Specification, section 10:
    "SpeechStudio stores configuration inside the project directory.
     Configuration files shall use JSON format."

AI Development Rules, section 19:
    "Every user configurable option shall be editable through the graphical
     interface. Users shall never edit Python files to change application
     behaviour. Settings are stored as JSON."

Single responsibility: load and save settings.json. Nothing else.

P3.25 (audit SS-H04 / SS-H09 / SS-L14) — hardening summary:
  * ONE authoritative instance per settings file per process
    (``SettingsManager.instance()``). Engine, MainWindow and the entry
    point all share it — no divergent caches.
  * Atomic writes (tmp file + os.replace) — no torn settings.json on
    crash mid-write.
  * Reload-on-read: ``get()`` re-reads the disk file when it changed
    (mtime), so independent instances never serve stale values.
  * Robust load: ANY malformed file (bad JSON, wrong root type, wrong
    encoding) falls back to defaults with a logged warning; the corrupt
    file is quarantined (renamed) so the app self-heals and never bricks
    startup (audit SS-H09).
  * ``get()`` tolerates non-dict section values instead of raising
    (audit SS-L14).
"""

from __future__ import annotations
import os
import json
import threading
from typing import Any, Dict, Optional

from engine.logger import get_logger
from engine.models import GenerationParameters

logger = get_logger("settings_manager")


# ---------------------------------------------------------------------------
# Default settings
# ---------------------------------------------------------------------------
# Applied when no settings file exists yet. Every key here has a default so
# the application always has a valid settings state on first launch.
DEFAULT_SETTINGS: Dict[str, Any] = {
    "application": {
        "theme": "modern-dark",
        "developer_mode": False,
        "check_for_model_on_startup": True,
        "current_project": "Default",
        "current_scene": "Untitled",
        "playback_volume": 1.0,
        # First-run flag: False until the demo text has been inserted once.
        # Used by MainWindow to insert the Hungarian demo text on the very
        # first application startup (and only then).
        "first_run_completed": False,
        # Multi-speaker dialogue: maps speaker labels to voice profile IDs.
        # Updated automatically by the LongNarrationDialog (no manual editing).
        # Persists across dialog reopens so the user doesn't have to reassign
        # voices every time they open the Narration dialog.
        "speaker_voice_map": {},
    },
    "generation": GenerationParameters().to_dict(),
    "voice": {
        "default_voice_id": None,
        "auto_validate_reference": True,
    },
    "window": {
        "width": 1280,
        "height": 800,
        "sidebar_width": 440,
        "control_panel_width": 440,
        "sidebar_collapsed": False,
        "control_panel_collapsed": False,
    },
    "audio": {
        "output_directory": "outputs",
        "default_format": "wav",
        "auto_play": True,
        "volume": 1.0,
        # Dialogue concatenation settings (used by concatenate_with_silence).
        "concatenate_silence_ms": 300,
        "concatenate_randomize": True,
    },
    "performance": {
        "keep_model_loaded": True,
        "release_tensors_after_generation": True,
        "model_precision": "bfloat16",
    },
}


class SettingsManager:
    """Loads and saves application settings as JSON.

    The settings file lives at settings/settings.json inside the project
    directory (System Specification section 10). All paths are relative.

    P3.23 FIX (audit: settings split brain / cross-instance overwrite):
    Multiple SettingsManager instances can exist (MainWindow, Engine,
    SpeechStudio startup). Each instance tracks which keys IT modified
    (the dirty set) and _save() merges ONLY those keys on top of a fresh
    re-read of the disk file — concurrent writes by other instances are
    preserved.

    P3.25 HARDENING (audit SS-H04 residual + SS-H09 + SS-L14):
      * Production code obtains the ONE shared instance per settings dir
        via ``SettingsManager.instance(settings_dir)`` — Engine, MainWindow
        and the entry point share the same cache.
      * Independent instances stay coherent anyway: ``get()`` re-reads the
        disk file when its mtime changed, eliminating the stale-read
        residual.
      * Writes are ATOMIC (tmp file + os.replace).
      * ANY malformed settings file falls back to defaults (never crashes
        startup); the corrupt file is quarantined alongside the settings
        dir for diagnosis.
      * Non-dict section values no longer raise in ``get()``.
    """

    _instances: Dict[str, "SettingsManager"] = {}
    _instances_lock = threading.Lock()

    @classmethod
    def instance(cls, settings_dir: str) -> "SettingsManager":
        """Return the shared SettingsManager for ``settings_dir``.

        P3.25 (SS-H04): the single authoritative owner per settings file
        per process. Production code (Engine, MainWindow, entry point,
        dialogs) MUST use this instead of constructing private instances.
        """
        key = os.path.abspath(settings_dir)
        with cls._instances_lock:
            sm = cls._instances.get(key)
            if sm is None:
                sm = cls(settings_dir)
                cls._instances[key] = sm
            return sm

    def __init__(self, settings_dir: str):
        self._settings_dir = settings_dir
        self._file_path = os.path.join(settings_dir, "settings.json")
        self._settings: Dict[str, Any] = {}
        self._lock = threading.RLock()
        # (section, key) tuples set via set() since the last successful
        # save; whole sections set via set_section() are tracked as
        # (section, None).
        self._dirty: set = set()
        # (st_ino, st_mtime_ns, st_size) of the disk file as last seen by
        # this instance (None = no file / unknown). Used by get() to
        # reload-on-change. The INODE component is the key signal for
        # this app's own writes (each save is an atomic os.replace → new
        # inode); mtime/size cover in-place edits by external tools.
        self._disk_stat: Optional[tuple] = None
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load(self) -> None:
        """Load settings from disk, merging with defaults.

        P3.25 (SS-H09): ANY failure — unreadable file, invalid JSON, wrong
        root type, wrong encoding — falls back to defaults with a logged
        warning. The corrupt file is quarantined (renamed to
        settings.json.corrupt-<timestamp>) so the next save writes a clean
        file and startup can never brick.
        """
        if not os.path.isfile(self._file_path):
            logger.info("No settings file found; using defaults.")
            self._settings = _deep_copy(DEFAULT_SETTINGS)
            self._disk_stat = None
            self._save()
            return
        loaded = None
        try:
            with open(self._file_path, "r", encoding="utf-8",
                      errors="replace") as handle:
                loaded = json.load(handle)
        except Exception as exc:  # SS-H09: broad — never crash startup
            logger.error("Failed to load settings (%s): %s — falling back "
                         "to defaults.", type(exc).__name__, exc)
            loaded = None
        if not isinstance(loaded, dict):
            if loaded is not None:
                logger.error("settings.json root is %s (expected object) — "
                             "falling back to defaults.",
                             type(loaded).__name__)
            elif os.path.isfile(self._file_path):
                logger.error("settings.json is not valid JSON — falling "
                             "back to defaults.")
            self._quarantine_corrupt()
            self._settings = _deep_copy(DEFAULT_SETTINGS)
            self._disk_stat = None
            return
        # Root must not be an empty/invalid container; merge with defaults.
        self._settings = _deep_merge(DEFAULT_SETTINGS, loaded)
        self._disk_stat = self._current_stat()
        logger.info("Settings loaded from %s", self._file_path)

    def _current_stat(self) -> Optional[tuple]:
        """(ino, mtime_ns, size) of the settings file, or None."""
        try:
            st = os.stat(self._file_path)
            return (st.st_ino, st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _quarantine_corrupt(self) -> None:
        """Rename a corrupt settings file aside (audit SS-H09)."""
        try:
            import time
            stamp = time.strftime("%Y%m%d_%H%M%S")
            quarantine = "{0}.corrupt-{1}".format(self._file_path, stamp)
            os.replace(self._file_path, quarantine)
            logger.warning("Corrupt settings file quarantined as %s",
                           quarantine)
        except OSError as exc:
            logger.error("Could not quarantine corrupt settings file: %s",
                         exc)

    def _reload_if_changed(self) -> None:
        """Reload the disk state when another instance rewrote the file.

        P3.25 (SS-H04 residual): values READ by an instance previously
        stayed cached until that instance next saved — a stale read could
        silently revert a newer value written by another instance. The
        (inode, mtime_ns, size) check detects every rewrite INCLUDING
        same-timestamp writes: this app's own saves are atomic
        os.replace operations, which always change the inode.
        """
        stat = self._current_stat()
        if stat == self._disk_stat:
            return
        # The file changed under us — refresh the cache (keep our dirty
        # set: unsaved local modifications still win on next save).
        logger.debug("Settings file changed on disk — reloading cache.")
        try:
            with open(self._file_path, "r", encoding="utf-8",
                      errors="replace") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                self._settings = _deep_merge(DEFAULT_SETTINGS, loaded)
                self._disk_stat = stat
            else:
                logger.warning("Settings file on disk is not a JSON "
                               "object — keeping current cache.")
                self._disk_stat = stat
        except Exception as exc:
            logger.warning("Could not reload changed settings file: %s", exc)
            self._disk_stat = stat

    def _save(self) -> None:
        """Persist settings to disk (merge-on-write, ATOMIC).

        P3.23: re-reads the CURRENT disk file (if any) and merges only
        this instance's dirty keys on top, so concurrent writes made by
        other SettingsManager instances (Engine / startup reader) are
        never silently reverted.

        P3.25 (SS-H04): the write is ATOMIC — the merged state is written
        to a temp file in the same directory and os.replace()d into place,
        so a crash mid-write can never leave a torn/partial settings.json.
        """
        with self._lock:
            os.makedirs(self._settings_dir, exist_ok=True)
            try:
                # 1. Fresh read of the disk state (defaults if unreadable).
                disk: Dict[str, Any] = {}
                if os.path.isfile(self._file_path):
                    try:
                        with open(self._file_path, "r", encoding="utf-8",
                                  errors="replace") as handle:
                            disk = json.load(handle)
                        if not isinstance(disk, dict):
                            disk = {}
                    except Exception as exc:
                        logger.warning(
                            "Could not re-read settings before save: %s", exc)
                        disk = {}
                merged = _deep_merge(DEFAULT_SETTINGS, disk) \
                    if disk else _deep_copy(DEFAULT_SETTINGS)

                # 2. Apply ONLY this instance's modifications.
                for section, key in self._dirty:
                    if key is None:
                        # Whole-section replacement (set_section).
                        merged[section] = _deep_copy(
                            self._settings.get(section, {}))
                    else:
                        if section not in merged or not isinstance(
                                merged.get(section), dict):
                            merged[section] = {}
                        if section in self._settings and key in \
                                self._settings.get(section, {}):
                            merged[section][key] = _deep_copy(
                                self._settings[section][key])

                # 3. ATOMIC write: tmp file + os.replace (same filesystem).
                tmp_path = "{0}.tmp-{1}".format(
                    self._file_path, id(self))
                with open(tmp_path, "w", encoding="utf-8") as handle:
                    json.dump(merged, handle, indent=2, ensure_ascii=False)
                os.replace(tmp_path, self._file_path)

                # 4. Refresh the in-memory cache (now consistent with disk,
                #    including keys other instances wrote since our load).
                self._settings = merged
                self._dirty.clear()
                self._disk_stat = self._current_stat()
                logger.debug("Settings saved to %s (merge-on-write, atomic)",
                             self._file_path)
            except OSError as exc:
                logger.error("Failed to save settings: %s", exc)

    # ------------------------------------------------------------------
    # Access API
    # ------------------------------------------------------------------
    def get(self, section: str, key: Optional[str] = None,
            default: Any = None) -> Any:
        """Read a setting value.

        Args:
            section: top-level key (e.g. "generation", "window").
            key: optional second-level key.
            default: returned if the key is not found.

        P3.25 (SS-L14): a non-dict section value (e.g. {"window": "big"})
        previously raised AttributeError — now the default is returned.
        """
        self._reload_if_changed()
        section_data = self._settings.get(section, {})
        if key is None:
            return section_data if section_data is not None else default
        if not isinstance(section_data, dict):
            return default
        return section_data.get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        """Write a setting value and persist immediately."""
        with self._lock:
            if section not in self._settings or \
                    not isinstance(self._settings.get(section), dict):
                self._settings[section] = {}
            self._settings[section][key] = value
            self._dirty.add((section, key))
            self._save()
        logger.debug("Setting updated: %s.%s = %s", section, key, value)

    def set_section(self, section: str, data: Dict[str, Any]) -> None:
        """Replace an entire section and persist."""
        with self._lock:
            self._settings[section] = data
            # Also drop any per-key dirty entries for this section — the whole
            # section is being replaced.
            self._dirty = {(s, k) for (s, k) in self._dirty
                           if s != section or k is None}
            self._dirty.add((section, None))
            self._save()
        logger.debug("Section replaced: %s", section)

    def get_generation_parameters(self) -> GenerationParameters:
        """Return the default generation parameters as a dataclass."""
        data = self.get("generation") or {}
        if not isinstance(data, dict):
            data = {}
        return GenerationParameters.from_dict(data)

    def set_generation_parameters(self, params: GenerationParameters) -> None:
        """Persist generation parameters as the new defaults."""
        self.set_section("generation", params.to_dict())

    @property
    def all_settings(self) -> Dict[str, Any]:
        """Return a deep copy of all settings (for the settings dialog)."""
        self._reload_if_changed()
        return _deep_copy(self._settings)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _deep_copy(obj: Any) -> Any:
    """Deep-copy a JSON-serialisable structure."""
    return json.loads(json.dumps(obj))


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override into base; base provides defaults.

    Used so that new default keys added in a future version appear
    automatically even when the user's settings file was written by an
    older version.
    """
    result = _deep_copy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = _deep_copy(value)
    return result
