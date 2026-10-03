"""The operation layer. CLI, MCP server and SDK all call these, so the logic exists once.

Every op takes an OpContext (settings + path policy) and returns typed objects plus a bounded,
JSON-safe dict. Nothing here prints, and nothing processes media that was not explicitly asked for.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ..engine.artifacts import (
    default_artifact_path,
    find_artifact,
    load_artifact,
    new_result,
    save_artifact,
    summarize,
)
from ..engine.context import Settings
from ..errors import InvalidArgumentError, MusicContextError
from ..schemas import (
    AnalysisReport,
    MusicContextResult,
    MusicDirection,
    MusicSelection,
    Preferences,
    VideoProject,
)
from ..security import network
from ..security.content import bound_json, injection_warnings
from ..security.paths import PathPolicy, policy_from_settings
from ..timeline.markers import parse_user_marker

VOCALS = ("none", "allowed", "preferred", "spoken_word")


@dataclass
class OpContext:
    settings: Settings
    policy: PathPolicy
    restricted: bool = False
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, settings, restricted: bool = False) -> OpContext:
        return cls(settings=settings, policy=policy_from_settings(settings, restricted), restricted=restricted)

    def bound(self, obj):
        return bound_json(obj, max_chars=self.settings.max_output_chars)


def prefs_from_dict(d: dict | None) -> Preferences:
    """Build Preferences from CLI flags / MCP arguments, validating the awkward fields."""
    d = dict(d or {})
    markers = []
    for spec in d.pop("marker", None) or d.pop("markers", None) or []:
        markers.append(parse_user_marker(spec) if isinstance(spec, str) else (float(spec[0]), str(spec[1])))
    vocals = d.pop("vocals", None)
    if vocals is not None and vocals not in VOCALS:
        raise InvalidArgumentError(f"Invalid --vocals '{vocals}'.", f"Choose one of: {', '.join(VOCALS)}.")
    platform = d.pop("platform", None)
    if platform:
        from ..direction.presets import PRESETS

        if platform.lower() not in PRESETS:
            raise InvalidArgumentError(f"Unknown platform preset '{platform}'.", f"Available presets: {', '.join(sorted(PRESETS))}.")
        platform = platform.lower()
    known = set(Preferences.model_fields)
    extra = {k: v for k, v in d.items() if k not in known}
    if extra:
        raise InvalidArgumentError(f"Unknown preference(s): {', '.join(sorted(extra))}.", f"Valid preferences: {', '.join(sorted(known))}.")
    return Preferences(**{k: v for k, v in d.items() if v not in (None, [], ())}, user_markers=markers, vocals=vocals, platform=platform)


def load_profile(ctx: OpContext, prefs: Preferences):
    if not prefs.profile:
        return None
    from ..direction import profile as profile_mod

    return profile_mod.load(ctx.settings, prefs.profile)


@contextlib.contextmanager
def resolve_media(ctx: OpContext, target: str, what: str = "video") -> Iterator[Path]:
    """Yield a local path for a file or (when explicitly enabled) an https URL."""
    if network.is_url(target):
        if ctx.settings.local_only:
            raise MusicContextError("Remote URLs are disabled by --local-only.", "Download the file yourself and pass a local path.")
        with network.download(target, ctx.settings) as p:
            yield p
        return
    yield ctx.policy.input_file(target, what)


# ---------------------------------------------------------------- inspect / analyze


def op_inspect(ctx: OpContext, video: str) -> dict:
    from ..analysis.video import inspect_video

    with resolve_media(ctx, video) as p:
        meta = inspect_video(p, ctx.settings)
    out = meta.model_dump(mode="json")
    out["warnings"] = injection_warnings({f"container_tags.{k}": v for k, v in meta.container_tags.items()})
    return ctx.bound(out)


def op_analyze(ctx: OpContext, video: str, *, transcript: str | None = None, cut_threshold: float = 0.28) -> tuple[AnalysisReport, dict]:
    from ..engine.pipeline import analyze_video

    tx = ctx.policy.input_file(transcript, "transcript") if transcript else None
    with resolve_media(ctx, video) as p:
        rep, cached = analyze_video(p, ctx.settings, transcript=tx, cut_threshold=cut_threshold)
    d = rep.model_dump(mode="json")
    d["cache_hit"] = cached
    return rep, ctx.bound(d)


# ---------------------------------------------------------------- plan


def _project(rep: AnalysisReport, prefs: Preferences) -> VideoProject:
    return VideoProject(name=Path(rep.metadata.path).stem, video=rep.metadata, brief=prefs.brief)


def _provider_options(ctx: OpContext) -> list[dict]:
    from ..providers import registry

    return [c.model_dump(mode="json") for c in registry.capabilities(ctx.settings)]


def build_plan(ctx: OpContext, rep: AnalysisReport, prefs: Preferences, variants: int = 1) -> MusicContextResult:
    from ..direction.planner import create_plan
    from ..providers import registry

    plan = create_plan(rep, prefs, load_profile(ctx, prefs), variants=variants)
    warnings = list(rep.warnings)
    messages: list[str] = []
    if rep.stats.get("speech_source") == "heuristic_vad":
        messages.append("Speech timing is estimated. Pass --transcript <file.srt|.vtt|.json> for exact voiceover timing.")
    if not any(m.source == "user" for m in plan.markers):
        messages.append("Key moments were inferred from the energy curve. If the video has a specific reveal or CTA, pass --marker 18.2=reveal to anchor the music to it.")
    warnings.extend(registry.load_warnings())
    res = new_result(
        _project(rep, prefs), prefs, rep, plan.directions[0],
        alternatives=plan.directions[1:], markers=plan.markers, timeline=plan.timeline,
        sync_opportunities=plan.sync_opportunities, provider_options=_provider_options(ctx),
        provenance={"analysis": "ffmpeg + numpy heuristics (no vision model)", "direction": "MusicContext direction engine",
                    "speech": str(rep.stats.get("speech_source"))},
        warnings=warnings, messages=messages,
    )
    return res


def op_plan(ctx: OpContext, video: str, *, prefs: dict | None = None, variants: int = 1, transcript: str | None = None,
            output: str | None = None, save: bool = True, force: bool = False) -> tuple[MusicContextResult, Path | None, dict]:
    p = prefs_from_dict(prefs)
    rep, _ = op_analyze(ctx, video, transcript=transcript)
    res = build_plan(ctx, rep, p, variants)
    path = _save(ctx, res, output, save, force)
    return res, path, ctx.bound(res.model_dump(mode="json"))


def _save(ctx: OpContext, res: MusicContextResult, output: str | None, save: bool, force: bool) -> Path | None:
    if not save:
        return None
    target = ctx.policy.output_file(output, force=force) if output else default_artifact_path(ctx.settings, Path(res.project.video.path), res.project.video.sha256)
    return save_artifact(res, target, force=force or output is None)


def _existing(ctx: OpContext, video: str, artifact: str | None) -> MusicContextResult | None:
    if artifact:
        return load_artifact(ctx.policy.input_file(artifact, "artifact"))
    if not network.is_url(video):
        try:
            p = ctx.policy.input_file(video, "video")
        except MusicContextError:
            return None
        if str(p).endswith(".musicctx"):
            return load_artifact(p)
    return None


# ---------------------------------------------------------------- search / recommend


def op_search(ctx: OpContext, *, text: str = "", video: str | None = None, prefs: dict | None = None, limit: int = 20,
              provider: str | None = None, artifact: str | None = None) -> dict:
    from ..music.search import search_music
    from ..providers.base import SearchQuery

    direction = None
    if video or artifact:
        res = _existing(ctx, video or "", artifact)
        if res is None and video:
            res, _, _ = op_plan(ctx, video, prefs=prefs, save=False)
        direction = res.music_direction if res else None
    if direction is not None:
        q = SearchQuery.from_direction(direction, direction.arc.sections[-1].end_time if direction.arc.sections else None)
        if text:
            q.text = f"{q.text} {text}".strip()
    else:
        p = prefs_from_dict(prefs)
        q = SearchQuery(text=text, styles=p.style or p.genre, moods=p.mood, bpm_range=(p.bpm * 0.9, p.bpm * 1.1) if p.bpm else None,
                        target_bpm=p.bpm, vocals=p.vocals, require_commercial=p.commercial_use, min_duration=p.duration)
    out = search_music(ctx.settings, q, providers=[provider] if provider else None, limit=limit)
    return ctx.bound({
        "query": q.describe(), "candidates": [c.model_dump(mode="json") for c in out.candidates],
        "by_provider": out.by_provider, "warnings": out.warnings, "messages": out.messages,
    })


def op_recommend(ctx: OpContext, video: str, *, prefs: dict | None = None, limit: int = 5, provider: str | None = None,
                 transcript: str | None = None, artifact: str | None = None, output: str | None = None,
                 save: bool = True, force: bool = False) -> tuple[MusicContextResult, Path | None, dict]:
    from ..music.search import search_music
    from ..music.selection import rank_candidates
    from ..providers.base import SearchQuery
    from ..sync.alignment import plan_alignment

    res = _existing(ctx, video, artifact)
    if res is None:
        res, _, _ = op_plan(ctx, video, prefs=prefs, transcript=transcript, save=False)
    direction, rep = res.music_direction, res.analysis
    q = SearchQuery.from_direction(direction, rep.metadata.duration)
    found = search_music(ctx.settings, q, providers=[provider] if provider else None, limit=max(limit * 4, 20))
    ranked, excluded = rank_candidates(found.candidates, direction, rep, q)
    selected = None
    if ranked:
        top = ranked[0]
        selected = MusicSelection(assessment=top, alignment=plan_alignment(top.candidate, direction, rep, res.markers), selected_by="recommendation")
    messages = list(res.messages) + list(found.messages)
    if not ranked:
        messages.append("No candidate music is available yet. Add your own music with `musiccontext library scan <dir>`, "
                        "or generate a track offline with `musiccontext generate <video>`.")
    res = res.model_copy(update={
        "candidates": ranked[:limit], "excluded_candidates": excluded[:20], "selected": selected,
        "licenses": [c.candidate.license for c in ranked[:limit] if c.candidate.license],
        "warnings": list(dict.fromkeys(res.warnings + found.warnings)), "messages": list(dict.fromkeys(messages)),
    })
    path = _save(ctx, res, output, save, force)
    return res, path, ctx.bound(res.model_dump(mode="json"))


# ---------------------------------------------------------------- generate


def op_generate(ctx: OpContext, video: str, *, prefs: dict | None = None, provider: str | None = None, dest: str | None = None,
                duration: float | None = None, seed: int | None = None, transcript: str | None = None,
                artifact: str | None = None, output: str | None = None, save: bool = True, force: bool = False):
    from ..music.generation import build_request, generate_music, generation_report

    res = _existing(ctx, video, artifact)
    if res is None:
        res, _, _ = op_plan(ctx, video, prefs=prefs, transcript=transcript, save=False)
    out_path = ctx.policy.output_file(dest, force=force) if dest else None
    gen = generate_music(ctx.settings, res.music_direction, res.analysis, provider=provider, dest=out_path,
                         duration=duration, seed=seed, markers=res.markers, force=force)
    req = build_request(res.music_direction, res.analysis, duration=duration, seed=seed, markers=res.markers)
    res = res.model_copy(update={"generated": gen, "licenses": _dedupe_licenses([*res.licenses, *([gen.license] if gen.license else [])])})
    path = _save(ctx, res, output, save, force)
    return res, path, ctx.bound({"generation": generation_report(gen, req), "artifact": str(path) if path else None})


def _dedupe_licenses(licenses: list) -> list:
    """Pydantic models are not hashable, so dedupe on the fields that identify a license."""
    seen, out = set(), []
    for lic in licenses:
        if lic is None:
            continue
        key = (lic.provider, lic.license, lic.source_url, lic.commercial_use, lic.verified)
        if key not in seen:
            seen.add(key)
            out.append(lic)
    return out


# ---------------------------------------------------------------- sync / render


def op_sync(ctx: OpContext, *, video: str, music: str, output: str, artifact: str | None = None, prefs: dict | None = None,
            duck: bool = True, ducking_mode: str = "expression", loop: bool | None = None, target_lufs: float | None = None,
            keep_original_audio: bool = True, transcript: str | None = None, force: bool = False, dry_run: bool = False,
            copy_video: bool = True) -> dict:
    from ..music.features import analyze_music
    from ..schemas import MusicCandidate
    from ..sync.alignment import plan_alignment
    from ..sync.editor import RenderPlan, render

    res = _existing(ctx, video, artifact)
    with resolve_media(ctx, video) as vpath, resolve_media(ctx, music, "music") as mpath:
        if res is None:
            rep, _ = op_analyze(ctx, str(vpath), transcript=transcript)
            res = build_plan(ctx, rep, prefs_from_dict(prefs))
        rep, direction = res.analysis, res.music_direction
        feats = analyze_music(mpath, ctx.settings)
        cand = MusicCandidate(id="input", provider="user", title=mpath.name, uri=str(mpath), features=feats)
        align = plan_alignment(cand, direction, rep, res.markers)
        if loop is not None:
            align = align.model_copy(update={"loop": loop})
        plan = RenderPlan(
            video=vpath, music=mpath, output=Path(output), alignment=align,
            ducking=direction.ducking if duck else None, speech=rep.speech, keep_original_audio=keep_original_audio,
            target_lufs=target_lufs, ducking_mode=ducking_mode, copy_video=copy_video, force=force,
        )
        result = render(plan, ctx.settings, policy=ctx.policy, dry_run=dry_run)
    return ctx.bound({
        "output": str(result.output), "duration": result.duration, "expected_duration": result.expected_duration,
        "applied": result.applied, "filters": result.filters, "ducking_applied": result.ducking_applied,
        "sync_points": [s.model_dump(mode="json") for s in result.sync_points],
        "alignment": align.model_dump(mode="json"), "warnings": result.warnings,
        "command": result.command_display(), "dry_run": dry_run,
    })


# ---------------------------------------------------------------- misc


def op_providers(ctx: OpContext) -> dict:
    from ..providers import registry

    return ctx.bound({"providers": _provider_options(ctx), "warnings": registry.load_warnings(),
                      "local_only": ctx.settings.local_only,
                      "note": "A provider marked available=false is reported, never silently skipped. Credential values are never shown."})


def op_library_search(ctx: OpContext, text: str = "", *, limit: int = 20, bpm: float | None = None, vocals: str | None = None,
                      min_duration: float | None = None, commercial_use: bool = False) -> dict:
    from ..music.library import search_tracks
    from ..providers.base import SearchQuery

    q = SearchQuery(text=text, target_bpm=bpm, bpm_range=(bpm * 0.9, bpm * 1.1) if bpm else None, vocals=vocals,
                    min_duration=min_duration, require_commercial=commercial_use,
                    tags_any=[t for t in text.lower().split() if t])
    tracks = search_tracks(ctx.settings, q, limit=limit)
    return ctx.bound({"query": q.describe(), "count": len(tracks), "tracks": [t.model_dump(mode="json") for t in tracks]})


def op_get_artifact(ctx: OpContext, *, path: str | None = None, video: str | None = None, full: bool = False) -> dict:
    if path:
        res = load_artifact(ctx.policy.input_file(path, "artifact"))
    elif video:
        p = find_artifact(ctx.settings, Path(video))
        if not p:
            raise InvalidArgumentError(f"No stored artifact found for '{Path(video).name}'.", "Run `musiccontext plan <video>` first.")
        res = load_artifact(p)
    else:
        raise InvalidArgumentError("Provide either a path or a video.", "e.g. musiccontext export demo.mp4")
    return ctx.bound(res.model_dump(mode="json") if full else summarize(res))


def direction_summary(d: MusicDirection) -> dict:
    """Compact direction view used by human output and by agents that only need the brief."""
    return {
        "style": d.style, "mood": d.mood, "energy": d.energy, "target_bpm": d.tempo.target_bpm,
        "bpm_range": [d.tempo.min_bpm, d.tempo.max_bpm], "rhythm": d.rhythm, "vocals": d.vocals,
        "texture": d.texture, "instruments": d.instrumentation.preferred, "avoid": d.instrumentation.prohibited,
        "arc": [{"label": s.label, "start": s.start_time, "end": s.end_time, "energy": s.energy, "action": s.action} for s in d.arc.sections],
        "peak_time": d.arc.peak_time, "ending": d.arc.ending, "brief": d.brief,
        "confidence": d.explanation.confidence, "confidence_note": d.explanation.confidence_note,
    }
