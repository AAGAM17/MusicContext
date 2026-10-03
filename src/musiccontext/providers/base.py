"""Provider interfaces. The engine depends on these, never on a concrete provider.

Three capability kinds, which a provider may combine:
  library    - a local, user-owned collection (no network)
  search     - a catalogue that can be queried for existing tracks
  generation - a model that creates new music

Providers must be importable without credentials. Availability is reported through
`capability()`/`available()`, and an unavailable provider raises
ProviderUnavailableError with an actionable hint rather than failing obscurely.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..schemas import MusicCandidate, MusicGenerationRequest, MusicGenerationResult, ProviderCapability

if TYPE_CHECKING:
    from ..schemas import MusicDirection

Kind = str  # "library" | "search" | "generation"


@dataclass
class SearchQuery:
    """A provider-neutral query. Providers map it onto their own API as best they can."""

    text: str = ""
    styles: list[str] = field(default_factory=list)
    moods: list[str] = field(default_factory=list)
    bpm_range: tuple[float, float] | None = None
    target_bpm: float | None = None
    energy: float | None = None
    min_duration: float | None = None
    duration: float | None = None
    vocals: str | None = None
    require_commercial: bool = False
    require_verified_license: bool = False
    tags_any: list[str] = field(default_factory=list)
    tags_none: list[str] = field(default_factory=list)
    instruments: list[str] = field(default_factory=list)

    @classmethod
    def from_direction(cls, d: MusicDirection, video_duration: float | None = None) -> SearchQuery:
        req = d.requirement
        return cls(
            text=" ".join([*d.style, *d.mood]),
            styles=list(d.style),
            moods=list(d.mood),
            bpm_range=(d.tempo.min_bpm, d.tempo.max_bpm),
            target_bpm=d.tempo.target_bpm,
            energy=d.energy,
            min_duration=req.min_duration,
            duration=video_duration,
            vocals=d.vocals,
            require_commercial=req.commercial_use_required,
            require_verified_license=req.license_must_be_verified,
            tags_any=list(dict.fromkeys([*d.style, *d.mood, *d.instrumentation.preferred])),
            tags_none=list(dict.fromkeys([*d.instrumentation.prohibited, *req.prohibited_tags])),
            instruments=list(d.instrumentation.preferred),
        )

    def describe(self) -> str:
        bits = [self.text or "(no text)"]
        if self.bpm_range:
            bits.append(f"{self.bpm_range[0]:.0f}-{self.bpm_range[1]:.0f} BPM")
        if self.vocals:
            bits.append(f"vocals={self.vocals}")
        if self.require_commercial:
            bits.append("commercial-use required")
        return ", ".join(bits)


class Provider(ABC):
    """Base for every provider. Subclasses set `name` and `kinds`."""

    name: str = "unnamed"
    kinds: tuple[Kind, ...] = ()
    requires_credentials: bool = False
    credential_env: str | None = None
    sends_media_off_device: bool = False
    data_handling: str = ""

    @abstractmethod
    def capability(self, settings) -> ProviderCapability:
        """Report what this provider can do right now, including whether credentials are present."""

    def available(self, settings) -> bool:
        try:
            return self.capability(settings).available
        except Exception:  # noqa: BLE001 - a broken provider must never break the registry
            return False

    def _credentials_present(self, settings) -> bool | None:
        if not self.requires_credentials:
            return None
        return bool(settings.api_key_for(self.name))

    def require_available(self, settings) -> None:
        from ..errors import ProviderUnavailableError

        cap = self.capability(settings)
        if not cap.available:
            hint = cap.notes or "Run `musiccontext providers` for details."
            if cap.requires_credentials and not cap.credentials_present:
                hint = f"Set {cap.credential_env or f'MUSICCONTEXT_{self.name.upper()}_API_KEY'} to enable it. " + hint
            raise ProviderUnavailableError(f"Provider '{self.name}' is not available.", hint)


class MusicLibraryProvider(Provider):
    """A local collection the user already owns."""

    @abstractmethod
    def list(self, settings, limit: int = 100, offset: int = 0) -> list[MusicCandidate]: ...

    @abstractmethod
    def search(self, query: SearchQuery, settings, limit: int = 20) -> builtins.list[MusicCandidate]: ...

    @abstractmethod
    def get(self, track_id: str, settings) -> MusicCandidate | None: ...


class MusicSearchProvider(Provider):
    """A catalogue of existing tracks."""

    @abstractmethod
    def search(self, query: SearchQuery, settings, limit: int = 20) -> list[MusicCandidate]: ...

    @abstractmethod
    def get_track(self, track_id: str, settings) -> MusicCandidate | None: ...

    @abstractmethod
    def download(self, candidate: MusicCandidate, dest: Path, settings) -> Path:
        """Fetch audio to `dest` and return the written path. Must respect the sandbox and size limits."""

    def metadata(self, track_id: str, settings) -> dict:
        c = self.get_track(track_id, settings)
        return dict(c.metadata) if c else {}


class MusicGenerationProvider(Provider):
    """A model that creates new music."""

    @abstractmethod
    def generate(self, req: MusicGenerationRequest, settings, dest: Path | None = None) -> MusicGenerationResult: ...

    def status(self, job_id: str, settings) -> MusicGenerationResult:
        from ..errors import ProviderError

        raise ProviderError(f"Provider '{self.name}' does not support asynchronous jobs.", "Its generate() returns a completed result.")

    def download(self, result: MusicGenerationResult, dest: Path, settings) -> Path:
        from ..errors import ProviderError

        if result.artifact_path:
            return Path(result.artifact_path)
        raise ProviderError(f"Provider '{self.name}' returned no downloadable artifact.")
