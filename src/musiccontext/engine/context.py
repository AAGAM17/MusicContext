"""Settings: environment variables first, then an optional TOML file. No telemetry, ever."""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..security.secrets import REDACTED, RedactingFilter


def _flag(v: str | None, default=False) -> bool:
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def config_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(os.environ.get("MUSICCONTEXT_CONFIG", Path(base) / "musiccontext" / "config.toml"))


@dataclass
class Settings:
    home: Path
    cache_dir: Path
    provider: str | None = None
    model: str | None = None
    allow_remote: bool = False
    allow_insecure_http: bool = False
    allow_private_network: bool = False  # test/dev only; documented as unsafe
    local_only: bool = False
    roots: list[str] = field(default_factory=list)
    max_output_chars: int = 60_000
    max_download_bytes: int = 2 * 1024**3
    remote_timeout: float = 30.0
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    no_cache: bool = False
    verbose: bool = False

    @property
    def library_db(self) -> Path:
        return self.home / "library.db"

    @property
    def artifacts_dir(self) -> Path:
        return self.home / "artifacts"

    @property
    def profiles_dir(self) -> Path:
        return self.home / "profiles"

    def api_key_for(self, provider: str) -> str | None:
        """Provider-namespaced key first, generic key only when that provider is the selected one."""
        specific = os.environ.get(f"MUSICCONTEXT_{provider.upper().replace('-', '_')}_API_KEY")
        if specific:
            return specific
        if self.provider == provider:
            return os.environ.get("MUSICCONTEXT_API_KEY")
        return None

    def show(self) -> dict:
        """Config for display; secrets are reported as set/unset only."""
        keys = sorted(k for k in os.environ if k.startswith("MUSICCONTEXT_") and k.endswith("_API_KEY"))
        return {
            "home": str(self.home),
            "cache_dir": str(self.cache_dir),
            "config_file": str(config_file()),
            "provider": self.provider,
            "model": self.model,
            "allow_remote": self.allow_remote,
            "allow_insecure_http": self.allow_insecure_http,
            "allow_private_network": self.allow_private_network,
            "local_only": self.local_only,
            "roots": self.roots,
            "max_output_chars": self.max_output_chars,
            "max_download_bytes": self.max_download_bytes,
            "ffmpeg": self.ffmpeg,
            "ffprobe": self.ffprobe,
            "credentials": {k: REDACTED for k in keys},
            "telemetry": "none (MusicContext never sends telemetry)",
        }


def load_settings(**overrides) -> Settings:
    file_cfg: dict = {}
    cf = config_file()
    if cf.is_file():
        try:
            file_cfg = tomllib.loads(cf.read_text())
        except (tomllib.TOMLDecodeError, OSError):
            file_cfg = {}
    env = os.environ

    def pick(env_name, key, default=None):
        return env.get(env_name, file_cfg.get(key, default))

    home = Path(pick("MUSICCONTEXT_HOME", "home", Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "musiccontext")).expanduser()
    cache = Path(pick("MUSICCONTEXT_CACHE_DIR", "cache_dir", Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "musiccontext")).expanduser()
    roots = [r for r in (env.get("MUSICCONTEXT_ROOTS") or "").split(os.pathsep) if r] or list(file_cfg.get("roots", []))
    s = Settings(
        home=home,
        cache_dir=cache,
        provider=pick("MUSICCONTEXT_PROVIDER", "provider"),
        model=pick("MUSICCONTEXT_MODEL", "model"),
        allow_remote=_flag(env.get("MUSICCONTEXT_ALLOW_REMOTE"), bool(file_cfg.get("allow_remote", False))),
        allow_insecure_http=_flag(env.get("MUSICCONTEXT_ALLOW_INSECURE_HTTP")),
        allow_private_network=_flag(env.get("MUSICCONTEXT_ALLOW_PRIVATE_NETWORK")),
        local_only=_flag(env.get("MUSICCONTEXT_LOCAL_ONLY"), bool(file_cfg.get("local_only", False))),
        roots=roots,
        max_output_chars=int(env.get("MUSICCONTEXT_MAX_OUTPUT_CHARS", file_cfg.get("max_output_chars", 60_000))),
        max_download_bytes=int(env.get("MUSICCONTEXT_MAX_DOWNLOAD_BYTES", file_cfg.get("max_download_bytes", 2 * 1024**3))),
        ffmpeg=env.get("MUSICCONTEXT_FFMPEG", "ffmpeg"),
        ffprobe=env.get("MUSICCONTEXT_FFPROBE", "ffprobe"),
    )
    for k, v in overrides.items():
        if v is not None and hasattr(s, k):
            setattr(s, k, v)
    return s


def setup_logging(verbose: bool = False, quiet: bool = False) -> None:
    root = logging.getLogger("musiccontext")
    root.handlers.clear()
    h = logging.StreamHandler(sys.stderr)  # never stdout: stdout carries JSON / MCP frames
    h.addFilter(RedactingFilter())
    h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(h)
    root.setLevel(logging.DEBUG if verbose else logging.ERROR if quiet else logging.WARNING)
    root.propagate = False
