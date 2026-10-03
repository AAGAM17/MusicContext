"""Pure ffmpeg filter fragments for the music chain.

Deliberately free of I/O: every function returns one filter statement (or None when the
step is a no-op), so the whole filtergraph can be unit-tested without ffmpeg installed.
"""

from __future__ import annotations

import math

from ..errors import InvalidArgumentError

CURVES = (
    "tri", "qsin", "hsin", "esin", "log", "ipar", "qua", "cub", "squ", "cbr", "par", "exp",
    "iqsin", "ihsin", "dese", "desi", "losi", "sinc", "isinc", "nofade",
)
LAYOUTS = {1: "mono", 2: "stereo"}


def finite(name: str, value: float) -> float:
    """Reject NaN/inf before it reaches ffmpeg, where it becomes an opaque parse error."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise InvalidArgumentError(f"{name} must be a number (got {value!r}).", "Pass a plain float.") from None
    if not math.isfinite(x):
        raise InvalidArgumentError(f"{name} must be a finite number (got {value!r}).", "NaN and infinity are not valid times or gains.")
    return x


def seconds(name: str, value: float) -> float:
    x = finite(name, value)
    if x < 0:
        raise InvalidArgumentError(f"{name} must be >= 0 (got {x:g}).", "Times and durations on the music timeline cannot be negative.")
    return x


def num(x: float) -> str:
    """Deterministic, locale-free, exponent-free number for a filter argument."""
    s = f"{float(x):.6f}".rstrip("0").rstrip(".")
    return s or "0"


def _curve(curve: str) -> str:
    if curve not in CURVES:
        raise InvalidArgumentError(f"Unknown fade curve {curve!r}.", "Valid curves: " + ", ".join(CURVES) + ".")
    return curve


def afade_in(duration: float, start: float = 0.0, curve: str = "tri") -> str | None:
    d = seconds("fade_in duration", duration)
    st = seconds("fade_in start", start)
    if d <= 0:
        return None
    return f"afade=t=in:st={num(st)}:d={num(d)}:curve={_curve(curve)}"


def afade_out(duration: float, start: float, curve: str = "tri") -> str | None:
    d = seconds("fade_out duration", duration)
    st = seconds("fade_out start", start)
    if d <= 0:
        return None
    return f"afade=t=out:st={num(st)}:d={num(d)}:curve={_curve(curve)}"


def volume_db(gain_db: float) -> str | None:
    g = finite("gain_db", gain_db)
    if abs(g) < 0.05:
        return None
    return f"volume={num(g)}dB"


def atrim_seek_args(music_start: float) -> list[str]:
    """The `-ss` fragment for the music input. Place it immediately BEFORE the music `-i`.

    Input-side seek is both accurate (modern ffmpeg decodes from the preceding keyframe and
    discards the excess) and cheap, because it does not decode the skipped head. Output-side
    `-ss` would instead be applied after the filtergraph, shifting every fade/duck timestamp
    we compute here -- so it goes before `-i`.
    """
    s = seconds("music_start", music_start)
    return ["-ss", num(s)] if s > 0 else []


def adelay_filter(delay_s: float, channels: int = 2) -> str | None:
    d = seconds("video_delay", delay_s)
    if d <= 0:
        return None
    n = int(channels)
    if n < 1:
        raise InvalidArgumentError(f"channels must be >= 1 (got {channels!r}).", "Use 1 (mono) or 2 (stereo).")
    ms = num(d * 1000.0)
    # all=1 so the last delay also covers any channel the list does not name.
    return "adelay=delays=" + "|".join([ms] * n) + ":all=1"


def apad_to(duration: float) -> str:
    """Pad the music with silence so it spans `duration`; a longer input is left alone."""
    d = seconds("pad duration", duration)
    if d <= 0:
        raise InvalidArgumentError("Cannot pad music to a zero-length span.", "The video duration minus the music delay must be greater than zero.")
    return f"apad=whole_dur={num(d)}"


def loudnorm_filter(target_lufs: float, true_peak: float = -1.5) -> str:
    i = finite("target_lufs", target_lufs)
    tp = finite("true_peak", true_peak)
    if not -70.0 <= i <= -5.0:
        raise InvalidArgumentError(f"target_lufs {i:g} is outside ffmpeg's range.", "Use an integrated loudness between -70 and -5 LUFS (-14 suits most platforms).")
    if not -9.0 <= tp <= 0.0:
        raise InvalidArgumentError(f"true_peak {tp:g} is outside ffmpeg's range.", "Use a true peak between -9 and 0 dBTP (-1.5 is a safe default).")
    return f"loudnorm=I={num(i)}:TP={num(tp)}:LRA=11"


def format_filter(sample_rate: int = 48000, channels: int = 2) -> str:
    """Canonical output format, so every later filter sees one rate and layout."""
    sr = int(sample_rate)
    if not 8000 <= sr <= 384000:
        raise InvalidArgumentError(f"sample_rate {sr} is out of range.", "Use something between 8000 and 384000 Hz (48000 is the default).")
    layout = LAYOUTS.get(int(channels))
    if layout is None:
        raise InvalidArgumentError(f"Unsupported channel count {channels!r}.", "Use 1 (mono) or 2 (stereo).")
    # first_pts=0 anchors the stream at zero after the input-side -ss, so fade/duck times are absolute.
    return f"aresample={sr}:first_pts=0,aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts={layout}"
