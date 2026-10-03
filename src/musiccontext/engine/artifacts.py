"""The portable `.musicctx` artifact: save, load, locate and summarize."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from .. import ARTIFACT_VERSION, __version__
from ..errors import ArtifactError
from ..schemas import MusicContextResult

SUFFIX = ".musicctx"


def now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


def default_artifact_path(settings, video_path: Path, content_hash: str = "") -> Path:
    stem = "".join(c for c in Path(video_path).stem if c.isalnum() or c in "._-")[:60] or "project"
    tail = f"-{content_hash[:8]}" if content_hash else ""
    return settings.artifacts_dir / f"{stem}{tail}{SUFFIX}"


def save_artifact(result: MusicContextResult, path: Path, *, force: bool = False) -> Path:
    path = Path(path)
    if path.exists() and not force:
        raise ArtifactError(f"Artifact already exists: {path}", "Pass --force to overwrite, or choose another --output path.")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    tmp.replace(path)
    return path


def load_artifact(path: Path) -> MusicContextResult:
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except OSError as e:
        raise ArtifactError(f"Cannot read artifact '{path.name}': {e.strerror}.") from None
    except json.JSONDecodeError as e:
        raise ArtifactError(f"Artifact '{path.name}' is not valid JSON (line {e.lineno}).", "Re-create it with `musiccontext plan`.") from None
    if not isinstance(data, dict) or data.get("format") != "musiccontext.artifact":
        raise ArtifactError(f"'{path.name}' is not a MusicContext artifact.", "Expected a .musicctx file produced by `musiccontext plan`.")
    ver = str(data.get("version", "0"))
    if ver.split(".")[0] != ARTIFACT_VERSION.split(".")[0]:
        raise ArtifactError(f"Artifact '{path.name}' uses format version {ver}; this build understands {ARTIFACT_VERSION}.",
                            "Re-run `musiccontext plan` to regenerate it.")
    try:
        return MusicContextResult.model_validate(data)
    except Exception as e:  # noqa: BLE001 - pydantic error detail is long; keep it actionable
        raise ArtifactError(f"Artifact '{path.name}' does not match the current schema.", f"Re-create it with `musiccontext plan`. ({type(e).__name__})") from None


def find_artifact(settings, video_path: Path, content_hash: str = "") -> Path | None:
    p = default_artifact_path(settings, video_path, content_hash)
    if p.is_file():
        return p
    stem = Path(video_path).stem
    matches = sorted(settings.artifacts_dir.glob(f"{stem}*{SUFFIX}")) if settings.artifacts_dir.is_dir() else []
    return matches[0] if matches else None


def new_result(project, preferences, analysis, direction, **kw) -> MusicContextResult:
    return MusicContextResult(tool_version=__version__, created_at=now_iso(), project=project, preferences=preferences,
                              analysis=analysis, music_direction=direction, **kw)


def summarize(result: MusicContextResult, max_items: int = 8) -> dict:
    """A bounded, agent-friendly summary of an artifact."""
    d = result.music_direction
    sel = result.selected
    return {
        "video": {"path": result.project.video.path, "duration": result.project.video.duration,
                  "resolution": f"{result.project.video.width}x{result.project.video.height}", "has_audio": result.project.video.has_audio},
        "story": {"pace": result.analysis.story.pace, "segments": [{"role": s.role, "start": s.start_time, "end": s.end_time, "energy": s.energy} for s in result.analysis.story.segments[:max_items]],
                  "peak_time": result.analysis.story.peak_time, "speech_ratio": result.analysis.stats.get("speech_ratio")},
        "direction": {"style": d.style, "mood": d.mood, "energy": d.energy, "target_bpm": d.tempo.target_bpm,
                      "bpm_range": [d.tempo.min_bpm, d.tempo.max_bpm], "vocals": d.vocals, "rhythm": d.rhythm,
                      "sections": [{"label": s.label, "start": s.start_time, "end": s.end_time, "energy": s.energy, "action": s.action} for s in d.arc.sections],
                      "ducking": {"enabled": d.ducking.enabled, "duck_depth_db": d.ducking.duck_depth_db},
                      "confidence": d.explanation.confidence},
        "markers": [{"t": m.timestamp, "type": m.type, "action": m.suggested_music_action, "importance": m.importance, "source": m.source} for m in result.markers if m.importance >= 0.5][:max_items * 2],
        "selected": None if not sel else {"id": sel.assessment.candidate.id, "title": sel.assessment.candidate.title,
                                          "provider": sel.assessment.candidate.provider, "reasons": sel.assessment.reasons[:4]},
        "candidate_count": len(result.candidates),
        "warnings": result.warnings[:max_items],
        "messages": result.messages[:max_items],
    }
