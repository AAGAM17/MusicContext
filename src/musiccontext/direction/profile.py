"""Brand/creative profiles: small JSON files under <home>/profiles/."""

from __future__ import annotations

import re

from ..errors import InvalidArgumentError
from ..schemas import BrandProfile
from ..security.content import sanitize_text


def _path(settings, name: str):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name) or name.startswith("."):
        raise InvalidArgumentError(f"Invalid profile name '{name}'.", "Use letters, digits, '-', '_' or '.' (max 64 chars).")
    return settings.profiles_dir / f"{name}.json"


def save(settings, name: str, profile: BrandProfile, force: bool = False):
    p = _path(settings, name)
    if p.exists() and not force:
        raise InvalidArgumentError(f"Profile '{name}' already exists.", "Pass --force to overwrite.")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(profile.model_dump_json(indent=2))
    return p


def load(settings, name: str) -> BrandProfile:
    p = _path(settings, name)
    if not p.is_file():
        raise InvalidArgumentError(f"Profile '{name}' not found.", "Create it with `musiccontext profile create <name> --style ...`.")
    return BrandProfile.model_validate_json(p.read_text())


def list_profiles(settings) -> list[str]:
    return sorted(p.stem for p in settings.profiles_dir.glob("*.json")) if settings.profiles_dir.is_dir() else []


def make(name: str, styles, avoid, instruments, mood, vocals=None, notes="") -> BrandProfile:
    return BrandProfile(brand=sanitize_text(name, 64), preferred_styles=list(styles), avoid=list(avoid), preferred_instrumentation=list(instruments),
                        mood=list(mood), vocals=vocals, notes=sanitize_text(notes, 300))
