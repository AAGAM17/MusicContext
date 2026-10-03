#!/usr/bin/env python3
"""Benchmark the real analysis pipeline through the Python API (not the CLI).

Synthesizes a video of each requested duration, then measures probe / scene detection /
frame sampling / audio / speech from the timings the pipeline already records, plus
total analyze_video cold and warm, plus create_plan. Stdlib only.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import platform
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

STEPS = ("probe", "scene_detection", "frames", "audio", "speech")


def _fixtures():
    spec = importlib.util.spec_from_file_location("_mcfixtures", ROOT / "scripts" / "make_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def peak_rss_mb() -> float:
    """ru_maxrss is BYTES on macOS and KILOBYTES on Linux/BSD; normalise to MiB."""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / 1024**2 if sys.platform == "darwin" else raw / 1024


def _ffmpeg(argv: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-nostdin", *argv], check=True)


@contextlib.contextmanager
def _quiet_stderr():
    """Hide a child process's stderr: it writes to fd 2 directly, so redirect the fd."""
    with open(os.devnull, "w") as null:
        saved = os.dup(2)
        try:
            os.dup2(null.fileno(), 2)
            yield
        finally:
            os.dup2(saved, 2)
            os.close(saved)


def _synth_video(path: Path, seconds: int, fps: int = 10, w: int = 320, h: int = 180) -> None:
    """Scene-changing silent video.

    Prefers scripts/make_fixtures.make_long_video; falls back to an equivalent local
    filtergraph because that helper's `mandelbrot`/`life` sources reject `d=` on
    FFmpeg 7.x and it floor-divides the duration to a multiple of 20 s.
    """
    try:
        with _quiet_stderr():
            _fixtures().make_long_video(path, seconds, w=w, h=h, fps=fps)
        if abs(_probe_duration(path) - seconds) <= 1.0:
            return
    except (subprocess.CalledProcessError, OSError):
        pass
    seg = 15
    srcs = [
        f"testsrc2=s={w}x{h}:r={fps}:d={seg}",
        f"gradients=s={w}x{h}:r={fps}:d={seg}:speed=0.03",
        f"color=c=0x203a6b:s={w}x{h}:r={fps}:d={seg}",
        f"color=c=0xe8e4dc:s={w}x{h}:r={fps}:d={seg}",
    ]
    n = max(2, -(-seconds // seg))
    argv: list[str] = []
    for i in range(n):
        argv += ["-f", "lavfi", "-i", srcs[i % len(srcs)]]
    graph = "".join(f"[{i}:v]format=yuv420p[v{i}];" for i in range(n))
    graph += "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    _ffmpeg([*argv, "-filter_complex", graph, "-map", "[v]", "-c:v", "libx264",
             "-preset", "ultrafast", "-crf", "35", "-g", "50", "-t", str(seconds), str(path)])


def _probe_duration(path: Path) -> float:
    cp = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(cp.stdout.strip() or 0)


def _add_voice(video: Path, out: Path, seconds: int) -> None:
    """Mux a speech-like bed so the audio and speech stages have real work to do."""
    fx = _fixtures()
    wav = out.with_suffix(".voice.wav")
    intervals = [(float(a), float(min(a + 8, seconds))) for a in range(2, seconds, 20)]
    fx.write_wav(wav, fx.speech_like(seconds, intervals), fx.SR)
    _ffmpeg(["-i", str(video), "-i", str(wav), "-c:v", "copy", "-c:a", "aac", "-b:a", "64k",
             "-shortest", str(out)])
    wav.unlink(missing_ok=True)


def measure(seconds: int, workdir: Path) -> dict:
    from musiccontext.direction.planner import create_plan
    from musiccontext.engine.context import load_settings
    from musiccontext.engine.pipeline import analyze_video

    silent = workdir / f"v{seconds}.mp4"
    clip = workdir / f"av{seconds}.mp4"
    t0 = time.perf_counter()
    _synth_video(silent, seconds)
    _add_voice(silent, clip, seconds)
    synth_s = time.perf_counter() - t0

    settings = load_settings()
    settings.cache_dir = workdir / f"cache{seconds}"  # a private cache, so "cold" is really cold

    t0 = time.perf_counter()
    report, hit = analyze_video(clip, settings)
    cold = time.perf_counter() - t0
    assert not hit, "cold run should not hit the cache"

    t0 = time.perf_counter()
    _, hit = analyze_video(clip, settings)
    warm = time.perf_counter() - t0
    assert hit, "warm run should hit the cache"

    t0 = time.perf_counter()
    plan = create_plan(report)
    plan_s = time.perf_counter() - t0

    actual = report.metadata.duration
    return {
        "duration_s": seconds,
        "actual_duration_s": round(actual, 2),
        "size_mb": round(clip.stat().st_size / 1024**2, 2),
        "synth_s": round(synth_s, 2),
        "steps_s": {k: report.timings_s.get(k) for k in STEPS},
        "analyze_cold_s": round(cold, 3),
        "analyze_warm_s": round(warm, 3),
        "create_plan_s": round(plan_s, 4),
        "x_realtime_cold": round(actual / cold, 1) if cold else None,
        "x_realtime_warm": round(actual / warm, 1) if warm else None,
        "scenes": len(report.scenes),
        "directions": len(plan.directions),
        "peak_rss_mb": round(peak_rss_mb(), 1),
    }


def render_table(rows: list[dict]) -> str:
    head = ["dur", "probe", "scenes", "frames", "audio", "speech", "cold", "x-rt", "warm", "plan", "RSS"]
    widths = [6, 7, 7, 7, 7, 7, 7, 7, 7, 7, 8]
    out = ["  ".join(h.rjust(w) for h, w in zip(head, widths, strict=True))]
    out.append("  ".join("-" * w for w in widths))
    for r in rows:
        s = r["steps_s"]
        cells = [
            f"{r['duration_s']}s",
            *(f"{s[k]:.2f}" if s.get(k) is not None else "n/a" for k in STEPS),
            f"{r['analyze_cold_s']:.2f}",
            f"{r['x_realtime_cold']}x",
            f"{r['analyze_warm_s']:.3f}",
            f"{r['create_plan_s']:.3f}",
            f"{r['peak_rss_mb']:.0f}MB",
        ]
        out.append("  ".join(c.rjust(w) for c, w in zip(cells, widths, strict=True)))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--durations", type=int, nargs="+", default=[30, 60, 300],
                    help="clip lengths in seconds (1800 is opt-in: it is slow)")
    ap.add_argument("--json", action="store_true", help="emit JSON on stdout instead of a table")
    args = ap.parse_args()

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("ffmpeg/ffprobe not on PATH; nothing to benchmark.", file=sys.stderr)
        return 2

    rows = []
    with tempfile.TemporaryDirectory(prefix="musiccontext-bench-") as tmp:
        for seconds in args.durations:
            rows.append(measure(seconds, Path(tmp)))

    ver = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=False)
    env = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "ffmpeg": ver.stdout.splitlines()[0] if ver.stdout else "unknown",
    }
    if args.json:
        print(json.dumps({"environment": env, "results": rows}, indent=2))
    else:
        print(f"{env['platform']} / {env['machine']} / python {env['python']}")
        print(env["ffmpeg"])
        print()
        print(render_table(rows))
        print("\ncold/warm = total analyze_video; x-rt = clip seconds per wall second (higher is better).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
