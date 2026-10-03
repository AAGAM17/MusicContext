"""Placement must be musical, honest about its basis, and must say why it chose each number."""

from __future__ import annotations

import pytest
from conftest import make_candidate, needs_ffmpeg

from musiccontext.errors import InvalidArgumentError
from musiccontext.schemas import AlignmentPlan, MusicCandidate
from musiccontext.sync.alignment import alignment_summary, plan_alignment, verify_coverage

pytestmark = needs_ffmpeg

# A long track with two lifts: the stronger one at 14.0s can be slid onto the fixture's 11.0s peak.
LIFTS = [(14.0, 1.2), (20.0, 0.8)]


def long_track(duration=40.0):
    return make_candidate(cid="long", bpm=120.0, duration=duration, features_kw={"lifts": LIFTS})


def residual(plan, candidate, peak):
    """Where the lifts actually land on the video timeline, relative to the peak."""
    offset = plan.video_delay - plan.music_start
    return min(abs(t + offset - peak) for t, _ in candidate.features.lifts)


def test_long_track_does_not_loop_and_lands_a_lift_on_the_peak(direction, analysis, plan):
    peak = direction.arc.peak_time
    assert peak is not None, "the fixture direction is expected to have a peak"
    c = long_track()
    p = plan_alignment(c, direction, analysis, plan.markers)
    assert p.loop is False and p.loop_end is None
    assert p.music_start > 0
    assert residual(p, c, peak) <= 0.15
    note = next(n for n in p.notes if "music_start" in n)
    assert "residual error" in note and f"{peak:.1f}s peak" in note
    assert verify_coverage(p, c.features.duration, analysis.metadata.duration) == []


def test_music_start_never_starves_the_video(direction, analysis, plan):
    """The seek is clamped so the remaining track still reaches the end of the video."""
    c = long_track(duration=analysis.metadata.duration + 1.0)
    p = plan_alignment(c, direction, analysis, plan.markers)
    assert 0.0 <= p.music_start <= 1.0 + 1e-6
    assert c.features.duration - p.music_start >= analysis.metadata.duration - 1e-6
    assert verify_coverage(p, c.features.duration, analysis.metadata.duration) == []


def test_short_track_loops_on_a_downbeat(direction, analysis, plan):
    c = make_candidate(cid="short", bpm=120.0, duration=12.0)
    assert c.features.downbeats
    p = plan_alignment(c, direction, analysis, plan.markers)
    assert p.loop is True
    assert p.loop_end in c.features.downbeats
    assert p.loop_crossfade == 0.0  # a bar line needs no crossfade
    assert "downbeat" in " ".join(p.notes)
    assert verify_coverage(p, c.features.duration, analysis.metadata.duration) == []


def test_short_track_without_beats_loops_at_the_track_end_with_a_crossfade(direction, analysis, plan):
    c = make_candidate(cid="nobeats", bpm=None, duration=12.0)
    assert c.features.downbeats == [] and c.features.bpm is None
    p = plan_alignment(c, direction, analysis, plan.markers)
    assert p.loop is True
    assert p.loop_end == pytest.approx(c.features.duration)
    assert 0.5 <= p.loop_crossfade <= 1.5
    assert p.basis == "none"
    assert p.sync_points == []
    assert "no beat data" in " ".join(p.notes)


def test_natural_ending_only_in_the_exact_fit_case(direction, analysis, plan):
    exact = make_candidate(cid="exact", bpm=120.0, duration=analysis.metadata.duration,
                           features_kw={"lifts": LIFTS})
    fitted = plan_alignment(exact, direction, analysis, plan.markers)
    assert fitted.natural_ending is True
    assert fitted.fade_out <= 0.5  # it resolves on its own; no need to fade it
    assert plan_alignment(long_track(), direction, analysis, plan.markers).natural_ending is False
    assert plan_alignment(make_candidate(cid="s", bpm=120.0, duration=12.0), direction, analysis,
                          plan.markers).natural_ending is False
    off = plan_alignment(exact, direction, analysis, plan.markers, prefer_natural_ending=False)
    assert off.natural_ending is False and off.fade_out > 1.0


def test_fade_out_follows_the_arc_ending(direction, analysis, plan):
    faded = direction.model_copy(update={"arc": direction.arc.model_copy(update={"ending": "fade_out"})})
    p = plan_alignment(long_track(), analysis=analysis, direction=faded, markers=plan.markers)
    assert p.fade_out > 1.0
    assert "fade_out" in " ".join(p.notes)


