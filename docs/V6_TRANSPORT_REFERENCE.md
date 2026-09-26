# V6 Horizontal Transport — Reference Specification

**Status**: Active
**Source reference**: `gpu_transport_horizontal_waveform_v12_test_v6.py`
**Implementation**: `ui/panels/transport_gl.py` (GPU renderer) + `ui/panels/waveform_player.py` (integration)

---

## 1. Purpose

This document is the **authoritative visual specification** for the
SpeechStudio bottom transport. It is extracted **line-by-line** from the
validated standalone V6 reference test
(`gpu_transport_horizontal_waveform_v12_test_v6.py`).

The V6 file is **not** a conceptual mockup. It contains real, validated
pixel values, glow formulas, color components, and layout geometry. The
production transport is a **direct GPU port** of the V12 reference
shader — the exact same fragment shader runs in the application as in
the standalone test. The existing application's playback engine (WAV
loading, peak extraction, playhead updates, volume, play/pause/stop
state machine) is preserved unchanged and drives the GPU renderer via
a small set of state-mapping setters.

---

## 2. Reference Canvas

The V6 standalone test renders into a **1280 × 300 px** reference space
(hard-coded in the fragment shader):

```glsl
float scale = min(uResolution.x / 1280.0, uResolution.y / 300.0);
vec2 origin = vec2(
    (uResolution.x - 1280.0 * scale) * 0.5,
    (uResolution.y - 300.0  * scale) * 0.5
);
```

All V6 coordinates below are expressed in this reference space. The
implementation uses `QColor.fromRgbF()` to preserve the exact float
components (no integer rounding loss).

---

## 3. Extracted V6 Values

### 3.1 Island (single rounded surface)

| Property          | V6 Value                                             | Shader source |
|-------------------|------------------------------------------------------|---------------|
| Center            | (640, 150)                                           | `vec2(640.0, 150.0)` |
| Half-size         | (624, 116) → total 1248 × 232 px                     | `vec2(624.0, 116.0)` |
| Corner radius     | 18 px                                                | `roundBox(..., 18.0)` |
| Body color        | `rgb(0.082, 0.074, 0.092)`                           | `vec3(0.082, 0.074, 0.092)` |
| Border color      | `rgb(0.16, 0.13, 0.19)` × 0.75 alpha                 | shader line |
| Canvas margin     | 16 px each side (1280 - 1248 = 32 / 2 = 16)          | derived |

### 3.2 Play / Pause control

| Property              | V6 Value                                              | Shader source |
|-----------------------|-------------------------------------------------------|---------------|
| Center                | (120, 150)                                            | `vec2(120.0, 150.0)` |
| Outer hit radius      | 96 px                                                 | `_hit_test: d <= 96.0` |
| Core disc radius      | 80 px                                                 | `smoothstep(79.0, 80.0, d)` |
| Track ring radius (R) | 88 px                                                 | `const float R = 88.0` |
| Track ring width      | 3 px                                                  | `ringMask(d, R, 3.0)` |
| Active track width    | 4 px                                                  | `ringMask(d, R, 4.0)` |
| Total diameter        | **192 × 192 px**                                      | outer 96 × 2 |
| Core color            | `rgb(0.055, 0.050, 0.065)` ≈ #0E0D11                  | shader |
| Track color           | `rgb(0.165, 0.165, 0.165)` ≈ #2A2A2A                  | shader |
| Icon texture size     | 128 × 128 px                                          | `QImage(128, 128, ...)` |
| Icon UV mapping       | `playP / 64.0 + vec2(0.5)` (icon spans ±64 px)         | shader |

### 3.3 Stop control

| Property          | V6 Value                                              | Shader source |
|-------------------|-------------------------------------------------------|---------------|
| Center            | (315, 150)                                            | `vec2(315.0, 150.0)` |
| Half-size         | (40, 40) → total 80 × 80 px                           | `vec2(40.0)` |
| Corner radius     | 12 px                                                 | `roundBox(stopP, vec2(40.0), 12.0)` |
| Body color        | `rgb(0.1098, 0.1059, 0.1059)` ≈ #1C1B1B                | shader |
| Border color      | purple × 0.55                                         | `purple * stopBorder * 0.55` |

