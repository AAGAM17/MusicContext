"""Pure filter-builder tests: no ffmpeg, no files, just the strings and the arithmetic."""

from __future__ import annotations

import math

import pytest

from musiccontext.errors import InvalidArgumentError
from musiccontext.schemas import SpeechSegment
from musiccontext.sync import ducking as duck
from musiccontext.sync import fades
from musiccontext.sync.looping import MAX_CROSSFADE_REPEATS, LoopPlan, loop_filter_chain, plan_loop

# ---------------------------------------------------------------- fades

def test_afade_in_and_out():
    assert fades.afade_in(1.5) == "afade=t=in:st=0:d=1.5:curve=tri"
    assert fades.afade_out(2.0, 22.0, curve="qsin") == "afade=t=out:st=22:d=2:curve=qsin"
    assert fades.afade_in(0.0) is None
    assert fades.afade_out(0, 10.0) is None


def test_negative_fade_is_rejected():
    with pytest.raises(InvalidArgumentError):
        fades.afade_in(-1.0)
    with pytest.raises(InvalidArgumentError):
        fades.afade_out(2.0, -0.5)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_inf_are_rejected(bad):
    for fn in (fades.afade_in, fades.apad_to, fades.volume_db, fades.atrim_seek_args, fades.adelay_filter):
        with pytest.raises(InvalidArgumentError):
            fn(bad)


def test_unknown_curve_is_rejected():
    with pytest.raises(InvalidArgumentError):
        fades.afade_in(1.0, curve="swoosh")


def test_volume_db_deadband():
    assert fades.volume_db(0.0) is None
    assert fades.volume_db(0.04) is None
    assert fades.volume_db(-6.0) == "volume=-6dB"
    assert fades.volume_db(3.5) == "volume=3.5dB"


def test_seek_args_are_input_side_and_omitted_at_zero():
    assert fades.atrim_seek_args(0.0) == []
    assert fades.atrim_seek_args(12.25) == ["-ss", "12.25"]


def test_adelay_and_apad():
    assert fades.adelay_filter(0.0) is None
    assert fades.adelay_filter(0.5) == "adelay=delays=500|500:all=1"
    assert fades.adelay_filter(0.5, channels=1) == "adelay=delays=500:all=1"
    assert fades.apad_to(24.0) == "apad=whole_dur=24"
    with pytest.raises(InvalidArgumentError):
        fades.apad_to(0.0)


def test_loudnorm_and_format():
    assert fades.loudnorm_filter(-14.0) == "loudnorm=I=-14:TP=-1.5:LRA=11"
    assert "sample_rates=48000" in fades.format_filter()
    assert "channel_layouts=mono" in fades.format_filter(44100, 1)
    with pytest.raises(InvalidArgumentError):
        fades.loudnorm_filter(-200.0)
    with pytest.raises(InvalidArgumentError):
        fades.loudnorm_filter(-14.0, true_peak=6.0)
    with pytest.raises(InvalidArgumentError):
        fades.format_filter(48000, 7)


def test_num_is_deterministic_and_exponent_free():
    assert fades.num(0) == "0"
    assert fades.num(1e-5) == "0.00001"
    assert fades.num(48000.0) == "48000"


# ---------------------------------------------------------------- looping

def test_repeats_cover_the_needed_duration():
    p = plan_loop(10.0, 0.0, 24.0)
    assert p.repeats == 3           # 10 s usable, 24 s to fill
    assert p.total == pytest.approx(30.0)
    assert p.segments == [(0.0, 10.0)] * 3
    assert plan_loop(10.0, 0.0, 10.0).repeats == 1
    assert plan_loop(10.0, 0.0, 10.5).repeats == 2
    assert plan_loop(10.0, 0.0, 30.0).repeats == 3


def test_repeats_account_for_the_crossfade_overlap():
    p = plan_loop(12.0, 2.0, 24.0, crossfade=2.0)
    usable = 10.0                   # 2.0 .. 12.0
    assert p.crossfade == 2.0
    assert p.repeats == 1 + math.ceil((24.0 - usable) / (usable - 2.0))  # == 3
    assert p.total == pytest.approx(usable + 2 * (usable - 2.0))
    assert p.total >= 24.0


