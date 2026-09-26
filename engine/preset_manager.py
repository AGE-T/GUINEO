"""
SpeechStudio Engine - Preset Manager
====================================

A Preset captures the *voice + emotion + style + prosody + generation
parameters* of a single configuration so the user can re-apply it with
one click.  Presets are stored as YAML (primary) with a JSON fallback for
environments without PyYAML.

Engine Specification, AI Development Rules:
    - PresetManager is a pure data layer.  It does NOT import PySide6.
    - PresetManager never touches the model, never builds prompts.
    - YAML is the primary on-disk format.  JSON is used as a fallback.

Preset schema (v1):

    name: "Default Audiobook"
    voice_id: "narrator_female"
    emotion: "Calm"
    style: "Narration"
    speed: "Slow"
    pitch: "Normal"
    delivery: "Expressive Low"
    parameters:
        temperature: 1.3
        top_p: 0.95
        top_k: 300
        max_new_tokens: 2048
        seed: null
        append_silence: 0.5
        normalize_output: false
        auto_play: true
        output_format: "wav"
"""

from __future__ import annotations
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.models import GenerationParameters


# ---------------------------------------------------------------------------
# Preset dataclass
# ---------------------------------------------------------------------------
@dataclass
class Preset:
    """A user-defined configuration snapshot.

    Attributes:
        name: human-readable preset name (unique within a PresetManager).
        voice_id: voice profile id, or None.
        emotion: emotion name, or None.
        style: style name, or None.
        speed: speed option name ("Slow", "Normal", "Fast", ...), or None.
        pitch: pitch option name, or None.
        delivery: delivery option name, or None.
        parameters: full GenerationParameters dataclass.
    """

    name: str = ""
    voice_id: Optional[str] = None
    emotion: Optional[str] = None
    style: Optional[str] = None
    speed: Optional[str] = None
    pitch: Optional[str] = None
    delivery: Optional[str] = None
    parameters: GenerationParameters = field(default_factory=GenerationParameters)

    # ------------------------------------------------------------------
    # (De)serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "voice_id": self.voice_id,
            "emotion": self.emotion,
            "style": self.style,
            "speed": self.speed,
            "pitch": self.pitch,
            "delivery": self.delivery,
            "parameters": self.parameters.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Preset":
        params_data = data.get("parameters", {})
        if isinstance(params_data, GenerationParameters):
            params = params_data
        else:
            params = GenerationParameters.from_dict(params_data or {})
        return cls(
            name=str(data.get("name", "")),
            voice_id=data.get("voice_id"),
            emotion=data.get("emotion"),
            style=data.get("style"),
            speed=data.get("speed"),
            pitch=data.get("pitch"),
            delivery=data.get("delivery"),
            parameters=params,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Preset):
            return NotImplemented
        return self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)


# ---------------------------------------------------------------------------
# YAML / JSON serialisation helpers
# ---------------------------------------------------------------------------
_NAME_RE = re.compile(r"[^A-Za-z0-9_\-]")


def _safe_filename(name: str) -> str:
    """Convert a preset name to a filesystem-safe basename (no extension)."""
    cleaned = _NAME_RE.sub("_", name.strip()).strip("_")
    return cleaned or "preset"


