"""
SpeechStudio Model Loading Dialog.

Shows a modal dialog while the model loads into VRAM, with a progress
message and a spinner. The user can cancel the load.

P3.32: optional hero animation. If a playable animated file exists under
``assets/animations/`` — ``model_load.webp`` preferred, ``model_load.gif``
fallback; exactly the two animated formats this Qt build's QMovie plays
(``QMovie.supportedFormats() == ['gif', 'webp']``, verified on PySide6
6.11.2) — it replaces the indeterminate progress bar as the activity
indicator. With no usable asset the dialog is exactly the pre-P3.32
layout: the P3.25 / P3.29 regression contracts are untouched.

P3.33 (user-reported defects, both proven by pixels offscreen):
(1) CENTERING — the hero label is inserted with an explicit
``AlignHCenter`` layout alignment. Without it the fixed-size label sat
at the layout cell origin (the LEFT content margin), ~100px off-center
on the 400px dialog, while the title below it was centered.
(2) LOOP-FOREVER — assets exported with a FINITE loop count (the
shipped ``model_load.webp``: ``QMovie.loopCount() == 0``) play once,
emit ``finished`` and then FREEZE on their last frame; a loading
indicator must never go static, so ``finished`` restarts the movie
while the dialog is visible. Infinite-loop assets (``loopCount() ==
-1``) never emit ``finished`` and are unaffected.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from PySide6.QtCore import QByteArray, QEventLoop, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QMovie
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QLabel, QProgressBar,
    QPushButton, QWidget,
)

from engine.logger import get_logger
from ui.theme import Palette

logger = get_logger("model_load_dialog")


# ---------------------------------------------------------------------------
# P3.32: optional load animation (animated WebP preferred, GIF fallback)
# ---------------------------------------------------------------------------
# Resolve the application root from this file's location:
#   ui/panels/model_load_dialog.py  ->  ui/panels/  ->  ui/  ->  <app_root>
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Directory scanned for the optional load animation. Kept as a module
#: attribute (read at call time, never captured) so tests can retarget it.
ANIMATIONS_DIR = os.path.join(_APP_ROOT, "assets", "animations")

#: Candidate file names, in preference order. The first candidate that
#: BOTH exists AND decodes as a playable animated QMovie wins.
ANIMATION_FILE_CANDIDATES = ("model_load.webp", "model_load.gif")

#: Display box for the animation, in logical (device-independent) pixels.
#: Export the asset at 2x (280x280 for a square loop) for crisp HiDPI
#: rendering; the movie is scaled down aspect-preserving to fit this box.
ANIMATION_MAX_SIZE = QSize(140, 140)

#: Dialog size when the animation is active. Without a usable animation
#: asset the dialog keeps the P3.25-fixed 400x220 exactly.
DIALOG_SIZE_WITH_ANIMATION = (400, 380)


def find_animation_file() -> Optional[str]:
    """Return the first EXISTING animation candidate path (or None).

    Pure path-existence check (no decoding) so the preference order is
    trivially testable; playability is decided by the caller via QMovie.
    Reads :data:`ANIMATIONS_DIR` at call time.
    """
    try:
        if not os.path.isdir(ANIMATIONS_DIR):
            return None
        for name in ANIMATION_FILE_CANDIDATES:
            path = os.path.join(ANIMATIONS_DIR, name)
            if os.path.isfile(path):
                return path
    except OSError:
        pass
    return None


def _load_animation_movie(parent: Optional[QWidget]) -> Optional[QMovie]:
    """Load the first PLAYABLE animation from :data:`ANIMATIONS_DIR`.

    Returns a QMovie already jumped to frame 0 (at its native size — the
    caller computes the scaled size), or None when no candidate exists
    or none decodes. Static single-frame images are rejected too (a
    still image is not an activity indicator). A corrupt or non-animated
    file never raises and never breaks the dialog: the candidate is
    skipped with a warning and the classic progress-bar layout is used
    instead.
    """
    try:
        if not os.path.isdir(ANIMATIONS_DIR):
            return None
    except OSError:
        return None

    for name in ANIMATION_FILE_CANDIDATES:
        path = os.path.join(ANIMATIONS_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            # NOTE: the (fileName, format, parent) overload with an EMPTY
            # QByteArray lets Qt sniff the format from the file header —
            # passing (path, parent) directly would try to coerce the
            # parent into the QByteArray format slot (PySide6 overload
            # resolution) and raise TypeError.
            movie = QMovie(path, QByteArray(), parent)
            if movie.isValid() and movie.jumpToFrame(0) \
                    and movie.frameCount() > 1:
                logger.info("model load animation active: %s", path)
                return movie
            logger.warning(
                "animation candidate is not playable or not animated, "
                "skipping: %s", path)
        except Exception:
            logger.warning(
                "animation candidate failed to load, skipping: %s", path)
    return None


class ModelLoadDialog(QDialog):
    """Modal dialog shown while the model is loading.

    Displays a spinner animation (simulated with a progress bar), an
    elapsed-time counter and status messages. The load happens on a
    worker thread; this dialog polls (``status_fn`` -> ModelManager
    status) to update the phase message ("Loading tokenizer...",
    "Loading model weights...", "Moving model to CUDA...").

    P3.29: the poll is safe because ModelManager.status() is guaranteed
    non-blocking (microsecond state-lock snapshot + sys.modules torch
    guard). Before the P3.29 fix the GUI thread froze for the whole
    load (the dialog showed "(Not Responding)" and the elapsed timer
    never ticked) because status() blocked on the lock load() held.

    P3.32: when ``assets/animations/model_load.webp`` (or ``.gif``)
    exists and is playable, it is shown as a hero animation ABOVE the
    title and the indeterminate progress bar is hidden (the animation
    is the activity indicator). P3.33: the hero label is centered via
    its layout-item alignment (a fixed-size widget without one sits at
    the left cell origin), and finite-loop assets are restarted on
    ``finished`` so the indicator never freezes. The movie is started in
    :meth:`start` BEFORE the synchronous paint, so the painted dialog
    already contains live animation content — the P3.25 white-window
    invariant is preserved: the dialog is fully painted BEFORE the
    caller submits the load job, so the ``import torch`` GIL starvation
    can never produce a blank window. Note that WHILE the GUI thread is
    starved the animation may briefly stall — exactly like the busy bar
    it replaces (both are driven by the GUI event loop) — and resumes
    as soon as the event loop gets time (P3.29 contract).
    """

    load_cancelled = Signal()

    def __init__(self, parent=None,
                 status_fn: Optional[Callable] = None):
        super().__init__(parent)
        self.setWindowTitle("Loading Model")
        self.setModal(True)
        # P3.25 FIX: 180px was too short for the content (title + status +
        # progress + timer + Cancel under the theme QSS paddings) — the
        # layout squeezed the Cancel button to 15px, erasing its label.
        # 220px fits every item at its size hint. (With the P3.32 hero
        # animation active the dialog grows to
        # DIALOG_SIZE_WITH_ANIMATION instead.)
        self.setFixedSize(400, 220)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)
        # P3.25 FIX (white/blank loading window): the app-wide QSS styles
        # QDialog backgrounds as TRANSPARENT (the main window's dark look
        # comes from the ambient background layers behind it — a separate
        # top-level dialog has no such backdrop). Without an explicit
        # background the dialog body fell back to the platform default.
        # Set the dark surface explicitly, exactly like the (visually
        # verified) AssembleDialog does.
        self.setStyleSheet("background-color: {0};".format(Palette.BG_BASE))
        # P3.29: optional poll callable returning a ModelStatus (used to
        # surface the current load phase). Must be non-blocking.
        self._status_fn = status_fn
        # P3.32: optional hero animation (None => classic layout).
        self._movie: Optional[QMovie] = None
        self._anim_label: Optional[QLabel] = None
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_status)
        self._elapsed = 0

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        self._title = QLabel("Loading Higgs Audio V3 Model")
        self._title.setStyleSheet("font-size: 16px; font-weight: bold; color: %s;" % Palette.TEXT_PRIMARY)
        layout.addWidget(self._title)

        self._status = QLabel("Initializing...")
        self._status.setStyleSheet("color: %s;" % Palette.TEXT_SECONDARY)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate (busy indicator)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)
        layout.addWidget(self._progress)

        self._timer_label = QLabel("")
        self._timer_label.setStyleSheet("color: %s;" % Palette.TEXT_DISABLED)
        self._timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._timer_label)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self._cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # P3.32: try to upgrade the classic layout with the hero
        # animation. Every failure mode (missing directory, missing
        # file, corrupt file, non-animated file) keeps the layout above
        # byte-for-byte.
        self._apply_animation()

    def _apply_animation(self) -> None:
        """Activate the hero animation if a playable asset exists.

        No-op (classic layout) otherwise — see module docstring.
        """
        movie = _load_animation_movie(self)
        if movie is None:
            return

        # Scale aspect-preserving into the display box. The native frame
        # size is read from frame 0 (jumpToFrame already done by the
        # loader).
        native = movie.currentPixmap().size()
        scaled = native.scaled(
            ANIMATION_MAX_SIZE, Qt.AspectRatioMode.KeepAspectRatio)
        movie.setScaledSize(scaled)

        self._movie = movie
        # P3.33: finite-loop assets (loopCount >= 0) emit finished after
        # their last pass and QMovie then freezes on the final frame.
        # A loading indicator must NEVER go static — restart it. The
        # guard (dialog still visible) prevents a zombie movie restarting
        # on a dialog that stop() already closed. Infinite-loop assets
        # never emit finished, so this connection is dormant for them.
        movie.finished.connect(self._on_animation_finished)

        self._anim_label = QLabel()
        self._anim_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._anim_label.setMovie(movie)
        self._anim_label.setFixedSize(ANIMATION_MAX_SIZE)
        # P3.33 CENTERING FIX: the alignment on the QLabel only centers
        # the movie pixmap INSIDE the label. The label itself must be
        # centered in the dialog with an explicit layout-item alignment:
        # a fixed-size widget inserted WITHOUT one is parked at the
        # layout cell origin — the left content margin (user-reported:
        # animation hugging the left edge while the title was centered).
        self.layout().insertWidget(
            0, self._anim_label, 0, Qt.AlignmentFlag.AlignHCenter)

        # QLabel only renders movie content after the movie emits
        # ``updated`` — and jumpToFrame() to the frame it already sits on
        # can be a NO-OP that emits nothing (the label would stay empty
        # until the first real frame change while running). Jump to a
        # DIFFERENT frame and back so the label adopts the scaled frame 0
        # immediately; frameCount > 1 is guaranteed by the loader.
        movie.jumpToFrame(1)
        movie.jumpToFrame(0)

        # The animation IS the activity indicator; the busy bar would be
        # a second, redundant one. Center the text stack beneath the
        # centered hero (the timer label and Cancel are centered already).
        self._progress.hide()
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(*DIALOG_SIZE_WITH_ANIMATION)

    def _on_animation_finished(self) -> None:
        """P3.33: keep a finite-loop animation looping forever.

        Assets exported with a finite loop count (``QMovie.loopCount()``
        >= 0 — e.g. the shipped ``model_load.webp``) play their passes,
        emit ``finished`` and then stop dead on the last frame. The
        dialog is a LOADING indicator: restart the movie so the
        animation runs for as long as the model loads. Only while the
        dialog is actually visible — never resurrect a movie on a
        dialog that stop() already closed.
        """
        if self._movie is not None and self.isVisible():
            self._movie.start()

    def _on_cancel(self) -> None:
        self.load_cancelled.emit()
        self.reject()

    def start(self) -> None:
        """Start polling, show the dialog AND paint it synchronously.

        P3.25 FIX (white/blank loading window): ``show()`` only MAPS the
        window — the actual paint is deferred to the event loop. The
        model-load worker thread starts ``import torch`` immediately
        after the caller submits the load job; on Windows that import
        holds the GIL in long C-extension stretches, so the GUI thread's
        event loop cannot process the dialog's expose/paint events for
        many seconds. The user then sees a mapped-but-unpainted window:
        a white, content-free rectangle with only the OS title bar.

        Forcing the paint here — while the GUI thread still owns the GIL
        and BEFORE the caller submits the load job — guarantees the dark
        themed dialog (title, status, progress bar, Cancel) is on screen
        before any GIL starvation can begin.

        P3.32: the same invariant covers the hero animation — the movie
        is jumped back to frame 0, STARTED and only then is the dialog
        shown + repainted, so the synchronously painted dialog already
        contains live animation content.
        """
        self._elapsed = 0
        self._timer.start(500)  # poll every 500ms
        if self._movie is not None:
            self._movie.jumpToFrame(0)
            self._movie.start()
        self.show()
        # Flush pending layout/expose events (but NOT user input — no
        # re-entrancy from clicks), then paint synchronously into the
        # backing store. This is the standard Qt splash-screen pattern.
        QApplication.processEvents(
            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        self.repaint()

    def stop(self) -> None:
        """Stop polling and close the dialog."""
        self._timer.stop()
        if self._movie is not None:
            self._movie.stop()
        self.accept()

    def _poll_status(self) -> None:
        self._elapsed += 0.5
        mins = int(self._elapsed) // 60
        secs = int(self._elapsed) % 60
        self._timer_label.setText("Elapsed: {0:02d}:{1:02d}".format(mins, secs))
        # P3.29: surface the loader's current phase ("Loading
        # tokenizer..." / "Loading model weights..." / "Moving model to
        # CUDA..."). status_fn is ModelManager-backed and guaranteed
        # non-blocking (P3.29 lock-scope fix) — polling it can never
        # freeze the dialog.
        if self._status_fn is not None:
            try:
                st = self._status_fn()
            except Exception:
                st = None
            phase = getattr(st, "phase", "") or ""
            if phase:
                self._status.setText(phase)

    def set_status(self, message: str) -> None:
        self._status.setText(message)