def test_fade_in_is_short_unless_the_first_section_is_an_intro(direction, analysis, plan):
    assert 0.3 <= plan_alignment(long_track(), direction, analysis, plan.markers).fade_in <= 1.0
    sections = [direction.arc.sections[0].model_copy(update={"label": "intro"}), *direction.arc.sections[1:]]
    intro = direction.model_copy(update={"arc": direction.arc.model_copy(update={"sections": sections})})
    p = plan_alignment(long_track(), intro, analysis, plan.markers)
    assert 1.0 < p.fade_in <= 2.0


def test_sync_points_are_tight_and_honest_about_their_quality(direction, analysis, plan):
    c = long_track()
    p = plan_alignment(c, direction, analysis, plan.markers)
    assert p.sync_points, "a 120 BPM track against this fixture should hit several markers"
    assert p.basis == "estimated"
    important = {round(m.start_time, 3) for m in plan.markers if m.importance >= 0.5}
    for sp in p.sync_points:
        assert abs(sp.offset_ms) <= 150
        assert sp.quality == "estimated"  # DSP-derived beats are never reported as detected
        assert round(sp.marker_time, 3) in important
    assert any(sp.music_event == "lift" and sp.marker_time == direction.arc.peak_time for sp in p.sync_points)


def test_gain_uses_the_ducking_bed_and_flags_unmeasured_loudness(direction, analysis, plan):
    assert analysis.speech, "the fixture video has narration"
    p = plan_alignment(long_track(), direction, analysis, plan.markers)
    assert p.gain_db == direction.ducking.bed_gain_db
    assert "unverified" in " ".join(p.notes)

    loud = make_candidate(cid="loud", bpm=120.0, duration=40.0,
                          features_kw={"lifts": LIFTS, "loudness_lufs": -14.0, "loudness_source": "ebur128"})
    q = plan_alignment(loud, direction, analysis, plan.markers)
    assert q.gain_db == pytest.approx(direction.ducking.bed_gain_db - 22.0 + 14.0)
    assert "-14.0 LUFS" in " ".join(q.notes) and "-22 LUFS" in " ".join(q.notes)


def test_no_speech_means_no_gain_change(direction, analysis, plan, silent_video, settings):
    from musiccontext.direction.planner import create_plan
    from musiccontext.engine.pipeline import analyze_video

    quiet, _ = analyze_video(silent_video, settings)
    qplan = create_plan(quiet)
    p = plan_alignment(long_track(duration=30.0), qplan.directions[0], quiet, qplan.markers)
    assert p.gain_db == 0.0
    assert p.video_delay == 0.0
    assert "no speech" in " ".join(p.notes)


def test_features_are_required_and_the_error_says_what_to_do(direction, analysis, plan):
    bare = MusicCandidate(id="bare", provider="local")
    with pytest.raises(InvalidArgumentError) as e:
        plan_alignment(bare, direction, analysis, plan.markers)
    assert "analyze_music" in (e.value.hint or "")


def test_verify_coverage_flags_a_plan_that_cannot_cover_the_video():
    short = AlignmentPlan(music_start=0.0, loop=False, fade_out=1.5)
    warnings = verify_coverage(short, track_duration=10.0, video_duration=30.0)
    assert len(warnings) == 1
    assert "20.00s of silence" in warnings[0] and "loop=True" in warnings[0]

    assert verify_coverage(AlignmentPlan(music_start=12.0), 10.0, 30.0)[0].startswith("music_start")
    assert verify_coverage(AlignmentPlan(loop=True, loop_end=1.0, music_start=5.0), 30.0, 10.0)[0].startswith("loop_end")
    overrun = AlignmentPlan(music_start=0.0, loop=False, fade_out=0.0)
    assert "cut off mid-phrase" in verify_coverage(overrun, 60.0, 10.0)[0]
    assert verify_coverage(AlignmentPlan(music_start=0.0, loop=False, fade_out=1.5), 30.0, 30.0) == []


def test_alignment_summary_is_one_readable_paragraph(direction, analysis, plan):
    text = alignment_summary(plan_alignment(long_track(), direction, analysis, plan.markers))
    assert "\n" not in text
    assert "fade in" in text and "marker(s)" in text
