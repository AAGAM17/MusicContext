from __future__ import annotations

from ..schemas import Constraint, DuckingSpec, MusicRequirement, Preferences


def ducking_for(speech_ratio: float, has_speech: bool, bpm: float) -> DuckingSpec:
    if not has_speech:
        return DuckingSpec(enabled=False, frequency_guidance="No voiceover detected; no speech-driven ducking planned.")
    dense = speech_ratio > 0.6
    return DuckingSpec(
        enabled=True,
        bed_gain_db=-6.0 if dense else -3.0,
        duck_depth_db=8.0 if dense else 12.0,
        attack_ms=200, release_ms=700 if dense else 500, lift_in_pauses=not dense,
        frequency_guidance="Keep 1-4 kHz (speech intelligibility) uncluttered: favour pads/sub-bass/plucks over lead melodies and busy hi-hats.",
        energy_cap_under_speech=0.55,
    )


def build_constraints(prefs: Preferences, vocals: str, bpm_range: tuple[float, float], speech_ratio: float, duration: float,
                      prohibited: list[str], preset_name: str | None, brand_avoid: list[str]) -> tuple[list[Constraint], MusicRequirement]:
    c: list[Constraint] = []
    if vocals == "none":
        why = "voiceover present" if speech_ratio >= 0.15 else "requested/preset default"
        c.append(Constraint(kind="instrumental_only", severity="must", description=f"Instrumental only ({why}).", source="analysis" if speech_ratio >= 0.15 and not prefs.vocals else "user" if prefs.vocals else "preset"))
    c.append(Constraint(kind="tempo_range", severity="should", description=f"Prefer {bpm_range[0]:.0f}-{bpm_range[1]:.0f} BPM (half/double time acceptable).", source="analysis"))
    c.append(Constraint(kind="duration", severity="should", description=f"Track should cover {duration:.1f}s or be loopable on a bar boundary.", source="analysis"))
    c.append(Constraint(kind="ending", severity="should", description="Music must end cleanly at the end of the video (resolution or fade), never cut off mid-phrase.", source="analysis"))
    if prefs.commercial_use or prefs.require_verified_license:
        c.append(Constraint(kind="license", severity="must", description="Commercial use must be verified by license metadata; unverified tracks are not cleared.", source="user"))
    else:
        c.append(Constraint(kind="license_awareness", severity="should", description="Check license metadata before publishing; never assume a track is cleared.", source="safety"))
    if prohibited:
        c.append(Constraint(kind="avoid", severity="should", description="Avoid: " + ", ".join(prohibited), source="brand_profile" if brand_avoid else "user"))
    if preset_name:
        c.append(Constraint(kind="platform_preset", severity="should", description=f"Platform preset '{preset_name}' applied; any field can be overridden.", source="preset"))
    req = MusicRequirement(
        bpm_range=bpm_range, vocals=vocals, min_duration=duration * 0.6,  # type: ignore[arg-type]
        commercial_use_required=prefs.commercial_use, license_must_be_verified=prefs.commercial_use or prefs.require_verified_license,
        prohibited_tags=[p.lower() for p in prohibited],
    )
    return c, req
