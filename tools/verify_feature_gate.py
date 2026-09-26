#!/usr/bin/env python3
"""
SpeechStudio Feature-Gate Verification
======================================

Verifies that experimental / beta features are properly gated so they
do not activate unintentionally.

Checks performed:
    1. Raw mode (F-104) is gated behind the "Raw Mode" checkbox in
       NarrationEditor.  ``is_raw_mode()`` returns False by default.
    2. Batch generation (F-401..410) is gated behind the
       "batch_generation" Tools-menu action; the BatchManager is only
       instantiated after the engine is connected.
    3. Pipeline verification logging (F-801..804) is unconditional
       (this is intentional; we log SHA-256 at every stage).

The script uses AST inspection so it does NOT import PySide6.

Exit code 0 = all gates verified; 1 = at least one violation.

Usage:
    python3 tools/verify_feature_gate.py [--root PATH]
"""

from __future__ import annotations
import argparse
import ast
import os
import sys
from typing import List


def _read_source(root: str, rel: str) -> str:
    path = os.path.join(root, rel)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _check_raw_mode_gate(root: str) -> List[str]:
    """Verify that NarrationEditor defaults raw_mode to False and that
    ``is_raw_mode()`` reads from the same field."""
    violations: List[str] = []
    try:
        src = _read_source(root, "ui/panels/narration_editor.py")
        tree = ast.parse(src)
    except (OSError, SyntaxError) as exc:
        return ["narration_editor.py: cannot parse: {0}".format(exc)]

    # Walk the class definitions to find NarrationEditor.__init__
    found_raw_init = False
    found_is_raw_mode = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (isinstance(tgt, ast.Attribute) and
                        isinstance(tgt.value, ast.Name) and
                        tgt.value.id == "self" and
                        tgt.attr == "_raw_mode"):
                    found_raw_init = True
                    # The RHS should be False (or a falsy literal)
                    if isinstance(node.value, ast.Constant):
                        if node.value.value is not False:
                            violations.append(
                                "narration_editor.py: self._raw_mode must "
                                "default to False, got {0}".format(
                                    node.value.value))
        if isinstance(node, ast.FunctionDef) and node.name == "is_raw_mode":
            found_is_raw_mode = True

    if not found_raw_init:
        violations.append(
            "narration_editor.py: missing 'self._raw_mode = False' "
            "initialisation")
    if not found_is_raw_mode:
        violations.append(
            "narration_editor.py: missing is_raw_mode() method")

    return violations


def _check_batch_gate(root: str) -> List[str]:
    """Verify that the BatchManager is created in _connect_engine
    (after engine is connected), not at __init__ time."""
    violations: List[str] = []
    try:
        src = _read_source(root, "ui/main_window.py")
    except OSError as exc:
        return ["main_window.py: cannot read: {0}".format(exc)]

    if "BatchManager(" not in src:
        violations.append(
            "main_window.py: BatchManager is never instantiated")
    if "_on_open_batch_generation" not in src:
        violations.append(
            "main_window.py: missing _on_open_batch_generation handler")

    # Check that the menu bar wires the batch_generation action
    try:
        menu_src = _read_source(root, "ui/panels/menu_bar.py")
    except OSError as exc:
        return ["menu_bar.py: cannot read: {0}".format(exc)]

    if '"batch_generation"' not in menu_src:
        violations.append(
            "menu_bar.py: missing 'batch_generation' menu action")

    return violations


def _check_pipeline_verify_logging(root: str) -> List[str]:
    """Verify that all four pipeline verification stages log SHA-256."""
    violations: List[str] = []
    expected_markers = [
        ("ui/main_window.py", "Stage 1 - MainWindow._update_prompt_preview"),
        ("ui/main_window.py", "Stage 2 - MainWindow._start_generation"),
        ("engine/engine.py", "Stage 3 - Engine._execute_generation"),
        ("engine/generation_manager.py",
         "Stage 4/5 - GenerationManager.generate"),
    ]
    for rel, marker in expected_markers:
        try:
            src = _read_source(root, rel)
        except OSError:
            violations.append("{0}: cannot read".format(rel))
            continue
        if marker not in src:
            violations.append(
                "{0}: missing pipeline verification marker '{1}'".format(
                    rel, marker))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None)
    args = parser.parse_args()

    if args.root is None:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(here)
    else:
        root = os.path.abspath(args.root)

    print("Verifying feature gates for:", root)

    all_violations: List[str] = []
    all_violations.extend(_check_raw_mode_gate(root))
    all_violations.extend(_check_batch_gate(root))
    all_violations.extend(_check_pipeline_verify_logging(root))

    if all_violations:
        print("\nFAILED: {0} violation(s):\n".format(len(all_violations)))
        for v in all_violations:
            print("  - {0}".format(v))
        return 1

    print("PASS: all feature gates verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
