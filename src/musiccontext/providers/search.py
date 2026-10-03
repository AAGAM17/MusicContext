"""Search-provider plumbing: untrusted-payload mapping plus a documented remote template.

MusicContext ships no catalogue integration. Inventing a vendor's endpoint, field names or
pricing would be a lie about an API nobody here has called, so what lives in this module is
the part that is genuinely reusable: turning an arbitrary provider JSON object into a
MusicCandidate without letting its text become an instruction, a secret leak or a false
licence claim. `RemoteSearchProvider` is the subclass contract around it.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..errors import ProviderError, ProviderUnavailableError
from ..schemas import Measured, MusicCandidate, MusicFeatures, MusicLicense, ProviderCapability
from ..security.content import sanitize_meta, sanitize_tags, sanitize_text
from .base import MusicSearchProvider, SearchQuery

DOCS = "docs/providers/"
# Never copied into a candidate, whatever the provider calls it.
_SECRETISH = re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|credential|authorization|bearer|session)")
_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "name", "track_name"),
    "artist": ("artist", "author", "artist_name", "creator"),
    "duration": ("duration", "length_seconds", "duration_seconds", "length"),
    "bpm": ("bpm", "tempo", "tempo_bpm"),
    "tags": ("tags", "genres", "genre", "keywords", "moods"),
    "url": ("url", "uri", "preview_url", "download_url", "audio_url", "stream_url"),
    "key": ("key", "musical_key"),
    "has_vocals": ("has_vocals", "vocals", "is_vocal", "vocal"),
    "bpm_confidence": ("bpm_confidence", "tempo_confidence"),
}
_LICENSE_FIELDS = ("license", "license_name", "license_id", "license_url", "licence", "commercial_use",
                   "commercial_use_allowed", "rights", "attribution_required", "spdx")
_TRUE = ("yes", "true", "1", "allowed", "permitted", "y")
_FALSE = ("no", "false", "0", "prohibited", "forbidden", "n")


def _first(payload: dict, field: str):
    for k in _ALIASES[field]:
        if payload.get(k) is not None:
            return payload[k]
    return None


def _number(value) -> float | None:
    """Parse a number the provider claims. A non-numeric claim becomes None, never a guess."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float("inf") else None


