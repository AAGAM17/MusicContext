"""Frame-level audio features for a video's existing soundtrack (50 Hz, streaming, bounded memory)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .media import decode_mono

SR = 16000
FRAME = 512
HOP = 320  # 20 ms -> 50 frames/s
FPS = SR / HOP


@dataclass
class AudioSeries:
    rms_db: np.ndarray
    voice_ratio: np.ndarray  # share of spectral energy in 300-3400 Hz
    flatness: np.ndarray
    fps: float = FPS

    @property
    def duration(self) -> float:
        return len(self.rms_db) / self.fps

    def empty(self) -> bool:
        return len(self.rms_db) == 0


def analyze_series(path: Path, settings=None, max_seconds: float | None = None) -> AudioSeries:
    win = np.hanning(FRAME).astype(np.float32)
    freqs = np.fft.rfftfreq(FRAME, 1 / SR)
    voice = (freqs >= 300) & (freqs <= 3400)
    band = (freqs >= 100) & (freqs <= 6000)
    rms_l, vr_l, fl_l = [], [], []
    left = np.zeros(0, np.float32)
    for chunk in decode_mono(path, SR, settings, max_seconds=max_seconds):
        buf = np.concatenate([left, chunk])
        if len(buf) < FRAME:
            left = buf
            continue
        n = (len(buf) - FRAME) // HOP + 1
        frames = np.lib.stride_tricks.sliding_window_view(buf, FRAME)[::HOP][:n]
        rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
        mag = np.abs(np.fft.rfft(frames * win, axis=1)) ** 2 + 1e-12
        tot = mag.sum(1)
        rms_l.append(20 * np.log10(rms + 1e-9))
        vr_l.append(mag[:, voice].sum(1) / tot)
        b = mag[:, band]
        fl_l.append(np.exp(np.mean(np.log(b), axis=1)) / np.mean(b, axis=1))
        left = buf[n * HOP :]
    if not rms_l:
        z = np.zeros(0)
        return AudioSeries(z, z, z)
    return AudioSeries(np.concatenate(rms_l), np.concatenate(vr_l), np.concatenate(fl_l))


def smooth(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or len(x) == 0:
        return x
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = np.empty(len(x))
    h = n // 2
    for_idx = np.arange(len(x))
    lo = np.clip(for_idx - h, 0, len(x))
    hi = np.clip(for_idx + h + 1, 0, len(x))
    out[:] = (c[hi] - c[lo]) / np.maximum(hi - lo, 1)
    return out


def runs(mask: np.ndarray, fps: float, min_len: float, merge_gap: float) -> list[tuple[float, float]]:
    """Boolean mask -> merged (start, end) seconds, dropping runs shorter than min_len."""
    if len(mask) == 0:
        return []
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    s, e = np.where(d == 1)[0], np.where(d == -1)[0]
    segs: list[list[float]] = []
    for a, b in zip(s / fps, e / fps, strict=True):
        if segs and a - segs[-1][1] <= merge_gap:
            segs[-1][1] = b
        else:
            segs.append([a, b])
    return [(a, b) for a, b in segs if b - a >= min_len]