def _dump_yaml(data: Dict[str, Any]) -> str:
    """Dump ``data`` as YAML, falling back to a minimal hand-rolled emitter.

    This avoids a hard dependency on PyYAML in environments that lack it.
    """
    try:
        import yaml  # type: ignore
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    except ImportError:
        pass

    # Minimal YAML emitter (sufficient for the Preset schema above).
    lines: List[str] = []

    def _emit(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    lines.append("{0}{1}:".format(prefix, k))
                    _emit(prefix + "  ", v)
                elif v is None:
                    lines.append("{0}{1}: null".format(prefix, k))
                elif isinstance(v, bool):
                    lines.append("{0}{1}: {2}".format(prefix, k, "true" if v else "false"))
                elif isinstance(v, (int, float)):
                    lines.append("{0}{1}: {2}".format(prefix, k, v))
                else:
                    s = str(v).replace('"', '\\"')
                    lines.append('{0}{1}: "{2}"'.format(prefix, k, s))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    lines.append(prefix + "-")
                    _emit(prefix + "  ", item)
                else:
                    lines.append("{0}- {1}".format(prefix, item))

    _emit("", data)
    return "\n".join(lines) + "\n"


def _load_yaml(text: str) -> Dict[str, Any]:
    """Parse YAML text, falling back to JSON if PyYAML is unavailable."""
    try:
        import yaml  # type: ignore
        loaded = yaml.safe_load(text)
        return loaded if isinstance(loaded, dict) else {}
    except ImportError:
        pass

    # Try JSON (works if the file was actually JSON)
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except (json.JSONDecodeError, ValueError):
        pass

    # Very small YAML reader for flat mappings + one level of nesting.
    # This is enough to read back what _dump_yaml writes.
    root: Dict[str, Any] = {}
    stack: List[tuple] = [("", root)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        # Pop stack to current indent
        while stack and stack[-1][0] and len(stack[-1][0]) > indent:
            stack.pop()
        cur_prefix, cur_dict = stack[-1]
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            new: Dict[str, Any] = {}
            cur_dict[key] = new
            stack.append((" " * (indent + 2), new))
        else:
            cur_dict[key] = _coerce_scalar(value)
    return root


def _coerce_scalar(value: str) -> Any:
    if value in ("null", "None", "~", ""):
        return None
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    # Strip quotes
    if (value.startswith('"') and value.endswith('"')) or \
       (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


# ---------------------------------------------------------------------------
# PresetManager
# ---------------------------------------------------------------------------
class PresetManager:
    """Loads, saves, lists, deletes, and renames Preset objects.

    The manager owns a single directory.  Each preset is persisted as a
    single file (``<safe_name>.yaml`` or ``<safe_name>.json``).  YAML is
    preferred; JSON is used only when PyYAML is unavailable at save time.
    """

    SCHEMA_VERSION = 1

    def __init__(self, presets_dir: str):
        self._dir = presets_dir
        os.makedirs(self._dir, exist_ok=True)
        self._cache: Optional[Dict[str, Preset]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def directory(self) -> str:
        return self._dir

    # ------------------------------------------------------------------
    # Loading / saving
    # ------------------------------------------------------------------
    def load(self) -> Dict[str, Preset]:
        """Load every preset from disk into a name->Preset dict.

        The result is cached.  Call :meth:`refresh` to force a reload.
        """
        if self._cache is not None:
            return self._cache

        result: Dict[str, Preset] = {}
        if not os.path.isdir(self._dir):
            self._cache = result
            return result

        for fname in sorted(os.listdir(self._dir)):
            if not (fname.endswith(".yaml") or fname.endswith(".yml")
                    or fname.endswith(".json")):
                continue
            fpath = os.path.join(self._dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    text = fh.read()
                if fname.endswith(".json"):
                    data = json.loads(text)
                else:
                    data = _load_yaml(text)
                if not isinstance(data, dict):
                    continue
                preset = Preset.from_dict(data)
                if not preset.name:
                    preset.name = os.path.splitext(fname)[0]
                # Detect collisions: keep the first one loaded
                if preset.name not in result:
                    result[preset.name] = preset
            except Exception:
                # Corrupt preset file - skip it but do not crash.
                continue

        self._cache = result
        return result

    def refresh(self) -> Dict[str, Preset]:
        """Force a reload from disk."""
        self._cache = None
        return self.load()

    def save(self, preset: Preset) -> str:
        """Persist ``preset`` to disk and update the in-memory cache.

        Returns the relative path of the saved file.
        Raises ValueError if the preset name is invalid.
        """
        self.validate_name(preset.name)

        data = {
            "schema_version": self.SCHEMA_VERSION,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **preset.to_dict(),
        }

        # Prefer YAML, fall back to JSON.
        try:
            import yaml  # noqa: F401
            ext = ".yaml"
            payload = _dump_yaml(data)
        except ImportError:
            ext = ".json"
            payload = json.dumps(data, indent=2, ensure_ascii=False)

        fname = _safe_filename(preset.name) + ext
        fpath = os.path.join(self._dir, fname)
        # Remove any sibling preset files with a different extension so we
        # do not leave stale copies lying around.
        for old_ext in (".yaml", ".yml", ".json"):
            old_path = os.path.join(self._dir,
                                    _safe_filename(preset.name) + old_ext)
            if old_path != fpath and os.path.isfile(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass

        with open(fpath, "w", encoding="utf-8") as fh:
            fh.write(payload)

        # Update cache
        if self._cache is None:
            self.load()
        assert self._cache is not None
        self._cache[preset.name] = preset

        return os.path.relpath(fpath, os.path.dirname(self._dir))

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------
    def list(self) -> List[Preset]:
        """Return a list of all presets, sorted alphabetically by name."""
        presets = list(self.load().values())
        presets.sort(key=lambda p: p.name.lower())
        return presets

    def get(self, name: str) -> Optional[Preset]:
        """Return the preset with ``name`` or None if not found."""
        return self.load().get(name)

    def delete(self, name: str) -> bool:
        """Delete a preset by name.  Returns True if a file was removed."""
        self.validate_name(name)
        removed = False
        for ext in (".yaml", ".yml", ".json"):
            fpath = os.path.join(self._dir, _safe_filename(name) + ext)
            if os.path.isfile(fpath):
                try:
                    os.remove(fpath)
                    removed = True
                except OSError:
                    pass
        if self._cache is not None and name in self._cache:
            del self._cache[name]
        return removed

    def rename(self, old_name: str, new_name: str) -> Preset:
        """Rename a preset.

        Loads the preset, deletes the old file, saves under the new name.
        Raises KeyError if ``old_name`` does not exist.
        Raises ValueError if ``new_name`` is invalid or already in use.
        """
        preset = self.get(old_name)
        if preset is None:
            raise KeyError("Preset not found: {0}".format(old_name))
        self.validate_name(new_name)
        if new_name != old_name and self.get(new_name) is not None:
            raise ValueError("A preset named '{0}' already exists.".format(new_name))

        preset.name = new_name
        self.delete(old_name)
        # Force the cache to drop the old entry before saving under the new
        # name (save() updates the cache itself).
        self.save(preset)
        return preset

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self, preset: Preset) -> List[str]:
        """Return a list of human-readable validation warnings.

        An empty list means the preset is valid.
        """
        warnings: List[str] = []
        try:
            self.validate_name(preset.name)
        except ValueError as exc:
            warnings.append(str(exc))
        param_warnings = preset.parameters.validate()
        warnings.extend(param_warnings)
        return warnings

    @staticmethod
    def validate_name(name: str) -> None:
        """Raise ValueError if ``name`` is not a valid preset name."""
        if not name or not name.strip():
            raise ValueError("Preset name must not be empty.")
        if len(name) > 120:
            raise ValueError("Preset name must be 120 characters or fewer.")
        # Disallow filesystem-dangerous characters
        if any(c in name for c in ('/', '\\', ':', '*', '?', '"', '<', '>', '|')):
            raise ValueError(
                "Preset name may not contain / \\ : * ? \" < > |")
