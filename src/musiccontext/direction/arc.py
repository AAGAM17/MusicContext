"""Musical arc: sections with energy, rhythm and a concrete action, anchored to the peak moment."""

from __future__ import annotations

import numpy as np

from ..schemas import MusicArc, MusicMarker, MusicSection, StoryModel
from ..timeline.events import voiceover_blocks
from .energy import rhythm_for

LABELS = {
    "hook": "hook", "establishing": "intro", "problem": "undercurrent", "build": "build", "reveal": "peak", "climax": "peak",
    "development": "sustain", "resolution_cta": "resolution", "outro": "outro",
}
DESC = {
    "intro": "atmospheric, sparse opening; establish tone without drawing attention",
    "hook": "immediate rhythmic hook to grab attention",
    "undercurrent": "subtle pulse underneath; keep space for the message",
    "build": "rising layers and rhythmic density leading to the payoff",
    "peak": "full arrangement; the main hit lands exactly on the payoff",
    "sustain": "confident sustained section; hold energy without escalating",
    "resolution": "ease off into a clear resolution / call to action",
    "outro": "release and close cleanly",
}


def build_arc(story: StoryModel, music_curve: list[tuple[float, float]], markers: list[MusicMarker], speech, preset_platform: str | None) -> MusicArc:
    t = np.array([p[0] for p in music_curve])
    e = np.array([p[1] for p in music_curve])

    def mean_e(a, b):
        m = (t >= a) & (t < max(b, a + 1e-3))
        return float(e[m].mean()) if m.any() else float(np.interp((a + b) / 2, t, e))

    # peak anchor: user reveal marker wins, else inferred reveal/climax
    user_reveal = next((m for m in markers if m.source == "user" and m.type in ("reveal", "product_appearance", "feature_reveal", "action_peak")), None)
    peak_t = user_reveal.start_time if user_reveal else story.peak_time
    spans: list[tuple[float, float, str]] = [(s.start_time, s.end_time, s.role) for s in story.segments]
    if user_reveal and peak_t is not None:
        new: list[tuple[float, float, str]] = []
        for a, b, role in spans:
            if a < peak_t < b - 0.5 and peak_t - a > 0.5:
                new.append((a, peak_t, "build"))
                new.append((peak_t, b, "reveal"))
            elif abs(a - peak_t) <= 0.5:
                new.append((a, b, "reveal"))
            elif role in ("reveal", "climax"):
                new.append((a, b, "development"))  # only the user's marker may be the peak
            else:
                new.append((a, b, role))
        spans = new
        peak_t = peak_t
        # segment immediately before peak should read as a build
        for i, (a, b, role) in enumerate(spans):
            if i + 1 < len(spans) and spans[i + 1][2] == "reveal" and role in ("development", "problem", "establishing"):
                spans[i] = (a, b, "build" if i > 0 else role)
    sections: list[MusicSection] = []
    speech_blocks = voiceover_blocks(speech)
    for a, b, role in spans:
        label = LABELS.get(role, role)
        en = mean_e(a, b)
        if label == "peak":
            en = max(en, 0.8 if not user_reveal else en)
        elif label == "build":
            en = min(max(en, mean_e(a, b)), 0.8)
        under = sum(max(0.0, min(b, y) - max(a, x)) for x, y in speech_blocks) / max(b - a, 1e-6) > 0.35
        action = {"peak": "peak", "build": "build", "resolution": "resolve", "outro": "resolve", "intro": "hold", "hook": "hit"}.get(label, "sustain")
        desc = DESC.get(label, f"{role} section")
        if under:
            desc += "; thin out and stay out of the speech range during narration"
        sections.append(MusicSection(
            start_time=round(a, 3), end_time=round(b, 3), confidence=0.9 if (user_reveal and label == "peak") else 0.5, source="user" if (user_reveal and label == "peak") else "inferred",
            label=label, energy=round(en, 3), description=desc, action=action, rhythm=rhythm_for(en, under),  # type: ignore[arg-type]
            provenance="story segment " + role + (" anchored to user marker" if user_reveal and label == "peak" else ""),
        ))
    # ensure a build exists before the peak: if the preceding section is not rising, relabel it
    for i in range(1, len(sections)):
        if sections[i].label == "peak" and sections[i - 1].label in ("sustain", "undercurrent", "intro") and sections[i - 1].duration >= 3 and sections[i - 1].energy < sections[i].energy - 0.15:
            sections[i - 1] = sections[i - 1].model_copy(update={"label": "build", "action": "build", "description": DESC["build"]})
    # breakpoint energy curve: builds ramp up, peaks land at full level then settle, resolutions decay
    pts: list[tuple[float, float]] = []
    prev_e = sections[0].energy if sections else 0.3
    for s in sections:
        if s.label == "build":
            pts += [(s.start_time, round(prev_e, 3)), (s.end_time, round(min(0.98, s.energy + 0.1), 3))]
        elif s.label == "peak":
            pts += [(s.start_time, round(min(0.98, s.energy + 0.05), 3)), (s.end_time, round(s.energy * 0.9, 3))]
        elif s.label in ("resolution", "outro"):
            pts += [(s.start_time, round(s.energy, 3)), (s.end_time, round(max(0.1, s.energy * 0.6), 3))]
        else:
            pts += [(s.start_time, round(s.energy, 3)), (s.end_time, round(s.energy, 3))]
        prev_e = pts[-1][1]
    last = sections[-1] if sections else None
    ending = "resolve" if last and last.label in ("resolution", "outro") else "fade_out"
    if preset_platform in ("shorts", "reels", "tiktok"):
        ending = "fade_out"
    return MusicArc(sections=sections, energy_curve=pts, peak_time=round(peak_t, 3) if peak_t is not None else None, ending=ending,  # type: ignore[arg-type]
                    rationale="Sections follow the story segments; the peak is anchored to " + ("the user-supplied marker." if user_reveal else "the inferred reveal/climax." if peak_t is not None else "nothing (no clear peak found)."))
