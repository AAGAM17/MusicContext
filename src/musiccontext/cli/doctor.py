"""`musiccontext doctor`: check the install, FFmpeg, providers, credentials and agent integrations.

Reports credential PRESENCE by environment-variable name, never a value.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .. import __version__
from ..analysis.media import run


def _check(name, status, detail, hint=None) -> dict:
    return {"name": name, "status": status, "detail": detail, **({"hint": hint} if hint else {})}


def _writable(p: Path) -> tuple[bool, str]:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
        return True, "writable"
    except OSError as e:
        return False, f"not writable: {e.strerror}"


def _tool_version(exe: str) -> str:
    try:
        cp = run([exe, "-version"], timeout=20, check=False)
        return cp.stdout.splitlines()[0][:70] if cp.stdout else "unknown version"
    except Exception as e:  # noqa: BLE001
        return f"could not run: {type(e).__name__}"


def run_doctor(ctx) -> dict:
    s = ctx.settings
    sections: dict[str, list[dict]] = {}
    nxt: list[str] = []

    # --- runtime
    rt = [_check("python", "ok" if sys.version_info >= (3, 11) else "fail",
                 f"{sys.version.split()[0]} ({sys.implementation.name})",
                 None if sys.version_info >= (3, 11) else "MusicContext needs Python 3.11 or newer.")]
    rt.append(_check("musiccontext", "ok", f"{__version__} from {Path(__file__).resolve().parents[1]}"))
    for mod, minver in (("numpy", "1.26"), ("pydantic", "2.6")):
        try:
            m = __import__(mod)
            rt.append(_check(mod, "ok", getattr(m, "__version__", "unknown")))
        except ImportError:
            rt.append(_check(mod, "fail", "not installed", f"pip install '{mod}>={minver}'"))
    sections["Runtime"] = rt

    # --- ffmpeg
    media = []
    for tool in ("ffmpeg", "ffprobe"):
        exe = shutil.which(getattr(s, tool, tool))
        if exe:
            media.append(_check(tool, "ok", f"{exe} — {_tool_version(exe)}"))
        else:
            media.append(_check(tool, "fail", "not found on PATH",
                                "macOS: brew install ffmpeg · Debian/Ubuntu: apt install ffmpeg · or set "
                                f"MUSICCONTEXT_{tool.upper()} to the binary path"))
            nxt.append("Install FFmpeg — analysis and rendering cannot run without it.")
    sections["Media tools"] = media

    # --- storage
    store = []
    for label, path in (("home", s.home), ("cache", s.cache_dir), ("artifacts", s.artifacts_dir)):
        ok, detail = _writable(path)
        store.append(_check(label, "ok" if ok else "fail", f"{path} — {detail}",
                            None if ok else "Set MUSICCONTEXT_HOME / MUSICCONTEXT_CACHE_DIR to a writable directory."))
    try:
        from ..music.library import library_stats

        st = library_stats(s)
        n = st.get("count", 0)
        store.append(_check("library", "ok" if n else "info", f"{n} track(s) indexed at {s.library_db}",
                            None if n else "Add your own music: musiccontext library scan ~/Music"))
        if not n:
            nxt.append("Index some music so `recommend` has candidates: musiccontext library scan <dir>")
    except Exception as e:  # noqa: BLE001
        store.append(_check("library", "warn", f"could not open {s.library_db}: {type(e).__name__}", "It is created on first use."))
    sections["Storage"] = store

    # --- providers
    prov = []
    try:
        from ..providers import registry

        caps = registry.capabilities(s)
        for c in caps:
            status = "ok" if c.available else "info"
            detail = f"{'/'.join(c.kinds)} — {c.notes or ('available' if c.available else 'unavailable')}"
            hint = None
            if c.requires_credentials and not c.credentials_present:
                hint = f"set {c.credential_env} to enable it"
            prov.append(_check(c.name, status, detail, hint))
        if not any(c.available and "generation" in c.kinds for c in caps):
            nxt.append("No generation provider is available; `musiccontext generate` needs the built-in `procedural` provider.")
        for w in registry.load_warnings():
            prov.append(_check("plugin", "warn", w))
    except Exception as e:  # noqa: BLE001
        prov.append(_check("registry", "fail", f"{type(e).__name__}: {e}"))
    creds = sorted(k for k in os.environ if k.startswith("MUSICCONTEXT_") and k.endswith("_API_KEY"))
    prov.append(_check("credentials", "ok" if creds else "info",
                       f"{len(creds)} configured: {', '.join(creds) if creds else 'none'} (names only; values are never read here)"))
    sections["Providers"] = prov

    # --- privacy / network
    net = [
        _check("telemetry", "ok", "none — MusicContext never sends usage data"),
        _check("remote URLs", "info", "enabled" if s.allow_remote else "disabled (default)",
               None if s.allow_remote else "Set MUSICCONTEXT_ALLOW_REMOTE=1 to allow https downloads."),
        _check("local-only mode", "info", "on" if s.local_only else "off"),
    ]
    if s.allow_private_network:
        net.append(_check("private networks", "warn", "ALLOWED — SSRF protection is relaxed", "Unset MUSICCONTEXT_ALLOW_PRIVATE_NETWORK unless you are testing."))
    sections["Privacy"] = net

    # --- agent integrations
    ag = []
    from ..agent.install import skill_source, targets_for

    src = skill_source()
    ag.append(_check("skill source", "ok" if src else "fail", str(src) if src else "skill files not found",
                     None if src else "Reinstall the package; the Skill ships as package data."))
    for name, exe, tdir in targets_for():
        found = shutil.which(exe)
        installed = (tdir / "SKILL.md").is_file() if tdir else False
        if not found:
            ag.append(_check(name, "info", f"{exe} not on PATH", f"Install {name} to use the Skill there."))
        elif installed:
            ag.append(_check(name, "ok", f"{exe} found; Skill installed at {tdir}"))
        else:
            ag.append(_check(name, "info", f"{exe} found; Skill not installed", "Run: musiccontext init-agent"))
            nxt.append(f"Install the Skill for {name}: musiccontext init-agent")
    # MCP self-test, in-process so it cannot hang on a pipe
    try:
        from ..agent.mcp_server import self_test

        t = self_test(s)
        ag.append(_check("mcp server", "ok", f"responds to initialize and lists {t['tools']} tools"))
    except Exception as e:  # noqa: BLE001
        ag.append(_check("mcp server", "fail", f"{type(e).__name__}: {e}", "Run `musiccontext mcp` manually to see the error."))
    sections["Agent integrations"] = ag

    ok = not any(ch["status"] == "fail" for checks in sections.values() for ch in checks)
    if ok and not nxt:
        nxt.append("Try it: musiccontext analyze <video> && musiccontext plan <video>")
    return {"ok": ok, "version": __version__, "sections": sections, "next": list(dict.fromkeys(nxt))}
