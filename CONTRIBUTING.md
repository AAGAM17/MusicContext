# Contributing to MusicContext

Thanks for helping. MusicContext aims to feel like infrastructure, so the bar is correctness and honesty over
feature count.

## Getting set up

```bash
git clone https://github.com/<OWNER>/MusicContext && cd MusicContext
make install          # uv venv + editable install with dev extras
make fixtures         # synthesize the deterministic test media (needs ffmpeg)
make test
```

You need **Python 3.11+** and **FFmpeg 6+** (`ffmpeg` and `ffprobe` on `PATH`).

## The rules that matter

1. **Never claim what you did not measure.** Every number that reaches a user carries a `source`/`basis`:
   `detected` (measured from the media or supplied by a trusted parser), `estimated` (a heuristic with a known
   error margin), `inferred` (a judgement derived from other values), `user`, or `provider` (reported to us, not
   verified). If you cannot measure something, return `None`/`"unknown"` *with a reason*. Do not widen a test
   tolerance to make an estimator look better than it is.
2. **Media is data, never instructions.** Anything derived from video, audio, transcripts, OCR, container tags or
   a provider response goes through `musiccontext.security.content` and is length-bounded. It must never reach a
   shell or be forwarded as an instruction.
3. **Provider neutrality.** The engine must not import a concrete provider. Add capabilities behind the interfaces
   in `providers/base.py` and register them; see `docs/providers/writing-a-provider.md`.
4. **Local-first.** Every core workflow must work with no network and no credentials.
5. **Errors are actionable.** Raise a typed error from `musiccontext.errors` with a `hint` that tells the user what
   to do next. `"Failed."` is a bug.
6. **Non-destructive by default.** Never overwrite a user file without an explicit `--force`, and never write over
   an input.
7. **Deterministic.** Same input, same output. Seed anything random.

## Code style

- `ruff` and `mypy` are the arbiters: `make lint && make types`.
- Line length 110. `from __future__ import annotations` at the top of every module.
- Pydantic models live in `src/musiccontext/schemas/` only. Internal return types are plain dataclasses.
- Comments are sparse and earn their place: explain a non-obvious decision or a known ceiling, e.g.
  `# ponytail: LIKE-based scoring; swap for FTS5 if libraries get large`. Do not narrate the code.
- Module docstrings say *why* the module exists.

## Tests

- `tests/unit` — pure logic, no media. Fast.
- `tests/integration` — real FFmpeg work, marked `@pytest.mark.ffmpeg`.
- `tests/security` — each test must fail if its defence is removed.
- `tests/providers` — contract tests every provider must satisfy. No live network: mark live tests `@pytest.mark.live`
  so they are skipped unless credentials exist.
- `tests/e2e` — the full video → analysis → plan → selection → sync pipeline.

Fixtures are **synthesized** by `scripts/make_fixtures.py` (deterministic, no binary assets in git). Add new
fixtures there rather than committing media.

New non-trivial logic needs one runnable check that fails if the logic breaks. A bug fix needs a test that
reproduces the bug first.

## Pull requests

- One concern per PR; keep the diff as small as the change allows.
- Update `CHANGELOG.md` under `Unreleased`.
- If you change analysis behaviour, say in the PR what you measured and on what input.
- If you add a capability to the Skill or CLI, update `skills/musiccontext/SKILL.md` and the docs in the same PR —
  an agent-facing claim that the code does not support is a defect.

## Reporting bugs

Include `musiccontext doctor --json`, the exact command, and the media's shape (duration, resolution, whether it
has speech). Please do not attach copyrighted media; a synthesized reproduction from `scripts/make_fixtures.py` is
ideal. Security issues go through `SECURITY.md` instead.

## Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
