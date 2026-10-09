#!/usr/bin/env python3
"""
SpeechStudio Architecture Verification
======================================

Verifies the layered-architecture rules described in
``docs/governance/architecture_manifest.yaml`` section 2.

Rules checked:
    1. No file in ``ui/`` imports ``torch`` or ``transformers``.
    2. No file in ``ui/`` imports any engine module other than the
       ``Engine`` facade root, the BY-DESIGN whitelisted modules, or
       the DOCUMENTED BASELINE-DEBT modules (see the two tiers below —
       both recorded in the manifest).
    3. No file in ``engine/`` imports ``PySide6``/PyQt or anything
       from ``ui.``, with exactly two per-file documented exceptions
       (see ``ENGINE_QT_EXCEPTIONS``).

P3.45.5 corrections (verification/test hygiene round):
    * The old whitelist contained the top-level name ``"engine"``,
      which made the ``engine.*`` prefix check vacuous — ANY engine
      submodule passed. The facade root is now allowed ONLY as the
      exact import ``engine`` (plus its re-exported names); every
      ``engine.<module>`` import must be explicitly listed.
    * Import scanning now covers the FULL AST — lazy imports nested
      in function/class bodies (previously exempt) can no longer
      silently bypass the rules. Relative imports are resolved to
      absolute module paths, and ``from engine import NAME`` is
      resolved against the actual ``engine/`` package contents (a
      NAME that is a submodule counts as ``engine.NAME``; a facade
      re-export such as ``Engine`` counts as the facade root).
    * The engine "no Qt" rule gained two narrowly-scoped, documented
      exceptions (version reporting + the known QTimer debt) so that
      the rule is now enforced for every OTHER import — including
      lazy ones — instead of being bypassed by all lazy imports.

The two-tier whitelist distinguishes INTENT from DEBT:
    * ``UI_ALLOWED_ENGINE_IMPORTS`` — by-design allowances (data
      classes, display lookups, managers owned by their dialogs).
    * ``UI_BASELINE_ENGINE_IMPORTS`` — direct engine-module imports
      that already existed at the P3.45.5 baseline and are recorded
      here as ARCHITECTURAL DEBT (the manifest documents them; the
      D-R10 facade-style decision is deliberate future work, NOT
      started). The verifier enforces "no NEW direct imports": any
      engine module outside both tiers is a violation.

Exit code 0 = all rules pass; 1 = at least one violation.

Usage:
    python3 tools/verify_architecture.py [--root PATH]
"""

from __future__ import annotations
import argparse
import os
import sys
import ast
from typing import Dict, List, Set

# ---------------------------------------------------------------------------
# UI -> engine import policy
# ---------------------------------------------------------------------------

# Tier 1 — BY-DESIGN allowances. The bare facade root "engine" is NOT
# listed (it is allowed separately, as the exact import only) so that
# this set can never re-create the old vacuous prefix check.
UI_ALLOWED_ENGINE_IMPORTS: Set[str] = {
    "engine.models",                # read-only data classes
    "engine.events",                # event types for signal wiring
    "engine.errors",                # engine exception types
    "engine.version",               # version/build reporting
    "engine.logger",                # structured logging
    "engine.higgs_tokens",          # for display lookups (EMOTIONS, ...)
    "engine.narration_blocks",      # data classes for the editor
    "engine.narration_block_manager",
    "engine.prompt_optimizer",      # used by NarrationEditor for preview
    "engine.prompt_builder",        # used by NarrationEditor for preview
    "engine.preset_manager",        # data class + manager
    "engine.batch_manager",         # data class + manager
    "engine.settings_manager",      # used by Settings dialog
    "engine.history_manager",       # used by history tab
    "engine.voice_manager",         # used by voice library
    "engine.audio_manager",         # used by waveform player
}

# Tier 2 — DOCUMENTED BASELINE DEBT (P3.45.5 reconciliation). Direct
# engine-module imports that already exist in the UI at the baseline.
# Recorded so the verifier enforces "no NEW direct imports" while the
# facade consolidation decision (D-R10 family) remains future work.
# Adding an entry requires a deliberate manifest update.
UI_BASELINE_ENGINE_IMPORTS: Set[str] = {
    "engine.audio_provenance",      # slot/coverage summaries (P3.44.5+)
    "engine.character_colors",      # speaker colour derivation
    "engine.combined_audio",        # combine/concatenate helpers
    "engine.duration_estimation",   # canonical estimator (P3.45.2B)
    "engine.narration_splitter",    # splitter data types
    "engine.output_guard",          # preflight verdicts (P3.45.2A)
    "engine.project_exporter",      # project export
    "engine.project_manager",       # project/scene persistence
    "engine.prompt_state",          # prompt token state
    "engine.recents_manager",       # recents list
    "engine.scene_persistence",     # scene save/load
}

