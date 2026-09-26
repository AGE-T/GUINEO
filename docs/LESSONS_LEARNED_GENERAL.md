# General Development Lessons — Applicable to Future Projects

**Source:** SpeechStudio development (2025-06-28 to 2025-06-29)  
**Purpose:** Generalized lessons learned during development that apply to any Python + Qt (PySide6/PyQt) desktop application project.

---

## 1. Threading: Cross-Thread Communication

### Lesson: QTimer.singleShot does NOT work from worker threads

**The Problem:** Calling `QTimer.singleShot(0, fn)` from a `ThreadPoolExecutor` worker thread silently fails. The timer is created on the worker thread, which has no Qt event loop to process it. The callback is never executed.

**The Solution:** Use a Qt signal-based marshaller:

```python
class ThreadMarshaller(QObject):
    """Thread-safe UI marshalling via Qt signal."""
    _call = Signal(object)  # carries a callable

    def __init__(self, parent=None):
        super().__init__(parent)
        self._call.connect(self._execute)

    def _execute(self, fn):
        fn()

    def marshal(self, fn):
        """Call from ANY thread. fn executes on the UI thread."""
        self._call.emit(fn)
```

**Why it works:** Qt signals automatically marshal cross-thread emissions. When a signal is emitted from a worker thread, the connected slot runs on the receiver's thread (the UI thread), which has an event loop.

**General rule:** NEVER use `QTimer.singleShot` from a non-UI thread. Always use signals for cross-thread communication.

---

### Lesson: concurrent.futures done-callbacks run on the worker thread

When you call `future.add_done_callback(fn)`, `fn` runs on the thread that completed the future — NOT the UI thread. If the callback needs to update UI state, it must marshal to the UI thread.

**Safe pattern:**
```python
def on_done(future):
    # Process result on worker thread (thread-safe with locks)
    result = future.result()
    with lock:
        update_shared_state(result)
    # Marshal ONLY the UI update to the UI thread
    marshaller.marshal(update_ui)
```

**Unsafe pattern:**
```python
def on_done(future):
    result = future.result()
    # DON'T touch Qt widgets here — wrong thread!
    label.setText("Done")
```

---

### Lesson: ThreadPoolExecutor "already running" race condition

When using `ThreadPoolExecutor(max_workers=1)`, the single worker thread may still be running done-callbacks when you try to submit the next task. The `submit()` call may raise `RuntimeError("A generation is already running.")`.

**Solution:** Catch the specific RuntimeError, revert the job to pending, and retry after a short delay:

```python
try:
    future = executor.submit(task)
except RuntimeError as exc:
    if "already running" in str(exc).lower():
        # Retry after delay
        QTimer.singleShot(300, retry_function)
        return
    raise  # Different error — propagate
```

---

## 2. Qt/PySide6 API Gotchas

### Lesson: ExtraSelection is on QTextEdit, not QPlainTextEdit

In PySide6, `ExtraSelection` is defined on `QTextEdit`, not `QPlainTextEdit` — even though `QPlainTextEdit` inherits from `QTextEdit`.

```python
# WRONG — AttributeError
sel = QPlainTextEdit.ExtraSelection()

# CORRECT
from PySide6.QtWidgets import QTextEdit
sel = QTextEdit.ExtraSelection()
```

**General rule:** Always verify PySide6 API availability on the specific class, not just the parent.

---

### Lesson: QShortcut is in QtGui, not QtWidgets

In PySide6 6.x, `QShortcut` was moved from `PySide6.QtWidgets` to `PySide6.QtGui`.

```python
# WRONG — ImportError
from PySide6.QtWidgets import QShortcut

# CORRECT
from PySide6.QtGui import QShortcut
```

---

### Lesson: Init order matters when parent __init__ calls overridden methods

If a parent class `__init__` calls a method that the child overrides, the child's attributes must be set BEFORE `super().__init__()`:

```python
class Child(Parent):
    def __init__(self):
        # Set attributes BEFORE super().__init__()
        # because Parent.__init__ calls self.update_margins()
        # which we override and which references self._data
        self._data = []
        self._enabled = True
        super().__init__()
        # Other initialization that doesn't need to exist
        # before the parent constructor runs
        self._timer = QTimer(self)
```

---

### Lesson: LineNumberArea positioning with custom margins

When adding custom viewport margins (e.g., for a block gutter), the `LineNumberArea` widget must be positioned at `x=0`, not at `contentsRect().left()` — because `contentsRect().left()` shifts when you add margins:

```python
def resizeEvent(self, event):
    super().resizeEvent(event)
    cr = self.contentsRect()
    # Position at x=0, NOT cr.left() — cr.left() shifts with margins
    self._line_number_area.setGeometry(
        QRect(0, cr.top(), self.line_number_area_width(), cr.height()))
```

---

## 3. Architecture Patterns

### Lesson: Single source of truth for prompt building

If the Prompt Preview and the generation pipeline build the prompt through different code paths, they WILL diverge. Use a single method:

```python
def _build_prompt(self):
    """Single source of truth — called by both preview and generation."""
    if raw_mode:
        return editor.get_text(), []
    elif blocks_exist:
        return editor.get_final_prompt(), []
    else:
        return prompt_builder.build(text, **defaults), warnings
```

---

### Lesson: Engine should not modify the prompt

The generation engine should use `request.text` as-is. If the engine runs a PromptBuilder, it creates a hidden modification step that's invisible to the preview. The UI builds the complete prompt; the engine just executes it.

```python
# WRONG — engine modifies the prompt
if "<|" in request.text:
    prompt = request.text
else:
    prompt = prompt_builder.build(request.text, ...)

# CORRECT — engine uses as-is
prompt = request.text
```

