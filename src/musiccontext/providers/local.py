"""The user's own music, as a provider.

It is always available because it needs nothing: no credentials, no network, not even any
indexed tracks. An empty library is a real answer, and `capability().notes` says how to fill it.
"""

from __future__ import annotations

import builtins

from ..errors import MusicContextError
from ..schemas import MusicCandidate, ProviderCapability
from .base import MusicLibraryProvider, SearchQuery


class LocalLibraryProvider(MusicLibraryProvider):
    name = "local"
    kinds = ("library", "search")
    requires_credentials = False
    sends_media_off_device = False
    data_handling = "Reads only local files you added; nothing leaves the machine."

    def capability(self, settings) -> ProviderCapability:
        from ..music import library

        try:
            count = int(library.library_stats(settings)["count"])
            notes = (f"{count} track{'' if count == 1 else 's'} indexed" if count
                     else "library is empty - run `musiccontext library scan <dir>`")
        except MusicContextError as e:
            # Still available: a fresh index is one scan away. Report the problem instead of hiding it.
            notes = f"library index unreadable ({e.message}) - {e.hint or 'delete it and re-scan'}"
        return ProviderCapability(
            name=self.name,
            kinds=["library", "search"],
            available=True,
            requires_credentials=False,
            sends_media_off_device=False,
            data_handling=self.data_handling,
            supports={"search": True, "download": False, "metadata": True},
            notes=notes,
        )

    def list(self, settings, limit: int = 100, offset: int = 0) -> list[MusicCandidate]:
        from ..music import library

        return library.list_tracks(settings, limit=limit, offset=offset)

    def search(self, query: SearchQuery, settings, limit: int = 20) -> builtins.list[MusicCandidate]:
        from ..music import library

        return library.search_tracks(settings, query, limit=limit)

    def get(self, track_id: str, settings) -> MusicCandidate | None:
        from ..music import library

        return library.get_track(settings, track_id)