### 3.4 Waveform container

| Property          | V6 Value                                              | Shader source |
|-------------------|-------------------------------------------------------|---------------|
| Center            | (850, 150)                                            | `vec2(850.0, 150.0)` |
| Half-size         | (390, 96) → total 780 × 192 px                        | `vec2(390.0, 96.0)` |
| Corner radius     | 10 px                                                 | `roundBox(waveP, waveHalf, 10.0)` |
| Body color        | `rgb(0.065, 0.060, 0.072)` ≈ #111012                  | shader |
| Border color      | `rgb(0.12, 0.10, 0.15)` × 0.40 weight                  | shader |
| Inactive bar      | `rgb(0.28, 0.24, 0.34)` ≈ #483D57                     | shader |
| Active bar        | primary #D0BCFF                                       | shader |
| Center line       | `rgb(0.22, 0.18, 0.28)` × 0.25 weight                 | shader |
| Bar max half-h    | 34 px (amplitude × 34.0)                               | shader |

### 3.5 Layout gaps (left to right)

| Gap                | V6 Value | Derivation |
|--------------------|----------|------------|
| Island left margin | 16 px    | 1280 - 1248 = 32 / 2 |
| Play left edge     | 24 px    | 120 - 96 |
| Play→Stop gap      | **59 px** | 275 (Stop left) - 216 (Play right) |
| Stop→Wave gap      | **105 px** | 460 (Wave left) - 355 (Stop right) |
| Wave right edge    | 1240 px  | 850 + 390 |
| Island right edge  | 1264 px  | 640 + 624 |
| Right padding      | 24 px    | 1280 - 1264 + 16 = 24 (matches Play left) |

### 3.6 Colors (exact float values preserved via `QColor.fromRgbF()`)

| Token              | Float RGB                                              | Hex (8-bit) |
|--------------------|--------------------------------------------------------|-------------|
| `V6_PURPLE_GLOW`   | (0.6588235, 0.3333333, 0.9686275)                     | #A855F7 |
| `V6_PRIMARY`       | (0.8156863, 0.7372549, 1.0)                            | #D0BCFF |
| `V6_PLAY_CORE`     | (0.055, 0.050, 0.065)                                  | #0E0D11 |
| `V6_PLAY_TRACK`    | (0.165, 0.165, 0.165)                                  | #2A2A2A |
| `V6_STOP_BODY`     | (0.1098, 0.1059, 0.1059)                               | #1C1B1B |
| `V6_ISLAND_BODY`   | (0.082, 0.074, 0.092)                                  | #151318 |
| `V6_ISLAND_BORDER` | (0.16, 0.13, 0.19)                                     | #292130 |
| `V6_WAVE_BODY`     | (0.065, 0.060, 0.072)                                  | #111012 |
| `V6_WAVE_BORDER`   | (0.12, 0.10, 0.15)                                     | #1F1A26 |
| `V6_WAVE_INACTIVE` | (0.28, 0.24, 0.34)                                     | #483D57 |
| `V6_WAVE_CENTER_LINE` | (0.22, 0.18, 0.28)                                  | #382E47 |

### 3.7 Icon SVG paths (Material Symbols)

| Icon        | Path                                          | V6 constant |
|-------------|-----------------------------------------------|-------------|
| play_arrow  | `M8 5v14l11-7z`                               | `PLAY_ARROW_PATH` |
| pause       | `M6 19h4V5H6v14zm8-14v14h4V5h-4z`             | `PAUSE_PATH` |
| stop        | `M6 6h12v12H6z`                               | `STOP_PATH` |

---

## 4. Glow Formulas (pixel-accurate reproduction)

The V6 fragment shader computes glow via exponential falloff. The
implementation in `waveform_player.py` reproduces these formulas
**exactly** using numpy per-pixel computation, then composites the
resulting alpha array onto a `QImage` and converts to `QPixmap`.

### 4.1 Play/Pause glow

