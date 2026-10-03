"""Provider registry: built-ins plus third-party providers from the `musiccontext.providers` entry point."""

from __future__ import annotations

import logging

from ..errors import NoProviderConfiguredError, ProviderUnavailableError
from ..schemas import ProviderCapability
from .base import Kind, Provider

log = logging.getLogger("musiccontext.providers")
_REGISTRY: dict[str, Provider] = {}
_LOAD_WARNINGS: list[str] = []
_BOOTSTRAPPED = False


def register_provider(provider: Provider, *, replace: bool = False) -> None:
    if provider.name in _REGISTRY and not replace:
        raise ValueError(f"Provider '{provider.name}' is already registered.")
    _REGISTRY[provider.name] = provider


def unregister_provider(name: str) -> None:
    _REGISTRY.pop(name, None)


def _bootstrap() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True
    from .generation import ProceduralGenerationProvider
    from .local import LocalLibraryProvider

    for p in (LocalLibraryProvider(), ProceduralGenerationProvider()):
        _REGISTRY.setdefault(p.name, p)
    _load_entry_points()


def _load_entry_points() -> None:
    from importlib.metadata import entry_points

    try:
        eps = entry_points(group="musiccontext.providers")
    except Exception as e:  # noqa: BLE001
        _LOAD_WARNINGS.append(f"Could not read provider entry points: {type(e).__name__}")
        return
    for ep in eps:
        try:
            obj = ep.load()
            p = obj() if isinstance(obj, type) else obj
            if not isinstance(p, Provider):
                raise TypeError(f"{ep.name} is not a Provider")
            _REGISTRY.setdefault(p.name, p)
        except Exception as e:  # noqa: BLE001 - a broken plugin must not break the tool
            _LOAD_WARNINGS.append(f"Provider plugin '{ep.name}' failed to load: {type(e).__name__}: {e}")
            log.warning("provider plugin %s failed: %s", ep.name, e)


def load_warnings() -> list[str]:
    _bootstrap()
    return list(_LOAD_WARNINGS)


def list_providers(kind: Kind | None = None) -> list[Provider]:
    _bootstrap()
    return [p for p in _REGISTRY.values() if kind is None or kind in p.kinds]


def get_provider(name: str, kind: Kind | None = None) -> Provider:
    _bootstrap()
    p = _REGISTRY.get(name)
    if p is None:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise ProviderUnavailableError(f"Unknown provider '{name}'.", f"Registered providers: {known}. Run `musiccontext providers` for details.")
    if kind and kind not in p.kinds:
        raise ProviderUnavailableError(f"Provider '{name}' does not support {kind}.", f"It supports: {', '.join(p.kinds)}.")
    return p


def capabilities(settings) -> list[ProviderCapability]:
    out = []
    for p in list_providers():
        try:
            out.append(p.capability(settings))
        except Exception as e:  # noqa: BLE001
            out.append(ProviderCapability(name=p.name, kinds=list(p.kinds), available=False, notes=f"capability check failed: {type(e).__name__}"))  # type: ignore[arg-type]
    return sorted(out, key=lambda c: (not c.available, c.name))


def pick(kind: Kind, settings, requested: str | None = None) -> Provider:
    """Choose a provider of `kind`: the requested one, else settings.provider, else the only available one."""
    _bootstrap()
    if requested:
        p = get_provider(requested, kind)
        p.require_available(settings)
        return p
    if settings.provider:
        named = _REGISTRY.get(settings.provider)
        if named and kind in named.kinds:
            named.require_available(settings)
            return named
    avail = [p for p in list_providers(kind) if p.available(settings)]
    if not avail:
        raise NoProviderConfiguredError({"generation": "music generation", "search": "music search", "library": "music library"}.get(kind, kind))
    if len(avail) > 1:
        local = [p for p in avail if "library" in p.kinds]  # prefer the user's own music
        if kind == "library" and local:
            return local[0]
    return avail[0]
