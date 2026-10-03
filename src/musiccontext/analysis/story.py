"""Story model: scenes -> energy -> narrative segments with an extensible role taxonomy.

Roles are plain strings. `ROLES` documents the built-in vocabulary; callers (and future
analyzers, e.g. a vision model) may emit others and everything downstream treats unknown
roles generically.
"""

from __future__ import annotations

import numpy as np

from ..schemas import (
    AnalysisReport,
    AudioEvent,
    NarrativeEvent,
    Scene,
    SpeechSegment,
    StoryModel,
    StorySegment,
    Transition,
    VideoMetadata,
    VisualEvent,
)
from . import visual
from .audio import AudioSeries, runs
from .scenes import FrameSeries

ROLES = {
    "hook": "high-energy opening meant to grab attention",
    "establishing": "calm opening that sets context",
    "development": "steady middle material",
    "problem": "lower-energy setup/explanation before a payoff",
    "build": "rising energy toward a payoff",
    "reveal": "the payoff moment: energy peak after a build",
    "climax": "peak without a preceding build",
    "resolution_cta": "closing section / call to action",
    "outro": "closing section",
}
MIN_SCENE = 0.8
MAX_SEGMENTS = 8


def _scene_bounds(cut_times: list[float], duration: float) -> list[tuple[float, float]]:
    pts = [0.0] + [t for t in cut_times] + [duration]
    out: list[tuple[float, float]] = []
    for a, b in zip(pts[:-1], pts[1:], strict=True):
        if out and (b - a) < MIN_SCENE:
            out[-1] = (out[-1][0], b)  # absorb flicker into the previous scene
        else:
            out.append((a, b))
    if len(out) > 1 and out[-1][1] - out[-1][0] < MIN_SCENE:
        out[-2] = (out[-2][0], out[-1][1])
        out.pop()
    return out


def _sl(t: np.ndarray, a: float, b: float) -> slice:
    i = int(np.searchsorted(t, a, "left"))
    j = int(np.searchsorted(t, b, "left"))
    return slice(i, max(j, i + 1))


def build_scenes(duration: float, cut_times: list[float], fs: FrameSeries, audio: AudioSeries | None) -> list[Scene]:
    bounds = _scene_bounds(cut_times, duration)
    ct = np.array(cut_times) if cut_times else np.array([])
    has_audio = audio is not None and not audio.empty()
    raws, parts = [], []
    for a, b in bounds:
        s = _sl(fs.t, a, b)
        mo = float(fs.motion[s].mean()) if len(fs.t) else 0.0
        br = float(fs.brightness[s].mean()) if len(fs.t) else 0.5
        sa = float(fs.saturation[s].mean()) if len(fs.t) else 0.5
        wa = float(fs.warmth[s].mean()) if len(fs.t) else 0.0
        mid = (a + b) / 2
        rate = float(np.sum(np.abs(ct - mid) <= 4.0) / 8.0) if ct.size else 0.0
        au = None
        if has_audio:
            seg = audio.rms_db[int(a * audio.fps) : max(int(b * audio.fps), int(a * audio.fps) + 1)]  # type: ignore[union-attr]
            # mean power, not mean dB: see the note in music/features.py
            au = float(np.clip((10 * np.log10((10 ** (seg / 10)).mean() + 1e-12) + 50) / 40, 0, 1)) if len(seg) else 0.0
        w = {"m": 0.5, "c": 0.3, "a": 0.2 if au is not None else 0.0}
        raw = (w["m"] * mo + w["c"] * min(rate / 1.0, 1.0) + w["a"] * (au or 0.0)) / sum(w.values())
        raws.append(raw)
        parts.append((mo, br, sa, wa, rate))
    lo, hi = min(raws), max(raws)
    scale = max(hi - lo, 0.3)  # a narrow absolute range must not be inflated to look dramatic
    scenes = []
    for i, ((a, b), raw, (mo, br, sa, wa, rate)) in enumerate(zip(bounds, raws, parts, strict=True)):
        e = float(np.clip(0.08 + 0.84 * (raw - lo) / scale, 0, 1))
        scenes.append(Scene(
            start_time=round(a, 3), end_time=round(b, 3), confidence=0.7, source="estimated", index=i,
            energy=round(e, 3), motion=round(mo, 3), brightness=round(br, 3), saturation=round(sa, 3), warmth=round(wa, 3),
            visual_style=visual.describe(mo, br, sa, wa), pace=visual.pace_from_rate(rate, b - a),  # type: ignore[arg-type]
            provenance="cut-bounded scene; energy = 0.5*motion + 0.3*cut-rate + 0.2*audio level, range-normalized",
        ))
    return scenes