# ---------------------------------------------------------------------------
# Engine -> Qt exceptions (documented; see architecture_manifest.yaml §2)
# ---------------------------------------------------------------------------

# Per-file, per-module exceptions to the engine "no Qt" rule. Every
# other Qt/PyQt import — top-level OR lazy — remains a violation.
ENGINE_QT_EXCEPTIONS: Dict[str, Set[str]] = {
    # Version REPORTING only: get_qt_version() probes the PySide6 root
    # package for __version__ (the same pattern as the torch /
    # transformers probes in the same file). No Qt machinery is
    # imported or instantiated; the import is exception-guarded.
    "engine/version.py": {"PySide6"},
    # P3.45.5 audit (known debt): the busy-retry and inter-job pacing
    # timers call QTimer.singleShot directly; both paths run on the UI
    # thread via the marshal_to_ui contract. The documented future task
    # is routing the scheduler through a UI-injected callable. No other
    # Qt module may appear in this file.
    "engine/batch_manager.py": {"PySide6.QtCore"},
}

# Modules the engine is FORBIDDEN from importing.
ENGINE_FORBIDDEN_IMPORTS: Set[str] = {
    "PySide6",
    "PyQt5",
    "PyQt6",
    "ui",
}


def _walk_python_files(root: str, subdir: str) -> List[str]:
    """Yield every .py file under root/subdir, excluding __pycache__."""
    base = os.path.join(root, subdir)
    if not os.path.isdir(base):
        return []
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        if "__pycache__" in dirpath:
            continue
        for f in filenames:
            if f.endswith(".py"):
                out.append(os.path.join(dirpath, f))
    return out


def _module_package(root: str, filepath: str, layer: str) -> List[str]:
    """Package path of a module file, e.g. ui/panels/foo.py ->
    ["ui", "panels"] (used to resolve relative imports)."""
    rel = os.path.relpath(filepath, os.path.join(root, layer))
    parts = [layer] + [p for p in rel.split(os.sep)[:-1]]
    return parts


def _resolve_import_from(pkg: List[str], node: ast.ImportFrom
                         ) -> List[str]:
    """Absolute module names referenced by an ImportFrom node."""
    level = node.level
    if level == 0:
        return [node.module] if node.module else []
    # Relative import: strip (level-1) trailing components from the
    # package path. A level that escapes the top-level package cannot
    # be resolved here and is ignored (such code would be broken).
    if level > 1:
        if len(pkg) < (level - 1):
            return []
        base = pkg[: len(pkg) - (level - 1)]
    else:
        base = list(pkg)
    prefix = ".".join(base)
    if node.module:
        return [(prefix + "." + node.module) if prefix else node.module]
    # ``from . import a, b`` — the names may themselves be modules.
    return [(prefix + "." + a.name) if prefix else a.name
            for a in node.names]


