"""The library is the offline half of MusicContext, so these tests exercise real audio end to end:
real ffprobe, real tempo estimation, a real SQLite index. No mocks, no network.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from conftest import needs_ffmpeg

from musiccontext.errors import ArtifactError
from musiccontext.music import library
from musiccontext.providers.base import SearchQuery

pytestmark = needs_ffmpeg


@pytest.fixture
def lib_dir(tmp_path, fx_dir) -> Path:
    """A private copy of the click fixtures: tests write sidecars and duplicates into it."""
    d = tmp_path / "music"
    d.mkdir()
    for name in ("click.wav", "click90.wav"):
        shutil.copy(fx_dir / name, d / name)
    return d


def _by_name(settings) -> dict[str, object]:
    return {Path(t.uri).name: t for t in library.list_tracks(settings)}


def _found(settings, **kw) -> list[str]:
    return [Path(t.uri).name for t in library.search_tracks(settings, SearchQuery(**kw))]


def test_scan_measures_bpm_then_reports_unchanged(settings, lib_dir):
    res = library.scan_directory(settings, lib_dir)
    assert sorted(Path(p).name for p in res.added) == ["click.wav", "click90.wav"]
    assert res.errors == []

    tracks = _by_name(settings)
    assert tracks["click.wav"].features.bpm.value == pytest.approx(120.0, abs=3.0)
    assert tracks["click90.wav"].features.bpm.value == pytest.approx(90.0, abs=3.0)
    # the basis travels with the number
    assert tracks["click.wav"].features.bpm.source == "estimated"
    assert 0.0 < tracks["click.wav"].features.bpm.confidence <= 1.0

    again = library.scan_directory(settings, lib_dir)
    assert again.added == []
    assert sorted(Path(p).name for p in again.unchanged) == ["click.wav", "click90.wav"]

    forced = library.scan_directory(settings, lib_dir, force=True)
    assert sorted(Path(p).name for p in forced.updated) == ["click.wav", "click90.wav"]
    assert library.library_stats(settings)["count"] == 2


def test_non_audio_skipped_corrupt_audio_does_not_abort(settings, lib_dir):
    (lib_dir / "notes.txt").write_text("liner notes, not music")
    (lib_dir / "broken.wav").write_bytes(b"this is not a wave file at all" * 50)

    res = library.scan_directory(settings, lib_dir)

    assert sorted(Path(p).name for p in res.added) == ["click.wav", "click90.wav"]
    assert [Path(p).name for p, _ in res.errors] == ["broken.wav"]
    assert "notes.txt" not in str(res.errors)
    assert res.errors[0][1]  # the failure carries a reason, not an empty string
    assert library.library_stats(settings)["count"] == 2


def test_duplicate_content_is_reported_not_indexed_twice(settings, lib_dir):
    shutil.copy(lib_dir / "click.wav", lib_dir / "click_copy.wav")

    res = library.scan_directory(settings, lib_dir)

    assert [Path(p).name for p in res.duplicates] == ["click_copy.wav"]
    assert sorted(Path(p).name for p in res.added) == ["click.wav", "click90.wav"]
    assert len(library.list_tracks(settings)) == 2


def test_search_hard_filters(settings, lib_dir):
    library.scan_directory(settings, lib_dir)
    library.add_track(settings, lib_dir / "click.wav", tags=["electronic", "driving"], force=True)

    assert _found(settings, bpm_range=(115, 125)) == ["click.wav"]
    assert _found(settings, bpm_range=(85, 95)) == ["click90.wav"]
    # half/double time is the same groove: 120 answers a 240 or a 60 BPM brief
    assert _found(settings, bpm_range=(235, 245)) == ["click.wav"]
    assert _found(settings, bpm_range=(55, 65), target_bpm=60) == ["click.wav"]

    assert _found(settings, tags_none=["driving"]) == ["click90.wav"]
    assert _found(settings, min_duration=25.0) == ["click.wav"]
    assert _found(settings, bpm_range=(300, 320)) == []


def test_search_scores_tag_and_tempo_matches_first(settings, lib_dir):
    library.scan_directory(settings, lib_dir)
    library.add_track(settings, lib_dir / "click90.wav", tags=["downtempo"], force=True)

    assert _found(settings, tags_any=["downtempo"])[0] == "click90.wav"
    assert _found(settings, target_bpm=120.0)[0] == "click.wav"


def test_license_is_unverified_until_a_sidecar_says_otherwise(settings, lib_dir):
    library.scan_directory(settings, lib_dir)
    lic = _by_name(settings)["click.wav"].license
    assert lic.verified is False
    assert lic.commercial_use == "unknown"

    (lib_dir / "click.wav.license.json").write_text(
        json.dumps({"license": "CC0-1.0", "commercial_use": True, "attribution_required": False})
    )
    candidate = library.add_track(settings, lib_dir / "click.wav", force=True)

    assert candidate.license.verified is True
    assert candidate.license.commercial_use == "yes"
    assert candidate.license.allows_commercial() is True
    assert library.library_stats(settings)["with_verified_license"] == 1


def test_library_stats_and_row_lifecycle(settings, lib_dir):
    library.scan_directory(settings, lib_dir)
    library.add_track(settings, lib_dir / "click.wav", tags=["electronic"], force=True)

    stats = library.library_stats(settings)
    assert stats["count"] == 2
    assert stats["with_bpm"] == 2
    assert stats["with_verified_license"] == 0
    assert stats["total_duration"] == pytest.approx(49.8, abs=1.0)
    assert stats["top_tags"]["electronic"] == 1

    track = _by_name(settings)["click.wav"]
    assert library.get_track(settings, track.id).id == track.id
    assert track.id == track.metadata["content_hash"][:16]
    assert library.remove_track(settings, track.id) is True
    assert library.remove_track(settings, track.id) is False
    assert library.get_track(settings, track.id) is None
    assert library.library_stats(settings)["count"] == 1
    assert (lib_dir / "click.wav").exists()  # removing a row never touches the audio


def test_progress_callback_and_non_recursive_scan(settings, lib_dir):
    nested = lib_dir / "deeper"
    nested.mkdir()
    shutil.copy(lib_dir / "click90.wav", nested / "nested.wav")
    (lib_dir / "click90.wav").unlink()

    seen: list[str] = []
    res = library.scan_directory(settings, lib_dir, recursive=False, progress=lambda p: seen.append(p.name))

    assert seen == ["click.wav"]
    assert len(res.added) == 1
    assert len(library.scan_directory(settings, lib_dir, recursive=True).added) == 1


def test_future_schema_version_is_reported_not_crashed(settings, lib_dir):
    library.add_track(settings, lib_dir / "click.wav")
    conn = sqlite3.connect(settings.library_db)
    conn.execute(f"PRAGMA user_version = {library.SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()

    with pytest.raises(ArtifactError) as err:
        library.list_tracks(settings)
    assert "schema version" in str(err.value)
    assert err.value.hint and "library scan" in err.value.hint
