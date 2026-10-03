"""Cut-rhythm summary used to pick a tempo whose beats can land on the edit."""

from __future__ import annotations

import numpy as np


def cut_rhythm(cut_times: list[float]) -> dict:
    if len(cut_times) < 3:
        return {"regular": False, "interval": None, "count": len(cut_times)}
    iv = np.diff(sorted(cut_times))
    med = float(np.median(iv))
    spread = float(np.percentile(iv, 75) - np.percentile(iv, 25))
    return {"regular": bool(med > 0.3 and spread <= 0.25 * med), "interval": round(med, 3), "count": len(cut_times)}


def bpm_for_interval(interval: float, lo: float, hi: float, target: float) -> float | None:
    """BPM (within [lo, hi], closest to target) such that `interval` is a whole number of beats."""
    cands = [60.0 * k / interval for k in range(1, 64) if lo <= 60.0 * k / interval <= hi]
    return round(min(cands, key=lambda b: abs(b - target)), 1) if cands else None
