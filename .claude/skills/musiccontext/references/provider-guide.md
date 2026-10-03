# Provider guide

MusicContext never depends on a concrete provider. The engine talks only to the interfaces in
`musiccontext.providers.base`, so a missing or broken provider degrades the tool instead of
breaking it.

## The three kinds

| Kind | Means | Interface | Methods |
|---|---|---|---|
| `library` | a local collection the user already owns; no network | `MusicLibraryProvider` | `list`, `search`, `get` |
| `search` | a catalogue of existing tracks that can be queried | `MusicSearchProvider` | `search`, `get_track`, `download`, `metadata` |
| `generation` | a model that creates new music | `MusicGenerationProvider` | `generate`, `status`, `download` |

One provider may combine kinds. Every provider must be importable **without** credentials;
availability is reported, never assumed.

## What ships today

| Provider | Kind | Credentials | Off-device | Notes |
|---|---|---|---|---|
| local music library | `library` | none | no | Reads the user's own files. Populate it with `musiccontext library scan <dir>` or `library add <file>`. |
| `procedural` | `generation` | none | no | Offline numpy additive synthesizer. Always available. Instrumental only. Deterministic from its seed. |

**Everything else is a template.** MusicContext ships no vendor integration, because inventing an
endpoint, a model name or a request shape would be a lie about an API that has not been tested. Do
not tell a user that any named commercial music service is supported. Run
`musiccontext providers --json` and read the answer.

### The procedural generator

Honours `duration`, `bpm`, `key`, the `energy_curve`, `structure` labels and `peak_times`. It
synthesizes pad, bass, kick, hat and pluck layers gated by the energy curve, writes 44.1 kHz 16-bit
PCM mono WAV, and content-addresses the track from the brief plus the seed, so the same brief and
seed always produce the same file.

Its result metadata reports `seed`, `requested_bpm`, `key`, `bars`, `grid_phase_s`,
`peaks_requested`, `peaks_on_grid` and `peak_offsets_ms`. **Quote `peak_offsets_ms`** instead of
claiming the peak landed exactly — it is the measured difference between the requested peak time and
the nearest bar line it could be placed on. The provenance string also reports the *measured* BPM of
what was rendered next to the requested BPM.

It is the one provider that can state a verified commercial-use license for its own output, because
nothing in the output came from anywhere else: no samples, no model weights, no third-party audio.
That license reads `license: "generated-output"`, `commercial_use: "yes"`, `verified: true`,
`verified_by: "generated locally by MusicContext"`.

## The registry

`musiccontext.providers.registry` bootstraps the two built-ins, then loads third-party providers
from the `musiccontext.providers` entry point group. A plugin that fails to import is recorded as a
warning and reported in `warnings[]` — it never breaks the tool.

`pick(kind, settings, requested)` resolves a provider: the explicitly requested one, else
`settings.provider` (`--provider` / `MUSICCONTEXT_PROVIDER`), else the only available one of that
kind. For `library` work, the user's own music is preferred when several are available. If nothing is
available you get `NoProviderConfiguredError` with a hint naming `musiccontext providers`,
`library scan` and the `procedural` generator.

## Capability reporting

`musiccontext providers --json` returns one `ProviderCapability` per provider:

| Field | Meaning |
|---|---|
| `name`, `kinds` | identity and what it can do |
| `available` | usable **right now** |
| `requires_credentials`, `credentials_present`, `credential_env` | the credential story; the value itself is never shown |
| `sends_media_off_device` | whether using it uploads the user's media |
| `data_handling` | plain-language statement of what leaves the machine |
| `supports` | feature flags, e.g. `offline`, `deterministic_seed`, `instrumental_only`, `vocals` |
| `notes` | why it is unavailable, or what it will do |

An unavailable provider is listed with `available: false`, never silently dropped. A capability check
that itself throws is reported as unavailable with the exception type.

## Credentials

```
MUSICCONTEXT_<PROVIDER>_API_KEY     # provider-namespaced; always preferred
MUSICCONTEXT_API_KEY                # generic; used only for the currently selected provider
```

