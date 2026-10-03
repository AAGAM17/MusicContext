"""Find where visual markers and musical events (beats/downbeats/phrase ends/lifts) coincide."""

from __future__ import annotations

from ..schemas import MusicFeatures, MusicMarker, SyncPoint
from .beats import nearest


def music_events(f: MusicFeatures, placement_offset: float = 0.0) -> dict[str, list[float]]:
    """Musical event times on the VIDEO timeline given where the track is placed (offset = video time of track t=0)."""
    db = f.downbeats
    phrase = db[3::4] if len(db) >= 4 else []
    return {
        "downbeat": [t + placement_offset for t in db],
        "beat": [t + placement_offset for t in f.beats],
        "phrase_end": [t + placement_offset for t in phrase],
        "lift": [t + placement_offset for t, _ in f.lifts],
    }


def find_sync_points(markers: list[MusicMarker], events: dict[str, list[float]], quality: str, tolerance: float = 0.15,
                     min_importance: float = 0.5) -> list[SyncPoint]:
    """For each important marker, the closest musical event within `tolerance` seconds (priority: lift > phrase_end > downbeat > beat)."""
    out: list[SyncPoint] = []
    for m in markers:
        if m.importance < min_importance:
            continue
        best = None
        for kind in ("lift", "phrase_end", "downbeat", "beat"):
            n = nearest(events.get(kind, []), m.start_time)
            if n and abs(n[1]) <= tolerance and (best is None or abs(n[1]) < abs(best[1]) - 0.02):
                best = (kind, n[1], n[0])
        if best:
            kind, delta, t = best
            out.append(SyncPoint(marker_time=m.start_time, marker_type=m.type, music_event=kind, music_time=round(t, 3),  # type: ignore[arg-type]
                                 offset_ms=round(delta * 1000, 1), quality=quality,  # type: ignore[arg-type]
                                 note=f"{kind.replace('_', ' ')} {abs(delta)*1000:.0f} ms {'after' if delta > 0 else 'before'} the {m.type.replace('_', ' ')}"))
    return out
