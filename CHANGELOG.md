# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Documentation under `docs/` (architecture, data model, Claude Code, Codex status, MCP, providers, workflows,
  deployment) and `examples/` (an offline end-to-end `demo.sh`, a sample transcript and license sidecar).
- `CODE_OF_CONDUCT.md`, issue and pull-request templates, README badges and a plan-vs-measured figure.
- Project URLs and Python version classifiers in `pyproject.toml`.

### Changed

- The procedural generator now follows the requested energy curve in *level* as well as density (a 20 dB range). On
  the demo clip the measured section energy went from 0.65 / 0.76 / 0.66 to 0.16 / 0.66 / 0.17 against a plan of
  0.14 / 0.74 / 0.14; tempo and peak placement are unchanged. A regression test pins it.
- `recommend` no longer says "no candidate music is available" when the library has tracks that were filtered or
  excluded; it says what was searched for and why nothing passed.
- `ruff` and `mypy` are clean and now block CI (they were advisory).

### Fixed

- Repository URLs in the README, manifests, changelog and `SECURITY.md` pointed at a placeholder owner.
- `SECURITY.md` referred to maintainer emails that `CONTRIBUTING.md` never listed; reporting is via GitHub Security
  Advisories.

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

[Unreleased]: https://github.com/AAGAM17/MusicContext/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AAGAM17/MusicContext/releases/tag/v0.1.0