**V6 shader**:
```glsl
float baseGlow      = exp(-max(d - 94.0, 0.0) / 15.0) * 0.22;
float playingGlow   = exp(-max(d - 94.0, 0.0) / 25.0) * 0.42
                    + exp(-max(d - 94.0, 0.0) / 45.0) * 0.22;
float glow          = mix(baseGlow, playingGlow, uPlaying);
glow               *= 1.0 + 0.18 * uPlayHover;
color              += purple * glow;
```

**Implementation** (`_build_play_glow_pixmap()`):
```python
d_clip = np.maximum(d - 94.0, 0.0)
base = np.exp(-d_clip / 15.0) * 0.22
playing_glow = (
    np.exp(-d_clip / 25.0) * 0.42
    + np.exp(-d_clip / 45.0) * 0.22
)
pf = 1.0 if playing else 0.0
glow = (1.0 - pf) * base + pf * playing_glow
if hover:
    glow = glow * 1.18
```

Image: 300 × 300 px (extends ~150 px from center; covers the full falloff
range of ~80 px past radius 94).

### 4.2 Stop glow

**V6 shader**:
```glsl
float stopGlow = exp(-max(stopD, 0.0) / 15.0) * 0.40;
stopGlow      *= 1.0 + 0.30 * uStopHover;
if (uPressed > 0.5 && uStopHover > 0.5) {
    stopGlow += exp(-max(stopD, 0.0) / 25.0) * 0.70;
}
color += purple * stopGlow;
```

**Implementation** (`_build_stop_glow_pixmap()`):
```python
sdf = _rounded_rect_sdf(size, size, 40, 40, 12)  # V6 roundBox()
sdf_clip = np.maximum(sdf, 0.0)
glow = np.exp(-sdf_clip / 15.0) * 0.40
if hover:
    glow = glow * 1.30
if pressed and hover:
    glow = glow + np.exp(-sdf_clip / 25.0) * 0.70
```

The rounded-rectangle SDF (`_rounded_rect_sdf()`) reproduces the V6
`roundBox()` GLSL function:
```glsl
vec2 q = abs(p) - halfSize + vec2(radius);
return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
```

### 4.3 Glow caching strategy

There are 4 visual states for each control:

| Control | States (cached as QPixmap) |
|---------|---------------------------|
| Play    | (playing=F, hover=F), (F, T), (T, F), (T, T) |
| Stop    | (hover=F, pressed=F), (F, T), (T, F), (T, T) |

Pixmaps are pre-built in `__init__()` and selected at paint time via a
dict lookup — no per-frame numpy computation.

---

## 5. Implementation Architecture

### 5.1 Class structure

```
WaveformPlayer(QWidget)              ← the V6 transport island itself
├── TransportPlayButton(QWidget)    ← 192×192 Play/Pause with glow + ring
├── TransportStopButton(QWidget)    ← 80×80 Stop with glow + body
├── WaveformCanvas(QWidget)          ← 192-tall waveform with real WAV data
└── Volume slider + icon            ← right-side audio controls
```

### 5.2 Widget heights (matching V6)

| Widget                  | Height |
|-------------------------|--------|
| WaveformPlayer (island) | 232 px (V6_ISLAND_HEIGHT) |
| TransportPlayButton     | 192 px (V6_PLAY_DIAMETER) |
| TransportStopButton     | 80 px (V6_STOP_SIZE) |
| WaveformCanvas          | 192 px (V6_WAVE_HEIGHT, matches Play) |

### 5.3 Layout (QHBoxLayout, contentsMargins = 24/20/24/20)

```
[24px] [Play 192] [59px] [Stop 80] [105px] [Waveform flexible] [24px] [Volume]
```

The 24px left/right padding matches V6 (Play left edge at x=24 inside the
island). The 20px top/bottom padding centers the 192 px controls inside the
232 px island (232 - 192 = 40 / 2 = 20).

### 5.4 V6 constants (module-level)

All V6 reference values are defined as module-level constants in
`waveform_player.py` (prefixed `V6_`). This makes the values easy to audit
against the V6 shader source and prevents accidental drift.

### 5.5 Progress arc rendering

