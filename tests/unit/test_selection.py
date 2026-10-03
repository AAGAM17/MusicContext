"""Scoring must stay explainable, honest about missing data, and deterministic."""

from __future__ import annotations

import random

import pytest
from conftest import make_candidate, needs_ffmpeg

from musiccontext.music.selection import (
    WEIGHTS,
    aggregate,
    assess_candidate,
    explain_assessment,
    rank_candidates,
    select,
    weights_for,
)
from musiccontext.schemas import MusicCandidate, MusicLicense

pytestmark = needs_ffmpeg

UNSCORED = ("unknown", "not_applicable")


def dim(assessment, name):
    return next(d for d in assessment.dimensions if d.name == name)


def overall(assessment, direction, analysis):
    return aggregate(assessment.dimensions, weights_for(direction, analysis))[0]


def test_all_nine_dimensions_carry_a_basis(direction, analysis):
    c = make_candidate(cid="a", bpm=direction.tempo.target_bpm, tags=["piano", "minimal"])
    a = assess_candidate(c, direction, analysis)
    assert [d.name for d in a.dimensions] == list(WEIGHTS)
    assert all(d.reasons for d in a.dimensions), "every dimension must say where its verdict came from"
    assert all(d.basis != "unknown" for d in a.dimensions if d.level not in UNSCORED)
    assert 3 <= len(a.reasons) <= 6
    assert "tempo_match" in explain_assessment(a)


def test_tempo_on_target_is_high(direction, analysis):
    t = direction.tempo.target_bpm
    a = assess_candidate(make_candidate(cid="on", bpm=t), direction, analysis)
    d = dim(a, "tempo_match")
    assert d.level == "high"
    assert d.basis == "estimated"  # our DSP measured it
    assert f"{t:.0f}" in " ".join(d.reasons)


def test_half_time_also_scores_high_and_says_so(direction, analysis):
    t = direction.tempo.target_bpm
    d = dim(assess_candidate(make_candidate(cid="half", bpm=round(t / 2, 2)), direction, analysis), "tempo_match")
    assert d.level == "high"
    assert "double time" in " ".join(d.reasons).lower()


def test_wildly_wrong_tempo_scores_low(direction, analysis):
    d = dim(assess_candidate(make_candidate(cid="off", bpm=round(direction.tempo.target_bpm * 1.5, 2)),
                             direction, analysis), "tempo_match")
    assert d.level == "low"
    assert "half nor double" in " ".join(d.reasons)


def test_missing_features_is_unknown_never_low(direction, analysis):
    bare = MusicCandidate(id="bare", provider="local", title="No metadata at all")
    a = assess_candidate(bare, direction, analysis)  # must not raise
    assert dim(a, "tempo_match").level == "unknown"
    assert "no tempo metadata" in dim(a, "tempo_match").reasons[0]
    assert all(d.level in UNSCORED for d in a.dimensions), [d.name for d in a.dimensions if d.level not in UNSCORED]
    assert not any(d.level == "low" for d in a.dimensions)

    good = make_candidate(cid="good", bpm=direction.tempo.target_bpm, duration=30.0,
                          tags=[direction.mood[0], *direction.instrumentation.preferred[:1]], vocals=False)
    ranked, excluded = rank_candidates([bare, good], direction, analysis)
    assert excluded == []
    assert [x.candidate.id for x in ranked] == ["good", "bare"]


def test_unknown_dimensions_are_skipped_not_zeroed(direction, analysis):
    """A track with no license data must not be punished as if licensing scored 0."""
    c = make_candidate(cid="nolicense", bpm=direction.tempo.target_bpm, duration=30.0,
                       tags=[direction.mood[0]], vocals=False)
    a = assess_candidate(c, direction, analysis)
    assert dim(a, "licensing_fit").level == "unknown"
    weights = weights_for(direction, analysis)
    scored = [d for d in a.dimensions if d.level not in UNSCORED and d.score is not None and weights[d.name] > 0]
    line = next(r for r in a.reasons if r.startswith("overall fit"))
    assert f"{len(scored)} scored dimensions" in line
    assert "not scored: licensing_fit" in line
    # the published aggregate is the mean over the scored dimensions only
    expected = sum(d.score * weights[d.name] for d in scored) / sum(weights[d.name] for d in scored)
    assert f"overall fit {expected:.2f}" in line
    assert overall(a, direction, analysis) == pytest.approx(expected, abs=1e-4)


def test_no_speech_zeroes_the_voiceover_weight(direction, analysis, silent_video, settings):
    from musiccontext.engine.pipeline import analyze_video

    quiet, _ = analyze_video(silent_video, settings)
    a = assess_candidate(make_candidate(cid="q", bpm=direction.tempo.target_bpm, duration=30.0), direction, quiet)
    assert dim(a, "voiceover_compatibility").level == "not_applicable"
    assert weights_for(direction, quiet)["voiceover_compatibility"] == 0.0
    assert weights_for(direction, analysis)["voiceover_compatibility"] == WEIGHTS["voiceover_compatibility"]


