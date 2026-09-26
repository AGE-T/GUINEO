# Feature Spec: Advanced Prompt Editor (F-104..F-108)

## Summary

The AdvancedPromptEditor functionality (line numbers, syntax
highlighting, bracket matching, search/replace) has been **merged into
the NarrationEditor**.  There is no separate `AdvancedPromptEditor`
dock widget.  The `BlockAwarePlainTextEdit` inherits from `CodeEditor`
so the editor gains all four capabilities while still rendering block
decorations.

## Feature IDs

| ID    | Name                  |
|-------|-----------------------|
| F-104 | Raw mode (literal)    |
| F-105 | Search/replace bar    |
| F-106 | Line numbers          |
| F-107 | Bracket matching      |
| F-108 | Higgs syntax highlight|

## Components

`ui/panels/advanced_prompt_editor.py` exports:

- `HiggsTokenHighlighter(QSyntaxHighlighter)` - colours
  `<|emotion:...|>`, `<|style:...|>`, `<|prosody:...|>`, `<|sfx:...|>`.
- `LineNumberArea(QWidget)` - paints line numbers for its parent.
- `CodeEditor(QPlainTextEdit)` - monospace font, no wrap, line numbers,
  syntax highlighting, bracket matching.
- `SearchReplaceBar(QWidget)` - collapsible find/replace bar.

There is intentionally **no** `AdvancedPromptEditor` QDockWidget class.

## NarrationEditor Integration

`BlockAwarePlainTextEdit(CodeEditor)`:

1. Sets instance state (`_blocks`, `_show_blocks`, `_flash_*`) BEFORE
   `super().__init__()` so signals fired during CodeEditor init do not
   hit AttributeError.
2. Overrides `_update_line_number_area_width` to also recompute the
   block gutter via `_update_margins`.
3. `_update_margins` reserves `ln_width + gutter_width` pixels.
4. `_paint_block_decorations` offsets all painting by `ln_width` so
   the block gutter sits to the RIGHT of the line-number area.
5. `mousePressEvent` interprets clicks only when
   `ln_width <= click_x < ln_width + GUTTER_WIDTH`.
6. `resizeEvent` lets `CodeEditor.resizeEvent` position the
   `LineNumberArea` at `x=0`.

## Raw Mode

`NarrationEditor` exposes `is_raw_mode() -> bool` and a "Raw Mode"
checkbox in the mode bar.  When raw mode is on:

- The block gutter is hidden.
- `_build_prompt` returns the editor's literal text as-is (no
  PromptBuilder involvement).
- The block-manager state is preserved but ignored for prompt
  building.

## Search/Replace

A magnifier-button toggles the `SearchReplaceBar`.  The bar provides:

- Find next / find previous (with wrap-around).
- Replace / replace all.
- Case-sensitive toggle.
- Status label (e.g. "Replaced 3 occurrence(s).").

`Ctrl+F` toggles the bar; `Escape` closes it.

## Test Plan

1. Type a prompt with `<|emotion:Awe|>` in it, verify the token is
   coloured.
2. Place the cursor next to a `(`, verify the matching `)` is
   highlighted.
3. Switch to Narration Blocks mode, verify the line-number area
   remains visible and the block gutter sits to its right.
4. Toggle Raw Mode, verify the block gutter disappears and the prompt
   preview shows the literal editor text.
5. Press Ctrl+F, type a search term, verify the first match is
   selected and wrap-around works.
