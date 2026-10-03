"""The arc must honour what the user said about the video, even when the picture is quiet.

A text-driven explainer has almost no motion, so its story energy is flat. A user-declared reveal must still
read as a peak, and a user-declared call to action must still start the resolution.
"""

from __future__ import annotations

from musiccontext.direction.arc import build_arc
from musiccontext.schemas import StoryModel
from musiccontext.schemas.video import StorySegment
from musiccontext.timeline.markers import make


def flat_story(duration=32.0, cut=4.0, energy=0.2) -> StoryModel:
    n = int(duration // cut)
    segs = [StorySegment(start_time=i * cut, end_time=(i + 1) * cut, index=i, role="establishing" if i == 0 else "development", energy=energy)
            for i in range(n)]
    curve = [(float(t), energy) for t in range(int(duration) + 1)]
    return StoryModel(duration=duration, segments=segs, energy_curve=curve, peak_time=None)


def arc_for(markers):
    story = flat_story()
    return build_arc(story, story.energy_curve, markers, [], None)


def test_user_reveal_on_a_flat_video_is_a_real_peak():
    arc = arc_for([make(12.0, "reveal", "told", source="user", confidence=0.9)])
    peak = next(s for s in arc.sections if s.label == "peak")
    before = [s for s in arc.sections if s.end_time <= peak.start_time + 1e-6][-1]
    assert peak.start_time == 12.0
    assert peak.energy >= before.energy + 0.25, (peak.energy, before.energy)


def test_user_cta_starts_the_resolution_and_the_music_resolves():
    arc = arc_for([make(12.0, "reveal", "told", source="user"), make(28.0, "cta", "told", source="user")])
    labels = [(s.label, s.start_time, s.end_time) for s in arc.sections]
    assert ("resolution", 28.0, 32.0) in labels, labels
    assert arc.sections[-1].label == "resolution"
    assert not any(s.label == "resolution" and s.start_time < 28.0 for s in arc.sections)
    assert arc.ending == "resolve"


def test_an_inferred_cta_does_not_move_the_resolution():
    arc = arc_for([make(12.0, "reveal", "told", source="user"), make(28.0, "cta", "guessed", source="inferred")])
    assert not any(s.label == "resolution" for s in arc.sections)
