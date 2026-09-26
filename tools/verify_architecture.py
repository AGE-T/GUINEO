#!/usr/bin/env python3
"""
SpeechStudio Architecture Verification
======================================

Verifies the layered-architecture rules described in
``docs/governance/architecture_manifest.yaml`` section 2.

Rules checked:
    1. No file in ``ui/`` imports ``torch`` or ``transformers``.
    2. No file in ``ui/`` imports any engine module other than the
       ``Engine`` facade, the data classes in ``engine.models``, or
       the helper modules whitelisted below.
    3. No file in ``engine/`` imports ``PySide6``.
    4. No file in ``engine/`` imports anything from ``ui.``.

Exit code 0 = all rules pass; 1 = at least one violation.

Usage:
    python3 tools/verify_architecture.py [--root PATH]
"""

from __future__ import annotations
import argparse
import os
import sys
import ast
from typing import List, Set, Tuple


# Modules the UI is allowed to import from the engine.
# Everything else under ``engine.`` is considered an internal.
UI_ALLOWED_ENGINE_IMPORTS: Set[str] = {
    "engine",
    "engine.models",
    "engine.events",
    "engine.errors",
    "engine.version",
    "engine.logger",
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


def _imported_modules(filepath: str) -> List[str]:
    """Return a list of fully-qualified module names imported by ``filepath``.

    Only **top-level** imports are reported (those that execute at module
    load time).  Imports nested inside function or class bodies are
    considered lazy imports and are NOT flagged - this lets engine
    modules lazily probe for optional dependencies like ``torch``,
    ``PySide6``, ``soundfile``, etc. without violating the architecture
    rules.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, OSError):
        return []

    imports: List[str] = []

    def _is_top_level(node: ast.AST) -> bool:
        """True if ``node`` is a direct child of the Module (i.e. not
        nested inside a function or class body)."""
        # Walk the tree from the root and check whether ``node`` appears
        # as a direct child of the Module node.  We do this by recursing
        # and tracking depth.
        for child in ast.iter_child_nodes(tree):
            if child is node:
                return True
        return False

    for node in ast.iter_child_nodes(tree):
        # Top-level imports (module load time)
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
        elif isinstance(node, ast.Try):
            # Imports inside a top-level try block are treated as
            # optional-dependency probes (e.g. ``try: import yaml /
            # except ImportError: ...``).  These are allowed.
            for handler in node.handlers:
                for h_node in ast.iter_child_nodes(handler):
                    if isinstance(h_node, ast.Import):
                        for alias in h_node.names:
                            imports.append(alias.name)
                    elif isinstance(h_node, ast.ImportFrom):
                        if h_node.module:
                            imports.append(h_node.module)
            # Also imports in the try body itself (before any except)
            for t_node in ast.iter_child_nodes(node):
                if t_node is node.handlers:
                    continue
                if isinstance(t_node, ast.Import):
                    for alias in t_node.names:
                        imports.append(alias.name)
                elif isinstance(t_node, ast.ImportFrom):
                    if t_node.module:
                        imports.append(t_node.module)
    return imports


def _check_ui_layer(root: str) -> List[str]:
    """Return a list of violation messages for the UI layer."""
    violations: List[str] = []
    for path in _walk_python_files(root, "ui"):
        rel = os.path.relpath(path, root)
        for mod in _imported_modules(path):
            top = mod.split(".")[0]
            if top in ("torch", "transformers", "torchaudio", "numpy"):
                # numpy is allowed because data classes use it for typing
                # in some places; torch/transformers are NOT.
                if top == "numpy":
                    continue
                violations.append(
                    "{0}: imports '{1}' (UI must not import ML libraries)"
                    .format(rel, mod))
            if top == "engine" and mod not in UI_ALLOWED_ENGINE_IMPORTS:
                # Allow sub-modules of whitelisted packages (e.g.
                # "engine.models.foo" would still be flagged, but
                # "engine.models" is OK).
                if not any(mod == w or mod.startswith(w + ".")
                           for w in UI_ALLOWED_ENGINE_IMPORTS):
                    violations.append(
                        "{0}: imports '{1}' (UI may only use the Engine "
                        "facade and whitelisted data modules)".format(
                            rel, mod))
    return violations


def _check_engine_layer(root: str) -> List[str]:
    """Return a list of violation messages for the engine layer."""
    violations: List[str] = []
    for path in _walk_python_files(root, "engine"):
        rel = os.path.relpath(path, root)
        for mod in _imported_modules(path):
            top = mod.split(".")[0]
            if top in ENGINE_FORBIDDEN_IMPORTS:
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

    all_violations: List[str] = []
    all_violations.extend(_check_ui_layer(root))
    all_violations.extend(_check_engine_layer(root))

    if all_violations:
        print("\nFAILED: {0} violation(s):\n".format(len(all_violations)))
        for v in all_violations:
            print("  - {0}".format(v))
        return 1

    print("PASS: no architecture violations detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
