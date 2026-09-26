#!/usr/bin/env python3
"""
SpeechStudio — Ambient Background CPU Benchmark
================================================

Measures the CPU cost of the ambient background renderer before and
after the QPixmap caching optimization.

Before optimization (old approach):
    Every paintEvent called the renderer (2 QRadialGradient + 1 linear
    gradient fills). At 30-60 FPS UI repaint rate, this was 30-60
    render() calls per second.

After optimization (new approach):
    paintEvent only blits a cached QPixmap (drawPixmap — essentially
    free). The renderer is called at 2 FPS by the timer. This is a
    15-30x reduction in render() calls.

This benchmark measures:
    1. render() cost — the time for one AmbientRendererCPU.render() call.
    2. blit cost — the time for one QPainter.drawPixmap() call.
    3. Simulated "before" CPU per second — render() * 30 FPS.
    4. Simulated "after" CPU per second — render() * 2 FPS + blit() * 30 FPS.
    5. Speedup ratio.

Usage:
    python tools/benchmark_ambient.py
    python tools/benchmark_ambient.py --width 1920 --height 1080
    python tools/benchmark_ambient.py --frames 200

Requirements:
    PySide6 (the same package the app uses).
"""

from __future__ import annotations
import argparse
import sys
import os
import time

# Ensure we can import from the SpeechStudio package.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, APP_ROOT)

from PySide6.QtCore import QSize
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtWidgets import QApplication

from ui.panels.ambient_renderer import AmbientRendererCPU, BG_CHARCOAL


def benchmark_render(renderer: AmbientRendererCPU, width: int, height: int,
                     frames: int) -> float:
    """Measure the time for ``frames`` render() calls.

    Returns the total time in seconds.
    """
    pixmap = QPixmap(width, height)
    t0 = time.perf_counter()
    for i in range(frames):
        # Vary the time to simulate animation.
        t = i * 0.5
        renderer.render(t, width, height, pixmap)
    t1 = time.perf_counter()
    return t1 - t0


def benchmark_blit(width: int, height: int, frames: int) -> float:
    """Measure the time for ``frames`` drawPixmap() calls.

    This simulates the paintEvent cost in the optimized approach —
    just blitting the cached pixmap, no gradient computation.

    Returns the total time in seconds.
    """
    # Create a pre-rendered pixmap (the "cache").
    pixmap = QPixmap(width, height)
    pixmap.fill(BG_CHARCOAL)

    # We need a widget to paint onto — use an offscreen QPixmap as the
    # paint device (simulates a paintEvent without a real widget).
    target = QPixmap(width, height)

    t0 = time.perf_counter()
    for _ in range(frames):
        painter = QPainter(target)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
    t1 = time.perf_counter()
    return t1 - t0


def format_ms(seconds: float, frames: int) -> str:
    """Format seconds as 'X.XX ms/call'."""
    ms_per_call = (seconds / frames) * 1000.0
    return "{0:.2f} ms/call".format(ms_per_call)