def _tri(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    s = str(value).strip().lower()
    return "yes" if s in _TRUE else "no" if s in _FALSE else "unknown"


def _flag(value) -> bool | None:
    t = _tri(value)
    return None if t == "unknown" else t == "yes"


def candidate_from_payload(payload: dict, provider: str, *, id_key: str = "id") -> MusicCandidate:
    """Map one UNTRUSTED provider JSON object onto a MusicCandidate.

    Every string is sanitized (control characters, ANSI escapes and secrets stripped, length
    bounded). Features are built only from fields the provider actually supplied: an unparseable
    tempo becomes None rather than a number we made up, and `has_vocals` is set only when the
    payload states it. The licence is `verified=True` only when the payload carries explicit
    licence fields; otherwise it is unverified and the reason is recorded in the metadata.
    """
    if not isinstance(payload, dict):
        raise ProviderError(f"Provider '{provider}' returned a {type(payload).__name__} where an object was expected.",
                            f"Each search hit must be a JSON object. See {DOCS}.")
    raw_id = payload.get(id_key) or payload.get("id") or payload.get("track_id") or payload.get("slug")
    track_id = sanitize_text(raw_id, 120)
    if not track_id:
        raise ProviderError(f"Provider '{provider}' returned a track with no usable '{id_key}'.",
                            f"MusicContext needs a stable id per track to cache and re-fetch it. See {DOCS}.")

    duration = _number(_first(payload, "duration"))
    bpm = _number(_first(payload, "bpm"))
    key = sanitize_text(_first(payload, "key"), 40) or None
    has_vocals = _flag(_first(payload, "has_vocals")) if _first(payload, "has_vocals") is not None else None

    notes: dict[str, str] = {}
    features: MusicFeatures | None = None
    if duration is not None or bpm is not None or key or has_vocals is not None:
        conf = _number(_first(payload, "bpm_confidence"))
        if duration is None:
            # ponytail: MusicFeatures.duration is required by the schema, so an absent duration has to
            # be recorded as 0 with this note rather than silently becoming a plausible-looking number.
            notes["duration_note"] = "provider did not supply a duration; 0 is a placeholder, not a measurement"
        features = MusicFeatures(
            duration=round(duration, 3) if duration is not None else 0.0,
            bpm=Measured(value=bpm, source="provider", confidence=min(max(conf, 0.0), 1.0) if conf is not None else 0.5) if bpm else None,
            key=key,
            has_vocals=has_vocals,
        )
        if _first(payload, "bpm") is not None and bpm is None:
            notes["bpm_note"] = f"provider tempo {sanitize_text(_first(payload, 'bpm'), 40)!r} is not a number; recorded as unknown"

    lic_fields = {k: payload[k] for k in _LICENSE_FIELDS if payload.get(k) is not None}
    if lic_fields:
        license_name = next((sanitize_text(lic_fields[k], 120) for k in ("license", "license_name", "licence", "spdx", "license_id") if lic_fields.get(k)), None)
        commercial = next((_tri(lic_fields[k]) for k in ("commercial_use", "commercial_use_allowed") if k in lic_fields), "unknown")
        lic = MusicLicense(
            provider=provider,
            license=license_name or None,
            commercial_use=commercial,  # type: ignore[arg-type]
            attribution_required=_flag(lic_fields["attribution_required"]) if "attribution_required" in lic_fields else None,
            source_url=sanitize_text(lic_fields.get("license_url"), 300) or None,
            verified=True,
            verified_by=f"{provider} API license fields",
        )
    else:
        lic = MusicLicense(provider=provider, commercial_use="unknown", verified=False)
        notes["license_unverified_reason"] = (
            f"the {provider} response carried no license fields, so rights are unverified; "
            "check them with the provider before publishing"
        )

    url = sanitize_text(_first(payload, "url"), 500) or None
    consumed = {id_key, "id", "track_id", "slug", *(a for group in _ALIASES.values() for a in group), *_LICENSE_FIELDS}
    extra = {k: v for k, v in payload.items() if k not in consumed and not _SECRETISH.search(str(k))}
    metadata = {**sanitize_meta(extra), **notes}
    return MusicCandidate(
        id=track_id,
        provider=provider,
        title=sanitize_text(_first(payload, "title"), 200),
        artist=sanitize_text(_first(payload, "artist"), 200) or None,
        uri=url,
        tags=sanitize_tags(_first(payload, "tags") if isinstance(_first(payload, "tags"), (list, tuple)) else [_first(payload, "tags")]),
        features=features,
        license=lic,
        metadata=metadata,
    )


def sandbox_output(dest: Path | str, settings, *, force: bool = False) -> Path:
    """Resolve a download target through the filesystem sandbox.

    With MUSICCONTEXT_ROOTS configured (the MCP/REST case) the target must resolve inside a root;
    without it the trusted-CLI policy applies, which still refuses symlinks and silent overwrites.
    """
    from ..security.paths import PathPolicy

    roots = [Path(r).expanduser().resolve() for r in getattr(settings, "roots", [])] or None
    target = Path(dest).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    return PathPolicy(roots).output_file(target, force=force)


class RemoteSearchProvider(MusicSearchProvider):
    """Template for a music catalogue. Not registered, not usable until subclassed.

    A subclass supplies the vendor-specific parts and nothing else::

        class AcmeCatalogue(RemoteSearchProvider):
            name = "acme"
            endpoint = "https://api.acme.example/v1/search"   # from the vendor's own docs
            credential_env = "MUSICCONTEXT_ACME_API_KEY"

            def _query(self, query: SearchQuery, settings, limit: int) -> list[dict]:
                '''Map SearchQuery onto the vendor's parameters; return its raw JSON hits.'''

    Inherited for free: credential and local-only gating in `capability()`, the
    unavailable-provider error, `candidate_from_payload` over every hit (sanitizing, no invented
    tempo, no unearned "verified" licence), and a `download()` that goes through the SSRF-safe,
    size-capped fetcher into a sandboxed path.

    `_query` returns UNTRUSTED data. Return the vendor's objects as-is and let
    `candidate_from_payload` clean them; never interpolate provider text into a command, a path
    or a prompt, and never put the credential in a result, a log or an error.
    """

    endpoint: str  # no default: a subclass must name its own vendor's endpoint
    credential_env: str | None = None
    requires_credentials = True
    kinds = ("search",)
    sends_media_off_device = False
    data_handling = "Only the query text and numeric filters leave this machine; your media is never uploaded."
    id_key = "id"

    def _env_name(self) -> str:
        return self.credential_env or f"MUSICCONTEXT_{self.name.upper().replace('-', '_')}_API_KEY"

    def capability(self, settings) -> ProviderCapability:
        has_key = bool(settings.api_key_for(self.name))
        local_only = bool(getattr(settings, "local_only", False))
        if local_only:
            notes = "Disabled because local_only is set (MUSICCONTEXT_LOCAL_ONLY=1); no request leaves this machine."
        elif not has_key:
            notes = f"Set {self._env_name()} to enable it. See {DOCS} for the subclass contract."
        else:
            notes = f"Configured. Queries {getattr(self, 'endpoint', '(endpoint not set)')}."
        return ProviderCapability(
            name=self.name,
            kinds=["search"],
            available=has_key and not local_only,
            requires_credentials=True,
            credentials_present=has_key,
            credential_env=self._env_name(),
            sends_media_off_device=self.sends_media_off_device,
            data_handling=self.data_handling,
            supports={"offline": False, "license_metadata": True},
            notes=notes,
        )

    def _require(self, settings) -> None:
        cap = self.capability(settings)
        if not cap.available:
            hint = (cap.notes or f"Set {self._env_name()} to enable it.") + f" Provider template: {DOCS}"
            raise ProviderUnavailableError(f"Provider '{self.name}' is not configured.", hint)

    def search(self, query: SearchQuery, settings, limit: int = 20) -> list[MusicCandidate]:
        self._require(settings)
        hits = self._query(query, settings, limit) or []
        out: list[MusicCandidate] = []
        for hit in hits[: max(0, limit)]:
            out.append(candidate_from_payload(hit, self.name, id_key=self.id_key))
        return out

    def get_track(self, track_id: str, settings) -> MusicCandidate | None:
        self._require(settings)
        wanted = sanitize_text(track_id, 120)
        hits = self._query(SearchQuery(text=wanted), settings, 20) or []
        for hit in hits:
            c = candidate_from_payload(hit, self.name, id_key=self.id_key)
            if c.id == wanted:
                return c
        return None

    # --- the hook a subclass implements ----------------------------------------------
    def _query(self, query: SearchQuery, settings, limit: int) -> list[dict]:
        raise NotImplementedError(f"{type(self).__name__}._query() is not implemented; see {DOCS} for the subclass contract.")

    def download(self, candidate: MusicCandidate, dest: Path, settings, *, force: bool = False) -> Path:
        """Fetch `candidate.uri` into `dest` through the SSRF-safe, size-capped fetcher."""
        from ..security.network import download as net_download
        from ..security.network import is_url

        self._require(settings)
        url = candidate.uri or ""
        if not is_url(url):
            raise ProviderError(f"Candidate '{candidate.id}' from '{self.name}' has no downloadable URL.",
                                "Call search()/get_track() again, or ask the provider for a media URL.")
        target = sandbox_output(dest, settings, force=force)
        with net_download(url, settings) as tmp:
            target.write_bytes(tmp.read_bytes())
        return target
