# P3.44.9.1 — Block Gutter / Text Scroll Synchronisation

**Phase**: P3.44.9.1 (targeted visual follow-up — evidence-first, minimal fix)
**Status**: COMPLETE
**Predecessors**: P3.44.9 (block offset/delete integrity) — COMPLETE,
untouched by this task (Case A, see §6).
**Files changed**: `ui/panels/narration_editor.py` (only production file),
`tests/test_p3_44_9_1_block_gutter_scroll_sync.py` (new),
`DEVELOPMENT_LOG.txt`, `docs/governance/OPEN_BUGS.md`, this record.

---

## 1. Symptom

With 30+ blocks the editor's left block gutter desynchronised from the
text viewport while scrolling:

- At the top of the document the labels (B1, B2, …) aligned with their
  blocks' text.
- After scrolling substantially downwards the text moved correctly but
  the labels did not appear to move — B1..B12 stayed visually near the
  top while the visible text was already around much later blocks.
- After any event that forced a fresh gutter paint (resize, block-data
  change) at depth, the labels COLLAPSED/ACCUMULATED: every row landed
  one MIN_ROW_HEIGHT (18 px) below the previous ROW instead of at its
  own block's text position — a stacking cascade that worsened with
  depth (measured: 25 labels in an 11-block band; B15 sat 170 px below
  its own text in the real-MainWindow probe).

## 2. Diagnosis method (probes, read-only)

Three deterministic offscreen probes (kept in the audit scratch area,
`ss/p34491_*.py`; no production code modified during diagnosis):

