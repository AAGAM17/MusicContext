# Writing a provider

The engine depends only on the interfaces in `src/musiccontext/providers/base.py`. A provider can live in your own
package and be discovered through an entry point. No change to MusicContext is needed.

## Contract

Every provider subclasses `Provider` (or one of the three kinds) and sets:

| Attribute | Meaning |
|---|---|
| `name` | unique id; also the credential env name |
| `kinds` | subset of `"library"`, `"search"`, `"generation"` |
| `requires_credentials`, `credential_env` | default `MUSICCONTEXT_<NAME>_API_KEY` |
| `sends_media_off_device` | **be truthful**: it is shown to the user |
| `data_handling` | one sentence on what leaves the machine |

and implements `capability(settings) -> ProviderCapability`. Rules:

1. **Importable and `capability()` callable without credentials**, and it must not raise. A broken provider must
   never break the registry.
2. When unavailable, say why and name the env var in `notes`.
3. Raise `ProviderUnavailableError` / `ProviderError` from `musiccontext.errors`, always with a `hint`.
4. Everything the vendor returns is **untrusted**. Never put vendor text in a command, a path or a prompt, and never
   put a credential in a result, log or error.

## A search provider

Subclass `RemoteSearchProvider` and write only the vendor-specific part:

```python
# acme_musiccontext/provider.py
from musiccontext.providers.base import SearchQuery
from musiccontext.providers.search import RemoteSearchProvider


class AcmeCatalogue(RemoteSearchProvider):
    name = "acme"
    endpoint = "https://api.acme.example/v1/search"      # from the vendor's own docs
    credential_env = "MUSICCONTEXT_ACME_API_KEY"
    sends_media_off_device = False
    data_handling = "Only the query text and tempo/energy filters are sent to Acme."

    def _query(self, query: SearchQuery, settings, limit: int) -> list[dict]:
        key = settings.api_key_for(self.name)
        ...  # call the vendor with `key`; return its raw JSON hits, unmodified
```

Inherited for free: credential and `--local-only` gating, the "not configured" error, conversion of every hit with
`candidate_from_payload` (sanitizes text, **never invents a tempo, never grants an unearned "verified" license**,
drops anything named like a secret), and a `download()` that goes through the SSRF-safe, size-capped fetcher into a
sandboxed path.

## A generation provider

Subclass `HTTPGenerationProvider` and implement `_submit`, `_poll` and `_artifact_url`, or implement
`MusicGenerationProvider.generate()` yourself for anything non-HTTP. The built-in `ProceduralGenerationProvider`
(`providers/generation.py`) is the reference for a fully local one. Whatever you return is re-analysed with the same
analyzer used on any track, so the reported tempo and key are measured.

## Register it

In your package's `pyproject.toml`:

```toml
[project.entry-points."musiccontext.providers"]
acme = "acme_musiccontext.provider:AcmeCatalogue"
```

The entry point may name a class (instantiated with no arguments) or an instance. A plugin that fails to load
becomes a warning in `musiccontext providers`, not a crash. In tests you can call
`musiccontext.providers.registry.register_provider(AcmeCatalogue())`.

Verify:

```bash
musiccontext providers
MUSICCONTEXT_ACME_API_KEY=... musiccontext search "cinematic technology" --provider acme
```

## Test it

`tests/providers/test_provider_contracts.py` is the contract every provider must satisfy: `capability()` answers
without credentials, kinds agree with the class, an unconfigured remote provider names its env var, and payloads
cannot smuggle an instruction, control character or secret into a `MusicCandidate`. Copy those checks for your
provider. Mark tests that need a live account `@pytest.mark.live`, and do not call the network from the default
suite.
