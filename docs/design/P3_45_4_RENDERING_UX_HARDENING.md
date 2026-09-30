# P3.45.4 — Rendering / UX Hardening

Baseline: `main @ 87b56f4` (P3.45.3 build commit). Every finding below was
independently re-verified at that HEAD with a runtime probe
(`ss/audit_p3454/probe.py`, real Qt widgets offscreen) before any change —
and re-measured after the fix (`ss/audit_p3454/probe_postfix.py`, real
handler paths). Nothing was changed on intuition.

---

## 1. CODE-LEVEL AUDIT (verified at HEAD, not from prior reports)

The nine task-listed triage findings were each traced to their code and,
where a runtime signal exists, executed against real widgets:

| # | Triage | Verdict at `87b56f4` | Evidence |
|---|--------|----------------------|----------|
| A | D-R1 duplicated `engine.app_root` | **PROVEN, pure duplication** | `engine/engine.py:489-492` (docstring) and `:784-786` — identical bodies (`return self._app_root`); class-body order makes the SECOND the effective one, the first is shadowed dead code |
| B | D-R2 `_check_states` index-keyed + Load Queue reset | **PROVEN (5 failure modes)** | index-keyed writes at `batch_generation.py:917/2419/2694/2701` vs THREE docstrings claiming slot-keying (`:1892`, `:2402`, `:2714`); runtime: reorder migrates the checkmark to the wrong job; delete migrates it AND leaves `_checked_indices()==[2]` on a 2-row table; duplicate migrates it; Load Queue keeps stale index→checked mappings across queues; Load Queue never re-derives the documented P3.28 §11 defaults |
| C | D-R3 redundant `toPlainText()` in paint paths | **PROVEN (dead + length-only)** | gutter `paintEvent:188` `text = editor.toPlainText()` — full-document copy per paint, **never consumed** (word-boundary scan of the paint body); `ln_width` at `:189` dead local; `_block_rows:135`, gutter `mousePressEvent:452`, `_highlight_current_line:709` copy the whole document for its LENGTH only |
| D | D-R4 no-op click full viewport repaint | **PROVEN** | `_on_block_click_requested` unconditionally re-runs `_render_block_visuals()` (set_block_data + set_show_blocks + `_highlight_current_line` → 2× `viewport().update()`); measured: 1 full text-viewport paint for a click that changed nothing on screen |
| E | D-R5 flash-timer viewport repaints | **PROVEN** | `_on_flash_tick` + `flash_blocks` call `self.viewport().update()` every 30 ms; measured: **32 full text-viewport paints per ~0.9 s flash**, although the flash overlay is painted ONLY in `_BlockGutterWidget.paintEvent` (`:440-443`) — the viewport renders none of the flash state |
| F | D-R6 hardcoded block stripes | **PROVEN theme defect** | `_highlight_current_line:710-712` `QColor("#252840")/QColor("#222538")` — dark-family literals while the app has 5 themes, `apply_theme()` live-updates the `Palette` class attributes, and users switch themes at runtime (View → Theme menu `main_window.py:6014`, Settings save `:6078`) → dark stripes painted over the LIGHT theme |
| G | D-R8 dead `modern_control_panel.py` | **NOT a deletion candidate** | zero production importers, zero test references, BUT its own docstring documents it as the drop-in alternative ("MainWindow can switch between the two implementations with a single import edit") + it has a `__main__` smoke demo; git-tracked since creation → a documented reserved route, kept |
| H | §5.2 transparency / OpenGL text | **STOP — architectural follow-up** | per `FONT_RENDERING_METRICS_AUDIT.md`: source-verified architecture concern (QOpenGLWidget FBO composition + `WA_TranslucentBackground` tree + PassThrough fractional DPI), contrast softening 229→219 measured offscreen but the visual effect is NOT reproducible offscreen; platform/DPI dependent, non-deterministic, no glyph corruption (rasters crisp at every dpr); every fix direction (opaque shell, CPU ambient at non-100% DPI, rounding policy) is a rendering-architecture decision — out of P3.45.4 scope |
| — | D-R7/D-R9/D-R10 (triage §5.1) | **not in the P3.45.4 issue list** | combined-rows churn, Generate-button flicker, private-attribute reach-ins remain documented for future slices; untouched here |

A supporting identity was measured before relying on it:
`len(doc.toPlainText()) == doc.characterCount() - 1` holds for empty,
single-block, multi-block, trailing-newline, Unicode and the real editor
document (Qt counts each block's paragraph separator; `toPlainText` maps
each to one `\n`; the trailing separator has no text).

## 2. CONFIRMED DEFECTS (only these were fixed)

D-R1, D-R2, D-R3, D-R4, D-R5, D-R6 — each with the failure mode above.

## 3. IMPLEMENTATION (six local fixes, no new machinery)

### 3.1 `engine/engine.py` — one authoritative `app_root` (D-R1)

