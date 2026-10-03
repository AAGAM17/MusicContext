from __future__ import annotations

from pydantic import Field

from .. import ARTIFACT_VERSION
from .direction import MusicDirection, Preferences
from .music import CandidateAssessment, MusicGenerationResult, MusicLicense, MusicSelection
from .timeline import MusicMarker, Strict, SyncPoint
from .video import AnalysisReport, VideoProject


class MusicContextResult(Strict):
    """The portable `.musicctx` artifact. Human-readable JSON; versioned."""

    format: str = "musiccontext.artifact"
    version: str = ARTIFACT_VERSION
    tool_version: str
    created_at: str
    project: VideoProject
    preferences: Preferences
    analysis: AnalysisReport
    music_direction: MusicDirection
    alternatives: list[MusicDirection] = Field(default_factory=list)
    markers: list[MusicMarker] = Field(default_factory=list)
    timeline: list[dict] = Field(default_factory=list, description="Merged chronological view: markers, speech and arc sections.")
    sync_opportunities: list[dict] = Field(default_factory=list)
    candidates: list[CandidateAssessment] = Field(default_factory=list)
    excluded_candidates: list[dict] = Field(default_factory=list)
    generated: MusicGenerationResult | None = None
    selected: MusicSelection | None = None
    sync_decisions: list[SyncPoint] = Field(default_factory=list)
    licenses: list[MusicLicense] = Field(default_factory=list)
    provider_options: list[dict] = Field(default_factory=list)
    provenance: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list, description="Actionable next-step messages (e.g. no provider configured).")
