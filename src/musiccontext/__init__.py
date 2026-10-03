"""MusicContext: music intelligence for AI agents."""

from __future__ import annotations

__version__ = "0.1.0"

# Bump when the .musicctx artifact layout or cached analysis semantics change.
ARTIFACT_VERSION = "1.0"
ANALYSIS_VERSION = "1"


def __getattr__(name: str):  # lazy so `import musiccontext` stays cheap
    if name == "MusicContext":
        from .sdk.client import MusicContext

        return MusicContext
    raise AttributeError(name)


__all__ = ["ANALYSIS_VERSION", "ARTIFACT_VERSION", "MusicContext", "__version__"]
