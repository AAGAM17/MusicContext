"""Plain-language visual style descriptors from simple frame statistics (no vision model required)."""

from __future__ import annotations


def describe(motion: float, brightness: float, saturation: float, warmth: float) -> list[str]:
    tags = []
    tags.append("dark" if brightness < 0.3 else "bright" if brightness > 0.65 else "mid-tone")
    tags.append("muted" if saturation < 0.25 else "vivid" if saturation > 0.65 else "balanced-colour")
    if abs(warmth) > 0.08:
        tags.append("warm" if warmth > 0 else "cool")
    tags.append("static" if motion < 0.02 else "dynamic" if motion > 0.45 else "gentle-motion")
    return tags


def pace_from_rate(cuts_per_s: float, scene_len: float) -> str:
    if cuts_per_s > 0.4 or scene_len < 1.5:
        return "fast"
    if cuts_per_s < 0.1 and scene_len > 5:
        return "slow"
    return "moderate"