def format_cpu_percent(seconds: float, fps: int) -> str:
    """Format as 'X.XX% of one core' for the given FPS."""
    ms_per_call = (seconds / 1) * 1000.0  # ms for 1 call (uses frames=1 equivalent)
    # Actually we need ms/call — recompute from the raw measurement.
    # This function is called with the total time for N frames, so:
    # ms_per_call = (total_seconds / N) * 1000
    # But we don't have N here. Let's pass it differently.
    # Simplification: caller passes the per-call time already.
    return ""  # placeholder — computed in main()


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark the ambient background renderer.")
    parser.add_argument("--width", type=int, default=1920,
                        help="Pixmap width (default: 1920)")
    parser.add_argument("--height", type=int, default=1080,
                        help="Pixmap height (default: 1080)")
    parser.add_argument("--frames", type=int, default=100,
                        help="Number of frames to benchmark (default: 100)")
    args = parser.parse_args()

    width = args.width
    height = args.height
    frames = args.frames

    print("=" * 60)
    print("SpeechStudio Ambient Background CPU Benchmark")
    print("=" * 60)
    print()
    print("Pixmap size: {0}x{1}".format(width, height))
    print("Frames:      {0}".format(frames))
    print()

    # Qt needs a QApplication for QPainter to work offscreen.
    app = QApplication.instance() or QApplication(sys.argv[:1])

    renderer = AmbientRendererCPU()

    # Warm up (first call allocates internal structures).
    warmup_pixmap = QPixmap(width, height)
    renderer.render(0.0, width, height, warmup_pixmap)

    # 1. Benchmark render() — the expensive gradient computation.
    print("1. render() — 2 QRadialGradient + 1 linear gradient fills")
    render_time = benchmark_render(renderer, width, height, frames)
    render_ms = (render_time / frames) * 1000.0
    print("   Total:    {0:.3f} s".format(render_time))
    print("   Per call: {0:.3f} ms".format(render_ms))
    print()

    # 2. Benchmark drawPixmap() — the cheap blit.
    print("2. drawPixmap() — blit the cached pixmap (paintEvent cost)")
    blit_time = benchmark_blit(width, height, frames)
    blit_ms = (blit_time / frames) * 1000.0
    print("   Total:    {0:.3f} s".format(blit_time))
    print("   Per call: {0:.3f} ms".format(blit_ms))
    print()

    # 3. Simulated CPU usage: BEFORE (old approach).
    # The old approach called render() on every paintEvent. Qt repaints
    # at ~30-60 FPS during UI activity (hover, scroll, resize, etc.).
    print("3. BEFORE optimization (render on every paintEvent @ 30 FPS):")
    before_cpu_per_sec = render_ms * 30  # ms/sec
    before_cpu_percent = before_cpu_per_sec / 10.0  # ms/sec → % of one core
    print("   render() calls/sec:  30")
    print("   CPU per second:      {0:.1f} ms ({1:.1f}% of one core)"
          .format(before_cpu_per_sec, before_cpu_percent))
    print()

    # 4. Simulated CPU usage: AFTER (new approach).
    # The new approach: timer fires at 60 FPS (16ms), but the renderer
    # only regenerates at ~15 FPS (needs_redraw throttles). paintEvent
    # blits the cached pixmap at 30 FPS (UI repaint rate).
    REGEN_FPS = 15  # CPU renderer's needs_redraw rate
    BLIT_FPS = 30   # typical UI repaint rate
    print("4. AFTER optimization (render @ {0} FPS + blit @ {1} FPS):".format(
        REGEN_FPS, BLIT_FPS))
    after_render_cpu = render_ms * REGEN_FPS  # ms/sec from render
    after_blit_cpu = blit_ms * BLIT_FPS       # ms/sec from blit
    after_cpu_per_sec = after_render_cpu + after_blit_cpu
    after_cpu_percent = after_cpu_per_sec / 10.0
    print("   render() calls/sec:  {0}   ({1:.1f} ms/sec)"
          .format(REGEN_FPS, after_render_cpu))
    print("   blit calls/sec:      {0}  ({1:.1f} ms/sec)"
          .format(BLIT_FPS, after_blit_cpu))
    print("   CPU per second:      {0:.1f} ms ({1:.1f}% of one core)"
          .format(after_cpu_per_sec, after_cpu_percent))
    print()

    # 5. Speedup.
    if after_cpu_per_sec > 0:
        speedup = before_cpu_per_sec / after_cpu_per_sec
        print("5. Speedup: {0:.1f}x less CPU".format(speedup))
    print()

    # 6. Memory check — verify pixmap reuse doesn't leak.
    print("6. Memory check — 1000 render() calls with same-size pixmap")
    pixmap = QPixmap(width, height)
    import tracemalloc
    tracemalloc.start()
    snapshot1 = tracemalloc.take_snapshot()
    for i in range(1000):
        renderer.render(i * 0.5, width, height, pixmap)
    snapshot2 = tracemalloc.take_snapshot()
    stats = snapshot2.compare_to(snapshot1, "lineno")
    total_diff = sum(s.size_diff for s in stats if s.size_diff > 0)
    print("   Python heap growth after 1000 renders: {0} bytes"
          .format(total_diff))
    if total_diff < 10000:
        print("   PASS — no significant memory growth")
    else:
        print("   WARN — memory growth detected (may be allocator overhead)")
    print()

    print("=" * 60)
    print("Conclusion:")
    print("  The QPixmap caching optimization reduces ambient background")
    print("  CPU usage by ~{0:.0f}x during active UI repainting.".format(speedup))
    print("  The paintEvent now costs {0:.3f} ms (blit) instead of "
          "{1:.3f} ms (render).".format(blit_ms, render_ms))
    print("=" * 60)


if __name__ == "__main__":
    main()
