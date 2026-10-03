"""Build music markers and a merged timeline from an AnalysisReport plus user-supplied markers."""

from __future__ import annotations

from ..schemas import AnalysisReport, MusicMarker, Preferences
from . import markers as mk

VO_GAP = 1.5  # speech segments closer than this form one voiceover block


def voiceover_blocks(speech) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for s in sorted(speech, key=lambda s: s.start_time):
        if out and s.start_time - out[-1][1] <= VO_GAP:
            out[-1][1] = max(out[-1][1], s.end_time)
        else:
            out.append([s.start_time, s.end_time])
    return [(a, b) for a, b in out]


def build_markers(rep: AnalysisReport, prefs: Preferences | None = None) -> list[MusicMarker]:
    prefs = prefs or Preferences()
    d = rep.metadata.duration
    out: list[MusicMarker] = []
    for tr in rep.transitions:
        if tr.kind == "hard_cut":
            out.append(mk.make(tr.start_time, "hard_cut", f"Hard cut (scene score {tr.strength:.2f})", source="detected", confidence=tr.confidence,
                               provenance=tr.provenance, importance=0.25 + 0.3 * tr.strength))
        else:
            out.append(mk.make(tr.start_time, "transition", f"Soft transition (score {tr.strength:.2f})", source="detected", confidence=tr.confidence, provenance=tr.provenance))
    segs = rep.story.segments
    for prev, nxt in zip(segs[:-1], segs[1:], strict=True):
        delta = nxt.energy - prev.energy
        act = "lift" if delta > 0.15 else "release" if delta < -0.15 else "transition_fill"
        out.append(mk.make(nxt.start_time, "scene_change", f"Story section change: {prev.role} -> {nxt.role} (energy {prev.energy:.2f} -> {nxt.energy:.2f})",
                           confidence=0.55, provenance="story segmentation", action=act, importance=0.5 + min(abs(delta), 0.4)))
    for s in segs:
        if s.role == "reveal":
            out.append(mk.make(s.start_time, "reveal", f"Energy peaks after a build (energy {s.energy:.2f}); inferred payoff moment", confidence=0.45, provenance="story model peak"))
        elif s.role == "climax":
            out.append(mk.make(s.start_time, "action_peak", f"Highest-energy section (energy {s.energy:.2f})", confidence=0.45, provenance="story model peak"))
        elif s.role == "resolution_cta":
            out.append(mk.make(s.start_time, "cta", "Closing section after the peak; possible call to action", confidence=0.35, provenance="story model: last segment"))
    for a, b in voiceover_blocks(rep.speech):
        src = "detected" if rep.stats.get("speech_source") in ("transcript", "embedded_subtitles") else "estimated"
        conf = 0.9 if src == "detected" else 0.55
        out.append(mk.make(a, "voiceover_start", "Speech begins; music should make space", source=src, confidence=conf, provenance=f"speech via {rep.stats.get('speech_source')}"))
        out.append(mk.make(b, "voiceover_end", "Speech ends; music can return", source=src, confidence=conf, provenance=f"speech via {rep.stats.get('speech_source')}"))
    out.append(mk.make(d, "ending", "End of video", source="detected", confidence=1.0, provenance="container duration", action="resolve"))
    for t, kind in prefs.user_markers:
        out.append(mk.make(min(t, d), kind, f"Supplied by the user/agent: {kind} at {t:.2f}s", source="user", confidence=1.0, provenance="user marker"))
    # user markers supersede inferred markers of the same type within 1.5 s
    user = [m for m in out if m.source == "user"]
    keep = [m for m in out if m.source == "user" or not any(u.type == m.type and abs(u.start_time - m.start_time) <= 1.5 for u in user)]
    keep.sort(key=lambda m: (m.start_time, -m.importance))
    return keep


def build_timeline(markers: list[MusicMarker], rep: AnalysisReport, sections=None) -> list[dict]:
    rows: list[dict] = []
    for m in markers:
        if m.type in ("hard_cut",) and m.importance < 0.4:
            continue  # cuts stay in `markers`; the merged timeline lists only meaningful beats
        rows.append({"t": m.start_time, "kind": "marker", "type": m.type, "action": m.suggested_music_action, "importance": m.importance, "source": m.source, "label": m.reason})
    for a, b in voiceover_blocks(rep.speech):
        rows.append({"t": a, "end": b, "kind": "voiceover", "label": f"voiceover {a:.1f}-{b:.1f}s", "source": rep.stats.get("speech_source")})
    for s in sections or []:
        rows.append({"t": s.start_time, "end": s.end_time, "kind": "music_section", "label": f"{s.label}: {s.description}", "energy": s.energy, "action": s.action})
    rows.sort(key=lambda r: (r["t"], r["kind"]))
    return rows
