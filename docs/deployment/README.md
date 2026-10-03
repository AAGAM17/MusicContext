# Deployment

MusicContext is a CLI and a stdio MCP server, not a long-running service. There are no ports to open and nothing
to health-check.

## Local (verified)

Requires Python 3.11+ and FFmpeg 6+ with `ffmpeg` and `ffprobe` on `PATH`.

```bash
pipx install "musiccontext[agent] @ git+https://github.com/AAGAM17/MusicContext"
musiccontext doctor
```

This path was tested by building the wheel, installing it into a fresh virtual environment, and running `doctor`,
`plan`, `generate` and `sync`, plus an MCP session over stdio.

State lives in:

| What | Where | Override |
|---|---|---|
| library, artifacts, profiles | `$XDG_DATA_HOME/musiccontext` (default `~/.local/share/musiccontext`) | `MUSICCONTEXT_HOME` |
| analysis cache | `$XDG_CACHE_HOME/musiccontext` (default `~/.cache/musiccontext`) | `MUSICCONTEXT_CACHE_DIR` |
| config (optional TOML) | `$XDG_CONFIG_HOME/musiccontext/config.toml` | `MUSICCONTEXT_CONFIG` |

`musiccontext config show` prints the effective settings with credentials redacted.

### Environment variables

| Variable | Effect |
|---|---|
| `MUSICCONTEXT_ROOTS` | directories the MCP server may read and write (`:`-separated) |
| `MUSICCONTEXT_LOCAL_ONLY=1` | refuse any provider that would send media off the machine |
| `MUSICCONTEXT_ALLOW_REMOTE=1` | permit HTTPS input URLs (off by default; SSRF-guarded) |
| `MUSICCONTEXT_PROVIDER`, `MUSICCONTEXT_MODEL` | default provider and model |
| `MUSICCONTEXT_<PROVIDER>_API_KEY` | credential for one provider (environment only) |
| `MUSICCONTEXT_FFMPEG`, `MUSICCONTEXT_FFPROBE` | paths to the binaries |
| `MUSICCONTEXT_MAX_OUTPUT_CHARS` | output bound for MCP and `--json` (default 60000) |

## CI

The repository's own workflow (`.github/workflows/ci.yml`) is a working example: lint, type-check, tests on Python
3.11–3.13 (Linux) and 3.12 (macOS), a separate security job, and a package job that installs the built wheel into a
clean environment and smoke-tests it. Fixtures are synthesized, so no media is checked in.

## Container (not yet verified)

A `Dockerfile` and `docker-compose.yml` are included: a two-stage build on `python:3.12-slim` with FFmpeg, running as
a non-root user, with writable state in a volume.

```bash
docker compose run --rm musiccontext analyze myclip.mp4
```

**This image has not been built or run by the maintainers**, so treat it as a starting point. The Docker publish
step in `release.yml` is deliberately commented out for the same reason. If you build it, please report the result.