Removed the SHADOWED first property (the one inside the voice-facade
section); its P3.43 docstring moved to the surviving definition in the
Properties section. Zero behaviour change by construction: the class
attribute `app_root` already resolved to the surviving function object.

### 3.2 `ui/panels/narration_editor.py` — paint-path work (D-R3/D-R4/D-R5/D-R6)

* **D-R3**: the dead `text`/`ln_width` locals in the gutter `paintEvent`
  were deleted. `_block_rows`, the gutter `mousePressEvent` and
  `_highlight_current_line` obtain the document length via the live,
  O(1) `document().characterCount() - 1` — the measured identity, NOT a
  cache, so it cannot go stale. The P3.44.9.1 geometry algorithm
  (cursorRect anchoring, degenerate-adjacency stacking, monotonic trim)
  is untouched — only the source of the clamp length changed, to an equal
  value.
* **D-R4**: `_on_block_click_requested` now calls `_render_block_visuals()`
  only when the selection actually changed (`changed`). The properties
  panel refresh — the documented "every click produces visible feedback"
  — is unchanged. Every path that can change the visuals (block-manager
  mutations, `set_text`, `set_global_defaults`, `_set_mode`,
  `set_show_blocks`, status pushes) already re-renders through its own
  call site, so a same-block re-click re-applied identical state.
* **D-R5**: `flash_blocks` and `_on_flash_tick` no longer invalidate the
  text viewport; the gutter remains invalidated per tick. THE TIMER IS
  KEPT — it drives the visible gutter flash (fade-out) and stops itself
  at the end; only the provably-invisible viewport repaint is gone.
* **D-R6**: the alternating block stripes now read
  `Palette.BG_SURFACE` / `Palette.BG_SURFACE_ALT` (alpha 45, unchanged
  alternation semantics). Because the stripe formats are computed at
  `_highlight_current_line` time (not at paint time), a new public
  `NarrationEditor.refresh_theme()` re-derives them — following the
  existing `toolbar.refresh_theme()` / `top_nav.refresh_theme()`
  convention — and MainWindow's two theme-application sites (View →
  Theme menu and the Settings save path) call it beside the existing
  refreshes.

### 3.3 `ui/panels/batch_generation.py` — `_check_states` identity (D-R2)

The state is the user's transient "include this part in the next
generation" choice — it naturally belongs to a specific JOB object:

* `_check_key(job)` = `("slot", slot_id)` for structural jobs — the
  frozen structural identity (P3.44.6) the selective-generation flow
  already reasons in, and exactly what the three pre-fix docstrings
  documented — or `("job", id(job))` for slot-less manual rows
  (+Add, P3.44.6 duplicates that deliberately strip slot provenance:
  a duplicate can never inherit the original's transient state, and the
  state dies with the object).
* `_init_check_defaults`, `_build_check_widget` (initial state + toggle
  closure), the in-place P3.44.1 §1 sync, and All/None write/read
  job-keyed states.
* `_checked_indices()` derives indices from the CURRENT job order at
  call time (always in range — the contract MainWindow's P3.44.4
  selective run consumes).
* `_on_load_queue` clears the dict and re-derives the documented P3.28
  §11 defaults for the loaded jobs (the pre-load checkmarks were choices
  about different jobs).
* `_on_edit` carries a MANUAL job's state over to the replacement clone
  (a structural job keeps its slot_id through the round-trip — same key,
  no transfer needed); editing never resets the user's checkbox.
* `_rebuild_table` prunes keys no current job can resolve (deleted jobs,
  replaced manual objects, pre-Load-Queue leftovers) — the dict always
  describes exactly the current queue.
* The three docstrings now describe the actual implementation.

### 3.4 Test updates (documented, mechanical)

Eight suites wrote raw row-index dicts into `_check_states` (the pre-fix
internal representation); each site now derives the job key through the
dialog's own `_check_key` (helpers `job_key`/`_check_dict`/`select_only`
with in-line "P3.45.4 (documented update)" notes):
`test_p3_28` (10 sites), `test_p3_44_1` (helper + 5),
`test_p3_44_2` (1), `test_p3_44_4` (select_only), `test_p3_44_5` (3),
`test_p3_45_2a` (6), `test_p3_45_3` (1), `test_p3_44_playback` (1 read).
Each test's intent (which rows are selected) is unchanged.

## 4. MEASURABLE RESULTS (instrumented, real widgets)

| Path | Before (`87b56f4`) | After |
|------|--------------------|-------|
| no-op block click (same block) | 1 full text-viewport paint + 1 gutter paint | **0 viewport paints, 0 gutter paints** |
| real selection change | 1 viewport + 1 gutter | 1 viewport + 1 gutter (unchanged contract) |
| one flash animation (~0.9 s) | 32 full text-viewport paints | **0 per tick** (gutter still ~30 paints — the visible flash) |
| gutter paint, per paint | 1 full-document copy (`toPlainText`, unused) + 1 dead `line_number_area_width()` | neither |
| gutter row layout, per paint | 1 full-document copy for the length clamp | O(1) live `characterCount()-1` (equal value — measured identity) |
| stripes in light theme | hardcoded `#252840`/`#222538` (dark) | the light theme's surface tokens, immediately on switch |

