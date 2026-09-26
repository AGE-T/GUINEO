#!/usr/bin/env python3
"""SpeechStudio Transport Renderer validation — RETIRED (P3.25).

RETIRED per audit SS-M20: this tool statically validated the deprecated
GPU transport renderer (ui/panels/transport_gl.py, pre-rewrite V12/V6
architecture). The transport was rewritten; transport_gl.py is
DEPRECATED and slated for removal, so the tool's 183 assertions were
permanently red (180/183 failing) against the current build — a stale
"red" that misrepresented project health without protecting anything.

The live verification set is:
    tools/verify_architecture.py
    tools/verify_compile.py
    tools/verify_feature_gate.py
    tools/verify_functional_integrity.py
    tools/verify_integration.py

This stub exits 0 while claiming NOTHING — it exists so historical
documentation referencing the tool resolves to an honest explanation.
"""

import sys

if __name__ == "__main__":
    print("verify_transport_gl.py: RETIRED (deprecated renderer, "
          "audit SS-M20).")
    print("The live verification scripts are listed in this file's "
          "docstring.")
    sys.exit(0)
