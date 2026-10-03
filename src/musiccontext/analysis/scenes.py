"""Scene/cut detection with FFmpeg's scene score, plus a streaming low-res frame pass for motion & colour."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..schemas import Transition
from .media import run, which

_PTS = re.compile(r"pts_time:([0-9.]+)")
_SCORE = re.compile(r"lavfi\.scene_score=([0-9.]+)")


@dataclass
class Cut:
    time: float
    score: float


def detect_cuts(path: Path, duration: float, settings=None, threshold: float = 0.28) -> list[Cut]:
    exe = which("ffmpeg", settings)
    vf = f"scale=320:-2:flags=fast_bilinear,select='gt(scene,{threshold})',metadata=print:key=lavfi.scene_score:file=-"
    cp = run([exe, "-v", "error", "-nostdin", "-i", str(path), "-an", "-vf", vf, "-f", "null", "-"], timeout=max(600, duration * 4))
    cuts: list[Cut] = []
    t = None
    for line in cp.stdout.splitlines():
        if (m := _PTS.search(line)):
            t = float(m.group(1))
        elif (m := _SCORE.search(line)) and t is not None:
            if 0.2 < t < duration - 0.2 and (not cuts or t - cuts[-1].time > 0.25):
                cuts.append(Cut(round(t, 3), min(1.0, float(m.group(1)))))
            t = None
    return cuts


def cuts_to_transitions(cuts: list[Cut]) -> list[Transition]:
    out = []
    for c in cuts:
        hard = c.score >= 0.45
        out.append(Transition(
            start_time=c.time, end_time=c.time, confidence=round(min(0.95, 0.55 + c.score * 0.4), 2),
            source="detected", kind="hard_cut" if hard else "soft_transition", strength=round(c.score, 3),
            provenance=f"ffmpeg scene score {c.score:.2f} ({'>=' if hard else '<'} 0.45 hard-cut cutoff)",
        ))
    return out


@dataclass
class FrameSeries:
    t: np.ndarray
    motion: np.ndarray       # 0..1, cut-spikes removed
    brightness: np.ndarray   # 0..1
    saturation: np.ndarray   # 0..1
    warmth: np.ndarray       # -1..1
    sample_fps: float


def sample_frames(path: Path, duration: float, cut_times: list[float], settings=None) -> FrameSeries:
    """One streaming pass at 64x36 RGB; memory stays bounded regardless of video length."""
    fps = 4.0 if duration <= 120 else 2.0 if duration <= 600 else 1.0
    exe = which("ffmpeg", settings)
    w, h = 64, 36
    argv = [exe, "-v", "error", "-nostdin", "-i", str(path), "-an", "-vf", f"fps={fps},scale={w}:{h}:flags=area,format=rgb24", "-f", "rawvideo", "-"]
    fsize = w * h * 3
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)  # noqa: S603
    assert proc.stdout is not None
    ts, mo, br, sa, wa = [], [], [], [], []
    prev = None
    i = 0
    cut_set = np.array(cut_times) if cut_times else np.array([])
    try:
        while buf := proc.stdout.read(fsize):
            if len(buf) < fsize:
                break
            f = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3).astype(np.float32) / 255.0
            t = i / fps
            mx, mn = f.max(2), f.min(2)
            br.append(float(f.mean()))
            sa.append(float(np.where(mx > 0.02, (mx - mn) / np.maximum(mx, 1e-3), 0).mean()))
            wa.append(float((f[..., 0] - f[..., 2]).mean()))
            if prev is None:
                m = 0.0
            else:
                m = float(np.abs(f - prev).mean())
                if cut_set.size and np.min(np.abs(cut_set - t)) <= 1.5 / fps:
                    m = mo[-1] if mo else 0.0  # a cut is not motion; carry the previous value
            mo.append(m)
            ts.append(t)
            prev = f
            i += 1
    finally:
        proc.stdout.close()
        proc.kill() if proc.poll() is None else None
        proc.wait()
    n = len(ts)
    motion = np.clip(np.array(mo) / 0.10, 0, 1) if n else np.zeros(0)  # ponytail: fixed 0.10 mean-abs-diff = "max motion"; calibrate per content type if needed
    return FrameSeries(np.array(ts), motion, np.array(br), np.array(sa), np.clip(np.array(wa) * 2, -1, 1), fps)