def test_loop_end_beyond_the_track_is_clamped_and_noted():
    p = plan_loop(8.0, 0.0, 20.0, loop_end=99.0)
    assert p.segments[0] == (0.0, 8.0)
    assert any("past the end of the track" in n for n in p.notes)


def test_oversized_crossfade_is_clamped_and_noted():
    p = plan_loop(10.0, 0.0, 30.0, crossfade=9.0)
    assert p.crossfade == 5.0
    assert any("shortened" in n for n in p.notes)


def test_tiny_usable_segment_is_rejected():
    with pytest.raises(InvalidArgumentError) as e:
        plan_loop(10.0, 9.8, 24.0)          # 0.2 s usable
    assert "longer track" in (e.value.hint or "")
    with pytest.raises(InvalidArgumentError):
        plan_loop(10.0, 0.0, 24.0, loop_end=0.2)


def test_loop_chain_uses_aloop_when_there_is_no_crossfade():
    stmts, label = loop_filter_chain(plan_loop(10.0, 0.0, 24.0), "m", "mloop")
    assert label == "mloop"
    assert len(stmts) == 1
    assert "aloop=loop=2:size=480000:start=0" in stmts[0]
    assert "atrim=end=10" in stmts[0]
    assert stmts[0].startswith("[m]") and stmts[0].endswith("[mloop]")


def test_loop_chain_crossfades_and_is_a_no_op_for_one_play():
    p = plan_loop(12.0, 2.0, 24.0, crossfade=2.0)
    stmts, label = loop_filter_chain(p, "m", "mloop")
    assert label == "mloop"
    assert sum(s.count("acrossfade") for s in stmts) == p.repeats - 1
    assert f"asplit={p.repeats}" in stmts[0]
    assert stmts[-1].endswith("[mloop]")
    assert loop_filter_chain(plan_loop(30.0, 0.0, 10.0), "m", "mloop") == ([], "m")


def test_crossfade_repeat_ceiling():
    over = LoopPlan(segments=[(0.0, 1.0)] * 65, crossfade=0.25, repeats=65, total=100.0, notes=[])
    with pytest.raises(InvalidArgumentError) as e:
        loop_filter_chain(over)
    assert str(MAX_CROSSFADE_REPEATS) in e.value.message
    ok = LoopPlan(segments=[(0.0, 1.0)] * MAX_CROSSFADE_REPEATS, crossfade=0.25, repeats=MAX_CROSSFADE_REPEATS, total=100.0, notes=[])
    assert loop_filter_chain(ok)[1] == "mloop"


# ---------------------------------------------------------------- ducking

def test_blocks_use_the_project_voiceover_grouping_plus_rolls():
    segs = [SpeechSegment(start_time=2.0, end_time=4.0), SpeechSegment(start_time=5.0, end_time=8.0),
            SpeechSegment(start_time=13.0, end_time=19.0)]
    blocks = duck.ducking_blocks(segs)
    assert blocks == [(1.8, 8.3), (12.8, 19.3)]     # 4.0->5.0 gap is under VO_GAP, so one block


def test_pre_roll_is_clamped_at_zero_and_overlaps_merge():
    assert duck.ducking_blocks([(0.1, 1.0), (1.1, 2.0)], pre_roll=0.5, post_roll=0.5) == [(0.0, 2.5)]


def test_more_than_max_blocks_are_merged_down():
    segs = [(i * 5.0, i * 5.0 + 1.0) for i in range(60)]
    assert len(duck.ducking_blocks(segs, max_blocks=1_000_000)) == 60
    merged = duck.ducking_blocks(segs)
    assert len(merged) == 40
    assert merged[0][0] == pytest.approx(-0.0 + 0.0)      # first block still starts at 0 after clamping
    assert merged[-1][1] == pytest.approx(60 * 5.0 - 5.0 + 1.0 + 0.3)
    assert all(b[0] <= b[1] for b in merged)
    assert merged == sorted(merged)


def test_reversed_speech_segment_is_rejected():
    with pytest.raises(InvalidArgumentError):
        duck.ducking_blocks([(5.0, 1.0)])


