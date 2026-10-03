"""Search across every configured music provider at once.

An unavailable provider is reported, never silently dropped: the caller is told which
environment variable would switch it on. A provider that misbehaves becomes a warning and the
rest of the search still returns, because the local library alone is a usable answer. Results
are interleaved so a chatty catalogue cannot crowd out the user's own music.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Any

from ..schemas import MusicCandidate
from ..security.secrets import redact

SCAN_HINT = "add your own music with `musiccontext library scan <dir>`"
PROVIDER_HINT = "or configure a search provider (`musiccontext providers` lists them)"


@dataclass
class SearchResults:
    candidates: list[MusicCandidate] = field(default_factory=list)
    by_provider: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def search_music(
    settings,
    query,
    *,
    providers: list[str] | None = None,
    limit: int = 20,
    local_only: bool | None = None,
) -> SearchResults:
    """Query every library/search provider and merge the results. Never raises on an empty result."""
    from ..providers import registry

    res = SearchResults()
    offline = bool(getattr(settings, "local_only", False)) or bool(local_only)

    pool: dict[str, Any] = {}
    for kind in ("library", "search"):
        for p in registry.list_providers(kind):
            pool.setdefault(p.name, p)
    if providers:
        unknown = [n for n in providers if n not in pool]
        if unknown:
            res.messages.append(
                f"Unknown provider(s): {', '.join(sorted(unknown))}. "
                f"Searchable providers: {', '.join(sorted(pool)) or 'none'}."
            )
        pool = {n: p for n, p in pool.items() if n in providers}
    if offline:
        res.messages.append(
            "local_only is set: only on-device library providers were queried, so nothing left this machine."
        )

    # The user's own music first, then catalogues by name: a stable order makes the merge deterministic.
    order = sorted(pool.values(), key=lambda p: ("library" not in p.kinds, p.name))
    per_provider: dict[str, list[MusicCandidate]] = {}
    for p in order:
        try:
            cap = p.capability(settings)
        except Exception as e:  # noqa: BLE001 - a broken provider must not break the search
            res.warnings.append(redact(f"Provider '{p.name}' failed its capability check: {type(e).__name__}: {e}"))
            continue
        if offline and (cap.sends_media_off_device or "library" not in p.kinds):
            res.messages.append(f"Skipped '{p.name}': it is not an on-device library and local_only is set.")
            continue
        if not cap.available:
            env = cap.credential_env or f"MUSICCONTEXT_{p.name.upper().replace('-', '_')}_API_KEY"
            reason = f"Set {env} to enable it." if cap.requires_credentials and not cap.credentials_present else ""
            res.messages.append(f"Skipped '{p.name}': not available. {reason} {cap.notes}".strip())
            continue
        try:
            found = p.search(query, settings, limit=limit) or []
        except Exception as e:  # noqa: BLE001 - one bad provider costs a warning, not the search
            res.warnings.append(redact(f"Provider '{p.name}' search failed: {type(e).__name__}: {e}"))
            continue
        per_provider[p.name] = list(found)[:limit]

    seen: set[str] = set()
    counts = dict.fromkeys(per_provider, 0)
    for rank in zip_longest(*per_provider.values()):  # round-robin: one provider cannot crowd out the rest
        for name, candidate in zip(per_provider, rank, strict=True):
            if candidate is None or len(res.candidates) >= limit:
                continue
            key = candidate.metadata.get("content_hash") or f"{candidate.provider}:{candidate.id}"
            if key in seen:
                continue
            seen.add(key)
            res.candidates.append(candidate)
            counts[name] += 1
    res.by_provider = counts

    if not res.candidates:
        queried = ", ".join(sorted(per_provider)) or "no providers"
        res.messages.append(
            f"No track matched this brief ({queried} queried). What to do next: {SCAN_HINT}, {PROVIDER_HINT}, "
            "or generate a track offline with `musiccontext generate <video>`."
        )
    return res