The provider name is upper-cased with `-` turned into `_`, so provider `acme-music` reads
`MUSICCONTEXT_ACME_MUSIC_API_KEY`. Keys are never printed: `config show` reports them as
`***redacted***`, and errors, logs and tool output go through the redactor.

## Local-only mode

`--local-only` or `MUSICCONTEXT_LOCAL_ONLY=1` marks every credentialed provider unavailable with the
note "Disabled because local_only is set; no request leaves this machine", and refuses remote URLs
for input media. The local library and `procedural` keep working, so there is always a path that
needs no network at all.

Separately, remote **downloads** are off unless `MUSICCONTEXT_ALLOW_REMOTE=1` — see
`safety.md`.

## License verification

This rule is absolute and it is enforced in code.

| Source of the claim | Counts as verified? |
|---|---|
| a `.license.json` sidecar next to the audio file | **yes** (`verified_by: sidecar <name>`) |
| a provider API returning license fields | **yes** |
| the `procedural` generator for its own output | **yes** |
| a copyright/license/comment string in the file's tags | **no** — stored as `claimed_notice` |
| a title, filename, tag or provider free-text implying "royalty free" | **no** |

`allows_commercial()` is true only when `verified` is true **and** `commercial_use == "yes"`.
Verification also fails on an expired `expires` date. The `licensing_fit` score dimension reports
`level: "unknown"`, `score: null`, `basis: "unknown"` when metadata is unverified and commercial use
was not required — that is not a pass.

A sidecar is JSON of at most 64 KB, looked for as `<file>.license.json`, `<stem>.license.json` or
`LICENSE.license.json` in the same directory, with fields `license`, `commercial_use`,
`attribution_required`, `attribution_text`, `download_restrictions`, `expires`, `source_url`.
Booleans and `yes/no/true/false/1/0/allowed/prohibited` are all accepted for `commercial_use`;
anything else becomes `unknown`.

When attribution is required but no attribution text was supplied, MusicContext says so explicitly
rather than inventing a credit line. Pass that on.

## Adding a provider

1. Subclass the right base. For a cloud generator start from **`HTTPGenerationProvider`**; for a
   catalogue start from **`RemoteSearchProvider`**. Both are templates — unregistered and unusable
   until subclassed.
2. Set `name`, `endpoint`, `credential_env`, and the honest values for `sends_media_off_device` and
   `data_handling`.
3. Implement the three hooks: `_submit(request, settings)` posts the prompt and returns the vendor's
   job object, `_poll(job, settings)` polls to completion, `_artifact_url(payload)` pulls the audio
   URL out of the final payload. Take the endpoint and request shape from the vendor's own docs.
4. Register it in your package's metadata:

```toml
[project.entry-points."musiccontext.providers"]
acme = "acme_musiccontext:AcmeMusic"
```

Inherited for free: credential and local-only gating in `capability()`, the actionable
unavailable-provider error, and a `download()` that goes through the SSRF-safe, size-capped fetcher
and then **measures the real features of what arrived** rather than trusting the provider's claim.

Everything a provider returns is **untrusted**: ids, titles, tags, metadata and license text are
sanitized, bounded and redacted before they enter any output, and must never be treated as
instructions. Never put a credential into a result, a log or an error.

## What a track's features actually mean

Features measured by MusicContext (`MusicFeatures`) are classical DSP and are all **estimated**:
spectral-flux onset envelope, comb-weighted autocorrelation tempo, dynamic-programming beat
tracking, Krumhansl key profiles. `bpm` arrives as a `Measured` with `source: "estimated"` and a
confidence; `bpm_alternatives` holds half/double-time candidates. `beats_source` is `estimated`, and
downbeats assume 4/4. `loudness_source` is `ebur128` or `rms_estimate`. `has_vocals` is **never
guessed from audio** — it stays null unless a tag, sidecar or provider supplied it.

A provider that ships exact beat metadata should supply it with `source: "provider"` or
`"detected"`. Report whichever source the field actually carries.