def _require_license(direction):
    req = direction.requirement.model_copy(update={"commercial_use_required": True, "license_must_be_verified": True})
    return direction.model_copy(update={"requirement": req})


def test_unlicensed_candidate_is_excluded_when_commercial_use_required(direction, analysis):
    strict = _require_license(direction)
    c = make_candidate(cid="unlicensed", bpm=strict.tempo.target_bpm, duration=30.0)
    ranked, excluded = rank_candidates([c], strict, analysis)
    assert ranked == []
    assert len(excluded) == 1
    assert excluded[0]["id"] == "unlicensed" and excluded[0]["provider"] == "local"
    assert "verification" in excluded[0]["reason"]
    assert select([c], strict, analysis) is None


def test_verified_commercial_license_is_not_excluded(direction, analysis):
    strict = _require_license(direction)
    lic = MusicLicense(provider="library", license="CC0-1.0", commercial_use="yes", verified=True,
                       verified_by="sidecar demo.license.json")
    c = make_candidate(cid="cleared", bpm=strict.tempo.target_bpm, duration=30.0, license=lic)
    ranked, excluded = rank_candidates([c], strict, analysis)
    assert excluded == []
    d = dim(ranked[0], "licensing_fit")
    assert d.level == "high" and d.score == 1.0
    assert "commercial use permitted" in " ".join(d.reasons)


def test_vocals_exclusion_only_fires_on_a_known_true(direction, analysis):
    assert direction.vocals == "none"
    sings = make_candidate(cid="sings", bpm=direction.tempo.target_bpm, duration=30.0, vocals=True)
    unknown = make_candidate(cid="unknown", bpm=direction.tempo.target_bpm, duration=30.0, vocals=None)
    ranked, excluded = rank_candidates([sings, unknown], direction, analysis)
    assert [e["id"] for e in excluded] == ["sings"]
    assert "vocals" in excluded[0]["reason"]
    assert [x.candidate.id for x in ranked] == ["unknown"]
    # the unknown track still has to say that its vocal status is unverified
    assert any("unknown" in r for r in dim(ranked[0], "voiceover_compatibility").reasons)


def test_prohibited_tag_is_a_hard_exclusion(direction, analysis):
    banned = direction.instrumentation.prohibited[0]
    c = make_candidate(cid="banned", bpm=direction.tempo.target_bpm, duration=30.0, tags=[banned])
    ranked, excluded = rank_candidates([c], direction, analysis)
    assert ranked == []
    assert banned.replace("_", "-") in excluded[0]["reason"]


def test_short_unloopable_track_is_excluded(direction, analysis):
    short = make_candidate(cid="stub", bpm=None, duration=direction.requirement.min_duration - 2.0)
    assert short.features.downbeats == []
    ranked, excluded = rank_candidates([short], direction, analysis)
    assert ranked == []
    assert "no downbeats" in excluded[0]["reason"]


def test_ranking_is_deterministic_under_shuffling(direction, analysis):
    pool = [
        make_candidate(cid=f"c{i}", bpm=bpm, duration=dur, tags=list(tags))
        for i, (bpm, dur, tags) in enumerate([
            (direction.tempo.target_bpm, 30.0, [direction.mood[0], "piano"]),
            (direction.tempo.target_bpm * 1.5, 30.0, ["piano"]),
            (direction.tempo.target_bpm, 18.0, []),
            (direction.tempo.target_bpm / 2, 40.0, [direction.style[0]]),
            (direction.tempo.target_bpm * 0.95, 26.0, ["piano", direction.mood[-1]]),
        ])
    ]
    first = [a.candidate.id for a in rank_candidates(pool, direction, analysis)[0]]
    for seed in (1, 2, 3):
        shuffled = pool[:]
        random.Random(seed).shuffle(shuffled)
        assert [a.candidate.id for a in rank_candidates(shuffled, direction, analysis)[0]] == first


def test_ties_break_on_candidate_id(direction, analysis):
    kw = dict(bpm=direction.tempo.target_bpm, duration=30.0, tags=[direction.mood[0]])
    pool = [make_candidate(cid=cid, **kw) for cid in ("zz", "aa", "mm")]
    ranked, _ = rank_candidates(pool, direction, analysis)
    scores = {a.candidate.id: overall(a, direction, analysis) for a in ranked}
    assert len(set(scores.values())) == 1, scores
    assert [a.candidate.id for a in ranked] == ["aa", "mm", "zz"]


def test_select_returns_the_top_assessment_without_alignment(direction, analysis):
    pool = [make_candidate(cid="low", bpm=direction.tempo.target_bpm * 1.5, duration=10.0, tags=["piano"]),
            make_candidate(cid="top", bpm=direction.tempo.target_bpm, duration=30.0, tags=[direction.mood[0], "piano"])]
    sel = select(pool, direction, analysis)
    assert sel is not None
    assert sel.assessment.candidate.id == "top"
    assert sel.alignment is None
    assert sel.selected_by == "recommendation"


def test_select_rejects_an_unknown_selected_by(direction, analysis):
    from musiccontext.errors import InvalidArgumentError

    with pytest.raises(InvalidArgumentError):
        select([make_candidate()], direction, analysis, selected_by="vibes")
