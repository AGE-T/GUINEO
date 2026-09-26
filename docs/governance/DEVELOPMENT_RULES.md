# SpeechStudio Development Rules

> Mandatory rules for every contributor.  Pull requests that violate
> any rule MUST be rejected.  These rules exist to keep the codebase
> maintainable, portable, and aligned with the architecture manifest.

## 1. Architecture

1. **Three layers, no shortcuts.** UI -> Engine -> External.  See
   `docs/governance/architecture_manifest.yaml` section 2.
2. **UI never imports torch / transformers / engine internals.** Use
   the `Engine` facade only.
3. **Engine never imports PySide6.** Engine modules must be importable
   in a headless environment.
4. **No circular imports.** If you need a forward reference, use
   `from __future__ import annotations` and string type hints.

## 2. Prompt Construction

5. **PromptBuilder is the only token constructor.** No other module
   may call `make_token()` or build `<|category:tag|>` strings.
6. **Engine._execute_generation trusts request.text.** Do NOT add
   heuristics like `"<|" in text` to bypass PromptBuilder.
7. **UI never concatenates Higgs tokens.** The UI inserts human-readable
   markers (`{sfx:Laughter:Haha}`, `{pause}`); only PromptBuilder
   converts them to real tokens.

## 3. Concurrency

8. **No Qt calls from worker threads.** Workers emit Signals or
   schedule work via `QTimer.singleShot(0, fn)` invoked on the UI
   thread.
9. **One generation at a time.** The worker pool enforces this; the
   BatchManager retries with a 200ms delay if it sees "already
   running".
10. **Audio playback is independent.** Playback never blocks generation.

## 4. Persistence

11. **YAML is the primary format.** JSON is a fallback for environments
    without PyYAML.  Every loader must accept both.
12. **Nothing writes outside the project directory.** All outputs go
    to `outputs/`, `settings/`, `voices/`, or `logs/`.
13. **Timestamps are filename-safe.** Use `%Y%m%d_%H%M%S`.

## 5. Error Handling

14. **Every generation returns a GenerationResult.** Even failures.
15. **Engine errors are structured.** Use the `errors.py` dataclass
    hierarchy (`EngineError`, `ValidationFailed`, ...).
16. **UI errors are user-facing.** Show `QMessageBox` with a clear
    message and a suggested action.

## 6. Logging

17. **Use `get_logger(component)`.** Never `print()`.
18. **Pipeline verification is logged at every stage.** See
    `architecture_manifest.yaml` section 7.
19. **No secrets in logs.** Logs may be shared for debugging.

## 7. Testing

20. **All `tools/verify_*.py` scripts must pass** before a release.
21. **New features need an entry in `feature_registry.yaml`.**
22. **Architecture changes need an ADR** in `docs/adr/`.

## 8. Code Style

23. **`from __future__ import annotations`** at the top of every module.
24. **Type hints on every public function.**
25. **Docstrings on every public class and method.**
26. **No emojis in code or UI** unless explicitly requested.
27. **Use `.format()` for string formatting** (matches the existing
    codebase style).

## 9. UI

28. **All user-facing strings go through Qt.** No raw `print()` to the
    console for user messages.
29. **Widgets are owned by their parent.** Never store widget
    references in module-level globals.
30. **Long operations run on a worker thread.** The UI thread must
    stay responsive.

## 10. Verification

31. **Run `python3 -m py_compile <file>` before committing.**
32. **Run `tools/verify_architecture.py` before merging.**
33. **Run `tools/verify_integration.py` before releasing.**

---

Failure to follow these rules risks regressing the project to the
pre-governance state that triggered this rebuild.
