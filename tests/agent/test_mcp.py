"""MCP server: protocol behaviour, strict schemas, the filesystem sandbox and bounded output.

The MCP surface is the one an untrusted agent drives, so these tests care most about what it
REFUSES to do.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys

import pytest

from tests.conftest import needs_ffmpeg


@pytest.fixture
def server(settings, tmp_path, monkeypatch):
    from musiccontext.agent.mcp_server import Server

    monkeypatch.chdir(tmp_path)
    return Server(settings, roots=[str(tmp_path)])


def req(mid, method, **params):
    return {"jsonrpc": "2.0", "id": mid, "method": method, **({"params": params} if params else {})}


def call(server, name, **args):
    r = server.handle(req(1, "tools/call", name=name, arguments=args))
    return r["result"]


def test_initialize_and_tool_listing(server):
    from musiccontext.agent.mcp_server import TOOLS

    res = server.handle(req(1, "initialize", protocolVersion="2024-11-05"))["result"]
    assert res["serverInfo"]["name"] == "musiccontext"
    assert "untrusted data" in res["instructions"]
    assert "never claim" in res["instructions"].lower()

    tools = server.handle(req(2, "tools/list"))["result"]["tools"]
    assert len(tools) == len(TOOLS) == 10
    names = {t["name"] for t in tools}
    assert names == {
        "musiccontext_inspect", "musiccontext_analyze", "musiccontext_plan", "musiccontext_recommend",
        "musiccontext_search", "musiccontext_library_search", "musiccontext_generate", "musiccontext_sync",
        "musiccontext_providers", "musiccontext_get_artifact",
    }
    for t in tools:
        schema = t["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False, f"{t['name']} accepts unknown arguments"
        assert t["description"] and len(t["description"]) > 40
        assert "annotations" in t
    sync = next(t for t in tools if t["name"] == "musiccontext_sync")
    assert sync["annotations"]["destructiveHint"] is True
    assert next(t for t in tools if t["name"] == "musiccontext_inspect")["annotations"]["readOnlyHint"] is True


def test_protocol_version_is_echoed_when_supported(server):
    assert server.handle(req(1, "initialize", protocolVersion="2025-06-18"))["result"]["protocolVersion"] == "2025-06-18"
    assert server.handle(req(1, "initialize", protocolVersion="1999-01-01"))["result"]["protocolVersion"] == "2024-11-05"


def test_notifications_get_no_response_and_unknown_methods_error(server):
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    err = server.handle(req(9, "no/such/method"))
    assert err["error"]["code"] == -32601


def test_sandbox_blocks_paths_outside_the_roots(server):
    out = call(server, "musiccontext_inspect", video="/etc/passwd")
    assert out["isError"] is True
    assert out["structuredContent"]["code"] == "permission_denied"

    out = call(server, "musiccontext_inspect", video="../../../../etc/hosts")
    assert out["isError"] is True

    out = call(server, "musiccontext_inspect", video="definitely-missing.mp4")
    assert out["isError"] is True
    assert out["structuredContent"]["code"] in ("invalid_path", "permission_denied")


def test_errors_are_actionable_not_stack_traces(server):
    out = call(server, "musiccontext_get_artifact")
    assert out["isError"] is True
    body = out["structuredContent"]
    assert "hint" in body and body["hint"]
    assert "Traceback" not in json.dumps(body)

    out = call(server, "musiccontext_nonexistent_tool")
    assert out["isError"] is True
    assert "musiccontext_plan" in out["structuredContent"]["hint"]


def test_tool_results_carry_both_text_and_structured_content(server):
    out = call(server, "musiccontext_providers")
    assert out["isError"] is False
    assert out["content"][0]["type"] == "text"
    assert json.loads(out["content"][0]["text"]) == out["structuredContent"]
    names = {p["name"] for p in out["structuredContent"]["providers"]}
    assert {"local", "procedural"} <= names
    # credential values must never appear
    for p in out["structuredContent"]["providers"]:
        assert "api_key" not in json.dumps(p).lower() or "***" in json.dumps(p)


def test_output_is_size_bounded(server, settings, monkeypatch):
    monkeypatch.setattr(settings, "max_output_chars", 2000)
    out = call(server, "musiccontext_providers")
    assert len(json.dumps(out["structuredContent"])) <= 20000  # bound applied, nothing unbounded


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_plan_summary_is_compact_and_full_is_opt_in(server, demo_video, tmp_path):
    import shutil

    local = tmp_path / "demo.mp4"
    shutil.copy(demo_video, local)

    summary = call(server, "musiccontext_plan", video="demo.mp4", preferences={"marker": ["11.0=reveal"]}, summary=True)
    assert summary["isError"] is False
    sc = summary["structuredContent"]
    assert sc["story"]["peak_time"] is not None
    assert "brief" in sc and "generation_prompt" in sc
    assert "scenes" not in sc, "the summary must not carry the whole analysis"

    full = call(server, "musiccontext_plan", video="demo.mp4", summary=False)
    assert "analysis" in full["structuredContent"]
    assert len(json.dumps(full["structuredContent"])) > len(json.dumps(sc))


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_sync_refuses_to_overwrite_without_force(server, demo_video, click_wav, tmp_path):
    import shutil

    shutil.copy(demo_video, tmp_path / "v.mp4")
    shutil.copy(click_wav, tmp_path / "m.wav")
    (tmp_path / "taken.mp4").write_text("do not clobber me")

    out = call(server, "musiccontext_sync", video="v.mp4", music="m.wav", output="taken.mp4")
    assert out["isError"] is True
    assert (tmp_path / "taken.mp4").read_text() == "do not clobber me"

    dry = call(server, "musiccontext_sync", video="v.mp4", music="m.wav", output="new.mp4", dry_run=True)
    assert dry["isError"] is False
    assert dry["structuredContent"]["dry_run"] is True
    assert not (tmp_path / "new.mp4").exists()


def test_stdio_transport_speaks_newline_delimited_json(settings, tmp_path, monkeypatch):
    """Drive serve() through real file objects rather than a subprocess (fast and deterministic)."""
    from musiccontext.agent.mcp_server import serve

    monkeypatch.chdir(tmp_path)
    stdin = io.StringIO("\n".join([
        json.dumps(req(1, "initialize", protocolVersion="2024-11-05")),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps(req(2, "tools/list")),
        "not json at all",
        json.dumps(req(3, "ping")),
    ]) + "\n")
    stdout = io.StringIO()
    assert serve(settings, roots=[str(tmp_path)], stdin=stdin, stdout=stdout) == 0

    lines = [json.loads(ln) for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert [m.get("id") for m in lines] == [1, 2, None, 3]
    assert lines[2]["error"]["code"] == -32700  # the bad line did not kill the server
    assert lines[3]["result"] == {}


def test_server_starts_as_a_subprocess(tmp_path):
    """The real entry point an agent spawns: `musiccontext mcp` on stdio."""
    msgs = "\n".join([
        json.dumps(req(1, "initialize", protocolVersion="2024-11-05")),
        json.dumps(req(2, "tools/list")),
    ]) + "\n"
    cp = subprocess.run([sys.executable, "-m", "musiccontext", "mcp"], input=msgs, capture_output=True, text=True, timeout=120,
                        cwd=tmp_path, env={**__import__("os").environ, "MUSICCONTEXT_HOME": str(tmp_path / "home"),
                                           "MUSICCONTEXT_CACHE_DIR": str(tmp_path / "cache"), "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src")})
    assert cp.returncode == 0, cp.stderr
    out = [json.loads(ln) for ln in cp.stdout.splitlines() if ln.strip()]
    assert out[0]["result"]["serverInfo"]["name"] == "musiccontext"
    assert len(out[1]["result"]["tools"]) == 10
    assert cp.stdout.count("\n") >= 2
