"""Beat-grid helpers."""

from __future__ import annotations

import numpy as np


def grid(bpm: float, offset: float, duration: float) -> np.ndarray:
    p = 60.0 / bpm
    return np.arange(offset, duration + p, p)


def nearest(times, t: float) -> tuple[float, float] | None:
    """(closest value, signed distance value-t) or None when `times` is empty."""
    a = np.asarray(times, dtype=float)
    if a.size == 0:
        return None
    i = int(np.argmin(np.abs(a - t)))
    return float(a[i]), float(a[i] - t)


def grid_fit(event_times: list[float], bpm: float, tol: float = 0.07) -> tuple[float, float]:
    """Best beat-grid phase for a set of event times: returns (fraction within tol, phase offset s)."""
    if not event_times or bpm <= 0:
        return 0.0, 0.0
    p = 60.0 / bpm
    ev = np.array(event_times)
    best = (-1.0, 0.0)
    for ph in np.arange(0, p, 0.01):
        d = np.abs(((ev - ph + p / 2) % p) - p / 2)
        f = float(np.mean(d <= tol))
        if f > best[0]:
            best = (f, float(ph))
    return best