The V6 progress ring shows playback progress. The implementation maps the
existing `_play_position` (0..1 fraction of audio duration) to the arc
fraction:

```python
# V6 starts arc at 6 o'clock, grows clockwise as progress increases
start_angle = 270 * 16  # 6 o'clock in Qt 1/16-degree units
span_angle = int(-self._progress * 360 * 16)  # negative = clockwise sweep
p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), start_angle, span_angle)
```

This drives the ring from `WaveformPlayer.set_play_position()`:
```python
def set_play_position(self, position_sec):
    if self._canvas._duration > 0:
        pos = position_sec / self._canvas._duration
        self._canvas.set_play_position(pos)
        self._play_btn.set_progress(pos)  # ← drives V6 ring
```

---

## 6. Preserved Application Functionality

The transport keeps the existing playback engine **completely intact**:

### 6.1 Signals (unchanged)

| Signal              | Trigger |
|---------------------|---------|
| `play_requested`    | Play/Pause clicked in stopped/paused state |
| `pause_requested`   | Play/Pause clicked in playing state |
| `stop_requested`    | Stop clicked |
| `seek_requested(float seconds)` | Waveform click/drag |
| `volume_changed(float 0..1)` | Volume slider moved |

### 6.2 Public API (unchanged signatures)

| Method | Purpose |
|--------|---------|
| `set_audio_info(duration, sample_rate, output_path)` | Load WAV + reset state |
| `set_play_position(position_sec)` | Update playhead + V6 ring |
| `set_playback_state(state)` | "stopped" / "playing" / "paused" |
| `set_volume(volume 0..1)` | Set slider (blocks signals) |
| `get_volume() → float` | Read slider value |
| `clear()` | Reset canvas + buttons |
| `current_path` (property) | Current audio file path |

### 6.3 Preserved engine components

- **WAV loading** — `WaveformCanvas.load_wav()` unchanged (numpy peak extraction)
- **Peak recomputation on resize** — `resizeEvent()` triggers reload
- **Click-to-seek** — left-click on waveform seeks to that position
- **Drag-to-scrub** — left-drag on waveform continuously seeks
- **Hover indicator** — subtle dashed line follows cursor
- **Mono downmix** — multi-channel WAVs are averaged to mono for visualization

---

## 7. Visual Hierarchy (V6-faithful)

The implementation reproduces the V6 visual hierarchy:

1. **Play/Pause is dominant** — 192 × 192 px (the largest control in the
   transport). Its glow halo, dark core, and progress ring make it the
   visual anchor.

