"""The procedural generator must honour the brief it is given, not merely claim to.

Every assertion here is measured from the written WAV with the same analyzer MusicContext runs on
third-party audio: tempo, duration, downbeat placement, level. If a tempo assertion fails, the
synth's beat grid is wrong — the tolerance is the point.
"""

from __future__ import annotations

import shutil
import wave

import pytest

from musiccontext.errors import InvalidArgumentError
from musiccontext.music.features import analyze_music
from musiccontext.music.generation import build_request, generate_music, generation_report
from musiccontext.providers.generation import ProceduralGenerationProvider
from musiccontext.schemas import MusicGenerationRequest

# analyze_music decodes through ffmpeg, so every generation here needs it.
pytestmark = [
    pytest.mark.ffmpeg,
    pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not on PATH"),
]

BPM_TOLERANCE = 2.5
PEAK_TOLERANCE_MS = 120.0


def make_req(duration=20.0, bpm=110.0, seed=7, peaks=(), **kw):
    kw.setdefault("key", "A minor")
    kw.setdefault("structure", ["intro", "build", "peak", "resolution"])
    kw.setdefault("energy_curve", [(0.0, 0.3), (duration * 0.45, 0.65), (duration * 0.8, 0.95), (duration, 0.4)])
    return MusicGenerationRequest(
        prompt="warm instrumental bed that lifts into a confident peak",
        duration=duration, bpm=bpm, seed=seed, peak_times=list(peaks), **kw,
    )


@pytest.fixture
def provider():
    return ProceduralGenerationProvider()


def test_always_available_without_configuration(provider, settings):
    cap = provider.capability(settings)
    assert cap.available is True
    assert cap.requires_credentials is False
    assert cap.sends_media_off_device is False
    assert cap.kinds == ["generation"]


def test_same_seed_is_byte_identical_and_different_seeds_differ(provider, settings, tmp_path):
    req = make_req(duration=12.0, bpm=100.0, seed=1234)
    a = provider.generate(req, settings, tmp_path / "a.wav").artifact_path
    b = provider.generate(req, settings, tmp_path / "b.wav").artifact_path
    assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()
    assert a != b

    other = provider.generate(make_req(duration=12.0, bpm=100.0, seed=99), settings, tmp_path / "c.wav")
    assert (tmp_path / "c.wav").read_bytes() != (tmp_path / "a.wav").read_bytes()
    assert other.track_id != provider.generate(req, settings, tmp_path / "d.wav").track_id


@pytest.mark.parametrize("bpm", [90.0, 128.0])
def test_measured_tempo_matches_the_request(provider, settings, tmp_path, bpm):
    """The honesty check: the rendered beat grid must be measurable at the requested tempo."""
    result = provider.generate(make_req(duration=20.0, bpm=bpm, seed=5), settings, tmp_path / f"t{bpm}.wav")
    feats = result.candidate.features
    assert feats is not None and feats.bpm is not None, f"no tempo measured at {bpm} BPM"
    assert abs(feats.bpm.value - bpm) <= BPM_TOLERANCE, f"requested {bpm}, measured {feats.bpm.value}"
    assert feats.bpm.confidence > 0.3
    assert len(feats.beats) >= int(20.0 * bpm / 60.0) - 2


def test_duration_and_wav_format(provider, settings, tmp_path):
    dest = tmp_path / "fmt.wav"
    result = provider.generate(make_req(duration=15.0, bpm=112.0, seed=3), settings, dest)
    with wave.open(str(dest), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 44100
        frames = w.getnframes()
    assert abs(frames / 44100 - 15.0) <= 0.15
    assert abs(result.duration - 15.0) <= 0.15
    assert result.candidate.features is not None
    assert abs(result.candidate.features.duration - 15.0) <= 0.15


def test_level_is_normalised_below_full_scale(provider, settings, tmp_path):
    import numpy as np

    dest = tmp_path / "level.wav"
    provider.generate(make_req(duration=10.0, bpm=120.0, seed=11), settings, dest)
    with wave.open(str(dest), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(pcm)))
    assert 0.5 < peak < 1.0, f"peak {peak} should be hot but not clipped"
    assert peak == pytest.approx(10 ** (-1 / 20), abs=0.01)  # -1 dBFS


@pytest.mark.parametrize(("bpm", "peak"), [(90.0, 7.3), (128.0, 9.1)])
def test_requested_peak_lands_on_a_measured_downbeat(provider, settings, tmp_path, bpm, peak):
    """The bar grid is phased so the first requested peak sits exactly on a bar line.

    Only the *first* peak can: at a constant tempo the bars after it are fixed, so later peaks are
    snapped to the nearest downbeat and the offset is reported in the result metadata.
    """
    result = provider.generate(make_req(duration=22.0, bpm=bpm, seed=8, peaks=[peak]), settings, tmp_path / f"p{bpm}.wav")
    feats = result.candidate.features
    assert feats is not None and feats.downbeats, "no downbeats measured"
    nearest = min(feats.downbeats, key=lambda d: abs(d - peak))
    err_ms = abs(nearest - peak) * 1000
    assert err_ms <= PEAK_TOLERANCE_MS, f"peak {peak}s landed {err_ms:.0f} ms from measured downbeat {nearest}s"
    assert result.metadata["peaks_on_grid"] == f"{peak:.3f}"


