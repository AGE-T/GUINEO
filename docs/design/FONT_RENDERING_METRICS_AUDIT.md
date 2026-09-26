# SpeechStudio — Rendered Font Metrics Audit ("claims 14px but looks compressed/rasterized")

**AUDIT ONLY — no code, size, or style was changed.** Everything below is
**measured at runtime** (probes: `/home/z/audit_probes/probe_font_metrics.py`,
`probe_font_scale.py` — read-only, offscreen, production startup path:
`load_fonts()` → `apply_theme("modern-dark")` → real widgets).

---

## 1. Which font family actually renders? (Q1)

**Inter.** `QFontInfo.family() == 'Inter'` on every surface that claims 14px
(TopNav NavButton, Sidebar nav rows, ALL-SCENES rows, Recents primary label).
The theme's CSS-style stack (`"Inter", "Segoe UI", …`) IS parsed by Qt 6.11
QSS into `QFont.families()` (the audit's initial hypothesis that Qt drops a
comma list was **disproven by measurement** — families list survives intact
and resolves to the first registered family).

## 2. Is Inter actually loaded? (Q2)

**Yes.** `load_fonts()` (called in `SpeechStudio.py` before any widget)
registers: `Inter [400,600,700]`, `Libre Franklin [600,700]`,
`JetBrains Mono [400]`. Bundled Inter is **v4.002** (current Google Fonts),
hinted TTFs (`fonthashint: True`).

## 3. Is a fallback in use? (Q3)

