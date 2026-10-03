"""Temporal base model and timeline objects.

Every temporal object carries start/end, confidence, source and provenance, so no
timestamp is unexplained. `source` distinguishes how a value was obtained:

  detected  - measured directly from media or supplied by a trusted parser
  estimated - computed by a heuristic with a known error margin
  inferred  - a judgement derived from other values (e.g. "this looks like a reveal")
  user      - supplied by the user or the calling agent
  provider  - reported by a music provider (not verified by us)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

Source = Literal["detected", "estimated", "inferred", "user", "provider"]

MARKER_TYPES = (
    "scene_change", "hard_cut", "reveal", "title", "logo", "product_appearance", "feature_reveal",
    "action_peak", "transition", "cta", "ending", "voiceover_start", "voiceover_end",
)
MarkerType = Literal[
    "scene_change", "hard_cut", "reveal", "title", "logo", "product_appearance", "feature_reveal",
    "action_peak", "transition", "cta", "ending", "voiceover_start", "voiceover_end",
]
MusicAction = Literal["none", "hit", "peak", "lift", "build", "drop_out", "resolve", "duck", "release", "fade_out", "transition_fill"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Temporal(Strict):
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    confidence: float = Field(default=1.0, ge=0, le=1)
    source: Source = "detected"
    provenance: str = Field(default="", max_length=300, description="Which analyzer/rule/file produced this value.")

    @model_validator(mode="after")
    def _ordered(self):
        if self.end_time < self.start_time:
            raise ValueError("end_time must be >= start_time")
        return self

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


class Transition(Temporal):
    kind: Literal["hard_cut", "soft_transition", "fade", "unknown"] = "hard_cut"
    strength: float = Field(default=0.5, ge=0, le=1, description="Visual change magnitude across the boundary.")


class MusicMarker(Temporal):
    """A point-in-time event the music can react to. start_time == end_time == timestamp."""

    type: MarkerType
    importance: float = Field(ge=0, le=1)
    suggested_music_action: MusicAction = "none"
    reason: str = Field(max_length=300)

    @model_validator(mode="before")
    @classmethod
    def _from_timestamp(cls, data):
        if isinstance(data, dict) and "timestamp" in data:
            data = dict(data)
            t = data.pop("timestamp")
            data.setdefault("start_time", t)
            data.setdefault("end_time", t)
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def timestamp(self) -> float:
        return self.start_time


class SyncPoint(Strict):
    """A place where a visual marker and a musical event coincide (or nearly do)."""

    marker_time: float
    marker_type: str
    music_event: Literal["beat", "downbeat", "phrase_end", "lift", "track_end", "grid"]
    music_time: float = Field(description="Time of the musical event on the output timeline (seconds).")
    offset_ms: float = Field(description="music_time - marker_time in milliseconds; 0 is perfectly aligned.")
    quality: Literal["detected", "estimated", "inferred"]
    note: str = ""
