from __future__ import annotations

from typing import Literal

from pydantic import Field

from .timeline import Source, Strict, SyncPoint


class MusicLicense(Strict):
    provider: str
    license: str | None = Field(default=None, description="License name/SPDX id as stated by the verifying source.")
    commercial_use: Literal["yes", "no", "unknown"] = "unknown"
    attribution_required: bool | None = None
    attribution_text: str | None = None
    download_restrictions: str | None = None
    expires: str | None = Field(default=None, description="ISO date when the license lapses, if any.")
    source_url: str | None = None
    verified: bool = Field(default=False, description="True only when a trusted source (sidecar file, provider API) asserts it.")
    verified_by: str | None = None
    claimed_notice: str | None = Field(default=None, description="Unverified text found in file tags (e.g. copyright). Not proof of license.")

    def allows_commercial(self) -> bool:
        return self.verified and self.commercial_use == "yes"


class Measured(Strict):
    value: float
    source: Source
    confidence: float = Field(ge=0, le=1)


class MusicFeatures(Strict):
    duration: float
    bpm: Measured | None = None
    bpm_alternatives: list[float] = Field(default_factory=list, description="Half/double-time candidates.")
    key: str | None = None
    key_confidence: float | None = None
    loudness_lufs: float | None = None
    loudness_source: Literal["ebur128", "rms_estimate"] | None = None
    beats: list[float] = Field(default_factory=list)
    downbeats: list[float] = Field(default_factory=list)
    beats_source: Source = "estimated"
    lifts: list[tuple[float, float]] = Field(default_factory=list, description="(time, strength) energy lifts, strongest first.")
    energy_curve: list[tuple[float, float]] = Field(default_factory=list, description="(time_s, 0-1) ~1 Hz.")
    spectral_centroid_hz: float | None = None
    band_ratios: dict[str, float] = Field(default_factory=dict, description="low/mid/high energy share; mid=300-3400Hz.")
    dynamic_range_db: float | None = None
    has_vocals: bool | None = Field(default=None, description="Never guessed from audio; only from tags/provider/sidecar.")


class MusicCandidate(Strict):
    id: str
    provider: str
    title: str = ""
    artist: str | None = None
    uri: str | None = Field(default=None, description="Local path or provider URL. Not fetched automatically.")
    tags: list[str] = Field(default_factory=list)
    features: MusicFeatures | None = None
    license: MusicLicense | None = None
    metadata: dict[str, str] = Field(default_factory=dict, description="UNTRUSTED, sanitized.")


class ScoreDimension(Strict):
    name: Literal[
        "mood_match", "tempo_match", "energy_match", "instrumentation_match", "structure_match",
        "voiceover_compatibility", "duration_fit", "transition_fit", "licensing_fit",
    ]
    level: Literal["high", "medium", "low", "unknown", "not_applicable"]
    score: float | None = Field(default=None, ge=0, le=1)
    basis: Literal["detected", "estimated", "inferred", "provider", "unknown"] = "unknown"
    reasons: list[str] = Field(default_factory=list)


class CandidateAssessment(Strict):
    candidate: MusicCandidate
    dimensions: list[ScoreDimension]
    reasons: list[str]
    concerns: list[str] = Field(default_factory=list)


class AlignmentPlan(Strict):
    """How a track would be placed against the video. All times in seconds."""

    music_start: float = Field(default=0.0, description="Seek position into the track (trim from start).")
    video_delay: float = Field(default=0.0, description="Silence before the track starts on the video timeline.")
    loop: bool = False
    loop_end: float | None = Field(default=None, description="Track position (s) to loop back from; a downbeat when beat data exists.")
    loop_crossfade: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    natural_ending: bool = False
    gain_db: float = 0.0
    sync_points: list[SyncPoint] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    basis: Literal["detected", "estimated", "inferred", "none"] = "none"



class MusicSelection(Strict):
    assessment: CandidateAssessment
    alignment: AlignmentPlan | None = None
    selected_by: Literal["user", "recommendation", "generation"] = "recommendation"


class ProviderCapability(Strict):
    name: str
    kinds: list[Literal["library", "search", "generation"]]
    available: bool
    requires_credentials: bool = False
    credentials_present: bool | None = None
    credential_env: str | None = None
    sends_media_off_device: bool = False
    data_handling: str = ""
    supports: dict[str, bool] = Field(default_factory=dict)
    notes: str = ""


class MusicGenerationRequest(Strict):
    prompt: str = Field(max_length=2000)
    negative_prompt: str = Field(default="", max_length=1000)
    duration: float = Field(gt=0, le=900)
    bpm: float | None = Field(default=None, ge=40, le=220)
    style: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    instrumentation: list[str] = Field(default_factory=list)
    structure: list[str] = Field(default_factory=list, description="Section labels in order, e.g. intro, build, peak, resolution.")
    energy_curve: list[tuple[float, float]] = Field(default_factory=list)
    peak_times: list[float] = Field(default_factory=list, description="Moments where the track should land a peak/downbeat.")
    key: str | None = None
    vocals: Literal["none", "allowed", "preferred", "spoken_word"] = "none"
    reference_constraints: list[str] = Field(default_factory=list)
    seed: int | None = None


class MusicGenerationResult(Strict):
    provider: str
    track_id: str
    duration: float
    candidate: MusicCandidate
    artifact_path: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    provenance: str = ""
    license: MusicLicense | None = None
    status: Literal["completed", "pending", "failed"] = "completed"
