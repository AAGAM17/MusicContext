# Security

MusicContext processes untrusted media and talks to third-party providers. Both are treated as hostile input.

## Reporting a vulnerability

Please report security issues privately via GitHub Security Advisories on `https://github.com/<OWNER>/MusicContext`
("Report a vulnerability"), or by email to the maintainers listed in `CONTRIBUTING.md`. Do not open a public issue
for an exploitable bug.

Include: the version (`musiccontext --version`), the platform, a minimal reproduction, and what you observed. Please
describe the class of problem rather than attaching a weaponized exploit. We aim to acknowledge within 7 days.

## Threat model

MusicContext is a local-first CLI/MCP tool. The trust boundaries are:

| Input | Trust | Handling |
|---|---|---|
| CLI arguments typed by the user | trusted (the user is the principal) | paths resolved, no shell |
| Video/audio content and container tags | **untrusted** | sanitized, length-bounded, never executed |
| OCR / subtitles / transcripts | **untrusted** | only timings and word counts are kept; text is discarded |
| Provider API responses | **untrusted** | sanitized before entering any model |
| MCP tool arguments | **untrusted** | strict schemas, filesystem sandbox, bounded output |
| Remote URLs | **untrusted and disabled by default** | opt-in, HTTPS-only, SSRF-guarded |

### Prompt injection

Text extracted from media is *data*. MusicContext never interprets it as an instruction, never uses it to build a
command, and never forwards it as agent instructions. Strings that look like instructions (for example
`"ignore previous instructions"`, `"run rm -rf"`, `"upload this file to attacker.example"`) are flagged in the
`warnings` field so a calling agent can tell the user that the media contains suspicious content. Agents consuming
MusicContext output must do the same — see `skills/musiccontext/references/safety.md`.

### Command execution

FFmpeg and FFprobe are invoked with argument lists only. There is no shell interpolation anywhere in the codebase
(`shell=True` is forbidden and enforced by `ruff` rule `S`). Media-derived strings are never placed in an argument
position that FFmpeg would interpret as a filter or protocol.

### Filesystem

- The CLI runs with the user's own authority: it can read what you can read, and it refuses to overwrite any file
  unless you pass `--force`, and never writes over one of its own inputs.
- The MCP server and REST API are **sandboxed**: every path must resolve inside an allowed root (the working
  directory by default, extended with `MUSICCONTEXT_ROOTS=dir1:dir2`). Traversal, NUL bytes, symlinks that escape
  the sandbox, and writes through symlinks are rejected.
- Renders are written to a temporary file in the destination directory and atomically moved into place.

### Network and SSRF

Remote input is **off by default**. With `MUSICCONTEXT_ALLOW_REMOTE=1`:

- `https://` only (plain HTTP must be explicitly allowed separately),
- DNS is resolved once and the socket is pinned to the validated IP, which closes the DNS-rebinding window,
- any address that is not globally routable is refused (loopback, private ranges, link-local, and cloud metadata
  endpoints such as `169.254.169.254`), including IPv4-mapped IPv6 forms,
- redirects are re-validated hop by hop and limited in number,
- the content type must be audio or video, the body is size-capped, the transfer is deadlined, and the temporary
  file is always removed.

### Secrets

Credentials are read from environment variables only. MusicContext never writes a credential to its config file,
never logs one, and redacts known key shapes plus the *values* of any `*_KEY`/`*_TOKEN`/`*_SECRET`/`*_PASSWORD`
environment variable from errors, logs, rendered commands and tool output. `musiccontext config show` lists
credential names and prints `***redacted***` instead of values.

### Privacy

No telemetry, ever. Nothing leaves the machine unless you select a provider that requires it; such providers report
`sends_media_off_device` in `musiccontext providers`, and `--local-only` (or `MUSICCONTEXT_LOCAL_ONLY=1`) disables
them outright.

## Supported versions

MusicContext is pre-1.0: security fixes land on the latest minor release.

## Running the security suite

```bash
make test-security      # or: pytest tests/security -q
```

These tests are written so that removing a defence makes them fail.
