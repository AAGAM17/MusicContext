"""Shared deterministic fixtures. No network, no external assets: everything is synthesized by FFmpeg/numpy."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_spec = importlib.util.spec_from_file_location("_mcfixtures", ROOT / "scripts" / "make_fixtures.py")
fixtures_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures_mod)  # type: ignore[union-attr]

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")

# The fixture video's ground truth, asserted by tests.
SCENE_BOUNDS = fixtures_mod.SCENE_BOUNDS          # [0, 5, 11, 17, 24]
SPEECH_INTERVALS = fixtures_mod.SPEECH_INTERVALS  # [(2.0, 8.0), (13.0, 19.0)]
DEMO_DURATION = 24.0
CLICK_BPM = 120.0


@pytest.fixture(scope="session")
def fx_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("fixtures")
    if HAS_FFMPEG:
        fixtures_mod.make_demo_video(d / "demo.mp4")
        fixtures_mod.make_demo_video(d / "silent.mp4", with_voice=False, duration=12.0)
    fixtures_mod.make_click(d / "click.wav")
    fixtures_mod.make_click(d / "click90.wav", bpm=90.0, duration=20.0)
    fixtures_mod.write_wav(d / "quiet.wav", fixtures_mod.np.zeros(int(16000 * 5), "float32"), 16000)
    return d


@pytest.fixture
def demo_video(fx_dir) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    return fx_dir / "demo.mp4"


@pytest.fixture
def silent_video(fx_dir) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    return fx_dir / "silent.mp4"


@pytest.fixture
def click_wav(fx_dir) -> Path:
    return fx_dir / "click.wav"


@pytest.fixture
def tmp_home(tmp_path, monkeypatch) -> Path:
    """Isolated MUSICCONTEXT_HOME + cache + config so tests never touch the user's data."""
    home = tmp_path / "mchome"
    home.mkdir()
    monkeypatch.setenv("MUSICCONTEXT_HOME", str(home))
    monkeypatch.setenv("MUSICCONTEXT_CACHE_DIR", str(tmp_path / "mccache"))
    monkeypatch.setenv("MUSICCONTEXT_CONFIG", str(tmp_path / "noconfig.toml"))
    for k in list(__import__("os").environ):
        if k.startswith("MUSICCONTEXT_") and k.endswith("_API_KEY"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("MUSICCONTEXT_PROVIDER", raising=False)
    monkeypatch.delenv("MUSICCONTEXT_ALLOW_REMOTE", raising=False)
    return home


@pytest.fixture
def settings(tmp_home):
    from musiccontext.engine.context import load_settings

    return load_settings()


@pytest.fixture
def analysis(demo_video, settings):
    """A real AnalysisReport for the fixture video (cached per test session by content hash)."""
    from musiccontext.engine.pipeline import analyze_video

    rep, _ = analyze_video(demo_video, settings)
    return rep


@pytest.fixture
def direction(analysis):
    from musiccontext.direction.planner import create_plan

    return create_plan(analysis).directions[0]


@pytest.fixture
def plan(analysis):
    from musiccontext.direction.planner import create_plan

    return create_plan(analysis)


def make_candidate(cid="t1", provider="local", bpm=110.0, duration=30.0, tags=(), vocals=None, uri=None, **kw):
    """Build a MusicCandidate with plausible features for scoring tests."""
    from musiccontext.schemas import Measured, MusicCandidate, MusicFeatures

    feats = MusicFeatures(
        duration=duration,
        bpm=Measured(value=bpm, source="estimated", confidence=0.8) if bpm else None,
        beats=[i * 60.0 / bpm for i in range(int(duration * bpm / 60))] if bpm else [],
        downbeats=[i * 4 * 60.0 / bpm for i in range(int(duration * bpm / 240))] if bpm else [],
        energy_curve=[(float(i), 0.5) for i in range(int(duration))],
        band_ratios={"low": 0.4, "mid": 0.35, "high": 0.25},
        has_vocals=vocals,
        **kw.pop("features_kw", {}),
    )
    return MusicCandidate(id=cid, provider=provider, title=kw.pop("title", f"Track {cid}"), tags=list(tags), features=feats, uri=uri, **kw)
