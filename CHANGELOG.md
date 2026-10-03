# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-03

First public release. Pre-1.0: interfaces may still change, and the `.musicctx` artifact is at format version 1.0.

### Added

- **Video analysis** (`musiccontext analyze`): scene/cut detection via FFmpeg scene scores, streaming motion and
  colour statistics at bounded memory, silence and speech intervals, an energy curve, and an inferred story model
  with an extensible role taxonomy. Every value carries `source` (`detected`/`estimated`/`inferred`/`user`/`provider`),
  `confidence` and `provenance`.
- **Music Direction Engine** (`musiccontext plan`): style, mood, energy, tempo (with an acceptable range and
  cut-rhythm-aware tempo selection), rhythm, instrumentation, texture, vocals policy, a musical arc with sections
  and actions, ducking guidance, constraints, a machine-readable requirement, a human music brief, a
  provider-neutral generation prompt, and an explanation with evidence and a confidence note.
- **Markers and synchronization opportunities**: typed moments (reveal, product appearance, CTA, voiceover
  boundaries, cuts, transitions, ending) each with importance, a suggested music action, and a reason. User-supplied
  `--marker TIME=TYPE` moments override inferred ones.
- **Music feature extraction**: tempo (comb-weighted autocorrelation), beat tracking (dynamic programming),
  downbeats, key (Krumhansl profiles), EBU R128 loudness, spectral band ratios, energy lifts — all reported as
  `estimated` with their basis.
- **Local music library** (`musiccontext library`): SQLite index with content-hash dedup, incremental re-analysis,
  and explainable search.
- **Explainable selection** (`musiccontext recommend`): nine independent compatibility dimensions, transparent
  aggregate ordering, hard exclusions with reasons, and no opaque "best track" verdict.
- **Synchronization and rendering** (`musiccontext sync`): trimming, looping on musical boundaries, crossfades,
  fades, gain and loudness handling, deterministic speech ducking, beat/downbeat/phrase alignment with reported
  offsets, subtitle passthrough, atomic non-destructive output, and post-render verification of the real duration.
- **Music generation** (`musiccontext generate`): a fully offline, deterministic `procedural` provider that follows
  the plan's tempo, key, energy curve and peak times, plus documented HTTP provider templates for third parties.
- **Provider abstraction**: library/search/generation interfaces, a registry with a `musiccontext.providers` entry
  point, explicit capability reporting (availability, credentials, whether media leaves the machine), and graceful
  behaviour when nothing is configured.
- **Licensing as a first-class concern**: a license counts as verified only when a `.license.json` sidecar or a
  provider API asserts it; file tags are recorded as unverified claims.
- **Agent layer**: a provider-neutral Agent Skill, a Claude Code plugin manifest, a Codex plugin manifest, and an
  MCP server (stdio, JSON-RPC 2.0) exposing ten tools with strict schemas, a filesystem sandbox and bounded output.
- **`musiccontext init-agent`**: installs the Skill for Claude Code and/or Codex, optionally wires up MCP, never
  overwrites without `--force`, and supports `--dry-run`.
- **`musiccontext doctor`**: checks Python, FFmpeg, storage, providers, credential presence (names only), privacy
  posture and agent integrations, including an in-process MCP self-test.
- **Python SDK** (`from musiccontext import MusicContext`) over the same operation layer as the CLI and MCP server.
- **Security**: path sandboxing, SSRF-guarded opt-in remote downloads with DNS pinning, secret redaction, untrusted
  media handling, and a security test suite written so that removing a defence breaks it.
- **Caching**: content-addressed, keyed on content hash plus tool/analysis version and configuration, with stored
  key material re-verified on read so a stale artifact can never masquerade as current.
- Docker image, Docker Compose file, GitHub Actions CI and release workflows, benchmarks, examples and documentation.

### Known limitations

- Scene *roles*, reveals and CTAs are heuristic inferences from pacing, motion and energy; no vision model or OCR is
  used. Supply `--marker TIME=TYPE` when a moment matters.
- Speech detection is a heuristic voice-activity estimator (confidence capped at 0.6) unless you pass a transcript
  or the file carries subtitles.
- Tempo, beats, key and loudness from our own DSP are `estimated`, not ground truth; half/double-time ambiguity is
  reported rather than hidden.
- Codex support is **unverified**: the Codex CLI available during development exposed no skills command, so only the
  MCP path is expected to work there. See `docs/agents/codex.md`.
- Only the local library and the offline `procedural` generator ship as working providers. Everything else is a
  documented template that a user must configure.

[Unreleased]: https://github.com/<OWNER>/MusicContext/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/<OWNER>/MusicContext/releases/tag/v0.1.0