def energy_curve(scenes: list[Scene], duration: float, fs: FrameSeries) -> list[tuple[float, float]]:
    n = int(np.ceil(duration)) + 1
    t = np.arange(n, dtype=float)
    base = np.zeros(n)
    for sc in scenes:
        base[(t >= sc.start_time) & (t < sc.end_time)] = sc.energy
    base[-1] = base[-2] if n > 1 else 0
    if len(fs.t) > 2:
        local = np.interp(t, fs.t, np.convolve(fs.motion, np.ones(5) / 5, mode="same"))
        mx = max(float(local.max()), 0.25)
        base = 0.8 * base + 0.2 * np.clip(local / mx, 0, 1) * (float(np.max(base)) if np.max(base) > 0 else 1.0)
    k = np.ones(3) / 3
    sm = np.convolve(np.pad(base, 1, mode="edge"), k, mode="valid")
    return [(float(x), round(float(np.clip(y, 0, 1)), 3)) for x, y in zip(t, sm, strict=True)]


def _segments(scenes: list[Scene]) -> list[list[Scene]]:
    groups: list[list[Scene]] = [[s] for s in scenes]

    def e(g):
        d = sum(s.duration for s in g)
        return sum(s.energy * s.duration for s in g) / d if d else 0.0

    def dur(g):
        return sum(s.duration for s in g)

    changed = True
    while changed and len(groups) > 1:
        changed = False
        for i in range(len(groups) - 1):
            if abs(e(groups[i]) - e(groups[i + 1])) < 0.12 or dur(groups[i]) < 2.5 or dur(groups[i + 1]) < 2.5:
                groups[i : i + 2] = [groups[i] + groups[i + 1]]
                changed = True
                break
    while len(groups) > MAX_SEGMENTS:
        i = min(range(len(groups) - 1), key=lambda k: abs(e(groups[k]) - e(groups[k + 1])))
        groups[i : i + 2] = [groups[i] + groups[i + 1]]
    return groups


def assign_roles(groups: list[list[Scene]]) -> list[tuple[str, float]]:
    en = [sum(s.energy * s.duration for s in g) / max(sum(s.duration for s in g), 1e-6) for g in groups]
    n = len(groups)
    roles = ["development"] * n
    if n == 1:
        return [("development", en[0])]
    peak = int(np.argmax(en))
    med = float(np.median(en))
    roles[0] = "hook" if en[0] >= 0.7 * max(en) and en[0] > 0.5 else "establishing"
    if peak > 0:
        rise = en[peak] - (en[peak - 1] if peak > 0 else 0)
        pos = groups[peak][0].start_time / max(groups[-1][-1].end_time, 1e-6)
        roles[peak] = "reveal" if rise >= 0.15 and 0.2 <= pos <= 0.9 and en[peak] > 0.45 else "climax"
        for i in range(1, peak):
            roles[i] = "build" if en[i] >= en[i - 1] - 0.02 and en[i] > med * 0.9 else "problem"
    if n > 1 and peak != n - 1:
        roles[-1] = "resolution_cta"
    elif n > 2 and roles[-1] == "climax":
        roles[-1] = "outro"
    for i in range(peak + 1, n - 1):
        roles[i] = "development"
    return list(zip(roles, en, strict=True))


def build_story(scenes: list[Scene], duration: float, curve, median_cut: float | None) -> StoryModel:
    groups = _segments(scenes)
    roles = assign_roles(groups)
    segs = []
    for i, (g, (role, e)) in enumerate(zip(groups, roles, strict=True)):
        style = sorted({t for s in g for t in s.visual_style})
        segs.append(StorySegment(
            start_time=g[0].start_time, end_time=g[-1].end_time, confidence=0.5, source="inferred", index=i, role=role,
            energy=round(float(e), 3), scene_indices=[s.index for s in g], description=f"{role} ({', '.join(style[:4])})",
            provenance="adjacent scenes grouped by energy; role from position + energy rank (heuristic, no semantic understanding)",
        ))
    peak = next((s for s in segs if s.role in ("reveal", "climax")), None)
    pace_vote = [s.pace for s in scenes]
    pace = max(set(pace_vote), key=pace_vote.count) if pace_vote else "moderate"
    return StoryModel(
        duration=duration, segments=segs, energy_curve=curve, peak_time=peak.start_time if peak else None,
        pace=pace, median_cut_interval=median_cut,
        visual_style=sorted({t for s in scenes for t in s.visual_style}),
    )


