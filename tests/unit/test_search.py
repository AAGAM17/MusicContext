"""Aggregated search must degrade gracefully: an empty library, a credential-less provider or a
provider that throws all have to produce an answer plus an explanation, never an exception.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import needs_ffmpeg

from musiccontext.music import library
from musiccontext.music.search import search_music
from musiccontext.providers import registry
from musiccontext.providers.base import MusicSearchProvider, SearchQuery
from musiccontext.schemas import ProviderCapability

pytestmark = needs_ffmpeg


class BoomProvider(MusicSearchProvider):
    """Deliberately broken: available, then raises the moment it is asked for anything."""

    name = "boom"
    kinds = ("search",)

    def capability(self, settings) -> ProviderCapability:
        return ProviderCapability(name=self.name, kinds=["search"], available=True, notes="test double")

    def search(self, query, settings, limit: int = 20):
        raise RuntimeError("upstream returned 500 for token=abcdef1234567890")

    def get_track(self, track_id, settings):
        return None

    def download(self, candidate, dest, settings) -> Path:
        raise RuntimeError("nope")


@pytest.fixture
def lib_dir(tmp_path, fx_dir) -> Path:
    d = tmp_path / "music"
    d.mkdir()
    for name in ("click.wav", "click90.wav"):
        shutil.copy(fx_dir / name, d / name)
    return d


def test_search_music_returns_library_candidates(settings, lib_dir):
    library.scan_directory(settings, lib_dir)

    out = search_music(settings, SearchQuery(bpm_range=(115, 125)))

    assert [c.provider for c in out.candidates] == ["local"]
    assert Path(out.candidates[0].uri).name == "click.wav"
    assert out.by_provider == {"local": 1}
    assert out.warnings == []


def test_limit_truncates_and_counts_match(settings, lib_dir):
    library.scan_directory(settings, lib_dir)

    out = search_music(settings, SearchQuery(), limit=1)

    assert len(out.candidates) == 1
    assert out.by_provider["local"] == 1


def test_local_only_still_returns_local_music_and_says_so(settings, lib_dir):
    library.scan_directory(settings, lib_dir)
    settings.local_only = True

    out = search_music(settings, SearchQuery())

    assert len(out.candidates) == 2
    assert all(c.provider == "local" for c in out.candidates)
    assert any("local_only" in m for m in out.messages)

    # the explicit flag reaches the same conclusion on a settings object that does not set it
    settings.local_only = False
    assert any("local_only" in m for m in search_music(settings, SearchQuery(), local_only=True).messages)


def test_empty_library_explains_the_next_step(settings):
    out = search_music(settings, SearchQuery(text="warm piano"))

    assert out.candidates == []
    assert out.warnings == []
    assert any("library scan" in m for m in out.messages)


def test_broken_provider_becomes_a_warning(settings, lib_dir):
    library.scan_directory(settings, lib_dir)
    registry.register_provider(BoomProvider(), replace=True)
    try:
        out = search_music(settings, SearchQuery())
    finally:
        registry.unregister_provider("boom")

    assert [c.provider for c in out.candidates] == ["local", "local"]
    assert len(out.warnings) == 1
    assert "boom" in out.warnings[0]
    assert "abcdef1234567890" not in out.warnings[0]  # redacted on the way out
    assert out.by_provider == {"local": 2}


def test_unknown_requested_provider_is_a_message(settings, lib_dir):
    library.scan_directory(settings, lib_dir)

    out = search_music(settings, SearchQuery(), providers=["nope"])

    assert out.candidates == []
    assert any("Unknown provider" in m for m in out.messages)