def test_second_peak_is_snapped_to_the_nearest_bar_and_says_so(provider, settings, tmp_path):
    result = provider.generate(make_req(duration=24.0, bpm=120.0, seed=2, peaks=[6.0, 15.4]), settings, tmp_path / "two.wav")
    assert "peak_note" in result.metadata
    offsets = [abs(float(x)) for x in result.metadata["peak_offsets_ms"].split(",")]
    assert offsets[0] < 1.0  # the first peak is exact
    assert offsets[1] <= 1000.0  # the rest land within half a bar (2 s at 120 BPM)


def test_license_and_provenance_are_honest(provider, settings, tmp_path):
    result = provider.generate(make_req(duration=10.0, bpm=100.0, seed=4242), settings, tmp_path / "lic.wav")
    lic = result.license
    assert lic is not None and lic.verified is True
    assert lic.commercial_use == "yes"
    assert lic.allows_commercial() is True
    assert lic.provider == "procedural"
    assert lic.attribution_required is False
    assert lic.verified_by == "generated locally by MusicContext"
    assert "4242" in result.provenance
    assert "synthesized from scratch" in result.provenance.lower()
    assert result.candidate.license == lic


def test_vocals_request_is_answered_not_ignored(provider, settings, tmp_path):
    result = provider.generate(make_req(duration=8.0, bpm=100.0, vocals="preferred"), settings, tmp_path / "v.wav")
    assert "instrumental" in result.metadata["vocals_note"]
    plain = provider.generate(make_req(duration=8.0, bpm=100.0), settings, tmp_path / "v2.wav")
    assert "vocals_note" not in plain.metadata


def test_key_is_honoured(provider, settings, tmp_path):
    result = provider.generate(make_req(duration=16.0, bpm=110.0, key="F# minor", seed=6), settings, tmp_path / "k.wav")
    assert result.metadata["key"] == "F# minor"
    assert result.candidate.features is not None and result.candidate.features.key


def test_default_destination_is_under_settings_home(provider, settings):
    result = provider.generate(make_req(duration=6.0, bpm=100.0), settings)
    path = __import__("pathlib").Path(result.artifact_path)
    assert path.is_file()
    assert path.parent == settings.home / "generated"
    assert path.name.startswith("procedural-proc-")


# --- build_request -----------------------------------------------------------------


def test_build_request_maps_the_direction(direction, analysis):
    from musiccontext.timeline import markers as M

    ms = [
        M.make(7.5, "reveal", "product reveal"),            # importance 0.95, action peak
        M.make(3.0, "hard_cut", "cut"),                     # importance 0.30, action none
        M.make(12.0, "title", "title card"),                # importance 0.70, action hit
    ]
    req = build_request(direction, analysis, seed=31, markers=ms)
    assert req.prompt == direction.generation_prompt[:2000]
    assert req.duration == pytest.approx(analysis.metadata.duration, abs=0.01)
    assert req.bpm == direction.tempo.target_bpm
    assert req.structure == [s.label for s in direction.arc.sections]
    assert req.energy_curve == [(float(t), float(e)) for t, e in direction.arc.energy_curve]
    assert req.seed == 31
    assert req.vocals == direction.vocals
    if direction.vocals == "none":
        assert "vocals" in req.negative_prompt
    assert 7.5 in req.peak_times
    assert 12.0 in req.peak_times
    assert 3.0 not in req.peak_times


def test_build_request_falls_back_to_the_arc_peak(direction, analysis):
    req = build_request(direction, analysis, markers=[])
    if direction.arc.peak_time is not None:
        assert req.peak_times == [round(direction.arc.peak_time, 3)]


def test_build_request_needs_a_duration(direction):
    class NoVideo:  # an analysis that cannot say how long the video is
        pass

    with pytest.raises(InvalidArgumentError):
        build_request(direction, NoVideo())


def test_explicit_duration_wins(direction, analysis):
    assert build_request(direction, analysis, duration=42.0).duration == 42.0


# --- end to end --------------------------------------------------------------------


def test_generate_music_end_to_end(settings, direction, analysis, tmp_path):
    dest = tmp_path / "out.wav"
    result = generate_music(settings, direction, analysis, provider="procedural", dest=dest, duration=18.0, seed=77)
    assert result.provider == "procedural"
    assert result.status == "completed"
    assert dest.is_file()

    request = build_request(direction, analysis, duration=18.0, seed=77)
    report = generation_report(result, request)
    assert report["provider"] == "procedural"
    assert report["license"]["verified"] is True
    assert report["license"]["commercial_use"] == "yes"
    assert report["bpm"]["requested"] == request.bpm
    assert report["bpm"]["measured_source"] == "estimated"
    assert abs(report["bpm"]["delta"]) <= BPM_TOLERANCE
    assert report["artifact_path"] == str(dest)
    assert report["downbeats_measured"] > 0
    for peak in report["peaks"]:
        assert peak["nearest_measured_downbeat"] is not None
    # the report quotes measured features, so re-analyzing the artifact must agree
    again = analyze_music(dest, settings, loudness=False)
    assert again.bpm is not None
    assert abs(again.bpm.value - report["bpm"]["measured"]) < 0.01


def test_generate_music_refuses_to_clobber_without_force(settings, direction, analysis, tmp_path):
    from musiccontext.errors import InvalidPathError

    dest = tmp_path / "exists.wav"
    dest.write_bytes(b"keep me")
    with pytest.raises(InvalidPathError):
        generate_music(settings, direction, analysis, provider="procedural", dest=dest, duration=6.0)
    assert dest.read_bytes() == b"keep me"
    generate_music(settings, direction, analysis, provider="procedural", dest=dest, duration=6.0, force=True)
    assert dest.read_bytes()[:4] == b"RIFF"