**No** — in the measured environment. Control experiment proves the fallback
IS reachable and what it would look like: with `font-size` only (no family),
resolution falls to **DejaVu Sans** — which is **22% narrower per character**
(avgCharW 7px vs Inter's 9px) → *that* is exactly a "compressed" look. On
Windows the equivalent fallback would be Segoe UI. One-minute production
check: **Help → Font Status** (`get_font_status()`) must show `[OK] Inter`.

## 4. Actual pixel size of the rendered QFont? (Q4)

| Surface | QFontInfo.pixelSize | weight | styleName |
|---|---|---|---|
| TopNav NavButton | **14** | 600 | SemiBold |
| Sidebar nav label | **14** | 600 | SemiBold |
| Sidebar ALL-SCENES (active) | **14** | 600 | SemiBold |
| Sidebar Recents primary | **14** | 400 | Regular |
| Sidebar Recents metadata | 12 | 400 | Regular |
| **Plain dialog QLabel** (no own stylesheet) | **13** | 400 | Regular |

## 5. Does the stylesheet override QFont? (Q5)

**Yes — QSS always wins over programmatic `setFont()`.** Widget-level QSS
(`font-size: 14px` in the P3.34 stylesheets) overrides both; on widgets
WITHOUT their own rule, the global theme QSS `* { font-size: 13px; ... }`
applies → **dialog text is genuinely 13px**, not 14. This is a real
inconsistency between surfaces, not a rendering error.

## 6. Is devicePixelRatio affecting rendering? (Q6)

Here: dpr = 1.0 (offscreen). Production: `SpeechStudio.py:216-218` sets
`HighDpiScaleFactorRoundingPolicy.PassThrough` → on Windows 125%/150%
scaling the app runs at a **fractional devicePixelRatio**. Measured raster
experiment (`QT_SCALE_FACTOR` 1.0/1.25/1.5/1.75): Qt re-rasterizes glyphs
AT each scale (cap height ×dpr: 17→21→26→30 device px, **0.0% soft edges**
at every factor) — i.e. **font rasterization itself stays crisp at any
scale**. Fractional dpr becomes harmful only through the *composition* path
(§11/§12).

## 7. Is Qt using a scaled (point-size) font? (Q7)

**No.** Every compared font is pixel-based (`pointSizeF() == -1`). The app
default font is point-based (9pt) but is overridden by QSS everywhere that
matters.

## 8. Is font-weight making glyphs denser? (Q8)

**Yes — measurably.** Weight 600 requests resolve to the REAL
`Inter_SemiBold.ttf` face (`QFontInfo.weight()==600`, styleName
'SemiBold' — **no faux/algorithmic bolding**). Ink measurement at 14px:
- w400: 1,104 inked px, mean alpha 137
- w600: 1,307 inked px (**+18% coverage**), mean alpha 157 (**+15% density**)

Small em + high ink density is a major contributor to the "compressed /
rasterized" perception.

## 9. Ascent / descent / line spacing? (Q9)

Inter @14px (all 14px surfaces): **ascent 14, descent 3, height 17,
lineSpacing 17, leading 0, capHeight 10, averageCharWidth 9**. Note the
perceptual trap: "14px font" means 14px *em* — the visible Latin caps are
only **10px** and lowercase body ~8px. Nothing is vertically squished;
metrics are Inter-correct.

## 10. Is widget height clipping the font? (Q10)

**No.** Font height 17px vs NavButton 48px (minH 48), nav rows 40px,
Recents rows 44px; each label's `sizeHint` height == 17 == font height
(no margin squeeze). No clipping anywhere measured.

## 11. Any transform or scaling on parents? (Q11)

No widget transforms (none of these classes is a graphics-view). **BUT**
the architecture has the decisive soft-render path: `MainWindow` embeds
`AmbientBackgroundGPUV5` — a **QOpenGLWidget** (factory tries GPU first,
`main_window.py:237-240`) — which forces Qt to render the **entire window's
widget layer into an FBO texture composited via OpenGL**, and the central
shell / splitter / editor are all `WA_TranslucentBackground` (blended,
non-opaque). This is the only mechanism in the app that degrades an
otherwise-crisp glyph raster: texture filtering + alpha blending (grayscale
AA, no ClearType subpixel) — and at fractional Windows DPI the texture is
resampled by 1.25/1.5×.

## 12. Global font/style affecting these widgets? (Q12)

Yes: theme QSS `* { font-family: <stack>; font-size: 13px; color: … }`.
Family part works (§1); the **13px base** silently sizes every unstyled
surface (§5). No `letter-spacing` on the 14px labels (only on 0.5px
small-caps labels) — no horizontal squeeze mechanism exists.

## 13. Same label rendered differently elsewhere? (Q13)

All four measured: TopNav **Inter 14 w600**, Sidebar nav **Inter 14 w600**,
Recents primary **Inter 14 w400**, dialog plain **Inter 13 w400**. Same
family everywhere; differences are **size (13 vs 14) and weight (400 vs
600)** — the dialog looks one pixel smaller because it IS.

---

# VERDICT — combination, ranked by measured evidence

| Suspect | Verdict | Evidence |
|---|---|---|
| **DPI/COMPOSITION (rendering path)** | **PRIMARY CAUSE (of "rasterized")** | Font raster proven crisp at every dpr (§6); the only quality-degrading mechanism in the app is QOpenGLWidget FBO composition + translucent tree + PassThrough fractional DPI (§11). Cannot be visually reproduced offscreen — source-verified architecture; see 1-minute user-side confirmation below. |
| **STYLE OVERRIDE inconsistency** | **CONFIRMED (of "claims differ")** | Global 13px vs local 14px — dialog text IS 13px (§5, §13). |
| **FONT WEIGHT density** | **CONFIRMED contributor (of "compressed")** | Real SemiBold face, +18% ink, +15% alpha at 10px visible caps (§8, §9). |
| FONT FAMILY / fallback | **NOT the cause here** (fallback WOULD compress: −22% char width — check Font Status on the user machine) | §1, §3 |
| FONT SIZE numeric | **Correct as claimed** (14 measured) — do NOT bump | §4 |
| FONT METRICS | Normal Inter metrics | §9 |
| WIDGET HEIGHT clipping | None | §10 |
| Point-size scaling / transforms / letter-spacing | None | §7, §11, §12 |

## Root-cause statement

The **QFont objects are correct** (Inter, 14px, real SemiBold, no clipping,
no fallback, crisp at any scale). The degraded look comes from (a) the
**composition/scaling path** — OpenGL-FBO texturing forced by the ambient
`QOpenGLWidget` + `WA_TranslucentBackground` tree + `PassThrough` fractional
Windows scaling, which resamples the whole UI texture; amplified by (b)
**SemiBold density at a 10px visible cap height**, and (c) the **13px dialog
baseline** making "14px" surfaces look inconsistent.

## 1-minute user-side confirmation (no code change)

1. **Help → Font Status** — must show `[OK] Inter` (if `[MISSING]`, the
   Windows machine is on the Segoe-UI fallback → that alone is "compressed").
2. **Windows display scale** — note it (100% / 125% / 150%). At 100% the
   fractional-resample factor disappears.
3. **A/B the ambient renderer** — if the GPU ambient background is disabled
   (renderer falls back to CPU static / plain), the window leaves the FBO
   composition path; if text crispness returns, the primary cause is proven.

## Fix directions when implementation is approved (NOT applied)

1. **Rendering path**: keep the widget layer out of texture composition —
   opaque central shell (drop `WA_TranslucentBackground` on the shell where
   the design allows), or CPU/static ambient at non-100% DPI, or
   `RoundPreferFloor` instead of `PassThrough`.
2. **Consistency**: drive the global base from the same 14px token (or give
   dialog body text an explicit 14px rule) so "14px" means 14 everywhere.
3. **Density** (optional, no size change): nav/section labels at weight 500
   once the composition path is fixed — decide after re-viewing.
4. Keep sizes exactly as-is until 1–3 land, then re-judge visually.
