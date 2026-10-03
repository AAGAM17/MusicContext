"""Video metadata via ffprobe."""

from __future__ import annotations

from math import gcd
from pathlib import Path

from ..errors import UnsupportedMediaError
from ..schemas import VideoMetadata
from ..security.content import sanitize_meta
from .media import content_hash, ffprobe


def _fps(s: str | None) -> float:
    try:
        a, b = (s or "0/1").split("/")
        return float(a) / float(b) if float(b) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def inspect_video(path: Path, settings=None, *, with_hash: bool = True) -> VideoMetadata:
    info = ffprobe(path, settings)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    if v is None:
        raise UnsupportedMediaError(f"'{path.name}' has no video stream.", "Pass a video file; for audio use `musiccontext library add`.")
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    fmt = info.get("format", {})
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    g = gcd(w, h) or 1
    dur = float(fmt.get("duration") or v.get("duration") or 0)
    if dur <= 0:
        raise UnsupportedMediaError(f"Could not determine the duration of '{path.name}'.")
    return VideoMetadata(
        path=str(path),
        sha256=content_hash(path) if with_hash else "",
        duration=round(dur, 3),
        fps=round(_fps(v.get("avg_frame_rate") if v.get("avg_frame_rate") not in (None, "0/0") else v.get("r_frame_rate")), 3),
        width=w,
        height=h,
        aspect_ratio=f"{w // g}:{h // g}" if w and h else "unknown",
        aspect_ratio_float=round(w / h, 4) if h else 0.0,
        video_codec=v.get("codec_name"),
        has_audio=bool(audio),
        audio_streams=len(audio),
        audio_codec=audio[0].get("codec_name") if audio else None,
        audio_sample_rate=int(audio[0]["sample_rate"]) if audio and audio[0].get("sample_rate") else None,
        subtitle_streams=len(subs),
        size_bytes=int(fmt.get("size") or path.stat().st_size),
        container_tags=sanitize_meta(fmt.get("tags", {})),
    )