def _imported_modules(filepath: str, root: str) -> List[str]:
    """Return the fully-qualified module names imported by ``filepath``.

    P3.45.5: the FULL AST is scanned — imports nested inside function
    or class bodies (lazy imports) are included, so they can no longer
    bypass the architecture rules. Relative imports are resolved to
    absolute paths. For ``from engine import NAME``, NAME is resolved
    against the actual ``engine/`` package contents: a submodule NAME
    counts as ``engine.NAME``; a facade re-export (a class/function
    such as ``Engine``) counts as the facade root and is not expanded.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, OSError):
        return []

    pkg = _module_package(root, filepath, filepath_rel_layer(root,
                                                             filepath))
    mods: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_import_from(pkg, node)
            mods.extend(resolved)
            # ``from engine import NAME`` (absolute OR relative-resolved):
            # a NAME that is an actual engine submodule counts as
            # ``engine.NAME``; a facade re-export (a class/function such
            # as ``Engine``) counts as the facade root and is not
            # expanded — it can never smuggle a submodule past the check.
            if "engine" in resolved:
                for alias in node.names:
                    sub = alias.name
                    if os.path.isfile(os.path.join(root, "engine",
                                                   sub + ".py")) \
                       or os.path.isdir(os.path.join(root, "engine", sub)):
                        mods.append("engine." + sub)
    return mods


def filepath_rel_layer(root: str, filepath: str) -> str:
    """The first path component of filepath relative to root (the
    layer subdir, e.g. "ui" or "engine")."""
    rel = os.path.relpath(filepath, root)
    return rel.split(os.sep)[0]


def _engine_import_allowed(mod: str) -> bool:
    """True when a ``ui`` file may import ``mod`` from the engine."""
    if mod == "engine":
        # The facade root import (``from engine import Engine``) —
        # allowed ONLY as this exact name; "engine.<anything>" must be
        # explicitly whitelisted below.
        return True
    allowed = UI_ALLOWED_ENGINE_IMPORTS | UI_BASELINE_ENGINE_IMPORTS
    return any(mod == w or mod.startswith(w + ".") for w in allowed)


def _check_ui_layer(root: str,
                    debt_usage: Dict[str, Set[str]] = None
                    ) -> List[str]:
    """Return a list of violation messages for the UI layer.

    When ``debt_usage`` is provided it is filled with
    {baseline-debt module: {files importing it}} for the informational
    report printed by ``main()`` (transparency, not enforcement).
    """
    violations: List[str] = []
    for path in _walk_python_files(root, "ui"):
        rel = os.path.relpath(path, root)
        for mod in _imported_modules(path, root):
            top = mod.split(".")[0]
            if top in ("torch", "transformers", "torchaudio", "numpy"):
                # numpy is allowed because data classes use it for typing
                # in some places; torch/transformers are NOT.
                if top == "numpy":
                    continue
                violations.append(
                    "{0}: imports '{1}' (UI must not import ML libraries)"
                    .format(rel, mod))
            if top == "engine" and not _engine_import_allowed(mod):
                violations.append(
                    "{0}: imports '{1}' (UI may import the Engine facade "
                    "root and only the whitelisted/documented-baseline "
                    "engine modules — see architecture_manifest.yaml §2)"
                    .format(rel, mod))
                continue
            if (debt_usage is not None and top == "engine"
                    and mod in UI_BASELINE_ENGINE_IMPORTS):
                debt_usage.setdefault(mod, set()).add(rel.replace(os.sep, "/"))
    return violations


def _check_engine_layer(root: str,
                        known_debt: List[str] = None
                        ) -> List[str]:
    """Return a list of violation messages for the engine layer.

    When ``known_debt`` is provided it is filled with the documented
    exception hits (transparency: every run prints them, so the debt
    cannot hide; it just does not fail the gate).
    """
    violations: List[str] = []
    for path in _walk_python_files(root, "engine"):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        for mod in _imported_modules(path, root):
            top = mod.split(".")[0]
            if top in ENGINE_FORBIDDEN_IMPORTS:
                if mod in ENGINE_QT_EXCEPTIONS.get(rel, set()):
                    if known_debt is not None:
                        note = ("{0}: imports '{1}' (documented exception "
                                "— see architecture_manifest.yaml §2)"
                                .format(rel, mod))
                        if note not in known_debt:
                            known_debt.append(note)
                    continue
                violations.append(
                    "{0}: imports '{1}' (Engine must not import Qt or UI)"
                    .format(rel, mod))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None,
                        help="SpeechStudio project root (auto-detected)")
    args = parser.parse_args()

    if args.root is None:
        # tools/verify_architecture.py -> project root is two levels up.
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(here)
    else:
        root = os.path.abspath(args.root)

    print("Verifying architecture for:", root)

    debt_usage: Dict[str, Set[str]] = {}
    known_debt: List[str] = []
    all_violations: List[str] = []
    all_violations.extend(_check_ui_layer(root, debt_usage))
    all_violations.extend(_check_engine_layer(root, known_debt))

    # Transparency block: what is DOCUMENTED DEBT (permitted baseline),
    # not a violation. This is the manifest's "existing architectural
    # debt" made visible on every run.
    if debt_usage:
        print("\nDocumented UI baseline-debt imports (permitted, "
              "recorded in architecture_manifest.yaml §2):")
        for mod in sorted(debt_usage):
            files = sorted(debt_usage[mod])
            print("  - {0}  ({1} file{2}: {3})".format(
                mod, len(files), "" if len(files) == 1 else "s",
                ", ".join(files)))
    if known_debt:
        print("\nDocumented engine-side Qt exceptions (permitted, "
              "recorded in architecture_manifest.yaml §2):")
        for note in known_debt:
            print("  - {0}".format(note))

    if all_violations:
        print("\nFAILED: {0} violation(s):\n".format(len(all_violations)))
        for v in all_violations:
            print("  - {0}".format(v))
        return 1

    print("\nPASS: no architecture violations detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
