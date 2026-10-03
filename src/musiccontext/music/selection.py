"""Explainable candidate scoring: nine independent dimensions instead of an opaque "best track".

Each dimension carries its own level/score/basis/reasons, so a human or agent can reject one axis
without discarding the rest, and the aggregate used for ordering is published alongside its weights.
Missing evidence scores "unknown", never "low": we never guess BPM, licensing or vocal presence.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import numpy as np

from ..direction.mood import expand
from ..errors import InvalidArgumentError
from ..schemas import (
    AnalysisReport,
    CandidateAssessment,
    MusicCandidate,
    MusicDirection,
    MusicFeatures,
    MusicSelection,
    ScoreDimension,
)
from ..timeline.beats import grid_fit, nearest
from .licensing import license_fit, verify_commercial

if TYPE_CHECKING:
    from ..providers.base import SearchQuery

# Ordering weights only. They never change a dimension's own level/score, and a dimension whose
# level is "unknown"/"not_applicable" is dropped from the weighted mean rather than counted as 0.
WEIGHTS: dict[str, float] = {
    "mood_match": 1.0,
    "tempo_match": 1.2,
    "energy_match": 1.0,
    "instrumentation_match": 0.6,
    "structure_match": 0.8,
    "voiceover_compatibility": 1.2,  # zeroed when the video has no speech
    "duration_fit": 0.9,
    "transition_fit": 0.5,
    "licensing_fit": 1.0,  # also a hard gate in rank_candidates when the direction requires it
}

SPEECH_FLOOR = 0.02  # below this share of the timeline, speech is noise rather than narration
TEMPO_MEDIUM_FACTOR = 1.12  # how far outside the BPM range still counts as a near miss
# 300-3400 Hz is the speech band; a music bed whose mid share exceeds this competes with narration.
MID_CLEAR, MID_BUSY = 0.30, 0.45
UNSCORED = ("unknown", "not_applicable")


def speech_ratio(analysis: AnalysisReport) -> float:
    v = analysis.stats.get("speech_ratio")
    return float(v) if isinstance(v, (int, float)) else 0.0


def weights_for(direction: MusicDirection, analysis: AnalysisReport) -> dict[str, float]:
    """The weights actually applied to this video (voiceover compatibility only counts with speech)."""
    w = dict(WEIGHTS)
    if speech_ratio(analysis) <= SPEECH_FLOOR:
        w["voiceover_compatibility"] = 0.0
    return w


def aggregate(dimensions: list[ScoreDimension], weights: dict[str, float]) -> tuple[float, list[str], list[str]]:
    """(weighted mean, names that were scored, names that were not) — unknowns are skipped, not zeroed."""
    scored = [d for d in dimensions if d.score is not None and d.level not in UNSCORED and weights.get(d.name, 0.0) > 0]
    names = {d.name for d in scored}
    skipped = [d.name for d in dimensions if d.name not in names]
    if not scored:
        return 0.0, [], skipped
    total = sum(weights[d.name] for d in scored)
    value = sum((d.score or 0.0) * weights[d.name] for d in scored) / total
    return round(value, 4), [d.name for d in scored], skipped


# ---------------------------------------------------------------------------- evidence helpers


def _norm(text: str) -> str:
    return text.strip().lower().replace("_", "-")


def _vocab(c: MusicCandidate) -> set[str]:
    """Everything the provider told us about the track, normalised for synonym matching."""
    out: set[str] = set()
    for item in (*c.tags, *c.metadata.values(), c.title, c.artist or ""):
        s = _norm(str(item))
        if not s:
            continue
        out.add(s)
        out.update(t for t in re.split(r"[^a-z0-9]+", s) if len(t) > 2)
    return out


def _overlap(terms: list[str], vocab: set[str]) -> tuple[list[str], list[str]]:
    hit, miss = [], []
    for t in terms:
        (hit if expand(_norm(t)) & vocab else miss).append(_norm(t))
    return hit, miss


def _resample(curve: list[tuple[float, float]], n: int = 16) -> np.ndarray | None:
    """Curve on a normalised 0-1 time grid, so track and arc curves of different lengths compare."""
    ded: dict[float, float] = {}
    for x, y in curve:  # arc curves repeat an x at section boundaries; the later value wins
        ded[float(x)] = float(y)
    xs = sorted(ded)
    if len(xs) < 2:
        return None
    return np.interp(np.linspace(xs[0], xs[-1], n), xs, [ded[x] for x in xs])


def _beats_basis(f: MusicFeatures) -> str:
    return {"detected": "detected", "user": "detected", "inferred": "inferred"}.get(f.beats_source, "estimated")


# ---------------------------------------------------------------------------- dimensions


def _tempo(f: MusicFeatures | None, direction: MusicDirection, query: SearchQuery | None) -> ScoreDimension:
    if f is None or f.bpm is None:
        return ScoreDimension(
            name="tempo_match", level="unknown", basis="unknown",
            reasons=["no tempo metadata; provider did not supply BPM and the file was not analyzed",
                     "run `musiccontext analyze-music` on the file (or pick a provider that returns BPM) to score tempo"],
        )
    lo, hi = (query.bpm_range if query and query.bpm_range else (direction.tempo.min_bpm, direction.tempo.max_bpm))
    target = (query.target_bpm if query and query.target_bpm else direction.tempo.target_bpm)
    half = max(hi - target, target - lo, 1.0)

    def fit(v: float) -> tuple[str, float]:
        if lo <= v <= hi:
            return "high", round(max(0.7, 1.0 - 0.3 * abs(v - target) / half), 3)
        d = (lo - v) if v < lo else (v - hi)
        if lo / TEMPO_MEDIUM_FACTOR <= v <= hi * TEMPO_MEDIUM_FACTOR:
            return "medium", round(max(0.4, 0.65 - 0.25 * d / half), 3)
        return "low", round(max(0.0, 0.3 - abs(v - target) / (4 * half)), 3)

    v = f.bpm.value
    options: list[tuple[float, str]] = [(v, "")]
    for alt, label in ((v * 2, "double time"), (v / 2, "half time")):
        options.append((alt, label))
    for alt in f.bpm_alternatives:
        if all(abs(alt - x) > 0.5 for x, _ in options):
            options.append((float(alt), "a provider-listed alternative tempo"))
    best = max(options, key=lambda o: (fit(o[0])[1], -abs(o[0] - target)))
    level, score = fit(best[0])
    if level == "low":
        reasons = [f"BPM {v:.1f} is outside the {lo:.0f}-{hi:.0f} target range (target {target:.0f}), "
                   f"and neither half nor double time lands inside it"]
    elif best[1]:
        where = "sits inside" if level == "high" else "only comes close to"
        reasons = [f"BPM {v:.1f} {where} the {lo:.0f}-{hi:.0f} target range when read at {best[1]} ({best[0]:.1f} BPM)"]
    elif level == "high":
        reasons = [f"BPM {v:.1f} sits inside the {lo:.0f}-{hi:.0f} target range (target {target:.0f})"]
    else:
        reasons = [f"BPM {v:.1f} is just outside the {lo:.0f}-{hi:.0f} target range (target {target:.0f})"]
    reasons.append(f"tempo confidence {f.bpm.confidence:.2f} ({f.bpm.source})")
    return ScoreDimension(name="tempo_match", level=level, score=score, basis=f.bpm.source, reasons=reasons)  # type: ignore[arg-type]


def _energy(f: MusicFeatures | None, direction: MusicDirection, query: SearchQuery | None) -> ScoreDimension:
    if f is None or not f.energy_curve:
        return ScoreDimension(name="energy_match", level="unknown", basis="unknown",
                              reasons=["no energy curve; the track was never analyzed, so its level is unknown"])
    want = float(query.energy) if query and query.energy is not None else direction.energy
    mean = float(np.mean([p[1] for p in f.energy_curve]))
    diff = abs(mean - want)
    level = "high" if diff <= 0.12 else "medium" if diff <= 0.25 else "low"
    reasons = [f"mean energy {mean:.2f} against the direction's target {want:.2f}"]
    shape_bonus = 0.0
    a, b = _resample(f.energy_curve), _resample(direction.arc.energy_curve)
    if a is None or b is None:
        reasons.append("not enough samples to compare the energy shape against the direction's arc")
    elif float(a.std()) < 1e-6:
        reasons.append("the track's energy is flat, so it cannot follow the direction's arc shape")
    elif float(b.std()) < 1e-6:
        reasons.append("the direction's arc is flat, so only the overall level was compared")
    else:
        corr = float(np.corrcoef(a, b)[0, 1])
        shape_bonus = 0.1 * max(0.0, corr)
        if corr >= 0.5:
            reasons.append(f"its shape follows the direction's arc (correlation {corr:+.2f})")
        elif corr <= -0.5:
            reasons.append(f"its shape runs opposite to the direction's arc (correlation {corr:+.2f}); "
                           f"the track falls where the arc rises")
        else:
            reasons.append(f"its shape is unrelated to the direction's arc (correlation {corr:+.2f})")
    return ScoreDimension(name="energy_match", level=level, score=round(min(1.0, max(0.0, 1.0 - diff) + shape_bonus), 3),
                          basis="estimated", reasons=reasons)


def _tagged(name: str, terms: list[str], c: MusicCandidate, what: str) -> tuple[ScoreDimension | None, list[str], list[str]]:
    """Shared tag-overlap preamble: returns an early 'unknown' dimension, or (None, hits, misses)."""
    if not (c.tags or c.metadata):
        return ScoreDimension(name=name, level="unknown", basis="unknown",  # type: ignore[arg-type]
                              reasons=[f"provider supplied no tags or metadata, so {what} cannot be judged",
                                       "title text alone is not evidence of a track's character"]), [], []
    if not terms:
        return ScoreDimension(name=name, level="not_applicable", basis="inferred",  # type: ignore[arg-type]
                              reasons=[f"the direction names no {what} to match"]), [], []
    hit, miss = _overlap(terms, _vocab(c))
    return None, hit, miss


def _mood(c: MusicCandidate, direction: MusicDirection, query: SearchQuery | None) -> ScoreDimension:
    terms = list(dict.fromkeys([*(query.moods if query else []), *direction.mood, *direction.style]))
    early, hit, miss = _tagged("mood_match", terms, c, "mood")
    if early:
        return early
    frac = len(hit) / len(terms)
    level = "high" if frac >= 0.5 else "medium" if hit else "low"
    reasons = [f"tags match {len(hit)}/{len(terms)} of the wanted mood/style terms: {', '.join(hit)}" if hit
               else f"no tag or metadata value matches any of {', '.join(terms)} (or their synonyms)"]
    if miss:
        reasons.append(f"unmatched: {', '.join(miss)}")
    return ScoreDimension(name="mood_match", level=level, score=round(0.2 + 0.8 * frac, 3), basis="provider", reasons=reasons)


def _instrumentation(c: MusicCandidate, direction: MusicDirection) -> ScoreDimension:
    inst = direction.instrumentation
    banned, _ = _overlap(inst.prohibited, _vocab(c))
    if banned:
        return ScoreDimension(name="instrumentation_match", level="low", score=0.0, basis="provider",
                              reasons=[f"tags name prohibited instrumentation: {', '.join(banned)}",
                                       "the direction rules these out, so this is a hard concern, not a preference"])
    terms = list(dict.fromkeys([*inst.preferred, *inst.optional]))
    early, hit, miss = _tagged("instrumentation_match", terms, c, "instrumentation")
    if early:
        return early
    want = len(inst.preferred) or len(terms)
    pref_hit = [h for h in hit if h in {_norm(p) for p in inst.preferred}]
    frac = min(1.0, (len(pref_hit) + 0.5 * (len(hit) - len(pref_hit))) / max(want, 1))
    level = "high" if frac >= 0.5 else "medium" if hit else "low"
    reasons = [f"tags name {len(hit)} wanted instrument(s): {', '.join(hit)}" if hit
               else f"no tag names any of the preferred instruments ({', '.join(inst.preferred) or 'none listed'})",
               "no prohibited instrumentation appears in the tags"]
    if miss:
        reasons.append(f"not mentioned: {', '.join(miss[:6])}")
    return ScoreDimension(name="instrumentation_match", level=level, score=round(0.2 + 0.8 * frac, 3),
                          basis="provider", reasons=reasons)


def _structure(f: MusicFeatures | None, direction: MusicDirection) -> ScoreDimension:
    peak = direction.arc.peak_time
    if peak is None:
        return ScoreDimension(name="structure_match", level="not_applicable", basis="inferred",
                              reasons=["the direction has no peak moment for the track's structure to hit"])
    if f is None or (not f.lifts and not f.downbeats):
        return ScoreDimension(name="structure_match", level="unknown", basis="unknown",
                              reasons=["no lifts or downbeats; the track's structure was never analyzed"])
    # ponytail: judged at the track's own timeline (music_start 0). sync.alignment can shift the track
    # to place a lift on the peak; this dimension reports the as-is fit, which is the pessimistic case.
    boundaries = [peak, *(s.start_time for s in direction.arc.sections if s.label == "peak")]
    if f.lifts:
        n = nearest([t for t, _ in f.lifts], peak)
        dist = abs(n[1]) if n else None
        strength = next((s for t, s in f.lifts if n and abs(t - n[0]) < 1e-6), 0.0)
        level = "high" if dist is not None and dist <= 1.0 else "medium" if dist is not None and dist <= 3.0 else "low"
        reasons = [f"closest energy lift to the {peak:.1f}s peak is at {n[0]:.2f}s (strength {strength:.2f}), "
                   f"{dist:.2f}s away" if n and dist is not None else "no usable lift times"]
        near = sum(1 for b in boundaries if (m := nearest([t for t, _ in f.lifts], b)) and abs(m[1]) <= 2.0)
        reasons.append(f"{near}/{len(boundaries)} build->peak boundaries have a lift within 2.0s")
        score = 1.0 if level == "high" else 0.6 if level == "medium" else 0.25
    else:
        n = nearest(f.downbeats, peak)
        dist = abs(n[1]) if n else 99.0
        level = "medium" if dist <= 1.0 else "low"
        reasons = [f"no energy lifts were detected; the nearest downbeat to the {peak:.1f}s peak is "
                   f"{dist:.2f}s away, so a hit can be placed but the track does not lift there"]
        score = 0.5 if level == "medium" else 0.25
    return ScoreDimension(name="structure_match", level=level, score=score, basis=_beats_basis(f), reasons=reasons)  # type: ignore[arg-type]


def _voiceover(f: MusicFeatures | None, analysis: AnalysisReport) -> ScoreDimension:
    ratio = speech_ratio(analysis)
    if ratio <= SPEECH_FLOOR:
        return ScoreDimension(name="voiceover_compatibility", level="not_applicable", basis="detected",
                              reasons=[f"the video has no narration to protect (speech covers {ratio:.1%} of it)"])
    if f is None:
        return ScoreDimension(name="voiceover_compatibility", level="unknown", basis="unknown",
                              reasons=[f"narration covers {ratio:.0%} of the video but the track was never analyzed"])
    if f.has_vocals is True:
        return ScoreDimension(name="voiceover_compatibility", level="low", score=0.1, basis="provider",
                              reasons=["the track has vocals, which collide with narration in the same register",
                                       f"narration covers {ratio:.0%} of the video"])
    mid = f.band_ratios.get("mid")
    if mid is None:
        return ScoreDimension(name="voiceover_compatibility", level="unknown", basis="unknown",
                              reasons=["no band ratios; the track's share of the 300-3400 Hz speech band is unknown"])
    level = "high" if mid <= MID_CLEAR else "medium" if mid <= MID_BUSY else "low"
    score = round(max(0.1, min(1.0, 1.0 - (mid - 0.2) * 1.6)), 3)
    reasons = [f"{mid:.0%} of its energy sits in the 300-3400 Hz speech band "
               f"({'leaves the band clear' if level == 'high' else 'competes with narration' if level == 'low' else 'partly shares the band'})"]
    if f.has_vocals is False:
        reasons.append("instrumental arrangement leaves the 1-4 kHz speech band clear")
    else:
        reasons.append("vocal presence is unknown: no tag or provider field states it, so verify before publishing")
    if f.dynamic_range_db is None:
        reasons.append("dynamic range was not measured, so ducking depth cannot be predicted")
    else:
        reasons.append(f"dynamic range {f.dynamic_range_db:.1f} dB"
                       + (" is wide, so a compressor or deeper ducking will be needed" if f.dynamic_range_db > 18 else
                          " is steady enough to sit under narration"))
        if f.dynamic_range_db > 18 and level == "high":
            level, score = "medium", min(score, 0.65)
    return ScoreDimension(name="voiceover_compatibility", level=level, score=score, basis="estimated", reasons=reasons)  # type: ignore[arg-type]


def _duration(f: MusicFeatures | None, analysis: AnalysisReport) -> ScoreDimension:
    video = analysis.metadata.duration
    if f is None:
        return ScoreDimension(name="duration_fit", level="unknown", basis="unknown",
                              reasons=["no duration metadata for the track"])
    if f.duration >= video:
        return ScoreDimension(name="duration_fit", level="high", score=1.0, basis="estimated",
                              reasons=[f"{f.duration:.1f}s covers the full {video:.1f}s video with "
                                       f"{f.duration - video:.1f}s to trim"])
    if f.downbeats:
        frac = f.duration / video
        return ScoreDimension(name="duration_fit", level="medium", score=round(0.45 + 0.3 * frac, 3), basis=_beats_basis(f),  # type: ignore[arg-type]
                              reasons=[f"{f.duration:.1f}s is shorter than the {video:.1f}s video",
                                       f"{len(f.downbeats)} downbeats were found, so it can be looped on a bar line"])
    return ScoreDimension(name="duration_fit", level="low", score=round(0.2 * f.duration / video, 3), basis="estimated",
                          reasons=[f"{f.duration:.1f}s is shorter than the {video:.1f}s video",
                                   "no downbeats were found, so there is no musical point to loop from"])


def _transition(f: MusicFeatures | None, analysis: AnalysisReport) -> ScoreDimension:
    cuts = [t.start_time for t in analysis.transitions]
    if not cuts:
        return ScoreDimension(name="transition_fit", level="not_applicable", basis="detected",
                              reasons=["no cuts were detected in the video, so there is nothing to land on a beat"])
    if f is None or f.bpm is None:
        return ScoreDimension(name="transition_fit", level="unknown", basis="unknown",
                              reasons=["the track's tempo is unknown, so cut-to-beat alignment cannot be computed"])
    frac, phase = grid_fit(cuts, f.bpm.value)
    level = "high" if frac >= 0.66 else "medium" if frac >= 0.34 else "low"
    return ScoreDimension(name="transition_fit", level=level, score=round(frac, 3), basis=f.bpm.source,  # type: ignore[arg-type]
                          reasons=[f"{round(frac * len(cuts))}/{len(cuts)} video cuts ({frac:.0%}) would fall on a beat "
                                   f"at {f.bpm.value:.1f} BPM with the grid offset by {phase:.2f}s"])


# ---------------------------------------------------------------------------- assessment


def assess_candidate(candidate: MusicCandidate, direction: MusicDirection, analysis: AnalysisReport,
                     query: SearchQuery | None = None) -> CandidateAssessment:
    """Score one candidate on nine independent dimensions. Never raises on missing data."""
    f = candidate.features
    req = direction.requirement
    dims = [
        _mood(candidate, direction, query),
        _tempo(f, direction, query),
        _energy(f, direction, query),
        _instrumentation(candidate, direction),
        _structure(f, direction),
        _voiceover(f, analysis),
        _duration(f, analysis),
        _transition(f, analysis),
        license_fit(candidate.license, req.commercial_use_required, req.license_must_be_verified),
    ]
    weights = weights_for(direction, analysis)
    value, scored, skipped = aggregate(dims, weights)
    used = ", ".join(f"{n} x{weights[n]:g}" for n in scored)
    line = (f"overall fit {value:.2f} (weighted mean across {len(scored)} scored dimensions: {used or 'none'}"
            + (f"; not scored: {', '.join(skipped)}" if skipped else "") + ")")
    strong = sorted((d for d in dims if d.level in ("high", "medium")), key=lambda d: (-(d.score or 0), -weights.get(d.name, 0.0), d.name))
    reasons = [line, *[d.reasons[0] for d in strong[:5] if d.reasons]]
    if len(reasons) < 3:
        reasons += [d.reasons[0] for d in dims if d.level in UNSCORED and d.reasons][: 3 - len(reasons)]
    concerns = [f"{d.name} is {d.level}: {d.reasons[0]}" for d in dims
                if d.level in ("low", "unknown") and weights.get(d.name, 0.0) > 0 and d.reasons]
    return CandidateAssessment(candidate=candidate, dimensions=dims, reasons=reasons, concerns=concerns)


def _exclusion(candidate: MusicCandidate, direction: MusicDirection) -> str | None:
    """The hard fails. None means the candidate stays in the running."""
    req, f = direction.requirement, candidate.features
    if req.commercial_use_required or req.license_must_be_verified:
        cleared, why = verify_commercial(candidate.license)
        if not cleared:
            return (f"commercial use is required but the license failed verification: {why}; "
                    f"add a .license.json sidecar or use a provider that returns license metadata")
    if direction.vocals == "none" and f is not None and f.has_vocals is True:
        return "the direction is instrumental-only and this track is tagged as having vocals"
    banned, _ = _overlap(list(dict.fromkeys([*req.prohibited_tags, *direction.instrumentation.prohibited])), _vocab(candidate))
    if banned:
        return f"tagged with prohibited material: {', '.join(banned)}"
    if f is not None and f.duration < req.min_duration and not f.downbeats:
        return (f"{f.duration:.1f}s is under the {req.min_duration:.1f}s minimum and has no downbeats to loop from")
    return None


def rank_candidates(candidates: list[MusicCandidate], direction: MusicDirection, analysis: AnalysisReport,
                    query: SearchQuery | None = None) -> tuple[list[CandidateAssessment], list[dict]]:
    """(ranked assessments, hard exclusions). Ordering is the published weighted aggregate, ties by id."""
    weights = weights_for(direction, analysis)
    ranked: list[CandidateAssessment] = []
    excluded: list[dict] = []
    for c in candidates:
        why = _exclusion(c, direction)
        if why:
            excluded.append({"id": c.id, "title": c.title, "provider": c.provider, "reason": why})
            continue
        ranked.append(assess_candidate(c, direction, analysis, query))
    ranked.sort(key=lambda a: (-aggregate(a.dimensions, weights)[0], a.candidate.id))
    excluded.sort(key=lambda e: e["id"])
    return ranked, excluded


def select(candidates: list[MusicCandidate], direction: MusicDirection, analysis: AnalysisReport,
           query: SearchQuery | None = None, selected_by: str = "recommendation") -> MusicSelection | None:
    """The top-ranked candidate, or None when every candidate hard-failed. Alignment is added by the caller."""
    if selected_by not in ("user", "recommendation", "generation"):
        raise InvalidArgumentError(f"Unknown selected_by '{selected_by}'.", "Use one of: user, recommendation, generation.")
    ranked, _ = rank_candidates(candidates, direction, analysis, query)
    if not ranked:
        return None
    return MusicSelection(assessment=ranked[0], selected_by=selected_by)  # type: ignore[arg-type]


def explain_assessment(assessment: CandidateAssessment) -> str:
    c = assessment.candidate
    head = f"{c.title or c.id} ({c.provider})"
    lines = [head]
    for d in assessment.dimensions:
        score = f" [{d.score:.2f}]" if d.score is not None else ""
        lines.append(f"  {d.name}: {d.level}{score} - {d.reasons[0] if d.reasons else 'no reason recorded'} (basis: {d.basis})")
    lines += [f"  ! {x}" for x in assessment.concerns]
    return "\n".join(lines)