def build_report(meta: VideoMetadata, cuts, transitions: list[Transition], fs: FrameSeries, audio: AudioSeries | None,
                 speech: list[SpeechSegment], speech_source: str, caps: dict, warnings: list[str], timings: dict,
                 existing_music: dict | None = None) -> AnalysisReport:
    d = meta.duration
    ct = [c.time for c in cuts]
    scenes = build_scenes(d, ct, fs, audio)
    curve = energy_curve(scenes, d, fs)
    med = float(np.median(np.diff([0.0] + ct + [d]))) if ct else None
    story = build_story(scenes, d, curve, round(med, 2) if med else None)

    vis: list[VisualEvent] = []
    for c in cuts:
        vis.append(VisualEvent(start_time=c.time, end_time=c.time, kind="cut" if c.score >= 0.45 else "soft_transition", magnitude=round(min(c.score, 1), 2),
                               confidence=0.85, source="detected", provenance="ffmpeg scene score"))
    if len(fs.t) > 4:
        for a, b in runs(fs.motion > 0.55, fs.sample_fps, 1.0, 0.5):
            vis.append(VisualEvent(start_time=round(a, 2), end_time=round(b, 2), kind="motion_spike", magnitude=round(float(fs.motion[_sl(fs.t, a, b)].mean()), 2),
                                   confidence=0.6, source="estimated", provenance="sustained frame-difference above 0.55"))
        for a, b in runs(fs.motion < 0.01, fs.sample_fps, 3.0, 0.5):
            vis.append(VisualEvent(start_time=round(a, 2), end_time=round(b, 2), kind="static_section", magnitude=0.0,
                                   confidence=0.6, source="estimated", provenance="frame-difference below 0.01 for >= 3 s"))
    vis.sort(key=lambda e: e.start_time)

    aev: list[AudioEvent] = []
    speech_s = sum(s.duration for s in speech)
    silence_s = 0.0
    if audio is not None and not audio.empty():
        for a, b in runs(audio.rms_db < -55, audio.fps, 1.0, 0.2):
            silence_s += b - a
            aev.append(AudioEvent(start_time=round(a, 2), end_time=round(b, 2), kind="silence", level_db=-55.0, confidence=0.9, source="detected", provenance="rms < -55 dBFS for >= 1 s"))
    for s in speech:
        aev.append(AudioEvent(start_time=s.start_time, end_time=s.end_time, kind="speech", confidence=s.confidence, source=s.source, provenance=s.provenance))
    if existing_music:
        aev.append(AudioEvent(start_time=0, end_time=d, kind="music_like", confidence=existing_music["confidence"], source="estimated",
                              label=f"periodic pulse near {existing_music['bpm']:.0f} BPM", provenance="tempo autocorrelation on the existing audio track"))
    aev.sort(key=lambda e: e.start_time)

    narr: list[NarrativeEvent] = []
    for seg in story.segments:
        if seg.role in ("reveal", "climax"):
            narr.append(NarrativeEvent(start_time=seg.start_time, end_time=seg.end_time, kind="reveal_candidate" if seg.role == "reveal" else "climax_candidate", importance=0.9,
                                       confidence=0.45, source="inferred", label=f"energy rises to {seg.energy:.2f}", provenance="peak of the story energy curve after a rise"))
        if seg.role == "resolution_cta":
            narr.append(NarrativeEvent(start_time=seg.start_time, end_time=seg.end_time, kind="cta_candidate", importance=0.6, confidence=0.4, source="inferred",
                                       label="closing section", provenance="last story segment after the peak"))
    stats = {
        "duration": d, "scene_count": len(scenes), "cut_count": len(cuts),
        "cuts_per_minute": round(len(cuts) / d * 60, 2) if d else 0, "median_scene_length_s": round(float(np.median([s.duration for s in scenes])), 2),
        "mean_motion": round(float(np.mean([s.motion for s in scenes])), 3), "mean_energy": round(float(np.mean([p[1] for p in curve])), 3),
        "peak_energy": round(max(p[1] for p in curve), 3), "has_audio": meta.has_audio,
        "speech_ratio": round(speech_s / d, 3) if d else 0, "speech_source": speech_source,
        "silence_ratio": round(silence_s / d, 3) if d else 0,
        "existing_music_estimate": f"~{existing_music['bpm']:.0f} BPM (confidence {existing_music['confidence']:.2f})" if existing_music else None,
    }
    return AnalysisReport(metadata=meta, scenes=scenes, transitions=transitions, visual_events=vis, audio_events=aev, speech=speech,
                          narrative_events=narr, story=story, stats=stats, capabilities=caps, warnings=warnings, timings_s=timings)
