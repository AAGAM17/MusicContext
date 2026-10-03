"""Marker taxonomy: default importance and suggested music action per marker type, plus user-input parsing."""

from __future__ import annotations

import re

from ..errors import InvalidArgumentError
from ..schemas import MARKER_TYPES, MusicMarker

DEFAULTS: dict[str, tuple[float, str]] = {
    "reveal": (0.95, "peak"), "product_appearance": (0.85, "lift"), "feature_reveal": (0.7, "hit"), "title": (0.7, "hit"),
    "logo": (0.75, "hit"), "action_peak": (0.65, "hit"), "cta": (0.6, "resolve"), "ending": (0.6, "resolve"),
    "scene_change": (0.55, "transition_fill"), "hard_cut": (0.3, "none"), "transition": (0.35, "transition_fill"),
    "voiceover_start": (0.5, "duck"), "voiceover_end": (0.4, "release"),
}
ALIASES = {
    "product_reveal": "reveal", "product": "product_appearance", "feature": "feature_reveal", "call_to_action": "cta",
    "end": "ending", "peak": "action_peak", "drop": "reveal", "intro": "title", "cut": "hard_cut",
}
_TIME = re.compile(r"^\s*(?:(\d+):)?(\d+(?:\.\d+)?)\s*$")


def parse_time(s: str) -> float:
    m = _TIME.match(str(s))
    if not m:
        raise InvalidArgumentError(f"Invalid timestamp '{s}'.", "Use seconds (18.2) or [M:]SS.s (0:18.2).")
    return float(m.group(1) or 0) * 60 + float(m.group(2))


def parse_user_marker(spec: str) -> tuple[float, str]:
    """'18.2=reveal' | '0:18.2:reveal' | '18.2 reveal'"""
    m = re.match(r"^\s*([0-9:.]+)\s*[=\s,]\s*([A-Za-z_]+)\s*$", spec) or re.match(r"^\s*([0-9]+:[0-9.]+|[0-9.]+)\s*:\s*([A-Za-z_]+)\s*$", spec)
    if not m:
        raise InvalidArgumentError(f"Invalid marker '{spec}'.", f"Use TIME=TYPE, e.g. 18.2=reveal. Types: {', '.join(MARKER_TYPES)}.")
    kind = ALIASES.get(m.group(2).lower(), m.group(2).lower())
    if kind not in MARKER_TYPES:
        raise InvalidArgumentError(f"Unknown marker type '{m.group(2)}'.", f"Known types: {', '.join(MARKER_TYPES)}.")
    return parse_time(m.group(1)), kind


def make(t: float, kind: str, reason: str, *, source="inferred", confidence=0.5, provenance="", importance: float | None = None, action: str | None = None) -> MusicMarker:
    imp, act = DEFAULTS[kind]
    return MusicMarker(
        timestamp=round(t, 3), type=kind, importance=round(importance if importance is not None else imp, 2),  # type: ignore[arg-type]
        suggested_music_action=action or act, reason=reason[:300], confidence=round(confidence, 2), source=source, provenance=provenance,  # type: ignore[arg-type]
    )
