"""Licensing is a first-class concern.

A license claim counts as VERIFIED only when a trusted source asserts it: a `.license.json`
sidecar the user wrote, or a provider API that returns license fields. Text found in audio
tags (e.g. a copyright comment) is recorded as `claimed_notice` and never treated as proof.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..schemas import MusicLicense, ScoreDimension
from ..security.content import sanitize_text

SIDECAR_SUFFIX = ".license.json"
_TRUE = ("yes", "true", "1", "allowed", "permitted")
_FALSE = ("no", "false", "0", "prohibited", "forbidden")


def sidecar_candidates(path: Path) -> list[Path]:
    return [path.with_name(path.name + SIDECAR_SUFFIX), path.with_suffix(SIDECAR_SUFFIX), path.parent / ("LICENSE" + SIDECAR_SUFFIX)]


def load_sidecar(path: Path) -> tuple[dict | None, Path | None]:
    for p in sidecar_candidates(path):
        try:
            if p.is_file() and p.stat().st_size <= 64_000:
                return json.loads(p.read_text()), p
        except (OSError, json.JSONDecodeError):
            continue
    return None, None


def _tri(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    s = str(v).strip().lower()
    return "yes" if s in _TRUE else "no" if s in _FALSE else "unknown"


def license_for_local_file(path: Path, tags: dict | None = None) -> MusicLicense:
    """Build a license record for a local file: verified from a sidecar, otherwise unverified."""
    data, src = load_sidecar(path)
    tags = tags or {}
    claimed = next((sanitize_text(tags[k], 200) for k in ("copyright", "license", "comment", "TCOP", "WCOP") if tags.get(k)), None)
    if not data:
        return MusicLicense(
            provider="local", verified=False, commercial_use="unknown", claimed_notice=claimed,
            download_restrictions=None,
            source_url=None,
        )
    return MusicLicense(
        provider="local",
        license=sanitize_text(data.get("license"), 120) or None,
        commercial_use=_tri(data.get("commercial_use", "unknown")),  # type: ignore[arg-type]
        attribution_required=bool(data["attribution_required"]) if "attribution_required" in data else None,
        attribution_text=sanitize_text(data.get("attribution_text"), 300) or None,
        download_restrictions=sanitize_text(data.get("download_restrictions"), 200) or None,
        expires=sanitize_text(data.get("expires"), 40) or None,
        source_url=sanitize_text(data.get("source_url"), 300) or None,
        verified=True,
        verified_by=f"sidecar {src.name}" if src else "sidecar",
        claimed_notice=claimed,
    )


def expired(lic: MusicLicense) -> bool:
    if not lic.expires:
        return False
    import datetime as _dt

    try:
        return _dt.date.fromisoformat(lic.expires[:10]) < _dt.date.today()
    except ValueError:
        return False


def verify_commercial(lic: MusicLicense | None) -> tuple[bool, str]:
    """(cleared, reason). Only ever True when verified metadata says so."""
    if lic is None:
        return False, "no license metadata available"
    if not lic.verified:
        return False, "license metadata is unverified" + (f" (file claims: {lic.claimed_notice})" if lic.claimed_notice else "")
    if expired(lic):
        return False, f"license expired on {lic.expires}"
    if lic.commercial_use == "yes":
        extra = " (attribution required)" if lic.attribution_required else ""
        return True, f"verified by {lic.verified_by or lic.provider}: commercial use permitted{extra}"
    if lic.commercial_use == "no":
        return False, "license states commercial use is not permitted"
    return False, "license does not state commercial-use status"


def license_fit(lic: MusicLicense | None, require_commercial: bool, require_verified: bool) -> ScoreDimension:
    cleared, reason = verify_commercial(lic)
    if require_commercial or require_verified:
        if cleared:
            return ScoreDimension(name="licensing_fit", level="high", score=1.0, basis="provider" if lic and lic.provider != "local" else "detected", reasons=[reason])
        return ScoreDimension(name="licensing_fit", level="low", score=0.0, basis="unknown",
                              reasons=[f"commercial use was required but {reason}", "add a .license.json sidecar or use a provider that returns license metadata"])
    if lic is None or not lic.verified:
        return ScoreDimension(name="licensing_fit", level="unknown", score=None, basis="unknown",
                              reasons=["license metadata is unverified; check rights before publishing"] + ([f"file claims: {lic.claimed_notice}"] if lic and lic.claimed_notice else []))
    return ScoreDimension(name="licensing_fit", level="high" if lic.commercial_use == "yes" else "medium", score=1.0 if lic.commercial_use == "yes" else 0.6,
                          basis="detected", reasons=[reason])


def attribution_lines(licenses: list[MusicLicense]) -> list[str]:
    out = []
    for lic in licenses:
        if lic.attribution_required and lic.attribution_text:
            out.append(lic.attribution_text)
        elif lic.attribution_required:
            out.append(f"Attribution required by {lic.provider} but no attribution text was supplied; ask the provider for the exact credit line.")
    return list(dict.fromkeys(out))


def write_sidecar(path: Path, data: dict, force: bool = False) -> Path:
    p = path.with_name(path.name + SIDECAR_SUFFIX)
    if p.exists() and not force:
        from ..errors import InvalidPathError

        raise InvalidPathError(f"License sidecar already exists: {p}", "Pass --force to overwrite.")
    p.write_text(json.dumps(data, indent=2))
    return p
