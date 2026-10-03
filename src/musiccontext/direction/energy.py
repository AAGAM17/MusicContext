from __future__ import annotations

import numpy as np


def music_curve_from_story(curve: list[tuple[float, float]], bias: float, user_energy: float | None) -> list[tuple[float, float]]:
    """Map the video's visual energy curve to a target music energy curve (preset bias, optional user override of the mean)."""
    if not curve:
        return []
    t = np.array([p[0] for p in curve])
    e = np.array([p[1] for p in curve])
    e = np.clip(e * 0.9 + 0.05 + bias, 0.02, 0.98)
    if user_energy is not None:
        e = np.clip(e + (user_energy - float(e.mean())), 0.02, 0.98)
    return [(float(a), round(float(b), 3)) for a, b in zip(t, e, strict=True)]


def rhythm_for(energy: float, under_speech: bool) -> str:
    if under_speech:
        return "sparse" if energy < 0.55 else "moderate"
    if energy < 0.25:
        return "sparse"
    if energy < 0.45:
        return "relaxed"
    if energy < 0.65:
        return "moderate"
    if energy < 0.82:
        return "driving"
    return "dense"
