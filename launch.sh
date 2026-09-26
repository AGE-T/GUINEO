#!/usr/bin/env bash
# ============================================================
#  GUINEO - Linux Launcher (development / testing)
# ------------------------------------------------------------
#  The production target is Windows (launch.bat).
#  This script is provided so the bootstrap logic and the
#  PySide6 interface can be exercised on Linux during
#  development.
# ============================================================

set -e

# --- Resolve the project root (this file's directory) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- Locate Python 3 (prefer python3, fall back to python) ---
if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "[GUINEO] Python was not found on this system."
    echo "[GUINEO] Please install Python 3.11."
    exit 1
fi

# --- Run the bootstrap ---
"$PYTHON" bootstrap.py
if [ $? -ne 0 ]; then
    echo ""
    echo "[GUINEO] Bootstrap failed. See logs/bootstrap.log for details."
    exit 1
fi
