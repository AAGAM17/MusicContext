"""Stable import surface for the music data model (the definitions live in `musiccontext.schemas`)."""

from __future__ import annotations

from ..schemas.music import (  # noqa: F401
    AlignmentPlan,
    CandidateAssessment,
    Measured,
    MusicCandidate,
    MusicFeatures,
    MusicGenerationRequest,
    MusicGenerationResult,
    MusicLicense,
    MusicSelection,
    ProviderCapability,
    ScoreDimension,
)

__all__ = [
    "AlignmentPlan", "CandidateAssessment", "Measured", "MusicCandidate", "MusicFeatures",
    "MusicGenerationRequest", "MusicGenerationResult", "MusicLicense", "MusicSelection",
    "ProviderCapability", "ScoreDimension",
]
