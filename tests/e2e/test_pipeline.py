"""The acceptance path: video -> analysis -> story -> plan -> selection -> sync -> final video.

These tests assert the product promise, not implementation details: that timestamps line up,
that the output really is the right length, that nothing is overwritten, and that every claim
carries its basis.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import DEMO_DURATION, SCENE_BOUNDS, SPEECH_INTERVALS, needs_ffmpeg

pytestmark = [needs_ffmpeg, pytest.mark.ffmpeg]


@pytest.fixture
def ctx(settings):
    from musiccontext.agent.ops import OpContext

    return OpContext.create(settings)


def _overlaps(a, b, c, d) -> float:
    return max(0.0, min(b, d) - max(a, c))


def test_analysis_finds_the_real_structure(ctx, demo_video):
    from musiccontext.agent import ops

    rep, payload = ops.op_analyze(ctx, str(demo_video))

    assert rep.metadata.duration == pytest.approx(DEMO_DURATION, abs=0.2)
    assert rep.metadata.has_audio

    # the fixture has hard cuts at 5, 11 and 17 s; every detected cut must be a real one
    cuts = [t.start_time for t in rep.transitions]
    for expected in SCENE_BOUNDS[1:-1]:
        assert any(abs(c - expected) < 0.35 for c in cuts), f"missed the cut at {expected}s: {cuts}"
    for c in cuts:
        assert min(abs(c - e) for e in SCENE_BOUNDS[1:-1]) < 0.35, f"invented a cut at {c}s"

    # the busiest scene (11-17 s, testsrc2) must be the energy peak
    busiest = max(rep.scenes, key=lambda s: s.energy)
    assert busiest.start_time == pytest.approx(11.0, abs=0.4)

    # speech is found inside the intervals we synthesized, and not outside them
    assert rep.speech, "no speech detected in a video that has narration"
    covered = sum(_overlaps(s.start_time, s.end_time, a, b) for s in rep.speech for a, b in SPEECH_INTERVALS)
    total = sum(s.duration for s in rep.speech)
    assert covered / total > 0.85, "speech segments stray outside the narrated intervals"
    assert 0.25 < rep.stats["speech_ratio"] < 0.75

    # provenance discipline: nothing unexplained
    for item in [*rep.scenes, *rep.transitions, *rep.speech, *rep.story.segments]:
        assert item.source in ("detected", "estimated", "inferred", "user", "provider")
        assert item.provenance, f"{type(item).__name__} has no provenance"
    assert payload["capabilities"]["vision_model"].startswith("not used")


def test_plan_anchors_the_music_to_a_told_moment(ctx, demo_video):
    from musiccontext.agent import ops

    res, path, _ = ops.op_plan(ctx, str(demo_video), prefs={"marker": ["11.0=reveal"], "platform": "product-demo"}, save=True)
    d = res.music_direction

    assert d.arc.peak_time == pytest.approx(11.0, abs=0.01)
    labels = [s.label for s in d.arc.sections]
    assert "peak" in labels and labels.index("peak") > 0, f"no build before the peak: {labels}"
    peak = next(s for s in d.arc.sections if s.label == "peak")
    assert peak.start_time == pytest.approx(11.0, abs=0.01)
    assert peak.source == "user", "a user-supplied reveal must be recorded as user-sourced"

    # narration present => instrumental, speech-aware tempo cap, ducking planned
    assert d.vocals == "none"
    assert d.tempo.target_bpm <= 112
    assert d.ducking.enabled and d.ducking.duck_depth_db > 0
    assert any("voiceover" in e for e in d.explanation.evidence)

    # the brief is usable by a human or a provider
    assert "11.0s" in d.brief and f"{DEMO_DURATION:.1f}s" in d.brief
    assert d.explanation.confidence_note

    # artifact round-trips
    from musiccontext.engine.artifacts import load_artifact

    again = load_artifact(path)
    assert again.music_direction.arc.peak_time == d.arc.peak_time
    assert json.loads(path.read_text())["format"] == "musiccontext.artifact"


def test_recommend_explains_and_excludes(ctx, demo_video, fx_dir, tmp_path):
    """A matching track is scored on every dimension; a mismatched one is filtered out."""
    import sys

    sys.path.insert(0, "scripts")
    from make_fixtures import make_click

    from musiccontext.agent import ops
    from musiccontext.music.library import scan_directory

    lib = tmp_path / "lib"
    lib.mkdir()
    make_click(lib / "bed95.wav", bpm=95.0, duration=40.0)   # inside the direction's range
    make_click(lib / "bed160.wav", bpm=160.0, duration=40.0)  # far outside it
    scan_directory(ctx.settings, lib)

    res, _p, _d = ops.op_recommend(ctx, str(demo_video), prefs={"marker": ["11.0=reveal"]}, limit=5)
    assert res.candidates, "a tempo-matched track in the library was not offered"
    titles = [a.candidate.title for a in res.candidates]
    assert any("95" in t for t in titles)
    assert not any("160" in t for t in titles), "a 160 BPM track should not match a ~93 BPM brief"

    top = res.candidates[0]
    names = {dim.name for dim in top.dimensions}
    assert names == {"mood_match", "tempo_match", "energy_match", "instrumentation_match", "structure_match",
                     "voiceover_compatibility", "duration_fit", "transition_fit", "licensing_fit"}
    tempo = next(d for d in top.dimensions if d.name == "tempo_match")
    assert tempo.level == "high" and tempo.basis == "estimated"
    # an unverified license must never read as cleared
    lic = next(d for d in top.dimensions if d.name == "licensing_fit")
    assert lic.level == "unknown"
    assert top.reasons

    # placement is concrete and honest
    plan = res.selected.alignment
    assert plan is not None
    assert plan.basis in ("estimated", "detected")
    for sp in plan.sync_points:
        assert abs(sp.offset_ms) <= 200
        assert sp.quality in ("detected", "estimated", "inferred")


def test_sync_renders_a_correct_video_without_touching_inputs(ctx, demo_video, tmp_path):
    import sys

    sys.path.insert(0, "scripts")
    from make_fixtures import make_click

    from musiccontext.agent import ops

    music = tmp_path / "bed.wav"
    make_click(music, bpm=95.0, duration=40.0)
    before = (demo_video.stat().st_mtime, demo_video.stat().st_size, music.stat().st_size)
    out = tmp_path / "final.mp4"

    d = ops.op_sync(ctx, video=str(demo_video), music=str(music), output=str(out))

    assert out.is_file()
    assert d["duration"] == pytest.approx(DEMO_DURATION, abs=0.25), "output length must match the video"
    assert d["ducking_applied"] is True
    assert any("duck" in a for a in d["applied"])
    assert (demo_video.stat().st_mtime, demo_video.stat().st_size, music.stat().st_size) == before

    # a second render to the same path must refuse
    from musiccontext.errors import InvalidPathError

    with pytest.raises(InvalidPathError):
        ops.op_sync(ctx, video=str(demo_video), music=str(music), output=str(out))
    ops.op_sync(ctx, video=str(demo_video), music=str(music), output=str(out), force=True)


def test_generate_then_sync_works_with_no_providers_configured(ctx, demo_video, tmp_path):
    """The offline path: nothing configured, no network, still produces a scored video."""
    from musiccontext.agent import ops

    res, _p, gen = ops.op_generate(ctx, str(demo_video), prefs={"marker": ["11.0=reveal"]}, seed=11, save=False)
    track = res.generated
    assert track is not None and track.status == "completed"
    audio = tmp_path.parent / track.artifact_path if False else track.artifact_path
    assert audio and str(audio).endswith(".wav")

    report = gen["generation"]
    assert report["bpm"]["measured"] == pytest.approx(report["bpm"]["requested"], abs=2.5), report["bpm"]
    assert report["duration"]["measured"] == pytest.approx(DEMO_DURATION, abs=0.3)
    # the license is legitimately verified: we synthesized the audio ourselves
    assert track.license and track.license.verified and track.license.commercial_use == "yes"

    out = tmp_path / "generated.mp4"
    d = ops.op_sync(ctx, video=str(demo_video), music=str(audio), output=str(out))
    assert d["duration"] == pytest.approx(DEMO_DURATION, abs=0.25)


def test_silent_video_gets_music_and_no_ducking(ctx, silent_video, tmp_path):
    import sys

    sys.path.insert(0, "scripts")
    from make_fixtures import make_click

    from musiccontext.agent import ops

    res, _p, _d = ops.op_plan(ctx, str(silent_video), save=False)
    assert res.music_direction.ducking.enabled is False
    assert res.analysis.capabilities["speech"].startswith("unavailable")

    music = tmp_path / "m.wav"
    make_click(music, bpm=110.0, duration=30.0)
    out = tmp_path / "silent-scored.mp4"
    d = ops.op_sync(ctx, video=str(silent_video), music=str(music), output=str(out))
    assert d["ducking_applied"] is False
    assert d["duration"] == pytest.approx(12.0, abs=0.25)
