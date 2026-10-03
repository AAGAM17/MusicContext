"""Cache integrity: a stale or tampered artifact must never be served as current.

The cache is content-addressed, but the key alone is not trusted on read: every entry
stores its own key material and that material is re-verified. These tests break the
material on disk and assert the entry is dropped rather than returned.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from musiccontext.engine import cache as cache_mod
from musiccontext.engine.cache import Cache, make_key

VALUE = {"duration": 24.0, "scenes": 4}


@pytest.fixture
def cache(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    return Cache(root)


def test_same_inputs_give_the_same_key():
    a, mat_a = make_key("analysis", "hash1", {"cut_threshold": 0.28})
    b, mat_b = make_key("analysis", "hash1", {"cut_threshold": 0.28})
    assert a == b and mat_a == mat_b


@pytest.mark.parametrize(
    ("step", "content", "config", "extra"),
    [
        ("analysis", "hash1", {"cut_threshold": 0.35}, None),   # config differs
        ("analysis", "hash2", {"cut_threshold": 0.28}, None),   # content differs
        ("music", "hash1", {"cut_threshold": 0.28}, None),      # step differs
        ("analysis", "hash1", {"cut_threshold": 0.28}, {"transcript": "abc"}),
    ],
)
def test_any_input_change_changes_the_key(step, content, config, extra):
    base, _ = make_key("analysis", "hash1", {"cut_threshold": 0.28})
    other, _ = make_key(step, content, config, extra)
    assert other != base


def test_key_material_binds_the_tool_and_analysis_versions():
    _, material = make_key("analysis", "hash1", {"cut_threshold": 0.28})
    assert material["tool_version"] == cache_mod.__version__
    assert material["analysis_version"] == cache_mod.ANALYSIS_VERSION


def test_a_new_tool_version_changes_the_key(monkeypatch):
    base, _ = make_key("analysis", "hash1")
    monkeypatch.setattr(cache_mod, "__version__", "99.0.0")
    assert make_key("analysis", "hash1")[0] != base


def test_roundtrip_hit(cache):
    key, material = make_key("analysis", "hash1", {"cut_threshold": 0.28})
    cache.put(key, material, VALUE)
    assert cache.get(key, material) == VALUE


def test_tampered_material_on_disk_is_never_served(cache):
    key, material = make_key("analysis", "hash1", {"cut_threshold": 0.28})
    cache.put(key, material, VALUE)
    path = cache._path(key)
    entry = json.loads(path.read_text())
    entry["material"]["tool_version"] = "99.0.0"  # an artifact from an incompatible build
    path.write_text(json.dumps(entry))
    assert cache.get(key, material) is None


def test_material_mismatch_is_refused_even_when_the_key_matches(cache, monkeypatch):
    """A key collision (or a hand-crafted filename) must not get past the material check."""
    key, material = make_key("analysis", "hash1", {"cut_threshold": 0.28})
    cache.put(key, material, VALUE)
    monkeypatch.setattr(cache_mod, "__version__", "99.0.0")
    _, newer_material = make_key("analysis", "hash1", {"cut_threshold": 0.28})
    assert cache.get(key, newer_material) is None


def test_corrupt_cache_file_returns_none_instead_of_raising(cache):
    key, material = make_key("analysis", "hash1")
    cache.put(key, material, VALUE)
    cache._path(key).write_text("{not json at all")
    assert cache.get(key, material) is None


def test_missing_entry_returns_none(cache):
    key, material = make_key("analysis", "never-written")
    assert cache.get(key, material) is None


def test_disabled_cache_never_reads_or_writes(tmp_path):
    c = Cache(tmp_path / "cache", enabled=False)
    key, material = make_key("analysis", "hash1")
    c.put(key, material, VALUE)
    assert c.get(key, material) is None
    assert not (tmp_path / "cache").exists()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores mode bits")
def test_unwritable_cache_directory_does_not_break_put(cache):
    """A read-only cache (shared image, mounted volume) must degrade, not crash."""
    key, material = make_key("analysis", "unwritable-entry")
    original = stat.S_IMODE(cache.root.stat().st_mode)
    cache.root.chmod(0o500)
    try:
        cache.put(key, material, VALUE)  # must not raise
        assert cache.get(key, material) is None
    finally:
        cache.root.chmod(original)
