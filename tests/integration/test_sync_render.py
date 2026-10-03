"""Real renders through ffmpeg: duration, stream counts, refusals, and the measured duck depth."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from musiccontext.analysis.audio import analyze_series
from musiccontext.errors import InvalidPathError
from musiccontext.schemas import AlignmentPlan, DuckingSpec, SpeechSegment
from musiccontext.sync.editor import RenderPlan, probe_duration, render

pytestmark = pytest.mark.ffmpeg

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("_mcfixtures_sync", ROOT / "scripts" / "make_fixtures.py")
fixtures_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures_mod)

SPEECH = [SpeechSegment(start_time=2.0, end_time=8.0), SpeechSegment(start_time=13.0, end_time=19.0)]


def _tone(path: Path, seconds: float, freq: float = 220.0, amp: float = 0.5, sr: int = 48000) -> Path:
    """A plainly audible music bed, so the ducking measurement is not fighting the noise floor."""
    t = np.arange(int(sr * seconds)) / sr
    fixtures_mod.write_wav(path, (amp * np.sin(2 * np.pi * freq * t)).astype("float32"), sr)
    return path


def _region_db(series, start: float, end: float) -> float:
    return float(np.mean(series.rms_db[int(start * series.fps) : int(end * series.fps)]))


def test_short_music_loops_to_cover_the_video(demo_video, tmp_path, settings):
    before = (demo_video.stat().st_mtime_ns, demo_video.stat().st_size)
    plan = RenderPlan(
        video=demo_video,
        music=_tone(tmp_path / "bed.wav", 3.0),
        output=tmp_path / "out.mp4",
        alignment=AlignmentPlan(loop=True, fade_in=1.0, fade_out=2.0, basis="estimated"),
    )
    res = render(plan, settings)

    assert res.output.exists()
    assert res.duration == pytest.approx(24.0, abs=0.25)
    assert res.duration == pytest.approx(res.expected_duration, abs=0.25)
    assert probe_duration(res.output, settings) == pytest.approx(24.0, abs=0.25)
    assert res.audio_streams == 1
    assert any("aloop=" in f for f in res.filters)
    assert any("afade=t=in" in f for f in res.filters)
    assert any("afade=t=out" in f for f in res.filters)
    assert any("normalize=0" in f for f in res.filters)  # the dialogue is not attenuated by amix
    assert any("looped the track" in a for a in res.applied)
    assert res.warnings == []
    # the inputs are read-only
    assert (demo_video.stat().st_mtime_ns, demo_video.stat().st_size) == before
    assert not list(tmp_path.glob("*.part-*"))


def test_refuses_to_overwrite_without_force(demo_video, click_wav, tmp_path, settings):
    out = tmp_path / "twice.mp4"
    first = render(RenderPlan(video=demo_video, music=click_wav, output=out, alignment=AlignmentPlan()), settings)
    stamp = (out.stat().st_mtime_ns, out.stat().st_size)

    with pytest.raises(InvalidPathError) as e:
        render(RenderPlan(video=demo_video, music=click_wav, output=out, alignment=AlignmentPlan()), settings)
    assert "--force" in (e.value.hint or "")
    assert (out.stat().st_mtime_ns, out.stat().st_size) == stamp

    again = render(RenderPlan(video=demo_video, music=click_wav, output=out, alignment=AlignmentPlan(), force=True), settings)
    assert again.duration == pytest.approx(first.duration, abs=0.25)


def test_refuses_to_write_over_an_input(demo_video, click_wav, tmp_path, settings):
    for target in (demo_video, click_wav):
        with pytest.raises(InvalidPathError) as e:
            render(RenderPlan(video=demo_video, music=click_wav, output=target, alignment=AlignmentPlan(), force=True), settings)
        assert "overwrite an input" in e.value.message


def test_video_without_an_audio_stream_gets_music_only(silent_video, tmp_path, settings):
    plan = RenderPlan(video=silent_video, music=_tone(tmp_path / "bed2.wav", 20.0), output=tmp_path / "silent.mp4", alignment=AlignmentPlan())
    res = render(plan, settings)

    assert res.audio_streams == 1
    assert res.duration == pytest.approx(12.0, abs=0.25)
    assert not any("amix" in f for f in res.filters)
    assert any("no audio stream" in a for a in res.applied)


def test_ducking_measurably_lowers_the_music_under_speech(demo_video, tmp_path, settings):
    music = _tone(tmp_path / "bed3.wav", 30.0)
    spec = DuckingSpec(enabled=True, duck_depth_db=14.0, attack_ms=200, release_ms=400)
    res = render(
        RenderPlan(video=demo_video, music=music, output=tmp_path / "duck.mp4", alignment=AlignmentPlan(), ducking=spec, speech=SPEECH),
        settings,
    )
    assert res.ducking_applied
    assert any("volume=" in f and "eval=frame" in f for f in res.filters)
    assert any("music ducked 14.0 dB during 1.8-8.3s" == a for a in res.applied)

    series = analyze_series(res.output, settings)
    clean = _region_db(series, 9.5, 12.0)      # between the two voiceover blocks
    ducked = _region_db(series, 4.0, 6.0)      # inside the first voiceover block
    assert clean - ducked > 8.0, f"ducking only measured {clean - ducked:.2f} dB (clean {clean:.2f}, ducked {ducked:.2f})"

    flat = render(
        RenderPlan(video=demo_video, music=music, output=tmp_path / "flat.mp4", alignment=AlignmentPlan()),
        settings,
    )
    assert not flat.ducking_applied
    unducked = analyze_series(flat.output, settings)
    assert abs(_region_db(unducked, 9.5, 12.0) - _region_db(unducked, 4.0, 6.0)) < 3.0


def test_dry_run_builds_a_command_without_writing(demo_video, click_wav, tmp_path, settings):
    out = tmp_path / "dry.mp4"
    res = render(RenderPlan(video=demo_video, music=click_wav, output=out, alignment=AlignmentPlan(fade_in=1.0)), settings, dry_run=True)

    assert "-c:v" in res.command
    assert "-filter_complex" in res.command
    assert res.duration == 0.0
    assert res.expected_duration == pytest.approx(24.0, abs=0.25)
    assert res.filters
    assert "ffmpeg" in res.command_display()
    assert not out.exists()
    assert not list(tmp_path.glob("*.mp4")) and not list(tmp_path.glob("*.part-*"))
