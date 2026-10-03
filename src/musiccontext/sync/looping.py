"""Cover a long video with a short track: how many repeats are needed, and the graph to do it."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..errors import InvalidArgumentError
from .fades import num, seconds

MIN_USABLE = 0.5
MAX_CROSSFADE_REPEATS = 64


@dataclass
class LoopPlan:
    segments: list[tuple[float, float]]
    crossfade: float
    repeats: int
    total: float
    notes: list[str] = field(default_factory=list)


def plan_loop(
    track_duration: float,
    music_start: float,
    needed_duration: float,
    *,
    loop_end: float | None = None,
    crossfade: float = 0.0,
) -> LoopPlan:
    """How many plays of [music_start, loop_end] it takes to cover `needed_duration`.

    Each extra play adds `usable - crossfade` seconds, because a crossfade overlaps the tail
    of one copy with the head of the next.
    """
    td = seconds("track_duration", track_duration)
    ms = seconds("music_start", music_start)
    need = seconds("needed_duration", needed_duration)
    xf = seconds("loop_crossfade", crossfade)
    end = td if loop_end is None else seconds("loop_end", loop_end)
    notes: list[str] = []
    if end > td:
        notes.append(f"loop_end {num(end)}s is past the end of the track; looping back from {num(td)}s instead.")
        end = td
    usable = end - ms
    if usable <= MIN_USABLE:
        raise InvalidArgumentError(
            f"Only {usable:.2f}s of the track is usable for looping (music_start {ms:.2f}s, loop point {end:.2f}s).",
            "Pick a longer track, lower the alignment music_start, or choose a later loop point.",
        )
    if xf > usable / 2:
        notes.append(f"loop crossfade shortened from {num(xf)}s to {num(usable / 2)}s so it fits the loop segment.")
        xf = usable / 2
    repeats = 1 if need <= usable else 1 + math.ceil((need - usable) / (usable - xf))
    total = usable + (repeats - 1) * (usable - xf)
    return LoopPlan(segments=[(ms, end)] * repeats, crossfade=xf, repeats=repeats, total=total, notes=notes)


def loop_filter_chain(
    plan: LoopPlan,
    label_in: str = "m",
    label_out: str = "mloop",
    *,
    sample_rate: int = 48000,
) -> tuple[list[str], str]:
    """Filter statements that realise `plan`, plus the label its audio comes out on."""
    if plan.repeats <= 1:
        return [], label_in
    start, end = plan.segments[0]
    usable = end - start
    # The music input is already seeked to `start` by the input-side -ss, so here the segment is [0, usable).
    trim = f"atrim=end={num(usable)},asetpts=N/SR/TB"

    if plan.crossfade <= 0:
        # aloop counts samples, so a gapless loop is sample-accurate rather than timestamp-accurate.
        size = int(round(usable * int(sample_rate)))
        return [f"[{label_in}]{trim},aloop=loop={plan.repeats - 1}:size={size}:start=0,asetpts=N/SR/TB[{label_out}]"], label_out

    if plan.repeats > MAX_CROSSFADE_REPEATS:
        raise InvalidArgumentError(
            f"A crossfaded loop would need {plan.repeats} repeats, more than the {MAX_CROSSFADE_REPEATS} supported.",
            "Use a longer track, or set loop_crossfade to 0 so the exact sample-accurate aloop is used instead.",
        )
    # ponytail: one asplit output + one acrossfade node per repeat, i.e. O(repeats) graph nodes.
    # That is why MAX_CROSSFADE_REPEATS exists; the crossfade-free path above has no such ceiling.
    n = plan.repeats
    copies = [f"{label_out}x{i}" for i in range(n)]
    stmts = [f"[{label_in}]{trim},asplit={n}" + "".join(f"[{c}]" for c in copies)]
    cur = copies[0]
    for i in range(1, n):
        nxt = label_out if i == n - 1 else f"{label_out}a{i}"
        stmts.append(f"[{cur}][{copies[i]}]acrossfade=d={num(plan.crossfade)}:c1=tri:c2=tri[{nxt}]")
        cur = nxt
    return stmts, label_out