---

### Lesson: SHA-256 hash verification for pipeline integrity

When a prompt travels through multiple pipeline stages, log SHA-256 hashes at each stage. If the hashes match, the pipeline is verified. If they don't, you know exactly where the prompt changed.

```python
import hashlib
hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
logger.info("Stage N: SHA-256=%s len=%d", hash, len(prompt))
```

---

### Lesson: Transaction context manager for multi-field updates

When updating multiple UI fields programmatically (e.g., applying a preset), use a context manager to batch the updates:

```python
with control_panel.batch_update():
    control_panel.set_emotion(value1)
    control_panel.set_style(value2)
    control_panel.set_speed(value3)
# One notification fires here, not three
```

This is cleaner and less error-prone than `blockSignals(True/False)`.

---

### Lesson: Manager classes should not know about UI

A `PresetManager` should handle persistence (load/save/list/delete/validate) only. The apply logic (setting UI widgets) belongs in the UI orchestration layer (MainWindow). This keeps the manager testable without Qt and follows the single responsibility principle.

---

## 4. File Format Decisions

### Lesson: YAML over JSON for human-editable configuration

YAML is superior to JSON for configuration files that users edit by hand:

| Feature | YAML | JSON |
|---------|------|------|
| Multi-line strings | `|` block scalar | `\n` escapes (unreadable) |
| Comments | `#` supported | Not supported |
| Readability | Clean, minimal syntax | Verbose (quotes, braces) |
| Portability | Good | Better (universal) |

Use YAML for user-facing config files. Use JSON for machine-only internal state.

---

## 5. Logging

### Lesson: Use the project's logger infrastructure, not raw logging.getLogger()

```python
# WRONG — no handlers configured, messages silently dropped
logger = logging.getLogger("my_module")

# CORRECT — uses project's configured logger
from engine.logger import get_logger
logger = get_logger("my_module")
```

**General rule:** Every project should have a centralized logger setup. All modules should use it, not create their own `logging.getLogger()` calls.

---

### Lesson: Add logging to every step of an async execution chain

When debugging "it stops after the first item," you need to know exactly which step fails. Log at every state transition:

```
Step 1: start → submit job
Step 2: job done callback → process result
Step 3: marshal to UI thread
Step 4: UI update + schedule next
Step 5: next job starts
```

If the log stops at Step 2, you know the marshalling is broken. If it stops at Step 4, you know the scheduling is broken.

---

## 6. Development Process

### Lesson: Read before write

ALWAYS read a file before editing it. Never assume the file's content based on memory or context. The file on disk is the truth.

```python
# ALWAYS do this:
Read("file.py")
Edit("file.py", old_str, new_str)

# NEVER do this:
Write("file.py", entire_content_from_memory)  # May overwrite newer changes
```

---

### Lesson: Prefer targeted edits over full-file rewrites

`Edit` (find-and-replace) has a smaller blast radius than `Write` (full overwrite). If the context is stale, a full overwrite silently deletes newer work.

---

### Lesson: Impact analysis before modifying any file

Before modifying a file, document:
1. Why this file must change
2. Which systems depend on it
3. What unrelated functionality could be affected
4. What regression risks exist

This prevents "I just changed one thing and three features broke" scenarios.

---

### Lesson: Feature Registry as specification, not inventory

A Feature Registry should define what features SHOULD exist (specification). If a feature is missing, the implementation is regressed. The registry should NEVER be silently updated to match the current (broken) state.

---

## 7. Environment and Deployment

### Lesson: Cloud sandbox environments can reset without warning

Always maintain a local backup (zip) of the project. If the environment resets, you lose everything — including git history. Push to a remote repository (GitHub/GitLab) for permanent protection.

---

### Lesson: Exclude large directories from version control

`.gitignore` should exclude:
- `venv/` — virtual environment (regenerated by bootstrap)
- `__pycache__/` — compiled Python (regenerated automatically)
- `models/` — model weights (too large, downloaded by bootstrap)
- `outputs/` — generated audio (not source code)
- `logs/` — log files (runtime artifacts)

---

## 8. Batch Generation Architecture

### Lesson: Process results on the worker thread, marshal only UI updates

When a `concurrent.futures` done-callback fires on the worker thread:
1. Process the result directly (protected by locks) — don't marshal the processing
2. Marshal ONLY the UI update to the UI thread

This avoids the "QTimer.singleShot from worker thread" problem entirely.

```python
def on_done(future):
    result = future.result()
    with lock:
        job.status = COMPLETED
        job.output_path = result.output_path
    # Marshal only the UI refresh, not the result processing
    marshaller.marshal(refresh_ui_and_schedule_next)
```

---

### Lesson: Use output_duration, not generation_time

When displaying "Duration" in a batch job table, use the audio length (`result.output_duration`), not the wall-clock generation time (`result.generation_time`). Users want to know how long the audio is, not how long it took to generate.

---

### Lesson: Immediate UI feedback on Stop

When the user clicks "Stop," immediately:
1. Mark all pending jobs as SKIPPED
2. Refresh the table
3. Update button states

Don't wait for the current job to finish — the user wants visual feedback the instant they click.

---

## Summary

The most important lessons:

1. **Qt signals, not QTimer, for cross-thread marshalling**
2. **Single source of truth for prompt building**
3. **SHA-256 hash verification for pipeline integrity**
4. **Transaction context managers for multi-field updates**
5. **Read before write, prefer edits over rewrites**
6. **Logger infrastructure must be used consistently**
7. **Process results on worker thread, marshal only UI updates**
8. **Feature Registry as specification, not inventory**