2. **Stop is secondary** — 80 × 80 px (less than half Play's diameter).
   Smaller glow, smaller body, but still clearly identifiable as a stop
   control thanks to the prominent Material Symbols icon.

3. **Waveform is the largest area** — 192 px tall (matching Play height),
   flexible width. Contains the real audio data with V6-style color mix
   (inactive → primary) around the playhead.

4. **Volume controls are minimal** — small icon + 120 px slider on the
   right side, inside the same island surface.

5. **Significant breathing room** between Stop and Waveform (105 px,
   matching V6 exactly) — this is intentional V6 design, not a layout
   accident.

---

## 8. Validation Checklist

Use this checklist to verify the implementation matches V6:

- [ ] Play/Pause diameter = 192 px (V6_PLAY_DIAMETER)
- [ ] Stop size = 80 × 80 px (V6_STOP_SIZE)
- [ ] Waveform height = 192 px (V6_WAVE_HEIGHT, matches Play height)
- [ ] Play glow visible (base 0.22 alpha, playing 0.42 + 0.22)
- [ ] Stop glow visible (base 0.40 alpha)
- [ ] Play→Stop gap = 59 px (V6_PLAY_TO_STOP_GAP)
- [ ] Stop→Wave gap = 105 px (V6_STOP_TO_WAVE_GAP)
- [ ] Island corner radius = 18 px (V6_ISLAND_RADIUS)
- [ ] Island body color = rgb(21, 19, 24) (V6_ISLAND_BODY)
- [ ] Waveform body color = rgb(17, 15, 18) (V6_WAVE_BODY)
- [ ] Progress ring starts at 6 o'clock, grows clockwise
- [ ] Material Symbols icons: play_arrow, pause, stop
- [ ] Icon texture size = 128 px (V6_ICON_TEXTURE_SIZE)
- [ ] Existing playback signals/API preserved

---

## 9. Future Work

- **Friendly/Advanced Control Panel** redesign — deferred; will use the
  same V6 visual language (dark surfaces, purple accent, glow on primary
  actions).
- **Multi-block narration waveform** — currently the waveform shows the
  currently-playing file. A future enhancement could show colored regions
  for each narration block, using the V6 inactive/active color mix as the
  base style.
- **Animation polish** — V6 uses a slow ring rotation during playback
  (`ROTATION_SECONDS = 4.0`). This could be added as a subtle ambient
  animation on the progress ring when playing.

---

## 10. GPU Rendering Architecture (V12 direct port)

The transport is rendered by a single `QOpenGLWidget` subclass
(`HorizontalTransportGL` in `ui/panels/transport_gl.py`). The
fragment shader is reproduced **verbatim** from the validated V12
standalone reference — same uniforms, same SDF functions, same glow
formulas, same progress ring math, same round-cap geometry, same icon
sampling. The only intentional omission is the mock waveform
(`waveformAmplitude()` + bar rendering), because the real waveform
data is painted by the existing `WaveformCanvas` (QPainter, real WAV
peaks) on top of the GPU-drawn container.

### 10.1 GL pipeline

```
QSurfaceFormat (4.6 CompatibilityProfile, set globally in SpeechStudio.py)
    ↓
HorizontalTransportGL(QOpenGLWidget)
    ↓
initializeGL():
    QOpenGLShaderProgram (vertex + fragment)
    QOpenGLBuffer (fullscreen VBO, 6 vertices, 2 floats each)
    QOpenGLTexture × 3 (play_arrow, pause, stop — Material Symbols Outlined)
    ↓
resizeGL(): glViewport(0, 0, w, h)
    ↓
paintGL():
    program.bind()
    glUniform2f(uResolution, w, h)
    glUniform1f(uPlaying, ...)
    glUniform1f(uPlayHover, ...)
    glUniform1f(uStopHover, ...)
    glUniform1f(uPressed, ...)
    glUniform1f(uOffset, ...)
    glUniform1f(uRotation, ...)
    glUniform1f(uPlayhead, ...)
    play_texture.bind(0); pause_texture.bind(1); stop_texture.bind(2)
    glUniform1i(uPlayIcon, 0); glUniform1i(uPauseIcon, 1); glUniform1i(uStopIcon, 2)
    vbo.bind()
    glDrawArrays(GL_TRIANGLES, 0, 6)
    ↓
fragment shader paints EVERY pixel in one pass:
    - island body + border (roundBox SDF)
    - Play/Pause glow (exp falloff from circle SDF)
    - Play/Pause core disc
    - progress track ring (ringMask, radius 88, half-width 3)
    - active progress arc (ringMask, radius 88, half-width 4)
    - round caps (start + end, same coordinate system as the arc)
    - Play/Pause icon (GPU-sampled Material Symbols texture)
    - Stop glow (exp falloff from rounded-rect SDF)
    - Stop body + border
    - Stop icon (GPU-sampled Material Symbols texture)
    - waveform CONTAINER (dark rounded surface + border + center line + playhead)
```

### 10.2 What is NOT done in the GPU renderer

- **Waveform bars** — painted by the existing `WaveformCanvas` (QPainter)
  on top of the GPU container, using real WAV peak data extracted via
  numpy. The GPU renderer only draws the container surface.
- **Volume slider** — the existing `QSlider` is positioned on top of
  the GPU island at the right edge.
- **Playback state machine** — owned by `WaveformPlayer`, which calls
  `set_playing()` / `set_progress()` / `set_playhead()` /
  `trigger_stop_reset()` on the GPU renderer.

### 10.3 Glow architecture check

- GPU glow: **YES** (computed in the fragment shader via `exp()` falloff
  from the circle/rounded-rect SDFs)
- QPainter glow: **NO** (QPainter is only used to rasterise the Material
  Symbols SVG paths into QOpenGLTextures — that is the validated V12
  `icon_texture()` approach, not a glow path)
- QPixmap glow: **NO**
- NumPy glow: **NO**
- Glow derived from GPU geometry: **YES** (the SDF that defines the
  control shape also drives the glow falloff)
- Glow resolution independent: **YES** (the fragment shader recomputes
  every pixel on every paint, so the glow scales perfectly with the
  widget size)

### 10.4 Opaque rendering (Windows stability)

The transport shader outputs `gl_FragColor = vec4(color, 1.0)` —
**fully opaque** everywhere, matching the V12 reference exactly. The
QOpenGLWidget does NOT set `WA_TranslucentBackground`. This is critical
for Windows stability:

- `QOpenGLWidget` + `WA_TranslucentBackground` requires an alpha buffer
  in the `QSurfaceFormat`. The global `QSurfaceFormat` (set in
  `SpeechStudio.py`) does NOT request an alpha buffer, so translucent
  backgrounds would render as black and could cause rendering failures
  or silent crashes on Windows.
- The ambient GPU V5 renderer (already working on the user's system)
  also outputs `alpha=1.0` everywhere and does NOT set
  `WA_TranslucentBackground`.
- The transport is an opaque bar at the bottom of the window. The
  ambient background shows through the 16px margins around the
  `main_area` (above the transport and on the sides), not through the
  transport itself.

### 10.5 Defensive GL error handling

Both `initializeGL()` and `paintGL()` are wrapped in `try/except`.
If GL initialization fails (e.g. shader compile error, missing uniform,
VBO creation failure, texture creation failure) or a runtime GL error
occurs during painting, the error is logged to `logs/engine.log` and
the `_init_failed` flag is set. `paintGL` checks this flag and returns
early — the widget renders nothing, but the **application does NOT
crash**. This matches the defensive pattern used by the ambient GPU V5
renderer.

### 10.6 State mapping (existing app → GPU renderer)

| Existing app state | GPU renderer call | Effect |
|---|---|---|
| `WaveformPlayer.set_playback_state("playing")` | `gl.set_playing(True)` | icon swaps to `pause`; glow strengthens |
| `WaveformPlayer.set_playback_state("paused")` | `gl.set_playing(False)` | icon swaps to `play_arrow`; glow returns to base |
| `WaveformPlayer.set_playback_state("stopped")` | `gl.set_progress(0.0)` + `gl.set_playhead(0.0)` | progress ring empties (smooth ~350 ms unwind via V12 STOP_RESET_SECONDS) |
| `WaveformPlayer.set_play_position(sec)` | `gl.set_progress(sec/duration)` + `gl.set_playhead(sec/duration)` | progress ring fills proportionally; GPU playhead line moves |
| User clicks Stop | `gl.trigger_stop_reset()` | target_offset jumps to CIRCUMFERENCE; _tick smooths over ~350 ms |
| User hovers Play/Pause | `gl.play_hover` (set via mouseMoveEvent) | glow × (1 + 0.18) |
| User hovers Stop | `gl.stop_hover` (set via mouseMoveEvent) | stopGlow × (1 + 0.30) |
| User presses Stop | `gl.pressed` (set via mousePressEvent) | stopGlow += 0.70 |

### 10.7 Click handling

The GPU widget's `_hit_test()` reproduces the V12 reference exactly:
it scales the mouse position into the 1280×300 reference canvas, then
tests the Play circle (radius 96 around (120, 150)) and the Stop
rounded rect (±40 around (315, 150)). Clicks inside Play emit
`play_clicked`; clicks inside Stop emit `stop_clicked` + trigger the
smooth reset. Clicks outside both controls (e.g. on the waveform) do
NOT activate either — the hover states are independent.

### 10.8 Validation

Run `python3 tools/verify_transport_gl.py` for the full static
validation suite (126 checks covering GL architecture, shader
completeness, V12 progress + caps + rotation, V12 glow, V12 icon
textures, V6 horizontal geometry, state mapping, public API
preservation, and no-leftover-old-code checks).
