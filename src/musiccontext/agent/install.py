"""`musiccontext init-agent`: install the provider-neutral Skill into Claude Code, Codex, Cursor, VS Code Copilot and Gemini CLI.

Nothing is overwritten without --force, every change is reported, and --dry-run shows the
exact set of changes first. The same Skill directory is used for every agent.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

SKILL_NAME = "musiccontext"
HOME = Path.home()

# key -> (label, executable, home config dir, global skills dir, project skills dir, MCP file + servers key (project-scoped))
# All of these read the open Agent Skills layout (<dir>/<name>/SKILL.md), so one Skill serves them all.
# ponytail: paths follow each agent's documented skills dir; only Claude Code is verified end to end.
AGENTS = {
    "claude": ("Claude Code", "claude", HOME / ".claude", HOME / ".claude/skills", Path(".claude/skills"), (Path(".mcp.json"), "mcpServers")),
    "codex": ("Codex", "codex", HOME / ".codex", HOME / ".agents/skills", Path(".agents/skills"), None),
    "cursor": ("Cursor", "cursor", HOME / ".cursor", HOME / ".cursor/skills", Path(".cursor/skills"), (Path(".cursor/mcp.json"), "mcpServers")),
    "copilot": ("VS Code Copilot", "code", HOME / ".copilot", HOME / ".copilot/skills", Path(".github/skills"), (Path(".vscode/mcp.json"), "servers")),
    "gemini": ("Gemini CLI", "gemini", HOME / ".gemini", HOME / ".gemini/skills", Path(".gemini/skills"), None),
}


def detected(key: str) -> bool:
    _, exe, cfg, *_ = AGENTS[key]
    return bool(shutil.which(exe)) or cfg.is_dir()


def skill_source() -> Path | None:
    """The Skill ships as package data; fall back to the repo layout when running from a checkout."""
    here = Path(__file__).resolve().parents[1]
    for cand in (here / "_data" / "skills" / SKILL_NAME, here.parents[1] / "skills" / SKILL_NAME):
        if (cand / "SKILL.md").is_file():
            return cand
    return None


def targets_for(project: bool = False) -> list[tuple[str, str, Path]]:
    """[(label, cli executable, skill install dir)]"""
    return [(label, exe, ((Path.cwd() / proj) if project else glob) / SKILL_NAME)
            for label, exe, _, glob, proj, _ in AGENTS.values()]


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


def _mcp_json(path: Path, key: str, force: bool, dry_run: bool) -> dict:
    """Merge the MusicContext server into a project MCP file (never clobbers other servers)."""
    entry = {"command": "musiccontext", "args": ["mcp"]}
    if key == "servers":  # VS Code wants an explicit transport type
        entry = {"type": "stdio"} | entry
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            return {"target": str(path), "action": "failed", "note": f"existing {path.name} is not valid JSON; fix or remove it first"}
    servers = data.setdefault(key, {})
    if SKILL_NAME in servers and not force:
        return {"target": str(path), "action": "skipped", "note": f"{key}.musiccontext already present — pass --force to replace"}
    existed = path.is_file()
    if dry_run:
        return {"target": str(path), "action": "would-update" if existed else "would-create", "note": f"adds {key}.musiccontext -> `musiccontext mcp`"}
    servers[SKILL_NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return {"target": str(path), "action": "updated" if existed else "created", "note": f"{key}.musiccontext -> `musiccontext mcp`"}


def install(settings, *, agents: list[str] | None = None, everywhere: bool = False, mcp: bool = False,
            project: bool = False, force: bool = False, dry_run: bool = False) -> dict:
    """Install into the named agents, else every agent found on this machine (`everywhere`: all of them)."""
    src = skill_source()
    actions: list[dict] = []
    warnings: list[str] = []
    nxt: list[str] = []
    if src is None:
        return {"ok": False, "dry_run": dry_run, "actions": [],
                "warnings": ["Could not find the Skill files. Reinstall the package (they ship as package data)."], "next": []}

    chosen = [k for k in AGENTS if (k in agents if agents else everywhere or detected(k))]
    if not chosen:
        warnings.append("No supported agent found. Name one with --agent claude|codex|cursor|copilot|gemini, or use --all.")
    for key in chosen:
        label, exe, _, glob, proj, mcp_file = AGENTS[key]
        tdir = (Path.cwd() / proj if project else glob) / SKILL_NAME
        act = _copy_skill(src, tdir, force, dry_run)
        actions.append(act | {"note": f"{label}: {act['note']}"})
        if mcp and mcp_file:
            actions.append(_mcp_json(Path.cwd() / mcp_file[0], mcp_file[1], force, dry_run))
        elif mcp and key == "codex":
            cmd = "codex mcp add musiccontext -- musiccontext mcp"
            actions.append({"target": "codex MCP config", "action": "skipped",
                            "note": f"run this yourself so your global Codex config is not edited behind your back:\n       {cmd}"})
            nxt.append(cmd)
    if "codex" in chosen:
        warnings.append("Codex Skill support is UNVERIFIED in this build; the MCP server is the supported Codex path.")

    if any(a["action"] in ("created", "updated", "would-create", "would-update") for a in actions):
        nxt.insert(0, "Restart your agent session, then type /musiccontext (or just ask it to use MusicContext).")
    if not mcp:
        nxt.append("Also wire up the MCP server with: musiccontext init-agent --mcp")
    ok = not any(a["action"] == "failed" for a in actions)
    return {"ok": ok, "dry_run": dry_run, "skill_source": str(src), "actions": actions,
            "warnings": warnings, "next": list(dict.fromkeys(nxt))}
