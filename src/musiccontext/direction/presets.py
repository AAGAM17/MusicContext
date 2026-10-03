"""Platform/content presets. They bias the direction but every field stays overrideable."""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "youtube": {"energy_bias": 0.0, "vocals": None, "target_lufs": -14, "notes": "Long-form: avoid fatigue, keep a bed under narration."},
    "shorts": {"energy_bias": 0.12, "bpm": (100, 140), "style": ["energetic"], "target_lufs": -14, "notes": "Vertical short-form: hook in the first second, loop-friendly ending."},
    "reels": {"energy_bias": 0.1, "bpm": (95, 135), "style": ["energetic", "electronic"], "target_lufs": -14, "notes": "Instagram Reels: punchy, trend-adjacent, loop-friendly."},
    "tiktok": {"energy_bias": 0.12, "bpm": (100, 140), "style": ["energetic", "electronic"], "target_lufs": -14, "notes": "TikTok: immediate hook, strong rhythm."},
    "linkedin": {"energy_bias": -0.08, "style": ["corporate", "minimal"], "mood": ["confident"], "vocals": "none", "avoid": ["heavy_edm"], "target_lufs": -16, "notes": "Often watched muted or at low volume; stay professional and unobtrusive."},
    "product-demo": {"energy_bias": -0.03, "style": ["technology", "minimal"], "mood": ["confident", "premium"], "vocals": "none", "bpm": (90, 125), "target_lufs": -16, "notes": "Leave room for narration/UI sounds; build to the feature reveal."},
    "advertisement": {"energy_bias": 0.05, "style": ["energetic", "cinematic"], "mood": ["confident", "exciting"], "target_lufs": -14, "notes": "Clear arc with a decisive peak and a clean ending."},
    "documentary": {"energy_bias": -0.12, "style": ["documentary", "ambient"], "mood": ["curious"], "vocals": "none", "bpm": (60, 105), "target_lufs": -18, "notes": "Supportive, textural, rarely beat-driven."},
    "presentation": {"energy_bias": -0.15, "style": ["minimal", "corporate"], "mood": ["calm", "confident"], "vocals": "none", "bpm": (70, 105), "target_lufs": -18, "notes": "Stays out of the way of the speaker."},
    "game-trailer": {"energy_bias": 0.15, "style": ["cinematic", "orchestral"], "mood": ["tense", "exciting"], "bpm": (80, 150), "target_lufs": -14, "notes": "Big dynamic range, hits on cuts."},
    "cinematic": {"energy_bias": 0.0, "style": ["cinematic"], "mood": ["premium"], "target_lufs": -16, "notes": "Wide dynamics, hybrid/orchestral palette."},
    "tutorial": {"energy_bias": -0.15, "style": ["minimal", "ambient"], "mood": ["calm"], "vocals": "none", "bpm": (70, 105), "target_lufs": -20, "notes": "Quiet bed; never competes with instruction."},
}


def get(name: str | None) -> dict:
    return PRESETS.get((name or "").lower(), {})
