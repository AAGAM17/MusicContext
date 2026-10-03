"""CLI contract: exit codes, --json shape, actionable errors, and non-destructive defaults.

Driven through a real subprocess, so this also proves the console entry point is wired up.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def run(tmp_path):
    env = {**os.environ, "MUSICCONTEXT_HOME": str(tmp_path / "home"), "MUSICCONTEXT_CACHE_DIR": str(tmp_path / "cache"),
           "MUSICCONTEXT_CONFIG": str(tmp_path / "config.toml"), "PYTHONPATH": str(ROOT / "src"), "NO_COLOR": "1"}
    for k in list(env):
        if k.startswith("MUSICCONTEXT_") and k.endswith("_API_KEY"):
            env.pop(k)

    def _run(*args, expect=0, timeout=300):
        cp = subprocess.run([sys.executable, "-m", "musiccontext", *map(str, args)], capture_output=True, text=True,
                            timeout=timeout, cwd=ROOT, env=env)
        if expect is not None:
            assert cp.returncode == expect, f"exit {cp.returncode} for {args}\nSTDOUT {cp.stdout[-2500:]}\nSTDERR {cp.stderr[-2500:]}"
        return cp

    return _run


def test_version_and_help(run):
    assert run("--version").stdout.strip().startswith("musiccontext ")
    out = run("--help").stdout
    for cmd in ("inspect", "analyze", "plan", "recommend", "search", "generate", "sync", "render", "export",
                "library", "providers", "config", "profile", "doctor", "init-agent", "mcp"):
        assert cmd in out, f"{cmd} missing from --help"
    assert run().stdout.startswith("usage:")  # no args prints help, exit 0


def test_providers_json_is_machine_readable(run):
    d = json.loads(run("providers", "--json").stdout)
    names = {p["name"] for p in d["providers"]}
    assert {"local", "procedural"} <= names
    for p in d["providers"]:
        assert set(p) >= {"name", "kinds", "available", "requires_credentials", "sends_media_off_device"}


def test_config_show_never_prints_a_secret(run, tmp_path):
    env_extra = {"MUSICCONTEXT_ACME_API_KEY": "supersecret-value-123456"}
    cp = subprocess.run([sys.executable, "-m", "musiccontext", "config", "show", "--json"], capture_output=True, text=True,
                        timeout=120, cwd=ROOT, env={**os.environ, **env_extra, "PYTHONPATH": str(ROOT / "src"),
                                                    "MUSICCONTEXT_HOME": str(tmp_path / "h"), "MUSICCONTEXT_CONFIG": str(tmp_path / "c.toml")})
    assert cp.returncode == 0
    assert "supersecret-value-123456" not in cp.stdout
    d = json.loads(cp.stdout)
    assert "MUSICCONTEXT_ACME_API_KEY" in d["credentials"]
    assert d["credentials"]["MUSICCONTEXT_ACME_API_KEY"] == "***redacted***"
    assert d["telemetry"].startswith("none")


def test_config_set_rejects_credentials_and_unknown_keys(run, tmp_path):
    cp = run("config", "set", "MUSICCONTEXT_API_KEY", "x", expect=2)
    assert "Cannot set" in cp.stderr
    assert "only ever read from environment variables" in cp.stderr
    run("config", "set", "provider", "local")
    cfg = Path(json.loads(run("config", "path", "--json").stdout)["config_file"])
    assert 'provider = "local"' in cfg.read_text()


def test_missing_file_is_an_actionable_error(run):
    cp = run("inspect", "no-such-file.mp4", expect=1)
    assert "not found" in cp.stderr.lower()
    assert "->" in cp.stderr, "errors must carry a hint"
    d = json.loads(run("inspect", "no-such-file.mp4", "--json", expect=1).stdout)
    assert d["error"]["code"] == "invalid_path" and d["error"]["hint"]


def test_bad_marker_and_preset_are_rejected_with_the_vocabulary(run):
    cp = run("plan", "x.mp4", "--marker", "nonsense", expect=2)
    assert "TIME=TYPE" in cp.stderr and "reveal" in cp.stderr
    cp = run("plan", "x.mp4", "--platform", "myspace", expect=2)
    assert "Available presets" in cp.stderr and "tiktok" in cp.stderr
    cp = run("plan", "x.mp4", "--vocals", "yodelling", expect=2)
    assert "invalid choice" in cp.stderr.lower()


def test_remote_url_is_refused_by_default(run):
    cp = run("inspect", "https://example.com/video.mp4", expect=1)
    assert "MUSICCONTEXT_ALLOW_REMOTE" in cp.stderr


def test_library_on_an_empty_library_is_not_an_error(run):
    d = json.loads(run("library", "stats", "--json").stdout)
    assert d["count"] == 0
    d = json.loads(run("library", "search", "cinematic", "--json").stdout)
    assert d["count"] == 0 and d["tracks"] == []


def test_doctor_reports_structured_checks(run):
    d = json.loads(run("doctor", "--json", expect=None).stdout)
    assert set(d) >= {"ok", "sections", "next"}
    assert {"Runtime", "Media tools", "Storage", "Providers", "Privacy", "Agent integrations"} <= set(d["sections"])
    for checks in d["sections"].values():
        for ch in checks:
            assert ch["status"] in ("ok", "warn", "fail", "info")
            assert ch["name"] and ch["detail"]


def test_init_agent_dry_run_changes_nothing(run, tmp_path):
    cp = subprocess.run([sys.executable, "-m", "musiccontext", "init-agent", "--project", "--mcp", "--dry-run", "--json"],
                        capture_output=True, text=True, timeout=120, cwd=tmp_path,
                        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "MUSICCONTEXT_HOME": str(tmp_path / "h")})
    assert cp.returncode == 0, cp.stderr
    d = json.loads(cp.stdout)
    assert d["dry_run"] is True
    assert all(a["action"].startswith("would") or a["action"] == "skipped" for a in d["actions"])
    assert not (tmp_path / ".claude").exists() and not (tmp_path / ".mcp.json").exists()


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_quiet_json_and_artifact_flow(run, demo_video, tmp_path):
    d = json.loads(run("inspect", demo_video, "--json").stdout)
    assert d["duration"] == pytest.approx(24.0, abs=0.2)

    out = tmp_path / "plan.musicctx"
    res = json.loads(run("plan", demo_video, "--marker", "11.0=reveal", "--json", "-o", str(out)).stdout)
    assert res["music_direction"]["arc"]["peak_time"] == pytest.approx(11.0, abs=0.01)
    assert out.is_file()

    # writing to the same path again must refuse, and --force must allow it
    cp = run("plan", demo_video, "--json", "-o", str(out), expect=1)
    assert json.loads(cp.stdout)["error"]["code"] in ("invalid_path", "artifact_error")
    run("plan", demo_video, "--json", "-o", str(out), "--force")

    summary = json.loads(run("export", str(out), "--json").stdout)
    assert {"video", "story", "direction", "markers"} <= set(summary)
    full = json.loads(run("export", str(out), "--full", "--json").stdout)
    assert "analysis" in full

    assert run("analyze", demo_video, "--quiet").stdout == ""


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_sync_requires_output_and_never_overwrites_an_input(run, demo_video, click_wav, tmp_path):
    cp = run("sync", "--video", demo_video, "--music", click_wav, expect=2)
    assert "--output is required" in cp.stderr

    cp = run("sync", "--video", demo_video, "--music", click_wav, "--output", str(demo_video), expect=1)
    assert "overwrite an input" in cp.stderr or "already exists" in cp.stderr

    out = tmp_path / "ok.mp4"
    d = json.loads(run("sync", "--video", demo_video, "--music", click_wav, "--output", str(out), "--json").stdout)
    assert out.is_file() and d["duration"] == pytest.approx(24.0, abs=0.25)


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_render_one_shot_generates_and_mixes(run, demo_video, tmp_path):
    out = tmp_path / "rendered.mp4"
    d = json.loads(run("render", demo_video, "--generate", "--seed", "3", "-o", str(out), "--json").stdout)
    assert out.is_file()
    assert d["duration"] == pytest.approx(24.0, abs=0.3)
    assert d["generated"]["provider"] == "procedural"
    assert d["generated"]["bpm"]["measured"] == pytest.approx(d["generated"]["bpm"]["requested"], abs=2.5)
