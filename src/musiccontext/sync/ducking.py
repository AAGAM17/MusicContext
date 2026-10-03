"""Make room for speech under the music.

We already know where the speech is, so the default mode automates the music gain over time
instead of listening to the dialogue: the same inputs always produce the same filter string
and the same output level, with no dependence on how loud the narrator happens to be -- or
even on the video having a dialogue track at all.
"""

from __future__ import annotations

from types import SimpleNamespace

from ..errors import InvalidArgumentError
from ..timeline.events import voiceover_blocks
from .fades import finite, num, seconds

MIN_RAMP = 1e-3  # a zero-length ramp would divide by zero inside the expression


def _pairs(speech_segments) -> list[tuple[float, float]]:
    out = []
    for s in speech_segments:
        if isinstance(s, (tuple, list)):
            a, b = finite("speech start", s[0]), finite("speech end", s[1])
        else:
            a, b = finite("speech start", s.start_time), finite("speech end", s.end_time)
        if b < a:
            raise InvalidArgumentError(f"Speech segment ends ({b:g}s) before it starts ({a:g}s).", "Check the speech segments passed to ducking.")
        out.append((a, b))
    return out


def _merge(blocks: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(blocks):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def ducking_blocks(speech_segments, *, pre_roll: float = 0.2, post_roll: float = 0.3, max_blocks: int = 40) -> list[tuple[float, float]]:
    """Speech intervals widened into music-gain blocks, merged until there are at most `max_blocks`."""
    pre = seconds("pre_roll", pre_roll)
    post = seconds("post_roll", post_roll)
    if int(max_blocks) < 1:
        raise InvalidArgumentError(f"max_blocks must be >= 1 (got {max_blocks!r}).", "40 keeps the volume expression short enough for ffmpeg to parse.")
    # Reuse the project's voiceover grouping so ducking and the timeline agree on what one block is.
    raw = voiceover_blocks([SimpleNamespace(start_time=a, end_time=b) for a, b in _pairs(speech_segments)])
    blocks = _merge([(max(0.0, a - pre), b + post) for a, b in raw])
    # An over-long filter string is an ffmpeg failure mode, so absorb the narrowest gaps first.
    while len(blocks) > int(max_blocks):
        i = min(range(len(blocks) - 1), key=lambda j: blocks[j + 1][0] - blocks[j][1])
        blocks[i : i + 2] = [(blocks[i][0], max(blocks[i][1], blocks[i + 1][1]))]
    return blocks


def duck_expression(blocks, depth_db: float, attack_ms: float, release_ms: float, duration: float) -> str | None:
    """A `volume` filter whose gain follows the speech blocks exactly.

    Weight per block w(t) ramps 0 -> 1 over `attack` before the block, holds 1 through it, and
    ramps 1 -> 0 over `release` after it. The gain is 10^(-depth/20 * max(w_i)), i.e. -depth dB
    inside a block and 0 dB away from every block.
    """
    depth = finite("duck_depth_db", depth_db)
    at = max(seconds("attack_ms", attack_ms) / 1000.0, MIN_RAMP)
    rl = max(seconds("release_ms", release_ms) / 1000.0, MIN_RAMP)
    dur = seconds("duration", duration)
    if depth <= 0.05:
        return None
    terms = []
    for a, b in blocks:
        a0, b1 = min(max(0.0, finite("block start", a)), dur), min(max(0.0, finite("block end", b)), dur)
        if b1 <= a0:
            continue
        # Commas are escaped because this lives inside an ffmpeg filtergraph argument.
        up = f"clip((t-{num(a0 - at)})/{num(at)}\\,0\\,1)"
        down = f"clip(({num(b1 + rl)}-t)/{num(rl)}\\,0\\,1)"
        terms.append(f"min({up}\\,{down})")
    if not terms:
        return None
    weight = terms[-1]
    for t in reversed(terms[:-1]):
        weight = f"max({t}\\,{weight})"
    return f"volume=eval=frame:volume=pow(10\\,{num(-depth / 20.0)}*({weight}))"


def sidechain_filters(depth_db: float, attack_ms: float, release_ms: float) -> list[str]:
    """Level-driven ducking against the original dialogue.

    Needs the video to actually carry an audio stream -- the dialogue is the sidechain key --
    and the amount of ducking follows the dialogue level, so a quiet narrator ducks less than a
    loud one and the result is not reproducible across different mixes. Use the default
    expression mode when a guaranteed depth matters.
    """
    depth = finite("duck_depth_db", depth_db)
    at = seconds("attack_ms", attack_ms)
    rl = seconds("release_ms", release_ms)
    if depth <= 0.05:
        return []
    # ponytail: sidechaincompress has no "reduce by N dB" knob, so depth maps onto ratio
    # approximately; switch to mode="expression" when the exact depth is what you need.
    ratio = min(20.0, max(1.0, 1.0 + depth / 2.0))
    return [
        f"sidechaincompress=threshold=0.03:ratio={num(ratio)}"
        f":attack={num(min(max(at, 0.01), 2000.0))}:release={num(min(max(rl, 0.01), 9000.0))}:makeup=1:level_sc=1:mix=1"
    ]


def describe_ducking(blocks, depth_db: float) -> list[str]:
    d = finite("duck_depth_db", depth_db)
    return [f"music ducked {d:.1f} dB during {float(a):.1f}-{float(b):.1f}s" for a, b in blocks]
