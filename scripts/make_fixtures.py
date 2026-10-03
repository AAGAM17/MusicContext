"""Deterministic synthetic fixtures (no downloads, no external assets).

demo.mp4  - 24 s, 4 visually distinct scenes (calm -> busier -> energetic -> calm) with a
            speech-like voiceover in two intervals (2.0-8.0 s and 13.0-19.0 s) over a quiet bed.
click.wav - 120 BPM click/kick track used to validate tempo estimation.
"""

from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 16000
SPEECH_INTERVALS = [(2.0, 8.0), (13.0, 19.0)]
SCENE_BOUNDS = [0.0, 5.0, 11.0, 17.0, 24.0]


def speech_like(duration: float, intervals, seed: int = 7, sr: int = SR) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.zeros(int(duration * sr), np.float32)
    for a, b in intervals:
        t = a
        while t < b - 0.1:
            syl = rng.uniform(0.12, 0.2)
            if rng.random() < 0.08:
                t += rng.uniform(0.25, 0.5)  # natural pause
                continue
            f0 = rng.uniform(110, 190)
            n = int(syl * sr)
            tt = np.arange(n) / sr
            sig = np.zeros(n, np.float32)
            for h in range(1, 28):
                f = f0 * h
                if f > 3600:
                    break
                formant = np.exp(-((f - 500) / 350) ** 2) + 0.7 * np.exp(-((f - 1500) / 600) ** 2) + 0.35 * np.exp(-((f - 2600) / 700) ** 2)
                sig += (formant / h**0.3) * np.sin(2 * np.pi * f * tt + rng.uniform(0, 6.28))
            env = np.hanning(n) ** 0.7
            i0 = int(t * sr)
            out[i0 : i0 + n] += (sig * env * 0.12)[: len(out) - i0]
            t += syl + rng.uniform(0.01, 0.05)
    out += rng.normal(0, 0.002, len(out)).astype(np.float32)
    return out


def write_wav(path: Path, x: np.ndarray, sr: int) -> None:
    x = np.clip(x, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def make_click(path: Path, bpm: float = 120.0, duration: float = 30.0, sr: int = 22050) -> None:
    n = int(duration * sr)
    x = np.zeros(n, np.float32)
    period = 60.0 / bpm
    t = 0.0
    k = 0
    tt = np.arange(int(0.12 * sr)) / sr
    kick = np.sin(2 * np.pi * (120 * np.exp(-tt * 18) + 45) * tt) * np.exp(-tt * 22)
    while t < duration - 0.15:
        i = int(t * sr)
        g = 1.0 if k % 4 == 0 else 0.7
        x[i : i + len(kick)] += (g * kick)[: n - i].astype(np.float32)
        k += 1
        t = k * period
    write_wav(path, x * 0.8, sr)


def make_demo_video(path: Path, with_voice: bool = True, duration: float = 24.0, w: int = 320, h: int = 180, fps: int = 24) -> None:
    d = [SCENE_BOUNDS[i + 1] - SCENE_BOUNDS[i] for i in range(4)]
    src = [
        f"color=c=0x0b1020:s={w}x{h}:r={fps}:d={d[0]}",                          # calm, dark, static
        f"gradients=s={w}x{h}:r={fps}:d={d[1]}:speed=0.02:c0=0x203a6b:c1=0x2d6a4f",  # slow drifting colour
        f"testsrc2=s={w}x{h}:r={fps}:d={d[2]}",                                  # busy, high motion
        f"color=c=0xe8e4dc:s={w}x{h}:r={fps}:d={d[3]}",                          # calm, bright
    ]
    argv = ["ffmpeg", "-y", "-v", "error", "-nostdin"]
    for s in src:
        argv += ["-f", "lavfi", "-i", s]
    wav = path.with_suffix(".voice.wav")
    if with_voice:
        write_wav(wav, speech_like(duration, SPEECH_INTERVALS), SR)
        argv += ["-i", str(wav)]
    fc = "".join(f"[{i}:v]format=yuv420p[v{i}];" for i in range(4)) + "".join(f"[v{i}]" for i in range(4)) + "concat=n=4:v=1:a=0[v]"
    argv += ["-filter_complex", fc, "-map", "[v]"]
    if with_voice:
        argv += ["-map", "4:a", "-c:a", "aac", "-b:a", "96k"]
    argv += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "48", "-t", str(duration), str(path)]
    subprocess.run(argv, check=True)  # noqa: S603
    wav.unlink(missing_ok=True)


def make_long_video(path: Path, seconds: int, w: int = 320, h: int = 180, fps: int = 10) -> None:
    """Cheap long video for benchmarks: alternating scene sources, low-res."""
    seg = 20
    n = max(1, seconds // seg)
    srcs = ["testsrc2", "mandelbrot", "gradients", "life=ratio=0.1"]
    argv = ["ffmpeg", "-y", "-v", "error", "-nostdin"]
    fc = ""
    for i in range(n):
        s = srcs[i % len(srcs)]
        opt = "" if "=" in s else ""
        argv += ["-f", "lavfi", "-i", f"{s}{':' if '=' in s else '='}s={w}x{h}:r={fps}:d={seg}" if s != "life=ratio=0.1" else f"life=s={w}x{h}:r={fps}:ratio=0.1:rate={fps}:mold=10:random_seed=3:death_color=#101010:life_color=#c0e0ff,trim=duration={seg}"]
        fc += f"[{i}:v]scale={w}:{h},format=yuv420p[v{i}];"
    fc += "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    argv += ["-filter_complex", fc, "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", "-g", "50", str(path)]
    subprocess.run(argv, check=True)  # noqa: S603


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)
    make_demo_video(out / "demo.mp4")
    make_click(out / "click.wav")
    print(f"wrote {out/'demo.mp4'} and {out/'click.wav'}")
