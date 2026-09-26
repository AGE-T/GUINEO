SpeechStudio — Model Load Animation (P3.32 / P3.33)
====================================================

A hero animation file in this directory upgrades the "Loading Model"
dialog:

    model_load.webp   (preferred — animated WebP)
    model_load.gif    (fallback — animated GIF)

These are exactly the two animated formats this Qt build's QMovie plays
(probed at runtime: QMovie.supportedFormats() == ['gif', 'webp']).

SHIPPED ASSET (P3.33)
---------------------
model_load.webp — the user's own export (256x256, 88 frames, ~24 fps,
opaque white card, ~270 KB). It ships WITH the application now, so the
animated dialog works out of the box. Replace the file any time to
change the animation (see the spec below); delete it to get the classic
progress-bar layout back.

Export specification (for replacements)
---------------------------------------
* Export at 2x for crisp HiDPI rendering: the file is scaled
  aspect-preserving into a 140x140 logical (device-independent) box,
  so a square loop should be exported at 280x280 px. (The shipped
  256x256 asset renders acceptably; 280x280 is the ideal.)
* Infinite seamless loop, 24-30 fps.
* Loop count does NOT matter since P3.33: assets exported with a
  FINITE loop count are restarted automatically when they finish, so
  any export behaves as an endless loading indicator.
* With transparency (WebP has full 8-bit alpha) so the animation sits
  cleanly on the dark dialog surface — or bake the dark background
  into the frames. GIF only has 1-bit alpha (hard edges); prefer WebP.
* Keep it small: ideally under ~1.5 MB.
* No audio track (strip it when exporting from a video editor).

Behaviour
---------
* WebP is preferred when both files exist; the first file that decodes
  as a valid MULTI-frame animation wins.
* P3.33: the hero animation is centered horizontally in the dialog
  (layout-item AlignHCenter). A fixed-size widget inserted without an
  explicit alignment would sit at the LEFT content margin — that was
  the user-reported P3.33 defect ("nem rossz, csak nem középen van").
* P3.33: finite-loop assets are restarted on QMovie.finished() while
  the dialog is visible, so the loading indicator never freezes on its
  last frame (the shipped export itself is a finite-loop asset:
  QMovie.loopCount() == 0). Never restarted once the dialog closed.
* Any problem (missing file, corrupt file, static single-frame image)
  silently falls back to the classic progress-bar layout — the dialog
  can never break because of this asset.
* VIDEO formats (mp4/webm) are intentionally NOT supported: they would
  require the Qt Multimedia backend/codec stack for no benefit (no
  alpha channel, heavier runtime) — and during the initial
  `import torch` GIL starvation the animation would stall exactly like
  a QMovie does, because every frame is composited on the GUI thread.
