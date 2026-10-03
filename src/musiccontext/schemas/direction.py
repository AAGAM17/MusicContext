from __future__ import annotations

from typing import Literal

from pydantic import Field

from .timeline import Strict, Temporal


class Explanation(Strict):
    what: str
    why: list[str]
    when: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    confidence_note: str = ""


class TempoSpec(Strict):
    target_bpm: float
    min_bpm: float
    max_bpm: float
    changes: list[tuple[float, float]] = Field(default_factory=list, description="(time_s, bpm) if a tempo change is wanted.")
    basis: Literal["inferred", "user"] = "inferred"
    rationale: str = ""


class Instrumentation(Strict):
    preferred: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    prohibited: list[str] = Field(default_factory=list)


class DuckingSpec(Strict):
    enabled: bool
    bed_gain_db: float = Field(default=0.0, description="Static music level adjustment relative to normal level when speech is present in the project.")
    duck_depth_db: float = Field(default=0.0, description="Extra attenuation applied during speech (positive number = dB of reduction).")
    attack_ms: float = 250
    release_ms: float = 600
    lift_in_pauses: bool = True
    frequency_guidance: str = ""
    energy_cap_under_speech: float | None = None


class MusicSection(Temporal):
    label: str
    energy: float = Field(ge=0, le=1)
    description: str
    action: str = ""
    rhythm: Literal["sparse", "moderate", "dense", "syncopated", "driving", "relaxed"] = "moderate"


class MusicArc(Strict):
    sections: list[MusicSection]
    energy_curve: list[tuple[float, float]]
    peak_time: float | None = None
    ending: Literal["resolve", "fade_out", "hard_stop", "sustain"] = "resolve"
    rationale: str = ""


class Constraint(Strict):
    kind: str
    severity: Literal["must", "should"]
    description: str
    source: Literal["user", "preset", "brand_profile", "analysis", "safety"]


class MusicRequirement(Strict):
    """What a candidate track must satisfy (derived from the direction; used by selection)."""

    bpm_range: tuple[float, float]
    vocals: Literal["none", "allowed", "preferred", "spoken_word"]
    min_duration: float
    commercial_use_required: bool
    license_must_be_verified: bool
    prohibited_tags: list[str] = Field(default_factory=list)
    required_tags: list[str] = Field(default_factory=list)


class MusicDirection(Strict):
    label: str = "primary"
    style: list[str]
    mood: list[str]
    energy: float = Field(ge=0, le=1, description="Overall target energy.")
    tempo: TempoSpec
    rhythm: Literal["sparse", "moderate", "dense", "syncopated", "driving", "relaxed"]
    instrumentation: Instrumentation
    texture: list[str]
    vocals: Literal["none", "allowed", "preferred", "spoken_word"]
    arc: MusicArc
    ducking: DuckingSpec
    constraints: list[Constraint]
    requirement: MusicRequirement
    explanation: Explanation
    brief: str = Field(description="One-paragraph music brief an agent or human can hand to a provider/composer.")
    generation_prompt: str = Field(description="Provider-neutral prompt text derived from the direction.")


class Preferences(Strict):
    """User/agent preferences. Anything left None is decided by the engine."""

    style: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    genre: list[str] = Field(default_factory=list)
    bpm: float | None = Field(default=None, ge=40, le=220)
    energy: float | None = Field(default=None, ge=0, le=1)
    instruments: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    era: str | None = None
    vocals: Literal["none", "allowed", "preferred", "spoken_word"] | None = None
    language: str | None = None
    reference_styles: list[str] = Field(default_factory=list)
    duration: float | None = None
    brand_personality: list[str] = Field(default_factory=list)
    audience: str | None = None
    platform: str | None = None
    content_type: str | None = None
    commercial_use: bool = False
    require_verified_license: bool = False
    profile: str | None = None
    brief: str | None = Field(default=None, max_length=1000, description="Free-text description of the video from the user.")
    user_markers: list[tuple[float, str]] = Field(default_factory=list, description="(time_s, marker type) supplied by the user/agent.")


class BrandProfile(Strict):
    brand: str
    preferred_styles: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    preferred_instrumentation: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    vocals: Literal["none", "allowed", "preferred", "spoken_word"] | None = None
    notes: str = ""
