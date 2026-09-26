#!/usr/bin/env python3
"""
SpeechStudio Compile Verification
=================================

Compiles every Python file under the SpeechStudio project root and
reports any syntax errors.  Uses ``py_compile`` so this is a static
check (no imports are executed).

Exit code 0 = every file compiles; 1 = at least one failure.

Usage:
    python3 tools/verify_compile.py [--root PATH]
"""

from __future__ import annotations
import argparse
import os
import py_compile
import sys
from typing import List


def _walk_python_files(root: str) -> List[str]:
    """Yield every .py file under root, excluding __pycache__ and venvs."""
    out: List[str] = []
    skip_dirs = {"__pycache__", ".venv", "venv", ".git", "node_modules",
                 "models", "spec", "research"}
    for dirpath, dirnames, filenames in os.walk(root):
        if any(part in skip_dirs for part in dirpath.split(os.sep)):
            continue
        for f in filenames:
            if f.endswith(".py"):
                out.append(os.path.join(dirpath, f))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None,
                        help="SpeechStudio project root (auto-detected)")
    args = parser.parse_args()

    if args.root is None:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(here)
    else:
        root = os.path.abspath(args.root)

    print("Compiling every .py file under:", root)

    files = _walk_python_files(root)
    if not files:
        print("WARNING: no .py files found.")
        return 1

    failures: List[str] = []
    for path in sorted(files):
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append("{0}: {1}".format(
                os.path.relpath(path, root), exc))

    print("Compiled {0} file(s).".format(len(files)))
    if failures:
        print("\nFAILED: {0} compile error(s):\n".format(len(failures)))
        for f in failures:
            print("  - {0}".format(f))
        return 1

    print("PASS: every file compiles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
