"""`musiccontext init-agent`: install the provider-neutral Skill into Claude Code and/or Codex.

Nothing is overwritten without --force, every change is reported, and --dry-run shows the
exact set of changes first. The same Skill directory is used for every agent.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

CLAUDE_HOME = Path.home() / ".claude"
CODEX_HOME = Path.home() / ".codex"
SKILL_NAME = "musiccontext"


def skill_source() -> Path | None:
    """The Skill ships as package data; fall back to the repo layout when running from a checkout."""
    here = Path(__file__).resolve().parents[1]
    for cand in (here / "_data" / "skills" / SKILL_NAME, here.parents[1] / "skills" / SKILL_NAME):
        if (cand / "SKILL.md").is_file():
            return cand
    return None


def targets_for(project: bool = False) -> list[tuple[str, str, Path | None]]:
    """[(label, cli executable, skill install dir)]"""
    base = Path.cwd() / ".claude" if project else CLAUDE_HOME
    return [
        ("Claude Code", "claude", base / "skills" / SKILL_NAME),
        ("Codex", "codex", (Path.cwd() / ".codex" if project else CODEX_HOME) / "skills" / SKILL_NAME),
    ]


def _copy_skill(src: Path, dest: Path, force: bool, dry_run: bool) -> dict:
    exists = (dest / "SKILL.md").is_file()
    if exists and not force:
        return {"target": str(dest), "action": "skipped", "note": "already installed — pass --force to overwrite"}
    action = ("would-update" if exists else "would-create") if dry_run else ("updated" if exists else "created")
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
    return {"target": str(dest), "action": action, "note": f"Skill files from {src}"}


def _mcp_json(path: Path, force: bool, dry_run: bool) -> dict:
    """Project-scoped .mcp.json for Claude Code (merged, never clobbered)."""
    entry = {"command": "musiccontext", "args": ["mcp"], "env": {}}
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            return {"target": str(path), "action": "failed", "note": "existing .mcp.json is not valid JSON; fix or remove it first"}
    servers = data.setdefault("mcpServers", {})
    if SKILL_NAME in servers and not force:
        return {"target": str(path), "action": "skipped", "note": "mcpServers.musiccontext already present — pass --force to replace"}
    existed = path.is_file()
    if dry_run:
        return {"target": str(path), "action": "would-update" if existed else "would-create", "note": "adds mcpServers.musiccontext -> `musiccontext mcp`"}
    servers[SKILL_NAME] = entry
    path.write_text(json.dumps(data, indent=2) + "\n")
    return {"target": str(path), "action": "updated" if existed else "created", "note": "mcpServers.musiccontext -> `musiccontext mcp`"}


def install(settings, *, claude: bool = False, codex: bool = False, mcp: bool = False, project: bool = False,
            force: bool = False, dry_run: bool = False) -> dict:
    src = skill_source()
    actions: list[dict] = []
    warnings: list[str] = []
    nxt: list[str] = []
    if src is None:
        return {"ok": False, "dry_run": dry_run, "actions": [],
                "warnings": ["Could not find the Skill files. Reinstall the package (they ship as package data)."], "next": []}

    want_all = not (claude or codex)
    for label, exe, tdir in targets_for(project):
        want = want_all or (claude and exe == "claude") or (codex and exe == "codex")
        if not want or tdir is None:
            continue
        if not shutil.which(exe) and not want_all:
            warnings.append(f"{label} CLI ('{exe}') is not on PATH; installing the Skill anyway as you asked for it explicitly.")
        elif not shutil.which(exe):
            actions.append({"target": str(tdir), "action": "skipped", "note": f"{label} CLI not found on PATH"})
            continue
        act = _copy_skill(src, tdir, force, dry_run)
        actions.append(act | {"note": f"{label}: {act['note']}"})
        if exe == "codex":
            warnings.append("Codex Skill support is UNVERIFIED in this build: the installed Codex CLI exposes no skills command. "
                            "The MCP server is the supported Codex path.")

    if mcp:
        if want_all or claude:
            actions.append(_mcp_json(Path.cwd() / ".mcp.json", force, dry_run))
        if want_all or codex:
            cmd = "codex mcp add musiccontext -- musiccontext mcp"
            if shutil.which("codex"):
                actions.append({"target": "codex MCP config", "action": "skipped",
                                "note": f"run this yourself so your global Codex config is not edited behind your back:\n       {cmd}"})
                nxt.append(cmd)
            else:
                actions.append({"target": "codex MCP config", "action": "skipped", "note": "codex CLI not found"})

    if any(a["action"] in ("created", "updated", "would-create", "would-update") for a in actions):
        nxt.insert(0, "Restart your agent session, then run /musiccontext (or just ask it to use MusicContext).")
    if not mcp:
        nxt.append("Also wire up the MCP server with: musiccontext init-agent --mcp")
    ok = not any(a["action"] == "failed" for a in actions)
    return {"ok": ok, "dry_run": dry_run, "skill_source": str(src), "actions": actions,
            "warnings": warnings, "next": list(dict.fromkeys(nxt))}