def _eval_duck(expr: str, t: float) -> float:
    """Evaluate the generated ffmpeg expression in Python, so the arithmetic is really checked."""
    body = expr.split("volume=eval=frame:volume=", 1)[1].replace("\\,", ",")
    env = {"t": t, "pow": pow, "min": min, "max": max,
           "clip": lambda x, lo, hi: lo if x < lo else hi if x > hi else x}
    return eval(body, {"__builtins__": {}}, env)  # noqa: S307


def _eval_db(expr: str, t: float) -> float:
    return 20.0 * math.log10(_eval_duck(expr, t))


def test_duck_expression_arithmetic_is_exact():
    blocks = [(2.0, 8.0), (13.0, 19.0)]
    expr = duck.duck_expression(blocks, 12.0, 250.0, 600.0, 24.0)
    assert expr is not None and expr.startswith("volume=eval=frame:volume=")
    assert "eval=frame" in expr
    # inside a block -> full depth
    assert _eval_db(expr, 5.0) == pytest.approx(-12.0, abs=1e-6)
    assert _eval_db(expr, 2.0) == pytest.approx(-12.0, abs=1e-6)
    assert _eval_db(expr, 18.0) == pytest.approx(-12.0, abs=1e-6)
    # middle of a long gap, and before/after everything -> untouched
    assert _eval_db(expr, 10.5) == pytest.approx(0.0, abs=1e-6)
    assert _eval_db(expr, 0.0) == pytest.approx(0.0, abs=1e-6)
    assert _eval_db(expr, 23.0) == pytest.approx(0.0, abs=1e-6)
    # mid-ramp -> exactly half the depth (attack 1.75->2.0, release 8.0->8.6)
    assert _eval_db(expr, 1.875) == pytest.approx(-6.0, abs=1e-6)
    assert _eval_db(expr, 8.3) == pytest.approx(-6.0, abs=1e-6)
    # and monotone across the attack ramp
    ramp = [_eval_db(expr, 1.75 + 0.025 * i) for i in range(11)]
    assert ramp == sorted(ramp, reverse=True)
    assert ramp[0] == pytest.approx(0.0, abs=1e-6)
    assert ramp[-1] == pytest.approx(-12.0, abs=1e-6)


def test_duck_expression_clips_to_the_duration_and_skips_nothing_to_do():
    assert duck.duck_expression([(2.0, 8.0)], 0.0, 250.0, 600.0, 24.0) is None
    assert duck.duck_expression([], 12.0, 250.0, 600.0, 24.0) is None
    assert duck.duck_expression([(30.0, 40.0)], 12.0, 250.0, 600.0, 24.0) is None
    expr = duck.duck_expression([(20.0, 40.0)], 12.0, 250.0, 600.0, 24.0)
    assert _eval_db(expr, 23.0) == pytest.approx(-12.0, abs=1e-6)


def test_zero_ramps_do_not_divide_by_zero():
    expr = duck.duck_expression([(2.0, 8.0)], 10.0, 0.0, 0.0, 24.0)
    assert _eval_db(expr, 5.0) == pytest.approx(-10.0, abs=1e-6)
    assert _eval_db(expr, 1.0) == pytest.approx(0.0, abs=1e-6)


def test_expression_stays_bounded_for_many_blocks():
    expr = duck.duck_expression(duck.ducking_blocks([(i * 5.0, i * 5.0 + 1.0) for i in range(60)]), 10.0, 200.0, 400.0, 300.0)
    assert len(expr) < 8000
    assert _eval_db(expr, 0.5) == pytest.approx(-10.0, abs=1e-6)


def test_sidechain_filters_and_description():
    fs = duck.sidechain_filters(12.0, 250.0, 600.0)
    assert len(fs) == 1 and fs[0].startswith("sidechaincompress=")
    assert "attack=250" in fs[0] and "release=600" in fs[0] and "level_sc=1" in fs[0]
    assert duck.sidechain_filters(0.0, 250.0, 600.0) == []
    assert duck.describe_ducking([(2.0, 8.0)], 12.0) == ["music ducked 12.0 dB during 2.0-8.0s"]
    assert duck.describe_ducking([], 12.0) == []