## 5. FROZEN-CONTRACT SAFETY

* **P3.44.9.1**: the geometry algorithm in `_block_rows` is untouched;
  only the clamp-length source changed to the measured-equal identity.
  The `updateRequest` scroll wiring is untouched (pinned by the new
  scroll-paint test + the full P3.44.9.1 suite: 19 passed).
* **P3.45.1**: `update_block_status` / badge painting untouched (pinned
  by the focused suite + the P3.45.1 suite: 20 passed).
* **P3.45.3**: `_NameCell` chips, `slot_identity_summary`, queue-vs-
  structural tooltips untouched (spot-checked in the focused suite and
  the launch smoke; full suite: 40 passed).
* **P3.45.2A / P3.45.2B**: preflight and estimator code paths not
  touched (42 / 33 passed).
* **Scene Combine / Manual Concatenate / provenance / save-load**:
  untouched (65 + 29 + 17 + 104 + 157 + 116 + 159 passed).

## 6. WHAT WAS **NOT** DONE (scope gate)

* No rendering framework, layout engine, repaint manager, or theme
  architecture was introduced — the fixes use existing signals, existing
  state ownership, the existing Palette, and existing widget lifecycles.
* No caching subsystem — the length identity is live document state.
* The flash TIMER was kept (it drives the visible effect; only the
  provably-invisible viewport invalidation was removed).
* `modern_control_panel.py` was NOT deleted (documented reserved
  alternative — see §1 G).
* The transparency/OpenGL text question was NOT implemented (see §1 H).
* D-R7 / D-R9 / D-R10 (not in this task's issue list) remain documented
  for future slices.
* No click semantics changed: the properties panel still refreshes on
  every click; the cursor-driven current-line highlight is untouched;
  gutter click hit-testing is unchanged (same row layout, same clamp).

## 7. REMAINING RISKS / DEFERRED ARCHITECTURAL ISSUES

1. **Transparency-stack / GL text composition** (Issue H): cross-platform
   rendering-architecture decision — opaque central shell, CPU/static
   ambient at non-100% DPI, or `RoundPreferFloor` rounding. Requires a
   platform-capable environment to decide; NOT a P3.45.4 work item.
   (Already documented in `FONT_RENDERING_METRICS_AUDIT.md` fix
   directions.)
2. **Two same-slot jobs in one queue** (hand-edited queue YAML) would
   share one check state under slot keying — unreachable through the
   product flows (Generate Long replaces the queue; duplicates strip
   slot provenance), noted as an accepted edge.
3. **Pre-existing failures, unchanged**: SS-3 (P3.37 README section),
   the P3.44.4 stop-timing flake (green isolated; proven flaky on
   pristine trees), `verify_integration`'s stop-semantics violation
   (pristine-identical) — each needs its own evidence-first
   investigation, not a rendering-hardening change.

## 8. TESTS

New focused suite `tests/test_p3_45_4_rendering_ux_hardening.py`
(28 tests): app_root single-definition AST + behaviour; check-state
lifecycle through the REAL handlers (correct job / reorder ×2 / delete /
duplicate / Load Queue ×2 / fresh dialog / failed job / manual-job edit
transfer / All-None / dict hygiene / manual-mode absence); paint paths
(AST: no `toPlainText` in paint/hit-test/highlight; geometry identity +
row invariants; REAL scroll repaints gutter + viewport; P3.45.1 badge);
click repaint (no-op = 0 viewport paints; real change repaints; REAL
`QTest.mouseClick` selects the block); flash (gutter animates ≥10
paints, viewport ≤2, timer self-stops); theme (live tokens, AST no
hardcoded `QColor` constructors, theme switch re-derives stripes,
selection ACCENT@35 / override WARNING@22 unchanged); P3.45.3 safety
(chips travel with jobs across reorder; manual window unchanged).

Validation battery (all at the implementation commit): focused 28/28
TWICE; P3.45.3 40; P3.45.2B 33; P3.45.2A 42; P3.45.1 20; P3.44.9
76+35 subtests; P3.44.9.1 19; P3.28 65; P3.23 29; P3.31 17; P3.44.1/4/
5/6 104; P3.44.7/8/2/playback 157; two-workflows/nb/outlier/ux-batch
116; save/load family 159; FULL suite 1725 passed + 35 subtests + the
two documented pre-existing failures; verify_compile 153/153; verify_
architecture PASS; verify_functional_integrity 80/80; verify_integration
= the documented pre-existing stop-semantics failure only; launch smoke
(real entry path) 12/12.