- **`p34491_truth_probe.py`** — pins the coordinate semantics
  empirically:
  - `cursorRect()` AND `blockBoundingGeometry()` are VIEWPORT
    (scroll-compensated) coordinates: `blockBoundingGeometry(blk)
    .translated(contentOffset()) == cursorRect` exactly.
  - The vertical scrollbar is line-granular (`value` == the first
    visible block number); `contentOffset()` carries only the small
    top margin.
  - The LineNumberArea's own math (`blockBoundingGeometry(...).
    translated(contentOffset())`) is therefore the independent
    reference for every alignment assertion in the tests.
- **`p34491_gutter_probe.py`** — 40 real blocks, paint counters on
  both margin widgets, measurements at top/middle/bottom + a
  5/15/30/40 block sweep + resize-while-scrolled (pre-fix numbers in
  §4).
- **`p34491_layout_probe.py`** — proves the document-coordinate
  invariant across scrolls (`doc_y(pos)` never changes with scroll —
  scrolling mutates no layout and no block data; Case A evidence).

## 3. Architecture facts (code trace)

- The text editor is `BlockAwarePlainTextEdit` (a `QPlainTextEdit`
  subclass via `CodeEditor`, `ui/panels/advanced_prompt_editor.py`).
  Scrolling is owned by the QPlainTextEdit internal vertical
  scrollbar; every scroll emits `QPlainTextEdit.updateRequest(rect, dy)`.
- The block gutter is `_BlockGutterWidget` — a SEPARATE child widget
  positioned in the left margin band (beside the `LineNumberArea`),
  geometry set only in `resizeEvent` (fixed band, full editor height).
- `CodeEditor.__init__` connects `updateRequest` ONLY to
  `_update_line_number_area` (which scrolls/updates the
  LineNumberArea). The block gutter had NO connection to any scroll
  signal.
- `_BlockGutterWidget._block_rows()` computes each block's row from
  `editor.cursorRect(...)` at the block's start/end offsets — i.e.
  VIEWPORT coordinates, live and scroll-compensated at call time.
  Painting and click hit-testing share this ONE layout.

## 4. Root cause (three components, all visual-layer)

Diagnosis table (measured values from the 40-block probe, pre-fix):

| Question | Answer (measured) |
|---|---|
| Text widget | `BlockAwarePlainTextEdit` (QPlainTextEdit) |
| Scroll owner | QPlainTextEdit vertical scrollbar (line-granular; `value` == first visible block) |
| Gutter widget | `_BlockGutterWidget` — separate child widget in the margin band, geometry only in `resizeEvent` |
| Block Y source | `editor.cursorRect()` at block offsets — viewport coordinates, live |
| Text viewport coords | `blockBoundingGeometry().translated(contentOffset())` == `cursorRect` (proven equal) |
| Gutter coords | Same viewport space as the text (rows are live cursorRect y values) |
| Scroll offset source | Implicit in `cursorRect` (viewport-compensated) — nothing to subtract |
| Scroll callback | `updateRequest` → CodeEditor connected it ONLY to the LineNumberArea; the gutter had none |
| Repaint triggers | `set_block_data` / `set_show_blocks` / status updates / flash timer / `resizeEvent` — NO scroll trigger |
| Resize trigger | `resizeEvent` repositions the band (and repaints) |

1. **Frozen image** — `updateRequest` never drove the gutter
   repaint. Measured pre-fix: gutter paints during a full scroll:
   **+0** (LineNumberArea: +1). The on-screen state stayed at the
   scroll=0 rows (B1..B12 at y=4..598) while their text had scrolled
   to y=−701..−179 — 12/12 misaligned.
2. **Row cascade (scrolled-off dragging)** — the P3.41 row clamp
   `row_top = max(y_top, prev_bottom + 1)` chained UNCONDITIONALLY
   against the previous ROW bottom. Blocks scrolled off above the
   viewport have negative `y_top`; the clamp dragged the first one to
   y≈0 and every following row one MIN_ROW_HEIGHT lower — measured
   fresh-layout at bottom: B2@18, B3@37, …, B32@588 (31/31
   misaligned; ~19 px/block cascade).
3. **Sentence-pair chaining** — the same clamp chained across
   paragraphs whenever the P3.44.8 sentence splitter produced
   consecutive sentence blocks sharing a wrapped line: each label
   drifted one MIN_ROW_HEIGHT per same-line sentence (pre-existing
   since P3.41; measured 170 px at B15 in the real-MainWindow probe).

None of the three touches block data (§6).

## 5. The fix (minimal, three local changes in
`ui/panels/narration_editor.py`)

1. **`BlockAwarePlainTextEdit._update_line_number_area(rect, dy)`
   override** — calls `super()` (the CodeEditor implementation that
   keeps the LineNumberArea in sync) and then
   `self._block_gutter_widget.update()`: the gutter now repaints on
   the SAME `updateRequest` path that fires on every scroll step. No
   timers, no polling, no new signal plumbing. A construction-order
   guard (`_block_gutter_widget` pre-seeded to `None` before
   `super().__init__()`, mirroring the constructor's existing
   "instance state before super" pattern) covers the updateRequest
   emissions that happen during base-class construction.
2. **`_block_rows()` stacking guard** — stacking below the previous
   row now happens ONLY on genuine degenerate adjacency: the block's
   text starting on the same/adjacent TEXT line as the previous
   block's text end (`y_top <= prev_text_bottom + 1`). Separated
   blocks anchor at their OWN text start in viewport coordinates, so
   every row moves with the text while scrolling and nothing is
   dragged into the band.
3. **Monotonic trim pass** — a final pass keeps rows strictly
   disjoint even when the lazy `QPlainTextDocumentLayout` hands out
   non-monotonic estimates for blocks far outside the viewport:
   overlap is resolved by trimming the EARLIER row's bottom, never by
   pushing the later row away from its own text anchor. (A trimmed
   row may be shorter than MIN_ROW_HEIGHT in that over-constrained
   case — self-healing on the next paint once the layout
   materialises. The settled layouts the P3.41 contract tests
   exercise never trigger the trim.)

Post-fix probe values: gutter paints during scroll +1 per scroll
burst; on-screen rows == fresh rows == text geometry; 0 misaligned at
top/middle/bottom, 0 misaligned in the 5/15/30/40 sweep, 19/19
aligned after resize-while-scrolled.

## 6. Shared-root conclusion (P3.44.9) — Case A: SEPARATE ROOT

The P3.44.9 offset engine (`NarrationBlockManager.on_text_changed` /
`_apply_edit_to_blocks`) is NOT involved:

- Scrolling fires no text-change path (no `textChanged`, no
  `on_text_changed`) — the offset engine never runs during a scroll.
- `p34491_layout_probe.py`: document geometry (`doc_y`) is invariant
  across scroll positions on all settling strategies.
- The regression tests pin it: `test_scroll_never_mutates_block_data`
  (byte-identical ids/offsets/owned text/lock state across scroll
  cycles) and `test_edit_identity_through_offset_engine` (an edit
  while scrolled still follows the P3.44.9 authoritative rule) — both
  are CONTROLS that pass on the pre-fix code.

Block ranges, text ownership, IDs and ordering are untouched by the
fix; visual synchronisation is a presentation concern, exactly as the
task required.

## 7. Regression coverage

`tests/test_p3_44_9_1_block_gutter_scroll_sync.py` — 19 tests (real
widgets; two full-stack tests with real Engine + real MainWindow +
real auto-detect splitter):

- **Scroll positions**: top (CONTROL), middle, bottom,
  every-scrollbar-position sweep.
- **Block counts**: 15/30/40 scrolled to bottom (5-block no-scroll
  control inside the sweep).
- **Identity**: painted labels match the blocks whose anchored rows
  intersect the band (two-directional with a 2px culling-boundary
  tolerance); scroll never mutates block data (CONTROL).
- **Geometry**: scrolled-off blocks not dragged into the band / no
  cascade; round-trip zero drift; five repeated scroll cycles zero
  drift; wrapped variable-height alignment.
- **Interactions**: resize while scrolled (widen + narrow, re-wrap);
  edit through the REAL offset engine while scrolled (identity
  CONTROL + alignment); Scene round-trip rehydration through the real
  `begin_scene_load`/`set_text`/`set_scene_blocks`/`end_scene_load`
  path + scroll.
- **Repaint contract**: paint counters (patched BEFORE instantiation
  — PySide6 resolves Python overrides of C++ virtuals per instance at
  creation time) prove the gutter repaints on scroll and that the
  last-painted rows equal the fresh layout and sit at the text
  geometry.
- **P3.41 contract controls** (must keep passing): degenerate
  adjacency stacking (same-line + adjacent-lines variants) and
  stacked-row click hit-testing via real QTest clicks.

**Old/new proof (git stash of the production change)**: pre-fix code
**14 failed / 5 passed** — the 5 passing are exactly the intentional
controls (top alignment, scroll data purity, edit identity, P3.41
adjacency, P3.41 click consistency). Post-fix: **19/19**.

Test-harness lessons encoded in the file (proven during
development): mirror the real `set_block_data` + `set_show_blocks`
pairing (a bare `set_block_data` leaves the gutter hidden → no
paints); settle the lazy layout with 3-consecutive-stable polls; the
offscreen MainWindow editor viewport can lay out squeezed at small
window sizes — resize to a fixed generous size and guard the viewport
height before measuring; paint-event patching must precede widget
instantiation; the ±1px `blockBoundingGeometry().bottom()` vs
`cursorRect().bottom()` difference at the culling edge needs the
strict/loose tolerance pair.

## 8. Residuals (evidence-based, accepted)

- **Single stack-step label offset for sentence pairs sharing a
  wrapped line**: when two sentence blocks genuinely start/end on the
  same text line, the later label sits ONE disjoint MIN_ROW_HEIGHT
  step below the previous label (bounded, local — previously the
  chain was unbounded across the whole band). This is the price of
  keeping the P3.41 disjointness contract without a rendering
  redesign; it is asserted explicitly (the "adjacent" branch of the
  anchor contract) rather than hidden.
- **Lazy-layout estimates for far-offscreen blocks**: rows for blocks
  far outside the viewport come from `QPlainTextDocumentLayout`
  estimates until the layout materialises; such rows are culled by
  the paint pass and self-heal on the next repaint. The monotonic
  trim pass only guards disjointness in that window.
- **No windowed visual inspection in this sandbox**: all geometry
  assertions are computed from real Qt widget geometry offscreen
  (cursorRect / blockBoundingGeometry / paint counters / real
  QTest clicks); a human-visual pass on a windowed desktop remains
  recommended but was not possible here.

## 9. Scope check

- P3.44.9 block integrity NOT changed (Case A proven; the offset
  engine files are untouched).
- No unrelated refactor; one production file changed.
- No timers / polling / periodic sync introduced — the fix rides the
  existing `updateRequest` signal path.
- P3.45 (rendering/UX hardening) remains the next major roadmap item;
  Batch Generation Architecture V2 remains deferred. This task stops
  here — no automatic entry into P3.45.
