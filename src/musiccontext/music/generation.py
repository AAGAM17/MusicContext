"""Turn a MusicDirection into a generation request, run it, and report what actually came back.

The split matters: `build_request` is the pure mapping (direction -> provider-neutral brief),
`generate_music` picks a provider and runs it, and `generation_report` compares the brief against
the *measured* features of the artifact. That last step is what makes a generated track auditable
rather than merely asserted.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..errors import InvalidArgumentError
from ..schemas import MusicDirection, MusicGenerationRequest, MusicGenerationResult

if TYPE_CHECKING:
    from ..providers.base import MusicGenerationProvider

PEAK_ACTIONS = ("peak", "hit")
PEAK_IMPORTANCE = 0.6


def _video_duration(analysis) -> float | None:
    for obj, attr in ((getattr(analysis, "metadata", None), "duration"), (getattr(analysis, "story", None), "duration")):
        v = getattr(obj, attr, None)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def build_request(
    direction: MusicDirection,
    analysis,
    *,
    duration: float | None = None,
    seed: int | None = None,
    markers=None,
) -> MusicGenerationRequest:
    """Map a direction (plus the markers that justify its peaks) onto a provider-neutral brief."""
    total = float(duration) if duration else _video_duration(analysis)
    if not total or total <= 0:
        raise InvalidArgumentError(
            "Cannot decide how long the generated track should be.",
            "Pass an explicit duration (seconds), or analyze a video first so its duration is known.",
        )

    negatives = list(dict.fromkeys(direction.instrumentation.prohibited))
    if direction.vocals == "none" and "vocals" not in negatives:
        negatives.append("vocals")

    peaks = [
        round(float(m.timestamp), 3)
        for m in (markers or [])
        if getattr(m, "suggested_music_action", "none") in PEAK_ACTIONS
        and getattr(m, "importance", 0.0) >= PEAK_IMPORTANCE
        and 0.0 <= float(m.timestamp) < total
    ]
    if not peaks and direction.arc.peak_time is not None and 0.0 <= direction.arc.peak_time < total:
        peaks = [round(float(direction.arc.peak_time), 3)]

    return MusicGenerationRequest(
        prompt=direction.generation_prompt[:2000],
        negative_prompt=", ".join(negatives)[:1000],
        duration=round(total, 3),
        bpm=direction.tempo.target_bpm,
        style=list(direction.style),
        mood=list(direction.mood),
        instrumentation=list(direction.instrumentation.preferred),
        structure=[s.label for s in direction.arc.sections],
        energy_curve=[(float(t), float(e)) for t, e in direction.arc.energy_curve],
        peak_times=sorted(dict.fromkeys(peaks)),
        key=getattr(direction, "key", None),
        vocals=direction.vocals,
        reference_constraints=[c.description for c in direction.constraints if c.severity == "must"][:12],
        seed=seed,
    )


def generate_music(
    settings,
    direction: MusicDirection,
    analysis,
    *,
    provider: str | None = None,
    dest: Path | str | None = None,
    duration: float | None = None,
    seed: int | None = None,
    markers=None,
    force: bool = False,
) -> MusicGenerationResult:
    """Generate a track for `direction`.

    Raises NoProviderConfiguredError (with its own hint about the local library and the offline
    procedural generator) when nothing can generate, and InvalidPathError when `dest` exists and
    `force` is not set.
    """
    from ..providers import registry

    chosen = registry.pick("generation", settings, provider)
    request = build_request(direction, analysis, duration=duration, seed=seed, markers=markers)
    target: Path | None = None
    if dest is not None:
        from ..providers.search import sandbox_output

        target = sandbox_output(dest, settings, force=force)
    return cast("MusicGenerationProvider", chosen).generate(request, settings, target)


def generation_report(result: MusicGenerationResult, request: MusicGenerationRequest) -> dict:
    """A bounded brief-vs-reality dict: what was asked for next to what the artifact measures."""
    feats = result.candidate.features
    measured_bpm = feats.bpm.value if feats and feats.bpm else None
    downbeats = list(feats.downbeats) if feats else []

    peaks = []
    for p in request.peak_times:
        nearest = min(downbeats, key=lambda d: abs(d - p)) if downbeats else None
        peaks.append({
            "requested": round(float(p), 3),
            "nearest_measured_downbeat": round(float(nearest), 3) if nearest is not None else None,
            "offset_ms": round((nearest - p) * 1000, 1) if nearest is not None else None,
        })

    lic = result.license or result.candidate.license
    return {
        "provider": result.provider,
        "track_id": result.track_id,
        "duration": {"requested": round(request.duration, 3), "rendered": round(result.duration, 3),
                     "measured": round(feats.duration, 3) if feats else None},
        "bpm": {
            "requested": request.bpm,
            "measured": measured_bpm,
            "measured_source": feats.bpm.source if feats and feats.bpm else None,
            "measured_confidence": feats.bpm.confidence if feats and feats.bpm else None,
            "delta": round(measured_bpm - request.bpm, 2) if (measured_bpm and request.bpm) else None,
        },
        "key": {"requested": request.key, "measured": feats.key if feats else None,
                "measured_confidence": feats.key_confidence if feats else None},
        "peaks": peaks[:24],
        "structure_requested": list(request.structure)[:24],
        "beats_measured": len(feats.beats) if feats else 0,
        "downbeats_measured": len(downbeats),
        "license": {
            "provider": lic.provider if lic else None,
            "license": lic.license if lic else None,
            "commercial_use": lic.commercial_use if lic else "unknown",
            "verified": bool(lic and lic.verified),
            "verified_by": lic.verified_by if lic else None,
            "attribution_required": lic.attribution_required if lic else None,
        },
        "artifact_path": result.artifact_path,
        "provenance": result.provenance,
        "status": result.status,
    }
