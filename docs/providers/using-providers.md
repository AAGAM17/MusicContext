# Using providers

```bash
musiccontext providers          # or --json
```

For each provider it reports: kinds, whether it is **available now**, which environment variable it needs, whether
that credential is present (a name, never a value), and whether it would **send media off your machine**.
A provider that is unavailable is reported with the reason, never silently skipped.

## What ships

| Provider | Kind | Network | Notes |
|---|---|---|---|
| `local` | library, search | none | Your own files, indexed in SQLite. Empty until you `library scan`. |
| `procedural` | generation | none | Offline synthesizer (numpy). Deterministic from its seed. Honours duration, BPM, key, energy curve and peak times. |

Remote catalogues and generators are **templates** (`RemoteSearchProvider`, `HTTPGenerationProvider`). MusicContext
does not ship an integration with any vendor, because inventing an endpoint or request shape for an API nobody here
has called would be a lie. See [writing-a-provider.md](writing-a-provider.md).

## Choosing one

```bash
musiccontext recommend demo.mp4 --provider local
musiccontext generate demo.mp4 --provider procedural --seed 7
MUSICCONTEXT_PROVIDER=local musiccontext search "cinematic technology"
```

Without `--provider`, MusicContext uses `settings.provider` if set, otherwise the only available provider of that
kind (preferring your own library).

## Credentials

- Environment only: `MUSICCONTEXT_<PROVIDER>_API_KEY` (uppercased, `-` becomes `_`).
- `MUSICCONTEXT_API_KEY` is used only for the provider you selected with `--provider` / `MUSICCONTEXT_PROVIDER`.
- Never written to the config file, never logged, never printed. `musiccontext config show` prints
  `***redacted***` for every credential.

## Keeping everything on your machine

```bash
musiccontext plan demo.mp4 --local-only        # or MUSICCONTEXT_LOCAL_ONLY=1
```

`--local-only` disables any provider that would send data off the device. Remote *input URLs* are a separate switch
and are off by default (`MUSICCONTEXT_ALLOW_REMOTE=1`; HTTPS only, SSRF-guarded).

## Your own music and licenses

```bash
musiccontext library scan ~/Music
musiccontext library search "cinematic" --bpm 110 --commercial-use
```

A track's license is `verified` only from a `.license.json` sidecar next to the file (`track.mp3.license.json`,
`track.license.json`, or `LICENSE.license.json` in the folder) or from a provider API that returns license fields:

```json
{ "license": "CC-BY-4.0", "commercial_use": true, "attribution_required": true,
  "attribution_text": "Track by Artist (CC BY 4.0)", "source_url": "https://example.com/track" }
```

A copyright string inside an audio tag is recorded as an **unverified claim**. With `--commercial-use`, tracks
without a verified license are excluded, and each exclusion lists its reason.
